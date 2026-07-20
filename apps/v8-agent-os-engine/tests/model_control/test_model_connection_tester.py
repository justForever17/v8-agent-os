from __future__ import annotations

from types import SimpleNamespace

from core.model_connection_tester import ModelConnectionTester


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
