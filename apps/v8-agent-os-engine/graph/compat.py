import json
import uuid
from collections.abc import Mapping

from langchain_core.messages import AIMessage, ToolMessage

from core.response_normalizer import ensure_reasoning_content, sanitize_model_tool_calls


_SAME_BATCH_FILE_PRODUCERS = {"write_native_file"}
_SAME_BATCH_FILE_CONSUMERS = {"run_system_command", "command_session_broker"}

# A model can occasionally emit the same call twice in one AI message.  Those
# calls cannot observe each other's result, so executing both only amplifies
# side effects and inflates the visible tool count.  Keep this allowlist narrow:
# it covers the tools seen in the duplicate-call incidents, while leaving
# genuinely parallel domain calls untouched.
_SAME_BATCH_DEDUPE_TOOLS = {
    "write_native_file",
    "write_file",
    "apply_patch",
    "read_native_file",
    "run_system_command",
    "command_session_broker",
    "read_background_output",
    "send_background_input",
    "memory_broker",
    "session_context_broker",
    "update_todo",
    "write_todos",
}


def _tool_call_name(call) -> str:
    if not isinstance(call, Mapping):
        return str(getattr(call, "name", "") or "").strip()
    for key in ("name", "toolName", "tool_name"):
        value = call.get(key)
        if value:
            return str(value).strip()
    for key in ("function", "functionCall", "function_call"):
        nested = call.get(key)
        if isinstance(nested, Mapping) and nested.get("name"):
            return str(nested.get("name") or "").strip()
    return ""


def _tool_call_id(call) -> str:
    if not isinstance(call, Mapping):
        return str(getattr(call, "id", "") or "").strip()
    for key in ("id", "tool_call_id", "toolCallId", "providerToolCallId"):
        value = call.get(key)
        if value:
            return str(value).strip()
    for key in ("function", "functionCall", "function_call"):
        nested = call.get(key)
        if isinstance(nested, Mapping) and nested.get("id"):
            return str(nested.get("id") or "").strip()
    return ""


def _tool_call_args(call):
    if not isinstance(call, Mapping):
        return getattr(call, "args", None)
    for key in ("args", "input", "arguments", "parameters"):
        if key in call:
            value = call.get(key)
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (TypeError, ValueError):
                    return value
            return value
    for key in ("function", "functionCall", "function_call"):
        nested = call.get(key)
        if isinstance(nested, Mapping):
            return _tool_call_args(nested)
    return None


