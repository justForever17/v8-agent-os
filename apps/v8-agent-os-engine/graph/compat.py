import uuid
from collections.abc import Mapping

from langchain_core.messages import AIMessage, ToolMessage

from core.response_normalizer import ensure_reasoning_content, sanitize_model_tool_calls


_SAME_BATCH_FILE_PRODUCERS = {"write_native_file"}
_SAME_BATCH_FILE_CONSUMERS = {"run_system_command", "command_session_broker"}


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
    return defer_same_batch_file_consumers(sanitize_model_tool_calls(response))
