from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


def test_creative_media_reconciler_replays_terminal_proof_after_ack_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.creative_canvas_graph as graph_module
    import main
    import runtimes.creative_media.runtime as creative_runtime_module

    async def exercise() -> None:
        calls: list[tuple[object, ...]] = []
        captured: dict[str, object] = {}
        proof = {
            "schema": "v8.creative_media_remote_terminal_proof.v1",
            "source": "provider_status_api",
            "providerStatus": "cancelled",
            "observedAt": "2026-08-16T00:00:00Z",
        }
        report = {
            "jobId": "job-a",
            "remoteTaskMayContinue": False,
            "projectionPending": True,
            "terminalProof": proof,
        }

        class FakeGraphService:
            def __init__(self) -> None:
                self.applied = False
                self.advanced = False
                self.transition_count = 0
                self.received_proofs: list[dict] = []

            def apply_remote_terminal_reconciliation(self, candidate: dict) -> dict:
                self.received_proofs.append(dict(candidate["terminalProof"]))
                status = "already_applied" if self.applied else "applied"
                calls.append(("project", status, self.advanced))
                if not self.applied:
                    self.applied = True
                    self.transition_count += 1
                return {"status": status}

            def advance_after_retry(self) -> None:
                assert self.applied is True
                self.advanced = True
                calls.append(("advance", "retry"))

        class FakeRuntime:
            def __init__(self) -> None:
                self.pending = True
                self.ack_attempts = 0

            def list_remote_reconcile_reports(self, **filters) -> list[dict]:
                assert filters == {"remote_task_may_continue": False, "projection_pending": True}
                calls.append(("scan", self.pending))
                return [dict(report)] if self.pending else []

            def mark_remote_reconcile_projected(
                self,
                job_id: str,
                terminal_proof: dict,
                *,
                projection_error: str = "",
            ) -> dict:
                assert job_id == "job-a"
                assert terminal_proof == proof
                assert projection_error == ""
                self.ack_attempts += 1
                calls.append(("ack", self.ack_attempts))
                if self.ack_attempts == 1:
                    raise OSError("simulated acknowledgement loss")
                self.pending = False
                report["projectionPending"] = False
                report["projectedAt"] = "2026-08-16T00:00:01Z"
                return dict(report)

            def start_remote_reconciler(self, **kwargs) -> asyncio.Task:
                captured.update(kwargs)
                return asyncio.create_task(asyncio.sleep(0))

        fake_graph = FakeGraphService()
        fake_runtime = FakeRuntime()
        monkeypatch.setattr(graph_module, "creative_canvas_graph_service", fake_graph)
        monkeypatch.setattr(creative_runtime_module, "creative_media_runtime", fake_runtime)

        task = await main._start_creative_media_remote_reconciler([{"jobId": "job-a"}])
        callback = captured["on_cycle"]
        assert callable(callback)
        await callback({"checked": 0})
        assert fake_runtime.pending is True
        assert fake_graph.transition_count == 1

        fake_graph.advance_after_retry()
        await callback({"checked": 0})
        assert fake_runtime.pending is False
        assert report["projectedAt"] == "2026-08-16T00:00:01Z"

        await callback({"checked": 0})
        await task

        assert captured["recovery_candidates"] == [{"jobId": "job-a"}]
        assert calls == [
            ("scan", True),
            ("project", "applied", False),
            ("ack", 1),
            ("advance", "retry"),
            ("scan", True),
            ("project", "already_applied", True),
            ("ack", 2),
            ("scan", False),
        ]
        assert fake_graph.received_proofs == [proof, proof]
        assert fake_graph.transition_count == 1

    asyncio.run(exercise())


