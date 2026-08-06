from __future__ import annotations

import json
import importlib
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.knowledge_db import KnowledgeDB
from core.knowledge_projection import KnowledgeProjectionService
from core.memory_store import MemoryStore
from core.memory_p0_migration import MemoryP0MigrationService
from core.storage_backup import StorageBackupService
from runtimes.memory.knowledge_service import KnowledgeService


def test_write_contract_reinforces_idempotently_and_replaces_with_revision(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    first = db.write_knowledge(
        fact="默认使用严格验收",
        category="project_rule",
        scope="project:demo",
        source_session="session-1",
        transcript_hash="transcript-1",
    )
    reinforced = db.write_knowledge(
        fact="默认使用严格验收",
        category="project_rule",
        scope="project:demo",
        source_session="session-1",
        transcript_hash="transcript-1",
    )
    replacement = db.write_knowledge(
        fact="默认使用分层验收",
        category="project_rule",
        scope="project:demo",
        relation="replace",
        target_fact_id=str(first["factId"]),
        source_session="session-2",
        transcript_hash="transcript-2",
    )

    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, fact, lifecycle_state, superseded_by, lineage_id, revision_no FROM knowledge ORDER BY revision_no"
        ).fetchall()
        observation_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_observations").fetchone()[0])

    assert reinforced["action"] == "reinforce"
    assert reinforced["canonicalFactId"] == first["factId"]
    assert replacement["replacedFactId"] == first["factId"]
    assert rows[0]["lifecycle_state"] == "superseded"
    assert rows[0]["superseded_by"] == replacement["factId"]
    assert rows[1]["lineage_id"] == rows[0]["lineage_id"]
    assert rows[1]["revision_no"] == 2
    assert observation_count == 2
    assert [item["id"] for item in db.fts_search("分层", scope="project:demo")] == [replacement["factId"]]
    assert db.fts_search("严格", scope="project:demo") == []


