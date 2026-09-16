from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import threading

import numpy as np
import pytest
from PIL import Image

from core.creative_canvas_graph import CreativeCanvasGraphService
from runtimes.creative_media.scene_control import (
    PACK_SCHEMA, SceneControlError, interpolate, json_digest, normalize_scene,
    prepare_pack_references, render_frame, sha256_file, validate_reference,
)
from runtimes.creative_media.runtime import CreativeMediaRuntime, _build_minimax_video_payload


def scene_fixture(*, holdout=False):
    entities = [
        {"entityId": "robot", "name": "Tin courier", "kind": "character", "shape": "capsule", "proxyColor": "#d84c3e", "size": [0.75, 1.8, 0.5], "appearance": "square antenna and striped scarf", "material": "brushed aluminum", "motion": [{"time": 0, "position": [-1, 0, 0], "pose": {"rightArm": -45}}, {"time": 4, "position": [1, 0, 0], "pose": {"rightArm": 45}}]},
        {"entityId": "parcel", "name": "Flying parcel", "kind": "object", "shape": "box", "proxyColor": "#3878d2", "size": [0.8, 0.8, 0.8], "appearance": "origami wings", "material": "kraft paper", "motion": [{"time": 0, "position": [1, 0.6, 0]}, {"time": 4, "position": [-1, 0.6, 0]}]},
    ]
    if holdout:
        entities[0].update({"kind": "object", "shape": "sphere", "appearance": "ceramic planet", "material": "glazed porcelain", "size": [1.2, 1.2, 1.2]})
        entities[1].update({"appearance": "ice cube", "material": "translucent glass"})
    return normalize_scene({"schema": "v8.proxy_scene.v1", "width": 256, "height": 256, "fps": 24, "durationSeconds": 4,
                            "style": "handmade stop motion" if not holdout else "abstract product photography", "entities": entities,
                            "camera": {"fov": 45, "keyframes": [{"time": 0, "position": [0, 2.6, 7], "target": [0, 0.9, 0]}, {"time": 4, "position": [1.0, 2.2, 6], "target": [0, 0.9, 0]}]}})


@pytest.mark.parametrize("holdout", [False, True])
def test_rendered_identity_depth_and_camera_follow_entity_keys(holdout):
    scene = scene_fixture(holdout=holdout)
    first, ids, depth, camera = render_frame(scene, 0)
    last, last_ids, _, last_camera = render_frame(scene, 4)
    palette = {tuple(bytes.fromhex(item["proxyColor"][1:])) for item in scene["entities"]}
    assert set(map(tuple, ids.reshape(-1, 3))) == {(0, 0, 0), *palette}
    assert np.array_equal(np.any(ids != 0, axis=2), depth > 0)
    assert not np.array_equal(first, last)
    assert camera["position"] != last_camera["position"]
    red = tuple(bytes.fromhex(scene["entities"][0]["proxyColor"][1:]))
    first_x = np.where(np.all(ids == red, axis=2))[1].mean()
    last_x = np.where(np.all(last_ids == red, axis=2))[1].mean()
    assert last_x > first_x + 25  # Wrong identity reassignment by screen order fails.
    assert interpolate(scene["entities"][0]["motion"], 2)["pose"]["rightArm"] == 0


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_color", "nan", "descending_time"])
def test_scene_rejects_ambiguous_or_invalid_control(mutation):
    scene = scene_fixture()
    if mutation == "duplicate_id":
        scene["entities"][1]["entityId"] = "robot"
    elif mutation == "duplicate_color":
        scene["entities"][1]["proxyColor"] = scene["entities"][0]["proxyColor"]
    elif mutation == "nan":
        scene["entities"][0]["size"][0] = float("nan")
    else:
        scene["entities"][0]["motion"][1]["time"] = 0
    with pytest.raises(SceneControlError):
        normalize_scene(scene)


def test_interpolated_camera_cannot_silently_render_invalid_view():
    scene = scene_fixture()
    scene["camera"]["keyframes"] = [{"time": 0, "position": [0, 0, 1], "target": [0, 0, 0]}, {"time": 4, "position": [0, 0, -1], "target": [0, 0, 0]}]
    with pytest.raises(SceneControlError, match="crosses its target"):
        render_frame(scene, 2)


