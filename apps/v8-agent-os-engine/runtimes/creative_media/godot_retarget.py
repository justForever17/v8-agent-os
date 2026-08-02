from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any

from core.process_launch import run_windowless

from .gltf_rig import inspect_rig_path
from .motion_capture import inspect_motion_package


RETARGET_SCHEMA = "v8.godot_humanoid_retarget.v1"
GODOT_SCRIPT = Path(__file__).resolve().parent / "resources" / "godot_humanoid_retarget.gd"


class GodotRetargetError(RuntimeError):
    pass


def _configured_godot_runtime() -> dict[str, Any]:
    from runtimes.plugin_manager import plugin_manager_service

    try:
        setup = plugin_manager_service.plugin_setup("godot", probe=False)
    except Exception as exc:
        raise GodotRetargetError("Godot 插件配置不可用；请先在插件管理中心完成 Godot 接入。") from exc
    status = dict(setup.get("status") or {})
    if not status.get("offlinePrerequisitesReady"):
        raise GodotRetargetError("Godot 应用、项目或开发场景尚未通过离线配置校验。")
    if str(setup.get("scenario") or "").lower() != "3d":
        raise GodotRetargetError("当前 Godot 开发场景不是 3D；动作重定向不会覆盖用户配置。")
    executable = Path(str(setup.get("godotExecutable") or "")).expanduser()
    if not executable.is_file():
        raise GodotRetargetError("用户配置的 Godot 可执行文件不可用。")
    version = str(dict(dict(status.get("steps") or {}).get("application") or {}).get("version") or "")
    return {"executable": executable, "version": version}


def _vector(points: Any, start: int, end: int, *, confidence: float) -> list[float] | None:
    left = points[start]
    right = points[end]
    if not all(math.isfinite(float(value)) for value in [*left[:3], *right[:3]]):
        return None
    if min(float(left[3]), float(right[3])) < confidence:
        return None
    raw = [float(right[0] - left[0]), float(-(right[1] - left[1])), float(-(right[2] - left[2]))]
    length = math.sqrt(sum(value * value for value in raw))
    return [round(value / length, 7) for value in raw] if length > 1e-8 else None


def _midpoint(points: Any, left: int, right: int) -> list[float] | None:
    values = points[[left, right]]
    if not all(math.isfinite(float(value)) for value in values[:, :3].reshape(-1)):
        return None
    return [float((values[0][axis] + values[1][axis]) / 2) for axis in range(3)] + [float(min(values[0][3], values[1][3]))]


def _vector_values(left: list[float] | None, right: list[float] | None, *, confidence: float) -> list[float] | None:
    if left is None or right is None or min(left[3], right[3]) < confidence:
        return None
    raw = [
        float(right[0] - left[0]),
        float(-(right[1] - left[1])),
        float(-(right[2] - left[2])),
    ]
    length = math.sqrt(sum(value * value for value in raw))
    return [round(value / length, 7) for value in raw] if length > 1e-8 else None


