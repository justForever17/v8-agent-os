from __future__ import annotations

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
