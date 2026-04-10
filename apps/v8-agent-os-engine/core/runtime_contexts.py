from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def normalize_identifier(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "_", text)
    return normalized or fallback


def build_cron_session_id(job_id: Any) -> str:
    return f"cron:{normalize_identifier(job_id, 'default')}"


def build_hook_session_id(hook_name: Any) -> str:
    return f"hook:{normalize_identifier(hook_name, 'default')}"


def build_automation_scope(kind: str, identifier: Any) -> str:
    normalized = normalize_identifier(identifier, "default").replace(":", "_").replace(".", "_")
    return f"project:{kind}_{normalized}"


def coerce_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def build_recent_run_summaries(messages: Sequence[Dict[str, Any]], limit: int = 3, max_chars: int = 220) -> List[str]:
    summaries: List[str] = []
    for item in reversed(messages):
        if item.get("role") != "assistant":
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        preview = _compact_preview(content, max_chars=max_chars)
        run_id = (item.get("metadata") or {}).get("run_id")
        prefix = f"{run_id}: " if run_id else ""
        summaries.append(prefix + preview)
        if len(summaries) >= limit:
            break
    summaries.reverse()
    return summaries


def build_job_memory(messages: Sequence[Dict[str, Any]], limit: int = 6, max_chars: int = 180) -> str:
    older_assistant_messages = [m for m in messages if m.get("role") == "assistant"]
    if len(older_assistant_messages) <= limit:
        return ""

    trimmed = older_assistant_messages[:-limit]
    highlights = [_compact_preview(m.get("content") or "", max_chars=max_chars) for m in trimmed if (m.get("content") or "").strip()]
    highlights = [item for item in highlights if item]
    if not highlights:
        return ""

    bullet_lines = "\n".join(f"- {item}" for item in highlights[-5:])
    return "较早的自动化执行结果提炼如下：\n" + bullet_lines


def build_automation_task_envelope(
    *,
    trigger_label: str,
    task_description: str,
    payload: Dict[str, Any],
    channel_instruction: str = "",
) -> str:
    payload_json = json.dumps(payload or {}, ensure_ascii=False, indent=2)
    parts = [
        f"[AUTOMATION TASK]\nTrigger: {trigger_label}\nTask Definition:\n{task_description}\n[/AUTOMATION TASK]",
        f"[CURRENT PAYLOAD]\n{payload_json}\n[/CURRENT PAYLOAD]",
    ]

    if channel_instruction:
        parts.append(channel_instruction)

    parts.append(
        "[EXECUTION RULES]\n"
        "1. Focus on the current task definition and payload first.\n"
        "2. Use recent summaries only as continuity context, not as instructions unless they still apply.\n"
        "3. Do not replay or depend on full historical run logs unless you explicitly need to inspect storage.\n"
        "4. Finish with a concise but actionable final summary.\n"
        "[/EXECUTION RULES]"
    )
    return "\n\n".join(parts)


