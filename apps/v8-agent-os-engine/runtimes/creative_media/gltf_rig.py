from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

from .governed_media import resolve_governed_media_path


RIG_SCHEMA = "v8.rig_profile.v1"
GLB_MAGIC = b"glTF"
GLB_JSON_CHUNK = 0x4E4F534A
MAX_GLTF_JSON_BYTES = 64 * 1024 * 1024


class RigInspectionError(ValueError):
    pass


HUMANOID_ALIASES = {
    "hips": ("hips", "pelvis", "roothips", "mixamorighips"),
    "spine": ("spine", "spine1", "spine01", "mixamorigspine"),
    "chest": ("chest", "spine2", "spine02", "mixamorigspine1"),
    "upper_chest": ("upperchest", "spine3", "spine03", "mixamorigspine2"),
    "neck": ("neck", "neck1", "mixamorigneck"),
    "head": ("head", "mixamorighead"),
    "left_shoulder": ("leftshoulder", "lshoulder", "shoulderl", "mixamorigleftshoulder"),
    "left_upper_arm": ("leftarm", "leftupperarm", "lupperarm", "upperarml", "mixamorigleftarm"),
    "left_lower_arm": ("leftforearm", "leftlowerarm", "lforearm", "lowerarml", "mixamorigleftforearm"),
    "left_hand": ("lefthand", "lhand", "handl", "mixamoriglefthand"),
    "right_shoulder": ("rightshoulder", "rshoulder", "shoulderr", "mixamorigrightshoulder"),
    "right_upper_arm": ("rightarm", "rightupperarm", "rupperarm", "upperarmr", "mixamorigrightarm"),
    "right_lower_arm": ("rightforearm", "rightlowerarm", "rforearm", "lowerarmr", "mixamorigrightforearm"),
    "right_hand": ("righthand", "rhand", "handr", "mixamorigrighthand"),
    "left_upper_leg": ("leftupleg", "leftupperleg", "lthigh", "thighl", "mixamorigleftupleg"),
    "left_lower_leg": ("leftleg", "leftlowerleg", "lcalf", "calfl", "mixamorigleftleg"),
    "left_foot": ("leftfoot", "lfoot", "footl", "mixamorigleftfoot"),
    "left_toes": ("lefttoe", "lefttoebase", "ltoe", "mixamoriglefttoebase"),
    "right_upper_leg": ("rightupleg", "rightupperleg", "rthigh", "thighr", "mixamorigrightupleg"),
    "right_lower_leg": ("rightleg", "rightlowerleg", "rcalf", "calfr", "mixamorigrightleg"),
    "right_foot": ("rightfoot", "rfoot", "footr", "mixamorigrightfoot"),
    "right_toes": ("righttoe", "righttoebase", "rtoe", "mixamorigrighttoebase"),
}
REQUIRED_RETARGET_BONES = {
    "hips", "spine", "head",
    "left_upper_arm", "left_lower_arm", "left_hand",
    "right_upper_arm", "right_lower_arm", "right_hand",
    "left_upper_leg", "left_lower_leg", "left_foot",
    "right_upper_leg", "right_lower_leg", "right_foot",
}


def _normalize_bone_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _load_gltf_json(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".gltf":
        if path.stat().st_size > MAX_GLTF_JSON_BYTES:
            raise RigInspectionError("glTF JSON exceeds the governed inspection limit")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RigInspectionError("glTF JSON is invalid") from exc
    elif suffix == ".glb":
        try:
            with path.open("rb") as handle:
                header = handle.read(12)
                if len(header) != 12:
                    raise RigInspectionError("GLB header is incomplete")
                magic, version, total_length = struct.unpack("<4sII", header)
                if magic != GLB_MAGIC or version != 2 or total_length != path.stat().st_size:
                    raise RigInspectionError("GLB header is invalid or unsupported")
                chunk_header = handle.read(8)
                if len(chunk_header) != 8:
                    raise RigInspectionError("GLB JSON chunk is missing")
                chunk_length, chunk_type = struct.unpack("<II", chunk_header)
                if chunk_type != GLB_JSON_CHUNK or chunk_length > MAX_GLTF_JSON_BYTES:
                    raise RigInspectionError("GLB JSON chunk is invalid")
                payload = json.loads(handle.read(chunk_length).rstrip(b"\x00 \t\r\n").decode("utf-8"))
        except RigInspectionError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, struct.error) as exc:
            raise RigInspectionError("GLB structure is invalid") from exc
    else:
        raise RigInspectionError("Rig inspection only accepts GLB or GLTF")
    if not isinstance(payload, dict) or str(dict(payload.get("asset") or {}).get("version") or "")[:1] != "2":
        raise RigInspectionError("Only glTF 2.x assets are supported")
    return payload


