from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from typing import Any

import pytest

from core.database import DatabaseManager
from core.model_control_plane import ModelControlPlane
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend


@pytest.fixture(autouse=True)
def _isolate_user_config_file(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as broker_module
    import core.storage as storage_module

    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(broker_module, "db", DatabaseManager(tmp_path / "state.db"))


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _install_model_config(
    module,
    monkeypatch,
    config: dict[str, Any],
    *,
    role_definitions: dict[str, dict[str, Any]] | None = None,
):
    definitions = role_definitions or {
        "supervisor": {"label": "Supervisor", "capabilityClasses": ["chat_general"]},
        "subagent": {"label": "Subagent", "capabilityClasses": ["chat_general"]},
        "summary": {"label": "Summary", "capabilityClasses": ["chat_general"]},
    }

    def snapshot() -> dict[str, Any]:
        return _copy(config)

    def mutate(mutator):
        proposed = mutator(snapshot())
        config.clear()
        config.update(_copy(proposed))
        return snapshot()

    monkeypatch.setattr(module.model_control_plane, "get_config", snapshot)
    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", snapshot)
    monkeypatch.setattr(module.model_control_plane, "normalize_config", lambda value: _copy(value))
    monkeypatch.setattr(module.model_control_plane, "mutate_config", mutate)
    monkeypatch.setattr(module.model_control_plane, "get_role_definitions", lambda _config: deepcopy(definitions))
    return snapshot


def _install_real_model_control_plane(module, monkeypatch, initial: dict[str, Any]):
    persisted: dict[str, Any] = {}
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    control_plane = ModelControlPlane(credential_store=credential_store)

    def read_config() -> dict[str, Any]:
        return _copy(persisted)

    def save_config(value: dict[str, Any]) -> None:
        persisted.clear()
        persisted.update(_copy(value))

    monkeypatch.setattr(module.storage, "get_models_config", read_config)
    monkeypatch.setattr(module.storage, "save_models_config", save_config)
    monkeypatch.setattr(module, "model_control_plane", control_plane)
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    control_plane.save_config(initial)
    return control_plane, persisted, credential_store


class _CatalogStub:
    def __init__(
        self,
        *,
        providers: list[dict[str, Any]] | None = None,
        normalized_models: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._managed = {"version": 1, "providers": _copy(providers or [])}
        self._models = deepcopy(normalized_models or {})

    def _digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self._managed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def managed_recovery_state(self) -> dict[str, Any]:
        return {"managedDigest": self._digest(), "managedExists": True, "managedValid": True}

    def _assert_digest(self, expected_current_digest: str) -> None:
        if not expected_current_digest or expected_current_digest != self._digest():
            raise ValueError("managed catalog digest changed")

    def load_managed(self) -> dict[str, Any]:
        return _copy(self._managed)

    def get_managed_provider(self, provider_id: str) -> dict[str, Any] | None:
        return next(
            (_copy(item) for item in self._managed["providers"] if item.get("id") == provider_id),
            None,
        )

    def delete_managed_provider(self, provider_id: str, *, expected_current_digest: str = "") -> bool:
        if expected_current_digest:
            self._assert_digest(expected_current_digest)
        before = len(self._managed["providers"])
        self._managed["providers"] = [
            item for item in self._managed["providers"] if item.get("id") != provider_id
        ]
        return len(self._managed["providers"]) != before

    def restore_managed_provider(
        self,
        provider_id: str,
        provider: dict[str, Any] | None,
        *,
        expected_current_digest: str = "",
    ) -> dict[str, Any] | None:
        if expected_current_digest:
            self._assert_digest(expected_current_digest)
        self.delete_managed_provider(provider_id)
        if provider is not None:
            self._managed["providers"].append(_copy(provider))
            return _copy(provider)
        return None

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self.get_managed_provider(provider_id)

    def upsert_managed_provider(
        self,
        provider: dict[str, Any],
        *,
        expected_current_digest: str = "",
    ) -> dict[str, Any]:
        if expected_current_digest:
            self._assert_digest(expected_current_digest)
        self.delete_managed_provider(str(provider.get("id") or ""))
        self._managed["providers"].append(_copy(provider))
        return _copy(provider)

    def normalize_model(self, provider: dict[str, Any], model_id: str) -> dict[str, Any]:
        return _copy(self._models.get((str(provider.get("id") or ""), model_id), {}))


def _model_config(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "providers": {
            "provider": {
                "provider": {
                    "name": "Provider",
                    "base_url": "https://api.provider.test/v1",
                    "api_standard": "openai",
                },
                "models": {
                    "target": {
                        "type": "TEXT",
                        "contextWindow": 262_144,
                        "maxTokens": 8_192,
                        "isEnabled": enabled,
                    },
                    "sibling": {
                        "type": "TEXT",
                        "contextWindow": 262_144,
                        "maxTokens": 8_192,
                        "isEnabled": True,
                    },
                },
            }
        },
        "roles": {},
        "bindings": {"agents": {}},
    }


def _catalog_text_model(model_id: str = "model-a") -> dict[str, Any]:
    return {
        "id": model_id,
        "modelId": model_id,
        "type": "TEXT",
        "contextWindow": 262_144,
        "maxTokens": 8_192,
        "capabilities": {"chat": True, "reasoning": True},
        "capabilityClass": "chat_general",
        "runtimeReady": True,
        "isEnabled": True,
        "sourceRefs": ["https://docs.provider.test/models/model-a"],
    }


def _catalog_provider(*, auth: dict[str, Any] | None = None, base_url: str = "https://api.provider.test/v1"):
    return {
        "id": "provider",
        "name": "Provider",
        "baseUrl": base_url,
        "apiStandard": "openai",
        "credentialRealm": "provider",
        "auth": auth or {
            "type": "api_key",
            "header": "Authorization",
            "scheme": "Bearer",
        },
        "defaultChannelId": "responses",
        "channels": [
            {
                "id": "responses",
                "baseUrl": base_url,
                "apiStandard": "openai",
                "wireProtocols": ["openai.responses"],
                "defaultWireProtocol": "openai.responses",
            }
        ],
    }


def test_model_policy_bundle_commit_rollback_and_target_cas(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {"keep": {"provider": {"name": "Keep"}, "models": {}}},
        "roles": {},
        "bindings": {"agents": {}},
        "governance": {"enabled": True, "maxLocalRetries": 1},
        "routingPolicies": {"chat": "supervisor"},
        "roleParameters": {"supervisor": {"temperature": None}},
    }
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    prepared = service.prepare_model_policy(
        governance={"maxLocalRetries": 2},
        routing_policies={"chat": "supervisor"},
        role_parameters={"supervisor": {"temperature": 0.3}},
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert prepared["state"] == "ready_to_commit"
    assert committed["state"] == "committed"
    assert config["governance"]["maxLocalRetries"] == 2
    assert config["roleParameters"]["supervisor"]["temperature"] == 0.3

    config["providers"]["later"] = {"provider": {"name": "Later"}, "models": {}}
    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")
    assert rolled_back["state"] == "rolled_back"
    assert config["governance"] == {"enabled": True, "maxLocalRetries": 1}
    assert config["roleParameters"] == {"supervisor": {"temperature": None}}
    assert "later" in config["providers"]

    stale = service.prepare_model_policy(
        governance={"maxLocalRetries": 3},
        routing_policies=None,
        role_parameters=None,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["governance"]["maxProviderSwitches"] = 9
    conflicted = service.commit(stale["transactionId"], owner_id="owner")
    assert conflicted["state"] == "conflict"
    assert conflicted["error"]["code"] == "config_transaction_stale"
    persisted = service.get_transaction(stale["transactionId"], owner_id="owner")
    assert persisted["state"] == "conflict"
    assert persisted["error"]["code"] == "config_transaction_stale"
    assert config["governance"]["maxLocalRetries"] == 1


def test_model_snapshot_recovery_restores_only_a_durable_safe_broker_preimage(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    clean = _model_config()
    config = _copy(clean)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    source = service._insert_transaction(
        target_kind="model_role",
        target_id="supervisor",
        operation="assign",
        state="ready_to_commit",
        owner_id="owner",
        session_id="session",
        run_id="run",
        before=clean,
        proposed={"role": "supervisor", "modelRef": "provider::target", "newCredentialRefs": []},
    )
    service._update_transaction(source["transactionId"], state="committed")
    corrupt = {
        "providers": {"corrupt": {"provider": {"name": "Corrupt"}, "models": {}}},
        "roles": {"supervisor": "corrupt::missing"},
        "bindings": {"agents": {}},
    }
    config.clear()
    config.update(_copy(corrupt))

    prepared = service.prepare_model_snapshot_recovery(
        source_transaction_id=source["transactionId"],
        owner_id="owner",
        session_id="session",
        run_id="recovery",
    )
    assert config == corrupt
    assert prepared["state"] == "ready_to_commit"

    committed = service.commit(prepared["transactionId"], owner_id="owner")
    assert committed["state"] == "committed"
    assert config == clean

    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")
    assert rolled_back["state"] == "rolled_back"
    assert config == corrupt


def test_model_snapshot_recovery_rejects_a_broker_preimage_with_raw_secret(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    clean = _model_config()
    clean["providers"]["provider"]["provider"]["api_key"] = "sk-not-safe-to-recover"
    config = _model_config()
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    source = service._insert_transaction(
        target_kind="model_role",
        target_id="supervisor",
        operation="assign",
        state="ready_to_commit",
        owner_id="owner",
        session_id="session",
        run_id="run",
        before=clean,
        proposed={"newCredentialRefs": []},
    )
    service._update_transaction(source["transactionId"], state="committed")

    with pytest.raises(module.ConfigBrokerError) as blocked:
        service.prepare_model_snapshot_recovery(
            source_transaction_id=source["transactionId"],
            owner_id="owner",
            session_id="session",
            run_id="recovery",
        )

    assert blocked.value.code == "model_snapshot_source_contains_secret"


def test_model_snapshot_recovery_records_an_already_restored_snapshot_without_rewrite(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    clean = _model_config()
    config = _copy(clean)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    source = service._insert_transaction(
        target_kind="model_role",
        target_id="supervisor",
        operation="assign",
        state="ready_to_commit",
        owner_id="owner",
        session_id="session",
        run_id="run",
        before=clean,
        proposed={"newCredentialRefs": []},
    )
    service._update_transaction(source["transactionId"], state="committed")

    prepared = service.prepare_model_snapshot_recovery(
        source_transaction_id=source["transactionId"],
        owner_id="owner",
        session_id="session",
        run_id="recovery",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert committed["result"]["alreadyCurrent"] is True
    assert config == clean


def test_model_policy_bundle_rejects_unknown_fields_without_transaction(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    config = {"providers": {}, "roles": {}, "bindings": {"agents": {}}}
    _install_model_config(module, monkeypatch, config)

    with pytest.raises(module.ConfigBrokerError) as blocked:
        module.ConfigBrokerService().prepare_model_policy(
            governance={"inventedLimit": 1},
            routing_policies=None,
            role_parameters=None,
            owner_id="owner",
            session_id="session",
            run_id="run",
        )

    assert blocked.value.code == "model_policy_field_unknown"
    with test_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM config_broker_transactions").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("operation", "initial_enabled", "committed_exists", "committed_enabled"),
    [
        ("enable", False, True, True),
        ("disable", True, True, False),
        ("remove", True, False, None),
    ],
)
def test_model_record_change_is_recoverable_and_preserves_siblings(
    tmp_path,
    monkeypatch,
    operation: str,
    initial_enabled: bool,
    committed_exists: bool,
    committed_enabled: bool | None,
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / f"{operation}.db"))
    config = _model_config(enabled=initial_enabled)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    prepared = service.prepare_model_record_change(
        model_ref="provider::target",
        operation=operation,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")
    models = config["providers"]["provider"]["models"]

    assert committed["state"] == "committed"
    assert ("target" in models) is committed_exists
    if committed_enabled is not None:
        assert models["target"]["isEnabled"] is committed_enabled
    assert "sibling" in models

    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")
    models = config["providers"]["provider"]["models"]
    assert rolled_back["state"] == "rolled_back"
    assert models["target"]["isEnabled"] is initial_enabled
    assert "sibling" in models


def test_model_record_rollback_stops_when_sibling_set_drifted(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_model_record_change(
        model_ref="provider::target",
        operation="disable",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"

    config["providers"]["provider"]["models"]["concurrent"] = {"isEnabled": True}
    rollback = service.rollback(prepared["transactionId"], owner_id="owner")

    assert rollback["state"] == "conflict"
    assert rollback["rollback"]["errorCode"] == "config_rollback_conflict"
    assert config["providers"]["provider"]["models"]["target"]["isEnabled"] is False
    assert "concurrent" in config["providers"]["provider"]["models"]


def test_model_provider_change_commit_and_rollback_preserve_models(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    prepared = service.prepare_model_provider_change(
        provider_id="provider",
        operation="upsert",
        provider_config={"isEnabled": False},
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert config["providers"]["provider"]["provider"]["is_enabled"] is False
    assert set(config["providers"]["provider"]["models"]) == {"target", "sibling"}
    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert "is_enabled" not in config["providers"]["provider"]["provider"]
    assert set(config["providers"]["provider"]["models"]) == {"target", "sibling"}


def test_model_provider_target_drift_requires_new_credential_and_tracks_retired_ref(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    old_ref = credential_store.put("old-secret", namespace="model")
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    config = _model_config(enabled=True)
    config["providers"]["provider"]["provider"].update(
        {
            "credentialRef": old_ref,
            "credentialSource": "os_credential_store",
            "authContract": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        }
    )
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    with pytest.raises(module.ConfigBrokerError) as missing_key:
        service.prepare_model_provider_change(
            provider_id="provider",
            operation="upsert",
            provider_config={"baseUrl": "https://other.provider.test/v1"},
            request_secret=False,
            oauth_credential="",
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    assert missing_key.value.code == "model_provider_credential_required"
    assert config["providers"]["provider"]["provider"]["credentialRef"] == old_ref

    prepared = service.prepare_model_provider_change(
        provider_id="provider",
        operation="upsert",
        provider_config={"authContract": {"type": "none"}},
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    assert prepared["state"] == "ready_to_commit"
    assert transaction["proposed"]["supersededCredentialRefs"] == [old_ref]
    assert credential_store.status(old_ref).configured is True
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    assert credential_store.status(old_ref).configured is True
    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert config["providers"]["provider"]["provider"]["credentialRef"] == old_ref
    assert credential_store.status(old_ref).configured is True


def test_enabled_oauth_provider_requires_reference_and_rolls_back_missing_file(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    oauth_contract = {
        "type": "oauth_file",
        "path": str(tmp_path / "missing-oauth.json"),
        "preset": "test",
    }

    with pytest.raises(module.ConfigBrokerError) as missing_reference:
        service.prepare_model_provider_change(
            provider_id="provider",
            operation="upsert",
            provider_config={"authContract": oauth_contract},
            request_secret=False,
            oauth_credential="",
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    assert missing_reference.value.code == "model_provider_oauth_credential_required"

    before = deepcopy(config)
    prepared = service.prepare_model_provider_change(
        provider_id="provider",
        operation="upsert",
        provider_config={"authContract": oauth_contract},
        request_secret=False,
        oauth_credential=f"oauth:{tmp_path / 'missing-oauth.json'}",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == "provider_runtime_validation_failed"
    assert config == before


def test_provider_delete_rechecks_role_and_agent_dependencies(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    config["roles"]["supervisor"] = "provider::target"
    with pytest.raises(module.ConfigBrokerError) as bound_role:
        service.prepare_model_provider_change(
            provider_id="provider",
            operation="remove",
            provider_config=None,
            request_secret=False,
            oauth_credential="",
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    assert bound_role.value.code == "provider_still_bound"

    config["roles"].clear()
    prepared = service.prepare_model_provider_change(
        provider_id="provider",
        operation="remove",
        provider_config=None,
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["bindings"]["agents"]["late"] = {"modelId": "provider::sibling"}
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == "provider_still_bound"
    assert "provider" in config["providers"]


def test_model_binding_partial_update_preserves_existing_fields_and_is_recoverable(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)

    def save_config(value):
        config.clear()
        config.update(_copy(value))
        return _copy(config)

    monkeypatch.setattr(module.model_control_plane, "save_config", save_config)
    service = module.ConfigBrokerService()
    surface = {
        "mode": "provider_reasoning",
        "trust": "adapter_verified",
        "responseFields": ["reasoning_content"],
    }
    prepared = service.prepare_model_binding(
        provider_id="provider",
        model_id="target",
        model_config={"reasoningSurface": surface},
        source_provider_id="provider",
        source_model_id="target",
        source="reasoning_repair_probe",
        replace_provider_models=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    model = config["providers"]["provider"]["models"]["target"]
    assert model["type"] == "TEXT"
    assert model["contextWindow"] == 262_144
    assert model["maxTokens"] == 8_192
    assert model["reasoningSurface"] == surface
    assert "sibling" in config["providers"]["provider"]["models"]

    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert "reasoningSurface" not in config["providers"]["provider"]["models"]["target"]


def test_model_binding_move_and_replace_block_bound_removed_models(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    config["providers"]["other"] = {
        "provider": {
            "name": "Other",
            "base_url": "https://other.provider.test/v1",
            "api_standard": "openai",
        },
        "models": {},
    }
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    config["roles"]["supervisor"] = "provider::target"
    with pytest.raises(module.ConfigBrokerError) as moving_bound:
        service.prepare_model_binding(
            provider_id="other",
            model_id="moved",
            model_config={},
            source_provider_id="provider",
            source_model_id="target",
            source="manual",
            replace_provider_models=False,
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    assert moving_bound.value.code == "model_binding_still_bound"

    config["roles"] = {}
    config["bindings"]["agents"]["reviewer"] = {"model_id": "provider::sibling"}
    with pytest.raises(module.ConfigBrokerError) as replacing_bound:
        service.prepare_model_binding(
            provider_id="provider",
            model_id="target",
            model_config={},
            source_provider_id="provider",
            source_model_id="target",
            source="catalog_import",
            replace_provider_models=True,
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    assert replacing_bound.value.code == "model_binding_still_bound"

    config["bindings"]["agents"] = {}
    prepared = service.prepare_model_binding(
        provider_id="other",
        model_id="moved",
        model_config={},
        source_provider_id="provider",
        source_model_id="target",
        source="manual",
        replace_provider_models=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["roles"]["supervisor"] = "provider::target"
    committed = service.commit(prepared["transactionId"], owner_id="owner")
    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == "model_binding_still_bound"
    assert "target" in config["providers"]["provider"]["models"]
    assert "moved" not in config["providers"]["other"]["models"]


@pytest.mark.parametrize(
    ("target_kind", "expected_code"),
    [
        ("provider", "provider_runtime_validation_failed"),
        ("binding", "model_binding_runtime_validation_failed"),
    ],
)
def test_provider_and_binding_static_verification_failure_rolls_back(
    tmp_path,
    monkeypatch,
    target_kind,
    expected_code,
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / f"{target_kind}.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    monkeypatch.setattr(
        module.ConfigBrokerService,
        f"_verify_committed_{'provider' if target_kind == 'provider' else 'model'}_static",
        lambda *_args, **_kwargs: {
            "ok": False,
            "status": "invalid",
            "summary": "invalid",
            "verifier": "test",
        },
    )
    service = module.ConfigBrokerService()
    if target_kind == "provider":
        prepared = service.prepare_model_provider_change(
            provider_id="provider",
            operation="upsert",
            provider_config={"isEnabled": False},
            request_secret=False,
            oauth_credential="",
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    else:
        prepared = service.prepare_model_binding(
            provider_id="provider",
            model_id="target",
            model_config={"reasoningSurface": {"mode": "provider_reasoning"}},
            source_provider_id="provider",
            source_model_id="target",
            source="reasoning_repair_probe",
            replace_provider_models=False,
            owner_id="owner",
            session_id="session",
            run_id="run",
        )

    committed = service.commit(prepared["transactionId"], owner_id="owner")
    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == expected_code
    assert config == _model_config(enabled=True)


def test_custom_catalog_provider_delete_is_cas_scoped_and_recoverable(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module
    from core.model_provider_catalog import ModelProviderCatalog

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    catalog = ModelProviderCatalog(
        custom_path=tmp_path / "custom.json",
        managed_path=tmp_path / "managed.json",
    )
    target = catalog.build_custom_provider("Target", "https://target.example/v1")
    sibling = catalog.build_custom_provider("Sibling", "https://sibling.example/v1")
    catalog.save_custom_provider(target)
    catalog.save_custom_provider(sibling)
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)
    service = module.ConfigBrokerService()

    prepared = service.prepare_custom_catalog_provider_removal(
        provider_id=str(target["id"]),
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    assert all(item["id"] != target["id"] for item in catalog.load_custom()["providers"])
    assert any(item["id"] == sibling["id"] for item in catalog.load_custom()["providers"])

    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert {item["id"] for item in catalog.load_custom()["providers"]} == {target["id"], sibling["id"]}


def test_custom_catalog_corruption_blocks_delete_without_overwriting_bytes(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module
    from core.model_provider_catalog import ModelProviderCatalog

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    custom_path = tmp_path / "custom.json"
    catalog = ModelProviderCatalog(custom_path=custom_path, managed_path=tmp_path / "managed.json")
    target = catalog.build_custom_provider("Target", "https://target.example/v1")
    catalog.save_custom_provider(target)
    catalog.load_custom()
    corrupt_bytes = b"{broken-custom-catalog"
    custom_path.write_bytes(corrupt_bytes)
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)

    with pytest.raises(module.ConfigBrokerError) as invalid:
        module.ConfigBrokerService().prepare_custom_catalog_provider_removal(
            provider_id=str(target["id"]),
            owner_id="owner",
            session_id="session",
            run_id="run",
        )
    assert invalid.value.code == "catalog_custom_invalid"
    assert custom_path.read_bytes() == corrupt_bytes


def test_startup_reconciles_provider_write_from_pre_persist_planned_digest(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_model_provider_change(
        provider_id="provider",
        operation="upsert",
        provider_config={"isEnabled": False},
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    def crash_after_persist(mutator):
        proposed = mutator(_copy(config))
        config.clear()
        config.update(_copy(proposed))
        raise SystemExit("simulated crash after config persistence")

    monkeypatch.setattr(module.model_control_plane, "mutate_config", crash_after_persist)
    with pytest.raises(SystemExit):
        service.commit(prepared["transactionId"], owner_id="owner")

    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    assert transaction["state"] == "committing"
    assert transaction["validation"]["targetPlannedDigest"]
    assert not transaction["validation"].get("targetWorkingDigest")
    assert config["providers"]["provider"]["provider"]["is_enabled"] is False

    def mutate(mutator):
        proposed = mutator(_copy(config))
        config.clear()
        config.update(_copy(proposed))
        return _copy(config)

    monkeypatch.setattr(module.model_control_plane, "mutate_config", mutate)
    reconciled = module.ConfigBrokerService().reconcile_incomplete_transactions()
    assert reconciled["transactions"] == [
        {"transactionId": prepared["transactionId"], "state": "rolled_back"}
    ]
    assert "is_enabled" not in config["providers"]["provider"]["provider"]


def test_reconcile_preserves_credential_cleanup_failure_for_explicit_retry(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config(enabled=True)
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    transaction = service._insert_transaction(
        target_kind="model_provider",
        target_id="provider",
        operation="upsert",
        state="ready_to_commit",
        owner_id="owner",
        session_id="session",
        run_id="run",
        before=_copy(config),
        proposed={
            "providerId": "provider",
            "operation": "upsert",
            "provider": _copy(config["providers"]["provider"]["provider"]),
            "newCredentialRefs": ["cred:v8-model:cleanup-retry"],
        },
    )
    config["providers"]["provider"]["provider"]["description"] = "concurrent"
    attempts = []

    def fail_delete(reference):
        attempts.append(reference)
        raise RuntimeError("fail cleanup")

    monkeypatch.setattr(module.credential_ref_store, "delete", fail_delete)
    committed = service.commit(transaction["transactionId"], owner_id="owner")
    assert committed["state"] == "recovery_required"
    assert committed["error"]["code"] == "config_credential_cleanup_failed"
    assert "cred:v8-model:cleanup-retry" not in str(committed)

    reconciled = module.ConfigBrokerService().reconcile_incomplete_transactions()
    assert reconciled["transactions"] == [
        {"transactionId": transaction["transactionId"], "state": "recovery_required"}
    ]
    pending = service.get_transaction(transaction["transactionId"], owner_id="owner")
    assert pending["error"]["code"] == "config_credential_cleanup_failed"
    assert "cred:v8-model:cleanup-retry" not in str(pending)

    monkeypatch.setattr(module.credential_ref_store, "delete", lambda reference: attempts.append(reference) or True)
    retried = service.rollback(transaction["transactionId"], owner_id="owner")
    assert retried["state"] == "conflict"
    assert retried["rollback"]["credentialCleanupPending"] is False
    assert attempts == [
        "cred:v8-model:cleanup-retry",
        "cred:v8-model:cleanup-retry",
        "cred:v8-model:cleanup-retry",
    ]


def test_model_prepare_reuses_existing_transport_without_partial_field_drift(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    credential_ref = credential_store.put("secret-value", namespace="model")
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    config = _model_config(enabled=True)
    config["providers"]["provider"]["provider"].update(
        {
            "credentialRef": credential_ref,
            "credentialSource": "os_credential_store",
            "authContract": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
            "channels": [
                {
                    "id": "responses",
                    "baseUrl": "https://api.provider.test/v1",
                    "apiStandard": "openai",
                    "wireProtocols": ["openai.responses"],
                    "defaultWireProtocol": "openai.responses",
                }
            ],
            "defaultChannelId": "responses",
        }
    )
    config["providers"]["provider"]["models"]["target"].update(
        {
            "capabilities": {"chat": True, "reasoning": True},
            "capabilitySource": "provider_metadata",
            "sourceRefs": ["https://docs.provider.test/model"],
        }
    )
    _install_model_config(module, monkeypatch, config)

    prepared = module.ConfigBrokerService().prepare_model(
        provider_id="provider",
        model_id="target",
        provider_name="",
        base_url="",
        api_standard="",
        model_type="",
        context_window=None,
        max_tokens=None,
        capabilities=None,
        evidence_refs=None,
        credential_required=True,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = module.ConfigBrokerService().get_transaction(
        prepared["transactionId"], owner_id="owner", include_private=True
    )

    assert prepared["state"] == "ready_to_commit"
    assert transaction["proposed"]["provider"]["credentialRef"] == credential_ref
    assert transaction["proposed"]["provider"]["defaultChannelId"] == "responses"
    assert transaction["proposed"]["model"]["type"] == "TEXT"
    assert transaction["proposed"]["model"]["capabilities"] == {"chat": True, "reasoning": True}
    assert transaction["proposed"]["model"]["sourceRefs"] == ["https://docs.provider.test/model"]


def test_model_prepare_reuses_a_legacy_managed_credential_without_auth_contract(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    credential_ref = credential_store.put("secret-value", namespace="model")
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    config = _model_config(enabled=True)
    config["providers"]["provider"]["provider"].update(
        {
            "credentialRef": credential_ref,
            "credentialSource": "os_credential_store",
        }
    )
    config["providers"]["provider"]["models"]["target"].update(
        {
            "capabilities": {"chat": True, "reasoning": True},
            "sourceRefs": ["https://docs.provider.test/model"],
        }
    )
    _install_model_config(module, monkeypatch, config)

    prepared = module.ConfigBrokerService().prepare_model(
        provider_id="provider",
        model_id="target",
        provider_name="",
        base_url="",
        api_standard="",
        model_type="",
        context_window=None,
        max_tokens=None,
        capabilities=None,
        evidence_refs=None,
        credential_required=True,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = module.ConfigBrokerService().get_transaction(
        prepared["transactionId"], owner_id="owner", include_private=True
    )

    assert prepared["state"] == "ready_to_commit"
    assert transaction["proposed"]["credentialReuseAuthorized"] is True
    assert transaction["proposed"]["provider"]["credentialRef"] == credential_ref


def test_model_commit_does_not_treat_a_materialized_managed_credential_as_a_stale_revision(
    tmp_path, monkeypatch
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    credential_ref = credential_store.put("secret-value", namespace="model")
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    config = _model_config(enabled=True)
    config["providers"]["provider"]["provider"].update(
        {
            "credentialRef": credential_ref,
            "credentialSource": "os_credential_store",
        }
    )
    _install_model_config(module, monkeypatch, config)

    def materialized_config() -> dict[str, Any]:
        current = _copy(config)
        current["providers"]["provider"]["provider"]["api_key"] = "secret-value"
        return current

    monkeypatch.setattr(module.model_control_plane, "get_config", materialized_config)

    def upsert_provider_model_records(**kwargs: Any) -> dict[str, Any]:
        kwargs["precondition"](materialized_config())
        config["providers"]["provider"] = {
            "provider": _copy(kwargs["provider_patch"]),
            "models": {
                **dict(config["providers"]["provider"]["models"]),
                "target": _copy(kwargs["model_patch"]),
            },
        }
        kwargs["before_persist"](_copy(config))
        return {"config": _copy(config)}

    monkeypatch.setattr(module.model_control_plane, "upsert_provider_model_records", upsert_provider_model_records)
    monkeypatch.setattr(
        module.ConfigBrokerService,
        "_verify_committed_model",
        lambda *_args: {"ok": True, "status": "ok", "summary": "ok", "verifier": "test"},
    )
    service = module.ConfigBrokerService()
    prepared = service.prepare_model(
        provider_id="provider",
        model_id="target",
        provider_name="",
        base_url="",
        api_standard="",
        model_type="",
        context_window=None,
        max_tokens=None,
        capabilities=None,
        evidence_refs=None,
        credential_required=True,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert prepared["state"] == "ready_to_commit"
    assert committed["state"] == "committed"


def test_real_control_plane_provider_binding_and_delete_share_one_durable_revision(
    tmp_path, monkeypatch
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    initial = _model_config(enabled=True)
    initial["providers"]["provider"]["provider"].update(
        {
            "authContract": {
                "type": "api_key",
                "header": "Authorization",
                "scheme": "Bearer",
            },
            "channels": [
                {
                    "id": "chat-completions",
                    "label": "OpenAI Chat Completions",
                    "baseUrl": "https://api.provider.test/v1",
                    "apiStandard": "openai",
                    "wireProtocols": ["openai.chat_completions"],
                }
            ],
            "defaultChannelId": "chat-completions",
        }
    )
    control_plane, persisted, credential_store = _install_real_model_control_plane(
        module,
        monkeypatch,
        initial,
    )
    credential_ref = credential_store.put("secret-value", namespace="model")
    seeded = control_plane.get_storage_safe_config()
    seeded_provider = seeded["providers"]["provider"]["provider"]
    seeded_provider.update(
        {
            "credentialRef": credential_ref,
            "credentialSource": "os_credential_store",
            "authContract": {
                "type": "api_key",
                "header": "Authorization",
                "scheme": "Bearer",
            },
        }
    )
    control_plane.save_config(seeded)
    service = module.ConfigBrokerService()

    materialized = control_plane.get_config()["providers"]["provider"]["provider"]
    assert materialized["api_key"] == "secret-value"
    assert materialized["credentialStatus"] == "configured"

    provider_change = service.prepare_model_provider_change(
        provider_id="provider",
        operation="upsert",
        provider_config={
            "name": "Provider",
            "icon": "",
            "base_url": "https://api.provider.test/v1",
            "api_standard": "openai",
            "authContract": {
                "type": "api_key",
                "header": "Authorization",
                "scheme": "Bearer",
            },
            "channels": [
                {
                    "id": "chat-completions",
                    "label": "OpenAI Chat Completions",
                    "baseUrl": "https://api.provider.test/v1/",
                    "apiStandard": "openai",
                    "wireProtocols": ["openai.chat_completions"],
                }
            ],
            "defaultChannelId": "chat-completions",
        },
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run-provider",
    )
    provider_commit = service.commit(
        provider_change["transactionId"],
        owner_id="owner",
        user_confirmed_target=True,
    )

    assert provider_commit["state"] == "committed", provider_commit.get("error")
    stored_provider = persisted["providers"]["provider"]["provider"]
    assert stored_provider["icon"] is None
    assert stored_provider["channels"][0]["baseUrl"] == "https://api.provider.test/v1/"
    assert stored_provider["defaultChannelId"] == "chat-completions"
    assert "credentialStatus" not in stored_provider
    assert "api_key" not in stored_provider

    stale_provider_change = service.prepare_model_provider_change(
        provider_id="provider",
        operation="upsert",
        provider_config={"description": "planned description"},
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run-stale-provider",
    )

    def apply_concurrent_durable_change(current: dict[str, Any]) -> dict[str, Any]:
        current["providers"]["provider"]["provider"]["description"] = "concurrent description"
        return current

    control_plane.mutate_config(apply_concurrent_durable_change)
    stale_provider_commit = service.commit(
        stale_provider_change["transactionId"],
        owner_id="owner",
        user_confirmed_target=True,
    )

    assert stale_provider_commit["state"] == "conflict"
    assert stale_provider_commit["error"]["code"] == "config_transaction_stale"
    assert persisted["providers"]["provider"]["provider"]["description"] == "concurrent description"

    monkeypatch.setattr(
        module.ConfigBrokerService,
        "_verify_committed_model",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "ok",
            "summary": "ok",
            "verifier": "test",
        },
    )
    model_change = service.prepare_model(
        provider_id="provider",
        model_id="catalog-model",
        provider_name="",
        base_url="",
        api_standard="",
        model_type="MULTIMODAL",
        context_window=1_000_000,
        max_tokens=4_096,
        capabilities={"chat": True, "vision": True, "multimodal": True},
        evidence_refs=["https://docs.provider.test/catalog-model"],
        credential_required=True,
        owner_id="owner",
        session_id="session",
        run_id="run-model",
        catalog_fact_provenance={
            "contextWindow": {
                "source": "official_docs",
                "confidence": "authoritative",
                "sourceRefs": ["https://docs.provider.test/catalog-model"],
            },
            "maxTokens": {
                "source": "v8_conservative_2026_default",
                "confidence": "estimated",
                "notes": "Not an official model limit.",
            },
        },
    )
    model_commit = service.commit(model_change["transactionId"], owner_id="owner")

    assert model_change["state"] == "ready_to_commit"
    assert model_commit["state"] == "committed", model_commit.get("error")
    stored_model = persisted["providers"]["provider"]["models"]["catalog-model"]
    assert stored_model["contextWindow"] == 1_000_000
    assert stored_model["maxTokens"] == 4_096
    assert stored_model["factProvenance"]["maxTokens"]["confidence"] == "estimated"
    assert "credentialStatus" not in persisted["providers"]["provider"]["provider"]
    assert "api_key" not in persisted["providers"]["provider"]["provider"]

    binding = service.prepare_model_binding(
        provider_id="provider",
        model_id="target",
        model_config={"maxTokens": 9_216},
        source_provider_id="provider",
        source_model_id="target",
        source="manual",
        replace_provider_models=False,
        owner_id="owner",
        session_id="session",
        run_id="run-binding",
    )
    binding_commit = service.commit(binding["transactionId"], owner_id="owner")

    assert binding_commit["state"] == "committed"
    assert persisted["providers"]["provider"]["models"]["target"]["maxTokens"] == 9_216

    removal = service.prepare_model_provider_change(
        provider_id="provider",
        operation="remove",
        provider_config=None,
        request_secret=False,
        oauth_credential="",
        owner_id="owner",
        session_id="session",
        run_id="run-remove",
    )
    removal_commit = service.commit(removal["transactionId"], owner_id="owner")

    assert removal_commit["state"] == "committed"
    assert "provider" not in persisted["providers"]


def test_role_commit_does_not_treat_a_materialized_managed_credential_as_a_changed_model(
    tmp_path, monkeypatch
) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    credential_ref = credential_store.put("secret-value", namespace="model")
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    config = _model_config(enabled=True)
    config["providers"]["provider"]["provider"].update(
        {
            "credentialRef": credential_ref,
            "credentialSource": "os_credential_store",
        }
    )
    _install_model_config(module, monkeypatch, config)

    def materialized_config() -> dict[str, Any]:
        current = _copy(config)
        current["providers"]["provider"]["provider"]["api_key"] = "secret-value"
        return current

    def mutate(mutator):
        proposed = mutator(materialized_config())
        safe = module.model_control_plane._storage_safe_config(proposed)
        config.clear()
        config.update(_copy(safe))
        return _copy(config)

    monkeypatch.setattr(module.model_control_plane, "get_config", materialized_config)
    monkeypatch.setattr(module.model_control_plane, "mutate_config", mutate)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_assignment(
        role="supervisor",
        model_ref="provider::target",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert prepared["state"] == "ready_to_commit"
    assert committed["state"] == "committed"
    assert config["roles"]["supervisor"] == "provider::target"


def test_model_prepare_rejects_forged_provenance_and_secret_evidence(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    _install_model_config(module, monkeypatch, {"providers": {}, "roles": {}, "bindings": {"agents": {}}})
    service = module.ConfigBrokerService()
    kwargs = {
        "provider_id": "provider",
        "model_id": "model",
        "provider_name": "Provider",
        "base_url": "https://api.provider.test/v1",
        "api_standard": "openai",
        "model_type": "TEXT",
        "context_window": 32_000,
        "max_tokens": 4_096,
        "capabilities": {"chat": True},
        "credential_required": False,
        "owner_id": "owner",
        "session_id": "session",
        "run_id": "run",
        "provider_config": {"authContract": {"type": "none"}},
    }
    with pytest.raises(module.ConfigBrokerError) as forged:
        service.prepare_model(
            **kwargs,
            evidence_refs=[],
            model_config={"factProvenance": {"contextWindow": {"confidence": "authoritative"}}},
        )
    assert forged.value.code == "config_patch_field_unknown"

    with pytest.raises(module.ConfigBrokerError) as secret_ref:
        service.prepare_model(
            **kwargs,
            evidence_refs=["Bearer abcdefghijklmnop"],
        )
    assert secret_ref.value.code == "config_secret_in_evidence_ref"

    prepared = service.prepare_model(**kwargs, evidence_refs=["https://docs.provider.test/model"])
    assert prepared["state"] == "ready_to_commit"
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    assert transaction["proposed"]["model"]["factProvenance"]["contextWindow"]["confidence"] == "unverified"

    catalog_prepared = service.prepare_model(
        **kwargs,
        evidence_refs=["https://docs.provider.test/model"],
        catalog_fact_provenance={
            "contextWindow": {
                "source": "official_docs",
                "confidence": "authoritative",
                "sourceRefs": ["https://docs.provider.test/model"],
            },
            "maxTokens": {
                "source": "v8_conservative_2026_default",
                "confidence": "estimated",
                "notes": "Not an official model limit.",
            },
        },
    )
    catalog_transaction = service.get_transaction(
        catalog_prepared["transactionId"], owner_id="owner", include_private=True
    )
    assert catalog_transaction["proposed"]["model"]["factProvenance"]["contextWindow"]["confidence"] == "authoritative"
    assert catalog_transaction["proposed"]["model"]["factProvenance"]["maxTokens"]["source"] == "v8_conservative_2026_default"


def test_role_unbind_restores_plain_role_without_touching_other_roles(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"supervisor": "provider::target", "summary": "provider::summary"},
        "bindings": {"agents": {}},
    }
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    prepared = service.prepare_role_unbind(
        role="supervisor",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    assert config["roles"] == {"supervisor": "", "summary": "provider::summary"}

    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert config["roles"] == {
        "supervisor": "provider::target",
        "summary": "provider::summary",
    }


def test_role_unbind_deletes_and_restores_agent_binding(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {
        "providers": {},
        "roles": {"subagent": "provider::fallback"},
        "bindings": {
            "agents": {
                "reviewer": {"model_id": "provider::target"},
                "writer": {"model_id": "provider::writer"},
            }
        },
    }
    _install_model_config(module, monkeypatch, config)
    monkeypatch.setattr(
        module.storage,
        "get_agent",
        lambda agent_id: {"id": agent_id, "name": "Reviewer"} if agent_id == "reviewer" else None,
    )
    service = module.ConfigBrokerService()

    prepared = service.prepare_role_unbind(
        role="agent:reviewer",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    assert config["bindings"]["agents"] == {"writer": {"model_id": "provider::writer"}}

    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert config["bindings"]["agents"] == {
        "reviewer": {"model_id": "provider::target"},
        "writer": {"model_id": "provider::writer"},
    }


def test_managed_provider_delete_commit_and_rollback_are_target_scoped(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    target = {
        "id": "target",
        "name": "Target",
        "baseUrl": "https://target.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    sibling = {
        "id": "sibling",
        "name": "Sibling",
        "baseUrl": "https://sibling.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    catalog = _CatalogStub(providers=[target, sibling])
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)
    service = module.ConfigBrokerService()

    prepared = service.prepare_catalog_provider_removal(
        provider_id="target",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert catalog.get_managed_provider("target") is None
    assert catalog.get_managed_provider("sibling") == sibling

    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")
    assert rolled_back["state"] == "rolled_back"
    assert catalog.get_managed_provider("target") == target
    assert catalog.get_managed_provider("sibling") == sibling


def test_catalog_connect_reuses_only_exact_existing_credential(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    credential_store = CredentialRefStore(MemoryCredentialBackend())
    credential_ref = credential_store.put(
        "secret",
        reference="cred:v8-model:provider-exact",
        namespace="model",
    )
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    provider = _catalog_provider()
    model = _catalog_text_model()
    channels = deepcopy(provider["channels"])
    config = {
        "providers": {
            "provider": {
                "provider": {
                    "name": "Provider",
                    "base_url": provider["baseUrl"],
                    "api_standard": "openai",
                    "providerKind": "chat",
                    "type": "API",
                    "credential_mode": "apiKey",
                    "credentialRealm": "provider",
                    "authContract": deepcopy(provider["auth"]),
                    "channels": channels,
                    "defaultChannelId": "responses",
                    "credentialRef": credential_ref,
                },
                "models": {},
            }
        },
        "roles": {},
        "bindings": {"agents": {}},
    }
    _install_model_config(module, monkeypatch, config)
    catalog = _CatalogStub(
        providers=[provider],
        normalized_models={("provider", "model-a"): model},
    )
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)

    prepared = module.ConfigBrokerService().prepare_catalog_model(
        provider_id="provider",
        model_id="model-a",
        discover_if_needed=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = module.ConfigBrokerService().get_transaction(
        prepared["transactionId"],
        owner_id="owner",
        include_private=True,
    )

    assert prepared["state"] == "ready_to_commit"
    assert prepared["credentialReused"] is True
    assert transaction["proposed"]["credentialReuseAuthorized"] is True
    assert transaction["proposed"]["provider"]["credentialRef"] == credential_ref
    assert transaction["proposed"]["model"]["endpointBinding"]["wireProtocol"] == "openai.responses"


@pytest.mark.parametrize(
    "mutation",
    [
        {"base_url": "https://other.provider.test/v1"},
        {"authContract": {"type": "api_key", "header": "X-API-Key"}},
    ],
)
def test_credential_fingerprint_rejects_endpoint_or_auth_drift(monkeypatch, mutation) -> None:
    import core.config_broker_service as module

    credential_store = CredentialRefStore(MemoryCredentialBackend())
    credential_ref = credential_store.put(
        "secret",
        reference="cred:v8-model:provider-drift",
        namespace="model",
    )
    monkeypatch.setattr(module, "credential_ref_store", credential_store)
    exact = {
        "base_url": "https://api.provider.test/v1",
        "api_standard": "openai",
        "credentialRealm": "provider",
        "type": "API",
        "authContract": {
            "type": "api_key",
            "header": "Authorization",
            "scheme": "Bearer",
        },
    }

    proposed = {**deepcopy(exact), **deepcopy(mutation)}
    assert module._credential_ref_is_reusable(
        credential_ref,
        existing_provider=exact,
        proposed_provider=proposed,
    ) is False


def test_oauth_catalog_facts_are_not_blocked_by_storage_minimal_patch(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {"providers": {}, "roles": {}, "bindings": {"agents": {}}}
    _install_model_config(module, monkeypatch, config)
    provider = _catalog_provider(
        auth={"type": "oauth_file", "path": "~/.codex/auth.json", "preset": "codex"},
        base_url="https://chatgpt.com/backend-api",
    )
    provider["id"] = "codex"
    provider["name"] = "Codex"
    model = _catalog_text_model()
    catalog = _CatalogStub(
        providers=[provider],
        normalized_models={("codex", "model-a"): model},
    )
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)

    prepared = module.ConfigBrokerService().prepare_catalog_model(
        provider_id="codex",
        model_id="model-a",
        discover_if_needed=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    assert prepared["ok"] is True
    assert prepared["state"] == "ready_to_commit"
    assert prepared["credentialReused"] is False


def test_catalog_connect_rejects_provider_without_http_endpoint(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {"providers": {}, "roles": {}, "bindings": {"agents": {}}}
    _install_model_config(module, monkeypatch, config)
    provider = _catalog_provider(base_url="")
    provider["channels"] = []
    provider["defaultChannelId"] = ""
    catalog = _CatalogStub(
        providers=[provider],
        normalized_models={("provider", "model-a"): _catalog_text_model()},
    )
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)

    with pytest.raises(module.ConfigBrokerError) as blocked:
        module.ConfigBrokerService().prepare_catalog_model(
            provider_id="provider",
            model_id="model-a",
            discover_if_needed=False,
            owner_id="owner",
            session_id="session",
            run_id="run",
        )

    assert blocked.value.code == "catalog_connection_plan_invalid"


def test_media_model_verification_uses_static_runtime_contract(monkeypatch) -> None:
    import core.config_broker_service as module

    service = module.ConfigBrokerService()
    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(
        service,
        "_verify_committed_model_static",
        lambda _provider_id, _model_id, _config: {
            "ok": True,
            "status": "configured",
            "summary": "media contract configured",
            "verifier": "static_runtime_contract",
        },
    )
    monkeypatch.setattr(
        module,
        "_get_model_connection_tester",
        lambda: pytest.fail("media verification must not invoke the generic chat connection tester"),
    )

    result = service._verify_committed_model(
        {
            "providerId": "provider",
            "modelId": "image-model",
            "model": {
                "type": "IMAGE",
                "capabilityClass": "media_generation",
                "endpointBinding": {"adapter": "openai_images"},
            },
        }
    )

    assert result == {
        "ok": True,
        "status": "configured",
        "summary": "模型配置已按运行时静态合同保存；真实连接、流式和工具续写需显式执行连接测试。",
        "verifier": "static_runtime_contract",
    }


def test_chat_model_save_uses_static_contract_and_leaves_live_probe_explicit(monkeypatch) -> None:
    import core.config_broker_service as module

    service = module.ConfigBrokerService()
    monkeypatch.setattr(module.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(
        service,
        "_verify_committed_model_static",
        lambda provider_id, model_id, _config: {
            "ok": True,
            "status": "configured",
            "summary": f"{provider_id}::{model_id}",
            "verifier": "static_runtime_contract",
        },
    )
    monkeypatch.setattr(
        module,
        "_get_model_connection_tester",
        lambda: pytest.fail("saving a model must not run the explicit live capability suite"),
    )

    result = service._verify_committed_model(
        {
            "providerId": "provider",
            "modelId": "model-a",
            "model": {"type": "TEXT"},
        }
    )

    assert result["ok"] is True
    assert result["verifier"] == "static_runtime_contract"
    assert "显式执行连接测试" in result["summary"]


def test_managed_catalog_recovery_transaction_is_reversible(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module
    from core.model_provider_catalog import ModelProviderCatalog

    builtin_path = tmp_path / "provider_catalog.json"
    custom_path = tmp_path / "custom.json"
    managed_path = tmp_path / "managed.json"
    builtin_path.write_text('{"version":1,"providers":[]}', encoding="utf-8")
    catalog = ModelProviderCatalog(
        path=builtin_path,
        custom_path=custom_path,
        managed_path=managed_path,
    )
    first = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    catalog.upsert_managed_provider(first)
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Updated"})
    invalid_bytes = b"{invalid-overlay"
    managed_path.write_bytes(invalid_bytes)

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)
    service = module.ConfigBrokerService()

    prepared = service.prepare_catalog_recovery(
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert catalog.load_managed()["providers"] == [first]
    assert catalog.managed_recovery_state()["rejectedExists"] is True

    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")

    assert rolled_back["state"] == "rolled_back"
    assert managed_path.read_bytes() == invalid_bytes
    assert catalog.managed_recovery_state()["rejectedExists"] is False


def test_catalog_replace_restores_complete_provider_model_snapshot(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    _install_model_config(module, monkeypatch, config)
    provider = _catalog_provider(auth={"type": "none"})
    provider["singleActiveModel"] = True
    catalog = _CatalogStub(
        providers=[provider],
        normalized_models={("provider", "model-a"): _catalog_text_model("model-a")},
    )
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)
    def upsert_provider_model_records(**kwargs):
        provider_id = kwargs["provider_id"]
        model_id = kwargs["model_id"]
        provider_data = dict(config["providers"].get(provider_id) or {})
        models = {} if kwargs["replace_provider_models"] else dict(provider_data.get("models") or {})
        models[model_id] = _copy(kwargs["model_patch"])
        config["providers"][provider_id] = {"provider": _copy(kwargs["provider_patch"]), "models": models}
        return {"config": _copy(config)}

    monkeypatch.setattr(module.model_control_plane, "upsert_provider_model_records", upsert_provider_model_records)
    monkeypatch.setattr(module.ConfigBrokerService, "_verify_committed_model", lambda *_args: {"ok": True, "status": "ok", "summary": "ok", "verifier": "test"})
    service = module.ConfigBrokerService()

    prepared = service.prepare_catalog_model(
        provider_id="provider",
        model_id="model-a",
        discover_if_needed=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    assert set(config["providers"]["provider"]["models"]) == {"model-a"}

    assert service.rollback(prepared["transactionId"], owner_id="owner")["state"] == "rolled_back"
    assert config["providers"]["provider"]["models"] == _model_config()["providers"]["provider"]["models"]


def test_model_remove_rechecks_role_and_agent_dependencies_inside_commit(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_model_record_change(
        model_ref="provider::target",
        operation="remove",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["bindings"]["agents"]["late"] = {"modelId": "provider::target"}

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == "model_still_bound"
    assert "target" in config["providers"]["provider"]["models"]
    assert config["bindings"]["agents"]["late"]["modelId"] == "provider::target"


def test_role_assignment_cas_stops_when_target_model_changes(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    _install_model_config(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_assignment(
        role="supervisor",
        model_ref="provider::target",
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["providers"]["provider"]["models"]["target"]["isEnabled"] = False

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "conflict"
    assert committed["error"]["code"] == "config_transaction_stale"
    assert config["roles"] == {}


@pytest.mark.parametrize(
    "governance",
    [
        {"maxLocalRetries": "1"},
        {"providerErrorRateThreshold": float("nan")},
        {"budgets": {"runMaxCost": -0.1}},
        {"budgets": {"projectBudgets": [{"projectId": "one"}, {"projectId": "one"}]}},
    ],
)
def test_model_policy_rejects_non_strict_or_non_finite_values(tmp_path, monkeypatch, governance) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    _install_model_config(module, monkeypatch, _model_config())

    with pytest.raises(module.ConfigBrokerError):
        module.ConfigBrokerService().prepare_model_policy(
            governance=governance,
            routing_policies=None,
            role_parameters=None,
            owner_id="owner",
            session_id="session",
            run_id="run",
        )


def test_model_prepare_uses_selected_channel_as_safety_and_commit_target(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    _install_model_config(module, monkeypatch, {"providers": {}, "roles": {}, "bindings": {"agents": {}}})
    service = module.ConfigBrokerService()
    prepared = service.prepare_model(
        provider_id="provider",
        model_id="model",
        provider_name="Provider",
        base_url="https://root.provider.test/v1",
        api_standard="openai",
        channel_id="responses",
        wire_protocol="openai.responses",
        model_type="TEXT",
        context_window=32_000,
        max_tokens=4_000,
        capabilities={"chat": True},
        evidence_refs=[],
        credential_required=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
        provider_config={
            "authContract": {"type": "none"},
            "channels": [{
                "id": "responses",
                "baseUrl": "https://egress.provider.test/v1",
                "apiStandard": "openai",
                "wireProtocols": ["openai.responses"],
                "authContract": {"type": "none"},
                "defaultWireProtocol": "openai.responses",
            }],
            "defaultChannelId": "responses",
        },
    )
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)

    assert transaction["proposed"]["provider"]["base_url"] == "https://egress.provider.test/v1"
    assert transaction["proposed"]["model"]["endpointBinding"]["channelId"] == "responses"
    assert transaction["proposed"]["model"]["endpointBinding"]["wireProtocol"] == "openai.responses"


def test_model_prepare_derives_credential_requirement_from_auth_contract(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    _install_model_config(module, monkeypatch, {"providers": {}, "roles": {}, "bindings": {"agents": {}}})
    service = module.ConfigBrokerService()

    with pytest.raises(module.ConfigBrokerError) as mismatch:
        service.prepare_model(
            provider_id="provider",
            model_id="image-model",
            provider_name="Provider",
            base_url="https://api.provider.test/v1",
            api_standard="openai",
            model_type="IMAGE",
            context_window=None,
            max_tokens=None,
            capabilities={},
            evidence_refs=[],
            credential_required=False,
            owner_id="owner",
            session_id="session",
            run_id="run",
            model_config={
                "capabilityClass": "media_generation",
                "parameterProfile": "media_generation",
            },
        )
    assert mismatch.value.code == "provider_credential_contract_mismatch"

    prepared = service.prepare_model(
        provider_id="local-no-auth",
        model_id="image-model",
        provider_name="Local no-auth",
        base_url="http://127.0.0.1:8188",
        api_standard="comfyui",
        model_type="IMAGE",
        context_window=None,
        max_tokens=None,
        capabilities={},
        evidence_refs=[],
        credential_required=False,
        owner_id="owner",
        session_id="session",
        run_id="run",
        provider_config={"authContract": {"type": "none"}},
        model_config={
            "capabilityClass": "media_generation",
            "parameterProfile": "media_generation",
        },
    )
    assert prepared["state"] == "ready_to_commit"


def test_static_media_verifier_rejects_missing_managed_credential(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = {"providers": {}, "roles": {}, "bindings": {"agents": {}}}
    snapshot = _install_model_config(module, monkeypatch, config)

    def upsert(
        *,
        provider_id,
        provider_patch,
        model_id,
        model_patch,
        source,
        replace_provider_models,
        precondition,
        before_persist,
    ):
        current = snapshot()
        precondition(current)
        current["providers"][provider_id] = {
            "provider": _copy(provider_patch),
            "models": {model_id: _copy(model_patch)},
        }
        before_persist(current)
        config.clear()
        config.update(_copy(current))
        return {"config": snapshot()}

    def record(model_ref, _config=None):
        provider_id, model_id = model_ref.split("::", 1)
        provider = dict(config.get("providers", {}).get(provider_id) or {})
        if model_id not in dict(provider.get("models") or {}):
            return None
        return {
            "provider": _copy(provider.get("provider") or {}),
            "model": _copy((provider.get("models") or {}).get(model_id) or {}),
        }

    monkeypatch.setattr(module.model_control_plane, "upsert_provider_model_records", upsert)
    monkeypatch.setattr(module.model_control_plane, "get_model_record", record)
    service = module.ConfigBrokerService()
    before = snapshot()
    transaction = service._insert_transaction(
        target_kind="model",
        target_id="provider::image-model",
        operation="upsert",
        state="ready_to_commit",
        owner_id="owner",
        session_id="session",
        run_id="run",
        before=before,
        proposed={
            "providerId": "provider",
            "modelId": "image-model",
            "provider": {
                "name": "Provider",
                "base_url": "https://api.provider.test/v1",
                "api_standard": "openai",
                "authContract": {"type": "api_key"},
            },
            "model": {
                "type": "IMAGE",
                "capabilityClass": "media_generation",
                "parameterProfile": "media_generation",
            },
            "credentialRequired": True,
            "newCredentialRefs": [],
        },
    )

    committed = service.commit(transaction["transactionId"], owner_id="owner")

    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == "model_connection_validation_failed"
    assert config["providers"] == {}


def test_catalog_recovery_finalize_uses_recovered_and_rejected_digest_cas(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module
    from core.model_provider_catalog import ModelProviderCatalog

    builtin_path = tmp_path / "provider_catalog.json"
    managed_path = tmp_path / "managed.json"
    builtin_path.write_text('{"version":1,"providers":[]}', encoding="utf-8")
    catalog = ModelProviderCatalog(path=builtin_path, custom_path=tmp_path / "custom.json", managed_path=managed_path)
    catalog.upsert_managed_provider({
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    })
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Updated"})
    managed_path.write_bytes(b"{invalid-overlay")
    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    monkeypatch.setattr(module, "_get_model_provider_catalog", lambda: catalog)
    service = module.ConfigBrokerService()

    recovery = service.prepare_catalog_recovery(owner_id="owner", session_id="session", run_id="run")
    assert service.commit(recovery["transactionId"], owner_id="owner")["state"] == "committed"
    finalization = service.prepare_catalog_recovery_finalize(owner_id="owner", session_id="session", run_id="run")
    committed = service.commit(finalization["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert committed["result"] == {"recovered": False, "finalized": True, "originalIsolated": False}
    assert catalog.managed_recovery_state()["rejectedExists"] is False
