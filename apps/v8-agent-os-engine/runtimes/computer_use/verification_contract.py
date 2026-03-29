from __future__ import annotations

from typing import Any, Dict, Mapping

from runtimes.computer_use.types import ComputerUseActionResult, ComputerUseVerification


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def recommended_next_action_payload(
    *,
    action_type: str,
    status: str,
    verification: ComputerUseVerification | Mapping[str, Any] | None,
    scene: Mapping[str, Any] | None = None,
    update_request: Mapping[str, Any] | None = None,
) -> str:
    verification_payload = (
        verification.as_dict() if isinstance(verification, ComputerUseVerification) else dict(verification or {})
    )
    scene_payload = dict(scene or {})
    if isinstance(update_request, Mapping) and update_request.get("requested"):
        return "request_ui_update"

    normalized_status = _lower(status)
    verification_status = _lower(verification_payload.get("status"))
    verification_level = _lower(verification_payload.get("level"))
    transition_state = _lower(scene_payload.get("transitionState"))
    blocker_state = _lower(scene_payload.get("blockerState"))

    if normalized_status in {"blocked", "update_requested"}:
        if verification_status == "window_binding_unresolved":
            return "ensure_window_then_retry"
        if verification_status == "pre_action_blocker_detected" or blocker_state not in {"", "none", "ready"}:
            return "resolve_blocker_then_retry"
        if verification_status in {
            "high_risk_visual_confirmation_required",
            "high_risk_pre_action_confirmation_required",
        }:
            return "observe_scene_then_retry"
        if verification_status in {
            "keyboard_focus_unconfirmed",
            "window_typing_focus_unconfirmed",
        } or (action_type == "type_text" and verification_level == "review_required"):
            return "focus_target_then_retry"
        return "reobserve_before_retry"

    if normalized_status == "completed" and verification_payload.get("passed") is True:
        if verification_status == "already_in_target_state":
            return "continue_without_repeating"
        return "continue"
    if transition_state in {"waiting", "loading"}:
        return "wait_and_reobserve"
    if verification_level == "review_required":
        return "observe_scene_then_retry"
    if verification_level == "failed":
        return "inspect_evidence_and_retry"
    return "inspect_evidence"


def blocked_reason_payload(
    *,
    status: str,
    verification: ComputerUseVerification | Mapping[str, Any] | None,
    update_request: Mapping[str, Any] | None = None,
    message: str | None = None,
) -> str | None:
    if isinstance(update_request, Mapping):
        reason = str(update_request.get("reason") or "").strip()
        if reason:
            return reason
    verification_payload = (
        verification.as_dict() if isinstance(verification, ComputerUseVerification) else dict(verification or {})
    )
    reason = str(verification_payload.get("reason") or "").strip()
    if reason:
        return reason
    if _lower(status) in {"blocked", "update_requested"}:
        return str(message or "").strip() or None
    return str(message or "").strip() or None


