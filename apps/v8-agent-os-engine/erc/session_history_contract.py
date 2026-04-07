from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from core.context_governance import extract_latest_context_governance
from core.runtime_projection import build_projection_summary
from erc.session_realtime_contract import build_context_references, build_processes_snapshot


def _parse_metadata(metadata: Any) -> Dict[str, Any]:
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(metadata, str):
        try:
            import json

            parsed = json.loads(metadata)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _coerce_string(value: Any) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _derive_scope_tags(metadata: Dict[str, Any], session_row: Dict[str, Any]) -> List[str]:
    explicit = [
        *([str(item or "").strip() for item in (metadata.get("scopeTags") or [])]),
        *([str(item or "").strip() for item in (metadata.get("scope_tags") or [])]),
        *([str(item or "").strip() for item in (session_row.get("scopeTags") or [])]),
        *([str(item or "").strip() for item in (session_row.get("scope_tags") or [])]),
    ]
    normalized = [item for item in explicit if item]
    if normalized:
        return list(dict.fromkeys(normalized))

    tags: List[str] = []
    for value in (
        metadata.get("project_id"),
        metadata.get("projectId"),
        session_row.get("projectId"),
        session_row.get("project_id"),
        metadata.get("resolved_scope"),
        metadata.get("resolvedScope"),
        session_row.get("resolvedScope"),
        session_row.get("resolved_scope"),
    ):
        current = _coerce_string(value)
        if current and current not in tags:
            tags.append(current)
    return tags


