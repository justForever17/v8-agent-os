import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import client_identity, auth_context
from core.client_identity.owner import IdentityError, session_identifier
from core.client_identity.service import ClientIdentityService, ACCESS_TTL, encode
from core.client_identity.resources import sign_resource_url, verify_resource_request
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend


@pytest.fixture
def identity(tmp_path, monkeypatch):
    clock = [1800000000.0]
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), clock=lambda: clock[0], config_reader=lambda: {})
    service.owners.bootstrap(login="owner", name="Owner", now=clock[0])
    service.test_clock = clock
    monkeypatch.setattr(client_identity, "_service", service)
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-internal")
    return service


def paired(service):
    ticket = service.create_ticket(base_url="https://phone.example.invalid")
    return service.consume_ticket(code=ticket["pairingCode"], instance_id=ticket["instanceId"])


def resign(service, token, *, header=None, **patch):
    from core.client_identity.service import decode
    head, payload, signature = token.split(".")
    claims = {**json.loads(decode(payload)), **patch}
    if header is not None:
        head = encode(json.dumps(header).encode())
    payload = encode(json.dumps(claims).encode())
    signature = encode(hmac.new(service._key("signing").encode(), (head + "." + payload).encode(), hashlib.sha256).digest())
    return head + "." + payload + "." + signature


def test_pairing_race_consumes_and_creates_exactly_once(identity):
    ticket = identity.create_ticket(base_url="https://phone.example.invalid")
    def consume():
        try:
            return identity.consume_ticket(code=ticket["pairingCode"], instance_id=ticket["instanceId"])
        except IdentityError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        result = list(workers.map(lambda _: consume(), range(2)))
    assert sum(isinstance(item, dict) for item in result) == 1
    assert "pairing_ticket_consumed" in result
    assert len(identity.devices(identity.owner()["id"])) == 1


@pytest.mark.parametrize("claims", [
    {"aud": "wrong"}, {"iss": "other-instance"}, {"type": "mobile_access"},
    {"did": "missing"}, {"sub": "another-owner"}, {"iat": 1900000000}, {"exp": 1800000000},
    {"exp": 1800000000 + ACCESS_TTL + 1}, {"sid": "different-owner"}, {"iat": True},
])
def test_new_access_rejects_forged_claims(identity, claims):
    token = paired(identity)["accessToken"]
    assert identity.verify_access(resign(identity, token, **claims)) is None


def test_fixed_algorithm_and_role_from_owner(identity):
    token = paired(identity)["accessToken"]
    assert identity.verify_access(resign(identity, token, header={"alg": "none", "typ": "JWT"})) is None
    context = identity.verify_access(resign(identity, token, role="SUPERUSER", login="attacker"))
    assert context.role == "ADMIN" and context.login == "owner"


def test_access_device_and_user_association_and_immediate_revoke(identity):
    first, second = paired(identity), paired(identity)
    assert identity.verify_access(first["accessToken"])
    identity.revoke(identity.owner()["id"], first["deviceId"])
    assert identity.verify_access(first["accessToken"]) is None
    assert identity.verify_access(second["accessToken"])
    with pytest.raises(IdentityError, match="refresh_token_invalid"):
        identity.refresh(first["refreshToken"])


def test_refresh_lost_response_idempotent_encrypted_and_other_id_replay_revokes_only_family(identity):
    first, other = paired(identity), paired(identity)
    rotated = identity.refresh(first["refreshToken"], rotation_id="rotation-1")
    assert identity.refresh(first["refreshToken"], rotation_id="rotation-1") == rotated
    assert rotated["deviceId"] == first["deviceId"]
    with identity.database() as db:
        encrypted = db.execute("SELECT encrypted_result FROM client_refresh_replays").fetchone()[0]
        assert rotated["refreshToken"] not in encrypted and rotated["accessToken"] not in encrypted
    with pytest.raises(IdentityError, match="refresh_token_replayed"):
        identity.refresh(first["refreshToken"], rotation_id="rotation-2")
    assert identity.verify_access(rotated["accessToken"]) is None
    assert identity.verify_access(other["accessToken"])


