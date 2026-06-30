from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.client_terminal_broker import (
    create_terminal_session,
    list_terminal_profiles,
    read_terminal_session,
    send_terminal_input,
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
