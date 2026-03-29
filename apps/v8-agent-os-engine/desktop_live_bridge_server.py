from __future__ import annotations

import asyncio
import sys
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.desktop_live import desktop_live_service
from core.system_base import get_bridge_config, get_internal_secret


router = APIRouter(prefix="/v1")


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


def _require_internal_secret(x_v8_agent_os_secret: str | None = Header(default=None)) -> None:
    expected = get_internal_secret()
    if not expected or x_v8_agent_os_secret != expected:
        raise HTTPException(status_code=401, detail="未授权的桌面直播 bridge 请求。")


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


@router.get("/desktop-live/status", dependencies=[Depends(_require_internal_secret)])
async def get_desktop_live_status():
    payload = desktop_live_service.get_status()
    payload["bridgeLayer"] = "python_local_webrtc_bridge"
    payload["bridgeExecutable"] = sys.executable
    return payload


@router.post("/desktop-live/session", dependencies=[Depends(_require_internal_secret)])
async def create_desktop_live_session(payload: DesktopLiveCreatePayload):
    try:
        return desktop_live_service.create_session(payload.viewer_id or "anonymous")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/desktop-live/offer", dependencies=[Depends(_require_internal_secret)])
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


@router.post("/desktop-live/candidate", dependencies=[Depends(_require_internal_secret)])
async def create_desktop_live_candidate(payload: DesktopLiveCandidatePayload):
    try:
        return await desktop_live_service.add_webrtc_candidate(payload.session_id, payload.candidate)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/desktop-live/stream", dependencies=[Depends(_require_internal_secret)])
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
        },
    )


@router.delete("/desktop-live/session/{session_id}", dependencies=[Depends(_require_internal_secret)])
async def delete_desktop_live_session(session_id: str):
    return {"success": await desktop_live_service.delete_session(session_id)}


app = FastAPI(title="V8 Agent OS Desktop Live Bridge")
app.include_router(router)


def _resolve_host_port() -> tuple[str, int]:
    bridge_url = str(get_bridge_config().get("desktopLiveBridgeBaseUrl") or "http://127.0.0.1:8011/v1")
    parsed = urlparse(bridge_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8011)
    return host, port


if __name__ == "__main__":
    host, port = _resolve_host_port()
    uvicorn.run(app, host=host, port=port, reload=False, log_level="warning")
