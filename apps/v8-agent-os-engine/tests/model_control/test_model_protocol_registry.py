from __future__ import annotations

import asyncio

from api import platform_routes
from langchain_openai import ChatOpenAI

from core.llm_factory import LLMFactory
from core.model_endpoint_binding import build_model_endpoint_binding, persist_model_endpoint_binding
from core.model_protocol_registry import suggest_model_protocol
from core.provider_hosted_tools import provider_hosted_tool_schemas


def _use_in_memory_model_hub_commit(monkeypatch):
    def _commit(*, plan, incoming_credential, **_kwargs):
        provider_patch = dict(plan["providerPatch"])
        if incoming_credential:
            provider_patch["api_key"] = incoming_credential
        mutation = platform_routes.model_control_plane.upsert_provider_model_records(
            provider_id=str(plan["providerId"]),
            provider_patch=provider_patch,
            model_id=str(plan["modelId"]),
            model_patch=dict(plan["modelPatch"]),
            source="quick_connect",
            replace_provider_models=bool(plan.get("replaceProviderModels")),
        )
        return {
            "ok": True,
            "transactionId": "cfg_txn_test",
            "ownerId": "test",
            "config": platform_routes.model_control_plane.get_public_config(dict(mutation.get("config") or {})),
        }

    monkeypatch.setattr(platform_routes, "_execute_model_hub_connection", _commit)


def test_first_party_openai_reasoning_model_suggests_responses_without_rewriting_legacy_binding():
    advice = suggest_model_protocol(
        "openai",
        "openai",
        "gpt-5.6-sol",
        provider_meta={"name": "OpenAI"},
        model_meta={"type": "MULTIMODAL"},
    )
    binding = build_model_endpoint_binding(
        "openai",
        "gpt-5.6-sol",
        {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "api_standard": "openai"},
        {"type": "MULTIMODAL"},
    )

    assert advice["wireProtocol"] == "openai.responses"
    assert advice["confidence"] == "reviewed"
    assert binding["wireProtocol"] == ""
    assert binding["endpointPath"] == ""
    assert binding["protocolSuggestion"] == "openai.responses"
    assert binding["protocolEndpointPath"] == "responses"


def test_custom_openai_compatible_provider_is_warned_and_defaults_to_chat_completions():
    advice = suggest_model_protocol(
        "custom-cpm-a3678d32",
        "openai",
        "gpt-5.6-sol",
        provider_meta={"name": "CPM", "isCustom": True},
        model_meta={"type": "MULTIMODAL"},
    )

    assert advice["wireProtocol"] == "openai.chat_completions"
    assert advice["confidence"] == "hint"
    assert advice["warning"]


def test_explicit_responses_binding_controls_endpoint_and_langchain_request_mode():
    persisted = persist_model_endpoint_binding(
        "cpm",
        "gpt-5.6-sol",
        {"base_url": "https://example.test/v1", "api_standard": "openai"},
        {
            "type": "MULTIMODAL",
            "endpointBinding": {
                "wireProtocol": "openai.responses",
                "protocolConfidence": "authoritative",
                "protocolSource": "manual",
            },
        },
        source="manual",
    )
    binding = persisted["endpointBinding"]
    kwargs = LLMFactory._build_openai_kwargs(
        "gpt-5.6-sol",
        {"api_key": "sk-test", "wire_protocol": binding["wireProtocol"]},
        store=True,
        use_previous_response_id=True,
    )

    assert binding["endpointPath"] == "responses"
    assert binding["providerModelId"] == "gpt-5.6-sol"
    assert binding["protocolWarning"] == ""
    assert kwargs["use_responses_api"] is True
    assert kwargs["store"] is False
    assert kwargs["use_previous_response_id"] is False


def test_responses_hosted_tools_are_default_off_and_reject_unmanaged_tool_types():
    default_binding = build_model_endpoint_binding(
        "openai",
        "gpt-5.6-sol",
        {"base_url": "https://api.openai.com/v1", "api_standard": "openai"},
        {"type": "MULTIMODAL", "endpointBinding": {"wireProtocol": "openai.responses"}},
    )
    enabled_binding = build_model_endpoint_binding(
        "openai",
        "gpt-5.6-sol",
        {"base_url": "https://api.openai.com/v1", "api_standard": "openai"},
        {
            "type": "MULTIMODAL",
            "endpointBinding": {
                "wireProtocol": "openai.responses",
                "providerHostedTools": {
                    "enabled": True,
                    "tools": ["web_search", "computer_use_preview", "mcp"],
                    "source": "manual",
                },
            },
        },
    )

    assert default_binding["providerHostedTools"] == {
        "enabled": False,
        "tools": ["web_search"],
        "source": "default_disabled",
    }
    assert provider_hosted_tool_schemas(
        wire_protocol=default_binding["wireProtocol"],
        config=default_binding["providerHostedTools"],
    ) == []
    assert enabled_binding["providerHostedTools"] == {
        "enabled": True,
        "tools": ["web_search"],
        "source": "manual",
    }
    assert provider_hosted_tool_schemas(
        wire_protocol=enabled_binding["wireProtocol"],
        config=enabled_binding["providerHostedTools"],
    ) == [{"type": "web_search"}]


def test_hosted_tool_setting_is_inert_outside_responses_protocol():
    assert provider_hosted_tool_schemas(
        wire_protocol="openai.chat_completions",
        config={"enabled": True, "tools": ["web_search"], "source": "manual"},
    ) == []


