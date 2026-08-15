from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

import requests


PUBLIC_IMAGE = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250919/adsyrp/move_input_image.jpeg"
PUBLIC_VIDEO = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250919/kaakcn/move_input_video.mp4"


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
            "title": "Creative Media P4 live smoke",
            "userId": "creative-media-p4-live-smoke",
            **scope,
            "scopeMode": "explicit",
        },
    )
    session_id = str(session.get("id") or session.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError(f"session creation returned no id: {session}")
    return session_id


def _verify_artifacts(base_url: str, job: dict[str, Any], session_id: str) -> None:
    artifacts = job.get("artifacts") or []
    if not artifacts:
        raise RuntimeError(f"job succeeded without artifacts: {job.get('jobId')}")
    for artifact in artifacts:
        artifact_id = artifact.get("artifactId") or artifact.get("id")
        if not artifact_id:
            raise RuntimeError(f"artifact missing id: {artifact}")
        response = requests.get(
            f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content",
            params={"sessionId": session_id},
            timeout=120,
        )
        response.raise_for_status()
        if not response.content:
            raise RuntimeError(f"artifact content is empty: {artifact_id}")


def _submit_job(base_url: str, payload: dict[str, Any], label: str, *, timeout_seconds: int = 600) -> dict[str, Any]:
    job = _post(base_url, "/creative-media/jobs", payload)["job"]
    deadline = time.time() + timeout_seconds
    while job.get("status") not in {"succeeded", "failed", "cancelled"} and time.time() < deadline:
        time.sleep(int(payload.get("pollIntervalSeconds") or 10))
        job = _get(base_url, f"/creative-media/jobs/{job['jobId']}")["job"]
    if job.get("status") != "succeeded":
        raise RuntimeError(f"{label} did not succeed: {job}")
    session_id = str(payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError("live smoke job is missing required session authority")
    _verify_artifacts(base_url, job, session_id)
    print(f"[PASS] {label}: {job['jobId']} quality={job.get('qualityStatus')}")
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description="P4 Creative Media live smoke. Uses env vars only; never pass API keys as arguments.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9530/v1")
    parser.add_argument("--workspace", default=r"E:\Projects\test2")
    parser.add_argument("--live-dashscope", action="store_true")
    parser.add_argument("--live-openai-compatible", action="store_true")
    parser.add_argument("--openai-edit-image-path", default="", help="Optional local image path for OpenAI-compatible /images/edits smoke.")
    parser.add_argument("--skip-long-video", action="store_true")
    args = parser.parse_args()

    scope = {
        "projectId": "creative-media-p4-smoke",
        "workspaceId": "test2",
        "workspacePath": args.workspace,
    }
    planned = [
        ("DashScope image generate", {"modality": "image", "adapter": "dashscope", "model": "qwen-image-2.0", "prompt": "A tiny original glass rabbit sculpture on a white desk, soft studio lighting.", "ratio": "1:1", **scope}),
        ("DashScope image edit", {"modality": "image", "operationKind": "image.edit", "adapter": "dashscope", "model": "qwen-image-2.0-pro", "prompt": "Make the background a clean pastel blue studio backdrop while preserving the main subject.", "imageUrls": [PUBLIC_IMAGE], **scope}),
        ("DashScope T2V", {"modality": "video", "operationKind": "video.text_to_video", "adapter": "dashscope", "model": "wan2.7-t2v", "prompt": "A five second cinematic shot of a tiny glass rabbit on a white desk, slow push-in camera, stable single action.", "duration": 5, "ratio": "16:9", "resolution": "720P", "wait": True, **scope}),
        ("DashScope I2V", {"modality": "video", "operationKind": "video.image_to_video", "adapter": "dashscope", "model": "wan2.7-i2v", "prompt": "Animate the subject with subtle breathing-like motion and a slow camera push-in.", "imageUrls": [PUBLIC_IMAGE], "duration": 5, "ratio": "16:9", "resolution": "720P", "wait": True, **scope}),
    ]
    advanced = [
        ("DashScope action transfer", {"modality": "video", "operationKind": "video.action_transfer", "adapter": "dashscope", "model": "wan2.2-animate-move", "prompt": "Transfer the reference motion to the target character while preserving identity.", "imageUrls": [PUBLIC_IMAGE], "referenceVideoUrl": PUBLIC_VIDEO, "wait": True, **scope}),
        ("DashScope video edit", {"modality": "video", "operationKind": "video.video_edit", "adapter": "dashscope", "model": "wan2.7-videoedit", "prompt": "Change the visual style to a clean cinematic commercial look while preserving the motion.", "videoUrl": PUBLIC_VIDEO, "wait": True, **scope}),
    ]

    if not args.live_dashscope and not args.live_openai_compatible:
        print("Dry run. Planned P4 checks:")
        for label, payload in [*planned, *advanced]:
            print(f"- {label}: operation={payload.get('operationKind', 'image.generate')} model={payload.get('model')} workspace={args.workspace}")
        print("Pass --live-dashscope and ensure DASHSCOPE_API_KEY is set to execute DashScope calls.")
        return 0

    workspace_path = Path(args.workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise RuntimeError("--workspace must be an existing directory for live execution")
    scope["workspacePath"] = str(workspace_path)
    scope["sessionId"] = _create_session(args.base_url, scope)

    if args.live_dashscope:
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise RuntimeError("DASHSCOPE_API_KEY is not set; refusing to run live DashScope smoke.")
        for label, payload in planned:
            _submit_job(args.base_url, payload, label)
        if not args.skip_long_video:
            for label, payload in advanced:
                try:
                    _submit_job(args.base_url, payload, label, timeout_seconds=900)
                except Exception as exc:
                    print(f"[WARN] {label}: {type(exc).__name__}: {exc}")

    if args.live_openai_compatible:
        if not args.openai_edit_image_path:
            print("[SKIP] OpenAI-compatible image edit requires --openai-edit-image-path with a local source image.")
        else:
            _submit_job(
                args.base_url,
                {
                    "modality": "image",
                    "operationKind": "image.edit",
                    "adapter": "openai_images",
                    "model": "gpt-image-2",
                    "prompt": "Add a subtle soft shadow while preserving the original object.",
                    "imagePath": args.openai_edit_image_path,
                    **scope,
                },
                "OpenAI-compatible image edit",
            )
    quality = _get(args.base_url, "/creative-media/quality-jobs")
    costs = _get(args.base_url, "/creative-media/cost-ledger")
    safety = _get(args.base_url, "/creative-media/safety-events")
    print(f"[INFO] qualityJobs={len(quality.get('qualityJobs') or [])} costEntries={len(costs.get('entries') or [])} safetyEvents={len(safety.get('events') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
