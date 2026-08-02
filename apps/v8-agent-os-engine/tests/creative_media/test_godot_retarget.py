from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from runtimes.creative_media import godot_retarget
from runtimes.creative_media.motion_capture import MOTION_SCHEMA


def _rig_payload(*, complete: bool = True) -> dict:
    names = [
        "Hips", "Spine", "Head",
        "LeftArm", "LeftForeArm", "LeftHand",
        "RightArm", "RightForeArm", "RightHand",
        "LeftUpLeg", "LeftLeg", "LeftFoot",
        "RightUpLeg", "RightLeg", "RightFoot",
    ]
    if not complete:
        names = names[:3]
    return {
        "asset": {"version": "2.0"},
        "nodes": [{"name": name} for name in names],
        "skins": [{"joints": list(range(len(names))), "skeleton": 0}],
    }


def _write_motion(path) -> None:
    pose_world = np.zeros((2, 33, 4), dtype=np.float32)
    pose_world[:, :, 3] = 0.95
    for frame_index in range(2):
        pose_world[frame_index, :, 0] = np.linspace(-0.5, 0.5, 33)
        pose_world[frame_index, :, 1] = np.linspace(1.0, 0.0, 33)
        pose_world[frame_index, :, 2] = frame_index * 0.05
    manifest = {
        "schema": MOTION_SCHEMA,
        "source": {
            "frameCount": 2,
            "timeBase": {"numerator": 1, "denominator": 30000},
        },
    }
    manifest_bytes = np.frombuffer(
        json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
        dtype=np.uint8,
    )
    with path.open("wb") as handle:
        np.savez_compressed(
            handle,
            manifest_json=manifest_bytes,
            frame_index=np.array([0, 1], dtype=np.int64),
            pts_ticks=np.array([0, 1001], dtype=np.int64),
            pose_world=pose_world,
        )


def test_build_godot_motion_payload_preserves_exact_timebase(tmp_path):
    motion_path = tmp_path / "walk.v8motion"
    _write_motion(motion_path)

    payload = godot_retarget.build_godot_motion_payload(
        motion_path,
        mapping={"hips": "Hips", "left_upper_arm": "LeftArm"},
    )

    assert payload["frameCount"] == 2
    assert payload["times"] == [0.0, 0.033366667]
    assert payload["rootMotion"] is False
    assert len(payload["directions"]["left_upper_arm"]) == 2


def test_retarget_uses_configured_godot_and_records_closed_proof(monkeypatch, tmp_path):
    motion_path = tmp_path / "walk.v8motion"
    model_path = tmp_path / "character.gltf"
    output_path = tmp_path / "animated.glb"
    _write_motion(motion_path)
    model_path.write_text(json.dumps(_rig_payload()), encoding="utf-8")
    executable = tmp_path / "Godot.exe"
    executable.write_bytes(b"configured")
    monkeypatch.setattr(
        godot_retarget,
        "_configured_godot_runtime",
        lambda: {"executable": executable, "version": "4.6-test"},
    )

    def fake_run(command, **_kwargs):
        assert command[0] == str(executable)
        assert "--headless" in command
        assert command[-1] == str(output_path.resolve())
        output_path.write_bytes(b"glTF-export")
        return subprocess.CompletedProcess(command, 0, stdout="V8_RETARGET_OK", stderr="")

    monkeypatch.setattr(godot_retarget, "run_windowless", fake_run)

    proof = godot_retarget.retarget_motion_with_godot(
        motion_path=motion_path,
        model_path=model_path,
        output_path=output_path,
    )

    assert proof["adapter"] == "godot_humanoid_v1"
    assert proof["godotVersion"] == "4.6-test"
    assert proof["rootMotion"] is False
    assert proof["automaticRigging"] is False
    assert proof["providerInvoked"] is False
    assert proof["output"]["byteSize"] == len(b"glTF-export")


def test_retarget_rejects_incomplete_rig_before_godot_execution(monkeypatch, tmp_path):
    motion_path = tmp_path / "walk.v8motion"
    model_path = tmp_path / "partial.gltf"
    _write_motion(motion_path)
    model_path.write_text(json.dumps(_rig_payload(complete=False)), encoding="utf-8")
    monkeypatch.setattr(
        godot_retarget,
        "_configured_godot_runtime",
        lambda: {"executable": tmp_path / "Godot.exe", "version": "4.6-test"},
    )

    with pytest.raises(godot_retarget.GodotRetargetError, match="骨骼映射不完整"):
        godot_retarget.retarget_motion_with_godot(
            motion_path=motion_path,
            model_path=model_path,
            output_path=tmp_path / "animated.glb",
        )
