from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

from core.process_launch import windowless_subprocess_kwargs
from core.runtime.feature_packs import (
    preferred_feature_pack_execution_provider,
    resolve_feature_pack_asset,
)

from .governed_media import (
    governed_ffmpeg_pair,
    probe_request,
    resolve_governed_media_path,
)


MOTION_SCHEMA = "v8.motion_capture.v1"
MOTION_MIME_TYPE = "application/vnd.v8.motion+zip"
DECODE_SIZE = 640
POSE_LANDMARK_COUNT = 33
HAND_LANDMARK_COUNT = 21
MOTION_GUIDANCE_SCHEMA = "v8.motion_guidance_video.v1"
MOTION_GUIDANCE_SIZE = 768
POSE_CONNECTIONS = (
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
    (11, 23), (12, 24), (23, 24), (23, 25), (25, 27),
    (27, 29), (29, 31), (24, 26), (26, 28), (28, 30), (30, 32),
)
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)


class MotionCaptureError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _iter_rgb_frames(source_path: Path, *, width: int = DECODE_SIZE, height: int = DECODE_SIZE) -> Iterator[Any]:
    try:
        import numpy as np
    except ImportError as exc:
        raise MotionCaptureError("Motion capture requires the managed NumPy runtime") from exc

    ffmpeg, _, _ = governed_ffmpeg_pair()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-i", str(source_path),
        "-map", "0:v:0",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        "-fps_mode", "passthrough",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1",
    ]
    frame_size = width * height * 3
    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            bufsize=frame_size * 2,
            **windowless_subprocess_kwargs(),
        )
        try:
            if process.stdout is None:
                raise MotionCaptureError("Governed ffmpeg decoder has no output stream")
            while True:
                payload = _read_exact(process.stdout, frame_size)
                if not payload:
                    break
                if len(payload) != frame_size:
                    raise MotionCaptureError("Governed ffmpeg returned a partial video frame")
                yield np.frombuffer(payload, dtype=np.uint8).reshape((height, width, 3)).copy()
            return_code = process.wait(timeout=30)
            if return_code != 0:
                stderr_file.seek(0)
                detail = stderr_file.read().decode("utf-8", errors="replace").strip().splitlines()
                raise MotionCaptureError((detail[-1] if detail else "Governed ffmpeg decode failed")[-360:])
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


class _MediaPipeHolisticDetector:
    def __init__(self, model_path: Path, *, min_confidence: float) -> None:
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise MotionCaptureError(
                "动作采集能力包未安装或尚未重启 Engine；请先在控制台安装动作采集能力包。"
            ) from exc
        self._mp = mp
        preferred = preferred_feature_pack_execution_provider("creative_media_motion_capture").upper()

        def create(provider: str) -> Any:
            delegate = (
                mp.tasks.BaseOptions.Delegate.GPU
                if provider == "GPU"
                else mp.tasks.BaseOptions.Delegate.CPU
            )
            options = mp.tasks.vision.HolisticLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path), delegate=delegate),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                min_face_detection_confidence=min_confidence,
                min_face_landmarks_confidence=min_confidence,
                min_pose_detection_confidence=min_confidence,
                min_pose_landmarks_confidence=min_confidence,
                min_hand_landmarks_confidence=min_confidence,
                output_face_blendshapes=False,
                output_segmentation_mask=False,
            )
            return mp.tasks.vision.HolisticLandmarker.create_from_options(options)

        self.execution_provider = "GPU" if preferred == "GPU" else "CPU"
        try:
            self._detector = create(self.execution_provider)
        except Exception:
            if self.execution_provider != "GPU":
                raise
            self.execution_provider = "CPU"
            self._detector = create("CPU")

    def __enter__(self) -> "_MediaPipeHolisticDetector":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._detector.close()

    def detect(self, rgb_frame: Any, timestamp_ms: int) -> Any:
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)
        return self._detector.detect_for_video(image, timestamp_ms)


def _create_detector(model_path: Path, *, min_confidence: float) -> _MediaPipeHolisticDetector:
    return _MediaPipeHolisticDetector(model_path, min_confidence=min_confidence)


def _flat_landmarks(value: Any) -> list[Any]:
    items = list(value or [])
    if items and not hasattr(items[0], "x"):
        nested = list(items[0] or [])
        if nested and hasattr(nested[0], "x"):
            return nested
    return items


def _landmark_frame(value: Any, count: int) -> Any:
    import numpy as np

    frame = np.full((count, 4), np.nan, dtype=np.float32)
    for index, landmark in enumerate(_flat_landmarks(value)[:count]):
        confidence = getattr(landmark, "visibility", None)
        if confidence is None:
            confidence = getattr(landmark, "presence", None)
        if confidence is None:
            confidence = 1.0
        frame[index] = [
            float(getattr(landmark, "x", math.nan)),
            float(getattr(landmark, "y", math.nan)),
            float(getattr(landmark, "z", math.nan)),
            float(confidence),
        ]
    return frame


