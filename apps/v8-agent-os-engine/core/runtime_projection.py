from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import re
from typing import Any, Dict, List, Optional

from core.context_governance import normalize_context_audit
from core.json_safe import to_jsonable
from core.multimodal_payload_adapter import normalize_artifact_record
from core.storage import storage


def _default_supervisor_profile() -> Dict[str, Any]:
    profile = storage.get_supervisor_profile()
    return {
        "agentName": profile.get("name") or "智能主管",
        "agentAvatar": profile.get("avatar") or "",
        "agentRoleLabel": profile.get("roleLabel") or "主理人",
    }


_SUPERVISOR_AGENT_IDS = {"supervisor", "system_supervisor", "system-supervisor"}


def _canonical_agent_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_supervisor_agent_id(value: Any) -> bool:
    return _canonical_agent_id(value) in _SUPERVISOR_AGENT_IDS


def _durable_agent_profile(row: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    agent_id = (
        row.get("agent_id")
        or metadata.get("agentId")
        or metadata.get("agent_id")
        or metadata.get("ownerAgentId")
        or metadata.get("owner_agent_id")
    )
    agent_kind = str(
        metadata.get("agentType")
        or metadata.get("agent_type")
        or metadata.get("ownerAgentKind")
        or metadata.get("owner_agent_kind")
        or ""
    ).strip().lower()
    if row.get("role") == "assistant" and (
        _is_supervisor_agent_id(agent_id) or agent_kind == "supervisor"
    ):
        return _default_supervisor_profile()
    return {
        "agentName": row.get("agent_name"),
        "agentAvatar": row.get("agent_avatar"),
        "agentRoleLabel": row.get("agent_role_label"),
    }

STATUS_LABELS = {
    "created": "待运行",
    "queued": "排队中",
    "running": "进行中",
    "waiting_approval": "等待审批",
    "waiting_input": "等待继续",
    "waiting_external_tool": "等待外部工具",
    "abandoned": "已放弃",
    "paused": "已暂停",
    "recoverable_failed": "可恢复失败",
    "failed": "失败",
    "cancelled": "已取消",
    "completed": "已完成",
}

TOOL_INPUT_INTERNAL_KEYS = {
    "runtime",
    "callbacks",
    "config",
    "context",
    "store",
    "streamwriter",
    "toolcallid",
}


def _parse_metadata(metadata: Any) -> Dict[str, Any]:
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _read_string(record: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _coerce_string(value: Any) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_tool_arg_key(key: Any) -> str:
    return "".join(ch for ch in str(key or "").lower() if ch.isalnum())


def _sanitize_tool_payload_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, raw_value in value.items():
            if _normalize_tool_arg_key(key) in TOOL_INPUT_INTERNAL_KEYS:
                continue
            sanitized[str(key)] = _sanitize_tool_payload_value(raw_value)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_tool_payload_value(item) for item in value]
    return to_jsonable(value)


def _bounded_runtime_timeline_value(value: Any, *, depth: int = 0) -> Any:
    """Keep worker timeline cards useful without shipping full tool payloads."""

    if depth >= 4:
        return "[structured value omitted]" if isinstance(value, (dict, list)) else to_jsonable(value)
    if isinstance(value, str):
        normalized = value
        if len(normalized) <= 4000:
            return normalized
        return f"{normalized[:3600]}\n…[已截断 {len(normalized) - 3600} 字符]"
    if isinstance(value, dict):
        items = list(value.items())[:48]
        result = {str(key): _bounded_runtime_timeline_value(raw, depth=depth + 1) for key, raw in items}
        if len(value) > len(items):
            result["_truncatedKeys"] = len(value) - len(items)
        return result
    if isinstance(value, list):
        items = [_bounded_runtime_timeline_value(item, depth=depth + 1) for item in value[:24]]
        if len(value) > len(items):
            items.append(f"…[已截断 {len(value) - len(items)} 项]")
        return items
    return to_jsonable(value)


def _normalize_source_group(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"web", "cron", "hooks"}:
        return normalized
    return ""


def _normalize_source(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_chat_type(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return "group" if normalized == "group" else "p2p"


def _derive_channel_context(record: Dict[str, Any], *, source_hint: str | None = None) -> Dict[str, Any]:
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    metadata = _parse_metadata(record.get("metadata") or summary.get("metadata"))
    channel_type = _coerce_string(
        _read_string(record, ["channel_type", "channelType"])
        or _read_string(summary, ["channel_type", "channelType"])
        or _read_string(metadata, ["channel_type", "channelType"])
    )
    channel_name = _coerce_string(
        _read_string(record, ["channel_name", "channelName"])
        or _read_string(summary, ["channel_name", "channelName"])
        or _read_string(metadata, ["channel_name", "channelName"])
        or channel_type
    )
    channel_domain = _coerce_string(
        _read_string(record, ["channel_domain", "channelDomain"])
        or _read_string(summary, ["channel_domain", "channelDomain"])
        or _read_string(metadata, ["channel_domain", "channelDomain"])
    )
    chat_type = _normalize_chat_type(
        _read_string(record, ["chat_type", "chatType"])
        or _read_string(summary, ["chat_type", "chatType"])
        or _read_string(metadata, ["chat_type", "chatType"])
    )
    account_id = _coerce_string(
        _read_string(record, ["account_id", "accountId"])
        or _read_string(summary, ["account_id", "accountId"])
        or _read_string(metadata, ["account_id", "accountId"])
    )
    default_account = _coerce_string(
        _read_string(record, ["default_account", "defaultAccount"])
        or _read_string(summary, ["default_account", "defaultAccount"])
        or _read_string(metadata, ["default_account", "defaultAccount"])
    )

    return {
        **({"channelType": channel_type} if channel_type else {}),
        **({"channelName": channel_name} if channel_name else {}),
        **({"channelDomain": channel_domain} if channel_domain else {}),
        **({"chatType": chat_type} if chat_type else {}),
        **({"accountId": account_id} if account_id else {}),
        **({"defaultAccount": default_account} if default_account else {}),
    }


def _derive_source_group(record: Dict[str, Any], *, source_hint: str | None = None) -> str:
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    explicit = _normalize_source_group(
        record.get("sourceGroup")
        or record.get("source_group")
        or summary.get("sourceGroup")
        or summary.get("source_group")
    )
    if explicit in {"cron", "hooks"}:
        return explicit

    metadata = _parse_metadata(record.get("metadata") or summary.get("metadata"))
    metadata_explicit = _normalize_source_group(
        metadata.get("sourceGroup")
        or metadata.get("source_group")
        or metadata.get("historyGroup")
        or metadata.get("history_group")
    )
    if metadata_explicit in {"web", "cron", "hooks"}:
        return metadata_explicit
    source = _normalize_source(
        record.get("source")
        or summary.get("source")
        or record.get("trigger_source")
        or summary.get("trigger_source")
        or metadata.get("trigger_source")
        or metadata.get("triggerSource")
        or metadata.get("source")
        or source_hint
    )
    if not source:
        return "web"
    if source == "cron" or source.startswith("cron"):
        return "cron"
    if source == "hooks" or source.startswith("hook") or source.startswith("trigger"):
        return "hooks"

    if explicit == "web":
        return "web"
    return "web"


def project_artifacts_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("topic") != "artifact.recorded":
            continue
        payload = event.get("payload") or {}
        metadata = payload.get("metadata") or {}
        surface_visible = metadata.get("surfaceVisible")
        if surface_visible is None:
            surface_visible = metadata.get("surface_visible")
        if surface_visible is False:
            continue
        artifact_id = payload.get("artifactId")
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifacts.append(
            normalize_artifact_record(
                {
                    "artifactId": artifact_id,
                    "kind": payload.get("kind"),
                    "mimeType": payload.get("mimeType"),
                    "title": payload.get("title"),
                    "previewUrl": payload.get("previewUrl"),
                    "externalUrl": payload.get("externalUrl"),
                    "workspacePath": payload.get("workspacePath"),
                    "sourcePath": payload.get("sourcePath"),
                    "metadata": payload.get("metadata") or {},
                    "sessionId": payload.get("sessionId") or event.get("session_id"),
                    "runId": payload.get("runId") or event.get("run_id"),
                    "messageId": payload.get("messageId"),
                    "createdAt": payload.get("createdAt") or event.get("event_ts"),
                }
            )
        )
    return artifacts


TODO_MUTATION_PATTERNS = [
    re.compile(r"command\s*\(\s*update\s*=\s*\{[^)]*todos", re.IGNORECASE),
    re.compile(r"\bpersistent task plan\b", re.IGNORECASE),
    re.compile(r"\btodo\s*#?\d+\b.*\b(marked|updated|done|in_progress|pending|skipped|created)\b", re.IGNORECASE),
    re.compile(r"\bcreated with\s+\d+\s+items\b", re.IGNORECASE),
]


def _string_looks_like_todo_mutation(value: Any) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in TODO_MUTATION_PATTERNS)


def _contains_todo_mutation_hint(value: Any, depth: int = 0) -> bool:
    if depth > 4 or value is None:
        return False
    if isinstance(value, str):
        return _string_looks_like_todo_mutation(value)
    if isinstance(value, list):
        return any(_contains_todo_mutation_hint(item, depth + 1) for item in value)
    if not isinstance(value, dict):
        return False

    if isinstance(value.get("todos"), (list, dict)):
        return True

    update = value.get("update")
    if isinstance(update, dict) and ("todos" in update or "todo" in update):
        return True

    request = value.get("request")
    if isinstance(request, dict) and ("todos" in request or "todo" in request):
        return True

    return any(_contains_todo_mutation_hint(item, depth + 1) for item in value.values())


def _is_todo_tool_payload(value: Dict[str, Any]) -> bool:
    tool = value.get("tool") or value
    tool_name = str(
        tool.get("toolName")
        or tool.get("tool_name")
        or value.get("toolName")
        or value.get("tool_name")
        or ""
    ).strip().lower()
    return tool_name in {"write_todos", "update_todo"} or _contains_todo_mutation_hint(value)


def _merge_unique_list(base_list: object, incoming_list: object) -> list:
    merged: list = []
    seen: set[str] = set()
    for collection in (base_list, incoming_list):
        if not isinstance(collection, list):
            continue
        for item in collection:
            fingerprint = str(item)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(item)
    return merged


def _normalize_merge_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _coerce_merge_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _message_identity_keys(message: dict | None) -> list[str]:
    if not isinstance(message, dict):
        return []
    keys: list[str] = []
    message_id = str(message.get("id") or "").strip()
    if message_id:
        keys.append(f"id:{message_id}")
    role = str(message.get("role") or "").strip().lower()
    run_id = str(message.get("runId") or message.get("run_id") or "").strip()
    if role == "assistant" and run_id:
        keys.append(f"assistant-run:{run_id}")
    if role == "tool" and run_id:
        keys.append(f"tool-run:{run_id}")
    timestamp = _coerce_merge_int(message.get("timestamp"))
    time_bucket = timestamp // 60000 if timestamp > 0 else 0
    if role:
        content_signature = _normalize_merge_text(message.get("content"))
        if content_signature and time_bucket > 0:
            keys.append(f"{role}-content:{time_bucket}:{content_signature}")
        reasoning_signature = _normalize_merge_text(message.get("reasoningContent"))
        if reasoning_signature and time_bucket > 0:
            keys.append(f"{role}-reasoning:{time_bucket}:{reasoning_signature}")
        tool_invocations = message.get("toolInvocations")
        if isinstance(tool_invocations, list):
            for invocation in tool_invocations:
                if not isinstance(invocation, dict):
                    continue
                tool_call_id = str(invocation.get("toolCallId") or "").strip()
                tool_name = str(invocation.get("toolName") or "").strip().lower()
                if tool_call_id:
                    keys.append(f"{role}-tool-call:{tool_call_id}")
                elif tool_name and time_bucket > 0:
                    keys.append(f"{role}-tool-name:{time_bucket}:{tool_name}")
    return keys


def _message_richness(message: dict | None) -> int:
    if not isinstance(message, dict):
        return 0
    return (
        len(str(message.get("content") or "").strip())
        + (len(message.get("parts") or []) * 140 if isinstance(message.get("parts"), list) else 0)
        + (len(message.get("nodes") or []) * 120 if isinstance(message.get("nodes"), list) else 0)
        + (len(message.get("artifacts") or []) * 200 if isinstance(message.get("artifacts"), list) else 0)
        + (len(message.get("images") or []) * 80 if isinstance(message.get("images"), list) else 0)
    )


def _merge_message_payload(base: dict, incoming: dict) -> dict:
    merged = dict(base)
    for key, value in incoming.items():
        if value is None:
            continue
        if key in {"artifacts", "images"} and isinstance(value, list):
            merged[key] = _merge_unique_list(merged.get(key), value)
            continue
        if key in {"parts", "nodes", "toolInvocations"} and isinstance(value, list):
            base_list = merged.get(key) if isinstance(merged.get(key), list) else []
            if not base_list:
                merged[key] = value
            continue
        if key == "metadata" and isinstance(value, dict):
            merged[key] = {
                **(merged.get("metadata") if isinstance(merged.get("metadata"), dict) else {}),
                **value,
            }
            continue
        if key in {"content", "reasoningContent"}:
            current_content = str(merged.get(key) or "").strip()
            next_content = str(value or "").strip()
            if not next_content:
                continue
            if not current_content or len(next_content) > len(current_content):
                merged[key] = value
            continue
        merged[key] = value
    return merged


def merge_authoritative_timeline_messages(
    primary_messages: list[dict],
    incoming_messages: list[dict] | None,
) -> list[dict]:
    """Merge same-run messages while letting richer/durable content repair thin snapshots."""
    merged: list[dict] = []
    seen_by_identity: dict[str, int] = {}

    for item in list(primary_messages or []):
        if not isinstance(item, dict):
            continue
        merged.append(dict(item))
        for identity_key in _message_identity_keys(item):
            seen_by_identity[identity_key] = len(merged) - 1

    for item in list(incoming_messages or []):
        if not isinstance(item, dict):
            continue
        matching_index = next(
            (
                seen_by_identity[identity_key]
                for identity_key in _message_identity_keys(item)
                if identity_key in seen_by_identity
            ),
            None,
        )
        if matching_index is not None:
            index = matching_index
            current = merged[index]
            if _message_richness(item) > _message_richness(current):
                merged[index] = _merge_message_payload(current, item)
            else:
                merged[index] = _merge_message_payload(item, current)
            for identity_key in _message_identity_keys(merged[index]):
                seen_by_identity[identity_key] = index
            continue
        merged.append(dict(item))
        for identity_key in _message_identity_keys(item):
            seen_by_identity[identity_key] = len(merged) - 1
    return merged


def format_durable_chat_messages(rows: list[dict]) -> list[dict]:
    formatted: list[dict] = []
    for row in rows:
        created_at = row.get("created_at")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        agent_profile = _durable_agent_profile(row, metadata)
        try:
            timestamp = int(datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp() * 1000) if created_at else 0
        except Exception:
            timestamp = 0
        nodes = []
        message = {
            "id": row["id"],
            "role": row["role"],
            "runId": str(metadata.get("run_id") or metadata.get("runId") or "").strip() or None,
            "content": row.get("content") or "",
            "reasoningContent": row.get("reasoning_content"),
            "createdAt": row.get("created_at"),
            "timestamp": timestamp,
            **agent_profile,
            "agentId": row.get("agent_id"),
            "images": row.get("images") or [],
            "metadata": metadata,
            "nodes": nodes,
        }
        if row.get("reasoning_content"):
            nodes.append(
                {
                    "id": f"{row['id']}:reasoning",
                    "kind": "execution",
                    "executionType": "reasoning",
                    "content": row.get("reasoning_content"),
                    "timestamp": timestamp,
                    **agent_profile,
                }
            )
        if row.get("content"):
            nodes.append(
                {
                    "id": f"{row['id']}:content",
                    "kind": "narrative",
                    "role": row.get("role"),
                    "content": row.get("content"),
                    "timestamp": timestamp,
                    **agent_profile,
                }
            )
        tool_calls = row.get("tool_calls")
        if isinstance(tool_calls, str):
            try:
                tool_calls = json.loads(tool_calls)
            except Exception:
                tool_calls = None
        if isinstance(tool_calls, list):
            invocations = []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = tool_call.get("id", "")
                tool_name = tool_call.get("name", tool_call.get("function", {}).get("name", ""))
                tool_args = _sanitize_tool_payload_value(
                    tool_call.get("args", tool_call.get("function", {}).get("arguments", {}))
                )
                tool_result = tool_call.get("result", None)
                invocations.append(
                    {
                        "toolCallId": tool_call_id,
                        "toolName": tool_name,
                        "args": tool_args,
                        "result": tool_result,
                    }
                )
                nodes.append(
                    {
                        "id": f"{row['id']}:tool:{tool_call_id or len(invocations)}:call",
                        "kind": "execution",
                        "executionType": "tool_call",
                        "toolCallId": tool_call_id,
                        "toolName": tool_name,
                        "args": tool_args,
                        "timestamp": timestamp,
                        **agent_profile,
                    }
                )
                if tool_result is not None:
                    nodes.append(
                        {
                            "id": f"{row['id']}:tool:{tool_call_id or len(invocations)}:result",
                            "kind": "execution",
                            "executionType": "tool_result",
                            "toolCallId": tool_call_id,
                            "toolName": tool_name,
                            "result": tool_result,
                            "timestamp": timestamp,
                            **agent_profile,
                        }
                    )
            if invocations:
                message["toolInvocations"] = invocations
        formatted.append(message)
    return formatted


def project_chat_messages_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    current_assistant: Optional[Dict[str, Any]] = None
    current_assistant_run_id: Optional[str] = None
    active_agent_profile: Dict[str, Any] = _default_supervisor_profile()
    last_text_delta_by_run: dict[str, str] = {}
    last_reasoning_delta_by_run: dict[str, str] = {}
    last_text_delta_global: str = ""
    last_text_delta_global_run_id: str = ""
    last_reasoning_delta_global: str = ""
    last_reasoning_delta_global_run_id: str = ""

    def _longest_overlap_suffix_prefix(current: str, incoming: str) -> int:
        limit = min(len(current), len(incoming))
        for size in range(limit, 0, -1):
            if current[-size:] == incoming[:size]:
                return size
        return 0

    def _suppress_neighbor_duplicate_delta(
        delta: str,
        *,
        run_id_key: str,
        last_by_run: dict[str, str],
        last_global: str,
        last_global_run_id: str,
    ) -> tuple[str, str, str]:
        normalized_delta = str(delta or "")
        if not normalized_delta:
            return "", last_global, last_global_run_id
        if run_id_key and last_by_run.get(run_id_key) == normalized_delta:
            return "", last_global, last_global_run_id
        if last_global and normalized_delta == last_global:
            return "", last_global, last_global_run_id
        if last_global and run_id_key and run_id_key != last_global_run_id:
            if normalized_delta.startswith(last_global):
                normalized_delta = normalized_delta[len(last_global):]
            elif last_global.startswith(normalized_delta) or normalized_delta in last_global:
                return "", last_global, last_global_run_id
            else:
                overlap = _longest_overlap_suffix_prefix(last_global, normalized_delta)
                if overlap > 0:
                    normalized_delta = normalized_delta[overlap:]
        if not normalized_delta:
            return "", last_global, last_global_run_id
        if run_id_key:
            last_by_run[run_id_key] = str(delta or "")
        return normalized_delta, str(delta or ""), run_id_key or last_global_run_id

    def ensure_assistant(event: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal current_assistant, current_assistant_run_id
        run_id = event.get("run_id")
        if current_assistant is None or current_assistant_run_id != run_id:
            current_assistant = {
                "id": event.get("event_id") or f"assistant_{run_id or len(messages)}",
                "role": "assistant",
                "runId": run_id,
                "content": "",
                "parts": [],
                "agentName": active_agent_profile.get("agentName", "智能主管"),
                "agentAvatar": active_agent_profile.get("agentAvatar", _default_supervisor_profile().get("agentAvatar", "")),
                "agentRoleLabel": active_agent_profile.get("agentRoleLabel", "主理人"),
                "agentType": "supervisor",
                "timestamp": _event_timestamp_ms(event),
                "images": [],
                "artifacts": [],
            }
            current_assistant_run_id = run_id
            messages.append(current_assistant)
        return current_assistant

    for event in events:
        topic = event.get("topic")
        payload = event.get("payload") or {}

        if topic == "message.user.recorded":
            current_assistant = None
            current_assistant_run_id = None
            metadata = dict(payload.get("metadata") or {})
            attachments = [dict(item) for item in list(payload.get("attachments") or []) if isinstance(item, dict)]
            if attachments and not metadata.get("attachments"):
                metadata["attachments"] = attachments
            audio_refs: set[str] = set()
            for attachment in attachments:
                declared_kind = str(attachment.get("mediaKind") or attachment.get("media_kind") or "").strip().lower()
                declared_mime = str(attachment.get("mimeType") or attachment.get("mime_type") or attachment.get("type") or "").strip().lower()
                explicitly_visual = declared_kind in {"image", "video"} or declared_mime.startswith(("image/", "video/"))
                probe = str(attachment.get("name") or "").strip().lower()
                is_audio = not explicitly_visual and (
                    declared_kind == "audio"
                    or declared_mime.startswith("audio/")
                    or bool(re.search(r"\.(mp3|m4a|wav|ogg|opus|aac|flac|webm)(?:[?#\s].*)?$", probe))
                )
                if not is_audio:
                    continue
                for key in ("previewUrl", "preview_url", "publicUrl", "public_url", "url", "workspacePath", "workspace_path"):
                    value = str(attachment.get(key) or "").strip().lower()
                    if value:
                        audio_refs.add(value)
            message_images = [
                value
                for value in list(payload.get("images") or [])
                if str(value or "").strip().lower() not in audio_refs
            ]
            messages.append(
                {
                    "id": payload.get("message_id") or event.get("event_id"),
                    "role": "user",
                    "runId": event.get("run_id"),
                    "content": payload.get("content", ""),
                    "parts": [{"type": "text", "content": payload.get("content", "")}],
                    "timestamp": _event_timestamp_ms(event),
                    "images": message_images,
                    "artifacts": [],
                    "metadata": metadata,
                }
            )
            continue

        if topic == "message.tool.recorded":
            if _is_todo_tool_payload(payload):
                continue
            current_assistant = None
            current_assistant_run_id = None
            messages.append(
                {
                    "id": payload.get("message_id") or event.get("event_id"),
                    "role": "tool",
                    "runId": event.get("run_id"),
                    "content": payload.get("content", ""),
                    "parts": [{"type": "text", "content": payload.get("content", "")}],
                    "timestamp": _event_timestamp_ms(event),
                    "images": [],
                    "artifacts": [],
                    "metadata": payload.get("metadata") or {},
                }
            )
            continue

        if topic == "agent.started":
            agent = payload.get("agent") or payload
            agent_id = agent.get("id") or agent.get("agent_id") or event.get("agent_id")
            is_supervisor = _is_supervisor_agent_id(agent_id)
            default_profile = _default_supervisor_profile()
            active_agent_profile = {
                "agentName": default_profile["agentName"] if is_supervisor else agent.get("name") or agent.get("agent_name") or str(agent_id or "Agent"),
                "agentAvatar": default_profile["agentAvatar"] if is_supervisor else agent.get("avatar") or agent.get("agent_avatar") or "",
                "agentRoleLabel": default_profile["agentRoleLabel"] if is_supervisor else agent.get("roleLabel") or agent.get("agent_role_label") or "Agent",
            }
            assistant = ensure_assistant(event)
            if is_supervisor:
                assistant["agentName"] = active_agent_profile["agentName"]
                assistant["agentAvatar"] = active_agent_profile["agentAvatar"]
                assistant["agentRoleLabel"] = active_agent_profile["agentRoleLabel"]
                assistant["agentType"] = "supervisor"
            else:
                assistant["agentType"] = "agent"
            continue

        if topic == "run.text.delta":
            assistant = ensure_assistant(event)
            delta = payload.get("content") or payload.get("delta") or ""
            if not delta:
                continue
            run_id_key = str(event.get("run_id") or current_assistant_run_id or "").strip()
            delta, last_text_delta_global, last_text_delta_global_run_id = _suppress_neighbor_duplicate_delta(
                str(delta),
                run_id_key=run_id_key,
                last_by_run=last_text_delta_by_run,
                last_global=last_text_delta_global,
                last_global_run_id=last_text_delta_global_run_id,
            )
            if not delta:
                continue
            last_part = assistant["parts"][-1] if assistant["parts"] else None
            if (
                last_part
                and last_part.get("type") == "text"
                and last_part.get("agentName") == active_agent_profile.get("agentName")
            ):
                last_part["content"] += delta
            else:
                assistant["parts"].append(
                    {
                        "type": "text",
                        "content": delta,
                        **active_agent_profile,
                    }
                )
            if (
                active_agent_profile.get("agentName") == "智能主管"
                or active_agent_profile.get("agentRoleLabel") == "主理人"
            ):
                assistant["content"] += delta
            continue

        if topic == "run.reasoning.delta":
            assistant = ensure_assistant(event)
            delta = payload.get("content") or payload.get("delta") or ""
            if not delta:
                continue
            run_id_key = str(event.get("run_id") or current_assistant_run_id or "").strip()
            delta, last_reasoning_delta_global, last_reasoning_delta_global_run_id = _suppress_neighbor_duplicate_delta(
                str(delta),
                run_id_key=run_id_key,
                last_by_run=last_reasoning_delta_by_run,
                last_global=last_reasoning_delta_global,
                last_global_run_id=last_reasoning_delta_global_run_id,
            )
            if not delta:
                continue
            last_part = assistant["parts"][-1] if assistant["parts"] else None
            if (
                last_part
                and last_part.get("type") == "reasoning"
                and last_part.get("agentName") == active_agent_profile.get("agentName")
            ):
                last_part["content"] += delta
            else:
                assistant["parts"].append(
                    {
                        "type": "reasoning",
                        "content": delta,
                        "time": 0,
                        **active_agent_profile,
                    }
                )
            continue

        if topic == "tool.started":
            if _is_todo_tool_payload(payload):
                continue
            assistant = ensure_assistant(event)
            tool = payload.get("tool") or payload
            assistant["parts"].append(
                {
                    "type": "tool_call",
                    "toolCallId": tool.get("toolCallId") or tool.get("tool_call_id"),
                    "toolName": tool.get("toolName") or tool.get("tool_name"),
                    "args": _sanitize_tool_payload_value(tool.get("args") or {}),
                    **active_agent_profile,
                }
            )
            continue

        if topic == "tool.finished":
            if _is_todo_tool_payload(payload):
                continue
            assistant = ensure_assistant(event)
            tool = payload.get("tool") or payload
            agent_visible_result = (
                tool.get("agentVisibleResult")
                or tool.get("agent_visible_result")
                or tool.get("agentVisibleOutput")
                or tool.get("agent_visible_output")
            )
            assistant["parts"].append(
                {
                    "type": "tool_result",
                    "toolCallId": tool.get("toolCallId") or tool.get("tool_call_id"),
                    "toolName": tool.get("toolName") or tool.get("tool_name"),
                    "result": agent_visible_result if agent_visible_result is not None else (tool.get("result") or tool.get("result_preview")),
                    "agentVisibleResult": agent_visible_result,
                    "agentVisibleChars": tool.get("agentVisibleChars") or tool.get("agent_visible_chars"),
                    **({"mcpApp": tool.get("mcpApp") or tool.get("mcp_app")} if (tool.get("mcpApp") or tool.get("mcp_app")) else {}),
                    **active_agent_profile,
                }
            )
            continue

        if topic == "approval.requested":
            continue

        if topic == "approval.rejected":
            continue

        if topic in {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}:
            current_assistant = None
            current_assistant_run_id = None

    projected_artifacts = project_artifacts_from_events(events)
    artifacts_by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    artifacts_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for artifact in projected_artifacts:
        message_id = artifact.get("messageId") or artifact.get("message_id")
        run_id = artifact.get("runId") or artifact.get("run_id")
        if message_id:
            artifacts_by_message[str(message_id)].append(artifact)
        elif run_id:
            artifacts_by_run[str(run_id)].append(artifact)

    assistant_by_run: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.get("role") == "assistant" and message.get("runId"):
            assistant_by_run[str(message["runId"])] = message

    for message in messages:
        artifact_bucket = message.setdefault("artifacts", [])
        message_id = message.get("id")
        if message_id and message_id in artifacts_by_message:
            artifact_bucket.extend(artifacts_by_message.pop(message_id))

    for run_id, run_artifacts in artifacts_by_run.items():
        target_message = assistant_by_run.get(run_id)
        if target_message is None:
            continue
        target_message.setdefault("artifacts", []).extend(run_artifacts)

    return messages


def build_chat_projection_snapshot(session_id: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest_seq = 0
    if events:
        latest_seq = int(events[-1].get("seq") or 0)
    return {
        "session_id": session_id,
        "latest_seq": latest_seq,
        "messages": project_chat_messages_from_events(events),
        "artifacts": project_artifacts_from_events(events),
    }


def _truncate_runtime_summary(text: str, limit: int = 120) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _runtime_timeline_entry(
    event: Dict[str, Any],
    *,
    runtime_id: str,
    kind: str,
    summary: str,
    status: Optional[str] = None,
    actor_label: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source = event.get("source") or {}
    payload = event.get("payload") or {}
    return {
        "id": event.get("event_id") or f"evt_{event.get('seq') or summary}",
        "seq": int(event.get("seq") or 0),
        "runId": event.get("run_id"),
        "runtimeId": runtime_id,
        "topic": str(event.get("topic") or ""),
        "kind": kind,
        "summary": summary,
        "actorLabel": actor_label or source.get("agent_id") or "扩展运行",
        "timestamp": event.get("event_ts") or event.get("ts") or event.get("created_at"),
        "status": status,
        "metadata": metadata if metadata is not None else payload,
    }


CANVAS_GRAPH_RUN_STATE_TOPIC = "canvas.graph.run.state"
CANVAS_GRAPH_RUN_STATE_SCHEMA = "v8.creative_canvas_graph_run_state.v1"
CANVAS_OUTPUT_REVIEW_STATE_TOPIC = "canvas.graph.output.review.state"
CANVAS_OUTPUT_REVIEW_STATE_SCHEMA = "v8.creative_canvas_output_review_state.v1"
CANVAS_OUTPUT_DELIVERY_STATE_TOPIC = "canvas.graph.output.delivery.state"
CANVAS_OUTPUT_DELIVERY_STATE_SCHEMA = "v8.creative_canvas_output_delivery_state.v1"
CANVAS_GRAPH_RUN_STATE_STATUSES = {
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "failed",
    "interrupted",
    "recovered",
    "completed",
}
CANVAS_GRAPH_RUN_STATE_SUMMARIES = {
    "queued": "创作画布任务已排队",
    "running": "创作画布任务正在运行",
    "cancelling": "正在取消创作画布任务",
    "cancelled": "创作画布任务已取消",
    "failed": "创作画布任务执行失败",
    "interrupted": "创作画布任务因 Engine 重启而中断",
    "recovered": "创作画布任务已恢复",
    "completed": "创作画布任务执行完成",
}


def _canvas_graph_run_state_metadata(
    event: Dict[str, Any],
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if payload.get("schema") != CANVAS_GRAPH_RUN_STATE_SCHEMA:
        return None
    if any(
        alias in payload
        for alias in (
            "session_id",
            "workspace_id",
            "graph_id",
            "graph_run_id",
            "canvas_operation_id",
            "run_id",
            "retry_of_graph_run_id",
        )
    ):
        return None

    def canonical_text(key: str) -> str:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) else ""

    session_id = canonical_text("sessionId")
    workspace_id = canonical_text("workspaceId")
    graph_id = canonical_text("graphId")
    graph_run_id = canonical_text("graphRunId")
    canvas_operation_id = canonical_text("canvasOperationId")
    if not all((session_id, workspace_id, graph_id, graph_run_id, canvas_operation_id)):
        return None
    event_session_id = str(event.get("session_id") or "").strip()
    if event_session_id != session_id:
        return None

    raw_run_id = payload.get("runId")
    if raw_run_id is not None and not isinstance(raw_run_id, str):
        return None
    run_id = str(raw_run_id or "").strip() or None
    event_run_id = str(event.get("run_id") or "").strip() or None
    if event_run_id != run_id:
        return None

    status = canonical_text("status").lower()
    if status not in CANVAS_GRAPH_RUN_STATE_STATUSES:
        return None
    transition = canonical_text("transition").lower()
    if transition not in {"", "recovered", "retry_failed_branch", "remote_terminal_reconciled"}:
        return None
    retry_of_graph_run_id = canonical_text("retryOfGraphRunId")
    if transition == "retry_failed_branch" and not retry_of_graph_run_id:
        return None

    metadata: Dict[str, Any] = {
        "schema": CANVAS_GRAPH_RUN_STATE_SCHEMA,
        "sessionId": session_id,
        "workspaceId": workspace_id,
        "graphId": graph_id,
        "graphRunId": graph_run_id,
        "canvasOperationId": canvas_operation_id,
        "runId": run_id,
        "status": status,
    }
    if transition:
        metadata["transition"] = transition
    if retry_of_graph_run_id:
        metadata["retryOfGraphRunId"] = retry_of_graph_run_id
    recovery = payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
    if (
        status in {"failed", "interrupted"}
        and recovery.get("canRetry") is True
        and str(recovery.get("mode") or "").strip() == "failed_branch"
    ):
        metadata["recovery"] = {"canRetry": True, "mode": "failed_branch"}
    return metadata


def _canvas_output_state_metadata(
    event: Dict[str, Any],
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    topic = str(event.get("topic") or "")
    schema = str(payload.get("schema") or "")
    is_review = topic == CANVAS_OUTPUT_REVIEW_STATE_TOPIC and schema == CANVAS_OUTPUT_REVIEW_STATE_SCHEMA
    is_delivery = topic == CANVAS_OUTPUT_DELIVERY_STATE_TOPIC and schema == CANVAS_OUTPUT_DELIVERY_STATE_SCHEMA
    if not (is_review or is_delivery):
        return None
    allowed = {
        "schema", "runtimeId", "surfaceTargets", "sessionId", "graphId", "resultNodeId",
        "outputVersionId", "selectionRevision", "review", "affectedReviews", "delivery",
    }
    if set(payload) - allowed:
        return None
    if payload.get("runtimeId") != "creative_media" or payload.get("surfaceTargets") != [
        "runtime_card", "runtime_timeline", "process"
    ]:
        return None
    session_id = str(payload.get("sessionId") or "").strip()
    graph_id = str(payload.get("graphId") or "").strip()
    result_node_id = str(payload.get("resultNodeId") or "").strip()
    output_version_id = str(payload.get("outputVersionId") or "").strip()
    revision = payload.get("selectionRevision")
    if (
        not all((session_id, graph_id, result_node_id, output_version_id))
        or str(event.get("session_id") or "").strip() != session_id
        or type(revision) is not int
        or revision < 0
    ):
        return None

    def delivery_projection(value: Any) -> Optional[Dict[str, Any]]:
        record = value if isinstance(value, dict) else {}
        if set(record) - {"status", "attempt", "errorDetailCode", "manifestArtifactId", "deliveredAt"}:
            return None
        status = str(record.get("status") or "").strip().lower()
        attempt = record.get("attempt")
        if status not in {"idle", "pending", "failed", "delivered"} or type(attempt) is not int or attempt < 0:
            return None
        projected = {"status": status, "attempt": attempt}
        error_code = str(record.get("errorDetailCode") or "").strip()
        artifact_id = str(record.get("manifestArtifactId") or "").strip()
        delivered_at = str(record.get("deliveredAt") or "").strip()
        if error_code and not re.fullmatch(r"[a-z0-9_]{1,120}", error_code):
            return None
        if error_code:
            projected["errorDetailCode"] = error_code[:120]
        if status == "delivered" and artifact_id:
            projected["manifestArtifactId"] = artifact_id[:160]
        if status == "delivered" and delivered_at:
            projected["deliveredAt"] = delivered_at[:64]
        return projected

    def review_projection(value: Any) -> Optional[Dict[str, Any]]:
        review = value if isinstance(value, dict) else {}
        if set(review) - {"decision", "revision", "selectedForDelivery", "reviewedAt", "delivery"}:
            return None
        decision = str(review.get("decision") or "").strip().lower()
        review_revision = review.get("revision")
        if decision not in {"pending", "approved", "rejected"} or type(review_revision) is not int:
            return None
        delivery = delivery_projection(review.get("delivery"))
        if delivery is None:
            return None
        return {
            "decision": decision,
            "revision": review_revision,
            "selectedForDelivery": bool(review.get("selectedForDelivery")),
            "delivery": delivery,
        }

    metadata: Dict[str, Any] = {
        "schema": schema,
        "sessionId": session_id,
        "graphId": graph_id,
        "resultNodeId": result_node_id,
        "outputVersionId": output_version_id,
        "selectionRevision": revision,
    }
    if is_review:
        review = review_projection(payload.get("review"))
        affected = payload.get("affectedReviews")
        if review is None or not isinstance(affected, list) or len(affected) > 32:
            return None
        for item in affected:
            if (
                not isinstance(item, dict)
                or set(item) - {"outputVersionId", "review"}
                or not str(item.get("outputVersionId") or "").strip()
                or review_projection(item.get("review")) is None
            ):
                return None
        metadata["review"] = review
        metadata["affectedReviewCount"] = len(affected)
    else:
        delivery = delivery_projection(payload.get("delivery"))
        if delivery is None:
            return None
        metadata["delivery"] = delivery
    return metadata


RUNTIME_EPISODE_ACTOR_LABELS = {
    "research": "Research Runtime",
    "engineering": "Engineering Runtime",
    "creative_media": "Creative Media Runtime",
    "computer_use": "Computer Use Runtime",
    "rpa": "RPA Runtime",
    "subagent_swarm": "Agent Swarm",
    "chat": "Supervisor",
}


def _runtime_record(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _runtime_nested_record(payload: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for key in keys:
        nested = _runtime_record(payload.get(key))
        if nested:
            return nested
    return {}


def _runtime_kind_from_hint(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if not normalized:
        return ""
    if "research" in normalized or "evidence" in normalized or "source_matrix" in normalized:
        return "research"
    if "engineering" in normalized or "project_coding" in normalized or "patch" in normalized or "verification" in normalized:
        return "engineering"
    if "creative" in normalized or "asset" in normalized or "media" in normalized or "recipe" in normalized:
        return "creative_media"
    if "computer_use" in normalized or "computer" in normalized or "observation" in normalized or "desktop" in normalized:
        return "computer_use"
    if "rpa" in normalized or "trace" in normalized:
        return "rpa"
    if "delegation" in normalized or "subagent" in normalized or "child" in normalized:
        return "subagent_swarm"
    if "capability" in normalized:
        return "chat"
    return normalized


def _runtime_kind_from_payload(payload: Dict[str, Any], *, topic: str = "") -> str:
    episode = _runtime_nested_record(payload, "episode")
    need = _runtime_nested_record(payload, "need")
    handoff = _runtime_nested_record(payload, "handoffRef", "handoff")
    child = _runtime_nested_record(payload, "childDelegation", "child_delegation")
    tool = _runtime_nested_record(payload, "tool")
    for record in (payload, episode, need, handoff, child, tool):
        hint = _read_string(
            record,
            [
                "runtimeId",
                "runtime_id",
                "ownerRuntimeId",
                "owner_runtime_id",
                "runtimeKind",
                "runtime_kind",
                "runtime",
                "kind",
                "family",
            ],
        )
        runtime_id = _runtime_kind_from_hint(hint)
        if runtime_id:
            return runtime_id
    if topic.startswith("research.") or topic.startswith("research_broker."):
        return "research"
    if topic.startswith("delegation.") or topic.startswith("delegation_broker.") or topic.startswith("subagent.task."):
        return "subagent_swarm"
    return "chat"


def _runtime_actor_label(runtime_id: str) -> str:
    return RUNTIME_EPISODE_ACTOR_LABELS.get(runtime_id, runtime_id or "Runtime")


def _runtime_status_from_topic(topic: str, record: Dict[str, Any]) -> str:
    explicit = _read_string(record, ["dispatchStatus", "dispatch_status", "status", "state", "phase"])
    normalized = str(explicit or topic).lower()
    if any(token in normalized for token in ("fail", "error", "reject", "blocked", "cancel", "stalled")):
        return "failed"
    if any(token in normalized for token in ("complete", "finish", "done", "success", "succeeded", "merged", "ready")):
        return "completed"
    if any(token in normalized for token in ("attempt", "revealed", "missing", "no_task", "no_tasks", "unconfirmed")):
        return "attempted"
    if any(token in normalized for token in ("waiting", "queued", "leased", "active", "running", "started", "dispatch", "routed", "detected")):
        return "active"
    return explicit or "progress"


def _truthy_context_field(payload: Dict[str, Any], keys: List[str]) -> bool:
    return any(bool(payload.get(key)) for key in keys)


def _positive_context_field(payload: Dict[str, Any], keys: List[str]) -> bool:
    for key in keys:
        try:
            if int(payload.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _context_governance_is_effective(payload: Dict[str, Any]) -> bool:
    durable_flush = _runtime_record(payload.get("durable_flush"))
    durable_reason = str(durable_flush.get("reason") or durable_flush.get("status") or "").strip().lower()
    recall_audit = _runtime_record(payload.get("recall_audit"))
    skip_reasons = {"", "compaction_not_needed", "none", "prepared", "context_prepared", "skipped", "unchanged"}
    if bool(payload.get("compaction_applied")):
        return True
    if _positive_context_field(payload, ["estimated_saved_tokens"]):
        return True
    if _truthy_context_field(
        payload,
        [
            "approval_required",
            "pendingApproval",
            "requiresApproval",
            "approvalRequested",
            "approval_request",
            "truncation_risk",
            "truncated",
            "context_truncated",
            "budget_exceeded",
            "overflow",
        ],
    ):
        return True
    if _positive_context_field(payload, ["truncated_tokens", "overflow_tokens", "tokens_over_budget"]):
        return True
    if durable_reason not in skip_reasons and re.search(r"compact|flush|truncate|approval|recall|inject|budget|overflow", durable_reason):
        return True
    return bool(recall_audit.get("injection_allowed"))


def _context_governance_summary(payload: Dict[str, Any]) -> str:
    parts: list[str] = []
    saved_tokens = int(payload.get("estimated_saved_tokens") or 0)
    block_count = int(payload.get("block_count") or 0)
    recall_audit = _runtime_record(payload.get("recall_audit"))
    if bool(payload.get("compaction_applied")) or saved_tokens > 0:
        parts.append(f"压缩节省约 {saved_tokens} tokens" if saved_tokens > 0 else "已执行上下文压缩")
    if recall_audit.get("injection_allowed"):
        parts.append(f"召回注入 {block_count} 个 context block" if block_count > 0 else "已注入召回上下文")
    if _truthy_context_field(payload, ["approval_required", "pendingApproval", "requiresApproval", "approvalRequested", "approval_request"]):
        parts.append("需要审批")
    if _truthy_context_field(payload, ["truncation_risk", "truncated", "context_truncated", "budget_exceeded", "overflow"]):
        parts.append("存在截断/预算风险")
    resolved_scope = str(payload.get("resolved_scope") or "").strip()
    if resolved_scope:
        parts.append(f"scope={resolved_scope}")
    return "上下文治理：" + "，".join(parts) if parts else "上下文治理已更新"


def _compact_runtime_handoff_ref(value: Any) -> Dict[str, Any]:
    record = _runtime_record(value)
    compact: Dict[str, Any] = {}
    for source_key, target_key in [
        ("handoffRefId", "handoffRefId"),
        ("handoff_ref_id", "handoffRefId"),
        ("handoffId", "handoffId"),
        ("handoff_id", "handoffId"),
        ("artifactId", "artifactId"),
        ("artifact_id", "artifactId"),
        ("producerEpisodeId", "producerEpisodeId"),
        ("producer_episode_id", "producerEpisodeId"),
        ("kind", "kind"),
        ("status", "status"),
        ("compactSummary", "compactSummary"),
        ("compact_summary", "compactSummary"),
        ("handoffStage", "handoffStage"),
        ("handoff_stage", "handoffStage"),
        ("requiresContinuation", "requiresContinuation"),
        ("requires_continuation", "requiresContinuation"),
        ("recommendedNextAction", "recommendedNextAction"),
        ("recommended_next_action", "recommendedNextAction"),
        ("createdAt", "createdAt"),
        ("created_at", "createdAt"),
    ]:
        item = record.get(source_key)
        if item not in (None, "", [], {}):
            compact[target_key] = item
    for source_key, target_key in [
        ("artifactRefs", "artifactRefs"),
        ("artifact_refs", "artifactRefs"),
        ("evidenceRefs", "evidenceRefs"),
        ("evidence_refs", "evidenceRefs"),
        ("recipeRefs", "recipeRefs"),
        ("recipe_refs", "recipeRefs"),
    ]:
        items = record.get(source_key)
        if isinstance(items, list):
            compact[target_key] = [str(item) for item in items[:24] if str(item).strip()]
    return compact


def _runtime_episode_metadata(payload: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for source_key, target_key in [
        ("runtimeId", "runtimeId"),
        ("runtime_id", "runtimeId"),
        ("episodeId", "episodeId"),
        ("episode_id", "episodeId"),
        ("needId", "episodeId"),
        ("need_id", "episodeId"),
        ("kind", "episodeKind"),
        ("runtimeKind", "episodeKind"),
        ("runtime_kind", "episodeKind"),
        ("parentEpisodeId", "parentEpisodeId"),
        ("parent_episode_id", "parentEpisodeId"),
        ("rootEpisodeId", "rootEpisodeId"),
        ("root_episode_id", "rootEpisodeId"),
        ("producerEpisodeId", "producerEpisodeId"),
        ("producer_episode_id", "producerEpisodeId"),
        ("evidenceBundleId", "evidenceBundleId"),
        ("evidence_bundle_id", "evidenceBundleId"),
        ("dispatchStatus", "dispatchStatus"),
        ("dispatch_status", "dispatchStatus"),
        ("ownerRuntimeId", "ownerRuntimeId"),
        ("owner_runtime_id", "ownerRuntimeId"),
        ("ownerAgentKind", "ownerAgentKind"),
        ("owner_agent_kind", "ownerAgentKind"),
        ("ownerAgentId", "ownerAgentId"),
        ("owner_agent_id", "ownerAgentId"),
    ]:
        value = record.get(source_key, payload.get(source_key))
        if value not in (None, ""):
            metadata[target_key] = value
    handoff_refs = record.get("handoffRefs") or record.get("handoff_refs") or payload.get("handoffRefs") or payload.get("handoff_refs")
    if handoff_refs:
        values = handoff_refs if isinstance(handoff_refs, list) else [handoff_refs]
        compact_handoffs = [item for item in (_compact_runtime_handoff_ref(value) for value in values) if item]
        if compact_handoffs:
            metadata["handoffRefs"] = compact_handoffs
            metadata["handoff"] = compact_handoffs[0]
            if compact_handoffs[0].get("requiresContinuation") is not None:
                metadata["requiresContinuation"] = bool(compact_handoffs[0]["requiresContinuation"])
            recommended_next_action = compact_handoffs[0].get("recommendedNextAction")
            if isinstance(recommended_next_action, str) and recommended_next_action.strip():
                metadata["recommendedNextAction"] = recommended_next_action.strip()
    producer_episode_id = metadata.get("producerEpisodeId")
    if producer_episode_id and not metadata.get("episodeId"):
        metadata["episodeId"] = producer_episode_id
    if record.get("missingTasks") or record.get("missing_tasks") or record.get("missingResult") or record.get("missing_result"):
        metadata["missingResult"] = True
    return metadata


def _runtime_episode_progress_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Project only the already-sanitized worker timeline node.

    Runtime episode progress also carries scheduler and recovery details. Those remain
    on Runtime Surface; the session timeline only needs stable lineage plus the
    bounded node produced by the worker progress adapter.
    """
    progress = _runtime_nested_record(payload, "progress")
    timeline_node = _runtime_nested_record(progress, "timelineNode", "timeline_node")
    embedded_topic = _read_string(timeline_node, ["topic"])
    if not timeline_node or not embedded_topic.startswith("subagent."):
        return {}

    allowed_node_keys = {
        "id",
        "kind",
        "executionType",
        "execution_type",
        "topic",
        "content",
        "role",
        "toolName",
        "tool_name",
        "toolCallId",
        "tool_call_id",
        "args",
        "result",
        "agentVisibleResult",
        "agent_visible_result",
        "artifact",
    }
    compact_node = {
        str(key): _bounded_runtime_timeline_value(_sanitize_tool_payload_value(value))
        for key, value in timeline_node.items()
        if key in allowed_node_keys and value not in (None, "", [], {})
    }
    if not compact_node:
        return {}

    compact_progress: Dict[str, Any] = {"timelineNode": compact_node}
    for source_key, target_key in [
        ("stage", "stage"),
        ("status", "status"),
        ("agentId", "agentId"),
        ("agentName", "agentName"),
        ("delegationId", "delegationId"),
        ("taskBriefId", "taskBriefId"),
        ("parentDelegationId", "parentDelegationId"),
        ("delegationDepth", "delegationDepth"),
    ]:
        value = progress.get(source_key)
        if value not in (None, ""):
            compact_progress[target_key] = value

    metadata = _runtime_episode_metadata(payload, payload)
    metadata["progress"] = compact_progress
    timeline_node_id = _read_string(compact_node, ["id"])
    episode_id = _read_string(metadata, ["episodeId"])
    if timeline_node_id:
        metadata["dedupeKey"] = f"subagent-timeline:{episode_id or 'episode'}:{timeline_node_id}"
    return metadata


def _subagent_delta_metadata(payload: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """Keep a bounded subagent narrative/reasoning delta with stable lineage."""
    runtime_context = _runtime_nested_record(payload, "runtimeContext", "runtime_context")
    content = _read_string(payload, ["snapshot", "content", "summary", "message"])
    if not content:
        return {}
    content = content[:6000]
    metadata: Dict[str, Any] = {
        "runtimeId": "subagent_swarm",
        "ownerRuntimeId": "subagent_swarm",
        "ownerAgentKind": "subagent",
        "content": content,
    }
    for source, keys in [
        (payload, ["ownerAgentId", "owner_agent_id"]),
        (payload, ["delegationId", "delegation_id"]),
        (payload, ["invocationId", "invocation_id"]),
        (payload, ["taskBriefId", "task_brief_id"]),
        (runtime_context, ["subagent_id", "subagentId"]),
        (runtime_context, ["delegation_id", "delegationId"]),
    ]:
        value = _read_string(source, keys)
        if not value:
            continue
        if "subagent" in keys[0].lower() or "owneragent" in keys[0].lower():
            metadata.setdefault("ownerAgentId", value)
        elif "delegation" in keys[0].lower():
            metadata.setdefault("delegationId", value)
        elif "invocation" in keys[0].lower():
            metadata.setdefault("invocationId", value)
        elif "taskbrief" in keys[0].lower():
            metadata.setdefault("taskBriefId", value)
    segment_key = _read_string(payload, ["segmentKey", "segment_key", "streamRunKey", "stream_run_key"])
    if segment_key:
        metadata["dedupeKey"] = f"subagent-delta:{topic}:{segment_key}"
    if topic == "subagent.reasoning.delta":
        metadata["reasoningKind"] = _read_string(payload, ["reasoningKind", "reasoning_kind"]) or "summary"
    return metadata


def _runtime_episode_id(record: Dict[str, Any], event: Dict[str, Any]) -> str:
    return _read_string(
        record,
        [
            "episodeId",
            "episode_id",
            "needId",
            "need_id",
            "delegationId",
            "delegation_id",
            "taskBriefId",
            "task_brief_id",
            "invocationId",
            "invocation_id",
            "handoffRefId",
            "handoff_ref_id",
            "handoffId",
            "handoff_id",
            "artifactId",
            "artifact_id",
        ],
    ) or str(event.get("event_id") or event.get("seq") or "").strip()


def _runtime_tool_name(payload: Dict[str, Any]) -> str:
    tool = _runtime_nested_record(payload, "tool")
    return _read_string(payload, ["toolName", "tool_name", "name"]) or _read_string(tool, ["toolName", "tool_name", "name"])


def _runtime_tool_call_id(payload: Dict[str, Any]) -> str:
    tool = _runtime_nested_record(payload, "tool")
    return _read_string(
        payload,
        ["toolInvocationId", "tool_invocation_id", "toolCallId", "tool_call_id", "id"],
    ) or _read_string(tool, ["toolInvocationId", "tool_invocation_id", "toolCallId", "tool_call_id", "id"])


def _runtime_id_for_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower()
    if normalized == "research_broker":
        return "research"
    if normalized in {"delegation_broker", "parallel_delegate_task"}:
        return "subagent_swarm"
    return "chat"


def _creative_media_tool_entry(
    event: Dict[str, Any],
    topic: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    tool = _runtime_nested_record(payload, "tool")
    result = _runtime_record(tool.get("result"))
    lifecycle = topic.rsplit(".", 1)[-1]
    result_status = _read_string(result, ["status", "state"]).lower()
    failed = (
        lifecycle == "failed"
        or result.get("ok") is False
        or bool(result.get("error"))
        or result_status in {"failed", "error", "blocked", "cancelled"}
    )
    status = "failed" if failed else ("active" if lifecycle == "started" else "completed")
    tool_name = _runtime_tool_name(payload) or "creative_media"
    summary = f"{tool_name} · {status}"
    metadata: Dict[str, Any] = {
        "runtimeId": "creative_media",
        "ownerRuntimeId": _read_string(payload, ["ownerRuntimeId", "owner_runtime_id"]) or "creative_media",
        "ownerAgentKind": _read_string(payload, ["ownerAgentKind", "owner_agent_kind"]),
        "ownerAgentId": _read_string(payload, ["ownerAgentId", "owner_agent_id"]),
        "toolName": tool_name,
        "toolInvocationId": _runtime_tool_call_id(payload),
        "status": _read_string(result, ["status", "state"]) or status,
        "ok": False if failed else result.get("ok"),
    }
    source = _runtime_record(event.get("source"))
    if not metadata["ownerAgentId"] and source.get("agent_id"):
        metadata["ownerAgentId"] = source.get("agent_id")
    return _runtime_timeline_entry(
        event,
        runtime_id="creative_media",
        kind="tool",
        summary=summary,
        status=status,
        actor_label=_runtime_actor_label("creative_media"),
        metadata={key: value for key, value in metadata.items() if value not in (None, "")},
    )


def _runtime_orchestration_entry(event: Dict[str, Any], topic: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record: Dict[str, Any] = payload
    kind = "progress"
    summary = ""
    runtime_id = ""

    if topic == "runtime.episode.progress":
        metadata = _runtime_episode_progress_metadata(payload)
        if not metadata:
            # Scheduler chatter remains Runtime Surface only. A server-sanitized
            # timeline node is the explicit opt-in for the subagent detail surface.
            return None
        progress = _runtime_nested_record(payload, "progress")
        return _runtime_timeline_entry(
            event,
            runtime_id="subagent_swarm",
            kind="progress",
            summary=_truncate_runtime_summary(_read_string(progress, ["summary"]) or "协作过程已更新", 140),
            status=_runtime_status_from_topic(topic, progress),
            actor_label=_read_string(progress, ["agentName", "agent_name"]) or _runtime_actor_label("subagent_swarm"),
            metadata=metadata,
        )
    if topic in {"subagent.text.delta", "subagent.reasoning.delta"}:
        metadata = _subagent_delta_metadata(payload, topic)
        if not metadata:
            return None
        owner_agent_id = _read_string(metadata, ["ownerAgentId"])
        summary = "协作回复已更新" if topic == "subagent.text.delta" else "协作思考已更新"
        return _runtime_timeline_entry(
            event,
            runtime_id="subagent_swarm",
            kind="progress",
            summary=summary,
            status="active",
            actor_label=owner_agent_id or _runtime_actor_label("subagent_swarm"),
            metadata=metadata,
        )
    if topic.startswith("capability.need."):
        record = _runtime_nested_record(payload, "need") or payload
        runtime_id = _runtime_kind_from_payload({**payload, **record}, topic=topic)
        need_reason = _read_string(record, ["reason", "summary", "message"])
        summary = need_reason or f"检测到 {runtime_id} 能力需求"
        kind = "progress"
    elif topic.startswith("runtime.episode."):
        record = _runtime_nested_record(payload, "episode") or payload
        runtime_id = _runtime_kind_from_payload({**payload, **record}, topic=topic)
        state = _read_string(record, ["state", "status", "phase"]) or topic.rsplit(".", 1)[-1]
        reason = _read_string(record, ["reason", "summary", "message"])
        summary = reason or f"{_runtime_actor_label(runtime_id)} episode {state}"
        kind = "progress"
    elif topic.startswith("handoff.ref."):
        record = _runtime_nested_record(payload, "handoffRef", "handoff") or payload
        runtime_id = _runtime_kind_from_payload({**payload, **record}, topic=topic)
        summary = _read_string(record, ["compactSummary", "compact_summary", "summary", "message"]) or "已创建 handoff 结果"
        kind = "handoff"
    elif topic.startswith("delegation.child."):
        record = _runtime_nested_record(payload, "childDelegation", "child_delegation") or payload
        runtime_id = "subagent_swarm"
        reason = _read_string(record, ["reason", "summary", "message"])
        summary = reason or "子代理请求孙 agent / child delegation"
        kind = "progress"
    elif topic.startswith("creative_media.tool."):
        return _creative_media_tool_entry(event, topic, payload)
    elif topic.startswith("research.") or topic.startswith("research_broker."):
        runtime_id = "research"
        summary = _read_string(payload, ["summary", "message", "question"]) or "Research Runtime 已更新"
        kind = "tool" if "broker" in topic or topic.endswith(".result") else "progress"
    elif topic.startswith("delegation_broker.") or topic.startswith("delegation.") or topic.startswith("subagent.task."):
        runtime_id = "subagent_swarm"
        summary = _read_string(payload, ["summary", "message", "reason"]) or "Agent Swarm 调度已更新"
        kind = "tool" if "broker" in topic or topic.startswith("subagent.task.") else "progress"
    else:
        tool_name = _runtime_tool_name(payload)
        if tool_name == "research_broker":
            runtime_id = "research"
            summary = _read_string(payload, ["summary", "message"]) or "Research broker 已调用"
            kind = "tool"
        elif tool_name == "delegation_broker":
            runtime_id = "subagent_swarm"
            if topic == "tool.started":
                summary = _read_string(payload, ["summary", "message"]) or "Delegation broker 已启动"
                record = {**payload, "dispatchStatus": "dispatch_attempted"}
            else:
                tasks = payload.get("tasks")
                items = payload.get("items")
                has_confirmed_tasks = (
                    (isinstance(tasks, list) and len(tasks) > 0)
                    or (isinstance(items, list) and len(items) > 0 and not bool(payload.get("missingTasks") or payload.get("missing_tasks")))
                    or bool(payload.get("taskConfirmed") or payload.get("task_confirmed"))
                )
                dispatch_status = _read_string(payload, ["dispatchStatus", "dispatch_status", "status", "state"])
                missing_tasks = bool(
                    payload.get("missingTasks")
                    or payload.get("missing_tasks")
                    or payload.get("missingResult")
                    or payload.get("missing_result")
                    or payload.get("error") == "missing_tasks"
                    or dispatch_status == "missing_tasks"
                    or (not has_confirmed_tasks and not dispatch_status)
                )
                summary = _read_string(payload, ["summary", "message"]) or ("尝试派发子代理，但未确认实际任务" if missing_tasks else "Delegation broker 已调用")
                runtime_context_run_id = str(event.get("run_id") or "").strip() or "unknown"
                record = {
                    **payload,
                    "dispatchStatus": dispatch_status or ("missing_tasks" if missing_tasks else "dispatch_attempted"),
                    "missingResult": missing_tasks,
                    "dispatchGroup": payload.get("dispatchGroup") or (f"delegation_missing_tasks:{runtime_context_run_id}" if missing_tasks else payload.get("delegationId")),
                    "diagnosticKey": payload.get("diagnosticKey") or ("delegation_missing_tasks" if missing_tasks else None),
                }
            kind = "tool"
        else:
            return None

    runtime_id = runtime_id or _runtime_kind_from_payload(payload, topic=topic)
    status = _runtime_status_from_topic(topic, record)
    metadata = _runtime_episode_metadata(payload, record)
    if _runtime_episode_id(record, event):
        metadata.setdefault("episodeId", _runtime_episode_id(record, event))
    return _runtime_timeline_entry(
        event,
        runtime_id=runtime_id,
        kind=kind,
        summary=_truncate_runtime_summary(summary, 140),
        status=status,
        actor_label=_runtime_actor_label(runtime_id),
        metadata=metadata,
    )


RUNTIME_TIMELINE_MILESTONE_TOPICS = {
    CANVAS_GRAPH_RUN_STATE_TOPIC,
    CANVAS_OUTPUT_REVIEW_STATE_TOPIC,
    CANVAS_OUTPUT_DELIVERY_STATE_TOPIC,
    "handoff.ref.created",
    "runtime.episode.active",
    "runtime.episode.cancelled",
    "runtime.episode.completed",
    "runtime.episode.degraded",
    "runtime.episode.failed",
    "runtime.episode.queued",
    "runtime.episode.resumed",
    "runtime.episode.retry_scheduled",
    "runtime.episode.started",
    "runtime.episode.waiting",
    "runtime.episode.waiting_input",
}


def select_runtime_timeline_window(
    timeline: List[Dict[str, Any]],
    *,
    recent_limit: int = 160,
    milestone_limit: int = 32,
) -> List[Dict[str, Any]]:
    recent_count = max(1, int(recent_limit or 160))
    milestone_count = max(0, int(milestone_limit or 0))
    recent = list(timeline[-recent_count:])

    # The compact global window is intentionally small, but a subagent detail
    # panel must not collapse to the last "completed" status. Preserve the
    # bounded worker timeline nodes for the most recent delegation episodes;
    # phone/desktop compact surfaces still apply their own 160-entry limit.
    subagent_groups: Dict[str, List[Dict[str, Any]]] = {}
    for entry in timeline:
        if str(entry.get("topic") or "").strip().lower() != "runtime.episode.progress":
            continue
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        progress = metadata.get("progress") if isinstance(metadata.get("progress"), dict) else {}
        if not isinstance(progress.get("timelineNode"), dict):
            continue
        episode_id = str(metadata.get("episodeId") or progress.get("delegationId") or "").strip()
        if not episode_id:
            continue
        subagent_groups.setdefault(episode_id, []).append(entry)
    selected_subagent: List[Dict[str, Any]] = []
    latest_groups = sorted(
        subagent_groups.items(),
        key=lambda item: max(int(row.get("seq") or 0) for row in item[1]),
        reverse=True,
    )[:4]
    for _episode_id, rows in latest_groups:
        selected_subagent.extend(rows[-320:])

    selected_by_identity = {
        (str(entry.get("id") or ""), int(entry.get("seq") or 0)): entry
        for entry in [*recent, *selected_subagent]
    }
    recent = sorted(
        selected_by_identity.values(),
        key=lambda entry: int(entry.get("seq") or 0),
    )
    if len(timeline) <= recent_count and not selected_subagent:
        return recent
    if milestone_count <= 0:
        return recent
    recent_ids = {
        (str(entry.get("id") or ""), int(entry.get("seq") or 0))
        for entry in recent
    }
    historical: List[Dict[str, Any]] = []
    for entry in reversed(timeline[:-recent_count]):
        if str(entry.get("topic") or "").strip().lower() not in RUNTIME_TIMELINE_MILESTONE_TOPICS:
            continue
        key = (str(entry.get("id") or ""), int(entry.get("seq") or 0))
        if key in recent_ids:
            continue
        historical.append(entry)
        if len(historical) >= milestone_count:
            break
    historical.reverse()
    return [*historical, *recent]


def project_runtime_timeline_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projected: List[Dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    tool_starts: Dict[str, Dict[str, Any]] = {}
    tool_results: set[str] = set()
    terminal_event_by_run: Dict[str, Dict[str, Any]] = {}

    for event in events:
        topic = str(event.get("topic") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if topic == "tool.started":
            tool_id = _runtime_tool_call_id(payload)
            if tool_id:
                tool_starts[tool_id] = event
        elif topic == "tool.finished":
            tool_id = _runtime_tool_call_id(payload)
            if tool_id:
                tool_results.add(tool_id)
        elif topic in {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}:
            run_id = str(event.get("run_id") or "").strip()
            if run_id:
                terminal_event_by_run[run_id] = event

    for event in events:
        topic = str(event.get("topic") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entry: Optional[Dict[str, Any]] = None

        orchestration_entry = _runtime_orchestration_entry(event, topic, payload if isinstance(payload, dict) else {})
        if orchestration_entry:
            entry = orchestration_entry
        elif topic == CANVAS_GRAPH_RUN_STATE_TOPIC:
            canvas_metadata = _canvas_graph_run_state_metadata(event, payload)
            if canvas_metadata:
                canvas_status = str(canvas_metadata["status"])
                entry = _runtime_timeline_entry(
                    event,
                    runtime_id="creative_media",
                    kind="progress",
                    summary=CANVAS_GRAPH_RUN_STATE_SUMMARIES[canvas_status],
                    status=canvas_status,
                    actor_label=_runtime_actor_label("creative_media"),
                    metadata=canvas_metadata,
                )
        elif topic in {CANVAS_OUTPUT_REVIEW_STATE_TOPIC, CANVAS_OUTPUT_DELIVERY_STATE_TOPIC}:
            canvas_metadata = _canvas_output_state_metadata(event, payload)
            if canvas_metadata:
                if topic == CANVAS_OUTPUT_REVIEW_STATE_TOPIC:
                    review = canvas_metadata["review"]
                    status = str(review["decision"])
                    summary = {
                        "approved": "创作画布版本已批准",
                        "rejected": "创作画布版本已拒绝",
                        "pending": "创作画布版本评审已更新",
                    }[status]
                    kind = "governance"
                else:
                    status = str(canvas_metadata["delivery"]["status"])
                    summary = {
                        "pending": "创作画布成品正在交付",
                        "failed": "创作画布成品交付失败",
                        "delivered": "创作画布成品已交付",
                        "idle": "创作画布成品等待交付",
                    }[status]
                    kind = "progress"
                entry = _runtime_timeline_entry(
                    event,
                    runtime_id="creative_media",
                    kind=kind,
                    summary=summary,
                    status=status,
                    actor_label=_runtime_actor_label("creative_media"),
                    metadata=canvas_metadata,
                )
        elif topic == "extension.route.selected":
            skill_count = len(list(payload.get("skillCandidates") or []))
            mcp_count = len(list(payload.get("mcpToolCandidates") or []))
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="progress",
                summary=f"已筛出 {skill_count} 个 Skills，{mcp_count} 个 MCP 工具",
                status="selected",
                metadata={"skillCount": skill_count, "mcpToolCount": mcp_count},
            )
        elif topic == "extension.skill.loaded":
            skill_name = str(payload.get("skillName") or "未知 Skill")
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="tool",
                summary=f"已读取 Skill：{skill_name}",
                status="loaded",
                metadata={"skillName": skill_name},
            )
        elif topic in {"extension.skill.blocked", "safety.skill_blocked"}:
            skill_name = str(payload.get("skillName") or "未知 Skill")
            verdict = str(payload.get("verdict") or "high").strip() or "high"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="governance",
                summary=f"Safety Guardian 已阻断 Skill：{skill_name}（{verdict}）",
                status="blocked",
                metadata={"skillName": skill_name, "verdict": verdict},
            )
        elif topic == "extension.mcp.candidate_exposed":
            count = int(payload.get("count") or len(list(payload.get("toolNames") or [])) or 0)
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="progress",
                summary=f"已暴露 {count} 个 MCP 工具",
                status="ready",
                metadata={"count": count},
            )
        elif topic == "extension.mcp.invoked":
            tool_names = [str(item).strip() for item in list(payload.get("toolNames") or []) if str(item).strip()]
            summary = f"已调用 MCP 工具：{', '.join(tool_names[:3])}" if tool_names else "已调用 MCP 工具"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="tool",
                summary=_truncate_runtime_summary(summary, 96),
                status="invoked",
                metadata={"toolNames": tool_names[:12]},
            )
        elif topic == "extension.execution.completed":
            tool_names = [str(item).strip() for item in list(payload.get("toolNames") or []) if str(item).strip()]
            if tool_names:
                summary = f"扩展执行完成，调用了 {', '.join(tool_names[:3])}"
            else:
                summary = "扩展候选执行完成"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="progress",
                summary=summary,
                status="completed",
                metadata={"toolNames": tool_names[:12], "hasToolCalls": bool(tool_names)},
            )
        elif topic == "chat.command_preset.applied":
            preset_name = str(payload.get("name") or "未命名命令").strip() or "未命名命令"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary=f"已应用命令预设：{preset_name}",
                status="configured",
                actor_label="聊天运行",
            )
        elif topic == "engineering_lane.trigger.decided":
            trigger = payload.get("triggerDecision") if isinstance(payload.get("triggerDecision"), dict) else {}
            active = bool(trigger.get("active"))
            matched = bool(trigger.get("matched"))
            signals = [str(item).strip() for item in list(trigger.get("signals") or []) if str(item).strip()]
            if active:
                summary = "工程模式已激活"
                if signals:
                    summary += f" · {', '.join(signals[:3])}"
            elif matched:
                summary = "工程模式已评估，但未进入工程主链"
            else:
                summary = "工程模式未命中"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="engineering",
                kind="progress",
                summary=_truncate_runtime_summary(summary, 120),
                status="active" if active else "idle",
                actor_label="工程治理",
            )
        elif topic == "engineering.plan.projected":
            summary = str(payload.get("summary") or "").strip() or "工程契约已投影"
            blocked_count = int(payload.get("blockedCount") or 0)
            warning_count = int(payload.get("warningCount") or 0)
            if blocked_count > 0:
                status = "blocked"
            elif warning_count > 0:
                status = "warning"
            else:
                status = "projected"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="engineering",
                kind="progress",
                summary=_truncate_runtime_summary(summary, 120),
                status=status,
                actor_label="工程治理",
            )
        elif topic == "engineering.proof.collected":
            summary = str(payload.get("summary") or "").strip() or "工程证明已收集"
            status = str(payload.get("verificationStatus") or payload.get("status") or "planned").strip() or "planned"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="engineering",
                kind="progress",
                summary=_truncate_runtime_summary(summary, 120),
                status=status,
                actor_label="工程治理",
            )
        elif topic == "run.lane.queued":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="当前会话正在排队等待执行",
                status="queued",
                actor_label="运行调度",
            )
        elif topic == "run.lane.acquired":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="已获得当前会话执行权",
                status="running",
                actor_label="运行调度",
            )
        elif topic == "run.lane.rejected":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="当前会话忙碌，本次请求未进入执行",
                status="rejected",
                actor_label="运行调度",
            )
        elif topic == "run.lane.released":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="已释放当前会话执行权",
                status="idle",
                actor_label="运行调度",
            )
        elif topic == "run.state.changed":
            next_state = str(
                payload.get("to_status")
                or payload.get("toStatus")
                or payload.get("status")
                or payload.get("state")
                or payload.get("nextState")
                or "unknown"
            ).strip() or "unknown"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary=f"运行状态已切换为：{next_state}",
                status=next_state,
                actor_label="运行治理",
            )
        elif topic == "run.paused":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary="当前运行已暂停",
                status="paused",
                actor_label="运行治理",
            )
        elif topic == "run.resumed":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary="当前运行已恢复",
                status="running",
                actor_label="运行治理",
            )
        elif topic == "run.interrupted":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary="当前运行已被中断",
                status="interrupted",
                actor_label="运行治理",
            )
        elif topic == "run.cancelled":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary="当前运行已取消",
                status="cancelled",
                actor_label="运行治理",
            )
        elif topic == "run.retry.requested":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary="已请求重新执行当前运行",
                status="retry_requested",
                actor_label="运行治理",
            )
        elif topic in {"approval.approved", "approval.rejected", "approval.auto_approved"}:
            approval_status = {
                "approval.approved": "approved",
                "approval.rejected": "rejected",
                "approval.auto_approved": "auto_approved",
            }.get(topic, "resolved")
            approval_summary = {
                "approval.approved": "审批已通过",
                "approval.rejected": "审批已拒绝",
                "approval.auto_approved": "审批已自动通过",
            }.get(topic, "审批状态已更新")
            entry = _runtime_timeline_entry(
                event,
                runtime_id="automation",
                kind="governance",
                summary=approval_summary,
                status=approval_status,
                actor_label="审批治理",
            )
        elif topic == "safety.preflight.blocked":
            reason = str(payload.get("reason") or payload.get("verdict") or "未通过安全预检").strip() or "未通过安全预检"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="governance",
                summary=f"安全预检阻断：{reason}",
                status="blocked",
                actor_label="Safety Guardian",
            )
        elif topic == "context.prepared":
            raw_governance_payload = payload if isinstance(payload, dict) else {}
            governance_payload = {
                **raw_governance_payload,
                **normalize_context_audit(raw_governance_payload),
            }
            if _context_governance_is_effective(governance_payload):
                runtime_id = str(governance_payload.get("runtime_kind") or "context_governance").strip() or "context_governance"
                entry = _runtime_timeline_entry(
                    event,
                    runtime_id=runtime_id,
                    kind="governance",
                    summary=_context_governance_summary(governance_payload),
                    status="prepared",
                    actor_label="上下文治理",
                    metadata={
                        **governance_payload,
                        "eventTs": str(event.get("event_ts") or event.get("ts") or "").strip(),
                        "runId": str(event.get("run_id") or "").strip(),
                        "eventSource": dict(event.get("source") or {}),
                    },
                )
        elif topic == "run.liveness.blocked":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="运行被阻塞，等待继续执行",
                status="blocked",
                actor_label="运行状态",
            )
        elif topic == "run.liveness.recovered":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="运行已恢复并继续执行",
                status="running",
                actor_label="运行状态",
            )
        elif topic == "run.liveness.stalled":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="检测到运行长时间无进展",
                status="stalled",
                actor_label="运行状态",
            )
        elif topic == "run.watchdog.stream_idle_timeout":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="流式执行长时间无输出，已触发看门狗",
                status="stalled",
                actor_label="运行状态",
            )
        elif topic == "run.continuation.scheduled":
            continuation_count = int(payload.get("continuationCount") or 0)
            continuation_reason = str(payload.get("continuationReason") or "unknown").strip() or "unknown"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary=f"已静默续跑第 {continuation_count} 段执行（{continuation_reason}）",
                status="continued",
                actor_label="续跑管理",
            )
        elif topic == "supervisor.graph.diagnostics":
            graph_ms = payload.get("graphBuildMs")
            route_ms = payload.get("routeBuildMs")
            system_ms = payload.get("systemContentBuildMs")
            rag_ms = payload.get("passiveRagMs")
            parts: list[str] = []
            if graph_ms is not None:
                parts.append(f"graph {graph_ms}ms")
            if route_ms is not None:
                parts.append(f"route {route_ms}ms")
            if system_ms is not None:
                parts.append(f"prompt {system_ms}ms")
            if rag_ms is not None:
                parts.append(f"rag {rag_ms}ms")
            summary = "Supervisor 诊断：" + ("，".join(parts) if parts else "已记录内部构建指标")
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary=summary,
                status="diagnostics",
                actor_label="Supervisor 诊断",
            )

        if not entry:
            continue

        dedupe_key = (int(entry.get("seq") or 0), str(entry.get("topic") or ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        projected.append(entry)

    for tool_id, start_event in tool_starts.items():
        if tool_id in tool_results:
            continue
        start_payload = start_event.get("payload") if isinstance(start_event.get("payload"), dict) else {}
        tool_name = _runtime_tool_name(start_payload) or "unknown_tool"
        run_id = str(start_event.get("run_id") or "").strip()
        terminal_event = terminal_event_by_run.get(run_id) or start_event
        runtime_id = _runtime_id_for_tool_name(tool_name)
        summary = f"工具 {tool_name} 未收到结果"
        if tool_name == "delegation_broker":
            summary = "尝试派发子代理，但未收到工具结果，未确认实际派发"
        elif tool_name == "research_broker":
            summary = "Research broker 未收到工具结果，证据包未确认"
        diagnostic = _runtime_timeline_entry(
            terminal_event,
            runtime_id=runtime_id,
            kind="governance",
            summary=summary,
            status="missing_result",
            actor_label=_runtime_actor_label(runtime_id),
            metadata={
                "toolInvocationId": tool_id,
                "toolName": tool_name,
                "missingResult": True,
                "dispatchStatus": "missing_result" if tool_name == "delegation_broker" else None,
                "startedSeq": int(start_event.get("seq") or 0),
                "terminalSeq": int(terminal_event.get("seq") or 0),
            },
        )
        diagnostic["id"] = f"{diagnostic.get('id')}:missing_tool:{tool_id}"
        diagnostic["topic"] = "tool.missing_result"
        projected.append(diagnostic)

    projected.sort(key=lambda item: int(item.get("seq") or 0))
    return projected


def project_pending_approvals(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projected: List[Dict[str, Any]] = []
    for row in rows:
        request = row.get("request") or {}
        approval_kind = str(row.get("approval_kind") or request.get("approvalKind") or request.get("approval_kind") or "").strip().lower()
        interaction_kind = str(request.get("interactionKind") or request.get("interaction_kind") or row.get("interaction_kind") or "").strip().lower()
        if approval_kind == "ask_user" or interaction_kind == "ask_user":
            continue
        projected.append(
            {
                "id": row.get("id"),
                "approvalId": row.get("id"),
                "runId": row.get("run_id"),
                "sessionId": row.get("session_id"),
                "approvalKind": row.get("approval_kind"),
                "status": row.get("status"),
                "question": request.get("question") or request.get("prompt"),
                "prompt": request.get("prompt") or request.get("question"),
                "toolCallId": request.get("toolCallId"),
                "request": request,
                "createdAt": row.get("created_at"),
                "updatedAt": row.get("updated_at"),
                "expiresAt": row.get("expires_at"),
            }
        )
    return projected


def project_ask_user_interactions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projected: List[Dict[str, Any]] = []
    for row in rows:
        request = row.get("request") or {}
        projected.append(
            {
                "id": row.get("id"),
                "interactionId": row.get("id"),
                "runId": row.get("run_id"),
                "sessionId": row.get("session_id"),
                "assistantMessageId": row.get("assistant_message_id"),
                "toolCallId": row.get("tool_call_id") or request.get("toolCallId"),
                "question": row.get("question") or request.get("question") or request.get("prompt"),
                "prompt": row.get("prompt") or request.get("prompt") or request.get("question"),
                "status": row.get("status"),
                "interactionKind": "ask_user",
                "request": request,
                "answer": row.get("answer_text"),
                "answerText": row.get("answer_text"),
                "createdAt": row.get("created_at"),
                "resolvedAt": row.get("resolved_at"),
            }
        )
    return projected


def build_projection_controls(
    workflow: Optional[Dict[str, Any]],
    approvals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    workflow = workflow or {}
    approvals = approvals or []
    workflow_status = str(workflow.get("status") or "").strip()
    step_status = str(workflow.get("currentStepStatus") or "").strip()
    recoverable = bool(workflow.get("recoverable"))
    has_pending_approval = any(item.get("status") == "pending" for item in approvals)
    run_id = workflow.get("rootRunId")

    can_resume = recoverable and workflow_status in {"paused", "waiting_input", "waiting_external_tool"}
    can_retry = recoverable and workflow_status in {"recoverable_failed", "failed", "cancelled"}
    can_interrupt = workflow_status in {"running", "waiting_approval", "waiting_external_tool", "paused"}
    can_open_approval = has_pending_approval

    return {
        "runId": run_id,
        "canResume": can_resume,
        "canRetry": can_retry,
        "canInterrupt": can_interrupt,
        "canApprove": has_pending_approval,
        "canReject": has_pending_approval,
        "canOpenApproval": can_open_approval,
        "pendingApprovalCount": len(approvals),
        "recoverable": recoverable,
        "workflowStatus": workflow_status or None,
        "stepStatus": step_status or None,
    }


def build_recoverable_view(
    workflow: Optional[Dict[str, Any]],
    controls: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    workflow = workflow or {}
    controls = controls or {}
    return {
        "recoverable": bool(workflow.get("recoverable")),
        "strategy": workflow.get("resumeStrategy") or "replay_from_step",
        "workflowStatus": workflow.get("status"),
        "currentStepStatus": workflow.get("currentStepStatus"),
        "canResume": bool(controls.get("canResume")),
        "canRetry": bool(controls.get("canRetry")),
    }


def derive_preview_excerpt(
    snapshot: Optional[Dict[str, Any]] = None,
    *,
    fallback_preview: Optional[str] = None,
) -> Optional[str]:
    snapshot = snapshot or {}
    overlay = snapshot.get("overlay") or {}
    preview = overlay.get("assistantPreview") if isinstance(overlay, dict) else None
    if isinstance(preview, dict):
        preview_text = str(preview.get("content") or "").strip()
        preview_reasoning = str(preview.get("reasoningContent") or "").strip()
        if preview_text or preview_reasoning:
            return (preview_text or preview_reasoning)[:120]

    messages = list(snapshot.get("messages") or [])
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content[:120]
        for part in reversed(list(message.get("parts") or [])):
            part_content = str(part.get("content") or "").strip()
            if part_content:
                return part_content[:120]

    if fallback_preview:
        return fallback_preview[:120]
    return None


def _status_label(status: Optional[str]) -> Optional[str]:
    normalized = str(status or "").strip()
    if not normalized:
        return None
    return STATUS_LABELS.get(normalized, normalized)


def _runtime_summary(
    *,
    owner_runtime: Optional[str],
    workflow_status: Optional[str],
    current_step_title: Optional[str],
    step_status: Optional[str],
) -> Optional[str]:
    current_step_title = str(current_step_title or "").strip()
    if current_step_title:
        return current_step_title

    runtime_label = str(owner_runtime or "").strip()
    status_label = _status_label(step_status) or _status_label(workflow_status)
    if runtime_label and status_label:
        return f"{runtime_label} · {status_label}"
    if status_label:
        return status_label
    if runtime_label:
        return runtime_label
    return None


def build_projection_summary(
    *,
    session: Optional[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    workflow: Optional[Dict[str, Any]],
    approvals: Optional[List[Dict[str, Any]]],
    latest_seq: int,
    source: str,
) -> Dict[str, Any]:
    session = session or {}
    workflow = workflow or {}
    approvals = approvals or []
    channel_context = _derive_channel_context(session, source_hint=source)
    preview_excerpt = derive_preview_excerpt(snapshot, fallback_preview=session.get("previewExcerpt"))
    workflow_status = workflow.get("status") or session.get("workflowStatus")
    step_status = workflow.get("currentStepStatus") or session.get("stepStatus")
    owner_runtime = workflow.get("ownerRuntime") or session.get("ownerRuntime")
    current_step_title = workflow.get("currentStepTitle") or session.get("currentStepTitle")
    last_activity_at = (
        workflow.get("updatedAt")
        or session.get("workflowUpdatedAt")
        or session.get("lastActivityAt")
        or session.get("updated_at")
        or session.get("updatedAt")
    )
    return {
        "id": session.get("id"),
        "title": session.get("title"),
        "updatedAt": session.get("updated_at") or session.get("updatedAt"),
        "latestSeq": latest_seq,
        "source": source,
        "sourceGroup": _derive_source_group(session, source_hint=source),
        **channel_context,
        "workflowStatus": workflow_status,
        "statusLabel": _status_label(workflow_status),
        "stepStatus": step_status,
        "recoverable": bool(workflow.get("recoverable") if workflow else session.get("recoverable")),
        "ownerRuntime": owner_runtime,
        "ownerAgentId": workflow.get("ownerAgentId") or session.get("ownerAgentId"),
        "currentStepId": workflow.get("currentStepId") or session.get("currentStepId"),
        "currentStepKey": workflow.get("currentStepKey") or session.get("currentStepKey"),
        "currentStepTitle": current_step_title,
        "pendingApprovalCount": len(approvals),
        "hasPendingApproval": any(item.get("status") == "pending" for item in approvals),
        "previewExcerpt": preview_excerpt,
        "lastNarrativeExcerpt": preview_excerpt,
        "lastRuntimeSummary": _runtime_summary(
            owner_runtime=owner_runtime,
            workflow_status=workflow_status,
            current_step_title=current_step_title,
            step_status=step_status,
        ),
        "hasDurablePreview": bool(preview_excerpt),
        "lastActivityAt": last_activity_at,
        "metadata": session.get("metadata") or {},
    }


def apply_projection_overlay(snapshot: Dict[str, Any], overlay: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not overlay:
        return snapshot

    preview = overlay.get("assistantPreview")
    if not isinstance(preview, dict) or (not preview.get("content") and not preview.get("reasoningContent")):
        return snapshot

    messages = list(snapshot.get("messages") or [])
    run_id = preview.get("runId")
    target = next(
        (
            message
            for message in reversed(messages)
            if message.get("role") == "assistant" and message.get("runId") == run_id
        ),
        None,
    )

    if target is None:
        target = {
            "id": preview.get("id") or f"preview_{run_id}",
            "role": "assistant",
            "runId": run_id,
            "content": preview.get("content") or "",
            "parts": [],
            "agentName": preview.get("agentName") or "智能主管",
            "agentAvatar": preview.get("agentAvatar") or _default_supervisor_profile().get("agentAvatar", ""),
            "agentRoleLabel": preview.get("agentRoleLabel") or "主理人",
            "agentType": "supervisor",
            "timestamp": 0,
            "images": [],
            "artifacts": [],
        }
        messages.append(target)

    existing_content = target.get("content") or ""
    preview_content = preview.get("content") or ""
    if len(preview_content) > len(existing_content):
        target["content"] = preview_content

    reasoning_content = preview.get("reasoningContent") or ""
    parts = list(target.get("parts") or [])
    text_part = next((part for part in reversed(parts) if part.get("type") == "text"), None)
    if preview_content:
        if text_part is None:
            parts.append(
                {
                    "type": "text",
                    "content": preview_content,
                    "agentName": preview.get("agentName") or target.get("agentName"),
                    "agentAvatar": preview.get("agentAvatar") or target.get("agentAvatar"),
                    "agentRoleLabel": preview.get("agentRoleLabel") or target.get("agentRoleLabel"),
                }
            )
        elif len(preview_content) > len(text_part.get("content") or ""):
            text_part["content"] = preview_content

    if reasoning_content:
        reasoning_part = next((part for part in reversed(parts) if part.get("type") == "reasoning"), None)
        if reasoning_part is None:
            parts.append(
                {
                    "type": "reasoning",
                    "content": reasoning_content,
                    "time": 0,
                    "agentName": preview.get("agentName") or target.get("agentName"),
                    "agentAvatar": preview.get("agentAvatar") or target.get("agentAvatar"),
                    "agentRoleLabel": preview.get("agentRoleLabel") or target.get("agentRoleLabel"),
                }
            )
        elif len(reasoning_content) > len(reasoning_part.get("content") or ""):
            reasoning_part["content"] = reasoning_content

    target["parts"] = parts
    merged = dict(snapshot)
    merged["messages"] = messages
    merged["overlay"] = overlay
    return merged


def _event_timestamp_ms(event: Dict[str, Any]) -> int:
    event_ts = event.get("event_ts") or event.get("ts") or event.get("created_at")
    if not event_ts:
        return 0
    try:
        normalized = str(event_ts).replace("Z", "+00:00")
        from datetime import datetime

        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except Exception:
        return 0
