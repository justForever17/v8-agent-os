from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from core.knowledge_db import KnowledgeDB, knowledge_db
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


logger = logging.getLogger("v8_agent_os.knowledge_projection")
VECTOR_BATCH_SIZE = 16


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _scope_path(scope: str) -> Path:
    return _scope_path_for_root(scope, V8_AGENT_OS_HOME / "memory")


def _scope_path_for_root(scope: str, memory_root: Path) -> Path:
    normalized = str(scope or "global").strip() or "global"
    areas = Path(memory_root) / "knowledge" / "areas"
    if ":" in normalized:
        prefix, value = normalized.split(":", 1)
        token = value.strip().replace(":", "__").replace("/", "_").replace("\\", "_") or "default"
        folder = {
            "project": "projects",
            "channel": "channels",
            "workspace": "workspaces",
            "external_api_thread": "external_api_threads",
        }.get(prefix)
        if folder:
            return areas / folder / token / "items.json"
    return areas / "general" / "items.json"


def _json_value(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class KnowledgeProjectionService:
    """Projects canonical SQLite knowledge into JSON and Chroma.

    Projection failure is durable in the outbox and never rolls back or mutates
    canonical knowledge lifecycle.
    """

    def __init__(self, db: KnowledgeDB = knowledge_db, *, memory_root: Optional[Path] = None):
        self.db = db
        self.memory_root = Path(memory_root) if memory_root is not None else None
        self._root_vector_store = None

    def _get_vector_store(self):
        if self.memory_root is None:
            from core.vector_store import get_vector_store

            return get_vector_store()
        if self._root_vector_store is None:
            from core.vector_store import VectorStore

            self._root_vector_store = VectorStore(db_dir=self.memory_root / ".index" / "chroma_db")
        return self._root_vector_store

    def close(self) -> None:
        if self._root_vector_store is None:
            return
        close = getattr(self._root_vector_store, "close", None)
        if callable(close):
            close()
        self._root_vector_store = None

    def _atomic_write_scope(self, scope: str) -> None:
        with self.db._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge
                WHERE scope = ?
                ORDER BY created_at ASC, revision_no ASC, id ASC
                """,
                (scope,),
            ).fetchall()
        path = (
            _scope_path_for_root(scope, self.memory_root)
            if self.memory_root is not None
            else _scope_path(scope)
        )
        if not rows:
            path.unlink(missing_ok=True)
            return
        items = []
        for row in rows:
            item = dict(row)
            item["evidence_refs"] = _json_value(item.pop("evidence_refs_json", None), [])
            item["metadata"] = _json_value(item.pop("metadata_json", None), {})
            item["timestamp"] = item.get("created_at") or item.get("updated_at")
            items.append(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(items, indent=2, ensure_ascii=False)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def rebuild_json(self) -> Dict[str, int]:
        with self.db._conn() as conn:
            scopes = [
                str(row["scope"] or "global")
                for row in conn.execute("SELECT DISTINCT scope FROM knowledge ORDER BY scope").fetchall()
            ]
        for scope in scopes:
            self._atomic_write_scope(scope)
        return {"scopeCount": len(scopes)}

    def _project_vector(self, row: Optional[Dict[str, Any]], *, fact_id: str, operation: str) -> None:
        vector_store = self._get_vector_store()
        if operation == "remove" or row is None:
            vector_store.delete_by_ids([fact_id])
            return
        lifecycle = str(row.get("lifecycle_state") or "active").strip().lower()
        status = str(row.get("status") or "active").strip().lower()
        if status != "active" or lifecycle in {"stale", "tombstoned", "superseded", "quarantined"}:
            vector_store.delete_by_ids([fact_id])
            return
        ids = vector_store.add_documents(
            [
                {
                    "id": fact_id,
                    "text": str(row.get("fact") or ""),
                    "metadata": {
                        "category": str(row.get("category") or "general"),
                        "scope": str(row.get("scope") or "global"),
                        "lineage_id": str(row.get("lineage_id") or fact_id),
                        "revision_no": int(row.get("revision_no") or 1),
                    },
                }
            ]
        )
        if fact_id not in list(ids or []):
            raise RuntimeError("vector projection returned no persisted fact id")

    @staticmethod
    def _is_vector_active(row: Optional[Dict[str, Any]]) -> bool:
        if row is None:
            return False
        lifecycle = str(row.get("lifecycle_state") or "active").strip().lower()
        status = str(row.get("status") or "active").strip().lower()
        return status == "active" and lifecycle not in {
            "stale",
            "tombstoned",
            "superseded",
            "quarantined",
        }

    @staticmethod
    def _vector_document(row: Dict[str, Any]) -> Dict[str, Any]:
        fact_id = str(row.get("id") or "")
        return {
            "id": fact_id,
            "text": str(row.get("fact") or ""),
            "metadata": {
                "category": str(row.get("category") or "general"),
                "scope": str(row.get("scope") or "global"),
                "lineage_id": str(row.get("lineage_id") or fact_id),
                "revision_no": int(row.get("revision_no") or 1),
            },
        }

    def process_outbox(self, *, limit: int = 50) -> Dict[str, Any]:
        effective_limit = max(1, min(int(limit or 50), 500))
        now = _utc_now_iso()
        with self.db._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE knowledge_projection_outbox
                SET status = 'retry', updated_at = ?
                WHERE status = 'processing'
                  AND datetime(updated_at) < datetime('now', '-5 minutes')
                """,
                (now,),
            )
            rows = conn.execute(
                """
                SELECT * FROM knowledge_projection_outbox
                WHERE status IN ('queued', 'retry')
                  AND (next_attempt_at IS NULL OR datetime(next_attempt_at) <= datetime('now'))
                ORDER BY id ASC
                LIMIT ?
                """,
                (effective_limit,),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                conn.execute(
                    f"UPDATE knowledge_projection_outbox SET status = 'processing', updated_at = ? WHERE id IN ({','.join('?' for _ in ids)})",
                    (now, *ids),
                )

        fact_ids = sorted({str(row["fact_id"]) for row in rows})
        payload_by_fact: Dict[str, Dict[str, Any]] = {}
        if fact_ids:
            placeholders = ",".join("?" for _ in fact_ids)
            with self.db._conn() as conn:
                payload_by_fact = {
                    str(row["id"]): dict(row)
                    for row in conn.execute(
                        f"SELECT * FROM knowledge WHERE id IN ({placeholders})",
                        fact_ids,
                    ).fetchall()
                }
        scope_by_event: Dict[int, str] = {}
        for queued in rows:
            payload = payload_by_fact.get(str(queued["fact_id"]))
            fallback_payload = _json_value(queued["payload_json"], {})
            scope_by_event[int(queued["id"])] = str(
                (payload or {}).get("scope") or fallback_payload.get("scope") or "global"
            )
        scope_errors: Dict[str, Exception] = {}
        for scope in sorted(set(scope_by_event.values())):
            try:
                self._atomic_write_scope(scope)
            except Exception as exc:
                scope_errors[scope] = exc

        vector_errors: Dict[str, Exception] = {}
        if rows:
            vector_store = self._get_vector_store()
            remove_ids = [fact_id for fact_id in fact_ids if not self._is_vector_active(payload_by_fact.get(fact_id))]
            if remove_ids:
                try:
                    vector_store.delete_by_ids(remove_ids)
                except Exception as exc:
                    for fact_id in remove_ids:
                        vector_errors[fact_id] = exc
            upsert_rows = [payload_by_fact[fact_id] for fact_id in fact_ids if self._is_vector_active(payload_by_fact.get(fact_id))]
            for offset in range(0, len(upsert_rows), VECTOR_BATCH_SIZE):
                batch_rows = upsert_rows[offset : offset + VECTOR_BATCH_SIZE]
                batch_ids = [str(row["id"]) for row in batch_rows]
                try:
                    persisted_ids = set(
                        str(item)
                        for item in list(
                            vector_store.add_documents([self._vector_document(row) for row in batch_rows]) or []
                        )
                    )
                    for fact_id in batch_ids:
                        if fact_id not in persisted_ids:
                            vector_errors[fact_id] = RuntimeError(
                                "vector projection returned no persisted fact id"
                            )
                except Exception as exc:
                    for fact_id in batch_ids:
                        vector_errors[fact_id] = exc

        completed = 0
        retried = 0
        dead_letter = 0
        for queued in rows:
            event_id = int(queued["id"])
            fact_id = str(queued["fact_id"])
            attempts = int(queued["attempts"] or 0) + 1
            try:
                scope = scope_by_event[event_id]
                if scope in scope_errors:
                    raise scope_errors[scope]
                if fact_id in vector_errors:
                    raise vector_errors[fact_id]
                with self.db._conn() as conn:
                    conn.execute(
                        """
                        UPDATE knowledge_projection_outbox
                        SET status = 'completed', attempts = ?, last_error = NULL,
                            next_attempt_at = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (attempts, _utc_now_iso(), event_id),
                    )
                    conn.execute(
                        """
                        UPDATE knowledge_projection_outbox
                        SET status = 'completed', last_error = 'recovered_by_later_projection',
                            next_attempt_at = NULL, updated_at = ?
                        WHERE fact_id = ? AND status = 'dead_letter' AND id < ?
                        """,
                        (_utc_now_iso(), fact_id, event_id),
                    )
                completed += 1
            except Exception as exc:
                terminal = attempts >= 8
                status = "dead_letter" if terminal else "retry"
                retry_at = None
                if not terminal:
                    retry_at = (
                        datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** min(attempts, 8)))
                    ).isoformat().replace("+00:00", "Z")
                with self.db._conn() as conn:
                    conn.execute(
                        """
                        UPDATE knowledge_projection_outbox
                        SET status = ?, attempts = ?, last_error = ?, next_attempt_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (status, attempts, str(exc)[:1000], retry_at, _utc_now_iso(), event_id),
                    )
                if terminal:
                    dead_letter += 1
                else:
                    retried += 1
                logger.warning("Knowledge projection %s degraded for %s: %s", event_id, fact_id, exc)
        return {
            "processed": len(rows),
            "completed": completed,
            "retry": retried,
            "deadLetter": dead_letter,
        }

    def enqueue_reconcile(self) -> int:
        batch_id = uuid.uuid4().hex[:12]
        with self.db._conn() as conn:
            rows = conn.execute("SELECT id, status, lifecycle_state FROM knowledge").fetchall()
            for row in rows:
                lifecycle = str(row["lifecycle_state"] or "active").strip().lower()
                active = str(row["status"] or "active") == "active" and lifecycle not in {
                    "stale",
                    "tombstoned",
                    "superseded",
                    "quarantined",
                }
                self.db._enqueue_projection(
                    conn,
                    fact_id=str(row["id"]),
                    operation="upsert" if active else "remove",
                    event_key=f"reconcile:{batch_id}:{row['id']}:{'upsert' if active else 'remove'}",
                )
            return len(rows)

    def reconcile_vectors(self) -> Dict[str, int]:
        vector_store = self._get_vector_store()
        collection = vector_store.collection
        if collection is None:
            raise RuntimeError("vector collection unavailable")
        current = collection.get(include=[])
        vector_ids = {str(item) for item in list((current or {}).get("ids") or [])}
        with self.db._conn() as conn:
            rows = conn.execute(
                """
                SELECT id FROM knowledge
                WHERE status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded', 'quarantined')
                """
            ).fetchall()
            canonical_ids = {str(row["id"]) for row in rows}
            missing = canonical_ids - vector_ids
            orphaned = vector_ids - canonical_ids
            batch_id = uuid.uuid4().hex[:12]
            for fact_id in missing:
                self.db._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="upsert",
                    event_key=f"vector-reconcile:{batch_id}:{fact_id}:upsert",
                )
        if orphaned:
            vector_store.delete_by_ids(sorted(orphaned))
        return {"missing": len(missing), "orphanedRemoved": len(orphaned)}

    @staticmethod
    def _projection_signature(item: Dict[str, Any]) -> tuple[str, str, str, str, int]:
        return (
            str(item.get("id") or ""),
            str(item.get("status") or "active"),
            str(item.get("lifecycle_state") or "active"),
            str(item.get("lineage_id") or item.get("id") or ""),
            int(item.get("revision_no") or 1),
        )

    def health(self, *, deep: bool = False) -> Dict[str, Any]:
        with self.db._conn() as conn:
            counts = {
                str(row["status"]): int(row["count"])
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM knowledge_projection_outbox GROUP BY status"
                ).fetchall()
            }
            pending = int(
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_resolution_candidates WHERE state = 'pending'"
                ).fetchone()[0]
            )
            canonical_rows = [dict(row) for row in conn.execute(
                "SELECT id, scope, status, lifecycle_state, lineage_id, revision_no FROM knowledge"
            ).fetchall()]
        backlog = sum(counts.get(key, 0) for key in ("queued", "processing", "retry"))
        active_ids = {
            str(row["id"])
            for row in canonical_rows
            if str(row.get("status") or "active") == "active"
            and str(row.get("lifecycle_state") or "active") == "active"
        }
        result: Dict[str, Any] = {
            "state": "degraded" if counts.get("dead_letter", 0) else ("syncing" if backlog else "ready"),
            "outbox": counts,
            "backlog": backlog,
            "pendingResolutionCount": pending,
            "canonical": {
                "total": len(canonical_rows),
                "active": len(active_ids),
            },
        }
        if not deep:
            return result

        by_scope: Dict[str, list[Dict[str, Any]]] = {}
        for row in canonical_rows:
            by_scope.setdefault(str(row.get("scope") or "global"), []).append(row)
        json_drift_scopes: list[str] = []
        expected_projection_paths: set[Path] = set()
        for scope, rows in by_scope.items():
            path = (
                _scope_path_for_root(scope, self.memory_root)
                if self.memory_root is not None
                else _scope_path(scope)
            )
            expected_projection_paths.add(path.resolve(strict=False))
            try:
                projected = json.loads(path.read_text(encoding="utf-8"))
                projected_items = [item for item in projected if isinstance(item, dict)]
            except Exception:
                projected_items = []
            canonical_signature = sorted(self._projection_signature(row) for row in rows)
            projected_signature = sorted(self._projection_signature(row) for row in projected_items)
            if canonical_signature != projected_signature:
                json_drift_scopes.append(scope)
        projection_root = (self.memory_root or (V8_AGENT_OS_HOME / "memory")) / "knowledge" / "areas"
        orphan_projection_count = 0
        if projection_root.exists():
            orphan_projection_count = sum(
                1
                for path in projection_root.rglob("items.json")
                if path.resolve(strict=False) not in expected_projection_paths
            )

        vector_state = "ready"
        vector_missing = 0
        vector_orphaned = 0
        try:
            vector_store = self._get_vector_store()
            if vector_store.collection is None:
                raise RuntimeError("vector collection unavailable")
            current = vector_store.collection.get(include=[])
            vector_ids = {str(item) for item in list((current or {}).get("ids") or [])}
            vector_missing = len(active_ids - vector_ids)
            vector_orphaned = len(vector_ids - active_ids)
            if vector_missing or vector_orphaned:
                vector_state = "drifted"
        except Exception:
            vector_state = "unavailable"

        if json_drift_scopes or orphan_projection_count or vector_state != "ready" or counts.get("dead_letter", 0):
            result["state"] = "degraded"
        elif backlog:
            result["state"] = "syncing"
        else:
            result["state"] = "ready"
        result["json"] = {
            "state": "drifted" if json_drift_scopes or orphan_projection_count else "ready",
            "driftScopeCount": len(json_drift_scopes),
            "orphanProjectionCount": orphan_projection_count,
        }
        result["vector"] = {
            "state": vector_state,
            "missing": vector_missing,
            "orphaned": vector_orphaned,
        }
        return result


knowledge_projection_service = KnowledgeProjectionService()
