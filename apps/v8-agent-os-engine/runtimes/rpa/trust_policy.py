from __future__ import annotations

from typing import Any, Dict, List

from runtimes.rpa.execution_semantics import normalize_script_assessment_status


TEMPLATE_TRUST_POLICY_VERSION = "rpa-template-trust-v1"
TEMPLATE_APPROVAL_READY_THRESHOLD = 0.84
TEMPLATE_SHADOW_READY_THRESHOLD = 0.72
TEMPLATE_AT_RISK_THRESHOLD = 0.66


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except Exception:
        return default


def _normalize_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"candidate", "review_required", "approved", "rejected", "frozen"}:
        return normalized
    return "candidate"


def _normalize_score(value: Any) -> float:
    numeric = _float(value, 0.0) or 0.0
    return round(max(0.0, min(1.0, numeric)), 3)


def evaluate_template_governance(
    *,
    template_payload: Dict[str, Any],
    calibration: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(template_payload or {})
    metadata = dict(payload.get("metadata") or {})
    assessment = dict(payload.get("assessment") or {})
    promotion_gate = dict(payload.get("promotionGate") or {})
    calibration_payload = dict(calibration or {})

    template_status = _normalize_status(metadata.get("templateStatus") or payload.get("status"))
    assessment_status = normalize_script_assessment_status(assessment.get("status") or "review_required")
    score = _normalize_score(assessment.get("score"))
    source_trace_count = _int(metadata.get("sourceTraceCount"), 1)
    local_repair_count = _int(metadata.get("localRepairCount"), 0)
    repair_trace_count = len([item for item in list(metadata.get("repairTraceRunIds") or []) if str(item).strip()])
    target_strategy_count = len([item for item in list(metadata.get("targetStrategyKeys") or []) if str(item).strip()])
    attachment_capability_count = len([item for item in list(metadata.get("attachmentCapabilities") or []) if str(item).strip()])
    compile_issue_count = len([item for item in list(metadata.get("compileIssues") or []) if str(item).strip()])
    promotion_gate_blocked = bool(promotion_gate.get("blockedPromotion"))
    completed_rate = _float(calibration_payload.get("completedRate"))
    fallback_heavy_rate = _float(calibration_payload.get("fallbackHeavyRate"))
    review_required_rate = _float(calibration_payload.get("reviewRequiredRate"))
    local_repair_rate = _float(calibration_payload.get("localRepairRate"))
    profile_augmented_ratio = _float(calibration_payload.get("profileAugmentedRatio"))
    native_success_rate = _float(calibration_payload.get("nativeSuccessRate"))
    runs = _int(calibration_payload.get("runs"), 0)

    confidence = score
    if source_trace_count >= 2:
        confidence += 0.05
    if source_trace_count >= 3:
        confidence += 0.03
    if target_strategy_count:
        confidence += min(0.03, target_strategy_count * 0.01)
    if attachment_capability_count:
        confidence += 0.01
    if compile_issue_count:
        confidence -= min(0.08, compile_issue_count * 0.02)
    if promotion_gate_blocked:
        confidence -= 0.12
    if local_repair_count:
        confidence -= min(0.1, local_repair_count * 0.04)
    if repair_trace_count:
        confidence -= min(0.08, repair_trace_count * 0.03)
    if completed_rate is not None:
        if completed_rate >= 0.9:
            confidence += 0.05
        elif completed_rate >= 0.82:
            confidence += 0.03
        elif completed_rate <= 0.55:
            confidence -= 0.08
        elif completed_rate <= 0.7:
            confidence -= 0.04
    if fallback_heavy_rate is not None:
        if fallback_heavy_rate >= 0.4:
            confidence -= 0.09
        elif fallback_heavy_rate >= 0.25:
            confidence -= 0.05
    if review_required_rate is not None and review_required_rate >= 0.3:
        confidence -= 0.05
    if local_repair_rate is not None and local_repair_rate >= 0.3:
        confidence -= 0.06
    if profile_augmented_ratio is not None and profile_augmented_ratio >= 0.6:
        confidence -= 0.05
    if native_success_rate is not None and native_success_rate >= 0.85:
        confidence += 0.03
    confidence = _normalize_score(confidence)

    reasons: List[str] = []
    if source_trace_count >= 2:
        reasons.append(f"模板已聚合 {source_trace_count} 条 trace 证据")
    else:
        reasons.append("模板仅来自单条 trace，仍需继续积累证据")
    if local_repair_count:
        reasons.append(f"模板最近累计 {local_repair_count} 次局部修补")
    if repair_trace_count:
        reasons.append("模板包含 repair trace 来源")
    if compile_issue_count:
        reasons.append(f"模板仍带有 {compile_issue_count} 条编译问题")
    if promotion_gate_blocked:
        reasons.append("模板未通过 promotion gate，当前不应提级为稳定模板")
    if completed_rate is not None:
        reasons.append(f"历史完成率 {round(completed_rate, 3)}")
    if fallback_heavy_rate is not None and fallback_heavy_rate >= 0.25:
        reasons.append("历史 fallback 依赖偏高")
    if review_required_rate is not None and review_required_rate >= 0.3:
        reasons.append("历史人工复核率偏高")
    if local_repair_rate is not None and local_repair_rate >= 0.3:
        reasons.append("历史局部修补率偏高")
    if profile_augmented_ratio is not None and profile_augmented_ratio >= 0.6:
        reasons.append("历史上较多依赖 profile 合成定位")
    if native_success_rate is not None and native_success_rate >= 0.85:
        reasons.append("原生语义执行历史稳定")

    stage = "candidate"
    recommended_decision = "keep_candidate"
    rollout_mode = "computer_use_first"
    prefer_template_execution = False
    allow_computer_use_fallback = True
    approval_required = True

    if template_status == "approved":
        stage = "approved_live"
        recommended_decision = "keep_approved"
        rollout_mode = "template_preferred"
        prefer_template_execution = True
        approval_required = False
        if (
            confidence < TEMPLATE_AT_RISK_THRESHOLD
            or (fallback_heavy_rate is not None and fallback_heavy_rate >= 0.35)
            or (local_repair_rate is not None and local_repair_rate >= 0.35)
            or (review_required_rate is not None and review_required_rate >= 0.3)
        ):
            stage = "approved_at_risk"
            recommended_decision = "freeze"
            rollout_mode = "template_preferred_with_fallback"
            reasons.append("已批准模板出现退化信号，建议冻结并重新校准")
    elif template_status == "frozen":
        stage = "frozen_hold"
        recommended_decision = "review_required"
        rollout_mode = "computer_use_first"
        reasons.append("模板已冻结，不应直接作为首选执行路径")
    elif template_status == "rejected":
        stage = "rejected_hold"
        recommended_decision = "keep_rejected"
        rollout_mode = "computer_use_first"
        reasons.append("模板已被拒绝，仅保留历史参考价值")
    elif promotion_gate_blocked:
        stage = "candidate"
        recommended_decision = "review_required"
        rollout_mode = "computer_use_first"
        reasons.append("promotion gate 未通过，必须继续由 ComputerUse 探索")
    elif assessment_status in {"compile_blocked", "fallback_heavy"} or confidence < TEMPLATE_AT_RISK_THRESHOLD:
        stage = "candidate"
        recommended_decision = "review_required"
        rollout_mode = "computer_use_first"
        reasons.append("当前模板稳定性不足，暂不应提升到主执行链")
    elif (
        assessment_status == "accepted"
        and confidence >= TEMPLATE_APPROVAL_READY_THRESHOLD
        and source_trace_count >= 2
        and local_repair_count == 0
        and (completed_rate is None or completed_rate >= 0.85)
        and (fallback_heavy_rate is None or fallback_heavy_rate < 0.25)
        and (review_required_rate is None or review_required_rate < 0.2)
    ):
        stage = "approval_ready"
        recommended_decision = "approve"
        rollout_mode = "candidate_shadow"
        reasons.append("已达到审批准备条件，可以进入模板审批")
    else:
        stage = "shadow_ready"
        recommended_decision = "keep_candidate"
        rollout_mode = "candidate_shadow"
        reasons.append("模板可作为 shadow candidate 继续积累样本")

    assessment_signals = dict(assessment.get("signals") or {})
    decision_reason_group = "compile_rule"
    if float(dict(assessment_signals.get("bindingSummary") or {}).get("lowConfidenceRatio") or 0.0) >= 0.5:
        decision_reason_group = "binding_risk"
    elif int(dict(assessment_signals.get("preflightSummary") or {}).get("blockerDetectedSteps") or 0) > 0:
        decision_reason_group = "preflight_risk"
    elif assessment_status == "fallback_heavy":
        decision_reason_group = "recovery_heavy"
    elif runs >= 3 and (
        (fallback_heavy_rate is not None and fallback_heavy_rate >= 0.35)
        or (local_repair_rate is not None and local_repair_rate >= 0.35)
    ):
        decision_reason_group = "calibration_drag"

    return {
        "version": TEMPLATE_TRUST_POLICY_VERSION,
        "templateStatus": template_status,
        "assessmentStatus": assessment_status,
        "confidence": confidence,
        "stage": stage,
        "recommendedDecision": recommended_decision,
        "promotionEligible": stage in {"approval_ready", "approved_live", "approved_at_risk"},
        "approvalRequired": approval_required,
        "preferTemplateExecution": prefer_template_execution,
        "allowComputerUseFallback": allow_computer_use_fallback,
        "rolloutMode": rollout_mode,
        "decisionScope": "script",
        "decisionReasonGroup": decision_reason_group,
        "decisionSignals": {
            "bindingSummary": dict(assessment_signals.get("bindingSummary") or {}),
            "preflightSummary": dict(assessment_signals.get("preflightSummary") or {}),
            "recoverySummary": dict(assessment_signals.get("recoverySummary") or {}),
            "historicalRuns": runs,
            "historicalCompletedRate": completed_rate,
            "historicalFallbackHeavyRate": fallback_heavy_rate,
            "historicalLocalRepairRate": local_repair_rate,
        },
        "signals": {
            "sourceTraceCount": source_trace_count,
            "localRepairCount": local_repair_count,
            "repairTraceCount": repair_trace_count,
            "targetStrategyCount": target_strategy_count,
            "attachmentCapabilityCount": attachment_capability_count,
            "compileIssueCount": compile_issue_count,
            "promotionGateBlocked": promotion_gate_blocked,
            "historicalRuns": runs,
            "historicalCompletedRate": completed_rate,
            "historicalFallbackHeavyRate": fallback_heavy_rate,
            "historicalReviewRequiredRate": review_required_rate,
            "historicalLocalRepairRate": local_repair_rate,
            "historicalProfileAugmentedRatio": profile_augmented_ratio,
            "historicalNativeSuccessRate": native_success_rate,
        },
        "reasons": list(dict.fromkeys(reasons)),
    }


def draft_template_governance_summary(governance: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(governance or {})
    return {
        "version": payload.get("version") or TEMPLATE_TRUST_POLICY_VERSION,
        "templateStatus": payload.get("templateStatus"),
        "stage": payload.get("stage"),
        "recommendedDecision": payload.get("recommendedDecision"),
        "confidence": payload.get("confidence"),
        "promotionEligible": bool(payload.get("promotionEligible")),
        "approvalRequired": bool(payload.get("approvalRequired")),
        "preferTemplateExecution": bool(payload.get("preferTemplateExecution")),
        "allowComputerUseFallback": bool(payload.get("allowComputerUseFallback", True)),
        "rolloutMode": payload.get("rolloutMode"),
        "decisionScope": payload.get("decisionScope"),
        "decisionReasonGroup": payload.get("decisionReasonGroup"),
        "decisionSignals": dict(payload.get("decisionSignals") or {}),
        "reasons": list(payload.get("reasons") or []),
        "signals": dict(payload.get("signals") or {}),
    }