def _manifest_bytes(manifest: dict[str, Any]) -> Any:
    import numpy as np

    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return np.frombuffer(payload, dtype=np.uint8)


def _qa_summary(pose_frames: Any, *, frame_count: int, min_confidence: float) -> dict[str, Any]:
    import numpy as np

    detected = np.isfinite(pose_frames[:, :, 0]).any(axis=1) if frame_count else np.zeros((0,), dtype=bool)
    coverage = float(detected.mean()) if detected.size else 0.0
    confidence_values = pose_frames[:, :, 3]
    finite_confidence = confidence_values[np.isfinite(confidence_values)]
    mean_confidence = float(finite_confidence.mean()) if finite_confidence.size else 0.0
    if coverage < 0.4:
        status = "failed"
    elif coverage < 0.8 or mean_confidence < min_confidence:
        status = "warning"
    else:
        status = "passed"
    return {
        "status": status,
        "poseCoverage": round(coverage, 6),
        "meanLandmarkConfidence": round(mean_confidence, 6),
        "detectedFrameCount": int(detected.sum()),
        "undetectedFrameCount": int(frame_count - int(detected.sum())),
        "minimumRequestedConfidence": min_confidence,
        "warnings": [
            *(["low_pose_coverage"] if coverage < 0.8 else []),
            *(["low_landmark_confidence"] if mean_confidence < min_confidence else []),
        ],
    }