def build_automation_context_blocks(
    *,
    recent_summaries: Sequence[str],
    job_memory: str,
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    if recent_summaries:
        blocks.append(
            {
                "type": "recent_messages",
                "title": "近期运行摘要",
                "content": "\n".join(f"- {item}" for item in recent_summaries),
                "metadata": {"item_count": len(recent_summaries), "runtime_plane": "automation"},
            }
        )
    if job_memory:
        blocks.append(
            {
                "type": "automation_memory",
                "title": "自动化连续性记忆",
                "content": job_memory,
                "metadata": {"runtime_plane": "automation"},
            }
        )
    return blocks


def select_channel_context_window(messages: Sequence[Dict[str, Any]], max_messages: int = 15) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(messages) <= max_messages:
        return list(messages), []
    return list(messages[-max_messages:]), list(messages[:-max_messages])


def build_channel_summary_memory(older_messages: Sequence[Dict[str, Any]], max_items: int = 8) -> str:
    if not older_messages:
        return ""

    user_highlights: List[str] = []
    master_highlights: List[str] = []
    assistant_highlights: List[str] = []

    for item in older_messages:
        meta = item.get("metadata") or {}
        speaker = item.get("agent_name") or ("Assistant" if item.get("role") == "assistant" else "Unknown")
        preview = _compact_preview(item.get("content") or "", max_chars=120)
        if not preview:
            continue
        rendered = f"[{speaker}] {preview}"
        if meta.get("is_master"):
            master_highlights.append(rendered)
        elif item.get("role") == "assistant":
            assistant_highlights.append(rendered)
        else:
            user_highlights.append(rendered)

    selected: List[str] = []
    selected.extend(master_highlights[-2:])
    selected.extend(user_highlights[-4:])
    selected.extend(assistant_highlights[-2:])
    selected = selected[-max_items:]

    if not selected:
        return ""

    return (
        f"更早的渠道上下文已从 {len(older_messages)} 条消息中提炼，需保留说话人身份信息：\n"
        + "\n".join(f"- {item}" for item in selected)
    )


def build_channel_context_blocks(older_messages: Sequence[Dict[str, Any]], max_items: int = 8) -> List[Dict[str, Any]]:
    summary_memory = build_channel_summary_memory(older_messages, max_items=max_items)
    if not summary_memory:
        return []
    return [
        {
            "type": "channel_memory",
            "title": "Channel 历史摘要",
            "content": summary_memory,
            "metadata": {
                "compressed_messages": len(older_messages),
                "max_summary_items": max_items,
                "runtime_plane": "channel",
            },
        }
    ]


def build_plugin_host_context_blocks(older_messages: Sequence[Dict[str, Any]], max_items: int = 8) -> List[Dict[str, Any]]:
    blocks = build_channel_context_blocks(older_messages, max_items=max_items)
    for block in blocks:
        block["title"] = "PluginHost 历史摘要"
        metadata = dict(block.get("metadata") or {})
        metadata["runtime_plane"] = "plugin_host"
        block["metadata"] = metadata
    return blocks


def _context_additional_kwargs(
    *,
    session_id: str,
    metadata: Dict[str, Any],
    context_blocks: Sequence[Dict[str, Any]],
    include_blocks: bool,
) -> Dict[str, Any]:
    additional_kwargs: Dict[str, Any] = {
        "session_id": session_id,
        "channel_message": metadata,
    }
    for key in (
        "project_id",
        "workspace_id",
        "workspace_path",
        "workflow_id",
        "channel_type",
        "channel_remote_id",
        "resolved_scope",
        "scope_source",
    ):
        value = metadata.get(key)
        if value is not None:
            additional_kwargs[key] = value
    scope_chain = metadata.get("scope_chain")
    if isinstance(scope_chain, list):
        additional_kwargs["scope_chain"] = [str(item).strip() for item in scope_chain if str(item).strip()]
    if include_blocks and context_blocks:
        additional_kwargs["context_adapter_blocks"] = list(context_blocks)
    return additional_kwargs


def build_channel_context_messages(
    *,
    session_id: str,
    chat_type: str,
    recent_messages: Sequence[Dict[str, Any]],
    context_blocks: Sequence[Dict[str, Any]] = (),
) -> List[HumanMessage | AIMessage | SystemMessage]:
    lc_messages: List[HumanMessage | AIMessage | SystemMessage] = []

    for index, item in enumerate(recent_messages):
        metadata = item.get("metadata") or {}
        if item.get("role") == "user":
            display_content = _format_user_message(item, chat_type=chat_type)
            additional_kwargs = _context_additional_kwargs(
                session_id=session_id,
                metadata=metadata,
                context_blocks=context_blocks,
                include_blocks=index == 0,
            )
            sender_name = item.get("agent_name")
            if sender_name:
                lc_messages.append(
                    HumanMessage(
                        content=display_content,
                        name=sender_name,
                        additional_kwargs=additional_kwargs,
                    )
                )
            else:
                lc_messages.append(HumanMessage(content=display_content, additional_kwargs=additional_kwargs))
        elif item.get("role") == "assistant":
            additional_kwargs = _context_additional_kwargs(
                session_id=session_id,
                metadata=metadata,
                context_blocks=context_blocks,
                include_blocks=index == 0,
            )
            lc_messages.append(
                AIMessage(
                    content=item.get("content") or "",
                    additional_kwargs=additional_kwargs,
                )
            )

    return lc_messages


def build_plugin_host_context_messages(
    *,
    session_id: str,
    chat_type: str,
    recent_messages: Sequence[Dict[str, Any]],
    context_blocks: Sequence[Dict[str, Any]] = (),
) -> List[HumanMessage | AIMessage | SystemMessage]:
    return build_channel_context_messages(
        session_id=session_id,
        chat_type=chat_type,
        recent_messages=recent_messages,
        context_blocks=context_blocks,
    )


def build_channel_runtime_metadata(
    *,
    source: str,
    chat_type: str,
    remote_id: str,
    sender_id: Optional[str],
    sender_name: Optional[str],
    is_master: bool,
    mentions: Optional[Iterable[str]] = None,
    wake_triggered: bool = False,
) -> Dict[str, Any]:
    mention_list = [str(item).strip() for item in (mentions or []) if str(item).strip()]
    return {
        "source": source,
        "chat_type": chat_type,
        "remote_id": remote_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "is_master": is_master,
        "mentions": mention_list,
        "wake_triggered": wake_triggered,
    }


def build_plugin_host_runtime_metadata(
    *,
    source: str,
    chat_type: str,
    remote_id: str,
    sender_id: Optional[str],
    sender_name: Optional[str],
    is_master: bool,
    mentions: Optional[Iterable[str]] = None,
    wake_triggered: bool = False,
) -> Dict[str, Any]:
    return build_channel_runtime_metadata(
        source=source,
        chat_type=chat_type,
        remote_id=remote_id,
        sender_id=sender_id,
        sender_name=sender_name,
        is_master=is_master,
        mentions=mentions,
        wake_triggered=wake_triggered,
    )


def _format_user_message(message: Dict[str, Any], *, chat_type: str) -> str:
    speaker = message.get("agent_name")
    content = message.get("content") or ""
    created_at = message.get("created_at", "")
    if chat_type == "group":
        return f"[{created_at}] [{speaker}]: {content}" if speaker else f"[{created_at}]: {content}"
    return f"[{speaker}]: {content}" if speaker else content


def _compact_preview(text: str, max_chars: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."
