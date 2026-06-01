import json
import asyncio
import logging
import mimetypes
import re
import threading
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .models import ChatMessage, ChatRequest
from core.database import db
from core.json_safe import to_jsonable
from core.scoped_workspace_resource import build_workspace_resource_ref
from core.workspace_capability import build_workspace_binding
from core.workspace_state_digest import mark_workspace_state_stale
from core.realtime_protocol import (
    build_runtime_event,
    format_ndjson,
    hello_event,
    heartbeat_event,
    runtime_envelope,
    utc_now_iso,
    verify_ws_ticket,
)
from erc.command_service import command_service
from erc.command_router import runtime_command_router
from erc.models import RuntimeCommand
from erc.session_runtime import session_runtime_service
from runtimes.memory.scope_resolution import ScopeBindingConflictError
from runtimes.chat.runtime import chat_runtime


router = APIRouter()
_UPLOAD_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")
logger = logging.getLogger("v8chat.chat_realtime")


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


_ACTIVE_CHAT_STATUSES = {
    "queued",
    "pending",
    "starting",
    "attached",
    "observing",
    "streaming",
    "running",
    "waiting",
    "waiting_approval",
    "waiting_input",
    "waiting_external_tool",
    "waiting_external",
    "paused",
}
_TERMINAL_CHAT_STATUSES = {"completed", "finished", "failed", "cancelled", "canceled", "blocked", "error"}
_FALLBACK_ACTIVE_CHAT_STATUSES = _ACTIVE_CHAT_STATUSES - {"paused"}


def _resolve_request_session_id(request: ChatRequest) -> str:
    data = request.data
    resolved = str(
        request.session_id
        or request.conversation_id
        or (getattr(data, "conversation_id", None) if data else None)
        or ""
    ).strip()
    if not resolved:
        resolved = str(uuid.uuid4())
    request.session_id = resolved
    request.conversation_id = request.conversation_id or resolved
    if data is not None and not getattr(data, "conversation_id", None):
        data.conversation_id = resolved
    return resolved


def _emit_human_guidance_event(topic: str, *, session_id: str, run_id: str | None, payload: dict) -> dict:
    event = build_runtime_event(
        kind="event",
        topic=topic,
        session_id=session_id,
        run_id=run_id,
        seq=db.get_next_runtime_seq(session_id) if session_id else None,
        payload=payload,
        source={
            "plane": "engine",
            "component": "chat_runtime",
            "node": "human_guidance_queue",
            "agent_id": None,
        },
    )
    db.add_runtime_event(event)
    return event


def _find_active_chat_run(session_id: str) -> dict | None:
    if not session_id:
        return None
    lane = db.get_session_lane_record(session_id) or {}
    lane_run_id = str(lane.get("active_run_id") or "").strip()
    if lane_run_id:
        record = db.get_run_record(lane_run_id)
        if record and str(record.get("status") or "").strip().lower() in _ACTIVE_CHAT_STATUSES:
            return record
        lane_state = str(lane.get("state") or "").strip().lower()
        if lane_state and lane_state not in {"idle", "released", "completed", "failed", "cancelled", "canceled"}:
            return {
                "id": lane_run_id,
                "session_id": session_id,
                "status": lane_state,
                "metadata": {
                    "source": "session_lane_record",
                    "runRecordMissing": record is None,
                    "runRecordStatus": (record or {}).get("status"),
                },
            }
    for record in db.list_run_records(session_id=session_id, run_type="chat", limit=20):
        if str(record.get("status") or "").strip().lower() in _FALLBACK_ACTIVE_CHAT_STATUSES:
            return record
    return None


def _existing_queue_item_for_client_message(session_id: str, client_message_id: str | None) -> dict | None:
    normalized_client_message_id = str(client_message_id or "").strip()
    if not normalized_client_message_id:
        return None
    return db.get_chat_user_message_queue_item_by_client_message_id(
        session_id=session_id,
        client_message_id=normalized_client_message_id,
    )


def _latest_user_content_from_request(request: ChatRequest) -> str:
    for message in reversed(list(request.messages or [])):
        if str(message.role or "").strip().lower() == "user":
            return str(message.content or "").strip()
    return ""


def _queue_item_payload(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "sessionId": item.get("session_id"),
        "runId": item.get("run_id"),
        "clientMessageId": item.get("client_message_id"),
        "content": item.get("content") or "",
        "state": item.get("state") or "pending",
        "ordinal": item.get("ordinal"),
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
        "promotedAt": item.get("promoted_at"),
        "injectedAt": item.get("injected_at"),
        "consumedAt": item.get("consumed_at"),
        "cancelledAt": item.get("cancelled_at"),
    }


