from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from erc.models import ApprovalRequest
from erc.run_service import run_service


class CommandService:
    def __init__(self) -> None:
        self._control_signals: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _should_auto_approve(self, approval_kind: str) -> bool:
        normalized = str(approval_kind or "").strip().lower()
        return normalized not in {"", "ask_user", "human_input_required", "waiting_input"}

    def request_approval(self, request: ApprovalRequest) -> Dict[str, Any]:
        from core.database import db

        auto_approved = self._should_auto_approve(request.approval_kind)
        status = "approved" if auto_approved else "pending"
        response = (
            {
                "decision": "approved",
                "autoApproved": True,
                "policySource": "default_auto_approve",
            }
            if auto_approved
            else None
        )
        db.add_pending_approval(
            approval_id=request.approval_id,
            session_id=request.session_id,
            run_id=request.run_id,
            approval_kind=request.approval_kind,
            status=status,
            request=request.request,
            response=response,
            expires_at=request.expires_at,
        )
        return {
            "approval_id": request.approval_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
            "approval_kind": request.approval_kind,
            "status": status,
            "request": request.request,
            "response": response,
            "expires_at": request.expires_at,
            "autoApproved": auto_approved,
            "policySource": "default_auto_approve" if auto_approved else "manual_review",
        }

    def approve(self, approval_id: str, response: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        from core.database import db

        db.update_pending_approval(approval_id, status="approved", response=response)
        approval = db.get_pending_approval(approval_id)
        if approval and approval.get("run_id"):
            self.clear_control_signal(approval["run_id"])
        return approval

    def reject(self, approval_id: str, response: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        from core.database import db

        db.update_pending_approval(approval_id, status="rejected", response=response)
        approval = db.get_pending_approval(approval_id)
        if approval and approval.get("run_id"):
            self.issue_control_signal(
                approval["run_id"],
                command="approval_rejected",
                reason=(response or {}).get("reason") if isinstance(response, dict) else None,
                payload={"approval_id": approval_id, "response": response or {}},
            )
        return approval

    def issue_control_signal(
        self,
        run_id: str,
        *,
        command: str,
        reason: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        signal = {
            "command": command,
            "reason": reason,
            "payload": payload or {},
        }
        with self._lock:
            self._control_signals[run_id] = signal
        run_service.set_control_signal(run_id, command=command, reason=reason, payload=payload)
        return signal

    def peek_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            signal = self._control_signals.get(run_id)
        if signal:
            return dict(signal)
        persisted = run_service.get_control_signal(run_id)
        return dict(persisted) if persisted else None

    def consume_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            signal = self._control_signals.pop(run_id, None)
        persisted = run_service.get_control_signal(run_id)
        effective = signal or persisted
        if effective:
            run_service.clear_control_signal(run_id)
            return dict(effective)
        return None

    def clear_control_signal(self, run_id: str) -> None:
        with self._lock:
            self._control_signals.pop(run_id, None)
        run_service.clear_control_signal(run_id)

    def pause_run(self, run_id: str, *, reason: Optional[str] = None) -> None:
        run_service.transition_run(
            run_id,
            status="paused",
            metadata={"pause_reason": reason} if reason else None,
        )
        self.issue_control_signal(run_id, command="pause", reason=reason)

    def resume_run(self, run_id: str, *, reason: Optional[str] = None) -> None:
        self.clear_control_signal(run_id)
        run_service.transition_run(
            run_id,
            status="running",
            metadata={"resume_reason": reason} if reason else None,
        )

    def cancel_run(self, run_id: str, *, reason: Optional[str] = None) -> None:
        run_service.transition_run(run_id, status="cancelled", error_message=reason)
        self.issue_control_signal(run_id, command="cancel", reason=reason)

    def interrupt_run(self, run_id: str, *, reason: Optional[str] = None) -> None:
        run_service.transition_run(
            run_id,
            status="paused",
            metadata={"interrupt_reason": reason} if reason else None,
        )
        self.issue_control_signal(run_id, command="interrupt", reason=reason)

    def retry_run(self, run_id: str, *, reason: Optional[str] = None) -> None:
        self.issue_control_signal(run_id, command="retry", reason=reason)


command_service = CommandService()
