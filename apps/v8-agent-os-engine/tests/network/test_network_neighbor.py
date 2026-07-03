from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.database import DatabaseManager
from runtimes.network_supervisor import neighbor as neighbor_module
from runtimes.network_supervisor.models import NetworkEnvelope, NetworkPeerMutationPayload, NetworkTraceContext
from runtimes.network_supervisor.neighbor import NetworkNeighborService
from runtimes.network_supervisor.neighbor_workspace import resolve_network_neighbor_workspace_binding
from runtimes.network_supervisor import neighbor_workspace as neighbor_workspace_module


class FakeNetworkSupervisorService:
    def __init__(self) -> None:
        self.state: dict = {}
        self.upserts: list[NetworkPeerMutationPayload] = []
        self.deleted: list[str] = []
        self.posted: list[tuple[str, str, NetworkEnvelope]] = []
        self.identity = {
            "peerId": "peer_local",
            "displayName": "Main Device",
            "publicKey": "local-public-key",
            "publicKeyFingerprint": "local-fp",
            "localPeerTokenFingerprint": "local-token-fp",
            "advertisedBaseUrl": "http://main.local:9530",
            "advertisedWsUrl": "ws://main.local:9530/v1/network-supervisor/peer/ws",
            "transportProfileId": "",
            "peerBaseUrl": "",
        }
        self.secrets = {"localPeerToken": "local-token"}
        self.reload_count = 0
        self.stop_count = 0
        self.config = SimpleNamespace(
            enabled=False,
            discovery=SimpleNamespace(lan_enabled=False),
            node=SimpleNamespace(display_name="Main Device"),
        )
        self.peers_payload = {
            "trustedItems": [],
            "discoveredItems": [
                {
                    "peerId": "peer_remote",
                    "displayName": "Remote Device",
                    "online": True,
                    "lastSeenAt": "2026-07-02T00:00:00Z",
                    "source": "lan",
                    "baseUrl": "http://remote.local:9530",
                    "wsUrl": "ws://remote.local:9530/v1/network-supervisor/peer/ws",
                    "publicKey": "remote-public-key",
                    "publicKeyFingerprint": "remote-fp",
                }
            ],
            "meshCandidates": [],
        }

    def read_state(self) -> dict:
        return self.state

    def write_state(self, payload: dict) -> None:
        self.state = dict(payload)

    def ensure_local_identity(self) -> dict:
        return dict(self.identity)

    def read_secrets(self) -> dict:
        return dict(self.secrets)

    def list_peers_payload(self) -> dict:
        return self.peers_payload

    def list_peers(self) -> list[dict]:
        return [*self.peers_payload.get("trustedItems", []), *self.peers_payload.get("discoveredItems", [])]

    def status_payload(self) -> dict:
        return {
            "enabled": True,
            "started": True,
            "node": {"peerId": self.identity["peerId"], "displayName": self.identity["displayName"]},
            "discovery": {"lanEnabled": True, "lastAnnounceAt": "2026-07-02T00:00:00Z"},
        }

    def get_config_model(self):
        return self.config

    def save_config_model(self, config) -> None:
        self.config = config

    async def reload(self) -> None:
        self.reload_count += 1

    async def stop(self) -> None:
        self.stop_count += 1

    def upsert_peer(self, payload: NetworkPeerMutationPayload) -> dict:
        self.upserts.append(payload)
        return {"ok": True, "peerId": payload.peer_id}

    def delete_peer(self, peer_id: str) -> dict:
        self.deleted.append(peer_id)
        return {"ok": True, "peerId": peer_id}

    def verify_envelope(self, envelope: NetworkEnvelope, **_kwargs) -> dict:
        return {"publicKey": envelope.payload.get("publicKey") or "remote-public-key", "trustedPeer": None}

    def build_envelope(self, *, message_type: str, to_peer_id: str, payload: dict, trace=None, expires_in_seconds: int = 60) -> NetworkEnvelope:
        return NetworkEnvelope.model_validate(
            {
                "version": "1",
                "messageId": f"msg_{message_type}",
                "messageType": message_type,
                "sentAt": "2026-07-02T00:00:00Z",
                "expiresAt": "2026-07-02T00:05:00Z",
                "fromPeerId": self.identity["peerId"],
                "toPeerId": to_peer_id,
                "nonce": f"nonce_{message_type}",
                "signature": "sig",
                "trace": (trace or NetworkTraceContext()).model_dump(by_alias=True),
                "payload": payload,
            }
        )

    async def _post_peer(self, peer_id: str, path: str, envelope: NetworkEnvelope) -> dict:
        self.posted.append((peer_id, path, envelope))
        return {
            "version": "1",
            "messageId": "msg_neighbor.message.ack",
            "messageType": "neighbor.message.ack",
            "sentAt": "2026-07-02T00:00:00Z",
            "expiresAt": "2026-07-02T00:05:00Z",
            "fromPeerId": peer_id,
            "toPeerId": self.identity["peerId"],
            "nonce": "nonce_ack",
            "signature": "sig",
            "trace": {},
            "payload": {"status": "received"},
        }