def _legacy_messages_for_request(session_id: str) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for item in db.get_messages(session_id):
        role = str(item.get("role") or "").strip() or "user"
        content = str(item.get("content") or "")
        if not content and role not in {"assistant", "tool"}:
            continue
        messages.append(ChatMessage(role=role, content=content))
    return messages


def _request_from_queue_item(item: dict, *, run_id: str) -> ChatRequest:
    stored_request = item.get("request") if isinstance(item.get("request"), dict) else {}
    try:
        base_request = ChatRequest.model_validate(stored_request) if stored_request else ChatRequest(messages=[])
    except Exception:
        base_request = ChatRequest(messages=[])
    session_id = str(item.get("session_id") or base_request.session_id or "").strip()
    if not session_id:
        raise RuntimeError("Queued message is missing session_id.")
    history = _legacy_messages_for_request(session_id)
    history.append(ChatMessage(role="user", content=str(item.get("content") or "")))
    base_request.messages = history
    base_request.session_id = session_id
    base_request.conversation_id = base_request.conversation_id or session_id
    base_request.client_message_id = str(item.get("client_message_id") or "").strip() or f"queued_{item.get('id')}"
    if base_request.data is not None:
        base_request.data.client_message_id = base_request.client_message_id
    base_request.resume_run_id = None
    return base_request


def _schedule_next_queued_user_message(session_id: str) -> str | None:
    pending = db.list_chat_user_message_queue(session_id=session_id, states=["pending"], limit=1)
    if not pending:
        return None
    next_run_id = f"run_{uuid.uuid4().hex}"
    db.create_run_record(
        run_id=next_run_id,
        session_id=session_id,
        run_type="chat",
        status="queued",
        trigger_source="queued_user_message",
        metadata={
            "source": "chat_user_message_queue",
            "queueItemId": pending[0].get("id"),
        },
    )
    item = db.claim_next_pending_chat_user_message(session_id=session_id, consumed_run_id=next_run_id)
    if not item:
        db.update_run_record(
            next_run_id,
            status="cancelled",
            error_message="Queued user message was claimed by another drain before scheduling.",
            metadata={"source": "chat_user_message_queue", "cancelReason": "claim_lost"},
        )
        return None
    request = _request_from_queue_item(item, run_id=next_run_id)
    _emit_human_guidance_event(
        "human_guidance.consumed",
        session_id=session_id,
        run_id=next_run_id,
        payload={
            "queueMessage": _queue_item_payload(item),
            "state": "consumed",
            "summary": "上一轮已完成，正在自动消费一条排队消息。",
        },
    )
    return _schedule_chat_run(request, transport="queued_user_message", run_id=next_run_id)


def _fire_on_chat_end_if_terminal(session_id: str, run_id: str | None) -> None:
    if not run_id:
        return
    from core.terminal_post_run import terminal_post_run_service

    try:
        terminal_post_run_service.dispatch(
            session_id=session_id,
            run_id=run_id,
            source_component="chat_realtime",
        )
    except Exception as exc:
        logger.warning(
            "Terminal post-run dispatch failed for session %s run %s; keeping chat run terminal state intact: %s",
            session_id,
            run_id,
            exc,
        )
        try:
            event = build_runtime_event(
                kind="event",
                topic="terminal_post_run.failed",
                session_id=session_id,
                run_id=run_id,
                seq=db.get_next_runtime_seq(session_id) if session_id else None,
                payload={
                    "summary": "终端治理任务失败，已作为诊断记录，不影响本轮对话完成态。",
                    "sourceComponent": "chat_realtime",
                    "error": str(exc),
                    "governanceOnly": True,
                    "hiddenFromHistory": True,
                },
                source={
                    "plane": "engine",
                    "component": "chat_realtime",
                    "node": "terminal_post_run",
                    "agent_id": None,
                },
            )
            db.add_runtime_event(event)
        except Exception:
            logger.debug(
                "Failed to persist terminal post-run diagnostic for session %s run %s",
                session_id,
                run_id,
                exc_info=True,
            )

    try:
        record = db.get_run_record(run_id) or {}
        if str(record.get("status") or "").strip().lower() != "completed":
            return
        for item in db.requeue_promoted_chat_user_messages_for_run(
            session_id=session_id,
            run_id=run_id,
            reason="run_completed_before_guidance_injection",
        ):
            _emit_human_guidance_event(
                "human_guidance.queued",
                session_id=session_id,
                run_id=run_id,
                payload={
                    "queueMessage": _queue_item_payload(item),
                    "state": "pending",
                    "summary": "运行已结束，引导未赶上安全检查点，已回到消息队列。",
                },
            )
        _schedule_next_queued_user_message(session_id)
    except Exception as exc:
        logger.warning(
            "Completed chat queue drain failed for session %s run %s; preserving completed run state: %s",
            session_id,
            run_id,
            exc,
        )