@pytest.fixture()
def pack_fixture(tmp_path):
    scene = scene_fixture()
    files, references, paths = [], [], {}
    for index, entity in enumerate(scene["entities"]):
        path = tmp_path / f"ref-{index}.png"
        Image.new("RGB", (256, 256), "#34bd81" if index else "#c9b067").save(path)
        resource = {"origin": "source", "id": f"source-{index}", "entityId": entity["entityId"], "bindingKey": f"{entity['entityId']}:front", "semanticRole": "front", "purpose": "shape and material identity", "resourceDigest": sha256_file(path)}
        binding, _ = validate_reference(resource, scene, path)
        references.append({**binding, "file": path.name})
        files.append({"file": path.name, "channel": "entity_reference", "artifactId": f"copy-{index}", "sha256": sha256_file(path), "mediaType": "image"})
        paths[resource["id"]] = paths[f"copy-{index}"] = path
    for channel, media_type in (("visual_proxy", "video"), ("identity_board", "image"), ("depth", "document")):
        path = tmp_path / (channel + (".png" if media_type == "image" else ".mp4" if media_type == "video" else ".zip"))
        if media_type == "image":
            Image.new("RGB", (512, 512), "#dedacb").save(path)
        else:
            path.write_bytes(channel.encode())
        files.append({"file": path.name, "channel": channel, "artifactId": channel, "sha256": sha256_file(path), "mediaType": media_type})
        paths[channel] = path
    manifest = {"schema": PACK_SCHEMA, "scene": scene, "sceneDigest": json_digest(scene), "files": files, "references": references, "missingEntityReferences": [], "lineage": {"sessionId": "session-a", "workspaceId": "workspace-a", "workspaceKey": "workspace-key"}}
    request = {**manifest["lineage"], "prompt": "Keep both entities independent."}
    return manifest, request, lambda item: paths[item["id"]], paths


def test_provider_compiler_keeps_entity_purpose_and_all_references(pack_fixture):
    manifest, request, resolve, _ = pack_fixture
    compiled = prepare_pack_references(manifest, request=request, resolve=resolve)
    assert len(compiled["canvasInputs"]) == 4  # Two real references + board + proxy.
    assert compiled["sceneControl"]["bindings"][1]["entityId"] == "parcel"
    assert compiled["sceneControl"]["bindings"][1]["imageIndex"] == 2
    assert compiled["sceneControl"]["unsupported"][0]["channel"] == "depth"
    for value in ("origami wings", "brushed aluminum", "rightArm", "Camera timing", "shape and material identity"):
        assert value in compiled["prompt"]


@pytest.mark.parametrize("change", ["session", "workspace", "source_bytes", "missing", "scene_tamper"])
def test_pack_generation_fails_closed_before_transport(pack_fixture, change):
    manifest, request, resolve, paths = pack_fixture
    if change == "session":
        request["sessionId"] = "session-b"
    elif change == "workspace":
        request["workspaceKey"] = "other-root"
    elif change == "source_bytes":
        paths["source-0"].write_bytes(b"revised bytes")
    elif change == "missing":
        manifest["missingEntityReferences"] = ["robot"]
    else:
        manifest["scene"]["entities"][0]["appearance"] = "silently replaced"
    with pytest.raises(SceneControlError):
        prepare_pack_references(manifest, request=request, resolve=resolve)


