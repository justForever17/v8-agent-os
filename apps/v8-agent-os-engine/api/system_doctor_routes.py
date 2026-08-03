from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from core.system_doctor import system_doctor_service


router = APIRouter(prefix="/system/doctor", tags=["system-doctor"])


@router.get("")
async def get_system_doctor():
    try:
        return await asyncio.to_thread(system_doctor_service.run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/repair-plan")
async def build_system_doctor_repair_plan(payload: dict[str, Any] | None = Body(default=None)):
    try:
        checks = payload.get("checks") if isinstance(payload, dict) else None
        return await asyncio.to_thread(
            system_doctor_service.build_repair_plan,
            checks if isinstance(checks, list) else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
