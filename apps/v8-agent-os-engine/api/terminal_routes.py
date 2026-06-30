from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.client_terminal_broker import (
    consume_terminal_session_output,
    create_terminal_session,
    list_terminal_profiles,
    read_terminal_session,
    resize_terminal_session,
    send_terminal_input,
    write_terminal_session_input,
    terminate_terminal_session,
)


router = APIRouter(prefix="/terminal")


class CreateTerminalSessionRequest(BaseModel):
    profileId: str | None = None
    cwd: str | None = None
    conversationId: str | None = None


class TerminalInputRequest(BaseModel):
    inputText: str


@router.get("/profiles")
async def get_terminal_profiles():
    try:
        return list_terminal_profiles()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions")
async def create_terminal_session_route(request: CreateTerminalSessionRequest):
    try:
        return create_terminal_session(
            profile_id=request.profileId,
            cwd=request.cwd,
            conversation_id=request.conversationId,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/sessions/{session_id}")
async def read_terminal_session_route(session_id: str):
    return read_terminal_session(session_id)


@router.post("/sessions/{session_id}/input")
async def send_terminal_input_route(session_id: str, request: TerminalInputRequest):
    return send_terminal_input(session_id, request.inputText)


@router.post("/sessions/{session_id}/terminate")
async def terminate_terminal_session_route(session_id: str):
    return terminate_terminal_session(session_id)


@router.websocket("/sessions/{session_id}/ws")
async def terminal_session_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()

    async def send_payload(payload: dict) -> None:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    snapshot = read_terminal_session(session_id)
    if not snapshot.get("ok"):
        await send_payload({
            "type": "error",
            "message": snapshot.get("error") or snapshot.get("detail") or "Terminal session not found.",
            "session": snapshot,
        })
        await websocket.close()
        return

    await send_payload({"type": "snapshot", "session": snapshot})

    async def output_loop() -> None:
        while True:
            await asyncio.sleep(0.05)
            current = consume_terminal_session_output(session_id)
            if not current.get("ok"):
                await send_payload({
                    "type": "error",
                    "message": current.get("error") or current.get("detail") or "Terminal session is unavailable.",
                    "session": current,
                })
                return
            delta = str(current.get("outputDelta") or "")
            if delta:
                await send_payload({"type": "output", "data": delta})
            if current.get("isRunning") is False:
                await send_payload({"type": "status", "session": current})
                return

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
            if message_type == "input":
                write_terminal_session_input(session_id, str(message.get("data") or ""))
            elif message_type == "resize":
                resized = resize_terminal_session(
                    session_id,
                    cols=int(message.get("cols") or 80),
                    rows=int(message.get("rows") or 24),
                )
                await send_payload({"type": "snapshot", "session": resized})
            elif message_type == "terminate":
                terminated = terminate_terminal_session(session_id)
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
