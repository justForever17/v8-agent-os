from __future__ import annotations

from typing import Any, Iterable

BASELINE_SYSTEM_TOOL_NAMES = {
    "ask_user",
    "read_native_file",
    "share_workspace_file",
    "write_native_file",
    "grep_search",
    "run_system_command",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
    "download_media_for_vision",
    "vision_media_analyzer",
    "web_fetch",
    "http_request",
    "s3_upload_file",
    "s3_list_objects",
    "s3_download_file",
}


def is_baseline_system_tool_name(name: str | None) -> bool:
    return str(name or "").strip() in BASELINE_SYSTEM_TOOL_NAMES


def tool_ref_name(tool_ref: Any) -> str:
    return str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip()


def select_baseline_system_tools(tools: Iterable[Any]) -> list[Any]:
    return [tool_ref for tool_ref in tools if is_baseline_system_tool_name(tool_ref_name(tool_ref))]


def select_baseline_system_tool_names(tools: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for tool_ref in select_baseline_system_tools(tools):
        name = tool_ref_name(tool_ref)
        if name and name not in names:
            names.append(name)
    return names
