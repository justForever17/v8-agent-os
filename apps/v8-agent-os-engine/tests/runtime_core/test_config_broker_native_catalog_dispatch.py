from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config_file(tmp_path, monkeypatch) -> None:
    from core.database import DatabaseManager
    import core.config_broker_service as broker_module
    import core.storage as storage_module

    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(broker_module, "db", DatabaseManager(tmp_path / "state.db"))


def _invoke_config_broker(payload: dict[str, Any]) -> dict[str, Any]:
    from core.tools.native.mcp import config_broker

    return json.loads(config_broker.invoke(payload))


def _install_runtime_identity(monkeypatch) -> None:
    import core.tools.native.mcp as module

    monkeypatch.setattr(
        module,
        "get_runtime_context",
        lambda: {
            "userId": "owner-catalog",
            "session_id": "session-catalog",
            "runId": "run-catalog",
        },
    )


def test_catalog_models_forwards_inventory_filters_exactly(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _catalog_inventory(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "catalog_models"}

    monkeypatch.setattr(module.config_broker_service, "catalog_inventory", _catalog_inventory)

    result = _invoke_config_broker(
        {
            "mode": "catalog_models",
            "provider_id": "provider-a",
            "query": "vision",
            "limit": 37,
            "offset": 11,
        }
    )

    assert result == {"ok": True, "mode": "catalog_models"}
    assert calls == [
        {
            "provider_id": "provider-a",
            "query": "vision",
            "limit": 37,
            "offset": 11,
        }
    ]


def test_catalog_discover_forwards_provider_query_and_pagination_exactly(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _catalog_discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "catalog_discover"}

    monkeypatch.setattr(module.config_broker_service, "catalog_discover", _catalog_discover)

    result = _invoke_config_broker(
        {
            "mode": "catalog_discover",
            "provider_id": "provider-b",
            "query": "new-model",
            "limit": 23,
            "offset": 41,
        }
    )

    assert result == {"ok": True, "mode": "catalog_discover"}
    assert calls == [
        {
            "provider_id": "provider-b",
            "query": "new-model",
            "limit": 23,
            "offset": 41,
        }
    ]


def test_catalog_connect_prepare_forwards_contract_and_runtime_identity_exactly(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _prepare_catalog_model(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "catalog_connect_prepare"}

    monkeypatch.setattr(module.config_broker_service, "prepare_catalog_model", _prepare_catalog_model)

    result = _invoke_config_broker(
        {
            "mode": "catalog_connect_prepare",
            "provider_id": "provider-c",
            "model_id": "models/model-c",
            "channel_id": "responses",
            "wire_protocol": "openai.responses",
            "discover_if_needed": False,
        }
    )

    assert result == {"ok": True, "mode": "catalog_connect_prepare"}
    assert calls == [
        {
            "provider_id": "provider-c",
            "model_id": "models/model-c",
            "channel_id": "responses",
            "wire_protocol": "openai.responses",
            "discover_if_needed": False,
            "owner_id": "owner-catalog",
            "session_id": "session-catalog",
            "run_id": "run-catalog",
        }
    ]


def test_model_snapshot_recovery_prepare_forwards_only_a_durable_source_transaction(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _prepare_model_snapshot_recovery(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "model_snapshot_recover_prepare"}

    monkeypatch.setattr(
        module.config_broker_service,
        "prepare_model_snapshot_recovery",
        _prepare_model_snapshot_recovery,
    )

    result = _invoke_config_broker(
        {
            "mode": "model_snapshot_recover_prepare",
            "source_transaction_id": "cfg_txn_durable_source",
        }
    )

    assert result == {"ok": True, "mode": "model_snapshot_recover_prepare"}
    assert calls == [
        {
            "source_transaction_id": "cfg_txn_durable_source",
            "owner_id": "owner-catalog",
            "session_id": "session-catalog",
            "run_id": "run-catalog",
        }
    ]


def test_catalog_provider_prepare_forwards_preset_evidence_and_runtime_identity_exactly(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _prepare_catalog_provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "catalog_provider_prepare"}

    monkeypatch.setattr(module.config_broker_service, "prepare_catalog_provider", _prepare_catalog_provider)
    preset = {
        "id": "provider-d",
        "baseUrl": "https://api.provider-d.test/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key", "header": "Authorization"},
        "channels": [
            {
                "id": "responses",
                "baseUrl": "https://api.provider-d.test/v1",
                "apiStandard": "openai",
                "wireProtocols": ["openai.responses"],
                "defaultWireProtocol": "openai.responses",
            }
        ],
        "models": [
            {
                "id": "model-d",
                "contextWindow": 256_000,
                "maxOutputTokens": 16_000,
                "capabilities": ["text", "reasoning"],
                "reasoningSurface": {
                    "mode": "reasoning_summary",
                    "trust": "official",
                    "requestStyle": "openai_reasoning",
                    "responseFields": ["reasoning.summary"],
                    "displayKind": "summary",
                },
                "metadata": {"releaseTrack": "stable"},
            }
        ],
    }
    evidence_refs = ["https://docs.provider-d.test/models"]

    result = _invoke_config_broker(
        {
            "mode": "catalog_provider_prepare",
            "provider_preset": preset,
            "evidence_refs": evidence_refs,
        }
    )

    assert result == {"ok": True, "mode": "catalog_provider_prepare"}
    assert calls == [
        {
            "provider_preset": preset,
            "evidence_refs": evidence_refs,
            "owner_id": "owner-catalog",
            "session_id": "session-catalog",
            "run_id": "run-catalog",
        }
    ]


def test_model_prepare_forwards_public_patch_names_and_runtime_identity(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _prepare_model(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "model_prepare"}

    monkeypatch.setattr(module.config_broker_service, "prepare_model", _prepare_model)
    provider_config = {
        "channels": [{"id": "responses", "wireProtocols": ["openai.responses"]}],
        "timeoutMs": 45_000,
    }
    model_settings = {
        "reasoningEffortControl": {"levels": ["low", "high"]},
        "parameterProfile": {"reasoning": {"effort": "high"}},
    }

    result = _invoke_config_broker(
        {
            "mode": "model_prepare",
            "provider_id": "provider-e",
            "provider_name": "Provider E",
            "model_id": "model-e",
            "base_url": "https://api.provider-e.test/v1",
            "api_standard": "openai",
            "model_type": "TEXT",
            "context_window": 128_000,
            "max_tokens": 8_192,
            "capabilities": {"chat": True, "reasoning": True},
            "evidence_refs": ["https://docs.provider-e.test/model-e"],
            "provider_config": provider_config,
            "model_settings": model_settings,
        }
    )

    assert result == {"ok": True, "mode": "model_prepare"}
    assert len(calls) == 1
    assert calls[0]["provider_config"] == provider_config
    assert calls[0]["model_config"] == model_settings
    assert calls[0]["owner_id"] == "owner-catalog"
    assert calls[0]["session_id"] == "session-catalog"
    assert calls[0]["run_id"] == "run-catalog"


def test_config_broker_schema_imports_with_public_patch_names_only() -> None:
    from core.tools.native.mcp import config_broker

    schema = config_broker.args_schema.model_json_schema()
    properties = dict(schema.get("properties") or {})

    assert {
        "provider_config",
        "model_settings",
        "provider_preset",
        "discover_if_needed",
        "provider_operation",
        "request_secret",
        "source_provider_id",
        "source_model_id",
        "binding_source",
        "replace_provider_models",
        "record_operation",
        "governance",
        "routing_policies",
        "role_parameters",
        "source_transaction_id",
    } <= set(properties)
    assert "model_config" not in properties
    assert "credential_required" not in properties
    model_settings = dict((schema.get("$defs") or {})["ConfigBrokerModelSettings"].get("properties") or {})
    provider_config = dict((schema.get("$defs") or {})["ConfigBrokerProviderConfig"].get("properties") or {})
    assert "factProvenance" not in model_settings
    assert "metadata" not in model_settings
    assert "oauthRef" not in provider_config


def test_config_broker_schema_declares_modes_and_forbids_unknown_config_fields() -> None:
    from core.tools.native.mcp import config_broker

    schema = config_broker.args_schema.model_json_schema()
    definitions = dict(schema.get("$defs") or {})

    assert schema.get("additionalProperties") is False
    assert set(schema["properties"]["mode"]["enum"]) == {
        "models",
        "model_list",
        "inventory",
        "role_matrix",
        "recommend",
        "catalog_models",
        "catalog_discover",
        "catalog_connect_prepare",
        "catalog_provider_prepare",
        "catalog_provider_remove_prepare",
        "catalog_custom_provider_remove_prepare",
        "catalog_recover_prepare",
        "catalog_recover_finalize_prepare",
        "model_snapshot_recover_prepare",
        "model_provider_prepare",
        "model_binding_prepare",
        "model_default_prepare",
        "model_prepare",
        "role_prepare",
        "role_unbind_prepare",
        "model_record_prepare",
        "model_policy_prepare",
        "media_operation_prepare",
        "commit",
        "status",
        "rollback",
        "mcp_list",
        "mcp_status",
        "mcp_prepare_install",
        "mcp_prepare_remove",
    }
    for definition_name in (
        "ConfigBrokerProviderConfig",
        "ConfigBrokerChannel",
        "ConfigBrokerAuthContract",
        "ConfigBrokerModelSettings",
        "ConfigBrokerReasoningSurface",
        "ConfigBrokerThinkingControl",
        "ConfigBrokerReasoningEffortControl",
        "ConfigBrokerProviderPreset",
        "ConfigBrokerCatalogModel",
        "ConfigBrokerCapabilityEntry",
        "ConfigBrokerGovernance",
        "ConfigBrokerBudgetPolicy",
        "ConfigBrokerCredentialRequirement",
        "ConfigBrokerRoleParameters",
    ):
        assert definitions[definition_name].get("additionalProperties") is False


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "not-a-config-mode"},
        {"mode": "model_provider_prepare", "provider_operation": "replace"},
        {"mode": "models", "unexpected": True},
        {"mode": "model_prepare", "provider_config": {"unexpected": True}},
        {"mode": "model_provider_prepare", "provider_config": {"oauthRef": "oauth:private-ref"}},
        {
            "mode": "model_prepare",
            "provider_config": {"channels": [{"id": "responses", "unexpected": True}]},
        },
        {
            "mode": "model_prepare",
            "model_settings": {"reasoningEffortControl": {"levels": ["low"], "unexpected": True}},
        },
        {
            "mode": "catalog_provider_prepare",
            "provider_preset": {
                "id": "provider-a",
                "auth": {"type": "api_key", "unexpected": True},
            },
        },
    ],
)
def test_config_broker_schema_rejects_unknown_modes_and_fields_before_dispatch(payload: dict[str, Any]) -> None:
    from core.tools.native.mcp import config_broker

    result = json.loads(config_broker.invoke(payload))

    assert result["state"] == "blocked"
    assert result["error"]["code"] == "config_broker_input_invalid"