def _build_channel_history_subdocument(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    channel = {
        "channelType": _coerce_string(summary.get("channelType")),
        "channelName": _coerce_string(summary.get("channelName")),
        "channelDomain": _coerce_string(summary.get("channelDomain")),
        "accountId": _coerce_string(summary.get("accountId")),
        "chatType": _coerce_string(summary.get("chatType")),
        "defaultAccount": _coerce_string(summary.get("defaultAccount")),
    }
    return channel if any(channel.values()) else None


def _derive_runtime_family(event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    topic = _coerce_string(event.get("topic")) or ""
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    explicit = (
        _coerce_string(source.get("component"))
        or _coerce_string(payload.get("runtimeFamily"))
        or _coerce_string(payload.get("runtimeId"))
        or _coerce_string(payload.get("runtime"))
    )
    normalized = str(explicit or "").strip().lower()
    if normalized in {
        "chat",
        "memory",
        "automation",
        "extensions",
        "network_supervisor",
        "computer_use",
        "rpa",
        "plugin_host_tool",
        "plugin_host_channel",
        "desktop_live",
    }:
        return normalized
    if topic.startswith("plugin_host.") or topic.startswith("channel."):
        return "plugin_host_channel"
    if topic.startswith("extension."):
        return "extensions"
    if topic.startswith("approval.") or topic.startswith("run.") or topic.startswith("context."):
        return "chat"
    return normalized or "chat"


def _derive_visibility(runtime_family: str, topic: str) -> str:
    if runtime_family == "plugin_host_channel":
        return "history_only"
    if runtime_family == "desktop_live":
        return "excluded"
    if topic == "context.prepared":
        return "hidden"
    return "visible"


def _derive_targets(topic: str, runtime_family: str, visibility: str) -> List[str]:
    if visibility == "excluded":
        return []
    if visibility == "history_only":
        return ["history"]

    targets: List[str] = ["timeline"]
    if topic in {"agent.started", "run.text.delta", "run.reasoning.delta", "message.user.recorded", "message.tool.recorded"}:
        targets.append("message")
    if topic in {"tool.started", "tool.finished"}:
        targets.extend(["message", "runtime"])
    if topic.startswith("approval."):
        targets.extend(["approval", "runtime"])
    if topic == "artifact.recorded":
        targets.extend(["artifact", "runtime"])
    if topic.startswith("run.") or topic.startswith("safety.") or topic.startswith("context."):
        targets.append("runtime")
    if topic == "context.prepared":
        targets.append("context")

    deduped: List[str] = []
    for target in targets:
        if target not in deduped:
            deduped.append(target)
    return deduped


def build_session_history_materialized_record(
    *,
    session_row: Dict[str, Any],
    workflow_view: Optional[Dict[str, Any]],
    approvals: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    latest_seq: int,
    source: str,
    run_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session_row = session_row or {}
    workflow_view = workflow_view or {}
    approvals = approvals or []
    metadata = _parse_metadata(session_row.get("metadata"))
    summary = build_projection_summary(
        session=session_row,
        snapshot=snapshot,
        workflow=workflow_view,
        approvals=approvals,
        latest_seq=latest_seq,
        source=source,
    )
    workflow_status = _coerce_string(workflow_view.get("status")) or _coerce_string(summary.get("workflowStatus")) or "idle"
    root_run_id = _coerce_string(workflow_view.get("rootRunId"))
    owner_runtime = _coerce_string(workflow_view.get("ownerRuntime")) or _coerce_string(summary.get("ownerRuntime"))
    last_activity_at = _coerce_string(summary.get("lastActivityAt")) or _coerce_string(session_row.get("updated_at")) or _coerce_string(session_row.get("updatedAt"))
    channel = _build_channel_history_subdocument(summary)

    return {
        "id": _coerce_string(session_row.get("id")) or "",
        "sessionId": _coerce_string(session_row.get("id")) or "",
        "title": _coerce_string(summary.get("title")) or _coerce_string(session_row.get("title")) or "新对话",
        "source": source,
        "sourceGroup": _coerce_string(summary.get("sourceGroup")) or "web",
        "runtimeOwner": owner_runtime,
        "ownerRuntime": owner_runtime,
        "ownerAgentId": _coerce_string(workflow_view.get("ownerAgentId")) or _coerce_string(summary.get("ownerAgentId")),
        "createdAt": _coerce_string(session_row.get("created_at")) or _coerce_string(session_row.get("createdAt")),
        "updatedAt": _coerce_string(session_row.get("updatedAt")) or _coerce_string(session_row.get("updated_at")),
        "updated_at": _coerce_string(session_row.get("updated_at")) or _coerce_string(session_row.get("updatedAt")),
        "startedAt": _coerce_string(run_record.get("started_at") if run_record else None) or _coerce_string(session_row.get("created_at")) or _coerce_string(session_row.get("createdAt")),
        "lastActivityAt": last_activity_at,
        "endedAt": _coerce_string(run_record.get("finished_at") if run_record else None),
        "status": workflow_status,
        "workflowStatus": workflow_status,
        "statusLabel": _coerce_string(summary.get("statusLabel")),
        "stepStatus": _coerce_string(summary.get("stepStatus")),
        "currentRunId": root_run_id,
        "lastRunId": root_run_id,
        "currentStepId": _coerce_string(summary.get("currentStepId")),
        "currentStepKey": _coerce_string(summary.get("currentStepKey")),
        "currentStepTitle": _coerce_string(summary.get("currentStepTitle")),
        "previewExcerpt": _coerce_string(summary.get("previewExcerpt")),
        "lastNarrativeExcerpt": _coerce_string(summary.get("lastNarrativeExcerpt")),
        "lastRuntimeSummary": _coerce_string(summary.get("lastRuntimeSummary")),
        "pendingApprovalCount": int(summary.get("pendingApprovalCount") or 0),
        "hasPendingApproval": bool(summary.get("hasPendingApproval")),
        "recoverable": bool(summary.get("recoverable")),
        "scopeTags": _derive_scope_tags(metadata, session_row),
        "controls": session_row.get("controls") or None,
        "metadata": session_row.get("metadata") or {},
        "workflowSummary": {
            "workflowStatus": workflow_status,
            "statusLabel": _coerce_string(summary.get("statusLabel")),
            "stepStatus": _coerce_string(summary.get("stepStatus")),
            "ownerRuntime": owner_runtime,
            "ownerAgentId": _coerce_string(workflow_view.get("ownerAgentId")) or _coerce_string(summary.get("ownerAgentId")),
            "currentStepId": _coerce_string(summary.get("currentStepId")),
            "currentStepKey": _coerce_string(summary.get("currentStepKey")),
            "currentStepTitle": _coerce_string(summary.get("currentStepTitle")),
        },
        "channel": channel,
        "channelType": channel.get("channelType") if channel else None,
        "channelName": channel.get("channelName") if channel else None,
        "channelDomain": channel.get("channelDomain") if channel else None,
        "accountId": channel.get("accountId") if channel else None,
        "chatType": channel.get("chatType") if channel else None,
        "defaultAccount": channel.get("defaultAccount") if channel else None,
    }


def build_session_history_ledger_entries(
    *,
    session_id: str,
    runtime_events: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    ledger: List[Dict[str, Any]] = []
    for event in runtime_events:
        payload = event.get("payload") or {}
        message_ref = payload.get("messageId") or payload.get("message_id")
        tool_call_id = payload.get("toolCallId") or payload.get("tool_call_id")
        process_ref = payload.get("processId") or payload.get("commandId") or payload.get("command_id")
        resource_ref = payload.get("artifactId") or payload.get("workspacePath") or payload.get("sourcePath")
        topic = _coerce_string(event.get("topic")) or "event"
        runtime_family = _derive_runtime_family(event, payload)
        visibility = _derive_visibility(runtime_family, topic)
        targets = _derive_targets(topic, runtime_family, visibility)
        ledger.append(
            {
                "eventId": _coerce_string(event.get("event_id")) or f"evt:{event.get('seq') or len(ledger)}",
                "seq": int(event.get("seq") or 0),
                "sessionId": session_id,
                "runId": _coerce_string(event.get("run_id")),
                "ts": _coerce_string(event.get("event_ts") or event.get("ts") or event.get("created_at")) or "",
                "runtimeFamily": _coerce_string(runtime_family) or "chat",
                "eventName": topic,
                "scope": "active_run" if _coerce_string(event.get("run_id")) else "session",
                "visibility": visibility,
                "targets": targets,
                "messageRef": _coerce_string(message_ref),
                "toolCallId": _coerce_string(tool_call_id),
                "processRef": _coerce_string(process_ref),
                "resourceRef": _coerce_string(resource_ref),
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return ledger


def build_session_history_detail(
    *,
    session_row: Dict[str, Any],
    workflow_view: Optional[Dict[str, Any]],
    approvals: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    latest_seq: int,
    source: str,
    runtime_events: List[Dict[str, Any]],
    run_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session_row = session_row or {}
    workflow_view = workflow_view or {}
    approvals = approvals or []
    runtime_events = runtime_events or []
    record = build_session_history_materialized_record(
        session_row=session_row,
        workflow_view=workflow_view,
        approvals=approvals,
        snapshot=snapshot,
        latest_seq=latest_seq,
        source=source,
        run_record=run_record,
    )
    current_run_id = _coerce_string(record.get("currentRunId"))
    return {
        "record": record,
        "ledger": build_session_history_ledger_entries(session_id=record["sessionId"], runtime_events=runtime_events),
        "processes": build_processes_snapshot(snapshot=snapshot or {}, run_id=current_run_id),
        "contextReferences": build_context_references(snapshot or {}),
        "contextGovernance": extract_latest_context_governance(runtime_events),
    }
