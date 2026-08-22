from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.computer_use_tool_surface import (
    DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES,
    select_supervisor_native_tools,
)
from core.runtime_tool_access import (
    FEATURE_PACK_GATED_RUNTIME_KINDS,
    runtime_kind_available,
    runtime_kind_for_tool_name,
    runtime_tool_available,
)

FALLBACK_NATIVE_TOOL_NAMES = {
    "delegation_broker",
    "agent_broker",
    "tool_observation_detail",
    "run_system_command",
    "command_session_broker",
    "session_context_broker",
    "session_message_broker",
    "plugin_broker",
    "config_broker",
    "rpa_list_robot_scripts",
    "rpa_run_draft",
    "rpa_run_existing_flow",
    "computer_use_list_apps",
    "computer_use_desktop_capabilities",
    "computer_use_resolve_execution_route",
    "computer_use_observe_scene",
    "computer_use_execute_task",
    "computer_use_list_windows",
    "computer_use_observe",
    "computer_use_find_element",
    "computer_use_click",
    "computer_use_type_text",
    "computer_use_hotkey",
    "computer_use_scroll",
    "computer_use_wait_for_element",
    "computer_use_capture_screenshot",
    "computer_use_open_app",
    "computer_use_focus_window",
    "computer_use_find_and_type",
    "computer_use_scroll_list",
    "computer_use_click_toolbar_action",
    "computer_use_execute_plan",
    "read_native_file",
    "write_native_file",
    "grep_search",
    "download_media_for_vision",
    "web_broker",
    "research_broker",
    "http_request",
    "s3_broker",
    "wait",
    "list_processes",
    "manage_process",
    "manage_cron",
    "manage_hook",
    "read_audit_log",
    "memory_broker",
    "memory_recall",
    "mem_update",
    "memory_map_expand",
    "memory_read_day",
    "ask_user",
    "write_todos",
    "update_todo",
    "vision_media_analyzer",
}


@dataclass(slots=True)
class SupervisorToolDefinition:
    name: str
    description: str
    reason: str = ""
    runtime_kind: str | None = None
    runtime_label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "description": self.description,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.runtime_kind:
            payload["runtimeKind"] = self.runtime_kind
        if self.runtime_label:
            payload["runtimeLabel"] = self.runtime_label
        return payload


@dataclass(slots=True)
class _FallbackToolRef:
    name: str
    description: str = "系统默认可用工具。"


def _runtime_availability_snapshot() -> dict[str, bool]:
    try:
        from core.runtime.startup_profile import installed_runtime_families

        installed = set(installed_runtime_families())
    except Exception:
        installed = set()
    return {
        runtime_kind: runtime_kind in installed
        for runtime_kind in FEATURE_PACK_GATED_RUNTIME_KINDS
    }


def _runtime_kind_is_available(runtime_kind: Any, runtime_availability: dict[str, bool] | None) -> bool:
    normalized = str(runtime_kind or "").strip()
    if runtime_availability is None:
        return runtime_kind_available(normalized)
    return runtime_availability.get(normalized, True)


def _runtime_tool_is_available(tool_name: Any, runtime_availability: dict[str, bool] | None) -> bool:
    if runtime_availability is None:
        return runtime_tool_available(tool_name)
    return runtime_availability.get(runtime_kind_for_tool_name(tool_name), True)


def _native_tool_definitions(
    *,
    runtime_availability: dict[str, bool] | None = None,
    runtime_defs: list[SupervisorToolDefinition] | None = None,
) -> list[SupervisorToolDefinition]:
    from erc.capability_registry import capability_registry
    bound_runtime_defs = (
        runtime_defs
        if runtime_defs is not None
        else _runtime_managed_definitions(runtime_availability=runtime_availability)
    )
    try:
        from core.native_tools import NATIVE_TOOLS

        filtered_native_tools = capability_registry.filter_direct_tools(
            NATIVE_TOOLS,
            runtime_availability=runtime_availability,
        )
        selected_native_tools = select_supervisor_native_tools(
            filtered_native_tools=filtered_native_tools,
            supervisor_allowed_tools=None,
            config_allowed_tools=None,
        )
        definitions: list[SupervisorToolDefinition] = []
        for tool_ref in selected_native_tools:
            tool_name = str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip()
            if not tool_name:
                continue
            if not _runtime_tool_is_available(tool_name, runtime_availability):
                continue
            if _matches_runtime_managed_tool(tool_name, bound_runtime_defs):
                continue
            definitions.append(
                SupervisorToolDefinition(
                    name=tool_name,
                    description=str(getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or "").strip()
                    or "系统默认可用工具。",
                    reason="系统默认能力；实际每轮可见面仍受当前任务边界与治理策略约束。",
                )
            )
        return definitions
    except Exception:
        filtered_names = capability_registry.filter_direct_tools(
            [_FallbackToolRef(name=item) for item in sorted(FALLBACK_NATIVE_TOOL_NAMES)],
            runtime_availability=runtime_availability,
        )
        selected_names = select_supervisor_native_tools(
            filtered_native_tools=filtered_names,
            supervisor_allowed_tools=None,
            config_allowed_tools=None,
        )
        definitions: list[SupervisorToolDefinition] = []
        for tool_ref in selected_names:
            normalized = str(getattr(tool_ref, "name", tool_ref) or "").strip()
            if not normalized:
                continue
            if not _runtime_tool_is_available(normalized, runtime_availability):
                continue
            if _matches_runtime_managed_tool(normalized, bound_runtime_defs):
                continue
            definitions.append(
                SupervisorToolDefinition(
                    name=normalized,
                    description="系统默认可用工具。",
                    reason="系统默认能力；实际每轮可见面仍受当前任务边界与治理策略约束。",
                )
            )
        return definitions


