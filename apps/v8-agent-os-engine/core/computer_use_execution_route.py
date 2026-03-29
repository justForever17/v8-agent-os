from __future__ import annotations

from typing import Any


def _normalized_app_payload(
    *,
    app_hint: str | None,
    resolved_app: dict[str, Any] | None,
    route: dict[str, Any],
) -> dict[str, Any]:
    return {
        "requested": app_hint,
        "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
        "appId": route.get("appId") or (resolved_app or {}).get("appId"),
    }


def _route_runtime(mode: str) -> str:
    if mode == "reuse_mode":
        return "rpa"
    return "computer_use"


def _route_high_level_tool(
    *,
    mode: str,
    app_hint: str | None,
    target_hint: str | None,
) -> str | None:
    if mode == "reuse_mode":
        return None
    if app_hint:
        return "computer_use_launch_app"
    if target_hint:
        return "computer_use_observe_scene"
    return "computer_use_observe_scene"


def _route_next_action_summary(
    *,
    mode: str,
    app_hint: str | None,
    target_hint: str | None,
) -> dict[str, Any]:
    runtime_kind = _route_runtime(mode)
    next_tool = _route_high_level_tool(mode=mode, app_hint=app_hint, target_hint=target_hint)
    if mode == "reuse_mode":
        summary = "已存在可复用肌肉记忆，应优先交给 RPA/模板主执行链。"
    elif mode == "hybrid_mode":
        summary = "已有部分可复用肌肉记忆，应以既有流程为骨架，由 Computer Use 局部补足。"
    else:
        summary = "未找到足够可信的肌肉记忆，应进入 Computer Use 学习模式。"
    return {
        "runtime": runtime_kind,
        "tool": next_tool,
        "summary": summary,
    }


def _compact_visual_signal_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(summary or {})
    return {
        "visualLocatorBacked": bool(payload.get("visualLocatorBacked")),
        "visualLocatorBackedSteps": int(payload.get("visualLocatorBackedSteps") or 0),
        "verifiedVisualLocatorSteps": int(payload.get("verifiedVisualLocatorSteps") or 0),
        "visualJudgeBacked": bool(payload.get("visualJudgeBacked")),
        "visualJudgeSteps": int(payload.get("visualJudgeSteps") or 0),
        "visualJudgeSelectedSteps": int(payload.get("visualJudgeSelectedSteps") or 0),
        "visualSemanticRoles": list(payload.get("visualSemanticRoles") or []),
        "visualLocatorProviders": list(payload.get("visualLocatorProviders") or []),
        "recentVisualAcceptanceStatus": payload.get("recentVisualAcceptanceStatus"),
        "recentVisualAcceptanceCount": int(payload.get("recentVisualAcceptanceCount") or 0),
        "recentVisualAcceptanceReadTextCaseCount": int(payload.get("recentVisualAcceptanceReadTextCaseCount") or 0),
        "recentVisualAcceptanceOcrEnhancedCaseCount": int(payload.get("recentVisualAcceptanceOcrEnhancedCaseCount") or 0),
    }


def _compact_timing_signal_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(summary or {})
    return {
        "waitSensitive": bool(payload.get("waitSensitive")),
        "waitSensitiveSteps": int(payload.get("waitSensitiveSteps") or 0),
        "loadingSensitiveSteps": int(payload.get("loadingSensitiveSteps") or 0),
        "transitionStates": list(payload.get("transitionStates") or []),
        "stabilityWaitObservedSteps": int(payload.get("stabilityWaitObservedSteps") or 0),
        "stabilityWaitTimeoutSteps": int(payload.get("stabilityWaitTimeoutSteps") or 0),
        "stabilityWaitStatuses": list(payload.get("stabilityWaitStatuses") or []),
        "budgetExceededSteps": int(payload.get("budgetExceededSteps") or 0),
        "timeBudgetExceededSteps": int(payload.get("timeBudgetExceededSteps") or 0),
        "maxSettleBudgetMs": int(payload.get("maxSettleBudgetMs") or 0),
        "maxElapsedMs": int(payload.get("maxElapsedMs") or 0),
        "maxPostActionSettleTimeoutMs": int(payload.get("maxPostActionSettleTimeoutMs") or 0),
        "maxPostActionSettlePollMs": int(payload.get("maxPostActionSettlePollMs") or 0),
        "maxPostActionStableRounds": int(payload.get("maxPostActionStableRounds") or 0),
    }