def test_refresh_recovery_survives_restart_and_expires(identity):
    first = paired(identity)
    rotated = identity.refresh(first["refreshToken"], rotation_id="stable")
    restarted = ClientIdentityService(identity.home, identity.credentials, clock=identity.clock, config_reader=identity.config_reader)
    assert restarted.refresh(first["refreshToken"], rotation_id="stable") == rotated
    identity.test_clock[0] += 61
    with pytest.raises(IdentityError, match="refresh_token_replayed"):
        restarted.refresh(first["refreshToken"], rotation_id="stable")


def test_repeated_local_attach_reuses_device_without_invalidating_other_window(identity):
    first = identity.create_session(name="Web", surface="web", hidden=True)
    second = identity.create_session(name="Web", surface="web", hidden=True)
    assert first["deviceId"] == second["deviceId"]
    assert identity.verify_access(first["accessToken"]) and identity.verify_access(second["accessToken"])
    assert identity.devices(identity.owner()["id"]) == []


def test_profile_display_email_does_not_change_session_owner(identity):
    first = paired(identity)
    before = session_identifier(identity.owner())
    identity.update_profile(identity.owner()["id"], {"email": "new@example.invalid"})
    assert session_identifier(identity.owner()) == before
    assert identity.verify_access(first["accessToken"]).session_id == before
    assert identity.refresh(first["refreshToken"])["user"]["email"] == "new@example.invalid"


def test_corrupt_owner_cannot_bootstrap(identity):
    identity.owners.path.write_text('{"users":"broken"}', encoding="utf-8")
    with pytest.raises(IdentityError, match="owner_storage_unavailable"):
        identity.owners.bootstrap(now=identity.clock())
    assert identity.owners.path.read_text() == '{"users":"broken"}'


def test_resource_capability_exact_target_method_expiry_and_revocation(identity):
    pair = paired(identity)
    context = identity.verify_access(pair["accessToken"])
    path = sign_resource_url(identity, "/api/client/workspace/resource?workspace_relative_path=image.png&workspace_id=w1", context)
    assert verify_resource_request(identity, path)
    assert verify_resource_request(identity, path.replace("image.png", "private.png")) is None
    assert verify_resource_request(identity, path, method="POST") is None
    with pytest.raises(IdentityError):
        sign_resource_url(identity, "/api/client/workspace/files/%2e%2e/secret", context)
    identity.revoke(context.subject, context.device_id)
    assert verify_resource_request(identity, path) is None


def test_http_public_cannot_enter_management_and_actor_headers_ignored(identity):
    from api.client_identity_routes import router, management_router
    app = FastAPI()
    app.include_router(router)
    app.include_router(management_router)
    client = TestClient(app)
    pair = paired(identity)
    assert client.get("/v1/client-identity/devices", headers={"Authorization": "Bearer " + pair["accessToken"]}).status_code == 401
    assert client.post("/api/client/auth/login", json={}).status_code == 404
    response = client.get("/api/client/auth/me", headers={"x-v8-agent-os-secret": "fixture-internal", "x-v8-agent-os-user-email": "attacker", "x-v8-admin-role": "ROOT"})
    assert response.status_code == 200 and response.json()["user"]["login"] == "owner"
    assert "password" not in response.text
    response = client.get("/api/client/auth/me", headers={"Authorization": "Bearer invalid", "x-v8-agent-os-secret": "fixture-internal"})
    assert response.status_code == 401


def test_http_bootstrap_conflict_has_actionable_code_and_preserves_owner(identity):
    from api.client_identity_routes import management_router
    app = FastAPI()
    app.include_router(management_router)
    client = TestClient(app)
    before = identity.owners.owner().copy()
    headers = {"x-v8-agent-os-secret": "fixture-internal"}

    # A rendered setup page may become stale after another local window creates
    # the Owner. Admin uses this code to switch to login without clearing input.
    conflict = client.post("/v1/client-identity/bootstrap", headers=headers,
                           json={"login": "replacement", "password": "fixture-password"})
    assert conflict.status_code == 409
    assert conflict.json() == {"ok": False, "error": "owner_already_initialized", "code": "owner_already_initialized"}
    assert identity.owners.owner() == before

    rejected = client.post("/v1/client-identity/verify-credentials", headers=headers,
                           json={"login": "owner", "password": "wrong"})
    assert rejected.status_code == 401
    assert rejected.json()["code"] == rejected.json()["error"] == "invalid_credentials"


