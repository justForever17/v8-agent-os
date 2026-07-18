from __future__ import annotations

import hashlib
from typing import Any, Mapping


MEMORY_EXTRACTION_MODE_AUTO = "auto"
MEMORY_EXTRACTION_MODE_MANUAL = "manual"
MEMORY_EXTRACTION_MODES = {
    MEMORY_EXTRACTION_MODE_AUTO,
    MEMORY_EXTRACTION_MODE_MANUAL,
}


def memory_extraction_runtime_session_id(source_session_id: str) -> str:
    digest = hashlib.md5(str(source_session_id).encode("utf-8")).hexdigest()[:10]
    return f"hook:on_chat_end:memory:{digest}"


def resolve_memory_extraction_mode(config: Mapping[str, Any] | None) -> str:
    payload = config or {}
    explicit_mode = str(payload.get("extraction_mode") or "").strip().lower()
    if explicit_mode in MEMORY_EXTRACTION_MODES:
        return explicit_mode
    return (
        MEMORY_EXTRACTION_MODE_AUTO
        if bool(payload.get("extraction_enabled", True))
        else MEMORY_EXTRACTION_MODE_MANUAL
    )


def normalize_memory_extraction_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = dict(config or {})
    mode = resolve_memory_extraction_mode(normalized)
    normalized["extraction_mode"] = mode
    # Keep the legacy switch coherent during the compatibility window. Old
    # callers still read this field, while manual execution bypasses it
    # explicitly through the runner contract.
    normalized["extraction_enabled"] = mode == MEMORY_EXTRACTION_MODE_AUTO
    return normalized
