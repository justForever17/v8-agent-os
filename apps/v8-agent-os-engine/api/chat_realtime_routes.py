import json
import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .models import ChatRequest
from core.database import db
from core.json_safe import to_jsonable
from core.realtime_protocol import (
    build_runtime_event,
    format_ndjson,
    hello_event,
    heartbeat_event,
    runtime_envelope,
    utc_now_iso,
    verify_ws_ticket,
)
from erc.command_router import runtime_command_router
from erc.models import RuntimeCommand
from erc.session_runtime import session_runtime_service
from runtimes.chat.runtime import chat_runtime


router = APIRouter()


async def _send_json_safe(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_text(json.dumps(to_jsonable(payload), ensure_ascii=False, separators=(",", ":")))


def _runtime_error_event(topic: str, message: str, *, session_id: str | None = None, run_id: str | None = None):
    return build_runtime_event(
        kind="error",
        topic=topic,
        session_id=session_id,
        run_id=run_id,
        payload={"message": message},
        source={
            "plane": "engine",
            "component": "chat_ws",
            "node": "command_router",
            "agent_id": None,
        },
    )


def _runtime_ack_event(topic: str, payload: dict, *, session_id: str | None = None, run_id: str | None = None):
    return build_runtime_event(
        kind="ack",
        topic=topic,
        session_id=session_id,
        run_id=run_id,
        payload=payload,
        source={
            "plane": "engine",
            "component": "chat_ws",
            "node": "command_router",
            "agent_id": None,
        },
    )


def _fire_on_chat_end_if_terminal(session_id: str, run_id: str | None) -> None:
    if not run_id:
        return
    from core.terminal_post_run import terminal_post_run_service

    terminal_post_run_service.dispatch(
        session_id=session_id,
        run_id=run_id,
        source_component="chat_realtime",
    )


async def _drain_chat_run(request: ChatRequest, *, transport: str, run_id: str | None = None) -> None:
    active_run_id = run_id or request.resume_run_id
    async for _ in iter_chat_events(request, transport=transport, run_id=run_id):
        pass
    _fire_on_chat_end_if_terminal(request.session_id or "", active_run_id)


def _schedule_chat_run(request: ChatRequest, *, transport: str, run_id: str | None = None) -> str | None:
    scheduled_run_id = run_id or request.resume_run_id
    asyncio.create_task(_drain_chat_run(request, transport=transport, run_id=run_id))
    return scheduled_run_id


runtime_command_router.configure(schedule_chat_run=_schedule_chat_run)


async def iter_chat_events(request: ChatRequest, transport: str = "http", run_id: str | None = None):
    async for event in chat_runtime.stream_legacy_events(request, transport=transport, run_id=run_id):
        yield event


async def event_generator(request: ChatRequest, run_id: str):
    async for event in iter_chat_events(request, transport="http", run_id=run_id):
        yield format_ndjson(event)


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, background_tasks: BackgroundTasks):
    if not request.session_id:
        request.session_id = str(uuid.uuid4())
    run_id = request.resume_run_id or f"run_{uuid.uuid4().hex}"

    def trigger_on_chat_end():
        _fire_on_chat_end_if_terminal(request.session_id or "", run_id)

    background_tasks.add_task(trigger_on_chat_end)

    return StreamingResponse(
        event_generator(request, run_id),
        media_type="application/x-ndjson",
        background=background_tasks,
    )


@router.post("/chat/submit")
async def chat_submit(request: ChatRequest):
    if not request.session_id:
        request.session_id = str(uuid.uuid4())

    run_id = request.resume_run_id or f"run_{uuid.uuid4().hex}"
    _schedule_chat_run(request, transport="submit", run_id=run_id)
    return {
        "accepted": True,
        "session_id": request.session_id,
        "conversationId": request.session_id,
        "run_id": run_id,
        "runId": run_id,
    }


