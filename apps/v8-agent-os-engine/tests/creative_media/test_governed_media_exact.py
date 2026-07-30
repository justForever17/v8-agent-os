from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock

import pytest

from core.dependency_registry import build_dependency_status
from core.process_launch import run_windowless
from runtimes.creative_media import governed_media
from runtimes.creative_media.runtime import CreativeMediaRuntime


def _ffmpeg_pair_or_skip() -> tuple[str, str, str]:
    status = next(item for item in build_dependency_status(refresh=True) if item["id"] == "ffmpeg")
    detection = status["detection"]
    if not detection.get("detected"):
        pytest.skip("paired FFmpeg/FFprobe 7.0+ is unavailable")
    return detection["path"], detection["ffprobePath"], detection["version"]


def _run(command: list[str]) -> None:
    result = run_windowless(command, capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr


def test_probe_cache_rechecks_session_authority_but_reuses_unchanged_media_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    resources = iter([
        {"kind": "source", "id": "source-session-a"},
        {"kind": "workspace_asset", "id": "asset-session-b"},
    ])
    calls = {"resource": 0, "header": 0, "timeline": 0}

    def resolve(_request: dict):
        calls["resource"] += 1
        return source, next(resources)

    def header(_ffprobe: str, _path: Path):
        calls["header"] += 1
        return {
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
            "format": {"format_name": "mp4"},
        }

    def timeline(_ffprobe: str, _path: Path, _stream: dict):
        calls["timeline"] += 1
        return {
            "unit": "frame",
            "count": 2,
            "timeBase": {"numerator": 1, "denominator": 24, "value": "1/24"},
            "boundaryTicks": [0, 1, 2],
            "durationSeconds": "0.083333333",
            "displayPrecision": 6,
        }

    governed_media._PROBE_CACHE.clear()
    monkeypatch.setattr(governed_media, "_ffmpeg_pair", lambda: ("ffmpeg", "ffprobe", "7.0"))
    monkeypatch.setattr(governed_media, "_resource_path", resolve)
    monkeypatch.setattr(governed_media, "_stream_header", header)
    monkeypatch.setattr(governed_media, "_video_timeline", timeline)

    first = governed_media.probe_request({"sessionId": "session-a", "sourceId": "source-session-a"})
    second = governed_media.probe_request({"sessionId": "session-b", "workspaceAssetId": "asset-session-b"})

    assert first["resource"] == {"kind": "source", "id": "source-session-a"}
    assert second["resource"] == {"kind": "workspace_asset", "id": "asset-session-b"}
    assert calls == {"resource": 2, "header": 1, "timeline": 1}


def test_video_preview_probe_returns_header_timeline_without_scanning_frames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    calls = {"header": 0, "timeline": 0}

    def header(_ffprobe: str, _path: Path):
        calls["header"] += 1
        return {
            "streams": [{
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "time_base": "1/90000",
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30000/1001",
                "duration": "N/A",
            }],
            "format": {"duration": "10.01", "format_name": "mp4"},
        }

    def timeline(_ffprobe: str, _path: Path, _stream: dict):
        calls["timeline"] += 1
        return {
            "unit": "frame",
            "count": 300,
            "timeBase": {"numerator": 1, "denominator": 90000, "value": "1/90000"},
            "boundaryTicks": list(range(301)),
            "durationSeconds": "10.01",
            "displayPrecision": 6,
            "approximate": False,
        }

    governed_media._PROBE_CACHE.clear()
    monkeypatch.setattr(governed_media, "_ffmpeg_pair", lambda: ("ffmpeg", "ffprobe", "7.0"))
    monkeypatch.setattr(
        governed_media,
        "_resource_path",
        lambda _request: (source, {"kind": "source", "id": "source-video"}),
    )
    monkeypatch.setattr(governed_media, "_stream_header", header)
    monkeypatch.setattr(governed_media, "_video_timeline", timeline)

    preview = governed_media.probe_request({
        "sessionId": "session-a",
        "sourceId": "source-video",
        "detail": "preview",
    })
    assert preview["timeline"]["approximate"] is True
    assert preview["timeline"]["count"] == 300
    assert "boundaryTicks" not in preview["timeline"]
    assert calls == {"header": 1, "timeline": 0}

    exact = governed_media.probe_request({"sessionId": "session-a", "sourceId": "source-video"})
    assert exact["timeline"]["approximate"] is False
    assert calls == {"header": 2, "timeline": 1}


def test_exact_probe_coalesces_concurrent_requests_without_losing_session_resource_truth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "shared.mp4"
    source.write_bytes(b"media")
    timeline_started = Event()
    release_timeline = Event()
    call_lock = Lock()
    calls = {"header": 0, "timeline": 0}

    def resolve(request: dict):
        return source, {"kind": "source", "id": str(request["sourceId"])}

    def header(_ffprobe: str, _path: Path):
        with call_lock:
            calls["header"] += 1
        return {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}], "format": {"format_name": "mp4"}}

    def timeline(_ffprobe: str, _path: Path, _stream: dict):
        with call_lock:
            calls["timeline"] += 1
        timeline_started.set()
        assert release_timeline.wait(timeout=5)
        return {
            "unit": "frame",
            "count": 2,
            "timeBase": {"numerator": 1, "denominator": 24, "value": "1/24"},
            "boundaryTicks": [0, 1, 2],
            "durationSeconds": "0.083333333",
            "displayPrecision": 6,
            "approximate": False,
        }

    governed_media._PROBE_CACHE.clear()
    governed_media._PROBE_INFLIGHT.clear()
    monkeypatch.setattr(governed_media, "_ffmpeg_pair", lambda: ("ffmpeg", "ffprobe", "7.0"))
    monkeypatch.setattr(governed_media, "_resource_path", resolve)
    monkeypatch.setattr(governed_media, "_stream_header", header)
    monkeypatch.setattr(governed_media, "_video_timeline", timeline)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(governed_media.probe_request, {"sessionId": "session-a", "sourceId": "source-a"})
        assert timeline_started.wait(timeout=5)
        second = pool.submit(governed_media.probe_request, {"sessionId": "session-b", "sourceId": "source-b"})
        release_timeline.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert calls == {"header": 1, "timeline": 1}
    assert first_result["resource"]["id"] == "source-a"
    assert second_result["resource"]["id"] == "source-b"


