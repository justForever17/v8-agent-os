from __future__ import annotations

import pytest

from core.llm_exceptions import (
    V8LLMCapabilityMismatchError,
    V8LLMInvalidRequestError,
    V8LLMProviderUnavailableError,
    raise_as_v8_llm_error,
)
from core.llm_factory import LLMFactory, OpenAICompatibleEmbedding, RestReranker
from core.model_control_plane import model_control_plane
from core.model_endpoint_binding import build_model_endpoint_binding
from core.model_ref import make_model_ref
from core.provider_compatibility import normalize_provider_error


def test_endpoint_binding_preserves_selected_channel_auth_contract() -> None:
    binding = build_model_endpoint_binding(
        "provider",
        "model-a",
        {
            "base_url": "https://api.example.test/v1",
            "api_standard": "openai",
            "authContract": {
                "type": "api_key",
                "header": "Authorization",
                "scheme": "Bearer",
            },
            "channels": [
                {
                    "id": "header-key",
                    "baseUrl": "https://api.example.test/v1",
                    "apiStandard": "openai",
                    "wireProtocols": ["openai.chat_completions"],
                    "authContract": {"type": "api_key", "header": "x-api-key"},
                }
            ],
            "defaultChannelId": "header-key",
        },
        {
            "type": "TEXT",
            "endpointBinding": {
                "route": "model-a",
                "channelId": "header-key",
                "wireProtocol": "openai.chat_completions",
            },
        },
    )

    assert binding["authContract"] == {"type": "api_key", "header": "x-api-key"}


def test_openai_runtime_applies_custom_header_without_reusing_bearer_slot() -> None:
    kwargs = LLMFactory._build_openai_kwargs(
        "model-a",
        {
            "api_key": "secret-value",
            "auth_contract": {"type": "api_key", "header": "x-api-key"},
        },
    )

    assert kwargs["api_key"] == "sk-dummy"
    assert kwargs["default_headers"] == {"x-api-key": "secret-value"}
    assert "default_query" not in kwargs


def test_openai_runtime_applies_query_auth_without_authorization_secret() -> None:
    kwargs = LLMFactory._build_openai_kwargs(
        "model-a",
        {
            "api_key": "secret-value",
            "auth_contract": {"type": "api_key", "query": "key"},
        },
    )

    assert kwargs["api_key"] == "sk-dummy"
    assert kwargs["default_query"] == {"key": "secret-value"}
    assert "default_headers" not in kwargs


@pytest.mark.parametrize("query_name", ["key", "customCredential"])
def test_provider_error_redacts_arbitrary_query_auth_parameter(query_name: str) -> None:
    secret = "audit-sentinel-secret"
    normalized = normalize_provider_error(
        TimeoutError(f"request timed out: https://api.example.test/v1?{query_name}={secret}&model=model-a")
    )

    assert normalized["code"] == "timeout"
    assert normalized["message"] == "Provider request timed out."
    assert secret not in str(normalized)


def test_llm_error_preserves_safe_provider_diagnostics() -> None:
    class ProviderFailure(RuntimeError):
        status_code = 503
        body = {"error": {"type": "upstream_unavailable", "message": "do not persist this body"}}

    with pytest.raises(V8LLMProviderUnavailableError) as failure:
        raise_as_v8_llm_error(
            ProviderFailure("503 service unavailable"),
            provider="Provider",
            model="provider::model",
            details={"mode": "invoke"},
        )

    assert failure.value.details == {
        "mode": "invoke",
        "providerDiagnostic": {
            "exceptionType": "ProviderFailure",
            "statusCode": 503,
            "providerCode": "upstream_unavailable",
        },
    }
    assert "do not persist this body" not in str(failure.value.details)


def test_anthropic_runtime_applies_custom_scheme_header() -> None:
    kwargs = LLMFactory._build_anthropic_kwargs(
        "model-a",
        {
            "api_key": "secret-value",
            "auth_contract": {
                "type": "api_key",
                "header": "Authorization",
                "scheme": "Token",
            },
        },
    )

    assert kwargs["api_key"] == "sk-dummy"
    assert kwargs["default_headers"] == {"Authorization": "Token secret-value"}


