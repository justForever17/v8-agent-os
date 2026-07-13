from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from core.storage import storage
from core.storage_registry import storage_registry_service
from core.storage_retention import storage_retention_service
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


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


@router.post("/compact")
async def compact_storage(payload: dict[str, Any] | None = Body(default=None)):
    try:
        return storage_retention_service.compact_physical(
            reason=str((payload or {}).get("reason") or "manual_idle_compaction")
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/registry/refresh")
async def refresh_storage_registry():
    try:
        return storage_registry_service.snapshot(home=V8_AGENT_OS_HOME, refresh=True, schedule_refresh=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/config")
async def save_storage_retention_config(payload: dict[str, Any] = Body(...)):
    try:
        storage.save_storage_retention_config(payload)
        return {"status": "success", "config": storage_retention_service.get_config()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
