from __future__ import annotations

import uuid
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.database import db
from core.realtime_protocol import utc_now_iso
from runtimes.memory.workflow_service import workflow_memory_service
from runtimes.memory.workspace_scope import canonical_workspace_scope


def _workflow_test_config() -> dict:
    return {
        "enabled": True,
        "hintInjectionEnabled": True,
        "progressiveHintsEnabled": True,
        "minSuccessCount": 1,
        "errorfulSuccessRequiresUserAcceptance": True,
        "maxInjectedHints": 2,
        "maxHintChars": 1200,
        "maxActiveWorkflowGuidesPerRun": 1,
        "quarantineOnNegativeFeedback": True,
        "requireApprovalForSideEffects": True,
        "riskTierActivationPolicy": {
            "read_only": "auto",
            "low": "auto",
            "medium": "approval",
            "high": "approval",
            "critical": "quarantine",
        },
    }


def _engineering_workflow_test_config(**engineering_overrides: object) -> dict:
    cfg = _workflow_test_config()
    cfg["engineering"] = {
        "enabled": True,
        "extractFromProofLedger": True,
        "requireEngineeringModeForInjection": True,
        "requireVerifiedProofForActivation": True,
        "learnFailedVerificationAsAntiPattern": True,
        "minVerifiedSuccessCount": 1,
        **engineering_overrides,
    }
    return cfg


