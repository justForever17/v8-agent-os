from __future__ import annotations

import pytest

from core.llm_factory import LLMFactory
from core.model_control_plane import model_control_plane
from core.model_thinking_control import provider_reasoning_transport_patch, reasoning_summary_request_patch


def _openai_responses_meta() -> dict:
    return {
        "wire_protocol": "openai.responses",
        "reasoning_surface": {
            "mode": "reasoning_summary",
            "requestStyle": "openai_reasoning",
            "responseFields": ["reasoning.summary"],
        },
        "model_record": {"capabilities": ["reasoning"]},
    }


def test_supported_responses_model_requests_auto_summary_and_encrypted_replay():
    patch = reasoning_summary_request_patch(_openai_responses_meta())
    assert patch == {
        "reasoning": {"summary": "auto"},
        "include": ["reasoning.encrypted_content"],
    }


def test_chat_completions_and_unverified_surfaces_are_not_sent_responses_fields():
    chat_meta = _openai_responses_meta()
    chat_meta["wire_protocol"] = "openai.chat_completions"
    assert reasoning_summary_request_patch(chat_meta) == {}

    unknown_meta = _openai_responses_meta()
    unknown_meta["reasoning_surface"]["trust"] = "catalog_only"
    unknown_meta["reasoning_surface"]["responseFields"] = ["reasoning"]
    assert reasoning_summary_request_patch(unknown_meta) == {}


def test_llm_factory_applies_summary_patch_to_openai_responses_binding():
    kwargs = LLMFactory._build_openai_kwargs(
        "gpt-test",
        {
            **_openai_responses_meta(),
            "provider_id": "openai",
            "api_standard": "openai",
        },
    )
    assert kwargs["reasoning"]["summary"] == "auto"
    assert "reasoning.encrypted_content" in kwargs["include"]


def test_minimax_m3_uses_documented_split_reasoning_transport():
    metadata = {
        "wire_protocol": "openai.chat_completions",
        "reasoning_surface": {
            "mode": "provider_reasoning",
            "trust": "official",
            "requestStyle": "minimax_interleaved_thinking",
            "responseFields": ["reasoning_details"],
        },
    }

    assert provider_reasoning_transport_patch(metadata) == {
        "extra_body": {"reasoning_split": True}
    }
    kwargs = LLMFactory._build_openai_kwargs("MiniMax-M3", metadata)
    assert kwargs["extra_body"]["reasoning_split"] is True


@pytest.mark.parametrize("stream_mode", ["delta", "cumulative"])
def test_factory_carries_reasoning_contract_and_binding_without_sending_internal_options(monkeypatch, stream_mode):
    from langchain_core.messages import HumanMessage
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    surface = {
        "mode": "provider_reasoning", "trust": "official", "requestStyle": "minimax_interleaved_thinking",
        "responseFields": ["reasoning_details", "content[inline_think]"], "streamMode": stream_mode,
    }
    metadata = {
        "is_found": True, "model_id": "custom-wire-model", "model_ref": "configured-provider::custom-binding",
        "api_standard": "openai", "wire_protocol": "openai.chat_completions",
        "api_key": "test-key", "base_url": "https://fixture.example/v1", "reasoning_surface": surface,
    }
    monkeypatch.setattr(LLMFactory, "_resolve_model_metadata", classmethod(lambda cls, _model_id: metadata))
    monkeypatch.setattr(LLMFactory, "_attach_telemetry", classmethod(lambda cls, kwargs, *_args, **_kwargs: kwargs))
    adapter = LLMFactory.create_chat_model("configured-provider::custom-binding")
    model = adapter._get_base_model()
    payload = model._get_request_payload([HumanMessage(content="public fixture")])
    assert payload["extra_body"]["reasoning_split"] is True
    assert payload["model"] == "custom-wire-model"
    assert "v8_reasoning_surface" not in payload
    assert model._reasoning_origin()["modelRef"] == "configured-provider::custom-binding"
    chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_details": [{"type": "reasoning.text", "text": "A"}]}}]}] * 2
    from test_openai_compatible_reasoning_adapter import _SyncClient
    object.__setattr__(model, "client", _SyncClient(chunks))
    combined = list(model.stream([HumanMessage(content="public fixture")]))
    full = combined[0]
    for chunk in combined[1:]:
        full += chunk
    assert full.additional_kwargs["reasoning_details"][0]["text"] == ("AA" if stream_mode == "delta" else "A")
    message = model._create_chat_result({"choices": [{"message": {"role": "assistant", "content": "<think>public fixture</think>Visible"}}]}).generations[0].message
    other = V8OpenAICompatibleChatModel(model="custom-wire-model", api_key="test-key", base_url="https://other.example/v1")
    assert other._get_request_payload([message])["messages"][0]["content"] == "Visible"


def test_split_reasoning_transport_is_not_inferred_for_other_chat_models():
    metadata = {
        "wire_protocol": "openai.chat_completions",
        "reasoning_surface": {
            "mode": "provider_reasoning",
            "trust": "catalog_only",
            "requestStyle": "minimax_interleaved_thinking",
            "responseFields": ["reasoning_details"],
        },
    }

    assert provider_reasoning_transport_patch(metadata) == {}


def test_openai_compatible_stream_usage_is_capability_driven() -> None:
    supported = LLMFactory._build_openai_kwargs(
        "configured-model",
        {
            "api_key": "sk-test",
            "capabilities": {"streaming": True, "streamUsage": True},
        },
        streaming=True,
    )
    unsupported = LLMFactory._build_openai_kwargs(
        "another-model",
        {
            "api_key": "sk-test",
            "capabilities": {"streaming": True, "streamUsage": False},
        },
        streaming=True,
    )

    assert supported["stream_usage"] is True
    assert "stream_usage" not in unsupported


def test_modelhub_stream_usage_alias_is_normalized_without_model_name_logic() -> None:
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "alias-provider": {
                    "provider": {"name": "Alias Provider", "api_standard": "openai"},
                    "models": {
                        "alias-model": {
                            "type": "CHAT",
                            "capabilities": {"chat": True, "stream_usage": True},
                        }
                    },
                }
            }
        }
    )
    record = model_control_plane.get_model_record("alias-provider::alias-model", config)
    assert record["model"]["capabilities"]["streamUsage"] is True
