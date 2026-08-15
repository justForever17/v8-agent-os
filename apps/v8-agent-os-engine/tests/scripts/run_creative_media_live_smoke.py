from __future__ import annotations

import argparse
import sys
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


def _create_session(base_url: str, workspace_path: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    session = _post(
        base_url,
        "/sessions",
        {
            "title": "Creative Media live smoke",
            "userId": "creative-media-live-smoke",
            "projectId": f"creative-media-live-{stamp}",
            "workspaceId": f"creative-media-live-workspace-{stamp}",
            "workspacePath": str(workspace_path),
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


def _submit_and_verify(base_url: str, payload: dict[str, Any], label: str) -> dict[str, Any]:
    result = _post(base_url, "/creative-media/jobs", payload)
    job = result["job"]
    if job.get("status") not in {"succeeded", "running", "queued"}:
        raise RuntimeError(f"{label} failed: {job}")
    if job.get("status") != "succeeded":
        deadline = time.time() + 360
        while time.time() < deadline:
            job = _get(base_url, f"/creative-media/jobs/{job['jobId']}")["job"]
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(8)
    if job.get("status") != "succeeded":
        raise RuntimeError(f"{label} did not succeed: {job}")
    artifacts = job.get("artifacts") or []
    if not artifacts:
        raise RuntimeError(f"{label} succeeded without artifacts: {job}")
    session_id = str(payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError("live smoke job is missing required session authority")
    for artifact in artifacts:
        _verify_artifact_content(base_url, artifact, session_id)
    print(f"[PASS] {label}: {job['jobId']} -> {[item.get('artifactId') for item in artifacts]}")
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live creative media smoke checks against an already running V8 engine.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9530/v1")
    parser.add_argument("--workspace-path", default="")
    parser.add_argument("--live", action="store_true", help="Actually call providers. Without this flag, only prints the planned checks.")
    args = parser.parse_args()

    checks = [
        (
            "local2 gpt-image-2 image",
            {
                "modality": "image",
                "adapter": "openai_images",
                "providerId": "custom-local2-2d54b4df",
                "model": "gpt-image-2",
                "prompt": "A clean product-style image of a small glass rabbit on a white desk, soft studio lighting.",
                "ratio": "1:1",
                "responseFormat": "b64_json"
            },
        ),
        (
            "Volcengine Seedream image",
            {
                "modality": "image",
                "adapter": "volcengine_ark",
                "prompt": "一张简洁高级的产品摄影图：透明玻璃兔子摆件放在白色桌面上，柔和棚拍光，1:1。",
                "ratio": "1:1",
                "responseFormat": "url"
            },
        ),
        (
            "Volcengine Seedance 5s video",
            {
                "modality": "video",
                "adapter": "volcengine_ark",
                "prompt": "5秒视频，透明玻璃兔子摆件在白色桌面上，镜头缓慢推进，柔和棚拍光，动作简单稳定。",
                "ratio": "16:9",
                "resolution": "720p",
                "duration": 5,
                "wait": True,
                "timeoutSeconds": 360,
                "pollIntervalSeconds": 10,
                "generateAudio": False
            },
        ),
        (
            "V8 audio TTS voice",
            {
                "modality": "voice",
                "text": "这是一段 V8OS Creative Runtime P1 的语音生成验收音频。"
            },
        ),
    ]

    if not args.live:
        print("Dry run. Planned checks:")
        for label, payload in checks:
            print(f"- {label}: {payload}")
        print("Pass --live to execute provider calls.")
        return 0

    workspace_path = Path(args.workspace_path).expanduser().resolve()
    if not args.workspace_path or not workspace_path.is_dir():
        raise RuntimeError("--workspace-path must be an existing directory for --live")
    session_id = _create_session(args.base_url, workspace_path)
    for label, payload in checks:
        _submit_and_verify(
            args.base_url,
            {**payload, "sessionId": session_id, "workspacePath": str(workspace_path)},
            label,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
