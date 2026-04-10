from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.context_durable_flush import flush_before_context_compaction
from core.llm_factory import llm_factory
from core.storage import storage


_ALLOWED_BLOCK_TYPES = {
    "history_summary",
    "channel_memory",
    "automation_memory",
    "recent_messages",
    "memory_recall",
}

_GOAL_KEYWORDS = (
    "目标",
    "需求",
    "要求",
    "必须",
    "请",
    "final",
    "最终",
    "deliver",
    "constraint",
    "限制",
    "约束",
    "路径",
    "目录",
    "url",
    "链接",
)
_BLOCKER_KEYWORDS = (
    "失败",
    "错误",
    "异常",
    "阻塞",
    "卡住",
    "无法",
    "approve",
    "approval",
    "reject",
    "审批",
    "确认",
    "中断",
    "retry",
)
_DECISION_KEYWORDS = (
    "决定",
    "结论",
    "已完成",
    "完成",
    "下一步",
    "方案",
    "建议",
    "修复",
    "implemented",
    "resolved",
    "updated",
)
_GOVERNANCE_KEYWORDS = (
    "handoff",
    "lane",
    "snapshot",
    "projection",
    "runtime",
    "workflow",
    "ledger",
    "event",
    "治理",
    "恢复",
    "快照",
)


@dataclass
class ContextBlock:
    type: str
    title: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedContext:
    messages: List[BaseMessage]
    blocks: List[ContextBlock]
    audit: Dict[str, Any]


