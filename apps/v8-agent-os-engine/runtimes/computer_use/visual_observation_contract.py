from __future__ import annotations

from typing import Any, Dict, List


def _list_bounds(value: Any) -> List[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
    except Exception:
        return None


def summarize_visual_observation(
    *,
    locator: str | None,
    role: str,
    observer_resolution: Dict[str, Any] | None,
    locator_resolution: Dict[str, Any] | None,
) -> Dict[str, Any]:
    observer = dict(observer_resolution or {})
    resolved = dict(locator_resolution or {})
    ranking = dict(resolved.get("semanticRanking") or {})
    matches = [dict(item) for item in list(resolved.get("matches") or []) if isinstance(item, dict)]

    dialog_bounds = _list_bounds(observer.get("dialogBounds"))
    title_zone_bounds = _list_bounds(observer.get("titleZoneBounds"))
    content_zone_bounds = _list_bounds(observer.get("contentZoneBounds"))
    action_zone_bounds = _list_bounds(observer.get("actionZoneBounds"))
    primary_action_zone_bounds = _list_bounds(observer.get("primaryActionZoneBounds"))
    primary_action_button_bounds = _list_bounds(observer.get("primaryActionButtonBounds"))
    scope_bounds = _list_bounds(resolved.get("scopeBounds"))

    candidate_count = int(ranking.get("candidateCount") or resolved.get("matchCount") or len(matches))
    selected_strong = bool(ranking.get("selectedStrong"))
    provider_id = str(resolved.get("providerId") or "").strip() or None
    raw_dialog_confidence = observer.get("dialogConfidence")
    dialog_confidence = float(raw_dialog_confidence or 0.0)
    raw_dialog_confidence_level = observer.get("dialogConfidenceLevel")
    dialog_confidence_level = str(raw_dialog_confidence_level or "").strip().lower() or None
    dialog_suppressed = bool(observer.get("dialogSuppressed"))
    dialog_suppression_reasons = list(observer.get("dialogSuppressionReasons") or [])

    candidate_texts: List[str] = []
    for item in matches[:5]:
        text = str(item.get("text") or "").strip()
        if text:
            candidate_texts.append(text)

    reason_codes: List[str] = []
    if role == "action_button":
        if dialog_bounds is None:
            reason_codes.append("dialog_scope_missing")
        if dialog_suppressed or dialog_confidence_level == "low":
            reason_codes.append("dialog_low_confidence")
        if scope_bounds is None:
            reason_codes.append("scope_missing")
        if action_zone_bounds is None and primary_action_button_bounds is None:
            reason_codes.append("action_zone_missing")
        if candidate_count == 0:
            reason_codes.append("no_candidates")
        if candidate_count > 1 and not selected_strong:
            reason_codes.append("ambiguous_candidates")
        if candidate_count > 0 and not candidate_texts:
            reason_codes.append("no_text_candidates")
        if provider_id == "centered_dialog_scope_fallback":
            reason_codes.append("scope_fallback_only")
    else:
        if candidate_count == 0:
            reason_codes.append("no_candidates")
        if candidate_count > 1 and not selected_strong:
            reason_codes.append("ambiguous_candidates")

    ambiguity_level = "low"
    if "ambiguous_candidates" in reason_codes or "scope_fallback_only" in reason_codes:
        ambiguity_level = "high"
    elif reason_codes:
        ambiguity_level = "medium"

    judge_recommended = any(
        code
        in {
            "dialog_scope_missing",
            "scope_missing",
            "action_zone_missing",
            "ambiguous_candidates",
            "no_text_candidates",
            "scope_fallback_only",
        }
        for code in reason_codes
    )

    return {
        "locator": str(locator or "").strip() or None,
        "role": str(role or "").strip() or "generic",
        "dialogDetected": dialog_bounds is not None,
        "dialogBounds": dialog_bounds,
        "dialogConfidence": dialog_confidence,
        "dialogConfidenceLevel": dialog_confidence_level,
        "dialogSuppressed": dialog_suppressed,
        "dialogSuppressionReasons": dialog_suppression_reasons,
        "titleZoneBounds": title_zone_bounds,
        "contentZoneBounds": content_zone_bounds,
        "actionZoneBounds": action_zone_bounds,
        "primaryActionZoneBounds": primary_action_zone_bounds,
        "primaryActionButtonBounds": primary_action_button_bounds,
        "scopeBounds": scope_bounds,
        "providerId": provider_id,
        "candidateCount": candidate_count,
        "candidateTexts": candidate_texts,
        "selectedStrong": selected_strong,
        "ambiguityLevel": ambiguity_level,
        "judgeRecommended": judge_recommended,
        "reasonCodes": reason_codes,
    }


def build_visual_judge_suggestion(
    *,
    observation: Dict[str, Any] | None,
    locator_resolution: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    summary = dict(observation or {})
    if not bool(summary.get("judgeRecommended")):
        return None
    resolved = dict(locator_resolution or {})
    ranking = dict(resolved.get("semanticRanking") or {})
    ranked = [dict(item) for item in list(ranking.get("rankedCandidates") or []) if isinstance(item, dict)]
    top_candidates: List[Dict[str, Any]] = []
    for item in ranked[:3]:
        top_candidates.append(
            {
                "text": str(item.get("text") or "").strip() or None,
                "label": str(item.get("label") or "").strip() or None,
                "semanticHint": str(item.get("semanticHint") or "").strip() or None,
                "bbox": _list_bounds(item.get("bbox")),
                "score": float(item.get("score") or 0.0),
                "reasons": list(item.get("reasons") or []),
                "sourceLocator": str(item.get("sourceLocator") or "").strip() or None,
                "providerId": str(item.get("providerId") or "").strip() or None,
            }
        )
    if not top_candidates:
        for item in [dict(match) for match in list(resolved.get("matches") or []) if isinstance(match, dict)][:3]:
            top_candidates.append(
                {
                    "text": str(item.get("text") or "").strip() or None,
                    "label": str(item.get("label") or "").strip() or None,
                    "semanticHint": str(item.get("semanticHint") or "").strip() or None,
                    "bbox": _list_bounds(item.get("bbox")),
                    "score": None,
                    "reasons": [],
                    "sourceLocator": str(item.get("_sourceLocator") or "").strip() or None,
                    "providerId": str(item.get("_providerId") or "").strip() or None,
                }
            )
    return {
        "required": True,
        "trigger": list(summary.get("reasonCodes") or []),
        "role": str(summary.get("role") or "generic"),
        "locator": str(summary.get("locator") or "").strip() or None,
        "providerId": str(summary.get("providerId") or "").strip() or None,
        "dialogBounds": _list_bounds(summary.get("dialogBounds")),
        "dialogConfidence": float(summary.get("dialogConfidence") or 0.0),
        "dialogConfidenceLevel": str(summary.get("dialogConfidenceLevel") or "").strip().lower() or None,
        "dialogSuppressed": bool(summary.get("dialogSuppressed")),
        "scopeBounds": _list_bounds(summary.get("scopeBounds")),
        "primaryActionButtonBounds": _list_bounds(summary.get("primaryActionButtonBounds")),
        "candidateCount": int(summary.get("candidateCount") or 0),
        "topCandidates": top_candidates,
        "question": "请在当前 scope 内判断哪个候选最像应执行的目标元素，并确认是否应点击。",
    }
