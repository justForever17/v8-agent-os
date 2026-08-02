from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Sequence

from langchain_core.messages import BaseMessage

from core.database import db
from erc.runtime_context import get_runtime_context


def snapshot_type_for_target_role(target_role: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9:_-]+", "_", str(target_role or "supervisor").strip())
    normalized = normalized.replace(":", "__")
    return f"context_compaction_baseline:{normalized or 'supervisor'}"


def digest_messages(messages: Sequence[BaseMessage]) -> str:
    payload = []
    for message in messages:
        payload.append(
            {
                "id": getattr(message, "id", None),
                "type": getattr(message, "type", ""),
                "content": getattr(message, "content", ""),
                "name": getattr(message, "name", None),
                "tool_calls": getattr(message, "tool_calls", None),
                "tool_call_id": getattr(message, "tool_call_id", None),
                "private_state_digest": hashlib.sha256(
                    json.dumps(
                        {
                            "additional_kwargs": getattr(message, "additional_kwargs", None),
                            "response_metadata": getattr(message, "response_metadata", None),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def load_compaction_baseline(*, session_id: str, target_role: str) -> Dict[str, Any] | None:
    row = db.get_latest_runtime_snapshot(session_id, snapshot_type=snapshot_type_for_target_role(target_role))
    if not row:
        return None
    snapshot = row.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def baseline_matches_messages(baseline: Dict[str, Any] | None, messages: Sequence[BaseMessage]) -> bool:
    if not isinstance(baseline, dict):
        return False
    try:
        covered_count = int(baseline.get("coveredMessageCount") or 0)
    except (TypeError, ValueError):
        return False
    if covered_count <= 0 or covered_count > len(messages):
        return False
    expected_hash = str(baseline.get("coveredMessagesHash") or "").strip()
    return bool(expected_hash) and expected_hash == digest_messages(messages[:covered_count])


def persist_compaction_baseline(
    *,
    session_id: str,
    target_role: str,
    covered_messages: Sequence[BaseMessage],
    baseline_text: str,
    estimated_tokens: int,
    summary_method: str,
    chunked: bool,
    context_window_tokens: int,
    trigger_ratio: float,
    resolved_model_id: str | None,
) -> Dict[str, Any]:
    runtime_ctx = get_runtime_context()
    run_id = str(runtime_ctx.get("run_id") or "").strip() or None
    covered_hash = digest_messages(covered_messages)
    snapshot_id = f"ctxb_{hashlib.md5(f'{session_id}:{target_role}:{covered_hash}'.encode('utf-8')).hexdigest()}"
    snapshot = {
        "targetRole": target_role,
        "coveredMessageCount": len(covered_messages),
        "coveredMessagesHash": covered_hash,
        "baselineText": str(baseline_text or "").strip(),
        "estimatedTokens": int(estimated_tokens or 0),
        "summaryMethod": str(summary_method or "rule_summary"),
        "chunked": bool(chunked),
        "contextWindowTokens": int(context_window_tokens or 0),
        "triggerRatio": float(trigger_ratio or 0.0),
        "resolvedModelId": str(resolved_model_id or "").strip(),
        "updatedAt": runtime_ctx.get("event_ts") or None,
        "snapshotId": snapshot_id,
    }
    db.add_runtime_snapshot(
        snapshot_id=snapshot_id,
        session_id=session_id,
        run_id=run_id,
        latest_seq=int(runtime_ctx.get("latest_seq") or 0),
        snapshot_type=snapshot_type_for_target_role(target_role),
        snapshot=snapshot,
    )
    return snapshot
