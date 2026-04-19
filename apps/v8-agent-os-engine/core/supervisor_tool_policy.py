from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.computer_use_tool_surface import (
    DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES,
    select_supervisor_native_tools,
)

FALLBACK_NATIVE_TOOL_NAMES = {
    "run_system_command",
    "command_session_broker",
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
    "share_workspace_file",
    "write_native_file",
    "grep_search",
    "download_media_for_vision",
    "web_broker",
    "http_request",
    "s3_broker",
    "wait",
    "list_processes",
    "manage_process",
    "manage_cron",
    "manage_hook",
    "read_audit_log",
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


def _native_tool_definitions() -> list[SupervisorToolDefinition]:
    from erc.capability_registry import capability_registry
    runtime_defs = _runtime_managed_definitions()
    try:
        from core.native_tools import NATIVE_TOOLS

        filtered_native_tools = capability_registry.filter_direct_tools(NATIVE_TOOLS)
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
            if _matches_runtime_managed_tool(tool_name, runtime_defs):
                continue
            definitions.append(
                SupervisorToolDefinition(
                    name=tool_name,
                    description=str(getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or "").strip()
                    or "系统默认可用工具。",
                    reason="系统默认能力，主理人始终可用。",
                )
            )
        return definitions
    except Exception:
        filtered_names = capability_registry.filter_direct_tools(
            [_FallbackToolRef(name=item) for item in sorted(FALLBACK_NATIVE_TOOL_NAMES)]
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
            if _matches_runtime_managed_tool(normalized, runtime_defs):
                continue
            definitions.append(
                SupervisorToolDefinition(
                    name=normalized,
                    description="系统默认可用工具。",
                    reason="系统默认能力，主理人始终可用。",
                )
            )
        return definitions


def _ensure_runtime_managed_descriptors_loaded() -> None:
    try:
        from erc.runtime_registry import runtime_registry

        existing_kinds = {descriptor.kind for descriptor in runtime_registry.list_descriptors()}
        if {"computer_use", "rpa"}.issubset(existing_kinds):
            return
        from runtimes.chat.runtime import chat_runtime  # noqa: F401
        from runtimes.memory.runtime import memory_runtime  # noqa: F401
        from runtimes.automation.runtime import automation_runtime  # noqa: F401
        from runtimes.network_supervisor.runtime import network_supervisor_runtime  # noqa: F401
        from runtimes.plugin_host.runtime import plugin_host_runtime  # noqa: F401
        from runtimes.computer_use.runtime import computer_use_runtime  # noqa: F401
        from runtimes.rpa.runtime import rpa_runtime  # noqa: F401
    except Exception:
        return


def _runtime_managed_definitions() -> list[SupervisorToolDefinition]:
    from erc.capability_registry import capability_registry

    _ensure_runtime_managed_descriptors_loaded()
    definitions: list[SupervisorToolDefinition] = []
    for descriptor in capability_registry.list():
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


def sanitize_supervisor_allowed_tools(raw_allowed_tools: Any) -> list[str] | None:
    raw_names = [str(item).strip() for item in list(raw_allowed_tools or []) if str(item).strip()]
    if not raw_names:
        return None
    native_names = {item.name for item in _native_tool_definitions()}
    runtime_defs = _runtime_managed_definitions()
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


def build_supervisor_tool_policy_snapshot(raw_allowed_tools: Any) -> dict[str, Any]:
    native_defs = _native_tool_definitions()
    runtime_defs = _runtime_managed_definitions()
    sanitized = sanitize_supervisor_allowed_tools(raw_allowed_tools)

    return {
        "allowedTools": sanitized,
        "lockedNativeTools": [item.as_dict() for item in native_defs],
        "runtimeManagedTools": [item.as_dict() for item in runtime_defs],
    }
