from __future__ import annotations

import asyncio
import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.system_base import get_internal_secret
from core.client_terminal_broker import (
    consume_terminal_ws_ticket,
    consume_terminal_session_output,
    create_terminal_session,
    issue_terminal_ws_ticket,
    list_terminal_sessions,
    list_terminal_profiles,
    read_terminal_session,
    resize_terminal_session,
    send_terminal_input,
    write_terminal_session_input,
    terminate_terminal_session,
    require_terminal_owner,
)


router = APIRouter(prefix="/terminal")


def require_terminal_internal_secret(x_v8_agent_os_secret: str | None = Header(default=None)) -> None:
    expected_secret = get_internal_secret()
    if not expected_secret or not hmac.compare_digest(str(x_v8_agent_os_secret or ""), expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


class CreateTerminalSessionRequest(BaseModel):
    createRequestId: str | None = None
    profileId: str | None = None
    cwd: str | None = None
    conversationId: str | None = None
    workspaceId: str | None = None
    projectId: str | None = None


class TerminalInputRequest(BaseModel):
    inputText: str

class TerminalResizeRequest(BaseModel):
    cols: int
    rows: int


def require_terminal_access(session_id: str, x_v8_agent_os_user_email: str | None = Header(default=None), _auth: None = Depends(require_terminal_internal_secret)) -> None:
    try: require_terminal_owner(session_id, str(x_v8_agent_os_user_email or ""))
    except PermissionError as exc: raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc: raise HTTPException(status_code=404, detail=str(exc))


@router.get("/profiles")
async def get_terminal_profiles(_auth: None = Depends(require_terminal_internal_secret)):
    try:
        return list_terminal_profiles()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions")
async def create_terminal_session_route(
    request: CreateTerminalSessionRequest,
    _auth: None = Depends(require_terminal_internal_secret),
    x_v8_agent_os_user_email: str | None = Header(default=None),
    x_v8_agent_os_user_id: str | None = Header(default=None),
):
    try:
        if not x_v8_agent_os_user_email: raise HTTPException(status_code=401, detail="Terminal user is required")
        if request.conversationId:
            from core.database import db
            conversation = db.get_session(request.conversationId)
            if not conversation: raise HTTPException(status_code=404, detail="Conversation not found")
            owner = str(conversation.get("user_id") or "")
            if owner and owner != "anonymous" and owner not in {x_v8_agent_os_user_id, x_v8_agent_os_user_email}:
                raise HTTPException(status_code=403, detail="Conversation owner mismatch")
        return await asyncio.to_thread(
            create_terminal_session,
            profile_id=request.profileId,
            cwd=request.cwd,
            conversation_id=request.conversationId,
            workspace_id=request.workspaceId,
            project_id=request.projectId,
            create_request_id=request.createRequestId,
            user_email=x_v8_agent_os_user_email,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/sessions")
async def list_terminal_sessions_route(
    conversationId: str | None = Query(default=None),
    _auth: None = Depends(require_terminal_internal_secret),
    x_v8_agent_os_user_email: str | None = Header(default=None),
):
    if not x_v8_agent_os_user_email: raise HTTPException(status_code=401, detail="Terminal user is required")
    return list_terminal_sessions(conversation_id=conversationId, user_email=x_v8_agent_os_user_email)


@router.get("/sessions/{session_id}")
async def read_terminal_session_route(
    session_id: str,
    _auth: None = Depends(require_terminal_access),
    cursor: int = Query(default=0, ge=0),
):
    return read_terminal_session(session_id, cursor)


@router.post("/sessions/{session_id}/ws-ticket")
async def issue_terminal_session_ws_ticket_route(
    session_id: str,
    x_v8_agent_os_secret: str | None = Header(default=None),
    x_v8_agent_os_user_email: str | None = Header(default=None),
    x_v8_terminal_origin: str | None = Header(default=None),
):
    expected_secret = get_internal_secret()
    if not expected_secret or not hmac.compare_digest(str(x_v8_agent_os_secret or ""), expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    user_email = str(x_v8_agent_os_user_email or "").strip()
    if not user_email:
        raise HTTPException(status_code=401, detail="Terminal user is required")
    try:
        return issue_terminal_ws_ticket(session_id, user_email=user_email, origin=str(x_v8_terminal_origin or ""))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        detail = str(exc)
        raise HTTPException(status_code=404 if "not found" in detail.lower() else 400, detail=detail)


@router.post("/sessions/{session_id}/input")
async def send_terminal_input_route(
    session_id: str,
    request: TerminalInputRequest,
    _auth: None = Depends(require_terminal_access),
):
    return send_terminal_input(session_id, request.inputText)


@router.post("/sessions/{session_id}/terminate")
async def terminate_terminal_session_route(
    session_id: str,
    _auth: None = Depends(require_terminal_access),
):
    return terminate_terminal_session(session_id)


@router.post("/sessions/{session_id}/resize")
async def resize_terminal_session_route(session_id: str, request: TerminalResizeRequest, _auth: None = Depends(require_terminal_access)):
    return resize_terminal_session(session_id, cols=request.cols, rows=request.rows)


@router.websocket("/sessions/{session_id}/ws")
async def terminal_session_websocket(websocket: WebSocket, session_id: str):
    ticket_result = consume_terminal_ws_ticket(session_id, websocket.query_params.get("ticket") or "", websocket.headers.get("origin") or "")
    if not ticket_result.get("ok"):
        await websocket.close(code=1008)
        return

    try:
        cursor = max(0, int(websocket.query_params.get("cursor") or 0))
    except ValueError:
        await websocket.close(code=1008)
        return
    await websocket.accept()

    async def send_payload(payload: dict) -> None:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    snapshot = read_terminal_session(session_id, cursor)
    if not snapshot.get("ok"):
        await send_payload({
            "type": "error",
            "message": snapshot.get("error") or snapshot.get("detail") or "Terminal session not found.",
            "session": snapshot,
        })
        await websocket.close()
        return

    if snapshot.get("outputReset"):
        cursor = 0
    snapshot["outputDelta"] = ""
    snapshot["outputCursor"] = cursor
    await send_payload({"type": "snapshot", "session": snapshot})
    acknowledged = asyncio.Event()
    acknowledged.set()
    sent_cursor = cursor

    async def output_loop() -> None:
        nonlocal cursor, sent_cursor
        status_at = 0.0
        while True:
            await acknowledged.wait()
            current = await asyncio.to_thread(consume_terminal_session_output, session_id, cursor)
            if not current.get("ok"):
                await send_payload({
                    "type": "error",
                    "message": current.get("error") or current.get("detail") or "Terminal session is unavailable.",
                    "session": current,
                })
                return
            delta = str(current.get("outputDelta") or "")
            if delta:
                cursor = current["cursor"]
                sent_cursor = cursor
                acknowledged.clear()
                await send_payload({"type": "output", "data": delta, "cursor": cursor, "generation": current["generation"]})
            if current.get("isRunning") is False and not current.get("hasMore"):
                await acknowledged.wait()
                state = await asyncio.to_thread(read_terminal_session, session_id, cursor)
                state["outputDelta"] = ""
                state["outputCursor"] = cursor
                await send_payload({"type": "status", "session": state})
                return
            now = asyncio.get_running_loop().time()
            if now - status_at >= 1:
                status_at = now
                state = await asyncio.to_thread(read_terminal_session, session_id, cursor)
                state["outputDelta"] = ""
                state["outputCursor"] = cursor
                await send_payload({"type": "status", "session": state})
            if not delta: await asyncio.sleep(0.05)

    async def input_loop() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except Exception:
                message = {"type": "input", "data": raw}
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "input")
            if message_type == "ack":
                if int(message.get("cursor") or -1) == sent_cursor: acknowledged.set()
            elif message_type == "input":
                result = await asyncio.to_thread(write_terminal_session_input, session_id, str(message.get("data") or ""))
                if result.get("ok") is False: await send_payload({"type": "error", "message": result.get("error") or "Input failed"})
            elif message_type == "resize":
                resized = resize_terminal_session(
                    session_id,
                    cols=int(message.get("cols") or 80),
                    rows=int(message.get("rows") or 24),
                )
                await send_payload({"type": "snapshot", "session": resized})
            elif message_type == "terminate":
                terminated = await asyncio.to_thread(terminate_terminal_session, session_id)
                await send_payload({"type": "status", "session": terminated})
                return

    output_task = asyncio.create_task(output_loop())
    input_task = asyncio.create_task(input_loop())
    try:
        done, pending = await asyncio.wait({output_task, input_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        output_task.cancel()
        input_task.cancel()
    except Exception as exc:
        output_task.cancel()
        input_task.cancel()
        try:
            await send_payload({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        output_task.cancel(); input_task.cancel()
        await asyncio.gather(output_task, input_task, return_exceptions=True)
