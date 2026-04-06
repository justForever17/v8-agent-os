from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.json_safe import to_jsonable
from core.system_base import get_internal_secret


LEGACY_TOPIC_MAP = {
    "protocol_connected": "session.connected",
    "agent_start": "agent.started",
    "text_chunk": "run.text.delta",
    "reasoning_chunk": "run.reasoning.delta",
    "tool_start": "tool.started",
    "tool_result": "tool.finished",
    "done": "run.completed",
    "error": "run.failed",
}


def format_ndjson(data: Dict[str, Any]) -> str:
    return json.dumps(data, default=str) + "\n"


def protocol_connected_event(session_id: str, transport: str = "http", run_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "protocol_connected",
        "conversationId": session_id,
        "sessionId": session_id,
        "transport": transport,
        "runId": run_id,
        "run_id": run_id,
    }


def hello_event(transport: str = "websocket") -> Dict[str, Any]:
    return {
        "v": 1,
        "kind": "hello",
        "topic": "session.connected",
        "event_id": f"evt_{uuid.uuid4().hex}",
        "ts": utc_now_iso(),
        "payload": {
            "transport": transport,
            "protocol": "v8chat.chat.realtime.v1",
        },
    }


def heartbeat_event() -> Dict[str, Any]:
    return {
        "v": 1,
        "kind": "heartbeat",
        "topic": "session.heartbeat",
        "event_id": f"evt_{uuid.uuid4().hex}",
        "ts": utc_now_iso(),
        "payload": {"ok": True},
    }


def runtime_envelope(
    legacy_event: Dict[str, Any],
    *,
    session_id: str,
    run_id: str,
    seq: int,
    agent_id: Optional[str] = None,
    node: Optional[str] = None,
) -> Dict[str, Any]:
    event_type = str(legacy_event.get("type", "event"))
    return build_runtime_event(
        topic=LEGACY_TOPIC_MAP.get(event_type, f"legacy.{event_type}"),
        payload=legacy_event,
        session_id=session_id,
        conversation_id=session_id,
        run_id=run_id,
        seq=seq,
        source={
            "plane": "engine",
            "component": "chat_runtime",
            "node": node or agent_id or "supervisor",
            "agent_id": agent_id or "supervisor",
        },
    )


def build_runtime_event(
    *,
    topic: str,
    payload: Dict[str, Any],
    kind: str = "event",
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    run_id: Optional[str] = None,
    seq: Optional[int] = None,
    source: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "v": 1,
        "kind": kind,
        "topic": topic,
        "event_id": event_id or f"evt_{uuid.uuid4().hex}",
        "session_id": session_id,
        "conversation_id": conversation_id or session_id,
        "run_id": run_id,
        "seq": seq,
        "ts": ts or utc_now_iso(),
        "source": source or {
            "plane": "engine",
            "component": "chat_runtime",
            "node": "supervisor",
            "agent_id": "supervisor",
        },
        "payload": to_jsonable(payload),
    }


def verify_ws_ticket(ticket: Optional[str]) -> Optional[Dict[str, Any]]:
    secret = get_internal_secret()
    if not secret:
        return {"sub": "anonymous", "aud": "chat_ws", "mode": "insecure"}

    if not ticket:
        return None

    try:
        payload_b64, signature_b64 = ticket.split(".", 1)
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        given_sig = _b64url_decode(signature_b64)
        if not hmac.compare_digest(given_sig, expected_sig):
            return None

        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        exp = int(payload.get("exp", 0))
        if exp <= int(time.time()):
            return None
        if payload.get("aud") not in {"chat_ws", "realtime"}:
            return None
        return payload
    except Exception:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))
