"""
知识数据库 — SQLite 统一承载 FTS5 全文检索 + 知识图谱 + 增量索引

Tables:
  knowledge       — 知识条目主表
  knowledge_fts   — FTS5 全文检索虚拟表 (jieba 分词后空格分隔)
  entities        — 知识图谱实体
  relations       — 知识图谱关系 (subject → predicate → object)
  file_index      — 增量索引追踪 (path → mtime → hash)

位置: ~/.v8-agent-os/memory/.index/knowledge.db

FTS5 中文分词策略:
  写入: fact → jieba.cut → 空格拼接 → fact_tokenized 字段
  检索: query → jieba.cut → 空格拼接 → FTS5 MATCH
  原理: unicode61 tokenizer 按空格分词，jieba 预处理确保中文可检索
"""

import re
import sqlite3
import json
import hashlib
import logging
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

logger = logging.getLogger("v8_agent_os.knowledge_db")

DB_PATH = V8_AGENT_OS_HOME / "memory" / ".index" / "knowledge.db"

# === 中文分词 ===
try:
    import jieba
    jieba.setLogLevel(logging.WARNING)  # 静默 jieba 加载日志
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False
    logger.warning("[KnowledgeDB] jieba not installed, Chinese FTS5 will be degraded.")


def tokenize_for_fts(text: str) -> str:
    """
    对文本进行 jieba 分词，返回空格分隔的词语序列。
    如果 jieba 不可用，直接返回原文。
    
    例: "知识图谱全文检索" → "知识 图谱 全文 检索"
    """
    if not _HAS_JIEBA or not text:
        return text
    
    # 保留英文原样，中文进行分词
    words = jieba.cut(text, cut_all=False)
    return " ".join(w.strip() for w in words if w.strip())


_FTS_RESERVED_WORDS = {"AND", "OR", "NOT", "NEAR"}


def sanitize_fts_query_tokens(tokenized_query: str, *, max_terms: int = 32) -> List[str]:
    """Return safe FTS5 prefix-query tokens without quotes/operators."""
    tokens: List[str] = []
    for raw in str(tokenized_query or "").split():
        cleaned = re.sub(r"[\"'`‘’“”\(\)\[\]\{\}:;,+*/\\|&!<>~=^$?#]+", " ", raw)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        for part in cleaned.split():
            term = part.strip().strip("-_.")
            if not term or term.upper() in _FTS_RESERVED_WORDS:
                continue
            if len(term) > 64:
                term = term[:64]
            tokens.append(term)
            if len(tokens) >= max_terms:
                return tokens
    return tokens


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_confidence(value: object, default: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(parsed, 1.0))


