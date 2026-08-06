from __future__ import annotations

import pytest

from core.config_broker_service import _provider_target_fingerprint
from core.model_catalog_connection import build_catalog_model_connection_plan


def _provider(**overrides):
    return {
        "id": "example",
        "name": "Example",
        "baseUrl": "https://api.example.test/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        "models": [],
        **overrides,
    }


def _model(**overrides):
    return {
        "id": "reasoning-model",
        "type": "TEXT",
        "contextWindow": 300_000,
        "maxTokens": 32_000,
        "capabilities": {"chat": True, "reasoning": True},
        "capabilityClass": "chat_reasoning",
        "reasoningEffortControl": {
            "supportsReasoningEffort": True,
            "levels": ["auto", "low", "high"],
        },
        **overrides,
    }


def test_catalog_channel_inherits_default_wire_protocol_and_projects_auth_contract() -> None:
    provider = _provider(
        channels=[
            {
                "id": "responses",
                "baseUrl": "https://responses.example.test/v1",
                "apiStandard": "openai",
                "wireProtocols": ["openai.responses"],
                "defaultWireProtocol": "openai.responses",
            }
        ],
        defaultChannelId="responses",
    )

    plan = build_catalog_model_connection_plan(
        provider=provider,
        model=_model(),
        model_id="reasoning-model",
        channel_id="responses",
        use_catalog_default_channel=True,
    )

    assert plan["selectedChannel"] == {
        "id": "responses",
        "wireProtocol": "openai.responses",
        "baseUrl": "https://responses.example.test/v1",
        "apiStandard": "openai",
    }
    assert plan["providerPatch"]["authContract"] == {
        "type": "api_key",
        "header": "Authorization",
        "scheme": "Bearer",
    }
    assert plan["modelPatch"]["endpointBinding"]["wireProtocol"] == "openai.responses"
    assert plan["modelPatch"]["reasoningEffortControl"]["levels"] == ["auto", "low", "high"]


def test_catalog_channel_rejects_unsupported_wire_protocol() -> None:
    provider = _provider(
        channels=[
            {
                "id": "responses",
                "baseUrl": "https://responses.example.test/v1",
                "apiStandard": "openai",
                "wireProtocols": ["openai.responses"],
            }
        ]
    )

    with pytest.raises(ValueError, match="not supported"):
        build_catalog_model_connection_plan(
            provider=provider,
            model=_model(),
            model_id="reasoning-model",
            channel_id="responses",
            wire_protocol="openai.chat_completions",
        )


def test_catalog_placeholder_without_endpoint_is_not_connectable() -> None:
    with pytest.raises(ValueError, match="executable runtime adapter"):
        build_catalog_model_connection_plan(
            provider=_provider(baseUrl="", apiStandard="catalog_only", auth={"type": "none"}),
            model=_model(),
            model_id="reasoning-model",
        )


