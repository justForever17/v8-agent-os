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
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
import re
from langchain_core.messages import SystemMessage, HumanMessage
import logging
import json
import hashlib
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException

from core.database import db
from core.background_context_guard import prepare_background_model_messages
from core.background_model_output import sanitize_background_model_output
from core.memory_canonicalization import canonicalize_memory_extraction_result
from core.llm_chat_adapter import _extract_json_payload
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS, storage
from core.memory_router import MemoryRouter
from core.knowledge_db import knowledge_db
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

_VISUAL_ENRICHMENT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_VISUAL_ENRICHMENT_MAX_IMAGES_DEFAULT = 2
_VISION_RESULT_MARKERS = (
    "vision_media_analyzer",
    "--- Vision Analysis Complete ---",
    "Vision Analysis Complete",
)

_NOISY_KNOWLEDGE_HINTS = (
    "oauth",
    "callback",
    "扫码",
    "二维码",
    "登录链接",
    "安装成功",
    "安装失败",
    "安装结果",
    "连通性测试",
    "验证通过",
    "验证失败",
    "voice reply script",
    "tts copy",
    "语音文案",
    "reply_",
    ".ogg",
    ".mp3",
    ".wav",
    "角色扮演",
    "测试对话",
)

_NOISY_PREFERENCE_HINTS = (
    "临时",
    "一次性",
    "workaround",
    "debug",
    "调试",
    "排障",
    "命令输出",
    "oauth",
    "callback",
    "二维码",
    "扫码",
    ".ogg",
    ".mp3",
    ".wav",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# === LLM 提取结果模型 ===

class PreferenceExtraction(BaseModel):
    scope: str = Field(description="Scope of this preference (e.g., 'global', 'project:v8-agent-os')")
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
    relation: str = Field(default="new", description="Knowledge relation: new | reinforce | replace | refine | conflict")
    target_fact_id: str = Field(default="", description="Existing fact ID used by reinforce/replace/refine/conflict when evidence is explicit")
    overwrite_id: str = Field(default="", description="Deprecated compatibility alias for relation=replace + target_fact_id")
    importance: int = Field(default=50, description="Importance score from 0 to 100")
    confidence: float = Field(default=0.5, description="Confidence score from 0.0 to 1.0")
    durability: str = Field(default="operational", description="Durability: stable | operational | transient")
    target_store: str = Field(default="knowledge", description="Target store: knowledge | daily_log | skip")
    persisted_fact_id: str = Field(default="", exclude=True)

class EntityExtraction(BaseModel):
    name: str = Field(description="Name of the entity, lowercase and concise (e.g., 'next.js', 'react')")
    type: str = Field(description="Type of entity (technology, concept, project, user, tool, service, etc.)")

class RelationExtraction(BaseModel):
    subject: str = Field(description="Subject entity name")
    predicate: str = Field(description="Relation predicate (e.g., USES, DEPENDS_ON, PREFERS, IMPLEMENTS)")
    object: str = Field(description="Object entity name")
    source_fact_index: int = Field(default=-1, description="Zero-based index of the emitted knowledge item that proves this relation")

class WorkflowEpisodeExtraction(BaseModel):
    task_family: str = Field(description="Reusable workflow family, e.g. 'install and use a V8 skill' or 'debug desktop live bridge'")
    initial_user_intent: str = Field(default="", description="The user intent that triggered the workflow")
    canonical_trigger_patterns: List[str] = Field(default_factory=list, description="Short trigger phrases that would match future similar tasks")
    first_action_signature: str = Field(default="", description="The first useful action that indicates this workflow has started")
    runtime_lane: str = Field(default="", description="Runtime lane or surface involved, e.g. chat, desktop, memory, extensions")
    ordered_actions: List[str] = Field(default_factory=list, description="Cleaned successful action chain, not raw trial-and-error")
    tool_skill_sequence: List[str] = Field(default_factory=list, description="Tools, brokers, skills, or runtimes used in the successful path")
    failure_markers: List[str] = Field(default_factory=list, description="Wrong turns, failing tools, or confusing signals seen in this episode")
    user_correction_points: List[str] = Field(default_factory=list, description="User corrections or clarifications that changed the path")
    final_success_evidence: str = Field(default="", description="Evidence that the workflow finally succeeded or was accepted")
    user_verdict: str = Field(default="", description="Explicit user acceptance, rejection, or correction if present")
    side_effect_scope: str = Field(default="", description="Side effects involved, e.g. read-only, writes files, launches app, external network")
    privacy_scope: str = Field(default="local_runtime", description="Privacy scope: local_runtime | project | global | sensitive")
    golden_path_steps: List[str] = Field(default_factory=list, description="Cleaned 'do this next time' steps")
    anti_patterns: List[str] = Field(default_factory=list, description="What not to repeat from the failed or noisy path")
    verification_steps: List[str] = Field(default_factory=list, description="How to verify this workflow succeeded")
    confidence: float = Field(default=0.5, description="Confidence score from 0.0 to 1.0")

class MemoryExtractionResult(BaseModel):
    summary: str = Field(description="A concise, one-sentence summary of the core outcome or discussed topic of the session")
    tags: List[str] = Field(description="A list of 3-5 tags describing the session")
    preferences: List[PreferenceExtraction] = Field(default_factory=list, description="Extracted user preferences")
    knowledge: List[KnowledgeExtraction] = Field(default_factory=list, description="Extracted non-transient semantic facts")
    entities: List[EntityExtraction] = Field(default_factory=list, description="Extracted entities for the knowledge graph")
    relations: List[RelationExtraction] = Field(default_factory=list, description="Extracted relationships between entities")
    workflow_episodes: List[WorkflowEpisodeExtraction] = Field(default_factory=list, description="Reusable procedural workflow episodes. Only emit when the transcript proves a repeatable action chain with success evidence.")


class PeriodicSummaryPayload(BaseModel):
    summary: str = Field(description="A concise continuity summary for this period")
    body: str = Field(default="", description="Compact markdown body for the period summary file")


@dataclass
class MemoryExtractionAttempt:
    result: Optional[MemoryExtractionResult] = None
    failure_stage: str = ""
    failure_reason: str = ""
    extractor_model: str = ""
    raw_output_preview: str = ""
    parser_error_preview: str = ""

# === 专业工具函数 ===

def _get_background_llm():
    """获取后台 LLM（使用 memory 专用配置）"""
    router = MemoryRouter()
    return router.get_extractor_llm()


def _extractor_model_name(llm: Any) -> str:
    for key in ("model_id", "model_name", "model"):
        value = getattr(llm, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    meta = getattr(llm, "meta", None)
    if isinstance(meta, dict):
        for key in ("model_id", "model_name", "model"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _build_extraction_attempt(
    *,
    result: Optional[MemoryExtractionResult] = None,
    failure_stage: str = "",
    failure_reason: str = "",
    extractor_model: str = "",
    raw_output_preview: str = "",
    parser_error_preview: str = "",
) -> MemoryExtractionAttempt:
    return MemoryExtractionAttempt(
        result=result,
        failure_stage=failure_stage,
        failure_reason=str(failure_reason or "").strip(),
        extractor_model=str(extractor_model or "").strip(),
        raw_output_preview=_safe_json_excerpt(raw_output_preview, limit=1200),
        parser_error_preview=_safe_json_excerpt(parser_error_preview, limit=600),
    )

def _generate_quick_summary(chat_text: str) -> str:
    """快速生成会话一句话摘要，用于预检索历史知识"""
    try:
        llm = _get_background_llm()
        system_prompt = "You are a summarizing assistant. Read the chat log and output exactly ONE short sentence summarizing the core technical topic or outcome of the conversation. Output only the sentence, nothing else."
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Chat Log:\n\n{chat_text[:2000]}...") # truncate for speed/cost
        ])
        summary = sanitize_background_model_output(response).text.strip()
        return summary or "latest conversation"
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
        "preference_importance_threshold": _as_int("preference_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["preference_importance_threshold"]), 0, 100),
        "preference_confidence_threshold": _as_float("preference_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["preference_confidence_threshold"]), 0.0, 1.0),
        "knowledge_importance_threshold": _as_int("knowledge_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_importance_threshold"]), 0, 100),
        "knowledge_confidence_threshold": _as_float("knowledge_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_confidence_threshold"]), 0.0, 1.0),
        "global_knowledge_importance_threshold": _as_int("global_knowledge_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_importance_threshold"]), 0, 100),
        "global_knowledge_confidence_threshold": _as_float("global_knowledge_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_confidence_threshold"]), 0.0, 1.0),
        "global_operational_importance_threshold": _as_int("global_operational_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_importance_threshold"]), 0, 100),
        "global_operational_confidence_threshold": _as_float("global_operational_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_confidence_threshold"]), 0.0, 1.0),
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
            "id": msg.get("id") or msg.get("entry_id"),
            "role": msg.get("role"),
            "content": msg.get("content") or msg.get("text") or "",
            "source": msg.get("source"),
            "seq": msg.get("seq"),
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

    last_hash = str(state.get("last_content_hash") or "").strip()
    current_full_hash = _message_hash(messages)
    if last_hash and current_full_hash == last_hash:
        return [], state, "duplicate_transcript"

    new_messages = messages[last_count:]
    if not new_messages:
        return messages, state, "transcript_changed"

    current_incremental_hash = _message_hash(new_messages)
    if last_hash and current_incremental_hash == last_hash:
        return [], state, "duplicate_increment"

    return new_messages, state, "incremental"


def _safe_json_excerpt(value: Any, *, limit: int = 1200) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        normalized = value.strip()
    else:
        try:
            normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            normalized = str(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _memory_visual_enrichment_policy() -> Dict[str, Any]:
    memory_config = storage.get_memory_config() or {}
    raw_config = memory_config.get("visual_enrichment")
    config = raw_config if isinstance(raw_config, dict) else {}
    enabled_value = config.get("enabled", memory_config.get("visual_enrichment_enabled", True))
    try:
        max_images = int(config.get("max_images", memory_config.get("visual_enrichment_max_images", _VISUAL_ENRICHMENT_MAX_IMAGES_DEFAULT)))
    except (TypeError, ValueError):
        max_images = _VISUAL_ENRICHMENT_MAX_IMAGES_DEFAULT
    return {
        "enabled": enabled_value is not False,
        "max_images": max(0, min(max_images, 5)),
    }


def _is_image_media_source(value: str, mime_type: str = "") -> bool:
    mime = str(mime_type or "").strip().lower()
    if mime.startswith("image/"):
        return True
    source = str(value or "").strip().strip("'\"")
    if not source:
        return False
    lower_source = source.lower().split("?", 1)[0].split("#", 1)[0]
    return any(lower_source.endswith(ext) for ext in _VISUAL_ENRICHMENT_IMAGE_EXTENSIONS)


def _clean_media_source(value: Any) -> str:
    source = str(value or "").strip()
    source = source.strip(" \t\r\n'\"`")
    source = source.rstrip(".,;)")
    return source


def _candidate_source_kind(source: str) -> str:
    normalized = str(source or "").strip()
    if re.match(r"^https?://", normalized, re.IGNORECASE):
        return "source_url"
    if re.match(r"^[A-Za-z]:[\\/]", normalized) or normalized.startswith("\\\\") or normalized.startswith("/"):
        return "file_path"
    return "unknown"


def _add_visual_candidate(
    candidates: List[Dict[str, Any]],
    seen: set[str],
    *,
    source: Any,
    mime_type: str = "",
    message_id: str = "",
    origin: str = "",
) -> None:
    cleaned = _clean_media_source(source)
    if not cleaned or not _is_image_media_source(cleaned, mime_type):
        return
    source_kind = _candidate_source_kind(cleaned)
    if source_kind == "unknown":
        return
    key = cleaned.lower()
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        {
            "source": cleaned,
            "sourceKind": source_kind,
            "mimeType": str(mime_type or "").strip(),
            "messageId": message_id,
            "origin": origin,
        }
    )


