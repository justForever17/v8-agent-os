from __future__ import annotations

import json
from typing import Any

from .backend import V8PromptUpdate


PRODUCT_AGENT_NAME = "V8OS 编程助手"


def compact_text(value: Any, *, max_chars: int = 1200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 36].rstrip()}\n\n...[已省略 {len(text) - max_chars + 36} 字]"


def markdown_update_from_v8(update: V8PromptUpdate) -> dict[str, Any]:
    body = compact_text(update.text)
    if not body:
        body = "V8OS 已更新当前任务状态。"
    return {
        "role": update.role,
        "kind": update.kind,
        "status": update.status,
        "content": body,
        "_meta": {
            "v8os": {
                "runId": update.run_id,
                "toolCallId": update.tool_call_id,
                "episodeId": update.episode_id,
                "detailRef": update.detail_ref,
                "rawRef": update.raw_ref,
            }
        },
    }


def compact_runtime_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    topic = str(event.get("topic") or payload.get("topic") or "runtime.update")
    summary = (
        payload.get("agentVisible")
        or payload.get("summary")
        or payload.get("message")
        or payload.get("text")
        or topic
    )
    text = compact_text(summary, max_chars=1000)
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            text = compact_text(parsed.get("summary") or parsed.get("message") or topic, max_chars=1000)
        except Exception:
            text = topic
    return {
        "role": "assistant",
        "kind": "event",
        "status": str(payload.get("status") or event.get("kind") or "running"),
        "content": text,
        "_meta": {
            "v8os": {
                "sessionId": event.get("session_id") or event.get("sessionId"),
                "runId": event.get("run_id") or event.get("runId"),
                "toolCallId": payload.get("toolCallId") or payload.get("tool_call_id"),
                "episodeId": payload.get("episodeId") or payload.get("episode_id"),
                "detailRef": payload.get("detailRef") or payload.get("rawRef"),
                "topic": topic,
            }
        },
    }


def permission_event_kind(kind: str | None) -> str:
    normalized = str(kind or "").strip().lower().replace("-", "_")
    if normalized in {"ask_user", "askuser", "human_question", "user_question"}:
        return "ask_user"
    if normalized in {"spec_approval", "spec_stage_approval", "requirements_approval", "design_approval", "tasks_approval"}:
        return "spec_approval"
    if any(token in normalized for token in ("file", "command", "shell", "safety", "approval", "permission")):
        return "permission"
    return "unknown"
