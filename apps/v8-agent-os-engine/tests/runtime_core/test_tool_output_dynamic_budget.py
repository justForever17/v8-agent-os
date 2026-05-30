from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from graph.tool_routing import (
    DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS,
    _tool_output_budget_for_request,
    _truncate_agent_visible_result,
    _truncate_tool_message_content,
)


class ToolOutputDynamicBudgetTest(unittest.TestCase):
    def _request(self, tool_name: str, messages=None, config=None):
        return SimpleNamespace(
            tool_call={"name": tool_name, "id": "tool-call-1", "args": {}},
            state={"messages": list(messages or [])},
            config=dict(config or {}),
        )

    def test_default_budget_is_below_15k_hard_ceiling(self):
        budget = _tool_output_budget_for_request(self._request("read_native_file"), "read_native_file")

        self.assertLess(budget["agentVisibleBudget"], DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS)
        self.assertEqual(budget["hardMaxChars"], DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS)
        self.assertEqual(budget["budgetSource"], "dynamic_context_budget")

    def test_long_context_models_raise_clean_web_budget(self):
        budget = _tool_output_budget_for_request(
            self._request(
                "web_read",
                config={"configurable": {"contextWindowTokens": 1_000_000, "reservedOutputTokens": 4096}},
            ),
            "web_read",
        )

        self.assertEqual(budget["toolOutputKind"], "web")
        self.assertGreaterEqual(budget["agentVisibleBudget"], 50_000)
        self.assertEqual(budget["baseTargetChars"], 16_000)

    def test_budget_shrinks_when_context_is_nearly_full(self):
        nearly_full = HumanMessage(content="x" * 110_000)
        request = self._request(
            "read_native_file",
            messages=[nearly_full],
            config={"configurable": {"contextWindowTokens": 32000, "reservedOutputTokens": 2048}},
        )

        budget = _tool_output_budget_for_request(request, "read_native_file")

        self.assertLessEqual(budget["agentVisibleBudget"], 2000)
        self.assertGreaterEqual(budget["agentVisibleBudget"], 1200)

    def test_json_truncation_uses_plain_summary_and_detail_ref(self):
        payload = {
            "runId": "run-1",
            "toolCallId": "tool-call-1",
            "rawRef": "raw://tool/1",
            "recommendedNextAction": "ask_user",
            "verification": {"status": "needs_human_attention", "reason": "login gate"},
            "candidateBoard": [{"id": index, "text": "candidate" * 200} for index in range(100)],
            "debug": "noise" * 5000,
        }
        message = ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            name="computer_use_execute_task",
            tool_call_id="tool-call-1",
        )

        truncated = _truncate_tool_message_content(
            message,
            {
                "budgetSource": "dynamic_context_budget",
                "agentVisibleBudget": 2400,
                "hardMaxChars": 15000,
            },
        )
        content = str(truncated.content)

        self.assertLessEqual(len(content), 2400)
        self.assertFalse(content.lstrip().startswith("{"))
        self.assertNotIn("_v8ToolSurface", content)
        self.assertNotIn('"debug"', content)
        self.assertIn("computer use execute task result", content)
        self.assertIn("Verification: needs_human_attention | login gate", content)
        self.assertIn("Next: ask_user", content)
        self.assertIn("tool_observation_detail(raw_ref='toolobs://", content)

    def test_web_surface_truncation_preserves_detail_ref(self):
        message = ToolMessage(
            content=json.dumps(
                {
                    "ok": True,
                    "mode": "read",
                    "title": "Long page",
                    "url": "https://example.com/long",
                    "text": "important opening\n" + ("body line\n" * 3000),
                    "extractionQuality": "usable",
                },
                ensure_ascii=False,
            ),
            name="web_broker",
            tool_call_id="tool-call-web",
        )

        truncated = _truncate_tool_message_content(
            message,
            {
                "budgetSource": "dynamic_context_budget",
                "agentVisibleBudget": 1600,
                "hardMaxChars": 60000,
            },
        )
        content = str(truncated.content)

        self.assertLessEqual(len(content), 1600)
        self.assertIn("Web broker", content)
        self.assertIn("tool_observation_detail(raw_ref='toolobs://", content)
        self.assertIn("Raw: toolobs://", content)

    def test_worker_result_marker_is_preserved(self):
        marker = '<V8_WORKER_RESULT>{"status":"succeeded","summary":"done"}</V8_WORKER_RESULT>'
        message = ToolMessage(
            content=("banner\n" + "x" * 5000 + marker + "tail\n" + "y" * 5000),
            name="command_session_broker",
            tool_call_id="tool-call-1",
        )

        truncated = _truncate_tool_message_content(
            message,
            {
                "budgetSource": "dynamic_context_budget",
                "agentVisibleBudget": 1800,
                "hardMaxChars": 15000,
            },
        )

        self.assertIn(marker, str(truncated.content))
        self.assertEqual(
            truncated.response_metadata["v8_tool_output_budget"]["semanticTruncationStrategy"],
            "worker_result_marker_preserving",
        )

    def test_command_update_messages_use_same_guard(self):
        message = ToolMessage(
            content=json.dumps(
                {
                    "jobId": "job-1",
                    "providerTaskId": "task-1",
                    "operationKind": "video.text_to_video",
                    "artifactIds": ["artifact-1"],
                    "providerResponse": "z" * 8000,
                }
            ),
            name="creative_media_get_job",
            tool_call_id="tool-call-1",
        )
        command = Command(update={"messages": [message], "other": "kept"})

        result = _truncate_agent_visible_result(
            command,
            {
                "budgetSource": "dynamic_context_budget",
                "agentVisibleBudget": 2000,
                "hardMaxChars": 15000,
            },
        )
        result_message = result.update["messages"][0]
        content = str(result_message.content)

        self.assertEqual(result.update["other"], "kept")
        self.assertIn("Creative Media get_job", content)
        self.assertIn("job-1", content)
        self.assertIn("video.text_to_video", content)
        self.assertIn("Artifacts: 1", content)
        self.assertIn("providerResponse", content)


if __name__ == "__main__":
    unittest.main()
