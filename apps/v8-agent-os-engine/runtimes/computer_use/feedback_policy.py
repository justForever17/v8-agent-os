from __future__ import annotations

from typing import Any, Dict, List


def build_feedback_suggestions(
    *,
    action_type: str,
    action_payload: Dict[str, Any] | None,
    result: Any,
    binding_decision: Any,
    invocation: Any,
) -> Dict[str, Any]:
    payload = dict(action_payload or {})
    result_metadata = dict(getattr(result, "metadata", {}) or {})
    verification = getattr(result, "verification", None)
    verification_payload = verification.as_dict() if hasattr(verification, "as_dict") else dict(verification or {})
    visual_fallback = dict(result_metadata.get("visualFallback") or {})
    update_request = dict(result_metadata.get("updateRequest") or {})
    scene = dict(result_metadata.get("scene") or {})
    binding_block = dict(result_metadata.get("windowBinding") or result_metadata.get("bindingBlock") or {})

    resolved_app_id = getattr(binding_decision, "resolved_app_id", None)
    binding_mode = str(getattr(binding_decision, "binding_mode", "none") or "none").strip().lower()
    binding_confidence = round(float(getattr(binding_decision, "binding_confidence", 0.0) or 0.0), 3)
    profile_eligible = bool(getattr(binding_decision, "profile_eligible", False))
    compat_debug = bool(getattr(invocation, "compat_debug", False))

    selector_memory_candidate = None
    suggested_selector = dict(visual_fallback.get("suggestedSelector") or {})
    if resolved_app_id and suggested_selector:
        selector_memory_candidate = {
            "appId": resolved_app_id,
            "selector": suggested_selector,
            "source": "visual_fallback",
            "reason": str(visual_fallback.get("reason") or "visual_fallback_suggested_selector").strip(),
            "candidateOnly": True,
            "compatDebug": compat_debug,
        }

    app_profile_recommendation = None
    if binding_mode != "explicit" and (not resolved_app_id or not profile_eligible):
        app_profile_recommendation = {
            "requestedAppId": getattr(binding_decision, "requested_app_id", None),
            "resolvedAppId": resolved_app_id,
            "bindingMode": binding_mode,
            "bindingConfidence": binding_confidence,
            "reason": (
                "当前动作依赖启发式 app 绑定，建议补充显式 app_id 或为目标应用完善 profile/playbook。"
                if resolved_app_id
                else "当前动作未能解析到稳定 app 绑定，建议补充显式 app_id、窗口特征或应用 profile。"
            ),
        }

    playbook_recommendation = None
    if update_request or visual_fallback or int(getattr(result, "attempt_count", 1) or 1) > 1:
        playbook_recommendation = {
            "actionType": str(action_type or "").strip().lower(),
            "resolvedAppId": resolved_app_id,
            "reason": str(
                update_request.get("reason")
                or visual_fallback.get("reason")
                or verification_payload.get("reason")
                or "当前动作依赖恢复链或触发更新请求，建议沉淀为 playbook / RPA 草稿候选。"
            ).strip(),
            "signals": {
                "updateRequested": bool(update_request),
                "visualFallbackUsed": bool(visual_fallback),
                "attemptCount": max(1, int(getattr(result, "attempt_count", 1) or 1)),
                "compatDebug": compat_debug,
            },
        }

    preflight_hints: List[Dict[str, Any]] = []
    blocker_state = str(scene.get("blockerState") or "").strip().lower()
    verification_status = str(verification_payload.get("status") or "").strip().lower()
    if binding_block:
        preflight_hints.append(
            {
                "kind": "window_binding",
                "reason": "动作在窗口绑定阶段被阻断，后续正式执行前应先完成窗口/scene 绑定校验。",
                "details": binding_block,
            }
        )
    if blocker_state not in {"", "none"}:
        preflight_hints.append(
            {
                "kind": "scene_blocker",
                "reason": f"检测到阻塞界面 `{blocker_state}`，正式执行前应增加 blocker 预检。",
                "details": {"blockerState": blocker_state, "pageIdentity": scene.get("pageIdentity")},
            }
        )
    if verification_status in {
        "window_binding_unresolved",
        "pre_action_blocker_detected",
        "high_risk_visual_confirmation_required",
        "visual_guard_unconfirmed",
    }:
        preflight_hints.append(
            {
                "kind": "verification_guard",
                "reason": str(
                    verification_payload.get("reason")
                    or "动作验证未通过，建议把对应 guard 条件前移到 preflight。"
                ).strip(),
                "details": {"status": verification_status},
            }
        )
    if compat_debug and preflight_hints:
        preflight_hints.append(
            {
                "kind": "compat_debug_note",
                "reason": "当前来源为 compat/debug 调用，仅生成候选反馈，不自动写回 canonical 配置。",
                "details": {"invocationSource": getattr(invocation, "invocation_source", "compat_http")},
            }
        )

    feedback = {
        "selectorMemoryCandidate": selector_memory_candidate,
        "appProfileRecommendation": app_profile_recommendation,
        "playbookRecommendation": playbook_recommendation,
        "preflightHints": preflight_hints,
    }
    return {
        key: value
        for key, value in feedback.items()
        if value not in (None, {}, [])
    }
