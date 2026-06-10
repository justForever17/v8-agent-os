import os
import json
import hashlib
import shutil
import shlex
import subprocess
import mimetypes
import locale
import platform
import re
import time
import unicodedata
from datetime import datetime, timezone
import httpx
import psutil
import threading
import queue
import uuid
import sys
import logging
from collections import deque
from pathlib import Path
from typing import Any, Optional, Annotated, Dict
from core.time_truth import utc_now_iso

if sys.platform == "win32":
    try:
        from winpty import PTY
        HAS_WINPTY = True
    except ImportError:
        HAS_WINPTY = False
else:
    import pty
    import os
    HAS_WINPTY = False

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, Send, interrupt

from core.artifact_store import artifact_store
from core.context.delegation import build_delegation_context, latest_delegation_context
from core.database import db
from core.delegation_broker import (
    build_workset_dispatch_decisions,
    build_minimal_task_brief,
    choose_best_external_worker,
    choose_best_external_worker_with_diagnostics,
    choose_best_local_agent,
    choose_best_local_agent_with_diagnostics,
    compact_external_worker_registry_entry,
    default_external_worker_descriptors,
    external_worker_command_profile,
    expand_delegation_task_briefs,
    make_external_delegation_id,
    make_local_delegation_id,
    normalize_external_worker_descriptors,
    normalize_task_brief,
    normalize_task_briefs,
    parse_delegation_id,
    parse_external_worker_result_block,
    render_external_worker_command,
    reveal_subagent_family,
    task_brief_query_text,
    task_brief_summary,
)
from core.runtime_tool_access import (
    RUNTIME_BROKER_TOOL_NAME,
    grant_runtime_tool_groups,
    normalize_runtime_access,
    revoke_runtime_tool_groups,
    runtime_access_from_route_context,
    runtime_tool_groups_catalog,
)
from core.runtime_episodes import (
    build_runtime_episode,
    enqueue_runtime_episode,
    emit_runtime_episode_event,
    normalize_capability_kind,
    upsert_runtime_episode,
)
from core.computer_use_execution_route import (
    _compact_environment_signal_summary,
    _compact_timing_signal_summary,
    _compact_visual_signal_summary,
    build_compact_execution_route,
    determine_execution_ready_mode,
)
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.native_command_governance import (
    _detect_interactive_command,
    _detect_session_preferred_command,
    _strip_leading_shell_cwd,
    _windows_shell_syntax_violation_payload,
)
from core.native_automation_tools import *  # automation/process/cron/hook tool family compatibility exports
from core.native_command_tools import *  # command/session tool family compatibility exports
from core.native_creative_media_tools import *  # creative media tool family compatibility exports
from core.native_delegation_tools import *  # delegation broker tool family compatibility exports
from core.native_computer_use_tools import *  # computer use tool family compatibility exports
from core.native_memory_tools import *  # memory broker tool family compatibility exports
from core.native_rpa_tools import *  # standalone RPA tool family compatibility exports
from core.native_spec_tools import *  # spec broker tool family compatibility exports
from core.native_todo_tools import *  # supervisor todo tool family compatibility exports
from core.native_workspace_file_tools import *  # workspace/file tool family compatibility exports
from core.native_desktop_governance import (
    _DESKTOP_ROUTE_COMPUTER_USE_MUTATING_TOOLS,
    _DESKTOP_ROUTE_RPA_MUTATING_TOOLS,
    _DESKTOP_ROUTE_SOURCE,
    _computer_use_action_guard,
    _desktop_route_app_stale_reason,
    _desktop_route_compact_metadata,
    _desktop_route_executable_draft_id,
    _desktop_route_gate,
    _desktop_route_gate_failure_response,
    _desktop_route_latest_bound_human_message,
    _desktop_route_merge_into_response,
    _desktop_route_message_fingerprint,
    _desktop_route_normalize_token,
    _desktop_route_task_mismatch_reason,
    _guard_computer_use_steps,
    _is_supervisor_delegated_task_message,
    _message_text_content,
)
from core.native_tool_governance import (
    _enforce_safety_decision,
    _is_langgraph_interrupt,
    _is_safety_operation_previously_approved,
    _raise_langgraph_interrupt_if_needed,
    _raise_runtime_governance_exception_if_needed,
    _safety_operation_fingerprint,
)
from core.native_workspace_governance import (
    _apply_scoped_text_patch,
    _current_run_inventory_key,
    _dominant_newline,
    _line_count_for_guard,
    _workspace_has_existing_items,
    _workspace_inventory_block_payload,
    _workspace_inventory_gate_required,
    _workspace_inventory_status,
    _workspace_inventory_tokens,
)
from core.storage import StorageManager
from runtimes.computer_use.primitives import list_computer_use_primitives, primitive_validation_matrix
from runtimes.rpa.promotion_gate import draft_environment_signal_summary, draft_timing_signal_summary
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import SafetyDecision, safety_guardian
from core.workspace_capability import build_workspace_binding, preflight_command_workspace, resolve_workspace_tool_path
from core.workspace_guard import ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from core.workspace_state_digest import (
    command_may_change_workspace,
    mark_workspace_state_stale,
    record_workspace_inventory_token,
)
from core.tools.media_downloader import download_media_for_vision
from core.tools.research_broker import research_broker
from core.tools.s3_tools import s3_broker, s3_download_file, s3_list_objects, s3_upload_file
from core.spec_service import spec_service
from core.tools.tool_execution_envelope import ToolExecutionEnvelope
from core.tool_observation_detail import (
    _parse_tool_observation_json,
    _redact_tool_observation_preview,
    _render_research_observation_detail,
    _render_web_observation_detail,
    _tool_observation_short_text,
    render_tool_observation_detail,
)
from core.tools.vision_media_analyzer import vision_media_analyzer
from core.tools.web_fetcher import web_broker, web_extract, web_fetch, web_read, web_search
from runtimes.computer_use.verification_contract import (
    build_evidence_summary_payload,
    build_environment_signal_summary_payload,
    build_timing_signal_summary_payload,
    build_visual_signal_summary_payload,
    recommended_next_action_payload,
)

