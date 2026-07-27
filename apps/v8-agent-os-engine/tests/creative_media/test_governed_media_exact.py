from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

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
