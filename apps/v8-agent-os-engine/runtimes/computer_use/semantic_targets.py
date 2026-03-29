from __future__ import annotations

from typing import Any, Dict, List, Tuple


INPUT_TARGET_KEYS = {
    "primary_input",
    "secondary_input",
    "search_input",
    "message_input",
    "recipient_input",
    "subject_input",
    "address_bar",
    "editor",
    "input",
}

RESULT_TARGET_KEYS = {
    "primary_result",
    "candidate_item",
    "conversation_result",
}

ACTION_TARGET_KEYS = {
    "confirm_action",
    "submit_action",
    "send_button",
}

LIST_TARGET_KEYS = {
    "primary_list",
    "list",
    "content_receiver",
}


def normalize_selector_key(selector_key: Any) -> str:
    return str(selector_key or "").strip().lower()


def is_input_target_key(selector_key: Any) -> bool:
    return normalize_selector_key(selector_key) in INPUT_TARGET_KEYS


def is_result_target_key(selector_key: Any) -> bool:
    return normalize_selector_key(selector_key) in RESULT_TARGET_KEYS


def is_action_target_key(selector_key: Any) -> bool:
    return normalize_selector_key(selector_key) in ACTION_TARGET_KEYS


def input_selector_fallback_keys() -> List[str]:
    return ["primary_input", "editor", "input", "address_bar"]


def list_selector_fallback_keys() -> List[str]:
    return ["primary_list", "list", "content_receiver"]


def preferred_result_region_bounds(preferred_result_region: Any) -> Tuple[float, float] | None:
    normalized = str(preferred_result_region or "").strip().lower()
    if normalized == "upper":
        return (0.08, 0.42)
    if normalized == "middle":
        return (0.28, 0.72)
    if normalized == "lower":
        return (0.58, 0.94)
    return None


def should_bias_towards_text_lane(action_payload: Dict[str, Any]) -> bool:
    preferred_hit_zone = str(action_payload.get("preferred_hit_zone") or "").strip().lower()
    selector_key = normalize_selector_key(action_payload.get("selector_key"))
    if preferred_hit_zone in {"text_lane", "text_lane_left", "label_lane", "content_lane"}:
        return True
    return selector_key in {"playable_song_text_lane", "candidate_item", "primary_result"}


def should_accept_visual_point(action_payload: Dict[str, Any], suggested_point: List[float]) -> bool:
    if not isinstance(suggested_point, list) or len(suggested_point) != 2:
        return False
    try:
        x = float(suggested_point[0])
        y = float(suggested_point[1])
    except Exception:
        return False
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return False

    bounds = preferred_result_region_bounds(action_payload.get("preferred_result_region"))
    if bounds is not None and not (bounds[0] <= y <= bounds[1]):
        return False

    selector_key = normalize_selector_key(action_payload.get("selector_key"))
    if is_result_target_key(selector_key) or is_action_target_key(selector_key):
        if should_bias_towards_text_lane(action_payload) and x < 0.14:
            return False
    if is_input_target_key(selector_key) and x < 0.08:
        return False
    return True


def generic_result_visual_hint(
    *,
    target_text: str,
    preferred_result_region: str,
    preferred_result_section: str,
    preferred_hit_zone: str,
    activation_gesture: str,
    forbidden_result_tokens: List[str],
) -> str:
    if not target_text:
        return ""
    lines = [
        f"6. 若截图里存在多个相似候选，请优先选择名称完全等于“{target_text}”的目标项；不要选包含额外前后缀、功能标签或扩展词的近似项。\n",
        "7. 避开分组标题、放大镜/头像/图标行、网页搜索入口或其它非主结果行。\n",
    ]
    if preferred_result_region:
        lines.append(f"8. 若结果存在多块区域，请优先关注 {preferred_result_region} 区域中的精确项。\n")
    if preferred_result_section:
        lines.append(f"9. 若结果分为多个分区，请优先选择 {preferred_result_section} 分区中的目标项。\n")
    if preferred_hit_zone:
        lines.append(f"10. 返回的 point 应尽量落在 {preferred_hit_zone} 对应的主交互区域上。\n")
    if activation_gesture:
        lines.append(f"11. 该目标后续需要优先支持 {activation_gesture} 激活方式。\n")
    if forbidden_result_tokens:
        lines.append(f"12. 避开名称中包含这些词的候选：{', '.join(forbidden_result_tokens)}。\n")
    return "".join(lines)


def generic_input_visual_hint(*, target_text: str) -> str:
    lines = [
        "6. 如果目标是输入区，请返回真正可输入文本的区域中心点，不要落在左侧图标、头像、前缀按钮或装饰区域上。\n",
    ]
    if target_text:
        lines.append(
            f"7. 如果输入框里已显示“{target_text}”，或候选结果已明显围绕“{target_text}”展开，可视为输入成功。\n"
        )
    return "".join(lines)
