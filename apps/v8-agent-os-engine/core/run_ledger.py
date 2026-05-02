from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.database import db
from core.observability_db import observability_db, redact_observability_text


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    return text or datetime.now(timezone.utc).isoformat()


def _sort_key(item: Dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("ts") or item.get("createdAt") or ""), str(item.get("id") or ""))


def _clip_summary(value: Any, limit: int = 700) -> str:
    text = redact_observability_text(str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "...[truncated]"


class RunLedgerService:
    """Read model that stitches existing runtime evidence into one run timeline.

    The service is intentionally non-authoritative: runtime storage remains in the
    existing tables, while run_ledger_events captures cross-system crumbs that do
    not fit a single runtime table.
    """

    def record_event(
        self,
        *,
        event_type: str,
        run_id: str | None = None,
        session_id: str | None = None,
        runtime_kind: str | None = None,
        source: str | None = None,
        summary: str | None = None,
        refs: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            return observability_db.add_run_ledger_event(
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "event_type": event_type,
                    "runtime_kind": runtime_kind,
                    "source": source or "engine",
                    "summary": summary or "",
                    "refs": refs or {},
                    "payload": payload or {},
                }
            )
        except Exception:
            # Ledger must never break the runtime path.
            return None

    def get_run_ledger(self, run_id: str) -> Dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id:
            raise ValueError("runId is required")
        run = db.get_run_record(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found")
        session_id = str(run.get("session_id") or "").strip() or None
        runtime_kind = str(run.get("run_type") or "").strip() or None

        timeline: list[dict[str, Any]] = []
        timeline.append(
            {
                "id": f"{run_id}:run_record:start",
                "type": "run.started",
                "source": "run_records",
                "runtimeKind": runtime_kind,
                "ts": _timestamp(run.get("started_at")),
                "summary": f"Run started as {runtime_kind or 'runtime'}",
                "refs": {"runId": run_id, "sessionId": session_id},
            }
        )
        if run.get("finished_at") or str(run.get("status") or "") in {"completed", "failed", "cancelled"}:
            status = str(run.get("status") or "unknown")
            timeline.append(
                {
                    "id": f"{run_id}:run_record:finish",
                    "type": f"run.{status}",
                    "source": "run_records",
                    "runtimeKind": runtime_kind,
                    "ts": _timestamp(run.get("finished_at") or run.get("started_at")),
                    "summary": _clip_summary(run.get("error_message") or f"Run status is {status}"),
                    "refs": {"runId": run_id, "sessionId": session_id},
                }
            )

        for event in db.get_runtime_events_for_run(run_id, session_id=session_id, limit=300):
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            topic = str(event.get("topic") or "")
            timeline.append(
                {
                    "id": event.get("id") or event.get("event_id") or f"{run_id}:runtime:{event.get('seq')}",
                    "type": topic or str(event.get("kind") or "runtime.event"),
                    "source": "runtime_events",
                    "runtimeKind": runtime_kind,
                    "ts": _timestamp(event.get("event_ts") or event.get("created_at")),
                    "summary": _clip_summary(payload.get("summary") or payload.get("status") or topic),
                    "refs": {"runId": run_id, "sessionId": session_id, "seq": event.get("seq")},
                    "payload": payload,
                }
            )

        for item in observability_db.list_run_ledger_events(run_id=run_id, limit=300).get("items", []):
            timeline.append(
                {
                    "id": item.get("id"),
                    "type": item.get("eventType"),
                    "source": item.get("source") or "run_ledger_events",
                    "runtimeKind": item.get("runtimeKind") or runtime_kind,
                    "ts": _timestamp(item.get("createdAt")),
                    "summary": _clip_summary(item.get("summary")),
                    "refs": item.get("refs") or {},
                    "payload": item.get("payload") or {},
                }
            )

        approvals = db.list_pending_approvals(run_id=run_id)
        for approval in approvals:
            request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            approval_id = approval.get("id")
            timeline.append(
                {
                    "id": f"{run_id}:approval:{approval_id}",
                    "type": f"approval.{approval.get('status') or 'pending'}",
                    "source": "pending_approvals",
                    "runtimeKind": runtime_kind,
                    "ts": _timestamp(approval.get("created_at") or approval.get("updated_at")),
                    "summary": _clip_summary(request.get("summary") or request.get("reason") or approval.get("approval_kind")),
                    "refs": {"runId": run_id, "sessionId": session_id, "approvalId": approval_id},
                    "payload": {
                        "approvalKind": approval.get("approval_kind"),
                        "status": approval.get("status"),
                    },
                }
            )

        observations = observability_db.list_tool_observation_records(run_id=run_id, limit=100).get("items", [])
        for obs in observations:
            timeline.append(
                {
                    "id": f"{run_id}:toolobs:{obs.get('id')}",
                    "type": "tool.observation",
                    "source": "tool_observation_records",
                    "runtimeKind": obs.get("runtimeKind") or runtime_kind,
                    "ts": _timestamp(obs.get("createdAt")),
                    "summary": _clip_summary(f"{obs.get('toolName') or 'tool'} output captured"),
                    "refs": {
                        "runId": run_id,
                        "sessionId": session_id,
                        "toolCallId": obs.get("toolCallId"),
                        "rawRef": obs.get("rawRef"),
                    },
                    "payload": {
                        "rawChars": obs.get("rawChars"),
                        "visibleChars": obs.get("visibleChars"),
                        "surface": obs.get("surface"),
                    },
                }
            )

        compactions = observability_db.list_conversation_compaction_records(run_id=run_id, limit=100).get("items", [])
        for compaction in compactions:
            timeline.append(
                {
                    "id": f"{run_id}:compaction:{compaction.get('id')}",
                    "type": "context.compaction_applied",
                    "source": "conversation_compaction_records",
                    "runtimeKind": compaction.get("runtime_kind") or runtime_kind,
                    "ts": _timestamp(compaction.get("createdAt")),
                    "summary": _clip_summary(
                        f"{compaction.get('summary_method') or 'summary'} saved ~{compaction.get('estimated_saved_tokens') or 0} tokens"
                    ),
                    "refs": {
                        "runId": run_id,
                        "sessionId": session_id,
                        "compactionRecordId": compaction.get("id"),
                        "baselineSnapshotRef": compaction.get("baseline_snapshot_ref"),
                    },
                    "payload": {
                        "targetRole": compaction.get("target_role"),
                        "triggerReason": compaction.get("trigger_reason"),
                        "coveredMessageCount": compaction.get("covered_message_count"),
                    },
                }
            )

        for artifact in db.list_runtime_artifacts(run_id=run_id, limit=100):
            artifact_id = artifact.get("id")
            timeline.append(
                {
                    "id": f"{run_id}:artifact:{artifact_id}",
                    "type": "artifact.produced",
                    "source": "runtime_artifacts",
                    "runtimeKind": runtime_kind,
                    "ts": _timestamp(artifact.get("created_at") or artifact.get("createdAt")),
                    "summary": _clip_summary(artifact.get("title") or artifact.get("artifactKind") or artifact_id),
                    "refs": {
                        "runId": run_id,
                        "sessionId": session_id,
                        "artifactId": artifact_id,
                    },
                    "payload": {
                        "kind": artifact.get("artifactKind") or artifact.get("artifact_kind"),
                        "mimeType": artifact.get("mimeType") or artifact.get("mime_type"),
                        "surfaceVisible": artifact.get("surfaceVisible"),
                    },
                }
            )

        workflow = db.get_workflow_ledger_for_run(run_id)
        if workflow:
            timeline.append(
                {
                    "id": f"{run_id}:workflow:{workflow.get('id')}",
                    "type": f"workflow.{workflow.get('status') or 'updated'}",
                    "source": "workflow_ledgers",
                    "runtimeKind": workflow.get("owner_runtime") or runtime_kind,
                    "ts": _timestamp(workflow.get("updated_at") or workflow.get("created_at")),
                    "summary": _clip_summary(workflow.get("last_error_message") or workflow.get("current_step_id") or workflow.get("status")),
                    "refs": {
                        "runId": run_id,
                        "sessionId": session_id,
                        "workflowId": workflow.get("id"),
                    },
                    "payload": {
                        "status": workflow.get("status"),
                        "recoverable": workflow.get("recoverable"),
                        "resumeStrategy": workflow.get("resume_strategy"),
                    },
                }
            )

        timeline.sort(key=_sort_key)
        refs = {
            "rawEvidenceRefs": sorted({str(item.get("refs", {}).get("rawRef")) for item in timeline if item.get("refs", {}).get("rawRef")}),
            "approvalRefs": sorted({str(item.get("refs", {}).get("approvalId")) for item in timeline if item.get("refs", {}).get("approvalId")}),
            "artifactRefs": sorted({str(item.get("refs", {}).get("artifactId")) for item in timeline if item.get("refs", {}).get("artifactId")}),
            "compactionRefs": sorted({str(item.get("refs", {}).get("compactionRecordId")) for item in timeline if item.get("refs", {}).get("compactionRecordId")}),
        }
        return {
            "runId": run_id,
            "sessionId": session_id,
            "runtimeKind": runtime_kind,
            "status": run.get("status"),
            "run": run,
            "timeline": timeline,
            "refs": refs,
            "finalStatus": run.get("status"),
            "nextAction": self._infer_next_action(run, approvals, timeline),
        }

    def list_ledgers(self, *, session_id: str | None = None, limit: int = 20) -> Dict[str, Any]:
        runs = db.list_run_records(session_id=session_id, limit=max(1, min(int(limit or 20), 100)))
        items = []
        for run in runs:
            run_id = str(run.get("id") or "")
            try:
                ledger = self.get_run_ledger(run_id)
                items.append(
                    {
                        "runId": run_id,
                        "sessionId": ledger.get("sessionId"),
                        "runtimeKind": ledger.get("runtimeKind"),
                        "status": ledger.get("status"),
                        "startedAt": run.get("started_at"),
                        "finishedAt": run.get("finished_at"),
                        "eventCount": len(ledger.get("timeline") or []),
                        "refs": ledger.get("refs") or {},
                        "nextAction": ledger.get("nextAction"),
                    }
                )
            except Exception:
                items.append(
                    {
                        "runId": run_id,
                        "sessionId": run.get("session_id"),
                        "runtimeKind": run.get("run_type"),
                        "status": run.get("status"),
                        "startedAt": run.get("started_at"),
                        "finishedAt": run.get("finished_at"),
                        "eventCount": 0,
                        "refs": {},
                        "nextAction": "inspect_run_record",
                    }
                )
        return {"items": items, "count": len(items)}

    @staticmethod
    def _infer_next_action(run: Dict[str, Any], approvals: List[Dict[str, Any]], timeline: List[Dict[str, Any]]) -> str:
        status = str(run.get("status") or "").strip()
        if status == "waiting_approval" or any(str(item.get("status") or "") == "pending" for item in approvals):
            return "review_pending_approval"
        if status == "waiting_external_tool":
            return "wait_for_external_tool_result_or_timeout"
        if status in {"failed", "cancelled"}:
            return "inspect_error_and_retry_or_close"
        if status in {"completed", "succeeded"}:
            return "done"
        if any(item.get("type") == "context.compaction_applied" for item in timeline):
            return "continue_with_compacted_context"
        return "observe"


run_ledger_service = RunLedgerService()
