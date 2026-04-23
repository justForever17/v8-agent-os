from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from runtimes.engineering.service import engineering_lane_service


router = APIRouter(prefix="/engineering-lane", tags=["engineering-lane"])


@router.post("/dry-run")
async def engineering_lane_dry_run(payload: dict):
    try:
        return engineering_lane_service.dry_run(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/proof-ledger")
async def list_engineering_proof_entries(
    session_id: Optional[str] = Query(default=None, alias="sessionId"),
    run_id: Optional[str] = Query(default=None, alias="runId"),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    try:
        return {
            "items": engineering_lane_service.list_proof_entries(
                session_id=session_id,
                run_id=run_id,
                status=status,
                limit=limit,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/proof-ledger/{entry_id}")
async def get_engineering_proof_entry(entry_id: str):
    try:
        item = engineering_lane_service.get_proof_entry(entry_id)
        if not item:
            raise HTTPException(status_code=404, detail="Proof entry not found")
        return item
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/workset-observations")
async def list_engineering_workset_observations(
    session_id: Optional[str] = Query(default=None, alias="sessionId"),
    run_id: Optional[str] = Query(default=None, alias="runId"),
    task_brief_id: Optional[str] = Query(default=None, alias="taskBriefId"),
    decision_source: Optional[str] = Query(default=None, alias="decisionSource"),
    limit: int = Query(default=40, ge=1, le=200),
):
    try:
        return {
            "items": engineering_lane_service.list_workset_observations(
                session_id=session_id,
                run_id=run_id,
                task_brief_id=task_brief_id,
                decision_source=decision_source,
                limit=limit,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/proof-ledger")
async def add_engineering_proof_entry(payload: dict):
    try:
        return engineering_lane_service.add_proof_entry(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/proof-ledger/refresh")
async def refresh_engineering_proof_entry(payload: dict):
    try:
        session_id = str((payload or {}).get("sessionId") or (payload or {}).get("session_id") or "").strip()
        run_id = str((payload or {}).get("runId") or (payload or {}).get("run_id") or "").strip()
        if not session_id or not run_id:
            raise HTTPException(status_code=400, detail="sessionId and runId are required")
        return engineering_lane_service.refresh_proof_from_existing_evidence(session_id=session_id, run_id=run_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