def test_explicit_revision_can_supersede_a_stale_target_but_default_write_cannot(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    original = db.write_knowledge(
        fact="LangChain 固定使用 0.x",
        category="dependency_rule",
        scope="workspace:test8",
        fact_id="fact-langchain-old",
    )
    with db._conn() as conn:
        conn.execute(
            "UPDATE knowledge SET lifecycle_state = 'stale' WHERE id = ?",
            (original["factId"],),
        )

    with pytest.raises(ValueError, match="not the current canonical fact"):
        db.write_knowledge(
            fact="LangChain 使用 1.x，禁止使用低版本依赖",
            category="dependency_rule",
            scope="workspace:test8",
            relation="replace",
            target_fact_id=str(original["factId"]),
        )

    replacement = db.write_knowledge(
        fact="LangChain 使用 1.x，禁止使用低版本依赖",
        category="dependency_rule",
        scope="workspace:test8",
        relation="replace",
        target_fact_id=str(original["factId"]),
        allow_stale_target=True,
        maintainer_source="human_admin",
        evidence_refs=["message:explicit-correction"],
    )

    with db._conn() as conn:
        old_row = conn.execute(
            "SELECT lifecycle_state, superseded_by FROM knowledge WHERE id = ?",
            (original["factId"],),
        ).fetchone()
        new_row = conn.execute(
            "SELECT lineage_id, revision_no, metadata_json FROM knowledge WHERE id = ?",
            (replacement["factId"],),
        ).fetchone()
        audit = conn.execute(
            "SELECT reason, metadata_json FROM knowledge_lifecycle_audit "
            "WHERE fact_id = ? AND action = 'superseded' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (original["factId"],),
        ).fetchone()

    assert old_row["lifecycle_state"] == "superseded"
    assert old_row["superseded_by"] == replacement["factId"]
    assert new_row["revision_no"] == 2
    assert json.loads(new_row["metadata_json"])["replacedStaleTarget"] is True
    assert audit["reason"] == "knowledge_replace_stale_target"
    assert json.loads(audit["metadata_json"])["replacedStaleTarget"] is True


def test_stale_revision_cannot_bypass_a_newer_canonical_lineage_fact(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    original = db.write_knowledge(
        fact="旧规则",
        category="rule",
        scope="workspace:test8",
        fact_id="fact-old",
    )
    db.write_knowledge(
        fact="当前规则",
        category="rule",
        scope="workspace:test8",
        relation="replace",
        target_fact_id=str(original["factId"]),
        fact_id="fact-current",
    )
    with db._conn() as conn:
        # Simulate an imported/legacy row labelled stale after a newer revision
        # already became canonical.  It must not be allowed to fork the lineage.
        conn.execute(
            "UPDATE knowledge SET lifecycle_state = 'stale' WHERE id = ?",
            (original["factId"],),
        )

    with pytest.raises(ValueError, match="newer canonical revision"):
        db.write_knowledge(
            fact="错误地从旧规则再分叉",
            category="rule",
            scope="workspace:test8",
            relation="replace",
            target_fact_id=str(original["factId"]),
            allow_stale_target=True,
        )


def test_concurrent_exact_writes_create_one_fact_and_three_observations(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")

    def write(index: int) -> str:
        result = db.write_knowledge(
            fact="并发写入必须归并为同一条知识",
            category="architecture",
            scope="project:v8",
            source_run=f"run-{index}",
            transcript_hash=f"transcript-{index}",
        )
        return str(result["canonicalFactId"])

    with ThreadPoolExecutor(max_workers=3) as pool:
        canonical_ids = list(pool.map(write, range(3)))

    with db._conn() as conn:
        fact_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE scope = 'project:v8' AND lifecycle_state = 'active'"
            ).fetchone()[0]
        )
        observation_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_observations").fetchone()[0])
    assert len(set(canonical_ids)) == 1
    assert fact_count == 1
    assert observation_count == 3


def test_maintenance_exact_dedupe_moves_observations_to_canonical_fact(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    db.add_knowledge(
        "fact-older",
        "同义知识必须保留全部来源证据",
        category="rule",
        scope="project:v8",
        source_session="session-older",
        confidence=0.8,
        evidence_refs=["message:older"],
    )
    db.add_knowledge(
        "fact-newer",
        "同义知识必须保留全部来源证据",
        category="rule",
        scope="project:v8",
        source_session="session-newer",
        confidence=0.9,
        evidence_refs=["message:newer"],
    )
    with db._conn() as conn:
        db._record_observation(
            conn,
            fact_id="fact-older",
            raw_fact="同义知识必须保留全部来源证据",
            relation="new",
            source_session="session-older",
            source_run="run-older",
            source_message_ids=["message-older"],
            confidence=0.8,
            importance=60,
            durability="durable",
            evidence_refs=["message:older"],
            transcript_hash="transcript-older",
            created_at="2026-01-01T00:00:00+00:00",
        )
        db._record_observation(
            conn,
            fact_id="fact-newer",
            raw_fact="同义知识必须保留全部来源证据",
            relation="new",
            source_session="session-newer",
            source_run="run-newer",
            source_message_ids=["message-newer"],
            confidence=0.9,
            importance=50,
            durability="operational",
            evidence_refs=["message:newer"],
            transcript_hash="transcript-newer",
            created_at="2026-01-02T00:00:00+00:00",
        )

    result = db.maintenance_compact_knowledge(limit=20)

    with db._conn() as conn:
        facts = conn.execute(
            "SELECT id, lifecycle_state, superseded_by, importance, evidence_refs_json FROM knowledge ORDER BY id"
        ).fetchall()
        observations = conn.execute(
            "SELECT fact_id, source_run, transcript_hash, created_at FROM knowledge_observations ORDER BY transcript_hash"
        ).fetchall()
    canonical = next(row for row in facts if row["lifecycle_state"] == "active")
    retired = next(row for row in facts if row["lifecycle_state"] == "superseded")
    canonical_observations = [row for row in observations if row["fact_id"] == canonical["id"]]

    assert result["supersededCount"] == 1
    assert retired["superseded_by"] == canonical["id"]
    assert {
        "transcript-newer",
        "transcript-older",
    }.issubset({row["transcript_hash"] for row in canonical_observations})
    assert next(row for row in canonical_observations if row["transcript_hash"] == "transcript-older")["created_at"] == (
        "2026-01-01T00:00:00+00:00"
    )
    assert canonical["importance"] == 60
    assert set(json.loads(canonical["evidence_refs_json"])) == {"message:newer", "message:older"}


def test_conflict_is_quarantined_and_cross_scope_replace_is_rejected(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    current = db.write_knowledge(fact="端口为 9530", category="config", scope="project:a")
    conflict = db.write_knowledge(
        fact="端口可能为 9531",
        category="config",
        scope="project:a",
        relation="conflict",
        target_fact_id=str(current["factId"]),
    )
    with db._conn() as conn:
        row = conn.execute("SELECT status, lifecycle_state FROM knowledge WHERE id = ?", (conflict["factId"],)).fetchone()
        pending = int(conn.execute("SELECT COUNT(*) FROM knowledge_resolution_candidates WHERE state = 'pending'").fetchone()[0])
    assert row["status"] == "quarantined"
    assert row["lifecycle_state"] == "quarantined"
    assert pending == 1
    assert db.fts_search("9531", scope="project:a") == []

    with pytest.raises(ValueError, match="cross scope"):
        db.write_knowledge(
            fact="跨项目错误更新",
            category="config",
            scope="project:b",
            relation="replace",
            target_fact_id=str(current["factId"]),
        )


def test_human_resolution_reinforce_moves_evidence_and_retires_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    current = db.write_knowledge(fact="默认端口为 9530", category="config", scope="project:v8")
    conflict = db.write_knowledge(
        fact="默认端口仍然是 9530",
        category="config",
        scope="project:v8",
        relation="conflict",
        target_fact_id=str(current["factId"]),
        source_session="session-evidence",
        source_run="run-evidence",
        source_message_ids=["message-evidence"],
        transcript_hash="transcript-evidence",
        evidence_refs=["message:message-evidence"],
    )

    class ProjectionStub:
        def process_outbox(self, *, limit: int):
            return {"processed": limit}

    class MemoryStoreStub:
        @staticmethod
        def _validate_scope(scope: str) -> str:
            return scope

        @staticmethod
        def _scope_uses_repo_signature(_scope: str) -> bool:
            return False

        @staticmethod
        def _current_soft_signature():
            return {}

    service_module = importlib.import_module("runtimes.memory.knowledge_service")

    monkeypatch.setattr(service_module, "knowledge_db", db)
    monkeypatch.setattr(service_module, "knowledge_projection_service", ProjectionStub())
    monkeypatch.setattr(service_module, "memory_store", MemoryStoreStub())
    resolved = KnowledgeService().resolve_candidate(
        candidate_id=str(conflict["resolutionCandidateId"]),
        resolution="reinforce",
    )

    with db._conn() as conn:
        target_observations = conn.execute(
            "SELECT source_run, source_message_ids_json, evidence_refs_json FROM knowledge_observations WHERE fact_id = ?",
            (str(current["factId"]),),
        ).fetchall()
        candidate = conn.execute(
            "SELECT lifecycle_state, superseded_by FROM knowledge WHERE id = ?",
            (str(conflict["factId"]),),
        ).fetchone()
        resolution_state = conn.execute(
            "SELECT state, resolution FROM knowledge_resolution_candidates WHERE id = ?",
            (str(conflict["resolutionCandidateId"]),),
        ).fetchone()

    assert resolved["canonicalFactId"] == current["factId"]
    assert len(target_observations) == 2
    assert target_observations[-1]["source_run"] == "run-evidence"
    assert json.loads(target_observations[-1]["source_message_ids_json"]) == ["message-evidence"]
    assert json.loads(target_observations[-1]["evidence_refs_json"]) == ["message:message-evidence"]
    assert candidate["lifecycle_state"] == "superseded"
    assert candidate["superseded_by"] == current["factId"]
    assert dict(resolution_state) == {"state": "resolved", "resolution": "reinforce"}


def test_restore_tombstone_creates_new_revision_in_same_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    original = db.write_knowledge(fact="用户偏好简洁输出", scope="global")
    assert db.delete_knowledge(str(original["factId"])) is True

    class ProjectionStub:
        def process_outbox(self, *, limit: int):
            return {"processed": limit}

    class MemoryStoreStub:
        @staticmethod
        def _validate_scope(scope: str) -> str:
            return scope

        @staticmethod
        def _scope_uses_repo_signature(_scope: str) -> bool:
            return False

        @staticmethod
        def _current_soft_signature():
            return {}

    service_module = importlib.import_module("runtimes.memory.knowledge_service")
    monkeypatch.setattr(service_module, "knowledge_db", db)
    monkeypatch.setattr(service_module, "knowledge_projection_service", ProjectionStub())
    monkeypatch.setattr(service_module, "memory_store", MemoryStoreStub())

    assert KnowledgeService().restore_knowledge(fact_id=str(original["factId"])) is True
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, lineage_id, revision_no, lifecycle_state FROM knowledge ORDER BY revision_no"
        ).fetchall()
    assert [row["revision_no"] for row in rows] == [1, 2]
    assert rows[0]["lineage_id"] == rows[1]["lineage_id"] == original["lineageId"]
    assert rows[0]["lifecycle_state"] == "tombstoned"
    assert rows[1]["lifecycle_state"] == "active"


def test_memory_store_compatibility_entry_never_writes_json_or_vector_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    projection_calls: list[int] = []

    class ProjectionStub:
        def process_outbox(self, *, limit: int):
            projection_calls.append(limit)
            return {"processed": limit}

    import core.knowledge_db as knowledge_db_module
    import core.knowledge_projection as projection_module
    import core.memory_store as memory_store_module

    memory_root = tmp_path / "memory"
    monkeypatch.setattr(memory_store_module, "MEMORY_ROOT", memory_root)
    monkeypatch.setattr(memory_store_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(knowledge_db_module, "knowledge_db", db)
    monkeypatch.setattr(projection_module, "knowledge_projection_service", ProjectionStub())
    store = MemoryStore()
    monkeypatch.setattr(
        store,
        "_sync_vector_store_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("direct vector write")),
    )

    fact_id = store.add_knowledge("旧兼容入口也必须写 SQLite", "architecture", scope="global")
    assert store.update_knowledge(fact_id, "旧兼容入口只写 SQLite") is True
    with db._conn() as conn:
        current_id = str(
            conn.execute(
                "SELECT id FROM knowledge WHERE lineage_id = ? ORDER BY revision_no DESC LIMIT 1",
                (fact_id,),
            ).fetchone()[0]
        )
    assert store.delete_knowledge(current_id) is True

    assert not (memory_root / "knowledge" / "areas" / "general" / "items.json").exists()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT fact, lifecycle_state, revision_no FROM knowledge ORDER BY revision_no"
        ).fetchall()
    assert [row["revision_no"] for row in rows] == [1, 2]
    assert rows[0]["lifecycle_state"] == "superseded"
    assert rows[1]["lifecycle_state"] == "tombstoned"
    assert projection_calls == [10, 10, 10]


def test_physical_purge_requires_confirmation_and_only_accepts_inactive_fact(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    result = db.write_knowledge(fact="仅高级治理可物理清除", scope="global")
    fact_id = str(result["factId"])
    assert db.hard_delete_knowledge(fact_id) is False
    assert db.hard_delete_knowledge(fact_id, confirm=True) is False
    assert db.delete_knowledge(fact_id) is True
    assert db.hard_delete_knowledge(fact_id, confirm=True) is True
    with db._conn() as conn:
        assert conn.execute("SELECT 1 FROM knowledge WHERE id = ?", (fact_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM knowledge_observations WHERE fact_id = ?", (fact_id,)).fetchone() is None
        queued = conn.execute(
            "SELECT operation, payload_json FROM knowledge_projection_outbox WHERE fact_id = ? ORDER BY id DESC LIMIT 1",
            (fact_id,),
        ).fetchone()
    assert queued["operation"] == "remove"
    assert json.loads(queued["payload_json"])["scope"] == "global"


def test_incremental_json_projection_cannot_reactivate_existing_superseded_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    old = db.write_knowledge(fact="旧规则", category="rule", scope="global", fact_id="fact-old")
    db.write_knowledge(
        fact="新规则",
        category="rule",
        scope="global",
        relation="replace",
        target_fact_id=str(old["factId"]),
        fact_id="fact-new",
    )
    areas = tmp_path / "memory" / "knowledge" / "areas" / "general"
    areas.mkdir(parents=True)
    (areas / "items.json").write_text(
        json.dumps(
            [
                {"id": "fact-old", "fact": "旧规则", "category": "rule", "scope": "global", "status": "active"},
                {"id": "projection-only", "fact": "投影不能回灌", "category": "rule", "scope": "global", "status": "active"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.knowledge_db.V8_AGENT_OS_HOME", tmp_path)

    assert db.run_incremental_index() == 0
    with db._conn() as conn:
        row = conn.execute("SELECT lifecycle_state, superseded_by FROM knowledge WHERE id = 'fact-old'").fetchone()
        projection_only = conn.execute("SELECT 1 FROM knowledge WHERE id = 'projection-only'").fetchone()
    assert row["lifecycle_state"] == "superseded"
    assert row["superseded_by"] == "fact-new"
    assert projection_only is None


def test_scoped_relation_requires_active_evidence_and_does_not_cross_project(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    fact = db.write_knowledge(
        fact="V8OS 使用 SQLite 保存知识真相",
        category="architecture",
        scope="project:v8os",
    )
    db.add_scoped_relation(
        "v8os",
        "USES",
        "sqlite",
        scope="project:v8os",
        source_fact_ids=[str(fact["factId"])],
    )
    assert db.multi_hop_query("v8os", scopes=["project:v8os", "global"])[0]["scope"] == "project:v8os"
    assert db.multi_hop_query("v8os", scopes=["project:other", "global"]) == []

    db.write_knowledge(
        fact="V8OS 使用 PostgreSQL 保存知识真相",
        category="architecture",
        scope="project:v8os",
        relation="replace",
        target_fact_id=str(fact["factId"]),
    )
    assert db.multi_hop_query("v8os", scopes=["project:v8os", "global"]) == []


def test_scoped_relation_delete_archives_evidence_and_read_path_rechecks_fact_state(tmp_path: Path) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    fact = db.write_knowledge(fact="画布属于创意媒体", scope="project:media")
    relation_id = db.add_scoped_relation(
        "canvas",
        "BELONGS_TO",
        "creative_media",
        scope="project:media",
        source_fact_ids=[str(fact["factId"])],
    )
    assert db.delete_relation("canvas", "BELONGS_TO", "creative_media", scope="project:other") is False
    assert db.delete_relation("canvas", "BELONGS_TO", "creative_media", scope="project:media") is True
    with db._conn() as conn:
        relation = conn.execute("SELECT status FROM scoped_relations WHERE id = ?", (relation_id,)).fetchone()
        evidence_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM scoped_relation_evidence WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()[0]
        )
    assert relation["status"] == "archived"
    assert evidence_count == 1

    active_relation_id = db.add_scoped_relation(
        "canvas",
        "BELONGS_TO",
        "creative_media",
        scope="project:media",
        source_fact_ids=[str(fact["factId"])],
    )
    with db._conn() as conn:
        conn.execute(
            "UPDATE knowledge SET lifecycle_state = 'superseded' WHERE id = ?",
            (str(fact["factId"]),),
        )
    assert active_relation_id == relation_id
    assert db.query_entity("canvas", scopes=["project:media"]) == []


def test_projection_empty_vector_result_stays_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    result = db.write_knowledge(fact="需要投影的知识", scope="global")
    projection_root = tmp_path / "projection" / "items.json"
    monkeypatch.setattr("core.knowledge_projection._scope_path", lambda _scope: projection_root)

    class EmptyVectorStore:
        collection = object()

        def add_documents(self, _documents):
            return []

        def delete_by_ids(self, _ids):
            return None

    monkeypatch.setattr("core.vector_store.get_vector_store", lambda: EmptyVectorStore())
    service = KnowledgeProjectionService(db)
    processed = service.process_outbox(limit=10)

    assert processed["retry"] == 1
    assert projection_root.exists()
    with db._conn() as conn:
        status = conn.execute(
            "SELECT status, attempts FROM knowledge_projection_outbox WHERE fact_id = ? ORDER BY id DESC LIMIT 1",
            (result["factId"],),
        ).fetchone()
    assert status["status"] == "retry"
    assert status["attempts"] == 1


def test_projection_custom_memory_root_binds_chroma_to_that_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    requested_paths: list[Path] = []

    class VectorStoreStub:
        def __init__(self, *, db_dir: Path):
            requested_paths.append(Path(db_dir))

    monkeypatch.setattr("core.vector_store.VectorStore", VectorStoreStub)
    memory_root = tmp_path / "isolated-memory"
    service = KnowledgeProjectionService(db, memory_root=memory_root)

    assert isinstance(service._get_vector_store(), VectorStoreStub)
    assert requested_paths == [memory_root / ".index" / "chroma_db"]


def test_projection_batches_vector_upserts_instead_of_calling_per_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = KnowledgeDB(tmp_path / "knowledge.db")
    for index in range(120):
        db.write_knowledge(fact=f"批量投影知识 {index}", scope="global", fact_id=f"fact-{index:03d}")
    projection_root = tmp_path / "projection" / "items.json"
    monkeypatch.setattr("core.knowledge_projection._scope_path", lambda _scope: projection_root)
    batch_sizes: list[int] = []

    class BatchVectorStore:
        collection = object()

        def add_documents(self, documents):
            batch_sizes.append(len(documents))
            return [item["id"] for item in documents]

        def delete_by_ids(self, _ids):
            return None

    monkeypatch.setattr("core.vector_store.get_vector_store", lambda: BatchVectorStore())
    result = KnowledgeProjectionService(db).process_outbox(limit=500)

    assert result == {"processed": 120, "completed": 120, "retry": 0, "deadLetter": 0}
    assert batch_sizes == [16, 16, 16, 16, 16, 16, 16, 8]


def _create_empty_sqlite(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
        conn.commit()


def test_backup_rollback_restores_derived_directory_and_original_absence(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    _create_empty_sqlite(database)
    existing = tmp_path / "areas"
    existing.mkdir()
    (existing / "items.json").write_text("original", encoding="utf-8")
    originally_missing = tmp_path / "chroma_db"
    service = StorageBackupService(root=tmp_path / "backups")
    backup = service.create_backup(
        purpose="memory-p0-test",
        sqlite_paths=[database],
        directory_paths=[existing, originally_missing],
        plan_digest="digest",
    )
    (existing / "items.json").write_text("changed", encoding="utf-8")
    originally_missing.mkdir()
    (originally_missing / "vector.bin").write_bytes(b"derived")

    service.restore_backup(Path(backup["path"]) / "manifest.json", offline=True)

    assert (existing / "items.json").read_text(encoding="utf-8") == "original"
    assert not originally_missing.exists()


def test_realistic_migration_copy_repairs_reactivation_and_rolls_back(tmp_path: Path) -> None:
    state = tmp_path / "state.db"
    checkpoints = tmp_path / "checkpoints.db"
    observability = tmp_path / "observability.db"
    for path in (state, checkpoints, observability):
        _create_empty_sqlite(path)
    memory_root = tmp_path / "memory"
    knowledge_path = memory_root / ".index" / "knowledge.db"
    db = KnowledgeDB(knowledge_path)
    db.write_knowledge(fact="旧配置", scope="project:demo", fact_id="fact-old")
    db.write_knowledge(
        fact="新配置",
        scope="project:demo",
        relation="replace",
        target_fact_id="fact-old",
        fact_id="fact-new",
    )
    with db._conn() as conn:
        conn.execute("UPDATE knowledge SET lifecycle_state = 'active' WHERE id = 'fact-old'")
        conn.execute(
            "UPDATE knowledge SET metadata_json = ? WHERE id = 'fact-old'",
            (json.dumps({"maintenance": {"mergeSuggestion": {"targetId": "fact-new", "similarity": 0.97}}}),),
        )
        conn.execute(
            "UPDATE knowledge SET metadata_json = ? WHERE id = 'fact-new'",
            (json.dumps({"maintenance": {"mergeSuggestion": {"targetId": "fact-old", "similarity": 0.97}}}),),
        )
    areas = memory_root / "knowledge" / "areas" / "projects" / "demo"
    areas.mkdir(parents=True)
    (areas / "items.json").write_text(
        json.dumps(
            [
                {"id": "fact-old", "fact": "旧配置", "scope": "project:demo", "status": "active"},
                {"id": "legacy-missing", "fact": "仅存在于旧投影", "scope": "project:demo", "status": "active"},
                {
                    "id": "legacy-deleted",
                    "fact": "已经删除的旧投影不能复活",
                    "scope": "project:demo",
                    "status": "deleted",
                    "lifecycle_state": "active",
                },
            ]
        ),
        encoding="utf-8",
    )
    chroma = memory_root / ".index" / "chroma_db"
    chroma.mkdir(parents=True)
    (chroma / "marker.txt").write_text("derived", encoding="utf-8")
    migration = MemoryP0MigrationService(
        knowledge_db_path=knowledge_path,
        state_db_path=state,
        checkpoint_db_path=checkpoints,
        observability_db_path=observability,
        memory_root=memory_root,
        backup_root=tmp_path / "backups",
    )

    plan = migration.plan()
    assert plan["safeToApply"] is True
    assert plan["activeSupersededIds"] == ["fact-old"]
    assert {item["id"] for item in plan["missingJsonItems"]} == {"legacy-missing", "legacy-deleted"}
    deleted_plan_item = next(item for item in plan["missingJsonItems"] if item["id"] == "legacy-deleted")
    assert deleted_plan_item["importLifecycleState"] == "tombstoned"
    assert len(plan["mergeSuggestions"]) == 1
    with pytest.raises(RuntimeError, match="plan changed"):
        migration.apply(idempotency_key="p0-test-mismatch", expected_plan_digest="wrong")
    applied = migration.apply(
        idempotency_key="p0-test",
        expected_plan_digest=str(plan["planDigest"]),
    )
    with db._conn() as conn:
        repaired = conn.execute("SELECT lifecycle_state FROM knowledge WHERE id = 'fact-old'").fetchone()[0]
        imported = conn.execute("SELECT COUNT(*) FROM knowledge WHERE id = 'legacy-missing'").fetchone()[0]
        deleted_import = conn.execute(
            "SELECT status, lifecycle_state FROM knowledge WHERE id = 'legacy-deleted'"
        ).fetchone()
        pending = conn.execute("SELECT COUNT(*) FROM knowledge_resolution_candidates WHERE state = 'pending'").fetchone()[0]
    assert repaired == "superseded"
    assert imported == 1
    assert dict(deleted_import) == {"status": "deleted", "lifecycle_state": "tombstoned"}
    assert pending == 1
    assert applied["projection"]["json"]["scopeCount"] == 1
    assert applied["projection"]["ftsRows"] == 2
    projected = json.loads((areas / "items.json").read_text(encoding="utf-8"))
    assert {item["id"] for item in projected} == {
        "fact-old",
        "fact-new",
        "legacy-missing",
        "legacy-deleted",
    }
    assert "fact-old" not in [item["id"] for item in db.fts_search("旧配置", scope="project:demo")]
    assert [item["id"] for item in db.fts_search("旧投影", scope="project:demo")] == ["legacy-missing"]
    assert db.fts_search("不能复活", scope="project:demo") == []
    assert migration.apply(
        idempotency_key="p0-test",
        expected_plan_digest=str(plan["planDigest"]),
    )["idempotent"] is True

    manifest = Path(applied["backup"]["path"]) / "manifest.json"
    migration.rollback(manifest_path=manifest, offline=True)
    restored = KnowledgeDB(knowledge_path)
    with restored._conn() as conn:
        assert conn.execute("SELECT lifecycle_state FROM knowledge WHERE id = 'fact-old'").fetchone()[0] == "active"
        assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE id = 'legacy-missing'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge WHERE id = 'legacy-deleted'").fetchone()[0] == 0


def test_migration_resumes_projection_phase_without_reapplying_or_rebacking_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state.db"
    checkpoints = tmp_path / "checkpoints.db"
    observability = tmp_path / "observability.db"
    for path in (state, checkpoints, observability):
        _create_empty_sqlite(path)
    memory_root = tmp_path / "memory"
    knowledge_path = memory_root / ".index" / "knowledge.db"
    KnowledgeDB(knowledge_path).write_knowledge(fact="投影阶段可恢复", scope="global")
    migration = MemoryP0MigrationService(
        knowledge_db_path=knowledge_path,
        state_db_path=state,
        checkpoint_db_path=checkpoints,
        observability_db_path=observability,
        memory_root=memory_root,
        backup_root=tmp_path / "backups",
    )
    plan = migration.plan()
    monkeypatch.setattr(KnowledgeProjectionService, "rebuild_json", lambda _self: {"scopeCount": 1})
    monkeypatch.setattr(
        KnowledgeProjectionService,
        "reconcile_vectors",
        lambda _self: {"missing": 0, "orphanedRemoved": 0},
    )

    def interrupted(_self, *, limit: int):
        raise RuntimeError(f"simulated_projection_interrupt:{limit}")

    monkeypatch.setattr(KnowledgeProjectionService, "process_outbox", interrupted)
    with pytest.raises(RuntimeError, match="simulated_projection_interrupt"):
        migration.apply(idempotency_key="resume-projection", expected_plan_digest=str(plan["planDigest"]))
    with KnowledgeDB(knowledge_path)._conn() as conn:
        state_after_interrupt = conn.execute(
            "SELECT state FROM memory_migration_journal WHERE migration_id = 'resume-projection'"
        ).fetchone()[0]
    assert state_after_interrupt == "projecting"
    assert len(list((tmp_path / "backups" / "memory-p0").glob("resume-projection"))) == 1

    monkeypatch.setattr(
        KnowledgeProjectionService,
        "process_outbox",
        lambda _self, *, limit: {"processed": 0, "completed": 0, "retry": 0, "deadLetter": 0},
    )
    monkeypatch.setattr(
        KnowledgeProjectionService,
        "health",
        lambda _self: {"state": "ready", "outbox": {}, "backlog": 0},
    )
    resumed = migration.apply(
        idempotency_key="resume-projection",
        expected_plan_digest=str(plan["planDigest"]),
    )
    assert resumed["resumed"] is True
    with KnowledgeDB(knowledge_path)._conn() as conn:
        assert conn.execute(
            "SELECT state FROM memory_migration_journal WHERE migration_id = 'resume-projection'"
        ).fetchone()[0] == "completed"
    assert len(list((tmp_path / "backups" / "memory-p0").glob("resume-projection"))) == 1
