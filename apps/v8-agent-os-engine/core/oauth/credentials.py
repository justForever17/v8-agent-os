from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from core.oauth.store import resolve_oauth_ref_path


_INVISIBLE_PATH_MARKERS = {
    "\ufeff",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u200e",
    "\u200f",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
}


def sanitize_oauth_path(raw_path: str | None) -> str:
    raw = str(raw_path or "")
    cleaned = "".join(ch for ch in raw if ch not in _INVISIBLE_PATH_MARKERS)
    return cleaned.strip()


def normalize_oauth_reference(raw_value: str | None) -> str:
    raw = str(raw_value or "").strip()
    if not raw.startswith("oauth:"):
        return raw
    path = sanitize_oauth_path(raw[6:])
    return f"oauth:{path}" if path else "oauth:"


def _read_oauth_payload(oauth_path: Path) -> Dict[str, Any]:
    text = oauth_path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"access_token": text}


def _pick_first(payload: Dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current:
            return str(current)
    return ""


def resolve_oauth_reference(
    raw_value: str | None,
    *,
    provider_id: str = "",
    api_standard: str = "openai",
) -> Dict[str, Any]:
    normalized = normalize_oauth_reference(raw_value)
    if not normalized:
        return {"credential": "", "normalizedReference": "", "oauthPath": ""}

    if not normalized.startswith("oauth:"):
        return {
            "credential": normalized,
            "normalizedReference": normalized,
            "oauthPath": "",
        }

    oauth_path_value = sanitize_oauth_path(normalized[6:])
    oauth_path = Path(oauth_path_value).expanduser()
    result: Dict[str, Any] = {
        "credential": "",
        "normalizedReference": f"oauth:{oauth_path_value}",
        "oauthPath": str(oauth_path),
        "projectId": "",
        "accountId": "",
        "accessToken": "",
        "oauthFlavor": "",
        "error": "",
    }

    if not oauth_path.exists():
        result["error"] = f"OAuth 文件不存在：{oauth_path}"
        return result

    try:
        payload = _read_oauth_payload(oauth_path)
    except Exception as exc:
        result["error"] = f"OAuth 文件读取失败：{oauth_path.name} ({exc})"
        return result

    access_token = _pick_first(
        payload,
        ("access_token",),
        ("token",),
        ("tokens", "access_token"),
        ("tokens", "token"),
    )
    api_key = _pick_first(
        payload,
        ("api_key",),
        ("google_api_key",),
        ("OPENAI_API_KEY",),
        ("GEMINI_API_KEY",),
    )
    project_id = _pick_first(payload, ("projectId",), ("project_id",))
    account_id = _pick_first(payload, ("accountId",), ("account_id",), ("tokens", "account_id"))
    api_standard_lower = str(api_standard or "openai").lower()
    provider_lower = str(provider_id or "").lower()

    result["projectId"] = project_id
    result["accountId"] = account_id
    result["accessToken"] = access_token

    if api_standard_lower in {"google", "gemini"}:
        if api_key:
            result["credential"] = api_key
            return result
        if access_token:
            result["credential"] = access_token
            result["oauthFlavor"] = "gemini_cli"
            return result
        result["error"] = "OAuth 文件中未找到 Gemini 可用的 API Key。"
        return result

    if access_token:
        result["credential"] = access_token
        if provider_lower in {"qwen", "qwen-oauth"}:
            result["oauthFlavor"] = "qwen"
        elif account_id or oauth_path.name.lower() == "auth.json":
            result["oauthFlavor"] = "codex"
        return result

    if api_key:
        result["credential"] = api_key
        return result

    if provider_lower in {"qwen", "qwen-oauth"}:
        result["error"] = "Qwen OAuth 文件中未找到 access_token。"
        return result

    result["error"] = "OAuth 文件中未找到可用凭证。"
    return result


def resolve_provider_oauth_credential(
    *,
    provider_id: str,
    provider_config: Dict[str, Any],
) -> Dict[str, Any]:
    credential_mode = str(provider_config.get("credential_mode") or "").strip()
    oauth_ref = str(provider_config.get("oauth_ref") or "").strip()
    api_standard = str(provider_config.get("api_standard") or "openai")

    if credential_mode == "oauthFile" and oauth_ref:
        oauth_path = resolve_oauth_ref_path(provider_id, oauth_ref)
        return resolve_oauth_reference(
            f"oauth:{oauth_path}",
            provider_id=provider_id,
            api_standard=api_standard,
        ) | {
            "oauthRef": oauth_ref,
            "credentialMode": credential_mode,
        }

    return resolve_oauth_reference(
        str(provider_config.get("api_key") or ""),
        provider_id=provider_id,
        api_standard=api_standard,
    ) | {
        "oauthRef": oauth_ref,
        "credentialMode": credential_mode or ("oauthFile" if str(provider_config.get("api_key") or "").startswith("oauth:") else "apiKey"),
    }
