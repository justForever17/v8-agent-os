from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


from core.context_orchestrator import ContextOrchestrator  # noqa: E402


def _long_text(prefix: str, repeat: int = 240) -> str:
    return ((prefix + " ") * repeat).strip()


class ContextOrchestratorGovernanceTests(unittest.TestCase):
    def test_prepare_compacts_only_old_non_system_messages_and_keeps_last_human(self):
        orchestrator = ContextOrchestrator()
        messages = [
            SystemMessage(content="SYSTEM persona must remain."),
            HumanMessage(content=_long_text("old user goal and constraint")),
            AIMessage(content=_long_text("old assistant reasoning and status")),
            HumanMessage(
                content=_long_text("mid user context that will be compressed"),
                additional_kwargs={
                    "context_adapter_blocks": [
                        {
                            "type": "memory_recall",
                            "title": "Memory Recall",
                            "content": "remembered preference: 中文输出",
                        }
                    ]
                },
            ),
            AIMessage(content=_long_text("mid assistant execution trace")),
            HumanMessage(content="latest human question must survive"),
        ]

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "compression": {
                "enabled": True,
                "default_context_window_tokens": 600,
                "soft_trigger_ratio": 0.20,
                "hard_trigger_ratio": 0.40,
                "keep_recent_messages": 2,
                "use_llm_summary": False,
                "max_summary_input_tokens": 1200,
                "max_summary_input_messages": 20,
                "max_summary_output_tokens": 256,
            }
        }), patch("core.context_orchestrator.llm_factory.get_model_context_window", return_value=600), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ):
            prepared = orchestrator.prepare(
                messages=messages,
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="test-model",
                resolved_scope="workspace:main",
                scope_chain=["global", "workspace:main"],
                leading_system_content="LEADING SYSTEM CONTENT",
            )

        self.assertTrue(prepared.audit["compaction_applied"])
        self.assertEqual(prepared.audit["compaction_method"], "rule_summary")
        self.assertIn("history_summary", prepared.audit["block_types"])
        self.assertIn("memory_recall", prepared.audit["block_types"])
        rendered_system_text = "\n".join(
            str(message.content)
            for message in prepared.messages
            if isinstance(message, SystemMessage)
        )
        self.assertIn("LEADING SYSTEM CONTENT", rendered_system_text)
        self.assertIn("SYSTEM persona must remain.", rendered_system_text)
        self.assertIn("[CONTEXT BLOCK: HISTORY_SUMMARY]", rendered_system_text)
        self.assertIn("remembered preference: 中文输出", rendered_system_text)
        self.assertEqual(prepared.messages[-1].content, "latest human question must survive")

    def test_prepare_uses_llm_summary_on_hard_overflow_when_enabled(self):
        orchestrator = ContextOrchestrator()
        messages = [
            SystemMessage(content="SYSTEM persona must remain."),
            HumanMessage(content=_long_text("user goal", 320)),
            AIMessage(content=_long_text("assistant reply", 320)),
            HumanMessage(content=_long_text("more context", 320)),
            AIMessage(content=_long_text("execution details", 320)),
            HumanMessage(content="latest human task"),
        ]

        fake_llm = SimpleNamespace(
            invoke=lambda _messages, config=None: AIMessage(content="deterministic llm summary")  # noqa: ARG005
        )

        with patch("core.context_orchestrator.storage.get_context_config", return_value={
            "compression": {
                "enabled": True,
                "default_context_window_tokens": 500,
                "soft_trigger_ratio": 0.10,
                "hard_trigger_ratio": 0.20,
                "keep_recent_messages": 2,
                "use_llm_summary": True,
                "max_summary_input_tokens": 1200,
                "max_summary_input_messages": 20,
                "max_summary_output_tokens": 128,
            }
        }), patch("core.context_orchestrator.storage.get_role_model_id", return_value="summary-model"), patch(
            "core.context_orchestrator.llm_factory.get_model_context_window",
            return_value=500,
        ), patch(
            "core.context_orchestrator.llm_factory.create_chat_model",
            return_value=fake_llm,
        ), patch(
            "core.context_orchestrator.flush_before_context_compaction",
            return_value={"ok": True, "skipped": False, "reason": "test"},
        ):
            prepared = orchestrator.prepare(
                messages=messages,
                runtime_kind="chat",
                target_role="supervisor",
                resolved_model_id="test-model",
            )

        self.assertTrue(prepared.audit["compaction_applied"])
        self.assertEqual(prepared.audit["trigger_reason"], "hard_token_budget")
        self.assertEqual(prepared.audit["compaction_method"], "llm_summary")
        self.assertEqual(prepared.blocks[0].metadata.get("summary_method"), "llm_summary")
        self.assertIn("deterministic llm summary", prepared.blocks[0].content)


if __name__ == "__main__":
    unittest.main()
