from __future__ import annotations

import asyncio
import uuid
from typing import Any

from core.database import db
from runtimes.network_supervisor.models import NetworkEnvelope
from runtimes.network_supervisor.relay_transport import RelayTransport, build_relay_transport
from runtimes.network_supervisor.service import network_supervisor_service


class NetworkRelayWorkerService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._ws_task: asyncio.Task | None = None
        self._enabled = False
        self._worker_id = f"network_relay_{uuid.uuid4().hex[:10]}"

    async def start(self) -> None:
        self._enabled = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        self._enabled = False
        task = self._task
        ws_task = self._ws_task
        self._task = None
        self._ws_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if ws_task and not ws_task.done():
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass

    def running(self) -> bool:
        return bool(self._task is not None and not self._task.done())

    def _relay_status(self) -> dict[str, Any]:
        try:
            return network_supervisor_service.relay_status_payload()
        except Exception:
            return {"available": False, "reasons": ["relay_status_unavailable"]}

    def relay_available(self) -> bool:
        return bool(self._relay_status().get("available"))

    def _transport(self) -> RelayTransport:
        status = self._relay_status()
        if not status.get("available"):
            raise RuntimeError(f"Relay is not available: {','.join(list(status.get('reasons') or []))}")
        protocol = dict(status.get("protocol") or {})
        adapter = dict(status.get("activeAdapter") or {})
        return build_relay_transport(
            adapter,
            protocol_version=str(protocol.get("version") or "v8-relay.v1"),
            default_ttl_seconds=int(protocol.get("defaultTtlSeconds") or 300),
        )

    def enqueue_outbox(
        self,
        *,
        target_peer_id: str,
        envelope: NetworkEnvelope,
        link_id: str | None = None,
        local_message_id: str | None = None,
    ) -> dict[str, Any]:
        item = db.add_network_relay_outbox_item(
            outbox_id=f"nrout_{uuid.uuid4().hex}",
            target_peer_id=target_peer_id,
            link_id=link_id,
            local_message_id=local_message_id,
            envelope=envelope.model_dump(by_alias=True),
        )
        self._kick()
        return item

    def _kick(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = asyncio.create_task(self._loop(run_once=True))

    async def _loop(self, *, run_once: bool = False) -> None:
        while self._enabled or run_once:
            try:
                processed = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                processed = False
            if run_once:
                return
            await asyncio.sleep(0.5 if processed else 5.0)

    async def _ws_loop(self) -> None:
        while self._enabled:
            try:
                if not self.relay_available():
                    await asyncio.sleep(10.0)
                    continue
                ws_info = await self.connect_ws()
                url = str(ws_info.get("url") or "").strip()
                if not url:
                    await asyncio.sleep(10.0)
                    continue
                import websockets

                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
                    async for _message in websocket:
                        await self.process_inbox_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(10.0)

    async def process_once(self) -> bool:
        processed_outbox = await self.process_outbox_once()
        processed_inbox = await self.process_inbox_once()
        return bool(processed_outbox or processed_inbox)

    async def process_outbox_once(self) -> bool:
        item = db.claim_next_network_relay_outbox_item(worker_id=self._worker_id, lease_seconds=180)
        if not item:
            return False
        outbox_id = str(item.get("outboxId") or item.get("id") or "")
        try:
            transport = self._transport()
            envelope = NetworkEnvelope.model_validate(item.get("envelope") or {})
            result = await transport.publish(
                envelope,
                idempotency_key=str(item.get("localMessageId") or envelope.message_id),
            )
            if not result.ok:
                raise RuntimeError(f"Relay publish returned not-ok state={result.state}")
            db.complete_network_relay_outbox_item(outbox_id, relay_message_id=result.relay_message_id)
        except Exception as exc:
            db.fail_network_relay_outbox_item(outbox_id, error=str(exc), retry_delay_seconds=30)
        return True

    async def process_inbox_once(self) -> bool:
        status = self._relay_status()
        if not status.get("available"):
            return False
        local_peer_id = str((status.get("localNode") or {}).get("peerId") or "").strip()
        if not local_peer_id:
            return False
        cursor = db.get_network_relay_cursor(local_peer_id)
        transport = self._transport()
        result = await transport.pull(local_peer_id, cursor=cursor, limit=50)
        if not result.ok or not result.items:
            return False
        processed = False
        ack_ids: list[str] = []
        for item in result.items:
            relay_message_id = str(item.get("relayMessageId") or item.get("messageId") or "").strip()
            item_cursor = str(item.get("cursor") or relay_message_id or result.next_cursor or "").strip()
            envelope_payload = item.get("envelope") if isinstance(item.get("envelope"), dict) else item.get("payload")
            try:
                if not isinstance(envelope_payload, dict):
                    raise RuntimeError("Relay mailbox item is missing envelope")
                envelope = NetworkEnvelope.model_validate(envelope_payload)
                from runtimes.network_supervisor.neighbor import network_neighbor_service

                await network_neighbor_service.handle_peer_message(envelope)
                if relay_message_id:
                    ack_ids.append(relay_message_id)
                    db.add_network_relay_delivery_ack(peer_id=local_peer_id, relay_message_id=relay_message_id, metadata={"cursor": item_cursor})
                if item_cursor:
                    db.upsert_network_relay_cursor(peer_id=local_peer_id, cursor=item_cursor)
                processed = True
            except Exception as exc:
                db.add_network_relay_dead_letter(
                    direction="inbound",
                    peer_id=local_peer_id,
                    relay_message_id=relay_message_id or None,
                    envelope=envelope_payload if isinstance(envelope_payload, dict) else {},
                    reason=str(exc),
                    metadata={"cursor": item_cursor},
                )
                if relay_message_id:
                    ack_ids.append(relay_message_id)
                if item_cursor:
                    db.upsert_network_relay_cursor(peer_id=local_peer_id, cursor=item_cursor)
                processed = True
        if ack_ids:
            try:
                await transport.ack(local_peer_id, ack_ids)
            except Exception as exc:
                db.add_network_relay_dead_letter(
                    direction="ack",
                    peer_id=local_peer_id,
                    reason=f"Relay ACK failed: {exc}",
                    metadata={"messageIds": ack_ids},
                )
        return processed

    async def connect_ws(self) -> dict[str, Any]:
        status = self._relay_status()
        local_peer_id = str((status.get("localNode") or {}).get("peerId") or "").strip()
        if not local_peer_id:
            return {"ok": False, "reason": "missing_local_peer_id"}
        return await self._transport().connect_ws(local_peer_id)


network_relay_worker_service = NetworkRelayWorkerService()
