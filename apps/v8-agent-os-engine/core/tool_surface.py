from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command


DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS = 15000
MAX_TOOL_OUTPUT_LENGTH = DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS
DEFAULT_CONTEXT_WINDOW_TOKENS = 32000
DEFAULT_OUTPUT_RESERVE_TOKENS = 2048
CONTEXT_SAFETY_BUFFER_RATIO = 0.2
CHARS_PER_TOKEN_ESTIMATE = 4
MIN_TOOL_OUTPUT_BUDGET_CHARS = 1200

TOOL_OUTPUT_TARGET_CHARS = {
    "default": 8000,
    "catalog": 12000,
    "diagnostic": 12000,
    "operation": 6000,
}

JSON_PRIORITY_KEYS = (
    "ok",
    "kind",
    "status",
    "summary",
    "runId",
    "traceId",
    "toolCallId",
    "rawRef",
    "summaryRef",
    "recommendedNextAction",
    "selectedPlaybook",
    "selectedPlaybookExecutor",
    "factResolution",
    "laneDecision",
    "candidateAttempts",
    "shortSequenceVerification",
    "verification",
    "artifactIds",
    "artifacts",
    "jobId",
    "providerTaskId",
    "operationKind",
    "modality",
    "providerId",
    "model",
    "modelId",
    "modelRef",
    "qualityStatus",
    "qualityJobId",
    "qualityJobIds",
    "retryReason",
    "fallbackAttempts",
    "policyRejectReason",
    "rawProviderResponseRef",
    "error",
    "exitCode",
    "returnCode",
    "stderr",
    "stderrTail",
    "refs",
    "count",
    "limit",
    "hasMore",
    "cursor",
    "detailTool",
)

