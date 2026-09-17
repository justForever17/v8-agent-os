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


def test_reprepare_after_lost_apply_receipt_reconciles_across_restart(system):
    source = system.source
    created = source.create("owner", {}, {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]})
    asyncio.run(source.process_once())
    job = source.store.get(created["jobId"])
    source.action(job["jobId"], "owner", "confirm", {"commandId": "confirm", "revision": job["revision"], "planDigest": job["planDigest"]})
    async def lost(peer, path, envelope):
        await system.deliver(peer, path, envelope)
        raise HTTPException(503, {"failureClass": "peer_unreachable"})
    system.sender._post_peer = lost
    asyncio.run(source.process_once())
    job = source.store.get(job["jobId"])
    source.action(job["jobId"], "owner", "prepare", {"commandId": "prepare", "revision": job["revision"]})
    system.sender._post_peer = system.deliver
    recovered = ConfigDistributionService(store=DistributionStore(system.db), network=system.sender, neighbors=source.neighbors, authorize=lambda _: None)
    asyncio.run(recovered.process_once())
    job = recovered.store.get(job["jobId"])
    assert job["state"] == "completed" and job["targets"][0]["state"] == "committed"
    assert job["targets"][0]["generation"] == 1
    with system.db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM config_broker_transactions").fetchone()[0] == 1


def test_cancel_before_new_generation_reaches_target_converges(system):
    source = system.source
    created = source.create("owner", {}, {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]})
    asyncio.run(source.process_once())
    old = source.store.get(created["jobId"])
    old_plan = {"jobId": old["jobId"], "generation": 1, "receipt": old["targets"][0]["receipt"]}
    source.action(old["jobId"], "owner", "prepare", {"commandId": "prepare", "revision": old["revision"]})
    new = source.store.get(old["jobId"])
    command = {"commandId": "cancel", "revision": new["revision"]}
    source.action(new["jobId"], "owner", "cancel", command)
    asyncio.run(source.process_once())
    assert source.store.get(new["jobId"])["state"] == "cancelled"
    assert source.action(new["jobId"], "owner", "cancel", command)["state"] == "cancelled"
    with pytest.raises(HTTPException): apply(system, "apply", old_plan)
    late = receive(system, "prepare", jobId=new["jobId"], generation=2, templateId=new["templateId"], values=new["values"], mapping=new["targets"][0]["mapping"])
    assert late["state"] == "cancelled"
    assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 100


def test_pending_jobs_are_not_evicted_by_terminal_history(system):
    source = system.source
    created = source.create("owner", {}, {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]})
    original = source.store.get(created["jobId"])
    for index in range(55):
        completed = {**deepcopy(original), "jobId": f"done-{index}", "createdAt": f"9999-{index:02}", "state": "completed"}
        source.store.command("owner", f"done-{index}", f"digest-{index}", completed["jobId"], create=completed)
    catalog = source.inventory("owner", "instance")
    assert catalog["pendingCount"] == 1 and len(catalog["jobs"]) == 20
    assert catalog["jobs"][0]["jobId"] == created["jobId"]
    assert catalog["jobs"][0]["summary"] and catalog["jobs"][0]["targets"] == []
    assert catalog["jobsNextCursor"] == "20"
    assert len(source.store.page("owner", 20)["items"]) == 20
    assert source.store.page("owner", 40)["nextCursor"] is None


def test_unmapped_target_is_blocked_individually_and_remapping_is_explicit(system):
    source = system.source
    system.plane.mutate_config(lambda config: {**config, "roles": {**config["roles"], "summary": "source-only::model"}})
    job = source.create("owner", {}, {"commandId": "create", "templateId": "model-roles", "targets": [{"linkId": "target_link", "mapping": {"roles": {}, "models": {}}}]})
    asyncio.run(source.process_once())
    blocked = source.store.get(job["jobId"])
    assert blocked["targets"][0]["state"] == "blocked"
    assert blocked["targets"][0]["missingRequirements"] == ["target_model_mapping_required"]
    source.action(job["jobId"], "owner", "prepare", {"commandId": "map", "revision": blocked["revision"],
        "targets": [{"linkId": "target_link", "mapping": {"models": {"summary": "missing::model"}}}]})
    asyncio.run(source.process_once())
    assert source.store.get(job["jobId"])["targets"][0]["errorCode"] == "model_not_found"
    assert not source.store.get(job["jobId"])["targets"][0]["approved"]


