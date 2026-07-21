from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from runtimes.plugin_manager.catalog import RESOURCE_ROOT, plugin_catalog_service
from runtimes.plugin_manager.service import PluginManagerError, plugin_manager_service


router = APIRouter(prefix="/api/plugins", tags=["plugin-manager"])


def _raise(exc: PluginManagerError):
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})


@router.get("/catalog")
async def get_catalog(refresh: bool = Query(default=False)):
    if refresh:
        await plugin_catalog_service.refresh()
    return plugin_manager_service.list_catalog()


@router.get("/mentions")
async def get_plugin_mentions():
    """Return the minimal plugin projection used by Web/Phone composers."""
    catalog = plugin_manager_service.list_catalog()
    items: list[dict[str, Any]] = []
    for plugin in list(catalog.get("plugins") or []):
        if not isinstance(plugin, dict):
            continue
        plugin_id = str(plugin.get("id") or "").strip()
        if not plugin_id:
            continue
        status = str(plugin_manager_service.readiness_status(plugin_id).get("status") or "invalid")
        provider_adapters = list(plugin.get("providerAdapters") or [])
        reviewed_mcp = [item for item in list(plugin.get("mcpServers") or []) if list(item.get("allowedTools") or [])]
        cli_profiles = list(plugin.get("cliProfiles") or [])
        skills = list(plugin.get("skills") or [])
        default_component = next(
            (
                str(item.get("id") or "").strip()
                for group in (provider_adapters, reviewed_mcp, cli_profiles, skills)
                for item in group
                if str(item.get("id") or "").strip()
            ),
            "",
        )
        items.append(
            {
                "pluginId": plugin_id,
                "displayName": str(plugin.get("displayName") or plugin_id).strip(),
                "description": str(plugin.get("description") or "").strip(),
                "status": status,
                "configurationUrl": f"/admin/plugins?plugin={plugin_id}",
                "componentIds": [default_component] if default_component else [],
            }
        )
    return {"items": items}


@router.get("/oauth/callback", response_class=HTMLResponse)
async def complete_oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    from runtimes.extensions.mcp.oauth import mcp_oauth_coordinator

    result = mcp_oauth_coordinator.complete(code=code, state=state, error=error)
    succeeded = bool(result.get("ok"))
    title = "授权已接收" if succeeded else "授权未完成"
    detail = "可以关闭此窗口并返回 V8 Agent OS。" if succeeded else "请返回 V8 Agent OS 重新发起授权。"
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        f"<title>{title}</title><body style='font-family:system-ui;padding:32px'>"
        f"<h1>{title}</h1><p>{detail}</p></body></html>",
        status_code=200 if succeeded else 400,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/{plugin_id}/oauth/start")
