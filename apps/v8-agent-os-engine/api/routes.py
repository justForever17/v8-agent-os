import importlib

from fastapi import APIRouter, Body, HTTPException, Request

from core.runtime.startup_profile import (
    disabled_reason_summary,
    resolve_startup_profile,
    runtime_cluster_summary,
    runtime_submode_summary,
    service_enabled,
    service_state,
    startup_bundle_diagnostics,
    startup_bundle_summary,
)
from core.storage import storage
from erc.kernel import erc_kernel
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
from . import platform_routes as platform_routes_module
from . import run_control_routes as run_control_routes_module
from . import session_workflow_routes as session_workflow_routes_module
from .models import RunCommandPayload


router = APIRouter()
_STARTUP_PROFILE = resolve_startup_profile()


def _load_router_module(module_name: str):
    return importlib.import_module(f"{__package__}.{module_name}")


def _include_optional_router(module_name: str) -> None:
    module = _load_router_module(module_name)
    router.include_router(module.router)


def _get_extensions_runtime_service():
    return importlib.import_module("core.extensions_runtime").extensions_runtime_service


def _get_mcp_manager():
    return importlib.import_module("mcp_client").mcp_manager


def _get_memory_backend_health():
    return importlib.import_module("core.memory.backend_health").inspect_memory_backend


def _get_silk_toolchain_status():
    status = importlib.import_module("core.plugin_host.silk_codec").silk_toolchain_status
    return status() if callable(status) else status


def _plugin_host_enabled() -> bool:
    try:
        return bool(storage.get_plugin_host_config().get("enabled", True))
    except Exception:
        return True


def _network_supervisor_enabled() -> bool:
    try:
        return bool(storage.get_network_supervisor_runtime_config().get("enabled", False))
    except Exception:
        return False


def _desktop_live_enabled() -> bool:
    try:
        return bool((storage.get_system_base_config().get("desktopLive") or {}).get("enabled", True))
    except Exception:
        return True


def _service_states(profile: str = _STARTUP_PROFILE) -> dict[str, dict[str, object]]:
    return {
        "extensions": service_state("extensions", profile=profile),
        "knowledge": service_state("knowledge", profile=profile),
        "network_supervisor": service_state(
            "network_supervisor",
            profile=profile,
            runtime_kind="network_supervisor",
            config_enabled=_network_supervisor_enabled(),
        ),
        "computer_use": service_state(
            "computer_use",
            profile=profile,
            runtime_kind="computer_use",
        ),
        "desktop_live": service_state(
            "desktop_live",
            profile=profile,
            config_enabled=_desktop_live_enabled(),
        ),
        "rpa": service_state(
            "rpa",
            profile=profile,
            runtime_kind="rpa",
        ),
        "ops": service_state("ops", profile=profile),
        "mcp": service_state("mcp", profile=profile),
        "plugin_host": service_state(
            "plugin_host",
            profile=profile,
            runtime_kind="plugin_host",
            config_enabled=_plugin_host_enabled(),
        ),
    }


router.include_router(chat_realtime_routes_module.router)
router.include_router(command_preset_routes_module.router)
router.include_router(config_registry_routes_module.router)
router.include_router(session_workflow_routes_module.router)
router.include_router(run_control_routes_module.router)
router.include_router(platform_routes_module.router)

if service_enabled("extensions", profile=_STARTUP_PROFILE):
    _include_optional_router("extensions_routes")
if service_enabled("knowledge", profile=_STARTUP_PROFILE):
    _include_optional_router("knowledge_routes")
if service_enabled("network_supervisor", profile=_STARTUP_PROFILE, runtime_kind="network_supervisor"):
    _include_optional_router("network_supervisor_routes")
if service_enabled("computer_use", profile=_STARTUP_PROFILE, runtime_kind="computer_use"):
    _include_optional_router("computer_use_routes")
if service_enabled("desktop_live", profile=_STARTUP_PROFILE):
    _include_optional_router("desktop_live_routes")
if service_enabled("rpa", profile=_STARTUP_PROFILE, runtime_kind="rpa"):
    _include_optional_router("rpa_routes")
if service_enabled("ops", profile=_STARTUP_PROFILE):
    _include_optional_router("ops_routes")


def _get_agent_profile(agent_id: str) -> dict[str, str]:
    return storage.get_agent_runtime_profile(agent_id)


@router.get("/health")
async def health():
    service_states = _service_states()
    skills_status = SkillLoader.get_startup_status()
    mcp_enabled = service_enabled("mcp", profile=_STARTUP_PROFILE)
    extensions_enabled = service_enabled("extensions", profile=_STARTUP_PROFILE)
    mcp_status = _get_mcp_manager().get_startup_status() if mcp_enabled else {"startupState": "disabled"}
    extensions_status = (
        _get_extensions_runtime_service().get_startup_status()
        if extensions_enabled
        else {"startupState": "disabled"}
    )
    inspect_memory_backend = _get_memory_backend_health()
    return {
        "status": "ok",
        "mcp_tools": len(_get_mcp_manager().get_tools()) if mcp_enabled else 0,
        "mcp": _get_mcp_manager().get_health_summary() if mcp_enabled else {"status": "disabled"},
        "skillsStartupState": skills_status.get("startupState"),
        "extensionsStartupState": extensions_status.get("startupState"),
        "mcpStartupState": mcp_status.get("startupState"),
        "startupProfile": _STARTUP_PROFILE,
        "startupBundle": startup_bundle_summary(_STARTUP_PROFILE),
        "runtimeClusters": runtime_cluster_summary(_STARTUP_PROFILE),
        "runtimeSubmodes": runtime_submode_summary(_STARTUP_PROFILE),
        "startupDiagnostics": startup_bundle_diagnostics(_STARTUP_PROFILE),
        "disabledReasons": disabled_reason_summary(_STARTUP_PROFILE),
        "serviceStates": service_states,
        "skillsRuntime": skills_status,
        "extensionsRuntime": extensions_status,
        "mcpRuntime": mcp_status,
        "silk": _get_silk_toolchain_status() if service_enabled("plugin_host", profile=_STARTUP_PROFILE) else {"status": "disabled"},
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