def test_bounded_hundred_target_prepare_partial_mapping_and_receipts(system):
    source = system.source
    source._link = lambda **kwargs: ({"linkId": kwargs["link_id"], "peerId": kwargs["link_id"], "remoteNickname": kwargs["link_id"]}, kwargs["link_id"])
    counters = {"active": 0, "peak": 0}
    async def exchange(peer_id, action, body):
        counters["active"] += 1; counters["peak"] = max(counters["peak"], counters["active"])
        try:
            await asyncio.sleep(0.002)
            index = int(peer_id.removeprefix("peer-"))
            if index % 5 == 0: raise HTTPException(503, {"failureClass": "peer_unreachable"})
            return {"state": "prepared", "receipt": {"transactionId": peer_id, "planDigest": peer_id, "expiresAt": 9999999999}, "diff": [], "errorCode": "", "missingRequirements": []}
        finally: counters["active"] -= 1
    source._exchange = exchange
    job = source.create("owner", {}, {"commandId": "scale", "templateId": "model-policy", "targets": [{"linkId": f"peer-{index}"} for index in range(100)]})
    asyncio.run(source.process_once())
    state = source.store.get(job["jobId"])
    assert counters["peak"] == 4
    assert len([target for target in state["targets"] if target["state"] == "prepared"]) == 80
    assert len([target for target in state["targets"] if target["state"] == "offline"]) == 20
    assert state["state"] == "awaiting_confirmation" and not any(target["approved"] for target in state["targets"])


def test_local_workspace_selection_requires_trust_revision_and_never_accepts_path(system, tmp_path, monkeypatch):
    from core.config_distribution_local import local_workspaces, bind_local_workspace
    from core.storage import storage
    from runtimes.memory.models import ProjectDescriptor
    from runtimes.memory.project_registry import project_registry_service
    path = tmp_path / "target-project"; path.mkdir()
    project = ProjectDescriptor(id="fixture-project", name="Fixture Project", workspacePath=str(path), workspaceId="fixture-workspace", workspaceTrustState="trusted")
    storage.save_projects_registry({"projects": [project.model_dump(by_alias=True)], "defaultProjectId": "fixture-project"})
    def update_link(link_id, body, **_expected):
        system.source_link["workspaceBinding"] = body["workspaceBinding"]
    monkeypatch.setattr(system.source.neighbors, "update_link", update_link, raising=False)
    catalog = local_workspaces(system.source)
    selected = catalog["projects"][0]; link = catalog["links"][0]
    payload = {"projectId": selected["projectId"], "projectRevision": selected["revision"], "linkRevision": link["revision"], "trustConfirmed": True}
    with pytest.raises(HTTPException): bind_local_workspace(system.source, link["linkId"], {**payload, "workspacePath": "C:/source-private"})
    with pytest.raises(HTTPException): bind_local_workspace(system.source, link["linkId"], {**payload, "trustConfirmed": False})
    assert not system.source_link.get("workspaceBinding")
    result = bind_local_workspace(system.source, link["linkId"], payload)
    assert result["links"][0]["localPath"] == str(path)
    assert system.source_link["workspaceBinding"]["workspacePath"] == str(path)
    assert not any("Path" in key for template in system.source.inventory("owner", "instance")["templates"] for key in template["values"])
    project_registry_service.patch_project("fixture-project", {"name": "Changed"})
    with pytest.raises(HTTPException): bind_local_workspace(system.source, link["linkId"], payload)


