from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from core.storage import storage
from core.storage_retention import storage_retention_service


router = APIRouter(prefix="/storage-retention", tags=["storage-retention"])


@router.get("/stats")
async def get_storage_retention_stats():
    try:
        return storage_retention_service.build_stats()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/dry-run")
async def dry_run_storage_retention(payload: dict[str, Any] | None = Body(default=None)):
    try:
        return storage_retention_service.enforce(dry_run=True, reason=str((payload or {}).get("reason") or "manual_dry_run"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/prune")
async def prune_storage_retention(payload: dict[str, Any] | None = Body(default=None)):
    try:
        return storage_retention_service.enforce(dry_run=False, reason=str((payload or {}).get("reason") or "manual_prune"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/config")
async def save_storage_retention_config(payload: dict[str, Any] = Body(...)):
    try:
        storage.save_storage_retention_config(payload)
        return {"status": "success", "config": storage_retention_service.get_config()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
