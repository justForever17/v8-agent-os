from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from runtimes.creative_media import motion_capture


@dataclass
class _Landmark:
    x: float
    y: float
    z: float
    visibility: float = 0.9


class _Result:
    def __init__(self, *, visible: bool = True) -> None:
        points = [_Landmark(index / 33, 0.5, -0.1) for index in range(33)] if visible else []
        hands = [_Landmark(index / 21, 0.4, -0.05) for index in range(21)] if visible else []
        self.pose_landmarks = points
        self.pose_world_landmarks = points
        self.left_hand_landmarks = hands
        self.left_hand_world_landmarks = hands
        self.right_hand_landmarks = hands
        self.right_hand_world_landmarks = hands


class _Detector:
    def __init__(self, *, visible_frames: set[int] | None = None) -> None:
        self.visible_frames = visible_frames
        self.timestamps: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def detect(self, frame, timestamp_ms: int):
        del frame
        index = len(self.timestamps)
        self.timestamps.append(timestamp_ms)
        return _Result(visible=self.visible_frames is None or index in self.visible_frames)


def _probe(frame_count: int = 3):
    boundaries = [0, 1001, 2002, 3003][: frame_count + 1]
    return {
        "schema": "v8.governed_media_probe.v1",
        "fingerprint": "v8mf-test",
        "kind": "video",
        "timeline": {
            "unit": "frame",
            "count": frame_count,
            "timeBase": {"numerator": 1, "denominator": 30000},
            "boundaryTicks": boundaries,
            "durationSeconds": "0.100100000",
            "variableFrameRate": False,
        },
    }


def _install_fakes(monkeypatch, tmp_path, *, frame_count: int = 3, decoded_count: int | None = None, detector=None):
    model_path = tmp_path / "holistic.task"
    model_path.write_bytes(b"verified-model")
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    frames = decoded_count if decoded_count is not None else frame_count
    fake_detector = detector or _Detector()
    monkeypatch.setattr(motion_capture, "resolve_feature_pack_asset", lambda *_args: model_path)
    monkeypatch.setattr(motion_capture, "resolve_governed_media_path", lambda _request: (source_path, {"kind": "source", "id": "src-a"}))
    monkeypatch.setattr(motion_capture, "probe_request", lambda _request: _probe(frame_count))
    monkeypatch.setattr(
        motion_capture,
        "_iter_rgb_frames",
        lambda _path: iter([np.zeros((4, 4, 3), dtype=np.uint8) for _ in range(frames)]),
    )
    monkeypatch.setattr(motion_capture, "_create_detector", lambda *_args, **_kwargs: fake_detector)
    return fake_detector


def test_extract_holistic_motion_preserves_exact_pts_and_qa(monkeypatch, tmp_path):
    detector = _install_fakes(monkeypatch, tmp_path)
    output_path = tmp_path / "motion.v8motion"

    proof = motion_capture.extract_holistic_motion(
        {"sessionId": "session-a", "sourceId": "src-a"},
        output_path=output_path,
    )

    assert proof["providerInvoked"] is False
    assert proof["manifest"]["source"]["timeBase"] == {"numerator": 1, "denominator": 30000}
    assert proof["manifest"]["qa"]["status"] == "passed"
    assert detector.timestamps == [0, 33, 67]
    assert motion_capture.inspect_motion_package(output_path)["source"]["frameCount"] == 3
    frame = motion_capture.read_motion_frame(output_path, 1)
    assert frame["frameIndex"] == 1
    assert frame["ptsTicks"] == 1001
    assert len(frame["pose"]) == 33
    with np.load(output_path, allow_pickle=False) as package:
        assert package["pose"].shape == (3, 33, 4)
        assert package["left_hand_world"].shape == (3, 21, 4)


def test_extract_holistic_motion_records_low_coverage_warning(monkeypatch, tmp_path):
    detector = _Detector(visible_frames={0})
    _install_fakes(monkeypatch, tmp_path, detector=detector)
    output_path = tmp_path / "motion.v8motion"

    proof = motion_capture.extract_holistic_motion(
        {"sessionId": "session-a", "sourceId": "src-a"},
        output_path=output_path,
    )

    assert proof["manifest"]["qa"]["status"] == "failed"
    assert proof["manifest"]["qa"]["poseCoverage"] == pytest.approx(1 / 3, abs=1e-6)
    assert proof["manifest"]["qa"]["warnings"] == ["low_pose_coverage"]


def test_extract_holistic_motion_rejects_missing_pack(monkeypatch, tmp_path):
    monkeypatch.setattr(motion_capture, "resolve_feature_pack_asset", lambda *_args: None)

    with pytest.raises(motion_capture.MotionCaptureError, match="动作采集能力包"):
        motion_capture.extract_holistic_motion({}, output_path=tmp_path / "motion.v8motion")


def test_extract_holistic_motion_rejects_decode_timeline_drift(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, tmp_path, frame_count=3, decoded_count=2)

    with pytest.raises(motion_capture.MotionCaptureError, match="ffprobe=3, decoded=2"):
        motion_capture.extract_holistic_motion(
            {"sessionId": "session-a", "sourceId": "src-a"},
            output_path=tmp_path / "motion.v8motion",
        )


def test_motion_frame_reader_rejects_out_of_range(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, tmp_path)
    output_path = tmp_path / "motion.v8motion"
    motion_capture.extract_holistic_motion(
        {"sessionId": "session-a", "sourceId": "src-a"},
        output_path=output_path,
    )

    with pytest.raises(motion_capture.MotionCaptureError, match="超出时间轴"):
        motion_capture.read_motion_frame(output_path, 3)
