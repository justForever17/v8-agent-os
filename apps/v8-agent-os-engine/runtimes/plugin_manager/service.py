from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core.database import db
from core.security.credentials import CredentialRefStore, CredentialStoreError, credential_ref_store
from core.storage import storage
from core.v8_agent_os_paths import (
    PLUGIN_MANAGER_BIN_ROOT,
    PLUGIN_MANAGER_LOG_ROOT,
    PLUGIN_MANAGER_ROOT,
)

from .catalog import RESOURCE_ROOT, plugin_catalog_service
from .requirements import (
    compile_plugin_requirements,
    discover_requirement_sources,
    read_explicit_import_source,
)
from .schema import CliAction, CliProfile, CommandSpec, PluginConfigRequirement, PluginManifest


AGENT_SKILLS_ROOT = Path.home() / ".agents" / "skills"
SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization|credential)", re.I)
SAFE_COMPONENT_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")
CODE_OWNED_PROVIDER_ADAPTERS = {
    "creative_media.aliyun_bailian_dashscope",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("***" if SECRET_KEY_RE.search(str(key)) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _hash_path(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_value(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _platform_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def _safe_owned_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    roots = [PLUGIN_MANAGER_ROOT.resolve(), PLUGIN_MANAGER_BIN_ROOT.resolve(), AGENT_SKILLS_ROOT.resolve()]
    return any(resolved == root or root in resolved.parents for root in roots)


class PluginManagerError(RuntimeError):
    def __init__(self, message: str, *, code: str = "plugin_manager_error", status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PluginManagerService:
    kind = "plugin_manager"

    _plugin_locks_guard = threading.RLock()
    _plugin_locks: dict[str, threading.RLock] = {}

    def __init__(self, *, credential_store: CredentialRefStore | None = None) -> None:
        self._cache_lock = threading.RLock()
        self._ownership_cache: tuple[frozenset[str], frozenset[str]] | None = None
        self._grant_cache: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        self._catalog_projection_cache: tuple[
            tuple[int, int],
            dict[str, Any],
            tuple[dict[str, Any], ...],
        ] | None = None
        self._catalog_installation_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._credential_store = credential_store or credential_ref_store
        self._ensure_plugin_schema()
        self.reconcile_install_jobs()

    @staticmethod
    def _table_columns(conn: Any, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _ensure_plugin_schema(self) -> None:
        """Additive, domain-local schema evolution for provisional plugin tables."""

        with db.get_connection() as conn:
            job_columns = self._table_columns(conn, "plugin_install_jobs")
            job_additions = {
                "plan_digest": "TEXT",
                "idempotency_key": "TEXT",
                "staging_path": "TEXT",
                "updated_at": "TEXT",
                "external_reconciliation": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in job_additions.items():
                if name not in job_columns:
                    conn.execute(f"ALTER TABLE plugin_install_jobs ADD COLUMN {name} {definition}")

            install_columns = self._table_columns(conn, "plugin_installations")
            for name, definition in {
                "manifest_digest": "TEXT",
                "receipt_json": "TEXT",
            }.items():
                if name not in install_columns:
                    conn.execute(f"ALTER TABLE plugin_installations ADD COLUMN {name} {definition}")

            grant_columns = self._table_columns(conn, "plugin_grants")
            for name, definition in {
                "owner_user_id": "TEXT",
                "manifest_version": "TEXT",
                "manifest_digest": "TEXT",
                "catalog_revision": "INTEGER",
                "state": "TEXT NOT NULL DEFAULT 'active'",
                "terminal_reason": "TEXT",
                "grant_source": "TEXT NOT NULL DEFAULT 'user_reference'",
            }.items():
                if name not in grant_columns:
                    conn.execute(f"ALTER TABLE plugin_grants ADD COLUMN {name} {definition}")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plugin_install_steps (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(job_id, ordinal)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plugin_credential_bindings (
                    id TEXT PRIMARY KEY,
                    plugin_id TEXT NOT NULL,
                    component_id TEXT NOT NULL,
                    requirement_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    target_name TEXT,
                    secret_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(plugin_id, requirement_id)
                )
                """
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_plugin_jobs_idempotency ON plugin_install_jobs(plugin_id, idempotency_key) WHERE idempotency_key IS NOT NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_plugin_steps_job ON plugin_install_steps(job_id, ordinal)")
            conn.execute(
                """
                UPDATE plugin_grants
                SET state='invalidated', terminal_reason='schema_upgrade_requires_regrant'
                WHERE state='active' AND (owner_user_id IS NULL OR manifest_digest IS NULL)
                """
            )
            conn.commit()

    @contextmanager
    def _plugin_lock(self, plugin_id: str):
        normalized = str(plugin_id or "").strip().lower()
        with self._plugin_locks_guard:
            lock = self._plugin_locks.setdefault(normalized, threading.RLock())
        with lock:
            yield

    @staticmethod
    def _manifest_digest(manifest: PluginManifest) -> str:
        return _hash_value(manifest.model_dump(mode="json"))

    def _invalidate_ownership_cache(self) -> None:
        with self._cache_lock:
            self._ownership_cache = None

    def _invalidate_grant_cache(self) -> None:
        with self._cache_lock:
            self._grant_cache.clear()

    def _invalidate_catalog_installation_cache(self) -> None:
        with self._cache_lock:
            self._catalog_installation_cache = None

    def _catalog_installation_rows(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._catalog_installation_cache
            if cached is not None and now - cached[0] <= 0.25:
                return cached[1]
        rows = self._installation_rows()
        with self._cache_lock:
            self._catalog_installation_cache = (now, rows)
        return rows

    def _catalog_projection(self) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        """Cache catalog-owned metadata while keeping installation state live."""

        catalog = plugin_catalog_service.load()
        cache_key = (id(catalog), int(catalog.revision))
        with self._cache_lock:
            cached = self._catalog_projection_cache
            if cached is not None and cached[0] == cache_key:
                return cached[1], cached[2]

        plugins = tuple(
            {
                **manifest.model_dump(mode="json"),
                "manifestDigest": self._manifest_digest(manifest),
                "catalogRevision": catalog.revision,
                "componentCounts": {
                    "cli": len(manifest.cliProfiles),
                    "skills": len(manifest.skills),
                    "mcp": len(manifest.mcpServers),
                    "uiAdapters": len(manifest.uiAdapters),
                    "providerAdapters": len(manifest.providerAdapters),
                },
                "grantRequired": True,
                "brandAssetUrl": f"/v1/api/plugins/{manifest.id}/logo",
            }
            for manifest in catalog.plugins
        )
        snapshot = plugin_catalog_service.snapshot()
        with self._cache_lock:
            self._catalog_projection_cache = (cache_key, snapshot, plugins)
        return snapshot, plugins

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "插件管理中心",
            "summary": "管理精选官方 CLI、Skill、MCP、UI:// 适配器与最小任务授权。",
            "responsibilities": [
                "维护签名插件目录与可信缓存",
                "执行可回滚的插件组件安装和卸载",
                "管理任务/会话授权与特权能力投影",
                "提供受治理 CLI、健康检查和审计账本",
            ],
            "routingKeywords": ["插件", "CLI", "MCP", "Skill", "插件商店", "授权"],
            "acceptedInputs": ["catalog refresh", "install plan", "configuration", "task grant"],
            "producedOutputs": ["plugin status", "install job", "grant projection", "doctor report"],
            "ownedSteps": ["plugin.catalog", "plugin.install", "plugin.configure", "plugin.grant", "plugin.doctor"],
            "supportsPause": False,
            "supportsResume": True,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "support",
            "promptHints": [
                "@插件 是强提示而非唯一入口；Supervisor 可为当前任务授权已安装、已配置且健康的最小组件集合。",
                "Supervisor 不得自行安装插件、补配置、读取密钥或创建长期会话授权。",
                "Supervisor 常驻面只读取紧凑状态，不加载完整命令和工具树。",
            ],
            "capabilities": [
                {
                    "key": "plugin_manager.lifecycle",
                    "label": "插件生命周期与授权治理",
                    "summary": "对精选官方组件执行签名校验、事务安装、精确授权、特权投影、Doctor 与安全卸载。",
                    "accepts": ["signed manifest", "user plugin reference", "supervisor task grant", "structured CLI request"],
                    "outputs": ["installation state", "capability projection", "audit event"],
                    "examples": ["安装并配置 Figma MCP", "为当前任务授权 GitHub CLI"],
                    "risk_level": "high",
                },
            ],
            "metadata": {
                "statusSurface": "compact",
                "supervisorToolSurface": True,
                "grantRequired": True,
                "projectionLane": "privileged",
            },
        }

    def _manifest(self, plugin_id: str) -> PluginManifest:
        manifest = plugin_catalog_service.get(plugin_id)
        if not manifest:
            raise PluginManagerError(f"未知插件：{plugin_id}", code="plugin_not_found", status_code=404)
        return manifest

    def _plugin_root(self, plugin_id: str) -> Path:
        config = storage.get_plugin_manager_config()
        configured = str(config.get("installRoot") or "").strip()
        root = Path(configured).expanduser() if configured else PLUGIN_MANAGER_ROOT
        return root / plugin_id

    def _bin_root(self) -> Path:
        configured = str(storage.get_plugin_manager_config().get("binRoot") or "").strip()
        return Path(configured).expanduser() if configured else PLUGIN_MANAGER_BIN_ROOT

    def _installation_rows(self) -> dict[str, dict[str, Any]]:
        with db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM plugin_installations").fetchall()
        return {str(row["plugin_id"]): dict(row) for row in rows}

    def _component_rows(self, plugin_id: str | None = None) -> list[dict[str, Any]]:
        with db.get_connection() as conn:
            if plugin_id:
                rows = conn.execute(
                    "SELECT * FROM plugin_components WHERE plugin_id = ? ORDER BY component_type, component_id",
                    (plugin_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM plugin_components ORDER BY plugin_id, component_type").fetchall()
        return [dict(row) for row in rows]

    def _installation_payload(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {
                "installed": False,
                "state": "not_installed",
                "configured": False,
                "online": False,
            }
        return {
            "installed": str(row.get("state") or "") in {"installed", "degraded"},
            "state": row.get("state"),
            "configured": bool(row.get("configured")),
            "online": bool(row.get("online")),
            "externalOwnership": bool(row.get("external_ownership")),
            "installedAt": row.get("installed_at"),
            "updatedAt": row.get("updated_at"),
            "health": _loads(row.get("health_json"), {}),
        }

    def verify_brand_asset(self, manifest: PluginManifest) -> dict[str, Any]:
        asset_path = (RESOURCE_ROOT / manifest.brand.file).resolve()
        if RESOURCE_ROOT.resolve() not in asset_path.parents:
            raise PluginManagerError("品牌资产路径越界", code="brand_asset_path_invalid")
        actual = _hash_path(asset_path)
        return {
            "ok": bool(actual and actual == manifest.brand.sha256),
            "path": str(asset_path),
            "expectedSha256": manifest.brand.sha256,
            "actualSha256": actual,
        }

    def list_catalog(self) -> dict[str, Any]:
        installations = self._catalog_installation_rows()
        snapshot, catalog_plugins = self._catalog_projection()
        plugins = []
        for plugin in catalog_plugins:
            plugin_id = str(plugin.get("id") or "")
            install = self._installation_payload(installations.get(plugin_id))
            plugins.append(
                {
                    **plugin,
                    "installation": install,
                }
            )
        return {"catalog": dict(snapshot), "plugins": plugins}

    def list_installed(self) -> dict[str, Any]:
        installations = self._installation_rows()
        items = []
        for plugin_id, row in sorted(installations.items()):
            manifest = plugin_catalog_service.get(plugin_id)
            items.append(
                {
                    "pluginId": plugin_id,
                    "displayName": manifest.displayName if manifest else plugin_id,
                    **self._installation_payload(row),
                    "components": [self._component_payload(item) for item in self._component_rows(plugin_id)],
                }
            )
        return {"items": items, "count": len(items)}

    def _component_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("component_id"),
            "type": row.get("component_type"),
            "path": row.get("owned_path"),
            "source": row.get("source_url"),
            "version": row.get("source_version"),
            "sha256": row.get("content_sha256"),
            "ownership": row.get("ownership"),
            "state": row.get("state"),
            "metadata": _loads(row.get("metadata_json"), {}),
        }

    def status_summary(self, *, session_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        installations = self._installation_rows()
        grants = self.active_grants(session_id=session_id, run_id=run_id) if session_id else []
        granted_ids = {item["pluginId"] for item in grants}
        items = []
        for manifest in plugin_catalog_service.load().plugins:
            state = self._installation_payload(installations.get(manifest.id))
            items.append(
                {
                    "pluginId": manifest.id,
                    "name": manifest.displayName,
                    "installed": state["installed"],
                    "configured": state["configured"],
                    "online": state["online"],
                    "authorized": manifest.id in granted_ids,
                    "needsAuthorization": manifest.id not in granted_ids,
                }
            )
        return {"runtime": self.runtime_descriptor(), "plugins": items}

    @staticmethod
    def _grantable_components(manifest: PluginManifest) -> list[dict[str, Any]]:
        components: list[dict[str, Any]] = []
        components.extend(
            {
                "id": item.id,
                "type": "cli",
                "actions": [action.id for action in item.actions],
            }
            for item in manifest.cliProfiles
        )
        components.extend({"id": item.id, "type": "skill"} for item in manifest.skills)
        components.extend(
            {
                "id": item.id,
                "type": "mcp",
                "tools": list(item.allowedTools),
            }
            for item in manifest.mcpServers
        )
        components.extend({"id": item.id, "type": "ui_adapter"} for item in manifest.uiAdapters)
        components.extend({"id": item.id, "type": "provider_adapter"} for item in manifest.providerAdapters)
        return components

    def supervisor_catalog(
        self,
        *,
        plugin_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(plugin_id or "").strip().lower()
        manifests = [self._manifest(normalized_id)] if normalized_id else list(plugin_catalog_service.load().plugins)
        items: list[dict[str, Any]] = []
        for manifest in manifests:
            readiness = self.readiness_status(manifest.id)
            authorization = (
                self.authorization_status(manifest.id, session_id=session_id, run_id=run_id)
                if session_id
                else {"authorized": False}
            )
            items.append(
                {
                    "pluginId": manifest.id,
                    "name": manifest.displayName,
                    "category": manifest.category,
                    "summary": manifest.description,
                    "status": readiness["status"],
                    "ready": readiness["ready"],
                    "authorized": bool(authorization.get("authorized")),
                    "components": self._grantable_components(manifest),
                    "configurationUrl": readiness["configurationUrl"],
                }
            )
        return {
            "mode": "status" if normalized_id else "list",
            "items": items,
            "count": len(items),
            "policy": "@插件是强提示；Supervisor 只能为当前 run 授权已就绪插件的最小组件集合。",
        }

    def authorize_for_supervisor(
        self,
        *,
        plugin_id: str,
        component_ids: Iterable[str],
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        grant = self.create_grant(
            plugin_id=plugin_id,
            scope="task",
            session_id=session_id,
            run_id=run_id,
            grantee_type="supervisor",
            grantee_id="supervisor",
            component_ids=component_ids,
            grant_source="supervisor_task",
        )
        return {
            "mode": "authorize",
            "status": "authorized",
            "pluginId": grant["pluginId"],
            "grant": grant,
            "nextAction": "下一轮将投影获授权的 Skill、MCP 或 CLI；委派时仅传递直接子 Agent 所需的组件子集。",
        }

    def _credential_bindings(self, plugin_id: str) -> dict[str, dict[str, Any]]:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM plugin_credential_bindings WHERE plugin_id=? ORDER BY requirement_id",
                (plugin_id,),
            ).fetchall()
        return {str(row["requirement_id"]): dict(row) for row in rows}

    def _redact_known_credentials(self, plugin_id: str, value: Any) -> Any:
        secrets_to_mask: list[str] = []
        for binding in self._credential_bindings(plugin_id).values():
            try:
                secret = self._credential_store.resolve(str(binding.get("secret_ref") or ""))
            except CredentialStoreError:
                continue
            if len(secret) >= 4:
                secrets_to_mask.append(secret)

        def visit(item: Any) -> Any:
            if isinstance(item, dict):
                return {
                    str(key): ("***" if SECRET_KEY_RE.search(str(key)) else visit(child))
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [visit(child) for child in item]
            if isinstance(item, str):
                result = item
                for secret in secrets_to_mask:
                    result = result.replace(secret, "***")
                return result
            return item

        return visit(value)

    def configuration_requirements(self, plugin_id: str) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        bindings = self._credential_bindings(manifest.id)
        manager_config = storage.get_plugin_manager_config()
        plugin_values = dict((manager_config.get("pluginConfigValues") or {}).get(manifest.id) or {})
        mcp_servers = dict(storage.get_mcp_config().get("mcpServers") or {})
        server_by_component = {server.id: dict(mcp_servers.get(server.serverName) or {}) for server in manifest.mcpServers}
        items: list[dict[str, Any]] = []
        for requirement in compile_plugin_requirements(manifest):
            binding = bindings.get(requirement.id)
            configured = False
            server_config = server_by_component.get(str(requirement.componentId or ""), {})
            if requirement.kind == "oauth":
                oauth_state = server_config.get("x-v8-oauth") if isinstance(server_config.get("x-v8-oauth"), dict) else {}
                oauth_ref = str(oauth_state.get("secretRef") or "").strip()
                configured = bool(oauth_ref) and self._credential_store.status(oauth_ref).configured
            elif binding:
                configured = self._credential_store.status(str(binding.get("secret_ref") or "")).configured
            elif requirement.kind not in {"secret", "oauth"}:
                if requirement.target == "env" and requirement.targetName:
                    configured = bool(str((server_config.get("env") or {}).get(requirement.targetName) or "").strip())
                elif requirement.target == "header" and requirement.targetName:
                    configured = bool(str((server_config.get("headers") or {}).get(requirement.targetName) or "").strip())
                elif requirement.target == "url":
                    configured = bool(str(server_config.get("url") or "").strip())
                else:
                    configured = requirement.id in plugin_values or bool(
                        requirement.id in (server_config.get("x-v8-config-values") or {})
                    )
            discoveries = [item.public_payload() for item in discover_requirement_sources(requirement)]
            status = "configured" if configured else ("missing" if requirement.required else "unknown")
            items.append(
                {
                    **requirement.model_dump(mode="json"),
                    "status": status,
                    "configured": configured,
                    "discovery": discoveries,
                    "availableForImport": any(bool(item.get("present")) for item in discoveries),
                }
            )
        required_items = [item for item in items if item["required"] and item["confidence"] != "hint"]
        return {
            "pluginId": manifest.id,
            "requirements": items,
            "hasRequirements": bool(items),
            "configured": all(item["status"] == "configured" for item in required_items),
        }

    def detect_configuration_sources(self, plugin_id: str) -> dict[str, Any]:
        """Refresh presence-only discovery without reading or returning any value."""

        return self.configuration_requirements(plugin_id)

    async def import_configuration_source(
        self,
        plugin_id: str,
        *,
        requirement_id: str,
        source_id: str,
    ) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        requirement = next(
            (item for item in compile_plugin_requirements(manifest) if item.id == requirement_id),
            None,
        )
        if requirement is None:
            raise PluginManagerError("配置要求不存在", code="configuration_requirement_not_found", status_code=404)
        advertised = {item.source_id for item in discover_requirement_sources(requirement) if item.present}
        if source_id not in advertised:
            raise PluginManagerError("导入来源不可用或未经声明", code="credential_import_source_invalid", status_code=409)
        try:
            value = read_explicit_import_source(source_id)
        except ValueError as exc:
            raise PluginManagerError(str(exc), code="credential_import_failed", status_code=409) from exc
        await self.configure(plugin_id, {requirement.id: value})
        self._event(
            manifest.id,
            "credential_imported",
            "ok",
            details={"requirementId": requirement.id, "sourceId": source_id},
        )
        return {
            "ok": True,
            "pluginId": manifest.id,
            "requirementId": requirement.id,
            "status": "configured",
        }

    def prepare_oauth(self, plugin_id: str, *, component_id: str | None = None) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        if manifest.id not in self._installation_rows():
            raise PluginManagerError("请先安装插件", code="plugin_not_installed", status_code=409)
        oauth_components = {
            str(item.componentId or "")
            for item in compile_plugin_requirements(manifest)
            if item.kind == "oauth"
        }
        selected = next(
            (
                server
                for server in manifest.mcpServers
                if server.id in oauth_components
                and (not component_id or server.id == str(component_id).strip())
            ),
            None,
        )
        if selected is None or selected.transport not in {"http", "sse"} or not selected.url:
            raise PluginManagerError("插件没有可用的 OAuth MCP 组件", code="plugin_oauth_component_not_found", status_code=404)
        payload = storage.get_mcp_config()
        servers = dict(payload.get("mcpServers") or {})
        current = dict(servers.get(selected.serverName) or self._expand_template(manifest, selected.configTemplate))
        current.update(
            {
                "url": selected.url,
                "oauth": True,
                "disabled": False,
                "x-v8-plugin-owner": manifest.id,
                "x-v8-plugin-component": selected.id,
                "x-v8-oauth-allowed-domains": sorted(
                    {str(item).strip().lower().lstrip(".") for item in manifest.officialDomains if str(item).strip()}
                ),
            }
        )
        servers[selected.serverName] = current
        payload["mcpServers"] = servers
        storage.save_mcp_config(payload)
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM plugin_components WHERE plugin_id=? AND component_id=?",
                (manifest.id, selected.id),
            ).fetchone()
            metadata = _loads(row["metadata_json"], {}) if row else {}
            metadata.update(
                {
                    "serverName": selected.serverName,
                    "allowedTools": list(selected.allowedTools),
                    "configSha256": _hash_value(current),
                }
            )
            conn.execute(
                "UPDATE plugin_components SET metadata_json=?, updated_at=? WHERE plugin_id=? AND component_id=?",
                (_json(metadata), utc_now_iso(), manifest.id, selected.id),
            )
            conn.commit()
        self._event(manifest.id, "oauth_started", "pending", details={"componentId": selected.id})
        return {
            "ok": True,
            "pluginId": manifest.id,
            "componentId": selected.id,
            "serverName": selected.serverName,
            "status": "connecting",
        }

    def refresh_configuration_status(self, plugin_id: str) -> dict[str, Any]:
        state = self.configuration_requirements(plugin_id)
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE plugin_installations SET configured=?, updated_at=? WHERE plugin_id=?",
                (1 if state["configured"] else 0, utc_now_iso(), plugin_id),
            )
            conn.commit()
        self._invalidate_catalog_installation_cache()
        return state

    def readiness_status(self, plugin_id: str) -> dict[str, Any]:
        manifest = plugin_catalog_service.get(plugin_id)
        if manifest is None:
            return {"pluginId": str(plugin_id or ""), "status": "invalid", "ready": False, "canAuthorize": False}
        row = self._installation_rows().get(manifest.id)
        state = self._installation_payload(row)
        if not state["installed"]:
            status = "not_installed"
        else:
            config_state = self.configuration_requirements(manifest.id)
            effective_configured = config_state["configured"] if config_state["hasRequirements"] else state["configured"]
            if not effective_configured:
                status = "needs_configuration"
            elif not state["online"]:
                status = "offline"
            else:
                status = "ready"
        return {
            "pluginId": manifest.id,
            "status": status,
            "ready": status == "ready",
            "canAuthorize": status == "ready",
            "configurationUrl": f"/admin/plugins?plugin={manifest.id}",
        }

    def authorization_status(
        self,
        plugin_id: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        grantee_type: str = "supervisor",
        grantee_id: str = "supervisor",
    ) -> dict[str, Any]:
        readiness = self.readiness_status(plugin_id)
        if not readiness["ready"]:
            return {**readiness, "authorized": False}
        session = db.get_session(str(session_id or "")) if session_id else None
        owner_user_id = str((session or {}).get("user_id") or "")
        manifest = self._manifest(readiness["pluginId"])
        current_digest = self._manifest_digest(manifest)
        active = bool(session_id) and any(
            grant["pluginId"] == readiness["pluginId"]
            and grant.get("ownerUserId") == owner_user_id
            and grant.get("manifestDigest") == current_digest
            and grant.get("state") == "active"
            for grant in self.active_grants(
                session_id=str(session_id),
                run_id=run_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
            )
        )
        status = "authorized" if active else "invalid"
        return {
            **readiness,
            "status": status,
            "authorized": status == "authorized",
            "reason": None if active else "grant_missing",
        }

    def _command_context(self, manifest: PluginManifest, *, plugin_root: Path | None = None) -> dict[str, str]:
        root = plugin_root or self._plugin_root(manifest.id)
        return {
            "pluginRoot": str(root),
            "pluginBin": str(root / "node_modules" / ".bin"),
            "shimRoot": str(self._bin_root()),
        }

    def _expand_argv(
        self,
        manifest: PluginManifest,
        command: CommandSpec,
        *,
        plugin_root: Path | None = None,
    ) -> list[str]:
        context = self._command_context(manifest, plugin_root=plugin_root)
        expanded = []
        for item in command.argv:
            value = str(item)
            for key, replacement in context.items():
                value = value.replace(f"{{{key}}}", replacement)
            expanded.append(value)
        return expanded

    def build_install_plan(self, plugin_id: str) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        current_platform = _platform_name()
        cli_steps = []
        approval_classes = set(manifest.governance.approvalClasses)
        for profile in manifest.cliProfiles:
            supported = current_platform in profile.platforms
            argv = self._expand_argv(manifest, profile.install)
            approval_required = bool(
                profile.ownership == "external"
                or profile.install.requiresElevation
                or profile.install.mayRestart
                or "system-install" in approval_classes
            )
            cli_steps.append(
                {
                    "componentId": profile.id,
                    "supported": supported,
                    "ownership": profile.ownership,
                    "argv": argv,
                    "estimatedDownloadMb": profile.install.estimatedDownloadMb,
                    "requiresElevation": profile.install.requiresElevation,
                    "mayRestart": profile.install.mayRestart,
                    "approvalRequired": approval_required,
                }
            )
        plan = {
            "pluginId": manifest.id,
            "displayName": manifest.displayName,
            "manifestVersion": manifest.version,
            "manifestDigest": self._manifest_digest(manifest),
            "catalogRevision": plugin_catalog_service.load().revision,
            "platform": current_platform,
            "installRoot": str(self._plugin_root(manifest.id)),
            "binRoot": str(self._bin_root()),
            "steps": {
                "preflight": True,
                "cli": cli_steps,
                "skills": [item.model_dump(mode="json") for item in manifest.skills],
                "mcp": [item.model_dump(mode="json") for item in manifest.mcpServers],
                "uiAdapters": [item.model_dump(mode="json") for item in manifest.uiAdapters],
                "providerAdapters": [item.model_dump(mode="json") for item in manifest.providerAdapters],
                "health": list(manifest.governance.healthChecks),
            },
            "approvalRequired": any(item["approvalRequired"] for item in cli_steps),
            "sideEffects": list(manifest.governance.sideEffects),
            "paidOperations": manifest.governance.paidOperations,
        }
        plan["planDigest"] = _hash_value(plan)
        return plan

    def create_install_job(
        self,
        plugin_id: str,
        *,
        dry_run: bool = True,
        approved: bool = False,
        plan_digest: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        plan = self.build_install_plan(plugin_id)
        expected_digest = str(plan["planDigest"])
        normalized_digest = str(plan_digest or "").strip()
        normalized_idempotency = str(idempotency_key or "").strip() or None
        if not dry_run and (not approved or normalized_digest != expected_digest):
            raise PluginManagerError(
                "安装必须批准当前 dry-run 计划，且 planDigest 必须完全匹配。",
                code="installation_approval_required",
                status_code=409,
            )
        with self._plugin_lock(manifest.id):
            if normalized_idempotency:
                with db.get_connection() as conn:
                    existing = conn.execute(
                        """
                        SELECT id, plan_digest, dry_run
                        FROM plugin_install_jobs
                        WHERE plugin_id=? AND idempotency_key=?
                        """,
                        (manifest.id, normalized_idempotency),
                    ).fetchone()
                if existing:
                    if (
                        str(existing["plan_digest"] or "") != expected_digest
                        or bool(existing["dry_run"]) != bool(dry_run)
                    ):
                        raise PluginManagerError(
                            "同一 idempotency key 已绑定到不同的安装计划。",
                            code="plugin_install_idempotency_conflict",
                            status_code=409,
                        )
                    return self.get_install_job(str(existing["id"]))
            with db.get_connection() as conn:
                active = conn.execute(
                    """
                    SELECT id FROM plugin_install_jobs
                    WHERE plugin_id=? AND dry_run=0
                      AND state NOT IN ('ready','rolled_back','rollback_failed','external_reconciliation_required','failed','completed')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (manifest.id,),
                ).fetchone()
            if active:
                raise PluginManagerError("该插件已有进行中的安装事务", code="plugin_install_in_progress", status_code=409)
            job_id = f"plugin_job_{uuid.uuid4().hex}"
            now = utc_now_iso()
            initial_state = "planned" if dry_run else "awaiting_approval"
            with db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO plugin_install_jobs
                    (id, plugin_id, action, state, dry_run, approval_required, approved, plan_json,
                     plan_digest, idempotency_key, created_at, updated_at)
                    VALUES (?, ?, 'install', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        manifest.id,
                        initial_state,
                        1 if dry_run else 0,
                        1 if plan["approvalRequired"] else 0,
                        1 if approved else 0,
                        _json(plan),
                        expected_digest,
                        normalized_idempotency,
                        now,
                        now,
                    ),
                )
                conn.commit()
            self._append_job_step(job_id, "plan", initial_state, {"planDigest": expected_digest})
            self._event(manifest.id, "install_planned", "ok", job_id=job_id, details={"dryRun": dry_run, "plan": plan})
        return self.get_install_job(job_id)

    def _append_job_step(self, job_id: str, step_type: str, state: str, details: dict[str, Any] | None = None) -> None:
        with db.get_connection() as conn:
            next_ordinal = int(
                conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM plugin_install_steps WHERE job_id=?",
                    (job_id,),
                ).fetchone()[0]
            )
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO plugin_install_steps
                (id, job_id, ordinal, state, step_type, details_json, created_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"plugin_step_{uuid.uuid4().hex}",
                    job_id,
                    next_ordinal,
                    state,
                    step_type,
                    _json(_redact(details or {})),
                    now,
                    now if state in {"completed", "failed", "ready", "rolled_back"} else None,
                ),
            )
            conn.commit()

    def _set_job_state(
        self,
        job_id: str,
        state: str,
        *,
        step_type: str | None = None,
        details: dict[str, Any] | None = None,
        staging_path: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE plugin_install_jobs SET state=?, updated_at=?, staging_path=COALESCE(?, staging_path) WHERE id=?",
                (state, now, staging_path, job_id),
            )
            conn.commit()
        self._append_job_step(job_id, step_type or state, state, details)

    def reconcile_install_jobs(self) -> dict[str, Any]:
        """Reconcile interrupted work without replaying an external installer."""

        terminal = {
            "planned",
            "awaiting_approval",
            "ready",
            "completed",
            "rolled_back",
            "rollback_failed",
            "failed",
            "external_reconciliation_required",
        }
        with db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM plugin_install_jobs WHERE dry_run=0 ORDER BY created_at").fetchall()
        reconciled: list[dict[str, str]] = []
        for raw in rows:
            row = dict(raw)
            if str(row.get("state") or "") in terminal:
                continue
            plan = _loads(row.get("plan_json"), {})
            has_external = any(bool(step.get("ownership") == "external") for step in list((plan.get("steps") or {}).get("cli") or []))
            staging = Path(str(row.get("staging_path") or "")) if row.get("staging_path") else None
            if staging and staging.exists() and _safe_owned_path(staging):
                shutil.rmtree(staging, ignore_errors=True)
            rollback: dict[str, Any] | None = None
            if has_external:
                next_state = "external_reconciliation_required"
            else:
                manifest = plugin_catalog_service.get(str(row.get("plugin_id") or ""))
                if manifest is None:
                    next_state = "rollback_failed"
                    rollback = {"ok": False, "errors": ["manifest unavailable during restart reconciliation"]}
                else:
                    installed_components = [
                        self._component_payload(item)
                        for item in self._component_rows(manifest.id)
                    ]
                    rollback = self._rollback(
                        manifest,
                        _loads(row.get("snapshot_json"), {}),
                        installed_components,
                    )
                    next_state = "rolled_back" if rollback["ok"] else "rollback_failed"
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE plugin_install_jobs SET state=?, external_reconciliation=?, result_json=?, updated_at=?, finished_at=? WHERE id=?",
                    (
                        next_state,
                        1 if has_external else 0,
                        _json({"rollback": rollback}) if rollback is not None else None,
                        utc_now_iso(),
                        utc_now_iso(),
                        row["id"],
                    ),
                )
                conn.commit()
            self._append_job_step(
                str(row["id"]),
                "startup_reconcile",
                next_state,
                {"external": has_external, "rollback": rollback},
            )
            reconciled.append({"jobId": str(row["id"]), "state": next_state})
        return {"reconciled": reconciled, "count": len(reconciled)}

    def get_install_job(self, job_id: str) -> dict[str, Any]:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM plugin_install_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise PluginManagerError("安装任务不存在", code="install_job_not_found", status_code=404)
        item = dict(row)
        with db.get_connection() as conn:
            steps = conn.execute(
                "SELECT * FROM plugin_install_steps WHERE job_id=? ORDER BY ordinal",
                (job_id,),
            ).fetchall()
        return {
            "jobId": item["id"],
            "pluginId": item["plugin_id"],
            "action": item["action"],
            "state": item["state"],
            "dryRun": bool(item["dry_run"]),
            "approvalRequired": bool(item["approval_required"]),
            "approved": bool(item["approved"]),
            "plan": _loads(item.get("plan_json"), {}),
            "planDigest": item.get("plan_digest"),
            "idempotencyKey": item.get("idempotency_key"),
            "stagingPath": item.get("staging_path"),
            "externalReconciliation": bool(item.get("external_reconciliation")),
            "snapshot": _loads(item.get("snapshot_json"), {}),
            "result": _loads(item.get("result_json"), {}),
            "error": item.get("error_message"),
            "createdAt": item.get("created_at"),
            "startedAt": item.get("started_at"),
            "finishedAt": item.get("finished_at"),
            "steps": [
                {
                    "ordinal": step["ordinal"],
                    "type": step["step_type"],
                    "state": step["state"],
                    "details": _loads(step["details_json"], {}),
                    "createdAt": step["created_at"],
                    "finishedAt": step["finished_at"],
                }
                for step in steps
            ],
        }

    def list_install_jobs(self, *, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM plugin_install_jobs ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return {"items": [self.get_install_job(str(row["id"])) for row in rows]}

    async def run_install_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_install_job(job_id)
        if job["dryRun"]:
            return job
        if job["state"] != "awaiting_approval":
            return job
        manifest = self._manifest(job["pluginId"])
        with self._plugin_lock(manifest.id):
            # Re-read inside the per-plugin critical section. Concurrent async
            # callers share the same OS thread, so the RLock alone cannot
            # serialize coroutines across awaits; the persisted state is the
            # single-claim guard.
            job = self.get_install_job(job_id)
            if job["state"] != "awaiting_approval":
                return job
            current_plan = self.build_install_plan(manifest.id)
            if str(job.get("planDigest") or "") != str(current_plan.get("planDigest") or ""):
                self._finish_job(job_id, state="failed", result={}, error="approved plan digest no longer matches catalog")
                return self.get_install_job(job_id)
            root = self._plugin_root(manifest.id)
            staging = root.parent / ".staging" / job_id
            backup = root.parent / ".staging" / f"{job_id}.previous"
            snapshot = self._snapshot(manifest)
            snapshot["backupPath"] = str(backup)
            now = utc_now_iso()
            with db.get_connection() as conn:
                claimed = conn.execute(
                    """
                    UPDATE plugin_install_jobs
                    SET state='staging', snapshot_json=?, staging_path=?, started_at=?, updated_at=?, error_message=NULL
                    WHERE id=? AND state='awaiting_approval'
                    """,
                    (_json(snapshot), str(staging), now, now, job_id),
                )
                conn.commit()
            if claimed.rowcount != 1:
                return self.get_install_job(job_id)
            self._append_job_step(job_id, "staging", "staging", {"path": str(staging)})
            created_components: list[dict[str, Any]] = []
            external_started = False
            try:
                if staging.exists():
                    shutil.rmtree(staging)
                staging.mkdir(parents=True, exist_ok=True)
                self._bin_root().mkdir(parents=True, exist_ok=True)
                AGENT_SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
                PLUGIN_MANAGER_LOG_ROOT.mkdir(parents=True, exist_ok=True)

                self._set_job_state(job_id, "verifying", step_type="catalog_and_brand_verification")
                brand = self.verify_brand_asset(manifest)
                if not brand["ok"]:
                    raise PluginManagerError("品牌资产校验失败", code="brand_asset_hash_mismatch")

                self._set_job_state(job_id, "installing", step_type="component_install")
                self._upsert_installation(
                    manifest,
                    state="installing",
                    health={"ok": False, "online": False, "checks": []},
                    external=any(profile.ownership == "external" for profile in manifest.cliProfiles),
                )
                profile_results: list[tuple[CliProfile, dict[str, Any]]] = []
                for profile in manifest.cliProfiles:
                    if _platform_name() not in profile.platforms:
                        continue
                    if profile.ownership == "external":
                        external_started = True
                        if profile.install.requiresElevation:
                            self._set_job_state(job_id, "waiting_for_elevation", step_type="external_elevation")
                            result = await asyncio.to_thread(self._execute_elevated_spec, manifest, profile.install)
                        else:
                            result = await asyncio.to_thread(self._execute_spec, manifest, profile.install)
                        self._set_job_state(job_id, "reconciling", step_type="external_reconcile")
                    else:
                        result = await asyncio.to_thread(
                            self._execute_spec,
                            manifest,
                            profile.install,
                            plugin_root=staging,
                        )
                    if result["returnCode"] != 0:
                        raise PluginManagerError(
                            f"CLI 安装失败：{profile.id}: {result['stderrTail'] or result['stdoutTail']}",
                            code="cli_install_failed",
                        )
                    profile_results.append((profile, result))

                self._set_job_state(job_id, "validating", step_type="pre_commit_validation")
                self._set_job_state(job_id, "committing", step_type="atomic_commit")
                if any(profile.ownership == "managed" for profile, _ in profile_results):
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if backup.exists():
                        shutil.rmtree(backup)
                    if root.exists():
                        root.replace(backup)
                    staging.replace(root)

                for profile, result in profile_results:
                    created_components.append(self._register_cli_component(manifest, profile, result))
                    created_components.extend(self._ensure_cli_shims(manifest, profile))
                for skill in manifest.skills:
                    created_components.extend(
                        await asyncio.to_thread(
                            self._install_skill_component,
                            manifest,
                            skill.model_dump(mode="json"),
                        )
                    )
                if manifest.mcpServers:
                    created_components.extend(self._install_mcp_components(manifest))
                for adapter in manifest.uiAdapters:
                    created_components.append(
                        self._register_component(
                            manifest.id,
                            adapter.id,
                            "ui_adapter",
                            source_url=manifest.officialLinks.documentation,
                            metadata=adapter.model_dump(mode="json"),
                        )
                    )
                for adapter in manifest.providerAdapters:
                    created_components.append(
                        self._register_component(
                            manifest.id,
                            adapter.id,
                            "provider_adapter",
                            source_url=manifest.officialLinks.documentation,
                            metadata=adapter.model_dump(mode="json"),
                        )
                    )

                health = await self.doctor(manifest.id, persist=False)
                external = any(profile.ownership == "external" for profile in manifest.cliProfiles)
                install_state = "installed" if health["ok"] or not manifest.cliProfiles else "degraded"
                receipt = {
                    "manifestDigest": self._manifest_digest(manifest),
                    "catalogRevision": plugin_catalog_service.load().revision,
                    "components": created_components,
                    "committedAt": utc_now_iso(),
                }
                self._upsert_installation(manifest, state=install_state, health=health, external=external, receipt=receipt)
                result = {"ok": True, "state": install_state, "components": created_components, "health": health, "receipt": receipt}
                self._finish_job(job_id, state="ready", result=result)
                self._append_job_step(job_id, "complete", "ready", {"state": install_state})
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                self._event(manifest.id, "install_completed", "ok", job_id=job_id, details=result)
                self._refresh_extensions()
                return self.get_install_job(job_id)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if external_started:
                    self._finish_job(
                        job_id,
                        state="external_reconciliation_required",
                        result={"externalReconciliationRequired": True},
                        error=error,
                    )
                    with db.get_connection() as conn:
                        conn.execute("UPDATE plugin_install_jobs SET external_reconciliation=1 WHERE id=?", (job_id,))
                        conn.commit()
                    self._append_job_step(job_id, "external_reconcile", "external_reconciliation_required", {"error": error})
                    self._event(manifest.id, "install_reconciliation_required", "error", job_id=job_id, details={"error": error})
                    return self.get_install_job(job_id)
                self._set_job_state(job_id, "rolling_back", step_type="rollback")
                rollback = self._rollback(manifest, snapshot, created_components)
                final_state = "rolled_back" if rollback["ok"] else "rollback_failed"
                self._finish_job(job_id, state=final_state, result={"rollback": rollback}, error=error)
                self._append_job_step(job_id, "rollback", final_state, rollback)
                self._event(manifest.id, "install_failed", "error", job_id=job_id, details={"error": error, "rollback": rollback})
                return self.get_install_job(job_id)

    def _execute_elevated_spec(self, manifest: PluginManifest, spec: CommandSpec) -> dict[str, Any]:
        if _platform_name() != "windows":
            return self._execute_spec(manifest, spec)
        argv = self._expand_argv(manifest, spec)
        if not argv or Path(argv[0]).name.lower() not in {"winget", "winget.exe"}:
            return {"argv": ["elevated", "<rejected>"], "returnCode": 126, "stdoutTail": "", "stderrTail": "only allowlisted winget elevation is supported", "durationMs": 0}
        started = datetime.now(timezone.utc)
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        args = ",".join(quote(item) for item in argv[1:])
        script = f"$p=Start-Process -FilePath {quote(argv[0])} -ArgumentList @({args}) -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeoutSeconds,
        )
        return {
            "argv": ["winget", "<approved-external-install>"],
            "returnCode": completed.returncode,
            "stdoutTail": (completed.stdout or "")[-1000:],
            "stderrTail": (completed.stderr or "")[-1000:],
            "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        }

    def _snapshot(self, manifest: PluginManifest) -> dict[str, Any]:
        mcp_payload = storage.get_mcp_config()
        return {
            "pluginRootExisted": self._plugin_root(manifest.id).exists(),
            "mcpConfig": mcp_payload,
            "components": self._component_rows(manifest.id),
            "installation": self._installation_rows().get(manifest.id),
        }

    def _execute_spec(
        self,
        manifest: PluginManifest,
        spec: CommandSpec,
        *,
        plugin_root: Path | None = None,
        env_overlay: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        argv = self._expand_argv(manifest, spec, plugin_root=plugin_root)
        started = datetime.now(timezone.utc)
        if spec.downloadUrl and spec.downloadTarget and spec.downloadSha256:
            effective_root = (plugin_root or self._plugin_root(manifest.id)).resolve()
            target_text = str(spec.downloadTarget).replace("{pluginRoot}", str(effective_root))
            target = Path(target_text).expanduser().resolve()
            if target != effective_root and effective_root not in target.parents:
                return {
                    "argv": argv,
                    "returnCode": 126,
                    "stdoutTail": "",
                    "stderrTail": "managed download target escapes plugin root",
                    "durationMs": 0,
                }
            partial = target.with_suffix(target.suffix + ".part")
            try:
                import httpx

                response = httpx.get(spec.downloadUrl, timeout=spec.timeoutSeconds, follow_redirects=True)
                response.raise_for_status()
                actual = hashlib.sha256(response.content).hexdigest()
                if actual != spec.downloadSha256:
                    raise ValueError(f"download SHA-256 mismatch: expected {spec.downloadSha256}, got {actual}")
                target.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(response.content)
                partial.replace(target)
                return {
                    "argv": ["managed-download", spec.downloadUrl, str(target)],
                    "returnCode": 0,
                    "stdoutTail": f"verified {actual}",
                    "stderrTail": "",
                    "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                }
            except Exception as exc:
                partial.unlink(missing_ok=True)
                return {
                    "argv": ["managed-download", spec.downloadUrl, str(target)],
                    "returnCode": 1,
                    "stdoutTail": "",
                    "stderrTail": f"{type(exc).__name__}: {exc}",
                    "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                }
        try:
            completed = subprocess.run(
                argv,
                cwd=spec.cwd or None,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeoutSeconds,
                env={
                    **os.environ,
                    "PATH": f"{self._bin_root()}{os.pathsep}{os.environ.get('PATH', '')}",
                    **dict(env_overlay or {}),
                },
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            return {
                "argv": argv,
                "returnCode": completed.returncode,
                "stdoutTail": stdout[-4000:],
                "stderrTail": stderr[-4000:],
                "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except FileNotFoundError as exc:
            return {"argv": argv, "returnCode": 127, "stdoutTail": "", "stderrTail": str(exc), "durationMs": 0}
        except subprocess.TimeoutExpired as exc:
            return {
                "argv": argv,
                "returnCode": 124,
                "stdoutTail": str(exc.stdout or "")[-4000:],
                "stderrTail": "command timed out",
                "durationMs": spec.timeoutSeconds * 1000,
            }

    def _register_cli_component(self, manifest: PluginManifest, profile: CliProfile, result: dict[str, Any]) -> dict[str, Any]:
        root = self._plugin_root(manifest.id)
        return self._register_component(
            manifest.id,
            profile.id,
            "cli",
            owned_path=str(root) if profile.ownership == "managed" else "",
            source_url=manifest.officialLinks.documentation,
            source_version=manifest.version,
            ownership=profile.ownership,
            metadata={"commands": profile.commands, "installResult": _redact(result)},
        )

    def _ensure_cli_shims(self, manifest: PluginManifest, profile: CliProfile) -> list[dict[str, Any]]:
        if profile.ownership != "managed":
            return []
        plugin_bin = self._plugin_root(manifest.id) / "node_modules" / ".bin"
        rows = []
        for command in profile.commands:
            if profile.shimCommand:
                launcher = [str(self._expand_template(manifest, item)) for item in profile.shimCommand]
                command_line = subprocess.list2cmdline(launcher)
                source_description = command_line
            else:
                source = plugin_bin / f"{command}.cmd"
                if not source.exists():
                    source = plugin_bin / command
                if not source.exists():
                    continue
                command_line = f'"{source}"'
                source_description = str(source)
            shim = self._bin_root() / f"{command}.cmd"
            shim.write_text(f"@echo off\r\n{command_line} %*\r\n", encoding="utf-8")
            rows.append(
                self._register_component(
                    manifest.id,
                    f"{profile.id}:shim:{command}",
                    "cli",
                    owned_path=str(shim),
                    source_url=manifest.officialLinks.documentation,
                    source_version=manifest.version,
                    metadata={"command": command, "target": source_description},
                )
            )
        return rows

    @staticmethod
    def _run_skill_git_step(argv: list[str], *, timeout_seconds: int = 300) -> dict[str, Any]:
        command = [str(item) for item in argv]
        environment = dict(os.environ)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                completed = subprocess.run(
                    command,
                    shell=False,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout_seconds,
                    env=environment,
                )
            except FileNotFoundError as exc:
                raise PluginManagerError("Skill 安装需要受支持的 Git 可执行文件", code="skill_git_unavailable") from exc
            except subprocess.TimeoutExpired as exc:
                raise PluginManagerError("Skill Git 操作超时", code="skill_install_timeout") from exc

            def tail(handle: Any, limit: int = 4000) -> str:
                size = handle.tell()
                handle.seek(max(0, size - limit))
                return handle.read(limit).decode("utf-8", errors="replace")

            result = {
                "returnCode": int(completed.returncode),
                "stdoutTail": tail(stdout_file),
                "stderrTail": tail(stderr_file),
            }
        if result["returnCode"] != 0:
            detail = str(result["stderrTail"] or result["stdoutTail"] or "unknown git error").strip()
            raise PluginManagerError(f"Skill Git 操作失败：{detail[-2000:]}", code="skill_install_failed")
        return result

    def _install_skill_component(self, manifest: PluginManifest, skill: dict[str, Any]) -> list[dict[str, Any]]:
        source_kind = str(skill.get("sourceKind") or "git").strip().lower()
        if source_kind == "managed_cli":
            return self._install_managed_cli_skill_component(manifest, skill)

        temp_root = Path(tempfile.mkdtemp(prefix=f"v8-plugin-{manifest.id}-"))
        try:
            repo_root = temp_root / "repo"
            revision = str(skill["revision"])
            self._run_skill_git_step(["git", "init", str(repo_root)])
            self._run_skill_git_step(
                ["git", "-C", str(repo_root), "remote", "add", "origin", str(skill["repository"])]
            )
            self._run_skill_git_step(
                ["git", "-C", str(repo_root), "fetch", "--depth", "1", "--no-tags", "origin", revision]
            )
            self._run_skill_git_step(["git", "-C", str(repo_root), "checkout", "--detach", "FETCH_HEAD"])
            verified = self._run_skill_git_step(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
            if str(verified.get("stdoutTail") or "").strip().lower() != revision.lower():
                raise PluginManagerError("Skill Git 提交校验失败", code="skill_revision_mismatch")
            source_root = (repo_root / skill["path"]).resolve()
            if repo_root.resolve() not in source_root.parents or not source_root.exists():
                raise PluginManagerError("官方 Skill 路径不存在或越界", code="skill_path_invalid")
            target_root = AGENT_SKILLS_ROOT / skill["targetDirectory"]
            if target_root.exists():
                raise PluginManagerError(
                    f"Skill 目标目录已存在：{target_root}",
                    code="skill_target_exists",
                    status_code=409,
                )
            shutil.copytree(source_root, target_root)
            return [
                self._register_component(
                    manifest.id,
                    skill["id"],
                    "skill",
                    owned_path=str(target_root),
                    source_url=skill["repository"],
                    source_version=skill["revision"],
                    metadata={"officialOrganization": skill["officialOrganization"], "sourcePath": skill["path"]},
                )
            ]
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def _install_managed_cli_skill_component(
        self,
        manifest: PluginManifest,
        skill: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source_component_id = str(skill.get("sourceComponentId") or "").strip()
        source_profile = next(
            (
                profile
                for profile in manifest.cliProfiles
                if profile.id == source_component_id and profile.ownership == "managed"
            ),
            None,
        )
        if source_profile is None:
            raise PluginManagerError(
                "Skill 来源未绑定到受管 CLI 组件",
                code="skill_source_component_invalid",
            )

        plugin_root = self._plugin_root(manifest.id).resolve()
        source_root = (plugin_root / str(skill["path"])).resolve()
        if plugin_root != source_root and plugin_root not in source_root.parents:
            raise PluginManagerError("受管 CLI Skill 路径越界", code="skill_path_invalid")
        if not source_root.exists():
            raise PluginManagerError("受管 CLI 包未提供声明的 Skill", code="skill_path_invalid")

        target_root = AGENT_SKILLS_ROOT / str(skill["targetDirectory"])
        if target_root.exists():
            raise PluginManagerError(
                f"Skill 目标目录已存在：{target_root}",
                code="skill_target_exists",
                status_code=409,
            )
        staging_target = target_root.with_name(f".{target_root.name}.staging-{uuid.uuid4().hex}")
        try:
            if source_root.is_dir():
                shutil.copytree(source_root, staging_target)
            else:
                staging_target.mkdir(parents=True, exist_ok=False)
                target_name = "SKILL.md" if source_root.name.lower() == "skill.md" else source_root.name
                shutil.copy2(source_root, staging_target / target_name)
            staging_target.replace(target_root)
        except Exception:
            if staging_target.exists():
                shutil.rmtree(staging_target, ignore_errors=True)
            if target_root.exists():
                shutil.rmtree(target_root, ignore_errors=True)
            raise

        return [
            self._register_component(
                manifest.id,
                str(skill["id"]),
                "skill",
                owned_path=str(target_root),
                source_url=str(skill["repository"]),
                source_version=str(skill["revision"]),
                metadata={
                    "officialOrganization": skill["officialOrganization"],
                    "sourceKind": "managed_cli",
                    "sourceComponentId": source_component_id,
                    "sourcePath": skill["path"],
                },
            )
        ]

    def _install_mcp_components(self, manifest: PluginManifest) -> list[dict[str, Any]]:
        payload = storage.get_mcp_config()
        servers = dict(payload.get("mcpServers") or {})
        rows = []
        for server in manifest.mcpServers:
            if server.serverName in servers and str((servers[server.serverName] or {}).get("x-v8-plugin-owner") or "") != manifest.id:
                raise PluginManagerError(
                    f"MCP server 名称已被用户配置占用：{server.serverName}",
                    code="mcp_server_conflict",
                    status_code=409,
                )
            config = self._expand_template(manifest, server.configTemplate)
            config["disabled"] = True
            config["x-v8-plugin-owner"] = manifest.id
            config["x-v8-plugin-component"] = server.id
            if bool(config.get("oauth")) or any(str(field).lower() == "oauth" for field in server.authFields):
                config["oauth"] = True
                config["x-v8-oauth-allowed-domains"] = list(manifest.officialDomains)
            servers[server.serverName] = config
            rows.append(
                self._register_component(
                    manifest.id,
                    server.id,
                    "mcp",
                    source_url=server.repository or server.url or "",
                    source_version=manifest.version,
                    metadata={"serverName": server.serverName, "allowedTools": server.allowedTools, "configSha256": _hash_value(config)},
                )
            )
        payload["mcpServers"] = servers
        storage.save_mcp_config(payload)
        return rows

    def _expand_template(self, manifest: PluginManifest, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._expand_template(manifest, item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._expand_template(manifest, item) for item in value]
        if isinstance(value, str):
            context = self._command_context(manifest)
            result = value
            for key, replacement in context.items():
                result = result.replace(f"{{{key}}}", replacement)
            for profile in manifest.cliProfiles:
                for command in profile.commands:
                    result = result.replace(f"{{shim:{command}}}", str(self._bin_root() / f"{command}.cmd"))
            return result
        return value

    def _register_component(
        self,
        plugin_id: str,
        component_id: str,
        component_type: str,
        *,
        owned_path: str = "",
        source_url: str = "",
        source_version: str = "",
        ownership: str = "managed",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not SAFE_COMPONENT_ID_RE.match(component_id):
            raise PluginManagerError("组件 ID 不合法", code="component_id_invalid")
        path = Path(owned_path) if owned_path else None
        digest = _hash_path(path) if path and ownership == "managed" else ""
        now = utc_now_iso()
        row_id = f"plugin_component_{uuid.uuid4().hex}"
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO plugin_components
                (id, plugin_id, component_id, component_type, owned_path, source_url, source_version,
                 content_sha256, ownership, state, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'installed', ?, ?, ?)
                ON CONFLICT(plugin_id, component_id) DO UPDATE SET
                    component_type=excluded.component_type,
                    owned_path=excluded.owned_path,
                    source_url=excluded.source_url,
                    source_version=excluded.source_version,
                    content_sha256=excluded.content_sha256,
                    ownership=excluded.ownership,
                    state='installed',
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    row_id,
                    plugin_id,
                    component_id,
                    component_type,
                    owned_path or None,
                    source_url or None,
                    source_version or None,
                    digest or None,
                    ownership,
                    _json(metadata or {}),
                    now,
                    now,
                ),
            )
            conn.commit()
        self._invalidate_ownership_cache()
        return {"id": component_id, "type": component_type, "path": owned_path or None, "sha256": digest or None}

    def _upsert_installation(
        self,
        manifest: PluginManifest,
        *,
        state: str,
        health: dict[str, Any],
        external: bool,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        required_configuration = [
            item
            for item in compile_plugin_requirements(manifest)
            if item.required and item.confidence != "hint"
        ]
        auto_configured = 0 if required_configuration else 1
        with db.get_connection() as conn:
            previous = conn.execute(
                "SELECT manifest_digest FROM plugin_installations WHERE plugin_id=?",
                (manifest.id,),
            ).fetchone()
            previous_digest = str(previous["manifest_digest"] or "") if previous else ""
            current_digest = self._manifest_digest(manifest)
            conn.execute(
                """
                INSERT INTO plugin_installations
                (plugin_id, manifest_version, catalog_revision, state, install_root, external_ownership,
                 configured, online, health_json, installed_at, updated_at, manifest_digest, receipt_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    manifest_version=excluded.manifest_version,
                    catalog_revision=excluded.catalog_revision,
                    state=excluded.state,
                    install_root=excluded.install_root,
                    external_ownership=excluded.external_ownership,
                    configured=CASE WHEN excluded.configured=1 THEN 1 ELSE plugin_installations.configured END,
                    online=excluded.online,
                    health_json=excluded.health_json,
                    manifest_digest=excluded.manifest_digest,
                    receipt_json=COALESCE(excluded.receipt_json, plugin_installations.receipt_json),
                    installed_at=COALESCE(plugin_installations.installed_at, excluded.installed_at),
                    updated_at=excluded.updated_at
                """,
                (
                    manifest.id,
                    manifest.version,
                    plugin_catalog_service.load().revision,
                    state,
                    str(self._plugin_root(manifest.id)),
                    1 if external else 0,
                    auto_configured,
                    1 if health.get("online") else 0,
                    _json(health),
                    now,
                    now,
                    current_digest,
                    _json(receipt) if receipt is not None else None,
                ),
            )
            if previous_digest and previous_digest != current_digest:
                conn.execute(
                    "UPDATE plugin_grants SET state='invalidated', revoked_at=?, terminal_reason='manifest_changed' WHERE plugin_id=? AND state='active'",
                    (now, manifest.id),
                )
            conn.commit()
        self._invalidate_catalog_installation_cache()
        if previous_digest and previous_digest != current_digest:
            self._invalidate_grant_cache()

    def _finish_job(self, job_id: str, *, state: str, result: dict[str, Any], error: str = "") -> None:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE plugin_install_jobs SET state=?, result_json=?, error_message=?, finished_at=?, updated_at=? WHERE id=?",
                (state, _json(_redact(result)), error or None, utc_now_iso(), utc_now_iso(), job_id),
            )
            conn.commit()

    def _rollback(self, manifest: PluginManifest, snapshot: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, Any]:
        removed = []
        errors = []
        for component in reversed(components):
            path_text = str(component.get("path") or "").strip()
            if not path_text:
                continue
            path = Path(path_text)
            try:
                if path.exists() and _safe_owned_path(path):
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    removed.append(path_text)
            except Exception as exc:
                errors.append(f"{path_text}: {exc}")
        try:
            storage.save_mcp_config(dict(snapshot.get("mcpConfig") or {}))
        except Exception as exc:
            errors.append(f"mcp restore: {exc}")
        backup_text = str(snapshot.get("backupPath") or "").strip()
        if backup_text:
            backup = Path(backup_text)
            root = self._plugin_root(manifest.id)
            try:
                if backup.exists():
                    if root.exists():
                        if root.is_dir():
                            shutil.rmtree(root)
                        else:
                            root.unlink()
                    backup.replace(root)
            except Exception as exc:
                errors.append(f"plugin root restore: {exc}")
        with db.get_connection() as conn:
            conn.execute("DELETE FROM plugin_components WHERE plugin_id = ?", (manifest.id,))
            conn.execute("DELETE FROM plugin_installations WHERE plugin_id = ?", (manifest.id,))
            installation = snapshot.get("installation")
            if isinstance(installation, dict) and installation:
                columns = [name for name in installation if name in self._table_columns(conn, "plugin_installations")]
                conn.execute(
                    f"INSERT INTO plugin_installations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(installation[name] for name in columns),
                )
            for component in list(snapshot.get("components") or []):
                if not isinstance(component, dict):
                    continue
                columns = [name for name in component if name in self._table_columns(conn, "plugin_components")]
                conn.execute(
                    f"INSERT INTO plugin_components ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(component[name] for name in columns),
                )
            conn.commit()
        self._invalidate_catalog_installation_cache()
        self._invalidate_ownership_cache()
        self._invalidate_grant_cache()
        return {"ok": not errors, "removed": removed, "errors": errors}

    def _bind_credential(
        self,
        manifest: PluginManifest,
        requirement: PluginConfigRequirement,
        value: str,
    ) -> str:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT secret_ref FROM plugin_credential_bindings WHERE plugin_id=? AND requirement_id=?",
                (manifest.id, requirement.id),
            ).fetchone()
        existing_ref = str(row["secret_ref"] or "") if row else None
        try:
            secret_ref = self._credential_store.put(value, reference=existing_ref)
        except CredentialStoreError as exc:
            raise PluginManagerError(str(exc), code="secure_credential_store_unavailable", status_code=503) from exc
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO plugin_credential_bindings
                (id, plugin_id, component_id, requirement_id, target, target_name, secret_ref, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id, requirement_id) DO UPDATE SET
                    component_id=excluded.component_id,
                    target=excluded.target,
                    target_name=excluded.target_name,
                    secret_ref=excluded.secret_ref,
                    updated_at=excluded.updated_at
                """,
                (
                    f"plugin_credential_{uuid.uuid4().hex}",
                    manifest.id,
                    requirement.componentId or "",
                    requirement.id,
                    requirement.target,
                    requirement.targetName,
                    secret_ref,
                    now,
                    now,
                ),
            )
            conn.commit()
        return secret_ref

    async def configure(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        values = dict(values or {})
        manifest = self._manifest(plugin_id)
        installation = self._installation_rows().get(manifest.id)
        if not installation:
            raise PluginManagerError("请先安装插件", code="plugin_not_installed", status_code=409)
        requirements = compile_plugin_requirements(manifest)
        by_component: dict[str, list[PluginConfigRequirement]] = {}
        for requirement in requirements:
            by_component.setdefault(str(requirement.componentId or ""), []).append(requirement)

        manager_config = storage.get_plugin_manager_config()
        all_plugin_values = dict(manager_config.get("pluginConfigValues") or {})
        plugin_values = dict(all_plugin_values.get(manifest.id) or {})
        mcp_component_ids = {item.id for item in manifest.mcpServers}
        changed_non_mcp_components: set[str] = set()
        for requirement in requirements:
            raw_value = values.get(requirement.id)
            if raw_value in (None, "") and requirement.targetName:
                raw_value = values.get(requirement.targetName)
            if requirement.kind == "oauth":
                if raw_value not in (None, ""):
                    raise PluginManagerError(
                        "OAuth 配置必须通过系统浏览器授权，不能手工输入 token。",
                        code="oauth_browser_flow_required",
                        status_code=409,
                    )
                continue
            if raw_value in (None, ""):
                continue
            if requirement.kind == "boolean":
                if isinstance(raw_value, bool):
                    normalized_value: Any = raw_value
                elif str(raw_value).strip().lower() in {"true", "1", "yes", "on"}:
                    normalized_value = True
                elif str(raw_value).strip().lower() in {"false", "0", "no", "off"}:
                    normalized_value = False
                else:
                    raise PluginManagerError("布尔配置值无效", code="configuration_value_invalid")
            else:
                normalized_value = raw_value
            if requirement.kind == "enum" and str(normalized_value) not in set(requirement.options):
                raise PluginManagerError("枚举配置值不在允许范围内", code="configuration_value_invalid")
            if requirement.kind == "url":
                from urllib.parse import urlparse

                parsed = urlparse(str(normalized_value).strip())
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise PluginManagerError("URL 配置必须是有效的 HTTP/HTTPS 地址", code="configuration_value_invalid")
            values[requirement.id] = normalized_value
            if requirement.kind == "secret":
                self._bind_credential(manifest, requirement, str(normalized_value))
            elif str(requirement.componentId or "") not in mcp_component_ids:
                plugin_values[requirement.id] = normalized_value
            changed_non_mcp_components.add(str(requirement.componentId or ""))
        if plugin_values:
            all_plugin_values[manifest.id] = plugin_values
            manager_config["pluginConfigValues"] = all_plugin_values
            storage.save_plugin_manager_config(manager_config)

        payload = storage.get_mcp_config()
        servers = dict(payload.get("mcpServers") or {})
        configured_components: list[str] = sorted(item for item in changed_non_mcp_components if item)
        bindings = self._credential_bindings(manifest.id)
        for server in manifest.mcpServers:
            current = dict(servers.get(server.serverName) or self._expand_template(manifest, server.configTemplate))
            env = dict(current.get("env") or {})
            headers = dict(current.get("headers") or {})
            refs = dict(current.get("x-v8-credential-refs") or {})
            for requirement in by_component.get(server.id, []):
                raw_value = values.get(requirement.id)
                if raw_value in (None, "") and requirement.targetName:
                    raw_value = values.get(requirement.targetName)
                value = str(raw_value or "").strip()
                if requirement.kind == "oauth":
                    continue
                if requirement.kind == "secret":
                    binding = bindings.get(requirement.id)
                    if binding:
                        refs[requirement.id] = {
                            "secretRef": str(binding.get("secret_ref") or ""),
                            "target": requirement.target,
                            "targetName": requirement.targetName,
                        }
                    continue
                if not value:
                    continue
                elif requirement.target == "env" and requirement.targetName:
                    env[requirement.targetName] = value
                elif requirement.target == "header" and requirement.targetName:
                    headers[requirement.targetName] = value
                elif requirement.target == "url":
                    current["url"] = value
                else:
                    current.setdefault("x-v8-config-values", {})[requirement.id] = raw_value
            if env:
                current["env"] = env
            if headers:
                current["headers"] = headers
            if refs:
                current["x-v8-credential-refs"] = refs
            current["disabled"] = bool(values.get("disabled", False))
            current["x-v8-plugin-owner"] = manifest.id
            current["x-v8-plugin-component"] = server.id
            servers[server.serverName] = current
            if server.id not in configured_components:
                configured_components.append(server.id)
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT metadata_json FROM plugin_components WHERE plugin_id=? AND component_id=?",
                    (manifest.id, server.id),
                ).fetchone()
                metadata = _loads(row["metadata_json"], {}) if row else {}
                metadata["serverName"] = server.serverName
                metadata["allowedTools"] = list(server.allowedTools)
                metadata["configSha256"] = _hash_value(current)
                conn.execute(
                    "UPDATE plugin_components SET metadata_json=?, updated_at=? WHERE plugin_id=? AND component_id=?",
                    (_json(metadata), utc_now_iso(), manifest.id, server.id),
                )
                conn.commit()
        payload["mcpServers"] = servers
        storage.save_mcp_config(payload)
        config_state = self.configuration_requirements(manifest.id)
        configured = bool(config_state["configured"])
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE plugin_installations SET configured=?, updated_at=? WHERE plugin_id=?",
                (1 if configured else 0, utc_now_iso(), manifest.id),
            )
            conn.commit()
        self._invalidate_catalog_installation_cache()
        self._event(
            manifest.id,
            "configured",
            "ok" if configured else "incomplete",
            details={"components": configured_components, "configured": configured},
        )
        self._refresh_extensions()
        return {
            "ok": configured,
            "pluginId": manifest.id,
            "configuredComponents": configured_components,
            "configuration": config_state,
        }

    async def doctor(self, plugin_id: str, *, persist: bool = True) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        checks = []
        for profile in manifest.cliProfiles:
            if _platform_name() not in profile.platforms:
                continue
            result = await asyncio.to_thread(self._execute_spec, manifest, profile.version)
            checks.append(
                {
                    "componentId": profile.id,
                    "kind": "cli-version",
                    "ok": result["returnCode"] == 0,
                    "summary": (result["stdoutTail"] or result["stderrTail"])[-500:],
                }
            )
        if manifest.mcpServers:
            payload = storage.get_mcp_config()
            servers = dict(payload.get("mcpServers") or {})
            try:
                from runtimes.extensions.mcp.client import mcp_manager

                runtime_status = dict(mcp_manager.get_status() or {})
            except Exception:
                runtime_status = {}
            for server in manifest.mcpServers:
                config = dict(servers.get(server.serverName) or {})
                enabled = bool(config) and not bool(config.get("disabled", True))
                connection_state = str((runtime_status.get(server.serverName) or {}).get("status") or "unknown")
                connected = connection_state == "connected"
                checks.append(
                    {
                        "componentId": server.id,
                        "kind": "mcp-config",
                        "ok": enabled and connected,
                        "summary": f"enabled={enabled}; connection={connection_state}",
                    }
                )
        if manifest.uiAdapters:
            checks.append({"componentId": "ui-adapters", "kind": "ui-adapter", "ok": True, "summary": "registered"})
        ok = all(item["ok"] for item in checks) if checks else True
        online = any(item["ok"] for item in checks) if checks else True
        result = {"ok": ok, "online": online, "pluginId": manifest.id, "checks": checks, "checkedAt": utc_now_iso()}
        if persist:
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE plugin_installations SET state=?, online=?, health_json=?, updated_at=? WHERE plugin_id=?",
                    ("installed" if ok else "degraded", 1 if online else 0, _json(result), utc_now_iso(), manifest.id),
                )
                if not ok:
                    conn.execute(
                        "UPDATE plugin_grants SET state='invalidated', revoked_at=?, terminal_reason='health_check_failed' WHERE plugin_id=? AND state='active'",
                        (utc_now_iso(), manifest.id),
                    )
                conn.commit()
            self._invalidate_catalog_installation_cache()
            if not ok:
                self._invalidate_grant_cache()
            self._event(manifest.id, "doctor", "ok" if ok else "degraded", details=result)
        return result

    def uninstall(self, plugin_id: str, *, force: bool = False, purge: bool = False) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        components = self._component_rows(manifest.id)
        drift = []
        mcp_payload = storage.get_mcp_config()
        mcp_servers = dict(mcp_payload.get("mcpServers") or {})
        for item in components:
            if item.get("component_type") == "mcp":
                metadata = _loads(item.get("metadata_json"), {})
                server_name = str(metadata.get("serverName") or "").strip()
                expected = str(metadata.get("configSha256") or "").strip()
                current = mcp_servers.get(server_name)
                actual = _hash_value(current) if current is not None else ""
                if expected and actual and actual != expected:
                    drift.append({"componentId": item["component_id"], "serverName": server_name, "expected": expected, "actual": actual})
                continue
            if item.get("ownership") != "managed" or not item.get("owned_path") or not item.get("content_sha256"):
                continue
            path = Path(str(item["owned_path"]))
            actual = _hash_path(path)
            if actual and actual != item["content_sha256"]:
                drift.append({"componentId": item["component_id"], "path": str(path), "expected": item["content_sha256"], "actual": actual})
        if drift and not force:
            raise PluginManagerError(
                "检测到用户修改的插件组件，卸载已停止。",
                code="component_hash_drift",
                status_code=409,
            )
        removed = []
        for item in reversed(components):
            if item.get("ownership") != "managed":
                continue
            path_text = str(item.get("owned_path") or "").strip()
            if not path_text:
                continue
            path = Path(path_text)
            if path.exists() and _safe_owned_path(path):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(path_text)
        oauth_refs = [
            str(((config or {}).get("x-v8-oauth") or {}).get("secretRef") or "").strip()
            for config in mcp_servers.values()
            if str((config or {}).get("x-v8-plugin-owner") or "") == manifest.id
            and isinstance((config or {}).get("x-v8-oauth"), dict)
        ]
        payload = mcp_payload
        servers = mcp_servers
        for name, config in list(servers.items()):
            if str((config or {}).get("x-v8-plugin-owner") or "") == manifest.id:
                servers.pop(name, None)
        payload["mcpServers"] = servers
        storage.save_mcp_config(payload)
        purged_credentials = 0
        if purge:
            bindings = self._credential_bindings(manifest.id)
            references = [str(binding.get("secret_ref") or "") for binding in bindings.values()]
            references.extend(oauth_refs)
            for reference in sorted({item for item in references if item}):
                try:
                    purged_credentials += 1 if self._credential_store.delete(reference) else 0
                except CredentialStoreError:
                    pass
            manager_config = storage.get_plugin_manager_config()
            plugin_values = dict(manager_config.get("pluginConfigValues") or {})
            plugin_values.pop(manifest.id, None)
            manager_config["pluginConfigValues"] = plugin_values
            storage.save_plugin_manager_config(manager_config)
        with db.get_connection() as conn:
            conn.execute("DELETE FROM plugin_installations WHERE plugin_id=?", (manifest.id,))
            conn.execute(
                "UPDATE plugin_grants SET state='invalidated', revoked_at=?, terminal_reason='plugin_uninstalled' WHERE plugin_id=? AND state='active'",
                (utc_now_iso(), manifest.id),
            )
            if purge:
                conn.execute("DELETE FROM plugin_credential_bindings WHERE plugin_id=?", (manifest.id,))
            conn.commit()
        self._invalidate_catalog_installation_cache()
        self._invalidate_ownership_cache()
        self._invalidate_grant_cache()
        self._event(manifest.id, "uninstalled", "ok", details={"removed": removed, "externalComponentsRetained": [item["component_id"] for item in components if item.get("ownership") == "external"]})
        self._refresh_extensions()
        return {
            "ok": True,
            "pluginId": manifest.id,
            "removed": removed,
            "drift": drift,
            "configurationPreserved": not purge,
            "purgedCredentials": purged_credentials,
        }

    def create_grant(
        self,
        *,
        plugin_id: str,
        scope: str,
        session_id: str,
        run_id: str | None,
        grantee_type: str = "supervisor",
        grantee_id: str = "supervisor",
        tool_call_id: str = "",
        component_ids: Iterable[str] | None = None,
        parent_grant_id: str | None = None,
        grant_source: str = "user_reference",
    ) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        normalized_source = str(grant_source or "user_reference").strip().lower()
        if normalized_source not in {"user_reference", "supervisor_task", "delegation", "admin"}:
            raise PluginManagerError("插件授权来源无效", code="grant_source_invalid")
        scope = str(scope or "task").strip().lower()
        if scope not in {"task", "session"}:
            raise PluginManagerError("授权范围必须是 task 或 session", code="grant_scope_invalid")
        if scope == "task" and not str(run_id or "").strip():
            raise PluginManagerError("任务授权必须绑定 runId", code="grant_run_required")
        if scope == "session" and not storage.get_plugin_manager_config().get("allowSessionGrant", True):
            raise PluginManagerError("当前策略不允许会话持续授权", code="session_grant_disabled")
        session = db.get_session(session_id)
        if not session:
            raise PluginManagerError("授权会话不存在", code="grant_session_not_found", status_code=404)
        owner_user_id = str(session.get("user_id") or "").strip()
        if not owner_user_id:
            raise PluginManagerError("授权会话缺少 owner", code="grant_owner_missing", status_code=409)
        readiness = self.readiness_status(manifest.id)
        if not readiness["ready"]:
            reason_labels = {
                "not_installed": "未安装",
                "needs_configuration": "未配置",
                "offline": "离线",
                "invalid": "无效",
            }
            raise PluginManagerError(
                f"插件暂不可授权：{reason_labels.get(str(readiness['status']), str(readiness['status']))}",
                code="plugin_not_ready",
                status_code=409,
            )
        declared = {
            *[item.id for item in manifest.cliProfiles],
            *[item.id for item in manifest.skills],
            *[item.id for item in manifest.mcpServers],
            *[item.id for item in manifest.uiAdapters],
            *[item.id for item in manifest.providerAdapters],
        }
        requested = [str(item).strip() for item in list(component_ids or []) if str(item).strip()]
        if not requested:
            raise PluginManagerError(
                "插件授权必须明确指定最小组件集合",
                code="grant_components_required",
                status_code=409,
            )
        selected = requested
        if not set(selected).issubset(declared):
            raise PluginManagerError("授权包含未声明组件", code="grant_component_invalid")
        if grantee_type == "subagent":
            if not parent_grant_id:
                raise PluginManagerError("子代理授权必须引用父授权", code="parent_grant_required")
            parent = self._grant_row(parent_grant_id)
            if not parent or parent.get("grantee_type") not in {"supervisor", "subagent"}:
                raise PluginManagerError("父授权不存在", code="parent_grant_invalid")
            if parent.get("grantee_type") == "subagent":
                raise PluginManagerError("插件授权不得向孙代理继续扩散", code="grant_transitive_denied", status_code=403)
            if parent.get("plugin_id") != manifest.id or parent.get("session_id") != session_id:
                raise PluginManagerError("子代理授权必须与父授权属于同一插件和会话", code="parent_grant_scope_invalid")
            if str(parent.get("owner_user_id") or "") != owner_user_id:
                raise PluginManagerError("子代理授权 owner 与父授权不一致", code="parent_grant_owner_invalid", status_code=403)
            if scope == "task" and parent.get("scope") == "task" and parent.get("run_id") != run_id:
                raise PluginManagerError("子代理任务授权必须与父授权属于同一 run", code="parent_grant_run_invalid")
            parent_components = set(_loads(parent.get("component_ids_json"), []))
            if not set(selected).issubset(parent_components):
                raise PluginManagerError("子代理授权不得扩大组件范围", code="grant_scope_escalation", status_code=403)
        selected = sorted(set(selected))
        with db.get_connection() as conn:
            existing = conn.execute(
                """
                SELECT * FROM plugin_grants
                WHERE plugin_id=? AND scope=? AND session_id=?
                  AND COALESCE(run_id, '')=COALESCE(?, '')
                  AND grantee_type=? AND grantee_id=?
                  AND component_ids_json=? AND revoked_at IS NULL
                  AND state='active' AND owner_user_id=?
                  AND COALESCE(parent_grant_id, '')=COALESCE(?, '')
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    manifest.id,
                    scope,
                    session_id,
                    run_id if scope == "task" else None,
                    grantee_type,
                    grantee_id,
                    _json(selected),
                    owner_user_id,
                    parent_grant_id,
                ),
            ).fetchone()
        if existing:
            return self._grant_payload(dict(existing))

        grant_id = f"plugin_grant_{uuid.uuid4().hex}"
        created_at = utc_now_iso()
        expires_at = None
        if scope == "task":
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO plugin_grants
                (id, plugin_id, scope, session_id, run_id, grantee_type, grantee_id,
                 component_ids_json, created_at, expires_at, parent_grant_id, owner_user_id,
                 manifest_version, manifest_digest, catalog_revision, state, grant_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    grant_id,
                    manifest.id,
                    scope,
                    session_id,
                    run_id if scope == "task" else None,
                    grantee_type,
                    grantee_id,
                    _json(selected),
                    created_at,
                    expires_at,
                    parent_grant_id,
                    owner_user_id,
                    manifest.version,
                    self._manifest_digest(manifest),
                    plugin_catalog_service.load().revision,
                    normalized_source,
                ),
            )
            conn.commit()
        self._invalidate_grant_cache()
        self._event(manifest.id, "grant_created", "ok", grant_id=grant_id, session_id=session_id, run_id=run_id, details={"scope": scope, "source": normalized_source, "granteeType": grantee_type, "granteeId": grantee_id, "componentIds": selected})
        return self._grant_payload(self._grant_row(grant_id))

    def _grant_row(self, grant_id: str) -> dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM plugin_grants WHERE id=?", (grant_id,)).fetchone()
        return dict(row) if row else None

    def _grant_payload(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {}
        return {
            "grantId": row["id"],
            "pluginId": row["plugin_id"],
            "scope": row["scope"],
            "sessionId": row["session_id"],
            "runId": row.get("run_id"),
            "granteeType": row["grantee_type"],
            "granteeId": row["grantee_id"],
            "ownerUserId": row.get("owner_user_id"),
            "manifestVersion": row.get("manifest_version"),
            "manifestDigest": row.get("manifest_digest"),
            "catalogRevision": row.get("catalog_revision"),
            "state": row.get("state") or ("revoked" if row.get("revoked_at") else "active"),
            "terminalReason": row.get("terminal_reason"),
            "componentIds": _loads(row.get("component_ids_json"), []),
            "createdAt": row["created_at"],
            "expiresAt": row.get("expires_at"),
            "revokedAt": row.get("revoked_at"),
            "parentGrantId": row.get("parent_grant_id"),
            "source": row.get("grant_source") or "user_reference",
        }

    def active_grants(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        grantee_type: str | None = None,
        grantee_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = (
            str(session_id or ""),
            str(run_id or ""),
            str(grantee_type or ""),
            str(grantee_id or ""),
        )
        cached = self._grant_cache.get(cache_key)
        if cached is not None:
            return [{**item, "componentIds": list(item.get("componentIds") or [])} for item in cached]
        now = utc_now_iso()
        query = """
            SELECT * FROM plugin_grants
            WHERE session_id=? AND revoked_at IS NULL AND state='active'
              AND (expires_at IS NULL OR expires_at > ?)
              AND (scope='session' OR (scope='task' AND run_id=?))
        """
        params: list[Any] = [session_id, now, run_id]
        if grantee_type:
            query += " AND grantee_type=?"
            params.append(grantee_type)
        if grantee_id:
            query += " AND grantee_id=?"
            params.append(grantee_id)
        query += " ORDER BY created_at"
        with db.get_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        result = [self._grant_payload(dict(row)) for row in rows]
        with self._cache_lock:
            self._grant_cache[cache_key] = result
        return [{**item, "componentIds": list(item.get("componentIds") or [])} for item in result]

    def list_active_grants(self, *, limit: int = 200) -> list[dict[str, Any]]:
        now = utc_now_iso()
        safe_limit = max(1, min(int(limit or 200), 500))
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM plugin_grants
                WHERE revoked_at IS NULL AND state='active' AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC LIMIT ?
                """,
                (now, safe_limit),
            ).fetchall()
        return [self._grant_payload(dict(row)) for row in rows]

    def revoke_grant(self, grant_id: str) -> dict[str, Any]:
        row = self._grant_row(grant_id)
        if not row:
            raise PluginManagerError("授权不存在", code="grant_not_found", status_code=404)
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute("UPDATE plugin_grants SET revoked_at=?, state='revoked', terminal_reason='explicit_revoke' WHERE id=?", (now, grant_id))
            conn.execute("UPDATE plugin_grants SET revoked_at=?, state='revoked', terminal_reason='parent_revoked' WHERE parent_grant_id=? AND revoked_at IS NULL", (now, grant_id))
            conn.commit()
        self._invalidate_grant_cache()
        self._event(row["plugin_id"], "grant_revoked", "ok", grant_id=grant_id, session_id=row["session_id"], run_id=row.get("run_id"))
        return {"ok": True, "grantId": grant_id, "revokedAt": now}

    def revoke_session_grants(self, session_id: str, *, reason: str = "session_deleted") -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        if not normalized:
            return {"ok": True, "sessionId": "", "revoked": 0}
        now = utc_now_iso()
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, plugin_id, run_id FROM plugin_grants WHERE session_id=? AND state='active' AND revoked_at IS NULL",
                (normalized,),
            ).fetchall()
            conn.execute(
                "UPDATE plugin_grants SET revoked_at=?, state='revoked', terminal_reason=? WHERE session_id=? AND state='active' AND revoked_at IS NULL",
                (now, reason, normalized),
            )
            conn.commit()
        self._invalidate_grant_cache()
        for row in rows:
            self._event(
                str(row["plugin_id"]),
                "grant_revoked",
                "ok",
                grant_id=str(row["id"]),
                session_id=normalized,
                run_id=row["run_id"],
                details={"reason": reason},
            )
        return {"ok": True, "sessionId": normalized, "revoked": len(rows), "revokedAt": now}

    def validate_grant_for_invocation(
        self,
        *,
        grant_id: str,
        plugin_id: str,
        component_id: str,
        session_id: str,
        run_id: str | None,
        grantee_type: str,
        grantee_id: str,
        manifest_digest: str | None = None,
    ) -> dict[str, Any]:
        row = self._grant_row(grant_id)
        if not row:
            raise PluginManagerError("插件授权不存在", code="plugin_grant_not_found", status_code=403)
        now = utc_now_iso()
        if row.get("state") != "active" or row.get("revoked_at"):
            raise PluginManagerError("插件授权已失效", code="plugin_grant_inactive", status_code=403)
        if row.get("expires_at") and str(row["expires_at"]) <= now:
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE plugin_grants SET state='expired', terminal_reason='ttl_expired' WHERE id=?",
                    (grant_id,),
                )
                conn.commit()
            self._invalidate_grant_cache()
            raise PluginManagerError("插件授权已过期", code="plugin_grant_expired", status_code=403)
        session = db.get_session(session_id)
        owner_user_id = str((session or {}).get("user_id") or "")
        if not session or owner_user_id != str(row.get("owner_user_id") or ""):
            raise PluginManagerError("插件授权 owner 不匹配", code="plugin_grant_owner_mismatch", status_code=403)
        if (
            str(row.get("plugin_id") or "") != str(plugin_id or "")
            or str(row.get("session_id") or "") != str(session_id or "")
            or str(row.get("grantee_type") or "") != str(grantee_type or "")
            or str(row.get("grantee_id") or "") != str(grantee_id or "")
        ):
            raise PluginManagerError("插件授权上下文不匹配", code="plugin_grant_context_mismatch", status_code=403)
        if row.get("scope") == "task" and str(row.get("run_id") or "") != str(run_id or ""):
            raise PluginManagerError("插件任务授权 run 不匹配", code="plugin_grant_run_mismatch", status_code=403)
        if component_id not in set(_loads(row.get("component_ids_json"), [])):
            raise PluginManagerError("插件组件未获授权", code="plugin_grant_component_denied", status_code=403)
        manifest = self._manifest(plugin_id)
        current_digest = self._manifest_digest(manifest)
        if manifest_digest and str(manifest_digest) != str(row.get("manifest_digest") or ""):
            raise PluginManagerError("工具投影与授权清单不匹配", code="plugin_grant_projection_stale", status_code=403)
        if str(row.get("manifest_digest") or "") != current_digest:
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE plugin_grants SET state='invalidated', terminal_reason='manifest_changed' WHERE id=?",
                    (grant_id,),
                )
                conn.commit()
            self._invalidate_grant_cache()
            raise PluginManagerError("插件清单已变化，授权必须重新确认", code="plugin_grant_manifest_changed", status_code=403)
        installation = self._installation_rows().get(manifest.id)
        install_state = self._installation_payload(installation)
        if not install_state["installed"] or not install_state["configured"] or not install_state["online"]:
            raise PluginManagerError("插件当前不可用", code="plugin_grant_plugin_unavailable", status_code=409)
        return self._grant_payload(row)

    def expire_task_grants(self, *, run_id: str, reason: str = "run_terminal") -> dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return {"ok": True, "runId": "", "expired": 0}
        now = utc_now_iso()
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, plugin_id, session_id FROM plugin_grants WHERE scope='task' AND run_id=? AND revoked_at IS NULL",
                (normalized_run_id,),
            ).fetchall()
            conn.execute(
                "UPDATE plugin_grants SET revoked_at=?, state='completed', terminal_reason=? WHERE scope='task' AND run_id=? AND revoked_at IS NULL",
                (now, reason, normalized_run_id),
            )
            conn.commit()
        self._invalidate_grant_cache()
        for row in rows:
            self._event(
                str(row["plugin_id"]),
                "grant_expired",
                "ok",
                grant_id=str(row["id"]),
                session_id=str(row["session_id"]),
                run_id=normalized_run_id,
                details={"reason": reason},
            )
        return {"ok": True, "runId": normalized_run_id, "expired": len(rows), "revokedAt": now}

    def delegate_grants_to_subagent(
        self,
        *,
        plugin_references: Iterable[dict[str, Any]] | None = None,
        session_id: str,
        run_id: str,
        subagent_id: str,
    ) -> list[dict[str, Any]]:
        requested: dict[str, set[str]] = {}
        for reference in list(plugin_references or []):
            if not isinstance(reference, dict):
                continue
            plugin_id = str(reference.get("pluginId") or "").strip().lower()
            component_ids = {
                str(item).strip()
                for item in list(reference.get("componentIds") or [])
                if str(item).strip()
            }
            if not plugin_id or not component_ids:
                raise PluginManagerError(
                    "子代理插件授权必须包含 pluginId 和非空 componentIds。",
                    code="delegation_components_required",
                    status_code=403,
                )
            requested.setdefault(plugin_id, set()).update(component_ids)
        if not requested:
            return []
        parent_grants = self.active_grants(
            session_id=session_id,
            run_id=run_id,
            grantee_type="supervisor",
            grantee_id="supervisor",
        )
        parent_by_plugin = {str(item.get("pluginId") or "").strip().lower(): item for item in parent_grants}
        missing = sorted(set(requested) - set(parent_by_plugin))
        if missing:
            raise PluginManagerError(
                f"子代理请求了当前 Supervisor 尚未授权的插件：{', '.join(missing)}",
                code="subagent_plugin_grant_missing",
                status_code=403,
            )
        delegated: list[dict[str, Any]] = []
        for plugin_id in sorted(requested):
            selected = sorted(requested[plugin_id])
            parent_components = set(parent_by_plugin[plugin_id].get("componentIds") or [])
            if not set(selected).issubset(parent_components):
                raise PluginManagerError(
                    f"子代理请求扩大插件 {plugin_id} 的组件范围。",
                    code="grant_scope_escalation",
                    status_code=403,
                )
            delegated.append(self.create_grant(
                plugin_id=plugin_id,
                scope="task",
                session_id=session_id,
                run_id=run_id,
                grantee_type="subagent",
                grantee_id=subagent_id,
                component_ids=selected,
                parent_grant_id=str(parent_by_plugin[plugin_id].get("grantId") or ""),
                grant_source="delegation",
            ))
        return delegated

    def resolve_creative_media_adapter(
        self,
        *,
        adapter_id: str,
        session_id: str | None,
        run_id: str | None,
        grantee_type: str = "supervisor",
        grantee_id: str = "supervisor",
    ) -> dict[str, Any] | None:
        """Resolve a signed, grant-backed adapter to a code-owned handler.

        The returned credential values stay inside Engine process memory and are
        never included in Agent/API projections. Plugin metadata cannot inject
        executable code: ``handlerId`` must be present in the compiled allowlist.
        """

        normalized_adapter = str(adapter_id or "").strip()
        normalized_session = str(session_id or "").strip()
        if not normalized_adapter or not normalized_session:
            return None
        grants = self.active_grants(
            session_id=normalized_session,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
        for grant in grants:
            manifest = self._manifest(str(grant.get("pluginId") or ""))
            adapter = next(
                (item for item in manifest.providerAdapters if item.id == normalized_adapter),
                None,
            )
            if adapter is None or adapter.id not in set(grant.get("componentIds") or []):
                continue
            if adapter.handlerId not in CODE_OWNED_PROVIDER_ADAPTERS:
                return None
            self.validate_grant_for_invocation(
                grant_id=str(grant.get("grantId") or ""),
                plugin_id=manifest.id,
                component_id=adapter.id,
                session_id=normalized_session,
                run_id=run_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                manifest_digest=str(grant.get("manifestDigest") or "") or None,
            )
            bindings = self._credential_bindings(manifest.id)
            credentials: dict[str, str] = {}
            for requirement in adapter.configRequirements:
                if requirement.kind != "secret":
                    continue
                binding = bindings.get(requirement.id)
                secret_ref = str((binding or {}).get("secret_ref") or "").strip()
                if not secret_ref:
                    if requirement.required:
                        return None
                    continue
                try:
                    credentials[str(requirement.targetName or requirement.id)] = self._credential_store.resolve(secret_ref)
                except CredentialStoreError:
                    return None
            return {
                "pluginId": manifest.id,
                "componentId": adapter.id,
                "handlerId": adapter.handlerId,
                "grantId": grant.get("grantId"),
                "manifestDigest": grant.get("manifestDigest"),
                "credentials": credentials,
            }
        return None

    def projection_for(
        self,
        *,
        session_id: str,
        run_id: str | None,
        grantee_type: str = "supervisor",
        grantee_id: str = "supervisor",
    ) -> dict[str, Any]:
        grants = self.active_grants(
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
        skills: list[dict[str, Any]] = []
        mcp_tools: list[Any] = []
        projected_mcp_names: set[str] = set()
        cli_entries: list[dict[str, Any]] = []
        adapters: list[dict[str, Any]] = []
        provider_adapters: list[dict[str, Any]] = []
        for grant in grants:
            manifest = self._manifest(grant["pluginId"])
            components = set(grant["componentIds"])
            for skill in manifest.skills:
                if skill.id in components:
                    skills.append({"pluginId": manifest.id, "grantId": grant["grantId"], **skill.model_dump(mode="json")})
            for profile in manifest.cliProfiles:
                if profile.id in components:
                    cli_entries.append({"pluginId": manifest.id, "pluginName": manifest.displayName, "grantId": grant["grantId"], **profile.model_dump(mode="json")})
            for adapter in manifest.uiAdapters:
                if adapter.id in components:
                    adapters.append({"pluginId": manifest.id, "grantId": grant["grantId"], **adapter.model_dump(mode="json")})
            for adapter in manifest.providerAdapters:
                if adapter.id in components:
                    provider_adapters.append({"pluginId": manifest.id, "grantId": grant["grantId"], **adapter.model_dump(mode="json")})
            selected_servers = {server.serverName: server for server in manifest.mcpServers if server.id in components}
            if selected_servers:
                try:
                    from runtimes.extensions.mcp.client import mcp_manager
                    from runtimes.plugin_manager.guarded_tools import build_guarded_mcp_tool

                    for tool in mcp_manager.get_tools():
                        metadata = dict(getattr(tool, "metadata", None) or {})
                        server_name = str(metadata.get("server_name") or "")
                        server = selected_servers.get(server_name)
                        if not server:
                            continue
                        allowed = set(server.allowedTools)
                        if str(getattr(tool, "name", "")) in allowed:
                            guarded = build_guarded_mcp_tool(
                                tool,
                                plugin_id=manifest.id,
                                component_id=server.id,
                                grant=grant,
                            )
                            guarded_name = str(getattr(guarded, "name", "") or "")
                            if guarded_name and guarded_name not in projected_mcp_names:
                                projected_mcp_names.add(guarded_name)
                                mcp_tools.append(guarded)
                except Exception:
                    pass
        return {
            "grants": grants,
            "skills": skills,
            "mcpTools": mcp_tools,
            "cliProfiles": cli_entries,
            "uiAdapters": adapters,
            "providerAdapters": provider_adapters,
        }

    async def execute_cli(
        self,
        *,
        plugin_id: str,
        profile_id: str,
        action_id: str,
        parameters: dict[str, Any] | None,
        session_id: str,
        run_id: str | None,
        grantee_type: str = "supervisor",
        grantee_id: str = "supervisor",
        tool_call_id: str = "",
    ) -> dict[str, Any]:
        projection = self.projection_for(
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
        profile_payload = next(
            (
                item
                for item in projection["cliProfiles"]
                if item["pluginId"] == plugin_id and item["id"] == profile_id
            ),
            None,
        )
        if not profile_payload:
            raise PluginManagerError("当前任务未授权该 CLI", code="plugin_cli_not_granted", status_code=403)
        manifest = self._manifest(plugin_id)
        profile = next(item for item in manifest.cliProfiles if item.id == profile_id)
        self.validate_grant_for_invocation(
            grant_id=str(profile_payload.get("grantId") or ""),
            plugin_id=manifest.id,
            component_id=profile.id,
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )
        normalized_action = str(action_id or "").strip()
        if not normalized_action:
            raise PluginManagerError("CLI actionId 不能为空", code="plugin_cli_action_required")
        built_in_spec = {
            "help": CommandSpec(argv=[profile.commands[0], "--help"]),
            "version": profile.version,
            "login": profile.login,
            "start": profile.start,
            "stop": profile.stop,
        }.get(normalized_action)
        supplied = dict(parameters or {})
        action_spec = built_in_spec
        if built_in_spec is not None and supplied:
            raise PluginManagerError("内置 CLI 动作不接受额外参数", code="plugin_cli_parameters_denied", status_code=403)
        if action_spec is None:
            action = next((item for item in profile.actions if item.id == normalized_action), None)
            if action is None:
                raise PluginManagerError(f"CLI 不支持动作：{normalized_action}", code="plugin_cli_action_unsupported", status_code=403)
            action_spec = self._build_cli_action_spec(manifest, profile, action, supplied)
        if action_spec is None:
            raise PluginManagerError(f"CLI 不支持动作：{normalized_action}", code="plugin_cli_action_unsupported")
        command_preview = subprocess.list2cmdline(self._expand_argv(manifest, action_spec))
        from core.tools.native.tool_governance import _enforce_safety_decision
        from erc.runtime_context import get_runtime_context
        from erc.safety_guardian import safety_guardian

        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_system_command(
                command_preview,
                runtime_context={
                    **dict(get_runtime_context() or {}),
                    "runtime_kind": "plugin_manager",
                    "plugin_id": plugin_id,
                    "plugin_profile_id": profile_id,
                    "plugin_action": normalized_action,
                },
            ),
            tool_call_id=tool_call_id,
            question=f"插件 {manifest.displayName} 将执行受治理 CLI 操作，是否继续？\n\n命令：{command_preview}",
        )
        if not allowed:
            raise PluginManagerError(error_message or "Safety Guardian 已阻止该 CLI 操作", code="plugin_cli_safety_blocked", status_code=403)
        result = await asyncio.to_thread(
            self._execute_spec,
            manifest,
            action_spec,
            env_overlay=self._cli_credential_env(manifest, profile),
        )
        safe_result = self._redact_known_credentials(plugin_id, result)
        event_id = self._event(plugin_id, "cli_executed", "ok" if result["returnCode"] == 0 else "error", session_id=session_id, run_id=run_id, details={"profileId": profile_id, "action": normalized_action, "result": safe_result})
        succeeded = result["returnCode"] == 0
        return {
            "pluginId": plugin_id,
            "profileId": profile_id,
            "action": normalized_action,
            "returnCode": result["returnCode"],
            "status": "completed" if succeeded else "failed",
            "summary": (
                f"{manifest.displayName} CLI action '{normalized_action}' completed."
                if succeeded
                else f"{manifest.displayName} CLI action '{normalized_action}' failed with exit code {result['returnCode']}."
            ),
            "detailRef": f"plugin-event://{event_id}",
        }

    def _build_cli_action_spec(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        action: CliAction,
        parameters: dict[str, Any],
    ) -> CommandSpec:
        definitions = {item.name: item for item in action.parameters}
        unknown = sorted(set(parameters) - set(definitions))
        if unknown:
            raise PluginManagerError(
                f"CLI 动作包含未声明参数：{', '.join(unknown)}",
                code="plugin_cli_parameter_unknown",
                status_code=403,
            )
        missing = [item.name for item in action.parameters if item.required and parameters.get(item.name) in (None, "")]
        if missing:
            raise PluginManagerError(
                f"CLI 动作缺少参数：{', '.join(missing)}",
                code="plugin_cli_parameter_missing",
            )
        executable = str(self._bin_root() / f"{profile.commands[0]}.cmd") if profile.ownership == "managed" else profile.commands[0]
        template = list(action.argv or [executable, action.id])
        if template and template[0] in profile.commands:
            template[0] = executable
        argv: list[str] = []
        consumed: set[str] = set()
        for token in template:
            rendered = str(token)
            for name, definition in definitions.items():
                marker = f"{{{name}}}"
                if marker not in rendered:
                    continue
                value = parameters.get(name)
                if value in (None, ""):
                    raise PluginManagerError(f"CLI 参数 {name} 不能为空", code="plugin_cli_parameter_missing")
                rendered = rendered.replace(marker, str(value))
                consumed.add(name)
            argv.append(rendered)
        for name, definition in definitions.items():
            if name in consumed or name not in parameters:
                continue
            value = parameters[name]
            if definition.kind == "enum" and str(value) not in set(definition.options):
                raise PluginManagerError(f"CLI 参数 {name} 不在允许值中", code="plugin_cli_parameter_invalid")
            if definition.kind == "boolean":
                if not isinstance(value, bool):
                    raise PluginManagerError(f"CLI 参数 {name} 必须是布尔值", code="plugin_cli_parameter_invalid")
                if value and definition.flag:
                    argv.append(definition.flag)
                continue
            if definition.flag:
                argv.extend([definition.flag, str(value)])
            elif definition.positional:
                argv.append(str(value))
            else:
                raise PluginManagerError(f"CLI 参数 {name} 缺少安全投影规则", code="plugin_cli_parameter_contract_invalid")
        return CommandSpec(argv=argv, timeoutSeconds=action.timeoutSeconds)

    def _cli_credential_env(self, manifest: PluginManifest, profile: CliProfile) -> dict[str, str]:
        requirements = [item for item in compile_plugin_requirements(manifest) if item.componentId == profile.id]
        bindings = self._credential_bindings(manifest.id)
        result: dict[str, str] = {}
        for requirement in requirements:
            if requirement.target != "env" or not requirement.targetName:
                continue
            binding = bindings.get(requirement.id)
            if not binding:
                if requirement.required and requirement.confidence != "hint":
                    raise PluginManagerError("CLI 所需凭据尚未配置", code="plugin_cli_configuration_missing", status_code=409)
                continue
            try:
                result[requirement.targetName] = self._credential_store.resolve(str(binding["secret_ref"]))
            except CredentialStoreError as exc:
                raise PluginManagerError("CLI 凭据引用不可用", code="plugin_cli_credential_unavailable", status_code=409) from exc
        return result

    def plugin_owned_skill_roots(self) -> set[str]:
        roots, _ = self.plugin_owned_components()
        return set(roots)

    def plugin_owned_mcp_servers(self) -> set[str]:
        _, servers = self.plugin_owned_components()
        return set(servers)

    def plugin_owned_components(self) -> tuple[frozenset[str], frozenset[str]]:
        return self._plugin_ownership_snapshot()

    def _plugin_ownership_snapshot(self) -> tuple[frozenset[str], frozenset[str]]:
        cached = self._ownership_cache
        if cached is not None:
            return cached
        roots: set[str] = set()
        servers: set[str] = set()
        for item in self._component_rows():
            if item.get("component_type") == "skill" and item.get("owned_path"):
                roots.add(str(Path(str(item["owned_path"])).resolve()))
            elif item.get("component_type") == "mcp":
                name = str(_loads(item.get("metadata_json"), {}).get("serverName") or "").strip()
                if name:
                    servers.add(name)
        with self._cache_lock:
            self._ownership_cache = (frozenset(roots), frozenset(servers))
        return self._ownership_cache

    def _event(
        self,
        plugin_id: str | None,
        event_type: str,
        status: str,
        *,
        job_id: str | None = None,
        grant_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        event_id = f"plugin_event_{uuid.uuid4().hex}"
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO plugin_events
                (id, plugin_id, job_id, grant_id, event_type, status, actor_type, actor_id,
                 session_id, run_id, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'runtime', 'plugin_manager', ?, ?, ?, ?)
                """,
                (
                    event_id,
                    plugin_id,
                    job_id,
                    grant_id,
                    event_type,
                    status,
                    session_id,
                    run_id,
                    _json(self._redact_known_credentials(str(plugin_id or ""), details or {}) if plugin_id else _redact(details or {})),
                    utc_now_iso(),
                ),
            )
            conn.commit()
        return event_id

    def list_events(self, *, plugin_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        with db.get_connection() as conn:
            if plugin_id:
                rows = conn.execute(
                    "SELECT * FROM plugin_events WHERE plugin_id=? ORDER BY created_at DESC LIMIT ?",
                    (plugin_id, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM plugin_events ORDER BY created_at DESC LIMIT ?", (safe_limit,)).fetchall()
        return {"items": [{**dict(row), "details": _loads(row["details_json"], {})} for row in rows]}

    def _refresh_extensions(self) -> None:
        try:
            from runtimes.extensions.runtime import extensions_runtime_service

            extensions_runtime_service.schedule_skill_refresh()
        except Exception:
            pass
        try:
            from runtimes.extensions.mcp.client import mcp_manager

            loop = asyncio.get_running_loop()
            loop.create_task(mcp_manager.reload_if_changed())
        except Exception:
            pass


plugin_manager_service = PluginManagerService()