async def _drain_chat_run(request: ChatRequest, *, transport: str, run_id: str | None = None) -> None:
    active_run_id = run_id or request.resume_run_id
    async for _ in iter_chat_events(request, transport=transport, run_id=run_id):
        pass
    _fire_on_chat_end_if_terminal(request.session_id or "", active_run_id)


def _schedule_chat_run(request: ChatRequest, *, transport: str, run_id: str | None = None) -> str | None:
    scheduled_run_id = run_id or request.resume_run_id
    threading.Thread(
        target=lambda: asyncio.run(_drain_chat_run(request, transport=transport, run_id=run_id)),
        name=f"chat-run-{scheduled_run_id or uuid.uuid4().hex[:8]}",
        daemon=True,
    ).start()
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
    await run_in_threadpool(target.write_bytes, bytes(content))
    mark_workspace_state_stale(
        {
            "runtime_kind": "chat",
            "session_id": session_id,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "project_id": project_id,
        },
        reason="workspace_upload",
        subject=str(target),
    )

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
    session_id = _resolve_request_session_id(request)
    if not request.resume_run_id:
        active_run = _find_active_chat_run(session_id)
        if active_run:
            content = _latest_user_content_from_request(request)
            if not content and not (request.attachments or request.fileUrls):
                raise HTTPException(status_code=400, detail="运行中队列消息必须包含文本或附件。")
            client_message_id = request.client_message_id or (getattr(request.data, "client_message_id", None) if request.data else None)
            queue_item = _existing_queue_item_for_client_message(session_id, client_message_id)
            if not queue_item:
                queue_item = db.add_chat_user_message_queue_item(
                    queue_id=f"queued_{uuid.uuid4().hex}",
                    session_id=session_id,
                    run_id=str(active_run.get("id") or ""),
                    client_message_id=client_message_id,
                    content=content or "已上传附件",
                    attachments=[item.model_dump(mode="json", by_alias=True) for item in list(request.attachments or [])],
                    file_urls=list(request.fileUrls or []),
                    request_payload=request.model_dump(mode="json", by_alias=True),
                    metadata={
                        "source": "chat_stream_while_run_active",
                        "activeRunStatus": active_run.get("status"),
                    },
                )
            event = _emit_human_guidance_event(
                "human_guidance.queued",
                session_id=session_id,
                run_id=str(active_run.get("id") or ""),
                payload={
                    "queueMessage": _queue_item_payload(queue_item),
                    "state": queue_item.get("state") or "pending",
                    "summary": "消息已排队，将在当前运行完成后自动发送。",
                },
            )

            async def queued_event_generator():
                yield format_ndjson(event)

            return StreamingResponse(
                queued_event_generator(),
                media_type="application/x-ndjson",
            )
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
    session_id = _resolve_request_session_id(request)

    run_id = request.resume_run_id or f"run_{uuid.uuid4().hex}"
    client_message_id = request.client_message_id or (getattr(request.data, "client_message_id", None) if request.data else None)
    user_message = None
    execution_request = request
    if not request.resume_run_id:
        active_run = _find_active_chat_run(session_id)
        if active_run:
            content = _latest_user_content_from_request(request)
            if not content and not (request.attachments or request.fileUrls):
                raise HTTPException(status_code=400, detail="运行中队列消息必须包含文本或附件。")
            queue_item = _existing_queue_item_for_client_message(session_id, client_message_id)
            if not queue_item:
                queue_item = db.add_chat_user_message_queue_item(
                    queue_id=f"queued_{uuid.uuid4().hex}",
                    session_id=session_id,
                    run_id=str(active_run.get("id") or ""),
                    client_message_id=client_message_id,
                    content=content or "已上传附件",
                    attachments=[item.model_dump(mode="json", by_alias=True) for item in list(request.attachments or [])],
                    file_urls=list(request.fileUrls or []),
                    request_payload=request.model_dump(mode="json", by_alias=True),
                    metadata={
                        "source": "chat_submit_while_run_active",
                        "activeRunStatus": active_run.get("status"),
                    },
                )
            event_payload = {
                "queueMessage": _queue_item_payload(queue_item),
                "state": queue_item.get("state") or "pending",
                "summary": "消息已排队，将在当前运行完成后自动发送。",
            }
            _emit_human_guidance_event(
                "human_guidance.queued",
                session_id=session_id,
                run_id=str(active_run.get("id") or ""),
                payload=event_payload,
            )
            return {
                "accepted": True,
                "queued": True,
                "session_id": session_id,
                "conversationId": session_id,
                "clientMessageId": client_message_id,
                "run_id": active_run.get("id"),
                "runId": active_run.get("id"),
                "queuedMessage": to_jsonable(event_payload["queueMessage"]),
                "userMessage": None,
            }
    if not request.resume_run_id:
        try:
            chat_run = chat_runtime.prepare_run_context(
                request,
                transport="submit",
                run_id=run_id,
                build_engineering_context=False,
            )
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
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": client_message_id,
        "run_id": run_id,
        "runId": run_id,
        "userMessage": to_jsonable(user_message) if user_message else None,
    }


