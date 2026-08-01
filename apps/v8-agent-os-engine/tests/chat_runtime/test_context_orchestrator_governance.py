from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage



from core.background_context_guard import build_background_guard_messages, prepare_background_model_messages  # noqa: E402
from core.context_compaction_baseline import digest_messages  # noqa: E402
from core.context_orchestrator import ContextOrchestrator  # noqa: E402


def _long_text(prefix: str, repeat: int = 220) -> str:
    return ((prefix + " ") * repeat).strip()


class ContextOrchestratorGovernanceTests(unittest.TestCase):
    def test_prepare_creates_and_reuses_persistent_baseline(self):
        orchestrator = ContextOrchestrator()
        snapshot_holder: dict[str, dict] = {}
        messages = [
            SystemMessage(content="SYSTEM persona must remain."),
            HumanMessage(content=_long_text("old user goal and constraint")),
            AIMessage(content=_long_text("old assistant reasoning and status")),
            HumanMessage(content=_long_text("mid user context that will be compressed")),
            AIMessage(content=_long_text("mid assistant execution trace")),
            HumanMessage(content="latest human question must survive"),
        ]

        def _load_snapshot(*, session_id: str, target_role: str):  # noqa: ANN001
            return snapshot_holder.get(f"{session_id}:{target_role}")

        def _persist_snapshot(**kwargs):  # noqa: ANN003
            snapshot = {
                "coveredMessageCount": len(kwargs["covered_messages"]),
                "coveredMessagesHash": digest_messages(kwargs["covered_messages"]),
                "baselineText": kwargs["baseline_text"],
                "summaryMethod": kwargs["summary_method"],
                "chunked": kwargs["chunked"],
            }
            snapshot_holder[f"{kwargs['session_id']}:{kwargs['target_role']}"] = snapshot
            return snapshot

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "compression": {
                "enabled": True,
                "mode": "persistent_baseline",
                "default_context_window_tokens": 600,
                "trigger_ratio": 0.22,
                "hard_trigger_ratio": 0.22,
                "keep_recent_turns": 1,
                "keep_recent_messages": 2,
                "use_llm_summary": False,
                "max_summary_input_tokens": 1200,
                "max_summary_input_messages": 20,
                "max_summary_output_tokens": 256,
            }
        }), patch("core.context_orchestrator.llm_factory.get_model_context_window", return_value=600), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ), patch("core.context_orchestrator.get_runtime_context", return_value={"session_id": "session-persistent"}), patch(
            "core.context_orchestrator.load_compaction_baseline",
            side_effect=_load_snapshot,
        ), patch(
            "core.context_orchestrator.persist_compaction_baseline",
            side_effect=_persist_snapshot,
        ):
            first = orchestrator.prepare(
                messages=messages,
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="test-model",
                resolved_scope="workspace:main",
                scope_chain=["global", "workspace:main"],
                leading_system_content="LEADING SYSTEM CONTENT",
            )
            second = orchestrator.prepare(
                messages=messages,
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="test-model",
                resolved_scope="workspace:main",
                scope_chain=["global", "workspace:main"],
                leading_system_content="LEADING SYSTEM CONTENT",
            )

        self.assertTrue(first.audit["compaction_applied"])
        self.assertTrue(first.audit["baseline_refreshed"])
        self.assertGreater(first.audit["baseline_message_count"], 0)
        self.assertIn("history_summary", first.audit["block_types"])
        self.assertTrue(second.audit["baseline_active"])
        self.assertFalse(second.audit["baseline_refreshed"])
        self.assertTrue(str(second.audit["trigger_reason"]).startswith("baseline_reused"))
        rendered_system_text = "\n".join(
            str(message.content)
            for message in second.messages
            if isinstance(message, SystemMessage)
        )
        self.assertIn("LEADING SYSTEM CONTENT", rendered_system_text)
        self.assertIn("SYSTEM persona must remain.", rendered_system_text)
        self.assertEqual(second.messages[-1].content, "latest human question must survive")

    def test_prepare_rolls_forward_existing_baseline_without_waiting_for_reoverflow(self):
        orchestrator = ContextOrchestrator()
        old_messages = [
            HumanMessage(content="old turn 1"),
            AIMessage(content="old answer 1"),
            HumanMessage(content="old turn 2 should move into baseline"),
            AIMessage(content="old answer 2 should move into baseline"),
        ]
        seeded_snapshot = {
            "coveredMessageCount": 2,
            "coveredMessagesHash": digest_messages(old_messages[:2]),
            "baselineText": "seeded baseline for first turn",
            "summaryMethod": "rule_summary",
            "chunked": False,
        }
        persisted: list[dict] = []

        def _persist_snapshot(**kwargs):  # noqa: ANN003
            snapshot = {
                "coveredMessageCount": len(kwargs["covered_messages"]),
                "coveredMessagesHash": digest_messages(kwargs["covered_messages"]),
                "baselineText": kwargs["baseline_text"],
                "summaryMethod": kwargs["summary_method"],
                "chunked": kwargs["chunked"],
            }
            persisted.append(snapshot)
            return snapshot

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "compression": {
                "enabled": True,
                "mode": "persistent_baseline",
                "default_context_window_tokens": 100000,
                "trigger_ratio": 0.94,
                "hard_trigger_ratio": 0.94,
                "keep_recent_turns": 1,
                "keep_recent_messages": 2,
                "use_llm_summary": False,
                "max_summary_input_tokens": 1200,
                "max_summary_input_messages": 20,
                "max_summary_output_tokens": 256,
            }
        }), patch("core.context_orchestrator.llm_factory.get_model_context_window", return_value=100000), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ), patch("core.context_orchestrator.get_runtime_context", return_value={"session_id": "session-roll-forward"}), patch(
            "core.context_orchestrator.load_compaction_baseline",
            return_value=seeded_snapshot,
        ), patch(
            "core.context_orchestrator.persist_compaction_baseline",
            side_effect=_persist_snapshot,
        ):
            prepared = orchestrator.prepare(
                messages=[SystemMessage(content="SYSTEM")] + old_messages + [HumanMessage(content="latest question")],
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="test-model",
            )

        self.assertTrue(prepared.audit["baseline_active"])
        self.assertTrue(prepared.audit["baseline_refreshed"])
        self.assertEqual(prepared.audit["trigger_reason"], "baseline_refreshed")
        self.assertGreaterEqual(prepared.audit["baseline_message_count"], 3)
        self.assertTrue(persisted)

    def test_prepare_chunks_llm_summary_when_summary_model_window_is_smaller(self):
        orchestrator = ContextOrchestrator()
        messages = [
            SystemMessage(content="SYSTEM persona must remain."),
            HumanMessage(content=_long_text("user goal", 180)),
            AIMessage(content=_long_text("assistant reply", 180)),
            HumanMessage(content=_long_text("more context", 180)),
            AIMessage(content=_long_text("execution details", 180)),
            HumanMessage(content=_long_text("final task", 120)),
        ]
        fake_llm = SimpleNamespace(
            invoke=lambda _messages, config=None: AIMessage(content="deterministic llm summary")  # noqa: ARG005
        )

        def _context_window(model_id: str | None):  # noqa: ANN001
            if str(model_id or "").strip() == "summary-model":
                return 128
            return 1000

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "compression": {
                "enabled": True,
                "mode": "persistent_baseline",
                "default_context_window_tokens": 1000,
                "trigger_ratio": 0.20,
                "hard_trigger_ratio": 0.20,
                "keep_recent_turns": 1,
                "keep_recent_messages": 2,
                "use_llm_summary": True,
                "max_summary_input_tokens": 400,
                "max_summary_input_messages": 10,
                "max_summary_output_tokens": 128,
                "compression_model_safety_ratio": 0.9,
            }
        }), patch("core.context_orchestrator.storage.get_role_model_id", return_value="summary-model"), patch(
            "core.context_orchestrator.llm_factory.get_model_context_window",
            side_effect=_context_window,
        ), patch(
            "core.context_orchestrator.llm_factory.create_chat_model",
            return_value=fake_llm,
        ), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ), patch("core.context_orchestrator.get_runtime_context", return_value={}):
            prepared = orchestrator.prepare(
                messages=messages,
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="test-model",
            )

        self.assertTrue(prepared.audit["compaction_applied"])
        self.assertEqual(prepared.audit["compaction_method"], "llm_summary")
        self.assertTrue(bool(prepared.blocks[0].metadata.get("chunked")))
        self.assertIn("deterministic llm summary", prepared.blocks[0].content)

    def test_live_audit_can_force_compaction_without_changing_model_config(self):
        orchestrator = ContextOrchestrator()
        persisted: list[dict] = []
        messages = [
            HumanMessage(content=_long_text("old live audit goal", 260)),
            AIMessage(content=_long_text("old live audit status", 260)),
            HumanMessage(content=_long_text("middle live audit context", 260)),
            AIMessage(content=_long_text("middle live audit execution", 260)),
            HumanMessage(content="latest runtime/subagent continuation request"),
        ]

        def _persist_snapshot(**kwargs):  # noqa: ANN003
            snapshot = {
                "coveredMessageCount": len(kwargs["covered_messages"]),
                "coveredMessagesHash": digest_messages(kwargs["covered_messages"]),
                "baselineText": kwargs["baseline_text"],
                "summaryMethod": kwargs["summary_method"],
                "chunked": kwargs["chunked"],
            }
            persisted.append(snapshot)
            return snapshot

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "schema_version": 3,
            "compression": {
                "enabled": True,
                "mode": "persistent_baseline",
                "default_context_window_tokens": 32000,
                "trigger_ratio": 0.94,
                "hard_trigger_ratio": 0.94,
                "keep_recent_turns": 4,
                "keep_recent_messages": 8,
                "use_llm_summary": True,
                "max_summary_input_tokens": 5000,
                "max_summary_input_messages": 60,
                "max_summary_output_tokens": 800,
            },
        }), patch("core.context_orchestrator.llm_factory.get_model_context_window", return_value=1_000_000), patch(
            "core.context_orchestrator.storage.get_role_model_id",
            return_value="summary-model",
        ), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ), patch("core.context_orchestrator.get_runtime_context", return_value={
            "session_id": "session-live-audit",
            "run_id": "run-live-audit",
            "live_audit": {
                "runtimeSubagentClosureLiveAudit": True,
                "preferContextCompaction": True,
            },
        }), patch(
            "core.context_orchestrator.load_compaction_baseline",
            return_value=None,
        ), patch(
            "core.context_orchestrator.persist_compaction_baseline",
            side_effect=_persist_snapshot,
        ):
            prepared = orchestrator.prepare(
                messages=messages,
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="large-window-model",
            )

        self.assertTrue(prepared.audit["compaction_applied"])
        self.assertEqual(
            prepared.audit["context_governance_reason"],
            "runtime_subagent_closure_live_audit_forced_compaction",
        )
        self.assertEqual(prepared.audit["effective_context_window_tokens"], 2048)
        self.assertGreater(prepared.audit["estimated_effective_input_tokens"], 0)
        self.assertIn("history_summary", prepared.audit["block_types"])
        self.assertTrue(persisted)

    def test_background_guard_turns_long_material_into_compactable_history(self):
        messages = build_background_guard_messages(
            system_prompt="system",
            instruction="Return JSON for the prepared material.",
            materials=[
                {
                    "title": "Huge background",
                    "kind": "memory_transcript",
                    "content": _long_text("background detail", 900),
                }
            ],
            chunk_chars=900,
        )

        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIsInstance(messages[-1], HumanMessage)
        self.assertEqual(messages[-1].content, "Return JSON for the prepared material.")
        background_chunks = [
            message
            for message in messages
            if isinstance(message, HumanMessage)
            and str(message.content).startswith("[BACKGROUND MATERIAL:")
        ]
        self.assertGreater(len(background_chunks), 1)

    def test_background_guard_chunks_reconstruct_material_without_losing_boundary_spaces(self):
        material = ("evidence sentence. [S1] " * 90).strip()
        messages = build_background_guard_messages(
            system_prompt="system",
            instruction="review",
            materials=[{"title": "Exact answer", "kind": "candidate", "content": material}],
            chunk_chars=800,
        )
        chunks: list[str] = []
        for message in messages:
            content = str(getattr(message, "content", ""))
            if not content.startswith("[BACKGROUND MATERIAL: Exact answer |"):
                continue
            _header, body = content.split("\n", 1)
            chunks.append(body.rsplit("\n[/BACKGROUND MATERIAL]", 1)[0])

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), material)

    def test_background_guard_compacts_material_without_dropping_final_instruction(self):
        persisted: list[dict] = []

        def _persist_snapshot(**kwargs):  # noqa: ANN003
            snapshot = {
                "coveredMessageCount": len(kwargs["covered_messages"]),
                "coveredMessagesHash": digest_messages(kwargs["covered_messages"]),
                "baselineText": kwargs["baseline_text"],
                "summaryMethod": kwargs["summary_method"],
                "chunked": kwargs["chunked"],
            }
            persisted.append(snapshot)
            return snapshot

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "compression": {
                "enabled": True,
                "mode": "persistent_baseline",
                "default_context_window_tokens": 700,
                "trigger_ratio": 0.20,
                "hard_trigger_ratio": 0.20,
                "keep_recent_turns": 1,
                "keep_recent_messages": 2,
                "use_llm_summary": False,
                "max_summary_input_tokens": 1200,
                "max_summary_input_messages": 20,
                "max_summary_output_tokens": 256,
            }
        }), patch("core.context_orchestrator.llm_factory.get_model_context_window", return_value=700), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ), patch("core.context_orchestrator.get_runtime_context", return_value={
            "session_id": "session-background-guard",
            "run_id": "run-background-guard",
        }), patch(
            "core.context_orchestrator.load_compaction_baseline",
            return_value=None,
        ), patch(
            "core.context_orchestrator.persist_compaction_baseline",
            side_effect=_persist_snapshot,
        ):
            prepared = prepare_background_model_messages(
                system_prompt="You are a background JSON worker.",
                instruction="Return the final JSON object now.",
                materials=[
                    {
                        "title": "Periodic memory logs",
                        "kind": "memory_maintenance",
                        "content": _long_text("nightly memory maintenance material", 600),
                    }
                ],
                runtime_kind="memory",
                target_role="memory:maintenance",
                resolved_model_id="test-model",
                component="test",
                node="background_guard",
                emit_event=False,
            )

        self.assertTrue(prepared.audit["compaction_applied"])
        self.assertIn("history_summary", prepared.audit["block_types"])
        self.assertEqual(prepared.messages[-1].content, "Return the final JSON object now.")
        self.assertTrue(persisted)


if __name__ == "__main__":
    unittest.main()

