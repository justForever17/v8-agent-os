from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from core.database import DatabaseManager
from core.observability_db import ObservabilityDatabaseManager
from core.storage_retention import StorageRetentionService
import core.storage_retention as storage_retention_module


def _patch_retention_paths(monkeypatch, root: Path) -> ObservabilityDatabaseManager:
    state_path = root / "state.db"
    checkpoint_path = root / "checkpoints.db"
    observability_path = root / "observability.db"
    monkeypatch.setattr(storage_retention_module, "STATE_DB_PATH", state_path)
    monkeypatch.setattr(storage_retention_module, "CHECKPOINT_DB_PATH", checkpoint_path)
    monkeypatch.setattr(storage_retention_module, "OBSERVABILITY_DB_PATH", observability_path)
    monkeypatch.setattr(storage_retention_module, "PLUGIN_INSTALL_LOG_ROOT", root / "logs" / "plugins")
    monkeypatch.setattr(storage_retention_module, "RUNTIME_DATA_HOME", root / "runtime-data")
    monkeypatch.setattr(storage_retention_module, "V8_AGENT_OS_HOME", root)
    obs = ObservabilityDatabaseManager(observability_path)
    monkeypatch.setattr(storage_retention_module, "observability_db", obs)
    return obs


def _make_service(max_bytes: int) -> StorageRetentionService:
    service = StorageRetentionService()
    service.get_config = lambda: {
        "version": 1,
        "enabled": True,
        "maxBytes": max_bytes,
        "mode": "hard_rolling",
        "protectUserVisibleTranscript": True,
    }
    return service


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
            db.add_runtime_snapshot(f"snapshot-{index}", "s1", None, index, "chat_projection", {"payload": "x" * 4096})
        service = _make_service(1)

        result = service.enforce(dry_run=False, reason="unit_test")

        assert result["protected"]["messages"] is True
        with db.get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM chat_canonical_messages").fetchone()[0] == 1


def test_retention_prunes_old_checkpoints_but_keeps_active_thread(monkeypatch):
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
            conn.execute("CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))")
            conn.execute("CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))")
            conn.execute("INSERT INTO checkpoints VALUES ('active-thread', '', '001', NULL, 'msgpack', zeroblob(1024), zeroblob(10))")
            conn.execute("INSERT INTO checkpoints VALUES ('old-thread', '', '001', NULL, 'msgpack', zeroblob(1024), zeroblob(10))")
            conn.execute("INSERT INTO writes VALUES ('old-thread', '', '001', 'task', 0, 'messages', 'msgpack', zeroblob(1024))")
            conn.commit()
        service = _make_service(1)

        result = service.enforce(dry_run=False, reason="unit_test")

        assert any(action["action"] == "prune_old_checkpoints" for action in result["actions"])
        with closing(sqlite3.connect(checkpoint_path)) as conn:
            threads = {row[0] for row in conn.execute("SELECT thread_id FROM checkpoints").fetchall()}
            assert "active-thread" in threads
            assert "old-thread" not in threads

