from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from core.database import DatabaseManager
from erc.safety_guardian import safety_guardian


RUNTIME_CONTEXT = {
    "workspace_path": r"C:\Users\sunny\.v8-agent-os\workspace",
    "runtime_kind": "chat",
}


class SafetyRuntimeP3P4Tests(unittest.TestCase):
    def setUp(self):
        safety_guardian._recent_downloads = []
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = DatabaseManager(Path(self.temp_dir.name) / "state.db")
        self.db_patch = patch("erc.safety_guardian.db", self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_allowlist_candidate_is_strongly_bound_and_revocable(self):
        decision = safety_guardian.assess_system_command(
            "Stop-Process -Name python -Force",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        candidate = safety_guardian.build_allowlist_candidate(decision)
        entry = safety_guardian.record_allowlist_candidate(candidate, approval_id="approval_1")
        self.assertTrue(entry["enabled"])
        self.assertIsNotNone(safety_guardian.is_allowlisted(decision))

        other_runtime_decision = safety_guardian.assess_system_command(
            "Stop-Process -Name python -Force",
            runtime_context={**RUNTIME_CONTEXT, "runtime_kind": "cron"},
        )
        self.assertIsNone(safety_guardian.is_allowlisted(other_runtime_decision))

        revoked = safety_guardian.revoke_safety_allowlist_entry(entry["id"])
        self.assertFalse(revoked["enabled"])
        self.assertIsNone(safety_guardian.is_allowlisted(decision))

    def test_approval_allowlist_requires_explicit_persist_flag(self):
        decision = safety_guardian.assess_system_command(
            "Stop-Process -Name python -Force",
            runtime_context=RUNTIME_CONTEXT,
        )
        candidate = safety_guardian.build_allowlist_candidate(decision)
        approval = {
            "id": "approval_2",
            "session_id": "session_1",
            "run_id": "run_1",
            "approval_kind": "safety_review",
            "request": {"allowlistCandidate": candidate},
        }
        self.assertIsNone(safety_guardian.record_allowlist_from_approval(approval, {"approved": True}))
        persisted = safety_guardian.record_allowlist_from_approval(
            approval,
            {"approved": True, "persistSafetyAllowlist": True},
        )
        self.assertIsNotNone(persisted)
        self.assertTrue(persisted["enabled"])

    def test_dashboard_redacts_secret_like_values_and_aggregates(self):
        self.db.create_or_update_session("session_1", "Safety session", user_id="test")
        self.db.create_run_record("run_1", "session_1", run_type="chat", status="waiting_approval")
        self.db.add_pending_approval(
            approval_id="approval_3",
            session_id="session_1",
            run_id="run_1",
            approval_kind="safety_review",
            status="pending",
            request={
                "question": "Run command with token=secret-value",
                "safety": {"verdict": "review", "risk_code": "process_control_command", "reason": "needs review"},
            },
        )
        self.db.add_audit_log(
            source_type="SAFETY",
            action="native_tool_safety",
            status="WARNING",
            details=json.dumps(
                {
                    "subject": "curl https://example.com?api_key=secret-value",
                    "verdict": "review",
                    "reason": "token=secret-value",
                    "riskCode": "download_execute_command",
                    "governanceTarget": "system_integrity",
                    "details": {
                        "runtime_context": RUNTIME_CONTEXT,
                        "analysis": {
                            "decodedCommands": ["echo token=secret-value"],
                            "encodedIndicators": [],
                            "downloadHosts": ["example.com"],
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
        dashboard = safety_guardian.build_dashboard_payload(limit=10)
        rendered = json.dumps(dashboard, ensure_ascii=False)
        self.assertIn("pendingSafetyApprovals", dashboard)
        self.assertEqual(dashboard["summary"]["pendingSafetyApprovals"], 1)
        self.assertNotIn("secret-value", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_dry_run_explain_does_not_record_recent_download(self):
        payload = safety_guardian.explain_system_command(
            r'curl https://example.com/tool.ps1 -o "C:\Users\sunny\.v8-agent-os\workspace\tool.ps1"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertTrue(payload["dryRun"])
        self.assertEqual(safety_guardian._recent_downloads, [])

        execution = safety_guardian.assess_system_command(
            r'powershell -File "C:\Users\sunny\.v8-agent-os\workspace\tool.ps1"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(execution.verdict, "allow")


if __name__ == "__main__":
    unittest.main()

