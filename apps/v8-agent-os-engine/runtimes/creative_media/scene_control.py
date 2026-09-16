"""Typed proxy scenes and deterministic control media; execution stays in Creative Media.

The software renderer is deliberately a primitive scene renderer. Its lossless ID
and depth passes describe the proxy, never a claim about a generative model output.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

from core.process_launch import windowless_subprocess_kwargs
from .governed_media import governed_ffmpeg_pair

SCENE_SCHEMA = "v8.proxy_scene.v1"
PACK_SCHEMA = "v8.proxy_scene_control_pack.v1"
CONSUMPTION_SCHEMA = "v8.proxy_scene_provider_consumption.v1"
REFERENCE_ROLES = {"front", "left", "right", "back", "face", "detail", "material", "style", "scale"}
BINDING_FIELDS = ("entityId", "bindingKey", "semanticRole", "purpose", "resourceDigest")


class SceneControlError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise SceneControlError(f"{label} must be a finite number between {low} and {high}")
    return float(value)


def _vector(value: Any, label: str, low: float = -100, high: float = 100) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise SceneControlError(f"{label} requires three coordinates")
    return [_number(item, label, low, high) for item in value]


def _color(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise SceneControlError("Proxy color requires #RRGGBB")
    return value.lower()


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", value):
        raise SceneControlError(f"{label} is invalid")
    return value


def _keys(values: Any, duration: float, *, camera: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not 1 <= len(values) <= 120:
        raise SceneControlError("Motion and camera require 1 to 120 keyframes")
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise SceneControlError("Keyframe must be an object")
        frame = {"time": _number(value.get("time"), "Keyframe time", 0, duration), "position": _vector(value.get("position"), "Keyframe position")}
        if camera:
            frame["target"] = _vector(value.get("target"), "Camera target")
            if math.dist(frame["target"], frame["position"]) < 0.1:
                raise SceneControlError("Camera position must differ from its target")
        else:
            frame["rotation"] = _vector(value.get("rotation", [0, 0, 0]), "Rotation", -3600, 3600)
            pose = value.get("pose") or {}
            if not isinstance(pose, dict) or set(pose) - {"leftArm", "rightArm", "leftLeg", "rightLeg"}:
                raise SceneControlError("Primitive pose supports leftArm/rightArm/leftLeg/rightLeg angles")
            frame["pose"] = {key: _number(pose.get(key, 0), key, -180, 180) for key in ("leftArm", "rightArm", "leftLeg", "rightLeg")}
        if result and frame["time"] <= result[-1]["time"]:
            raise SceneControlError("Keyframe times must be strictly increasing")
        result.append(frame)
    if result[0]["time"] != 0:
        raise SceneControlError("Motion and camera must start at time zero")
    return result


def normalize_scene(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SCENE_SCHEMA:
        raise SceneControlError(f"Scene requires schema {SCENE_SCHEMA}")
    duration = _number(value.get("durationSeconds", 4), "Duration", 1, 15)
    fps = _number(value.get("fps", 24), "FPS", 1, 60)
    width = int(_number(value.get("width", 640), "Width", 256, 1280))
    height = int(_number(value.get("height", 360), "Height", 256, 1280))
    if width % 2 or height % 2:
        raise SceneControlError("Scene video dimensions must be even")
    if width * height * math.ceil(duration * fps) > 280_000_000:
        raise SceneControlError("Scene exceeds local render budget; reduce resolution, duration or FPS explicitly")
    raw_entities = value.get("entities")
    if not isinstance(raw_entities, list) or not 1 <= len(raw_entities) <= 12:
        raise SceneControlError("Scene requires 1 to 12 entities")
    entities, ids, colors = [], set(), set()
    for item in raw_entities:
        if not isinstance(item, dict):
            raise SceneControlError("Entity must be an object")
        entity_id = _id(item.get("entityId"), "Entity identity")
        color = _color(item.get("proxyColor"))
        if entity_id in ids or color in colors or color == "#000000":
            raise SceneControlError("Entities require unique identities and nonblack distinct proxy colors")
        ids.add(entity_id)
        colors.add(color)
        if item.get("shape") not in {"capsule", "box", "sphere"} or item.get("kind") not in {"character", "object", "environment"}:
            raise SceneControlError("Entity kind or primitive shape is unsupported")
        entity = {"entityId": entity_id, "name": str(item.get("name") or ""), "kind": item["kind"], "shape": item["shape"], "proxyColor": color,
                  "size": _vector(item.get("size"), "Entity size", 0.05, 50), "position": _vector(item.get("position", [0, 0, 0]), "Position"),
                  "rotation": _vector(item.get("rotation", [0, 0, 0]), "Rotation", -3600, 3600),
                  "appearance": str(item.get("appearance") or ""), "material": str(item.get("material") or "")}
        entity["motion"] = _keys(item.get("motion") or [{"time": 0, "position": entity["position"], "rotation": entity["rotation"]}], duration)
        entities.append(entity)
    camera = value.get("camera") or {}
    if not isinstance(camera, dict) or camera.get("projection", "perspective") != "perspective":
        raise SceneControlError("Primitive renderer supports perspective cameras")
    return {"schema": SCENE_SCHEMA, "durationSeconds": duration, "fps": fps, "width": width, "height": height,
            "background": _color(value.get("background", "#ece9e0")), "style": str(value.get("style") or ""), "entities": entities,
            "camera": {"projection": "perspective", "fov": _number(camera.get("fov", 45), "Camera FOV", 15, 100),
                       "keyframes": _keys(camera.get("keyframes") or [{"time": 0, "position": [0, 3, 8], "target": [0, 1, 0]}], duration, camera=True)}}


def interpolate(keys: list[dict[str, Any]], time: float) -> dict[str, Any]:
    left, right = keys[0], keys[-1]
    for candidate in keys:
        if candidate["time"] <= time:
            left = candidate
        if candidate["time"] >= time:
            right = candidate
            break
    ratio = 0 if right["time"] <= left["time"] else min(1, max(0, (time - left["time"]) / (right["time"] - left["time"])))
    result: dict[str, Any] = {"time": time}
    for key in ("position", "target", "rotation"):
        if key in left:
            result[key] = [a + (b - a) * ratio for a, b in zip(left[key], right[key])]
    if "pose" in left:
        result["pose"] = {key: a + (right["pose"][key] - a) * ratio for key, a in left["pose"].items()}
    return result


def _rotation(angles: list[float]) -> Any:
    import numpy as np
    x, y, z = [math.radians(item) for item in angles]
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def _mesh(shape: str) -> tuple[Any, Any]:
    import numpy as np
    if shape == "box":
        vertices = np.array([[x, y, z] for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)])
        faces = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
        return vertices, np.array(faces)
    vertices, faces = [], []
    for row in range(9):
        latitude = math.pi * row / 8
        for column in range(12):
            angle = 2 * math.pi * column / 12
            vertices.append([0.5 * math.sin(latitude) * math.cos(angle), 0.5 * math.cos(latitude), 0.5 * math.sin(latitude) * math.sin(angle)])
    for row in range(8):
        for column in range(12):
            a, b = row * 12 + column, row * 12 + (column + 1) % 12
            faces.extend([[a, a + 12, b], [b, a + 12, b + 12]])
    return np.array(vertices), np.array(faces)


def render_frame(scene: dict[str, Any], time: float) -> tuple[Any, Any, Any, dict[str, Any]]:
    import numpy as np
    width, height = scene["width"], scene["height"]
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:] = tuple(bytes.fromhex(scene["background"][1:]))
    identity = np.zeros_like(rgb)
    depth = np.full((height, width), np.inf)
    camera = interpolate(scene["camera"]["keyframes"], time)
    eye, target = np.array(camera["position"]), np.array(camera["target"])
    forward = target - eye
    if np.linalg.norm(forward) < 0.05:
        raise SceneControlError("Camera crosses its target during interpolation; adjust the camera path")
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 1, 0])
    if np.linalg.norm(right) < 1e-5:
        right = np.cross(forward, [0, 0, 1])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    view = np.stack([right, up, forward])
    focal = height / (2 * math.tan(math.radians(scene["camera"]["fov"]) / 2))
    geometry = {kind: _mesh(kind) for kind in ("box", "sphere")}
    for entity in scene["entities"]:
        state = interpolate(entity["motion"], time)
        size = np.array(entity["size"])
        parts = [(entity["shape"], [0, 0.5, 0], [1, 1, 1], 0)]
        if entity["shape"] == "capsule":
            pose = state["pose"]
            parts = [("sphere", [0, 0.66, 0], [0.72, 0.39, 0.68], 0), ("sphere", [0, 0.925, 0], [0.49, 0.15, 0.60], 0)]
            for side, sign in (("left", -1), ("right", 1)):
                parts.extend([("arm", [sign * 0.43, 0.79, 0], [0.19, 0.32, 0.28], pose[side + "Arm"]), ("leg", [sign * 0.22, 0.49, 0], [0.27, 0.47, 0.43], pose[side + "Leg"])])
        for kind, center, scale, angle in parts:
            vertices, faces = geometry["box" if kind in {"box", "arm", "leg"} else "sphere"]
            local = vertices * np.array(scale)
            if kind in {"arm", "leg"}:
                local[:, 1] -= scale[1] / 2
                local = local @ _rotation([angle, 0, 0]).T
            world = ((local + np.array(center)) * size) @ _rotation(state["rotation"]).T + np.array(state["position"])
            projected = (world - eye) @ view.T
            for face in faces:
                triangle = projected[face]
                if np.min(triangle[:, 2]) < 0.05:
                    continue
                xy = triangle[:, :2] / triangle[:, 2:3] * focal
                xy[:, 0] += width / 2
                xy[:, 1] = height / 2 - xy[:, 1]
                x0, y0 = np.maximum(np.floor(xy.min(axis=0)).astype(int), [0, 0])
                x1, y1 = np.minimum(np.ceil(xy.max(axis=0)).astype(int), [width - 1, height - 1])
                if x1 < x0 or y1 < y0:
                    continue
                a, b, c = xy
                denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
                if abs(denominator) < 1e-8:
                    continue
                ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
                wa = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / denominator
                wb = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / denominator
                wc = 1 - wa - wb
                reciprocal = wa / triangle[0, 2] + wb / triangle[1, 2] + wc / triangle[2, 2]
                z = np.divide(1, reciprocal, out=np.full_like(reciprocal, np.inf), where=reciprocal > 0)
                window = depth[y0:y1 + 1, x0:x1 + 1]
                visible = (wa >= 0) & (wb >= 0) & (wc >= 0) & (z < window)
                window[visible] = z[visible]
                color = np.array(tuple(bytes.fromhex(entity["proxyColor"][1:])))
                identity[y0:y1 + 1, x0:x1 + 1][visible] = color
                normal = np.cross(world[face[1]] - world[face[0]], world[face[2]] - world[face[0]])
                shade = 0.7 + 0.3 * abs(float(normal @ np.array([0.3, 0.8, 0.5]))) / max(1e-8, float(np.linalg.norm(normal)))
                rgb[y0:y1 + 1, x0:x1 + 1][visible] = np.minimum(255, color * shade).astype(np.uint8)
    depth16 = np.where(np.isfinite(depth), np.minimum(65535, np.where(np.isfinite(depth), depth, 0) * 1000), 0).astype(np.uint16)
    return rgb, identity, depth16, {**camera, "focalPixels": focal, "worldToCamera": view.tolist()}


def validate_reference(item: dict[str, Any], scene: dict[str, Any], path: Path) -> tuple[dict[str, Any], bytes]:
    from PIL import Image
    entity_id = _id(item.get("entityId"), "Reference entity")
    if entity_id not in {entity["entityId"] for entity in scene["entities"]}:
        raise SceneControlError("Reference entity is not in this scene")
    binding = _id(item.get("bindingKey"), "Reference binding key")
    role, purpose = item.get("semanticRole"), str(item.get("purpose") or "").strip()
    if role not in REFERENCE_ROLES or not purpose:
        raise SceneControlError("Each reference requires an explicit view and purpose")
    expected = str(item.get("resourceDigest") or "").removeprefix("sha256:").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise SceneControlError("Reference needs its full content SHA-256 binding; rebind the current source")
    if path.stat().st_size > 30 * 1024 * 1024:
        raise SceneControlError("Reference image exceeds the 30 MB local atlas budget")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise SceneControlError("Reference content changed; rebind the current source revision")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except Exception as exc:
        raise SceneControlError("Reference is not a decodable image") from exc
    return {"entityId": entity_id, "bindingKey": binding, "semanticRole": role, "purpose": purpose, "resourceDigest": expected,
            "source": {key: item[key] for key in ("origin", "id", "sourceNodeId") if key in item}}, content


def render_control_pack(scene: dict[str, Any], *, directory: Path, references: list[tuple[dict[str, Any], Path]], lineage: dict[str, Any], cancelled: threading.Event) -> dict[str, Any]:
    from PIL import Image, ImageDraw, ImageOps
    scene = normalize_scene(scene)
    if any(not entity["name"].strip() for entity in scene["entities"]):
        raise SceneControlError("Give each entity a name before rendering its control pack")
    directory.mkdir(parents=True, exist_ok=False)
    files, atlas, seen = [], [], set()

    def stopped() -> None:
        if cancelled.is_set():
            raise SceneControlError("Proxy scene render cancelled")

    def record(path: Path, channel: str, media_type: str, mime: str) -> dict[str, Any]:
        row = {"file": path.name, "channel": channel, "mediaType": media_type, "mimeType": mime, "sha256": sha256_file(path), "byteSize": path.stat().st_size}
        files.append(row)
        return row

    stopped()
    for item, path in references:
        binding, content = validate_reference(item, scene, path)
        if binding["bindingKey"] in seen:
            raise SceneControlError("Reference binding keys must be unique")
        seen.add(binding["bindingKey"])
        target = directory / f"reference-{len(atlas) + 1}{path.suffix.lower()}"
        target.write_bytes(content)
        row = record(target, "entity_reference", "image", __import__("mimetypes").guess_type(target.name)[0] or "image/png")
        atlas.append({**binding, "file": row["file"]})
    board_width, tile = 768, 256
    board_rows = max(2, math.ceil((len(atlas) or len(scene["entities"])) / 3))
    board = Image.new("RGB", (board_width, board_rows * 320), "#faf9f5")
    draw = ImageDraw.Draw(board)
    for index, binding in enumerate(atlas or [{"entityId": entity["entityId"], "semanticRole": "missing"} for entity in scene["entities"]]):
        entity = next(row for row in scene["entities"] if row["entityId"] == binding["entityId"])
        x, y = index % 3 * tile, index // 3 * 320
        draw.rectangle((x + 8, y + 8, x + 248, y + 46), fill=entity["proxyColor"])
        draw.text((x + 16, y + 20), f"{entity['entityId']} / {binding['semanticRole']}", fill="black")
        if binding.get("file"):
            with Image.open(directory / binding["file"]) as source:
                board.paste(ImageOps.contain(source.convert("RGB"), (240, 250)), (x + 8, y + 58))
        else:
            draw.text((x + 16, y + 80), "Reference not bound", fill="black")
    board_path = directory / "identity-board.png"
    board.save(board_path)
    record(board_path, "identity_board", "image", "image/png")
    video_path = directory / "solid-proxy.mp4"
    ffmpeg, _, ffmpeg_version = governed_ffmpeg_pair()
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{scene['width']}x{scene['height']}", "-r", str(scene["fps"]), "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)]
    frame_count = math.ceil(scene["fps"] * scene["durationSeconds"])
    camera_frames = []
    with tempfile.TemporaryFile() as errors, zipfile.ZipFile(directory / "instance-id.zip", "w", zipfile.ZIP_STORED) as ids, zipfile.ZipFile(directory / "depth16.zip", "w", zipfile.ZIP_STORED) as depths:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, **windowless_subprocess_kwargs())
        try:
            for index in range(frame_count):
                stopped()
                rgb, identity, depth, camera = render_frame(scene, index / scene["fps"])
                camera_frames.append(camera)
                process.stdin.write(rgb.tobytes())
                for archive, pixels in ((ids, identity), (depths, depth)):
                    buffer = io.BytesIO()
                    Image.fromarray(pixels).save(buffer, format="PNG")
                    archive.writestr(f"{index:06d}.png", buffer.getvalue())
                if index in {0, frame_count - 1}:
                    path = directory / ("first-frame.png" if index == 0 else "last-frame.png")
                    Image.fromarray(rgb).save(path)
                    record(path, "first_frame" if index == 0 else "last_frame", "image", "image/png")
            process.stdin.close()
            if process.wait(timeout=120) != 0:
                raise SceneControlError("Governed FFmpeg failed to encode the proxy video")
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    stopped()
    record(video_path, "visual_proxy", "video", "video/mp4")
    record(directory / "instance-id.zip", "instance_id", "document", "application/zip")
    record(directory / "depth16.zip", "depth", "document", "application/zip")
    camera_path = directory / "camera.json"
    camera_path.write_text(json.dumps({"frames": camera_frames, "nearMeters": 0.05, "depthEncoding": "camera_z_millimeters_uint16; 0=background; saturated at 65535"}, indent=2), encoding="utf-8")
    record(camera_path, "camera", "document", "application/json")
    scene_path = directory / "scene.json"
    scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    record(scene_path, "scene", "document", "application/json")
    missing = [entity["entityId"] for entity in scene["entities"] if not any(ref["entityId"] == entity["entityId"] for ref in atlas)]
    return {"schema": PACK_SCHEMA, "scene": scene, "sceneDigest": json_digest(scene), "lineage": lineage, "references": atlas, "files": files,
            "timeline": {"fps": scene["fps"], "timeBase": 1 / scene["fps"], "frameCount": frame_count, "durationSeconds": frame_count / scene["fps"]},
            "coordinateSystem": {"unit": "meter", "up": "+Y", "rotation": "XYZ degrees", "interpolation": "linear", "entityOrigin": "ground center"},
            "renderer": {"id": "v8-primitive-software-3d", "version": 1, "encoder": "governed_ffmpeg", "encoderVersion": ffmpeg_version},
            "guidance": "visual_proxy_soft_reference", "missingEntityReferences": missing,
            "limitations": ["Primitive proxies approximate bodies and pose; no rig, collision, hand or face simulation.", "Generative providers receive soft visual references; exact identity, pose and geometry are not guaranteed.", "Lossless instance IDs and camera-depth maps describe proxy geometry only."]}


def prepare_pack_references(manifest: dict[str, Any], *, request: dict[str, Any], resolve: Callable[[dict[str, Any]], Path]) -> dict[str, Any]:
    if manifest.get("schema") != PACK_SCHEMA:
        raise SceneControlError("Connected document is not a proxy scene control pack")
    scene = normalize_scene(manifest.get("scene"))
    if json_digest(scene) != manifest.get("sceneDigest"):
        raise SceneControlError("Control pack scene digest changed")
    lineage = manifest.get("lineage") or {}
    for field in ("sessionId", "workspaceId", "workspaceKey"):
        if str(lineage.get(field) or "") != str(request.get(field) or ""):
            raise SceneControlError(f"Control pack {field} does not match the current owner")
    if manifest.get("missingEntityReferences"):
        raise SceneControlError("Control pack is partial: bind a real reference for every entity before video generation")
    files = {item["file"]: item for item in manifest.get("files", [])}
    selected, consumed, unsupported = [], [], []
    image_indices: dict[str, int] = {}
    for item in manifest.get("files", []):
        if item.get("channel") not in {"identity_board", "entity_reference", "visual_proxy"}:
            unsupported.append({"channel": item.get("channel"), "sha256": item.get("sha256"), "status": "not_submitted", "reason": "retained_control_evidence; remote adapter has no typed geometry slot"})
            continue
        resource = {"origin": "artifact", "id": item.get("artifactId"), "mediaType": item["mediaType"], "portId": "references", "resourceDigest": item["sha256"]}
        path = resolve(resource)
        if sha256_file(path) != item["sha256"]:
            raise SceneControlError("Control media content changed after baking")
        if item["mediaType"] == "image":
            if item["sha256"] in image_indices:
                continue  # Shared bytes have one transport slot and all uses below remain explicit.
            image_indices[item["sha256"]] = len(image_indices) + 1
        selected.append(resource)
        consumed.append({"channel": item["channel"], "artifactId": item.get("artifactId"), "sha256": item["sha256"], "mediaType": item["mediaType"], "status": "planned"})
    bindings = []
    for ref in manifest.get("references", []):
        source = {**ref["source"], **{field: ref[field] for field in BINDING_FIELDS}, "mediaType": "image"}
        validate_reference(source, scene, resolve(source))
        bindings.append({**{field: ref[field] for field in BINDING_FIELDS}, "imageIndex": image_indices[files[ref["file"]]["sha256"]]})
    if not bindings or not any(item["mediaType"] == "video" for item in selected):
        raise SceneControlError("Control pack is missing its real proxy video or entity atlas")
    prompt = "\n".join([str(request.get("prompt") or ""), "Use the supplied colored proxy video as a soft motion, timing, relative scale and camera reference. Do not copy its solid proxy colors into the result. Keep each entity's identity and appearance tied to its own references. The identity board explains the color mapping. No exact pose or geometry guarantee is implied.",
                       "Scene style: " + scene["style"], "Entity appearance/material/size and timed movement:",
                       json.dumps(scene["entities"], ensure_ascii=False, separators=(",", ":")), "Camera timing:",
                       json.dumps(scene["camera"], ensure_ascii=False, separators=(",", ":")), "Reference image numbers (1-based in submitted image order), views and purposes:",
                       json.dumps(bindings, ensure_ascii=False, separators=(",", ":"))]).strip()
    return {"prompt": prompt, "canvasInputs": selected, "durationSeconds": scene["durationSeconds"],
            "sceneControl": {"schema": CONSUMPTION_SCHEMA, "sceneDigest": manifest["sceneDigest"], "packLineage": lineage,
                             "guidance": "visual_proxy_soft_reference", "status": "planned", "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                             "bindings": bindings, "consumed": consumed, "unsupported": unsupported}}
