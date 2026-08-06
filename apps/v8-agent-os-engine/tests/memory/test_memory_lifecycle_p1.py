from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agents import memory_agent
from core import knowledge_db as knowledge_db_module
from core.knowledge_db import KnowledgeDB
from core import memory_store as memory_store_module


def test_evidence_scores_usage_and_tombstone_audit_are_persistent(tmp_path: Path) -> None:
    database = KnowledgeDB(tmp_path / "knowledge.db")
    first = database.write_knowledge(
        fact="Agent 浏览器登录态只保存在专用 profile",
        category="security",
        scope="project:v8",
        source_session="session-live",
        source_run="run-1",
        source_message_ids=["message-1"],
        transcript_hash="transcript-1",
        confidence=0.72,
        importance=81,
        durability="stable",
        evidence_refs=["message:message-1", "run:run-1"],
    )
    database.write_knowledge(
        fact="Agent 浏览器登录态只保存在专用 profile",
        category="security",
        scope="project:v8",
        relation="reinforce",
        target_fact_id=str(first["factId"]),
        source_session="session-live",
        source_run="run-2",
        source_message_ids=["message-2"],
        transcript_hash="transcript-2",
        confidence=0.91,
        importance=90,
        durability="stable",
        evidence_refs=["message:message-2", "run:run-2"],
    )

    assert database.mark_knowledge_injected([str(first["factId"])]) == 1
    assert database.delete_knowledge(
        str(first["factId"]),
        actor="human_admin",
        reason="user_requested_cleanup",
        evidence_refs=["admin-action:delete"],
    )

    with database._conn() as conn:
        row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (first["factId"],)).fetchone()
        observations = conn.execute(
            "SELECT confidence, importance, durability, evidence_refs_json FROM knowledge_observations WHERE fact_id = ? ORDER BY created_at",
            (first["factId"],),
        ).fetchall()
        audit = conn.execute(
            "SELECT action, actor, reason, evidence_refs_json FROM knowledge_lifecycle_audit WHERE fact_id = ? ORDER BY created_at DESC LIMIT 1",
            (first["factId"],),
        ).fetchone()

    assert row["status"] == "deleted"
    assert row["lifecycle_state"] == "tombstoned"
    assert row["usage_count"] == 1
    assert row["last_injected_at"]
    assert row["last_verified_at"]
    assert row["confidence"] == 0.91
    assert row["importance"] == 90
    assert len(observations) == 2
    assert observations[0]["confidence"] == 0.72
    assert observations[0]["importance"] == 81
    assert observations[0]["durability"] == "stable"
    assert "message:message-1" in observations[0]["evidence_refs_json"]
    assert audit["action"] == "tombstoned"
    assert audit["actor"] == "human_admin"
    assert audit["reason"] == "user_requested_cleanup"
    assert "admin-action:delete" in audit["evidence_refs_json"]


def test_graph_relation_accepts_independent_evidence_and_survives_fact_tombstone(tmp_path: Path) -> None:
    database = KnowledgeDB(tmp_path / "knowledge.db")
    fact = database.write_knowledge(
        fact="Creative Media 使用受控画布",
        scope="project:media",
        fact_id="fact-media",
    )
    relation_id = database.add_scoped_relation(
        "creative media",
        "USES",
        "canvas",
        scope="project:media",
        source_fact_ids=[str(fact["factId"])],
        evidence_refs=["artifact:creative-media-contract"],
    )

    assert database.delete_knowledge(str(fact["factId"]), actor="test", reason="fixture")
    results = database.query_entity("creative media", scopes=["project:media"])

    assert results and results[0]["object"] == "canvas"
    with database._conn() as conn:
        relation = conn.execute("SELECT status FROM scoped_relations WHERE id = ?", (relation_id,)).fetchone()
        external = conn.execute(
            "SELECT evidence_ref FROM scoped_relation_evidence_refs WHERE relation_id = ?",
            (relation_id,),
        ).fetchall()
    assert relation["status"] == "active"
    assert [row["evidence_ref"] for row in external] == ["artifact:creative-media-contract"]


