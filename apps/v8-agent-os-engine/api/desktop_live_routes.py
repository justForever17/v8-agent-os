import asyncio
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.desktop_live import desktop_live_service


router = APIRouter()


class DesktopLiveCreatePayload(BaseModel):
    viewer_id: str | None = None


class DesktopLiveOfferPayload(BaseModel):
    session_id: str
    sdp: str | None = None
    type: str = "offer"
    viewer_id: str | None = None


class DesktopLiveCandidatePayload(BaseModel):
    session_id: str
    candidate: dict[str, Any] | None = None


async def _frame_stream(session_id: str) -> AsyncIterator[bytes]:
    boundary = b"--frame\r\n"
    while True:
        try:
            frame = await asyncio.to_thread(desktop_live_service.capture_frame, session_id)
        except Exception as exc:
            yield boundary
            payload = f"Content-Type: application/json\r\n\r\n{{\"error\":\"{str(exc)}\"}}\r\n".encode("utf-8", errors="replace")
            yield payload
            break

        yield boundary
        yield b"Content-Type: image/jpeg\r\n"
        yield f"Content-Length: {len(frame)}\r\n\r\n".encode("utf-8")
        yield frame
        yield b"\r\n"
        await asyncio.sleep(0.333)


@router.get("/desktop-live/status")
async def get_desktop_live_status():
    return desktop_live_service.get_status()


@router.post("/desktop-live/session")
async def create_desktop_live_session(payload: DesktopLiveCreatePayload):
    try:
        return desktop_live_service.create_session(payload.viewer_id or "anonymous")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/desktop-live/offer")
async def create_desktop_live_offer(payload: DesktopLiveOfferPayload):
    if not payload.sdp:
        raise HTTPException(status_code=400, detail="缺少 WebRTC offer SDP。")
    try:
        return await desktop_live_service.create_webrtc_answer(
            payload.session_id,
            payload.viewer_id or "anonymous",
            payload.sdp,
            payload.type or "offer",
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/desktop-live/candidate")
async def create_desktop_live_candidate(payload: DesktopLiveCandidatePayload):
    try:
        return await desktop_live_service.add_webrtc_candidate(payload.session_id, payload.candidate)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/desktop-live/stream")
async def stream_desktop_live(session_id: str = Query(..., alias="sessionId")):
    try:
        desktop_live_service.touch_session(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return StreamingResponse(
        _frame_stream(session_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.delete("/desktop-live/session/{session_id}")
async def delete_desktop_live_session(session_id: str):
    return {"success": await desktop_live_service.delete_session(session_id)}
