from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core.database import db
from core.llm_factory import llm_factory
from core.llm_tree_prefilter import select_family_keys_with_llm
from core.model_control_plane import model_control_plane
from core.plugin_host.tool_exposure import expand_tool_family_seeds
from core.plugin_host.silk_codec import silk_toolchain_status
from core.skills_install_service import get_skill_dependency_policy
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from erc.event_bus import event_bus
from erc.models import RuntimeSource
from erc.runtime_context import get_runtime_context
from mcp_client import mcp_manager
from skills.loader import SkillLoader


_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]{2,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "help",
    "please",
    "skill",
    "skills",
    "tool",
    "tools",
    "mcp",
    "服务",
    "工具",
    "一下",
    "一个",
    "一些",
    "使用",
    "帮我",
}
_SKILL_RERANK_POOL_FLOOR = 10
_MCP_RERANK_POOL_FLOOR = 12
_PLUGIN_HOST_RERANK_POOL_FLOOR = 12
_PLUGIN_HOST_BOUND_CAP = 24
_CROSS_RUNTIME_ESCAPE_TOKENS = {
    "blocker",
    "blocked",
    "blocking",
    "stale",
    "stuck",
    "retry",
    "fallback",
    "switch",
    "handoff",
    "delegate",
    "delegation",
    "parallel",
    "error",
    "errors",
    "failed",
    "failure",
    "cannot",
    "cant",
    "missing",
    "auth",
    "unauthorized",
    "permission",
    "权限",
    "授权",
    "失败",
    "错误",
    "卡住",
    "阻塞",
    "并发",
    "切换",
    "降级",
}
_EXTENSION_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "v8_agent_os_extensions_runtime_context",
    default={},
)


@dataclass(slots=True)
class ExtensionRouteBundle:
    prompt_addition: str
    filtered_tools: list[Any]
    selected_skill_names: list[str]
    exposed_mcp_tool_names: list[str]
    candidate_summary: dict[str, Any]


def _tokenize(text: str) -> list[str]:
    lowered = str(text or "").lower()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(lowered):
        stripped = token.strip().lower()
        if len(stripped) <= 1 or stripped in _STOPWORDS:
            continue
        tokens.append(stripped)
    return tokens


def _score_text(*, query_tokens: list[str], title: str, description: str) -> int:
    if not query_tokens:
        return 0

    title_tokens = _tokenize(title)
    description_tokens = _tokenize(description)
    title_set = set(title_tokens)
    description_set = set(description_tokens)

    score = 0
    for token in query_tokens:
        if token in title_set:
            score += 4
        if token in description_set:
            score += 2
        if token in str(title or "").lower():
            score += 2
        if token in str(description or "").lower():
            score += 1
    return score


def _truncate(text: str, limit: int = 100) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _tool_name(tool_ref: Any) -> str:
    return getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")).strip()


def _tool_description(tool_ref: Any) -> str:
    raw = getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or ""
    return str(raw).strip().splitlines()[0]


def _is_mcp_tool(tool_ref: Any) -> bool:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return bool(metadata.get("server_name"))


def _is_plugin_host_tool(tool_ref: Any) -> bool:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return bool(metadata.get("pluginHost"))


def _build_skill_rerank_document(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"skill: {str(skill.get('name') or skill.get('folder') or '').strip()}",
            f"description: {str(skill.get('description') or '').strip() or '暂无说明。'}",
            f"path: {str(skill.get('path') or '').strip()}",
        ]
    ).strip()


def _skill_entry_payload(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        "skillName": str(skill.get("skillName") or skill.get("name") or skill.get("folder") or "").strip(),
        "skillRoot": str(skill.get("skillRoot") or skill.get("path") or "").strip(),
        "instructionPath": str(skill.get("instructionPath") or "").strip(),
        "referencesDir": str(skill.get("referencesDir") or "").strip(),
        "scriptsDir": str(skill.get("scriptsDir") or "").strip(),
        "assetsDir": str(skill.get("assetsDir") or "").strip(),
        "templatesDir": str(skill.get("templatesDir") or "").strip(),
        "availableFiles": [
            str(item).strip()
            for item in list(skill.get("availableFiles") or [])
            if str(item).strip()
        ],
    }