def test_legacy_import_preserves_device_and_expires_old_access_window(tmp_path):
    from core.client_identity.owner import atomic_json, timestamp
    clock = [1800000000.0]
    owner = {"id": "legacy-owner", "login": "owner", "email": "owner@example.invalid", "role": "ADMIN", "createdAt": timestamp(clock[0])}
    secret = "synthetic-legacy-signing-value"
    refresh = "synthetic-legacy-refresh"
    atomic_json(tmp_path / "users.json", {"users": [owner]})
    atomic_json(tmp_path / "mobile_app_auth.json", {"version": 1, "secret": secret})
    atomic_json(tmp_path / "mobile_app_tokens.json", {"version": 1, "refreshTokens": [{"id": "legacy-device", "userId": owner["id"], "sessionIdentifier": owner["email"],
        "tokenHash": hashlib.sha256((secret + ":" + refresh).encode()).hexdigest(), "createdAt": timestamp(clock[0]), "expiresAt": timestamp(clock[0] + 86400 * 30)}]})
    before = (tmp_path / "mobile_app_tokens.json").read_bytes()
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), clock=lambda: clock[0], config_reader=lambda: {})
    report = service.initialize()
    assert report["deviceCount"] == 1
    new = service.refresh(refresh, rotation_id="upgrade")
    legacy = resign(service, new["accessToken"], type="mobile_access", iss=None, aud=None, exp=int(clock[0]) + 86400)
    assert service.verify_access(legacy).device_id == "legacy-device"
    assert new["deviceId"] == "legacy-device"
    assert (tmp_path / "mobile_app_tokens.json").read_bytes() == before
    clock[0] += 86401
    future_legacy = resign(service, new["accessToken"], type="mobile_access", iss=None, aud=None, iat=int(clock[0]), exp=int(clock[0]) + 86400)
    assert service.verify_access(future_legacy) is None
    assert service.initialize() == {"ok": True, "imported": False}


def test_key_loss_never_replaces_key_or_consumes_refresh(identity):
    from core.security.credentials import CredentialStoreError
    pair = paired(identity)
    with identity.database() as db:
        reference = db.execute("SELECT value FROM client_identity_meta WHERE key='signing_ref'").fetchone()[0]
    identity.credentials.delete(reference)
    restarted = ClientIdentityService(identity.home, identity.credentials, clock=identity.clock, config_reader=identity.config_reader)
    with pytest.raises(IdentityError, match="identity_credentials_unavailable"):
        restarted.refresh(pair["refreshToken"], rotation_id="unavailable")
    with identity.database() as db:
        assert db.execute("SELECT consumed_at FROM client_refresh_tokens").fetchone()[0] is None
    assert not identity.credentials.status(reference).configured


def test_profile_capability_is_never_written_as_persistent_metadata(identity):
    pair = paired(identity)
    context = identity.verify_access(pair["accessToken"])
    identity.update_profile(context.subject, {"image": "/user-assets/avatar/fixture.webp"})
    response = identity.client_user(context)
    assert "v8sig=" in response["image"]
    clean = identity.clean_profile_patch(context, {"image": response["image"]})
    identity.update_profile(context.subject, clean)
    assert identity.owner()["image"] == "/user-assets/avatar/fixture.webp"
    assert "v8sig" not in identity.owners.path.read_text()


def test_refresh_same_rotation_race_rotates_once(identity):
    pair = paired(identity)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: identity.refresh(pair["refreshToken"], rotation_id="same-request"), range(2)))
    assert results[0] == results[1]
    with identity.database() as db:
        assert db.execute("SELECT COUNT(*) FROM client_refresh_tokens WHERE device_id=?", (pair["deviceId"],)).fetchone()[0] == 2


def test_pairing_transaction_rolls_back_on_signing_failure(identity, monkeypatch):
    ticket = identity.create_ticket(base_url="https://phone.example.invalid")
    original = identity._access
    def fail(*args, **kwargs):
        raise IdentityError("synthetic_signing_failure", 503)
    monkeypatch.setattr(identity, "_access", fail)
    with pytest.raises(IdentityError, match="synthetic_signing_failure"):
        identity.consume_ticket(code=ticket["pairingCode"], instance_id=ticket["instanceId"])
    assert identity.devices(identity.owner()["id"]) == []
    monkeypatch.setattr(identity, "_access", original)
    assert identity.consume_ticket(code=ticket["pairingCode"], instance_id=ticket["instanceId"])["accessToken"]


