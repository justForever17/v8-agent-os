from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core.database import db
from core.process_launch import run_windowless
from core.security.credentials import CredentialRefStore, CredentialStoreError, credential_ref_store
from core.storage import storage
from core.v8_agent_os_paths import (
    PLUGIN_MANAGER_BIN_ROOT,
    PLUGIN_MANAGER_LOG_ROOT,
    PLUGIN_MANAGER_ROOT,
)

from .catalog import RESOURCE_ROOT, plugin_catalog_service
from .cli_capability_sync import (
    CliCapabilitySyncError,
    actions_from_snapshot,
    merge_discovered_actions,
    read_snapshot,
    resolve_reviewed_help_capability,
    sync_gda_capabilities,
    sync_mediakit_capabilities,
    sync_reviewed_help_capabilities,
)
from .godot_setup import evaluate_godot_setup, stable_godot_setup_projection
from .cli_auth import CliBrowserAuthAdapter, browser_auth_adapter, open_system_browser
from .requirements import (
    compile_plugin_requirements,
    discover_requirement_sources,
    read_explicit_import_source,
)
from .schema import CliAction, CliProfile, CommandSpec, PluginConfigRequirement, PluginManifest


AGENT_SKILLS_ROOT = Path.home() / ".agents" / "skills"
SKILLS_CLI_PACKAGE = "skills@1.5.19"
SKILLS_CLI_CACHE_SECONDS = 30.0
SKILLS_CLI_NPM_REGISTRIES = (
    "https://registry.npmmirror.com",
    "https://registry.npmjs.org",
)
MANAGED_GITHUB_RELEASE_MIRROR_PREFIXES = ("https://ghproxy.net/",)
SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization|credential)", re.I)
SAFE_COMPONENT_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VERSION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)")
PINNED_PACKAGE_VERSION_RE = re.compile(r"@(?P<version>\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)$")
MANAGED_CMD_PROGRAM_RE = re.compile(r'^\s*SET\s+"_prog=(?P<program>[^"]+)"\s*$', re.I | re.M)
MANAGED_CMD_TARGET_RE = re.compile(r'"%dp0%\\(?P<target>\.\.\\[^"]+)"\s+%\*\s*$', re.I | re.M)
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


def _architecture_name() -> str:
    machine = platform.machine().strip().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return "amd64"