@pytest.fixture()
def neighbor_service(monkeypatch, tmp_path):
    fake_network = FakeNetworkSupervisorService()
    temp_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(neighbor_module, "network_supervisor_service", fake_network)
    monkeypatch.setattr(neighbor_module, "db", temp_db)
    monkeypatch.setattr(
        neighbor_workspace_module.workspace_resolution_service,
        "get_main_workspace_path",
        lambda: str(tmp_path / "workspace"),
    )
    return NetworkNeighborService(), fake_network, temp_db, tmp_path


def test_discovered_candidate_does_not_auto_become_trusted_link(neighbor_service):
    svc, _fake_network, temp_db, _tmp_path = neighbor_service

    candidates = svc.list_candidates()

    assert candidates["items"][0]["peerId"] == "peer_remote"
    assert temp_db.list_network_neighbor_links() == []


def test_switch_starts_and_stops_wake_queue(monkeypatch, neighbor_service):
    svc, fake_network, _temp_db, _tmp_path = neighbor_service
    started: list[bool] = []
    stopped: list[bool] = []

    async def fake_start():
        started.append(True)

    async def fake_stop():
        stopped.append(True)

    monkeypatch.setattr(svc, "start", fake_start)
    monkeypatch.setattr(svc, "stop", fake_stop)

    asyncio.run(svc.set_switch(enabled=True))
    asyncio.run(svc.set_switch(enabled=False))

    assert started == [True]
    assert stopped == [True]
    assert fake_network.reload_count == 1
    assert fake_network.stop_count == 1


def test_pairing_code_is_one_time_and_limited(neighbor_service):
    svc, _fake_network, _temp_db, _tmp_path = neighbor_service
    invitation = svc.create_pairing_invitation(local_nickname="Main")

    consumed = svc._consume_local_pairing_code(invitation["code"])

    assert consumed["localNickname"] == "Main"
    with pytest.raises(Exception):
        svc._consume_local_pairing_code(invitation["code"])


def test_pairing_consume_writes_trust_link_and_roles(neighbor_service):
    svc, fake_network, temp_db, _tmp_path = neighbor_service
    invitation = svc.create_pairing_invitation(local_role="primary", local_nickname="Main")
    envelope = NetworkEnvelope.model_validate(
        {
            "version": "1",
            "messageId": "msg_pair",
            "messageType": "neighbor.pairing.consume",
            "sentAt": "2026-07-02T00:00:00Z",
            "expiresAt": "2026-07-02T00:05:00Z",
            "fromPeerId": "peer_remote",
            "toPeerId": "peer_local",
            "nonce": "nonce_pair",
            "signature": "sig",
            "trace": {},
            "payload": {
                "code": invitation["code"],
                "displayName": "Remote Device",
                "nickname": "Remote",
                "baseUrl": "http://remote.local:9530",
                "wsUrl": "ws://remote.local:9530/v1/network-supervisor/peer/ws",
                "publicKey": "remote-public-key",
                "peerToken": "remote-token",
            },
        }
    )

    response = svc.handle_pairing_consume(envelope)
    link = temp_db.get_network_neighbor_link_by_peer("peer_remote")

    assert response.message_type == "neighbor.pairing.accepted"
    assert response.payload["peerToken"] == "local-token"
    assert fake_network.upserts[0].peer_id == "peer_remote"
    assert fake_network.upserts[0].peer_token == "remote-token"
    assert link["localRole"] == "primary"
    assert link["remoteRole"] == "companion"
    assert link["remoteNickname"] == "Remote"


def test_remote_workspace_path_maps_to_local_compatible_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(
        neighbor_workspace_module.workspace_resolution_service,
        "get_main_workspace_path",
        lambda: str(tmp_path / "workspace"),
    )

    binding = resolve_network_neighbor_workspace_binding(
        peer_id="peer_windows",
        local_role="companion",
        remote_workspace_id="main",
        remote_workspace_path=r"E:\Projects\v8chat\v8-agent-os",
    )

    assert binding["source"] == "network_compatible_workspace"
    assert binding["remoteWorkspacePath"] == r"E:\Projects\v8chat\v8-agent-os"
    assert binding["workspacePath"] != r"E:\Projects\v8chat\v8-agent-os"
    assert str(tmp_path / "workspace") in binding["workspacePath"]


def test_neighbor_message_pool_truncates_preview_not_body(neighbor_service):
    svc, _fake_network, temp_db, _tmp_path = neighbor_service
    link = temp_db.upsert_network_neighbor_link(
        link_id="nlink_test",
        peer_id="peer_remote",
        local_nickname="Main",
        remote_nickname="Remote",
        local_role="primary",
        remote_role="companion",
        workspace_binding={},
    )
    body = "hello " * 300

    asyncio.run(svc.send_message(link_id=link["linkId"], body=body))
    timeline = svc.timeline(link["linkId"])

    assert len(timeline["items"]) == 1
    assert timeline["items"][0]["body"] == body
    assert len(timeline["items"][0]["preview"]) < len(body)