def test_http_password_compatibility_and_bootstrap_single_owner(identity, tmp_path, monkeypatch):
    from api.client_identity_routes import router, management_router
    app = FastAPI()
    app.include_router(router)
    app.include_router(management_router)
    client = TestClient(app)
    headers = {"x-v8-agent-os-secret": "fixture-internal"}
    assert client.post("/v1/client-identity/password", headers=headers, json={"newPassword": "first-pass"}).status_code == 200
    assert client.post("/v1/client-identity/verify-credentials", headers=headers, json={"login": "owner", "password": "first-pass"}).status_code == 200
    assert client.post("/v1/client-identity/password", headers=headers, json={"newPassword": "second-pass", "oldPassword": "wrong"}).status_code == 400
    assert client.post("/v1/client-identity/password", headers=headers, json={"newPassword": "second-pass", "oldPassword": "first-pass"}).status_code == 200
    response = client.post("/v1/client-identity/bootstrap", headers=headers, json={"login": "other", "name": "Other"})
    assert response.status_code == 409


def test_http_no_admin_owner_bootstrap_and_phone_pairing(tmp_path, monkeypatch):
    from api.client_identity_routes import router, management_router
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), config_reader=lambda: {})
    monkeypatch.setattr(client_identity, "_service", service)
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-internal")
    app = FastAPI()
    app.include_router(router)
    app.include_router(management_router)
    client = TestClient(app)
    headers = {"x-v8-agent-os-secret": "fixture-internal"}
    assert client.get("/api/client/instance").json()["initialized"] is False
    result = client.post("/v1/client-identity/bootstrap", headers=headers, json={"name": "Server Owner"})
    assert result.status_code == 200
    ticket = client.post("/v1/client-identity/pairing-ticket", headers=headers, json={"adminBaseUrl": "https://phone.example.invalid"}).json()
    pair = client.post("/api/client/pairing/consume", json={"code": ticket["pairingCode"], "instanceId": ticket["instanceId"]})
    assert pair.status_code == 200
    assert client.get("/api/client/auth/me", headers={"Authorization": "Bearer " + pair.json()["accessToken"]}).json()["user"]["name"] == "Server Owner"
    assert client.get("/v1/client-identity/migration", headers=headers).json()["credentialStatus"] == {"signing": True, "refresh-recovery": True}


def test_passwordless_cli_owner_can_finish_admin_setup_without_repairing_phone(tmp_path, monkeypatch):
    from api.client_identity_routes import router, management_router
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), config_reader=lambda: {})
    monkeypatch.setattr(client_identity, "_service", service)
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-internal")
    app = FastAPI(); app.include_router(router); app.include_router(management_router)
    client = TestClient(app)
    headers = {"x-v8-agent-os-secret": "fixture-internal"}
    assert client.post("/v1/client-identity/bootstrap", headers=headers, json={"login": "cli-owner"}).status_code == 200
    initial = service.owner()
    pair = service.create_session(name="Paired before Admin setup")
    assert client.get("/v1/client-identity/owner", headers=headers).json()["needsSetup"] is True
    completed = client.post("/v1/client-identity/bootstrap", headers=headers, json={"login": "admin-owner", "password": "synthetic-password"})
    assert completed.status_code == 200
    assert service.owner()["id"] == initial["id"]
    assert session_identifier(service.owner()) == session_identifier(initial)
    assert client.get("/v1/client-identity/owner", headers=headers).json()["needsSetup"] is False
    assert service.verify_access(pair["accessToken"]).subject == initial["id"]
    assert service.refresh(pair["refreshToken"], rotation_id="after-admin-setup")["deviceId"] == pair["deviceId"]


def test_phone_gateway_rejects_local_identity_before_profile_or_refresh_mutation(identity):
    from api.client_identity_routes import router
    from core.remote_link.phone_gateway import create_phone_gateway_app
    app = FastAPI(); app.include_router(router)
    client = TestClient(create_phone_gateway_app(client_app=app))
    pair = identity.create_session(name="Desktop", surface="web", hidden=True)
    headers = {"authorization": "Bearer " + pair["accessToken"]}
    assert client.get("/api/client/auth/me", headers=headers).status_code == 403
    assert client.patch("/api/client/auth/profile", headers=headers, json={"name": "spoofed"}).status_code == 403
    assert identity.owner()["name"] == "Owner"
    denied = client.post("/api/client/auth/refresh", json={"refreshToken": pair["refreshToken"], "rotationId": "forbidden"})
    assert denied.status_code == 403
    assert identity.verify_access(pair["accessToken"]) is not None
    # A denied gateway attempt did not consume or revoke the local refresh token.
    assert identity.refresh(pair["refreshToken"], rotation_id="local-legitimate")["deviceId"] == pair["deviceId"]


