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
)


def normalize_context_audit(audit: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(audit or {})
    normalized: Dict[str, Any] = {}
    for key in CONTEXT_AUDIT_FIELDS:
        value = payload.get(key)
        if key in {"block_types"}:
            normalized[key] = [str(item).strip() for item in (value or []) if str(item).strip()]
        elif key in {"context_policy_version", "context_window_tokens", "original_message_count", "estimated_input_tokens", "block_count", "estimated_saved_tokens"}:
            try:
                normalized[key] = int(value or 0)
            except (TypeError, ValueError):
                normalized[key] = 0
        elif key == "compaction_applied":
            normalized[key] = bool(value)
        else:
            normalized[key] = str(value or "").strip() if value is not None else ""
    return normalized


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
        payload = normalize_context_audit(event.get("payload") or {})
        payload["eventTs"] = str(event.get("event_ts") or event.get("ts") or "").strip()
        payload["runId"] = str(event.get("run_id") or "").strip()
        payload["eventSource"] = dict(event.get("source") or {})
        return payload
    return None
