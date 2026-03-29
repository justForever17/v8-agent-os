from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.extensions_runtime import extensions_runtime_service


router = APIRouter()


@router.get("/extensions/catalog")
async def get_extensions_catalog():
    try:
        return extensions_runtime_service.build_catalog()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/health")
async def get_extensions_health():
    try:
        return extensions_runtime_service.build_health()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/usage-summary")
async def get_extensions_usage_summary(window_hours: int = 24):
    try:
        return extensions_runtime_service.build_usage_summary(window_hours=max(1, min(window_hours, 168)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/extensions/reload")
async def reload_extensions():
    try:
        payload = await extensions_runtime_service.reload()
        return {"status": "success", **payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
