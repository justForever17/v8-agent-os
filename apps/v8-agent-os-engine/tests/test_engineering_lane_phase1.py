from __future__ import annotations

import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from core.database import db
from core.terminal_post_run import TerminalPostRunService
from runtimes.engineering.service import engineering_lane_service


def _engineering_config(**overrides):
    config = {
        "enabled": True,
        "triggerMode": "auto",
        "contextPackBudget": 1600,
        "proofLedgerEnabled": True,
        "suppressDailyMemory": True,
        "suppressMemoryMap": True,
        "rankedWorkflowPathCount": 3,
    }
    config.update(overrides)
    return config


class EngineeringLanePhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.proof_ids: list[str] = []

    def tearDown(self) -> None:
        with db.get_connection() as conn:
            for proof_id in self.proof_ids:
                conn.execute("DELETE FROM engineering_proof_entries WHERE id = ?", (proof_id,))
            conn.execute("DELETE FROM runtime_events WHERE session_id LIKE 'eng-test-%'")
            conn.execute("DELETE FROM run_records WHERE session_id LIKE 'eng-test-%'")
            conn.execute("DELETE FROM messages WHERE session_id LIKE 'eng-test-%'")
            conn.execute("DELETE FROM sessions WHERE id LIKE 'eng-test-%'")
            conn.commit()

    def _repo(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "package.json").write_text('{"scripts":{"test":"vitest","build":"tsc --noEmit"}}', encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "admin-panel.tsx").write_text("export const value = 1\n", encoding="utf-8")
        (root / ".agents" / "rules").mkdir(parents=True)
        (root / ".agents" / "rules" / "AGENTS.md").write_text("# Repo rules\nRun the narrowest verification first.\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=str(root), capture_output=True, text=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "test@example.local"], cwd=str(root), capture_output=True, text=True, timeout=10)
        subprocess.run(["git", "config", "user.name", "Engineering Test"], cwd=str(root), capture_output=True, text=True, timeout=10)
        subprocess.run(["git", "add", "."], cwd=str(root), capture_output=True, text=True, timeout=10)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(root), capture_output=True, text=True, timeout=10)
        return temp, root

    def test_auto_triggers_for_code_request_with_repo(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        descriptor = {"workspaceRoot": str(root)}
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config()):
            decision = engineering_lane_service.trigger_decision(
                user_query="修复 admin panel 代码并跑 typecheck",
                mode="auto",
                workspace_descriptor=descriptor,
            )
        self.assertTrue(decision["active"])
        self.assertIn("verification", decision["signals"])

    def test_auto_does_not_trigger_for_non_engineering_request(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        descriptor = {"workspaceRoot": str(root)}
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config()):
            decision = engineering_lane_service.trigger_decision(
                user_query="帮我生成一张产品海报图片",
                mode="auto",
                workspace_descriptor=descriptor,
            )
        self.assertFalse(decision["active"])

    def test_context_pack_suppresses_daily_and_map_but_keeps_workflow_slot(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config()), patch(
            "runtimes.engineering.service.workspace_resolution_service.resolve_workspace_descriptor",
            return_value={"workspaceRoot": str(root), "source": "main_workspace", "isScopedOverride": False},
        ), patch("runtimes.engineering.service.workflow_memory_service.match_hints", return_value=[]):
            result = engineering_lane_service.build_context_pack(
                user_query="实现 API route 并补测试",
                mode="force",
            )
        suppression = result["contextPack"]["memorySuppression"]
        self.assertTrue(suppression["suppressDailyMemory"])
        self.assertTrue(suppression["suppressMemoryMap"])
        self.assertTrue(suppression["workflowHintsRetained"])
        self.assertGreaterEqual(result["contextPackEstimatedTokens"], 1)

    def test_context_pack_includes_evidence_graph_and_coding_contract(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config()), patch(
            "runtimes.engineering.service.workspace_resolution_service.resolve_workspace_descriptor",
            return_value={"workspaceRoot": str(root), "source": "main_workspace", "isScopedOverride": False},
        ), patch("runtimes.engineering.service.workflow_memory_service.match_hints", return_value=[]):
            result = engineering_lane_service.build_context_pack(
                user_query="修复 admin-panel 组件并补 typecheck",
                mode="force",
            )
        evidence = result["evidenceGraphDigest"]
        contract = result["codingPlannerContractPreview"]
        self.assertTrue(evidence["enabled"])
        self.assertTrue(evidence["repoDetected"])
        self.assertTrue(contract["enabled"])
        self.assertIn("criticalFiles", contract)
        self.assertIn("verificationMatrix", contract)
        self.assertIn("worksetSoftGateDecision", result)

    def test_soft_gate_warns_when_changed_file_outside_write_set(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "src" / "admin-panel.tsx").write_text("export const value = 9\n", encoding="utf-8")
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config(worksetGovernanceMode="soft_gate")), patch(
            "runtimes.engineering.service.workspace_resolution_service.resolve_workspace_descriptor",
            return_value={"workspaceRoot": str(root), "source": "main_workspace", "isScopedOverride": False},
        ), patch("runtimes.engineering.service.workflow_memory_service.match_hints", return_value=[]):
            result = engineering_lane_service.build_context_pack(
                user_query="修复 admin panel",
                mode="force",
                task_brief={"taskBriefId": "task-1", "goal": "修复 docs", "writeSet": ["docs/"]},
            )
        gate = result["worksetSoftGateDecision"]
        self.assertEqual(gate["risk"], "outside_write_set")
        self.assertTrue(gate["warning"])
        self.assertIn("src/admin-panel.tsx", gate["outsideWriteSet"])

    def test_planner_plan_is_enriched_with_engineering_capsule(self) -> None:
        plan = {
            "riskFlags": [],
            "taskBriefs": [{"taskBriefId": "task-1", "goal": "Fix admin panel", "writeSet": []}],
        }
        engineering_context = {
            "triggerDecision": {"active": True},
            "contextPack": {
                "evidenceGraphDigest": {"repoDetected": True, "repoRoot": str(Path.cwd()), "branch": "main", "dirtyState": {}, "criticalFileCandidates": []},
                "codingPlannerContractPreview": {
                    "enabled": True,
                    "criticalFiles": ["src/admin-panel.tsx"],
                    "readSet": ["src/admin-panel.tsx"],
                    "writeSet": ["src/admin-panel.tsx"],
                    "verificationMatrix": [{"kind": "typecheck", "command": "npm run typecheck"}],
                    "riskFlags": [],
                    "proofExpectations": ["Verification is required."],
                },
            },
        }
        enriched = engineering_lane_service.enrich_planner_plan_with_engineering_contract(plan, engineering_context=engineering_context)
        self.assertEqual(enriched["taskBriefs"][0]["writeSet"], ["src/admin-panel.tsx"])
        self.assertIn("engineeringTaskCapsule", enriched["taskBriefs"][0])
        self.assertTrue(enriched["codingPlannerContract"]["enabled"])

    def test_proof_ledger_verified_requires_evidence(self) -> None:
        entry = engineering_lane_service.add_proof_entry(
            {
                "mode": "dry_run",
                "patchIntent": "No commands attached",
                "verificationStatus": "verified",
            }
        )
        self.proof_ids.append(entry["id"])
        self.assertEqual(entry["verificationStatus"], "unverified")

    def _create_completed_run(self, *, root: Path, active: bool = True, mode: str = "auto"):
        session_id = f"eng-test-{uuid.uuid4()}"
        run_id = f"eng-run-{uuid.uuid4()}"
        db.create_or_update_session(session_id, "Engineering test")
        db.create_run_record(
            run_id=run_id,
            session_id=session_id,
            run_type="chat",
            status="completed",
            trigger_source="test",
            metadata={
                "engineeringMode": mode,
                "engineeringTriggerDecision": {"active": active, "reason": "test"},
                "workspace_path": str(root),
            },
        )
        db.add_message(
            msg_id=f"msg-{uuid.uuid4()}",
            session_id=session_id,
            role="user",
            content="修复代码并验证",
            metadata={"run_id": run_id},
        )
        return session_id, run_id

    def _add_tool_events(self, *, session_id: str, run_id: str, tool_name: str, command: str, result: dict):
        tool_call_id = f"tool-{uuid.uuid4()}"
        seq = db.get_next_runtime_seq(session_id)
        db.add_runtime_event({
            "event_id": str(uuid.uuid4()),
            "session_id": session_id,
            "run_id": run_id,
            "seq": seq,
            "kind": "event",
            "topic": "tool.started",
            "ts": "2026-04-23T00:00:00Z",
            "source": {"component": "chat_runtime"},
            "payload": {
                "type": "tool_start",
                "tool": {"toolCallId": tool_call_id, "toolName": tool_name, "args": {"command": command}},
            },
        })
        db.add_runtime_event({
            "event_id": str(uuid.uuid4()),
            "session_id": session_id,
            "run_id": run_id,
            "seq": seq + 1,
            "kind": "event",
            "topic": "tool.finished",
            "ts": "2026-04-23T00:00:01Z",
            "source": {"component": "chat_runtime"},
            "payload": {
                "type": "tool_result",
                "tool": {"toolCallId": tool_call_id, "toolName": tool_name, "result": result},
            },
        })

    def test_terminal_proof_not_created_for_inactive_engineering_run(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        session_id, run_id = self._create_completed_run(root=root, active=False)
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config(autoProofCollectionEnabled=True)):
            result = engineering_lane_service.collect_terminal_proof(session_id=session_id, run_id=run_id)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(db.list_engineering_proof_entries(session_id=session_id, run_id=run_id), [])

    def test_terminal_proof_marks_changed_files_without_validation_unverified(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "src" / "admin-panel.tsx").write_text("export const value = 2\n", encoding="utf-8")
        session_id, run_id = self._create_completed_run(root=root, active=True)
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config(autoProofCollectionEnabled=True)):
            result = engineering_lane_service.collect_terminal_proof(session_id=session_id, run_id=run_id)
        entry = result["entry"]
        self.proof_ids.append(entry["id"])
        self.assertEqual(entry["verificationStatus"], "unverified")
        self.assertIn("src/admin-panel.tsx", entry["changedFiles"])
        self.assertTrue(any("No test/typecheck/build/compile evidence" in risk for risk in entry["residualRisks"]))

    def test_terminal_proof_uses_existing_successful_validation_command(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "src" / "admin-panel.tsx").write_text("export const value = 3\n", encoding="utf-8")
        session_id, run_id = self._create_completed_run(root=root, active=True)
        self._add_tool_events(
            session_id=session_id,
            run_id=run_id,
            tool_name="run_system_command",
            command="python -m py_compile apps/example.py",
            result={"status": "ok", "stdoutPreview": "compiled", "returnCode": 0},
        )
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config(autoProofCollectionEnabled=True)):
            result = engineering_lane_service.collect_terminal_proof(session_id=session_id, run_id=run_id)
        entry = result["entry"]
        self.proof_ids.append(entry["id"])
        self.assertEqual(entry["verificationStatus"], "verified")
        self.assertTrue(entry["commands"][0]["isValidation"])

    def test_terminal_proof_failed_validation_enters_diagnostics(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        (root / "src" / "admin-panel.tsx").write_text("export const value = 4\n", encoding="utf-8")
        session_id, run_id = self._create_completed_run(root=root, active=True)
        self._add_tool_events(
            session_id=session_id,
            run_id=run_id,
            tool_name="command_session_broker",
            command="npm run build",
            result={"ok": False, "summary": "build failed", "returnCode": 1, "finalPreview": "Type error"},
        )
        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config(autoProofCollectionEnabled=True)):
            result = engineering_lane_service.collect_terminal_proof(session_id=session_id, run_id=run_id)
        entry = result["entry"]
        self.proof_ids.append(entry["id"])
        self.assertEqual(entry["verificationStatus"], "failed_verification")
        diagnostics = entry["diagnostics"]["items"]
        self.assertTrue(any(item.get("severity") == "error" for item in diagnostics))

    def test_terminal_proof_does_not_run_validation_commands(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        session_id, run_id = self._create_completed_run(root=root, active=True)
        observed: list[list[str]] = []

        def fake_run(args, *, cwd, timeout=5.0):
            observed.append(list(args))
            if args[:2] == ["git", "status"]:
                return {"ok": True, "stdout": "", "stderr": ""}
            if args[:2] == ["git", "diff"]:
                return {"ok": True, "stdout": "", "stderr": ""}
            return {"ok": True, "stdout": str(root), "stderr": ""}

        with patch.object(engineering_lane_service, "get_config", return_value=_engineering_config(autoProofCollectionEnabled=True)), patch(
            "runtimes.engineering.service._run_command",
            side_effect=fake_run,
        ):
            result = engineering_lane_service.collect_terminal_proof(session_id=session_id, run_id=run_id)
        entry = result["entry"]
        self.proof_ids.append(entry["id"])
        flattened = " ".join(" ".join(args) for args in observed)
        self.assertNotIn("py_compile", flattened)
        self.assertNotIn("pytest", flattened)

    def test_terminal_post_run_schedules_engineering_proof_independently(self) -> None:
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        session_id, run_id = self._create_completed_run(root=root, active=True)
        service = TerminalPostRunService()
        calls: list[str] = []
        with patch.object(service, "_schedule_engineering_proof_if_needed", return_value=True) as proof, patch.object(
            service,
            "_schedule_memory_extraction",
            side_effect=lambda **_: calls.append("memory"),
        ), patch.object(service, "_run_non_memory_hooks", side_effect=lambda **_: calls.append("hooks")):
            self.assertTrue(service.dispatch(session_id=session_id, run_id=run_id, source_component="test"))
        self.assertTrue(proof.called)
        self.assertEqual(calls, ["memory", "hooks"])


if __name__ == "__main__":
    unittest.main()
