from fastapi import APIRouter, Body, HTTPException, Request

from core.extensions_runtime import extensions_runtime_service
from core.memory.backend_health import inspect_memory_backend
from core.plugin_host.silk_codec import silk_toolchain_status
from core.storage import storage
from erc.kernel import erc_kernel
from mcp_client import mcp_manager
from skills.loader import SkillLoader
from runtimes.memory.prompts import (
    render_memory_admin_chat_prompt,
    render_memory_consolidation_prompt,
    render_memory_extraction_prompt,
    render_periodic_summary_prompt,
)

from . import chat_realtime_routes as chat_realtime_routes_module
from . import command_preset_routes as command_preset_routes_module
from . import config_registry_routes as config_registry_routes_module
from . import computer_use_routes as computer_use_routes_module
from . import desktop_live_routes as desktop_live_routes_module
from . import extensions_routes as extensions_routes_module
from . import knowledge_routes as knowledge_routes_module
from . import ops_routes as ops_routes_module
from . import platform_routes as platform_routes_module
from . import rpa_routes as rpa_routes_module
from . import run_control_routes as run_control_routes_module
from . import session_workflow_routes as session_workflow_routes_module
from .models import RunCommandPayload


router = APIRouter()
router.include_router(chat_realtime_routes_module.router)
router.include_router(command_preset_routes_module.router)
router.include_router(config_registry_routes_module.router)
router.include_router(extensions_routes_module.router)
router.include_router(session_workflow_routes_module.router)
router.include_router(run_control_routes_module.router)
router.include_router(platform_routes_module.router)
router.include_router(knowledge_routes_module.router)
router.include_router(computer_use_routes_module.router)
router.include_router(desktop_live_routes_module.router)
router.include_router(rpa_routes_module.router)
router.include_router(ops_routes_module.router)


def _get_agent_profile(agent_id: str) -> dict[str, str]:
    return storage.get_agent_runtime_profile(agent_id)


@router.get("/health")
async def health():
    skills_status = SkillLoader.get_startup_status()
    mcp_status = mcp_manager.get_startup_status()
    extensions_status = extensions_runtime_service.get_startup_status()
    return {
        "status": "ok",
        "mcp_tools": len(mcp_manager.get_tools()),
        "mcp": mcp_manager.get_health_summary(),
        "skillsStartupState": skills_status.get("startupState"),
        "extensionsStartupState": extensions_status.get("startupState"),
        "mcpStartupState": mcp_status.get("startupState"),
        "skillsRuntime": skills_status,
        "extensionsRuntime": extensions_status,
        "mcpRuntime": mcp_status,
        "silk": silk_toolchain_status(),
        "memory": inspect_memory_backend(),
        "identity": storage.get_system_identity(),
    }


@router.get("/sessions")
async def compat_get_sessions(request: Request):
    return await session_workflow_routes_module.get_sessions(
        request=request,
        user_id=None,
        workspace_id=None,
        scope=None,
        source="all",
        limit=50,
    )


@router.get("/sessions/{session_id}/messages")
async def compat_get_session_messages(session_id: str):
    return await session_workflow_routes_module.get_session_messages(session_id=session_id)


@router.get("/sessions/{session_id}/snapshot")
async def compat_get_runtime_snapshot(session_id: str):
    return await session_workflow_routes_module.get_session_snapshot(session_id=session_id)


@router.get("/sessions/{session_id}/workflow")
async def compat_get_workflow_for_session(session_id: str):
    return await session_workflow_routes_module.get_workflow_for_session(session_id=session_id)


@router.get("/workflows/{workflow_id}")
async def compat_get_workflow_detail(workflow_id: str):
    return await session_workflow_routes_module.get_workflow_detail(workflow_id=workflow_id)


@router.get("/runs/{run_id}/workflow")
async def compat_get_workflow_for_run(run_id: str):
    return await session_workflow_routes_module.get_workflow_for_run(run_id=run_id)


@router.get("/runtime-capabilities")
async def compat_get_runtime_capabilities(query: str | None = None):
    return await session_workflow_routes_module.get_runtime_capabilities(query=query)


@router.get("/realtime/sessions/{session_id}/snapshot")
async def compat_get_realtime_snapshot(session_id: str):
    return await session_workflow_routes_module.get_realtime_snapshot(session_id=session_id)


@router.get("/realtime/sessions/{session_id}/events")
async def compat_get_realtime_events(session_id: str, after_seq: int | None = None):
    return await session_workflow_routes_module.get_realtime_events(session_id=session_id, after_seq=after_seq)


@router.get("/approvals")
async def compat_get_pending_approvals():
    return await run_control_routes_module.get_pending_approvals()


@router.post("/approvals/{approval_id}/approve")
async def compat_approve_pending_approval(
    approval_id: str,
    body: dict = Body(default_factory=dict),
):
    return await run_control_routes_module.approve_pending_approval(
        approval_id=approval_id,
        payload=RunCommandPayload.model_validate(body or {}),
    )


@router.post("/approvals/{approval_id}/reject")
async def compat_reject_pending_approval(
    approval_id: str,
    body: dict = Body(default_factory=dict),
):
    return await run_control_routes_module.reject_pending_approval(
        approval_id=approval_id,
        payload=RunCommandPayload.model_validate(body or {}),
    )


@router.get("/runtime-capabilities/{kind}")
async def compat_get_runtime_capability(kind: str):
    registry = erc_kernel.get_runtime_registry()
    description = registry.describe(kind)
    if not description:
        raise HTTPException(status_code=404, detail=f"Runtime '{kind}' not found")
    return description


@router.get("/config/prompts/{name}")
async def compat_get_prompt(name: str):
    try:
        if name == "supervisor":
            return {"name": name, "content": storage.read_text("V8_AGENT_OS.md"), "embedded": False}
        if name == "memory_extraction":
            return {"name": name, "content": render_memory_extraction_prompt("{format_instructions}"), "embedded": True}
        if name == "memory_consolidation":
            return {"name": name, "content": render_memory_consolidation_prompt("{format_instructions}"), "embedded": True}
        if name == "memory_admin_chat":
            return {"name": name, "content": render_memory_admin_chat_prompt(), "embedded": True}
        if name == "memory_periodic_summary":
            return {"name": name, "content": render_periodic_summary_prompt(tier="week", content="<recent_logs>"), "embedded": True}
        raise HTTPException(status_code=404, detail="Unknown prompt")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/config/prompts/{name}")
async def compat_save_prompt(name: str, body: dict):
    try:
        if name == "supervisor":
            storage.write_text("V8_AGENT_OS.md", body.get("content", ""))
            return {"status": "success", "embedded": False}
        if name in {"memory_extraction", "memory_consolidation", "memory_admin_chat", "memory_periodic_summary"}:
            raise HTTPException(status_code=400, detail="Memory prompts are embedded in code and are read-only.")
        raise HTTPException(status_code=404, detail="Unknown prompt")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
