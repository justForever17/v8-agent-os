from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


CONTEXT_AUDIT_FIELDS = (
    "context_policy_version",
    "runtime_kind",
    "target_role",
    "resolved_model_id",
    "context_window_tokens",
    "original_message_count",
    "estimated_input_tokens",
    "trigger_reason",
    "compaction_applied",
    "compaction_method",
    "block_types",
    "block_count",
    "estimated_saved_tokens",
    "block_summaries",
    "resolved_scope",
    "scope_chain",
    "durable_flush",
)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _normalize_string(value: Any) -> str:
    return str(value or "").strip() if value is not None else ""


def _normalize_block_summaries(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {
            "type": _normalize_string(item.get("type")),
            "title": _normalize_string(item.get("title")),
            "runtime_plane": _normalize_string(item.get("runtime_plane")),
            "content_preview": _normalize_string(item.get("content_preview")),
        }
        try:
            entry["estimated_tokens"] = int(item.get("estimated_tokens") or 0)
        except (TypeError, ValueError):
            entry["estimated_tokens"] = 0
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            entry["metadata"] = {
                str(key).strip(): metadata[key]
                for key in metadata
                if str(key).strip()
            }
        normalized.append(entry)
    return normalized


def _synthesize_block_summaries(block_types: list[str]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for block_type in block_types:
        normalized_type = _normalize_string(block_type)
        if not normalized_type:
            continue
        summaries.append(
            {
                "type": normalized_type,
                "title": normalized_type.replace("_", " ").strip(),
                "runtime_plane": "",
                "content_preview": "",
                "estimated_tokens": 0,
            }
        )
    return summaries


def _normalize_durable_flush(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    return {
        "ok": bool(payload.get("ok", False)),
        "skipped": bool(payload.get("skipped", False)),
        "reason": _normalize_string(payload.get("reason")),
    }


def normalize_context_audit(audit: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(audit or {})
    normalized: Dict[str, Any] = {}
    for key in CONTEXT_AUDIT_FIELDS:
        value = payload.get(key)
        if key in {"block_types", "scope_chain"}:
            normalized[key] = [str(item).strip() for item in (value or []) if str(item).strip()]
        elif key == "block_summaries":
            normalized[key] = _normalize_block_summaries(value)
        elif key == "durable_flush":
            normalized[key] = _normalize_durable_flush(value)
        elif key in {"context_policy_version", "context_window_tokens", "original_message_count", "estimated_input_tokens", "block_count", "estimated_saved_tokens"}:
            normalized[key] = _coerce_int(value, 0)
        elif key == "compaction_applied":
            normalized[key] = bool(value)
        else:
            normalized[key] = _normalize_string(value)

    if normalized["block_summaries"] and not normalized["block_types"]:
        normalized["block_types"] = [
            str(item.get("type") or "").strip()
            for item in normalized["block_summaries"]
            if isinstance(item, dict) and str(item.get("type") or "").strip()
        ]
    if normalized["block_types"] and not normalized["block_summaries"]:
        normalized["block_summaries"] = _synthesize_block_summaries(normalized["block_types"])
    if not normalized["block_count"]:
        normalized["block_count"] = max(
            len(normalized["block_summaries"]),
            len(normalized["block_types"]),
        )
    if normalized["resolved_scope"] == "" and normalized["scope_chain"]:
        normalized["resolved_scope"] = str(normalized["scope_chain"][-1] or "").strip()
    if not normalized["scope_chain"] and normalized["resolved_scope"]:
        normalized["scope_chain"] = [normalized["resolved_scope"]]
    if not normalized["durable_flush"].get("reason"):
        if normalized["compaction_applied"]:
            normalized["durable_flush"] = {
                **normalized["durable_flush"],
                "reason": "compaction_applied",
            }
        else:
            normalized["durable_flush"] = {
                "ok": True,
                "skipped": True,
                "reason": "compaction_not_needed",
            }
    return normalized


def _coerce_context_governance_event(event: Dict[str, Any] | None) -> Optional[Dict[str, Any]]:
    payload = normalize_context_audit((event or {}).get("payload") or {})
    if not payload:
        return None
    return {
        **payload,
        "eventId": _normalize_string((event or {}).get("event_id")),
        "eventTs": _normalize_string((event or {}).get("event_ts") or (event or {}).get("ts")),
        "seq": _coerce_int((event or {}).get("seq"), 0),
        "runId": _normalize_string((event or {}).get("run_id")),
        "eventSource": dict((event or {}).get("source") or {}),
    }


def emit_context_prepared_event(
    audit: Dict[str, Any] | None,
    *,
    component: str,
    node: str,
    agent_id: str | None = None,
) -> Optional[Dict[str, Any]]:
    from erc.event_bus import event_bus
    from erc.models import RuntimeSource
    from erc.runtime_context import get_runtime_context

    runtime_ctx = get_runtime_context()
    session_id = str(runtime_ctx.get("session_id") or "").strip()
    if not session_id:
        return None
    emitter = event_bus.create_emitter(
        session_id=session_id,
        conversation_id=str(runtime_ctx.get("conversation_id") or session_id).strip() or session_id,
        run_id=str(runtime_ctx.get("run_id") or "").strip() or None,
        source=RuntimeSource(
            plane="engine",
            component=component,
            node=node,
            agent_id=agent_id,
        ),
    )
    return emitter.emit("context.prepared", normalize_context_audit(audit))


def extract_latest_context_governance(runtime_events: Iterable[Dict[str, Any]] | None) -> Optional[Dict[str, Any]]:
    events = list(runtime_events or [])
    for event in reversed(events):
        if str(event.get("topic") or "").strip() != "context.prepared":
            continue
        payload = _coerce_context_governance_event(event)
        if payload:
            return payload
    return None


def extract_context_governance_history(
    runtime_events: Iterable[Dict[str, Any]] | None,
    *,
    limit: int | None = None,
) -> list[Dict[str, Any]]:
    normalized: list[Dict[str, Any]] = []
    for event in runtime_events or []:
        if str(event.get("topic") or "").strip() != "context.prepared":
            continue
        payload = _coerce_context_governance_event(event)
        if payload:
            normalized.append(payload)
    if limit is not None and limit > 0:
        return normalized[-limit:]
    return normalized
