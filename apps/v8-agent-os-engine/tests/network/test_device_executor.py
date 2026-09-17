"""Adversarial transport tests with isolated Engine identity and real SQLite."""
from __future__ import annotations

import concurrent.futures
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.client_identity.service import ClientIdentityService
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from runtimes.network_supervisor.executors.protocol import ExecutorError, canonical, digest, parse
from runtimes.network_supervisor.executors.service import ExecutorService


@pytest.fixture
def fixture(tmp_path):
    clock = [1_800_000_000.0]
    identity = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), clock=lambda: clock[0])
    owner = identity.owners.bootstrap(login="fixture", name="Fixture", now=clock[0])["id"]
    service = ExecutorService(identity)
    ticket = service.identities.ticket(owner, device_class="esp32", name="Logic fixture", base_url="https://engine.example")
    enrolled = service.enroll({**ticket, "deviceClass": "esp32"})
    principal = service.identities.verify(enrolled["credential"])
    capabilities = [{"capability": c, "resourceId": "logic.led"} for c in ("sensor.read", "actuator.set")]
    service.grant(owner, enrolled["deviceId"], 1, capabilities)
    hello = {"type": "hello", "protocolVersion": 1, "authorityId": enrolled["authorityId"], "deviceId": enrolled["deviceId"],
             "bootId": "boot1", "controlSessionId": "arm1", "localEnabled": True, "capabilityRevision": 1, "capabilities": capabilities}
    session = service.hello(principal, hello)
    return SimpleNamespace(service=service, identity=identity, owner=owner, clock=clock, enrolled=enrolled,
                           principal=principal, capabilities=capabilities, hello=hello, epoch=session["leaseEpoch"], path=tmp_path)


def create(f, command_id="command1", *, capability="actuator.set", **overrides):
    values = dict(owner=f.owner, command_id=command_id, device=f.enrolled["deviceId"], capability=capability,
                  resource="logic.led", arguments={"level": True, "maxHoldMs": 1000}, precondition={"resourceRevision": 0},
                  ttl_ms=5000, trace={"runId": "run1", "episodeId": "episode1"})
    if capability == "sensor.read":
        values.update(arguments={}, precondition={})
    values.update(overrides)
    return f.service.create(**values)


def dispatch(f, **kwargs):
    result = create(f, **kwargs)
    f.service.activate(f.owner, result["commandId"])
    commands = f.service.outbound(f.enrolled["deviceId"], f.epoch)
    assert len(commands) == 1
    assert commands[0]["commandDigest"] == digest(commands[0])
    return commands[0]


def receipt(command, status, seq, **extra):
    return {"type": "receipt", "protocolVersion": 1, **{k: command[k] for k in (
        "commandId", "commandDigest", "deviceId", "authorityId", "bootId", "controlSessionId", "leaseEpoch", "grantRevision")},
        "status": status, "receiptSeq": seq, "deviceMonotonicMs": 100 + seq, **extra}


def test_single_use_enrollment_race_and_role_separation(fixture):
    f = fixture
    ticket = f.service.identities.ticket(f.owner, device_class="android", name="Fixture", base_url="https://engine.example")
    def consume(_):
        try:
            return f.service.enroll({**ticket, "deviceClass": "android"})["deviceId"]
        except Exception:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert len([r for r in pool.map(consume, range(2)) if r]) == 1
    phone = f.identity.create_session(name="Human", surface="phone")
    with pytest.raises(Exception, match="executor_credential_required"):
        f.service.identities.verify(phone["accessToken"])
    assert f.identity.verify_access(f.enrolled["credential"]) is None
    with f.identity.database() as db:
        stored = " ".join(str(tuple(r)) for r in db.execute("SELECT * FROM executor_identities"))
    assert f.enrolled["credential"] not in stored


def test_duplicate_is_immutable_and_transport_never_resends(fixture):
    f = fixture
    original = dispatch(f)
    assert create(f)["status"] == "sent"
    assert f.service.outbound(original["deviceId"], f.epoch) == []
    with pytest.raises(ExecutorError, match="command_id_conflict"):
        create(f, arguments={"level": False, "maxHoldMs": 1000})
    for state, seq in [("received", 1), ("started", 2), ("succeeded", 3)]:
        result = f.service.receipt(original["deviceId"], f.epoch, receipt(original, state, seq))
    assert result["businessVerification"] == "unverified"
    assert create(f)["receipt"]["status"] == "succeeded"
    assert f.service.outbound(original["deviceId"], f.epoch) == []


