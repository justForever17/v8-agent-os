from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.database import DATABASE_SCHEMA_VERSION, DatabaseManager


def _schema_version(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0] if row else 0)


def test_new_database_is_initialized_and_versioned(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    manager = DatabaseManager(path)

    assert _schema_version(path) == DATABASE_SCHEMA_VERSION
    with manager.get_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runtime_episode_idempotency'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runtime_side_effect_receipts'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_canvas_graph_run_event_outbox'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_canvas_graph_remote_terminal_receipts'"
        ).fetchone()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"


def test_unversioned_legacy_database_runs_idempotent_upgrade_once(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                user_id TEXT NOT NULL,
                agent_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        conn.execute("PRAGMA user_version = 0")

    DatabaseManager(path)
    DatabaseManager(path)

    assert _schema_version(path) == DATABASE_SCHEMA_VERSION
    with sqlite3.connect(path) as conn:
        message_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert {
            "reasoning_content",
            "images",
            "metadata_json",
            "agent_id",
            "agent_name",
            "agent_avatar",
            "agent_role_label",
        }.issubset(message_columns)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runtime_episodes'"
        ).fetchone()


def test_version_one_database_upgrades_runtime_safety_ledgers(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 1")

    DatabaseManager(path)

    assert _schema_version(path) == DATABASE_SCHEMA_VERSION
    with sqlite3.connect(path) as conn:
        table_names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "runtime_episode_idempotency",
        "runtime_side_effect_receipts",
        "creative_canvas_graph_run_event_outbox",
        "creative_canvas_graph_remote_terminal_receipts",
    }.issubset(table_names)


def test_current_schema_self_heals_missing_runtime_safety_ledgers(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")

    DatabaseManager(path)
    DatabaseManager(path)

    with sqlite3.connect(path) as conn:
        table_names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "runtime_episode_idempotency",
        "runtime_side_effect_receipts",
        "creative_canvas_graph_run_event_outbox",
        "creative_canvas_graph_remote_terminal_receipts",
    }.issubset(table_names)


def test_current_schema_probe_only_rechecks_runtime_safety_ledgers(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    DatabaseManager(path)

    class TracingDatabaseManager(DatabaseManager):
        def __init__(self, db_path: Path):
            self.schema_statements: list[str] = []
            super().__init__(db_path)

        @contextmanager
        def get_connection(self):
            with super().get_connection() as conn:
                conn.set_trace_callback(self.schema_statements.append)
                yield conn

    manager = TracingDatabaseManager(path)
    normalized = [statement.strip().upper() for statement in manager.schema_statements]

    assert normalized[0] == "PRAGMA USER_VERSION"
    assert any("CREATE TABLE IF NOT EXISTS RUNTIME_EPISODE_IDEMPOTENCY" in statement for statement in normalized)
    assert any("CREATE TABLE IF NOT EXISTS RUNTIME_SIDE_EFFECT_RECEIPTS" in statement for statement in normalized)
    assert any("CREATE TABLE IF NOT EXISTS CREATIVE_CANVAS_GRAPH_RUN_EVENT_OUTBOX" in statement for statement in normalized)
    assert any(
        "CREATE TABLE IF NOT EXISTS CREATIVE_CANVAS_GRAPH_REMOTE_TERMINAL_RECEIPTS" in statement
        for statement in normalized
    )
    assert not any(statement.startswith("ALTER TABLE") for statement in normalized)


def test_failed_legacy_migration_is_not_marked_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.db"

    def fail_backfill(self, conn) -> None:  # noqa: ANN001, ARG001
        raise sqlite3.OperationalError("simulated migration failure")

    monkeypatch.setattr(
        DatabaseManager,
        "_backfill_internal_computer_use_probe_sessions",
        fail_backfill,
    )

    DatabaseManager(path)

    assert _schema_version(path) == 0


def test_newer_schema_is_not_opened_by_older_binary(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeError, match="newer than supported"):
        DatabaseManager(path)

    assert _schema_version(path) == DATABASE_SCHEMA_VERSION + 1
