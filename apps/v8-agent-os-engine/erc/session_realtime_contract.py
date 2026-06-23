from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.database import db
from core.storage import storage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AuthoritativeSessionRuntimeState:
    run_record: Optional[Dict[str, Any]]
    current_run_id: Optional[str]
    current_run: Optional[Dict[str, Any]]
    todos: Dict[str, Any]
    runtime_status: str
    processes: list[Dict[str, Any]]


def build_current_run_view(run_record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not run_record:
        return None
    session_id = str(run_record.get("session_id") or "").strip() or None
    run_id = str(run_record.get("id") or "").strip() or None
    canonical_message = (
        db.get_chat_canonical_message_by_run(session_id=session_id, run_id=run_id, role="assistant")
        if session_id and run_id
        else None
    )
    return {
        "id": run_id,
        "session_id": session_id,
        "messageId": canonical_message.get("id") if isinstance(canonical_message, dict) else None,
        "status": run_record.get("status"),
        "started_at": run_record.get("started_at"),
        "finished_at": run_record.get("finished_at"),
        "trigger_source": run_record.get("trigger_source"),
        "metadata": dict(run_record.get("metadata") or {}),
    }


def derive_runtime_status(
    *,
    current_run: Optional[Dict[str, Any]],
    workflow_view: Dict[str, Any],
    lane_view: Dict[str, Any],
) -> str:
    run_status = str((current_run or {}).get("status") or "").strip()
    if run_status:
        return run_status

    lane_state = str(lane_view.get("state") or "").strip().lower()
    if lane_state == "active":
        return "running"
    if lane_state == "queued":
        return "queued"
    if lane_state == "rejected":
        return "failed"

    workflow_status = str(workflow_view.get("status") or "").strip()
    if workflow_status:
        return workflow_status
    return "idle"


def select_current_run_record(
    *,
    workflow_view: Dict[str, Any],
    lane_view: Dict[str, Any],
    runtime_events: list[Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    candidate_ids: list[str] = []
    for candidate in (
        lane_view.get("activeRunId"),
        lane_view.get("queuedRunId"),
        workflow_view.get("rootRunId"),
        runtime_events[-1].get("run_id") if runtime_events else None,
    ):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in candidate_ids:
            candidate_ids.append(normalized)

    for run_id in candidate_ids:
        run_record = db.get_run_record(run_id)
        if run_record:
            return run_record, run_id

    return None, candidate_ids[0] if candidate_ids else None


def build_todos_snapshot(*, session_id: str, run_id: Optional[str]) -> Dict[str, Any]:
    snapshot = storage.get_active_todo_snapshot(session_id=session_id, run_id=run_id) or {}
    if not snapshot:
        return {"items": [], "allCompleted": False, "runId": run_id, "sessionId": session_id}
    items = snapshot.get("items")
    normalized_items = items if isinstance(items, list) else []
    return {
        "taskId": snapshot.get("taskId"),
        "taskName": snapshot.get("taskName"),
        "planMarkdown": snapshot.get("planMarkdown"),
        "runId": snapshot.get("runId") or run_id,
        "sessionId": snapshot.get("sessionId") or session_id,
        "createdAt": snapshot.get("createdAt"),
        "updatedAt": snapshot.get("updatedAt"),
        "isActive": bool(snapshot.get("isActive", False)),
        "isStale": bool(snapshot.get("isStale", False)),
        "allCompleted": bool(snapshot.get("allCompleted", False)),
        "items": normalized_items,
    }


def _as_record(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_record_list(value: Any) -> list[Dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string(value: Any) -> str:
    return str(value or "").strip()


def _extract_command_session_payload(result: Any) -> Dict[str, Any] | None:
    payload: Dict[str, Any] | None = None
    if isinstance(result, str):
        trimmed = result.strip()
        if trimmed:
            try:
                parsed = json.loads(trimmed)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
    elif isinstance(result, dict):
        payload = result

    if payload:
        nested_items = payload.get("items")
        if isinstance(nested_items, list):
            for item in nested_items:
                if not isinstance(item, dict):
                    continue
                nested_command = item.get("commandSession")
                if isinstance(nested_command, dict):
                    command_id = _string(nested_command.get("commandId")) or _string(nested_command.get("sessionId"))
                    if command_id:
                        return {
                            "commandId": command_id,
                            "sessionId": _string(nested_command.get("sessionId")) or command_id,
                            "runId": _string(nested_command.get("runId")) or None,
                            "mode": _string(payload.get("mode")) or "dispatch",
                        }
        mode = _string(payload.get("mode")).lower()
        command_id = _string(payload.get("commandId")) or _string(payload.get("sessionId"))
        if mode in {"session", "start"} and command_id:
            return {
                "commandId": command_id,
                "sessionId": _string(payload.get("sessionId")) or command_id,
                "runId": _string(payload.get("runId")) or None,
                "mode": mode,
            }

    if isinstance(result, str):
        marker = "id:"
        lowered = result.lower()
        index = lowered.find(marker)
        if index >= 0:
            command_id = result[index + len(marker):].splitlines()[0].strip()
            if command_id:
                return {"commandId": command_id, "sessionId": command_id, "runId": None}
    return None


def _build_process_message_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    messages = _as_record_list(snapshot.get("messages"))
    for message in messages:
        message_id = _string(message.get("id"))
        message_run_id = _string(message.get("runId"))
        message_session_id = _string(message.get("sessionId") or snapshot.get("session_id") or snapshot.get("sessionId"))
        nodes = _as_record_list(message.get("nodes"))
        tool_calls: Dict[str, Dict[str, Any]] = {}
        for node in nodes:
            if _string(node.get("kind")) != "execution":
                continue
            execution_type = _string(node.get("executionType"))
            tool_name = _string(node.get("toolName"))
            if execution_type == "tool_call" and tool_name in {"run_system_command", "start_background_command", "command_session_broker", "delegation_broker"}:
                tool_call_id = _string(node.get("toolCallId"))
                command_session = _extract_command_session_payload(node.get("result"))
                if command_session and command_session.get("commandId"):
                    mapping[_string(command_session["commandId"])] = {
                        "toolCallId": tool_call_id or None,
                        "sourceMessageId": message_id or None,
                        "runId": _string(command_session.get("runId")) or message_run_id or None,
                        "sessionId": message_session_id or None,
                    }
                if tool_call_id:
                    command_session = command_session or {}
                    tool_calls[tool_call_id] = {
                        "toolCallId": tool_call_id,
                        "sourceMessageId": message_id or None,
                        "runId": _string(command_session.get("runId")) or message_run_id or None,
                        "sessionId": message_session_id or None,
                    }
            elif execution_type == "tool_result":
                command_session = _extract_command_session_payload(node.get("result"))
                command_id = _string((command_session or {}).get("commandId"))
                if not command_id:
                    continue
                if _string((command_session or {}).get("mode")) not in {"session", "start", "dispatch", "observe", "resume", "interrupt"}:
                    continue
                tool_call_id = _string(node.get("toolCallId"))
                link = dict(tool_calls.get(tool_call_id) or {})
                if not link:
                    link = {
                        "toolCallId": tool_call_id or None,
                        "sourceMessageId": message_id or None,
                        "runId": _string(command_session.get("runId")) or message_run_id or None,
                        "sessionId": message_session_id or None,
                    }
                else:
                    if not link.get("runId"):
                        link["runId"] = _string(command_session.get("runId")) or message_run_id or None
                    if not link.get("sessionId"):
                        link["sessionId"] = message_session_id or None
                mapping[command_id] = link
    return mapping


def build_processes_snapshot(*, session_id: Optional[str], snapshot: Dict[str, Any], run_id: Optional[str]) -> list[Dict[str, Any]]:
    from core.native_tools import list_background_process_snapshots

    try:
        process_links = _build_process_message_index(snapshot)
        message_ids = {
            _string(message.get("id"))
            for message in _as_record_list(snapshot.get("messages"))
            if _string(message.get("id"))
        }
        current_run_ids = {
            value
            for value in {
                _string(run_id),
                _string(snapshot.get("currentRunId")),
                _string(_as_record(snapshot.get("currentRun")).get("id")),
            }
            if value
        }
        processes: list[Dict[str, Any]] = []
        for item in list_background_process_snapshots(session_id=session_id):
            command_id = _string(item.get("commandId") or item.get("processId"))
            link = process_links.get(command_id, {})
            process_session_id = _string(item.get("sessionId"))
            process_run_id = _string(item.get("runId"))
            linked_session_id = _string(link.get("sessionId"))
            linked_run_id = _string(link.get("runId"))
            source_message_id = _string(link.get("sourceMessageId"))
            if session_id:
                session_match = process_session_id == session_id or linked_session_id == session_id
                message_match = bool(source_message_id and source_message_id in message_ids)
                run_match = bool(current_run_ids and ({process_run_id, linked_run_id} & current_run_ids))
                has_linkage_evidence = bool(
                    process_session_id
                    or linked_session_id
                    or source_message_id
                    or process_run_id
                    or linked_run_id
                )
                contradictory_session_evidence = bool(
                    (process_session_id and process_session_id != session_id)
                    or (linked_session_id and linked_session_id != session_id)
                )
                contradictory_message_or_run_evidence = bool(
                    source_message_id
                    or process_run_id
                    or linked_run_id
                )
                if (
                    not (session_match or message_match or run_match)
                    and has_linkage_evidence
                    and (contradictory_session_evidence or contradictory_message_or_run_evidence)
                ):
                    continue
            elif current_run_ids and process_run_id and process_run_id not in current_run_ids and linked_run_id not in current_run_ids:
                continue
            process = {
                **item,
                "sessionId": process_session_id or linked_session_id or session_id,
                "processId": command_id or _string(item.get("processId")),
                "commandId": command_id or _string(item.get("processId")),
                "toolCallId": link.get("toolCallId"),
                "sourceMessageId": link.get("sourceMessageId"),
            }
            processes.append(process)
        return processes
    except Exception:
        logger.exception(
            "Failed to build process snapshot; degrading to empty process surface",
            extra={"run_id": run_id},
        )
        return []


def build_lightweight_processes_snapshot(*, session_id: Optional[str], run_id: Optional[str]) -> list[Dict[str, Any]]:
    """Build process HUD data without loading or refreshing the full chat projection.

    The full process surface can link processes back to historical tool-call messages,
    but that requires scanning the chat snapshot. Phone/Web polling needs a stable,
    cheap endpoint first: current session/run bound command sessions are enough for
    the HUD, and unbound legacy processes are intentionally ignored here.
    """

    from core.native_tools import list_background_process_snapshots

    normalized_session_id = _string(session_id) or None
    normalized_run_id = _string(run_id) or None
    try:
        processes: list[Dict[str, Any]] = []
        for item in list_background_process_snapshots(session_id=None, run_id=None):
            process_session_id = _string(item.get("sessionId"))
            process_run_id = _string(item.get("runId"))
            if normalized_session_id and process_session_id != normalized_session_id:
                if not (normalized_run_id and process_run_id == normalized_run_id):
                    continue
            elif not normalized_session_id and normalized_run_id and process_run_id != normalized_run_id:
                continue
            elif not normalized_session_id and not normalized_run_id:
                continue
            command_id = _string(item.get("commandId") or item.get("processId"))
            process = {
                **item,
                "sessionId": process_session_id or normalized_session_id,
                "processId": command_id or _string(item.get("processId")),
                "commandId": command_id or _string(item.get("processId")),
                "toolCallId": item.get("toolCallId"),
                "sourceMessageId": item.get("sourceMessageId"),
            }
            processes.append(process)
        return processes
    except Exception:
        logger.exception(
            "Failed to build lightweight process snapshot; degrading to empty process surface",
            extra={"run_id": run_id},
        )
        return []


def build_context_references(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    references: Dict[str, Dict[str, Any]] = {}
    messages = _as_record_list(snapshot.get("messages"))
    file_tools = {"read_file", "view_file", "replace_file_content", "multi_replace_file_content", "write_to_file"}
    search_tools = {"find_by_name", "grep_search", "list_dir"}
    web_tools = {"search_web", "read_url_content", "web_broker", "web_fetch", "web_read", "web_extract"}

    for message in messages:
        if _string(message.get("role")) != "assistant":
            continue
        source_message_id = _string(message.get("id")) or None
        for node in _as_record_list(message.get("nodes")):
            if _string(node.get("kind")) != "execution" or _string(node.get("executionType")) != "tool_call":
                continue
            tool_name = _string(node.get("toolName"))
            tool_call_id = _string(node.get("toolCallId")) or None
            args = _as_record(node.get("args")) or _as_record(_as_record(node.get("data")).get("args"))

            if tool_name in file_tools:
                path = _string(args.get("AbsolutePath") or args.get("TargetFile") or args.get("filePath"))
                if path:
                    label = path.replace("\\", "/").split("/")[-1]
                    references[f"file:{label.lower()}"] = {
                        "id": f"file:{label.lower()}",
                        "type": "file",
                        "label": label,
                        "details": path,
                        "toolName": tool_name,
                        "toolCallId": tool_call_id,
                        "sourceMessageId": source_message_id,
                    }
                continue

            if tool_name in search_tools:
                query = _string(args.get("Pattern") or args.get("Query") or args.get("SearchDirectory"))
                if query:
                    short_query = f"{query[:15]}..." if len(query) > 15 else query
                    references[f"search:{short_query.lower()}"] = {
                        "id": f"search:{short_query.lower()}",
                        "type": "search",
                        "label": f"搜索: {short_query}",
                        "details": f"Tool: {tool_name}",
                        "toolName": tool_name,
                        "toolCallId": tool_call_id,
                        "sourceMessageId": source_message_id,
                    }
                continue

            if tool_name in web_tools:
                query = _string(args.get("query") or args.get("Url") or args.get("url"))
                if query:
                    short_query = f"{query[:20]}..." if len(query) > 20 else query
                    references[f"web:{short_query.lower()}"] = {
                        "id": f"web:{short_query.lower()}",
                        "type": "web",
                        "label": f"网页: {short_query}",
                        "details": query,
                        "toolName": tool_name,
                        "toolCallId": tool_call_id,
                        "sourceMessageId": source_message_id,
                    }
                continue

            if tool_name in {"memory_recall", "memory_broker"}:
                query = _string(args.get("query") or args.get("entity") or args.get("memory_ref") or args.get("memory_ref_or_date")) or "knowledge"
                short_query = f"{query[:15]}..." if len(query) > 15 else query
                references[f"memory:{short_query.lower()}"] = {
                    "id": f"memory:{short_query.lower()}",
                    "type": "memory",
                    "label": f"记忆: {short_query}",
                    "details": query,
                    "toolName": tool_name,
                    "toolCallId": tool_call_id,
                    "sourceMessageId": source_message_id,
                }

    return list(references.values())[-10:]


def augment_workflow_projection(
    workflow_projection: Optional[Dict[str, Any]],
    *,
    todos: Dict[str, Any],
    current_run: Optional[Dict[str, Any]],
    runtime_status: str,
    latest_seq: int,
) -> Dict[str, Any]:
    record = dict(workflow_projection or {})
    record["todos"] = todos
    record["currentRun"] = current_run
    record["runtimeStatus"] = runtime_status
    record["latestSeq"] = latest_seq
    return record


def resolve_authoritative_session_runtime_state(
    *,
    session_id: str,
    workflow_view: Optional[Dict[str, Any]],
    lane_view: Optional[Dict[str, Any]],
    runtime_events: list[Dict[str, Any]],
) -> AuthoritativeSessionRuntimeState:
    from core.native_tools import list_background_process_snapshots

    normalized_workflow = workflow_view if isinstance(workflow_view, dict) else {}
    normalized_lane = lane_view if isinstance(lane_view, dict) else {}
    run_record, current_run_id = select_current_run_record(
        workflow_view=normalized_workflow,
        lane_view=normalized_lane,
        runtime_events=runtime_events,
    )
    current_run = build_current_run_view(run_record)
    resolved_run_id = current_run_id or (current_run or {}).get("id")
    todos = build_todos_snapshot(session_id=session_id, run_id=resolved_run_id)
    try:
        processes = list_background_process_snapshots(session_id=session_id)
    except Exception:
        logger.exception(
            "Failed to resolve authoritative process snapshots; degrading to empty process list",
            extra={"session_id": session_id, "run_id": resolved_run_id},
        )
        processes = []
    runtime_status = derive_runtime_status(
        current_run=current_run,
        workflow_view=normalized_workflow,
        lane_view=normalized_lane,
    )
    return AuthoritativeSessionRuntimeState(
        run_record=run_record,
        current_run_id=resolved_run_id,
        current_run=current_run,
        todos=todos,
        runtime_status=runtime_status,
        processes=processes,
    )