def test_disconnect_after_send_before_ack_is_unknown_and_query_only_after_reboot(fixture):
    f = fixture
    command = dispatch(f)
    f.service.disconnect(command["deviceId"], f.epoch)
    assert f.service.status(f.owner, command["commandId"])["status"] == "unknown_outcome"
    # Reopen the persistent service, with a new device boot and a higher epoch.
    f.service = ExecutorService(f.identity)
    session = f.service.hello(f.principal, {**f.hello, "bootId": "boot2", "controlSessionId": "arm2"})
    assert session["leaseEpoch"] > f.epoch
    f.epoch = session["leaseEpoch"]
    assert f.service.outbound(command["deviceId"], f.epoch) == []
    assert f.service.queries(command["deviceId"]) == [{"type": "query", "commandId": command["commandId"]}]
    with pytest.raises(ExecutorError, match="outcome_reconciliation_required"):
        create(f, "command2")
    # An authenticated query can recover an old completed receipt, never rerun it.
    result = f.service.receipt(command["deviceId"], f.epoch, receipt(command, "succeeded", 3))
    assert result["status"] == "succeeded" and result["businessVerification"] == "unverified"


@pytest.mark.parametrize("at_start", [False, True])
def test_cancel_preserves_actual_late_completion(fixture, at_start):
    f = fixture
    command = dispatch(f)
    if at_start:
        f.service.receipt(command["deviceId"], f.epoch, receipt(command, "started", 2))
    result = f.service.cancel(f.owner, command["commandId"])
    assert result["cancelRequested"] and result["status"] != "cancelled"
    result = f.service.receipt(command["deviceId"], f.epoch, receipt(command, "succeeded", 3))
    assert result["cancelRequested"] and result["status"] == "succeeded"
    with pytest.raises(ExecutorError, match="terminal_receipt_conflict"):
        f.service.receipt(command["deviceId"], f.epoch, receipt(command, "started", 4))


def test_cancel_before_send_proves_no_execution(fixture):
    f = fixture
    create(f)
    assert f.service.cancel(f.owner, "command1")["status"] == "cancelled"
    f.service.activate(f.owner, "command1")
    assert f.service.outbound(f.enrolled["deviceId"], f.epoch) == []


def test_grant_cas_revocation_and_old_channel_are_fenced(fixture):
    f = fixture
    command = dispatch(f)
    with pytest.raises(ExecutorError, match="grant_revision_conflict"):
        f.service.grant(f.owner, command["deviceId"], 1, [])
    f.service.grant(f.owner, command["deviceId"], 2, [])
    with pytest.raises(ExecutorError, match="connection_fenced"):
        f.service.outbound(command["deviceId"], f.epoch)
    f.epoch = f.service.hello(f.principal, f.hello)["leaseEpoch"]
    with pytest.raises(ExecutorError, match="capability_not_granted"):
        create(f, "read2", capability="sensor.read")
    f.service.revoke(f.owner, command["deviceId"])
    with pytest.raises(Exception, match="executor_credential_revoked"):
        f.service.identities.verify(f.enrolled["credential"])
    with pytest.raises(ExecutorError, match="executor_revoked"):
        f.service.renew(command["deviceId"], f.epoch)


def test_lease_expiry_does_not_refresh_command_deadline(fixture):
    f = fixture
    command = create(f)["command"]
    f.clock[0] += 6
    assert f.service.status(f.owner, command["commandId"])["status"] == "expired"
    f.service.renew(command["deviceId"], f.epoch)
    assert create(f)["command"]["deadlineUnixMs"] == command["deadlineUnixMs"]
    f.service.activate(f.owner, command["commandId"])
    assert f.service.outbound(command["deviceId"], f.epoch) == []


def test_two_connections_only_current_epoch_can_deliver(fixture):
    f = fixture
    old = f.epoch
    f.epoch = f.service.hello(f.principal, f.hello)["leaseEpoch"]
    create(f)
    f.service.activate(f.owner, "command1")
    with pytest.raises(ExecutorError, match="connection_fenced"):
        f.service.outbound(f.enrolled["deviceId"], old)
    # A late disconnect from the fenced socket cannot take down its replacement.
    f.service.disconnect(f.enrolled["deviceId"], old)
    assert len(f.service.outbound(f.enrolled["deviceId"], f.epoch)) == 1


