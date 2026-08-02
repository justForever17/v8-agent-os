from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtimes.creative_media.comfyui_workflow import (
    bind_comfyui_inputs,
    select_comfyui_output,
    validate_comfyui_workflow,
)
from runtimes.creative_media.runtime import creative_media_runtime


def _workflow() -> dict:
    return {
        "schema": "v8.comfyui.workflow.v1",
        "operationKind": "video.action_transfer",
        "prompt": {
            "10": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
            "11": {"class_type": "LoadVideo", "inputs": {"video": "placeholder.mp4"}},
            "20": {"class_type": "SaveVideo", "inputs": {"images": ["11", 0]}},
        },
        "bindings": {
            "image": {"nodeId": "10", "inputName": "image"},
            "video": {"nodeId": "11", "inputName": "video"},
        },
        "output": {"nodeId": "20", "field": "videos", "index": 0},
    }


def _config(workflow: dict | None = None) -> dict:
    media_limits = {
        "adapter": "comfyui_workflow",
        "operationKinds": ["video.action_transfer"],
    }
    if workflow is not None:
        media_limits["comfyuiWorkflow"] = workflow
    return {
        "providers": {
            "comfyui": {
                "provider": {
                    "name": "ComfyUI",
                    "base_url": "http://127.0.0.1:8188",
                    "api_standard": "comfyui",
                    "is_enabled": True,
                },
                "models": {
                    "comfyui-workflow": {
                        "type": "WORKFLOW",
                        "isEnabled": True,
                        "operationKinds": ["video.action_transfer"],
                        "mediaLimits": media_limits,
                        "endpointBinding": {
                            "route": "comfyui-workflow",
                            "providerModelId": "comfyui-workflow",
                            "operationKind": "video.action_transfer",
                            "adapter": "comfyui_workflow",
                            "apiStandard": "comfyui",
                        },
                    }
                },
            }
        }
    }


def test_comfyui_workflow_validates_bindings_and_rejects_embedded_credentials():
    normalized = validate_comfyui_workflow(_workflow())
    prompt = bind_comfyui_inputs(
        normalized,
        {"image": "v8os/job/character.png", "video": "v8os/job/action.mp4"},
    )

    assert prompt["10"]["inputs"]["image"] == "v8os/job/character.png"
    assert prompt["11"]["inputs"]["video"] == "v8os/job/action.mp4"

    invalid = _workflow()
    invalid["bindings"]["video"]["inputName"] = "missing"
    with pytest.raises(ValueError, match="unknown node input"):
        validate_comfyui_workflow(invalid)

    secret = _workflow()
    secret["prompt"]["10"]["inputs"]["api_key"] = "secret-in-workflow"
    with pytest.raises(ValueError, match="embedded credential"):
        validate_comfyui_workflow(secret)


def test_comfyui_output_selection_is_explicit_and_path_safe():
    selected = select_comfyui_output(
        _workflow(),
        {
            "outputs": {
                "20": {
                    "videos": [
                        {"filename": "result.mp4", "subfolder": "v8os/output", "type": "output"}
                    ]
                }
            }
        },
    )
    assert selected == {"filename": "result.mp4", "subfolder": "v8os/output", "type": "output"}

    with pytest.raises(ValueError, match="unsafe output"):
        select_comfyui_output(
            _workflow(),
            {"outputs": {"20": {"videos": [{"filename": "result.mp4", "subfolder": "../escape", "type": "output"}]}}},
        )


def test_comfyui_candidate_requires_workflow_but_not_local_api_key(monkeypatch):
    monkeypatch.setattr("runtimes.creative_media.runtime.model_control_plane.get_config", lambda: _config(_workflow()))
    monkeypatch.setattr(creative_media_runtime, "_volc_credentials", lambda: {})
    ready = next(
        item
        for item in creative_media_runtime.list_model_candidates()
        if item.get("modelRef") == "comfyui::comfyui-workflow"
    )
    assert ready["modality"] == "video"
    assert ready["operationKind"] == "video.action_transfer"
    assert ready["available"] is True
    assert ready["readiness"]["reasonCodes"] == []

    monkeypatch.setattr("runtimes.creative_media.runtime.model_control_plane.get_config", lambda: _config())
    blocked = next(
        item
        for item in creative_media_runtime.list_model_candidates()
        if item.get("modelRef") == "comfyui::comfyui-workflow"
    )
    assert blocked["available"] is False
    assert blocked["readiness"]["reasonCodes"] == ["comfyui_workflow_invalid"]


