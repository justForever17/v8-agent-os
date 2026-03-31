from __future__ import annotations

from typing import Iterable, List, Set


_BRIDGE_KEYWORD_BY_USE = {
    "open_app": "Open App",
    "focus_window": "Focus Window",
    "find_and_type": "Find And Type",
    "scroll_list": "Scroll List",
    "click_toolbar_action": "Click Toolbar Action",
    "right_click": "Right Click",
    "hover": "Hover",
    "drag": "Drag",
    "page_scroll": "Page Scroll",
    "hotkey": "Hotkey",
    "wait_for_element": "Wait For Element",
    "capture_screenshot": "Capture Screenshot",
    "execute_plan": "Execute Plan",
    "observe": "Observe",
    "observe_desktop": "Observe Desktop",
    "click": "Click",
    "double_click": "Double Click",
    "type_text": "Type Text",
    "scroll": "Scroll",
    "wait": "Wait",
}


def keyword_name_for_use(use: str) -> str:
    normalized = str(use or "").strip().lower()
    if normalized in _BRIDGE_KEYWORD_BY_USE:
        return _BRIDGE_KEYWORD_BY_USE[normalized]
    return " ".join(part.capitalize() for part in normalized.split("_") if part)


def supported_bridge_keywords() -> Set[str]:
    return set(_BRIDGE_KEYWORD_BY_USE.values())


def supported_bridge_uses() -> Set[str]:
    return set(_BRIDGE_KEYWORD_BY_USE.keys())


def is_supported_bridge_use(use: str) -> bool:
    return str(use or "").strip().lower() in supported_bridge_uses()


def bridge_keyword_issues(step_uses: Iterable[str]) -> List[str]:
    issues: List[str] = []
    for use in step_uses:
        normalized = str(use or "").strip().lower()
        if not normalized:
            issues.append("存在缺少 use 的步骤，无法映射 bridge keyword。")
            continue
        if normalized not in _BRIDGE_KEYWORD_BY_USE:
            issues.append(f"步骤 use={normalized} 缺少 bridge keyword 契约。")
    return issues
