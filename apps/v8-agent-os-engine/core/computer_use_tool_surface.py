from __future__ import annotations

from core.system_tools.baseline import is_baseline_system_tool_name

DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES = {
    "computer_use_capture_screenshot",
    "computer_use_click",
    "computer_use_click_toolbar_action",
    "computer_use_execute_plan",
    "computer_use_find_and_type",
    "computer_use_find_element",
    "computer_use_focus_window",
    "computer_use_hotkey",
    "computer_use_list_windows",
    "computer_use_observe",
    "computer_use_open_app",
    "computer_use_scroll",
    "computer_use_scroll_list",
    "computer_use_type_text",
    "computer_use_wait_for_element",
    "execute_system_command",
    "run_system_command",
    "start_background_command",
    "http_request",
    "list_processes",
    "manage_cron",
    "manage_hook",
    "manage_process",
    "memory_map",
    "mem_summary",
    "read_audit_log",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
    "write_native_file",
}

SUPERVISOR_HIGH_LEVEL_COMPUTER_USE_TOOLS = {
    "computer_use_list_apps",
    "computer_use_list_primitives",
    "computer_use_desktop_capabilities",
    "computer_use_lookup_muscle_memory",
    "computer_use_list_muscle_memories",
    "computer_use_resolve_execution_route",
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
}

SUPERVISOR_LOW_LEVEL_COMPUTER_USE_TOOLS = {
    "computer_use_capture_screenshot",
    "computer_use_click",
    "computer_use_click_toolbar_action",
    "computer_use_execute_plan",
    "computer_use_find_and_type",
    "computer_use_find_element",
    "computer_use_focus_window",
    "computer_use_hotkey",
    "computer_use_list_windows",
    "computer_use_observe",
    "computer_use_open_app",
    "computer_use_scroll",
    "computer_use_scroll_list",
    "computer_use_type_text",
    "computer_use_wait_for_element",
}

SUPERVISOR_ALLOW_LOW_LEVEL_COMPUTER_USE_MARKER = "computer_use_allow_low_level"


def normalize_supervisor_native_allowlist(
    *,
    supervisor_allowed_tools,
    config_allowed_tools,
):
    normalized = {
        str(name).strip()
        for name in list(supervisor_allowed_tools or config_allowed_tools or [])
        if str(name).strip()
    }
    low_level_requested = bool(normalized & SUPERVISOR_LOW_LEVEL_COMPUTER_USE_TOOLS)
    allow_low_level = SUPERVISOR_ALLOW_LOW_LEVEL_COMPUTER_USE_MARKER in normalized
    if low_level_requested:
        normalized.update(SUPERVISOR_HIGH_LEVEL_COMPUTER_USE_TOOLS)
        if not allow_low_level:
            normalized.difference_update(SUPERVISOR_LOW_LEVEL_COMPUTER_USE_TOOLS)
    normalized.discard(SUPERVISOR_ALLOW_LOW_LEVEL_COMPUTER_USE_MARKER)
    return normalized, allow_low_level


def select_supervisor_native_tools(
    *,
    filtered_native_tools,
    supervisor_allowed_tools,
    config_allowed_tools,
):
    explicit_allowlist, allow_low_level = normalize_supervisor_native_allowlist(
        supervisor_allowed_tools=supervisor_allowed_tools,
        config_allowed_tools=config_allowed_tools,
    )

    selected = []
    for tool_ref in filtered_native_tools:
        tool_name = getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")).strip()
        if not tool_name:
            selected.append(tool_ref)
            continue
        if is_baseline_system_tool_name(tool_name):
            selected.append(tool_ref)
            continue
        if tool_name in SUPERVISOR_LOW_LEVEL_COMPUTER_USE_TOOLS and not allow_low_level:
            continue
        if tool_name in DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES and tool_name not in explicit_allowlist:
            continue
        selected.append(tool_ref)
    return selected