async def start_plugin_oauth(
    plugin_id: str,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(default={}),
):
    try:
        prepared = plugin_manager_service.prepare_oauth(
            plugin_id,
            component_id=str(payload.get("componentId") or "").strip() or None,
        )
        from runtimes.extensions.mcp.client import mcp_manager

        background_tasks.add_task(mcp_manager.refresh_server, prepared["serverName"])
        return prepared
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/{plugin_id}/oauth/status")
async def get_plugin_oauth_status(plugin_id: str, componentId: str | None = None):
    try:
        manifest = plugin_manager_service._manifest(plugin_id)
        selected = next(
            (
                item
                for item in manifest.mcpServers
                if (not componentId or item.id == componentId)
                and any(str(field).strip().lower() == "oauth" for field in item.authFields)
            ),
            None,
        )
        if selected is None:
            raise PluginManagerError("插件没有 OAuth MCP 组件", code="plugin_oauth_component_not_found", status_code=404)
        from runtimes.extensions.mcp.oauth import mcp_oauth_coordinator

        return {
            "pluginId": manifest.id,
            "componentId": selected.id,
            **mcp_oauth_coordinator.status(selected.serverName),
            "configuration": plugin_manager_service.configuration_requirements(manifest.id),
        }
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/oauth/cancel")
async def cancel_plugin_oauth(plugin_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        manifest = plugin_manager_service._manifest(plugin_id)
        component_id = str(payload.get("componentId") or "").strip()
        selected = next(
            (
                item
                for item in manifest.mcpServers
                if (not component_id or item.id == component_id)
                and any(str(field).strip().lower() == "oauth" for field in item.authFields)
            ),
            None,
        )
        if selected is None:
            raise PluginManagerError("插件没有 OAuth MCP 组件", code="plugin_oauth_component_not_found", status_code=404)
        from runtimes.extensions.mcp.oauth import mcp_oauth_coordinator

        return {"pluginId": manifest.id, "componentId": selected.id, **mcp_oauth_coordinator.cancel(selected.serverName)}
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/cli-login/start")
async def start_plugin_cli_login(plugin_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return await asyncio.to_thread(
            plugin_manager_service.start_cli_login,
            plugin_id,
            component_id=str(payload.get("componentId") or "").strip(),
        )
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/{plugin_id}/cli-login/status")
async def get_plugin_cli_login_status(plugin_id: str, componentId: str = Query(default="")):
    try:
        return await asyncio.to_thread(
            plugin_manager_service.cli_login_status,
            plugin_id,
            component_id=str(componentId or "").strip(),
        )
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/cli-login/cancel")
async def cancel_plugin_cli_login(plugin_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return await asyncio.to_thread(
            plugin_manager_service.cancel_cli_login,
            plugin_id,
            component_id=str(payload.get("componentId") or "").strip(),
        )
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/installed")
async def get_installed():
    return plugin_manager_service.list_installed()


@router.get("/status-summary")
async def get_status_summary(sessionId: str | None = None, runId: str | None = None):
    return plugin_manager_service.status_summary(session_id=sessionId, run_id=runId)


@router.get("/grants")
async def get_grants(sessionId: str | None = None, runId: str | None = None):
    if sessionId:
        return {"items": plugin_manager_service.active_grants(session_id=sessionId, run_id=runId)}
    return {"items": plugin_manager_service.list_active_grants()}


@router.get("/events")
async def get_events(pluginId: str | None = None, limit: int = 100):
    return plugin_manager_service.list_events(plugin_id=pluginId, limit=limit)


@router.get("/{plugin_id}/configuration-requirements")
async def get_configuration_requirements(plugin_id: str):
    try:
        return plugin_manager_service.configuration_requirements(plugin_id)
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/configuration-detect")
async def detect_configuration(plugin_id: str):
    try:
        return plugin_manager_service.detect_configuration_sources(plugin_id)
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/configuration-import")
async def import_configuration(plugin_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        if payload.get("confirmed") is not True:
            raise PluginManagerError(
                "导入已有授权必须由用户明确确认。",
                code="credential_import_confirmation_required",
                status_code=409,
            )
        return await plugin_manager_service.import_configuration_source(
            plugin_id,
            requirement_id=str(payload.get("requirementId") or ""),
            source_id=str(payload.get("sourceId") or ""),
        )
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/{plugin_id}/authorization-status")
async def get_authorization_status(
    plugin_id: str,
    sessionId: str | None = None,
    runId: str | None = None,
):
    return plugin_manager_service.authorization_status(
        plugin_id,
        session_id=sessionId,
        run_id=runId,
    )


@router.get("/{plugin_id}/readiness")
async def get_plugin_readiness(plugin_id: str):
    return plugin_manager_service.readiness_status(plugin_id)


@router.get("/{plugin_id}/machine-discovery")
async def get_plugin_machine_discovery(plugin_id: str, refresh: bool = Query(default=False)):
    try:
        return await asyncio.to_thread(
            plugin_manager_service.discover_machine_components,
            plugin_id,
            force=refresh,
        )
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/{plugin_id}/logo")
async def get_plugin_logo(plugin_id: str):
    try:
        manifest = plugin_manager_service._manifest(plugin_id)
        verification = plugin_manager_service.verify_brand_asset(manifest)
        if not verification["ok"]:
            raise PluginManagerError("品牌资产校验失败", code="brand_asset_hash_mismatch", status_code=409)
        path = Path(verification["path"])
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400, immutable"})
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(default={}),
):
    try:
        dry_run = bool(payload.get("dryRun", True))
        job = plugin_manager_service.create_install_job(
            plugin_id,
            dry_run=dry_run,
            approved=bool(payload.get("approved", False)),
            plan_digest=str(payload.get("planDigest") or "").strip() or None,
            idempotency_key=str(payload.get("idempotencyKey") or "").strip() or None,
        )
        if not dry_run:
            background_tasks.add_task(plugin_manager_service.run_install_job, job["jobId"])
        return job
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/install-jobs/{job_id}")
async def get_install_job(job_id: str):
    try:
        return plugin_manager_service.get_install_job(job_id)
    except PluginManagerError as exc:
        _raise(exc)


@router.get("/install-jobs")
async def get_install_jobs(limit: int = 100):
    return plugin_manager_service.list_install_jobs(limit=limit)


@router.post("/{plugin_id}/configure")
async def configure_plugin(plugin_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return await plugin_manager_service.configure(plugin_id, dict(payload.get("values") or payload))
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/{plugin_id}/doctor")
async def doctor_plugin(plugin_id: str):
    try:
        return await plugin_manager_service.doctor(plugin_id)
    except PluginManagerError as exc:
        _raise(exc)


@router.delete("/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    force: bool = Query(default=False),
    purge: bool = Query(default=False),
):
    try:
        return plugin_manager_service.uninstall(plugin_id, force=force, purge=purge)
    except PluginManagerError as exc:
        _raise(exc)


@router.post("/grants")
async def create_grant(payload: dict[str, Any] = Body(default={})):
    try:
        return plugin_manager_service.create_grant(
            plugin_id=str(payload.get("pluginId") or ""),
            scope=str(payload.get("scope") or "task"),
            session_id=str(payload.get("sessionId") or ""),
            run_id=str(payload.get("runId") or "").strip() or None,
            grantee_type=str(payload.get("granteeType") or "supervisor"),
            grantee_id=str(payload.get("granteeId") or "supervisor"),
            component_ids=list(payload.get("componentIds") or []),
            parent_grant_id=str(payload.get("parentGrantId") or "").strip() or None,
            grant_source="admin",
        )
    except PluginManagerError as exc:
        _raise(exc)


@router.delete("/grants/{grant_id}")
async def revoke_grant(grant_id: str):
    try:
        return plugin_manager_service.revoke_grant(grant_id)
    except PluginManagerError as exc:
        _raise(exc)
