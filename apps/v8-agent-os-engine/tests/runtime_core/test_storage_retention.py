from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from core.database import DatabaseManager
from erc.checkpoint_security import build_checkpoint_serializer
from erc.checkpoint_store import CheckpointStore
from langgraph.checkpoint.base import empty_checkpoint
from core.observability_db import ObservabilityDatabaseManager
from core.storage_retention import StorageRetentionService
import core.storage_retention as storage_retention_module


def _create_checkpoint_tables(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))")
    conn.execute("CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))")


def _insert_checkpoint(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    checkpoint_id: str,
    parent_checkpoint_id: str | None,
    channel_values: dict | None = None,
) -> None:
    serializer = build_checkpoint_serializer()
    checkpoint = empty_checkpoint()
    checkpoint["id"] = checkpoint_id
    checkpoint["channel_values"] = dict(channel_values or {"messages": []})
    serialization_type, blob = serializer.dumps_typed(checkpoint)
    conn.execute(
        "INSERT INTO checkpoints VALUES (?, '', ?, ?, ?, ?, ?)",
        (thread_id, checkpoint_id, parent_checkpoint_id, serialization_type, blob, b"{}"),
    )


def _insert_write(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    checkpoint_id: str,
    task_id: str,
    channel: str,
    value: object,
) -> None:
    serialization_type, blob = build_checkpoint_serializer().dumps_typed(value)
    conn.execute(
        "INSERT INTO writes VALUES (?, '', ?, ?, 0, ?, ?, ?)",
        (thread_id, checkpoint_id, task_id, channel, serialization_type, blob),
    )


def _patch_retention_paths(monkeypatch, root: Path) -> ObservabilityDatabaseManager:
    state_path = root / "state.db"
    checkpoint_path = root / "checkpoints.db"
    observability_path = root / "observability.db"
    monkeypatch.setattr(storage_retention_module, "STATE_DB_PATH", state_path)
    monkeypatch.setattr(storage_retention_module, "CHECKPOINT_DB_PATH", checkpoint_path)
    monkeypatch.setattr(storage_retention_module, "OBSERVABILITY_DB_PATH", observability_path)
    monkeypatch.setattr(storage_retention_module, "PLUGIN_MANAGER_LOG_ROOT", root / "logs" / "plugins")
    monkeypatch.setattr(storage_retention_module, "RUNTIME_DATA_HOME", root / "runtime-data")
    monkeypatch.setattr(storage_retention_module, "V8_AGENT_OS_HOME", root)
    obs = ObservabilityDatabaseManager(observability_path)
    monkeypatch.setattr(storage_retention_module, "observability_db", obs)
    return obs


def _make_service(log_budget_bytes: int) -> StorageRetentionService:
    service = StorageRetentionService()
    service.get_config = lambda: {
        "version": 2,
        "enabled": True,
        "policy": "disk_watermark",
        "protectUserVisibleTranscript": True,
        "diskWatermarks": {
            "warningRatio": 0.15,
            "criticalRatio": 0.10,
            "emergencyRatio": 0.05,
            "emergencyFreeBytes": 2 * 1024 * 1024 * 1024,
        },
        "budgets": {
            "logs": {"maxBytes": log_budget_bytes, "mode": "rolling"},
            "checkpoints": {"maxBytes": 4 * 1024 * 1024 * 1024, "mode": "elastic"},
        },
    }
    return service


