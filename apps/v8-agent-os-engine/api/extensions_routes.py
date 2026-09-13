from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, HTTPException, Query

from core.extensions_store_service import (
    ExtensionStoreError,
    install_store_mcp,
    install_store_skill,
    list_store_mcp,
    list_store_skills,
    get_store_mcp_detail,
    get_store_skill_detail,
)
from core.mcp_config_service import McpConfigValidationError
from core.skills_install_service import SkillInstallValidationError


router = APIRouter()


def _get_extensions_runtime_service():
    from core.extensions_runtime import extensions_runtime_service

    return extensions_runtime_service


@router.get("/extensions/catalog")
async def get_extensions_catalog(
    workspacePath: str | None = None,
    workspaceId: str | None = None,
    projectId: str | None = None,
):
    try:
        return _get_extensions_runtime_service().build_catalog(
            workspace_path=workspacePath,
            workspace_id=workspaceId,
            project_id=projectId,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/health")
async def get_extensions_health():
    try:
        return _get_extensions_runtime_service().build_health()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/usage-summary")
async def get_extensions_usage_summary(window_hours: int = 24):
    try:
        return _get_extensions_runtime_service().build_usage_summary(window_hours=max(1, min(window_hours, 168)))
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
        return await _get_extensions_runtime_service().build_prefilter_preview(
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
        payload = await _get_extensions_runtime_service().reload()
        return {"status": "success", **payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/skills")
async def get_extensions_store_skills(
    query: str = "",
    limit: int = Query(default=24, ge=1, le=60),
    refresh: bool = False,
    provider: str = "international",
    page: int = Query(default=1, ge=1, le=10000),
):
    try:
        return await asyncio.to_thread(
            list_store_skills,
            query=query,
            limit=limit,
            refresh=refresh,
            **({"provider": provider, "page": page} if provider != "international" or isinstance(page, int) and page != 1 else {}),
        )
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/extensions/store/skills/install")
async def install_extensions_store_skill(payload: dict = Body(...)):
    try:
        return await asyncio.to_thread(install_store_skill, payload)
    except SkillInstallValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.to_payload())
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/skills/detail")
async def get_extensions_store_skill_detail(
    source: str,
    skillId: str,
    refresh: bool = False,
    provider: str = "international",
):
    try:
        return await asyncio.to_thread(
            get_store_skill_detail,
            source=source,
            skill_id=skillId,
            refresh=refresh,
            **({"provider": provider} if provider != "international" else {}),
        )
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/mcp")
async def get_extensions_store_mcp(
    query: str = "",
    limit: int = Query(default=24, ge=1, le=60),
    refresh: bool = False,
    provider: str = "international",
    page: int = Query(default=1, ge=1, le=10000),
):
    try:
        return await asyncio.to_thread(
            list_store_mcp,
            query=query,
            limit=limit,
            refresh=refresh,
            **({"provider": provider, "page": page} if provider != "international" or isinstance(page, int) and page != 1 else {}),
        )
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/mcp/detail")
async def get_extensions_store_mcp_detail(
    id: str,
    refresh: bool = False,
    provider: str = "international",
):
    try:
        return await asyncio.to_thread(
            get_store_mcp_detail,
            mcp_id=id,
            refresh=refresh,
            **({"provider": provider} if provider != "international" else {}),
        )
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/extensions/store/mcp/install")
async def install_extensions_store_mcp(payload: dict = Body(...)):
    try:
        return await asyncio.to_thread(install_store_mcp, payload)
    except McpConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.to_payload())
    except ExtensionStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_payload())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/extensions/store/operations")
async def get_store_operations():
    from core.extensions_store_operations import list_operations
    return await asyncio.to_thread(list_operations)


@router.post("/extensions/store/operations/{kind}")
async def create_store_operation(kind: str, payload: dict = Body(...)):
    from core.extensions_store_operations import start_operation
    if kind not in {"skills", "mcp"}:
        raise HTTPException(status_code=400, detail="无效扩展类型。")
    if payload.get("provider", "international") not in {"international", "modelscope"}:
        raise HTTPException(status_code=400, detail="无效来源。")
    return await asyncio.to_thread(start_operation, kind, payload,
                                  install_store_skill if kind == "skills" else install_store_mcp)


@router.post("/extensions/store/operations/{operation_id}/cancel")
async def cancel_store_operation(operation_id: str):
    from core.extensions_store_operations import cancel_operation
    try:
        return await asyncio.to_thread(cancel_operation, operation_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="安装操作不存在。")


@router.post("/extensions/store/mcp/check")
async def check_store_mcp(payload: dict = Body(...)):
    from core.mcp_config_service import mcp_runtime_status_snapshot, request_mcp_inventory_refresh
    await asyncio.to_thread(request_mcp_inventory_refresh, "extensions_store_check")
    snapshot = await asyncio.to_thread(mcp_runtime_status_snapshot)
    server_name = str(payload.get("serverName") or "")
    status = (snapshot.get("servers") or {}).get(server_name) or {"status": "pending"}
    return {"serverName": server_name, "connection": status, "businessReachability": "not_checked", "authorized": False}


@router.delete("/extensions/skills/{skill_id}")
async def delete_extension_skill(
    skill_id: str,
    scope: str | None = None,
    workspaceId: str | None = None,
    workspacePath: str | None = None,
    projectId: str | None = None,
):
    try:
        return _get_extensions_runtime_service().delete_skill(
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