def test_receipt_wrong_device_digest_and_sequence_cannot_change_state(fixture):
    f = fixture
    command = dispatch(f)
    for extra in ({"commandDigest": "0" * 64}, {"deviceId": "other"}, {"grantRevision": 1}, {"authorityId": "wrong"}):
        with pytest.raises(ExecutorError, match="receipt_identity_mismatch"):
            f.service.receipt(command["deviceId"], f.epoch, {**receipt(command, "succeeded", 1), **extra})
    good = receipt(command, "started", 2)
    f.service.receipt(command["deviceId"], f.epoch, good)
    assert f.service.receipt(command["deviceId"], f.epoch, good)["status"] == "started"
    with pytest.raises(ExecutorError, match="receipt_sequence_conflict"):
        f.service.receipt(command["deviceId"], f.epoch, receipt(command, "succeeded", 2))
    assert f.service.status(f.owner, command["commandId"])["status"] == "started"


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"a":NaN}', '{"a":1e999}', '{"x":' + '[' * 20 + '1' + ']' * 20 + '}', '{"x":"' + 'x' * 17000 + '"}'])
def test_malformed_frames_rejected(raw):
    with pytest.raises(ExecutorError):
        parse(raw)


def test_real_asgi_websocket_auth_receipts_and_revoke(fixture, monkeypatch):
    from api import device_executor_routes as routes
    from core import client_identity
    from core.remote_link.phone_gateway import create_phone_gateway_app
    f = fixture
    monkeypatch.setattr(routes, "get_executor_service", lambda: f.service)
    monkeypatch.setattr(client_identity, "_service", f.identity)
    app = FastAPI()
    app.include_router(routes.router)
    gateway = create_phone_gateway_app(client_app=app)
    with TestClient(gateway) as client:
        human = f.identity.create_session(name="Phone fixture", surface="phone")
        response = client.get("/api/client/executors", headers={"Authorization": "Bearer " + f.enrolled["credential"]})
        assert response.status_code == 401
        response = client.get("/api/client/executors", headers={"Authorization": "Bearer " + human["accessToken"]})
        assert response.status_code == 200 and response.json()["items"][0]["deviceId"] == f.enrolled["deviceId"]
        with pytest.raises(Exception):
            with client.websocket_connect("/api/executor/ws", headers={"Authorization": "Bearer " + human["accessToken"]}):
                pass
        with client.websocket_connect("/api/executor/ws", headers={"Authorization": "Bearer " + f.enrolled["credential"]}) as ws:
            ws.send_text(canonical(f.hello))
            f.epoch = ws.receive_json()["leaseEpoch"]
            create(f, capability="sensor.read")
            f.service.activate(f.owner, "command1")
            command = ws.receive_json()
            assert command["commandId"] == "command1"
            for state, seq in [("received", 1), ("started", 2), ("succeeded", 3)]:
                ws.send_text(canonical(receipt(command, state, seq)))
                assert ws.receive_json()["type"] == "receipt_ack"
            assert f.service.status(f.owner, "command1")["businessVerification"] == "unverified"
            f.service.revoke(f.owner, f.enrolled["deviceId"])
            with pytest.raises(Exception):
                ws.receive_json()


@pytest.mark.parametrize("outcome", ["succeeded", "unknown_outcome"])
def test_existing_episode_queue_owns_device_handoff_and_never_retries_unknown(fixture, monkeypatch, outcome):
    from core.database import DatabaseManager
    from core.runtime_episodes import build_runtime_episode
    from core import runtime_episode_runner as runner_module
    from runtimes.network_supervisor.executors import episode as device_episode
    f = fixture
    manager = DatabaseManager(f.path / "episode.db")
    monkeypatch.setattr(runner_module, "db", manager)
    monkeypatch.setattr(device_episode, "get_executor_service", lambda: f.service)
    create(f)
    episode = build_runtime_episode(kind="device_action", need={"episodeId": "episode1",
        "inputs": {"commandId": "command1", "ownerId": f.owner}, "retryPolicy": {"maxAttempts": 1}})
    manager.upsert_runtime_episode_record(episode, enqueue=True)
    runner = runner_module.RuntimeEpisodeRunner()
    claimed = manager.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["device_action"])
    assert claimed is not None
    physical_writes = []
    async def run():
        work = asyncio.create_task(runner._execute_episode(claimed))
        try:
            for _ in range(100):
                commands = f.service.outbound(f.enrolled["deviceId"], f.epoch)
                if commands:
                    command = commands[0]
                    physical_writes.append(command["commandId"])
                    if outcome == "succeeded":
                        f.service.receipt(command["deviceId"], f.epoch, receipt(command, "succeeded", 3))
                    else:
                        f.service.disconnect(command["deviceId"], f.epoch)
                    break
                await asyncio.sleep(.01)
            await asyncio.wait_for(work, 5)
        finally:
            if not work.done():
                work.cancel()
    asyncio.run(run())
    stored = manager.get_runtime_episode("episode1")
    assert stored["state"] == ("completed" if outcome == "succeeded" else "failed")
    assert stored["resultRef"]
    assert physical_writes == ["command1"]
    assert manager.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["device_action"]) is None
    assert f.service.status(f.owner, "command1")["businessVerification"] == "unverified"


