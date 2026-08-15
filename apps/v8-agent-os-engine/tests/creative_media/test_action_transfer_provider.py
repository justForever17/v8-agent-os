from __future__ import annotations

import asyncio

import pytest

from runtimes.creative_media.runtime import creative_media_runtime


def _dashscope_action_transfer_config() -> dict:
    return {
        "providers": {
            "dashscope": {
                "provider": {
                    "name": "DashScope",
                    "base_url": "https://dashscope.example.test/api/v1",
                    "api_key": "stored-key",
                    "api_standard": "dashscope",
                },
                "models": {
                    "wan2.2-animate-move": {
                        "type": "VIDEO",
                        "operationKinds": ["video.action_transfer"],
                        "mediaLimits": {
                            "adapter": "dashscope",
                            "operationKinds": ["video.action_transfer"],
                        },
                        "endpointBinding": {
                            "route": "wan2.2-animate-move",
                            "providerModelId": "wan2.2-animate-move",
                            "operationKind": "video.action_transfer",
                            "adapter": "dashscope",
                        },
                    },
                    "wan2.7-i2v": {
                        "type": "VIDEO",
                        "operationKinds": ["video.action_transfer"],
                        "mediaLimits": {
                            "adapter": "dashscope",
                            "operationKinds": ["video.action_transfer"],
                        },
                        "endpointBinding": {
                            "route": "wan2.7-i2v",
                            "providerModelId": "wan2.7-i2v",
                            "operationKind": "video.action_transfer",
                            "adapter": "dashscope",
                        },
                    },
                },
            }
        }
    }


@pytest.fixture(autouse=True)
def _configured_dashscope_action_transfer(monkeypatch):
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        _dashscope_action_transfer_config,
    )


def _job() -> dict:
    return {
        "jobId": "cm-action-transfer",
        "operationKind": "video.action_transfer",
        "status": "created",
    }


def test_dashscope_action_transfer_uses_exact_official_contract(monkeypatch):
    captured: dict = {}

    async def request_json(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return {"output": {"task_id": "task-action", "task_status": "PENDING"}}

    monkeypatch.setattr(
        creative_media_runtime,
        "_dashscope_credentials",
        lambda: {"apiKey": "sk-test", "baseUrl": "https://dashscope.example.test/api/v1"},
    )
    monkeypatch.setattr(creative_media_runtime, "_request_json", request_json)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: dict(job))

    result = asyncio.run(creative_media_runtime._submit_dashscope_video_job(
        _job(),
        {
            "model": "wan2.2-animate-move",
            "targetImageUrl": "https://cdn.example.test/character.png",
            "referenceVideoUrl": "https://cdn.example.test/action.mp4",
            "mode": "wan-pro",
        },
    ))

    assert result["providerTaskId"] == "task-action"
    assert captured["url"].endswith("/services/aigc/image2video/video-synthesis")
    assert captured["json"] == {
        "model": "wan2.2-animate-move",
        "input": {
            "image_url": "https://cdn.example.test/character.png",
            "video_url": "https://cdn.example.test/action.mp4",
            "watermark": False,
        },
        "parameters": {"mode": "wan-pro"},
    }


def test_dashscope_action_transfer_resolves_canvas_provider_artifacts(monkeypatch):
    captured: dict = {}

    async def request_json(_method, _url, **kwargs):
        captured.update(kwargs)
        return {"output": {"task_id": "task-canvas", "task_status": "PENDING"}}

    monkeypatch.setattr(
        creative_media_runtime,
        "_dashscope_credentials",
        lambda: {"apiKey": "sk-test", "baseUrl": "https://dashscope.example.test/api/v1"},
    )
    monkeypatch.setattr(creative_media_runtime, "_request_json", request_json)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: dict(job))
    monkeypatch.setattr(
        creative_media_runtime,
        "_artifact_provider_transport_url",
        lambda artifact_id: f"https://cdn.example.test/{artifact_id}",
    )

    asyncio.run(creative_media_runtime._submit_dashscope_video_job(
        _job(),
        {
            "model": "wan2.2-animate-move",
            "canvasInputs": [
                {"portId": "image", "origin": "artifact", "id": "character.png"},
                {"portId": "video", "origin": "artifact", "id": "action.mp4"},
            ],
        },
    ))

    assert captured["json"]["input"]["image_url"].endswith("/character.png")
    assert captured["json"]["input"]["video_url"].endswith("/action.mp4")


def test_dashscope_action_transfer_rejects_wrong_model_and_local_only_canvas_input(monkeypatch):
    monkeypatch.setattr(
        creative_media_runtime,
        "_dashscope_credentials",
        lambda: {"apiKey": "sk-test", "baseUrl": "https://dashscope.example.test/api/v1"},
    )

    with pytest.raises(ValueError, match="select wan2.2-animate-move"):
        asyncio.run(creative_media_runtime._submit_dashscope_video_job(
            _job(),
            {
                "model": "wan2.7-i2v",
                "targetImageUrl": "https://cdn.example.test/character.png",
                "referenceVideoUrl": "https://cdn.example.test/action.mp4",
            },
        ))

    with pytest.raises(ValueError, match="local-only"):
        asyncio.run(creative_media_runtime._submit_dashscope_video_job(
            _job(),
            {
                "model": "wan2.2-animate-move",
                "canvasInputs": [
                    {"portId": "image", "origin": "source", "id": "source-image"},
                    {"portId": "video", "origin": "source", "id": "source-video"},
                ],
            },
        ))


def test_configured_dashscope_action_transfer_candidate_is_executable(monkeypatch):
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        _dashscope_action_transfer_config,
    )
    monkeypatch.setattr(creative_media_runtime, "_volc_credentials", lambda: {})

    candidate = next(
        item
        for item in creative_media_runtime.list_model_candidates()
        if item.get("modelRef") == "dashscope::wan2.2-animate-move"
    )

    assert candidate["operationKind"] == "video.action_transfer"
    assert candidate["available"] is True
    assert candidate["readiness"]["reasonCodes"] == []
