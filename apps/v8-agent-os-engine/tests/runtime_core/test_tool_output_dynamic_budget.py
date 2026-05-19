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

    def test_json_truncation_preserves_decision_critical_fields(self):
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
        parsed = json.loads(content)

        self.assertLessEqual(len(content), 2400)
        self.assertEqual(parsed["runId"], "run-1")
        self.assertEqual(parsed["toolCallId"], "tool-call-1")
        self.assertEqual(parsed["rawRef"], "raw://tool/1")
        self.assertEqual(parsed["recommendedNextAction"], "ask_user")
        self.assertIn("verification", parsed)
        self.assertTrue(parsed["_v8ToolSurface"]["truncated"])
        self.assertGreater(parsed["_v8ToolSurface"]["omittedChars"], 0)
        self.assertIn("rawRef", parsed["_v8ToolSurface"])

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