@pytest.mark.parametrize("operation", ["cancel_after_commit", "withdraw_after_local_change"])
def test_lost_cleanup_receipt_restart_preserves_actual_write_history(system, monkeypatch, operation):
    source = system.source
    created = source.create("owner", {}, {"commandId": "create", "templateId": "model-policy", "targets": [{"linkId": "target_link"}]})
    source.store.mutate(created["jobId"], lambda job: job["values"]["governance"]["budgets"].update(runMaxTokens=777))
    asyncio.run(source.process_once())
    def act(action):
        current = source.store.get(created["jobId"])
        return source.action(current["jobId"], "owner", action, {"commandId": action, "revision": current["revision"], "planDigest": current["planDigest"]})
    commits, restores = [], []
    original_commit, original_restore = system.broker.commit, system.broker._restore_snapshot
    def commit(*args, **kwargs): commits.append(1); return original_commit(*args, **kwargs)
    def restore(*args, **kwargs): restores.append(1); return original_restore(*args, **kwargs)
    monkeypatch.setattr(system.broker, "commit", commit)
    monkeypatch.setattr(system.broker, "_restore_snapshot", restore)
    act("confirm")
    if operation == "withdraw_after_local_change":
        asyncio.run(source.process_once()); act("withdraw")
    async def lost(peer, path, envelope):
        await system.deliver(peer, path, envelope)
        if operation == "cancel_after_commit": act("cancel")
        raise HTTPException(503, {"failureClass": "peer_unreachable"})
    system.sender._post_peer = lost
    asyncio.run(source.process_once())
    if operation == "withdraw_after_local_change":
        system.plane.mutate_config(lambda config: {**config, "governance": {**config["governance"], "budgets": {**config["governance"]["budgets"], "runMaxTokens": 999}}})
    system.target = ConfigDistributionService(store=DistributionStore(system.db), network=system.receiver, neighbors=system.target.neighbors, authorize=lambda _: None)
    source = ConfigDistributionService(store=DistributionStore(system.db), network=system.sender, neighbors=source.neighbors, authorize=lambda _: None)
    system.sender._post_peer = system.deliver
    if operation == "withdraw_after_local_change": act("retry")
    asyncio.run(source.process_once())
    result = source.store.get(created["jobId"])
    assert len(commits) == 1
    if operation == "cancel_after_commit":
        assert result["state"] == "cancelled" and result["targets"][0]["state"] == "committed"
        assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 777 and not restores
    else:
        assert result["state"] == "withdrawn" and result["targets"][0]["state"] == "rolled_back" and len(restores) == 1
        assert result["targets"][0]["receipt"]["readback"]["governance.budgets.runMaxTokens"] == 100
        assert system.plane.get_config()["governance"]["budgets"]["runMaxTokens"] == 999


@pytest.fixture
def local_binding(system, tmp_path, monkeypatch):
    import core.storage as storage_module
    import runtimes.memory.project_registry as registry_module
    import persistence.repositories.scope_binding_repository as scope_module
    import runtimes.network_supervisor.neighbor as neighbor_module
    from runtimes.memory.models import ProjectDescriptor
    from core.config_distribution_local import local_workspaces
    monkeypatch.setattr(registry_module, "db", system.db)
    monkeypatch.setattr(scope_module, "db", system.db)
    monkeypatch.setattr(neighbor_module, "db", system.db)
    monkeypatch.setattr(neighbor_module, "network_supervisor_service", system.sender)
    monkeypatch.setattr(system.sender, "list_peers", lambda: [])
    system.source._neighbors = neighbor_module.NetworkNeighborService()
    selected, newer = tmp_path / "selected", tmp_path / "newer"
    selected.mkdir(); newer.mkdir()
    projects = [ProjectDescriptor(id="selected", name="Selected", workspacePath=str(selected), workspaceId="selected-workspace", workspaceTrustState="restricted"),
                ProjectDescriptor(id="newer", name="Newer", workspacePath=str(newer), workspaceId="newer-workspace", workspaceTrustState="trusted")]
    storage_module.storage.save_projects_registry({"projects": [project.model_dump(by_alias=True) for project in projects], "defaultProjectId": "selected"})
    system.db.upsert_network_neighbor_link(link_id="target_link", peer_id="target", local_nickname="Source", remote_nickname="Target", local_role="primary", remote_role="companion")
    catalog = local_workspaces(system.source)
    project = next(item for item in catalog["projects"] if item["projectId"] == "selected")
    return SimpleNamespace(system=system, registry=registry_module.project_registry_service, selected=selected, newer=newer,
                           payload={"projectId": "selected", "projectRevision": project["revision"], "linkRevision": catalog["links"][0]["revision"], "trustConfirmed": True})