def test_config_broker_validation_error_never_echoes_secret_bearing_input() -> None:
    from core.tools.native.mcp import config_broker

    serialized = config_broker.invoke(
        {
            "mode": "models",
            "api_key": "sk-must-never-enter-validation-surface",
        }
    )

    assert "must-never" not in serialized
    assert "api_key" not in serialized
    assert json.loads(serialized)["error"]["code"] == "config_broker_input_invalid"


def test_config_broker_unknown_service_exception_never_exposes_exception_text(monkeypatch) -> None:
    import core.tools.native.mcp as module

    def _catalog_inventory(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("apiKey=sk-must-never-enter-agent-surface")

    monkeypatch.setattr(module.config_broker_service, "catalog_inventory", _catalog_inventory)

    result = _invoke_config_broker({"mode": "catalog_models"})
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["error"]["code"] == "config_broker_failed"
    assert result["state"] == "failed"
    assert "must-never" not in serialized
    assert "apiKey" not in serialized


def test_config_broker_controlled_service_exception_never_exposes_exception_text(monkeypatch) -> None:
    import core.tools.native.mcp as module

    def _catalog_inventory(**_kwargs: Any) -> dict[str, Any]:
        raise module.ConfigBrokerError(
            "Authorization: Bearer must-never-enter-human-surface",
            code="catalog_discovery_failed",
        )

    monkeypatch.setattr(module.config_broker_service, "catalog_inventory", _catalog_inventory)

    result = _invoke_config_broker({"mode": "catalog_models"})
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["error"]["code"] == "catalog_discovery_failed"
    assert result["state"] == "blocked"
    assert "must-never" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized


def test_model_provider_prepare_forwards_secret_free_contract_and_runtime_identity(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _prepare_model_provider_change(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "model_provider_prepare"}

    monkeypatch.setattr(
        module.config_broker_service,
        "prepare_model_provider_change",
        _prepare_model_provider_change,
    )

    result = _invoke_config_broker(
        {
            "mode": "model_provider_prepare",
            "provider_id": "provider-native",
            "provider_operation": "upsert",
            "request_secret": True,
            "provider_config": {
                "name": "Native Provider",
                "baseUrl": "https://native-provider.example/v1",
                "apiStandard": "openai",
                "authContract": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
            },
        }
    )

    assert result == {"ok": True, "mode": "model_provider_prepare"}
    assert calls == [
        {
            "provider_id": "provider-native",
            "operation": "upsert",
            "provider_config": {
                "name": "Native Provider",
                "baseUrl": "https://native-provider.example/v1",
                "apiStandard": "openai",
                "authContract": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
            },
            "request_secret": True,
            "oauth_credential": "",
            "owner_id": "owner-catalog",
            "session_id": "session-catalog",
            "run_id": "run-catalog",
        }
    ]


def test_model_binding_prepare_forwards_typed_model_contract_and_runtime_identity(monkeypatch) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _prepare_model_binding(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": "model_binding_prepare"}

    monkeypatch.setattr(module.config_broker_service, "prepare_model_binding", _prepare_model_binding)

    result = _invoke_config_broker(
        {
            "mode": "model_binding_prepare",
            "provider_id": "provider-target",
            "model_id": "model-target",
            "source_provider_id": "provider-source",
            "source_model_id": "model-source",
            "binding_source": "catalog",
            "replace_provider_models": True,
            "model_settings": {
                "type": "TEXT",
                "capabilities": {"chat": True, "reasoning": True},
            },
        }
    )

    assert result == {"ok": True, "mode": "model_binding_prepare"}
    assert calls == [
        {
            "provider_id": "provider-target",
            "model_id": "model-target",
            "model_config": {
                "type": "TEXT",
                "capabilities": {"chat": True, "reasoning": True},
            },
            "source_provider_id": "provider-source",
            "source_model_id": "model-source",
            "source": "catalog",
            "replace_provider_models": True,
            "owner_id": "owner-catalog",
            "session_id": "session-catalog",
            "run_id": "run-catalog",
        }
    ]


@pytest.mark.parametrize(
    ("mode", "method_name", "request_payload", "expected"),
    [
        (
            "catalog_provider_remove_prepare",
            "prepare_catalog_provider_removal",
            {"provider_id": "provider-remove"},
            {"provider_id": "provider-remove"},
        ),
        (
            "catalog_custom_provider_remove_prepare",
            "prepare_custom_catalog_provider_removal",
            {"provider_id": "custom-remove"},
            {"provider_id": "custom-remove"},
        ),
        (
            "model_provider_prepare",
            "prepare_model_provider_change",
            {"provider_id": "provider-remove", "provider_operation": "remove"},
            {
                "provider_id": "provider-remove",
                "operation": "remove",
                "provider_config": None,
                "request_secret": False,
                "oauth_credential": "",
            },
        ),
        (
            "catalog_recover_prepare",
            "prepare_catalog_recovery",
            {},
            {},
        ),
        (
            "catalog_recover_finalize_prepare",
            "prepare_catalog_recovery_finalize",
            {},
            {},
        ),
        (
            "role_unbind_prepare",
            "prepare_role_unbind",
            {"role": "agent:reviewer"},
            {"role": "agent:reviewer"},
        ),
        (
            "model_default_prepare",
            "prepare_model_default",
            {"model_ref": "provider::model", "category": "text_generation"},
            {"model_ref": "provider::model", "category": "text_generation"},
        ),
        (
            "model_record_prepare",
            "prepare_model_record_change",
            {"model_ref": "provider::model", "record_operation": "disable"},
            {"model_ref": "provider::model", "operation": "disable"},
        ),
        (
            "model_policy_prepare",
            "prepare_model_policy",
            {
                "governance": {"maxLocalRetries": 2},
                "routing_policies": {"chat": "supervisor"},
                "role_parameters": {"supervisor": {"temperature": 0.3}},
            },
            {
                "governance": {"maxLocalRetries": 2},
                "routing_policies": {"chat": "supervisor"},
                "role_parameters": {"supervisor": {"temperature": 0.3}},
            },
        ),
    ],
)
def test_config_lifecycle_modes_forward_runtime_identity(
    monkeypatch,
    mode: str,
    method_name: str,
    request_payload: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    import core.tools.native.mcp as module

    calls: list[dict[str, Any]] = []
    _install_runtime_identity(monkeypatch)

    def _handler(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "mode": mode}

    monkeypatch.setattr(module.config_broker_service, method_name, _handler, raising=False)

    result = _invoke_config_broker({"mode": mode, **request_payload})

    assert result == {"ok": True, "mode": mode}
    assert calls == [
        {
            **expected,
            "owner_id": "owner-catalog",
            "session_id": "session-catalog",
            "run_id": "run-catalog",
        }
    ]


@pytest.mark.parametrize(
    ("provider_config", "model_config"),
    [
        (
            {
                "channels": [
                    {
                        "id": "responses",
                        "authContract": {"nested": {"Authorization": "Bearer must-not-flow"}},
                    }
                ]
            },
            None,
        ),
        (
            None,
            {"parameterProfile": {"credentials": [{"apiKey": "must-not-flow"}]}},
        ),
        (
            None,
            {"sourceRefs": ["https://docs.example.test/model?accessToken=must-not-flow"]},
        ),
    ],
)
def test_service_rejects_secret_fields_nested_in_model_patches(
    monkeypatch,
    provider_config: dict[str, Any] | None,
    model_config: dict[str, Any] | None,
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", lambda: {"providers": {}})

    with pytest.raises(module.ConfigBrokerError) as blocked:
        module.ConfigBrokerService().prepare_model(
            provider_id="provider-secret-test",
            model_id="model-secret-test",
            provider_name="Secret Test",
            base_url="https://api.secret-test.invalid/v1",
            api_standard="openai",
            model_type="TEXT",
            context_window=128_000,
            max_tokens=8_192,
            capabilities={"chat": True},
            evidence_refs=[],
            credential_required=False,
            owner_id="owner-catalog",
            session_id="session-catalog",
            run_id="run-catalog",
            provider_config=provider_config,
            model_config=model_config,
        )

    assert blocked.value.code == "config_secret_in_patch"
    assert "must-not-flow" not in str(blocked.value)
