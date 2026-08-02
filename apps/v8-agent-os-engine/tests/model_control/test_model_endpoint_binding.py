from __future__ import annotations

from copy import deepcopy

import pytest

from core.model_endpoint_binding import (
    build_model_endpoint_binding,
    persist_model_endpoint_binding,
    public_models_config,
)
from core.model_control_plane import ModelControlPlane
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.storage import storage


def test_manual_media_route_keeps_visible_route_and_separates_wire_model_id():
    provider = {
        "base_url": "https://example.test/v1",
        "api_standard": "openai_images",
    }
    model = {"type": "IMAGE", "capabilityClass": "media_generation"}

    persisted = persist_model_endpoint_binding(
        "cpm",
        "images/edits/gpt-image-2",
        provider,
        model,
        source="manual",
    )
    binding = persisted["endpointBinding"]

    assert binding["route"] == "images/edits/gpt-image-2"
    assert binding["endpointPath"] == "images/edits"
    assert binding["providerModelId"] == "gpt-image-2"
    assert binding["operationKind"] == "image.edit"
    assert binding["requestUrlPreview"] == "https://example.test/v1/images/edits"
    assert binding["provenance"] == {"source": "manual", "confidence": "authoritative"}


def test_catalog_submit_path_is_relative_to_provider_base_url():
    binding = build_model_endpoint_binding(
        "minimax",
        "image_generation/image-01-live",
        {"base_url": "https://api.minimaxi.com/v1", "api_standard": "minimax_image_generation"},
        {
            "type": "IMAGE",
            "mediaLimits": {
                "submitPath": "/v1/image_generation",
                "providerModelId": "image-01-live",
                "operationKinds": ["image.generate", "image.edit"],
            },
        },
    )

    assert binding["route"] == "image_generation/image-01-live"
    assert binding["endpointPath"] == "image_generation"
    assert binding["providerModelId"] == "image-01-live"
    assert binding["operationKind"] == ""


def test_media_binding_does_not_inherit_chat_protocol_and_preserves_multi_operation_scope():
    provider = {
        "api_standard": "openai",
        "base_url": "https://api.example.test/v1",
        "channels": [
            {
                "id": "openai",
                "apiStandard": "openai",
                "baseUrl": "https://api.example.test/v1",
                "wireProtocols": ["openai.chat_completions"],
                "defaultWireProtocol": "openai.chat_completions",
            }
        ],
        "defaultChannelId": "openai",
    }
    persisted = persist_model_endpoint_binding(
        "images",
        "images/generations/gpt-image-2",
        provider,
        {
            "type": "IMAGE",
            "operationKinds": ["image.generate", "image.edit"],
            "mediaLimits": {
                "adapter": "openai_images",
                "operationKinds": ["image.generate", "image.edit"],
            },
            "endpointBinding": {
                "channelId": "openai",
                "wireProtocol": "",
                "operationKind": "",
                "adapter": "openai_images",
            },
        },
        source="manual",
    )

    binding = persisted["endpointBinding"]
    assert binding["wireProtocol"] == ""
    assert binding["operationKind"] == ""
    assert binding["adapter"] == "openai_images"
    assert persisted["mediaLimits"]["operationKinds"] == ["image.generate", "image.edit"]


def test_explicitly_unbound_media_adapter_is_not_replaced_by_stale_media_limit():
    binding = build_model_endpoint_binding(
        "images",
        "images/generations/gpt-image-2",
        {"base_url": "https://api.example.test/v1", "api_standard": "openai_images"},
        {
            "type": "IMAGE",
            "mediaLimits": {"adapter": "openai_images", "operationKinds": ["image.generate"]},
            "endpointBinding": {"adapter": "", "operationKind": "image.generate"},
        },
    )

    assert binding["adapter"] == ""


def test_non_media_model_id_with_slash_is_not_treated_as_endpoint_route():
    binding = build_model_endpoint_binding(
        "modelscope",
        "Qwen/Qwen3-Embedding-8B",
        {"base_url": "https://api-inference.modelscope.cn/v1", "api_standard": "openai"},
        {"type": "EMBEDDING", "capabilityClass": "embedding"},
    )

    assert binding["route"] == "Qwen/Qwen3-Embedding-8B"
    assert binding["endpointPath"] == ""
    assert binding["providerModelId"] == "Qwen/Qwen3-Embedding-8B"


