from __future__ import annotations

import hashlib
import mimetypes
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from core.process_launch import run_windowless
from core.v8_agent_os_paths import RUNTIME_DATA_HOME
from core.workspace_identity import workspace_path_key
from runtimes.creative_media.governed_media import (
    GovernedMediaError,
    governed_ffmpeg_pair,
    governed_media_fingerprint,
    resolve_governed_media_path,
)


PREVIEW_CACHE_VERSION = "canvas-preview-v1"
_PREVIEW_LOCKS_GUARD = threading.Lock()
_PREVIEW_LOCKS: dict[str, threading.Lock] = {}


class CreativeCanvasPreviewError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreativeCanvasPreview:
    path: Path
    media_type: str
    etag: str
    source_fingerprint: str
    generated: bool


def _preview_lock(key: str) -> threading.Lock:
    with _PREVIEW_LOCKS_GUARD:
        return _PREVIEW_LOCKS.setdefault(key, threading.Lock())


def _media_kind(path: Path, declared: str = "") -> str:
    mime = str(declared or mimetypes.guess_type(path.name)[0] or "").lower()
    suffix = path.suffix.lower()
    if suffix == ".psd" or "photoshop" in mime:
        return "psd"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if suffix in {".glb", ".gltf", ".obj", ".fbx", ".stl", ".usd", ".usdz"}:
        return "model_3d"
    return "file"


def _run_ffmpeg(command: list[str], *, timeout: int) -> None:
    try:
        completed = run_windowless(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CreativeCanvasPreviewError(f"Canvas preview generation failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip().splitlines()
        raise CreativeCanvasPreviewError((detail[-1] if detail else "ffmpeg failed")[-360:])


def _render_image(source: Path, target: Path) -> None:
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((1280, 960), Image.Resampling.LANCZOS)
            image.save(target, format="WEBP", quality=82, method=4)
    except (OSError, ValueError) as exc:
        raise CreativeCanvasPreviewError("Canvas image preview could not be decoded") from exc


def _render_psd(source: Path, target: Path) -> None:
    from core.tools.native.creative_media_psd import render_psd_preview_image

    try:
        image = render_psd_preview_image(source).convert("RGB")
        image.thumbnail((1280, 960), Image.Resampling.LANCZOS)
        image.save(target, format="WEBP", quality=82, method=4)
    except (OSError, ValueError) as exc:
        raise CreativeCanvasPreviewError("Canvas PSD preview could not be rendered") from exc


def _atomic_target(target: Path) -> Path:
    return target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp{target.suffix}")


def resolve_canvas_preview(request: dict[str, Any]) -> CreativeCanvasPreview:
    try:
        source, resource = resolve_governed_media_path(request)
    except (FileNotFoundError, PermissionError, GovernedMediaError, ValueError) as exc:
        raise CreativeCanvasPreviewError(str(exc)) from exc
    fingerprint = governed_media_fingerprint(source)
    kind = _media_kind(source, str(request.get("mimeType") or ""))
    if kind == "model_3d":
        raise CreativeCanvasPreviewError("3D proxy preview requires the configured Godot adapter")
    if kind == "file" or source.suffix.lower() == ".svg":
        return CreativeCanvasPreview(
            path=source,
            media_type=str(mimetypes.guess_type(source.name)[0] or "application/octet-stream"),
            etag=f'"{fingerprint}"',
            source_fingerprint=fingerprint,
            generated=False,
        )

    workspace_hint = str(request.get("workspaceKey") or "").strip()
    workspace_identity = workspace_hint or workspace_path_key(str(source.parent)) or "workspace"
    workspace_partition = hashlib.sha256(workspace_identity.encode("utf-8", errors="ignore")).hexdigest()[:24]
    cache_root = RUNTIME_DATA_HOME / "cache" / "canvas-previews" / workspace_partition
    cache_root.mkdir(parents=True, exist_ok=True)
    extension = ".webp" if kind in {"image", "psd"} else ".mp4" if kind == "video" else ".mp3"
    cache_key = f"{PREVIEW_CACHE_VERSION}-{kind}-{fingerprint}"
    target = cache_root / f"{cache_key}{extension}"
    media_type = "image/webp" if extension == ".webp" else "video/mp4" if extension == ".mp4" else "audio/mpeg"
    lock = _preview_lock(cache_key)
    with lock:
        if target.is_file() and target.stat().st_size > 0:
            return CreativeCanvasPreview(target, media_type, f'"{cache_key}"', fingerprint, True)
        temporary = _atomic_target(target)
        try:
            if kind == "image":
                _render_image(source, temporary)
            elif kind == "psd":
                _render_psd(source, temporary)
            else:
                ffmpeg, _ffprobe, _version = governed_ffmpeg_pair()
                if kind == "video":
                    _run_ffmpeg([
                        ffmpeg,
                        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-i", str(source), "-map", "0:v:0", "-t", "8",
                        "-vf", "fps=12,scale=trunc(min(960\\,iw)/2)*2:-2",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
                    ], timeout=180)
                else:
                    _run_ffmpeg([
                        ffmpeg,
                        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-i", str(source), "-map", "0:a:0", "-t", "60",
                        "-vn", "-c:a", "libmp3lame", "-b:a", "96k", str(temporary),
                    ], timeout=180)
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise CreativeCanvasPreviewError("Canvas preview generator produced no output")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
    return CreativeCanvasPreview(target, media_type, f'"{cache_key}"', fingerprint, True)
