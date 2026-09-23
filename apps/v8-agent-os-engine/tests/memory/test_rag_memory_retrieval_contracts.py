from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import core.knowledge_db as knowledge_db_module
import core.storage as storage_module
import core.vector_store as vector_store_module
from core.memory_store import MemoryStore
from core.vector_store import VectorStore
from graph.supervisor_context import condense_passive_memory_query
from graph.supervisor_execution import _mark_materialized_memory_blocks


class _NullConnection:
    def execute(self, *_args: Any, **_kwargs: Any) -> "_NullConnection":
        return self

    def fetchone(self) -> None:
        return None

    def __enter__(self) -> "_NullConnection":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _FakeKnowledgeDB:
    def __init__(self, fts_search: Callable[[str, str | None, int], list[dict[str, Any]]]) -> None:
        self._fts_search = fts_search
        self.fts_calls: list[dict[str, Any]] = []
        self.mark_calls: list[dict[str, Any]] = []

    def fts_search(self, query: str, scope: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self.fts_calls.append({"query": query, "scope": scope, "limit": limit})
        return self._fts_search(query, scope, limit)

    @contextmanager
    def _conn(self):
        yield _NullConnection()

    def mark_knowledge_injected(self, fact_ids: list[str], *, verified: bool = False) -> int:
        self.mark_calls.append({"fact_ids": list(fact_ids), "verified": verified})
        return len(fact_ids)


def _fts_row(
    fact_id: str,
    *,
    scope: str,
    fact: str,
    relevance: float = -1.0,
    evidence_refs_json: str | None = None,
) -> dict[str, Any]:
    row = {
        "id": fact_id,
        "fact": fact,
        "category": "engineering",
        "scope": scope,
        "relevance": relevance,
    }
    if evidence_refs_json is not None:
        row["evidence_refs_json"] = evidence_refs_json
    return row


def _vector_row(
    fact_id: str,
    *,
    scope: str,
    fact: str,
    relevance_score: float = 0.82,
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "text": fact,
        "metadata": {"scope": scope, "category": "engineering"},
        "relevance_score": relevance_score,
    }


def _isolated_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: dict[str, Any],
    knowledge_db: _FakeKnowledgeDB,
    vector_store: Any | None = None,
    vector_enabled: bool = False,
) -> MemoryStore:
    monkeypatch.setattr(knowledge_db_module, "knowledge_db", knowledge_db)
    monkeypatch.setattr(storage_module.storage, "get_memory_config", lambda: config)
    monkeypatch.setattr(
        "core.runtime.startup_profile.optional_capability_enabled",
        lambda capability: vector_enabled and capability == "vector_memory",
    )
    if vector_store is not None:
        monkeypatch.setattr(vector_store_module, "get_vector_store", lambda: vector_store)

    store = object.__new__(MemoryStore)
    monkeypatch.setattr(store, "_is_injectable_knowledge", lambda _fact_id: True)
    return store


def test_balanced_recall_preserves_vector_and_fts_rank_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    shared_fact = "V8OS 的 memory broker 是高影响记忆的验证入口。"
    knowledge_db = _FakeKnowledgeDB(
        lambda _query, _scope, _limit: [
            _fts_row("fact-shared", scope="workspace:alpha", fact=shared_fact, relevance=-1.0),
        ]
    )
    vector_store = SimpleNamespace(
        similarity_search_with_rerank=lambda _query, **_kwargs: [
            _vector_row("fact-shared", scope="workspace:alpha", fact=shared_fact),
        ]
    )
    store = _isolated_store(
        monkeypatch,
        config={
            "recall_strategy": "balanced",
            "fts_enabled": True,
            "graph_enabled": False,
            "retrieval_threshold": 0.05,
            "recall_top_k": 3,
            "rerank_enabled": False,
        },
        knowledge_db=knowledge_db,
        vector_store=vector_store,
        vector_enabled=True,
    )

    preview = store._execute_unified_recall(
        query="memory broker verification",
        limit=1,
        scope="workspace:alpha",
        scopes=["workspace:alpha"],
        allow_rerank=False,
    )

    item = next(item for item in preview["items"] if item["id"] == "fact-shared")
    assert {source for source in item["source"].split("+") if source} == {"fts5", "vector"}

    channel_ranks = item.get("channel_ranks")
    channel_scores = item.get("channel_scores")
    assert isinstance(channel_ranks, dict), "merged candidates must retain per-channel ranks"
    assert isinstance(channel_scores, dict), "merged candidates must retain per-channel scores"
    assert set(channel_ranks) == {"fts5", "vector"}
    assert channel_ranks["fts5"] == 1
    assert channel_ranks["vector"] == 1
    assert channel_scores["fts5"] > 0
    assert channel_scores["vector"] > 0
    assert item["fusion_score"] > 0
    assert preview["diagnostics"]["fusion_strategy"] == "rrf"
    assert set(preview["diagnostics"]["fusion_channels"]) == {"fts5", "vector"}


def test_unified_recall_applies_fts_scope_before_candidate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _fts_row(
        "fact-alpha-target",
        scope="workspace:alpha",
        fact="workspace alpha 的部署入口是 Engine 9530。",
    )
    global_noise = [
        _fts_row(
            f"global-noise-{index}",
            scope="global",
            fact=f"global noise {index} also mentions deployment",
        )
        for index in range(8)
    ]

    def scoped_search(_query: str, scope: str | None, limit: int) -> list[dict[str, Any]]:
        if scope == "workspace:alpha":
            return [target]
        if scope == "global":
            return global_noise[:limit]
        return global_noise[:limit]

    knowledge_db = _FakeKnowledgeDB(scoped_search)
    store = _isolated_store(
        monkeypatch,
        config={
            "recall_strategy": "keyword",
            "fts_enabled": True,
            "graph_enabled": False,
            "retrieval_threshold": 0.05,
            "recall_top_k": 1,
            "rerank_enabled": False,
        },
        knowledge_db=knowledge_db,
    )

    preview = store._execute_unified_recall(
        query="deployment",
        limit=1,
        scope="workspace:alpha",
        scopes=["workspace:alpha"],
        allow_rerank=False,
    )

    assert any(item["id"] == target["id"] for item in preview["accepted_items"])
    assert knowledge_db.fts_calls
    assert all(call["scope"] is not None for call in knowledge_db.fts_calls)
    assert any(call["scope"] == "workspace:alpha" for call in knowledge_db.fts_calls)


