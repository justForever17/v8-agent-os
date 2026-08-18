from __future__ import annotations

from typing import Any, Dict, List, Optional


_SIDE_EFFECT_TOPICS = {
    "artifact.recorded",
    "tool.finished",
    "channel.outbound.sent",
    "channel.push.sent",
    "computer_use.step.executed",
    "extension.execution.completed",
    "side_effect.started",
    "side_effect.completed",
    "side_effect.failed",
    "side_effect.skipped_duplicate",
}


def _latest_event_for_topics(events: List[Dict[str, Any]], topics: set[str]) -> Optional[Dict[str, Any]]:
    for event in reversed(events):
        if str(event.get("topic") or "") in topics:
            return event
    return None


def build_liveness_view(
    *,
    run_record: Optional[Dict[str, Any]],
    workflow_view: Optional[Dict[str, Any]],
    runtime_events: List[Dict[str, Any]],
    lane_view: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    workflow_view = workflow_view or {}
    lane_view = lane_view or {}
    run_status = str((run_record or {}).get("status") or "").strip().lower()
    workflow_status = str(workflow_view.get("status") or "").strip().lower()
    latest_event = runtime_events[-1] if runtime_events else None
    latest_topic = str((latest_event or {}).get("topic") or "")
    last_progress_at = (
        (latest_event or {}).get("event_ts")
        or (latest_event or {}).get("ts")
        or (latest_event or {}).get("created_at")
        or (run_record or {}).get("started_at")
    )
    last_side_effect_event = _latest_event_for_topics(runtime_events, _SIDE_EFFECT_TOPICS)
    last_side_effect_at = (
        (last_side_effect_event or {}).get("event_ts")
        or (last_side_effect_event or {}).get("ts")
        or (last_side_effect_event or {}).get("created_at")
    )

    stalled = latest_topic in {"run.watchdog.stream_idle_timeout", "run.liveness.stalled"}
    blocked = bool(lane_view.get("state") == "queued")
    waiting = run_status in {"waiting_approval", "waiting_input", "waiting_external_tool", "paused"} or workflow_status in {
        "waiting_approval",
        "waiting_external_tool",
        "paused",
    }

    status = "idle"
    idle_reason = None
    blocked_reason = None
    watchdog_source = None

    if stalled:
        status = "stalled"
        idle_reason = "stream_idle_timeout"
        watchdog_source = "stream_watchdog"
    elif blocked:
        status = "blocked"
        blocked_reason = f"blocked_by:{lane_view.get('blockedByRunId') or lane_view.get('activeRunId') or 'unknown'}"
    elif waiting:
        status = "waiting"
        idle_reason = run_status or workflow_status or "waiting"
    elif run_status == "queued":
        status = "queued"
        idle_reason = "awaiting_admission"
    elif run_status == "running" or workflow_status in {"running", "created"}:
        status = "running"
    elif run_status in {"completed", "failed", "cancelled", "interrupted", "abandoned"}:
        status = "terminal"

    return {
        "runId": (run_record or {}).get("id") or workflow_view.get("rootRunId"),
        "status": status,
        "heartbeatKind": "runtime_projection",
        "lastProgressAt": last_progress_at,
        "lastSideEffectAt": last_side_effect_at,
        "idleReason": idle_reason,
        "watchdogSource": watchdog_source,
        "blockedReason": blocked_reason,
        "stalled": bool(stalled),
        "laneState": lane_view.get("state"),
        "workflowStatus": workflow_view.get("status"),
        "runStatus": (run_record or {}).get("status"),
    }
