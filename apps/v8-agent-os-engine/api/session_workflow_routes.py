import uuid
import importlib
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from .models import RuntimeCapabilityPolicyPayload, RuntimeStabilityConfigPayload, ScopeResolvePayload, SessionScopeBindingPayload
from core.context_governance import (
    extract_context_governance_history,
    extract_latest_context_governance,
)
from core.database import db
from core.artifact_store import artifact_store
from core.multimodal_payload_adapter import normalize_artifact_record
from core.scoped_workspace_resource import resolve_scoped_workspace_resource
from core.runtime_projection import (
    build_projection_controls,
    build_projection_summary,
    build_recoverable_view,
    project_ask_user_interactions,
    project_runtime_timeline_from_events,
    project_pending_approvals,
)
from erc.capability_registry import capability_registry
from erc.chat_canonical_transcript import build_canonical_chat_messages
from erc.command_router import runtime_command_router
from erc.liveness_projection import build_liveness_view
from erc.recovery_policy import derive_recovery_class
from erc.session_realtime_contract import (
    augment_workflow_projection,
    build_processes_snapshot,
    resolve_authoritative_session_runtime_state,
)
from erc.session_history_contract import (
    build_session_history_detail,
    build_session_history_materialized_record,
)
from erc.session_admission_service import session_admission_service
from erc.snapshot_service import snapshot_service
from erc.runtime_stability import runtime_stability_service
from erc.workflow_ledger import workflow_ledger_service
from erc.workflow_projection import workflow_projection_service
from runtimes.memory.scope_resolution import (
    scope_resolution_service,
    session_scope_binding_service,
)


router = APIRouter()


def _now_perf_ms() -> float:
    return time.perf_counter() * 1000


def _elapsed_ms(started_at_ms: float) -> int:
    return max(0, int(round(_now_perf_ms() - started_at_ms)))


