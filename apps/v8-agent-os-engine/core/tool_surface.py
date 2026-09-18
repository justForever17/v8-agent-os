from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .tool_surfaces.budget import (
    COMMAND_TOOL_NAMES,
    DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS,
    JSON_PRIORITY_KEYS,
    MAX_RESEARCH_DELIVERY_SURFACE_CHARS,
    TOOL_OBSERVATION_DETAIL_NAME,
    runtime_kind_for_tool,
    utc_now_iso,
)
from .tool_surfaces.formatting import (
    WORKER_RESULT_RE,
    _compact_json_value,
    _head_tail_truncate_text,
    _looks_like_structured_json_prefix,
    _tool_json_any_payload,
    _tool_surface_payload,
    _truncate_worker_result_preserving_marker,
)
from .tool_surfaces.extensions import (
    _render_audit_log_surface,
    _render_skill_instructions_surface,
)
from .tool_surfaces.terminal import (
    _command_agent_visible_surface,
    _decision_agent_visible_surface,
)

def _prune_agent_visible_json(value: Any) -> Any:
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key, item in value.items():
            nested = _prune_agent_visible_json(item)
            if nested in (None, "", [], {}):
                continue
            pruned[key] = nested
        return pruned
    if isinstance(value, list):
        return [
            nested
            for nested in (_prune_agent_visible_json(item) for item in value)
            if nested not in (None, "", [], {})
        ]
    return value
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
    payload = _prune_agent_visible_json(payload)
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
        compact["_v8ToolSurface"] = surface
    else:
        compact = {"items": compact, "_v8ToolSurface": surface}
    compact = _prune_agent_visible_json(compact)
    rendered = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(rendered) <= budget:
        return rendered

    minimal: dict[str, Any] = {"_v8ToolSurface": surface}
    if isinstance(payload, dict):
        for key in JSON_PRIORITY_KEYS:
            if key in payload:
                minimal[key] = _compact_json_value(payload.get(key), depth=1, text_limit=240)
    minimal = _prune_agent_visible_json(minimal)
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
    metadata_payload = dict(metadata or {})
    runtime_context: dict[str, Any] = {}
    try:
        from erc.runtime_context import get_runtime_context

        runtime_context = dict(get_runtime_context() or {})
    except Exception:
        runtime_context = {}
    run_id = str(
        (budget_meta or {}).get("runId")
        or (budget_meta or {}).get("run_id")
        or metadata_payload.get("runId")
        or metadata_payload.get("run_id")
        or runtime_context.get("run_id")
        or runtime_context.get("runId")
        or ""
    ).strip()
    session_id = str(
        metadata_payload.get("sessionId")
        or metadata_payload.get("session_id")
        or (budget_meta or {}).get("sessionId")
        or (budget_meta or {}).get("session_id")
        or runtime_context.get("session_id")
        or runtime_context.get("sessionId")
        or ""
    ).strip()
    workspace_path = str(
        metadata_payload.get("workspacePath")
        or metadata_payload.get("workspace_path")
        or (budget_meta or {}).get("workspacePath")
        or (budget_meta or {}).get("workspace_path")
        or runtime_context.get("workspace_path")
        or runtime_context.get("workspacePath")
        or ""
    ).strip()
    if run_id and not metadata_payload.get("runId"):
        metadata_payload["runId"] = run_id
    if session_id and not metadata_payload.get("sessionId"):
        metadata_payload["sessionId"] = session_id
    if workspace_path and not metadata_payload.get("workspacePath"):
        metadata_payload["workspacePath"] = workspace_path
    try:
        from core.observability_db import observability_db

        metadata_payload.setdefault("surfaceContract", "runtime-v1")
        metadata_payload.setdefault("rawSha256", _hash_text(raw_content or ""))
        observability_db.add_tool_observation_record(
            {
                "id": observation_id,
                "raw_ref": raw_ref,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "runtime_kind": runtime_kind,
                "surface": surface,
                "raw_chars": len(raw_content or ""),
                "visible_chars": len(visible_content or "") if visible_content is not None else 0,
                "raw_sha256": _hash_text(raw_content or ""),
                "raw_body": raw_content,
                "visible_body": visible_content,
                "budget": dict(budget_meta or {}),
                "metadata": metadata_payload,
                "created_at": utc_now_iso(),
            }
        )
    except Exception as exc:
        # Raw refs should never break the agent-visible tool result.
        print(f"[ToolSurface] Failed to persist observation for {tool_name}: {exc}")
        return ""
    return raw_ref