def test_storage_retention_stats_separates_logs_from_checkpoints(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        checkpoint_path = root / "checkpoints.db"
        checkpoint_path.write_bytes(b"x" * (2 * 1024 * 1024))
        log_root = root / "logs" / "plugins"
        log_root.mkdir(parents=True)
        (log_root / "demo.log").write_text("hello", encoding="utf-8")
        service = StorageRetentionService()
        service.get_config = lambda: {
            "version": 2,
            "enabled": True,
            "policy": "disk_watermark",
            "protectUserVisibleTranscript": True,
            "diskWatermarks": {
                "warningRatio": 0.15,
                "criticalRatio": 0.10,
                "emergencyRatio": 0.05,
                "emergencyFreeBytes": 2 * 1024 * 1024 * 1024,
            },
            "budgets": {
                "logs": {"maxBytes": 1024 * 1024 * 1024, "mode": "rolling"},
                "checkpoints": {"maxBytes": 4 * 1024 * 1024 * 1024, "mode": "elastic"},
            },
        }

        stats = service.build_stats()

        assert "checkpoints" in stats["budgetComponents"]
        assert stats["budgetComponents"]["logs"]["usedBytes"] < stats["budgetComponents"]["checkpoints"]["usedBytes"]
        assert stats["budgetComponents"]["checkpoints"]["usedBytes"] >= 2 * 1024 * 1024
        assert stats["policy"] == "disk_watermark"
        assert "maxBytes" not in stats
        assert "overCapBytes" not in stats


def test_startup_pressure_only_auto_applies_registry_disposable_plan(monkeypatch, tmp_path: Path) -> None:
    _patch_retention_paths(monkeypatch, tmp_path)
    service = _make_service(1024 * 1024 * 1024)
    disk = {
        "totalBytes": 1000,
        "freeBytes": 80,
        "freeRatio": 0.08,
        "watermark": "critical",
        "warningRatio": 0.15,
        "criticalRatio": 0.10,
        "emergencyRatio": 0.05,
        "emergencyFreeBytes": 20,
        "emergencySafeMode": False,
    }
    calls: dict[str, object] = {}
    monkeypatch.setattr(service, "_disk_health", lambda: disk)

    def _build_plan(*, home: Path, pressure_bytes: int = 0, **_kwargs):
        calls["home"] = home
        calls["pressureBytes"] = pressure_bytes
        return {
            "planDigest": "pressure-plan",
            "actions": [{"entryId": "cache", "path": str(tmp_path / "cache.bin"), "bytes": 70}],
            "candidateBytes": 70,
            "candidateFiles": 1,
            "pressureTargetBytes": pressure_bytes,
        }

    monkeypatch.setattr(storage_retention_module.storage_registry_service, "build_cleanup_plan", _build_plan)
    monkeypatch.setattr(
        storage_retention_module.storage_registry_service,
        "apply_cleanup_plan",
        lambda **_kwargs: {"status": "completed", "removedFiles": 1, "removedBytes": 70},
    )
    monkeypatch.setattr(
        service,
        "_execute_retention",
        lambda **kwargs: {"status": "dry_run", "registryPlanDigest": kwargs["registry_plan"]["planDigest"]},
    )

    result = service.startup_check()

    assert calls["home"] == tmp_path
    assert calls["pressureBytes"] == 70
    assert result["status"] == "auto_cleaned"
    assert result["automaticCleanup"]["scope"] == "derived_cache_test_owned_files"
    assert result["automaticCleanup"]["triggerWatermark"] == "critical"
    assert result["automaticCleanup"]["auditState"] == "recorded"


def test_retention_migrates_legacy_logs_and_clears_state(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        obs = _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_invocation_logs (
                    id, model_id, status, input_tokens, output_tokens, total_tokens
                ) VALUES ('legacy-invocation', 'demo-model', 'completed', 1, 2, 3)
                """
            )
            conn.execute(
                "INSERT INTO system_audit_log (id, source_type, action, status, details) VALUES ('audit-1', 'TEST', 'run', 'ok', '{}')"
            )
            conn.commit()
        service = _make_service(200 * 1024 * 1024)

        result = service.enforce(dry_run=False, reason="unit_test")

        assert any(action["action"] == "migrate_state_table" for action in result["actions"])
        assert obs.get_recent_model_invocations(limit=5)[0]["id"] == "legacy-invocation"
        assert obs.get_audit_logs(limit=5)[0]["id"] == "audit-1"
        with db.get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM model_invocation_logs").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM system_audit_log").fetchone()[0] == 0


def test_retention_prune_preserves_user_visible_messages(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("s1", "demo", user_id="user")
        db.create_run_record("run-retention", "s1", run_type="chat", status="completed")
        db.add_message("m1", "s1", "user", "hello")
        db.create_chat_canonical_message(
            message_id="cm1",
            session_id="s1",
            run_id=None,
            ordinal=1,
            role="assistant",
            state="finalized",
            nodes=[{"type": "text", "text": "visible"}],
            content_text="visible",
        )
        for index in range(6):
            db.add_runtime_snapshot(
                f"snapshot-{index}",
                "s1",
                "run-retention",
                index,
                "chat_projection",
                {"payload": "x" * 4096},
            )
        service = _make_service(1)

        result = service.enforce(dry_run=False, reason="unit_test")

        assert result["protected"]["messages"] is True
        with db.get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM chat_canonical_messages").fetchone()[0] == 1


def test_add_message_is_idempotent_for_canonical_projection_updates():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DatabaseManager(Path(temp_dir) / "state.db")
        db.create_or_update_session("s1", "demo", user_id="user")

        db.add_message("assistant-1", "s1", "assistant", "draft", metadata={"run_id": "run-1"})
        db.add_message(
            "assistant-1",
            "s1",
            "assistant",
            "final",
            reasoning_content="reasoned",
            tool_calls=[{"name": "spec_broker"}],
            metadata={"run_id": "run-1", "finalized": True},
        )

        rows = db.get_messages("s1")
        assert len(rows) == 1
        assert rows[0]["id"] == "assistant-1"
        assert rows[0]["content"] == "final"
        assert rows[0]["reasoning_content"] == "reasoned"
        assert rows[0]["tool_calls"] == [{"name": "spec_broker"}]
        assert rows[0]["metadata"]["finalized"] is True


def test_run_transition_to_non_terminal_clears_terminal_error_state():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DatabaseManager(Path(temp_dir) / "state.db")
        db.create_or_update_session("s1", "demo", user_id="user")
        db.create_run_record("run-1", "s1", run_type="chat", status="running")

        db.update_run_record("run-1", status="failed", error_message="spec_next_stage_not_created")
        failed = db.get_run_record("run-1")
        assert failed["status"] == "failed"
        assert failed["error_message"] == "spec_next_stage_not_created"
        assert failed["finished_at"]

        db.update_run_record("run-1", status="running")
        resumed = db.get_run_record("run-1")
        assert resumed["status"] == "running"
        assert resumed["error_message"] is None
        assert resumed["finished_at"] is None


def test_terminal_run_transition_cancels_only_its_pending_human_actions():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DatabaseManager(Path(temp_dir) / "state.db")
        db.create_or_update_session("s1", "demo", user_id="user")
        db.create_run_record("run-terminal", "s1", run_type="chat", status="running")
        db.create_run_record("run-other", "s1", run_type="chat", status="running")
        db.add_pending_approval(
            "approval-terminal",
            "s1",
            "run-terminal",
            "spec_stage_approval",
            "pending",
            {"stage": "requirements"},
        )
        db.add_pending_approval(
            "approval-other",
            "s1",
            "run-other",
            "spec_stage_approval",
            "pending",
            {"stage": "design"},
        )
        db.add_ask_user_interaction(
            interaction_id="ask-terminal",
            session_id="s1",
            run_id="run-terminal",
            question="需要确认",
            status="pending",
        )
        db.add_ask_user_interaction(
            interaction_id="ask-other",
            session_id="s1",
            run_id="run-other",
            question="另一个运行的问题",
            status="pending",
        )

        db.update_run_record("run-terminal", status="cancelled")

        assert db.get_pending_approval("approval-terminal")["status"] == "cancelled"
        assert db.get_ask_user_interaction("ask-terminal")["status"] == "cancelled"
        assert db.get_pending_approval("approval-other")["status"] == "pending"
        assert db.get_ask_user_interaction("ask-other")["status"] == "pending"


def test_completed_run_transition_does_not_erase_pending_governance():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DatabaseManager(Path(temp_dir) / "state.db")
        db.create_or_update_session("s1", "demo", user_id="user")
        db.create_run_record("run-completed", "s1", run_type="chat", status="running")
        db.add_pending_approval(
            "approval-completed",
            "s1",
            "run-completed",
            "spec_stage_approval",
            "pending",
            {"stage": "requirements"},
        )

        db.update_run_record("run-completed", status="completed")

        assert db.get_pending_approval("approval-completed")["status"] == "pending"


def test_retention_prunes_old_checkpoints_but_keeps_active_and_idle_latest(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("active-session", "active", user_id="user")
        db.create_or_update_session("old-session", "old", user_id="user")
        db.create_run_record(
            "active-run",
            "active-session",
            thread_id="active-thread",
            run_type="chat",
            status="running",
        )
        db.create_run_record(
            "old-run",
            "old-session",
            thread_id="old-thread",
            run_type="chat",
            status="completed",
        )
        checkpoint_path = root / "checkpoints.db"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            _create_checkpoint_tables(conn)
            _insert_checkpoint(conn, thread_id="active-thread", checkpoint_id="001", parent_checkpoint_id=None)
            _insert_checkpoint(conn, thread_id="old-thread", checkpoint_id="001", parent_checkpoint_id=None)
            _insert_checkpoint(conn, thread_id="old-thread", checkpoint_id="002", parent_checkpoint_id="001")
            _insert_write(conn, thread_id="old-thread", checkpoint_id="001", task_id="task", channel="messages", value=[])
            _insert_write(conn, thread_id="old-thread", checkpoint_id="002", task_id="task", channel="messages", value=[])
            conn.commit()
        service = _make_service(1)

        result = service.enforce(dry_run=False, reason="unit_test")

        assert any(action["action"] == "prune_old_checkpoints" for action in result["actions"])
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            threads = {row[0] for row in conn.execute("SELECT thread_id FROM checkpoints").fetchall()}
            assert "active-thread" in threads
            assert "old-thread" in threads
            old_rows = conn.execute(
                "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints WHERE thread_id = 'old-thread'"
            ).fetchall()
            assert old_rows == [("002", None)]
            assert conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id = 'old-thread'").fetchone()[0] == 1


def test_checkpoint_lifecycle_pruning_runs_even_when_storage_is_below_budget(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("idle-session", "idle", user_id="user")
        checkpoint_path = root / "checkpoints.db"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            _create_checkpoint_tables(conn)
            _insert_checkpoint(conn, thread_id="idle-session", checkpoint_id="001", parent_checkpoint_id=None)
            _insert_checkpoint(conn, thread_id="idle-session", checkpoint_id="002", parent_checkpoint_id="001")
            conn.commit()
        service = _make_service(10 * 1024 * 1024)

        dry_run = service.enforce(dry_run=True, reason="below_budget_plan")
        assert next(action for action in dry_run["actions"] if action["action"] == "prune_old_checkpoints")[
            "checkpoints"
        ] == 1

        result = service.enforce(dry_run=False, reason="below_budget_apply")
        assert any(action["action"] == "prune_old_checkpoints" for action in result["actions"])
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            assert conn.execute("SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints").fetchall() == [
                ("002", None)
            ]


def test_retention_keeps_langgraph_latest_checkpoint_resumable(monkeypatch):
    import asyncio

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("resume-session", "resume", user_id="user")
        db.create_run_record(
            "resume-run",
            "resume-session",
            thread_id="resume-session",
            run_type="chat",
            status="completed",
        )
        checkpoint_path = root / "checkpoints.db"

        async def seed() -> None:
            store = CheckpointStore(checkpoint_path)
            saver = await store.get_async_sqlite_saver()
            config = {"configurable": {"thread_id": "resume-session", "checkpoint_ns": ""}}
            first = empty_checkpoint()
            first["channel_values"] = {"resume_marker": "old"}
            config = await saver.aput(config, first, {}, {})
            latest = empty_checkpoint()
            latest["channel_values"] = {"resume_marker": "latest"}
            await saver.aput(config, latest, {}, {})
            await store.close()

        asyncio.run(seed())
        service = _make_service(1)
        result = service.enforce(dry_run=False, reason="resume_test")
        assert any(action["action"] == "prune_old_checkpoints" for action in result["actions"])

        async def resume() -> str:
            store = CheckpointStore(checkpoint_path)
            saver = await store.get_async_sqlite_saver()
            checkpoint_tuple = await saver.aget_tuple(
                {"configurable": {"thread_id": "resume-session", "checkpoint_ns": ""}}
            )
            await store.close()
            assert checkpoint_tuple is not None
            return str(checkpoint_tuple.checkpoint["channel_values"]["resume_marker"])

        assert asyncio.run(resume()) == "latest"


def test_waiting_session_keeps_bounded_recovery_tail(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("waiting-session", "waiting", user_id="user")
        db.create_run_record(
            "waiting-run",
            "waiting-session",
            thread_id="waiting-session",
            run_type="chat",
            status="waiting_approval",
        )
        checkpoint_path = root / "checkpoints.db"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            _create_checkpoint_tables(conn)
            for index in range(12):
                checkpoint_id = f"{index:03d}"
                parent = f"{index - 1:03d}" if index else None
                _insert_checkpoint(
                    conn,
                    thread_id="waiting-session",
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=parent,
                )
            conn.commit()
        result = _make_service(1).enforce(dry_run=False, reason="waiting_tail_test")
        assert any(action["action"] == "prune_old_checkpoints" for action in result["actions"])
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            rows = conn.execute(
                "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints WHERE thread_id = 'waiting-session' ORDER BY checkpoint_id"
            ).fetchall()
        assert len(rows) == 8
        assert rows[-1][0] == "011"
        assert rows[0][1] is None


def test_retention_backup_failure_blocks_checkpoint_mutation(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("idle-session", "idle", user_id="user")
        checkpoint_path = root / "checkpoints.db"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            _create_checkpoint_tables(conn)
            _insert_checkpoint(conn, thread_id="idle-session", checkpoint_id="001", parent_checkpoint_id=None)
            _insert_checkpoint(conn, thread_id="idle-session", checkpoint_id="002", parent_checkpoint_id="001")
            conn.commit()

        def fail_backup(*_args, **_kwargs):
            raise RuntimeError("simulated backup failure")

        monkeypatch.setattr(storage_retention_module.StorageBackupService, "create_backup", fail_backup)
        result = _make_service(1).enforce(dry_run=False, reason="backup_failure_test")

        assert result["status"] == "blocked"
        assert result["errorCode"] == "backup_failed"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2


def test_emergency_safe_mode_never_prunes_checkpoints(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        db = DatabaseManager(root / "state.db")
        db.create_or_update_session("idle-session", "idle", user_id="user")
        checkpoint_path = root / "checkpoints.db"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            _create_checkpoint_tables(conn)
            _insert_checkpoint(conn, thread_id="idle-session", checkpoint_id="001", parent_checkpoint_id=None)
            _insert_checkpoint(conn, thread_id="idle-session", checkpoint_id="002", parent_checkpoint_id="001")
            conn.commit()
        service = _make_service(1)
        monkeypatch.setattr(
            service,
            "_disk_health",
            lambda: {"totalBytes": 100, "freeBytes": 1, "freeRatio": 0.01, "emergencySafeMode": True},
        )

        result = service.enforce(dry_run=False, reason="low_disk_test")

        assert result["status"] == "blocked"
        assert result["errorCode"] == "emergency_safe_mode"
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2


def test_startup_only_marks_interrupted_retention_for_recovery(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _patch_retention_paths(monkeypatch, root)
        service = _make_service(1)
        service._write_journal({"operationId": "interrupted", "state": "applying"})

        result = service.startup_check()

        assert result["status"] == "planned"
        assert result["journal"]["state"] == "recovery_required"
        assert service._journal()["state"] == "recovery_required"

