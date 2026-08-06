from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from core.database import DatabaseManager


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _install_model_control_plane(module, monkeypatch, config: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = {
        "supervisor": {"label": "Supervisor", "capabilityClasses": ["chat_general"]},
        "summary": {"label": "Summary", "capabilityClasses": ["chat_general"]},
        "embedding": {"label": "Embedding", "capabilityClasses": ["embedding"]},
    }
    mutations: list[dict[str, Any]] = []

    def snapshot() -> dict[str, Any]:
        return _copy(config)

    def model_record(model_ref: str, current: dict[str, Any]) -> dict[str, Any] | None:
        parts = str(model_ref or "").split("::", 1)
        if len(parts) != 2:
            return None
        provider_id, model_id = parts
        model = dict(
            ((current.get("providers") or {}).get(provider_id) or {}).get("models", {}).get(model_id) or {}
        )
        if not model:
            return None
        return {
            "model_ref": f"{provider_id}::{model_id}",
            "model_id": model_id,
            "provider_id": provider_id,
            "model": model,
        }

    def list_models(current: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for provider_id, provider in dict(current.get("providers") or {}).items():
            for model_id, model in dict((provider or {}).get("models") or {}).items():
                model_data = dict(model or {})
                rows.append(
                    {
                        "modelRef": f"{provider_id}::{model_id}",
                        "modelId": model_id,
                        "type": model_data.get("type"),
                        "capabilityClass": model_data.get("capabilityClass"),
                        "capabilities": dict(model_data.get("capabilities") or {}),
                        "eligibility": {
                            "selectable": bool(model_data.get("selectable", True)),
                            "shortLabel": "可用" if model_data.get("selectable", True) else "不可用",
                        },
                    }
                )
        return rows

    def compatible(role_definition: dict[str, Any], record: dict[str, Any]) -> bool:
        allowed = set(role_definition.get("capabilityClasses") or [])
        capability_class = str(dict(record.get("model") or {}).get("capabilityClass") or "")
        return not allowed or capability_class in allowed

    def mutate(mutator):
        proposed = mutator(snapshot())
        config.clear()
        config.update(_copy(proposed))
        mutations.append(snapshot())
        return snapshot()

    monkeypatch.setattr(module.model_control_plane, "get_config", snapshot)
    monkeypatch.setattr(module.model_control_plane, "get_storage_safe_config", snapshot)
    monkeypatch.setattr(module.model_control_plane, "get_role_definitions", lambda _config: deepcopy(definitions))
    monkeypatch.setattr(module.model_control_plane, "get_model_record", model_record)
    monkeypatch.setattr(module.model_control_plane, "list_models", list_models)
    monkeypatch.setattr(module.model_control_plane, "is_model_compatible", compatible)
    monkeypatch.setattr(module.model_control_plane, "mutate_config", mutate)
    return mutations


def _model_config() -> dict[str, Any]:
    return {
        "providers": {
            "provider": {
                "provider": {"name": "Provider"},
                "models": {
                    "chat-new": {"type": "TEXT", "capabilityClass": "chat_general"},
                    "summary-new": {"type": "TEXT", "capabilityClass": "chat_general"},
                    "embed-new": {"type": "EMBEDDING", "capabilityClass": "embedding"},
                    "blocked": {
                        "type": "TEXT",
                        "capabilityClass": "chat_general",
                        "selectable": False,
                    },
                },
            }
        },
        "roles": {
            "supervisor": "provider::chat-old",
            "summary": "provider::summary-old",
            "embedding": "provider::embed-old",
            "unrelated": "provider::keep",
        },
        "bindings": {"agents": {}},
    }


def test_role_bundle_prepare_and_commit_mixed_assign_unbind_once(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    config = _model_config()
    mutations = _install_model_control_plane(module, monkeypatch, config)
    service = module.ConfigBrokerService()

    prepared = service.prepare_role_bindings(
        updates={
            "supervisor": "provider::chat-new",
            "summary": "",
            "embedding": "provider::embed-new",
        },
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)

    assert prepared["state"] == "ready_to_commit"
    assert transaction["targetKind"] == "model_role_bundle"
    assert transaction["proposed"]["updates"] == {
        "supervisor": "provider::chat-new",
        "summary": "",
        "embedding": "provider::embed-new",
    }
    with test_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM config_broker_transactions").fetchone()[0] == 1

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "committed"
    assert len(mutations) == 1
    assert config["roles"] == {
        "supervisor": "provider::chat-new",
        "summary": "",
        "embedding": "provider::embed-new",
        "unrelated": "provider::keep",
    }
    assert committed["result"]["roles"] == {
        "supervisor": "provider::chat-new",
        "summary": "",
        "embedding": "provider::embed-new",
    }


def test_role_bundle_rejects_ineligible_model_without_transaction(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    test_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", test_db)
    config = _model_config()
    _install_model_control_plane(module, monkeypatch, config)

    with pytest.raises(module.ConfigBrokerError) as blocked:
        module.ConfigBrokerService().prepare_role_bindings(
            updates={"summary": "", "supervisor": "provider::blocked"},
            owner_id="owner",
            session_id="session",
            run_id="run",
        )

    assert blocked.value.code == "model_role_ineligible"
    with test_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM config_broker_transactions").fetchone()[0] == 0
    assert config["roles"]["summary"] == "provider::summary-old"


def test_role_bundle_stale_role_or_model_never_partially_writes(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    mutations = _install_model_control_plane(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    stale_role = service.prepare_role_bindings(
        updates={"supervisor": "provider::chat-new", "summary": ""},
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["roles"]["summary"] = "provider::concurrent"

    role_conflict = service.commit(stale_role["transactionId"], owner_id="owner")

    assert role_conflict["state"] == "conflict"
    assert role_conflict["error"]["code"] == "config_transaction_stale"
    assert config["roles"]["supervisor"] == "provider::chat-old"
    assert config["roles"]["summary"] == "provider::concurrent"
    assert mutations == []

    stale_model = service.prepare_role_bindings(
        updates={"supervisor": "provider::chat-new", "summary": ""},
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    config["providers"]["provider"]["models"]["chat-new"]["revision"] = 2

    model_conflict = service.commit(stale_model["transactionId"], owner_id="owner")

    assert model_conflict["state"] == "conflict"
    assert model_conflict["error"]["code"] == "config_transaction_stale"
    assert config["roles"]["supervisor"] == "provider::chat-old"
    assert config["roles"]["summary"] == "provider::concurrent"
    assert mutations == []


def test_role_bundle_rollback_restores_only_touched_roles(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    _install_model_control_plane(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_bindings(
        updates={"supervisor": "provider::chat-new", "summary": ""},
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    assert service.commit(prepared["transactionId"], owner_id="owner")["state"] == "committed"
    config["roles"]["unrelated"] = "provider::later"

    rolled_back = service.rollback(prepared["transactionId"], owner_id="owner")

    assert rolled_back["state"] == "rolled_back"
    assert config["roles"]["supervisor"] == "provider::chat-old"
    assert config["roles"]["summary"] == "provider::summary-old"
    assert config["roles"]["unrelated"] == "provider::later"


def test_role_bundle_projection_mismatch_rolls_back_entire_bundle(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    _install_model_control_plane(module, monkeypatch, config)
    mutation_count = 0

    def mutate_with_projection_loss(mutator):
        nonlocal mutation_count
        mutation_count += 1
        proposed = mutator(_copy(config))
        if mutation_count == 1:
            proposed["roles"]["summary"] = "provider::projection-loss"
        config.clear()
        config.update(_copy(proposed))
        return _copy(config)

    monkeypatch.setattr(module.model_control_plane, "mutate_config", mutate_with_projection_loss)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_bindings(
        updates={"supervisor": "provider::chat-new", "summary": ""},
        owner_id="owner",
        session_id="session",
        run_id="run",
    )

    committed = service.commit(prepared["transactionId"], owner_id="owner")

    assert committed["state"] == "rolled_back"
    assert committed["error"]["code"] == "model_role_bundle_projection_mismatch"
    assert config["roles"]["supervisor"] == "provider::chat-old"
    assert config["roles"]["summary"] == "provider::summary-old"
    assert mutation_count == 2


def test_role_bundle_startup_reconcile_restores_interrupted_commit(tmp_path, monkeypatch) -> None:
    import core.config_broker_service as module

    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    config = _model_config()
    _install_model_control_plane(module, monkeypatch, config)
    service = module.ConfigBrokerService()
    prepared = service.prepare_role_bindings(
        updates={"supervisor": "provider::chat-new", "summary": ""},
        owner_id="owner",
        session_id="session",
        run_id="run",
    )
    transaction = service.get_transaction(prepared["transactionId"], owner_id="owner", include_private=True)
    config["roles"].update({"supervisor": "provider::chat-new", "summary": ""})
    validation = dict(transaction["validation"])
    validation["targetWorkingDigest"] = module._digest(
        service._target_snapshot(transaction["targetKind"], transaction["targetId"], config)
    )
    service._update_transaction(
        prepared["transactionId"],
        state="committing",
        validation_json=module._json(validation),
    )

    reconciled = module.ConfigBrokerService().reconcile_incomplete_transactions()

    assert reconciled["ok"] is True
    assert reconciled["transactions"] == [
        {"transactionId": prepared["transactionId"], "state": "rolled_back"}
    ]
    assert config["roles"]["supervisor"] == "provider::chat-old"
    assert config["roles"]["summary"] == "provider::summary-old"