def extract_holistic_motion(request: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError as exc:
        raise MotionCaptureError("Motion capture requires the managed NumPy runtime") from exc

    model_path = resolve_feature_pack_asset("creative_media_motion_capture", "holistic_landmarker")
    if model_path is None:
        raise MotionCaptureError(
            "动作采集能力包未安装、模型资产未通过校验或 Engine 尚未重启；请在控制台修复能力包状态。"
        )
    source_path, resource = resolve_governed_media_path(request)
    probe = probe_request(request)
    timeline = dict(probe.get("timeline") or {})
    if probe.get("kind") != "video" or timeline.get("unit") != "frame":
        raise MotionCaptureError("单人动作提取只接受当前会话绑定的视频来源")
    boundary_ticks = np.asarray(list(timeline.get("boundaryTicks") or [])[:-1], dtype=np.int64)
    frame_count = int(timeline.get("count") or 0)
    if frame_count <= 0 or boundary_ticks.size != frame_count:
        raise MotionCaptureError("受治理时间轴缺少逐帧 PTS，无法保证动作与视频对齐")
    time_base = dict(timeline.get("timeBase") or {})
    numerator = int(time_base.get("numerator") or 0)
    denominator = int(time_base.get("denominator") or 0)
    if numerator <= 0 or denominator <= 0:
        raise MotionCaptureError("受治理时间轴的 time base 无效")
    min_confidence = min(0.9, max(0.1, float(request.get("minimumConfidence") or 0.5)))
    pose: list[Any] = []
    pose_world: list[Any] = []
    left_hand: list[Any] = []
    left_hand_world: list[Any] = []
    right_hand: list[Any] = []
    right_hand_world: list[Any] = []
    inference_timestamps: list[int] = []
    previous_timestamp = -1
    execution_provider = "CPU"
    with _create_detector(model_path, min_confidence=min_confidence) as detector:
        execution_provider = str(getattr(detector, "execution_provider", "CPU") or "CPU")
        for frame_index, rgb_frame in enumerate(_iter_rgb_frames(source_path)):
            if frame_index >= frame_count:
                raise MotionCaptureError("解码帧数超过 ffprobe 时间轴；拒绝写入错位动作数据")
            exact_ms = int(round(int(boundary_ticks[frame_index]) * numerator * 1000 / denominator))
            timestamp_ms = max(previous_timestamp + 1, exact_ms)
            previous_timestamp = timestamp_ms
            inference_timestamps.append(timestamp_ms)
            result = detector.detect(rgb_frame, timestamp_ms)
            pose.append(_landmark_frame(getattr(result, "pose_landmarks", None), POSE_LANDMARK_COUNT))
            pose_world.append(_landmark_frame(getattr(result, "pose_world_landmarks", None), POSE_LANDMARK_COUNT))
            left_hand.append(_landmark_frame(getattr(result, "left_hand_landmarks", None), HAND_LANDMARK_COUNT))
            left_hand_world.append(_landmark_frame(getattr(result, "left_hand_world_landmarks", None), HAND_LANDMARK_COUNT))
            right_hand.append(_landmark_frame(getattr(result, "right_hand_landmarks", None), HAND_LANDMARK_COUNT))
            right_hand_world.append(_landmark_frame(getattr(result, "right_hand_world_landmarks", None), HAND_LANDMARK_COUNT))
    decoded_count = len(pose)
    if decoded_count != frame_count:
        raise MotionCaptureError(
            f"动作提取逐帧校验失败：ffprobe={frame_count}, decoded={decoded_count}"
        )
    pose_array = np.stack(pose).astype(np.float32, copy=False)
    qa = _qa_summary(pose_array, frame_count=frame_count, min_confidence=min_confidence)
    manifest = {
        "schema": MOTION_SCHEMA,
        "version": 1,
        "source": {
            "resource": resource,
            "fingerprint": probe.get("fingerprint"),
            "frameCount": frame_count,
            "timeBase": {"numerator": numerator, "denominator": denominator},
            "durationSeconds": timeline.get("durationSeconds"),
            "variableFrameRate": timeline.get("variableFrameRate"),
        },
        "capture": {
            "people": 1,
            "executionProvider": execution_provider,
            "faceLandmarksStored": False,
            "coordinateSystems": {
                "normalized": "MediaPipe image-normalized x/y and relative z",
                "world": "MediaPipe metric world coordinates centered at the body or hand origin",
            },
            "decodeSize": [DECODE_SIZE, DECODE_SIZE],
        },
        "model": {
            "featurePackId": "creative_media_motion_capture",
            "assetId": "holistic_landmarker",
            "sha256": _sha256_file(model_path),
        },
        "qa": qa,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        np.savez_compressed(
            handle,
            manifest_json=_manifest_bytes(manifest),
            frame_index=np.arange(frame_count, dtype=np.int64),
            pts_ticks=boundary_ticks,
            inference_timestamp_ms=np.asarray(inference_timestamps, dtype=np.int64),
            pose=pose_array,
            pose_world=np.stack(pose_world).astype(np.float32, copy=False),
            left_hand=np.stack(left_hand).astype(np.float32, copy=False),
            left_hand_world=np.stack(left_hand_world).astype(np.float32, copy=False),
            right_hand=np.stack(right_hand).astype(np.float32, copy=False),
            right_hand_world=np.stack(right_hand_world).astype(np.float32, copy=False),
        )
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise MotionCaptureError("动作数据包写入失败")
    return {
        "schema": MOTION_SCHEMA,
        "manifest": manifest,
        "byteSize": output_path.stat().st_size,
        "providerInvoked": False,
        "engine": {
            "timelineAuthority": "ffprobe",
            "decodeAuthority": "ffmpeg",
            "landmarkAuthority": "mediapipe_holistic",
            "windowless": True,
        },
    }


def inspect_motion_package(path: Path) -> dict[str, Any]:
    try:
        import numpy as np
        with np.load(path, allow_pickle=False) as package:
            payload = bytes(package["manifest_json"].tolist())
        manifest = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MotionCaptureError("动作数据包损坏或格式不受支持") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != MOTION_SCHEMA:
        raise MotionCaptureError("动作数据包 schema 不受支持")
    return manifest


def _motion_guidance_frame(pose: Any, left_hand: Any, right_hand: Any, *, frame_index: int) -> Any:
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise MotionCaptureError("Motion guidance rendering requires the managed Pillow and NumPy runtime") from exc

    image = Image.new("RGB", (MOTION_GUIDANCE_SIZE, MOTION_GUIDANCE_SIZE), (25, 20, 18))
    drawer = ImageDraw.Draw(image)

    def point(item: Any) -> tuple[int, int] | None:
        if item.shape[0] < 2 or not bool(np.isfinite(item[:2]).all()):
            return None
        x = int(round(float(item[0]) * (MOTION_GUIDANCE_SIZE - 1)))
        y = int(round(float(item[1]) * (MOTION_GUIDANCE_SIZE - 1)))
        return max(0, min(x, MOTION_GUIDANCE_SIZE - 1)), max(0, min(y, MOTION_GUIDANCE_SIZE - 1))

    def draw(points: Any, connections: tuple[tuple[int, int], ...], color: tuple[int, int, int]) -> None:
        resolved = [point(item) for item in points]
        for start, end in connections:
            if start < len(resolved) and end < len(resolved) and resolved[start] and resolved[end]:
                drawer.line((resolved[start], resolved[end]), fill=color, width=4)
        for item in resolved:
            if item:
                x, y = item
                drawer.ellipse((x - 7, y - 7, x + 7, y + 7), outline=color, width=2)
                drawer.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(250, 247, 245))

    draw(pose, POSE_CONNECTIONS, (80, 210, 255))
    draw(left_hand, HAND_CONNECTIONS, (255, 145, 95))
    draw(right_hand, HAND_CONNECTIONS, (185, 105, 255))
    drawer.text((28, 28), f"V8 MOTION  |  FRAME {frame_index + 1}", fill=(235, 225, 220))
    return np.asarray(image, dtype=np.uint8)


def _write_motion_guidance_video(frames: Iterator[Any], *, output_path: Path, fps: Fraction) -> None:
    ffmpeg, _, _ = governed_ffmpeg_pair()
    command = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s:v", f"{MOTION_GUIDANCE_SIZE}x{MOTION_GUIDANCE_SIZE}",
        "-r", f"{fps.numerator}/{fps.denominator}",
        "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            **windowless_subprocess_kwargs(),
        )
        try:
            if process.stdin is None:
                raise MotionCaptureError("Governed ffmpeg encoder has no input stream")
            for frame in frames:
                process.stdin.write(frame.tobytes(order="C"))
            process.stdin.close()
            return_code = process.wait(timeout=120)
            if return_code != 0:
                stderr_file.seek(0)
                detail = stderr_file.read().decode("utf-8", errors="replace").strip().splitlines()
                raise MotionCaptureError((detail[-1] if detail else "Governed ffmpeg encode failed")[-360:])
        finally:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def render_motion_guidance_video(motion_path: Path, *, output_path: Path) -> dict[str, Any]:
    try:
        import numpy as np
        with np.load(motion_path, allow_pickle=False) as package:
            manifest = json.loads(bytes(package["manifest_json"].tolist()).decode("utf-8"))
            pose = package["pose"].copy()
            left_hand = package["left_hand"].copy()
            right_hand = package["right_hand"].copy()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MotionCaptureError("动作数据包损坏或格式不受支持") from exc
    if manifest.get("schema") != MOTION_SCHEMA:
        raise MotionCaptureError("动作数据包 schema 不受支持")
    frame_count = int(pose.shape[0])
    duration_seconds = float(((manifest.get("source") or {}).get("durationSeconds") or 0))
    if frame_count <= 0 or duration_seconds <= 0:
        raise MotionCaptureError("动作数据包缺少可渲染的逐帧时间轴")
    fps = Fraction(frame_count, 1) / Fraction(str(duration_seconds))
    fps = fps.limit_denominator(100_000)
    frames = (
        _motion_guidance_frame(pose[index], left_hand[index], right_hand[index], frame_index=index)
        for index in range(frame_count)
    )
    _write_motion_guidance_video(frames, output_path=output_path, fps=fps)
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise MotionCaptureError("动作指导视频写入失败")
    variable_frame_rate = bool(((manifest.get("source") or {}).get("variableFrameRate")))
    return {
        "schema": MOTION_GUIDANCE_SCHEMA,
        "sourceMotionSchema": MOTION_SCHEMA,
        "frameCount": frame_count,
        "durationSeconds": duration_seconds,
        "outputFps": {"numerator": fps.numerator, "denominator": fps.denominator},
        "timingMode": "average_cfr_from_exact_motion_timeline",
        "warnings": ["source_vfr_rendered_as_average_cfr"] if variable_frame_rate else [],
        "providerInvoked": False,
        "engine": {"renderer": "pillow", "encoder": "governed_ffmpeg", "windowless": True},
    }


