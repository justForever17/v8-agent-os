from __future__ import annotations

import asyncio
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import HTTPException


class _BrokerStub:
    def __init__(
        self,
        *,
        on_commit: Callable[[str], None] | None = None,
        commit_result: dict[str, Any] | None = None,
        prepare_error: Exception | None = None,
    ) -> None:
        self.on_commit = on_commit
        self.commit_result = commit_result or {"ok": True, "state": "committed"}
        self.prepare_error = prepare_error
        self.role_prepares: list[dict[str, Any]] = []
        self.policy_prepares: list[dict[str, Any]] = []
        self.commits: list[tuple[str, str]] = []

    def prepare_role_bindings(self, **kwargs: Any) -> dict[str, Any]:
        if self.prepare_error:
            raise self.prepare_error
        self.role_prepares.append(deepcopy(kwargs))
        return {"transactionId": f"role-{len(self.role_prepares)}", "state": "ready_to_commit"}

    def prepare_model_policy(self, **kwargs: Any) -> dict[str, Any]:
        if self.prepare_error:
            raise self.prepare_error
        self.policy_prepares.append(deepcopy(kwargs))
        return {"transactionId": f"policy-{len(self.policy_prepares)}", "state": "ready_to_commit"}

    def commit(self, transaction_id: str, *, owner_id: str) -> dict[str, Any]:
        self.commits.append((transaction_id, owner_id))
        if self.on_commit:
            self.on_commit(transaction_id)
        return deepcopy(self.commit_result)


