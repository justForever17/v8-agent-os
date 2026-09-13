from __future__ import annotations

import asyncio
import base64
import json
import threading
import socket
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from runtimes.network_supervisor.models import NetworkEnvelope, NetworkSupervisorRuntimeConfig, NetworkTraceContext
from runtimes.network_supervisor.service import NetworkSupervisorService
from runtimes.network_supervisor.transport_setup import advertised_endpoint, normalize_peer_origin, parse_connection_invitation


def node(peer_id):
    service = NetworkSupervisorService()
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    config = NetworkSupervisorRuntimeConfig.model_validate({"node": {"peerId": peer_id}})
    state = {}
    service.get_config_model = lambda: config
    service.read_state = lambda: state.copy()
    service.write_state = lambda value: (state.clear(), state.update(value))
    service._private_key = lambda: key
    service._local_identity = lambda: {"peerId": peer_id, "publicKey": public}
    return service, config, public


def pair():
    left, config, _ = node("left")
    right, _, public = node("right")
    left._peer_endpoint = lambda _peer: {"baseUrl": "http://fixture", "publicKey": public}
    left._peer_headers = lambda _peer: {"X-V8-Peer-Token": "fixture-only"}
    request = left.build_envelope(message_type="neighbor.message", to_peer_id="right", payload={"messageId": "message1", "body": "hello"})
    return left, right, request


def reply(right, request, **patch):
    return right.build_envelope(message_type="neighbor.message.ack", to_peer_id="left",
        payload={"requestMessageId": request.message_id, "messageId": "message1", "status": "received", **patch}).model_dump(by_alias=True)


@pytest.mark.parametrize("fault", ["unsigned", "wrong_message", "wrong_request", "html", "redirect", "different_peer", "failed_ack"])
def test_http_success_cannot_forge_delivery(fault):
    left, right, request = pair()
    data = reply(right, request)
    if fault == "unsigned": data["signature"] = ""
    if fault == "wrong_message": data = reply(right, request, messageId="other")
    if fault == "wrong_request": data = reply(right, request, requestMessageId="other")
    if fault == "different_peer": data["fromPeerId"] = "attacker"
    if fault == "failed_ack": data = reply(right, request, status="failed")
    response = httpx.Response(200, json=data)
    if fault == "html": response = httpx.Response(200, text="Cloudflare sign in")
    if fault == "redirect": response = httpx.Response(302, headers={"Location": "https://external.invalid"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _req: response)) as client:
            left._http_client = client
            with pytest.raises(HTTPException) as error:
                await left._post_peer("right", "peer/neighbors/messages", request)
            assert error.value.status_code == 502
    asyncio.run(run())