def _persist_agent_visible_observation(raw_ref: str, visible_content: str, budget_meta: dict[str, Any]) -> None:
    if not raw_ref:
        return
    try:
        from core.observability_db import observability_db

        observability_db.update_tool_observation_visible_surface(
            raw_ref,
            visible_content=visible_content,
            budget=dict(budget_meta or {}),
        )
    except Exception as exc:
        print(f"[ToolSurface] Failed to persist agent-visible surface for {raw_ref}: {exc}")
def _copy_tool_message_with_budget(message: ToolMessage, content: str, budget_meta: dict[str, Any]) -> ToolMessage:
    _persist_agent_visible_observation(str(budget_meta.get("rawRef") or ""), content, budget_meta)
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    response_metadata = dict(getattr(message, "response_metadata", {}) or {})
    additional_kwargs["v8_tool_output_budget"] = budget_meta
    if getattr(message, "name", "") == "device_broker":
        try:
            device_payload = json.loads(message.content)
        except (ValueError, TypeError):
            device_payload = None
        if isinstance(device_payload, dict):
            additional_kwargs["v8_device_execution"] = {
                key: device_payload[key] for key in ("status", "ok", "code", "businessVerification") if key in device_payload
            }
    # Keep native command lifecycle facts when the Agent Surface renders JSON
    # as terminal text. This receipt is runtime metadata, never inferred from
    # the model's prose or from a session admission being labelled "ok".
    if getattr(message, "name", "") in {"run_system_command", "command_session_broker", "read_background_output"}:
        try:
            command_payload = json.loads(message.content)
        except (ValueError, TypeError):
            command_payload = None
        if isinstance(command_payload, dict) and command_payload.get("kind") in {"command_result", "command_session"}:
            additional_kwargs["v8_command_execution"] = {
                key: command_payload[key]
                for key in ("kind", "ok", "state", "commandId", "sessionId", "returnCode", "cursor", "nextCursor")
                if key in command_payload
            }
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
    raw_ref = ""
    if tool_name != TOOL_OBSERVATION_DETAIL_NAME:
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
        }
    )
    if raw_ref:
        budget_meta["rawRef"] = raw_ref
    else:
        budget_meta.pop("rawRef", None)

    if tool_name in COMMAND_TOOL_NAMES and WORKER_RESULT_RE.search(content_str or ""):
        notice = (
            "OUTPUT TRUNCATED BY DYNAMIC TOOL OUTPUT BUDGET. "
            f"Original length: {len(content_str)} chars; budget: {budget} chars"
        )
        marker_preserved = _truncate_worker_result_preserving_marker(content_str, budget, notice)
        if marker_preserved is not None:
            budget_meta.update(
                {
                    "wasBudgetTruncated": len(content_str) > len(marker_preserved),
                    "semanticTruncationStrategy": "worker_result_marker_preserving",
                    "originalChars": len(original_content_str),
                    "visibleChars": len(marker_preserved),
                }
            )
            return _copy_tool_message_with_budget(message, marker_preserved, budget_meta)

    if tool_name in COMMAND_TOOL_NAMES:
        command_surface = _command_agent_visible_surface(
            tool_name=tool_name,
            content=content_str,
            raw_ref=raw_ref,
            budget=budget,
        )
        if command_surface is not None:
            was_truncated = len(command_surface) > budget
            if was_truncated:
                command_surface = _head_tail_truncate_text(
                    command_surface,
                    budget,
                    f"command output truncated; rawRef={raw_ref}",
                )
            budget_meta.update(
                {
                    "wasBudgetTruncated": was_truncated,
                    "semanticTruncationStrategy": "command_terminal_surface",
                    "originalChars": len(original_content_str),
                    "visibleChars": len(command_surface),
                }
            )
            return _copy_tool_message_with_budget(message, command_surface, budget_meta)

    if "not a valid tool" in content_str.lower():
        invalid_surface = (
            content_str
            if len(content_str) <= budget
            else _head_tail_truncate_text(
                content_str,
                budget,
                f"invalid tool response truncated; rawRef={raw_ref}",
            )
        )
        budget_meta.update(
            {
                "wasBudgetTruncated": len(invalid_surface) < len(content_str),
                "semanticTruncationStrategy": "invalid_tool_error",
                "originalChars": len(original_content_str),
                "visibleChars": len(invalid_surface),
            }
        )
        return _copy_tool_message_with_budget(message, invalid_surface, budget_meta)

    if tool_name == "fetch_skill_instructions":
        skill_surface = _render_skill_instructions_surface(content_str, raw_ref, budget=budget)
        budget_meta.update(
            {
                "wasBudgetTruncated": len(skill_surface) < len(content_str),
                "semanticTruncationStrategy": "skill_instructions_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(skill_surface),
            }
        )
        return _copy_tool_message_with_budget(message, skill_surface, budget_meta)

    if tool_name == "read_audit_log":
        audit_surface = _render_audit_log_surface(content_str, raw_ref, budget=budget)
        budget_meta.update(
            {
                "wasBudgetTruncated": len(audit_surface) < len(content_str),
                "semanticTruncationStrategy": "audit_log_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(audit_surface),
            }
        )
        return _copy_tool_message_with_budget(message, audit_surface, budget_meta)

    decision_surface = _decision_agent_visible_surface(
        tool_name=tool_name,
        content=content_str,
        raw_ref=raw_ref,
        budget=budget,
    )
    if decision_surface is not None:
        preserve_full_research = tool_name == "research_broker" and decision_surface.startswith("Research answer\n")
        decision_limit = MAX_RESEARCH_DELIVERY_SURFACE_CHARS if preserve_full_research else budget
        was_truncated = len(decision_surface) > decision_limit
        if was_truncated:
            decision_surface = _head_tail_truncate_text(
                decision_surface,
                decision_limit,
                (
                    f"research delivery surface truncated at {MAX_RESEARCH_DELIVERY_SURFACE_CHARS} chars; rawRef={raw_ref}"
                    if preserve_full_research
                    else f"decision surface truncated; rawRef={raw_ref}"
                ),
            )
        budget_meta.update(
            {
                "wasBudgetTruncated": was_truncated,
                "semanticTruncationStrategy": (
                    "research_bounded_delivery_surface" if preserve_full_research else "decision_summary_surface"
                ),
                "originalChars": len(original_content_str),
                "visibleChars": len(decision_surface),
            }
        )
        return _copy_tool_message_with_budget(message, decision_surface, budget_meta)

    if _looks_like_structured_json_prefix(content_str) and _tool_json_any_payload(content_str) is None:
        malformed_surface = "\n".join(
            line
            for line in (
                f"{str(tool_name or 'tool').replace('_', ' ')} returned incomplete structured output.",
                "The partial JSON is hidden because it is not safe or useful as an agent/client result.",
                f"Detail: tool_observation_detail(raw_ref='{raw_ref}')" if raw_ref else "",
            )
            if line
        )
        budget_meta.update(
            {
                "wasBudgetTruncated": True,
                "semanticTruncationStrategy": "malformed_structured_output_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(malformed_surface),
            }
        )
        return _copy_tool_message_with_budget(message, malformed_surface, budget_meta)

    if tool_name == TOOL_OBSERVATION_DETAIL_NAME:
        was_truncated = len(content_str) > budget
        if was_truncated:
            from core.tool_observation_detail import budget_plain_observation_page

            content_str = budget_plain_observation_page(content_str, budget) or _head_tail_truncate_text(
                content_str,
                budget,
                f"tool observation detail truncated; original length {len(original_content_str)} chars",
            )
        budget_meta.update(
            {
                "wasBudgetTruncated": was_truncated,
                "semanticTruncationStrategy": "tool_observation_detail_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(content_str),
            }
        )
        return _copy_tool_message_with_budget(message, content_str, budget_meta)

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
def apply_command_tool_surface_budget(
    command: Command, budget_meta: dict[str, Any] | None = None, *, tool_name: str | None = None,
) -> Command:
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
            truncated = apply_tool_surface_budget(
                message, dict(budget_meta or {}), tool_name=getattr(message, "name", None) or tool_name,
            )
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
def apply_agent_visible_budget(
    result: Any, budget_meta: dict[str, Any] | None = None, *, tool_name: str | None = None,
):
    if isinstance(result, ToolMessage):
        return apply_tool_surface_budget(result, budget_meta, tool_name=getattr(result, "name", None) or tool_name)
    if isinstance(result, Command):
        return apply_command_tool_surface_budget(result, budget_meta, tool_name=tool_name)
    return result