def test_actual_provider_payload_oracle_kills_reference_loss_and_truncation(pack_fixture):
    manifest, request, resolve, _ = pack_fixture
    request.update(prepare_pack_references(manifest, request=request, resolve=resolve))
    payload = _build_minimax_video_payload(model="MiniMax-H3", operation_kind="video.reference_to_video", prompt=request["prompt"], image_references=["https://fixture/a.png", "https://fixture/b.png", "https://fixture/board.png"], video_references=["https://fixture/proxy.mp4"], duration_seconds=4)
    runtime = CreativeMediaRuntime()
    job = {}
    runtime._verify_scene_provider_payload(job, request, payload)
    assert job["sceneControl"]["submittedCounts"] == {"image": 3, "video": 1, "audio": 0}
    lost = copy.deepcopy(payload)
    lost["content"].pop()
    with pytest.raises(SceneControlError, match="lost or transformed"):
        runtime._verify_scene_provider_payload({}, request, lost)
    truncated = copy.deepcopy(payload)
    truncated["content"][0]["text"] = request["prompt"][:100]
    with pytest.raises(SceneControlError, match="lost or transformed"):
        runtime._verify_scene_provider_payload({}, request, truncated)
    with pytest.raises(ValueError, match="7000"):
        _build_minimax_video_payload(model="MiniMax-H3", operation_kind="video.reference_to_video", prompt="x" * 7001, image_references=["https://fixture/a.png"], video_references=["https://fixture/proxy.mp4"], duration_seconds=4)


def test_canvas_graph_preserves_long_prompt_and_reference_binding():
    service = CreativeCanvasGraphService()
    binding = {"entityId": "robot", "bindingKey": "robot:front", "semanticRole": "front", "purpose": "identity material", "resourceDigest": "a" * 64}
    graph = service._normalize_graph({"nodes": [{"nodeId": "source", "kind": "resource", "origin": "source", "resourceId": "source-a", "mediaType": "image"}, {"nodeId": "scene", "kind": "action", "actionDefinitionId": "creative_media.render_proxy_scene_control_pack", "prompt": "tail" * 4000, "parameters": {"scene": scene_fixture()}}], "edges": [{"edgeId": "ref", "from": "source", "to": "scene", "role": "data", "toPortId": "references", "dataType": "image", **binding}]})
    assert graph["nodes"][1]["prompt"] == "tail" * 4000
    assert all(graph["edges"][0][key] == value for key, value in binding.items())
    request = service._request_for_entry(entry={"capability": "video.render_proxy_scene_control_pack", "actionNodeId": "scene", "resultNodeId": "result", "configurationRevision": 8, "parameters": {"scene": scene_fixture()}, "inputs": [{"portId": "references", "sourceNodeId": "source", "resource": {"origin": "source", "id": "source-a", "mediaType": "image"}, **binding}]}, graph_id="g", graph_run_id="gr", session_id="s", chat_run_id="r", canvas_operation_id="o", project_id="p", workspace_id="w", workspace_path="workspace")
    assert request["canvasConfigurationRevision"] == 8
    assert all(request["canvasInputs"][0][key] == value for key, value in binding.items())


def test_local_cancel_waits_for_renderer_and_removes_late_files(monkeypatch, tmp_path):
    runtime = CreativeMediaRuntime()
    jobs = {}
    started = threading.Event()

    def save(job):
        jobs[job["jobId"]] = copy.deepcopy(job)
        return job

    def render(_scene, *, directory, cancelled, **_kwargs):
        directory.mkdir()
        started.set()
        assert cancelled.wait(5)
        (directory / "late.mp4").write_bytes(b"late encoder output")
        raise SceneControlError("cancelled")

    monkeypatch.setattr(runtime, "_save_job", save)
    monkeypatch.setattr(runtime, "_output_path", lambda *_: tmp_path / "manifest.v8scene.json")
    monkeypatch.setattr("runtimes.creative_media.runtime.render_control_pack", render)

    async def exercise():
        task = asyncio.create_task(runtime._create_proxy_scene_job({"operationKind": "video.render_proxy_scene_control_pack", "scene": scene_fixture()}))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert list(jobs.values())[0]["status"] == "cancelled"
    assert list(jobs.values())[0]["artifacts"] == []
    assert list(tmp_path.iterdir()) == []


def test_cancelled_provider_callback_cannot_publish_scene_artifact(monkeypatch, tmp_path):
    runtime = CreativeMediaRuntime()
    monkeypatch.setattr(runtime, "_assert_active_authority_fence", lambda *_: None)
    monkeypatch.setattr(runtime, "get_job", lambda *_args, **_kwargs: {"status": "cancelled"})
    with pytest.raises(SceneControlError, match="late provider"):
        runtime._record_local_artifact(file_path=tmp_path / "late.mp4", job={"jobId": "scene-job", "sceneControl": {"status": "submitted"}}, kind="video", mime_type="video/mp4", metadata={})


