from __future__ import annotations

import json

import pytest

from core.database import DatabaseManager
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend, resolve_config_credential_refs


@pytest.fixture(autouse=True)
def _isolate_user_config_files(tmp_path, monkeypatch) -> None:
    import core.storage as storage_module

    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(storage_module, "MCP_JSON_PATH", tmp_path / "mcp.json")


def test_model_prepare_blocks_incomplete_facts_and_persists_recovery_record(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    monkeypatch.setattr(module.storage, "get_models_config", lambda: {})
    service = module.ConfigBrokerService()

    result = service.prepare_model(
        provider_id="example",
        model_id="example-chat",
        provider_name="Example",
        base_url="https://api.example.test/v1",
        api_standard="openai",
        model_type="TEXT",
        context_window=262_144,
        max_tokens=None,
        capabilities={"chat": True},
        evidence_refs=["https://docs.example.test/models"],
        credential_required=True,
        owner_id="owner@example.test",
        session_id="",
        run_id="run-test",
    )

    assert result["ok"] is False
    assert result["state"] == "blocked"
    assert result["requiredFacts"] == ["maxTokens"]
    persisted = service.get_transaction(result["transactionId"], owner_id="owner@example.test")
    assert persisted["state"] == "blocked"
    assert "api" not in json.dumps(persisted).lower()


def test_ui_action_persists_schema_not_secret_and_is_one_time(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as config_module
    import core.ui_action_requests as action_module

    test_db = DatabaseManager(tmp_path / "state.db")
    memory_store = CredentialRefStore(MemoryCredentialBackend())
    monkeypatch.setattr(action_module, "db", test_db)
    monkeypatch.setattr(action_module, "credential_ref_store", memory_store)
    monkeypatch.setattr(
        config_module.config_broker_service,
        "attach_credentials_and_commit",
        lambda transaction_id, bindings, owner_id: {
            "ok": True,
            "state": "committed",
            "summary": "configured",
        },
    )
    service = action_module.UiActionRequestService()
    action = service.create(
        kind="secret_input",
        owner_id="owner@example.test",
        session_id="session-test",
        run_id="run-test",
        title="Connect provider",
        description="Exact target shown",
        target_label="https://api.example.test/v1",
        fields=[
            {
                "id": "apiKey",
                "kind": "secret",
                "label": "API Key",
                "required": True,
                "binding": {"namespace": "model", "target": "provider", "targetName": "api_key"},
            }
        ],
        handler_type="config_broker_secret",
        handler_ref="cfg_txn_test",
    )

    submitted = service.submit(
        action["actionRequestId"],
        values={"apiKey": "top-secret-value"},
        owner_id="owner@example.test",
        session_id="session-test",
    )
    assert submitted["state"] == "submitted"
    with test_db.get_connection() as conn:
        row = dict(conn.execute("SELECT * FROM ui_action_requests WHERE id=?", (action["actionRequestId"],)).fetchone())
    assert "top-secret-value" not in json.dumps(row)

    with pytest.raises(action_module.UiActionRequestError) as duplicate:
        service.submit(
            action["actionRequestId"],
            values={"apiKey": "another-secret"},
            owner_id="owner@example.test",
            session_id="session-test",
        )
    assert duplicate.value.code == "ui_action_already_terminal"


def test_ui_action_preserves_credential_after_broker_takes_ownership(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as config_module
    import core.ui_action_requests as action_module

    test_db = DatabaseManager(tmp_path / "state.db")
    memory_store = CredentialRefStore(MemoryCredentialBackend())
    captured_bindings = []
    monkeypatch.setattr(action_module, "db", test_db)
    monkeypatch.setattr(action_module, "credential_ref_store", memory_store)

    def commit_with_credentials(transaction_id, *, bindings, owner_id):
        captured_bindings.extend(bindings)
        return {"ok": True, "state": "committed", "summary": "configured"}

    monkeypatch.setattr(
        config_module.config_broker_service,
        "attach_credentials_and_commit",
        commit_with_credentials,
    )
    service = action_module.UiActionRequestService()
    action = service.create(
        kind="secret_input",
        owner_id="owner@example.test",
        session_id="session-test",
        run_id="run-test",
        title="Connect provider",
        description="Exact target shown",
        target_label="https://api.example.test/v1",
        fields=[
            {
                "id": "apiKey",
                "kind": "secret",
                "label": "API Key",
                "required": True,
                "binding": {"namespace": "model", "target": "provider", "targetName": "api_key"},
            }
        ],
        handler_type="config_broker_secret",
        handler_ref="cfg_txn_test",
    )

    def fail_public_projection(action_id, *, owner_id, session_id):
        raise RuntimeError("result projection failed")

    monkeypatch.setattr(service, "public", fail_public_projection)
    with pytest.raises(action_module.UiActionRequestError) as failed:
        service.submit(
            action["actionRequestId"],
            values={"apiKey": "broker-owned-secret"},
            owner_id="owner@example.test",
            session_id="session-test",
        )

    assert failed.value.code == "ui_action_submit_failed"
    assert len(captured_bindings) == 1
    credential_ref = captured_bindings[0]["secretRef"]
    assert memory_store.resolve(credential_ref) == "broker-owned-secret"
    with test_db.get_connection() as conn:
        row = conn.execute(
            "SELECT state FROM ui_action_requests WHERE id=?",
            (action["actionRequestId"],),
        ).fetchone()
    assert row["state"] == "submitted"


def test_ui_action_claim_prevents_concurrent_secret_submission(tmp_path, monkeypatch) -> None:
    import core.ui_action_requests as action_module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(action_module, "db", test_db)
    service = action_module.UiActionRequestService()
    action = service.create(
        kind="secret_input",
        owner_id="owner@example.test",
        session_id="session-test",
        run_id="run-test",
        title="Connect provider",
        description="Exact target shown",
        target_label="https://api.example.test/v1",
        fields=[{"id": "apiKey", "kind": "secret", "label": "API Key", "required": True}],
        handler_type="config_broker_secret",
        handler_ref="cfg_txn_test",
    )
    with test_db.get_connection() as conn:
        conn.execute(
            "UPDATE ui_action_requests SET submitted_at=? WHERE id=?",
            ("2026-07-22T00:00:00Z", action["actionRequestId"]),
        )
        conn.commit()

    with pytest.raises(action_module.UiActionRequestError) as concurrent:
        service.submit(
            action["actionRequestId"],
            values={"apiKey": "never-stored"},
            owner_id="owner@example.test",
            session_id="session-test",
        )
    assert concurrent.value.code == "ui_action_submit_in_progress"

    with pytest.raises(action_module.UiActionRequestError) as wrong_session:
        service.public(
            action["actionRequestId"],
            owner_id="owner@example.test",
            session_id="another-session",
        )
    assert wrong_session.value.code == "ui_action_session_mismatch"


def test_ui_action_rejects_unknown_owner_or_runtime_scope(tmp_path, monkeypatch) -> None:
    import core.ui_action_requests as action_module

    monkeypatch.setattr(action_module, "db", DatabaseManager(tmp_path / "state.db"))
    service = action_module.UiActionRequestService()
    with pytest.raises(action_module.UiActionRequestError) as missing_owner:
        service.create(
            kind="secret_input",
            owner_id="",
            session_id="session-test",
            run_id="run-test",
            title="Connect provider",
            description="",
            target_label="https://api.example.test/v1",
            fields=[],
            handler_type="config_broker_secret",
            handler_ref="cfg_txn_test",
        )
    assert missing_owner.value.code == "ui_action_owner_required"


def test_inventory_groups_vision_and_surfaces_open_circuit(monkeypatch) -> None:
    import core.config_broker_service as module

    models = [
        {
            "modelRef": "p:text",
            "modelId": "text",
            "providerId": "p",
            "providerName": "Provider",
            "type": "TEXT",
            "capabilityClass": "chat_general",
            "contextWindow": 262_144,
            "maxTokens": 8_192,
        },
        {
            "modelRef": "v:vision",
            "modelId": "vision",
            "providerId": "v",
            "providerName": "Vision Provider",
            "type": "MULTIMODAL",
            "capabilityClass": "vision_multimodal",
            "contextWindow": 262_144,
            "maxTokens": 8_192,
            "capabilities": {"vision": True},
        },
    ]
    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: {})
    monkeypatch.setattr(module.model_control_plane, "list_models", lambda _config: models)
    monkeypatch.setattr(
        module.model_control_plane,
        "get_provider_statuses",
        lambda _config: [
            {"providerId": "p", "status": "attention", "circuitState": "open", "errorCount": 4},
            {"providerId": "v", "status": "healthy", "circuitState": "closed", "errorCount": 0},
        ],
    )

    payload = module.ConfigBrokerService().inventory()
    assert payload["groups"] == [{"category": "text", "count": 1}, {"category": "vision", "count": 1}]
    assert payload["models"][0]["category"] == "vision"
    unhealthy = next(item for item in payload["models"] if item["modelId"] == "text")
    assert unhealthy["status"] == "unhealthy"


def test_recommendation_never_treats_untyped_extension_role_as_any_model(monkeypatch) -> None:
    import core.config_broker_service as module

    config = {"roles": {"channel": "chat-provider::chat-model"}}
    models = [
        {
            "modelRef": "chat-provider::chat-model",
            "modelId": "chat-model",
            "providerName": "Chat Provider",
            "capabilityClass": "chat_general",
            "eligibility": {"selectable": True, "shortLabel": "可用"},
        },
        {
            "modelRef": "vision-provider::vision-model",
            "modelId": "vision-model",
            "providerName": "Vision Provider",
            "capabilityClass": "vision_multimodal",
            "eligibility": {"selectable": True, "shortLabel": "可用"},
        },
        {
            "modelRef": "embedding-provider::embedding-model",
            "modelId": "embedding-model",
            "providerName": "Embedding Provider",
            "capabilityClass": "embedding",
            "eligibility": {"selectable": True, "shortLabel": "可用"},
        },
        {
            "modelRef": "media-provider::image-model",
            "modelId": "image-model",
            "providerName": "Media Provider",
            "capabilityClass": "media_generation",
            "eligibility": {"selectable": True, "shortLabel": "可用"},
        },
    ]
    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: config)
    monkeypatch.setattr(module.model_control_plane, "list_models", lambda _config: models)

    payload = module.ConfigBrokerService().recommend(role="channel", limit=10)

    assert {item["modelId"] for item in payload["candidates"]} == {"chat-model", "vision-model"}


def test_role_prepare_creates_committable_transaction_without_ui_action_scope(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: {})
    monkeypatch.setattr(
        module.model_control_plane,
        "get_role_definitions",
        lambda _config: {"default": {"label": "Default", "capabilityClasses": ["chat_general"]}},
    )
    monkeypatch.setattr(
        module.model_control_plane,
        "get_model_record",
        lambda _model_ref, _config: {"model_ref": "provider::chat", "model_id": "chat"},
    )
    monkeypatch.setattr(
        module.model_control_plane,
        "list_models",
        lambda _config: [
            {
                "modelRef": "provider::chat",
                "capabilityClass": "chat_general",
                "eligibility": {"selectable": True, "shortLabel": "可用"},
            }
        ],
    )
    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", lambda: {})

    prepared = module.ConfigBrokerService().prepare_role_assignment(
        role="default",
        model_ref="provider::chat",
        owner_id="local-cli",
        session_id="",
        run_id="",
    )

    assert prepared["state"] == "ready_to_commit"
    with test_db.get_connection() as conn:
        row = dict(conn.execute("SELECT target_kind,target_id FROM config_broker_transactions").fetchone())
    assert row == {"target_kind": "model_role", "target_id": "default"}


def test_registered_subagent_binding_commits_and_uses_subagent_contract(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    config = {"roles": {"subagent": "provider::default"}, "bindings": {"agents": {}}}
    monkeypatch.setattr(module, "db", test_db)
    monkeypatch.setattr(module.storage, "get_agent", lambda agent_id: {"id": agent_id, "name": "Reviewer"})
    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: json.loads(json.dumps(config)))
    monkeypatch.setattr(
        module.model_control_plane,
        "get_role_definitions",
        lambda _config: {"subagent": {"label": "Subagent", "capabilityClasses": ["chat_general"]}},
    )
    monkeypatch.setattr(
        module.model_control_plane,
        "get_model_record",
        lambda _model_ref, _config: {"model_ref": "provider::reviewer", "model_id": "reviewer"},
    )
    monkeypatch.setattr(
        module.model_control_plane,
        "list_models",
        lambda _config: [
            {
                "modelRef": "provider::reviewer",
                "capabilityClass": "chat_general",
                "eligibility": {"selectable": True, "shortLabel": "可用"},
            }
        ],
    )
    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", lambda: json.loads(json.dumps(config)))
    def _mutate(mutator):
        proposed = mutator(json.loads(json.dumps(config)))
        config.clear()
        config.update(json.loads(json.dumps(proposed)))
        return json.loads(json.dumps(config))

    monkeypatch.setattr(module.model_control_plane, "mutate_config", _mutate)
    service = module.ConfigBrokerService()

    prepared = service.prepare_role_assignment(
        role="agent:reviewer",
        model_ref="provider::reviewer",
        owner_id="owner@example.test",
        session_id="session-test",
        run_id="run-test",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner@example.test")

    assert committed["state"] == "committed"
    assert config["bindings"]["agents"]["reviewer"] == {"model_id": "provider::reviewer"}


def test_media_operation_transaction_is_scoped_recoverable_and_requires_ready_projection(
    tmp_path,
    monkeypatch,
) -> None:
    import core.config_broker_service as module
    import runtimes.creative_media.runtime as creative_module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    model_ref = "minimax-cn::v2/video_generation/MiniMax-H3"
    state = {
        "version": 1,
        "updatedAt": "before",
        "selections": [
            {
                "operationKind": "image.generate",
                "modelRefs": ["images::configured"],
                "enabled": True,
                "priority": 10,
                "updatedAt": "before",
            },
            {
                "operationKind": "video.reference_to_video",
                "modelRefs": [model_ref],
                "enabled": False,
                "priority": 390,
                "updatedAt": "before",
            },
        ],
        "models": [
            {
                "candidateId": "image-candidate",
                "operationKind": "image.generate",
                "modelRef": "images::configured",
                "enabled": True,
                "priority": 10,
                "updatedAt": "before",
            },
            {
                "candidateId": "h3-candidate",
                "modality": "video",
                "operationKind": "video.reference_to_video",
                "providerId": "minimax-cn",
                "modelId": "v2/video_generation/MiniMax-H3",
                "modelRef": model_ref,
                "adapter": "minimax_video",
                "enabled": False,
                "priority": 390,
                "updatedAt": "before",
            },
        ],
    }

    def _read_json(_filename):
        return json.loads(json.dumps(state))

    def _mutate_json(_filename, mutator):
        proposed = mutator(json.loads(json.dumps(state)))
        state.clear()
        state.update(json.loads(json.dumps(proposed)))
        return json.loads(json.dumps(state))

    def _preferences():
        selection = next(
            item
            for item in state["selections"]
            if item["operationKind"] == "video.reference_to_video"
        )
        enabled = bool(selection["enabled"])
        candidate = {
            "candidateId": "h3-candidate",
            "modality": "video",
            "operationKind": "video.reference_to_video",
            "providerId": "minimax-cn",
            "modelId": "v2/video_generation/MiniMax-H3",
            "modelRef": model_ref,
            "adapter": "minimax_video",
            "source": "model_control_plane",
            "available": True,
            "enabled": enabled,
            "priority": 390,
            "readiness": {"executable": True, "reasonCodes": []},
        }
        return {
            "connectedOptions": [candidate],
            "operationRows": [
                {
                    "operationKind": "video.reference_to_video",
                    "enabled": enabled,
                    "selectedModelRefs": [model_ref],
                }
            ],
            "executionProjection": {
                "video.reference_to_video": {
                    "status": "ready" if enabled else "blocked",
                    "configuredModelRefs": [model_ref],
                }
            },
        }

    monkeypatch.setattr(module.storage, "read_json", _read_json)
    monkeypatch.setattr(module.storage, "mutate_json", _mutate_json)
    monkeypatch.setattr(creative_module.creative_media_runtime, "get_model_preferences", _preferences)
    service = module.ConfigBrokerService()

    prepared = service.prepare_media_operation(
        operation_kind="video.reference_to_video",
        model_ref="minimax-cn::v2%2Fvideo_generation%2FMiniMax-H3",
        enabled=True,
        priority=None,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert committed["result"] == {
        "operationKind": "video.reference_to_video",
        "modelRef": model_ref,
        "enabled": True,
        "priority": 390,
        "executionStatus": "ready",
    }
    assert next(item for item in state["selections"] if item["operationKind"] == "image.generate")["modelRefs"] == [
        "images::configured"
    ]
    assert next(
        item for item in state["selections"] if item["operationKind"] == "video.reference_to_video"
    )["enabled"] is True

    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")

    assert rolled_back["state"] == "rolled_back"
    assert next(
        item for item in state["selections"] if item["operationKind"] == "video.reference_to_video"
    )["enabled"] is False
    assert next(item for item in state["selections"] if item["operationKind"] == "image.generate")["modelRefs"] == [
        "images::configured"
    ]


def test_mcp_secret_prepare_rejects_missing_runtime_scope_without_orphan_transaction(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    monkeypatch.setattr(module.storage, "get_mcp_config", lambda: {"mcpServers": {}})
    service = module.ConfigBrokerService()

    with pytest.raises(module.ConfigBrokerError) as blocked:
        service.prepare_mcp(
            operation="install",
            name="example",
            server={"type": "http", "url": "https://mcp.example.test"},
            credential_requirements=[
                {"id": "token", "target": "header", "targetName": "Authorization", "label": "Token"}
            ],
            owner_id="",
            session_id="",
            run_id="",
        )
    assert blocked.value.code == "config_ui_action_scope_required"
    with test_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM config_broker_transactions").fetchone()[0] == 0


def test_storage_safe_model_snapshot_moves_internal_plaintext_to_credential_store(monkeypatch) -> None:
    import core.model_control_plane as module

    saved: dict = {}
    memory_store = CredentialRefStore(MemoryCredentialBackend())
    raw = {
        "providers": {
            "example": {
                "provider": {
                    "name": "Example",
                    "type": "API",
                    "api_standard": "openai",
                    "base_url": "https://api.example.test/v1",
                    "api_key": "legacy-secret",
                },
                "models": {},
            }
        }
    }
    monkeypatch.setattr(module.storage, "get_models_config", lambda: raw)
    monkeypatch.setattr(module.storage, "save_models_config", lambda value: saved.update(value))

    snapshot = module.ModelControlPlane(credential_store=memory_store).get_storage_safe_config()
    provider = snapshot["providers"]["example"]["provider"]
    assert "legacy-secret" not in json.dumps(snapshot)
    assert provider["credentialRef"].startswith("cred:v8-model:")
    assert memory_store.resolve(provider["credentialRef"]) == "legacy-secret"
    assert "legacy-secret" not in json.dumps(saved)


def test_provider_health_accepts_managed_credential_reference(monkeypatch) -> None:
    from core.provider_health_service import ProviderHealthService

    service = ProviderHealthService()
    monkeypatch.setattr(service, "_health_map", lambda _config: {})
    statuses = service.build_provider_statuses(
        {
            "providers": {
                "example": {
                    "provider": {
                        "name": "Example",
                        "type": "API",
                        "api_standard": "openai",
                        "base_url": "https://api.example.test/v1",
                        "credentialRef": "cred:v8-model:example",
                    },
                    "models": {},
                }
            }
        },
        [{"providerId": "example", "isEnabled": True, "capabilityClass": "chat_general"}],
        {},
    )

    assert statuses[0]["status"] == "healthy"


def test_mcp_snapshot_moves_v8os_plaintext_to_refs_and_restores_runtime_values(monkeypatch) -> None:
    import core.config_broker_service as module

    saved: dict = {}
    memory_store = CredentialRefStore(MemoryCredentialBackend())
    monkeypatch.setattr(module, "credential_ref_store", memory_store)
    monkeypatch.setattr(module.storage, "save_mcp_config", lambda value: saved.update(value))
    safe = module.ConfigBrokerService()._credentialize_mcp_config(
        {
            "mcpServers": {
                "example": {
                    "type": "http",
                    "url": "https://mcp.example.test",
                    "headers": {"Authorization": "Bearer legacy-secret"},
                }
            }
        }
    )

    assert "legacy-secret" not in json.dumps(safe)
    server = safe["mcpServers"]["example"]
    materialized = resolve_config_credential_refs(server, store=memory_store)
    assert materialized["headers"]["Authorization"] == "Bearer legacy-secret"
    assert "legacy-secret" not in json.dumps(saved)


def test_mcp_rollback_refreshes_runtime_inventory(monkeypatch) -> None:
    import core.config_broker_service as module

    calls: list[str] = []
    config = {"mcpServers": {}}

    def _mutate(mutator):
        proposed = mutator(json.loads(json.dumps(config)))
        config.clear()
        config.update(json.loads(json.dumps(proposed)))
        return json.loads(json.dumps(config))

    monkeypatch.setattr(module.storage, "mutate_mcp_config", _mutate)
    monkeypatch.setattr(module, "request_mcp_inventory_refresh", lambda reason: calls.append(reason))

    result = module.ConfigBrokerService()._restore_snapshot(
        {
            "targetKind": "mcp",
            "before": {"mcpServers": {}},
            "proposed": {"newCredentialRefs": []},
        }
    )

    assert result["ok"] is True
    assert calls == ["config_broker_rollback"]


def test_mcp_refresh_failure_rolls_back_only_transaction_target(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "mcpServers": {
            "keep": {"type": "http", "url": "https://keep.example.test/mcp"},
        }
    }

    def _copy_config() -> dict:
        return json.loads(json.dumps(config))

    def _mutate(mutator):
        proposed = mutator(_copy_config())
        config.clear()
        config.update(json.loads(json.dumps(proposed)))
        return _copy_config()

    refresh_calls: list[str] = []

    def _refresh(*, reason: str):
        refresh_calls.append(reason)
        if reason == "config_broker_commit":
            raise RuntimeError("simulated refresh failure")

    monkeypatch.setattr(module.storage, "get_mcp_config", _copy_config)
    monkeypatch.setattr(module.storage, "mutate_mcp_config", _mutate)
    monkeypatch.setattr(module, "request_mcp_inventory_refresh", _refresh)
    service = module.ConfigBrokerService()
    prepared = service.prepare_mcp(
        operation="install",
        name="new",
        server={"type": "http", "url": "https://new.example.test/mcp"},
        credential_requirements=[],
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "rolled_back"
    assert config == {
        "mcpServers": {
            "keep": {"type": "http", "url": "https://keep.example.test/mcp"},
        }
    }
    assert refresh_calls == ["config_broker_commit", "config_broker_rollback"]


def test_config_broker_rejects_secret_bearing_command_arguments() -> None:
    from core.tools.native.mcp import ConfigBrokerError, _reject_secret_bearing_args

    _reject_secret_bearing_args(["-y", "@example/server", "--readonly"])
    with pytest.raises(ConfigBrokerError) as blocked:
        _reject_secret_bearing_args(["--api-key=secret"])
    assert blocked.value.code == "config_secret_in_command_args"


def test_model_config_read_is_pure_and_does_not_run_migrations(monkeypatch) -> None:
    import core.model_control_plane as module

    raw = {"providers": {}, "roles": {}, "bindings": {"agents": {}}}
    monkeypatch.setattr(module.storage, "get_models_config", lambda: raw)
    monkeypatch.setattr(
        module.storage,
        "save_models_config",
        lambda _value: pytest.fail("a model config read must not write"),
    )

    config = module.ModelControlPlane(
        credential_store=CredentialRefStore(MemoryCredentialBackend())
    ).get_config()

    assert config["providers"] == {}


def test_model_prepare_keeps_explicit_channel_and_endpoint_contract(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", lambda: {})
    service = module.ConfigBrokerService()

    prepared = service.prepare_model(
        provider_id="cpm",
        model_id="gpt-5.5",
        provider_name="CPM",
        base_url="https://cpm.example.test/v1",
        api_standard="openai",
        channel_id="openai-responses",
        wire_protocol="openai.responses",
        endpoint_path="responses",
        model_type="MULTIMODAL",
        context_window=1_000_000,
        max_tokens=32_768,
        capabilities={"chat": True, "vision": True, "reasoning": True},
        evidence_refs=["https://cpm.example.test/docs/models"],
        credential_required=False,
        owner_id="local-cli",
        session_id="",
        run_id="",
        provider_config={"authContract": {"type": "none"}},
    )

    transaction = service.get_transaction(
        prepared["transactionId"],
        owner_id="local-cli",
        include_private=True,
    )
    assert transaction["proposed"]["model"]["endpointBinding"] == {
        "version": 2,
        "route": "gpt-5.5",
        "channelId": "openai-responses",
        "wireProtocol": "openai.responses",
        "endpointPath": "responses",
        "providerModelId": "gpt-5.5",
        "protocolSource": "config_broker_explicit",
        "provenance": {
            "source": "config_broker_explicit",
            "confidence": "authoritative",
        },
    }


def _install_role_control_plane_fakes(module, monkeypatch, config: dict):
    def _copy_config():
        return json.loads(json.dumps(config))

    def _mutate(mutator):
        proposed = mutator(_copy_config())
        config.clear()
        config.update(json.loads(json.dumps(proposed)))
        return _copy_config()

    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: _copy_config())
    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", lambda: _copy_config())
    monkeypatch.setattr(
        module.model_control_plane,
        "get_role_definitions",
        lambda _config: {"default": {"label": "Default", "capabilityClasses": ["chat_general"]}},
    )
    monkeypatch.setattr(
        module.model_control_plane,
        "get_model_record",
        lambda _ref, _config: {
            "model_ref": "provider::new",
            "model_id": "new",
            "model": {"type": "TEXT", "capabilityClass": "chat_general"},
        },
    )
    monkeypatch.setattr(
        module.model_control_plane,
        "list_models",
        lambda _config: [
            {
                "modelRef": "provider::new",
                "type": "TEXT",
                "capabilityClass": "chat_general",
                "eligibility": {"selectable": True, "shortLabel": "可用"},
            }
        ],
    )
    monkeypatch.setattr(module.model_control_plane, "mutate_config", _mutate)
    return _copy_config


def test_atomic_role_commit_rejects_change_between_fast_check_and_write(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"default": "provider::old"},
        "bindings": {"agents": {}},
    }
    _install_role_control_plane_fakes(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_assignment(
        role="default",
        model_ref="provider::new",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    # Simulate a write arriving after the fast check but before the model lock.
    monkeypatch.setattr(service, "_assert_target_revision", lambda _transaction: {})
    config["roles"]["default"] = "provider::concurrent"
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "conflict"
    assert committed["error"]["code"] == "config_transaction_stale"
    assert config["roles"]["default"] == "provider::concurrent"


def test_precise_role_rollback_preserves_unrelated_changes_and_stops_on_target_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"default": "provider::old", "summary": "provider::summary-old"},
        "bindings": {"agents": {}},
    }
    _install_role_control_plane_fakes(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    first = service.prepare_role_assignment(
        role="default",
        model_ref="provider::new",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(first["transactionId"], owner_id="owner")["state"] == "committed"
    config["roles"]["summary"] = "provider::summary-new"

    rolled_back = service.rollback(first["transactionId"], owner_id="owner")
    assert rolled_back["state"] == "rolled_back"
    assert config["roles"] == {
        "default": "provider::old",
        "summary": "provider::summary-new",
    }

    second = service.prepare_role_assignment(
        role="default",
        model_ref="provider::new",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(second["transactionId"], owner_id="owner")["state"] == "committed"
    config["roles"]["default"] = "provider::newer"
    conflicted = service.rollback(second["transactionId"], owner_id="owner")

    assert conflicted["state"] == "conflict"
    assert config["roles"]["default"] == "provider::newer"


def test_startup_reconciles_committing_transaction_from_working_digest(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"default": "provider::old"},
        "bindings": {"agents": {}},
    }
    _install_role_control_plane_fakes(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_assignment(
        role="default",
        model_ref="provider::new",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    config["roles"]["default"] = "provider::new"
    validation = dict(transaction["validation"])
    validation["targetWorkingDigest"] = module._digest(
        service._target_snapshot("model_role", "default", config)
    )
    service._update_transaction(
        prepared["transactionId"],
        state="committing",
        validation_json=module._json(validation),
    )

    result = module.ConfigBrokerService().reconcile_incomplete_transactions()

    assert result["ok"] is True
    assert result["transactions"] == [{"transactionId": prepared["transactionId"], "state": "rolled_back"}]
    assert config["roles"]["default"] == "provider::old"


def test_rollback_retries_fail_once_credential_cleanup(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"default": "provider::old"},
        "bindings": {"agents": {}},
    }
    _install_role_control_plane_fakes(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_assignment(
        role="default",
        model_ref="provider::new",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    proposed = dict(transaction["proposed"])
    proposed["newCredentialRefs"] = ["cred:v8-model:test-cleanup"]
    service._update_transaction(prepared["transactionId"], proposed_json=module._json(proposed))
    attempts = []

    def delete(reference):
        attempts.append(reference)
        if len(attempts) == 1:
            raise RuntimeError("fail once")
        return True

    monkeypatch.setattr(module.credential_ref_store, "delete", delete)

    first = service.rollback(prepared["transactionId"], owner_id="owner")
    second = service.rollback(prepared["transactionId"], owner_id="owner")

    assert first["state"] == "recovery_required"
    assert first["rollback"]["targetRestored"] is True
    assert second["state"] == "rolled_back"
    assert attempts == ["cred:v8-model:test-cleanup", "cred:v8-model:test-cleanup"]
    assert config["roles"]["default"] == "provider::old"


def test_stale_commit_cleans_transaction_created_credentials_without_overwrite(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"default": "provider::old"},
        "bindings": {"agents": {}},
    }
    _install_role_control_plane_fakes(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_assignment(
        role="default",
        model_ref="provider::new",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    proposed = dict(transaction["proposed"])
    proposed["newCredentialRefs"] = ["cred:v8-model:stale-cleanup"]
    service._update_transaction(prepared["transactionId"], proposed_json=module._json(proposed))
    deleted = []
    monkeypatch.setattr(module.credential_ref_store, "delete", lambda reference: deleted.append(reference) or True)
    config["roles"]["default"] = "provider::concurrent"

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "conflict"
    assert deleted == ["cred:v8-model:stale-cleanup"]
    assert config["roles"]["default"] == "provider::concurrent"
