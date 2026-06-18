from __future__ import annotations

from typing import Any
import asyncio
import hashlib
import json
import os
import re

from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from core.tool_surface import (
    MAX_TOOL_OUTPUT_LENGTH,
    apply_agent_visible_budget,
    apply_tool_surface_budget,
    tool_output_budget_for_request,
)

DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS = 60000
DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = float(os.environ.get("V8_AGENT_OS_TOOL_CALL_TIMEOUT_SECONDS", "240").strip() or "240")

SUPERVISOR_DIRECT_SCOPE_ALLOWED_TOOLS = {
    "delegation_broker",
    "runtime_broker",
    "ask_user",
    "write_todos",
    "update_todo",
}
SUPERVISOR_DIRECT_SCOPE_GATED_TOOLS = {
    "run_system_command",
    "command_session_broker",
    "web_broker",
    "write_native_file",
    "replace_native_file",
    "edit_native_file",
    "delete_native_file",
    "creative_media_create_job",
    "creative_media_retry_job",
    "computer_use_execute",
    "computer_use_click",
    "computer_use_type_text",
    "computer_use_drag",
}
SUPERVISOR_DIRECT_SCOPE_PROJECT_WRITE_TOOLS = {
    "write_native_file",
    "replace_native_file",
    "edit_native_file",
    "delete_native_file",
}
_ENGINEERING_ROUTE_TOOLS = {
    "run_system_command",
    "command_session_broker",
    "write_native_file",
    "replace_native_file",
    "edit_native_file",
    "delete_native_file",
}
_RESEARCH_ROUTE_TOOLS = {"web_broker"}
_PLANNING_WEB_TOOL_LIMIT = 3
_SPEC_PLANNING_WEB_TOOL_LIMIT = 8
_SUPERVISOR_DIRECT_WRITE_NATIVE_FILE_LIMIT = 3
_SUPERVISOR_TOOL_STEP_EXEMPT_TOOLS = {
    "ask_user",
    "fetch_skill_instructions",
    "memory_broker",
    "research_broker",
    "runtime_broker",
    "spec_broker",
    "update_todo",
    "write_todos",
}


def _planning_fact_gathering_active(state_mapping: dict[str, Any]) -> bool:
    route_context = dict(state_mapping.get("current_route_context") or {})
    planner_mode = str(state_mapping.get("planner_mode") or route_context.get("plannerMode") or "").strip().lower()
    return bool(
        state_mapping.get("task_planning_mode")
        or state_mapping.get("taskPlanningMode")
        or state_mapping.get("specMode")
        or state_mapping.get("spec_mode")
        or route_context.get("taskPlanningMode")
        or route_context.get("task_planning_mode")
        or route_context.get("specMode")
        or route_context.get("spec_mode")
        or planner_mode in {"plan", "planner", "force", "auto"}
    )


def _spec_mode_active(state_mapping: dict[str, Any]) -> bool:
    route_context = dict(state_mapping.get("current_route_context") or {})
    return bool(
        state_mapping.get("specMode")
        or state_mapping.get("spec_mode")
        or route_context.get("specMode")
        or route_context.get("spec_mode")
    )


