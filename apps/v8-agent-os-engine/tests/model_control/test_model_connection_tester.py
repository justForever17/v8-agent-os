from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.model_connection_tester import ModelConnectionTester
from core.observability_db import ObservabilityDatabaseManager


class _FakeChatClient:
    def invoke(self, _messages):
        return SimpleNamespace(content="OK", response_metadata={"finish_reason": "stop"})


def test_connection_probe_uses_small_output_cap(monkeypatch):
    tester = ModelConnectionTester()
    captured: dict[str, object] = {}

    def fake_create_chat_model(model_id, **kwargs):
        captured["model_id"] = model_id
        captured.update(kwargs)
        return _FakeChatClient()

    monkeypatch.setattr("core.model_connection_tester.llm_factory.create_chat_model", fake_create_chat_model)

    result = tester._test_chat_model(  # noqa: SLF001 - connection probe contract
        model_id="gemini::gemini-3-flash-preview",
        meta={"base_url": "https://cloudcode-pa.googleapis.com", "global_max_tokens": 65536},
    )

    assert result["message"] == "OK"
    assert captured["max_tokens"] == 16
    assert captured["temperature"] == 0
    assert captured["streaming"] is False


def test_streaming_probe_consumes_the_provider_stream_to_terminal(monkeypatch):
    tester = ModelConnectionTester()
    state = {"completed": False}

    class StreamingClient:
        def stream(self, _messages):
            yield AIMessage(content="O")
            yield AIMessage(content="K")
            state["completed"] = True

    monkeypatch.setattr(
        "core.model_connection_tester.llm_factory.create_chat_model",
        lambda *_args, **_kwargs: StreamingClient(),
    )

    result = tester._test_streaming_capability(
        model_id="provider::model",
        meta={"base_url": "https://example.test"},
    )

    assert result["message"] == "OK"
    assert state["completed"] is True


def test_tool_probe_verifies_the_tool_result_continuation(monkeypatch):
    tester = ModelConnectionTester()
    calls: list[tuple[object, list[object]]] = []

    class BoundClient:
        def __init__(self, tool_choice):
            self.tool_choice = tool_choice

        def invoke(self, messages):
            calls.append((self.tool_choice, messages))
            if self.tool_choice == "required":
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "echo_status",
                            "args": {"status": "ok"},
                        }
                    ],
                )
            assert isinstance(messages[0], HumanMessage)
            assert isinstance(messages[1], AIMessage)
            assert isinstance(messages[2], ToolMessage)
            assert messages[2].tool_call_id == "call-1"
            assert messages[2].content == "ok"
            return AIMessage(content="done")

    class ToolClient:
        def bind_tools(self, _tools, tool_choice=None):
            return BoundClient(tool_choice)

    monkeypatch.setattr(
        "core.model_connection_tester.llm_factory.create_chat_model",
        lambda *_args, **_kwargs: ToolClient(),
    )

    result = tester._test_tool_calling_capability(
        model_id="provider::model",
        meta={"base_url": "https://example.test"},
    )

    assert result["continuationVerified"] is True
    assert result["message"] == "tool continuation ok · echo_status"
    assert [item[0] for item in calls] == ["required", None]


def test_anthropic_probe_endpoint_exposes_misconfigured_versioned_base_url():
    tester = ModelConnectionTester()

    assert tester._build_anthropic_messages_endpoint(base_url="https://provider.example.test") == (
        "https://provider.example.test/v1/messages"
    )
    assert tester._build_anthropic_messages_endpoint(base_url="https://provider.example.test/v1") == (
        "https://provider.example.test/v1/v1/messages"
    )
    assert tester._build_anthropic_messages_endpoint(base_url="https://provider.example.test/anthropic") == (
        "https://provider.example.test/anthropic/v1/messages"
    )


def test_oauth_provider_connection_skips_deep_capability_suite(monkeypatch):
    tester = ModelConnectionTester()
    meta = {
        "model_id": "gpt-5.5",
        "model_ref": "codex::gpt-5.5",
        "provider_id": "codex",
        "provider_name": "Codex OAuth",
        "base_url": "https://chatgpt.com/backend-api",
        "provider_adapter": "codex-oauth-runtime",
        "runtime_ready": True,
        "effective_capability_matrix": {
            "supports_streaming": True,
            "supports_prompt_emulated_tools": True,
            "supports_prompt_fallback_structured_output": True,
        },
        "provider_record": {
            "id": "codex",
            "type": "PLATFORM",
            "oauth_preset": "codex",
        },
        "model_record": {"type": "TEXT"},
    }

    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: {
            "latencyMs": 12.3,
            "message": "OK",
            "requestKind": "chat_completion",
            "resolvedEndpoint": "https://chatgpt.com/backend-api",
        },
    )
    monkeypatch.setattr(
        tester,
        "_run_capability_checks",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("deep checks should be skipped")),
    )
    monkeypatch.setattr(tester, "_record_health", lambda **_kwargs: None)

    result = tester.test_model_connection(model_id="gpt-5.5", provider_id="codex")

    assert result["ok"] is True
    assert result["modelRef"] == "codex::gpt-5.5"
    assert result["capabilityChecks"]["toolCalling"]["status"] == "skipped"
    assert result["capabilityChecks"]["toolCalling"]["reason"] == "basic_connection_probe_only_for_oauth_quota_safety"
    assert result["degradeApplied"] is True