def _background_process_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


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
        self._grant_cache: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        self._catalog_projection_cache: tuple[
            tuple[int, int],
            dict[str, Any],
            tuple[dict[str, Any], ...],
        ] | None = None
        self._catalog_installation_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._skills_inventory_cache: tuple[float, dict[str, Any]] | None = None
        self._machine_discovery_cache: dict[str, dict[str, Any]] = {}
        self._cli_auth_states: dict[tuple[str, str], dict[str, Any]] = {}
        self._cli_capability_profile_cache: dict[tuple[str, str, int, int], CliProfile] = {}
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
                "delegation_id": "TEXT",
                "delegation_depth": "INTEGER",
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plugin_setup_state (
                    plugin_id TEXT PRIMARY KEY,
                    adapter TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
            conn.execute(
                """
                UPDATE plugin_grants
                SET state='invalidated', terminal_reason='delegation_identity_requires_regrant'
                WHERE state='active' AND grantee_type='subagent'
                  AND (delegation_id IS NULL OR delegation_id='')
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

    def _invalidate_skills_inventory_cache(self) -> None:
        with self._cache_lock:
            self._skills_inventory_cache = None
            self._machine_discovery_cache.clear()

    def _invalidate_machine_discovery_cache(self, plugin_id: str | None = None) -> None:
        with self._cache_lock:
            self._catalog_projection_cache = None
            if plugin_id:
                self._machine_discovery_cache.pop(str(plugin_id).strip().lower(), None)
            else:
                self._machine_discovery_cache.clear()

    @staticmethod
    def _run_skills_cli(
        arguments: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        executable = shutil.which("npx") or shutil.which("npx.cmd")
        if not executable:
            return {
                "returnCode": 127,
                "stdoutTail": "",
                "stderrTail": "npx is not installed",
            }
        deadline = time.monotonic() + max(1, int(timeout_seconds or 0))
        attempts: list[dict[str, Any]] = []
        for index, registry in enumerate(SKILLS_CLI_NPM_REGISTRIES):
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            remaining_registries = len(SKILLS_CLI_NPM_REGISTRIES) - index
            environment = dict(os.environ)
            environment.update(
                {
                    "CI": "1",
                    "NO_COLOR": "1",
                    "FORCE_COLOR": "0",
                    "npm_config_registry": registry,
                }
            )
            try:
                completed = run_windowless(
                    [executable, "--yes", SKILLS_CLI_PACKAGE, *arguments],
                    cwd=str(cwd) if cwd else None,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(1.0, remaining / remaining_registries),
                    check=False,
                )
                result = {
                    "returnCode": int(completed.returncode),
                    "stdoutTail": str(completed.stdout or "")[-1000000:],
                    "stderrTail": str(completed.stderr or "")[-8000:],
                    "registry": registry,
                }
                attempts.append(
                    {"registry": registry, "returnCode": result["returnCode"]}
                )
                if result["returnCode"] == 0:
                    return {**result, "attempts": attempts}
            except subprocess.TimeoutExpired:
                attempts.append({"registry": registry, "returnCode": 124})
            except OSError as exc:
                attempts.append(
                    {
                        "registry": registry,
                        "returnCode": 127,
                        "error": str(exc)[:240],
                    }
                )
        last = attempts[-1] if attempts else {"returnCode": 124}
        return {
            "returnCode": int(last.get("returnCode") or 1),
            "stdoutTail": "",
            "stderrTail": (
                "skills CLI timed out across configured npm registries"
                if int(last.get("returnCode") or 0) == 124
                else str(last.get("error") or "skills CLI failed across configured npm registries")
            ),
            "attempts": attempts,
        }

    @staticmethod
    def _repository_identity(value: str) -> str:
        normalized = str(value or "").strip().replace("\\", "/").rstrip("/")
        if normalized.endswith(".git"):
            normalized = normalized[:-4]
        marker = "github.com/"
        if marker in normalized.lower():
            normalized = normalized[normalized.lower().index(marker) + len(marker):]
        return normalized.lower().strip("/")

    def _skills_cli_inventory(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._skills_inventory_cache
            if not force and cached is not None and now - cached[0] <= SKILLS_CLI_CACHE_SECONDS:
                return cached[1]

        version_result = self._run_skills_cli(["--version"], timeout_seconds=15)
        version_output = "\n".join(
            item
            for item in (
                str(version_result.get("stdoutTail") or ""),
                str(version_result.get("stderrTail") or ""),
            )
            if item
        )
        result = self._run_skills_cli(["list", "--global", "--json"], timeout_seconds=30)
        items: list[dict[str, Any]] = []
        error = ""
        if result["returnCode"] == 0:
            stdout = str(result.get("stdoutTail") or "").strip()
            try:
                payload = json.loads(stdout[stdout.index("["):])
                if isinstance(payload, list):
                    items = [dict(item) for item in payload if isinstance(item, dict)]
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                error = f"skills CLI returned invalid inventory: {exc}"
        else:
            error = str(result.get("stderrTail") or result.get("stdoutTail") or "skills CLI failed").strip()

        lock_entries: dict[str, dict[str, Any]] = {}
        lock_path = AGENT_SKILLS_ROOT.parent / ".skill-lock.json"
        try:
            lock_payload = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {}
            lock_entries = {
                str(name): dict(item)
                for name, item in dict(lock_payload.get("skills") or {}).items()
                if isinstance(item, dict)
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            lock_entries = {}

        inventory = {
            "ok": not error,
            "tool": SKILLS_CLI_PACKAGE,
            "toolVersion": self._normalized_version(version_output),
            "toolProbeOk": version_result["returnCode"] == 0,
            "items": items,
            "lockEntries": lock_entries,
            "error": error,
        }
        with self._cache_lock:
            self._skills_inventory_cache = (time.monotonic(), inventory)
        return inventory

    def _skill_source_matches(self, lock_entry: dict[str, Any], skill: Any) -> bool:
        source_kind = skill.get("sourceKind") if isinstance(skill, dict) else skill.sourceKind
        repository = skill.get("repository") if isinstance(skill, dict) else skill.repository
        source_path = skill.get("path") if isinstance(skill, dict) else skill.path
        if not lock_entry or str(source_kind or "git") != "git":
            return False
        expected_repository = self._repository_identity(str(repository or ""))
        actual_repository = self._repository_identity(
            str(lock_entry.get("sourceUrl") or lock_entry.get("source") or "")
        )
        if not expected_repository or actual_repository != expected_repository:
            return False
        installed_path = str(lock_entry.get("skillPath") or "").replace("\\", "/").strip("/")
        expected_path = str(source_path or "").replace("\\", "/").strip("/")
        return installed_path == f"{expected_path}/SKILL.md" or installed_path.startswith(f"{expected_path}/")

    @staticmethod
    def _cli_search_path() -> str:
        paths = [str(os.environ.get("PATH") or "")]
        if os.name == "nt":
            try:
                import winreg

                for hive, key_path in (
                    (winreg.HKEY_CURRENT_USER, "Environment"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                ):
                    try:
                        with winreg.OpenKey(hive, key_path) as key:
                            value, _ = winreg.QueryValueEx(key, "Path")
                            paths.append(str(value or ""))
                    except OSError:
                        continue
            except (ImportError, OSError):
                pass
        entries: list[str] = []
        seen: set[str] = set()
        for value in paths:
            for item in str(value or "").split(os.pathsep):
                expanded = os.path.expandvars(item.strip().strip('"'))
                if not expanded:
                    continue
                key = os.path.normcase(os.path.normpath(expanded))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(expanded)
        return os.pathsep.join(entries)

    def _refresh_process_cli_path(self) -> str:
        """Make CLIs installed after Engine launch visible to governed commands."""

        search_path = self._cli_search_path()
        if search_path:
            os.environ["PATH"] = search_path
        return search_path

    def _discover_cli_commands(self, profile: CliProfile) -> dict[str, str]:
        search_path = self._cli_search_path()
        return {
            command: str(path)
            for command in profile.commands
            if (path := shutil.which(command, path=search_path))
        }

    @staticmethod
    def _normalized_version(value: Any) -> str:
        text = ANSI_ESCAPE_RE.sub("", str(value or "")).strip()
        if not text:
            return ""
        if re.fullmatch(r"[0-9a-fA-F]{40}", text):
            return text.lower()
        match = VERSION_TOKEN_RE.search(text)
        return str(match.group(1) if match else "").strip()

    @staticmethod
    def _catalog_cli_version(profile: CliProfile) -> str:
        if profile.ownership != "managed":
            return ""
        for token in reversed(profile.install.argv):
            match = PINNED_PACKAGE_VERSION_RE.search(str(token or ""))
            if match:
                return str(match.group("version"))
        for value in (profile.install.downloadUrl, *profile.install.argv):
            text = str(value or "")
            for pattern in (
                r"/download/v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)/",
                r"[-_]v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)\.(?:zip|exe|phar|tar|gz)$",
            ):
                match = re.search(pattern, text, re.I)
                if match:
                    return str(match.group(1))
        return ""

    def _probe_cli_version(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        *,
        detected_commands: dict[str, str] | None = None,
        plugin_root: Path | None = None,
    ) -> dict[str, Any]:
        version_spec = profile.version
        detected_executable = str((detected_commands or {}).get(profile.commands[0]) or "").strip()
        if detected_executable:
            version_argv = list(version_spec.argv)
            if version_argv:
                version_argv[0] = detected_executable
            version_spec = version_spec.model_copy(update={"argv": version_argv})
        result = self._execute_spec(manifest, version_spec, plugin_root=plugin_root)
        output = "\n".join(
            item for item in (str(result.get("stdoutTail") or ""), str(result.get("stderrTail") or "")) if item
        )
        return {
            "ok": int(result.get("returnCode") or 0) == 0,
            "version": self._normalized_version(output),
            "returnCode": int(result.get("returnCode") or 0),
            "durationMs": int(result.get("durationMs") or 0),
        }

    @staticmethod
    def _version_state(installed: str, catalog: str, *, update_supported: bool) -> str:
        if not installed:
            return "unknown"
        if not catalog:
            return "unsupported"
        if installed.lower() == catalog.lower():
            return "current"
        return "available" if update_supported else "review_required"

    @staticmethod
    def _cached_extension_skill_metadata() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        try:
            from runtimes.extensions.runtime import extensions_runtime_service

            items = extensions_runtime_service.list_skills(
                force_refresh=False,
                prefer_cached_ready_inventory=True,
                include_scoped=False,
            )
        except Exception:
            items = []
        by_name: dict[str, dict[str, Any]] = {}
        by_root: dict[str, dict[str, Any]] = {}
        for raw in items:
            item = dict(raw or {})
            name = str(item.get("skillName") or item.get("name") or "").strip()
            root = str(item.get("skillRoot") or item.get("path") or "").strip()
            if name:
                by_name[name] = item
            if root:
                by_root[str(Path(root).expanduser().resolve()).lower()] = item
        return by_name, by_root

    @staticmethod
    def _cached_mcp_status() -> dict[str, dict[str, Any]]:
        try:
            from runtimes.extensions.runtime import extensions_runtime_service

            return {
                str(name): dict(payload or {})
                for name, payload in extensions_runtime_service.get_mcp_status().items()
            }
        except Exception:
            return {}

    def discover_machine_components(self, plugin_id: str, *, force: bool = False) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        cache_key = manifest.id.lower()
        if not force:
            with self._cache_lock:
                cached = self._machine_discovery_cache.get(cache_key)
            if cached is not None:
                return copy.deepcopy(cached)
        policy = self._component_policy(manifest)
        component_rows = {
            str(item.get("component_id") or ""): item
            for item in self._component_rows(manifest.id)
            if str(item.get("state") or "") == "installed"
        }

        cli_items: list[dict[str, Any]] = []
        for profile in policy["cliProfiles"]:
            detected_commands = self._discover_cli_commands(profile)
            registered_row = component_rows.get(profile.id)
            registered = registered_row is not None
            version_argv = self._expand_argv(manifest, profile.version)
            version_executable = Path(version_argv[0]).expanduser() if version_argv else None
            registered_present = bool(
                registered
                and (
                    detected_commands
                    or (version_executable is not None and version_executable.exists())
                )
            )
            state = "registered" if registered_present else "detected" if detected_commands else "missing"
            probe = {
                "ok": False,
                "version": "",
                "returnCode": None,
                "durationMs": 0,
            }
            if state in {"registered", "detected"}:
                try:
                    probe = self._probe_cli_version(
                        manifest,
                        profile,
                        detected_commands=detected_commands,
                    )
                except Exception as exc:
                    probe = {
                        **probe,
                        "error": str(exc).strip() or exc.__class__.__name__,
                    }
            installed_version = str(
                probe.get("version")
                or (registered_row or {}).get("source_version")
                or ""
            ).strip()
            available_version = self._catalog_cli_version(profile)
            update_supported = bool(profile.ownership == "managed" and available_version)
            version_state = self._version_state(
                installed_version,
                available_version,
                update_supported=update_supported,
            ) if state != "missing" else "unknown"
            action = "keep" if registered_present else "adopt" if detected_commands else "install"
            if action == "keep" and version_state == "available":
                action = "update"
            cli_items.append(
                {
                    "componentId": profile.id,
                    "componentType": "cli",
                    "displayName": profile.commands[0],
                    "description": next(
                        (
                            str(item.description).strip()
                            for item in profile.actions
                            if str(item.description or "").strip()
                        ),
                        manifest.description,
                    ),
                    "state": state,
                    "action": action,
                    "commands": list(profile.commands),
                    "detectedCommands": detected_commands,
                    "ownership": "plugin" if registered_present else "external" if detected_commands else profile.ownership,
                    "installedVersion": installed_version,
                    "availableVersion": available_version,
                    "versionState": version_state,
                    "updateSupported": update_supported,
                    "probe": probe,
                    "members": [
                        {
                            "name": command,
                            "description": next(
                                (
                                    str(item.description).strip()
                                    for item in profile.actions
                                    if str(item.description or "").strip()
                                ),
                                manifest.description,
                            ),
                        }
                        for command in profile.commands
                    ],
                }
            )

        skills_inventory = self._skills_cli_inventory(force=force) if policy["skills"] else {
            "ok": True,
            "tool": SKILLS_CLI_PACKAGE,
            "items": [],
            "lockEntries": {},
            "error": "",
        }
        inventory_by_name = {
            str(item.get("name") or ""): item
            for item in list(skills_inventory.get("items") or [])
            if str(item.get("name") or "").strip()
        }
        lock_entries = dict(skills_inventory.get("lockEntries") or {})
        skill_metadata_by_name, skill_metadata_by_root = self._cached_extension_skill_metadata()
        skill_items: list[dict[str, Any]] = []
        for skill in policy["skills"]:
            expected_names = list(skill.skillNames)
            registered_row = component_rows.get(skill.id)
            registered = registered_row is not None
            registered_metadata = _loads((registered_row or {}).get("metadata_json"), {})
            registered_names = {
                str(name).strip()
                for name in list(registered_metadata.get("skillNames") or [])
                if str(name).strip()
            }
            managed_names = {
                str(name).strip()
                for name in list(registered_metadata.get("managedSkillNames") or [])
                if str(name).strip()
            }
            if expected_names:
                candidate_names = expected_names
            else:
                candidate_names = sorted(
                    registered_names
                    | {
                        name
                        for name, entry in lock_entries.items()
                        if self._skill_source_matches(entry, skill)
                    }
                )
            registered_paths = [
                Path(str(path)).expanduser()
                for path in list(registered_metadata.get("skillPaths") or [])
                if str(path).strip()
            ]
            if not registered_paths and str((registered_row or {}).get("owned_path") or "").strip():
                registered_paths = [Path(str(registered_row["owned_path"])).expanduser()]
            registered_path_keys = {
                os.path.normcase(os.path.abspath(str(path)))
                for path in registered_paths
            }
            detected_names: list[str] = []
            conflicts: list[str] = []
            paths: list[str] = []
            receipt_version_fallback = False
            for name in candidate_names:
                installed = inventory_by_name.get(name)
                if not installed:
                    continue
                installed_path = str(installed.get("path") or "").strip()
                source_matches = self._skill_source_matches(lock_entries.get(name) or {}, skill)
                receipt_owns_installed_path = bool(
                    registered
                    and name in managed_names
                    and name in registered_names
                    and installed_path
                    and os.path.normcase(os.path.abspath(installed_path)) in registered_path_keys
                )
                if source_matches or receipt_owns_installed_path:
                    detected_names.append(name)
                    if installed_path:
                        paths.append(installed_path)
                    if receipt_owns_installed_path and not source_matches:
                        receipt_version_fallback = True
                elif name in expected_names:
                    conflicts.append(name)
            missing_names = [name for name in expected_names if name not in detected_names and name not in conflicts]
            registered_present = bool(
                registered
                and (
                    (
                        expected_names
                        and set(expected_names).issubset(registered_names)
                        and len(registered_paths) >= len(expected_names)
                        and all(path.exists() for path in registered_paths)
                    )
                    or (not expected_names and registered_paths and all(path.exists() for path in registered_paths))
                )
            )
            if registered_present:
                state = "registered"
                action = "keep"
            elif conflicts:
                state = "conflict"
                action = "review"
            elif expected_names and not missing_names:
                state = "detected"
                action = "adopt"
            elif detected_names:
                state = "partial"
                action = "complete"
            else:
                state = "missing" if skills_inventory.get("ok") else "unknown"
                action = "install" if skills_inventory.get("ok") else "review"
            installed_revisions = {
                str(
                    (lock_entries.get(name) or {}).get("ref")
                    or (lock_entries.get(name) or {}).get("revision")
                    or (lock_entries.get(name) or {}).get("commit")
                    or ""
                ).strip()
                for name in detected_names
            }
            installed_revisions.discard("")
            registered_version = str((registered_row or {}).get("source_version") or "").strip()
            if receipt_version_fallback:
                installed_version = registered_version
            elif len(installed_revisions) == 1:
                installed_version = next(iter(installed_revisions))
            else:
                installed_version = registered_version
            available_version = str(skill.revision or "").strip()
            update_supported = bool(
                skills_inventory.get("ok")
                and not conflicts
                and available_version
            )
            version_state = self._version_state(
                installed_version,
                available_version,
                update_supported=update_supported,
            ) if state not in {"missing", "unknown"} else "unknown"
            if action in {"keep", "adopt", "complete"} and version_state == "available":
                action = "update"
            members = []
            for name in expected_names or detected_names:
                metadata = dict(skill_metadata_by_name.get(name) or {})
                if not metadata:
                    installed = inventory_by_name.get(name) or {}
                    path = str(installed.get("path") or "").strip()
                    if path:
                        try:
                            metadata = dict(
                                skill_metadata_by_root.get(str(Path(path).expanduser().resolve()).lower())
                                or {}
                            )
                        except OSError:
                            metadata = {}
                members.append(
                    {
                        "name": name,
                        "description": str(
                            metadata.get("description")
                            or skill.reviewNote
                            or manifest.description
                        ).strip(),
                    }
                )
            skill_items.append(
                {
                    "componentId": skill.id,
                    "componentType": "skill",
                    "displayName": expected_names[0] if len(expected_names) == 1 else skill.id,
                    "description": str(skill.reviewNote or manifest.description).strip(),
                    "state": state,
                    "action": action,
                    "expectedNames": expected_names,
                    "detectedNames": detected_names,
                    "missingNames": missing_names,
                    "conflicts": conflicts,
                    "paths": paths,
                    "installer": SKILLS_CLI_PACKAGE,
                    "installedVersion": installed_version,
                    "availableVersion": available_version,
                    "versionState": version_state,
                    "updateSupported": update_supported,
                    "members": members,
                }
            )

        mcp_config = dict(storage.get_mcp_config().get("mcpServers") or {})
        mcp_status = self._cached_mcp_status()
        mcp_items: list[dict[str, Any]] = []
        ordinary_mcp: list[dict[str, Any]] = []
        selected_mcp_servers = list(policy["mcpServers"])
        selected_mcp_ids = {server.id for server in selected_mcp_servers}
        for server in manifest.mcpServers:
            if server.id in selected_mcp_ids:
                continue
            config = dict(mcp_config.get(server.serverName) or {})
            owner = str(config.get("x-v8-plugin-owner") or "").strip()
            if config and owner != manifest.id:
                ordinary_mcp.append(
                    {
                        "componentId": server.id,
                        "serverName": server.serverName,
                        "enabled": not bool(config.get("disabled", False)),
                        "managedBy": "extensions_runtime",
                        "note": "User-managed MCP configuration is not owned or modified by Plugin Manager.",
                    }
                )
        for server in selected_mcp_servers:
            config = dict(mcp_config.get(server.serverName) or {})
            owner = str(config.get("x-v8-plugin-owner") or "").strip()
            registered_row = component_rows.get(server.id)
            target_version = str(server.revision or manifest.version or "").strip()
            installed_version = str((registered_row or {}).get("source_version") or "").strip()
            live = dict(mcp_status.get(server.serverName) or {})
            if config and owner and owner != manifest.id:
                state = "conflict"
                action = "review"
                ordinary_mcp.append(
                    {
                        "componentId": server.id,
                        "serverName": server.serverName,
                        "enabled": not bool(config.get("disabled", False)),
                        "managedBy": "extensions_runtime",
                        "note": "User-managed MCP configuration is not owned or modified by Plugin Manager.",
                    }
                )
            elif config and not owner:
                state = "conflict"
                action = "review"
                ordinary_mcp.append(
                    {
                        "componentId": server.id,
                        "serverName": server.serverName,
                        "enabled": not bool(config.get("disabled", False)),
                        "managedBy": "extensions_runtime",
                        "note": "User-managed MCP configuration is not owned or modified by Plugin Manager.",
                    }
                )
            elif config:
                state = "registered" if registered_row else "detected"
                action = "keep" if registered_row else "adopt"
            else:
                state = "missing"
                action = "install"
            update_supported = bool(config and owner == manifest.id and target_version)
            version_state = self._version_state(
                installed_version,
                target_version,
                update_supported=update_supported,
            ) if state in {"registered", "detected"} else "unknown"
            if action == "keep" and version_state == "available":
                action = "update"
            live_tools = [
                {
                    "name": str(item.get("name") or "").strip(),
                    "description": str(item.get("description") or manifest.description).strip(),
                }
                for item in list(live.get("tools") or [])
                if str(item.get("name") or "").strip()
            ]
            members = live_tools or [
                {"name": name, "description": manifest.description}
                for name in server.allowedTools
            ] or [{"name": server.serverName, "description": manifest.description}]
            mcp_items.append(
                {
                    "componentId": server.id,
                    "componentType": "mcp",
                    "displayName": server.serverName,
                    "description": manifest.description,
                    "serverName": server.serverName,
                    "state": state,
                    "action": action,
                    "installedVersion": installed_version,
                    "availableVersion": target_version,
                    "runtimeVersion": str(live.get("serverInfoVersion") or "").strip(),
                    "protocolVersion": str(live.get("protocolVersion") or "").strip(),
                    "versionState": version_state,
                    "updateSupported": update_supported,
                    "members": members,
                }
            )

        all_components = [*cli_items, *skill_items, *mcp_items]
        conflicts = sum(1 for item in all_components if item["state"] == "conflict")
        missing = sum(1 for item in all_components if item["state"] in {"missing", "partial", "unknown"})
        detected = sum(1 for item in all_components if item["state"] in {"registered", "detected"})
        updates_available = sum(1 for item in all_components if item.get("action") == "update")
        total_units = (
            len(cli_items)
            + sum(max(1, len(item.get("expectedNames") or [])) for item in skill_items)
            + len(mcp_items)
        )
        present_units = sum(1 for item in cli_items if item["state"] in {"registered", "detected"})
        present_units += sum(
            len(item.get("expectedNames") or [])
            if item["state"] == "registered" and item.get("expectedNames")
            else len(item.get("detectedNames") or [])
            if item.get("expectedNames")
            else 1 if item["state"] in {"registered", "detected"} else 0
            for item in skill_items
        )
        present_units += sum(1 for item in mcp_items if item["state"] in {"registered", "detected"})
        missing_units = max(0, total_units - present_units)
        coverage = (
            "blocked"
            if conflicts
            else "complete"
            if total_units == present_units
            else "partial"
            if present_units
            else "none"
        )
        result = {
            "pluginId": manifest.id,
            "skillsCli": {
                "available": bool(skills_inventory.get("ok")),
                "package": SKILLS_CLI_PACKAGE,
                "version": str(skills_inventory.get("toolVersion") or ""),
                "probeOk": bool(skills_inventory.get("toolProbeOk")),
                "error": str(skills_inventory.get("error") or ""),
            },
            "cli": cli_items,
            "skills": skill_items,
            "mcp": mcp_items,
            "components": all_components,
            "ordinaryMcp": ordinary_mcp,
            "summary": {
                "detected": detected,
                "needsCompletion": missing,
                "conflicts": conflicts,
                "ordinaryMcp": len(ordinary_mcp),
                "updatesAvailable": updates_available,
                "presentUnits": present_units,
                "totalUnits": total_units,
                "missingUnits": missing_units,
                "coverage": coverage,
            },
        }
        with self._cache_lock:
            self._machine_discovery_cache[cache_key] = copy.deepcopy(result)
        return result

    def warm_machine_discovery(self) -> dict[str, Any]:
        """Warm local-only component discovery without installing or mutating anything."""

        catalog = plugin_catalog_service.load()
        completed: list[str] = []
        failures: list[dict[str, str]] = []
        for manifest in catalog.plugins:
            try:
                self.discover_machine_components(manifest.id, force=True)
                completed.append(manifest.id)
            except Exception as exc:
                failures.append(
                    {
                        "pluginId": manifest.id,
                        "error": str(exc).strip() or exc.__class__.__name__,
                    }
                )
        return {
            "status": "ready" if not failures else "degraded",
            "checked": len(completed),
            "failed": failures,
            "sideEffects": "none",
        }

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

        projected_plugins: list[dict[str, Any]] = []
        for manifest in catalog.plugins:
            policy = self._component_policy(manifest)
            active_skill_count = sum(max(1, len(item.skillNames)) for item in policy["skills"])
            declared_skill_count = sum(max(1, len(item.skillNames)) for item in manifest.skills)
            projected_plugins.append(
                {
                    **manifest.model_dump(mode="json"),
                    "manifestDigest": self._manifest_digest(manifest),
                    "catalogRevision": catalog.revision,
                    "componentCounts": {
                        "cli": len(policy["agentCliProfiles"]),
                        "runtimeSupport": len(policy["runtimeSupportProfiles"]),
                        "skills": active_skill_count,
                        "mcp": len(policy["mcpServers"]),
                        "uiAdapters": len(manifest.uiAdapters),
                        "providerAdapters": len(manifest.providerAdapters),
                    },
                    "declaredComponentCounts": {
                        "cli": sum(1 for item in manifest.cliProfiles if item.exposure == "agent"),
                        "runtimeSupport": sum(1 for item in manifest.cliProfiles if item.exposure == "runtime_support"),
                        "skills": declared_skill_count,
                        "mcp": len(manifest.mcpServers),
                        "uiAdapters": len(manifest.uiAdapters),
                        "providerAdapters": len(manifest.providerAdapters),
                    },
                    "componentPolicy": {
                        "mode": (
                            "setup_gated_cli_mcp"
                            if manifest.setupAdapter == "godot_v1"
                            else "official_cli_first"
                        ),
                        "transport": policy["transport"],
                        "installable": policy["installable"],
                        "blockingReasons": list(policy["blockingReasons"]),
                        "selectedComponentIds": sorted(policy["activeComponentIds"]),
                        "runtimeSupportComponentIds": sorted(policy["runtimeSupportComponentIds"]),
                        "skippedComponentIds": list(policy["skippedComponentIds"]),
                    },
                    "grantRequired": bool(policy["agentComponentIds"]),
                    "runtimeManaged": bool(policy["runtimeSupportComponentIds"]),
                    "brandAssetUrl": f"/v1/api/plugins/{manifest.id}/logo",
                }
            )
        plugins = tuple(projected_plugins)
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

    def _component_policy(
        self,
        manifest: PluginManifest,
        *,
        platform_name: str | None = None,
        architecture_name: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the one installable transport without loading every integration.

        Curated plugins prefer an official CLI on the current platform. An
        official MCP server is selected only when no usable CLI exists. Skills
        remain companions to either transport and are never replaced by MCP.
        """

        current_platform = str(platform_name or _platform_name()).strip().lower()
        current_architecture = str(architecture_name or _architecture_name()).strip().lower()
        selected_cli = [
            profile
            for profile in manifest.cliProfiles
            if current_platform in profile.platforms
            and (not profile.architectures or current_architecture in profile.architectures)
        ]
        selected_agent_cli = [profile for profile in selected_cli if profile.exposure == "agent"]
        selected_runtime_support = [profile for profile in selected_cli if profile.exposure == "runtime_support"]
        if manifest.setupAdapter == "godot_v1":
            selected_mcp = list(manifest.mcpServers)
            transport = "cli_mcp"
        elif selected_agent_cli:
            selected_mcp = []
            transport = "cli"
        elif manifest.mcpServers:
            selected_mcp = list(manifest.mcpServers)
            transport = "mcp_platform_fallback" if any(item.exposure == "agent" for item in manifest.cliProfiles) else "mcp"
        elif selected_runtime_support:
            selected_mcp = []
            transport = "runtime_support"
        else:
            selected_mcp = []
            transport = "skill_only" if manifest.skills else "none"

        selected_cli_ids = {item.id for item in selected_cli}
        selected_mcp_ids = {item.id for item in selected_mcp}
        setup_scenario = ""
        if manifest.setupAdapter == "godot_v1":
            setup_scenario = str(self._setup_values(manifest.id).get("scenario") or "").strip().lower()
        selected_skills = []
        blocked_skills = []
        skipped_scenario_skills = []
        for skill in manifest.skills:
            if skill.setupScenarios and setup_scenario not in set(skill.setupScenarios):
                skipped_scenario_skills.append(skill.id)
                continue
            if skill.sourceKind == "managed_cli" and str(skill.sourceComponentId or "") not in selected_cli_ids:
                blocked_skills.append(skill.id)
                continue
            selected_skills.append(skill)

        blocking_reasons: list[str] = []
        if manifest.cliProfiles and not selected_cli and not selected_mcp:
            blocking_reasons.append(
                f"no supported official CLI or MCP transport for {current_platform}/{current_architecture}"
            )
        if blocked_skills:
            blocking_reasons.append(
                "required companion Skills depend on an unavailable managed CLI: " + ", ".join(sorted(blocked_skills))
            )

        active_component_ids = {
            *[item.id for item in selected_cli],
            *[item.id for item in selected_skills],
            *[item.id for item in selected_mcp],
            *[item.id for item in manifest.uiAdapters],
            *[item.id for item in manifest.providerAdapters],
        }
        agent_component_ids = {
            *[item.id for item in selected_agent_cli],
            *[item.id for item in selected_skills],
            *[item.id for item in selected_mcp],
            *[item.id for item in manifest.uiAdapters],
            *[item.id for item in manifest.providerAdapters],
        }
        return {
            "platform": current_platform,
            "architecture": current_architecture,
            "transport": transport,
            "installable": not blocking_reasons,
            "blockingReasons": blocking_reasons,
            "cliProfiles": selected_cli,
            "agentCliProfiles": selected_agent_cli,
            "runtimeSupportProfiles": selected_runtime_support,
            "skills": selected_skills,
            "mcpServers": selected_mcp,
            "activeComponentIds": active_component_ids,
            "agentComponentIds": agent_component_ids,
            "runtimeSupportComponentIds": {item.id for item in selected_runtime_support},
            "skippedComponentIds": sorted(
                {
                    *[item.id for item in manifest.cliProfiles if item.id not in selected_cli_ids],
                    *[item.id for item in manifest.mcpServers if item.id not in selected_mcp_ids],
                    *blocked_skills,
                    *skipped_scenario_skills,
                }
            ),
        }

    def _setup_values(self, plugin_id: str) -> dict[str, Any]:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT values_json FROM plugin_setup_state WHERE plugin_id=?",
                (str(plugin_id or "").strip().lower(),),
            ).fetchone()
        return dict(_loads(row["values_json"], {})) if row else {}

    def plugin_setup(self, plugin_id: str, *, probe: bool = False) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        if manifest.setupAdapter != "godot_v1":
            raise PluginManagerError(
                "插件没有分步接入流程",
                code="plugin_setup_unavailable",
                status_code=404,
            )
        values = self._setup_values(manifest.id)
        status = evaluate_godot_setup(values, probe_mcp=probe)
        return {"pluginId": manifest.id, **stable_godot_setup_projection(values, status)}

    def update_plugin_setup(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        if manifest.setupAdapter != "godot_v1":
            raise PluginManagerError(
                "插件没有分步接入流程",
                code="plugin_setup_unavailable",
                status_code=404,
            )
        current = self._setup_values(manifest.id)
        allowed = {"godotExecutable", "projectPath", "scenario"}
        for key, value in dict(values or {}).items():
            if key not in allowed:
                raise PluginManagerError("接入字段不受支持", code="plugin_setup_field_invalid")
            if not isinstance(value, str):
                raise PluginManagerError("接入字段必须是文本", code="plugin_setup_value_invalid")
            current[key] = value.strip()
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO plugin_setup_state (plugin_id, adapter, values_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    adapter=excluded.adapter,
                    values_json=excluded.values_json,
                    updated_at=excluded.updated_at
                """,
                (manifest.id, manifest.setupAdapter, _json(current), now),
            )
            conn.commit()
        self._invalidate_machine_discovery_cache(manifest.id)
        self._event(manifest.id, "setup_updated", "ok", details={"fields": sorted(values)})
        return self.plugin_setup(manifest.id, probe=False)

    def _active_installed_component_ids(self, manifest: PluginManifest) -> set[str]:
        policy = self._component_policy(manifest)
        installed_ids = {str(item.get("component_id") or "") for item in self._component_rows(manifest.id)}
        return set(policy["activeComponentIds"]).intersection(installed_ids)

    def _grantable_installed_component_ids(self, manifest: PluginManifest) -> set[str]:
        policy = self._component_policy(manifest)
        return self._active_installed_component_ids(manifest).intersection(policy["agentComponentIds"])

    def _requirement_component_ids(self, manifest: PluginManifest) -> set[str]:
        policy_ids = set(self._component_policy(manifest)["activeComponentIds"])
        if manifest.id not in self._installation_rows():
            return policy_ids
        installed_ids = self._active_installed_component_ids(manifest)
        return installed_ids or policy_ids

    def _plugin_root(self, plugin_id: str) -> Path:
        config = storage.get_plugin_manager_config()
        configured = str(config.get("installRoot") or "").strip()
        root = Path(configured).expanduser() if configured else PLUGIN_MANAGER_ROOT
        return root / plugin_id

    def _cli_capability_snapshot_path(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        *,
        plugin_root: Path | None = None,
    ) -> Path | None:
        if profile.capabilitySync is None:
            return None
        root = plugin_root or self._plugin_root(manifest.id)
        return root / profile.capabilitySync.snapshotPath

    def _effective_cli_profile(self, manifest: PluginManifest, profile: CliProfile) -> CliProfile:
        snapshot_path = self._cli_capability_snapshot_path(manifest, profile)
        if snapshot_path is None:
            return profile
        try:
            stat = snapshot_path.stat()
        except OSError:
            return profile
        cache_key = (manifest.id, profile.id, stat.st_mtime_ns, stat.st_size)
        cached = self._cli_capability_profile_cache.get(cache_key)
        if cached is not None:
            return cached
        snapshot = read_snapshot(snapshot_path)
        if (
            not snapshot
            or str(snapshot.get("pluginId") or "") != manifest.id
            or str(snapshot.get("profileId") or "") != profile.id
        ):
            return profile
        discovered = actions_from_snapshot(snapshot)
        effective = profile.model_copy(
            update={"actions": merge_discovered_actions(profile.actions, discovered)}
        )
        self._cli_capability_profile_cache = {
            key: value
            for key, value in self._cli_capability_profile_cache.items()
            if key[:2] != (manifest.id, profile.id)
        }
        self._cli_capability_profile_cache[cache_key] = effective
        return effective

    def _sync_cli_profile_capabilities(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        *,
        plugin_root: Path | None = None,
        previous_root: Path | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any] | None:
        contract = profile.capabilitySync
        target_path = self._cli_capability_snapshot_path(manifest, profile, plugin_root=plugin_root)
        if contract is None or target_path is None:
            return None
        version_spec = profile.version
        if plugin_root is None:
            version_spec = self._effective_cli_spec(manifest, profile, version_spec)
        expanded = self._expand_argv(manifest, version_spec, plugin_root=plugin_root)
        if not expanded:
            raise PluginManagerError("CLI capability sync 缺少可执行文件", code="plugin_cli_capability_executable_missing")
        capability_executable = expanded[0]
        if contract.adapter == "gda_cli_v1":
            capability_executable = self._command_context(
                manifest,
                plugin_root=plugin_root,
            )["gdaExecutable"]
        previous_path = (
            self._cli_capability_snapshot_path(manifest, profile, plugin_root=previous_root)
            if previous_root is not None
            else target_path
        )
        try:
            if contract.adapter == "reviewed_help_v1":
                result = sync_reviewed_help_capabilities(
                    executable=capability_executable,
                    canonical_command=profile.commands[0],
                    version_arguments=expanded[1:],
                    plugin_id=manifest.id,
                    profile_id=profile.id,
                    reviewed_roots=profile.allowedArguments,
                    help_arguments=contract.helpArguments,
                    help_placement=contract.helpPlacement,
                    target_path=target_path,
                    previous_path=previous_path,
                    block_breaking_upgrade=contract.blockBreakingUpgrade,
                    force_refresh=force_refresh,
                )
            else:
                sync_adapter = {
                    "mediakit_cli_v1": sync_mediakit_capabilities,
                    "gda_cli_v1": sync_gda_capabilities,
                }.get(contract.adapter)
                if sync_adapter is None:
                    raise PluginManagerError(
                        f"不支持 CLI capability adapter：{contract.adapter}",
                        code="plugin_cli_capability_adapter_unsupported",
                    )
                result = sync_adapter(
                    executable=capability_executable,
                    plugin_id=manifest.id,
                    profile_id=profile.id,
                    target_path=target_path,
                    previous_path=previous_path,
                    block_breaking_upgrade=contract.blockBreakingUpgrade,
                    force_refresh=force_refresh,
                )
            if result is None:
                raise PluginManagerError(
                    f"不支持 CLI capability adapter：{contract.adapter}",
                    code="plugin_cli_capability_adapter_unsupported",
                )
        except CliCapabilitySyncError as exc:
            raise PluginManagerError(
                str(exc),
                code="plugin_cli_capability_sync_failed",
            ) from exc
        self._cli_capability_profile_cache.clear()
        if not result.get("accepted"):
            issue_count = len(list(result.get("issues") or []))
            raise PluginManagerError(
                f"CLI 升级包含 {issue_count} 项破坏性 schema 变化；已保留上一个可用能力快照",
                code="plugin_cli_capability_breaking_upgrade",
            )
        return result

    def sync_cli_capabilities(self, plugin_id: str, profile_id: str) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        profile = next((item for item in manifest.cliProfiles if item.id == profile_id), None)
        if profile is None:
            raise PluginManagerError("CLI profile 不存在", code="plugin_cli_profile_not_found", status_code=404)
        if profile.exposure != "agent":
            raise PluginManagerError(
                "runtime-support 组件不提供 Agent CLI 能力同步",
                code="plugin_cli_runtime_support_denied",
                status_code=403,
            )
        result = self._sync_cli_profile_capabilities(manifest, profile, force_refresh=True)
        if result is None:
            raise PluginManagerError("CLI profile 未声明 schema 同步合同", code="plugin_cli_capability_sync_unavailable")
        return result

    def resolve_cli_action(
        self,
        plugin_id: str,
        profile_id: str,
        command_path: Iterable[str],
    ) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        profile = next((item for item in manifest.cliProfiles if item.id == profile_id), None)
        if profile is None:
            raise PluginManagerError("CLI profile 不存在", code="plugin_cli_profile_not_found", status_code=404)
        if profile.exposure != "agent":
            raise PluginManagerError(
                "runtime-support 组件不提供 Agent CLI 动作",
                code="plugin_cli_runtime_support_denied",
                status_code=403,
            )
        contract = profile.capabilitySync
        if contract is None or contract.adapter != "reviewed_help_v1":
            raise PluginManagerError(
                "该 CLI 使用固定或供应商 schema，不需要按 help 解析动作",
                code="plugin_cli_action_resolution_unavailable",
            )
        if profile.id not in self._active_installed_component_ids(manifest):
            raise PluginManagerError("CLI 组件尚未安装", code="plugin_cli_component_not_installed", status_code=409)
        normalized_path = [str(item or "").strip() for item in command_path if str(item or "").strip()]
        if not normalized_path:
            raise PluginManagerError("command_path 不能为空", code="plugin_cli_command_path_required")
        self._sync_cli_profile_capabilities(manifest, profile, force_refresh=False)
        target_path = self._cli_capability_snapshot_path(manifest, profile)
        if target_path is None:
            raise PluginManagerError("CLI capability snapshot 不存在", code="plugin_cli_capability_snapshot_missing")
        version_spec = self._effective_cli_spec(manifest, profile, profile.version)
        expanded = self._expand_argv(manifest, version_spec)
        if not expanded:
            raise PluginManagerError("CLI capability sync 缺少可执行文件", code="plugin_cli_capability_executable_missing")
        try:
            result = resolve_reviewed_help_capability(
                executable=expanded[0],
                canonical_command=profile.commands[0],
                command_path=normalized_path,
                help_arguments=contract.helpArguments,
                help_placement=contract.helpPlacement,
                target_path=target_path,
                max_cached_actions=contract.maxCachedActions,
                block_breaking_upgrade=contract.blockBreakingUpgrade,
            )
        except CliCapabilitySyncError as exc:
            raise PluginManagerError(str(exc), code="plugin_cli_action_resolution_failed") from exc
        self._cli_capability_profile_cache.clear()
        if result.get("kind") == "group":
            return {
                "pluginId": manifest.id,
                "profileId": profile.id,
                "kind": "group",
                "commandPath": list(result.get("commandPath") or []),
                "children": list(result.get("children") or []),
            }
        action = dict(result.get("action") or {})
        return {
            "pluginId": manifest.id,
            "profileId": profile.id,
            "kind": "action",
            "commandPath": list(result.get("commandPath") or []),
            "action": {
                "id": action.get("id"),
                "description": action.get("description"),
                "mutating": bool(action.get("mutating")),
                "inputSchema": dict(action.get("inputSchema") or {}),
            },
        }

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
            component_rows = self._component_rows(plugin_id)
            active_component_ids = self._active_installed_component_ids(manifest) if manifest else set()
            items.append(
                {
                    "pluginId": plugin_id,
                    "displayName": manifest.displayName if manifest else plugin_id,
                    **self._installation_payload(row),
                    "components": [
                        self._component_payload(item)
                        for item in component_rows
                        if not manifest or str(item.get("component_id") or "") in active_component_ids
                    ],
                    "inactiveComponentCount": len(
                        [
                            item
                            for item in component_rows
                            if manifest and str(item.get("component_id") or "") not in active_component_ids
                        ]
                    ),
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

    def _grantable_components(self, manifest: PluginManifest) -> list[dict[str, Any]]:
        active_ids = self._grantable_installed_component_ids(manifest)
        components: list[dict[str, Any]] = []
        components.extend(
            {
                "id": item.id,
                "type": "cli",
                "actions": [action.id for action in self._effective_cli_profile(manifest, item).actions],
            }
            for item in manifest.cliProfiles
            if item.id in active_ids and item.exposure == "agent"
        )
        components.extend({"id": item.id, "type": "skill"} for item in manifest.skills if item.id in active_ids)
        components.extend(
            {
                "id": item.id,
                "type": "mcp",
                "tools": list(item.allowedTools),
            }
            for item in manifest.mcpServers
            if item.id in active_ids
        )
        components.extend({"id": item.id, "type": "ui_adapter"} for item in manifest.uiAdapters if item.id in active_ids)
        components.extend(
            {"id": item.id, "type": "provider_adapter"}
            for item in manifest.providerAdapters
            if item.id in active_ids
        )
        return components

    def _plugin_usage_preview(self, manifest: PluginManifest) -> dict[str, Any]:
        """Load bounded usage hints only after a named plugin status request."""

        self._refresh_process_cli_path()
        policy = self._component_policy(manifest)
        active_ids = self._active_installed_component_ids(manifest)
        component_rows = {
            str(item.get("component_id") or ""): item
            for item in self._component_rows(manifest.id)
        }
        cli_items: list[dict[str, Any]] = []
        for profile in policy["agentCliProfiles"]:
            if profile.id not in active_ids:
                continue
            sync_error = ""
            if profile.capabilitySync is not None:
                try:
                    self._sync_cli_profile_capabilities(manifest, profile, force_refresh=False)
                except PluginManagerError as exc:
                    sync_error = exc.code
            snapshot_path = self._cli_capability_snapshot_path(manifest, profile)
            snapshot = read_snapshot(snapshot_path) if snapshot_path else None
            effective_profile = self._effective_cli_profile(manifest, profile)
            cli_items.append(
                {
                    "componentId": profile.id,
                    "command": profile.commands[0],
                    "available": not sync_error,
                    "rootCommands": list((snapshot or {}).get("rootCommands") or []),
                    "actions": [
                        {
                            "id": action.id,
                            "description": action.description or "",
                            "mutating": action.mutating,
                        }
                        for action in effective_profile.actions
                    ],
                    **({"errorCode": sync_error} if sync_error else {}),
                }
            )

        skill_items: list[dict[str, str]] = []
        extension_skills: list[dict[str, Any]] = []
        try:
            from runtimes.extensions.runtime import extensions_runtime_service

            extension_skills = extensions_runtime_service.list_skills(
                force_refresh=False,
                prefer_cached_ready_inventory=True,
                include_scoped=False,
            )
        except Exception:
            extension_skills = []
        extensions_by_root = {
            str(Path(str(item.get("skillRoot") or item.get("path") or "")).resolve()).lower(): item
            for item in extension_skills
            if str(item.get("skillRoot") or item.get("path") or "").strip()
        }
        for skill in policy["skills"]:
            if skill.id not in active_ids:
                continue
            row = component_rows.get(skill.id) or {}
            metadata = _loads(row.get("metadata_json"), {})
            installed_roots = [
                Path(str(path))
                for path in list(metadata.get("skillPaths") or [])
                if str(path).strip()
            ]
            entries_by_root = {
                str(root.resolve()).lower(): extensions_by_root.get(str(root.resolve()).lower())
                for root in installed_roots
            }
            matched_entries = [entry for entry in entries_by_root.values() if isinstance(entry, dict)]
            if matched_entries:
                for entry in matched_entries:
                    skill_items.append(
                        {
                            "componentId": skill.id,
                            "name": str(entry.get("skillName") or entry.get("name") or "").strip(),
                            "summary": str(entry.get("description") or "").strip()[:500],
                        }
                    )
            else:
                skill_items.extend(
                    {"componentId": skill.id, "name": name, "summary": ""}
                    for name in list(skill.skillNames or [skill.targetDirectory])
                )

        mcp_items: list[dict[str, str]] = []
        selected_servers = {
            server.serverName: server
            for server in policy["mcpServers"]
            if server.id in active_ids
        }
        if selected_servers:
            try:
                from runtimes.extensions.runtime import extensions_runtime_service

                for tool_ref in extensions_runtime_service.get_mcp_tools():
                    metadata = dict(getattr(tool_ref, "metadata", None) or {})
                    server_name = str(metadata.get("server_name") or "").strip()
                    server = selected_servers.get(server_name)
                    tool_name = str(getattr(tool_ref, "name", "") or "").strip()
                    if server is None or tool_name not in set(server.allowedTools):
                        continue
                    mcp_items.append(
                        {
                            "componentId": server.id,
                            "name": tool_name,
                            "summary": str(getattr(tool_ref, "description", "") or "").strip()[:500],
                        }
                    )
            except Exception:
                mcp_items = []

        return {
            "transport": policy["transport"],
            "cli": cli_items,
            "skills": skill_items,
            "mcpTools": mcp_items,
        }

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
            component_policy = self._component_policy(manifest)
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
                    "componentPolicy": {
                        "transport": component_policy["transport"],
                        "skippedComponentIds": component_policy["skippedComponentIds"],
                    },
                    "configurationUrl": readiness["configurationUrl"],
                    **({"usage": self._plugin_usage_preview(manifest)} if normalized_id else {}),
                }
            )
        named_usage = (
            dict(items[0].get("usage") or {})
            if normalized_id and items and isinstance(items[0].get("usage"), dict)
            else {}
        )
        if named_usage.get("cli"):
            next_action = (
                "Component IDs are grant identifiers, never CLI actions, Skill names, or MCP tool names. Authorize the "
                "smallest matching component set, then invoke an authorized CLI through plugin_cli using actionId plus "
                "typed parameters. Never bypass the plugin grant with run_system_command."
            )
        elif normalized_id:
            next_action = (
                "Component IDs are grant identifiers, never Skill or MCP tool names. Authorize only the smallest matching "
                "Skill/MCP component, then use the listed runtime name."
            )
        else:
            next_action = "Use status with a plugin_id to load its on-demand CLI help, Skill metadata, or MCP tool metadata."
        return {
            "mode": "status" if normalized_id else "list",
            "items": items,
            "count": len(items),
            "policy": "@插件是强提示；Supervisor 只能为当前 run 授权已就绪插件的最小组件集合。",
            "nextAction": next_action,
        }

    def supervisor_availability_prompt(self) -> str:
        """Render a small, independent catalog hint for the Supervisor.

        This is intentionally not an Extensions shortlist. It advertises only
        installed plugins and never expands Skill bodies, MCP tool schemas, or
        CLI action definitions before plugin_broker is called.
        """

        installations = self._installation_rows()
        lines: list[str] = []
        for manifest in plugin_catalog_service.load().plugins:
            install = self._installation_payload(installations.get(manifest.id))
            if not install["installed"]:
                continue
            readiness = self.readiness_status(manifest.id)
            capability_text = "; ".join(str(item).strip() for item in manifest.capabilities if str(item).strip())
            artifact_text = "; ".join(str(item).strip() for item in manifest.artifacts if str(item).strip())
            status = str(readiness.get("status") or "invalid")
            summary = capability_text or manifest.description
            artifacts = artifact_text or "typed results and artifact references declared by the plugin"
            lines.append(f"- {manifest.id} ({status}): {summary} -> {artifacts}")
        if not lines:
            return ""
        return "\n".join(
            [
                "[Plugin Catalog]",
                "Curated plugins are optional, on-demand capabilities. This catalog hint does not alter ordinary Extensions routing.",
                "Only a current explicit reference to a registered, installed plugin activates the privileged package route and bypasses generic Skill/MCP prefiltering for that request.",
                "Plugin Skills are portable instructions; upstream references to a particular agent host do not make them host-exclusive.",
                "Call plugin_broker(status) to load the exact on-demand route; no Skill body, MCP schema, or CLI help is loaded by this hint.",
                "Plugin CLI actions require a minimal task grant and run through plugin_cli(actionId, typed parameters); never route catalog-owned CLI commands through run_system_command. Component IDs are grant identifiers, not CLI actions, Skill names, or MCP tool names.",
                *lines,
                "[/Plugin Catalog]",
            ]
        )

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
        active_component_ids = self._requirement_component_ids(manifest)
        for requirement in compile_plugin_requirements(manifest, component_ids=active_component_ids):
            binding = bindings.get(requirement.id)
            configured = False
            server_config = server_by_component.get(str(requirement.componentId or ""), {})
            if requirement.kind == "oauth":
                oauth_state = server_config.get("x-v8-oauth") if isinstance(server_config.get("x-v8-oauth"), dict) else {}
                oauth_ref = str(oauth_state.get("secretRef") or "").strip()
                configured = bool(oauth_ref) and self._credential_store.status(oauth_ref).configured
            elif requirement.kind == "cli_login":
                configured = self.cli_login_status(
                    manifest.id,
                    component_id=str(requirement.componentId or ""),
                )["status"] == "connected"
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
            (
                item
                for item in compile_plugin_requirements(
                    manifest,
                    component_ids=self._requirement_component_ids(manifest),
                )
                if item.id == requirement_id
            ),
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
            for item in compile_plugin_requirements(
                manifest,
                component_ids=self._requirement_component_ids(manifest),
            )
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

    def _cli_browser_auth_contract(
        self,
        plugin_id: str,
        *,
        component_id: str,
    ) -> tuple[PluginManifest, CliProfile, CliBrowserAuthAdapter]:
        manifest = self._manifest(plugin_id)
        if manifest.id not in self._installation_rows():
            raise PluginManagerError("请先安装插件", code="plugin_not_installed", status_code=409)
        normalized_component = str(component_id or "").strip()
        profile = next((item for item in manifest.cliProfiles if item.id == normalized_component), None)
        adapter = browser_auth_adapter(manifest, profile) if profile else None
        if profile is None or profile.exposure != "agent" or profile.login is None or adapter is None:
            raise PluginManagerError(
                "插件没有受支持的 CLI 浏览器登录入口",
                code="plugin_cli_browser_login_not_found",
                status_code=404,
            )
        if profile.id not in self._active_installed_component_ids(manifest):
            raise PluginManagerError("CLI 组件尚未登记", code="plugin_cli_not_installed", status_code=409)
        return manifest, profile, adapter

    def _cli_auth_environment(self, adapter: CliBrowserAuthAdapter) -> dict[str, str]:
        environment = {
            **os.environ,
            "PATH": f"{self._bin_root()}{os.pathsep}{self._cli_search_path()}",
            "NO_COLOR": "1",
            "GH_PROMPT_DISABLED": "1",
        }
        for name in adapter.clear_environment:
            environment.pop(name, None)
        return environment

    def _run_cli_auth_status(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        adapter: CliBrowserAuthAdapter,
    ) -> bool:
        spec = self._effective_cli_spec(
            manifest,
            profile,
            CommandSpec(argv=adapter.status_argv(profile), timeoutSeconds=15),
        )
        try:
            completed = run_windowless(
                self._expand_argv(manifest, spec),
                cwd=spec.cwd or None,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=spec.timeoutSeconds,
                env=self._cli_auth_environment(adapter),
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def start_cli_login(
        self,
        plugin_id: str,
        *,
        component_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        manifest, profile, adapter = self._cli_browser_auth_contract(
            plugin_id,
            component_id=component_id,
        )
        key = (manifest.id, profile.id)
        started_at = utc_now_iso()
        with self._cache_lock:
            current = self._cli_auth_states.get(key) or {}
            process = current.get("process")
            if process is not None and process.poll() is None:
                return {
                    "pluginId": manifest.id,
                    "componentId": profile.id,
                    "status": "waiting_for_browser",
                    "authorizationUrl": adapter.browser_url,
                    "browserOpened": bool(current.get("browserOpened")),
                    "interactionHint": adapter.interaction_hint,
                }
            if str(current.get("status") or "") == "connecting":
                return {
                    "pluginId": manifest.id,
                    "componentId": profile.id,
                    "status": "connecting",
                    "authorizationUrl": adapter.browser_url,
                    "browserOpened": False,
                    "interactionHint": adapter.interaction_hint,
                }
            self._cli_auth_states[key] = {
                "status": "connecting",
                "startedAt": started_at,
                "browserOpened": False,
            }
        if not force and self._run_cli_auth_status(manifest, profile, adapter):
            with self._cache_lock:
                self._cli_auth_states[key] = {
                    "status": "connected",
                    "startedAt": started_at,
                    "browserOpened": False,
                }
            return {
                "pluginId": manifest.id,
                "componentId": profile.id,
                "status": "connected",
                "authorizationUrl": adapter.browser_url,
                "browserOpened": False,
                "interactionHint": adapter.interaction_hint,
            }

        login_spec = self._effective_cli_spec(
            manifest,
            profile,
            profile.login.model_copy(update={"argv": adapter.login_argv(profile)}),
        )
        popen_kwargs: dict[str, Any] = {
            "cwd": login_spec.cwd or None,
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": self._cli_auth_environment(adapter),
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(self._expand_argv(manifest, login_spec), **popen_kwargs)
        except (FileNotFoundError, OSError) as exc:
            with self._cache_lock:
                self._cli_auth_states[key] = {
                    "status": "failed",
                    "startedAt": started_at,
                    "browserOpened": False,
                }
            raise PluginManagerError(
                "无法启动 CLI 浏览器登录",
                code="plugin_cli_browser_login_start_failed",
                status_code=409,
            ) from exc
        with self._cache_lock:
            self._cli_auth_states[key] = {
                "process": process,
                "status": "waiting_for_browser",
                "startedAt": started_at,
                "browserOpened": False,
            }
        browser_opened = adapter.cli_opens_browser or bool(
            adapter.browser_url and open_system_browser(adapter.browser_url)
        )
        with self._cache_lock:
            self._cli_auth_states[key]["browserOpened"] = browser_opened
        self._event(manifest.id, "cli_login_started", "pending", details={"componentId": profile.id})
        return {
            "pluginId": manifest.id,
            "componentId": profile.id,
            "status": "waiting_for_browser",
            "authorizationUrl": adapter.browser_url,
            "browserOpened": browser_opened,
            "interactionHint": adapter.interaction_hint,
        }

    def cli_login_status(self, plugin_id: str, *, component_id: str) -> dict[str, Any]:
        manifest, profile, adapter = self._cli_browser_auth_contract(
            plugin_id,
            component_id=component_id,
        )
        key = (manifest.id, profile.id)
        with self._cache_lock:
            state = dict(self._cli_auth_states.get(key) or {})
        process = state.get("process")
        if process is not None and process.poll() is None:
            status = "waiting_for_browser"
        elif self._run_cli_auth_status(manifest, profile, adapter):
            status = "connected"
        elif process is not None:
            status = "failed"
        else:
            status = str(state.get("status") or "idle")
        if status != state.get("status") or (process is not None and process.poll() is not None):
            with self._cache_lock:
                self._cli_auth_states[key] = {
                    "status": status,
                    "startedAt": state.get("startedAt"),
                }
        return {
            "pluginId": manifest.id,
            "componentId": profile.id,
            "status": status,
            "error": "CLI 登录未完成，请重试。" if status == "failed" else None,
            "authorizationUrl": adapter.browser_url,
            "interactionHint": adapter.interaction_hint,
        }

    def cancel_cli_login(self, plugin_id: str, *, component_id: str) -> dict[str, Any]:
        manifest, profile, adapter = self._cli_browser_auth_contract(
            plugin_id,
            component_id=component_id,
        )
        key = (manifest.id, profile.id)
        with self._cache_lock:
            state = dict(self._cli_auth_states.get(key) or {})
        process = state.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        with self._cache_lock:
            self._cli_auth_states[key] = {"status": "cancelled", "startedAt": state.get("startedAt")}
        self._event(manifest.id, "cli_login_cancelled", "cancelled", details={"componentId": profile.id})
        return {
            "pluginId": manifest.id,
            "componentId": profile.id,
            "status": "cancelled",
            "authorizationUrl": adapter.browser_url,
            "interactionHint": adapter.interaction_hint,
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
            "enginePython": sys.executable,
            "engineRoot": str(Path(__file__).resolve().parents[2]),
            "gdaExecutable": str(root / "bin" / ("gda.exe" if os.name == "nt" else "gda")),
        }

    def _setup_environment(
        self,
        manifest: PluginManifest,
        *,
        plugin_root: Path | None = None,
    ) -> dict[str, str]:
        if manifest.setupAdapter != "godot_v1":
            return {}
        setup = self.plugin_setup(manifest.id, probe=False)
        values = self._setup_values(manifest.id)
        if not bool((setup.get("status") or {}).get("offlinePrerequisitesReady")):
            raise PluginManagerError(
                "Godot 应用、项目和场景尚未完成验证",
                code="plugin_setup_incomplete",
                status_code=409,
            )
        root = plugin_root or self._plugin_root(manifest.id)
        return {
            "PYTHONUTF8": "1",
            "GDA_GODOT": str(values.get("godotExecutable") or ""),
            "GDA_PROJECT": str(values.get("projectPath") or ""),
            "UV_TOOL_DIR": str(root / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(root / "bin"),
            "UV_PYTHON_INSTALL_DIR": str(root / "python"),
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
        component_policy = self._component_policy(manifest, platform_name=current_platform)
        machine_discovery = self.discover_machine_components(manifest.id)
        discovered_cli = {
            str(item.get("componentId") or ""): item
            for item in list(machine_discovery.get("cli") or [])
        }
        discovered_skills = {
            str(item.get("componentId") or ""): item
            for item in list(machine_discovery.get("skills") or [])
        }
        discovered_mcp = {
            str(item.get("componentId") or ""): item
            for item in list(machine_discovery.get("mcp") or [])
        }
        skills_cli_available = bool(
            (machine_discovery.get("skillsCli") or {}).get("available")
        )
        cli_steps = []
        approval_classes = set(manifest.governance.approvalClasses)
        for profile in component_policy["cliProfiles"]:
            argv = self._expand_argv(manifest, profile.install)
            discovery = dict(discovered_cli.get(profile.id) or {})
            action = str(discovery.get("action") or "install")
            approval_required = bool(
                action == "install"
                and (
                    profile.ownership == "external"
                    or profile.install.requiresElevation
                    or profile.install.mayRestart
                    or "system-install" in approval_classes
                )
            )
            cli_steps.append(
                {
                    "componentId": profile.id,
                    "action": action,
                    "detectedCommands": dict(discovery.get("detectedCommands") or {}),
                    "supported": True,
                    "ownership": profile.ownership,
                    "argv": argv,
                    "estimatedDownloadMb": profile.install.estimatedDownloadMb,
                    "requiresElevation": profile.install.requiresElevation,
                    "mayRestart": profile.install.mayRestart,
                    "approvalRequired": approval_required,
                }
            )
        skill_steps = []
        for skill in component_policy["skills"]:
            discovery = dict(discovered_skills.get(skill.id) or {})
            skill_conflict = bool(
                str(discovery.get("state") or "").strip().lower() == "conflict"
                or list(discovery.get("conflicts") or [])
            )
            skill_supported = bool(skills_cli_available and not skill_conflict)
            blocked_reason = (
                "skill_name_conflict"
                if skill_conflict
                else "skills_cli_unavailable"
                if not skills_cli_available
                else ""
            )
            skill_steps.append(
                {
                    **skill.model_dump(mode="json"),
                    "action": (
                        str(discovery.get("action") or "install")
                        if skill_supported
                        else "blocked"
                    ),
                    "state": (
                        str(discovery.get("state") or "missing")
                        if skill_supported
                        else "blocked"
                    ),
                    "supported": skill_supported,
                    **(
                        {"blockedReason": blocked_reason}
                        if blocked_reason
                        else {}
                    ),
                    "detectedNames": list(discovery.get("detectedNames") or []),
                    "missingNames": list(discovery.get("missingNames") or []),
                    "conflicts": list(discovery.get("conflicts") or []),
                    "paths": list(discovery.get("paths") or []),
                }
            )
        mcp_steps = []
        for server in component_policy["mcpServers"]:
            discovery = dict(discovered_mcp.get(server.id) or {})
            mcp_steps.append(
                {
                    **server.model_dump(mode="json"),
                    "action": str(discovery.get("action") or "install"),
                    "state": str(discovery.get("state") or "missing"),
                    "installedVersion": str(discovery.get("installedVersion") or ""),
                    "availableVersion": str(discovery.get("availableVersion") or ""),
                }
            )

        setup_projection: dict[str, Any] | None = None
        blocking_reasons = list(component_policy["blockingReasons"])
        if manifest.setupAdapter == "godot_v1":
            setup_projection = self.plugin_setup(manifest.id, probe=True)
            if not bool((setup_projection.get("status") or {}).get("readyForInstall")):
                blocking_reasons.extend(
                    f"godot_setup_{item}"
                    for item in list((setup_projection.get("status") or {}).get("blockingReasons") or [])
                )
        deferred_reasons: list[str] = []
        unavailable_skill_ids = {
            item.id for item in component_policy["skills"]
            if not skills_cli_available
        }
        conflicting_skill_ids = {
            str(item.get("id") or "")
            for item in skill_steps
            if item.get("blockedReason") == "skill_name_conflict"
            and str(item.get("id") or "")
        }
        deferred_skill_ids = unavailable_skill_ids | conflicting_skill_ids
        if unavailable_skill_ids:
            deferred_reasons.append("skills_cli_unavailable")
        if conflicting_skill_ids:
            deferred_reasons.append("skill_name_conflict")
        if deferred_skill_ids and not component_policy["cliProfiles"] and not component_policy["mcpServers"]:
            blocking_reasons.extend(deferred_reasons)
        selected_mcp_names = {item.serverName for item in component_policy["mcpServers"]}
        if any(item.get("serverName") in selected_mcp_names for item in machine_discovery.get("ordinaryMcp") or []):
            blocking_reasons.append("ordinary_mcp_name_conflict")
        installable = bool(component_policy["installable"] and not blocking_reasons)
        selected_component_ids = set(component_policy["activeComponentIds"]) - deferred_skill_ids
        skipped_component_ids = sorted(
            {
                *component_policy["skippedComponentIds"],
                *deferred_skill_ids,
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
                "setup": setup_projection,
                "cli": cli_steps,
                "skills": skill_steps,
                "mcp": mcp_steps,
                "uiAdapters": [item.model_dump(mode="json") for item in manifest.uiAdapters],
                "providerAdapters": [item.model_dump(mode="json") for item in manifest.providerAdapters],
                "health": list(manifest.governance.healthChecks),
            },
            "componentPolicy": {
                "mode": "setup_gated_cli_mcp" if manifest.setupAdapter == "godot_v1" else "official_cli_first",
                "transport": component_policy["transport"],
                "installable": installable,
                "blockingReasons": blocking_reasons,
                "degradedReasons": deferred_reasons,
                "selectedComponentIds": sorted(selected_component_ids),
                "skippedComponentIds": skipped_component_ids,
            },
            "machineDiscovery": machine_discovery,
            "installable": installable,
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
        if not dry_run and not bool(plan.get("installable")):
            raise PluginManagerError(
                "当前平台没有可安装的官方插件组件：" + "; ".join(plan["componentPolicy"]["blockingReasons"]),
                code="plugin_components_unavailable",
                status_code=409,
            )
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

    def _append_component_job_step(
        self,
        job_id: str,
        *,
        component_type: str,
        component_id: str,
        state: str,
        action: str = "install",
    ) -> None:
        self._append_job_step(
            job_id,
            "component",
            state,
            {
                "componentType": component_type,
                "componentId": component_id,
                "action": action,
            },
        )

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
        plan = _loads(item.get("plan_json"), {})
        projected_steps = [
            {
                "ordinal": step["ordinal"],
                "type": step["step_type"],
                "state": step["state"],
                "details": _loads(step["details_json"], {}),
                "createdAt": step["created_at"],
                "finishedAt": step["finished_at"],
            }
            for step in steps
        ]
        plan_steps = dict(plan.get("steps") or {})
        total_components = sum(
            len(list(plan_steps.get(key) or []))
            for key in ("cli", "skills", "mcp", "uiAdapters", "providerAdapters")
        )
        completed_components: set[str] = set()
        active_components: dict[str, dict[str, Any]] = {}
        last_completed_component: dict[str, Any] | None = None
        for step in projected_steps:
            if step["type"] != "component":
                continue
            details = dict(step.get("details") or {})
            component_key = f"{details.get('componentType', '')}:{details.get('componentId', '')}"
            if component_key == ":":
                continue
            if step["state"] == "running":
                active_components[component_key] = details
            elif step["state"] == "completed":
                completed_components.add(component_key)
                active_components.pop(component_key, None)
                last_completed_component = details
            elif step["state"] == "failed":
                active_components[component_key] = details
        current_component = list(active_components.values())[-1] if active_components else None
        return {
            "jobId": item["id"],
            "pluginId": item["plugin_id"],
            "action": item["action"],
            "state": item["state"],
            "dryRun": bool(item["dry_run"]),
            "approvalRequired": bool(item["approval_required"]),
            "approved": bool(item["approved"]),
            "plan": plan,
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
            "steps": projected_steps,
            "progress": {
                "phase": item["state"],
                "completedComponents": min(len(completed_components), total_components),
                "totalComponents": total_components,
                "currentComponent": current_component,
                "lastCompletedComponent": last_completed_component,
            },
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
            component_policy = self._component_policy(manifest)
            selected_cli_profiles = list(component_policy["cliProfiles"])
            skill_plan_steps = {
                str(item.get("id") or ""): dict(item)
                for item in list((current_plan.get("steps") or {}).get("skills") or [])
            }
            selected_skills = [
                skill
                for skill in component_policy["skills"]
                if (skill_plan_steps.get(skill.id) or {}).get("supported") is not False
            ]
            selected_mcp_servers = list(component_policy["mcpServers"])
            cli_plan_steps = {
                str(item.get("componentId") or ""): dict(item)
                for item in list((current_plan.get("steps") or {}).get("cli") or [])
            }
            mcp_plan_steps = {
                str(item.get("id") or ""): dict(item)
                for item in list((current_plan.get("steps") or {}).get("mcp") or [])
            }
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
            current_component: dict[str, str] | None = None
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
                    external=any(profile.ownership == "external" for profile in selected_cli_profiles),
                )
                profile_results: list[tuple[CliProfile, dict[str, Any], str, dict[str, str]]] = []
                capability_sync_results: list[dict[str, Any]] = []
                for profile in selected_cli_profiles:
                    step = dict(cli_plan_steps.get(profile.id) or {})
                    action = str(step.get("action") or "install")
                    current_component = {
                        "componentType": "cli",
                        "componentId": profile.id,
                        "action": action,
                    }
                    self._append_component_job_step(
                        job_id,
                        component_type="cli",
                        component_id=profile.id,
                        state="running",
                        action=action,
                    )
                    detected_commands = {
                        str(name): str(path)
                        for name, path in dict(step.get("detectedCommands") or {}).items()
                        if str(name).strip() and str(path).strip()
                    }
                    if action in {"adopt", "keep"}:
                        version_spec = profile.version
                        detected_executable = str(detected_commands.get(profile.commands[0]) or "").strip()
                        if detected_executable:
                            version_argv = list(version_spec.argv)
                            if version_argv:
                                version_argv[0] = detected_executable
                            version_spec = version_spec.model_copy(update={"argv": version_argv})
                        result = await asyncio.to_thread(
                            self._execute_spec,
                            manifest,
                            version_spec,
                            env_overlay=self._setup_environment(manifest),
                        )
                    elif profile.ownership == "external":
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
                            env_overlay=self._setup_environment(manifest, plugin_root=staging),
                        )
                    if result["returnCode"] != 0:
                        raise PluginManagerError(
                            f"CLI 安装失败：{profile.id}: {result['stderrTail'] or result['stdoutTail']}",
                            code="cli_install_failed",
                        )
                    if action == "install" and profile.ownership == "external":
                        detected_commands = self._discover_cli_commands(profile)
                        if not detected_commands:
                            raise PluginManagerError(
                                f"系统安装器已结束，但尚未能定位 CLI：{profile.commands[0]}",
                                code="external_cli_reconciliation_required",
                            )
                    profile_results.append((profile, result, action, detected_commands))
                    self._append_component_job_step(
                        job_id,
                        component_type="cli",
                        component_id=profile.id,
                        state="completed",
                        action=action,
                    )
                    current_component = None

                self._set_job_state(job_id, "validating", step_type="pre_commit_validation")
                for profile, _, action, _ in profile_results:
                    if profile.capabilitySync is None:
                        continue
                    target_root = (
                        staging
                        if profile.ownership == "managed" and action in {"install", "update"}
                        else root
                    )
                    sync_result = await asyncio.to_thread(
                        self._sync_cli_profile_capabilities,
                        manifest,
                        profile,
                        plugin_root=target_root,
                        previous_root=root if target_root != root else None,
                    )
                    if sync_result:
                        capability_sync_results.append({"profileId": profile.id, **sync_result})
                        self._append_job_step(
                            job_id,
                            "capability_sync",
                            "validated",
                            {
                                "componentId": profile.id,
                                "version": sync_result.get("candidateVersion"),
                                "actionCount": sync_result.get("actionCount"),
                                "classification": sync_result.get("classification"),
                                "digest": sync_result.get("candidateDigest"),
                            },
                        )
                self._set_job_state(job_id, "committing", step_type="atomic_commit")
                if any(
                    profile.ownership == "managed" and action in {"install", "update"}
                    for profile, _, action, _ in profile_results
                ):
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if backup.exists():
                        shutil.rmtree(backup)
                    if root.exists():
                        root.replace(backup)
                    staging.replace(root)

                    for profile, _, action, _ in profile_results:
                        if (
                            manifest.setupAdapter != "godot_v1"
                            or profile.ownership != "managed"
                            or action not in {"install", "update"}
                        ):
                            continue
                        self._append_job_step(
                            job_id,
                            "cli_relink",
                            "running",
                            {"componentId": profile.id},
                        )
                        relink_result = await asyncio.to_thread(
                            self._execute_spec,
                            manifest,
                            profile.install,
                            plugin_root=root,
                            env_overlay=self._setup_environment(manifest, plugin_root=root),
                        )
                        if relink_result["returnCode"] != 0:
                            raise PluginManagerError(
                                f"CLI 原子切换后重绑定失败：{profile.id}: "
                                f"{relink_result['stderrTail'] or relink_result['stdoutTail']}",
                                code="cli_relink_failed",
                            )
                        self._append_job_step(
                            job_id,
                            "cli_relink",
                            "completed",
                            {"componentId": profile.id},
                        )

                for profile, result, action, detected_commands in profile_results:
                    commands_for_registration = detected_commands or None
                    created_components.append(
                        self._register_cli_component(
                            manifest,
                            profile,
                            result,
                            adopted_commands=commands_for_registration,
                            adopted=action == "adopt",
                        )
                    )
                    if action in {"install", "update"}:
                        created_components.extend(self._ensure_cli_shims(manifest, profile))
                skill_update_names = sorted(
                    {
                        str(name).strip()
                        for skill in selected_skills
                        if str((skill_plan_steps.get(skill.id) or {}).get("action") or "install") == "update"
                        for name in (
                            list(skill.skillNames)
                            or list((skill_plan_steps.get(skill.id) or {}).get("detectedNames") or [])
                        )
                        if str(name).strip()
                    }
                )
                if skill_update_names:
                    skill_backup = root.parent / ".staging" / f"{job_id}.skills.previous"
                    snapshot["skillBackup"] = self._snapshot_skill_state(
                        skill_update_names,
                        backup_root=skill_backup,
                    )
                    self._persist_job_snapshot(job_id, snapshot)
                    self._append_job_step(
                        job_id,
                        "skill_snapshot",
                        "completed",
                        {"count": len(skill_update_names)},
                    )
                for skill in selected_skills:
                    skill_step = dict(skill_plan_steps.get(skill.id) or {})
                    action = str(skill_step.get("action") or "install")
                    current_component = {
                        "componentType": "skill",
                        "componentId": skill.id,
                        "action": action,
                    }
                    self._append_component_job_step(
                        job_id,
                        component_type="skill",
                        component_id=skill.id,
                        state="running",
                        action=action,
                    )
                    created_components.extend(
                        await asyncio.to_thread(
                            self._install_skill_component,
                            manifest,
                            skill.model_dump(mode="json"),
                            action=action,
                        )
                    )
                    self._append_component_job_step(
                        job_id,
                        component_type="skill",
                        component_id=skill.id,
                        state="completed",
                        action=action,
                    )
                    current_component = None
                for server in selected_mcp_servers:
                    mcp_step = dict(mcp_plan_steps.get(server.id) or {})
                    action = str(mcp_step.get("action") or "install")
                    current_component = {
                        "componentType": "mcp",
                        "componentId": server.id,
                        "action": action,
                    }
                    self._append_component_job_step(
                        job_id,
                        component_type="mcp",
                        component_id=server.id,
                        state="running",
                        action=action,
                    )
                    created_components.extend(self._install_mcp_components(manifest, [server]))
                    self._append_component_job_step(
                        job_id,
                        component_type="mcp",
                        component_id=server.id,
                        state="completed",
                        action=action,
                    )
                    current_component = None
                for adapter in manifest.uiAdapters:
                    current_component = {
                        "componentType": "ui_adapter",
                        "componentId": adapter.id,
                        "action": "install",
                    }
                    self._append_component_job_step(
                        job_id,
                        component_type="ui_adapter",
                        component_id=adapter.id,
                        state="running",
                    )
                    created_components.append(
                        self._register_component(
                            manifest.id,
                            adapter.id,
                            "ui_adapter",
                            source_url=manifest.officialLinks.documentation,
                            metadata=adapter.model_dump(mode="json"),
                        )
                    )
                    self._append_component_job_step(
                        job_id,
                        component_type="ui_adapter",
                        component_id=adapter.id,
                        state="completed",
                    )
                    current_component = None
                for adapter in manifest.providerAdapters:
                    current_component = {
                        "componentType": "provider_adapter",
                        "componentId": adapter.id,
                        "action": "install",
                    }
                    self._append_component_job_step(
                        job_id,
                        component_type="provider_adapter",
                        component_id=adapter.id,
                        state="running",
                    )
                    created_components.append(
                        self._register_component(
                            manifest.id,
                            adapter.id,
                            "provider_adapter",
                            source_url=manifest.officialLinks.documentation,
                            metadata=adapter.model_dump(mode="json"),
                        )
                    )
                    self._append_component_job_step(
                        job_id,
                        component_type="provider_adapter",
                        component_id=adapter.id,
                        state="completed",
                    )
                    current_component = None

                health = await self.doctor(manifest.id, persist=False, sync_capabilities=False)
                external = any(
                    profile.ownership == "external" or action in {"adopt", "keep"}
                    for profile, _, action, _ in profile_results
                )
                install_state = "installed" if health["ok"] or not selected_cli_profiles else "degraded"
                receipt = {
                    "manifestDigest": self._manifest_digest(manifest),
                    "catalogRevision": plugin_catalog_service.load().revision,
                    "components": created_components,
                    "capabilitySnapshots": [
                        {
                            "profileId": result.get("profileId"),
                            "version": result.get("candidateVersion"),
                            "digest": result.get("candidateDigest"),
                            "actionCount": result.get("actionCount"),
                            "classification": result.get("classification"),
                        }
                        for result in capability_sync_results
                    ],
                    "committedAt": utc_now_iso(),
                }
                self._upsert_installation(manifest, state=install_state, health=health, external=external, receipt=receipt)
                result = {
                    "ok": True,
                    "state": install_state,
                    "components": created_components,
                    "health": health,
                    "receipt": receipt,
                }
                self._finish_job(job_id, state="ready", result=result)
                self._append_job_step(job_id, "complete", "ready", {"state": install_state})
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                self._cleanup_skill_snapshot(snapshot.get("skillBackup"))
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                self._event(manifest.id, "install_completed", "ok", job_id=job_id, details=result)
                self._refresh_extensions()
                return self.get_install_job(job_id)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if current_component is not None:
                    self._append_component_job_step(
                        job_id,
                        component_type=current_component["componentType"],
                        component_id=current_component["componentId"],
                        state="failed",
                        action=current_component["action"],
                    )
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
            creationflags=_background_process_creation_flags(),
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

    @staticmethod
    def _persist_job_snapshot(job_id: str, snapshot: dict[str, Any]) -> None:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE plugin_install_jobs SET snapshot_json=?, updated_at=? WHERE id=?",
                (_json(snapshot), utc_now_iso(), job_id),
            )
            conn.commit()

    def _snapshot_skill_state(self, names: Iterable[str], *, backup_root: Path) -> dict[str, Any]:
        selected = sorted({str(name).strip() for name in names if str(name).strip()})
        inventory = self._skills_cli_inventory(force=True)
        if not inventory.get("ok"):
            raise PluginManagerError(
                str(inventory.get("error") or "skills CLI inventory failed"),
                code="skills_cli_unavailable",
                status_code=503,
            )
        inventory_by_name = {
            str(item.get("name") or ""): dict(item)
            for item in list(inventory.get("items") or [])
            if str(item.get("name") or "").strip()
        }
        skills_root = AGENT_SKILLS_ROOT.expanduser().absolute()
        if backup_root.exists():
            shutil.rmtree(backup_root)
        backup_root.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, str]] = []
        try:
            for index, name in enumerate(selected):
                source_text = str((inventory_by_name.get(name) or {}).get("path") or "").strip()
                if not source_text:
                    raise PluginManagerError(
                        f"无法定位待更新 Skill：{name}",
                        code="skill_update_source_missing",
                    )
                source = Path(source_text).expanduser().absolute()
                if not source.is_relative_to(skills_root) or not source.is_dir():
                    raise PluginManagerError(
                        f"待更新 Skill 不属于受管全局目录：{name}",
                        code="skill_update_path_unmanaged",
                    )
                backup = backup_root / "skills" / f"{index:03d}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, backup, symlinks=True)
                entries.append({"name": name, "target": str(source), "backup": str(backup)})

            lock_path = AGENT_SKILLS_ROOT.parent / ".skill-lock.json"
            lock_existed = lock_path.is_file()
            lock_backup = backup_root / "skill-lock.json"
            if lock_existed:
                shutil.copy2(lock_path, lock_backup)
            return {
                "backupRoot": str(backup_root),
                "names": selected,
                "entries": entries,
                "lockPath": str(lock_path),
                "lockBackup": str(lock_backup),
                "lockExisted": lock_existed,
            }
        except Exception:
            shutil.rmtree(backup_root, ignore_errors=True)
            raise

    def _restore_skill_snapshot(self, snapshot: Any) -> dict[str, Any]:
        payload = dict(snapshot or {}) if isinstance(snapshot, dict) else {}
        if not payload:
            return {"ok": True, "restored": [], "errors": []}
        restored: list[str] = []
        errors: list[str] = []
        skills_root = AGENT_SKILLS_ROOT.expanduser().absolute()
        for entry in list(payload.get("entries") or []):
            name = str(entry.get("name") or "").strip()
            target = Path(str(entry.get("target") or "")).expanduser().absolute()
            backup = Path(str(entry.get("backup") or "")).expanduser().absolute()
            try:
                if not target.is_relative_to(skills_root) or not backup.is_dir():
                    raise ValueError("snapshot path is outside the managed Skill root")
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(backup, target, symlinks=True)
                restored.append(name)
            except Exception as exc:
                errors.append(f"skill:{name}: {exc}")
        lock_path = Path(str(payload.get("lockPath") or AGENT_SKILLS_ROOT.parent / ".skill-lock.json"))
        lock_backup = Path(str(payload.get("lockBackup") or "")) if payload.get("lockBackup") else None
        try:
            if bool(payload.get("lockExisted")):
                if lock_backup is None or not lock_backup.is_file():
                    raise FileNotFoundError("Skill lock snapshot is missing")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(lock_backup, lock_path)
            elif lock_path.exists():
                lock_path.unlink()
        except Exception as exc:
            errors.append(f"skill lock restore: {exc}")
        self._invalidate_skills_inventory_cache()
        self._cleanup_skill_snapshot(payload)
        return {"ok": not errors, "restored": restored, "errors": errors}

    @staticmethod
    def _cleanup_skill_snapshot(snapshot: Any) -> None:
        payload = dict(snapshot or {}) if isinstance(snapshot, dict) else {}
        backup_text = str(payload.get("backupRoot") or "").strip()
        if not backup_text:
            return
        backup = Path(backup_text).expanduser().absolute()
        staging_root = (PLUGIN_MANAGER_ROOT / ".staging").expanduser().absolute()
        if backup.is_relative_to(staging_root):
            shutil.rmtree(backup, ignore_errors=True)

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

                sources = [spec.downloadUrl]
                if re.match(
                    r"^https://github\.com/[^/]+/[^/]+/releases/download/",
                    spec.downloadUrl,
                    re.I,
                ):
                    sources.extend(
                        f"{prefix}{spec.downloadUrl}"
                        for prefix in MANAGED_GITHUB_RELEASE_MIRROR_PREFIXES
                    )
                sources = list(dict.fromkeys(sources))
                deadline = time.monotonic() + spec.timeoutSeconds
                failures: list[str] = []
                for source_url in sources:
                    remaining = deadline - time.monotonic()
                    if remaining < 1:
                        failures.append("global_deadline_exceeded")
                        break
                    try:
                        response = httpx.get(
                            source_url,
                            timeout=max(1.0, min(45.0, remaining)),
                            follow_redirects=True,
                        )
                        response.raise_for_status()
                        actual = hashlib.sha256(response.content).hexdigest()
                        if actual != spec.downloadSha256:
                            raise ValueError(
                                f"download SHA-256 mismatch: expected {spec.downloadSha256}, got {actual}"
                            )
                        payload = response.content
                        if spec.archiveFormat == "zip" and spec.archiveEntry:
                            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                                normalized_entries = {
                                    str(item.filename).replace("\\", "/"): item
                                    for item in archive.infolist()
                                    if not item.is_dir()
                                }
                                archive_info = normalized_entries.get(spec.archiveEntry)
                                if archive_info is None:
                                    raise ValueError(f"archive entry not found: {spec.archiveEntry}")
                                unix_mode = (archive_info.external_attr >> 16) & 0xFFFF
                                if unix_mode and stat.S_ISLNK(unix_mode):
                                    raise ValueError("managed archive entry must not be a symbolic link")
                                if archive_info.file_size > 512 * 1024 * 1024:
                                    raise ValueError("managed archive entry exceeds the 512 MiB safety limit")
                                payload = archive.read(archive_info)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        partial.write_bytes(payload)
                        partial.replace(target)
                        return {
                            "argv": ["managed-download", source_url, str(target)],
                            "returnCode": 0,
                            "stdoutTail": f"verified {actual}; source={source_url}",
                            "stderrTail": "",
                            "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                        }
                    except Exception as source_error:
                        failures.append(
                            f"{source_url}: {type(source_error).__name__}: {source_error}"[:1000]
                        )
                raise RuntimeError(
                    "managed download sources exhausted: " + " | ".join(failures)
                )
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
            search_path = self._refresh_process_cli_path()
            execution_argv = self._resolve_execution_argv(argv, search_path=search_path, manifest=manifest)
            executable_name = Path(execution_argv[0]).stem.lower() if execution_argv else ""
            registries: tuple[str, ...] = (
                SKILLS_CLI_NPM_REGISTRIES
                if executable_name in {"npm", "npx"}
                else ("",)
            )
            deadline = time.monotonic() + spec.timeoutSeconds
            completed = None
            timeout_error: subprocess.TimeoutExpired | None = None
            registry_attempts: list[str] = []
            for index, registry in enumerate(registries):
                remaining = deadline - time.monotonic()
                if remaining < 1:
                    break
                remaining_registries = len(registries) - index
                command_env = {
                    **os.environ,
                    "PATH": f"{self._bin_root()}{os.pathsep}{search_path}",
                    **dict(env_overlay or {}),
                }
                if registry:
                    command_env["npm_config_registry"] = registry
                try:
                    completed = run_windowless(
                        execution_argv,
                        cwd=spec.cwd or None,
                        shell=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=max(1.0, remaining / remaining_registries),
                        env=command_env,
                    )
                    registry_attempts.append(
                        f"{registry or 'default'}:{completed.returncode}"
                    )
                    if completed.returncode == 0:
                        break
                except subprocess.TimeoutExpired as exc:
                    timeout_error = exc
                    registry_attempts.append(f"{registry or 'default'}:timeout")
                    completed = None
                    continue
            if completed is None:
                if timeout_error is not None:
                    raise timeout_error
                raise subprocess.TimeoutExpired(execution_argv, spec.timeoutSeconds)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            return {
                "argv": argv,
                "returnCode": completed.returncode,
                "stdoutTail": stdout[-4000:],
                "stderrTail": (
                    stderr[-3600:]
                    + (f"\nregistryAttempts={','.join(registry_attempts)}" if len(registry_attempts) > 1 else "")
                )[-4000:],
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

    def _resolve_execution_argv(
        self,
        argv: list[str],
        *,
        search_path: str,
        manifest: PluginManifest | None = None,
    ) -> list[str]:
        """Resolve allowlisted launchers without invoking a command shell.

        Windows CreateProcess does not expand ``npm``/``npx`` to their ``.cmd``
        launchers when ``shell=False``.  Resolve only argv[0] through PATH so the
        governed argument vector and manifest allowlist remain unchanged.
        """

        resolved_argv = [str(item) for item in argv]
        if not resolved_argv:
            return resolved_argv
        executable = resolved_argv[0]
        candidate = Path(executable).expanduser()
        resolved_path: Path | None = None
        if candidate.is_file():
            resolved_path = candidate.resolve()
        else:
            effective_search_path = f"{self._bin_root()}{os.pathsep}{search_path}"
            resolved = shutil.which(executable, path=effective_search_path)
            if not resolved and os.name == "nt" and not Path(executable).suffix:
                for suffix in (".cmd", ".exe", ".bat", ".com"):
                    resolved = shutil.which(f"{executable}{suffix}", path=effective_search_path)
                    if resolved:
                        break
            if resolved:
                resolved_path = Path(resolved).resolve()
        if resolved_path is None:
            return resolved_argv
        if manifest is not None:
            managed_argv = self._resolve_managed_cli_shim_argv(
                manifest,
                resolved_path,
                resolved_argv[1:],
                search_path=search_path,
            )
            if managed_argv is not None:
                return managed_argv
        resolved_argv[0] = str(resolved_path)
        return resolved_argv

    def _resolve_managed_batch_launcher(
        self,
        manifest: PluginManifest,
        source: Path,
        *,
        search_path: str,
    ) -> list[str]:
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PluginManagerError(
                "受管 CLI 启动器无法读取",
                code="plugin_cli_launcher_unreadable",
            ) from exc
        programs = [match.group("program").strip() for match in MANAGED_CMD_PROGRAM_RE.finditer(text)]
        targets = [match.group("target").strip() for match in MANAGED_CMD_TARGET_RE.finditer(text)]
        if not programs or not targets:
            raise PluginManagerError(
                "受管 CLI 的 Windows 启动器无法解析为原生入口",
                code="plugin_cli_launcher_unsupported",
            )
        plugin_root = self._plugin_root(manifest.id).resolve()
        target = (source.parent / targets[-1].replace("\\", os.sep)).resolve()
        if not target.is_file() or not target.is_relative_to(plugin_root):
            raise PluginManagerError(
                "受管 CLI 的原生入口超出插件目录或不存在",
                code="plugin_cli_launcher_target_invalid",
            )
        program = programs[-1]
        program_path = Path(program).expanduser()
        if program_path.is_file():
            executable = str(program_path.resolve())
        else:
            local_executable = source.parent / f"{program}.exe"
            resolved = local_executable if local_executable.is_file() else shutil.which(program, path=search_path)
            if not resolved:
                raise PluginManagerError(
                    "受管 CLI 的原生解释器不可用",
                    code="plugin_cli_launcher_runtime_missing",
                )
            executable = str(Path(resolved).resolve())
        return [executable, str(target)]

    def _managed_cli_launcher(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        command: str,
        *,
        search_path: str,
    ) -> tuple[list[str], str] | None:
        if profile.ownership != "managed" or profile.exposure != "agent" or command not in profile.commands:
            return None
        if profile.shimCommand:
            launcher = [str(self._expand_template(manifest, item)) for item in profile.shimCommand]
            return launcher, subprocess.list2cmdline(launcher)
        source = self._plugin_root(manifest.id) / "node_modules" / ".bin" / f"{command}.cmd"
        if not source.exists():
            source = source.with_suffix("")
        if not source.exists():
            return None
        if source.suffix.lower() in {".cmd", ".bat"}:
            launcher = self._resolve_managed_batch_launcher(manifest, source, search_path=search_path)
        else:
            launcher = [str(source.resolve())]
        return launcher, str(source)

    def _resolve_managed_cli_shim_argv(
        self,
        manifest: PluginManifest,
        executable: Path,
        arguments: list[str],
        *,
        search_path: str,
    ) -> list[str] | None:
        if os.path.normcase(str(executable.parent.resolve())) != os.path.normcase(str(self._bin_root().resolve())):
            return None
        command = executable.stem
        profile = next((item for item in manifest.cliProfiles if command in item.commands), None)
        if profile is None:
            return None
        resolved = self._managed_cli_launcher(
            manifest,
            profile,
            command,
            search_path=search_path,
        )
        if resolved is None:
            return None
        launcher, _ = resolved
        return [*launcher, *arguments]

    def _register_cli_component(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        result: dict[str, Any],
        *,
        adopted_commands: dict[str, str] | None = None,
        adopted: bool = False,
    ) -> dict[str, Any]:
        root = self._plugin_root(manifest.id)
        detected = dict(adopted_commands or {})
        probe: dict[str, Any] = {}
        try:
            probe = self._probe_cli_version(
                manifest,
                profile,
                detected_commands=detected,
                plugin_root=root,
            )
        except Exception as exc:
            probe = {"ok": False, "version": "", "error": str(exc).strip() or exc.__class__.__name__}
        installed_version = str(
            probe.get("version")
            or self._catalog_cli_version(profile)
            or manifest.version
        ).strip()
        return self._register_component(
            manifest.id,
            profile.id,
            "cli",
            owned_path=str(root) if profile.ownership == "managed" and not detected else "",
            source_url=manifest.officialLinks.documentation,
            source_version=installed_version,
            ownership="external" if detected else profile.ownership,
            metadata={
                "commands": profile.commands,
                "detectedCommands": detected,
                "adopted": adopted,
                "declaredOwnership": profile.ownership,
                "versionProbe": probe,
                "installResult": _redact(result),
            },
        )

    def _effective_cli_spec(
        self,
        manifest: PluginManifest,
        profile: CliProfile,
        spec: CommandSpec,
    ) -> CommandSpec:
        row = next(
            (
                item
                for item in self._component_rows(manifest.id)
                if str(item.get("component_id") or "") == profile.id
            ),
            None,
        )
        metadata = _loads((row or {}).get("metadata_json"), {})
        detected_commands = dict(metadata.get("detectedCommands") or {})
        executable = str(detected_commands.get(profile.commands[0]) or "").strip()
        if not executable:
            return spec
        argv = list(spec.argv)
        if argv:
            first = str(argv[0])
            if first in profile.commands or "{pluginBin}" in first or Path(first).stem.lower() == profile.commands[0].lower():
                argv[0] = executable
        return spec.model_copy(update={"argv": argv})

    def _ensure_cli_shims(self, manifest: PluginManifest, profile: CliProfile) -> list[dict[str, Any]]:
        if profile.ownership != "managed" or profile.exposure != "agent":
            return []
        rows = []
        for command in profile.commands:
            resolved = self._managed_cli_launcher(
                manifest,
                profile,
                command,
                search_path=self._cli_search_path(),
            )
            if resolved is None:
                continue
            launcher, source_description = resolved
            command_line = subprocess.list2cmdline(launcher)
            shim = self._bin_root() / f"{command}.cmd"
            shim.write_text(f"@echo off\r\n{command_line} %*\r\n", encoding="utf-8")
            rows.append(
                self._register_component(
                    manifest.id,
                    f"{profile.id}:shim:{command}",
                    "cli",
                    owned_path=str(shim),
                    source_url=manifest.officialLinks.documentation,
                    source_version=self._catalog_cli_version(profile) or manifest.version,
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
                completed = run_windowless(
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

    @staticmethod
    def _skill_names_from_source(source_root: Path) -> list[str]:
        names: list[str] = []
        skill_files = [source_root] if source_root.is_file() else sorted(source_root.rglob("SKILL.md"))
        for skill_file in skill_files:
            if skill_file.is_dir() or skill_file.name.lower() != "skill.md":
                continue
            try:
                text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            frontmatter = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
            match = re.search(r"(?m)^name:\s*['\"]?([^'\"\r\n]+)", frontmatter)
            name = str(match.group(1) if match else skill_file.parent.name).strip()
            if name and name not in names:
                names.append(name)
        return names

    def _install_skill_component(
        self,
        manifest: PluginManifest,
        skill: dict[str, Any],
        *,
        action: str = "install",
    ) -> list[dict[str, Any]]:
        source_kind = str(skill.get("sourceKind") or "git").strip().lower()
        temp_root: Path | None = None
        if source_kind == "managed_cli":
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
                raise PluginManagerError("Skill 来源未绑定到受管 CLI 组件", code="skill_source_component_invalid")
            plugin_root = self._plugin_root(manifest.id).resolve()
            source_root = (plugin_root / str(skill["path"])).resolve()
            if plugin_root != source_root and plugin_root not in source_root.parents:
                raise PluginManagerError("受管 CLI Skill 路径越界", code="skill_path_invalid")
        else:
            temp_root = Path(tempfile.mkdtemp(prefix=f"v8-plugin-{manifest.id}-"))
            try:
                repo_root = temp_root / "repo"
                revision = str(skill["revision"])
                self._run_skill_git_step(["git", "init", str(repo_root)])
                self._run_skill_git_step(
                    ["git", "-C", str(repo_root), "remote", "add", "origin", str(skill["repository"])]
                )
                for attempt in range(3):
                    try:
                        self._run_skill_git_step(
                            ["git", "-C", str(repo_root), "fetch", "--depth", "1", "--no-tags", "origin", revision]
                        )
                        break
                    except PluginManagerError:
                        if attempt == 2:
                            raise
                        time.sleep(0.5 * (attempt + 1))
                self._run_skill_git_step(["git", "-C", str(repo_root), "checkout", "--detach", "FETCH_HEAD"])
                verified = self._run_skill_git_step(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
                if str(verified.get("stdoutTail") or "").strip().lower() != revision.lower():
                    raise PluginManagerError("Skill Git 提交校验失败", code="skill_revision_mismatch")
                source_root = (repo_root / str(skill["path"])).resolve()
                if source_root != repo_root.resolve() and repo_root.resolve() not in source_root.parents:
                    raise PluginManagerError("受审 Skill 路径越界", code="skill_path_invalid")
            except Exception:
                shutil.rmtree(temp_root, ignore_errors=True)
                raise

        try:
            if not source_root.exists():
                raise PluginManagerError("受审 Skill 路径不存在", code="skill_path_invalid")
            source_names = self._skill_names_from_source(source_root)
            declared_names = [str(item) for item in list(skill.get("skillNames") or []) if str(item).strip()]
            if declared_names and not set(declared_names).issubset(source_names):
                raise PluginManagerError("签名目录中的 Skill 名称与受审来源不一致", code="skill_name_mismatch")
            selected_names = declared_names or source_names
            if not selected_names:
                raise PluginManagerError("受审来源中没有可安装的 SKILL.md", code="skill_source_empty")

            before = self._skills_cli_inventory(force=True)
            if not before.get("ok"):
                raise PluginManagerError(
                    str(before.get("error") or "skills CLI inventory failed"),
                    code="skills_cli_unavailable",
                    status_code=503,
                )
            inventory_by_name = {
                str(item.get("name") or ""): item
                for item in list(before.get("items") or [])
                if str(item.get("name") or "").strip()
            }
            lock_entries = dict(before.get("lockEntries") or {})
            registered_component = next(
                (
                    row
                    for row in self._component_rows(manifest.id)
                    if str(row.get("component_id") or "") == str(skill["id"])
                    and str(row.get("component_type") or "") == "skill"
                ),
                None,
            )
            registered_metadata = _loads(
                (registered_component or {}).get("metadata_json"),
                {},
            )
            registered_names = {
                str(name).strip()
                for name in list(registered_metadata.get("skillNames") or [])
                if str(name).strip()
            }
            registered_paths = {
                os.path.normcase(os.path.abspath(str(path)))
                for path in list(registered_metadata.get("skillPaths") or [])
                if str(path).strip()
            }
            registered_source_matches = bool(
                registered_component
                and str(registered_component.get("ownership") or "") == "skills_cli"
                and self._repository_identity(str(registered_component.get("source_url") or ""))
                == self._repository_identity(str(skill.get("repository") or ""))
            )
            adopted_names: list[str] = []
            adopted_paths: list[str] = []
            conflicts: list[str] = []
            for name in selected_names:
                installed = inventory_by_name.get(name)
                if not installed:
                    continue
                installed_path = str(installed.get("path") or "").strip()
                receipt_owns_installed_path = bool(
                    action == "update"
                    and registered_source_matches
                    and name in registered_names
                    and installed_path
                    and os.path.normcase(os.path.abspath(installed_path)) in registered_paths
                )
                if (
                    source_kind == "git"
                    and self._skill_source_matches(lock_entries.get(name) or {}, skill)
                ) or receipt_owns_installed_path:
                    adopted_names.append(name)
                    if installed_path:
                        adopted_paths.append(installed_path)
                else:
                    conflicts.append(name)
            if conflicts:
                raise PluginManagerError(
                    "同名 Skill 已由其他来源安装：" + ", ".join(conflicts),
                    code="skill_name_conflict",
                    status_code=409,
                )

            missing_names = [name for name in selected_names if name not in adopted_names]
            install_names = selected_names if action == "update" else missing_names
            if install_names:
                source_locator = str(source_root)
                install_result = self._run_skills_cli(
                    [
                        "add",
                        source_locator,
                        "--global",
                        "--agent",
                        "codex",
                        "--copy",
                        "--yes",
                        "--skill",
                        *install_names,
                    ],
                    timeout_seconds=600,
                )
                if install_result["returnCode"] != 0:
                    raise PluginManagerError(
                        str(install_result.get("stderrTail") or install_result.get("stdoutTail") or "skills CLI failed")[-2000:],
                        code="skill_install_failed",
                    )
                self._invalidate_skills_inventory_cache()
            after = self._skills_cli_inventory(force=True)
            after_by_name = {
                str(item.get("name") or ""): item
                for item in list(after.get("items") or [])
                if str(item.get("name") or "").strip()
            }
            missing_after = [name for name in selected_names if name not in after_by_name]
            if missing_after:
                raise PluginManagerError(
                    "skills CLI 未登记预期 Skill：" + ", ".join(missing_after),
                    code="skill_install_incomplete",
                )
            managed_paths = [str(after_by_name[name].get("path") or "") for name in install_names]
            skill_paths = [
                str(after_by_name[name].get("path") or "")
                for name in selected_names
                if str(after_by_name[name].get("path") or "").strip()
            ]
            ownership = "skills_cli" if install_names else "external"
            return [
                self._register_component(
                    manifest.id,
                    str(skill["id"]),
                    "skill",
                    owned_path=managed_paths[0] if managed_paths else adopted_paths[0] if adopted_paths else "",
                    source_url=str(skill["repository"]),
                    source_version=str(skill["revision"]),
                    ownership=ownership,
                    metadata={
                        "officialOrganization": skill["officialOrganization"],
                        "sourceKind": source_kind,
                        "sourceComponentId": str(skill.get("sourceComponentId") or ""),
                        "sourcePath": skill["path"],
                        "sourceTrust": str(skill.get("sourceTrust") or "official"),
                        "sourceLicense": str(skill.get("sourceLicense") or ""),
                        "reviewNote": str(skill.get("reviewNote") or ""),
                        "installer": SKILLS_CLI_PACKAGE,
                        "skillNames": selected_names,
                        "skillPaths": skill_paths,
                        "managedSkillNames": install_names,
                        "adoptedSkillNames": [name for name in adopted_names if name not in install_names],
                        "installAction": action,
                    },
                )
            ]
        finally:
            if temp_root is not None:
                shutil.rmtree(temp_root, ignore_errors=True)

    def _install_mcp_components(
        self,
        manifest: PluginManifest,
        selected_servers: Iterable[Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = storage.get_mcp_config()
        servers = dict(payload.get("mcpServers") or {})
        rows = []
        effective_servers = (
            list(selected_servers)
            if selected_servers is not None
            else list(self._component_policy(manifest)["mcpServers"])
        )
        for server in effective_servers:
            if server.serverName in servers and str((servers[server.serverName] or {}).get("x-v8-plugin-owner") or "") != manifest.id:
                raise PluginManagerError(
                    f"MCP server 名称已被用户配置占用：{server.serverName}",
                    code="mcp_server_conflict",
                    status_code=409,
                )
            config = self._expand_template(manifest, server.configTemplate)
            config["disabled"] = manifest.setupAdapter != "godot_v1"
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
                    source_version=str(server.revision or manifest.version),
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
                if profile.exposure != "agent":
                    continue
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
        self._invalidate_machine_discovery_cache(plugin_id)
        return {
            "id": component_id,
            "type": component_type,
            "path": owned_path or None,
            "sha256": digest or None,
            "ownership": ownership,
            "metadata": dict(metadata or {}),
        }

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
            for item in compile_plugin_requirements(
                manifest,
                component_ids=self._component_policy(manifest)["activeComponentIds"],
            )
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
        self._invalidate_machine_discovery_cache(manifest.id)
        if previous_digest and previous_digest != current_digest:
            self._invalidate_grant_cache()

    def _finish_job(self, job_id: str, *, state: str, result: dict[str, Any], error: str = "") -> None:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE plugin_install_jobs SET state=?, result_json=?, error_message=?, finished_at=?, updated_at=? WHERE id=?",
                (state, _json(_redact(result)), error or None, utc_now_iso(), utc_now_iso(), job_id),
            )
            conn.commit()

    def _remove_skills_cli_names(self, names: Iterable[str]) -> dict[str, Any]:
        selected = sorted({str(name).strip() for name in names if str(name).strip()})
        if not selected:
            return {"ok": True, "removed": [], "error": ""}
        result = self._run_skills_cli(
            ["remove", *selected, "--global", "--yes"],
            timeout_seconds=300,
        )
        self._invalidate_skills_inventory_cache()
        return {
            "ok": result["returnCode"] == 0,
            "removed": selected if result["returnCode"] == 0 else [],
            "error": str(result.get("stderrTail") or result.get("stdoutTail") or "")[-2000:],
        }

    def _rollback(self, manifest: PluginManifest, snapshot: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, Any]:
        removed = []
        errors = []
        snapshotted_skill_names = {
            str(name).strip()
            for name in list((snapshot.get("skillBackup") or {}).get("names") or [])
            if str(name).strip()
        }
        for component in reversed(components):
            metadata = dict(component.get("metadata") or {})
            managed_skill_names = list(metadata.get("managedSkillNames") or [])
            if component.get("type") == "skill" and managed_skill_names:
                removable_names = [name for name in managed_skill_names if name not in snapshotted_skill_names]
                removal = self._remove_skills_cli_names(removable_names)
                if removal["ok"]:
                    removed.extend(f"skill:{name}" for name in removal["removed"])
                else:
                    errors.append(f"skills CLI rollback: {removal['error']}")
                continue
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
        skill_restore = self._restore_skill_snapshot(snapshot.get("skillBackup"))
        if not skill_restore["ok"]:
            errors.extend(skill_restore["errors"])
        removed.extend(f"skill-restored:{name}" for name in skill_restore["restored"])
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
        requirements = compile_plugin_requirements(
            manifest,
            component_ids=self._requirement_component_ids(manifest),
        )
        by_component: dict[str, list[PluginConfigRequirement]] = {}
        for requirement in requirements:
            by_component.setdefault(str(requirement.componentId or ""), []).append(requirement)

        manager_config = storage.get_plugin_manager_config()
        all_plugin_values = dict(manager_config.get("pluginConfigValues") or {})
        plugin_values = dict(all_plugin_values.get(manifest.id) or {})
        active_component_ids = self._requirement_component_ids(manifest)
        selected_mcp_servers = [item for item in manifest.mcpServers if item.id in active_component_ids]
        mcp_component_ids = {item.id for item in selected_mcp_servers}
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
        for server in selected_mcp_servers:
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

    async def doctor(
        self,
        plugin_id: str,
        *,
        persist: bool = True,
        sync_capabilities: bool = True,
    ) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        active_component_ids = self._active_installed_component_ids(manifest)
        checks = []
        for profile in manifest.cliProfiles:
            if profile.id not in active_component_ids:
                continue
            if sync_capabilities and profile.capabilitySync is not None:
                try:
                    capability_result = await asyncio.to_thread(
                        self._sync_cli_profile_capabilities,
                        manifest,
                        profile,
                    )
                    checks.append(
                        {
                            "componentId": profile.id,
                            "kind": "cli-capability-schema",
                            "ok": bool((capability_result or {}).get("accepted")),
                            "summary": (
                                f"{(capability_result or {}).get('actionCount', 0)} actions; "
                                f"{(capability_result or {}).get('classification', 'unknown')}"
                            ),
                            "version": (capability_result or {}).get("candidateVersion"),
                            "digest": (capability_result or {}).get("candidateDigest"),
                        }
                    )
                except PluginManagerError as exc:
                    checks.append(
                        {
                            "componentId": profile.id,
                            "kind": "cli-capability-schema",
                            "ok": False,
                            "summary": str(exc),
                            "errorCode": exc.code,
                        }
                    )
            version_spec = self._effective_cli_spec(manifest, profile, profile.version)
            result = await asyncio.to_thread(
                self._execute_spec,
                manifest,
                version_spec,
                env_overlay=self._setup_environment(manifest),
            )
            checks.append(
                {
                    "componentId": profile.id,
                    "kind": "cli-version",
                    "ok": result["returnCode"] == 0,
                    "summary": (result["stdoutTail"] or result["stderrTail"])[-500:],
                }
            )
        component_rows = {
            str(item.get("component_id") or ""): item
            for item in self._component_rows(manifest.id)
        }
        for skill in manifest.skills:
            if skill.id not in active_component_ids:
                continue
            row = component_rows.get(skill.id) or {}
            owned_path = str(row.get("owned_path") or "").strip()
            metadata = _loads(row.get("metadata_json"), {})
            skill_paths = [str(item) for item in list(metadata.get("skillPaths") or []) if str(item).strip()]
            if not skill_paths and owned_path:
                skill_paths = [owned_path]
            present = bool(skill_paths) and all((Path(item) / "SKILL.md").is_file() for item in skill_paths)
            checks.append(
                {
                    "componentId": skill.id,
                    "kind": "skill-file",
                    "ok": present,
                    "summary": (
                        f"{len(skill_paths)} Skill package(s) are installed"
                        if present
                        else "One or more SKILL.md files are missing"
                    ),
                }
            )
        selected_mcp_servers = [server for server in manifest.mcpServers if server.id in active_component_ids]
        if selected_mcp_servers:
            payload = storage.get_mcp_config()
            servers = dict(payload.get("mcpServers") or {})
            try:
                from runtimes.extensions.mcp.client import mcp_manager

                runtime_status = dict(mcp_manager.get_status() or {})
            except Exception:
                runtime_status = {}
            for server in selected_mcp_servers:
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
        if any(item.id in active_component_ids for item in manifest.uiAdapters):
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
        referenced_skill_names: set[str] = set()
        for other in self._component_rows():
            if str(other.get("plugin_id") or "") == manifest.id or other.get("component_type") != "skill":
                continue
            referenced_skill_names.update(
                str(name)
                for name in list(_loads(other.get("metadata_json"), {}).get("skillNames") or [])
                if str(name).strip()
            )
        managed_skill_names: set[str] = set()
        for item in components:
            if item.get("component_type") != "skill" or item.get("ownership") != "skills_cli":
                continue
            managed_skill_names.update(
                str(name)
                for name in list(_loads(item.get("metadata_json"), {}).get("managedSkillNames") or [])
                if str(name).strip() and str(name) not in referenced_skill_names
            )
        if managed_skill_names:
            skill_removal = self._remove_skills_cli_names(managed_skill_names)
            if not skill_removal["ok"] and not force:
                raise PluginManagerError(
                    f"skills CLI 未能安全移除插件 Skill：{skill_removal['error']}",
                    code="skill_uninstall_failed",
                    status_code=409,
                )
            removed.extend(f"skill:{name}" for name in skill_removal["removed"])
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
            conn.execute("DELETE FROM plugin_components WHERE plugin_id=?", (manifest.id,))
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
        self._invalidate_skills_inventory_cache()
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
        delegation_id: str | None = None,
        delegation_depth: int | None = None,
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
        declared = self._grantable_installed_component_ids(manifest)
        requested = [str(item).strip() for item in list(component_ids or []) if str(item).strip()]
        if not requested:
            raise PluginManagerError(
                "插件授权必须明确指定最小组件集合",
                code="grant_components_required",
                status_code=409,
            )
        selected = requested
        if not set(selected).issubset(declared):
            raise PluginManagerError("授权包含未安装、未启用或仅供 V8OS runtime 使用的组件", code="grant_component_invalid")
        if grantee_type == "subagent":
            normalized_delegation_id = str(delegation_id or "").strip()
            try:
                normalized_delegation_depth = int(delegation_depth or 0)
            except (TypeError, ValueError):
                normalized_delegation_depth = 0
            if not normalized_delegation_id or normalized_delegation_depth not in {1, 2}:
                raise PluginManagerError(
                    "子代理授权必须绑定精确 delegationId，且仅允许直接子 Agent 或孙 Agent 两层。",
                    code="delegation_identity_required",
                    status_code=403,
                )
            if not parent_grant_id:
                raise PluginManagerError("子代理授权必须引用父授权", code="parent_grant_required")
            parent = self._grant_row(parent_grant_id)
            if not parent or parent.get("grantee_type") not in {"supervisor", "subagent"}:
                raise PluginManagerError("父授权不存在", code="parent_grant_invalid")
            if parent.get("grantee_type") == "supervisor" and normalized_delegation_depth != 1:
                raise PluginManagerError("Supervisor 授权只能投影给直接子 Agent。", code="grant_depth_invalid", status_code=403)
            if parent.get("grantee_type") == "subagent":
                parent_depth = int(parent.get("delegation_depth") or 0)
                if parent_depth != 1 or normalized_delegation_depth != 2:
                    raise PluginManagerError("插件授权最多传递到孙 Agent，不能继续扩散。", code="grant_transitive_denied", status_code=403)
            if parent.get("plugin_id") != manifest.id or parent.get("session_id") != session_id:
                raise PluginManagerError("子代理授权必须与父授权属于同一插件和会话", code="parent_grant_scope_invalid")
            if str(parent.get("owner_user_id") or "") != owner_user_id:
                raise PluginManagerError("子代理授权 owner 与父授权不一致", code="parent_grant_owner_invalid", status_code=403)
            if scope == "task" and parent.get("scope") == "task" and parent.get("run_id") != run_id:
                raise PluginManagerError("子代理任务授权必须与父授权属于同一 run", code="parent_grant_run_invalid")
            parent_components = set(_loads(parent.get("component_ids_json"), []))
            if not set(selected).issubset(parent_components):
                raise PluginManagerError("子代理授权不得扩大组件范围", code="grant_scope_escalation", status_code=403)
            if normalized_delegation_depth == 2 and set(selected) == parent_components:
                raise PluginManagerError(
                    "孙 Agent 授权必须是父授权的严格组件子集",
                    code="grant_scope_not_strict_subset",
                    status_code=403,
                )
        selected = sorted(set(selected))
        normalized_delegation_id = str(delegation_id or "").strip() or None
        normalized_delegation_depth = int(delegation_depth or 0) if grantee_type == "subagent" else None
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
                  AND COALESCE(delegation_id, '')=COALESCE(?, '')
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
                    normalized_delegation_id,
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
                 manifest_version, manifest_digest, catalog_revision, state, grant_source,
                 delegation_id, delegation_depth)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
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
                    normalized_delegation_id,
                    normalized_delegation_depth,
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
            "delegationId": row.get("delegation_id"),
            "delegationDepth": row.get("delegation_depth"),
            "source": row.get("grant_source") or "user_reference",
        }

    def _grant_with_grantable_components(self, grant: dict[str, Any]) -> dict[str, Any] | None:
        try:
            manifest = self._manifest(str(grant.get("pluginId") or ""))
            grantable = self._grantable_installed_component_ids(manifest)
        except Exception:
            return None
        component_ids = sorted(set(grant.get("componentIds") or []).intersection(grantable))
        if not component_ids:
            return None
        return {**grant, "componentIds": component_ids}

    def active_grants(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        grantee_type: str | None = None,
        grantee_id: str | None = None,
        delegation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if str(grantee_type or "").strip() == "subagent" and (
            not str(grantee_id or "").strip()
            or not str(delegation_id or "").strip()
        ):
            return []
        cache_key = (
            str(session_id or ""),
            str(run_id or ""),
            str(grantee_type or ""),
            str(grantee_id or ""),
            str(delegation_id or ""),
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
        if delegation_id:
            query += " AND delegation_id=?"
            params.append(str(delegation_id))
        query += " ORDER BY created_at"
        with db.get_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        result = [
            filtered
            for row in rows
            if (filtered := self._grant_with_grantable_components(self._grant_payload(dict(row)))) is not None
        ]
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
        return [
            filtered
            for row in rows
            if (filtered := self._grant_with_grantable_components(self._grant_payload(dict(row)))) is not None
        ]

    def revoke_grant(self, grant_id: str) -> dict[str, Any]:
        row = self._grant_row(grant_id)
        if not row:
            raise PluginManagerError("授权不存在", code="grant_not_found", status_code=404)
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute("UPDATE plugin_grants SET revoked_at=?, state='revoked', terminal_reason='explicit_revoke' WHERE id=?", (now, grant_id))
            conn.execute(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM plugin_grants WHERE parent_grant_id=?
                    UNION ALL
                    SELECT child.id FROM plugin_grants child
                    JOIN descendants parent ON child.parent_grant_id=parent.id
                )
                UPDATE plugin_grants
                SET revoked_at=?, state='revoked', terminal_reason='parent_revoked'
                WHERE id IN (SELECT id FROM descendants) AND revoked_at IS NULL
                """,
                (grant_id, now),
            )
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
        delegation_id: str | None = None,
        delegation_depth: int | None = None,
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
        row_delegation_id = str(row.get("delegation_id") or "").strip()
        if row.get("grantee_type") == "subagent":
            if not row_delegation_id or row_delegation_id != str(delegation_id or "").strip():
                raise PluginManagerError("插件授权 delegation 上下文不匹配", code="plugin_grant_delegation_mismatch", status_code=403)
            if int(row.get("delegation_depth") or 0) != int(delegation_depth or 0):
                raise PluginManagerError("插件授权委派深度不匹配", code="plugin_grant_depth_mismatch", status_code=403)
        if row.get("scope") == "task" and str(row.get("run_id") or "") != str(run_id or ""):
            raise PluginManagerError("插件任务授权 run 不匹配", code="plugin_grant_run_mismatch", status_code=403)
        if component_id not in set(_loads(row.get("component_ids_json"), [])):
            raise PluginManagerError("插件组件未获授权", code="plugin_grant_component_denied", status_code=403)
        manifest = self._manifest(plugin_id)
        if component_id not in self._grantable_installed_component_ids(manifest):
            raise PluginManagerError(
                "插件组件未安装、未启用或仅供 V8OS runtime 使用",
                code="plugin_component_not_grantable",
                status_code=409,
            )
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
        delegation_id: str,
        delegation_depth: int,
        parent_agent_id: str | None = None,
        parent_delegation_id: str | None = None,
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
        try:
            normalized_depth = int(delegation_depth)
        except (TypeError, ValueError):
            normalized_depth = 0
        if normalized_depth == 1:
            parent_grants = self.active_grants(
                session_id=session_id,
                run_id=run_id,
                grantee_type="supervisor",
                grantee_id="supervisor",
            )
        elif normalized_depth == 2:
            normalized_parent_agent = str(parent_agent_id or "").strip()
            normalized_parent_delegation = str(parent_delegation_id or "").strip()
            if not normalized_parent_agent or not normalized_parent_delegation:
                raise PluginManagerError(
                    "孙 Agent 插件授权缺少父 Agent 的 delegation 身份。",
                    code="parent_delegation_identity_required",
                    status_code=403,
                )
            parent_grants = self.active_grants(
                session_id=session_id,
                run_id=run_id,
                grantee_type="subagent",
                grantee_id=normalized_parent_agent,
                delegation_id=normalized_parent_delegation,
            )
        else:
            raise PluginManagerError(
                "插件授权只允许投影给直接子 Agent 或孙 Agent。",
                code="grant_depth_invalid",
                status_code=403,
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
            if normalized_depth == 2 and set(selected) == parent_components:
                raise PluginManagerError(
                    f"孙 Agent 请求继承插件 {plugin_id} 的完整组件范围；孙 Agent 授权必须是严格组件子集。",
                    code="grant_scope_not_strict_subset",
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
                delegation_id=str(delegation_id or "").strip(),
                delegation_depth=normalized_depth,
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
        delegation_id: str | None = None,
        delegation_depth: int | None = None,
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
            delegation_id=delegation_id,
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
                delegation_id=delegation_id,
                delegation_depth=delegation_depth,
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
        delegation_id: str | None = None,
    ) -> dict[str, Any]:
        grants = self.active_grants(
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            delegation_id=delegation_id,
        )
        skills: list[dict[str, Any]] = []
        mcp_tools: list[Any] = []
        projected_mcp_names: set[str] = set()
        cli_entries: list[dict[str, Any]] = []
        adapters: list[dict[str, Any]] = []
        provider_adapters: list[dict[str, Any]] = []
        component_rows_by_plugin: dict[str, dict[str, dict[str, Any]]] = {}
        for grant in grants:
            manifest = self._manifest(grant["pluginId"])
            components = set(grant["componentIds"]).intersection(self._grantable_installed_component_ids(manifest))
            component_rows = component_rows_by_plugin.setdefault(
                manifest.id,
                {
                    str(item.get("component_id") or ""): item
                    for item in self._component_rows(manifest.id)
                },
            )
            for skill in manifest.skills:
                if skill.id in components:
                    row = component_rows.get(skill.id) or {}
                    metadata = _loads(row.get("metadata_json"), {})
                    skills.append(
                        {
                            "pluginId": manifest.id,
                            "grantId": grant["grantId"],
                            **skill.model_dump(mode="json"),
                            "installedRoots": list(metadata.get("skillPaths") or []),
                        }
                    )
            for profile in manifest.cliProfiles:
                if profile.id in components and profile.exposure == "agent":
                    # Runtime projection only proves that the exact component is
                    # granted. Action schemas are loaded through plugin_broker on
                    # demand so large CLI inventories do not pollute every turn.
                    cli_entries.append(
                        {
                            "pluginId": manifest.id,
                            "pluginName": manifest.displayName,
                            "grantId": grant["grantId"],
                            "id": profile.id,
                            "command": profile.commands[0],
                        }
                    )
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

    def resolve_privileged_channel(
        self,
        *,
        plugin_references: Iterable[dict[str, Any]] | None,
        session_id: str,
        run_id: str | None,
        grantee_type: str = "supervisor",
        grantee_id: str = "supervisor",
        delegation_id: str | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve the explicit plugin-package lane without replacing Extensions.

        Ordinary Skills and MCP servers remain owned by the Extensions runtime.
        The privileged package lane is activated only when the current request
        explicitly references a catalogued, installed plugin.  The returned
        projection is still grant-backed and component-bounded; installation or
        selection alone never authorizes an invocation.
        """

        requested_components: dict[str, set[str]] = {}
        requested_plugin_ids: list[str] = []
        requested_scopes: dict[str, set[str]] = {}
        for raw_reference in list(plugin_references or []):
            if not isinstance(raw_reference, dict):
                continue
            plugin_id = str(raw_reference.get("pluginId") or raw_reference.get("plugin_id") or "").strip().lower()
            if not plugin_id:
                continue
            if plugin_id not in requested_components:
                requested_plugin_ids.append(plugin_id)
                requested_components[plugin_id] = set()
                requested_scopes[plugin_id] = set()
            requested_components[plugin_id].update(
                str(item).strip()
                for item in list(raw_reference.get("componentIds") or raw_reference.get("component_ids") or [])
                if str(item).strip()
            )
            requested_scopes[plugin_id].add(
                str(raw_reference.get("scope") or "task").strip().lower() or "task"
            )

        empty_projection = {
            "grants": [],
            "skills": [],
            "mcpTools": [],
            "cliProfiles": [],
            "uiAdapters": [],
            "providerAdapters": [],
        }
        if not requested_plugin_ids:
            return {
                "active": False,
                "mode": "extensions_default",
                "prefilterBypassed": False,
                "requestedPluginIds": [],
                "installedPluginIds": [],
                "projectedPluginIds": [],
                "blocked": [],
                "projection": empty_projection,
            }

        installations = self._installation_rows()
        installed_plugin_ids: list[str] = []
        blocked: list[dict[str, Any]] = []
        active_component_ids: dict[str, set[str]] = {}
        for plugin_id in requested_plugin_ids:
            manifest = plugin_catalog_service.get(plugin_id)
            if manifest is None:
                blocked.append({"pluginId": plugin_id, "status": "invalid", "reason": "not_registered"})
                continue
            installation = self._installation_payload(installations.get(plugin_id))
            if not installation["installed"]:
                blocked.append({"pluginId": plugin_id, "status": "not_installed", "reason": "not_installed"})
                continue
            installed_plugin_ids.append(plugin_id)
            installed_components = self._active_installed_component_ids(manifest)
            grantable_components = self._grantable_installed_component_ids(manifest)
            active_component_ids[plugin_id] = grantable_components
            requested = requested_components[plugin_id]
            if not requested:
                blocked.append(
                    {
                        "pluginId": plugin_id,
                        "status": "invalid",
                        "reason": "components_required",
                        "configurationUrl": f"/admin/plugins?plugin={plugin_id}",
                    }
                )
                continue
            runtime_support_components = sorted(requested.intersection(installed_components - grantable_components))
            if runtime_support_components:
                blocked.append(
                    {
                        "pluginId": plugin_id,
                        "status": "invalid",
                        "reason": "runtime_support_not_grantable",
                        "componentIds": runtime_support_components,
                        "configurationUrl": f"/admin/plugins?plugin={plugin_id}",
                    }
                )
            missing_components = sorted(requested - installed_components)
            if missing_components:
                blocked.append(
                    {
                        "pluginId": plugin_id,
                        "status": "invalid",
                        "reason": "component_not_installed",
                        "componentIds": missing_components,
                        "configurationUrl": f"/admin/plugins?plugin={plugin_id}",
                    }
                )

        active = bool(installed_plugin_ids)
        if not active:
            return {
                "active": False,
                "mode": "extensions_default",
                "prefilterBypassed": False,
                "requestedPluginIds": requested_plugin_ids,
                "installedPluginIds": [],
                "projectedPluginIds": [],
                "blocked": blocked,
                "projection": empty_projection,
            }

        readiness_by_plugin: dict[str, dict[str, Any]] = {}
        ready_plugin_ids: set[str] = set()
        for plugin_id in installed_plugin_ids:
            readiness = self.readiness_status(plugin_id)
            readiness_by_plugin[plugin_id] = readiness
            if readiness.get("ready"):
                ready_plugin_ids.add(plugin_id)
                continue
            blocked.append(
                {
                    "pluginId": plugin_id,
                    "status": readiness.get("status") or "invalid",
                    "reason": readiness.get("status") or "plugin_unavailable",
                    "configurationUrl": readiness.get("configurationUrl"),
                }
            )

        full_projection = projection if projection is not None else self.projection_for(
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            delegation_id=delegation_id,
        )
        selected_grants: list[dict[str, Any]] = []
        selected_grant_ids: set[str] = set()
        projected_plugin_ids: set[str] = set()
        for raw_grant in list(full_projection.get("grants") or []):
            grant = dict(raw_grant or {})
            plugin_id = str(grant.get("pluginId") or "").strip().lower()
            if plugin_id not in ready_plugin_ids:
                continue
            requested = requested_components.get(plugin_id) or set()
            allowed = sorted(
                requested.intersection(active_component_ids.get(plugin_id) or set()).intersection(
                    set(grant.get("componentIds") or [])
                )
            )
            if not allowed:
                continue
            grant_id = str(grant.get("grantId") or "").strip()
            if not grant_id:
                continue
            grant["componentIds"] = allowed
            selected_grants.append(grant)
            selected_grant_ids.add(grant_id)
            projected_plugin_ids.add(plugin_id)

        def _component_projection_items(key: str) -> list[dict[str, Any]]:
            selected: list[dict[str, Any]] = []
            for raw_item in list(full_projection.get(key) or []):
                item = dict(raw_item or {})
                plugin_id = str(item.get("pluginId") or "").strip().lower()
                component_id = str(item.get("componentId") or item.get("id") or "").strip()
                grant_id = str(item.get("grantId") or "").strip()
                if (
                    plugin_id in projected_plugin_ids
                    and grant_id in selected_grant_ids
                    and component_id in (requested_components.get(plugin_id) or set())
                ):
                    selected.append(item)
            return selected

        selected_mcp_tools: list[Any] = []
        for tool in list(full_projection.get("mcpTools") or []):
            metadata = dict(getattr(tool, "metadata", None) or {})
            plugin_id = str(metadata.get("plugin_id") or "").strip().lower()
            component_id = str(metadata.get("plugin_component_id") or "").strip()
            grant_id = str(metadata.get("plugin_grant_id") or "").strip()
            if (
                plugin_id in projected_plugin_ids
                and grant_id in selected_grant_ids
                and component_id in (requested_components.get(plugin_id) or set())
            ):
                selected_mcp_tools.append(tool)

        structurally_blocked_plugin_ids = {
            str(item.get("pluginId") or "").strip().lower()
            for item in blocked
            if str(item.get("reason") or "")
            in {"components_required", "component_not_installed", "runtime_support_not_grantable"}
        }
        for plugin_id in sorted(ready_plugin_ids):
            if plugin_id not in projected_plugin_ids and plugin_id not in structurally_blocked_plugin_ids:
                readiness = readiness_by_plugin[plugin_id]
                blocked.append(
                    {
                        "pluginId": plugin_id,
                        "status": "invalid",
                        "reason": "grant_missing",
                        "configurationUrl": readiness.get("configurationUrl"),
                    }
                )

        privileged_projection = {
            "grants": selected_grants,
            "skills": _component_projection_items("skills"),
            "mcpTools": selected_mcp_tools,
            "cliProfiles": _component_projection_items("cliProfiles"),
            "uiAdapters": _component_projection_items("uiAdapters"),
            "providerAdapters": _component_projection_items("providerAdapters"),
        }
        return {
            "active": True,
            "mode": "privileged_bundle",
            "prefilterBypassed": True,
            "requestedPluginIds": requested_plugin_ids,
            "installedPluginIds": installed_plugin_ids,
            "projectedPluginIds": sorted(projected_plugin_ids),
            "requestedScopes": {key: sorted(values) for key, values in requested_scopes.items()},
            "blocked": blocked,
            "projection": privileged_projection,
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
        delegation_id: str | None = None,
        delegation_depth: int | None = None,
        tool_call_id: str = "",
    ) -> dict[str, Any]:
        manifest = self._manifest(plugin_id)
        declared_profile = next((item for item in manifest.cliProfiles if item.id == profile_id), None)
        if declared_profile is None:
            raise PluginManagerError("CLI 组件不存在", code="plugin_cli_profile_not_found", status_code=404)
        if declared_profile.exposure != "agent":
            raise PluginManagerError(
                "该组件仅供 V8OS runtime 使用，不能投影为 Agent CLI",
                code="plugin_cli_runtime_support_denied",
                status_code=403,
            )
        projection = self.projection_for(
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            delegation_id=delegation_id,
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
        # Installed CLIs may be upgraded outside V8OS. Revalidate the versioned
        # capability contract before every invocation and keep the last known
        # good snapshot when a breaking change is detected.
        if declared_profile.capabilitySync is not None:
            self._sync_cli_profile_capabilities(
                manifest,
                declared_profile,
                force_refresh=False,
            )
        profile = self._effective_cli_profile(manifest, declared_profile)
        self.validate_grant_for_invocation(
            grant_id=str(profile_payload.get("grantId") or ""),
            plugin_id=manifest.id,
            component_id=profile.id,
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            delegation_id=delegation_id,
            delegation_depth=delegation_depth,
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
        action_spec = self._effective_cli_spec(manifest, profile, action_spec)
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
        default_executable = (
            str(self._bin_root() / f"{profile.commands[0]}.cmd")
            if profile.ownership == "managed"
            else profile.commands[0]
        )
        executable = self._effective_cli_spec(
            manifest,
            profile,
            CommandSpec(argv=[default_executable]),
        ).argv[0]
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
                rendered = rendered.replace(marker, self._serialize_cli_parameter(name, definition, value))
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
                elif value is False and definition.defaultValue is True and definition.flag:
                    argv.append(f"{definition.flag}=false")
                continue
            rendered_value = self._serialize_cli_parameter(name, definition, value)
            if definition.flag:
                argv.extend([definition.flag, rendered_value])
            elif definition.positional:
                argv.append(rendered_value)
            else:
                raise PluginManagerError(f"CLI 参数 {name} 缺少安全投影规则", code="plugin_cli_parameter_contract_invalid")
        return CommandSpec(argv=argv, timeoutSeconds=action.timeoutSeconds)

    @staticmethod
    def _serialize_cli_parameter(name: str, definition: Any, value: Any) -> str:
        if definition.kind in {"text", "enum", "file"}:
            if not isinstance(value, str):
                raise PluginManagerError(f"CLI 参数 {name} 必须是字符串", code="plugin_cli_parameter_invalid")
            if definition.kind == "enum" and value not in set(definition.options):
                raise PluginManagerError(f"CLI 参数 {name} 不在允许值中", code="plugin_cli_parameter_invalid")
            return value
        if definition.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise PluginManagerError(f"CLI 参数 {name} 必须是整数", code="plugin_cli_parameter_invalid")
            return str(value)
        if definition.kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PluginManagerError(f"CLI 参数 {name} 必须是数值", code="plugin_cli_parameter_invalid")
            return str(value)
        if definition.kind == "json":
            if not isinstance(value, (list, dict)):
                raise PluginManagerError(f"CLI 参数 {name} 必须是数组或对象", code="plugin_cli_parameter_invalid")
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if definition.kind == "boolean":
            if not isinstance(value, bool):
                raise PluginManagerError(f"CLI 参数 {name} 必须是布尔值", code="plugin_cli_parameter_invalid")
            return "true" if value else "false"
        raise PluginManagerError(f"CLI 参数 {name} 类型不受支持", code="plugin_cli_parameter_contract_invalid")

    def _cli_credential_env(self, manifest: PluginManifest, profile: CliProfile) -> dict[str, str]:
        requirements = [item for item in compile_plugin_requirements(manifest) if item.componentId == profile.id]
        bindings = self._credential_bindings(manifest.id)
        result: dict[str, str] = dict(profile.environment)
        result.update(self._setup_environment(manifest))
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
                metadata = _loads(item.get("metadata_json"), {})
                roots.update(
                    str(Path(str(path)).resolve())
                    for path in list(metadata.get("skillPaths") or [])
                    if str(path).strip()
                )
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
