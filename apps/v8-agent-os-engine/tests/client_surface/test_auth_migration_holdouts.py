from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request
from starlette.requests import Request as StarletteRequest

from api import client_routes
from core import auth_context, client_identity
from core.client_auth_boundary import EngineControlBoundary
from core.client_identity.service import ClientIdentityService
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend


@pytest.fixture
def identity(tmp_path, monkeypatch):
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()))
    owner = service.owners.bootstrap(login="owner", name="Owner", now=service.clock())
    service.initialize()
    phone = service.create_session(name="Phone", surface="phone")
    local = service.create_session(name="CLI", surface="cli", hidden=True)
    monkeypatch.setattr(client_identity, "_service", service)
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-internal")
    return SimpleNamespace(service=service, owner=owner, phone=phone, local=local)


def _request(app, method, path, *, headers=None, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://engine.local") as client:
            return await client.request(method, path, headers=headers or {}, **kwargs)
    return asyncio.run(run())


def test_engine_boundary_rejects_private_v1_before_handler_and_accepts_only_secret(identity):
    app = FastAPI()
    calls = []

    @app.get("/v1/private")
    def private():
        calls.append("called")
        return {"ok": True}

    app.add_middleware(EngineControlBoundary, secret_reader=lambda: "fixture-internal")
    denied = _request(app, "GET", "/v1/private", headers={"x-v8-agent-os-user-email": "owner"})
    assert denied.status_code == 401
    assert denied.headers["x-v8-auth-stage"] == "pre_execution"
    assert calls == []
    allowed = _request(app, "GET", "/v1/private", headers={"x-v8-agent-os-secret": "fixture-internal"})
    assert allowed.status_code == 200 and allowed.json() == {"ok": True}
    assert calls == ["called"]


def test_phone_bearer_cannot_enter_local_only_desktop_surface(identity):
    app = FastAPI()
    internal = APIRouter()

    @internal.get("/config-registry/desktop-pet")
    def desktop_config():
        return {"ok": True, "owner": "local"}

    app.state.client_internal_router = internal
    app.include_router(client_routes.router)
    phone = _request(app, "GET", "/api/client/desktop-pet/config",
                     headers={"Authorization": "Bearer " + identity.phone["accessToken"]})
    assert phone.status_code == 403
    local = _request(app, "GET", "/api/client/desktop-pet/config",
                     headers={"x-v8-agent-os-secret": "fixture-internal"})
    assert local.status_code == 200 and local.json()["owner"] == "local"


def test_revoked_phone_token_stops_at_auth_and_does_not_replay_internal_action(identity):
    app = FastAPI()
    internal = APIRouter()
    calls = []

    @internal.post("/sessions")
    async def create(request: Request):
        calls.append(await request.json())
        return {"id": "should-not-exist"}

    app.state.client_internal_router = internal
    app.state.client_database = SimpleNamespace(get_session=lambda _id: None)
    app.include_router(client_routes.router)
    identity.service.revoke(identity.owner["id"], identity.phone["deviceId"])
    response = _request(app, "POST", "/api/client/conversations",
                        headers={"Authorization": "Bearer " + identity.phone["accessToken"]},
                        json={"title": "must not execute"})
    assert response.status_code == 401
    assert response.headers["x-v8-auth-stage"] == "pre_execution"
    assert calls == []


def test_config_transaction_actor_ignores_legacy_user_header(identity):
    from api.platform_routes import _config_actor

    request = StarletteRequest({
        "type": "http", "method": "GET", "path": "/v1/config-broker/transactions/x",
        "query_string": b"", "headers": [
            (b"x-v8-agent-os-secret", b"fixture-internal"),
            (b"x-v8-agent-os-user-email", b"attacker@example.invalid"),
            (b"x-v8-admin-role", b"ROOT"),
        ],
    })
    assert _config_actor(request) == identity.owner["id"]
