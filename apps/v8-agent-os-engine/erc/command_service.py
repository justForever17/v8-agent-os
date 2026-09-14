from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from erc.models import ApprovalRequest, INTERRUPTIBLE_RUN_STATUSES
from erc.run_service import run_service


class ApprovalDecisionConflict(ValueError):
    def __init__(self, approval_id: str, resolution: Dict[str, Any]) -> None:
        self.detail = {"code": resolution.get("reason"), "approvalId": approval_id,
                       "status": (resolution.get("approval") or {}).get("status"), "runStatus": resolution.get("runStatus")}
        super().__init__(str(resolution.get("reason") or "approval_decision_conflict"))


class CommandService:
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

    def _approved_operation(self, approval: Dict[str, Any], response: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        fingerprint = self._operation_fingerprint(request)
        if not fingerprint:
            return None
        return {
            "fingerprint": fingerprint,
            "targetFingerprint": self._operation_target_fingerprint(request),
            "approval_id": approval.get("id") or approval.get("approval_id"),
            "approval_kind": approval.get("approval_kind"),
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "response": response or {},
            "request": {
                "riskCode": request.get("riskCode"),
                "runtimeKind": request.get("runtimeKind"),
                "toolCallId": request.get("toolCallId"),
            },
        }

    def _remember_safety_allowlist(self, approval: Dict[str, Any], response: Optional[Dict[str, Any]]) -> None:
        try:
            from erc.safety_guardian import safety_guardian

            safety_guardian.record_allowlist_from_approval(approval, response if isinstance(response, dict) else {})
        except Exception:
            return

    def request_approval(self, request: ApprovalRequest) -> Dict[str, Any]:
        from core.database import db

        if request.approval_kind == "spec_stage_approval":
            from core.spec_service import spec_service, SpecApprovalVersionConflict
            payload = dict(request.request)
            review = spec_service.capture_stage_review(workspace_path=str(payload.get("workspacePath") or ""),
                spec_id=str(payload.get("specId") or ""), stage=str(payload.get("stage") or ""))
            if payload.get("documentSha256"):
                error = spec_service._review_version_error(payload["documentSha256"], review["documentSha256"])
                if error:
                    raise SpecApprovalVersionConflict(error)
            payload.update({key: review[key] for key in ("documentSha256", "documentPath")})
            fingerprint = f"spec_stage_approval:{payload['workspacePath']}:{payload['specId']}:{payload['stage']}:{review['documentSha256']}"
            payload.update(operationFingerprint=fingerprint, operationTargetFingerprint=fingerprint)
            saved = db.create_spec_review_approval(approval_id=request.approval_id, session_id=request.session_id,
                run_id=request.run_id, request=payload, expires_at=request.expires_at)
            return {**saved, "approval_id": saved["id"], "autoApproved": False, "policySource": "manual_review"}

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
        return self._decide_approval(approval_id, "approved", response)

    def reject(self, approval_id: str, response: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        return self._decide_approval(approval_id, "rejected", response)

    def _decide_approval(self, approval_id: str, status: str, response: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        from core.database import db

        response = self._sanitize_approval_response(response)
        approval = db.get_pending_approval(approval_id)
        if not approval:
            return None
        resolution = db.resolve_pending_approval_if_pending(
            approval_id, status=status, response=response, expected_request=approval.get("request"),
            approved_operation=self._approved_operation(approval, response) if status == "approved" else None,
        )
        if not resolution.get("updated") and resolution.get("reason") != "already_decided":
            if resolution.get("reason") == "approval_not_found":
                return None
            raise ApprovalDecisionConflict(approval_id, resolution)
        decided = dict(resolution["approval"])
        if resolution.get("updated") and status == "approved":
            self._remember_safety_allowlist(decided, response)
        # The receipt stays immutable; transition metadata is only for the
        # kernel/router in this dispatch, and is never persisted on the row.
        return {**decided, "_decision": {key: value for key, value in resolution.items() if key != "approval"}}

    def issue_control_signal(
        self,
        run_id: str,
        *,
        command: str,
        reason: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        record = run_service.set_control_signal(run_id, command=command, reason=reason, payload=payload)
        return (record.get("metadata") or {}).get("control_signal") if record else None

    def peek_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        return run_service.get_control_signal(run_id)

    def consume_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        return run_service.consume_control_signal(run_id)

    def clear_control_signal(self, run_id: str, *, expected_signal: Optional[Dict[str, Any]] = None) -> None:
        run_service.clear_control_signal(run_id, expected_signal=expected_signal)

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

    def interrupt_run(self, run_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        transition = run_service.transition_run_if_status(
            run_id,
            expected_statuses=INTERRUPTIBLE_RUN_STATUSES,
            status="interrupted",
            metadata={"interrupt_reason": reason} if reason else None,
        )
        if not transition.get("updated"):
            return transition
        self.issue_control_signal(run_id, command="interrupt", reason=reason)
        return transition

    def retry_run(self, run_id: str, *, reason: Optional[str] = None) -> None:
        self.issue_control_signal(run_id, command="retry", reason=reason)


command_service = CommandService()