storage = StorageManager()
logger = logging.getLogger(__name__)





_AGENT_TEXT_PREVIEW_CHARS = 700
_AGENT_LIST_LIMIT = 20
_AGENT_DETAIL_LIST_LIMIT = 6
_AGENT_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[78]|\x1b[@-_]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _agent_preview_text(value: Any, *, limit: int = _AGENT_TEXT_PREVIEW_CHARS) -> str | None:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _AGENT_ANSI_ESCAPE_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 32)].rstrip() + f"...[omitted {len(text) - limit + 32} chars]"


def _agent_compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        compact[key] = value
    return compact


def _agent_limited_list(values: Any, *, limit: int = _AGENT_LIST_LIMIT) -> list[Any]:
    return list(values or [])[: max(0, int(limit))]


def _agent_signal_flags(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flags: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            if value:
                flags[key] = True
        elif isinstance(value, (int, float)):
            if value:
                flags[key] = value
        elif isinstance(value, str):
            if value.strip():
                flags[key] = value.strip()
        elif isinstance(value, list):
            items = [item for item in value if item not in (None, "")]
            if items:
                flags[key] = items[:8]
        elif isinstance(value, dict):
            nested = _agent_signal_flags(value)
            if nested:
                flags[key] = nested
    return flags


def _agent_compact_signal_bundle(*payloads: Any) -> dict[str, Any]:
    bundle: dict[str, Any] = {}
    for payload in payloads:
        bundle.update(_agent_signal_flags(payload))
    return bundle


# New OS & Orchestration Tools
# ==========================================

@tool
def http_request(
    method: str,
    url: str,
    headers: dict = None,
    body: str = None,
    timeout: int = 10,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Make an HTTP/HTTPS request.

    Arguments:
        method (str): GET, POST, PUT, DELETE, etc.
        url (str): The URL to request.
        headers (dict, optional): JSON dictionary of headers.
        body (str, optional): The request body string.
        timeout (int): Timeout in seconds. Default 10.
    """
    try:
        runtime_context = get_runtime_context()
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_http_request(method, url, headers=headers, body=body, runtime_context=runtime_context),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到外部网络请求需要确认，是否继续？\n\n{method.upper()} {url}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止网络请求。"

        with httpx.Client(timeout=timeout) as client:
            request = client.build_request(method, url, headers=headers, content=body)
            response = client.send(request)
            result = f"Status Code: {response.status_code}\n"
            # Return truncated to ~10,000 chars to protect context window
            text = response.text
            if len(text) > 10000:
                result += text[:10000] + f"\n\n...[TRUNCATED] (Total {len(text)} chars)"
            else:
                result += text
            safety_guardian.observe_post_action(
                action_family="http_request",
                summary=f"已执行 HTTP 请求：{method.upper()} {url}",
                details={"method": method.upper(), "url": url, "status_code": response.status_code},
                runtime_context=runtime_context,
            )
            return result
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Request failed: {str(e)}"

@tool
def tool_observation_detail(raw_ref: str, max_chars: int = 6000) -> str:
    """Read a bounded, redacted preview for a previous tool observation rawRef."""
    return render_tool_observation_detail(raw_ref, max_chars=max_chars)

def _runtime_broker_payload(
    *,
    mode: str,
    ok: bool,
    summary: str,
    grants: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    rejected: list[str] | None = None,
    error: str | None = None,
    detail_level: str = "summary",
    changed: list[dict[str, Any]] | None = None,
    episode: dict[str, Any] | None = None,
    next_action: str | None = None,
) -> str:
    normalized_detail = str(detail_level or "summary").strip().lower()
    group_items = list(groups or [])
    if normalized_detail not in {"catalog", "detail", "full"}:
        original_group_count = len(group_items)
        group_items = [
            {
                "group": str(item.get("group") or ""),
                "kind": str(item.get("runtimeKind") or ""),
                "label": str(item.get("label") or item.get("group") or ""),
            }
            for item in group_items
            if isinstance(item, dict)
        ][:6]
    else:
        original_group_count = len(group_items)
    payload = {
        "mode": mode,
        "ok": ok,
        "summary": summary,
        "activeGrants": [str((item or {}).get("group") or item) for item in list(grants or [])],
        "availableGroups": group_items,
        "rejected": list(rejected or []),
        "detailMode": normalized_detail if normalized_detail in {"catalog", "detail", "full"} else "summary",
        "detailTool": "runtime_broker(mode='list', detail_level='catalog') for compact catalog; detail_level='full' for diagnostics",
    }
    if changed is not None:
        payload["changed"] = list(changed or [])
    if episode:
        episode_id = str(episode.get("episodeId") or episode.get("needId") or "")
        episode_kind = str(episode.get("kind") or "")
        episode_state = str(episode.get("state") or "")
        payload["episode"] = {
            "episodeId": episode_id,
            "kind": episode_kind,
            "state": episode_state,
            "reason": str(episode.get("reason") or ""),
            "continuationTarget": str(episode.get("continuationTarget") or ""),
        }
        payload["queuedEpisodeId"] = episode_id
        payload["episodeKind"] = episode_kind
        payload["state"] = episode_state
        payload["nextAction"] = "wait_episode"
    if next_action:
        payload["recommendedNextAction"] = next_action
    if normalized_detail not in {"catalog", "detail", "full"} and groups:
        omitted_tools = sum(len(list(item.get("toolNames") or [])) for item in list(groups or []) if isinstance(item, dict))
        payload["omitted"] = {
            "toolNames": omitted_tools,
            "availableGroups": max(0, original_group_count - len(group_items)),
            "reason": "default list is a compact route menu; capability_registry already describes runtime details",
        }
    if error:
        payload["error"] = error
    if normalized_detail in {"catalog", "detail", "full"}:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_RUNTIME_ROUTE_DEFAULT_GROUPS: dict[str, list[str]] = {
    "engineering": ["delegation.recursive"],
    "research": ["research.core"],
    "creative_media": ["creative_media.core"],
    "computer_use": ["computer_use.control"],
    "rpa": ["rpa.run"],
    "delegation": ["delegation.recursive"],
    "memory": ["memory.read"],
}


def _normalize_capability_kind(value: Any) -> str:
    return normalize_capability_kind(value)


def _capability_route_groups(
    *,
    need: dict[str, Any],
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    tool_groups: Optional[list[str]],
) -> list[str]:
    kind = _normalize_capability_kind(need.get("kind") or runtime_kind)
    requested: list[str] = []
    requested.extend(list(need.get("requiredRuntimeAccess") or []))
    requested.extend(list(tool_groups or []))
    if tool_group:
        requested.append(tool_group)
    requested.extend(_RUNTIME_ROUTE_DEFAULT_GROUPS.get(kind, []))
    return normalize_runtime_access(requested, runtime_kind=runtime_kind or kind)


def _planner_task_briefs_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    state = dict(state or {})
    planner_plan = state.get("planner_plan")
    briefs: list[Any] = []
    if isinstance(planner_plan, dict):
        for key in ("workerBriefs", "worker_briefs", "taskBriefs", "task_briefs", "tasks"):
            value = planner_plan.get(key)
            if isinstance(value, list) and value:
                briefs = value
                break
    if not briefs:
        route_context = dict(state.get("current_route_context") or {})
        for episode in list(route_context.get("capabilityEpisodes") or []):
            if not isinstance(episode, dict):
                continue
            inputs = episode.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for key in ("workerBriefs", "worker_briefs", "taskBriefs", "task_briefs", "tasks"):
                value = inputs.get(key)
                if isinstance(value, list) and value:
                    briefs = value
                    break
            if briefs:
                break
    return normalize_task_briefs(briefs)


def _minimal_route_task_from_need(need: dict[str, Any], kind: str) -> dict[str, Any]:
    inputs = dict(need.get("inputs") or {}) if isinstance(need.get("inputs"), dict) else {}
    blocked_tool = str(need.get("tool") or inputs.get("blockedTool") or "").strip()
    args = dict(inputs.get("blockedToolArgs") or {}) if isinstance(inputs.get("blockedToolArgs"), dict) else {}
    command = str(args.get("command") or args.get("_raw") or "").strip()
    target_path = str(args.get("path") or args.get("filePath") or args.get("file_path") or "").strip()
    reason = str(need.get("reason") or inputs.get("brief") or inputs.get("query") or "").strip()
    goal = (
        command
        or target_path
        or reason
        or (f"Handle blocked Supervisor tool {blocked_tool} through {kind} runtime." if blocked_tool else f"Run {kind} runtime episode.")
    )
    brief = {
        "taskBriefId": f"route-{kind}-minimal",
        "title": goal[:96],
        "goal": goal,
        "brief": goal,
        "familyHint": "engineering" if kind == "engineering" else ("research" if kind == "research" else "generalist"),
        "executionLaneHint": "auto",
        "requiredCapabilities": ["workspace_mutation", "verification"] if kind == "engineering" else [],
        "acceptanceContract": "Return a compact handoff with outcome, evidence, and next steps.",
    }
    workspace = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip()
    if workspace:
        brief["workspacePath"] = workspace
        brief["writeSet"] = [target_path or workspace]
    if blocked_tool:
        brief["context"] = {"blockedTool": blocked_tool, **({"workspacePath": workspace} if workspace else {})}
    return brief


def _infer_route_kind_from_payload(payload: dict[str, Any], *fallbacks: Any) -> str:
    candidates: list[str] = []
    for key in ("kind", "runtimeKind", "runtime_kind", "runtime", "capability", "routeIntent", "route_intent", "tool"):
        value = payload.get(key)
        if value is not None:
            candidates.append(str(value))
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    for key in ("kind", "runtimeKind", "capability", "routeIntent", "blockedTool"):
        value = inputs.get(key)
        if value is not None:
            candidates.append(str(value))
    candidates.extend([str(item) for item in fallbacks if item])
    joined = " ".join(candidates).strip().lower().replace("-", "_")
    if not joined:
        return ""
    if any(token in joined for token in ("engineer", "project", "coding", "implementation", "write_native_file", "run_system_command", "install", "build", "workspace")):
        return "engineering"
    if any(token in joined for token in ("research", "search", "evidence", "web_research")):
        return "research"
    if any(token in joined for token in ("delegation", "subagent", "worker", "agent_swarm")):
        return "delegation"
    if any(token in joined for token in ("creative", "media", "asset", "image", "video", "audio")):
        return "creative_media"
    if any(token in joined for token in ("computer_use", "desktop", "browser", "screen")):
        return "computer_use"
    if "rpa" in joined or "trace" in joined:
        return "rpa"
    return _normalize_capability_kind(joined)


def _coerce_route_need_payload(
    need: Any,
    *,
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    reason: Optional[str],
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(need, dict):
        payload = dict(need)
    elif isinstance(need, str):
        raw = need.strip()
        payload = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = dict(parsed)
                elif isinstance(parsed, list):
                    payload = {"taskBriefs": parsed, "reason": reason or "capability_route"}
                else:
                    payload = {"routeIntent": raw, "reason": reason or raw}
            except Exception:
                payload = {"routeIntent": raw, "reason": reason or raw}
    elif need:
        payload = {"reason": str(need)}
    else:
        payload = {}

    route_kind = _infer_route_kind_from_payload(payload, runtime_kind, tool_group, reason)
    if route_kind:
        payload["kind"] = route_kind
    if reason and not str(payload.get("reason") or "").strip():
        payload["reason"] = str(reason).strip()

    inputs = dict(payload.get("inputs") or {}) if isinstance(payload.get("inputs"), dict) else {}
    for source_key, target_key in (
        ("cwd", "workspacePath"),
        ("workspace", "workspacePath"),
        ("workspacePath", "workspacePath"),
        ("workspace_path", "workspacePath"),
        ("task", "task"),
        ("query", "query"),
        ("brief", "brief"),
    ):
        value = payload.get(source_key)
        if value is not None and str(value).strip():
            inputs.setdefault(target_key, value)

    runtime_context = get_runtime_context()
    state_dict = dict(state or {})
    for source in (state_dict, dict(state_dict.get("current_route_context") or {}), runtime_context):
        workspace = str(source.get("workspace_path") or source.get("workspacePath") or "").strip()
        if workspace:
            inputs.setdefault("workspacePath", workspace)
            break
    if inputs:
        payload["inputs"] = inputs
    return payload


def _enrich_route_need_for_episode(
    need: dict[str, Any],
    *,
    kind: str,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(need or {})
    enriched["kind"] = kind
    enriched.setdefault("source", "supervisor")
    enriched.setdefault("reason", str(enriched.get("reason") or "capability_route").strip() or "capability_route")
    inputs = dict(enriched.get("inputs") or {}) if isinstance(enriched.get("inputs"), dict) else {}
    planner_briefs = _planner_task_briefs_from_state(state)

    if kind in {"engineering", "delegation"}:
        route_tasks = planner_briefs or normalize_task_briefs(inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or [])
        if not route_tasks:
            route_tasks = [normalize_task_brief(_minimal_route_task_from_need(enriched, kind))]
        if not inputs.get("workerBriefs"):
            inputs["workerBriefs"] = route_tasks
        if not inputs.get("tasks"):
            inputs["tasks"] = route_tasks
        if not inputs.get("taskBriefs"):
            inputs["taskBriefs"] = route_tasks
        if kind == "engineering":
            inputs.setdefault(
                "proofExpectations",
                [
                    "Execute through Engineering Runtime.",
                    "Return touched files, commands, verification proof, and remaining risks.",
                ],
            )
    elif kind == "research":
        route_briefs = planner_briefs or normalize_task_briefs(inputs.get("taskBriefs") or inputs.get("tasks") or [])
        brief_query = ""
        for brief in route_briefs:
            if not isinstance(brief, dict):
                continue
            for key in ("routeQuery", "query", "question", "goal", "title"):
                value = str(brief.get(key) or "").strip()
                if value:
                    brief_query = value
                    break
            if brief_query:
                break
        query = str(inputs.get("query") or enriched.get("query") or brief_query or enriched.get("reason") or "").strip()
        if query:
            inputs.setdefault("query", query)
            inputs.setdefault("question", query)
        if not inputs.get("taskBriefs"):
            inputs["taskBriefs"] = route_briefs or [normalize_task_brief(_minimal_route_task_from_need(enriched, kind))]
        inputs.setdefault("sourcePolicy", "multi_source_evidence")
        research_blob = json.dumps(inputs.get("taskBriefs") or [], ensure_ascii=False, default=str).lower()
        if any(marker in research_blob for marker in ("full_read", "multi_source", "evidence_bundle", "claim_table", "claimtable", "sourcematrix", "source_matrix", "citations")):
            inputs.setdefault("mode", "run")

    enriched["inputs"] = inputs
    return enriched


_RUNTIME_LIST_ROUTE_INTENT_MARKERS = (
    "episode",
    "route",
    "wait_episode",
    "queued",
    "queue",
    "handoff",
    "plan_only",
    "work_plan",
    "dispatch",
    "degraded",
    "runtime path",
    "创建 episode",
    "创建运行时",
    "进入运行时",
    "运行时路径",
    "路由",
    "入队",
    "等待",
    "回流",
    "交接",
    "派发",
    "委派",
    "降级",
)


def _runtime_list_request_should_route(
    *,
    need: Any,
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    reason: Optional[str],
    detail_level: str,
) -> bool:
    """Correct list calls that are clearly asking for episode routing.

    Some models use runtime_broker(mode="list") while their arguments say they
    want to create/wait for an episode. Catalog/detail list calls must remain
    harmless discovery, so this only triggers for summary-level list requests
    with explicit route/episode intent.
    """

    normalized_detail = str(detail_level or "summary").strip().lower()
    if normalized_detail in {"catalog", "detail", "full"}:
        return False
    route_kind = _infer_route_kind_from_payload(
        need if isinstance(need, dict) else {},
        runtime_kind,
        tool_group,
        reason,
    )
    if route_kind not in _RUNTIME_ROUTE_DEFAULT_GROUPS:
        return False
    if need:
        return True
    probe = " ".join(
        str(item or "")
        for item in (
            runtime_kind,
            tool_group,
            reason,
        )
    ).strip().lower()
    if not probe:
        return False
    return any(marker in probe for marker in _RUNTIME_LIST_ROUTE_INTENT_MARKERS)


def _append_runtime_episode(
    route_context: dict[str, Any],
    *,
    need: dict[str, Any],
    kind: str,
    groups: list[dict[str, Any]],
    allow_direct_fallback: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    route_context = dict(route_context or {})
    runtime_context = get_runtime_context()
    session_id = str(
        runtime_context.get("session_id")
        or runtime_context.get("sessionId")
        or route_context.get("session_id")
        or route_context.get("sessionId")
        or ""
    ).strip() or None
    run_id = str(
        runtime_context.get("run_id")
        or runtime_context.get("runId")
        or route_context.get("run_id")
        or route_context.get("runId")
        or ""
    ).strip() or None
    root_run_id = str(
        runtime_context.get("root_run_id")
        or runtime_context.get("rootRunId")
        or route_context.get("root_run_id")
        or route_context.get("rootRunId")
        or run_id
        or ""
    ).strip() or None
    workspace_path = str(
        runtime_context.get("workspace_path")
        or runtime_context.get("workspacePath")
        or route_context.get("workspace_path")
        or route_context.get("workspacePath")
        or ""
    ).strip() or None
    bound_need = dict(need or {})
    if session_id:
        bound_need.setdefault("sessionId", session_id)
        bound_need.setdefault("session_id", session_id)
    if run_id:
        bound_need.setdefault("runId", run_id)
        bound_need.setdefault("run_id", run_id)
    if root_run_id:
        bound_need.setdefault("rootRunId", root_run_id)
    inputs = dict(bound_need.get("inputs") or {}) if isinstance(bound_need.get("inputs"), dict) else {}
    if workspace_path:
        bound_need.setdefault("workspacePath", workspace_path)
        bound_need.setdefault("workspace_path", workspace_path)
        inputs.setdefault("workspacePath", workspace_path)
        inputs.setdefault("workspace_path", workspace_path)
    bound_need["inputs"] = inputs
    episode = build_runtime_episode(
        need=bound_need,
        kind=kind,
        state="queued",
        required_runtime_access=[str((item or {}).get("group") or item) for item in groups],
        continuation_target=str(bound_need.get("continuationTarget") or "runtime_episode_runner"),
        extra={"allowDirectFallback": bool(allow_direct_fallback)},
    )
    persisted = enqueue_runtime_episode(episode, session_id=session_id, run_id=run_id, priority=int(need.get("priority") or 0))
    merged_episode = {**episode, **{k: v for k, v in persisted.items() if k in {"session_id", "sessionId", "run_id", "runId", "state", "lastHeartbeatAt"}}}
    if session_id:
        merged_episode.setdefault("sessionId", session_id)
        merged_episode.setdefault("session_id", session_id)
    if run_id:
        merged_episode.setdefault("runId", run_id)
        merged_episode.setdefault("run_id", run_id)
    if root_run_id:
        merged_episode.setdefault("rootRunId", root_run_id)
    return upsert_runtime_episode(route_context, merged_episode), merged_episode


def _emit_runtime_episode_event(topic: str, payload: dict[str, Any]) -> None:
    emit_runtime_episode_event(topic, payload, source={"runtime": "supervisor", "tool": "runtime_broker"})


@tool
def runtime_broker(
    mode: str = "list",
    runtime_kind: Optional[str] = None,
    tool_group: Optional[str] = None,
    tool_groups: Optional[list[str]] = None,
    reason: Optional[str] = None,
    detail_level: str = "summary",
    need: Any = None,
    allow_direct_fallback: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Supervisor-only broker for listing, granting, routing, checking, and revoking runtime tool groups for the current run."""
    normalized_mode = str(mode or "list").strip().lower()
    route_context = dict((state or {}).get("current_route_context") or {})
    if normalized_mode == "list" and _runtime_list_request_should_route(
        need=need,
        runtime_kind=runtime_kind,
        tool_group=tool_group,
        reason=reason,
        detail_level=detail_level,
    ):
        normalized_mode = "route"

    if normalized_mode == "list":
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime tool groups available for run-scoped grant.",
                            groups=runtime_tool_groups_catalog(),
                            grants=[
                                {"group": group, "runtimeKind": group.split(".", 1)[0]}
                                for group in runtime_access_from_route_context(route_context)
                            ],
                            detail_level=detail_level,
                            next_action="Prefer runtime_broker(mode='route', need={'kind':'research'|'engineering'|...}); use grant only for explicit tool group access.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
            },
        )

    if normalized_mode == "route":
        need_payload = _coerce_route_need_payload(
            need,
            runtime_kind=runtime_kind,
            tool_group=tool_group,
            reason=reason,
            state=state,
        )
        route_kind = _normalize_capability_kind(need_payload.get("kind") or runtime_kind or tool_group)
        if not route_kind:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=route) requires need.kind or runtime_kind.",
                                error="missing_capability_kind",
                                next_action="Call runtime_broker(mode='route', need={'kind':'research'|'engineering'|...}).",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        need_payload = _enrich_route_need_for_episode(need_payload, kind=route_kind, state=state)
        requested_groups = _capability_route_groups(
            need=need_payload,
            runtime_kind=runtime_kind or route_kind,
            tool_group=tool_group,
            tool_groups=tool_groups,
        )
        updated_context = route_context
        grants: list[dict[str, Any]] = []
        rejected: list[str] = []
        if requested_groups:
            updated_context, grants, rejected = grant_runtime_tool_groups(
                route_context,
                requested_groups,
                reason=str(reason or need_payload.get("reason") or "capability_route").strip(),
            )
        updated_context, episode = _append_runtime_episode(
            updated_context,
            need=need_payload,
            kind=route_kind,
            groups=grants,
            allow_direct_fallback=allow_direct_fallback,
        )
        _emit_runtime_episode_event("capability.need.detected", {"episode": episode})
        _emit_runtime_episode_event("runtime.episode.queued", {"episode": episode})
        if route_kind in {"engineering", "delegation"}:
            next_action = "wait_episode"
        elif route_kind == "research":
            next_action = "wait_episode"
        elif route_kind == "creative_media":
            next_action = "wait_episode"
        elif route_kind in {"computer_use", "rpa"}:
            next_action = "wait_episode"
        else:
            next_action = "wait_episode"
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=not rejected,
                            summary=f"Routed capability need to {route_kind}.",
                            grants=grants,
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
                            detail_level=detail_level,
                            changed=grants,
                            episode=episode,
                            next_action=next_action,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
                "planner_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "dispatched": True,
                    "blocked": False,
                    "reason": "runtime_episode_queued",
                    "episodeId": str(episode.get("episodeId") or ""),
                    "episodeKind": route_kind,
                    "episodeCount": 1,
                    "nextAction": "wait_episode",
                },
            },
        )

    requested_groups = list(tool_groups or [])
    if tool_group:
        requested_groups.append(tool_group)
    requested_groups = normalize_runtime_access(requested_groups, runtime_kind=runtime_kind)

    if normalized_mode == "status":
        active_groups = runtime_access_from_route_context(route_context)
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Current run-scoped runtime tool grants.",
                            groups=runtime_tool_groups_catalog(),
                            grants=[
                                {"group": group, "runtimeKind": group.split(".", 1)[0]}
                                for group in active_groups
                            ],
                            detail_level=detail_level,
                            next_action="Use granted tools or grant/revoke a group.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
            },
        )

    if normalized_mode == "grant":
        if not requested_groups:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=grant) requires tool_group or tool_groups.",
                                error="missing_tool_group",
                                next_action="Call list, then grant a group id.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        updated_context, grants, rejected = grant_runtime_tool_groups(
            route_context,
            requested_groups,
            reason=str(reason or "").strip(),
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=not rejected,
                            summary=(
                                "Runtime tool group granted for this run. It will be visible on the next supervisor step."
                                if not rejected
                                else "Some requested runtime tool groups were not granted."
                            ),
                            grants=grants,
                            groups=runtime_tool_groups_catalog() if str(detail_level or "").strip().lower() in {"catalog", "detail", "full"} else [],
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
                            detail_level=detail_level,
                            changed=grants,
                            next_action="Next step can use the granted tools.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
            },
        )

    if normalized_mode == "revoke":
        updated_context, grants = revoke_runtime_tool_groups(
            route_context,
            requested_groups if requested_groups else None,
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime tool grants updated for this run.",
                            grants=grants,
                            detail_level=detail_level,
                            changed=grants,
                            next_action="Continue with the remaining grants.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
            },
        )

    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_runtime_broker_payload(
                        mode=normalized_mode or "unknown",
                        ok=False,
                        summary=f"Unsupported runtime_broker mode: {normalized_mode}",
                        error="unsupported_mode",
                        next_action="Use one of: list, status, grant, revoke.",
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "current_route_context": route_context,
        },
    )




