from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from core.response_normalizer import V8_CANONICAL_TOOL_CALL_PREFIX, is_v8_canonical_tool_call_id


def _clean_fragment(value: Any, *, fallback: str = "tool") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_:-]+", "_", text).strip("_")
    return (text or fallback)[:28]


def make_tool_invocation_id(
    value: Any = "",
    *,
    tool_name: str = "",
    run_id: str = "",
    callback_run_id: str = "",
) -> str:
    """Return a stable V8 tool invocation id for event/card correlation.

    Model-facing ToolMessage IDs are not rewritten here. This helper only
    gives realtime/UI/tool-card surfaces a canonical id when a provider,
    callback, internal command, or virtual tool path did not already provide
    one.
    """

    raw = str(value or "").strip()
    if raw and is_v8_canonical_tool_call_id(raw):
        return raw
    basis = raw or str(callback_run_id or "").strip()
    if not basis:
        basis = uuid.uuid4().hex
    digest = hashlib.sha256(f"{run_id}|{tool_name}|{basis}".encode("utf-8", errors="ignore")).hexdigest()[:20]
    prefix = _clean_fragment(tool_name)
    return f"{V8_CANONICAL_TOOL_CALL_PREFIX}{prefix}_{digest}"

