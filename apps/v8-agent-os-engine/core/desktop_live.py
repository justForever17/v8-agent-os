from __future__ import annotations

import asyncio
import io
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

try:
    import mss
except Exception:  # pragma: no cover - optional runtime dependency
    mss = None

try:
    import numpy as np
except Exception:  # pragma: no cover - optional runtime dependency
    np = None

try:
    from av import VideoFrame
except Exception:  # pragma: no cover - optional runtime dependency
    VideoFrame = None

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from aiortc.sdp import candidate_from_sdp
except Exception as exc:  # pragma: no cover - optional runtime dependency
    RTCPeerConnection = None
    RTCSessionDescription = None
    VideoStreamTrack = object  # type: ignore[assignment]
    candidate_from_sdp = None
    _WEBRTC_IMPORT_ERROR = str(exc)
else:
    _WEBRTC_IMPORT_ERROR = ""

from PIL import Image

from core.system_base import get_desktop_live_config
from runtimes.computer_use.runtime import computer_use_runtime


LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)


@dataclass
class DesktopLiveSession:
    id: str
    viewer_id: str
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    activated_at: float | None = None
    connection_state: str = "pending"
    pc: Any | None = None
    track: Any | None = None


class DesktopCaptureVideoTrack(VideoStreamTrack):  # type: ignore[misc]
    kind = "video"

    def __init__(self, service: "DesktopLiveService", session_id: str, *, max_width: int, max_height: int, target_fps: int):
        super().__init__()
        self._service = service
        self._session_id = session_id
        self._max_width = max_width
        self._max_height = max_height
        self._frame_interval = 1.0 / max(1, target_fps)
        self._next_capture_at: float | None = None

    async def recv(self):  # pragma: no cover - exercised via runtime WebRTC session
        if VideoFrame is None or np is None:
            raise RuntimeError("桌面直播缺少视频帧依赖。")

        pts, time_base = await self.next_timestamp()
        now = time.perf_counter()
        if self._next_capture_at is None:
            self._next_capture_at = now
        else:
            self._next_capture_at = max(self._next_capture_at + self._frame_interval, now)
            delay = self._next_capture_at - now
            if delay > 0:
                await asyncio.sleep(delay)

        rgb_frame = await asyncio.to_thread(
            self._service.capture_video_array,
            self._session_id,
            self._max_width,
            self._max_height,
        )
        frame = VideoFrame.from_ndarray(rgb_frame, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


class DesktopLiveService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, DesktopLiveSession] = {}
        self._active_session_id: str | None = None
        self._jpeg_quality = 68
        self._availability_cache: dict[str, Any] | None = None
        self._availability_cached_at = 0.0
        self._availability_ttl_seconds = 3.0

    def _config(self) -> dict[str, Any]:
        return get_desktop_live_config()

    def _cleanup_expired_sessions(self) -> None:
        config = self._config()
        expire_before = time.time() - max(5, int(config.get("idleReleaseSeconds") or 15))
        stale_ids: list[str] = []
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.last_seen_at >= expire_before:
                    continue
                stale_ids.append(session_id)
        if not stale_ids:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            for session_id in stale_ids:
                loop.create_task(self.delete_session(session_id))
        else:
            for session_id in stale_ids:
                self.delete_session_sync(session_id)

    def _is_webrtc_supported(self) -> tuple[bool, str | None]:
        if RTCPeerConnection is None or RTCSessionDescription is None or candidate_from_sdp is None:
            return False, _WEBRTC_IMPORT_ERROR or "未安装 aiortc"
        if VideoFrame is None:
            return False, "未安装 av"
        if np is None:
            return False, "未安装 numpy"
        return True, None

    def _availability(self) -> dict[str, Any]:
        now = time.time()
        if self._availability_cache and (now - self._availability_cached_at) < self._availability_ttl_seconds:
            return dict(self._availability_cache)

        config = self._config()
        if not config.get("enabled", True):
            payload = {
                "available": False,
                "reason": "系统基础配置已关闭桌面直播。",
                "source": "systemBase.desktopLive",
                "webrtcAvailable": False,
                "fallbackAvailable": False,
            }
            self._availability_cache = payload
            self._availability_cached_at = now
            return dict(payload)

        webrtc_available, webrtc_reason = self._is_webrtc_supported()
        try:
            availability = computer_use_runtime.availability()
        except Exception as exc:  # pragma: no cover - defensive
            payload = {
                "available": False,
                "reason": str(exc),
                "source": "computer_use_runtime",
                "webrtcAvailable": webrtc_available,
                "fallbackAvailable": False,
            }
            self._availability_cache = payload
            self._availability_cached_at = now
            return dict(payload)

        details = availability.get("details") or {}
        fallback_available = bool(availability.get("available"))
        available = bool(availability.get("available")) and webrtc_available
        reason = None
        if not availability.get("available"):
            reason = "桌面采集驱动当前不可用"
        elif not webrtc_available:
            reason = f"桌面直播缺少 WebRTC 依赖：{webrtc_reason}"

        payload = {
            "available": available,
            "reason": reason,
            "source": "computer_use_runtime",
            "platform": availability.get("platform"),
            "backend": availability.get("backend"),
            "requires": availability.get("requires") or [],
            "webrtcAvailable": webrtc_available,
            "fallbackAvailable": fallback_available,
            "details": {
                "driver": details.get("driver"),
                "visionFallback": details.get("visionFallback"),
            },
        }
        self._availability_cache = payload
        self._availability_cached_at = now
        return dict(payload)

    def get_status(self) -> dict[str, Any]:
        self._cleanup_expired_sessions()
        availability = self._availability()
        config = self._config()
        with self._lock:
            active = self._sessions.get(self._active_session_id or "")
            occupied = bool(active)
            return {
                **availability,
                "mode": "webrtc_bridge",
                "fallbackMode": "multipart_jpeg_stream",
                "captureSurface": "primary_display",
                "activeSessionId": active.id if active else None,
                "viewerCount": 1 if active else 0,
                "singleViewer": bool(config.get("singleViewerOnly", True)),
                "bridgeActive": occupied and active.connection_state in {"connecting", "connected"},
                "config": {
                    "enabled": bool(config.get("enabled", True)),
                    "maxWidth": int(config.get("maxWidth") or 960),
                    "maxHeight": int(config.get("maxHeight") or 540),
                    "targetFps": int(config.get("targetFps") or 10),
                    "idleReleaseSeconds": int(config.get("idleReleaseSeconds") or 15),
                    "captureDisplay": str(config.get("captureDisplay") or "primary"),
                },
            }

    def get_observation_context(self) -> dict[str, Any]:
        self._cleanup_expired_sessions()
        with self._lock:
            active = self._sessions.get(self._active_session_id or "")
            if not active:
                return {
                    "source": "computer_use_local_capture",
                    "sessionId": None,
                    "frameTimestamp": None,
                    "frameArtifactId": None,
                    "frameRef": None,
                }
            frame_time = active.last_seen_at or active.activated_at or active.created_at
            frame_ref = f"desktop-live:{active.id}:{int(frame_time * 1000)}"
            return {
                "source": "desktop_live",
                "sessionId": active.id,
                "frameTimestamp": datetime.fromtimestamp(frame_time, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "frameArtifactId": None,
                "frameRef": frame_ref,
                "connectionState": active.connection_state,
            }

    def create_session(self, viewer_id: str) -> dict[str, Any]:
        self._cleanup_expired_sessions()
        availability = self._availability()
        if not availability.get("available"):
            raise RuntimeError(str(availability.get("reason") or "当前桌面直播不可用"))

        config = self._config()
        with self._lock:
            active = self._sessions.get(self._active_session_id or "")
            if active:
                if bool(config.get("singleViewerOnly", True)) and active.viewer_id != viewer_id:
                    raise RuntimeError("当前已有其他观看会话，桌面直播当前只支持单观看者。")
                active.last_seen_at = time.time()
                return {
                    "sessionId": active.id,
                    "viewerCount": 1,
                    "mode": "webrtc_bridge",
                }

            session_id = f"desktop-live-{uuid.uuid4().hex[:12]}"
            session = DesktopLiveSession(id=session_id, viewer_id=viewer_id)
            self._sessions[session_id] = session
            self._active_session_id = session_id
            return {
                "sessionId": session_id,
                "viewerCount": 1,
                "mode": "webrtc_bridge",
            }

    def touch_session(self, session_id: str) -> DesktopLiveSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise RuntimeError("桌面直播会话不存在或已失效。")
            if self._active_session_id != session_id:
                raise RuntimeError("当前桌面直播会话未处于活动状态。")
            session.last_seen_at = time.time()
            return session

    def _load_capture_image(self) -> Image.Image:
        driver = getattr(computer_use_runtime, "driver", None)
        if driver is None or not getattr(driver, "is_available", lambda: False)():
            raise RuntimeError("桌面采集驱动当前不可用。")

        if mss is not None:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                return Image.frombytes("RGB", shot.size, shot.rgb)

        fd, temp_path = tempfile.mkstemp(prefix="v8chat-desktop-live-", suffix=".png")
        os.close(fd)
        try:
            driver.capture_screenshot(temp_path)
            with Image.open(temp_path) as file:
                return file.convert("RGB")
        finally:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass

    def _fit_image(self, image: Image.Image, max_width: int, max_height: int) -> Image.Image:
        width, height = image.size
        if width <= max_width and height <= max_height:
            return image.convert("RGB")
        scale = min(max_width / max(width, 1), max_height / max(height, 1))
        target_width = max(1, int(width * scale))
        target_height = max(1, int(height * scale))
        return image.resize((target_width, target_height), LANCZOS).convert("RGB")

    def _encode_stream_frame(self, image: Image.Image, max_width: int, max_height: int) -> bytes:
        encoded = self._fit_image(image, max_width, max_height)
        buffer = io.BytesIO()
        encoded.save(buffer, format="JPEG", quality=self._jpeg_quality, optimize=True)
        return buffer.getvalue()

    def capture_frame(self, session_id: str) -> bytes:
        self.touch_session(session_id)
        config = self._config()
        image = self._load_capture_image()
        return self._encode_stream_frame(
            image,
            int(config.get("maxWidth") or 960),
            int(config.get("maxHeight") or 540),
        )

    def capture_video_array(self, session_id: str, max_width: int, max_height: int):
        self.touch_session(session_id)
        if np is None:
            raise RuntimeError("桌面直播缺少 numpy 依赖。")
        image = self._fit_image(self._load_capture_image(), max_width, max_height)
        return np.asarray(image.convert("RGB"))

    async def create_webrtc_answer(self, session_id: str, viewer_id: str, offer_sdp: str, offer_type: str = "offer") -> dict[str, Any]:
        self._cleanup_expired_sessions()
        availability = self._availability()
        if not availability.get("available"):
            raise RuntimeError(str(availability.get("reason") or "当前桌面直播不可用"))

        config = self._config()
        max_width = int(config.get("maxWidth") or 960)
        max_height = int(config.get("maxHeight") or 540)
        target_fps = int(config.get("targetFps") or 10)

        session = self.touch_session(session_id)
        if bool(config.get("singleViewerOnly", True)) and session.viewer_id != viewer_id:
            raise RuntimeError("当前桌面直播会话不属于当前用户。")

        pc = RTCPeerConnection()
        track = DesktopCaptureVideoTrack(
            self,
            session_id,
            max_width=max_width,
            max_height=max_height,
            target_fps=target_fps,
        )

        with self._lock:
            session.pc = pc
            session.track = track
            session.connection_state = "connecting"
            session.activated_at = time.time()
            session.last_seen_at = time.time()

        @pc.on("connectionstatechange")
        async def _on_connection_state_change():  # pragma: no cover - runtime callback
            with self._lock:
                current = self._sessions.get(session_id)
                if current:
                    current.connection_state = pc.connectionState
                    current.last_seen_at = time.time()
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await self.delete_session(session_id)

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
            pc.addTrack(track)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await self._wait_for_ice_gathering_complete(pc)
            with self._lock:
                current = self._sessions.get(session_id)
                if current:
                    current.connection_state = pc.connectionState or "connecting"
                    current.last_seen_at = time.time()
            local = pc.localDescription
            if local is None:
                raise RuntimeError("桌面直播未能生成 WebRTC answer")
            return {
                "sessionId": session_id,
                "type": local.type,
                "sdp": local.sdp,
            }
        except Exception:
            await self.delete_session(session_id)
            raise

    async def add_webrtc_candidate(self, session_id: str, candidate_payload: dict[str, Any] | None) -> dict[str, Any]:
        session = self.touch_session(session_id)
        pc = session.pc
        if pc is None:
            raise RuntimeError("桌面直播会话尚未完成 WebRTC 初始化。")

        if not candidate_payload or not candidate_payload.get("candidate"):
            await pc.addIceCandidate(None)
            return {"success": True, "endOfCandidates": True}

        if candidate_from_sdp is None:
            raise RuntimeError("桌面直播缺少 aiortc candidate 解析能力。")

        candidate_value = str(candidate_payload.get("candidate") or "")
        if candidate_value.startswith("candidate:"):
            candidate_value = candidate_value.split(":", 1)[1]
        candidate = candidate_from_sdp(candidate_value)
        candidate.sdpMid = candidate_payload.get("sdpMid")
        candidate.sdpMLineIndex = candidate_payload.get("sdpMLineIndex")
        username_fragment = candidate_payload.get("usernameFragment")
        if username_fragment is not None:
            candidate.usernameFragment = username_fragment
        await pc.addIceCandidate(candidate)
        self.touch_session(session_id)
        return {"success": True}

    async def _wait_for_ice_gathering_complete(self, pc: Any) -> None:
        if getattr(pc, "iceGatheringState", "") == "complete":
            return
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()

        @pc.on("icegatheringstatechange")
        def _on_ice_state_change():  # pragma: no cover - runtime callback
            if getattr(pc, "iceGatheringState", "") == "complete" and not waiter.done():
                waiter.set_result(None)

        try:
            await asyncio.wait_for(waiter, timeout=3.0)
        except asyncio.TimeoutError:
            pass

    async def delete_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if self._active_session_id == session_id:
                self._active_session_id = None
        if not session:
            return False
        track = session.track
        pc = session.pc
        if track is not None:
            try:
                track.stop()
            except Exception:
                pass
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass
        return True

    def delete_session_sync(self, session_id: str) -> bool:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(self.delete_session(session_id))
            return True

        with self._lock:
            session = self._sessions.pop(session_id, None)
            if self._active_session_id == session_id:
                self._active_session_id = None
        if not session:
            return False
        track = session.track
        if track is not None:
            try:
                track.stop()
            except Exception:
                pass
        pc = session.pc
        if pc is not None:
            try:
                asyncio.run(pc.close())
            except Exception:
                pass
        return True


desktop_live_service = DesktopLiveService()
