from __future__ import annotations

from typing import Any, Dict, Optional

from core.database import db

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
        return descriptor

    def transition_run(
        self,
        run_id: str,
        *,
        status: str,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        db.update_run_record(
            run_id,
            status=status,
            error_message=error_message,
            metadata=metadata,
        )

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
