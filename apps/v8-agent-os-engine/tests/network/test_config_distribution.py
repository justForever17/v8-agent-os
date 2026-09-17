from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.database import DatabaseManager
from core.config_distribution_store import DistributionStore
from core.config_distribution_service import ConfigDistributionService
from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig, TrustedPeerConfig
from runtimes.network_supervisor.service import NetworkSupervisorService


def node(peer_id):
    network = NetworkSupervisorService()
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    config = NetworkSupervisorRuntimeConfig.model_validate({"enabled": True, "node": {"peerId": peer_id}})
    network.get_config_model = lambda: config
    network.ensure_local_identity = lambda: {"peerId": peer_id, "publicKey": public}
    network._private_key = lambda: key
    return network, config, public


@pytest.fixture
def system(tmp_path, monkeypatch):
    import core.config_broker_service as broker
    import core.config_distribution_templates as portable
    import core.storage as storage_module
    from core.model_control_plane import ModelControlPlane
    from core.security.credentials import CredentialRefStore, MemoryCredentialBackend

    database = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(broker, "db", database)
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    plane = ModelControlPlane(credential_store=CredentialRefStore(MemoryCredentialBackend()))
    monkeypatch.setattr(broker, "model_control_plane", plane)
    monkeypatch.setattr(portable, "model_control_plane", plane)
    broker_service = broker.ConfigBrokerService()
    monkeypatch.setattr(broker, "config_broker_service", broker_service)
    monkeypatch.setattr(portable, "config_broker_service", broker_service)
    import core.config_distribution_service as distribution
    monkeypatch.setattr(distribution, "config_broker_service", broker_service)
    plane.save_config({"governance": {"budgets": {"runMaxTokens": 100}}, "roleParameters": {"supervisor": {"temperature": 0.7}}})
    sender, sender_config, sender_public = node("source")
    receiver, receiver_config, receiver_public = node("target")
    receiver_config.trust.trusted_peers = [TrustedPeerConfig(peerId="source", publicKey=sender_public, baseUrl="http://127.0.0.1:19991")]
    sender_config.trust.trusted_peers = [TrustedPeerConfig(peerId="target", publicKey=receiver_public, baseUrl="http://127.0.0.1:19992")]
    link = {"linkId": "source_link", "peerId": "source", "trustStatus": "trusted", "localRole": "companion", "remoteRole": "primary", "createdAt": "fixture", "metadata": {}}
    source_link = {**link, "peerId": "target", "localRole": "primary", "remoteRole": "companion", "linkId": "target_link"}
    neighbors = SimpleNamespace(_link_for_peer_or_404=lambda peer: link,
                                _link_or_404=lambda identity: link, list_links=lambda: {"items": [link]})
    service = ConfigDistributionService(store=DistributionStore(database), network=receiver, neighbors=neighbors, authorize=lambda _: None)
    source_neighbors = SimpleNamespace(_link_for_peer_or_404=lambda peer: source_link,
                                       _link_or_404=lambda identity: source_link, list_links=lambda: {"items": [source_link]})
    source = ConfigDistributionService(store=DistributionStore(database), network=sender, neighbors=source_neighbors, authorize=lambda _: None)
    async def deliver(_peer, _path, envelope):
        return (await service.handle_envelope(envelope)).model_dump(by_alias=True)
    sender._post_peer = deliver
    return SimpleNamespace(target=service, source=source, sender=sender, receiver=receiver, link=link, source_link=source_link,
                           plane=plane, broker=broker_service, db=database, deliver=deliver)


def receive(system, action, **payload):
    envelope = system.sender.build_envelope(message_type="config.distribution." + action, to_peer_id="target", payload={"protocolVersion": 1, **payload})
    return asyncio.run(system.target.handle_envelope(envelope)).payload


def plan(system, generation=1, job_id="job", tokens=777):
    return receive(system, "prepare", jobId=job_id, generation=generation, templateId="model-policy",
                   values={"governance": {"budgets": {"runMaxTokens": tokens}}, "roleParameters": {}}, mapping={})


def apply(system, action, prepared):
    return receive(system, action, jobId=prepared["jobId"], generation=prepared["generation"],
                   planDigest=prepared["receipt"]["planDigest"], targetBinding=prepared["receipt"]["targetBinding"])


