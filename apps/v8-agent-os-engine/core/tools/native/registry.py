from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NativeToolRegistration:
    name: str
    family: str


_TOOL_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "system_session",
        (
            "run_system_command",
            "command_session_broker",
        ),
    ),
    (
        "runtime",
        (
            "runtime_broker",
            "delegation_broker",
            "request_peer_help",
        ),
    ),
    ("conversation_history", ("session_context_broker",)),
    ("extensions", ("mcp_server_config", "plugin_broker")),
    ("spec", ("spec_broker",)),
    (
        "system_session",
        (
            "read_background_output",
            "send_background_input",
            "terminate_background_command",
        ),
    ),
    (
        "rpa",
        (
            "rpa_list_robot_scripts",
            "rpa_run_draft",
            "rpa_run_existing_flow",
        ),
    ),
    (
        "creative_media",
        (
            "creative_media_capabilities",
            "creative_media_plan",
            "creative_media_assets",
            "creative_media_jobs",
            "creative_media_edit",
            "creative_media_quality",
        ),
    ),
    (
        "computer_use",
        (
            "computer_use_list_apps",
            "computer_use_list_primitives",
            "computer_use_desktop_capabilities",
            "computer_use_lookup_muscle_memory",
            "computer_use_list_muscle_memories",
            "computer_use_resolve_execution_route",
            "computer_use_execute_task",
            "computer_use_launch_app",
            "computer_use_ensure_window",
            "computer_use_observe_scene",
            "computer_use_click_target",
            "computer_use_input_text",
            "computer_use_paste_text",
            "computer_use_paste_files",
            "computer_use_right_click_target",
            "computer_use_hover_target",
            "computer_use_send_hotkey",
            "computer_use_scroll_view",
            "computer_use_drag_pointer",
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
        ),
    ),
    (
        "workspace_file",
        (
            "read_native_file",
            "share_workspace_file",
            "workspace_broker",
            "write_native_file",
            "grep_search",
            "download_media_for_vision",
        ),
    ),
    (
        "web_research",
        (
            "web_broker",
            "web_fetch",
            "web_read",
            "web_extract",
            "web_search",
            "research_broker",
            "delegate_network_task",
            "http_request",
        ),
    ),
    ("network_supervisor", ("network_neighbor_broker",)),
    (
        "workspace_file",
        (
            "s3_broker",
            "s3_upload_file",
            "s3_list_objects",
            "s3_download_file",
        ),
    ),
    (
        "automation",
        (
            "wait",
            "list_processes",
            "manage_process",
            "manage_cron",
            "manage_hook",
            "read_audit_log",
        ),
    ),
    ("detail", ("tool_observation_detail",)),
    (
        "memory",
        (
            "memory_broker",
            "memory_recall",
            "mem_delete",
            "mem_update",
            "mem_summary",
            "memory_map",
            "memory_map_expand",
            "memory_read_day",
        ),
    ),
    (
        "governance",
        (
            "ask_user",
            "write_todos",
            "update_todo",
        ),
    ),
    ("vision", ("vision_media_analyzer",)),
)


def _registrations() -> tuple[NativeToolRegistration, ...]:
    registrations: list[NativeToolRegistration] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for family, names in _TOOL_FAMILIES:
        normalized_family = str(family or "").strip()
        if not normalized_family:
            raise ValueError("native tool registry family cannot be empty")
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name:
                raise ValueError(f"native tool registry contains an empty tool name in family {normalized_family!r}")
            if name in seen:
                duplicates.append(name)
            seen.add(name)
            registrations.append(NativeToolRegistration(name=name, family=normalized_family))
    if duplicates:
        raise ValueError(f"native tool registry contains duplicate tool names: {', '.join(sorted(set(duplicates)))}")
    return tuple(registrations)


NATIVE_TOOL_REGISTRATIONS: tuple[NativeToolRegistration, ...] = _registrations()
NATIVE_TOOL_NAMES: tuple[str, ...] = tuple(item.name for item in NATIVE_TOOL_REGISTRATIONS)
_FAMILY_BY_NAME: dict[str, str] = {item.name: item.family for item in NATIVE_TOOL_REGISTRATIONS}


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", getattr(tool, "__name__", "")) or "").strip()


def native_tool_names() -> list[str]:
    return list(NATIVE_TOOL_NAMES)


def native_tool_family_for_name(name: str) -> str | None:
    return _FAMILY_BY_NAME.get(str(name or "").strip())


def build_native_tools(namespace: dict[str, Any]) -> list[Any]:
    if not isinstance(namespace, dict):
        raise TypeError("build_native_tools requires the defining module globals() namespace")

    tools: list[Any] = []
    actual_names: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    mismatched: list[str] = []
    duplicates: list[str] = []
    seen_actual: set[str] = set()

    for expected_name in NATIVE_TOOL_NAMES:
        if expected_name not in namespace:
            missing.append(expected_name)
            continue
        tool = namespace[expected_name]
        actual_name = _tool_name(tool)
        if not actual_name:
            invalid.append(expected_name)
            continue
        if actual_name != expected_name:
            mismatched.append(f"{expected_name}->{actual_name}")
        if actual_name in seen_actual:
            duplicates.append(actual_name)
        seen_actual.add(actual_name)
        actual_names.append(actual_name)
        tools.append(tool)

    errors: list[str] = []
    if missing:
        errors.append("missing=" + ",".join(missing))
    if invalid:
        errors.append("invalid=" + ",".join(invalid))
    if mismatched:
        errors.append("mismatched=" + ",".join(mismatched))
    if duplicates:
        errors.append("duplicates=" + ",".join(sorted(set(duplicates))))
    if actual_names != list(NATIVE_TOOL_NAMES):
        errors.append("order_or_name_mismatch")
    if errors:
        raise ValueError("native tool registry validation failed: " + "; ".join(errors))
    return tools


def native_tool_manifest(namespace: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    actual_names: dict[str, str] = {}
    if namespace is not None:
        for expected_name in NATIVE_TOOL_NAMES:
            tool = namespace.get(expected_name) if isinstance(namespace, dict) else None
            actual_names[expected_name] = _tool_name(tool)
    return [
        {
            "name": item.name,
            "family": item.family,
            **({"actualName": actual_names.get(item.name)} if namespace is not None else {}),
        }
        for item in NATIVE_TOOL_REGISTRATIONS
    ]
