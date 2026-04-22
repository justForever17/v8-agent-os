from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


from runtimes.network_supervisor.openai_compat import (  # noqa: E402
    build_engine_chat_request_from_openai,
    build_openai_completion_response,
    normalize_openai_messages_to_chat_messages,
    select_external_tools_for_request,
)
from api.network_supervisor_routes import _resolve_openai_scope_headers  # noqa: E402
from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig  # noqa: E402
from runtimes.network_supervisor.service import network_supervisor_service  # noqa: E402


class NetworkSupervisorOpenAICompatTests(unittest.TestCase):
    def _enabled_network_config(self) -> NetworkSupervisorRuntimeConfig:
        config = NetworkSupervisorRuntimeConfig()
        config.enabled = True
        config.openai_compat.enabled = True
        return config

    def test_verify_openai_compat_token_accepts_legacy_strings_and_managed_objects(self):
        config = self._enabled_network_config()
        secrets_payload = {
            "openaiCompatTokens": [
                "legacy-token",
                {
                    "id": "oct_test",
                    "label": "Fixture",
                    "token": "managed-token",
                    "fingerprint": "fixture",
                    "createdAt": "2026-04-22T00:00:00Z",
                },
            ]
        }

        with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
            network_supervisor_service,
            "read_secrets",
            return_value=secrets_payload,
        ):
            network_supervisor_service.verify_openai_compat_token("legacy-token")
            network_supervisor_service.verify_openai_compat_token("managed-token")
            with self.assertRaises(HTTPException) as raised:
                network_supervisor_service.verify_openai_compat_token("wrong-token")
            self.assertEqual(raised.exception.status_code, 401)

    def test_list_openai_compat_tokens_returns_plaintext_for_admin_control_plane(self):
        secrets_payload = {
            "openaiCompatTokens": [
                "legacy-token",
                {"id": "oct_test", "label": "Fixture", "token": "managed-token"},
            ]
        }

        with patch.object(network_supervisor_service, "read_secrets", return_value=secrets_payload):
            payload = network_supervisor_service.list_openai_compat_tokens()

        items = payload["items"]
        self.assertEqual([item["token"] for item in items], ["legacy-token", "managed-token"])
        self.assertTrue(items[0]["id"].startswith("legacy_"))
        self.assertEqual(items[1]["id"], "oct_test")

    def test_scope_headers_reject_raw_workspace_path_and_respect_workspace_header_gate(self):
        config = self._enabled_network_config()
        with patch.object(network_supervisor_service, "get_config_model", return_value=config):
            with self.assertRaises(HTTPException) as raised:
                _resolve_openai_scope_headers(SimpleNamespace(headers={"x-v8-workspace-path": "C:\\unsafe"}))
            self.assertEqual(raised.exception.status_code, 400)

        config = self._enabled_network_config()
        config.openai_compat.allow_workspace_headers = False
        with patch.object(network_supervisor_service, "get_config_model", return_value=config):
            with self.assertRaises(HTTPException) as raised:
                _resolve_openai_scope_headers(SimpleNamespace(headers={"x-v8-project-id": "project-1"}))
            self.assertEqual(raised.exception.status_code, 403)

    def test_select_external_tools_respects_tool_choice_none_and_specific_function(self):
        raw_tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_docs",
                    "description": "Search docs",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_note",
                    "description": "Save note",
                    "parameters": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                    },
                },
            },
        ]

        self.assertEqual(select_external_tools_for_request(raw_tools, tool_choice="none"), [])

        selected = select_external_tools_for_request(
            raw_tools,
            tool_choice={"type": "function", "function": {"name": "save_note"}},
        )

        self.assertEqual([item.function.name for item in selected], ["save_note"])
        self.assertEqual(selected[0].function.internal_alias_name, "network_save_note")

    def test_normalize_openai_messages_maps_external_tool_calls_and_tool_messages(self):
        external_tools = select_external_tools_for_request(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "search_docs",
                        "description": "Search docs",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ]
        )

        normalized = normalize_openai_messages_to_chat_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_wire_1",
                            "type": "function",
                            "function": {
                                "name": "search_docs",
                                "arguments": "{\"query\":\"v8 agent os\"}",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_wire_1",
                    "name": "search_docs",
                    "content": "found",
                },
            ],
            external_tools=external_tools,
        )

        self.assertEqual(normalized[0].role, "assistant")
        self.assertEqual(normalized[0].tool_calls[0].function.name, "network_search_docs")
        self.assertEqual(normalized[1].role, "tool")
        self.assertEqual(normalized[1].name, "network_search_docs")

    def test_build_engine_chat_request_from_openai_requires_messages_and_keeps_scope_headers(self):
        with self.assertRaisesRegex(ValueError, "at least one valid message"):
            build_engine_chat_request_from_openai({})

        request = build_engine_chat_request_from_openai(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "请查文档"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_docs",
                            "description": "Search docs",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        },
                    }
                ],
            },
            project_id="project-1",
            workspace_id="workspace-1",
            scope_hint="docs",
            scope_mode="explicit",
        )

        self.assertEqual(request.config.model_name, "gpt-4o-mini")
        self.assertEqual(request.project_id, "project-1")
        self.assertEqual(request.workspace_id, "workspace-1")
        self.assertEqual(request.scope_hint, "docs")
        self.assertEqual(request.config.external_tools[0].function.internal_alias_name, "network_search_docs")

    def test_build_openai_completion_response_only_exposes_external_tool_calls(self):
        external_tools = select_external_tools_for_request(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "search_docs",
                        "description": "Search docs",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ]
        )

        response = build_openai_completion_response(
            response_id="chatcmpl_test",
            model_name="gpt-4o",
            events=[
                {
                    "type": "tool_start",
                    "tool": {
                        "toolName": "network_search_docs",
                        "toolCallId": "v8_tool_call_1",
                        "args": {"query": "memory runtime"},
                    },
                },
                {
                    "type": "tool_start",
                    "tool": {
                        "toolName": "internal_private_tool",
                        "toolCallId": "v8_tool_call_private",
                        "args": {"secret": True},
                    },
                },
            ],
            external_tools=external_tools,
        )

        message = response["choices"][0]["message"]
        tool_calls = list(message.get("tool_calls") or [])
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "search_docs")
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")


if __name__ == "__main__":
    unittest.main()
