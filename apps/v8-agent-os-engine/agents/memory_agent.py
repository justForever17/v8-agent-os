"""
Memory Agent — 系统指令驱动的专业记忆维护 Agent

设计原则:
- 不接受用户指令，只接受系统指令
- 每次会话结束后，系统触发执行:
  1. 轻量化总结对话
  2. 根据总结进行 fts_search 预检索历史知识
  3. LLM 基于对话和历史知识提取 Structural Data (Summary, Tags, Facts, Preferences, Entities, Relations)
  4. 写入记忆库、知识图谱、更新 Daily Log 的 YAML 头并写入日志

与 Supervisor 的分工:
- Supervisor: 轻量 CRUD (mem_save/search/delete), 用户显式触发
- Memory Agent: 全面兜底提取, 系统自动触发, 确保无遗漏
"""

from pydantic import BaseModel, Field
from typing import Any, List, Optional, Dict
from datetime import datetime
from langchain_core.messages import SystemMessage, HumanMessage
import logging
import json
import hashlib
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException

from core.database import db
from core.storage import storage
from core.memory_router import MemoryRouter
from core.knowledge_db import knowledge_db
from core.scope_detector import detect_scope
from core.audit_logger import audit_logger
from runtimes.memory.runtime import memory_runtime
from runtimes.memory.prompts import (
    render_memory_extraction_prompt,
    render_periodic_summary_prompt,
)
from runtimes.memory.scope_resolution import (
    build_scope_chain,
    scope_resolution_service,
    session_scope_binding_service,
)

logger = logging.getLogger(__name__)

# === LLM 提取结果模型 ===

class PreferenceExtraction(BaseModel):
    scope: str = Field(description="Scope of this preference (e.g., 'global', 'app:coding', 'project:v8-agent-os')")
    key: str = Field(description="A short key name for this preference (e.g., 'preferred_framework', 'code_style')")
    value: str = Field(description="The actual value or content of the preference")
    importance: int = Field(default=50, description="Importance score from 0 to 100")
    confidence: float = Field(default=0.5, description="Confidence score from 0.0 to 1.0")
    durability: str = Field(default="stable", description="Durability: stable | operational | transient")
    target_store: str = Field(default="preference", description="Target store: preference | daily_log | skip")

class KnowledgeExtraction(BaseModel):
    fact: str = Field(description="A concise, atomic factual knowledge about the user's project, business, or environment")
    category: str = Field(description="Category of the fact (e.g., 'Architecture', 'Business Logic')")
    scope: str = Field(description="Scope of the knowledge")
    overwrite_id: str = Field(default="", description="If this updates an existing fact from the provided context, put its exact fact_id here. Otherwise leave empty.")
    importance: int = Field(default=50, description="Importance score from 0 to 100")
    confidence: float = Field(default=0.5, description="Confidence score from 0.0 to 1.0")
    durability: str = Field(default="operational", description="Durability: stable | operational | transient")
    target_store: str = Field(default="knowledge", description="Target store: knowledge | daily_log | skip")

class EntityExtraction(BaseModel):
    name: str = Field(description="Name of the entity, lowercase and concise (e.g., 'next.js', 'react')")
    type: str = Field(description="Type of entity (technology, concept, project, user, tool, service, etc.)")

class RelationExtraction(BaseModel):
    subject: str = Field(description="Subject entity name")
    predicate: str = Field(description="Relation predicate (e.g., USES, DEPENDS_ON, PREFERS, IMPLEMENTS)")
    object: str = Field(description="Object entity name")

class MemoryExtractionResult(BaseModel):
    summary: str = Field(description="A concise, one-sentence summary of the core outcome or discussed topic of the session")
    tags: List[str] = Field(description="A list of 3-5 tags describing the session")
    preferences: List[PreferenceExtraction] = Field(default_factory=list, description="Extracted user preferences")
    knowledge: List[KnowledgeExtraction] = Field(default_factory=list, description="Extracted non-transient semantic facts")
    entities: List[EntityExtraction] = Field(default_factory=list, description="Extracted entities for the knowledge graph")
    relations: List[RelationExtraction] = Field(default_factory=list, description="Extracted relationships between entities")

