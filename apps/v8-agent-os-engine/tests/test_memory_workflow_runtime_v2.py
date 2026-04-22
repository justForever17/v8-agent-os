from __future__ import annotations

import uuid
import unittest
from unittest.mock import patch

from core.database import db
from core.realtime_protocol import utc_now_iso
from runtimes.memory.workflow_service import workflow_memory_service


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


class MemoryWorkflowRuntimeV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
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

    def test_planner_context_downgrades_hint_to_checklist_bias(self) -> None:
        payload = {
            "id": f"mw_ep_planner_{self.suffix}",
            "taskFamily": f"Planner-aware docs workflow {self.suffix}",
            "taskFamilySignature": f"wf:test_planner_aware:{self.suffix}",
            "canonicalTriggerPatterns": ["planner docs workflow", "governance doc"],
            "firstActionTriggers": ["read_native_file"],
            "runtimeEvidence": [{"topic": "tool.finished", "tool": "read_native_file", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": [
                "Read the governance document first.",
                "Convert the findings into a task brief.",
            ],
            "verificationSteps": ["Confirm the task brief matches the planner acceptance contract."],
            "finalSuccessEvidence": "planner consumed the checklist",
            "confidence": 0.88,
        }
        session_id = f"session_planner_{self.suffix}"
        run_id = f"run_planner_{self.suffix}"
        db.create_or_update_session(session_id, title="workflow planner aware test")
        db.create_run_record(run_id=run_id, session_id=session_id, run_type="test", status="running")
        self.session_ids.append(session_id)
        self.run_ids.append(run_id)
        db.add_runtime_event(
            {
                "event_id": f"evt_planner_{self.suffix}",
                "session_id": session_id,
                "run_id": run_id,
                "seq": db.get_latest_runtime_seq(session_id) + 1,
                "kind": "projection",
                "topic": "planner.plan.projected",
                "ts": utc_now_iso(),
                "source": {"runtimeId": "planner_lane"},
                "payload": {
                    "planId": "plan_test",
                    "taskBriefs": [{"taskBriefId": "tb_1", "goal": "Review the governance doc"}],
                },
            }
        )

        with patch("runtimes.memory.workflow_service.workflow_memory_config", side_effect=_workflow_test_config):
            record = self._add_episode(payload, extraction_source="runtime_evidence")
            block = workflow_memory_service.build_hints_block(
                query=f"Please use the planner docs workflow {self.suffix}",
                scope_chain=["global"],
                session_id=session_id,
                run_id=run_id,
            )
            hint_events = workflow_memory_service.list_hint_events(candidate_id=record["candidate"]["id"], limit=5)

        self.assertIn("Delivery mode: checklist / bias", block)
        self.assertIn("Checklist focus", block)
        self.assertIn("plan=plan_test", block)
        self.assertIn("task=tb_1", block)
        self.assertTrue(hint_events)
        self.assertTrue(hint_events[0].get("metadata", {}).get("plannerAware"))
        self.assertEqual(hint_events[0].get("metadata", {}).get("deliveryMode"), "planner_checklist_bias")

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


if __name__ == "__main__":
    unittest.main()