def _humanoid_mapping(joint_names: list[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    normalized = {name: _normalize_bone_name(name) for name in joint_names if name}
    mapping: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    for canonical, aliases in HUMANOID_ALIASES.items():
        exact = [name for name, value in normalized.items() if value in aliases]
        if len(exact) == 1:
            mapping[canonical] = exact[0]
            continue
        if len(exact) > 1:
            ambiguous[canonical] = sorted(exact)
            continue
        suffix = [name for name, value in normalized.items() if any(value.endswith(alias) for alias in aliases)]
        if len(suffix) == 1:
            mapping[canonical] = suffix[0]
        elif len(suffix) > 1:
            ambiguous[canonical] = sorted(suffix)
    return mapping, ambiguous


def inspect_rig_path(path: Path) -> dict[str, Any]:
    payload = _load_gltf_json(path)
    nodes = [dict(item) if isinstance(item, dict) else {} for item in list(payload.get("nodes") or [])]
    skins = [dict(item) if isinstance(item, dict) else {} for item in list(payload.get("skins") or [])]
    skeletons: list[dict[str, Any]] = []
    all_joint_names: list[str] = []
    for skin_index, skin in enumerate(skins):
        joint_indices = [int(item) for item in list(skin.get("joints") or []) if isinstance(item, int) and 0 <= item < len(nodes)]
        joint_names = [str(nodes[index].get("name") or f"bone_{index}") for index in joint_indices]
        all_joint_names.extend(joint_names)
        skeleton_index = skin.get("skeleton")
        skeletons.append({
            "skinIndex": skin_index,
            "name": str(skin.get("name") or f"Skin {skin_index + 1}"),
            "jointCount": len(joint_indices),
            "jointNames": joint_names,
            "rootJoint": str(nodes[skeleton_index].get("name") or f"bone_{skeleton_index}")
            if isinstance(skeleton_index, int) and 0 <= skeleton_index < len(nodes)
            else None,
        })
    unique_joint_names = list(dict.fromkeys(all_joint_names))
    mapping, ambiguous = _humanoid_mapping(unique_joint_names)
    missing = sorted(REQUIRED_RETARGET_BONES - set(mapping))
    animations = []
    for index, raw_animation in enumerate(list(payload.get("animations") or [])):
        animation = dict(raw_animation) if isinstance(raw_animation, dict) else {}
        channels = [dict(item) for item in list(animation.get("channels") or []) if isinstance(item, dict)]
        target_nodes = list(dict.fromkeys(
            str(nodes[node_index].get("name") or f"node_{node_index}")
            for item in channels
            for node_index in [dict(item.get("target") or {}).get("node")]
            if isinstance(node_index, int) and 0 <= node_index < len(nodes)
        ))
        animations.append({
            "animationIndex": index,
            "name": str(animation.get("name") or f"Animation {index + 1}"),
            "channelCount": len(channels),
            "targetNodes": target_nodes,
        })
    return {
        "schema": RIG_SCHEMA,
        "version": 1,
        "format": path.suffix.lower().lstrip("."),
        "nodeCount": len(nodes),
        "skinCount": len(skins),
        "jointCount": len(unique_joint_names),
        "rigged": bool(skins and unique_joint_names),
        "skeletons": skeletons,
        "humanoidMapping": mapping,
        "ambiguousBones": ambiguous,
        "missingRequiredBones": missing,
        "animations": animations,
        "readyForGodotRetarget": bool(skins and unique_joint_names and not missing and not ambiguous),
        "adapter": {
            "id": "godot_humanoid_v1",
            "automaticRigging": False,
            "people": 1,
        },
    }


def inspect_rigged_model(request: dict[str, Any]) -> dict[str, Any]:
    path, resource = resolve_governed_media_path(request)
    profile = inspect_rig_path(path)
    return {**profile, "resource": resource}


__all__ = ["RIG_SCHEMA", "RigInspectionError", "inspect_rig_path", "inspect_rigged_model"]