@tool
def ask_user(question: str, details: Optional[str] = None, tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
    """Ask the user for mandatory input or confirmation and pause the graph until a response is provided."""
    runtime_context = get_runtime_context() or {}
    session_id = str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    agent_id = str(runtime_context.get("agent_id") or runtime_context.get("agentId") or "").strip()
    runtime_kind = str(runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or "").strip()
    node = str(runtime_context.get("node") or runtime_context.get("node_name") or "").strip()
    normalized_tool_call_id = str(tool_call_id or "").strip()

    if session_id:
        try:
            pending_interactions = db.list_ask_user_interactions(session_id=session_id, status="pending")
        except Exception:
            pending_interactions = []
        active_interaction = None
        for item in pending_interactions:
            if not isinstance(item, dict):
                continue
            existing_tool_call_id = str(item.get("tool_call_id") or item.get("toolCallId") or "").strip()
            active_interaction = item
            if normalized_tool_call_id and existing_tool_call_id == normalized_tool_call_id:
                return json.dumps(
                    {
                        "ok": False,
                        "status": "already_pending",
                        "error": "ask_user_already_pending",
                        "summary": "这个澄清请求已经在等待用户回答。",
                        "interactionId": item.get("id"),
                        "question": item.get("question") or item.get("prompt"),
                        "recommendedNextAction": "Wait for the existing ask_user response.",
                    },
                    ensure_ascii=False,
                )
            break
        if active_interaction is not None:
            return json.dumps(
                {
                    "ok": False,
                    "status": "blocked_waiting_for_active_ask_user",
                    "error": "ask_user_blocked_by_active_interaction",
                    "summary": "同一会话已有一个 ask_user 正在等待用户回答；当前请求已被串行保护阻断。",
                    "activeInteractionId": active_interaction.get("id"),
                    "activeQuestion": active_interaction.get("question") or active_interaction.get("prompt"),
                    "requester": {
                        "runtimeKind": runtime_kind,
                        "agentId": agent_id,
                        "node": node,
                        "runId": run_id,
                    },
                    "recommendedNextAction": "Wait for the active ask_user response, then retry if the information is still missing.",
                },
                ensure_ascii=False,
            )

    request = {
        "question": question,
        "prompt": question,
        "toolCallId": tool_call_id,
        "interactionKind": "ask_user",
        "requesterRuntimeKind": runtime_kind,
        "requesterAgentId": agent_id,
        "requesterNode": node,
        "requesterRunId": run_id,
    }
    if details:
        request["details"] = details

    try:
        response = interrupt(request)
    except Exception as exc:
        _raise_langgraph_interrupt_if_needed(exc)
        if "__pregel_scratchpad" in str(exc):
            return json.dumps(
                {
                    "ok": False,
                    "error": "ask_user_unavailable_in_runtime_gate",
                    "summary": "当前工具调用上下文不能暂停询问用户。",
                    "recommendedNextAction": "runtime_broker(mode='route') or queue human guidance for the active run.",
                },
                ensure_ascii=False,
            )
        raise
    if isinstance(response, dict):
        if isinstance(response.get("answer"), str) and response["answer"].strip():
            return response["answer"].strip()
        return json.dumps(response, ensure_ascii=False)
    return str(response)







@tool
async def delegate_network_task(
    peer_id: str,
    task: str,
    timeout_seconds: Optional[int] = None,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    scope_hint: Optional[str] = None,
) -> str:
    """Explicitly delegate a task to a trusted remote V8 node and wait for the final result."""
    from runtimes.network_supervisor.service import network_supervisor_service

    result = await network_supervisor_service.delegate_task(
        peer_id=str(peer_id or "").strip(),
        task=str(task or "").strip(),
        timeout_seconds=int(timeout_seconds) if timeout_seconds is not None else None,
        project_id=str(project_id or "").strip() or None,
        workspace_id=str(workspace_id or "").strip() or None,
        workspace_path=str(workspace_path or "").strip() or None,
        scope_hint=str(scope_hint or "").strip() or None,
    )
    return str(result.get("result") or "").strip()


# Export all tools for easier binding. The registry owns order and family
# metadata; tool implementations intentionally remain in this module.
from core.native_tool_registry import build_native_tools

NATIVE_TOOLS = build_native_tools(globals())
