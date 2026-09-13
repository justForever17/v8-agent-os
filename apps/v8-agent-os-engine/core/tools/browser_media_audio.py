"""Persist scoped browser audio and optionally reuse the configured STT owner."""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import uuid


async def attach_browser_audio(result: dict, context: dict, *, transcribe: bool = False) -> None:
    audio = result.get("audio") or {}
    if audio.get("status") != "captured":
        return
    from core.artifact_store import artifact_store
    from core.v8_agent_os_paths import RUNTIME_DATA_HOME
    from core.workspace_capability import build_workspace_binding

    data = base64.b64decode(audio.pop("data", ""), validate=True)
    if not data or len(data) > 2 * 1024 * 1024 or audio.get("mimeType") not in {"audio/webm", "audio/webm;codecs=opus"}:
        raise ValueError("invalid_browser_audio")
    directory = RUNTIME_DATA_HOME / "browser" / "audio"
    directory.mkdir(parents=True, exist_ok=True)
    # .webm is commonly inferred as video/webm; .weba keeps the existing media
    # analyzer/artifact MIME owner on its audio path without an override.
    path = directory / f"{uuid.uuid4().hex}.weba"
    binding = build_workspace_binding(context)
    path.write_bytes(data)
    try:
        artifact = artifact_store.record_local_file(
            file_path=path, session_id=context["session_id"], run_id=context.get("run_id"), auto_attach_to_message=False,
            metadata={"workspacePath": str(binding.active_workspace_root), "browserSessionId": result.get("browserSessionId"),
                      "pageId": result.get("pageId"), "purpose": "browser_media_audio",
                      "mediaStartTime": audio["startTime"], "mediaEndTime": audio["endTime"]},
            source_component="browser_broker", node="browser_media",
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    audio["artifactRef"] = {"artifactId": artifact["artifactId"], "filePath": str(path), "contentUrl": artifact.get("contentUrl")}
    if transcribe:
        audio["transcript"] = await transcribe_browser_clip(data, audio["startTime"], audio["endTime"])
    else:
        audio["transcript"] = {"status": "not_requested"}


async def transcribe_browser_clip(data: bytes, start_time: float, end_time: float) -> dict:
    from core.audio.stt_provider import MockSTTProvider, ModelRefSTTProvider, STTManager

    provider = STTManager.get_provider()
    # Do not turn a fallback configuration notice into a purported transcript.
    if isinstance(provider, (MockSTTProvider, ModelRefSTTProvider)):
        return {"status": "unavailable", "reason": "configured_stt_unavailable"}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"status": "unavailable", "reason": "audio_conversion_requires_ffmpeg"}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000",
            "-t", "11", "-f", "wav", "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        wav, _ = await asyncio.wait_for(process.communicate(data), timeout=15)
        if process.returncode or len(wav) <= 44:
            return {"status": "unavailable", "reason": "audio_conversion_failed"}
        text = await asyncio.wait_for(provider.transcribe(wav, "wav"), timeout=30)
        if not str(text or "").strip():
            return {"status": "unavailable", "reason": "stt_no_text"}
        # The existing STT contract returns plain text. Clip-level attribution is
        # real; word timings or subtitle-level synchronization would be invented.
        return {"status": "transcribed", "text": str(text), "startTime": start_time, "endTime": end_time,
                "alignment": "clip_only", "wordTimestampsAvailable": False, "inputTrust": "untrusted_transcript"}
    except asyncio.TimeoutError:
        return {"status": "unavailable", "reason": "stt_or_conversion_timeout"}
    except Exception:
        return {"status": "unavailable", "reason": "stt_or_conversion_failed"}
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