def _compact_match_signal_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(summary or {})
    notification_sensing_requested_count = int(payload.get("notificationSensingRequestedCount") or 0)
    notification_sensing_available_count = int(payload.get("notificationSensingAvailableCount") or 0)
    sound_sensing_requested_count = int(payload.get("soundSensingRequestedCount") or 0)
    sound_sensing_available_count = int(payload.get("soundSensingAvailableCount") or 0)
    notification_observed_count = int(payload.get("notificationObservedCount") or 0)
    sound_observed_count = int(payload.get("soundObservedCount") or 0)
    return {
        "total": int(payload.get("total") or 0),
        "visualLocatorBackedCount": int(payload.get("visualLocatorBackedCount") or 0),
        "visualJudgeBackedCount": int(payload.get("visualJudgeBackedCount") or 0),
        "waitSensitiveCount": int(payload.get("waitSensitiveCount") or 0),
        "loadingSensitiveCount": int(payload.get("loadingSensitiveCount") or 0),
        "visualSemanticRoles": list(payload.get("visualSemanticRoles") or []),
        "visualLocatorProviders": list(payload.get("visualLocatorProviders") or []),
        "transitionStates": list(payload.get("transitionStates") or []),
        "environmentAwareCount": int(payload.get("environmentAwareCount") or 0),
        "dialogAwareCount": int(payload.get("dialogAwareCount") or 0),
        "focusAwareCount": int(payload.get("focusAwareCount") or 0),
        "notificationAwareCount": int(payload.get("notificationAwareCount") or 0),
        "soundAwareCount": int(payload.get("soundAwareCount") or 0),
        "notificationSensingRequested": bool(payload.get("notificationSensingRequested"))
        or notification_sensing_requested_count > 0,
        "notificationSensingAvailable": bool(payload.get("notificationSensingAvailable"))
        or notification_sensing_available_count > 0,
        "soundSensingRequested": bool(payload.get("soundSensingRequested")) or sound_sensing_requested_count > 0,
        "soundSensingAvailable": bool(payload.get("soundSensingAvailable")) or sound_sensing_available_count > 0,
        "notificationObserved": bool(payload.get("notificationObserved")) or notification_observed_count > 0,
        "soundObserved": bool(payload.get("soundObserved")) or sound_observed_count > 0,
        "notificationSensingRequestedCount": notification_sensing_requested_count,
        "notificationSensingAvailableCount": notification_sensing_available_count,
        "soundSensingRequestedCount": sound_sensing_requested_count,
        "soundSensingAvailableCount": sound_sensing_available_count,
        "notificationObservedCount": notification_observed_count,
        "soundObservedCount": sound_observed_count,
        "notificationSignalProviders": list(payload.get("notificationSignalProviders") or []),
        "soundSignalProviders": list(payload.get("soundSignalProviders") or []),
        "notificationSensingModes": list(payload.get("notificationSensingModes") or []),
        "soundSensingModes": list(payload.get("soundSensingModes") or []),
        "maxNotificationCandidateCount": int(payload.get("maxNotificationCandidateCount") or 0),
        "maxSoundActiveSessionCount": int(payload.get("maxSoundActiveSessionCount") or 0),
        "pageIdentities": list(payload.get("pageIdentities") or []),
    }


