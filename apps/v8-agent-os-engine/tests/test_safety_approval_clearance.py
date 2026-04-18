from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

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
        service = CommandService()
        approval = {
            "id": "approval_1",
            "run_id": "run_a",
            "approval_kind": "safety_review",
            "request": {
                "operationFingerprint": "safety:abc",
                "riskCode": "protected_config_write",
                "runtimeKind": "chat",
                "toolCallId": "call_1",
            },
        }

        with patch("erc.command_service.run_service.get_run", return_value={"id": "run_a", "metadata": {}}), \
             patch("erc.command_service.run_service.update_metadata") as update_metadata:
            service._remember_approved_operation(approval, {"approved": True})

        update_metadata.assert_called_once()
        _, updates = update_metadata.call_args.args
        operations = updates["approvedSafetyOperations"]
        self.assertEqual(operations[0]["fingerprint"], "safety:abc")
        self.assertEqual(operations[0]["approval_id"], "approval_1")


if __name__ == "__main__":
    unittest.main()
