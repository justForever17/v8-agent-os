from __future__ import annotations

from pathlib import Path

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

OAUTH_CORE_PATH = V8_AGENT_OS_HOME / "core" / "oauth"
OAUTH_PROVIDERS_PATH = OAUTH_CORE_PATH / "providers"


def canonical_oauth_provider_dir(provider_id: str) -> Path:
    slug = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(provider_id or "provider").strip().lower())
    normalized = slug.strip("-_") or "provider"
    return OAUTH_PROVIDERS_PATH / normalized


def build_oauth_ref(provider_id: str, filename: str) -> str:
    provider_dir = canonical_oauth_provider_dir(provider_id)
    return str((provider_dir.name + "/" + filename).replace("\\", "/"))


def resolve_oauth_ref_path(provider_id: str, oauth_ref: str | None) -> Path:
    normalized = str(oauth_ref or "").strip().replace("\\", "/")
    if not normalized:
        return canonical_oauth_provider_dir(provider_id)
    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate
    return OAUTH_PROVIDERS_PATH / normalized