def build_evidence_summary_payload(
    *,
    message: str | None,
    observation: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    artifact: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    observation_payload = dict(observation or {})
    metadata_payload = dict(metadata or {})
    primary_visual_locator = dict(metadata_payload.get("visualLocator") or {})
    visual_observation = dict(primary_visual_locator.get("visualObservation") or {})
    visual_judge = dict(primary_visual_locator.get("visualJudge") or {})
    visual_semantic_candidates = [
        dict(item or {})
        for item in list(primary_visual_locator.get("visualSemanticCandidates") or [])[:6]
        if isinstance(item, dict)
    ]
    artifacts = []
    if isinstance(artifact, Mapping) and artifact:
        artifacts.append(dict(artifact))
    screenshot_artifact = observation_payload.get("screenshotArtifact")
    if isinstance(screenshot_artifact, Mapping) and screenshot_artifact:
        artifacts.append(dict(screenshot_artifact))
    evidence_summary = {
        "message": message,
        "artifacts": artifacts,
        "screenHash": observation_payload.get("screenHash"),
        "treeHash": observation_payload.get("treeHash"),
        "selectorStats": metadata_payload.get("selectorStats"),
        "stabilityWait": metadata_payload.get("stabilityWait"),
        "focusedElementId": observation_payload.get("focusedElementId"),
        "visualLocator": primary_visual_locator,
        "postActionVisualLocator": dict(metadata_payload.get("postActionVisualLocator") or {}),
        "startVisualLocator": dict(metadata_payload.get("startVisualLocator") or {}),
        "endVisualLocator": dict(metadata_payload.get("endVisualLocator") or {}),
        "visualObservation": visual_observation,
        "visualJudge": visual_judge,
        "visualSemanticCandidates": visual_semantic_candidates,
        "visualDecision": {
            "role": visual_observation.get("role"),
            "candidateCount": visual_observation.get("candidateCount"),
            "ambiguityLevel": visual_observation.get("ambiguityLevel"),
            "judgeDecision": visual_judge.get("decision"),
            "judgeConfidence": visual_judge.get("confidence"),
        },
    }
    evidence_summary["visualSignalSummary"] = build_visual_signal_summary_payload(
        metadata=metadata_payload,
        visual_decision=evidence_summary.get("visualDecision"),
        verification=None,
        evidence_summary=evidence_summary,
    )
    evidence_summary["timingSignalSummary"] = build_timing_signal_summary_payload(
        metadata=metadata_payload,
        scene=metadata_payload.get("scene") or {},
        evidence_summary=evidence_summary,
    )
    return evidence_summary


def build_visual_signal_summary_payload(
    *,
    metadata: Mapping[str, Any] | None,
    visual_decision: Mapping[str, Any] | None,
    verification: ComputerUseVerification | Mapping[str, Any] | None,
    evidence_summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    metadata_payload = dict(metadata or {})
    evidence_payload = dict(evidence_summary or {})
    verification_payload = (
        verification.as_dict() if isinstance(verification, ComputerUseVerification) else dict(verification or {})
    )
    primary_visual_locator = dict(metadata_payload.get("visualLocator") or evidence_payload.get("visualLocator") or {})
    post_action_visual_locator = dict(
        metadata_payload.get("postActionVisualLocator") or evidence_payload.get("postActionVisualLocator") or {}
    )
    start_visual_locator = dict(metadata_payload.get("startVisualLocator") or evidence_payload.get("startVisualLocator") or {})
    end_visual_locator = dict(metadata_payload.get("endVisualLocator") or evidence_payload.get("endVisualLocator") or {})
    visual_decision_payload = dict(
        visual_decision or metadata_payload.get("visualDecision") or evidence_payload.get("visualDecision") or {}
    )
    locator_backed = bool(
        primary_visual_locator or post_action_visual_locator or start_visual_locator or end_visual_locator
    )
    providers = list(
        dict.fromkeys(
            provider
            for provider in [
                str(primary_visual_locator.get("providerId") or "").strip(),
                str(post_action_visual_locator.get("providerId") or "").strip(),
                str(start_visual_locator.get("providerId") or "").strip(),
                str(end_visual_locator.get("providerId") or "").strip(),
            ]
            if provider
        )
    )
    role = str(visual_decision_payload.get("role") or "").strip()
    judge_decision = str(visual_decision_payload.get("judgeDecision") or "").strip().lower()
    verification_level = str(verification_payload.get("level") or "").strip().lower()
    return {
        "visualLocatorBacked": locator_backed,
        "visualLocatorBackedSteps": 1 if locator_backed else 0,
        "verifiedVisualLocatorSteps": 1 if locator_backed and verification_level == "verified" else 0,
        "visualJudgeBacked": bool(judge_decision),
        "visualJudgeSteps": 1 if judge_decision else 0,
        "visualJudgeSelectedSteps": 1 if judge_decision == "candidate" else 0,
        "visualSemanticRoles": [role] if role else [],
        "visualLocatorProviders": providers,
    }


def build_timing_signal_summary_payload(
    *,
    metadata: Mapping[str, Any] | None,
    scene: Mapping[str, Any] | None,
    evidence_summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    metadata_payload = dict(metadata or {})
    scene_payload = dict(scene or {})
    evidence_payload = dict(evidence_summary or {})
    budget_payload = dict(metadata_payload.get("budget") or {})
    stability_wait = dict(metadata_payload.get("stabilityWait") or evidence_payload.get("stabilityWait") or {})
    transition_state = str(scene_payload.get("transitionState") or "").strip().lower()
    stability_wait_status = str(stability_wait.get("status") or "").strip().lower()
    settle_timeout_ms = int(
        metadata_payload.get("post_action_settle_timeout_ms")
        or metadata_payload.get("postActionSettleTimeoutMs")
        or 0
    )
    settle_poll_ms = int(
        metadata_payload.get("post_action_settle_poll_ms")
        or metadata_payload.get("postActionSettlePollMs")
        or 0
    )
    stable_rounds = int(
        metadata_payload.get("post_action_stable_rounds")
        or metadata_payload.get("postActionStableRounds")
        or 0
    )
    exceeded = {
        str(item).strip().lower()
        for item in list(budget_payload.get("exceeded") or [])
        if str(item).strip()
    }
    wait_sensitive = bool(
        settle_timeout_ms > 0
        or settle_poll_ms > 0
        or stable_rounds > 0
        or stability_wait
        or transition_state in {"waiting", "loading", "waiting_for_transition"}
    )
    return {
        "waitSensitive": wait_sensitive,
        "waitSensitiveSteps": 1 if wait_sensitive else 0,
        "loadingSensitiveSteps": 1 if transition_state in {"waiting", "loading", "waiting_for_transition"} else 0,
        "transitionStates": [transition_state] if transition_state else [],
        "stabilityWaitObservedSteps": 1 if stability_wait else 0,
        "stabilityWaitTimeoutSteps": 1 if stability_wait_status == "timeout" else 0,
        "stabilityWaitStatuses": [stability_wait_status] if stability_wait_status else [],
        "budgetExceededSteps": 1 if budget_payload.get("withinBudget") is False else 0,
        "timeBudgetExceededSteps": 1 if "time" in exceeded else 0,
        "maxSettleBudgetMs": int(budget_payload.get("settleBudgetMs") or 0),
        "maxElapsedMs": int(budget_payload.get("elapsedMs") or 0),
        "maxPostActionSettleTimeoutMs": settle_timeout_ms,
        "maxPostActionSettlePollMs": settle_poll_ms,
        "maxPostActionStableRounds": stable_rounds,
    }


def build_environment_signal_summary_payload(
    *,
    metadata: Mapping[str, Any] | None,
    observation: Mapping[str, Any] | None,
    evidence_summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    metadata_payload = dict(metadata or {})
    observation_payload = dict(observation or {})
    evidence_payload = dict(evidence_summary or {})
    scene_payload = dict(
        metadata_payload.get("scene")
        or evidence_payload.get("sceneAssessment")
        or observation_payload.get("sceneAssessment")
        or {}
    )
    binding_payload = dict(
        metadata_payload.get("bindingAssessment")
        or evidence_payload.get("bindingAssessment")
        or observation_payload.get("bindingAssessment")
        or {}
    )
    visual_observation = dict(
        metadata_payload.get("visualObservation")
        or evidence_payload.get("visualObservation")
        or dict(metadata_payload.get("visualLocator") or {}).get("visualObservation")
        or {}
    )
    blocker_state = str(scene_payload.get("blockerState") or "").strip().lower()
    transition_state = str(scene_payload.get("transitionState") or "").strip().lower()
    scene_confidence = str(
        scene_payload.get("confidence")
        or metadata_payload.get("pageIdentityConfidence")
        or ""
    ).strip().lower()
    binding_status = str(binding_payload.get("status") or "").strip().lower()
    binding_confidence = str(binding_payload.get("confidence") or "").strip().lower()
    affordances = [
        str(item).strip()
        for item in list(scene_payload.get("affordances") or [])
        if str(item).strip()
    ]
    screen_hash = str(observation_payload.get("screenHash") or "").strip()
    tree_hash = str(observation_payload.get("treeHash") or "").strip()
    focused_element_id = str(observation_payload.get("focusedElementId") or "").strip()
    dialog_detected = bool(visual_observation.get("dialogDetected"))
    dialog_confidence_level = str(visual_observation.get("dialogConfidenceLevel") or "").strip().lower()
    dialog_suppressed = bool(visual_observation.get("dialogSuppressed"))

    probe_payload = dict(
        metadata_payload.get("environmentProbe")
        or metadata_payload.get("ambientProbe")
        or evidence_payload.get("environmentProbe")
        or {}
    )

    def _normalized_probe_values(*values: Any) -> list[str]:
        normalized: list[str] = []
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

    notification_probe = dict(probe_payload.get("notification") or {})
    sound_probe = dict(probe_payload.get("sound") or {})
    notification_requested = bool(notification_probe.get("requested"))
    sound_requested = bool(sound_probe.get("requested"))
    notification_available = bool(notification_probe.get("available"))
    sound_available = bool(sound_probe.get("available"))
    notification_observed = bool(notification_probe.get("observed"))
    sound_observed = bool(sound_probe.get("observed"))
    notification_mode = str(notification_probe.get("mode") or "").strip().lower() or None
    sound_mode = str(sound_probe.get("mode") or "").strip().lower() or None
    notification_candidate_count = int(notification_probe.get("candidateCount") or 0)
    sound_active_session_count = int(sound_probe.get("activeSessionCount") or 0)
    notification_providers = list(
        dict.fromkeys(
            _normalized_probe_values(
                notification_probe.get("provider"),
                notification_probe.get("providers") or [],
            )
        )
    )
    sound_providers = list(
        dict.fromkeys(
            _normalized_probe_values(
                sound_probe.get("provider"),
                sound_probe.get("providers") or [],
            )
        )
    )
    page_identity = str(
        scene_payload.get("pageIdentity")
        or metadata_payload.get("pageIdentity")
        or evidence_payload.get("pageIdentity")
        or ""
    ).strip()
    observation_driven = bool(
        screen_hash
        or tree_hash
        or focused_element_id
        or scene_payload
        or binding_payload
        or visual_observation
    )
    window_binding_verified = binding_status in {"verified", "bound", "matched"} or binding_confidence == "high"
    ambient_change_observed = bool(
        dialog_detected
        or blocker_state not in {"", "none", "ready"}
        or transition_state not in {"", "observed", "stable", "ready", "none"}
    )
    return {
        "observationDriven": observation_driven,
        "desktopEnvironmentAware": observation_driven,
        "windowBindingStatus": binding_status or None,
        "windowBindingConfidence": binding_confidence or None,
        "windowBindingVerified": window_binding_verified,
        "windowBindingRequiresUpdateRequest": bool(binding_payload.get("requiresUpdateRequest")),
        "pageIdentity": page_identity or None,
        "sceneConfidence": scene_confidence or None,
        "blockerState": blocker_state or None,
        "transitionState": transition_state or None,
        "affordances": list(dict.fromkeys(affordances)),
        "dialogDetected": dialog_detected,
        "dialogConfidenceLevel": dialog_confidence_level or None,
        "dialogSuppressed": dialog_suppressed,
        "focusKnown": bool(focused_element_id),
        "focusedElementPresent": bool(focused_element_id),
        "elementCount": len(list(observation_payload.get("elements") or [])),
        "screenObserved": bool(screen_hash),
        "treeObserved": bool(tree_hash),
        "ambientChangeObserved": ambient_change_observed,
        "notificationSensingRequested": notification_requested,
        "notificationSensingAvailable": notification_available,
        "notificationSensingRequestedCount": 1 if notification_requested else 0,
        "notificationSensingAvailableCount": 1 if notification_available else 0,
        "notificationSensingMode": notification_mode,
        "notificationSensingModes": [notification_mode] if notification_mode else [],
        "notificationSignalProviders": notification_providers,
        "notificationObserved": notification_observed,
        "notificationCandidateCount": notification_candidate_count,
        "soundSensingRequested": sound_requested,
        "soundSensingAvailable": sound_available,
        "soundSensingRequestedCount": 1 if sound_requested else 0,
        "soundSensingAvailableCount": 1 if sound_available else 0,
        "soundSensingMode": sound_mode,
        "soundSensingModes": [sound_mode] if sound_mode else [],
        "soundSignalProviders": sound_providers,
        "soundObserved": sound_observed,
        "soundActiveSessionCount": sound_active_session_count,
    }


def learning_loop_summary_payload(
    *,
    execution_mode: str,
    status: str,
    verification: ComputerUseVerification | Mapping[str, Any] | None,
    update_request: Mapping[str, Any] | None,
    recommended_next_action: str,
) -> Dict[str, Any]:
    verification_payload = (
        verification.as_dict() if isinstance(verification, ComputerUseVerification) else dict(verification or {})
    )
    return {
        "mode": execution_mode,
        "modeLabel": {
            "reuse_mode": "复用模式",
            "hybrid_mode": "混合模式",
            "learn_mode": "学习模式",
        }.get(execution_mode, execution_mode),
        "phaseOrder": ["observe", "act", "verify"],
        "observationDriven": True,
        "verified": bool(verification_payload.get("passed")),
        "blocked": _lower(status) in {"blocked", "update_requested"},
        "requiresUpdateRequest": bool(isinstance(update_request, Mapping) and update_request.get("requested")),
        "recommendedNextAction": recommended_next_action,
    }


def build_runtime_control_payload(
    *,
    status: str,
    update_request: Mapping[str, Any] | None,
    recommended_next_action: str,
    primitive_live_baseline: Mapping[str, Any] | None = None,
    visual_decision: Mapping[str, Any] | None = None,
    environment_signal_summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "blocked": bool(_lower(status) in {"blocked", "update_requested"}),
        "requiresUpdateRequest": bool(isinstance(update_request, Mapping) and update_request.get("requested")),
        "recommendedNextAction": recommended_next_action,
        "primitiveLiveBaseline": dict(primitive_live_baseline or {}),
        "visualDecision": dict(visual_decision or {}),
        "environmentSignalSummary": dict(environment_signal_summary or {}),
    }


def build_result_contract(
    *,
    action_type: str,
    execution_mode: str,
    result: ComputerUseActionResult,
    verification: ComputerUseVerification,
    update_request: Mapping[str, Any] | None,
    primitive_live_baseline: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    scene_payload = dict(result.metadata.get("scene") or {}) if isinstance(result.metadata, dict) else {}
    observation_payload = result.observation.as_dict() if result.observation else {}
    metadata_payload = dict(result.metadata or {})
    artifact_payload = dict(result.artifact or {}) if result.artifact else None
    evidence_summary = build_evidence_summary_payload(
        message=result.message,
        observation=observation_payload,
        metadata=metadata_payload,
        artifact=artifact_payload,
    )
    visual_decision = dict(evidence_summary.get("visualDecision") or {})
    visual_signal_summary = build_visual_signal_summary_payload(
        metadata=metadata_payload,
        visual_decision=visual_decision,
        verification=verification,
        evidence_summary=evidence_summary,
    )
    timing_signal_summary = build_timing_signal_summary_payload(
        metadata=metadata_payload,
        scene=scene_payload,
        evidence_summary=evidence_summary,
    )
    evidence_summary["visualSignalSummary"] = dict(visual_signal_summary)
    evidence_summary["timingSignalSummary"] = dict(timing_signal_summary)
    environment_signal_summary = build_environment_signal_summary_payload(
        metadata=metadata_payload,
        observation=observation_payload,
        evidence_summary=evidence_summary,
    )
    evidence_summary["environmentSignalSummary"] = dict(environment_signal_summary)
    recommended_next_action = recommended_next_action_payload(
        action_type=action_type,
        status=result.status,
        verification=verification,
        scene=scene_payload,
        update_request=update_request,
    )
    return {
        "blockedReason": blocked_reason_payload(
            status=result.status,
            verification=verification,
            update_request=update_request,
            message=result.message,
        ),
        "recommendedNextAction": recommended_next_action,
        "evidenceSummary": evidence_summary,
        "runtimeControl": build_runtime_control_payload(
            status=result.status,
            update_request=update_request,
            recommended_next_action=recommended_next_action,
            primitive_live_baseline=primitive_live_baseline,
            visual_decision=visual_decision,
            environment_signal_summary=environment_signal_summary,
        ),
        "learningLoop": learning_loop_summary_payload(
            execution_mode=execution_mode,
            status=result.status,
            verification=verification,
            update_request=update_request,
            recommended_next_action=recommended_next_action,
        ),
        "visualDecision": visual_decision,
        "visualSignalSummary": visual_signal_summary,
        "timingSignalSummary": timing_signal_summary,
        "environmentSignalSummary": environment_signal_summary,
    }
