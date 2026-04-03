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

[app:chat]
# 聊天场景偏好 — Agent 判断为闲聊时使用
communication_style: 友好、热情

[app:coding]
# 编程场景偏好 — Agent 判断为技术任务时使用
communication_style: 专业、严谨
"""

# === 正则 ===
SCOPE_PATTERN = re.compile(r'^\[([^\]]+)\]$', re.MULTILINE)
KV_PATTERN = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$', re.MULTILINE)
_SPECIFIC_SCOPE_PREFIXES = ("project:", "workspace:", "workflow:", "channel:")
_ALLOWED_APP_SCOPES = {"app:chat", "app:coding", "app:writing"}


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
        for area in ["general", "chat", "coding", "writing", "projects", "workspaces", "channels", "workflows"]:
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
        if normalized in _ALLOWED_APP_SCOPES:
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
        
        优先级链: current_scope > app:coding > global
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
            "app:chat": "# 聊天场景偏好 — Agent 判断为闲聊时使用",
            "app:coding": "# 编程场景偏好 — Agent 判断为技术任务时使用",
            "app:writing": "# 写作场景偏好 — Agent 判断为内容创作时使用",
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
        elif scope.startswith("workspace:"):
            workspace_name = scope.split(":", 1)[1]
            path = areas_dir / "workspaces" / workspace_name / "items.json"
        elif scope.startswith("channel:"):
            channel_name = scope.split(":", 1)[1].replace(":", "__")
            path = areas_dir / "channels" / channel_name / "items.json"
        elif scope.startswith("workflow:"):
            workflow_name = scope.split(":", 1)[1]
            path = areas_dir / "workflows" / workflow_name / "items.json"
        elif scope.startswith("app:"):
            area_name = scope.split(":", 1)[1]  # chat, coding, writing
            path = areas_dir / area_name / "items.json"
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

    def unified_recall(self, query: str, limit: int = 5, scope: Optional[str] = None, scopes: Optional[List[str]] = None) -> List[Dict]:
        """
        统一的混合检索 (Hybrid Retrieval):
        结合 FTS5 (全文), ChromaDB (向量语义), 并附加知识图谱的一跳扩展。
        最后通过 Reranker 重排并去重返回 Top N。
        """
        from core.storage import storage

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
            retrieval_threshold = float(memory_config.get("retrieval_threshold") or 0.0)
        except (TypeError, ValueError):
            retrieval_threshold = 0.0
        retrieval_threshold = max(0.0, min(retrieval_threshold, 1.0))

        use_vector = recall_strategy in {"balanced", "semantic"}
        use_fts = bool(memory_config.get("fts_enabled", True)) and recall_strategy in {"balanced", "keyword"}
        use_graph = bool(memory_config.get("graph_enabled", True))

        combined_results = {} # id -> dict
        allowed_scopes = set(self._normalize_scope_chain(scope=scope or "global", scope_chain=scopes))
        if "global" not in allowed_scopes:
            allowed_scopes.add("global")
        
        # 1. Vector Search
        if use_vector:
            try:
                from core.vector_store import get_vector_store
                from core.knowledge_db import knowledge_db

                vs = get_vector_store()
                # We fetch more internally to have a good pool for reranking
                v_results = vs.similarity_search_with_rerank(query, top_k=effective_limit * 2, fetch_k=20)
                for r in v_results:
                    fact_id = r["id"]
                    parent_id = r.get("metadata", {}).get("parent_id")
                    final_fact = r["text"]
                    final_id = fact_id
                    
                    # Phase 25.2: Parent-Child Fetch (Get broad context from DB)
                    if parent_id:
                        with knowledge_db._conn() as conn:
                            parent_row = conn.execute("SELECT fact FROM knowledge WHERE id = ?", (parent_id,)).fetchone()
                            if parent_row:
                                final_fact = parent_row[0]
                                final_id = parent_id
                    
                    item_scope = r.get("metadata", {}).get("scope", "global")
                    if item_scope not in allowed_scopes:
                        continue
                    combined_results[final_id] = {
                        "id": final_id,
                        "fact": final_fact,
                        "category": r.get("metadata", {}).get("category", "general"),
                        "scope": item_scope,
                        "source": "vector"
                    }
            except Exception as e:
                logger.warning(f"[MemoryStore] Vector search error in unified_recall: {e}")
            
        # 2. FTS5 Search
        if use_fts:
            try:
                from core.knowledge_db import knowledge_db

                f_results = knowledge_db.fts_search(query, limit=effective_limit * 4)
                for r in f_results:
                    fact_id = r.get("id")
                    final_fact = r.get("fact", "")
                    final_id = fact_id
                    if r.get("scope", "global") not in allowed_scopes:
                        continue
                    
                    # FTS also returns children; we elevate to parent
                    with knowledge_db._conn() as conn:
                        p_id_row = conn.execute("SELECT parent_id FROM knowledge WHERE id = ?", (fact_id,)).fetchone()
                        if p_id_row and p_id_row[0]:
                            parent_row = conn.execute("SELECT fact FROM knowledge WHERE id = ?", (p_id_row[0],)).fetchone()
                            if parent_row:
                                final_fact = parent_row[0]
                                final_id = p_id_row[0]
                    
                    if final_id and final_id not in combined_results:
                        combined_results[final_id] = {
                            "id": final_id,
                            "fact": final_fact,
                            "category": r.get("category", "general"),
                            "scope": r.get("scope", "global"),
                            "source": "fts5"
                        }
            except Exception as e:
                logger.warning(f"[MemoryStore] FTS5 search error in unified_recall: {e}")
            
        # 3. Graph Expansion (Phase 25.1: LLM/Regex Entity Extraction + Multi-hop)
        if use_graph:
            try:
                from core.memory_router import MemoryRouter
                from core.knowledge_db import knowledge_db
                import re
                
                # Fast Regex Fallback: extract alphabetic words or Chinese words (length > 1) to act as loose entities
                words = set([w.lower() for w in re.findall(r'\b[a-zA-Z]{2,}\b', query)])
                zh_words = set(re.findall(r'[\u4e00-\u9fa5]{2,}', query))
                
                # Filter out very common stop words if needed, but loose regex means we accept most
                potential_entities = list(words.union(zh_words))
                
                # Attempt LLM extraction for precision
                try:
                    router = MemoryRouter()
                    llm = router.get_extractor_llm()
                    prompt = (
                        "Extract 1 to 3 core subject entities from the following query to assist knowledge graph retrieval. "
                        "Output ONLY a comma-separated list of entities, no markdown, no quotes (e.g. Next.js, database, Python). If no obvious entities, output 'NONE'.\n\n"
                        f"Query: {query}"
                    )
                    from langchain_core.messages import SystemMessage
                    response = llm.invoke([SystemMessage(content=prompt)], config={"callbacks": []})
                    content = response.content.strip().replace("'", "").replace('"', '')
                    if "none" not in content.lower() and "</think>" not in content.lower():
                        llm_entities = [e.strip().lower() for e in content.split(',') if e.strip()]
                        if llm_entities:
                            potential_entities = llm_entities # Override loose regex with precise LLM output
                    elif "</think>" in content.lower():
                         # Handle deepseek reasoner output
                         actual_content = content.split("</think>")[-1].strip()
                         if "none" not in actual_content.lower():
                             llm_entities = [e.strip().lower() for e in actual_content.split(',') if e.strip()]
                             if llm_entities:
                                 potential_entities = llm_entities
                except Exception as e_llm:
                    logger.debug(f"[MemoryStore] Fast LLM entity extraction failed or timed out, using fallback regex: {e_llm}")

                graph_facts = []
                extracted_relations = set()
                
                for entity in potential_entities:
                    # Perform 2-hop graph query
                    relations = knowledge_db.multi_hop_query(entity, hops=2)
                    for rel in relations:
                        rel_str = f"{rel['subject']} {rel['predicate']} {rel['object']}"
                        if rel_str not in extracted_relations:
                            extracted_relations.add(rel_str)
                            graph_facts.append(f"[Graph Context] {rel_str}")
                
                if graph_facts:
                    logger.info(f"[MemoryStore] Graph Expansion: extracted entities {potential_entities}, fetched {len(graph_facts)} relations.")
                    # Inject graph relations into the reranking pool as virtual documents
                    for i, gf in enumerate(graph_facts):
                        virtual_id = f"graph_fact_{i}"
                        combined_results[virtual_id] = {
                            "id": virtual_id,
                            "fact": gf,
                            "category": "graph_context",
                            "scope": "global",
                            "source": "graph"
                        }
                    
            except Exception as e:
                logger.warning(f"[MemoryStore] Graph expansion pipeline failed in unified_recall: {e}")

        if not combined_results:
            return []
            
        docs_to_rerank = []
        ids_order = []
        for fact_id, data in combined_results.items():
            docs_to_rerank.append(data["fact"])
            ids_order.append(fact_id)
            
        # 4. Global Reranking
        try:
            from core.memory_router import MemoryRouter
            router = MemoryRouter()
            reranker = router.get_reranker_model()
            
            # Re-rank the combined pool
            ranked = reranker.rerank(query, docs_to_rerank, top_k=effective_limit)
            
            final_results = []
            for r in ranked:
                idx = r["index"]
                fact_id = ids_order[idx]
                item = combined_results[fact_id]
                item["relevance_score"] = r.get("relevance_score", 0.0)
                final_results.append(item)

            if retrieval_threshold > 0:
                final_results = [
                    item for item in final_results
                    if float(item.get("relevance_score") or 0.0) >= retrieval_threshold
                ]

            return final_results[:effective_limit]
            
        except Exception as e:
            logger.warning(f"[MemoryStore] Unified recall reranking failed, returning fallback: {e}")
            # Fallback to taking items directly
            return list(combined_results.values())[:effective_limit]
            
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
    
    def append_daily_log_with_yaml(self, content: str, session_summary: str, session_tags: List[str]):
        """追加日志并维护含有历史摘要与 tag 的 YAML frontmatter 实现渐进式加载"""
        now = datetime.now()
        log_path = self._get_daily_log_path(now)
        
        # 追加的文本
        time_str = now.strftime("%H:%M")
        entry = f"\n### {time_str}\n{content}\n"
        
        if not log_path.exists() or log_path.stat().st_size == 0:
            # 创建新文件及初始 YAML 头
            header = f"---\ntype: daily_log\ndate: \"{now.strftime('%Y-%m-%d')}\"\ntags:\n"
            for t in session_tags:
                header += f"  - {t}\n"
            header += "summaries:\n"
            header += f"  - \"{session_summary.replace('\"', '')}\"\n"
            header += "---\n\n"
            log_path.write_text(header + entry, encoding="utf-8")
        else:
            # 仅简单向文件末尾追加，目前为避免破坏复杂的多会话日志，简易实现为仅追加文本。
            # 严格按照“YAML追加”则应读取解析替换。这里为稳定暂退化为普通追加。
            # 如果需要完整的 frontmatter 修改，可读取整个文本并在 "---" 内替换。
            text = log_path.read_text(encoding="utf-8")
            if text.startswith("---"):
                # 简单正则表达式替换以插入 summary 和 tags
                if "summaries:" in text:
                    text = text.replace("summaries:\n", f"summaries:\n  - \"{session_summary.replace('\"', '')}\"\n", 1)
                else:
                    text = text.replace("---\n\n", f"summaries:\n  - \"{session_summary.replace('\"', '')}\"\n---\n\n", 1)
                for t in session_tags:
                    if f"  - {t}\n" not in text:
                        text = text.replace("tags:\n", f"tags:\n  - {t}\n", 1)
                log_path.write_text(text + entry, encoding="utf-8")
            else:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                    
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
                if not any(f"(scope: {scope})" in snippet for scope in allowed_scopes):
                    continue
            matched.append(snippet)
        return matched[-max_entries_per_day:]

    def get_recent_logs(self, days: int = 2, scope_chain: Optional[List[str]] = None) -> str:
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
        构建渐进式 Session 上下文注入文本，结合历史概要、用户偏好和两日的日志YAML头部。
        Returns: 注入到 System Prompt 的文本
        """
        from core.storage import storage

        memory_config = storage.get_memory_config() or {}
        try:
            max_recent_days = max(1, min(int(memory_config.get("max_recent_days") or 2), 30))
        except (TypeError, ValueError):
            max_recent_days = 2
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
                f"[RECENT ACTIVITY LOGS (Last {max_recent_days} days)]\n"
                f"{recent}\n"
                "[/RECENT ACTIVITY LOGS]"
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
            if scope.startswith(("project:", "workspace:", "workflow:", "channel:")):
                candidate_chain.append("app:coding")
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
