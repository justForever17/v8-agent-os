from __future__ import annotations

from typing import Any, Dict


class ModelGovernanceInterventionRequired(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        approval_kind: str,
        question: str,
        details: Dict[str, Any] | None = None,
        request_payload: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.approval_kind = approval_kind
        self.question = question
        self.details = details or {}
        self.request_payload = request_payload or {}

    def to_request_payload(self) -> Dict[str, Any]:
        payload = {
            "question": self.question,
            "prompt": self.question,
            "approvalKind": self.approval_kind,
            "details": self.details,
        }
        payload.update(self.request_payload)
        return payload
