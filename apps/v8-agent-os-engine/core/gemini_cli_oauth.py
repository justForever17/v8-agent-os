from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

import requests


CODE_ASSIST_ENDPOINT_PROD = "https://cloudcode-pa.googleapis.com"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_NODE_CLIENT_UA = "google-api-nodejs-client/9.15.1"
GEMINI_CLI_OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GEMINI_CLI_OAUTH_CLIENT_SECRET = "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"
GEMINI_CLI_OAUTH_SCOPE = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _safe_json_load(raw_text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw_text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _read_oauth_payload(oauth_path: Path) -> Dict[str, Any]:
    raw_text = oauth_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return {}
    payload = _safe_json_load(raw_text)
    if payload:
        return payload
    return {"access_token": raw_text}


def _write_oauth_payload(oauth_path: Path, payload: Dict[str, Any]) -> None:
    oauth_path.parent.mkdir(parents=True, exist_ok=True)
    oauth_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_platform() -> str:
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower() or os.environ.get("PROCESSOR_ARCHITEW6432", "").lower()
    if sys.platform == "win32" and arch in {"amd64", "x86_64"}:
        return "WINDOWS_AMD64"
    if sys.platform == "darwin":
        machine = os.uname().machine.lower() if hasattr(os, "uname") else ""
        if machine == "arm64":
            return "DARWIN_ARM64"
        if machine in {"x86_64", "amd64"}:
            return "DARWIN_AMD64"
    if sys.platform == "linux":
        machine = os.uname().machine.lower() if hasattr(os, "uname") else ""
        if machine in {"x86_64", "amd64"}:
            return "LINUX_AMD64"
        if machine == "arm64":
            return "LINUX_ARM64"
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
        "ideName": "IDE_UNSPECIFIED",
        "ideVersion": "0.37.0",
        "platform": platform_name,
        "pluginType": "GEMINI",
        "updateChannel": "stable",
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


def _refresh_token_payload(
    *,
    refresh_token: str,
    timeout: float,
) -> Dict[str, Any]:
    response = requests.post(
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": GEMINI_CLI_OAUTH_CLIENT_ID,
            "client_secret": GEMINI_CLI_OAUTH_CLIENT_SECRET,
            "scope": " ".join(GEMINI_CLI_OAUTH_SCOPE),
        },
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(_extract_error_message(response))
    payload = response.json() if response.content else {}
    if not isinstance(payload, dict) or not str(payload.get("access_token") or "").strip():
        raise RuntimeError("Google token refresh 未返回 access_token。")
    return payload


def _resolve_token_expired(payload: Dict[str, Any]) -> bool:
    expiry_raw = payload.get("expiry_date")
    if expiry_raw is None:
        return not bool(str(payload.get("access_token") or "").strip())
    try:
        expiry_ms = int(expiry_raw)
    except Exception:
        return not bool(str(payload.get("access_token") or "").strip())
    return expiry_ms <= int(time.time() * 1000) + 60_000


def resolve_gemini_cli_runtime_session(
    *,
    oauth_path: str = "",
    access_token: str = "",
    refresh_token: str = "",
    base_url: str | None = None,
    project_id: str = "",
    timeout: float = 30.0,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    endpoint = _normalize_base_url(base_url)
    payload: Dict[str, Any] = {}
    oauth_file = Path(str(oauth_path or "").strip()).expanduser() if str(oauth_path or "").strip() else None
    if oauth_file and oauth_file.exists():
        payload = _read_oauth_payload(oauth_file)

    resolved_refresh_token = str(refresh_token or payload.get("refresh_token") or "").strip()
    resolved_access_token = str(access_token or payload.get("access_token") or "").strip()
    if not resolved_access_token and not resolved_refresh_token:
        return {
            "ok": False,
            "message": "Gemini CLI OAuth 文件中未找到 access_token 或 refresh_token。",
            "projectId": project_id,
            "baseUrl": endpoint,
            "oauthPath": str(oauth_file) if oauth_file else "",
        }

    needs_refresh = force_refresh or bool(resolved_refresh_token and (not resolved_access_token or _resolve_token_expired(payload)))
    if needs_refresh:
        refreshed = _refresh_token_payload(refresh_token=resolved_refresh_token, timeout=timeout)
        expiry_ms = int(time.time() * 1000) + int(refreshed.get("expires_in") or 3600) * 1000
        payload = {
            **payload,
            **refreshed,
            "refresh_token": str(refreshed.get("refresh_token") or resolved_refresh_token or ""),
            "expiry_date": expiry_ms,
        }
        if oauth_file is not None:
            _write_oauth_payload(oauth_file, payload)
        resolved_access_token = str(payload.get("access_token") or "").strip()
        resolved_refresh_token = str(payload.get("refresh_token") or "").strip()

    return {
        "ok": bool(resolved_access_token),
        "message": "Gemini CLI OAuth 凭据已就绪。" if resolved_access_token else "Gemini CLI OAuth 凭据不可用。",
        "projectId": str(project_id or payload.get("projectId") or payload.get("project_id") or ""),
        "baseUrl": endpoint,
        "oauthPath": str(oauth_file) if oauth_file else "",
        "accessToken": resolved_access_token,
        "refreshToken": resolved_refresh_token,
        "oauthPayload": payload,
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
    oauth_path: str = "",
    access_token: str,
    base_url: str | None = None,
    project_id: str = "",
    timeout: float = 30.0,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    session = resolve_gemini_cli_runtime_session(
        oauth_path=oauth_path,
        access_token=access_token,
        base_url=base_url,
        project_id=project_id,
        timeout=timeout,
        force_refresh=force_refresh,
    )
    endpoint = str(session.get("baseUrl") or _normalize_base_url(base_url))
    if not session.get("ok"):
        return {
            "ok": False,
            "latencyMs": 0.0,
            "message": str(session.get("message") or "Gemini CLI OAuth 凭据不可用。"),
            "projectId": str(session.get("projectId") or project_id or ""),
            "requestKind": "gemini_cli_oauth",
            "metadata": {"baseUrl": endpoint},
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
            headers=_build_headers(str(session.get("accessToken") or ""), metadata),
            json=payload,
            timeout=timeout,
        )
        if response.status_code == 401 and not force_refresh:
            return probe_gemini_cli_connection(
                oauth_path=oauth_path,
                access_token=access_token,
                base_url=base_url,
                project_id=project_id,
                timeout=timeout,
                force_refresh=True,
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
            "accessToken": str(session.get("accessToken") or ""),
            "metadata": {
                "platform": platform_name,
                "baseUrl": endpoint,
                "tierId": tier,
                "clientMetadata": metadata,
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


def bootstrap_gemini_cli_runtime(
    *,
    oauth_path: str = "",
    access_token: str,
    base_url: str | None = None,
    project_id: str = "",
    timeout: float = 30.0,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    result = probe_gemini_cli_connection(
        oauth_path=oauth_path,
        access_token=access_token,
        base_url=base_url,
        project_id=project_id,
        timeout=timeout,
        force_refresh=force_refresh,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "message": str(result.get("message") or "Gemini CLI runtime bootstrap failed."),
            "projectId": str(result.get("projectId") or project_id or ""),
            "requestKind": "gemini_cli_runtime_bootstrap",
            "metadata": dict(result.get("metadata") or {}),
        }
    metadata = dict(result.get("metadata") or {})
    metadata.setdefault("requestKind", "gemini_cli_runtime_bootstrap")
    return {
        "ok": True,
        "message": str(result.get("message") or "Gemini CLI runtime bootstrap ready."),
        "projectId": str(result.get("projectId") or project_id or ""),
        "accessToken": str(result.get("accessToken") or ""),
        "requestKind": "gemini_cli_runtime_bootstrap",
        "metadata": metadata,
    }