def test_media_generation_connection_uses_provider_probe(monkeypatch):
    tester = ModelConnectionTester()
    meta = {
        "model_id": "comfyui-workflow",
        "model_ref": "comfyui::comfyui-workflow",
        "provider_id": "comfyui",
        "provider_name": "ComfyUI",
        "base_url": "http://127.0.0.1:8188",
        "api_standard": "comfyui",
        "capability_class": "media_generation",
        "runtime_ready": True,
        "effective_capability_matrix": {},
        "provider_record": {
            "id": "comfyui",
            "providerKind": "media_generation",
            "apiStandard": "comfyui",
        },
        "model_record": {"type": "MEDIA"},
    }

    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_media_generation_provider",
        lambda **_kwargs: {
            "latencyMs": 8.5,
            "message": "ComfyUI 节点 10 个，checkpoint 2 个",
            "requestKind": "media_generation_probe",
            "resolvedEndpoint": "http://127.0.0.1:8188/object_info",
        },
    )
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("media provider should not use chat probe")),
    )
    monkeypatch.setattr(tester, "_record_health", lambda **_kwargs: None)

    result = tester.test_model_connection(model_id="comfyui-workflow", provider_id="comfyui")

    assert result["ok"] is True
    assert result["requestKind"] == "media_generation_probe"
    assert result["capabilityChecks"]["streaming"]["status"] == "skipped"
    assert result["capabilityChecks"]["streaming"]["reason"] == "media_generation_provider_probe_only"


def test_openai_provider_gemini_model_does_not_probe_an_unconfigured_native_route(monkeypatch):
    tester = ModelConnectionTester()
    meta = {
        "model_id": "gemini-3.5-flash-low",
        "model_ref": "cliproxy::gemini-3.5-flash-low",
        "provider_id": "cliproxy",
        "provider_name": "CLI Proxy",
        "base_url": "http://127.0.0.1:8731/v1",
        "api_key": "sk-test",
        "api_standard": "openai",
        "runtime_ready": True,
        "effective_capability_matrix": {},
        "provider_record": {"id": "cliproxy", "type": "API", "apiStandard": "openai"},
        "model_record": {"type": "TEXT"},
    }

    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: {
            "latencyMs": 10.0,
            "message": "OK",
            "requestKind": "chat_completion",
            "resolvedEndpoint": "http://127.0.0.1:8731/v1",
        },
    )
    monkeypatch.setattr(tester, "_run_capability_checks", lambda **_kwargs: {})
    monkeypatch.setattr(tester, "_record_health", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_probe_gemini_native_generate_content",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unconfigured channel must not be probed")),
    )

    result = tester.test_model_connection(model_id="gemini-3.5-flash-low", provider_id="cliproxy")

    assert result["ok"] is True
    assert "protocolWarning" not in result
    assert result.get("nativeGeminiProbe") is None


def test_openai_failure_does_not_retry_a_guessed_gemini_route(monkeypatch):
    tester = ModelConnectionTester()
    meta = {
        "model_id": "gemini-3.5-flash-low",
        "model_ref": "cliproxy::gemini-3.5-flash-low",
        "provider_id": "cliproxy",
        "provider_name": "CLI Proxy",
        "base_url": "http://127.0.0.1:8731/v1",
        "api_key": "sk-test",
        "api_standard": "openai",
        "runtime_ready": True,
        "effective_capability_matrix": {},
        "provider_record": {"id": "cliproxy", "type": "API", "apiStandard": "openai"},
        "model_record": {"type": "TEXT"},
    }

    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("404 not found: /v1/chat/completions")),
    )
    monkeypatch.setattr(tester, "_record_health", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_probe_gemini_native_generate_content",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unconfigured channel must not be probed")),
    )

    result = tester.test_model_connection(model_id="gemini-3.5-flash-low", provider_id="cliproxy")

    assert result["ok"] is False
    assert result["error"]["code"] != "protocol_mismatch"
    assert result["nativeGeminiProbe"] is None
    assert result["recommendedBaseUrl"] is None


