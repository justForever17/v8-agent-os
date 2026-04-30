from __future__ import annotations

from typing import Any


def _step_samples(step_contracts: list[dict[str, Any]] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for step in list(step_contracts or [])[:limit]:
        if not isinstance(step, dict):
            continue
        samples.append(
            {
                "stepId": step.get("stepId") or step.get("id"),
                "action": step.get("action") or step.get("kind"),
                "status": step.get("status"),
                "target": step.get("target") or step.get("targetDescription"),
                "recommendedNextAction": step.get("recommendedNextAction"),
            }
        )
    return [item for item in samples if any(value not in (None, "", [], {}) for value in item.values())]


def _next_action(*, ok: bool, requires_human_attention: bool, step_contracts: list[dict[str, Any]] | None = None) -> str:
    for step in list(step_contracts or []):
        if not isinstance(step, dict):
            continue
        recommended = str(step.get("recommendedNextAction") or "").strip()
        if recommended and str(step.get("status") or "").strip().lower() != "completed":
            return recommended
    if ok:
        return "observe_scene_verify"
    if requires_human_attention:
        return "request_human_attention"
    return "resolve_route_then_retry"


def compact_execute_task_result(
    *,
    payload: dict[str, Any],
    execution_ready_mode: str,
    goal: str,
    app_hint: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    success_criteria: str | None,
) -> dict[str, Any]:
    execution_summary = dict(payload.get("executionSummary") or {})
    execution_payload = dict(payload.get("execution") or {})
    artifact_ids = [
        str((item or {}).get("artifactId") or (item or {}).get("id") or "").strip()
        for item in list(execution_payload.get("artifacts") or payload.get("artifacts") or [])
        if isinstance(item, dict)
    ]
    startup_readiness = execution_payload.get("startupReadiness")
    if startup_readiness is None:
        for step in list(execution_payload.get("steps") or []):
            if not isinstance(step, dict):
                continue
            result_payload = dict(step.get("result") or {})
            action_result = dict(result_payload.get("result") or result_payload)
            target = dict(action_result.get("target") or {})
            metadata = dict(action_result.get("metadata") or {})
            startup_readiness = target.get("startupReadiness") or metadata.get("startupReadiness")
            if startup_readiness:
                break
    contract_summary = dict(payload.get("contractSummary") or {})
    step_contracts = [dict(item) for item in list(contract_summary.get("steps") or []) if isinstance(item, dict)]
    ok = bool(payload.get("ok"))
    blocked_steps = int(execution_summary.get("blockedSteps") or 0)
    update_requested_steps = int(execution_summary.get("updateRequestedSteps") or 0)
    requires_human_attention = blocked_steps > 0 or update_requested_steps > 0
    failed_steps = int(execution_summary.get("failedSteps") or 0)
    requires_retry = not ok and not requires_human_attention
    if failed_steps and not ok:
        requires_retry = True
    summary = "已通过 ComputerUseRuntime 任务执行链完成桌面任务。" if ok else "ComputerUseRuntime 已完成本轮任务尝试，但当前结果仍需复查或重试。"
    return {
        "ok": ok,
        "runId": payload.get("runId") or execution_payload.get("runId"),
        "traceId": payload.get("traceId") or execution_payload.get("traceId"),
        "executionReadyMode": execution_ready_mode,
        "executedBy": "computer_use",
        "summary": summary,
        "selectedPlaybook": payload.get("selectedPlaybook") or execution_payload.get("selectedPlaybook"),
        "selectedPlaybookExecutor": payload.get("selectedPlaybookExecutor") or execution_payload.get("selectedPlaybookExecutor"),
        "factResolution": payload.get("factResolution") or execution_payload.get("factResolution"),
        "laneDecision": payload.get("laneDecision") or execution_payload.get("laneDecision"),
        "candidateAttempts": payload.get("candidateAttempts") or execution_payload.get("candidateAttempts"),
        "shortSequenceVerification": payload.get("shortSequenceVerification") or execution_payload.get("shortSequenceVerification"),
        "artifactIds": [item for item in artifact_ids if item][:8],
        "verification": {
            "passed": ok,
            "status": "completed" if ok else ("review_required" if requires_human_attention else "retry_required"),
            "successCriteria": str(success_criteria or "").strip() or None,
            "executionSummary": execution_summary,
            "visualSignalSummary": dict(payload.get("visualSignalSummary") or {}),
            "timingSignalSummary": dict(payload.get("timingSignalSummary") or {}),
            "environmentSignalSummary": dict(payload.get("environmentSignalSummary") or {}),
        },
        "evidence": {
            "goal": goal,
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId"),
            },
            "target": target_hint,
            "stepSamples": _step_samples(step_contracts),
        },
        "recommendedNextAction": _next_action(ok=ok, requires_human_attention=requires_human_attention, step_contracts=step_contracts),
        "startupReadiness": startup_readiness,
        "resourceLease": execution_payload.get("resourceLease"),
        "humanInputRequest": execution_payload.get("humanInputRequest"),
        "requiresRetry": requires_retry,
        "requiresHumanAttention": requires_human_attention,
    }
