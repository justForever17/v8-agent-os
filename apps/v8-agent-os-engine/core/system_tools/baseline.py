from __future__ import annotations

from importlib import import_module
from typing import Any, Iterable

BASELINE_SYSTEM_TOOL_NAME_ORDER = (
    "read_native_file",
    "write_native_file",
    "grep_search",
    "run_system_command",
    "command_session_broker",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
    "web_broker",
    "http_request",
    "download_media_for_vision",
    "vision_media_analyzer",
    "fetch_skill_instructions",
    "tool_observation_detail",
    "wait",
)

BASELINE_SYSTEM_TOOL_NAMES = set(BASELINE_SYSTEM_TOOL_NAME_ORDER)


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


def build_baseline_system_tool_descriptors() -> list[dict[str, str]]:
    descriptors: list[dict[str, str]] = []
    native_tools_module = None
    try:
        native_tools_module = import_module("core.native_tools")
    except Exception:
        native_tools_module = None

    for name in BASELINE_SYSTEM_TOOL_NAME_ORDER:
        description = ""
        if native_tools_module is not None:
            tool_ref = getattr(native_tools_module, name, None)
            description = str(getattr(tool_ref, "description", "") or "").strip()
        descriptors.append(
            {
                "name": name,
                "description": description,
            }
        )
    return descriptors
