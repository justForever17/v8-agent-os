from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _stable_fingerprint(payload: Dict[str, Any]) -> str:
    return hashlib.md5(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_normalized_signal_payload(
    *,
    source_kind: str,
    signal_kind: str,
    owner_runtime: str,
    summary: str,
    related_session_id: str | None = None,
    related_run_id: str | None = None,
    task_relevant: bool = False,
    blocking: bool = False,
    metadata: Dict[str, Any] | None = None,
    fingerprint: str | None = None,
) -> Dict[str, Any]:
    normalized_metadata = _jsonable(dict(metadata or {}))
    normalized_summary = str(summary or "").strip()
    payload = {
        "source_kind": str(source_kind or "").strip() or "runtime",
        "signal_kind": str(signal_kind or "").strip() or "signal",
        "owner_runtime": str(owner_runtime or "").strip() or "unknown",
        "summary": normalized_summary[:240],
        "related_session_id": str(related_session_id or "").strip() or None,
        "related_run_id": str(related_run_id or "").strip() or None,
        "task_relevant": bool(task_relevant),
        "blocking": bool(blocking),
        "metadata": normalized_metadata,
    }
    payload["fingerprint"] = str(fingerprint or "").strip() or _stable_fingerprint(
        {
            "source_kind": payload["source_kind"],
            "signal_kind": payload["signal_kind"],
            "owner_runtime": payload["owner_runtime"],
            "summary": payload["summary"],
            "related_session_id": payload["related_session_id"],
            "metadata": normalized_metadata,
        }
    )
    return payload
