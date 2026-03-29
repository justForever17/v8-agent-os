import uuid

from langchain_core.messages import AIMessage, ToolMessage

from core.response_normalizer import ensure_reasoning_content, sanitize_model_tool_calls


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
    return sanitize_model_tool_calls(response)
