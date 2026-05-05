import json
import asyncio
import mimetypes
import re
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .models import ChatRequest
from core.database import db
from core.json_safe import to_jsonable
from core.scoped_workspace_resource import build_workspace_resource_ref
from core.workspace_capability import build_workspace_binding
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
from runtimes.memory.scope_resolution import ScopeBindingConflictError
from runtimes.chat.runtime import chat_runtime


router = APIRouter()
_UPLOAD_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def _engine_now_ms() -> int:
    return int(time.time() * 1000)


def _engine_ms_to_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _mark_engine_yield(event: dict) -> dict:
    timestamp_ms = _engine_now_ms()
    diagnostics = dict(event.get("_diagnostics") or {})
    diagnostics["engineYieldAtMs"] = timestamp_ms
    diagnostics["engineYieldAt"] = _engine_ms_to_iso(timestamp_ms)
    event["_diagnostics"] = diagnostics
    return event


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
    try:
        async for event in chat_runtime.stream_legacy_events(request, transport=transport, run_id=run_id):
            if isinstance(event, dict):
                event = _mark_engine_yield(event)
            yield event
    except ScopeBindingConflictError as exc:
        yield build_runtime_event(
            kind="error",
            topic="scope.conflict",
            session_id=request.session_id,
            run_id=run_id or request.resume_run_id,
            payload=exc.payload,
            source={
                "plane": "engine",
                "component": "chat_runtime",
                "node": "scope_resolution",
                "agent_id": None,
            },
        )


async def event_generator(request: ChatRequest, run_id: str):
    async for event in iter_chat_events(request, transport="http", run_id=run_id):
        yield format_ndjson(event)


def _safe_upload_filename(value: str | None) -> str:
    raw = Path(str(value or "").strip()).name or f"upload-{uuid.uuid4().hex[:8]}"
    safe = _UPLOAD_FILENAME_SAFE_RE.sub("_", raw).strip(" .")
    return safe[:140] or f"upload-{uuid.uuid4().hex[:8]}"


def _form_text(form: object, *names: str) -> str:
    getter = getattr(form, "get", None)
    if not callable(getter):
        return ""
    for name in names:
        value = getter(name)
        text = str(value or "").strip()
        if text:
            return text
    return ""


@router.post("/chat/upload")
async def chat_upload(request: Request):
    form = await request.form()
    upload = form.get("file") if hasattr(form, "get") else None
    if not upload or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="缺少上传文件。")

    filename = _safe_upload_filename(getattr(upload, "filename", "") or None)
    session_id = _form_text(form, "sessionId", "session_id", "conversationId", "conversation_id")
    workspace_id = _form_text(form, "workspaceId", "workspace_id")
    workspace_path = _form_text(form, "workspacePath", "workspace_path")
    project_id = _form_text(form, "projectId", "project_id")
    binding = build_workspace_binding(
        {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "project_id": project_id,
        },
        runtime_kind="chat",
    )
    workspace_root = binding.active_workspace_root.resolve(strict=False)
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise HTTPException(status_code=404, detail=f"Active Workspace Root 不存在或不是目录: {workspace_root}")

    upload_dir = workspace_root / ".v8" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    resolved_upload_dir = upload_dir.resolve(strict=False)
    try:
        resolved_upload_dir.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="上传目录越过当前工作区边界，已拒绝。") from exc

    unique_filename = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}-{filename}"
    target = (resolved_upload_dir / unique_filename).resolve(strict=False)
    try:
        target.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="上传目标越过当前工作区边界，已拒绝。") from exc

    content = await upload.read()
    if not isinstance(content, (bytes, bytearray)):
        raise HTTPException(status_code=400, detail="上传文件内容不可读取。")
    target.write_bytes(bytes(content))

    workspace_relative_path = target.relative_to(workspace_root).as_posix()
    content_type = str(getattr(upload, "content_type", "") or "").strip() or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    resource_ref = build_workspace_resource_ref(
        workspace_relative_path=workspace_relative_path,
        path_plane="workspace_download",
        workspace_root=workspace_root,
        workspace_id=binding.workspace_id or None,
        project_id=binding.project_id or None,
        mime_type=content_type,
        display_label=filename,
        previewable=content_type.startswith(("image/", "video/", "audio/", "text/")),
        downloadable=True,
        surface_visible=True,
    )
    admin_path = str(resource_ref.get("adminPath") or "")
    return {
        "id": f"upload_{uuid.uuid4().hex[:16]}",
        "name": filename,
        "url": admin_path,
        "publicUrl": admin_path,
        "previewUrl": admin_path,
        "path": workspace_relative_path,
        "workspacePath": str(target),
        "workspaceRelativePath": workspace_relative_path,
        "workspaceRoot": str(workspace_root),
        "workspaceId": binding.workspace_id or None,
        "projectId": binding.project_id or None,
        "type": content_type,
        "size": len(content),
        "createdAt": utc_now_iso(),
        "resourceRef": resource_ref,
        "metadata": {
            "sessionId": session_id or None,
            "source": "os_phone_upload",
            "workspaceBinding": binding.as_dict(),
        },
    }


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
    client_message_id = request.client_message_id or (getattr(request.data, "client_message_id", None) if request.data else None)
    user_message = None
    execution_request = request
    if not request.resume_run_id:
        try:
            chat_run = chat_runtime.prepare_run_context(request, transport="submit", run_id=run_id)
        except ScopeBindingConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.payload) from exc
        user_message = chat_runtime.record_request_inputs(chat_run)
        if not user_message:
            user_message = db.get_chat_canonical_message_by_run(
                session_id=chat_run.session_id,
                run_id=chat_run.active_run_id,
                role="user",
            )
    _schedule_chat_run(execution_request, transport="submit", run_id=run_id)
    return {
        "accepted": True,
        "session_id": request.session_id,
        "conversationId": request.session_id,
        "clientMessageId": client_message_id,
        "run_id": run_id,
        "runId": run_id,
        "userMessage": to_jsonable(user_message) if user_message else None,
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
                            "contextGovernanceHistory": snapshot_payload.get("contextGovernanceHistory") or [],
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
