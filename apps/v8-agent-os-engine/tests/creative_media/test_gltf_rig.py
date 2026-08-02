from __future__ import annotations

import json
import struct

import pytest

from runtimes.creative_media.gltf_rig import RigInspectionError, inspect_rig_path


def _rig_payload():
    names = [
        "Hips", "Spine", "Head",
        "LeftArm", "LeftForeArm", "LeftHand",
        "RightArm", "RightForeArm", "RightHand",
        "LeftUpLeg", "LeftLeg", "LeftFoot",
        "RightUpLeg", "RightLeg", "RightFoot",
    ]
    return {
        "asset": {"version": "2.0"},
        "nodes": [{"name": name} for name in names],
        "skins": [{"name": "Body", "joints": list(range(len(names))), "skeleton": 0}],
        "animations": [{"name": "Idle", "channels": [{"target": {"node": 0, "path": "rotation"}}], "samplers": [{}]}],
    }


def test_inspect_gltf_humanoid_rig(tmp_path):
    path = tmp_path / "character.gltf"
    path.write_text(json.dumps(_rig_payload()), encoding="utf-8")

    result = inspect_rig_path(path)

    assert result["rigged"] is True
    assert result["readyForGodotRetarget"] is True
    assert result["humanoidMapping"]["left_lower_arm"] == "LeftForeArm"
    assert result["jointCount"] == 15
    assert result["animations"][0]["name"] == "Idle"


def test_inspect_glb_uses_json_chunk_without_loading_binary_payload(tmp_path):
    json_payload = json.dumps(_rig_payload(), separators=(",", ":")).encode("utf-8")
    json_payload += b" " * ((4 - len(json_payload) % 4) % 4)
    binary = b"binary-mesh-data"
    binary += b"\x00" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(json_payload) + 8 + len(binary)
    path = tmp_path / "character.glb"
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_payload), 0x4E4F534A) + json_payload
        + struct.pack("<II", len(binary), 0x004E4942) + binary
    )

    result = inspect_rig_path(path)

    assert result["format"] == "glb"
    assert result["readyForGodotRetarget"] is True


def test_inspect_rig_reports_missing_required_bones(tmp_path):
    payload = _rig_payload()
    payload["nodes"] = [{"name": "Hips"}, {"name": "Spine"}, {"name": "Head"}]
    payload["skins"][0]["joints"] = [0, 1, 2]
    path = tmp_path / "partial.gltf"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = inspect_rig_path(path)

    assert result["rigged"] is True
    assert result["readyForGodotRetarget"] is False
    assert "left_hand" in result["missingRequiredBones"]


def test_inspect_rig_rejects_unrigged_and_malformed_assets(tmp_path):
    unrigged = tmp_path / "mesh.gltf"
    unrigged.write_text(json.dumps({"asset": {"version": "2.0"}, "nodes": [{"name": "Mesh"}]}), encoding="utf-8")
    assert inspect_rig_path(unrigged)["rigged"] is False

    malformed = tmp_path / "bad.glb"
    malformed.write_bytes(b"not-a-glb")
    with pytest.raises(RigInspectionError):
        inspect_rig_path(malformed)
