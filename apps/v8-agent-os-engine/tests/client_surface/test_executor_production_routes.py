"""Production registration order with real gateway, auth and isolated executor IO."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import client_routes, device_executor_routes
from core import client_identity
from core.client_identity.service import ClientIdentityService
from core.remote_link.phone_gateway import create_phone_gateway_app
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from runtimes.network_supervisor.executors.service import ExecutorService
from tests.scripts.run_executor_media_server_smoke import include_production_client_routers


def production_client_app():
    # Execute the registrations from main.py in their original order. Importing
    # the full production lifespan would start unrelated services; inventing a
    # hand-written route order here previously concealed the catch-all shadowing.
    app = FastAPI()
    include_production_client_routers(app, client_routes, device_executor_routes)
    app.state.route_owners = []

    @app.middleware("http")
    async def observe_endpoint(request, call_next):
        response = await call_next(request)
        app.state.route_owners.append(request.scope.get("endpoint"))
        return response

    return app


@pytest.mark.parametrize("operation,method,handler", [
    ("tickets", "POST", "ticket"),
    ("list", "GET", "devices"),
    ("grants", "PUT", "grant"),
    ("revoke", "DELETE", "revoke"),
    ("media", "DELETE", "delete_owned_media"),
    ("command", "GET", "command"),
    ("cancel", "POST", "cancel"),
    ("reconcile", "POST", "reconcile"),
])
def test_production_executor_routes_reach_dedicated_handlers(tmp_path, monkeypatch, operation, method, handler):
    identity = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()))
    owner = identity.owners.bootstrap(login="fixture", name="Fixture", now=identity.clock())["id"]
    service = ExecutorService(identity)
    monkeypatch.setattr(client_identity, "_service", identity)
    monkeypatch.setattr(device_executor_routes, "get_executor_service", lambda: service)
    human = identity.create_session(name="Phone fixture", surface="phone")
    body = None
    path = "/api/client/executors"
    device = None
    if operation in {"grants", "revoke"}:
        ticket = service.identities.ticket(owner, device_class="android", name="Fixture", base_url="https://engine.invalid")
        device = service.enroll({**ticket, "deviceClass": "android"})["deviceId"]
        path += "/" + device
    if operation == "tickets":
        path += "/tickets"
        body = {"deviceClass": "android", "name": "Android", "baseUrl": "https://engine.invalid"}
    elif operation == "grants":
        path += "/grants"
        body = {"expectedRevision": 1, "grants": [{"capability": "android.observe", "resourceId": "fixture.app"}]}
    elif operation == "media":
        path += "/media/media_" + "0" * 32
    elif operation in {"command", "cancel", "reconcile"}:
        path += "/commands/missing-command" + ("/" + operation if operation != "command" else "")
        body = {"note": "Isolated routing fixture"} if operation == "reconcile" else None

    app = production_client_app()
    with TestClient(create_phone_gateway_app(client_app=app)) as client:
        assert client.request(method, path, json=body).status_code == 401
        response = client.request(method, path, json=body, headers={"Authorization": "Bearer " + human["accessToken"]})
    # Read the endpoint selected by the actual ASGI dispatch, including FastAPI's
    # included-router implementation. The old main order selected client_api.
    assert app.state.route_owners[-1] is getattr(device_executor_routes, handler), (operation, response.status_code, response.json())
    if operation in {"media", "command", "cancel", "reconcile"}:
        assert response.status_code == 404
        assert response.json()["code"] == ("media_not_found" if operation == "media" else "command_not_found")
        return
    assert response.status_code == 200
    payload = response.json()
    with identity.database() as db:
        if operation == "tickets":
            row = db.execute("SELECT device_class,owner_id,base_url,consumed_at FROM executor_enrollments").fetchone()
            assert tuple(row) == ("android", owner, "https://engine.invalid", None)
            assert payload["authorityId"] == identity.instance()["instanceId"]
        elif operation == "list":
            assert payload == {"items": []}
        elif operation == "grants":
            assert payload["grantRevision"] == 2 and payload["grants"] == body["grants"]
            assert db.execute("SELECT grant_revision FROM executor_connections WHERE device_id=?", (device,)).fetchone()[0] == 2
        else:
            assert payload == {"ok": True, "deviceId": device, "revoked": True}
            assert db.execute("SELECT revoked_at FROM executor_identities WHERE device_id=?", (device,)).fetchone()[0] is not None


@pytest.mark.parametrize("method,suffix", [("PUT", "/grants"), ("DELETE", "")])
def test_production_executor_owner_rejection_remains_intact(tmp_path, monkeypatch, method, suffix):
    identity = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()))
    owner = identity.owners.bootstrap(login="fixture", now=identity.clock())["id"]
    service = ExecutorService(identity)
    monkeypatch.setattr(client_identity, "_service", identity)
    monkeypatch.setattr(device_executor_routes, "get_executor_service", lambda: service)
    human = identity.create_session(name="Phone fixture", surface="phone")
    ticket = service.identities.ticket(owner, device_class="android", name="Fixture", base_url="https://engine.invalid")
    device = service.enroll({**ticket, "deviceClass": "android"})["deviceId"]
    with identity.transaction() as db:
        db.execute("UPDATE executor_identities SET owner_id='other-owner' WHERE device_id=?", (device,))
    with TestClient(create_phone_gateway_app(client_app=production_client_app())) as client:
        response = client.request(method, "/api/client/executors/" + device + suffix,
                                  headers={"Authorization": "Bearer " + human["accessToken"]},
                                  json={"expectedRevision": 1, "grants": []})
    assert response.status_code in {401, 404}
    assert response.json()["code"] in {"executor_owner_changed", "executor_not_found"}
    with identity.database() as db:
        assert db.execute("SELECT revoked_at FROM executor_identities WHERE device_id=?", (device,)).fetchone()[0] is None
        assert db.execute("SELECT grant_revision FROM executor_connections WHERE device_id=?", (device,)).fetchone()[0] == 1


def test_production_client_catch_all_keeps_its_auth_and_fallback_contract(tmp_path, monkeypatch):
    identity = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()))
    identity.owners.bootstrap(login="fixture", now=identity.clock())
    monkeypatch.setattr(client_identity, "_service", identity)
    human = identity.create_session(name="Phone fixture", surface="phone")
    app = production_client_app()
    with TestClient(app) as client:
        assert client.get("/api/client/runtime/bridge").status_code == 401
        denied = client.get("/api/client/runtime/bridge", headers={"Authorization": "Bearer " + human["accessToken"]})
        assert denied.status_code == 403 and denied.json()["detail"] == "local_client_required"
        unknown = client.get("/api/client/unknown", headers={"Authorization": "Bearer " + human["accessToken"]})
        assert unknown.status_code == 404 and unknown.json()["detail"] == "client_route_not_found"
    assert all(endpoint is client_routes.client_api for endpoint in app.state.route_owners)
