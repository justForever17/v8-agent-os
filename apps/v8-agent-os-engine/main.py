import os
import sys
import asyncio
from contextlib import asynccontextmanager


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
from core.audio import routes as audio_routes
from core.runtime.health import inspect_engine_runtime
from core.extensions_runtime import extensions_runtime_service
from core.memory.backend_health import inspect_memory_backend
from core.plugin_host import plugin_host_service
from core.plugin_host.silk_codec import silk_toolchain_status
from core.plugin_host import routes as plugin_host_routes
from core.models.provider_compatibility import install_provider_compatibility_patches
from runtimes.network_supervisor import network_supervisor_runtime, network_supervisor_service
from core.storage import storage
from core.system_base import get_allowed_origins
from skills.loader import SkillLoader
from erc.checkpoint_store import checkpoint_store

from mcp_client import mcp_manager
from core.automation.cron import cron_manager
from erc.session_admission_service import session_admission_service
from erc.workflow_ledger import workflow_ledger_service

install_provider_compatibility_patches()

# Import side effect: register network_supervisor runtime into runtime_registry/capability_registry.
_ = network_supervisor_runtime


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


async def _safe_initialize_mcp(app: FastAPI):
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
    runtime_health = inspect_engine_runtime()
    print(
        "[Engine] Runtime launch context:",
        {
            "interpreter": runtime_health["interpreterPath"],
            "launch_mode": runtime_health["launchMode"],
            "reload": runtime_health["reload"],
            "interpreter_drift": runtime_health["interpreterDrift"],
            "launcher_drift": runtime_health["launcherDrift"],
        },
    )
    for warning in runtime_health.get("warnings") or []:
        print(f"[Engine] Runtime warning: {warning}")
    _start_skill_refresh(app)
    await _reconcile_orphaned_workflows()
    await _reconcile_session_lanes()
    
    # Initialize MCP Client connections
    await _safe_initialize_mcp(app)
    await extensions_runtime_service.start(
        skill_refresh_task=getattr(app.state, "skills_refresh_task", None),
        mcp_init_task=getattr(app.state, "mcp_init_task", None),
    )
    
    # Start Cron Manager
    cron_manager.start()
    
    # Start Plugin Host before optional runtimes so plugin registry and host state are ready.
    await plugin_host_service.start()
    await network_supervisor_service.start()
    
    yield
    # Shutdown logic
    print("[Engine] Shutting down V8 Agent OS Engine...")
    cron_manager.shutdown()
    await network_supervisor_service.stop()
    await extensions_runtime_service.stop()
    await plugin_host_service.stop()
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
app.include_router(audio_routes.router)
app.include_router(plugin_host_routes.router)

@app.get("/workspace/{file_path:path}")
async def serve_workspace_file(file_path: str):
    from core.storage import storage
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    import os
    
    workspace_config = storage.get_workspace_config()
    base_path = workspace_config.get("agent_workspace_path")
    if not base_path:
        raise HTTPException(status_code=404, detail="Workspace not configured")
        
    full_path = os.path.abspath(os.path.join(base_path, file_path))
    if not full_path.startswith(os.path.abspath(base_path)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(full_path)

@app.get("/health")
async def health_check():
    skills_status = SkillLoader.get_startup_status()
    mcp_status = mcp_manager.get_startup_status()
    extensions_status = extensions_runtime_service.get_startup_status()
    return {
        "status": "ok",
        "service": "v8-agent-os-engine",
        "mcp_tools": len(mcp_manager.get_tools()),
        "mcp": mcp_manager.get_health_summary(),
        "skillsStartupState": skills_status.get("startupState"),
        "extensionsStartupState": extensions_status.get("startupState"),
        "mcpStartupState": mcp_status.get("startupState"),
        "skillsRuntime": skills_status,
        "extensionsRuntime": extensions_status,
        "mcpRuntime": mcp_status,
        "silk": silk_toolchain_status(),
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
