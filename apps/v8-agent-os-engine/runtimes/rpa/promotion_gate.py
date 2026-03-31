from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


PROMOTION_GATE_VERSION = "rpa-promotion-gate-v2"


def _string(value: Any) -> str:
    return str(value or "").strip()


def _normalize_status(value: Any) -> str:
    return _string(value).lower()


def _step_variables(step: Dict[str, Any], script_variables: Iterable[Dict[str, Any]]) -> set[str]:
    placeholders = {
        _string(item.get("placeholder")).strip("{}")
        for item in list(script_variables or [])
        if isinstance(item, dict) and _string(item.get("placeholder"))
    }
    names = {
        _string(item.get("name"))
        for item in list(script_variables or [])
        if isinstance(item, dict) and _string(item.get("name"))
    }
    return {item for item in placeholders.union(names) if item}


def _has_unbound_placeholder(value: Any, variables: set[str]) -> bool:
    if isinstance(value, str):
        if "{{" in value and "}}" in value:
            token = value.replace("{", "").replace("}", "").strip()
            return token not in variables
        return False
    if isinstance(value, list):
        return any(_has_unbound_placeholder(item, variables) for item in value)
    if isinstance(value, dict):
        return any(_has_unbound_placeholder(item, variables) for item in value.values())
    return False


def _visual_locator_metadata(
    metadata: Dict[str, Any],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    candidates = [
        dict(metadata.get("visualLocator") or {}),
        dict(metadata.get("postActionVisualLocator") or {}),
        dict(metadata.get("startVisualLocator") or {}),
        dict(metadata.get("endVisualLocator") or {}),
    ]
    provider_ids = [
        _string(item.get("providerId") or item.get("parserId"))
        for item in candidates
        if isinstance(item, dict)
    ]
    provider_ids = [item for item in provider_ids if item]
    semantic_roles = [
        _string(
            dict(item.get("visualObservation") or {}).get("role")
            or item.get("semanticRole")
        )
        for item in candidates
        if isinstance(item, dict)
    ]
    semantic_roles = [item for item in semantic_roles if item]
    judge_decisions = [
        _normalize_status(dict(item.get("visualJudge") or {}).get("decision") or dict(item.get("visualJudge") or {}).get("status"))
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("visualJudge"), dict)
    ]
    judge_decisions = [item for item in judge_decisions if item]
    backed = bool(provider_ids) or bool(
        _string(params.get("visual_locator")) or _string(params.get("start_visual_locator")) or _string(params.get("end_visual_locator"))
    )
    return {
        "visualLocatorBacked": backed,
        "visualLocatorProvider": provider_ids[0] if provider_ids else None,
        "visualSemanticRole": semantic_roles[0] if semantic_roles else None,
        "visualJudgeBacked": bool(judge_decisions),
        "visualJudgeSelected": any(item == "candidate" for item in judge_decisions),
    }