@pytest.mark.parametrize(
    ("base_url", "error"),
    [
        ("https://user:password@api.example.test/v1", "userinfo"),
        ("https://api.example.test/v1?api_key=secret", "URL query or fragment"),
        ("https://api.example.test/v1#token", "URL query or fragment"),
    ],
)
def test_catalog_connection_rejects_secret_bearing_endpoint_urls(
    base_url: str,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        build_catalog_model_connection_plan(
            provider=_provider(baseUrl=base_url),
            model=_model(),
            model_id="reasoning-model",
        )


def test_auth_transport_changes_credential_target_fingerprint() -> None:
    common = {
        "base_url": "https://api.example.test/v1",
        "api_standard": "openai",
        "credentialRealm": "example",
        "type": "API",
    }

    bearer = _provider_target_fingerprint(
        {
            **common,
            "authContract": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        }
    )
    header_key = _provider_target_fingerprint(
        {
            **common,
            "authContract": {"type": "api_key", "header": "x-api-key"},
        }
    )

    assert bearer
    assert header_key
    assert bearer != header_key


def test_media_endpoint_template_is_materialized_once() -> None:
    plan = build_catalog_model_connection_plan(
        provider=_provider(providerKind="media_generation"),
        model=_model(
            id="stable-image-core",
            type="IMAGE",
            capabilityClass="media_generation",
            mediaLimits={
                "endpointPath": "v2beta/stable-image/generate/{model}",
                "providerModelId": "stable-image-core",
                "operationKinds": ["image.generate"],
            },
        ),
        model_id="v2beta/stable-image/generate/stable-image-core",
    )

    assert plan["modelId"] == "v2beta/stable-image/generate/stable-image-core"
    binding = plan["modelPatch"]["endpointBinding"]
    assert binding["route"] == "v2beta/stable-image/generate/stable-image-core"
    assert binding["endpointPath"] == "v2beta/stable-image/generate/stable-image-core"
    assert binding["providerModelId"] == "stable-image-core"


def test_no_auth_provider_has_no_credential_mode() -> None:
    plan = build_catalog_model_connection_plan(
        provider=_provider(auth={"type": "none"}),
        model=_model(),
        model_id="reasoning-model",
    )

    assert plan["credentialRequired"] is False
    assert plan["credentialConfigured"] is True
    assert plan["credentialMode"] == "none"
    assert plan["providerPatch"]["authContract"] == {"type": "none"}


def test_catalog_reconnect_preserves_existing_provider_and_model_disable_gates() -> None:
    plan = build_catalog_model_connection_plan(
        provider=_provider(),
        model=_model(),
        model_id="reasoning-model",
        existing_provider={"is_enabled": False},
        existing_model={"isEnabled": False},
    )

    assert plan["providerPatch"]["is_enabled"] is False
    assert plan["modelPatch"]["isEnabled"] is False


def test_channel_auth_contract_is_bound_to_the_model_endpoint() -> None:
    provider = _provider(
        channels=[
            {
                "id": "custom-header",
                "baseUrl": "https://api.example.test/v1",
                "apiStandard": "openai",
                "wireProtocols": ["openai.chat_completions"],
                "defaultWireProtocol": "openai.chat_completions",
                "auth": {"type": "api_key", "header": "x-api-key"},
            }
        ],
        defaultChannelId="custom-header",
    )

    plan = build_catalog_model_connection_plan(
        provider=provider,
        model=_model(),
        model_id="reasoning-model",
        channel_id="custom-header",
        use_catalog_default_channel=True,
    )

    assert plan["providerPatch"]["authContract"] == {
        "type": "api_key",
        "header": "x-api-key",
    }
    assert plan["modelPatch"]["endpointBinding"]["authContract"] == {
        "type": "api_key",
        "header": "x-api-key",
    }


def test_channel_cannot_promote_api_key_provider_to_oauth_file() -> None:
    provider = _provider(
        channels=[
            {
                "id": "stolen-oauth",
                "baseUrl": "https://collector.example.test/v1",
                "apiStandard": "openai",
                "auth": {"type": "oauth_file", "path": "C:/private/token.json"},
            }
        ]
    )

    with pytest.raises(ValueError, match="cannot introduce or change oauth_file auth"):
        build_catalog_model_connection_plan(
            provider=provider,
            model=_model(),
            model_id="reasoning-model",
            channel_id="stolen-oauth",
        )


def test_catalog_only_provider_is_not_reported_as_executable_even_with_a_base_url() -> None:
    with pytest.raises(ValueError, match="executable runtime adapter"):
        build_catalog_model_connection_plan(
            provider=_provider(
                apiStandard="catalog_only",
                providerKind="media_generation",
                adapter="catalog_only",
            ),
            model=_model(
                type="IMAGE",
                capabilityClass="media_generation",
                adapter="catalog_only",
            ),
            model_id="future-image-model",
        )


def test_catalog_connection_materializes_protocol_endpoint_template() -> None:
    plan = build_catalog_model_connection_plan(
        provider=_provider(
            apiStandard="gemini",
            auth={"type": "api_key", "query": "key"},
            channels=[
                {
                    "id": "gemini-native",
                    "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
                    "apiStandard": "gemini",
                    "wireProtocols": ["gemini.generate_content"],
                    "defaultWireProtocol": "gemini.generate_content",
                }
            ],
            defaultChannelId="gemini-native",
        ),
        model=_model(id="gemini-test"),
        model_id="gemini-test",
        use_catalog_default_channel=True,
    )

    binding = plan["modelPatch"]["endpointBinding"]
    assert binding["endpointPath"] == "models/gemini-test:generateContent"
    assert "{model}" not in binding["endpointPath"]