def test_two_http_nodes_verify_signed_ack_and_exact_message():
    left, right, request = pair()
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            assert self.path == "/v1/network-supervisor/peer/neighbors/messages"
            assert self.headers["X-V8-Peer-Token"] == "fixture-only"
            envelope = NetworkEnvelope.model_validate(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            right.verify_envelope(envelope, provided_public_key=left._local_identity()["publicKey"], allow_untrusted=True)
            received.append(envelope.payload["messageId"])
            encoded = json.dumps(reply(right, envelope)).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(encoded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    public = right._local_identity()["publicKey"]
    left._peer_endpoint = lambda _peer: {"baseUrl": f"http://127.0.0.1:{server.server_port}", "publicKey": public}
    async def run():
        async with httpx.AsyncClient(trust_env=False) as client:
            left._http_client = client
            result = await left._post_peer("right", "peer/neighbors/messages", request)
            assert result["payload"]["messageId"] == "message1"
            assert received == ["message1"]
    try: asyncio.run(run())
    finally: server.shutdown(); server.server_close(); thread.join(2)


def test_reload_preserves_accepted_work_and_http_client():
    async def run():
        service, _, _ = node("local")
        service._started = True
        task = asyncio.create_task(asyncio.Event().wait())
        service._active_inbound_tasks["accepted"] = task
        client = object(); service._http_client = client
        await service.reload()
        assert not task.done() and service._active_inbound_tasks["accepted"] is task
        assert service._http_client is client
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())


@pytest.mark.parametrize("origin", ["http://127.0.0.1:9528", "http://localhost", "http://[::1]", "http://0.0.0.0", "https://user:secret@example.com", "https://example.com/path", "https://x.trycloudflare.com", "http://example.com", "http://169.254.169.254"])
def test_setup_rejects_unshareable_or_credential_urls(origin):
    with pytest.raises(HTTPException): normalize_peer_origin(origin)


@pytest.mark.parametrize("origin", ["http://192.168.3.2:9528", "http://100.64.1.2:9528", "http://device.tailnet.ts.net:9528", "https://v8.example.com", "https://[fd00::2]:9528"])
def test_setup_accepts_lan_mesh_and_stable_tls(origin):
    assert normalize_peer_origin(origin) == origin


def test_loopback_broadcast_uses_admin_candidate_without_fake_websocket(monkeypatch):
    monkeypatch.setattr("runtimes.network_supervisor.transport_setup._candidate_ips", lambda: [{"address": "192.168.3.2"}])
    endpoint = advertised_endpoint({"advertisedBaseUrl": "http://127.0.0.1:9530", "advertisedWsUrl": "ws://127.0.0.1:9530"}, "http://127.0.0.1:9528")
    assert endpoint == {"advertisedBaseUrl": "http://192.168.3.2:9528", "advertisedWsUrl": "", "peerBaseUrl": "http://192.168.3.2:9528"}


def test_detected_addresses_use_real_interfaces_instead_of_fake_ip_hostname(monkeypatch):
    import psutil
    from core.v8_link import _candidate_ips
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {"wifi": SimpleNamespace(isup=True), "old": SimpleNamespace(isup=False), "tun": SimpleNamespace(isup=True)})
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        "wifi": [SimpleNamespace(family=socket.AF_INET, address="192.168.3.2")],
        "old": [SimpleNamespace(family=socket.AF_INET, address="192.168.9.2")],
        "tun": [SimpleNamespace(family=socket.AF_INET, address="198.18.0.1")],
    })
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: pytest.fail("DNS is not NIC enumeration"))
    assert _candidate_ips() == [{"address": "192.168.3.2", "family": "ipv4", "private": True}]


def invitation():
    return {"kind": "v8-peer-invitation.v1", "peerId": "remote", "displayName": "Worker", "baseUrl": "https://v8.example.com",
            "publicKey": base64.b64encode(b"a" * 32).decode(), "code": "ABCD2345",
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()}


def test_portable_invitation_does_not_need_multicast_or_peer_token():
    item = invitation()
    assert parse_connection_invitation(json.dumps(item), local_peer_id="local") == item
    assert "peerToken" not in item


@pytest.mark.parametrize("patch", [{"peerId": "local"}, {"publicKey": "invalid"}, {"code": "guess"},
    {"baseUrl": "http://127.0.0.1"}, {"baseUrl": "https://name:secret@example.com"}, {"peerToken": "secret"},
    {"expiresAt": "2000-01-01T00:00:00Z"}, {"displayName": "x" * 9000}])
def test_portable_invitation_rejects_invalid_expired_secret_and_self(patch):
    with pytest.raises(HTTPException):
        parse_connection_invitation({**invitation(), **patch}, local_peer_id="local")


