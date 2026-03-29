from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.database import db
from core.runtime_projection import (
    build_projection_controls,
    build_projection_summary,
    build_recoverable_view,
    project_pending_approvals,
)


def _truncate_text(value: Any, limit: int = 120) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _compact_event_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key in (
        "message",
        "reason",
        "status",
        "question",
        "prompt",
        "approval_id",
        "toolCallId",
        "toolName",
        "kind",
    ):
        value = payload.get(key)
        if value not in (None, "", []):
            compact[key] = value

    if payload.get("content"):
        compact["content"] = _truncate_text(payload.get("content"))
    elif payload.get("delta"):
        compact["delta"] = _truncate_text(payload.get("delta"))

    request = payload.get("request")
    if isinstance(request, dict):
        request_summary = {
            "question": request.get("question") or request.get("prompt"),
            "toolCallId": request.get("toolCallId"),
        }
        request_summary = {k: v for k, v in request_summary.items() if v not in (None, "")}
        if request_summary:
            compact["request"] = request_summary

    response = payload.get("response")
    if isinstance(response, dict):
        response_summary = {
            "status": response.get("status"),
            "value": _truncate_text(response.get("value")),
            "message": _truncate_text(response.get("message")),
        }
        response_summary = {k: v for k, v in response_summary.items() if v not in (None, "")}
        if response_summary:
            compact["response"] = response_summary

    return compact


def _compact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    source = event.get("source") or {}
    return {
        "eventId": event.get("id") or event.get("event_id"),
        "seq": int(event.get("seq") or 0),
        "kind": event.get("kind"),
        "topic": event.get("topic"),
        "ts": event.get("event_ts") or event.get("ts") or event.get("created_at"),
        "source": {
            "component": source.get("component"),
            "node": source.get("node"),
            "agentId": source.get("agent_id"),
        },
        "payload": _compact_event_payload(dict(event.get("payload") or {})),
    }


