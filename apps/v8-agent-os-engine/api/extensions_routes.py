from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from core.extensions_runtime import extensions_runtime_service
from core.extensions_store_service import (
    ExtensionStoreError,
    install_store_mcp,
    install_store_skill,
    list_store_mcp,
    list_store_skills,
    get_store_mcp_detail,
)
from core.mcp_config_service import McpConfigValidationError
from core.skills_install_service import SkillInstallValidationError


router = APIRouter()


@router.get("/extensions/catalog")
async def get_extensions_catalog(
    workspacePath: str | None = None,
    workspaceId: str | None = None,
    projectId: str | None = None,
):
    try:
        return extensions_runtime_service.build_catalog(
            workspace_path=workspacePath,
            workspace_id=workspaceId,
            project_id=projectId,
        )
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


@router.get("/extensions/preview")
async def get_extensions_preview(
    query: str = "",
    refresh: bool = False,
    workspacePath: str | None = None,
    workspaceId: str | None = None,
    projectId: str | None = None,
):
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        return await extensions_runtime_service.build_prefilter_preview(
            user_query=normalized_query,
            refresh=bool(refresh),
            workspace_path=workspacePath,
            workspace_id=workspaceId,
            project_id=projectId,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/extensions/reload")
async def reload_extensions():
    try:
        payload = await extensions_runtime_service.reload()
        return {"status": "success", **payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/skills")
async def get_extensions_store_skills(
    query: str = "",
    limit: int = Query(default=24, ge=1, le=60),
    refresh: bool = False,
):
    try:
        return list_store_skills(query=query, limit=limit, refresh=refresh)
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/extensions/store/skills/install")
async def install_extensions_store_skill(payload: dict = Body(...)):
    try:
        return install_store_skill(payload)
    except SkillInstallValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.to_payload())
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/mcp")
async def get_extensions_store_mcp(
    query: str = "",
    limit: int = Query(default=24, ge=1, le=60),
    refresh: bool = False,
):
    try:
        return list_store_mcp(query=query, limit=limit, refresh=refresh)
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/mcp/detail")
async def get_extensions_store_mcp_detail(
    id: str,
    refresh: bool = False,
):
    try:
        return get_store_mcp_detail(mcp_id=id, refresh=refresh)
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/extensions/store/mcp/install")
async def install_extensions_store_mcp(payload: dict = Body(...)):
    try:
        return install_store_mcp(payload)
    except McpConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.to_payload())
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/extensions/skills/{skill_id}")
async def delete_extension_skill(
    skill_id: str,
    scope: str | None = None,
    workspaceId: str | None = None,
    workspacePath: str | None = None,
    projectId: str | None = None,
):
    try:
        return extensions_runtime_service.delete_skill(
            skill_id,
            scope=scope,
            workspace_id=workspaceId,
            workspace_path=workspacePath,
            project_id=projectId,
            initiated_by="admin_extensions_skill_list_delete",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