def test_prepare_confirm_readback_duplicate_restart_and_withdraw(system):
    prepared = plan(system)
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100
    assert prepared["diff"] == [{"field": "governance.budgets.runMaxTokens", "before": 100, "after": 777}]
    result = apply(system, "apply", prepared)
    assert result["state"] == "committed"
    assert result["receipt"]["readback"]["governance.budgets.runMaxTokens"] == 777
    assert apply(system, "apply", prepared)["receipt"] == result["receipt"]
    with system.db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM config_broker_transactions").fetchone()[0] == 1
    system.target = ConfigDistributionService(store=DistributionStore(system.db), network=system.receiver,
                                              neighbors=system.target.neighbors, authorize=lambda _: None)
    assert apply(system, "apply", prepared)["state"] == "committed"
    assert apply(system, "withdraw", prepared)["state"] == "rolled_back"
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100


def test_cancel_tombstone_and_late_apply_cannot_write(system):
    prepared = plan(system)
    assert apply(system, "cancel", prepared)["state"] == "cancelled"
    assert apply(system, "apply", prepared)["state"] == "cancelled"
    receive(system, "cancel", jobId="not-yet-prepared", generation=1)
    with pytest.raises(HTTPException):
        plan(system, job_id="not-yet-prepared")
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100


def test_changed_config_fences_commit_and_changed_after_fences_rollback(system):
    prepared = plan(system)
    system.plane.mutate_config(lambda config: {**config, "governance": {**config["governance"], "budgets": {**config["governance"]["budgets"], "runMaxTokens": 200}}})
    assert apply(system, "apply", prepared)["state"] == "conflict"
    fresh = plan(system, generation=2)
    assert apply(system, "apply", fresh)["state"] == "committed"
    system.plane.mutate_config(lambda config: {**config, "governance": {**config["governance"], "budgets": {**config["governance"]["budgets"], "runMaxTokens": 900}}})
    assert apply(system, "withdraw", fresh)["state"] == "conflict"
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 900


@pytest.mark.parametrize("fault", ["revoke", "roles", "role_round_trip", "new_link", "new_key", "old_protocol", "wrong_target", "scope", "workspace"])
def test_authority_and_protocol_changes_cannot_commit(system, fault):
    prepared = plan(system)
    kwargs = {"jobId": "job", "generation": 1, "planDigest": prepared["receipt"]["planDigest"], "targetBinding": prepared["receipt"]["targetBinding"]}
    envelope = system.sender.build_envelope(message_type="config.distribution.apply", to_peer_id="target", payload={"protocolVersion": 0 if fault == "old_protocol" else 1, **kwargs})
    if fault == "revoke": system.receiver.get_config_model().trust.trusted_peers = []
    if fault == "roles": system.link["localRole"] = "primary"
    if fault == "role_round_trip": system.link["metadata"]["configAuthorityVersion"] = "changed"
    if fault == "new_link": system.link["linkId"] = "new-link"
    if fault == "new_key": system.receiver.get_config_model().trust.trusted_peers[0].public_key = base64.b64encode(b"x" * 32).decode()
    if fault == "wrong_target": envelope.to_peer_id = "elsewhere"
    if fault == "scope": system.receiver.get_config_model().trust.trusted_peers[0].allowed_scopes = ["research"]
    if fault == "workspace": system.receiver.get_config_model().trust.trusted_peers[0].allowed_workspaces = ["isolated-project"]
    with pytest.raises(HTTPException): asyncio.run(system.target.handle_envelope(envelope))
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100


@pytest.mark.parametrize("extra", [{"owner": "other"}, {"apiKey": "synthetic"}, {"workspacePath": "C:\\private"}, {"grant": {"all": True}}, {"projectBudgets": [{"projectId": "private"}]}])
def test_portable_whitelist_rejects_secrets_authority_and_paths(system, extra):
    with pytest.raises(HTTPException):
        receive(system, "prepare", jobId="job", generation=1, templateId="model-policy", mapping={},
                values={"governance": {"budgets": {"runMaxTokens": 10, **extra}}, "roleParameters": {}})
    with system.db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM config_broker_transactions").fetchone()[0] == 0


def test_model_mapping_requires_target_local_model(system):
    result = receive(system, "prepare", jobId="model-job", generation=1, templateId="model-roles",
                     values={"roles": {"supervisor": ""}}, mapping={"models": {"supervisor": "missing::model"}})
    assert result["state"] == "blocked" and result["missingRequirements"] == ["model_not_found"]


def test_old_generation_late_confirmation_and_command_reuse(system):
    first = plan(system)
    # Identical proposed values produce the same Broker plan digest. Only the
    # distribution generation can reject the old confirmation in this case.
    plan(system, generation=2)
    with pytest.raises(HTTPException): apply(system, "apply", first)
    with pytest.raises(HTTPException): plan(system, generation=2, tokens=333)
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100