def test_canvas_outbox_repair_loop_runs_independently_and_is_cancellable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.creative_canvas_graph as graph_module
    import main

    async def exercise() -> None:
        repaired = asyncio.Event()
        calls: list[int] = []

        class FakeGraphService:
            def repair_graph_run_state_outbox(self, *, limit: int) -> dict:
                calls.append(limit)
                repaired.set()
                return {"projected": 1}

        monkeypatch.setattr(graph_module, "creative_canvas_graph_service", FakeGraphService())
        task = asyncio.create_task(main._run_creative_canvas_outbox_repair_loop(interval_seconds=0.1))
        await asyncio.wait_for(repaired.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == [2048]

    asyncio.run(exercise())


def test_lifespan_cleanup_stops_services_and_tasks_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    async def exercise() -> None:
        events: list[str] = []

        class AsyncService:
            def __init__(self, label: str) -> None:
                self.label = label

            async def stop(self) -> None:
                events.append(f"{self.label}.stop")

        class CronService:
            def shutdown(self) -> None:
                events.append("cron.shutdown")

        monkeypatch.setattr(main, "_get_runtime_episode_runner", lambda: AsyncService("runner"))
        monkeypatch.setattr(main, "_get_network_relay_worker_service", lambda: AsyncService("relay"))
        monkeypatch.setattr(main, "_get_network_neighbor_service", lambda: AsyncService("neighbor"))
        monkeypatch.setattr(main, "_get_network_supervisor_service", lambda: AsyncService("supervisor"))
        monkeypatch.setattr(main, "_get_cron_manager", lambda: CronService())
        monkeypatch.setattr(main, "_get_extensions_runtime_service", lambda: AsyncService("extensions"))
        monkeypatch.setattr(main, "_get_chat_run_scheduler", lambda: AsyncService("chat_scheduler"))

        async def cleanup_mcp() -> None:
            events.append("mcp.cleanup")

        async def cleanup_ui_patch() -> None:
            events.append("ui_patch.shutdown")

        async def close_checkpoints() -> None:
            events.append("checkpoint_store.close")

        monkeypatch.setattr(main, "_safe_cleanup", cleanup_mcp)
        monkeypatch.setattr(main, "_shutdown_ui_patch_preview", cleanup_ui_patch)
        monkeypatch.setattr(main, "_safe_close_checkpoints", close_checkpoints)

        async def pending(label: str) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                events.append(f"task.cancel:{label}")

        app = _app()
        state = main._new_lifespan_state(app)
        for service in (
            "checkpoint_store",
            "chat_scheduler",
            "mcp",
            "extensions",
            "cron",
            "network_supervisor",
            "network_neighbor",
            "network_relay",
            "episode_runner",
        ):
            main._mark_lifespan_service_starting(state, service)
        engineering_task = asyncio.create_task(pending("engineering"))
        mcp_task = asyncio.create_task(pending("mcp"))
        main._track_lifespan_task(app, state, "engineering_task", engineering_task)
        main._track_lifespan_task(app, state, "mcp_task", mcp_task)
        await asyncio.sleep(0)

        result = await main._shutdown_lifespan_services(app, state, reason="shutdown")

        assert events == [
            "runner.stop",
            "relay.stop",
            "neighbor.stop",
            "supervisor.stop",
            "cron.shutdown",
            "extensions.stop",
            "task.cancel:mcp",
            "task.cancel:engineering",
            "mcp.cleanup",
            "ui_patch.shutdown",
            "chat_scheduler.stop",
            "checkpoint_store.close",
        ]
        assert result["cleanupCompleted"] is True
        assert result["cleanupErrors"] == []
        assert app.state.engineering_task is None
        assert app.state.mcp_task is None

        await main._shutdown_lifespan_services(app, state, reason="duplicate")
        assert len(events) == 12
        assert state["cleanup_reason"] == "shutdown"

    asyncio.run(exercise())


def test_lifespan_cleanup_continues_after_individual_stop_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    async def exercise() -> None:
        events: list[str] = []

        class FailingRunner:
            async def stop(self) -> None:
                events.append("runner.stop")
                raise RuntimeError("runner stop failed")

        class Extensions:
            async def stop(self) -> None:
                events.append("extensions.stop")

        async def cleanup_ui_patch() -> None:
            events.append("ui_patch.shutdown")

        monkeypatch.setattr(main, "_get_runtime_episode_runner", lambda: FailingRunner())
        monkeypatch.setattr(main, "_get_extensions_runtime_service", lambda: Extensions())
        monkeypatch.setattr(main, "_shutdown_ui_patch_preview", cleanup_ui_patch)

        app = _app()
        state = main._new_lifespan_state(app)
        main._mark_lifespan_service_starting(state, "extensions")
        main._mark_lifespan_service_starting(state, "episode_runner")

        result = await main._shutdown_lifespan_services(app, state, reason="shutdown")

        assert events == ["runner.stop", "extensions.stop", "ui_patch.shutdown"]
        assert result["cleanupCompleted"] is True
        assert result["cleanupErrors"] == [
            {
                "action": "episode_runner.stop",
                "errorType": "RuntimeError",
                "message": "runner stop failed",
            }
        ]

    asyncio.run(exercise())


def test_lifespan_startup_failure_rolls_back_partial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    async def exercise() -> None:
        events: list[str] = []
        captured_task: asyncio.Task | None = None

        class Extensions:
            async def stop(self) -> None:
                events.append("extensions.stop")

        async def pending() -> None:
            await asyncio.Event().wait()

        async def fail_startup(app, state) -> None:
            nonlocal captured_task
            main._mark_lifespan_service_starting(state, "extensions")
            captured_task = asyncio.create_task(pending())
            main._track_lifespan_task(app, state, "startup_task", captured_task)
            await asyncio.sleep(0)
            raise RuntimeError("late startup failure")

        monkeypatch.setattr(main, "_start_lifespan_services", fail_startup)
        monkeypatch.setattr(main, "_get_extensions_runtime_service", lambda: Extensions())

        app = _app()
        with pytest.raises(RuntimeError, match="late startup failure"):
            async with main.lifespan(app):
                raise AssertionError("lifespan must not yield after startup failure")

        assert events == ["extensions.stop"]
        assert captured_task is not None and captured_task.cancelled()
        assert app.state.startup_task is None
        assert app.state.startup_failure == {
            "phase": "starting:extensions",
            "errorType": "RuntimeError",
            "message": "late startup failure",
        }
        assert app.state.lifespan_diagnostics["cleanupReason"] == "startup_failure"
        assert app.state.lifespan_diagnostics["cleanupCompleted"] is True

    asyncio.run(exercise())


def test_lifespan_normal_shutdown_uses_shared_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    async def exercise() -> None:
        events: list[str] = []

        class Runner:
            async def stop(self) -> None:
                events.append("runner.stop")

        async def start(_app, state) -> None:
            main._mark_lifespan_service_starting(state, "episode_runner")
            main._set_lifespan_phase(state, "ready")

        async def cleanup_ui_patch() -> None:
            events.append("ui_patch.shutdown")

        monkeypatch.setattr(main, "_start_lifespan_services", start)
        monkeypatch.setattr(main, "_get_runtime_episode_runner", lambda: Runner())
        monkeypatch.setattr(main, "_shutdown_ui_patch_preview", cleanup_ui_patch)

        app = _app()
        async with main.lifespan(app):
            events.append("serving")

        assert events == ["serving", "runner.stop", "ui_patch.shutdown"]
        assert app.state.lifespan_diagnostics["cleanupReason"] == "shutdown"

    asyncio.run(exercise())


def test_real_startup_sequence_rolls_back_when_network_relay_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.model_control_plane as model_control_plane_module
    import core.provider_continuation as provider_continuation_module
    import main

    async def exercise() -> None:
        events: list[str] = []

        class Storage:
            def migrate_legacy_local_config(self):
                return None

            def migrate_legacy_mcp_config(self):
                return None

            def ensure_legacy_model_bindings_migrated(self):
                return None

            def migrate_system_base_config(self):
                return None

            def migrate_safety_guardian_config(self):
                return None

            def ensure_memory_runtime_defaults(self):
                return []

        class CheckpointStore:
            async def ensure_preflight(self):
                events.append("checkpoint_store.preflight")
                return {
                    "policyVersion": 1,
                    "mode": "test",
                    "checkpointRows": 0,
                    "writeRows": 0,
                    "durationMs": 0,
                }

            async def close(self):
                events.append("checkpoint_store.close")

        class McpManager:
            async def initialize(self):
                await asyncio.Event().wait()

            async def cleanup(self):
                events.append("mcp.cleanup")

        class Extensions:
            async def start(self, **_kwargs):
                events.append("extensions.start")

            async def stop(self):
                events.append("extensions.stop")

        class Cron:
            def start(self):
                events.append("cron.start")

            def shutdown(self):
                events.append("cron.shutdown")

        class NetworkService:
            def __init__(self, label: str, *, fail_start: bool = False) -> None:
                self.label = label
                self.fail_start = fail_start

            async def start(self):
                events.append(f"{self.label}.start")
                if self.fail_start:
                    raise RuntimeError("relay unavailable")

            async def stop(self):
                events.append(f"{self.label}.stop")

        async def noop_async(*_args, **_kwargs):
            return None

        async def reconcile_config_broker(*_args, **_kwargs):
            events.append("config_broker.reconcile")

        monkeypatch.setattr(main, "storage", Storage())
        monkeypatch.setattr(main, "checkpoint_store", CheckpointStore())
        monkeypatch.setattr(
            model_control_plane_module,
            "model_control_plane",
            SimpleNamespace(migrate_reasoning_surfaces=lambda: []),
        )
        monkeypatch.setattr(
            provider_continuation_module,
            "migrate_persisted_provider_continuations",
            lambda _db: {"migratedRows": 0, "invalidRows": 0},
        )
        monkeypatch.setattr(main, "_reconcile_config_broker_transactions", reconcile_config_broker)
        monkeypatch.setattr(main, "_ensure_default_workflow_memories", lambda: None)
        monkeypatch.setattr(main, "_reconcile_orphaned_workflows", noop_async)
        monkeypatch.setattr(main, "_reconcile_session_lanes", noop_async)
        monkeypatch.setattr(main, "_reconcile_engineering_workspaces", noop_async)
        monkeypatch.setattr(main, "_reconcile_creative_canvas_graph_runs", noop_async)
        monkeypatch.setattr(main, "_ensure_plugin_manager_runtime_registered", lambda: None)
        monkeypatch.setattr(
            main,
            "_service_flags",
            lambda: {
                "audio": False,
                "mcp": True,
                "skills": False,
                "extensions": True,
                "cron": True,
                "network_supervisor": True,
                "desktop_live": False,
            },
        )
        monkeypatch.setattr(main, "_service_states", lambda _profile: {})
        monkeypatch.setattr(
            main,
            "inspect_engine_runtime",
            lambda: {
                "interpreterPath": "python",
                "launchMode": "test",
                "reload": False,
                "interpreterDrift": False,
                "launcherDrift": False,
                "warnings": [],
            },
        )
        monkeypatch.setattr(main, "build_installation_snapshot", lambda: {"installedRuntimeFamilies": []})
        monkeypatch.setattr(main, "startup_bundle_summary", lambda _profile: {})
        monkeypatch.setattr(main, "_get_mcp_manager", lambda: McpManager())
        extensions = Extensions()
        cron = Cron()
        supervisor = NetworkService("supervisor")
        neighbor = NetworkService("neighbor")
        relay = NetworkService("relay", fail_start=True)
        monkeypatch.setattr(main, "_get_extensions_runtime_service", lambda: extensions)
        monkeypatch.setattr(main, "_get_cron_manager", lambda: cron)
        monkeypatch.setattr(main, "_ensure_network_supervisor_runtime_registered", lambda: None)
        monkeypatch.setattr(main, "_get_network_supervisor_service", lambda: supervisor)
        monkeypatch.setattr(main, "_get_network_neighbor_service", lambda: neighbor)
        monkeypatch.setattr(main, "_get_network_relay_worker_service", lambda: relay)

        app = _app()
        with pytest.raises(RuntimeError, match="relay unavailable"):
            async with main.lifespan(app):
                raise AssertionError("lifespan must not yield")

        assert events == [
            "config_broker.reconcile",
            "checkpoint_store.preflight",
            "extensions.start",
            "cron.start",
            "supervisor.start",
            "neighbor.start",
            "relay.start",
            "relay.stop",
            "neighbor.stop",
            "supervisor.stop",
            "cron.shutdown",
            "extensions.stop",
            "mcp.cleanup",
            "checkpoint_store.close",
        ]
        assert app.state.lifespan_diagnostics["cleanupErrors"] == []
        assert app.state.lifespan_diagnostics["cleanupReason"] == "startup_failure"
        assert app.state.mcp_init_task is None
        assert app.state.storage_pressure_monitor_task is None
        assert app.state.knowledge_projection_recovery_task is None

    asyncio.run(exercise())


def test_config_broker_recovery_degraded_result_does_not_fail_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    class ConfigBroker:
        def reconcile_incomplete_transactions(self):
            return {
                "ok": False,
                "reconciled": 1,
                "transactions": [
                    {"transactionId": "private-id-1", "state": "rolled_back"},
                    {"transactionId": "private-id-2", "state": "recovery_required"},
                ],
            }

    monkeypatch.setattr(main, "_get_config_broker_service", lambda: ConfigBroker())
    app = _app()
    metrics: dict[str, object] = {}

    result = asyncio.run(main._reconcile_config_broker_transactions(app, metrics))

    assert result == {
        "ok": False,
        "reconciled": 1,
        "stateCounts": {"rolled_back": 1, "recovery_required": 1},
    }
    assert app.state.config_broker_recovery == result
    assert metrics["configBrokerRecovery"] == result
    assert "private-id" not in str(metrics)


def test_config_broker_recovery_exception_remains_startup_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    class ConfigBroker:
        def reconcile_incomplete_transactions(self):
            raise RuntimeError("transaction journal unreadable")

    monkeypatch.setattr(main, "_get_config_broker_service", lambda: ConfigBroker())

    with pytest.raises(RuntimeError, match="transaction journal unreadable"):
        asyncio.run(main._reconcile_config_broker_transactions(_app(), {}))
