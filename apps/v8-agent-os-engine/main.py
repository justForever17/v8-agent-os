import os
import sys
import asyncio
import importlib
import inspect
import time
from contextlib import asynccontextmanager
from pathlib import Path


_PROCESS_BOOT_STARTED_AT = time.perf_counter()


# LangGraph reads this switch at module import time. Keep it ahead of all local
# imports so graph compilation can derive and apply the state-schema allowlist.
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"


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

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from core.runtime.startup_profile import (
    build_installation_snapshot,
    disabled_reason_summary,
    get_runtime_registry_state,
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

STARTUP_PROFILE = resolve_startup_profile()
INSTALL_PROFILE = resolve_install_profile()
INSTALL_PLATFORM = resolve_install_platform()

from api import routes
from core.runtime.health import inspect_engine_runtime
from core.time_truth import utc_now_iso
from core.realtime_protocol import utc_now_iso
from core.storage import storage
from core.storage_retention import storage_retention_service
from core.knowledge_projection import knowledge_projection_service
from core.system_base import get_allowed_origins
from core.workspace_guard import ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from erc.checkpoint_store import checkpoint_store

from erc.session_admission_service import session_admission_service
from erc.workflow_ledger import workflow_ledger_service

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
    return _import_module("runtimes.extensions.mcp.client").mcp_manager


def _get_extensions_runtime_service():
    return _import_module("runtimes.extensions.runtime").extensions_runtime_service


def _get_audio_routes():
    return _import_module("core.audio.routes")


def _get_cron_manager():
    return _import_module("core.automation.cron").cron_manager


def _get_network_supervisor_service():
    return _import_module("runtimes.network_supervisor.service").network_supervisor_service


def _get_network_neighbor_service():
    return _import_module("runtimes.network_supervisor.neighbor").network_neighbor_service


def _get_network_relay_worker_service():
    return _import_module("runtimes.network_supervisor.relay_runtime").network_relay_worker_service


def _get_runtime_episode_runner():
    return _import_module("core.runtime_episode_runner").runtime_episode_runner


def _get_config_broker_service():
    return _import_module("core.config_broker_service").config_broker_service


def _ensure_network_supervisor_runtime_registered() -> None:
    _import_module("runtimes.network_supervisor.runtime")


def _ensure_plugin_manager_runtime_registered() -> None:
    _import_module("runtimes.plugin_manager.runtime")


def _get_memory_backend_health():
    return _import_module("core.memory.backend_health").inspect_memory_backend


async def _prewarm_provider_compatibility() -> None:
    """Warm expensive LangChain provider patches without delaying readiness."""

    try:
        install = _import_module("core.provider_compatibility").install_provider_compatibility_patches
        await asyncio.to_thread(install)
    except Exception as exc:
        print(f"[Engine] Provider compatibility prewarm failed (non-fatal): {type(exc).__name__}")


def _ensure_default_workflow_memories() -> None:
    try:
        service = _import_module("runtimes.memory.workflow_service").workflow_memory_service
        result = service.ensure_default_workflow_candidates()
        if result.get("created") or result.get("updated"):
            print("[Engine] Default workflow memories ensured.", result)
    except Exception as e:
        print(f"[Engine] Default workflow memory seeding error (non-fatal): {e}")


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


def _service_flags(profile: str = STARTUP_PROFILE, *, _state: dict[str, object] | None = None) -> dict[str, bool]:
    return {
        "audio": service_enabled("audio", profile=profile, _state=_state),
        "mcp": service_enabled("mcp", profile=profile, _state=_state),
        "skills": service_enabled("skills", profile=profile, _state=_state),
        "extensions": service_enabled("extensions", profile=profile, _state=_state),
        "cron": service_enabled("cron", profile=profile, _state=_state),
        "network_supervisor": service_enabled(
            "network_supervisor",
            profile=profile,
            runtime_kind="network_supervisor",
            config_enabled=_network_supervisor_enabled(),
            _state=_state,
        ),
        "desktop_live": service_enabled(
            "desktop_live",
            profile=profile,
            config_enabled=_desktop_live_enabled(),
            _state=_state,
        ),
    }


def _service_states(profile: str = STARTUP_PROFILE, *, _state: dict[str, object] | None = None) -> dict[str, dict[str, object]]:
    return {
        "audio": service_state("audio", profile=profile, _state=_state),
        "mcp": service_state("mcp", profile=profile, _state=_state),
        "skills": service_state("skills", profile=profile, _state=_state),
        "extensions": service_state("extensions", profile=profile, _state=_state),
        "cron": service_state("cron", profile=profile, _state=_state),
        "network_supervisor": service_state(
            "network_supervisor",
            profile=profile,
            runtime_kind="network_supervisor",
            config_enabled=_network_supervisor_enabled(),
            _state=_state,
        ),
        "desktop_live": service_state(
            "desktop_live",
            profile=profile,
            config_enabled=_desktop_live_enabled(),
            _state=_state,
        ),
    }


async def _safe_initialize_mcp(app: FastAPI, state: dict[str, object] | None = None):
    async def _initialize() -> None:
        mcp_manager = _get_mcp_manager()
        await mcp_manager.initialize()

    init_task = asyncio.create_task(_initialize())
    init_task.add_done_callback(lambda task: _log_background_task(task, "mcp_initialize"))
    if state is None:
        app.state.mcp_init_task = init_task
    else:
        _track_lifespan_task(app, state, "mcp_init_task", init_task)
    print("[Engine] MCP initialization started in background; startup will not wait for server probes.")


def _start_skill_refresh(app: FastAPI, state: dict[str, object] | None = None) -> None:
    extensions_runtime_service = _get_extensions_runtime_service()
    loaded_from_cache = extensions_runtime_service.prime_skill_cache()
    if loaded_from_cache:
        print(f"[Engine] Loaded {len(extensions_runtime_service.list_skills(force_refresh=False))} cached native skills.")
    else:
        print("[Engine] No cached skills snapshot found. Skill registry will warm up in background.")
    task = extensions_runtime_service.schedule_skill_refresh()
    task.add_done_callback(lambda refresh_task: _log_background_task(refresh_task, "skills_refresh"))
    if state is None:
        app.state.skills_refresh_task = task
    else:
        _track_lifespan_task(app, state, "skills_refresh_task", task)

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
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        # uvicorn --reload tears down the cancel scope, causing shield to fail.
        # This is expected and non-fatal — process exit handles resource cleanup.
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        print("[Engine] MCP cleanup cancelled during shutdown (non-fatal).")
    except Exception as e:
        print(f"[Engine] MCP cleanup error (non-fatal): {e}")


async def _safe_close_checkpoints():
    try:
        await checkpoint_store.close()
        print("[Engine] Checkpoint store closed.")
    except Exception as e:
        print(f"[Engine] Checkpoint store close error (non-fatal): {e}")


def _new_lifespan_state(app: FastAPI) -> dict[str, object]:
    state: dict[str, object] = {
        "phase": "initializing",
        "started": {
            "checkpoint_store": False,
            "mcp": False,
            "extensions": False,
            "cron": False,
            "network_supervisor": False,
            "network_neighbor": False,
            "network_relay": False,
            "episode_runner": False,
        },
        "tasks": [],
        "cleanup_started": False,
        "cleanup_completed": False,
        "cleanup_reason": None,
        "cleanup_actions": [],
        "cleanup_errors": [],
        "cleanup_duration_ms": None,
        "failure_phase": None,
        "failure_type": None,
    }
    app.state.lifespan_state = state
    return state


def _set_lifespan_phase(state: dict[str, object], phase: str) -> None:
    state["phase"] = phase


def _mark_lifespan_service_starting(state: dict[str, object], service: str) -> None:
    started = state["started"]
    assert isinstance(started, dict)
    started[service] = True
    _set_lifespan_phase(state, f"starting:{service}")


def _track_lifespan_task(
    app: FastAPI,
    state: dict[str, object],
    attribute: str,
    task: asyncio.Task,
) -> None:
    setattr(app.state, attribute, task)
    tasks = state["tasks"]
    assert isinstance(tasks, list)
    tasks.append((attribute, task))


def _lifespan_status_snapshot(state: dict[str, object]) -> dict[str, object]:
    tasks = state.get("tasks") or []
    started = state.get("started") or {}
    return {
        "phase": state.get("phase"),
        "startedServices": [name for name, value in dict(started).items() if value],
        "trackedTasks": [attribute for attribute, _task in tasks],
        "cleanupStarted": bool(state.get("cleanup_started")),
        "cleanupCompleted": bool(state.get("cleanup_completed")),
        "cleanupReason": state.get("cleanup_reason"),
        "cleanupDurationMs": state.get("cleanup_duration_ms"),
        "cleanupActions": list(state.get("cleanup_actions") or []),
        "cleanupErrors": list(state.get("cleanup_errors") or []),
        "failurePhase": state.get("failure_phase"),
        "failureType": state.get("failure_type"),
    }


async def _shutdown_ui_patch_preview() -> None:
    from core.ui_patch import ui_patch_service

    await asyncio.to_thread(ui_patch_service.shutdown)


async def _shutdown_lifespan_services(
    app: FastAPI,
    state: dict[str, object],
    *,
    reason: str,
) -> dict[str, object]:
    if state.get("cleanup_started"):
        return _lifespan_status_snapshot(state)

    cleanup_started_at = time.perf_counter()
    state["cleanup_started"] = True
    state["cleanup_reason"] = reason
    _set_lifespan_phase(state, f"cleanup:{reason}")
    actions = state["cleanup_actions"]
    errors = state["cleanup_errors"]
    started = state["started"]
    assert isinstance(actions, list)
    assert isinstance(errors, list)
    assert isinstance(started, dict)

    async def _attempt(label: str, callback) -> None:
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
            actions.append(label)
        except BaseException as exc:
            errors.append({"action": label, "errorType": type(exc).__name__, "message": str(exc)})
            print(f"[Engine] Lifespan cleanup action '{label}' failed: {type(exc).__name__}: {exc}")

    # Services are stopped in the exact reverse order in which startup invokes them.
    if started.get("episode_runner"):
        await _attempt("episode_runner.stop", lambda: _get_runtime_episode_runner().stop())
    if started.get("network_relay"):
        await _attempt("network_relay.stop", lambda: _get_network_relay_worker_service().stop())
    if started.get("network_neighbor"):
        await _attempt("network_neighbor.stop", lambda: _get_network_neighbor_service().stop())
    if started.get("network_supervisor"):
        await _attempt("network_supervisor.stop", lambda: _get_network_supervisor_service().stop())
    if started.get("cron"):
        await _attempt("cron.shutdown", lambda: _get_cron_manager().shutdown())
    if started.get("extensions"):
        await _attempt("extensions.stop", lambda: _get_extensions_runtime_service().stop())

    tracked_tasks = list(state.get("tasks") or [])
    for attribute, task in reversed(tracked_tasks):
        async def _cancel_task(task_to_cancel=task) -> None:
            if not task_to_cancel.done():
                task_to_cancel.cancel()
            try:
                await task_to_cancel
            except asyncio.CancelledError:
                pass

        await _attempt(f"task.cancel:{attribute}", _cancel_task)
        setattr(app.state, attribute, None)

    if started.get("mcp"):
        await _attempt("mcp.cleanup", _safe_cleanup)
    if reason == "shutdown":
        await _attempt("ui_patch.shutdown", _shutdown_ui_patch_preview)
    if started.get("checkpoint_store"):
        await _attempt("checkpoint_store.close", _safe_close_checkpoints)

    state["cleanup_duration_ms"] = round((time.perf_counter() - cleanup_started_at) * 1000, 2)
    state["cleanup_completed"] = True
    _set_lifespan_phase(state, "stopped")
    snapshot = _lifespan_status_snapshot(state)
    app.state.lifespan_diagnostics = snapshot
    if errors:
        print(
            "[Engine] Lifespan cleanup completed with errors:",
            {"reason": reason, "errorCount": len(errors), "actions": len(actions)},
        )
    else:
        print(
            "[Engine] Lifespan cleanup completed:",
            {"reason": reason, "actions": len(actions)},
        )
    return snapshot


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


async def _reconcile_engineering_workspaces():
    try:
        from core.engineering_sandbox.service import get_engineering_sandbox_service

        service = get_engineering_sandbox_service()
        result = await asyncio.to_thread(service.reconcile_startup)
        if any(result.values()):
            print("[Engine] Managed engineering workspace reconciliation completed.", result)
    except Exception as e:
        print(f"[Engine] Managed engineering workspace reconciliation error (non-fatal): {e}")


async def _reconcile_creative_canvas_graph_runs():
    try:
        from core.creative_canvas_graph import creative_canvas_graph_service

        result = await asyncio.to_thread(creative_canvas_graph_service.reconcile_startup)
        if any(result.values()):
            print("[Engine] Creative Canvas graph reconciliation completed.", result)
    except Exception as e:
        print(f"[Engine] Creative Canvas graph reconciliation error (non-fatal): {e}")


async def _reconcile_config_broker_transactions(
    app: FastAPI,
    startup_metrics: dict[str, object],
) -> dict[str, object]:
    recovery_started_at = time.perf_counter()
    config_broker_recovery = await asyncio.to_thread(
        _get_config_broker_service().reconcile_incomplete_transactions
    )
    recovery_transactions = list(config_broker_recovery.get("transactions") or [])
    recovery_state_counts: dict[str, int] = {}
    for transaction in recovery_transactions:
        recovery_state = str((transaction or {}).get("state") or "unknown")
        recovery_state_counts[recovery_state] = recovery_state_counts.get(recovery_state, 0) + 1
    recovery_summary = {
        "ok": bool(config_broker_recovery.get("ok")),
        "reconciled": int(config_broker_recovery.get("reconciled") or 0),
        "stateCounts": recovery_state_counts,
    }
    app.state.config_broker_recovery = recovery_summary
    startup_metrics["configBrokerRecoveryMs"] = round(
        (time.perf_counter() - recovery_started_at) * 1000,
        2,
    )
    startup_metrics["configBrokerRecovery"] = recovery_summary
    if recovery_summary["ok"]:
        print("[Engine] Config Broker transaction recovery completed:", recovery_summary)
    else:
        print(
            "[Engine] Config Broker transaction recovery requires explicit operator recovery:",
            recovery_summary,
        )
    return recovery_summary


async def _start_lifespan_services(app: FastAPI, state: dict[str, object]) -> None:
    startup_started_at = time.perf_counter()
    _set_lifespan_phase(state, "config_migration")
    startup_metrics = {
        "moduleImportMs": round((_MODULE_IMPORT_COMPLETED_AT - _PROCESS_BOOT_STARTED_AT) * 1000, 2),
        "startupQueueMs": round((startup_started_at - _MODULE_IMPORT_COMPLETED_AT) * 1000, 2),
    }
    app.state.startup_metrics = startup_metrics
    # Startup logic: Load all SKILL.md files into the registry
    print("[Engine] Bootstrapping V8 Agent OS Engine...")
    from core.model_control_plane import model_control_plane

    config_migration_started_at = time.perf_counter()
    await asyncio.to_thread(storage.migrate_legacy_local_config)
    await asyncio.to_thread(storage.migrate_legacy_mcp_config)
    await asyncio.to_thread(storage.ensure_legacy_model_bindings_migrated)
    await asyncio.to_thread(storage.migrate_system_base_config)
    await asyncio.to_thread(storage.migrate_safety_guardian_config)
    reasoning_migrations = await asyncio.to_thread(model_control_plane.migrate_reasoning_surfaces)
    if reasoning_migrations:
        print(
            "[Engine] Applied explicit model reasoning-surface migrations:",
            {"records": len(reasoning_migrations)},
        )
    startup_metrics["configMigrationMs"] = round((time.perf_counter() - config_migration_started_at) * 1000, 2)

    _set_lifespan_phase(state, "config_broker_recovery")
    await _reconcile_config_broker_transactions(app, startup_metrics)

    _set_lifespan_phase(state, "checkpoint_preflight")
    state_preflight_started_at = time.perf_counter()
    _mark_lifespan_service_starting(state, "checkpoint_store")
    checkpoint_preflight = await checkpoint_store.ensure_preflight()
    print(
        "[Engine] Checkpoint security preflight completed:",
        {
            "policyVersion": checkpoint_preflight.get("policyVersion"),
            "mode": checkpoint_preflight.get("mode"),
            "checkpointRows": checkpoint_preflight.get("checkpointRows"),
            "writeRows": checkpoint_preflight.get("writeRows"),
            "durationMs": checkpoint_preflight.get("durationMs"),
        },
    )
    from core.database import db
    from core.provider_continuation import migrate_persisted_provider_continuations

    continuation_migration = await asyncio.to_thread(migrate_persisted_provider_continuations, db)
    if continuation_migration["migratedRows"] or continuation_migration["invalidRows"]:
        print(
            "[Engine] Provider continuation metadata migration completed:",
            continuation_migration,
        )
    applied_memory_defaults = storage.ensure_memory_runtime_defaults()
    if applied_memory_defaults:
        print("[Engine] Applied memory runtime defaults:", applied_memory_defaults)
    _ensure_default_workflow_memories()
    startup_metrics["statePreflightMs"] = round((time.perf_counter() - state_preflight_started_at) * 1000, 2)
    service_flags = _service_flags()
    state["service_flags"] = service_flags
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
        skill_refresh_started_at = time.perf_counter()
        _start_skill_refresh(app, state)
        startup_metrics["skillRefreshScheduleMs"] = round(
            (time.perf_counter() - skill_refresh_started_at) * 1000,
            2,
        )
    reconciliation_started_at = time.perf_counter()
    await _reconcile_orphaned_workflows()
    await _reconcile_session_lanes()
    await _reconcile_engineering_workspaces()
    await _reconcile_creative_canvas_graph_runs()
    startup_metrics["reconciliationMs"] = round((time.perf_counter() - reconciliation_started_at) * 1000, 2)
    service_start_started_at = time.perf_counter()
    async def _cleanup_terminal_engineering_workspaces() -> None:
        try:
            from core.engineering_sandbox.service import get_engineering_sandbox_service

            service = get_engineering_sandbox_service()
            accepted_cleanup = await asyncio.to_thread(
                service.cleanup_accepted_worktrees,
                limit_runs=250,
            )
            aged_cleanup = await asyncio.to_thread(
                service.cleanup_terminal_worktrees,
                older_than_days=1,
                abandoned_after_days=7,
                limit=250,
            )
            cleanup = {
                "removed": int(accepted_cleanup.get("removed") or 0) + int(aged_cleanup.get("removed") or 0),
                "accepted": accepted_cleanup,
                "aged": aged_cleanup,
                "failures": [
                    *(accepted_cleanup.get("failures") or []),
                    *(aged_cleanup.get("failures") or []),
                ],
            }
            if cleanup.get("removed") or cleanup.get("failures"):
                print(
                    "[Engine] Managed engineering workspace cleanup completed.",
                    {
                        "removed": cleanup["removed"],
                        "acceptedRemoved": int(accepted_cleanup.get("removed") or 0),
                        "agedRemoved": int(aged_cleanup.get("removed") or 0),
                        "failureCount": len(cleanup["failures"]),
                    },
                )
        except Exception as exc:
            print(f"[Engine] Managed engineering workspace cleanup failed (non-fatal): {exc}")

    async def _monitor_engineering_workspace_cleanup() -> None:
        while True:
            await _cleanup_terminal_engineering_workspaces()
            await asyncio.sleep(60 * 60)

    _track_lifespan_task(
        app,
        state,
        "engineering_workspace_cleanup_task",
        asyncio.create_task(_monitor_engineering_workspace_cleanup()),
    )
    async def _recover_knowledge_projections() -> None:
        try:
            projection_recovery = await asyncio.to_thread(
                knowledge_projection_service.process_outbox,
                limit=500,
            )
            if projection_recovery.get("processed"):
                print("[Engine] Knowledge projection recovery:", projection_recovery)
        except Exception as exc:
            print(f"[Engine] Knowledge projection recovery failed (non-fatal): {exc}")

    knowledge_projection_recovery_task = asyncio.create_task(_recover_knowledge_projections())
    knowledge_projection_recovery_task.add_done_callback(
        lambda task: _log_background_task(task, "knowledge_projection_recovery")
    )
    _track_lifespan_task(
        app,
        state,
        "knowledge_projection_recovery_task",
        knowledge_projection_recovery_task,
    )

    async def _run_startup_retention_check() -> None:
        try:
            retention_config = storage.get_storage_retention_config()
            if not retention_config.get("enabled", True):
                return
            result = await asyncio.to_thread(storage_retention_service.startup_check)
            plan = dict(result.get("plan") or {})
            if plan.get("actions"):
                print(
                    "[Engine] Storage retention plan ready:",
                    {
                        "status": result.get("status"),
                        "beforeBytes": plan.get("beforeBytes"),
                        "actions": len(plan.get("actions") or []),
                    },
                )
        except Exception as exc:
            print(f"[Engine] Storage retention startup check failed (non-fatal): {exc}")

    _track_lifespan_task(
        app,
        state,
        "storage_retention_startup_task",
        asyncio.create_task(_run_startup_retention_check()),
    )

    async def _monitor_storage_pressure() -> None:
        while True:
            await asyncio.sleep(30 * 60)
            try:
                retention_config = storage.get_storage_retention_config()
                if not retention_config.get("enabled", True):
                    continue
                disk = await asyncio.to_thread(storage_retention_service.disk_health)
                if disk.get("watermark") == "healthy":
                    continue
                result = await asyncio.to_thread(storage_retention_service.startup_check)
                cleanup = dict(result.get("automaticCleanup") or {})
                if cleanup:
                    print(
                        "[Engine] Disk-watermark cleanup completed:",
                        {
                            "status": result.get("status"),
                            "watermark": cleanup.get("triggerWatermark"),
                            "removedFiles": cleanup.get("removedFiles"),
                            "removedBytes": cleanup.get("removedBytes"),
                            "failures": len(cleanup.get("failures") or []),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[Engine] Disk-watermark storage check failed (non-fatal): {exc}")

    _track_lifespan_task(
        app,
        state,
        "storage_pressure_monitor_task",
        asyncio.create_task(_monitor_storage_pressure()),
    )

    plugin_registration_started_at = time.perf_counter()
    _ensure_plugin_manager_runtime_registered()
    startup_metrics["pluginRegistrationMs"] = round(
        (time.perf_counter() - plugin_registration_started_at) * 1000,
        2,
    )
    if service_flags["mcp"]:
        mcp_schedule_started_at = time.perf_counter()
        _mark_lifespan_service_starting(state, "mcp")
        await _safe_initialize_mcp(app, state)
        startup_metrics["mcpScheduleMs"] = round(
            (time.perf_counter() - mcp_schedule_started_at) * 1000,
            2,
        )
    if service_flags["extensions"]:
        extensions_start_started_at = time.perf_counter()
        _mark_lifespan_service_starting(state, "extensions")
        await _get_extensions_runtime_service().start(
            skill_refresh_task=getattr(app.state, "skills_refresh_task", None),
            mcp_init_task=getattr(app.state, "mcp_init_task", None),
            wait_for_initial_refresh=False,
        )
        startup_metrics["extensionsStartMs"] = round(
            (time.perf_counter() - extensions_start_started_at) * 1000,
            2,
        )
    if service_flags["cron"]:
        cron_start_started_at = time.perf_counter()
        _mark_lifespan_service_starting(state, "cron")
        _get_cron_manager().start()
        startup_metrics["cronStartMs"] = round(
            (time.perf_counter() - cron_start_started_at) * 1000,
            2,
        )
    if service_flags["network_supervisor"]:
        _ensure_network_supervisor_runtime_registered()
        _mark_lifespan_service_starting(state, "network_supervisor")
        await _get_network_supervisor_service().start()
        _mark_lifespan_service_starting(state, "network_neighbor")
        await _get_network_neighbor_service().start()
        _mark_lifespan_service_starting(state, "network_relay")
        await _get_network_relay_worker_service().start()
    episode_runner_started_at = time.perf_counter()
    _mark_lifespan_service_starting(state, "episode_runner")
    await _get_runtime_episode_runner().start()
    startup_metrics["episodeRunnerStartMs"] = round(
        (time.perf_counter() - episode_runner_started_at) * 1000,
        2,
    )
    session_coordination_started_at = time.perf_counter()
    try:
        from erc.session_coordination_service import session_coordination_service

        recovery = await asyncio.to_thread(session_coordination_service.recover_pending)
        if recovery.get("recovered") or recovery.get("expired"):
            print("[Engine] Session coordination recovery completed:", recovery)
    except Exception as exc:
        print(f"[Engine] Session coordination recovery failed (non-fatal): {exc}")
    startup_metrics["sessionCoordinationRecoveryMs"] = round(
        (time.perf_counter() - session_coordination_started_at) * 1000,
        2,
    )

    startup_metrics["serviceStartMs"] = round((time.perf_counter() - service_start_started_at) * 1000, 2)
    startup_metrics["lifespanMs"] = round((time.perf_counter() - startup_started_at) * 1000, 2)
    startup_metrics["readyMs"] = round((time.perf_counter() - _PROCESS_BOOT_STARTED_AT) * 1000, 2)
    app.state.startup_metrics = startup_metrics
    _set_lifespan_phase(state, "ready")
    _track_lifespan_task(
        app,
        state,
        "provider_compatibility_prewarm_task",
        asyncio.create_task(_prewarm_provider_compatibility()),
    )
    app.state.lifespan_diagnostics = _lifespan_status_snapshot(state)
    print("[Engine] Startup ready:", startup_metrics)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = _new_lifespan_state(app)
    try:
        await _start_lifespan_services(app, state)
    except BaseException as exc:
        state["failure_phase"] = state.get("phase")
        state["failure_type"] = type(exc).__name__
        startup_metrics = dict(getattr(app.state, "startup_metrics", {}) or {})
        startup_metrics.update(
            {
                "failed": True,
                "failurePhase": state.get("failure_phase"),
                "failureType": state.get("failure_type"),
            }
        )
        app.state.startup_metrics = startup_metrics
        app.state.startup_failure = {
            "phase": state.get("failure_phase"),
            "errorType": type(exc).__name__,
            "message": str(exc),
        }
        print(
            "[Engine] Startup failed; rolling back initialized services:",
            {"phase": state.get("failure_phase"), "errorType": type(exc).__name__},
        )
        await _shutdown_lifespan_services(app, state, reason="startup_failure")
        raise

    try:
        yield
    finally:
        print("[Engine] Shutting down V8 Agent OS Engine...")
        await _shutdown_lifespan_services(app, state, reason="shutdown")

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


@app.middleware("http")
async def attach_engine_now_header(request, call_next):
    response = await call_next(request)
    response.headers["x-v8-engine-now"] = utc_now_iso()
    return response

# Register API routers
app.include_router(routes.router, prefix="/v1")
if _service_flags()["audio"]:
    app.include_router(_get_audio_routes().router)

_MODULE_IMPORT_COMPLETED_AT = time.perf_counter()

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

@app.get("/readyz")
async def readiness_check(response: Response):
    runner_status = _get_runtime_episode_runner().readiness_status()
    ready = bool(runner_status.get("ready"))
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "degraded",
        "service": "v8-agent-os-engine",
        "ready": ready,
        "startup": dict(getattr(app.state, "startup_metrics", {}) or {}),
        "lifespan": dict(getattr(app.state, "lifespan_diagnostics", {}) or {}),
        "configBrokerRecovery": dict(getattr(app.state, "config_broker_recovery", {}) or {}),
        "runtimeEpisodeRunner": runner_status,
    }


@app.get("/health")
async def health_check():
    runtime_state = get_runtime_registry_state()
    service_flags = _service_flags(_state=runtime_state)
    service_states = _service_states(_state=runtime_state)
    extensions_runtime_service = _get_extensions_runtime_service()
    skills_status = extensions_runtime_service.get_skill_startup_status()
    mcp_status = extensions_runtime_service.get_mcp_startup_status() if service_flags["mcp"] else {"startupState": "disabled"}
    extensions_status = (
        extensions_runtime_service.get_startup_status()
        if service_flags["extensions"]
        else {"startupState": "disabled"}
    )
    inspect_memory_backend = _get_memory_backend_health()
    runner_status = _get_runtime_episode_runner().readiness_status()
    return {
        "status": "ok",
        "service": "v8-agent-os-engine",
        **build_installation_snapshot(_state=runtime_state),
        "startupProfile": STARTUP_PROFILE,
        "startupBundle": startup_bundle_summary(STARTUP_PROFILE, _state=runtime_state),
        "runtimeClusters": runtime_cluster_summary(STARTUP_PROFILE, _state=runtime_state),
        "runtimeSubmodes": runtime_submode_summary(STARTUP_PROFILE, _state=runtime_state),
        "startupDiagnostics": startup_bundle_diagnostics(STARTUP_PROFILE, _state=runtime_state),
        "disabledReasons": disabled_reason_summary(STARTUP_PROFILE, _state=runtime_state),
        "serviceStates": service_states,
        "mcp_tools": len(extensions_runtime_service.get_mcp_tools()) if service_flags["mcp"] else 0,
        "mcp": extensions_runtime_service.get_mcp_health_summary() if service_flags["mcp"] else {"status": "disabled"},
        "skillsStartupState": skills_status.get("startupState"),
        "extensionsStartupState": extensions_status.get("startupState"),
        "mcpStartupState": mcp_status.get("startupState"),
        "skillsRuntime": skills_status,
        "extensionsRuntime": extensions_status,
        "mcpRuntime": mcp_status,
        "engineRuntime": inspect_engine_runtime(),
        "lifespan": dict(getattr(app.state, "lifespan_diagnostics", {}) or {}),
        "configBrokerRecovery": dict(getattr(app.state, "config_broker_recovery", {}) or {}),
        "runtimeEpisodeRunner": runner_status,
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