# === 专业工具函数 ===

def _get_background_llm():
    """获取后台 LLM（使用 memory 专用配置）"""
    router = MemoryRouter()
    return router.get_extractor_llm()

def _generate_quick_summary(chat_text: str) -> str:
    """快速生成会话一句话摘要，用于预检索历史知识"""
    try:
        llm = _get_background_llm()
        system_prompt = "You are a summarizing assistant. Read the chat log and output exactly ONE short sentence summarizing the core technical topic or outcome of the conversation. Output only the sentence, nothing else."
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Chat Log:\n\n{chat_text[:2000]}...") # truncate for speed/cost
        ])
        return response.content.strip()
    except Exception as e:
        logger.warning(f"[MemoryAgent] Quick summary generation failed: {e}")
        return "latest conversation"

def _get_extraction_prompt(format_instructions: str) -> str:
    return render_memory_extraction_prompt(format_instructions)


def _load_memory_policy() -> Dict[str, Any]:
    memory_config = storage.get_memory_config() or {}

    def _as_int(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(memory_config.get(key) if key in memory_config else default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _as_float(key: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(memory_config.get(key) if key in memory_config else default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    return {
        "extraction_enabled": bool(memory_config.get("extraction_enabled", True)),
        "preference_importance_threshold": _as_int("preference_importance_threshold", 70, 0, 100),
        "preference_confidence_threshold": _as_float("preference_confidence_threshold", 0.75, 0.0, 1.0),
        "knowledge_importance_threshold": _as_int("knowledge_importance_threshold", 60, 0, 100),
        "knowledge_confidence_threshold": _as_float("knowledge_confidence_threshold", 0.70, 0.0, 1.0),
    }


def _normalize_target_store(value: str, *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"preference", "knowledge", "daily_log", "skip"}:
        return normalized
    return default


def _normalize_durability(value: str, *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"stable", "operational", "transient"}:
        return normalized
    return default


def _message_hash(messages: List[Dict[str, Any]]) -> str:
    payload = [
        {
            "id": msg.get("id"),
            "role": msg.get("role"),
            "content": msg.get("content"),
            "created_at": msg.get("created_at"),
        }
        for msg in messages
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _resolve_incremental_messages(
    *,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, Any] | None, str]:
    state = memory_runtime.get_extraction_state(session_id)
    if not state:
        return messages, None, "full_scan"

    last_count = int(state.get("last_processed_message_count") or 0)
    if last_count < 0 or last_count > len(messages):
        return messages, state, "checkpoint_reset"

    new_messages = messages[last_count:]
    if not new_messages:
        return [], state, "no_new_messages"

    last_hash = str(state.get("last_content_hash") or "").strip()
    current_incremental_hash = _message_hash(new_messages)
    if last_hash and current_incremental_hash == last_hash:
        return [], state, "duplicate_increment"

    return new_messages, state, "incremental"

def _extract_with_llm(
    chat_text: str,
    context_text: str,
    *,
    resolved_scope: str,
    scope_chain: Optional[List[str]] = None,
) -> Optional[MemoryExtractionResult]:
    """
    [专业工具] 使用 LLM 和 PydanticOutputParser 从对话中提取结构化知识。
    同时注入预检索到的 Historical Context，以便更新/防重。
    兼容 DeepSeek-Reasoner (R1) 和 Chat 等不支持原生 structured output (json_schema) 的模型。
    """
    try:
        llm = _get_background_llm()
        parser = PydanticOutputParser(pydantic_object=MemoryExtractionResult)
        format_instructions = parser.get_format_instructions()
    except Exception as e:
        logger.error(f"[MemoryAgent] No valid extractor configured: {e}")
        return None
    
    system_prompt = _get_extraction_prompt(format_instructions)
    
    try:
        raw_response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Runtime Scope Context:\n"
                    f"- resolved_scope: {resolved_scope}\n"
                    f"- scope_chain: {', '.join(scope_chain or [resolved_scope])}\n\n"
                    f"Historical Context (Prior Knowledge):\n{context_text}\n\n"
                    f"Chat Log:\n{chat_text}"
                )
            )
        ])
        str_content = raw_response.content
        
        # 兼容 deepseek-reasoner 返回可能包含 <think> 标签的思维链
        if "<think>" in str_content and "</think>" in str_content:
            str_content = str_content.split("</think>")[-1].strip()
            
        # 很多时候大模型即使被警告，还是会包在 ```json...``` 里
        import re
        json_match = re.search(r"```(?:json)?(.*?)```", str_content, re.DOTALL)
        if json_match:
            str_content = json_match.group(1).strip()
            
        # 针对无限复读循环做暴力截断 (如超过 2000 字符且没有结束的 }，则修复)
        if len(str_content) > 4000:
            logger.warning("[MemoryAgent] LLM output unusually long, possible repetition bug. Truncating.")
            str_content = str_content[:4000]
            
        try:
            parsed_result = parser.invoke(str_content)
            return parsed_result
        except OutputParserException as e:
            logger.warning(f"[MemoryAgent] Output parsing failed: {e}. Attempting auto-fix...")
            from langchain.output_parsers import OutputFixingParser
            fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=llm)
            return fixing_parser.parse(str_content)
            
    except Exception as e:
        logger.error(f"[MemoryAgent] LLM extraction error: {e}")
        return None

