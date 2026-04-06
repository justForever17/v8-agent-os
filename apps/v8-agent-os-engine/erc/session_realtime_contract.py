from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.database import db
from core.storage import storage


@dataclass(slots=True)
class AuthoritativeSessionRuntimeState:
    run_record: Optional[Dict[str, Any]]
    current_run_id: Optional[str]
    current_run: Optional[Dict[str, Any]]
    todos: Dict[str, Any]
    runtime_status: str


def build_current_run_view(run_record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not run_record:
        return None
    return {
        "id": run_record.get("id"),
        "session_id": run_record.get("session_id"),
        "status": run_record.get("status"),
        "started_at": run_record.get("started_at"),
        "finished_at": run_record.get("finished_at"),
        "trigger_source": run_record.get("trigger_source"),
        "metadata": dict(run_record.get("metadata") or {}),
    }


def derive_runtime_status(
    *,
    current_run: Optional[Dict[str, Any]],
    workflow_view: Dict[str, Any],
    lane_view: Dict[str, Any],
) -> str:
    run_status = str((current_run or {}).get("status") or "").strip()
    if run_status:
        return run_status

    lane_state = str(lane_view.get("state") or "").strip().lower()
    if lane_state == "active":
        return "running"
    if lane_state == "queued":
        return "queued"
    if lane_state == "rejected":
        return "failed"

    workflow_status = str(workflow_view.get("status") or "").strip()
    if workflow_status:
        return workflow_status
    return "idle"


def select_current_run_record(
    *,
    workflow_view: Dict[str, Any],
    lane_view: Dict[str, Any],
    runtime_events: list[Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    candidate_ids: list[str] = []
    for candidate in (
        lane_view.get("activeRunId"),
        lane_view.get("queuedRunId"),
        workflow_view.get("rootRunId"),
        runtime_events[-1].get("run_id") if runtime_events else None,
    ):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in candidate_ids:
            candidate_ids.append(normalized)

    for run_id in candidate_ids:
        run_record = db.get_run_record(run_id)
        if run_record:
            return run_record, run_id

    return None, candidate_ids[0] if candidate_ids else None


def build_todos_snapshot(*, session_id: str, run_id: Optional[str]) -> Dict[str, Any]:
    if not run_id:
        return {"items": [], "allCompleted": False, "runId": None, "sessionId": session_id}

    snapshot = storage.get_active_todo_snapshot(session_id=session_id, run_id=run_id) or {}
    items = snapshot.get("items")
    normalized_items = items if isinstance(items, list) else []
    return {
        "taskId": snapshot.get("taskId"),
        "taskName": snapshot.get("taskName"),
        "planMarkdown": snapshot.get("planMarkdown"),
        "runId": snapshot.get("runId") or run_id,
        "sessionId": snapshot.get("sessionId") or session_id,
        "createdAt": snapshot.get("createdAt"),
        "updatedAt": snapshot.get("updatedAt"),
        "isActive": bool(snapshot.get("isActive", False)),
        "isStale": bool(snapshot.get("isStale", False)),
        "allCompleted": bool(snapshot.get("allCompleted", False)),
        "items": normalized_items,
    }


def augment_workflow_projection(
    workflow_projection: Optional[Dict[str, Any]],
    *,
    todos: Dict[str, Any],
    current_run: Optional[Dict[str, Any]],
    runtime_status: str,
    latest_seq: int,
) -> Dict[str, Any]:
    record = dict(workflow_projection or {})
    record["todos"] = todos
    record["currentRun"] = current_run
    record["runtimeStatus"] = runtime_status
    record["latestSeq"] = latest_seq
    return record


def resolve_authoritative_session_runtime_state(
    *,
    session_id: str,
    workflow_view: Optional[Dict[str, Any]],
    lane_view: Optional[Dict[str, Any]],
    runtime_events: list[Dict[str, Any]],
) -> AuthoritativeSessionRuntimeState:
    normalized_workflow = workflow_view if isinstance(workflow_view, dict) else {}
    normalized_lane = lane_view if isinstance(lane_view, dict) else {}
    run_record, current_run_id = select_current_run_record(
        workflow_view=normalized_workflow,
        lane_view=normalized_lane,
        runtime_events=runtime_events,
    )
    current_run = build_current_run_view(run_record)
    resolved_run_id = current_run_id or (current_run or {}).get("id")
    todos = build_todos_snapshot(session_id=session_id, run_id=resolved_run_id)
    runtime_status = derive_runtime_status(
        current_run=current_run,
        workflow_view=normalized_workflow,
        lane_view=normalized_lane,
    )
    return AuthoritativeSessionRuntimeState(
        run_record=run_record,
        current_run_id=resolved_run_id,
        current_run=current_run,
        todos=todos,
        runtime_status=runtime_status,
    )
