from __future__ import annotations

import uuid

from core.context_governance import extract_latest_context_governance
from core.database import db
from core.runtime_projection import (
    apply_projection_overlay,
    build_chat_projection_snapshot,
    build_projection_controls,
    build_projection_summary,
    build_recoverable_view,
    project_runtime_timeline_from_events,
    project_pending_approvals,
)
from erc.liveness_projection import build_liveness_view
from erc.recovery_policy import derive_recovery_class
from erc.session_realtime_contract import (
    augment_workflow_projection,
    build_context_references,
    build_processes_snapshot,
    resolve_authoritative_session_runtime_state,
)
from erc.session_admission_service import session_admission_service
from erc.workflow_ledger import workflow_ledger_service
from erc.workflow_projection import workflow_projection_service


class SnapshotService:
    def record_runtime_snapshot(
        self,
        *,
        session_id: str,
        snapshot_type: str,
        snapshot: Dict,
        run_id: Optional[str] = None,
        latest_seq: Optional[int] = None,
    ) -> Dict:
        db.add_runtime_snapshot(
            snapshot_id=f"snap_{uuid.uuid4().hex}",
            session_id=session_id,
            run_id=run_id,
            latest_seq=int(latest_seq if latest_seq is not None else db.get_latest_runtime_seq(session_id)),
            snapshot_type=snapshot_type,
            snapshot=snapshot,
        )
        return snapshot

    def refresh_chat_projection(self, session_id: str, *, run_id: Optional[str] = None) -> Dict:
        runtime_events = db.get_runtime_events(session_id)
        snapshot = build_chat_projection_snapshot(session_id, runtime_events)
        snapshot = apply_projection_overlay(snapshot, workflow_ledger_service.get_session_projection_overlay(session_id))
        self.record_runtime_snapshot(
            session_id=session_id,
            run_id=run_id or (runtime_events[-1].get("run_id") if runtime_events else None),
            latest_seq=snapshot["latest_seq"],
            snapshot_type="chat_projection",
            snapshot=snapshot,
        )
        return snapshot

    def ensure_chat_projection_row(self, session_id: str) -> Optional[Dict]:
        snapshot_row = db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")
        latest_runtime_seq = db.get_latest_runtime_seq(session_id)
        if (
            not snapshot_row
            or int(snapshot_row.get("latest_seq") or 0) < latest_runtime_seq
        ):
            runtime_events = db.get_runtime_events(session_id)
            if runtime_events:
                self.refresh_chat_projection(
                    session_id,
                    run_id=runtime_events[-1].get("run_id"),
                )
                snapshot_row = db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")
        elif snapshot_row:
            snapshot_row["snapshot"] = apply_projection_overlay(
                snapshot_row.get("snapshot", {}),
                workflow_ledger_service.get_session_projection_overlay(session_id),
            )
        return snapshot_row

    def build_chat_projection_payload(self, session_id: str) -> Dict:
        snapshot_row = self.ensure_chat_projection_row(session_id)
        session_row = db.get_session(session_id) or {"id": session_id, "title": "New Chat", "metadata": {}}
        workflow_view = workflow_ledger_service.get_session_workflow_view(session_id)
        pending_approvals = project_pending_approvals(
            db.list_pending_approvals(session_id=session_id, status="pending")
        )
        runtime_events = db.get_runtime_events(session_id)
        runtime_timeline = project_runtime_timeline_from_events(runtime_events)
        context_governance = extract_latest_context_governance(runtime_events)
        lane_view = session_admission_service.get_lane_view(session_id)
        session_runtime = resolve_authoritative_session_runtime_state(
            session_id=session_id,
            workflow_view=workflow_view,
            lane_view=lane_view,
            runtime_events=runtime_events,
        )
        run_record = session_runtime.run_record
        current_run = session_runtime.current_run
        todos = session_runtime.todos
        runtime_status = session_runtime.runtime_status
        recovery_class = derive_recovery_class(run_record, workflow_view=workflow_view)
        liveness = build_liveness_view(
            run_record=run_record,
            workflow_view=workflow_view,
            runtime_events=runtime_events,
            lane_view=lane_view,
        )
        if not snapshot_row:
            overlay = workflow_ledger_service.get_session_projection_overlay(session_id)
            source = "projection_overlay"
            workflow_projection = augment_workflow_projection(
                workflow_projection_service.build(session_id=session_id),
                todos=todos,
                current_run=current_run,
                runtime_status=runtime_status,
                latest_seq=0,
            )
            if not overlay:
                controls = build_projection_controls(workflow_view, pending_approvals)
                return {
                    "session_id": session_id,
                    "snapshot": None,
                    "latestSeq": 0,
                    "runtimeTimeline": runtime_timeline,
                    "workflow": workflow_view,
                    "workflowProjection": workflow_projection,
                    "approvals": pending_approvals,
                    "controls": controls,
                    "recoverable": build_recoverable_view(workflow_view, controls),
                    "todos": todos,
                    "processes": [],
                    "contextReferences": [],
                    "currentRun": current_run,
                    "runtimeStatus": runtime_status,
                    "summary": build_projection_summary(
                        session=session_row,
                        snapshot=None,
                        workflow=workflow_view,
                        approvals=pending_approvals,
                        latest_seq=0,
                        source=source,
                    ),
                    "source": source,
                    "contextGovernance": context_governance,
                    "lane": lane_view,
                    "liveness": liveness,
                    "recoveryClass": recovery_class,
                }
            base_snapshot = {"session_id": session_id, "latest_seq": 0, "messages": [], "artifacts": []}
            snapshot = apply_projection_overlay(base_snapshot, overlay)
            latest_seq = int(overlay.get("lastEventSeq") or 0)
            workflow_projection = augment_workflow_projection(
                workflow_projection,
                todos=todos,
                current_run=current_run,
                runtime_status=runtime_status,
                latest_seq=latest_seq,
            )
            controls = build_projection_controls(workflow_view, pending_approvals)
            return {
                "session_id": session_id,
                "snapshot": snapshot,
                "latestSeq": latest_seq,
                "runtimeTimeline": runtime_timeline,
                "workflow": workflow_view,
                "workflowProjection": workflow_projection,
                "approvals": pending_approvals,
                "controls": controls,
                "recoverable": build_recoverable_view(workflow_view, controls),
                "todos": todos,
                "processes": build_processes_snapshot(session_id=session_id, snapshot=snapshot, run_id=session_runtime.current_run_id),
                "contextReferences": build_context_references(snapshot),
                "currentRun": current_run,
                "runtimeStatus": runtime_status,
                "summary": build_projection_summary(
                    session=session_row,
                    snapshot=snapshot,
                    workflow=workflow_view,
                    approvals=pending_approvals,
                    latest_seq=latest_seq,
                    source=source,
                ),
                "source": source,
                "contextGovernance": context_governance,
                "lane": lane_view,
                "liveness": liveness,
                "recoveryClass": recovery_class,
            }
        snapshot = snapshot_row.get("snapshot", {})
        latest_seq = int(snapshot_row.get("latest_seq", 0) or 0)
        controls = build_projection_controls(workflow_view, pending_approvals)
        workflow_projection = augment_workflow_projection(
            workflow_projection_service.build(session_id=session_id),
            todos=todos,
            current_run=current_run,
            runtime_status=runtime_status,
            latest_seq=latest_seq,
        )
        return {
            "session_id": session_id,
            "snapshot": snapshot,
            "latestSeq": latest_seq,
            "runtimeTimeline": runtime_timeline,
            "workflow": workflow_view,
            "workflowProjection": workflow_projection,
            "approvals": pending_approvals,
            "controls": controls,
            "recoverable": build_recoverable_view(workflow_view, controls),
            "todos": todos,
            "processes": build_processes_snapshot(session_id=session_id, snapshot=snapshot, run_id=session_runtime.current_run_id),
            "contextReferences": build_context_references(snapshot),
            "currentRun": current_run,
            "runtimeStatus": runtime_status,
            "summary": build_projection_summary(
                session=session_row,
                snapshot=snapshot,
                workflow=workflow_view,
                approvals=pending_approvals,
                latest_seq=latest_seq,
                source="runtime_snapshot",
            ),
            "source": "runtime_snapshot",
            "contextGovernance": context_governance,
            "lane": lane_view,
            "liveness": liveness,
            "recoveryClass": recovery_class,
        }

    def get_latest_chat_projection(self, session_id: str) -> Optional[Dict]:
        return db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")


snapshot_service = SnapshotService()