def _ensure_runtime_managed_descriptors_loaded(
    *,
    runtime_availability: dict[str, bool] | None = None,
) -> None:
    try:
        from erc.runtime_registry import runtime_registry

        existing_kinds = {descriptor.kind for descriptor in runtime_registry.list_descriptors()}
        if {"computer_use", "rpa"}.issubset(existing_kinds):
            return
        from runtimes.chat.runtime import chat_runtime  # noqa: F401
        from runtimes.memory.runtime import memory_runtime  # noqa: F401
        from runtimes.automation.runtime import automation_runtime  # noqa: F401
        from runtimes.network_supervisor.runtime import network_supervisor_runtime  # noqa: F401
        from runtimes.plugin_manager.runtime import plugin_manager_service  # noqa: F401
        if _runtime_kind_is_available("computer_use", runtime_availability):
            from runtimes.computer_use.runtime import computer_use_runtime  # noqa: F401
        if _runtime_kind_is_available("rpa", runtime_availability):
            from runtimes.rpa.runtime import rpa_runtime  # noqa: F401
    except Exception:
        return


def _runtime_managed_definitions(
    *,
    runtime_availability: dict[str, bool] | None = None,
) -> list[SupervisorToolDefinition]:
    from erc.capability_registry import capability_registry

    _ensure_runtime_managed_descriptors_loaded(runtime_availability=runtime_availability)
    definitions: list[SupervisorToolDefinition] = []
    for descriptor in capability_registry.list():
        if not _runtime_kind_is_available(descriptor.kind, runtime_availability):
            continue
        metadata = descriptor.metadata or {}
        exact_names = [str(item).strip() for item in list(metadata.get("managedToolNames") or []) if str(item).strip()]
        prefixes = [str(item).strip() for item in list(metadata.get("managedToolPrefixes") or []) if str(item).strip()]

        for tool_name in exact_names:
            definitions.append(
                SupervisorToolDefinition(
                    name=tool_name,
                    description=f"{descriptor.display_name} 默认接管的运行时工具。",
                    reason=f"由 {descriptor.display_name} 统一管理。",
                    runtime_kind=descriptor.kind,
                    runtime_label=descriptor.display_name,
                )
            )
        for raw_prefix in prefixes:
            prefix = raw_prefix[:-1] if raw_prefix.endswith("*") else raw_prefix
            if not prefix:
                continue
            definitions.append(
                SupervisorToolDefinition(
                    name=f"{prefix}*",
                    description=f"{descriptor.display_name} 默认接管的工具前缀。",
                    reason=f"由 {descriptor.display_name} 统一管理。",
                    runtime_kind=descriptor.kind,
                    runtime_label=descriptor.display_name,
                )
            )
    return definitions


def _matches_runtime_managed_tool(tool_name: str, runtime_defs: list[SupervisorToolDefinition]) -> SupervisorToolDefinition | None:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return None
    for runtime_def in runtime_defs:
        if runtime_def.name.endswith("*"):
            prefix = runtime_def.name[:-1]
            if prefix and normalized.startswith(prefix):
                return runtime_def
            continue
        if runtime_def.name == normalized:
            return runtime_def
    return None


def _raw_allowed_tool_names(raw_allowed_tools: Any) -> list[str]:
    return [str(item).strip() for item in list(raw_allowed_tools or []) if str(item).strip()]


def _sanitize_supervisor_allowed_tool_names(
    raw_names: list[str],
    *,
    native_defs: list[SupervisorToolDefinition],
    runtime_defs: list[SupervisorToolDefinition],
) -> list[str] | None:
    native_names = {item.name for item in native_defs}
    sanitized: list[str] = []
    for tool_name in raw_names:
        if tool_name in native_names:
            continue
        if tool_name in DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES:
            continue
        if _matches_runtime_managed_tool(tool_name, runtime_defs):
            continue
        if tool_name not in sanitized:
            sanitized.append(tool_name)
    return sanitized or None


def sanitize_supervisor_allowed_tools(raw_allowed_tools: Any) -> list[str] | None:
    raw_names = _raw_allowed_tool_names(raw_allowed_tools)
    if not raw_names:
        return None
    runtime_availability = _runtime_availability_snapshot()
    runtime_defs = _runtime_managed_definitions(runtime_availability=runtime_availability)
    native_defs = _native_tool_definitions(
        runtime_availability=runtime_availability,
        runtime_defs=runtime_defs,
    )
    return _sanitize_supervisor_allowed_tool_names(
        raw_names,
        native_defs=native_defs,
        runtime_defs=runtime_defs,
    )


def build_supervisor_tool_policy_snapshot(raw_allowed_tools: Any) -> dict[str, Any]:
    runtime_availability = _runtime_availability_snapshot()
    runtime_defs = _runtime_managed_definitions(runtime_availability=runtime_availability)
    native_defs = _native_tool_definitions(
        runtime_availability=runtime_availability,
        runtime_defs=runtime_defs,
    )
    raw_names = _raw_allowed_tool_names(raw_allowed_tools)
    sanitized = (
        _sanitize_supervisor_allowed_tool_names(
            raw_names,
            native_defs=native_defs,
            runtime_defs=runtime_defs,
        )
        if raw_names
        else None
    )

    return {
        "allowedTools": sanitized,
        "lockedNativeTools": [item.as_dict() for item in native_defs],
        "runtimeManagedTools": [item.as_dict() for item in runtime_defs],
    }
