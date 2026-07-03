from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from api.models import ChatRequest
from core.database import db
from runtimes.chat.runtime import chat_runtime
from runtimes.network_supervisor.models import (
    NetworkEnvelope,
    NetworkPeerMutationPayload,
    NetworkTraceContext,
)
from runtimes.network_supervisor.neighbor_workspace import resolve_network_neighbor_workspace_binding
from runtimes.network_supervisor.service import network_supervisor_service


PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_CODE_TTL_SECONDS = 300
PAIRING_MAX_ATTEMPTS = 5
MESSAGE_PREVIEW_CHARS = 800
MESSAGE_BODY_MAX_CHARS = 65536


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fingerprint(raw: str) -> str:
    return hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest()[:16]


def _hash_pairing_code(invite_id: str, code: str) -> str:
    return hashlib.sha256(f"{invite_id}:{str(code or '').strip().upper()}".encode("utf-8")).hexdigest()


def _generate_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(8))


def _role_pair(local_role: str | None) -> tuple[str, str]:
    normalized = str(local_role or "").strip().lower()
    if normalized == "companion":
        return "companion", "primary"
    return "primary", "companion"


def _clean_nickname(value: str | None, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized[:48] if normalized else fallback


def _link_id(local_peer_id: str, peer_id: str) -> str:
    return f"nlink_{hashlib.sha1(f'{local_peer_id}:{peer_id}'.encode('utf-8')).hexdigest()[:16]}"


def _message_text(value: str | None) -> tuple[str, str, bool]:
    raw = str(value or "")
    truncated = len(raw) > MESSAGE_BODY_MAX_CHARS
    body = raw[:MESSAGE_BODY_MAX_CHARS]
    preview = body[:MESSAGE_PREVIEW_CHARS]
    if len(body) > MESSAGE_PREVIEW_CHARS:
        preview += "…"
    return body, preview, truncated


class NetworkNeighborService:
    def __init__(self) -> None:
        self._wake_queue_task: asyncio.Task | None = None
        self._wake_queue_enabled = False
        self._wake_queue_worker_id = f"network_neighbor_{uuid.uuid4().hex[:10]}"

    async def start(self) -> None:
        self._wake_queue_enabled = True
        if self._wake_queue_task is None or self._wake_queue_task.done():
            self._wake_queue_task = asyncio.create_task(self._wake_queue_loop())

    async def stop(self) -> None:
        self._wake_queue_enabled = False
        task = self._wake_queue_task
        self._wake_queue_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _kick_wake_queue_processing(self) -> None:
        if self._wake_queue_task is not None and not self._wake_queue_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._wake_queue_task = asyncio.create_task(self._wake_queue_loop(run_once=True))

    async def _wake_queue_loop(self, *, run_once: bool = False) -> None:
        while self._wake_queue_enabled or run_once:
            try:
                processed = await self.process_wake_queue_once(worker_id=self._wake_queue_worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                processed = False
            if run_once:
                return
            await asyncio.sleep(0.2 if processed else 2.0)

    async def process_wake_queue_once(self, *, worker_id: str | None = None) -> bool:
        item = db.claim_next_network_neighbor_wake_item(
            worker_id=worker_id or self._wake_queue_worker_id,
            lease_seconds=180,
        )
        if not item:
            return False
        queue_id = str(item.get("queueId") or item.get("id") or "").strip()
        payload = dict(item.get("payload") or {})
        try:
            link = db.get_network_neighbor_link(str(item.get("linkId") or "")) or dict(payload.get("link") or {})
            inbound_message = dict(payload.get("inboundMessage") or {})
            workspace_binding = dict(payload.get("workspaceBinding") or {})
            if not link or not inbound_message:
                raise RuntimeError("Wake queue item is missing link or inbound message payload")
            await self._execute_neighbor_supervisor_message(
                link=link,
                inbound_message=inbound_message,
                workspace_binding=workspace_binding,
                run_id=str(item.get("runId") or item.get("run_id") or ""),
            )
            db.complete_network_neighbor_wake_item(queue_id)
        except Exception as exc:
            db.fail_network_neighbor_wake_item(queue_id, error=str(exc), retry_delay_seconds=30)
        return True

    def _state(self) -> dict[str, Any]:
        return network_supervisor_service.read_state()

    def _write_state(self, state: dict[str, Any]) -> None:
        network_supervisor_service.write_state(state)

    def _local_identity(self) -> dict[str, Any]:
        return network_supervisor_service.ensure_local_identity()

    def _local_peer_token(self) -> str:
        return str(network_supervisor_service.read_secrets().get("localPeerToken") or "").strip()

    def _peer_view(self, peer_id: str) -> dict[str, Any] | None:
        for item in network_supervisor_service.list_peers():
            if str(item.get("peerId") or "").strip() == str(peer_id or "").strip():
                return dict(item)
        return None

    def _trust_peer(
        self,
        *,
        peer_id: str,
        display_name: str,
        base_url: str,
        ws_url: str | None,
        transport_profile_id: str | None,
        peer_base_url: str | None,
        public_key: str,
        peer_token: str,
    ) -> None:
        network_supervisor_service.upsert_peer(
            NetworkPeerMutationPayload.model_validate(
                {
                    "peerId": peer_id,
                    "displayName": display_name or peer_id,
                    "baseUrl": base_url or "",
                    "wsUrl": ws_url or "",
                    "transportProfileId": transport_profile_id or None,
                    "peerBaseUrl": peer_base_url or None,
                    "publicKey": public_key,
                    "allowedScopes": [],
                    "allowedWorkspaces": [],
                    "peerToken": peer_token,
                }
            )
        )

    def _upsert_link(
        self,
        *,
        peer_id: str,
        local_nickname: str,
        remote_nickname: str,
        local_role: str,
        remote_role: str,
        workspace_binding: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        local_peer_id = str(self._local_identity().get("peerId") or "").strip()
        return db.upsert_network_neighbor_link(
            link_id=_link_id(local_peer_id, peer_id),
            peer_id=peer_id,
            local_nickname=local_nickname,
            remote_nickname=remote_nickname,
            local_role=local_role,
            remote_role=remote_role,
            workspace_binding=workspace_binding or {},
            metadata=metadata or {},
            last_seen_at=_utc_iso(),
        )

    def _link_for_peer_or_404(self, peer_id: str) -> dict[str, Any]:
        link = db.get_network_neighbor_link_by_peer(peer_id)
        if not link:
            raise HTTPException(status_code=404, detail=f"Neighbor link not found for peer: {peer_id}")
        return link

    def _link_or_404(self, link_id: str) -> dict[str, Any]:
        link = db.get_network_neighbor_link(link_id)
        if not link:
            raise HTTPException(status_code=404, detail=f"Neighbor link not found: {link_id}")
        return link

    def _neighbor_session_id(self, link_id: str) -> str:
        return f"network_neighbor_{str(link_id or '').strip()}"

    def _ensure_neighbor_session(self, link: dict[str, Any], workspace_binding: dict[str, Any]) -> str:
        session_id = self._neighbor_session_id(str(link.get("linkId") or link.get("id") or ""))
        title = f"邻居对话 · {link.get('remoteNickname') or link.get('peerId') or '设备'}"
        db.create_or_update_session(
            session_id=session_id,
            title=title,
            user_id="network-neighbor",
            metadata={
                "channel_type": "network_neighbor",
                "source": "network_neighbor",
                "peerId": link.get("peerId"),
                "linkId": link.get("linkId") or link.get("id"),
                "workspaceBinding": workspace_binding,
            },
        )
        return session_id

    def _prune_pairing_invites(self, state: dict[str, Any]) -> dict[str, Any]:
        invites = {
            str(key): dict(value)
            for key, value in dict(state.get("neighborPairingInvites") or {}).items()
            if isinstance(value, dict)
        }
        now = _utc_now()
        kept: dict[str, Any] = {}
        for invite_id, invite in invites.items():
            try:
                expires_at = _parse_utc(str(invite.get("expiresAt") or ""))
            except Exception:
                continue
            if expires_at >= now and not str(invite.get("consumedAt") or "").strip():
                kept[invite_id] = invite
        state["neighborPairingInvites"] = kept
        return kept

    def status_payload(self) -> dict[str, Any]:
        runtime_status = network_supervisor_service.status_payload()
        links = db.list_network_neighbor_links()
        candidates = self.list_candidates()
        wake_items = db.list_network_neighbor_wake_queue(states=["queued", "retry", "leased", "failed"], limit=50)
        return {
            "ok": True,
            "enabled": bool(runtime_status.get("enabled")),
            "started": bool(runtime_status.get("started")),
            "node": {
                "peerId": runtime_status.get("node", {}).get("peerId"),
                "displayName": runtime_status.get("node", {}).get("displayName"),
            },
            "discovery": {
                "lanEnabled": bool(runtime_status.get("discovery", {}).get("lanEnabled")),
                "candidateCount": len(candidates.get("items") or []),
                "connectedCount": len(links),
                "lastAnnounceAt": runtime_status.get("discovery", {}).get("lastAnnounceAt"),
            },
            "wakeQueue": {
                "queued": len([item for item in wake_items if item.get("state") == "queued"]),
                "retry": len([item for item in wake_items if item.get("state") == "retry"]),
                "leased": len([item for item in wake_items if item.get("state") == "leased"]),
                "failed": len([item for item in wake_items if item.get("state") == "failed"]),
                "workerRunning": bool(self._wake_queue_task is not None and not self._wake_queue_task.done()),
            },
            "links": links,
        }

    async def set_switch(self, *, enabled: bool, display_name: str | None = None, reload_service: bool = True) -> dict[str, Any]:
        config = network_supervisor_service.get_config_model()
        config.enabled = bool(enabled)
        config.discovery.lan_enabled = bool(enabled)
        if display_name and str(display_name).strip():
            config.node.display_name = str(display_name).strip()[:48]
        network_supervisor_service.save_config_model(config)
        if reload_service:
            if enabled:
                await network_supervisor_service.reload()
                await self.start()
            else:
                await self.stop()
                await network_supervisor_service.stop()
        return self.status_payload()

    def list_candidates(self) -> dict[str, Any]:
        peers = network_supervisor_service.list_peers_payload()
        trusted_ids = {str(item.get("peerId") or "").strip() for item in list(peers.get("trustedItems") or [])}
        items: list[dict[str, Any]] = []
        for item in list(peers.get("discoveredItems") or []):
            peer_id = str(item.get("peerId") or "").strip()
            if not peer_id or peer_id in trusted_ids:
                continue
            items.append(
                {
                    "peerId": peer_id,
                    "displayName": item.get("displayName") or peer_id,
                    "online": bool(item.get("online")),
                    "lastSeenAt": item.get("lastSeenAt"),
                    "source": item.get("source") or "lan",
                    "baseUrl": item.get("baseUrl") or item.get("resolvedBaseUrl") or "",
                    "address": item.get("address") or "",
                }
            )
        return {"ok": True, "items": items, "meshCandidates": list(peers.get("meshCandidates") or [])}

    def create_pairing_invitation(
        self,
        *,
        ttl_seconds: int = PAIRING_CODE_TTL_SECONDS,
        local_role: str | None = "primary",
        local_nickname: str | None = None,
    ) -> dict[str, Any]:
        identity = self._local_identity()
        local_role, remote_role = _role_pair(local_role)
        code = _generate_pairing_code()
        invite_id = f"npair_{uuid.uuid4().hex[:12]}"
        expires_at = _utc_now() + timedelta(seconds=max(60, min(int(ttl_seconds or PAIRING_CODE_TTL_SECONDS), 900)))
        state = self._state()
        invites = self._prune_pairing_invites(state)
        invites[invite_id] = {
            "inviteId": invite_id,
            "codeHash": _hash_pairing_code(invite_id, code),
            "expiresAt": _utc_iso(expires_at),
            "createdAt": _utc_iso(),
            "createdByPeerId": identity["peerId"],
            "localRole": local_role,
            "remoteRole": remote_role,
            "localNickname": _clean_nickname(local_nickname, str(identity.get("displayName") or "本机")),
            "attemptCount": 0,
            "maxUses": 1,
        }
        state["neighborPairingInvites"] = invites
        self._write_state(state)
        return {
            "ok": True,
            "inviteId": invite_id,
            "code": code,
            "expiresAt": _utc_iso(expires_at),
            "ttlSeconds": int((expires_at - _utc_now()).total_seconds()),
            "localRole": local_role,
            "remoteRole": remote_role,
        }

    def _consume_local_pairing_code(self, code: str) -> dict[str, Any]:
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            raise HTTPException(status_code=400, detail="Missing pairing code")
        state = self._state()
        invites = self._prune_pairing_invites(state)
        matched_id = ""
        matched_invite: dict[str, Any] | None = None
        for invite_id, invite in invites.items():
            if int(invite.get("attemptCount") or 0) >= PAIRING_MAX_ATTEMPTS:
                continue
            if secrets.compare_digest(str(invite.get("codeHash") or ""), _hash_pairing_code(invite_id, normalized_code)):
                matched_id = invite_id
                matched_invite = dict(invite)
                break
        if not matched_invite:
            for invite in invites.values():
                invite["attemptCount"] = int(invite.get("attemptCount") or 0) + 1
            state["neighborPairingInvites"] = invites
            self._write_state(state)
            raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
        matched_invite["consumedAt"] = _utc_iso()
        invites.pop(matched_id, None)
        state.setdefault("neighborPairingConsumed", {})[matched_id] = matched_invite
        state["neighborPairingInvites"] = invites
        self._write_state(state)
        return matched_invite

    async def consume_pairing_invitation(
        self,
        *,
        peer_id: str,
        code: str,
        local_nickname: str | None = None,
    ) -> dict[str, Any]:
        candidate = self._peer_view(peer_id)
        if not candidate:
            raise HTTPException(status_code=404, detail=f"Neighbor candidate not found: {peer_id}")
        public_key = str(candidate.get("publicKey") or "").strip()
        if not public_key:
            raise HTTPException(status_code=400, detail="Neighbor candidate is missing a public key")
        identity = self._local_identity()
        envelope = network_supervisor_service.build_envelope(
            message_type="neighbor.pairing.consume",
            to_peer_id=peer_id,
            payload={
                "code": str(code or "").strip().upper(),
                "peerId": identity["peerId"],
                "displayName": identity["displayName"],
                "baseUrl": identity["advertisedBaseUrl"],
                "wsUrl": identity["advertisedWsUrl"],
                "transportProfileId": identity.get("transportProfileId") or "",
                "peerBaseUrl": identity.get("peerBaseUrl") or "",
                "publicKey": identity["publicKey"],
                "publicKeyFingerprint": identity["publicKeyFingerprint"],
                "peerToken": self._local_peer_token(),
                "nickname": _clean_nickname(local_nickname, str(identity.get("displayName") or "本机")),
            },
            trace=NetworkTraceContext(delegation_id=f"pair_{uuid.uuid4().hex[:12]}"),
            expires_in_seconds=60,
        )
        response_payload = await network_supervisor_service._post_peer(peer_id, "peer/neighbors/pairing/consume", envelope)
        response_envelope = NetworkEnvelope.model_validate(response_payload)
        verified = network_supervisor_service.verify_envelope(
            response_envelope,
            allow_untrusted=True,
            provided_public_key=public_key,
        )
        payload = dict(response_envelope.payload or {})
        remote_peer_id = response_envelope.from_peer_id
        remote_display_name = str(payload.get("displayName") or candidate.get("displayName") or remote_peer_id).strip()
        local_role = str(payload.get("remoteRole") or "companion").strip() or "companion"
        remote_role = str(payload.get("localRole") or "primary").strip() or "primary"
        self._trust_peer(
            peer_id=remote_peer_id,
            display_name=remote_display_name,
            base_url=str(payload.get("baseUrl") or candidate.get("baseUrl") or "").strip(),
            ws_url=str(payload.get("wsUrl") or candidate.get("wsUrl") or "").strip(),
            transport_profile_id=str(payload.get("transportProfileId") or candidate.get("transportProfileId") or "").strip() or None,
            peer_base_url=str(payload.get("peerBaseUrl") or candidate.get("peerBaseUrl") or "").strip() or None,
            public_key=str(verified.get("publicKey") or public_key),
            peer_token=str(payload.get("peerToken") or "").strip(),
        )
        workspace_binding = resolve_network_neighbor_workspace_binding(
            peer_id=remote_peer_id,
            local_role=local_role,
            remote_project_id=str(payload.get("workspaceBinding", {}).get("projectId") or "").strip() or None
            if isinstance(payload.get("workspaceBinding"), dict)
            else None,
            remote_workspace_id=str(payload.get("workspaceBinding", {}).get("workspaceId") or "").strip() or None
            if isinstance(payload.get("workspaceBinding"), dict)
            else None,
            remote_workspace_path=str(payload.get("workspaceBinding", {}).get("workspacePath") or "").strip() or None
            if isinstance(payload.get("workspaceBinding"), dict)
            else None,
        )
        link = self._upsert_link(
            peer_id=remote_peer_id,
            local_nickname=_clean_nickname(local_nickname, str(identity.get("displayName") or "本机")),
            remote_nickname=_clean_nickname(str(payload.get("nickname") or remote_display_name), remote_display_name),
            local_role=local_role,
            remote_role=remote_role,
            workspace_binding=workspace_binding,
            metadata={"pairingMethod": "short_code", "pairedBy": "consumer"},
        )
        return {"ok": True, "link": link, "peer": {"peerId": remote_peer_id, "displayName": remote_display_name}}

    def handle_pairing_consume(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        verified = network_supervisor_service.verify_envelope(envelope, allow_untrusted=True)
        payload = dict(envelope.payload or {})
        invite = self._consume_local_pairing_code(str(payload.get("code") or ""))
        peer_id = envelope.from_peer_id
        peer_display_name = str(payload.get("displayName") or peer_id).strip()
        peer_public_key = str(verified.get("publicKey") or payload.get("publicKey") or "").strip()
        peer_token = str(payload.get("peerToken") or "").strip()
        if not peer_public_key or not peer_token:
            raise HTTPException(status_code=400, detail="Pairing request is missing identity material")
        self._trust_peer(
            peer_id=peer_id,
            display_name=peer_display_name,
            base_url=str(payload.get("baseUrl") or "").strip(),
            ws_url=str(payload.get("wsUrl") or "").strip(),
            transport_profile_id=str(payload.get("transportProfileId") or "").strip() or None,
            peer_base_url=str(payload.get("peerBaseUrl") or "").strip() or None,
            public_key=peer_public_key,
            peer_token=peer_token,
        )
        identity = self._local_identity()
        local_role = str(invite.get("localRole") or "primary")
        remote_role = str(invite.get("remoteRole") or "companion")
        workspace_binding = resolve_network_neighbor_workspace_binding(
            peer_id=peer_id,
            local_role=local_role,
        )
        self._upsert_link(
            peer_id=peer_id,
            local_nickname=_clean_nickname(str(invite.get("localNickname") or ""), str(identity.get("displayName") or "本机")),
            remote_nickname=_clean_nickname(str(payload.get("nickname") or peer_display_name), peer_display_name),
            local_role=local_role,
            remote_role=remote_role,
            workspace_binding=workspace_binding,
            metadata={"pairingMethod": "short_code", "pairedBy": "inviter", "inviteId": invite.get("inviteId")},
        )
        return network_supervisor_service.build_envelope(
            message_type="neighbor.pairing.accepted",
            to_peer_id=peer_id,
            payload={
                "peerId": identity["peerId"],
                "displayName": identity["displayName"],
                "nickname": _clean_nickname(str(invite.get("localNickname") or ""), str(identity.get("displayName") or "本机")),
                "baseUrl": identity["advertisedBaseUrl"],
                "wsUrl": identity["advertisedWsUrl"],
                "transportProfileId": identity.get("transportProfileId") or "",
                "peerBaseUrl": identity.get("peerBaseUrl") or "",
                "publicKey": identity["publicKey"],
                "publicKeyFingerprint": identity["publicKeyFingerprint"],
                "peerToken": self._local_peer_token(),
                "localRole": local_role,
                "remoteRole": remote_role,
                "workspaceBinding": workspace_binding,
            },
            trace=envelope.trace,
            expires_in_seconds=60,
        )

    def list_links(self) -> dict[str, Any]:
        peer_views = {str(item.get("peerId") or ""): dict(item) for item in network_supervisor_service.list_peers()}
        items: list[dict[str, Any]] = []
        for link in db.list_network_neighbor_links():
            peer_id = str(link.get("peerId") or "").strip()
            peer = peer_views.get(peer_id, {})
            items.append(
                {
                    **link,
                    "online": bool(peer.get("online")),
                    "lastSeenAt": peer.get("lastSeenAt") or link.get("lastSeenAt"),
                    "displayName": peer.get("displayName") or link.get("remoteNickname") or peer_id,
                }
            )
        return {"ok": True, "items": items}

    def update_link(self, link_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        link = self._link_or_404(link_id)
        next_local_role, next_remote_role = _role_pair(str(payload.get("localRole") or link.get("localRole") or "primary"))
        workspace_payload = payload.get("workspaceBinding") if isinstance(payload.get("workspaceBinding"), dict) else None
        if workspace_payload and next_local_role != "primary":
            raise HTTPException(status_code=403, detail="Only the primary device can change the shared workspace binding")
        workspace_binding = dict(link.get("workspaceBinding") or {})
        if workspace_payload:
            workspace_binding = resolve_network_neighbor_workspace_binding(
                peer_id=str(link.get("peerId") or ""),
                local_role=next_local_role,
                remote_project_id=str(workspace_payload.get("remoteProjectId") or workspace_payload.get("projectId") or "").strip() or None,
                remote_workspace_id=str(workspace_payload.get("remoteWorkspaceId") or workspace_payload.get("workspaceId") or "").strip() or None,
                remote_workspace_path=str(workspace_payload.get("remoteWorkspacePath") or workspace_payload.get("workspacePath") or "").strip() or None,
                configured_binding=workspace_payload,
            )
        updated = db.upsert_network_neighbor_link(
            link_id=str(link.get("linkId") or link_id),
            peer_id=str(link.get("peerId") or ""),
            local_nickname=_clean_nickname(payload.get("localNickname"), str(link.get("localNickname") or "本机")),
            remote_nickname=_clean_nickname(payload.get("remoteNickname"), str(link.get("remoteNickname") or link.get("peerId") or "邻居设备")),
            local_role=next_local_role,
            remote_role=next_remote_role,
            trust_status=str(link.get("trustStatus") or "trusted"),
            workspace_binding=workspace_binding,
            metadata=dict(link.get("metadata") or {}),
            last_seen_at=str(link.get("lastSeenAt") or "") or None,
        )
        return {"ok": True, "link": updated}

    def revoke_link(self, link_id: str) -> dict[str, Any]:
        link = self._link_or_404(link_id)
        network_supervisor_service.delete_peer(str(link.get("peerId") or ""))
        deleted = db.delete_network_neighbor_link(link_id)
        return {"ok": bool(deleted), "linkId": link_id, "peerId": link.get("peerId")}

    async def send_message(
        self,
        *,
        link_id: str,
        body: str,
        wake_supervisor: bool = False,
        workspace_binding: dict[str, Any] | None = None,
        source: str = "user",
    ) -> dict[str, Any]:
        link = self._link_or_404(link_id)
        identity = self._local_identity()
        binding = resolve_network_neighbor_workspace_binding(
            peer_id=str(link.get("peerId") or ""),
            local_role=str(link.get("localRole") or ""),
            configured_binding=workspace_binding or dict(link.get("workspaceBinding") or {}),
        )
        body_text, preview, truncated = _message_text(body)
        message_id = f"nmsg_{uuid.uuid4().hex}"
        local_message = db.add_network_neighbor_message(
            message_id=message_id,
            link_id=str(link.get("linkId") or link_id),
            direction="outbound",
            from_peer_id=str(identity.get("peerId") or ""),
            from_nickname=str(link.get("localNickname") or identity.get("displayName") or "本机"),
            role=str(link.get("localRole") or "primary"),
            body=body_text,
            preview=preview,
            status="sent",
            workspace_binding=binding,
            metadata={"source": source, "bodyTruncated": truncated, "wakeSupervisor": bool(wake_supervisor)},
        )
        envelope = network_supervisor_service.build_envelope(
            message_type="neighbor.message",
            to_peer_id=str(link.get("peerId") or ""),
            payload={
                "messageId": message_id,
                "body": body_text,
                "preview": preview,
                "bodyTruncated": truncated,
                "wakeSupervisor": bool(wake_supervisor),
                "workspaceBinding": binding,
                "fromNickname": str(link.get("localNickname") or identity.get("displayName") or "本机"),
                "role": str(link.get("localRole") or "primary"),
                "source": source,
            },
            trace=NetworkTraceContext(delegation_id=f"neighbor_{uuid.uuid4().hex[:12]}"),
            expires_in_seconds=120,
        )
        try:
            from runtimes.network_supervisor.relay_runtime import network_relay_worker_service
        except Exception:
            network_relay_worker_service = None
        if network_relay_worker_service is not None and network_relay_worker_service.relay_available():
            queued = network_relay_worker_service.enqueue_outbox(
                target_peer_id=str(link.get("peerId") or ""),
                link_id=str(link.get("linkId") or link_id),
                local_message_id=message_id,
                envelope=envelope,
            )
            return {"ok": True, "message": local_message, "delivery": {"status": "queued_via_relay", "outboxId": queued.get("outboxId")}}
        try:
            ack = await network_supervisor_service._post_peer(str(link.get("peerId") or ""), "peer/neighbors/messages", envelope)
        except Exception as exc:
            if network_relay_worker_service is not None and network_relay_worker_service.relay_available():
                queued = network_relay_worker_service.enqueue_outbox(
                    target_peer_id=str(link.get("peerId") or ""),
                    link_id=str(link.get("linkId") or link_id),
                    local_message_id=message_id,
                    envelope=envelope,
                )
                return {"ok": True, "message": local_message, "delivery": {"status": "queued_via_relay", "outboxId": queued.get("outboxId"), "directError": str(exc)}}
            raise
        return {"ok": True, "message": local_message, "delivery": ack}

    async def handle_peer_message(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        network_supervisor_service.verify_envelope(envelope)
        link = self._link_for_peer_or_404(envelope.from_peer_id)
        payload = dict(envelope.payload or {})
        binding_payload = payload.get("workspaceBinding") if isinstance(payload.get("workspaceBinding"), dict) else {}
        workspace_binding = resolve_network_neighbor_workspace_binding(
            peer_id=envelope.from_peer_id,
            local_role=str(link.get("localRole") or ""),
            remote_project_id=str(binding_payload.get("projectId") or binding_payload.get("remoteProjectId") or "").strip() or None,
            remote_workspace_id=str(binding_payload.get("workspaceId") or binding_payload.get("remoteWorkspaceId") or "").strip() or None,
            remote_workspace_path=str(binding_payload.get("workspacePath") or binding_payload.get("remoteWorkspacePath") or "").strip() or None,
            configured_binding=dict(link.get("workspaceBinding") or {}),
        )
        body_text, preview, truncated = _message_text(str(payload.get("body") or ""))
        stored = db.add_network_neighbor_message(
            message_id=f"nmsg_{uuid.uuid4().hex}",
            link_id=str(link.get("linkId") or ""),
            direction="inbound",
            from_peer_id=envelope.from_peer_id,
            from_nickname=_clean_nickname(str(payload.get("fromNickname") or ""), str(link.get("remoteNickname") or envelope.from_peer_id)),
            role=str(payload.get("role") or link.get("remoteRole") or "companion"),
            body=body_text,
            preview=preview,
            status="received",
            workspace_binding=workspace_binding,
            metadata={
                "remoteMessageId": str(payload.get("messageId") or ""),
                "bodyTruncated": bool(payload.get("bodyTruncated")) or truncated,
                "wakeSupervisor": bool(payload.get("wakeSupervisor")),
                "source": str(payload.get("source") or "peer"),
            },
        )
        run_id = None
        queue_item = None
        if bool(payload.get("wakeSupervisor")):
            run_id = f"run_{uuid.uuid4().hex}"
            queue_item = db.add_network_neighbor_wake_queue_item(
                queue_id=f"nwake_{uuid.uuid4().hex}",
                link_id=str(link.get("linkId") or ""),
                message_id=str(stored.get("messageId") or stored.get("id") or ""),
                run_id=run_id,
                payload={
                    "link": link,
                    "inboundMessage": stored,
                    "workspaceBinding": workspace_binding,
                    "sourcePeerId": envelope.from_peer_id,
                },
            )
            self._kick_wake_queue_processing()
        return network_supervisor_service.build_envelope(
            message_type="neighbor.message.ack",
            to_peer_id=envelope.from_peer_id,
            payload={
                "messageId": str(payload.get("messageId") or ""),
                "receivedMessageId": stored.get("messageId"),
                "status": "received",
                "runScheduled": bool(run_id),
                "runId": run_id,
                "queueId": queue_item.get("queueId") if isinstance(queue_item, dict) else None,
            },
            trace=envelope.trace,
            expires_in_seconds=60,
        )

    async def _execute_neighbor_supervisor_message(
        self,
        *,
        link: dict[str, Any],
        inbound_message: dict[str, Any],
        workspace_binding: dict[str, Any],
        run_id: str,
    ) -> None:
        session_id = self._ensure_neighbor_session(link, workspace_binding)
        request = ChatRequest.model_validate(
            {
                "messages": [{"role": "user", "content": str(inbound_message.get("body") or "").strip()}],
                "stream": True,
                "sessionId": session_id,
                "conversationId": session_id,
                "userId": f"network-peer:{link.get('peerId')}",
                "projectId": workspace_binding.get("projectId"),
                "workspaceId": workspace_binding.get("workspaceId"),
                "workspacePath": workspace_binding.get("workspacePath"),
                "scopeHint": "network_neighbor",
            }
        )
        aggregated = ""
        try:
            async for event in chat_runtime.stream_legacy_events(request, transport="network_neighbor", run_id=run_id):
                event_type = str(event.get("type") or "").strip()
                if event_type == "text_chunk":
                    aggregated += str(event.get("content") or "")
                elif event_type == "done":
                    break
                elif event_type == "error":
                    aggregated = f"邻居消息处理失败：{event.get('error') or 'unknown error'}"
                    break
        except Exception as exc:
            aggregated = f"邻居消息处理失败：{exc}"
        if not aggregated.strip():
            return
        try:
            await self.send_message(
                link_id=str(link.get("linkId") or link.get("id") or ""),
                body=aggregated,
                wake_supervisor=False,
                workspace_binding=workspace_binding,
                source="supervisor",
            )
        except Exception:
            body_text, preview, truncated = _message_text(aggregated)
            identity = self._local_identity()
            db.add_network_neighbor_message(
                message_id=f"nmsg_{uuid.uuid4().hex}",
                link_id=str(link.get("linkId") or link.get("id") or ""),
                direction="outbound",
                from_peer_id=str(identity.get("peerId") or ""),
                from_nickname=str(link.get("localNickname") or identity.get("displayName") or "本机"),
                role=str(link.get("localRole") or "primary"),
                body=body_text,
                preview=preview,
                status="local_only",
                run_id=run_id,
                workspace_binding=workspace_binding,
                metadata={"source": "supervisor", "delivery": "failed", "bodyTruncated": truncated},
            )

    def timeline(self, link_id: str, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        self._link_or_404(link_id)
        after_seq = int(cursor) if str(cursor or "").strip().isdigit() else None
        items = db.list_network_neighbor_messages(link_id=link_id, after_seq=after_seq, limit=limit)
        next_cursor = str(items[-1].get("seq")) if items else (str(cursor) if cursor is not None else None)
        return {"ok": True, "items": items, "nextCursor": next_cursor}


network_neighbor_service = NetworkNeighborService()