def test_provider_native_media_route_is_split_without_a_hidden_adapter_rewrite():
    binding = build_model_endpoint_binding(
        "dashscope",
        "services/aigc/multimodal-generation/generation/qwen-image-2.0-pro",
        {"base_url": "https://dashscope.aliyuncs.com/api/v1", "api_standard": "dashscope"},
        {"type": "IMAGE", "capabilityClass": "media_generation"},
    )

    assert binding["endpointPath"] == "services/aigc/multimodal-generation/generation"
    assert binding["providerModelId"] == "qwen-image-2.0-pro"
    assert binding["operationKind"] == "image.generate"
    assert binding["requestUrlPreview"] == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def test_model_binding_selects_an_explicit_provider_channel_without_rewriting_base_url():
    provider = {
        "api_standard": "openai",
        "base_url": "https://api.example.test/v1",
        "channels": [
            {
                "id": "openai",
                "label": "OpenAI-compatible",
                "apiStandard": "openai",
                "baseUrl": "https://api.example.test/v1",
                "wireProtocols": ["openai.chat_completions"],
                "defaultWireProtocol": "openai.chat_completions",
            },
            {
                "id": "anthropic",
                "label": "Anthropic Messages",
                "apiStandard": "anthropic",
                "baseUrl": "https://api.example.test/anthropic",
                "wireProtocols": ["anthropic.messages"],
                "defaultWireProtocol": "anthropic.messages",
            },
        ],
        "defaultChannelId": "openai",
    }
    binding = build_model_endpoint_binding(
        "example",
        "model-a",
        provider,
        {"type": "TEXT", "endpointBinding": {"channelId": "anthropic"}},
    )

    assert binding["channelId"] == "anthropic"
    assert binding["apiStandard"] == "anthropic"
    assert binding["wireProtocol"] == "anthropic.messages"
    assert binding["baseUrl"] == "https://api.example.test/anthropic"
    assert binding["requestUrlPreview"] == "https://api.example.test/anthropic/v1/messages"


def test_anthropic_endpoint_preview_exposes_version_already_in_channel_base_url():
    provider = {
        "api_standard": "openai",
        "base_url": "https://api.example.test/v1",
        "channels": [
            {
                "id": "anthropic",
                "label": "Anthropic Messages",
                "apiStandard": "anthropic",
                "baseUrl": "https://api.example.test/v1",
                "wireProtocols": ["anthropic.messages"],
                "defaultWireProtocol": "anthropic.messages",
            }
        ],
        "defaultChannelId": "anthropic",
    }

    binding = build_model_endpoint_binding(
        "custom-provider",
        "claude-opus-test",
        provider,
        {"type": "TEXT", "endpointBinding": {"channelId": "anthropic"}},
    )

    assert binding["endpointPath"] == "v1/messages"
    assert binding["requestUrlPreview"] == "https://api.example.test/v1/v1/messages"


def test_legacy_provider_projection_keeps_v1_and_does_not_infer_gemini_v1beta():
    binding = build_model_endpoint_binding(
        "custom",
        "gemini-3.5-flash-low",
        {"api_standard": "openai", "base_url": "https://proxy.example.test/v1"},
        {"type": "TEXT"},
    )

    assert binding["channelId"] == "default"
    assert binding["baseUrl"] == "https://proxy.example.test/v1"
    assert binding["wireProtocol"] == ""


def test_model_binding_rejects_protocol_outside_selected_channel():
    provider = {
        "channels": [
            {
                "id": "anthropic",
                "apiStandard": "anthropic",
                "baseUrl": "https://api.example.test/anthropic",
                "wireProtocols": ["anthropic.messages"],
                "defaultWireProtocol": "anthropic.messages",
            }
        ],
        "defaultChannelId": "anthropic",
    }

    with pytest.raises(ValueError, match="is not supported"):
        persist_model_endpoint_binding(
            "example",
            "model-a",
            provider,
            {
                "type": "TEXT",
                "endpointBinding": {
                    "channelId": "anthropic",
                    "wireProtocol": "openai.chat_completions",
                },
            },
            source="manual",
        )


def test_explicit_channel_api_version_is_visible_in_request_preview():
    binding = build_model_endpoint_binding(
        "gemini-proxy",
        "gemini-3.5-flash-low",
        {
            "channels": [
                {
                    "id": "gemini",
                    "apiStandard": "gemini",
                    "baseUrl": "https://provider.example.test/gemini",
                    "apiVersion": "v1beta",
                    "wireProtocols": ["gemini.generate_content"],
                    "defaultWireProtocol": "gemini.generate_content",
                }
            ],
            "defaultChannelId": "gemini",
        },
        {"type": "MULTIMODAL", "endpointBinding": {"channelId": "gemini"}},
    )

    assert binding["apiVersion"] == "v1beta"
    assert binding["requestUrlPreview"] == "https://provider.example.test/gemini/v1beta/models/{model}:generateContent"