@pytest.mark.parametrize("surface", ["executor", "supervisor_peer", "peer", "android_executor"])
def test_phone_identity_never_issues_executor_or_peer_credentials(identity, surface):
    with pytest.raises(IdentityError, match="phone_pairing_only"):
        identity.create_ticket(base_url="https://phone.example.invalid", surface=surface)
    with pytest.raises(IdentityError, match="unsupported_identity_surface"):
        identity.create_session(name="other-role", surface=surface)


def test_manifest_retains_transport_profiles_without_promoting_old_admin(identity):
    identity.config_reader = lambda: {"remoteLink": {"activeProfileId": "tail", "phoneGateway": {"enabled": True, "publicBaseUrl": "https://phone.example.invalid"},
        "transportProfiles": [
            {"id": "tail", "kind": "tailscale", "adminBaseUrl": "https://old-admin.example.invalid", "phoneBaseUrl": "https://phone-tail.example.invalid"},
            {"id": "old", "kind": "wireguard", "adminBaseUrl": "https://unmigrated-admin.example.invalid"},
            {"id": "off", "kind": "cloudflare_tunnel", "phoneBaseUrl": "https://off.example.invalid", "enabled": False},
            {"id": "temporary", "kind": "cloudflare_tunnel", "phoneBaseUrl": "https://fixture.trycloudflare.com"},
            {"id": "stable", "kind": "cloudflare_tunnel", "phoneBaseUrl": "https://stable.example.invalid"},
        ]}}
    manifest = identity.manifest("https://current-phone.example.invalid")
    assert manifest["transportKind"] == "tailscale" and manifest["activeProfileId"] == "tail"
    endpoints = {row["baseUrl"] for row in manifest["endpoints"]}
    assert "https://phone-tail.example.invalid" in endpoints
    assert "https://unmigrated-admin.example.invalid" not in endpoints
    assert "https://old-admin.example.invalid" not in endpoints
    assert "https://off.example.invalid" not in endpoints
    assert "https://fixture.trycloudflare.com" not in endpoints
    assert "https://stable.example.invalid" in endpoints
    assert "cloudflare_stable_https_origin_required:temporary" in manifest["warnings"]
    assert "phone_profile_endpoint_migration_required:old" in manifest["warnings"]


def test_pairing_display_and_ticket_use_engine_selected_phone_endpoint(identity):
    from urllib.parse import parse_qs, urlsplit
    identity.config_reader = lambda: {"remoteLink": {"activeProfileId": "vpn", "phoneGateway": {
        "publicBaseUrl": "https://gateway.example.invalid"}, "transportProfiles": [
            {"id": "vpn", "kind": "tailscale", "phoneBaseUrl": "https://phone-vpn.example.invalid",
             "adminBaseUrl": "https://admin.example.invalid"}]}}
    manifest = identity.manifest("http://127.0.0.1:9530")
    assert manifest["pairing"] == {"available": True, "baseUrl": "https://phone-vpn.example.invalid",
                                    "reason": "", "reachability": "not_verified"}
    ticket = identity.create_ticket()
    assert ticket["adminBaseUrl"] == manifest["pairing"]["baseUrl"]
    assert parse_qs(urlsplit(ticket["pairingUri"]).query)["admin"] == [ticket["adminBaseUrl"]]
    assert "127.0.0.1" not in ticket["pairingUri"]
    receipt = identity.consume_ticket(code=ticket["pairingCode"], instance_id=ticket["instanceId"])
    assert receipt["adminBaseUrl"] == ticket["adminBaseUrl"]
    assert identity.verify_access(receipt["accessToken"]).surface == "phone"


