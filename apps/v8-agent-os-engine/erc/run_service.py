from __future__ import annotations

from typing import Any, Dict, Optional

from core.database import db
from core.run_ledger import run_ledger_service

from erc.models import RunDescriptor


class RunService:
    def create_run(self, descriptor: RunDescriptor) -> RunDescriptor:
        db.create_run_record(
            run_id=descriptor.run_id,
            session_id=descriptor.session_id,
            conversation_id=descriptor.conversation_id,
            thread_id=descriptor.thread_id,
            user_id=descriptor.user_id,
            run_type=descriptor.runtime_kind,
            status=descriptor.status,
            trigger_source=descriptor.trigger_source,
            agent_id=descriptor.agent_id,
            workflow_id=descriptor.workflow_id,
            channel_type=descriptor.channel_type,
            metadata=descriptor.metadata,
        )
        run_ledger_service.record_event(
            event_type="run.started",
            run_id=descriptor.run_id,
            session_id=descriptor.session_id,
            runtime_kind=descriptor.runtime_kind,
            source="erc.run_service",
            summary=f"Run started as {descriptor.runtime_kind}",
            refs={"runId": descriptor.run_id, "sessionId": descriptor.session_id},
            payload={
                "status": descriptor.status,
                "triggerSource": descriptor.trigger_source,
                "workflowId": descriptor.workflow_id,
                "channelType": descriptor.channel_type,
            },
        )
        return descriptor

    def transition_run(
        self,
        run_id: str,
        *,
        status: str,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        persisted_metadata: Optional[Dict[str, Any]] = None
        if metadata is not None:
            existing = db.get_run_record(run_id) or {}
            persisted_metadata = dict(existing.get("metadata") or {})
            persisted_metadata.update(metadata or {})
        db.update_run_record(
            run_id,
            status=status,
            error_message=error_message,
            metadata=persisted_metadata,
        )
        run_record = db.get_run_record(run_id) or {}
        run_ledger_service.record_event(
            event_type=f"run.status.{status}",
            run_id=run_id,
            session_id=run_record.get("session_id"),
            runtime_kind=run_record.get("run_type"),
            source="erc.run_service",
            summary=error_message or f"Run status changed to {status}",
            refs={"runId": run_id, "sessionId": run_record.get("session_id")},
            payload={"status": status, "errorMessage": error_message, "metadata": metadata or {}},
        )

    def transition_run_if_status(
        self,
        run_id: str,
        *,
        expected_statuses: set[str],
        status: str,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        persisted_metadata: Optional[Dict[str, Any]] = None
        if metadata is not None:
            existing = db.get_run_record(run_id) or {}
            persisted_metadata = dict(existing.get("metadata") or {})
            persisted_metadata.update(metadata or {})
        result = db.update_run_record_if_status(
            run_id,
            expected_statuses=expected_statuses,
            status=status,
            error_message=error_message,
            metadata=persisted_metadata,
        )
        if not result.get("updated"):
            return result
        run_record = result.get("run_record") or db.get_run_record(run_id) or {}
        run_ledger_service.record_event(
            event_type=f"run.status.{status}",
            run_id=run_id,
            session_id=run_record.get("session_id"),
            runtime_kind=run_record.get("run_type"),
            source="erc.run_service",
            summary=error_message or f"Run status changed to {status}",
            refs={"runId": run_id, "sessionId": run_record.get("session_id")},
            payload={
                "status": status,
                "errorMessage": error_message,
                "metadata": metadata or {},
                "conditional": True,
                "previousStatus": result.get("previousStatus"),
            },
        )
        return result

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return db.get_run_record(run_id)

    def update_metadata(self, run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        run_record = self.get_run(run_id)
        if not run_record:
            return None
        metadata = dict(run_record.get("metadata") or {})
        metadata.update(updates or {})
        db.update_run_record(run_id, status=run_record["status"], metadata=metadata)
        refreshed = self.get_run(run_id)
        return refreshed

    def update_metadata_key_if_state(
        self,
        run_id: str,
        *,
        key: str,
        expected_state: str,
        next_value: Dict[str, Any],
        expected_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        return db.update_run_metadata_key_if_state(
            run_id,
            key=key,
            expected_state=expected_state,
            next_value=next_value,
            expected_status=expected_status,
        )

    def claim_runtime_episode_resume_schedule(
        self,
        run_id: str,
        *,
        marker_key: str,
        next_marker: Dict[str, Any],
        terminal_states: set[str],
        active_states: set[str],
    ) -> Dict[str, Any]:
        return db.claim_runtime_episode_resume_schedule(
            run_id,
            marker_key=marker_key,
            next_marker=next_marker,
            expected_marker_state="waiting",
            expected_status="running",
            terminal_states=terminal_states,
            active_states=active_states,
        )

    def set_control_signal(
        self,
        run_id: str,
        *,
        command: str,
        reason: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.update_metadata(
            run_id,
            {
                "control_signal": {
                    "command": command,
                    "reason": reason,
                    "payload": payload or {},
                }
            },
        )

    def get_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        run_record = self.get_run(run_id)
        if not run_record:
            return None
        metadata = run_record.get("metadata") or {}
        signal = metadata.get("control_signal")
        return dict(signal) if isinstance(signal, dict) else None

    def clear_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        run_record = self.get_run(run_id)
        if not run_record:
            return None
        metadata = dict(run_record.get("metadata") or {})
        if "control_signal" not in metadata:
            return run_record
        metadata.pop("control_signal", None)
        db.update_run_record(run_id, status=run_record["status"], metadata=metadata)
        return self.get_run(run_id)


run_service = RunService()