def test_comfyui_adapter_uploads_session_inputs_and_records_video(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "character.png"
    video_path = tmp_path / "action.mp4"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")
    monkeypatch.setattr("runtimes.creative_media.runtime.model_control_plane.get_config", lambda: _config(_workflow()))
    monkeypatch.setattr(
        creative_media_runtime,
        "_canvas_input_path",
        lambda *, session_id, item: image_path if item["portId"] == "image" else video_path,
    )
    uploads: list[dict] = []
    prompt_payload: dict = {}

    async def request_multipart(_method, _url, **kwargs):
        uploaded = kwargs["files"]["image"]
        uploads.append({"filename": uploaded[0], "content": uploaded[1].read(), "data": kwargs["data"]})
        return {"name": uploaded[0], "subfolder": kwargs["data"]["subfolder"], "type": "input"}

    async def request_json(method, url, **kwargs):
        if url.endswith("/prompt"):
            prompt_payload.update(kwargs["json"])
            return {"prompt_id": "prompt-1"}
        assert method == "GET"
        return {
            "prompt-1": {
                "outputs": {
                    "20": {
                        "videos": [
                            {"filename": "result.mp4", "subfolder": "v8os/output", "type": "output"}
                        ]
                    }
                }
            }
        }

    async def download(_url, destination, **_kwargs):
        destination.write_bytes(b"result-video")
        return "video/mp4"

    monkeypatch.setattr(creative_media_runtime, "_request_multipart_json", request_multipart)
    monkeypatch.setattr(creative_media_runtime, "_request_json", request_json)
    monkeypatch.setattr(creative_media_runtime, "_download_provider_file", download)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: dict(job))
    monkeypatch.setattr(
        creative_media_runtime,
        "_record_local_artifact",
        lambda **kwargs: {"artifactId": "artifact-comfy-video", "sourcePath": str(kwargs["file_path"])},
    )
    monkeypatch.setattr(creative_media_runtime, "_output_path", lambda *_args: tmp_path / "output.mp4")
    request = {
        "sessionId": "session-1",
        "providerId": "comfyui",
        "modelId": "comfyui-workflow",
        "modelRef": "comfyui::comfyui-workflow",
        "operationKind": "video.action_transfer",
        "canvasInputs": [
            {"portId": "image", "origin": "source", "id": "source-image"},
            {"portId": "video", "origin": "source", "id": "source-video"},
        ],
    }
    job = {
        "jobId": "cm-comfy",
        "request": request,
        "sessionId": "session-1",
        "runId": "run-1",
        "modality": "video",
        "operationKind": "video.action_transfer",
    }

    running = asyncio.run(creative_media_runtime._submit_comfyui_workflow_job(job, request))
    completed = asyncio.run(creative_media_runtime._poll_comfyui_workflow_job(running))

    assert [item["content"] for item in uploads] == [b"image", b"video"]
    assert prompt_payload["prompt"]["10"]["inputs"]["image"].endswith("/character.png")
    assert prompt_payload["prompt"]["11"]["inputs"]["video"].endswith("/action.mp4")
    assert completed["status"] == "succeeded"
    assert completed["artifacts"][0]["artifactId"] == "artifact-comfy-video"


def test_comfyui_poll_projects_execution_error_without_raw_history(monkeypatch):
    monkeypatch.setattr("runtimes.creative_media.runtime.model_control_plane.get_config", lambda: _config(_workflow()))

    async def request_json(_method, _url, **_kwargs):
        return {
            "prompt-error": {
                "outputs": {},
                "status": {
                    "status_str": "error",
                    "completed": True,
                    "messages": [["execution_error", {"traceback": "private provider trace"}]],
                },
            }
        }

    monkeypatch.setattr(creative_media_runtime, "_request_json", request_json)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: dict(job))
    job = {
        "jobId": "cm-comfy-error",
        "providerTaskId": "prompt-error",
        "status": "running",
        "request": {
            "providerId": "comfyui",
            "modelId": "comfyui-workflow",
            "modelRef": "comfyui::comfyui-workflow",
        },
    }

    result = asyncio.run(creative_media_runtime._poll_comfyui_workflow_job(job))

    assert result["status"] == "failed"
    assert result["error"] == "ComfyUI workflow execution failed"
    assert "traceback" not in result["error"]