def test_device_tool_uses_exact_safety_before_episode_activation(fixture, monkeypatch):
    from core import database
    from core.database import DatabaseManager
    from core.tools.native import device, tool_governance
    from erc.runtime_context import bind_runtime_context
    f = fixture
    manager = DatabaseManager(f.path / "tool.db")
    manager.create_or_update_session("test-session", "Executor fixture", user_id=f.owner)
    manager.create_run_record(run_id="tool-run", session_id="test-session", user_id=f.owner, run_type="chat", status="running")
    monkeypatch.setattr(database, "db", manager)
    monkeypatch.setattr(device, "get_executor_service", lambda: f.service)
    approvals = []
    def deny(decision, **kwargs):
        approvals.append(decision)
        assert f.service.outbound(f.enrolled["deviceId"], f.epoch) == []
        return False, "fixture denial"
    monkeypatch.setattr(tool_governance, "_enforce_safety_decision", deny)
    with bind_runtime_context(run_id="tool-run", session_id="test-session"):
        result = json.loads(device.device_broker.func(mode="execute", device_id=f.enrolled["deviceId"],
            capability="actuator.set", resource_id="logic.led", arguments={"level": True, "maxHoldMs": 1000},
            precondition={"resourceRevision": 1}, tool_call_id="tool-call"))
    assert result["code"] == "device_action_blocked"
    assert f.service.status(f.owner, result["commandId"])["status"] == "cancelled"
    target = json.loads(approvals[0].details["target"])
    assert target["grantRevision"] == 2 and target["leaseEpoch"] == f.epoch
    assert target["arguments"] == {"level": True, "maxHoldMs": 1000}
    assert approvals[0].details["exactApprovalRequired"] is True


def test_supervisor_tool_queues_existing_runtime_and_explicit_subagent_is_denied(fixture, monkeypatch):
    from core import database, runtime_episodes
    from core.database import DatabaseManager
    from core.tools.native import device
    from erc.runtime_context import bind_runtime_context
    f = fixture
    manager = DatabaseManager(f.path / "tool-queue.db")
    manager.create_or_update_session("test-session", "Executor fixture", user_id=f.owner)
    manager.create_run_record(run_id="tool-run", session_id="test-session", user_id=f.owner, run_type="chat", status="running")
    monkeypatch.setattr(database, "db", manager)
    monkeypatch.setattr(runtime_episodes, "db", manager)
    monkeypatch.setattr(device, "get_executor_service", lambda: f.service)
    with bind_runtime_context(run_id="tool-run", session_id="test-session"):
        result = json.loads(device.device_broker.func(mode="execute", device_id=f.enrolled["deviceId"],
            capability="sensor.read", resource_id="logic.led", tool_call_id="read-tool-call"))
    assert result["status"] == "queued"
    episode = manager.claim_runtime_episode(worker_id="fixture-worker", kinds=["device_action"], lease_seconds=30)
    assert episode["inputs"]["commandId"] == result["commandId"] and episode["inputs"]["ownerId"] == f.owner
    assert f.service.outbound(f.enrolled["deviceId"], f.epoch) == []  # Only the episode runner can activate it.
    with bind_runtime_context(run_id="tool-run", session_id="test-session", subagent_id="child"):
        denied = json.loads(device.device_broker.func(mode="list"))
    assert denied["code"] == "supervisor_device_tool_required"