def test_public_config_redacts_credentials_and_projects_legacy_binding_without_mutation():
    original = {
        "providers": {
            "cpm": {
                "provider": {
                    "name": "CPM",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-value",
                    "credential_mode": "apiKey",
                },
                "models": {
                    "images/generations/gpt-image-2": {
                        "type": "IMAGE",
                        "mediaLimits": {"providerModelId": "gpt-image-2"},
                    }
                },
            }
        }
    }
    snapshot = deepcopy(original)

    public = public_models_config(original)
    provider = public["providers"]["cpm"]["provider"]
    model = public["providers"]["cpm"]["models"]["images/generations/gpt-image-2"]

    assert "api_key" not in provider
    assert provider["credentialConfigured"] is True
    assert model["endpointBinding"]["providerModelId"] == "gpt-image-2"
    assert model["endpointBinding"]["persisted"] is False
    assert original == snapshot


def test_public_config_keeps_oauth_path_configuration_without_exposing_raw_credential():
    public = public_models_config(
        {
            "providers": {
                "codex": {
                    "provider": {
                        "name": "Codex",
                        "api_key": "oauth:C:/Users/example/.codex/auth.json",
                        "credential_mode": "oauthFile",
                    },
                    "models": {},
                }
            }
        }
    )

    provider = public["providers"]["codex"]["provider"]
    assert "api_key" not in provider
    assert provider["oauthPath"] == "C:/Users/example/.codex/auth.json"
    assert provider["credentialConfigured"] is True


def test_model_credentials_use_a_separate_opaque_namespace():
    store = CredentialRefStore(MemoryCredentialBackend())

    reference = store.put("secret-value", namespace="model")

    assert reference.startswith("cred:v8-model:")
    assert store.resolve(reference) == "secret-value"


def test_provider_write_persists_only_credential_reference(monkeypatch):
    persisted = {"providers": {}}
    store = CredentialRefStore(MemoryCredentialBackend())
    plane = ModelControlPlane(credential_store=store)

    monkeypatch.setattr(storage, "get_models_config", lambda: deepcopy(persisted))

    def save_config(config):
        persisted.clear()
        persisted.update(deepcopy(config))

    monkeypatch.setattr(storage, "save_models_config", save_config)

    plane.upsert_provider_record(
        "cpm",
        {
            "name": "CPM",
            "base_url": "https://example.test/v1",
            "api_standard": "openai_images",
            "api_key": "secret-value",
        },
    )

    stored_provider = persisted["providers"]["cpm"]["provider"]
    assert "api_key" not in stored_provider
    assert stored_provider["credentialRef"].startswith("cred:v8-model:")
    assert plane.get_config()["providers"]["cpm"]["provider"]["api_key"] == "secret-value"
    assert "api_key" not in plane.get_public_config()["providers"]["cpm"]["provider"]


def test_provider_write_persists_multiple_channels_and_projects_them_publicly(monkeypatch):
    persisted = {"providers": {}}
    plane = ModelControlPlane(credential_store=CredentialRefStore(MemoryCredentialBackend()))
    monkeypatch.setattr(storage, "get_models_config", lambda: deepcopy(persisted))

    def save_config(config):
        persisted.clear()
        persisted.update(deepcopy(config))

    monkeypatch.setattr(storage, "save_models_config", save_config)
    channels = [
        {
            "id": "openai",
            "label": "OpenAI-compatible",
            "apiStandard": "openai",
            "baseUrl": "https://api.example.test/v1",
            "wireProtocols": ["openai.chat_completions"],
            "defaultWireProtocol": "openai.chat_completions",
        },
        {
            "id": "anthropic",
            "label": "Anthropic Messages",
            "apiStandard": "anthropic",
            "baseUrl": "https://api.example.test/anthropic",
            "wireProtocols": ["anthropic.messages"],
            "defaultWireProtocol": "anthropic.messages",
        },
    ]

    plane.upsert_provider_record(
        "multi",
        {
            "name": "Multi",
            "base_url": channels[0]["baseUrl"],
            "api_standard": "openai",
            "channels": channels,
            "defaultChannelId": "openai",
        },
    )

    public_provider = plane.get_public_config()["providers"]["multi"]["provider"]
    assert public_provider["defaultChannelId"] == "openai"
    assert [item["id"] for item in public_provider["channels"]] == ["openai", "anthropic"]
    assert public_provider["channelsSource"] == "configured"