def test_cleanup_plan_is_review_only_and_covers_all_requested_reasons(tmp_path: Path) -> None:
    database = KnowledgeDB(tmp_path / "knowledge.db")
    old_time = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat().replace("+00:00", "Z")
    unused = database.write_knowledge(
        fact="很久没有使用的低证据知识",
        scope="project:old",
        source_session="session-missing",
        confidence=0.3,
        evidence_refs=["message:single-low-confidence-source"],
        fact_id="fact-unused",
    )
    current = database.write_knowledge(fact="旧规则", scope="global", fact_id="fact-old")
    database.write_knowledge(
        fact="新规则",
        scope="global",
        relation="replace",
        target_fact_id=str(current["factId"]),
        fact_id="fact-new",
    )
    with database._conn() as conn:
        conn.execute(
            "UPDATE knowledge SET created_at = ?, last_seen_at = ? WHERE id = ?",
            (old_time, old_time, unused["factId"]),
        )

    plan = database.create_cleanup_plan(
        existing_session_ids={"session-existing"},
        unused_days=180,
        low_evidence_confidence=0.55,
    )

    by_id = {item["factId"]: item for item in plan["candidates"]}
    assert set(by_id["fact-unused"]["reasons"]) == {"unused", "low_evidence", "source_session_missing"}
    assert "superseded" in by_id["fact-old"]["reasons"]
    assert plan["summary"]["destructiveActions"] == 0
    with database._conn() as conn:
        assert conn.execute("SELECT status FROM knowledge WHERE id = 'fact-unused'").fetchone()[0] == "active"
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_cleanup_candidates WHERE plan_id = ?",
            (plan["planId"],),
        ).fetchone()[0] == len(plan["candidates"])


def test_memory_agent_derives_evidence_refs_from_canonical_sources() -> None:
    result = memory_agent.MemoryExtractionResult(
        summary="memory evidence",
        tags=["memory"],
        knowledge=[
            memory_agent.KnowledgeExtraction(
                fact="证据引用必须来自 canonical source",
                category="architecture",
                scope="global",
                confidence=0.8,
                importance=70,
                durability="stable",
            )
        ],
    )
    captured: dict = {}

    def _write(**kwargs):
        captured.update(kwargs)
        return {"factId": "fact-evidence", "canonicalFactId": "fact-evidence", "action": "new"}

    policy = {
        "knowledge_importance_threshold": 0,
        "knowledge_confidence_threshold": 0,
        "global_knowledge_importance_threshold": 0,
        "global_knowledge_confidence_threshold": 0,
        "global_operational_importance_threshold": 0,
        "global_operational_confidence_threshold": 0,
    }
    with patch.object(memory_agent.memory_runtime, "write_knowledge", side_effect=_write):
        stored, _ = memory_agent._store_knowledge(
            result,
            "session-1",
            policy,
            source_run="run-1",
            source_message_ids=["message-1", "message-2"],
            transcript_hash="transcript-1",
        )

    assert stored == 1
    assert captured["evidence_refs"] == [
        "message:message-1",
        "message:message-2",
        "run:run-1",
        "session:session-1",
    ]


def test_memory_agent_only_allows_stale_replacement_during_explicit_correction_mode() -> None:
    result = memory_agent.MemoryExtractionResult(
        summary="explicit correction",
        tags=["memory"],
        knowledge=[
            memory_agent.KnowledgeExtraction(
                fact="LangChain 使用 1.x，禁止低版本依赖",
                category="dependency_rule",
                scope="workspace:test8",
                relation="replace",
                target_fact_id="fact-stale-rule",
                confidence=0.9,
                importance=80,
                durability="stable",
            )
        ],
    )
    captured: list[dict] = []

    def _write(**kwargs):  # noqa: ANN003
        captured.append(kwargs)
        return {"factId": "fact-new", "canonicalFactId": "fact-new", "action": "replace"}

    policy = {
        "knowledge_importance_threshold": 0,
        "knowledge_confidence_threshold": 0,
        "global_knowledge_importance_threshold": 0,
        "global_knowledge_confidence_threshold": 0,
        "global_operational_importance_threshold": 0,
        "global_operational_confidence_threshold": 0,
    }
    with patch.object(memory_agent.memory_runtime, "write_knowledge", side_effect=_write):
        memory_agent._store_knowledge(result, "session-1", policy, allow_stale_replace=False)
        memory_agent._store_knowledge(result, "session-1", policy, allow_stale_replace=True)

    assert captured[0]["allow_stale_target"] is False
    assert captured[1]["allow_stale_target"] is True