def _build_historical_context(*, quick_summary: str, scope_chain: List[str]) -> str:
    context_sections: List[str] = []
    try:
        past_knowledge = memory_runtime.query_knowledge(query=quick_summary, scopes=scope_chain, limit=5)
        if past_knowledge:
            lines = ["Historical Knowledge:"]
            for pk in past_knowledge:
                lines.append(f"- [id: {pk['id']}] (scope: {pk.get('scope', 'global')}) {pk['fact']}")
            context_sections.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"[MemoryAgent] FTS5 search failed during pre-retrieval: {e}")

    try:
        historical_prefs = memory_runtime.load_preferences(scope=scope_chain[-1], scope_chain=scope_chain)
        if historical_prefs:
            lines = ["Existing Preferences:"]
            for key, value in historical_prefs.items():
                lines.append(f"- {key}: {value}")
            context_sections.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"[MemoryAgent] Failed to load historical preferences: {e}")

    return "\n\n".join(context_sections) if context_sections else "No prior knowledge retrieved."


def _should_store_preference(pref: PreferenceExtraction, policy: Dict[str, Any]) -> bool:
    if _normalize_target_store(pref.target_store, default="preference") != "preference":
        return False
    if _normalize_durability(pref.durability, default="stable") != "stable":
        return False
    return (
        int(pref.importance or 0) >= int(policy["preference_importance_threshold"])
        and float(pref.confidence or 0.0) >= float(policy["preference_confidence_threshold"])
    )


def _should_store_knowledge(fact: KnowledgeExtraction, policy: Dict[str, Any]) -> bool:
    if _normalize_target_store(fact.target_store, default="knowledge") != "knowledge":
        return False
    if _normalize_durability(fact.durability, default="operational") == "transient":
        return False
    return (
        int(fact.importance or 0) >= int(policy["knowledge_importance_threshold"])
        and float(fact.confidence or 0.0) >= float(policy["knowledge_confidence_threshold"])
    )