def test_source_confirm_identity_partial_transport_recovery_and_replay(system):
    source = system.source
    body = {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link", "mapping": {}}]}
    created = source.create("owner", {}, body)
    asyncio.run(source.process_once())
    prepared = source.store.get(created["jobId"])
    assert prepared["state"] == "awaiting_confirmation", [(row["state"], row["errorCode"]) for row in prepared["targets"]]
    confirm = {"commandId": "confirm", "revision": prepared["revision"], "planDigest": prepared["planDigest"]}
    with pytest.raises(HTTPException): source.action(prepared["jobId"], "another-owner", "confirm", confirm)
    with pytest.raises(HTTPException): source.action(prepared["jobId"], "owner", "confirm", {**confirm, "planDigest": "wrong"})
    source.action(prepared["jobId"], "owner", "confirm", confirm)
    async def lost_reply(peer, path, envelope):
        await system.deliver(peer, path, envelope)
        raise HTTPException(503, {"failureClass": "peer_unreachable"})
    system.sender._post_peer = lost_reply
    asyncio.run(source.process_once())
    partial = source.store.get(prepared["jobId"])
    assert partial["state"] == "partial" and partial["targets"][0]["state"] == "offline"
    assert source.create("owner", {}, body)["jobId"] == created["jobId"]
    system.sender._post_peer = system.deliver
    recovered = ConfigDistributionService(store=DistributionStore(system.db), network=system.sender, neighbors=source.neighbors, authorize=lambda _: None)
    recovered.action(prepared["jobId"], "owner", "retry", {"commandId": "retry", "revision": partial["revision"]})
    asyncio.run(recovered.process_once())
    assert recovered.store.get(prepared["jobId"])["state"] == "completed"
    with system.db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM config_broker_transactions").fetchone()[0] == 1


def test_cancel_during_inflight_prepare_stays_cancelled(system):
    source = system.source
    created = source.create("owner", {}, {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]})
    async def cancel_on_reply(peer, path, envelope):
        result = await system.deliver(peer, path, envelope)
        current = source.store.get(created["jobId"])
        source.action(created["jobId"], "owner", "cancel", {"commandId": "cancel", "revision": current["revision"]})
        return result
    system.sender._post_peer = cancel_on_reply
    asyncio.run(source.process_once())
    assert source.store.get(created["jobId"])["state"] == "cancelling"
    system.sender._post_peer = system.deliver
    asyncio.run(source.process_once())
    assert source.store.get(created["jobId"])["state"] == "cancelled"


def test_phone_gateway_allows_only_typed_distribution_routes():
    from core.remote_link.phone_gateway import PHONE_GATEWAY_ROUTES
    for path, method in [("/api/client/config-distribution", "GET"), ("/api/client/config-distribution/job/confirm", "POST")]:
        assert any(route.matches(path, method) for route in PHONE_GATEWAY_ROUTES)
    assert not any(route.matches("/v1/config-broker/transactions/tx/commit", "POST") for route in PHONE_GATEWAY_ROUTES)


def test_recovery_required_reconciles_readback_without_second_commit(system, monkeypatch):
    import core.config_distribution_templates as portable
    prepared = plan(system)
    original = portable.projection
    def bad_readback(*args, **kwargs):
        result = original(*args, **kwargs)
        result["governance.budgets.runMaxTokens"] = -1
        return result
    monkeypatch.setattr(portable, "projection", bad_readback)
    assert apply(system, "apply", prepared)["state"] == "recovery_required"
    monkeypatch.setattr(portable, "projection", original)
    assert apply(system, "status", prepared)["state"] == "committed"
    assert apply(system, "withdraw", prepared)["state"] == "rolled_back"
    with system.db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM config_broker_transactions").fetchone()[0] == 1


def test_withdraw_from_unknown_readback_uses_broker_cas(system, monkeypatch):
    import core.config_distribution_templates as portable
    prepared = plan(system)
    original = portable.projection
    monkeypatch.setattr(portable, "projection", lambda *args, **kwargs: {"governance.budgets.runMaxTokens": -1})
    assert apply(system, "apply", prepared)["state"] == "recovery_required"
    monkeypatch.setattr(portable, "projection", original)
    assert apply(system, "withdraw", prepared)["state"] == "rolled_back"
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100


def test_current_owner_can_cancel_after_original_phone_revoked(system):
    source = system.source
    body = {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]}
    created = source.create("owner", {"deviceId": "old"}, body)
    asyncio.run(source.process_once())
    job = source.store.get(created["jobId"])
    def authorize(authority):
        if authority.get("deviceId") == "old": raise HTTPException(403, "distribution_device_revoked")
    source.authorize = authorize
    with pytest.raises(HTTPException):
        source.action(job["jobId"], "owner", "confirm", {"commandId": "old-confirm", "revision": job["revision"], "planDigest": job["planDigest"]}, authority={"deviceId": "new"})
    source.action(job["jobId"], "owner", "cancel", {"commandId": "cancel", "revision": job["revision"]}, authority={"deviceId": "new"})
    asyncio.run(source.process_once())
    assert source.store.get(job["jobId"])["state"] == "cancelled"


