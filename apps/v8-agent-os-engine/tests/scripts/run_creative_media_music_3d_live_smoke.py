from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "creative_media_music_3d"


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
            "title": "Creative Media music and 3D live smoke",
            "userId": "creative-media-music-3d-live-smoke",
            **scope,
            "scopeMode": "explicit",
        },
    )
    session_id = str(session.get("id") or session.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError(f"session creation returned no id: {session}")
    return session_id


def _download_artifacts(
    base_url: str,
    job: dict[str, Any],
    output_dir: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    downloads: list[dict[str, Any]] = []
    for artifact in list(job.get("artifacts") or []):
        artifact_id = str(artifact.get("artifactId") or artifact.get("id") or "").strip()
        if not artifact_id:
            raise RuntimeError(f"artifact missing id: {artifact}")
        response = requests.get(
            f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content",
            params={"sessionId": session_id},
            timeout=180,
        )
        response.raise_for_status()
        if not response.content:
            raise RuntimeError(f"artifact content is empty: {artifact_id}")
        title = str(artifact.get("title") or artifact_id).strip() or artifact_id
        suffix = Path(title).suffix or ".bin"
        target = output_dir / f"{artifact_id}{suffix}"
        target.write_bytes(response.content)
        downloads.append(
            {
                "artifactId": artifact_id,
                "kind": artifact.get("kind"),
                "mimeType": artifact.get("mimeType"),
                "downloadPath": str(target),
                "sizeBytes": len(response.content),
            }
        )
    if not downloads:
        raise RuntimeError(f"job succeeded without artifacts: {job.get('jobId')}")
    return downloads


def _submit_and_wait(base_url: str, label: str, payload: dict[str, Any], report_dir: Path, *, timeout_seconds: int) -> dict[str, Any]:
    job = _post(base_url, "/creative-media/jobs", payload)["job"]
    deadline = time.time() + timeout_seconds
    while job.get("status") not in {"succeeded", "failed", "cancelled"} and time.time() < deadline:
        time.sleep(int(payload.get("pollIntervalSeconds") or 8))
        job = _get(base_url, f"/creative-media/jobs/{job['jobId']}")["job"]
    if job.get("status") != "succeeded":
        raise RuntimeError(f"{label} did not succeed: {job}")
    session_id = str(payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError("live smoke job is missing required session authority")
    downloads = _download_artifacts(
        base_url,
        job,
        report_dir / label.replace(" ", "_").lower(),
        session_id,
    )
    return {"label": label, "jobId": job.get("jobId"), "status": job.get("status"), "artifacts": downloads}


def _operation_summary(prefs: dict[str, Any], operation_kind: str) -> dict[str, Any]:
    rows = list(prefs.get("operationRows") or [])
    options = [
        {
            "providerId": item.get("providerId"),
            "modelId": item.get("modelId"),
            "modelRef": item.get("modelRef"),
            "adapter": item.get("adapter"),
            "available": item.get("available"),
            "briefOnly": item.get("briefOnly"),
        }
        for item in list(prefs.get("connectedOptions") or [])
        if item.get("operationKind") == operation_kind
    ]
    return {
        "row": next((item for item in rows if item.get("operationKind") == operation_kind), {}),
        "connectedOptions": options,
    }


def _option_payload(summary: dict[str, Any]) -> dict[str, Any]:
    row = dict(summary.get("row") or {})
    selected = {str(item or "") for item in list(row.get("selectedModelRefs") or [])}
    options = [dict(item) for item in list(summary.get("connectedOptions") or [])]
    option = next((item for item in options if selected and str(item.get("modelRef") or "") in selected), None) or (options[0] if options else {})
    if not option:
        return {}
    return {
        "providerId": option.get("providerId"),
        "modelId": option.get("modelId"),
        "adapter": option.get("adapter"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Creative Media music/3D smoke. Default is dry-run; --live consumes provider quota.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9530/v1")
    parser.add_argument("--workspace", default=r"E:\Projects\test2")
    parser.add_argument("--project-id", default="creative-media-music-3d-smoke")
    parser.add_argument("--workspace-id", default="test2")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--skip-music", action="store_true")
    parser.add_argument("--skip-3d", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / stamp
    report_dir.mkdir(parents=True, exist_ok=True)

    try:
        prefs = _get(args.base_url, "/creative-media/model-preferences")
    except Exception as exc:
        report = {"live": args.live, "status": "engine_unavailable", "error": f"{type(exc).__name__}: {exc}"}
        (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[FAIL] Engine is unavailable at {args.base_url}: {exc}")
        return 2 if args.live else 0

    report: dict[str, Any] = {
        "live": args.live,
        "baseUrl": args.base_url,
        "workspace": args.workspace,
        "operations": {
            "music.generate": _operation_summary(prefs, "music.generate"),
            "music.cover": _operation_summary(prefs, "music.cover"),
            "model3d.generate": _operation_summary(prefs, "model3d.generate"),
        },
        "results": [],
    }
    (report_dir / "dry_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.live:
        print("[DRY-RUN] Creative Media music/3D executable options:")
        for operation_kind, summary in report["operations"].items():
            row = summary.get("row") or {}
            print(f"- {operation_kind}: enabled={row.get('enabled')} optionCount={row.get('optionCount')} selected={row.get('selectedModelRefs')}")
        print(f"[DRY-RUN] Report: {report_dir / 'dry_run.json'}")
        return 0

    workspace_path = Path(args.workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise RuntimeError("--workspace must be an existing directory for --live")
    scope = {
        "projectId": args.project_id,
        "workspaceId": args.workspace_id,
        "workspacePath": str(workspace_path),
    }
    scope["sessionId"] = _create_session(args.base_url, scope)
    if not args.skip_music:
        music_option = _option_payload(report["operations"]["music.generate"])
        if not music_option:
            raise RuntimeError("No configured music.generate model is visible in Creative Media preferences.")
        report["results"].append(
            _submit_and_wait(
                args.base_url,
                "music_generate",
                {
                    "modality": "music",
                    "operationKind": "music.generate",
                    "prompt": "short upbeat 8-bit game loop, heroic, clean melody, no vocals",
                    "is_instrumental": True,
                    "wait": True,
                    "timeoutSeconds": args.timeout_seconds,
                    "pollIntervalSeconds": 8,
                    **music_option,
                    **scope,
                },
                report_dir,
                timeout_seconds=args.timeout_seconds,
            )
        )
    if not args.skip_3d:
        model3d_option = _option_payload(report["operations"]["model3d.generate"])
        if not model3d_option:
            raise RuntimeError("No configured model3d.generate model is visible in Creative Media preferences.")
        report["results"].append(
            _submit_and_wait(
                args.base_url,
                "model3d_generate",
                {
                    "modality": "model3d",
                    "operationKind": "model3d.generate",
                    "prompt": "a small low-poly treasure chest game prop, clean geometry, game asset",
                    "resultFormat": "GLB",
                    "wait": True,
                    "timeoutSeconds": args.timeout_seconds,
                    "pollIntervalSeconds": 10,
                    **model3d_option,
                    **scope,
                },
                report_dir,
                timeout_seconds=args.timeout_seconds,
            )
        )
    report["status"] = "succeeded"
    (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PASS] Report: {report_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
