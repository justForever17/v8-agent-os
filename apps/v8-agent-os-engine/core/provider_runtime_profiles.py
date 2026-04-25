from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.oauth.credentials import sanitize_oauth_path
from core.oauth.store import resolve_oauth_ref_path


def normalize_api_standard(value: Any) -> str:
    return str(value or "openai").strip().lower() or "openai"


def is_gemini_cli_provider(
    *,
    api_standard: Any,
    provider_config: Mapping[str, Any] | None,
    oauth_flavor: str = "",
) -> bool:
    normalized_api = normalize_api_standard(api_standard)
    normalized_flavor = str(oauth_flavor or "").strip().lower()
    provider = dict(provider_config or {})
    oauth_preset = str(provider.get("oauth_preset") or provider.get("oauthPreset") or "").strip().lower()
    return normalized_api in {"google", "gemini"} and (normalized_flavor == "gemini_cli" or oauth_preset == "geminicli")


def is_anthropic_compat_provider(*, api_standard: Any, base_url: Any) -> bool:
    normalized_api = normalize_api_standard(api_standard)
    normalized_base = str(base_url or "").strip().lower().rstrip("/")
    return normalized_api == "anthropic" and normalized_base.startswith("https://api.deepseek.com/anthropic")


def is_codex_oauth_provider(
    *,
    api_standard: Any,
    provider_config: Mapping[str, Any] | None,
    oauth_flavor: str = "",
) -> bool:
    normalized_api = normalize_api_standard(api_standard)
    normalized_flavor = str(oauth_flavor or "").strip().lower()
    provider = dict(provider_config or {})
    oauth_preset = str(provider.get("oauth_preset") or provider.get("oauthPreset") or "").strip().lower()
    normalized_base = str(provider.get("base_url") or provider.get("baseUrl") or "").strip().lower().rstrip("/")
    return (
        normalized_api == "openai"
        and (normalized_flavor == "codex" or oauth_preset == "codex")
        and normalized_base.startswith("https://chatgpt.com/backend-api")
    )


def _resolve_oauth_path(provider_id: str, provider_config: Mapping[str, Any]) -> Path | None:
    provider = dict(provider_config or {})
    oauth_ref = str(provider.get("oauth_ref") or provider.get("oauthRef") or "").strip()
    if oauth_ref:
        try:
            return resolve_oauth_ref_path(provider_id, oauth_ref)
        except Exception:
            return None
    api_key = str(provider.get("api_key") or provider.get("apiKey") or "").strip()
    if api_key.startswith("oauth:"):
        try:
            return Path(sanitize_oauth_path(api_key[6:])).expanduser()
        except Exception:
            return None
    return None


def runtime_readiness_for_provider(
    *,
    provider_id: str = "",
    api_standard: Any,
    provider_config: Mapping[str, Any] | None,
    oauth_flavor: str = "",
    credential: str = "",
    oauth_path: str = "",
) -> tuple[bool, str]:
    provider = dict(provider_config or {})
    if is_gemini_cli_provider(
        api_standard=api_standard,
        provider_config=provider,
        oauth_flavor=oauth_flavor,
    ):
        if str(credential or "").strip():
            return True, ""
        explicit_oauth_path = str(oauth_path or "").strip()
        if explicit_oauth_path and Path(explicit_oauth_path).expanduser().exists():
            return True, ""
        resolved_oauth_path = _resolve_oauth_path(provider_id, provider)
        if resolved_oauth_path and resolved_oauth_path.exists():
            return True, ""
        return False, "gemini_cli_credentials_missing"
    if is_codex_oauth_provider(
        api_standard=api_standard,
        provider_config=provider,
        oauth_flavor=oauth_flavor,
    ):
        if str(credential or "").strip():
            return True, ""
        explicit_oauth_path = str(oauth_path or "").strip()
        if explicit_oauth_path and Path(explicit_oauth_path).expanduser().exists():
            return True, ""
        resolved_oauth_path = _resolve_oauth_path(provider_id, provider)
        if resolved_oauth_path and resolved_oauth_path.exists():
            return True, ""
        return False, "codex_oauth_credentials_missing"
    return True, ""


def resolve_provider_adapter(
    *,
    api_standard: Any,
    provider_config: Mapping[str, Any] | None,
    oauth_flavor: str = "",
) -> tuple[str, str]:
    provider = dict(provider_config or {})
    normalized_api = normalize_api_standard(api_standard)
    if is_gemini_cli_provider(
        api_standard=normalized_api,
        provider_config=provider,
        oauth_flavor=oauth_flavor,
    ):
        return "gemini-cli-runtime", "geminiCli runtime"
    if is_codex_oauth_provider(
        api_standard=normalized_api,
        provider_config=provider,
        oauth_flavor=oauth_flavor,
    ):
        return "openai-codex-responses", "OpenAI Codex Responses runtime"
    if is_anthropic_compat_provider(
        api_standard=normalized_api,
        base_url=provider.get("base_url") or provider.get("baseUrl") or "",
    ):
        return "anthropic-compat", "DeepSeek Anthropic compat"
    if normalized_api in {"google", "gemini"}:
        return "gemini", "gemini"
    if normalized_api == "anthropic":
        return "anthropic", "anthropic"
    return "openai-compatible", "openai-compatible"
