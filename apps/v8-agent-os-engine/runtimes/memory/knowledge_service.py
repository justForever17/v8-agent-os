from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.knowledge_db import knowledge_db
from core.knowledge_projection import knowledge_projection_service
from core.memory.store import memory_store


class KnowledgeService:
    """封装知识 CRUD、FTS 与图谱统计。"""

    @staticmethod
    def _projection_state(fact_id: str) -> str:
        with knowledge_db._conn() as conn:
            row = conn.execute(
                """
                SELECT status FROM knowledge_projection_outbox
                WHERE fact_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (fact_id,),
            ).fetchone()
        if not row or str(row["status"]) == "completed":
            return "ready"
        if str(row["status"]) == "dead_letter":
            return "degraded"
        return "queued"

    def write_knowledge(
        self,
        *,
        fact: str,
        category: str = "general",
        scope: str = "global",
        relation: str = "new",
        target_fact_id: Optional[str] = None,
        source_session: Optional[str] = None,
        source_run: Optional[str] = None,
        source_message_ids: Optional[List[str]] = None,
        transcript_hash: Optional[str] = None,
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
        importance: int = 50,
        durability: str = "operational",
        evidence_refs: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
        promotion_reason: Optional[str] = None,
        metadata: Optional[Dict] = None,
        fact_id: Optional[str] = None,
        lineage_id_override: Optional[str] = None,
        revision_no_override: Optional[int] = None,
    ) -> Dict[str, object]:
        normalized_scope = memory_store._validate_scope(scope)
        signature = memory_store._current_soft_signature() if memory_store._scope_uses_repo_signature(normalized_scope) else {}
        result = knowledge_db.write_knowledge(
            fact=fact,
            category=category,
            scope=normalized_scope,
            relation=relation,
            target_fact_id=target_fact_id,
            source_session=source_session,
            source_run=source_run,
            source_message_ids=source_message_ids,
            transcript_hash=transcript_hash,
            maintainer_source=maintainer_source,
            confidence=confidence,
            importance=importance,
            durability=durability,
            evidence_refs=evidence_refs,
            parent_id=parent_id,
            agents_hash=str(signature.get("agentsHash") or ""),
            repo_signature=str(signature.get("repoSignature") or ""),
            signature_policy=str(signature.get("signaturePolicy") or "soft_v1"),
            promotion_reason=promotion_reason,
            metadata=metadata,
            fact_id=fact_id,
            lineage_id_override=lineage_id_override,
            revision_no_override=revision_no_override,
        )
        knowledge_projection_service.process_outbox(limit=10)
        result["projectionState"] = self._projection_state(str(result["factId"]))
        return result

    def query_knowledge(
        self,
        *,
        query: Optional[str] = None,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        return memory_store.query_knowledge(
            query=query,
            scope=scope,
            scopes=scopes,
            category=category,
            limit=limit,
        )

    def add_knowledge(
        self,
        *,
        fact: str,
        category: str = "general",
        scope: str = "global",
        source_session: Optional[str] = None,
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
        importance: int = 50,
        durability: str = "operational",
        source_run: Optional[str] = None,
        source_message_ids: Optional[List[str]] = None,
        transcript_hash: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> str:
        result = self.write_knowledge(
            fact=fact,
            category=category,
            scope=scope,
            relation="new",
            source_session=source_session,
            source_run=source_run,
            source_message_ids=source_message_ids,
            transcript_hash=transcript_hash,
            maintainer_source=maintainer_source,
            confidence=confidence,
            importance=importance,
            durability=durability,
            evidence_refs=evidence_refs,
        )
        return str(result["factId"])

    def update_knowledge(
        self,
        *,
        fact_id: str,
        new_fact: str,
        category: Optional[str] = None,
        scope: Optional[str] = None,
        maintainer_source: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> bool:
        with knowledge_db._conn() as conn:
            current = conn.execute("SELECT * FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
        if not current:
            return False
        result = self.write_knowledge(
            fact=new_fact,
            category=category or str(current["category"] or "general"),
            scope=scope or str(current["scope"] or "global"),
            relation="replace",
            target_fact_id=fact_id,
            maintainer_source=maintainer_source or str(current["maintainer_source"] or "memory_runtime"),
            confidence=confidence if confidence is not None else float(current["confidence"] or 1.0),
            importance=int(current["importance"] or 50),
            durability=str(current["durability"] or "operational"),
            metadata={"deprecatedOverwriteId": fact_id},
        )
        return bool(result.get("factId"))

    def delete_knowledge(self, *, fact_id: str) -> bool:
        deleted = knowledge_db.delete_knowledge(fact_id)
        if deleted:
            knowledge_projection_service.process_outbox(limit=10)
        return deleted

    def restore_knowledge(self, *, fact_id: str) -> bool:
        with knowledge_db._conn() as conn:
            source = conn.execute("SELECT * FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
            if not source:
                return False
            lineage_id = str(source["lineage_id"] or source["id"])
            current = conn.execute(
                """
                SELECT * FROM knowledge
                WHERE lineage_id = ? AND status = 'active'
                  AND COALESCE(lifecycle_state, 'active') = 'active'
                ORDER BY revision_no DESC LIMIT 1
                """,
                (lineage_id,),
            ).fetchone()
            max_revision = int(
                conn.execute(
                    "SELECT COALESCE(MAX(revision_no), 0) FROM knowledge WHERE lineage_id = ?",
                    (lineage_id,),
                ).fetchone()[0]
            )
        target_id = str(current["id"]) if current else None
        relation = "replace" if target_id else "new"
        result = self.write_knowledge(
            fact=str(source["fact"]),
            category=str(source["category"] or "general"),
            scope=str(source["scope"] or "global"),
            relation=relation,
            target_fact_id=target_id,
            maintainer_source="human_admin",
            confidence=float(source["confidence"] or 1.0),
            importance=int(source["importance"] or 50),
            durability=str(source["durability"] or "operational"),
            metadata={"rollbackFromFactId": fact_id},
            lineage_id_override=lineage_id if not target_id else None,
            revision_no_override=max_revision + 1 if not target_id else None,
        )
        return bool(result.get("factId"))

    def revalidate_knowledge(self, *, fact_id: str, maintainer_source: str = "human_admin") -> bool:
        return memory_store.revalidate_knowledge(fact_id, maintainer_source=maintainer_source)

    def search_full_text(
        self,
        *,
        query: str,
        scope: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        return knowledge_db.fts_search(query, scope=scope, limit=limit)

    def list_recent_knowledge(
        self,
        *,
        scope: Optional[str] = None,
        limit: int = 50,
        status: str = "active",
    ) -> List[Dict]:
        normalized_status = str(status or "active").strip().lower() or "active"
        if normalized_status == "active":
            memory_store.refresh_stale_revalidation(scopes=[scope] if scope else None)
        return knowledge_db.get_all_knowledge(scope=scope, limit=limit, status=normalized_status)

    def get_knowledge_count(self) -> int:
        return knowledge_db.get_knowledge_count()

    def get_graph_stats(self) -> Dict:
        return knowledge_db.get_graph_stats()

    def get_full_graph(self, *, limit: int = 100) -> Dict:
        return knowledge_db.get_full_graph(limit=limit)

    def get_projection_health(self) -> Dict:
        return knowledge_projection_service.health(deep=True)

    def list_resolution_candidates(self, *, limit: int = 100) -> List[Dict]:
        with knowledge_db._conn() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.proposed_relation, c.state, c.similarity, c.reason,
                       c.created_at, c.resolved_at, c.resolution,
                       candidate.id AS candidate_fact_id, candidate.fact AS candidate_fact,
                       candidate.scope AS candidate_scope, candidate.category AS candidate_category,
                       candidate.maintainer_source AS candidate_maintainer_source,
                       candidate.source_session AS candidate_source_session,
                       (SELECT COUNT(*) FROM knowledge_observations observation
                        WHERE observation.fact_id = candidate.id) AS source_observation_count,
                       target.id AS target_fact_id, target.fact AS target_fact,
                       target.scope AS target_scope, target.category AS target_category
                FROM knowledge_resolution_candidates c
                JOIN knowledge candidate ON candidate.id = c.candidate_fact_id
                LEFT JOIN knowledge target ON target.id = c.target_fact_id
                WHERE c.state = 'pending'
                ORDER BY c.created_at ASC
                LIMIT ?
                """,
                (max(1, min(int(limit or 100), 500)),),
            ).fetchall()
        items: List[Dict] = []
        for row in rows:
            item = dict(row)
            source = str(item.get("candidate_maintainer_source") or "memory_runtime").lower()
            category = str(item.get("candidate_category") or "").lower()
            if "human" in source:
                source_kind = "human_edit"
            elif category == "user_document" or "document" in source:
                source_kind = "document"
            elif "network" in source:
                source_kind = "network"
            elif "migration" in source or "legacy" in source:
                source_kind = "historical_migration"
            else:
                source_kind = "conversation"
            item["source_kind"] = source_kind
            items.append(item)
        return items

    def resolve_candidate(self, *, candidate_id: str, resolution: str) -> Dict[str, object]:
        normalized = str(resolution or "").strip().lower()
        if normalized not in {"reinforce", "replace", "refine", "discard"}:
            raise ValueError("resolution must be reinforce, replace, refine, or discard")
        with knowledge_db._conn() as conn:
            row = conn.execute(
                """
                SELECT c.*, candidate.fact, candidate.category, candidate.scope,
                       candidate.confidence, candidate.importance, candidate.durability,
                       candidate.lifecycle_state, candidate.source_session
                FROM knowledge_resolution_candidates c
                JOIN knowledge candidate ON candidate.id = c.candidate_fact_id
                WHERE c.id = ? AND c.state = 'pending'
                """,
                (candidate_id,),
            ).fetchone()
            observation = conn.execute(
                """
                SELECT source_session, source_run, source_message_ids_json,
                       evidence_refs_json, transcript_hash
                FROM knowledge_observations
                WHERE fact_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(row["candidate_fact_id"]),),
            ).fetchone() if row else None
        if not row:
            raise ValueError("pending resolution candidate not found")
        candidate_fact_id = str(row["candidate_fact_id"])
        target_fact_id = str(row["target_fact_id"] or "").strip() or None
        source_message_ids: List[str] = []
        evidence_refs: List[str] = []
        if observation:
            try:
                source_message_ids = list(json.loads(observation["source_message_ids_json"] or "[]"))
            except Exception:
                source_message_ids = []
            try:
                evidence_refs = list(json.loads(observation["evidence_refs_json"] or "[]"))
            except Exception:
                evidence_refs = []
        source_session = (
            str(observation["source_session"] or "").strip() if observation else ""
        ) or str(row["source_session"] or "").strip() or None
        source_run = str(observation["source_run"] or "").strip() if observation else ""
        transcript_hash = str(observation["transcript_hash"] or "").strip() if observation else ""
        result: Dict[str, object] = {"resolution": normalized, "candidateId": candidate_id}
        if normalized == "reinforce":
            if not target_fact_id:
                raise ValueError("reinforce requires a target fact")
            write = self.write_knowledge(
                fact=str(row["fact"]),
                category=str(row["category"] or "general"),
                scope=str(row["scope"] or "global"),
                relation="reinforce",
                target_fact_id=target_fact_id,
                source_session=source_session,
                source_run=source_run or None,
                source_message_ids=source_message_ids,
                transcript_hash=transcript_hash or f"resolution:{candidate_id}",
                maintainer_source="human_admin",
                confidence=float(row["confidence"] or 1.0),
                importance=int(row["importance"] or 50),
                durability=str(row["durability"] or "operational"),
                evidence_refs=evidence_refs,
                metadata={"resolvedCandidateId": candidate_id},
            )
            superseded = knowledge_db.mark_knowledge_superseded(
                candidate_fact_id,
                target_fact_id,
                reason="human_resolution_reinforce",
            )
            if not superseded and str(row["lifecycle_state"] or "").strip().lower() != "superseded":
                raise RuntimeError("failed to retire reinforced candidate")
            result.update(write)
        elif normalized in {"replace", "refine"}:
            if not target_fact_id:
                raise ValueError(f"{normalized} requires a target fact")
            deterministic_fact_id = "fact-" + hashlib.sha256(
                f"resolution:{candidate_id}:{normalized}".encode("utf-8")
            ).hexdigest()[:12]
            write = self.write_knowledge(
                fact=str(row["fact"]),
                category=str(row["category"] or "general"),
                scope=str(row["scope"] or "global"),
                relation=normalized,
                target_fact_id=target_fact_id,
                source_session=source_session,
                source_run=source_run or None,
                source_message_ids=source_message_ids,
                transcript_hash=transcript_hash or f"resolution:{candidate_id}",
                maintainer_source="human_admin",
                confidence=float(row["confidence"] or 1.0),
                importance=int(row["importance"] or 50),
                durability=str(row["durability"] or "operational"),
                evidence_refs=evidence_refs,
                metadata={"resolvedCandidateId": candidate_id},
                fact_id=deterministic_fact_id,
            )
            if str(row["lifecycle_state"] or "").strip().lower() == "quarantined":
                knowledge_db.delete_knowledge(candidate_fact_id)
            result.update(write)
        else:
            knowledge_db.delete_knowledge(candidate_fact_id)
        with knowledge_db._conn() as conn:
            conn.execute(
                """
                UPDATE knowledge_resolution_candidates
                SET state = 'resolved', resolution = ?, resolved_at = ?
                WHERE id = ? AND state = 'pending'
                """,
                (normalized, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), candidate_id),
            )
        knowledge_projection_service.process_outbox(limit=20)
        return result

    def query_entity(self, *, entity: str, scopes: Optional[List[str]] = None) -> List[Dict]:
        return knowledge_db.query_entity(entity, scopes=scopes)

    def add_entity(
        self,
        *,
        name: str,
        entity_type: str = "concept",
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
    ) -> None:
        knowledge_db.add_entity(
            name,
            entity_type,
            maintainer_source=maintainer_source,
            confidence=confidence,
        )

    def delete_entity(self, *, name: str, scope: Optional[str] = None) -> bool:
        return knowledge_db.delete_entity(name, scope=scope)

    def add_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_name: str,
        scope: str,
        source_fact_ids: List[str],
        confidence: float = 1.0,
        maintainer_source: str = "memory_runtime",
    ) -> None:
        knowledge_db.add_scoped_relation(
            subject,
            predicate,
            object_name,
            scope=scope,
            source_fact_ids=source_fact_ids,
            confidence=confidence,
            maintainer_source=maintainer_source,
        )

    def delete_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_name: str,
        scope: Optional[str] = None,
    ) -> bool:
        return knowledge_db.delete_relation(subject, predicate, object_name, scope=scope)

    def query_multi_hop(
        self,
        *,
        entity: str,
        hops: int = 2,
        scopes: Optional[List[str]] = None,
    ) -> List[Dict]:
        return knowledge_db.multi_hop_query(entity, hops, scopes=scopes)

    def search_entities(self, *, keyword: str, limit: int = 20) -> List[Dict]:
        return knowledge_db.search_entities(keyword, limit)


knowledge_service = KnowledgeService()
