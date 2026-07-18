from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from erc.checkpoint_governance import CheckpointGovernanceError, checkpoint_governance_service


router = APIRouter(prefix="/checkpoint-governance", tags=["checkpoint-governance"])


@router.post("/plan")
async def plan_checkpoint_operation(payload: dict[str, Any] = Body(...)):
    try:
        return await checkpoint_governance_service.plan(
            mode=str(payload.get("mode") or ""),
            source_session_id=str(payload.get("sourceSessionId") or payload.get("source_session_id") or ""),
            source_checkpoint_id=str(payload.get("sourceCheckpointId") or payload.get("source_checkpoint_id") or ""),
            user_id=str(payload.get("userId") or payload.get("user_id") or ""),
            state_patch=payload.get("statePatch") if isinstance(payload.get("statePatch"), dict) else payload.get("state_patch"),
            as_node=str(payload.get("asNode") or payload.get("as_node") or ""),
        )
    except CheckpointGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/operations/{operation_id}")
async def get_checkpoint_operation(operation_id: str):
    try:
        return checkpoint_governance_service.get_operation(operation_id)
    except CheckpointGovernanceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/operations/{operation_id}/execute")
async def execute_checkpoint_operation(operation_id: str):
    """Deterministic execution entry; still refuses operations without an approved gate."""

    try:
        return await checkpoint_governance_service.execute_approved(operation_id)
    except CheckpointGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