def read_motion_frame(path: Path, frame_index: int) -> dict[str, Any]:
    try:
        import numpy as np
        with np.load(path, allow_pickle=False) as package:
            count = int(package["frame_index"].shape[0])
            if frame_index < 0 or frame_index >= count:
                raise MotionCaptureError("动作帧超出时间轴范围")

            def points(name: str) -> list[list[float | None]]:
                values = package[name][frame_index]
                return [
                    [float(item[0]), float(item[1]), float(item[2]), float(item[3])]
                    if bool(np.isfinite(item).all()) else [None, None, None, None]
                    for item in values
                ]

            return {
                "schema": "v8.motion_capture.frame.v1",
                "frameIndex": frame_index,
                "ptsTicks": int(package["pts_ticks"][frame_index]),
                "inferenceTimestampMs": int(package["inference_timestamp_ms"][frame_index]),
                "pose": points("pose"),
                "leftHand": points("left_hand"),
                "rightHand": points("right_hand"),
            }
    except MotionCaptureError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise MotionCaptureError("动作数据包损坏或格式不受支持") from exc


__all__ = [
    "MOTION_MIME_TYPE",
    "MOTION_SCHEMA",
    "MotionCaptureError",
    "extract_holistic_motion",
    "inspect_motion_package",
    "read_motion_frame",
    "render_motion_guidance_video",
]
