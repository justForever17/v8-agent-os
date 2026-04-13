"""
分层记忆存储引擎 — MEMORY.md scope-based 偏好 + 知识分区 + 时序日志

核心设计:
- MEMORY.md: scope 分区偏好（人类可读可编辑）
- knowledge/areas/{scope}/items.json: 分区知识库
- daily/{YYYY}/{MM}/{YYYY-MM-DD}.md: 时序日志
- .index/: 向量索引（ChromaDB）

参考: adaptive-agent-mcp 的 MemoryParser + KnowledgeRouter
"""

import re
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

logger = logging.getLogger("v8_agent_os.memory")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# === 配置 ===
CONFIG_DIR = V8_AGENT_OS_HOME
MEMORY_ROOT = CONFIG_DIR / "memory"

# === MEMORY.md 模板 ===
MEMORY_TEMPLATE_V2 = """---
type: user_preferences
version: "2.0"
last_updated: "{date}"
---

[global]
# 全局偏好 — 适用于所有场景
language: zh-CN
system_name: V8 Agent OS
system_slug: v8-agent-os
system_author: justForever17
"""

# === 正则 ===
SCOPE_PATTERN = re.compile(r'^\[([^\]]+)\]$', re.MULTILINE)
KV_PATTERN = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$', re.MULTILINE)
_SPECIFIC_SCOPE_PREFIXES = ("project:", "channel:")


