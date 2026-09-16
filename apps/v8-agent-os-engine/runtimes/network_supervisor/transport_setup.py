"""Connection suggestions and bounded HTTP diagnostics; no peer trust mutation."""
from __future__ import annotations

import ipaddress
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException

from core.v8_link import strip_api_suffix


def parse_connection_invitation(value: str | dict, *, local_peer_id: str) -> dict:
    try:
        if len(json.dumps(value).encode("utf-8")) > 8192:
            raise ValueError()
        item = json.loads(value) if isinstance(value, str) else dict(value)
        if not isinstance(item, dict):
            raise ValueError()
        fields = {"kind", "peerId", "displayName", "baseUrl", "publicKey", "code", "expiresAt"}
        if set(item) - fields or item.get("kind") != "v8-peer-invitation.v1":
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(item.get("peerId") or "")) or item["peerId"] == local_peer_id:
            raise ValueError()
        if len(str(item.get("displayName") or "")) > 100:
            raise ValueError()
        if len(base64.b64decode(item["publicKey"], validate=True)) != 32:
            raise ValueError()
        if not re.fullmatch(r"[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}", str(item.get("code") or "")):
            raise ValueError()
        expiry = datetime.fromisoformat(str(item["expiresAt"]).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None or not now < expiry <= now + timedelta(minutes=15):
            raise ValueError()
        item["baseUrl"] = normalize_peer_origin(item["baseUrl"])
        return item
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="peer_invitation_invalid_or_expired") from exc


def normalize_peer_origin(value: str) -> str:
    raw = strip_api_suffix(value)
    try:
        url = urlsplit(raw)
        port = url.port
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
            raise ValueError()
        host = url.hostname.lower()
        if host == "localhost":
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local):
            raise ValueError()
        if host.endswith(".trycloudflare.com") or host == "trycloudflare.com":
            raise ValueError()
        # Public ingress must use TLS; private LAN/Tailscale may use HTTP + signed envelopes.
        private_address = address is not None and (address.is_private or
            (address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10")))
        if url.scheme == "http" and not private_address:
            if not host.endswith((".ts.net", ".local")):
                raise ValueError()
        authority = f"[{host}]" if ":" in host else host
        if port:
            authority += f":{port}"
        return urlunsplit((url.scheme, authority, "", "", ""))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="peer_origin_invalid_or_not_shareable") from exc


def advertised_endpoint(node: dict) -> dict:
    """Return only an explicitly configured peer ingress.

    The historical fallback derived LAN addresses from Admin :9528.  Admin is
    now an optional configuration surface, so that address is not a peer
    transport and must never be advertised as one.
    """
    configured = str(node.get("peerBaseUrl") or node.get("advertisedBaseUrl") or "")
    try:
        origin = normalize_peer_origin(configured)
    except HTTPException:
        return {"advertisedBaseUrl": "", "advertisedWsUrl": "", "peerBaseUrl": ""}
    ws_url = str(node.get("advertisedWsUrl") or "")
    try:
        ws_host = urlsplit(ws_url).hostname or ""
        if not ws_host or ws_host == "localhost" or ipaddress.ip_address(ws_host).is_loopback:
            ws_url = ""
    except ValueError:
        pass
    return {"advertisedBaseUrl": origin, "advertisedWsUrl": ws_url,
            "peerBaseUrl": str(node.get("peerBaseUrl") or "")}


def connection_setup(service) -> dict:
    from core.storage import storage
    from core.v8_link import normalize_remote_link_config
    system = storage.get_system_base_config()
    bridge = dict(system.get("bridge") or {})
    remote = normalize_remote_link_config(
        dict(system.get("remoteLink") or {}),
        admin_base_url=str(bridge.get("adminBaseUrl") or ""),
        engine_base_url=str(bridge.get("engineBaseUrl") or ""),
    )
    candidates = []
    # Peer ingress must be explicit.  Do not synthesize LAN :9528/:9530
    # addresses because those listeners are loopback or Admin-only by default.
    for profile in remote.get("transportProfiles") or []:
        explicit = str((profile or {}).get("peerBaseUrl") or "").strip()
        if not explicit:
            continue
        try:
            origin = normalize_peer_origin(explicit)
        except HTTPException:
            continue
        candidates.append({"kind": str((profile or {}).get("kind") or "manual_url"),
                           "profileId": str((profile or {}).get("id") or ""), "origin": origin})
    active_id = str(remote.get("activeProfileId") or "")
    candidates.sort(key=lambda item: item.get("profileId") != active_id)
    config = service.get_config_model()
    current = config.node.peer_base_url or config.node.advertised_base_url
    try:
        normalize_peer_origin(current)
        warning = ""
    except HTTPException:
        warning = "advertised_address_not_shareable"
    return {"currentOrigin": current, "suggestions": candidates, "warning": warning,
            "discoveryError": service._discovery_error, "httpPeerProxy": True, "adminWebSocket": False,
            "gatewayPort": int((remote.get("phoneGateway") or {}).get("port") or 9532),
            "ingressTarget": "engine_gateway", "requiresExplicitForwarding": True,
            "remoteReachabilityVerified": False}


async def probe_peer_origin(origin: str) -> dict:
    target = normalize_peer_origin(origin)
    # A deliberately incomplete envelope proves route reachability without authenticating,
    # enrolling, waking or running any peer. Never label it an authenticated peer handshake.
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"{target}/v1/network-supervisor/peer/challenge", json={})
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        missing_fields = {tuple(item.get("loc") or []) for item in detail if isinstance(item, dict)} if isinstance(detail, list) else set()
        reached = response.status_code == 422 and {("body", "messageId"), ("body", "signature"), ("body", "fromPeerId")} <= missing_fields
        return {"origin": target, "routeReachable": reached, "httpStatus": response.status_code,
                "peerAuthenticated": False, "remoteReachabilityVerified": False}
    except (httpx.RequestError, ValueError):
        return {"origin": target, "routeReachable": False, "peerAuthenticated": False,
                "remoteReachabilityVerified": False, "reason": "peer_route_unreachable"}