def _store_preferences(result: MemoryExtractionResult, policy: Dict[str, Any]):
    """[专业工具] 将偏好存入 MEMORY.md（按评分与 store 约束过滤）"""
    stored = 0
    for pref in result.preferences:
        if not _should_store_preference(pref, policy):
            continue
        key = pref.key.strip()
        if not key:
            key = "preference"
        try:
            memory_runtime.upsert_preference(key=key, value=pref.value, scope=pref.scope)
            stored += 1
            logger.info(f"[MemoryAgent] Preference → MEMORY.md [{pref.scope}] {key} = {pref.value}")
        except ValueError as exc:
            logger.warning(f"[MemoryAgent] Preference skipped due to invalid scope: {exc}")
    return stored

def _store_knowledge(result: MemoryExtractionResult, session_id: str, policy: Dict[str, Any]):
    """[专业工具] 将知识存入分区 JSON + ChromaDB，处理覆盖与新增"""
    stored = 0
    for fact in result.knowledge:
        if not _should_store_knowledge(fact, policy):
            continue
        if fact.overwrite_id:
            # 更新/覆盖旧知识
            try:
                success = memory_runtime.update_knowledge(
                    fact_id=fact.overwrite_id,
                    new_fact=fact.fact,
                    category=fact.category,
                    scope=fact.scope
                )
                if success:
                    stored += 1
                    logger.info(f"[MemoryAgent] Overwrote Knowledge: {fact.overwrite_id} -> {fact.fact}")
                else:
                    logger.warning(f"[MemoryAgent] Overwrite ID {fact.overwrite_id} not found, adding as new.")
                    memory_runtime.add_knowledge(
                        fact=fact.fact, category=fact.category, scope=fact.scope, source_session=session_id
                    )
                    stored += 1
            except ValueError as exc:
                logger.warning(f"[MemoryAgent] Knowledge skipped due to invalid scope: {exc}")
        else:
            # 正常新增
            try:
                memory_runtime.add_knowledge(
                    fact=fact.fact, category=fact.category, scope=fact.scope, source_session=session_id
                )
                stored += 1
            except ValueError as exc:
                logger.warning(f"[MemoryAgent] Knowledge skipped due to invalid scope: {exc}")
    return stored

def _append_session_log(result: MemoryExtractionResult, scope: str, session_id: str):
    """[专业工具] 记录到时序日志，同时自动维护 YAML 头以实现渐进式加载支持摘要和 Tag"""
    content_lines = []
    if result.knowledge:
        content_lines.append("**Extracted Knowledge:**")
        for f in result.knowledge:
            op = "[UPDATE]" if f.overwrite_id else "[NEW]"
            content_lines.append(f"- {op} {f.fact}")
    if result.preferences:
        content_lines.append("**Extracted Preferences:**")
        for p in result.preferences:
            content_lines.append(f"- [{p.scope}] {p.key}: {p.value}")
    
    if not content_lines:
        content_lines.append("No new long-term items extracted.")
        
    full_content = (
        f"Session `{session_id[:8]}` (scope: {scope})\n"
        f"**Summary**: {result.summary}\n\n" + "\n".join(content_lines)
    )
    
    # 追加到日志（memory_store.append_daily_log_with_yaml 将负责处理 YAML Merge）
    if hasattr(memory_runtime, 'append_daily_log_with_yaml'):
        memory_runtime.append_daily_log_with_yaml(
            content=full_content,
            session_summary=result.summary,
            session_tags=result.tags
        )
    else:
        # 兼容回退
        memory_runtime.append_daily_log(
            content=full_content,
            tags=result.tags
        )


