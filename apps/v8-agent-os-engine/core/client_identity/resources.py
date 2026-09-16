"""Short-lived read capabilities for native image/video consumers without headers."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, unquote

from .owner import IdentityError, session_identifier
from .service import decode, encode


def _resource_path(path: str) -> str:
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in decoded or any(part in (".", "..") for part in decoded.split("/")):
        raise IdentityError("resource_path_invalid")
    patterns = (
        r"/api/client/workspace/resource",
        r"/api/client/workspace/files/[^/]+(?:/.*)?",
        r"/api/client/(?:artifacts|sources)/[^/]+(?:/.*)?",
        r"/api/client/(?:artifacts|sources)(?:/.*)?",
        r"/api/client/(?:sessions|conversations)/[^/]+/(?:artifacts|sources|files)(?:/.*)?",
        r"/api/client/user-assets/(?:avatar|background)/[A-Za-z0-9._-]+",
        r"/user-assets/(?:avatar|background)/[A-Za-z0-9._-]+",
    )
    if not any(re.fullmatch(pattern, decoded) for pattern in patterns):
        raise IdentityError("resource_path_invalid")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in ("v8sig", "v8exp")]
    return parsed.path + ("?" + urlencode(query) if query else "")


def sign_resource_url(service, path: str, context, *, session_id: str = "", ttl_seconds: int = 600) -> str:
    """Caller first verifies ownership of the referenced session/resource."""
    normalized = _resource_path(path)
    service._ready()
    expiry = int(service.clock()) + max(1, min(600, ttl_seconds))
    claims = {"path": normalized, "subject": context.subject, "device": context.device_id,
              "session": session_id, "exp": expiry, "instance": service.instance()["instanceId"]}
    payload = encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signature = encode(hmac.new(service._key("signing").encode(), ("resource-v1:" + payload).encode(), hashlib.sha256).digest())
    return normalized + ("&" if "?" in normalized else "?") + urlencode({"v8exp": expiry, "v8sig": payload + "." + signature})


def verify_resource_request(service, path: str, *, method: str = "GET"):
    from core.auth_context import EngineAuthContext
    if method not in ("GET", "HEAD"):
        return None
    try:
        normalized = _resource_path(path)
        query = dict(parse_qsl(urlsplit(path).query))
        payload, signature = query["v8sig"].split(".")
        service._ready()
        expected = hmac.new(service._key("signing").encode(), ("resource-v1:" + payload).encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(signature)):
            return None
        claims = json.loads(decode(payload))
        now, expiry = service.clock(), claims["exp"]
        if claims["path"] != normalized or str(expiry) != query["v8exp"] or type(expiry) is not int or not now < expiry <= now + 600 or claims["instance"] != service.instance()["instanceId"]:
            return None
        owner = service.owners.owner()
        if owner["id"] != claims["subject"]:
            return None
        device_id = claims.get("device")
        if device_id:
            with service.database() as db:
                device = db.execute("SELECT * FROM client_devices WHERE id=?", (device_id,)).fetchone()
            if not device or device["user_id"] != owner["id"] or device["revoked_at"] is not None or device["expires_at"] <= now:
                return None
        return EngineAuthContext(owner["id"], session_identifier(owner), owner["login"], owner["role"], device_id,
            int(now), expiry, claims["instance"], "v8-resource", "resource_capability", device["surface"] if device_id else "local", "local_client" if not device_id or device["hidden"] else "human_phone")
    except (IdentityError, ValueError, TypeError, KeyError, UnicodeError):
        return None
