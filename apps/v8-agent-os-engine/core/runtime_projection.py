from __future__ import annotations

from collections import defaultdict
import json
import re
from typing import Any, Dict, List, Optional

from core.context_governance import normalize_context_audit
from core.multimodal_payload_adapter import normalize_artifact_record
from core.storage import storage


def _default_supervisor_profile() -> Dict[str, Any]:
    profile = storage.get_supervisor_profile()
    return {
        "agentName": profile.get("name") or "智能主管",
        "agentAvatar": profile.get("avatar") or "",
        "agentRoleLabel": profile.get("roleLabel") or "主理人",
    }

STATUS_LABELS = {
    "created": "待运行",
    "queued": "排队中",
    "running": "进行中",
    "waiting_approval": "等待审批",
    "waiting_input": "等待继续",
    "paused": "已暂停",
    "recoverable_failed": "可恢复失败",
    "failed": "失败",
    "cancelled": "已取消",
    "completed": "已完成",
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


def _normalize_source_group(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"web", "channels", "cron", "hooks"}:
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
    handoff_source = _normalize_source(metadata.get("handoff_source") or metadata.get("handoffSource"))
    transport_managed_by = _normalize_source(metadata.get("transport_managed_by") or metadata.get("transportManagedBy"))
    bridge_backed_source = (
        handoff_source == "openclaw_bridge" or transport_managed_by == "openclaw_bridge"
    ) and source not in {"", "web", "cron", "hooks"} and not source.startswith("hook") and not source.startswith("trigger") and not source.startswith("cron")

    channel_type = _coerce_string(
        _read_string(record, ["channel_type", "channelType"])
        or _read_string(summary, ["channel_type", "channelType"])
        or _read_string(metadata, ["channel_type", "channelType"])
        or (source if bridge_backed_source else "")
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


def _has_channel_context(context: Dict[str, Any]) -> bool:
    return bool(
        context.get("channelType")
        or context.get("channelName")
        or context.get("channelDomain")
        or context.get("accountId")
        or context.get("defaultAccount")
    )


def _is_bridge_managed_channel_record(record: Dict[str, Any], *, source_hint: str | None = None) -> bool:
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    metadata = _parse_metadata(record.get("metadata") or summary.get("metadata"))
    context = _derive_channel_context(record, source_hint=source_hint)
    if not _has_channel_context(context):
        return False

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
    handoff_source = _normalize_source(metadata.get("handoff_source") or metadata.get("handoffSource"))
    transport_managed_by = _normalize_source(metadata.get("transport_managed_by") or metadata.get("transportManagedBy"))
    bridge_managed = handoff_source == "openclaw_bridge" or transport_managed_by == "openclaw_bridge"

    if source in {"channels", "openclaw_channels", "openclaw_channel"}:
        return True
    if bridge_managed:
        return True
    return False


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

    is_canonical_channel_record = _is_bridge_managed_channel_record(record, source_hint=source_hint)
    if explicit == "channels":
        return "channels" if is_canonical_channel_record else "web"
    if is_canonical_channel_record:
        return "channels"
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
            messages.append(
                {
                    "id": payload.get("message_id") or event.get("event_id"),
                    "role": "user",
                    "runId": event.get("run_id"),
                    "content": payload.get("content", ""),
                    "parts": [{"type": "text", "content": payload.get("content", "")}],
                    "timestamp": _event_timestamp_ms(event),
                    "images": payload.get("images") or [],
                    "artifacts": [],
                    "metadata": payload.get("metadata") or {},
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
            active_agent_profile = {
                "agentName": agent.get("name") or agent.get("agent_name") or "智能主管",
                "agentAvatar": agent.get("avatar") or agent.get("agent_avatar") or _default_supervisor_profile().get("agentAvatar", ""),
                "agentRoleLabel": agent.get("roleLabel") or agent.get("agent_role_label") or "主理人",
            }
            assistant = ensure_assistant(event)
            if (
                active_agent_profile["agentName"] == "智能主管"
                or active_agent_profile["agentRoleLabel"] == "主理人"
            ):
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
                    "args": tool.get("args") or {},
                    **active_agent_profile,
                }
            )
            continue

        if topic == "tool.finished":
            if _is_todo_tool_payload(payload):
                continue
            assistant = ensure_assistant(event)
            tool = payload.get("tool") or payload
            assistant["parts"].append(
                {
                    "type": "tool_result",
                    "toolCallId": tool.get("toolCallId") or tool.get("tool_call_id"),
                    "toolName": tool.get("toolName") or tool.get("tool_name"),
                    "result": tool.get("result") or tool.get("result_preview"),
                    **active_agent_profile,
                }
            )
            continue

        if topic == "approval.requested":
            request = payload.get("request") or {}
            interaction_kind = str(
                request.get("interactionKind")
                or request.get("interaction_kind")
                or payload.get("interactionKind")
                or payload.get("interaction_kind")
                or ""
            ).strip().lower()
            approval_kind = str(
                payload.get("approval_kind")
                or payload.get("approvalKind")
                or ""
            ).strip().lower()
            if interaction_kind != "ask_user" and approval_kind != "ask_user":
                continue
            assistant = ensure_assistant(event)
            tool_call_id = (
                (request.get("toolCallId"))
                or payload.get("approval_id")
                or event.get("event_id")
            )
            existing = next(
                (
                    part
                    for part in reversed(assistant["parts"])
                    if part.get("type") == "tool_call" and part.get("toolCallId") == tool_call_id
                ),
                None,
            )
            if existing is not None:
                existing["toolName"] = "ask_user"
                existing["args"] = payload.get("request") or existing.get("args") or {}
            else:
                assistant["parts"].append(
                    {
                        "type": "tool_call",
                        "toolCallId": tool_call_id,
                        "toolName": "ask_user",
                        "args": request,
                        **active_agent_profile,
                    }
                )
            continue

        if topic == "approval.rejected":
            request = payload.get("request") or {}
            interaction_kind = str(
                request.get("interactionKind")
                or request.get("interaction_kind")
                or payload.get("interactionKind")
                or payload.get("interaction_kind")
                or ""
            ).strip().lower()
            approval_kind = str(
                payload.get("approval_kind")
                or payload.get("approvalKind")
                or ""
            ).strip().lower()
            if interaction_kind != "ask_user" and approval_kind != "ask_user":
                continue
            assistant = ensure_assistant(event)
            assistant["parts"].append(
                {
                    "type": "tool_result",
                    "toolCallId": payload.get("approval_id") or event.get("event_id"),
                    "result": payload.get("response") or {"status": topic},
                    **active_agent_profile,
                }
            )
            continue

        if topic in {"run.completed", "run.failed", "run.cancelled"}:
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


def project_runtime_timeline_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projected: List[Dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    for event in events:
        topic = str(event.get("topic") or "")
        payload = event.get("payload") or {}
        entry: Optional[Dict[str, Any]] = None

        if topic == "extension.route.selected":
            skill_count = len(list(payload.get("skillCandidates") or []))
            mcp_count = len(list(payload.get("mcpToolCandidates") or []))
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="progress",
                summary=f"已筛出 {skill_count} 个 Skills，{mcp_count} 个 MCP 工具",
                status="selected",
            )
        elif topic == "extension.skill.loaded":
            skill_name = str(payload.get("skillName") or "未知 Skill")
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="tool",
                summary=f"已读取 Skill：{skill_name}",
                status="loaded",
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
            )
        elif topic == "extension.mcp.candidate_exposed":
            count = int(payload.get("count") or len(list(payload.get("toolNames") or [])) or 0)
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="progress",
                summary=f"已暴露 {count} 个 MCP 工具",
                status="ready",
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
            )
        elif topic == "extension.execution.completed":
            tool_names = [str(item).strip() for item in list(payload.get("toolNames") or []) if str(item).strip()]
            message_preview = _truncate_runtime_summary(str(payload.get("messagePreview") or ""), 96)
            if tool_names:
                summary = f"扩展执行完成，调用了 {', '.join(tool_names[:3])}"
            elif message_preview:
                summary = f"扩展返回：{message_preview}"
            else:
                summary = "扩展执行完成"
            entry = _runtime_timeline_entry(
                event,
                runtime_id="extensions",
                kind="progress",
                summary=summary,
                status="completed",
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
        elif topic == "chat.task_planning_mode.enabled":
            entry = _runtime_timeline_entry(
                event,
                runtime_id="chat",
                kind="progress",
                summary="已开启任务模式",
                status="configured",
                actor_label="聊天运行",
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
            next_state = str(payload.get("status") or payload.get("state") or payload.get("nextState") or "unknown").strip() or "unknown"
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
            governance_payload = normalize_context_audit(payload if isinstance(payload, dict) else {})
            runtime_id = str(governance_payload.get("runtime_kind") or "chat").strip() or "chat"
            saved_tokens = governance_payload.get("estimated_saved_tokens")
            block_count = int(governance_payload.get("block_count") or 0)
            resolved_scope = str(governance_payload.get("resolved_scope") or "").strip()
            summary_parts: list[str] = []
            if saved_tokens is not None:
                summary_parts.append(f"节省 {saved_tokens} tokens")
            if block_count > 0:
                summary_parts.append(f"注入 {block_count} 个 context block")
            if resolved_scope:
                summary_parts.append(f"scope={resolved_scope}")
            summary = "上下文治理已更新"
            if summary_parts:
                summary = "上下文治理已更新：" + "，".join(summary_parts)
            entry = _runtime_timeline_entry(
                event,
                runtime_id=runtime_id,
                kind="governance",
                summary=summary,
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

    projected.sort(key=lambda item: int(item.get("seq") or 0))
    return projected


def project_pending_approvals(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projected: List[Dict[str, Any]] = []
    for row in rows:
        request = row.get("request") or {}
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

    can_resume = recoverable and workflow_status in {"paused", "waiting_input"}
    can_retry = recoverable and workflow_status in {"recoverable_failed", "failed", "cancelled"}
    can_interrupt = workflow_status in {"running", "waiting_approval", "paused"}
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
