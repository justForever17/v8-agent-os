import traceback

from fastapi import APIRouter, Body, HTTPException, Query, Request

from core.plugin_host import plugin_host_service
from core.storage import storage
from .service import PluginConfigValidationError

router = APIRouter(prefix="/v1/plugin-host", tags=["PluginHost"])


def _snapshot():
    return plugin_host_service.public_snapshot()


@router.get("")
async def get_plugin_host_snapshot():
    return _snapshot()


@router.post("/rescan")
async def rescan_plugin_host():
    snapshot = plugin_host_service.rescan()
    return {"status": "success", "pluginHost": snapshot}


@router.post("/install")
async def install_plugin(payload: dict = Body(default={})):
    try:
        job = await plugin_host_service.create_install_job(
            install_spec=str(payload.get("installSpec") or "").strip() or None,
            installer_command=str(payload.get("installerCommand") or "").strip() or None,
            plugin_type_hint=str(payload.get("pluginTypeHint") or "").strip() or None,
            requested_by=str(payload.get("requestedBy") or "admin").strip() or "admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "job": job, "pluginHost": _snapshot()}


@router.get("/install-jobs/{job_id}")
async def get_plugin_install_job(job_id: str):
    job = plugin_host_service.get_install_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"安装任务不存在：{job_id}")
    return {"job": job, "pluginHost": _snapshot()}


@router.post("/plugins/{plugin_id}/activation")
async def set_plugin_activation(plugin_id: str, payload: dict = Body(default={})):
    enabled = bool(payload.get("enabled", True))
    snapshot = plugin_host_service.public_snapshot(plugin_host_service.set_activation_state(plugin_id, enabled))
    return {"status": "success", "pluginId": plugin_id, "enabled": enabled, "pluginHost": snapshot}


@router.post("/plugins/{plugin_id}/health")
async def refresh_plugin_health(plugin_id: str):
    try:
        snapshot = plugin_host_service.public_snapshot(await plugin_host_service.refresh_plugin_health(plugin_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}") from exc
    return {"status": "success", "pluginId": plugin_id, "pluginHost": snapshot}


@router.post("/plugins/{plugin_id}/config")
async def save_plugin_config(plugin_id: str, payload: dict = Body(default={})):
    try:
        snapshot = plugin_host_service.public_snapshot(await plugin_host_service.save_plugin_config(
            plugin_id,
            values=dict(payload.get("values") or {}),
            account_id=str(payload.get("accountId") or "").strip() or None,
        ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}") from exc
    except PluginConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "fieldErrors": exc.field_errors,
                "normalizedPreview": exc.normalized_preview,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "pluginId": plugin_id, "pluginHost": snapshot}


@router.post("/plugins/{plugin_id}/retry-onboarding")
async def retry_plugin_onboarding(plugin_id: str, payload: dict = Body(default={})):
    try:
        job = await plugin_host_service.retry_onboarding(
            plugin_id,
            requested_by=str(payload.get("requestedBy") or "admin").strip() or "admin",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "job": job, "pluginHost": _snapshot()}


@router.post("/send")
async def send_via_plugin_host(payload: dict = Body(default={})):
    channel_type = str(payload.get("channelType") or "").strip()
    receive_id = str(payload.get("receiveId") or "").strip()
    text = str(payload.get("text") or "").strip()
    media_url = str(payload.get("mediaUrl") or "").strip()
    if not channel_type or not receive_id or (not text and not media_url):
        raise HTTPException(status_code=400, detail="缺少 channelType / receiveId，且 text / mediaUrl 至少提供一项")
    chat_type = "group" if str(payload.get("chatType") or "").strip().lower() == "group" else "p2p"
    agent_id = str(payload.get("agentId") or "supervisor").strip() or "supervisor"
    trigger_source = str(payload.get("triggerSource") or "plugin_host_manual_send").strip() or "plugin_host_manual_send"
    ledger_error: str | None = None
    session_id: str | None = None
    try:
        account_id = str(payload.get("accountId") or "").strip() or None
        reply_to_id = str(payload.get("replyToId") or "").strip() or None
        thread_id = str(payload.get("threadId") or "").strip() or None
        agent_profile = storage.get_agent_runtime_profile(agent_id)
        if media_url:
            receipt = await plugin_host_service.broadcast_media(
                channel_type=channel_type,
                receive_id=receive_id,
                media_url=media_url,
                text=text or None,
                account_id=account_id,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
            )
            try:
                from runtimes.plugin_host.runtime import plugin_host_runtime

                session_id = plugin_host_runtime.record_media_push(
                    source=channel_type,
                    chat_type=chat_type,
                    remote_id=receive_id,
                    trigger_source=trigger_source,
                    agent_id=agent_id,
                    agent_profile=agent_profile,
                    delivery_receipt=receipt,
                    visible_content=text or None,
                    media_delivery={
                        "kind": "manual_media_probe",
                        "deliveryMode": "attachment",
                        "mediaUrl": media_url,
                        **dict(receipt.get("mediaAsset") or {}),
                    },
                )
            except Exception as exc:
                ledger_error = str(exc).strip() or exc.__class__.__name__
        else:
            receipt = await plugin_host_service.broadcast_text(
                channel_type=channel_type,
                receive_id=receive_id,
                text=text,
                account_id=account_id,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
            )
            try:
                from runtimes.plugin_host.runtime import plugin_host_runtime

                session_id = plugin_host_runtime.record_outbound_push(
                    source=channel_type,
                    chat_type=chat_type,
                    remote_id=receive_id,
                    final_msg=text,
                    trigger_source=trigger_source,
                    agent_id=agent_id,
                    agent_profile=agent_profile,
                    delivery_receipt=receipt,
                )
            except Exception as exc:
                ledger_error = str(exc).strip() or exc.__class__.__name__
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "receipt": receipt,
        "sessionId": session_id,
        "ledgerStatus": "degraded" if ledger_error else "ok",
        **({"ledgerError": ledger_error} if ledger_error else {}),
    }


@router.post("/inbound")
async def handoff_plugin_host_inbound(request: Request, payload: dict = Body(default={})):
    try:
        return await plugin_host_service.handle_inbound_handoff(
            client_host=str(getattr(request.client, "host", "") or "").strip(),
            payload=dict(payload or {}),
        )
    except RuntimeError as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        status_code = 403 if "仅允许本机" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip() or exc.__class__.__name__) from exc
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "message": str(exc).strip() or exc.__class__.__name__,
                "type": exc.__class__.__name__,
            },
        ) from exc


@router.get("/bridge/tools")
async def list_plugin_host_bridge_tools(
    query: str | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=128),
    refresh: bool = Query(default=False),
):
    try:
        return {"status": "success", **plugin_host_service.list_bridge_tools(query=query, limit=limit, refresh=refresh)}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bridge/tools/invoke")
async def invoke_plugin_host_bridge_tool(payload: dict = Body(default={})):
    try:
        result = plugin_host_service.invoke_bridge_tool(
            tool_name=str(payload.get("canonicalName") or payload.get("toolName") or "").strip(),
            plugin_id=str(payload.get("pluginId") or "").strip() or None,
            params=dict(payload.get("params") or {}),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "result": result}


@router.delete("/plugins/{plugin_id}")
async def delete_plugin(plugin_id: str):
    try:
        snapshot = plugin_host_service.uninstall_plugin(plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "pluginId": plugin_id, "pluginHost": snapshot}
