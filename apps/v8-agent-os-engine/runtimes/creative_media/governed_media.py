from __future__ import annotations

import hashlib
import json
import math
import subprocess
import threading
from collections import OrderedDict
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from core.dependency_registry import build_dependency_status
from core.process_launch import run_windowless
from core.workspace_media_library import workspace_media_library


MAX_TIMELINE_UNITS = 300_000
MAX_PROBE_CACHE_ENTRIES = 16
_PROBE_CACHE: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_INFLIGHT: dict[tuple[str, str], threading.Event] = {}


class GovernedMediaError(ValueError):
    pass


def _fraction(value: Any, *, fallback: Fraction = Fraction(0, 1)) -> Fraction:
    raw = str(value or "").strip()
    if not raw or raw in {"N/A", "0/0"}:
        return fallback
    try:
        return Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return fallback


def _decimal(value: Fraction, digits: int = 9) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    rendered = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    return rendered or "0"


def _rational_payload(value: Fraction) -> dict[str, Any]:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "value": f"{value.numerator}/{value.denominator}",
    }


def _ffmpeg_pair() -> tuple[str, str, str]:
    ffmpeg = next(
        (item for item in build_dependency_status() if str(item.get("id") or "") == "ffmpeg"),
        {},
    )
    detection = dict(ffmpeg.get("detection") or {})
    if not detection.get("detected"):
        raise GovernedMediaError(
            "Governed media editing requires a paired FFmpeg/FFprobe 7.0 or newer installation"
        )
    return (
        str(detection.get("path") or ""),
        str(detection.get("ffprobePath") or ""),
        str(detection.get("version") or ""),
    )


