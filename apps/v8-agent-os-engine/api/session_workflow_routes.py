import uuid
import importlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from .models import RuntimeCapabilityPolicyPayload, RuntimeStabilityConfigPayload, ScopeResolvePayload, SessionScopeBindingPayload
from core.context_governance import extract_latest_context_governance
from core.database import db
from core.multimodal_payload_adapter import normalize_artifact_record
from core.runtime_projection import (
    build_projection_controls,
    build_projection_summary,
    build_recoverable_view,
    project_runtime_timeline_from_events,
    project_pending_approvals,
)
from erc.capability_registry import capability_registry
from erc.command_router import runtime_command_router
from erc.liveness_projection import build_liveness_view
from erc.recovery_policy import derive_recovery_class
from erc.session_admission_service import session_admission_service
from erc.snapshot_service import snapshot_service
from erc.runtime_stability import runtime_stability_service
from erc.workflow_ledger import workflow_ledger_service
from erc.workflow_projection import workflow_projection_service
from core.storage import storage
from runtimes.memory.scope_resolution import (
    scope_resolution_service,
    session_scope_binding_service,
)


router = APIRouter()


def _memory_runtime():
    return importlib.import_module("runtimes.memory.runtime").memory_runtime


def _format_db_session_messages(rows: list[dict]) -> list[dict]:
    formatted = []
    for row in rows:
        msg_obj = {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "reasoningContent": row.get("reasoning_content"),
            "createdAt": row["created_at"],
            "agentName": row.get("agent_name"),
            "agentAvatar": row.get("agent_avatar"),
            "agentRoleLabel": row.get("agent_role_label"),
            "agentId": row.get("agent_id"),
            "images": row.get("images") or [],
            "metadata": row.get("metadata") or {},
        }
        if row.get("tool_calls"):
            try:
                tool_calls = row["tool_calls"]
                if isinstance(tool_calls, str):
                    import json

                    tool_calls = json.loads(tool_calls)

                invocations = []
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict):
                        invocations.append(
                            {
                                "toolCallId": tool_call.get("id", ""),
                                "toolName": tool_call.get("name", tool_call.get("function", {}).get("name", "")),
                                "args": tool_call.get("args", tool_call.get("function", {}).get("arguments", {})),
                                "result": tool_call.get("result", None),
                            }
                        )
                if invocations:
                    msg_obj["toolInvocations"] = invocations
            except Exception as exc:
                print(f"Error parsing tool_calls: {exc}")
        formatted.append(msg_obj)
    return formatted


def _build_durable_detail_payload(
    *,
    session_id: str,
    session_row: dict,
    messages: list[dict],
    context_governance: dict | None,
    runtime_timeline: list[dict] | None = None,
    runtime_events: list[dict] | None = None,
) -> dict:
    workflow_view = workflow_ledger_service.get_session_workflow_view(session_id)
    approvals = project_pending_approvals(db.list_pending_approvals(session_id=session_id, status="pending"))
    controls = build_projection_controls(workflow_view, approvals)
    runtime_events = list(runtime_events or [])
    root_run_id = workflow_view.get("rootRunId") if isinstance(workflow_view, dict) else None
    latest_run_id = root_run_id or (runtime_events[-1].get("run_id") if runtime_events else None)
    run_record = db.get_run_record(latest_run_id) if latest_run_id else None
    lane = session_admission_service.get_lane_view(session_id)
    recovery_class = derive_recovery_class(run_record, workflow_view=workflow_view)
    liveness = build_liveness_view(
        run_record=run_record,
        workflow_view=workflow_view,
        runtime_events=runtime_events,
        lane_view=lane,
    )
    return {
        "messages": messages,
        "runtimeTimeline": list(runtime_timeline or []),
        "workflow": workflow_view,
        "workflowProjection": workflow_projection_service.build(session_id=session_id),
        "approvals": approvals,
        "controls": controls,
        "recoverable": build_recoverable_view(workflow_view, controls),
        "summary": build_projection_summary(
            session=session_row,
            snapshot={"messages": messages},
            workflow=workflow_view,
            approvals=approvals,
            latest_seq=0,
            source="durable_detail_projection",
        ),
        "source": "durable_detail_projection",
        "contextGovernance": context_governance,
        "lane": lane,
        "liveness": liveness,
        "recoveryClass": recovery_class,
    }