def test_callback_sender_cannot_rewrite_another_peers_delegation_or_terminal_truth():
    service, _, _ = node("owner")
    writer, _, _ = node("worker")
    state = {"peerId": "worker", "direction": "outbound", "status": "completed", "result": "accepted answer"}
    service._delegation_entry = lambda _id: state
    service._store_delegation = lambda *_args: pytest.fail("must not mutate completed result")
    trace = NetworkTraceContext(delegation_id="known-task")
    duplicate = writer.build_envelope(message_type="delegation.result", to_peer_id="owner", payload={"content": "accepted answer"}, trace=trace)
    assert service.handle_protocol_callback(duplicate) == {"status": "completed", "duplicate": True}
    for message_type, payload in [("delegation.progress", {"progress": "late"}), ("delegation.result", {"content": "changed"})]:
        envelope = writer.build_envelope(message_type=message_type, to_peer_id="owner", payload=payload, trace=trace)
        with pytest.raises(HTTPException) as exc:
            service.handle_protocol_callback(envelope)
        assert exc.value.status_code == 409
    duplicate.from_peer_id = "other-trusted-peer"
    with pytest.raises(HTTPException) as exc:
        service.handle_protocol_callback(duplicate)
    assert exc.value.status_code == 403


def test_disabled_runtime_rejects_new_remote_work_before_execution():
    service, _, _ = node("receiver")
    sender, _, _ = node("sender")
    request = sender.build_envelope(message_type="delegation.request", to_peer_id="receiver", payload={"task": "never execute"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.handle_peer_delegations(request))
    assert exc.value.status_code == 403
    wake = sender.build_envelope(message_type="wake.request", to_peer_id="receiver", payload={})
    with pytest.raises(HTTPException) as exc:
        service.handle_peer_wake_request(wake)
    assert exc.value.status_code == 403


def test_duplicate_delegation_id_cannot_read_another_peers_finished_result():
    receiver, _, public = node("receiver")
    sender, _, _ = node("other-peer")
    receiver._delegation_entry = lambda _id: {"peerId": "real-owner", "direction": "inbound", "status": "completed", "task": "private task", "result": "private result"}
    request = sender.build_envelope(message_type="delegation.request", to_peer_id="receiver", payload={"task": "private task"}, trace=NetworkTraceContext(delegation_id="known-id"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(receiver._handle_inbound_delegation_request(request, public))
    assert exc.value.status_code == 403


def test_signed_callback_crosses_real_http_and_ack_binds_same_delegation():
    sender, receiver, _ = pair()
    sender.get_config_model().node.peer_id = "left"
    receiver.get_config_model().node.peer_id = "right"
    from runtimes.network_supervisor.models import TrustedPeerConfig
    receiver.get_config_model().trust.trusted_peers = [TrustedPeerConfig(peerId="left", baseUrl="http://fixture", publicKey=sender._local_identity()["publicKey"])]
    receiver.ensure_local_identity = receiver._local_identity
    changed = []
    receiver._delegation_entry = lambda _id: {"peerId": "left", "direction": "outbound", "status": "running"}
    receiver._store_delegation = lambda task_id, patch: changed.append((task_id, patch))
    request = sender.build_envelope(message_type="delegation.result", to_peer_id="right", payload={"content": "finished", "delegationId": "d1"}, trace=NetworkTraceContext(delegation_id="d1"))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            assert self.path == "/v1/network-supervisor/peer/delegations"
            assert self.headers["X-V8-Peer-Token"] == "fixture-only"
            wire = NetworkEnvelope.model_validate(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = asyncio.run(receiver.handle_peer_delegations(wire))
            encoded = response.model_dump_json(by_alias=True).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(encoded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    sender._peer_endpoint = lambda _id: {"baseUrl": f"http://127.0.0.1:{server.server_port}", "publicKey": receiver._local_identity()["publicKey"]}
    async def send():
        async with httpx.AsyncClient(trust_env=False) as client:
            sender._http_client = client
            response = await sender._post_peer("right", "peer/delegations", request)
            assert response["messageType"] == "delegation.result.ack"
            assert response["trace"]["delegationId"] == "d1"
            assert response["payload"]["requestMessageId"] == request.message_id
    try: asyncio.run(send())
    finally: server.shutdown(); server.server_close(); thread.join(2)
    assert changed[0][0] == "d1" and changed[0][1]["result"] == "finished"
