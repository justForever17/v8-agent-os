from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from erc.models import ApprovalRequest
from erc.run_service import run_service


class CommandService:
    def __init__(self) -> None:
        self._control_signals: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _should_auto_approve(self, approval_kind: str) -> bool:
        # Governance approvals must always surface explicitly instead of being
        # silently auto-approved by default policy.
        return False

    def _operation_fingerprint(self, request: Dict[str, Any] | None) -> str:
        if not isinstance(request, dict):
            return ""
        return str(
            request.get("operationFingerprint")
            or request.get("operation_fingerprint")
            or ""
        ).strip()

    def _operation_target_fingerprint(self, request: Dict[str, Any] | None) -> str:
        if not isinstance(request, dict):
            return ""
        return str(
            request.get("operationTargetFingerprint")
            or request.get("operation_target_fingerprint")
            or ""
        ).strip()

    def _sanitize_approval_response(self, response: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(response, dict):
            return response
        sanitized: Dict[str, Any] = {}
        sensitive_keys = {"password", "passwd", "secret", "token", "api_key", "apikey", "credential", "sensitive_input", "sensitiveInput"}
        for key, value in response.items():
            normalized_key = str(key or "").replace("-", "_").lower()
            if normalized_key in sensitive_keys or any(marker in normalized_key for marker in ("password", "secret", "token", "credential")):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = value
        return sanitized

    def _find_existing_pending_approval(self, request: ApprovalRequest) -> Optional[Dict[str, Any]]:
        fingerprint = self._operation_fingerprint(request.request)
        target_fingerprint = self._operation_target_fingerprint(request.request)
        candidates = {fingerprint, target_fingerprint}
        candidates.discard("")
        if not candidates:
            return None
        from core.database import db

        for approval in db.list_pending_approvals(
            session_id=request.session_id,
            run_id=request.run_id,
            status="pending",
        ):
            if str(approval.get("approval_kind") or "") != str(request.approval_kind or ""):
                continue
            approval_candidates = {
                self._operation_fingerprint(approval.get("request")),
                self._operation_target_fingerprint(approval.get("request")),
            }
            approval_candidates.discard("")
            if candidates.intersection(approval_candidates):
                return approval
        return None

    def _remember_approved_operation(self, approval: Dict[str, Any], response: Optional[Dict[str, Any]]) -> None:
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        fingerprint = self._operation_fingerprint(request)
        run_id = str(approval.get("run_id") or "").strip()
        if not fingerprint or not run_id:
            return
        run_record = run_service.get_run(run_id)
        if not run_record:
            return
        metadata = dict(run_record.get("metadata") or {})
        operations = metadata.get("approvedSafetyOperations")
        if not isinstance(operations, list):
            operations = []
        operations = [
            item for item in operations
            if not (isinstance(item, dict) and str(item.get("fingerprint") or "") == fingerprint)
        ]
        operations.append({
            "fingerprint": fingerprint,
            "targetFingerprint": self._operation_target_fingerprint(request),
            "approval_id": approval.get("id") or approval.get("approval_id"),
            "approval_kind": approval.get("approval_kind"),
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "response": response or approval.get("response") or {},
            "request": {
                "riskCode": request.get("riskCode"),
                "runtimeKind": request.get("runtimeKind"),
                "toolCallId": request.get("toolCallId"),
            },
        })
        run_service.update_metadata(run_id, {"approvedSafetyOperations": operations[-100:]})

    def _remember_safety_allowlist(self, approval: Dict[str, Any], response: Optional[Dict[str, Any]]) -> None:
        try:
            from erc.safety_guardian import safety_guardian

            safety_guardian.record_allowlist_from_approval(approval, response if isinstance(response, dict) else {})
        except Exception:
            return

    def request_approval(self, request: ApprovalRequest) -> Dict[str, Any]:
        from core.database import db

        existing = self._find_existing_pending_approval(request)
        if existing:
            return {
                "approval_id": existing.get("id") or existing.get("approval_id"),
                "session_id": existing.get("session_id"),
                "run_id": existing.get("run_id"),
                "approval_kind": existing.get("approval_kind"),
                "status": existing.get("status") or "pending",
                "request": existing.get("request") or {},
                "response": existing.get("response"),
                "expires_at": existing.get("expires_at"),
                "autoApproved": False,
                "policySource": "existing_pending",
                "reusedPendingApproval": True,
            }

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

        response = self._sanitize_approval_response(response)
        db.update_pending_approval(approval_id, status="approved", response=response)
        approval = db.get_pending_approval(approval_id)
        if approval:
            self._remember_approved_operation(approval, response)
            self._remember_safety_allowlist(approval, response)
        if approval and approval.get("run_id"):
            self.clear_control_signal(approval["run_id"])
        return approval

    def reject(self, approval_id: str, response: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        from core.database import db

        response = self._sanitize_approval_response(response)
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
