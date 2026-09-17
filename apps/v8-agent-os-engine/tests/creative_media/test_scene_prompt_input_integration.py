from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

from core.workspace_identity import workspace_path_key
from runtimes.creative_media.runtime import CreativeMediaRuntime
from runtimes.creative_media.scene_control import PACK_SCHEMA, SceneControlError, sha256_file
from tests.creative_media.test_scene_control import pack_fixture  # noqa: F401


@pytest.fixture
def scene_runtime(pack_fixture, monkeypatch, tmp_path):
    manifest, request, resolve, _ = pack_fixture
    request.update({"workspacePath": str(tmp_path), "operationKind": "video.reference_to_video",
                    "model": "MiniMax-H3", "adapter": "minimax_video"})
    manifest["lineage"]["workspaceKey"] = workspace_path_key(str(tmp_path))
    path = tmp_path / "manifest.v8scene.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    request["canvasInputs"] = [{"origin": "artifact", "id": "pack", "portId": "controlPack", "mediaType": "document"}]
    runtime = CreativeMediaRuntime()
    payloads, jobs = [], {}
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {
        "providerId": "fixture", "providerModelId": "MiniMax-H3",
        "providerMeta": {"api_key": "fixture-only", "base_url": "https://fixture.invalid"}})
    monkeypatch.setattr(runtime, "_artifact_provider_transport_url", lambda *_: "")
    monkeypatch.setattr(runtime, "_canvas_input_path", lambda *, session_id, item: path if item["id"] == "pack" else resolve(item))
    monkeypatch.setattr("runtimes.creative_media.runtime.db.get_runtime_artifact", lambda *_: {
        "metadata": {"controlPackSchema": PACK_SCHEMA, "contentSha256": sha256_file(path)}})

    def save(job):
        jobs[job["jobId"]] = deepcopy(job)
        return job

    async def transport(_method, _url, **kwargs):
        payloads.append(kwargs["json"])
        return {"task_id": f"fixture-task-{len(payloads)}"}

    monkeypatch.setattr(runtime, "_save_job", save)
    monkeypatch.setattr(runtime, "get_job", lambda job_id, **_kwargs: deepcopy(jobs[job_id]))
    monkeypatch.setattr(runtime, "_request_json", transport)
    monkeypatch.setattr(runtime, "create_job", runtime._create_video_job)
    return runtime, request, payloads


def test_scene_retry_preserves_negative_prompt_slots_duration_and_epoch(scene_runtime):
    runtime, request, payloads = scene_runtime
    request["negativePrompt"] = "不要把机器人换成人；不要新增字幕。"
    first = asyncio.run(runtime._create_video_job(request))
    assert first["status"] == "queued", first.get("error")
    second = asyncio.run(runtime.retry_job(first["jobId"]))
    assert second["status"] == "queued", second.get("error")
    assert payloads[0] == payloads[1]
    assert payloads[1]["duration"] == 4
    assert payloads[1]["content"][0]["text"].count(request["negativePrompt"]) == 1
    assert first["sceneControl"]["bindings"] == second["sceneControl"]["bindings"]
    assert first["sceneControl"]["controlPack"] == second["sceneControl"]["controlPack"]
    assert second["sceneControl"]["submittedCounts"] == {"image": 3, "video": 1, "audio": 0}
    # The same retry must still revalidate the transcript's current epoch.
    runtime._scene_context_lineage = lambda _session: {"contextEpoch": 1, "transcriptRevision": 1}
    stale = asyncio.run(runtime.retry_job(second["jobId"]))
    assert stale["status"] == "failed"
    assert "context was superseded" in stale["error"].lower()
    assert len(payloads) == 2


@pytest.mark.parametrize("extra", [
    {"referenceMedia": [{"type": "reference_image", "url": "https://fixture.invalid/other.png"}]},
    {"referenceAssetIds": ["other-artifact"]},
    {"firstFrame": "https://fixture.invalid/start.png"},
])
def test_scene_pack_cannot_be_mixed_with_unfrozen_reference_fields(scene_runtime, extra):
    runtime, request, payloads = scene_runtime
    with pytest.raises(SceneControlError, match="frozen control pack"):
        runtime._prepare_scene_video_request({**request, **extra})
    assert payloads == []