def _align_extraction_scopes(result: MemoryExtractionResult, resolved_scope: str):
    specific_prefixes = ("project:", "workspace:", "workflow:", "channel:")
    allowed_app_scopes = {"app:chat", "app:coding", "app:writing"}

    def _coerce_scope(value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            return resolved_scope
        if normalized == "global":
            return normalized
        if normalized.startswith("app:") and normalized not in allowed_app_scopes:
            return resolved_scope
        if normalized.startswith(specific_prefixes) and normalized != resolved_scope:
            return resolved_scope
        return normalized

    for pref in result.preferences:
        pref.scope = _coerce_scope(pref.scope)
    for fact in result.knowledge:
        fact.scope = _coerce_scope(fact.scope)

def _build_knowledge_graph(result: MemoryExtractionResult):
    """
    [专业工具] 从提取结果中提取图谱实体与关系。
    通过 SQLite INSERT OR IGNORE 自动去重。
    """
    relations_added = 0
    
    # 插入 Entities
    for entity in result.entities:
        knowledge_db.add_entity(entity.name.lower(), entity.type)
        
    # 插入 Relations
    for rel in result.relations:
        subj = rel.subject.strip().lower()
        pred = rel.predicate.strip().upper()
        obj = rel.object.strip().lower()
        if subj and pred and obj:
            knowledge_db.add_relation(subj, pred, obj)
            relations_added += 1
            
    if relations_added:
        logger.info(f"[MemoryAgent] Built {relations_added} graph relations via LLM structured extraction.")

def _run_incremental_index():
    """[专业工具] 执行增量索引刷新"""
    try:
        indexed = knowledge_db.run_incremental_index()
        if indexed:
            logger.info(f"[MemoryAgent] Incremental index: {indexed} items refreshed.")
    except Exception as e:
        logger.warning(f"[MemoryAgent] Incremental indexing failed: {e}")


def _emit_memory_event(
    run_handle: Any | None,
    topic: str,
    payload: Dict[str, Any],
):
    if run_handle is None:
        return
    try:
        run_handle.emit(topic, payload)
    except Exception as exc:
        logger.debug(f"[MemoryAgent] Failed to emit runtime event {topic}: {exc}")

# === 系统指令入口 ===

def _get_log_source(trigger_source: str) -> str:
    """Helper to convert trigger_source to system_audit_log source_type."""
    if not trigger_source:
        return "SYSTEM"
    ts = trigger_source.lower()
    if ts.startswith("hook"):
        return "HOOK"
    if ts.startswith("cron"):
        return "CRON"
    return "SYSTEM"

def analyze_session_memory(
    session_id: str,
    trigger_source: str = "SYSTEM",
    *,
    run_handle: Any | None = None,
    parent_run_id: str | None = None,
):
    """
    [系统指令] 会话结束后由系统自动触发后台提取任务。
    
    工作流：
    1. 获取原始日志
    2. 生成极简 Summary
    3. FTS5 检索历史上下文（防重与覆盖基准）
    4. 执行 Structured LLM Extraction
    5. 解析并分别落库（Preferences, Knowledge, DB Graph, Daily Log, Vectors, FTS5 Update）
    """
    source_type = _get_log_source(trigger_source)
    policy = _load_memory_policy()
    if not policy["extraction_enabled"]:
        logger.info(f"[MemoryAgent] Extraction disabled by config, skipping session {session_id}.")
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": "extraction_disabled",
                "parent_run_id": parent_run_id,
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "extraction_disabled",
            "parent_run_id": parent_run_id,
        }

    # 1. 获取会话日志
    messages = db.get_messages(session_id)
    if not messages:
        logger.info(f"[MemoryAgent] No messages for session {session_id}, skipping.")
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": "no_messages",
                "parent_run_id": parent_run_id,
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "no_messages",
            "parent_run_id": parent_run_id,
        }
    
    incremental_messages, extraction_state, extraction_mode = _resolve_incremental_messages(
        session_id=session_id,
        messages=messages,
    )
    if not incremental_messages:
        logger.info(f"[MemoryAgent] No incremental messages for session {session_id}, skipping extraction.")
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": extraction_mode,
                "parent_run_id": parent_run_id,
                "message_count": len(messages),
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": extraction_mode,
            "parent_run_id": parent_run_id,
            "message_count": len(messages),
        }

    chat_history_text = ""
    for msg in incremental_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            chat_history_text += f"{role.upper()}: {content}\n"
    
    if len(chat_history_text.strip()) < 50:
        logger.info(f"[MemoryAgent] Session too short, skipping extraction.")
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="SKIPPED",
            details=f"Session {session_id[:8]} too short (< 50 chars)"
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": "session_too_short",
                "content_length": len(chat_history_text.strip()),
                "parent_run_id": parent_run_id,
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "session_too_short",
            "content_length": len(chat_history_text.strip()),
            "parent_run_id": parent_run_id,
        }
    
    logger.info(f"[MemoryAgent] === System Command: Analyze Session {session_id[:8]} ===")
    _emit_memory_event(
        run_handle,
        "memory.session_extraction.started",
            {
                "session_id": session_id,
                "trigger_source": trigger_source,
                "parent_run_id": parent_run_id,
                "message_count": len(incremental_messages),
                "extraction_mode": extraction_mode,
                "previous_checkpoint": extraction_state or {},
            },
        )
    
    binding = session_scope_binding_service.get_binding(session_id)
    if binding and binding.status == "active":
        scope = binding.resolved_scope
        scope_chain = build_scope_chain(
            resolved_scope=binding.resolved_scope,
            detected_app_scope=detect_scope(chat_history_text),
            channel_type=binding.channel_type,
            channel_remote_id=binding.channel_remote_id,
            workspace_id=binding.workspace_id,
            project_id=binding.project_id,
            workflow_id=binding.workflow_id,
        )
    else:
        resolved = scope_resolution_service.resolve(
            session_id=session_id,
            conversation_id=session_id,
            user_query=chat_history_text,
            scope_mode="mixed",
        )
        binding = resolved.binding
        scope = binding.resolved_scope
        scope_chain = resolved.scope_chain
    _emit_memory_event(
        run_handle,
        "memory.scope.resolved",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "scope_chain": scope_chain,
            "project_id": binding.project_id,
            "workspace_id": binding.workspace_id,
            "parent_run_id": parent_run_id,
        },
    )

    # 2. 生成极简 Summary
    quick_summary = _generate_quick_summary(chat_history_text)
    logger.info(f"[MemoryAgent] Quick summary for retrieval: {quick_summary}")
    _emit_memory_event(
        run_handle,
        "memory.quick_summary.generated",
        {
            "session_id": session_id,
            "quick_summary": quick_summary,
            "resolved_scope": scope,
        },
    )
    
    # 3. FTS5 检索历史上下文（获取 Top 5 相关条目用于查重和更新覆盖）
    context_text = _build_historical_context(quick_summary=quick_summary, scope_chain=scope_chain)
    past_knowledge = []
    try:
        past_knowledge = memory_runtime.query_knowledge(query=quick_summary, scopes=scope_chain, limit=5)
    except Exception:
        past_knowledge = []
    logger.info(f"[MemoryAgent] Pre-retrieval found {len(past_knowledge)} related past facts.")
    _emit_memory_event(
        run_handle,
        "memory.context.recalled",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "query": quick_summary,
            "result_count": len(past_knowledge) if "past_knowledge" in locals() else 0,
        },
    )
    
    # 4. LLM 结构化提取
    try:
        result = _extract_with_llm(
            chat_history_text,
            context_text,
            resolved_scope=scope,
            scope_chain=scope_chain,
        )
    except Exception as e:
        logger.warning(f"[MemoryAgent] LLM extraction error: {e}")
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="FAILED",
            details=f"Error in LLM extraction ({session_id[:8]}): {str(e)}"
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.failed",
            {
                "session_id": session_id,
                "reason": str(e),
                "resolved_scope": scope,
            },
        )
        return {
            "status": "failed",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": str(e),
            "resolved_scope": scope,
        }
        
    if result is None:
        logger.warning(f"[MemoryAgent] LLM failed to return valid extraction.")
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="FAILED",
            details=f"LLM returned invalid/empty extraction for session {session_id[:8]}"
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.failed",
            {
                "session_id": session_id,
                "reason": "llm_returned_invalid_extraction",
                "resolved_scope": scope,
            },
        )
        return {
            "status": "failed",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "llm_returned_invalid_extraction",
            "resolved_scope": scope,
        }

    _align_extraction_scopes(result, scope)
    _emit_memory_event(
        run_handle,
        "memory.extraction.completed",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "summary": result.summary,
            "tags": result.tags,
            "preference_count": len(result.preferences),
            "knowledge_count": len(result.knowledge),
            "entity_count": len(result.entities),
            "relation_count": len(result.relations),
            "extraction_mode": extraction_mode,
        },
    )
       
    # 5. 分别落库
    stored_preferences = _store_preferences(result, policy)
    _emit_memory_event(
        run_handle,
        "memory.preferences.updated",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "count": stored_preferences,
        },
    )
    stored_knowledge = _store_knowledge(result, session_id, policy)
    _emit_memory_event(
        run_handle,
        "memory.knowledge.upserted",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "count": stored_knowledge,
        },
    )
    _build_knowledge_graph(result)
    _emit_memory_event(
        run_handle,
        "memory.graph.updated",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "entity_count": len(result.entities),
            "relation_count": len(result.relations),
        },
    )
    _append_session_log(result, scope, session_id)
    _emit_memory_event(
        run_handle,
        "memory.daily_log.appended",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "summary": result.summary,
            "tags": result.tags,
        },
    )
    
    # 增量 FTS5 刷新
    _run_incremental_index()
    current_hash = _message_hash(incremental_messages)
    memory_runtime.save_extraction_state(
        session_id=session_id,
        last_processed_message_id=messages[-1].get("id"),
        last_processed_message_count=len(messages),
        last_content_hash=current_hash,
        last_run_id=getattr(run_handle, "run_id", None),
        last_processed_at=datetime.now().isoformat(),
    )
    _emit_memory_event(
        run_handle,
        "memory.session_extraction.finished",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "summary": result.summary,
            "tags": result.tags,
            "preference_count": stored_preferences,
            "knowledge_count": stored_knowledge,
            "entity_count": len(result.entities),
            "relation_count": len(result.relations),
            "status": "completed",
            "extraction_mode": extraction_mode,
        },
    )
    
    logger.info(
        f"[MemoryAgent] === Complete: "
        f"{len(result.knowledge)} facts, "
        f"{len(result.preferences)} prefs, "
        f"{len(result.relations)} relations extracted. ==="
    )
    
    audit_logger.log(
        source_type=source_type,
        action=f"Memory Agent: Session Extraction",
        status="SUCCESS",
        details=f"Session {session_id[:8]} => {len(result.knowledge)} facts, {len(result.preferences)} prefs, {len(result.relations)} relations."
    )
    return {
        "status": "completed",
        "task_kind": "session_extraction",
        "session_id": session_id,
        "resolved_scope": scope,
        "summary": result.summary,
        "tags": result.tags,
        "preference_count": stored_preferences,
        "knowledge_count": stored_knowledge,
        "entity_count": len(result.entities),
        "relation_count": len(result.relations),
        "parent_run_id": parent_run_id,
        "extraction_mode": extraction_mode,
    }

