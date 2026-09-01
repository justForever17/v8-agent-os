from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from core.response_normalizer import extract_text_and_reasoning, sanitize_model_tool_calls
from graph.compat import sanitize_response_tool_calls


class ResponseNormalizerToolCallIdTests(unittest.TestCase):
    def test_reasoning_preserves_stream_token_whitespace_and_line_breaks(self):
        first = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": "I need"},
            response_metadata={},
        )
        second = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": " to inspect\n下一步"},
            response_metadata={},
        )

        self.assertEqual(extract_text_and_reasoning(first)[1], "I need")
        self.assertEqual(extract_text_and_reasoning(second)[1], " to inspect\n下一步")

    def test_provider_thought_signature_is_not_visible_reasoning(self):
        message = SimpleNamespace(
            content=[{"text": "Visible answer", "thoughtSignature": "opaque-provider-signature"}],
            additional_kwargs={},
            response_metadata={"thoughtSignature": "another-opaque-signature"},
        )

        text, reasoning = extract_text_and_reasoning(message)

        self.assertEqual(text, "Visible answer")
        self.assertEqual(reasoning, "")

    def test_provider_id_is_preserved_as_shadow_while_v8_binds_canonical_id(self):
        message = SimpleNamespace(
            content="",
            tool_calls=[
                {
                    "name": "web_broker",
                    "args": {"mode": "search", "target": "V8 Agent OS"},
                    "id": "call_provider_123",
                }
            ],
            additional_kwargs={},
            response_metadata={},
        )

        normalized = sanitize_model_tool_calls(message, provider_standard="openai")
        tool_call = normalized.tool_calls[0]

        self.assertTrue(str(tool_call["id"]).startswith("call_v8_"))
        self.assertEqual(tool_call["providerToolCallId"], "call_provider_123")
        self.assertEqual(tool_call["providerStandard"], "openai")
        self.assertEqual(tool_call["ordinal"], 0)

        normalized_again = sanitize_model_tool_calls(normalized, provider_standard="openai")
        self.assertEqual(normalized_again.tool_calls[0]["id"], tool_call["id"])
        self.assertEqual(normalized_again.tool_calls[0]["providerToolCallId"], "call_provider_123")

    def test_missing_provider_id_still_gets_stable_message_scoped_canonical_id(self):
        message = SimpleNamespace(
            content="",
            tool_calls=[
                {
                    "name": "command_session_broker",
                    "args": {"mode": "start", "command": "npm run dev"},
                }
            ],
            additional_kwargs={},
            response_metadata={},
        )

        normalized = sanitize_model_tool_calls(message, provider_standard="gemini")
        tool_call = normalized.tool_calls[0]

        self.assertTrue(str(tool_call["id"]).startswith("call_v8_"))
        self.assertEqual(tool_call["providerStandard"], "gemini")
        self.assertNotIn("providerToolCallId", tool_call)

        normalized_again = sanitize_model_tool_calls(normalized, provider_standard="gemini")
        self.assertEqual(normalized_again.tool_calls[0]["id"], tool_call["id"])

    def test_gemini_function_call_content_block_is_normalized(self):
        message = SimpleNamespace(
            content=[
                {
                    "functionCall": {
                        "id": "gemini_call_1",
                        "name": "web_broker",
                        "args": {"mode": "search", "target": "V8 Agent OS"},
                    }
                }
            ],
            additional_kwargs={},
            response_metadata={"providerStandard": "gemini"},
        )

        normalized = sanitize_model_tool_calls(message)
        tool_call = normalized.tool_calls[0]

        self.assertTrue(str(tool_call["id"]).startswith("call_v8_"))
        self.assertEqual(tool_call["providerToolCallId"], "gemini_call_1")
        self.assertEqual(tool_call["providerStandard"], "gemini")
        self.assertEqual(tool_call["name"], "web_broker")
        self.assertEqual(tool_call["args"], {"mode": "search", "target": "V8 Agent OS"})

    def test_gemini_function_call_without_provider_id_is_message_scoped(self):
        message = SimpleNamespace(
            content=[
                {
                    "function_call": {
                        "name": "command_session_broker",
                        "args": {"mode": "start", "command": "npm run dev"},
                    }
                }
            ],
            additional_kwargs={},
            response_metadata={"providerStandard": "google"},
        )

        normalized = sanitize_model_tool_calls(message)
        tool_call = normalized.tool_calls[0]

        self.assertTrue(str(tool_call["id"]).startswith("call_v8_"))
        self.assertEqual(tool_call["providerStandard"], "gemini")
        self.assertNotIn("providerToolCallId", tool_call)

        normalized_again = sanitize_model_tool_calls(normalized)
        self.assertEqual(normalized_again.tool_calls[0]["id"], tool_call["id"])

    def test_native_write_and_shell_consumer_are_split_across_turns(self):
        message = SimpleNamespace(
            content=[
                {"type": "text", "text": "I will write, then verify."},
                {
                    "type": "tool_use",
                    "id": "provider-write-1",
                    "name": "write_native_file",
                    "input": {"path": "app.py", "content": "print('ok')"},
                },
                {
                    "type": "tool_use",
                    "id": "provider-run-1",
                    "name": "run_system_command",
                    "input": {"command": "python app.py"},
                },
            ],
            tool_calls=[
                {
                    "id": "provider-write-1",
                    "name": "write_native_file",
                    "args": {"path": "app.py", "content": "print('ok')"},
                },
                {
                    "id": "provider-run-1",
                    "name": "run_system_command",
                    "args": {"command": "python app.py"},
                },
            ],
            additional_kwargs={},
            response_metadata={"providerStandard": "anthropic"},
        )

        normalized = sanitize_response_tool_calls(message)

        self.assertEqual([call["name"] for call in normalized.tool_calls], ["write_native_file"])
        self.assertEqual(
            normalized.additional_kwargs["v8_deferred_dependent_tool_calls"][0]["name"],
            "run_system_command",
        )
        self.assertNotIn(
            "run_system_command",
            [
                block.get("name")
                for block in normalized.content
                if isinstance(block, dict)
            ],
        )

        normalized_again = sanitize_response_tool_calls(normalized)
        self.assertEqual([call["name"] for call in normalized_again.tool_calls], ["write_native_file"])

    def test_independent_native_writes_remain_parallel(self):
        message = SimpleNamespace(
            content="",
            tool_calls=[
                {"id": "write-a", "name": "write_native_file", "args": {"path": "a.txt", "content": "a"}},
                {"id": "write-b", "name": "write_native_file", "args": {"path": "b.txt", "content": "b"}},
            ],
            additional_kwargs={},
            response_metadata={},
        )

        normalized = sanitize_response_tool_calls(message)

        self.assertEqual(
            [call["name"] for call in normalized.tool_calls],
            ["write_native_file", "write_native_file"],
        )
        self.assertNotIn("v8_deferred_dependent_tool_calls", normalized.additional_kwargs)

    def test_exact_duplicate_side_effect_call_in_one_response_is_deduplicated(self):
        message = SimpleNamespace(
            content=[
                {"type": "tool_use", "id": "write-1", "name": "write_native_file", "input": {"path": "a.py", "content": "x"}},
                {"type": "tool_use", "id": "write-2", "name": "write_native_file", "input": {"path": "a.py", "content": "x"}},
            ],
            tool_calls=[
                {"id": "write-1", "name": "write_native_file", "args": {"path": "a.py", "content": "x"}},
                {"id": "write-2", "name": "write_native_file", "args": {"content": "x", "path": "a.py"}},
            ],
            additional_kwargs={
                "tool_calls": [
                    {"id": "write-1", "name": "write_native_file", "input": {"path": "a.py", "content": "x"}},
                    {"id": "write-2", "name": "write_native_file", "input": {"content": "x", "path": "a.py"}},
                ]
            },
            response_metadata={},
        )

        normalized = sanitize_response_tool_calls(message)

        self.assertEqual(len(normalized.tool_calls), 1)
        self.assertEqual(normalized.tool_calls[0]["providerToolCallId"], "write-1")
        self.assertEqual(normalized.additional_kwargs["v8_deduplicated_tool_calls"][0]["name"], "write_native_file")
        self.assertEqual(len(normalized.content), 1)

    def test_langchain_ai_message_read_only_content_blocks_does_not_crash(self):
        """The current LangChain projection must not abort a delegated run."""

        message = AIMessage(
            content=[
                {"type": "text", "text": "write then verify"},
                {
                    "type": "tool_use",
                    "id": "provider-write-1",
                    "name": "write_native_file",
                    "input": {"path": "app.py", "content": "print('ok')"},
                },
                {
                    "type": "tool_use",
                    "id": "provider-run-1",
                    "name": "run_system_command",
                    "input": {"command": "python app.py"},
                },
            ],
            tool_calls=[
                {
                    "id": "provider-write-1",
                    "name": "write_native_file",
                    "args": {"path": "app.py", "content": "print('ok')"},
                },
                {
                    "id": "provider-run-1",
                    "name": "run_system_command",
                    "args": {"command": "python app.py"},
                },
            ],
        )

        normalized = sanitize_response_tool_calls(message)

        self.assertEqual([call["name"] for call in normalized.tool_calls], ["write_native_file"])
        self.assertNotIn("run_system_command", str(normalized.content))
        self.assertFalse(
            any(
                block.get("name") == "run_system_command"
                for block in normalized.content_blocks
                if isinstance(block, dict)
            )
        )

    def test_duplicate_unknown_tool_is_not_silently_deduplicated(self):
        message = SimpleNamespace(
            content="",
            tool_calls=[
                {"id": "custom-1", "name": "custom_tool", "args": {"value": 1}},
                {"id": "custom-2", "name": "custom_tool", "args": {"value": 1}},
            ],
            additional_kwargs={},
            response_metadata={},
        )

        normalized = sanitize_response_tool_calls(message)

        self.assertEqual(len(normalized.tool_calls), 2)
        self.assertNotIn("v8_deduplicated_tool_calls", normalized.additional_kwargs)


if __name__ == "__main__":
    unittest.main()

