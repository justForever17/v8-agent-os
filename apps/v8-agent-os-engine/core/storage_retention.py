from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List

from core.observability_db import observability_db
from core.storage import storage
from core.v8_agent_os_paths import (
    CHECKPOINT_DB_PATH,
    OBSERVABILITY_DB_PATH,
    PLUGIN_INSTALL_LOG_ROOT,
    RUNTIME_DATA_HOME,
    STATE_DB_PATH,
    V8_AGENT_OS_HOME,
)


ACTIVE_RUN_STATUSES = {"queued", "running", "waiting_approval", "waiting_input", "paused"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
LOG_FILE_SUFFIXES = {".log", ".jsonl", ".html", ".txt"}
STATE_LOG_TABLES = (
    "model_invocation_logs",
    "provider_health_logs",
    "prompt_cache_events",
    "prompt_cache_segments",
    "llm_response_cache",
    "tool_observation_records",
    "system_audit_log",
)
STATE_LOG_DELETE_TABLES = (
    "prompt_cache_segments",
    "prompt_cache_events",
    "tool_observation_records",
    "model_invocation_logs",
    "provider_health_logs",
    "llm_response_cache",
    "system_audit_log",
)


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _sqlite_family_size(path: Path) -> int:
    return sum(_file_size(candidate) for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")))


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class StorageRetentionService:
    """Central retention controller for V8OS observability and recoverability logs."""

    def get_config(self) -> Dict[str, Any]:
        return storage.get_storage_retention_config()

    def _estimate_state_log_payload_bytes(self) -> int:
        if not STATE_DB_PATH.exists():
            return 0
        total = 0
        with _connect(STATE_DB_PATH) as conn:
            for table in ("runtime_snapshots", "runtime_events", *STATE_LOG_TABLES):
                if not self._table_exists(conn, table):
                    continue
                try:
                    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                    expr = "+".join([f'COALESCE(length(CAST("{col}" AS BLOB)),0)' for col in cols])
                    row = conn.execute(f'SELECT COALESCE(SUM({expr}),0) AS bytes FROM "{table}"').fetchone()
                    total += int(row["bytes"] or 0) if row else 0
                except Exception:
                    continue
        return total

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def _log_files(self) -> List[Path]:
        roots = [PLUGIN_INSTALL_LOG_ROOT, RUNTIME_DATA_HOME / "rpa"]
        files: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for item in root.rglob("*"):
                if item.is_file() and (item.suffix.lower() in LOG_FILE_SUFFIXES or "log" in item.name.lower()):
                    files.append(item)
        return files

    def _log_files_size(self) -> int:
        return sum(_file_size(item) for item in self._log_files())

    def _governed_total_bytes(self) -> int:
        return (
            _sqlite_family_size(OBSERVABILITY_DB_PATH)
            + _sqlite_family_size(CHECKPOINT_DB_PATH)
            + self._estimate_state_log_payload_bytes()
            + self._log_files_size()
        )

    def build_stats(self) -> Dict[str, Any]:
        config = self.get_config()
        components = {
            "observabilityDbBytes": _sqlite_family_size(OBSERVABILITY_DB_PATH),
            "checkpointDbBytes": _sqlite_family_size(CHECKPOINT_DB_PATH),
            "stateLogPayloadBytes": self._estimate_state_log_payload_bytes(),
            "pluginRuntimeLogBytes": self._log_files_size(),
            "stateDbBytes": _sqlite_family_size(STATE_DB_PATH),
            "protectedUserTranscriptBytes": self._protected_payload_bytes(),
        }
        total = self._governed_total_bytes()
        return {
            "config": config,
            "maxBytes": int(config.get("maxBytes") or 209715200),
            "totalGovernedBytes": total,
            "overCapBytes": max(0, total - int(config.get("maxBytes") or 209715200)),
            "components": components,
            "recentRetentionEvents": observability_db.recent_retention_events(limit=10),
        }

    def _protected_payload_bytes(self) -> int:
        if not STATE_DB_PATH.exists():
            return 0
        with _connect(STATE_DB_PATH) as conn:
            total = 0
            for table in ("sessions", "messages", "chat_canonical_messages", "runtime_artifacts"):
                if not self._table_exists(conn, table):
                    continue
                cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                expr = "+".join([f'COALESCE(length(CAST("{col}" AS BLOB)),0)' for col in cols])
                row = conn.execute(f'SELECT COALESCE(SUM({expr}),0) AS bytes FROM "{table}"').fetchone()
                total += int(row["bytes"] or 0) if row else 0
            return total

    def migrate_legacy_logs(self, *, dry_run: bool = False) -> List[Dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        actions.extend(self._migrate_state_observability_tables(dry_run=dry_run))
        actions.extend(self._migrate_knowledge_execution_logs(dry_run=dry_run))
        return actions

    def _migrate_state_observability_tables(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        actions: list[dict[str, Any]] = []
        with observability_db.get_connection() as obs_conn:
            obs_conn.execute("ATTACH DATABASE ? AS state_db", (str(STATE_DB_PATH),))
            try:
                for table in STATE_LOG_TABLES:
                    if not self._table_exists(obs_conn, f"state_db.{table}") and not self._attached_table_exists(obs_conn, "state_db", table):
                        continue
                    count = int(obs_conn.execute(f"SELECT COUNT(*) AS count FROM state_db.{table}").fetchone()["count"] or 0)
                    if count <= 0:
                        continue
                    actions.append({"action": "migrate_state_table", "table": table, "rows": count, "dryRun": dry_run})
                    if not dry_run:
                        obs_conn.execute(f"INSERT OR IGNORE INTO main.{table} SELECT * FROM state_db.{table}")
                if not dry_run:
                    obs_conn.commit()
            finally:
                obs_conn.execute("DETACH DATABASE state_db")
        if not dry_run:
            with _connect(STATE_DB_PATH) as state_conn:
                for table in STATE_LOG_DELETE_TABLES:
                    if self._table_exists(state_conn, table):
                        state_conn.execute(f"DELETE FROM {table}")
                state_conn.commit()
                state_conn.execute("VACUUM")
        return actions

    @staticmethod
    def _attached_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
        return bool(conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def _migrate_knowledge_execution_logs(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        knowledge_path = V8_AGENT_OS_HOME / "memory" / ".index" / "knowledge.db"
        if not knowledge_path.exists():
            return []
        with _connect(knowledge_path) as knowledge_conn:
            if not self._table_exists(knowledge_conn, "execution_logs"):
                return []
            count = int(knowledge_conn.execute("SELECT COUNT(*) AS count FROM execution_logs").fetchone()["count"] or 0)
            if count <= 0:
                return []
            if not dry_run:
                with observability_db.get_connection() as obs_conn:
                    obs_conn.execute("ATTACH DATABASE ? AS knowledge_db", (str(knowledge_path),))
                    obs_conn.execute(
                        """
                        INSERT OR IGNORE INTO execution_logs (
                            id, task_name, action_type, action_target, trigger_source, status,
                            started_at, completed_at, duration_ms, error_message, payload
                        )
                        SELECT id, task_name, action_type, action_target, trigger_source, status,
                               started_at, finished_at, duration_ms, error_message, payload
                        FROM knowledge_db.execution_logs
                        """
                    )
                    obs_conn.commit()
                    obs_conn.execute("DETACH DATABASE knowledge_db")
                knowledge_conn.execute("DELETE FROM execution_logs")
                knowledge_conn.commit()
                knowledge_conn.execute("VACUUM")
            return [{"action": "migrate_knowledge_execution_logs", "rows": count, "dryRun": dry_run}]

    def enforce(self, *, dry_run: bool = False, reason: str = "manual") -> Dict[str, Any]:
        config = self.get_config()
        max_bytes = int(config.get("maxBytes") or 209715200)
        before = self._governed_total_bytes()
        actions = self.migrate_legacy_logs(dry_run=dry_run)
        total = self._governed_total_bytes()
        if total > max_bytes:
            actions.extend(self._prune_expired_response_cache(dry_run=dry_run))
            total = self._governed_total_bytes()
        for step in (
            self._prune_observability_logs,
            self._prune_runtime_snapshots,
            self._prune_completed_runtime_events,
            self._prune_old_checkpoints,
            self._prune_log_files,
        ):
            while total > max_bytes:
                step_actions = step(dry_run=dry_run)
                if not step_actions:
                    break
                actions.extend(step_actions)
                if dry_run:
                    break
                total = self._governed_total_bytes()
        after = self._governed_total_bytes()
        status = "dry_run" if dry_run else ("over_cap" if after > max_bytes else "completed")
        result = {
            "mode": "dry_run" if dry_run else "prune",
            "status": status,
            "reason": reason,
            "maxBytes": max_bytes,
            "beforeBytes": before,
            "afterBytes": after,
            "overCapBytes": max(0, after - max_bytes),
            "actions": actions,
            "protected": {
                "messages": True,
                "chatCanonicalMessages": True,
                "runtimeArtifacts": True,
                "memoryDiariesAndSummaries": True,
            },
        }
        if not dry_run:
            observability_db.add_retention_event(
                {
                    "mode": "prune",
                    "status": status,
                    "max_bytes": max_bytes,
                    "before_bytes": before,
                    "after_bytes": after,
                    "actions": actions,
                    "metadata": {"reason": reason},
                }
            )
        return result

    def _prune_expired_response_cache(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        with observability_db.get_connection() as conn:
            count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM llm_response_cache WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
            ).fetchone()["count"] or 0)
            if count <= 0:
                return []
            if not dry_run:
                conn.execute("DELETE FROM llm_response_cache WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')")
                conn.commit()
            return [{"action": "delete_expired_response_cache", "rows": count, "dryRun": dry_run}]

    def _prune_observability_logs(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        batch = 500
        actions: list[dict[str, Any]] = []
        with observability_db.get_connection() as conn:
            for table, order_col in (
                ("prompt_cache_events", "created_at"),
                ("tool_observation_records", "created_at"),
                ("provider_health_logs", "created_at"),
                ("model_invocation_logs", "started_at"),
                ("system_audit_log", "timestamp"),
                ("execution_logs", "started_at"),
            ):
                rows = conn.execute(f"SELECT id FROM {table} ORDER BY {order_col} ASC LIMIT ?", (batch,)).fetchall()
                ids = [row["id"] for row in rows]
                if not ids:
                    continue
                actions.append({"action": "prune_observability_table", "table": table, "rows": len(ids), "dryRun": dry_run})
                if not dry_run:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
                    conn.commit()
                break
        if not dry_run and actions:
            self._vacuum_db(OBSERVABILITY_DB_PATH)
        return actions

    def _prune_runtime_snapshots(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        with _connect(STATE_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT id FROM runtime_snapshots rs
                WHERE EXISTS (
                    SELECT 1 FROM runtime_snapshots newer
                    WHERE newer.session_id = rs.session_id
                      AND newer.snapshot_type = rs.snapshot_type
                      AND newer.latest_seq > rs.latest_seq
                )
                ORDER BY created_at ASC
                LIMIT 200
                """
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            if not dry_run:
                conn.execute(f"DELETE FROM runtime_snapshots WHERE id IN ({','.join('?' for _ in ids)})", ids)
                conn.commit()
                conn.execute("VACUUM")
            return [{"action": "prune_runtime_snapshots", "rows": len(ids), "dryRun": dry_run}]

    def _prune_completed_runtime_events(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        with _connect(STATE_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT re.id
                FROM runtime_events re
                JOIN run_records rr ON rr.id = re.run_id
                WHERE rr.status IN ('completed', 'failed', 'cancelled')
                  AND EXISTS (
                    SELECT 1 FROM chat_canonical_messages ccm
                    WHERE ccm.session_id = re.session_id
                  )
                  AND COALESCE(re.topic, '') NOT LIKE 'chat.message%'
                ORDER BY COALESCE(re.created_at, re.event_ts) ASC
                LIMIT 500
                """
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            if not dry_run:
                conn.execute(f"DELETE FROM runtime_events WHERE id IN ({','.join('?' for _ in ids)})", ids)
                conn.commit()
                conn.execute("VACUUM")
            return [{"action": "prune_completed_runtime_events", "rows": len(ids), "dryRun": dry_run}]

    def _prune_old_checkpoints(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not CHECKPOINT_DB_PATH.exists():
            return []
        protected = self._active_checkpoint_threads()
        with _connect(CHECKPOINT_DB_PATH) as conn:
            rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY checkpoint_id ASC LIMIT 200").fetchall()
            candidates = [str(row["thread_id"]) for row in rows if str(row["thread_id"]) not in protected]
            if not candidates:
                return []
            candidates = candidates[:25]
            if not dry_run:
                placeholders = ",".join("?" for _ in candidates)
                conn.execute(f"DELETE FROM writes WHERE thread_id IN ({placeholders})", candidates)
                conn.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({placeholders})", candidates)
                conn.commit()
                conn.execute("VACUUM")
            return [{"action": "prune_old_checkpoints", "threads": len(candidates), "dryRun": dry_run}]

    def _active_checkpoint_threads(self) -> set[str]:
        protected: set[str] = set()
        if not STATE_DB_PATH.exists():
            return protected
        with _connect(STATE_DB_PATH) as conn:
            for row in conn.execute(
                "SELECT id, session_id, thread_id FROM run_records WHERE status IN ('queued','running','waiting_approval','waiting_input','paused')"
            ).fetchall():
                for key in ("id", "session_id", "thread_id"):
                    value = row[key]
                    if value:
                        protected.add(str(value))
        return protected

    def _prune_log_files(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        files = sorted(self._log_files(), key=lambda path: path.stat().st_mtime if path.exists() else 0)
        if not files:
            return []
        targets = files[:25]
        actions = [{"action": "delete_log_file", "path": str(path), "bytes": _file_size(path), "dryRun": dry_run} for path in targets]
        if not dry_run:
            for path in targets:
                try:
                    path.unlink()
                except OSError:
                    pass
        return actions

    @staticmethod
    def _vacuum_db(path: Path) -> None:
        if not path.exists():
            return
        with _connect(path) as conn:
            conn.execute("VACUUM")


storage_retention_service = StorageRetentionService()
