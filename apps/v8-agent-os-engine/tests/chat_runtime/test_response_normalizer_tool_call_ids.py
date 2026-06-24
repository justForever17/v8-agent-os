from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


from core.response_normalizer import extract_text_and_reasoning, sanitize_model_tool_calls


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


if __name__ == "__main__":
    unittest.main()