@pytest.mark.parametrize("writer", ["link", "project"])
def test_binding_owner_cas_rejects_newer_worker_write(local_binding, monkeypatch, writer):
    import threading
    from pathlib import Path
    from core.config_distribution_local import bind_local_workspace
    fixture = local_binding
    at_gap, done = threading.Event(), threading.Event()
    errors = []
    def write():
        try:
            assert at_gap.wait(5)
            if writer == "link":
                fixture.system.source.neighbors.update_link("target_link", {"workspaceBinding": {"projectId": "newer", "workspaceId": "newer-workspace", "workspacePath": str(fixture.newer)}})
            else:
                fixture.registry.patch_project("selected", {"workspacePath": str(fixture.newer), "workspaceTrustState": "restricted", "workspaceTrustSource": "newer_owner_decision"})
        except BaseException as exc: errors.append(exc)
        finally: done.set()
    thread = threading.Thread(target=write); thread.start()
    original_is_dir = Path.is_dir
    owner_thread = threading.get_ident()
    armed = [True]
    def gap(path):
        if armed[0] and threading.get_ident() == owner_thread and path == fixture.selected:
            armed[0] = False; at_gap.set(); assert done.wait(5)
        return original_is_dir(path)
    monkeypatch.setattr(Path, "is_dir", gap)
    with pytest.raises(HTTPException) as error:
        bind_local_workspace(fixture.system.source, "target_link", fixture.payload)
    thread.join(5)
    assert error.value.status_code == 409 and not thread.is_alive() and not errors
    link = fixture.system.db.get_network_neighbor_link("target_link")
    project = fixture.registry.get_project("selected")
    assert link["workspaceBinding"].get("workspacePath") != str(fixture.selected)
    assert project.workspace_trust_state == "restricted"
    if writer == "project": assert project.workspace_path == str(fixture.newer)


@pytest.mark.parametrize("fail_restore", [False, True])
def test_link_failure_restores_only_confirmed_trust_or_reports_partial(local_binding, monkeypatch, fail_restore):
    from core.config_distribution_local import bind_local_workspace
    fixture = local_binding
    original_save = fixture.registry.project_repo.save_project
    def save(project):
        if fail_restore and project.workspace_trust_state == "restricted": raise OSError("fixture restore failure")
        return original_save(project)
    monkeypatch.setattr(fixture.registry.project_repo, "save_project", save)
    original_update = fixture.system.db.upsert_network_neighbor_link
    def concurrent_binding(**kwargs):
        assert fixture.registry.get_project("selected").workspace_trust_state == "trusted"
        original_update(link_id="target_link", peer_id="target", local_nickname="Source", remote_nickname="Target", local_role="primary", remote_role="companion",
                        workspace_binding={"workspacePath": str(fixture.newer)})
        return original_update(**kwargs)
    monkeypatch.setattr(fixture.system.db, "upsert_network_neighbor_link", concurrent_binding)
    with pytest.raises(HTTPException) as error:
        bind_local_workspace(fixture.system.source, "target_link", fixture.payload)
    assert error.value.status_code == 409
    assert error.value.detail == ("distribution_local_trust_recovery_required" if fail_restore else "distribution_local_workspace_changed")
    assert fixture.system.db.get_network_neighbor_link("target_link")["workspaceBinding"]["workspacePath"] == str(fixture.newer)
    assert fixture.registry.get_project("selected").workspace_trust_state == ("trusted" if fail_restore else "restricted")