class ContextOrchestrator:
    def __init__(self):
        self._summary_cache: Dict[str, ContextBlock] = {}

    def prepare(
        self,
        *,
        messages: Sequence[BaseMessage],
        runtime_kind: str,
        target_role: str,
        resolved_model_id: str | None = None,
        resolved_scope: str | None = None,
        scope_chain: Sequence[str] | None = None,
        leading_system_content: str | None = None,
        keep_recent_override: int | None = None,
        extra_blocks: Sequence[Dict[str, Any] | ContextBlock] | None = None,
    ) -> PreparedContext:
        policy = storage.get_context_config() or {}
        compression = dict(policy.get("compression") or {})
        context_window = (
            llm_factory.get_model_context_window(str(resolved_model_id or "").strip())
            or int(compression.get("default_context_window_tokens") or 32000)
        )

        cleaned_messages, adapter_blocks = self._extract_adapter_blocks(messages)
        if extra_blocks:
            adapter_blocks.extend(self._coerce_blocks(extra_blocks))

        rendered_messages: List[BaseMessage] = []
        if leading_system_content:
            rendered_messages.append(SystemMessage(content=leading_system_content))

        system_messages = [message for message in cleaned_messages if isinstance(message, SystemMessage)]
        non_system_messages = [message for message in cleaned_messages if not isinstance(message, SystemMessage)]
        rendered_messages.extend(system_messages)

        estimated_input_tokens = self._estimate_messages_tokens(rendered_messages + non_system_messages)
        trigger_reason = "disabled"
        compaction_applied = False
        history_block: ContextBlock | None = None
        method = "none"
        keep_recent_messages = int(keep_recent_override or compression.get("keep_recent_messages") or 6)
        durable_flush: Dict[str, Any] | None = None

        if compression.get("enabled", True):
            soft_limit = max(1, int(context_window * float(compression.get("soft_trigger_ratio") or 0.55)))
            hard_limit = max(1, int(context_window * float(compression.get("hard_trigger_ratio") or 0.75)))
            should_compact = estimated_input_tokens >= soft_limit
            trigger_reason = "soft_token_budget" if should_compact else "within_budget"
            if estimated_input_tokens > hard_limit:
                trigger_reason = "hard_token_budget"

            if should_compact:
                try:
                    durable_flush = flush_before_context_compaction(non_system_messages)
                except Exception as exc:
                    durable_flush = {
                        "ok": False,
                        "skipped": False,
                        "reason": f"flush_failed:{exc}",
                    }
                if durable_flush.get("ok", False):
                    summary_mode = "llm" if estimated_input_tokens > hard_limit else "rule"
                    non_system_messages, history_block, method = self._compact_non_system_messages(
                        messages=non_system_messages,
                        keep_recent_messages=keep_recent_messages,
                        compression=compression,
                        target_role=target_role,
                        resolved_model_id=resolved_model_id,
                        summary_mode=summary_mode,
                    )
                    compaction_applied = history_block is not None
                else:
                    trigger_reason = "pre_compaction_flush_failed"

        blocks: List[ContextBlock] = []
        if history_block is not None:
            blocks.append(history_block)
        blocks.extend(adapter_blocks)

        for block in blocks:
            rendered_messages.append(self._render_block_message(block))
        rendered_messages.extend(non_system_messages)

        estimated_final_tokens = self._estimate_messages_tokens(rendered_messages)
        explicit_scope = str(resolved_scope or "").strip()
        explicit_scope_chain = [str(item).strip() for item in (scope_chain or []) if str(item).strip()]
        if explicit_scope and explicit_scope not in explicit_scope_chain:
            explicit_scope_chain.append(explicit_scope)
        if explicit_scope or explicit_scope_chain:
            resolved_scope, scope_chain = explicit_scope, explicit_scope_chain
        else:
            resolved_scope, scope_chain = self._extract_scope_context(cleaned_messages)
        recall_audit = self._extract_recall_audit(cleaned_messages)
        audit = {
            "context_policy_version": policy.get("schema_version", 1),
            "runtime_kind": runtime_kind,
            "target_role": target_role,
            "resolved_model_id": str(resolved_model_id or "").strip(),
            "context_window_tokens": context_window,
            "original_message_count": len(messages),
            "estimated_input_tokens": estimated_input_tokens,
            "trigger_reason": trigger_reason,
            "compaction_applied": compaction_applied,
            "compaction_method": method,
            "block_types": [block.type for block in blocks],
            "block_count": len(blocks),
            "block_summaries": [self._build_block_summary(block) for block in blocks],
            "estimated_saved_tokens": max(0, estimated_input_tokens - estimated_final_tokens),
            "durable_flush": durable_flush or {"ok": True, "skipped": True, "reason": "compaction_not_needed"},
            "resolved_scope": resolved_scope,
            "scope_chain": scope_chain,
            "recall_audit": recall_audit,
        }
        if blocks:
            print(f"[ContextOrchestrator] {json.dumps(audit, ensure_ascii=False)}")

        return PreparedContext(messages=rendered_messages, blocks=blocks, audit=audit)

    def _compact_non_system_messages(
        self,
        *,
        messages: List[BaseMessage],
        keep_recent_messages: int,
        compression: Dict[str, Any],
        target_role: str,
        resolved_model_id: str | None,
        summary_mode: str,
    ) -> tuple[List[BaseMessage], ContextBlock | None, str]:
        if len(messages) <= keep_recent_messages:
            return messages, None, "none"

        last_human_idx = -1
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                last_human_idx = index
                break

        keep_from = max(0, len(messages) - keep_recent_messages)
        if last_human_idx >= 0:
            keep_from = min(keep_from, last_human_idx)
        if keep_from <= 0:
            return messages, None, "none"

        to_compress = messages[:keep_from]
        to_keep = messages[keep_from:]
        if not to_compress:
            return messages, None, "none"

        summary_block, method = self._build_history_block(
            to_compress=to_compress,
            compression=compression,
            target_role=target_role,
            resolved_model_id=resolved_model_id,
            summary_mode=summary_mode,
        )
        return to_keep, summary_block, method

    def _build_history_block(
        self,
        *,
        to_compress: Sequence[BaseMessage],
        compression: Dict[str, Any],
        target_role: str,
        resolved_model_id: str | None,
        summary_mode: str,
    ) -> tuple[ContextBlock, str]:
        cache_key = self._build_summary_cache_key(
            to_compress=to_compress,
            target_role=target_role,
            resolved_model_id=resolved_model_id,
            compression=compression,
            summary_mode=summary_mode,
        )
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached, str(cached.metadata.get("summary_method") or "cache")

        summary_model = str(storage.get_role_model_id("summary") or "").strip()
        selected_messages = self._select_summary_candidates(
            to_compress=to_compress,
            max_input_tokens=int(compression.get("max_summary_input_tokens") or 5000),
            max_input_messages=int(compression.get("max_summary_input_messages") or 60),
        )
        summary_text = None
        method = "rule_summary"
        if summary_mode == "llm" and compression.get("use_llm_summary") and summary_model:
            summary_text = self._build_llm_summary(
                to_compress=selected_messages,
                model_id=summary_model,
                max_input_tokens=int(compression.get("max_summary_input_tokens") or 5000),
                max_input_messages=int(compression.get("max_summary_input_messages") or 60),
                max_output_tokens=int(compression.get("max_summary_output_tokens") or 800),
            )
            if summary_text:
                method = "llm_summary"
        if not summary_text:
            summary_text = self._build_rule_summary(selected_messages)

        block = ContextBlock(
            type="history_summary",
            title="历史上下文提炼",
            content=summary_text,
            metadata={
                "summary_method": method,
                "compressed_messages": len(to_compress),
                "candidate_messages": len(selected_messages),
            },
        )
        self._summary_cache[cache_key] = block
        while len(self._summary_cache) > 24:
            oldest_key = next(iter(self._summary_cache))
            self._summary_cache.pop(oldest_key, None)
        return block, method

    def _build_llm_summary(
        self,
        *,
        to_compress: Sequence[BaseMessage],
        model_id: str,
        max_input_tokens: int,
        max_input_messages: int,
        max_output_tokens: int,
    ) -> str | None:
        try:
            llm = llm_factory.create_chat_model(model_id, temperature=0, max_tokens=max_output_tokens, _role="summary")
            clipped_messages = self._truncate_candidates_for_llm(
                messages=to_compress,
                max_input_tokens=max_input_tokens,
                max_input_messages=max_input_messages,
            )
            transcript = "\n".join(
                f"{self._message_role(message)}: {self._clip_text(self._message_text(message), 320)}"
                for message in clipped_messages
                if self._message_text(message)
            )
            if not transcript.strip():
                return ""
            prompt = (
                "你是上下文治理模块，只负责把旧对话压缩为可供后续执行继续使用的历史摘要。\n"
                "保留：用户目标、已完成的动作、关键文件路径/URL/产物、失败与阻塞、仍然有效的执行约束。\n"
                "不要复述寒暄，不要新增推断，不要输出前言。\n\n"
                f"{transcript}"
            )
            response = llm.invoke([HumanMessage(content=prompt)], config={"callbacks": []})
            return self._clip_text(self._message_text(response), max_output_tokens * 4)
        except Exception as exc:
            print(f"[ContextOrchestrator] LLM summary failed: {exc}")
            return None

    def _build_rule_summary(self, messages: Sequence[BaseMessage]) -> str:
        lines: List[str] = []
        for message in messages:
            text = self._clip_text(self._message_text(message), 180)
            if not text:
                continue
            lines.append(f"- {self._message_role(message)}: {text}")
        return "\n".join(lines[:20])

    def _select_summary_candidates(
        self,
        *,
        to_compress: Sequence[BaseMessage],
        max_input_tokens: int,
        max_input_messages: int,
    ) -> List[BaseMessage]:
        ranked: List[tuple[int, int, int, BaseMessage]] = []
        for index, message in enumerate(to_compress):
            text = self._message_text(message)
            if not text.strip():
                continue
            ranked.append(
                (
                    self._message_priority_score(message),
                    index,
                    self._estimate_message_tokens(message),
                    message,
                )
            )
        if not ranked:
            return list(to_compress)[-max_input_messages:]

        ranked.sort(key=lambda item: (-item[0], -item[1]))
        selected: List[tuple[int, BaseMessage]] = []
        used_tokens = 0
        for _score, index, token_cost, message in ranked:
            if len(selected) >= max_input_messages:
                break
            fits_budget = used_tokens + token_cost <= max_input_tokens
            if selected and not fits_budget:
                continue
            selected.append((index, message))
            used_tokens += token_cost

        if not selected:
            _best_score, best_index, _token_cost, best_message = ranked[0]
            selected.append((best_index, best_message))

        selected.sort(key=lambda item: item[0])
        return [message for _, message in selected]

    def _truncate_candidates_for_llm(
        self,
        *,
        messages: Sequence[BaseMessage],
        max_input_tokens: int,
        max_input_messages: int,
    ) -> List[BaseMessage]:
        clipped: List[BaseMessage] = []
        used_tokens = 0
        for message in messages:
            token_cost = self._estimate_message_tokens(message)
            if clipped and (len(clipped) >= max_input_messages or used_tokens + token_cost > max_input_tokens):
                break
            clipped.append(message)
            used_tokens += token_cost
        return clipped or list(messages[:1])

    def _extract_adapter_blocks(self, messages: Sequence[BaseMessage]) -> tuple[List[BaseMessage], List[ContextBlock]]:
        cleaned: List[BaseMessage] = []
        blocks: List[ContextBlock] = []
        for message in messages:
            cloned = deepcopy(message)
            kwargs = dict(getattr(cloned, "additional_kwargs", {}) or {})
            raw_blocks = kwargs.pop("context_adapter_blocks", None)
            if raw_blocks:
                block_items = [raw_blocks] if isinstance(raw_blocks, dict) else raw_blocks
                blocks.extend(self._coerce_blocks(block_items))
            cloned.additional_kwargs = kwargs
            cleaned.append(cloned)
        return cleaned, blocks

    def _coerce_blocks(self, items: Sequence[Dict[str, Any] | ContextBlock]) -> List[ContextBlock]:
        normalized: List[ContextBlock] = []
        for item in items:
            if isinstance(item, ContextBlock):
                if item.type in _ALLOWED_BLOCK_TYPES and item.content.strip():
                    normalized.append(item)
                continue
            if not isinstance(item, dict):
                continue
            block_type = str(item.get("type") or "").strip()
            content = str(item.get("content") or "").strip()
            if block_type not in _ALLOWED_BLOCK_TYPES or not content:
                continue
            normalized.append(
                ContextBlock(
                    type=block_type,
                    title=str(item.get("title") or block_type.replace("_", " ").title()),
                    content=content,
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        return normalized

    def _render_block_message(self, block: ContextBlock) -> SystemMessage:
        label = block.type.upper()
        return SystemMessage(
            content=f"[CONTEXT BLOCK: {label}]\n{block.title}\n{block.content}\n[/CONTEXT BLOCK]",
            additional_kwargs={
                "context_block": {
                    "type": block.type,
                    "title": block.title,
                    "metadata": dict(block.metadata or {}),
                }
            },
        )

    def _extract_scope_context(self, messages: Sequence[BaseMessage]) -> tuple[str, List[str]]:
        resolved_scope = ""
        scope_chain: List[str] = []
        for message in reversed(messages):
            kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
            candidate_scope = str(kwargs.get("resolved_scope") or "").strip()
            if candidate_scope and not resolved_scope:
                resolved_scope = candidate_scope
            candidate_chain = kwargs.get("scope_chain")
            if isinstance(candidate_chain, list) and not scope_chain:
                scope_chain = [str(item).strip() for item in candidate_chain if str(item).strip()]
            if resolved_scope and scope_chain:
                break
        if resolved_scope and resolved_scope not in scope_chain:
            scope_chain.append(resolved_scope)
        return resolved_scope, scope_chain

    def _extract_recall_audit(self, messages: Sequence[BaseMessage]) -> Dict[str, Any]:
        for message in reversed(messages):
            kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
            diagnostics = kwargs.get("memory_rag_diagnostics")
            if not isinstance(diagnostics, dict):
                continue
            return {
                "query": str(diagnostics.get("query") or "").strip(),
                "threshold": diagnostics.get("threshold"),
                "configured_threshold": diagnostics.get("configured_threshold"),
                "top_scores": list(diagnostics.get("top_scores") or []),
                "injection_allowed": bool(diagnostics.get("injection_allowed", False)),
                "reject_reason": str(diagnostics.get("reject_reason") or "").strip(),
                "has_recall_cue": bool(diagnostics.get("has_recall_cue", False)),
            }
        return {}

    def _build_block_summary(self, block: ContextBlock) -> Dict[str, Any]:
        metadata = dict(block.metadata or {})
        summary: Dict[str, Any] = {
            "type": block.type,
            "title": block.title,
            "runtime_plane": str(metadata.get("runtime_plane") or "").strip(),
            "estimated_tokens": self._estimate_text_tokens(block.content),
            "content_preview": self._clip_text(block.content, 160),
        }
        sanitized_metadata: Dict[str, Any] = {}
        for key in (
            "runtime_plane",
            "compressed_messages",
            "candidate_messages",
            "max_summary_items",
            "item_count",
            "fact_count",
            "summary_method",
            "source",
            "top_scores",
            "threshold",
        ):
            value = metadata.get(key)
            if value is None:
                continue
            sanitized_metadata[key] = value
        if sanitized_metadata:
            summary["metadata"] = sanitized_metadata
        return summary

    def _build_summary_cache_key(
        self,
        *,
        to_compress: Sequence[BaseMessage],
        target_role: str,
        resolved_model_id: str | None,
        compression: Dict[str, Any],
        summary_mode: str,
    ) -> str:
        session_id = ""
        for message in reversed(to_compress):
            session_id = str((getattr(message, "additional_kwargs", {}) or {}).get("session_id") or "").strip()
            if session_id:
                break
        payload = {
            "session_id": session_id,
            "target_role": target_role,
            "resolved_model_id": str(resolved_model_id or "").strip(),
            "summary_model": str(storage.get_role_model_id("summary") or "").strip(),
            "summary_mode": summary_mode,
            "compression": {
                "use_llm_summary": bool(compression.get("use_llm_summary", False)),
                "max_summary_input_tokens": int(compression.get("max_summary_input_tokens") or 5000),
                "max_summary_input_messages": int(compression.get("max_summary_input_messages") or 60),
                "max_summary_output_tokens": int(compression.get("max_summary_output_tokens") or 800),
            },
            "messages": [
                {
                    "type": message.type,
                    "content": self._message_text(message),
                    "tool_calls": getattr(message, "tool_calls", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                }
                for message in to_compress
            ],
        }
        digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return digest

    def _estimate_messages_tokens(self, messages: Sequence[BaseMessage]) -> int:
        total = 0
        for message in messages:
            total += self._estimate_message_tokens(message)
        return total

    def _estimate_message_tokens(self, message: BaseMessage) -> int:
        total = self._estimate_text_tokens(self._message_text(message))
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            total += self._estimate_text_tokens(json.dumps(message.tool_calls, ensure_ascii=False))
        return total

    def _message_priority_score(self, message: BaseMessage) -> int:
        text = self._message_text(message).strip()
        lowered = text.lower()

        if isinstance(message, HumanMessage):
            score = 460
        elif isinstance(message, AIMessage):
            score = 320
        elif isinstance(message, ToolMessage):
            score = 140
        else:
            score = 220

        if self._contains_any(lowered, _GOAL_KEYWORDS):
            score += 260
        if self._contains_any(lowered, _BLOCKER_KEYWORDS):
            score += 220
        if isinstance(message, AIMessage) and self._contains_any(lowered, _DECISION_KEYWORDS):
            score += 160
        if self._contains_any(lowered, _GOVERNANCE_KEYWORDS):
            score += 110
        if isinstance(message, ToolMessage):
            score -= 60
        if self._looks_repetitive(text):
            score -= 120
        if len(text) > 1200:
            score -= 40
        return score

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 4)

    @staticmethod
    def _contains_any(text: str, keywords: Sequence[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _looks_repetitive(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if len(normalized) < 240:
            return False
        tokens = normalized.split(" ")
        if len(tokens) < 24:
            return False
        unique_ratio = len(set(tokens)) / max(1, len(tokens))
        return unique_ratio < 0.42

    @staticmethod
    def _message_role(message: BaseMessage) -> str:
        if isinstance(message, HumanMessage):
            return "User"
        if isinstance(message, ToolMessage):
            return f"Tool({getattr(message, 'name', '?')})"
        if isinstance(message, AIMessage):
            return "Assistant"
        if isinstance(message, SystemMessage):
            return "System"
        return message.type

    @classmethod
    def _message_text(cls, message: BaseMessage) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content or "")

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3] + "..."


context_orchestrator = ContextOrchestrator()