def test_installed_langchain_openai_adapter_accepts_responses_hosted_tool_schema():
    model = ChatOpenAI(
        model="gpt-5.6-sol",
        api_key="test-key",
        use_responses_api=True,
    )

    bound = model.bind_tools([{"type": "web_search"}])

    assert bound.kwargs["tools"] == [{"type": "web_search"}]


def test_media_models_do_not_receive_chat_protocol_advice():
    advice = suggest_model_protocol(
        "cpm",
        "openai",
        "gpt-image-2",
        provider_meta={"name": "CPM"},
        model_meta={"type": "IMAGE", "capabilityClass": "media_generation"},
    )

    assert advice["wireProtocol"] == ""
    assert advice["confidence"] == "not_applicable"


def test_comfyui_provider_without_model_type_does_not_receive_chat_protocol_advice():
    advice = suggest_model_protocol(
        "comfyui",
        "comfyui",
        "workflow.generate",
        provider_meta={"name": "ComfyUI", "providerKind": "media_generation"},
        model_meta={},
    )

    assert advice["wireProtocol"] == ""
    assert advice["confidence"] == "not_applicable"


def test_perplexity_sonar_uses_reviewed_chat_completions_protocol():
    advice = suggest_model_protocol(
        "perplexity",
        "openai",
        "sonar-pro",
        provider_meta={"name": "Perplexity"},
        model_meta={"type": "TEXT"},
    )

    assert advice["wireProtocol"] == "openai.chat_completions"
    assert advice["confidence"] == "reviewed"
    assert advice["warning"] == ""


def test_xiaomi_mimo_openai_and_anthropic_catalogs_use_documented_protocols():
    openai_advice = suggest_model_protocol(
        "xiaomi-mimo",
        "openai",
        "mimo-v2.5-pro",
        provider_meta={"name": "Xiaomi MiMo"},
        model_meta={"type": "MULTIMODAL"},
    )
    anthropic_advice = suggest_model_protocol(
        "xiaomi-mimo-tokenplan-anthropic",
        "anthropic",
        "mimo-v2.5-pro",
        provider_meta={"name": "Xiaomi MiMo Token Plan"},
        model_meta={"type": "MULTIMODAL"},
    )

    assert openai_advice["wireProtocol"] == "openai.chat_completions"
    assert openai_advice["confidence"] == "reviewed"
    assert anthropic_advice["wireProtocol"] == "anthropic.messages"
    assert anthropic_advice["confidence"] == "reviewed"


def test_explicit_chat_completions_cannot_be_overridden_by_call_kwargs():
    kwargs = LLMFactory._build_openai_kwargs(
        "gpt-5.6-sol",
        {"api_key": "sk-test", "wire_protocol": "openai.chat_completions"},
        use_responses_api=True,
    )

    assert kwargs["use_responses_api"] is False


def test_embedding_models_do_not_receive_chat_protocol_advice():
    advice = suggest_model_protocol(
        "modelscope",
        "openai",
        "Qwen/Qwen3-Embedding-8B",
        provider_meta={"name": "ModelScope"},
        model_meta={"type": "EMBEDDING", "capabilityClass": "embedding"},
    )

    assert advice["wireProtocol"] == ""
    assert advice["confidence"] == "not_applicable"


def test_quick_connect_exposes_but_does_not_persist_first_party_protocol_suggestion(monkeypatch):
    _use_in_memory_model_hub_commit(monkeypatch)
    saved: dict = {}
    provider = {
        "id": "openai",
        "name": "OpenAI",
        "baseUrl": "https://api.openai.com/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "models": [{"id": "gpt-5.6-sol", "type": "MULTIMODAL"}],
    }
    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "openai" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved.setdefault("config", config))

    result = asyncio.run(platform_routes.connect_model_provider({
        "providerId": "openai",
        "modelId": "gpt-5.6-sol",
        "apiKey": "sk-test",
        "modelType": "MULTIMODAL",
    }))

    binding = result["config"]["providers"]["openai"]["models"]["gpt-5.6-sol"]["endpointBinding"]
    assert binding["wireProtocol"] == ""
    assert binding["endpointPath"] == ""
    assert binding["protocolSuggestion"] == "openai.responses"
    assert binding["protocolConfidence"] == "reviewed"


def test_quick_connect_custom_provider_keeps_chat_suggestion_without_silent_binding(monkeypatch):
    _use_in_memory_model_hub_commit(monkeypatch)
    saved: dict = {}
    provider = {
        "id": "custom-cpm-a3678d32",
        "name": "CPM",
        "baseUrl": "http://example.test/v1",
        "apiStandard": "openai",
        "isCustom": True,
        "auth": {"type": "api_key"},
        "models": [],
    }
    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == provider["id"] else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved.setdefault("config", config))

    result = asyncio.run(platform_routes.connect_model_provider({
        "providerId": provider["id"],
        "modelId": "gpt-5.6-sol",
        "apiKey": "sk-test",
        "modelType": "MULTIMODAL",
    }))

    binding = result["config"]["providers"][provider["id"]]["models"]["gpt-5.6-sol"]["endpointBinding"]
    assert binding["wireProtocol"] == ""
    assert binding["endpointPath"] == ""
    assert binding["protocolSuggestion"] == "openai.chat_completions"
    assert binding["protocolConfidence"] == "hint"
    assert binding["protocolWarning"]
