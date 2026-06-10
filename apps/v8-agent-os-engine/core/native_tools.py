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
from core.tools.native.command_governance import (
    _detect_interactive_command,
    _detect_session_preferred_command,
    _strip_leading_shell_cwd,
    _windows_shell_syntax_violation_payload,
)
from core.tools.native.automation import *  # automation/process/cron/hook tool family compatibility exports
from core.tools.native.command import *  # command/session tool family compatibility exports
from core.tools.native.creative_media import *  # creative media tool family compatibility exports
from core.tools.native.delegation import *  # delegation broker tool family compatibility exports
from core.tools.native.runtime import *  # runtime broker tool family compatibility exports
from core.tools.native.computer_use import *  # computer use tool family compatibility exports
from core.tools.native.memory import *  # memory broker tool family compatibility exports
from core.tools.native.rpa import *  # standalone RPA tool family compatibility exports
from core.tools.native.spec import *  # spec broker tool family compatibility exports
from core.tools.native.todo import *  # supervisor todo tool family compatibility exports
from core.tools.native.workspace_file import *  # workspace/file tool family compatibility exports
from core.tools.native.desktop_governance import (
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
from core.tools.native.tool_governance import (
    _enforce_safety_decision,
    _is_langgraph_interrupt,
    _is_safety_operation_previously_approved,
    _raise_langgraph_interrupt_if_needed,
    _raise_runtime_governance_exception_if_needed,
    _safety_operation_fingerprint,
)
from core.tools.native.workspace_governance import (
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
from core.tools.native.registry import build_native_tools

NATIVE_TOOLS = build_native_tools(globals())



