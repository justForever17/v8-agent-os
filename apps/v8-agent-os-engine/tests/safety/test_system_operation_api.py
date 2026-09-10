from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from api import system_operation_routes as routes
from core.database import DatabaseManager
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.system_operations.service import SystemOperationService


@pytest.fixture
def api(tmp_path, monkeypatch):
    from core.system_operations import accounts
    monkeypatch.setattr(accounts, "validate_account", lambda action, username, domain: (username, domain))
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session", "Fixture", user_id="owner")
    database.create_run_record("run", "session", user_id="owner")
    service = SystemOperationService(database, CredentialRefStore(MemoryCredentialBackend()))
    monkeypatch.setattr(routes, "get_internal_secret", lambda: "fixture-service-secret")
    monkeypatch.setattr(routes, "system_operation_service", service)
    from core import database as database_module
    monkeypatch.setattr(database_module, "db", database)
    from core.system_operations import platform
    monkeypatch.setattr(platform, "platform_status", lambda: {"os": "test"})
    app = FastAPI()
    app.include_router(routes.router)
    headers = {"x-v8-agent-os-secret": "fixture-service-secret", "x-v8-agent-os-user-email": "owner"}
    return TestClient(app), service, headers


def test_settings_never_return_secret_or_reference(api):
    client, _, headers = api
    response = client.put("/system-operations/credentials/unlock", json={"username": "fixture-account", "domain": "", "password": "fixture-password"}, headers=headers)
    assert response.status_code == 200
    settings = client.get("/system-operations/settings", headers=headers)
    assert settings.json()["profiles"]["unlock"]["configured"]
    assert "fixture-password" not in settings.text and "cred:v8-" not in settings.text
    other = client.get("/system-operations/settings", headers={**headers, "x-v8-agent-os-user-email": "other"})
    assert not other.json()["profiles"]["unlock"]["configured"]
    assert client.delete("/system-operations/credentials/unlock", headers=headers).status_code == 200
    assert not client.get("/system-operations/settings", headers=headers).json()["profiles"]["unlock"]["configured"]


@pytest.mark.parametrize("headers", [{}, {"x-v8-agent-os-user-email": "owner"}, {"x-v8-agent-os-user-email": "owner", "x-v8-agent-os-secret": "wrong"}])
def test_config_requires_authenticated_internal_owner(api, headers):
    client, _, _ = api
    assert client.get("/system-operations/settings", headers=headers).status_code == 401
    assert client.put("/system-operations/credentials/unlock", json={"password": "never-reflect-me"}, headers=headers).status_code == 401


def test_invalid_secret_form_never_reflects_input_in_validation_error(api):
    client, _, headers = api
    for payload in [{"password": "never-reflect-me", "username": ""}, {"password": "never-reflect-me", "extra": True}]:
        response = client.put("/system-operations/credentials/unlock", json=payload, headers=headers)
        assert response.status_code == 422
        assert "never-reflect-me" not in response.text


def test_existing_approval_route_requires_matching_operation_owner_and_scope(api):
    _, service, headers = api
    service.configure(owner="owner", action="unlock", username="fixture", domain="", password="fixture-secret")
    captured = []
    def pending(op):
        captured.append(op)
        raise RuntimeError("approval pending")
    with pytest.raises(RuntimeError):
        service.execute(payload={"action": "unlock"}, context={"user_id": "owner", "session_id": "session", "run_id": "run", "agent_id": "supervisor", "runtime_kind": "chat"}, tool_call_id="call", authorize=pending)
    approval = {"session_id": "session", "run_id": "run", "request": {"safety": {"details": {"operationId": captured[0]["operationId"]}}}}
    def request(values):
        return Request({"type": "http", "headers": [(key.encode(), value.encode()) for key, value in values.items()]})
    routes.validate_system_operation_approval(approval, request(headers))
    for invalid_headers, invalid_approval in [({}, approval), ({**headers, "x-v8-agent-os-user-email": "other"}, approval), (headers, {**approval, "run_id": "other"})]:
        with pytest.raises(HTTPException):
            routes.validate_system_operation_approval(invalid_approval, request(invalid_headers))
    # A late click must not let the approval owner resurrect a cancelled run.
    with service.database.get_connection() as conn:
        conn.execute("UPDATE run_records SET status='cancelled' WHERE id='run'")
        conn.commit()
    with pytest.raises(HTTPException) as rejected:
        routes.validate_system_operation_approval(approval, request(headers))
    assert rejected.value.status_code == 409


def test_component_setup_requires_admin_and_fixed_known_action(api, monkeypatch):
    from core.system_operations import setup
    client, _, headers = api
    called = []
    monkeypatch.setattr(setup, "begin_setup", lambda component, action: called.append((component, action)) or {"requested": True})
    assert client.post("/system-operations/components/unlock/install", headers=headers).status_code == 403
    assert not called
    assert client.post("/system-operations/components/unlock/install", headers={**headers, "x-v8-admin-role": "ADMIN"}).status_code == 200
    assert called == [("unlock", "install")]