def test_document_replacement_persists_evidence_and_lifecycle_audit(tmp_path: Path) -> None:
    database = KnowledgeDB(tmp_path / "knowledge.db")
    first = database.replace_user_document_chunks(
        filename="guide.md",
        chunks=[{"id": "document-old", "fact": "旧版说明"}],
        maintainer_source="document_upload",
        confidence=0.88,
        promotion_reason="user_upload",
    )
    second = database.replace_user_document_chunks(
        filename="guide.md",
        chunks=[{"id": "document-new", "fact": "新版说明"}],
        maintainer_source="document_upload",
        confidence=0.92,
        promotion_reason="user_replace",
    )

    assert first["created"] == ["document-old"]
    assert second == {"removed": ["document-old"], "created": ["document-new"]}
    with database._conn() as conn:
        old_row = conn.execute("SELECT status, lifecycle_state FROM knowledge WHERE id = 'document-old'").fetchone()
        new_row = conn.execute("SELECT evidence_refs_json FROM knowledge WHERE id = 'document-new'").fetchone()
        audits = conn.execute(
            "SELECT fact_id, action, reason FROM knowledge_lifecycle_audit ORDER BY created_at, id"
        ).fetchall()
        queued_remove = conn.execute(
            "SELECT COUNT(*) FROM knowledge_projection_outbox WHERE fact_id = 'document-old' AND operation = 'remove'"
        ).fetchone()[0]

    assert tuple(old_row) == ("deleted", "tombstoned")
    assert "document:guide.md" in new_row["evidence_refs_json"]
    assert ("document-old", "tombstoned", "document_replace") in [tuple(row) for row in audits]
    assert ("document-new", "created", "document_ingest") in [tuple(row) for row in audits]
    assert queued_remove == 1


def test_legacy_sources_backfill_factual_evidence_and_verification_time(tmp_path: Path) -> None:
    path = tmp_path / "legacy-evidence.db"
    database = KnowledgeDB(path)
    with database._conn() as conn:
        conn.execute(
            """
            INSERT INTO knowledge
            (id, fact, category, scope, status, source_session, lifecycle_state, evidence_refs_json)
            VALUES ('legacy-fact', '旧知识证据', 'architecture', 'global', 'active', 'session-legacy', 'active', '[]')
            """
        )
        conn.executemany(
            """
            INSERT INTO knowledge_observations
            (id, fact_id, raw_fact, relation, source_session, source_run, source_message_ids_json,
             confidence, importance, durability, evidence_refs_json, transcript_hash, created_at)
            VALUES (?, 'legacy-fact', '旧知识证据', 'reinforce', 'session-legacy', 'run-legacy',
                    '["message-legacy"]', 0.8, 70, 'stable', '[]', ?, ?)
            """,
            [
                ("legacy-observation-1", "legacy-hash-1", "2026-01-01T00:00:00Z"),
                ("legacy-observation-2", "legacy-hash-2", "2026-01-02T00:00:00Z"),
            ],
        )

    migrated = KnowledgeDB(path)
    with migrated._conn() as conn:
        fact = conn.execute(
            "SELECT evidence_refs_json, last_verified_at FROM knowledge WHERE id = 'legacy-fact'"
        ).fetchone()
        observation = conn.execute(
            "SELECT evidence_refs_json FROM knowledge_observations WHERE id = 'legacy-observation-1'"
        ).fetchone()

    assert "message:message-legacy" in fact["evidence_refs_json"]
    assert "run:run-legacy" in fact["evidence_refs_json"]
    assert "session:session-legacy" in fact["evidence_refs_json"]
    assert fact["last_verified_at"] == "2026-01-02T00:00:00Z"
    assert observation["evidence_refs_json"] == fact["evidence_refs_json"]


def test_actual_unified_recall_records_injection_usage(monkeypatch) -> None:
    store = memory_store_module.MemoryStore()
    captured: list[str] = []
    monkeypatch.setattr(
        store,
        "_execute_unified_recall",
        lambda **_kwargs: {"accepted_items": [{"id": "fact-used", "fact": "已注入"}]},
    )
    monkeypatch.setattr(
        knowledge_db_module.knowledge_db,
        "mark_knowledge_injected",
        lambda fact_ids, **_kwargs: captured.extend(fact_ids) or len(fact_ids),
    )

    result = store.unified_recall("测试注入")

    assert result == [{"id": "fact-used", "fact": "已注入"}]
    assert captured == ["fact-used"]
