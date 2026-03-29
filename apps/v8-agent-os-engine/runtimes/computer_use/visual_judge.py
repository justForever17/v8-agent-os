from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from runtimes.computer_use.visual_locator_scope import crop_capture_image_to_bounds


def _list_bounds(value: Any) -> List[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
    except Exception:
        return None


def _relative_position_label(bbox: List[int] | None, scope_bounds: List[int] | None) -> str:
    if not isinstance(bbox, list) or len(bbox) != 4 or not isinstance(scope_bounds, list) or len(scope_bounds) != 4:
        return "位置未知"
    left, top, right, bottom = [int(item) for item in bbox]
    scope_left, scope_top, scope_right, scope_bottom = [int(item) for item in scope_bounds]
    center_x = left + max(1, right - left) / 2.0
    center_y = top + max(1, bottom - top) / 2.0
    scope_width = max(1.0, float(scope_right - scope_left))
    scope_height = max(1.0, float(scope_bottom - scope_top))
    rel_x = (center_x - scope_left) / scope_width
    rel_y = (center_y - scope_top) / scope_height
    vertical = "顶部" if rel_y <= 0.34 else "底部" if rel_y >= 0.66 else "中部"
    horizontal = "左侧" if rel_x <= 0.34 else "右侧" if rel_x >= 0.66 else "中间"
    return f"{vertical}{horizontal}"


def _bbox_size_label(bbox: List[int] | None) -> str:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return "大小未知"
    width = max(1, int(bbox[2]) - int(bbox[0]))
    height = max(1, int(bbox[3]) - int(bbox[1]))
    return f"{width}x{height}"


def _json_object_candidates(text: str) -> List[str]:
    payload = str(text or "").strip()
    if not payload:
        return []
    candidates: List[str] = []
    fenced_start = payload.find("```json")
    if fenced_start >= 0:
        fenced_end = payload.find("```", fenced_start + 7)
        if fenced_end > fenced_start:
            candidates.append(payload[fenced_start + 7 : fenced_end].strip())
    generic_fence_start = payload.find("```")
    if generic_fence_start >= 0:
        generic_fence_end = payload.find("```", generic_fence_start + 3)
        if generic_fence_end > generic_fence_start:
            candidates.append(payload[generic_fence_start + 3 : generic_fence_end].strip())
    stack: List[int] = []
    for index, char in enumerate(payload):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            if not stack:
                candidates.append(payload[start : index + 1].strip())
    candidates.append(payload)
    deduped: List[str] = []
    seen: set[str] = set()
    for item in candidates:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def build_visual_judge_prompt(
    *,
    suggestion: Dict[str, Any],
) -> str:
    role = str(suggestion.get("role") or "generic").strip() or "generic"
    trigger = ", ".join(str(item).strip() for item in list(suggestion.get("trigger") or []) if str(item).strip()) or "无"
    dialog_confidence = str(suggestion.get("dialogConfidenceLevel") or "unknown").strip().lower() or "unknown"
    scope_bounds = _list_bounds(suggestion.get("scopeBounds")) or _list_bounds(suggestion.get("dialogBounds"))
    primary_action_button_bounds = _list_bounds(suggestion.get("primaryActionButtonBounds"))
    candidate_lines: List[str] = []
    for index, item in enumerate(list(suggestion.get("topCandidates") or [])[:5]):
        candidate = dict(item or {})
        bbox = _list_bounds(candidate.get("bbox"))
        text = str(candidate.get("text") or "").strip() or "无文字"
        label = str(candidate.get("label") or "").strip() or "无标签"
        semantic_hint = str(candidate.get("semanticHint") or "").strip() or "无语义提示"
        position = _relative_position_label(bbox, scope_bounds)
        size_label = _bbox_size_label(bbox)
        source_locator = str(candidate.get("sourceLocator") or "").strip() or "未知来源"
        provider_id = str(candidate.get("providerId") or "").strip() or "未知 provider"
        reasons = "；".join(str(reason).strip() for reason in list(candidate.get("reasons") or []) if str(reason).strip()) or "无额外规则说明"
        candidate_lines.append(
            f"[{index}] text={text} | label={label} | 语义={semantic_hint} | 位置={position} | 尺寸={size_label} | 来源={source_locator} | provider={provider_id} | reasons={reasons}"
        )
    primary_action_hint = ""
    if isinstance(primary_action_button_bounds, list):
        primary_action_hint = (
            f"观察器推测的主按钮区：{primary_action_button_bounds}。"
            "如果某个候选明显落在这个区域或与其角色一致，应优先考虑。"
        )
    return (
        "你是 Windows GUI 的视觉裁判。图片已经裁到当前目标 scope（通常是弹窗或其局部区域）。\n"
        "你的任务不是重新定位坐标，而是在现有候选里判断哪个最像应该点击的目标；如果都不对，就明确拒绝点击。\n"
        f"目标角色：{role}\n"
        f"触发原因：{trigger}\n"
        f"当前 dialog 置信等级：{dialog_confidence}\n"
        f"{primary_action_hint}\n"
        "候选列表：\n"
        + "\n".join(candidate_lines or ["无候选。"])
        + "\n输出要求：只输出一个 JSON 对象，不要输出其他说明。\n"
        '格式：{"decision":"candidate|no_click|review","selectedIndex":0,"confidence":"high|medium|low","reason":"简短原因"}\n'
        "规则：\n"
        "1. 如果候选中存在明显的主操作按钮，decision=candidate，并返回 selectedIndex。\n"
        "2. 标题、说明文字、背景消息文本都不能当成动作按钮。\n"
        "3. 如果候选都不应点击，返回 no_click。\n"
        "4. 如果仍然拿不准，返回 review。\n"
    )


def parse_visual_judge_analysis(analysis: str) -> Dict[str, Any] | None:
    text = str(analysis or "").strip()
    if not text:
        return None
    for candidate in _json_object_candidates(text):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        decision = str(parsed.get("decision") or "").strip().lower()
        if decision not in {"candidate", "no_click", "review"}:
            continue
        selected_index = parsed.get("selectedIndex")
        if selected_index is not None:
            try:
                selected_index = int(selected_index)
            except Exception:
                selected_index = None
        confidence = str(parsed.get("confidence") or "").strip().lower() or None
        reason = str(parsed.get("reason") or "").strip() or None
        return {
            "decision": decision,
            "selectedIndex": selected_index,
            "confidence": confidence,
            "reason": reason,
            "raw": parsed,
        }
    return None


def apply_visual_judge_decision(
    *,
    resolution: Dict[str, Any],
    decision: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(resolution or {})
    suggestion = dict(payload.get("visualJudgeSuggestion") or {})
    candidates = [dict(item or {}) for item in list(suggestion.get("topCandidates") or []) if isinstance(item, dict)]
    normalized_decision = dict(decision or {})
    judge_status = str(normalized_decision.get("decision") or normalized_decision.get("status") or "review").strip().lower()
    selected_index = normalized_decision.get("selectedIndex")
    chosen_candidate: Dict[str, Any] | None = None
    if judge_status == "candidate" and selected_index is not None:
        try:
            numeric_index = int(selected_index)
        except Exception:
            numeric_index = -1
        if 0 <= numeric_index < len(candidates):
            chosen_candidate = dict(candidates[numeric_index])
            normalized_decision["selectedIndex"] = numeric_index
    payload["visualJudge"] = normalized_decision
    if chosen_candidate is None:
        payload["matches"] = []
        payload["matchCount"] = 0
        payload["status"] = "judge_blocked"
        return payload
    bbox = _list_bounds(chosen_candidate.get("bbox"))
    if not isinstance(bbox, list):
        payload["matches"] = []
        payload["matchCount"] = 0
        payload["status"] = "judge_blocked"
        return payload
    left, top, right, bottom = [int(item) for item in bbox]
    center = [int(left + max(1, right - left) // 2), int(top + max(1, bottom - top) // 2)]
    selected_match = {
        "bbox": bbox,
        "center": center,
        "text": str(chosen_candidate.get("text") or "").strip() or None,
        "confidence": 1.0 if str(normalized_decision.get("confidence") or "").strip().lower() == "high" else 0.66,
    }
    payload["matches"] = [selected_match]
    payload["matchCount"] = 1
    payload["status"] = "judge_selected"
    ranking = dict(payload.get("semanticRanking") or {})
    ranking["judgeSelected"] = True
    ranking["judgeSelectedIndex"] = int(normalized_decision.get("selectedIndex") or 0)
    ranking["selectedStrong"] = True
    payload["semanticRanking"] = ranking
    return payload


def run_visual_judge(
    *,
    resolution: Dict[str, Any],
    current_search_image_path: str | None,
    capture_image_path: str | None,
    capture_bounds: List[int] | None,
    invoke: Callable[[str, str], str] | None,
    available: bool,
) -> Dict[str, Any]:
    payload = dict(resolution or {})
    suggestion = dict(payload.get("visualJudgeSuggestion") or {})
    if not bool(suggestion.get("required")):
        return payload
    if not bool(available) or not callable(invoke):
        return apply_visual_judge_decision(
            resolution=payload,
            decision={
                "status": "unavailable",
                "decision": "review",
                "reason": "视觉裁判不可用，当前歧义场景拒绝盲点。",
                "confidence": "low",
            },
        )

    judge_image_path: str | None = None
    temporary_scope_crop_path: str | None = None
    scope_bounds = _list_bounds(suggestion.get("scopeBounds")) or _list_bounds(suggestion.get("dialogBounds"))
    try:
        if (
            current_search_image_path
            and capture_image_path
            and str(current_search_image_path).strip()
            and Path(str(current_search_image_path)).exists()
            and str(Path(str(current_search_image_path)).resolve()) != str(Path(str(capture_image_path)).resolve())
        ):
            judge_image_path = str(current_search_image_path)
        elif capture_image_path and isinstance(scope_bounds, list) and isinstance(capture_bounds, list):
            cropped_scope_path, temp_scope_path = crop_capture_image_to_bounds(
                image_path=capture_image_path,
                capture_bounds=capture_bounds,
                target_bounds=scope_bounds,
            )
            if cropped_scope_path:
                judge_image_path = str(cropped_scope_path)
                temporary_scope_crop_path = temp_scope_path
        if not judge_image_path and capture_image_path:
            judge_image_path = str(capture_image_path)
        if not judge_image_path or not Path(judge_image_path).exists():
            return apply_visual_judge_decision(
                resolution=payload,
                decision={
                    "status": "judge_image_missing",
                    "decision": "review",
                    "reason": "视觉裁判缺少可用图像输入。",
                    "confidence": "low",
                },
            )
        prompt = build_visual_judge_prompt(suggestion=suggestion)
        analysis = invoke(judge_image_path, prompt)
        parsed = parse_visual_judge_analysis(analysis)
        if parsed is None:
            return apply_visual_judge_decision(
                resolution=payload,
                decision={
                    "status": "parse_failed",
                    "decision": "review",
                    "reason": "视觉裁判返回内容无法解析。",
                    "confidence": "low",
                    "analysis": str(analysis or ""),
                },
            )
        parsed["status"] = "selected" if parsed.get("decision") == "candidate" else str(parsed.get("decision") or "review")
        parsed["analysis"] = str(analysis or "")
        return apply_visual_judge_decision(
            resolution=payload,
            decision=parsed,
        )
    except Exception as exc:
        return apply_visual_judge_decision(
            resolution=payload,
            decision={
                "status": "error",
                "decision": "review",
                "reason": f"{exc.__class__.__name__}: {exc}",
                "confidence": "low",
            },
        )
    finally:
        if temporary_scope_crop_path:
            Path(temporary_scope_crop_path).unlink(missing_ok=True)
