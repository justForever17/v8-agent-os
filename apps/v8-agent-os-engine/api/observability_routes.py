from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from core.observability_db import observability_db


router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/tool-observations")
def list_tool_observations(
    runId: str | None = Query(default=None),
    sessionId: str | None = Query(default=None),
    toolName: str | None = Query(default=None),
    runtimeKind: str | None = Query(default=None),
    surface: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    return observability_db.list_tool_observation_records(
        run_id=runId,
        session_id=sessionId,
        tool_name=toolName,
        runtime_kind=runtimeKind,
        surface=surface,
        cursor=cursor,
        limit=limit,
        preview_chars=700,
    )


@router.get("/tool-observations/{observation_id}")
def get_tool_observation(observation_id: str):
    record = observability_db.reveal_tool_observation_record(observation_id, max_chars=1800)
    if not record:
        raise HTTPException(status_code=404, detail="tool observation not found")
    return record


@router.post("/tool-observations/{observation_id}/reveal")
def reveal_tool_observation(observation_id: str, payload: dict[str, Any] | None = Body(default=None)):
    max_chars = int((payload or {}).get("maxChars") or 12000)
    record = observability_db.reveal_tool_observation_record(observation_id, max_chars=max_chars)
    if not record:
        raise HTTPException(status_code=404, detail="tool observation not found")
    observability_db.add_audit_log(
        "OBSERVABILITY",
        "tool_observation_reveal",
        "SUCCESS",
        json.dumps(
            {
                "id": record.get("id"),
                "rawRef": record.get("rawRef"),
                "toolName": record.get("toolName"),
                "toolCallId": record.get("toolCallId"),
                "runtimeKind": record.get("runtimeKind"),
                "previewChars": record.get("previewChars"),
                "redacted": record.get("redacted"),
            },
            ensure_ascii=False,
        ),
    )
    return record


@router.get("/compactions")
def list_compactions(
    runId: str | None = Query(default=None),
    sessionId: str | None = Query(default=None),
    targetRole: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    return observability_db.list_conversation_compaction_records(
        run_id=runId,
        session_id=sessionId,
        target_role=targetRole,
        cursor=cursor,
        limit=limit,
    )
