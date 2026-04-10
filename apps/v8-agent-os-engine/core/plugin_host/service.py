from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import locale
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from core.database import db
from core.models.factory import llm_factory
from core.models.control_plane import model_control_plane
from core.llm_tree_prefilter import select_family_keys_with_llm
from core.storage import storage
from core.v8_agent_os_paths import OPENCLAW_DEFAULT_STATE_ROOT, PLUGIN_HOST_ROOT, PLUGIN_INSTALL_LOG_ROOT
from core.context.workspace import workspace_resolution_service
from .catalog import build_install_catalog
from .health import evaluate_plugin_health
from .inbound import normalize_inbound_message
from .media_assets import materialize_last_asset, materialize_last_assets, normalize_asset_sources
from .outbound import broadcast_media as outbound_broadcast_media
from .outbound import broadcast_text as outbound_broadcast_text
from .outbound import default_channel_type, default_target_for
from .profiles import onboarding_profile, resolve_plugin_profile_key, support_profile, transport_profile
from .registry import (
    default_plugin_registry,
    save_plugin_registry,
    scan_plugin_registry,
    update_plugin_record,
    upsert_install_job,
)
from .safety import build_group_guard_summary
from .setup import detect_onboarding_hints, ensure_openclaw_host_bridge, merge_setup_user_action
from .sidecar_handoff import ensure_gateway_launcher_patch, ensure_weixin_handoff_patch
from .tool_registry import plugin_host_tool_registry
from .tool_exposure import expand_tool_family_seeds


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_NPM_SPEC_PATTERN = re.compile(r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+")
_OPENCLAW_MJS_RELATIVE_PATH = Path("node_modules") / "openclaw" / "openclaw.mjs"
_OPENCLAW_DASHBOARD_URL = "http://127.0.0.1:18789/"
_OPENCLAW_DOCS_URL = "https://docs.openclaw.ai/install/index"
_OPENCLAW_BRIDGE_PLUGIN_IDS = ("openclaw-v8-bridge",)
_OPENCLAW_BRIDGE_SOURCE_DIR_CANDIDATES = ("openclaw-v8-bridge", "v8-bridge")
_OPENCLAW_GATEWAY_BUILTIN_TOOLS = {
    "message": {
        "name": "message",
        "description": "OpenClaw gateway 内置消息发送工具。",
        "source": "openclaw_gateway_builtin",
    },
    "sessions_list": {
        "name": "sessions_list",
        "description": "OpenClaw gateway 内置会话查询工具。",
        "source": "openclaw_gateway_builtin",
    },
}
_OPENCLAW_PLUGIN_INVENTORY_TTL_SECONDS = 60.0
_OPENCLAW_CHANNEL_ACCOUNTS_TTL_SECONDS = 60.0
_OPENCLAW_BRIDGE_DEFAULT_MANAGED_CHANNELS = ("openclaw-weixin",)
_BRIDGE_TOOL_CATALOG_TTL_SECONDS = 300.0
_BRIDGE_STATUS_HOT_TTL_SECONDS = 5.0
_BRIDGE_STATUS_HOT_REFRESH_TIMEOUT_SECONDS = 1.5
_BRIDGE_TOOL_INVENTORY_REFRESH_TIMEOUT_SECONDS = 3.0
_BRIDGE_TOOL_PREFILTER_TIMEOUT_SECONDS = 1.0
_BRIDGE_TOOL_RERANK_POOL_FLOOR = 16
_BRIDGE_TOOL_EXPOSURE_CAP = 24
_BRIDGE_TOOL_STOPWORDS = {
    "tool",
    "tools",
    "plugin",
    "plugins",
    "openclaw",
    "bridge",
    "runtime",
    "工具",
    "插件",
    "桥接",
    "一下",
    "一个",
}
_OPENCLAW_REGISTERED_TOOL_LINE_RE = re.compile(
    r"(?P<prefix>[a-z0-9_]+):\s+registered\s+(?P<body>.+)$",
    re.IGNORECASE,
)
_OPENCLAW_REGISTERED_TOOL_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


def _parse_json_field(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        payload = json.loads(str(raw))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _is_manual_plugin_host_push_trigger(trigger_source: str | None, *, channel_type: str) -> bool:
    normalized = str(trigger_source or "").strip()
    if not normalized:
        return False
    if normalized == channel_type:
        return False
    return normalized.startswith("plugin_host_")


def _normalize_openclaw_channel_id(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"wechat", "weixin"}:
        return "openclaw-weixin"
    if normalized == "lark":
        return "feishu"
    return normalized


class PluginConfigValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        normalized_preview: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.field_errors = dict(field_errors or {})
        self.normalized_preview = dict(normalized_preview or {})


class PluginHostService:
    def __init__(self) -> None:
        self._install_tasks: dict[str, asyncio.Task[Any]] = {}
        self._attachment_only_buffers: dict[str, dict[str, Any]] = {}
        self._attachment_only_lock = asyncio.Lock()
        self._openclaw_plugins_inventory_cache: dict[str, Any] | None = None
        self._openclaw_plugins_inventory_cache_at: float = 0.0
        self._openclaw_channel_accounts_cache: dict[str, list[str]] | None = None
        self._openclaw_channel_accounts_cache_at: float = 0.0
        self._bridge_tool_catalog_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._bridge_tool_catalog_refreshing: set[tuple[str, int]] = set()
        self._bridge_tool_catalog_cache_lock = threading.Lock()
        self._bridge_inventory_hot_cache: tuple[float, dict[str, Any]] | None = None
        self._bridge_inventory_hot_refreshing = False
        self._bridge_inventory_hot_lock = threading.Lock()
        self._bridge_status_hot_cache: tuple[float, dict[str, Any]] | None = None
        self._bridge_status_hot_refreshing = False
        self._bridge_status_hot_lock = threading.Lock()
        self._snapshot_refresh_lock = threading.Lock()
        self._refresh_in_flight = False
        self._startup_state: str = "cold"
        self._snapshot_freshness: str = "cached"
        self._last_refresh_at: str | None = None
        self._last_live_refresh_at: str | None = None
        self._last_deep_refresh_at: str | None = None
        self._last_refresh_error: str | None = None
        self._cached_public_snapshot: dict[str, Any] | None = None
        self._background_refresh_task: asyncio.Task[Any] | None = None
        self.managed_local_root().mkdir(parents=True, exist_ok=True)
        PLUGIN_INSTALL_LOG_ROOT.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _monotonic_age_within(cached_at: float, ttl_seconds: float) -> bool:
        return cached_at > 0 and (time.monotonic() - cached_at) <= max(ttl_seconds, 0.0)

    @staticmethod
    def _schedule_daemon_thread(target, *args, **kwargs) -> None:
        worker = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        worker.start()

    def _snapshot_status_fields(self) -> dict[str, Any]:
        return {
            "startupState": str(self._startup_state or "cold"),
            "snapshotFreshness": str(self._snapshot_freshness or "cached"),
            "refreshInFlight": bool(self._refresh_in_flight),
            "lastRefreshAt": self._last_refresh_at,
            "lastLiveRefreshAt": self._last_live_refresh_at,
            "lastDeepRefreshAt": self._last_deep_refresh_at,
            "lastRefreshError": self._last_refresh_error,
        }

    def _managed_local_plugins_allow_state(
        self,
        *,
        openclaw_config: dict[str, Any] | None = None,
        registry: dict[str, Any] | None = None,
        inventory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.is_external_host():
            return {
                "configured": False,
                "values": [],
                "expected": [],
            }
        config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        plugins_payload = dict(config_payload.get("plugins") or {})
        configured_values = [
            str(item).strip()
            for item in list(plugins_payload.get("allow") or [])
            if str(item).strip()
        ]
        registry_payload = dict(registry or default_plugin_registry())
        plugin_records = self._managed_local_plugin_records(registry_payload)
        expected_values = sorted(
            {
                str((plugin or {}).get("pluginId") or "").strip()
                for plugin in plugin_records
                if str((plugin or {}).get("pluginId") or "").strip()
                and Path(str((plugin or {}).get("installPath") or "")).expanduser().exists()
            }
        )
        configured_channel_ids = {
            _normalize_openclaw_channel_id(str(channel_id).strip())
            for channel_id in self._configured_openclaw_channel_ids(config_payload)
            if _normalize_openclaw_channel_id(str(channel_id).strip())
        }
        try:
            inventory_payload = inventory if isinstance(inventory, dict) else self._managed_local_plugins_inventory()
        except Exception:
            inventory_payload = {"plugins": []}
        for plugin in list(inventory_payload.get("plugins") or []):
            if not isinstance(plugin, dict):
                continue
            plugin_id = str(plugin.get("id") or "").strip()
            if not plugin_id:
                continue
            normalized_plugin_id = _normalize_openclaw_channel_id(plugin_id)
            channel_ids = {
                _normalize_openclaw_channel_id(str(item).strip())
                for item in [*list(plugin.get("channels") or []), *list(plugin.get("channelIds") or [])]
                if str(item).strip()
            }
            if plugin_id in _OPENCLAW_BRIDGE_PLUGIN_IDS or channel_ids or normalized_plugin_id in configured_channel_ids:
                expected_values = sorted({*expected_values, plugin_id})
        return {
            "configured": bool(configured_values),
            "values": configured_values,
            "expected": expected_values,
        }

    def _build_plugin_host_provenance_warnings(
        self,
        *,
        registry: dict[str, Any] | None = None,
        openclaw_config: dict[str, Any] | None = None,
        bridge_state: dict[str, Any] | None = None,
        inventory: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.is_external_host():
            return []
        registry_payload = dict(registry or default_plugin_registry())
        openclaw_config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        bridge_state_payload = dict(bridge_state or {})
        allow_state = self._managed_local_plugins_allow_state(
            openclaw_config=openclaw_config_payload,
            registry=registry_payload,
        )
        allow_values = {str(item).strip() for item in list(allow_state.get("values") or []) if str(item).strip()}
        warnings: list[dict[str, Any]] = []
        if not allow_values:
            warnings.append(
                {
                    "kind": "plugins_allow_missing",
                    "level": "warning",
                    "title": "plugins.allow 当前为空",
                    "description": "OpenClaw 4.8 当前仍在用未 pin 的全局自动发现插件。建议显式把 bridge 与渠道插件加入 plugins.allow，避免接管、工具树与 trust 状态长期漂浮。",
                }
            )
        expected_allow = {
            str(item).strip()
            for item in list(allow_state.get("expected") or [])
            if str(item).strip()
        }
        missing_expected = sorted(expected_allow - allow_values)
        if missing_expected:
            warnings.append(
                {
                    "kind": "plugins_allow_incomplete",
                    "level": "warning",
                    "title": "plugins.allow 未覆盖当前已安装插件",
                    "description": "OpenClaw 4.8 会继续加载这些插件，但 trust 与治理状态会偏漂浮。",
                    "pluginIds": missing_expected,
                }
            )
        critical_plugin_ids = set(_OPENCLAW_BRIDGE_PLUGIN_IDS)
        for plugin in self._managed_local_plugin_records(registry_payload):
            plugin_id = str((plugin or {}).get("pluginId") or "").strip()
            if not plugin_id:
                continue
            plugin_type = str((plugin or {}).get("pluginType") or "").strip().lower()
            if plugin_type == "channel":
                critical_plugin_ids.add(plugin_id)
        try:
            inventory_payload = inventory if isinstance(inventory, dict) else self._managed_local_plugins_inventory()
        except Exception:
            inventory_payload = {"plugins": []}
        for plugin in list(inventory_payload.get("plugins") or []):
            if not isinstance(plugin, dict):
                continue
            plugin_id = str(plugin.get("id") or "").strip()
            if not plugin_id:
                continue
            channel_ids = [
                _normalize_openclaw_channel_id(str(item).strip())
                for item in [*list(plugin.get("channels") or []), *list(plugin.get("channelIds") or [])]
                if str(item).strip()
            ]
            if channel_ids:
                critical_plugin_ids.add(plugin_id)
        plugin_map = {
            str((plugin or {}).get("pluginId") or "").strip(): dict(plugin)
            for plugin in self._managed_local_plugin_records(registry_payload)
            if str((plugin or {}).get("pluginId") or "").strip()
        }
        inventory_map = {
            str((plugin or {}).get("id") or "").strip(): dict(plugin)
            for plugin in list(inventory_payload.get("plugins") or [])
            if isinstance(plugin, dict) and str((plugin or {}).get("id") or "").strip()
        }
        for plugin_id in sorted(critical_plugin_ids):
            plugin = plugin_map.get(plugin_id) or inventory_map.get(plugin_id) or {}
            source = str(plugin.get("source") or "").strip() or "unknown"
            trusted = False
            if plugin_id in _OPENCLAW_BRIDGE_PLUGIN_IDS:
                trusted = bool(bridge_state_payload.get("installTrusted"))
            elif plugin_id in allow_values:
                trusted = True
            if trusted:
                continue
            if source in {"openclaw-plugin-root", "extensions_root", "managed_local_extensions"}:
                warnings.append(
                    {
                        "kind": "global_auto_discovery",
                        "level": "warning",
                        "pluginId": plugin_id,
                        "title": f"{plugin_id} 仍处于全局自动发现态",
                        "description": "当前插件来自 ~/.openclaw/extensions 自动发现，尚未进入稳定 trust/provenance 主链。建议通过 openclaw plugins install / --link 安装，并显式进入 plugins.allow。",
                        "source": source,
                    }
                )
        return warnings

    @staticmethod
    def _build_claim_field_contract_warnings(last_claim_payload_shape: dict[str, Any] | None) -> list[dict[str, Any]]:
        payload = dict(last_claim_payload_shape or {})
        missing_required = [
            str(item).strip()
            for item in list(payload.get("missingRequired") or [])
            if str(item).strip()
        ]
        missing_optional = [
            str(item).strip()
            for item in list(payload.get("missingOptional") or [])
            if str(item).strip()
        ]
        warnings: list[dict[str, Any]] = []
        if missing_required:
            warnings.append(
                {
                    "kind": "claim_contract_required_missing",
                    "level": "critical",
                    "title": "最近一次 claim 缺少必填字段",
                    "description": "这说明 bridge 看到的 OpenClaw 入站 payload 仍不完整，当前接管只能视为高风险假接管。",
                    "fields": missing_required,
                }
            )
        rich_missing = [
            field
            for field in missing_optional
            if field in {"threadId", "attachments", "mentions", "eventKind", "eventSubtype", "accountScope", "actionPayload"}
        ]
        if rich_missing:
            warnings.append(
                {
                    "kind": "claim_contract_rich_fields_missing",
                    "level": "warning",
                    "title": "最近一次 claim 缺少富交互字段",
                    "description": "这些字段缺失时，历史、附件、线程、工具或交互卡能力会出现表面接管但实际残缺的情况。",
                    "fields": rich_missing,
                }
            )
        return warnings

    def _augment_host_surface_diagnostics(
        self,
        host_surface: dict[str, Any],
        *,
        registry: dict[str, Any] | None = None,
        openclaw_config: dict[str, Any] | None = None,
        bridge_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = copy.deepcopy(dict(host_surface or {}))
        if self.is_external_host():
            current.setdefault("pluginsAllowConfigured", current.get("pluginsAllowConfigured"))
            current.setdefault("pluginsAllow", current.get("pluginsAllow") or [])
            current.setdefault("pluginsAllowExpected", current.get("pluginsAllowExpected") or [])
            current.setdefault("pluginProvenanceWarnings", current.get("pluginProvenanceWarnings") or [])
            current.setdefault("fieldContractWarnings", self._build_claim_field_contract_warnings(current.get("lastClaimPayloadShape")))
            return current
        allow_state = self._managed_local_plugins_allow_state(
            openclaw_config=openclaw_config,
            registry=registry,
        )
        current["pluginsAllowConfigured"] = bool(allow_state.get("configured"))
        current["pluginsAllow"] = list(allow_state.get("values") or [])
        current["pluginsAllowExpected"] = list(allow_state.get("expected") or [])
        current["pluginProvenanceWarnings"] = self._build_plugin_host_provenance_warnings(
            registry=registry,
            openclaw_config=openclaw_config,
            bridge_state=bridge_state,
        )
        current["fieldContractWarnings"] = self._build_claim_field_contract_warnings(current.get("lastClaimPayloadShape"))
        doctor_report = self._build_bridge_doctor_report(
            refresh=False,
            registry=registry,
            openclaw_config=openclaw_config,
            bridge_state=bridge_state,
            host_surface=current,
        )
        current["bridgeDoctorSummary"] = dict(doctor_report.get("summary") or {})
        current["bridgeDoctorChecks"] = [
            dict(item)
            for item in list(doctor_report.get("checks") or [])
            if isinstance(item, dict)
        ]
        return current

    @staticmethod
    def _bridge_doctor_check(
        *,
        key: str,
        status: str,
        title: str,
        description: str,
        details: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "key": str(key or "").strip(),
            "status": str(status or "warning").strip().lower() or "warning",
            "title": str(title or "").strip(),
            "description": str(description or "").strip(),
            **({"details": str(details).strip()} if str(details or "").strip() else {}),
            **({"data": dict(data)} if isinstance(data, dict) and data else {}),
        }

    @staticmethod
    def _bridge_doctor_status_rank(status: str | None) -> int:
        normalized = str(status or "").strip().lower()
        if normalized == "critical":
            return 3
        if normalized == "warning":
            return 2
        if normalized == "ok":
            return 1
        return 0

    def _summarize_bridge_doctor_checks(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        critical_count = sum(1 for item in checks if str(item.get("status") or "").strip().lower() == "critical")
        warning_count = sum(1 for item in checks if str(item.get("status") or "").strip().lower() == "warning")
        ok_count = sum(1 for item in checks if str(item.get("status") or "").strip().lower() == "ok")
        overall = "critical" if critical_count else ("warning" if warning_count else "ok")
        if overall == "critical":
            title = "OpenClaw 4.8 接管链存在阻断问题"
            description = "当前至少有一项 bridge / handoff / trust 检查未通过，不能把它视为稳定可接管。"
        elif overall == "warning":
            title = "OpenClaw 4.8 接管链可运行，但仍有退化风险"
            description = "当前 bridge 主链可用，但仍存在 provenance、字段合同或工具目录退化风险。"
        else:
            title = "OpenClaw 4.8 接管链检查通过"
            description = "bridge live route、trust/provenance、handoff 与工具目录主链当前都处于可用状态。"
        return {
            "status": overall,
            "criticalCount": critical_count,
            "warningCount": warning_count,
            "okCount": ok_count,
            "title": title,
            "description": description,
            "checkedAt": _now_iso(),
        }

    def _bridge_doctor_repair_plan(self, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = {
            str(item.get("key") or "").strip(): dict(item)
            for item in checks
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }
        steps: list[dict[str, Any]] = []
        if self._bridge_doctor_status_rank(indexed.get("bridge_runtime_drift", {}).get("status")) >= 2 or self._bridge_doctor_status_rank(indexed.get("bridge_status_route_reachable", {}).get("status")) >= 3:
            repo_root = self._managed_local_bridge_repo_root()
            steps.append(
                {
                    "key": "repair_bridge_runtime_drift",
                    "title": "校正 live bridge 源",
                    "description": "优先通过 OpenClaw canonical install/link 重新接管 bridge，再清理 ~/.openclaw/extensions 漂浮拷贝并重启 gateway。",
                    "commandHint": (
                        f"openclaw plugins install --link {repo_root}"
                        if repo_root is not None
                        else f"openclaw plugins install {self._bridge_package_name()}"
                    ),
                }
            )
        if self._bridge_doctor_status_rank(indexed.get("plugins_allow_present", {}).get("status")) >= 2 or self._bridge_doctor_status_rank(indexed.get("plugins_allow_complete", {}).get("status")) >= 2:
            steps.append(
                {
                    "key": "sync_plugins_allow",
                    "title": "同步 plugins.allow",
                    "description": "把 bridge 与当前已托管 channel plugin 写入 plugins.allow，结束 global auto-discovery 漂浮态。",
                }
            )
        if self._bridge_doctor_status_rank(indexed.get("bridge_install_provenance", {}).get("status")) >= 2:
            steps.append(
                {
                    "key": "repair_bridge_provenance",
                    "title": "恢复 canonical provenance",
                    "description": "把 bridge 从未追踪的 extensions root 漂浮态切回 install/link provenance 或显式 allowlist trust。",
                }
            )
        if self._bridge_doctor_status_rank(indexed.get("handoff_env_configured", {}).get("status")) >= 3:
            steps.append(
                {
                    "key": "repair_handoff_env",
                    "title": "重新注入 handoff env",
                    "description": "修复 bridge 私有配置与 gateway launcher env，确保 /v1/plugin-host/inbound 与 handoff token 一致。",
                }
            )
        if self._bridge_doctor_status_rank(indexed.get("tool_inventory_health", {}).get("status")) >= 2:
            steps.append(
                {
                    "key": "repair_tool_inventory",
                    "title": "刷新桥接工具目录",
                    "description": "优先重探 gateway RPC 与 durable inventory；若 scope 缺失则明确保留退化状态，不再伪装成完整工具树。",
                }
            )
        return steps

    def _build_bridge_doctor_report(
        self,
        *,
        refresh: bool = False,
        registry: dict[str, Any] | None = None,
        openclaw_config: dict[str, Any] | None = None,
        bridge_state: dict[str, Any] | None = None,
        host_surface: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.is_external_host():
            current_host_surface = dict(host_surface or {})
            checks = [
                self._bridge_doctor_check(
                    key="bridge_status_route_reachable",
                    status="ok" if bool(current_host_surface.get("bridgeReady")) else "warning",
                    title="外部 host bridge 状态",
                    description="当前 external host 模式下，doctor 只回显远端 hostSurface 里的桥接状态。",
                    details=str(current_host_surface.get("bridgePluginId") or "").strip() or None,
                )
            ]
            return {
                "checks": checks,
                "summary": self._summarize_bridge_doctor_checks(checks),
                "repairPlan": [],
                "restartRequired": False,
            }
        registry_payload = dict(registry or default_plugin_registry())
        openclaw_config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        bridge_state_payload = dict(
            bridge_state
            or self._managed_local_bridge_state(
                refresh=refresh,
                deep_inspect=True,
                openclaw_config=openclaw_config_payload,
            )
        )
        current_host_surface = dict(host_surface or {})
        allow_state = self._managed_local_plugins_allow_state(
            openclaw_config=openclaw_config_payload,
            registry=registry_payload,
        )
        inspect_payload = dict(bridge_state_payload.get("inspectPayload") or self._managed_local_plugin_inspect(_OPENCLAW_BRIDGE_PLUGIN_IDS[0]))
        inspect_plugin_payload = dict(inspect_payload.get("plugin") or {})
        route_payload = dict(bridge_state_payload.get("routePayload") or {})
        status_route_ok = bool(route_payload.get("ok"))
        status_route_error: str | None = None
        if not status_route_ok:
            try:
                status_body = self._openclaw_gateway_request_json(suffix="/plugins/openclaw-v8-bridge/status", timeout=15)
                if isinstance(status_body, dict) and bool(status_body.get("ok")):
                    route_payload = dict(status_body)
                    status_route_ok = True
                else:
                    status_route_error = str(status_body).strip() or "bridge status route returned non-ok payload"
            except Exception as exc:
                status_route_error = str(exc).strip() or exc.__class__.__name__
        tools_route_ok = False
        tools_route_error: str | None = None
        try:
            tools_body = self._openclaw_gateway_request_json(suffix="/plugins/openclaw-v8-bridge/tools", timeout=20)
            tools_route_ok = isinstance(tools_body, dict) and bool(tools_body.get("ok"))
            if not tools_route_ok:
                tools_route_error = str(tools_body).strip() or "bridge tools route returned non-ok payload"
        except Exception as exc:
            tools_route_error = str(exc).strip() or exc.__class__.__name__

        tool_catalog_error: str | None = None
        try:
            tool_catalog = self._bridge_tool_catalog(query="bridge doctor", limit=6, refresh=refresh)
        except Exception as exc:
            tool_catalog = {}
            tool_catalog_error = str(exc).strip() or exc.__class__.__name__

        repo_root = self._managed_local_bridge_repo_root()
        repo_hash = self._hash_plugin_root(repo_root)
        runtime_extension_path_text = str(route_payload.get("runtimeExtensionPath") or bridge_state_payload.get("runtimeExtensionPath") or inspect_plugin_payload.get("rootDir") or "").strip()
        runtime_extension_path = Path(runtime_extension_path_text).expanduser() if runtime_extension_path_text else None
        runtime_hash = self._hash_plugin_root(runtime_extension_path) if runtime_extension_path else None
        slot_root = self._managed_local_bridge_extension_path()
        slot_hash = self._hash_plugin_root(slot_root)
        expected_route_version = None
        if repo_root is not None:
            package_path = repo_root / "package.json"
            if package_path.exists():
                try:
                    expected_route_version = str((json.loads(package_path.read_text(encoding="utf-8")) or {}).get("version") or "").strip() or None
                except Exception:
                    expected_route_version = None
        runtime_drift_reasons: list[str] = []
        if repo_hash and slot_hash and repo_hash != slot_hash:
            runtime_drift_reasons.append("仓库 bridge 代码与 ~/.openclaw/extensions live slot hash 不一致。")
        if repo_hash and runtime_hash and repo_hash != runtime_hash:
            runtime_drift_reasons.append("bridge live route 当前加载的代码与仓库 bridge hash 不一致。")
        if repo_root is not None and runtime_extension_path is not None and not self._same_path(repo_root, runtime_extension_path) and not self._same_path(slot_root, runtime_extension_path):
            runtime_drift_reasons.append("bridge live route 当前加载路径既不是仓库根，也不是 ~/.openclaw/extensions slot。")
        route_version = str(route_payload.get("routeVersion") or bridge_state_payload.get("routeVersion") or "").strip()
        if expected_route_version and route_version and not route_version.startswith(expected_route_version):
            runtime_drift_reasons.append(f"routeVersion={route_version} 与当前仓库版本 {expected_route_version} 不一致。")
        code_fingerprint = str(route_payload.get("codeFingerprint") or bridge_state_payload.get("codeFingerprint") or "").strip()
        if repo_hash and code_fingerprint and repo_hash != code_fingerprint:
            runtime_drift_reasons.append("bridge /status 返回的 codeFingerprint 与当前仓库桥接代码 hash 不一致。")

        provenance_warnings = self._build_plugin_host_provenance_warnings(
            registry=registry_payload,
            openclaw_config=openclaw_config_payload,
            bridge_state=bridge_state_payload,
        )
        field_contract_warnings = self._build_claim_field_contract_warnings(
            current_host_surface.get("lastClaimPayloadShape") or bridge_state_payload.get("lastClaimPayloadShape")
        )
        checks: list[dict[str, Any]] = []
        checks.append(
            self._bridge_doctor_check(
                key="bridge_status_route_reachable",
                status="ok" if status_route_ok else "critical",
                title="bridge /status live route",
                description="用于确认当前 OpenClaw 实际加载的 bridge HTTP route 是否可达。",
                details=(f"routeVersion={route_version}" if status_route_ok and route_version else status_route_error),
                data={
                    "routeVersion": route_version or None,
                    "codeFingerprint": code_fingerprint or None,
                    "runtimeExtensionPath": runtime_extension_path_text or None,
                },
            )
        )
        checks.append(
            self._bridge_doctor_check(
                key="bridge_tools_route_reachable",
                status="ok" if tools_route_ok else "critical",
                title="bridge /tools live route",
                description="用于确认当前 live bridge 工具目录 route 是否还由新代码提供，不再停留在 404/旧拷贝状态。",
                details=None if tools_route_ok else tools_route_error,
            )
        )
        checks.append(
            self._bridge_doctor_check(
                key="bridge_runtime_drift",
                status="critical" if runtime_drift_reasons else "ok",
                title="bridge live 源一致性",
                description="对比仓库 bridge、~/.openclaw/extensions live slot 与当前 live route 返回的 runtime identity，判断是否出现代码漂移。",
                details="\n".join(runtime_drift_reasons) if runtime_drift_reasons else "当前 live route 与桥接代码源一致。",
                data={
                    "repoRoot": str(repo_root) if repo_root is not None else None,
                    "repoFingerprint": repo_hash,
                    "slotPath": str(slot_root),
                    "slotFingerprint": slot_hash,
                    "runtimeExtensionPath": runtime_extension_path_text or None,
                    "runtimeFingerprint": runtime_hash,
                    "expectedRouteVersion": expected_route_version,
                    "routeVersion": route_version or None,
                },
            )
        )
        install_provenance = str(bridge_state_payload.get("installProvenance") or "unknown").strip().lower()
        checks.append(
            self._bridge_doctor_check(
                key="bridge_install_provenance",
                status="ok" if install_provenance in {"install_record", "load_path"} else ("warning" if install_provenance in {"global_auto_discovery", "global_extensions_root"} else "critical"),
                title="bridge provenance",
                description="bridge 只有进入 install/link provenance 或显式 trust 主链，才算稳定托管。",
                details=str(bridge_state_payload.get("installProvenance") or "unknown"),
            )
        )
        checks.append(
            self._bridge_doctor_check(
                key="plugins_allow_present",
                status="ok" if bool(allow_state.get("configured")) and bool(list(allow_state.get("values") or [])) else "warning",
                title="plugins.allow 已配置",
                description="OpenClaw 4.8 下建议显式 pin bridge 与 channel plugin，结束全局自动发现漂浮态。",
                details=" / ".join(list(allow_state.get("values") or [])) or "plugins.allow 当前为空。",
            )
        )
        missing_expected = sorted(
            {
                str(item).strip()
                for item in list(allow_state.get("expected") or [])
                if str(item).strip()
            }
            - {
                str(item).strip()
                for item in list(allow_state.get("values") or [])
                if str(item).strip()
            }
        )
        checks.append(
            self._bridge_doctor_check(
                key="plugins_allow_complete",
                status="ok" if not missing_expected else "warning",
                title="plugins.allow 覆盖 bridge / channel plugin",
                description="即使插件能从 global extensions root 自动发现，也不应继续依赖这种未 pin 的接入方式。",
                details=None if not missing_expected else f"缺少：{', '.join(missing_expected)}",
            )
        )
        checks.append(
            self._bridge_doctor_check(
                key="channel_plugins_trusted",
                status="ok" if not provenance_warnings else "warning",
                title="channel plugin trust / provenance",
                description="渠道插件若仍停留在 global auto-discovery 漂浮态，后续 claim、工具树与渠道恢复都容易出现假接管。",
                details=None if not provenance_warnings else "\n".join(
                    f"{str(item.get('kind') or 'warning').strip()}: {str(item.get('pluginId') or ', '.join(item.get('pluginIds') or []) or item.get('title') or '').strip()}"
                    for item in provenance_warnings
                ),
            )
        )
        checks.append(
            self._bridge_doctor_check(
                key="handoff_env_configured",
                status="ok" if bool(bridge_state_payload.get("handoffConfigured")) else "critical",
                title="handoff env / token",
                description="bridge 只有拿到一致的 inbound URL + handoff token，才会把消息 handoff 给 V8 plugin_host。",
                details="已就绪" if bool(bridge_state_payload.get("handoffConfigured")) else "当前 bridge 仍未检测到有效 handoff token。",
            )
        )
        claim_contract_status = "ok"
        if any(str(item.get("level") or "").strip().lower() == "critical" for item in field_contract_warnings):
            claim_contract_status = "critical"
        elif field_contract_warnings:
            claim_contract_status = "warning"
        checks.append(
            self._bridge_doctor_check(
                key="claim_contract_healthy",
                status=claim_contract_status,
                title="最近一次 claim 字段合同",
                description="若缺少线程、附件、mentions、事件类型等字段，就会出现历史、线程或交互卡能力残缺的假接管。",
                details=None if not field_contract_warnings else "\n".join(
                    f"{str(item.get('kind') or '').strip()}: {', '.join(item.get('fields') or [])}"
                    for item in field_contract_warnings
                ),
            )
        )
        tool_inventory_health = str(tool_catalog.get("toolInventoryHealth") or "").strip().lower() or ("critical" if tool_catalog_error else "degraded")
        checks.append(
            self._bridge_doctor_check(
                key="tool_inventory_health",
                status="ok" if tool_inventory_health == "healthy" else ("warning" if tool_catalog or tool_catalog_error else "critical"),
                title="bridge 工具目录",
                description="工具树是否完整，不再只看静态 manifest，而要看 gateway RPC / durable cache / CLI inventory 的综合结果。",
                details=(
                    tool_catalog_error
                    or "\n".join(
                        str(item).strip()
                        for item in [
                            str((tool_catalog.get("toolInventoryErrors") or {}).get("stateCatalogError") or "").strip(),
                            str((tool_catalog.get("toolInventoryErrors") or {}).get("cliCatalogError") or "").strip(),
                            str((tool_catalog.get("toolInventoryErrors") or {}).get("gatewayCatalogError") or "").strip(),
                        ]
                        if str(item).strip()
                    )
                    or f"source={tool_catalog.get('toolInventorySource') or 'unknown'}"
                ),
                data={
                    "inventorySource": tool_catalog.get("toolInventorySource"),
                    "inventoryFreshness": tool_catalog.get("toolInventoryFreshness"),
                    "operatorReadAvailable": tool_catalog.get("operatorReadAvailable"),
                },
            )
        )
        summary = self._summarize_bridge_doctor_checks(checks)
        return {
            "checks": checks,
            "summary": summary,
            "repairPlan": self._bridge_doctor_repair_plan(checks),
            "restartRequired": False,
        }

    def _decorate_public_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(dict(snapshot or {}))
        current.update(self._snapshot_status_fields())
        return current

    def _minimal_public_snapshot(self) -> dict[str, Any]:
        runtime_config = self.get_runtime_config()
        runtime_enabled = bool(runtime_config.get("enabled", True))
        allowed_families = {
            str(item).strip().lower()
            for item in list(runtime_config.get("allowedFamilies") or [])
            if str(item).strip()
        }
        runtime_state = self._get_runtime_state()
        payload = default_plugin_registry()
        raw_plugins = payload.get("plugins") or {}
        if isinstance(raw_plugins, dict):
            plugins = [dict(item) for item in raw_plugins.values() if isinstance(item, dict)]
        elif isinstance(raw_plugins, list):
            plugins = [dict(item) for item in raw_plugins if isinstance(item, dict)]
        else:
            plugins = []
        if not self.is_external_host():
            plugins = [item for item in plugins if self._plugin_belongs_to_current_managed_root(item)]
        plugins.sort(key=lambda item: str(item.get("displayName") or item.get("pluginId") or "").lower())
        lifecycle_authority = "external_managed" if self.is_external_host() else "manual_local"
        gateway_reason = "PluginHostRuntime 正在后台刷新 OpenClaw 宿主状态。"
        inbound_ownership = "disabled"
        if runtime_enabled and "channel" in allowed_families:
            inbound_ownership = "unverified"
        host_surface = {
            "mode": self.host_mode(),
            "managedLocal": {
                "rootDir": str(self.managed_local_root()),
                "toolingRoot": str(self.managed_local_tooling_root()) if self.managed_local_tooling_root() else "",
                "launcherPath": str(self.managed_local_launcher_path()) if self.managed_local_launcher_path() else "",
                "autoStart": self.managed_local_auto_start(),
            },
            "externalHost": dict(runtime_config.get("externalHost") or {}),
            "coldStopped": not runtime_enabled,
            "gatewayHealth": self._synthetic_gateway_health(
                status="refreshing" if runtime_enabled else "cold_stopped",
                reason=gateway_reason if runtime_enabled else "PluginHostRuntime 已关闭，当前不保活 gateway。",
            ),
            "outboundReady": False,
            "inboundOwnership": inbound_ownership,
            "handoffReady": False,
            "handoffDrift": bool(runtime_state.get("handoffDrift", False)),
            "lastInboundHandoffAt": runtime_state.get("lastInboundHandoffAt"),
            "lifecycleAuthority": lifecycle_authority,
            "autoStartDriftDetected": bool(runtime_state.get("autoStartDriftDetected", False)),
            "reconciledAt": runtime_state.get("reconciledAt"),
            "cliSource": "missing",
            "toolingMode": "external_host" if self.is_external_host() else "missing",
            "toolingEntry": None,
            "launcherSource": "direct_cli_run",
            "launcherMissing": True,
            "bridgeReady": False,
            "bridgePluginId": None,
            "managedChannels": [],
            "installProvenance": "unknown",
            "installTrusted": False,
            "managedChannelsSource": "default",
                "configSource": "defaults",
                "handoffConfigured": False,
                "claimEnabled": False,
                "claimMissedReason": None,
                "lastClaimAt": None,
            "lastClaimAttemptAt": None,
            "lastClaimOutcome": None,
            "lastClaimDeclineReason": None,
            "lastClaimChannel": None,
            "lastClaimConversation": None,
            "lastClaimMessageId": None,
            "lastClaimAccountId": None,
            "lastClaimPayloadShape": None,
            "recentInboundProof": {},
            "assetSurface": self._build_asset_surface(runtime_state),
            "executionBoundary": {
                "summary": "PluginHostRuntime 只负责 bridge 与运行时观测；完整宿主状态会在后台刷新完成后切换为 live。",
                "localExecutionOwnedBy": ["chat", "extensions", "automation", "computer_use", "rpa"],
                "pluginHostDoesNotOwnLocalExecution": True,
            },
        }
        host_surface.setdefault("pluginsAllowConfigured", None)
        host_surface.setdefault("pluginsAllow", [])
        host_surface.setdefault("pluginsAllowExpected", [])
        host_surface.setdefault("pluginProvenanceWarnings", [])
        host_surface.setdefault("fieldContractWarnings", [])
        host_surface.setdefault("bridgeDoctorSummary", None)
        host_surface.setdefault("bridgeDoctorChecks", [])
        return {
            "pluginRoot": str(payload.get("pluginRoot") or self.managed_local_root()),
            "pluginExtensionsRoot": str(payload.get("pluginExtensionsRoot") or (self.managed_local_root() / "extensions")),
            "runtimeConfig": runtime_config,
            "hostSurface": host_surface,
            "controlSurface": self._control_surface(runtime_config=runtime_config),
            "plugins": [self._public_plugin_snapshot_item(plugin) for plugin in plugins],
            "summary": {
                "pluginCount": len(plugins),
                "activeCount": sum(1 for item in plugins if str(item.get("activationState") or "").strip() == "active"),
                "channelPluginCount": sum(1 for item in plugins if str(item.get("pluginType") or "").strip() == "channel"),
                "pendingJobCount": 0,
            },
        }

    def _set_cached_public_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(dict(snapshot or {}))
        self._cached_public_snapshot = current
        return current

    @staticmethod
    def _summarize_public_plugins(plugins: list[dict[str, Any]]) -> dict[str, Any]:
        normalized_plugins = [dict(item) for item in list(plugins or []) if isinstance(item, dict)]
        return {
            "pluginCount": len(normalized_plugins),
            "activeCount": sum(1 for item in normalized_plugins if str(item.get("activationState") or "").strip().lower() == "active"),
            "channelPluginCount": sum(1 for item in normalized_plugins if str(item.get("pluginType") or "").strip().lower() == "channel"),
            "pendingJobCount": 0,
        }

    @staticmethod
    def _channel_surface_evidence(
        *,
        registered_accounts: list[str] | None = None,
        live_inbound_proven: bool = False,
        reply_delivered: bool = False,
    ) -> list[str]:
        evidence: list[str] = []
        if list(registered_accounts or []):
            evidence.append("registered_accounts")
        if live_inbound_proven:
            evidence.append("live_inbound")
        if reply_delivered:
            evidence.append("reply_delivered")
        return evidence

    @staticmethod
    def _public_plugin_channel_surface(plugin: dict[str, Any]) -> dict[str, Any]:
        current_surface = dict(plugin.get("channelSurface") or {}) if isinstance(plugin.get("channelSurface"), dict) else {}
        channel_ids: list[str] = []
        for source in (
            list(current_surface.get("channelIds") or []),
            list(((plugin.get("capabilitySurface") or {}).get("channels") or [])),
            list(((plugin.get("capabilities") or {}).get("channels") or [])),
            list(((plugin.get("manifestSummary") or {}).get("channels") or [])),
        ):
            for item in source:
                normalized = _normalize_openclaw_channel_id(str(item).strip())
                if normalized:
                    channel_ids.append(normalized)
        registered_accounts = [
            str(item).strip()
            for item in list(current_surface.get("registeredAccounts") or [])
            if str(item).strip()
        ]
        live_inbound_proven = bool(current_surface.get("liveInboundProven"))
        reply_delivered = bool(current_surface.get("replyDelivered"))
        evidence = [
            str(item).strip()
            for item in list(current_surface.get("evidence") or [])
            if str(item).strip()
        ]
        if not evidence:
            evidence = PluginHostService._channel_surface_evidence(
                registered_accounts=registered_accounts,
                live_inbound_proven=live_inbound_proven,
                reply_delivered=reply_delivered,
            )
        configured = bool(current_surface.get("configured")) or bool(registered_accounts) or live_inbound_proven or reply_delivered
        return {
            "channelIds": list(dict.fromkeys(channel_ids)),
            "registeredAccounts": list(dict.fromkeys(registered_accounts)),
            "configured": configured,
            "liveInboundProven": live_inbound_proven,
            "replyDelivered": reply_delivered,
            "evidence": evidence,
        }

    @staticmethod
    def _public_channel_target(plugin: dict[str, Any]) -> str | None:
        channel_surface = PluginHostService._public_plugin_channel_surface(plugin)
        for channel_id in list(channel_surface.get("channelIds") or []):
            normalized = _normalize_openclaw_channel_id(str(channel_id).strip())
            if normalized:
                return normalized
        plugin_id = _normalize_openclaw_channel_id(str(plugin.get("pluginId") or "").strip())
        return plugin_id or None

    def _managed_local_gateway_health_quick(self, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        fallback_payload = dict(fallback or {})
        env = self._managed_local_env()
        cli_source = self._openclaw_cli_source(env)
        launcher_source, launcher_missing = self._gateway_launcher_source()
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            return {
                "runtime": {
                    "status": "missing_cli",
                    "detail": "当前宿主未解析到 openclaw CLI。",
                },
                "rpc": {
                    "ok": False,
                    "error": "当前宿主未解析到 openclaw CLI。",
                },
                "health": {
                    "healthy": False,
                },
                "warnings": ["当前宿主未解析到 openclaw CLI。"],
                "error": "当前宿主未解析到 openclaw CLI。",
                "cliSource": cli_source,
                "launcherSource": launcher_source,
                "launcherMissing": launcher_missing,
                "processSummary": dict(fallback_payload.get("processSummary") or {}),
            }
        try:
            payload = self._openclaw_gateway_request_json(suffix="/health", timeout=4)
        except Exception as exc:
            if fallback_payload:
                preserved = dict(fallback_payload)
                preserved["cliSource"] = preserved.get("cliSource") or cli_source
                preserved["launcherSource"] = preserved.get("launcherSource") or launcher_source
                preserved["launcherMissing"] = bool(preserved.get("launcherMissing", launcher_missing))
                return preserved
            return self._synthetic_gateway_health(status="unreachable", reason=str(exc).strip() or exc.__class__.__name__)

        healthy = bool(payload.get("ok")) and str(payload.get("status") or "").strip().lower() in {"live", "ok", "healthy"}
        runtime_status = "running" if healthy else "unknown"
        runtime_detail = None if healthy else "Gateway /health 探针已返回，但未报告 healthy 状态。"
        return {
            "runtime": {
                "status": runtime_status,
                "detail": runtime_detail,
            },
            "rpc": {
                "ok": healthy,
                "error": None if healthy else runtime_detail,
                "url": str(payload.get("url") or "").strip() or None,
            },
            "health": {
                "healthy": healthy,
            },
            "warnings": [],
            "error": None if healthy else runtime_detail,
            "cliSource": cli_source,
            "launcherSource": launcher_source,
            "launcherMissing": launcher_missing,
            "processSummary": dict(fallback_payload.get("processSummary") or {}),
        }

    def _refresh_public_plugins_with_live_evidence(
        self,
        plugins: list[dict[str, Any]],
        *,
        recent_inbound_proof: dict[str, Any] | None = None,
        host_surface: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.is_external_host():
            return [copy.deepcopy(dict(item)) for item in list(plugins or []) if isinstance(item, dict)]
        host_surface_payload = dict(host_surface or {})
        managed_channels = {
            str(item).strip()
            for item in list(host_surface_payload.get("managedChannels") or [])
            if str(item).strip()
        }
        bridge_ready = bool(host_surface_payload.get("bridgeReady"))
        bridge_status_stale = bool(host_surface_payload.get("bridgeStatusStale"))
        inbound_ownership = str(host_surface_payload.get("inboundOwnership") or "delegated").strip() or "delegated"
        handoff_ready = bool(host_surface_payload.get("handoffReady"))
        channel_accounts_state: dict[str, list[str]] = {}
        state_root = self.managed_local_root()
        known_channel_ids = sorted(
            {
                str(channel_id).strip()
                for plugin in list(plugins or [])
                if isinstance(plugin, dict)
                for channel_id in list(self._public_plugin_channel_surface(plugin).get("channelIds") or [])
                if str(channel_id).strip()
            }
        )
        for channel_id in known_channel_ids:
            accounts_path = state_root / channel_id / "accounts.json"
            if not accounts_path.exists():
                continue
            try:
                payload = json.loads(accounts_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, list):
                continue
            channel_accounts_state[channel_id] = [
                str(item).strip()
                for item in payload
                if str(item).strip()
            ]
        proof = dict(recent_inbound_proof or {})
        recent_inbound_channel = _normalize_openclaw_channel_id(str(proof.get("channelType") or "").strip())
        reply_delivered = bool(proof.get("replyDelivered"))
        ownership_proven = bool(proof.get("ownershipProven"))
        refreshed: list[dict[str, Any]] = []
        for plugin in list(plugins or []):
            if not isinstance(plugin, dict):
                continue
            current = copy.deepcopy(dict(plugin))
            channel_surface = self._public_plugin_channel_surface(current)
            registered_accounts: list[str] = list(channel_surface.get("registeredAccounts") or [])
            for channel_id in list(channel_surface.get("channelIds") or []):
                registered_accounts.extend(list(channel_accounts_state.get(str(channel_id).strip()) or []))
            registered_accounts = list(dict.fromkeys(str(item).strip() for item in registered_accounts if str(item).strip()))
            plugin_channel_target = self._public_channel_target(current)
            live_channel_proven = bool(
                str(current.get("pluginType") or "").strip().lower() == "channel"
                and plugin_channel_target
                and recent_inbound_channel
                and plugin_channel_target == recent_inbound_channel
                and (ownership_proven or reply_delivered)
            )
            channel_surface["registeredAccounts"] = registered_accounts
            channel_surface["liveInboundProven"] = live_channel_proven
            channel_surface["replyDelivered"] = bool(live_channel_proven and reply_delivered)
            channel_surface["configured"] = bool(registered_accounts) or live_channel_proven or bool(channel_surface.get("replyDelivered"))
            channel_surface["evidence"] = self._channel_surface_evidence(
                registered_accounts=registered_accounts,
                live_inbound_proven=bool(channel_surface.get("liveInboundProven")),
                reply_delivered=bool(channel_surface.get("replyDelivered")),
            )
            current["channelSurface"] = channel_surface
            if str(current.get("pluginType") or "").strip().lower() == "channel" and bool(channel_surface.get("configured")):
                if str(current.get("setupState") or "").strip().lower() in {"", "installed", "setup_pending", "needs_user_action", "failed"}:
                    current["setupState"] = "onboarded"
                if str(current.get("activationState") or "").strip().lower() != "disabled":
                    current["activationState"] = "active"
                if str(current.get("healthState") or "").strip().lower() in {"", "unknown", "setup_pending", "needs_user_action", "handoff_unready", "failed"}:
                    current["healthState"] = "healthy"
            onboarding_completed = bool(
                str(current.get("setupState") or "").strip().lower() == "onboarded"
                and {
                    str(item).strip()
                    for item in list(channel_surface.get("evidence") or [])
                    if str(item).strip()
                }
                & {"live_inbound", "reply_delivered"}
            )
            current["onboardingCompleted"] = onboarding_completed
            if str(current.get("pluginType") or "").strip().lower() == "channel":
                channel_target = self._public_channel_target(current)
                runtime_transport_ready = bool(
                    channel_target
                    and (
                        channel_target in managed_channels
                        or inbound_ownership == "v8_owned"
                        or handoff_ready
                        or onboarding_completed
                    )
                    and (bridge_ready or bridge_status_stale or inbound_ownership == "v8_owned" or handoff_ready or onboarding_completed)
                )
                if runtime_transport_ready:
                    current["supportTier"] = "transport-hosted"
                    current["familyAdapterReady"] = True
                    filtered_reasons = [
                        str(reason).strip()
                        for reason in list(current.get("unavailableReasons") or [])
                        if str(reason).strip()
                        and str(reason).strip() not in {
                            "插件已安装，但还未完成接入向导或配置。",
                            "当前插件尚未具备 V8-owned inbound handoff，不能作为受管 transport-hosted 插件运行。",
                        }
                    ]
                    current["unavailableReasons"] = filtered_reasons
            refreshed.append(current)
        return refreshed

    def _fast_refresh_public_snapshot(self) -> dict[str, Any]:
        current = copy.deepcopy(dict(self._cached_public_snapshot or self._minimal_public_snapshot()))
        runtime_config = dict(current.get("runtimeConfig") or self.get_runtime_config())
        current["runtimeConfig"] = runtime_config
        current["controlSurface"] = dict(current.get("controlSurface") or self._control_surface(runtime_config=runtime_config))
        current_plugins = [dict(item) for item in list(current.get("plugins") or []) if isinstance(item, dict)]
        current_plugins.sort(key=lambda item: str(item.get("displayName") or item.get("pluginId") or "").lower())
        current["plugins"] = current_plugins
        current["summary"] = self._summarize_public_plugins(current_plugins)
        host_surface = dict(current.get("hostSurface") or {})
        if self.is_external_host():
            current["hostSurface"] = host_surface
            return current

        runtime_enabled = bool(runtime_config.get("enabled", True))
        allowed_families = {
            str(item).strip().lower()
            for item in list(runtime_config.get("allowedFamilies") or [])
            if str(item).strip()
        }
        primary_channel_candidates: list[tuple[int, str]] = []
        for plugin in current_plugins:
            if str(plugin.get("pluginType") or "").strip().lower() != "channel":
                continue
            channel_target = self._public_channel_target(plugin)
            if not channel_target:
                continue
            priority = 0 if bool((plugin.get("channelSurface") or {}).get("configured")) else 50
            primary_channel_candidates.append((priority, channel_target))
        primary_channel = sorted(primary_channel_candidates, key=lambda item: (item[0], item[1]))[0][1] if primary_channel_candidates else None
        recent_inbound_proof = self._latest_inbound_execution_proof(channel_type=primary_channel)
        handoff_audit = self._recent_openclaw_handoff_audit(channel_type=primary_channel)
        if (
            self._managed_local_bridge_read_only()
            and not bool((handoff_audit or {}).get("observedInbound"))
            and not bool((recent_inbound_proof or {}).get("ownershipProven"))
        ):
            recent_inbound_proof = {}
        bridge_state = self._managed_local_bridge_state(refresh=True, deep_inspect=False)
        handoff_audit = self._normalize_handoff_audit(
            handoff_audit=handoff_audit,
            recent_inbound_proof=recent_inbound_proof,
            bridge_state=bridge_state,
        )
        if not runtime_enabled:
            gateway_health = self._synthetic_gateway_health(
                status="cold_stopped",
                reason="PluginHostRuntime 已关闭，当前不保活 gateway。",
            )
        elif "channel" not in allowed_families:
            gateway_health = self._synthetic_gateway_health(
                status="family_disabled",
                reason="当前宿主未允许 channel 家族接管，gateway 数据面不参与运行。",
            )
        else:
            gateway_health = self._managed_local_gateway_health_quick(fallback=dict(host_surface.get("gatewayHealth") or {}))
        effective_ownership, effective_handoff_ready, effective_reason, effective_handoff_at = self._derive_inbound_ownership(
            runtime_enabled=runtime_enabled,
            family_allowed="channel" in allowed_families,
            handoff_ready=bool(bridge_state.get("handoffConfigured")) or bool(host_surface.get("handoffReady")),
            default_ownership=str(host_surface.get("inboundOwnership") or "delegated").strip() or "delegated",
            recent_inbound_proof=recent_inbound_proof,
            handoff_audit=handoff_audit,
        )
        host_surface["bridgeReady"] = bridge_state.get("bridgeReady")
        host_surface["bridgePluginId"] = str(bridge_state.get("pluginId") or "").strip() or None
        host_surface["managedChannels"] = [
            str(item).strip()
            for item in list(bridge_state.get("managedChannels") or [])
            if str(item).strip()
        ]
        host_surface["installProvenance"] = str(bridge_state.get("installProvenance") or "").strip() or "unknown"
        host_surface["installTrusted"] = bool(bridge_state.get("installTrusted"))
        host_surface["managedChannelsSource"] = str(bridge_state.get("managedChannelsSource") or "").strip() or "default"
        host_surface["configSource"] = str(bridge_state.get("configSource") or "").strip() or "defaults"
        host_surface["refreshMode"] = str(bridge_state.get("refreshMode") or "").strip() or "hot"
        host_surface["resolvedStateDir"] = str(bridge_state.get("resolvedStateDir") or "").strip() or None
        host_surface["gatewayBaseUrl"] = str(bridge_state.get("gatewayBaseUrl") or "").strip() or None
        host_surface["v8InboundUrl"] = str(bridge_state.get("v8InboundUrl") or "").strip() or None
        host_surface["handoffConfigured"] = bool(bridge_state.get("handoffConfigured"))
        host_surface["claimEnabled"] = bool(bridge_state.get("claimEnabled"))
        host_surface["lastClaimAt"] = bridge_state.get("lastClaimAt")
        host_surface["lastClaimAttemptAt"] = bridge_state.get("lastClaimAttemptAt")
        host_surface["lastClaimOutcome"] = bridge_state.get("lastClaimOutcome")
        host_surface["lastClaimDeclineReason"] = bridge_state.get("lastClaimDeclineReason")
        host_surface["lastClaimChannel"] = bridge_state.get("lastClaimChannel")
        host_surface["lastClaimConversation"] = bridge_state.get("lastClaimConversation")
        host_surface["lastClaimMessageId"] = bridge_state.get("lastClaimMessageId")
        host_surface["lastClaimAccountId"] = bridge_state.get("lastClaimAccountId")
        host_surface["lastClaimPayloadShape"] = bridge_state.get("lastClaimPayloadShape")
        host_surface["bridgeStatusSource"] = bridge_state.get("bridgeStatusSource")
        host_surface["bridgeStatusObservedAt"] = bridge_state.get("bridgeStatusObservedAt")
        host_surface["bridgeStatusMs"] = bridge_state.get("bridgeStatusMs")
        host_surface["bridgeStatusError"] = bridge_state.get("bridgeStatusError")
        host_surface["bridgeStatusStale"] = bool(bridge_state.get("bridgeStatusStale"))
        host_surface["gatewayHealth"] = gateway_health
        host_surface["outboundReady"] = bool(runtime_enabled and "channel" in allowed_families and bool((gateway_health.get("health") or {}).get("healthy")))
        host_surface["inboundOwnership"] = effective_ownership
        host_surface["handoffReady"] = effective_handoff_ready
        if bool((recent_inbound_proof or {}).get("ownershipProven")):
            host_surface["handoffDrift"] = False
        elif handoff_audit:
            host_surface["handoffDrift"] = bool(handoff_audit.get("handoffDrift"))
        host_surface["lastInboundHandoffAt"] = (
            effective_handoff_at
            or host_surface.get("lastInboundHandoffAt")
            or recent_inbound_proof.get("inboundObservedAt")
        )
        if effective_reason:
            handoff_audit = {**handoff_audit, "reason": effective_reason}
        host_surface["handoffAudit"] = handoff_audit or None
        host_surface["recentInboundProof"] = recent_inbound_proof
        refreshed_plugins = self._refresh_public_plugins_with_live_evidence(
            current_plugins,
            recent_inbound_proof=recent_inbound_proof,
            host_surface=host_surface,
        )
        current["plugins"] = refreshed_plugins
        current["summary"] = self._summarize_public_plugins(refreshed_plugins)
        current["hostSurface"] = host_surface
        return current

    def _touch_cached_public_snapshot_from_runtime_state(self, *, last_inbound_handoff_at: str | None = None) -> None:
        if self._cached_public_snapshot is None:
            return
        current = copy.deepcopy(dict(self._cached_public_snapshot or {}))
        host_surface = dict(current.get("hostSurface") or {})
        if last_inbound_handoff_at:
            host_surface["lastInboundHandoffAt"] = last_inbound_handoff_at
        current["hostSurface"] = host_surface
        self._set_cached_public_snapshot(current)

    def _mark_snapshot_refreshing(self, *, preserve_error: bool = False) -> None:
        self._startup_state = "refreshing"
        self._snapshot_freshness = "cached"
        if not preserve_error:
            self._last_refresh_error = None

    def _background_refresh_requested(self) -> bool:
        return bool(self.scan_on_startup())

    def _prepare_registry_for_snapshot(self, *, refresh_registry: bool) -> dict[str, Any]:
        if not self.is_enabled():
            return default_plugin_registry()
        if self.is_external_host():
            return default_plugin_registry()
        self.managed_local_root().mkdir(parents=True, exist_ok=True)
        (self.managed_local_root() / "extensions").mkdir(parents=True, exist_ok=True)
        bridge_read_only = self._managed_local_bridge_read_only()
        if not bridge_read_only:
            try:
                self._ensure_managed_local_bridge_extension_link()
            except Exception:
                pass
        registry = scan_plugin_registry() if refresh_registry else default_plugin_registry()
        registry = self._prune_managed_local_registry_noise(registry)
        if not bridge_read_only:
            self._sync_managed_local_plugins_allowlist(payload=registry)
        self._save_runtime_state({"lifecycleAuthority": "manual_local"}, payload=registry)
        return registry

    def _refresh_snapshot_blocking(self, *, refresh_registry: bool) -> dict[str, Any]:
        response_snapshot: dict[str, Any] | None = None
        with self._snapshot_refresh_lock:
            self._refresh_in_flight = True
            try:
                if self.is_external_host():
                    self._save_runtime_state({"lifecycleAuthority": "external_managed"})
                    snapshot = self.build_snapshot(refresh_live_state=True)
                else:
                    registry = self._prepare_registry_for_snapshot(refresh_registry=refresh_registry)
                    snapshot = self.build_snapshot(registry, refresh_live_state=True)
                self._startup_state = "ready"
                self._snapshot_freshness = "live"
                self._last_refresh_error = None
                refreshed_at = _now_iso()
                self._last_refresh_at = refreshed_at
                self._last_live_refresh_at = refreshed_at
                self._last_deep_refresh_at = refreshed_at
                response_snapshot = self._set_cached_public_snapshot(self._public_snapshot_from_full(snapshot))
            except Exception as exc:
                self._startup_state = "error"
                self._snapshot_freshness = "cached"
                self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                refreshed_at = _now_iso()
                self._last_refresh_at = refreshed_at
                self._last_deep_refresh_at = refreshed_at
                if self._cached_public_snapshot is None:
                    self._set_cached_public_snapshot(self._minimal_public_snapshot())
                raise
            finally:
                self._refresh_in_flight = False
        return self._decorate_public_snapshot(response_snapshot or dict(self._cached_public_snapshot or {}))

    async def _refresh_snapshot_async(self, *, refresh_registry: bool) -> None:
        try:
            await asyncio.to_thread(self._refresh_snapshot_blocking, refresh_registry=refresh_registry)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _schedule_background_refresh(self, *, refresh_registry: bool) -> None:
        task = self._background_refresh_task
        if task and not task.done():
            return
        self._background_refresh_task = asyncio.create_task(self._refresh_snapshot_async(refresh_registry=refresh_registry))

    def get_runtime_config(self) -> dict[str, Any]:
        return storage.get_plugin_host_config()

    def is_enabled(self) -> bool:
        return bool(self.get_runtime_config().get("enabled", True))

    def scan_on_startup(self) -> bool:
        return bool(self.get_runtime_config().get("scanOnStartup", True))

    def allowed_families(self) -> list[str]:
        return [str(item).strip() for item in list(self.get_runtime_config().get("allowedFamilies") or []) if str(item).strip()]

    def family_allowed(self, family: str | None) -> bool:
        normalized = str(family or "plugin").strip().lower() or "plugin"
        return normalized in {item.lower() for item in self.allowed_families()}

    def host_mode(self) -> str:
        return str(self.get_runtime_config().get("hostMode") or "managed_local").strip().lower() or "managed_local"

    def is_managed_local(self) -> bool:
        return self.host_mode() == "managed_local"

    def is_external_host(self) -> bool:
        return self.host_mode() == "external"

    def managed_local_root(self) -> Path:
        config = self.get_runtime_config()
        managed_local = dict(config.get("managedLocal") or {})
        normalized = str(managed_local.get("rootDir") or OPENCLAW_DEFAULT_STATE_ROOT).strip() or str(OPENCLAW_DEFAULT_STATE_ROOT)
        return Path(normalized).expanduser()

    def _default_managed_local_tooling_root(self) -> Path | None:
        root_dir = self.managed_local_root()
        candidates = [
            root_dir,
            root_dir / "tooling" / "openclaw-cli",
        ]
        for candidate in candidates:
            if any(path.exists() for path in self._tooling_cli_candidates(candidate)):
                return candidate
            if any(path.exists() for path in self._tooling_package_root_candidates(candidate)):
                return candidate
        return None

    def managed_local_tooling_root(self) -> Path | None:
        config = self.get_runtime_config()
        managed_local = dict(config.get("managedLocal") or {})
        raw_value = managed_local.get("toolingRoot")
        if raw_value is None:
            return self._default_managed_local_tooling_root()
        normalized = str(raw_value).strip()
        if not normalized:
            return None
        return Path(normalized).expanduser()

    def _managed_local_tooling_root_explicitly_set(self) -> bool:
        config = self.get_runtime_config()
        managed_local = dict(config.get("managedLocal") or {})
        raw_value = managed_local.get("toolingRoot")
        return raw_value is not None and bool(str(raw_value).strip())

    def managed_local_launcher_path(self) -> Path | None:
        config = self.get_runtime_config()
        managed_local = dict(config.get("managedLocal") or {})
        raw_value = managed_local.get("launcherPath")
        if raw_value is None:
            default_launcher = self.managed_local_root() / "gateway.cmd"
            return default_launcher if default_launcher.exists() else None
        normalized = str(raw_value).strip()
        if not normalized:
            return None
        return Path(normalized).expanduser()

    def _managed_local_bridge_read_only(self) -> bool:
        return False

    def _managed_local_config_path(self) -> Path:
        return self.managed_local_root() / "openclaw.json"

    @staticmethod
    def _load_openclaw_config_candidate(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return dict(payload) if isinstance(payload, dict) else None

    @staticmethod
    def _score_openclaw_config_candidate(payload: dict[str, Any], path: Path) -> int:
        if not isinstance(payload, dict):
            return -1
        score = 0
        gateway = dict(payload.get("gateway") or {})
        channels = dict(payload.get("channels") or {})
        plugins = dict(payload.get("plugins") or {})
        commands = dict(payload.get("commands") or {})
        agents = dict(payload.get("agents") or {})
        defaults = dict(agents.get("defaults") or {})
        allow = [str(item).strip() for item in list(plugins.get("allow") or []) if str(item).strip()]
        if str(gateway.get("mode") or "").strip():
            score += 40
        if str(gateway.get("bind") or "").strip():
            score += 8
        if channels:
            score += 24 + min(8, len(channels)) * 2
        if allow:
            score += 12 + min(8, len(allow))
        if commands:
            score += 8
        if str(defaults.get("workspace") or "").strip():
            score += 8
        if isinstance(defaults.get("skipBootstrap"), bool):
            score += 2
        try:
            score += min(12, int(path.stat().st_size // 256))
        except Exception:
            pass
        return score

    def _managed_local_config_recovery_candidates(self) -> list[Path]:
        config_path = self._managed_local_config_path()
        state_dir = config_path.parent
        if not state_dir.exists():
            return []
        candidates: list[Path] = []
        for pattern in ("openclaw.json.bak*", "openclaw.json.clobbered.*"):
            for candidate in state_dir.glob(pattern):
                if candidate == config_path or not candidate.is_file():
                    continue
                candidates.append(candidate)
        seen: set[str] = set()
        deduped: list[Path] = []
        for candidate in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _recover_managed_local_openclaw_config_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        current = dict(payload or {})
        best_candidate: tuple[int, float, Path, dict[str, Any]] | None = None
        channel_candidates: dict[str, tuple[int, float, Path, dict[str, Any]]] = {}
        for candidate_path in self._managed_local_config_recovery_candidates():
            candidate_payload = self._load_openclaw_config_candidate(candidate_path)
            if not candidate_payload:
                continue
            score = self._score_openclaw_config_candidate(candidate_payload, candidate_path)
            if score < 0:
                continue
            try:
                mtime = candidate_path.stat().st_mtime
            except Exception:
                mtime = 0.0
            entry = (score, mtime, candidate_path, candidate_payload)
            if best_candidate is None or (score, mtime) > (best_candidate[0], best_candidate[1]):
                best_candidate = entry
            for channel_id, channel_payload in dict(candidate_payload.get("channels") or {}).items():
                normalized_channel_id = str(channel_id).strip()
                if not normalized_channel_id or not isinstance(channel_payload, dict):
                    continue
                existing = channel_candidates.get(normalized_channel_id)
                if existing is None or (score, mtime) > (existing[0], existing[1]):
                    channel_candidates[normalized_channel_id] = entry
        if best_candidate is None:
            return current, False

        baseline = dict(best_candidate[3] or {})
        changed = False

        current_channels = dict(current.get("channels") or {})
        baseline_channels = dict(baseline.get("channels") or {})
        if not current_channels and baseline_channels:
            current["channels"] = copy.deepcopy(baseline_channels)
            changed = True
            current_channels = dict(current.get("channels") or {})
        for channel_id, entry in channel_candidates.items():
            if channel_id in current_channels:
                continue
            candidate_payload = dict(entry[3] or {})
            candidate_channels = dict(candidate_payload.get("channels") or {})
            candidate_channel_payload = candidate_channels.get(channel_id)
            if not isinstance(candidate_channel_payload, dict):
                continue
            current_channels[channel_id] = copy.deepcopy(candidate_channel_payload)
            changed = True
        if current_channels:
            current["channels"] = current_channels

        current_gateway = dict(current.get("gateway") or {})
        baseline_gateway = dict(baseline.get("gateway") or {})
        for key in ("mode", "bind", "remote", "tailscale"):
            current_value = current_gateway.get(key)
            if current_value not in (None, ""):
                continue
            baseline_value = baseline_gateway.get(key)
            if baseline_value in (None, ""):
                continue
            current_gateway[key] = copy.deepcopy(baseline_value)
            changed = True
        if current_gateway:
            current["gateway"] = current_gateway

        current_tools = dict(current.get("tools") or {})
        baseline_tools = dict(baseline.get("tools") or {})
        if not current_tools and baseline_tools:
            current["tools"] = copy.deepcopy(baseline_tools)
            changed = True

        current_meta = dict(current.get("meta") or {})
        baseline_meta = dict(baseline.get("meta") or {})
        if not current_meta and baseline_meta:
            current["meta"] = copy.deepcopy(baseline_meta)
            changed = True

        return current, changed

    @staticmethod
    def _preserve_missing_openclaw_sections(
        payload: dict[str, Any],
        baseline: dict[str, Any],
    ) -> dict[str, Any]:
        current = dict(payload or {})
        reference = dict(baseline or {})

        for key in ("tools", "meta", "channels"):
            if key not in current and reference.get(key) is not None:
                current[key] = copy.deepcopy(reference.get(key))

        baseline_gateway = dict(reference.get("gateway") or {})
        current_gateway = dict(current.get("gateway") or {})
        if baseline_gateway:
            for key in ("mode", "bind", "remote", "tailscale"):
                if current_gateway.get(key) in (None, "") and baseline_gateway.get(key) not in (None, ""):
                    current_gateway[key] = copy.deepcopy(baseline_gateway.get(key))
            if current_gateway:
                current["gateway"] = current_gateway

        return current

    def _normalize_profile_backed_channel_config(
        self,
        *,
        plugin_id: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        profile_key = resolve_plugin_profile_key(plugin_id=str(plugin_id or "").strip() or None)
        current_payload = dict(payload or {})
        if profile_key not in {"discord", "feishu"}:
            return current_payload, False
        normalized_payload, _field_errors, _normalized_preview, _validation_mode = self._normalize_plugin_config_values(
            {"pluginId": str(plugin_id or "").strip()},
            current_payload,
        )
        return normalized_payload, normalized_payload != current_payload

    def _normalize_managed_local_openclaw_config_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        normalized_payload = dict(payload or {})
        channels_payload = dict(normalized_payload.get("channels") or {})
        normalized_channels: dict[str, Any] = {}
        changed = False

        for channel_id, raw_channel_payload in channels_payload.items():
            if not isinstance(raw_channel_payload, dict):
                normalized_channels[str(channel_id)] = raw_channel_payload
                continue

            current_channel_payload = dict(raw_channel_payload)
            top_level_payload = {key: value for key, value in current_channel_payload.items() if key != "accounts"}
            normalized_channel_payload, channel_changed = self._normalize_profile_backed_channel_config(
                plugin_id=str(channel_id),
                payload=top_level_payload,
            )

            accounts_payload = current_channel_payload.get("accounts")
            if isinstance(accounts_payload, dict):
                normalized_accounts: dict[str, Any] = {}
                for account_id, raw_account_payload in accounts_payload.items():
                    if isinstance(raw_account_payload, dict):
                        normalized_account_payload, account_changed = self._normalize_profile_backed_channel_config(
                            plugin_id=str(channel_id),
                            payload=dict(raw_account_payload),
                        )
                        normalized_accounts[str(account_id)] = normalized_account_payload
                        changed = changed or account_changed
                    else:
                        normalized_accounts[str(account_id)] = raw_account_payload
                if normalized_accounts:
                    normalized_channel_payload["accounts"] = normalized_accounts
            elif "accounts" in current_channel_payload:
                normalized_channel_payload["accounts"] = accounts_payload

            normalized_channels[str(channel_id)] = normalized_channel_payload
            changed = changed or channel_changed or normalized_channel_payload != current_channel_payload

        if normalized_channels:
            normalized_payload["channels"] = normalized_channels
        elif "channels" in normalized_payload and normalized_payload.get("channels") != normalized_channels:
            normalized_payload["channels"] = normalized_channels
            changed = True

        return normalized_payload, changed

    def _repair_managed_local_openclaw_config(self) -> tuple[dict[str, Any], bool]:
        config_path = self._managed_local_config_path()
        if not config_path.exists():
            return {}, False
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}, False
        if not isinstance(payload, dict):
            return {}, False
        normalized_payload, normalized_changed = self._normalize_managed_local_openclaw_config_payload(dict(payload))
        changed = normalized_changed
        if changed and not self._managed_local_bridge_read_only():
            self._write_managed_local_openclaw_config(normalized_payload)
        return normalized_payload, changed and not self._managed_local_bridge_read_only()

    def _read_managed_local_openclaw_config_raw(self) -> dict[str, Any]:
        config_path = self._managed_local_config_path()
        if not config_path.exists():
            return {}
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _read_managed_local_openclaw_config(self) -> dict[str, Any]:
        payload = self._read_managed_local_openclaw_config_raw()
        normalized_payload, _changed = self._normalize_managed_local_openclaw_config_payload(dict(payload))
        return dict(normalized_payload) if isinstance(normalized_payload, dict) else {}

    @staticmethod
    def _extract_json_payload_from_output(raw: str) -> Any:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        for marker in ("{", "["):
            position = text.find(marker)
            while position >= 0:
                candidate = text[position:].strip()
                try:
                    return json.loads(candidate)
                except Exception:
                    position = text.find(marker, position + 1)
        return None

    def _run_openclaw_json_command(self, *args: str, timeout: int = 30) -> Any:
        env = self._managed_local_env()
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            raise RuntimeError("当前宿主无法解析 openclaw CLI。")
        windows_node_argv = self._resolve_windows_node_openclaw_argv(env, *args)
        argv = (
            windows_node_argv
            or (
                self._wrap_windows_executable_argv(cli_executable, *args)
                if os.name == "nt"
                else [cli_executable, *args]
            )
        )
        completed = subprocess.run(
            argv,
            cwd=str(self.managed_local_root()),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        payload = self._extract_json_payload_from_output(stdout) or self._extract_json_payload_from_output(stderr)
        if completed.returncode != 0 and payload is None:
            detail = stderr.strip() or stdout.strip() or f"returnCode={completed.returncode}"
            raise RuntimeError(f"OpenClaw 命令失败：{detail}")
        return payload

    def _managed_local_plugin_inspect(self, plugin_id: str, *, timeout: int = 45) -> dict[str, Any]:
        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            return {}
        try:
            payload = self._run_openclaw_json_command("plugins", "inspect", normalized_plugin_id, "--json", timeout=timeout)
            if isinstance(payload, dict):
                plugin_payload = dict(payload.get("plugin") or {})
                if str(plugin_payload.get("id") or "").strip() == normalized_plugin_id:
                    return dict(payload)
        except Exception:
            pass
        try:
            payload = self._run_openclaw_json_command("plugins", "inspect", "--all", "--json", timeout=max(timeout, 60))
        except Exception:
            return {}
        if not isinstance(payload, list):
            return {}
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            plugin_payload = dict(entry.get("plugin") or {})
            if str(plugin_payload.get("id") or "").strip() == normalized_plugin_id:
                return dict(entry)
        return {}

    def _derive_openclaw_plugin_provenance(
        self,
        *,
        inspect_payload: dict[str, Any] | None,
        plugin_id: str | None = None,
        openclaw_config: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(inspect_payload or {})
        plugin_payload = dict(payload.get("plugin") or {})
        normalized_plugin_id = str(plugin_id or plugin_payload.get("id") or "").strip()
        plugin_root_raw = str(plugin_payload.get("rootDir") or plugin_payload.get("source") or "").strip()
        plugin_origin = str(plugin_payload.get("origin") or "").strip().lower()
        diagnostics = [
            str(item.get("message") or "").strip().lower()
            for item in list(payload.get("diagnostics") or [])
            if isinstance(item, dict)
        ]
        config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        plugins_payload = dict(config_payload.get("plugins") or {})
        installs_payload = dict(plugins_payload.get("installs") or {})
        load_paths = list(dict(plugins_payload.get("load") or {}).get("paths") or [])
        plugin_root = Path(plugin_root_raw).expanduser() if plugin_root_raw else None

        def _candidate_matches(raw_path: Any) -> bool:
            if not plugin_root:
                return False
            path_text = str(raw_path or "").strip()
            if not path_text:
                return False
            candidate = Path(path_text).expanduser()
            return self._same_path(plugin_root, candidate) or self._path_within_root(plugin_root, candidate)

        for install_record in installs_payload.values():
            if not isinstance(install_record, dict):
                continue
            if _candidate_matches(install_record.get("installPath") or install_record.get("sourcePath")):
                return "install_record"

        for load_path in load_paths:
            if _candidate_matches(load_path):
                return "load_path"

        if plugin_root and (
            self._path_within_root(plugin_root, self._managed_local_extensions_root())
            or self._plugin_root_matches_managed_local_extension_slot(normalized_plugin_id, plugin_root)
        ):
            return "global_extensions_root"

        if not normalized_plugin_id or not plugin_payload:
            return "missing"
        if any("without install/load-path provenance" in item for item in diagnostics):
            return "global_auto_discovery"
        if plugin_origin in {"global", "global_auto_discovery"}:
            return "global_auto_discovery"
        if plugin_origin in {"load_path", "loadpath"}:
            return "load_path"
        if plugin_origin in {"install", "installed", "link", "linked", "npm"}:
            return "install_record"
        return "unknown"

    def _bridge_install_is_trusted(
        self,
        *,
        plugin_id: str | None,
        install_provenance: str | None,
        openclaw_config: dict[str, Any] | None = None,
        inspect_payload: dict[str, Any] | None = None,
    ) -> bool:
        provenance = str(install_provenance or "").strip().lower()
        if provenance in {"install_record", "load_path"}:
            return True
        plugin_payload = dict((inspect_payload or {}).get("plugin") or {})
        plugin_root_raw = str(plugin_payload.get("rootDir") or plugin_payload.get("source") or "").strip()
        config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        allowlist = {
            str(item).strip()
            for item in list(dict(config_payload.get("plugins") or {}).get("allow") or [])
            if str(item).strip()
        }
        normalized_plugin_id = str(plugin_id or "").strip()
        if normalized_plugin_id and normalized_plugin_id in allowlist:
            return True
        if plugin_root_raw:
            plugin_root = Path(plugin_root_raw).expanduser()
            if self._plugin_root_matches_managed_local_extension_slot(plugin_id, plugin_root):
                return False
        return False

    def _derive_bridge_config_source(
        self,
        *,
        plugin_id: str | None,
        openclaw_config: dict[str, Any] | None = None,
        route_payload: dict[str, Any] | None = None,
    ) -> str:
        route = dict(route_payload or {})
        if str(route.get("configSource") or "").strip():
            return str(route.get("configSource")).strip()
        if os.environ.get("V8_AGENT_OS_PLUGIN_HOST_INBOUND_URL") or os.environ.get("V8_AGENT_OS_PLUGIN_HOST_HANDOFF_TOKEN"):
            return "env"
        config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        entries = dict((config_payload.get("plugins") or {}).get("entries") or {})
        entry_payload = dict(entries.get(str(plugin_id or "").strip()) or {})
        entry_config = dict(entry_payload.get("config") or {})
        if entry_config:
            return "plugin_entry"
        return "defaults"

    def _derive_bridge_managed_channels_source(
        self,
        *,
        plugin_config: dict[str, Any] | None,
        openclaw_config: dict[str, Any] | None = None,
        route_payload: dict[str, Any] | None = None,
    ) -> str:
        route = dict(route_payload or {})
        if str(route.get("managedChannelsSource") or "").strip():
            return str(route.get("managedChannelsSource")).strip()
        if list(dict(plugin_config or {}).get("managedChannels") or []):
            return "plugin_config"
        channels = dict((dict(openclaw_config or self._read_managed_local_openclaw_config()).get("channels") or {}))
        if channels:
            return "openclaw_channels"
        return "default"

    def _managed_local_plugins_inventory_from_state_manifest(self) -> dict[str, Any]:
        config_payload = self._read_managed_local_openclaw_config_raw()
        plugins_payload = dict(config_payload.get("plugins") or {})
        allowlist = {
            str(item).strip()
            for item in list(plugins_payload.get("allow") or [])
            if str(item).strip()
        }
        entry_payload = dict(plugins_payload.get("entries") or {})
        installs_payload = dict(plugins_payload.get("installs") or {})
        plugins_by_id: dict[str, dict[str, Any]] = {}

        def _append_plugin_record(
            *,
            configured_plugin_id: str,
            install_path: str,
            source: str,
        ) -> None:
            if not install_path:
                return
            manifest_path = Path(install_path) / "openclaw.plugin.json"
            if not manifest_path.exists():
                return
            try:
                manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                return
            if not isinstance(manifest_payload, dict):
                return
            plugin_id = str(manifest_payload.get("id") or configured_plugin_id or "").strip()
            if not plugin_id:
                return
            entry_record = entry_payload.get(configured_plugin_id) or entry_payload.get(plugin_id) or {}
            enabled = bool(entry_record.get("enabled", True))
            allowed = not allowlist or plugin_id in allowlist or str(configured_plugin_id).strip() in allowlist
            loaded = enabled and allowed
            current = plugins_by_id.get(plugin_id)
            if current and str(current.get("source") or "").strip() not in {"extensions_root", "managed_local_extensions"}:
                return
            plugins_by_id[plugin_id] = {
                "id": plugin_id,
                "name": str(manifest_payload.get("name") or plugin_id).strip() or plugin_id,
                "description": str(manifest_payload.get("description") or f"{plugin_id} tool").strip()
                or f"{plugin_id} tool",
                "enabled": loaded,
                "status": "loaded" if loaded else "disabled",
                "toolNames": [
                    str(item).strip()
                    for item in list(manifest_payload.get("tools") or [])
                    if str(item).strip()
                ],
                "channels": [
                    str(item).strip()
                    for item in list(manifest_payload.get("channels") or [])
                    if str(item).strip()
                ],
                "source": source,
                "installPath": install_path,
            }

        for configured_plugin_id, install_record in installs_payload.items():
            if not isinstance(install_record, dict):
                continue
            install_path = (
                str(install_record.get("installPath") or "").strip()
                or str(install_record.get("sourcePath") or "").strip()
            )
            _append_plugin_record(
                configured_plugin_id=str(configured_plugin_id).strip(),
                install_path=install_path,
                source=str(install_record.get("source") or "").strip() or "state_manifest",
            )

        extensions_root = self._managed_local_extensions_root()
        if extensions_root.exists():
            for candidate in extensions_root.iterdir():
                if not candidate.is_dir():
                    continue
                _append_plugin_record(
                    configured_plugin_id=str(candidate.name).strip(),
                    install_path=str(candidate),
                    source="extensions_root",
                )

        return {"plugins": list(plugins_by_id.values())}

    def _managed_local_plugins_inventory(self, *, refresh: bool = False) -> dict[str, Any]:
        if self.is_external_host():
            return {"plugins": []}
        if (
            not refresh
            and self._openclaw_plugins_inventory_cache is not None
            and time.monotonic() - self._openclaw_plugins_inventory_cache_at < _OPENCLAW_PLUGIN_INVENTORY_TTL_SECONDS
        ):
            return dict(self._openclaw_plugins_inventory_cache)
        normalized = self._managed_local_plugins_inventory_from_state_manifest()
        plugins = [dict(item) for item in list(normalized.get("plugins") or []) if isinstance(item, dict)]
        if not plugins:
            payload = self._run_openclaw_json_command("plugins", "list", "--json", timeout=45)
            if isinstance(payload, dict):
                normalized = dict(payload)
            elif isinstance(payload, list):
                normalized = {"plugins": list(payload)}
            else:
                normalized = {"plugins": []}
        plugins = [dict(item) for item in list(normalized.get("plugins") or []) if isinstance(item, dict)]
        normalized["plugins"] = plugins
        self._openclaw_plugins_inventory_cache = dict(normalized)
        self._openclaw_plugins_inventory_cache_at = time.monotonic()
        return dict(normalized)

    def _read_bridge_status_hot_cache(self) -> tuple[dict[str, Any] | None, bool]:
        with self._bridge_status_hot_lock:
            if not self._bridge_status_hot_cache:
                return None, False
            cached_at, payload = self._bridge_status_hot_cache
            return copy.deepcopy(dict(payload)), self._monotonic_age_within(cached_at, _BRIDGE_STATUS_HOT_TTL_SECONDS)

    def _write_bridge_status_hot_cache(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(dict(payload or {}))
        current["bridgeStatusStale"] = False
        current["bridgeStatusObservedAt"] = str(current.get("bridgeStatusObservedAt") or _now_iso()).strip() or _now_iso()
        current["bridgeStatusSource"] = str(current.get("bridgeStatusSource") or "gateway_route").strip() or "gateway_route"
        current["bridgeStatusMs"] = max(0, int(current.get("bridgeStatusMs") or 0))
        current["bridgeStatusError"] = str(current.get("bridgeStatusError") or "").strip() or None
        with self._bridge_status_hot_lock:
            self._bridge_status_hot_cache = (time.monotonic(), current)
        return copy.deepcopy(current)

    @staticmethod
    def _mark_bridge_status_payload_stale(
        payload: dict[str, Any],
        *,
        error: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        current = copy.deepcopy(dict(payload or {}))
        current["bridgeStatusStale"] = True
        current["bridgeStatusSource"] = str(source or current.get("bridgeStatusSource") or "hot_cache").strip() or "hot_cache"
        current["bridgeStatusObservedAt"] = str(current.get("bridgeStatusObservedAt") or _now_iso()).strip() or _now_iso()
        current["bridgeStatusMs"] = max(0, int(current.get("bridgeStatusMs") or 0))
        current["bridgeStatusError"] = str(error or current.get("bridgeStatusError") or "").strip() or None
        current["refreshMode"] = "hot"
        return current

    def _schedule_bridge_status_hot_refresh(self) -> None:
        with self._bridge_status_hot_lock:
            if self._bridge_status_hot_refreshing:
                return
            self._bridge_status_hot_refreshing = True

        def _worker() -> None:
            try:
                self._managed_local_bridge_state(refresh=True, deep_inspect=False)
            finally:
                with self._bridge_status_hot_lock:
                    self._bridge_status_hot_refreshing = False

        self._schedule_daemon_thread(_worker)

    def _read_bridge_inventory_hot_cache(self) -> tuple[dict[str, Any] | None, bool]:
        with self._bridge_inventory_hot_lock:
            if not self._bridge_inventory_hot_cache:
                return None, False
            cached_at, payload = self._bridge_inventory_hot_cache
            return copy.deepcopy(dict(payload)), self._monotonic_age_within(cached_at, _BRIDGE_TOOL_CATALOG_TTL_SECONDS)

    def _write_bridge_inventory_hot_cache(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(dict(payload or {}))
        with self._bridge_inventory_hot_lock:
            self._bridge_inventory_hot_cache = (time.monotonic(), current)
        return copy.deepcopy(current)

    def _schedule_bridge_inventory_hot_refresh(self) -> None:
        with self._bridge_inventory_hot_lock:
            if self._bridge_inventory_hot_refreshing:
                return
            self._bridge_inventory_hot_refreshing = True

        def _worker() -> None:
            try:
                self._refresh_bridge_inventory_hot_cache()
            finally:
                with self._bridge_inventory_hot_lock:
                    self._bridge_inventory_hot_refreshing = False

        self._schedule_daemon_thread(_worker)

    def _refresh_bridge_inventory_hot_cache(self) -> dict[str, Any]:
        body = self._fetch_bridge_tool_inventory_payload()
        return self._write_bridge_inventory_hot_cache(body)

    def _fetch_bridge_tool_inventory_payload(self) -> dict[str, Any]:
        body: dict[str, Any] | None = None
        gateway_inventory_error: str | None = None
        try:
            body = self._openclaw_gateway_request_json(
                suffix="/plugins/openclaw-v8-bridge/tools",
                timeout=_BRIDGE_TOOL_INVENTORY_REFRESH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            gateway_inventory_error = str(exc).strip() or exc.__class__.__name__
        if not isinstance(body, dict) or not bool(body.get("ok")):
            log_fallback = self._infer_bridge_tool_inventory_from_openclaw_logs()
            if log_fallback:
                body = dict(log_fallback)
                if gateway_inventory_error and not body.get("gatewayCatalogError"):
                    body["gatewayCatalogError"] = gateway_inventory_error
            elif isinstance(body, dict) and not bool(body.get("ok")):
                detail = str(body.get("error") or body).strip() or "unknown bridge tools error"
                raise RuntimeError(f"OpenClaw V8 Bridge tools catalog 读取失败：{detail}")
            elif gateway_inventory_error:
                raise RuntimeError(f"OpenClaw V8 Bridge tools catalog 读取失败：{gateway_inventory_error}")
        return dict(body or {})

    def _managed_local_bridge_state(
        self,
        *,
        refresh: bool = False,
        deep_inspect: bool = False,
        inventory: dict[str, Any] | None = None,
        openclaw_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.is_external_host():
            return {
                "bridgeReady": False,
                "pluginId": None,
                "managedChannels": [],
                "installProvenance": "unknown",
                "managedChannelsSource": "default",
                "configSource": "defaults",
                "claimEnabled": False,
                "lastClaimAt": None,
                "lastClaimAttemptAt": None,
                "lastClaimOutcome": None,
                "lastClaimDeclineReason": None,
                "lastClaimChannel": None,
                "lastClaimConversation": None,
                "lastClaimMessageId": None,
                "lastClaimAccountId": None,
                "lastClaimPayloadShape": None,
                "failClosed": True,
                "toolAllowlistMode": "all",
                "toolAllowlist": [],
                "refreshMode": "hot",
            }
        cached_status_payload: dict[str, Any] | None = None
        cached_status_fresh = False
        if not deep_inspect:
            cached_status_payload, cached_status_fresh = self._read_bridge_status_hot_cache()
            if not refresh and cached_status_payload is not None:
                if cached_status_fresh:
                    return cached_status_payload
                self._schedule_bridge_status_hot_refresh()
                return self._mark_bridge_status_payload_stale(cached_status_payload)
        try:
            self._ensure_managed_local_bridge_extension_link()
        except Exception:
            pass
        inventory_payload = inventory if isinstance(inventory, dict) else self._managed_local_plugins_inventory(refresh=bool(refresh and deep_inspect))
        plugins = [dict(item) for item in list(inventory_payload.get("plugins") or []) if isinstance(item, dict)]
        bridge_plugin = None
        for bridge_plugin_id in _OPENCLAW_BRIDGE_PLUGIN_IDS:
            bridge_plugin = next(
                (
                    plugin
                    for plugin in plugins
                    if str(plugin.get("id") or "").strip() == bridge_plugin_id
                ),
                None,
            )
            if bridge_plugin:
                break
        inspect_payload = (
            self._managed_local_plugin_inspect((bridge_plugin or {}).get("id") or _OPENCLAW_BRIDGE_PLUGIN_IDS[0])
            if deep_inspect
            else {}
        )
        inspect_plugin_payload = dict(inspect_payload.get("plugin") or {})
        bridge_plugin_id = (
            str((bridge_plugin or {}).get("id") or "").strip()
            or str(inspect_plugin_payload.get("id") or "").strip()
            or _OPENCLAW_BRIDGE_PLUGIN_IDS[0]
        )
        openclaw_config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        entries = dict((openclaw_config_payload.get("plugins") or {}).get("entries") or {})
        entry_payload = dict(entries.get(bridge_plugin_id or "") or {})
        plugin_config = dict(entry_payload.get("config") or {})
        plugin_config_channels = [
            _normalize_openclaw_channel_id(str(item).strip())
            for item in list(plugin_config.get("managedChannels") or [])
            if str(item).strip()
        ]
        configured_channels = self._configured_openclaw_channel_ids(openclaw_config_payload)
        managed_channels = (
            plugin_config_channels
            or configured_channels
            or list(_OPENCLAW_BRIDGE_DEFAULT_MANAGED_CHANNELS)
        )
        bridge_loaded = (
            str((bridge_plugin or {}).get("status") or "").strip().lower() == "loaded"
            or str(inspect_plugin_payload.get("status") or "").strip().lower() == "loaded"
        )
        bridge_enabled = bool(entry_payload.get("enabled", inspect_plugin_payload.get("enabled", True)))
        route_payload: dict[str, Any] = {}
        bridge_status_source = "config_only"
        bridge_status_error: str | None = None
        bridge_status_ms = 0
        bridge_status_observed_at: str | None = None
        should_probe_live_route = bool(
            bridge_plugin_id == "openclaw-v8-bridge"
            and bridge_enabled
            and (deep_inspect or refresh)
        )
        if bridge_plugin_id == "openclaw-v8-bridge" and bridge_enabled and should_probe_live_route:
            status_started_at = time.perf_counter()
            try:
                body = self._openclaw_gateway_request_json(
                    suffix="/plugins/openclaw-v8-bridge/status",
                    timeout=(15 if deep_inspect else _BRIDGE_STATUS_HOT_REFRESH_TIMEOUT_SECONDS),
                )
                bridge_status_ms = max(0, int((time.perf_counter() - status_started_at) * 1000))
                bridge_status_observed_at = _now_iso()
                if isinstance(body, dict) and (
                    bool(body.get("ok"))
                    or any(key in body for key in ("bridgeReady", "managedChannels", "routeVersion", "pluginId", "claimEnabled"))
                ):
                    route_payload = dict(body)
                    bridge_status_source = "gateway_route"
                else:
                    bridge_status_error = "invalid_status_payload"
            except Exception as exc:
                bridge_status_ms = max(0, int((time.perf_counter() - status_started_at) * 1000))
                bridge_status_observed_at = _now_iso()
                bridge_status_error = str(exc).strip() or ("timeout" if not deep_inspect else "status_probe_failed")
                route_payload = {}
        bridge_loaded = bridge_loaded or bool(route_payload)
        route_channels = [
            _normalize_openclaw_channel_id(str(item).strip())
            for item in list(route_payload.get("managedChannels") or [])
            if str(item).strip()
        ]
        if route_channels:
            managed_channels = route_channels
        route_install_provenance = str(route_payload.get("installProvenance") or "").strip()
        inventory_install_provenance = (
            "global_extensions_root"
            if str((bridge_plugin or {}).get("source") or "").strip() in {"extensions_root", "managed_local_extensions"}
            else ""
        )
        derived_install_provenance = str(
            self._derive_openclaw_plugin_provenance(
                inspect_payload=inspect_payload,
                plugin_id=bridge_plugin_id,
                openclaw_config=openclaw_config_payload,
            )
            or ""
        ).strip() if deep_inspect else ""
        install_provenance = str(
            (route_install_provenance if route_install_provenance.lower() not in {"", "unknown", "missing"} else "")
            or (derived_install_provenance if derived_install_provenance.lower() not in {"", "unknown", "missing"} else "")
            or inventory_install_provenance
            or "unknown"
        ).strip() or "unknown"
        managed_channels_source = self._derive_bridge_managed_channels_source(
            plugin_config=plugin_config,
            openclaw_config=openclaw_config_payload,
            route_payload=route_payload,
        )
        config_source = self._derive_bridge_config_source(
            plugin_id=bridge_plugin_id,
            openclaw_config=openclaw_config_payload,
            route_payload=route_payload,
        )
        install_trusted = self._bridge_install_is_trusted(
            plugin_id=bridge_plugin_id,
            install_provenance=install_provenance,
            openclaw_config=openclaw_config_payload,
            inspect_payload=inspect_payload,
        )
        expected_inbound_url = f"{self.managed_local_engine_base_url()}/v1/plugin-host/inbound"
        expected_handoff_token = self._managed_local_handoff_token()
        handoff_configured = bool(
            route_payload.get("handoffConfigured")
            if route_payload
            else str(plugin_config.get("handoffToken") or expected_handoff_token).strip()
        )
        claim_enabled = bool(route_payload.get("claimEnabled", bool(managed_channels)))
        if not route_payload:
            claim_enabled = bool(managed_channels)
        bridge_ready = bool(
            bridge_plugin_id
            and bridge_loaded
            and bridge_enabled
            and route_payload
            and bool(route_payload.get("bridgeReady"))
            and claim_enabled
        )
        state_payload = {
            "bridgeReady": bridge_ready,
            "pluginId": bridge_plugin_id,
            "managedChannels": list(dict.fromkeys(managed_channels)),
            "installProvenance": install_provenance,
            "installTrusted": install_trusted,
            "managedChannelsSource": managed_channels_source,
            "configSource": config_source,
            "routeVersion": str(route_payload.get("routeVersion") or "").strip() or None,
            "codeFingerprint": str(route_payload.get("codeFingerprint") or "").strip() or None,
            "runtimeExtensionPath": str(route_payload.get("runtimeExtensionPath") or "").strip() or None,
            "resolvedStateDir": str(route_payload.get("resolvedStateDir") or self.managed_local_root()).strip() or None,
            "gatewayBaseUrl": str(route_payload.get("gatewayBaseUrl") or self._managed_local_gateway_base_url()).strip() or None,
            "v8InboundUrl": str(route_payload.get("v8InboundUrl") or plugin_config.get("v8InboundUrl") or expected_inbound_url).strip() or None,
            "pluginsAllowConfigured": bool(route_payload.get("pluginsAllowConfigured")),
            "pluginsAllow": [
                str(item).strip()
                for item in list(route_payload.get("pluginsAllow") or [])
                if str(item).strip()
            ],
            "handoffConfigured": handoff_configured,
            "claimEnabled": claim_enabled,
            "claimMissedReason": route_payload.get("claimMissedReason"),
            "lastClaimAt": route_payload.get("lastClaimAt"),
            "lastClaimAttemptAt": route_payload.get("lastClaimAttemptAt"),
            "lastClaimOutcome": route_payload.get("lastClaimOutcome"),
            "lastClaimDeclineReason": route_payload.get("lastClaimDeclineReason"),
            "lastClaimChannel": route_payload.get("lastClaimChannel"),
            "lastClaimConversation": route_payload.get("lastClaimConversation"),
            "lastClaimMessageId": route_payload.get("lastClaimMessageId"),
            "lastClaimAccountId": route_payload.get("lastClaimAccountId"),
            "lastClaimPayloadShape": route_payload.get("lastClaimPayloadShape"),
            "failClosed": bool(route_payload.get("failClosed", plugin_config.get("failClosed", True))),
            "toolAllowlistMode": str(route_payload.get("toolAllowlistMode") or plugin_config.get("toolAllowlistMode") or "all").strip() or "all",
            "toolAllowlist": [
                str(item).strip()
                for item in list(route_payload.get("toolAllowlist") or plugin_config.get("toolAllowlist") or [])
                if str(item).strip()
            ],
            "inventoryPlugin": dict(bridge_plugin or {}),
            "inspectPayload": inspect_payload,
            "routePayload": route_payload,
            "bridgeStatusSource": bridge_status_source,
            "bridgeStatusObservedAt": bridge_status_observed_at or str((cached_status_payload or {}).get("bridgeStatusObservedAt") or "").strip() or None,
            "bridgeStatusMs": bridge_status_ms or int((cached_status_payload or {}).get("bridgeStatusMs") or 0),
            "bridgeStatusError": bridge_status_error or str((cached_status_payload or {}).get("bridgeStatusError") or "").strip() or None,
            "bridgeStatusStale": False,
            "refreshMode": "deep" if deep_inspect else "hot",
        }
        if not route_payload:
            if cached_status_payload is not None:
                merged_payload = dict(cached_status_payload)
                for key, value in state_payload.items():
                    if key in {"bridgeReady", "routeVersion", "codeFingerprint", "runtimeExtensionPath", "resolvedStateDir", "gatewayBaseUrl", "v8InboundUrl"}:
                        if value not in (None, "", [], {}):
                            merged_payload[key] = value
                        continue
                    merged_payload[key] = value
                return self._mark_bridge_status_payload_stale(
                    merged_payload,
                    error=bridge_status_error,
                    source="hot_cache",
                )
            state_payload["bridgeStatusStale"] = True
            state_payload["bridgeStatusSource"] = bridge_status_source
            state_payload["bridgeStatusError"] = bridge_status_error
            if not refresh and not deep_inspect:
                self._schedule_bridge_status_hot_refresh()
            return state_payload
        if not deep_inspect:
            return self._write_bridge_status_hot_cache(state_payload)
        return state_payload

    def _managed_local_channel_accounts_from_state_manifest(
        self,
        *,
        inventory: dict[str, Any] | None = None,
    ) -> dict[str, list[str]]:
        inventory_payload = inventory if isinstance(inventory, dict) else self._managed_local_plugins_inventory()
        channel_ids: set[str] = set()
        for plugin in list(inventory_payload.get("plugins") or []):
            if not isinstance(plugin, dict):
                continue
            plugin_id = _normalize_openclaw_channel_id(str(plugin.get("id") or "").strip())
            if plugin_id:
                channel_ids.add(plugin_id)
            for channel_id in list(plugin.get("channels") or []):
                normalized = _normalize_openclaw_channel_id(str(channel_id).strip())
                if normalized:
                    channel_ids.add(normalized)

        channels: dict[str, list[str]] = {}
        state_root = self.managed_local_root()
        for channel_id in sorted(channel_ids):
            accounts_path = state_root / channel_id / "accounts.json"
            if not accounts_path.exists():
                continue
            try:
                payload = json.loads(accounts_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, list):
                continue
            accounts = [str(item).strip() for item in payload if str(item).strip()]
            if accounts:
                channels[channel_id] = list(dict.fromkeys(accounts))
        return channels

    def _configured_openclaw_channel_ids(self, openclaw_config: dict[str, Any] | None = None) -> list[str]:
        config_payload = dict(openclaw_config or self._read_managed_local_openclaw_config())
        channels_payload = dict(config_payload.get("channels") or {})
        collected: list[str] = []
        for channel_id, channel_config in channels_payload.items():
            if isinstance(channel_config, dict) and channel_config.get("enabled") is False:
                continue
            normalized = _normalize_openclaw_channel_id(str(channel_id or "").strip())
            if normalized:
                collected.append(normalized)
        return list(dict.fromkeys(collected))

    @staticmethod
    def _canonical_bridge_tool_name(*, plugin_id: str | None, tool_name: str) -> str:
        normalized_tool_name = str(tool_name or "").strip()
        normalized_plugin_id = str(plugin_id or "").strip() or None
        if not normalized_tool_name:
            return ""
        if normalized_plugin_id:
            return f"{normalized_plugin_id}.{normalized_tool_name}"
        return f"gateway.{normalized_tool_name}"

    @staticmethod
    def _lexical_bridge_tool_score(tool: dict[str, Any], query_terms: list[str]) -> tuple[int, int, int, str]:
        canonical_name = str(tool.get("canonicalName") or "").strip().lower()
        label = str(tool.get("label") or "").strip().lower()
        description = str(tool.get("description") or "").strip().lower()
        plugin_id = str(tool.get("pluginId") or "").strip().lower()
        exact = 0
        prefix = 0
        contains = 0
        for term in query_terms:
            if not term:
                continue
            if canonical_name == term or label == term:
                exact += 1
            if canonical_name.startswith(term) or label.startswith(term) or plugin_id.startswith(term):
                prefix += 1
            if term in canonical_name or term in label or term in description or term in plugin_id:
                contains += 1
        return (-exact, -prefix, -contains, canonical_name)

    @staticmethod
    def _bridge_tool_query_terms(query: str | None) -> list[str]:
        terms: list[str] = []
        for raw_term in re.split(r"[\s,;|/]+", str(query or "").strip().lower()):
            normalized = str(raw_term or "").strip()
            if not normalized or len(normalized) <= 1 or normalized in _BRIDGE_TOOL_STOPWORDS:
                continue
            terms.append(normalized)
        return terms

    @staticmethod
    def _build_bridge_tool_rerank_document(tool: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"canonical: {str(tool.get('canonicalName') or '').strip()}",
                f"plugin: {str(tool.get('pluginId') or '').strip() or 'gateway'}",
                f"label: {str(tool.get('label') or '').strip()}",
                f"description: {str(tool.get('description') or '').strip() or 'OpenClaw tool'}",
                f"source: {str(tool.get('source') or '').strip() or 'core'}",
            ]
        ).strip()

    def _resolve_bridge_tool_prefilter_state(self) -> dict[str, Any]:
        config = storage.get_extensions_config() or {}
        policy = dict(config.get("prefilterPolicy") or config.get("rerankPolicy") or {})
        if not bool(policy.get("enabled", False)):
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
                    "reason": str(exc).strip() or exc.__class__.__name__,
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

    def _prefilter_bridge_tool_entries(
        self,
        *,
        user_query: str,
        items: list[dict[str, Any]],
        top_k: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prefilter_state = self._resolve_bridge_tool_prefilter_state()
        if len(items) <= 1:
            prefilter_state["mode"] = "lexical"
            prefilter_state["reason"] = "候选家族数量不足，无需预筛。"
            return list(items), prefilter_state
        if not prefilter_state.get("available") or not prefilter_state.get("modelId"):
            prefilter_state["mode"] = "fallback"
            return list(items[: max(top_k, 1)]), prefilter_state
        try:
            family_map: dict[str, dict[str, Any]] = {}
            for item in items:
                family_key = f"{str(item.get('pluginId') or 'gateway').strip() or 'gateway'}::{str(item.get('toolName') or item.get('canonicalName') or '').strip()}"
                family_map.setdefault(family_key, dict(item))
            if len(family_map) <= 1:
                prefilter_state["mode"] = "lexical"
                prefilter_state["reason"] = "候选家族数量不足，无需预筛。"
                return list(items[: max(top_k, 1)]), prefilter_state
            selected_keys, llm_state = select_family_keys_with_llm(
                role=str(prefilter_state.get("role") or "extensions_prefilter"),
                user_query=user_query,
                family_label="plugin_host",
                families=[
                    {
                        "key": key,
                        "title": str(item.get("canonicalName") or item.get("displayName") or item.get("toolName") or "").strip() or key,
                        "description": str(item.get("description") or "").strip(),
                        "memberCount": 1,
                        "examples": [str(item.get("toolName") or item.get("canonicalName") or "").strip() or key],
                    }
                    for key, item in family_map.items()
                ],
                max_families=max(1, int(top_k or 1)),
                timeout_seconds=_BRIDGE_TOOL_PREFILTER_TIMEOUT_SECONDS,
            )
            if not selected_keys:
                prefilter_state["mode"] = "fallback"
                prefilter_state["reason"] = str(llm_state.get("reason") or "LLM 未返回可用家族。").strip()
                prefilter_state["timedOut"] = bool(llm_state.get("timedOut"))
                prefilter_state["cacheHit"] = bool(llm_state.get("cacheHit"))
                prefilter_state["durationMs"] = int(llm_state.get("durationMs") or 0)
                return list(items[: max(top_k, 1)]), prefilter_state
            prefilter_state["mode"] = str(llm_state.get("mode") or "llm_tree")
            prefilter_state["reason"] = str(llm_state.get("reason") or "").strip()
            prefilter_state["timedOut"] = bool(llm_state.get("timedOut"))
            prefilter_state["cacheHit"] = bool(llm_state.get("cacheHit"))
            prefilter_state["durationMs"] = int(llm_state.get("durationMs") or 0)
            return [dict(family_map[key]) for key in selected_keys if key in family_map][: max(top_k, 1)], prefilter_state
        except Exception as exc:
            prefilter_state["mode"] = "fallback"
            prefilter_state["reason"] = str(exc).strip() or exc.__class__.__name__
            prefilter_state["timedOut"] = False
            prefilter_state["cacheHit"] = False
            prefilter_state["durationMs"] = 0
            return list(items[: max(top_k, 1)]), prefilter_state

    def _normalize_bridge_tool_entry(self, raw: dict[str, Any]) -> dict[str, Any]:
        plugin_id = str(raw.get("pluginId") or "").strip() or None
        tool_name = str(raw.get("toolName") or raw.get("name") or "").strip()
        canonical_name = str(raw.get("canonicalName") or "").strip() or self._canonical_bridge_tool_name(
            plugin_id=plugin_id,
            tool_name=tool_name,
        )
        source = str(raw.get("source") or ("plugin" if plugin_id else "core")).strip() or "core"
        return {
            "canonicalName": canonical_name,
            "toolName": tool_name,
            "pluginId": plugin_id,
            "label": str(raw.get("label") or tool_name or canonical_name).strip() or canonical_name,
            "description": str(raw.get("description") or raw.get("label") or tool_name or canonical_name).strip()
            or canonical_name,
            "source": source,
            "optional": bool(raw.get("optional")),
            "allowed": bool(raw.get("allowed", True)),
            "displayName": str(raw.get("displayName") or raw.get("label") or canonical_name).strip() or canonical_name,
        }

    def _infer_bridge_tool_inventory_from_openclaw_logs(self) -> dict[str, Any] | None:
        log_path, records = self._openclaw_log_tail_records(max_lines=4000, max_bytes=4 * 1024 * 1024)
        if not log_path or not records:
            return None
        inferred: dict[str, dict[str, Any]] = {}
        latest_observed_at: str | None = None
        for record in records:
            message = self._openclaw_log_record_message(record)
            if not message:
                continue
            match = _OPENCLAW_REGISTERED_TOOL_LINE_RE.search(message.strip().lower())
            if not match:
                continue
            prefix = str(match.group("prefix") or "").strip()
            body = str(match.group("body") or "").strip()
            if not prefix or not body:
                continue
            observed_at = self._openclaw_log_record_time(record)
            if observed_at:
                latest_observed_at = observed_at
            tokens = {
                token.strip()
                for token in _OPENCLAW_REGISTERED_TOOL_TOKEN_RE.findall(body)
                if token.strip() and token.strip() not in {"registered", "tool", "tools", "and"}
            }
            tool_names = sorted(token for token in tokens if token.startswith("feishu_"))
            if not tool_names and prefix.startswith("feishu_"):
                tool_names = [prefix]
            for tool_name in tool_names:
                canonical_name = self._canonical_bridge_tool_name(
                    plugin_id="openclaw-lark",
                    tool_name=tool_name,
                )
                inferred[canonical_name] = {
                    "canonicalName": canonical_name,
                    "toolName": tool_name,
                    "pluginId": "openclaw-lark",
                    "label": tool_name,
                    "description": f"从 OpenClaw 运行日志推断的动态工具：{tool_name}",
                    "source": "plugin",
                    "optional": False,
                    "allowed": True,
                    "displayName": tool_name,
                }
        if not inferred:
            return None
        inventory = [self._normalize_bridge_tool_entry(item) for item in inferred.values()]
        inventory.sort(
            key=lambda item: (
                str(item.get("pluginId") or "").strip().lower(),
                str(item.get("toolName") or item.get("canonicalName") or "").strip().lower(),
            )
        )
        return {
            "ok": True,
            "inventory": inventory,
            "inventorySource": "openclaw_log_registered_tools",
            "inventoryFreshness": "cached",
            "operatorReadAvailable": False,
            "stateCatalogError": None,
            "cliCatalogError": None,
            "gatewayCatalogError": None,
            "logPath": str(log_path),
            "observedAt": latest_observed_at,
        }

    def _read_bridge_tool_catalog_cache_entry(self, cache_key: tuple[str, int]) -> tuple[dict[str, Any] | None, bool]:
        with self._bridge_tool_catalog_cache_lock:
            entry = self._bridge_tool_catalog_cache.get(cache_key)
            if not entry:
                return None, False
            cached_at, payload = entry
            return copy.deepcopy(dict(payload)), self._monotonic_age_within(cached_at, _BRIDGE_TOOL_CATALOG_TTL_SECONDS)

    def _write_bridge_tool_catalog_cache_entry(self, cache_key: tuple[str, int], payload: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(dict(payload or {}))
        with self._bridge_tool_catalog_cache_lock:
            self._bridge_tool_catalog_cache[cache_key] = (time.monotonic(), current)
        return copy.deepcopy(current)

    def _schedule_bridge_tool_catalog_refresh(self, *, cache_key: tuple[str, int], query: str, limit: int) -> None:
        with self._bridge_tool_catalog_cache_lock:
            if cache_key in self._bridge_tool_catalog_refreshing:
                return
            self._bridge_tool_catalog_refreshing.add(cache_key)

        def _worker() -> None:
            try:
                self._refresh_bridge_tool_catalog_cache_entry(cache_key=cache_key, query=query, limit=limit)
            finally:
                with self._bridge_tool_catalog_cache_lock:
                    self._bridge_tool_catalog_refreshing.discard(cache_key)

        self._schedule_daemon_thread(_worker)

    def _load_bridge_inventory_for_hot_path(self, *, refresh: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        started_at = time.perf_counter()
        cached_payload, cache_fresh = self._read_bridge_inventory_hot_cache()
        if refresh:
            try:
                payload = self._refresh_bridge_inventory_hot_cache()
                return payload, {
                    "cacheHit": False,
                    "backgroundRefresh": False,
                    "inventoryStale": False,
                    "inventoryError": None,
                    "engineInventoryCacheMs": max(0, int((time.perf_counter() - started_at) * 1000)),
                    "bridgeToolsRequestMs": int(((payload.get("timingsMs") or {}).get("totalMs") or 0)),
                }
            except Exception as exc:
                if cached_payload is not None:
                    return cached_payload, {
                        "cacheHit": True,
                        "backgroundRefresh": True,
                        "inventoryStale": True,
                        "inventoryError": str(exc).strip() or exc.__class__.__name__,
                        "engineInventoryCacheMs": max(0, int((time.perf_counter() - started_at) * 1000)),
                        "bridgeToolsRequestMs": 0,
                    }
                raise

        if cached_payload is not None:
            if not cache_fresh:
                self._schedule_bridge_inventory_hot_refresh()
            return cached_payload, {
                "cacheHit": True,
                "backgroundRefresh": not cache_fresh,
                "inventoryStale": not cache_fresh,
                "inventoryError": None,
                "engineInventoryCacheMs": max(0, int((time.perf_counter() - started_at) * 1000)),
                "bridgeToolsRequestMs": 0,
            }

        payload = self._refresh_bridge_inventory_hot_cache()
        return payload, {
            "cacheHit": False,
            "backgroundRefresh": False,
            "inventoryStale": False,
            "inventoryError": None,
            "engineInventoryCacheMs": max(0, int((time.perf_counter() - started_at) * 1000)),
            "bridgeToolsRequestMs": int(((payload.get("timingsMs") or {}).get("totalMs") or 0)),
        }

    def _refresh_bridge_tool_catalog_cache_entry(self, *, cache_key: tuple[str, int], query: str, limit: int) -> dict[str, Any]:
        return self._bridge_tool_catalog(query=query, limit=limit, refresh=True)

    def _bridge_tool_catalog(self, *, query: str | None = None, limit: int = 12, refresh: bool = False) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("当前 PluginHostRuntime 已关闭，暂不提供 bridge 工具目录。")
        if self.is_external_host():
            raise RuntimeError("当前 external host 尚未接通 bridge tools catalog。")
        normalized_query = str(query or "").strip()
        normalized_limit = max(1, int(limit or 12))
        cache_key = (normalized_query, normalized_limit)
        if not refresh:
            cached_payload, cache_fresh = self._read_bridge_tool_catalog_cache_entry(cache_key)
            if cached_payload is not None:
                cached_payload["cacheHit"] = True
                if not cache_fresh:
                    self._schedule_bridge_tool_catalog_refresh(
                        cache_key=cache_key,
                        query=normalized_query,
                        limit=normalized_limit,
                    )
                    cached_payload["backgroundRefresh"] = True
                else:
                    cached_payload["backgroundRefresh"] = False
                return cached_payload

        total_started_at = time.perf_counter()
        timings_ms: dict[str, int] = {}
        bridge_state_started_at = time.perf_counter()
        bridge_state = self._managed_local_bridge_state(refresh=False, deep_inspect=False)
        timings_ms["bridgeStateMs"] = max(0, int((time.perf_counter() - bridge_state_started_at) * 1000))
        bridge_ready = bool(bridge_state.get("bridgeReady"))
        bridge_status_stale = bool(bridge_state.get("bridgeStatusStale"))

        inventory_body, inventory_meta = self._load_bridge_inventory_for_hot_path(refresh=refresh)
        timings_ms["engineInventoryCacheMs"] = int(inventory_meta.get("engineInventoryCacheMs") or 0)
        timings_ms["bridgeToolsRequestMs"] = int(inventory_meta.get("bridgeToolsRequestMs") or 0)

        selection_started_at = time.perf_counter()
        inventory = [
            self._normalize_bridge_tool_entry(item)
            for item in list(inventory_body.get("inventory") or [])
            if isinstance(item, dict)
        ]
        if not bridge_ready and not bridge_status_stale:
            inventory = [{**item, "allowed": False} for item in inventory]
        if not inventory and not bridge_ready and not bridge_status_stale:
            raise RuntimeError("当前尚未检测到可用的 OpenClaw V8 Bridge，且没有可恢复的工具目录。")

        callable_inventory = [dict(item) for item in inventory if bool(item.get("allowed"))]
        query_terms = self._bridge_tool_query_terms(query)
        lexical_started_at = time.perf_counter()
        if query_terms:
            callable_inventory = sorted(
                callable_inventory,
                key=lambda item: self._lexical_bridge_tool_score(item, query_terms),
            )
        else:
            callable_inventory = sorted(
                callable_inventory,
                key=lambda item: (
                    0 if str(item.get("source") or "").strip() == "core" else 1,
                    str(item.get("canonicalName") or "").strip().lower(),
                ),
            )
        timings_ms["lexicalMs"] = max(0, int((time.perf_counter() - lexical_started_at) * 1000))

        exposure_pool_limit = max(normalized_limit * 4, _BRIDGE_TOOL_RERANK_POOL_FLOOR)
        exposure_pool = [dict(item) for item in callable_inventory[: min(len(callable_inventory), exposure_pool_limit)]]
        selection: dict[str, Any] = {
            "mode": "lexical",
            "modelId": None,
            "role": None,
            "reason": None,
            "poolSize": len(exposure_pool),
            "inventorySize": len(inventory),
            "callableSize": len(callable_inventory),
            "timingsMs": timings_ms,
        }
        prefilter_timed_out = False
        prefilter_cache_hit = False
        if query_terms and len(exposure_pool) > 1:
            prefilter_started_at = time.perf_counter()
            exposure_pool, prefilter_state = self._prefilter_bridge_tool_entries(
                user_query=normalized_query,
                items=exposure_pool,
                top_k=normalized_limit,
            )
            timings_ms["prefilterMs"] = max(0, int((time.perf_counter() - prefilter_started_at) * 1000))
            prefilter_timed_out = bool(prefilter_state.get("timedOut"))
            prefilter_cache_hit = bool(prefilter_state.get("cacheHit"))
            selection.update(
                {
                    "mode": str(prefilter_state.get("mode") or "lexical"),
                    "modelId": str(prefilter_state.get("modelId") or "").strip() or None,
                    "role": str(prefilter_state.get("role") or "").strip() or None,
                    "reason": str(prefilter_state.get("reason") or "").strip() or None,
                    "prefilterTimedOut": prefilter_timed_out,
                    "prefilterCacheHit": prefilter_cache_hit,
                    "prefilterDurationMs": int(prefilter_state.get("durationMs") or 0),
                }
            )

        if query_terms:
            exposure_limit = min(max(normalized_limit * 3, 16), _BRIDGE_TOOL_EXPOSURE_CAP)
            exposure_seeds = [dict(item) for item in exposure_pool[:normalized_limit]]
            exposure = [
                dict(item)
                for item in expand_tool_family_seeds(
                    items=callable_inventory,
                    seeds=exposure_seeds,
                    get_plugin_id=lambda item: str(item.get("pluginId") or "").strip() or "gateway",
                    get_tool_name=lambda item: str(item.get("toolName") or item.get("canonicalName") or "").strip(),
                    get_identity=lambda item: str(item.get("canonicalName") or item.get("toolName") or "").strip(),
                    get_sort_key=lambda item: (
                        str(item.get("pluginId") or "gateway").strip().lower(),
                        str(item.get("toolName") or item.get("canonicalName") or "").strip().lower(),
                    ),
                    max_items=exposure_limit,
                )
            ]
        else:
            exposure_limit = min(max(normalized_limit, 1), _BRIDGE_TOOL_EXPOSURE_CAP)
            exposure_seeds = [dict(item) for item in callable_inventory[: min(len(callable_inventory), exposure_limit)]]
            exposure = [dict(item) for item in exposure_seeds[:exposure_limit]]
        timings_ms["selectionMs"] = max(0, int((time.perf_counter() - selection_started_at) * 1000))
        timings_ms["totalMs"] = max(0, int((time.perf_counter() - total_started_at) * 1000))
        selection.update(
            {
                "seedSize": len(exposure_seeds),
                "exposureLimit": exposure_limit,
                "expandedSize": len(exposure),
                "timingsMs": timings_ms,
            }
        )

        result = {
            "bridgeReady": bridge_ready,
            "bridgePluginId": str(bridge_state.get("pluginId") or "").strip() or None,
            "managedChannels": [
                str(item).strip()
                for item in list(bridge_state.get("managedChannels") or [])
                if str(item).strip()
            ],
            "toolInventoryHealth": (
                "healthy"
                if not str(inventory_meta.get("inventoryError") or "").strip()
                and str(inventory_body.get("inventorySource") or inventory_body.get("source") or "").strip() in {"gateway_rpc", "plugin_source_scan", "durable_cache"}
                and not any(
                    str(inventory_body.get(key) or "").strip()
                    for key in ("stateCatalogError", "cliCatalogError", "sourceScanCatalogError", "gatewayCatalogError")
                )
                else "degraded"
            ),
            "toolInventorySource": str(inventory_body.get("inventorySource") or inventory_body.get("source") or "").strip() or "unknown",
            "toolInventoryFreshness": str(inventory_body.get("inventoryFreshness") or "").strip() or "unknown",
            "operatorReadAvailable": inventory_body.get("operatorReadAvailable"),
            "toolInventoryErrors": {
                "stateCatalogError": str(inventory_body.get("stateCatalogError") or "").strip() or None,
                "cliCatalogError": str(inventory_body.get("cliCatalogError") or "").strip() or None,
                "sourceScanCatalogError": str(inventory_body.get("sourceScanCatalogError") or "").strip() or None,
                "gatewayCatalogError": str(inventory_body.get("gatewayCatalogError") or "").strip() or None,
            },
            "toolInventoryLogPath": str(inventory_body.get("logPath") or "").strip() or None,
            "toolInventoryObservedAt": str(inventory_body.get("observedAt") or "").strip() or None,
            "toolInventoryTimingsMs": dict(inventory_body.get("timingsMs") or {}),
            "cacheHit": bool(inventory_meta.get("cacheHit")),
            "backgroundRefresh": bool(inventory_meta.get("backgroundRefresh")),
            "inventoryError": str(inventory_meta.get("inventoryError") or "").strip() or None,
            "inventoryStale": bool(inventory_meta.get("inventoryStale")),
            "prefilterTimedOut": prefilter_timed_out,
            "prefilterCacheHit": prefilter_cache_hit,
            "inventory": inventory,
            "exposure": exposure,
            "tools": exposure,
            "selection": selection,
        }
        return self._write_bridge_tool_catalog_cache_entry(cache_key, result)

    def _plugin_inventory_record(self, plugin_id: str | None, *, inventory: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            return None
        inventory_payload = inventory if isinstance(inventory, dict) else self._managed_local_plugins_inventory()
        for plugin in list(inventory_payload.get("plugins") or []):
            if not isinstance(plugin, dict):
                continue
            if str(plugin.get("id") or "").strip() == normalized_plugin_id:
                return dict(plugin)
        return None

    def _plugin_live_tool_names(self, plugin: dict[str, Any] | None, *, inventory: dict[str, Any] | None = None) -> list[str]:
        normalized_plugin_id = str((plugin or {}).get("pluginId") or "").strip()
        inventory_record = self._plugin_inventory_record(normalized_plugin_id, inventory=inventory)
        if not inventory_record:
            return []
        tool_names = [
            str(item).strip()
            for item in list(inventory_record.get("toolNames") or [])
            if str(item).strip()
        ]
        return list(dict.fromkeys(tool_names))

    def _managed_local_gateway_base_url(self) -> str:
        config = self._read_managed_local_openclaw_config()
        gateway = dict(config.get("gateway") or {})
        port = int(gateway.get("port") or 18789)
        return f"http://127.0.0.1:{port}"

    def _managed_local_gateway_auth_token(self) -> str | None:
        config = self._read_managed_local_openclaw_config()
        gateway = dict(config.get("gateway") or {})
        auth = dict(gateway.get("auth") or {})
        token = str(auth.get("token") or auth.get("password") or "").strip()
        return token or None

    def _managed_local_handoff_token(self) -> str:
        env_override = str(os.environ.get("V8_AGENT_OS_PLUGIN_HOST_HANDOFF_TOKEN") or "").strip()
        if env_override:
            return env_override
        bridge = dict(storage.get_system_base_config().get("bridge") or {})
        internal_secret = str(bridge.get("internalSecret") or "").strip()
        if not internal_secret:
            return ""
        try:
            root_marker = str(self.managed_local_root().expanduser().resolve())
        except Exception:
            root_marker = str(self.managed_local_root().expanduser())
        return hmac.new(
            internal_secret.encode("utf-8"),
            f"plugin_host_handoff:v1:{root_marker}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _plugin_manifest_id(candidate_root: Path | None) -> str | None:
        root = candidate_root.expanduser() if isinstance(candidate_root, Path) else None
        if root is None:
            return None
        manifest_path = root / "openclaw.plugin.json"
        if not manifest_path.exists():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        plugin_id = str((payload or {}).get("id") or "").strip().lower()
        return plugin_id or None

    def _managed_local_bridge_repo_root(self) -> Path | None:
        env_override = str(os.environ.get("V8_AGENT_OS_OPENCLAW_BRIDGE_ROOT") or "").strip()
        if env_override:
            candidate = Path(env_override).expanduser()
            if self._plugin_manifest_id(candidate) in _OPENCLAW_BRIDGE_PLUGIN_IDS:
                return candidate
        here = Path(__file__).resolve()
        for ancestor in [here.parent, *here.parents]:
            for dirname in _OPENCLAW_BRIDGE_SOURCE_DIR_CANDIDATES:
                candidate = (ancestor / dirname).expanduser()
                if self._plugin_manifest_id(candidate) in _OPENCLAW_BRIDGE_PLUGIN_IDS:
                    return candidate
        return None

    def _managed_local_bridge_extension_path(self) -> Path:
        return self._managed_local_extensions_root() / _OPENCLAW_BRIDGE_PLUGIN_IDS[0]

    def _plugin_root_matches_managed_local_extension_slot(self, plugin_id: str | None, plugin_root: Path | None) -> bool:
        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id or plugin_root is None:
            return False
        slot_path = self._managed_local_extensions_root() / normalized_plugin_id
        if not os.path.lexists(str(slot_path)):
            return False
        return self._same_path(plugin_root, slot_path) or self._path_within_root(plugin_root, slot_path)

    @staticmethod
    def _path_resolves_outside_self(candidate: Path) -> bool:
        try:
            return candidate.exists() and candidate.expanduser().resolve() != candidate.expanduser().absolute()
        except Exception:
            return False

    def _ensure_managed_local_bridge_extension_link(self) -> dict[str, Any]:
        if not self.is_managed_local():
            return {"ok": False, "reason": "not_managed_local"}
        source_root = self._managed_local_bridge_repo_root()
        if source_root is None:
            return {"ok": False, "reason": "source_missing"}
        target_root = self._managed_local_bridge_extension_path()
        target_root.parent.mkdir(parents=True, exist_ok=True)
        created = False
        replaced_link = False
        if os.path.lexists(str(target_root)):
            existing_plugin_id = self._plugin_manifest_id(target_root)
            if self._path_resolves_outside_self(target_root):
                os.rmdir(target_root)
                replaced_link = True
            elif existing_plugin_id not in _OPENCLAW_BRIDGE_PLUGIN_IDS and existing_plugin_id is not None:
                return {
                    "ok": False,
                    "reason": "target_exists",
                    "targetPath": str(target_root),
                    "sourcePath": str(source_root),
                    "existingPluginId": existing_plugin_id,
                }
        if not target_root.exists():
            target_root.mkdir(parents=True, exist_ok=True)
            created = True
        sync_entries = (
            "package.json",
            "openclaw.plugin.json",
            "index.ts",
            "README.md",
            "bin",
            ".bridge-cli",
            ".bridge-private",
        )
        for entry_name in sync_entries:
            source_path = source_root / entry_name
            target_path = target_root / entry_name
            if not source_path.exists():
                if target_path.is_dir():
                    shutil.rmtree(target_path, ignore_errors=False)
                elif target_path.exists():
                    target_path.unlink()
                continue
            if source_path.is_dir():
                shutil.copytree(source_path, target_path, dirs_exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            source_stat = source_path.stat()
            needs_copy = True
            if target_path.exists():
                target_stat = target_path.stat()
                needs_copy = (
                    source_stat.st_size != target_stat.st_size
                    or int(source_stat.st_mtime_ns) != int(target_stat.st_mtime_ns)
                )
            if needs_copy:
                shutil.copy2(source_path, target_path)
        return {
            "ok": True,
            "linked": False,
            "mode": "mirror",
            "created": created,
            "replacedLink": replaced_link,
            "changed": created or replaced_link,
            "targetPath": str(target_root),
            "sourcePath": str(source_root),
        }

    @staticmethod
    def _hash_plugin_root(root: Path | None) -> str | None:
        candidate_root = root.expanduser() if isinstance(root, Path) else None
        if candidate_root is None or not candidate_root.exists():
            return None
        hasher = hashlib.sha1()
        hashed = False
        for relative_name in ("index.ts", "package.json", "openclaw.plugin.json"):
            file_path = candidate_root / relative_name
            if not file_path.exists() or not file_path.is_file():
                continue
            hasher.update(relative_name.encode("utf-8"))
            hasher.update(b"\n")
            hasher.update(file_path.read_bytes())
            hasher.update(b"\n")
            hashed = True
        if not hashed:
            return None
        return hasher.hexdigest()

    def _bridge_package_name(self, repo_root: Path | None = None) -> str:
        source_root = repo_root or self._managed_local_bridge_repo_root()
        if source_root:
            package_path = source_root / "package.json"
            if package_path.exists():
                try:
                    payload = json.loads(package_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                package_name = str((payload or {}).get("name") or "").strip()
                if package_name:
                    return package_name
        return "@v8-agent-os/openclaw-v8-bridge"

    def _run_openclaw_command_capture(self, *args: str, timeout: int = 180) -> dict[str, Any]:
        env = self._managed_local_env()
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            raise RuntimeError("当前宿主无法解析 openclaw CLI。")
        windows_node_argv = self._resolve_windows_node_openclaw_argv(env, *args)
        argv = (
            windows_node_argv
            or (
                self._wrap_windows_executable_argv(cli_executable, *args)
                if os.name == "nt"
                else [cli_executable, *args]
            )
        )
        completed = subprocess.run(
            argv,
            cwd=str(self.managed_local_root()),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "argv": argv,
            "returnCode": int(completed.returncode),
            "stdout": str(completed.stdout or ""),
            "stderr": str(completed.stderr or ""),
        }

    def _backup_managed_local_bridge_extension_slot(self, *, reason: str) -> dict[str, Any]:
        slot_path = self._managed_local_bridge_extension_path()
        if not os.path.lexists(str(slot_path)):
            return {"backedUp": False, "reason": "slot_missing"}
        backup_root = self._managed_local_extensions_root() / ".openclaw-install-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_root / f"{slot_path.name}-{reason}-{timestamp}"
        shutil.move(str(slot_path), str(backup_path))
        return {
            "backedUp": True,
            "sourcePath": str(slot_path),
            "backupPath": str(backup_path),
        }

    def _managed_local_bridge_declared(self, payload: dict[str, Any] | None = None) -> bool:
        config_payload = dict(payload or self._read_managed_local_openclaw_config())
        plugins_payload = dict(config_payload.get("plugins") or {})
        entries = dict(plugins_payload.get("entries") or {})
        installs = dict(plugins_payload.get("installs") or {})
        load_paths = list(dict(plugins_payload.get("load") or {}).get("paths") or [])
        if self._plugin_manifest_id(self._managed_local_bridge_extension_path()) in _OPENCLAW_BRIDGE_PLUGIN_IDS:
            return True
        for plugin_id in _OPENCLAW_BRIDGE_PLUGIN_IDS:
            if plugin_id in entries or plugin_id in installs:
                return True
        for raw_path in load_paths:
            path_text = str(raw_path or "").strip()
            if not path_text:
                continue
            candidate = Path(path_text).expanduser()
            if not candidate.is_absolute():
                candidate = (self.managed_local_root() / candidate).expanduser()
            if candidate.name.strip().lower() in _OPENCLAW_BRIDGE_PLUGIN_IDS:
                return True
            manifest_path = candidate / "openclaw.plugin.json"
            if not manifest_path.exists():
                continue
            try:
                manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str((manifest_payload or {}).get("id") or "").strip().lower() in _OPENCLAW_BRIDGE_PLUGIN_IDS:
                return True
        try:
            inspect_payload = self._managed_local_plugin_inspect(_OPENCLAW_BRIDGE_PLUGIN_IDS[0])
        except Exception:
            inspect_payload = {}
        plugin_payload = dict(inspect_payload.get("plugin") or {})
        if str(plugin_payload.get("id") or "").strip().lower() in _OPENCLAW_BRIDGE_PLUGIN_IDS:
            return True
        return False

    def _ensure_managed_local_bridge_plugin_config(
        self,
        *,
        payload: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        config_payload = dict(payload or self._read_managed_local_openclaw_config())
        if not self.is_managed_local() or not self._managed_local_bridge_declared(config_payload):
            return config_payload, False
        plugins_payload = dict(config_payload.get("plugins") or {})
        entries_payload = dict(plugins_payload.get("entries") or {})
        bridge_plugin_id = _OPENCLAW_BRIDGE_PLUGIN_IDS[0]
        plugin_entry = dict(entries_payload.get(bridge_plugin_id) or {})
        bridge_config = dict(plugin_entry.get("config") or {})
        expected_inbound_url = f"{self.managed_local_engine_base_url()}/v1/plugin-host/inbound"
        expected_handoff_token = self._managed_local_handoff_token()
        changed = False

        if str(bridge_config.get("v8InboundUrl") or "").strip() != expected_inbound_url:
            bridge_config["v8InboundUrl"] = expected_inbound_url
            changed = True
        if expected_handoff_token and str(bridge_config.get("handoffToken") or "").strip() != expected_handoff_token:
            bridge_config["handoffToken"] = expected_handoff_token
            changed = True
        if "failClosed" not in bridge_config:
            bridge_config["failClosed"] = True
            changed = True
        if not str(bridge_config.get("toolAllowlistMode") or "").strip():
            bridge_config["toolAllowlistMode"] = "all"
            changed = True
        if "enabled" not in plugin_entry:
            plugin_entry["enabled"] = True
            changed = True

        plugin_entry["config"] = bridge_config
        entries_payload[bridge_plugin_id] = plugin_entry
        plugins_payload["entries"] = entries_payload
        config_payload["plugins"] = plugins_payload
        if changed and persist:
            self._write_managed_local_openclaw_config(config_payload)
        return config_payload, changed

    def _write_managed_local_openclaw_config(self, payload: dict[str, Any]) -> None:
        config_path = self._managed_local_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        next_payload, _ = self._normalize_managed_local_openclaw_config_payload(dict(payload or {}))
        config_path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ensure_managed_local_gateway_mode_local_payload(
        self,
        *,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        config_payload = dict(payload or self._read_managed_local_openclaw_config())
        if not self.is_managed_local():
            return config_payload, False
        gateway_payload = dict(config_payload.get("gateway") or {})
        current_mode = str(gateway_payload.get("mode") or "").strip().lower()
        if current_mode == "local":
            return config_payload, False
        gateway_payload["mode"] = "local"
        config_payload["gateway"] = gateway_payload
        return config_payload, True

    def _ensure_managed_local_gateway_mode_local(self) -> bool:
        if not self.is_managed_local() or self._managed_local_bridge_read_only():
            return False
        config_payload, changed = self._ensure_managed_local_gateway_mode_local_payload()
        if changed:
            self._write_managed_local_openclaw_config(config_payload)
        return changed

    def _ensure_minimal_managed_local_openclaw_host_config(
        self,
        *,
        ensure_gateway_mode: bool = True,
    ) -> dict[str, Any]:
        config_payload = self._read_managed_local_openclaw_config()
        recovered_payload, recovered_changed = self._recover_managed_local_openclaw_config_payload(config_payload)
        config_payload = dict(recovered_payload or {})
        if not self.is_managed_local() or self._managed_local_bridge_read_only():
            return {
                "payload": config_payload,
                "changed": bool(recovered_changed),
                "recoveredConfigChanged": bool(recovered_changed),
                "bridgeConfigChanged": False,
                "gatewayModeChanged": False,
            }
        config_payload, bridge_config_changed = self._ensure_managed_local_bridge_plugin_config(
            payload=config_payload,
            persist=False,
        )
        gateway_mode_changed = False
        if ensure_gateway_mode:
            config_payload, gateway_mode_changed = self._ensure_managed_local_gateway_mode_local_payload(
                payload=config_payload,
            )
        changed = bool(recovered_changed or bridge_config_changed or gateway_mode_changed)
        if changed:
            self._write_managed_local_openclaw_config(config_payload)
        return {
            "payload": config_payload,
            "changed": changed,
            "recoveredConfigChanged": bool(recovered_changed),
            "bridgeConfigChanged": bool(bridge_config_changed),
            "gatewayModeChanged": bool(gateway_mode_changed),
        }

    def _ensure_managed_local_gateway_launcher_handoff(self) -> dict[str, Any]:
        return ensure_gateway_launcher_patch(
            self.managed_local_root(),
            engine_base_url=self.managed_local_engine_base_url(),
            handoff_token=self._managed_local_handoff_token() or None,
        )

    def _ensure_managed_local_weixin_sidecar_patch(self) -> dict[str, Any]:
        plugin_dir = self._managed_local_weixin_plugin_dir()
        if not plugin_dir.exists():
            return {
                "patched": False,
                "supported": False,
                "reason": f"未找到 openclaw-weixin 插件目录：{plugin_dir}",
            }
        return ensure_weixin_handoff_patch(
            plugin_dir,
            engine_base_url=self.managed_local_engine_base_url(),
        )

    def managed_local_auto_start(self) -> bool:
        config = self.get_runtime_config()
        managed_local = dict(config.get("managedLocal") or {})
        return bool(managed_local.get("autoStart", False))

    def managed_local_engine_base_url(self) -> str:
        candidate = str(os.environ.get("V8_AGENT_OS_PLUGIN_HOST_ENGINE_BASE_URL") or "http://127.0.0.1:9530").strip()
        return candidate.rstrip("/") or "http://127.0.0.1:9530"

    def external_host_config(self) -> dict[str, str]:
        config = self.get_runtime_config()
        external = dict(config.get("externalHost") or {})
        return {
            "baseUrl": str(external.get("baseUrl") or "").strip(),
            "gatewayBaseUrl": str(external.get("gatewayBaseUrl") or "").strip(),
            "authToken": str(external.get("authToken") or "").strip(),
        }

    def _get_runtime_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        registry_payload = payload or default_plugin_registry()
        runtime_state = dict(registry_payload.get("runtimeState") or {})
        normalized = dict(runtime_state)
        normalized["lifecycleAuthority"] = str(runtime_state.get("lifecycleAuthority") or "").strip() or None
        normalized["autoStartDriftDetected"] = bool(runtime_state.get("autoStartDriftDetected", False))
        normalized["reconciledAt"] = str(runtime_state.get("reconciledAt") or "").strip() or None
        normalized["handoffDrift"] = bool(runtime_state.get("handoffDrift", False))
        normalized["lastInboundHandoffAt"] = str(runtime_state.get("lastInboundHandoffAt") or "").strip() or None
        return normalized

    def _save_runtime_state(self, patch: dict[str, Any], *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        registry_payload = payload or default_plugin_registry()
        current = self._get_runtime_state(registry_payload)
        for key, value in dict(patch or {}).items():
            current[key] = value
        registry_payload["runtimeState"] = current
        save_plugin_registry(registry_payload)
        return current

    def record_inbound_handoff(self) -> dict[str, Any]:
        handoff_at = _now_iso()
        state = self._save_runtime_state(
            {
                "lastInboundHandoffAt": handoff_at,
                "handoffDrift": False,
                "lifecycleAuthority": "manual_local" if self.is_managed_local() else "external_managed",
            }
        )
        self._touch_cached_public_snapshot_from_runtime_state(last_inbound_handoff_at=handoff_at)
        return state

    def _record_asset_state(
        self,
        *,
        direction: str,
        asset: dict[str, Any] | None,
        message_assets: dict[str, Any] | None = None,
        tts_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current_state = self._get_runtime_state()
        patch: dict[str, Any] = {}
        normalized_direction = "inbound" if str(direction).strip().lower() == "inbound" else "outbound"
        patch_key = "lastInboundAsset" if normalized_direction == "inbound" else "lastOutboundAsset"
        manifest_key = "lastInboundMessageAssets" if normalized_direction == "inbound" else "lastOutboundMessageAssets"
        patch[patch_key] = dict(asset or {}) if isinstance(asset, dict) else None
        patch[manifest_key] = dict(message_assets or {}) if isinstance(message_assets, dict) else None
        if tts_meta is not None:
            existing_tts = dict(current_state.get("lastTts") or {}) if isinstance(current_state.get("lastTts"), dict) else {}
            merged_tts = {
                **existing_tts,
                **(dict(tts_meta or {}) if isinstance(tts_meta, dict) else {}),
            }
            if asset:
                merged_tts["workspacePath"] = asset.get("workspacePath")
                merged_tts["canonicalPath"] = asset.get("canonicalPath") or asset.get("workspacePath")
                merged_tts["pathPlane"] = asset.get("pathPlane") or merged_tts.get("pathPlane")
                merged_tts["storageClass"] = asset.get("storageClass") or merged_tts.get("storageClass")
            patch["lastTts"] = merged_tts
        return self._save_runtime_state(patch)

    @staticmethod
    def _first_asset_from_manifest(message_assets: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(message_assets, dict):
            return None
        assets = [dict(item) for item in list(message_assets.get("assets") or []) if isinstance(item, dict)]
        if not assets:
            return None
        first = dict(assets[0])
        first["messageSlot"] = str(message_assets.get("messageSlot") or "").strip() or None
        first["workspaceDirectory"] = str(message_assets.get("workspaceDirectory") or "").strip() or None
        return first

    def _managed_local_extensions_root(self) -> Path:
        return self.managed_local_root() / "extensions"

    def _managed_local_weixin_plugin_dir(self) -> Path:
        return self._managed_local_extensions_root() / "openclaw-weixin"

    def _managed_local_configured_plugin_paths(self) -> set[Path]:
        payload = self._read_managed_local_openclaw_config()
        plugins_payload = dict(payload.get("plugins") or {})
        installs_payload = dict(plugins_payload.get("installs") or {})
        load_paths = list(dict(plugins_payload.get("load") or {}).get("paths") or [])

        configured_paths: set[Path] = set()

        def _append_candidate(raw_path: Any) -> None:
            path_text = str(raw_path or "").strip()
            if not path_text:
                return
            candidate = Path(path_text).expanduser()
            if not candidate.is_absolute():
                candidate = (self.managed_local_root() / candidate).expanduser()
            if not candidate.exists():
                return
            try:
                configured_paths.add(candidate.resolve())
            except Exception:
                configured_paths.add(candidate)

        for install in installs_payload.values():
            if not isinstance(install, dict):
                continue
            _append_candidate(install.get("installPath") or install.get("sourcePath"))

        for load_path in load_paths:
            _append_candidate(load_path)

        return configured_paths

    @staticmethod
    def _path_within_root(candidate: Path, root: Path) -> bool:
        try:
            normalized_candidate = candidate.expanduser().resolve()
            normalized_root = root.expanduser().resolve()
        except Exception:
            normalized_candidate = candidate.expanduser()
            normalized_root = root.expanduser()
        return normalized_candidate == normalized_root or normalized_root in normalized_candidate.parents

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        try:
            return left.expanduser().resolve() == right.expanduser().resolve()
        except Exception:
            return str(left.expanduser()) == str(right.expanduser())

    @staticmethod
    def _job_contains_path_hint(job: dict[str, Any], candidate_root: Path) -> bool:
        normalized_root = str(candidate_root.expanduser()).strip().lower()
        if not normalized_root:
            return False
        try:
            haystack = json.dumps(job, ensure_ascii=False).lower()
        except Exception:
            haystack = str(job).lower()
        return normalized_root in haystack

    def _plugin_belongs_to_current_managed_root(self, plugin: dict[str, Any] | None) -> bool:
        if self.is_external_host():
            return True
        candidate = dict(plugin or {})
        install_path_raw = str(candidate.get("installPath") or "").strip()
        if not install_path_raw:
            return False
        install_path = Path(install_path_raw).expanduser()
        if self._path_within_root(install_path, self._managed_local_extensions_root()):
            return True
        try:
            normalized_install_path = install_path.resolve()
        except Exception:
            normalized_install_path = install_path
        return normalized_install_path in self._managed_local_configured_plugin_paths()

    def _managed_local_plugin_records(self, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        registry_payload = payload or default_plugin_registry()
        raw_plugins = registry_payload.get("plugins") or {}
        if isinstance(raw_plugins, dict):
            plugin_items = list(raw_plugins.values())
        elif isinstance(raw_plugins, list):
            plugin_items = list(raw_plugins)
        else:
            plugin_items = []
        return [
            dict(item)
            for item in plugin_items
            if isinstance(item, dict) and self._plugin_belongs_to_current_managed_root(item)
        ]

    def _install_job_matches_current_managed_root(
        self,
        job: dict[str, Any],
        *,
        current_plugin_ids: set[str],
        current_install_specs: set[str],
    ) -> bool:
        if self.is_external_host():
            return True
        current_root = self.managed_local_root()
        if not self._same_path(current_root, PLUGIN_HOST_ROOT):
            explicit_roots = [
                str(job.get("managedRoot") or "").strip(),
                str(job.get("pluginRoot") or "").strip(),
                str(job.get("pluginExtensionsRoot") or "").strip(),
            ]
            normalized_explicit_roots = [item for item in explicit_roots if item]
            if normalized_explicit_roots:
                return any(self._same_path(Path(item), current_root) for item in normalized_explicit_roots)
            return self._job_contains_path_hint(job, current_root)
        managed_root_raw = str(job.get("managedRoot") or job.get("pluginRoot") or "").strip()
        if managed_root_raw:
            return self._same_path(Path(managed_root_raw), current_root)
        if not self._same_path(current_root, PLUGIN_HOST_ROOT) and self._job_contains_path_hint(job, PLUGIN_HOST_ROOT):
            return False
        plugin_id = str(job.get("pluginId") or "").strip()
        if plugin_id and plugin_id in current_plugin_ids:
            return True
        install_spec = str(job.get("installSpec") or "").strip()
        if install_spec and install_spec in current_install_specs:
            return True
        return False

    @staticmethod
    def _install_job_sort_key(job: dict[str, Any]) -> str:
        return str(job.get("updatedAt") or job.get("finishedAt") or job.get("createdAt") or "")

    def _compact_install_jobs(self, jobs: dict[str, Any]) -> dict[str, Any]:
        grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            plugin_id = str(job.get("pluginId") or "").strip()
            install_spec = str(job.get("installSpec") or "").strip()
            key = plugin_id or install_spec or "__unscoped__"
            grouped.setdefault(key, []).append((str(job_id), dict(job)))

        compacted: dict[str, Any] = {}
        for entries in grouped.values():
            entries.sort(key=lambda item: self._install_job_sort_key(item[1]), reverse=True)
            for job_id, job in entries[:4]:
                compacted[job_id] = job
        return compacted

    def _prune_managed_local_registry_noise(self, registry: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.is_external_host():
            return registry or default_plugin_registry()

        payload = dict(registry or default_plugin_registry())
        raw_plugins = payload.get("plugins") or {}
        raw_jobs = payload.get("installJobs") or {}
        if not isinstance(raw_plugins, dict):
            raw_plugins = {}
        if not isinstance(raw_jobs, dict):
            raw_jobs = {}

        filtered_plugins: dict[str, Any] = {}
        for plugin_id, plugin in raw_plugins.items():
            if not isinstance(plugin, dict):
                continue
            if self._plugin_belongs_to_current_managed_root(plugin):
                filtered_plugins[str(plugin_id)] = dict(plugin)

        current_plugin_ids = {
            str(plugin.get("pluginId") or "").strip()
            for plugin in filtered_plugins.values()
            if isinstance(plugin, dict) and str(plugin.get("pluginId") or "").strip()
        }
        current_install_specs = {
            str(plugin.get("installSpec") or "").strip()
            for plugin in filtered_plugins.values()
            if isinstance(plugin, dict) and str(plugin.get("installSpec") or "").strip()
        }

        filtered_jobs: dict[str, Any] = {}
        removed_job_ids: list[str] = []
        for job_id, job in raw_jobs.items():
            if not isinstance(job, dict):
                removed_job_ids.append(str(job_id))
                continue
            if self._install_job_matches_current_managed_root(
                job,
                current_plugin_ids=current_plugin_ids,
                current_install_specs=current_install_specs,
            ):
                filtered_jobs[str(job_id)] = dict(job)
            else:
                removed_job_ids.append(str(job_id))

        compacted_jobs = self._compact_install_jobs(filtered_jobs)
        removed_job_ids.extend([job_id for job_id in filtered_jobs.keys() if job_id not in compacted_jobs])
        filtered_jobs = compacted_jobs

        changed = filtered_plugins != raw_plugins or filtered_jobs != raw_jobs
        if not changed:
            return payload

        for job_id in removed_job_ids:
            task = self._install_tasks.pop(job_id, None)
            if task and not task.done():
                task.cancel()

        payload["plugins"] = filtered_plugins
        payload["installJobs"] = filtered_jobs
        save_plugin_registry(payload)
        return default_plugin_registry()

    def materialize_inbound_asset(
        self,
        *,
        source_path: str | None = None,
        source_url: str | None = None,
        preferred_name: str | None = None,
        asset_kind: str | None = None,
        delivery_mode: str = "attachment",
    ) -> dict[str, Any] | None:
        message_assets = self.materialize_inbound_assets(
            source_path=source_path,
            source_url=source_url,
            preferred_name=preferred_name,
            asset_kind=asset_kind,
            delivery_mode=delivery_mode,
        )
        return self._first_asset_from_manifest(message_assets)

    def materialize_inbound_assets(
        self,
        *,
        source_path: str | None = None,
        source_url: str | None = None,
        preferred_name: str | None = None,
        asset_kind: str | None = None,
        delivery_mode: str = "attachment",
        message_slot: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        replace_root: bool = True,
    ) -> dict[str, Any] | None:
        normalized_sources = list(sources or []) or normalize_asset_sources(
            source_path=source_path,
            source_url=source_url,
            preferred_name=preferred_name,
            asset_kind=asset_kind,
            delivery_mode=delivery_mode,
        )
        workspace_root = workspace_resolution_service.get_main_workspace_path()
        message_assets = materialize_last_assets(
            direction="inbound",
            sources=normalized_sources,
            message_slot=message_slot,
            replace_root=replace_root,
            workspace_root=workspace_root,
        )
        if message_assets:
            self._record_asset_state(
                direction="inbound",
                asset=self._first_asset_from_manifest(message_assets),
                message_assets=message_assets,
            )
        return message_assets

    def materialize_outbound_asset(
        self,
        *,
        source_path: str | None = None,
        source_url: str | None = None,
        preferred_name: str | None = None,
        asset_kind: str | None = None,
        delivery_mode: str = "attachment",
        tts_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        message_assets = self.materialize_outbound_assets(
            source_path=source_path,
            source_url=source_url,
            preferred_name=preferred_name,
            asset_kind=asset_kind,
            delivery_mode=delivery_mode,
            tts_meta=tts_meta,
        )
        return self._first_asset_from_manifest(message_assets)

    def materialize_outbound_assets(
        self,
        *,
        source_path: str | None = None,
        source_url: str | None = None,
        preferred_name: str | None = None,
        asset_kind: str | None = None,
        delivery_mode: str = "attachment",
        tts_meta: dict[str, Any] | None = None,
        message_slot: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        replace_root: bool = True,
        record_state: bool = True,
    ) -> dict[str, Any] | None:
        normalized_sources = list(sources or []) or normalize_asset_sources(
            source_path=source_path,
            source_url=source_url,
            preferred_name=preferred_name,
            asset_kind=asset_kind,
            delivery_mode=delivery_mode,
        )
        message_assets = materialize_last_assets(
            direction="outbound",
            sources=normalized_sources,
            message_slot=message_slot,
            replace_root=replace_root,
        )
        if record_state and (message_assets or tts_meta is not None):
            self._record_asset_state(
                direction="outbound",
                asset=self._first_asset_from_manifest(message_assets),
                message_assets=message_assets,
                tts_meta=tts_meta,
            )
        return message_assets

    def record_tts_result(self, *, audio_codec: str, fallback_reason: str, file_path: str | None) -> dict[str, Any]:
        payload = {
            "audioCodec": str(audio_codec or "").strip() or None,
            "fallbackReason": str(fallback_reason or "").strip() or None,
            "sourcePath": str(file_path or "").strip() or None,
            "pathPlane": "runtime_private",
            "storageClass": "ephemeral",
            "generatedAt": _now_iso(),
        }
        return self._save_runtime_state({"lastTts": payload})

    @staticmethod
    def _attachment_buffer_key(*, channel_type: str, remote_id: str, account_id: str | None) -> str:
        return "||".join(
            [
                str(channel_type or "").strip().lower(),
                str(remote_id or "").strip(),
                str(account_id or "").strip(),
            ]
        )

    @staticmethod
    def _attachment_note(message_assets: dict[str, Any] | None) -> str | None:
        if not isinstance(message_assets, dict):
            return None
        assets = [dict(item) for item in list(message_assets.get("assets") or []) if isinstance(item, dict)]
        if not assets:
            return None
        directory = str(message_assets.get("workspaceDirectory") or "").strip()
        if directory:
            return f"附件已下载到本地目录：{directory}"
        paths = [str(item.get("canonicalPath") or item.get("workspacePath") or "").strip() for item in assets if str(item.get("canonicalPath") or item.get("workspacePath") or "").strip()]
        if not paths:
            return None
        if len(paths) == 1:
            return f"附件已下载到本地：{paths[0]}"
        return "附件已下载到本地：\n" + "\n".join(f"- {item}" for item in paths)

    @staticmethod
    def _merge_message_asset_manifests(
        left: dict[str, Any] | None,
        right: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(left, dict):
            return dict(right or {}) if isinstance(right, dict) else None
        if not isinstance(right, dict):
            return dict(left)
        merged_assets: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for candidate in [left, right]:
            for asset in list(candidate.get("assets") or []):
                if not isinstance(asset, dict):
                    continue
                workspace_path = str(asset.get("workspacePath") or "").strip()
                dedupe_key = workspace_path or json.dumps(asset, ensure_ascii=False, sort_keys=True)
                if dedupe_key in seen_paths:
                    continue
                seen_paths.add(dedupe_key)
                merged_assets.append(dict(asset))
        return {
            "messageSlot": str(right.get("messageSlot") or left.get("messageSlot") or "").strip() or None,
            "workspaceDirectory": str(right.get("workspaceDirectory") or left.get("workspaceDirectory") or "").strip() or None,
            "canonicalPath": str(right.get("canonicalPath") or left.get("canonicalPath") or "").strip() or None,
            "workspaceRoot": str(right.get("workspaceRoot") or left.get("workspaceRoot") or "").strip() or None,
            "workspaceRelativePath": str(right.get("workspaceRelativePath") or left.get("workspaceRelativePath") or "").strip() or None,
            "assetCount": len(merged_assets),
            "direction": str(right.get("direction") or left.get("direction") or "").strip() or None,
            "deliveryMode": str(right.get("deliveryMode") or left.get("deliveryMode") or "").strip() or "attachment",
            "pathPlane": str(right.get("pathPlane") or left.get("pathPlane") or "").strip() or None,
            "storageClass": str(right.get("storageClass") or left.get("storageClass") or "").strip() or None,
            "assets": merged_assets,
        }

    async def _dispatch_inbound_runtime_message(
        self,
        *,
        channel_type: str,
        remote_id: str,
        chat_type: str,
        content: str,
        sender_id: str | None,
        sender_name: str | None,
        metadata: dict[str, Any],
        audio_trigger: bool,
        record_only: bool,
    ) -> dict[str, Any]:
        from runtimes.plugin_host.runtime import PluginHostMessage, plugin_host_runtime

        self.record_inbound_handoff()
        response_text, tts_file_path, session_id = await plugin_host_runtime.dispatch_message(
            source=channel_type,
            chat_type=chat_type,
            remote_id=remote_id,
            message=PluginHostMessage(
                role="user",
                content=content,
                sender_id=sender_id,
                sender_name=sender_name,
                metadata=metadata,
            ),
            audio_trigger=audio_trigger,
            record_only=record_only,
        )
        return {
            "status": "success",
            "sessionId": session_id,
            "response": response_text,
            "ttsFilePath": tts_file_path,
        }

    async def _flush_attachment_only_buffer(self, buffer_key: str) -> None:
        await asyncio.sleep(10)
        async with self._attachment_only_lock:
            buffered = self._attachment_only_buffers.pop(buffer_key, None)
        if not buffered:
            return
        manifest = dict(buffered.get("messageAssets") or {})
        metadata = dict(buffered.get("metadata") or {})
        note = self._attachment_note(manifest)
        if note:
            metadata["attachment_note"] = note
            metadata.setdefault("workspace_directory", manifest.get("workspaceDirectory"))
            metadata["workspace_paths"] = [
                str(item.get("workspacePath") or "").strip()
                for item in list(manifest.get("assets") or [])
                if isinstance(item, dict) and str(item.get("workspacePath") or "").strip()
            ]
            metadata["message_assets"] = manifest
            metadata["media_asset"] = self._first_asset_from_manifest(manifest)
            metadata["workspace_path"] = str((metadata.get("workspace_paths") or [metadata.get("workspace_path") or ""])[0] or "").strip() or metadata.get("workspace_path")
            metadata["canonical_path"] = str(manifest.get("canonicalPath") or metadata.get("workspace_path") or "").strip() or None
            metadata["path_plane"] = str(manifest.get("pathPlane") or "").strip() or None
            metadata["storage_class"] = str(manifest.get("storageClass") or "").strip() or None
        synthetic_content = "[附件消息]"
        try:
            await self._dispatch_inbound_runtime_message(
                channel_type=str(buffered.get("channelType") or ""),
                remote_id=str(buffered.get("remoteId") or ""),
                chat_type=str(buffered.get("chatType") or "p2p"),
                content=synthetic_content,
                sender_id=str(buffered.get("senderId") or "").strip() or None,
                sender_name=str(buffered.get("senderName") or "").strip() or None,
                metadata={
                    **metadata,
                    "synthetic_inbound": True,
                    "buffered_attachment_only": True,
                },
                audio_trigger=bool(buffered.get("audioTrigger", False)),
                record_only=bool(buffered.get("recordOnly", False)),
            )
        except Exception as exc:
            print(f"[PluginHostRuntime] attachment-only buffer flush failed: {exc}")

    def _require_valid_inbound_handoff_token(self, *, headers: dict[str, Any] | None = None) -> None:
        expected_token = self._managed_local_handoff_token()
        if not expected_token:
            raise RuntimeError("当前 V8 PluginHost 尚未生成可用的 handoff token。")
        normalized_headers = {
            str(key or "").strip().lower(): str(value or "").strip()
            for key, value in dict(headers or {}).items()
            if str(key or "").strip()
        }
        presented_token = str(
            normalized_headers.get("x-v8-agent-os-plugin-host-handoff-token")
            or normalized_headers.get("x_v8_agent_os_plugin_host_handoff_token")
            or ""
        ).strip()
        authorization = str(normalized_headers.get("authorization") or "").strip()
        if not presented_token and authorization.lower().startswith("bearer "):
            presented_token = authorization[7:].strip()
        if not presented_token:
            raise RuntimeError("当前入站 handoff 缺少有效 handoff token。")
        if presented_token != expected_token:
            raise RuntimeError("当前入站 handoff token 无效。")

    async def handle_inbound_handoff(
        self,
        *,
        client_host: str,
        headers: dict[str, Any] | None = None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_client = str(client_host or "").strip()
        if normalized_client not in {"127.0.0.1", "::1", "0:0:0:0:0:0:0:1", "localhost"}:
            raise RuntimeError("当前入站 handoff 仅允许本机 OpenClaw bridge / gateway 调用。")
        self._require_valid_inbound_handoff_token(headers=headers)

        channel_type = str(payload.get("channelType") or "").strip()
        remote_id = str(payload.get("remoteId") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not channel_type or not remote_id:
            raise ValueError("缺少 channelType / remoteId")

        metadata = dict(payload.get("metadata") or {})
        account_id = str(payload.get("accountId") or "").strip() or None
        account_scope = str(payload.get("accountScope") or metadata.get("account_scope") or metadata.get("accountScope") or "").strip() or None
        thread_id = str(payload.get("threadId") or "").strip() or None
        mentions = [
            dict(item)
            for item in list(payload.get("mentions") or metadata.get("mentions") or [])
            if isinstance(item, dict)
        ]
        attachments = [
            dict(item)
            for item in list(payload.get("attachments") or metadata.get("attachments") or [])
            if isinstance(item, dict)
        ]
        event_kind = str(payload.get("eventKind") or metadata.get("event_kind") or metadata.get("eventKind") or "").strip() or None
        event_subtype = str(payload.get("eventSubtype") or metadata.get("event_subtype") or metadata.get("eventSubtype") or "").strip() or None
        raw_action_payload = payload.get("actionPayload")
        if raw_action_payload is None:
            raw_action_payload = metadata.get("action_payload")
        if raw_action_payload is None:
            raw_action_payload = metadata.get("actionPayload")
        action_payload = dict(raw_action_payload) if isinstance(raw_action_payload, dict) else {}
        raw_payload_ref = dict(payload.get("rawPayloadRef") or metadata.get("raw_payload_ref") or metadata.get("rawPayloadRef") or {})
        channel_envelope = dict(payload.get("channelEnvelope") or metadata.get("channel_envelope") or metadata.get("channelEnvelope") or {})
        metadata.setdefault("account_id", account_id)
        metadata.setdefault("account_scope", account_scope)
        metadata.setdefault("default_account", str(payload.get("defaultAccount") or "").strip() or None)
        metadata.setdefault("message_id", str(payload.get("messageId") or "").strip() or None)
        metadata.setdefault("context_token", str(payload.get("contextToken") or "").strip() or None)
        metadata.setdefault("channel_type", channel_type)
        metadata.setdefault("channel_name", str(payload.get("channelName") or "").strip() or channel_type)
        metadata.setdefault("channel_domain", str(payload.get("channelDomain") or "").strip() or None)
        if thread_id:
            metadata.setdefault("thread_id", thread_id)
        if mentions:
            metadata.setdefault("mentions", mentions)
        if attachments:
            metadata.setdefault("attachments", attachments)
        if event_kind:
            metadata.setdefault("event_kind", event_kind)
        if event_subtype:
            metadata.setdefault("event_subtype", event_subtype)
        if action_payload:
            metadata.setdefault("action_payload", action_payload)
        if raw_payload_ref:
            metadata.setdefault("raw_payload_ref", raw_payload_ref)
        normalized_channel_envelope = {
            **channel_envelope,
            "channelId": str((channel_envelope or {}).get("channelId") or channel_type).strip() or channel_type,
            "conversationId": str((channel_envelope or {}).get("conversationId") or payload.get("conversationId") or remote_id).strip() or remote_id,
            "remoteId": str((channel_envelope or {}).get("remoteId") or remote_id).strip() or remote_id,
            "messageId": str((channel_envelope or {}).get("messageId") or metadata.get("message_id") or "").strip() or None,
            "accountId": account_id,
            "accountScope": account_scope,
            "chatType": str((channel_envelope or {}).get("chatType") or payload.get("chatType") or "p2p").strip() or "p2p",
            "threadId": thread_id,
            "senderId": str((channel_envelope or {}).get("senderId") or payload.get("senderId") or "").strip() or None,
            "senderName": str((channel_envelope or {}).get("senderName") or payload.get("senderName") or "").strip() or None,
            "mentions": mentions,
            "attachments": attachments,
            "eventKind": event_kind,
            "eventSubtype": event_subtype,
            "actionPayload": action_payload or None,
            "rawPayloadRef": raw_payload_ref or None,
        }
        metadata["channel_envelope"] = normalized_channel_envelope
        media_path = str(payload.get("mediaPath") or "").strip()
        media_url = str(payload.get("mediaUrl") or "").strip()
        if not text and not media_path and not media_url:
            raise ValueError("缺少可处理的文本或媒体上下文")
        if media_path:
            metadata.setdefault("media_path", media_path)
        if media_url:
            metadata.setdefault("media_url", media_url)
        preferred_name = (
            str(payload.get("fileName") or "").strip()
            or str(payload.get("originalFileName") or "").strip()
            or str(metadata.get("file_name") or "").strip()
            or None
        )
        asset_kind = str(payload.get("assetKind") or metadata.get("asset_kind") or "").strip() or None
        message_slot = str(payload.get("messageId") or "").strip() or f"inbound_{uuid.uuid4().hex[:10]}"
        message_assets = None
        if media_path or media_url:
            try:
                message_assets = self.materialize_inbound_assets(
                    source_path=media_path or None,
                    source_url=media_url or None,
                    preferred_name=preferred_name,
                    asset_kind=asset_kind,
                    delivery_mode="attachment",
                    message_slot=message_slot,
                    replace_root=not bool(text),
                )
            except Exception as asset_exc:
                metadata["media_asset_error"] = str(asset_exc).strip() or asset_exc.__class__.__name__
        if message_assets:
            first_asset = self._first_asset_from_manifest(message_assets)
            attachment_note = self._attachment_note(message_assets)
            workspace_paths = [
                str(item.get("workspacePath") or "").strip()
                for item in list(message_assets.get("assets") or [])
                if isinstance(item, dict) and str(item.get("workspacePath") or "").strip()
            ]
            metadata.update(
                {
                    "workspace_path": str((workspace_paths or [first_asset.get("workspacePath") if first_asset else ""])[0] or "").strip() or None,
                    "workspace_paths": workspace_paths,
                    "workspace_directory": message_assets.get("workspaceDirectory"),
                    "canonical_path": str(message_assets.get("canonicalPath") or "").strip() or None,
                    "path_plane": str(message_assets.get("pathPlane") or "").strip() or None,
                    "storage_class": str(message_assets.get("storageClass") or "").strip() or None,
                    "media_asset": first_asset,
                    "message_assets": message_assets,
                    "asset_kind": (first_asset or {}).get("assetKind"),
                    "mime_type": (first_asset or {}).get("mimeType"),
                    "original_file_name": (first_asset or {}).get("originalFileName"),
                    "attachment_note": attachment_note,
                }
            )
        bridge_plugin_id = str(payload.get("bridgePluginId") or metadata.get("bridgePluginId") or "").strip() or None
        metadata.setdefault("transport_managed_by", "openclaw_bridge")
        metadata.setdefault("inbound_ownership", "v8")
        metadata.setdefault("handoff_source", str(payload.get("handoffSource") or metadata.get("handoffSource") or "openclaw_bridge"))
        metadata.setdefault("bridge_plugin_id", bridge_plugin_id)
        sender_id = str(payload.get("senderId") or "").strip() or None
        sender_name = str(payload.get("senderName") or "").strip() or None
        chat_type = "group" if str(payload.get("chatType") or "").strip().lower() == "group" else "p2p"
        metadata.setdefault("chat_type", chat_type)
        audio_trigger = bool(payload.get("audioTrigger", False))
        record_only = bool(payload.get("recordOnly", False))
        buffer_key = self._attachment_buffer_key(channel_type=channel_type, remote_id=remote_id, account_id=account_id)

        async with self._attachment_only_lock:
            buffered = self._attachment_only_buffers.get(buffer_key)
            if not text and message_assets:
                if buffered:
                    existing_task = buffered.get("task")
                    if existing_task and not existing_task.done():
                        existing_task.cancel()
                    buffered["messageAssets"] = self._merge_message_asset_manifests(
                        dict(buffered.get("messageAssets") or {}),
                        message_assets,
                    )
                    buffered["metadata"] = {**dict(buffered.get("metadata") or {}), **metadata}
                    buffered["messageId"] = str(metadata.get("message_id") or buffered.get("messageId") or "").strip() or None
                else:
                    buffered = {
                        "channelType": channel_type,
                        "remoteId": remote_id,
                        "chatType": chat_type,
                        "accountId": account_id,
                        "senderId": sender_id,
                        "senderName": sender_name,
                        "messageId": metadata.get("message_id"),
                        "audioTrigger": audio_trigger,
                        "recordOnly": record_only,
                        "metadata": metadata,
                        "messageAssets": message_assets,
                    }
                    self._attachment_only_buffers[buffer_key] = buffered
                task = asyncio.create_task(self._flush_attachment_only_buffer(buffer_key))
                buffered["task"] = task
                self._attachment_only_buffers[buffer_key] = buffered
                return {
                    "status": "buffered",
                    "buffered": True,
                    "bufferWindowSeconds": 10,
                    "messageAssets": buffered.get("messageAssets"),
                }

            if buffered:
                existing_task = buffered.get("task")
                if existing_task and not existing_task.done():
                    existing_task.cancel()
                self._attachment_only_buffers.pop(buffer_key, None)
                buffered_manifest = dict(buffered.get("messageAssets") or {})
                message_assets = self._merge_message_asset_manifests(buffered_manifest, message_assets)
                metadata = {
                    **dict(buffered.get("metadata") or {}),
                    **metadata,
                }
                if message_assets:
                    first_asset = self._first_asset_from_manifest(message_assets)
                    attachment_note = self._attachment_note(message_assets)
                    workspace_paths = [
                        str(item.get("workspacePath") or "").strip()
                        for item in list(message_assets.get("assets") or [])
                        if isinstance(item, dict) and str(item.get("workspacePath") or "").strip()
                    ]
                    metadata.update(
                        {
                            "workspace_path": str((workspace_paths or [first_asset.get("workspacePath") if first_asset else ""])[0] or "").strip() or None,
                            "workspace_paths": workspace_paths,
                            "workspace_directory": message_assets.get("workspaceDirectory"),
                            "canonical_path": str(message_assets.get("canonicalPath") or "").strip() or None,
                            "path_plane": str(message_assets.get("pathPlane") or "").strip() or None,
                            "storage_class": str(message_assets.get("storageClass") or "").strip() or None,
                            "media_asset": first_asset,
                            "message_assets": message_assets,
                            "attachment_note": attachment_note,
                        }
                    )

        content = text or "[媒体消息]"
        return await self._dispatch_inbound_runtime_message(
            channel_type=channel_type,
            remote_id=remote_id,
            chat_type=chat_type,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            metadata=metadata,
            audio_trigger=audio_trigger,
            record_only=record_only,
        )

    def _mark_managed_local_reconciled(self, *, auto_start_drift_detected: bool) -> dict[str, Any]:
        return self._save_runtime_state(
            {
                "lifecycleAuthority": "manual_local",
                "autoStartDriftDetected": bool(auto_start_drift_detected),
                "reconciledAt": _now_iso(),
            }
        )

    def _external_plugin_host_url(self, suffix: str = "") -> str:
        external = self.external_host_config()
        base_url = str(external.get("baseUrl") or "").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("当前为 external host 模式，但未配置 OpenClaw Host Base URL。")
        if base_url.endswith("/plugin-host"):
            root = base_url
        elif base_url.endswith("/v1"):
            root = f"{base_url}/plugin-host"
        else:
            root = f"{base_url}/v1/plugin-host"
        normalized_suffix = str(suffix or "").strip()
        if not normalized_suffix:
            return root
        return f"{root}/{normalized_suffix.lstrip('/')}"

    def _external_request_json(self, *, method: str, suffix: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._external_plugin_host_url(suffix)
        external = self.external_host_config()
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        auth_token = str(external.get("authToken") or "").strip()
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
            headers["X-V8Chat-Plugin-Host-Token"] = auth_token
        request = urllib_request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"外部 PluginHost 请求失败：{exc.code} {detail or exc.reason}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"无法连接外部 PluginHost：{exc.reason}") from exc
        try:
            return json.loads(body or "{}")
        except Exception as exc:
            raise RuntimeError(f"外部 PluginHost 返回了非 JSON 响应：{body[:200]}") from exc

    def _openclaw_gateway_request_json(
        self,
        *,
        suffix: str,
        payload: dict[str, Any] | None = None,
        gateway_token: str | None = None,
        timeout: float = 20,
    ) -> dict[str, Any]:
        base_url = self._managed_local_gateway_base_url().rstrip("/")
        url = f"{base_url}/{suffix.lstrip('/')}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        token = str(gateway_token or self._managed_local_gateway_auth_token() or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib_request.Request(url, data=data, method="POST" if payload is not None else "GET", headers=headers)
        try:
            with urllib_request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenClaw gateway 请求失败：{exc.code} {detail or exc.reason}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"无法连接 OpenClaw gateway：{exc.reason}") from exc
        try:
            parsed = json.loads(body or "{}")
        except Exception as exc:
            raise RuntimeError(f"OpenClaw gateway 返回了非 JSON 响应：{body[:200]}") from exc
        return dict(parsed) if isinstance(parsed, dict) else {"ok": True, "result": parsed}

    async def start(self) -> None:
        self._background_refresh_task = None
        if not self.is_enabled():
            self._set_cached_public_snapshot(self._minimal_public_snapshot())
            self._startup_state = "ready"
            self._snapshot_freshness = "cached"
            return
        if self.is_external_host():
            self._save_runtime_state({"lifecycleAuthority": "external_managed"})
            if self._cached_public_snapshot is None:
                self._set_cached_public_snapshot(self._minimal_public_snapshot())
            self._mark_snapshot_refreshing()
            self._schedule_background_refresh(refresh_registry=self._background_refresh_requested())
            return
        try:
            self._ensure_managed_local_bridge_extension_link()
        except Exception:
            pass
        try:
            self._ensure_minimal_managed_local_openclaw_host_config()
        except Exception:
            pass
        try:
            self._ensure_managed_local_gateway_launcher_handoff()
        except Exception:
            pass
        self._save_runtime_state({"lifecycleAuthority": "manual_local"})
        if self._cached_public_snapshot is None:
            self._set_cached_public_snapshot(self._minimal_public_snapshot())
        self._mark_snapshot_refreshing()
        self._schedule_background_refresh(refresh_registry=self._background_refresh_requested())

    async def stop(self) -> None:
        for task in list(self._install_tasks.values()):
            if task.done():
                continue
            task.cancel()
        self._install_tasks.clear()
        if self._background_refresh_task and not self._background_refresh_task.done():
            self._background_refresh_task.cancel()
            try:
                await self._background_refresh_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._background_refresh_task = None
        if self.is_managed_local():
            self._save_runtime_state({"lifecycleAuthority": "manual_local"})

    async def _stop_managed_local_gateway(self) -> None:
        env = self._managed_local_env()
        try:
            await self._run_gateway_lifecycle_command(env=env, append_event=lambda *_args, **_kwargs: None, action="stop")
        except Exception:
            pass
        await self._force_stop_managed_local_gateway_processes(append_event=lambda *_args, **_kwargs: None)

    def _managed_local_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["OPENCLAW_STATE_DIR"] = str(self.managed_local_root())
        env["V8_AGENT_OS_PLUGIN_HOST_INBOUND_URL"] = f"{self.managed_local_engine_base_url()}/v1/plugin-host/inbound"
        handoff_token = self._managed_local_handoff_token()
        if handoff_token:
            env["V8_AGENT_OS_PLUGIN_HOST_HANDOFF_TOKEN"] = handoff_token
        tooling_root = self.managed_local_tooling_root()
        global_npm_cli = self._windows_global_npm_openclaw_cli()
        global_npm_root = global_npm_cli.parent if global_npm_cli else None
        if tooling_root:
            entries = [tooling_root / "bin", tooling_root / "node_modules" / ".bin", tooling_root]
            if global_npm_root:
                entries.append(global_npm_root)
            return self._prepend_path(env, *entries)
        if global_npm_root:
            return self._prepend_path(env, global_npm_root)
        return env

    def _require_managed_local_gateway_ready(self, *, purpose: str) -> dict[str, Any]:
        gateway_health = self._managed_local_gateway_health()
        if bool((gateway_health.get("health") or {}).get("healthy")):
            return gateway_health
        reason = str(gateway_health.get("error") or ((gateway_health.get("runtime") or {}).get("detail") or "")).strip()
        detail = reason or "本地 OpenClaw gateway 未就绪。"
        raise RuntimeError(
            f"当前已禁用 Engine 自动拉起 OpenClaw。请先手动启动本地 OpenClaw gateway，再{purpose}。"
            f" 当前状态：{detail}"
        )

    def _sync_managed_local_plugins_allowlist(self, *, payload: dict[str, Any] | None = None) -> list[str]:
        registry = dict(payload) if isinstance(payload, dict) else default_plugin_registry()
        plugins = {
            str(plugin.get("pluginId") or "").strip(): plugin
            for plugin in self._managed_local_plugin_records(registry)
            if str((plugin or {}).get("pluginId") or "").strip()
        }
        trusted_plugin_ids = {
            plugin_id
            for plugin_id, plugin in plugins.items()
            if str(plugin_id).strip()
            and Path(str((plugin or {}).get("installPath") or "")).expanduser().exists()
        }
        try:
            inventory = self._managed_local_plugins_inventory(refresh=True)
        except Exception:
            inventory = {"plugins": []}
        configured_channel_ids = {
            _normalize_openclaw_channel_id(str(channel_id).strip())
            for channel_id in self._configured_openclaw_channel_ids()
            if _normalize_openclaw_channel_id(str(channel_id).strip())
        }
        for plugin in list(inventory.get("plugins") or []):
            if not isinstance(plugin, dict):
                continue
            plugin_id = str(plugin.get("id") or "").strip()
            if not plugin_id:
                continue
            channel_ids = {
                _normalize_openclaw_channel_id(str(item).strip())
                for item in [*list(plugin.get("channels") or []), *list(plugin.get("channelIds") or [])]
                if str(item).strip()
            }
            normalized_plugin_id = _normalize_openclaw_channel_id(plugin_id)
            if plugin_id in _OPENCLAW_BRIDGE_PLUGIN_IDS or channel_ids or normalized_plugin_id in configured_channel_ids:
                trusted_plugin_ids.add(plugin_id)
        trusted_plugin_ids = sorted(trusted_plugin_ids)
        if not self._managed_local_bridge_read_only():
            config_payload = self._read_managed_local_openclaw_config()
            plugins_payload = dict(config_payload.get("plugins") or {})
            current_allowlist = [
                str(item).strip()
                for item in list(plugins_payload.get("allow") or [])
                if str(item).strip()
            ]
            if current_allowlist != trusted_plugin_ids:
                plugins_payload["allow"] = trusted_plugin_ids
                config_payload["plugins"] = plugins_payload
                self._write_managed_local_openclaw_config(config_payload)
        return trusted_plugin_ids

    def _managed_local_handoff_status(
        self,
        plugin: dict[str, Any] | None = None,
        *,
        bridge_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = dict(plugin or {})
        channel_target = _normalize_openclaw_channel_id(
            str(self._channel_login_target(candidate) or candidate.get("pluginId") or "").strip()
        ) or None
        bridge_state_payload = dict(bridge_state or self._managed_local_bridge_state())
        managed_channels = {
            _normalize_openclaw_channel_id(str(item).strip())
            for item in list(bridge_state_payload.get("managedChannels") or [])
            if str(item).strip()
        }
        bridge_ready = bool(bridge_state_payload.get("bridgeReady"))
        channel_managed = bool(channel_target and channel_target in managed_channels)
        claim_enabled = bool(bridge_state_payload.get("claimEnabled"))
        last_claim_attempt_at = str(bridge_state_payload.get("lastClaimAttemptAt") or "").strip() or None
        last_claim_outcome = str(bridge_state_payload.get("lastClaimOutcome") or "").strip() or None
        last_claim_decline_reason = str(bridge_state_payload.get("lastClaimDeclineReason") or "").strip() or None
        last_claim_channel = _normalize_openclaw_channel_id(str(bridge_state_payload.get("lastClaimChannel") or "").strip())
        claim_targets_channel = bool(not channel_target or not last_claim_channel or last_claim_channel == channel_target)
        claim_observed = bool(last_claim_attempt_at and claim_targets_channel)
        claim_declining = bool(
            channel_managed
            and claim_observed
            and last_claim_outcome
            and last_claim_outcome not in {"handled", "claimed", "ok"}
        )
        claim_missing_reason = None
        if channel_managed:
            if not bridge_ready:
                claim_missing_reason = "bridge_unready"
            elif not claim_enabled:
                claim_missing_reason = "claim_disabled"
            elif claim_declining:
                claim_missing_reason = last_claim_decline_reason or last_claim_outcome or "claim_declined"
            elif not claim_observed:
                claim_missing_reason = "claim_not_observed"
        expected_bridge_claim_missed = bool(channel_managed and claim_missing_reason)
        effective_handoff_ready = bool(bridge_ready and channel_managed and not expected_bridge_claim_missed)
        return {
            "handoffReady": effective_handoff_ready,
            "inboundOwnership": "v8_owned" if effective_handoff_ready else "delegated",
            "supported": channel_managed,
            "reason": (
                "当前渠道已列入统一 OpenClaw V8 Bridge 的 managedChannels，最近 claim 与 handoff 状态正常。"
                if effective_handoff_ready
                else (
                    "当前渠道理论上应由统一 OpenClaw V8 Bridge 接管，但桥接尚未 ready 或 claim 未命中，消息可能回落到 OpenClaw 原生 runner。"
                    if expected_bridge_claim_missed
                    else "当前未检测到统一 OpenClaw V8 Bridge 已接管该渠道，真实入站仍可能停留在 OpenClaw 自身执行链。"
                )
            ),
            "pluginPatch": None,
            "launcherPatch": None,
            "bridgePluginId": bridge_state_payload.get("pluginId"),
            "managedChannels": sorted(managed_channels),
            "bridgeReady": bridge_ready,
            "claimEnabled": claim_enabled,
            "claimObserved": claim_observed,
            "lastClaimAttemptAt": last_claim_attempt_at,
            "lastClaimOutcome": last_claim_outcome,
            "lastClaimDeclineReason": last_claim_decline_reason or None,
            "claimMissedReason": claim_missing_reason,
            "expectedBridgeClaimMissed": expected_bridge_claim_missed,
        }

    def rescan(self) -> dict[str, Any]:
        return self._refresh_snapshot_blocking(refresh_registry=True)

    def refresh_public_snapshot(self, *, refresh_registry: bool = False) -> dict[str, Any]:
        if refresh_registry:
            return self._refresh_snapshot_blocking(refresh_registry=True)
        if self._cached_public_snapshot is None:
            self._set_cached_public_snapshot(self._minimal_public_snapshot())
        if not self._snapshot_refresh_lock.acquire(blocking=False):
            self._mark_snapshot_refreshing(preserve_error=True)
            return self._decorate_public_snapshot(dict(self._cached_public_snapshot or {}))
        response_snapshot: dict[str, Any] | None = None
        self._refresh_in_flight = True
        try:
            public = self._fast_refresh_public_snapshot()
            refreshed_at = _now_iso()
            self._startup_state = "ready"
            self._snapshot_freshness = "live"
            self._last_refresh_error = None
            self._last_refresh_at = refreshed_at
            self._last_live_refresh_at = refreshed_at
            response_snapshot = self._set_cached_public_snapshot(public)
        except Exception as exc:
            self._startup_state = "error"
            self._snapshot_freshness = "cached"
            self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
            self._last_refresh_at = _now_iso()
            if self._cached_public_snapshot is None:
                self._set_cached_public_snapshot(self._minimal_public_snapshot())
            response_snapshot = dict(self._cached_public_snapshot or {})
        finally:
            self._refresh_in_flight = False
            self._snapshot_refresh_lock.release()
        return self._decorate_public_snapshot(response_snapshot or dict(self._cached_public_snapshot or {}))

    def _shell_command_argv(self, command: str) -> list[str]:
        if os.name == "nt":
            shell = os.environ.get("COMSPEC") or "cmd.exe"
            return [shell, "/d", "/s", "/c", command]
        return ["/bin/sh", "-lc", command]

    def _wrap_windows_executable_argv(self, executable: str, *args: str) -> list[str]:
        shell = os.environ.get("COMSPEC") or "cmd.exe"
        normalized = str(executable)
        if normalized.lower().endswith((".cmd", ".bat")):
            return [shell, "/d", "/c", "call", normalized, *[str(item) for item in args]]
        return [normalized, *[str(item) for item in args]]

    def _direct_command_argv(self, command: str) -> list[str] | None:
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            return None
        if not argv:
            return None
        if os.name == "nt":
            argv = [
                token[1:-1] if len(token) >= 2 and token.startswith('"') and token.endswith('"') else token
                for token in argv
            ]
            executable = shutil.which(argv[0])
            if not executable:
                return None
            if executable.lower().endswith((".cmd", ".bat")):
                return self._wrap_windows_executable_argv(executable, *argv[1:])
            argv[0] = executable
        shell_markers = {"&&", "||", "|", ";", ">", ">>", "<", "2>", "2>>"}
        if any(token in shell_markers for token in argv):
            return None
        return argv

    async def _start_install_process(self, command: str, *, cwd: str, env: dict[str, str]) -> Any:
        argv = self._direct_command_argv(command) or self._shell_command_argv(command)
        return await self._start_install_process_argv(argv, cwd=cwd, env=env)

    async def _start_install_process_argv(self, argv: list[str], *, cwd: str, env: dict[str, str]) -> Any:
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except NotImplementedError:
            return subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def _spawn_background_process_argv(self, argv: list[str], *, cwd: str, env: dict[str, str]) -> subprocess.Popen[Any]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt":
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    async def _read_process_line(self, stream: Any) -> bytes:
        if stream is None:
            return b""
        if isinstance(stream, asyncio.StreamReader):
            return await stream.readline()
        return await asyncio.to_thread(stream.readline)

    def _decode_process_line(self, line: bytes | str) -> str:
        if isinstance(line, str):
            return line.rstrip("\r\n")
        encodings: list[str] = ["utf-8"]
        preferred = str(locale.getpreferredencoding(False) or "").strip()
        if preferred and preferred.lower() not in {item.lower() for item in encodings}:
            encodings.append(preferred)
        if os.name == "nt":
            for encoding in ("gbk", "cp936"):
                if encoding.lower() not in {item.lower() for item in encodings}:
                    encodings.append(encoding)
        for encoding in encodings:
            try:
                return line.decode(encoding).rstrip("\r\n")
            except UnicodeDecodeError:
                continue
        return line.decode(encodings[0], errors="replace").rstrip("\r\n")

    async def _wait_process(self, process: Any) -> int:
        if isinstance(process, asyncio.subprocess.Process):
            return await process.wait()
        return await asyncio.to_thread(process.wait)

    async def _terminate_process(self, process: Any) -> None:
        pid = getattr(process, "pid", None)
        try:
            if isinstance(process, asyncio.subprocess.Process):
                process.terminate()
            else:
                await asyncio.to_thread(process.terminate)
        except Exception:
            pass
        if os.name == "nt" and pid:
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                return

    def _prepend_path(self, env: dict[str, str], *entries: Path) -> dict[str, str]:
        separator = ";" if os.name == "nt" else ":"
        existing = str(env.get("PATH") or "")
        normalized_entries = [str(path) for path in entries if str(path)]
        env["PATH"] = separator.join([*normalized_entries, existing] if existing else normalized_entries)
        return env

    def _tooling_cli_candidates(self, tooling_root: Path) -> list[Path]:
        return [
            tooling_root / "bin" / "openclaw.cmd",
            tooling_root / "bin" / "openclaw",
            tooling_root / "node_modules" / ".bin" / "openclaw.cmd",
            tooling_root / "node_modules" / ".bin" / "openclaw",
            tooling_root / "openclaw.cmd",
            tooling_root / "openclaw",
        ]

    def _tooling_package_root_candidates(self, tooling_root: Path) -> list[Path]:
        return [
            tooling_root / "node_modules" / "openclaw",
            tooling_root / "lib" / "node_modules" / "openclaw",
        ]

    def _windows_global_npm_openclaw_cli(self) -> Path | None:
        if os.name != "nt":
            return None
        appdata = str(os.environ.get("APPDATA") or "").strip()
        if not appdata:
            return None
        npm_root = Path(appdata).expanduser() / "npm"
        for candidate in (npm_root / "openclaw.cmd", npm_root / "openclaw"):
            if candidate.exists():
                return candidate
        return None

    def _bundled_openclaw_cli(self) -> Path | None:
        tooling_root = self.managed_local_tooling_root()
        if not tooling_root:
            return None
        candidates = self._tooling_cli_candidates(tooling_root)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _configured_openclaw_cli(self) -> Path | None:
        if not self._managed_local_tooling_root_explicitly_set():
            return None
        tooling_root = self.managed_local_tooling_root()
        if not tooling_root:
            return None
        for candidate in self._tooling_cli_candidates(tooling_root):
            if candidate.exists():
                return candidate
        return None

    def _derived_local_openclaw_cli(self) -> Path | None:
        if self._managed_local_tooling_root_explicitly_set():
            return None
        tooling_root = self._default_managed_local_tooling_root()
        if not tooling_root:
            return None
        for candidate in self._tooling_cli_candidates(tooling_root):
            if candidate.exists():
                return candidate
        return None

    def _resolve_openclaw_package_root(self, env: dict[str, str] | None = None) -> Path | None:
        tooling_root = self.managed_local_tooling_root()
        if tooling_root:
            for candidate in self._tooling_package_root_candidates(tooling_root):
                if candidate.exists():
                    return candidate
        cli_path = self._resolve_openclaw_cli(env or self._managed_local_env())
        if not cli_path:
            return None
        cli_candidate = Path(cli_path)
        inferred_candidates = [
            cli_candidate.parent.parent / "openclaw",
            cli_candidate.parent.parent / "node_modules" / "openclaw",
            cli_candidate.parent / "node_modules" / "openclaw",
            cli_candidate.parent.parent / "lib" / "node_modules" / "openclaw",
        ]
        for candidate in inferred_candidates:
            if candidate.exists():
                return candidate
        return None

    def _resolve_openclaw_cli(self, env: dict[str, str]) -> str | None:
        configured_cli = self._configured_openclaw_cli()
        if configured_cli:
            return str(configured_cli)
        path_value = env.get("PATH") or os.environ.get("PATH") or ""
        system_cli = shutil.which("openclaw", path=path_value) or shutil.which("openclaw.cmd", path=path_value)
        if system_cli:
            return system_cli
        global_npm_cli = self._windows_global_npm_openclaw_cli()
        if global_npm_cli:
            return str(global_npm_cli)
        derived_cli = self._derived_local_openclaw_cli()
        if derived_cli:
            return str(derived_cli)
        bundled_cli = self._bundled_openclaw_cli()
        if bundled_cli:
            return str(bundled_cli)
        return None

    def _openclaw_cli_source(self, env: dict[str, str]) -> str:
        configured_cli = self._configured_openclaw_cli()
        if configured_cli:
            tooling_root = self.managed_local_tooling_root()
            try:
                if tooling_root and tooling_root.resolve() == self.managed_local_root().resolve():
                    return "state_root_local"
            except Exception:
                if tooling_root and str(tooling_root) == str(self.managed_local_root()):
                    return "state_root_local"
            return "configured_local"
        path_value = env.get("PATH") or os.environ.get("PATH") or ""
        system_cli = shutil.which("openclaw", path=path_value) or shutil.which("openclaw.cmd", path=path_value)
        global_npm_cli = self._windows_global_npm_openclaw_cli()
        if system_cli:
            if global_npm_cli:
                try:
                    if Path(system_cli).resolve() == global_npm_cli.resolve():
                        return "global_npm"
                except Exception:
                    if str(system_cli) == str(global_npm_cli):
                        return "global_npm"
            return "system_path"
        if global_npm_cli:
            return "global_npm"
        derived_cli = self._derived_local_openclaw_cli()
        if derived_cli:
            tooling_root = self._default_managed_local_tooling_root()
            try:
                if tooling_root and tooling_root.resolve() == self.managed_local_root().resolve():
                    return "state_root_local"
            except Exception:
                if tooling_root and str(tooling_root) == str(self.managed_local_root()):
                    return "state_root_local"
            return "bundled_local"
        bundled_cli = self._bundled_openclaw_cli()
        if bundled_cli:
            return "bundled_local"
        return "missing"

    def _control_surface(self, *, runtime_config: dict[str, Any]) -> dict[str, str | None]:
        docs_url = _OPENCLAW_DOCS_URL
        if self.is_external_host():
            external = dict(runtime_config.get("externalHost") or {})
            base_url = str(external.get("baseUrl") or "").strip().rstrip("/")
            dashboard_url = f"{base_url}/" if base_url else None
            return {
                "dashboardUrl": dashboard_url,
                "configUrl": dashboard_url,
                "docsUrl": docs_url,
            }
        dashboard_url = _OPENCLAW_DASHBOARD_URL
        control_config = self._read_managed_local_openclaw_config()
        gateway_auth = dict((control_config.get("gateway") or {}).get("auth") or {})
        gateway_token = str(gateway_auth.get("token") or "").strip()
        if gateway_token:
            encoded_token = urllib_parse.quote(gateway_token, safe="")
            dashboard_url = f"{_OPENCLAW_DASHBOARD_URL}#token={encoded_token}"
        return {
            "dashboardUrl": dashboard_url,
            "configUrl": dashboard_url,
            "docsUrl": docs_url,
        }

    def _gateway_launcher_source(self) -> tuple[str, bool]:
        config = self.get_runtime_config()
        managed_local = dict(config.get("managedLocal") or {})
        raw_value = managed_local.get("launcherPath")
        launcher_path = self.managed_local_launcher_path()
        if launcher_path and launcher_path.exists():
            default_launcher = self.managed_local_root() / "gateway.cmd"
            try:
                if launcher_path.resolve() != default_launcher.resolve():
                    return "configured_launcher", False
            except Exception:
                if str(launcher_path) != str(default_launcher):
                    return "configured_launcher", False
            return "gateway_cmd", False
        if raw_value is not None and str(raw_value).strip():
            return "configured_launcher", True
        return "direct_cli_run", True

    def _resolve_windows_node_openclaw_argv(self, env: dict[str, str], *args: str) -> list[str] | None:
        if os.name != "nt":
            return None
        node_executable = shutil.which("node", path=env.get("PATH") or "") or shutil.which("node")
        package_root = self._resolve_openclaw_package_root(env)
        openclaw_mjs = package_root / "openclaw.mjs" if package_root else None
        if not node_executable or not openclaw_mjs or not openclaw_mjs.exists():
            return None
        return [node_executable, str(openclaw_mjs), *[str(item) for item in args]]

    def _managed_local_gateway_processes(self) -> list[dict[str, Any]]:
        if os.name != "nt":
            return []
        query_script = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Get-CimInstance Win32_Process |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine |
    ConvertTo-Json -Compress
""".strip()
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", query_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return []
        if completed.returncode != 0:
            return []
        raw = str(completed.stdout or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = payload if isinstance(payload, list) else [payload]
        root_dir = str(self.managed_local_root()).lower()
        launcher_path = self.managed_local_launcher_path()
        gateway_cmd_path = str(launcher_path).lower() if launcher_path else ""
        package_root = self._resolve_openclaw_package_root()
        openclaw_runtime_markers = []
        if package_root:
            openclaw_runtime_markers.extend(
                [
                    str((package_root / "dist" / "index.js")).lower(),
                    str((package_root / "openclaw.mjs")).lower(),
                ]
            )
        processes: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            command_line = str(item.get("CommandLine") or "").strip()
            if not command_line:
                continue
            command_line_lower = command_line.lower()
            if root_dir not in command_line_lower:
                continue
            is_launcher = bool(gateway_cmd_path) and gateway_cmd_path in command_line_lower
            is_runtime = any(marker in command_line_lower for marker in openclaw_runtime_markers) and " gateway" in command_line_lower
            if not is_launcher and not is_runtime:
                continue
            processes.append(
                {
                    "pid": int(item.get("ProcessId") or 0),
                    "parentPid": int(item.get("ParentProcessId") or 0),
                    "name": str(item.get("Name") or "").strip(),
                    "commandLine": command_line,
                    "kind": "launcher" if is_launcher else "runtime",
                }
            )
        return [item for item in processes if item.get("pid")]

    def _managed_local_gateway_process_summary(self) -> dict[str, Any]:
        processes = self._managed_local_gateway_processes()
        launchers = [item for item in processes if item.get("kind") == "launcher"]
        runtimes = [item for item in processes if item.get("kind") == "runtime"]
        launcher_pids = {int(item.get("pid") or 0) for item in launchers if int(item.get("pid") or 0) > 0}
        runtime_pids = {int(item.get("pid") or 0) for item in runtimes if int(item.get("pid") or 0) > 0}
        runtime_roots = [
            item
            for item in runtimes
            if int(item.get("parentPid") or 0) not in launcher_pids and int(item.get("parentPid") or 0) not in runtime_pids
        ]
        duplicate_launcher_count = max(0, len(launchers) - 1)
        duplicate_runtime_count = max(0, len(runtime_roots) - 1)
        warnings: list[str] = []
        if duplicate_runtime_count > 0 or duplicate_launcher_count > 0:
            warnings.append(
                f"检测到 {len(launchers)} 个 gateway 启动器、{len(runtime_roots)} 个 gateway runtime 根进程，当前 managed_local 宿主并非单实例。"
            )
        return {
            "processes": processes,
            "launcherCount": len(launchers),
            "runtimeCount": len(runtime_roots),
            "runtimeProcessCount": len(runtimes),
            "duplicateLauncherCount": duplicate_launcher_count,
            "duplicateRuntimeCount": duplicate_runtime_count,
            "hasDuplicates": duplicate_launcher_count > 0 or duplicate_runtime_count > 0,
            "warnings": warnings,
        }

    async def _force_stop_managed_local_gateway_processes(self, *, append_event) -> int:
        process_summary = self._managed_local_gateway_process_summary()
        processes = list(process_summary.get("processes") or [])
        if not processes:
            return 0

        launchers = [item for item in processes if item.get("kind") == "launcher"]
        launcher_pids = {int(item.get("pid") or 0) for item in launchers if int(item.get("pid") or 0) > 0}
        runtime_pids = {int(item.get("pid") or 0) for item in processes if item.get("kind") == "runtime" and int(item.get("pid") or 0) > 0}
        runtime_roots = [
            item
            for item in processes
            if item.get("kind") == "runtime"
            and int(item.get("parentPid") or 0) not in launcher_pids
            and int(item.get("parentPid") or 0) not in runtime_pids
        ]
        kill_targets = [*launchers, *runtime_roots]
        if not kill_targets:
            kill_targets = processes

        append_event(
            "system",
            f"准备强制清理 managed_local gateway 进程：launcher={process_summary.get('launcherCount', 0)} runtime={process_summary.get('runtimeCount', 0)}",
        )
        killed = 0
        for target in sorted(kill_targets, key=lambda item: int(item.get("pid") or 0), reverse=True):
            pid = int(target.get("pid") or 0)
            if pid <= 0:
                continue
            try:
                completed = await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode == 0:
                    killed += 1
                    append_event("system", f"已强制清理 gateway 进程树 pid={pid}")
                else:
                    detail = str(completed.stderr or completed.stdout or "").strip()
                    if detail:
                        append_event("stderr", f"清理 gateway 进程 pid={pid} 失败：{detail}")
            except Exception as exc:
                append_event("stderr", f"清理 gateway 进程 pid={pid} 时异常：{exc}")
        if killed > 0:
            await asyncio.sleep(1)
            runtime_detail = " ".join(str(item).strip() for item in (process_summary.get("warnings") or []) if str(item).strip()).lower()
            auto_start_drift_detected = "startup-folder" in runtime_detail or bool(process_summary.get("hasDuplicates"))
            self._mark_managed_local_reconciled(auto_start_drift_detected=auto_start_drift_detected)
        return killed

    def _installer_needs_openclaw_cli(self, command: str) -> bool:
        lowered = str(command or "").strip().lower()
        return (
            lowered.startswith("openclaw ")
            or " openclaw " in f" {lowered} "
            or "openclaw.cmd" in lowered
            or "openclaw.mjs" in lowered
            or ("openclaw-" in lowered and "-cli" in lowered)
        )

    async def _ensure_openclaw_cli(self, env: dict[str, str], *, append_event, installer_command: str) -> dict[str, str]:
        tooling_root = self.managed_local_tooling_root()
        local_env = dict(env)
        if tooling_root:
            local_bin = tooling_root / "node_modules" / ".bin"
            local_env = self._prepend_path(local_env, local_bin, tooling_root)
        existing = self._resolve_openclaw_cli(local_env)
        if existing:
            return local_env
        if not self._installer_needs_openclaw_cli(installer_command):
            return env
        if not tooling_root:
            raise RuntimeError("当前未配置本地 OpenClaw toolingRoot，且系统 PATH 中也没有 openclaw CLI。请先全局安装 OpenClaw，或在 PluginHostRuntime 里填写本地 CLI / tooling 根目录。")

        npm_executable = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm_executable:
            raise RuntimeError("当前系统未找到 npm，无法自动准备 openclaw CLI。")

        tooling_root.mkdir(parents=True, exist_ok=True)
        append_event("system", "检测到系统未安装 openclaw，正在为插件宿主准备本地 CLI。")
        bootstrap_argv = (
            self._wrap_windows_executable_argv(
                npm_executable,
                "install",
                "--prefix",
                str(tooling_root),
                "openclaw@latest",
            )
            if os.name == "nt"
            else [npm_executable, "install", "--prefix", str(tooling_root), "openclaw@latest"]
        )
        append_event("system", f"bootstrap argv={' '.join(bootstrap_argv)}")
        process = await self._start_install_process_argv(bootstrap_argv, cwd=str(tooling_root), env=local_env)
        append_event("system", f"openclaw CLI bootstrap started (pid={process.pid})")

        async def _pump(stream, kind: str) -> None:
            while True:
                line = await self._read_process_line(stream)
                if not line:
                    break
                append_event(kind, self._decode_process_line(line))

        await asyncio.gather(_pump(process.stdout, "bootstrap_stdout"), _pump(process.stderr, "bootstrap_stderr"))
        returncode = await self._wait_process(process)
        append_event("system", f"openclaw CLI bootstrap exited with code {returncode}")
        if returncode != 0:
            raise RuntimeError(f"本地 openclaw CLI 安装失败，退出码 {returncode}")

        existing = self._resolve_openclaw_cli(local_env)
        if not existing:
            raise RuntimeError("openclaw CLI 已安装，但在宿主路径中仍无法解析。")
        return local_env

    async def _run_logged_command_argv(
        self,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        append_event,
        stdout_kind: str,
        stderr_kind: str,
    ) -> tuple[int, list[str], list[str]]:
        process = await self._start_install_process_argv(argv, cwd=cwd, env=env)
        append_event("system", f"command started: {' '.join(argv)} (pid={process.pid})")
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        async def _pump(stream, kind: str, bucket: list[str]) -> None:
            while True:
                line = await self._read_process_line(stream)
                if not line:
                    break
                text = self._decode_process_line(line)
                bucket.append(text)
                append_event(kind, text)

        await asyncio.gather(
            _pump(process.stdout, stdout_kind, stdout_lines),
            _pump(process.stderr, stderr_kind, stderr_lines),
        )
        returncode = await self._wait_process(process)
        append_event("system", f"command exited with code {returncode}: {' '.join(argv)}")
        return returncode, stdout_lines, stderr_lines

    async def _gateway_status_payload(self, *, env: dict[str, str], append_event) -> dict[str, Any] | None:
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            append_event("stderr", "当前宿主无法解析 openclaw CLI，无法检查 gateway 状态。")
            return None
        windows_node_argv = self._resolve_windows_node_openclaw_argv(env, "gateway", "status", "--json")
        argv = (
            windows_node_argv
            or (
                self._wrap_windows_executable_argv(cli_executable, "gateway", "status", "--json")
                if os.name == "nt"
                else [cli_executable, "gateway", "status", "--json"]
            )
        )
        returncode, stdout_lines, _stderr_lines = await self._run_logged_command_argv(
            argv,
            cwd=str(self.managed_local_root()),
            env=env,
            append_event=append_event,
            stdout_kind="gateway_stdout",
            stderr_kind="gateway_stderr",
        )
        if returncode != 0:
            return None
        payload_text = "\n".join(stdout_lines).strip()
        if not payload_text:
            return None
        try:
            return json.loads(payload_text)
        except json.JSONDecodeError:
            append_event("stderr", "gateway status --json 输出无法解析为 JSON。")
            return None

    async def _run_gateway_lifecycle_command(self, *, env: dict[str, str], append_event, action: str) -> tuple[int, list[str], list[str]]:
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            append_event("stderr", f"当前宿主无法解析 openclaw CLI，无法执行 gateway {action}。")
            return 1, [], []
        windows_node_argv = self._resolve_windows_node_openclaw_argv(env, "gateway", action)
        argv = (
            windows_node_argv
            or (
                self._wrap_windows_executable_argv(cli_executable, "gateway", action)
                if os.name == "nt"
                else [cli_executable, "gateway", action]
            )
        )
        return await self._run_logged_command_argv(
            argv,
            cwd=str(self.managed_local_root()),
            env=env,
            append_event=append_event,
            stdout_kind=f"gateway_{action}_stdout",
            stderr_kind=f"gateway_{action}_stderr",
        )

    async def _reconcile_managed_local_lifecycle_authority(self, *, env: dict[str, str], append_event) -> bool:
        gateway_health = self._managed_local_gateway_health()
        runtime_detail = str(((gateway_health.get("runtime") or {}).get("detail") or "")).strip().lower()
        process_summary = dict(gateway_health.get("processSummary") or {})
        auto_start_drift_detected = "startup-folder login item installed" in runtime_detail
        drift_detected = auto_start_drift_detected or bool(process_summary.get("hasDuplicates"))
        if not drift_detected:
            return False
        append_event("system", "检测到 OpenClaw 自启动或多实例漂移，正在收回 managed_local 生命周期主权。")
        await self._run_gateway_lifecycle_command(env=env, append_event=append_event, action="uninstall")
        await self._force_stop_managed_local_gateway_processes(append_event=append_event)
        self._mark_managed_local_reconciled(auto_start_drift_detected=True)
        return True

    async def _ensure_gateway_runtime(self, *, env: dict[str, str], append_event) -> bool:
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            append_event("stderr", "当前宿主无法解析 openclaw CLI，无法准备 gateway。")
            return False
        try:
            bridge_link = self._ensure_managed_local_bridge_extension_link()
            if bridge_link.get("changed"):
                append_event("system", "已在 ~/.openclaw/extensions 物化 openclaw-v8-bridge mirror surface，避免继续依赖会被投影的宿主配置。")
        except Exception as exc:
            append_event("stderr", f"当前 bridge extensions-root 链接准备失败：{exc}")
        try:
            weixin_patch = self._ensure_managed_local_weixin_sidecar_patch()
            if weixin_patch.get("patched"):
                append_event("system", "已为 openclaw-weixin 注入 V8 handoff 与配置保真补丁，避免登录流程继续擦掉 gateway.mode。")
        except Exception as exc:
            append_event("stderr", f"当前 openclaw-weixin sidecar 补丁准备失败：{exc}")
        try:
            host_config_result = self._ensure_minimal_managed_local_openclaw_host_config()
        except Exception as exc:
            append_event("stderr", f"当前最小宿主配置准备失败：{exc}")
            return False
        if bool(host_config_result.get("recoveredConfigChanged")):
            append_event("system", "已从最近一次完整宿主配置候选恢复缺失的 channel/runtime 配置，避免 OpenClaw 4.8 投影回写后丢失飞书等渠道运行面。")
        if bool(host_config_result.get("bridgeConfigChanged")):
            append_event("system", "已最小写入 bridge 私有配置，不再依赖完整 openclaw.json。")
        if bool(host_config_result.get("gatewayModeChanged")):
            append_event("system", "已通过最小宿主配置保证 gateway.mode=local。")
        try:
            launcher_patch = self._ensure_managed_local_gateway_launcher_handoff()
            if launcher_patch.get("patched"):
                append_event("system", "已向 gateway.cmd 注入 V8 inbound handoff 环境变量。")
        except Exception as exc:
            append_event("stderr", f"当前 gateway 启动脚本 handoff 注入失败：{exc}")
            return False

        await self._reconcile_managed_local_lifecycle_authority(env=env, append_event=append_event)

        status_payload = await self._gateway_status_payload(env=env, append_event=append_event)
        process_summary = self._managed_local_gateway_process_summary()
        if bool(((status_payload or {}).get("rpc") or {}).get("ok")) and not bool(process_summary.get("hasDuplicates")):
            return True
        if process_summary.get("hasDuplicates"):
            warning = next(iter(process_summary.get("warnings") or []), "").strip()
            append_event("stderr", warning or "检测到多个 managed_local gateway 进程树，将清理后重启单实例。")
        elif process_summary.get("runtimeCount", 0) > 0 or process_summary.get("launcherCount", 0) > 0:
            append_event("system", "gateway 未就绪，但检测到残留进程，将先清理旧实例后再启动。")
        await self._force_stop_managed_local_gateway_processes(append_event=append_event)

        if os.name == "nt":
            gateway_argv = self._resolve_windows_node_openclaw_argv(env, "gateway", "run", "--allow-unconfigured")
            if gateway_argv:
                append_event("system", "gateway 未运行，Engine 将直接托管 openclaw gateway run --allow-unconfigured，不再依赖宿主保留 gateway.mode。")
                await asyncio.to_thread(
                    self._spawn_background_process_argv,
                    gateway_argv,
                    cwd=str(self.managed_local_root()),
                    env=env,
                )
            else:
                argv = self._wrap_windows_executable_argv(cli_executable, "gateway", "run", "--allow-unconfigured")
                append_event("system", "未解析到 node/openclaw.mjs，回退到 openclaw gateway run --allow-unconfigured 启动本地 gateway。")
                await asyncio.to_thread(self._spawn_background_process_argv, argv, cwd=str(self.managed_local_root()), env=env)
        else:
            argv = [cli_executable, "gateway", "run", "--allow-unconfigured"]
            append_event("system", "尝试通过 openclaw gateway run --allow-unconfigured 启动本地 gateway。")
            await asyncio.to_thread(self._spawn_background_process_argv, argv, cwd=str(self.managed_local_root()), env=env)

        status_payload = None
        process_summary: dict[str, Any] = {}
        for _attempt in range(15):
            await asyncio.sleep(2)
            status_payload = await self._gateway_status_payload(env=env, append_event=append_event)
            process_summary = self._managed_local_gateway_process_summary()
            if bool(((status_payload or {}).get("rpc") or {}).get("ok")) and not bool(process_summary.get("hasDuplicates")):
                return True
        if process_summary.get("hasDuplicates"):
            append_event(
                "stderr",
                next(iter(process_summary.get("warnings") or []), "").strip()
                or "gateway 虽已返回健康状态，但仍检测到重复进程树，当前拒绝把它判定为可用。",
            )
        append_event("stderr", "gateway 仍未进入健康运行状态，请检查 gateway 日志或稍后重试。")
        return False

    def _synthetic_gateway_health(self, *, status: str, reason: str, healthy: bool = False, rpc_ok: bool = False) -> dict[str, Any]:
        return {
            "runtime": {
                "status": str(status or "unknown"),
                "detail": str(reason or "").strip() or None,
            },
            "rpc": {
                "ok": bool(rpc_ok),
                "error": None if rpc_ok else str(reason or "").strip() or None,
            },
            "health": {
                "healthy": bool(healthy),
            },
            "warnings": [str(reason).strip()] if str(reason or "").strip() else [],
            "error": None if healthy else str(reason or "").strip() or None,
        }

    def _managed_local_channel_accounts(self, *, refresh: bool = False) -> dict[str, list[str]]:
        if (
            not refresh
            and self._openclaw_channel_accounts_cache is not None
            and time.monotonic() - self._openclaw_channel_accounts_cache_at < _OPENCLAW_CHANNEL_ACCOUNTS_TTL_SECONDS
        ):
            return {key: list(value) for key, value in self._openclaw_channel_accounts_cache.items()}
        inventory_payload = self._managed_local_plugins_inventory(refresh=refresh)
        channels = self._managed_local_channel_accounts_from_state_manifest(inventory=inventory_payload)
        self._openclaw_channel_accounts_cache = {key: list(value) for key, value in channels.items()}
        self._openclaw_channel_accounts_cache_at = time.monotonic()
        return channels

    def _openclaw_log_dir(self) -> Path:
        override = str(os.environ.get("OPENCLAW_LOG_DIR") or "").strip()
        if override:
            return Path(override).expanduser()
        return Path(tempfile.gettempdir()) / "openclaw"

    def _latest_openclaw_log_file(self) -> Path | None:
        log_dir = self._openclaw_log_dir()
        if not log_dir.exists():
            return None
        candidates = [path for path in log_dir.glob("openclaw-*.log") if path.is_file()]
        if not candidates:
            return None
        try:
            return max(candidates, key=lambda item: item.stat().st_mtime)
        except Exception:
            return candidates[-1]

    def _openclaw_log_tail_records(self, *, max_lines: int = 800, max_bytes: int = 1024 * 1024) -> tuple[Path | None, list[dict[str, Any]]]:
        log_path = self._latest_openclaw_log_file()
        if not log_path:
            return None, []
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                chunk = handle.read().decode("utf-8", errors="replace")
        except Exception:
            return log_path, []
        lines = chunk.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        records: list[dict[str, Any]] = []
        for raw_line in lines:
            normalized = str(raw_line or "").strip()
            if not normalized:
                continue
            try:
                payload = json.loads(normalized)
            except Exception:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return log_path, records

    def _openclaw_log_record_time(self, record: dict[str, Any]) -> str | None:
        if not isinstance(record, dict):
            return None
        for candidate in (
            record.get("time"),
            ((record.get("_meta") or {}) if isinstance(record.get("_meta"), dict) else {}).get("date"),
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        return None

    def _openclaw_log_record_message(self, record: dict[str, Any]) -> str:
        if not isinstance(record, dict):
            return ""
        parts: list[str] = []
        meta = record.get("_meta")
        if isinstance(meta, dict):
            name = str(meta.get("name") or "").strip()
            if name:
                parts.append(name)
        indexed_keys = sorted((key for key in record.keys() if str(key).isdigit()), key=lambda item: int(str(item)))
        for key in indexed_keys:
            value = record.get(str(key))
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                parts.append(json.dumps(value, ensure_ascii=False))
            else:
                text = str(value).strip()
                if text:
                    parts.append(text)
        return " ".join(part for part in parts if part).strip()

    def _recent_openclaw_handoff_audit(self, *, channel_type: str | None) -> dict[str, Any]:
        normalized_channel = str(channel_type or "").strip()
        log_path, records = self._openclaw_log_tail_records()
        if not normalized_channel:
            return {
                "logPath": str(log_path) if log_path else None,
                "observedInbound": False,
                "inboundOwnership": "unknown",
                "handoffReady": False,
                "handoffDrift": False,
                "reason": "当前没有可用于 handoff 审计的 channel 插件。",
            }
        if not log_path or not records:
            return {
                "logPath": str(log_path) if log_path else None,
                "observedInbound": False,
                "inboundOwnership": "unknown",
                "handoffReady": False,
                "handoffDrift": False,
                "reason": "当前尚未找到可用于 handoff 审计的 OpenClaw 日志。",
            }

        channel_marker = f"gateway/channels/{normalized_channel}".lower()
        latest_inbound: dict[str, str] | None = None
        latest_handoff: dict[str, str] | None = None
        latest_handoff_failure: dict[str, str] | None = None
        latest_delegated_error: dict[str, str] | None = None

        for record in records:
            message = self._openclaw_log_record_message(record)
            if not message:
                continue
            message_lower = message.lower()
            if channel_marker not in message_lower:
                continue
            observed_at = self._openclaw_log_record_time(record)
            if "embedded agent failed before reply" in message_lower:
                latest_delegated_error = {"at": observed_at or "", "message": message}
            if "handofftov8:" in message_lower:
                if "transferred to v8" in message_lower:
                    latest_handoff = {"at": observed_at or "", "message": message}
                elif "failed" in message_lower:
                    latest_handoff_failure = {"at": observed_at or "", "message": message}
            inbound_detected = "inbound message:" in message_lower or " inbound:" in message_lower
            if inbound_detected and "handofftov8" not in message_lower:
                latest_inbound = {"at": observed_at or "", "message": message}

        observed_inbound = latest_inbound is not None
        inbound_ownership = "unknown"
        handoff_ready = False
        handoff_drift = False
        reason = "最近日志中还没有观察到真实入站。"

        inbound_at = str((latest_inbound or {}).get("at") or "").strip()
        handoff_at = str((latest_handoff or {}).get("at") or "").strip()
        delegated_at = str((latest_delegated_error or {}).get("at") or "").strip()
        handoff_failure_at = str((latest_handoff_failure or {}).get("at") or "").strip()

        if observed_inbound:
            if delegated_at and delegated_at >= inbound_at and (not handoff_at or delegated_at >= handoff_at):
                inbound_ownership = "delegated"
                handoff_ready = False
                handoff_drift = True
                reason = str((latest_delegated_error or {}).get("message") or "").strip() or "最近一次真实入站仍回落到了 OpenClaw sidecar 自带 agent。"
            elif handoff_failure_at and handoff_failure_at >= inbound_at and (not handoff_at or handoff_failure_at >= handoff_at):
                inbound_ownership = "delegated"
                handoff_ready = False
                handoff_drift = True
                reason = str((latest_handoff_failure or {}).get("message") or "").strip() or "最近一次真实入站的 V8 handoff 失败。"
            elif handoff_at and handoff_at >= inbound_at:
                inbound_ownership = "v8_owned"
                handoff_ready = True
                handoff_drift = False
                reason = None
            else:
                inbound_ownership = "delegated"
                handoff_ready = False
                handoff_drift = True
                reason = "最近一次真实入站之后，日志里尚未观察到 V8 handoff 成功信号。"

        return {
            "logPath": str(log_path),
            "observedInbound": observed_inbound,
            "lastObservedInboundAt": inbound_at or None,
            "lastObservedInbound": dict(latest_inbound or {}) or None,
            "lastHandoffAt": handoff_at or None,
            "lastHandoff": dict(latest_handoff or {}) or None,
            "lastHandoffFailureAt": handoff_failure_at or None,
            "lastHandoffFailure": dict(latest_handoff_failure or {}) or None,
            "lastDelegatedErrorAt": delegated_at or None,
            "lastDelegatedError": str((latest_delegated_error or {}).get("message") or "").strip() or None,
            "inboundOwnership": inbound_ownership,
            "handoffReady": handoff_ready,
            "handoffDrift": handoff_drift,
            "reason": reason,
        }

    def _derive_inbound_ownership(
        self,
        *,
        runtime_enabled: bool,
        family_allowed: bool,
        handoff_ready: bool,
        default_ownership: str,
        recent_inbound_proof: dict[str, Any] | None,
        handoff_audit: dict[str, Any] | None,
    ) -> tuple[str, bool, str | None, str | None]:
        if not runtime_enabled or not family_allowed:
            return "disabled", False, None, None

        proof = dict(recent_inbound_proof or {})
        audit = dict(handoff_audit or {})
        stage = str(proof.get("stage") or "").strip()
        proof_reason = str(proof.get("reason") or "").strip() or None
        proof_at = str(proof.get("inboundObservedAt") or proof.get("startedAt") or "").strip() or None

        if bool(proof.get("ownershipProven")):
            if stage in {"reply_delivered", "run_in_progress", "outbound_failed", "execution_failed", "reply_missing", "manual_outbound_only"}:
                return "v8_owned", bool(handoff_ready), proof_reason, proof_at

        if bool(audit.get("observedInbound")):
            audit_ownership = str(audit.get("inboundOwnership") or default_ownership or "delegated").strip() or "delegated"
            audit_ready = bool(audit.get("handoffReady"))
            audit_reason = str(audit.get("reason") or "").strip() or None
            audit_at = str(audit.get("lastHandoffAt") or audit.get("lastObservedInboundAt") or "").strip() or None
            return audit_ownership, audit_ready, audit_reason, audit_at

        if handoff_ready:
            return "unverified", True, "当前 bridge 握手已就绪，但最近还没有观察到新的真实入站证明。", None

        return default_ownership or "delegated", False, None, None

    def _normalize_handoff_audit(
        self,
        *,
        handoff_audit: dict[str, Any] | None,
        recent_inbound_proof: dict[str, Any] | None,
        bridge_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        audit = dict(handoff_audit or {})
        proof = dict(recent_inbound_proof or {})
        if not audit and not proof:
            return {}
        if bool(proof.get("ownershipProven")):
            proof_at = str(proof.get("inboundObservedAt") or proof.get("startedAt") or "").strip() or None
            proof_reason = str(proof.get("reason") or "").strip() or "最近一次真实入站已经进入 V8，并形成了 plugin_host 账本证明。"
            audit["inboundOwnership"] = "v8_owned"
            audit["handoffReady"] = bool((bridge_state or {}).get("bridgeReady", True))
            audit["handoffDrift"] = False
            audit["reason"] = proof_reason
            audit["observedInbound"] = True
            if proof_at:
                audit["lastObservedInboundAt"] = proof_at
                audit["lastHandoffAt"] = proof_at
        return audit

    def _plugin_channel_accounts(self, plugin: dict[str, Any], channels_state: dict[str, list[str]]) -> tuple[list[str], list[str]]:
        channel_ids: list[str] = []
        for source in (
            list((plugin.get("capabilitySurface") or {}).get("channels") or []),
            list((plugin.get("capabilities") or {}).get("channels") or []),
            list((plugin.get("manifestSummary") or {}).get("channels") or []),
        ):
            for item in source:
                normalized = str(item).strip()
                if normalized:
                    channel_ids.append(normalized)
        channel_ids = list(dict.fromkeys(channel_ids))
        accounts: list[str] = []
        for channel_id in channel_ids:
            accounts.extend(list(channels_state.get(channel_id) or []))
        return channel_ids, list(dict.fromkeys(accounts))

    def _managed_local_gateway_health(self) -> dict[str, Any]:
        env = self._managed_local_env()
        cli_source = self._openclaw_cli_source(env)
        launcher_source, launcher_missing = self._gateway_launcher_source()
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            return {
                "runtime": {
                    "status": "missing_cli",
                    "detail": "当前宿主未解析到 openclaw CLI。",
                },
                "rpc": {
                    "ok": False,
                    "error": "当前宿主未解析到 openclaw CLI。",
                },
                "health": {
                    "healthy": False,
                },
                "warnings": ["当前宿主未解析到 openclaw CLI。"],
                "error": "当前宿主未解析到 openclaw CLI。",
                "cliSource": cli_source,
                "launcherSource": launcher_source,
                "launcherMissing": launcher_missing,
            }

        process_summary = self._managed_local_gateway_process_summary()
        duplicate_warning = next(iter(process_summary.get("warnings") or []), "").strip()
        try:
            payload = self._openclaw_gateway_request_json(suffix="/health", timeout=10)
        except Exception:
            payload = {}
        if bool(payload.get("ok")):
            healthy = str(payload.get("status") or "").strip().lower() in {"live", "ok", "healthy"}
            runtime_status = "running" if healthy else "unknown"
            runtime_detail = None
            warnings: list[str] = []
            if duplicate_warning:
                warnings.append(duplicate_warning)
                runtime_status = "duplicate_processes"
                runtime_detail = duplicate_warning
                healthy = False
            return {
                "runtime": {
                    "status": runtime_status,
                    "detail": runtime_detail,
                },
                "rpc": {
                    "ok": healthy,
                    "error": None if healthy else runtime_detail,
                    "url": str(payload.get("url") or "").strip() or None,
                },
                "health": {
                    "healthy": healthy,
                },
                "warnings": warnings,
                "error": None if healthy else runtime_detail,
                "processSummary": process_summary,
                "cliSource": cli_source,
                "launcherSource": launcher_source,
                "launcherMissing": launcher_missing,
            }

        windows_node_argv = self._resolve_windows_node_openclaw_argv(env, "gateway", "status", "--json")
        argv = (
            windows_node_argv
            or (
                self._wrap_windows_executable_argv(cli_executable, "gateway", "status", "--json")
                if os.name == "nt"
                else [cli_executable, "gateway", "status", "--json"]
            )
        )
        try:
            completed = subprocess.run(
                argv,
                cwd=str(self.managed_local_root()),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            return self._synthetic_gateway_health(status="error", reason=str(exc).strip() or exc.__class__.__name__)

        payload: dict[str, Any] = {}
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = {}

        service_payload = dict(payload.get("service") or {})
        runtime = dict(service_payload.get("runtime") or payload.get("runtime") or {})
        rpc = dict(payload.get("rpc") or {})
        health = dict(payload.get("health") or {})
        missing_unit = bool(runtime.get("missingUnit"))
        runtime_status = str(runtime.get("status") or ("stopped" if completed.returncode != 0 else "unknown")).strip().lower() or "unknown"
        rpc_ok = bool(rpc.get("ok"))
        healthy = bool(health.get("healthy")) if health else (runtime_status == "running" and rpc_ok)
        warnings: list[str] = []
        runtime_detail = str(runtime.get("detail") or "").strip()
        rpc_error = str(rpc.get("error") or "").strip()
        if runtime_detail:
            warnings.append(runtime_detail)
        if rpc_error:
            warnings.append(rpc_error)
        if stderr:
            warnings.append(stderr)
        combined_detail = " ".join([runtime_detail, rpc_error, stderr]).strip().lower()
        if "config invalid" in combined_detail or "validation" in combined_detail or "invalid" in combined_detail:
            runtime_status = "config_invalid"
            healthy = False
        if duplicate_warning:
            warnings.append(duplicate_warning)
            runtime_status = "duplicate_processes"
            runtime_detail = duplicate_warning
            healthy = False
        elif rpc_ok:
            if runtime_status in {"unknown", "stopped"}:
                runtime_status = "running"
            if missing_unit:
                runtime_detail = ""
                warnings = [item for item in warnings if item != str(runtime.get("detail") or "").strip()]
            if not runtime_detail and stderr:
                runtime_detail = "Gateway RPC probe 已成功，但服务元数据返回了额外平台噪声。"
            healthy = True
        return {
            "runtime": {
                "status": runtime_status,
                "detail": runtime_detail or None,
            },
            "rpc": {
                "ok": rpc_ok,
                "error": rpc_error or None,
                "url": str(rpc.get("url") or "").strip() or None,
            },
            "health": {
                "healthy": healthy,
            },
            "warnings": list(dict.fromkeys(warnings)),
            "error": None if healthy else (warnings[0] if warnings else None),
            "processSummary": process_summary,
            "cliSource": cli_source,
            "launcherSource": launcher_source,
            "launcherMissing": launcher_missing,
        }

    def _build_host_surface(
        self,
        *,
        runtime_config: dict[str, Any],
        runtime_enabled: bool,
        allowed_families: set[str],
        external_status: dict[str, Any],
        handoff_audit: dict[str, Any] | None = None,
        recent_inbound_proof: dict[str, Any] | None = None,
        managed_channel_plugin: dict[str, Any] | None = None,
        bridge_state: dict[str, Any] | None = None,
        gateway_health: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime_state = self._get_runtime_state()
        if self.is_external_host():
            external_host = dict(runtime_config.get("externalHost") or {})
            current_external_status = dict(external_status or {})
            remote_host_surface = (
                dict(current_external_status.get("hostSurface") or {})
                if isinstance(current_external_status.get("hostSurface"), dict)
                else {}
            )
            inbound_ownership = str(remote_host_surface.get("inboundOwnership") or "delegated").strip() or "delegated"
            handoff_ready = bool(remote_host_surface.get("handoffReady"))
            gateway_health = (
                dict(remote_host_surface.get("gatewayHealth") or {})
                if isinstance(remote_host_surface.get("gatewayHealth"), dict)
                else {}
            )
            external_mode_status = str(current_external_status.get("status") or "").strip() or "unknown"
            if not gateway_health:
                if not external_host.get("gatewayBaseUrl"):
                    gateway_health = self._synthetic_gateway_health(
                        status="control_plane_only",
                        reason="当前 external host 未配置 gatewayBaseUrl，出站数据面不可用。",
                    )
                    inbound_ownership = "delegated"
                    handoff_ready = False
                    external_mode_status = "control_plane_only"
                elif current_external_status.get("reachable") is False:
                    gateway_health = self._synthetic_gateway_health(
                        status="unreachable",
                        reason=str(current_external_status.get("error") or "无法连接远端 PluginHost。"),
                    )
                    external_mode_status = "unreachable"
                else:
                    gateway_health = self._synthetic_gateway_health(
                        status="unknown",
                        reason="远端 PluginHost 尚未返回 gateway 状态。",
                    )
                    external_mode_status = "unknown"
            outbound_ready = bool(runtime_enabled and "channel" in allowed_families and external_host.get("gatewayBaseUrl")) and bool(
                remote_host_surface.get("outboundReady")
            )
            if not runtime_enabled:
                external_mode_status = "cold_stopped"
            elif "channel" not in allowed_families:
                external_mode_status = "family_disabled"
                inbound_ownership = "disabled"
                handoff_ready = False
            elif external_mode_status not in {"unreachable", "control_plane_only"}:
                if inbound_ownership != "v8_owned":
                    external_mode_status = "inbound_delegated"
                elif not outbound_ready:
                    external_mode_status = "outbound_unready"
                else:
                    external_mode_status = "connected"
            current_external_status["status"] = external_mode_status
            return self._augment_host_surface_diagnostics({
                "mode": self.host_mode(),
                "managedLocal": {
                    "rootDir": str(self.managed_local_root()),
                    "toolingRoot": str(self.managed_local_tooling_root()) if self.managed_local_tooling_root() else "",
                    "launcherPath": str(self.managed_local_launcher_path()) if self.managed_local_launcher_path() else "",
                    "autoStart": self.managed_local_auto_start(),
                },
                "externalHost": external_host,
                "coldStopped": not runtime_enabled,
                "gatewayHealth": gateway_health,
                "outboundReady": outbound_ready,
                "inboundOwnership": inbound_ownership,
                "handoffReady": handoff_ready,
                "handoffDrift": bool(runtime_state.get("handoffDrift", False)),
                "lastInboundHandoffAt": runtime_state.get("lastInboundHandoffAt"),
                "lifecycleAuthority": "external_managed",
                "autoStartDriftDetected": False,
                "reconciledAt": runtime_state.get("reconciledAt"),
                "cliSource": "missing",
                "toolingMode": "external_host",
                "toolingEntry": external_host.get("baseUrl") or None,
                "launcherSource": "direct_cli_run",
                "launcherMissing": True,
                "bridgeReady": bool(remote_host_surface.get("bridgeReady")),
                "bridgePluginId": str(remote_host_surface.get("bridgePluginId") or "").strip() or None,
                "managedChannels": [
                    str(item).strip()
                    for item in list(remote_host_surface.get("managedChannels") or [])
                    if str(item).strip()
                ],
                "installProvenance": str(remote_host_surface.get("installProvenance") or "").strip() or "unknown",
                "installTrusted": bool(remote_host_surface.get("installTrusted")),
                "managedChannelsSource": str(remote_host_surface.get("managedChannelsSource") or "").strip() or "default",
                "configSource": str(remote_host_surface.get("configSource") or "").strip() or "defaults",
                "refreshMode": str(remote_host_surface.get("refreshMode") or "").strip() or "hot",
                "resolvedStateDir": str(remote_host_surface.get("resolvedStateDir") or "").strip() or None,
                "gatewayBaseUrl": str(remote_host_surface.get("gatewayBaseUrl") or "").strip() or None,
                "v8InboundUrl": str(remote_host_surface.get("v8InboundUrl") or "").strip() or None,
                "bridgeStatusSource": str(remote_host_surface.get("bridgeStatusSource") or "").strip() or None,
                "bridgeStatusObservedAt": remote_host_surface.get("bridgeStatusObservedAt"),
                "bridgeStatusMs": int(remote_host_surface.get("bridgeStatusMs") or 0),
                "bridgeStatusError": str(remote_host_surface.get("bridgeStatusError") or "").strip() or None,
                "bridgeStatusStale": bool(remote_host_surface.get("bridgeStatusStale")),
                "pluginsAllowConfigured": remote_host_surface.get("pluginsAllowConfigured"),
                "pluginsAllow": [
                    str(item).strip()
                    for item in list(remote_host_surface.get("pluginsAllow") or [])
                    if str(item).strip()
                ],
                "handoffConfigured": bool(remote_host_surface.get("handoffConfigured")),
                "claimEnabled": bool(remote_host_surface.get("claimEnabled")),
                "lastClaimAt": remote_host_surface.get("lastClaimAt"),
                "lastClaimAttemptAt": remote_host_surface.get("lastClaimAttemptAt"),
                "lastClaimOutcome": remote_host_surface.get("lastClaimOutcome"),
                "lastClaimDeclineReason": remote_host_surface.get("lastClaimDeclineReason"),
                "lastClaimChannel": remote_host_surface.get("lastClaimChannel"),
                "lastClaimConversation": remote_host_surface.get("lastClaimConversation"),
                "lastClaimMessageId": remote_host_surface.get("lastClaimMessageId"),
                "lastClaimAccountId": remote_host_surface.get("lastClaimAccountId"),
                "lastClaimPayloadShape": remote_host_surface.get("lastClaimPayloadShape"),
                "expectedBridgeClaimMissed": bool(remote_host_surface.get("expectedBridgeClaimMissed")),
                "externalStatus": current_external_status,
            })

        bridge_state_payload = dict(bridge_state or self._managed_local_bridge_state())
        handoff_status = self._managed_local_handoff_status(
            plugin=managed_channel_plugin,
            bridge_state=bridge_state_payload,
        )
        managed_env = self._managed_local_env()
        resolved_cli = self._resolve_openclaw_cli(managed_env)
        if not runtime_enabled:
            gateway_health_payload = self._synthetic_gateway_health(
                status="cold_stopped",
                reason="PluginHostRuntime 已关闭，当前不保活 gateway。",
            )
            inbound_ownership = "disabled"
            handoff_ready = False
        elif "channel" not in allowed_families:
            gateway_health_payload = self._synthetic_gateway_health(
                status="family_disabled",
                reason="当前宿主未允许 channel 家族接管，gateway 数据面不参与运行。",
            )
            inbound_ownership = "disabled"
            handoff_ready = False
        else:
            gateway_health_payload = dict(gateway_health or self._managed_local_gateway_health())
            inbound_ownership = str(handoff_status.get("inboundOwnership") or "delegated").strip() or "delegated"
            handoff_ready = bool(handoff_status.get("handoffReady"))
        process_summary = dict(gateway_health_payload.get("processSummary") or {})
        runtime_detail = str(((gateway_health_payload.get("runtime") or {}).get("detail") or "")).strip().lower()
        auto_start_drift_detected = bool(runtime_state.get("autoStartDriftDetected", False)) or "startup-folder login item installed" in runtime_detail
        lifecycle_authority = "drifted" if runtime_enabled and (bool(process_summary.get("hasDuplicates")) or auto_start_drift_detected) else "manual_local"
        handoff_drift = bool(runtime_state.get("handoffDrift", False))
        last_inbound_handoff_at = runtime_state.get("lastInboundHandoffAt")
        effective_ownership, effective_handoff_ready, effective_reason, effective_handoff_at = self._derive_inbound_ownership(
            runtime_enabled=runtime_enabled,
            family_allowed="channel" in allowed_families,
            handoff_ready=handoff_ready,
            default_ownership=inbound_ownership,
            recent_inbound_proof=recent_inbound_proof,
            handoff_audit=handoff_audit,
        )
        inbound_ownership = effective_ownership
        handoff_ready = effective_handoff_ready
        if bool((recent_inbound_proof or {}).get("ownershipProven")):
            handoff_drift = False
        elif handoff_audit:
            handoff_drift = bool(handoff_audit.get("handoffDrift"))
        if effective_handoff_at:
            last_inbound_handoff_at = effective_handoff_at
        if effective_reason:
            handoff_status = {**handoff_status, "reason": effective_reason}
        outbound_ready = bool(runtime_enabled and "channel" in allowed_families and bool((gateway_health_payload.get("health") or {}).get("healthy")))
        cli_source = str(gateway_health_payload.get("cliSource") or self._openclaw_cli_source(managed_env))
        tooling_mode = {
            "system_path": "system_path",
            "global_npm": "global_install",
            "state_root_local": "prefix_install",
            "configured_local": "configured_local",
            "bundled_local": "legacy_bundled",
        }.get(cli_source, "missing")
        return self._augment_host_surface_diagnostics({
            "mode": self.host_mode(),
            "managedLocal": {
                "rootDir": str(self.managed_local_root()),
                "toolingRoot": str(self.managed_local_tooling_root()) if self.managed_local_tooling_root() else "",
                "launcherPath": str(self.managed_local_launcher_path()) if self.managed_local_launcher_path() else "",
                "autoStart": self.managed_local_auto_start(),
            },
            "externalHost": dict(runtime_config.get("externalHost") or {}),
            "coldStopped": not runtime_enabled,
            "gatewayHealth": gateway_health_payload,
            "outboundReady": outbound_ready,
            "inboundOwnership": inbound_ownership,
            "handoffReady": handoff_ready,
            "handoffDrift": handoff_drift,
            "lastInboundHandoffAt": last_inbound_handoff_at,
            "lifecycleAuthority": lifecycle_authority,
            "autoStartDriftDetected": auto_start_drift_detected,
            "reconciledAt": runtime_state.get("reconciledAt"),
            "cliSource": cli_source,
            "toolingMode": tooling_mode,
            "toolingEntry": str(resolved_cli or self.managed_local_tooling_root() or "").strip() or None,
            "launcherSource": str(gateway_health_payload.get("launcherSource") or self._gateway_launcher_source()[0]),
            "launcherMissing": bool(gateway_health_payload.get("launcherMissing")),
            "bridgeReady": bool(bridge_state_payload.get("bridgeReady")),
            "bridgePluginId": str(bridge_state_payload.get("pluginId") or "").strip() or None,
            "managedChannels": [
                str(item).strip()
                for item in list(bridge_state_payload.get("managedChannels") or [])
                if str(item).strip()
            ],
            "installProvenance": str(bridge_state_payload.get("installProvenance") or "").strip() or "unknown",
            "installTrusted": bool(bridge_state_payload.get("installTrusted")),
            "managedChannelsSource": str(bridge_state_payload.get("managedChannelsSource") or "").strip() or "default",
            "configSource": str(bridge_state_payload.get("configSource") or "").strip() or "defaults",
            "refreshMode": str(bridge_state_payload.get("refreshMode") or "").strip() or "hot",
            "resolvedStateDir": str(bridge_state_payload.get("resolvedStateDir") or "").strip() or None,
            "gatewayBaseUrl": str(bridge_state_payload.get("gatewayBaseUrl") or "").strip() or None,
            "v8InboundUrl": str(bridge_state_payload.get("v8InboundUrl") or "").strip() or None,
            "bridgeStatusSource": str(bridge_state_payload.get("bridgeStatusSource") or "").strip() or None,
            "bridgeStatusObservedAt": bridge_state_payload.get("bridgeStatusObservedAt"),
            "bridgeStatusMs": int(bridge_state_payload.get("bridgeStatusMs") or 0),
            "bridgeStatusError": str(bridge_state_payload.get("bridgeStatusError") or "").strip() or None,
            "bridgeStatusStale": bool(bridge_state_payload.get("bridgeStatusStale")),
            "handoffConfigured": bool(bridge_state_payload.get("handoffConfigured")),
            "claimEnabled": bool(bridge_state_payload.get("claimEnabled")),
            "lastClaimAt": bridge_state_payload.get("lastClaimAt"),
            "lastClaimAttemptAt": bridge_state_payload.get("lastClaimAttemptAt"),
            "lastClaimOutcome": bridge_state_payload.get("lastClaimOutcome"),
            "lastClaimDeclineReason": bridge_state_payload.get("lastClaimDeclineReason"),
            "lastClaimChannel": bridge_state_payload.get("lastClaimChannel"),
            "lastClaimConversation": bridge_state_payload.get("lastClaimConversation"),
            "lastClaimMessageId": bridge_state_payload.get("lastClaimMessageId"),
            "lastClaimAccountId": bridge_state_payload.get("lastClaimAccountId"),
            "lastClaimPayloadShape": bridge_state_payload.get("lastClaimPayloadShape"),
            "expectedBridgeClaimMissed": bool(handoff_status.get("expectedBridgeClaimMissed")),
            "externalStatus": external_status,
            "handoff": handoff_status,
            "handoffAudit": handoff_audit or None,
        },
            registry=default_plugin_registry(),
            bridge_state=bridge_state_payload,
        )

    def _resolve_installed_plugin(self, *, registry: dict[str, Any], plugin_id: str | None) -> dict[str, Any] | None:
        if not plugin_id:
            return None
        plugin = dict(((registry.get("plugins") or {}).get(plugin_id) or {}))
        if not plugin:
            return None
        if self.is_external_host() or self._plugin_belongs_to_current_managed_root(plugin):
            return plugin
        return None

    def _channel_login_target(self, plugin: dict[str, Any] | None) -> str | None:
        if not plugin:
            return None
        channels = list(((plugin.get("capabilitySurface") or {}).get("channels") or []))
        if channels:
            normalized = str(channels[0]).strip()
            return normalized or None
        channels = list(((plugin.get("manifestSummary") or {}).get("channels") or []))
        if channels:
            normalized = str(channels[0]).strip()
            return normalized or None
        return None

    async def _bridge_installed_plugin(self, *, plugin: dict[str, Any] | None, append_event) -> bool:
        if not plugin:
            return False
        install_path = Path(str(plugin.get("installPath") or ""))
        host_package_root = self._resolve_openclaw_package_root()
        if not host_package_root:
            append_event("stderr", "宿主 openclaw 包根目录不存在，无法为插件建立稳定桥接。")
            return False
        bridge_result = ensure_openclaw_host_bridge(plugin_dir=install_path, host_package_root=host_package_root)
        append_event(
            "system",
            f"已为插件建立宿主 openclaw 桥接 ({bridge_result['method']}): {bridge_result['bridgePath']}",
        )
        return True

    async def _run_channel_onboarding(self, *, env: dict[str, str], channel_id: str, append_event) -> dict[str, Any]:
        cli_executable = self._resolve_openclaw_cli(env)
        if not cli_executable:
            append_event("stderr", "当前宿主无法解析 openclaw CLI，无法执行首次接入。")
            return {"status": "failed"}

        onboarding_argv = (
            self._wrap_windows_executable_argv(cli_executable, "channels", "login", "--channel", channel_id)
            if os.name == "nt"
            else [cli_executable, "channels", "login", "--channel", channel_id]
        )
        append_event("system", f"开始执行首次接入：{' '.join(onboarding_argv)}")
        process = await self._start_install_process_argv(onboarding_argv, cwd=str(self.managed_local_root()), env=env)
        append_event("system", f"首次接入进程已启动 (pid={process.pid})")

        captured_lines: list[str] = []
        requires_action = asyncio.Event()

        async def _pump(stream, kind: str) -> None:
            while True:
                line = await self._read_process_line(stream)
                if not line:
                    break
                text = self._decode_process_line(line)
                captured_lines.append(text)
                append_event(kind, text)
                hints = detect_onboarding_hints(captured_lines[-120:])
                strong_instructions = [
                    str(item).strip()
                    for item in list(hints.get("instructions") or [])
                    if str(item).strip().lower().startswith("openclaw ")
                    or str(item).strip().lower().startswith("start with:")
                ]
                if (hints.get("urls") or hints.get("qrBlocks") or strong_instructions):
                    requires_action.set()

        stdout_task = asyncio.create_task(_pump(process.stdout, "onboarding_stdout"))
        stderr_task = asyncio.create_task(_pump(process.stderr, "onboarding_stderr"))
        wait_task = asyncio.create_task(self._wait_process(process))
        action_task = asyncio.create_task(requires_action.wait())

        try:
            done, pending = await asyncio.wait(
                {wait_task, action_task},
                timeout=45,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if action_task in done:
                append_event("system", "检测到首次接入需要人工继续操作，继续短暂收集接入链接后转入页面引导。")
                try:
                    await asyncio.wait_for(asyncio.shield(wait_task), timeout=2.5)
                except asyncio.TimeoutError:
                    pass
                if not wait_task.done():
                    await self._terminate_process(process)
                await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
                hints = detect_onboarding_hints(captured_lines)
                if not hints.get("urls"):
                    append_event("system", "当前接入进程未返回可直接跳转链接，页面将仅保留手动重试提示。")
                return {"status": "needs_user_action"}
            if wait_task in done:
                returncode = wait_task.result()
                append_event("system", f"首次接入进程退出码 {returncode}")
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                hints = detect_onboarding_hints(captured_lines)
                if hints.get("requiresUserAction"):
                    return {"status": "needs_user_action"}
                if returncode != 0:
                    return {"status": "failed", "returnCode": returncode}
                return {"status": "completed", "returnCode": returncode}

            append_event("stderr", "首次接入在超时时间内未给出明确结果，已停止当前进程，请稍后重试。")
            await self._terminate_process(process)
            for task in (stdout_task, stderr_task, wait_task, action_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, action_task, return_exceptions=True)
            hints = detect_onboarding_hints(captured_lines)
            if hints.get("requiresUserAction"):
                return {"status": "needs_user_action"}
            return {"status": "failed", "returnCode": None}
        finally:
            for task in (stdout_task, stderr_task, wait_task, action_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, action_task, return_exceptions=True)

    def _resolve_install_job_plugin_id(
        self,
        *,
        before_plugin_ids: set[str],
        install_spec: str,
        plugin_type_hint: str | None,
        registry: dict[str, Any],
    ) -> str | None:
        plugins = [dict(item) for item in list((registry.get("plugins") or {}).values()) if isinstance(item, dict)]
        normalized_spec = str(install_spec or "").strip()
        if normalized_spec:
            for plugin in plugins:
                if str(plugin.get("installSpec") or "").strip() == normalized_spec:
                    return str(plugin.get("pluginId") or "").strip() or None

        new_plugins = [plugin for plugin in plugins if str(plugin.get("pluginId") or "").strip() not in before_plugin_ids]
        if plugin_type_hint:
            normalized_type = str(plugin_type_hint).strip().lower()
            typed_new_plugins = [plugin for plugin in new_plugins if str(plugin.get("pluginType") or "").strip().lower() == normalized_type]
            if len(typed_new_plugins) == 1:
                return str(typed_new_plugins[0].get("pluginId") or "").strip() or None
        if len(new_plugins) == 1:
            return str(new_plugins[0].get("pluginId") or "").strip() or None
        return None

    def _infer_install_spec(self, *, install_spec: str | None, installer_command: str | None) -> str | None:
        normalized = str(install_spec or "").strip()
        if normalized:
            return normalized
        command = str(installer_command or "").strip()
        if not command:
            return None
        for candidate in _NPM_SPEC_PATTERN.findall(command):
            normalized_candidate = str(candidate).strip()
            if not normalized_candidate:
                continue
            lowered = normalized_candidate.lower()
            if lowered in {
                "npx",
                "npm",
                "openclaw",
                "install",
                "latest",
                "channel",
                "channels",
                "gateway",
            }:
                continue
            if normalized_candidate.startswith("@") or "/" in normalized_candidate:
                if normalized_candidate.endswith("-cli"):
                    return normalized_candidate[:-4]
                return normalized_candidate
        return None

    def _normalize_install_job(self, job: dict[str, Any]) -> dict[str, Any]:
        current = dict(job or {})
        events = list(current.get("events") or [])
        if not events:
            return current
        event_lines = [str((event or {}).get("content") or "") for event in events]
        normalized_user_action = detect_onboarding_hints(event_lines)
        current["userAction"] = normalized_user_action
        if normalized_user_action.get("requiresUserAction") and str(current.get("status") or "") == "completed":
            current["status"] = "needs_user_action"
        return current

    def _plugin_config_fields(self, plugin: dict[str, Any]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for source in (
            list((plugin.get("setupSurface") or {}).get("configFields") or []),
            list((plugin.get("setupSurface") or {}).get("renderableFields") or []),
            list((plugin.get("capabilitySurface") or {}).get("configFields") or []),
            list((plugin.get("capabilitySurface") or {}).get("renderableFields") or []),
        ):
            for field in source:
                if not isinstance(field, dict):
                    continue
                key = str(field.get("key") or "").strip()
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                fields.append(
                    {
                        "key": key,
                        "type": str(field.get("type") or "string").strip() or "string",
                        "required": bool(field.get("required", False)),
                        "label": str(field.get("label") or key).strip() or key,
                        "help": str(field.get("help") or "").strip() or None,
                        "enum": [str(item).strip() for item in list(field.get("enum") or []) if str(item).strip()] or None,
                        "format": str(field.get("format") or "").strip() or None,
                        "scope": str(field.get("scope") or "").strip() or None,
                    }
                )
        return fields

    def _plugin_primary_channel_id(self, plugin: dict[str, Any]) -> str | None:
        for candidate in (
            list((plugin.get("capabilitySurface") or {}).get("channels") or []),
            list((plugin.get("capabilities") or {}).get("channels") or []),
            list((plugin.get("manifestSummary") or {}).get("channels") or []),
        ):
            for item in candidate:
                normalized = str(item or "").strip().lower()
                if normalized:
                    return normalized
        plugin_id = str(plugin.get("pluginId") or "").strip().lower()
        return plugin_id or None

    def _plugin_chat_types(self, plugin: dict[str, Any]) -> list[str]:
        profile = transport_profile(plugin_id=str(plugin.get("pluginId") or "").strip() or None)
        if profile.get("chatTypes"):
            return [str(item).strip() for item in list(profile.get("chatTypes") or []) if str(item).strip()]
        primary = self._plugin_primary_channel_id(plugin)
        if primary in {"openclaw-weixin", "weixin"}:
            return ["direct"]
        return []

    def _config_surface_value_map(self, fields: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in fields:
            key = str(field.get("key") or "").strip()
            if not key:
                continue
            if key in payload:
                values[key] = payload.get(key)
        return values

    def _plugin_config_surface(
        self,
        plugin: dict[str, Any],
        *,
        openclaw_config: dict[str, Any],
        registered_accounts: list[str] | None = None,
    ) -> dict[str, Any]:
        fields = self._plugin_config_fields(plugin)
        setup_surface = dict(plugin.get("setupSurface") or {})
        capability_surface = dict(plugin.get("capabilitySurface") or {})
        validation_mode = self._plugin_validation_mode(plugin)
        render_mode = str(
            setup_surface.get("renderMode")
            or capability_surface.get("renderMode")
            or ("config_schema" if fields else "wizard_only")
        ).strip() or "config_schema"
        plugin_id = str(plugin.get("pluginId") or "").strip()
        plugin_family = str(plugin.get("pluginType") or "plugin").strip().lower() or "plugin"
        chat_types = self._plugin_chat_types(plugin)
        group_supported = "group" in {item.lower() for item in chat_types}
        account_scoped_by_field = any(str(field.get("key") or "").strip() == "accountId" for field in fields)
        if plugin_family == "channel":
            channels_payload = dict(openclaw_config.get("channels") or {})
            plugin_payload = dict(channels_payload.get(plugin_id) or {})
            shared_payload = {key: value for key, value in plugin_payload.items() if key != "accounts"}
            accounts_payload = plugin_payload.get("accounts")
            accounts_map = accounts_payload if isinstance(accounts_payload, dict) else {}
            account_options = sorted(
                {
                    str(item).strip()
                    for item in [*(registered_accounts or []), *list(accounts_map.keys())]
                    if str(item).strip()
                }
            )
            default_account_id = account_options[0] if account_options else None
            preview_source = dict(accounts_map.get(default_account_id) or {}) if default_account_id else dict(shared_payload)
            _, field_errors, normalized_preview, _ = self._normalize_plugin_config_values(plugin, preview_source)
            return {
                "canRender": bool(fields),
                "targetKind": "channel",
                "targetPath": f"channels.{plugin_id}",
                "renderMode": render_mode,
                "renderableFields": fields,
                "accountOptions": account_options,
                "defaultAccountId": default_account_id,
                "accountScoped": bool(account_options) or account_scoped_by_field,
                "values": self._config_surface_value_map(fields, shared_payload),
                "accountValuesById": {
                    account_id: self._config_surface_value_map(fields, dict(accounts_map.get(account_id) or {}))
                    for account_id in account_options
                },
                "chatTypes": chat_types,
                "groupSupported": group_supported,
                "validationMode": validation_mode,
                "fieldErrors": field_errors,
                "normalizedPreview": normalized_preview,
            }
        plugins_payload = dict(openclaw_config.get("plugins") or {})
        entries_payload = dict(plugins_payload.get("entries") or {})
        entry_payload = dict(entries_payload.get(plugin_id) or {})
        config_payload = dict(entry_payload.get("config") or {})
        _, field_errors, normalized_preview, _ = self._normalize_plugin_config_values(plugin, config_payload)
        return {
            "canRender": bool(fields),
            "targetKind": "plugin",
            "targetPath": f"plugins.entries.{plugin_id}.config",
            "renderMode": render_mode,
            "renderableFields": fields,
            "accountOptions": [],
            "defaultAccountId": None,
            "accountScoped": False,
            "values": self._config_surface_value_map(fields, config_payload),
            "accountValuesById": {},
            "chatTypes": chat_types,
            "groupSupported": group_supported,
            "validationMode": validation_mode,
            "fieldErrors": field_errors,
            "normalizedPreview": normalized_preview,
        }

    def _coerce_plugin_config_value(self, field: dict[str, Any], raw_value: Any) -> Any:
        field_type = str(field.get("type") or "string").strip().lower() or "string"
        field_format = str(field.get("format") or "").strip().lower()
        required = bool(field.get("required", False))
        field_key = str(field.get("key") or "").strip() or "字段"
        enum_values = [str(item).strip() for item in list(field.get("enum") or []) if str(item).strip()]
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            if required:
                raise ValueError(f"配置字段 {field_key} 为必填项。")
            return None
        if field_type == "boolean":
            if isinstance(raw_value, bool):
                value = raw_value
            else:
                normalized = str(raw_value).strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    value = True
                elif normalized in {"false", "0", "no", "off"}:
                    value = False
                else:
                    raise ValueError(f"配置字段 {field_key} 需要布尔值。")
            if enum_values and str(value).lower() not in {item.lower() for item in enum_values}:
                raise ValueError(f"配置字段 {field_key} 的值不在允许范围内。")
            return value
        if field_type == "number":
            try:
                value = float(raw_value)
            except Exception as exc:
                raise ValueError(f"配置字段 {field_key} 需要数值。") from exc
            if value.is_integer():
                value = int(value)
            if enum_values and str(value) not in enum_values:
                raise ValueError(f"配置字段 {field_key} 的值不在允许范围内。")
            return value
        value = str(raw_value).strip()
        if field_format == "csv_list":
            normalized_items = [item.strip() for item in re.split(r"[\n,]", value) if item.strip()]
            if required and not normalized_items:
                raise ValueError(f"配置字段 {field_key} 至少需要一个条目。")
            return normalized_items
        if enum_values and value not in enum_values:
            raise ValueError(f"配置字段 {field_key} 的值不在允许范围内。")
        return value

    def _plugin_validation_mode(self, plugin: dict[str, Any]) -> str:
        plugin_id = str(plugin.get("pluginId") or "").strip() or None
        package_manifest = plugin.get("packageManifest") if isinstance(plugin.get("packageManifest"), dict) else None
        profile_key = resolve_plugin_profile_key(plugin_id=plugin_id, package_manifest=package_manifest)
        setup_surface = dict(plugin.get("setupSurface") or {})
        if profile_key in {"discord", "feishu"}:
            return "profile_normalized"
        if bool(setup_surface.get("requiresWizard")) and not self._plugin_config_fields(plugin):
            return "wizard_only"
        return "manifest_schema"

    def _normalize_plugin_config_values(
        self,
        plugin: dict[str, Any],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any], str]:
        normalized = dict(values or {})
        field_errors: dict[str, str] = {}
        plugin_id = str(plugin.get("pluginId") or "").strip() or None
        package_manifest = plugin.get("packageManifest") if isinstance(plugin.get("packageManifest"), dict) else None
        profile_key = resolve_plugin_profile_key(plugin_id=plugin_id, package_manifest=package_manifest)
        validation_mode = self._plugin_validation_mode(plugin)

        if profile_key in {"discord", "feishu"}:
            dm_policy = str(normalized.get("dmPolicy") or "").strip()
            group_policy = str(normalized.get("groupPolicy") or "").strip()
            allow_from = normalized.get("allowFrom")
            group_allow_from = normalized.get("groupAllowFrom")
            normalized_allow_from = [str(item).strip() for item in list(allow_from or []) if str(item).strip()] if isinstance(allow_from, list) else []
            normalized_group_allow_from = [str(item).strip() for item in list(group_allow_from or []) if str(item).strip()] if isinstance(group_allow_from, list) else []

            if dm_policy == "open":
                if "*" not in normalized_allow_from:
                    normalized["allowFrom"] = ["*"]
                    field_errors["allowFrom"] = '当私聊策略为 open 时，OpenClaw 要求 allowFrom 显式包含 "*"；已自动归一化。'
            elif dm_policy != "allowlist" and allow_from:
                normalized.pop("allowFrom", None)
                field_errors["allowFrom"] = "当前私聊策略不会使用 allowFrom，已自动清空。"

            if profile_key == "feishu":
                if group_policy == "open":
                    if "*" not in normalized_group_allow_from:
                        normalized["groupAllowFrom"] = ["*"]
                        field_errors["groupAllowFrom"] = '当群聊策略为 open 时，OpenClaw 要求 groupAllowFrom 显式包含 "*"；已自动归一化。'
                elif group_policy != "allowlist" and group_allow_from:
                    normalized.pop("groupAllowFrom", None)
                    field_errors["groupAllowFrom"] = "当前群聊策略不会使用 groupAllowFrom，已自动清空。"

        return normalized, field_errors, dict(normalized), validation_mode

    def _plugin_support_profile(
        self,
        plugin: dict[str, Any],
        *,
        host_surface: dict[str, Any],
        live_tool_names: list[str] | None = None,
    ) -> dict[str, Any]:
        plugin_family = str(plugin.get("pluginType") or "plugin").strip().lower() or "plugin"
        plugin_id = str(plugin.get("pluginId") or "").strip()
        profile = support_profile(plugin_id=plugin_id or None)
        bridge_ready = bool(host_surface.get("bridgeReady"))
        bridge_status_stale = bool(host_surface.get("bridgeStatusStale"))
        managed_channels = {
            str(item).strip()
            for item in list(host_surface.get("managedChannels") or [])
            if str(item).strip()
        }
        inbound_ownership = str(host_surface.get("inboundOwnership") or "delegated").strip() or "delegated"
        handoff_ready = bool(host_surface.get("handoffReady"))
        resolved_live_tool_names = list(live_tool_names or self._plugin_live_tool_names(plugin))
        channel_target = str(self._channel_login_target(plugin) or plugin_id).strip()
        channel_surface = dict(plugin.get("channelSurface") or {})
        channel_evidence = {
            str(item).strip()
            for item in list(channel_surface.get("evidence") or [])
            if str(item).strip()
        }
        if plugin_family != "channel" and resolved_live_tool_names and bridge_ready:
            return {
                "supportTier": "tool-bridged",
                "executionSupport": "plugin_tools_proxy",
                "familyAdapterReady": True,
                "registrationMode": "openclaw_bridge",
            }
        runtime_transport_ready = bool(
            channel_target
            and (
                channel_target in managed_channels
                or inbound_ownership == "v8_owned"
                or handoff_ready
                or bool(channel_evidence & {"live_inbound", "reply_delivered"})
            )
            and (bridge_ready or bridge_status_stale or inbound_ownership == "v8_owned" or handoff_ready)
        )
        if plugin_family == "channel" and runtime_transport_ready:
            return {
                "supportTier": "transport-hosted",
                "executionSupport": "v8_handoff",
                "familyAdapterReady": True,
                "registrationMode": "transport_only",
            }
        if plugin_family == "channel" and self._managed_local_bridge_read_only() and not runtime_transport_ready and not bridge_status_stale:
            return {
                "supportTier": "handoff unsupported",
                "executionSupport": "execution unsupported",
                "familyAdapterReady": False,
                "registrationMode": "transport_only",
            }
        if plugin_family == "channel" and plugin_id in {"openclaw-weixin", "weixin"} and handoff_ready and inbound_ownership == "v8_owned":
            return {
                "supportTier": "transport-hosted",
                "executionSupport": "v8_handoff",
                "familyAdapterReady": True,
                "registrationMode": "transport_only",
            }
        return {
            "supportTier": str(profile.get("supportTier") or "registered only"),
            "executionSupport": str(profile.get("executionSupport") or "execution unsupported"),
            "familyAdapterReady": bool(profile.get("familyAdapterReady", False)),
            "registrationMode": str(profile.get("registrationMode") or "none"),
        }

    def _plugin_transport_capabilities(self, plugin: dict[str, Any]) -> dict[str, Any]:
        profile = transport_profile(plugin_id=str(plugin.get("pluginId") or "").strip() or None)
        onboarding = onboarding_profile(plugin_id=str(plugin.get("pluginId") or "").strip() or None)
        chat_types = [str(item).strip() for item in list(profile.get("chatTypes") or self._plugin_chat_types(plugin)) if str(item).strip()]
        group_supported = bool(profile.get("groupSupported")) if profile.get("groupSupported") is not None else ("group" in {item.lower() for item in chat_types})
        return {
            "audioOutbound": str(profile.get("audioOutbound") or "none"),
            "audioInbound": str(profile.get("audioInbound") or "none"),
            "fileOutbound": str(profile.get("fileOutbound") or "none"),
            "voiceDeliveryMode": str(profile.get("voiceDeliveryMode") or "unsupported"),
            "chatTypes": chat_types,
            "groupSupported": group_supported,
            "onboardingType": str(onboarding.get("onboardingType") or "config_only"),
        }

    def _build_asset_surface(self, runtime_state: dict[str, Any]) -> dict[str, Any]:
        def _sanitize_asset_payload(value: Any) -> Any:
            if isinstance(value, dict):
                path_plane = str(value.get("pathPlane") or value.get("path_plane") or "").strip() or "runtime_private"
                sanitized = {
                    key: _sanitize_asset_payload(item)
                    for key, item in value.items()
                    if key != "sourcePath"
                    and (path_plane != "runtime_private" or key not in {"workspaceRoot", "workspacePath", "workspaceDirectory"})
                }
                if "pathPlane" not in sanitized:
                    sanitized["pathPlane"] = path_plane
                return sanitized
            if isinstance(value, list):
                return [_sanitize_asset_payload(item) for item in value]
            return value

        return {
            "pathPlane": "runtime_private",
            "lastInboundAsset": _sanitize_asset_payload(runtime_state.get("lastInboundAsset"))
            if isinstance(runtime_state.get("lastInboundAsset"), dict)
            else None,
            "lastOutboundAsset": _sanitize_asset_payload(runtime_state.get("lastOutboundAsset"))
            if isinstance(runtime_state.get("lastOutboundAsset"), dict)
            else None,
            "lastInboundMessageAssets": _sanitize_asset_payload(runtime_state.get("lastInboundMessageAssets"))
            if isinstance(runtime_state.get("lastInboundMessageAssets"), dict)
            else None,
            "lastOutboundMessageAssets": _sanitize_asset_payload(runtime_state.get("lastOutboundMessageAssets"))
            if isinstance(runtime_state.get("lastOutboundMessageAssets"), dict)
            else None,
            "lastTts": _sanitize_asset_payload(runtime_state.get("lastTts"))
            if isinstance(runtime_state.get("lastTts"), dict)
            else None,
        }

    def _plugin_tool_surface(
        self,
        plugin: dict[str, Any],
        *,
        runtime_enabled: bool,
        allowed_families: set[str],
        host_surface: dict[str, Any],
        live_tool_names: list[str] | None = None,
    ) -> dict[str, Any]:
        plugin_family = str(plugin.get("pluginType") or "plugin").strip().lower() or "plugin"
        bridge_ready = bool(host_surface.get("bridgeReady"))
        managed_channels = {
            str(item).strip()
            for item in list(host_surface.get("managedChannels") or [])
            if str(item).strip()
        }
        plugin_id = str(plugin.get("pluginId") or "").strip()
        resolved_live_tool_names = list(live_tool_names or self._plugin_live_tool_names(plugin))
        if resolved_live_tool_names:
            callable_tools = [
                {
                    "name": self._canonical_bridge_tool_name(plugin_id=plugin_id, tool_name=tool_name),
                    "toolName": tool_name,
                    "pluginId": plugin_id,
                    "description": f"通过 OpenClaw bridge 调用 {plugin_id} 注册的原生工具 {tool_name}。",
                    "source": "openclaw_plugin",
                }
                for tool_name in resolved_live_tool_names
            ]
            callable_enabled = bool(runtime_enabled and plugin_family in allowed_families and bridge_ready)
            unavailable_reason: str | None = None
            if not runtime_enabled:
                unavailable_reason = "PluginHostRuntime 当前已关闭。"
            elif plugin_family not in allowed_families:
                unavailable_reason = f"当前宿主未允许 {plugin_family} 家族接管。"
            elif not bridge_ready:
                unavailable_reason = "当前尚未检测到已加载的 OpenClaw bridge 插件。"
            elif plugin_family == "channel" and str(self._channel_login_target(plugin) or plugin_id).strip() not in managed_channels:
                callable_enabled = False
                unavailable_reason = "当前渠道未列入 bridge 的 managedChannels，暂不把它的工具暴露给 V8。"
            return {
                "pluginId": plugin_id,
                "registrationMode": "openclaw_bridge",
                "callableTools": callable_tools,
                "callableEnabled": callable_enabled,
                "unavailableReason": unavailable_reason,
            }

        return {
            "pluginId": plugin_id,
            **plugin_host_tool_registry.describe_plugin(
                plugin=plugin,
                runtime_enabled=runtime_enabled,
                family_allowed=plugin_family in allowed_families,
                host_surface=host_surface,
            ),
        }

    def invoke_tool(self, *, plugin_id: str, tool_name: str, params: dict[str, Any] | None = None) -> Any:
        normalized_plugin_id = str(plugin_id or "").strip()
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_plugin_id:
            raise RuntimeError("缺少 plugin_id，无法代理原生插件工具。")
        if not normalized_tool_name:
            raise RuntimeError("缺少 tool_name，无法代理原生插件工具。")
        return self.invoke_bridge_tool(
            tool_name=self._canonical_bridge_tool_name(
                plugin_id=normalized_plugin_id,
                tool_name=normalized_tool_name,
            ),
            plugin_id=normalized_plugin_id,
            params=params,
        )

    def list_bridge_tools(self, *, query: str | None = None, limit: int = 12, refresh: bool = False) -> dict[str, Any]:
        return self._bridge_tool_catalog(query=query, limit=limit, refresh=refresh)

    def invoke_bridge_tool(
        self,
        *,
        tool_name: str,
        params: dict[str, Any] | None = None,
        plugin_id: str | None = None,
    ) -> Any:
        normalized_tool_name = str(tool_name or "").strip()
        normalized_plugin_id = str(plugin_id or "").strip() or None
        if not normalized_tool_name:
            raise RuntimeError("缺少 tool_name。")
        canonical_name = normalized_tool_name
        if "." not in canonical_name:
            canonical_name = self._canonical_bridge_tool_name(
                plugin_id=normalized_plugin_id,
                tool_name=normalized_tool_name,
            )
        catalog = self._bridge_tool_catalog(limit=256)
        callable_tools = [dict(item) for item in list(catalog.get("inventory") or []) if bool(item.get("allowed"))]
        match = next(
            (
                item
                for item in callable_tools
                if str(item.get("canonicalName") or "").strip() == canonical_name
            ),
            None,
        )
        if not match:
            raise RuntimeError(f"当前未发现可调用的 OpenClaw bridge 工具：{canonical_name}")
        params_payload = dict(params or {})
        action = str(params_payload.pop("action", "") or "").strip() or None
        session_key = str(params_payload.pop("sessionKey", "") or "").strip() or None
        body = self._openclaw_gateway_request_json(
            suffix="/plugins/openclaw-v8-bridge/tools/invoke",
            payload={
                "canonicalName": canonical_name,
                "toolName": str(match.get("toolName") or "").strip() or None,
                "pluginId": str(match.get("pluginId") or "").strip() or None,
                "params": params_payload,
                **({"action": action} if action else {}),
                **({"sessionKey": session_key} if session_key else {}),
            },
            timeout=60,
        )
        if not bool(body.get("ok")):
            detail = str(body.get("error") or body).strip() or "unknown bridge tool error"
            raise RuntimeError(f"OpenClaw bridge 工具执行失败：{detail}")
        return body

    def _bridge_error_allows_cli_fallback(self, exc: Exception) -> bool:
        detail = str(exc).strip() or exc.__class__.__name__
        fallback_markers = (
            "当前尚未检测到已加载的 OpenClaw V8 Bridge",
            "当前未发现可调用的 OpenClaw bridge 工具",
            "无法连接 OpenClaw gateway",
            "OpenClaw gateway 请求失败",
            "OpenClaw gateway 返回了非 JSON 响应",
        )
        return any(marker in detail for marker in fallback_markers)

    def _heal_managed_local_host_after_cli_fallback(self) -> dict[str, Any]:
        if self.is_external_host():
            return {"changed": False}
        try:
            return dict(self._ensure_minimal_managed_local_openclaw_host_config())
        except Exception as exc:
            return {
                "changed": False,
                "error": str(exc).strip() or exc.__class__.__name__,
            }

    def _build_gateway_message_params(
        self,
        *,
        channel_type: str,
        receive_id: str,
        text: str | None = None,
        media_url: str | None = None,
        account_id: str | None = None,
        reply_to_id: str | None = None,
        thread_id: str | None = None,
        tts_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(tts_payload or {})
        params: dict[str, Any] = {
            "action": "send",
            "channel": channel_type,
            "provider": channel_type,
            "to": receive_id,
        }
        normalized_text = str(text or "").strip()
        normalized_media_url = str(media_url or "").strip()
        if normalized_text:
            params["message"] = normalized_text
        if normalized_media_url:
            params["media"] = normalized_media_url
            filename = str(payload.get("fileName") or Path(normalized_media_url).name).strip()
            if filename:
                params["filename"] = filename
        content_type = str(payload.get("mimeType") or "").strip()
        if content_type:
            params["contentType"] = content_type
        if account_id:
            params["accountId"] = account_id
        if reply_to_id:
            params["replyTo"] = reply_to_id
        if thread_id:
            params["threadId"] = thread_id
        if bool(payload.get("asVoice")):
            params["asVoice"] = True
        for source_key, target_key in (
            ("playtimeMs", "playtimeMs"),
            ("sampleRate", "sampleRate"),
            ("bitsPerSample", "bitsPerSample"),
            ("encodeType", "encodeType"),
        ):
            value = payload.get(source_key)
            if value not in (None, ""):
                params[target_key] = value
        return params

    async def _broadcast_via_gateway_message(
        self,
        *,
        channel_type: str,
        receive_id: str,
        text: str | None = None,
        media_url: str | None = None,
        account_id: str | None = None,
        reply_to_id: str | None = None,
        thread_id: str | None = None,
        tts_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = dict(
            await asyncio.to_thread(
                self.invoke_bridge_tool,
                tool_name="gateway.message",
                params=self._build_gateway_message_params(
                    channel_type=channel_type,
                    receive_id=receive_id,
                    text=text,
                    media_url=media_url,
                    account_id=account_id,
                    reply_to_id=reply_to_id,
                    thread_id=thread_id,
                    tts_payload=tts_payload,
                ),
            )
        )
        receipt["deliveryPath"] = "gateway_tool"
        receipt["gatewayTool"] = "gateway.message"
        return receipt

    def _latest_inbound_execution_proof(self, *, channel_type: str | None) -> dict[str, Any]:
        normalized_channel = str(channel_type or "").strip()
        if not normalized_channel:
            return {
                "channelType": None,
                "stage": "no_channel_selected",
                "ownershipProven": False,
                "replyDelivered": False,
                "reason": "当前快照里没有可用于证明真实入站 ownership 的 channel 插件。",
            }

        query = """
            SELECT id, session_id, trigger_source, status, error_message, started_at, metadata
            FROM run_records
            WHERE run_type = 'plugin_host' AND trigger_source = ?
            ORDER BY started_at DESC
            LIMIT 1
        """
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (normalized_channel,))
            row = cursor.fetchone()
            if not row:
                return {
                    "channelType": normalized_channel,
                    "stage": "no_inbound_observed",
                    "ownershipProven": False,
                    "replyDelivered": False,
                    "reason": "当前还没有检测到该渠道在 V8 账本中的真实入站事实。",
                }

            inbound_run = dict(row)
            inbound_run["metadata"] = _parse_json_field(inbound_run.get("metadata"))
            run_id = str(inbound_run.get("id") or "").strip()
            session_id = str(inbound_run.get("session_id") or "").strip()
            started_at = str(inbound_run.get("started_at") or "").strip() or None
            run_status = str(inbound_run.get("status") or "").strip() or "unknown"
            error_message = str(inbound_run.get("error_message") or "").strip() or None

            cursor.execute(
                """
                SELECT topic, created_at, payload_json
                FROM runtime_events
                WHERE run_id = ?
                ORDER BY seq ASC
                """,
                (run_id,),
            )
            inbound_observed_at: str | None = None
            inbound_topics: list[str] = []
            for event_row in cursor.fetchall():
                topic = str(event_row["topic"] or "").strip()
                inbound_topics.append(topic)
                if topic == "plugin_host.inbound.normalized" and not inbound_observed_at:
                    inbound_observed_at = str(event_row["created_at"] or "").strip() or None

            cursor.execute(
                """
                SELECT id, status, error_message, started_at, trigger_source
                FROM run_records
                WHERE run_type = 'plugin_host_push'
                  AND session_id = ?
                  AND started_at >= ?
                ORDER BY started_at DESC
                LIMIT 8
                """,
                (session_id, started_at or ""),
            )
            push_rows = [dict(item) for item in cursor.fetchall()]

        automatic_push = next(
            (
                item
                for item in push_rows
                if str(item.get("trigger_source") or "").strip() == normalized_channel
            ),
            None,
        )
        manual_push = next(
            (
                item
                for item in push_rows
                if _is_manual_plugin_host_push_trigger(item.get("trigger_source"), channel_type=normalized_channel)
            ),
            None,
        )

        push_run_id = str((automatic_push or {}).get("id") or "").strip() or None
        push_status = str((automatic_push or {}).get("status") or "").strip() or None
        push_error = str((automatic_push or {}).get("error_message") or "").strip() or None
        push_started_at = str((automatic_push or {}).get("started_at") or "").strip() or None
        manual_push_run_id = str((manual_push or {}).get("id") or "").strip() or None
        manual_push_status = str((manual_push or {}).get("status") or "").strip() or None
        manual_push_started_at = str((manual_push or {}).get("started_at") or "").strip() or None

        if push_run_id:
            receipt_events = db.get_runtime_events_for_run(push_run_id, session_id=session_id)
            receipt_seen = any(str(item.get("topic") or "").strip() == "plugin_host.push.receipt" for item in receipt_events)
        else:
            receipt_seen = False

        if push_run_id and push_status == "completed" and receipt_seen:
            stage = "reply_delivered"
            reason = "最近一次真实入站已经进入 V8，并且成功写入 plugin_host_push / plugin_host.push.receipt。"
        elif push_run_id and push_status in {"failed", "cancelled"}:
            stage = "outbound_failed"
            reason = push_error or "最近一次真实入站已经进入 V8，但自动出站阶段失败。"
        elif run_status in {"failed", "cancelled"} and error_message and "OpenClaw 出站失败" in error_message:
            stage = "outbound_failed"
            reason = error_message
        elif run_status in {"failed", "cancelled"}:
            stage = "execution_failed"
            reason = error_message or "最近一次真实入站已经进入 V8，但执行链在回复前失败。"
        elif run_status in {"queued", "running", "waiting_input", "waiting_approval", "paused"}:
            stage = "run_in_progress"
            reason = "最近一次真实入站已经进入 V8，当前仍在执行或等待进一步处理。"
        elif manual_push_run_id:
            stage = "manual_outbound_only"
            reason = "当前只观察到人工触发的 plugin_host 出站回执，尚未证明真实入站已经自动回复成功。"
        else:
            stage = "reply_missing"
            reason = "最近一次真实入站已经进入 V8，但暂未观察到对应的 plugin_host_push 回执。"

        return {
            "channelType": normalized_channel,
            "runId": run_id,
            "sessionId": session_id,
            "status": run_status,
            "startedAt": started_at,
            "error": error_message,
            "inboundObservedAt": inbound_observed_at or started_at,
            "ownershipProven": bool(inbound_observed_at or run_id),
            "replyDelivered": bool(push_run_id and push_status == "completed" and receipt_seen),
            "pushRunId": push_run_id,
            "pushStatus": push_status,
            "pushStartedAt": push_started_at,
            "pushError": push_error,
            "manualPushRunId": manual_push_run_id,
            "manualPushStatus": manual_push_status,
            "manualPushStartedAt": manual_push_started_at,
            "stage": stage,
            "reason": reason,
            "topics": inbound_topics,
        }

    def build_snapshot(self, registry: dict[str, Any] | None = None, *, refresh_live_state: bool = False) -> dict[str, Any]:
        payload = registry or default_plugin_registry()
        runtime_config = self.get_runtime_config()
        runtime_enabled = bool(runtime_config.get("enabled", True))
        external_status: dict[str, Any] = {}
        if registry is None and runtime_enabled and self.is_external_host():
            try:
                remote_snapshot = self._external_request_json(method="GET")
                payload = dict(remote_snapshot.get("snapshot") or remote_snapshot)
                external_status = {
                    "reachable": True,
                    "status": "connected",
                    "error": None,
                    "hostSurface": dict((payload.get("hostSurface") or {})) if isinstance(payload.get("hostSurface"), dict) else {},
                }
            except Exception as exc:
                external_status = {"reachable": False, "status": "unreachable", "error": str(exc)}
                payload = default_plugin_registry()
        if not self.is_external_host():
            payload = self._prune_managed_local_registry_noise(payload)
        allowed_families = {str(item).strip().lower() for item in list(runtime_config.get("allowedFamilies") or []) if str(item).strip()}
        raw_plugins = payload.get("plugins") or {}
        raw_jobs = payload.get("installJobs") or {}
        if isinstance(raw_plugins, dict):
            plugins = list(raw_plugins.values())
        elif isinstance(raw_plugins, list):
            plugins = list(raw_plugins)
        else:
            plugins = []
        if isinstance(raw_jobs, dict):
            job_items = list(raw_jobs.values())
        elif isinstance(raw_jobs, list):
            job_items = list(raw_jobs)
        else:
            job_items = []
        plugins = [dict(item) for item in plugins if isinstance(item, dict)]
        if not self.is_external_host():
            plugins = [item for item in plugins if self._plugin_belongs_to_current_managed_root(item)]
        current_plugin_ids = {str(item.get("pluginId") or "").strip() for item in plugins if str(item.get("pluginId") or "").strip()}
        current_install_specs = {
            str(item.get("installSpec") or "").strip()
            for item in plugins
            if str(item.get("installSpec") or "").strip()
        }
        jobs = [
            self._normalize_install_job(dict(item))
            for item in job_items
            if isinstance(item, dict)
            and self._install_job_matches_current_managed_root(
                item,
                current_plugin_ids=current_plugin_ids,
                current_install_specs=current_install_specs,
            )
        ]
        plugins.sort(key=lambda item: str(item.get("displayName") or item.get("pluginId") or "").lower())
        jobs.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        openclaw_config = self._read_managed_local_openclaw_config() if not self.is_external_host() else {}
        plugins_inventory = self._managed_local_plugins_inventory(refresh=refresh_live_state) if not self.is_external_host() else {"plugins": []}
        bridge_state = self._managed_local_bridge_state(
            refresh=refresh_live_state,
            inventory=plugins_inventory,
            openclaw_config=openclaw_config,
        )
        channel_accounts_state = self._managed_local_channel_accounts(refresh=refresh_live_state) if not self.is_external_host() else {}
        live_tool_names_by_plugin: dict[str, list[str]] = {}
        live_inventory_by_plugin: dict[str, dict[str, Any]] = {}
        for plugin_record in list(plugins_inventory.get("plugins") or []):
            if not isinstance(plugin_record, dict):
                continue
            plugin_record_id = str(plugin_record.get("id") or "").strip()
            if not plugin_record_id:
                continue
            live_inventory_by_plugin[plugin_record_id] = dict(plugin_record)
            live_tool_names_by_plugin[plugin_record_id] = [
                str(item).strip()
                for item in list(plugin_record.get("toolNames") or [])
                if str(item).strip()
            ]
        managed_channels = {
            str(item).strip()
            for item in list(bridge_state.get("managedChannels") or [])
            if str(item).strip()
        }
        primary_channel_candidates: list[tuple[int, str]] = []
        for plugin in plugins:
            if not isinstance(plugin, dict):
                continue
            if str(plugin.get("pluginType") or "").strip().lower() != "channel":
                continue
            channel_target = self._channel_login_target(dict(plugin))
            if not channel_target:
                continue
            plugin_id = str(plugin.get("pluginId") or "").strip()
            profile = support_profile(plugin_id=plugin_id or None)
            priority = 50
            if channel_target in managed_channels:
                priority = 0
            elif bool(profile.get("familyAdapterReady")):
                priority = 0
            elif str(profile.get("supportTier") or "").strip().lower() == "handoff unsupported":
                priority = 100
            primary_channel_candidates.append((priority, channel_target))
        primary_channel = sorted(primary_channel_candidates, key=lambda item: (item[0], item[1]))[0][1] if primary_channel_candidates else None
        primary_channel_plugin = next(
            (
                plugin
                for plugin in plugins
                if str(self._channel_login_target(plugin) or "").strip() == str(primary_channel or "").strip()
            ),
            None,
        )
        recent_inbound_proof = self._latest_inbound_execution_proof(channel_type=primary_channel)
        handoff_audit = self._recent_openclaw_handoff_audit(channel_type=primary_channel)
        if (
            self._managed_local_bridge_read_only()
            and not bool((handoff_audit or {}).get("observedInbound"))
            and not bool((recent_inbound_proof or {}).get("ownershipProven"))
        ):
            recent_inbound_proof = {}
        handoff_audit = self._normalize_handoff_audit(
            handoff_audit=handoff_audit,
            recent_inbound_proof=recent_inbound_proof,
            bridge_state=bridge_state,
        )
        host_surface = self._build_host_surface(
            runtime_config=runtime_config,
            runtime_enabled=runtime_enabled,
            allowed_families=allowed_families,
            external_status=external_status,
            handoff_audit=handoff_audit,
            recent_inbound_proof=recent_inbound_proof,
            managed_channel_plugin=primary_channel_plugin,
            bridge_state=bridge_state,
            gateway_health=(
                self._managed_local_gateway_health()
                if (not self.is_external_host() and runtime_enabled and "channel" in allowed_families)
                else None
            ),
        )
        runtime_state = self._get_runtime_state(payload)
        host_surface["recentInboundProof"] = recent_inbound_proof
        if not host_surface.get("lastInboundHandoffAt") and recent_inbound_proof.get("ownershipProven"):
            host_surface["lastInboundHandoffAt"] = recent_inbound_proof.get("inboundObservedAt")
        host_surface["assetSurface"] = self._build_asset_surface(runtime_state)
        host_surface["executionBoundary"] = {
            "summary": "PluginHostRuntime 只负责渠道 transport、入站 handoff、出站发送与宿主状态；本地文件处理、工具调用、自动化与 RPA 仍由其他 runtime 执行。",
            "localExecutionOwnedBy": ["chat", "extensions", "automation", "computer_use", "rpa"],
            "pluginHostDoesNotOwnLocalExecution": True,
        }
        latest_job_by_plugin: dict[str, dict[str, Any]] = {}
        latest_job_by_install_spec: dict[str, dict[str, Any]] = {}
        for job in jobs:
            key = str(job.get("pluginId") or "").strip()
            if key and key not in latest_job_by_plugin:
                latest_job_by_plugin[key] = dict(job)
            spec = str(job.get("installSpec") or "").strip()
            if spec and spec not in latest_job_by_install_spec:
                latest_job_by_install_spec[spec] = dict(job)

        enriched_plugins: list[dict[str, Any]] = []
        actionable_job_ids: set[str] = set()
        for plugin in plugins:
            current = dict(plugin)
            latest_job = latest_job_by_plugin.get(str(current.get("pluginId") or "").strip()) or latest_job_by_install_spec.get(
                str(current.get("installSpec") or "").strip()
            )
            plugin_id = str(current.get("pluginId") or "").strip()
            live_inventory_record = dict(live_inventory_by_plugin.get(plugin_id) or {})
            plugin_live_tool_names = list(live_tool_names_by_plugin.get(plugin_id) or [])
            live_enabled = bool(live_inventory_record.get("enabled"))
            live_status = str(live_inventory_record.get("status") or "").strip().lower()
            if live_inventory_record:
                current["liveInventoryStatus"] = live_status or None
                current["liveInventoryEnabled"] = live_enabled
                if live_status in {"disabled", "error"} or not live_enabled:
                    current["activationState"] = "disabled"
                    current["lifecycleState"] = "disabled"
                    current["healthState"] = "disabled"
                elif live_status == "loaded":
                    current["activationState"] = "active"
                    if str(current.get("lifecycleState") or "").strip().lower() == "disabled":
                        current["lifecycleState"] = "installed"
            channel_ids, registered_accounts = self._plugin_channel_accounts(current, channel_accounts_state)
            plugin_channel_target = _normalize_openclaw_channel_id(self._channel_login_target(current))
            recent_inbound_channel = _normalize_openclaw_channel_id(str(recent_inbound_proof.get("channelType") or "").strip())
            live_channel_proven = bool(
                str(current.get("pluginType") or "").strip().lower() == "channel"
                and plugin_channel_target
                and plugin_channel_target == recent_inbound_channel
                and bool(recent_inbound_proof.get("ownershipProven") or recent_inbound_proof.get("replyDelivered"))
            )
            current["channelSurface"] = {
                "channelIds": channel_ids,
                "registeredAccounts": registered_accounts,
                "configured": bool(registered_accounts) or live_channel_proven,
                "liveInboundProven": live_channel_proven,
                "replyDelivered": bool(live_channel_proven and recent_inbound_proof.get("replyDelivered")),
                "evidence": self._channel_surface_evidence(
                    registered_accounts=registered_accounts,
                    live_inbound_proven=live_channel_proven,
                    reply_delivered=bool(live_channel_proven and recent_inbound_proof.get("replyDelivered")),
                ),
            }
            if (registered_accounts or live_channel_proven) and str(current.get("pluginType") or "").strip().lower() == "channel":
                if str(current.get("setupState") or "").strip().lower() in {"installed", "needs_user_action", "failed"}:
                    current["setupState"] = "onboarded"
                if str(current.get("activationState") or "").strip().lower() != "disabled":
                    current["activationState"] = "active"
                if latest_job:
                    latest_job = dict(latest_job)
                    if str(latest_job.get("status") or "").strip().lower() in {"needs_user_action", "running", "queued", "failed"}:
                        latest_job["status"] = "completed"
                        latest_job["finishedAt"] = latest_job.get("finishedAt") or _now_iso()
                    latest_job["userAction"] = {
                        "urls": [],
                        "qrHints": [],
                        "qrBlocks": [],
                        "instructions": ["检测到渠道已存在真实入站或已登记账号，本次首次接入视为已完成。"],
                        "requiresUserAction": False,
                    }
            current["latestInstallJob"] = latest_job
            setup_surface = merge_setup_user_action(
                dict(current.get("setupSurface") or {}),
                user_action=dict((latest_job or {}).get("userAction") or {}),
                job_status=str((latest_job or {}).get("status") or ""),
            )
            onboarding_surface = onboarding_profile(plugin=current)
            current["setupSurface"] = {
                **setup_surface,
                "actionMode": str(setup_surface.get("actionMode") or onboarding_surface.get("actionMode") or "config_form"),
                "manualSteps": list(setup_surface.get("manualSteps") or onboarding_surface.get("manualSteps") or []),
                "docsUrl": str(setup_surface.get("docsUrl") or onboarding_surface.get("docsUrl") or "").strip() or None,
                "requiredSecrets": list(setup_surface.get("requiredSecrets") or onboarding_surface.get("requiredSecrets") or []),
                "requiredIds": list(setup_surface.get("requiredIds") or onboarding_surface.get("requiredIds") or []),
                "pairingMode": str(setup_surface.get("pairingMode") or onboarding_surface.get("pairingMode") or "none"),
                "onboardingType": str(setup_surface.get("onboardingType") or onboarding_surface.get("onboardingType") or "config_only"),
            }
            current["configSurface"] = self._plugin_config_surface(
                current,
                openclaw_config=openclaw_config,
                registered_accounts=registered_accounts,
            )
            current.update(evaluate_plugin_health(current, latest_job=latest_job))
            plugin_family = str(current.get("pluginType") or "plugin").strip().lower() or "plugin"
            support_state = self._plugin_support_profile(
                current,
                host_surface=host_surface,
                live_tool_names=plugin_live_tool_names,
            )
            current.update(support_state)
            current["transportCapabilities"] = self._plugin_transport_capabilities(current)
            current["toolSurface"] = self._plugin_tool_surface(
                current,
                runtime_enabled=runtime_enabled,
                allowed_families=allowed_families,
                host_surface=host_surface,
                live_tool_names=plugin_live_tool_names,
            )
            runtime_block_reasons: list[str] = []
            if not runtime_enabled:
                runtime_block_reasons.append("PluginHostRuntime 当前已关闭。")
            if plugin_family not in allowed_families:
                runtime_block_reasons.append(f"当前宿主未允许 {plugin_family} 家族接管执行。")
            if (
                plugin_family == "channel"
                and runtime_enabled
                and plugin_family in allowed_families
                and not bool(host_surface.get("outboundReady"))
            ):
                runtime_block_reasons.append("PluginHost 宿主数据面当前未就绪，渠道出站仍不可用。")
                gateway_runtime_status = str(((host_surface.get("gatewayHealth") or {}).get("runtime") or {}).get("status") or "").strip()
                if gateway_runtime_status == "missing_cli":
                    runtime_block_reasons.append("本地 OpenClaw CLI 当前不可解析。")
                elif gateway_runtime_status == "config_invalid":
                    runtime_block_reasons.append("当前 OpenClaw 配置不合法，请先修复渠道配置。")
                elif gateway_runtime_status in {"stopped", "error", "unknown"}:
                    runtime_block_reasons.append("本地 gateway 当前未启动或尚未完成启动。")
                if not bool(((host_surface.get("gatewayHealth") or {}).get("rpc") or {}).get("ok")):
                    runtime_block_reasons.append("本地 gateway RPC 尚未就绪。")
                if str(current.get("healthState") or "") == "healthy":
                    current["healthState"] = "gateway_unavailable"
                    current["lifecycleState"] = "degraded"
            inbound_ownership = str(host_surface.get("inboundOwnership") or "delegated").strip() or "delegated"
            if (
                plugin_family == "channel"
                and runtime_enabled
                and plugin_family in allowed_families
                and inbound_ownership not in {"v8_owned", "unverified"}
            ):
                runtime_block_reasons.append("当前真实入站所有权尚未切到 V8 PluginHostRuntime，消息仍可能由 sidecar delegated。")
                if str(current.get("healthState") or "") == "healthy":
                    current["healthState"] = "handoff_unready"
                    current["lifecycleState"] = "degraded"
            if str(current.get("supportTier") or "") == "registered only":
                runtime_block_reasons.append("当前插件仅支持发现、注册与状态展示，尚未提供 V8 原生执行适配。")
            elif str(current.get("supportTier") or "") == "handoff unsupported":
                runtime_block_reasons.append("当前插件尚未具备 V8-owned inbound handoff，不能作为受管 transport-hosted 插件运行。")
            tool_unavailable_reason = str((current.get("toolSurface") or {}).get("unavailableReason") or "").strip()
            registration_mode = str((current.get("toolSurface") or {}).get("registrationMode") or "").strip()
            if tool_unavailable_reason and registration_mode not in {"transport_only", "none"}:
                runtime_block_reasons.append(tool_unavailable_reason)
            current["runtimeSurface"] = {
                "enabled": runtime_enabled,
                "allowedFamilies": sorted(allowed_families),
                "familyAllowed": plugin_family in allowed_families,
                "routable": runtime_enabled and plugin_family in allowed_families,
            }
            current["unavailableReasons"] = list(dict.fromkeys([*list(current.get("unavailableReasons") or []), *runtime_block_reasons]))
            if latest_job and str(latest_job.get("status") or "").strip().lower() in {"queued", "running", "needs_user_action"}:
                actionable_job_id = str(latest_job.get("jobId") or "").strip()
                if actionable_job_id:
                    actionable_job_ids.add(actionable_job_id)
            enriched_plugins.append(current)

        return {
            "pluginRoot": str(payload.get("pluginRoot") or self.managed_local_root()),
            "pluginExtensionsRoot": str(payload.get("pluginExtensionsRoot") or (self.managed_local_root() / "extensions")),
            "pluginInstallLogRoot": str(payload.get("pluginInstallLogRoot") or PLUGIN_INSTALL_LOG_ROOT),
            "installCatalog": build_install_catalog(),
            "runtimeConfig": runtime_config,
            "hostSurface": host_surface,
            "controlSurface": self._control_surface(runtime_config=runtime_config),
            "plugins": enriched_plugins,
            "installJobs": jobs,
            "safetySummary": build_group_guard_summary(),
            "summary": {
                "pluginCount": len(enriched_plugins),
                "activeCount": sum(
                    1
                    for item in enriched_plugins
                    if (
                        str(item.get("activationState")) == "active"
                        and str(item.get("healthState")) == "healthy"
                        and bool((item.get("runtimeSurface") or {}).get("routable"))
                    )
                ),
                "channelPluginCount": sum(1 for item in enriched_plugins if str(item.get("pluginType")) == "channel"),
                "pendingJobCount": len(actionable_job_ids),
            },
        }

    @staticmethod
    def _public_plugin_snapshot_item(plugin: dict[str, Any]) -> dict[str, Any]:
        transport = dict(plugin.get("transportCapabilities") or {})
        channel_surface = PluginHostService._public_plugin_channel_surface(plugin)
        onboarding_completed = bool(
            str(plugin.get("setupState") or "").strip().lower() == "onboarded"
            and {
                str(item).strip()
                for item in list(channel_surface.get("evidence") or [])
                if str(item).strip()
            }
            & {"live_inbound", "reply_delivered"}
        )
        return {
            "pluginId": str(plugin.get("pluginId") or "").strip() or None,
            "displayName": str(plugin.get("displayName") or "").strip() or None,
            "pluginType": str(plugin.get("pluginType") or "").strip() or None,
            "source": str(plugin.get("source") or "").strip() or None,
            "installSpec": str(plugin.get("installSpec") or "").strip() or None,
            "installPath": str(plugin.get("installPath") or "").strip() or None,
            "setupState": str(plugin.get("setupState") or "").strip() or None,
            "activationState": str(plugin.get("activationState") or "").strip() or None,
            "lifecycleState": str(plugin.get("lifecycleState") or "").strip() or None,
            "healthState": str(plugin.get("healthState") or "").strip() or None,
            "supportTier": str(plugin.get("supportTier") or "").strip() or None,
            "familyAdapterReady": bool(plugin.get("familyAdapterReady")),
            "onboardingCompleted": onboarding_completed,
            "unavailableReasons": [str(item).strip() for item in list(plugin.get("unavailableReasons") or []) if str(item).strip()],
            "transportCapabilities": {
                "chatTypes": [str(item).strip() for item in list(transport.get("chatTypes") or []) if str(item).strip()],
                "groupSupported": bool(transport.get("groupSupported")),
                "onboardingType": str(transport.get("onboardingType") or "").strip() or None,
                "audioOutbound": str(transport.get("audioOutbound") or "").strip() or None,
                "audioInbound": str(transport.get("audioInbound") or "").strip() or None,
                "fileOutbound": str(transport.get("fileOutbound") or "").strip() or None,
                "voiceDeliveryMode": str(transport.get("voiceDeliveryMode") or "").strip() or None,
            },
            "channelSurface": channel_surface,
        }

    def _public_snapshot_from_full(self, current: dict[str, Any]) -> dict[str, Any]:
        return {
            "pluginRoot": str(current.get("pluginRoot") or "").strip() or None,
            "pluginExtensionsRoot": str(current.get("pluginExtensionsRoot") or "").strip() or None,
            "runtimeConfig": dict(current.get("runtimeConfig") or {}),
            "hostSurface": dict(current.get("hostSurface") or {}),
            "controlSurface": dict(current.get("controlSurface") or {}),
            "plugins": [
                self._public_plugin_snapshot_item(dict(plugin))
                for plugin in list(current.get("plugins") or [])
                if isinstance(plugin, dict)
            ],
            "summary": dict(current.get("summary") or {}),
        }

    def public_snapshot(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        if snapshot is not None:
            public = self._public_snapshot_from_full(dict(snapshot))
            self._set_cached_public_snapshot(public)
            return self._decorate_public_snapshot(public)
        if self._cached_public_snapshot is None:
            self._set_cached_public_snapshot(self._minimal_public_snapshot())
        return self._decorate_public_snapshot(dict(self._cached_public_snapshot or {}))

    def bridge_doctor(self, *, refresh: bool = False) -> dict[str, Any]:
        if self.is_external_host():
            try:
                response = self._external_request_json(method="GET", suffix="doctor")
                if isinstance(response, dict):
                    return dict(response)
            except Exception as exc:
                checks = [
                    self._bridge_doctor_check(
                        key="external_plugin_host_doctor",
                        status="warning",
                        title="外部 PluginHost doctor 不可达",
                        description="当前 external host 尚未返回自己的 doctor 结果，页面只展示本地可见的最小诊断。",
                        details=str(exc).strip() or exc.__class__.__name__,
                    )
                ]
                return {
                    "checks": checks,
                    "summary": self._summarize_bridge_doctor_checks(checks),
                    "repairPlan": [],
                    "repairApplied": [],
                    "restartRequired": False,
                    "postRepairVerification": None,
                }
        report = self._build_bridge_doctor_report(refresh=refresh)
        return {
            **report,
            "repairApplied": [],
            "restartRequired": False,
            "postRepairVerification": None,
        }

    async def bridge_doctor_repair(self) -> dict[str, Any]:
        if self.is_external_host():
            response = self._external_request_json(method="POST", suffix="doctor/repair", payload={})
            return dict(response) if isinstance(response, dict) else {}

        initial_report = self._build_bridge_doctor_report(refresh=True)
        repair_applied: list[dict[str, Any]] = []
        restart_required = False
        repo_root = self._managed_local_bridge_repo_root()
        package_name = self._bridge_package_name(repo_root)
        checks_by_key = {
            str(item.get("key") or "").strip(): dict(item)
            for item in list(initial_report.get("checks") or [])
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }

        def _apply_step(key: str, title: str, description: str, **extra: Any) -> None:
            repair_applied.append(
                {
                    "key": key,
                    "title": title,
                    "description": description,
                    **extra,
                }
            )

        runtime_drift_status = str((checks_by_key.get("bridge_runtime_drift") or {}).get("status") or "").strip().lower()
        status_route_status = str((checks_by_key.get("bridge_status_route_reachable") or {}).get("status") or "").strip().lower()
        slot_root = self._managed_local_bridge_extension_path()
        repo_hash = self._hash_plugin_root(repo_root)
        slot_hash = self._hash_plugin_root(slot_root)

        install_capture: dict[str, Any] | None = None
        if repo_root is not None:
            try:
                install_capture = self._run_openclaw_command_capture("plugins", "install", "--link", str(repo_root), timeout=300)
            except Exception as exc:
                install_capture = {"returnCode": -1, "stderr": str(exc).strip() or exc.__class__.__name__}
            _apply_step(
                "bridge_install_or_link",
                "执行 bridge canonical link",
                "尝试通过 openclaw plugins install --link 把当前仓库 bridge 纳入 OpenClaw 4.8 的 canonical install/link 主链。",
                commandHint=f"openclaw plugins install --link {repo_root}",
                result=install_capture,
            )
        else:
            try:
                install_capture = self._run_openclaw_command_capture("plugins", "install", package_name, timeout=300)
            except Exception as exc:
                install_capture = {"returnCode": -1, "stderr": str(exc).strip() or exc.__class__.__name__}
            _apply_step(
                "bridge_install_package",
                "执行 bridge canonical install",
                "当前机器未检测到 bridge 仓库根，改为尝试通过正式 npm spec 安装 bridge。",
                commandHint=f"openclaw plugins install {package_name}",
                result=install_capture,
            )

        if (runtime_drift_status in {"critical", "warning"} or status_route_status == "critical") and os.path.lexists(str(slot_root)) and repo_hash and slot_hash and repo_hash != slot_hash:
            try:
                backup_result = self._backup_managed_local_bridge_extension_slot(reason="doctor-repair")
                _apply_step(
                    "backup_floating_bridge_slot",
                    "备份漂浮的 live bridge 拷贝",
                    "检测到 ~/.openclaw/extensions/openclaw-v8-bridge 与仓库桥接代码 hash 不一致，先移入备份目录，避免继续由旧拷贝提供 live route。",
                    result=backup_result,
                )
            except Exception as exc:
                _apply_step(
                    "backup_floating_bridge_slot",
                    "备份漂浮的 live bridge 拷贝失败",
                    "尝试备份 ~/.openclaw/extensions/openclaw-v8-bridge 时失败，后续只能继续尝试最小修复。",
                    error=str(exc).strip() or exc.__class__.__name__,
                )

        allow_before = self._managed_local_plugins_allow_state()
        synced_allowlist = self._sync_managed_local_plugins_allowlist()
        allow_after = self._managed_local_plugins_allow_state()
        if list(allow_before.get("values") or []) != list(allow_after.get("values") or []):
            _apply_step(
                "sync_plugins_allow",
                "同步 plugins.allow",
                "把 bridge 与当前已托管的 channel plugin 写入 plugins.allow，结束 global auto-discovery 漂浮态。",
                before=list(allow_before.get("values") or []),
                after=list(allow_after.get("values") or []),
                expected=list(allow_after.get("expected") or []),
            )
        elif synced_allowlist:
            _apply_step(
                "sync_plugins_allow",
                "复检 plugins.allow",
                "plugins.allow 已经覆盖当前稳定托管的 bridge / channel plugin，无需额外改写。",
                after=list(allow_after.get("values") or []),
            )

        try:
            host_config_result = self._ensure_minimal_managed_local_openclaw_host_config()
            if bool(host_config_result.get("changed")):
                _apply_step(
                    "repair_minimal_host_config",
                    "修复最小宿主配置",
                    "仅最小写入 bridge 私有配置与 gateway.mode，不再重建旧 3.27 风格的大块 openclaw.json。",
                    result=host_config_result,
                )
        except Exception as exc:
            _apply_step(
                "repair_minimal_host_config",
                "修复最小宿主配置失败",
                "写入 bridge 私有配置或 gateway.mode 时出现错误。",
                error=str(exc).strip() or exc.__class__.__name__,
            )
            restart_required = True

        try:
            launcher_patch = self._ensure_managed_local_gateway_launcher_handoff()
            _apply_step(
                "repair_gateway_launcher_handoff",
                "修复 gateway launcher handoff",
                "校正 gateway.cmd 中的 V8 inbound URL 与 handoff token 注入链。",
                result=launcher_patch,
            )
        except Exception as exc:
            _apply_step(
                "repair_gateway_launcher_handoff",
                "修复 gateway launcher handoff 失败",
                "gateway launcher env 注入失败，bridge 仍可能无法把消息 handoff 给 V8。",
                error=str(exc).strip() or exc.__class__.__name__,
            )
            restart_required = True

        gateway_events: list[dict[str, Any]] = []

        def _append_gateway_event(kind: str, message: str) -> None:
            gateway_events.append({"kind": str(kind), "message": str(message)})

        gateway_ready = False
        try:
            gateway_ready = await self._ensure_gateway_runtime(env=self._managed_local_env(), append_event=_append_gateway_event)
            _apply_step(
                "restart_gateway",
                "重启并复检 gateway",
                "按最小宿主配置重新拉起 OpenClaw gateway，并立即复检 bridge live route。",
                gatewayReady=bool(gateway_ready),
                events=gateway_events[-20:],
            )
        except Exception as exc:
            restart_required = True
            _apply_step(
                "restart_gateway",
                "重启并复检 gateway 失败",
                "gateway 重启过程中仍有错误，后续需要人工复检。",
                error=str(exc).strip() or exc.__class__.__name__,
                events=gateway_events[-20:],
            )

        post_report = self._build_bridge_doctor_report(refresh=True)
        post_checks_by_key = {
            str(item.get("key") or "").strip(): dict(item)
            for item in list(post_report.get("checks") or [])
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }
        route_still_missing = str((post_checks_by_key.get("bridge_status_route_reachable") or {}).get("status") or "").strip().lower() == "critical"
        if route_still_missing and repo_root is not None:
            try:
                mirror_result = self._ensure_managed_local_bridge_extension_link()
                _apply_step(
                    "fallback_bridge_mirror_surface",
                    "回退到 bridge mirror surface",
                    "canonical install/link 之后 live route 仍未恢复，回退到 ~/.openclaw/extensions mirror surface 作为 fail-closed 兜底。",
                    result=mirror_result,
                )
                gateway_events = []
                gateway_ready = await self._ensure_gateway_runtime(env=self._managed_local_env(), append_event=_append_gateway_event)
                _apply_step(
                    "restart_gateway_after_mirror",
                    "mirror surface 后再次重启 gateway",
                    "用仓库桥接代码重新物化 live slot 后再次拉起 gateway，验证 /status 与 /tools 是否恢复。",
                    gatewayReady=bool(gateway_ready),
                    events=gateway_events[-20:],
                )
            except Exception as exc:
                restart_required = True
                _apply_step(
                    "fallback_bridge_mirror_surface",
                    "回退到 bridge mirror surface 失败",
                    "bridge live route 仍未恢复，且兜底 mirror surface 也未成功。",
                    error=str(exc).strip() or exc.__class__.__name__,
                )
            post_report = self._build_bridge_doctor_report(refresh=True)

        current_snapshot = self.public_snapshot(self.build_snapshot())
        restart_required = restart_required or bool(
            str((post_report.get("summary") or {}).get("status") or "").strip().lower() == "critical"
            and not bool((post_report.get("summary") or {}).get("okCount"))
        )
        return {
            **initial_report,
            "repairApplied": repair_applied,
            "restartRequired": restart_required,
            "postRepairVerification": {
                "summary": dict(post_report.get("summary") or {}),
                "checks": [dict(item) for item in list(post_report.get("checks") or []) if isinstance(item, dict)],
            },
            "pluginHost": current_snapshot,
        }

    def get_install_job(self, job_id: str) -> dict[str, Any] | None:
        if self.is_external_host():
            response = self._external_request_json(method="GET", suffix=f"install-jobs/{urllib_parse.quote(job_id, safe='')}")
            job = response.get("job") if isinstance(response, dict) else None
            return self._normalize_install_job(dict(job)) if isinstance(job, dict) else None
        payload = default_plugin_registry()
        job = (payload.get("installJobs") or {}).get(job_id)
        return self._normalize_install_job(dict(job)) if isinstance(job, dict) else None

    def set_activation_state(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        if self.is_external_host():
            response = self._external_request_json(
                method="POST",
                suffix=f"plugins/{urllib_parse.quote(plugin_id, safe='')}/activation",
                payload={"enabled": bool(enabled)},
            )
            snapshot = dict(response.get("snapshot") or response)
            snapshot["runtimeConfig"] = self.get_runtime_config()
            return self.build_snapshot(snapshot)
        registry = update_plugin_record(
            plugin_id,
            {
                "activationState": "active" if enabled else "disabled",
            },
        )
        return self.build_snapshot(registry)

    async def save_plugin_config(
        self,
        plugin_id: str,
        *,
        values: dict[str, Any] | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            raise ValueError("缺少插件 ID。")
        payload_values = dict(values or {})
        normalized_account_id = str(account_id or "").strip() or None
        if self.is_external_host():
            response = self._external_request_json(
                method="POST",
                suffix=f"plugins/{urllib_parse.quote(normalized_plugin_id, safe='')}/config",
                payload={
                    "values": payload_values,
                    "accountId": normalized_account_id,
                },
            )
            snapshot = response.get("pluginHost") if isinstance(response, dict) else None
            if isinstance(snapshot, dict):
                return self.build_snapshot(dict(snapshot))
            return self.build_snapshot()

        registry = scan_plugin_registry()
        plugin = dict((registry.get("plugins") or {}).get(normalized_plugin_id) or {})
        if not plugin:
            raise KeyError(normalized_plugin_id)

        fields = self._plugin_config_fields(plugin)
        if not fields:
            raise ValueError("当前插件未声明可渲染的配置字段。")
        field_map = {str(field.get("key") or "").strip(): field for field in fields if str(field.get("key") or "").strip()}
        unknown_keys = [key for key in payload_values.keys() if str(key).strip() and str(key).strip() not in field_map]
        if unknown_keys:
            raise ValueError(f"存在未声明的配置字段：{', '.join(sorted(unknown_keys))}")

        normalized_values: dict[str, Any] = {}
        for key, field in field_map.items():
            if key not in payload_values:
                continue
            normalized_values[key] = self._coerce_plugin_config_value(field, payload_values.get(key))
        normalized_values, field_errors, normalized_preview, _validation_mode = self._normalize_plugin_config_values(plugin, normalized_values)
        shared_scoped_keys = {
            key
            for key, field in field_map.items()
            if str(field.get("scope") or "").strip().lower() == "shared"
        }

        openclaw_config = self._read_managed_local_openclaw_config()
        plugin_family = str(plugin.get("pluginType") or "plugin").strip().lower() or "plugin"
        if plugin_family == "channel":
            channels_payload = dict(openclaw_config.get("channels") or {})
            plugin_payload = dict(channels_payload.get(normalized_plugin_id) or {})
            if normalized_account_id:
                accounts_payload = dict(plugin_payload.get("accounts") or {})
                target_payload = dict(accounts_payload.get(normalized_account_id) or {})
            else:
                target_payload = {key: value for key, value in plugin_payload.items() if key != "accounts"}
            shared_payload = {key: value for key, value in plugin_payload.items() if key != "accounts"}

            for key, value in normalized_values.items():
                if key in shared_scoped_keys:
                    if value is None:
                        shared_payload.pop(key, None)
                    else:
                        shared_payload[key] = value
                    continue
                if value is None:
                    target_payload.pop(key, None)
                else:
                    target_payload[key] = value

            if normalized_account_id:
                accounts_payload = dict(plugin_payload.get("accounts") or {})
                if target_payload:
                    accounts_payload[normalized_account_id] = target_payload
                else:
                    accounts_payload.pop(normalized_account_id, None)
                plugin_payload = dict(shared_payload)
                if accounts_payload:
                    plugin_payload["accounts"] = accounts_payload
            else:
                preserved_accounts = dict(plugin_payload.get("accounts") or {})
                plugin_payload = dict(target_payload)
                if preserved_accounts:
                    plugin_payload["accounts"] = preserved_accounts

            channels_payload[normalized_plugin_id] = plugin_payload
            openclaw_config["channels"] = channels_payload
        else:
            plugins_payload = dict(openclaw_config.get("plugins") or {})
            entries_payload = dict(plugins_payload.get("entries") or {})
            plugin_entry = dict(entries_payload.get(normalized_plugin_id) or {})
            config_payload = dict(plugin_entry.get("config") or {})
            for key, value in normalized_values.items():
                if value is None:
                    config_payload.pop(key, None)
                else:
                    config_payload[key] = value
            if config_payload:
                plugin_entry["config"] = config_payload
            else:
                plugin_entry.pop("config", None)
            entries_payload[normalized_plugin_id] = plugin_entry
            plugins_payload["entries"] = entries_payload
            openclaw_config["plugins"] = plugins_payload

        self._write_managed_local_openclaw_config(openclaw_config)

        if self.is_enabled() and self.is_managed_local() and self.family_allowed("channel"):
            await self.start()
            registry = scan_plugin_registry()
        else:
            registry = scan_plugin_registry()
        snapshot = self.build_snapshot(registry)
        if field_errors:
            for item in list(snapshot.get("plugins") or []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("pluginId") or "").strip() != normalized_plugin_id:
                    continue
                config_surface = dict(item.get("configSurface") or {})
                config_surface["fieldErrors"] = field_errors
                config_surface["normalizedPreview"] = normalized_preview
                item["configSurface"] = config_surface
                break
        return snapshot

    async def refresh_plugin_health(self, plugin_id: str) -> dict[str, Any]:
        if self.is_external_host():
            response = self._external_request_json(
                method="POST",
                suffix=f"plugins/{urllib_parse.quote(plugin_id, safe='')}/health",
            )
            snapshot = dict(response.get("snapshot") or response)
            snapshot["runtimeConfig"] = self.get_runtime_config()
            return self.build_snapshot(snapshot)
        registry = default_plugin_registry()
        plugin = dict((registry.get("plugins") or {}).get(plugin_id) or {})
        if not plugin:
            raise KeyError(plugin_id)
        updated = update_plugin_record(
            plugin_id,
            evaluate_plugin_health(plugin),
        )
        return self.build_snapshot(updated)

    async def retry_onboarding(self, plugin_id: str, *, requested_by: str | None = None) -> dict[str, Any]:
        if self.is_external_host():
            response = self._external_request_json(
                method="POST",
                suffix=f"plugins/{urllib_parse.quote(plugin_id, safe='')}/retry-onboarding",
                payload={"requestedBy": requested_by or "admin"},
            )
            job = response.get("job") if isinstance(response, dict) else None
            return dict(job) if isinstance(job, dict) else {}
        registry = default_plugin_registry()
        plugin = dict((registry.get("plugins") or {}).get(plugin_id) or {})
        if not plugin:
            raise KeyError(plugin_id)
        if str(plugin.get("pluginType") or "").strip().lower() != "channel":
            raise ValueError("当前只支持对渠道插件重新执行首次接入。")
        channel_id = self._channel_login_target(plugin)
        if not channel_id:
            raise ValueError("当前插件未声明 channel 标识，无法重试首次接入。")
        job_id = f"plugin_onboarding_{uuid.uuid4().hex[:12]}"
        job = upsert_install_job(
            job_id,
            {
                "status": "queued",
                "pluginId": plugin_id,
                "pluginTypeHint": str(plugin.get("pluginType") or "").strip() or "channel",
                "installSpec": str(plugin.get("installSpec") or "").strip() or None,
                "installerCommand": f"openclaw channels login --channel {channel_id}",
                "requestedBy": requested_by or "admin",
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
                "managedRoot": str(self.managed_local_root()),
                "pluginRoot": str(self.managed_local_root()),
                "pluginExtensionsRoot": str(self._managed_local_extensions_root()),
                "events": [],
                "userAction": {"urls": [], "qrHints": [], "qrBlocks": [], "instructions": [], "requiresUserAction": False},
                "error": None,
            },
        )
        update_plugin_record(plugin_id, {"setupState": "installed"})
        task = asyncio.create_task(self._run_onboarding_job(job_id, plugin_id))
        self._install_tasks[job_id] = task
        return dict(job)

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        if self.is_external_host():
            response = self._external_request_json(
                method="DELETE",
                suffix=f"plugins/{urllib_parse.quote(plugin_id, safe='')}",
            )
            snapshot = dict(response.get("snapshot") or response)
            snapshot["runtimeConfig"] = self.get_runtime_config()
            return self.build_snapshot(snapshot)
        registry = default_plugin_registry()
        plugins = dict(registry.get("plugins") or {})
        plugin = dict(plugins.get(plugin_id) or {})
        if not plugin:
            raise KeyError(plugin_id)

        install_path = Path(str(plugin.get("installPath") or "")).resolve()
        extensions_root = (self.managed_local_root() / "extensions").resolve()
        if install_path.exists():
            if install_path != extensions_root and extensions_root not in install_path.parents:
                raise RuntimeError(f"插件安装路径不在稳定插件根下，拒绝删除：{install_path}")
            shutil.rmtree(install_path, ignore_errors=False)

        jobs = dict(registry.get("installJobs") or {})
        install_spec = str(plugin.get("installSpec") or "").strip()
        for job_id, job in list(jobs.items()):
            if not isinstance(job, dict):
                continue
            same_plugin = str(job.get("pluginId") or "").strip() == plugin_id
            same_spec = bool(install_spec) and str(job.get("installSpec") or "").strip() == install_spec
            if not same_plugin and not same_spec:
                continue
            task = self._install_tasks.pop(job_id, None)
            if task and not task.done():
                task.cancel()
            log_path = Path(str(job.get("logPath") or "")).expanduser()
            if log_path.is_file():
                log_path.unlink(missing_ok=True)
            jobs.pop(job_id, None)

        plugins.pop(plugin_id, None)
        registry["plugins"] = plugins
        registry["installJobs"] = jobs
        save_plugin_registry(registry)
        return self.rescan()

    def default_channel_type(self) -> str | None:
        if not self.is_enabled() or not self.family_allowed("channel"):
            return None
        if self.is_external_host():
            return None
        return default_channel_type()

    def normalize_inbound(
        self,
        *,
        source: str,
        chat_type: str,
        remote_id: str,
        text_content: str,
        sender_id: str | None = None,
        sender_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("当前 PluginHostRuntime 已关闭，暂不接管渠道入站。")
        if not self.family_allowed("channel"):
            raise RuntimeError("当前 PluginHostRuntime 未允许 channel 家族，暂不接管渠道入站。")
        if self.is_external_host():
            raise RuntimeError("当前 PluginHostRuntime 处于 external host 模式，渠道入站应由外部宿主接管。")
        return normalize_inbound_message(
            source=source,
            chat_type=chat_type,
            remote_id=remote_id,
            text_content=text_content,
            sender_id=sender_id,
            sender_name=sender_name,
            metadata=metadata,
        )

    async def create_install_job(
        self,
        *,
        install_spec: str | None = None,
        installer_command: str | None = None,
        plugin_type_hint: str | None = None,
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("当前 PluginHostRuntime 已关闭，无法创建安装任务。")
        if self.is_external_host():
            response = self._external_request_json(
                method="POST",
                suffix="install",
                payload={
                    "installSpec": install_spec,
                    "installerCommand": installer_command,
                    "pluginTypeHint": plugin_type_hint,
                    "requestedBy": requested_by or "admin",
                },
            )
            job = response.get("job") if isinstance(response, dict) else None
            return dict(job) if isinstance(job, dict) else {}
        job_id = f"plugin_install_{uuid.uuid4().hex[:12]}"
        command = str(installer_command or "").strip()
        normalized_spec = str(self._infer_install_spec(install_spec=install_spec, installer_command=command) or "").strip()
        if not command and not normalized_spec:
            raise ValueError("缺少安装命令或插件 spec")
        if not command:
            quoted_spec = shlex.quote(normalized_spec)
            command = f"openclaw plugins install {quoted_spec}"

        job = upsert_install_job(
            job_id,
            {
                "status": "queued",
                "pluginId": None,
                "pluginTypeHint": str(plugin_type_hint or "").strip() or None,
                "installSpec": normalized_spec or None,
                "installerCommand": command,
                "requestedBy": requested_by or "admin",
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
                "managedRoot": str(self.managed_local_root()),
                "pluginRoot": str(self.managed_local_root()),
                "pluginExtensionsRoot": str(self._managed_local_extensions_root()),
                "events": [],
                "userAction": {"urls": [], "qrHints": [], "instructions": [], "requiresUserAction": False},
                "error": None,
            },
        )
        task = asyncio.create_task(self._run_install_job(job_id))
        self._install_tasks[job_id] = task
        return dict(job)

    async def _run_install_job(self, job_id: str) -> None:
        job = self.get_install_job(job_id)
        if not job:
            return
        command = str(job.get("installerCommand") or "").strip()
        log_path = PLUGIN_INSTALL_LOG_ROOT / f"{job_id}.log"
        known_plugin_ids = {
            str(plugin_id).strip()
            for plugin_id in ((default_plugin_registry().get("plugins") or {}).keys())
            if str(plugin_id).strip()
        }
        env = self._managed_local_env()

        def _append_event(kind: str, content: str) -> None:
            current = self.get_install_job(job_id) or {}
            events = list(current.get("events") or [])
            events.append({"ts": _now_iso(), "kind": kind, "content": content})
            events = events[-200:]
            onboarding = detect_onboarding_hints([item.get("content") or "" for item in events])
            upsert_install_job(
                job_id,
                {
                    "status": current.get("status") or "running",
                    "events": events,
                    "userAction": onboarding,
                    "updatedAt": _now_iso(),
                },
            )
            with open(log_path, "a", encoding="utf-8", newline="\n") as log_file:
                log_file.write(f"[{kind}] {content}\n")

        upsert_install_job(job_id, {"status": "running", "startedAt": _now_iso(), "updatedAt": _now_iso(), "logPath": str(log_path)})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        try:
            env = await self._ensure_openclaw_cli(env, append_event=_append_event, installer_command=command)
            with open(log_path, "a", encoding="utf-8", newline="\n") as log_file:
                log_file.write(f"[system] command={command}\n")
                log_file.write(f"[system] cwd={self.managed_local_root()}\n")
                log_file.write(f"[system] OPENCLAW_STATE_DIR={env['OPENCLAW_STATE_DIR']}\n")
                log_file.write(f"[system] PATH={env.get('PATH', '')}\n")

            process = await self._start_install_process(command, cwd=str(self.managed_local_root()), env=env)
            upsert_install_job(job_id, {"pid": process.pid, "updatedAt": _now_iso()})
            _append_event("system", f"installer process started (pid={process.pid})")

            async def _pump(stream, kind: str) -> None:
                while True:
                    line = await self._read_process_line(stream)
                    if not line:
                        break
                    _append_event(kind, self._decode_process_line(line))

            await asyncio.gather(_pump(process.stdout, "stdout"), _pump(process.stderr, "stderr"))
            returncode = await self._wait_process(process)
            _append_event("system", f"installer process exited with code {returncode}")
            registry = scan_plugin_registry()
            install_spec = str(job.get("installSpec") or "").strip()
            plugin_type_hint = str(job.get("pluginTypeHint") or "").strip() or None
            plugin_id = self._resolve_install_job_plugin_id(
                before_plugin_ids=known_plugin_ids,
                install_spec=install_spec,
                plugin_type_hint=plugin_type_hint,
                registry=registry,
            )
            installed_plugin = self._resolve_installed_plugin(registry=registry, plugin_id=plugin_id)
            final_status = "completed" if returncode == 0 else "failed"
            current = self.get_install_job(job_id) or {}
            user_action = current.get("userAction") or {}
            if returncode == 0 and installed_plugin:
                try:
                    await self._bridge_installed_plugin(plugin=installed_plugin, append_event=_append_event)
                    registry = scan_plugin_registry()
                    installed_plugin = self._resolve_installed_plugin(registry=registry, plugin_id=plugin_id)
                except Exception as exc:
                    _append_event("stderr", str(exc))
                    final_status = "failed"
            if returncode == 0 and installed_plugin and str(installed_plugin.get("pluginType") or "").strip().lower() == "channel":
                channel_id = self._channel_login_target(installed_plugin)
                if channel_id:
                    self._require_managed_local_gateway_ready(purpose="执行渠道接入流程")
                    onboarding_result = await self._run_channel_onboarding(env=env, channel_id=channel_id, append_event=_append_event)
                    if onboarding_result.get("status") == "needs_user_action":
                        final_status = "needs_user_action"
                    elif onboarding_result.get("status") == "failed":
                        final_status = "failed"
                    current = self.get_install_job(job_id) or {}
                    user_action = current.get("userAction") or {}
            if final_status == "completed" and user_action.get("requiresUserAction"):
                final_status = "needs_user_action"
            upsert_install_job(
                job_id,
                {
                    "status": final_status,
                    "pluginId": plugin_id,
                    "finishedAt": _now_iso(),
                    "updatedAt": _now_iso(),
                    "returnCode": returncode,
                    "error": None if returncode == 0 else f"installer exited with code {returncode}",
                },
            )
            if plugin_id and final_status == "completed":
                update_plugin_record(plugin_id, {"setupState": "onboarded", "activationState": "active"})
            elif plugin_id and final_status == "needs_user_action":
                update_plugin_record(plugin_id, {"setupState": "needs_user_action"})
            elif plugin_id and final_status == "failed":
                update_plugin_record(plugin_id, {"setupState": "failed"})
        except asyncio.CancelledError:
            _append_event("system", "installation cancelled")
            upsert_install_job(job_id, {"status": "failed", "error": "installation cancelled", "finishedAt": _now_iso(), "updatedAt": _now_iso()})
            raise
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            _append_event("stderr", message)
            upsert_install_job(job_id, {"status": "failed", "error": message, "finishedAt": _now_iso(), "updatedAt": _now_iso()})
        finally:
            self._install_tasks.pop(job_id, None)

    async def _run_onboarding_job(self, job_id: str, plugin_id: str) -> None:
        log_path = PLUGIN_INSTALL_LOG_ROOT / f"{job_id}.log"
        env = self._managed_local_env()

        def _append_event(kind: str, content: str) -> None:
            current = self.get_install_job(job_id) or {}
            events = list(current.get("events") or [])
            events.append({"ts": _now_iso(), "kind": kind, "content": content})
            events = events[-200:]
            onboarding = detect_onboarding_hints([item.get("content") or "" for item in events])
            upsert_install_job(
                job_id,
                {
                    "status": current.get("status") or "running",
                    "events": events,
                    "userAction": onboarding,
                    "updatedAt": _now_iso(),
                },
            )
            with open(log_path, "a", encoding="utf-8", newline="\n") as log_file:
                log_file.write(f"[{kind}] {content}\n")

        upsert_install_job(job_id, {"status": "running", "startedAt": _now_iso(), "updatedAt": _now_iso(), "logPath": str(log_path)})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")

        try:
            env = await self._ensure_openclaw_cli(env, append_event=_append_event, installer_command=f"openclaw channels login --channel {plugin_id}")
            registry = scan_plugin_registry()
            plugin = self._resolve_installed_plugin(registry=registry, plugin_id=plugin_id)
            if not plugin:
                raise RuntimeError(f"插件不存在或已被移除：{plugin_id}")
            await self._bridge_installed_plugin(plugin=plugin, append_event=_append_event)
            registry = scan_plugin_registry()
            plugin = self._resolve_installed_plugin(registry=registry, plugin_id=plugin_id)
            channel_id = self._channel_login_target(plugin)
            if not channel_id:
                raise RuntimeError("当前插件未声明 channel 标识，无法执行首次接入。")
            self._require_managed_local_gateway_ready(purpose="执行首次接入")
            onboarding_result = await self._run_channel_onboarding(env=env, channel_id=channel_id, append_event=_append_event)
            final_status = str(onboarding_result.get("status") or "failed")
            upsert_install_job(
                job_id,
                {
                    "status": final_status,
                    "pluginId": plugin_id,
                    "finishedAt": _now_iso(),
                    "updatedAt": _now_iso(),
                    "returnCode": onboarding_result.get("returnCode"),
                    "error": None if final_status in {"completed", "needs_user_action"} else "onboarding failed",
                },
            )
            if final_status == "completed":
                update_plugin_record(plugin_id, {"setupState": "onboarded", "activationState": "active"})
            elif final_status == "needs_user_action":
                update_plugin_record(plugin_id, {"setupState": "needs_user_action"})
            else:
                update_plugin_record(plugin_id, {"setupState": "failed"})
        except asyncio.CancelledError:
            _append_event("system", "onboarding cancelled")
            upsert_install_job(job_id, {"status": "failed", "error": "onboarding cancelled", "finishedAt": _now_iso(), "updatedAt": _now_iso()})
            raise
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            _append_event("stderr", message)
            upsert_install_job(job_id, {"status": "failed", "error": message, "finishedAt": _now_iso(), "updatedAt": _now_iso()})
            update_plugin_record(plugin_id, {"setupState": "failed"})
        finally:
            self._install_tasks.pop(job_id, None)

    async def broadcast_text(
        self,
        *,
        channel_type: str,
        receive_id: str,
        text: str,
        account_id: str | None = None,
        reply_to_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("当前 PluginHostRuntime 已关闭，暂不接管渠道出站。")
        if not self.family_allowed("channel"):
            raise RuntimeError("当前 PluginHostRuntime 未允许 channel 家族，暂不接管渠道出站。")
        if self.is_external_host():
            external = self.external_host_config()
            if not external.get("gatewayBaseUrl"):
                raise RuntimeError("当前 external host 未配置 gatewayBaseUrl，仍处于 control-plane only，无法执行渠道出站。")
            response = self._external_request_json(
                method="POST",
                suffix="send",
                payload={
                    "channelType": channel_type,
                    "receiveId": receive_id,
                    "text": text,
                    "accountId": account_id,
                    "replyToId": reply_to_id,
                    "threadId": thread_id,
                },
            )
            return dict(response.get("receipt") or {})
        self._require_managed_local_gateway_ready(purpose="执行渠道出站")
        try:
            return await self._broadcast_via_gateway_message(
                channel_type=channel_type,
                receive_id=receive_id,
                text=text,
                account_id=account_id,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
            )
        except Exception as exc:
            if not self._bridge_error_allows_cli_fallback(exc):
                raise
            receipt = await outbound_broadcast_text(
                channel_type=channel_type,
                receive_id=receive_id,
                text=text,
                managed_root=self.managed_local_root(),
                managed_tooling_root=self.managed_local_tooling_root(),
                account_id=account_id,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
            )
            receipt["deliveryPath"] = "cli_fallback"
            receipt["gatewayFallbackReason"] = str(exc).strip() or exc.__class__.__name__
            repair = self._heal_managed_local_host_after_cli_fallback()
            if repair.get("changed"):
                receipt["hostConfigRecovered"] = True
            if repair.get("error"):
                receipt["hostConfigRecoveryError"] = str(repair.get("error") or "").strip() or None
            return receipt

    async def broadcast_media(
        self,
        *,
        channel_type: str,
        receive_id: str,
        media_url: str,
        text: str | None = None,
        account_id: str | None = None,
        reply_to_id: str | None = None,
        thread_id: str | None = None,
        tts_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("当前 PluginHostRuntime 已关闭，暂不接管渠道媒体出站。")
        if not self.family_allowed("channel"):
            raise RuntimeError("当前 PluginHostRuntime 未允许 channel 家族，暂不接管渠道媒体出站。")
        normalized_media_url = str(media_url or "").strip()
        if not normalized_media_url:
            raise RuntimeError("当前渠道媒体出站缺少 mediaUrl / filePath。")
        tts_payload = dict(tts_meta or {})
        local_path = normalized_media_url if Path(normalized_media_url).expanduser().exists() else None
        voice_mode = str(tts_payload.get("voiceMode") or tts_payload.get("deliveryMode") or "").strip().lower()
        target_container = str(tts_payload.get("container") or "").strip().lower()
        try:
            playtime_ms = int(tts_payload.get("playtimeMs")) if tts_payload.get("playtimeMs") not in (None, "") else None
        except Exception:
            playtime_ms = None
        try:
            sample_rate = int(tts_payload.get("sampleRate")) if tts_payload.get("sampleRate") not in (None, "") else None
        except Exception:
            sample_rate = None
        try:
            bits_per_sample = int(tts_payload.get("bitsPerSample")) if tts_payload.get("bitsPerSample") not in (None, "") else None
        except Exception:
            bits_per_sample = None
        try:
            encode_type = int(tts_payload.get("encodeType")) if tts_payload.get("encodeType") not in (None, "") else None
        except Exception:
            encode_type = None
        bridge_voice_delivery = bool(
            tts_payload.get("asVoice")
            or voice_mode in {"voice_note", "native_voice"}
            or target_container in {"ogg", "silk", "tencent_silk_v3"}
        )
        staged_manifest: dict[str, Any] | None = None
        staged_asset: dict[str, Any] | None = None
        staged_media_url = normalized_media_url
        if self.is_external_host():
            external = self.external_host_config()
            if not external.get("gatewayBaseUrl"):
                raise RuntimeError("当前 external host 未配置 gatewayBaseUrl，仍处于 control-plane only，无法执行渠道媒体出站。")
            response = self._external_request_json(
                method="POST",
                suffix="send",
                payload={
                    "channelType": channel_type,
                    "receiveId": receive_id,
                    "text": str(text or "").strip() or None,
                    "mediaUrl": normalized_media_url,
                    "accountId": account_id,
                    "replyToId": reply_to_id,
                    "threadId": thread_id,
                    "playtimeMs": playtime_ms,
                    "sampleRate": sample_rate,
                    "bitsPerSample": bits_per_sample,
                    "encodeType": encode_type,
                },
            )
            receipt = dict(response.get("receipt") or {})
            try:
                asset = self.materialize_outbound_asset(
                    source_path=local_path,
                    source_url=None if local_path else normalized_media_url,
                    delivery_mode=str((tts_meta or {}).get("deliveryMode") or "attachment"),
                    asset_kind=str((tts_meta or {}).get("assetKind") or "").strip() or None,
                    tts_meta=tts_meta,
                )
            except Exception as exc:
                asset = None
                receipt["mediaAssetError"] = str(exc).strip() or exc.__class__.__name__
            if asset:
                receipt["mediaAsset"] = asset
            return receipt
        self._require_managed_local_gateway_ready(purpose="执行渠道媒体出站")
        gateway_tts_payload = dict(tts_payload)
        if playtime_ms is not None:
            gateway_tts_payload["playtimeMs"] = playtime_ms
        if sample_rate is not None:
            gateway_tts_payload["sampleRate"] = sample_rate
        if bits_per_sample is not None:
            gateway_tts_payload["bitsPerSample"] = bits_per_sample
        if encode_type is not None:
            gateway_tts_payload["encodeType"] = encode_type
        if bridge_voice_delivery:
            gateway_tts_payload["asVoice"] = True
        try:
            staged_manifest = self.materialize_outbound_assets(
                source_path=local_path,
                source_url=None if local_path else normalized_media_url,
                delivery_mode=str((tts_meta or {}).get("deliveryMode") or "attachment"),
                asset_kind=str((tts_meta or {}).get("assetKind") or "").strip() or None,
                tts_meta=tts_meta,
                record_state=False,
            )
            staged_asset = self._first_asset_from_manifest(staged_manifest)
            staged_media_url = str((staged_asset or {}).get("workspacePath") or "").strip() or normalized_media_url
        except Exception as exc:
            raise RuntimeError(f"渠道媒体暂存失败：{str(exc).strip() or exc.__class__.__name__}") from exc
        try:
            receipt = await self._broadcast_via_gateway_message(
                channel_type=channel_type,
                receive_id=receive_id,
                text=str(text or "").strip() or None,
                media_url=staged_media_url,
                account_id=account_id,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
                tts_payload=gateway_tts_payload,
            )
        except Exception as exc:
            if not self._bridge_error_allows_cli_fallback(exc):
                raise
            receipt = await outbound_broadcast_media(
                channel_type=channel_type,
                receive_id=receive_id,
                media_url=staged_media_url,
                text=str(text or "").strip() or None,
                managed_root=self.managed_local_root(),
                managed_tooling_root=self.managed_local_tooling_root(),
                account_id=account_id,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
            )
            receipt["deliveryPath"] = "cli_fallback"
            receipt["gatewayFallbackReason"] = str(exc).strip() or exc.__class__.__name__
            repair = self._heal_managed_local_host_after_cli_fallback()
            if repair.get("changed"):
                receipt["hostConfigRecovered"] = True
            if repair.get("error"):
                receipt["hostConfigRecoveryError"] = str(repair.get("error") or "").strip() or None
        if staged_manifest or tts_meta is not None:
            self._record_asset_state(
                direction="outbound",
                asset=staged_asset,
                message_assets=staged_manifest,
                tts_meta=tts_meta,
            )
        if staged_asset:
            receipt["mediaAsset"] = staged_asset
        return receipt

    def default_target_for(self, channel_type: str) -> str | None:
        if not self.is_enabled() or not self.family_allowed("channel"):
            return None
        if self.is_external_host():
            return None
        return default_target_for(channel_type)


plugin_host_service = PluginHostService()