def test_auxiliary_clients_share_the_same_auth_transport() -> None:
    embedding = OpenAICompatibleEmbedding(
        model_name="embed-a",
        api_key="secret-value",
        base_url="https://api.example.test/v1",
        max_tokens=8192,
        auth_contract={"type": "api_key", "query": "api_key"},
    )
    reranker = RestReranker(
        model_name="rerank-a",
        api_key="secret-value",
        base_url="https://api.example.test/v1",
        max_tokens=8192,
        auth_contract={"type": "api_key", "header": "x-api-key"},
    )

    assert embedding.auth_headers == {}
    assert embedding.auth_query == {"api_key": "secret-value"}
    assert reranker.auth_headers == {"x-api-key": "secret-value"}
    assert reranker.auth_query == {}


def test_disabled_explicit_role_model_falls_back_to_enabled_default() -> None:
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "provider": {
                    "provider": {
                        "name": "Provider",
                        "base_url": "https://api.example.test/v1",
                    },
                    "models": {
                        "disabled": {
                            "type": "TEXT",
                            "isEnabled": False,
                            "capabilities": {"chat": True},
                        },
                        "enabled": {
                            "type": "TEXT",
                            "isEnabled": True,
                            "capabilities": {"chat": True},
                        },
                    },
                }
            },
            "roles": {
                "default": make_model_ref("provider", "enabled"),
                "supervisor": make_model_ref("provider", "disabled"),
            },
        }
    )

    resolution = model_control_plane.resolve_model_for_role("supervisor", config)

    assert resolution["bindingState"] == "inherited_default"
    assert resolution["resolvedModelRef"] == "provider::enabled"


def test_direct_chat_factory_installs_provider_compatibility_before_resolution(monkeypatch) -> None:
    import core.llm_factory as module

    calls: list[str] = []

    def _install() -> None:
        calls.append("installed")

    def _stop_resolution(cls, _model_id: str) -> dict:
        assert calls == ["installed"]
        raise RuntimeError("resolution reached")

    monkeypatch.setattr(module, "install_provider_compatibility_patches", _install)
    monkeypatch.setattr(LLMFactory, "_resolve_model_metadata", classmethod(_stop_resolution))

    with pytest.raises(RuntimeError, match="resolution reached"):
        LLMFactory.create_chat_model("provider::model-a")


@pytest.mark.parametrize("lookup_status", ["missing", "ambiguous"])
def test_direct_chat_factory_rejects_models_outside_model_hub(monkeypatch, lookup_status) -> None:
    monkeypatch.setattr(
        LLMFactory,
        "_resolve_model_metadata",
        classmethod(
            lambda cls, _model_id: {
                "is_found": False,
                "lookup_status": lookup_status,
            }
        ),
    )

    with pytest.raises(V8LLMInvalidRequestError) as blocked:
        LLMFactory.create_chat_model("unconfigured-model")

    assert blocked.value.code == "model_not_configured"
    assert blocked.value.model == "unconfigured-model"
    assert blocked.value.provider == "modelhub"


@pytest.mark.parametrize(
    "metadata,code",
    [
        ({"provider_enabled": False, "model_enabled": True}, "provider_disabled"),
        ({"provider_enabled": True, "model_enabled": False}, "model_disabled"),
    ],
)
def test_direct_runtime_creation_rejects_disabled_records(monkeypatch, metadata, code) -> None:
    monkeypatch.setattr(
        LLMFactory,
        "_resolve_model_metadata",
        classmethod(
            lambda cls, _model_id: {
                "is_found": True,
                "provider_id": "provider",
                "provider_name": "Provider",
                "model_id": "model-a",
                "runtime_ready": True,
                **metadata,
            }
        ),
    )

    with pytest.raises(V8LLMCapabilityMismatchError) as blocked:
        LLMFactory.create_chat_model("provider::model-a")

    assert blocked.value.code == code