def test_typed_pose_tree_is_not_mistaken_for_a_nested_resource_manifest(monkeypatch):
    runtime = CreativeMediaRuntime()
    monkeypatch.setattr(runtime, "_store_is_active", lambda: True)
    # Calls the real generic resource authority walker. The old implementation
    # raises media_resource_manifest_too_deep before a local job can be created.
    assert runtime._authorize_request_resources({"operationKind": "video.render_proxy_scene_control_pack", "scene": scene_fixture()}) == []


def test_minimax_http_boundary_receives_every_exact_reference_byte(pack_fixture, monkeypatch):
    manifest, request, resolve, paths = pack_fixture
    request.update(prepare_pack_references(manifest, request=request, resolve=resolve))
    request.update({"model": "MiniMax-H3", "adapter": "minimax_video", "operationKind": "video.reference_to_video"})
    runtime = CreativeMediaRuntime()
    captured = {}
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {"providerId": "fixture-minimax", "providerModelId": "MiniMax-H3", "providerMeta": {"api_key": "fixture-only", "base_url": "https://fixture.invalid"}})
    monkeypatch.setattr(runtime, "_artifact_provider_transport_url", lambda *_args: "")
    monkeypatch.setattr(runtime, "_canvas_input_path", lambda *, session_id, item: resolve(item))
    monkeypatch.setattr(runtime, "_save_job", lambda job: job)

    async def transport(method, url, **kwargs):
        captured.update({"method": method, "url": url, "payload": kwargs["json"]})
        return {"task_id": "fixture-task"}

    monkeypatch.setattr(runtime, "_request_json", transport)
    job = asyncio.run(runtime._submit_minimax_video_job({"jobId": "scene-test", "operationKind": "video.reference_to_video"}, request))
    assert job["providerTaskId"] == "fixture-task"
    assert captured["url"].endswith("/v2/video_generation")
    payload = captured["payload"]
    assert payload["content"][0]["text"] == request["prompt"]
    decoded = [base64.b64decode(item[item["type"]]["url"].split(",", 1)[1]) for item in payload["content"][1:]]
    assert set(decoded) == {paths[key].read_bytes() for key in ("copy-0", "copy-1", "visual_proxy", "identity_board")}
    assert [item["role"] for item in payload["content"][1:]] == ["reference_image"] * 3 + ["reference_video"]


def test_manifest_tamper_cannot_retarget_recorded_pack(pack_fixture, monkeypatch, tmp_path):
    from core.workspace_identity import workspace_path_key
    manifest, request, resolve, _ = pack_fixture
    request["workspacePath"] = str(tmp_path)
    manifest["lineage"]["workspaceKey"] = workspace_path_key(str(tmp_path))
    path = tmp_path / "manifest.v8scene.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    recorded_digest = sha256_file(path)
    request["canvasInputs"] = [{"origin": "artifact", "id": "pack", "portId": "controlPack", "mediaType": "document"}]
    runtime = CreativeMediaRuntime()
    monkeypatch.setattr(runtime, "_canvas_input_path", lambda *, session_id, item: path if item["id"] == "pack" else resolve(item))
    monkeypatch.setattr("runtimes.creative_media.runtime.db.get_runtime_artifact", lambda *_: {"metadata": {"controlPackSchema": PACK_SCHEMA, "contentSha256": recorded_digest}})
    prepared = runtime._prepare_scene_video_request(request)
    assert prepared["sceneControl"]["controlPack"]["sha256"] == recorded_digest
    manifest["references"][0]["purpose"] = "silently retargeted identity"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SceneControlError, match="recorded artifact revision"):
        runtime._prepare_scene_video_request(request)


