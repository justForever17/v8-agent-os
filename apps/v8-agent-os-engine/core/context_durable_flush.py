from __future__ import annotations

from typing import Any, Dict, Sequence

from langchain_core.messages import BaseMessage

from core.database import db
from erc.runtime_context import get_runtime_context
from erc.snapshot_service import snapshot_service
from erc.workflow_projection import workflow_projection_service


def _message_context_value(messages: Sequence[BaseMessage], key: str) -> str | None:
    for message in reversed(list(messages)):
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        value = str(additional_kwargs.get(key) or "").strip()
        if value:
            return value
    return None


def _resolve_context(messages: Sequence[BaseMessage]) -> Dict[str, str | None]:
    runtime_context = get_runtime_context()
    session_id = str(runtime_context.get("session_id") or "").strip() or _message_context_value(messages, "session_id")
    run_id = str(runtime_context.get("run_id") or "").strip() or _message_context_value(messages, "run_id")
    return {
        "session_id": session_id or None,
        "run_id": run_id or None,
        "runtime_kind": str(runtime_context.get("runtime_kind") or "").strip() or None,
    }


def flush_before_context_compaction(messages: Sequence[BaseMessage]) -> Dict[str, Any]:
    resolved = _resolve_context(messages)
    session_id = resolved.get("session_id")
    run_id = resolved.get("run_id")
    if not session_id:
        return {
            "ok": True,
            "skipped": True,
            "reason": "missing_session_id",
            "session_id": None,
            "run_id": run_id,
        }

    latest_seq_before = db.get_latest_runtime_seq(session_id)
    artifacts_before = len(db.list_runtime_artifacts(session_id=session_id, run_id=run_id, limit=200))
    snapshot = snapshot_service.refresh_chat_projection(session_id, run_id=run_id)
    workflow_projection = workflow_projection_service.build(session_id=session_id, run_id=run_id)
    return {
        "ok": True,
        "skipped": False,
        "reason": "refreshed",
        "session_id": session_id,
        "run_id": run_id,
        "latest_seq_before": latest_seq_before,
        "latest_seq_after": int(snapshot.get("latest_seq") or 0),
        "artifact_count": artifacts_before,
        "workflow_status": (workflow_projection or {}).get("workflow", {}).get("status"),
        "current_step_id": (workflow_projection or {}).get("workflow", {}).get("currentStepId"),
        "current_step_title": (workflow_projection or {}).get("workflow", {}).get("currentStepTitle"),
    }
