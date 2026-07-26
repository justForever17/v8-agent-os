from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageStat

from .image_analysis import analyze_image


CommandRunner = Callable[..., Any]
ExecutableResolver = Callable[[str], str | None]

MAX_DIAGNOSTIC_SECONDS = 120.0
BLACK_RE = re.compile(r"black_duration:([0-9.]+)")
FREEZE_RE = re.compile(r"freeze_duration:\s*([0-9.]+)")
SILENCE_RE = re.compile(r"silence_duration:\s*([0-9.]+)")
MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?inf|-?[0-9.]+)\s*dB", re.IGNORECASE)
MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?inf|-?[0-9.]+)\s*dB", re.IGNORECASE)


def _append_once(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rate(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if "/" in text:
        left, right = text.split("/", 1)
        denominator = _float(right)
        return _float(left) / denominator if denominator else 0.0
    return _float(text)


def _db(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"-inf", "inf"}:
        return -math.inf if text == "-inf" else math.inf
    try:
        return float(text)
    except ValueError:
        return None


def _expected_dimensions(request: dict[str, Any]) -> tuple[int, int] | None:
    raw = str(request.get("resolution") or request.get("size") or "").strip().lower()
    match = re.fullmatch(r"\s*(\d{2,5})\s*[x*×]\s*(\d{2,5})\s*", raw)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _expected_ratio(request: dict[str, Any]) -> float | None:
    raw = str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "").strip()
    if not raw:
        dimensions = _expected_dimensions(request)
        return dimensions[0] / dimensions[1] if dimensions and dimensions[1] else None
    separator = ":" if ":" in raw else "/" if "/" in raw else ""
    if not separator:
        return None
    left, right = raw.split(separator, 1)
    denominator = _float(right)
    return _float(left) / denominator if denominator else None


def _image_signature(path: Path) -> dict[str, Any] | None:
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            width, height = image.size
            sample = image.resize((32, 32), Image.Resampling.LANCZOS)
            average = [round(value, 2) for value in ImageStat.Stat(sample).mean[:3]]
            grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            flattened = getattr(grayscale, "get_flattened_data", None)
            pixels = list(flattened() if callable(flattened) else grayscale.getdata())
            bits = []
            for row in range(8):
                offset = row * 9
                bits.extend(pixels[offset + column] > pixels[offset + column + 1] for column in range(8))
            hash_value = 0
            for bit in bits:
                hash_value = (hash_value << 1) | int(bit)
        analysis = analyze_image(path, allow_onnx=False)
        subject = dict(analysis.get("subject") or {})
        return {
            "width": width,
            "height": height,
            "averageRgb": average,
            "perceptualHash": f"{hash_value:016x}",
            "subjectAreaRatio": subject.get("areaRatio"),
            "subjectCentroid": subject.get("centroid"),
            "subjectBbox": subject.get("bbox"),
            "analysisStatus": analysis.get("status"),
        }
    except Exception:
        return None


def image_visual_signature(path: str | Path) -> dict[str, Any] | None:
    return _image_signature(Path(path).expanduser())


def _extract_representative_frame(
    *,
    ffmpeg: str,
    path: str,
    duration: float,
    runner: CommandRunner,
) -> dict[str, Any] | None:
    seek = max(0.0, duration * 0.5)
    with tempfile.TemporaryDirectory(prefix="v8os-media-quality-") as directory:
        target = Path(directory) / "representative.png"
        try:
            completed = runner(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    f"{seek:.3f}",
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-y",
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except Exception:
            return None
        if completed.returncode != 0 or not target.is_file():
            return None
        return _image_signature(target)


def _run_video_scan(
    *,
    ffmpeg: str,
    path: str,
    duration: float,
    runner: CommandRunner,
) -> dict[str, Any]:
    scan_seconds = min(max(duration, 0.1), MAX_DIAGNOSTIC_SECONDS)
    try:
        completed = runner(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-v",
                "info",
                "-i",
                path,
                "-t",
                f"{scan_seconds:.3f}",
                "-vf",
                "blackdetect=d=1.5:pix_th=0.98,freezedetect=n=-50dB:d=2",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=max(45, min(180, int(scan_seconds * 3 + 20))),
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "scanSeconds": scan_seconds, "error": type(exc).__name__}
    diagnostics = str(completed.stderr or "")
    black_duration = sum(_float(item) for item in BLACK_RE.findall(diagnostics))
    freeze_duration = sum(_float(item) for item in FREEZE_RE.findall(diagnostics))
    return {
        "ok": completed.returncode == 0,
        "scanSeconds": round(scan_seconds, 3),
        "truncated": duration > scan_seconds + 0.01,
        "blackSeconds": round(black_duration, 3),
        "blackRatio": round(min(1.0, black_duration / max(scan_seconds, 0.001)), 4),
        "freezeSeconds": round(freeze_duration, 3),
        "freezeRatio": round(min(1.0, freeze_duration / max(scan_seconds, 0.001)), 4),
    }


def _run_audio_scan(
    *,
    ffmpeg: str,
    path: str,
    duration: float,
    runner: CommandRunner,
) -> dict[str, Any]:
    scan_seconds = min(max(duration, 0.1), MAX_DIAGNOSTIC_SECONDS)
    try:
        completed = runner(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-v",
                "info",
                "-i",
                path,
                "-t",
                f"{scan_seconds:.3f}",
                "-af",
                "volumedetect,silencedetect=noise=-50dB:d=1.0",
                "-vn",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=max(45, min(180, int(scan_seconds * 3 + 20))),
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "scanSeconds": scan_seconds, "error": type(exc).__name__}
    diagnostics = str(completed.stderr or "")
    mean_match = MEAN_VOLUME_RE.search(diagnostics)
    max_match = MAX_VOLUME_RE.search(diagnostics)
    silence_seconds = sum(_float(item) for item in SILENCE_RE.findall(diagnostics))
    return {
        "ok": completed.returncode == 0,
        "scanSeconds": round(scan_seconds, 3),
        "truncated": duration > scan_seconds + 0.01,
        "meanVolumeDb": _db(mean_match.group(1) if mean_match else None),
        "maxVolumeDb": _db(max_match.group(1) if max_match else None),
        "silenceSeconds": round(silence_seconds, 3),
        "silenceRatio": round(min(1.0, silence_seconds / max(scan_seconds, 0.001)), 4),
    }


def inspect_media_quality(
    *,
    path: str,
    kind: str,
    request: dict[str, Any],
    runner: CommandRunner,
    which: ExecutableResolver,
    analyze_visual: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[str] = []
    signature: dict[str, Any] = {"kind": kind}
    ffprobe = which("ffprobe")
    if not ffprobe:
        return {
            "checks": [{"name": "ffprobe_available", "ok": False}],
            "warnings": ["ffprobe_unavailable"],
            "failures": [],
            "signature": signature,
        }
    try:
        result = runner(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {
            "checks": [{"name": "ffprobe_exception", "ok": False, "error": type(exc).__name__}],
            "warnings": ["ffprobe_exception"],
            "failures": [],
            "signature": signature,
        }
    readable = result.returncode == 0
    checks.append({"name": "ffprobe_readable", "ok": readable})
    if not readable:
        return {
            "checks": checks,
            "warnings": warnings,
            "failures": ["ffprobe_failed"],
            "signature": signature,
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        return {
            "checks": [*checks, {"name": "ffprobe_payload_valid", "ok": False}],
            "warnings": warnings,
            "failures": ["ffprobe_payload_invalid"],
            "signature": signature,
        }
    streams = list(payload.get("streams") or [])
    format_payload = dict(payload.get("format") or {})
    duration = _float(format_payload.get("duration"))
    if duration <= 0:
        duration = max((_float(item.get("duration")) for item in streams), default=0.0)
    signature["durationSeconds"] = round(duration, 3)
    checks.append({"name": "media_duration_positive", "ok": duration > 0, "durationSeconds": round(duration, 3)})
    if duration <= 0:
        _append_once(failures, "duration_missing")
    expected_duration = request.get("duration") or request.get("durationSeconds") or request.get("duration_seconds")
    if expected_duration:
        expected = _float(expected_duration)
        tolerance = max(1.0, expected * 0.35)
        duration_ok = abs(duration - expected) <= tolerance
        checks.append(
            {
                "name": "media_duration_close_to_request",
                "ok": duration_ok,
                "expected": expected,
                "actual": round(duration, 3),
            }
        )
        if not duration_ok:
            _append_once(warnings, "duration_mismatch")

    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
    ffmpeg = which("ffmpeg")
    if kind == "video":
        checks.append({"name": "video_stream_present", "ok": bool(video_stream)})
        if not video_stream:
            _append_once(failures, "video_stream_missing")
        else:
            width = _integer(video_stream.get("width"))
            height = _integer(video_stream.get("height"))
            frame_rate = _rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
            signature.update(
                {
                    "width": width,
                    "height": height,
                    "frameRate": round(frame_rate, 4),
                    "videoCodec": str(video_stream.get("codec_name") or ""),
                    "pixelFormat": str(video_stream.get("pix_fmt") or ""),
                }
            )
            checks.append(
                {
                    "name": "video_stream_profile",
                    "ok": width > 0 and height > 0 and frame_rate > 0,
                    "width": width,
                    "height": height,
                    "frameRate": round(frame_rate, 4),
                    "codec": signature["videoCodec"],
                    "pixelFormat": signature["pixelFormat"],
                }
            )
            checks.append(
                {
                    "name": "video_dimensions",
                    "ok": width > 0 and height > 0,
                    "width": width,
                    "height": height,
                }
            )
            if width <= 0 or height <= 0:
                _append_once(failures, "video_dimensions_missing")
            if frame_rate <= 0:
                _append_once(warnings, "video_frame_rate_missing")
            expected_dimensions = _expected_dimensions(request)
            if expected_dimensions:
                dimensions_ok = (width, height) == expected_dimensions
                checks.append(
                    {
                        "name": "video_resolution_matches_request",
                        "ok": dimensions_ok,
                        "expected": list(expected_dimensions),
                        "actual": [width, height],
                    }
                )
                if not dimensions_ok:
                    _append_once(warnings, "video_resolution_mismatch")
            expected_ratio = _expected_ratio(request)
            if expected_ratio and width > 0 and height > 0:
                actual_ratio = width / height
                ratio_ok = abs(actual_ratio - expected_ratio) <= 0.035
                checks.append(
                    {
                        "name": "video_aspect_ratio_matches_request",
                        "ok": ratio_ok,
                        "expected": round(expected_ratio, 4),
                        "actual": round(actual_ratio, 4),
                    }
                )
                if not ratio_ok:
                    _append_once(warnings, "video_aspect_ratio_mismatch")
            expected_fps = _float(request.get("fps") or request.get("frameRate") or request.get("frame_rate"))
            if expected_fps:
                fps_ok = abs(frame_rate - expected_fps) <= 0.2
                checks.append(
                    {
                        "name": "video_frame_rate_matches_request",
                        "ok": fps_ok,
                        "expected": expected_fps,
                        "actual": round(frame_rate, 4),
                    }
                )
                if not fps_ok:
                    _append_once(warnings, "video_frame_rate_mismatch")
            if ffmpeg:
                scan = _run_video_scan(ffmpeg=ffmpeg, path=path, duration=duration, runner=runner)
                checks.append({"name": "video_decode_integrity", "ok": bool(scan.get("ok")), "scanSeconds": scan.get("scanSeconds")})
                if not scan.get("ok"):
                    _append_once(failures, "video_decode_failed")
                else:
                    checks.append(
                        {
                            "name": "video_black_frame_scan",
                            "ok": _float(scan.get("blackRatio")) < 0.85,
                            "blackSeconds": scan.get("blackSeconds"),
                            "blackRatio": scan.get("blackRatio"),
                        }
                    )
                    checks.append(
                        {
                            "name": "video_freeze_scan",
                            "ok": _float(scan.get("freezeRatio")) < 0.9,
                            "freezeSeconds": scan.get("freezeSeconds"),
                            "freezeRatio": scan.get("freezeRatio"),
                        }
                    )
                    if _float(scan.get("blackRatio")) >= 0.85:
                        _append_once(failures, "video_mostly_black")
                    elif _float(scan.get("blackRatio")) > 0.08:
                        _append_once(warnings, "video_black_segment_detected")
                    if _float(scan.get("freezeRatio")) >= 0.9:
                        _append_once(failures, "video_mostly_frozen")
                    elif _float(scan.get("freezeRatio")) > 0.35:
                        _append_once(warnings, "video_long_freeze_detected")
                    if scan.get("truncated"):
                        _append_once(warnings, "video_quality_scan_truncated")
                if analyze_visual:
                    visual_signature = _extract_representative_frame(
                        ffmpeg=ffmpeg,
                        path=path,
                        duration=duration,
                        runner=runner,
                    )
                    signature["visual"] = visual_signature
                    checks.append(
                        {
                            "name": "video_representative_frame_analysis",
                            "ok": bool(visual_signature),
                            "visualSignature": visual_signature,
                        }
                    )
                    if not visual_signature:
                        _append_once(warnings, "video_representative_frame_unavailable")
            else:
                checks.append({"name": "ffmpeg_quality_scan_available", "ok": False})
                _append_once(warnings, "ffmpeg_quality_scan_unavailable")

    require_audio = bool(request.get("requireAudio") or request.get("audioRequired") or request.get("require_audio"))
    if kind == "audio" or audio_stream or require_audio:
        checks.append({"name": "audio_stream_present", "ok": bool(audio_stream)})
        if not audio_stream:
            _append_once(failures, "audio_stream_missing")
        else:
            sample_rate = _integer(audio_stream.get("sample_rate"))
            channels = _integer(audio_stream.get("channels"))
            signature.update(
                {
                    "audioCodec": str(audio_stream.get("codec_name") or ""),
                    "sampleRate": sample_rate,
                    "channels": channels,
                    "channelLayout": str(audio_stream.get("channel_layout") or ""),
                }
            )
            checks.append(
                {
                    "name": "audio_stream_profile",
                    "ok": sample_rate > 0 and channels > 0,
                    "codec": signature["audioCodec"],
                    "sampleRate": sample_rate,
                    "channels": channels,
                    "channelLayout": signature["channelLayout"],
                }
            )
            if sample_rate <= 0 or channels <= 0:
                _append_once(warnings, "audio_stream_profile_incomplete")
            if ffmpeg:
                scan = _run_audio_scan(ffmpeg=ffmpeg, path=path, duration=duration, runner=runner)
                checks.append({"name": "audio_decode_integrity", "ok": bool(scan.get("ok")), "scanSeconds": scan.get("scanSeconds")})
                if not scan.get("ok"):
                    _append_once(failures, "audio_decode_failed")
                else:
                    mean_db = scan.get("meanVolumeDb")
                    max_db = scan.get("maxVolumeDb")
                    silence_ratio = _float(scan.get("silenceRatio"))
                    signature.update(
                        {
                            "meanVolumeDb": mean_db,
                            "maxVolumeDb": max_db,
                            "silenceRatio": silence_ratio,
                        }
                    )
                    effectively_silent = max_db == -math.inf or silence_ratio >= 0.98
                    checks.append(
                        {
                            "name": "audio_level_scan",
                            "ok": not effectively_silent,
                            "meanVolumeDb": mean_db,
                            "maxVolumeDb": max_db,
                            "silenceSeconds": scan.get("silenceSeconds"),
                            "silenceRatio": silence_ratio,
                        }
                    )
                    if effectively_silent:
                        _append_once(failures, "audio_effectively_silent")
                    else:
                        if isinstance(mean_db, (int, float)) and math.isfinite(mean_db) and mean_db < -35:
                            _append_once(warnings, "audio_level_too_low")
                        if isinstance(max_db, (int, float)) and math.isfinite(max_db) and max_db > -0.05:
                            _append_once(warnings, "audio_peak_near_clipping")
                        if silence_ratio > 0.6:
                            _append_once(warnings, "audio_excessive_silence")
                    if scan.get("truncated"):
                        _append_once(warnings, "audio_quality_scan_truncated")
            else:
                checks.append({"name": "ffmpeg_quality_scan_available", "ok": False})
                _append_once(warnings, "ffmpeg_quality_scan_unavailable")
    return {"checks": checks, "warnings": warnings, "failures": failures, "signature": signature}


def consistency_observation(
    artifact: dict[str, Any],
    checks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    identity = str(artifact.get("artifactId") or artifact.get("title") or "").strip()
    media = next((item for item in checks if item.get("name") == "media_technical_signature"), None)
    if media:
        signature = dict(media.get("signature") or {})
        if signature.get("kind") == "video":
            return {"artifact": identity, **signature}
    dimensions = next((item for item in checks if item.get("name") == "image_dimensions"), None)
    subject = next((item for item in checks if item.get("name") == "image_subject_analysis"), None)
    visual = dict((subject or {}).get("visualSignature") or {})
    if dimensions:
        return {
            "artifact": identity,
            "kind": "image",
            "width": dimensions.get("width"),
            "height": dimensions.get("height"),
            "visual": visual or None,
        }
    return None


def _spread(values: list[float]) -> float:
    return max(values) - min(values) if len(values) >= 2 else 0.0


def _centroid_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.sqrt(
        (_float(left.get("x")) - _float(right.get("x"))) ** 2
        + (_float(left.get("y")) - _float(right.get("y"))) ** 2
    )


def evaluate_cross_shot_consistency(
    observations: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = dict(config or {})
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[str] = []
    if len(observations) < 2:
        return {"checks": checks, "warnings": warnings, "failures": failures}
    resolutions = {
        (_integer(item.get("width")), _integer(item.get("height")))
        for item in observations
        if _integer(item.get("width")) > 0 and _integer(item.get("height")) > 0
    }
    frame_rates = [_float(item.get("frameRate")) for item in observations if _float(item.get("frameRate")) > 0]
    audio_profiles = {
        (_integer(item.get("sampleRate")), _integer(item.get("channels")))
        for item in observations
        if _integer(item.get("sampleRate")) > 0 or _integer(item.get("channels")) > 0
    }
    technical_ok = len(resolutions) <= 1 and _spread(frame_rates) <= 0.5 and len(audio_profiles) <= 1
    checks.append(
        {
            "name": "cross_shot_technical_consistency",
            "ok": technical_ok,
            "shotCount": len(observations),
            "resolutions": [list(item) for item in sorted(resolutions)],
            "frameRateSpread": round(_spread(frame_rates), 4),
            "audioProfiles": [list(item) for item in sorted(audio_profiles)],
        }
    )
    if not technical_ok:
        if bool(settings.get("strictTechnical", True)):
            _append_once(failures, "cross_shot_technical_mismatch")
        else:
            _append_once(warnings, "cross_shot_technical_mismatch")
    mean_levels = [
        _float(item.get("meanVolumeDb"))
        for item in observations
        if isinstance(item.get("meanVolumeDb"), (int, float)) and math.isfinite(float(item.get("meanVolumeDb")))
    ]
    level_spread = _spread(mean_levels)
    checks.append(
        {
            "name": "cross_shot_audio_level_consistency",
            "ok": level_spread <= 10.0,
            "meanLevelSpreadDb": round(level_spread, 3),
            "measuredShots": len(mean_levels),
        }
    )
    if level_spread > 10.0:
        _append_once(warnings, "cross_shot_audio_level_drift")
    visuals = [dict(item.get("visual") or {}) for item in observations if isinstance(item.get("visual"), dict)]
    area_values = [
        _float(item.get("subjectAreaRatio"))
        for item in visuals
        if item.get("subjectAreaRatio") is not None
    ]
    centroids = [dict(item.get("subjectCentroid") or {}) for item in visuals if item.get("subjectCentroid")]
    colors = [list(item.get("averageRgb") or []) for item in visuals if len(list(item.get("averageRgb") or [])) == 3]
    center_shift = 0.0
    if len(centroids) >= 2:
        anchor = centroids[0]
        center_shift = max(_centroid_distance(anchor, item) for item in centroids[1:])
    color_shift = 0.0
    if len(colors) >= 2:
        anchor_color = colors[0]
        color_shift = max(
            math.sqrt(sum((_float(left) - _float(right)) ** 2 for left, right in zip(anchor_color, item))) / 441.673
            for item in colors[1:]
        )
    visual_ok = (
        len(visuals) >= 2
        and _spread(area_values) <= 0.35
        and center_shift <= 0.35
        and color_shift <= 0.5
    )
    checks.append(
        {
            "name": "cross_shot_visual_continuity",
            "ok": visual_ok,
            "measuredShots": len(visuals),
            "subjectScaleSpread": round(_spread(area_values), 4),
            "maxSubjectCenterShift": round(center_shift, 4),
            "maxPaletteDistance": round(color_shift, 4),
        }
    )
    if len(visuals) < 2:
        _append_once(warnings, "cross_shot_visual_evidence_incomplete")
    elif not visual_ok:
        _append_once(warnings, "cross_shot_visual_drift")
    require_semantic_review = bool(settings.get("requireSemanticReview", True))
    checks.append(
        {
            "name": "cross_shot_semantic_identity_review",
            "ok": not require_semantic_review,
            "qualityState": "review_required" if require_semantic_review else "passed",
            "reason": (
                "local metrics cannot prove character or subject identity across shots"
                if require_semantic_review
                else "semantic review explicitly completed or waived by the governed request"
            ),
        }
    )
    return {"checks": checks, "warnings": warnings, "failures": failures}
