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
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone
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
            }.items():
                if column_name not in knowledge_columns:
                    conn.execute(f"ALTER TABLE knowledge ADD COLUMN {column_sql}")
            
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_predicate ON relations(predicate)")
    
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
                           k.metadata_json,
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
                           k.metadata_json,
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
        """查询所有激活的知识条目"""
        with self._conn() as conn:
            if scope:
                rows = conn.execute("""
                    SELECT id, fact, category, scope, status, source_session, updated_at,
                           lifecycle_state, last_seen_at, last_injected_at, last_verified_at,
                           evidence_refs_json, promotion_reason, superseded_by, tombstone_of,
                           decay_score, agents_hash, repo_signature, signature_policy,
                           maintainer_source, confidence, effective_confidence, metadata_json
                    FROM knowledge
                    WHERE scope IN (?, 'global') AND status = ?
                    ORDER BY effective_confidence DESC, updated_at DESC
                    LIMIT ?
                """, (scope, status, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, fact, category, scope, status, source_session, updated_at,
                           lifecycle_state, last_seen_at, last_injected_at, last_verified_at,
                           evidence_refs_json, promotion_reason, superseded_by, tombstone_of,
                           decay_score, agents_hash, repo_signature, signature_policy,
                           maintainer_source, confidence, effective_confidence, metadata_json
                    FROM knowledge
                    WHERE status = ?
                    ORDER BY effective_confidence DESC, updated_at DESC
                    LIMIT ?
                """, (status, limit)).fetchall()
            
            return [dict(r) for r in rows]
    
    # ==========================================
    # 知识 CRUD
    # ==========================================
    
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
        fact_tokenized = tokenize_for_fts(fact)
        now = _utc_now_iso()
        normalized_lifecycle = str(lifecycle_state or "active").strip().lower() or "active"
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
                # 更新：先删 FTS 旧记录，再更新主表，最后插 FTS 新记录
                old_rowid = existing[0]
                conn.execute(
                    "DELETE FROM knowledge_fts WHERE rowid = ?", (old_rowid,)
                )
                conn.execute("""
                    UPDATE knowledge SET fact=?, category=?, scope=?, status='active',
                           source_session=?, parent_id=?, lifecycle_state=?, last_seen_at=?,
                           agents_hash=?, repo_signature=?, signature_policy=?,
                           maintainer_source=?, confidence=?, effective_confidence=?,
                           evidence_refs_json=?, promotion_reason=?, metadata_json=?, updated_at=?
                    WHERE id=?
                """, (
                    fact, category, scope, source_session, parent_id, normalized_lifecycle, now,
                    agents_hash, repo_signature, signature_policy, normalized_source, bounded_confidence,
                    effective, evidence_refs_json, promotion_reason, metadata_json, now, fact_id,
                ))
                conn.execute("""
                    INSERT INTO knowledge_fts(rowid, fact_tokenized, category, scope)
                    VALUES (?, ?, ?, ?)
                """, (old_rowid, fact_tokenized, category, scope))
            else:
                # 新增
                conn.execute("""
                    INSERT INTO knowledge (
                        id, fact, category, scope, status, source_session, parent_id,
                        lifecycle_state, last_seen_at, agents_hash, repo_signature,
                        signature_policy, maintainer_source, confidence, effective_confidence,
                        evidence_refs_json, promotion_reason, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    fact_id, fact, category, scope, source_session, parent_id,
                    normalized_lifecycle, now, agents_hash, repo_signature,
                    signature_policy, normalized_source, bounded_confidence, effective,
                    evidence_refs_json, promotion_reason, metadata_json, now,
                ))
                
                # 获取新 rowid 同步到 FTS5
                new_rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                # Historical crashes or schema migrations may leave orphan FTS rows.
                # Clear the rowid before insert so incremental re-index stays idempotent.
                conn.execute(
                    "DELETE FROM knowledge_fts WHERE rowid = ?", (new_rowid,)
                )
                conn.execute("""
                    INSERT INTO knowledge_fts(rowid, fact_tokenized, category, scope)
                    VALUES (?, ?, ?, ?)
                """, (new_rowid, fact_tokenized, category, scope))
    
    def get_user_documents(self) -> List[Dict]:
        """获取所有上传的用户文档列表。利用 source_session 存储 filename"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT source_session as filename,
                       COUNT(id) as chunk_count,
                       MAX(created_at) as uploaded_at
                FROM knowledge
                WHERE category = 'user_document' AND source_session IS NOT NULL
                GROUP BY source_session
                ORDER BY uploaded_at DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def delete_user_document(self, filename: str) -> List[str]:
        """删除指定用户文档的所有切块（包括 FTS5 索引）。返回被删除事实的 IDs。"""
        with self._conn() as conn:
            # 找到要删除的记录 ID
            rows = conn.execute(
                "SELECT rowid, id FROM knowledge WHERE category = 'user_document' AND source_session = ?", 
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
            
            # 删除主表记录
            conn.execute(
                "DELETE FROM knowledge WHERE category = 'user_document' AND source_session = ?", 
                (filename,)
            )
            
            return fact_ids
    
    def delete_knowledge(self, fact_id: str) -> bool:
        """软删除知识条目"""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE knowledge SET status = 'deleted', lifecycle_state = 'tombstoned', updated_at = ? WHERE id = ? AND status = 'active'",
                (_utc_now_iso(), fact_id)
            )
            return cursor.rowcount > 0

    def set_knowledge_status(self, fact_id: str, status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"active", "deleted", "quarantined"}:
            return False
        with self._conn() as conn:
            lifecycle_state = "active" if normalized_status == "active" else ("tombstoned" if normalized_status == "deleted" else "quarantined")
            cursor = conn.execute(
                "UPDATE knowledge SET status = ?, lifecycle_state = ?, updated_at = ? WHERE id = ?",
                (normalized_status, lifecycle_state, _utc_now_iso(), fact_id),
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
                    metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded')
                """,
                (superseded_by, json.dumps(metadata, ensure_ascii=False), now, fact_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE relations SET source_fact_id = ?, updated_at = ? WHERE source_fact_id = ?",
                    (superseded_by, now, fact_id),
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
            maintenance["mergeSuggestion"] = {
                "targetId": target_id,
                "similarity": round(float(similarity or 0.0), 4),
                "reason": reason,
                "updatedAt": now,
            }
            metadata["maintenance"] = maintenance
            cursor = conn.execute(
                """
                UPDATE knowledge
                SET metadata_json = ?, updated_at = ?
                WHERE id = ?
                  AND status = 'active'
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('tombstoned', 'superseded')
                """,
                (json.dumps(metadata, ensure_ascii=False), now, fact_id),
            )
            return cursor.rowcount > 0

    def maintenance_compact_knowledge(
        self,
        *,
        limit: int = 500,
        auto_supersede_threshold: float = 0.985,
        max_clusters: int = 80,
    ) -> Dict[str, object]:
        """Conservatively dedupe highly similar same-scope facts.

        This is deterministic and intentionally narrow: it only acts inside the
        same scope and category, and prefers lifecycle superseding over deletion.
        """
        effective_limit = max(1, min(int(limit or 500), 2000))
        threshold = max(0.95, min(float(auto_supersede_threshold or 0.985), 1.0))
        cluster_budget = max(1, min(int(max_clusters or 80), 500))
        items = [
            item
            for item in self.get_all_knowledge(scope=None, limit=effective_limit, status="active")
            if str(item.get("lifecycle_state") or "active").strip().lower() not in {"stale", "tombstoned", "superseded"}
        ]
        buckets: Dict[tuple[str, str], List[Dict]] = {}
        for item in items:
            fact = _normalize_fact_for_compaction(item.get("fact"))
            if len(fact) < 16:
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

        def _keeper_key(item: Dict) -> tuple[float, str, str]:
            try:
                confidence = float(item.get("effective_confidence") or item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            return (confidence, str(item.get("updated_at") or ""), str(item.get("id") or ""))

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
                keeper = sorted(exact_group, key=_keeper_key, reverse=True)[0]
                for duplicate in exact_group:
                    if duplicate.get("id") == keeper.get("id"):
                        continue
                    duplicate_candidates += 1
                    if self.mark_knowledge_superseded(str(duplicate.get("id")), str(keeper.get("id")), reason="maintenance_exact_duplicate"):
                        superseded_count += 1
                        superseded_pairs.append({"sourceId": str(duplicate.get("id")), "targetId": str(keeper.get("id")), "reason": "exact_duplicate"})
            if processed_clusters >= cluster_budget:
                break

            candidates = sorted(
                [item for group in by_exact.values() if len(group) == 1 for item in group],
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
                    keeper, duplicate = sorted([left, right], key=_keeper_key, reverse=True)[:2]
                    if ratio >= threshold:
                        if self.mark_knowledge_superseded(str(duplicate.get("id")), str(keeper.get("id")), reason="maintenance_high_similarity"):
                            superseded_count += 1
                            superseded_pairs.append(
                                {
                                    "sourceId": str(duplicate.get("id")),
                                    "targetId": str(keeper.get("id")),
                                    "reason": "high_similarity",
                                }
                            )
                    else:
                        if self.mark_knowledge_merge_suggestion(
                            str(duplicate.get("id")),
                            str(keeper.get("id")),
                            similarity=ratio,
                        ):
                            merge_suggestion_count += 1
                            merge_suggestions.append(
                                {
                                    "sourceId": str(duplicate.get("id")),
                                    "targetId": str(keeper.get("id")),
                                    "similarity": round(ratio, 4),
                                    "reason": "similar_but_below_auto_threshold",
                                }
                            )
                        if len(merge_suggestions) >= 20:
                            break

        graph_result = self.maintenance_compact_graph()
        return {
            "candidateCount": duplicate_candidates,
            "supersededCount": superseded_count,
            "mergeSuggestionCount": merge_suggestion_count,
            "supersededPairs": superseded_pairs[:20],
            "mergeSuggestions": merge_suggestions[:20],
            "processedClusterCount": processed_clusters,
            "budgetStopped": processed_clusters >= cluster_budget,
            "graph": graph_result,
        }

    def maintenance_compact_graph(self, *, limit: int = 500) -> Dict[str, object]:
        """Return conservative graph health/compaction stats without destructive cleanup."""
        effective_limit = max(1, min(int(limit or 500), 2000))
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.source_fact_id, k.superseded_by, k.lifecycle_state
                FROM relations r
                LEFT JOIN knowledge k ON k.id = r.source_fact_id
                WHERE r.source_fact_id IS NOT NULL
                LIMIT ?
                """,
                (effective_limit,),
            ).fetchall()
            rewired = 0
            orphaned = 0
            now = _utc_now_iso()
            for row in rows:
                source_fact_id = str(row["source_fact_id"] or "").strip()
                superseded_by = str(row["superseded_by"] or "").strip() if "superseded_by" in row.keys() else ""
                lifecycle = str(row["lifecycle_state"] or "").strip().lower() if "lifecycle_state" in row.keys() else ""
                if source_fact_id and not lifecycle:
                    orphaned += 1
                    continue
                if lifecycle == "superseded" and superseded_by:
                    cursor = conn.execute(
                        "UPDATE relations SET source_fact_id = ?, updated_at = ? WHERE id = ?",
                        (superseded_by, now, row["id"]),
                    )
                    rewired += cursor.rowcount
            isolated_entities = conn.execute(
                """
                SELECT COUNT(*)
                FROM entities e
                WHERE e.name NOT IN (SELECT subject FROM relations)
                  AND e.name NOT IN (SELECT object FROM relations)
                """
            ).fetchone()[0]
        return {
            "relationCandidateCount": len(rows),
            "rewiredRelationCount": rewired,
            "orphanedRelationCount": orphaned,
            "isolatedEntityCount": int(isolated_entities or 0),
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
            cursor = conn.execute(
                f"""
                UPDATE knowledge
                SET lifecycle_state = 'stale', updated_at = ?
                WHERE status = 'active'
                  AND scope IN ({placeholders})
                  AND COALESCE(lifecycle_state, 'active') NOT IN ('stale', 'tombstoned', 'superseded')
                  AND ({' OR '.join(signature_conditions)})
                """,
                [now, *scope_values, *signature_params],
            )
            return cursor.rowcount

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
            row = conn.execute("SELECT confidence FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
            if not row:
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
            return cursor.rowcount > 0
    
    def get_knowledge_count(self) -> int:
        """获取活跃知识条目数"""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM knowledge WHERE status = 'active'").fetchone()
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
    
    def query_entity(self, entity: str) -> List[Dict]:
        """查询实体的所有关系（出边 + 入边）"""
        entity_lower = entity.lower()
        with self._conn() as conn:
            # 出边: entity → ?
            outgoing = conn.execute("""
                SELECT subject, predicate, object, confidence, effective_confidence, maintainer_source
                FROM relations
                WHERE subject = ?
                ORDER BY effective_confidence DESC, confidence DESC
            """, (entity_lower,)).fetchall()
            
            # 入边: ? → entity
            incoming = conn.execute("""
                SELECT subject, predicate, object, confidence, effective_confidence, maintainer_source
                FROM relations
                WHERE object = ?
                ORDER BY effective_confidence DESC, confidence DESC
            """, (entity_lower,)).fetchall()
            
            results = []
            for r in outgoing:
                results.append({"direction": "out", "subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3], "effectiveConfidence": r[4], "maintainerSource": r[5]})
            for r in incoming:
                results.append({"direction": "in", "subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3], "effectiveConfidence": r[4], "maintainerSource": r[5]})
            
            return results

    def delete_entity(self, name: str) -> bool:
        """删除实体及其所有关联的三元组关系"""
        name_lower = name.lower()
        with self._conn() as conn:
            # 检查实体是否存在
            cursor = conn.execute("SELECT name FROM entities WHERE name = ?", (name_lower,))
            if not cursor.fetchone():
                return False
            # 删除相关的关系
            conn.execute("DELETE FROM relations WHERE subject = ? OR object = ?", (name_lower, name_lower))
            # 删除实体
            conn.execute("DELETE FROM entities WHERE name = ?", (name_lower,))
            return True

    def delete_relation(self, subject: str, predicate: str, obj: str) -> bool:
        """删除指定关系三元组。"""
        subject_lower = subject.lower()
        object_lower = obj.lower()
        predicate_upper = predicate.upper()
        with self._conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM relations
                WHERE subject = ? AND predicate = ? AND object = ?
                """,
                (subject_lower, predicate_upper, object_lower),
            )
            return cursor.rowcount > 0

    def get_isolated_entities(self, limit: int = 50) -> List[Dict]:
        """获取没有关联任何关系的孤立实体"""
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT e.name, e.type
                FROM entities e
                WHERE e.name NOT IN (SELECT subject FROM relations)
                  AND e.name NOT IN (SELECT object FROM relations)
                LIMIT ?
            """, (limit,))
            return [{"name": row[0], "type": row[1]} for row in cursor.fetchall()]

    def search_entities(self, keyword: str, limit: int = 20) -> List[Dict]:
        """模糊搜索实体，方便不知道准确名称时查找"""
        keyword_lower = keyword.lower()
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT name, type, maintainer_source, confidence, effective_confidence
                FROM entities
                WHERE name LIKE ?
                ORDER BY effective_confidence DESC, name ASC
                LIMIT ?
            """, (f"%{keyword_lower}%", limit))
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
    
    def multi_hop_query(self, start: str, hops: int = 2) -> List[Dict]:
        """
        多跳查询：从起始实体出发，沿关系路径搜索 N 跳。
        
        例: multi_hop_query("python", hops=2) 
        → python → USES → fastapi → DEPENDS_ON → pydantic
        """
        start_lower = start.lower()
        visited = set()
        results = []
        current_level = [start_lower]
        
        with self._conn() as conn:
            for hop in range(hops):
                next_level = []
                for entity in current_level:
                    if entity in visited:
                        continue
                    visited.add(entity)
                    
                    rows = conn.execute("""
                        SELECT subject, predicate, object
                        FROM relations
                        WHERE subject = ? OR object = ?
                    """, (entity, entity)).fetchall()
                    
                    for r in rows:
                        results.append({
                            "hop": hop + 1,
                            "subject": r[0],
                            "predicate": r[1],
                            "object": r[2]
                        })
                        # 追踪下一跳目标
                        neighbor = r[2] if r[0] == entity else r[0]
                        if neighbor not in visited:
                            next_level.append(neighbor)
                
                current_level = next_level
                if not current_level:
                    break
        
        return results
    
    def get_graph_stats(self) -> Dict:
        """获取图谱统计"""
        with self._conn() as conn:
            entities_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            relations_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            top_entities = conn.execute("""
                SELECT name, type, 
                    (SELECT COUNT(*) FROM relations WHERE subject = e.name OR object = e.name) as degree
                FROM entities e
                ORDER BY degree DESC
                LIMIT 10
            """).fetchall()
            
            return {
                "entities": entities_count,
                "relations": relations_count,
                "top_entities": [{"name": r[0], "type": r[1], "degree": r[2]} for r in top_entities]
            }
    
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
    
    def get_full_graph(self, limit: int = 100) -> Dict:
        """
        返回 force-graph 兼容的完整图谱数据。
        
        Returns:
            {
                "nodes": [{"id": "next.js", "label": "Next.js", "type": "technology", "color": "#06b6d4", "val": 5}],
                "links": [{"source": "next.js", "target": "react", "label": "DEPENDS_ON"}]
            }
        
        毒点修复:
        - display name: name.title() 恢复大小写
        - 性能: 只取 Top N 实体 (按 degree 排序) + 相关 links
        """
        with self._conn() as conn:
            # 取 Top N 实体（按关联度排序）
            entities = conn.execute("""
                SELECT e.name, e.type, e.maintainer_source, e.confidence, e.effective_confidence,
                    (SELECT COUNT(*) FROM relations WHERE subject = e.name OR object = e.name) as degree
                FROM entities e
                ORDER BY effective_confidence DESC, degree DESC
                LIMIT ?
            """, (limit,)).fetchall()
            
            if not entities:
                return {"nodes": [], "links": []}
            
            # 构建实体名称集合（用于过滤 links）
            entity_names = {e[0] for e in entities}
            
            # 构建 nodes
            nodes = []
            for name, etype, maintainer_source, confidence, effective_confidence, degree in entities:
                # display name 恢复：lowercase → Title Case
                # 特殊处理："next.js" → "Next.js", "fastapi" → "Fastapi"
                display = name.replace(".", ".").title() if "." not in name else name.title()
                color = self.ENTITY_TYPE_COLORS.get(etype, "#94a3b8")  # 默认 slate
                
                nodes.append({
                    "id": name,
                    "label": display,
                    "type": etype,
                    "color": color,
                    "val": max(degree, 1),  # 节点大小基于关联度
                    "maintainerSource": maintainer_source,
                    "confidence": confidence,
                    "effectiveConfidence": effective_confidence,
                })
            
            # 取所有关系（两端都在 entity_names 中的）
            placeholders = ",".join("?" * len(entity_names))
            relations = conn.execute(f"""
                SELECT subject, predicate, object, confidence, effective_confidence, maintainer_source
                FROM relations
                WHERE subject IN ({placeholders}) AND object IN ({placeholders})
            """, (*entity_names, *entity_names)).fetchall()
            
            links = []
            for subj, pred, obj, conf, effective_conf, maintainer_source in relations:
                links.append({
                    "source": subj,
                    "target": obj,
                    "label": pred,
                    "confidence": conf,
                    "effectiveConfidence": effective_conf,
                    "maintainerSource": maintainer_source,
                })
            
            return {"nodes": nodes, "links": links}
    
    def hard_delete_knowledge(self, fact_id: str) -> bool:
        """
        物理删除知识条目 + 同步清理 FTS5 索引。
        
        毒点修复: 软删除不清理 FTS5 → 本方法彻底清除。
        """
        with self._conn() as conn:
            # 先获取 rowid 以清理 FTS5
            row = conn.execute(
                "SELECT rowid FROM knowledge WHERE id = ?", (fact_id,)
            ).fetchone()
            
            if not row:
                return False
            
            rowid = row[0]
            
            # 删除 FTS5 记录
            conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (rowid,))
            # 删除主表记录
            conn.execute("DELETE FROM knowledge WHERE id = ?", (fact_id,))
            
            return True
    
    def update_knowledge(self, fact_id: str, new_fact: str,
                         category: str = None, scope: str = None,
                         maintainer_source: str | None = None,
                         confidence: float | None = None,
                         agents_hash: str | None = None,
                         repo_signature: str | None = None,
                         signature_policy: str = "soft_v1") -> bool:
        """
        更新知识条目 + 重建 FTS5 索引。
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT rowid, fact, category, scope, confidence, maintainer_source FROM knowledge WHERE id = ?", (fact_id,)
            ).fetchone()
            
            if not row:
                return False
            
            rowid = row[0]
            final_cat = category or row[2]
            final_scope = scope or row[3]
            final_source = str(maintainer_source or row[5] or "memory_runtime").strip() or "memory_runtime"
            final_confidence = _bounded_confidence(confidence if confidence is not None else row[4])
            effective = _effective_confidence(final_confidence, final_source)
            fact_tokenized = tokenize_for_fts(new_fact)
            now = _utc_now_iso()
            
            # 更新主表
            conn.execute("""
                UPDATE knowledge SET fact=?, category=?, scope=?, lifecycle_state='active',
                    last_seen_at=?, agents_hash=COALESCE(NULLIF(?, ''), agents_hash),
                    repo_signature=COALESCE(NULLIF(?, ''), repo_signature),
                    signature_policy=?, maintainer_source=?, confidence=?,
                    effective_confidence=?, updated_at=?
                WHERE id=?
            """, (
                new_fact, final_cat, final_scope, now, agents_hash or "", repo_signature or "",
                signature_policy, final_source, final_confidence, effective, now, fact_id,
            ))
            
            # 重建 FTS5 记录
            conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (rowid,))
            conn.execute("""
                INSERT INTO knowledge_fts(rowid, fact_tokenized, category, scope)
                VALUES (?, ?, ?, ?)
            """, (rowid, fact_tokenized, final_cat, final_scope))
            
            return True
    
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
        """
        执行增量索引：扫描 knowledge/areas/ 下所有 items.json，
        仅对变更文件重建 FTS5 索引。
        """
        areas_dir = V8_AGENT_OS_HOME / "memory" / "knowledge" / "areas"
        if not areas_dir.exists():
            return 0
        
        stale_files = self.get_stale_files(areas_dir, "items.json")
        
        if not stale_files:
            logger.info("[KnowledgeDB] No stale files, index is up-to-date.")
            return 0
        
        total_indexed = 0
        for file_path in stale_files:
            try:
                items = json.loads(file_path.read_text(encoding="utf-8"))
                active_items = [i for i in items if i.get("status") == "active"]
                
                for item in active_items:
                    self.add_knowledge(
                        fact_id=item["id"],
                        fact=item.get("fact", ""),
                        category=item.get("category", "general"),
                        scope=item.get("scope", "global"),
                        source_session=item.get("source_session")
                    )
                    total_indexed += 1
                
                self.mark_indexed(file_path, len(active_items))
                logger.info(f"[KnowledgeDB] Indexed {len(active_items)} items from {file_path.name}")
                
            except Exception as e:
                logger.error(f"[KnowledgeDB] Error indexing {file_path}: {e}")
        
        logger.info(f"[KnowledgeDB] Incremental index complete: {total_indexed} items from {len(stale_files)} files.")
        return total_indexed

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