def _spec_runtime_execution_allowed(state_mapping: dict[str, Any]) -> bool:
    route_context = dict(state_mapping.get("current_route_context") or {})
    if bool(
        state_mapping.get("runtimeAllowed")
        or state_mapping.get("runtimeExecutionAllowed")
        or route_context.get("runtimeAllowed")
        or route_context.get("runtimeExecutionAllowed")
    ):
        return True
    for key in ("specExecutionGate", "spec_execution_gate"):
        gate = state_mapping.get(key) or route_context.get(key)
        if isinstance(gate, dict) and bool(gate.get("runtimeExecutionAllowed") or gate.get("runtimeAllowed")):
            return True
    candidates = (
        state_mapping.get("spec_brief"),
        state_mapping.get("specBrief"),
        route_context.get("specBrief"),
        route_context.get("spec_brief"),
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        pipeline = candidate.get("pipelineControl") if isinstance(candidate.get("pipelineControl"), dict) else {}
        if bool(pipeline.get("runtimeExecutionAllowed") or pipeline.get("runtimeAllowed")):
            return True
    return False


def _command_segment_head(segment: str) -> str:
    text = str(segment or "").strip()
    text = re.sub(r"^(?:&\s*)+", "", text).strip()
    if not text:
        return ""
    if (text.startswith('"') and '"' in text[1:]) or (text.startswith("'") and "'" in text[1:]):
        quote = text[0]
        end = text.find(quote, 1)
        return text[1:end].strip().lower() if end > 1 else ""
    return text.split()[0].strip().lower()


def _planning_readonly_command_allowed(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    lowered = text.lower()
    forbidden_patterns = (
        r"(^|[\s;&|])(?:npm|pnpm|yarn|bun)\s+(?:install|i|add|remove|uninstall|upgrade|update)\b",
        r"(^|[\s;&|])(?:npx\s+(?:--yes\s+|-y\s+)?create-|npm\s+create|pnpm\s+create|yarn\s+create)\b",
        r"(^|[\s;&|])(?:rm|del|erase|move|mv|copy|cp|mkdir|rmdir)\b",
        r"(^|[\s;&|])(?:new-item|set-content|add-content|out-file|remove-item|move-item|copy-item|rename-item)\b",
        r"(^|[\s;&|])(?:git\s+(?:checkout|switch|reset|clean|commit|push|pull|merge|rebase|apply|am|stash))\b",
        r"(^|[\s;&|])(?:python|python3|node|pwsh|powershell|cmd)\b(?!.*\s(?:--version|-v|-h|--help)\b)",
        r"(^|[\s;&|])(?:curl|wget)\b",
        r">|>>|\btee\b|\|\s*(?:set-content|add-content|out-file)\b",
    )
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in forbidden_patterns):
        return False
    # Strip a single leading cwd change; reading from a chosen workspace is okay,
    # but chained arbitrary writes remain blocked by the forbidden patterns above.
    normalized = re.sub(
        r'^\s*cd\s+(?:/d\s+)?(?:"[^"]+"|\'[^\']+\'|\S+)\s*(?:&&|;)\s*',
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    segments = [
        part.strip()
        for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized)
        if part.strip()
    ]
    if not segments:
        return False
    allowed_heads = {
        "pwd",
        "cd",
        "dir",
        "ls",
        "cat",
        "type",
        "echo",
        "git",
        "rg",
        "grep",
        "findstr",
        "get-childitem",
        "gci",
        "get-content",
        "gc",
        "select-string",
        "select-object",
        "sort-object",
        "measure-object",
        "test-path",
        "where.exe",
        "where",
        "npm",
        "pnpm",
        "yarn",
        "node",
    }
    for segment in segments:
        head = _command_segment_head(segment)
        if head not in allowed_heads:
            return False
        seg_lower = segment.lower()
        if head == "git" and not re.search(
            r"^\s*git\s+(?:status|diff|show|log|branch|rev-parse|ls-files|remote|config\s+--get)\b",
            seg_lower,
        ):
            return False
        if head in {"npm", "pnpm", "yarn"} and not re.search(r"^\s*(?:npm|pnpm|yarn)\s+(?:view|info|--version|-v)\b", seg_lower):
            return False
        if head == "node" and not re.search(r"^\s*node\s+(?:--version|-v)\b", seg_lower):
            return False
        if head == "echo" and re.search(r">\s*\S+", seg_lower):
            return False
    return True


def _planning_fact_gathering_allowed(
    *,
    tool_name: str,
    tool_call: dict[str, Any],
    state_mapping: dict[str, Any],
    tool_names: list[str],
    has_active_episode: bool,
) -> bool:
    if has_active_episode or not _planning_fact_gathering_active(state_mapping):
        return False
    if _spec_runtime_execution_allowed(state_mapping):
        return False
    if tool_name == "web_broker":
        web_calls = len([name for name in tool_names if name == "web_broker"])
        limit = _SPEC_PLANNING_WEB_TOOL_LIMIT if _spec_mode_active(state_mapping) else _PLANNING_WEB_TOOL_LIMIT
        return web_calls <= limit
    if tool_name == "run_system_command":
        args = _safe_tool_args(tool_call.get("args"))
        return _planning_readonly_command_allowed(str(args.get("command") or args.get("_raw") or ""))
    return False


def _supervisor_direct_pressure_count(tool_calls_or_names: list[Any]) -> int:
    count = 0
    for item in tool_calls_or_names:
        if isinstance(item, dict):
            tool_name = str(item.get("name") or "").strip()
            args = _safe_tool_args(item.get("args"))
        else:
            tool_name = str(item or "").strip()
            args = {}
        if not tool_name or tool_name in _SUPERVISOR_TOOL_STEP_EXEMPT_TOOLS:
            continue
        if tool_name == "run_system_command" and _planning_readonly_command_allowed(str(args.get("command") or args.get("_raw") or "")):
            continue
        if tool_name in SUPERVISOR_DIRECT_SCOPE_GATED_TOOLS or tool_name.startswith(("creative_media_", "computer_use_", "rpa_")):
            count += 1
    return count


def _supervisor_limited_write_native_file_allowed(
    tool_name: str,
    *,
    direct_pressure_count: int,
    project_write_count: int,
) -> bool:
    return (
        str(tool_name or "").strip() == "write_native_file"
        and project_write_count <= _SUPERVISOR_DIRECT_WRITE_NATIVE_FILE_LIMIT
        and direct_pressure_count <= 10
    )


def _supervisor_direct_scope_operation_fingerprint(run_id: str) -> str:
    return f"supervisor_direct_scope_exception:{str(run_id or '').strip()}"


def _state_messages(state: Any) -> list[Any]:
    if isinstance(state, dict):
        messages = state.get("messages")
    else:
        messages = getattr(state, "messages", None)
    return list(messages or []) if isinstance(messages, list) else []


def _state_mapping(state: Any) -> dict[str, Any]:
    return dict(state or {}) if isinstance(state, dict) else {}


def _safe_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        args = dict(raw_args)
    elif isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            args = dict(parsed) if isinstance(parsed, dict) else {"_raw": raw_args}
        except Exception:
            args = {"_raw": raw_args}
    else:
        args = {}
    compact: dict[str, Any] = {}
    for key, value in args.items():
        if key in {"apiKey", "api_key", "token", "password", "secret", "authorization"}:
            compact[key] = "<redacted>"
            continue
        if isinstance(value, str):
            compact[key] = value if len(value) <= 500 else value[:497].rstrip() + "..."
        elif isinstance(value, (int, float, bool)) or value is None:
            compact[key] = value
        elif isinstance(value, list):
            compact[key] = value[:8]
        elif isinstance(value, dict):
            compact[key] = {str(k): v for k, v in list(value.items())[:8]}
        else:
            compact[key] = str(value)
    return compact


def _route_kind_for_blocked_tool(tool_name: str, *, route_required: bool) -> str:
    normalized = str(tool_name or "").strip()
    if normalized in _RESEARCH_ROUTE_TOOLS:
        return "research"
    if normalized.startswith("creative_media_"):
        return "creative_media"
    if normalized.startswith("computer_use_"):
        return "computer_use"
    if normalized.startswith("rpa_"):
        return "rpa"
    if normalized in _ENGINEERING_ROUTE_TOOLS or route_required:
        return "engineering"
    return "delegation"


def _task_boundary_from_state(state_mapping: dict[str, Any]) -> dict[str, Any]:
    route_context = dict(state_mapping.get("current_route_context") or {})
    task_shape = dict(state_mapping.get("task_shape_hint") or route_context.get("taskShapeHint") or {})
    boundary = task_shape.get("boundaryDecision") if isinstance(task_shape.get("boundaryDecision"), dict) else {}
    return dict(boundary or {})


def _boundary_primary_runtime(boundary: dict[str, Any]) -> str:
    value = str(boundary.get("primaryRuntime") or "").strip()
    return value if value in {"engineering", "research", "creative_media", "computer_use", "rpa", "delegation"} else ""


def _workspace_from_state(state_mapping: dict[str, Any], args: dict[str, Any]) -> str:
    for key in ("workspace_path", "workspacePath", "project_workspace", "projectWorkspace"):
        value = state_mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    route_context = dict(state_mapping.get("current_route_context") or {})
    for key in ("workspacePath", "workspace_path", "projectWorkspace", "project_workspace"):
        value = route_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("workspacePath", "workspace_path", "cwd", "workingDirectory", "working_directory"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _spec_id_from_state(state_mapping: dict[str, Any]) -> str:
    route_context = dict(state_mapping.get("current_route_context") or {})
    for key in ("specId", "spec_id", "currentSpecId", "current_spec_id"):
        value = state_mapping.get(key) or route_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for raw in (
        state_mapping.get("specBrief"),
        state_mapping.get("spec_brief"),
        route_context.get("specBrief"),
        route_context.get("spec_brief"),
    ):
        if not isinstance(raw, dict):
            continue
        for key in ("specId", "spec_id", "currentSpecId", "current_spec_id"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _user_request_from_state(state_mapping: dict[str, Any]) -> str:
    for key in ("latest_user_content", "latestUserContent", "user_request", "userRequest"):
        value = state_mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    route_context = dict(state_mapping.get("current_route_context") or {})
    for key in ("latest_user_content", "latestUserContent", "user_request", "userRequest", "latestHumanUtterance"):
        value = route_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _compact_user_request(text: str, *, limit: int = 1800) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _route_intent_for_blocked_tool(
    *,
    tool_name: str,
    tool_call: dict[str, Any],
    state_mapping: dict[str, Any],
    hard_reasons: list[str],
    route_required: bool,
) -> dict[str, Any]:
    args = _safe_tool_args(tool_call.get("args"))
    route_kind = _route_kind_for_blocked_tool(tool_name, route_required=route_required)
    boundary = _task_boundary_from_state(state_mapping)
    boundary_primary = _boundary_primary_runtime(boundary)
    if "task_boundary_route_correction" in set(hard_reasons) and boundary_primary:
        route_kind = boundary_primary
    elif route_required and boundary_primary in {"engineering", "research", "creative_media", "computer_use", "rpa"}:
        route_kind = boundary_primary
    workspace = _workspace_from_state(state_mapping, args)
    user_request = _compact_user_request(_user_request_from_state(state_mapping))
    inputs: dict[str, Any] = {
        "blockedTool": tool_name,
        "blockedToolArgs": args,
        "blockedReasons": list(hard_reasons),
    }
    if user_request:
        inputs["userRequest"] = user_request
    if workspace:
        inputs["workspacePath"] = workspace
    spec_id = _spec_id_from_state(state_mapping)
    if (
        route_kind == "engineering"
        and _spec_mode_active(state_mapping)
        and _spec_runtime_execution_allowed(state_mapping)
    ):
        if spec_id:
            inputs["specId"] = spec_id
        inputs.update(
            {
                "taskBriefs": [
                    {
                        "taskBriefId": "approved-spec-runtime-execution",
                        "goal": user_request or "Execute the approved Spec through Engineering Runtime.",
                        "context": {
                            "source": "spec_runtime_execution_gate",
                            "blockedTool": tool_name,
                            "blockedToolArgs": args,
                            "userRequest": user_request,
                            **({"workspacePath": workspace} if workspace else {}),
                            **({"specId": spec_id} if spec_id else {}),
                        },
                        "writeSet": [workspace or "<project-workspace>/"],
                        "behaviorScope": ["implementation", "verification"],
                        "requiredCapabilities": ["workspace_mutation", "command_execution", "verification"],
                        "acceptanceContract": (
                            "Execute only the approved Spec tasks for the current specId. "
                            "Return touched files, commands, proof, artifacts, and residual risks."
                        ),
                        "executionLaneHint": "auto",
                        "familyHint": "engineering",
                    }
                ],
                "proofExpectations": [
                    "Execute approved Spec tasks through Engineering Runtime.",
                    "Return a typed handoff with spec/task refs before Supervisor finalizes.",
                ],
            }
        )
        return {
            "kind": route_kind,
            "source": "supervisor_direct_gate",
            "reason": "approved_spec_runtime_execution",
            "tool": tool_name,
            "specId": spec_id,
            "requiredRuntimeAccess": [],
            "inputs": inputs,
        }
    if route_kind == "engineering":
        command = str(args.get("command") or args.get("_raw") or "").strip()
        target_path = str(args.get("path") or args.get("filePath") or args.get("file_path") or "").strip()
        engineering_goal = user_request or command or target_path or f"Execute blocked engineering tool {tool_name}."
        inputs.update(
            {
                "taskBriefs": [
                    {
                        "taskBriefId": "blocked-tool-engineering",
                        "goal": engineering_goal,
                        "context": {
                            "blockedTool": tool_name,
                            "blockedCommand": command,
                            "blockedTargetPath": target_path,
                            "userRequest": user_request,
                            **({"workspacePath": workspace} if workspace else {}),
                        },
                        "writeSet": [target_path or workspace or "<project-workspace>/"],
                        "behaviorScope": ["implementation", "verification"],
                        "requiredCapabilities": ["workspace_mutation", "command_execution", "verification"],
                        "acceptanceContract": (
                            "Complete the original user engineering request, treating the blocked tool call as one attempted step. "
                            "Report touched files, commands, proof, and residual risks."
                        ),
                        "executionLaneHint": "auto",
                        "familyHint": "engineering",
                    }
                ],
                "proofExpectations": [
                    "Record command/file side effects in the Engineering Runtime proof ledger.",
                    "Return a handoff before Supervisor continues implementation.",
                ],
            }
        )
    elif route_kind == "research":
        query = str(args.get("query") or args.get("q") or args.get("_raw") or "").strip()
        inputs.update(
            {
                "query": query,
                "mode": "run",
                "taskBriefs": [
                    {
                        "taskBriefId": "blocked-tool-research",
                        "goal": query or "Run source-backed research for the current task.",
                        "behaviorScope": ["research", "source_triage"],
                        "requiredCapabilities": ["web_research", "evidence_synthesis"],
                        "acceptanceContract": "Return an evidence bundle with synthesized findings and source URLs.",
                        "executionLaneHint": "auto",
                        "familyHint": "research",
                    }
                ],
            }
        )
    else:
        inputs["brief"] = f"Route blocked Supervisor tool {tool_name} through {route_kind} runtime."
    return {
        "kind": route_kind,
        "source": "supervisor_direct_gate",
        "reason": hard_reasons[0] if hard_reasons else "capability_route_required",
        "tool": tool_name,
        "requiredRuntimeAccess": [],
        "inputs": inputs,
    }


def _has_active_runtime_episode(state_mapping: dict[str, Any], *, run_id: str = "", session_id: str = "") -> bool:
    active_states = {
        "detected",
        "routed",
        "queued",
        "leased",
        "active",
        "waiting",
        "waiting_child",
        "waiting_external",
        "waiting_approval",
    }
    route_context = dict(state_mapping.get("current_route_context") or {})
    for raw_episode in list(route_context.get("capabilityEpisodes") or []):
        if not isinstance(raw_episode, dict):
            continue
        if str(raw_episode.get("state") or "").strip() in active_states:
            return True
    try:
        from core.database import db

        episodes = []
        if run_id:
            episodes = db.list_runtime_episodes(run_id=run_id, active_only=True, limit=5)
        if not episodes and session_id:
            episodes = db.list_runtime_episodes(session_id=session_id, active_only=True, limit=5)
        return bool(episodes)
    except Exception:
        return False


def _enqueue_route_intent_episode(
    route_intent: dict[str, Any],
    *,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    kind = str(route_intent.get("kind") or "").strip() or "engineering"
    if not session_id and not run_id:
        return None
    try:
        from erc.runtime_context import get_runtime_context
        from core.runtime_episodes import build_runtime_episode, enqueue_runtime_episode

        runtime_context = get_runtime_context()
        root_run_id = str(
            route_intent.get("rootRunId")
            or route_intent.get("root_run_id")
            or runtime_context.get("rootRunId")
            or runtime_context.get("root_run_id")
            or run_id
            or ""
        ).strip()
        inputs = dict(route_intent.get("inputs") or {}) if isinstance(route_intent.get("inputs"), dict) else {}
        workspace_path = str(
            route_intent.get("workspacePath")
            or route_intent.get("workspace_path")
            or inputs.get("workspacePath")
            or inputs.get("workspace_path")
            or runtime_context.get("workspacePath")
            or runtime_context.get("workspace_path")
            or ""
        ).strip()
        if workspace_path:
            inputs.setdefault("workspacePath", workspace_path)
            inputs.setdefault("workspace_path", workspace_path)
        spec_id = str(route_intent.get("specId") or inputs.get("specId") or "").strip()
        if spec_id:
            inputs.setdefault("specId", spec_id)
        if str(route_intent.get("reason") or "") == "approved_spec_runtime_execution" and spec_id:
            raw_key_payload = {
                "runId": run_id,
                "sessionId": session_id,
                "kind": kind,
                "specId": spec_id,
                "reason": "approved_spec_runtime_execution",
            }
        else:
            raw_key_payload = {
                "runId": run_id,
                "sessionId": session_id,
                "kind": kind,
                "tool": route_intent.get("tool"),
                "inputs": inputs,
            }
        raw_key = json.dumps(
            raw_key_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
        episode_id = f"episode_gate_{digest}"
        need = {
            **route_intent,
            "episodeId": episode_id,
            "needId": episode_id,
            "sessionId": session_id,
            "session_id": session_id,
            "runId": run_id,
            "run_id": run_id,
            "rootRunId": root_run_id or run_id,
            "inputs": inputs,
            "idempotencyKey": f"direct_gate:{digest}",
        }
        try:
            from core.tools.native.runtime import _enrich_route_need_for_episode

            enrich_state = {
                "current_route_context": {
                    "specMode": bool(spec_id) or str(route_intent.get("reason") or "") == "approved_spec_runtime_execution",
                    "specId": spec_id,
                    "specBrief": {"specId": spec_id, "pipelineControl": {"runtimeExecutionAllowed": True}}
                    if spec_id
                    else {},
                    "specExecutionGate": {"runtimeExecutionAllowed": True}
                    if str(route_intent.get("reason") or "") == "approved_spec_runtime_execution"
                    else {},
                    "workspacePath": workspace_path,
                    "latestUserContent": inputs.get("userRequest") or "",
                },
                "specMode": bool(spec_id),
                "specId": spec_id,
                "workspacePath": workspace_path,
                "latestUserContent": inputs.get("userRequest") or "",
            }
            need = _enrich_route_need_for_episode(need, kind=kind, state=enrich_state)
            inputs = dict(need.get("inputs") or inputs)
        except Exception:
            need["inputs"] = inputs
        episode = build_runtime_episode(
            need=need,
            kind=kind,
            state="queued",
            required_runtime_access=list(route_intent.get("requiredRuntimeAccess") or []),
            continuation_target="supervisor_route_gate",
            extra={
                "sessionId": session_id,
                "session_id": session_id,
                "runId": run_id,
                "run_id": run_id,
                "rootRunId": root_run_id or run_id,
                "targetKind": "local_runtime",
                "targetId": kind,
                "retryPolicy": {"maxAttempts": 1},
                "idempotencyKey": f"direct_gate:{digest}",
            },
        )
        return enqueue_runtime_episode(episode, session_id=session_id or None, run_id=run_id or None, priority=20)
    except Exception as exc:
        return {"state": "failed", "errorCode": "gate_episode_enqueue_failed", "errorMessage": str(exc), "kind": kind}


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    calls = getattr(message, "tool_calls", None) or []
    normalized: list[dict[str, Any]] = []
    for call in list(calls or []):
        if isinstance(call, dict):
            normalized.append(call)
    if normalized:
        return normalized
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    for item in list(additional_kwargs.get("tool_calls") or []):
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        normalized.append(
            {
                "id": item.get("id"),
                "name": function.get("name") or item.get("name"),
                "args": function.get("arguments") or item.get("args"),
            }
        )
    return normalized


def _supervisor_direct_tool_calls(state: Any, current_tool_call: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen_current = False
    current_id = str((current_tool_call or {}).get("id") or "").strip()
    current_name = str((current_tool_call or {}).get("name") or "").strip()
    for message in _state_messages(state):
        for call in _message_tool_calls(message):
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            calls.append(call)
            if current_id and str(call.get("id") or "").strip() == current_id:
                seen_current = True
    if current_name and not seen_current:
        calls.append(current_tool_call)
    return calls


def _supervisor_direct_tool_names(state: Any, current_tool_call: dict[str, Any]) -> list[str]:
    return [
        str(call.get("name") or "").strip()
        for call in _supervisor_direct_tool_calls(state, current_tool_call)
        if str(call.get("name") or "").strip()
    ]


def _supervisor_direct_scope_approved(run_id: str, operation_fingerprint: str) -> bool:
    if not run_id or not operation_fingerprint:
        return False
    try:
        from erc.run_service import run_service

        run_record = run_service.get_run(run_id)
    except Exception:
        return False
    operations = (dict((run_record or {}).get("metadata") or {})).get("approvedSafetyOperations")
    if not isinstance(operations, list):
        return False
    for item in operations:
        if not isinstance(item, dict):
            continue
        if str(item.get("fingerprint") or "").strip() != operation_fingerprint:
            continue
        if str(item.get("approval_kind") or "").strip() == "supervisor_direct_scope_exception":
            return True
    return False


def _supervisor_direct_scope_requires_engineering_route(state_mapping: dict[str, Any]) -> bool:
    route_context = dict(state_mapping.get("current_route_context") or {})
    task_shape = dict(state_mapping.get("task_shape_hint") or route_context.get("taskShapeHint") or {})
    boundary = task_shape.get("boundaryDecision") if isinstance(task_shape.get("boundaryDecision"), dict) else {}
    primary = str(task_shape.get("primaryTaskShape") or "").strip()
    secondary = {
        str(item or "").strip()
        for item in list(task_shape.get("secondaryTaskShapes") or [])
        if str(item or "").strip()
    }
    engineering_trigger = dict(route_context.get("engineeringTriggerDecision") or {})
    return bool(
        route_context.get("explicitEngineeringRequested")
        or route_context.get("engineeringRequired")
        or str(route_context.get("engineeringMode") or "").strip() == "force"
        or str(boundary.get("primaryRuntime") or "").strip() == "engineering"
        or primary == "project_coding"
        or ("research" in secondary and primary in {"creative_media", "automation"})
        or bool(engineering_trigger.get("active"))
    )


def _supervisor_direct_scope_tool_allowed_by_runtime_episode(tool_name: str, state_mapping: dict[str, Any]) -> bool:
    normalized_tool = str(tool_name or "").strip()
    if normalized_tool.startswith("creative_media_"):
        tool_kind = "creative_media"
    elif normalized_tool.startswith("computer_use_"):
        tool_kind = "computer_use"
    elif normalized_tool.startswith("rpa_"):
        tool_kind = "rpa"
    else:
        return False
    route_context = dict(state_mapping.get("current_route_context") or {})
    active_id = str(route_context.get("activeCapabilityEpisodeId") or "").strip()
    for raw_episode in list(route_context.get("capabilityEpisodes") or []):
        if not isinstance(raw_episode, dict):
            continue
        episode_kind = str(raw_episode.get("kind") or "").strip()
        episode_id = str(raw_episode.get("episodeId") or raw_episode.get("needId") or "").strip()
        episode_state = str(raw_episode.get("state") or "").strip()
        if episode_kind != tool_kind:
            continue
        if episode_state and episode_state not in {"detected", "routed", "active", "waiting"}:
            continue
        if active_id and episode_id and active_id != episode_id:
            continue
        return True
    return False


def _supervisor_direct_scope_hard_block_message(
    request: Any,
    *,
    tool_node_name: str = "",
) -> ToolMessage | None:
    node_name = str(tool_node_name or "").strip()
    if node_name != "supervisor_tools":
        return None
    tool_call = dict(getattr(request, "tool_call", {}) or {})
    tool_name = str(tool_call.get("name") or "").strip()
    if not tool_name or tool_name in SUPERVISOR_DIRECT_SCOPE_ALLOWED_TOOLS:
        return None
    is_gated_tool = tool_name in SUPERVISOR_DIRECT_SCOPE_GATED_TOOLS or tool_name.startswith(("creative_media_", "computer_use_", "rpa_"))
    if not is_gated_tool:
        return None

    state_mapping = _state_mapping(getattr(request, "state", None))
    if _supervisor_direct_scope_tool_allowed_by_runtime_episode(tool_name, state_mapping):
        return None
    planner_dispatch_status = dict(state_mapping.get("planner_dispatch_status") or {})
    route_required = _supervisor_direct_scope_requires_engineering_route(state_mapping)
    tool_calls = _supervisor_direct_tool_calls(getattr(request, "state", None), tool_call)
    tool_names = [str(call.get("name") or "").strip() for call in tool_calls if str(call.get("name") or "").strip()]
    tool_step_count = len([name for name in tool_names if name])
    direct_pressure_count = _supervisor_direct_pressure_count(tool_calls)
    project_write_count = len([name for name in tool_names if name in SUPERVISOR_DIRECT_SCOPE_PROJECT_WRITE_TOOLS])
    from erc.runtime_context import get_runtime_context

    runtime_context = get_runtime_context()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    session_id = str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip()
    has_active_episode = _has_active_runtime_episode(state_mapping, run_id=run_id, session_id=session_id)
    if _planning_fact_gathering_allowed(
        tool_name=tool_name,
        tool_call=tool_call,
        state_mapping=state_mapping,
        tool_names=tool_names,
        has_active_episode=has_active_episode,
    ):
        tool_call.setdefault("metadata", {})
        if isinstance(tool_call.get("metadata"), dict):
            tool_call["metadata"]["planningFactGathering"] = True
        return None
    if _spec_mode_active(state_mapping) and not _spec_runtime_execution_allowed(state_mapping):
        content = (
            "[spec gate]\n"
            f"Blocked execution tool before approved Spec execution stage: {tool_name}\n"
            "Spec Mode requires requirements/bugfix, design, and tasks to be written and approved before execution runtimes or mutating tools run.\n"
            "Next step: use spec_broker to continue the current Spec stage, or use research_broker/web_broker/fetch_skill_instructions for bounded evidence gathering."
        )
        return ToolMessage(
            content=content,
            name=tool_name,
            tool_call_id=str(tool_call.get("id") or ""),
            status="error",
            additional_kwargs={
                "riskCode": "spec_runtime_execution_not_approved",
                "blockedTool": tool_name,
                "specMode": True,
                "runtimeExecutionAllowed": False,
                "allowedNextTools": [
                    "spec_broker",
                    "fetch_skill_instructions",
                    "research_broker",
                    "web_broker",
                    "memory_broker",
                    "ask_user",
                ],
                "recommendedNextAction": "continue_spec_stage",
            },
        )

    hard_reasons: list[str] = []
    boundary = _task_boundary_from_state(state_mapping)
    forbidden_routes = {
        str(item or "").strip()
        for item in list(boundary.get("forbiddenRoutes") or [])
        if str(item or "").strip()
    }
    boundary_primary = str(boundary.get("primaryRuntime") or "").strip()
    if tool_name.startswith("computer_use_") and "computer_use_for_literal_terminal_only" in forbidden_routes:
        hard_reasons.append("task_boundary_route_correction")
    if (
        tool_name.startswith("creative_media_")
        and boundary_primary == "engineering"
        and "creative_media_as_primary_unless_provider_named" in forbidden_routes
    ):
        hard_reasons.append("task_boundary_route_correction")
    if _spec_mode_active(state_mapping) and _spec_runtime_execution_allowed(state_mapping):
        hard_reasons.append("spec_runtime_execution_requires_runtime_episode")
    if bool(planner_dispatch_status.get("blocked")):
        hard_reasons.append(str(planner_dispatch_status.get("blockedReason") or planner_dispatch_status.get("reason") or "planner_dispatch_blocked"))
    limited_write_allowed = _supervisor_limited_write_native_file_allowed(
        tool_name,
        direct_pressure_count=direct_pressure_count,
        project_write_count=project_write_count,
    )
    if route_required and not limited_write_allowed:
        hard_reasons.append("capability_route_required")
    if direct_pressure_count > 10:
        hard_reasons.append("supervisor_tool_steps_gt_10")
    if project_write_count > _SUPERVISOR_DIRECT_WRITE_NATIVE_FILE_LIMIT:
        hard_reasons.append("supervisor_project_file_writes_gt_3")
    if not hard_reasons:
        return None
    raw_reasons = ", ".join(hard_reasons)
    if has_active_episode:
        hard_reasons = [reason for reason in hard_reasons if reason != "no_runtime_episode_queued"] or ["runtime_episode_already_queued"]
        raw_reasons = ", ".join(hard_reasons)
    route_intent = _route_intent_for_blocked_tool(
        tool_name=tool_name,
        tool_call=tool_call,
        state_mapping=state_mapping,
        hard_reasons=hard_reasons,
        route_required=route_required,
    )
    queued_episode: dict[str, Any] | None = None
    if not has_active_episode:
        queued_episode = _enqueue_route_intent_episode(route_intent, session_id=session_id, run_id=run_id)
        if queued_episode and str(queued_episode.get("state") or "").strip() in {"queued", "active", "leased", "waiting"}:
            has_active_episode = True
    next_action = "wait_episode" if has_active_episode else "runtime_broker(mode='route')"
    next_instruction = (
        "Next step: wait for the active Runtime episode handoff before continuing. Do not call direct mutating tools."
        if has_active_episode
        else (
            "Next step: call runtime_broker(mode='route', need=<routeIntent>) to create a Runtime episode, then wait "
            "for the episode handoff before continuing."
        )
    )
    content = (
        "[route required]\n"
        f"Blocked direct Supervisor tool: {tool_name}\n"
        f"Reason: {raw_reasons}\n"
        f"Boundary route: {route_intent.get('kind') or 'runtime'} via {route_intent.get('source') or 'runtime gate'}.\n"
        "Direct exception is not available for mutating, research, media, desktop, or long-running tools that must be owned by a runtime.\n"
        f"{next_instruction}"
    )
    return ToolMessage(
        content=content,
        name=tool_name,
        tool_call_id=str(tool_call.get("id") or ""),
        status="error",
        additional_kwargs={
            "riskCode": "complex_engineering_route_required",
            "runId": run_id,
            "blockedTool": tool_name,
            "toolStepCount": tool_step_count,
            "directPressureCount": direct_pressure_count,
            "projectWriteCount": project_write_count,
            "reasons": hard_reasons,
            "capabilityNeed": route_intent,
            "routeIntent": route_intent,
            "hasActiveRuntimeEpisode": has_active_episode,
            "allowedNextTools": ["runtime_broker"],
            "recommendedNextAction": next_action,
            "queuedEpisodeId": (queued_episode or {}).get("episodeId") or (queued_episode or {}).get("id"),
            "queuedEpisodeState": (queued_episode or {}).get("state"),
        },
    )


def _maybe_raise_supervisor_direct_scope_gate(request: Any, *, tool_node_name: str = "") -> None:
    # Runtime routing is not an approval surface. Older builds raised a
    # `supervisor_direct_scope_exception` review here, which let the Supervisor
    # keep doing complex project work directly. The hard-block ToolMessage above
    # now returns the route-required next action instead.
    return


def _tool_output_budget_for_request(request: Any, tool_name: str) -> dict[str, Any]:
    return tool_output_budget_for_request(request, tool_name)


def _truncate_tool_message_content(message: ToolMessage, budget_meta: dict[str, Any] | None = None) -> ToolMessage:
    return apply_tool_surface_budget(message, budget_meta)


def _truncate_command_tool_messages(command: Command, budget_meta: dict[str, Any] | None = None) -> Command:
    return apply_agent_visible_budget(command, budget_meta)


def _truncate_agent_visible_result(result, budget_meta: dict[str, Any] | None = None):
    return apply_agent_visible_budget(result, budget_meta)


async def async_tool_call_wrapper(request, execute, *, tool_node_name: str = ""):
    """Wrap tool execution with hook interception and output truncation."""
    from core.hooks_manager import hooks_manager
    from core.native_tools import _raise_runtime_governance_exception_if_needed

    tool_name = request.tool_call.get("name", "unknown")
    budget_meta = tool_output_budget_for_request(request, tool_name)
    hard_block_message = _supervisor_direct_scope_hard_block_message(request, tool_node_name=tool_node_name)
    if hard_block_message is not None:
        return apply_tool_surface_budget(
            hard_block_message,
            budget_meta,
            tool_name=tool_name,
        )
    _maybe_raise_supervisor_direct_scope_gate(request, tool_node_name=tool_node_name)

    try:
        hooks_manager.execute_hook("on_tool_execute_start", tool=tool_name)
    except Exception as hook_err:
        _raise_runtime_governance_exception_if_needed(hook_err)
        error_msg = str(hook_err)
        print(f"[ToolWrapper] Hook blocked tool {tool_name}: {error_msg}")
        return apply_tool_surface_budget(
            ToolMessage(
                content=(
                    f"Error executing tool '{tool_name}': Intercepted and blocked by a system hook. "
                    f"Reason: {error_msg}\nDo not attempt this tool call again."
                ),
                name=tool_name,
                tool_call_id=request.tool_call.get("id", ""),
            ),
            budget_meta,
            tool_name=tool_name,
        )

    try:
        result = await asyncio.wait_for(execute(request), timeout=max(1.0, DEFAULT_TOOL_CALL_TIMEOUT_SECONDS))
    except asyncio.TimeoutError:
        error_msg = (
            f"Tool '{tool_name}' timed out after {DEFAULT_TOOL_CALL_TIMEOUT_SECONDS:.0f}s. "
            "Treat this as a recoverable tool failure and continue with available runtime handoffs or route the work to the matching runtime."
        )
        print(f"[ToolWrapper] {error_msg}")
        return apply_tool_surface_budget(
            ToolMessage(
                content=error_msg,
                name=tool_name,
                tool_call_id=request.tool_call.get("id", ""),
                status="error",
            ),
            budget_meta,
            tool_name=tool_name,
        )
    except Exception as execution_err:
        _raise_runtime_governance_exception_if_needed(execution_err)
        error_msg = str(execution_err)
        print(f"[ToolWrapper] Tool {tool_name} failed: {error_msg}")
        if str(tool_name or "").startswith("network_") and "__pregel_scratchpad" in error_msg:
            from runtimes.network_supervisor.compat_errors import CompatBridgeHardStop

            raise CompatBridgeHardStop(
                f"External client tool bridge hard stop for '{tool_name}': missing LangGraph interrupt context "
                "(__pregel_scratchpad). The model must not retry this network_* tool in the same run."
            ) from execution_err
        return apply_tool_surface_budget(
            ToolMessage(
                content=(
                    f"Error executing tool '{tool_name}': {error_msg}\n"
                    "Do not attempt this tool call again unless the user changes the request or provides missing information."
                ),
                name=tool_name,
                tool_call_id=request.tool_call.get("id", ""),
                status="error",
            ),
            budget_meta,
            tool_name=tool_name,
        )

    try:
        hooks_manager.execute_hook("on_tool_execute_end", tool=tool_name)
    except Exception as hook_err:
        _raise_runtime_governance_exception_if_needed(hook_err)

    return apply_agent_visible_budget(result, budget_meta)


def create_routed_tool_node(tools, name, fallback_goto):
    """Return a ToolNode wrapper that always routes explicitly via Command."""
    async def _wrapped_tool_call(request, execute):
        return await async_tool_call_wrapper(request, execute, tool_node_name=name)

    base_node = ToolNode(
        tools,
        name=name,
        handle_tool_errors=False,
        awrap_tool_call=_wrapped_tool_call,
    )

    def _patch_command_goto(cmd):
        if isinstance(cmd, Command) and not getattr(cmd, "goto", None):
            return Command(goto=fallback_goto, update=cmd.update)
        return cmd

    async def routed_node(state, config=None, runtime=None):
        from langgraph.config import CONF, CONFIG_KEY_RUNTIME
        from langgraph.runtime import Runtime

        invoke_config = dict(config or {})
        configurable = dict(invoke_config.get(CONF) or {})
        if runtime is not None:
            configurable[CONFIG_KEY_RUNTIME] = runtime
        else:
            configurable.setdefault(CONFIG_KEY_RUNTIME, Runtime())
        invoke_config[CONF] = configurable

        result = await base_node.ainvoke(state, config=invoke_config)

        if isinstance(result, list):
            if any(isinstance(item, Command) for item in result):
                return [
                    _patch_command_goto(item) if isinstance(item, Command) else item
                    for item in result
                ]
            return Command(goto=fallback_goto, update={})

        if isinstance(result, dict):
            return Command(goto=fallback_goto, update=result)

        if isinstance(result, Command):
            return _patch_command_goto(result)

        return Command(goto=fallback_goto, update={})

    return routed_node