class MemoryWorkflowRuntimeV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.workspace_temp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.workspace_temp.name) / "workspace"
        self.workspace_root.mkdir()
        self.candidate_ids: list[str] = []
        self.episode_ids: list[str] = []
        self.session_ids: list[str] = []
        self.run_ids: list[str] = []

    def tearDown(self) -> None:
        with db.get_connection() as conn:
            for candidate_id in self.candidate_ids:
                conn.execute("DELETE FROM memory_workflow_hint_events WHERE candidate_id = ?", (candidate_id,))
                conn.execute("DELETE FROM memory_workflow_guide_states WHERE candidate_id = ?", (candidate_id,))
                conn.execute("DELETE FROM memory_workflow_candidates WHERE id = ?", (candidate_id,))
            for episode_id in self.episode_ids:
                conn.execute("DELETE FROM memory_workflow_episodes WHERE id = ?", (episode_id,))
            for run_id in self.run_ids:
                conn.execute("DELETE FROM runtime_events WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM run_records WHERE id = ?", (run_id,))
            for session_id in self.session_ids:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
        self.workspace_temp.cleanup()

    def _add_episode(self, payload: dict, *, extraction_source: str = "memory_agent") -> dict:
        episode = workflow_memory_service.normalize_episode_payload(
            payload,
            session_id=None,
            run_id=None,
            scope="global",
            extraction_source=extraction_source,
        )
        record = workflow_memory_service.add_episode(episode)
        self.episode_ids.append(record["episode"]["id"])
        self.candidate_ids.append(record["candidate"]["id"])
        return record

    def test_transcript_only_claim_cannot_activate_or_use_ordered_actions_as_golden_path(self) -> None:
        payload = {
            "id": f"mw_ep_transcript_only_{self.suffix}",
            "taskFamily": f"Transcript-only workflow {self.suffix}",
            "taskFamilySignature": f"wf:test_transcript_only:{self.suffix}",
            "initialUserIntent": "I completed this workflow successfully",
            "orderedActions": ["bad step copied from a noisy transcript"],
            "finalSuccessEvidence": "assistant claimed success in transcript",
            "confidence": 0.9,
        }

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload)

        episode = record["episode"]
        candidate = record["candidate"]
        self.assertEqual(episode["status"], "candidate")
        self.assertLessEqual(episode["confidence"], 0.45)
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["goldenPathSteps"], [])
        self.assertFalse(candidate["metadata"].get("hasRuntimeEvidence"))

    def test_runtime_evidence_read_only_workflow_can_become_active_hint(self) -> None:
        payload = {
            "id": f"mw_ep_read_only_{self.suffix}",
            "taskFamily": f"Read docs workflow {self.suffix}",
            "taskFamilySignature": f"wf:test_read_only:{self.suffix}",
            "canonicalTriggerPatterns": ["read docs workflow"],
            "initialUserIntent": "Read project docs and summarize the safe next step",
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "read_native_file", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": ["Read the canonical docs before proposing implementation."],
            "verificationSteps": ["Confirm the cited document path exists."],
            "finalSuccessEvidence": "user accepted the summary",
            "confidence": 0.82,
        }

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")

        candidate = record["candidate"]
        self.assertEqual(candidate["status"], "active_hint")
        self.assertEqual(candidate["riskTier"], "read_only")
        self.assertFalse(candidate["approvalRequired"])
        self.assertIn("Read the canonical docs", candidate["goldenPathSteps"][0])

    def test_high_risk_workflow_requires_approval_before_active_hint(self) -> None:
        payload = {
            "id": f"mw_ep_high_risk_{self.suffix}",
            "taskFamily": f"Install dependency workflow {self.suffix}",
            "taskFamilySignature": f"wf:test_high_risk:{self.suffix}",
            "canonicalTriggerPatterns": ["install dependency workflow"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "run_system_command", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "run_system_command npm install modifies files",
            "toolSkillSequence": ["run_system_command"],
            "goldenPathSteps": ["Ask for approval before installing dependencies."],
            "finalSuccessEvidence": "dependency installation completed and tests passed",
            "confidence": 0.84,
        }

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")

        candidate = record["candidate"]
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["riskTier"], "high")
        self.assertTrue(candidate["approvalRequired"])

    def test_progressive_hint_only_injects_next_step_and_records_outcome(self) -> None:
        payload = {
            "id": f"mw_ep_hint_{self.suffix}",
            "taskFamily": f"Progressive docs workflow {self.suffix}",
            "taskFamilySignature": f"wf:test_progressive_hint:{self.suffix}",
            "canonicalTriggerPatterns": ["progressive docs workflow"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "read_native_file", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": [
                "Read the project governance document first.",
                "Then patch the implementation file.",
                "Finally run the full build.",
            ],
            "antiPatterns": ["Do not patch before reading the governance doc."],
            "verificationSteps": ["Confirm the governance document was actually opened."],
            "finalSuccessEvidence": "user accepted the final workflow",
            "confidence": 0.9,
        }

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")
            candidate_id = record["candidate"]["id"]
            block = workflow_memory_service.build_hints_block(
                query=f"Please use the progressive docs workflow {self.suffix}",
                scope_chain=["global"],
                session_id=None,
                run_id=None,
            )
            updated = workflow_memory_service.get_candidate(candidate_id) or {}

        self.assertIn("[WORKFLOW HINTS]", block)
        self.assertIn("Read the project governance document first.", block)
        self.assertIn("Suggested next move", block)
        self.assertIn("Adaptation:", block)
        self.assertNotIn("Then patch the implementation file.", block)
        self.assertNotIn("Later steps", block)
        self.assertEqual(updated.get("lastHintOutcome"), "injected")
        self.assertEqual((updated.get("guideState") or {}).get("state"), "step_0_pending")

    def test_intent_gate_rejects_generic_generation_query(self) -> None:
        payload = {
            "id": f"mw_ep_nuwa_gate_{self.suffix}",
            "taskFamily": f"使用技能前读取说明 {self.suffix}",
            "taskFamilySignature": f"wf:test_nuwa_gate:{self.suffix}",
            "canonicalTriggerPatterns": ["使用技能", "女娲技能", "huashu-nuwa", "造skill", "蒸馏"],
            "firstActionTriggers": ["fetch_skill_instructions", "huashu-nuwa"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "fetch_skill_instructions", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": ["先调用 fetch_skill_instructions(\"huashu-nuwa\") 读取技能说明。"],
            "finalSuccessEvidence": "user accepted the skill workflow",
            "confidence": 0.9,
        }

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")
            unrelated = workflow_memory_service.match_hints(
                query="帮我生成一张产品海报图片",
                scope_chain=["global"],
                limit=2,
            )
            related = workflow_memory_service.match_hints(
                query="使用女娲技能调研爱因斯坦生成一个爱因斯坦skill",
                scope_chain=["global"],
                limit=2,
            )

        self.assertNotIn(record["candidate"]["id"], [item.get("id") for item in unrelated])
        self.assertIn(record["candidate"]["id"], [item.get("id") for item in related])

    def test_guide_state_advances_next_step_for_same_session_run(self) -> None:
        payload = {
            "id": f"mw_ep_step_state_{self.suffix}",
            "taskFamily": f"女娲技能执行链 {self.suffix}",
            "taskFamilySignature": f"wf:test_step_state:{self.suffix}",
            "canonicalTriggerPatterns": ["女娲技能", "huashu-nuwa", "蒸馏"],
            "firstActionTriggers": ["fetch_skill_instructions"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "fetch_skill_instructions", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": [
                "先调用 fetch_skill_instructions(\"huashu-nuwa\")。",
                "将技能说明压成 task brief。",
                "由 supervisor 保留最终验收。",
            ],
            "finalSuccessEvidence": "user accepted the workflow",
            "confidence": 0.9,
        }
        session_id = f"session_{self.suffix}"
        run_id = f"run_{self.suffix}"
        db.create_or_update_session(session_id, title="workflow guide state test")
        db.create_run_record(run_id=run_id, session_id=session_id, run_type="test", status="running")
        self.session_ids.append(session_id)
        self.run_ids.append(run_id)

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")
            workflow_memory_service.record_guide_state(
                candidate_id=record["candidate"]["id"],
                query="使用女娲技能",
                session_id=session_id,
                run_id=run_id,
                state="first_action_seen",
                current_step_index=1,
            )
            block = workflow_memory_service.build_hints_block(
                query="使用女娲技能调研爱因斯坦生成一个爱因斯坦skill",
                scope_chain=["global"],
                session_id=session_id,
                run_id=run_id,
            )

        self.assertIn("Step 2/3", block)
        self.assertIn("将技能说明压成 task brief", block)
        self.assertNotIn("Next best step: 先调用 fetch_skill_instructions", block)

    def test_terminal_guide_state_suppresses_same_session_run_hint(self) -> None:
        payload = {
            "id": f"mw_ep_terminal_state_{self.suffix}",
            "taskFamily": f"女娲技能终态链 {self.suffix}",
            "taskFamilySignature": f"wf:test_terminal_state:{self.suffix}",
            "canonicalTriggerPatterns": ["女娲技能", "huashu-nuwa", "蒸馏"],
            "firstActionTriggers": ["fetch_skill_instructions"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "fetch_skill_instructions", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": [
                "先调用 fetch_skill_instructions(\"huashu-nuwa\")。",
                "将技能说明压成 task brief。",
            ],
            "finalSuccessEvidence": "user accepted the workflow",
            "confidence": 0.9,
        }
        session_id = f"session_terminal_{self.suffix}"
        run_id = f"run_terminal_{self.suffix}"
        db.create_or_update_session(session_id, title="workflow terminal state test")
        db.create_run_record(run_id=run_id, session_id=session_id, run_type="test", status="running")
        self.session_ids.append(session_id)
        self.run_ids.append(run_id)

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")
            workflow_memory_service.record_guide_state(
                candidate_id=record["candidate"]["id"],
                query="使用女娲技能",
                session_id=session_id,
                run_id=run_id,
                state="verified",
                current_step_index=1,
            )
            same_run_block = workflow_memory_service.build_hints_block(
                query="使用女娲技能调研爱因斯坦",
                scope_chain=["global"],
                session_id=session_id,
                run_id=run_id,
            )
            fresh_run_block = workflow_memory_service.build_hints_block(
                query="使用女娲技能调研爱因斯坦",
                scope_chain=["global"],
                session_id=f"other_{session_id}",
                run_id=f"other_{run_id}",
            )

        self.assertEqual(same_run_block, "")
        self.assertIn("[WORKFLOW HINTS]", fresh_run_block)

    def test_negative_hint_outcome_marks_terminal_state_for_same_run(self) -> None:
        payload = {
            "id": f"mw_ep_negative_state_{self.suffix}",
            "taskFamily": f"女娲技能否定链 {self.suffix}",
            "taskFamilySignature": f"wf:test_negative_state:{self.suffix}",
            "canonicalTriggerPatterns": ["女娲技能", "huashu-nuwa", "蒸馏"],
            "firstActionTriggers": ["fetch_skill_instructions"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "fetch_skill_instructions", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": ["先读取 huashu-nuwa 技能说明。"],
            "finalSuccessEvidence": "user accepted the workflow",
            "confidence": 0.9,
        }
        session_id = f"session_negative_{self.suffix}"
        run_id = f"run_negative_{self.suffix}"
        db.create_or_update_session(session_id, title="workflow negative state test")
        db.create_run_record(run_id=run_id, session_id=session_id, run_type="test", status="running")
        self.session_ids.append(session_id)
        self.run_ids.append(run_id)

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")
            workflow_memory_service.record_hint_event(
                candidate_id=record["candidate"]["id"],
                query="使用女娲技能",
                hint={"nextStep": "先读取 huashu-nuwa 技能说明。", "currentStepIndex": 0},
                session_id=session_id,
                run_id=run_id,
                outcome="contradicted",
            )
            block = workflow_memory_service.build_hints_block(
                query="使用女娲技能",
                scope_chain=["global"],
                session_id=session_id,
                run_id=run_id,
            )
            updated = workflow_memory_service.get_candidate(record["candidate"]["id"]) or {}

        self.assertEqual(block, "")
        self.assertEqual(updated.get("lastHintOutcome"), "contradicted")
        self.assertGreaterEqual(int(updated.get("negative_feedback_count") or 0), 1)

    def test_hint_outcomes_affect_future_matching(self) -> None:
        base_payload = {
            "taskFamily": f"女娲技能说明链 {self.suffix}",
            "canonicalTriggerPatterns": ["女娲技能", "huashu-nuwa", "蒸馏"],
            "firstActionTriggers": ["fetch_skill_instructions"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "fetch_skill_instructions", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": ["先读取 huashu-nuwa 技能说明。"],
            "finalSuccessEvidence": "user accepted the workflow",
            "confidence": 0.7,
        }
        helped_payload = {
            **base_payload,
            "id": f"mw_ep_helped_{self.suffix}",
            "taskFamilySignature": f"wf:test_helped:{self.suffix}",
            "confidence": 0.7,
        }
        neutral_payload = {
            **base_payload,
            "id": f"mw_ep_neutral_{self.suffix}",
            "taskFamilySignature": f"wf:test_neutral:{self.suffix}",
            "confidence": 0.71,
        }

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            helped = self._add_episode(helped_payload, extraction_source="runtime_evidence")["candidate"]
            neutral = self._add_episode(neutral_payload, extraction_source="runtime_evidence")["candidate"]
            workflow_memory_service.record_hint_event(
                candidate_id=helped["id"],
                query="使用女娲技能",
                hint={"nextStep": "先读取 huashu-nuwa 技能说明。"},
                outcome="helped_success",
            )
            ranked = workflow_memory_service.match_hints(
                query="使用女娲技能调研爱因斯坦",
                scope_chain=["global"],
                limit=2,
            )
            workflow_memory_service.record_hint_event(
                candidate_id=helped["id"],
                query="使用女娲技能",
                hint={"nextStep": "先读取 huashu-nuwa 技能说明。"},
                outcome="contradicted",
            )
            suppressed = workflow_memory_service.match_hints(
                query="使用女娲技能调研爱因斯坦",
                scope_chain=["global"],
                limit=5,
            )
            workflow_memory_service.record_hint_event(
                candidate_id=neutral["id"],
                query="使用女娲技能",
                hint={"nextStep": "先读取 huashu-nuwa 技能说明。"},
                outcome="caused_failure",
            )
            quarantined = workflow_memory_service.get_candidate(neutral["id"]) or {}

        self.assertEqual(ranked[0].get("id"), helped["id"])
        self.assertNotIn(helped["id"], [item.get("id") for item in suppressed])
        self.assertEqual(quarantined.get("status"), "quarantine")

    def _engineering_proof_entry(self, *, proof_id: str, verification_status: str, extra: dict | None = None) -> dict:
        entry = {
            "id": proof_id,
            "mode": "auto",
            "patchIntent": "Fix the admin workflow panel and keep proof evidence clean",
            "verificationStatus": verification_status,
            "changedFiles": ["apps/v8-agent-os-admin/src/components/memory/MemoryWorkflowsPanel.tsx"],
            "writeSet": ["apps/v8-agent-os-admin/src/components/memory/"],
            "commands": [
                {
                    "tool": "run_system_command",
                    "command": "npm run build",
                    "returnCode": 0 if verification_status == "verified" else 1 if verification_status == "failed_verification" else None,
                    "isValidation": True,
                    "summary": "admin build completed" if verification_status == "verified" else "admin build failed",
                }
            ],
            "diagnostics": {
                "items": [
                    {
                        "source": "command",
                        "kind": "build",
                        "returnCode": 0 if verification_status == "verified" else 1 if verification_status == "failed_verification" else None,
                        "summary": "admin build completed" if verification_status == "verified" else "admin build failed",
                    }
                ],
                "worksetCorrelation": {
                    "risk": "within_write_set",
                    "outsideWriteSetFiles": [],
                    "manualOverride": {"present": False},
                },
            },
            "metadata": {
                "engineeringMode": "auto",
                "triggerDecision": {"active": True, "reason": "test"},
                "workspaceRoot": str(self.workspace_root),
            },
            "residualRisks": [],
        }
        if extra:
            entry.update(extra)
        return entry

    def test_verified_engineering_proof_creates_active_engineering_candidate(self) -> None:
        proof_id = f"proof_verified_{self.suffix}"
        with patch("runtimes.memory.workflow_service.workflow_memory_config", return_value=_engineering_workflow_test_config()):
            result = workflow_memory_service.record_engineering_proof_episode(
                proof_entry=self._engineering_proof_entry(proof_id=proof_id, verification_status="verified"),
                workset_observations=[],
            )
        self.episode_ids.append(result["episode"]["id"])
        self.candidate_ids.append(result["candidate"]["id"])

        candidate = result["candidate"]
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(candidate["workflowClass"], "engineering")
        self.assertEqual(candidate["sourceRuntime"], "engineering_lane")
        self.assertTrue(candidate["proofBacked"])
        self.assertTrue(candidate["verificationBacked"])
        self.assertEqual(candidate["lastVerificationStatus"], "verified")
        self.assertEqual(candidate["status"], "active_hint")
        self.assertIn(proof_id, candidate["proofEntryIds"])
        self.assertTrue(candidate["goldenPathSteps"])

    def test_engineering_verification_steps_do_not_store_absolute_command_paths(self) -> None:
        proof_id = f"proof_path_sanitized_{self.suffix}"
        with patch("runtimes.memory.workflow_service.workflow_memory_config", return_value=_engineering_workflow_test_config()):
            result = workflow_memory_service.record_engineering_proof_episode(
                proof_entry=self._engineering_proof_entry(
                    proof_id=proof_id,
                    verification_status="verified",
                    extra={
                        "commands": [
                            {
                                "tool": "run_system_command",
                                "command": "python -m py_compile E:\\Projects\\v8chat\\v8-agent-os\\apps\\v8-agent-os-engine\\core\\database.py",
                                "returnCode": 0,
                                "isValidation": True,
                                "summary": "compiled",
                            }
                        ]
                    },
                ),
                workset_observations=[],
            )
        self.episode_ids.append(result["episode"]["id"])
        self.candidate_ids.append(result["candidate"]["id"])

        joined = "\n".join(result["candidate"].get("verificationSteps") or [])
        self.assertNotIn("E:\\Projects", joined)
        self.assertIn("Python validation", joined)

    def test_unverified_engineering_proof_stays_candidate(self) -> None:
        proof_id = f"proof_unverified_{self.suffix}"
        with patch("runtimes.memory.workflow_service.workflow_memory_config", return_value=_engineering_workflow_test_config()):
            result = workflow_memory_service.record_engineering_proof_episode(
                proof_entry=self._engineering_proof_entry(
                    proof_id=proof_id,
                    verification_status="unverified",
                    extra={"commands": [], "diagnostics": {"worksetCorrelation": {"risk": "within_write_set"}}},
                ),
                workset_observations=[],
            )
        self.episode_ids.append(result["episode"]["id"])
        self.candidate_ids.append(result["candidate"]["id"])

        candidate = result["candidate"]
        self.assertEqual(candidate["workflowClass"], "engineering")
        self.assertTrue(candidate["proofBacked"])
        self.assertFalse(candidate["verificationBacked"])
        self.assertEqual(candidate["lastVerificationStatus"], "unverified")
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["goldenPathSteps"], [])

    def test_failed_engineering_proof_learns_anti_pattern_only(self) -> None:
        proof_id = f"proof_failed_{self.suffix}"
        with patch("runtimes.memory.workflow_service.workflow_memory_config", return_value=_engineering_workflow_test_config()):
            result = workflow_memory_service.record_engineering_proof_episode(
                proof_entry=self._engineering_proof_entry(proof_id=proof_id, verification_status="failed_verification"),
                workset_observations=[],
            )
        self.episode_ids.append(result["episode"]["id"])
        self.candidate_ids.append(result["candidate"]["id"])

        candidate = result["candidate"]
        self.assertEqual(candidate["workflowClass"], "engineering")
        self.assertEqual(candidate["lastVerificationStatus"], "failed_verification")
        self.assertIn(candidate["status"], {"candidate", "quarantine"})
        self.assertEqual(candidate["goldenPathSteps"], [])
        self.assertTrue(any("失败验证" in item or "failed" in item.lower() for item in candidate["antiPatterns"]))

    def test_engineering_workflow_only_matches_when_engineering_active(self) -> None:
        proof_id = f"proof_hint_{self.suffix}"
        with patch("runtimes.memory.workflow_service.workflow_memory_config", return_value=_engineering_workflow_test_config()):
            result = workflow_memory_service.record_engineering_proof_episode(
                proof_entry=self._engineering_proof_entry(proof_id=proof_id, verification_status="verified"),
                workset_observations=[],
            )
            candidate_id = result["candidate"]["id"]
            inactive = workflow_memory_service.match_hints(
                query="工程任务 admin build 需要验证",
                scope_chain=[canonical_workspace_scope(str(self.workspace_root))],
                limit=4,
                engineering_active=False,
            )
            active = workflow_memory_service.match_hints(
                query="工程任务 admin build 需要验证",
                scope_chain=[canonical_workspace_scope(str(self.workspace_root))],
                limit=4,
                engineering_active=True,
            )
        self.episode_ids.append(result["episode"]["id"])
        self.candidate_ids.append(candidate_id)

        self.assertNotIn(candidate_id, [item.get("id") for item in inactive])
        self.assertIn(candidate_id, [item.get("id") for item in active])

    def test_same_workflow_signature_is_isolated_by_physical_workspace(self) -> None:
        alpha = Path(self.workspace_temp.name) / "alpha"
        beta = Path(self.workspace_temp.name) / "beta"
        alpha.mkdir()
        beta.mkdir()
        base_payload = {
            "taskFamily": f"Shared build workflow {self.suffix}",
            "taskFamilySignature": f"wf:shared:{self.suffix}",
            "canonicalTriggerPatterns": ["shared build workflow"],
            "goldenPathSteps": ["run scoped verification"],
            "finalSuccessEvidence": "verified",
            "runtimeEvidence": [{"status": "verified"}],
            "evidenceSource": "runtime_events",
            "confidence": 0.9,
        }
        with patch(
            "runtimes.memory.workflow_service.workflow_memory_config",
            return_value=_workflow_test_config(),
        ):
            alpha_episode = workflow_memory_service.normalize_episode_payload(
                {**base_payload, "id": f"mw_ep_alpha_{self.suffix}"},
                session_id=None,
                run_id=None,
                scope=canonical_workspace_scope(str(alpha)),
            )
            beta_episode = workflow_memory_service.normalize_episode_payload(
                {**base_payload, "id": f"mw_ep_beta_{self.suffix}"},
                session_id=None,
                run_id=None,
                scope=canonical_workspace_scope(str(beta)),
            )
            alpha_record = workflow_memory_service.add_episode(alpha_episode)
            beta_record = workflow_memory_service.add_episode(beta_episode)

        for record in (alpha_record, beta_record):
            self.episode_ids.append(record["episode"]["id"])
            self.candidate_ids.append(record["candidate"]["id"])
        self.assertNotEqual(alpha_episode["task_family_signature"], beta_episode["task_family_signature"])
        self.assertNotEqual(alpha_record["candidate"]["id"], beta_record["candidate"]["id"])

        alpha_hints = workflow_memory_service.match_hints(
            query="shared build workflow",
            scope_chain=["global", canonical_workspace_scope(str(alpha))],
            limit=5,
        )
        self.assertIn(alpha_record["candidate"]["id"], [item.get("id") for item in alpha_hints])
        self.assertNotIn(beta_record["candidate"]["id"], [item.get("id") for item in alpha_hints])

        with self.assertRaisesRegex(ValueError, "workflow_candidate_scope_mismatch"):
            workflow_memory_service.merge_candidates(
                alpha_record["candidate"]["id"],
                [beta_record["candidate"]["id"]],
            )

    def test_engineering_proof_without_physical_workspace_is_not_learned(self) -> None:
        entry = self._engineering_proof_entry(
            proof_id=f"proof_missing_workspace_{self.suffix}",
            verification_status="verified",
            extra={"metadata": {"engineeringMode": "auto", "triggerDecision": {"active": True}}},
        )
        result = workflow_memory_service.record_engineering_proof_episode(
            proof_entry=entry,
            workset_observations=[],
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "workspace_identity_missing")


if __name__ == "__main__":
    unittest.main()