@router.get("/sessions")
async def get_sessions():
    """Retrieve all sessions handled by the Python DB Engine."""
    try:
        sessions = []
        for row in db.get_sessions():
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
                "updatedAt": row.get("workflowUpdatedAt") or row.get("lastActivityAt"),
            }
            approvals = project_pending_approvals(
                db.list_pending_approvals(session_id=row["id"], status="pending")
            )
            controls = build_projection_controls(workflow_view, approvals)
            summary = build_projection_summary(
                session=row,
                snapshot=None,
                workflow=workflow_view,
                approvals=approvals,
                latest_seq=0,
                source="session_list",
            )
            sessions.append(
                {
                    **row,
                    **summary,
                    "workflow": workflow_view,
                    "approvals": approvals,
                    "controls": controls,
                    "recoverableView": build_recoverable_view(workflow_view, controls),
                    "lane": session_admission_service.get_lane_view(row["id"]),
                    "recoveryClass": derive_recovery_class(
                        db.get_run_record(workflow_view.get("rootRunId")) if workflow_view.get("rootRunId") else None,
                        workflow_view=workflow_view,
                    ),
                    "source": "session_list",
                }
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
        if data.get("projectId") or data.get("workspaceId") or data.get("scopeHint"):
            scope_resolution_service.resolve(
                session_id=session_id,
                conversation_id=session_id,
                user_id=data.get("userId", "anonymous"),
                user_query="",
                project_id=data.get("projectId"),
                workspace_id=data.get("workspaceId"),
                workspace_path=data.get("workspacePath"),
                scope_hint=data.get("scopeHint"),
                scope_mode=data.get("scopeMode", "mixed"),
            )
        session = db.get_session(session_id)
        return session
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
                "controls": snapshot_payload.get("controls") or {},
                "recoverable": snapshot_payload.get("recoverable") or {},
                "summary": snapshot_payload.get("summary") or {},
                "contextGovernance": snapshot_payload.get("contextGovernance"),
            }

        session_row = db.get_session(session_id) or {"id": session_id, "title": "New Chat", "metadata": {}}
        durable_messages = _format_db_session_messages(db.get_messages(session_id))
        runtime_events = db.get_runtime_events(session_id)
        context_governance = extract_latest_context_governance(runtime_events)
        return _build_durable_detail_payload(
            session_id=session_id,
            session_row=session_row,
            messages=durable_messages,
            context_governance=context_governance,
            runtime_timeline=project_runtime_timeline_from_events(runtime_events),
            runtime_events=runtime_events,
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/todos")
async def get_session_todos(session_id: str, run_id: Optional[str] = Query(default=None)):
    try:
        snapshot = storage.get_active_todo_snapshot(session_id=session_id, run_id=run_id)
        return {
            "sessionId": session_id,
            "runId": run_id,
            "todo": snapshot,
            "source": "storage_snapshot" if snapshot else "empty",
        }
    except Exception as e:
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
            scope_hint=payload.scope_hint if payload else None,
            scope_mode=payload.scope_mode if payload else "mixed",
        )
        return {
            "sessionId": session_id,
            "binding": result.binding.model_dump(exclude_none=True),
            "scopeChain": result.scope_chain,
            "evidence": result.evidence,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/scope/history")
async def get_session_scope_history(session_id: str):
    try:
        history = scope_resolution_service.get_scope_history(session_id)
        return {
            "sessionId": session_id,
            "events": [item.model_dump(exclude_none=True) for item in history],
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
            scope_hint=payload.scope_hint,
            scope_mode=payload.scope_mode,
        )
        return {
            "binding": result.binding.model_dump(exclude_none=True),
            "scopeChain": result.scope_chain,
            "evidence": result.evidence,
            "requestedScope": result.requested_scope,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/runtime-events")
async def get_session_runtime_events(session_id: str, after_seq: int | None = None):
    try:
        return runtime_command_router.get_events(session_id, after_seq=after_seq)
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
    try:
        return runtime_command_router.get_snapshot(session_id)
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