def test_video_trim_uses_frame_indices_and_postflight_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe, version = _ffmpeg_pair_or_skip()
    source = tmp_path / "source.mp4"
    output = tmp_path / "trimmed.mp4"
    frame_output = tmp_path / "frame.png"
    _run([
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1.25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ])
    monkeypatch.setattr(governed_media, "_ffmpeg_pair", lambda: (ffmpeg, ffprobe, version))
    monkeypatch.setattr(
        governed_media,
        "_resource_path",
        lambda _request: (source, {"kind": "source", "id": "source-video"}),
    )
    probe = governed_media.probe_request({"sessionId": "session-a", "sourceId": "source-video"})
    assert probe["timeline"]["unit"] == "frame"
    assert probe["timeline"]["count"] == 30
    assert len(probe["timeline"]["boundaryTicks"]) == 31

    proof = governed_media.trim_exact(
        {
            "sessionId": "session-a",
            "sourceId": "source-video",
            "operationKind": "video.trim_exact",
            "probeFingerprint": probe["fingerprint"],
            "startFrameIndex": 5,
            "endFrameIndexExclusive": 17,
        },
        output_path=output,
    )
    assert proof["selection"]["expectedFrameCount"] == 12
    assert proof["output"]["timeline"]["count"] == 12
    assert proof["engine"]["windowless"] is True
    assert proof["engine"]["providerInvoked"] is False

    frame_proof = governed_media.trim_exact(
        {
            "sessionId": "session-a",
            "sourceId": "source-video",
            "operationKind": "video.extract_frame_exact",
            "probeFingerprint": probe["fingerprint"],
            "frameIndex": 9,
        },
        output_path=frame_output,
    )
    assert frame_proof["selection"]["frameIndex"] == 9
    assert frame_proof["output"]["timeline"]["count"] == 1
    assert frame_output.read_bytes().startswith(b"\x89PNG")


def test_audio_trim_uses_sample_indices_and_preserves_exact_sample_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe, version = _ffmpeg_pair_or_skip()
    source = tmp_path / "source.wav"
    output = tmp_path / "trimmed.flac"
    _run([
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-c:a", "pcm_s16le", str(source),
    ])
    monkeypatch.setattr(governed_media, "_ffmpeg_pair", lambda: (ffmpeg, ffprobe, version))
    monkeypatch.setattr(
        governed_media,
        "_resource_path",
        lambda _request: (source, {"kind": "source", "id": "source-audio"}),
    )
    probe = governed_media.probe_request({"sessionId": "session-a", "sourceId": "source-audio"})
    assert probe["timeline"]["unit"] == "sample"
    assert probe["timeline"]["sampleRate"] == 48000

    proof = governed_media.trim_exact(
        {
            "sessionId": "session-a",
            "sourceId": "source-audio",
            "operationKind": "audio.trim_exact",
            "probeFingerprint": probe["fingerprint"],
            "startSampleIndex": 1000,
            "endSampleIndexExclusive": 13000,
        },
        output_path=output,
    )
    assert proof["selection"]["expectedSampleCount"] == 12000
    assert proof["output"]["timeline"]["count"] == 12000


def test_exact_trim_job_bypasses_provider_model_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeStorage:
        base_dir = tmp_path

        def __init__(self) -> None:
            self.payloads: dict[str, dict] = {}

        def read_json(self, key: str) -> dict:
            return deepcopy(self.payloads.get(key) or {})

        def write_json(self, key: str, value: dict) -> None:
            self.payloads[key] = deepcopy(value)

    runtime = CreativeMediaRuntime()
    output = tmp_path / "exact.mp4"
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", FakeStorage())
    monkeypatch.setattr(runtime, "_output_path", lambda *_args, **_kwargs: output)
    monkeypatch.setattr(
        runtime,
        "_preferred_model_candidates",
        lambda *_args, **_kwargs: pytest.fail("local exact trim must not rank provider models"),
    )

    def fake_trim(_request: dict, *, output_path: Path) -> dict:
        output_path.write_bytes(b"video")
        return {"schema": "v8.governed_media_trim_proof.v1", "engine": {"providerInvoked": False}}

    monkeypatch.setattr("runtimes.creative_media.runtime.trim_governed_media_exact", fake_trim)
    monkeypatch.setattr(
        runtime,
        "_record_local_artifact",
        lambda **_kwargs: {"artifactId": "artifact-exact", "mimeType": "video/mp4"},
    )
    job = asyncio.run(runtime.create_job({
        "sessionId": "session-a",
        "modality": "video",
        "operationKind": "video.trim_exact",
        "sourceId": "source-video",
        "probeFingerprint": "v8mf-proof",
        "startFrameIndex": 2,
        "endFrameIndexExclusive": 7,
    }))
    assert job["status"] == "succeeded"
    assert job["adapter"] == "governed_ffmpeg"
    assert job["artifacts"][0]["artifactId"] == "artifact-exact"
    assert job["providerResponse"]["providerInvoked"] is False