def _forbid_direct_model_save(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("active internal config writes must use Config Broker")


def test_internal_config_route_imports_keep_config_broker_lazy() -> None:
    engine_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import api.config_registry_routes; "
                "import api.knowledge_routes; "
                "raise SystemExit(1 if 'core.config_broker_service' in sys.modules else 0)"
            ),
        ],
        cwd=engine_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_config_registry_role_updates_use_one_bundle_and_preserve_response(monkeypatch) -> None:
    from api import config_registry_routes as routes

    config = {
        "roles": {"supervisor": "provider::old", "summary": "provider::summary-old"},
        "roleParameters": {},
    }

    def on_commit(_transaction_id: str) -> None:
        config["roles"].update(broker.role_prepares[0]["updates"])

    broker = _BrokerStub(on_commit=on_commit)
    monkeypatch.setattr(routes, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(routes, "validate_text_role_model_window", lambda _role, _model_ref: {"ok": True})
    monkeypatch.setattr(routes.model_control_plane, "get_config", lambda: deepcopy(config))
    monkeypatch.setattr(routes.model_control_plane, "save_config", _forbid_direct_model_save)

    result = routes._update_role_bindings(
        {"supervisor": "provider::new", "summary": None}
    )

    assert result == {"supervisor": "provider::new", "summary": ""}
    assert len(broker.role_prepares) == 1
    assert broker.role_prepares[0]["updates"] == {
        "supervisor": "provider::new",
        "summary": "",
    }
    assert broker.commits == [("role-1", "local-admin")]


def test_config_registry_role_parameters_use_policy_bundle_and_normalize_temperature(monkeypatch) -> None:
    from api import config_registry_routes as routes

    config = {
        "roles": {},
        "roleParameters": {
            "supervisor": {"temperature": 0.7},
            "summary": {"temperature": 0.4},
        },
    }

    def on_commit(_transaction_id: str) -> None:
        patch = broker.policy_prepares[0]["role_parameters"]
        for role, values in patch.items():
            config["roleParameters"][role] = {
                **dict(config["roleParameters"].get(role) or {}),
                **values,
            }

    broker = _BrokerStub(on_commit=on_commit)
    monkeypatch.setattr(routes, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(routes.model_control_plane, "get_config", lambda: deepcopy(config))
    monkeypatch.setattr(routes.model_control_plane, "save_config", _forbid_direct_model_save)

    result = routes._update_role_parameters(
        {
            "supervisor": {"temperature": 0},
            "summary": {"temperature": 9},
        }
    )

    assert len(broker.policy_prepares) == 1
    assert broker.policy_prepares[0]["governance"] is None
    assert broker.policy_prepares[0]["routing_policies"] is None
    assert broker.policy_prepares[0]["role_parameters"] == {
        "supervisor": {"temperature": None},
        "summary": {"temperature": 2.0},
    }
    assert result == {
        "supervisor": {"temperature": None},
        "summary": {"temperature": 2.0},
    }


def test_config_registry_stale_bundle_preserves_conflict_status(monkeypatch) -> None:
    from api import config_registry_routes as routes

    broker = _BrokerStub(
        commit_result={
            "ok": False,
            "state": "conflict",
            "error": {"code": "config_transaction_stale", "message": "stale"},
        }
    )
    monkeypatch.setattr(routes, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(routes, "validate_text_role_model_window", lambda _role, _model_ref: {"ok": True})

    with pytest.raises(HTTPException) as stale:
        routes._update_role_bindings({"supervisor": "provider::new"})

    assert stale.value.status_code == 409
    assert stale.value.detail == {"code": "config_transaction_stale", "message": "stale"}


def test_knowledge_memory_config_commits_three_roles_as_one_bundle(monkeypatch) -> None:
    from api import knowledge_routes as routes

    saved: list[dict[str, Any]] = []
    broker = _BrokerStub()
    monkeypatch.setattr(routes, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(routes.storage, "get_memory_config", lambda: {})
    monkeypatch.setattr(routes.storage, "save_memory_config", lambda value: saved.append(deepcopy(value)))
    monkeypatch.setattr(routes, "normalize_memory_extraction_config", lambda value: dict(value))
    monkeypatch.setattr(routes.model_control_plane, "save_config", _forbid_direct_model_save)

    result = asyncio.run(
        routes.update_memory_config(
            {
                "extraction_model": "none",
                "embedding_model": "provider::embed",
                "reranker_model": "",
                "recall_strategy": "semantic",
            }
        )
    )

    assert result == {"status": "success"}
    assert len(broker.role_prepares) == 1
    assert broker.role_prepares[0]["updates"] == {
        "extraction": "",
        "embedding": "provider::embed",
        "reranker": "",
    }
    assert broker.commits == [("role-1", "local-admin")]
    assert saved == [{"recall_strategy": "semantic"}]


def test_knowledge_ineligible_model_does_not_save_memory_config(monkeypatch) -> None:
    from api import knowledge_routes as routes
    from core.config_broker_service import ConfigBrokerError

    saved: list[dict[str, Any]] = []
    broker = _BrokerStub(
        prepare_error=ConfigBrokerError(
            "目标模型不满足该功能的运行条件。",
            code="model_role_ineligible",
            status_code=409,
        )
    )
    monkeypatch.setattr(routes, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(routes.storage, "get_memory_config", lambda: {})
    monkeypatch.setattr(routes.storage, "save_memory_config", lambda value: saved.append(deepcopy(value)))
    monkeypatch.setattr(routes, "normalize_memory_extraction_config", lambda value: dict(value))

    with pytest.raises(HTTPException) as blocked:
        asyncio.run(routes.update_memory_config({"extraction_model": "provider::blocked"}))

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "model_role_ineligible"
    assert saved == []


def test_knowledge_context_config_keeps_success_contract(monkeypatch) -> None:
    from api import knowledge_routes as routes

    saved: list[dict[str, Any]] = []
    broker = _BrokerStub()
    monkeypatch.setattr(routes, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(routes.storage, "save_context_config", lambda value: saved.append(deepcopy(value)))

    result = asyncio.run(
        routes.update_context_config(
            {"policy": {"compression": True}, "bindings": {"summary_model": ""}}
        )
    )

    assert result == {"status": "success"}
    assert broker.role_prepares[0]["updates"] == {"summary": ""}
    assert saved == [{"compression": True}]