class WorkflowProjectionService:
    def _resolve_workflow(self, *, workflow_id: str | None = None, run_id: str | None = None, session_id: str | None = None) -> Optional[Dict[str, Any]]:
        if workflow_id:
            return db.get_workflow_ledger(workflow_id)
        if run_id:
            return db.get_workflow_ledger_for_run(run_id)
        if session_id:
            workflow = db.get_latest_workflow_for_session(session_id)
            if workflow:
                workflow["metadata"] = (
                    workflow["metadata"]
                    if isinstance(workflow.get("metadata"), dict)
                    else {}
                )
                workflow["recoverable"] = bool(workflow.get("recoverable"))
            return workflow
        return None

    def _session_row(self, session_id: str) -> Dict[str, Any]:
        return db.get_session(session_id) or {"id": session_id, "title": "New Chat", "metadata": {}}

    def _snapshot_row(self, session_id: str) -> Optional[Dict[str, Any]]:
        return db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")

    def _approvals(self, session_id: str, run_id: Optional[str]) -> List[Dict[str, Any]]:
        return project_pending_approvals(
            db.list_pending_approvals(session_id=session_id, run_id=run_id, status="pending")
        )

    def _step_event_ranges(self, steps: List[Dict[str, Any]], run_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        projected_steps: List[Dict[str, Any]] = []
        previous_seq = 0
        latest_seq = int(run_events[-1].get("seq") or 0) if run_events else 0

        for index, step in enumerate(steps):
            step_last_seq = int(step.get("last_event_seq") or 0)
            is_last_step = index == len(steps) - 1
            upper_bound = step_last_seq or (latest_seq if is_last_step else 0)
            if upper_bound <= 0 and not is_last_step:
                step_events = []
            elif upper_bound <= 0 and is_last_step:
                step_events = [event for event in run_events if int(event.get("seq") or 0) > previous_seq]
            else:
                step_events = [
                    event
                    for event in run_events
                    if previous_seq < int(event.get("seq") or 0) <= upper_bound
                ]
                previous_seq = max(previous_seq, upper_bound)

            preview = {}
            projection = step.get("projection") or {}
            if isinstance(projection, dict):
                preview = projection.get("assistant_preview") or {}

            projected_steps.append(
                {
                    "stepId": step.get("id"),
                    "sequenceIndex": int(step.get("sequence_index") or 0),
                    "stepKey": step.get("step_key"),
                    "title": step.get("title"),
                    "status": step.get("status"),
                    "ownerRuntime": step.get("owner_runtime"),
                    "ownerAgentId": step.get("owner_agent_id"),
                    "approvalId": step.get("approval_id"),
                    "retryCount": int(step.get("retry_count") or 0),
                    "resumeToken": step.get("resume_token"),
                    "lastEventSeq": int(step.get("last_event_seq") or 0),
                    "lastErrorCode": step.get("last_error_code"),
                    "lastErrorMessage": step.get("last_error_message"),
                    "updatedAt": step.get("updated_at"),
                    "projection": {
                        "hasAssistantPreview": bool(
                            isinstance(preview, dict)
                            and (preview.get("content") or preview.get("reasoningContent"))
                        ),
                        "assistantPreview": preview if isinstance(preview, dict) else None,
                    },
                    "eventCount": len(step_events),
                    "recentEvents": [_compact_event(event) for event in step_events[-5:]],
                    "input": step.get("input"),
                    "output": step.get("output"),
                }
            )

        return projected_steps

    def build(
        self,
        *,
        workflow_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        include_event_tail: bool = True,
    ) -> Optional[Dict[str, Any]]:
        workflow = self._resolve_workflow(workflow_id=workflow_id, run_id=run_id, session_id=session_id)
        if not workflow:
            return None

        session_id = str(workflow.get("session_id") or session_id or "")
        run_id = str(workflow.get("root_run_id") or run_id or "")
        session_row = self._session_row(session_id)
        steps = db.get_workflow_steps(str(workflow["id"]))
        approvals = self._approvals(session_id, run_id)
        run_events = db.get_runtime_events_for_run(run_id, session_id=session_id)
        latest_seq = int(run_events[-1].get("seq") or 0) if run_events else 0
        snapshot_row = self._snapshot_row(session_id)
        snapshot = snapshot_row.get("snapshot") if snapshot_row else None

        current_step = next((step for step in steps if step.get("id") == workflow.get("current_step_id")), None)
        workflow_view = {
            "workflowId": workflow.get("id"),
            "rootRunId": run_id,
            "workflowKind": workflow.get("workflow_kind"),
            "status": workflow.get("status"),
            "recoverable": bool(workflow.get("recoverable")),
            "ownerRuntime": (current_step or {}).get("owner_runtime") or workflow.get("owner_runtime"),
            "ownerAgentId": (current_step or {}).get("owner_agent_id") or workflow.get("owner_agent_id"),
            "currentStepId": workflow.get("current_step_id"),
            "currentStepKey": (current_step or {}).get("step_key"),
            "currentStepTitle": (current_step or {}).get("title"),
            "currentStepStatus": (current_step or {}).get("status"),
            "updatedAt": workflow.get("updated_at"),
        }
        controls = build_projection_controls(workflow_view, approvals)
        recoverable = build_recoverable_view(workflow_view, controls)
        summary = build_projection_summary(
            session=session_row,
            snapshot=snapshot,
            workflow=workflow_view,
            approvals=approvals,
            latest_seq=latest_seq,
            source="workflow_truth_chain",
        )
        step_views = self._step_event_ranges(steps, run_events)
        event_tail = [_compact_event(event) for event in run_events[-12:]] if include_event_tail else []
        has_preview = any(
            (step.get("projection") or {}).get("assistant_preview")
            for step in steps
            if isinstance(step.get("projection"), dict)
        )
        truth_chain = {
            "reconstructable": bool(run_events or step_views or approvals or snapshot_row),
            "hasRuntimeEvents": bool(run_events),
            "hasSnapshot": snapshot_row is not None,
            "hasDurablePreview": has_preview or bool(summary.get("hasDurablePreview")),
            "stepCount": len(step_views),
            "latestSeq": latest_seq,
            "source": "workflow_truth_chain",
        }

        return {
            "sessionId": session_id,
            "workflow": workflow_view,
            "steps": step_views,
            "approvals": approvals,
            "controls": controls,
            "recoverable": recoverable,
            "summary": summary,
            "eventTail": event_tail,
            "truthChain": truth_chain,
            "snapshotMeta": {
                "source": "runtime_snapshot" if snapshot_row else None,
                "latestSeq": int(snapshot_row.get("latest_seq") or 0) if snapshot_row else 0,
                "hasSnapshot": snapshot_row is not None,
            },
        }


workflow_projection_service = WorkflowProjectionService()
