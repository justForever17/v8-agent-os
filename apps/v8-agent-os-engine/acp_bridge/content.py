"""ACP v1 content projection; Engine remains the owner of execution truth."""
from __future__ import annotations
import json
from .protocol import JsonRpcError


def prompt_text(value):
    if not isinstance(value, list) or not value:
        raise JsonRpcError(-32602, "prompt must be a non-empty ACP ContentBlock array.")
    parts = []
    for block in value:
        if not isinstance(block, dict):
            raise JsonRpcError(-32602, "Each prompt content block must be an object.")
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind == "resource" and isinstance(block.get("resource"), dict):
            resource = block["resource"]
            if not isinstance(resource.get("text"), str):
                raise JsonRpcError(-32602, "Only embedded text resources are supported.")
            parts.append(f"Resource: {resource.get('uri', '')}\n{resource['text']}")
        elif kind == "resource_link" and isinstance(block.get("uri"), str):
            parts.append(f"Referenced resource (content not attached): {block['uri']}")
        else:
            raise JsonRpcError(-32602, f"Unsupported ACP prompt content type: {kind}.")
    text = "\n\n".join(parts)
    if not text.strip():
        raise JsonRpcError(-32602, "prompt cannot be empty.")
    return text


def text_update(text, *, thought=False, user=False, message_id=None, meta=None):
    result = {"sessionUpdate": "user_message_chunk" if user else "agent_thought_chunk" if thought else "agent_message_chunk", "content": {"type": "text", "text": text}}
    if message_id:
        result["messageId"] = message_id
    if meta:
        result["_meta"] = {"v8os": meta}
    return result


def event_data(event):
    return event.get("data") if isinstance(event.get("data"), dict) else event.get("payload") if isinstance(event.get("payload"), dict) else {}


def event_run_id(event):
    data = event_data(event)
    return str(event.get("runId") or event.get("run_id") or data.get("runId") or data.get("run_id") or "")


def event_kind(event):
    data = event_data(event)
    kind = event.get("type") or data.get("type")
    return event.get("name") or data.get("name") if kind == "custom_event" else kind


def tool_update(tool_id, title, status, output, meta, seen_tools):
    update = {"sessionUpdate": "tool_call_update" if tool_id in seen_tools else "tool_call", "toolCallId": tool_id, "title": title, "kind": "other", "status": status, "_meta": {"v8os": meta}}
    seen_tools.add(tool_id)
    if output not in (None, ""):
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        update["content"] = [{"type": "content", "content": {"type": "text", "text": text}}]
    return update


def runtime_update(event, *, seen_tools):
    data = event_data(event)
    kind = event_kind(event)
    run_id = event_run_id(event)
    meta = {"runId": run_id, "topic": event.get("topic"), "detailRef": data.get("detailRef") or data.get("rawRef")}
    if kind in {"text_chunk", "reasoning_chunk"}:
        text = event.get("content") if isinstance(event.get("content"), str) else data.get("content") or data.get("text") or ""
        if event.get("runtimeId", "chat") not in {"chat", "supervisor", None}:
            return tool_update(f"runtime:{event.get('runtimeId')}:{run_id}", str(event.get("runtimeId")), "in_progress", text, meta, seen_tools)
        return text_update(str(text), thought=kind == "reasoning_chunk", message_id=event.get("messageId") or data.get("messageId") or run_id or None, meta=meta)
    if kind in {"tool_start", "tool_result", "tool_call"}:
        tool = event.get("tool") if isinstance(event.get("tool"), dict) else data
        tool_id = str(tool.get("toolCallId") or tool.get("toolInvocationId") or data.get("toolCallId") or data.get("tool_call_id") or "")
        if not tool_id:
            raise JsonRpcError(-32000, "V8OS tool event has no tool call identity.")
        status = str(tool.get("resultStatus") or data.get("status") or "")
        state = "in_progress" if kind in {"tool_start", "tool_call"} else "failed" if status in {"failed", "error", "blocked", "timed_out", "terminated"} else "completed" if status in {"completed", "succeeded", "success", "ok"} else "pending"
        meta["resultStatus"] = status or "unknown"
        # Preserve the full audience-specific tool result; no adapter truncation.
        output = tool.get("agentVisibleResult", tool.get("result", data.get("result", "")))
        update = tool_update(tool_id, str(tool.get("toolName") or data.get("toolName") or "Tool"), state, output, meta, seen_tools)
        if kind in {"tool_start", "tool_call"}:
            update["rawInput"] = tool.get("args", data.get("args", {}))
        return update
    if kind in {"runtime_progress", "runtime_event", "artifact_recorded"}:
        title = str(data.get("summary") or data.get("message") or event.get("content") or event.get("topic") or kind)
        activity_id = str(data.get("episodeId") or event.get("runtimeId") or "runtime")
        state = str(data.get("status") or event.get("status") or "")
        status = "completed" if state in {"complete", "completed", "succeeded"} else "failed" if state in {"failed", "error", "blocked"} else "in_progress"
        return tool_update(f"activity:{run_id}:{activity_id}", title, status, title, meta, seen_tools)
    return None


def history_updates(record):
    messages = record.get("messages") or record.get("timeline") or (record.get("snapshot") or {}).get("messages") or []
    seen_tools = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or message.get("authorRole") or "").lower()
        if role not in {"user", "assistant", "supervisor"}:
            continue
        run_id = message.get("runId") or message.get("run_id")
        nodes = message.get("nodes") or []
        narrative = False
        for node in nodes:
            if not isinstance(node, dict) or node.get("displayInMessage") is False:
                continue
            execution = node.get("executionType")
            if execution in {"tool_call", "tool_result"}:
                yield runtime_update({"type": "tool_start" if execution == "tool_call" else "tool_result", "runId": run_id, "tool": node}, seen_tools=seen_tools)
            elif execution == "reasoning" and isinstance(node.get("content"), str):
                yield text_update(node["content"], thought=True, message_id=message.get("id"), meta={"runId": run_id})
            elif node.get("kind") == "narrative" and isinstance(node.get("content"), str):
                narrative = True
                yield text_update(node["content"], user=role == "user", message_id=message.get("id"), meta={"runId": run_id})
        if narrative:
            continue
        text = message.get("content")
        if not isinstance(text, str):
            text = message.get("text") if isinstance(message.get("text"), str) else ""
        if text:
            yield text_update(text, user=role == "user", message_id=message.get("id") or message.get("messageId"), meta={"runId": run_id})
