from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict

import requests


CODE_ASSIST_ENDPOINT_PROD = "https://cloudcode-pa.googleapis.com"
GOOGLE_NODE_CLIENT_UA = "google-api-nodejs-client/9.15.1"


def _resolve_platform() -> str:
    if sys.platform == "win32":
        return "WINDOWS"
    if sys.platform == "darwin":
        return "MACOS"
    return "PLATFORM_UNSPECIFIED"


def _platform_attempt_order() -> list[str]:
    primary = _resolve_platform()
    attempts = [primary]
    if primary != "PLATFORM_UNSPECIFIED":
        attempts.append("PLATFORM_UNSPECIFIED")
    return attempts


def _normalize_base_url(base_url: str | None) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    return normalized or CODE_ASSIST_ENDPOINT_PROD


def _build_metadata(platform_name: str, project_id: str = "") -> Dict[str, str]:
    metadata = {
        "ideType": "ANTIGRAVITY",
        "platform": platform_name,
        "pluginType": "GEMINI",
    }
    if project_id:
        metadata["duetProject"] = project_id
    return metadata


def _build_headers(access_token: str, metadata: Dict[str, str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": GOOGLE_NODE_CLIENT_UA,
        "X-Goog-Api-Client": f"gl-python/{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "Client-Metadata": json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
    }


def _extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    text = response.text.strip()
    if text:
        return text[:240]
    return f"HTTP {response.status_code} {response.reason}"


def _extract_project_id(payload: Dict[str, Any], fallback: str = "") -> str:
    project = payload.get("cloudaicompanionProject")
    if isinstance(project, str) and project.strip():
        return project.strip()
    if isinstance(project, dict):
        nested = project.get("id")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    current_tier = payload.get("currentTier")
    if isinstance(current_tier, dict):
        project = current_tier.get("cloudaicompanionProject")
        if isinstance(project, dict):
            nested = project.get("id")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return fallback


def probe_gemini_cli_connection(
    *,
    access_token: str,
    base_url: str | None = None,
    project_id: str = "",
    timeout: float = 30.0,
) -> Dict[str, Any]:
    endpoint = _normalize_base_url(base_url)
    if not access_token.strip():
        return {
            "ok": False,
            "latencyMs": 0.0,
            "message": "Gemini CLI OAuth 文件中未找到 access_token。",
            "projectId": project_id,
        }

    last_error = "Gemini CLI OAuth 连接失败。"
    started = time.perf_counter()
    for platform_name in _platform_attempt_order():
        metadata = _build_metadata(platform_name, project_id)
        payload: Dict[str, Any] = {"metadata": metadata}
        if project_id:
            payload["cloudaicompanionProject"] = project_id
        response = requests.post(
            f"{endpoint}/v1internal:loadCodeAssist",
            headers=_build_headers(access_token, metadata),
            json=payload,
            timeout=timeout,
        )
        if not response.ok:
            last_error = _extract_error_message(response)
            continue

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        data = response.json() if response.content else {}
        resolved_project = _extract_project_id(data if isinstance(data, dict) else {}, project_id)
        tier = ""
        if isinstance(data, dict):
            current_tier = data.get("currentTier")
            if isinstance(current_tier, dict):
                tier = str(current_tier.get("id") or "")

        message_parts = ["Gemini CLI OAuth 可用"]
        if resolved_project:
            message_parts.append(f"项目 {resolved_project}")
        if tier:
            message_parts.append(f"层级 {tier}")

        return {
            "ok": True,
            "latencyMs": latency_ms,
            "message": " · ".join(message_parts),
            "projectId": resolved_project,
            "requestKind": "gemini_cli_oauth",
            "metadata": {
                "platform": platform_name,
                "baseUrl": endpoint,
                "tierId": tier,
            },
        }

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": False,
        "latencyMs": latency_ms,
        "message": last_error,
        "projectId": project_id,
        "requestKind": "gemini_cli_oauth",
        "metadata": {
            "baseUrl": endpoint,
        },
    }