def _extract_media_sources_from_text(text: str) -> List[str]:
    content = str(text or "")
    sources: List[str] = []
    for match in re.finditer(r"\[User uploaded file:\s*([^\]]+)\]", content, flags=re.IGNORECASE):
        sources.append(match.group(1))
    for match in re.finditer(
        r"https?://[^\s\]\)\"']+\.(?:png|jpg|jpeg|webp|bmp|gif)(?:\?[^\s\]\)\"']*)?",
        content,
        flags=re.IGNORECASE,
    ):
        sources.append(match.group(0))
    for match in re.finditer(
        r"[A-Za-z]:[\\/][^\r\n\"'\]]+\.(?:png|jpg|jpeg|webp|bmp|gif)",
        content,
        flags=re.IGNORECASE,
    ):
        sources.append(match.group(0))
    return sources


def _collect_visual_media_candidates(
    *,
    durable_messages: List[Dict[str, Any]],
    transcript_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for message in durable_messages:
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("id") or "")
        for image in list(message.get("images") or []):
            _add_visual_candidate(candidates, seen, source=image, message_id=message_id, origin="durable.images")
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        for attachment in list(metadata.get("attachments") or []):
            if not isinstance(attachment, dict):
                continue
            mime_type = str(attachment.get("mimeType") or attachment.get("mime_type") or attachment.get("type") or "")
            for key in ("workspacePath", "workspace_path", "path", "filePath", "file_path", "url", "publicUrl", "public_url"):
                _add_visual_candidate(
                    candidates,
                    seen,
                    source=attachment.get(key),
                    mime_type=mime_type,
                    message_id=message_id,
                    origin=f"durable.attachments.{key}",
                )
        for source in _extract_media_sources_from_text(str(message.get("content") or "")):
            _add_visual_candidate(candidates, seen, source=source, message_id=message_id, origin="durable.content")

    for entry in transcript_entries:
        if not isinstance(entry, dict):
            continue
        message_id = str(entry.get("id") or "")
        content = str(entry.get("content") or "")
        for source in _extract_media_sources_from_text(content):
            _add_visual_candidate(candidates, seen, source=source, message_id=message_id, origin="transcript.content")
        for match in re.finditer(
            r'"(?:sourcePath|externalUrl|previewUrl|workspacePath|workspaceRelativePath|url)"\s*:\s*"([^"]+)"',
            content,
            flags=re.IGNORECASE,
        ):
            _add_visual_candidate(candidates, seen, source=match.group(1), message_id=message_id, origin="transcript.artifact_json")

    return candidates


def _candidate_already_analyzed(candidate: Dict[str, Any], transcript_text: str) -> bool:
    text = str(transcript_text or "")
    if not any(marker in text for marker in _VISION_RESULT_MARKERS):
        return False
    source = str(candidate.get("source") or "").strip()
    if not source:
        return False
    if f"Source: {source}" in text or f"source: {source}" in text:
        return True
    for marker in _VISION_RESULT_MARKERS:
        start = text.find(marker)
        while start >= 0:
            if source in text[start : start + 1600]:
                return True
            start = text.find(marker, start + len(marker))
    return False


def _invoke_memory_visual_analyzer(candidate: Dict[str, Any], *, session_id: str, run_id: str | None) -> str:
    from erc.runtime_context import bind_runtime_context
    from core.tools import vision_media_analyzer as vision_tool_module

    prompt = (
        "Memory Visual Enrichment：请只输出可供记忆抽取使用的客观文本证据。"
        "提取图片中的可见文字、界面/对象、错误信息、项目或用户偏好线索；"
        "不要推测身份，不要编造图片外信息；不确定的内容请标注为不确定。"
    )
    payload = {"prompt": prompt}
    source = str(candidate.get("source") or "").strip()
    if candidate.get("sourceKind") == "source_url":
        payload["source_url"] = source
        payload["mime_type_hint"] = str(candidate.get("mimeType") or "image/*")
    else:
        payload["file_path"] = source

    tool = getattr(vision_tool_module, "vision_media_analyzer")
    with bind_runtime_context(
        session_id=session_id,
        run_id=run_id,
        runtime_kind="memory",
        memory_visual_enrichment=True,
    ):
        if hasattr(tool, "invoke"):
            return str(tool.invoke(payload))
        return str(tool(**payload))


def _build_memory_visual_enrichment(
    *,
    session_id: str,
    durable_messages: List[Dict[str, Any]],
    transcript_entries: List[Dict[str, Any]],
    transcript_text: str,
    run_handle: Any | None = None,
) -> tuple[str, Dict[str, Any]]:
    policy = _memory_visual_enrichment_policy()
    candidates = _collect_visual_media_candidates(
        durable_messages=durable_messages,
        transcript_entries=transcript_entries,
    )
    diagnostics: Dict[str, Any] = {
        "enabled": bool(policy.get("enabled")),
        "scannedMediaCount": len(candidates),
        "enrichedCount": 0,
        "skippedCount": 0,
        "errorCount": 0,
        "maxImages": int(policy.get("max_images") or 0),
        "items": [],
    }
    if not policy.get("enabled"):
        diagnostics["skippedReason"] = "disabled"
        diagnostics["skippedCount"] = len(candidates)
        return "", diagnostics
    if not candidates:
        diagnostics["skippedReason"] = "no_unanalyzed_images"
        return "", diagnostics

    run_id = str(getattr(run_handle, "run_id", "") or "").strip() or None
    evidence_lines: List[str] = []
    processed = 0
    for candidate in candidates:
        item = {
            "source": candidate.get("source"),
            "sourceKind": candidate.get("sourceKind"),
            "messageId": candidate.get("messageId"),
            "origin": candidate.get("origin"),
        }
        if processed >= int(policy.get("max_images") or 0):
            item["status"] = "skipped"
            item["reason"] = "max_images_reached"
            diagnostics["skippedCount"] += 1
            diagnostics["items"].append(item)
            continue
        if _candidate_already_analyzed(candidate, transcript_text):
            item["status"] = "skipped"
            item["reason"] = "already_analyzed"
            diagnostics["skippedCount"] += 1
            diagnostics["items"].append(item)
            continue
        if candidate.get("sourceKind") == "file_path":
            source_path = Path(str(candidate.get("source") or ""))
            if not source_path.exists() or not source_path.is_file():
                item["status"] = "skipped"
                item["reason"] = "file_missing"
                diagnostics["skippedCount"] += 1
                diagnostics["items"].append(item)
                continue
        try:
            result = _invoke_memory_visual_analyzer(candidate, session_id=session_id, run_id=run_id)
            result_text = str(result or "").strip()
            if not result_text or result_text.lower().startswith(("error:", "vision media analysis failed")):
                item["status"] = "error"
                item["reason"] = "vision_result_error"
                item["preview"] = _safe_json_excerpt(result, limit=400)
                diagnostics["errorCount"] += 1
                diagnostics["items"].append(item)
                processed += 1
                continue
            item["status"] = "enriched"
            item["preview"] = _safe_json_excerpt(result, limit=400)
            diagnostics["enrichedCount"] += 1
            diagnostics["items"].append(item)
            processed += 1
            evidence_lines.append(
                "\n".join(
                    [
                        f"- source: {candidate.get('source')}",
                        f"  messageId: {candidate.get('messageId') or ''}",
                        f"  origin: {candidate.get('origin') or ''}",
                        "  analysis:",
                        "  " + _safe_json_excerpt(result, limit=1800).replace("\n", "\n  "),
                    ]
                )
            )
        except Exception as exc:
            item["status"] = "error"
            item["reason"] = "vision_call_failed"
            item["error"] = str(exc)
            diagnostics["errorCount"] += 1
            diagnostics["items"].append(item)
            processed += 1

    if not evidence_lines:
        return "", diagnostics
    block = (
        "\n\n[Memory Visual Evidence]\n"
        "以下内容由 vision_media_analyzer 在记忆维护阶段从会话图片生成。Memory agent 只能消费这些文本证据，不直接读取图片。\n"
        + "\n".join(evidence_lines)
        + "\n[/Memory Visual Evidence]\n"
    )
    return block, diagnostics