WORKER_RESULT_RE = re.compile(
    r"<V8_WORKER_RESULT\b[^>]*>.*?</V8_WORKER_RESULT>",
    re.IGNORECASE | re.DOTALL,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_kind_for_tool(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name.startswith("creative_media_"):
        return "creative_media"
    if name.startswith("computer_use_"):
        return "computer_use"
    if name.startswith("rpa_"):
        return "rpa"
    if name.startswith("memory_") or name.startswith("mem_"):
        return "memory"
    if name in {"manage_cron", "manage_hook", "list_processes", "read_audit_log"}:
        return "automation"
    if name == "runtime_broker":
        return "runtime_broker"
    if name == "fetch_skill_instructions":
        return "extensions"
    return "native"


def _text_for_token_estimate(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if content is not None:
        return str(content)
    if isinstance(value, dict):
        return str(value.get("content") or value)
    return str(value)


def _estimate_tokens_from_chars(text: str) -> int:
    return max(0, int(len(text or "") / CHARS_PER_TOKEN_ESTIMATE))


def _request_messages(request: Any) -> list[Any]:
    state = getattr(request, "state", None)
    if isinstance(state, dict):
        messages = state.get("messages")
        if isinstance(messages, list):
            return messages
    input_payload = getattr(request, "input", None)
    if isinstance(input_payload, dict):
        messages = input_payload.get("messages")
        if isinstance(messages, list):
            return messages
    return []


def _nested_config_value(config: Any, *names: str) -> Any:
    if not isinstance(config, dict):
        return None
    for name in names:
        if config.get(name) not in (None, ""):
            return config.get(name)
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        for name in names:
            if configurable.get(name) not in (None, ""):
                return configurable.get(name)
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        for name in names:
            if metadata.get(name) not in (None, ""):
                return metadata.get(name)
    return None


def _request_config(request: Any) -> dict[str, Any]:
    config = getattr(request, "config", None)
    return config if isinstance(config, dict) else {}


def _tool_output_kind(tool_name: str) -> str:
    normalized = (tool_name or "").lower()
    if "catalog" in normalized or "list_" in normalized or normalized.endswith("_list"):
        return "catalog"
    if "diagnostic" in normalized or "capabilities" in normalized or "observe" in normalized:
        return "diagnostic"
    if any(part in normalized for part in ("delete", "update", "write", "run_", "execute", "manage", "broker")):
        return "operation"
    return "default"


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def tool_output_budget_for_request(request: Any, tool_name: str) -> dict[str, Any]:
    config = _request_config(request)
    context_window_tokens = _safe_int(
        _nested_config_value(
            config,
            "contextWindowTokens",
            "modelContextWindowTokens",
            "model_context_window_tokens",
            "context_window_tokens",
        ),
        DEFAULT_CONTEXT_WINDOW_TOKENS,
    )
    output_reserve_tokens = _safe_int(
        _nested_config_value(
            config,
            "reservedOutputTokens",
            "maxOutputTokens",
            "max_tokens",
            "output_reserve_tokens",
        ),
        DEFAULT_OUTPUT_RESERVE_TOKENS,
    )
    hard_max_chars = _safe_int(
        _nested_config_value(
            config,
            "toolOutputHardMaxChars",
            "maxToolOutputChars",
            "tool_output_hard_max_chars",
        ),
        DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS,
    )
    messages = _request_messages(request)
    used_tokens = sum(_estimate_tokens_from_chars(_text_for_token_estimate(item)) for item in messages)
    safety_buffer_tokens = int(context_window_tokens * CONTEXT_SAFETY_BUFFER_RATIO)
    remaining_tokens = max(0, context_window_tokens - used_tokens - output_reserve_tokens - safety_buffer_tokens)
    dynamic_budget_chars = max(MIN_TOOL_OUTPUT_BUDGET_CHARS, remaining_tokens * CHARS_PER_TOKEN_ESTIMATE)
    kind = _tool_output_kind(tool_name)
    target_chars = TOOL_OUTPUT_TARGET_CHARS.get(kind, TOOL_OUTPUT_TARGET_CHARS["default"])
    agent_visible_budget = max(MIN_TOOL_OUTPUT_BUDGET_CHARS, min(dynamic_budget_chars, target_chars, hard_max_chars))
    return {
        "budgetSource": "dynamic_context_budget",
        "agentVisibleBudget": int(agent_visible_budget),
        "dynamicBudgetChars": int(dynamic_budget_chars),
        "hardMaxChars": int(hard_max_chars),
        "targetChars": int(target_chars),
        "toolOutputKind": kind,
        "contextWindowTokens": int(context_window_tokens),
        "estimatedPromptTokens": int(used_tokens),
        "reservedOutputTokens": int(output_reserve_tokens),
        "safetyBufferTokens": int(safety_buffer_tokens),
    }


def _line_safe_slice(text: str, limit: int, *, tail: bool = False) -> str:
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    if tail:
        chunk = text[-limit:]
        newline = chunk.find("\n")
        return chunk[newline + 1 :] if newline >= 0 else chunk
    chunk = text[:limit]
    newline = chunk.rfind("\n")
    if newline > max(80, limit // 2):
        return chunk[:newline]
    sentence = max(chunk.rfind("。"), chunk.rfind("."), chunk.rfind("!"), chunk.rfind("?"))
    if sentence > max(80, limit // 2):
        return chunk[: sentence + 1]
    return chunk.rstrip()


def _head_tail_truncate_text(text: str, budget: int, notice: str) -> str:
    if len(text) <= budget:
        return text
    marker = f"\n\n...[{notice}]...\n\n"
    available = max(0, budget - len(marker))
    if available <= 0:
        return marker[:budget]
    head_limit = max(1, int(available * 0.3))
    tail_limit = max(1, available - head_limit)
    return f"{_line_safe_slice(text, head_limit)}{marker}{_line_safe_slice(text, tail_limit, tail=True)}"


def _truncate_worker_result_preserving_marker(text: str, budget: int, notice: str) -> str | None:
    match = WORKER_RESULT_RE.search(text or "")
    if not match:
        return None
    marker = match.group(0)
    if len(text) <= budget:
        return text
    marker_notice = f"\n\n...[{notice}; V8_WORKER_RESULT preserved]...\n\n"
    context_budget = max(0, budget - len(marker) - len(marker_notice))
    if context_budget <= 0:
        return marker
    before = _line_safe_slice(text[: match.start()], int(context_budget * 0.3))
    after = _line_safe_slice(text[match.end() :], context_budget - len(before), tail=True)
    return f"{before}{marker_notice}{marker}{marker_notice}{after}".strip()


def _compact_json_value(value: Any, *, depth: int = 0, text_limit: int = 700) -> Any:
    if depth > 3:
        return _text_for_token_estimate(value)[:text_limit]
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
        return _head_tail_truncate_text(value, text_limit, f"field truncated; original length {len(value)} chars")
    if isinstance(value, list):
        limit = 5 if depth else 8
        items = [_compact_json_value(item, depth=depth + 1, text_limit=max(220, text_limit // 2)) for item in value[:limit]]
        if len(value) > limit:
            items.append({"omittedItems": len(value) - limit})
        return items
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        keys = [key for key in JSON_PRIORITY_KEYS if key in value]
        keys.extend([key for key in value.keys() if key not in keys][: max(0, 8 - len(keys))])
        for key in keys:
            compact[key] = _compact_json_value(value.get(key), depth=depth + 1, text_limit=max(220, text_limit // 2))
        omitted = max(0, len(value) - len(keys))
        if omitted:
            compact["omittedFields"] = omitted
        return compact
    return value


def _tool_surface_payload(
    *,
    tool_name: str,
    tool_call_id: str | None,
    runtime_kind: str,
    raw_ref: str | None,
    budget_meta: dict[str, Any],
    was_truncated: bool,
    strategy: str,
    omitted_chars: int = 0,
) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "toolCallId": tool_call_id,
        "runtimeKind": runtime_kind,
        "refs": {"rawRef": raw_ref} if raw_ref else {},
        "budget": {
            "budgetSource": budget_meta.get("budgetSource"),
            "agentVisibleBudget": budget_meta.get("agentVisibleBudget"),
            "hardMaxChars": budget_meta.get("hardMaxChars"),
            "targetChars": budget_meta.get("targetChars"),
        },
        "omitted": {
            "wasBudgetTruncated": was_truncated,
            "semanticTruncationStrategy": strategy,
            "omittedChars": max(0, int(omitted_chars or 0)),
        },
    }


def _inject_surface_metadata(text: str, surface: dict[str, Any], *, budget: int) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("{"):
        return text
    try:
        payload = json.loads(stripped)
    except Exception:
        return text
    if not isinstance(payload, dict):
        return text
    payload.setdefault("_v8ToolSurface", surface)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    return rendered if len(rendered) <= budget else text


def _truncate_json_semantic(
    text: str,
    budget_meta: dict[str, Any],
    *,
    tool_name: str,
    tool_call_id: str | None,
    runtime_kind: str,
    raw_ref: str | None,
) -> str | None:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, (dict, list)):
        return None

    original_len = len(text)
    budget = int(budget_meta["agentVisibleBudget"])
    surface = _tool_surface_payload(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        runtime_kind=runtime_kind,
        raw_ref=raw_ref,
        budget_meta=budget_meta,
        was_truncated=True,
        strategy="json_priority_fields",
        omitted_chars=max(0, original_len - budget),
    )
    compact = _compact_json_value(payload, text_limit=max(400, budget // 8))
    if isinstance(compact, dict):
        compact.setdefault("toolCallId", tool_call_id)
        compact["_v8ToolSurface"] = surface
    else:
        compact = {"items": compact, "_v8ToolSurface": surface}
    rendered = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(rendered) <= budget:
        return rendered

    minimal: dict[str, Any] = {"_v8ToolSurface": surface}
    if isinstance(payload, dict):
        for key in JSON_PRIORITY_KEYS:
            if key in payload:
                minimal[key] = _compact_json_value(payload.get(key), depth=1, text_limit=240)
    rendered = json.dumps(minimal, ensure_ascii=False, indent=2)
    if len(rendered) <= budget:
        return rendered
    return _head_tail_truncate_text(rendered, budget, f"semantic JSON output truncated; original length {original_len} chars")


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def record_raw_observation(
    *,
    tool_name: str,
    tool_call_id: str | None,
    runtime_kind: str,
    surface: str,
    raw_content: str,
    visible_content: str | None = None,
    budget_meta: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    observation_id = f"toolobs_{uuid.uuid4().hex}"
    raw_ref = f"toolobs://{observation_id}"
    try:
        from core.observability_db import observability_db

        observability_db.add_tool_observation_record(
            {
                "id": observation_id,
                "raw_ref": raw_ref,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "runtime_kind": runtime_kind,
                "surface": surface,
                "raw_chars": len(raw_content or ""),
                "visible_chars": len(visible_content if visible_content is not None else raw_content or ""),
                "raw_sha256": _hash_text(raw_content or ""),
                "raw_body": raw_content,
                "budget": dict(budget_meta or {}),
                "metadata": dict(metadata or {}),
                "created_at": utc_now_iso(),
            }
        )
    except Exception:
        # Raw refs should never break the agent-visible tool result.
        pass
    return raw_ref


def _copy_tool_message_with_budget(message: ToolMessage, content: str, budget_meta: dict[str, Any]) -> ToolMessage:
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    response_metadata = dict(getattr(message, "response_metadata", {}) or {})
    additional_kwargs["v8_tool_output_budget"] = budget_meta
    response_metadata["v8_tool_output_budget"] = budget_meta
    return message.model_copy(
        update={
            "content": content,
            "additional_kwargs": additional_kwargs,
            "response_metadata": response_metadata,
        }
    )


def apply_tool_surface_budget(
    message: ToolMessage,
    budget_meta: dict[str, Any] | None = None,
    *,
    tool_name: str | None = None,
    runtime_kind: str | None = None,
    surface: str = "tool_node",
) -> ToolMessage:
    content = message.content
    if not content:
        return message

    tool_name = str(tool_name or getattr(message, "name", "") or "").strip() or "unknown"
    tool_call_id = getattr(message, "tool_call_id", None)
    runtime_kind = str(runtime_kind or runtime_kind_for_tool(tool_name)).strip() or "native"
    original_content_str = content if isinstance(content, str) else str(content)
    content_str = original_content_str
    budget_meta = dict(budget_meta or {})
    budget = int(budget_meta.get("agentVisibleBudget") or DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS)
    raw_ref = record_raw_observation(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        runtime_kind=runtime_kind,
        surface=surface,
        raw_content=original_content_str,
        budget_meta=budget_meta,
    )

    budget_meta.update(
        {
            "toolCallId": tool_call_id,
            "runtimeKind": runtime_kind,
            "rawRef": raw_ref,
        }
    )

    strategy = "none"
    if len(content_str) > budget:
        notice = (
            "OUTPUT TRUNCATED BY DYNAMIC TOOL OUTPUT BUDGET. "
            f"Original length: {len(content_str)} chars; budget: {budget} chars"
        )
        marker_preserved = _truncate_worker_result_preserving_marker(content_str, budget, notice)
        if marker_preserved is not None:
            content_str = marker_preserved
            strategy = "worker_result_marker_preserving"
        else:
            json_truncated = _truncate_json_semantic(
                content_str,
                budget_meta,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                runtime_kind=runtime_kind,
                raw_ref=raw_ref,
            )
            if json_truncated is not None:
                content_str = json_truncated
                strategy = "json_priority_fields"
            else:
                surface_payload = _tool_surface_payload(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    runtime_kind=runtime_kind,
                    raw_ref=raw_ref,
                    budget_meta=budget_meta,
                    was_truncated=True,
                    strategy="head_tail_semantic_text",
                    omitted_chars=max(0, len(original_content_str) - budget),
                )
                text_budget = max(0, budget - len(json.dumps({"_v8ToolSurface": surface_payload}, ensure_ascii=False)) - 32)
                compact_text = _head_tail_truncate_text(content_str, max(1, text_budget), notice)
                content_str = json.dumps(
                    {
                        "summary": compact_text,
                        "_v8ToolSurface": surface_payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                strategy = "head_tail_semantic_text"
        budget_meta.update(
            {
                "wasBudgetTruncated": True,
                "semanticTruncationStrategy": strategy,
                "originalChars": len(original_content_str),
                "visibleChars": len(content_str),
            }
        )
    else:
        surface_payload = _tool_surface_payload(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            runtime_kind=runtime_kind,
            raw_ref=raw_ref,
            budget_meta=budget_meta,
            was_truncated=False,
            strategy="none",
        )
        content_str = _inject_surface_metadata(content_str, surface_payload, budget=budget)
        budget_meta.update(
            {
                "wasBudgetTruncated": False,
                "semanticTruncationStrategy": "none",
                "originalChars": len(original_content_str),
                "visibleChars": len(content_str),
            }
        )
    return _copy_tool_message_with_budget(message, content_str, budget_meta)


def apply_command_tool_surface_budget(command: Command, budget_meta: dict[str, Any] | None = None) -> Command:
    update = getattr(command, "update", None)
    if not isinstance(update, dict):
        return command
    messages = update.get("messages")
    if not isinstance(messages, list):
        return command

    changed = False
    next_messages = []
    for message in messages:
        if isinstance(message, ToolMessage):
            truncated = apply_tool_surface_budget(message, dict(budget_meta or {}))
            changed = changed or truncated is not message
            next_messages.append(truncated)
        else:
            next_messages.append(message)
    if not changed:
        return command
    next_update = dict(update)
    next_update["messages"] = next_messages
    return Command(
        graph=getattr(command, "graph", None),
        update=next_update,
        resume=getattr(command, "resume", None),
        goto=getattr(command, "goto", ()),
    )


def apply_agent_visible_budget(result: Any, budget_meta: dict[str, Any] | None = None):
    if isinstance(result, ToolMessage):
        return apply_tool_surface_budget(result, budget_meta)
    if isinstance(result, Command):
        return apply_command_tool_surface_budget(result, budget_meta)
    return result
