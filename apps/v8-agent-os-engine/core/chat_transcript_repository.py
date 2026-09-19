"""Canonical chat transcript persistence.

This module owns chat transcript state and revision reads/writes. It is deliberately
dependency-injected so DatabaseManager remains the SQLite lifecycle owner while
callers keep the established DatabaseManager API.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from core.json_safe import to_jsonable
from core.realtime_protocol import utc_now_iso


class ChatTranscriptRepository:
    """Persist canonical chat messages behind an explicit repository boundary."""

    def __init__(self, get_connection: Callable[[], Any], run_write: Callable[..., Any]) -> None:
        self._get_connection = get_connection
        self._run_write = run_write

    def get_chat_transcript_state(self, session_id: str) -> Dict[str, Any]:
        from core.conversation_schema import transcript_state
        with self._get_connection() as conn:
            return transcript_state(conn, session_id)

    def assert_chat_run_epoch(self, session_id: str, run_id: str) -> None:
        from core.conversation_schema import assert_run_epoch
        with self._get_connection() as conn:
            assert_run_epoch(conn, session_id, run_id)

    def has_chat_branch_artifact_ref(self, session_id: str, artifact_id: str) -> bool:
        with self._get_connection() as conn:
            return conn.execute("SELECT 1 FROM chat_branch_artifact_refs WHERE child_session_id=? AND artifact_id=?",
                                (session_id, artifact_id)).fetchone() is not None

    def get_next_chat_canonical_ordinal(self, session_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal FROM chat_canonical_messages WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return int(row["next_ordinal"]) if row else 1

    def create_chat_canonical_message(
        self,
        *,
        message_id: str,
        session_id: str,
        run_id: Optional[str],
        ordinal: int,
        role: str,
        state: str,
        nodes: list[dict[str, Any]],
        artifacts: Optional[list[dict[str, Any]]] = None,
        content_text: Optional[str] = None,
        reasoning_text: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        finalized_at: Optional[str] = None,
    ) -> None:
        nodes_str = json.dumps(to_jsonable(nodes or []), ensure_ascii=False)
        artifacts_str = json.dumps(to_jsonable(artifacts or []), ensure_ascii=False)
        metadata_str = json.dumps(to_jsonable(metadata or {}), ensure_ascii=False)
        now_iso = utc_now_iso()

        def _write():
            with self._get_connection() as conn:
                from core.conversation_schema import assert_run_epoch
                conn.execute("BEGIN IMMEDIATE")
                assert_run_epoch(conn, session_id, run_id)
                conn.execute(
                    '''
                    INSERT INTO chat_canonical_messages
                    (id, session_id, run_id, ordinal, role, state, nodes_json, artifacts_json, content_text, reasoning_text, metadata_json, version, created_at, updated_at, finalized_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ''',
                    (
                        message_id,
                        session_id,
                        run_id,
                        ordinal,
                        role,
                        state,
                        nodes_str,
                        artifacts_str,
                        content_text,
                        reasoning_text,
                        metadata_str,
                        now_iso,
                        now_iso,
                        finalized_at,
                    ),
                )
                conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (now_iso, session_id))
                conn.commit()

        self._run_write(_write)

    def update_chat_canonical_message(
        self,
        message_id: str,
        *,
        state: Optional[str] = None,
        nodes: Optional[list[dict[str, Any]]] = None,
        artifacts: Optional[list[dict[str, Any]]] = None,
        content_text: Optional[str] = None,
        reasoning_text: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        finalized_at: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_chat_canonical_message(message_id)
        if not existing:
            return None
        if expected_version is not None and int(existing.get("version") or 1) != expected_version:
            raise ValueError("canonical_message_version_conflict")
        next_nodes = nodes if nodes is not None else existing.get("nodes") or []
        next_artifacts = artifacts if artifacts is not None else existing.get("artifacts") or []
        next_metadata = metadata if metadata is not None else existing.get("metadata") or {}
        next_state = state or existing.get("state") or "pending"
        next_content = existing.get("content_text") if content_text is None else content_text
        next_reasoning = existing.get("reasoning_text") if reasoning_text is None else reasoning_text
        next_version = int(existing.get("version") or 0) + 1
        finalized_value = finalized_at if finalized_at is not None else existing.get("finalized_at")
        now_iso = utc_now_iso()

        def _write():
            with self._get_connection() as conn:
                from core.conversation_schema import assert_run_epoch
                conn.execute("BEGIN IMMEDIATE")
                assert_run_epoch(conn, str(existing.get("session_id") or ""), existing.get("run_id"))
                conn.execute(
                    '''
                    UPDATE chat_canonical_messages
                    SET state = ?,
                        nodes_json = ?,
                        artifacts_json = ?,
                        content_text = ?,
                        reasoning_text = ?,
                        metadata_json = ?,
                        version = ?,
                        updated_at = ?,
                        finalized_at = ?
                    WHERE id = ? AND version = ?
                    ''',
                    (
                        next_state,
                        json.dumps(to_jsonable(next_nodes), ensure_ascii=False),
                        json.dumps(to_jsonable(next_artifacts), ensure_ascii=False),
                        next_content,
                        next_reasoning,
                        json.dumps(to_jsonable(next_metadata), ensure_ascii=False),
                        next_version,
                        now_iso,
                        finalized_value,
                        message_id,
                        int(existing.get("version") or 1),
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0] != 1:
                    raise ValueError("canonical_message_version_conflict")
                session_id = str(existing.get("session_id") or "").strip()
                if session_id:
                    conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (now_iso, session_id))
                conn.commit()

        self._run_write(_write)
        return self.get_chat_canonical_message(message_id)

    def get_chat_canonical_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM chat_canonical_messages WHERE id = ?', (message_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_chat_canonical_row(dict(row))

    def get_chat_canonical_message_by_run(
        self,
        *,
        session_id: str,
        run_id: str,
        role: str = "assistant",
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ? AND run_id = ? AND role = ?
                ORDER BY ordinal DESC, updated_at DESC
                LIMIT 1
                ''',
                (session_id, run_id, role),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_chat_canonical_row(dict(row))

    def get_chat_canonical_message_by_client_message_id(
        self,
        *,
        session_id: str,
        client_message_id: str,
        role: str = "user",
    ) -> Optional[Dict[str, Any]]:
        normalized_client_id = str(client_message_id or "").strip()
        if not session_id or not normalized_client_id:
            return None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ? AND role = ?
                ORDER BY ordinal DESC, updated_at DESC
                ''',
                (session_id, role),
            )
            for row in cursor.fetchall():
                hydrated = self._hydrate_chat_canonical_row(dict(row))
                metadata = hydrated.get("metadata") if isinstance(hydrated.get("metadata"), dict) else {}
                row_client_id = str(
                    metadata.get("clientMessageId")
                    or metadata.get("client_message_id")
                    or hydrated.get("id")
                    or ""
                ).strip()
                if row_client_id == normalized_client_id:
                    return hydrated
        return None

    def get_chat_canonical_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id, session_id, session_id),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def get_chat_canonical_messages_before_ordinal(
        self,
        session_id: str,
        before_ordinal: int | None = None,
        *,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 500), 2000))
        ordinal_clause = ""
        params: list[Any] = [session_id]
        if before_ordinal is not None:
            ordinal_clause = "AND ordinal < ?"
            params.append(int(before_ordinal))
        params.extend([session_id, session_id, safe_limit])
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f'''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  {ordinal_clause}
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal DESC, created_at DESC
                LIMIT ?
                ''',
                tuple(params),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def get_chat_canonical_turn_index_rows(self, session_id: str) -> List[Dict[str, Any]]:
        """Return only the columns required to derive stable conversation turns.

        The turn index is a Human Surface navigation aid.  It intentionally does
        not hydrate canonical nodes, reasoning, tool payloads, metadata, or
        artifacts.  A bounded content prefix is included solely for the tick
        preview shown by clients.
        """

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT id,
                       session_id,
                       run_id,
                       json_extract(metadata_json, '$.sourceRunId') AS source_run_id,
                       ordinal,
                       role,
                       state,
                       SUBSTR(COALESCE(content_text, ''), 1, 241) AS content_preview,
                       created_at,
                       updated_at
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id, session_id, session_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_chat_canonical_messages_in_ordinal_range(
        self,
        session_id: str,
        *,
        first_ordinal: int,
        last_ordinal: int,
    ) -> List[Dict[str, Any]]:
        """Hydrate canonical messages only for one contiguous turn window."""

        lower_bound = min(int(first_ordinal), int(last_ordinal))
        upper_bound = max(int(first_ordinal), int(last_ordinal))
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND ordinal >= ?
                  AND ordinal <= ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id, lower_bound, upper_bound, session_id, session_id),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def has_chat_canonical_message_before_ordinal(self, session_id: str, before_ordinal: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT 1
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND ordinal < ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                LIMIT 1
                ''',
                (session_id, int(before_ordinal), session_id, session_id),
            )
            return cursor.fetchone() is not None

    def get_chat_canonical_messages_since(self, session_id: str, since_ts: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND updated_at >= ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id, since_ts, session_id, session_id),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def get_chat_message_deletions_since(self, session_id: str, since_ts: str) -> List[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT message_id, canonical_message_id
                FROM chat_message_deletions
                WHERE session_id = ?
                  AND deleted_at >= ?
                ORDER BY deleted_at ASC
                ''',
                (session_id, since_ts),
            )
            deleted_ids: list[str] = []
            seen: set[str] = set()
            for row in cursor.fetchall():
                for key in ("message_id", "canonical_message_id"):
                    deleted_id = str(row[key] or "").strip()
                    if deleted_id and deleted_id not in seen:
                        seen.add(deleted_id)
                        deleted_ids.append(deleted_id)
            return deleted_ids

    def get_chat_canonical_max_version(self, session_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COALESCE(MAX(version), 0) AS max_version FROM chat_canonical_messages WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return int(row["max_version"]) if row else 0

    def _hydrate_chat_canonical_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["nodes"] = json.loads(data["nodes_json"]) if data.get("nodes_json") else []
        data["artifacts"] = json.loads(data["artifacts_json"]) if data.get("artifacts_json") else []
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        return data
