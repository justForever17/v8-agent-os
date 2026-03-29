from __future__ import annotations

from copy import deepcopy
from typing import Any


CANONICAL_SYSTEM_IDENTITY: dict[str, Any] = {
    "systemName": "V8 Agent OS",
    "systemSlug": "v8-agent-os",
    "author": "justForever17",
    "legacyNames": ["v8chat"],
    "identityTags": [
        "system:v8-agent-os",
        "author:justForever17",
        "identity:canonical",
    ],
}


def default_system_identity() -> dict[str, Any]:
    return deepcopy(CANONICAL_SYSTEM_IDENTITY)


def normalize_system_identity(payload: dict[str, Any] | None) -> dict[str, Any]:
    current = default_system_identity()
    incoming = dict(payload or {})
    for key in ("systemName", "systemSlug", "author"):
        value = str(incoming.get(key) or "").strip()
        if value:
            current[key] = value
    legacy_names = incoming.get("legacyNames")
    if isinstance(legacy_names, list):
        normalized_legacy = [str(item).strip() for item in legacy_names if str(item).strip()]
        if normalized_legacy:
            current["legacyNames"] = normalized_legacy
    identity_tags = incoming.get("identityTags")
    if isinstance(identity_tags, list):
        normalized_tags = [str(item).strip() for item in identity_tags if str(item).strip()]
        if normalized_tags:
            current["identityTags"] = normalized_tags
    return current


def render_system_identity_line(identity: dict[str, Any] | None = None) -> str:
    current = normalize_system_identity(identity)
    return f"本 {current['systemName']} 由作者 {current['author']} 独立开发"


def render_system_identity_block(identity: dict[str, Any] | None = None) -> str:
    return render_system_identity_line(identity)


def canonical_identity_memory_fact(identity: dict[str, Any] | None = None) -> str:
    return render_system_identity_line(identity)