def _same_batch_tool_signature(call):
    name = _tool_call_name(call)
    if name not in _SAME_BATCH_DEDUPE_TOOLS:
        return None
    try:
        args = json.dumps(
            _tool_call_args(call),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return None
    return f"{name}:{args}"


def dedupe_same_batch_tool_calls(response):
    """Drop exact duplicate calls emitted in one model response.

    This is deliberately not a cross-turn or fuzzy dedupe.  A later turn may
    legitimately poll the same resource after observing a new result; only an
    identical call in the same response is removed.
    """

    calls = list(getattr(response, "tool_calls", None) or [])
    if len(calls) < 2:
        return response

    seen = set()
    kept = []
    dropped = []
    for call in calls:
        signature = _same_batch_tool_signature(call)
        if signature is not None and signature in seen:
            dropped.append({"id": _tool_call_id(call), "name": _tool_call_name(call)})
            continue
        if signature is not None:
            seen.add(signature)
        kept.append(call)

    if not dropped:
        return response

    response.tool_calls = kept
    additional_kwargs = dict(getattr(response, "additional_kwargs", None) or {})
    raw_calls = additional_kwargs.get("tool_calls")
    if isinstance(raw_calls, list):
        raw_seen = set()
        filtered_raw_calls = []
        for call in raw_calls:
            signature = _same_batch_tool_signature(call)
            if signature is not None and signature in raw_seen:
                continue
            if signature is not None:
                raw_seen.add(signature)
            filtered_raw_calls.append(call)
        additional_kwargs["tool_calls"] = filtered_raw_calls
    additional_kwargs["v8_deduplicated_tool_calls"] = dropped
    response.additional_kwargs = additional_kwargs

    for attribute in ("content", "content_blocks"):
        blocks = getattr(response, attribute, None)
        if not isinstance(blocks, list):
            continue
        block_seen = set()
        filtered_blocks = []
        for block in blocks:
            tool_call = _content_block_tool_call(block)
            signature = _same_batch_tool_signature(tool_call) if tool_call is not None else None
            if signature is not None and signature in block_seen:
                continue
            if signature is not None:
                block_seen.add(signature)
            filtered_blocks.append(block)
        setattr(response, attribute, filtered_blocks)
    return response


def _content_block_tool_call(block):
    if not isinstance(block, Mapping):
        return None
    if _tool_call_name(block):
        return block
    for key in ("function", "functionCall", "function_call"):
        nested = block.get(key)
        if isinstance(nested, Mapping) and _tool_call_name(nested):
            return nested
    return None


def defer_same_batch_file_consumers(response):
    """Split a write→execute dependency into separate model turns.

    Independent writes remain parallel.  Only the narrow, repeatedly observed
    unsafe batch is corrected: a native file producer and a shell/session
    consumer in the same model response.  The consumer can be requested on the
    next turn after the write ToolMessage exists, so no capability is removed.
    """

    calls = list(getattr(response, "tool_calls", None) or [])
    names = {_tool_call_name(call) for call in calls}
    if not (names & _SAME_BATCH_FILE_PRODUCERS and names & _SAME_BATCH_FILE_CONSUMERS):
        return response

    deferred = [call for call in calls if _tool_call_name(call) in _SAME_BATCH_FILE_CONSUMERS]
    kept = [call for call in calls if _tool_call_name(call) not in _SAME_BATCH_FILE_CONSUMERS]
    response.tool_calls = kept

    additional_kwargs = dict(getattr(response, "additional_kwargs", None) or {})
    if isinstance(additional_kwargs.get("tool_calls"), list):
        additional_kwargs["tool_calls"] = [
            call
            for call in additional_kwargs["tool_calls"]
            if _tool_call_name(call) not in _SAME_BATCH_FILE_CONSUMERS
        ]
    additional_kwargs["v8_deferred_dependent_tool_calls"] = [
        {
            "id": _tool_call_id(call),
            "name": _tool_call_name(call),
            "reason": "await_native_file_write_result",
        }
        for call in deferred
    ]
    response.additional_kwargs = additional_kwargs

    for attribute in ("content", "content_blocks"):
        blocks = getattr(response, attribute, None)
        if not isinstance(blocks, list):
            continue
        filtered = []
        for block in blocks:
            tool_call = _content_block_tool_call(block)
            if tool_call is not None and _tool_call_name(tool_call) in _SAME_BATCH_FILE_CONSUMERS:
                continue
            filtered.append(block)
        setattr(response, attribute, filtered)
    return response


def sanitize_message_chain(messages):
    """Keep only valid AI/tool-call pairs for strict provider compatibility."""
    ai_tc_ids = set()
    tool_msg_ids = set()
    for message in messages:
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            for tool_call in message.tool_calls:
                if "id" in tool_call:
                    ai_tc_ids.add(tool_call["id"])
        elif isinstance(message, ToolMessage):
            tool_msg_ids.add(message.tool_call_id)

    valid_tc_ids = ai_tc_ids & tool_msg_ids
    orphaned_ai_ids = ai_tc_ids - tool_msg_ids
    orphaned_tool_ids = tool_msg_ids - ai_tc_ids

    if orphaned_ai_ids or orphaned_tool_ids:
        print(
            f"[Sanitizer] Found {len(orphaned_ai_ids)} orphaned tool_calls (no response), "
            f"{len(orphaned_tool_ids)} orphaned tool messages (no parent)"
        )

    sanitized = []
    for message in messages:
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            kept_tool_calls = [tool_call for tool_call in message.tool_calls if tool_call.get("id") in valid_tc_ids]

            if len(kept_tool_calls) == len(message.tool_calls):
                sanitized.append(message)
            elif kept_tool_calls:
                from copy import deepcopy

                clean_message = deepcopy(message)
                clean_message.tool_calls = kept_tool_calls
                if "tool_calls" in clean_message.additional_kwargs:
                    clean_message.additional_kwargs["tool_calls"] = [
                        tool_call
                        for tool_call in clean_message.additional_kwargs["tool_calls"]
                        if tool_call.get("id") in valid_tc_ids
                    ]
                sanitized.append(clean_message)
            else:
                clean_content = message.content or "(Agent routing)"
                clean_message = AIMessage(
                    content=clean_content,
                    id=message.id or str(uuid.uuid4()),
                    additional_kwargs={
                        key: value for key, value in message.additional_kwargs.items() if key != "tool_calls"
                    },
                    response_metadata=dict(getattr(message, "response_metadata", {}) or {}),
                    usage_metadata=getattr(message, "usage_metadata", None),
                )
                sanitized.append(ensure_reasoning_content(clean_message))
        elif isinstance(message, ToolMessage):
            if message.tool_call_id in valid_tc_ids:
                sanitized.append(message)
            else:
                print(
                    f"[Sanitizer] Removing orphaned ToolMessage "
                    f"(tool_call_id={message.tool_call_id}, name={message.name})"
                )
        else:
            sanitized.append(message)

    return sanitized


def sanitize_response_tool_calls(response):
    """Repair fragmented tool calls and normalize reasoning fields."""
    normalized = sanitize_model_tool_calls(response)
    normalized = dedupe_same_batch_tool_calls(normalized)
    return defer_same_batch_file_consumers(normalized)