def _coerce_json_object(text: str) -> dict[str, Any]:
    try:
        payload = _extract_json_payload(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


def _clean_required_dict_items(items: Any, required_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return cleaned
    for item in items:
        if not isinstance(item, dict):
            continue
        if all(str(item.get(key) or "").strip() for key in required_keys):
            cleaned.append(item)
    return cleaned


def _repair_memory_extraction_payload(text: str) -> MemoryExtractionResult:
    payload = _coerce_json_object(text)
    if not payload:
        raise ValueError("memory extraction output did not contain a JSON object")
    payload["tags"] = [str(item).strip() for item in list(payload.get("tags") or []) if str(item or "").strip()][:8]
    payload["preferences"] = _clean_required_dict_items(payload.get("preferences"), ("scope", "key", "value"))
    payload["knowledge"] = _clean_required_dict_items(payload.get("knowledge"), ("fact", "category", "scope"))
    payload["entities"] = _clean_required_dict_items(payload.get("entities"), ("name", "type"))
    payload["relations"] = _clean_required_dict_items(payload.get("relations"), ("subject", "predicate", "object"))
    payload["workflow_episodes"] = [
        item for item in list(payload.get("workflow_episodes") or []) if isinstance(item, dict) and str(item.get("task_family") or "").strip()
    ]
    return MemoryExtractionResult.model_validate(payload)


def _looks_like_todo_update(value: Any) -> bool:
    text = _safe_json_excerpt(value, limit=500).lower()
    return "command(update={'todos'" in text or ('"todos"' in text and "task_" in text)


def _append_if_present(lines: List[str], label: str, value: Any, *, limit: int = 1200) -> None:
    text = _safe_json_excerpt(value, limit=limit)
    if text:
        lines.append(f"{label}: {text}")


def _projection_message_to_transcript_entry(message: Dict[str, Any], index: int) -> Dict[str, Any] | None:
    role = str(message.get("role") or "unknown").strip().lower()
    lines: List[str] = []
    _append_if_present(lines, "content", message.get("content"), limit=2400)

    for part in list(message.get("parts") or []):
        if not isinstance(part, dict) or _looks_like_todo_update(part):
            continue
        part_type = str(part.get("type") or "").strip().lower()
        if part_type in {"text", "markdown"}:
            _append_if_present(lines, "text", part.get("content"), limit=2400)
        elif part_type == "reasoning":
            continue
        elif part_type in {"tool_call", "tool_start"}:
            tool_name = part.get("toolName") or part.get("tool_name") or "tool"
            _append_if_present(lines, f"tool_call {tool_name}", part.get("args") or part.get("input"), limit=1000)
        elif part_type in {"tool_result", "tool_output"}:
            tool_name = part.get("toolName") or part.get("tool_name") or "tool"
            _append_if_present(lines, f"tool_result {tool_name}", part.get("result") or part.get("output"), limit=1200)
        elif part_type in {"artifact", "image", "video", "audio", "file"}:
            _append_if_present(lines, f"artifact {part_type}", part, limit=1000)

    for artifact in list(message.get("artifacts") or []):
        if isinstance(artifact, dict):
            _append_if_present(lines, "artifact", {
                "id": artifact.get("artifactId") or artifact.get("id"),
                "kind": artifact.get("kind"),
                "title": artifact.get("title"),
                "mimeType": artifact.get("mimeType"),
                "workspaceRelativePath": artifact.get("workspaceRelativePath"),
            }, limit=800)

    content = "\n".join(dict.fromkeys(line for line in lines if line.strip())).strip()
    if not content:
        return None
    return {
        "id": message.get("id") or f"projection:{index}",
        "role": role,
        "content": content,
        "created_at": message.get("createdAt") or message.get("timestamp"),
        "source": "projection",
        "run_id": message.get("runId") or message.get("run_id"),
    }


def _durable_message_to_transcript_entry(message: Dict[str, Any], index: int) -> Dict[str, Any] | None:
    role = str(message.get("role") or "unknown").strip().lower()
    lines: List[str] = []
    _append_if_present(lines, "content", message.get("content"), limit=2400)
    if message.get("tool_calls") and not _looks_like_todo_update(message.get("tool_calls")):
        _append_if_present(lines, "tool_calls", message.get("tool_calls"), limit=1200)
    if message.get("tool_results") and not _looks_like_todo_update(message.get("tool_results")):
        _append_if_present(lines, "tool_results", message.get("tool_results"), limit=1200)
    if message.get("images"):
        _append_if_present(lines, "images", message.get("images"), limit=800)

    content = "\n".join(dict.fromkeys(line for line in lines if line.strip())).strip()
    if not content:
        return None
    return {
        "id": message.get("id") or f"durable:{index}",
        "role": role,
        "content": content,
        "created_at": message.get("created_at") or message.get("createdAt"),
        "source": "durable",
        "run_id": (message.get("metadata") or {}).get("run_id") if isinstance(message.get("metadata"), dict) else None,
    }


def _canonical_message_to_transcript_entry(message: Dict[str, Any], index: int) -> Dict[str, Any] | None:
    role = str(message.get("role") or "unknown").strip().lower()
    lines: List[str] = []
    nodes = [node for node in list(message.get("nodes") or []) if isinstance(node, dict)]
    has_narrative_node = any(
        str(node.get("kind") or "").strip().lower() == "narrative" and str(node.get("content") or "").strip()
        for node in nodes
    )
    if not has_narrative_node:
        _append_if_present(lines, "content", message.get("content_text") or message.get("content"), limit=2400)

    for node in nodes:
        if not isinstance(node, dict) or _looks_like_todo_update(node):
            continue
        kind = str(node.get("kind") or "").strip().lower()
        if kind == "narrative":
            _append_if_present(lines, "text", node.get("content"), limit=2400)
        elif kind == "execution":
            execution_type = str(node.get("executionType") or node.get("execution_type") or "").strip().lower()
            if execution_type == "reasoning":
                continue
            elif execution_type == "tool_call":
                tool_name = node.get("toolName") or node.get("tool_name") or "tool"
                _append_if_present(lines, f"tool_call {tool_name}", node.get("args"), limit=1400)
            elif execution_type == "tool_result":
                tool_name = node.get("toolName") or node.get("tool_name") or "tool"
                _append_if_present(lines, f"tool_result {tool_name}", node.get("result"), limit=1600)
            elif execution_type == "agent_start":
                _append_if_present(lines, "agent_start", node.get("agentName") or node.get("agent_id"), limit=300)
        elif kind == "artifact":
            artifact = node.get("artifact") if isinstance(node.get("artifact"), dict) else node
            _append_if_present(lines, "artifact", {
                "id": artifact.get("artifactId") or artifact.get("id"),
                "kind": artifact.get("kind"),
                "title": artifact.get("title") or artifact.get("displayLabel"),
                "mimeType": artifact.get("mimeType") or artifact.get("mime_type"),
                "workspaceRelativePath": artifact.get("workspaceRelativePath") or artifact.get("workspace_relative_path"),
            }, limit=900)

    for artifact in list(message.get("artifacts") or []):
        if isinstance(artifact, dict):
            _append_if_present(lines, "artifact", {
                "id": artifact.get("artifactId") or artifact.get("id"),
                "kind": artifact.get("kind"),
                "title": artifact.get("title"),
                "mimeType": artifact.get("mimeType"),
                "workspaceRelativePath": artifact.get("workspaceRelativePath"),
            }, limit=800)

    content = "\n".join(dict.fromkeys(line for line in lines if line.strip())).strip()
    if not content:
        return None
    return {
        "id": message.get("id") or f"canonical:{index}",
        "role": role,
        "content": content,
        "created_at": message.get("created_at") or message.get("createdAt"),
        "source": "chat_canonical_messages",
        "run_id": message.get("run_id") or message.get("runId"),
    }


def _build_canonical_session_transcript(session_id: str, durable_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    runtime_events = db.get_runtime_events(session_id)
    latest_runtime_seq = db.get_latest_runtime_seq(session_id)
    snapshot_row = db.get_latest_runtime_snapshot(session_id, snapshot_type="chat_projection")
    snapshot = snapshot_row.get("snapshot") if snapshot_row else None
    snapshot_seq = int(snapshot_row.get("latest_seq") or 0) if snapshot_row else 0
    source = "chat_canonical_messages"

    entries: List[Dict[str, Any]] = []
    try:
        canonical_messages = db.get_chat_canonical_messages(session_id)
    except Exception as exc:
        logger.warning(f"[MemoryAgent] Failed to load canonical transcript for {session_id}: {exc}")
        canonical_messages = []

    for index, message in enumerate(canonical_messages or []):
        if isinstance(message, dict):
            entry = _canonical_message_to_transcript_entry(message, index)
            if entry:
                entries.append(entry)

    if entries:
        transcript_hash = _message_hash(entries)
        semantic_text = "\n".join(f"{entry.get('role', 'unknown').upper()}: {entry.get('content', '')}" for entry in entries).strip()
        return {
            "session_id": session_id,
            "source": source,
            "entries": entries,
            "latest_seq": latest_runtime_seq,
            "durable_message_count": len(durable_messages),
            "runtime_event_count": len(runtime_events),
            "user_message_count": sum(1 for entry in entries if entry.get("role") == "user"),
            "content_length": len(semantic_text),
            "hash": transcript_hash,
            "text": semantic_text,
        }

    source = "runtime_snapshot"

    if not isinstance(snapshot, dict) or snapshot_seq < latest_runtime_seq:
        try:
            from core.runtime_projection import build_chat_projection_snapshot

            snapshot = build_chat_projection_snapshot(session_id, runtime_events)
            snapshot_seq = int(snapshot.get("latest_seq") or latest_runtime_seq or 0)
            source = "runtime_projection"
        except Exception as exc:
            logger.warning(f"[MemoryAgent] Failed to build runtime projection transcript for {session_id}: {exc}")
            snapshot = None

    if isinstance(snapshot, dict) and isinstance(snapshot.get("messages"), list):
        for index, message in enumerate(snapshot.get("messages") or []):
            if isinstance(message, dict):
                entry = _projection_message_to_transcript_entry(message, index)
                if entry:
                    entries.append(entry)

    if not entries:
        source = "durable_messages"
        for index, message in enumerate(durable_messages):
            entry = _durable_message_to_transcript_entry(message, index)
            if entry:
                entries.append(entry)

    transcript_hash = _message_hash(entries)
    semantic_text = "\n".join(f"{entry.get('role', 'unknown').upper()}: {entry.get('content', '')}" for entry in entries).strip()
    return {
        "session_id": session_id,
        "source": source,
        "entries": entries,
        "latest_seq": snapshot_seq or latest_runtime_seq,
        "durable_message_count": len(durable_messages),
        "runtime_event_count": len(runtime_events),
        "user_message_count": sum(1 for entry in entries if entry.get("role") == "user"),
        "content_length": len(semantic_text),
        "hash": transcript_hash,
        "text": semantic_text,
    }

def _extract_with_llm(
    chat_text: str,
    context_text: str,
    *,
    resolved_scope: str,
    scope_chain: Optional[List[str]] = None,
) -> MemoryExtractionAttempt:
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
        return _build_extraction_attempt(
            failure_stage="extractor_config_missing",
            failure_reason=str(e),
        )
    extractor_model = _extractor_model_name(llm)
    
    system_prompt = _get_extraction_prompt(format_instructions)
    
    try:
        prepared_context = prepare_background_model_messages(
            system_prompt=system_prompt,
            instruction=(
                "Use the prepared background material to extract durable memory. "
                "Return valid JSON only.\n\n"
                f"Runtime Scope Context:\n"
                f"- resolved_scope: {resolved_scope}\n"
                f"- scope_chain: {', '.join(scope_chain or [resolved_scope])}"
            ),
            materials=[
                {
                    "title": "Historical Context (Prior Knowledge)",
                    "kind": "memory_recall",
                    "content": context_text,
                },
                {
                    "title": "Chat Log",
                    "kind": "session_transcript",
                    "content": chat_text,
                },
            ],
            runtime_kind="memory",
            target_role="memory:session_extraction",
            resolved_model_id=extractor_model,
            component="memory",
            node="session_extraction",
        )
        raw_response = llm.invoke(prepared_context.messages)
        sanitized_output = sanitize_background_model_output(raw_response)
        str_content = sanitized_output.text
            
        # 很多时候大模型即使被警告，还是会包在 ```json...``` 里
        import re
        json_match = re.search(r"```(?:json)?(.*?)```", str_content, re.DOTALL)
        if json_match:
            str_content = json_match.group(1).strip()
            
        # 针对无限复读循环做暴力截断 (如超过 2000 字符且没有结束的 }，则修复)
        if len(str_content) > 4000:
            logger.warning("[MemoryAgent] LLM output unusually long, possible repetition bug. Truncating.")
            str_content = str_content[:4000]

        raw_output_preview = _safe_json_excerpt(str_content, limit=1200)
        if not str_content.strip():
            return _build_extraction_attempt(
                failure_stage="llm_response_empty",
                failure_reason=sanitized_output.no_visible_text_reason or "LLM returned empty content.",
                extractor_model=extractor_model,
                raw_output_preview=raw_output_preview,
            )
            
        try:
            parsed_result = parser.invoke(str_content)
            return _build_extraction_attempt(
                result=parsed_result,
                extractor_model=extractor_model,
                raw_output_preview=raw_output_preview,
            )
        except OutputParserException as e:
            logger.warning(f"[MemoryAgent] Output parsing failed: {e}. Attempting auto-fix...")
            parser_error_preview = _safe_json_excerpt(str(e), limit=600)
            try:
                repaired_result = _repair_memory_extraction_payload(str_content)
                return _build_extraction_attempt(
                    result=repaired_result,
                    extractor_model=extractor_model,
                    raw_output_preview=raw_output_preview,
                    parser_error_preview=parser_error_preview,
                )
            except Exception as repair_error:
                logger.error(f"[MemoryAgent] Local output repair failed: {repair_error}")
                return _build_extraction_attempt(
                    failure_stage="repair_parser_failed",
                    failure_reason=str(repair_error),
                    extractor_model=extractor_model,
                    raw_output_preview=raw_output_preview,
                    parser_error_preview=parser_error_preview,
                )
        except Exception as e:
            logger.error(f"[MemoryAgent] Output parsing crashed: {e}")
            return _build_extraction_attempt(
                failure_stage="parser_failed",
                failure_reason=str(e),
                extractor_model=extractor_model,
                raw_output_preview=raw_output_preview,
                parser_error_preview=_safe_json_excerpt(str(e), limit=600),
            )
            
    except Exception as e:
        logger.error(f"[MemoryAgent] LLM extraction error: {e}")
        return _build_extraction_attempt(
            failure_stage="llm_invoke_failed",
            failure_reason=str(e),
            extractor_model=extractor_model,
        )


async def _synthesize_periodic_summary_payload(*, tier: str, content: str) -> PeriodicSummaryPayload:
    llm = _get_background_llm()
    parser = PydanticOutputParser(pydantic_object=PeriodicSummaryPayload)
    prompt = render_periodic_summary_prompt(
        tier=tier,
        content="[See prepared background material: scoped periodic memory logs.]",
        format_instructions=parser.get_format_instructions(),
    )
    prepared_context = prepare_background_model_messages(
        system_prompt="You are the long-term memory synthesizer module for V8 Agent OS.",
        instruction=prompt,
        materials=[
            {
                "title": f"Scoped periodic memory logs ({tier})",
                "kind": "periodic_memory_logs",
                "content": content,
            }
        ],
        runtime_kind="memory",
        target_role=f"memory:periodic_summary:{tier}",
        resolved_model_id=_extractor_model_name(llm),
        component="memory",
        node="periodic_summary",
    )
    response = await llm.ainvoke(prepared_context.messages)
    text_content = sanitize_background_model_output(response).text
    try:
        payload = _extract_json_payload(text_content)
        return PeriodicSummaryPayload.model_validate(payload)
    except Exception:
        pass
    fenced_match = re.search(r"```(?:json)?(.*?)```", text_content, re.DOTALL)
    if fenced_match:
        text_content = fenced_match.group(1).strip()
    raw_output_preview = _safe_json_excerpt(text_content, limit=1200)
    try:
        return parser.invoke(text_content)
    except OutputParserException as exc:
        logger.warning(f"[MemoryAgent] Periodic summary parsing failed: {exc}. Attempting local auto-fix...")
        try:
            payload = _coerce_json_object(text_content)
            return PeriodicSummaryPayload.model_validate(payload)
        except Exception as repair_error:
            raise RuntimeError(
                f"Periodic summary parsing failed after repair. raw={raw_output_preview} error={repair_error}"
            ) from repair_error


def _normalize_periodic_summary_payload(*, tier: str, payload: PeriodicSummaryPayload) -> Dict[str, str]:
    summary = " ".join(str(payload.summary or "").split()).strip()
    body = str(payload.body or "").strip()
    if body.startswith("---"):
        header_match = re.match(r'^---\n.*?\n---\s*', body, flags=re.DOTALL)
        if header_match:
            body = body[header_match.end():].strip()
    body = re.sub(r"^#\s+.+?$", "", body, flags=re.MULTILINE).strip()
    body_limits = {
        "week": 1600,
        "month": 1100,
        "year": 800,
    }
    body_limit = body_limits.get(tier, 1200)
    if len(body) > body_limit:
        body = body[: body_limit - 3].rstrip() + "..."
    if not summary:
        fallback = body or ""
        compact = re.sub(r"\s+", " ", fallback).strip()
        summary = compact[:177].rstrip() + "..." if len(compact) > 180 else compact
    return {
        "summary": summary,
        "body": body,
    }

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


def _scope_kind(scope: str) -> str:
    normalized = str(scope or "").strip().lower()
    if normalized.startswith("project:"):
        return "project"
    if normalized.startswith("channel:"):
        return "channel"
    if normalized.startswith("workspace:"):
        return "workspace"
    if normalized.startswith("external_api_thread:"):
        return "external_api_thread"
    return "global"


def _is_path_like_fact(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(token in normalized for token in (":\\", "/users/", "\\users\\", "~/"))


def _is_operational_learning_fact(fact: KnowledgeExtraction) -> bool:
    text = f"{fact.category} {fact.fact}".lower()
    return any(
        token in text
        for token in (
            "operational_workflow",
            "workflow",
            "computer use",
            "computer_use",
            "desktop live",
            "desktop_live",
            "tool workflow",
            "tool usage",
            "工具流程",
            "操作流程",
            "使用流程",
            "运行约定",
            "runtime contract",
            "runtime_contract",
        )
    )


def _item_key(item: Any, fields: List[str]) -> tuple:
    return tuple(str(getattr(item, field, "") or "").strip() for field in fields)


def _policy_int(policy: Dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(policy.get(key, default))
    except (TypeError, ValueError):
        value = default
    return value


def _policy_float(policy: Dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(policy.get(key, default))
    except (TypeError, ValueError):
        value = default
    return value


_WINDOWS_PATH_RE = re.compile(r"[a-zA-Z]:\\")
_UNIX_PATH_RE = re.compile(r"(^|[\s'\"(])/(users|home|tmp|var|opt|etc|mnt|volumes)/", re.IGNORECASE)


def _looks_path_like_text(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return (
        bool(_WINDOWS_PATH_RE.search(normalized))
        or bool(_UNIX_PATH_RE.search(lowered))
        or "\\.v8-agent-os\\" in lowered
        or "/.v8-agent-os/" in lowered
    )


def _preference_noise_reason(pref: PreferenceExtraction) -> str | None:
    text = f"{str(pref.key or '').strip().lower()} {str(pref.value or '').strip().lower()}"
    if _looks_path_like_text(text):
        return "path_like_preference"
    if any(token in text for token in _NOISY_PREFERENCE_HINTS):
        return "noise_hint"
    return None


def classify_global_preference_risk(key: str, value: str) -> str | None:
    text = f"{str(key or '').strip().lower()} {str(value or '').strip().lower()}"
    if _looks_path_like_text(text):
        return "path_like_global_preference"
    if any(token in text for token in _NOISY_PREFERENCE_HINTS):
        return "noisy_global_preference"
    return None


def classify_global_knowledge_risk(fact_text: str, category_text: str = "") -> str | None:
    normalized_fact = str(fact_text or "").strip().lower()
    normalized_category = str(category_text or "").strip().lower()
    combined = f"{normalized_fact} {normalized_category}"
    if _is_path_like_fact(normalized_fact) or _looks_path_like_text(normalized_fact):
        return "path_like_global"
    if any(token in combined for token in _NOISY_KNOWLEDGE_HINTS):
        return "noise_hint"
    return None


def _evaluate_preference_persistence(pref: PreferenceExtraction, policy: Dict[str, Any]) -> tuple[bool, str]:
    if _normalize_target_store(pref.target_store, default="preference") != "preference":
        return False, f"target_store_{_normalize_target_store(pref.target_store, default='preference')}"
    if _normalize_durability(pref.durability, default="stable") != "stable":
        return False, f"durability_{_normalize_durability(pref.durability, default='stable')}"
    noise_reason = _preference_noise_reason(pref)
    if noise_reason:
        return False, noise_reason
    if int(pref.importance or 0) < _policy_int(policy, "preference_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["preference_importance_threshold"])):
        return False, "importance_below_threshold"
    if float(pref.confidence or 0.0) < _policy_float(policy, "preference_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["preference_confidence_threshold"])):
        return False, "confidence_below_threshold"
    return True, "persisted"


def _evaluate_knowledge_persistence(fact: KnowledgeExtraction, policy: Dict[str, Any]) -> tuple[bool, str]:
    if _normalize_target_store(fact.target_store, default="knowledge") != "knowledge":
        return False, f"target_store_{_normalize_target_store(fact.target_store, default='knowledge')}"
    durability = _normalize_durability(fact.durability, default="operational")
    if durability == "transient":
        return False, "durability_transient"

    normalized_scope = str(fact.scope or "").strip().lower()
    scope_kind = _scope_kind(normalized_scope)
    fact_text = str(fact.fact or "").strip().lower()
    category_text = str(fact.category or "").strip().lower()
    if any(token in f"{fact_text} {category_text}" for token in _NOISY_KNOWLEDGE_HINTS):
        return False, "noise_hint"

    if scope_kind == "global":
        global_risk = classify_global_knowledge_risk(fact_text, category_text)
        if global_risk:
            return False, global_risk
        if durability == "operational" and _is_operational_learning_fact(fact):
            if int(fact.importance or 0) < _policy_int(policy, "global_operational_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_importance_threshold"])):
                return False, "importance_below_operational_threshold"
            if float(fact.confidence or 0.0) < _policy_float(policy, "global_operational_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_confidence_threshold"])):
                return False, "confidence_below_operational_threshold"
            return True, "persisted_operational_workflow"
        if durability == "operational":
            if int(fact.importance or 0) < _policy_int(policy, "global_operational_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_importance_threshold"])):
                return False, "importance_below_global_operational_threshold"
            if float(fact.confidence or 0.0) < _policy_float(policy, "global_operational_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_confidence_threshold"])):
                return False, "confidence_below_global_operational_threshold"
            return True, "persisted_global_operational"
        if durability != "stable":
            return False, "global_requires_stable_or_operational"
        if int(fact.importance or 0) < _policy_int(policy, "global_knowledge_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_importance_threshold"])):
            return False, "importance_below_global_threshold"
        if float(fact.confidence or 0.0) < _policy_float(policy, "global_knowledge_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_confidence_threshold"])):
            return False, "confidence_below_global_threshold"
        return True, "persisted"

    if durability not in {"stable", "operational"}:
        return False, f"durability_{durability}"
    if int(fact.importance or 0) < _policy_int(policy, "knowledge_importance_threshold", int(MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_importance_threshold"])):
        return False, "importance_below_threshold"
    if float(fact.confidence or 0.0) < _policy_float(policy, "knowledge_confidence_threshold", float(MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_confidence_threshold"])):
        return False, "confidence_below_threshold"
    return True, "persisted"


def _should_store_preference(pref: PreferenceExtraction, policy: Dict[str, Any]) -> bool:
    allowed, _ = _evaluate_preference_persistence(pref, policy)
    return allowed


def _should_store_knowledge(fact: KnowledgeExtraction, policy: Dict[str, Any]) -> bool:
    allowed, _ = _evaluate_knowledge_persistence(fact, policy)
    return allowed


def _store_preferences(result: MemoryExtractionResult, policy: Dict[str, Any]) -> tuple[int, List[PreferenceExtraction]]:
    """[专业工具] 将偏好存入 MEMORY.md（按评分与 store 约束过滤）"""
    stored = 0
    stored_items: List[PreferenceExtraction] = []
    for pref in result.preferences:
        if not _should_store_preference(pref, policy):
            continue
        key = pref.key.strip()
        if not key:
            key = "preference"
        try:
            memory_runtime.upsert_preference(key=key, value=pref.value, scope=pref.scope, source="memory_agent")
            stored += 1
            stored_items.append(pref)
            logger.info(f"[MemoryAgent] Preference → MEMORY.md [{pref.scope}] {key} = {pref.value}")
        except ValueError as exc:
            logger.warning(f"[MemoryAgent] Preference skipped due to invalid scope: {exc}")
    return stored, stored_items

def _store_knowledge(
    result: MemoryExtractionResult,
    session_id: str,
    policy: Dict[str, Any],
    *,
    source_run: str | None = None,
    source_message_ids: Optional[List[str]] = None,
    transcript_hash: str | None = None,
) -> tuple[int, List[KnowledgeExtraction]]:
    """Persist knowledge through the canonical transactional write contract."""
    stored = 0
    stored_items: List[KnowledgeExtraction] = []
    for fact in result.knowledge:
        if not _should_store_knowledge(fact, policy):
            continue
        relation = str(fact.relation or "new").strip().lower() or "new"
        target_fact_id = str(fact.target_fact_id or fact.overwrite_id or "").strip()
        if fact.overwrite_id:
            relation = "replace"
            knowledge_db.record_deprecated_usage("MemoryAgent.overwrite_id", context=fact.overwrite_id)
            logger.warning("[MemoryAgent] overwrite_id is deprecated; mapped to replace for %s", fact.overwrite_id)
        if relation not in {"new", "reinforce", "replace", "refine", "conflict"}:
            relation = "new"
        try:
            write_result = memory_runtime.write_knowledge(
                fact=fact.fact,
                category=fact.category,
                scope=fact.scope,
                relation=relation,
                target_fact_id=target_fact_id or None,
                source_session=session_id,
                source_run=source_run,
                source_message_ids=source_message_ids,
                transcript_hash=transcript_hash,
                maintainer_source="memory_agent",
                confidence=fact.confidence,
                importance=fact.importance,
                durability=_normalize_durability(fact.durability, default="operational"),
            )
            fact.persisted_fact_id = str(write_result.get("canonicalFactId") or write_result.get("factId") or "")
            stored += 1
            stored_items.append(fact)
            logger.info(
                "[MemoryAgent] Knowledge %s [%s] -> %s",
                write_result.get("action"),
                fact.scope,
                fact.persisted_fact_id,
            )
        except ValueError as exc:
            logger.warning(f"[MemoryAgent] Knowledge skipped due to invalid relation/scope: {exc}")
    return stored, stored_items

def _store_workflow_episodes(
    result: MemoryExtractionResult,
    *,
    session_id: str,
    run_handle: Any | None,
    effective_memory_scope: str,
    memory_policy: str,
    evidence_payloads: Optional[List[Dict[str, Any]]] = None,
) -> tuple[int, List[Dict[str, Any]]]:
    """Store reusable procedural workflow episodes separately from factual knowledge."""
    if memory_policy != "durable":
        return 0, []
    workflow_items = list(getattr(result, "workflow_episodes", []) or [])
    evidence_items = list(evidence_payloads or [])
    if not workflow_items:
        workflow_items = []
    if not workflow_items and not evidence_items:
        return 0, []
    stored: List[Dict[str, Any]] = []
    run_id = getattr(run_handle, "run_id", None)
    payloads: List[tuple[Dict[str, Any], str]] = []
    for item in evidence_items:
        if isinstance(item, dict):
            payloads.append((item, "runtime_evidence"))
    for item in workflow_items:
        payload = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        payloads.append((payload, "memory_agent"))
    for payload, extraction_source in payloads:
        try:
            record = memory_runtime.record_workflow_episode(
                payload=payload,
                session_id=session_id,
                run_id=run_id,
                scope=effective_memory_scope,
                extraction_source=extraction_source,
            )
            episode = record.get("episode") or {}
            candidate = record.get("candidate") or {}
            stored.append({"episode": episode, "candidate": candidate})
            _emit_memory_event(
                run_handle,
                "memory.workflow.episode_extracted",
                {
                    "session_id": session_id,
                    "episode_id": episode.get("id"),
                    "candidate_id": candidate.get("id"),
                    "task_family": episode.get("task_family"),
                    "status": episode.get("status"),
                    "candidate_status": candidate.get("status"),
                    "extraction_source": extraction_source,
                    "risk_tier": candidate.get("riskTier") or candidate.get("risk_tier"),
                },
            )
            _emit_memory_event(
                run_handle,
                "memory.workflow.candidate_updated",
                {
                    "candidate_id": candidate.get("id"),
                    "task_family": candidate.get("task_family"),
                    "status": candidate.get("status"),
                    "success_count": candidate.get("success_count"),
                    "correction_count": candidate.get("correction_count"),
                    "negative_feedback_count": candidate.get("negative_feedback_count"),
                },
            )
            if str(candidate.get("status") or "") == "quarantine":
                _emit_memory_event(
                    run_handle,
                    "memory.workflow.quarantined",
                    {
                        "candidate_id": candidate.get("id"),
                        "episode_id": episode.get("id"),
                        "task_family": candidate.get("task_family"),
                        "reason": "negative_feedback_or_policy",
                    },
                )
        except Exception as exc:
            logger.warning(f"[MemoryAgent] Workflow episode skipped: {exc}")
    return len(stored), stored

_GLOBAL_PROMOTION_RE = re.compile(
    r"(所有项目|全部项目|全局|所有工作区|任何工作区|跨项目|以后都|永远|默认|个人偏好|我的偏好|"
    r"all projects|global|all workspaces|any workspace|across projects|from now on|always|by default|personal preference|runtime governance|runtime contract)",
    re.IGNORECASE,
)


def _global_promotion_reason(text: str) -> str | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    if _GLOBAL_PROMOTION_RE.search(normalized):
        return "explicit_global_signal"
    lowered = normalized.lower()
    if "v8 agent os" in lowered and any(token in lowered for token in ("runtime", "governance", "contract", "主线", "治理", "契约")):
        return "v8_runtime_rule"
    return None


def _align_extraction_scopes(result: MemoryExtractionResult, effective_memory_scope: str) -> list[dict[str, Any]]:
    specific_prefixes = ("project:", "channel:", "workspace:", "external_api_thread:")
    target_scope = str(effective_memory_scope or "").strip() or "global"
    decisions: list[dict[str, Any]] = []

    def _coerce_scope(value: str, text: str, item_type: str) -> str:
        normalized = (value or "").strip()
        if target_scope == "global":
            decisions.append(
                {
                    "itemType": item_type,
                    "requestedScope": normalized or None,
                    "finalScope": "global",
                    "scopeDecision": "target_global",
                    "globalPromotionReason": "target_global",
                }
            )
            return "global"
        if normalized == "global":
            promotion_reason = _global_promotion_reason(text)
            if promotion_reason:
                decisions.append(
                    {
                        "itemType": item_type,
                        "requestedScope": normalized,
                        "finalScope": "global",
                        "scopeDecision": "global_promoted",
                        "globalPromotionReason": promotion_reason,
                    }
                )
                return "global"
            decisions.append(
                {
                    "itemType": item_type,
                    "requestedScope": normalized,
                    "finalScope": target_scope,
                    "scopeDecision": "global_rejected_to_current_scope",
                    "rejectedGlobalReason": "missing_explicit_global_signal",
                }
            )
            return target_scope
        if not normalized or normalized == "global":
            return target_scope
        if normalized.startswith(specific_prefixes) and normalized != target_scope:
            decisions.append(
                {
                    "itemType": item_type,
                    "requestedScope": normalized,
                    "finalScope": target_scope,
                    "scopeDecision": "specific_scope_rebound",
                    "rejectedGlobalReason": "specific_scope_mismatch",
                }
            )
            return target_scope
        decisions.append(
            {
                "itemType": item_type,
                "requestedScope": normalized,
                "finalScope": target_scope,
                "scopeDecision": "current_scope",
            }
        )
        return target_scope

    for pref in result.preferences:
        pref.scope = _coerce_scope(pref.scope, f"{pref.key} {pref.value}", "preference")
    for fact in result.knowledge:
        fact.scope = _coerce_scope(fact.scope, f"{fact.category} {fact.fact}", "knowledge")
    for workflow in getattr(result, "workflow_episodes", []) or []:
        if workflow.privacy_scope not in {"sensitive"}:
            workflow.privacy_scope = workflow.privacy_scope or "local_runtime"
    return decisions

def _build_knowledge_graph(
    result: MemoryExtractionResult,
    *,
    stored_knowledge_items: Optional[List[KnowledgeExtraction]] = None,
) -> Dict[str, int]:
    """
    [专业工具] 从提取结果中提取图谱实体与关系。
    通过 SQLite INSERT OR IGNORE 自动去重。
    """
    if not stored_knowledge_items:
        return {"entities": 0, "relations": 0}
    entities_added = 0
    relations_added = 0
    
    # 插入 Entities
    for entity in result.entities:
        name = entity.name.strip().lower()
        if not name:
            continue
        knowledge_db.add_entity(name, entity.type)
        entities_added += 1
        
    # 插入 Relations
    for rel in result.relations:
        subj = rel.subject.strip().lower()
        pred = rel.predicate.strip().upper()
        obj = rel.object.strip().lower()
        if not (subj and pred and obj):
            continue
        source_fact: Optional[KnowledgeExtraction] = None
        source_fact_index = int(rel.source_fact_index if rel.source_fact_index is not None else -1)
        if 0 <= source_fact_index < len(result.knowledge):
            candidate = result.knowledge[source_fact_index]
            if candidate in stored_knowledge_items:
                source_fact = candidate
        if source_fact is None and len(stored_knowledge_items) == 1:
            source_fact = stored_knowledge_items[0]
        if source_fact is None:
            matching = [
                item
                for item in stored_knowledge_items
                if subj in str(item.fact or "").lower() and obj in str(item.fact or "").lower()
            ]
            if len(matching) == 1:
                source_fact = matching[0]
        if source_fact is None or not source_fact.persisted_fact_id:
            logger.info("[MemoryAgent] Skipped ungrounded graph relation %s %s %s", subj, pred, obj)
            continue
        try:
            knowledge_db.add_scoped_relation(
                subj,
                pred,
                obj,
                scope=source_fact.scope,
                source_fact_ids=[source_fact.persisted_fact_id],
            )
            relations_added += 1
        except ValueError as exc:
            logger.info("[MemoryAgent] Skipped invalid scoped relation: %s", exc)
            
    if relations_added:
        logger.info(f"[MemoryAgent] Built {relations_added} graph relations via LLM structured extraction.")
    return {"entities": entities_added, "relations": relations_added}

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


def _update_run_metadata(run_handle: Any | None, updates: Dict[str, Any]) -> None:
    run_id = getattr(run_handle, "run_id", None)
    if not run_id:
        return
    try:
        memory_runtime.update_run_metadata(run_id, updates)
    except Exception as exc:
        logger.debug(f"[MemoryAgent] Failed to update run metadata for {run_id}: {exc}")


def _stored_preference_key(pref: PreferenceExtraction) -> tuple:
    return _item_key(pref, ["scope", "key", "value"])


def _stored_knowledge_key(fact: KnowledgeExtraction) -> tuple:
    return _item_key(fact, ["scope", "category", "fact"])


def _filter_reason_summary(
    result: MemoryExtractionResult,
    *,
    stored_preference_items: List[PreferenceExtraction],
    stored_knowledge_items: List[KnowledgeExtraction],
    policy: Dict[str, Any],
) -> Dict[str, Dict[str, int]]:
    stored_pref_keys = {_stored_preference_key(item) for item in stored_preference_items}
    stored_knowledge_keys = {_stored_knowledge_key(item) for item in stored_knowledge_items}
    reasons: Dict[str, Dict[str, int]] = {"preference": {}, "knowledge": {}}

    for pref in result.preferences:
        if _stored_preference_key(pref) in stored_pref_keys:
            continue
        _, reason = _evaluate_preference_persistence(pref, policy)
        reasons["preference"][reason] = reasons["preference"].get(reason, 0) + 1

    for fact in result.knowledge:
        if _stored_knowledge_key(fact) in stored_knowledge_keys:
            continue
        _, reason = _evaluate_knowledge_persistence(fact, policy)
        reasons["knowledge"][reason] = reasons["knowledge"].get(reason, 0) + 1

    return {kind: value for kind, value in reasons.items() if value}


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


def _effective_memory_scope(binding: Any | None, resolved_scope: str) -> str:
    if binding is not None:
        channel_type = str(getattr(binding, "channel_type", "") or "").strip()
        channel_remote_id = str(getattr(binding, "channel_remote_id", "") or "").strip()
        project_id = str(getattr(binding, "project_id", "") or "").strip()
        workspace_id = str(getattr(binding, "workspace_id", "") or "").strip()
        if channel_type and channel_remote_id:
            normalized_remote = channel_remote_id.replace(":", "_").replace("/", "_")
            return f"channel:{channel_type}:{normalized_remote}"
        if project_id:
            return f"project:{project_id}"
        if workspace_id:
            normalized_workspace = workspace_id.replace(":", "_").replace("/", "_").replace("\\", "_")
            return f"workspace:{normalized_workspace}"
    normalized_scope = str(resolved_scope or "").strip()
    if normalized_scope.startswith(("channel:", "project:", "workspace:", "external_api_thread:")):
        return normalized_scope
    return "workspace:main"


def _session_scope_hints(session_id: str) -> Dict[str, Any]:
    try:
        session = db.get_session(session_id) or {}
    except Exception:
        session = {}
    metadata = session.get("metadata") if isinstance(session, dict) else {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    workspace = metadata.get("workspace") if isinstance(metadata.get("workspace"), dict) else {}
    project = metadata.get("project") if isinstance(metadata.get("project"), dict) else {}
    channel = metadata.get("channel") if isinstance(metadata.get("channel"), dict) else {}
    return {
        "project_id": (
            metadata.get("project_id")
            or metadata.get("projectId")
            or project.get("id")
            or project.get("project_id")
        ),
        "workspace_id": (
            metadata.get("workspace_id")
            or metadata.get("workspaceId")
            or workspace.get("id")
            or workspace.get("workspace_id")
        ),
        "workspace_path": (
            metadata.get("workspace_path")
            or metadata.get("workspacePath")
            or workspace.get("path")
            or workspace.get("workspace_path")
        ),
        "workflow_id": metadata.get("workflow_id") or metadata.get("workflowId"),
        "channel_type": (
            metadata.get("channel_type")
            or metadata.get("channelType")
            or channel.get("type")
            or channel.get("channel_type")
        ),
        "channel_remote_id": (
            metadata.get("channel_remote_id")
            or metadata.get("channelRemoteId")
            or metadata.get("remote_id")
            or metadata.get("remoteId")
            or channel.get("remote_id")
            or channel.get("remoteId")
        ),
        "scope_hint": metadata.get("scope_hint") or metadata.get("scopeHint"),
    }


def _memory_source_runtime(trigger_source: str) -> str:
    normalized = str(trigger_source or "").strip().lower()
    if normalized.startswith("hook") or normalized.startswith("cron"):
        return "automation"
    if normalized.startswith("network_supervisor"):
        return "network_supervisor"
    if normalized.startswith("computer_use") or normalized.startswith("desktop_live"):
        return "computer_use"
    if normalized.startswith("chat"):
        return "chat"
    return "chat"


def _memory_provenance_class(trigger_source: str) -> str:
    normalized = str(trigger_source or "").strip().lower()
    if normalized.startswith("hook") or normalized.startswith("cron"):
        return "mechanical_automation"
    if normalized.startswith("computer_use") or normalized.startswith("desktop_live"):
        return "machine_observation"
    if normalized.startswith("approval") or normalized.startswith("safety") or normalized.startswith("govern") or normalized.startswith("wake"):
        return "governance_or_control"
    return "human_dialogue"


def _memory_policy_for_provenance(provenance_class: str) -> str:
    if provenance_class == "mechanical_automation":
        return "daily_summary_only"
    if provenance_class in {"governance_or_control", "machine_observation"}:
        return "skipped"
    return "durable"


def _append_session_log(
    result: MemoryExtractionResult,
    *,
    effective_memory_scope: str,
    session_id: str,
    source_runtime: str,
    provenance_class: str,
    memory_policy: str,
    stored_preference_items: List[PreferenceExtraction],
    stored_knowledge_items: List[KnowledgeExtraction],
    policy: Dict[str, Any],
):
    """[专业工具] 记录结构化 provenance 日志，同时维护 YAML frontmatter。"""
    content_lines = []
    stored_pref_keys = {_stored_preference_key(item) for item in stored_preference_items}
    stored_knowledge_keys = {_stored_knowledge_key(item) for item in stored_knowledge_items}

    content_lines.append("**Extracted candidates:**")
    if result.knowledge or result.preferences:
        for f in result.knowledge:
            op = "[UPDATE]" if f.overwrite_id else "[NEW]"
            content_lines.append(
                f"- [knowledge][{f.scope}][{_normalize_durability(f.durability, default='operational')}] {op} {f.fact}"
            )
        for p in result.preferences:
            content_lines.append(
                f"- [preference][{p.scope}][{_normalize_durability(p.durability, default='stable')}] {p.key}: {p.value}"
            )
    else:
        content_lines.append("- none")

    content_lines.append("")
    content_lines.append("**Persisted long-term memory:**")
    if stored_knowledge_items or stored_preference_items:
        for f in stored_knowledge_items:
            content_lines.append(f"- [knowledge][{f.scope}] {f.fact}")
        for p in stored_preference_items:
            content_lines.append(f"- [preference][{p.scope}] {p.key}: {p.value}")
    else:
        content_lines.append("- none")

    content_lines.append("")
    content_lines.append("**Filtered out (policy reason):**")
    filtered_lines = 0
    for f in result.knowledge:
        if _stored_knowledge_key(f) in stored_knowledge_keys:
            continue
        _, reason = _evaluate_knowledge_persistence(f, policy)
        content_lines.append(f"- [knowledge][{f.scope}][{reason}] {f.fact}")
        filtered_lines += 1
    for p in result.preferences:
        if _stored_preference_key(p) in stored_pref_keys:
            continue
        _, reason = _evaluate_preference_persistence(p, policy)
        content_lines.append(f"- [preference][{p.scope}][{reason}] {p.key}: {p.value}")
        filtered_lines += 1
    if filtered_lines <= 0:
        content_lines.append("- none")

    if memory_policy == "daily_summary_only":
        content_lines = ["Automation/Hook/Cron provenance retained as daily summary only."]
    elif memory_policy == "skipped":
        content_lines = ["Skipped long-term memory write due to provenance policy."]

    full_content = (
        f"Session `{session_id[:8]}`\n"
        f"**Summary**: {result.summary}\n\n" + "\n".join(content_lines)
    )

    entry_metadata = {
        "session_id": session_id,
        "effective_memory_scope": effective_memory_scope,
        "source_runtime": source_runtime,
        "provenance_class": provenance_class,
        "memory_policy": memory_policy,
        "extracted_preference_count": len(result.preferences),
        "extracted_knowledge_count": len(result.knowledge),
        "extracted_workflow_episode_count": len(getattr(result, "workflow_episodes", []) or []),
        "persisted_preference_count": len(stored_preference_items),
        "persisted_knowledge_count": len(stored_knowledge_items),
        "persisted_operational_workflow_count": sum(
            1 for item in stored_knowledge_items if _is_operational_learning_fact(item)
        ),
        "filtered_preference_count": max(0, len(result.preferences) - len(stored_preference_items)),
        "filtered_knowledge_count": max(0, len(result.knowledge) - len(stored_knowledge_items)),
        "filter_reasons": _filter_reason_summary(
            result,
            stored_preference_items=stored_preference_items,
            stored_knowledge_items=stored_knowledge_items,
            policy=policy,
        ),
    }

    if hasattr(memory_runtime, "append_daily_log_with_yaml"):
        memory_runtime.append_daily_log_with_yaml(
            content=full_content,
            session_summary=result.summary,
            session_tags=result.tags,
            entry_metadata=entry_metadata,
        )
    else:
        memory_runtime.append_daily_log(
            content=full_content,
            tags=result.tags,
        )

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

    # 1. 获取 canonical transcript：优先 runtime projection，其次 durable messages。
    durable_messages = db.get_messages(session_id)
    transcript = _build_canonical_session_transcript(session_id, durable_messages)
    transcript_entries = transcript["entries"]
    if not transcript_entries:
        logger.info(f"[MemoryAgent] No transcript entries for session {session_id}, skipping.")
        _update_run_metadata(
            run_handle,
            {
                "memory_extraction": {
                    "status": "skipped",
                    "skipReason": "no_messages",
                    "transcriptSource": transcript["source"],
                    "latestSeq": transcript["latest_seq"],
                }
            },
        )
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="SKIPPED",
            details=(
                f"Session {session_id[:8]} skipped: no_messages "
                f"(source={transcript['source']}, durable={transcript['durable_message_count']}, "
                f"events={transcript['runtime_event_count']})"
            ),
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": "no_messages",
                "parent_run_id": parent_run_id,
                "transcript_source": transcript["source"],
                "durable_message_count": transcript["durable_message_count"],
                "runtime_event_count": transcript["runtime_event_count"],
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "no_messages",
            "parent_run_id": parent_run_id,
            "transcript_source": transcript["source"],
        }

    if int(transcript.get("user_message_count") or 0) <= 0:
        logger.info(f"[MemoryAgent] No user transcript entries for session {session_id}, skipping.")
        _update_run_metadata(
            run_handle,
            {
                "memory_extraction": {
                    "status": "skipped",
                    "skipReason": "no_user_message",
                    "transcriptSource": transcript["source"],
                    "latestSeq": transcript["latest_seq"],
                    "entryCount": len(transcript_entries),
                }
            },
        )
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="SKIPPED",
            details=f"Session {session_id[:8]} skipped: no_user_message (source={transcript['source']}, entries={len(transcript_entries)})",
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": "no_user_message",
                "parent_run_id": parent_run_id,
                "transcript_source": transcript["source"],
                "entry_count": len(transcript_entries),
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "no_user_message",
            "parent_run_id": parent_run_id,
            "transcript_source": transcript["source"],
            "entry_count": len(transcript_entries),
        }
    
    incremental_messages, extraction_state, extraction_mode = _resolve_incremental_messages(
        session_id=session_id,
        messages=transcript_entries,
    )
    if not incremental_messages:
        logger.info(f"[MemoryAgent] No incremental messages for session {session_id}, skipping extraction.")
        _update_run_metadata(
            run_handle,
            {
                "memory_extraction": {
                    "status": "skipped",
                    "skipReason": extraction_mode,
                    "transcriptSource": transcript["source"],
                    "latestSeq": transcript["latest_seq"],
                    "entryCount": len(transcript_entries),
                }
            },
        )
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="SKIPPED",
            details=(
                f"Session {session_id[:8]} skipped: {extraction_mode} "
                f"(source={transcript['source']}, entries={len(transcript_entries)}, seq={transcript['latest_seq']})"
            ),
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": extraction_mode,
                "parent_run_id": parent_run_id,
                "message_count": len(transcript_entries),
                "transcript_source": transcript["source"],
                "latest_seq": transcript["latest_seq"],
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": extraction_mode,
            "parent_run_id": parent_run_id,
            "message_count": len(transcript_entries),
            "transcript_source": transcript["source"],
            "latest_seq": transcript["latest_seq"],
        }

    chat_history_text = ""
    for msg in incremental_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            chat_history_text += f"{role.upper()}: {content}\n"

    incremental_message_ids = {
        str(msg.get("id") or "").strip()
        for msg in incremental_messages
        if str(msg.get("id") or "").strip()
    }
    incremental_durable_messages = [
        item
        for item in durable_messages
        if isinstance(item, dict) and str(item.get("id") or "").strip() in incremental_message_ids
    ]
    visual_enrichment_block, visual_enrichment = _build_memory_visual_enrichment(
        session_id=session_id,
        durable_messages=incremental_durable_messages,
        transcript_entries=incremental_messages,
        transcript_text=str(transcript.get("text") or ""),
        run_handle=run_handle,
    )
    if visual_enrichment.get("scannedMediaCount") or visual_enrichment.get("enrichedCount") or visual_enrichment.get("errorCount"):
        _update_run_metadata(
            run_handle,
            {
                "memory_visual_enrichment": visual_enrichment,
            },
        )
        _emit_memory_event(
            run_handle,
            "memory.visual_enrichment.completed",
            {
                "session_id": session_id,
                "parent_run_id": parent_run_id,
                **visual_enrichment,
            },
        )
    if visual_enrichment_block:
        chat_history_text += visual_enrichment_block
    
    if len(chat_history_text.strip()) < 50:
        logger.info(f"[MemoryAgent] Session too short, skipping extraction.")
        _update_run_metadata(
            run_handle,
            {
                "memory_extraction": {
                    "status": "skipped",
                    "skipReason": "no_semantic_content",
                    "transcriptSource": transcript["source"],
                    "latestSeq": transcript["latest_seq"],
                    "entryCount": len(incremental_messages),
                    "contentLength": len(chat_history_text.strip()),
                    "visualEnrichment": visual_enrichment,
                }
            },
        )
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="SKIPPED",
            details=f"Session {session_id[:8]} skipped: no_semantic_content (< 50 chars, source={transcript['source']})"
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.skipped",
            {
                "session_id": session_id,
                "reason": "no_semantic_content",
                "content_length": len(chat_history_text.strip()),
                "parent_run_id": parent_run_id,
                "transcript_source": transcript["source"],
                "entry_count": len(incremental_messages),
                "visual_enrichment": visual_enrichment,
            },
        )
        return {
            "status": "skipped",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "no_semantic_content",
            "content_length": len(chat_history_text.strip()),
            "parent_run_id": parent_run_id,
            "transcript_source": transcript["source"],
            "visual_enrichment": visual_enrichment,
        }
    
    logger.info(f"[MemoryAgent] === System Command: Analyze Session {session_id[:8]} ===")
    _update_run_metadata(
        run_handle,
        {
            "memory_extraction": {
                "status": "running",
                "transcriptSource": transcript["source"],
                "latestSeq": transcript["latest_seq"],
                "entryCount": len(transcript_entries),
                "incrementalEntryCount": len(incremental_messages),
                "extractionMode": extraction_mode,
                "contentLength": len(chat_history_text.strip()),
                "visualEnrichment": visual_enrichment,
            }
        },
    )
    _emit_memory_event(
        run_handle,
        "memory.session_extraction.started",
            {
                "session_id": session_id,
                "trigger_source": trigger_source,
                "parent_run_id": parent_run_id,
                "message_count": len(incremental_messages),
                "canonical_entry_count": len(transcript_entries),
                "transcript_source": transcript["source"],
                "latest_seq": transcript["latest_seq"],
                "extraction_mode": extraction_mode,
                "previous_checkpoint": extraction_state or {},
                "visual_enrichment": visual_enrichment,
            },
        )
    
    binding = session_scope_binding_service.get_binding(session_id)
    if binding and binding.status == "active":
        scope = binding.resolved_scope
        scope_chain = build_scope_chain(
            resolved_scope=binding.resolved_scope,
            channel_type=binding.channel_type,
            channel_remote_id=binding.channel_remote_id,
            workspace_id=binding.workspace_id,
            project_id=binding.project_id,
            workflow_id=binding.workflow_id,
        )
    else:
        scope_hints = _session_scope_hints(session_id)
        resolved = scope_resolution_service.resolve(
            session_id=session_id,
            conversation_id=session_id,
            user_query=chat_history_text,
            scope_mode="explicit",
            project_id=scope_hints.get("project_id"),
            workspace_id=scope_hints.get("workspace_id"),
            workspace_path=scope_hints.get("workspace_path"),
            workflow_id=scope_hints.get("workflow_id"),
            channel_type=scope_hints.get("channel_type"),
            channel_remote_id=scope_hints.get("channel_remote_id"),
            scope_hint=scope_hints.get("scope_hint"),
        )
        binding = resolved.binding
        scope = binding.resolved_scope
        scope_chain = resolved.scope_chain
    effective_memory_scope = _effective_memory_scope(binding, scope)
    source_runtime = _memory_source_runtime(trigger_source)
    provenance_class = _memory_provenance_class(trigger_source)
    memory_policy = _memory_policy_for_provenance(provenance_class)
    _emit_memory_event(
        run_handle,
        "memory.scope.resolved",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "scope_chain": scope_chain,
            "project_id": binding.project_id,
            "workspace_id": binding.workspace_id,
            "parent_run_id": parent_run_id,
            "source_runtime": source_runtime,
            "provenance_class": provenance_class,
            "memory_policy": memory_policy,
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
            "effective_memory_scope": effective_memory_scope,
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
            "effective_memory_scope": effective_memory_scope,
            "query": quick_summary,
            "result_count": len(past_knowledge) if "past_knowledge" in locals() else 0,
        },
    )

    workflow_evidence_payloads: List[Dict[str, Any]] = []
    try:
        workflow_evidence_payloads = memory_runtime.collect_workflow_evidence(
            session_id=session_id,
            run_id=parent_run_id,
        )
    except Exception as exc:
        logger.warning(f"[MemoryAgent] Workflow evidence collection failed: {exc}")
        workflow_evidence_payloads = []
    _emit_memory_event(
        run_handle,
        "memory.workflow.evidence_collected",
        {
            "session_id": session_id,
            "parent_run_id": parent_run_id,
            "episode_draft_count": len(workflow_evidence_payloads),
            "evidence_action_count": sum(
                int(((item.get("evidenceSummary") or {}).get("eventCount")) or 0)
                for item in workflow_evidence_payloads
                if isinstance(item, dict)
            ),
        },
    )
    
    # 4. LLM 结构化提取
    try:
        extraction_attempt = _extract_with_llm(
            chat_history_text,
            context_text,
            resolved_scope=scope,
            scope_chain=scope_chain,
        )
    except Exception as e:
        logger.warning(f"[MemoryAgent] LLM extraction error: {e}")
        _update_run_metadata(
            run_handle,
            {
                "memory_extraction": {
                    "status": "failed",
                    "extractionFailureStage": "extractor_error",
                    "extractionFailureReason": str(e),
                    "resolvedScope": scope,
                    "effectiveMemoryScope": effective_memory_scope,
                    "transcriptSource": transcript["source"],
                    "latestSeq": transcript["latest_seq"],
                }
            },
        )
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
                "reason": "extractor_error",
                "error": str(e),
                "resolved_scope": scope,
            },
        )
        return {
            "status": "failed",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": "extractor_error",
            "error": str(e),
            "resolved_scope": scope,
        }
        
    result = extraction_attempt.result
    if result is None:
        failure_stage = extraction_attempt.failure_stage or "llm_response_empty"
        failure_reason = extraction_attempt.failure_reason or "Extractor returned no structured result."
        logger.warning(f"[MemoryAgent] LLM failed to return valid extraction. stage={failure_stage} reason={failure_reason}")
        _update_run_metadata(
            run_handle,
            {
                "memory_extraction": {
                    "status": "failed",
                    "extractionFailureStage": failure_stage,
                    "extractionFailureReason": failure_reason,
                    "extractorModel": extraction_attempt.extractor_model or None,
                    "rawOutputPreview": extraction_attempt.raw_output_preview or None,
                    "parserErrorPreview": extraction_attempt.parser_error_preview or None,
                    "resolvedScope": scope,
                    "effectiveMemoryScope": effective_memory_scope,
                    "memoryPolicy": memory_policy,
                    "provenanceClass": provenance_class,
                    "transcriptSource": transcript["source"],
                    "latestSeq": transcript["latest_seq"],
                }
            },
        )
        audit_logger.log(
            source_type=source_type,
            action=f"Memory Agent: Session Extraction",
            status="FAILED",
            details=f"LLM extraction failed for session {session_id[:8]} ({failure_stage}): {failure_reason}"
        )
        _emit_memory_event(
            run_handle,
            "memory.session_extraction.failed",
            {
                "session_id": session_id,
                "reason": failure_stage,
                "resolved_scope": scope,
                "effective_memory_scope": effective_memory_scope,
                "extractionFailureStage": failure_stage,
                "extractionFailureReason": failure_reason,
                "extractorModel": extraction_attempt.extractor_model or None,
                "rawOutputPreview": extraction_attempt.raw_output_preview or None,
                "parserErrorPreview": extraction_attempt.parser_error_preview or None,
                "source_runtime": source_runtime,
                "provenance_class": provenance_class,
                "memory_policy": memory_policy,
            },
        )
        return {
            "status": "failed",
            "task_kind": "session_extraction",
            "session_id": session_id,
            "reason": failure_stage,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "extractionFailureStage": failure_stage,
            "extractionFailureReason": failure_reason,
            "extractorModel": extraction_attempt.extractor_model or None,
            "rawOutputPreview": extraction_attempt.raw_output_preview or None,
            "parserErrorPreview": extraction_attempt.parser_error_preview or None,
        }

    scope_decisions = _align_extraction_scopes(result, effective_memory_scope)
    canonicalization = canonicalize_memory_extraction_result(result)
    _emit_memory_event(
        run_handle,
        "memory.extraction.completed",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "summary": result.summary,
            "tags": result.tags,
            "preference_count": len(result.preferences),
            "knowledge_count": len(result.knowledge),
            "workflow_episode_count": len(getattr(result, "workflow_episodes", []) or []),
            "entity_count": len(result.entities),
            "relation_count": len(result.relations),
            "extraction_mode": extraction_mode,
            "source_runtime": source_runtime,
            "provenance_class": provenance_class,
            "memory_policy": memory_policy,
            "extractorModel": extraction_attempt.extractor_model or None,
            "visualEnrichment": visual_enrichment,
            "scopeDecisions": scope_decisions[:20],
            "canonicalization": canonicalization,
            "rawOutputPreview": extraction_attempt.raw_output_preview or None,
            "parserErrorPreview": extraction_attempt.parser_error_preview or None,
            "extractionFailureStage": None,
            "extractionFailureReason": None,
        },
    )
       
    # 5. 分别落库
    stored_preferences = 0
    stored_knowledge = 0
    stored_preference_items: List[PreferenceExtraction] = []
    stored_knowledge_items: List[KnowledgeExtraction] = []
    stored_workflows = 0
    stored_workflow_records: List[Dict[str, Any]] = []
    graph_stats = {"entities": 0, "relations": 0}
    if memory_policy == "durable":
        stored_preferences, stored_preference_items = _store_preferences(result, policy)
        stored_knowledge, stored_knowledge_items = _store_knowledge(
            result,
            session_id,
            policy,
            source_run=getattr(run_handle, "run_id", None),
            source_message_ids=sorted(incremental_message_ids),
            transcript_hash=hashlib.sha256(chat_history_text.encode("utf-8")).hexdigest(),
        )
        stored_workflows, stored_workflow_records = _store_workflow_episodes(
            result,
            session_id=session_id,
            run_handle=run_handle,
            effective_memory_scope=effective_memory_scope,
            memory_policy=memory_policy,
            evidence_payloads=workflow_evidence_payloads,
        )
        graph_stats = _build_knowledge_graph(result, stored_knowledge_items=stored_knowledge_items)
    filter_reasons = _filter_reason_summary(
        result,
        stored_preference_items=stored_preference_items,
        stored_knowledge_items=stored_knowledge_items,
        policy=policy,
    )
    persisted_operational_workflows = sum(
        1 for item in stored_knowledge_items if _is_operational_learning_fact(item)
    )
    filtered_preferences = max(0, len(result.preferences) - len(stored_preference_items))
    filtered_knowledge = max(0, len(result.knowledge) - len(stored_knowledge_items))
    _emit_memory_event(
        run_handle,
        "memory.preferences.updated",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "count": stored_preferences,
            "memory_policy": memory_policy,
        },
    )
    _emit_memory_event(
        run_handle,
        "memory.knowledge.upserted",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "count": stored_knowledge,
            "memory_policy": memory_policy,
        },
    )
    _emit_memory_event(
        run_handle,
        "memory.graph.updated",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "entity_count": graph_stats["entities"],
            "relation_count": graph_stats["relations"],
            "persisted_entity_count": graph_stats["entities"],
            "persisted_relation_count": graph_stats["relations"],
            "memory_policy": memory_policy,
        },
    )
    _append_session_log(
        result,
        effective_memory_scope=effective_memory_scope,
        session_id=session_id,
        source_runtime=source_runtime,
        provenance_class=provenance_class,
        memory_policy=memory_policy,
        stored_preference_items=stored_preference_items,
        stored_knowledge_items=stored_knowledge_items,
        policy=policy,
    )
    _emit_memory_event(
        run_handle,
        "memory.daily_log.appended",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "summary": result.summary,
            "tags": result.tags,
            "source_runtime": source_runtime,
            "provenance_class": provenance_class,
            "memory_policy": memory_policy,
            "extractorModel": extraction_attempt.extractor_model or None,
            "rawOutputPreview": extraction_attempt.raw_output_preview or None,
            "parserErrorPreview": extraction_attempt.parser_error_preview or None,
            "extractionFailureStage": None,
            "extractionFailureReason": None,
        },
    )
    
    # 增量 FTS5 刷新
    _run_incremental_index()
    no_persisted_memory_reason = ""
    if memory_policy == "daily_summary_only":
        no_persisted_memory_reason = "daily_summary_only"
    elif memory_policy == "skipped":
        no_persisted_memory_reason = "skipped"
    elif stored_preferences + stored_knowledge + stored_workflows <= 0:
        extracted_memory_items = len(result.preferences) + len(result.knowledge) + len(getattr(result, "workflow_episodes", []) or [])
        no_persisted_memory_reason = "policy_filtered" if extracted_memory_items > 0 else "model_empty"
    current_hash = _message_hash(transcript_entries)
    last_entry = transcript_entries[-1] if transcript_entries else {}
    memory_runtime.save_extraction_state(
        session_id=session_id,
        last_processed_message_id=last_entry.get("id"),
        last_processed_message_count=len(transcript_entries),
        last_content_hash=current_hash,
        last_run_id=getattr(run_handle, "run_id", None),
        last_processed_at=_utc_now_iso(),
    )
    _update_run_metadata(
        run_handle,
        {
            "memory_extraction": {
                "status": "completed",
                "summary": result.summary,
                "resolvedScope": scope,
                "effectiveMemoryScope": effective_memory_scope,
                "extractorModel": extraction_attempt.extractor_model or None,
                "rawOutputPreview": extraction_attempt.raw_output_preview or None,
                "parserErrorPreview": extraction_attempt.parser_error_preview or None,
                "extractionFailureStage": None,
                "extractionFailureReason": None,
                "extractedPreferenceCount": len(result.preferences),
                "extractedKnowledgeCount": len(result.knowledge),
                "extractedWorkflowEpisodeCount": len(getattr(result, "workflow_episodes", []) or []),
                "extractedEntityCount": len(result.entities),
                "extractedRelationCount": len(result.relations),
                "persistedPreferenceCount": stored_preferences,
                "persistedKnowledgeCount": stored_knowledge,
                "persistedWorkflowEpisodeCount": stored_workflows,
                "persistedWorkflowCandidateIds": [
                    ((item.get("candidate") or {}).get("id"))
                    for item in stored_workflow_records
                    if isinstance(item, dict)
                ],
                "persistedOperationalWorkflowCount": persisted_operational_workflows,
                "persistedEntityCount": graph_stats["entities"],
                "persistedRelationCount": graph_stats["relations"],
                "filteredPreferenceCount": filtered_preferences,
                "filteredKnowledgeCount": filtered_knowledge,
                "filterReasons": filter_reasons,
                "noPersistedMemoryReason": no_persisted_memory_reason or None,
                "memoryPolicy": memory_policy,
                "provenanceClass": provenance_class,
                "sourceRuntime": source_runtime,
                "visualEnrichment": visual_enrichment,
                "canonicalization": canonicalization,
                "transcriptSource": transcript["source"],
                "latestSeq": transcript["latest_seq"],
                "extractionMode": extraction_mode,
            }
        },
    )
    _emit_memory_event(
        run_handle,
        "memory.session_extraction.finished",
        {
            "session_id": session_id,
            "resolved_scope": scope,
            "effective_memory_scope": effective_memory_scope,
            "summary": result.summary,
            "tags": result.tags,
            "preference_count": stored_preferences,
            "knowledge_count": stored_knowledge,
            "entity_count": graph_stats["entities"],
            "relation_count": graph_stats["relations"],
            "extracted_preference_count": len(result.preferences),
            "extracted_knowledge_count": len(result.knowledge),
            "extracted_workflow_episode_count": len(getattr(result, "workflow_episodes", []) or []),
            "extracted_entity_count": len(result.entities),
            "extracted_relation_count": len(result.relations),
            "persisted_preference_count": stored_preferences,
            "persisted_knowledge_count": stored_knowledge,
            "persisted_workflow_episode_count": stored_workflows,
            "persisted_operational_workflow_count": persisted_operational_workflows,
            "persisted_entity_count": graph_stats["entities"],
            "persisted_relation_count": graph_stats["relations"],
            "filtered_preference_count": filtered_preferences,
            "filtered_knowledge_count": filtered_knowledge,
            "filter_reasons": filter_reasons,
            "status": "completed",
            "extraction_mode": extraction_mode,
            "transcript_source": transcript["source"],
            "latest_seq": transcript["latest_seq"],
            "no_persisted_memory_reason": no_persisted_memory_reason or None,
            "source_runtime": source_runtime,
            "provenance_class": provenance_class,
            "memory_policy": memory_policy,
            "canonicalization": canonicalization,
        },
    )
    
    logger.info(
        f"[MemoryAgent] === Complete: "
        f"{len(result.knowledge)} facts, "
        f"{len(result.preferences)} prefs, "
        f"{len(getattr(result, 'workflow_episodes', []) or [])} workflow episodes, "
        f"{len(result.relations)} relations extracted. ==="
    )
    
    audit_logger.log(
        source_type=source_type,
        action=f"Memory Agent: Session Extraction",
        status="SUCCESS",
        details=(
            f"Session {session_id[:8]} => extracted {len(result.knowledge)} facts, "
            f"{len(result.preferences)} prefs, {len(getattr(result, 'workflow_episodes', []) or [])} workflow episodes, {len(result.relations)} relations; "
            f"persisted {stored_knowledge} facts, {stored_preferences} prefs, {stored_workflows} workflows, "
            f"{graph_stats['relations']} graph relations. "
            f"source={transcript['source']}, entries={len(transcript_entries)}, "
            f"seq={transcript['latest_seq']}"
            + (f", no_persisted_memory_reason={no_persisted_memory_reason}" if no_persisted_memory_reason else "")
            + f", effective_memory_scope={effective_memory_scope}, provenance_class={provenance_class}, memory_policy={memory_policy}"
        )
    )
    return {
        "status": "completed",
        "task_kind": "session_extraction",
        "session_id": session_id,
        "resolved_scope": scope,
        "effective_memory_scope": effective_memory_scope,
        "summary": result.summary,
        "tags": result.tags,
        "preference_count": stored_preferences,
        "knowledge_count": stored_knowledge,
        "entity_count": graph_stats["entities"],
        "relation_count": graph_stats["relations"],
        "extracted_preference_count": len(result.preferences),
        "extracted_knowledge_count": len(result.knowledge),
        "extracted_workflow_episode_count": len(getattr(result, "workflow_episodes", []) or []),
        "extracted_entity_count": len(result.entities),
        "extracted_relation_count": len(result.relations),
        "persisted_preference_count": stored_preferences,
        "persisted_knowledge_count": stored_knowledge,
        "persisted_workflow_episode_count": stored_workflows,
        "persisted_operational_workflow_count": persisted_operational_workflows,
        "persisted_entity_count": graph_stats["entities"],
        "persisted_relation_count": graph_stats["relations"],
        "filtered_preference_count": filtered_preferences,
        "filtered_knowledge_count": filtered_knowledge,
        "filter_reasons": filter_reasons,
        "parent_run_id": parent_run_id,
        "extraction_mode": extraction_mode,
        "transcript_source": transcript["source"],
        "latest_seq": transcript["latest_seq"],
        "message_count": len(incremental_messages),
        "content_length": len(chat_history_text.strip()),
        "no_persisted_memory_reason": no_persisted_memory_reason or None,
        "source_runtime": source_runtime,
        "provenance_class": provenance_class,
        "memory_policy": memory_policy,
        "canonicalization": canonicalization,
        "visual_enrichment": visual_enrichment,
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
    if tier in {"week", "month", "year"}:
        content = memory_runtime.get_logs_for_period(tier=tier, dt=dt, scope_chain=["global"])
    daily_log_count = content.count("] Ref: memory://day/")
        
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
            "daily_log_count": 0,
            "input_content_length": 0,
        }
        
    try:
        payload = await _synthesize_periodic_summary_payload(tier=tier, content=content)
        normalized_payload = _normalize_periodic_summary_payload(tier=tier, payload=payload)

        memory_runtime.save_periodic_summary(tier=tier, payload=normalized_payload, dt=dt)
        logger.info(f"[MemoryAgent] Successfully generated and saved {tier} summary.")
        _emit_memory_event(
            run_handle,
            "memory.summary.saved",
            {
                "tier": tier,
                "target_date": dt.isoformat(),
                "content_length": len(normalized_payload["body"] or ""),
                "summary_length": len(normalized_payload["summary"] or ""),
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
            "daily_log_count": daily_log_count,
            "input_content_length": len(content),
            "content_length": len(normalized_payload["body"] or ""),
            "summary_length": len(normalized_payload["summary"] or ""),
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
            "daily_log_count": daily_log_count,
            "input_content_length": len(content),
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
