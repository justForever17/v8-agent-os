"""Durable Engine events -> human-client SSE, without an Admin fanout hop."""
from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from core.client_transport import internal_json

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def _sse(name, value, *, session_id="", seq=None):
    event_id = f"id: {session_id}:{seq}\n" if seq is not None else ""
    return f"{event_id}event: {name}\ndata: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def _valid(request, principal):
    if principal.auth_method == "internal_service":
        return True
    from core.client_identity import get_identity_service
    token = request.headers.get("authorization", "").partition(" ")[2]
    context = await asyncio.to_thread(get_identity_service().verify_access, token)
    return bool(context and context.subject == principal.subject and context.device_id == principal.device_id)


def _client_event(event, request, principal):
    from api.client_routes import normalize_client_surface
    event = dict(event)
    payload = dict(event.get("payload") or {})
    # Runtime records retain raw execution evidence. The human transport uses
    # the existing Agent Surface projection instead of exposing raw tool JSON.
    topic = str(event.get("topic") or "")
    if topic == "tool.finished" or topic.endswith(".tool.finished"):
        visible = payload.get("agentVisibleResult", payload.get("agent_visible_result"))
        if visible is None:
            from core.runtime_projection import _agent_surface_for_missing_tool_result
            visible, _ = _agent_surface_for_missing_tool_result(str(payload.get("toolName") or payload.get("tool_name") or ""), payload.get("result"))
        payload["result"] = visible
        payload.pop("rawResult", None)
        payload.pop("raw_result", None)
    event["payload"] = payload
    return normalize_client_surface(event, request, principal)


async def _activity_events(request, principal):
    from core.session_activity import session_activity_broker
    cursor = session_activity_broker.current_seq
    # ready means refresh the index; retained history is not silently assumed.
    yield _sse("ready", {"seq": cursor})
    last_heartbeat = time.monotonic()
    while not await request.is_disconnected():
        if not await _valid(request, principal):
            yield _sse("auth_expired", {"code": "client_auth_expired"})
            return
        cursor, signals = await asyncio.to_thread(session_activity_broker.wait, owner_id=principal.session_id,
                                                  after_seq=cursor, timeout_seconds=0.5)
        for signal in signals:
            yield _sse("activity", signal)
        if time.monotonic() - last_heartbeat >= 12:
            yield _sse("heartbeat", {"seq": cursor})
            last_heartbeat = time.monotonic()


async def _session_events(request, principal, session_id, compact):
    from api.client_routes import normalize_client_surface, require_session
    raw_cursor = request.headers.get("last-event-id") or request.query_params.get("after_seq") or "0"
    # Event ids bind the resume point to a session; another session's cursor is
    # never applied. Old Admin ids were counters, so a fresh snapshot is sent.
    if ":" in raw_cursor:
        prefix, _, number = raw_cursor.rpartition(":")
        raw_cursor = number if prefix == session_id else "0"
    try:
        cursor = max(0, int(raw_cursor))
    except ValueError:
        cursor = 0

    async def snapshot():
        payload = await internal_json(request, principal, f"/sessions/{session_id}/snapshot", query={"compact": int(compact)})
        return normalize_client_surface(payload, request, principal)

    state = await snapshot()
    watermark = int(state.get("latestSeq") or 0)
    # A snapshot establishes a durable cursor. New events after that cursor are
    # replayed even if appended while the snapshot response is being rendered.
    if not cursor or cursor > watermark:
        cursor = watermark
    yield _sse("snapshot", state, session_id=session_id, seq=watermark)
    heartbeat_at = time.monotonic()
    snapshot_at = heartbeat_at
    delay = 0.15
    dirty = False
    while not await request.is_disconnected():
        if not await _valid(request, principal):
            yield _sse("auth_expired", {"code": "client_auth_expired"})
            return
        try:
            require_session(request, principal, session_id)
        except HTTPException:
            yield _sse("error", {"code": "session_not_found"})
            return
        page = await internal_json(request, principal, f"/sessions/{session_id}/runtime-events",
                                   query={"after_seq": cursor, "limit": 128})
        events = page.get("events", [])
        latest = int(page.get("latestSeq") or 0)
        expected = cursor
        gap = bool(not events and latest > cursor)
        for event in events:
            seq = int(event.get("seq") or 0)
            if seq <= expected:
                continue
            if seq > expected + 1:
                gap = True
                break
            expected = seq
        if gap:
            state = await snapshot()
            cursor = max(cursor, int(state.get("latestSeq") or 0))
            yield _sse("snapshot", {**state, "replayRecovery": "snapshot_gap"}, session_id=session_id, seq=cursor)
            snapshot_at = time.monotonic()
        else:
            changed = False
            for event in events:
                seq = int(event.get("seq") or 0)
                if seq <= cursor:
                    continue
                cursor = seq
                topic = str(event.get("topic") or "")
                # Match the existing session-realtime exclusions at transport
                # time: model response diagnostics may contain full raw traces.
                if (event.get("visibility") in {"internal", "runtime", "excluded", "history_only"}
                        or topic in {"session.connected", "session.subscribed", "extension.execution.completed"}
                        or topic.startswith("desktop_live.") or topic.endswith(".diagnostic")):
                    continue
                yield _sse("runtime", _client_event(event, request, principal), session_id=session_id, seq=seq)
                changed = True
            dirty = dirty or changed
            # A terminal event may be followed by silence. Keep the pending
            # snapshot until flushed instead of relying on another event.
            if dirty and time.monotonic() - snapshot_at >= 0.5:
                state = await snapshot()
                yield _sse("snapshot", state, session_id=session_id, seq=int(state.get("latestSeq") or cursor))
                snapshot_at = time.monotonic()
                dirty = False
        if time.monotonic() - heartbeat_at >= 12:
            yield _sse("heartbeat", {"ok": True, "seq": cursor})
            heartbeat_at = time.monotonic()
        delay = 0.15 if events else min(0.5, delay * 1.5)
        await asyncio.sleep(delay)


async def client_realtime(request: Request, principal, path: str):
    from api.client_routes import SEGMENT, normalize_client_surface, require_session
    if path == "realtime/session-activity/stream":
        return StreamingResponse(_activity_events(request, principal), media_type="text/event-stream", headers=SSE_HEADERS)
    match = re.fullmatch(rf"realtime/sessions/({SEGMENT})/(snapshot|stream)", path)
    if not match:
        raise HTTPException(404, "client_route_not_found")
    session_id, action = match.groups()
    require_session(request, principal, session_id)
    compact = request.query_params.get("compact") == "1" or request.query_params.get("surface") in {"phone", "desktop"}
    if action == "snapshot":
        result = await internal_json(request, principal, f"/sessions/{session_id}/snapshot", query={"compact": int(compact)})
        return JSONResponse(normalize_client_surface(result, request, principal))
    return StreamingResponse(_session_events(request, principal, session_id, compact), media_type="text/event-stream", headers=SSE_HEADERS)
