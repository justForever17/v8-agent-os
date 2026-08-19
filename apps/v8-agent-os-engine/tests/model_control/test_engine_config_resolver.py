from __future__ import annotations

import inspect

import pytest

from api.models import ChatMessage, ChatRequest, ChatRequestData, EngineConfig
from core.engine_config_resolver import (
    require_engine_config,
    resolve_engine_config_for_model_ref,
    resolve_engine_config_for_role,
)
from core.llm_exceptions import V8LLMInvalidRequestError
from runtimes.chat.runtime import ChatRuntime


def _routes() -> dict:
    return {
        "providers": {
            "minimax-cn": {
                "provider": {
                    "base_url": "https://api.minimaxi.com/v1",
                }
            }
        }
    }


def _engine_config() -> EngineConfig:
    return EngineConfig(
        provider="minimax-cn",
        model_name="MiniMax-M3",
        api_key="secret",
        base_url="https://api.minimaxi.com/v1",
    )


def test_engine_config_has_no_implicit_provider_or_model() -> None:
    config = EngineConfig()

    assert config.provider == ""
    assert config.model_name == ""


def test_chat_runtime_sources_do_not_restore_an_unconfigured_gpt4o_default() -> None:
    from api import models as api_models
    from core import engine_config_resolver

    guarded_sources = "\n".join(
        [
            inspect.getsource(api_models.EngineConfig),
            inspect.getsource(engine_config_resolver),
            inspect.getsource(ChatRuntime._resolve_engine_config),
        ]
    ).lower()

    assert "gpt-4o" not in guarded_sources
    assert "fallback_provider" not in guarded_sources
    assert "fallback_model" not in guarded_sources


def test_role_resolution_uses_model_hub_inherited_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.engine_config_resolver.storage.get_routes", _routes)
    monkeypatch.setattr(
        "core.engine_config_resolver.model_control_plane.resolve_model_for_role",
        lambda role: {
            "role": role,
            "bindingState": "default",
            "resolvedModelId": "MiniMax-M3",
            "resolvedModelRef": "minimax-cn::MiniMax-M3",
            "resolvedProviderId": "minimax-cn",
        },
    )
    monkeypatch.setattr(
        "core.engine_config_resolver._hydrate_provider_credentials",
        lambda provider, _config: ("secret", "https://api.minimaxi.com/v1"),
    )

    resolved = resolve_engine_config_for_role("supervisor")

    assert require_engine_config(resolved, role="supervisor") == _engine_config()


def test_missing_role_binding_does_not_invent_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.engine_config_resolver.storage.get_routes", lambda: {"providers": {}})
    monkeypatch.setattr(
        "core.engine_config_resolver.model_control_plane.resolve_model_for_role",
        lambda role: {
            "role": role,
            "bindingState": "unbound",
            "resolvedModelId": "",
            "resolvedModelRef": "",
            "resolvedProviderId": "",
        },
    )

    resolved = resolve_engine_config_for_role("supervisor")

    assert resolved["engine_config"] is None
    with pytest.raises(V8LLMInvalidRequestError) as blocked:
        require_engine_config(resolved, role="supervisor")
    assert blocked.value.code == "model_not_configured"
    assert blocked.value.details["role"] == "supervisor"


def test_missing_explicit_model_ref_does_not_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.engine_config_resolver.storage.get_routes", lambda: {"providers": {}})
    monkeypatch.setattr(
        "core.engine_config_resolver.model_control_plane.get_model_record",
        lambda *_args, **_kwargs: None,
    )

    resolved = resolve_engine_config_for_model_ref("unconfigured-provider::missing-model")

    assert resolved["engine_config"] is None
    with pytest.raises(V8LLMInvalidRequestError) as blocked:
        require_engine_config(resolved, model_ref="unconfigured-provider::missing-model")
    assert blocked.value.code == "model_not_configured"
    assert blocked.value.model == "unconfigured-provider::missing-model"


def test_chat_request_resolves_the_model_hub_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "runtimes.chat.runtime.resolve_engine_config_for_role",
        lambda role: {
            "engine_config": _engine_config(),
            "resolution": {
                "role": role,
                "bindingState": "default",
                "resolvedModelRef": "minimax-cn::MiniMax-M3",
            },
        },
    )
    request = ChatRequest(messages=[ChatMessage(role="user", content="hello")])

    ChatRuntime._resolve_engine_config(None, request)

    assert request.config.provider == "minimax-cn"
    assert request.config.model_name == "MiniMax-M3"
    assert request.config.base_url == "https://api.minimaxi.com/v1"


def test_chat_request_rejects_unknown_explicit_model_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "runtimes.chat.runtime.resolve_engine_config_for_model_ref",
        lambda model_ref, **_kwargs: {
            "engine_config": None,
            "resolution": {
                "role": "request_override",
                "bindingState": "missing",
                "rawModelId": model_ref,
                "lookupStatus": "missing",
            },
        },
    )
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        data=ChatRequestData(modelProfile="unconfigured-provider::missing-model"),
    )

    with pytest.raises(V8LLMInvalidRequestError) as blocked:
        ChatRuntime._resolve_engine_config(None, request)

    assert blocked.value.code == "model_not_configured"
    assert blocked.value.model == "unconfigured-provider::missing-model"
