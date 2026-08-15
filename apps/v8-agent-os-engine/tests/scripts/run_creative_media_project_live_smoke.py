from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


def _post(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{base_url.rstrip('/')}{path}", json=payload, timeout=300)
    response.raise_for_status()
    return response.json()


def _get(base_url: str, path: str) -> dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}{path}", timeout=120)
    response.raise_for_status()
    return response.json()


def _create_session(base_url: str, scope: dict[str, str]) -> str:
    session = _post(
        base_url,
        "/sessions",
        {
            "title": "Creative Media project live smoke",
            "userId": "creative-media-project-live-smoke",
            **scope,
            "scopeMode": "explicit",
        },
    )
    session_id = str(session.get("id") or session.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError(f"session creation returned no id: {session}")
    return session_id


def _verify_artifact_content(base_url: str, artifact: dict[str, Any], session_id: str) -> None:
    artifact_id = artifact.get("artifactId") or artifact.get("id")
    if not artifact_id:
        raise RuntimeError(f"artifact missing id: {artifact}")
    response = requests.get(
        f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content",
        params={"sessionId": session_id},
        timeout=120,
    )
    response.raise_for_status()
    if len(response.content) <= 0:
        raise RuntimeError(f"artifact content is empty: {artifact_id}")


def _submit_job(base_url: str, payload: dict[str, Any], label: str) -> dict[str, Any]:
    job = _post(base_url, "/creative-media/jobs", payload)["job"]
    if job.get("status") not in {"succeeded", "running", "queued"}:
        raise RuntimeError(f"{label} failed: {job}")
    if job.get("status") != "succeeded":
        deadline = time.time() + 420
        while time.time() < deadline:
            job = _get(base_url, f"/creative-media/jobs/{job['jobId']}")["job"]
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(8)
    if job.get("status") != "succeeded":
        raise RuntimeError(f"{label} did not succeed: {job}")
    artifacts = list(job.get("artifacts") or [])
    if not artifacts:
        raise RuntimeError(f"{label} succeeded without artifacts")
    session_id = str(payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError("live smoke job is missing required session authority")
    for artifact in artifacts:
        _verify_artifact_content(base_url, artifact, session_id)
    return job


def _source_path(artifact: dict[str, Any]) -> str:
    for key in ("sourcePath", "canonicalPath", "workspacePath"):
        value = str(artifact.get(key) or "").strip()
        if value:
            return value
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    return str((metadata or {}).get("sourcePath") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a fresh-project Creative Media live smoke.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9530/v1")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--workspace-path", default="")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--local2-provider-id", default="custom-local2-2d54b4df")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace_path = Path(args.workspace_path or (Path.home() / ".v8-agent-os" / "workspace" / "projects" / f"creative-media-smoke-{stamp}"))
    workspace_path.mkdir(parents=True, exist_ok=True)
    scope = {
        "projectId": args.project_id or f"creative-media-smoke-{stamp}",
        "workspaceId": f"workspace-{stamp}",
        "workspacePath": str(workspace_path),
    }
    planned = {
        "scope": scope,
        "checks": ["local2 gpt-image-2 image", "Volcengine Seedance 5s video", "V8 audio TTS", "P3 render video+audio"],
    }
    if not args.live:
        print(json.dumps({"ok": True, "dryRun": True, **planned}, ensure_ascii=False, indent=2))
        return 0

    _post(
        args.base_url,
        "/projects",
        {
            "id": scope["projectId"],
            "name": workspace_path.name or scope["projectId"],
            "workspaceId": scope["workspaceId"],
            "workspacePath": scope["workspacePath"],
            "tags": ["creative_media", "smoke"],
            "active": True,
        },
    )
    scope["sessionId"] = _create_session(args.base_url, scope)

    image_job = _submit_job(
        args.base_url,
        {
            **scope,
            "modality": "image",
            "adapter": "openai_images",
            "providerId": args.local2_provider_id,
            "model": "gpt-image-2",
            "prompt": "A simple studio product photo of a tiny translucent cube on a white desk, clean background.",
            "ratio": "1:1",
            "responseFormat": "b64_json",
        },
        "local2 image",
    )
    video_job = _submit_job(
        args.base_url,
        {
            **scope,
            "modality": "video",
            "adapter": "volcengine_ark",
            "prompt": "5秒视频，一个透明小方块在白色桌面上，镜头缓慢推进，柔和棚拍光，只有一个稳定动作。",
            "ratio": "16:9",
            "resolution": "720p",
            "duration": 5,
            "wait": True,
            "timeoutSeconds": 420,
            "pollIntervalSeconds": 10,
            "generateAudio": False,
        },
        "volcengine video",
    )
    voice_job = _submit_job(
        args.base_url,
        {
            **scope,
            "modality": "voice",
            "text": "这是 V8OS Creative Media 新项目工作区的稳定性验证音频。",
        },
        "voice",
    )

    image_artifact = image_job["artifacts"][0]
    video_artifact = video_job["artifacts"][0]
    voice_artifact = voice_job["artifacts"][0]
    video_asset = _post(
        args.base_url,
        "/creative-media/assets",
        {
            **scope,
            "role": "clip",
            "modality": "video",
            "artifactId": video_artifact.get("artifactId") or video_artifact.get("id"),
            "sourcePath": _source_path(video_artifact),
            "metadata": {"smoke": "fresh_project_live"},
        },
    )["asset"]
    voice_asset = _post(
        args.base_url,
        "/creative-media/assets",
        {
            **scope,
            "role": "voice_mix",
            "modality": "voice",
            "artifactId": voice_artifact.get("artifactId") or voice_artifact.get("id"),
            "sourcePath": _source_path(voice_artifact),
            "metadata": {"smoke": "fresh_project_live"},
        },
    )["asset"]
    _post(
        args.base_url,
        "/creative-media/assets",
        {
            **scope,
            "role": "reference_image",
            "modality": "image",
            "artifactId": image_artifact.get("artifactId") or image_artifact.get("id"),
            "sourcePath": _source_path(image_artifact),
            "metadata": {"smoke": "fresh_project_live"},
        },
    )
    recipe = _post(
        args.base_url,
        "/creative-media/recipes/compile",
        {
            **scope,
            "modality": "video",
            "prompt": "把新工作区生成的视频和语音拼成一个简短验收视频，不调用旧音乐播放器。",
            "durationSeconds": 5,
            "assetIds": [video_asset["assetId"], voice_asset["assetId"]],
        },
    )["recipe"]
    plan = _post(
        args.base_url,
        "/creative-media/edit-plans",
        {
            **scope,
            "recipeId": recipe["recipeId"],
            "assetIds": [video_asset["assetId"], voice_asset["assetId"]],
            "subtitleText": "Creative Media project smoke",
        },
    )["editPlan"]
    render = _post(args.base_url, "/creative-media/renders", {**scope, "planId": plan["planId"]})["render"]
    if render.get("status") != "succeeded":
        raise RuntimeError(f"render failed: {render}")
    for artifact in list(render.get("artifacts") or []):
        _verify_artifact_content(args.base_url, artifact)

    print(
        json.dumps(
            {
                "ok": True,
                **planned,
                "jobs": {
                    "image": image_job["jobId"],
                    "video": video_job["jobId"],
                    "voice": voice_job["jobId"],
                    "render": render.get("renderJobId"),
                },
                "artifacts": {
                    "image": [item.get("artifactId") for item in image_job.get("artifacts") or []],
                    "video": [item.get("artifactId") for item in video_job.get("artifacts") or []],
                    "voice": [item.get("artifactId") for item in voice_job.get("artifacts") or []],
                    "render": [item.get("artifactId") for item in render.get("artifacts") or []],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