@router.patch("/chat/queued-messages/{queue_id}")
async def update_queued_message(queue_id: str, request: Request):
    payload = await request.json()
    content = str((payload or {}).get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="排队消息内容不能为空。")
    item = db.get_chat_user_message_queue_item(queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="排队消息不存在。")
    if str(item.get("state") or "") != "pending":
        raise HTTPException(status_code=409, detail="只有 pending 状态的排队消息可以编辑。")
    updated = db.update_chat_user_message_queue_item(queue_id, content=content)
    if not updated:
        raise HTTPException(status_code=404, detail="排队消息不存在。")
    _emit_human_guidance_event(
        "human_guidance.queued",
        session_id=str(updated.get("session_id") or ""),
        run_id=str(updated.get("run_id") or "") or None,
        payload={
            "queueMessage": _queue_item_payload(updated),
            "state": "pending",
            "summary": "排队消息已更新。",
        },
    )
    return {"ok": True, "queuedMessage": to_jsonable(_queue_item_payload(updated))}


@router.delete("/chat/queued-messages/{queue_id}")
async def cancel_queued_message(queue_id: str):
    item = db.get_chat_user_message_queue_item(queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="排队消息不存在。")
    if str(item.get("state") or "") not in {"pending", "promoted"}:
        raise HTTPException(status_code=409, detail="该排队消息当前状态不能取消。")
    updated = db.update_chat_user_message_queue_item(
        queue_id,
        state="cancelled",
        timestamp_field="cancelled_at",
    )
    _emit_human_guidance_event(
        "human_guidance.cancelled",
        session_id=str(item.get("session_id") or ""),
        run_id=str(item.get("run_id") or "") or None,
        payload={
            "queueMessage": _queue_item_payload(updated or item),
            "state": "cancelled",
            "summary": "排队消息已取消。",
        },
    )
    return {"ok": True, "queuedMessage": to_jsonable(_queue_item_payload(updated or item))}


@router.post("/chat/queued-messages/{queue_id}/promote")
async def promote_queued_message(queue_id: str):
    item = db.get_chat_user_message_queue_item(queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="排队消息不存在。")
    if str(item.get("state") or "") != "pending":
        raise HTTPException(status_code=409, detail="只有 pending 状态的排队消息可以提升为引导。")
    run_id = str(item.get("run_id") or "").strip()
    record = db.get_run_record(run_id) if run_id else None
    if not record or str(record.get("status") or "").strip().lower() not in _ACTIVE_CHAT_STATUSES:
        raise HTTPException(status_code=409, detail="当前没有可注入引导的 active run。")
    updated = db.update_chat_user_message_queue_item(
        queue_id,
        state="promoted",
        timestamp_field="promoted_at",
    )
    queue_payload = _queue_item_payload(updated or item)
    command_service.issue_control_signal(
        run_id,
        command="guidance",
        reason="human_guidance_promoted",
        payload={"queueMessageId": queue_id},
    )
    _emit_human_guidance_event(
        "human_guidance.promoted",
        session_id=str(item.get("session_id") or ""),
        run_id=run_id,
        payload={
            "queueMessage": queue_payload,
            "state": "promoted",
            "summary": "排队消息已提升为运行中引导，将在安全检查点注入。",
        },
    )
    return {"ok": True, "queuedMessage": to_jsonable(queue_payload)}


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