def test_send_message_queues_relay_when_relay_available(monkeypatch, neighbor_service):
    svc, fake_network, temp_db, _tmp_path = neighbor_service
    link = temp_db.upsert_network_neighbor_link(
        link_id="nlink_relay",
        peer_id="peer_remote",
        local_nickname="Main",
        remote_nickname="Remote",
        local_role="primary",
        remote_role="companion",
        workspace_binding={},
    )
    queued: list[dict] = []

    import runtimes.network_supervisor.relay_runtime as relay_runtime_module

    monkeypatch.setattr(relay_runtime_module.network_relay_worker_service, "relay_available", lambda: True)
    monkeypatch.setattr(
        relay_runtime_module.network_relay_worker_service,
        "enqueue_outbox",
        lambda **kwargs: queued.append(kwargs) or {"outboxId": "nrout_test"},
    )

    result = asyncio.run(svc.send_message(link_id=link["linkId"], body="hello relay"))

    assert result["delivery"]["status"] == "queued_via_relay"
    assert result["delivery"]["outboxId"] == "nrout_test"
    assert queued[0]["target_peer_id"] == "peer_remote"
    assert queued[0]["local_message_id"]
    assert not fake_network.posted


def test_wake_supervisor_message_schedules_run(monkeypatch, neighbor_service):
    svc, fake_network, temp_db, _tmp_path = neighbor_service
    link = temp_db.upsert_network_neighbor_link(
        link_id="nlink_test",
        peer_id="peer_remote",
        local_nickname="Main",
        remote_nickname="Remote",
        local_role="companion",
        remote_role="primary",
        workspace_binding={},
    )
    scheduled = []

    async def fake_execute(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(svc, "_kick_wake_queue_processing", lambda: None)
    monkeypatch.setattr(svc, "_execute_neighbor_supervisor_message", fake_execute)
    envelope = NetworkEnvelope.model_validate(
        {
            "version": "1",
            "messageId": "msg_neighbor",
            "messageType": "neighbor.message",
            "sentAt": "2026-07-02T00:00:00Z",
            "expiresAt": "2026-07-02T00:05:00Z",
            "fromPeerId": "peer_remote",
            "toPeerId": "peer_local",
            "nonce": "nonce_neighbor",
            "signature": "sig",
            "trace": {},
            "payload": {
                "messageId": "remote_msg_1",
                "body": "请本机主理人处理一下",
                "wakeSupervisor": True,
                "fromNickname": "Remote",
                "role": "primary",
            },
        }
    )

    ack = asyncio.run(svc.handle_peer_message(envelope))

    assert ack.message_type == "neighbor.message.ack"
    assert ack.payload["runScheduled"] is True
    assert ack.payload["queueId"]
    queued = temp_db.list_network_neighbor_wake_queue(states=["queued"])
    assert len(queued) == 1
    assert queued[0]["messageId"] == temp_db.list_network_neighbor_messages(link_id=link["linkId"])[0]["messageId"]

    processed = asyncio.run(svc.process_wake_queue_once(worker_id="test-worker"))

    assert processed is True
    assert scheduled
    completed = temp_db.list_network_neighbor_wake_queue(states=["completed"])
    assert len(completed) == 1
    assert temp_db.list_network_neighbor_messages(link_id=link["linkId"])[0]["direction"] == "inbound"


def test_wake_queue_failure_retries_without_losing_item(monkeypatch, neighbor_service):
    svc, _fake_network, temp_db, _tmp_path = neighbor_service
    link = temp_db.upsert_network_neighbor_link(
        link_id="nlink_retry",
        peer_id="peer_remote",
        local_nickname="Main",
        remote_nickname="Remote",
        local_role="companion",
        remote_role="primary",
        workspace_binding={},
    )
    message = temp_db.add_network_neighbor_message(
        message_id="nmsg_retry",
        link_id=link["linkId"],
        direction="inbound",
        from_peer_id="peer_remote",
        from_nickname="Remote",
        role="primary",
        body="wake me",
        preview="wake me",
        status="received",
        workspace_binding={},
    )
    temp_db.add_network_neighbor_wake_queue_item(
        queue_id="nwake_retry",
        link_id=link["linkId"],
        message_id=message["messageId"],
        run_id="run_retry",
        payload={"link": link, "inboundMessage": message, "workspaceBinding": {}},
        max_attempts=2,
    )

    async def fail_execute(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "_execute_neighbor_supervisor_message", fail_execute)

    processed = asyncio.run(svc.process_wake_queue_once(worker_id="test-worker"))

    assert processed is True
    retry = temp_db.list_network_neighbor_wake_queue(states=["retry"])
    assert len(retry) == 1
    assert retry[0]["lastError"] == "boom"
