from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from runtimes.network_supervisor.models import NetworkEnvelope


RELAY_PROTOCOL_VERSION = "v8-relay.v1"
RELAY_MESSAGE_STATES = {"queued", "delivered", "acked", "expired", "dead_letter"}


@dataclass
class RelayAdapterDescriptor:
    id: str
    kind: str
    base_url: str
    websocket_url: str = ""
    rendezvous_path: str = "/v1/relay/rendezvous"
    mailbox_path: str = "/v1/relay/mailbox"
    websocket_path: str = "/v1/relay/ws"
    protocol_version: str = RELAY_PROTOCOL_VERSION
    default_ttl_seconds: int = 300

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, protocol_version: str = RELAY_PROTOCOL_VERSION, default_ttl_seconds: int = 300) -> "RelayAdapterDescriptor":
        return cls(
            id=str(payload.get("id") or "").strip(),
            kind=str(payload.get("kind") or "self_hosted").strip(),
            base_url=str(payload.get("baseUrl") or payload.get("base_url") or "").strip().rstrip("/"),
            websocket_url=str(payload.get("websocketUrl") or payload.get("websocket_url") or "").strip(),
            rendezvous_path=str(payload.get("rendezvousPath") or payload.get("rendezvous_path") or "/v1/relay/rendezvous").strip(),
            mailbox_path=str(payload.get("mailboxPath") or payload.get("mailbox_path") or "/v1/relay/mailbox").strip(),
            websocket_path=str(payload.get("websocketPath") or payload.get("websocket_path") or "/v1/relay/ws").strip(),
            protocol_version=protocol_version,
            default_ttl_seconds=max(60, int(default_ttl_seconds or 300)),
        )


@dataclass
class RelayPublishResult:
    ok: bool
    relay_message_id: str = ""
    state: str = "queued"
    cursor: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelayPullResult:
    ok: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelayAckResult:
    ok: bool
    acked: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class RelayTransport(Protocol):
    async def publish(self, envelope: NetworkEnvelope, *, ttl_seconds: int | None = None, idempotency_key: str | None = None) -> RelayPublishResult:
        ...

    async def pull(self, peer_id: str, cursor: str | None = None, *, limit: int = 50) -> RelayPullResult:
        ...

    async def ack(self, peer_id: str, message_ids: list[str]) -> RelayAckResult:
        ...

    async def connect_ws(self, peer_id: str) -> dict[str, Any]:
        ...

    async def health(self) -> dict[str, Any]:
        ...


class HTTPRelayTransport:
    def __init__(self, descriptor: RelayAdapterDescriptor, *, client: httpx.AsyncClient | None = None) -> None:
        self.descriptor = descriptor
        self._client = client

    def _url(self, path: str) -> str:
        base = self.descriptor.base_url.rstrip("/")
        suffix = str(path or "").strip()
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        if not base:
            raise RuntimeError("Relay adapter baseUrl is not configured")
        return f"{base}{suffix}"

    def _ws_url(self, peer_id: str) -> str:
        if self.descriptor.websocket_url:
            base = self.descriptor.websocket_url.rstrip("/")
            separator = "&" if "?" in base else "?"
            return f"{base}{separator}{urlencode({'peerId': peer_id})}"
        base = self.descriptor.base_url.rstrip("/")
        if base.startswith("https://"):
            base = f"wss://{base.removeprefix('https://')}"
        elif base.startswith("http://"):
            base = f"ws://{base.removeprefix('http://')}"
        path = self.descriptor.websocket_path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}?{urlencode({'peerId': peer_id})}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0))
        close_after = self._client is None
        try:
            response = await client.request(method, self._url(path), **kwargs)
            payload = response.json() if response.content else {}
            if response.is_error:
                raise RuntimeError(str(payload.get("detail") or payload.get("error") or response.text))
            return dict(payload or {})
        finally:
            if close_after:
                await client.aclose()

    async def publish(self, envelope: NetworkEnvelope, *, ttl_seconds: int | None = None, idempotency_key: str | None = None) -> RelayPublishResult:
        body = {
            "protocolVersion": self.descriptor.protocol_version,
            "targetPeerId": envelope.to_peer_id,
            "ttlSeconds": max(60, int(ttl_seconds or self.descriptor.default_ttl_seconds)),
            "idempotencyKey": idempotency_key or envelope.message_id,
            "envelope": envelope.model_dump(by_alias=True),
        }
        payload = await self._request("POST", "/v1/relay/publish", json=body)
        return RelayPublishResult(
            ok=bool(payload.get("ok", True)),
            relay_message_id=str(payload.get("relayMessageId") or payload.get("messageId") or ""),
            state=str(payload.get("state") or "queued"),
            cursor=str(payload.get("cursor") or ""),
            raw=payload,
        )

    async def pull(self, peer_id: str, cursor: str | None = None, *, limit: int = 50) -> RelayPullResult:
        query = {"limit": str(max(1, min(int(limit or 50), 200)))}
        if cursor:
            query["cursor"] = str(cursor)
        payload = await self._request("GET", f"/v1/relay/mailbox/{peer_id}?{urlencode(query)}")
        items = [dict(item) for item in list(payload.get("items") or []) if isinstance(item, dict)]
        return RelayPullResult(ok=bool(payload.get("ok", True)), items=items, next_cursor=str(payload.get("nextCursor") or ""), raw=payload)

    async def ack(self, peer_id: str, message_ids: list[str]) -> RelayAckResult:
        payload = await self._request(
            "POST",
            "/v1/relay/ack",
            json={"protocolVersion": self.descriptor.protocol_version, "peerId": peer_id, "messageIds": [str(item) for item in message_ids if str(item).strip()]},
        )
        return RelayAckResult(ok=bool(payload.get("ok", True)), acked=[str(item) for item in list(payload.get("acked") or [])], raw=payload)

    async def connect_ws(self, peer_id: str) -> dict[str, Any]:
        # The Engine worker remains durable-pull first. This returns the canonical
        # WebSocket endpoint so a realtime loop can attach without changing the
        # transport protocol.
        return {
            "ok": True,
            "adapterId": self.descriptor.id,
            "kind": self.descriptor.kind,
            "url": self._ws_url(peer_id),
            "mode": "websocket_presence_and_push_hint",
        }

    async def health(self) -> dict[str, Any]:
        payload = await self._request("GET", "/.well-known/v8-relay")
        return {
            "ok": bool(payload.get("ok", True)),
            "adapterId": self.descriptor.id,
            "kind": self.descriptor.kind,
            "protocolVersion": str(payload.get("protocolVersion") or payload.get("protocol", {}).get("version") or ""),
            "raw": payload,
        }


class SelfHostedRelayTransport(HTTPRelayTransport):
    pass


class CloudflareRelayTransport(HTTPRelayTransport):
    pass


def build_relay_transport(adapter_payload: dict[str, Any], *, protocol_version: str = RELAY_PROTOCOL_VERSION, default_ttl_seconds: int = 300) -> RelayTransport:
    descriptor = RelayAdapterDescriptor.from_payload(
        adapter_payload,
        protocol_version=protocol_version,
        default_ttl_seconds=default_ttl_seconds,
    )
    if descriptor.kind == "cloudflare":
        return CloudflareRelayTransport(descriptor)
    return SelfHostedRelayTransport(descriptor)
