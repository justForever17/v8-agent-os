from __future__ import annotations

from core.tool_surface import _render_config_broker_surface


def test_catalog_surface_bounds_providers_and_model_ids_without_transport_or_secret_fields() -> None:
    providers = []
    for provider_index in range(10):
        providers.append(
            {
                "providerId": f"provider-a{provider_index:02d}",
                "name": f"Provider {provider_index}",
                "baseUrl": f"https://private-{provider_index}.example/v1",
                "channels": [
                    {
                        "id": "responses",
                        "baseUrl": f"https://channel-{provider_index}.example/v1",
                    }
                ],
                "credentialRequired": True,
                "credentialRef": f"cred:v8-model:provider-secret-{provider_index}",
                "apiKey": f"sk-private-{provider_index}",
                "rawProviderPayload": {"authorization": f"Bearer private-{provider_index}"},
                "models": [
                    {
                        "id": f"catalog-model-{provider_index:02d}-{model_index:02d}",
                        "endpoint": f"https://model-{provider_index}-{model_index}.example/v1",
                        "credentialRef": f"cred:v8-model:model-secret-{model_index}",
                        "raw": {"providerResponse": "internal-only"},
                    }
                    for model_index in range(15)
                ],
            }
        )

    visible = _render_config_broker_surface(
        {
            "ok": True,
            "mode": "catalog_models",
            "total": len(providers),
            "providers": providers,
            "summary": "目录中有 10 个匹配供应商。",
        },
        "toolobs://config-broker-catalog",
    )

    assert visible.startswith("Configuration control (catalog_models)")
    assert "Model Hub providers:" in visible
    assert "- provider-a00: Provider 0" in visible
    assert "- provider-a07: Provider 7" in visible
    assert "- provider-a08: Provider 8" not in visible
    assert "- provider-a09: Provider 9" not in visible
    assert "catalog-model-00-00" in visible
    assert "catalog-model-00-11" in visible
    assert "catalog-model-00-12" not in visible
    assert "catalog-model-01-00" not in visible
    assert "... 3 more model(s); filter by provider/query or inspect detail" in visible
    assert "... 15 model(s) not shown; filter by provider/query or inspect detail" in visible
    assert "... 2 provider(s) omitted from this summary; inspect detail" in visible
    assert "private-0.example" not in visible
    assert "channel-0.example" not in visible
    assert "model-0-0.example" not in visible
    assert "cred:v8-model" not in visible
    assert "sk-private" not in visible
    assert "Bearer private" not in visible
    assert "credentialRequired" not in visible
    assert "rawProviderPayload" not in visible
    assert '"providers"' not in visible
    assert not visible.lstrip().startswith("{")


def test_catalog_discover_surface_shows_provider_model_status_and_offset_remaining_count() -> None:
    visible = _render_config_broker_surface(
        {
            "ok": True,
            "mode": "catalog_discover",
            "providerId": "provider-discovered",
            "offset": 4,
            "total": 21,
            "models": [
                {
                    "modelId": "model-alpha",
                    "availability": {"status": "available"},
                },
                {
                    "modelId": "model-beta",
                    "availability": {"catalogConnectable": True},
                },
                {
                    "modelId": "model-gamma",
                    "availability": {
                        "catalogConnectable": False,
                        "catalogConnectReason": "credential_required",
                    },
                },
            ],
        },
        "toolobs://config-broker-discover",
    )

    assert "Discovered models:" in visible
    assert "- model-alpha (provider-discovered): available" in visible
    assert "- model-beta (provider-discovered): connectable" in visible
    assert "- model-gamma (provider-discovered): credential_required" in visible
    assert "... use offset=7 to read the remaining 14 model(s)" in visible


def test_catalog_provider_surface_deducts_offset_from_remaining_count() -> None:
    visible = _render_config_broker_surface(
        {
            "ok": True,
            "mode": "catalog_models",
            "offset": 5,
            "total": 12,
            "providers": [
                {"providerId": "provider-5", "name": "Provider 5"},
                {"providerId": "provider-6", "name": "Provider 6"},
                {"providerId": "provider-7", "name": "Provider 7"},
            ],
        },
        "toolobs://config-broker-providers",
    )

    assert "... use offset=8 to read the remaining 4 provider(s)" in visible


def test_catalog_surface_exposes_invalid_managed_status_without_raw_overlay_details() -> None:
    visible = _render_config_broker_surface(
        {
            "ok": True,
            "mode": "catalog_models",
            "total": 0,
            "providers": [],
            "managedCatalogStatus": {
                "ok": False,
                "state": "invalid",
                "errorCode": "managed_catalog_invalid",
                "error": "Managed catalog overlay is invalid and was not applied.",
                "path": r"C:\Users\private\.v8-agent-os\model_provider_catalog.managed.json",
                "raw": '{"providers":[{"baseUrl":"https://private.example/v1","apiKey":"sk-secret"}',
            },
        },
        "toolobs://config-broker-invalid-catalog",
    )

    assert "Managed catalog blocked: Managed catalog overlay is invalid and was not applied." in visible
    assert "managed_catalog_invalid" not in visible
    assert r"C:\Users\private" not in visible
    assert "private.example" not in visible
    assert "sk-secret" not in visible
    assert '"providers"' not in visible
    assert '"baseUrl"' not in visible
    assert not visible.lstrip().startswith("{")