def test_vector_no_reranker_fallback_reports_unavailable_score_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Collection:
        def query(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "documents": [["semantic memory fact"]],
                "ids": [["fact-vector"]],
                "metadatas": [[{"scope": "global", "category": "engineering"}]],
            }

    vector_store = object.__new__(VectorStore)
    vector_store.collection = _Collection()
    vector_store.embedding_model = SimpleNamespace(embed_query=lambda _query: [0.1, 0.2])
    vector_store.reranker_model = None

    results = vector_store.similarity_search_with_rerank("semantic memory", top_k=1, fetch_k=3)

    assert len(results) == 1
    result = results[0]
    assert result["score_available"] is False
    assert result["score_source"] == "missing_distance"
    assert result["relevance_score"] is None

    knowledge_db = _FakeKnowledgeDB(lambda _query, _scope, _limit: [])
    store = _isolated_store(
        monkeypatch,
        config={
            "recall_strategy": "semantic",
            "fts_enabled": True,
            "graph_enabled": False,
            "retrieval_threshold": 0.05,
            "recall_top_k": 1,
            "rerank_enabled": False,
        },
        knowledge_db=knowledge_db,
        vector_store=SimpleNamespace(similarity_search_with_rerank=lambda _query, **_kwargs: results),
        vector_enabled=True,
    )

    preview = store._execute_unified_recall(
        query="semantic memory",
        limit=1,
        scope="global",
        scopes=["global"],
        allow_rerank=False,
    )

    assert preview["accepted_items"] == []
    assert preview["diagnostics"]["vector_degraded"] is True
    assert "missing_distance" in preview["diagnostics"]["vector_degraded_reasons"]


def test_unified_recall_does_not_mark_model_seen_before_provider_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    fact = _fts_row(
        "fact-retrieved",
        scope="global",
        fact="This fact was retrieved but has not yet been shown to a model.",
    )
    knowledge_db = _FakeKnowledgeDB(lambda _query, _scope, _limit: [fact])
    store = _isolated_store(
        monkeypatch,
        config={
            "recall_strategy": "keyword",
            "fts_enabled": True,
            "graph_enabled": False,
            "retrieval_threshold": 0.05,
            "recall_top_k": 1,
            "rerank_enabled": False,
        },
        knowledge_db=knowledge_db,
    )

    preview = store.preview_unified_recall("retrieved fact", limit=1, scope="global", scopes=["global"])
    assert preview["accepted_items"]
    assert knowledge_db.mark_calls == []

    accepted = store.unified_recall("retrieved fact", limit=1, scope="global", scopes=["global"])

    assert accepted
    assert knowledge_db.mark_calls == [], (
        "retrieval/accepted must not be recorded as model_seen; "
        "the marker belongs after final provider-message materialization"
    )


def test_passive_query_condensation_preserves_prior_entity_for_continuity_question() -> None:
    query, diagnostics = condense_passive_memory_query(
        [
            HumanMessage(content="我们讨论过 Mercury migration 的回滚方案。"),
            HumanMessage(content="它后来怎么处理？"),
        ],
        "它后来怎么处理？",
    )

    assert diagnostics["rewrite_applied"] is True
    assert diagnostics["rewrite_reason"] == "continuity_context"
    assert "Mercury migration" in query
    assert "它后来怎么处理" in query


def test_passive_query_condensation_leaves_self_contained_query_unchanged() -> None:
    query, diagnostics = condense_passive_memory_query(
        [HumanMessage(content="请查找 RFC-2026-017 的当前状态。")],
        "请查找 RFC-2026-017 的当前状态。",
    )

    assert query == "请查找 RFC-2026-017 的当前状态。"
    assert diagnostics["rewrite_applied"] is False
    assert diagnostics["rewrite_reason"] == "not_needed"


def test_passive_query_condensation_does_not_match_it_inside_an_english_word() -> None:
    query, diagnostics = condense_passive_memory_query(
        [HumanMessage(content="We discussed the deployment target for Engine 9530.")],
        "Write the deployment note for Engine 9530.",
    )

    assert query == "Write the deployment note for Engine 9530."
    assert diagnostics["rewrite_applied"] is False


def test_memory_usage_is_marked_only_after_memory_block_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    knowledge_db = _FakeKnowledgeDB(lambda _query, _scope, _limit: [])
    monkeypatch.setattr(knowledge_db_module, "knowledge_db", knowledge_db)

    _mark_materialized_memory_blocks(
        [
            SystemMessage(
                content="unrelated",
                additional_kwargs={"context_block": {"type": "unrelated", "metadata": {"memory_ids": ["ignored"]}}},
            ),
            SystemMessage(
                content="memory",
                additional_kwargs={
                    "context_block": {
                        "type": "memory_recall",
                        "metadata": {"memory_ids": ["fact-b", "fact-a", "fact-b"]},
                    }
                },
            ),
        ]
    )

    assert knowledge_db.mark_calls == [{"fact_ids": ["fact-a", "fact-b"], "verified": False}]
