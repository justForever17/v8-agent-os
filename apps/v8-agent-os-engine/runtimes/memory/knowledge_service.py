from __future__ import annotations

from typing import Dict, List, Optional

from core.knowledge_db import knowledge_db
from core.memory.store import memory_store


class KnowledgeService:
    """封装知识 CRUD、FTS 与图谱统计。"""

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
    ) -> str:
        return memory_store.add_knowledge(
            fact=fact,
            category=category,
            scope=scope,
            source_session=source_session,
            maintainer_source=maintainer_source,
            confidence=confidence,
        )

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
        return memory_store.update_knowledge(
            fact_id=fact_id,
            new_fact=new_fact,
            category=category,
            scope=scope,
            maintainer_source=maintainer_source,
            confidence=confidence,
        )

    def delete_knowledge(self, *, fact_id: str) -> bool:
        deleted = memory_store.delete_knowledge(fact_id)
        if deleted:
            knowledge_db.hard_delete_knowledge(fact_id)
        return deleted

    def restore_knowledge(self, *, fact_id: str) -> bool:
        return knowledge_db.set_knowledge_status(fact_id, "active")

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

    def query_entity(self, *, entity: str) -> List[Dict]:
        return knowledge_db.query_entity(entity)

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

    def delete_entity(self, *, name: str) -> bool:
        return knowledge_db.delete_entity(name)

    def add_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_name: str,
        confidence: float = 1.0,
        maintainer_source: str = "memory_runtime",
    ) -> None:
        knowledge_db.add_relation(
            subject,
            predicate,
            object_name,
            confidence=confidence,
            maintainer_source=maintainer_source,
        )

    def delete_relation(self, *, subject: str, predicate: str, object_name: str) -> bool:
        return knowledge_db.delete_relation(subject, predicate, object_name)

    def query_multi_hop(self, *, entity: str, hops: int = 2) -> List[Dict]:
        return knowledge_db.multi_hop_query(entity, hops)

    def search_entities(self, *, keyword: str, limit: int = 20) -> List[Dict]:
        return knowledge_db.search_entities(keyword, limit)


knowledge_service = KnowledgeService()