def _execution_route_metadata(
    metadata: Dict[str, Any],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(metadata.get("executionRoute") or {})
    route = _normalize_status(payload.get("route"))
    if not route:
        if _string(params.get("visual_locator")) or _string(params.get("start_visual_locator")) or _string(params.get("end_visual_locator")):
            route = "visual_locator"
        elif params.get("point") not in (None, "") or list(params.get("point_candidates") or []):
            route = "coordinate_fallback"
    return {
        "route": route or None,
        "source": _string(payload.get("source")) or None,
        "visualLocatorBacked": bool(payload.get("visualLocatorBacked")),
        "coordinateFallback": bool(payload.get("coordinateFallback")),
        "humanApprovalRequired": bool(payload.get("humanApprovalRequired")),
    }


def _step_gate_issues(
    step: Dict[str, Any],
    script_variables: Iterable[Dict[str, Any]],
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    issues: List[str] = []
    warnings: List[str] = []
    metadata = dict(step.get("metadata") or {})
    verification = dict(step.get("verification") or {})
    recovery = dict(step.get("recovery") or {})
    params = dict(step.get("params") or {})
    primitive = dict(metadata.get("primitive") or {})
    scene = dict(metadata.get("scene") or {})
    budget = dict(metadata.get("budget") or {})
    variables = _step_variables(step, script_variables)
    step_label = _string(step.get("stepId") or step.get("use") or "step")
    visual_locator_metadata = _visual_locator_metadata(metadata, params)
    execution_route_metadata = _execution_route_metadata(metadata, params)
    has_visual_locator = bool(visual_locator_metadata.get("visualLocatorBacked"))
    visual_locator_provider = _string(visual_locator_metadata.get("visualLocatorProvider"))
    execution_route = _normalize_status(execution_route_metadata.get("route"))
    signals: Dict[str, Any] = {
        "visualLocatorBacked": has_visual_locator,
        "visualLocatorProvider": visual_locator_provider or None,
        "visualSemanticRole": _string(visual_locator_metadata.get("visualSemanticRole")) or None,
        "visualJudgeBacked": bool(visual_locator_metadata.get("visualJudgeBacked")),
        "visualJudgeSelected": bool(visual_locator_metadata.get("visualJudgeSelected")),
        "executionRoute": execution_route or None,
    }

    if not primitive.get("id"):
        issues.append(f"{step_label}: 缺少 primitive 标识")
    if bool(primitive.get("requiresPageIdentity", True)) and not _string(scene.get("pageIdentity")):
        issues.append(f"{step_label}: 缺少 page identity")
    if bool(primitive.get("requiresVerificationContract", True)) and verification.get("status") in (None, "") and verification.get("passed") is None:
        issues.append(f"{step_label}: 缺少 verification evidence")
    verification_level = _normalize_status(verification.get("level"))
    verification_status = _normalize_status(verification.get("status"))
    blocker_state = _normalize_status(scene.get("blockerState"))
    transition_state = _normalize_status(scene.get("transitionState"))
    scene_confidence = _normalize_status(scene.get("confidence"))
    primitive_affordances = {_normalize_status(item) for item in list(primitive.get("affordances") or []) if _normalize_status(item)}
    if verification_level in {"review_required", "failed"}:
        issues.append(f"{step_label}: verification level 为 {verification_level}")
    if verification_level == "executed_only":
        issues.append(f"{step_label}: verification 仅证明动作已执行，未形成稳定业务成功证据")
    if execution_route == "coordinate_fallback":
        issues.append(f"{step_label}: 当前步骤走 coordinate fallback，不满足稳定提级要求")
    if execution_route == "human_approval":
        issues.append(f"{step_label}: 当前步骤仍依赖 human approval，不满足自动化提级要求")
    if blocker_state not in {"", "none"}:
        issues.append(f"{step_label}: scene blockerState 为 {blocker_state}")
    if transition_state in {"unknown", "failed", "blocked", "update_requested"}:
        issues.append(f"{step_label}: transitionState 为 {transition_state}")
    elif transition_state == "waiting_for_transition":
        warnings.append(f"{step_label}: transitionState 仍在等待稳定")
    if bool(primitive.get("requiresPageIdentity", True)) and scene_confidence == "low":
        issues.append(f"{step_label}: scene confidence 过低")
    if bool(primitive.get("requiresRecoveryPolicy", True)) and not recovery:
        issues.append(f"{step_label}: 缺少 recovery policy")
    if budget and budget.get("withinBudget") is False:
        issues.append(f"{step_label}: 动作超出预算")
    if params and _has_unbound_placeholder(params, variables):
        issues.append(f"{step_label}: 存在未绑定变量占位符")
    if (params.get("point") not in (None, "") or list(params.get("point_candidates") or [])) and not _string(scene.get("pageIdentity")):
        issues.append(f"{step_label}: 坐标动作缺少页面身份约束")
    if verification_status == "focus_verified" and _normalize_status(primitive.get("id")) != "window.focus":
        issues.append(f"{step_label}: verification 仅证明聚焦成功，不足以支持动作提级")
    if verification_level == "soft_verified" and verification_status in {
        "soft_verified_target_only",
        "coordinate_click_executed",
        "coordinate_text_executed",
        "coordinate_file_paste_executed",
    }:
        issues.append(f"{step_label}: verification 仅为弱 soft_verified 状态")
    if has_visual_locator and verification_level != "verified":
        issues.append(f"{step_label}: 视觉定位步骤缺少 verified 级别证据")
    if "file_payload" in primitive_affordances and verification_level != "verified":
        issues.append(f"{step_label}: 文件载荷步骤必须具备 verified 级别证据")
    if (params.get("point") not in (None, "") or list(params.get("point_candidates") or [])) and verification_level != "verified":
        issues.append(f"{step_label}: 坐标驱动步骤缺少 verified 级别证据")
    if has_visual_locator and visual_locator_provider:
        warnings.append(f"{step_label}: 使用统一视觉定位层 {visual_locator_provider}")
    if not bool(primitive.get("supportsRpaPromotion", True)):
        warnings.append(f"{step_label}: 当前 primitive 不建议直接提级为稳定模板")
    return issues, warnings, signals


def evaluate_promotion_gate(
    *,
    script_payload: Dict[str, Any],
    template_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    script = dict(script_payload or {})
    template = dict(template_payload or {})
    steps = [dict(item) for item in list((template or script).get("steps") or []) if isinstance(item, dict)]
    script_variables = list(script.get("variables") or [])
    issues: List[str] = []
    warnings: List[str] = []
    promotable_steps = 0
    visual_locator_backed_steps = 0
    verified_visual_locator_steps = 0
    visual_locator_providers: List[str] = []
    visual_judge_steps = 0
    visual_judge_selected_steps = 0
    visual_semantic_roles: List[str] = []
    for step in steps:
        step_issues, step_warnings, step_signals = _step_gate_issues(step, script_variables)
        if not step_issues:
            promotable_steps += 1
        issues.extend(step_issues)
        warnings.extend(step_warnings)
        if bool(step_signals.get("visualLocatorBacked")):
            visual_locator_backed_steps += 1
            provider_id = _string(step_signals.get("visualLocatorProvider"))
            if provider_id:
                visual_locator_providers.append(provider_id)
            verification = dict(step.get("verification") or {})
            if _normalize_status(verification.get("level")) == "verified":
                verified_visual_locator_steps += 1
        if bool(step_signals.get("visualJudgeBacked")):
            visual_judge_steps += 1
        if bool(step_signals.get("visualJudgeSelected")):
            visual_judge_selected_steps += 1
        semantic_role = _string(step_signals.get("visualSemanticRole"))
        if semantic_role:
            visual_semantic_roles.append(semantic_role)
    status = "passed" if not issues else "blocked"
    recommended_decision = "allow_promotion" if status == "passed" else "review_required"
    return {
        "version": PROMOTION_GATE_VERSION,
        "status": status,
        "eligible": status == "passed",
        "blockedPromotion": status != "passed",
        "recommendedDecision": recommended_decision,
        "reasons": list(dict.fromkeys(issues)),
        "warnings": list(dict.fromkeys(warnings)),
        "signals": {
            "stepCount": len(steps),
            "promotableSteps": promotable_steps,
            "blockedSteps": max(0, len(steps) - promotable_steps),
            "visualLocatorBackedSteps": visual_locator_backed_steps,
            "verifiedVisualLocatorSteps": verified_visual_locator_steps,
            "visualLocatorProviders": list(dict.fromkeys(visual_locator_providers)),
            "visualJudgeSteps": visual_judge_steps,
            "visualJudgeSelectedSteps": visual_judge_selected_steps,
            "visualSemanticRoles": list(dict.fromkeys(visual_semantic_roles)),
        },
    }


def draft_promotion_gate_summary(gate: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(gate or {})
    return {
        "version": payload.get("version") or PROMOTION_GATE_VERSION,
        "status": payload.get("status") or "blocked",
        "eligible": bool(payload.get("eligible")),
        "blockedPromotion": bool(payload.get("blockedPromotion", True)),
        "recommendedDecision": payload.get("recommendedDecision") or "review_required",
        "reasons": list(payload.get("reasons") or []),
        "warnings": list(payload.get("warnings") or []),
        "signals": dict(payload.get("signals") or {}),
    }


def draft_visual_signal_summary(
    gate: Dict[str, Any] | None = None,
    *,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    gate_payload = dict(gate or {})
    signal_payload = dict(gate_payload.get("signals") or {})
    metadata_payload = dict(metadata or {})
    nested_summary = dict(metadata_payload.get("visualSignalSummary") or metadata_payload.get("templateVisualSignalSummary") or {})
    if nested_summary:
        metadata_payload = {**metadata_payload, **nested_summary}
    visual_semantic_roles = [
        str(item).strip()
        for item in list(signal_payload.get("visualSemanticRoles") or metadata_payload.get("visualSemanticRoles") or [])
        if str(item).strip()
    ]
    visual_locator_providers = [
        str(item).strip()
        for item in list(signal_payload.get("visualLocatorProviders") or metadata_payload.get("visualLocatorProviders") or [])
        if str(item).strip()
    ]
    visual_locator_backed_steps = int(
        signal_payload.get("visualLocatorBackedSteps")
        or metadata_payload.get("visualLocatorBackedSteps")
        or 0
    )
    verified_visual_locator_steps = int(
        signal_payload.get("verifiedVisualLocatorSteps")
        or metadata_payload.get("verifiedVisualLocatorSteps")
        or 0
    )
    visual_judge_steps = int(
        signal_payload.get("visualJudgeSteps")
        or metadata_payload.get("visualJudgeStepCount")
        or 0
    )
    visual_judge_selected_steps = int(
        signal_payload.get("visualJudgeSelectedSteps")
        or metadata_payload.get("visualJudgeSelectedStepCount")
        or 0
    )
    return {
        "visualLocatorBacked": visual_locator_backed_steps > 0,
        "visualLocatorBackedSteps": visual_locator_backed_steps,
        "verifiedVisualLocatorSteps": verified_visual_locator_steps,
        "visualLocatorProviders": list(dict.fromkeys(visual_locator_providers)),
        "visualJudgeBacked": visual_judge_steps > 0,
        "visualJudgeSteps": visual_judge_steps,
        "visualJudgeSelectedSteps": visual_judge_selected_steps,
        "visualSemanticRoles": list(dict.fromkeys(visual_semantic_roles)),
        "recentVisualAcceptanceStatus": str(signal_payload.get("recentVisualAcceptanceStatus") or "").strip() or None,
        "recentVisualAcceptanceCount": int(signal_payload.get("recentVisualAcceptanceCount") or 0),
        "recentVisualAcceptanceReportCount": int(signal_payload.get("recentVisualAcceptanceReportCount") or 0),
        "recentVisualAcceptanceReadTextCaseCount": int(signal_payload.get("recentVisualAcceptanceReadTextCaseCount") or 0),
        "recentVisualAcceptanceOcrEnhancedCaseCount": int(signal_payload.get("recentVisualAcceptanceOcrEnhancedCaseCount") or 0),
    }


def draft_timing_signal_summary(
    gate: Dict[str, Any] | None = None,
    *,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    gate_payload = dict(gate or {})
    signal_payload = dict(gate_payload.get("signals") or {})
    metadata_payload = dict(metadata or {})
    nested_summary = dict(
        metadata_payload.get("timingSignalSummary") or metadata_payload.get("templateTimingSignalSummary") or {}
    )
    if nested_summary:
        metadata_payload = {**metadata_payload, **nested_summary}
    transition_states = [
        str(item).strip()
        for item in list(signal_payload.get("transitionStates") or metadata_payload.get("transitionStates") or [])
        if str(item).strip()
    ]
    stability_wait_statuses = [
        str(item).strip()
        for item in list(signal_payload.get("stabilityWaitStatuses") or metadata_payload.get("stabilityWaitStatuses") or [])
        if str(item).strip()
    ]
    wait_sensitive_steps = int(
        signal_payload.get("waitSensitiveSteps")
        or metadata_payload.get("waitSensitiveSteps")
        or 0
    )
    loading_sensitive_steps = int(
        signal_payload.get("loadingSensitiveSteps")
        or metadata_payload.get("loadingSensitiveSteps")
        or 0
    )
    stability_wait_observed_steps = int(
        signal_payload.get("stabilityWaitObservedSteps")
        or metadata_payload.get("stabilityWaitObservedSteps")
        or 0
    )
    stability_wait_timeout_steps = int(
        signal_payload.get("stabilityWaitTimeoutSteps")
        or metadata_payload.get("stabilityWaitTimeoutSteps")
        or 0
    )
    budget_exceeded_steps = int(
        signal_payload.get("budgetExceededSteps")
        or metadata_payload.get("budgetExceededSteps")
        or 0
    )
    time_budget_exceeded_steps = int(
        signal_payload.get("timeBudgetExceededSteps")
        or metadata_payload.get("timeBudgetExceededSteps")
        or 0
    )
    max_settle_budget_ms = int(
        signal_payload.get("maxSettleBudgetMs")
        or metadata_payload.get("maxSettleBudgetMs")
        or 0
    )
    max_elapsed_ms = int(
        signal_payload.get("maxElapsedMs")
        or metadata_payload.get("maxElapsedMs")
        or 0
    )
    max_post_action_settle_timeout_ms = int(
        signal_payload.get("maxPostActionSettleTimeoutMs")
        or metadata_payload.get("maxPostActionSettleTimeoutMs")
        or 0
    )
    max_post_action_settle_poll_ms = int(
        signal_payload.get("maxPostActionSettlePollMs")
        or metadata_payload.get("maxPostActionSettlePollMs")
        or 0
    )
    max_post_action_stable_rounds = int(
        signal_payload.get("maxPostActionStableRounds")
        or metadata_payload.get("maxPostActionStableRounds")
        or 0
    )
    inferred_wait_sensitive = bool(
        wait_sensitive_steps > 0
        or loading_sensitive_steps > 0
        or stability_wait_observed_steps > 0
        or max_settle_budget_ms > 0
        or max_post_action_settle_timeout_ms > 0
        or max_post_action_settle_poll_ms > 0
        or max_post_action_stable_rounds > 0
    )
    if inferred_wait_sensitive and wait_sensitive_steps <= 0:
        wait_sensitive_steps = 1
    return {
        "waitSensitive": inferred_wait_sensitive,
        "waitSensitiveSteps": wait_sensitive_steps,
        "loadingSensitiveSteps": loading_sensitive_steps,
        "transitionStates": list(dict.fromkeys(transition_states)),
        "stabilityWaitObservedSteps": stability_wait_observed_steps,
        "stabilityWaitTimeoutSteps": stability_wait_timeout_steps,
        "stabilityWaitStatuses": list(dict.fromkeys(stability_wait_statuses)),
        "budgetExceededSteps": budget_exceeded_steps,
        "timeBudgetExceededSteps": time_budget_exceeded_steps,
        "maxSettleBudgetMs": max_settle_budget_ms,
        "maxElapsedMs": max_elapsed_ms,
        "maxPostActionSettleTimeoutMs": max_post_action_settle_timeout_ms,
        "maxPostActionSettlePollMs": max_post_action_settle_poll_ms,
        "maxPostActionStableRounds": max_post_action_stable_rounds,
    }


def draft_environment_signal_summary(
    gate: Dict[str, Any] | None = None,
    *,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    _ = dict(gate or {})
    metadata_payload = dict(metadata or {})
    nested_summary = dict(
        metadata_payload.get("environmentSignalSummary") or metadata_payload.get("templateEnvironmentSignalSummary") or {}
    )
    if nested_summary:
        metadata_payload = {**metadata_payload, **nested_summary}
    def _normalized_values(*values: Any) -> List[str]:
        normalized: List[str] = []
        for value in values:
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if item in (None, ""):
                        continue
                    text = str(item).strip()
                    if text:
                        normalized.append(text)
                continue
            if value in (None, ""):
                continue
            text = str(value).strip()
            if text:
                normalized.append(text)
        return normalized

    page_identities = _normalized_values(
        metadata_payload.get("pageIdentity"),
        metadata_payload.get("pageIdentities") or [],
    )
    affordances = _normalized_values(metadata_payload.get("affordances") or [])
    transition_states = _normalized_values(
        metadata_payload.get("transitionState"),
        metadata_payload.get("transitionStates") or [],
    )
    blocker_states = _normalized_values(
        metadata_payload.get("blockerState"),
        metadata_payload.get("blockerStates") or [],
    )
    dialog_confidence_levels = _normalized_values(
        metadata_payload.get("dialogConfidenceLevel"),
        metadata_payload.get("dialogConfidenceLevels") or [],
    )
    notification_requested = bool(metadata_payload.get("notificationSensingRequested"))
    sound_requested = bool(metadata_payload.get("soundSensingRequested"))
    notification_available = bool(metadata_payload.get("notificationSensingAvailable"))
    sound_available = bool(metadata_payload.get("soundSensingAvailable"))
    notification_modes = _normalized_values(
        metadata_payload.get("notificationSensingMode"),
        metadata_payload.get("notificationSensingModes") or [],
        nested_summary.get("notificationSensingMode"),
        nested_summary.get("notificationSensingModes") or [],
    )
    sound_modes = _normalized_values(
        metadata_payload.get("soundSensingMode"),
        metadata_payload.get("soundSensingModes") or [],
        nested_summary.get("soundSensingMode"),
        nested_summary.get("soundSensingModes") or [],
    )
    notification_mode = notification_modes[0].lower() if notification_modes else None
    sound_mode = sound_modes[0].lower() if sound_modes else None
    notification_observed = bool(
        metadata_payload.get("notificationObserved")
        or nested_summary.get("notificationObserved")
        or int(metadata_payload.get("notificationObservedSteps") or 0) > 0
        or int(nested_summary.get("notificationObservedSteps") or 0) > 0
    )
    sound_observed = bool(
        metadata_payload.get("soundObserved")
        or nested_summary.get("soundObserved")
        or int(metadata_payload.get("soundObservedSteps") or 0) > 0
        or int(nested_summary.get("soundObservedSteps") or 0) > 0
    )
    notification_candidate_count = int(
        metadata_payload.get("notificationCandidateCount")
        or metadata_payload.get("maxNotificationCandidateCount")
        or nested_summary.get("notificationCandidateCount")
        or nested_summary.get("maxNotificationCandidateCount")
        or 0
    )
    sound_active_session_count = int(
        metadata_payload.get("soundActiveSessionCount")
        or metadata_payload.get("maxSoundActiveSessionCount")
        or nested_summary.get("soundActiveSessionCount")
        or nested_summary.get("maxSoundActiveSessionCount")
        or 0
    )
    notification_providers = _normalized_values(metadata_payload.get("notificationSignalProviders") or [])
    sound_providers = _normalized_values(metadata_payload.get("soundSignalProviders") or [])
    ambient_observation_backed_steps = int(
        metadata_payload.get("ambientObservationBackedSteps")
        or metadata_payload.get("observationDrivenSteps")
        or 0
    )
    window_binding_verified_steps = int(metadata_payload.get("windowBindingVerifiedSteps") or 0)
    dialog_aware_steps = int(metadata_payload.get("dialogAwareSteps") or 0)
    focus_aware_steps = int(metadata_payload.get("focusAwareSteps") or 0)
    blocking_aware_steps = int(metadata_payload.get("blockingAwareSteps") or 0)
    transition_aware_steps = int(metadata_payload.get("transitionAwareSteps") or 0)
    desktop_environment_aware = bool(
        ambient_observation_backed_steps > 0
        or window_binding_verified_steps > 0
        or dialog_aware_steps > 0
        or focus_aware_steps > 0
        or blocking_aware_steps > 0
        or transition_aware_steps > 0
        or page_identities
        or affordances
        or transition_states
        or blocker_states
        or dialog_confidence_levels
        or notification_requested
        or sound_requested
        or notification_available
        or sound_available
        or notification_observed
        or sound_observed
    )
    return {
        "desktopEnvironmentAware": desktop_environment_aware,
        "ambientObservationBackedSteps": ambient_observation_backed_steps,
        "windowBindingVerifiedSteps": window_binding_verified_steps,
        "dialogAwareSteps": dialog_aware_steps,
        "focusAwareSteps": focus_aware_steps,
        "blockingAwareSteps": blocking_aware_steps,
        "transitionAwareSteps": transition_aware_steps,
        "pageIdentities": list(dict.fromkeys(page_identities)),
        "affordances": list(dict.fromkeys(affordances)),
        "transitionStates": list(dict.fromkeys(transition_states)),
        "blockerStates": list(dict.fromkeys(blocker_states)),
        "dialogConfidenceLevels": list(dict.fromkeys(dialog_confidence_levels)),
        "notificationSensingRequested": notification_requested,
        "notificationSensingAvailable": notification_available,
        "notificationSensingRequestedCount": 1 if notification_requested else 0,
        "notificationSensingAvailableCount": 1 if notification_available else 0,
        "notificationSensingMode": notification_mode,
        "notificationSensingModes": [item.lower() for item in dict.fromkeys(notification_modes)],
        "notificationSignalProviders": list(dict.fromkeys(notification_providers)),
        "notificationObserved": notification_observed,
        "notificationObservedSteps": 1 if notification_observed else 0,
        "notificationCandidateCount": notification_candidate_count,
        "maxNotificationCandidateCount": notification_candidate_count,
        "soundSensingRequested": sound_requested,
        "soundSensingAvailable": sound_available,
        "soundSensingRequestedCount": 1 if sound_requested else 0,
        "soundSensingAvailableCount": 1 if sound_available else 0,
        "soundSensingMode": sound_mode,
        "soundSensingModes": [item.lower() for item in dict.fromkeys(sound_modes)],
        "soundSignalProviders": list(dict.fromkeys(sound_providers)),
        "soundObserved": sound_observed,
        "soundObservedSteps": 1 if sound_observed else 0,
        "soundActiveSessionCount": sound_active_session_count,
        "maxSoundActiveSessionCount": sound_active_session_count,
    }
