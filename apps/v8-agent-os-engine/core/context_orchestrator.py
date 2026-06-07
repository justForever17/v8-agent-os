from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.context_compaction_baseline import (
    baseline_matches_messages,
    digest_messages,
    load_compaction_baseline,
    persist_compaction_baseline,
)
from core.context_durable_flush import flush_before_context_compaction
from core.context_window_guard import context_window_guard
from core.llm_factory import llm_factory
from core.observability_db import observability_db
from core.storage import storage
from erc.runtime_context import get_runtime_context


_ALLOWED_BLOCK_TYPES = {
    "history_summary",
    "channel_memory",
    "automation_memory",
    "recent_messages",
    "memory_recall",
    "memory_broker",
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
        runtime_ctx = get_runtime_context()
        live_audit_context = (
            dict(runtime_ctx.get("live_audit") or {})
            if isinstance(runtime_ctx.get("live_audit"), dict)
            else {}
        )
        force_live_audit_compaction = bool(
            live_audit_context.get("runtimeSubagentClosureLiveAudit")
            and live_audit_context.get("preferContextCompaction")
        )
        context_governance_reason = ""
        if force_live_audit_compaction:
            compression = {
                **compression,
                "enabled": True,
                "mode": str(compression.get("mode") or "persistent_baseline").strip() or "persistent_baseline",
                "trigger_ratio": min(0.70, float(compression.get("trigger_ratio") or 0.94)),
                "hard_trigger_ratio": min(0.70, float(compression.get("hard_trigger_ratio") or compression.get("trigger_ratio") or 0.94)),
                "keep_recent_turns": 1,
                "keep_recent_messages": 2,
                "use_llm_summary": False,
            }
            context_governance_reason = "runtime_subagent_closure_live_audit_forced_compaction"
        window_guard = context_window_guard.resolve(
            target_role=target_role,
            runtime_kind=runtime_kind,
            model_ref=str(resolved_model_id or "").strip(),
            compression=compression,
        )
        if force_live_audit_compaction:
            forced_window = int(live_audit_context.get("forcedContextWindowTokens") or 2048)
            forced_window = max(512, min(forced_window, int(window_guard.get("effectiveContextWindowTokens") or forced_window)))
            trigger_ratio_forced = float(compression.get("trigger_ratio") or compression.get("hard_trigger_ratio") or 0.70)
            window_guard = dict(window_guard)
            warnings = [dict(item) for item in list(window_guard.get("warnings") or []) if isinstance(item, dict)]
            warnings.append(
                {
                    "reason": context_governance_reason,
                    "role": target_role,
                    "runtimeKind": runtime_kind,
                    "contextWindowTokens": forced_window,
                }
            )
            window_guard["effectiveContextWindowTokens"] = forced_window
            window_guard["triggerLimitTokens"] = max(1, int(forced_window * trigger_ratio_forced))
            window_guard["warnings"] = warnings
        context_window = int(window_guard.get("effectiveContextWindowTokens") or compression.get("default_context_window_tokens") or 32000)

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
        keep_recent_source = None if force_live_audit_compaction else keep_recent_override
        keep_recent_turns = int(keep_recent_source or compression.get("keep_recent_turns") or 4)
        keep_recent_messages = int(compression.get("keep_recent_messages") or max(keep_recent_turns * 2, 6))
        durable_flush: Dict[str, Any] | None = None
        compaction_mode = str(compression.get("mode") or "persistent_baseline").strip() or "persistent_baseline"
        trigger_ratio = float(compression.get("trigger_ratio") or compression.get("hard_trigger_ratio") or 0.94)
        trigger_limit = int(window_guard.get("triggerLimitTokens") or max(1, int(context_window * trigger_ratio)))
        compaction_latency_ms = 0
        recent_raw_count = len(non_system_messages)
        recent_raw_turn_count = 0
        baseline_used = False
        baseline_refreshed = False
        baseline_message_count = 0
        baseline_snapshot = None
        baseline_has_uncovered_messages = False
        session_id = str(runtime_ctx.get("session_id") or "").strip()
        old_prefix: List[BaseMessage] = []
        recent_tail: List[BaseMessage] = list(non_system_messages)

        if compression.get("enabled", True):
            old_prefix, recent_tail, recent_raw_turn_count = self._split_recent_tail_by_turns(
                messages=non_system_messages,
                keep_recent_turns=keep_recent_turns,
                keep_recent_messages=keep_recent_messages,
            )
            recent_raw_count = len(recent_tail)
            should_compact = estimated_input_tokens >= trigger_limit
            trigger_reason = "persistent_baseline_token_budget" if should_compact else "within_budget"
            if compaction_mode == "persistent_baseline" and session_id and old_prefix:
                baseline_snapshot = load_compaction_baseline(session_id=session_id, target_role=target_role)
                if baseline_snapshot:
                    covered_count = min(int(baseline_snapshot.get("coveredMessageCount") or 0), len(old_prefix))
                    covered_prefix = list(old_prefix[:covered_count])
                    if covered_prefix and baseline_matches_messages(baseline_snapshot, covered_prefix):
                        history_block = self._build_baseline_block_from_snapshot(baseline_snapshot)
                        baseline_used = history_block is not None
                        if baseline_used:
                            method = str(history_block.metadata.get("summary_method") or "baseline")
                            baseline_message_count = covered_count
                            uncovered_old_messages = list(old_prefix[covered_count:])
                            baseline_has_uncovered_messages = bool(uncovered_old_messages)
                            non_system_messages = uncovered_old_messages + list(recent_tail)
                    else:
                        non_system_messages = list(old_prefix) + list(recent_tail)
                else:
                    non_system_messages = list(old_prefix) + list(recent_tail)

            projected_messages = list(rendered_messages)
            if history_block is not None:
                projected_messages.append(self._render_block_message(history_block))
            projected_messages.extend(non_system_messages)
            projected_effective_tokens = self._estimate_messages_tokens(projected_messages)

            if compaction_mode == "persistent_baseline" and baseline_used:
                if baseline_has_uncovered_messages:
                    should_compact = True
                    trigger_reason = "baseline_roll_forward"
                else:
                    should_compact = False
                    trigger_reason = (
                        "baseline_reused_over_budget"
                        if projected_effective_tokens >= trigger_limit
                        else "baseline_reused"
                    )

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
                    baseline_source_messages = old_prefix if old_prefix else []
                    if (
                        compaction_mode == "persistent_baseline"
                        and baseline_used
                        and old_prefix
                        and len(old_prefix) > baseline_message_count
                    ):
                        baseline_source_messages = old_prefix
                    if baseline_source_messages:
                        summary_mode = "llm" if compression.get("use_llm_summary", True) else "rule"
                        compaction_started = time.perf_counter()
                        history_block, method = self._build_history_block(
                            to_compress=baseline_source_messages,
                            compression=compression,
                            target_role=target_role,
                            resolved_model_id=resolved_model_id,
                            summary_mode=summary_mode,
                            effective_context_window_tokens=context_window,
                        )
                        compaction_latency_ms = int((time.perf_counter() - compaction_started) * 1000)
                        compaction_applied = history_block is not None
                        if compaction_applied:
                            baseline_refreshed = bool(session_id and compaction_mode == "persistent_baseline")
                            baseline_message_count = len(baseline_source_messages)
                            if session_id and compaction_mode == "persistent_baseline":
                                persisted_snapshot = persist_compaction_baseline(
                                    session_id=session_id,
                                    target_role=target_role,
                                    covered_messages=baseline_source_messages,
                                    baseline_text=history_block.content,
                                    estimated_tokens=self._estimate_text_tokens(history_block.content),
                                    summary_method=str(history_block.metadata.get("summary_method") or method),
                                    chunked=bool(history_block.metadata.get("chunked")),
                                    context_window_tokens=context_window,
                                    trigger_ratio=trigger_ratio,
                                    resolved_model_id=resolved_model_id,
                                )
                                baseline_snapshot = persisted_snapshot
                            non_system_messages = list(recent_tail)
                            trigger_reason = (
                                "baseline_refreshed"
                                if baseline_refreshed
                                else "compaction_applied_without_baseline"
                            )
                        else:
                            non_system_messages = list(old_prefix) + list(recent_tail)
                    elif baseline_used:
                        non_system_messages = list(recent_tail)
                        compaction_applied = True
                        trigger_reason = "baseline_reused"
                else:
                    trigger_reason = "pre_compaction_flush_failed"
            elif baseline_used:
                compaction_applied = True
                trigger_reason = "baseline_reused"

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
            "context_governance_reason": context_governance_reason,
            "context_window_tokens": context_window,
            "effective_context_window_tokens": context_window,
            "context_window_participants": window_guard.get("participants") or [],
            "context_window_warnings": window_guard.get("warnings") or [],
            "summary_input_budget_tokens": window_guard.get("summaryInputBudgetTokens"),
            "original_message_count": len(messages),
            "estimated_input_tokens": estimated_input_tokens,
            "trigger_reason": trigger_reason,
            "compaction_applied": compaction_applied,
            "compaction_method": method,
            "compaction_mode": compaction_mode,
            "baseline_active": baseline_used,
            "baseline_refreshed": baseline_refreshed,
            "baseline_message_count": baseline_message_count,
            "recent_raw_message_count": recent_raw_count,
            "recent_raw_turn_count": recent_raw_turn_count,
            "trigger_ratio": trigger_ratio,
            "latency_ms": compaction_latency_ms,
            "noticeable_latency": compaction_latency_ms >= int(compression.get("noticeable_latency_ms") or 800),
            "block_types": [block.type for block in blocks],
            "block_count": len(blocks),
            "block_summaries": [self._build_block_summary(block) for block in blocks],
            "estimated_saved_tokens": max(0, estimated_input_tokens - estimated_final_tokens),
            "durable_flush": durable_flush or {"ok": True, "skipped": True, "reason": "compaction_not_needed"},
            "resolved_scope": resolved_scope,
            "scope_chain": scope_chain,
            "recall_audit": recall_audit,
        }
        if history_block is not None:
            covered_messages = list(old_prefix[:baseline_message_count]) if baseline_message_count > 0 else []
            covered_hash = digest_messages(covered_messages) if covered_messages else str((baseline_snapshot or {}).get("coveredMessagesHash") or "")
            baseline_snapshot_ref = str((baseline_snapshot or {}).get("snapshotId") or "").strip()
            if not baseline_snapshot_ref and covered_hash:
                baseline_snapshot_ref = f"{session_id}:{target_role}:{covered_hash}"
            try:
                compaction_record = observability_db.add_conversation_compaction_record(
                    {
                        "session_id": session_id or None,
                        "run_id": str(runtime_ctx.get("run_id") or "").strip() or None,
                        "target_role": target_role,
                        "runtime_kind": runtime_kind,
                        "resolved_model_id": str(resolved_model_id or "").strip(),
                        "trigger_reason": trigger_reason,
                        "compaction_mode": compaction_mode,
                        "summary_method": method,
                        "baseline_reused": baseline_used,
                        "baseline_refreshed": baseline_refreshed,
                        "baseline_snapshot_ref": baseline_snapshot_ref or None,
                        "covered_message_count": baseline_message_count,
                        "covered_messages_hash": covered_hash or None,
                        "summary_chars": len(history_block.content or ""),
                        "summary_tokens": self._estimate_text_tokens(history_block.content or ""),
                        "estimated_saved_tokens": audit["estimated_saved_tokens"],
                        "context_window_tokens": context_window,
                        "metadata": {
                            "blockType": history_block.type,
                            "blockTitle": history_block.title,
                            "durableFlush": durable_flush or {"ok": True, "skipped": True, "reason": "compaction_not_needed"},
                            "recentRawMessageCount": recent_raw_count,
                            "recentRawTurnCount": recent_raw_turn_count,
                            "noticeableLatency": audit["noticeable_latency"],
                            "contextWindowParticipants": audit["context_window_participants"],
                            "effectiveContextWindowTokens": audit["effective_context_window_tokens"],
                            "contextWindowWarnings": audit["context_window_warnings"],
                        },
                    }
                )
                audit["compactionRecordId"] = compaction_record.get("id")
                audit["coveredMessageHash"] = covered_hash
                audit["summaryChars"] = len(history_block.content or "")
                audit["summaryTokens"] = self._estimate_text_tokens(history_block.content or "")
                audit["baselineSnapshotRef"] = baseline_snapshot_ref or None
            except Exception as exc:
                audit["compactionRecordError"] = str(exc)
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
        effective_context_window_tokens: int | None = None,
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
            effective_context_window_tokens=effective_context_window_tokens,
        )
        return to_keep, summary_block, method

    def _split_recent_tail_by_turns(
        self,
        *,
        messages: Sequence[BaseMessage],
        keep_recent_turns: int,
        keep_recent_messages: int,
    ) -> tuple[List[BaseMessage], List[BaseMessage], int]:
        message_list = list(messages)
        if not message_list:
            return [], [], 0
        human_indexes = [index for index, message in enumerate(message_list) if isinstance(message, HumanMessage)]
        if not human_indexes:
            keep_from = max(0, len(message_list) - keep_recent_messages)
            return message_list[:keep_from], message_list[keep_from:], 0
        recent_humans = human_indexes[-max(1, keep_recent_turns):]
        keep_from = recent_humans[0]
        keep_from = min(keep_from, max(0, len(message_list) - keep_recent_messages))
        if keep_from <= 0:
            return [], message_list, len(recent_humans)
        return message_list[:keep_from], message_list[keep_from:], len(recent_humans)

    def _build_baseline_block_from_snapshot(self, snapshot: Dict[str, Any]) -> ContextBlock | None:
        content = str(snapshot.get("baselineText") or "").strip()
        if not content:
            return None
        return ContextBlock(
            type="history_summary",
            title="历史上下文提炼",
            content=content,
            metadata={
                "summary_method": str(snapshot.get("summaryMethod") or "baseline"),
                "compressed_messages": int(snapshot.get("coveredMessageCount") or 0),
                "candidate_messages": int(snapshot.get("coveredMessageCount") or 0),
                "chunked": bool(snapshot.get("chunked")),
                "source": "persistent_baseline",
            },
        )

    def _build_history_block(
        self,
        *,
        to_compress: Sequence[BaseMessage],
        compression: Dict[str, Any],
        target_role: str,
        resolved_model_id: str | None,
        summary_mode: str,
        effective_context_window_tokens: int | None = None,
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
        summary_source_messages = (
            list(to_compress)
            if summary_mode == "llm" and compression.get("use_llm_summary")
            else selected_messages
        )
        summary_text = None
        method = "rule_summary"
        chunked = False
        if summary_mode == "llm" and compression.get("use_llm_summary") and summary_model:
            summary_text, chunked = self._build_llm_summary(
                to_compress=summary_source_messages,
                model_id=summary_model,
                max_input_tokens=min(
                    int(compression.get("max_summary_input_tokens") or 5000),
                    int(effective_context_window_tokens or compression.get("default_context_window_tokens") or 32000),
                ),
                max_input_messages=int(compression.get("max_summary_input_messages") or 60),
                max_output_tokens=int(compression.get("max_summary_output_tokens") or 800),
                compression_model_safety_ratio=float(compression.get("compression_model_safety_ratio") or 0.90),
                effective_context_window_tokens=effective_context_window_tokens,
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
                "candidate_messages": len(summary_source_messages if summary_text and method == "llm_summary" else selected_messages),
                "chunked": chunked,
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
        compression_model_safety_ratio: float,
        effective_context_window_tokens: int | None = None,
    ) -> tuple[str | None, bool]:
        try:
            llm = llm_factory.create_chat_model(model_id, temperature=0, max_tokens=max_output_tokens, _role="summary")
            model_window = llm_factory.get_model_context_window(str(model_id or "").strip()) or max_input_tokens
            effective_window = min(
                int(model_window or max_input_tokens),
                int(effective_context_window_tokens or model_window or max_input_tokens),
            )
            safe_chunk_budget = max(256, min(max_input_tokens, int(effective_window * max(0.5, min(compression_model_safety_ratio, 0.95)))))
            clipped_messages = list(to_compress)
            if not clipped_messages:
                return "", False

            chunks = self._chunk_messages_for_summary(
                messages=clipped_messages,
                max_chunk_tokens=safe_chunk_budget,
                max_chunk_messages=max_input_messages,
            )
            if not chunks:
                return "", False

            chunk_summaries: List[str] = []
            for chunk in chunks:
                transcript = self._messages_to_summary_transcript(chunk)
                if not transcript.strip():
                    continue
                chunk_summaries.append(
                    self._summarize_fragment_group(
                        llm=llm,
                        fragments=[transcript],
                        max_output_tokens=max_output_tokens,
                    )
                )

            if not chunk_summaries:
                return "", False
            reduced, reduced_any = self._reduce_summary_fragments(
                llm=llm,
                fragments=chunk_summaries,
                max_output_tokens=max_output_tokens,
                max_chunk_tokens=safe_chunk_budget,
                max_chunk_messages=max_input_messages,
            )
            return (reduced[0] if reduced else ""), len(chunks) > 1 or reduced_any
        except Exception as exc:
            print(f"[ContextOrchestrator] LLM summary failed: {exc}")
            return None, False

    def _messages_to_summary_transcript(self, messages: Sequence[BaseMessage]) -> str:
        return "\n".join(
            f"{self._message_role(message)}: {self._clip_text(self._message_text(message), 320)}"
            for message in messages
            if self._message_text(message)
        )

    def _chunk_messages_for_summary(
        self,
        *,
        messages: Sequence[BaseMessage],
        max_chunk_tokens: int,
        max_chunk_messages: int,
    ) -> List[List[BaseMessage]]:
        chunks: List[List[BaseMessage]] = []
        current: List[BaseMessage] = []
        used_tokens = 0
        for message in messages:
            token_cost = max(1, self._estimate_message_tokens(message))
            if current and (used_tokens + token_cost > max_chunk_tokens or len(current) >= max_chunk_messages):
                chunks.append(current)
                current = []
                used_tokens = 0
            current.append(message)
            used_tokens += token_cost
        if current:
            chunks.append(current)
        return chunks

    def _chunk_fragments_for_summary(
        self,
        *,
        fragments: Sequence[str],
        max_chunk_tokens: int,
        max_chunk_messages: int,
    ) -> List[List[str]]:
        chunks: List[List[str]] = []
        current: List[str] = []
        used_tokens = 0
        for fragment in fragments:
            normalized = str(fragment or "").strip()
            if not normalized:
                continue
            token_cost = max(1, self._estimate_text_tokens(normalized))
            if current and (used_tokens + token_cost > max_chunk_tokens or len(current) >= max_chunk_messages):
                chunks.append(current)
                current = []
                used_tokens = 0
            current.append(normalized)
            used_tokens += token_cost
        if current:
            chunks.append(current)
        return chunks

    def _summarize_fragment_group(
        self,
        *,
        llm: Any,
        fragments: Sequence[str],
        max_output_tokens: int,
    ) -> str:
        prompt = (
            "你是上下文治理模块，只负责把旧对话压缩为可供后续执行继续使用的历史摘要。\n"
            "保留：用户目标、已完成的动作、关键文件路径/URL/产物、失败与阻塞、仍然有效的执行约束。\n"
            "不要复述寒暄，不要新增推断，不要输出前言。\n\n"
            + "\n\n".join(f"[FRAGMENT {index + 1}]\n{text}" for index, text in enumerate(fragments))
        )
        response = llm.invoke([HumanMessage(content=prompt)], config={"callbacks": []})
        return self._clip_text(self._message_text(response), max_output_tokens * 4)

    def _reduce_summary_fragments(
        self,
        *,
        llm: Any,
        fragments: Sequence[str],
        max_output_tokens: int,
        max_chunk_tokens: int,
        max_chunk_messages: int,
    ) -> tuple[List[str], bool]:
        current = [str(item or "").strip() for item in fragments if str(item or "").strip()]
        reduced_any = False
        while len(current) > 1:
            groups = self._chunk_fragments_for_summary(
                fragments=current,
                max_chunk_tokens=max_chunk_tokens,
                max_chunk_messages=max_chunk_messages,
            )
            if not groups:
                break
            if len(groups) == len(current):
                groups = [current[index:index + 2] for index in range(0, len(current), 2)]
            next_level = [
                self._summarize_fragment_group(
                    llm=llm,
                    fragments=group,
                    max_output_tokens=max_output_tokens,
                )
                for group in groups
                if group
            ]
            if not next_level:
                break
            current = next_level
            reduced_any = True
        return current, reduced_any

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
        content = f"[CONTEXT BLOCK: {label}]\n{block.title}\n{block.content}\n[/CONTEXT BLOCK]"
        return SystemMessage(
            content=content,
            additional_kwargs={
                "context_block": {
                    "type": block.type,
                    "title": block.title,
                    "metadata": dict(block.metadata or {}),
                },
                "v8_prompt_segments": [
                    {
                        "type": "dynamic",
                        "source": f"context_block:{block.type}",
                        "hash": hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest(),
                        "charCount": len(content),
                        "scope": "context_block",
                    }
                ],
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
                "compression_model_safety_ratio": float(compression.get("compression_model_safety_ratio") or 0.90),
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