def test_retry_revalidates_original_pack_without_growing_prompt(pack_fixture, monkeypatch, tmp_path):
    from core.workspace_identity import workspace_path_key
    manifest, request, resolve, _ = pack_fixture
    request.update({"workspacePath": str(tmp_path), "operationKind": "video.reference_to_video", "model": "MiniMax-H3", "adapter": "minimax_video", "sceneControl": {"spoofed": True}})
    manifest["lineage"]["workspaceKey"] = workspace_path_key(str(tmp_path))
    path = tmp_path / "manifest.v8scene.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    raw_input = {"origin": "artifact", "id": "pack", "portId": "controlPack", "mediaType": "document"}
    request["canvasInputs"] = [raw_input]
    runtime = CreativeMediaRuntime()
    payloads = []
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {"providerId": "fixture-minimax", "providerModelId": "MiniMax-H3", "providerMeta": {"api_key": "fixture-only", "base_url": "https://fixture.invalid"}})
    monkeypatch.setattr(runtime, "_artifact_provider_transport_url", lambda *_: "")
    monkeypatch.setattr(runtime, "_canvas_input_path", lambda *, session_id, item: path if item["id"] == "pack" else resolve(item))
    monkeypatch.setattr("runtimes.creative_media.runtime.db.get_runtime_artifact", lambda *_: {"metadata": {"controlPackSchema": PACK_SCHEMA, "contentSha256": sha256_file(path)}})
    monkeypatch.setattr(runtime, "_save_job", lambda job: job)

    async def transport(_method, _url, **kwargs):
        payloads.append(kwargs["json"])
        return {"task_id": f"fixture-task-{len(payloads)}"}

    monkeypatch.setattr(runtime, "_request_json", transport)
    first = asyncio.run(runtime._create_video_job(request))
    assert first["status"] == "queued", first.get("error")
    assert first["request"]["canvasInputs"] == [raw_input]
    assert "spoofed" not in first["sceneControl"]
    second = asyncio.run(runtime._create_video_job(first["request"]))
    assert second["status"] == "queued", second.get("error")
    assert payloads[0] == payloads[1]
    assert second["sceneControl"]["status"] == "submitted"


def test_reference_revoked_during_bake_cannot_publish_even_when_old_file_exists(pack_fixture, monkeypatch, tmp_path):
    manifest, request, resolve, paths = pack_fixture
    runtime = CreativeMediaRuntime()
    revoked = False
    registered = []
    inputs = [{**ref["source"], **{key: ref[key] for key in ("entityId", "bindingKey", "semanticRole", "purpose", "resourceDigest")}, "portId": "references", "mediaType": "image"} for ref in manifest["references"]]

    def current_resource(*, session_id, item):
        if revoked:
            raise PermissionError("revoked_current_reference")
        return resolve(item)

    def render(_scene, *, directory, **_kwargs):
        nonlocal revoked
        directory.mkdir()
        for item in manifest["files"]:
            (directory / item["file"]).write_bytes(paths[item["artifactId"]].read_bytes())
        revoked = True  # The old path and every byte remain readable.
        return copy.deepcopy(manifest)

    def record(**kwargs):
        registered.append(kwargs)
        return {"artifactId": f"artifact-{len(registered)}"}

    monkeypatch.setattr(runtime, "_store_is_active", lambda: False)
    monkeypatch.setattr(runtime, "_save_job", lambda job: job)
    monkeypatch.setattr(runtime, "get_job", lambda *_args, **_kwargs: {"status": "running"})
    monkeypatch.setattr(runtime, "_canvas_input_path", current_resource)
    monkeypatch.setattr(runtime, "_record_local_artifact", record)
    monkeypatch.setattr(runtime, "_output_path", lambda *_: tmp_path / "manifest.v8scene.json")
    monkeypatch.setattr("runtimes.creative_media.runtime.render_control_pack", render)
    job = asyncio.run(runtime._create_proxy_scene_job({**request, "operationKind": "video.render_proxy_scene_control_pack", "scene": manifest["scene"], "canvasInputs": inputs}))
    assert job["status"] == "failed"
    assert "revoked_current_reference" in job["error"]
    assert registered == []
    assert paths["source-0"].exists()
