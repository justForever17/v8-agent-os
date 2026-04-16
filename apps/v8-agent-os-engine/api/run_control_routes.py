from fastapi import APIRouter, HTTPException

from .models import RunCommandPayload
from core.database import db
from erc.command_router import runtime_command_router
from erc.models import RuntimeCommand


router = APIRouter()


@router.get("/approvals")
async def list_pending_approvals(
    session_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
):
    try:
        approvals = db.list_pending_approvals(
            session_id=session_id,
            run_id=run_id,
            status=status,
        )
        filtered = []
        for approval in approvals:
            request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            approval_kind = str(approval.get("approval_kind") or request.get("approvalKind") or request.get("approval_kind") or "").strip().lower()
            interaction_kind = str(request.get("interactionKind") or request.get("interaction_kind") or approval.get("interaction_kind") or "").strip().lower()
            if approval_kind == "ask_user" or interaction_kind == "ask_user":
                continue
            filtered.append(approval)
        return {
            "approvals": filtered
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
async def list_runs(
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
):
    try:
        return {
            "runs": db.list_run_records(
                session_id=session_id,
                status=status,
                limit=max(1, min(limit, 100)),
            )
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/commands/{command}")
async def dispatch_run_command(run_id: str, command: str, payload: RunCommandPayload):
    try:
        result = runtime_command_router.dispatch_run_command(
            RuntimeCommand(
                topic=f"run.{command}",
                run_id=run_id,
                reason=payload.reason or f"manual_{command}",
                response=dict(payload.response or {}),
                payload=dict(payload.payload or {}),
            )
        )
        if not result:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approvals/{approval_id}/approve")
async def approve_pending_approval(approval_id: str, payload: RunCommandPayload):
    try:
        result = runtime_command_router.dispatch_approval_command(
            RuntimeCommand(
                topic="approval.approve",
                approval_id=approval_id,
                response=dict(payload.response or {}),
                payload=dict(payload.payload or {}),
            )
        )
        if not result:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approvals/{approval_id}/reject")
async def reject_pending_approval(approval_id: str, payload: RunCommandPayload):
    try:
        result = runtime_command_router.dispatch_approval_command(
            RuntimeCommand(
                topic="approval.reject",
                approval_id=approval_id,
                response=dict(payload.response or {}),
                payload=dict(payload.payload or {}),
            )
        )
        if not result:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask-user/{interaction_id}/respond")
async def respond_ask_user(interaction_id: str, payload: RunCommandPayload):
    try:
        response = dict(payload.response or payload.payload or {})
        if "answer" not in response and payload.reason:
            response["answer"] = payload.reason
        response.setdefault("interactionId", interaction_id)
        result = runtime_command_router.dispatch_ask_user_command(
            RuntimeCommand(
                topic="ask_user.respond",
                interaction_id=interaction_id,
                response=response,
                payload=dict(payload.payload or {}),
            )
        )
        if not result:
            raise HTTPException(status_code=404, detail=f"Ask-user interaction '{interaction_id}' not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