def _compact_environment_signal_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(summary or {})
    notification_observed = bool(payload.get("notificationObserved") or int(payload.get("notificationObservedSteps") or 0) > 0)
    sound_observed = bool(payload.get("soundObserved") or int(payload.get("soundObservedSteps") or 0) > 0)
    return {
        "desktopEnvironmentAware": bool(payload.get("desktopEnvironmentAware")),
        "ambientObservationBackedSteps": int(payload.get("ambientObservationBackedSteps") or 0),
        "windowBindingVerifiedSteps": int(payload.get("windowBindingVerifiedSteps") or 0),
        "dialogAwareSteps": int(payload.get("dialogAwareSteps") or 0),
        "focusAwareSteps": int(payload.get("focusAwareSteps") or 0),
        "blockingAwareSteps": int(payload.get("blockingAwareSteps") or 0),
        "transitionAwareSteps": int(payload.get("transitionAwareSteps") or 0),
        "pageIdentities": list(payload.get("pageIdentities") or []),
        "affordances": list(payload.get("affordances") or []),
        "transitionStates": list(payload.get("transitionStates") or []),
        "blockerStates": list(payload.get("blockerStates") or []),
        "dialogConfidenceLevels": list(payload.get("dialogConfidenceLevels") or []),
        "notificationSensingRequested": bool(payload.get("notificationSensingRequested")),
        "notificationSensingAvailable": bool(payload.get("notificationSensingAvailable")),
        "notificationSensingRequestedCount": int(payload.get("notificationSensingRequestedCount") or 0),
        "notificationSensingAvailableCount": int(payload.get("notificationSensingAvailableCount") or 0),
        "notificationSensingMode": payload.get("notificationSensingMode"),
        "notificationSensingModes": list(payload.get("notificationSensingModes") or []),
        "notificationSignalProviders": list(payload.get("notificationSignalProviders") or []),
        "notificationObserved": notification_observed,
        "notificationObservedSteps": int(payload.get("notificationObservedSteps") or 0),
        "notificationCandidateCount": int(payload.get("notificationCandidateCount") or payload.get("maxNotificationCandidateCount") or 0),
        "maxNotificationCandidateCount": int(payload.get("maxNotificationCandidateCount") or 0),
        "soundSensingRequested": bool(payload.get("soundSensingRequested")),
        "soundSensingAvailable": bool(payload.get("soundSensingAvailable")),
        "soundSensingRequestedCount": int(payload.get("soundSensingRequestedCount") or 0),
        "soundSensingAvailableCount": int(payload.get("soundSensingAvailableCount") or 0),
        "soundSensingMode": payload.get("soundSensingMode"),
        "soundSensingModes": list(payload.get("soundSensingModes") or []),
        "soundSignalProviders": list(payload.get("soundSignalProviders") or []),
        "soundObserved": sound_observed,
        "soundObservedSteps": int(payload.get("soundObservedSteps") or 0),
        "soundActiveSessionCount": int(payload.get("soundActiveSessionCount") or payload.get("maxSoundActiveSessionCount") or 0),
        "maxSoundActiveSessionCount": int(payload.get("maxSoundActiveSessionCount") or 0),
    }


