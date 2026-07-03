from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx

from core.database import DatabaseManager
from runtimes.network_supervisor import relay_runtime as relay_runtime_module
from runtimes.network_supervisor.models import NetworkEnvelope
from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig
from runtimes.network_supervisor.relay_runtime import NetworkRelayWorkerService
from runtimes.network_supervisor.relay_transport import HTTPRelayTransport, RelayAdapterDescriptor
from runtimes.network_supervisor.service import network_supervisor_service


def _identity() -> dict[str, str]:
    return {
        "peerId": "peer_local",
        "displayName": "Main Device",
        "publicKeyFingerprint": "local-fp",
        "localPeerTokenFingerprint": "token-fp",
    }


def test_relay_status_exposes_self_hostable_protocol_with_cloudflare_adapter():
    config = NetworkSupervisorRuntimeConfig.model_validate(
        {
            "enabled": True,
            "relay": {
                "enabled": True,
                "activeAdapterId": "cloudflare",
                "adapters": [
                    {
                        "id": "cloudflare",
                        "kind": "cloudflare",
                        "displayName": "Cloudflare Relay",
                        "baseUrl": "https://relay.example.com",
                        "cloudflareWorkerName": "v8-relay",
                        "cloudflareQueueName": "v8-relay-mailbox",
                        "cloudflareDurableObjectNamespace": "V8RelayRoom",
                    }
                ],
            },
        }
    )

    with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
        network_supervisor_service,
        "_local_identity",
        return_value=_identity(),
    ):
        status = network_supervisor_service.relay_status_payload()

    assert status["available"] is True
    assert status["protocol"]["version"] == "v8-relay.v1"
    assert status["protocol"]["selfHostable"] is True
    assert status["protocol"]["cloudflareAdapter"] == "optional"
    assert status["activeAdapter"]["kind"] == "cloudflare"
    assert status["activeAdapter"]["endpoints"]["mailbox"] == "https://relay.example.com/v1/relay/mailbox"
    assert status["activeAdapter"]["endpoints"]["websocket"] == "wss://relay.example.com/v1/relay/ws"


def test_relay_config_update_does_not_change_compat_api_branch():
    config = NetworkSupervisorRuntimeConfig()
    config.enabled = True
    config.openai_compat.enabled = False

    with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
        network_supervisor_service,
        "save_config_model",
        return_value=config,
    ), patch.object(network_supervisor_service, "_local_identity", return_value=_identity()):
        result = network_supervisor_service.save_relay_config(
            {
                "enabled": True,
                "activeAdapterId": "self-hosted",
                "adapters": [
                    {
                        "id": "self-hosted",
                        "kind": "self_hosted",
                        "displayName": "Local Relay",
                        "baseUrl": "https://relay.local.test",
                    }
                ],
            }
        )

    assert result["ok"] is True
    assert result["status"]["available"] is True
    assert config.relay.enabled is True
    assert config.openai_compat.enabled is False


def test_relay_config_patch_preserves_existing_adapter_details():
    config = NetworkSupervisorRuntimeConfig.model_validate(
        {
            "enabled": True,
            "relay": {
                "enabled": False,
                "activeAdapterId": "cloudflare",
                "adapters": [
                    {
                        "id": "cloudflare",
                        "kind": "cloudflare",
                        "displayName": "Cloudflare Relay",
                        "baseUrl": "https://old.example.com",
                        "cloudflareWorkerName": "v8-relay",
                    }
                ],
            },
        }
    )

    with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
        network_supervisor_service,
        "save_config_model",
        return_value=config,
    ), patch.object(network_supervisor_service, "_local_identity", return_value=_identity()):
        result = network_supervisor_service.save_relay_config({"enabled": True})

    assert result["relay"]["enabled"] is True
    assert result["relay"]["activeAdapterId"] == "cloudflare"
    assert result["relay"]["adapters"][0]["baseUrl"] == "https://old.example.com"
    assert result["relay"]["adapters"][0]["cloudflareWorkerName"] == "v8-relay"


def test_http_relay_transport_uses_protocol_endpoints():
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/.well-known/v8-relay":
            return httpx.Response(200, json={"ok": True, "protocolVersion": "v8-relay.v1"})
        if request.url.path == "/v1/relay/publish":
            return httpx.Response(200, json={"ok": True, "relayMessageId": "rmsg_1", "state": "queued", "cursor": "1"})
        if request.url.path == "/v1/relay/mailbox/peer_local":
            return httpx.Response(200, json={"ok": True, "items": [], "nextCursor": "1"})
        if request.url.path == "/v1/relay/ack":
            return httpx.Response(200, json={"ok": True, "acked": ["rmsg_1"]})
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HTTPRelayTransport(RelayAdapterDescriptor(id="self-hosted", kind="self_hosted", base_url="https://relay.example.com"), client=client)
    envelope = NetworkEnvelope.model_validate(
        {
            "version": "1",
            "messageId": "msg_1",
            "messageType": "neighbor.message",
            "sentAt": "2026-07-02T00:00:00Z",
            "expiresAt": "2026-07-02T00:05:00Z",
            "fromPeerId": "peer_local",
            "toPeerId": "peer_remote",
            "nonce": "nonce_1",
            "signature": "sig",
            "trace": {},
            "payload": {"body": "hello"},
        }
    )

    async def run():
        assert (await transport.health())["ok"] is True
        assert (await transport.publish(envelope)).relay_message_id == "rmsg_1"
        assert (await transport.pull("peer_local", cursor="0")).ok is True
        assert (await transport.ack("peer_local", ["rmsg_1"])).acked == ["rmsg_1"]
        await client.aclose()

    asyncio.run(run())

    assert ("GET", "/.well-known/v8-relay") in requests
    assert ("POST", "/v1/relay/publish") in requests
    assert ("GET", "/v1/relay/mailbox/peer_local") in requests
    assert ("POST", "/v1/relay/ack") in requests


