from __future__ import annotations

import uuid

from core.context_governance import (
    extract_context_governance_history,
    extract_latest_context_governance,
)
from core.database import db
from core.runtime_projection import (
    build_projection_controls,
    build_projection_summary,
    build_recoverable_view,
    project_ask_user_interactions,
    project_runtime_timeline_from_events,
    project_pending_approvals,
)
from erc.chat_canonical_transcript import build_canonical_chat_messages
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
    @staticmethod
    def _flatten_artifacts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for message in messages:
            for artifact in list(message.get("artifacts") or []):
                if not isinstance(artifact, dict):
                    continue
                fingerprint = str(
                    artifact.get("id")
                    or artifact.get("artifactId")
                    or artifact.get("workspacePath")
                    or artifact.get("sourcePath")
                    or artifact.get("previewUrl")
                    or artifact.get("externalUrl")
                    or ""
                ).strip()
                if fingerprint and fingerprint in seen:
                    continue
                if fingerprint:
                    seen.add(fingerprint)
                artifacts.append(dict(artifact))
        return artifacts

    def _has_legacy_chat_data(self, session_id: str) -> bool:
        if db.get_chat_canonical_max_version(session_id) > 0:
            return False
        return bool(db.get_messages(session_id) or db.get_runtime_events(session_id))

    @staticmethod
    def _queued_messages(session_id: str) -> list[dict[str, Any]]:
        items = db.list_chat_user_message_queue(session_id, states=["pending", "promoted"], limit=20)
        return [
            {
                "id": item.get("id"),
                "sessionId": item.get("session_id"),
                "runId": item.get("run_id"),
                "clientMessageId": item.get("client_message_id"),
                "content": item.get("content") or "",
                "state": item.get("state") or "pending",
                "ordinal": item.get("ordinal"),
                "createdAt": item.get("created_at"),
                "updatedAt": item.get("updated_at"),
                "promotedAt": item.get("promoted_at"),
                "injectedAt": item.get("injected_at"),
                "consumedAt": item.get("consumed_at"),
                "cancelledAt": item.get("cancelled_at"),
            }
            for item in items
        ]

    def _build_projection_snapshot(
        self,
        session_id: str,
        *,
        latest_seq: int,
        canonical_version: int,
        legacy_chat_unsupported: bool = False,
    ) -> Dict:
        messages = build_canonical_chat_messages(session_id) if canonical_version > 0 else []
        return {
            "session_id": session_id,
            "latest_seq": latest_seq,
            "canonicalVersion": canonical_version,
            "legacyChatUnsupported": legacy_chat_unsupported,
            "messages": messages,
            "artifacts": self._flatten_artifacts(messages),
        }

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
        latest_seq = db.get_latest_runtime_seq(session_id)
        canonical_version = db.get_chat_canonical_max_version(session_id)
        legacy_chat_unsupported = canonical_version <= 0 and self._has_legacy_chat_data(session_id)
        snapshot = self._build_projection_snapshot(
            session_id,
            latest_seq=latest_seq,
            canonical_version=canonical_version,
            legacy_chat_unsupported=legacy_chat_unsupported,
        )
        self.record_runtime_snapshot(
            session_id=session_id,
            run_id=run_id,
            latest_seq=latest_seq,
            snapshot_type="chat_projection",
            snapshot=snapshot,
        )
        return snapshot

    def ensure_chat_projection_row(self, session_id: str) -> Optional[Dict]:
        snapshot_row = db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")
        latest_runtime_seq = db.get_latest_runtime_seq(session_id)
        latest_canonical_version = db.get_chat_canonical_max_version(session_id)
        if (
            not snapshot_row
            or int(snapshot_row.get("latest_seq") or 0) < latest_runtime_seq
            or int((snapshot_row.get("snapshot") or {}).get("canonicalVersion") or 0) < latest_canonical_version
        ):
            self.refresh_chat_projection(session_id)
            snapshot_row = db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")
        elif snapshot_row and not isinstance(snapshot_row.get("snapshot"), dict):
            snapshot_row = db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")
        if snapshot_row and isinstance(snapshot_row.get("snapshot"), dict):
            snapshot = dict(snapshot_row.get("snapshot") or {})
            snapshot["canonicalVersion"] = max(
                int(snapshot.get("canonicalVersion") or 0),
                latest_canonical_version,
            )
            snapshot_row["snapshot"] = snapshot
        return snapshot_row

    def build_chat_projection_payload(self, session_id: str) -> Dict:
        snapshot_row = self.ensure_chat_projection_row(session_id)
        session_row = db.get_session(session_id) or {"id": session_id, "title": "New Chat", "metadata": {}}
        workflow_view = workflow_ledger_service.get_session_workflow_view(session_id)
        pending_approvals = project_pending_approvals(
            db.list_pending_approvals(session_id=session_id, status="pending")
        )
        ask_user_interactions = project_ask_user_interactions(
            db.list_ask_user_interactions(session_id=session_id, status="pending")
        )
        runtime_events = db.get_runtime_events(session_id)
        runtime_timeline = project_runtime_timeline_from_events(runtime_events)
        context_governance = extract_latest_context_governance(runtime_events)
        context_governance_history = extract_context_governance_history(runtime_events, limit=12)
        lane_view = session_admission_service.get_lane_view(session_id)
        queued_messages = self._queued_messages(session_id)
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
            source = "canonical_projection"
            workflow_projection = augment_workflow_projection(
                workflow_projection_service.build(session_id=session_id),
                todos=todos,
                current_run=current_run,
                runtime_status=runtime_status,
                latest_seq=0,
            )
            latest_seq = 0
            snapshot = self._build_projection_snapshot(
                session_id,
                latest_seq=latest_seq,
                canonical_version=0,
                legacy_chat_unsupported=self._has_legacy_chat_data(session_id),
            )
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
                "askUserInteractions": ask_user_interactions,
                "queuedMessages": queued_messages,
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
                "contextGovernanceHistory": context_governance_history,
                "lane": lane_view,
                "liveness": liveness,
                "recoveryClass": recovery_class,
                "legacyChatUnsupported": bool(snapshot.get("legacyChatUnsupported")),
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
            "askUserInteractions": ask_user_interactions,
            "queuedMessages": queued_messages,
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
                source="canonical_snapshot",
            ),
            "source": "canonical_snapshot",
            "contextGovernance": context_governance,
            "contextGovernanceHistory": context_governance_history,
            "lane": lane_view,
            "liveness": liveness,
            "recoveryClass": recovery_class,
            "legacyChatUnsupported": bool(snapshot.get("legacyChatUnsupported")),
        }

    def get_latest_chat_projection(self, session_id: str) -> Optional[Dict]:
        return db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")


snapshot_service = SnapshotService()