@pytest.mark.parametrize("remote,reason", [
    ({}, "pairing_reachable_https_required"),
    ({"enabled": False, "phoneGateway": {"publicBaseUrl": "https://phone.example.invalid"}}, "remote_link_disabled"),
    ({"phoneGateway": {"enabled": False, "publicBaseUrl": "https://phone.example.invalid"}}, "phone_gateway_disabled"),
    ({"activeProfileId": "old", "transportProfiles": [{"id": "old", "adminBaseUrl": "https://admin.example.invalid"}]}, "pairing_reachable_https_required"),
    ({"activeProfileId": "off", "transportProfiles": [{"id": "off", "enabled": False, "phoneBaseUrl": "https://off.example.invalid"}]}, "pairing_reachable_https_required"),
])
def test_unconfigured_or_disabled_phone_does_not_offer_local_origin_or_issue_ticket(identity, remote, reason):
    identity.config_reader = lambda: {"remoteLink": remote}
    pairing = identity.manifest("http://127.0.0.1:9530")["pairing"]
    assert not pairing["available"] and pairing["baseUrl"] == "" and pairing["reason"] == reason
    with pytest.raises(IdentityError, match=reason):
        identity.create_ticket()
    if reason in ("remote_link_disabled", "phone_gateway_disabled"):
        with pytest.raises(IdentityError, match=reason):
            identity.create_ticket(base_url="https://override.example.invalid")
    with identity.database() as db:
        assert db.execute("SELECT COUNT(*) FROM client_pairing_tickets").fetchone()[0] == 0


@pytest.mark.parametrize("address", ["https://127.8.9.10", "https://localhost.", "https://test.localhost",
    "https://[::ffff:127.0.0.1]", "https://127.1", "https://2130706433", "https://0x7f000001", "https://0177.0.0.1",
    "https://%31%32%37.0.0.1", "https://１２７。０。０。１", "https://ＬＯＣＡＬＨＯＳＴ",
    "https://127.0.0.1\\@remote.example", "https://local\nhost", "https://bad host.invalid",
    "https://0.0.0.0", "https://[::]", "https://phone.invalid:99999", "http://192.168.1.10:9532"])
def test_phone_pairing_rejects_non_remote_origins_before_ticket_write(identity, address):
    identity.config_reader = lambda: {"remoteLink": {"phoneGateway": {"publicBaseUrl": address}}}
    assert not identity.manifest("")["pairing"]["available"]
    with pytest.raises(IdentityError, match="pairing_reachable_https_required"):
        identity.create_ticket(base_url=address)
    with identity.database() as db:
        assert db.execute("SELECT COUNT(*) FROM client_pairing_tickets").fetchone()[0] == 0


def test_pairing_accepts_international_hostname_without_rewriting_configured_path(identity):
    address = "https://例子.example.invalid/phone"
    identity.config_reader = lambda: {"remoteLink": {"phoneGateway": {"publicBaseUrl": address}}}
    assert identity.manifest("")["pairing"]["baseUrl"] == address
    assert identity.create_ticket()["adminBaseUrl"] == address


def test_restored_instance_does_not_overwrite_existing_os_keys(identity, tmp_path):
    import shutil
    pair = paired(identity)
    original_key = identity._key("refresh-recovery")
    copy_home = tmp_path / "restored"
    copy_home.mkdir()
    shutil.copytree(identity.home / "runtime", copy_home / "runtime")
    shutil.copyfile(identity.owners.path, copy_home / "users.json")
    restored = ClientIdentityService(copy_home, identity.credentials, clock=identity.clock, config_reader=lambda: {})
    restored.initialize()
    assert restored._key("refresh-recovery") == original_key
    reference = identity.credentials._target("cred:v8-system:identity-" + hashlib.sha256(identity.instance()["instanceId"].encode()).hexdigest()[:24] + "-refresh-recovery")
    assert identity.credentials._backend.read(reference) == original_key


def test_local_bootstrap_concurrency_and_admin_setup_preserve_existing_sessions(tmp_path, monkeypatch):
    from api.client_identity_routes import router, management_router
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), config_reader=lambda: {})
    monkeypatch.setattr(client_identity, "_service", service)
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-internal")
    with ThreadPoolExecutor(max_workers=2) as workers:
        pairs = list(workers.map(lambda _: service.create_session(name="Desktop", surface="web", hidden=True), range(2)))
    assert len(service.owners.payload()["users"]) == 1
    assert pairs[0]["deviceId"] == pairs[1]["deviceId"]
    app = FastAPI()
    app.include_router(router); app.include_router(management_router)
    client = TestClient(app)
    headers = {"x-v8-agent-os-secret": "fixture-internal"}
    assert client.get("/v1/client-identity/owner", headers=headers).json()["needsSetup"] is True
    initial_id, initial_sid = service.owner()["id"], session_identifier(service.owner())
    response = client.post("/v1/client-identity/bootstrap", headers=headers, json={"login": "my-login", "name": "Display", "password": "synthetic-password"})
    assert response.status_code == 200
    assert service.owner()["id"] == initial_id and session_identifier(service.owner()) == initial_sid
    assert service.verify_access(pairs[0]["accessToken"]).subject == initial_id
    assert client.get("/v1/client-identity/owner", headers=headers).json()["needsSetup"] is False
    assert client.post("/v1/client-identity/bootstrap", headers=headers, json={"password": "other-password"}).status_code == 409


