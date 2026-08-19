from __future__ import annotations

import sqlite3

import pytest

from core.database import DatabaseManager


def _corrupt_relay_index(path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = ? WHERE type = 'index' AND name = ?",
            (
                "CREATE INDEX idx_network_relay_outbox_message "
                "ON network_relay_outbox ()",
                "idx_network_relay_outbox_message",
            ),
        )
        conn.execute("PRAGMA writable_schema = OFF")
        conn.commit()


def test_known_malformed_relay_index_is_backed_up_and_repaired(tmp_path):
    database_path = tmp_path / "state.db"
    DatabaseManager(database_path)
    _corrupt_relay_index(database_path)

    DatabaseManager(database_path)

    with sqlite3.connect(database_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_network_relay_outbox_message",),
        ).fetchone()
        assert row is not None
        assert "local_message_id" in str(row[0])
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    backups = list(tmp_path.glob("state.db.schema-repair-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        conn.execute("PRAGMA writable_schema = ON")
        raw_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_network_relay_outbox_message'"
        ).fetchone()[0]
    assert "ON network_relay_outbox ()" in raw_sql


def test_unknown_malformed_index_remains_fail_closed(tmp_path):
    database_path = tmp_path / "state.db"
    DatabaseManager(database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = ? WHERE type = 'index' AND name = ?",
            (
                "CREATE INDEX idx_unrelated_bad ON sessions ()",
                "idx_sessions_user_id",
            ),
        )
        conn.execute("PRAGMA writable_schema = OFF")
        conn.commit()

    with pytest.raises(sqlite3.DatabaseError, match="malformed database schema"):
        DatabaseManager(database_path)