def test_relay_worker_publishes_durable_outbox(monkeypatch, tmp_path):
    temp_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(relay_runtime_module, "db", temp_db)
    worker = NetworkRelayWorkerService()

    envelope = NetworkEnvelope.model_validate(
        {
            "version": "1",
            "messageId": "msg_out",
            "messageType": "neighbor.message",
            "sentAt": "2026-07-02T00:00:00Z",
            "expiresAt": "2026-07-02T00:05:00Z",
            "fromPeerId": "peer_local",
            "toPeerId": "peer_remote",
            "nonce": "nonce_out",
            "signature": "sig",
            "trace": {},
            "payload": {"body": "hello"},
        }
    )
    temp_db.add_network_relay_outbox_item(outbox_id="nrout_1", target_peer_id="peer_remote", envelope=envelope.model_dump(by_alias=True))

    class FakeTransport:
        async def publish(self, envelope, **_kwargs):
            from runtimes.network_supervisor.relay_transport import RelayPublishResult

            return RelayPublishResult(ok=True, relay_message_id="rmsg_1", state="queued", cursor="1")

    monkeypatch.setattr(worker, "_transport", lambda: FakeTransport())

    assert asyncio.run(worker.process_outbox_once()) is True
    completed = temp_db.list_network_relay_outbox(states=["published"])
    assert len(completed) == 1
    assert completed[0]["relayMessageId"] == "rmsg_1"


def test_relay_worker_pulls_inbox_and_acks(monkeypatch, tmp_path):
    temp_db = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(relay_runtime_module, "db", temp_db)
    worker = NetworkRelayWorkerService()
    handled: list[NetworkEnvelope] = []
    acked: list[str] = []
    envelope = {
        "version": "1",
        "messageId": "msg_in",
        "messageType": "neighbor.message",
        "sentAt": "2026-07-02T00:00:00Z",
        "expiresAt": "2026-07-02T00:05:00Z",
        "fromPeerId": "peer_remote",
        "toPeerId": "peer_local",
        "nonce": "nonce_in",
        "signature": "sig",
        "trace": {},
        "payload": {"body": "hello"},
    }

    monkeypatch.setattr(
        relay_runtime_module.network_supervisor_service,
        "relay_status_payload",
        lambda: {"available": True, "localNode": {"peerId": "peer_local"}, "protocol": {"version": "v8-relay.v1"}, "activeAdapter": {"id": "self-hosted", "kind": "self_hosted", "baseUrl": "https://relay.example.com"}},
    )

    class FakeTransport:
        async def pull(self, peer_id, cursor=None, limit=50):
            from runtimes.network_supervisor.relay_transport import RelayPullResult

            return RelayPullResult(ok=True, items=[{"relayMessageId": "rmsg_in", "cursor": "1", "envelope": envelope}], next_cursor="1")

        async def ack(self, peer_id, message_ids):
            from runtimes.network_supervisor.relay_transport import RelayAckResult

            acked.extend(message_ids)
            return RelayAckResult(ok=True, acked=message_ids)

    async def fake_handle(envelope_obj):
        handled.append(envelope_obj)

    import runtimes.network_supervisor.neighbor as neighbor_module

    monkeypatch.setattr(worker, "_transport", lambda: FakeTransport())
    monkeypatch.setattr(neighbor_module.network_neighbor_service, "handle_peer_message", fake_handle)

    assert asyncio.run(worker.process_inbox_once()) is True
    assert handled and handled[0].message_id == "msg_in"
    assert acked == ["rmsg_in"]
    assert temp_db.get_network_relay_cursor("peer_local") == "1"


def test_cloudflare_worker_template_declares_required_protocol_endpoints():
    template = Path("apps/v8-agent-os-engine/runtimes/network_supervisor/relay_templates/cloudflare_worker.mjs").read_text(encoding="utf-8")

    for token in [
        "/.well-known/v8-relay",
        "/v1/relay/publish",
        "/v1/relay/mailbox/",
        "/v1/relay/ack",
        "/v1/relay/ws",
        "class V8RelayRoom",
        "dead_letter",
    ]:
        assert token in template