async def generate_periodic_summary(
    tier: str,
    target_date: datetime = None,
    trigger_source: str = "SYSTEM",
    *,
    run_handle: Any | None = None,
):
    """
    触发生成按周、月、年的高信噪比记忆摘要
    支持由系统 cron 定时触发或 Hooks 自动分发机制调度。
    """
    dt = target_date or datetime.now()
    source_type = _get_log_source(trigger_source)
    logger.info(f"[MemoryAgent] Generating periodic summary for tier={tier}")
    _emit_memory_event(
        run_handle,
        "memory.periodic_summary.started",
        {
            "tier": tier,
            "target_date": dt.isoformat(),
            "trigger_source": trigger_source,
        },
    )
    
    content = ""
    if tier == "week":
        content = memory_runtime.get_recent_logs(days=7, scope_chain=["global"])
    elif tier == "month":
        content = memory_runtime.get_recent_logs(days=30, scope_chain=["global"])
    elif tier == "year":
        content = memory_runtime.get_recent_logs(days=365, scope_chain=["global"])
        
    if not content.strip():
        logger.debug(f"[MemoryAgent] No logs found for {tier}, skipping summary.")
        audit_logger.log(
            source_type=source_type,
            action="Memory Agent: Periodic Summary",
            status="SKIPPED",
            details=f"No {tier} logs found to summarize."
        )
        _emit_memory_event(
            run_handle,
            "memory.periodic_summary.skipped",
            {
                "tier": tier,
                "target_date": dt.isoformat(),
                "reason": "no_logs_found",
            },
        )
        return {
            "status": "skipped",
            "task_kind": "periodic_summary",
            "tier": tier,
            "target_date": dt.isoformat(),
            "reason": "no_logs_found",
        }
        
    prompt = render_periodic_summary_prompt(tier=tier, content=content)
    
    try:
        llm = MemoryRouter().get_extractor_llm()
        response = await llm.ainvoke(prompt)
        
        memory_runtime.save_periodic_summary(tier=tier, content=response.content, dt=dt)
        logger.info(f"[MemoryAgent] Successfully generated and saved {tier} summary.")
        _emit_memory_event(
            run_handle,
            "memory.summary.saved",
            {
                "tier": tier,
                "target_date": dt.isoformat(),
                "content_length": len(response.content or ""),
            },
        )
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Periodic Summary",
            status="SUCCESS",
            details=f"Generated and saved {tier} summary."
        )
        return {
            "status": "completed",
            "task_kind": "periodic_summary",
            "tier": tier,
            "target_date": dt.isoformat(),
            "content_length": len(response.content or ""),
        }
    except Exception as e:
        logger.error(f"[MemoryAgent] Failed to generate {tier} summary: {e}")
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Periodic Summary",
            status="FAILED",
            details=f"Error generating {tier} summary: {e}"
        )
        _emit_memory_event(
            run_handle,
            "memory.periodic_summary.failed",
            {
                "tier": tier,
                "target_date": dt.isoformat(),
                "reason": str(e),
            },
        )
        return {
            "status": "failed",
            "task_kind": "periodic_summary",
            "tier": tier,
            "target_date": dt.isoformat(),
            "reason": str(e),
        }

# === Agent Hook 包装 ===

from typing import TypedDict, Any
from langgraph.graph import StateGraph, START, END

class AgentHookState(TypedDict):
    messages: list
    hook_event: str
    hook_context: Dict[str, Any]

def hook_node(state: AgentHookState):
    """
    Hook 节点：从 hook_context 提取 session_id 并执行分析。
    """
    session_id = state.get("hook_context", {}).get("session_id")
    if session_id:
        try:
            from agents.runners.maintenance_runner import memory_agent_runner

            hook_context = state.get("hook_context", {}) or {}
            memory_agent_runner.run_session_extraction(
                session_id,
                trigger_source=state.get("hook_event") or "HOOK",
                user_id=str(hook_context.get("user_id") or "system"),
                parent_run_id=hook_context.get("parent_run_id"),
            )
        except Exception as e:
            logger.error(f"[MemoryAgent] Hook execution failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        logger.warning("[MemoryAgent] Execution ignored: session_id is missing from hook_context")
    return state

workflow = StateGraph(AgentHookState)
workflow.add_node("agent", hook_node)
workflow.add_edge(START, "agent")
workflow.add_edge("agent", END)

# 作为 Agent Hook 向外暴露
compiled_graph = workflow.compile()