def build_compact_execution_route(
    *,
    action: str,
    goal: str,
    app_hint: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    route: dict[str, Any],
) -> dict[str, Any]:
    recommended_match = dict(route.get("recommendedMatch") or {})
    recommended_mode = str(route.get("recommendedMode") or "").strip() or "learn_mode"
    route_summary = dict(route.get("summary") or {})
    next_action = _route_next_action_summary(
        mode=recommended_mode,
        app_hint=app_hint,
        target_hint=target_hint,
    )
    recommended_tool = next_action.get("tool")
    recommended_tool_input = None
    if str(route.get("recommendedDraftId") or "").strip():
        recommended_tool = "rpa_run_draft"
        recommended_tool_input = {
            "script_id": route.get("recommendedDraftId"),
        }
    return {
        "ok": True,
        "action": action,
        "goal": goal,
        "target": {
            "requested": target_hint,
        },
        "app": _normalized_app_payload(
            app_hint=app_hint,
            resolved_app=resolved_app,
            route=route,
        ),
        "lookupMode": route.get("lookupMode"),
        "recommendedMode": recommended_mode,
        "recommendedModeLabel": route.get("recommendedModeLabel"),
        "recommendedAction": route.get("recommendedAction"),
        "recommendedActionLabel": route.get("recommendedActionLabel"),
        "recommendedRuntime": next_action.get("runtime"),
        "recommendedTool": recommended_tool,
        "recommendedToolSummary": next_action.get("summary"),
        "recommendedToolInput": recommended_tool_input,
        "requiresVariableBinding": bool(route.get("requiresVariableBinding")),
        "missingVariables": list(route.get("missingVariables") or []),
        "providedVariables": list(route.get("providedVariables") or []),
        "recommendedMatch": {
            "kind": recommended_match.get("kind"),
            "id": recommended_match.get("id"),
            "name": recommended_match.get("name"),
            "goal": recommended_match.get("goal"),
            "status": recommended_match.get("status"),
            "stage": recommended_match.get("stage"),
            "rolloutMode": recommended_match.get("rolloutMode"),
            "score": recommended_match.get("score"),
            "confidence": recommended_match.get("confidence"),
            "executionPath": recommended_match.get("executionPath"),
            "promotionGateStatus": recommended_match.get("promotionGateStatus"),
            "promotionGateBlocked": bool(recommended_match.get("promotionGateBlocked")),
            "promotionGateReasons": list(recommended_match.get("promotionGateReasons") or []),
            "promotionGateSignals": dict(recommended_match.get("promotionGateSignals") or {}),
            "visualSignalSummary": _compact_visual_signal_summary(
                recommended_match.get("visualSignalSummary")
            ),
            "timingSignalSummary": _compact_timing_signal_summary(
                recommended_match.get("timingSignalSummary")
            ),
            "environmentSignalSummary": _compact_environment_signal_summary(
                recommended_match.get("environmentSignalSummary")
            ),
            "missingVariables": list(recommended_match.get("missingVariables") or []),
            "reasons": list(recommended_match.get("reasons") or []),
        }
        if recommended_match
        else None,
        "summary": {
            **route_summary,
            "visualSignalSummary": _compact_visual_signal_summary(route_summary.get("visualSignalSummary")),
            "timingSignalSummary": _compact_timing_signal_summary(route_summary.get("timingSignalSummary")),
            "environmentSignalSummary": _compact_environment_signal_summary(route_summary.get("environmentSignalSummary")),
            "matchSignalSummary": _compact_match_signal_summary(route_summary.get("matchSignalSummary")),
        },
        "promotionGate": {
            "status": route_summary.get("promotionGateStatus"),
            "blocked": bool(route_summary.get("promotionGateBlocked")),
            "reasons": list(route_summary.get("promotionGateReasons") or []),
            "signals": dict(route_summary.get("promotionGateSignals") or {}),
            "visualSignalSummary": _compact_visual_signal_summary(route_summary.get("visualSignalSummary")),
            "environmentSignalSummary": _compact_environment_signal_summary(route_summary.get("environmentSignalSummary")),
        },
        "manualControls": dict(route.get("manualControls") or {}),
        "matches": [
            {
                "kind": item.get("kind"),
                "id": item.get("id"),
                "name": item.get("name"),
                "status": item.get("status"),
                "stage": item.get("stage"),
                "rolloutMode": item.get("rolloutMode"),
                "routeMode": item.get("routeMode"),
                "routeAction": item.get("routeAction"),
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "promotionGateStatus": item.get("promotionGateStatus"),
                "promotionGateBlocked": bool(item.get("promotionGateBlocked")),
                "promotionGateSignals": dict(item.get("promotionGateSignals") or {}),
                "visualSignalSummary": _compact_visual_signal_summary(item.get("visualSignalSummary")),
                "timingSignalSummary": _compact_timing_signal_summary(item.get("timingSignalSummary")),
                "environmentSignalSummary": _compact_environment_signal_summary(item.get("environmentSignalSummary")),
                "missingVariables": list(item.get("missingVariables") or []),
            }
            for item in list(route.get("matches") or [])[:5]
            if isinstance(item, dict)
        ],
    }
