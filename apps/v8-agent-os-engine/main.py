import os
import sys
import asyncio
import importlib
from contextlib import asynccontextmanager
from pathlib import Path


def _configure_pycache_behavior() -> None:
    if os.environ.get("V8_AGENT_OS_DISABLE_BYTECODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        sys.dont_write_bytecode = True
        return

    configured_prefix = str(
        os.environ.get("V8_AGENT_OS_PYCACHE_PREFIX")
        or (os.path.join(os.path.expanduser("~"), ".v8-agent-os", "cache", "pycache", "engine"))
    ).strip()
    if not configured_prefix:
        return
    os.makedirs(configured_prefix, exist_ok=True)
    os.environ.setdefault("PYTHONPYCACHEPREFIX", configured_prefix)
    sys.pycache_prefix = configured_prefix


_configure_pycache_behavior()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import routes
from core.runtime.health import inspect_engine_runtime
from core.models.provider_compatibility import install_provider_compatibility_patches
from core.runtime.startup_profile import (
    build_installation_snapshot,
    disabled_reason_summary,
    resolve_install_profile,
    resolve_startup_profile,
    resolve_install_platform,
    runtime_cluster_summary,
    runtime_submode_summary,
    service_enabled,
    service_state,
    startup_bundle_diagnostics,
    startup_bundle_summary,
)
from core.storage import storage
from core.system_base import get_allowed_origins
from core.workspace_guard import ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from skills.loader import SkillLoader
from erc.checkpoint_store import checkpoint_store

from erc.session_admission_service import session_admission_service
from erc.workflow_ledger import workflow_ledger_service

install_provider_compatibility_patches()
STARTUP_PROFILE = resolve_startup_profile()
INSTALL_PROFILE = resolve_install_profile()
INSTALL_PLATFORM = resolve_install_platform()


def _build_cors_allow_origins() -> list[str]:
    defaults = [
        "http://localhost:9527",
        "http://127.0.0.1:9527",
        "http://localhost:9528",
        "http://127.0.0.1:9528",
    ]
    allowlist: list[str] = []
    seen: set[str] = set()
    for candidate in [*defaults, *get_allowed_origins()]:
        normalized = str(candidate or "").strip().rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        allowlist.append(normalized)
    return allowlist


def _log_background_task(task: asyncio.Task, label: str) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        print(f"[Engine] Background task '{label}' failed: {type(exc).__name__}: {exc}")


def _import_module(module_name: str):
    return importlib.import_module(module_name)


def _get_mcp_manager():
    return _import_module("mcp_client").mcp_manager


def _get_extensions_runtime_service():
    return _import_module("core.extensions_runtime").extensions_runtime_service


def _get_plugin_host_service():
    return _import_module("core.plugin_host").plugin_host_service


def _get_plugin_host_routes():
    return _import_module("core.plugin_host.routes")


def _get_audio_routes():
    return _import_module("core.audio.routes")


def _get_cron_manager():
    return _import_module("core.automation.cron").cron_manager


def _get_network_supervisor_service():
    return _import_module("runtimes.network_supervisor.service").network_supervisor_service


def _ensure_network_supervisor_runtime_registered() -> None:
    _import_module("runtimes.network_supervisor.runtime")


def _get_memory_backend_health():
    return _import_module("core.memory.backend_health").inspect_memory_backend


def _get_silk_toolchain_status():
    status = _import_module("core.plugin_host.silk_codec").silk_toolchain_status
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


def _service_flags(profile: str = STARTUP_PROFILE) -> dict[str, bool]:
    return {
        "audio": service_enabled("audio", profile=profile),
        "mcp": service_enabled("mcp", profile=profile),
        "skills": service_enabled("skills", profile=profile),
        "extensions": service_enabled("extensions", profile=profile),
        "cron": service_enabled("cron", profile=profile),
        "plugin_host": service_enabled(
            "plugin_host",
            profile=profile,
            runtime_kind="plugin_host",
            config_enabled=_plugin_host_enabled(),
        ),
        "network_supervisor": service_enabled(
            "network_supervisor",
            profile=profile,
            runtime_kind="network_supervisor",
            config_enabled=_network_supervisor_enabled(),
        ),
        "desktop_live": service_enabled(
            "desktop_live",
            profile=profile,
            config_enabled=_desktop_live_enabled(),
        ),
    }


def _service_states(profile: str = STARTUP_PROFILE) -> dict[str, dict[str, object]]:
    return {
        "audio": service_state("audio", profile=profile),
        "mcp": service_state("mcp", profile=profile),
        "skills": service_state("skills", profile=profile),
        "extensions": service_state("extensions", profile=profile),
        "cron": service_state("cron", profile=profile),
        "plugin_host": service_state(
            "plugin_host",
            profile=profile,
            runtime_kind="plugin_host",
            config_enabled=_plugin_host_enabled(),
        ),
        "network_supervisor": service_state(
            "network_supervisor",
            profile=profile,
            runtime_kind="network_supervisor",
            config_enabled=_network_supervisor_enabled(),
        ),
        "desktop_live": service_state(
            "desktop_live",
            profile=profile,
            config_enabled=_desktop_live_enabled(),
        ),
    }


async def _safe_initialize_mcp(app: FastAPI):
    mcp_manager = _get_mcp_manager()
    init_task = asyncio.create_task(mcp_manager.initialize())
    init_task.add_done_callback(lambda task: _log_background_task(task, "mcp_initialize"))
    app.state.mcp_init_task = init_task
    try:
        await asyncio.wait_for(asyncio.shield(init_task), timeout=8.0)
        print("[Engine] MCP initialization completed during startup.")
    except asyncio.TimeoutError:
        print("[Engine] MCP initialization is still running in background; startup will continue.")
    except asyncio.CancelledError:
        print("[Engine] MCP initialization cancelled during startup (non-fatal).")


def _start_skill_refresh(app: FastAPI) -> None:
    loaded_from_cache = SkillLoader.prime_startup_cache()
    if loaded_from_cache:
        print(f"[Engine] Loaded {len(SkillLoader.get_all_skills(force_refresh=False))} cached native skills.")
    else:
        print("[Engine] No cached skills snapshot found. Skill registry will warm up in background.")
    task = SkillLoader.schedule_background_refresh()
    task.add_done_callback(lambda refresh_task: _log_background_task(refresh_task, "skills_refresh"))
    app.state.skills_refresh_task = task

async def _safe_cleanup():
    """MCP cleanup with timeout guard. Falls back to process-level cleanup if blocked."""
    mcp_manager = _get_mcp_manager()
    cleanup_task = asyncio.create_task(mcp_manager.cleanup())
    try:
        await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=5.0)
        print("[Engine] MCP cleanup completed.")
    except asyncio.TimeoutError:
        print("[Engine] MCP cleanup timed out after 5s — process exit will handle remaining resources.")
        cleanup_task.cancel()
    except asyncio.CancelledError:
        # uvicorn --reload tears down the cancel scope, causing shield to fail.
        # This is expected and non-fatal — process exit handles resource cleanup.
        print("[Engine] MCP cleanup cancelled during shutdown (non-fatal).")
    except Exception as e:
        print(f"[Engine] MCP cleanup error (non-fatal): {e}")


