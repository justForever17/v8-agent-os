"""SQLite owner for external-tool waits, independent of Network discovery JSON.

The first table creation and legacy import are one transaction. Once the table
exists, JSON is never a pending-state authority again, even if an old writer
reintroduces a stale copy. Consumed rows remain tombstones for duplicate replies.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any


class NetworkCompatPendingStore:
    def __init__(self, database: Any, legacy_path: Path):
        self.database = database
        self.legacy_path = legacy_path

    def _ensure_table(self, conn: Any) -> None:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='network_compat_pending_tools'").fetchone():
            if "result_json" not in {row["name"] for row in conn.execute("PRAGMA table_info(network_compat_pending_tools)")}:
                conn.execute("ALTER TABLE network_compat_pending_tools ADD COLUMN result_json TEXT")
                for row in conn.execute("SELECT id,payload_json FROM network_compat_pending_tools").fetchall():
                    metadata, result = self._separate_receipt(json.loads(row["payload_json"]))
                    conn.execute("UPDATE network_compat_pending_tools SET payload_json=?,result_json=? WHERE id=?",
                        (json.dumps(metadata, ensure_ascii=False), json.dumps(result, ensure_ascii=False) if result is not None else None, row["id"]))
            return
        # Read before creating the marker. Corrupt/inaccessible legacy data must
        # fail visibly, not be replaced by an empty successful migration.
        legacy = json.loads(self.legacy_path.read_text(encoding="utf-8")) if self.legacy_path.exists() else {}
        pending = legacy.get("pendingExternalTools") or {}
        if not isinstance(pending, dict) or any(not isinstance(row, dict) for row in pending.values()):
            raise ValueError("network_compat_legacy_pending_invalid")
        conn.execute("CREATE TABLE network_compat_pending_tools (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT)")
        for key, row in pending.items():
            metadata, result = self._separate_receipt(row)
            conn.execute("INSERT INTO network_compat_pending_tools(id,payload_json,result_json) VALUES (?,?,?)",
                (str(key), json.dumps(metadata, ensure_ascii=False), json.dumps(result, ensure_ascii=False) if result is not None else None))

    @staticmethod
    def _separate_receipt(item: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        metadata = dict(item)
        result = metadata.pop("toolResultReceipt", None)
        if result is not None:
            metadata["resultStored"] = True
        return metadata, result

    def ensure_migrated(self) -> None:
        with self.database.get_connection() as conn:
            if "result_json" in {row["name"] for row in conn.execute("PRAGMA table_info(network_compat_pending_tools)")}:
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_table(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def transaction(self):
        with self.database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_table(conn)
                before = {row["id"]: row["payload_json"] for row in conn.execute("SELECT id,payload_json FROM network_compat_pending_tools")}
                pending = {key: json.loads(value) for key, value in before.items()}
                yield pending
                for key, value in pending.items():
                    metadata, result = self._separate_receipt(value)
                    encoded = json.dumps(metadata, ensure_ascii=False)
                    if result is not None:
                        conn.execute("INSERT INTO network_compat_pending_tools(id,payload_json,result_json) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,result_json=excluded.result_json",
                            (key, encoded, json.dumps(result, ensure_ascii=False)))
                    elif before.get(key) != encoded:
                        conn.execute("INSERT INTO network_compat_pending_tools(id,payload_json) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json", (key, encoded))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def snapshot(self) -> dict[str, dict[str, Any]]:
        self.ensure_migrated()
        with self.database.get_connection() as conn:
            return {row["id"]: json.loads(row["payload_json"])
                    for row in conn.execute("SELECT id,payload_json FROM network_compat_pending_tools")}

    def receipt(self, receipt_id: str) -> dict[str, Any] | None:
        """Read one full result only after the caller resolves its owned receipt."""
        self.ensure_migrated()
        with self.database.get_connection() as conn:
            row = conn.execute("SELECT result_json FROM network_compat_pending_tools WHERE id=?", (receipt_id,)).fetchone()
            return json.loads(row["result_json"]) if row and row["result_json"] is not None else None