def _payload_size_bytes(payload) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def _attach_profile(payload: dict, *, route: str, started_at_ms: float, extra: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        return payload
    profile = {
        "route": route,
        "elapsedMs": _elapsed_ms(started_at_ms),
        **(extra or {}),
    }
    profile["payloadBytes"] = _payload_size_bytes(payload)
    payload["_profile"] = profile
    return payload


def _scope_resolution_payload(result) -> dict:
    evidence = dict(getattr(result, "evidence", {}) or {})
    return {
        "binding": result.binding.model_dump(exclude_none=True),
        "scopeChain": list(getattr(result, "scope_chain", []) or []),
        "evidence": evidence,
        "requestedScope": getattr(result, "requested_scope", None),
        "reusedExistingBinding": bool(getattr(result, "reused_existing_binding", False)),
        "rebindReason": str(evidence.get("rebind_reason") or "").strip() or None,
        "previousScope": str(evidence.get("previous_scope") or "").strip() or None,
        "nextScope": str(evidence.get("next_scope") or "").strip() or None,
    }


def _scope_history_event_payload(event) -> dict:
    payload = event.model_dump(exclude_none=True)
    evidence = dict(payload.get("evidence") or {})
    payload["rebindReason"] = str(evidence.get("rebind_reason") or "").strip() or None
    payload["previousScope"] = str(evidence.get("previous_scope") or "").strip() or None
    payload["nextScope"] = str(evidence.get("next_scope") or "").strip() or None
    payload["scopeAnchorComparison"] = evidence.get("scope_anchor_comparison") if isinstance(evidence.get("scope_anchor_comparison"), dict) else None
    return payload


def _memory_runtime():
    return importlib.import_module("runtimes.memory.runtime").memory_runtime

def _build_durable_detail_payload(
    *,
    session_id: str,
    session_row: dict,
    messages: list[dict],
    context_governance: dict | None,
    context_governance_history: list[dict] | None = None,
    session_source: str | None = None,
    runtime_timeline: list[dict] | None = None,
    runtime_events: list[dict] | None = None,
) -> dict:
    workflow_view = workflow_ledger_service.get_session_workflow_view(session_id)
    approvals = project_pending_approvals(db.list_pending_approvals(session_id=session_id, status="pending"))
    ask_user_interactions = project_ask_user_interactions(
        db.list_ask_user_interactions(session_id=session_id, status="pending")
    )
    controls = build_projection_controls(workflow_view, approvals)
    runtime_events = list(runtime_events or [])
    lane = session_admission_service.get_lane_view(session_id)
    session_runtime = resolve_authoritative_session_runtime_state(
        session_id=session_id,
        workflow_view=workflow_view,
        lane_view=lane,
        runtime_events=runtime_events,
    )
    latest_seq = max((int(event.get("seq") or 0) for event in runtime_events), default=0)
    run_record = session_runtime.run_record
    recovery_class = derive_recovery_class(run_record, workflow_view=workflow_view)
    liveness = build_liveness_view(
        run_record=run_record,
        workflow_view=workflow_view,
        runtime_events=runtime_events,
        lane_view=lane,
    )
    workflow_projection = augment_workflow_projection(
        workflow_projection_service.build(session_id=session_id),
        todos=session_runtime.todos,
        current_run=session_runtime.current_run,
        runtime_status=session_runtime.runtime_status,
        latest_seq=latest_seq,
    )
    return {
        "messages": messages,
        "latestSeq": latest_seq,
        "runtimeTimeline": list(runtime_timeline or []),
        "workflow": workflow_view,
        "workflowProjection": workflow_projection,
        "approvals": approvals,
        "askUserInteractions": ask_user_interactions,
        "controls": controls,
        "recoverable": build_recoverable_view(workflow_view, controls),
        "todos": session_runtime.todos,
        "currentRun": session_runtime.current_run,
        "runtimeStatus": session_runtime.runtime_status,
        "summary": build_projection_summary(
            session=session_row,
            snapshot={"messages": messages},
            workflow=workflow_view,
            approvals=approvals,
            latest_seq=latest_seq,
            source=session_source or "durable_detail_projection",
        ),
        "source": "durable_detail_projection",
        "contextGovernance": context_governance,
        "contextGovernanceHistory": list(context_governance_history or []),
        "lane": lane,
        "liveness": liveness,
        "recoveryClass": recovery_class,
    }


def _derive_session_source(session_row: dict, run_record: dict | None = None) -> str:
    metadata = session_row.get("metadata") if isinstance(session_row.get("metadata"), dict) else {}
    run_metadata = run_record.get("metadata") if isinstance((run_record or {}).get("metadata"), dict) else {}

    for candidate in (
        session_row.get("source"),
        metadata.get("source"),
        metadata.get("trigger_source"),
        metadata.get("triggerSource"),
        run_record.get("trigger_source") if run_record else None,
        run_metadata.get("source"),
        run_metadata.get("trigger_source"),
        run_metadata.get("triggerSource"),
    ):
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return "web"


@router.get("/sessions")
async def get_sessions():
    """Retrieve all sessions handled by the Python DB Engine."""
    try:
        sessions = []
        for row in db.get_sessions():
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if (
                bool(metadata.get("hideFromChatHistory"))
                or str(metadata.get("transport") or "").strip() == "network_supervisor_openai"
                or str(row.get("id") or "").startswith("network_openai_")
            ):
                continue
            workflow_view = {
                "workflowId": row.get("workflowId"),
                "rootRunId": row.get("rootRunId"),
                "status": row.get("workflowStatus"),
                "recoverable": bool(row.get("recoverable")),
                "ownerRuntime": row.get("ownerRuntime"),
                "ownerAgentId": row.get("ownerAgentId"),
                "currentStepId": row.get("currentStepId"),
                "currentStepKey": row.get("currentStepKey"),
                "currentStepTitle": row.get("currentStepTitle"),
                "currentStepStatus": row.get("stepStatus"),
                "updatedAt": row.get("workflowUpdatedAt") or row.get("updated_at"),
            }
            run_record = db.get_run_record(workflow_view.get("rootRunId")) if workflow_view.get("rootRunId") else None
            session_source = _derive_session_source(row, run_record)
            approvals = project_pending_approvals(
                db.list_pending_approvals(session_id=row["id"], status="pending")
            )
            controls = build_projection_controls(workflow_view, approvals)
            sessions.append(
                build_session_history_materialized_record(
                    session_row={**row, "controls": controls},
                    workflow_view=workflow_view,
                    approvals=approvals,
                    snapshot=None,
                    latest_seq=0,
                    source=session_source,
                    run_record=run_record,
                )
            )
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions")
async def create_session(data: dict = Body(...)):
    """Create a new session placeholder."""
    try:
        session_id = str(uuid.uuid4())
        db.create_or_update_session(
            session_id=session_id,
            title=data.get("title", "New Chat"),
            user_id=data.get("userId", "anonymous"),
        )
        if data.get("projectId") or data.get("workspaceId") or data.get("workspacePath") or data.get("scopeHint"):
            scope_resolution_service.resolve(
                session_id=session_id,
                conversation_id=session_id,
                user_id=data.get("userId", "anonymous"),
                user_query="",
                project_id=data.get("projectId"),
                workspace_id=data.get("workspaceId"),
                workspace_path=data.get("workspacePath"),
                thread_id=data.get("threadId"),
                scope_hint=data.get("scopeHint"),
                scope_mode=data.get("scopeMode", "explicit"),
            )
        session = db.get_session(session_id)
        workflow_view = workflow_ledger_service.get_session_workflow_view(session_id)
        approvals: list[dict] = []
        controls = build_projection_controls(workflow_view, approvals)
        session_source = _derive_session_source(session or {"id": session_id, "metadata": {}}, None)
        return build_session_history_materialized_record(
            session_row={**(session or {}), "controls": controls},
            workflow_view=workflow_view,
            approvals=approvals,
            snapshot=None,
            latest_seq=0,
            source=session_source,
            run_record=None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        db.delete_session(session_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    try:
        snapshot_payload = runtime_command_router.get_snapshot(session_id)
        snapshot = snapshot_payload.get("snapshot") or {}
        if snapshot.get("messages"):
            return {
                "messages": snapshot["messages"],
                "artifacts": snapshot.get("artifacts") or [],
                "source": snapshot_payload.get("source") or "runtime_snapshot",
                "latestSeq": snapshot_payload.get("latestSeq", 0),
                "runtimeTimeline": snapshot_payload.get("runtimeTimeline") or [],
                "workflow": snapshot_payload.get("workflow"),
                "workflowProjection": snapshot_payload.get("workflowProjection"),
                "approvals": snapshot_payload.get("approvals") or [],
                "askUserInteractions": snapshot_payload.get("askUserInteractions") or [],
                "controls": snapshot_payload.get("controls") or {},
                "recoverable": snapshot_payload.get("recoverable") or {},
                "todos": snapshot_payload.get("todos") or {"items": [], "allCompleted": False},
                "currentRun": snapshot_payload.get("currentRun"),
                "runtimeStatus": snapshot_payload.get("runtimeStatus"),
                "summary": snapshot_payload.get("summary") or {},
                "contextGovernance": snapshot_payload.get("contextGovernance"),
                "contextGovernanceHistory": snapshot_payload.get("contextGovernanceHistory") or [],
                "projection": snapshot_payload,
            }
        if snapshot_payload.get("legacyChatUnsupported") or snapshot.get("legacyChatUnsupported"):
            return {
                "messages": [],
                "artifacts": [],
                "source": snapshot_payload.get("source") or "legacy_chat_unsupported",
                "latestSeq": snapshot_payload.get("latestSeq", 0),
                "runtimeTimeline": snapshot_payload.get("runtimeTimeline") or [],
                "workflow": snapshot_payload.get("workflow"),
                "workflowProjection": snapshot_payload.get("workflowProjection"),
                "approvals": snapshot_payload.get("approvals") or [],
                "askUserInteractions": snapshot_payload.get("askUserInteractions") or [],
                "controls": snapshot_payload.get("controls") or {},
                "recoverable": snapshot_payload.get("recoverable") or {},
                "todos": snapshot_payload.get("todos") or {"items": [], "allCompleted": False},
                "currentRun": snapshot_payload.get("currentRun"),
                "runtimeStatus": snapshot_payload.get("runtimeStatus"),
                "summary": snapshot_payload.get("summary") or {},
                "contextGovernance": snapshot_payload.get("contextGovernance"),
                "contextGovernanceHistory": snapshot_payload.get("contextGovernanceHistory") or [],
                "projection": snapshot_payload,
                "legacyChatUnsupported": True,
            }

        session_row = db.get_session(session_id) or {"id": session_id, "title": "New Chat", "metadata": {}}
        durable_messages = build_canonical_chat_messages(session_id)
        runtime_events = db.get_runtime_events(session_id)
        context_governance = extract_latest_context_governance(runtime_events)
        context_governance_history = extract_context_governance_history(runtime_events)
        return _build_durable_detail_payload(
            session_id=session_id,
            session_row=session_row,
            messages=durable_messages,
            context_governance=context_governance,
            context_governance_history=context_governance_history,
            session_source=_derive_session_source(
                session_row,
                db.get_run_record((session_row.get("rootRunId") or "")) if session_row.get("rootRunId") else None,
            ),
            runtime_timeline=project_runtime_timeline_from_events(runtime_events),
            runtime_events=runtime_events,
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/messages/{message_id}")
async def delete_message(message_id: str):
    try:
        message = db.get_message(message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")

        session_id = str(message.get("session_id") or "")
        if not db.delete_message(message_id):
            raise HTTPException(status_code=404, detail="Message not found")

        if session_id:
            snapshot_service.refresh_chat_projection(session_id)
        return {
            "status": "success",
            "sessionId": session_id,
            "messageId": message_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/resource")
async def get_workspace_resource(
    workspace_relative_path: str = Query(..., alias="workspace_relative_path"),
    path_plane: str = Query(..., alias="path_plane"),
    workspace_id: Optional[str] = Query(None, alias="workspace_id"),
    project_id: Optional[str] = Query(None, alias="project_id"),
):
    try:
        resolved = resolve_scoped_workspace_resource(
            workspace_relative_path=workspace_relative_path,
            path_plane=path_plane,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response = FileResponse(str(resolved.absolute_path))
    response.headers["X-V8-Workspace-Relative-Path"] = resolved.workspace_relative_path
    response.headers["X-V8-Path-Plane"] = resolved.path_plane
    if resolved.workspace_id:
        response.headers["X-V8-Workspace-Id"] = resolved.workspace_id
    if resolved.project_id:
        response.headers["X-V8-Project-Id"] = resolved.project_id
    return response


@router.get("/sessions/{session_id}/scope")
async def get_session_scope(session_id: str):
    try:
        binding = session_scope_binding_service.get_binding(session_id)
        return {
            "sessionId": session_id,
            "binding": binding.model_dump(exclude_none=True) if binding else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sessions/{session_id}/scope")
async def put_session_scope(session_id: str, payload: SessionScopeBindingPayload):
    try:
        binding = session_scope_binding_service.upsert_binding(
            scope_resolution_service.resolve(
                session_id=session_id,
                conversation_id=payload.conversation_id or session_id,
                thread_id=payload.thread_id,
                user_id=payload.user_id,
                project_id=payload.project_id,
                workspace_id=payload.workspace_id,
                workspace_path=payload.workspace_path,
                workflow_id=payload.workflow_id,
                channel_type=payload.channel_type,
                channel_remote_id=payload.channel_remote_id,
                scope_hint=payload.scope_hint,
                scope_mode="explicit",
            ).binding.model_copy(
                update={
                    "scope_source": payload.scope_source or "admin_selected",
                    "scope_confidence": payload.scope_confidence or 1.0,
                }
            )
        )
        return {"sessionId": session_id, "binding": binding.model_dump(exclude_none=True)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/scope/re-resolve")
async def reresolve_session_scope(session_id: str, payload: Optional[ScopeResolvePayload] = Body(default=None)):
    try:
        result = scope_resolution_service.reresolve_session(
            session_id=session_id,
            user_query=payload.user_query if payload else "",
            project_id=payload.project_id if payload else None,
            workspace_id=payload.workspace_id if payload else None,
            workspace_path=payload.workspace_path if payload else None,
            workflow_id=payload.workflow_id if payload else None,
            channel_type=payload.channel_type if payload else None,
            channel_remote_id=payload.channel_remote_id if payload else None,
            thread_id=payload.thread_id if payload else None,
            scope_hint=payload.scope_hint if payload else None,
            scope_mode=payload.scope_mode if payload else "explicit",
        )
        return {"sessionId": session_id, **_scope_resolution_payload(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/scope/history")
async def get_session_scope_history(session_id: str):
    try:
        history = scope_resolution_service.get_scope_history(session_id)
        return {
            "sessionId": session_id,
            "events": [_scope_history_event_payload(item) for item in history],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scope/resolve")
async def resolve_scope(payload: ScopeResolvePayload):
    try:
        result = scope_resolution_service.resolve(
            session_id=payload.session_id,
            conversation_id=payload.session_id,
            user_query=payload.user_query or "",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            workflow_id=payload.workflow_id,
            channel_type=payload.channel_type,
            channel_remote_id=payload.channel_remote_id,
            thread_id=payload.thread_id,
            scope_hint=payload.scope_hint,
            scope_mode=payload.scope_mode or "explicit",
        )
        return _scope_resolution_payload(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/runtime-events")
async def get_session_runtime_events(session_id: str, after_seq: int | None = None):
    started_at_ms = _now_perf_ms()
    try:
        payload = runtime_command_router.get_events(session_id, after_seq=after_seq)
        events = payload.get("events") if isinstance(payload, dict) else []
        event_count = len(events) if isinstance(events, list) else 0
        latest_seq = max((int(event.get("seq") or 0) for event in events if isinstance(event, dict)), default=0)
        return _attach_profile(
            payload,
            route="engine.sessions.runtime_events",
            started_at_ms=started_at_ms,
            extra={
                "afterSeq": after_seq,
                "returnedEventCount": event_count,
                "latestReturnedSeq": latest_seq,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/artifacts")
async def get_session_runtime_artifacts(session_id: str, run_id: str | None = None, limit: int = 100):
    try:
        return {
            "artifacts": [
                normalize_artifact_record(item)
                for item in _memory_runtime().list_artifacts(session_id=session_id, run_id=run_id, limit=limit)
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts")
async def list_runtime_artifacts(session_id: str | None = None, run_id: str | None = None, limit: int = 100):
    try:
        return {
            "artifacts": [
                normalize_artifact_record(item)
                for item in _memory_runtime().list_artifacts(session_id=session_id, run_id=run_id, limit=limit)
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/artifacts/adopt-workspace-file")
async def adopt_workspace_artifact(body: dict = Body(...)):
    try:
        path = str(body.get("path") or body.get("workspacePath") or body.get("sourcePath") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        artifact = artifact_store.adopt_workspace_file(
            path=path,
            mode=str(body.get("mode") or "auto"),
            session_id=body.get("sessionId") or body.get("session_id"),
            run_id=body.get("runId") or body.get("run_id"),
            message_id=body.get("messageId") or body.get("message_id"),
            source_component="artifact_adoption_api",
            node="artifact_adoption_api",
        )
        return {"artifact": normalize_artifact_record(artifact)}
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts/{artifact_id}")
async def get_runtime_artifact(artifact_id: str):
    try:
        artifact = _memory_runtime().get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return normalize_artifact_record(artifact)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts/{artifact_id}/content")
async def get_runtime_artifact_content(artifact_id: str):
    try:
        artifact = _memory_runtime().get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        normalized = normalize_artifact_record(artifact)
        source_path = str(normalized.get("sourcePath") or "").strip()
        if not source_path:
            raise HTTPException(status_code=404, detail="Artifact has no local source path")
        path = Path(source_path).expanduser()
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact source file not found")
        filename = path.name
        media_type = str(normalized.get("mimeType") or "application/octet-stream")
        return FileResponse(path, media_type=media_type, filename=filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/snapshot")
async def get_session_snapshot(session_id: str):
    started_at_ms = _now_perf_ms()
    try:
        payload = runtime_command_router.get_snapshot(session_id)
        runtime_timeline = payload.get("runtimeTimeline") if isinstance(payload, dict) else []
        return _attach_profile(
            payload,
            route="engine.sessions.snapshot",
            started_at_ms=started_at_ms,
            extra={
                "latestSeq": int(payload.get("latestSeq") or 0) if isinstance(payload, dict) else 0,
                "runtimeTimelineCount": len(runtime_timeline) if isinstance(runtime_timeline, list) else 0,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    started_at_ms = _now_perf_ms()
    timings: dict[str, int] = {}
    try:
        step_started = _now_perf_ms()
        session_row = db.get_session(session_id)
        timings["dbSessionMs"] = _elapsed_ms(step_started)
        if session_row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        step_started = _now_perf_ms()
        runtime_events = db.get_runtime_events(session_id)
        timings["dbRuntimeEventsMs"] = _elapsed_ms(step_started)
        step_started = _now_perf_ms()
        workflow_view = workflow_ledger_service.get_session_workflow_view(session_id)
        timings["workflowViewMs"] = _elapsed_ms(step_started)
        step_started = _now_perf_ms()
        approvals = project_pending_approvals(
            db.list_pending_approvals(session_id=session_id, status="pending")
        )
        ask_user_interactions = project_ask_user_interactions(
            db.list_ask_user_interactions(session_id=session_id, status="pending")
        )
        timings["dbPendingItemsMs"] = _elapsed_ms(step_started)
        controls = build_projection_controls(workflow_view, approvals)
        step_started = _now_perf_ms()
        snapshot_payload = snapshot_service.build_chat_projection_payload(session_id)
        timings["snapshotBuildMs"] = _elapsed_ms(step_started)
        snapshot = snapshot_payload.get("snapshot")
        latest_seq = int(snapshot_payload.get("latestSeq") or 0)
        step_started = _now_perf_ms()
        timeline_messages = build_canonical_chat_messages(session_id)
        timings["canonicalMessagesMs"] = _elapsed_ms(step_started)
        if not timeline_messages and (snapshot_payload.get("legacyChatUnsupported") or (snapshot or {}).get("legacyChatUnsupported")):
            timeline_messages = []
        root_run_id = str(workflow_view.get("rootRunId") or "").strip()
        step_started = _now_perf_ms()
        run_record = db.get_run_record(root_run_id) if root_run_id else None
        timings["dbRunRecordMs"] = _elapsed_ms(step_started)
        session_source = _derive_session_source(session_row, run_record)
        step_started = _now_perf_ms()
        runtime_timeline = project_runtime_timeline_from_events(runtime_events)
        timings["runtimeTimelineProjectMs"] = _elapsed_ms(step_started)
        step_started = _now_perf_ms()
        detail = build_session_history_detail(
            session_row={**session_row, "controls": controls},
            workflow_view=workflow_view,
            approvals=approvals,
            snapshot=snapshot,
            timeline_messages=timeline_messages,
            latest_seq=latest_seq,
            source=session_source,
            runtime_events=runtime_events,
            runtime_timeline=runtime_timeline,
            run_record=run_record,
        )
        timings["historyDetailBuildMs"] = _elapsed_ms(step_started)
        detail["askUserInteractions"] = ask_user_interactions
        if snapshot_payload.get("legacyChatUnsupported") or (snapshot or {}).get("legacyChatUnsupported"):
            detail["legacyChatUnsupported"] = True
        return _attach_profile(
            detail,
            route="engine.sessions.history",
            started_at_ms=started_at_ms,
            extra={
                **timings,
                "runtimeEventCount": len(runtime_events),
                "runtimeTimelineCount": len(runtime_timeline),
                "messageCount": len(timeline_messages),
                "latestSeq": latest_seq,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/processes")
async def get_session_processes(session_id: str):
    started_at_ms = _now_perf_ms()
    timings: dict[str, int] = {}
    try:
        step_started = _now_perf_ms()
        session_row = db.get_session(session_id)
        timings["dbSessionMs"] = _elapsed_ms(step_started)
        if session_row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        step_started = _now_perf_ms()
        snapshot_payload = snapshot_service.build_chat_projection_payload(session_id)
        timings["snapshotBuildMs"] = _elapsed_ms(step_started)
        snapshot = snapshot_payload.get("snapshot") or {}
        current_run = snapshot_payload.get("currentRun") if isinstance(snapshot_payload.get("currentRun"), dict) else {}
        current_run_id = str(
            current_run.get("id")
            or snapshot_payload.get("currentRunId")
            or (snapshot_payload.get("workflow") or {}).get("rootRunId")
            or ""
        ).strip() or None
        step_started = _now_perf_ms()
        processes = build_processes_snapshot(
            session_id=session_id,
            snapshot=snapshot if isinstance(snapshot, dict) else {},
            run_id=current_run_id,
        )
        timings["processProjectionMs"] = _elapsed_ms(step_started)
        return _attach_profile({
            "sessionId": session_id,
            "currentRunId": current_run_id,
            "latestSeq": int(snapshot_payload.get("latestSeq") or 0),
            "processes": processes,
        }, route="engine.sessions.processes", started_at_ms=started_at_ms, extra={
            **timings,
            "processCount": len(processes) if isinstance(processes, list) else 0,
            "latestSeq": int(snapshot_payload.get("latestSeq") or 0),
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/workflow")
async def get_session_workflow_projection(session_id: str):
    try:
        projection = workflow_projection_service.build(session_id=session_id)
        if projection is None:
            raise HTTPException(status_code=404, detail="Workflow projection not found")
        return projection
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/workflow")
async def get_run_workflow_projection(run_id: str):
    try:
        projection = workflow_projection_service.build(run_id=run_id)
        if projection is None:
            raise HTTPException(status_code=404, detail="Workflow projection not found")
        return projection
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows/{workflow_id}")
async def get_workflow_projection(workflow_id: str):
    try:
        projection = workflow_projection_service.build(workflow_id=workflow_id)
        if projection is None:
            raise HTTPException(status_code=404, detail="Workflow projection not found")
        return projection
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runtime-capabilities")
async def get_runtime_capabilities(
    query: Optional[str] = Query(default=None),
    recommendation_limit: int = Query(default=5, alias="recommendationLimit"),
):
    try:
        return capability_registry.snapshot(
            query=query,
            recommendation_limit=max(1, min(recommendation_limit, 10)),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runtime-capabilities/{kind}/policy")
async def update_runtime_capability_policy(kind: str, payload: RuntimeCapabilityPolicyPayload):
    try:
        descriptor = capability_registry.get(kind)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Runtime '{kind}' is not registered")
        policy = capability_registry.set_policy(
            kind,
            payload.model_dump(by_alias=False, exclude_none=True),
        )
        return {
            "status": "success",
            "kind": kind,
            "policy": policy.as_dict(),
            "snapshot": capability_registry.snapshot(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/runtime-capabilities/{kind}/policy")
async def reset_runtime_capability_policy(kind: str):
    try:
        descriptor = capability_registry.get(kind)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Runtime '{kind}' is not registered")
        policy = capability_registry.reset_policy(kind)
        return {
            "status": "success",
            "kind": kind,
            "policy": policy.as_dict(),
            "snapshot": capability_registry.snapshot(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runtime-stability")
async def get_runtime_stability():
    try:
        return runtime_stability_service.build_payload()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runtime-stability")
async def save_runtime_stability(payload: RuntimeStabilityConfigPayload):
    try:
        config = runtime_stability_service.save_config(
            payload.model_dump(by_alias=True, exclude_none=True),
        )
        return {
            "status": "success",
            **runtime_stability_service.build_payload(),
            "saved": config.as_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
