from __future__ import annotations

from typing import Any

from core.database import db
from core.realtime_protocol import build_runtime_event


WORKBENCH_DOCUMENT_TOPICS = {
    "workbench.document.opened",
    "workbench.document.updated",
    "workbench.document.unavailable",
}


def emit_workbench_document_event(
    topic: str,
    *,
    session_id: str,
    document: dict[str, Any],
    run_id: str | None = None,
    runtime_id: str = "chat",
    source_component: str = "workbench",
    focus_requested: bool = False,
    user_initiated: bool = False,
) -> dict[str, Any]:
    normalized_topic = str(topic or "").strip()
    normalized_session_id = str(session_id or "").strip()
    if normalized_topic not in WORKBENCH_DOCUMENT_TOPICS:
        raise ValueError(f"Unsupported Workbench document topic: {normalized_topic}")
    if not normalized_session_id:
        raise ValueError("sessionId is required")
    if not str(document.get("documentId") or "").strip():
        raise ValueError("Workbench documentId is required")

    payload = {
        "type": "custom_event",
        "name": normalized_topic.replace("workbench.document.", "workbench_document_"),
        "runtimeId": str(runtime_id or "chat").strip() or "chat",
        "document": document,
        "focusRequested": bool(focus_requested),
        "userInitiated": bool(user_initiated),
    }
    event = build_runtime_event(
        kind="event",
        topic=normalized_topic,
        session_id=normalized_session_id,
        run_id=str(run_id or "").strip() or None,
        seq=db.get_next_runtime_seq(normalized_session_id),
        payload=payload,
        source={
            "plane": "engine",
            "component": str(source_component or "workbench"),
            "node": "workbench_document_registry",
            "agent_id": None,
        },
    )
    db.add_runtime_event(event)
    return event