def test_token_budget_field_exception_still_rejects_auth_tokens(system):
    import core.config_distribution_templates as portable
    template = portable.templates()[0]
    assert portable.prepare(template["id"], template["values"], {}, owner_id="owner", run_id="budget")["state"] == "ready_to_commit"
    from core.config_broker_service import _reject_secret_fields, ConfigBrokerError
    with pytest.raises(ConfigBrokerError):
        _reject_secret_fields({"globalDailyTokenLimit": "synthetic-secret"}, path="governance.budgets")
    with pytest.raises(ConfigBrokerError):
        _reject_secret_fields({"accessToken": "synthetic-secret"}, path="governance.budgets")


def test_target_model_readiness_exposes_missing_local_credentials(system):
    import core.config_distribution_templates as portable
    system.plane.mutate_config(lambda config: {**config, "providers": {"fixture": {
        "provider": {"name": "Fixture", "base_url": "https://fixture.invalid/v1", "api_standard": "openai", "credential_mode": "api_key", "authContract": {"type": "api_key"}},
        "models": {"chat": {"name": "Chat", "type": "TEXT", "contextWindow": 32768, "maxTokens": 2048,
                            "capabilityClass": "chat_general", "capabilities": {"streaming": True}}}}}})
    capabilities = portable.capabilities()
    model = next(row for row in capabilities["models"] if row["modelRef"] == "fixture::chat")
    assert model["ready"] is False and model["missingRequirements"]
    result = receive(system, "prepare", jobId="role-missing", generation=1, templateId="model-roles",
                     values={"roles": {"summary": ""}}, mapping={"models": {"summary": "fixture::chat"}})
    assert result["state"] == "blocked"


def test_roles_map_to_existing_target_model_and_recheck_auth_before_apply(system):
    import core.config_distribution_templates as portable
    system.plane.mutate_config(lambda config: {**config, "providers": {"local": {
        "provider": {"name": "Fixture local", "base_url": "http://127.0.0.1:19999/v1", "api_standard": "openai", "authContract": {"type": "none"}},
        "models": {"chat": {"name": "Local", "type": "TEXT", "contextWindow": 32768, "maxTokens": 2048,
                            "capabilityClass": "chat_general", "capabilities": {"streaming": True}}}}}})
    assert portable.capabilities()["models"][0]["ready"] is True
    prepared = receive(system, "prepare", jobId="role-ready", generation=1, templateId="model-roles",
                       values={"roles": {"supervisor": ""}}, mapping={"roles": {"supervisor": "summary"}, "models": {"supervisor": "local::chat"}})
    assert prepared["state"] == "prepared"
    assert apply(system, "apply", prepared)["state"] == "committed"
    assert system.plane.get_config()["roles"]["summary"] == "local::chat"
    assert apply(system, "withdraw", prepared)["state"] == "rolled_back"
    second = receive(system, "prepare", jobId="role-changed", generation=1, templateId="model-roles",
                     values={"roles": {"summary": ""}}, mapping={"models": {"summary": "local::chat"}})
    def require_credentials(config):
        config["providers"]["local"]["provider"]["authContract"] = {"type": "api_key"}
        return config
    system.plane.mutate_config(require_credentials)
    result = apply(system, "apply", second)
    assert result["state"] == "blocked" and result["errorCode"] == "target_local_credential_missing"
    assert not system.plane.get_config()["roles"]["summary"]


def test_cleanup_retry_uses_new_authority_after_original_phone_revoked(system):
    source = system.source
    job = source.create("owner", {"deviceId": "old"}, {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]})
    asyncio.run(source.process_once())
    job = source.store.get(job["jobId"])
    source.action(job["jobId"], "owner", "cancel", {"commandId": "cancel", "revision": job["revision"]}, authority={"deviceId": "new"})
    async def offline(*args): raise HTTPException(503, {"failureClass": "peer_unreachable"})
    system.sender._post_peer = offline
    asyncio.run(source.process_once())
    def authorize(authority):
        if authority.get("deviceId") == "old": raise HTTPException(403, "distribution_device_revoked")
    source.authorize = authorize
    current = source.store.get(job["jobId"])
    source.action(job["jobId"], "owner", "retry", {"commandId": "retry", "revision": current["revision"]}, authority={"deviceId": "new"})
    system.sender._post_peer = system.deliver
    asyncio.run(source.process_once())
    assert source.store.get(job["jobId"])["state"] == "cancelled"