def _normalize_evidence_refs(values: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        ref = str(value or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        normalized.append(ref[:1000])
    return normalized


def _effective_confidence(confidence: object, maintainer_source: str | None = None) -> float:
    base = _bounded_confidence(confidence)
    if str(maintainer_source or "").strip().lower() == "human_admin":
        return min(1.0, base * 1.5)
    return base


def _normalize_fact_for_compaction(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[`*_#>\[\]\(\){}\"'“”‘’，,。；;：:！？!?\\\/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_KNOWLEDGE_RELATIONS = {"new", "reinforce", "replace", "refine", "conflict"}


def _normalize_knowledge_relation(value: object, *, default: str = "new") -> str:
    normalized = str(value or default).strip().lower() or default
    return normalized if normalized in _KNOWLEDGE_RELATIONS else default


def _observation_hash(
    *,
    fact: str,
    scope: str,
    category: str,
    relation: str,
    source_session: str | None,
    source_run: str | None,
    transcript_hash: str | None,
) -> str:
    explicit = str(transcript_hash or "").strip()
    if explicit:
        return explicit
    payload = "\n".join(
        [
            str(source_session or ""),
            str(source_run or ""),
            str(scope or "global"),
            str(category or "general"),
            relation,
            _normalize_fact_for_compaction(fact),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class KnowledgeDB:
    """SQLite 知识数据库：FTS5 + 知识图谱 + 增量索引"""
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
    
    @contextmanager
    def _conn(self):
        """线程安全的连接上下文管理器"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # 并发读优化
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_schema(self):
        """初始化数据库 schema"""
        with self._conn() as conn:
            # 知识条目主表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY,
                    fact TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    scope TEXT DEFAULT 'global',
                    status TEXT DEFAULT 'active',
                    source_session TEXT,
                    parent_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Phase 25.2: Dynamic Schema Migration for Parent-Child Chunking
            cursor = conn.execute("PRAGMA table_info(knowledge)")
            knowledge_columns = {row["name"] for row in cursor.fetchall()}
            for column_name, column_sql in {
                "parent_id": "parent_id TEXT",
                "lifecycle_state": "lifecycle_state TEXT DEFAULT 'active'",
                "last_seen_at": "last_seen_at TEXT",
                "last_injected_at": "last_injected_at TEXT",
                "last_verified_at": "last_verified_at TEXT",
                "evidence_refs_json": "evidence_refs_json TEXT",
                "promotion_reason": "promotion_reason TEXT",
                "superseded_by": "superseded_by TEXT",
                "tombstone_of": "tombstone_of TEXT",
                "decay_score": "decay_score REAL DEFAULT 0",
                "agents_hash": "agents_hash TEXT",
                "repo_signature": "repo_signature TEXT",
                "signature_policy": "signature_policy TEXT DEFAULT 'soft_v1'",
                "maintainer_source": "maintainer_source TEXT DEFAULT 'memory_runtime'",
                "confidence": "confidence REAL DEFAULT 1.0",
                "effective_confidence": "effective_confidence REAL DEFAULT 1.0",
                "metadata_json": "metadata_json TEXT",
                "lineage_id": "lineage_id TEXT",
                "revision_no": "revision_no INTEGER DEFAULT 1",
                "importance": "importance INTEGER DEFAULT 50",
                "durability": "durability TEXT DEFAULT 'operational'",
                "valid_from": "valid_from TEXT",
                "valid_to": "valid_to TEXT",
                "usage_count": "usage_count INTEGER DEFAULT 0",
            }.items():
                if column_name not in knowledge_columns:
                    conn.execute(f"ALTER TABLE knowledge ADD COLUMN {column_sql}")

            conn.execute(
                """
                UPDATE knowledge
                SET lineage_id = COALESCE(NULLIF(lineage_id, ''), id),
                    revision_no = COALESCE(revision_no, 1),
                    valid_from = COALESCE(valid_from, created_at, updated_at)
                WHERE lineage_id IS NULL OR lineage_id = '' OR revision_no IS NULL OR valid_from IS NULL
                """
            )

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_observations (
                    id TEXT PRIMARY KEY,
                    fact_id TEXT NOT NULL,
                    source_session TEXT,
                    source_run TEXT,
                    source_message_ids_json TEXT,
                    raw_fact TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    importance INTEGER DEFAULT 50,
                    durability TEXT DEFAULT 'operational',
                    evidence_refs_json TEXT,
                    transcript_hash TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (fact_id) REFERENCES knowledge(id),
                    UNIQUE(fact_id, transcript_hash)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_resolution_candidates (
                    id TEXT PRIMARY KEY,
                    candidate_fact_id TEXT NOT NULL,
                    target_fact_id TEXT,
                    proposed_relation TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    similarity REAL,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT,
                    resolution TEXT,
                    FOREIGN KEY (candidate_fact_id) REFERENCES knowledge(id),
                    FOREIGN KEY (target_fact_id) REFERENCES knowledge(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_projection_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    fact_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_error TEXT,
                    payload_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS scoped_relations (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence REAL DEFAULT 1.0,
                    effective_confidence REAL DEFAULT 1.0,
                    maintainer_source TEXT DEFAULT 'memory_runtime',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(subject, predicate, object, scope)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS scoped_relation_evidence (
                    relation_id TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (relation_id, fact_id),
                    FOREIGN KEY (relation_id) REFERENCES scoped_relations(id),
                    FOREIGN KEY (fact_id) REFERENCES knowledge(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS scoped_relation_evidence_refs (
                    relation_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    evidence_kind TEXT DEFAULT 'external',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (relation_id, evidence_ref),
                    FOREIGN KEY (relation_id) REFERENCES scoped_relations(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_lifecycle_audit (
                    id TEXT PRIMARY KEY,
                    fact_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT,
                    evidence_refs_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_cleanup_plans (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'planned',
                    criteria_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    applied_at TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_cleanup_candidates (
                    plan_id TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    proposed_action TEXT NOT NULL DEFAULT 'review',
                    state TEXT NOT NULL DEFAULT 'planned',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (plan_id, fact_id),
                    FOREIGN KEY (plan_id) REFERENCES knowledge_cleanup_plans(id),
                    FOREIGN KEY (fact_id) REFERENCES knowledge(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_migration_events (
                    id TEXT PRIMARY KEY,
                    migration_id TEXT NOT NULL,
                    fact_id TEXT,
                    event_type TEXT NOT NULL,
                    detail_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_migration_journal (
                    migration_id TEXT PRIMARY KEY,
                    plan_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    backup_manifest_path TEXT,
                    detail_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS deprecated_memory_api_usage (
                    api_name TEXT PRIMARY KEY,
                    call_count INTEGER NOT NULL DEFAULT 0,
                    last_context TEXT,
                    last_used_at TEXT NOT NULL
                )
            """)
            
            # FTS5 全文检索虚拟表
            # 注意: fact_tokenized 是 jieba 分词后的空格分隔文本
            # 原始 fact 存储在 knowledge 主表中保持可读性
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    fact_tokenized,
                    category,
                    scope,
                    tokenize='unicode61'
                )
            """)
            
            # 知识图谱：实体表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    name TEXT PRIMARY KEY,
                    type TEXT DEFAULT 'concept',
                    maintainer_source TEXT DEFAULT 'memory_runtime',
                    confidence REAL DEFAULT 1.0,
                    effective_confidence REAL DEFAULT 1.0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor = conn.execute("PRAGMA table_info(entities)")
            entity_columns = {row["name"] for row in cursor.fetchall()}
            for column_name, column_sql in {
                "maintainer_source": "maintainer_source TEXT DEFAULT 'memory_runtime'",
                "confidence": "confidence REAL DEFAULT 1.0",
                "effective_confidence": "effective_confidence REAL DEFAULT 1.0",
                "updated_at": "updated_at TEXT",
            }.items():
                if column_name not in entity_columns:
                    conn.execute(f"ALTER TABLE entities ADD COLUMN {column_sql}")
            
            # 知识图谱：关系表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    source_fact_id TEXT,
                    confidence REAL DEFAULT 1.0,
                    effective_confidence REAL DEFAULT 1.0,
                    maintainer_source TEXT DEFAULT 'memory_runtime',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (subject) REFERENCES entities(name),
                    FOREIGN KEY (object) REFERENCES entities(name),
                    UNIQUE(subject, predicate, object)
                )
            """)
            cursor = conn.execute("PRAGMA table_info(relations)")
            relation_columns = {row["name"] for row in cursor.fetchall()}
            for column_name, column_sql in {
                "effective_confidence": "effective_confidence REAL DEFAULT 1.0",
                "maintainer_source": "maintainer_source TEXT DEFAULT 'memory_runtime'",
                "updated_at": "updated_at TEXT",
            }.items():
                if column_name not in relation_columns:
                    conn.execute(f"ALTER TABLE relations ADD COLUMN {column_sql}")
            
            # 增量索引：文件追踪表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_index (
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    content_hash TEXT NOT NULL,
                    chunk_count INTEGER DEFAULT 0,
                    indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 执行日志表：统一记录 Cron/Hooks/Agents 的运行状态
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_target TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    error_message TEXT,
                    payload TEXT
                )
            """)
            
            # 索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_state ON knowledge(lifecycle_state)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_repo_signature ON knowledge(repo_signature)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_agents_hash ON knowledge(agents_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_lineage_revision ON knowledge(lineage_id, revision_no)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_validity ON knowledge(valid_from, valid_to)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_observations_fact ON knowledge_observations(fact_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_resolution_state ON knowledge_resolution_candidates(state, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_projection_status ON knowledge_projection_outbox(status, next_attempt_at, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scoped_relations_scope_subject ON scoped_relations(scope, subject, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scoped_relations_scope_object ON scoped_relations(scope, object, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scoped_relation_evidence_fact ON scoped_relation_evidence(fact_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scoped_relation_evidence_refs_relation ON scoped_relation_evidence_refs(relation_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_audit_fact ON knowledge_lifecycle_audit(fact_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_cleanup_candidates_state ON knowledge_cleanup_candidates(plan_id, state)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_migration_events_migration ON knowledge_migration_events(migration_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_predicate ON relations(predicate)")
            self._backfill_legacy_evidence_refs(conn)
    
    # ==========================================
    # FTS5 全文检索
    # ==========================================
    
    def fts_search(self, query: str, scope: Optional[str] = None,
                   limit: int = 20) -> List[Dict]:
        """
        FTS5 全文检索，支持 jieba 中文分词。
        
        检索流程: query → jieba 分词 → FTS5 MATCH → 返回原始 fact
        """
        # 对查询进行分词
        tokenized_query = tokenize_for_fts(query)
        
        # 构建 FTS5 查询（每个词加通配符支持前缀匹配）
        words = sanitize_fts_query_tokens(tokenized_query)
        if not words:
            return []
        fts_query = " OR ".join([f'"{w}"*' for w in words])
        
        with self._conn() as conn:
            if scope:
                rows = conn.execute("""
                    SELECT k.id, k.fact, k.category, k.scope, k.status,
                           k.lifecycle_state, k.last_seen_at, k.last_injected_at,
                           k.last_verified_at, k.evidence_refs_json, k.promotion_reason,
                           k.superseded_by, k.tombstone_of, k.decay_score,
                           k.agents_hash, k.repo_signature, k.signature_policy,
                           k.maintainer_source, k.confidence, k.effective_confidence,
                           k.metadata_json, k.lineage_id, k.revision_no, k.importance,
                           k.durability, k.valid_from, k.valid_to, k.usage_count,
                           rank as relevance
                    FROM knowledge_fts f
                    JOIN knowledge k ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.scope IN (?, 'global') AND k.status = 'active'
                      AND COALESCE(k.lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded')
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, scope, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT k.id, k.fact, k.category, k.scope, k.status,
                           k.lifecycle_state, k.last_seen_at, k.last_injected_at,
                           k.last_verified_at, k.evidence_refs_json, k.promotion_reason,
                           k.superseded_by, k.tombstone_of, k.decay_score,
                           k.agents_hash, k.repo_signature, k.signature_policy,
                           k.maintainer_source, k.confidence, k.effective_confidence,
                           k.metadata_json, k.lineage_id, k.revision_no, k.importance,
                           k.durability, k.valid_from, k.valid_to, k.usage_count,
                           rank as relevance
                    FROM knowledge_fts f
                    JOIN knowledge k ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.status = 'active'
                      AND COALESCE(k.lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded')
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, limit)).fetchall()
            
            return [dict(r) for r in rows]
            
    def get_all_knowledge(self, scope: Optional[str] = None, limit: int = 50, status: str = "active") -> List[Dict]:
        """查询知识条目；active 投影只包含当前可用的 lifecycle。"""
        normalized_status = str(status or "active").strip().lower() or "active"
        lifecycle_clause = (
            "AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded', 'quarantined')"
            if normalized_status == "active"
            else ""
        )
        with self._conn() as conn:
            if scope:
                rows = conn.execute(f"""
                    SELECT id, fact, category, scope, status, source_session, updated_at,
                           lifecycle_state, last_seen_at, last_injected_at, last_verified_at,
                           evidence_refs_json, promotion_reason, superseded_by, tombstone_of,
                           decay_score, agents_hash, repo_signature, signature_policy,
                           maintainer_source, confidence, effective_confidence, metadata_json
                           , lineage_id, revision_no, importance, durability, valid_from, valid_to, usage_count
                    FROM knowledge
                    WHERE scope IN (?, 'global') AND status = ?
                      {lifecycle_clause}
                    ORDER BY effective_confidence DESC, updated_at DESC
                    LIMIT ?
                """, (scope, normalized_status, limit)).fetchall()
            else:
                rows = conn.execute(f"""
                    SELECT id, fact, category, scope, status, source_session, updated_at,
                           lifecycle_state, last_seen_at, last_injected_at, last_verified_at,
                           evidence_refs_json, promotion_reason, superseded_by, tombstone_of,
                           decay_score, agents_hash, repo_signature, signature_policy,
                           maintainer_source, confidence, effective_confidence, metadata_json
                           , lineage_id, revision_no, importance, durability, valid_from, valid_to, usage_count
                    FROM knowledge
                    WHERE status = ?
                      {lifecycle_clause}
                    ORDER BY effective_confidence DESC, updated_at DESC
                    LIMIT ?
                """, (normalized_status, limit)).fetchall()
            
            return [dict(r) for r in rows]

    def get_knowledge_maintenance_page(
        self,
        *,
        cursor_after: Optional[str] = None,
        limit: int = 200,
        scope: Optional[str] = None,
        status: str = "active",
    ) -> Dict[str, object]:
        effective_limit = max(1, min(int(limit or 200), 2000))
        normalized_status = str(status or "active").strip().lower() or "active"
        wrapped = False

        def _query(conn: sqlite3.Connection, after: Optional[str]) -> List[sqlite3.Row]:
            clauses = ["status = ?"]
            params: List[object] = [normalized_status]
            if normalized_status == "active":
                clauses.append("COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded', 'quarantined')")
            if scope:
                clauses.append("scope = ?")
                params.append(str(scope))
            if after:
                clauses.append("id > ?")
                params.append(str(after))
            params.append(effective_limit)
            return conn.execute(
                f"SELECT * FROM knowledge WHERE {' AND '.join(clauses)} ORDER BY id ASC LIMIT ?",
                params,
            ).fetchall()

        with self._conn() as conn:
            rows = _query(conn, cursor_after)
            if not rows and cursor_after:
                rows = _query(conn, None)
                wrapped = True
        items = [dict(row) for row in rows]
        next_cursor = str(items[-1].get("id") or "") if len(items) >= effective_limit else ""
        if len(items) < effective_limit:
            wrapped = True
        return {
            "items": items,
            "nextCursor": next_cursor,
            "wrapped": wrapped,
            "batchCount": len(items),
        }
    
    # ==========================================
    # 知识 CRUD
    # ==========================================

    @staticmethod
    def _insert_fts_row(conn: sqlite3.Connection, *, rowid: int, fact: str, category: str, scope: str) -> None:
        conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (rowid,))
        conn.execute(
            "INSERT INTO knowledge_fts(rowid, fact_tokenized, category, scope) VALUES (?, ?, ?, ?)",
            (rowid, tokenize_for_fts(fact), category, scope),
        )

    def rebuild_fts(self) -> int:
        """Rebuild FTS exclusively from injectable canonical SQLite rows."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT rowid, fact, category, scope
                FROM knowledge
                WHERE status = 'active'
                  AND COALESCE(lifecycle_state, 'active') = 'active'
                ORDER BY rowid
                """
            ).fetchall()
            conn.execute("DELETE FROM knowledge_fts")
            for row in rows:
                self._insert_fts_row(
                    conn,
                    rowid=int(row["rowid"]),
                    fact=str(row["fact"] or ""),
                    category=str(row["category"] or "general"),
                    scope=str(row["scope"] or "global"),
                )
        return len(rows)

    def record_deprecated_usage(self, api_name: str, *, context: str = "") -> None:
        normalized = str(api_name or "").strip()
        if not normalized:
            return
        now = _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO deprecated_memory_api_usage (api_name, call_count, last_context, last_used_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(api_name) DO UPDATE SET
                    call_count = deprecated_memory_api_usage.call_count + 1,
                    last_context = excluded.last_context,
                    last_used_at = excluded.last_used_at
                """,
                (normalized, str(context or "")[:500], now),
            )

    @staticmethod
    def _enqueue_projection(
        conn: sqlite3.Connection,
        *,
        fact_id: str,
        operation: str,
        payload: Optional[Dict] = None,
        event_key: Optional[str] = None,
    ) -> None:
        now = _utc_now_iso()
        stable_key = event_key or f"{fact_id}:{operation}:{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_projection_outbox (
                event_key, fact_id, operation, status, attempts, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
            """,
            (stable_key, fact_id, operation, json.dumps(payload or {}, ensure_ascii=False), now, now),
        )

    @staticmethod
    def _record_lifecycle_audit(
        conn: sqlite3.Connection,
        *,
        fact_id: str,
        action: str,
        actor: str,
        reason: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        audit_id = f"knowledge-audit-{uuid.uuid4().hex[:20]}"
        conn.execute(
            """
            INSERT INTO knowledge_lifecycle_audit (
                id, fact_id, action, actor, reason, evidence_refs_json, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                str(fact_id),
                str(action or "updated"),
                str(actor or "system"),
                str(reason or "") or None,
                json.dumps(_normalize_evidence_refs(evidence_refs), ensure_ascii=False),
                json.dumps(dict(metadata or {}), ensure_ascii=False),
                _utc_now_iso(),
            ),
        )
        return audit_id

    @staticmethod
    def _backfill_legacy_evidence_refs(conn: sqlite3.Connection) -> None:
        """Recover factual source references without inventing historical use."""
        observation_rows = conn.execute(
            """
            SELECT id, source_session, source_run, source_message_ids_json
            FROM knowledge_observations
            WHERE evidence_refs_json IS NULL OR TRIM(evidence_refs_json) IN ('', '[]')
            """
        ).fetchall()
        for row in observation_rows:
            refs: List[str] = []
            try:
                refs.extend(
                    f"message:{message_id}"
                    for message_id in list(json.loads(row["source_message_ids_json"] or "[]"))
                    if str(message_id or "").strip()
                )
            except Exception:
                pass
            if str(row["source_run"] or "").strip():
                refs.append(f"run:{row['source_run']}")
            if str(row["source_session"] or "").strip():
                refs.append(f"session:{row['source_session']}")
            refs = _normalize_evidence_refs(refs)[:50]
            if refs:
                conn.execute(
                    "UPDATE knowledge_observations SET evidence_refs_json = ? WHERE id = ?",
                    (json.dumps(refs, ensure_ascii=False), row["id"]),
                )

        fact_rows = conn.execute(
            """
            SELECT id, category, source_session
            FROM knowledge
            WHERE evidence_refs_json IS NULL OR TRIM(evidence_refs_json) IN ('', '[]')
            """
        ).fetchall()
        for row in fact_rows:
            refs: List[str] = []
            observations = conn.execute(
                "SELECT evidence_refs_json FROM knowledge_observations WHERE fact_id = ? ORDER BY created_at ASC",
                (row["id"],),
            ).fetchall()
            for observation in observations:
                try:
                    refs.extend(list(json.loads(observation["evidence_refs_json"] or "[]")))
                except Exception:
                    continue
            source_session = str(row["source_session"] or "").strip()
            if not refs and source_session:
                prefix = "document" if str(row["category"] or "").strip().lower() == "user_document" else "session"
                refs.append(f"{prefix}:{source_session}")
            refs = _normalize_evidence_refs(refs)[:50]
            if refs:
                conn.execute(
                    "UPDATE knowledge SET evidence_refs_json = ? WHERE id = ?",
                    (json.dumps(refs, ensure_ascii=False), row["id"]),
                )

        conn.execute(
            """
            UPDATE knowledge
            SET last_verified_at = (
                SELECT MAX(observation.created_at)
                FROM knowledge_observations observation
                WHERE observation.fact_id = knowledge.id
            )
            WHERE last_verified_at IS NULL
              AND (SELECT COUNT(*) FROM knowledge_observations observation WHERE observation.fact_id = knowledge.id) > 1
            """
        )

    def mark_knowledge_injected(self, fact_ids: List[str], *, verified: bool = False) -> int:
        """Record actual Agent-surface use, never preview/search inspection."""
        normalized_ids = sorted({str(item or "").strip() for item in fact_ids if str(item or "").strip()})
        if not normalized_ids:
            return 0
        now = _utc_now_iso()
        placeholders = ",".join("?" for _ in normalized_ids)
        with self._conn() as conn:
            if verified:
                cursor = conn.execute(
                    f"""
                    UPDATE knowledge
                    SET usage_count = COALESCE(usage_count, 0) + 1,
                        last_injected_at = ?, last_verified_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                      AND status = 'active'
                      AND COALESCE(lifecycle_state, 'active') = 'active'
                    """,
                    (now, now, now, *normalized_ids),
                )
            else:
                cursor = conn.execute(
                    f"""
                    UPDATE knowledge
                    SET usage_count = COALESCE(usage_count, 0) + 1,
                        last_injected_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                      AND status = 'active'
                      AND COALESCE(lifecycle_state, 'active') = 'active'
                    """,
                    (now, now, *normalized_ids),
                )
            return int(cursor.rowcount or 0)

    def create_cleanup_plan(
        self,
        *,
        existing_session_ids: Optional[set[str]] = None,
        unused_days: int = 180,
        low_evidence_confidence: float = 0.55,
        max_candidates: int = 1000,
    ) -> Dict[str, object]:
        """Create a persistent, non-destructive candidate plan for human review."""
        plan_id = f"knowledge-cleanup-{uuid.uuid4().hex[:16]}"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(unused_days or 180)))).isoformat().replace("+00:00", "Z")
        confidence_limit = _bounded_confidence(low_evidence_confidence, default=0.55)
        session_ids = {str(item) for item in (existing_session_ids or set()) if str(item).strip()}
        candidates: List[Dict[str, object]] = []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT knowledge.*,
                       (SELECT COUNT(*) FROM knowledge_observations observation
                        WHERE observation.fact_id = knowledge.id) AS observation_count,
                       (SELECT COUNT(*) FROM scoped_relation_evidence relation_evidence
                        WHERE relation_evidence.fact_id = knowledge.id) AS relation_evidence_count
                FROM knowledge
                WHERE COALESCE(lifecycle_state, 'active') != 'tombstoned'
                ORDER BY COALESCE(last_injected_at, last_seen_at, created_at) ASC, id ASC
                """
            ).fetchall()
            for row in rows:
                reasons: List[str] = []
                lifecycle = str(row["lifecycle_state"] or "active").strip().lower()
                usage_count = int(row["usage_count"] or 0)
                last_used = str(row["last_injected_at"] or row["last_seen_at"] or row["created_at"] or "")
                observation_count = int(row["observation_count"] or 0)
                try:
                    evidence_refs = _normalize_evidence_refs(json.loads(row["evidence_refs_json"] or "[]"))
                except Exception:
                    evidence_refs = []
                if usage_count <= 0 and last_used and last_used < cutoff:
                    reasons.append("unused")
                if (
                    float(row["effective_confidence"] or row["confidence"] or 0.0) < confidence_limit
                    and observation_count <= 1
                ):
                    reasons.append("low_evidence")
                if lifecycle == "superseded" or str(row["superseded_by"] or "").strip():
                    reasons.append("superseded")
                source_session = str(row["source_session"] or "").strip()
                if existing_session_ids is not None and source_session and source_session not in session_ids:
                    reasons.append("source_session_missing")
                if not reasons:
                    continue
                candidates.append(
                    {
                        "factId": str(row["id"]),
                        "scope": str(row["scope"] or "global"),
                        "lifecycleState": lifecycle,
                        "usageCount": usage_count,
                        "observationCount": observation_count,
                        "evidenceRefCount": len(evidence_refs),
                        "relationEvidenceCount": int(row["relation_evidence_count"] or 0),
                        "reasons": reasons,
                        "proposedAction": "review_tombstone" if lifecycle == "active" else "review_retention",
                    }
                )
                if len(candidates) >= max(1, min(int(max_candidates or 1000), 5000)):
                    break
            summary = {
                "candidateCount": len(candidates),
                "reasonCounts": {
                    reason: sum(1 for item in candidates if reason in item["reasons"])
                    for reason in ("unused", "low_evidence", "superseded", "source_session_missing")
                },
                "destructiveActions": 0,
            }
            criteria = {
                "unusedDays": max(1, int(unused_days or 180)),
                "lowEvidenceConfidence": confidence_limit,
                "maxCandidates": max(1, min(int(max_candidates or 1000), 5000)),
            }
            conn.execute(
                "INSERT INTO knowledge_cleanup_plans (id, state, criteria_json, summary_json, created_at) VALUES (?, 'planned', ?, ?, ?)",
                (plan_id, json.dumps(criteria, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), _utc_now_iso()),
            )
            for item in candidates:
                conn.execute(
                    """
                    INSERT INTO knowledge_cleanup_candidates (
                        plan_id, fact_id, reasons_json, proposed_action, state, created_at
                    ) VALUES (?, ?, ?, ?, 'planned', ?)
                    """,
                    (
                        plan_id,
                        item["factId"],
                        json.dumps(item["reasons"], ensure_ascii=False),
                        item["proposedAction"],
                        _utc_now_iso(),
                    ),
                )
        return {"planId": plan_id, "state": "planned", "summary": summary, "criteria": criteria, "candidates": candidates}

    @staticmethod
    def _deactivate_unsupported_relations(conn: sqlite3.Connection, *, fact_id: str) -> None:
        relation_rows = conn.execute(
            "SELECT relation_id FROM scoped_relation_evidence WHERE fact_id = ?",
            (fact_id,),
        ).fetchall()
        now = _utc_now_iso()
        for relation_row in relation_rows:
            relation_id = str(relation_row["relation_id"])
            still_supported = conn.execute(
                """
                SELECT 1 WHERE EXISTS (
                    SELECT 1
                    FROM scoped_relation_evidence evidence
                    JOIN knowledge fact ON fact.id = evidence.fact_id
                    WHERE evidence.relation_id = ?
                      AND fact.status = 'active'
                      AND COALESCE(fact.lifecycle_state, 'active') NOT IN (
                          'stale', 'tombstoned', 'superseded', 'quarantined'
                      )
                ) OR EXISTS (
                    SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                    WHERE evidence_ref.relation_id = ?
                )
                LIMIT 1
                """,
                (relation_id, relation_id),
            ).fetchone()
            if not still_supported:
                conn.execute(
                    "UPDATE scoped_relations SET status = 'archived', updated_at = ? WHERE id = ?",
                    (now, relation_id),
                )

    @staticmethod
    def _record_observation(
        conn: sqlite3.Connection,
        *,
        fact_id: str,
        raw_fact: str,
        relation: str,
        source_session: Optional[str],
        source_run: Optional[str],
        source_message_ids: Optional[List[str]],
        confidence: float,
        importance: int,
        durability: str,
        evidence_refs: Optional[List[str]],
        transcript_hash: str,
        created_at: Optional[str] = None,
    ) -> bool:
        observation_id = f"obs-{hashlib.sha256(f'{fact_id}:{transcript_hash}'.encode('utf-8')).hexdigest()[:24]}"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_observations (
                id, fact_id, source_session, source_run, source_message_ids_json,
                raw_fact, relation, confidence, importance, durability,
                evidence_refs_json, transcript_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                fact_id,
                source_session,
                source_run,
                json.dumps(list(source_message_ids or []), ensure_ascii=False),
                raw_fact,
                relation,
                _bounded_confidence(confidence),
                max(0, min(int(importance or 0), 100)),
                str(durability or "operational"),
                json.dumps(list(evidence_refs or []), ensure_ascii=False),
                transcript_hash,
                created_at or _utc_now_iso(),
            ),
        )
        return cursor.rowcount > 0

    def reinforce_duplicate_knowledge(
        self,
        fact_id: str,
        canonical_fact_id: str,
        *,
        reason: str = "maintenance_exact_duplicate",
    ) -> bool:
        """Merge exact-duplicate evidence before retiring the duplicate row.

        This path is deliberately stricter than a generic similarity merge. It
        only accepts active facts with the same scope, category and normalized
        text, and preserves every independent observation on the canonical fact.
        """
        source_id = str(fact_id or "").strip()
        target_id = str(canonical_fact_id or "").strip()
        if not source_id or not target_id or source_id == target_id:
            return False
        now = _utc_now_iso()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute("SELECT rowid, * FROM knowledge WHERE id = ?", (source_id,)).fetchone()
            target = conn.execute("SELECT rowid, * FROM knowledge WHERE id = ?", (target_id,)).fetchone()
            if not source or not target:
                return False
            for row in (source, target):
                if (
                    str(row["status"] or "active").strip().lower() != "active"
                    or str(row["lifecycle_state"] or "active").strip().lower() != "active"
                ):
                    return False
            if (
                str(source["scope"] or "global") != str(target["scope"] or "global")
                or str(source["category"] or "general") != str(target["category"] or "general")
                or _normalize_fact_for_compaction(source["fact"])
                != _normalize_fact_for_compaction(target["fact"])
            ):
                return False

            observations = conn.execute(
                "SELECT * FROM knowledge_observations WHERE fact_id = ? ORDER BY created_at, id",
                (source_id,),
            ).fetchall()
            if not observations:
                observations = [
                    {
                        "raw_fact": str(source["fact"] or ""),
                        "source_session": source["source_session"],
                        "source_run": None,
                        "source_message_ids_json": "[]",
                        "confidence": source["confidence"],
                        "importance": source["importance"],
                        "durability": source["durability"],
                        "evidence_refs_json": source["evidence_refs_json"],
                        "transcript_hash": f"legacy-fact:{source_id}",
                        "created_at": source["valid_from"] or source["updated_at"] or now,
                    }
                ]

            inserted_observations = 0
            merged_evidence: set[str] = set()
            observed_confidence = 0.0
            observed_importance = 0
            for payload in (target["evidence_refs_json"], source["evidence_refs_json"]):
                try:
                    merged_evidence.update(str(item) for item in json.loads(payload or "[]") if str(item).strip())
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            for observation in observations:
                try:
                    source_message_ids = json.loads(observation["source_message_ids_json"] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    source_message_ids = []
                try:
                    observation_evidence = json.loads(observation["evidence_refs_json"] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    observation_evidence = []
                merged_evidence.update(str(item) for item in observation_evidence if str(item).strip())
                observed_confidence = max(observed_confidence, float(observation["confidence"] or 0.0))
                observed_importance = max(observed_importance, int(observation["importance"] or 0))
                if self._record_observation(
                    conn,
                    fact_id=target_id,
                    raw_fact=str(observation["raw_fact"] or source["fact"] or ""),
                    relation="reinforce",
                    source_session=observation["source_session"],
                    source_run=observation["source_run"],
                    source_message_ids=source_message_ids,
                    confidence=float(observation["confidence"] or 0.0),
                    importance=int(observation["importance"] or 0),
                    durability=str(observation["durability"] or "operational"),
                    evidence_refs=observation_evidence,
                    transcript_hash=str(observation["transcript_hash"] or f"legacy-fact:{source_id}"),
                    created_at=str(observation["created_at"] or now),
                ):
                    inserted_observations += 1

            target_confidence = max(
                float(target["confidence"] or 0.0),
                float(source["confidence"] or 0.0),
                observed_confidence,
            )
            target_effective = max(
                float(target["effective_confidence"] or 0.0),
                float(source["effective_confidence"] or 0.0),
            )
            target_importance = max(
                int(target["importance"] or 0),
                int(source["importance"] or 0),
                observed_importance,
            )
            conn.execute(
                """
                UPDATE knowledge
                SET last_seen_at = ?, confidence = ?, effective_confidence = ?,
                    importance = ?, evidence_refs_json = ?
                WHERE id = ?
                """,
                (
                    now,
                    target_confidence,
                    target_effective,
                    target_importance,
                    json.dumps(sorted(merged_evidence), ensure_ascii=False),
                    target_id,
                ),
            )

            metadata: Dict[str, object] = {}
            try:
                metadata = json.loads(source["metadata_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            maintenance = dict(metadata.get("maintenance") or {})
            maintenance.update(
                {
                    "lastAction": "superseded",
                    "reason": reason,
                    "supersededBy": target_id,
                    "mergedObservationCount": inserted_observations,
                    "updatedAt": now,
                }
            )
            metadata["maintenance"] = maintenance
            cursor = conn.execute(
                """
                UPDATE knowledge
                SET lifecycle_state = 'superseded', superseded_by = ?, valid_to = ?,
                    metadata_json = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
                  AND COALESCE(lifecycle_state, 'active') = 'active'
                """,
                (target_id, now, json.dumps(metadata, ensure_ascii=False), now, source_id),
            )
            if not cursor.rowcount:
                return False
            conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (int(source["rowid"]),))
            self._deactivate_unsupported_relations(conn, fact_id=source_id)
            self._enqueue_projection(
                conn,
                fact_id=source_id,
                operation="remove",
                event_key=f"{source_id}:superseded:{target_id}",
            )
            self._enqueue_projection(
                conn,
                fact_id=target_id,
                operation="upsert",
                event_key=f"{target_id}:reinforced-by:{source_id}",
            )
            return True

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
        agents_hash: Optional[str] = None,
        repo_signature: Optional[str] = None,
        signature_policy: str = "soft_v1",
        promotion_reason: Optional[str] = None,
        metadata: Optional[Dict] = None,
        fact_id: Optional[str] = None,
        lineage_id_override: Optional[str] = None,
        revision_no_override: Optional[int] = None,
        allow_stale_target: bool = False,
    ) -> Dict[str, object]:
        """Write canonical knowledge and its observation in one SQLite transaction.

        Similarity heuristics never replace an existing fact here.  Only an exact
        normalized match can be reinforced automatically; replacements require an
        explicit target from the caller/user evidence.
        """
        normalized_fact = str(fact or "").strip()
        if not normalized_fact:
            raise ValueError("fact is required")
        normalized_scope = str(scope or "global").strip() or "global"
        normalized_category = str(category or "general").strip() or "general"
        requested_relation = _normalize_knowledge_relation(relation)
        normalized_source = str(maintainer_source or "memory_runtime").strip() or "memory_runtime"
        bounded_confidence = _bounded_confidence(confidence)
        bounded_importance = max(0, min(int(importance or 0), 100))
        normalized_durability = str(durability or "operational").strip().lower() or "operational"
        normalized_evidence_refs = _normalize_evidence_refs(evidence_refs)
        observation_hash = _observation_hash(
            fact=normalized_fact,
            scope=normalized_scope,
            category=normalized_category,
            relation=requested_relation,
            source_session=source_session,
            source_run=source_run,
            transcript_hash=transcript_hash,
        )
        now = _utc_now_iso()

        with self._conn() as conn:
            # Serialize the read-before-write relation decision. Without an
            # immediate writer lock, concurrent extractors can all observe no
            # exact match and create duplicate active facts.
            conn.execute("BEGIN IMMEDIATE")
            target = None
            if target_fact_id:
                target = conn.execute("SELECT rowid, * FROM knowledge WHERE id = ?", (target_fact_id,)).fetchone()

            if requested_relation == "new" and target is None:
                target = conn.execute(
                    """
                    SELECT rowid, * FROM knowledge
                    WHERE scope = ? AND category = ? AND status = 'active'
                      AND COALESCE(lifecycle_state, 'active') = 'active'
                    ORDER BY revision_no DESC, updated_at DESC
                    """,
                    (normalized_scope, normalized_category),
                ).fetchall()
                exact = next(
                    (
                        row
                        for row in target
                        if _normalize_fact_for_compaction(row["fact"]) == _normalize_fact_for_compaction(normalized_fact)
                    ),
                    None,
                )
                target = exact
                if exact is not None:
                    requested_relation = "reinforce"

            if requested_relation in {"replace", "reinforce", "refine"} and target is None:
                if requested_relation == "reinforce":
                    requested_relation = "new"
                else:
                    raise ValueError(f"{requested_relation} requires an existing target fact")

            if target is not None and requested_relation in {"replace", "reinforce", "refine", "conflict"}:
                target_scope = str(target["scope"] or "global")
                if target_scope != normalized_scope:
                    raise ValueError("knowledge replacement/refinement cannot cross scope boundaries")
                target_lifecycle = str(target["lifecycle_state"] or "active").strip().lower()
                allow_explicit_stale_replacement = bool(
                    allow_stale_target and requested_relation == "replace" and target_lifecycle == "stale"
                )
                if (
                    str(target["status"] or "active").strip().lower() != "active"
                    or (target_lifecycle != "active" and not allow_explicit_stale_replacement)
                ):
                    raise ValueError("knowledge relation target is not the current canonical fact")
                if allow_explicit_stale_replacement:
                    current_lineage_fact = conn.execute(
                        """
                        SELECT id FROM knowledge
                        WHERE lineage_id = ? AND status = 'active'
                          AND COALESCE(lifecycle_state, 'active') = 'active'
                        ORDER BY revision_no DESC, updated_at DESC
                        LIMIT 1
                        """,
                        (str(target["lineage_id"] or target["id"]),),
                    ).fetchone()
                    if current_lineage_fact and str(current_lineage_fact["id"]) != str(target["id"]):
                        raise ValueError("stale knowledge target already has a newer canonical revision")

            if requested_relation == "reinforce" and target is not None:
                canonical_fact_id = str(target["id"])
                inserted = self._record_observation(
                    conn,
                    fact_id=canonical_fact_id,
                    raw_fact=normalized_fact,
                    relation="reinforce",
                    source_session=source_session,
                    source_run=source_run,
                    source_message_ids=source_message_ids,
                    confidence=bounded_confidence,
                    importance=bounded_importance,
                    durability=normalized_durability,
                    evidence_refs=normalized_evidence_refs,
                    transcript_hash=observation_hash,
                )
                if inserted:
                    existing_evidence_refs: List[str] = []
                    try:
                        existing_evidence_refs = _normalize_evidence_refs(json.loads(target["evidence_refs_json"] or "[]"))
                    except Exception:
                        existing_evidence_refs = []
                    merged_evidence_refs = _normalize_evidence_refs([*existing_evidence_refs, *normalized_evidence_refs])
                    conn.execute(
                        """
                        UPDATE knowledge
                        SET last_seen_at = ?, confidence = MAX(confidence, ?),
                            effective_confidence = MAX(effective_confidence, ?),
                            importance = MAX(COALESCE(importance, 0), ?),
                            last_verified_at = ?, evidence_refs_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            now,
                            bounded_confidence,
                            _effective_confidence(bounded_confidence, normalized_source),
                            bounded_importance,
                            now,
                            json.dumps(merged_evidence_refs, ensure_ascii=False),
                            now,
                            canonical_fact_id,
                        ),
                    )
                    self._enqueue_projection(
                        conn,
                        fact_id=canonical_fact_id,
                        operation="upsert",
                        event_key=f"{canonical_fact_id}:reinforce:{observation_hash}",
                    )
                return {
                    "action": "reinforce",
                    "factId": canonical_fact_id,
                    "canonicalFactId": canonical_fact_id,
                    "lineageId": str(target["lineage_id"] or canonical_fact_id),
                    "projectionState": "queued" if inserted else "ready",
                }

            new_fact_id = str(fact_id or f"fact-{uuid.uuid4().hex[:12]}").strip()
            if not new_fact_id:
                raise ValueError("fact_id is required")
            existing_id = conn.execute("SELECT id FROM knowledge WHERE id = ?", (new_fact_id,)).fetchone()
            if existing_id:
                existing = conn.execute("SELECT * FROM knowledge WHERE id = ?", (new_fact_id,)).fetchone()
                return {
                    "action": "reinforce" if _normalize_fact_for_compaction(existing["fact"]) == _normalize_fact_for_compaction(normalized_fact) else "conflict",
                    "factId": new_fact_id,
                    "canonicalFactId": str(existing["id"]),
                    "lineageId": str(existing["lineage_id"] or existing["id"]),
                    "projectionState": "ready",
                }

            lineage_id = str(lineage_id_override or new_fact_id).strip() or new_fact_id
            revision_no = max(1, int(revision_no_override or 1))
            lifecycle_state = "quarantined" if requested_relation == "conflict" else "active"
            status = "quarantined" if requested_relation == "conflict" else "active"
            replaced_fact_id: Optional[str] = None
            replaced_stale_target = False
            if requested_relation == "replace" and target is not None:
                lineage_id = str(target["lineage_id"] or target["id"])
                max_lineage_revision = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(revision_no), 0) FROM knowledge WHERE lineage_id = ?",
                        (lineage_id,),
                    ).fetchone()[0]
                )
                revision_no = max(int(target["revision_no"] or 1), max_lineage_revision) + 1
                replaced_fact_id = str(target["id"])
                replaced_stale_target = str(target["lifecycle_state"] or "active").strip().lower() == "stale"
            metadata_payload = dict(metadata or {})
            if replaced_stale_target:
                # Stale facts are normally non-injectable.  A human-confirmed
                # correction may still create a new revision, but never revives
                # the old row or silently treats it as current truth.
                metadata_payload["replacedStaleTarget"] = True
            if target is not None and requested_relation in {"refine", "conflict"}:
                metadata_payload["relationTargetFactId"] = str(target["id"])
                metadata_payload["knowledgeRelation"] = requested_relation

            conn.execute(
                """
                INSERT INTO knowledge (
                    id, fact, category, scope, status, source_session, parent_id,
                    lifecycle_state, last_seen_at, agents_hash, repo_signature,
                    signature_policy, maintainer_source, confidence, effective_confidence,
                    evidence_refs_json, promotion_reason, metadata_json, lineage_id,
                    revision_no, importance, durability, valid_from, valid_to, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    new_fact_id,
                    normalized_fact,
                    normalized_category,
                    normalized_scope,
                    status,
                    source_session,
                    parent_id,
                    lifecycle_state,
                    now,
                    agents_hash,
                    repo_signature,
                    signature_policy,
                    normalized_source,
                    bounded_confidence,
                    _effective_confidence(bounded_confidence, normalized_source),
                    json.dumps(normalized_evidence_refs, ensure_ascii=False),
                    promotion_reason,
                    json.dumps(metadata_payload, ensure_ascii=False),
                    lineage_id,
                    revision_no,
                    bounded_importance,
                    normalized_durability,
                    now,
                    now,
                ),
            )
            rowid = int(conn.execute("SELECT rowid FROM knowledge WHERE id = ?", (new_fact_id,)).fetchone()[0])
            if status == "active":
                self._insert_fts_row(
                    conn,
                    rowid=rowid,
                    fact=normalized_fact,
                    category=normalized_category,
                    scope=normalized_scope,
                )

            if replaced_fact_id:
                conn.execute(
                    """
                    UPDATE knowledge
                    SET lifecycle_state = 'superseded', superseded_by = ?, valid_to = ?
                    WHERE id = ? AND status = 'active'
                    """,
                    (new_fact_id, now, replaced_fact_id),
                )
                self._record_lifecycle_audit(
                    conn,
                    fact_id=replaced_fact_id,
                    action="superseded",
                    actor=normalized_source,
                    reason="knowledge_replace_stale_target" if replaced_stale_target else "knowledge_replace",
                    evidence_refs=normalized_evidence_refs,
                    metadata={"supersededBy": new_fact_id, "replacedStaleTarget": replaced_stale_target},
                )
                old_rowid = int(target["rowid"])
                conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (old_rowid,))
                self._deactivate_unsupported_relations(conn, fact_id=replaced_fact_id)
                self._enqueue_projection(
                    conn,
                    fact_id=replaced_fact_id,
                    operation="remove",
                    event_key=f"{replaced_fact_id}:superseded:{new_fact_id}",
                )

            self._record_observation(
                conn,
                fact_id=new_fact_id,
                raw_fact=normalized_fact,
                relation=requested_relation,
                source_session=source_session,
                source_run=source_run,
                source_message_ids=source_message_ids,
                confidence=bounded_confidence,
                importance=bounded_importance,
                durability=normalized_durability,
                evidence_refs=normalized_evidence_refs,
                transcript_hash=observation_hash,
            )
            self._record_lifecycle_audit(
                conn,
                fact_id=new_fact_id,
                action="quarantined" if requested_relation == "conflict" else "created",
                actor=normalized_source,
                reason=f"knowledge_{requested_relation}",
                evidence_refs=normalized_evidence_refs,
                metadata={"lineageId": lineage_id, "revisionNo": revision_no},
            )
            resolution_candidate_id: Optional[str] = None
            if requested_relation == "conflict":
                resolution_candidate_id = f"resolution-{uuid.uuid4().hex[:16]}"
                conn.execute(
                    """
                    INSERT INTO knowledge_resolution_candidates (
                        id, candidate_fact_id, target_fact_id, proposed_relation, state, reason, created_at
                    ) VALUES (?, ?, ?, 'conflict', 'pending', ?, ?)
                    """,
                    (
                        resolution_candidate_id,
                        new_fact_id,
                        str(target["id"]) if target is not None else None,
                        "explicit_or_uncertain_conflict",
                        now,
                    ),
                )
            self._enqueue_projection(
                conn,
                fact_id=new_fact_id,
                operation="remove" if status != "active" else "upsert",
                event_key=f"{new_fact_id}:create:{observation_hash}",
            )
            result: Dict[str, object] = {
                "action": requested_relation,
                "factId": new_fact_id,
                "canonicalFactId": new_fact_id,
                "lineageId": lineage_id,
                "projectionState": "queued",
            }
            if replaced_fact_id:
                result["replacedFactId"] = replaced_fact_id
            if resolution_candidate_id:
                result["resolutionCandidateId"] = resolution_candidate_id
            return result
    
    def add_knowledge(self, fact_id: str, fact: str, category: str = "general",
                      scope: str = "global", source_session: Optional[str] = None,
                      parent_id: Optional[str] = None,
                      lifecycle_state: str = "active",
                      agents_hash: Optional[str] = None,
                      repo_signature: Optional[str] = None,
                      signature_policy: str = "soft_v1",
                      maintainer_source: str = "memory_runtime",
                      confidence: float = 1.0,
                      evidence_refs: Optional[List[str]] = None,
                      promotion_reason: Optional[str] = None,
                      metadata: Optional[Dict] = None):
        """添加知识条目 + 同步 FTS5 索引（自动 jieba 分词）"""
        now = _utc_now_iso()
        normalized_lifecycle = str(lifecycle_state or "active").strip().lower() or "active"
        normalized_status = (
            "deleted"
            if normalized_lifecycle == "tombstoned"
            else ("quarantined" if normalized_lifecycle == "quarantined" else "active")
        )
        normalized_source = str(maintainer_source or "memory_runtime").strip() or "memory_runtime"
        bounded_confidence = _bounded_confidence(confidence)
        effective = _effective_confidence(bounded_confidence, normalized_source)
        evidence_refs_json = json.dumps(list(evidence_refs or []), ensure_ascii=False)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        
        with self._conn() as conn:
            # 检查是否已存在
            existing = conn.execute(
                "SELECT rowid FROM knowledge WHERE id = ?", (fact_id,)
            ).fetchone()
            
            if existing:
                # Legacy/import callers may replay an already projected JSON item.
                # SQLite is canonical: an existing row is never overwritten or
                # reactivated from a derived projection.
                return
            else:
                # 新增
                conn.execute("""
                    INSERT INTO knowledge (
                        id, fact, category, scope, status, source_session, parent_id,
                        lifecycle_state, last_seen_at, agents_hash, repo_signature,
                        signature_policy, maintainer_source, confidence, effective_confidence,
                        evidence_refs_json, promotion_reason, metadata_json, lineage_id,
                        revision_no, importance, durability, valid_from, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 50, 'operational', ?, ?)
                """, (
                    fact_id, fact, category, scope, normalized_status, source_session, parent_id,
                    normalized_lifecycle, now, agents_hash, repo_signature,
                    signature_policy, normalized_source, bounded_confidence, effective,
                    evidence_refs_json, promotion_reason, metadata_json, fact_id, now, now,
                ))
                
                new_rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                injectable = normalized_status == "active" and normalized_lifecycle == "active"
                if injectable:
                    self._insert_fts_row(
                        conn,
                        rowid=int(new_rowid),
                        fact=fact,
                        category=category,
                        scope=scope,
                    )
                transcript_hash = hashlib.sha256(
                    f"legacy-import:{fact_id}:{scope}:{category}:{fact}".encode("utf-8")
                ).hexdigest()
                self._record_observation(
                    conn,
                    fact_id=fact_id,
                    raw_fact=fact,
                    relation="new",
                    source_session=source_session,
                    source_run=None,
                    source_message_ids=None,
                    confidence=bounded_confidence,
                    importance=50,
                    durability="operational",
                    evidence_refs=evidence_refs,
                    transcript_hash=transcript_hash,
                )
                self._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="upsert" if injectable else "remove",
                    event_key=f"{fact_id}:legacy-add",
                )
    
    def get_user_documents(self) -> List[Dict]:
        """获取所有上传的用户文档列表。利用 source_session 存储 filename"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT source_session as filename,
                       COUNT(id) as chunk_count,
                       MAX(created_at) as uploaded_at
                FROM knowledge
                WHERE category = 'user_document' AND source_session IS NOT NULL
                  AND status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded', 'quarantined')
                GROUP BY source_session
                ORDER BY uploaded_at DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def delete_user_document(self, filename: str) -> List[str]:
        """Tombstone all active chunks for a document and enqueue projection removal."""
        now = _utc_now_iso()
        with self._conn() as conn:
            # 找到要删除的记录 ID
            rows = conn.execute(
                "SELECT rowid, id FROM knowledge WHERE category = 'user_document' AND source_session = ? AND status = 'active'",
                (filename,)
            ).fetchall()
            
            if not rows:
                return []
                
            rowids = [r[0] for r in rows]
            fact_ids = [r[1] for r in rows]
            
            # 删除 FTS5 记录
            conn.execute(
                f"DELETE FROM knowledge_fts WHERE rowid IN ({','.join('?' * len(rowids))})", 
                rowids
            )
            
            conn.execute(
                """
                UPDATE knowledge
                SET status = 'deleted', lifecycle_state = 'tombstoned', valid_to = ?, updated_at = ?
                WHERE category = 'user_document' AND source_session = ? AND status = 'active'
                """,
                (now, now, filename),
            )
            for fact_id in fact_ids:
                self._deactivate_unsupported_relations(conn, fact_id=fact_id)
                self._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="remove",
                    event_key=f"document-delete:{filename}:{fact_id}:{now}",
                )
                self._record_lifecycle_audit(
                    conn,
                    fact_id=fact_id,
                    action="tombstoned",
                    actor="human_admin",
                    reason="document_delete",
                    evidence_refs=[f"document:{filename}"],
                )
            
            return fact_ids

    def replace_user_document_chunks(
        self,
        *,
        filename: str,
        chunks: List[Dict[str, object]],
        maintainer_source: str,
        confidence: float,
        promotion_reason: str,
    ) -> Dict[str, List[str]]:
        """Replace a document's canonical chunks in one SQLite transaction."""
        now = _utc_now_iso()
        bounded = _bounded_confidence(confidence)
        effective = _effective_confidence(bounded, maintainer_source)
        with self._conn() as conn:
            old_rows = conn.execute(
                """
                SELECT rowid, id FROM knowledge
                WHERE category = 'user_document' AND source_session = ? AND status = 'active'
                """,
                (filename,),
            ).fetchall()
            old_ids = [str(row["id"]) for row in old_rows]
            if old_rows:
                conn.execute(
                    f"DELETE FROM knowledge_fts WHERE rowid IN ({','.join('?' for _ in old_rows)})",
                    [int(row["rowid"]) for row in old_rows],
                )
                conn.execute(
                    """
                    UPDATE knowledge
                    SET status = 'deleted', lifecycle_state = 'tombstoned', valid_to = ?, updated_at = ?
                    WHERE category = 'user_document' AND source_session = ? AND status = 'active'
                    """,
                    (now, now, filename),
                )
                for old_id in old_ids:
                    self._deactivate_unsupported_relations(conn, fact_id=old_id)
                    self._enqueue_projection(
                        conn,
                        fact_id=old_id,
                        operation="remove",
                        event_key=f"document-replace:{filename}:remove:{old_id}:{now}",
                    )
                    self._record_lifecycle_audit(
                        conn,
                        fact_id=old_id,
                        action="tombstoned",
                        actor=maintainer_source,
                        reason="document_replace",
                        evidence_refs=[f"document:{filename}"],
                    )

            new_ids: List[str] = []
            for chunk in chunks:
                chunk_id = str(chunk.get("id") or f"upload-{uuid.uuid4().hex[:12]}").strip()
                chunk_fact = str(chunk.get("fact") or "").strip()
                if not chunk_fact:
                    continue
                parent_id = str(chunk.get("parent_id") or "").strip() or None
                metadata = dict(chunk.get("metadata") or {})
                document_evidence_refs = [f"document:{filename}"]
                conn.execute(
                    """
                    INSERT INTO knowledge (
                        id, fact, category, scope, status, source_session, parent_id,
                        lifecycle_state, last_seen_at, evidence_refs_json, promotion_reason,
                        metadata_json, lineage_id, revision_no, importance, durability,
                        valid_from, maintainer_source, confidence, effective_confidence, updated_at
                    ) VALUES (?, ?, 'user_document', 'global', 'active', ?, ?, 'active', ?, ?, ?, ?, ?, 1, 50, 'stable', ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        chunk_fact,
                        filename,
                        parent_id,
                        now,
                        json.dumps(document_evidence_refs, ensure_ascii=False),
                        promotion_reason,
                        json.dumps(metadata, ensure_ascii=False),
                        chunk_id,
                        now,
                        maintainer_source,
                        bounded,
                        effective,
                        now,
                    ),
                )
                rowid = int(conn.execute("SELECT rowid FROM knowledge WHERE id = ?", (chunk_id,)).fetchone()[0])
                self._insert_fts_row(
                    conn,
                    rowid=rowid,
                    fact=chunk_fact,
                    category="user_document",
                    scope="global",
                )
                transcript_hash = hashlib.sha256(
                    f"document:{filename}:{chunk_id}:{chunk_fact}".encode("utf-8")
                ).hexdigest()
                self._record_observation(
                    conn,
                    fact_id=chunk_id,
                    raw_fact=chunk_fact,
                    relation="new",
                    source_session=filename,
                    source_run=None,
                    source_message_ids=None,
                    confidence=bounded,
                    importance=50,
                    durability="stable",
                    evidence_refs=document_evidence_refs,
                    transcript_hash=transcript_hash,
                )
                self._record_lifecycle_audit(
                    conn,
                    fact_id=chunk_id,
                    action="created",
                    actor=maintainer_source,
                    reason="document_ingest",
                    evidence_refs=document_evidence_refs,
                    metadata={"filename": filename},
                )
                self._enqueue_projection(
                    conn,
                    fact_id=chunk_id,
                    operation="upsert",
                    event_key=f"document-replace:{filename}:upsert:{chunk_id}",
                )
                new_ids.append(chunk_id)
        return {"removed": old_ids, "created": new_ids}
    
    def delete_knowledge(
        self,
        fact_id: str,
        *,
        actor: str = "system",
        reason: str = "manual_delete",
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Tombstone long-term knowledge and retain an auditable lifecycle record."""
        now = _utc_now_iso()
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE knowledge
                SET status = 'deleted', lifecycle_state = 'tombstoned', valid_to = ?, updated_at = ?
                WHERE id = ?
                  AND status != 'deleted'
                  AND COALESCE(lifecycle_state, 'active') != 'tombstoned'
                """,
                (now, now, fact_id)
            )
            if cursor.rowcount:
                rowid = conn.execute("SELECT rowid FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                if rowid:
                    conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (rowid[0],))
                self._deactivate_unsupported_relations(conn, fact_id=fact_id)
                self._enqueue_projection(conn, fact_id=fact_id, operation="remove")
                self._record_lifecycle_audit(
                    conn,
                    fact_id=fact_id,
                    action="tombstoned",
                    actor=actor,
                    reason=reason,
                    evidence_refs=evidence_refs,
                    metadata=metadata,
                )
            return cursor.rowcount > 0

    def set_knowledge_status(self, fact_id: str, status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"active", "deleted", "quarantined"}:
            return False
        with self._conn() as conn:
            current = conn.execute(
                "SELECT rowid, fact, category, scope, lifecycle_state, superseded_by FROM knowledge WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if not current:
                return False
            if normalized_status == "active" and (
                str(current["lifecycle_state"] or "").strip().lower() in {"superseded", "tombstoned", "quarantined"}
                or str(current["superseded_by"] or "").strip()
            ):
                return False
            lifecycle_state = "active" if normalized_status == "active" else ("tombstoned" if normalized_status == "deleted" else "quarantined")
            cursor = conn.execute(
                "UPDATE knowledge SET status = ?, lifecycle_state = ?, updated_at = ? WHERE id = ?",
                (normalized_status, lifecycle_state, _utc_now_iso(), fact_id),
            )
            if cursor.rowcount:
                if normalized_status == "active":
                    self._insert_fts_row(
                        conn,
                        rowid=int(current["rowid"]),
                        fact=str(current["fact"] or ""),
                        category=str(current["category"] or "general"),
                        scope=str(current["scope"] or "global"),
                    )
                else:
                    conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (int(current["rowid"]),))
                    self._deactivate_unsupported_relations(conn, fact_id=fact_id)
                self._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="upsert" if normalized_status == "active" else "remove",
                )
            return cursor.rowcount > 0

    def quarantine_knowledge(self, fact_id: str) -> bool:
        return self.set_knowledge_status(fact_id, "quarantined")

    def mark_knowledge_superseded(
        self,
        fact_id: str,
        superseded_by: str,
        *,
        reason: str = "maintenance_duplicate",
    ) -> bool:
        """Soft-supersede a duplicate knowledge item without deleting evidence."""
        fact_id = str(fact_id or "").strip()
        superseded_by = str(superseded_by or "").strip()
        if not fact_id or not superseded_by or fact_id == superseded_by:
            return False
        now = _utc_now_iso()
        with self._conn() as conn:
            row = conn.execute("SELECT metadata_json FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
            if not row:
                return False
            metadata: Dict[str, object] = {}
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            maintenance = dict(metadata.get("maintenance") or {})
            maintenance.update(
                {
                    "lastAction": "superseded",
                    "reason": reason,
                    "supersededBy": superseded_by,
                    "updatedAt": now,
                }
            )
            metadata["maintenance"] = maintenance
            cursor = conn.execute(
                """
                UPDATE knowledge
                SET lifecycle_state = 'superseded',
                    superseded_by = ?,
                    valid_to = ?,
                    metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded')
                """,
                (superseded_by, now, json.dumps(metadata, ensure_ascii=False), now, fact_id),
            )
            if cursor.rowcount:
                rowid = conn.execute("SELECT rowid FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                if rowid:
                    conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (rowid[0],))
                self._deactivate_unsupported_relations(conn, fact_id=fact_id)
                self._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="remove",
                    event_key=f"{fact_id}:superseded:{superseded_by}",
                )
                self._record_lifecycle_audit(
                    conn,
                    fact_id=fact_id,
                    action="superseded",
                    actor="memory_maintenance",
                    reason=reason,
                    metadata={"supersededBy": superseded_by},
                )
            return cursor.rowcount > 0

    def mark_knowledge_merge_suggestion(
        self,
        fact_id: str,
        target_id: str,
        *,
        similarity: float,
        reason: str = "maintenance_similar_below_auto_threshold",
    ) -> bool:
        fact_id = str(fact_id or "").strip()
        target_id = str(target_id or "").strip()
        if not fact_id or not target_id or fact_id == target_id:
            return False
        now = _utc_now_iso()
        similarity_value = round(_bounded_confidence(similarity, default=0.0), 4)
        pair_ids = sorted((fact_id, target_id))
        candidate_id = "resolution-" + hashlib.sha256(
            f"{pair_ids[0]}:{pair_ids[1]}:similarity".encode("utf-8")
        ).hexdigest()[:16]
        with self._conn() as conn:
            row = conn.execute("SELECT metadata_json FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
            if not row:
                return False
            metadata: Dict[str, object] = {}
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            maintenance = dict(metadata.get("maintenance") or {})
            existing_suggestion = maintenance.get("mergeSuggestion") if isinstance(maintenance.get("mergeSuggestion"), dict) else {}
            existing_candidate = conn.execute(
                """
                SELECT id, candidate_fact_id, target_fact_id, similarity, reason
                FROM knowledge_resolution_candidates
                WHERE state = 'pending'
                  AND ((candidate_fact_id = ? AND target_fact_id = ?)
                    OR (candidate_fact_id = ? AND target_fact_id = ?))
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (fact_id, target_id, target_id, fact_id),
            ).fetchone()
            suggestion_unchanged = (
                str(existing_suggestion.get("targetId") or "") == target_id
                and round(_bounded_confidence(existing_suggestion.get("similarity"), default=0.0), 4) == similarity_value
                and str(existing_suggestion.get("reason") or "") == reason
            )
            candidate_unchanged = bool(
                existing_candidate
                and str(existing_candidate["candidate_fact_id"] or "") == fact_id
                and str(existing_candidate["target_fact_id"] or "") == target_id
                and round(_bounded_confidence(existing_candidate["similarity"], default=0.0), 4) == similarity_value
                and str(existing_candidate["reason"] or "") == reason
            )
            if suggestion_unchanged and candidate_unchanged:
                return False
            maintenance["mergeSuggestion"] = {
                "targetId": target_id,
                "similarity": similarity_value,
                "reason": reason,
                "updatedAt": now,
            }
            metadata["maintenance"] = maintenance
            cursor = conn.execute(
                """
                UPDATE knowledge
                SET metadata_json = ?
                WHERE id = ?
                  AND status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded')
                """,
                (json.dumps(metadata, ensure_ascii=False), fact_id),
            )
            if cursor.rowcount:
                conn.execute(
                    """
                    DELETE FROM knowledge_resolution_candidates
                    WHERE state = 'pending'
                      AND id != ?
                      AND ((candidate_fact_id = ? AND target_fact_id = ?)
                        OR (candidate_fact_id = ? AND target_fact_id = ?))
                    """,
                    (candidate_id, fact_id, target_id, target_id, fact_id),
                )
                conn.execute(
                    """
                    INSERT INTO knowledge_resolution_candidates (
                        id, candidate_fact_id, target_fact_id, proposed_relation,
                        state, similarity, reason, created_at
                    ) VALUES (?, ?, ?, 'reinforce', 'pending', ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        candidate_fact_id = excluded.candidate_fact_id,
                        target_fact_id = excluded.target_fact_id,
                        similarity = excluded.similarity,
                        reason = excluded.reason
                    """,
                    (candidate_id, fact_id, target_id, similarity_value, reason, now),
                )
            return cursor.rowcount > 0

    def maintenance_compact_knowledge(
        self,
        *,
        limit: int = 500,
        auto_supersede_threshold: float = 0.985,
        max_clusters: int = 80,
        cursor_after: Optional[str] = None,
    ) -> Dict[str, object]:
        """Conservatively dedupe highly similar same-scope facts.

        This is deterministic and intentionally narrow: it only acts inside the
        same scope and category, and prefers lifecycle superseding over deletion.
        """
        effective_limit = max(1, min(int(limit or 500), 2000))
        threshold = max(0.95, min(float(auto_supersede_threshold or 0.985), 1.0))
        cluster_budget = max(1, min(int(max_clusters or 80), 500))
        wrapped = False
        with self._conn() as conn:
            params: List[object] = []
            cursor_clause = ""
            if cursor_after:
                cursor_clause = " AND id > ?"
                params.append(str(cursor_after))
            params.append(effective_limit)
            rows = conn.execute(
                f"""
                SELECT * FROM knowledge
                WHERE status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded', 'quarantined')
                  {cursor_clause}
                ORDER BY id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            if not rows and cursor_after:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge
                    WHERE status = 'active'
                      AND COALESCE(lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded', 'quarantined')
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (effective_limit,),
                ).fetchall()
                wrapped = True
        items = [dict(row) for row in rows]
        buckets: Dict[tuple[str, str], List[Dict]] = {}
        for item in items:
            fact = _normalize_fact_for_compaction(item.get("fact"))
            if not fact:
                continue
            key = (str(item.get("scope") or "global").strip(), str(item.get("category") or "general").strip())
            normalized_item = dict(item)
            normalized_item["_normalized_fact"] = fact
            buckets.setdefault(key, []).append(normalized_item)

        duplicate_candidates = 0
        superseded_count = 0
        merge_suggestion_count = 0
        superseded_pairs: List[Dict[str, str]] = []
        merge_suggestions: List[Dict[str, object]] = []
        processed_clusters = 0

        def _keeper_key(item: Dict) -> tuple[float, int, str, str]:
            try:
                confidence = float(item.get("effective_confidence") or item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            try:
                importance = int(item.get("importance") or 0)
            except (TypeError, ValueError):
                importance = 0
            return (-confidence, -importance, str(item.get("created_at") or ""), str(item.get("id") or ""))

        for (_scope, _category), bucket in buckets.items():
            by_exact: Dict[str, List[Dict]] = {}
            for item in bucket:
                by_exact.setdefault(str(item.get("_normalized_fact") or ""), []).append(item)
            for exact_group in by_exact.values():
                if len(exact_group) < 2:
                    continue
                if processed_clusters >= cluster_budget:
                    break
                processed_clusters += 1
                keeper = sorted(exact_group, key=_keeper_key)[0]
                for duplicate in exact_group:
                    if duplicate.get("id") == keeper.get("id"):
                        continue
                    duplicate_candidates += 1
                    if self.reinforce_duplicate_knowledge(
                        str(duplicate.get("id")),
                        str(keeper.get("id")),
                        reason="maintenance_exact_duplicate",
                    ):
                        superseded_count += 1
                        superseded_pairs.append({"sourceId": str(duplicate.get("id")), "targetId": str(keeper.get("id")), "reason": "exact_duplicate"})
            if processed_clusters >= cluster_budget:
                break

            candidates = sorted(
                [
                    item
                    for group in by_exact.values()
                    if len(group) == 1
                    for item in group
                    if len(str(item.get("_normalized_fact") or "")) >= 16
                ],
                key=lambda item: str(item.get("_normalized_fact") or ""),
            )
            for index, left in enumerate(candidates):
                if processed_clusters >= cluster_budget:
                    break
                for right in candidates[index + 1 : min(index + 9, len(candidates))]:
                    if processed_clusters >= cluster_budget:
                        break
                    left_fact = str(left.get("_normalized_fact") or "")
                    right_fact = str(right.get("_normalized_fact") or "")
                    if not left_fact or not right_fact:
                        continue
                    ratio = SequenceMatcher(None, left_fact, right_fact).ratio()
                    if ratio < 0.94:
                        continue
                    processed_clusters += 1
                    duplicate_candidates += 1
                    keeper, duplicate = sorted([left, right], key=_keeper_key)[:2]
                    # Similarity is evidence for review, never authority to rewrite
                    # knowledge history.  Only normalized exact duplicates above
                    # are automatically reinforced/superseded.
                    if self.mark_knowledge_merge_suggestion(
                        str(duplicate.get("id")),
                        str(keeper.get("id")),
                        similarity=ratio,
                        reason="maintenance_similarity_review",
                    ):
                        merge_suggestion_count += 1
                        merge_suggestions.append(
                            {
                                "sourceId": str(duplicate.get("id")),
                                "targetId": str(keeper.get("id")),
                                "similarity": round(ratio, 4),
                                "reason": "similarity_requires_review",
                            }
                        )
                    if len(merge_suggestions) >= 20:
                        break

        graph_result = self.maintenance_compact_graph()
        next_cursor = str(items[-1].get("id") or "") if len(items) >= effective_limit else ""
        if len(items) < effective_limit:
            wrapped = True
        return {
            "candidateCount": duplicate_candidates,
            "supersededCount": superseded_count,
            "mergeSuggestionCount": merge_suggestion_count,
            "supersededPairs": superseded_pairs[:20],
            "mergeSuggestions": merge_suggestions[:20],
            "processedClusterCount": processed_clusters,
            "budgetStopped": processed_clusters >= cluster_budget,
            "cursor": {"nextCursor": next_cursor, "wrapped": wrapped, "batchCount": len(items)},
            "graph": graph_result,
        }

    def maintenance_compact_graph(
        self,
        *,
        limit: int = 500,
        isolated_entity_grace_days: int = 7,
    ) -> Dict[str, object]:
        """Repair graph references and prune old runtime-owned nodes without edges."""
        effective_limit = max(1, min(int(limit or 500), 2000))
        grace_days = max(1, min(int(isolated_entity_grace_days or 7), 365))
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT relation.id
                FROM scoped_relations relation
                WHERE relation.status = 'active'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM scoped_relation_evidence evidence
                    JOIN knowledge fact ON fact.id = evidence.fact_id
                    WHERE evidence.relation_id = relation.id
                      AND fact.status = 'active'
                      AND COALESCE(fact.lifecycle_state, 'active') NOT IN (
                        'stale', 'tombstoned', 'superseded', 'quarantined'
                      )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                    WHERE evidence_ref.relation_id = relation.id
                  )
                LIMIT ?
                """,
                (effective_limit,),
            ).fetchall()
            rewired = 0
            orphaned = len(rows)
            archived = 0
            if rows:
                relation_ids = [str(row["id"]) for row in rows]
                placeholders = ",".join("?" for _ in relation_ids)
                cursor = conn.execute(
                    f"UPDATE scoped_relations SET status = 'archived', updated_at = ? WHERE id IN ({placeholders})",
                    [_utc_now_iso(), *relation_ids],
                )
                archived = cursor.rowcount
            isolated_before = conn.execute(
                """
                SELECT COUNT(*)
                FROM entities e
                WHERE NOT EXISTS (
                    SELECT 1 FROM scoped_relations relation
                    WHERE relation.status = 'active'
                      AND (relation.subject = e.name OR relation.object = e.name)
                )
                """
            ).fetchone()[0]
            retained_legacy_entities = conn.execute(
                """
                SELECT COUNT(*)
                FROM entities e
                WHERE NOT EXISTS (
                    SELECT 1 FROM scoped_relations relation
                    WHERE relation.status = 'active'
                      AND (relation.subject = e.name OR relation.object = e.name)
                )
                  AND EXISTS (
                    SELECT 1 FROM relations legacy_relation
                    WHERE legacy_relation.subject = e.name OR legacy_relation.object = e.name
                  )
                """
            ).fetchone()[0]
            prunable_rows = conn.execute(
                """
                SELECT e.name
                FROM entities e
                WHERE COALESCE(e.maintainer_source, 'memory_runtime') = 'memory_runtime'
                  AND NOT EXISTS (
                      SELECT 1 FROM scoped_relations relation
                      WHERE relation.status = 'active'
                        AND (relation.subject = e.name OR relation.object = e.name)
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM relations legacy_relation
                      WHERE legacy_relation.subject = e.name OR legacy_relation.object = e.name
                  )
                  AND datetime(COALESCE(e.updated_at, e.created_at, '1970-01-01')) <= datetime('now', ?)
                ORDER BY datetime(COALESCE(e.updated_at, e.created_at, '1970-01-01')) ASC, e.name ASC
                LIMIT ?
                """,
                (f"-{grace_days} days", effective_limit),
            ).fetchall()
            prunable_names = [str(row["name"] or "").strip() for row in prunable_rows if str(row["name"] or "").strip()]
            pruned = 0
            if prunable_names:
                placeholders = ",".join("?" for _ in prunable_names)
                cursor = conn.execute(f"DELETE FROM entities WHERE name IN ({placeholders})", prunable_names)
                pruned = cursor.rowcount
            isolated_after = conn.execute(
                """
                SELECT COUNT(*)
                FROM entities e
                WHERE NOT EXISTS (
                    SELECT 1 FROM scoped_relations relation
                    WHERE relation.status = 'active'
                      AND (relation.subject = e.name OR relation.object = e.name)
                )
                """
            ).fetchone()[0]
        return {
            "relationCandidateCount": len(rows),
            "rewiredRelationCount": rewired,
            "orphanedRelationCount": orphaned,
            "archivedRelationCount": int(archived or 0),
            "isolatedEntityCountBefore": int(isolated_before or 0),
            "isolatedEntityCount": int(isolated_after or 0),
            "retainedLegacyEntityCount": int(retained_legacy_entities or 0),
            "prunedIsolatedEntityCount": int(pruned or 0),
            "isolatedEntityGraceDays": grace_days,
        }

    def mark_stale_for_signature_mismatch(
        self,
        *,
        scopes: List[str],
        agents_hash: str = "",
        repo_signature: str = "",
    ) -> int:
        scope_values = [str(item).strip() for item in list(scopes or []) if str(item).strip()]
        if not scope_values or not (agents_hash or repo_signature):
            return 0
        placeholders = ",".join("?" * len(scope_values))
        signature_params: list[object] = []
        signature_conditions: list[str] = []
        if agents_hash:
            signature_conditions.append("(agents_hash IS NOT NULL AND agents_hash != '' AND agents_hash != ?)")
            signature_params.append(agents_hash)
        if repo_signature:
            signature_conditions.append("(repo_signature IS NOT NULL AND repo_signature != '' AND repo_signature != ?)")
            signature_params.append(repo_signature)
        if not signature_conditions:
            return 0
        now = _utc_now_iso()
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id
                FROM knowledge
                WHERE status = 'active'
                  AND scope IN ({placeholders})
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded')
                  AND ({' OR '.join(signature_conditions)})
                """,
                [*scope_values, *signature_params],
            ).fetchall()
            fact_ids = [str(row["id"]) for row in rows]
            if not fact_ids:
                return 0
            id_placeholders = ",".join("?" for _ in fact_ids)
            conn.execute(
                f"UPDATE knowledge SET lifecycle_state = 'stale', updated_at = ? WHERE id IN ({id_placeholders})",
                [now, *fact_ids],
            )
            for fact_id in fact_ids:
                self._deactivate_unsupported_relations(conn, fact_id=fact_id)
                self._enqueue_projection(
                    conn,
                    fact_id=fact_id,
                    operation="remove",
                )
            return len(fact_ids)

    def revalidate_knowledge(
        self,
        fact_id: str,
        *,
        agents_hash: str = "",
        repo_signature: str = "",
        signature_policy: str = "soft_v1",
        maintainer_source: str = "human_admin",
    ) -> bool:
        now = _utc_now_iso()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT confidence, lifecycle_state, superseded_by FROM knowledge WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if not row:
                return False
            if (
                str(row["lifecycle_state"] or "").strip().lower() in {"superseded", "tombstoned", "quarantined"}
                or str(row["superseded_by"] or "").strip()
            ):
                return False
            confidence = _bounded_confidence(row["confidence"] if "confidence" in row.keys() else 1.0)
            effective = _effective_confidence(confidence, maintainer_source)
            cursor = conn.execute(
                """
                UPDATE knowledge
                SET lifecycle_state = 'active', status = 'active', last_verified_at = ?,
                    agents_hash = COALESCE(NULLIF(?, ''), agents_hash),
                    repo_signature = COALESCE(NULLIF(?, ''), repo_signature),
                    signature_policy = ?, maintainer_source = ?,
                    effective_confidence = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, agents_hash, repo_signature, signature_policy, maintainer_source, effective, now, fact_id),
            )
            if cursor.rowcount:
                conn.execute(
                    """
                    UPDATE scoped_relations
                    SET status = 'active', updated_at = ?
                    WHERE id IN (
                        SELECT evidence.relation_id
                        FROM scoped_relation_evidence evidence
                        WHERE evidence.fact_id = ?
                    )
                      AND (EXISTS (
                        SELECT 1
                        FROM scoped_relation_evidence evidence
                        JOIN knowledge fact ON fact.id = evidence.fact_id
                        WHERE evidence.relation_id = scoped_relations.id
                          AND fact.status = 'active'
                          AND COALESCE(fact.lifecycle_state, 'active') = 'active'
                      ) OR EXISTS (
                        SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                        WHERE evidence_ref.relation_id = scoped_relations.id
                      ))
                    """,
                    (now, fact_id),
                )
                self._enqueue_projection(conn, fact_id=fact_id, operation="upsert")
            return cursor.rowcount > 0
    
    def get_knowledge_count(self) -> int:
        """获取活跃知识条目数"""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM knowledge
                WHERE status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded', 'quarantined')
                """
            ).fetchone()
            return row[0] if row else 0
    
    # ==========================================
    # 知识图谱
    # ==========================================
    
    def add_entity(self, name: str, entity_type: str = "concept", maintainer_source: str = "memory_runtime", confidence: float = 1.0):
        """添加实体"""
        source = str(maintainer_source or "memory_runtime").strip() or "memory_runtime"
        bounded = _bounded_confidence(confidence)
        effective = _effective_confidence(bounded, source)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO entities (name, type, maintainer_source, confidence, effective_confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    type = excluded.type,
                    maintainer_source = excluded.maintainer_source,
                    confidence = excluded.confidence,
                    effective_confidence = excluded.effective_confidence,
                    updated_at = excluded.updated_at
                """,
                (name.lower(), entity_type, source, bounded, effective, _utc_now_iso())
            )
    
    def add_relation(self, subject: str, predicate: str, obj: str,
                     source_fact_id: Optional[str] = None, confidence: float = 1.0,
                     maintainer_source: str = "memory_runtime"):
        """
        添加关系三元组: (subject) -[predicate]-> (object)
        
        常用 predicate: USES, DEPENDS_ON, RELATED_TO, IS_A, HAS, PREFERS, WORKS_ON
        """
        subject_lower = subject.lower()
        obj_lower = obj.lower()
        source = str(maintainer_source or "memory_runtime").strip() or "memory_runtime"
        bounded = _bounded_confidence(confidence)
        effective = _effective_confidence(bounded, source)
        
        with self._conn() as conn:
            # 确保实体存在
            conn.execute("INSERT OR IGNORE INTO entities (name, type, maintainer_source, confidence, effective_confidence) VALUES (?, 'concept', ?, ?, ?)", (subject_lower, source, bounded, effective))
            conn.execute("INSERT OR IGNORE INTO entities (name, type, maintainer_source, confidence, effective_confidence) VALUES (?, 'concept', ?, ?, ?)", (obj_lower, source, bounded, effective))
            
            # 添加关系（忽略重复）
            conn.execute("""
                INSERT INTO relations (subject, predicate, object, source_fact_id, confidence, effective_confidence, maintainer_source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject, predicate, object) DO UPDATE SET
                    source_fact_id = COALESCE(excluded.source_fact_id, relations.source_fact_id),
                    confidence = excluded.confidence,
                    effective_confidence = excluded.effective_confidence,
                    maintainer_source = excluded.maintainer_source,
                    updated_at = excluded.updated_at
            """, (subject_lower, predicate.upper(), obj_lower, source_fact_id, bounded, effective, source, _utc_now_iso()))

    def add_scoped_relation(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        scope: str,
        source_fact_ids: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        confidence: float = 1.0,
        maintainer_source: str = "memory_runtime",
    ) -> str:
        """Add an injectable graph edge with fact evidence or an independent evidence ref."""
        normalized_scope = str(scope or "").strip()
        evidence_ids = sorted({str(item or "").strip() for item in list(source_fact_ids or []) if str(item or "").strip()})
        normalized_evidence_refs = _normalize_evidence_refs(evidence_refs)
        if not normalized_scope or (not evidence_ids and not normalized_evidence_refs):
            raise ValueError("scoped relation requires scope and fact evidence or an independent evidence ref")
        subject_lower = str(subject or "").strip().lower()
        object_lower = str(obj or "").strip().lower()
        predicate_upper = str(predicate or "").strip().upper()
        if not subject_lower or not object_lower or not predicate_upper:
            raise ValueError("subject, predicate and object are required")
        source = str(maintainer_source or "memory_runtime").strip() or "memory_runtime"
        bounded = _bounded_confidence(confidence)
        effective = _effective_confidence(bounded, source)
        relation_id = "rel-" + hashlib.sha256(
            f"{normalized_scope}:{subject_lower}:{predicate_upper}:{object_lower}".encode("utf-8")
        ).hexdigest()[:20]
        now = _utc_now_iso()
        with self._conn() as conn:
            evidence = []
            if evidence_ids:
                placeholders = ",".join("?" for _ in evidence_ids)
                evidence = conn.execute(
                    f"""
                    SELECT id, scope, status, lifecycle_state
                    FROM knowledge
                    WHERE id IN ({placeholders})
                    """,
                    evidence_ids,
                ).fetchall()
            valid_ids = {
                str(row["id"])
                for row in evidence
                if str(row["scope"] or "global") == normalized_scope
                and str(row["status"] or "active") == "active"
                and str(row["lifecycle_state"] or "active").strip().lower()
                not in {"stale", "tombstoned", "superseded", "quarantined"}
            }
            if evidence_ids and not valid_ids and not normalized_evidence_refs:
                raise ValueError("relation evidence is missing, inactive, or outside the requested scope")
            conn.execute(
                "INSERT OR IGNORE INTO entities (name, type, maintainer_source, confidence, effective_confidence) VALUES (?, 'concept', ?, ?, ?)",
                (subject_lower, source, bounded, effective),
            )
            conn.execute(
                "INSERT OR IGNORE INTO entities (name, type, maintainer_source, confidence, effective_confidence) VALUES (?, 'concept', ?, ?, ?)",
                (object_lower, source, bounded, effective),
            )
            conn.execute(
                """
                INSERT INTO scoped_relations (
                    id, subject, predicate, object, scope, status, confidence,
                    effective_confidence, maintainer_source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                ON CONFLICT(subject, predicate, object, scope) DO UPDATE SET
                    status = 'active', confidence = excluded.confidence,
                    effective_confidence = excluded.effective_confidence,
                    maintainer_source = excluded.maintainer_source,
                    updated_at = excluded.updated_at
                """,
                (
                    relation_id,
                    subject_lower,
                    predicate_upper,
                    object_lower,
                    normalized_scope,
                    bounded,
                    effective,
                    source,
                    now,
                    now,
                ),
            )
            actual = conn.execute(
                "SELECT id FROM scoped_relations WHERE subject = ? AND predicate = ? AND object = ? AND scope = ?",
                (subject_lower, predicate_upper, object_lower, normalized_scope),
            ).fetchone()
            relation_id = str(actual["id"])
            for fact_id in valid_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO scoped_relation_evidence (relation_id, fact_id, created_at) VALUES (?, ?, ?)",
                    (relation_id, fact_id, now),
                )
            for evidence_ref in normalized_evidence_refs:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO scoped_relation_evidence_refs (
                        relation_id, evidence_ref, evidence_kind, created_at
                    ) VALUES (?, ?, 'external', ?)
                    """,
                    (relation_id, evidence_ref, now),
                )
        return relation_id
    
    def query_entity(self, entity: str, scopes: Optional[List[str]] = None) -> List[Dict]:
        """Query evidence-backed relations inside an explicit scope chain."""
        entity_lower = entity.lower()
        requested_scopes = [str(item).strip() for item in list(scopes or []) if str(item).strip()]
        all_scopes = "*" in requested_scopes
        scope_values = [item for item in requested_scopes if item != "*"]
        if not requested_scopes:
            scope_values = ["global"]
        scope_clause = ""
        scope_params: List[object] = []
        if not all_scopes:
            placeholders = ",".join("?" for _ in scope_values)
            scope_clause = f" AND scope IN ({placeholders})"
            scope_params.extend(scope_values)
        with self._conn() as conn:
            outgoing = conn.execute(f"""
                SELECT subject, predicate, object, scope, confidence, effective_confidence, maintainer_source
                FROM scoped_relations relation
                WHERE subject = ?{scope_clause} AND status = 'active'
                  AND (EXISTS (
                    SELECT 1 FROM scoped_relation_evidence evidence
                    JOIN knowledge fact ON fact.id = evidence.fact_id
                    WHERE evidence.relation_id = relation.id
                      AND fact.status = 'active'
                      AND COALESCE(fact.lifecycle_state, 'active') = 'active'
                  ) OR EXISTS (
                    SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                    WHERE evidence_ref.relation_id = relation.id
                  ))
                ORDER BY effective_confidence DESC, confidence DESC
            """, (entity_lower, *scope_params)).fetchall()
            incoming = conn.execute(f"""
                SELECT subject, predicate, object, scope, confidence, effective_confidence, maintainer_source
                FROM scoped_relations relation
                WHERE object = ?{scope_clause} AND status = 'active'
                  AND (EXISTS (
                    SELECT 1 FROM scoped_relation_evidence evidence
                    JOIN knowledge fact ON fact.id = evidence.fact_id
                    WHERE evidence.relation_id = relation.id
                      AND fact.status = 'active'
                      AND COALESCE(fact.lifecycle_state, 'active') = 'active'
                  ) OR EXISTS (
                    SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                    WHERE evidence_ref.relation_id = relation.id
                  ))
                ORDER BY effective_confidence DESC, confidence DESC
            """, (entity_lower, *scope_params)).fetchall()
            
            results = []
            for r in outgoing:
                results.append({"direction": "out", "subject": r[0], "predicate": r[1], "object": r[2], "scope": r[3], "confidence": r[4], "effectiveConfidence": r[5], "maintainerSource": r[6]})
            for r in incoming:
                results.append({"direction": "in", "subject": r[0], "predicate": r[1], "object": r[2], "scope": r[3], "confidence": r[4], "effectiveConfidence": r[5], "maintainerSource": r[6]})
            
            return results

    def delete_entity(self, name: str, *, scope: Optional[str] = None) -> bool:
        """Archive evidence-backed relations without destroying shared entities."""
        name_lower = name.lower()
        with self._conn() as conn:
            if not conn.execute("SELECT 1 FROM entities WHERE name = ?", (name_lower,)).fetchone():
                return False
            params: List[object] = [_utc_now_iso(), name_lower, name_lower]
            scope_clause = ""
            if scope:
                scope_clause = " AND scope = ?"
                params.append(str(scope).strip())
            cursor = conn.execute(
                f"""
                UPDATE scoped_relations
                SET status = 'archived', updated_at = ?
                WHERE status = 'active' AND (subject = ? OR object = ?){scope_clause}
                """,
                params,
            )
            return cursor.rowcount > 0

    def delete_relation(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        scope: Optional[str] = None,
    ) -> bool:
        """Archive a scoped relation while preserving its evidence ledger."""
        subject_lower = subject.lower()
        object_lower = obj.lower()
        predicate_upper = predicate.upper()
        with self._conn() as conn:
            params: List[object] = [
                _utc_now_iso(),
                subject_lower,
                predicate_upper,
                object_lower,
            ]
            scope_clause = ""
            if scope:
                scope_clause = " AND scope = ?"
                params.append(str(scope).strip())
            cursor = conn.execute(
                f"""
                UPDATE scoped_relations
                SET status = 'archived', updated_at = ?
                WHERE status = 'active'
                  AND subject = ? AND predicate = ? AND object = ?{scope_clause}
                """,
                params,
            )
            return cursor.rowcount > 0

    def get_isolated_entities(self, limit: int = 50) -> List[Dict]:
        """获取没有关联任何关系的孤立实体"""
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT e.name, e.type
                FROM entities e
                WHERE e.name NOT IN (SELECT subject FROM scoped_relations WHERE status = 'active')
                  AND e.name NOT IN (SELECT object FROM scoped_relations WHERE status = 'active')
                LIMIT ?
            """, (limit,))
            return [{"name": row[0], "type": row[1]} for row in cursor.fetchall()]

    def search_entities(
        self,
        keyword: str,
        limit: int = 20,
        scopes: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Search only entities backed by relations visible to the scope set."""
        keyword_lower = keyword.lower()
        active_sql, scope_params = self._active_graph_relations_sql(
            scopes=scopes if scopes is not None else ["global"]
        )
        with self._conn() as conn:
            cursor = conn.execute(f"""
                WITH active_relations AS ({active_sql}), visible_entities AS (
                    SELECT subject AS name FROM active_relations
                    UNION
                    SELECT object AS name FROM active_relations
                )
                SELECT entity.name, entity.type, entity.maintainer_source,
                       entity.confidence, entity.effective_confidence
                FROM entities entity
                JOIN visible_entities visible ON visible.name = entity.name
                WHERE LOWER(entity.name) LIKE ?
                ORDER BY entity.effective_confidence DESC, entity.name ASC
                LIMIT ?
            """, (*scope_params, f"%{keyword_lower}%", limit))
            return [
                {
                    "name": row[0],
                    "type": row[1],
                    "maintainerSource": row[2],
                    "confidence": row[3],
                    "effectiveConfidence": row[4],
                }
                for row in cursor.fetchall()
            ]
    
    def multi_hop_query(self, start: str, hops: int = 2, scopes: Optional[List[str]] = None) -> List[Dict]:
        """
        多跳查询：从起始实体出发，沿关系路径搜索 N 跳。
        
        例: multi_hop_query("python", hops=2) 
        → python → USES → fastapi → DEPENDS_ON → pydantic
        """
        start_lower = start.lower()
        visited = set()
        results = []
        current_level = [start_lower]
        scope_values = [str(item).strip() for item in list(scopes or ["global"]) if str(item).strip()] or ["global"]
        placeholders = ",".join("?" for _ in scope_values)
        
        with self._conn() as conn:
            for hop in range(hops):
                next_level = []
                for entity in current_level:
                    if entity in visited:
                        continue
                    visited.add(entity)
                    
                    rows = conn.execute(f"""
                        SELECT subject, predicate, object, scope
                        FROM scoped_relations relation
                        WHERE (subject = ? OR object = ?)
                          AND scope IN ({placeholders}) AND status = 'active'
                          AND (EXISTS (
                            SELECT 1 FROM scoped_relation_evidence evidence
                            JOIN knowledge fact ON fact.id = evidence.fact_id
                            WHERE evidence.relation_id = relation.id
                              AND fact.status = 'active'
                              AND COALESCE(fact.lifecycle_state, 'active') = 'active'
                          ) OR EXISTS (
                            SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                            WHERE evidence_ref.relation_id = relation.id
                          ))
                    """, (entity, entity, *scope_values)).fetchall()
                    
                    for r in rows:
                        results.append({
                            "hop": hop + 1,
                            "subject": r[0],
                            "predicate": r[1],
                            "object": r[2],
                            "scope": r[3],
                        })
                        # 追踪下一跳目标
                        neighbor = r[2] if r[0] == entity else r[0]
                        if neighbor not in visited:
                            next_level.append(neighbor)
                
                current_level = next_level
                if not current_level:
                    break
        
        return results
    
    @staticmethod
    def _active_graph_relations_sql(
        *,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> tuple[str, List[object]]:
        requested_scopes = list(dict.fromkeys(
            str(item or "").strip()
            for item in list(scopes or [])
            if str(item or "").strip()
        ))
        if scopes is None:
            requested_scope = str(scope or "").strip()
            requested_scopes = [] if requested_scope in {"", "*"} else [requested_scope]
        if "*" in requested_scopes:
            requested_scopes = []

        # Workspace isolation is expressed as a set of internal scope aliases.
        # The global layer is shared by every workspace and is therefore always
        # included in a workspace-scoped graph query.
        if requested_scopes and "global" not in requested_scopes:
            requested_scopes.append("global")
        placeholders = ",".join("?" for _ in requested_scopes)
        scope_clause = f" AND relation.scope IN ({placeholders})" if requested_scopes else ""
        return (
            f"""
            SELECT relation.*
            FROM scoped_relations relation
            WHERE relation.status = 'active'
              {scope_clause}
              AND (EXISTS (
                SELECT 1 FROM scoped_relation_evidence evidence
                JOIN knowledge fact ON fact.id = evidence.fact_id
                WHERE evidence.relation_id = relation.id
                  AND fact.status = 'active'
                  AND COALESCE(fact.lifecycle_state, 'active') = 'active'
              ) OR EXISTS (
                SELECT 1 FROM scoped_relation_evidence_refs evidence_ref
                WHERE evidence_ref.relation_id = relation.id
              ))
            """,
            list(requested_scopes),
        )

    def get_graph_stats(
        self,
        *,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict:
        """Return evidence-backed graph health for an aggregate or workspace scope set."""
        active_sql, scope_params = self._active_graph_relations_sql(scope=scope, scopes=scopes)
        with self._conn() as conn:
            registered_entities_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            entities_count = conn.execute(
                f"""
                WITH active_relations AS ({active_sql}), active_entities AS (
                    SELECT subject AS name FROM active_relations
                    UNION
                    SELECT object AS name FROM active_relations
                )
                SELECT COUNT(*) FROM active_entities
                """,
                scope_params,
            ).fetchone()[0]
            relations_count = conn.execute(
                f"WITH active_relations AS ({active_sql}) SELECT COUNT(*) FROM active_relations",
                scope_params,
            ).fetchone()[0]
            legacy_relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            external_evidence_relations = conn.execute(
                f"""
                WITH active_relations AS ({active_sql})
                SELECT COUNT(DISTINCT relation.id)
                FROM active_relations relation
                JOIN scoped_relation_evidence_refs evidence_ref ON evidence_ref.relation_id = relation.id
                """,
                scope_params,
            ).fetchone()[0]
            top_entities = conn.execute(
                f"""
                WITH active_relations AS ({active_sql}), endpoints AS (
                    SELECT subject AS name FROM active_relations
                    UNION ALL
                    SELECT object AS name FROM active_relations
                )
                SELECT entity.name, entity.type, COUNT(endpoints.name) AS degree
                FROM entities entity
                JOIN endpoints ON endpoints.name = entity.name
                GROUP BY entity.name, entity.type
                ORDER BY degree DESC, entity.name ASC
                LIMIT 10
                """,
                scope_params,
            ).fetchall()
            has_scope_filter = bool(str(scope or "").strip() or scopes is not None)
            scoped_registered = int(entities_count) if has_scope_filter else int(registered_entities_count)
            return {
                "scope": str(scope or "").strip() or None,
                "scopes": list(scope_params) if scopes is not None else None,
                "entities": int(entities_count),
                "registeredEntities": scoped_registered,
                "isolatedEntities": max(scoped_registered - int(entities_count), 0),
                "relations": int(relations_count),
                "legacyArchivedRelations": int(legacy_relations),
                "sourceCoverage": 1.0,
                "externalEvidenceRelations": int(external_evidence_relations),
                "top_entities": [{"name": row[0], "type": row[1], "degree": row[2]} for row in top_entities],
            }

    def list_graph_scopes(self) -> List[Dict[str, object]]:
        """List internal scope sources that own active knowledge or graph relations."""
        active_sql, params = self._active_graph_relations_sql()
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                WITH active_relations AS ({active_sql}), available_scopes AS (
                    SELECT scope FROM active_relations
                    UNION
                    SELECT scope
                    FROM knowledge
                    WHERE status = 'active'
                      AND COALESCE(lifecycle_state, 'active') = 'active'
                )
                SELECT available_scopes.scope, COUNT(active_relations.id) AS relation_count
                FROM available_scopes
                LEFT JOIN active_relations ON active_relations.scope = available_scopes.scope
                GROUP BY available_scopes.scope
                ORDER BY relation_count DESC, available_scopes.scope ASC
                """,
                params,
            ).fetchall()
            workspace_roots: Dict[str, set[str]] = {}
            metadata_rows = conn.execute(
                """
                SELECT scope, metadata_json
                FROM knowledge
                WHERE status = 'active'
                  AND COALESCE(lifecycle_state, 'active') = 'active'
                """
            ).fetchall()
            for metadata_row in metadata_rows:
                try:
                    metadata = json.loads(metadata_row["metadata_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                workspace_root = str(
                    metadata.get("workspaceRoot")
                    or metadata.get("workspacePath")
                    or metadata.get("workspace_path")
                    or ""
                ).strip()
                if workspace_root:
                    workspace_roots.setdefault(str(metadata_row["scope"] or "global"), set()).add(workspace_root)
        return [
            {
                "scope": str(row["scope"] or "global"),
                "relationCount": int(row["relation_count"] or 0),
                "workspaceRoots": sorted(workspace_roots.get(str(row["scope"] or "global"), set())),
            }
            for row in rows
        ]
    
    # === 实体类型 → 颜色映射（供前端使用） ===
    ENTITY_TYPE_COLORS = {
        "user": "#6366f1",       # indigo
        "technology": "#06b6d4", # cyan
        "project": "#f59e0b",    # amber
        "concept": "#8b5cf6",    # violet
        "framework": "#10b981",  # emerald
        "language": "#ef4444",   # red
        "tool": "#ec4899",       # pink
        "platform": "#f97316",   # orange
    }
    
    def get_full_graph(
        self,
        limit: int = 100,
        *,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict:
        """Return force-graph data for one workspace scope set, or all scopes when omitted."""
        graph_stats = self.get_graph_stats(scope=scope, scopes=scopes)
        bounded_limit = max(1, min(int(limit or 100), 1000))
        active_sql, scope_params = self._active_graph_relations_sql(scope=scope, scopes=scopes)
        with self._conn() as conn:
            entities = conn.execute(
                f"""
                WITH active_relations AS ({active_sql}), endpoints AS (
                    SELECT subject AS name FROM active_relations
                    UNION ALL
                    SELECT object AS name FROM active_relations
                ), degrees AS (
                    SELECT name, COUNT(*) AS degree FROM endpoints GROUP BY name
                )
                SELECT entity.name, entity.type, entity.maintainer_source,
                       entity.confidence, entity.effective_confidence, degrees.degree
                FROM entities entity
                JOIN degrees ON degrees.name = entity.name
                ORDER BY entity.effective_confidence DESC, degrees.degree DESC, entity.name ASC
                LIMIT ?
                """,
                [*scope_params, bounded_limit],
            ).fetchall()
            if not entities:
                return {
                    "nodes": [],
                    "links": [],
                    "meta": {
                        "scope": str(scope or "").strip() or None,
                        "scopes": list(scope_params) if scopes is not None else None,
                        "totalEntities": int(graph_stats.get("entities") or 0),
                        "totalRelations": int(graph_stats.get("relations") or 0),
                        "renderedEntities": 0,
                        "renderedRelations": 0,
                        "limit": bounded_limit,
                        "truncated": False,
                    },
                }

            entity_names = sorted({str(row[0]) for row in entities})
            nodes = [
                {
                    "id": name,
                    "label": name.title(),
                    "type": entity_type,
                    "color": self.ENTITY_TYPE_COLORS.get(entity_type, "#94a3b8"),
                    "val": max(int(degree or 0), 1),
                    "maintainerSource": maintainer_source,
                    "confidence": confidence,
                    "effectiveConfidence": effective_confidence,
                }
                for name, entity_type, maintainer_source, confidence, effective_confidence, degree in entities
            ]
            placeholders = ",".join("?" for _ in entity_names)
            relations = conn.execute(
                f"""
                WITH active_relations AS ({active_sql})
                SELECT subject, predicate, object, scope, confidence,
                       effective_confidence, maintainer_source
                FROM active_relations
                WHERE subject IN ({placeholders}) AND object IN ({placeholders})
                ORDER BY effective_confidence DESC, subject ASC, predicate ASC, object ASC
                """,
                [*scope_params, *entity_names, *entity_names],
            ).fetchall()
            links = [
                {
                    "source": subject,
                    "target": object_name,
                    "label": predicate,
                    "scope": relation_scope,
                    "confidence": confidence,
                    "effectiveConfidence": effective_confidence,
                    "maintainerSource": maintainer_source,
                }
                for subject, predicate, object_name, relation_scope, confidence, effective_confidence, maintainer_source in relations
            ]
            return {
                "nodes": nodes,
                "links": links,
                "meta": {
                    "scope": str(scope or "").strip() or None,
                    "scopes": list(scope_params) if scopes is not None else None,
                    "totalEntities": int(graph_stats.get("entities") or 0),
                    "totalRelations": int(graph_stats.get("relations") or 0),
                    "renderedEntities": len(nodes),
                    "renderedRelations": len(links),
                    "limit": bounded_limit,
                    "truncated": len(nodes) < int(graph_stats.get("entities") or 0),
                },
            }
    
    def hard_delete_knowledge(self, fact_id: str, *, confirm: bool = False) -> bool:
        """
        Advanced physical purge. Ordinary deletion must use tombstones.
        """
        if not confirm:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT rowid, scope, status, lifecycle_state FROM knowledge WHERE id = ?", (fact_id,)
            ).fetchone()
            if not row:
                return False
            lifecycle = str(row["lifecycle_state"] or "active").strip().lower()
            if str(row["status"] or "active") == "active" and lifecycle == "active":
                return False
            if conn.execute(
                "SELECT 1 FROM knowledge WHERE superseded_by = ? LIMIT 1",
                (fact_id,),
            ).fetchone():
                return False
            if conn.execute(
                "SELECT 1 FROM knowledge_resolution_candidates WHERE target_fact_id = ? AND state = 'pending' LIMIT 1",
                (fact_id,),
            ).fetchone():
                return False
            relation_ids = [
                str(item["relation_id"])
                for item in conn.execute(
                    "SELECT relation_id FROM scoped_relation_evidence WHERE fact_id = ?",
                    (fact_id,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (int(row["rowid"]),))
            conn.execute("DELETE FROM knowledge_projection_outbox WHERE fact_id = ?", (fact_id,))
            conn.execute("DELETE FROM knowledge_resolution_candidates WHERE candidate_fact_id = ?", (fact_id,))
            conn.execute("DELETE FROM knowledge_migration_events WHERE fact_id = ?", (fact_id,))
            conn.execute("DELETE FROM scoped_relation_evidence WHERE fact_id = ?", (fact_id,))
            conn.execute("DELETE FROM knowledge_observations WHERE fact_id = ?", (fact_id,))
            conn.execute("DELETE FROM knowledge WHERE id = ?", (fact_id,))
            self._enqueue_projection(
                conn,
                fact_id=fact_id,
                operation="remove",
                payload={"scope": str(row["scope"] or "global")},
            )
            for relation_id in relation_ids:
                supported = conn.execute(
                    """
                    SELECT 1 WHERE EXISTS (
                        SELECT 1 FROM scoped_relation_evidence WHERE relation_id = ?
                    ) OR EXISTS (
                        SELECT 1 FROM scoped_relation_evidence_refs WHERE relation_id = ?
                    ) LIMIT 1
                    """,
                    (relation_id, relation_id),
                ).fetchone()
                if not supported:
                    conn.execute(
                        "UPDATE scoped_relations SET status = 'archived', updated_at = ? WHERE id = ?",
                        (_utc_now_iso(), relation_id),
                    )
            return True
    
    def update_knowledge(self, fact_id: str, new_fact: str,
                         category: str = None, scope: str = None,
                         maintainer_source: str | None = None,
                         confidence: float | None = None,
                         agents_hash: str | None = None,
                         repo_signature: str | None = None,
                         signature_policy: str = "soft_v1") -> bool:
        """Deprecated compatibility entry: create a replacement revision."""
        self.record_deprecated_usage("KnowledgeDB.update_knowledge", context=fact_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT fact, category, scope, confidence, importance, durability, maintainer_source FROM knowledge WHERE id = ?",
                (fact_id,),
            ).fetchone()
        if not row:
            return False
        result = self.write_knowledge(
            fact=new_fact,
            category=category or str(row["category"] or "general"),
            scope=scope or str(row["scope"] or "global"),
            relation="replace",
            target_fact_id=fact_id,
            maintainer_source=maintainer_source or str(row["maintainer_source"] or "memory_runtime"),
            confidence=confidence if confidence is not None else float(row["confidence"] or 1.0),
            importance=int(row["importance"] or 50),
            durability=str(row["durability"] or "operational"),
            agents_hash=agents_hash,
            repo_signature=repo_signature,
            signature_policy=signature_policy,
            metadata={"deprecatedOverwriteId": fact_id},
        )
        return bool(result.get("factId"))
    
    # ==========================================
    # 增量索引
    # ==========================================
    
    def needs_reindex(self, file_path: Path) -> bool:
        """检查文件是否需要重新索引（基于 mtime 对比）"""
        if not file_path.exists():
            return False
        
        current_mtime = file_path.stat().st_mtime
        
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mtime FROM file_index WHERE path = ?",
                (str(file_path),)
            ).fetchone()
            
            if not row:
                return True  # 新文件
            return row[0] != current_mtime  # mtime 不同 → 需要重新索引
    
    def mark_indexed(self, file_path: Path, chunk_count: int = 0):
        """标记文件已索引"""
        content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        content_hash = hashlib.md5(content.encode()).hexdigest()
        
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO file_index (path, mtime, content_hash, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                str(file_path),
                file_path.stat().st_mtime if file_path.exists() else 0,
                content_hash,
                chunk_count,
                _utc_now_iso()
            ))
    
    def get_stale_files(self, directory: Path, pattern: str = "*.json") -> List[Path]:
        """扫描目录，返回需要重新索引的文件列表"""
        stale = []
        for file_path in directory.rglob(pattern):
            if self.needs_reindex(file_path):
                stale.append(file_path)
        return stale
    
    def run_incremental_index(self):
        """Deprecated no-op: items.json is a one-way derived projection.

        Missing legacy JSON IDs are imported only by the explicit, backup-aware
        P0 migration. Runtime indexing must never feed projection data back into
        canonical SQLite.
        """
        self.record_deprecated_usage("KnowledgeDB.run_incremental_index", context="derived_projection_noop")
        return 0

    # ==========================================
    # 执行日志 (Execution Logs)
    # ==========================================
    
    def log_execution(self, log_id: str, task_name: str, action_type: str, action_target: str, 
                      trigger_source: str, status: str, payload: Optional[Dict] = None, 
                      error_message: Optional[str] = None, duration_ms: Optional[int] = None):
        """记录一条执行日志 (插入或更新)"""
        from core.observability_db import observability_db

        next_payload = dict(payload or {})
        if error_message:
            next_payload["errorMessage"] = error_message
        if duration_ms is not None:
            next_payload["durationMs"] = duration_ms
        observability_db.log_execution(
            log_id=log_id,
            task_name=task_name,
            action_type=action_type,
            action_target=action_target,
            trigger_source=trigger_source,
            status=status,
            payload=next_payload,
        )
                
    def get_execution_logs(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """获取执行日志列表，按时间倒序"""
        from core.observability_db import observability_db

        return observability_db.get_execution_logs(limit=limit, offset=offset)

# === 全局单例 ===
knowledge_db = KnowledgeDB()