@router.websocket("/chat/ws")
async def chat_websocket(websocket: WebSocket):
    ticket_payload = verify_ws_ticket(websocket.query_params.get("ticket"))
    if ticket_payload is None:
        await websocket.close(code=4401, reason="Invalid or expired websocket ticket")
        return

    await websocket.accept()
    await _send_json_safe(websocket, hello_event())

    try:
        while True:
            incoming = await websocket.receive_json()

            if not isinstance(incoming, dict):
                await _send_json_safe(websocket,
                    {
                        "v": 1,
                        "kind": "error",
                        "topic": "runtime.invalid_message",
                        "event_id": f"evt_{uuid.uuid4().hex}",
                        "ts": utc_now_iso(),
                        "payload": {"message": "WebSocket payload must be a JSON object"},
                    }
                )
                continue

            kind = incoming.get("kind")
            topic = incoming.get("topic")

            if kind == "heartbeat" or topic == "session.heartbeat":
                await _send_json_safe(websocket, heartbeat_event())
                continue

            if kind == "command" and topic == "session.subscribe":
                session_id = incoming.get("session_id") or incoming.get("payload", {}).get("session_id")
                if not session_id:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.invalid_request", "session.subscribe requires session_id")
                    )
                    continue
                include_snapshot = bool((incoming.get("payload") or {}).get("include_snapshot"))
                subscription = session_runtime_service.subscribe(session_id, include_snapshot=include_snapshot)
                await _send_json_safe(websocket, subscription["ack"])
                if subscription.get("snapshot_event"):
                    await _send_json_safe(websocket, subscription["snapshot_event"])
                continue

            if kind == "command" and topic == "session.snapshot.get":
                session_id = incoming.get("session_id") or incoming.get("payload", {}).get("session_id")
                if not session_id:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.invalid_request", "session.snapshot.get requires session_id")
                    )
                    continue
                snapshot_payload = runtime_command_router.get_snapshot(session_id)
                await _send_json_safe(websocket,
                    build_runtime_event(
                        kind="snapshot",
                        topic="session.snapshot.ready",
                        session_id=session_id,
                        seq=snapshot_payload.get("latestSeq", 0),
                        payload=snapshot_payload,
                        source={
                            "plane": "engine",
                            "component": "session_runtime",
                            "node": "snapshot_service",
                            "agent_id": None,
                        },
                    )
                )
                continue

            if kind == "command" and topic == "session.events.get":
                payload = incoming.get("payload") or {}
                session_id = incoming.get("session_id") or payload.get("session_id")
                if not session_id:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.invalid_request", "session.events.get requires session_id")
                    )
                    continue
                after_seq = payload.get("after_seq")
                events_payload = runtime_command_router.get_events(session_id, after_seq=after_seq)
                snapshot_payload = runtime_command_router.get_snapshot(session_id)
                await _send_json_safe(websocket,
                    _runtime_ack_event(
                        "session.events.replayed",
                        {
                            "session_id": session_id,
                            "events": events_payload.get("events", []),
                            "latest_seq": events_payload.get("latestSeq", 0),
                            "after_seq": after_seq,
                            "source": snapshot_payload.get("source"),
                            "contextGovernance": snapshot_payload.get("contextGovernance"),
                        },
                        session_id=session_id,
                    )
                )
                continue

            if kind == "command" and topic in {"run.pause", "run.resume", "run.cancel"}:
                payload = incoming.get("payload") or {}
                command = RuntimeCommand(
                    topic=topic,
                    run_id=incoming.get("run_id") or payload.get("run_id"),
                    reason=payload.get("reason") or topic.replace("run.", "manual_"),
                    payload=dict(payload),
                )
                if not command.run_id:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.invalid_request", f"{topic} requires run_id")
                    )
                    continue

                command_result = runtime_command_router.dispatch_run_command(command)
                if not command_result:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.not_found", f"Run '{command.run_id}' does not exist", run_id=command.run_id)
                    )
                    continue

                await _send_json_safe(websocket, command_result["transition_event"])
                await _send_json_safe(websocket, command_result["command_event"])
                continue

            if kind == "command" and topic in {"run.interrupt", "run.retry"}:
                payload = incoming.get("payload") or {}
                command = RuntimeCommand(
                    topic=topic,
                    run_id=incoming.get("run_id") or payload.get("run_id"),
                    reason=payload.get("reason") or topic.replace("run.", "manual_"),
                    payload=dict(payload),
                )
                if not command.run_id:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.invalid_request", f"{topic} requires run_id")
                    )
                    continue

                command_result = runtime_command_router.dispatch_run_command(command)
                if not command_result:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.not_found", f"Run '{command.run_id}' does not exist", run_id=command.run_id)
                    )
                    continue

                if command_result.get("transition_event"):
                    await _send_json_safe(websocket, command_result["transition_event"])
                await _send_json_safe(websocket, command_result["command_event"])
                continue

            if kind == "command" and topic in {"approval.approve", "approval.reject"}:
                payload = incoming.get("payload") or {}
                command = RuntimeCommand(
                    topic=topic,
                    approval_id=incoming.get("approval_id") or payload.get("approval_id"),
                    response=dict(payload.get("response") or {}),
                    payload=dict(payload),
                )
                if not command.approval_id:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.invalid_request", f"{topic} requires approval_id")
                    )
                    continue

                command_result = runtime_command_router.dispatch_approval_command(command)
                if not command_result:
                    await _send_json_safe(websocket,
                        _runtime_error_event("runtime.not_found", f"Approval '{command.approval_id}' does not exist")
                    )
                    continue

                await _send_json_safe(websocket, command_result["transition_event"])
                await _send_json_safe(websocket, command_result["command_event"])
                continue

            request_payload = incoming.get("request")
            if request_payload is None and kind == "command" and topic in {"chat.start", "chat.resume"}:
                request_payload = incoming.get("payload")
            if request_payload is None:
                request_payload = incoming

            try:
                request = ChatRequest.model_validate(request_payload)
            except Exception as exc:
                await _send_json_safe(websocket,
                    {
                        "v": 1,
                        "kind": "error",
                        "topic": "runtime.invalid_request",
                        "event_id": f"evt_{uuid.uuid4().hex}",
                        "ts": utc_now_iso(),
                        "payload": {"message": str(exc)},
                    }
                )
                continue

            if not request.session_id:
                request.session_id = str(uuid.uuid4())
            if (not request.user_id or request.user_id == "anonymous") and ticket_payload:
                request.user_id = ticket_payload.get("sub") or "anonymous"

            run_id = request.resume_run_id or f"run_{uuid.uuid4().hex}"
            seq = 0
            current_agent_id = "supervisor"

            async for legacy_event in iter_chat_events(request, transport="websocket", run_id=run_id):
                if legacy_event.get("type") == "agent_start":
                    current_agent_id = legacy_event.get("agent", {}).get("id") or current_agent_id or "supervisor"
                seq += 1
                await _send_json_safe(websocket,
                    runtime_envelope(
                        legacy_event,
                        session_id=request.session_id,
                        run_id=run_id,
                        seq=seq,
                        agent_id=current_agent_id,
                    )
                )
                if legacy_event.get("type") in {"done", "error"}:
                    break

            try:
                _fire_on_chat_end_if_terminal(request.session_id or "", run_id)
            except Exception as hook_exc:
                print(f"[Engine WS] Failed to trigger on_chat_end hook: {hook_exc}")
    except WebSocketDisconnect:
        return