class MemoryStore:
    """
    分层记忆存储引擎。
    
    Layer 1: 用户画像 (MEMORY.md, scope-based KV)
    Layer 2: 知识库 (knowledge/areas/{scope}/items.json)
    Layer 3: 时序日志 (daily/YYYY/MM/YYYY-MM-DD.md)
    """
    
    def __init__(self):
        self.memory_path = MEMORY_ROOT / "MEMORY.md"
        self._preferences_cache: Optional[Dict[str, Dict[str, str]]] = None
        self._cache_mtime: float = 0.0
        self._ensure_structure()
    
    # ==========================================
    # 目录结构初始化
    # ==========================================
    
    def _ensure_structure(self):
        """确保存储目录结构存在"""
        MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
        
        # 知识分区目录
        areas_dir = MEMORY_ROOT / "knowledge" / "areas"
        for area in ["general", "projects", "channels"]:
            (areas_dir / area).mkdir(parents=True, exist_ok=True)
        
        # 日志目录
        (MEMORY_ROOT / "daily").mkdir(exist_ok=True)
        
        # 索引目录
        (MEMORY_ROOT / ".index").mkdir(exist_ok=True)
        
        # 图谱目录
        (MEMORY_ROOT / ".graph").mkdir(exist_ok=True)
        
        # 创建默认 MEMORY.md
        if not self.memory_path.exists():
            content = MEMORY_TEMPLATE_V2.format(date=datetime.now().strftime("%Y-%m-%d"))
            self.memory_path.write_text(content, encoding="utf-8")
            logger.info(f"[MemoryStore] Created default MEMORY.md at {self.memory_path}")

    def _is_valid_scope(self, scope: str) -> bool:
        normalized = (scope or "").strip()
        if not normalized:
            return False
        if normalized == "global":
            return True
        return normalized.startswith(_SPECIFIC_SCOPE_PREFIXES)

    def _validate_scope(self, scope: str) -> str:
        normalized = (scope or "").strip()
        if not self._is_valid_scope(normalized):
            raise ValueError(f"Unsupported memory scope: {scope}")
        return normalized

    def _trim_text_to_budget(self, text: str, remaining_tokens: int) -> str:
        if not text or remaining_tokens <= 0:
            return ""
        max_chars = max(0, remaining_tokens * 4)
        if len(text) <= max_chars:
            return text
        if max_chars <= 3:
            return text[:max_chars]
        return text[: max_chars - 3].rstrip() + "..."
    
    # ==========================================
    # Layer 1: 偏好管理 (MEMORY.md)
    # ==========================================
    
    def _load_raw_preferences(self) -> Dict[str, Dict[str, str]]:
        """解析 MEMORY.md，返回 {scope: {key: value}} 字典"""
        if not self.memory_path.exists():
            return {"global": {}}
        
        # 缓存检查：仅当文件变更时重新解析
        try:
            mtime = self.memory_path.stat().st_mtime
            if self._preferences_cache is not None and mtime == self._cache_mtime:
                return self._preferences_cache
        except Exception:
            pass
        
        content = self.memory_path.read_text(encoding="utf-8")
        
        # 剥离 YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if fm_match:
            content = content[fm_match.end():]
        
        # 按 scope 分区解析
        data: Dict[str, Dict[str, str]] = {"global": {}}
        current_scope = "global"
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # 检测 [scope] 头
            scope_match = SCOPE_PATTERN.match(line)
            if scope_match:
                current_scope = scope_match.group(1)
                if not self._is_valid_scope(current_scope):
                    logger.warning(f"[MemoryStore] Ignoring invalid scope section in MEMORY.md: {current_scope}")
                    current_scope = "global"
                    continue
                if current_scope not in data:
                    data[current_scope] = {}
                continue
            
            # 解析 key: value
            kv_match = KV_PATTERN.match(line)
            if kv_match:
                key = kv_match.group(1)
                value = kv_match.group(2).strip()
                data[current_scope][key] = value
        
        # 更新缓存
        self._preferences_cache = data
        try:
            self._cache_mtime = self.memory_path.stat().st_mtime
        except Exception:
            self._cache_mtime = 0.0
        
        return data
    
    def load_preferences(self, scope: str = "global", scope_chain: Optional[List[str]] = None) -> Dict[str, str]:
        """
        加载合并后的偏好（考虑 scope 优先级回退）。
        
        优先级链: current_scope > global
        """
        all_data = self._load_raw_preferences()
        
        scopes_order = self._normalize_scope_chain(scope=scope, scope_chain=scope_chain)
        
        # 从低到高合并
        merged = {}
        for s in scopes_order:
            if s in all_data:
                merged.update(all_data[s])
        
        return merged
    
    def get_all_scopes(self) -> List[str]:
        """获取所有已定义的 scope"""
        return list(self._load_raw_preferences().keys())
    
    def update_preference(self, key: str, value: str, scope: str = "global"):
        """
        写入偏好到 MEMORY.md（覆盖同 scope 同 key）。
        """
        normalized_scope = self._validate_scope(scope)
        data = self._load_raw_preferences()
        
        if normalized_scope not in data:
            data[normalized_scope] = {}
        data[normalized_scope][key] = value
        
        self._save_preferences(data)
        logger.info(f"[MemoryStore] Updated preference [{normalized_scope}] {key} = {value}")

    def delete_preference(self, key: str, scope: str = "global") -> bool:
        """
        从 MEMORY.md 删除某个 scope 下的单条偏好。
        """
        normalized_scope = self._validate_scope(scope)
        data = self._load_raw_preferences()
        if normalized_scope not in data or key not in data[normalized_scope]:
            return False

        del data[normalized_scope][key]
        self._save_preferences(data)
        logger.info(f"[MemoryStore] Deleted preference [{normalized_scope}] {key}")
        return True
    
    def _save_preferences(self, data: Dict[str, Dict[str, str]]):
        """将偏好数据序列化写回 MEMORY.md"""
        lines = []
        
        # Frontmatter
        lines.append("---")
        lines.append("type: user_preferences")
        lines.append('version: "2.0"')
        lines.append(f'last_updated: "{datetime.now().strftime("%Y-%m-%d")}"')
        lines.append("---")
        lines.append("")
        
        # Scope 注释
        scope_comments = {
            "global": "# 全局偏好 — 适用于所有场景",
        }
        
        # global 优先，其余排序
        valid_scopes = [s for s in data if self._is_valid_scope(s)]
        scope_order = ["global"] + sorted([s for s in valid_scopes if s != "global"])
        
        for scope in scope_order:
            if scope not in data:
                continue
            
            lines.append(f"[{scope}]")
            if scope in scope_comments:
                lines.append(scope_comments[scope])
            elif scope.startswith("project:"):
                project_name = scope.split(":", 1)[1]
                lines.append(f"# 项目 {project_name} 专属偏好")
            elif scope.startswith("channel:"):
                channel_name = scope.split(":", 1)[1]
                lines.append(f"# 渠道 {channel_name} 专属偏好")
            
            for key, value in data[scope].items():
                if not key.startswith("_"):
                    lines.append(f"{key}: {value}")
            lines.append("")
        
        content = "\n".join(lines)
        self.memory_path.write_text(content, encoding="utf-8")
        
        # 刷新缓存
        self._preferences_cache = data
        try:
            self._cache_mtime = self.memory_path.stat().st_mtime
        except Exception:
            pass
    
    def format_preferences_for_injection(self, scope: str = "global", scope_chain: Optional[List[str]] = None) -> str:
        """格式化偏好用于 System Prompt 注入"""
        prefs = self.load_preferences(scope, scope_chain=scope_chain)
        if not prefs:
            return ""
        
        lines = [f"- {key}: {value}" for key, value in prefs.items()]
        return "\n".join(lines)
    
    # ==========================================
    # Layer 2: 知识分区 (knowledge/areas/)
    # ==========================================
    
    def _get_knowledge_path(self, scope: str, category: str = "general") -> Path:
        """根据 scope 路由到分区文件"""
        scope = self._validate_scope(scope)
        areas_dir = MEMORY_ROOT / "knowledge" / "areas"
        
        if scope.startswith("project:"):
            project_name = scope.split(":", 1)[1]
            path = areas_dir / "projects" / project_name / "items.json"
        elif scope.startswith("channel:"):
            channel_name = scope.split(":", 1)[1].replace(":", "__")
            path = areas_dir / "channels" / channel_name / "items.json"
        else:
            path = areas_dir / "general" / "items.json"
        
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    
    def add_knowledge(
        self,
        fact: str,
        category: str,
        scope: str = "global",
        source_session: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        """添加知识到分区 JSON + SQLite DB (FTS5)"""
        normalized_scope = self._validate_scope(scope)
        path = self._get_knowledge_path(normalized_scope, category)
        
        items = []
        if path.exists():
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                items = []
        
        fact_id = f"fact-{uuid.uuid4().hex[:8]}"
        item = {
            "id": fact_id,
            "fact": fact,
            "category": category,
            "scope": normalized_scope,
            "status": "active",
            "timestamp": _utc_now_iso(),
            "source_session": source_session,
            "tags": [str(tag).strip() for tag in list(tags or []) if str(tag).strip()],
        }
        items.append(item)
        
        path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        
        # 同步写入 SQLite DB (FTS5 自动索引)
        try:
            from core.knowledge_db import knowledge_db
            knowledge_db.add_knowledge(fact_id, fact, category, normalized_scope, source_session)
        except Exception as e:
            logger.warning(f"[MemoryStore] DB sync failed (non-fatal): {e}")

        # 同步写入 Vector Store
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            metadata = {"category": category, "scope": normalized_scope}
            if item.get("tags"):
                metadata["tags"] = ",".join(item["tags"])
            vs.add_documents([{"id": fact_id, "text": fact, "metadata": metadata}])
        except Exception as e:
            logger.warning(f"[MemoryStore] Vector Store sync failed (non-fatal): {e}")
        
        logger.info(f"[MemoryStore] Added knowledge {fact_id} to {path.name} [{normalized_scope}]")
        return fact_id
    
    def update_knowledge(self, fact_id: str, new_fact: str, category: str = None, scope: str = None) -> bool:
        """更新分区 JSON、SQLite 知识库及向量库"""
        from core.knowledge_db import knowledge_db
        try:
            # 找到旧的 scope
            with knowledge_db._conn() as conn:
                row = conn.execute("SELECT scope FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                if not row:
                    return False
                old_scope = row[0]
        except Exception as e:
            logger.warning(f"[MemoryStore] Could not fetch old scope: {e}")
            return False
            
        path = self._get_knowledge_path(old_scope)
        if not path.exists():
            return False
            
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            updated = False
            for item in items:
                if item.get("id") == fact_id:
                    item["fact"] = new_fact
                    if category: item["category"] = category
                    if scope:
                        item["scope"] = self._validate_scope(scope)
                        item["timestamp"] = _utc_now_iso()
                    updated = True
                    break
            if updated:
                path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
                # 更新 SQLite
                next_scope = self._validate_scope(scope) if scope else None
                knowledge_db.update_knowledge(fact_id, new_fact, category, next_scope)
                
                # 更新 Vector Store (通过覆盖同一 ID)
                try:
                    from core.vector_store import get_vector_store
                    vs = get_vector_store()
                    # We might need the full item for vector store, pull from updated item
                    updated_item = next((i for i in items if i.get("id") == fact_id), None)
                    if updated_item:
                        vs.add_documents([{"id": fact_id, "text": new_fact, "metadata": {"category": updated_item.get("category", "general"), "scope": updated_item.get("scope", "global")}}])
                except Exception as e:
                    logger.warning(f"[MemoryStore] Vector Store sync failed (non-fatal): {e}")

                logger.info(f"[MemoryStore] Updated knowledge {fact_id} in {path.name}")
                return True
        except Exception as e:
            logger.warning(f"[MemoryStore] Error updating JSON knowledge {fact_id}: {e}")
        return False
        
    def delete_knowledge(self, fact_id: str) -> bool:
        """从 JSON、SQLite (并且级联图谱边) 和向量库中物理/逻辑删除知识项"""
        from core.knowledge_db import knowledge_db
        try:
            # 找到旧的 scope
            with knowledge_db._conn() as conn:
                row = conn.execute("SELECT scope FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                if not row:
                    return False
                target_scope = row[0]
        except Exception as e:
            logger.warning(f"[MemoryStore] Could not fetch scope for deletion: {e}")
            return False
            
        path = self._get_knowledge_path(target_scope)
        if path.exists():
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
                original_len = len(items)
                # Soft delete in JSON to keep history, or hard delete. Adaptive approach commonly uses soft delete in JSON:
                for item in items:
                    if item.get("id") == fact_id and item.get("status") == "active":
                        item["status"] = "deleted"
                        item["deleted_at"] = _utc_now_iso()
                
                path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[MemoryStore] Error updating JSON for deletion {fact_id}: {e}")
        
        # 彻底执行级联删除 SQLite
        try:
            with knowledge_db._conn() as conn:
                # 级联删除关联此事实的图谱关系
                conn.execute("DELETE FROM relations WHERE source_fact_id = ?", (fact_id,))
                # 逻辑删除知识主条目并标记
                conn.execute("UPDATE knowledge SET status = 'deleted' WHERE id = ?", (fact_id,))
                # FTS表是触发器自动维护或靠重新索引维护，可以直接从FTS删以防止搜到
                conn.execute("DELETE FROM knowledge_fts WHERE rowid IN (SELECT rowid FROM knowledge WHERE id = ?)", (fact_id,))
        except Exception as e:
            logger.warning(f"[MemoryStore] DB cascade delete failed: {e}")
            
        # 尝试从 Vector Store 中删除 (Chroma)
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            if vs.collection:
                vs.collection.delete(ids=[fact_id])
        except Exception as e:
            logger.warning(f"[MemoryStore] Vector Store delete failed (non-fatal): {e}")
            
        logger.info(f"[MemoryStore] Deleted knowledge {fact_id} completely.")
        return True
    
    def query_knowledge(self, query: Optional[str] = None,
                        scope: Optional[str] = None,
                        scopes: Optional[List[str]] = None,
                        category: Optional[str] = None,
                        limit: int = 20) -> List[Dict]:
        """
        查询知识。优先使用 FTS5 全文检索，无查询词时直接从数据库获取最新列表。
        """
        from core.knowledge_db import knowledge_db

        scope_candidates = self._normalize_scope_chain(scope=scope or "global", scope_chain=scopes)
        include_exact_scopes = [item for item in scope_candidates if item != "global"]
        results = []

        if include_exact_scopes:
            seen_ids = set()
            for candidate_scope in reversed(include_exact_scopes):
                try:
                    batch = knowledge_db.fts_search(query, scope=candidate_scope, limit=limit) if query else knowledge_db.get_all_knowledge(scope=candidate_scope, limit=limit)
                except Exception as e:
                    logger.warning(f"[MemoryStore] Scoped knowledge query failed for {candidate_scope}: {e}")
                    batch = []
                for item in batch:
                    item_id = item.get("id")
                    if item_id and item_id in seen_ids:
                        continue
                    if item_id:
                        seen_ids.add(item_id)
                    results.append(item)
                if len(results) >= limit:
                    break
        else:
            if query:
                try:
                    results = knowledge_db.fts_search(query, scope=scope, limit=limit)
                    if results:
                        logger.info(f"[MemoryStore] FTS5 returned {len(results)} results for '{query}'")
                except Exception as e:
                    logger.warning(f"[MemoryStore] FTS5 search failed: {e}")
                    results = []
            else:
                try:
                    results = knowledge_db.get_all_knowledge(scope=scope, limit=limit)
                except Exception as e:
                    logger.warning(f"[MemoryStore] DB direct query failed: {e}")
                    results = []
                
        if category:
            results = [r for r in results if r.get("category") == category]

        results = [r for r in results if self._is_valid_scope(str(r.get("scope") or "global"))]
        return results[:limit]

    def _load_recall_runtime_config(
        self,
        *,
        limit: int,
    ) -> Dict[str, Any]:
        from core.storage import MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED, storage

        memory_config = storage.get_memory_config() or {}
        recall_strategy = str(memory_config.get("recall_strategy") or "balanced").strip().lower()
        if recall_strategy not in {"balanced", "semantic", "keyword"}:
            recall_strategy = "balanced"

        try:
            configured_top_k = int(memory_config.get("recall_top_k") or limit or 5)
        except (TypeError, ValueError):
            configured_top_k = limit or 5
        effective_limit = max(1, configured_top_k if limit == 5 else int(limit or configured_top_k))

        try:
            retrieval_threshold = float(memory_config.get("retrieval_threshold"))
        except (TypeError, ValueError, KeyError):
            retrieval_threshold = MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED
        retrieval_threshold = max(0.0, min(retrieval_threshold, 1.0))

        return {
            "memory_config": memory_config,
            "recall_strategy": recall_strategy,
            "effective_limit": effective_limit,
            "retrieval_threshold": retrieval_threshold,
            "use_vector": recall_strategy in {"balanced", "semantic"},
            "use_fts": bool(memory_config.get("fts_enabled", True)) and recall_strategy in {"balanced", "keyword"},
            "use_graph": bool(memory_config.get("graph_enabled", True)),
        }

    def _normalize_recall_score(self, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(score, 1.0))

    def _normalize_fts_relevance(self, raw_rank: Any, *, position: int, total: int) -> float:
        try:
            rank_value = abs(float(raw_rank))
        except (TypeError, ValueError):
            rank_value = 9999.0
        rank_score = 1.0 / (1.0 + rank_value)
        positional_score = max(0.0, 1.0 - (position / max(total, 1)))
        return max(0.0, min((rank_score * 0.7) + (positional_score * 0.3), 1.0))

    def _merge_recall_candidate(self, pool: Dict[str, Dict[str, Any]], candidate: Dict[str, Any]) -> None:
        candidate_id = str(candidate.get("id") or "").strip()
        if not candidate_id:
            return
        normalized_candidate = {
            "id": candidate_id,
            "fact": str(candidate.get("fact") or "").strip(),
            "category": str(candidate.get("category") or "general").strip() or "general",
            "scope": str(candidate.get("scope") or "global").strip() or "global",
            "source": str(candidate.get("source") or "unknown").strip() or "unknown",
            "raw_relevance_score": self._normalize_recall_score(candidate.get("raw_relevance_score")),
            "final_relevance_score": self._normalize_recall_score(candidate.get("final_relevance_score")),
            "accepted": bool(candidate.get("accepted", False)),
            "reject_reason": str(candidate.get("reject_reason") or "").strip(),
        }
        existing = pool.get(candidate_id)
        if existing is None:
            pool[candidate_id] = normalized_candidate
            return
        merged_sources = {
            item.strip()
            for item in f"{existing.get('source', '')}+{normalized_candidate['source']}".split("+")
            if item.strip()
        }
        existing["source"] = "+".join(sorted(merged_sources))
        existing["raw_relevance_score"] = max(
            self._normalize_recall_score(existing.get("raw_relevance_score")),
            normalized_candidate["raw_relevance_score"],
        )
        if not str(existing.get("fact") or "").strip() and normalized_candidate["fact"]:
            existing["fact"] = normalized_candidate["fact"]
        if not str(existing.get("category") or "").strip():
            existing["category"] = normalized_candidate["category"]
        if not str(existing.get("scope") or "").strip():
            existing["scope"] = normalized_candidate["scope"]

    def _extract_graph_seed_entities(self, query: str, seed_items: List[Dict[str, Any]]) -> List[str]:
        stop_words = {
            "为什么", "怎么", "如何", "什么", "这个", "那个", "这些", "那些", "需要", "以及",
            "记忆", "内容", "问题", "系统", "当前", "最近", "事情", "说明", "结果", "因为",
            "that", "this", "with", "from", "will", "would", "should", "about", "have", "into",
            "memory", "context", "issue", "problem", "result", "query", "history",
        }
        fragments = [str(query or "").strip()]
        fragments.extend(str(item.get("fact") or "").strip()[:240] for item in seed_items[:3])
        entities: List[str] = []
        for fragment in fragments:
            if not fragment:
                continue
            for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9._-]{2,}\b", fragment):
                normalized = token.lower().strip()
                if normalized in stop_words or normalized in entities:
                    continue
                entities.append(normalized)
            for token in re.findall(r"[\u4e00-\u9fa5]{2,8}", fragment):
                normalized = token.strip()
                if normalized in stop_words or normalized in entities:
                    continue
                entities.append(normalized)
        return entities[:6]

    def _execute_unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from core.knowledge_db import knowledge_db

        config = self._load_recall_runtime_config(limit=limit)
        effective_limit = int(config["effective_limit"])
        retrieval_threshold = float(config["retrieval_threshold"])
        minimum_quality_floor = 0.05
        effective_acceptance_threshold = max(retrieval_threshold, minimum_quality_floor)
        recall_strategy = str(config["recall_strategy"])
        use_vector = bool(config["use_vector"])
        use_fts = bool(config["use_fts"])
        use_graph = bool(config["use_graph"])

        allowed_scopes = set(self._normalize_scope_chain(scope=scope or "global", scope_chain=scopes))
        if "global" not in allowed_scopes:
            allowed_scopes.add("global")

        seed_candidates: Dict[str, Dict[str, Any]] = {}
        diagnostics: Dict[str, Any] = {
            "query": query,
            "scope": scope,
            "scopes": list(scopes or []),
            "allowed_scopes": list(allowed_scopes),
            "recall_strategy": recall_strategy,
            "threshold_snapshot": retrieval_threshold,
            "effective_acceptance_threshold": effective_acceptance_threshold,
            "seed_candidate_count": 0,
            "graph_candidate_count": 0,
            "graph_allowed": False,
            "graph_reject_reason": "",
            "graph_entities": [],
            "rerank_error": "",
        }

        if use_vector:
            try:
                from core.vector_store import get_vector_store

                vs = get_vector_store()
                vector_results = vs.similarity_search_with_rerank(
                    query,
                    top_k=max(effective_limit * 2, 6),
                    fetch_k=max(effective_limit * 4, 20),
                )
                for result in vector_results:
                    fact_id = result["id"]
                    parent_id = result.get("metadata", {}).get("parent_id")
                    final_fact = result["text"]
                    final_id = fact_id
                    if parent_id:
                        with knowledge_db._conn() as conn:
                            parent_row = conn.execute(
                                "SELECT fact FROM knowledge WHERE id = ?",
                                (parent_id,),
                            ).fetchone()
                            if parent_row:
                                final_fact = parent_row[0]
                                final_id = parent_id
                    item_scope = str(result.get("metadata", {}).get("scope", "global") or "global")
                    if item_scope not in allowed_scopes:
                        continue
                    self._merge_recall_candidate(
                        seed_candidates,
                        {
                            "id": final_id,
                            "fact": final_fact,
                            "category": result.get("metadata", {}).get("category", "general"),
                            "scope": item_scope,
                            "source": "vector",
                            "raw_relevance_score": result.get("relevance_score", 0.0),
                        },
                    )
            except Exception as exc:
                logger.warning(f"[MemoryStore] Vector search error in unified_recall: {exc}")

        if use_fts:
            try:
                fts_results = knowledge_db.fts_search(query, limit=max(effective_limit * 4, 8))
                total_fts = len(fts_results)
                for index, result in enumerate(fts_results):
                    fact_id = result.get("id")
                    final_fact = result.get("fact", "")
                    final_id = fact_id
                    item_scope = str(result.get("scope", "global") or "global")
                    if item_scope not in allowed_scopes:
                        continue
                    with knowledge_db._conn() as conn:
                        parent_row = conn.execute(
                            "SELECT parent_id FROM knowledge WHERE id = ?",
                            (fact_id,),
                        ).fetchone()
                        if parent_row and parent_row[0]:
                            elevated = conn.execute(
                                "SELECT fact FROM knowledge WHERE id = ?",
                                (parent_row[0],),
                            ).fetchone()
                            if elevated:
                                final_fact = elevated[0]
                                final_id = parent_row[0]
                    self._merge_recall_candidate(
                        seed_candidates,
                        {
                            "id": final_id,
                            "fact": final_fact,
                            "category": result.get("category", "general"),
                            "scope": item_scope,
                            "source": "fts5",
                            "raw_relevance_score": self._normalize_fts_relevance(
                                result.get("relevance"),
                                position=index,
                                total=total_fts,
                            ),
                        },
                    )
            except Exception as exc:
                logger.warning(f"[MemoryStore] FTS5 search error in unified_recall: {exc}")

        seed_items = sorted(
            seed_candidates.values(),
            key=lambda item: (
                self._normalize_recall_score(item.get("raw_relevance_score")),
                str(item.get("source") or ""),
            ),
            reverse=True,
        )
        diagnostics["seed_candidate_count"] = len(seed_items)

        combined_candidates: Dict[str, Dict[str, Any]] = dict(seed_candidates)
        graph_seed_floor = max(effective_acceptance_threshold, 0.20)
        graph_seed_items = [
            item for item in seed_items
            if self._normalize_recall_score(item.get("raw_relevance_score")) >= graph_seed_floor
        ]

        if use_graph and graph_seed_items:
            try:
                graph_entities = self._extract_graph_seed_entities(query, graph_seed_items)
                diagnostics["graph_entities"] = graph_entities
                extracted_relations: set[str] = set()
                base_graph_score = max(
                    self._normalize_recall_score(graph_seed_items[0].get("raw_relevance_score")),
                    graph_seed_floor,
                ) * 0.9
                for entity in graph_entities:
                    relations = knowledge_db.multi_hop_query(entity, hops=2)
                    for relation in relations:
                        relation_text = f"{relation['subject']} {relation['predicate']} {relation['object']}"
                        if relation_text in extracted_relations:
                            continue
                        extracted_relations.add(relation_text)
                        virtual_id = f"graph:{uuid.uuid5(uuid.NAMESPACE_OID, relation_text).hex[:12]}"
                        self._merge_recall_candidate(
                            combined_candidates,
                            {
                                "id": virtual_id,
                                "fact": f"[Graph Context] {relation_text}",
                                "category": "graph_context",
                                "scope": "global",
                                "source": "graph",
                                "raw_relevance_score": base_graph_score,
                            },
                        )
                diagnostics["graph_candidate_count"] = sum(
                    1 for item in combined_candidates.values()
                    if str(item.get("source") or "").find("graph") >= 0
                )
                diagnostics["graph_allowed"] = diagnostics["graph_candidate_count"] > 0
                if not diagnostics["graph_allowed"]:
                    diagnostics["graph_reject_reason"] = "no_graph_relations_from_seed_entities"
            except Exception as exc:
                diagnostics["graph_reject_reason"] = f"graph_pipeline_failed:{exc}"
                logger.warning(f"[MemoryStore] Graph expansion pipeline failed in unified_recall: {exc}")
        elif use_graph:
            diagnostics["graph_reject_reason"] = "no_high_quality_seed_results"
        else:
            diagnostics["graph_reject_reason"] = "graph_disabled"

        if not combined_candidates:
            return {
                "items": [],
                "accepted_items": [],
                "threshold_snapshot": retrieval_threshold,
                "diagnostics": diagnostics,
            }

        ids_order: List[str] = []
        docs_to_rerank: List[str] = []
        for fact_id, item in combined_candidates.items():
            ids_order.append(fact_id)
            docs_to_rerank.append(str(item.get("fact") or ""))

        reranked_scores: Dict[str, float] = {}
        try:
            from core.memory_router import MemoryRouter

            reranker = MemoryRouter().get_reranker_model()
            ranked = reranker.rerank(query, docs_to_rerank, top_k=len(docs_to_rerank))
            for row in ranked:
                idx = int(row.get("index") or 0)
                if idx < 0 or idx >= len(ids_order):
                    continue
                reranked_scores[ids_order[idx]] = self._normalize_recall_score(row.get("relevance_score", 0.0))
        except Exception as exc:
            diagnostics["rerank_error"] = str(exc)
            logger.warning(f"[MemoryStore] Unified recall reranking failed, falling back to raw scores: {exc}")

        all_items: List[Dict[str, Any]] = []
        for fact_id, item in combined_candidates.items():
            raw_score = self._normalize_recall_score(item.get("raw_relevance_score"))
            final_score = reranked_scores.get(fact_id, raw_score)
            accepted = final_score >= effective_acceptance_threshold
            all_items.append(
                {
                    **item,
                    "raw_relevance_score": raw_score,
                    "final_relevance_score": final_score,
                    "relevance_score": final_score,
                    "accepted": accepted,
                    "reject_reason": "" if accepted else "below_threshold",
                }
            )

        all_items.sort(
            key=lambda item: (
                self._normalize_recall_score(item.get("final_relevance_score")),
                self._normalize_recall_score(item.get("raw_relevance_score")),
            ),
            reverse=True,
        )

        all_accepted_items = [item for item in all_items if item.get("accepted")]
        accepted_items = all_accepted_items[:effective_limit]
        diagnostics["accepted_count"] = len(all_accepted_items)
        diagnostics["rejected_count"] = max(0, len(all_items) - len(all_accepted_items))

        return {
            "items": all_items[: max(effective_limit * 4, 12)],
            "accepted_items": accepted_items,
            "threshold_snapshot": retrieval_threshold,
            "effective_acceptance_threshold": effective_acceptance_threshold,
            "diagnostics": diagnostics,
        }

    def preview_unified_recall(
        self,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        preview = self._execute_unified_recall(query=query, limit=limit, scope=scope, scopes=scopes)
        return {
            "query": query,
            "scope": scope,
            "scopes": scopes or [],
            "threshold_snapshot": preview.get("threshold_snapshot"),
            "effective_acceptance_threshold": preview.get("effective_acceptance_threshold"),
            "diagnostics": preview.get("diagnostics") or {},
            "items": preview.get("items") or [],
            "accepted_items": preview.get("accepted_items") or [],
        }

    def unified_recall(self, query: str, limit: int = 5, scope: Optional[str] = None, scopes: Optional[List[str]] = None) -> List[Dict]:
        preview = self._execute_unified_recall(query=query, limit=limit, scope=scope, scopes=scopes)
        return list(preview.get("accepted_items") or [])
            
    # ==========================================
    # Layer 3: 时序日志 (daily/)
    # ==========================================
    
    def _get_daily_log_path(self, date: Optional[datetime] = None) -> Path:
        """获取日志文件路径: YYYY/MM_monthname/week_WW/YYYY-MM-DD.md"""
        if date is None:
            date = datetime.now()
        
        year = date.strftime("%Y")
        month_name = date.strftime("%m_%B").lower()
        week_num = date.strftime("%V")
        filename = date.strftime("%Y-%m-%d.md")
        
        path = MEMORY_ROOT / "daily" / year / month_name / f"week_{week_num}"
        path.mkdir(parents=True, exist_ok=True)
        return path / filename
    
    def append_daily_log(self, content: str, tags: Optional[List[str]] = None):
        """追加日志条目"""
        now = datetime.now()
        log_path = self._get_daily_log_path(now)
        
        # 新文件写入 frontmatter
        if not log_path.exists() or log_path.stat().st_size == 0:
            header_tags = str(tags) if tags else "[]"
            header = f"---\ntype: daily_log\ndate: \"{now.strftime('%Y-%m-%d')}\"\ntags: {header_tags}\n---\n\n"
            log_path.write_text(header, encoding="utf-8")
        
        # 追加带时间戳的条目
        time_str = now.strftime("%H:%M")
        entry = f"\n### {time_str}\n{content}\n"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
        
        logger.info(f"[MemoryStore] Appended daily log to {log_path}")

    def _split_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        header_match = re.match(r'^---\n(.*?)\n---\s*', content, flags=re.DOTALL)
        if not header_match:
            return {}, content
        header = header_match.group(1)
        body = content[header_match.end():]
        metadata: Dict[str, Any] = {}
        current_list_key: Optional[str] = None
        for raw_line in header.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            list_item = re.match(r'^\s*-\s*"?(.+?)"?\s*$', line)
            if list_item and current_list_key:
                metadata.setdefault(current_list_key, [])
                metadata[current_list_key].append(list_item.group(1).strip())
                continue
            key_match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', line)
            if not key_match:
                continue
            key = key_match.group(1).strip()
            value = key_match.group(2).strip()
            if value == "":
                metadata[key] = []
                current_list_key = key
            else:
                metadata[key] = value.strip('"')
                current_list_key = None
        return metadata, body

    def _render_frontmatter(self, metadata: Dict[str, Any]) -> str:
        lines = ["---"]
        for key in ("type", "date"):
            value = metadata.get(key)
            if value is not None:
                lines.append(f'{key}: "{value}"' if key == "date" else f"{key}: {value}")
        for key in ("tags", "summaries"):
            values = [str(item).strip() for item in list(metadata.get(key) or []) if str(item).strip()]
            lines.append(f"{key}:")
            for item in values:
                lines.append(f'  - "{item.replace(chr(34), "")}"')
        lines.append("---\n")
        return "\n".join(lines)

    def append_daily_log_with_yaml(
        self,
        content: str,
        session_summary: str,
        session_tags: List[str],
        entry_metadata: Optional[Dict[str, Any]] = None,
    ):
        """追加结构化日志并稳定维护 YAML frontmatter。"""
        now = datetime.now()
        log_path = self._get_daily_log_path(now)
        time_str = now.strftime("%H:%M")

        if log_path.exists() and log_path.stat().st_size > 0:
            existing_meta, existing_body = self._split_frontmatter(log_path.read_text(encoding="utf-8"))
        else:
            existing_meta, existing_body = {}, ""

        merged_tags = list(dict.fromkeys([*list(existing_meta.get("tags") or []), *[str(tag).strip() for tag in session_tags if str(tag).strip()]]))
        merged_summaries = list(
            dict.fromkeys(
                [*list(existing_meta.get("summaries") or []), *([session_summary.strip()] if str(session_summary or "").strip() else [])]
            )
        )
        meta = {
            "type": "daily_log",
            "date": now.strftime("%Y-%m-%d"),
            "tags": merged_tags,
            "summaries": merged_summaries,
        }

        entry_metadata = dict(entry_metadata or {})
        structured_lines = [
            f"session_id: {entry_metadata.get('session_id') or 'unknown'}",
            f"effective_memory_scope: {entry_metadata.get('effective_memory_scope') or 'global'}",
            f"source_runtime: {entry_metadata.get('source_runtime') or 'chat'}",
            f"provenance_class: {entry_metadata.get('provenance_class') or 'human_dialogue'}",
            f"memory_policy: {entry_metadata.get('memory_policy') or 'durable'}",
            f"extracted_long_term_items_count: {int(entry_metadata.get('extracted_long_term_items_count') or 0)}",
            f"summary: {session_summary.strip() if str(session_summary or '').strip() else 'n/a'}",
            "",
            content.strip(),
        ]
        entry = f"\n### {time_str}\n" + "\n".join(line for line in structured_lines if line is not None).strip() + "\n"
        log_path.write_text(self._render_frontmatter(meta) + (existing_body.rstrip() + "\n" if existing_body.strip() else "") + entry, encoding="utf-8")
        logger.info(f"[MemoryStore] Appended daily log with YAML updates to {log_path}")
    
    def _read_scoped_daily_entries(self, *, log_path: Path, allowed_scopes: List[str], max_entries_per_day: int = 8) -> List[str]:
        content = log_path.read_text(encoding="utf-8")
        body = content
        header_match = re.match(r'^---\n.*?\n---\s*', content, flags=re.DOTALL)
        if header_match:
            body = content[header_match.end():]

        sections = re.split(r'\n(?=###\s+\d{2}:\d{2}\n)', body)
        matched: List[str] = []
        for section in sections:
            snippet = section.strip()
            if not snippet.startswith("### "):
                continue
            if allowed_scopes:
                if not any(f"effective_memory_scope: {scope}" in snippet for scope in allowed_scopes):
                    continue
            matched.append(snippet)
        return matched[-max_entries_per_day:]

    def get_recent_logs(self, days: int = 1, scope_chain: Optional[List[str]] = None) -> str:
        """获取最近 N 天与 scope 匹配的日志条目摘要。"""
        now = datetime.now()
        summaries = []
        allowed_scopes = [scope for scope in self._normalize_scope_chain(scope_chain=scope_chain) if self._is_valid_scope(scope)]
        
        for i in range(days):
            date_check = now - timedelta(days=i)
            log_path = self._get_daily_log_path(date_check)
            
            if not log_path.exists():
                continue

            matched_entries = self._read_scoped_daily_entries(log_path=log_path, allowed_scopes=allowed_scopes)
            if not matched_entries:
                continue

            entry = f"[{date_check.strftime('%Y-%m-%d')}] Path: {log_path}\n" + "\n\n".join(matched_entries)
            summaries.append(entry.strip())

        return "\n\n".join(summaries) if summaries else ""

    def _read_daily_frontmatter_summaries(self, *, log_path: Path) -> List[str]:
        content = log_path.read_text(encoding="utf-8")
        header_match = re.match(r'^---\n(.*?)\n---\s*', content, flags=re.DOTALL)
        if not header_match:
            return []

        header = header_match.group(1)
        lines = header.splitlines()
        summaries: List[str] = []
        in_summaries = False
        for line in lines:
            if line.startswith("summaries:"):
                in_summaries = True
                continue
            if in_summaries and re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:", line):
                break
            if not in_summaries:
                continue
            match = re.match(r'^\s*-\s*"?(.+?)"?\s*$', line)
            if match:
                value = match.group(1).strip()
                if value:
                    summaries.append(value)
        return summaries

    def get_prior_window_memory_summary(
        self,
        *,
        detailed_days: int = 1,
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        """
        读取详细日志窗口之前一天的紧凑摘要。
        仅输出路径与 YAML frontmatter summaries，不回退正文。
        """
        normalized_chain = self._normalize_scope_chain(scope_chain=scope_chain)
        allowed_scopes = [scope for scope in normalized_chain if self._is_valid_scope(scope)]
        summary_day = max(1, int(detailed_days))
        date_check = datetime.now() - timedelta(days=summary_day)
        log_path = self._get_daily_log_path(date_check)
        if not log_path.exists():
            return ""

        matched_entries = self._read_scoped_daily_entries(
            log_path=log_path,
            allowed_scopes=allowed_scopes,
            max_entries_per_day=1,
        )
        if not matched_entries:
            return ""

        lines = [f"[{date_check.strftime('%Y-%m-%d')}] Path: {log_path}"]
        summaries = self._read_daily_frontmatter_summaries(log_path=log_path)
        if summaries:
            lines.append("Summaries:")
            lines.extend(f"- {item}" for item in summaries)
        return "\n".join(lines).strip()
        
    def get_hierarchical_summaries(self, scope_chain: Optional[List[str]] = None) -> str:
        """读取年、月、周的最高层级摘要，仅在非特定项目上下文中启用以避免跨 scope 污染。"""
        normalized_chain = self._normalize_scope_chain(scope_chain=scope_chain)
        if any(scope.startswith(_SPECIFIC_SCOPE_PREFIXES) for scope in normalized_chain):
            return ""

        now = datetime.now()
        year = now.strftime("%Y")
        month_name = now.strftime("%m_%B").lower()
        week_num = now.strftime("%V")
        
        base_dir = MEMORY_ROOT / "daily" / year
        parts = []
        
        def _read_summary(path: Path) -> str:
            content = path.read_text(encoding='utf-8').strip()
            if len(content) > 500:
                return content[:500] + "\n...(truncated)"
            return content

        # Week
        week_summary = base_dir / month_name / f"week_{week_num}" / "summary.md"
        if week_summary.exists():
            parts.append(f"[Week {week_num} Summary] Path: {week_summary}\n{_read_summary(week_summary)}")
            
        # Month
        month_summary = base_dir / month_name / "summary.md"
        if month_summary.exists():
            parts.append(f"[Month {now.strftime('%m')} Summary] Path: {month_summary}\n{_read_summary(month_summary)}")
            
        # Year
        year_summary = base_dir / "summary.md"
        if year_summary.exists():
            parts.append(f"[Year {year} Summary] Path: {year_summary}\n{_read_summary(year_summary)}")
            
        return "\n\n".join(parts)
        
    def read_memory_summary(self, tier: str, date_str: str = None) -> str:
        """
        根据层级(day, week, month, year)与指定的日期字符串，查找相应的结构化记录(日志或摘要)。
        date_str 格式必须至少包含对应的粒度，例如 YYYY-MM-DD 或 YYYY-MM，未指定则用当前时间。
        """
        dt = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        year = dt.strftime("%Y")
        month_name = dt.strftime("%m_%B").lower()
        week_num = dt.strftime("%V")
        
        base_dir = MEMORY_ROOT / "daily" / year
        
        if tier == "day":
            log_path = self._get_daily_log_path(dt)
            if log_path.exists():
                return f"[{log_path.name}]\n{log_path.read_text(encoding='utf-8')}"
            return f"No daily log found for {dt.strftime('%Y-%m-%d')}."
            
        elif tier == "week":
            summary_path = base_dir / month_name / f"week_{week_num}" / "summary.md"
            if summary_path.exists():
                return f"[Week {week_num} Summary]\n{summary_path.read_text(encoding='utf-8')}"
            return f"No weekly summary found for week {week_num} of {year}."
            
        elif tier == "month":
            summary_path = base_dir / month_name / "summary.md"
            if summary_path.exists():
                return f"[Month {dt.strftime('%m')} Summary]\n{summary_path.read_text(encoding='utf-8')}"
            return f"No monthly summary found for {month_name}."
            
        elif tier == "year":
            summary_path = base_dir / "summary.md"
            if summary_path.exists():
                return f"[Year {year} Summary]\n{summary_path.read_text(encoding='utf-8')}"
            return f"No yearly summary found for {year}."
            
        return f"Unknown tier: {tier}"

    def save_periodic_summary(self, tier: str, content: str, dt: datetime = None):
        """保存更高层级的聚合摘要"""
        dt = dt or datetime.now()
        year = dt.strftime("%Y")
        month_name = dt.strftime("%m_%B").lower()
        week_num = dt.strftime("%V")
        
        base_dir = MEMORY_ROOT / "daily" / year
        
        if tier == "week":
            path = base_dir / month_name / f"week_{week_num}" / "summary.md"
        elif tier == "month":
            path = base_dir / month_name / "summary.md"
        elif tier == "year":
            path = base_dir / "summary.md"
        else:
            raise ValueError(f"Unknown summary tier: {tier}")
            
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(f"[MemoryStore] Saved {tier} memory summary to {path}")

    # ==========================================
    # Session 初始化（三层注入组装）
    # ==========================================
    
    def build_session_context(self, user_query: str, scope: str = "global", scope_chain: Optional[List[str]] = None) -> str:
        """
        构建渐进式 Session 上下文注入文本，结合历史概要、用户偏好、近期详细日志和紧凑前序摘要。
        Returns: 注入到 System Prompt 的文本
        """
        from core.storage import storage

        memory_config = storage.get_memory_config() or {}
        try:
            max_recent_days = max(1, min(int(memory_config.get("max_recent_days") or 1), 30))
        except (TypeError, ValueError):
            max_recent_days = 1
        try:
            max_context_tokens = max(256, min(int(memory_config.get("max_context_tokens") or 2000), 16000))
        except (TypeError, ValueError):
            max_context_tokens = 2000

        parts = []
        parts.append("[SYSTEM NOTE] The following information is dynamically provided by the internal Memory & RAG agent system. It contains user preferences, historical summaries, and recent activity logs.")

        # --- Layer 1: 用户画像 ---
        normalized_chain = self._normalize_scope_chain(scope=scope, scope_chain=scope_chain)
        prefs_text = self.format_preferences_for_injection(scope, scope_chain=normalized_chain)
        if prefs_text:
            parts.append(
                "[USER PROFILE]\n"
                f"Active scope: {scope}\n"
                f"Scope chain: {' -> '.join(normalized_chain)}\n"
                f"User preferences:\n{prefs_text}\n"
                "Use these preferences to personalize your responses.\n"
                "[/USER PROFILE]"
            )
            
        # --- Layer 2: 宏观层级记忆 (年/月/周) ---
        hierarchical = self.get_hierarchical_summaries(scope_chain=normalized_chain)
        if hierarchical:
            parts.append(
                "[HIERARCHICAL MEMORY SUMMARIES]\n"
                f"{hierarchical}\n"
                "[/HIERARCHICAL MEMORY SUMMARIES]"
            )
        
        # --- Layer 3: 近期上下文 (包含 YAML frontmatter) ---
        recent = self.get_recent_logs(days=max_recent_days, scope_chain=normalized_chain)
        if recent:
            parts.append(
                f"[RECENT ACTIVITY LOGS (Detailed Window: Last {max_recent_days} days)]\n"
                f"{recent}\n"
                "[/RECENT ACTIVITY LOGS]"
            )

        prior_summary = self.get_prior_window_memory_summary(
            detailed_days=max_recent_days,
            scope_chain=normalized_chain,
        )
        if prior_summary:
            parts.append(
                "[PRIOR MEMORY SUMMARY BEFORE DETAILED WINDOW]\n"
                f"{prior_summary}\n"
                "[/PRIOR MEMORY SUMMARY BEFORE DETAILED WINDOW]"
            )

        rendered_parts: List[str] = []
        remaining_tokens = max_context_tokens
        for part in parts:
            trimmed = self._trim_text_to_budget(part, remaining_tokens)
            if not trimmed:
                continue
            rendered_parts.append(trimmed)
            remaining_tokens -= max(1, len(trimmed) // 4)
            if remaining_tokens <= 0:
                break

        return "\n\n".join(rendered_parts)

    def _normalize_scope_chain(self, *, scope: str = "global", scope_chain: Optional[List[str]] = None) -> List[str]:
        candidate_chain = [item for item in (scope_chain or []) if item and self._is_valid_scope(item)]
        if not candidate_chain:
            candidate_chain = ["global"]
            if scope != "global" and self._is_valid_scope(scope):
                candidate_chain.append(scope)

        normalized: List[str] = []
        for item in candidate_chain:
            if item not in normalized:
                normalized.append(item)
        if "global" not in normalized:
            normalized.insert(0, "global")
        return normalized


# === 全局单例 ===
memory_store = MemoryStore()