def _build_mcp_rerank_document(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return "\n".join(
        [
            f"tool: {_tool_name(tool_ref)}",
            f"server: {str(metadata.get('server_name') or '').strip() or 'unknown'}",
            f"description: {_tool_description(tool_ref) or '暂无说明。'}",
        ]
    ).strip()


def _build_plugin_host_rerank_document(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return "\n".join(
        [
            f"tool: {str(metadata.get('canonicalName') or _tool_name(tool_ref)).strip()}",
            f"plugin: {str(metadata.get('pluginId') or '').strip() or 'gateway'}",
            f"raw: {str(metadata.get('rawName') or '').strip() or _tool_name(tool_ref)}",
            f"description: {_tool_description(tool_ref) or '暂无说明。'}",
        ]
    ).strip()


def _plugin_host_tool_plugin_id(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("pluginId") or "gateway").strip() or "gateway"


def _plugin_host_tool_raw_name(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    raw_name = str(metadata.get("rawName") or "").strip()
    if raw_name:
        return raw_name
    tool_name = _tool_name(tool_ref)
    if tool_name.startswith("gateway."):
        return tool_name[len("gateway.") :].strip()
    if "." in tool_name:
        return tool_name.split(".", 1)[1].strip()
    return tool_name


def _plugin_host_tool_identity(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("canonicalName") or _tool_name(tool_ref)).strip()


def _mcp_tool_server_name(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("server_name") or "unknown").strip() or "unknown"


def _should_enable_cross_runtime_escape(query_tokens: list[str]) -> bool:
    if not query_tokens:
        return False
    return any(token in _CROSS_RUNTIME_ESCAPE_TOKENS for token in query_tokens)


def _extension_runtime_source(node: str = "extensions_runtime") -> RuntimeSource:
    runtime_context = get_runtime_context()
    return RuntimeSource(
        plane="engine",
        component="extensions_runtime",
        node=node,
        agent_id=str(runtime_context.get("agent_id") or "supervisor"),
    )


class ExtensionsRuntimeService:
    def __init__(self) -> None:
        self._startup_state = "cold"
        self._snapshot_freshness = "cold"
        self._last_refresh_at: str | None = None
        self._last_refresh_error: str | None = None
        self._cached_catalog: dict[str, Any] | None = None
        self._cached_health: dict[str, Any] | None = None
        self._background_refresh_task: asyncio.Task | None = None
        self._skills_inventory_watcher_task: asyncio.Task | None = None
        self._refresh_lock = asyncio.Lock()
        self._route_cache: dict[str, tuple[float, ExtensionRouteBundle]] = {}
        self._route_cache_ttl_seconds = 20.0
        self._last_skill_inventory_change: dict[str, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _cache_path(self) -> Path:
        configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_CACHE_FILE") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return V8_AGENT_OS_HOME / "extensions_runtime_cache.json"

    def _build_runtime_state(self) -> dict[str, Any]:
        skills_state = SkillLoader.get_startup_status()
        mcp_state = mcp_manager.get_startup_status()
        return {
            "startupState": self._startup_state,
            "snapshotFreshness": self._snapshot_freshness,
            "lastRefreshAt": self._last_refresh_at,
            "lastRefreshError": self._last_refresh_error,
            "skillsStartupState": str(skills_state.get("startupState") or "cold"),
            "mcpStartupState": str(mcp_state.get("startupState") or "cold"),
            "inventoryFreshness": self._snapshot_freshness,
            "exposureFreshness": self._snapshot_freshness,
            "skills": skills_state,
            "mcp": mcp_state,
            "silk": silk_toolchain_status(),
            "lastSkillInventoryChange": self._last_skill_inventory_change,
        }

    def _decorate_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload or {})
        runtime_state = self._build_runtime_state()
        decorated.update(
            {
                "startupState": runtime_state["startupState"],
                "snapshotFreshness": runtime_state["snapshotFreshness"],
                "lastRefreshAt": runtime_state["lastRefreshAt"],
                "lastRefreshError": runtime_state["lastRefreshError"],
                "skillsStartupState": runtime_state["skillsStartupState"],
                "mcpStartupState": runtime_state["mcpStartupState"],
                "runtime": runtime_state,
            }
        )
        return decorated

    def _decorate_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload or {})
        runtime_state = self._build_runtime_state()
        decorated.update(
            {
                "startupState": runtime_state["startupState"],
                "snapshotFreshness": runtime_state["snapshotFreshness"],
                "lastRefreshAt": runtime_state["lastRefreshAt"],
                "lastRefreshError": runtime_state["lastRefreshError"],
                "skillsStartupState": runtime_state["skillsStartupState"],
                "mcpStartupState": runtime_state["mcpStartupState"],
                "runtime": runtime_state,
                "silk": runtime_state["silk"],
            }
        )
        return decorated

    def _persist_cache(self) -> None:
        if self._cached_catalog is None or self._cached_health is None:
            return
        cache_path = self._cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updatedAt": self._last_refresh_at or self._now_iso(),
            "catalog": self._cached_catalog,
            "health": self._cached_health,
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_cache(self) -> bool:
        cache_path = self._cache_path()
        if not cache_path.exists():
            return False
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        catalog = payload.get("catalog")
        health = payload.get("health")
        if not isinstance(catalog, dict) or not isinstance(health, dict):
            return False
        self._cached_catalog = catalog
        self._cached_health = health
        self._startup_state = "ready"
        self._snapshot_freshness = "cached"
        self._last_refresh_at = str(payload.get("updatedAt") or "").strip() or None
        self._last_refresh_error = None
        return True

    def _build_catalog_live(self) -> dict[str, Any]:
        skills = list(SkillLoader.get_all_skills(force_refresh=False).values())
        skills_sorted = sorted(skills, key=lambda item: str(item.get("name") or "").lower())
        mcp_status = mcp_manager.get_status()
        skills_state = SkillLoader.get_startup_status()

        servers: list[dict[str, Any]] = []
        total_tools = 0
        connected_servers = 0
        for server_name, payload in sorted(mcp_status.items(), key=lambda item: item[0].lower()):
            tools = list(payload.get("tools") or [])
            status = str(payload.get("status") or "error")
            if status == "connected":
                connected_servers += 1
            total_tools += len(tools)
            config = dict(payload.get("config") or {})
            target = str(config.get("url") or config.get("command") or "")
            if config.get("args"):
                args_text = " ".join(str(item) for item in list(config.get("args") or []))
                target = f"{target} {args_text}".strip()
            servers.append(
                {
                    "name": server_name,
                    "status": status,
                    "toolCount": len(tools),
                    "tools": tools,
                    "transport": str(config.get("type") or ("stdio" if config.get("command") else "sse")),
                    "target": target,
                    "disabled": bool(config.get("disabled", False)),
                }
            )

        roots = SkillLoader._skills_roots or SkillLoader._resolve_skill_roots()
        skills_fingerprint = str(skills_state.get("fingerprint") or "").strip()
        changed_at = str(
            ((self._last_skill_inventory_change or {}).get("changedAt"))
            or skills_state.get("lastRefreshAt")
            or "",
        ).strip() or None
        return {
            "fingerprint": skills_fingerprint,
            "changedAt": changed_at,
            "lastSkillInventoryChange": self._last_skill_inventory_change,
            "summary": {
                "skillCount": len(skills_sorted),
                "mcpServerCount": len(servers),
                "connectedMcpServerCount": connected_servers,
                "mcpToolCount": total_tools,
            },
            "skillDependencyPolicy": get_skill_dependency_policy(),
            "skills": {
                "root": str(roots[0]) if roots else "",
                "roots": [str(root) for root in roots],
                "fingerprint": skills_fingerprint,
                "changedAt": changed_at,
                "items": [
                    {
                        "name": str(item.get("name") or item.get("folder") or ""),
                        "description": str(item.get("description") or "暂无说明。"),
                        "path": str(item.get("path") or ""),
                        "entry": _skill_entry_payload(item),
                    }
                    for item in skills_sorted
                ],
            },
            "mcp": {
                "servers": servers,
            },
        }

    def _build_health_live(self, catalog: dict[str, Any]) -> dict[str, Any]:
        status_breakdown = Counter()
        for server in list(((catalog.get("mcp") or {}).get("servers") or [])):
            status_breakdown[str(server.get("status") or "error")] += 1

        root = str(((catalog.get("skills") or {}).get("root")) or "")
        return {
            "summary": dict(catalog.get("summary") or {}),
            "skillDependencyPolicy": dict(catalog.get("skillDependencyPolicy") or {}),
            "skills": {
                "root": root,
                "available": bool(root),
            },
            "mcp": {
                "statusBreakdown": dict(status_breakdown),
            },
        }

    async def _wait_optional_task(self, task: asyncio.Task | None, *, timeout: float, label: str) -> None:
        if not task:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[ExtensionsRuntime] {label} is still refreshing in background; catalog warmup will continue with current state.")

    async def _refresh_runtime_snapshot(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
        force_skill_reload: bool = False,
        force_mcp_reload: bool = False,
    ) -> dict[str, Any]:
        async with self._refresh_lock:
            self._startup_state = "refreshing"
            self._snapshot_freshness = "cached" if self._cached_catalog else "cold"
            self._last_refresh_error = None
            if force_skill_reload:
                await asyncio.to_thread(SkillLoader.reload_skills)
            else:
                await self._wait_optional_task(skill_refresh_task, timeout=12.0, label="SkillLoader")
            if force_mcp_reload:
                await mcp_manager.cleanup()
                await mcp_manager.initialize()
            else:
                await self._wait_optional_task(mcp_init_task, timeout=12.0, label="MCP")

            catalog = self._build_catalog_live()
            health = self._build_health_live(catalog)
            self._cached_catalog = catalog
            self._cached_health = health
            self._startup_state = "ready"
            self._snapshot_freshness = "live"
            self._last_refresh_at = self._now_iso()
            self._last_refresh_error = None
            self._route_cache.clear()
            self._persist_cache()
            return self._decorate_health(health)

    async def _refresh_skill_inventory_if_changed(self, *, reason: str = "watcher") -> dict[str, Any]:
        change = await asyncio.to_thread(SkillLoader.reload_if_changed)
        if not change.get("changed"):
            return change
        self._last_skill_inventory_change = {
            **change,
            "changedAt": self._now_iso(),
            "reason": reason,
        }
        await self._refresh_runtime_snapshot()
        print(
            "[ExtensionsRuntime] Skills inventory changed: "
            f"reason={reason}, "
            f"added={change.get('addedSkills') or []}, "
            f"removed={change.get('removedSkills') or []}, "
            f"updated={change.get('updatedSkills') or []}"
        )
        return change

    def request_skill_inventory_refresh(self, *, reason: str = "manual") -> None:
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._refresh_skill_inventory_if_changed(reason=reason),
                loop,
            )
            return
        try:
            change = SkillLoader.reload_if_changed()
            if change.get("changed"):
                self._last_skill_inventory_change = {
                    **change,
                    "changedAt": self._now_iso(),
                    "reason": reason,
                }
                self._route_cache.clear()
                self._cached_catalog = None
                self._cached_health = None
        except Exception as exc:
            self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
            print(f"[ExtensionsRuntime] Immediate skills inventory refresh failed: {type(exc).__name__}: {exc}")

    def _ensure_skill_inventory_watcher(self) -> None:
        task = self._skills_inventory_watcher_task
        if task and not task.done():
            return

        async def _runner() -> None:
            while True:
                await asyncio.sleep(2.0)
                try:
                    await self._refresh_skill_inventory_if_changed(reason="watcher")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                    print(f"[ExtensionsRuntime] Skills inventory watcher failed: {type(exc).__name__}: {exc}")

        self._skills_inventory_watcher_task = asyncio.create_task(_runner(), name="extensions_runtime:skills_inventory_watcher")

    async def start(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        if self._cached_catalog is None or self._cached_health is None:
            self._load_cache()
        if self._cached_catalog is None or self._cached_health is None:
            cold_catalog = self._build_catalog_live()
            self._cached_catalog = cold_catalog
            self._cached_health = self._build_health_live(cold_catalog)
        self._startup_state = "refreshing"
        self._snapshot_freshness = "cached" if self._last_refresh_at else "cold"
        self._last_refresh_error = None

        if self._background_refresh_task and not self._background_refresh_task.done():
            self._ensure_skill_inventory_watcher()
            return

        async def _runner() -> None:
            try:
                await self._refresh_runtime_snapshot(
                    skill_refresh_task=skill_refresh_task,
                    mcp_init_task=mcp_init_task,
                )
            except Exception as exc:
                self._startup_state = "error"
                self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                print(f"[ExtensionsRuntime] Background refresh failed: {type(exc).__name__}: {exc}")

        self._background_refresh_task = asyncio.create_task(_runner(), name="extensions_runtime:refresh")
        self._ensure_skill_inventory_watcher()

    async def stop(self) -> None:
        self._loop = None
        watcher_task = self._skills_inventory_watcher_task
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        self._skills_inventory_watcher_task = None
        task = self._background_refresh_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_startup_status(self) -> dict[str, Any]:
        return self._build_runtime_state()

    def _resolve_prefilter_policy(self) -> dict[str, Any]:
        config = storage.get_extensions_config() or {}
        policy = dict(config.get("prefilterPolicy") or config.get("rerankPolicy") or {})
        enabled = bool(policy.get("enabled", False))
        if not enabled:
            return {
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": "",
                "role": "",
                "reason": "disabled",
            }

        for role in ("extensions_prefilter", "extensions_reranker"):
            try:
                resolved = model_control_plane.resolve_model_for_role(role)
            except Exception as exc:
                return {
                    "enabled": True,
                    "available": False,
                    "mode": "fallback",
                    "modelId": "",
                    "role": role,
                    "reason": str(exc),
                }
            model_id = str(resolved.get("resolvedModelId") or "").strip()
            if model_id:
                return {
                    "enabled": True,
                    "available": True,
                    "mode": "llm_tree",
                    "modelId": model_id,
                    "role": role,
                    "reason": "",
                }

        return {
            "enabled": True,
            "available": False,
            "mode": "fallback",
            "modelId": "",
            "role": "extensions_prefilter",
            "reason": "未绑定可用的扩展候选预筛模型。",
        }

    def build_catalog(self) -> dict[str, Any]:
        payload = dict(self._cached_catalog or self._build_catalog_live())
        return self._decorate_catalog(payload)

    def build_health(self) -> dict[str, Any]:
        payload = dict(self._cached_health or self._build_health_live(self._cached_catalog or self._build_catalog_live()))
        return self._decorate_health(payload)

    async def reload(self) -> dict[str, Any]:
        return await self._refresh_runtime_snapshot(force_skill_reload=True, force_mcp_reload=True)

    def build_contextual_route(
        self,
        *,
        user_query: str,
        available_tools: list[Any],
        loaded_agents: list[dict[str, Any]] | None = None,
        inherited_skill_names: list[str] | None = None,
        skill_limit: int = 6,
        mcp_limit: int = 8,
        plugin_host_limit: int = 8,
    ) -> ExtensionRouteBundle:
        query_tokens = _tokenize(user_query)
        cross_runtime_escape = _should_enable_cross_runtime_escape(query_tokens)
        effective_skill_limit = min(max(skill_limit + (2 if cross_runtime_escape else 0), skill_limit), 10)
        effective_mcp_limit = min(max(mcp_limit + (2 if cross_runtime_escape else 0), mcp_limit), 12)
        effective_plugin_host_limit = min(max(plugin_host_limit + (4 if cross_runtime_escape else 0), plugin_host_limit), 12)
        prefilter_policy = self._resolve_prefilter_policy()
        prefilter_mode = str(prefilter_policy.get("mode") or "lexical")
        prefilter_model_id = str(prefilter_policy.get("modelId") or "").strip()
        prefilter_role = str(prefilter_policy.get("role") or "").strip()
        prefilter_reason = str(prefilter_policy.get("reason") or "").strip()

        skill_entries = list(SkillLoader.get_all_skills(force_refresh=False).values())
        ranked_skills = sorted(
            (
                (
                    _score_text(
                        query_tokens=query_tokens,
                        title=str(item.get("name") or item.get("folder") or ""),
                        description=str(item.get("description") or ""),
                    ),
                    str(item.get("name") or item.get("folder") or ""),
                    item,
                )
                for item in skill_entries
            ),
            key=lambda row: (-row[0], row[1].lower()),
        )
        skill_pool_limit = max(effective_skill_limit * 2, _SKILL_RERANK_POOL_FLOOR)
        skill_pool = [row[2] for row in ranked_skills if row[0] > 0][:skill_pool_limit]
        if not skill_pool:
            skill_pool = [row[2] for row in ranked_skills[: min(skill_pool_limit, len(ranked_skills))]]
        selected_skills = list(skill_pool[:effective_skill_limit])

        mcp_tools = [tool for tool in available_tools if _is_mcp_tool(tool)]
        plugin_host_tools = [tool for tool in available_tools if _is_plugin_host_tool(tool)]
        base_tools = [tool for tool in available_tools if not _is_mcp_tool(tool) and not _is_plugin_host_tool(tool)]
        ranked_mcp = sorted(
            (
                (
                    _score_text(
                        query_tokens=query_tokens,
                        title=_tool_name(tool),
                        description=_tool_description(tool),
                    ),
                    _tool_name(tool).lower(),
                    tool,
                )
                for tool in mcp_tools
            ),
            key=lambda row: (-row[0], row[1]),
        )
        mcp_pool_limit = max(effective_mcp_limit * 2, _MCP_RERANK_POOL_FLOOR)
        mcp_pool = [row[2] for row in ranked_mcp if row[0] > 0][:mcp_pool_limit]
        if not mcp_pool:
            mcp_pool = [row[2] for row in ranked_mcp[: min(mcp_pool_limit, len(ranked_mcp))]]
        selected_mcp_tools = list(mcp_pool[:effective_mcp_limit])

        ranked_plugin_host = sorted(
            (
                (
                    _score_text(
                        query_tokens=query_tokens,
                        title=_tool_name(tool),
                        description=_tool_description(tool),
                    ),
                    _tool_name(tool).lower(),
                    tool,
                )
                for tool in plugin_host_tools
            ),
            key=lambda row: (-row[0], row[1]),
        )
        plugin_host_pool_limit = max(effective_plugin_host_limit * 2, _PLUGIN_HOST_RERANK_POOL_FLOOR)
        plugin_host_pool = [row[2] for row in ranked_plugin_host if row[0] > 0][:plugin_host_pool_limit]
        if not plugin_host_pool:
            plugin_host_pool = [
                row[2] for row in ranked_plugin_host[: min(plugin_host_pool_limit, len(ranked_plugin_host))]
            ]
        selected_plugin_host_seeds = list(plugin_host_pool[:effective_plugin_host_limit])
        skill_family_map = {
            str(item.get("path") or item.get("name") or item.get("folder") or "").strip(): item
            for item in skill_pool
            if str(item.get("path") or item.get("name") or item.get("folder") or "").strip()
        }
        mcp_family_map: dict[str, list[Any]] = {}
        for tool in mcp_pool:
            mcp_family_map.setdefault(_mcp_tool_server_name(tool), []).append(tool)
        plugin_host_seed_map: dict[str, Any] = {}
        for tool in plugin_host_pool:
            family_key = f"{_plugin_host_tool_plugin_id(tool)}::{_plugin_host_tool_raw_name(tool)}"
            plugin_host_seed_map.setdefault(family_key, tool)

        skill_state: dict[str, Any] = {}
        mcp_state: dict[str, Any] = {}
        plugin_host_state: dict[str, Any] = {}
        should_prefilter = len(skill_family_map) > 1 or len(mcp_family_map) > 1 or len(plugin_host_seed_map) > 1
        if prefilter_policy.get("enabled") and prefilter_policy.get("available") and prefilter_model_id and not should_prefilter:
            prefilter_mode = "lexical"
            prefilter_reason = "候选家族数量不足，无需额外预筛。"
        if prefilter_policy.get("enabled") and prefilter_policy.get("available") and prefilter_model_id and should_prefilter:
            try:
                skill_keys, skill_state = select_family_keys_with_llm(
                    role=prefilter_role or "extensions_prefilter",
                    user_query=user_query,
                    family_label="skills",
                    families=[
                        {
                            "key": key,
                            "title": str(item.get("name") or item.get("folder") or "").strip() or key,
                            "description": str(item.get("description") or "").strip(),
                            "memberCount": 1,
                            "examples": [str(item.get("name") or item.get("folder") or "").strip() or key],
                        }
                        for key, item in skill_family_map.items()
                    ],
                    max_families=effective_skill_limit,
                    timeout_seconds=1.0,
                )
                if skill_keys:
                    selected_skills = [skill_family_map[key] for key in skill_keys if key in skill_family_map][:effective_skill_limit]

                mcp_keys, mcp_state = select_family_keys_with_llm(
                    role=prefilter_role or "extensions_prefilter",
                    user_query=user_query,
                    family_label="mcp",
                    families=[
                        {
                            "key": server_name,
                            "title": server_name,
                            "description": "MCP 服务工具族",
                            "memberCount": len(items),
                            "examples": [_tool_name(tool) for tool in items[:4]],
                        }
                        for server_name, items in mcp_family_map.items()
                    ],
                    max_families=max(1, min(effective_mcp_limit, len(mcp_family_map))),
                    timeout_seconds=1.0,
                )
                if mcp_keys:
                    mcp_bound_limit = min(max(effective_mcp_limit * 3, _MCP_RERANK_POOL_FLOOR), max(len(mcp_tools), effective_mcp_limit))
                    selected_mcp_tools = []
                    for key in mcp_keys:
                        selected_mcp_tools.extend(mcp_family_map.get(key, []))
                    selected_mcp_tools = list(dict.fromkeys(selected_mcp_tools))[:mcp_bound_limit]

                plugin_host_keys, plugin_host_state = select_family_keys_with_llm(
                    role=prefilter_role or "extensions_prefilter",
                    user_query=user_query,
                    family_label="plugin_host",
                    families=[
                        {
                            "key": family_key,
                            "title": str((getattr(tool, "metadata", None) or {}).get("canonicalName") or _tool_name(tool)).strip() or family_key,
                            "description": _tool_description(tool),
                            "memberCount": 1,
                            "examples": [_tool_name(tool)],
                        }
                        for family_key, tool in plugin_host_seed_map.items()
                    ],
                    max_families=effective_plugin_host_limit,
                    timeout_seconds=1.0,
                )
                if plugin_host_keys:
                    selected_plugin_host_seeds = [plugin_host_seed_map[key] for key in plugin_host_keys if key in plugin_host_seed_map][:effective_plugin_host_limit]

                prefilter_mode = "llm_tree"
                prefilter_reason = (
                    skill_state.get("reason")
                    or mcp_state.get("reason")
                    or plugin_host_state.get("reason")
                    or ""
                )
                if any(bool(state.get("timedOut")) for state in (skill_state, mcp_state, plugin_host_state)):
                    prefilter_mode = "fallback"
                    prefilter_reason = "timeout"
                    selected_skills = list(skill_pool[:effective_skill_limit])
                    selected_mcp_tools = list(mcp_pool[:effective_mcp_limit])
                    selected_plugin_host_seeds = list(plugin_host_pool[:effective_plugin_host_limit])
            except Exception as exc:
                prefilter_mode = "fallback"
                prefilter_reason = str(exc)
                selected_skills = list(skill_pool[:effective_skill_limit])
                selected_mcp_tools = list(mcp_pool[:effective_mcp_limit])
                selected_plugin_host_seeds = list(plugin_host_pool[:effective_plugin_host_limit])

        plugin_host_bound_limit = min(
            max(effective_plugin_host_limit * 2, _PLUGIN_HOST_RERANK_POOL_FLOOR),
            _PLUGIN_HOST_BOUND_CAP,
        )
        selected_plugin_host_tools = expand_tool_family_seeds(
            items=plugin_host_tools,
            seeds=selected_plugin_host_seeds,
            get_plugin_id=_plugin_host_tool_plugin_id,
            get_tool_name=_plugin_host_tool_raw_name,
            get_identity=_plugin_host_tool_identity,
            get_sort_key=lambda tool: (
                _plugin_host_tool_plugin_id(tool).lower(),
                _plugin_host_tool_raw_name(tool).lower(),
                _plugin_host_tool_identity(tool).lower(),
            ),
            max_items=plugin_host_bound_limit,
        )

        inherited_skill_names_set = {
            str(item or "").strip()
            for item in list(inherited_skill_names or [])
            if str(item or "").strip()
        }
        if inherited_skill_names_set:
            inherited_skill_entries = [
                item
                for item in skill_entries
                if str(item.get("name") or item.get("folder") or "").strip() in inherited_skill_names_set
            ]
            if inherited_skill_entries:
                merged_skills: list[dict[str, Any]] = []
                seen_skill_keys: set[str] = set()
                for item in inherited_skill_entries + list(selected_skills):
                    key = str(item.get("path") or item.get("name") or item.get("folder") or "").strip()
                    if not key or key in seen_skill_keys:
                        continue
                    seen_skill_keys.add(key)
                    merged_skills.append(item)
                selected_skills = merged_skills[:effective_skill_limit]

        selected_skill_names = [str(item.get("name") or item.get("folder") or "") for item in selected_skills]
        selected_skill_entries = [_skill_entry_payload(item) for item in selected_skills]
        exposed_mcp_tool_names = [_tool_name(tool) for tool in selected_mcp_tools]
        exposed_plugin_host_tool_names = [_tool_name(tool) for tool in selected_plugin_host_tools]
        filtered_tools = base_tools + selected_mcp_tools + selected_plugin_host_tools

        lines = [
            "\n[Extensions Runtime]",
            f"- Skills 候选：{len(selected_skill_names)} / 已安装 {len(skill_entries)}",
            f"- MCP 工具候选：{len(exposed_mcp_tool_names)} / 已连接工具 {len(mcp_tools)}",
        ]
        if cross_runtime_escape:
            lines.append("- Cross-runtime escape：已启用。检测到阻塞/切换类任务语义，本轮适度放宽跨 runtime 候选。")
        if prefilter_mode == "llm_tree":
            lines.append(f"- 候选预筛：已启用 LLM 工具树预筛（模型：{prefilter_model_id}）")
        elif prefilter_policy.get("enabled"):
            details = prefilter_reason or "当前未绑定可用的扩展候选预筛模型。"
            lines.append(f"- 候选预筛：本轮已回退 lexical（{_truncate(details, 120)}）")
        if selected_skill_names:
            lines.append("- 当前命中的 Skills 目录入口：")
            for entry in selected_skill_entries[:effective_skill_limit]:
                lines.append(f"  - {entry.get('skillName') or 'unknown'}")
                if entry.get("skillRoot"):
                    lines.append(f"    - Root: {entry.get('skillRoot')}")
                if entry.get("instructionPath"):
                    lines.append(f"    - Instruction: {entry.get('instructionPath')}")
                if entry.get("referencesDir"):
                    lines.append(f"    - References: {entry.get('referencesDir')}")
                if entry.get("scriptsDir"):
                    lines.append(f"    - Scripts: {entry.get('scriptsDir')}")
                if entry.get("assetsDir"):
                    lines.append(f"    - Assets: {entry.get('assetsDir')}")
                if entry.get("templatesDir"):
                    lines.append(f"    - Templates: {entry.get('templatesDir')}")
                for item in list(entry.get("availableFiles") or [])[:10]:
                    lines.append(f"    - {item}")
            lines.append("  - 按当前 skill 的要求去做。")
        if exposed_mcp_tool_names:
            lines.append("- 当前暴露给本轮的 MCP 工具：")
            for tool in selected_mcp_tools[:effective_mcp_limit]:
                server_name = str((getattr(tool, "metadata", None) or {}).get("server_name") or "Unknown")
                lines.append(f"  - {_tool_name(tool)} ({server_name}): {_truncate(_tool_description(tool), 80)}")
        if exposed_plugin_host_tool_names:
            lines.append("- 当前暴露给本轮的 OpenClaw 工具：")
            for tool in selected_plugin_host_tools[:effective_plugin_host_limit]:
                metadata = getattr(tool, "metadata", None) or {}
                plugin_id = str(metadata.get("pluginId") or "").strip() or "gateway"
                lines.append(f"  - {str(metadata.get('canonicalName') or _tool_name(tool)).strip()} ({plugin_id}): {_truncate(_tool_description(tool), 80)}")
        lines.append("[/Extensions Runtime]")

        return ExtensionRouteBundle(
            prompt_addition="\n".join(lines),
            filtered_tools=filtered_tools,
            selected_skill_names=selected_skill_names,
            exposed_mcp_tool_names=exposed_mcp_tool_names,
            candidate_summary={
                "mode": prefilter_mode,
                "modelId": prefilter_model_id,
                "role": prefilter_role,
                "reason": prefilter_reason or None,
                "prefilterTimedOut": bool(any(bool(state.get("timedOut")) for state in (skill_state, mcp_state, plugin_host_state))),
                "prefilterCacheHit": bool(any(bool(state.get("cacheHit")) for state in (skill_state, mcp_state, plugin_host_state))),
                "skills": selected_skill_names,
                "skillEntries": selected_skill_entries,
                "mcpTools": exposed_mcp_tool_names,
                "pluginHostTools": exposed_plugin_host_tool_names,
                "skillCandidates": len(selected_skill_names),
                "mcpCandidates": len(exposed_mcp_tool_names),
                "pluginHostCandidates": len(exposed_plugin_host_tool_names),
                "skillPoolSize": len(skill_pool),
                "mcpPoolSize": len(mcp_pool),
                "pluginHostPoolSize": len(plugin_host_pool),
                "requestedSkillLimit": skill_limit,
                "requestedMcpLimit": mcp_limit,
                "requestedPluginHostLimit": plugin_host_limit,
                "effectiveSkillLimit": effective_skill_limit,
                "effectiveMcpLimit": effective_mcp_limit,
                "effectivePluginHostLimit": effective_plugin_host_limit,
                "crossRuntimeEscape": cross_runtime_escape,
                "pluginHostSeedCount": len(selected_plugin_host_seeds),
                "pluginHostBoundLimit": plugin_host_bound_limit,
                "pluginHostBoundCount": len(exposed_plugin_host_tool_names),
                "totalInstalledSkills": len(skill_entries),
                "totalConnectedMcpTools": len(mcp_tools),
                "totalPluginHostTools": len(plugin_host_tools),
                "agentCount": len(list(loaded_agents or [])),
            },
        )

    def build_supervisor_route(
        self,
        *,
        user_query: str,
        supervisor_tools: list[Any],
        loaded_agents: list[dict[str, Any]] | None = None,
        skill_limit: int = 6,
        mcp_limit: int = 8,
        plugin_host_limit: int = 8,
    ) -> ExtensionRouteBundle:
        context_payload = self._resolve_event_context()
        session_id = str(context_payload.get("session_id") or "").strip() or "global"
        normalized_query = " ".join(_tokenize(user_query)) or str(user_query or "").strip().lower()
        tool_signature = ",".join(sorted(_tool_name(tool) for tool in supervisor_tools if _tool_name(tool)))
        inventory_revision = str(self._last_refresh_at or "cold")
        cache_key = "|".join(
            [
                session_id,
                normalized_query,
                inventory_revision,
                str(len(list(loaded_agents or []))),
                str(skill_limit),
                str(mcp_limit),
                str(plugin_host_limit),
                tool_signature,
            ]
        )
        now = time.monotonic()
        cached = self._route_cache.get(cache_key)
        if cached and (now - cached[0]) <= self._route_cache_ttl_seconds:
            return cached[1]

        bundle = self.build_contextual_route(
            user_query=user_query,
            available_tools=supervisor_tools,
            loaded_agents=loaded_agents,
            skill_limit=skill_limit,
            mcp_limit=mcp_limit,
            plugin_host_limit=plugin_host_limit,
        )
        self._route_cache[cache_key] = (now, bundle)
        if len(self._route_cache) > 128:
            stale_keys = sorted(self._route_cache.items(), key=lambda item: item[1][0])[:32]
            for stale_key, _ in stale_keys:
                self._route_cache.pop(stale_key, None)
        return bundle

    def bind_execution_context(self, **context: Any):
        current = dict(_EXTENSION_CONTEXT.get() or {})
        current.update({key: value for key, value in context.items() if value is not None})
        return _EXTENSION_CONTEXT.set(current)

    def reset_execution_context(self, token: contextvars.Token) -> None:
        _EXTENSION_CONTEXT.reset(token)

    def _resolve_event_context(self) -> dict[str, Any]:
        payload = dict(_EXTENSION_CONTEXT.get() or {})
        runtime_context = get_runtime_context()
        for key in ("session_id", "conversation_id", "run_id", "agent_id"):
            if payload.get(key) is None and runtime_context.get(key) is not None:
                payload[key] = runtime_context.get(key)
        return payload

    def _emit(self, topic: str, payload: dict[str, Any], *, node: str) -> None:
        context_payload = self._resolve_event_context()
        session_id = str(context_payload.get("session_id") or "")
        if not session_id:
            return
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=str(context_payload.get("conversation_id") or session_id),
            run_id=str(context_payload.get("run_id") or "") or None,
            source=_extension_runtime_source(node=node),
        )
        emitter.emit(topic, payload, source=_extension_runtime_source(node=node))

    def emit_route_selected(self, *, user_query: str, route_bundle: ExtensionRouteBundle) -> None:
        self._emit(
            "extension.route.selected",
            {
                "queryPreview": _truncate(user_query, 160),
                "skillCandidates": route_bundle.selected_skill_names,
                "skillEntries": route_bundle.candidate_summary.get("skillEntries") or [],
                "mcpToolCandidates": route_bundle.exposed_mcp_tool_names,
                "pluginHostToolCandidates": route_bundle.candidate_summary.get("pluginHostTools") or [],
                "counts": route_bundle.candidate_summary,
                "routing": {
                    "mode": route_bundle.candidate_summary.get("mode"),
                    "modelId": route_bundle.candidate_summary.get("modelId"),
                    "role": route_bundle.candidate_summary.get("role"),
                    "skillPoolSize": route_bundle.candidate_summary.get("skillPoolSize"),
                    "mcpPoolSize": route_bundle.candidate_summary.get("mcpPoolSize"),
                    "pluginHostPoolSize": route_bundle.candidate_summary.get("pluginHostPoolSize"),
                    "selectedSkills": route_bundle.candidate_summary.get("skills"),
                    "selectedMcpTools": route_bundle.candidate_summary.get("mcpTools"),
                    "selectedPluginHostTools": route_bundle.candidate_summary.get("pluginHostTools"),
                },
            },
            node="route_selected",
        )
        if route_bundle.exposed_mcp_tool_names:
            self._emit(
                "extension.mcp.candidate_exposed",
                {
                    "toolNames": route_bundle.exposed_mcp_tool_names,
                    "count": len(route_bundle.exposed_mcp_tool_names),
                },
                node="mcp_candidate_exposed",
            )

    def emit_skill_loaded(self, *, skill_name: str, skill_path: str) -> None:
        self._emit(
            "extension.skill.loaded",
            {
                "skillName": skill_name,
                "skillPath": skill_path,
            },
            node="skill_loaded",
        )

    def emit_skill_blocked(
        self,
        *,
        skill_name: str,
        skill_path: str,
        verdict: str,
        confidence: float,
        skill_trust_score: int,
        audit_id: str,
        reasons: list[str],
        flagged_files: list[dict[str, Any]],
    ) -> None:
        payload = {
            "skillName": skill_name,
            "skillPath": skill_path,
            "verdict": verdict,
            "confidence": confidence,
            "skillTrustScore": skill_trust_score,
            "auditId": audit_id,
            "reasons": list(reasons or []),
            "flaggedFiles": list(flagged_files or []),
        }
        self._emit("extension.skill.blocked", payload, node="skill_blocked")
        self._emit("safety.skill_blocked", payload, node="skill_blocked")

    def emit_response_tool_calls(self, response: Any) -> None:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        if not tool_calls:
            return
        current_mcp_tools = {tool.name for tool in mcp_manager.get_tools()}
        invoked = [str(item.get("name") or "") for item in tool_calls if str(item.get("name") or "") in current_mcp_tools]
        if not invoked:
            return
        self._emit(
            "extension.mcp.invoked",
            {
                "toolNames": invoked,
                "count": len(invoked),
            },
            node="mcp_invoked",
        )

    def emit_execution_completed(self, *, response: Any) -> None:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        self._emit(
            "extension.execution.completed",
            {
                "hasToolCalls": bool(tool_calls),
                "toolNames": [str(item.get("name") or "") for item in tool_calls],
                "messagePreview": _truncate(str(getattr(response, "content", "") or ""), 200),
            },
            node="execution_completed",
        )

    def emit_supervisor_diagnostics(self, payload: dict[str, Any]) -> None:
        self._emit("supervisor.turn.diagnostics", payload, node="supervisor_diagnostics")

    def build_usage_summary(self, *, window_hours: int = 24) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        threshold = (now - timedelta(hours=max(window_hours, 1))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        current_mcp_tools = {tool.name for tool in mcp_manager.get_tools()}

        skill_counter: Counter[str] = Counter()
        mcp_counter: Counter[str] = Counter()
        recent_events: list[dict[str, Any]] = []
        exposure_counter: Counter[str] = Counter()
        failure_count = 0

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT topic, payload_json, event_ts
                FROM runtime_events
                WHERE event_ts >= ?
                  AND (topic LIKE 'extension.%' OR topic = 'tool.started')
                ORDER BY event_ts DESC
                LIMIT 300
                """,
                (threshold,),
            )
            rows = cursor.fetchall()

        for row in rows:
            topic = str(row["topic"] or "")
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            event_ts = str(row["event_ts"] or "")
            if topic == "extension.skill.loaded":
                skill_name = str(payload.get("skillName") or "").strip()
                if skill_name:
                    skill_counter[skill_name] += 1
                    recent_events.append({"kind": "skill", "name": skill_name, "ts": event_ts})
            elif topic == "extension.skill.blocked":
                skill_name = str(payload.get("skillName") or "").strip()
                verdict = str(payload.get("verdict") or "").strip()
                if skill_name:
                    recent_events.append(
                        {
                            "kind": "skill_blocked",
                            "name": skill_name,
                            "status": verdict or "blocked",
                            "ts": event_ts,
                        }
                    )
            elif topic == "extension.mcp.invoked":
                for tool_name in list(payload.get("toolNames") or []):
                    normalized = str(tool_name or "").strip()
                    if normalized:
                        mcp_counter[normalized] += 1
                        recent_events.append({"kind": "mcp", "name": normalized, "ts": event_ts})
            elif topic == "extension.mcp.candidate_exposed":
                for tool_name in list(payload.get("toolNames") or []):
                    normalized = str(tool_name or "").strip()
                    if normalized:
                        exposure_counter[normalized] += 1
            elif topic == "extension.execution.completed":
                if not payload.get("hasToolCalls") and "失败" in str(payload.get("messagePreview") or ""):
                    failure_count += 1
            elif topic == "tool.started":
                tool = payload.get("tool") or payload
                tool_name = str(tool.get("toolName") or tool.get("tool_name") or "").strip()
                if tool_name and tool_name in current_mcp_tools:
                    mcp_counter[tool_name] += 1
                    recent_events.append({"kind": "mcp", "name": tool_name, "ts": event_ts})

        candidate_summary = {
            "skills": sum(skill_counter.values()),
            "mcpTools": sum(mcp_counter.values()),
            "currentExposedSkillCandidates": len(skill_counter),
            "currentExposedMcpCandidates": len(exposure_counter),
        }

        return {
            "windowHours": max(window_hours, 1),
            "skills": {
                "totalUses": sum(skill_counter.values()),
                "topItems": [{"name": name, "count": count} for name, count in skill_counter.most_common(6)],
            },
            "mcp": {
                "totalUses": sum(mcp_counter.values()),
                "topItems": [{"name": name, "count": count} for name, count in mcp_counter.most_common(8)],
            },
            "recentHits": recent_events[:10],
            "degradationSummary": {
                "recentFailures": failure_count,
            },
            "candidateExposure": candidate_summary,
        }


extensions_runtime_service = ExtensionsRuntimeService()
