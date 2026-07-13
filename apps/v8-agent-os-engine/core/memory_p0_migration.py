from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from core.knowledge_db import DB_PATH as KNOWLEDGE_DB_PATH
from core.knowledge_db import KnowledgeDB
from core.knowledge_projection import KnowledgeProjectionService
from core.storage_backup import StorageBackupService, sqlite_quick_check
from core.v8_agent_os_paths import (
    CHECKPOINT_DB_PATH,
    OBSERVABILITY_DB_PATH,
    STATE_DB_PATH,
    V8_AGENT_OS_HOME,
)


MIGRATION_VERSION = "memory-p0-v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iter_json_items(areas_dir: Path) -> Iterable[Dict[str, Any]]:
    if not areas_dir.exists():
        return
    for path in areas_dir.rglob("items.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict):
                yield item


def _legacy_json_lifecycle(item: Dict[str, Any]) -> str:
    """Normalize contradictory legacy projection fields conservatively."""
    status = str(item.get("status") or "").strip().lower()
    lifecycle = str(item.get("lifecycle_state") or "").strip().lower()
    if status in {"deleted", "tombstoned"}:
        return "tombstoned"
    if status == "quarantined":
        return "quarantined"
    if lifecycle in {"active", "stale", "tombstoned", "superseded", "quarantined"}:
        return lifecycle
    return "active"


class MemoryP0MigrationService:
    def __init__(
        self,
        *,
        knowledge_db_path: Path = KNOWLEDGE_DB_PATH,
        state_db_path: Path = STATE_DB_PATH,
        checkpoint_db_path: Path = CHECKPOINT_DB_PATH,
        observability_db_path: Path = OBSERVABILITY_DB_PATH,
        memory_root: Optional[Path] = None,
        backup_root: Optional[Path] = None,
    ):
        self.knowledge_db_path = Path(knowledge_db_path)
        self.state_db_path = Path(state_db_path)
        self.checkpoint_db_path = Path(checkpoint_db_path)
        self.observability_db_path = Path(observability_db_path)
        self.memory_root = memory_root or (V8_AGENT_OS_HOME / "memory")
        self.areas_dir = self.memory_root / "knowledge" / "areas"
        self.chroma_dir = self.memory_root / ".index" / "chroma_db"
        self.backups = StorageBackupService(root=backup_root or (V8_AGENT_OS_HOME / "backups"))

    def _journal(self, migration_id: str) -> Optional[Dict[str, Any]]:
        if not self.knowledge_db_path.exists():
            return None
        with closing(sqlite3.connect(self.knowledge_db_path, timeout=60)) as conn:
            conn.row_factory = sqlite3.Row
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_migration_journal'"
            ).fetchone():
                return None
            row = conn.execute(
                "SELECT * FROM memory_migration_journal WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _backup_result(manifest_path: str) -> Dict[str, Any]:
        path = Path(str(manifest_path or ""))
        if not path.exists():
            return {"state": "missing", "manifest": str(path)}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {**payload, "path": str(path.parent)}

    def _finalize_projection(
        self,
        *,
        db: KnowledgeDB,
        migration_id: str,
        plan_digest: str,
        detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        rebuilt_fts_rows = db.rebuild_fts()
        projection_service = KnowledgeProjectionService(db, memory_root=self.memory_root)
        projection_errors: list[str] = []
        try:
            try:
                json_projection = projection_service.rebuild_json()
            except Exception as exc:
                json_projection = {"scopeCount": 0}
                projection_errors.append(f"json:{exc}")
            try:
                vector_reconcile = projection_service.reconcile_vectors()
            except Exception as exc:
                vector_reconcile = {"missing": 0, "orphanedRemoved": 0}
                projection_errors.append(f"vector_reconcile:{exc}")
            projection_run = {
                "processed": 0,
                "completed": 0,
                "retry": 0,
                "deadLetter": 0,
                "batches": 0,
            }
            for _ in range(20):
                batch = projection_service.process_outbox(limit=500)
                projection_run["batches"] += 1
                for key in ("processed", "completed", "retry", "deadLetter"):
                    projection_run[key] += int(batch.get(key) or 0)
                if int(batch.get("processed") or 0) == 0:
                    break
            if projection_run["retry"] or projection_run["deadLetter"]:
                projection_errors.append(
                    f"outbox:retry={projection_run['retry']},dead_letter={projection_run['deadLetter']}"
                )
            health = projection_service.health()
            if str(health.get("state") or "ready") != "ready" and not projection_errors:
                projection_errors.append(f"projection_state:{health.get('state')}")
            projection: Dict[str, Any] = {
                **health,
                "json": json_projection,
                "ftsRows": rebuilt_fts_rows,
                "vectorReconcile": vector_reconcile,
                "run": projection_run,
                "errors": projection_errors,
            }
            if projection_errors:
                projection["state"] = "degraded"
        finally:
            projection_service.close()

        completed_detail = {**detail, "projectionState": projection.get("state", "ready")}
        with db._conn() as conn:
            conn.execute(
                """
                UPDATE memory_migration_journal
                SET state = 'completed', detail_json = ?, updated_at = ?
                WHERE migration_id = ? AND plan_digest = ? AND state = 'projecting'
                """,
                (
                    json.dumps(completed_detail, ensure_ascii=False),
                    _utc_now_iso(),
                    migration_id,
                    plan_digest,
                ),
            )
        return projection

    def _existing_ids(self) -> set[str]:
        if not self.knowledge_db_path.exists():
            return set()
        with closing(sqlite3.connect(self.knowledge_db_path, timeout=60)) as conn:
            return {str(row[0]) for row in conn.execute("SELECT id FROM knowledge").fetchall()}

    def plan(self) -> Dict[str, Any]:
        quick_checks = {
            str(path): sqlite_quick_check(path)
            for path in (
                self.state_db_path,
                self.checkpoint_db_path,
                self.observability_db_path,
                self.knowledge_db_path,
            )
            if path.exists()
        }
        active_superseded = []
        raw_merge_suggestions = []
        broken_links = []
        cycles = []
        cross_scope = []
        legacy_relations = 0
        if self.knowledge_db_path.exists():
            with closing(sqlite3.connect(self.knowledge_db_path, timeout=60)) as conn:
                conn.row_factory = sqlite3.Row
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(knowledge)").fetchall()}
                lifecycle = "lifecycle_state" if "lifecycle_state" in columns else "'active'"
                superseded = "superseded_by" if "superseded_by" in columns else "NULL"
                metadata = "metadata_json" if "metadata_json" in columns else "NULL"
                rows = conn.execute(
                    f"SELECT id, scope, updated_at, {lifecycle} AS lifecycle_state, {superseded} AS superseded_by, {metadata} AS metadata_json FROM knowledge"
                ).fetchall()
                by_id = {str(row["id"]): row for row in rows}
                for row in rows:
                    fact_id = str(row["id"])
                    target = str(row["superseded_by"] or "").strip()
                    if target and str(row["lifecycle_state"] or "active").strip().lower() == "active":
                        active_superseded.append(fact_id)
                    if target and target not in by_id:
                        broken_links.append({"factId": fact_id, "targetFactId": target})
                    if target and target in by_id and str(row["scope"] or "global") != str(by_id[target]["scope"] or "global"):
                        cross_scope.append({"factId": fact_id, "targetFactId": target})
                    seen = {fact_id}
                    current = target
                    while current:
                        if current in seen:
                            cycles.append({"factId": fact_id, "cycleAt": current})
                            break
                        seen.add(current)
                        next_row = by_id.get(current)
                        current = str(next_row["superseded_by"] or "").strip() if next_row else ""
                    try:
                        item_metadata = json.loads(row["metadata_json"] or "{}")
                    except Exception:
                        item_metadata = {}
                    suggestion = ((item_metadata.get("maintenance") or {}).get("mergeSuggestion") or {})
                    if suggestion.get("targetId"):
                        target_row = by_id.get(str(suggestion.get("targetId")))
                        raw_merge_suggestions.append(
                            {
                                "factId": fact_id,
                                "targetFactId": str(suggestion.get("targetId")),
                                "similarity": suggestion.get("similarity"),
                                "reason": suggestion.get("reason"),
                                "_sourceUpdatedAt": str(row["updated_at"] or ""),
                                "_targetUpdatedAt": str(target_row["updated_at"] or "") if target_row else "",
                            }
                        )
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='relations'").fetchone():
                    legacy_relations = int(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
        existing_ids = self._existing_ids()
        missing_json_items = [
            {**item, "importLifecycleState": _legacy_json_lifecycle(item)}
            for item in _iter_json_items(self.areas_dir)
            if str(item.get("id") or "").strip() and str(item.get("id")) not in existing_ids
        ]
        suggestions_by_pair: Dict[tuple[str, str], Dict[str, Any]] = {}
        for suggestion in raw_merge_suggestions:
            source_id = str(suggestion["factId"])
            target_id = str(suggestion["targetFactId"])
            pair = tuple(sorted((source_id, target_id)))
            current = suggestions_by_pair.get(pair)
            source_rank = (str(suggestion.get("_sourceUpdatedAt") or ""), source_id)
            current_rank = (
                str(current.get("_sourceUpdatedAt") or ""),
                str(current.get("factId") or ""),
            ) if current else ("", "")
            if current is None or source_rank > current_rank:
                suggestions_by_pair[pair] = suggestion
        merge_suggestions = []
        for suggestion in suggestions_by_pair.values():
            cleaned = dict(suggestion)
            cleaned.pop("_sourceUpdatedAt", None)
            cleaned.pop("_targetUpdatedAt", None)
            merge_suggestions.append(cleaned)
        merge_suggestions.sort(key=lambda item: (str(item["factId"]), str(item["targetFactId"])))
        digest_payload = {
            "version": MIGRATION_VERSION,
            "activeSuperseded": sorted(active_superseded),
            "missingJsonItems": sorted(
                (
                    str(item.get("id")),
                    str(item.get("importLifecycleState") or "active"),
                )
                for item in missing_json_items
            ),
            "mergeSuggestions": merge_suggestions,
            "brokenLinks": broken_links,
            "cycles": cycles,
            "crossScope": cross_scope,
            "legacyRelations": legacy_relations,
        }
        digest = hashlib.sha256(
            json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "version": MIGRATION_VERSION,
            "planDigest": digest,
            "quickChecks": quick_checks,
            "safeToApply": all(value == "ok" for value in quick_checks.values()) and not broken_links and not cycles and not cross_scope,
            "activeSupersededIds": active_superseded,
            "missingJsonItems": missing_json_items,
            "mergeSuggestions": merge_suggestions,
            "brokenLinks": broken_links,
            "cycles": cycles,
            "crossScopeLinks": cross_scope,
            "legacyArchivedRelationCount": legacy_relations,
        }

    def apply(
        self,
        *,
        idempotency_key: Optional[str] = None,
        expected_plan_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        expected_digest = str(expected_plan_digest or "").strip()
        migration_id = idempotency_key or f"memory-p0-{uuid.uuid4().hex[:12]}"
        existing = self._journal(migration_id)
        if existing:
            stored_digest = str(existing.get("plan_digest") or "")
            if expected_digest and expected_digest != stored_digest:
                raise RuntimeError("idempotency key reused with a different plan")
            state = str(existing.get("state") or "")
            if state == "completed":
                if expected_digest and expected_digest != stored_digest:
                    raise RuntimeError("idempotency key reused with a different plan")
                return {
                    "status": "completed",
                    "migrationId": migration_id,
                    "planDigest": stored_digest,
                    "idempotent": True,
                }
            if state == "projecting":
                try:
                    detail = json.loads(existing.get("detail_json") or "{}")
                except Exception:
                    detail = {}
                db = KnowledgeDB(self.knowledge_db_path)
                projection = self._finalize_projection(
                    db=db,
                    migration_id=migration_id,
                    plan_digest=stored_digest,
                    detail=detail,
                )
                return {
                    "status": "completed",
                    "migrationId": migration_id,
                    "planDigest": stored_digest,
                    "backup": self._backup_result(str(existing.get("backup_manifest_path") or "")),
                    **detail,
                    "projection": projection,
                    "resumed": True,
                }
            raise RuntimeError("unfinished memory migration requires rollback before retry")
        plan = self.plan()
        if not plan["safeToApply"]:
            raise RuntimeError("memory migration plan is not safe to apply")
        if expected_digest and expected_digest != str(plan["planDigest"]):
            raise RuntimeError("memory migration plan changed after review")
        directories = [self.areas_dir, self.chroma_dir]
        backup = self.backups.create_backup(
            purpose="memory-p0",
            sqlite_paths=(
                self.state_db_path,
                self.checkpoint_db_path,
                self.observability_db_path,
                self.knowledge_db_path,
            ),
            directory_paths=directories,
            plan_digest=str(plan["planDigest"]),
            backup_id=migration_id,
        )
        db = KnowledgeDB(self.knowledge_db_path)
        manifest_path = str(Path(str(backup["path"])) / "manifest.json")
        with db._conn() as conn:
            existing = conn.execute(
                "SELECT state, plan_digest FROM memory_migration_journal WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            if existing and str(existing["state"]) == "completed":
                if str(existing["plan_digest"]) != str(plan["planDigest"]):
                    raise RuntimeError("idempotency key reused with a different plan")
                return {"status": "completed", "migrationId": migration_id, "idempotent": True}
            now = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO memory_migration_journal (
                    migration_id, plan_digest, state, backup_manifest_path,
                    detail_json, created_at, updated_at
                ) VALUES (?, ?, 'applying', ?, ?, ?, ?)
                ON CONFLICT(migration_id) DO UPDATE SET
                    state = 'applying', backup_manifest_path = excluded.backup_manifest_path,
                    detail_json = excluded.detail_json, updated_at = excluded.updated_at
                """,
                (migration_id, plan["planDigest"], manifest_path, json.dumps(plan, ensure_ascii=False), now, now),
            )
            for fact_id in plan["activeSupersededIds"]:
                row = conn.execute("SELECT rowid, superseded_by FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                if not row:
                    continue
                conn.execute(
                    "UPDATE knowledge SET lifecycle_state = 'superseded', valid_to = COALESCE(valid_to, updated_at) WHERE id = ?",
                    (fact_id,),
                )
                conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (row["rowid"],))
                db._deactivate_unsupported_relations(conn, fact_id=fact_id)
                event_id = "migration-event-" + hashlib.sha256(
                    f"{migration_id}:{fact_id}:repair_reactivated_superseded".encode("utf-8")
                ).hexdigest()[:16]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_migration_events (
                        id, migration_id, fact_id, event_type, detail_json, created_at
                    ) VALUES (?, ?, ?, 'repair_reactivated_superseded', ?, ?)
                    """,
                    (event_id, migration_id, fact_id, json.dumps({"supersededBy": row["superseded_by"]}, ensure_ascii=False), now),
                )
                db._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="remove",
                    event_key=f"migration:{migration_id}:remove:{fact_id}",
                )
            for suggestion in plan["mergeSuggestions"]:
                candidate_id = "resolution-" + hashlib.sha256(
                    f"{suggestion['factId']}:{suggestion['targetFactId']}:migration".encode("utf-8")
                ).hexdigest()[:16]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_resolution_candidates (
                        id, candidate_fact_id, target_fact_id, proposed_relation,
                        state, similarity, reason, created_at
                    ) VALUES (?, ?, ?, 'reinforce', 'pending', ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        suggestion["factId"],
                        suggestion["targetFactId"],
                        suggestion.get("similarity"),
                        suggestion.get("reason") or "legacy_merge_suggestion",
                        now,
                    ),
                )

        imported = 0
        for item in plan["missingJsonItems"]:
            fact_id = str(item.get("id") or "").strip()
            fact = str(item.get("fact") or "").strip()
            if not fact_id or not fact:
                continue
            db.add_knowledge(
                fact_id=fact_id,
                fact=fact,
                category=str(item.get("category") or "general"),
                scope=str(item.get("scope") or "global"),
                source_session=item.get("source_session"),
                lifecycle_state=str(item.get("importLifecycleState") or _legacy_json_lifecycle(item)),
                maintainer_source=str(item.get("maintainer_source") or "legacy_json_import"),
                confidence=float(item.get("confidence") or 1.0),
                evidence_refs=list(item.get("evidence_refs") or []),
                metadata=dict(item.get("metadata") or {}),
            )
            imported += 1
        detail = {
            "repaired": len(plan["activeSupersededIds"]),
            "imported": imported,
            "pendingResolutions": len(plan["mergeSuggestions"]),
            "legacyArchivedRelations": plan["legacyArchivedRelationCount"],
        }
        with db._conn() as conn:
            conn.execute(
                """
                UPDATE memory_migration_journal
                SET state = 'projecting', detail_json = ?, updated_at = ?
                WHERE migration_id = ? AND plan_digest = ? AND state = 'applying'
                """,
                (
                    json.dumps(detail, ensure_ascii=False),
                    _utc_now_iso(),
                    migration_id,
                    plan["planDigest"],
                ),
            )
        projection = self._finalize_projection(
            db=db,
            migration_id=migration_id,
            plan_digest=str(plan["planDigest"]),
            detail=detail,
        )
        return {
            "status": "completed",
            "migrationId": migration_id,
            "planDigest": plan["planDigest"],
            "backup": backup,
            "repaired": len(plan["activeSupersededIds"]),
            "imported": imported,
            "pendingResolutions": len(plan["mergeSuggestions"]),
            "legacyArchivedRelations": plan["legacyArchivedRelationCount"],
            "projection": projection,
        }

    def rollback(self, *, manifest_path: Path, offline: bool) -> Dict[str, Any]:
        return self.backups.restore_backup(manifest_path, offline=offline)


memory_p0_migration_service = MemoryP0MigrationService()
