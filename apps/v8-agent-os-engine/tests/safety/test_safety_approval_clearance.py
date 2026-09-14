from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


from erc.command_service import CommandService
from erc.models import ApprovalRequest


class SafetyApprovalClearanceTests(unittest.TestCase):
    def test_request_approval_reuses_existing_pending_operation(self):
        service = CommandService()
        request = ApprovalRequest(
            approval_id="approval_new",
            session_id="session_a",
            run_id="run_a",
            approval_kind="safety_review",
            request={"operationFingerprint": "safety:abc", "question": "confirm"},
        )
        existing = {
            "id": "approval_existing",
            "session_id": "session_a",
            "run_id": "run_a",
            "approval_kind": "safety_review",
            "status": "pending",
            "request": {"operationFingerprint": "safety:abc"},
            "response": None,
        }

        with patch("core.database.db.list_pending_approvals", return_value=[existing]), \
             patch("core.database.db.add_pending_approval") as add_pending:
            result = service.request_approval(request)

        self.assertEqual(result["approval_id"], "approval_existing")
        self.assertTrue(result["reusedPendingApproval"])
        add_pending.assert_not_called()

    def test_approved_operation_is_persisted_on_run_metadata(self):
        from core.database import db
        import uuid

        service = CommandService()
        identity = uuid.uuid4().hex
        run_id, session_id, approval_id = f"run_{identity}", f"session_{identity}", f"approval_{identity}"
        db.create_or_update_session(session_id, "clearance fixture")
        db.create_run_record(run_id, session_id, status="waiting_approval")
        service.request_approval(ApprovalRequest(
            approval_id=approval_id, session_id=session_id, run_id=run_id, approval_kind="safety_review",
            request={
                "operationFingerprint": "safety:abc",
                "riskCode": "protected_config_write",
                "runtimeKind": "chat",
                "toolCallId": "call_1",
            },
        ))
        with patch.object(service, "_remember_safety_allowlist"):
            service.approve(approval_id, {"approved": True})
        # The old mock only asserted a stale read-modify-write. Check the
        # actual atomic decision/clearance transaction instead.
        operations = db.get_run_record(run_id)["metadata"]["approvedSafetyOperations"]
        self.assertEqual(operations[0]["fingerprint"], "safety:abc")
        self.assertEqual(operations[0]["approval_id"], approval_id)


if __name__ == "__main__":
    unittest.main()

