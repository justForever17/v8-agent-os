from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from core.tools.native.creative_media_facade import CREATIVE_MEDIA_ACTION_REGISTRY
from runtimes.creative_media.media_quality import (
    evaluate_cross_shot_consistency,
    inspect_media_quality,
)
from runtimes.creative_media.runtime import creative_media_runtime


class FakeJsonStorage:
    def __init__(self) -> None:
        self.payloads: dict[str, dict] = {}

    def read_json(self, filename: str):
        return deepcopy(self.payloads.get(filename) or {})

    def write_json(self, filename: str, data) -> None:
        self.payloads[filename] = deepcopy(data)


def _resolver(name: str) -> str:
    return name


def _probe_payload(*, width: int = 1920, height: int = 1080, fps: str = "30/1") -> str:
    return json.dumps(
        {
            "format": {"duration": "5.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "width": width,
                    "height": height,
                    "avg_frame_rate": fps,
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                    "channel_layout": "stereo",
                },
            ],
        }
    )


def test_quality_facade_exposes_typed_cross_shot_contract() -> None:
    assert "crossShotConsistency" in CREATIVE_MEDIA_ACTION_REGISTRY["quality"]["create_job"].allowed_fields
    assert "crossShotConsistency" in CREATIVE_MEDIA_ACTION_REGISTRY["quality"]["qa_check"].allowed_fields


def test_video_quality_inspects_decode_picture_and_audio_profiles() -> None:
    def runner(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=_probe_payload(), stderr="")
        if "-vf" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="black_duration:0.2\nfreeze_duration: 0.1",
            )
        if "-af" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="mean_volume: -18.0 dB\nmax_volume: -1.0 dB\nsilence_duration: 0.2",
            )
        raise AssertionError(command)

    result = inspect_media_quality(
        path="movie.mp4",
        kind="video",
        request={"duration": 5, "resolution": "1920x1080", "fps": 30},
        runner=runner,
        which=_resolver,
    )

    assert result["failures"] == []
    assert result["warnings"] == []
    by_name = {item["name"]: item for item in result["checks"]}
    assert by_name["video_dimensions"]["ok"] is True
    assert by_name["video_decode_integrity"]["ok"] is True
    assert by_name["video_black_frame_scan"]["blackRatio"] == 0.04
    assert by_name["audio_level_scan"]["meanVolumeDb"] == -18.0
    assert result["signature"]["sampleRate"] == 48000


def test_audio_quality_rejects_effectively_silent_output() -> None:
    probe = json.dumps(
        {
            "format": {"duration": "10.0"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "sample_rate": "44100",
                    "channels": 1,
                    "channel_layout": "mono",
                }
            ],
        }
    )

    def runner(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=probe, stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="mean_volume: -inf dB\nmax_volume: -inf dB\nsilence_duration: 10.0",
        )

    result = inspect_media_quality(
        path="silent.mp3",
        kind="audio",
        request={},
        runner=runner,
        which=_resolver,
    )

    assert "audio_effectively_silent" in result["failures"]
    assert next(item for item in result["checks"] if item["name"] == "audio_level_scan")["ok"] is False


def test_cross_shot_gate_separates_technical_truth_from_semantic_review() -> None:
    observations = [
        {
            "artifact": "shot-a",
            "kind": "video",
            "width": 1920,
            "height": 1080,
            "frameRate": 24.0,
            "sampleRate": 48000,
            "channels": 2,
            "meanVolumeDb": -18.0,
            "visual": {"averageRgb": [30, 40, 50], "subjectAreaRatio": 0.4, "subjectCentroid": {"x": 0.5, "y": 0.5}},
        },
        {
            "artifact": "shot-b",
            "kind": "video",
            "width": 1280,
            "height": 720,
            "frameRate": 30.0,
            "sampleRate": 44100,
            "channels": 1,
            "meanVolumeDb": -8.0,
            "visual": {"averageRgb": [220, 30, 30], "subjectAreaRatio": 0.9, "subjectCentroid": {"x": 0.9, "y": 0.1}},
        },
    ]

    result = evaluate_cross_shot_consistency(observations)

    assert "cross_shot_technical_mismatch" in result["failures"]
    assert "cross_shot_visual_drift" in result["warnings"]
    semantic = next(item for item in result["checks"] if item["name"] == "cross_shot_semantic_identity_review")
    assert semantic["qualityState"] == "review_required"


def test_cross_shot_gate_accepts_governed_semantic_review_result() -> None:
    observation = {
        "kind": "video",
        "width": 1920,
        "height": 1080,
        "frameRate": 24.0,
        "sampleRate": 48000,
        "channels": 2,
        "meanVolumeDb": -18.0,
        "visual": {"averageRgb": [30, 40, 50], "subjectAreaRatio": 0.4, "subjectCentroid": {"x": 0.5, "y": 0.5}},
    }

    result = evaluate_cross_shot_consistency(
        [{"artifact": "shot-a", **observation}, {"artifact": "shot-b", **observation}],
        config={"requireSemanticReview": False},
    )

    assert result["failures"] == []
    assert result["warnings"] == []
    semantic = next(item for item in result["checks"] if item["name"] == "cross_shot_semantic_identity_review")
    assert semantic["qualityState"] == "passed"


def test_quality_job_runs_cross_shot_gate_on_two_video_outputs(monkeypatch, tmp_path: Path) -> None:
    storage = FakeJsonStorage()
    first = tmp_path / "shot-a.mp4"
    second = tmp_path / "shot-b.mp4"
    first.write_bytes(b"video-a")
    second.write_bytes(b"video-b")

    def runner(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=_probe_payload(width=640, height=360, fps="24/1"), stderr="")
        if "-frames:v" in command:
            Image.new("RGB", (64, 36), (20, 40, 80)).save(Path(command[-1]))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "-vf" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "-af" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="mean_volume: -18.0 dB\nmax_volume: -1.0 dB")
        raise AssertionError(command)

    monkeypatch.setattr("runtimes.creative_media.runtime.storage", storage)
    monkeypatch.setattr("runtimes.creative_media.runtime.subprocess.run", runner)
    monkeypatch.setattr("runtimes.creative_media.runtime.shutil.which", _resolver)

    result = creative_media_runtime.create_quality_job(
        {
            "artifacts": [
                {"artifactId": "shot-a", "kind": "video", "sourcePath": str(first)},
                {"artifactId": "shot-b", "kind": "video", "sourcePath": str(second)},
            ],
            "crossShotConsistency": {"requireSemanticReview": False},
        }
    )

    assert result["status"] == "passed"
    check_names = {item["name"] for item in result["checks"]}
    assert "cross_shot_technical_consistency" in check_names
    assert "cross_shot_visual_continuity" in check_names
    assert result["summary"].startswith("视频已通过")
