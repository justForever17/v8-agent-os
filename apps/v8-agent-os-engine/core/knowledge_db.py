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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
            try:
                conn.execute("ALTER TABLE knowledge ADD COLUMN parent_id TEXT")
            except Exception:
                pass # Column likely already exists
            
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
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 知识图谱：关系表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    source_fact_id TEXT,
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (subject) REFERENCES entities(name),
                    FOREIGN KEY (object) REFERENCES entities(name),
                    UNIQUE(subject, predicate, object)
                )
            """)
            
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
        words = [w for w in tokenized_query.split() if w and len(w) > 0]
        if not words:
            return []
        fts_query = " OR ".join([f'"{w}"*' for w in words])
        
        with self._conn() as conn:
            if scope:
                rows = conn.execute("""
                    SELECT k.id, k.fact, k.category, k.scope, k.status,
                           rank as relevance
                    FROM knowledge_fts f
                    JOIN knowledge k ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.scope IN (?, 'global') AND k.status = 'active'
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, scope, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT k.id, k.fact, k.category, k.scope, k.status,
                           rank as relevance
                    FROM knowledge_fts f
                    JOIN knowledge k ON k.rowid = f.rowid
                    WHERE knowledge_fts MATCH ? AND k.status = 'active'
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, limit)).fetchall()
            
            return [dict(r) for r in rows]
            
    def get_all_knowledge(self, scope: Optional[str] = None, limit: int = 50, status: str = "active") -> List[Dict]:
        """查询所有激活的知识条目"""
        with self._conn() as conn:
            if scope:
                rows = conn.execute("""
                    SELECT id, fact, category, scope, status, source_session, updated_at
                    FROM knowledge
                    WHERE scope IN (?, 'global') AND status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, (scope, status, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, fact, category, scope, status, source_session, updated_at
                    FROM knowledge
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, (status, limit)).fetchall()
            
            return [dict(r) for r in rows]
    
    # ==========================================
    # 知识 CRUD
    # ==========================================
    
    def add_knowledge(self, fact_id: str, fact: str, category: str = "general",
                      scope: str = "global", source_session: Optional[str] = None,
                      parent_id: Optional[str] = None):
        """添加知识条目 + 同步 FTS5 索引（自动 jieba 分词）"""
        fact_tokenized = tokenize_for_fts(fact)
        
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
                           source_session=?, parent_id=?, updated_at=?
                    WHERE id=?
                """, (fact, category, scope, source_session, parent_id, _utc_now_iso(), fact_id))
                conn.execute("""
                    INSERT INTO knowledge_fts(rowid, fact_tokenized, category, scope)
                    VALUES (?, ?, ?, ?)
                """, (old_rowid, fact_tokenized, category, scope))
            else:
                # 新增
                conn.execute("""
                    INSERT INTO knowledge (id, fact, category, scope, status, source_session, parent_id, updated_at)
                    VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                """, (fact_id, fact, category, scope, source_session, parent_id, _utc_now_iso()))
                
                # 获取新 rowid 同步到 FTS5
                new_rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
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
                "UPDATE knowledge SET status = 'deleted', updated_at = ? WHERE id = ? AND status = 'active'",
                (_utc_now_iso(), fact_id)
            )
            return cursor.rowcount > 0

    def set_knowledge_status(self, fact_id: str, status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"active", "deleted", "quarantined"}:
            return False
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE knowledge SET status = ?, updated_at = ? WHERE id = ?",
                (normalized_status, _utc_now_iso(), fact_id),
            )
            return cursor.rowcount > 0

    def quarantine_knowledge(self, fact_id: str) -> bool:
        return self.set_knowledge_status(fact_id, "quarantined")
    
    def get_knowledge_count(self) -> int:
        """获取活跃知识条目数"""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM knowledge WHERE status = 'active'").fetchone()
            return row[0] if row else 0
    
    # ==========================================
    # 知识图谱
    # ==========================================
    
    def add_entity(self, name: str, entity_type: str = "concept"):
        """添加实体"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO entities (name, type) VALUES (?, ?)",
                (name.lower(), entity_type)
            )
    
    def add_relation(self, subject: str, predicate: str, obj: str,
                     source_fact_id: Optional[str] = None, confidence: float = 1.0):
        """
        添加关系三元组: (subject) -[predicate]-> (object)
        
        常用 predicate: USES, DEPENDS_ON, RELATED_TO, IS_A, HAS, PREFERS, WORKS_ON
        """
        subject_lower = subject.lower()
        obj_lower = obj.lower()
        
        with self._conn() as conn:
            # 确保实体存在
            conn.execute("INSERT OR IGNORE INTO entities (name, type) VALUES (?, 'concept')", (subject_lower,))
            conn.execute("INSERT OR IGNORE INTO entities (name, type) VALUES (?, 'concept')", (obj_lower,))
            
            # 添加关系（忽略重复）
            conn.execute("""
                INSERT OR IGNORE INTO relations (subject, predicate, object, source_fact_id, confidence)
                VALUES (?, ?, ?, ?, ?)
            """, (subject_lower, predicate.upper(), obj_lower, source_fact_id, confidence))
    
    def query_entity(self, entity: str) -> List[Dict]:
        """查询实体的所有关系（出边 + 入边）"""
        entity_lower = entity.lower()
        with self._conn() as conn:
            # 出边: entity → ?
            outgoing = conn.execute("""
                SELECT subject, predicate, object, confidence
                FROM relations
                WHERE subject = ?
                ORDER BY confidence DESC
            """, (entity_lower,)).fetchall()
            
            # 入边: ? → entity
            incoming = conn.execute("""
                SELECT subject, predicate, object, confidence
                FROM relations
                WHERE object = ?
                ORDER BY confidence DESC
            """, (entity_lower,)).fetchall()
            
            results = []
            for r in outgoing:
                results.append({"direction": "out", "subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3]})
            for r in incoming:
                results.append({"direction": "in", "subject": r[0], "predicate": r[1], "object": r[2], "confidence": r[3]})
            
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
                SELECT name, type
                FROM entities
                WHERE name LIKE ?
                LIMIT ?
            """, (f"%{keyword_lower}%", limit))
            return [{"name": row[0], "type": row[1]} for row in cursor.fetchall()]
    
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
                SELECT e.name, e.type,
                    (SELECT COUNT(*) FROM relations WHERE subject = e.name OR object = e.name) as degree
                FROM entities e
                ORDER BY degree DESC
                LIMIT ?
            """, (limit,)).fetchall()
            
            if not entities:
                return {"nodes": [], "links": []}
            
            # 构建实体名称集合（用于过滤 links）
            entity_names = {e[0] for e in entities}
            
            # 构建 nodes
            nodes = []
            for name, etype, degree in entities:
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
                })
            
            # 取所有关系（两端都在 entity_names 中的）
            placeholders = ",".join("?" * len(entity_names))
            relations = conn.execute(f"""
                SELECT subject, predicate, object, confidence
                FROM relations
                WHERE subject IN ({placeholders}) AND object IN ({placeholders})
            """, (*entity_names, *entity_names)).fetchall()
            
            links = []
            for subj, pred, obj, conf in relations:
                links.append({
                    "source": subj,
                    "target": obj,
                    "label": pred,
                    "confidence": conf,
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
                         category: str = None, scope: str = None) -> bool:
        """
        更新知识条目 + 重建 FTS5 索引。
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT rowid, fact, category, scope FROM knowledge WHERE id = ?", (fact_id,)
            ).fetchone()
            
            if not row:
                return False
            
            rowid = row[0]
            final_cat = category or row[2]
            final_scope = scope or row[3]
            fact_tokenized = tokenize_for_fts(new_fact)
            
            # 更新主表
            conn.execute("""
                UPDATE knowledge SET fact=?, category=?, scope=?, updated_at=?
                WHERE id=?
            """, (new_fact, final_cat, final_scope, _utc_now_iso(), fact_id))
            
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
        now = _utc_now_iso()
        payload_str = json.dumps(payload, ensure_ascii=False) if payload else "{}"
        
        with self._conn() as conn:
            # 检查是否存在
            existing = conn.execute("SELECT id FROM execution_logs WHERE id = ?", (log_id,)).fetchone()
            if existing:
                # 更新完成状态
                conn.execute("""
                    UPDATE execution_logs 
                    SET status=?, finished_at=?, duration_ms=?, error_message=?
                    WHERE id=?
                """, (status, now, duration_ms, error_message, log_id))
            else:
                # 初始插入
                conn.execute("""
                    INSERT INTO execution_logs (id, task_name, action_type, action_target, trigger_source, status, started_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (log_id, task_name, action_type, action_target, trigger_source, status, now, payload_str))
                
    def get_execution_logs(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """获取执行日志列表，按时间倒序"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, task_name, action_type, action_target, trigger_source, status, 
                       started_at, finished_at, duration_ms, error_message, payload
                FROM execution_logs
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
            
            results = []
            for r in rows:
                row_dict = dict(r)
                # 解析 payload 回 Dict
                if row_dict.get("payload"):
                    try:
                        row_dict["payload"] = json.loads(row_dict["payload"])
                    except:
                        pass
                results.append(row_dict)
            return results

# === 全局单例 ===
knowledge_db = KnowledgeDB()