def test_playlist_conflicts_and_phone_updates_preserve_shared_appearance(identity):
    from core.client_identity.owner import atomic_json
    directory = identity.home / "assets/user-media/background"
    directory.mkdir(parents=True)
    for filename, kind in (("phone.webp", "image"), ("one.webp", "image"), ("two.mp4", "video")):
        (directory / filename).write_bytes(b"already-validated-upload-fixture")
        atomic_json(directory / (".receipt-" + filename + ".json"), {"userId": identity.owner()["id"], "kind": kind})
    identity.update_profile(identity.owner()["id"], {"appearance": {"lightBackgroundMedia": "/user-assets/background/phone.webp", "lightBackgroundEnabled": True, "unknownField": "keep"}})
    playlist = {"revision": 0, "enabled": True, "imageDurationMs": 1000, "items": [{"id": "one", "kind": "image", "media": "/user-assets/background/one.webp"}, {"id": "two", "kind": "video", "media": "/user-assets/background/two.mp4"}]}
    saved = identity.update_profile(identity.owner()["id"], {"appearance": {"webBackground": playlist}})
    assert saved["appearance"]["webBackground"]["revision"] == 1
    with pytest.raises(IdentityError, match="BACKGROUND_REVISION_CONFLICT"):
        identity.update_profile(identity.owner()["id"], {"appearance": {"webBackground": playlist}})
    saved = identity.update_profile(identity.owner()["id"], {"name": "Phone rename", "appearance": {"lightBackgroundEnabled": False}})
    assert len(saved["appearance"]["webBackground"]["items"]) == 2
    assert saved["appearance"]["unknownField"] == "keep"
    assert saved["appearance"]["lightBackgroundMedia"] == "/user-assets/background/phone.webp"


def test_foreign_or_missing_background_receipt_never_enters_playlist(identity):
    from core.client_identity.owner import atomic_json
    directory = identity.home / "assets/user-media/background"
    directory.mkdir(parents=True)
    (directory / "foreign.webp").write_bytes(b"fixture")
    atomic_json(directory / ".receipt-foreign.webp.json", {"userId": "different-owner", "kind": "image"})
    for media in ("foreign.webp", "missing.webp"):
        with pytest.raises(IdentityError, match="BACKGROUND_MEDIA_UNAVAILABLE"):
            identity.update_profile(identity.owner()["id"], {"appearance": {"webBackground": {"revision": 0, "items": [{"id": "asset", "kind": "image", "media": "/user-assets/background/" + media}]}}})
    assert identity.owner()["appearance"] == {}


def test_two_actual_background_uploads_survive_playlist_commit_and_cleanup(identity):
    import io
    from PIL import Image
    from api.client_asset_routes import router
    from core.client_user_media import remove_unreferenced
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    image = io.BytesIO(); Image.new("RGB", (8, 8), "navy").save(image, format="PNG")
    paths = []
    for _ in range(2):
        response = client.post("/api/client/user-background-upload", content=image.getvalue(), headers={"x-v8-agent-os-secret": "fixture-internal", "content-type": "image/png", "x-v8-upload-mode": "raw", "x-v8-background-intent": "playlist"})
        assert response.status_code == 200 and response.json()["receipt"]
        paths.append(response.json()["path"])
    assert identity.owner()["appearance"] == {}
    identity.update_profile(identity.owner()["id"], {"appearance": {"webBackground": {"revision": 0, "items": [{"id": f"item-{i}", "kind": "image", "media": media} for i, media in enumerate(paths)]}}})
    remove_unreferenced(identity.owners, paths[0], kind="background")
    for media in paths:
        assert (identity.home / "assets/user-media/background" / media.rsplit("/", 1)[-1]).is_file()