def build_godot_motion_payload(motion_path: Path, *, mapping: dict[str, str], minimum_confidence: float = 0.5) -> dict[str, Any]:
    try:
        import numpy as np
        with np.load(motion_path, allow_pickle=False) as package:
            pose = package["pose_world"].astype(np.float32, copy=False)
            pts_ticks = package["pts_ticks"].astype(np.int64, copy=False)
    except (ImportError, OSError, ValueError, KeyError) as exc:
        raise GodotRetargetError("动作数据包无法用于 Godot 重定向。") from exc
    manifest = inspect_motion_package(motion_path)
    time_base = dict(dict(manifest.get("source") or {}).get("timeBase") or {})
    numerator = int(time_base.get("numerator") or 0)
    denominator = int(time_base.get("denominator") or 0)
    if numerator <= 0 or denominator <= 0 or pose.shape[0] != pts_ticks.shape[0]:
        raise GodotRetargetError("动作数据包时间轴无效。")
    directions: dict[str, list[list[float] | None]] = {canonical: [] for canonical in mapping}
    for frame in pose:
        hips = _midpoint(frame, 23, 24)
        shoulders = _midpoint(frame, 11, 12)
        ears = _midpoint(frame, 7, 8)
        frame_directions = {
            "hips": _vector_values(hips, shoulders, confidence=minimum_confidence),
            "spine": _vector_values(hips, shoulders, confidence=minimum_confidence),
            "chest": _vector_values(hips, shoulders, confidence=minimum_confidence),
            "upper_chest": _vector_values(hips, shoulders, confidence=minimum_confidence),
            "neck": _vector_values(shoulders, ears, confidence=minimum_confidence),
            "head": _vector_values(shoulders, ears, confidence=minimum_confidence),
            "left_shoulder": _vector(frame, 11, 12, confidence=minimum_confidence),
            "left_upper_arm": _vector(frame, 11, 13, confidence=minimum_confidence),
            "left_lower_arm": _vector(frame, 13, 15, confidence=minimum_confidence),
            "left_hand": _vector_values(
                [*frame[15][:3], float(frame[15][3])],
                _midpoint(frame, 17, 19),
                confidence=minimum_confidence,
            ),
            "right_shoulder": _vector(frame, 12, 11, confidence=minimum_confidence),
            "right_upper_arm": _vector(frame, 12, 14, confidence=minimum_confidence),
            "right_lower_arm": _vector(frame, 14, 16, confidence=minimum_confidence),
            "right_hand": _vector_values(
                [*frame[16][:3], float(frame[16][3])],
                _midpoint(frame, 18, 20),
                confidence=minimum_confidence,
            ),
            "left_upper_leg": _vector(frame, 23, 25, confidence=minimum_confidence),
            "left_lower_leg": _vector(frame, 25, 27, confidence=minimum_confidence),
            "left_foot": _vector(frame, 27, 31, confidence=minimum_confidence),
            "right_upper_leg": _vector(frame, 24, 26, confidence=minimum_confidence),
            "right_lower_leg": _vector(frame, 26, 28, confidence=minimum_confidence),
            "right_foot": _vector(frame, 28, 32, confidence=minimum_confidence),
        }
        for canonical in directions:
            directions[canonical].append(frame_directions.get(canonical))
    times = [round(int(value) * numerator / denominator, 9) for value in pts_ticks]
    return {
        "schema": "v8.godot_humanoid_motion_input.v1",
        "times": times,
        "mapping": mapping,
        "directions": directions,
        "rootMotion": False,
        "frameCount": len(times),
    }


def retarget_motion_with_godot(
    *,
    motion_path: Path,
    model_path: Path,
    output_path: Path,
    minimum_confidence: float = 0.5,
) -> dict[str, Any]:
    runtime = _configured_godot_runtime()
    rig_profile = inspect_rig_path(model_path)
    if not rig_profile.get("rigged"):
        raise GodotRetargetError("目标 GLB/GLTF 没有 skin/joints；首版不提供自动绑骨。")
    if not rig_profile.get("readyForGodotRetarget"):
        missing = ",".join(list(rig_profile.get("missingRequiredBones") or [])[:8])
        raise GodotRetargetError(f"目标模型的人形骨骼映射不完整：{missing or 'ambiguous_bones'}")
    motion_payload = build_godot_motion_payload(
        motion_path,
        mapping=dict(rig_profile.get("humanoidMapping") or {}),
        minimum_confidence=min(0.9, max(0.1, minimum_confidence)),
    )
    if not GODOT_SCRIPT.is_file():
        raise GodotRetargetError("Godot 重定向脚本缺失。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v8-godot-retarget-", dir=output_path.parent) as temp_value:
        temp_root = Path(temp_value)
        (temp_root / "project.godot").write_text(
            '[application]\nconfig/name="V8 Motion Retarget"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
            encoding="utf-8",
        )
        motion_json = temp_root / "motion.json"
        motion_json.write_text(json.dumps(motion_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        command = [
            str(runtime["executable"]),
            "--headless",
            "--path", str(temp_root),
            "--script", str(GODOT_SCRIPT),
            "--",
            str(model_path.resolve()),
            str(motion_json.resolve()),
            str(output_path.resolve()),
        ]
        completed = run_windowless(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(300, min(1800, int(motion_payload["frameCount"] / 10) + 180)),
            check=False,
        )
        if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
            detail = str(completed.stderr or completed.stdout or "Godot headless export failed").strip().splitlines()
            raise GodotRetargetError((detail[-1] if detail else "Godot headless export failed")[-360:])
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        "schema": RETARGET_SCHEMA,
        "adapter": "godot_humanoid_v1",
        "godotVersion": runtime.get("version"),
        "frameCount": motion_payload["frameCount"],
        "rootMotion": False,
        "automaticRigging": False,
        "output": {"byteSize": output_path.stat().st_size, "sha256": digest},
        "providerInvoked": False,
    }


__all__ = [
    "GodotRetargetError",
    "RETARGET_SCHEMA",
    "build_godot_motion_payload",
    "retarget_motion_with_godot",
]
