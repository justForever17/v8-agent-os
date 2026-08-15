from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "creative_media_voice"


def _post(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{base_url.rstrip('/')}{path}", json=payload, timeout=240)
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
            "title": "Creative Media voice live smoke",
            "userId": "creative-media-voice-live-smoke",
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
        suffix = Path(title).suffix or ".mp3"
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


def _submit_and_download(base_url: str, label: str, payload: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    job = _post(base_url, "/creative-media/jobs", payload)["job"]
    if job.get("status") not in {"succeeded", "failed", "cancelled"}:
        deadline = time.time() + int(payload.get("timeoutSeconds") or 240)
        while job.get("status") not in {"succeeded", "failed", "cancelled"} and time.time() < deadline:
            time.sleep(int(payload.get("pollIntervalSeconds") or 5))
            job = _get(base_url, f"/creative-media/jobs/{job['jobId']}")["job"]
    if job.get("status") != "succeeded":
        raise RuntimeError(f"{label} did not succeed: {job}")
    session_id = str(payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError("live smoke job is missing required session authority")
    return {
        "label": label,
        "jobId": job.get("jobId"),
        "status": job.get("status"),
        "providerResponse": job.get("providerResponse"),
        "artifacts": _download_artifacts(base_url, job, report_dir / label, session_id),
    }


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
    minimax_options = [item for item in options if str(item.get("adapter") or "") == "minimax_tts"]
    option = (
        next((item for item in minimax_options if selected and str(item.get("modelRef") or "") in selected), None)
        or (minimax_options[0] if minimax_options else {})
    )
    if not option:
        return {}
    return {
        "providerId": option.get("providerId"),
        "modelId": option.get("modelId"),
        "adapter": option.get("adapter"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Creative Media voice smoke. Default is dry-run; --live consumes provider quota.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9530/v1")
    parser.add_argument("--workspace", default=r"E:\Projects\test1")
    parser.add_argument("--project-id", default="creative-media-voice-smoke")
    parser.add_argument("--workspace-id", default="test1")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--skip-design", action="store_true")
    parser.add_argument("--skip-tts", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / stamp
    report_dir.mkdir(parents=True, exist_ok=True)

    prefs = _get(args.base_url, "/creative-media/model-preferences")
    report: dict[str, Any] = {
        "live": args.live,
        "baseUrl": args.base_url,
        "workspace": args.workspace,
        "operations": {
            "voice.design": _operation_summary(prefs, "voice.design"),
            "voice.tts": _operation_summary(prefs, "voice.tts"),
        },
        "results": [],
    }
    (report_dir / "dry_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.live:
        print("[DRY-RUN] Creative Media voice executable options:")
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
    design_option = _option_payload(report["operations"]["voice.design"])
    tts_option = _option_payload(report["operations"]["voice.tts"])
    voice_id = "female-shaonv"
    if not args.skip_design:
        if not design_option:
            raise RuntimeError("No MiniMax voice.design model is visible in Creative Media preferences.")
        design_result = _submit_and_download(
            args.base_url,
            "voice_design",
            {
                "modality": "voice",
                "operationKind": "voice.design",
                "voicePrompt": "温柔、清晰、带一点未来科技感的中文女性讲解音色，适合 V8 Agent OS 产品演示旁白。",
                "previewText": "你好，我是 V8 Agent OS 的演示旁白。接下来我会用简洁清晰的方式介绍多运行时协作。",
                **design_option,
                **scope,
            },
            report_dir,
        )
        report["results"].append(design_result)
        voice_id = str((design_result.get("providerResponse") or {}).get("voiceId") or voice_id)
    if not args.skip_tts:
        if not tts_option:
            raise RuntimeError("No MiniMax voice.tts model is visible in Creative Media preferences.")
        report["results"].append(
            _submit_and_download(
                args.base_url,
                "voice_tts",
                {
                    "modality": "voice",
                    "operationKind": "voice.tts",
                    "text": "V8 Agent OS 可以把工程运行时、创意媒体运行时和子代理协作起来，生成页面、素材、配音、音乐和三维资产。",
                    "voiceId": voice_id,
                    "format": "mp3",
                    **tts_option,
                    **scope,
                },
                report_dir,
            )
        )
    report["status"] = "succeeded"
    (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PASS] Report: {report_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