async def _safe_close_checkpoints():
    try:
        await checkpoint_store.close()
        print("[Engine] Checkpoint store closed.")
    except Exception as e:
        print(f"[Engine] Checkpoint store close error (non-fatal): {e}")


async def _reconcile_orphaned_workflows():
    try:
        result = workflow_ledger_service.reconcile_orphaned_runs()
        print(
            "[Engine] Workflow orphan reconciliation completed.",
            result,
        )
    except Exception as e:
        print(f"[Engine] Workflow orphan reconciliation error (non-fatal): {e}")


async def _reconcile_session_lanes():
    try:
        result = session_admission_service.reconcile_after_restart()
        print(
            "[Engine] Session lane reconciliation completed.",
            result,
        )
    except Exception as e:
        print(f"[Engine] Session lane reconciliation error (non-fatal): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic: Load all SKILL.md files into the registry
    print("[Engine] Bootstrapping V8 Agent OS Engine...")
    service_flags = _service_flags()
    runtime_health = inspect_engine_runtime()
    print(
        "[Engine] Runtime launch context:",
        {
            "interpreter": runtime_health["interpreterPath"],
            "launch_mode": runtime_health["launchMode"],
            "reload": runtime_health["reload"],
            "interpreter_drift": runtime_health["interpreterDrift"],
            "launcher_drift": runtime_health["launcherDrift"],
            "startup_profile": STARTUP_PROFILE,
            "install_profile": INSTALL_PROFILE,
            "install_platform": INSTALL_PLATFORM,
            "installed_runtime_families": build_installation_snapshot()["installedRuntimeFamilies"],
            "startup_bundle": startup_bundle_summary(STARTUP_PROFILE),
            "startup_services": _service_states(STARTUP_PROFILE),
        },
    )
    for warning in runtime_health.get("warnings") or []:
        print(f"[Engine] Runtime warning: {warning}")
    if service_flags["skills"]:
        _start_skill_refresh(app)
    await _reconcile_orphaned_workflows()
    await _reconcile_session_lanes()

    if service_flags["mcp"]:
        await _safe_initialize_mcp(app)
    if service_flags["extensions"]:
        await _get_extensions_runtime_service().start(
            skill_refresh_task=getattr(app.state, "skills_refresh_task", None),
            mcp_init_task=getattr(app.state, "mcp_init_task", None),
        )
    if service_flags["cron"]:
        _get_cron_manager().start()
    if service_flags["plugin_host"]:
        await _get_plugin_host_service().start()
    if service_flags["network_supervisor"]:
        _ensure_network_supervisor_runtime_registered()
        await _get_network_supervisor_service().start()
    
    yield
    # Shutdown logic
    print("[Engine] Shutting down V8 Agent OS Engine...")
    if service_flags["cron"]:
        _get_cron_manager().shutdown()
    if service_flags["network_supervisor"]:
        await _get_network_supervisor_service().stop()
    if service_flags["extensions"]:
        await _get_extensions_runtime_service().stop()
    if service_flags["plugin_host"]:
        await _get_plugin_host_service().stop()
    skills_task = getattr(app.state, "skills_refresh_task", None)
    if skills_task and not skills_task.done():
        skills_task.cancel()
        try:
            await skills_task
        except asyncio.CancelledError:
            pass
    init_task = getattr(app.state, "mcp_init_task", None)
    if init_task and not init_task.done():
        init_task.cancel()
        try:
            await init_task
        except asyncio.CancelledError:
            pass
    # MCP cleanup with timeout — prevents Lark WebSocket from blocking shutdown
    if service_flags["mcp"]:
        print("[MCP] Cleaning up MCP Client connections...")
        await _safe_cleanup()
    await _safe_close_checkpoints()

app = FastAPI(
    title="V8 Agent OS Engine",
    description="Python Native Orchestration Engine for V8 Agent OS using FastAPI and LangGraph",
    version="1.0.0",
    lifespan=lifespan
)

# Allow CORS for local V8 Agent OS development (Web: 9527, Admin: 9528)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(routes.router, prefix="/v1")
if _service_flags()["audio"]:
    app.include_router(_get_audio_routes().router)
if _service_flags()["plugin_host"]:
    app.include_router(_get_plugin_host_routes().router)

@app.get("/workspace/{file_path:path}")
async def serve_workspace_file(file_path: str):
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    try:
        workspace_root = ensure_workspace_auto_create_allowed(
            Path(workspace_resolution_service.get_main_workspace_path()).expanduser(),
            source="engine.main.serve_workspace_file",
            allow_missing=True,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    requested_path = (workspace_root / file_path).resolve(strict=False)
    try:
        requested_path.relative_to(workspace_root.resolve(strict=False))
    except ValueError as error:
        raise HTTPException(status_code=403, detail="Access denied") from error

    if not requested_path.exists() or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(requested_path)

@app.get("/health")
async def health_check():
    service_flags = _service_flags()
    service_states = _service_states()
    skills_status = SkillLoader.get_startup_status()
    mcp_status = _get_mcp_manager().get_startup_status() if service_flags["mcp"] else {"startupState": "disabled"}
    extensions_status = (
        _get_extensions_runtime_service().get_startup_status()
        if service_flags["extensions"]
        else {"startupState": "disabled"}
    )
    silk_status = _get_silk_toolchain_status() if service_flags["plugin_host"] else {"status": "disabled"}
    inspect_memory_backend = _get_memory_backend_health()
    return {
        "status": "ok",
        "service": "v8-agent-os-engine",
        **build_installation_snapshot(),
        "startupProfile": STARTUP_PROFILE,
        "startupBundle": startup_bundle_summary(STARTUP_PROFILE),
        "runtimeClusters": runtime_cluster_summary(STARTUP_PROFILE),
        "runtimeSubmodes": runtime_submode_summary(STARTUP_PROFILE),
        "startupDiagnostics": startup_bundle_diagnostics(STARTUP_PROFILE),
        "disabledReasons": disabled_reason_summary(STARTUP_PROFILE),
        "serviceStates": service_states,
        "mcp_tools": len(_get_mcp_manager().get_tools()) if service_flags["mcp"] else 0,
        "mcp": _get_mcp_manager().get_health_summary() if service_flags["mcp"] else {"status": "disabled"},
        "skillsStartupState": skills_status.get("startupState"),
        "extensionsStartupState": extensions_status.get("startupState"),
        "mcpStartupState": mcp_status.get("startupState"),
        "skillsRuntime": skills_status,
        "extensionsRuntime": extensions_status,
        "mcpRuntime": mcp_status,
        "silk": silk_status,
        "engineRuntime": inspect_engine_runtime(),
        "memory": inspect_memory_backend(),
        "identity": storage.get_system_identity(),
    }

if __name__ == "__main__":
    import uvicorn

    def _env_flag(name: str, default: bool = False) -> bool:
        raw = str(os.getenv(name, "")).strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    # V8 Agent OS uses 9528 for admin and 9527 for web by default.
    # The canonical stable entry is explicit interpreter + foreground single-process.
    port = int(os.getenv("ENGINE_PORT", 9530))
    host = os.getenv("ENGINE_HOST", "0.0.0.0")
    reload_enabled = _env_flag("ENGINE_RELOAD", default=False)
    if os.name == "nt" and reload_enabled:
        print("[Engine] ENGINE_RELOAD=1 on Windows is development-only and does not guarantee interpreter consistency.")
    if reload_enabled:
        uvicorn.run("main:app", host=host, port=port, reload=True)
    else:
        uvicorn.run(app, host=host, port=port, reload=False)
