from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from core.config_migration import config_migration_service


router = APIRouter(prefix="/config/migrations", tags=["config-migrations"])


@router.get("")
async def list_config_migrations():
    try:
        return config_migration_service.list_ledger()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/plan")
async def get_config_migration_plan(target: str = "storage_retention_balanced"):
    try:
        return config_migration_service.build_plan(target=target)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/apply")
async def apply_config_migration(payload: dict[str, Any] | None = Body(default=None)):
    try:
        data = payload or {}
        return config_migration_service.apply_plan(
            target=str(data.get("target") or "storage_retention_balanced"),
            reason=str(data.get("reason") or "admin_migration"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/rollback/{migration_id}")
async def rollback_config_migration(migration_id: str):
    try:
        result = config_migration_service.rollback(migration_id)
        if result.get("status") in {"not_found", "backup_missing"}:
            raise HTTPException(status_code=404, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
