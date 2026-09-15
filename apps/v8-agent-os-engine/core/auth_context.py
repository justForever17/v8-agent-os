"""Engine canonical verification for the existing mobile access token format.

This is deliberately verification only: issuance, pairing and revocation remain
owned by the existing credential store until their migration contract is complete.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.v8_agent_os_paths import V8_AGENT_OS_HOME


@dataclass(frozen=True)
class EngineAuthContext:
    subject: str
    session_id: str
    login: str
    role: str
    device_id: str | None
    issued_at: int
    expires_at: int
    issuer: str | None = None
    audience: str | None = None


def _b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _read_secret() -> str:
    path = V8_AGENT_OS_HOME / "mobile_app_auth.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("secret") or "").strip() if isinstance(payload, dict) else ""


def verify_mobile_access_token(token: str, *, now: int | None = None) -> EngineAuthContext | None:
    parts = str(token or "").strip().split(".")
    if len(parts) != 3 or not all(parts):
        return None
    secret = _read_secret()
    if not secret:
        return None
    encoded_header, encoded_payload, signature = parts
    expected = hmac.new(secret.encode(), f"{encoded_header}.{encoded_payload}".encode(), hashlib.sha256).digest()
    try:
        actual = _b64(signature)
        if not hmac.compare_digest(actual, expected):
            return None
        payload: Any = json.loads(_b64(encoded_payload).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "mobile_access":
        return None
    try:
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    if expires_at <= int(time.time() if now is None else now) or expires_at <= issued_at:
        return None
    subject = str(payload.get("sub") or "").strip()
    session_id = str(payload.get("sid") or "").strip()
    login = str(payload.get("login") or "").strip()
    role = str(payload.get("role") or "").strip()
    if not subject or not session_id or not login or not role:
        return None
    return EngineAuthContext(subject, session_id, login, role, str(payload.get("did") or "").strip() or None, issued_at, expires_at, str(payload.get("iss") or "").strip() or None, str(payload.get("aud") or "").strip() or None)

