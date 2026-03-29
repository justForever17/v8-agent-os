from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.command_presets import list_command_presets, read_command_preset


router = APIRouter()


@router.get("/commands")
async def get_command_presets():
    try:
        return {
            "items": list_command_presets(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/commands/{name}")
async def get_command_preset(name: str):
    try:
        preset = read_command_preset(name)
        if not preset:
            raise HTTPException(status_code=404, detail="Command preset not found")
        return preset
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