def test_openai_provider_claude_model_does_not_probe_an_unconfigured_anthropic_route(monkeypatch):
    tester = ModelConnectionTester()
    meta = {
        "model_id": "claude-sonnet-4-5",
        "model_ref": "cliproxy::claude-sonnet-4-5",
        "provider_id": "cliproxy",
        "provider_name": "CLI Proxy",
        "base_url": "http://127.0.0.1:8731/v1",
        "api_key": "sk-test",
        "api_standard": "openai",
        "runtime_ready": True,
        "effective_capability_matrix": {},
        "provider_record": {"id": "cliproxy", "type": "API", "apiStandard": "openai"},
        "model_record": {"type": "TEXT"},
    }

    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: {
            "latencyMs": 10.0,
            "message": "OK",
            "requestKind": "chat_completion",
            "resolvedEndpoint": "http://127.0.0.1:8731/v1",
        },
    )
    monkeypatch.setattr(tester, "_run_capability_checks", lambda **_kwargs: {})
    monkeypatch.setattr(tester, "_record_health", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_probe_anthropic_native_messages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unconfigured channel must not be probed")),
    )

    result = tester.test_model_connection(model_id="claude-sonnet-4-5", provider_id="cliproxy")

    assert result["ok"] is True
    assert "protocolWarning" not in result
    assert result.get("nativeAnthropicProbe") is None


def test_openai_failure_does_not_retry_a_guessed_anthropic_route(monkeypatch):
    tester = ModelConnectionTester()
    meta = {
        "model_id": "claude-sonnet-4-5",
        "model_ref": "cliproxy::claude-sonnet-4-5",
        "provider_id": "cliproxy",
        "provider_name": "CLI Proxy",
        "base_url": "http://127.0.0.1:8731/v1",
        "api_key": "sk-test",
        "api_standard": "openai",
        "runtime_ready": True,
        "effective_capability_matrix": {},
        "provider_record": {"id": "cliproxy", "type": "API", "apiStandard": "openai"},
        "model_record": {"type": "TEXT"},
    }

    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("404 not found: /v1/chat/completions")),
    )
    monkeypatch.setattr(tester, "_record_health", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_probe_anthropic_native_messages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unconfigured channel must not be probed")),
    )

    result = tester.test_model_connection(model_id="claude-sonnet-4-5", provider_id="cliproxy")

    assert result["ok"] is False
    assert result["error"]["code"] != "protocol_mismatch"
    assert result["nativeAnthropicProbe"] is None
    assert result["recommendedApiStandard"] is None


@pytest.mark.parametrize("leak_surface", ["raw", "quote", "quote_plus", "header", "query"])
def test_provider_error_never_exposes_supplied_key_in_response_or_health_db(
    monkeypatch,
    tmp_path,
    leak_surface,
):
    tester = ModelConnectionTester()
    api_key = "opaque/key+with space=value"
    encoded = quote(api_key)
    fully_encoded = quote(api_key, safe="")
    plus_encoded = quote_plus(api_key)
    leaked_value = {
        "raw": api_key,
        "quote": encoded,
        "quote_plus": plus_encoded,
        "header": f"Authorization: Bearer {api_key}",
        "query": f"https://provider.example.test/v1?api_key={plus_encoded}",
    }[leak_surface]
    meta = {
        "model_id": "model-a",
        "model_ref": "provider::model-a",
        "provider_id": "provider",
        "provider_name": "Provider",
        "base_url": "https://provider.example.test/v1",
        "api_key": api_key,
        "api_standard": "openai",
        "runtime_ready": True,
        "effective_capability_matrix": {},
        "provider_record": {"id": "provider", "type": "API", "apiKey": api_key},
        "model_record": {"type": "TEXT"},
    }
    health_db = ObservabilityDatabaseManager(tmp_path / "observability.db")

    monkeypatch.setattr("core.model_connection_tester.db", health_db)
    monkeypatch.setattr(tester, "_resolve_metadata", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(tester, "_probe_local_capability", lambda **_kwargs: None)
    monkeypatch.setattr(
        tester,
        "_test_chat_model",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(f"401 unauthorized: {leaked_value}")),
    )

    result = tester.test_model_connection(model_id="model-a", provider_id="provider")

    assert result["ok"] is False
    assert result["error"]["code"] == "auth_error"
    assert result["error"]["message"] == "Provider authentication failed."
    response_payload = json.dumps(result, ensure_ascii=False)
    with health_db.get_connection() as conn:
        health_row = conn.execute(
            "SELECT error_code, error_message, detail_json FROM provider_health_logs"
        ).fetchone()
    assert health_row is not None
    assert health_row["error_code"] == "auth_error"
    assert health_row["error_message"] == "Provider authentication failed."
    persisted_payload = json.dumps(dict(health_row), ensure_ascii=False)
    for secret_variant in {api_key, encoded, fully_encoded, plus_encoded}:
        assert secret_variant not in response_payload
        assert secret_variant not in persisted_payload
