from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core.v8_agent_os_paths import OBSERVABILITY_DB_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_threshold(days: int) -> str:
    safe_days = max(1, min(int(days or 1), 30))
    return (datetime.now(timezone.utc) - timedelta(days=safe_days)).strftime("%Y-%m-%d %H:%M:%S")


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;\"']{8,})"),
    re.compile(r"(?i)(bearer)\s+([A-Za-z0-9._~+/=-]{12,})"),
    re.compile(r"(?i)(sk-[A-Za-z0-9._-]{12,})"),
    re.compile(r"(?i)(v8o[a-zA-Z0-9._-]{12,})"),
]


def redact_observability_text(text: str) -> str:
    redacted = str(text or "")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}=<redacted>" if match.groups() and len(match.groups()) > 1 else "<redacted>", redacted)
    return redacted


class ObservabilityDatabaseManager:
    """SQLite store for non-authoritative logs, telemetry, and cache diagnostics."""

    def __init__(self, db_path: Path = OBSERVABILITY_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_invocation_logs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT,
                    session_id TEXT,
                    provider_id TEXT,
                    provider_name TEXT,
                    model_id TEXT NOT NULL,
                    role TEXT,
                    capability_class TEXT,
                    request_kind TEXT,
                    status TEXT NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    cost_input REAL DEFAULT 0,
                    cost_output REAL DEFAULT 0,
                    cost_total REAL DEFAULT 0,
                    latency_ms REAL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    is_streaming INTEGER DEFAULT 0,
                    metadata_json TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_health_logs (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    provider_name TEXT,
                    model_id TEXT,
                    run_id TEXT,
                    session_id TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    latency_ms REAL DEFAULT 0,
                    detail_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_cache_events (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT,
                    model_id TEXT,
                    model_ref TEXT,
                    role TEXT,
                    profile_id TEXT,
                    static_prefix_key TEXT,
                    response_cache_key TEXT,
                    decision TEXT NOT NULL,
                    skip_reason TEXT,
                    provider_patch_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_cache_segments (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    segment_type TEXT NOT NULL,
                    source TEXT,
                    content_hash TEXT NOT NULL,
                    char_count INTEGER DEFAULT 0,
                    estimated_tokens INTEGER DEFAULT 0,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES prompt_cache_events (id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_response_cache (
                    response_cache_key TEXT PRIMARY KEY,
                    static_prefix_key TEXT,
                    provider_id TEXT,
                    model_id TEXT,
                    model_ref TEXT,
                    role TEXT,
                    response_body_json TEXT NOT NULL,
                    metadata_json TEXT,
                    hit_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_target TEXT,
                    trigger_source TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_ms REAL,
                    error_message TEXT,
                    payload TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retention_events (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    max_bytes INTEGER NOT NULL,
                    before_bytes INTEGER DEFAULT 0,
                    after_bytes INTEGER DEFAULT 0,
                    actions_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_observation_records (
                    id TEXT PRIMARY KEY,
                    raw_ref TEXT UNIQUE NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_call_id TEXT,
                    run_id TEXT,
                    session_id TEXT,
                    runtime_kind TEXT,
                    surface TEXT,
                    raw_chars INTEGER DEFAULT 0,
                    visible_chars INTEGER DEFAULT 0,
                    raw_sha256 TEXT,
                    raw_body_text TEXT,
                    visible_body_text TEXT,
                    budget_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_columns(
                conn,
                "tool_observation_records",
                {
                    "run_id": "TEXT",
                    "session_id": "TEXT",
                    "visible_body_text": "TEXT",
                },
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_compaction_records (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    run_id TEXT,
                    target_role TEXT NOT NULL,
                    runtime_kind TEXT,
                    resolved_model_id TEXT,
                    trigger_reason TEXT,
                    compaction_mode TEXT,
                    summary_method TEXT,
                    baseline_reused INTEGER DEFAULT 0,
                    baseline_refreshed INTEGER DEFAULT 0,
                    baseline_snapshot_ref TEXT,
                    covered_message_count INTEGER DEFAULT 0,
                    covered_messages_hash TEXT,
                    summary_chars INTEGER DEFAULT 0,
                    summary_tokens INTEGER DEFAULT 0,
                    estimated_saved_tokens INTEGER DEFAULT 0,
                    context_window_tokens INTEGER DEFAULT 0,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_ledger_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT,
                    session_id TEXT,
                    event_type TEXT NOT NULL,
                    runtime_kind TEXT,
                    source TEXT,
                    summary TEXT,
                    refs_json TEXT,
                    payload_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_invocation_logs_run_id ON model_invocation_logs (run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_invocation_logs_model_id ON model_invocation_logs (model_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_invocation_logs_started_at ON model_invocation_logs (started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_health_logs_provider_id ON provider_health_logs (provider_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_cache_events_created_at ON prompt_cache_events (created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_cache_events_prefix_key ON prompt_cache_events (static_prefix_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_cache_segments_event_id ON prompt_cache_segments (event_id, ordinal)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_response_cache_expires_at ON llm_response_cache (expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON system_audit_log (timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_source ON system_audit_log (source_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_logs_started_at ON execution_logs (started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_retention_events_created_at ON retention_events (created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_observation_records_created_at ON tool_observation_records (created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_observation_records_tool ON tool_observation_records (tool_name, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_observation_records_call ON tool_observation_records (tool_call_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_observation_records_run ON tool_observation_records (run_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_observation_records_session ON tool_observation_records (session_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_compaction_records_created_at ON conversation_compaction_records (created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_compaction_records_session ON conversation_compaction_records (session_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_compaction_records_run ON conversation_compaction_records (run_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_run_ledger_events_run ON run_ledger_events (run_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_run_ledger_events_session ON run_ledger_events (session_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_run_ledger_events_type ON run_ledger_events (event_type, created_at DESC)")
            conn.commit()

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def add_tool_observation_record(self, record: Dict[str, Any]) -> None:
        metadata = dict(record.get("metadata") or {})
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_observation_records (
                    id, raw_ref, tool_name, tool_call_id, run_id, session_id, runtime_kind, surface,
                    raw_chars, visible_chars, raw_sha256, raw_body_text, visible_body_text,
                    budget_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("id"),
                    record.get("raw_ref"),
                    record.get("tool_name"),
                    record.get("tool_call_id"),
                    metadata.get("runId") or metadata.get("run_id"),
                    metadata.get("sessionId") or metadata.get("session_id"),
                    record.get("runtime_kind"),
                    record.get("surface"),
                    int(record.get("raw_chars") or 0),
                    int(record.get("visible_chars") or 0),
                    record.get("raw_sha256"),
                    record.get("raw_body"),
                    record.get("visible_body"),
                    json.dumps(record.get("budget") or {}, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    record.get("created_at") or utc_now_iso(),
                ),
            )
            conn.commit()

    def update_tool_observation_visible_surface(
        self,
        raw_ref_or_id: str,
        *,
        visible_content: str,
        budget: Dict[str, Any] | None = None,
    ) -> None:
        normalized = str(raw_ref_or_id or "").strip()
        if not normalized:
            return
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE tool_observation_records
                SET visible_body_text = ?, visible_chars = ?, budget_json = ?
                WHERE raw_ref = ? OR id = ?
                """,
                (
                    str(visible_content or ""),
                    len(str(visible_content or "")),
                    json.dumps(budget or {}, ensure_ascii=False),
                    normalized,
                    normalized.removeprefix("toolobs://"),
                ),
            )
            conn.commit()

    def get_tool_observation_record(self, raw_ref: str) -> Optional[Dict[str, Any]]:
        if not raw_ref:
            return None
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tool_observation_records WHERE raw_ref = ? OR id = ?",
                (raw_ref, raw_ref.removeprefix("toolobs://")),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["budget"] = json.loads(item["budget_json"]) if item.get("budget_json") else {}
            item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else {}
            return item

    def list_tool_observation_records(self, **filters: Any) -> Dict[str, Any]:
        limit = max(1, min(int(filters.get("limit") or 50), 200))
        cursor = str(filters.get("cursor") or "").strip()
        query = "SELECT * FROM tool_observation_records WHERE 1=1"
        params: list[Any] = []
        for key, column in (
            ("run_id", "run_id"),
            ("session_id", "session_id"),
            ("tool_name", "tool_name"),
            ("runtime_kind", "runtime_kind"),
            ("surface", "surface"),
        ):
            value = str(filters.get(key) or "").strip()
            if value:
                query += f" AND {column} = ?"
                params.append(value)
        if cursor:
            query += " AND datetime(created_at) < datetime(?)"
            params.append(cursor)
        query += " ORDER BY datetime(created_at) DESC LIMIT ?"
        params.append(limit + 1)
        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items: list[dict[str, Any]] = []
            for row in rows:
                item = self._decode_tool_observation_row(dict(row), preview_chars=int(filters.get("preview_chars") or 700))
                items.append(item)
            next_cursor = items[-1]["created_at"] if has_more and items else None
            return {"items": items, "nextCursor": next_cursor, "hasMore": has_more}

    def reveal_tool_observation_record(self, raw_ref_or_id: str, *, max_chars: int = 12000) -> Optional[Dict[str, Any]]:
        record = self.get_tool_observation_record(str(raw_ref_or_id or ""))
        if not record:
            return None
        return self._decode_tool_observation_row(record, preview_chars=max(500, min(int(max_chars or 12000), 50000)))

    def _decode_tool_observation_row(self, row: Dict[str, Any], *, preview_chars: int) -> Dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else json.loads(row.get("metadata_json") or "{}")
        budget = row.get("budget") if isinstance(row.get("budget"), dict) else json.loads(row.get("budget_json") or "{}")
        raw_body = str(row.get("raw_body_text") or "")
        raw_preview = raw_body[: max(0, int(preview_chars or 0))]
        preview = redact_observability_text(raw_preview)
        visible_body = str(row.get("visible_body_text") or "")
        visible_raw_preview = visible_body[: max(0, int(preview_chars or 0))]
        visible_preview = redact_observability_text(visible_raw_preview)
        return {
            "id": row.get("id"),
            "rawRef": row.get("raw_ref"),
            "toolName": row.get("tool_name"),
            "toolCallId": row.get("tool_call_id"),
            "runId": row.get("run_id") or metadata.get("runId") or metadata.get("run_id"),
            "sessionId": row.get("session_id") or metadata.get("sessionId") or metadata.get("session_id"),
            "runtimeKind": row.get("runtime_kind"),
            "surface": row.get("surface"),
            "rawChars": int(row.get("raw_chars") or 0),
            "visibleChars": int(row.get("visible_chars") or 0),
            "rawSha256": row.get("raw_sha256"),
            "created_at": row.get("created_at"),
            "createdAt": row.get("created_at"),
            "budget": budget,
            "metadata": metadata,
            "preview": preview,
            "previewChars": len(preview),
            "omittedChars": max(0, len(raw_body) - len(raw_preview)),
            "redacted": preview != raw_preview,
            "agentVisiblePreview": visible_preview,
            "agentVisiblePreviewChars": len(visible_preview),
            "agentVisibleOmittedChars": max(0, len(visible_body) - len(visible_raw_preview)),
        }

    def add_conversation_compaction_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_id = str(record.get("id") or f"cmp_{uuid.uuid4().hex}")
        payload = {
            "id": record_id,
            "session_id": record.get("session_id"),
            "run_id": record.get("run_id"),
            "target_role": record.get("target_role") or "supervisor",
            "runtime_kind": record.get("runtime_kind"),
            "resolved_model_id": record.get("resolved_model_id"),
            "trigger_reason": record.get("trigger_reason"),
            "compaction_mode": record.get("compaction_mode"),
            "summary_method": record.get("summary_method"),
            "baseline_reused": 1 if record.get("baseline_reused") else 0,
            "baseline_refreshed": 1 if record.get("baseline_refreshed") else 0,
            "baseline_snapshot_ref": record.get("baseline_snapshot_ref"),
            "covered_message_count": int(record.get("covered_message_count") or 0),
            "covered_messages_hash": record.get("covered_messages_hash"),
            "summary_chars": int(record.get("summary_chars") or 0),
            "summary_tokens": int(record.get("summary_tokens") or 0),
            "estimated_saved_tokens": int(record.get("estimated_saved_tokens") or 0),
            "context_window_tokens": int(record.get("context_window_tokens") or 0),
            "metadata_json": json.dumps(record.get("metadata") or {}, ensure_ascii=False),
            "created_at": record.get("created_at") or utc_now_iso(),
        }
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversation_compaction_records (
                    id, session_id, run_id, target_role, runtime_kind, resolved_model_id,
                    trigger_reason, compaction_mode, summary_method, baseline_reused,
                    baseline_refreshed, baseline_snapshot_ref, covered_message_count,
                    covered_messages_hash, summary_chars, summary_tokens,
                    estimated_saved_tokens, context_window_tokens, metadata_json, created_at
                ) VALUES (
                    :id, :session_id, :run_id, :target_role, :runtime_kind, :resolved_model_id,
                    :trigger_reason, :compaction_mode, :summary_method, :baseline_reused,
                    :baseline_refreshed, :baseline_snapshot_ref, :covered_message_count,
                    :covered_messages_hash, :summary_chars, :summary_tokens,
                    :estimated_saved_tokens, :context_window_tokens, :metadata_json, :created_at
                )
                """,
                payload,
            )
            conn.commit()
        return {**payload, "metadata": json.loads(payload["metadata_json"])}

    def list_conversation_compaction_records(self, **filters: Any) -> Dict[str, Any]:
        limit = max(1, min(int(filters.get("limit") or 50), 200))
        cursor = str(filters.get("cursor") or "").strip()
        query = "SELECT * FROM conversation_compaction_records WHERE 1=1"
        params: list[Any] = []
        for key, column in (
            ("session_id", "session_id"),
            ("run_id", "run_id"),
            ("target_role", "target_role"),
        ):
            value = str(filters.get(key) or "").strip()
            if value:
                query += f" AND {column} = ?"
                params.append(value)
        if cursor:
            query += " AND datetime(created_at) < datetime(?)"
            params.append(cursor)
        query += " ORDER BY datetime(created_at) DESC LIMIT ?"
        params.append(limit + 1)
        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.get("metadata_json") or "{}")
                item["baselineReused"] = bool(item.pop("baseline_reused", 0))
                item["baselineRefreshed"] = bool(item.pop("baseline_refreshed", 0))
                item["createdAt"] = item.get("created_at")
                items.append(item)
            next_cursor = items[-1]["created_at"] if has_more and items else None
            return {"items": items, "nextCursor": next_cursor, "hasMore": has_more}

    def add_run_ledger_event(self, record: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(record.get("id") or f"rle_{uuid.uuid4().hex}")
        payload = {
            "id": event_id,
            "run_id": record.get("run_id") or record.get("runId"),
            "session_id": record.get("session_id") or record.get("sessionId"),
            "event_type": str(record.get("event_type") or record.get("eventType") or "").strip(),
            "runtime_kind": record.get("runtime_kind") or record.get("runtimeKind"),
            "source": record.get("source"),
            "summary": redact_observability_text(str(record.get("summary") or "")),
            "refs_json": json.dumps(record.get("refs") or {}, ensure_ascii=False),
            "payload_json": json.dumps(record.get("payload") or {}, ensure_ascii=False),
            "created_at": record.get("created_at") or record.get("createdAt") or utc_now_iso(),
        }
        if not payload["event_type"]:
            raise ValueError("run ledger event_type is required")
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_ledger_events (
                    id, run_id, session_id, event_type, runtime_kind, source,
                    summary, refs_json, payload_json, created_at
                ) VALUES (
                    :id, :run_id, :session_id, :event_type, :runtime_kind, :source,
                    :summary, :refs_json, :payload_json, :created_at
                )
                """,
                payload,
            )
            conn.commit()
        return self._decode_run_ledger_event(payload)

    def list_run_ledger_events(self, **filters: Any) -> Dict[str, Any]:
        limit = max(1, min(int(filters.get("limit") or 100), 500))
        cursor = str(filters.get("cursor") or "").strip()
        query = "SELECT * FROM run_ledger_events WHERE 1=1"
        params: list[Any] = []
        for key, column in (
            ("run_id", "run_id"),
            ("session_id", "session_id"),
            ("event_type", "event_type"),
            ("runtime_kind", "runtime_kind"),
        ):
            value = str(filters.get(key) or "").strip()
            if value:
                query += f" AND {column} = ?"
                params.append(value)
        if cursor:
            query += " AND datetime(created_at) < datetime(?)"
            params.append(cursor)
        query += " ORDER BY datetime(created_at) DESC LIMIT ?"
        params.append(limit + 1)
        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [self._decode_run_ledger_event(dict(row)) for row in rows]
            next_cursor = items[-1]["createdAt"] if has_more and items else None
            return {"items": items, "nextCursor": next_cursor, "hasMore": has_more}

    @staticmethod
    def _decode_run_ledger_event(row: Dict[str, Any]) -> Dict[str, Any]:
        refs_raw = row.get("refs_json")
        payload_raw = row.get("payload_json")
        try:
            refs = json.loads(refs_raw or "{}")
        except Exception:
            refs = {}
        try:
            payload = json.loads(payload_raw or "{}")
        except Exception:
            payload = {}
        return {
            "id": row.get("id"),
            "runId": row.get("run_id"),
            "sessionId": row.get("session_id"),
            "eventType": row.get("event_type"),
            "runtimeKind": row.get("runtime_kind"),
            "source": row.get("source"),
            "summary": row.get("summary") or "",
            "refs": refs,
            "payload": payload,
            "createdAt": row.get("created_at"),
            "created_at": row.get("created_at"),
        }

    def add_model_invocation_log(self, record: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO model_invocation_logs (
                    id, run_id, session_id, provider_id, provider_name, model_id, role, capability_class,
                    request_kind, status, input_tokens, output_tokens, total_tokens, cost_input, cost_output,
                    cost_total, latency_ms, error_code, error_message, is_streaming, metadata_json,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("id"),
                    record.get("run_id"),
                    record.get("session_id"),
                    record.get("provider_id"),
                    record.get("provider_name"),
                    record.get("model_id"),
                    record.get("role"),
                    record.get("capability_class"),
                    record.get("request_kind"),
                    record.get("status"),
                    int(record.get("input_tokens") or 0),
                    int(record.get("output_tokens") or 0),
                    int(record.get("total_tokens") or 0),
                    float(record.get("cost_input") or 0.0),
                    float(record.get("cost_output") or 0.0),
                    float(record.get("cost_total") or 0.0),
                    float(record.get("latency_ms") or 0.0),
                    record.get("error_code"),
                    record.get("error_message"),
                    1 if record.get("is_streaming") else 0,
                    json.dumps(record.get("metadata") or {}, ensure_ascii=False),
                    record.get("started_at"),
                    record.get("finished_at"),
                ),
            )
            conn.commit()

    def add_provider_health_log(self, record: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO provider_health_logs (
                    id, provider_id, provider_name, model_id, run_id, session_id, status,
                    error_code, error_message, latency_ms, detail_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("id"),
                    record.get("provider_id"),
                    record.get("provider_name"),
                    record.get("model_id"),
                    record.get("run_id"),
                    record.get("session_id"),
                    record.get("status"),
                    record.get("error_code"),
                    record.get("error_message"),
                    float(record.get("latency_ms") or 0.0),
                    json.dumps(record.get("detail") or {}, ensure_ascii=False),
                ),
            )
            conn.commit()

    def add_prompt_cache_event(self, record: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO prompt_cache_events (
                    id, provider_id, model_id, model_ref, role, profile_id,
                    static_prefix_key, response_cache_key, decision, skip_reason,
                    provider_patch_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("id"),
                    record.get("provider_id"),
                    record.get("model_id"),
                    record.get("model_ref"),
                    record.get("role"),
                    record.get("profile_id"),
                    record.get("static_prefix_key"),
                    record.get("response_cache_key"),
                    record.get("decision"),
                    record.get("skip_reason"),
                    json.dumps(record.get("provider_patch") or {}, ensure_ascii=False),
                    json.dumps(record.get("metadata") or {}, ensure_ascii=False),
                    record.get("created_at") or utc_now_iso(),
                ),
            )
            conn.commit()

    def add_prompt_cache_segments(self, event_id: str, segments: List[Dict[str, Any]]) -> None:
        if not event_id:
            return
        with self.get_connection() as conn:
            conn.execute("DELETE FROM prompt_cache_segments WHERE event_id = ?", (event_id,))
            for index, segment in enumerate(segments or []):
                conn.execute(
                    """
                    INSERT INTO prompt_cache_segments (
                        id, event_id, ordinal, segment_type, source, content_hash,
                        char_count, estimated_tokens, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        event_id,
                        index,
                        segment.get("type") or segment.get("segment_type") or "",
                        segment.get("source"),
                        segment.get("hash") or segment.get("content_hash") or "",
                        int(segment.get("charCount") or segment.get("char_count") or 0),
                        int(segment.get("estimatedTokens") or segment.get("estimated_tokens") or 0),
                        json.dumps(segment.get("metadata") or {}, ensure_ascii=False),
                        utc_now_iso(),
                    ),
                )
            conn.commit()

    def get_llm_response_cache(self, response_cache_key: str) -> Optional[Dict[str, Any]]:
        if not response_cache_key:
            return None
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM llm_response_cache
                WHERE response_cache_key = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (response_cache_key, utc_now_iso()),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["response"] = json.loads(item["response_body_json"]) if item.get("response_body_json") else {}
            item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else {}
            return item

    def upsert_llm_response_cache(self, record: Dict[str, Any]) -> None:
        key = str(record.get("response_cache_key") or "")
        if not key:
            return
        ttl_seconds = int(record.get("ttl_seconds") or 600)
        now_epoch = time.time()
        created_at = record.get("created_at") or datetime.fromtimestamp(now_epoch, timezone.utc).isoformat()
        expires_at = datetime.fromtimestamp(now_epoch + max(ttl_seconds, 1), timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO llm_response_cache (
                    response_cache_key, static_prefix_key, provider_id, model_id, model_ref,
                    role, response_body_json, metadata_json, hit_count, created_at,
                    updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(response_cache_key) DO UPDATE SET
                    response_body_json=excluded.response_body_json,
                    metadata_json=excluded.metadata_json,
                    static_prefix_key=excluded.static_prefix_key,
                    provider_id=excluded.provider_id,
                    model_id=excluded.model_id,
                    model_ref=excluded.model_ref,
                    role=excluded.role,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at
                """,
                (
                    key,
                    record.get("static_prefix_key"),
                    record.get("provider_id"),
                    record.get("model_id"),
                    record.get("model_ref"),
                    record.get("role"),
                    json.dumps(record.get("response") or {}, ensure_ascii=False),
                    json.dumps(record.get("metadata") or {}, ensure_ascii=False),
                    created_at,
                    utc_now_iso(),
                    expires_at,
                ),
            )
            conn.commit()

    def increment_llm_response_cache_hit(self, response_cache_key: str) -> None:
        if not response_cache_key:
            return
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE llm_response_cache SET hit_count = hit_count + 1, updated_at = ? WHERE response_cache_key = ?",
                (utc_now_iso(), response_cache_key),
            )
            conn.commit()

    def get_prompt_cache_stats(self, limit: int = 50, days: int = 1) -> Dict[str, Any]:
        threshold = _window_threshold(days)
        with self.get_connection() as conn:
            by_decision = [dict(row) for row in conn.execute(
                """
                SELECT decision, COUNT(*) AS count
                FROM prompt_cache_events
                WHERE datetime(created_at) >= datetime(?)
                GROUP BY decision
                ORDER BY count DESC
                """,
                (threshold,),
            ).fetchall()]
            by_skip_reason = [dict(row) for row in conn.execute(
                """
                SELECT skip_reason, COUNT(*) AS count
                FROM prompt_cache_events
                WHERE COALESCE(skip_reason, '') != ''
                  AND datetime(created_at) >= datetime(?)
                GROUP BY skip_reason
                ORDER BY count DESC
                """,
                (threshold,),
            ).fetchall()]
            totals_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    SUM(CASE WHEN COALESCE(provider_patch_json, '') NOT IN ('', '{}', 'null') THEN 1 ELSE 0 END) AS provider_patch_events,
                    SUM(CASE WHEN decision = 'hit' THEN 1 ELSE 0 END) AS response_hits,
                    SUM(CASE WHEN decision = 'miss' THEN 1 ELSE 0 END) AS response_misses,
                    SUM(CASE WHEN decision = 'skipped' THEN 1 ELSE 0 END) AS response_skipped
                FROM prompt_cache_events
                WHERE datetime(created_at) >= datetime(?)
                """,
                (threshold,),
            ).fetchone()
            total_events = int(totals_row["total_events"] or 0) if totals_row else 0
            provider_patch_events = int(totals_row["provider_patch_events"] or 0) if totals_row else 0
            response_hits = int(totals_row["response_hits"] or 0) if totals_row else 0
            response_misses = int(totals_row["response_misses"] or 0) if totals_row else 0
            response_skipped = int(totals_row["response_skipped"] or 0) if totals_row else 0
            prefix_rows = [dict(row) for row in conn.execute(
                """
                SELECT static_prefix_key, COUNT(*) AS count
                FROM prompt_cache_events
                WHERE datetime(created_at) >= datetime(?)
                  AND COALESCE(static_prefix_key, '') != ''
                GROUP BY static_prefix_key
                """,
                (threshold,),
            ).fetchall()]
            reused_prefixes = [row for row in prefix_rows if int(row.get("count") or 0) > 1]
            reused_prefix_event_count = sum(int(row.get("count") or 0) for row in reused_prefixes)
            segment_rows = [dict(row) for row in conn.execute(
                """
                SELECT s.segment_type, COUNT(*) AS segments, COALESCE(SUM(s.estimated_tokens), 0) AS estimated_tokens
                FROM prompt_cache_segments s
                JOIN prompt_cache_events e ON e.id = s.event_id
                WHERE datetime(e.created_at) >= datetime(?)
                GROUP BY s.segment_type
                ORDER BY s.segment_type
                """,
                (threshold,),
            ).fetchall()]
            cache_row = conn.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(hit_count), 0) AS hits
                FROM llm_response_cache
                WHERE expires_at IS NULL OR expires_at > ?
                """,
                (utc_now_iso(),),
            ).fetchone()
            recent: list[dict[str, Any]] = []
            for row in conn.execute(
                """
                SELECT * FROM prompt_cache_events
                WHERE datetime(created_at) >= datetime(?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (threshold, max(1, min(int(limit or 50), 200))),
            ).fetchall():
                item = dict(row)
                item["providerPatch"] = json.loads(item["provider_patch_json"]) if item.get("provider_patch_json") else {}
                item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else {}
                recent.append(item)
            return {
                "window": {"days": max(1, min(int(days or 1), 30)), "since": threshold},
                "totals": {
                    "events": total_events,
                    "providerPatchEvents": provider_patch_events,
                    "responseHits": response_hits,
                    "responseMisses": response_misses,
                    "responseSkipped": response_skipped,
                    "reusedPrefixKeys": len(reused_prefixes),
                    "reusedPrefixEvents": reused_prefix_event_count,
                },
                "rates": {
                    "providerPatchRate": _rate(provider_patch_events, total_events),
                    "staticPrefixReuseRate": _rate(reused_prefix_event_count, total_events),
                    "v8ExactResponseHitRate": _rate(response_hits, response_hits + response_misses),
                    "responseCacheSkipRate": _rate(response_skipped, total_events),
                },
                "segmentTokenEstimate": {
                    str(row.get("segment_type") or "unknown"): {
                        "segments": int(row.get("segments") or 0),
                        "estimatedTokens": int(row.get("estimated_tokens") or 0),
                    }
                    for row in segment_rows
                },
                "providerUsage": {
                    "cachedInputTokensReported": False,
                    "cachedInputTokenRate": None,
                    "note": "provider usage did not report cached input token fields",
                },
                "eventsByDecision": by_decision,
                "eventsBySkipReason": by_skip_reason,
                "responseCache": {
                    "entries": int(cache_row["count"] or 0) if cache_row else 0,
                    "hits": int(cache_row["hits"] or 0) if cache_row else 0,
                },
                "recentEvents": recent,
            }

    def get_prompt_cache_prefix_use_counts(self, days: int = 1) -> Dict[str, int]:
        threshold = _window_threshold(days)
        with self.get_connection() as conn:
            return {
                str(row["static_prefix_key"]): int(row["count"] or 0)
                for row in conn.execute(
                    """
                    SELECT static_prefix_key, COUNT(*) AS count
                    FROM prompt_cache_events
                    WHERE datetime(created_at) >= datetime(?)
                      AND COALESCE(static_prefix_key, '') != ''
                    GROUP BY static_prefix_key
                    """,
                    (threshold,),
                ).fetchall()
            }

    def purge_prompt_cache(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            counts: Dict[str, int] = {}
            for table in ("prompt_cache_segments", "prompt_cache_events", "llm_response_cache"):
                row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
                counts[table] = int(row["count"] or 0) if row else 0
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            return {"deleted": counts}

    def add_audit_log(self, source_type: str, action: str, status: str, details: str = None) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO system_audit_log (id, source_type, action, status, details) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), source_type, action, status, details),
            )
            conn.commit()

    def get_audit_logs(self, limit: int = 100, offset: int = 0, source_type: str = None, status: str = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM system_audit_log WHERE 1=1"
        params: list[Any] = []
        if source_type:
            query += " AND source_type = ?"
            params.append(source_type)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def clear_audit_logs(self, *, source_type: str = None, status: str = None) -> Dict[str, Any]:
        query = "DELETE FROM system_audit_log WHERE 1=1"
        count_query = "SELECT COUNT(*) AS count FROM system_audit_log WHERE 1=1"
        params: list[Any] = []
        if source_type:
            query += " AND source_type = ?"
            count_query += " AND source_type = ?"
            params.append(source_type)
        if status:
            query += " AND status = ?"
            count_query += " AND status = ?"
            params.append(status)
        with self.get_connection() as conn:
            row = conn.execute(count_query, params).fetchone()
            deleted = int(row["count"] or 0) if row else 0
            conn.execute(query, params)
            conn.commit()
            return {"deleted": deleted}

    def log_execution(self, log_id: str, task_name: str, action_type: str, action_target: str, trigger_source: str, status: str, payload: Optional[Dict[str, Any]] = None) -> None:
        with self.get_connection() as conn:
            existing = conn.execute("SELECT id FROM execution_logs WHERE id = ?", (log_id,)).fetchone()
            payload_str = json.dumps(payload or {}, ensure_ascii=False)
            if existing:
                conn.execute(
                    """
                    UPDATE execution_logs
                    SET status = ?, completed_at = CURRENT_TIMESTAMP, payload = ?
                    WHERE id = ?
                    """,
                    (status, payload_str, log_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO execution_logs (id, task_name, action_type, action_target, trigger_source, status, started_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                    """,
                    (log_id, task_name, action_type, action_target, trigger_source, status, payload_str),
                )
            conn.commit()

    def get_execution_logs(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = []
            for row in conn.execute(
                "SELECT * FROM execution_logs ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall():
                item = dict(row)
                item["payload"] = json.loads(item.get("payload") or "{}")
                item["finished_at"] = item.get("completed_at")
                rows.append(item)
            return rows

    def get_recent_model_invocations(self, limit: int = 20, days: int | None = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows: list[dict[str, Any]] = []
            if days is not None:
                query = """
                    SELECT * FROM model_invocation_logs
                    WHERE datetime(started_at) >= datetime(?)
                    ORDER BY started_at DESC
                    LIMIT ?
                """
                params: tuple[Any, ...] = (_window_threshold(days), limit)
            else:
                query = "SELECT * FROM model_invocation_logs ORDER BY started_at DESC LIMIT ?"
                params = (limit,)
            for row in conn.execute(query, params).fetchall():
                item = dict(row)
                item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else {}
                rows.append(item)
            return rows

    def get_model_invocation_window_totals(self, days: int = 1) -> Dict[str, Any]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS invocations,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost_total), 0) AS cost_total
                FROM model_invocation_logs
                WHERE datetime(started_at) >= datetime(?)
                """,
                (_window_threshold(days),),
            ).fetchone()
            return dict(row) if row else {"invocations": 0, "total_tokens": 0, "cost_total": 0.0}

    def list_model_invocations(self, **filters: Any) -> List[Dict[str, Any]]:
        query = "SELECT * FROM model_invocation_logs WHERE 1=1"
        params: list[Any] = []
        for key, column in (
            ("session_id", "session_id"),
            ("run_id", "run_id"),
            ("capability_class", "capability_class"),
            ("request_kind", "request_kind"),
            ("status", "status"),
        ):
            if filters.get(key):
                query += f" AND {column} = ?"
                params.append(filters[key])
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(int(filters.get("limit") or 20))
        with self.get_connection() as conn:
            rows = []
            for row in conn.execute(query, params).fetchall():
                item = dict(row)
                item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else {}
                rows.append(item)
            return rows

    def get_model_usage_distribution(self, days: int = 7, limit: int = 12) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(
                """
                SELECT model_id, provider_name, provider_id, COUNT(*) AS invocations,
                       SUM(total_tokens) AS total_tokens, SUM(cost_total) AS cost_total
                FROM model_invocation_logs
                WHERE started_at >= datetime('now', ?)
                GROUP BY model_id, provider_name, provider_id
                ORDER BY invocations DESC, total_tokens DESC
                LIMIT ?
                """,
                (f"-{max(days, 1)} day", limit),
            ).fetchall()]

    def get_provider_health_summary(self, days: int = 7) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(
                """
                SELECT provider_id, provider_name, COUNT(*) AS events,
                       SUM(CASE WHEN status IN ('completed', 'healthy') THEN 1 ELSE 0 END) AS success_count,
                       SUM(CASE WHEN status NOT IN ('completed', 'healthy') THEN 1 ELSE 0 END) AS error_count,
                       AVG(latency_ms) AS avg_latency_ms,
                       MAX(created_at) AS last_seen_at
                FROM provider_health_logs
                WHERE created_at >= datetime('now', ?)
                  AND COALESCE(json_extract(detail_json, '$.source'), '') != 'manual_connection_test'
                GROUP BY provider_id, provider_name
                ORDER BY events DESC, provider_name ASC
                """,
                (f"-{max(days, 1)} day",),
            ).fetchall()]

    def get_daily_invocation_activity(self, days: int = 7) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(
                """
                SELECT date(started_at) AS day,
                       COUNT(*) AS invocations,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM model_invocation_logs
                WHERE started_at >= datetime('now', ?)
                GROUP BY date(started_at)
                """,
                (f"-{max(days, 1)} day",),
            ).fetchall()]

    def get_run_invocation_totals(self, run_id: str) -> Dict[str, Any]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS invocations, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost_total), 0) AS cost_total,
                       COALESCE(SUM(latency_ms), 0) AS latency_ms_total,
                       MAX(finished_at) AS last_finished_at
                FROM model_invocation_logs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            return dict(row) if row else {
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0.0,
                "latency_ms_total": 0.0,
                "last_finished_at": None,
            }

    def add_retention_event(self, record: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO retention_events (
                    id, mode, status, max_bytes, before_bytes, after_bytes,
                    actions_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("id") or str(uuid.uuid4()),
                    record.get("mode") or "dry_run",
                    record.get("status") or "completed",
                    int(record.get("max_bytes") or 0),
                    int(record.get("before_bytes") or 0),
                    int(record.get("after_bytes") or 0),
                    json.dumps(record.get("actions") or [], ensure_ascii=False),
                    json.dumps(record.get("metadata") or {}, ensure_ascii=False),
                    record.get("created_at") or utc_now_iso(),
                ),
            )
            conn.commit()

    def recent_retention_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = []
            for row in conn.execute(
                "SELECT * FROM retention_events ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit or 20), 100)),),
            ).fetchall():
                item = dict(row)
                item["actions"] = json.loads(item.get("actions_json") or "[]")
                item["metadata"] = json.loads(item.get("metadata_json") or "{}")
                rows.append(item)
            return rows


observability_db = ObservabilityDatabaseManager()