def _run_json(command: list[str], *, timeout: int = 180) -> dict[str, Any]:
    try:
        result = run_windowless(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GovernedMediaError(f"Governed media probe failed: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffprobe failed").strip().splitlines()
        raise GovernedMediaError((detail[-1] if detail else "ffprobe failed")[-360:])
    try:
        payload = json.loads(result.stdout or "{}")
    except (TypeError, ValueError) as exc:
        raise GovernedMediaError("Governed media probe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise GovernedMediaError("Governed media probe returned an invalid payload")
    return payload


def _resource_path(request: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    session_id = str(request.get("sessionId") or request.get("session_id") or "").strip()
    source_id = str(request.get("sourceId") or "").strip()
    artifact_id = str(request.get("artifactId") or "").strip()
    workspace_asset_id = str(request.get("workspaceAssetId") or "").strip()
    selected = [value for value in (source_id, artifact_id, workspace_asset_id) if value]
    if not session_id:
        raise GovernedMediaError("Governed media operations require a current session")
    if len(selected) != 1:
        raise GovernedMediaError("Governed media operations require exactly one source, artifact, or workspace asset")
    if source_id:
        return workspace_media_library.resolve_source_path(session_id=session_id, source_id=source_id), {
            "kind": "source",
            "id": source_id,
        }
    if artifact_id:
        return workspace_media_library.resolve_artifact_path(session_id=session_id, artifact_id=artifact_id), {
            "kind": "artifact",
            "id": artifact_id,
        }
    return workspace_media_library.resolve_asset_path(
        session_id=session_id,
        asset_id=workspace_asset_id,
        require_session_use=bool(request.get("requireSessionUse", True)),
    ), {"kind": "workspace_asset", "id": workspace_asset_id}


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(stat.st_mtime_ns).encode("ascii"))
    digest.update(b"\0")
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            handle.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return f"v8mf_{digest.hexdigest()}"


def governed_ffmpeg_pair() -> tuple[str, str, str]:
    return _ffmpeg_pair()


def governed_media_fingerprint(path: Path) -> str:
    return _fingerprint(path)


def resolve_governed_media_path(request: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    return _resource_path(request)


def _stream_header(ffprobe: str, path: Path) -> dict[str, Any]:
    return _run_json([
        ffprobe,
        "-v", "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,time_base,avg_frame_rate,r_frame_rate,nb_frames,duration_ts,duration,sample_rate,channels:format=duration,format_name,size",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ])


def _video_timeline(ffprobe: str, path: Path, stream: dict[str, Any]) -> dict[str, Any]:
    time_base = _fraction(stream.get("time_base"))
    if time_base <= 0:
        raise GovernedMediaError("Video stream has no usable time base")
    avg_rate = _fraction(stream.get("avg_frame_rate"))
    nominal_rate = _fraction(stream.get("r_frame_rate"))
    declared_count = int(stream.get("nb_frames") or 0) if str(stream.get("nb_frames") or "").isdigit() else 0
    duration = _fraction(stream.get("duration"))
    estimated_count = declared_count or (
        math.ceil(float(duration * (avg_rate or nominal_rate))) if duration > 0 and (avg_rate or nominal_rate) > 0 else 0
    )
    if estimated_count > MAX_TIMELINE_UNITS:
        raise GovernedMediaError(
            f"Video timeline exceeds the governed limit of {MAX_TIMELINE_UNITS} frames"
        )
    frame_payload = _run_json([
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_frames",
        "-show_entries", "frame=best_effort_timestamp,pts,pkt_duration,key_frame,pict_type",
        "-of", "json",
        str(path),
    ])
    raw_frames = list(frame_payload.get("frames") or [])
    if not raw_frames:
        raise GovernedMediaError("Video stream contains no decodable frames")
    if len(raw_frames) > MAX_TIMELINE_UNITS:
        raise GovernedMediaError(
            f"Video timeline exceeds the governed limit of {MAX_TIMELINE_UNITS} frames"
        )
    pts_values: list[int] = []
    durations: list[int] = []
    keyframes: list[int] = []
    for index, raw in enumerate(raw_frames):
        frame = dict(raw or {})
        pts_raw = frame.get("best_effort_timestamp")
        if pts_raw in {None, "N/A"}:
            pts_raw = frame.get("pts")
        if pts_raw in {None, "N/A"}:
            if not pts_values:
                pts = 0
            elif durations[-1] > 0:
                pts = pts_values[-1] + durations[-1]
            else:
                raise GovernedMediaError(f"Frame {index} has no usable presentation timestamp")
        else:
            pts = int(pts_raw)
        duration_ticks = int(frame.get("pkt_duration") or 0)
        pts_values.append(pts)
        durations.append(max(0, duration_ticks))
        if int(frame.get("key_frame") or 0) == 1:
            keyframes.append(index)
    first_pts = pts_values[0]
    fallback_duration = 0
    if len(pts_values) > 1:
        positive_deltas = [right - left for left, right in zip(pts_values, pts_values[1:]) if right > left]
        fallback_duration = positive_deltas[len(positive_deltas) // 2] if positive_deltas else 0
    if fallback_duration <= 0 and nominal_rate > 0:
        fallback_duration = max(1, round(float((Fraction(1, 1) / nominal_rate) / time_base)))
    last_duration = durations[-1] or fallback_duration or 1
    boundaries = [pts - first_pts for pts in pts_values]
    boundaries.append(pts_values[-1] - first_pts + last_duration)
    variable = len(set(right - left for left, right in zip(boundaries, boundaries[1:]))) > 1
    return {
        "unit": "frame",
        "count": len(raw_frames),
        "timeBase": _rational_payload(time_base),
        "averageFrameRate": _rational_payload(avg_rate),
        "nominalFrameRate": _rational_payload(nominal_rate),
        "variableFrameRate": variable,
        "boundaryTicks": boundaries,
        "keyframeIndices": keyframes,
        "durationSeconds": _decimal(Fraction(boundaries[-1], 1) * time_base),
        "displayPrecision": min(9, max(3, len(str(time_base.denominator)))),
        "approximate": False,
    }


def _video_preview_timeline(stream: dict[str, Any], format_payload: dict[str, Any]) -> dict[str, Any]:
    time_base = _fraction(stream.get("time_base"))
    if time_base <= 0:
        raise GovernedMediaError("Video stream has no usable time base")
    avg_rate = _fraction(stream.get("avg_frame_rate"))
    nominal_rate = _fraction(stream.get("r_frame_rate"))
    rate = avg_rate or nominal_rate
    duration = _fraction(stream.get("duration"))
    if duration <= 0:
        duration = _fraction(format_payload.get("duration"))
    declared_count = int(stream.get("nb_frames") or 0) if str(stream.get("nb_frames") or "").isdigit() else 0
    estimated_count = declared_count or (math.ceil(float(duration * rate)) if duration > 0 and rate > 0 else 0)
    if estimated_count <= 0 or duration <= 0:
        raise GovernedMediaError("Video stream has no usable preview timeline")
    if estimated_count > MAX_TIMELINE_UNITS:
        raise GovernedMediaError(
            f"Video timeline exceeds the governed limit of {MAX_TIMELINE_UNITS} frames"
        )
    if rate <= 0:
        rate = Fraction(estimated_count, 1) / duration
    return {
        "unit": "frame",
        "count": estimated_count,
        "timeBase": _rational_payload(time_base),
        "averageFrameRate": _rational_payload(rate),
        "nominalFrameRate": _rational_payload(nominal_rate or rate),
        "variableFrameRate": None,
        "durationSeconds": _decimal(duration),
        "displayPrecision": min(9, max(3, len(str(time_base.denominator)))),
        "approximate": True,
    }


def _audio_timeline(stream: dict[str, Any], format_payload: dict[str, Any]) -> dict[str, Any]:
    sample_rate = int(stream.get("sample_rate") or 0)
    if sample_rate <= 0:
        raise GovernedMediaError("Audio stream has no usable sample rate")
    time_base = _fraction(stream.get("time_base"), fallback=Fraction(1, sample_rate))
    duration_ticks = int(stream.get("duration_ts") or 0)
    if duration_ticks > 0 and time_base > 0:
        sample_count = int(Fraction(duration_ticks, 1) * time_base * sample_rate)
    else:
        duration = _fraction(stream.get("duration") or format_payload.get("duration"))
        sample_count = int(duration * sample_rate)
    if sample_count <= 0:
        raise GovernedMediaError("Audio stream has no usable duration")
    return {
        "unit": "sample",
        "count": sample_count,
        "sampleRate": sample_rate,
        "timeBase": _rational_payload(Fraction(1, sample_rate)),
        "durationSeconds": _decimal(Fraction(sample_count, sample_rate)),
        "displayPrecision": min(9, max(5, len(str(sample_rate)))),
        "approximate": False,
    }


def probe_request(request: dict[str, Any]) -> dict[str, Any]:
    _, ffprobe, version = _ffmpeg_pair()
    path, resource = _resource_path(request)
    fingerprint = _fingerprint(path)
    cache_key = (str(path.resolve()), fingerprint)
    preview_only = str(request.get("detail") or "").strip().lower() == "preview"
    wait_for: threading.Event | None = None
    owns_exact_probe = False
    with _PROBE_CACHE_LOCK:
        cached = _PROBE_CACHE.get(cache_key)
        if cached is not None:
            _PROBE_CACHE.move_to_end(cache_key)
            return {**deepcopy(cached), "resource": resource}
        if not preview_only:
            wait_for = _PROBE_INFLIGHT.get(cache_key)
            if wait_for is None:
                wait_for = threading.Event()
                _PROBE_INFLIGHT[cache_key] = wait_for
                owns_exact_probe = True
    if wait_for is not None and not owns_exact_probe:
        if not wait_for.wait(timeout=370):
            raise GovernedMediaError("Timed out waiting for the governed media timeline index")
        with _PROBE_CACHE_LOCK:
            cached = _PROBE_CACHE.get(cache_key)
            if cached is None:
                raise GovernedMediaError("The governed media timeline index could not be created")
            _PROBE_CACHE.move_to_end(cache_key)
            return {**deepcopy(cached), "resource": resource}
    try:
        payload = _stream_header(ffprobe, path)
        streams = [dict(item) for item in list(payload.get("streams") or []) if isinstance(item, dict)]
        video_streams = [item for item in streams if item.get("codec_type") == "video"]
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
        if video_streams:
            selected = video_streams[0]
            timeline = (
                _video_preview_timeline(selected, dict(payload.get("format") or {}))
                if preview_only
                else _video_timeline(ffprobe, path, selected)
            )
            kind = "video"
        elif audio_streams:
            selected = audio_streams[0]
            timeline = _audio_timeline(selected, dict(payload.get("format") or {}))
            kind = "audio"
        else:
            raise GovernedMediaError("Selected resource has no supported audio or video stream")
        result = {
            "schema": "v8.governed_media_probe.v1",
            "fingerprint": fingerprint,
            "kind": kind,
            "timeline": timeline,
            "stream": {
                "index": int(selected.get("index") or 0),
                "codec": str(selected.get("codec_name") or ""),
                "hasAudio": bool(audio_streams),
                "audioStreamIndex": int(audio_streams[0].get("index") or 0) if audio_streams else None,
            },
            "format": {
                "name": str(dict(payload.get("format") or {}).get("format_name") or ""),
                "size": path.stat().st_size,
            },
            "engine": {"ffmpegVersion": version, "timelineAuthority": "ffprobe"},
        }
        if not preview_only or kind == "audio":
            with _PROBE_CACHE_LOCK:
                _PROBE_CACHE[cache_key] = deepcopy(result)
                _PROBE_CACHE.move_to_end(cache_key)
                while len(_PROBE_CACHE) > MAX_PROBE_CACHE_ENTRIES:
                    _PROBE_CACHE.popitem(last=False)
        return {**result, "resource": resource}
    finally:
        if owns_exact_probe:
            with _PROBE_CACHE_LOCK:
                completed = _PROBE_INFLIGHT.pop(cache_key, None)
                if completed is not None:
                    completed.set()


def trim_exact(
    request: dict[str, Any],
    *,
    output_path: Path,
) -> dict[str, Any]:
    ffmpeg, _, version = _ffmpeg_pair()
    source_path, resource = _resource_path(request)
    probe = probe_request(request)
    expected_fingerprint = str(request.get("probeFingerprint") or "").strip()
    if not expected_fingerprint or expected_fingerprint != probe["fingerprint"]:
        raise GovernedMediaError("Media changed after timeline probing; refresh the timeline before trimming")
    timeline = dict(probe.get("timeline") or {})
    operation_kind = str(request.get("operationKind") or "").strip()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command: list[str]
    if operation_kind == "video.extract_frame_exact":
        if timeline.get("unit") != "frame":
            raise GovernedMediaError("video.extract_frame_exact requires a video timeline")
        frame_index = int(request.get("frameIndex"))
        count = int(timeline.get("count") or 0)
        if frame_index < 0 or frame_index >= count:
            raise GovernedMediaError("Frame index is outside the probed video timeline")
        boundaries = [int(value) for value in list(timeline.get("boundaryTicks") or [])]
        time_base_payload = dict(timeline.get("timeBase") or {})
        time_base = Fraction(int(time_base_payload.get("numerator") or 0), int(time_base_payload.get("denominator") or 1))
        frame_seconds = Fraction(boundaries[frame_index], 1) * time_base
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source_path),
            "-vf", f"select=eq(n\\,{frame_index})", "-frames:v", "1", "-c:v", "png", str(output_path),
        ]
        selection = {
            "unit": "frame",
            "frameIndex": frame_index,
            "timeSeconds": _decimal(frame_seconds),
        }
    elif operation_kind == "video.trim_exact":
        if timeline.get("unit") != "frame":
            raise GovernedMediaError("video.trim_exact requires a video timeline")
        start = int(request.get("startFrameIndex"))
        end = int(request.get("endFrameIndexExclusive"))
        count = int(timeline.get("count") or 0)
        if start < 0 or end <= start or end > count:
            raise GovernedMediaError("Frame range is outside the probed video timeline")
        boundaries = [int(value) for value in list(timeline.get("boundaryTicks") or [])]
        time_base_payload = dict(timeline.get("timeBase") or {})
        time_base = Fraction(int(time_base_payload.get("numerator") or 0), int(time_base_payload.get("denominator") or 1))
        start_seconds = Fraction(boundaries[start], 1) * time_base
        end_seconds = Fraction(boundaries[end], 1) * time_base
        filters = [f"[0:v:0]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS[v]"]
        has_audio = bool(dict(probe.get("stream") or {}).get("hasAudio"))
        if has_audio:
            filters.append(
                f"[0:a:0]asetpts=PTS-STARTPTS,atrim=start={_decimal(start_seconds)}:end={_decimal(end_seconds)},asetpts=PTS-STARTPTS[a]"
            )
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source_path),
            "-filter_complex", ";".join(filters), "-map", "[v]",
        ]
        if has_audio:
            command.extend(["-map", "[a]", "-c:a", "aac", "-b:a", "192k"])
        command.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output_path),
        ])
        selection = {
            "unit": "frame",
            "startFrameIndex": start,
            "endFrameIndexExclusive": end,
            "expectedFrameCount": end - start,
            "startSeconds": _decimal(start_seconds),
            "endSeconds": _decimal(end_seconds),
        }
    elif operation_kind == "audio.trim_exact":
        if timeline.get("unit") != "sample":
            raise GovernedMediaError("audio.trim_exact requires an audio timeline")
        start = int(request.get("startSampleIndex"))
        end = int(request.get("endSampleIndexExclusive"))
        count = int(timeline.get("count") or 0)
        if start < 0 or end <= start or end > count:
            raise GovernedMediaError("Sample range is outside the probed audio timeline")
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source_path),
            "-filter_complex", f"[0:a:0]atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS[a]",
            "-map", "[a]", "-c:a", "flac", str(output_path),
        ]
        sample_rate = int(timeline.get("sampleRate") or 0)
        selection = {
            "unit": "sample",
            "startSampleIndex": start,
            "endSampleIndexExclusive": end,
            "expectedSampleCount": end - start,
            "startSeconds": _decimal(Fraction(start, sample_rate)),
            "endSeconds": _decimal(Fraction(end, sample_rate)),
        }
    else:
        raise GovernedMediaError("Unsupported governed local media operation")
    try:
        result = run_windowless(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(
                180,
                int(float(selection.get("endSeconds") or selection.get("timeSeconds") or 0)
                    - float(selection.get("startSeconds") or 0)) * 4 + 60,
            ),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GovernedMediaError(f"Governed media trim failed: {type(exc).__name__}") from exc
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip().splitlines()
        raise GovernedMediaError((detail[-1] if detail else "ffmpeg failed")[-360:])
    output_request = dict(request)
    output_request.pop("sourceId", None)
    output_request.pop("artifactId", None)
    output_request.pop("workspaceAssetId", None)
    # Postflight uses direct path metadata below because the output is not an artifact until this proof passes.
    header = _stream_header(_ffmpeg_pair()[1], output_path)
    output_streams = [dict(item) for item in list(header.get("streams") or []) if isinstance(item, dict)]
    if operation_kind in {"video.extract_frame_exact", "video.trim_exact"}:
        output_video = next((item for item in output_streams if item.get("codec_type") == "video"), None)
        if not output_video:
            raise GovernedMediaError("Governed media output has no video stream")
        output_timeline = _video_timeline(_ffmpeg_pair()[1], output_path, output_video)
        actual_count = int(output_timeline.get("count") or 0)
        expected_count = 1 if operation_kind == "video.extract_frame_exact" else int(selection["expectedFrameCount"])
        if actual_count != expected_count:
            raise GovernedMediaError(
                f"Media postflight frame count mismatch: expected {expected_count}, got {actual_count}"
            )
    else:
        output_audio = next((item for item in output_streams if item.get("codec_type") == "audio"), None)
        if not output_audio:
            raise GovernedMediaError("Trim output has no audio stream")
        output_timeline = _audio_timeline(output_audio, dict(header.get("format") or {}))
        actual_count = int(output_timeline.get("count") or 0)
        if abs(actual_count - int(selection["expectedSampleCount"])) > 1:
            raise GovernedMediaError(
                f"Trim postflight sample count mismatch: expected {selection['expectedSampleCount']}, got {actual_count}"
            )
    return {
        "schema": "v8.governed_media_trim_proof.v1",
        "resource": resource,
        "sourceFingerprint": probe["fingerprint"],
        "selection": selection,
        "output": {
            "byteSize": output_path.stat().st_size,
            "timeline": output_timeline,
        },
        "engine": {
            "ffmpegVersion": version,
            "windowless": True,
            "providerInvoked": False,
        },
    }
