from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def principal(subject="verified-owner"):
    from core.auth_context import EngineAuthContext
    context = EngineAuthContext(subject, "session", "owner", "admin", None, 0, 9999999999)
    return Request({"type": "http", "method": "POST", "path": "/v1/config-broker/model-policy/prepare",
                    "headers": [(b"x-v8-agent-os-user-email", b"forged-owner")],
                    "state": {"engine_auth_context": context}})


@pytest.fixture
def broker(tmp_path, monkeypatch):
    from core.database import DatabaseManager
    from core import config_broker_service as service_module
    from core import storage as storage_module
    from api import platform_routes
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(service_module, "db", DatabaseManager(tmp_path / "state.db"))
    service = service_module.ConfigBrokerService()
    monkeypatch.setattr(platform_routes, "config_broker_service", service)
    return platform_routes, service_module


def test_budget_route_uses_verified_owner_and_real_prepare_commit_rollback(broker):
    routes, service_module = broker
    request = principal()
    before = deepcopy(service_module.model_control_plane.get_config()["governance"]["budgets"])
    plan = asyncio.run(routes.config_broker_prepare_model_policy(request, {
        "governance": {"budgets": {"runMaxTokens": 12345, "globalDailyTokenLimit": 1000000, "globalDailyCostLimit": 2.5}}
    }))
    assert service_module.model_control_plane.get_config()["governance"]["budgets"] == before
    with pytest.raises(HTTPException) as wrong_owner:
        asyncio.run(routes.config_broker_transaction(plan["transactionId"], principal("another-owner")))
    assert wrong_owner.value.status_code == 403
    with pytest.raises(HTTPException) as wrong_digest:
        asyncio.run(routes.config_broker_commit(plan["transactionId"], request, {"planDigest": "wrong"}))
    assert wrong_digest.value.status_code == 409
    result = asyncio.run(routes.config_broker_commit(plan["transactionId"], request, {"planDigest": plan["planDigest"]}))
    assert result["state"] == "committed"
    after = service_module.model_control_plane.get_config()["governance"]["budgets"]
    assert after["runMaxTokens"] == 12345
    assert after["globalDailyTokenLimit"] == 1000000
    assert after["globalDailyCostLimit"] == 2.5
    restored = asyncio.run(routes.config_broker_rollback(plan["transactionId"], request))
    assert restored["state"] == "rolled_back"
    assert service_module.model_control_plane.get_config()["governance"]["budgets"] == before


@pytest.mark.parametrize("payload", [
    {"governance": {"budgets": {"runMaxTokens": -1}}},
    {"governance": {"budgets": {"apiKey": "synthetic-secret"}}},
    {"governance": {"budgets": {"accessToken": 12345}}},
    {"governance": ["wrong-type"]},
])
def test_policy_route_rejects_invalid_or_credential_payload_without_committing(broker, payload):
    routes, service_module = broker
    before = deepcopy(service_module.model_control_plane.get_config())
    with pytest.raises(HTTPException):
        asyncio.run(routes.config_broker_prepare_model_policy(principal(), payload))
    assert service_module.model_control_plane.get_config() == before
