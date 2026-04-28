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
from langgraph.types import Command, Interrupt as LangGraphInterrupt, Send, interrupt
try:
    from langgraph.errors import GraphBubbleUp, GraphInterrupt, Interrupt as ErrorInterrupt, NodeInterrupt
    LANGGRAPH_INTERRUPT_EXCEPTIONS = tuple(
        interrupt_type
        for interrupt_type in (GraphBubbleUp, GraphInterrupt, ErrorInterrupt, NodeInterrupt, LangGraphInterrupt)
        if interrupt_type is not None
    )
except Exception:  # pragma: no cover - defensive fallback for older langgraph builds
    LANGGRAPH_INTERRUPT_EXCEPTIONS = (LangGraphInterrupt,)


_LANGGRAPH_INTERRUPT_CLASS_NAMES = {
    "GraphBubbleUp",
    "GraphInterrupt",
    "Interrupt",
    "NodeInterrupt",
}


def _is_langgraph_interrupt(value: Any, *, _depth: int = 0) -> bool:
    if _depth > 4 or value is None:
        return False

    if LANGGRAPH_INTERRUPT_EXCEPTIONS and isinstance(value, LANGGRAPH_INTERRUPT_EXCEPTIONS):
        return True

    if value.__class__.__name__ in _LANGGRAPH_INTERRUPT_CLASS_NAMES:
        return True

    if isinstance(value, BaseException):
        return any(_is_langgraph_interrupt(item, _depth=_depth + 1) for item in value.args)

    if isinstance(value, (list, tuple, set)):
        return any(_is_langgraph_interrupt(item, _depth=_depth + 1) for item in value)

    if isinstance(value, dict):
        interrupt_keys = {"approvalKind", "approval_kind", "interactionKind", "interaction_kind", "question", "prompt", "toolCallId", "tool_call_id"}
        if any(key in value for key in interrupt_keys):
            return True
        return any(_is_langgraph_interrupt(item, _depth=_depth + 1) for item in value.values())

    nested_value = getattr(value, "value", None)
    if nested_value is not None and nested_value is not value:
        return _is_langgraph_interrupt(nested_value, _depth=_depth + 1)

    return False


def _raise_langgraph_interrupt_if_needed(exc: Exception) -> None:
    if _is_langgraph_interrupt(exc):
        raise exc


def _raise_runtime_governance_exception_if_needed(exc: Exception) -> None:
    if isinstance(exc, ModelGovernanceInterventionRequired):
        raise exc
    _raise_langgraph_interrupt_if_needed(exc)

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
    make_external_delegation_id,
    make_local_delegation_id,
    normalize_external_worker_descriptors,
    normalize_task_brief,
    normalize_task_briefs,
    parse_delegation_id,
    parse_external_worker_result_block,
    render_external_worker_command,
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
from core.computer_use_execution_route import (
    _compact_environment_signal_summary,
    _compact_timing_signal_summary,
    _compact_visual_signal_summary,
    build_compact_execution_route,
    determine_execution_ready_mode,
)
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.storage import StorageManager
from runtimes.computer_use.primitives import list_computer_use_primitives, primitive_validation_matrix
from runtimes.rpa.promotion_gate import draft_environment_signal_summary, draft_timing_signal_summary
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import SafetyDecision, safety_guardian
from core.workspace_guard import ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from core.tools.media_downloader import download_media_for_vision
from core.tools.s3_tools import s3_broker, s3_download_file, s3_list_objects, s3_upload_file
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

_COMPUTER_USE_APP_RESOLUTION_CACHE_TTL_MS = 3000
_COMPUTER_USE_APP_RESOLUTION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COMPUTER_USE_POINT_TAG_PATTERN = re.compile(
    r"<point>\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*</point>",
    re.IGNORECASE,
)
_DESKTOP_ROUTE_SOURCE = "computer_use_resolve_execution_route"
_DESKTOP_ROUTE_COMPUTER_USE_MUTATING_TOOLS = {
    "computer_use_launch_app",
    "computer_use_ensure_window",
    "computer_use_click_target",
    "computer_use_input_text",
    "computer_use_paste_text",
    "computer_use_paste_files",
    "computer_use_right_click_target",
    "computer_use_hover_target",
    "computer_use_send_hotkey",
    "computer_use_scroll_view",
    "computer_use_drag_pointer",
}
_DESKTOP_ROUTE_RPA_MUTATING_TOOLS = {
    "rpa_run_draft",
    "rpa_run_existing_flow",
}

def _get_memory_runtime():
    from runtimes.memory.runtime import memory_runtime

    return memory_runtime


def _get_computer_use_runtime():
    from runtimes.computer_use.runtime import computer_use_runtime

    return computer_use_runtime


def _get_rpa_runtime():
    from runtimes.rpa.runtime import rpa_runtime

    return rpa_runtime


def _enforce_safety_decision(
    decision: SafetyDecision,
    *,
    tool_call_id: str,
    question: str,
) -> tuple[bool, str | None]:
    operation_fingerprint = _safety_operation_fingerprint(decision, tool_call_id=tool_call_id)
    operation_target_fingerprint = _safety_operation_fingerprint(decision, tool_call_id="", include_tool_call_id=False)
    safety_guardian.log_decision_event(
        action="native_tool_safety",
        decision=decision,
        subject=question,
        metadata={
            "toolCallId": tool_call_id,
            "operationFingerprint": operation_fingerprint,
            "operationTargetFingerprint": operation_target_fingerprint,
        },
    )
    if decision.is_allow():
        return True, None

    if decision.is_block() or not decision.allow_override:
        return False, f"Safety Guardian 已阻止该操作：{decision.reason}"

    allowlist_entry = safety_guardian.is_allowlisted(decision)
    if allowlist_entry:
        safety_guardian.log_decision_event(
            action="native_tool_safety_allowlist_reused",
            decision=decision,
            subject=question,
            metadata={
                "toolCallId": tool_call_id,
                "allowlistEntryId": allowlist_entry.get("id"),
            },
        )
        return True, None

    if operation_fingerprint and _is_safety_operation_previously_approved(operation_fingerprint, operation_target_fingerprint):
        safety_guardian.log_decision_event(
            action="native_tool_safety_approval_reused",
            decision=decision,
            subject=question,
            metadata={
                "toolCallId": tool_call_id,
                "operationFingerprint": operation_fingerprint,
                "operationTargetFingerprint": operation_target_fingerprint,
            },
        )
        return True, None

    request_payload = decision.to_interrupt_request(question=question, tool_call_id=tool_call_id)
    if operation_fingerprint:
        request_payload["operationFingerprint"] = operation_fingerprint
    if operation_target_fingerprint:
        request_payload["operationTargetFingerprint"] = operation_target_fingerprint
    request_payload["allowlistCandidate"] = safety_guardian.build_allowlist_candidate(decision)

    raise ModelGovernanceInterventionRequired(
        f"Safety Guardian 检测到治理审批请求：{decision.reason}",
        approval_kind="safety_review",
        question=question,
        details={
            "safety": decision.to_payload(),
            "toolCallId": tool_call_id,
            "operationFingerprint": operation_fingerprint,
            "operationTargetFingerprint": operation_target_fingerprint,
        },
        request_payload=request_payload,
    )


def _safety_operation_fingerprint(
    decision: SafetyDecision,
    *,
    tool_call_id: str = "",
    include_tool_call_id: bool = True,
) -> str:
    details = decision.details if isinstance(decision.details, dict) else {}
    runtime_context = details.get("runtime_context") if isinstance(details.get("runtime_context"), dict) else {}
    target = (
        details.get("path")
        or details.get("command")
        or details.get("url")
        or details.get("target")
        or details.get("pid")
        or ""
    )
    payload = {
        "runId": str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip(),
        "riskCode": decision.risk_code,
        "governanceTarget": decision.governance_target,
        "target": str(target).strip(),
    }
    if include_tool_call_id:
        payload["toolCallId"] = str(tool_call_id or "").strip()
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"safety:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _is_safety_operation_previously_approved(
    operation_fingerprint: str,
    operation_target_fingerprint: str = "",
) -> bool:
    runtime_context = get_runtime_context()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    if not run_id:
        return False
    run_record = db.get_run_record(run_id)
    if not run_record:
        return False
    operations = (run_record.get("metadata") or {}).get("approvedSafetyOperations")
    if not isinstance(operations, list):
        return False
    candidates = {str(operation_fingerprint or "").strip(), str(operation_target_fingerprint or "").strip()}
    candidates.discard("")
    for item in operations:
        if not isinstance(item, dict):
            continue
        if str(item.get("fingerprint") or "") in candidates:
            return True
        if str(item.get("targetFingerprint") or item.get("operationTargetFingerprint") or "") in candidates:
            return True
    return False

# Default text extensions to allow reading if mimetype fails
TEXT_EXTENSIONS = {'.txt', '.md', '.py', '.json', '.yaml', '.yml', '.csv', '.log', '.sh', '.bat', '.ps1', '.html', '.css', '.js', '.ts', '.tsx', '.jsx'}

def is_binary(file_path: str) -> bool:
    """Check if a file is highly likely to be binary."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        if mime_type.startswith('text/') or mime_type == 'application/json':
            return False
    
    ext = Path(file_path).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return False
        
    # Read first 1024 bytes and check for null bytes
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
            if b'\0' in chunk:
                return True
    except Exception:
        pass
    
    # If not a known text ext and not recognized by mime, assume binary for safety
    return True


def _detect_interactive_command(command: str) -> str | None:
    """Detect obviously interactive/TTY-oriented commands that should never run in blocking mode."""
    stripped = (command or "").strip()
    if not stripped:
        return None

    lowered = stripped.lower()
    tokens = lowered.split()
    if not tokens:
        return None

    head = tokens[0]
    simple_flags = {"-h", "--help", "-v", "--version"}
    if any(flag in tokens for flag in simple_flags):
        return None

    bare_interactive = {
        "qwen",
        "claude",
        "gemini",
        "codex",
        "aider",
        "python",
        "python3",
        "ipython",
        "node",
        "bash",
        "sh",
        "pwsh",
        "powershell",
        "cmd",
        "ssh",
        "sftp",
        "ftp",
        "telnet",
        "mysql",
        "psql",
        "sqlite3",
    }
    if lowered in bare_interactive:
        return f"检测到交互式命令 `{stripped}`，同步命令工具会阻塞等待输入。"

    if head in {"python", "python3"}:
        non_interactive_flags = {"-c", "-m"}
        if len(tokens) == 1 or not any(flag in tokens for flag in non_interactive_flags):
            return f"检测到 `{head}` 进入 REPL/交互模式的风险。"

    if head in {"node", "bash", "sh", "pwsh", "powershell"}:
        non_interactive_flags = {"-c", "-command", "-file", "-e"}
        if len(tokens) == 1 or not any(flag in tokens for flag in non_interactive_flags):
            return f"检测到 `{head}` 可能启动交互式终端。"

    if head == "cmd" and "/c" not in tokens:
        return "检测到 `cmd` 缺少 `/c`，很可能进入交互式终端。"

    if head in {"qwen", "claude", "gemini", "codex", "aider"}:
        return f"检测到 `{head}` 可能需要 TTY 或交互输入。"

    return None


def _detect_session_preferred_command(command: str) -> str | None:
    lowered = str(command or "").strip().lower()
    if not lowered:
        return None
    if lowered.startswith("npx skills "):
        return f"检测到 `{command}` 可能进入 Skills CLI 的交互会话，建议进入 session 模式。"
    long_running_markers = (
        "uvicorn ",
        "gunicorn ",
        "npm run dev",
        "pnpm dev",
        "yarn dev",
        "npm start",
        "pnpm start",
        "yarn start",
        "next dev",
        "vite",
        "tail -f",
        "watch ",
        "python -m http.server",
        "python -m uvicorn",
    )
    if any(marker in lowered for marker in long_running_markers):
        return f"检测到 `{command}` 更像长驻进程，建议进入 session 模式以便轮询和中断。"
    return None


def _normalize_background_input(data: str) -> str:
    if sys.platform == "win32":
        return data.replace("\r\n", "\n").replace("\n", "\r\n")
    return data


def _decode_background_input_escapes(data: str) -> str:
    """Decode a small, safe subset of escape sequences for interactive terminal input.

    Supervisors sometimes pass literal ``\\n`` / ``\\r`` / ``\\u001b`` strings instead of
    actual control characters. We only decode a narrow set that maps to terminal
    control input so we don't accidentally rewrite ordinary Windows paths.
    """
    text = str(data or "")
    if "\\" not in text:
        return text

    replacements = (
        ("\\r\\n", "\n"),
        ("\\n", "\n"),
        ("\\r", "\r"),
        ("\\u001b", "\x1b"),
        ("\\x1b", "\x1b"),
        ("\\e", "\x1b"),
    )
    normalized = text
    for source, target in replacements:
        normalized = normalized.replace(source, target)
    return normalized


def _write_winpty_input(pty_win: Any, data: str) -> None:
    """Feed Windows PTY input in a way that mimics a user pressing Enter.

    Some TUI CLIs (notably AI coding agents) do not reliably submit prompts when a
    full `text + CRLF` payload is written in one shot. Splitting text and Enter
    into separate writes makes the interaction behave much closer to a real user.
    """
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n")
    segments = normalized.split("\n")
    last_index = len(segments) - 1

    for index, segment in enumerate(segments):
        if segment:
            pty_win.write(segment)
            # Give Ink/TUI input handlers a moment to commit the typed content
            # before we inject Enter.
            time.sleep(0.03)
        should_send_enter = index < last_index
        if should_send_enter:
            pty_win.write("\r")
            time.sleep(0.05)


def _terminate_run_background_commands(run_id: str | None, *, interactive_only: bool = True) -> int:
    if not run_id:
        return 0

    removed = 0
    for command_id, bg_proc in list(_bg_processes.items()):
        if getattr(bg_proc, "run_id", None) != run_id:
            continue
        if interactive_only and not getattr(bg_proc, "interactive", False):
            continue
        try:
            bg_proc.terminate()
        except Exception:
            pass
        _bg_processes.pop(command_id, None)
        removed += 1
    return removed


def _computer_use_runtime_kwargs(goal: str) -> dict:
    runtime_context = get_runtime_context()
    root_goal = str(runtime_context.get("goal") or "").strip()
    normalized_goal = str(goal or "").strip()
    local_goal_prefixes = (
        "launch_app:",
        "launch_app_recover:",
        "observe_scene",
        "click_target",
        "input_text",
        "paste_text",
        "paste_files",
        "right_click_target",
        "hover_target",
        "send_hotkey",
        "scroll_view",
        "drag_pointer",
        "focus_window",
        "plan_step_",
    )
    effective_goal = normalized_goal
    if root_goal and (
        not normalized_goal
        or normalized_goal.startswith(local_goal_prefixes)
    ):
        effective_goal = root_goal
    return {
        "session_id": runtime_context.get("session_id"),
        "run_id": runtime_context.get("run_id"),
        "user_id": runtime_context.get("user_id") or "anonymous",
        "project_id": runtime_context.get("project_id"),
        "workspace_id": runtime_context.get("workspace_id"),
        "workspace_path": runtime_context.get("workspace_path"),
        "goal": effective_goal,
        "invocation_metadata": {
            "requestedGoal": normalized_goal or None,
            "rootGoal": root_goal or None,
        },
    }


def _message_text_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _is_supervisor_delegated_task_message(message: Any) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    normalized = _message_text_content(message).strip()
    return normalized.startswith("[Supervisor Delegated Task")


def _desktop_route_message_fingerprint(message: Any, index: int) -> str:
    explicit_id = str(getattr(message, "id", "") or "").strip()
    if explicit_id:
        return explicit_id
    digest = hashlib.md5(f"{index}:{_message_text_content(message)}".encode("utf-8")).hexdigest()[:12]
    return f"human:{index}:{digest}"


def _desktop_route_latest_bound_human_message(state: dict[str, Any] | None) -> tuple[str | None, Any | None]:
    messages = list((state or {}).get("messages") or [])
    fallback: tuple[str | None, Any | None] = (None, None)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, HumanMessage):
            continue
        fingerprint = _desktop_route_message_fingerprint(message, index)
        if fallback == (None, None):
            fallback = (fingerprint, message)
        if _is_supervisor_delegated_task_message(message):
            continue
        return fingerprint, message
    return fallback


def _desktop_route_normalize_token(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _desktop_route_compact_metadata(
    desktop_route: dict[str, Any] | None,
    *,
    route_gate_applied: bool,
    runtime_governed: bool = True,
    gate_error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "routeGateApplied": bool(route_gate_applied),
        "desktopRouteMode": str((desktop_route or {}).get("recommendedMode") or "").strip() or None,
        "executionReadyMode": str((desktop_route or {}).get("executionReadyMode") or "").strip() or None,
        "runtimeGoverned": bool(runtime_governed),
        "gateErrorCode": str(gate_error_code or "").strip() or None,
    }


def _desktop_route_merge_into_response(
    response: str,
    *,
    desktop_route: dict[str, Any] | None,
    route_gate_applied: bool,
    runtime_governed: bool = True,
    gate_error_code: str | None = None,
) -> str:
    try:
        payload = json.loads(response)
    except Exception:
        return response
    if not isinstance(payload, dict):
        return response
    payload.update(
        _desktop_route_compact_metadata(
            desktop_route,
            route_gate_applied=route_gate_applied,
            runtime_governed=runtime_governed,
            gate_error_code=gate_error_code,
        )
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _desktop_route_gate_failure_response(
    *,
    gate_error_code: str,
    summary: str,
    desktop_route: dict[str, Any] | None,
    recommended_next_tool: str,
    runtime_governed: bool = True,
) -> str:
    payload = {
        "ok": False,
        "status": "blocked",
        "blocked": True,
        "summary": summary,
        "gateErrorCode": gate_error_code,
        "recommendedNextTool": recommended_next_tool,
        "recommendedMode": str((desktop_route or {}).get("recommendedMode") or "").strip() or None,
        "executionReadyMode": str((desktop_route or {}).get("executionReadyMode") or "").strip() or None,
        "runtimeGoverned": bool(runtime_governed),
    }
    payload.update(
        _desktop_route_compact_metadata(
            desktop_route,
            route_gate_applied=True,
            runtime_governed=runtime_governed,
            gate_error_code=gate_error_code,
        )
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _desktop_route_app_stale_reason(
    *,
    desktop_route: dict[str, Any],
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
) -> str | None:
    normalized_requested_app = _desktop_route_normalize_token(app_query)
    if not normalized_requested_app:
        return None
    route_app_id = _desktop_route_normalize_token(desktop_route.get("appId"))
    route_requested_app = _desktop_route_normalize_token(desktop_route.get("requestedApp"))
    resolved_app_id = _desktop_route_normalize_token((resolved_app or {}).get("appId"))
    if route_app_id and resolved_app_id and route_app_id != resolved_app_id:
        return "当前工具目标应用与已解析的桌面路由不一致。"
    if route_requested_app and normalized_requested_app != route_requested_app and route_app_id != resolved_app_id:
        return "当前工具目标应用与已绑定的桌面路由不一致。"
    return None


def _desktop_route_task_mismatch_reason(
    *,
    desktop_route: dict[str, Any],
    goal: str | None,
    target: str | None,
) -> str | None:
    normalized_goal = _desktop_route_normalize_token(goal)
    route_goal = _desktop_route_normalize_token(desktop_route.get("goal"))
    if normalized_goal and route_goal and normalized_goal != route_goal:
        return "当前任务目标与已绑定的桌面路由不一致，请重新调用 computer_use_resolve_execution_route。"

    normalized_target = _desktop_route_normalize_token(target)
    route_target = _desktop_route_normalize_token(desktop_route.get("target"))
    if normalized_target and route_target and normalized_target != route_target:
        return "当前任务 target 与已绑定的桌面路由不一致，请重新调用 computer_use_resolve_execution_route。"
    return None


def _desktop_route_executable_draft_id(desktop_route: dict[str, Any] | None) -> str | None:
    route_payload = dict(desktop_route or {})
    direct_draft_id = str(route_payload.get("recommendedDraftId") or "").strip()
    if direct_draft_id:
        return direct_draft_id
    recommended_match = dict(route_payload.get("recommendedMatch") or {})
    source = dict(recommended_match.get("source") or {})
    source_draft_id = str(source.get("draftId") or "").strip()
    return source_draft_id or None


def _desktop_route_gate(
    *,
    state: dict[str, Any] | None,
    tool_name: str,
    app_query: str | None = None,
    resolved_app: dict[str, Any] | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    if not isinstance(state, dict):
        return True, None, None

    current_route_context = dict(state.get("current_route_context") or {})
    desktop_route = dict(current_route_context.get("desktopRoute") or {})
    if not desktop_route:
        return (
            False,
            _desktop_route_gate_failure_response(
                gate_error_code="ROUTE_GATE_REQUIRED",
                summary="桌面类变更工具必须先调用 computer_use_resolve_execution_route，再按返回的 executionReadyMode 选择执行面。",
                desktop_route=None,
                recommended_next_tool="computer_use_resolve_execution_route",
            ),
            None,
        )

    latest_human_id, _ = _desktop_route_latest_bound_human_message(state)
    bound_human_id = str(desktop_route.get("boundHumanMessageId") or "").strip()
    if bound_human_id and latest_human_id and bound_human_id != latest_human_id:
        return (
            False,
            _desktop_route_gate_failure_response(
                gate_error_code="STALE_ROUTE_CONTEXT",
                summary="桌面路由已过期：检测到新的用户输入或任务上下文，请重新调用 computer_use_resolve_execution_route。",
                desktop_route=desktop_route,
                recommended_next_tool="computer_use_resolve_execution_route",
            ),
            desktop_route,
        )

    stale_app_reason = _desktop_route_app_stale_reason(
        desktop_route=desktop_route,
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if stale_app_reason:
        return (
            False,
            _desktop_route_gate_failure_response(
                gate_error_code="STALE_ROUTE_CONTEXT",
                summary=stale_app_reason,
                desktop_route=desktop_route,
                recommended_next_tool="computer_use_resolve_execution_route",
            ),
            desktop_route,
        )

    execution_ready_mode = str(desktop_route.get("executionReadyMode") or "").strip() or "learn_mode"
    if tool_name in _DESKTOP_ROUTE_RPA_MUTATING_TOOLS:
        if execution_ready_mode not in {"reuse_mode", "hybrid_mode"}:
            return (
                False,
                _desktop_route_gate_failure_response(
                    gate_error_code="RUNTIME_MISMATCH",
                    summary="当前桌面路由已进入 learn_mode，Supervisor 应直接使用高层 computer_use_* 变更工具，而不是继续走 RPA 执行面。",
                    desktop_route=desktop_route,
                    recommended_next_tool="computer_use_launch_app",
                ),
                desktop_route,
            )
    elif tool_name in _DESKTOP_ROUTE_COMPUTER_USE_MUTATING_TOOLS:
        if execution_ready_mode in {"reuse_mode", "hybrid_mode"}:
            return (
                False,
                _desktop_route_gate_failure_response(
                    gate_error_code="RUNTIME_MISMATCH",
                    summary="当前桌面路由要求先从 RPA 主执行链进入，Supervisor 不应直接调用 mutating computer_use_* 工具。",
                    desktop_route=desktop_route,
                    recommended_next_tool=str(desktop_route.get("recommendedTool") or "rpa_run_draft"),
                ),
                desktop_route,
            )

    return True, None, desktop_route


def _computer_use_parse_point_tag(value: str | None) -> list[float] | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    matched = _COMPUTER_USE_POINT_TAG_PATTERN.search(normalized)
    if not matched:
        return None
    return [float(matched.group(1)), float(matched.group(2))]


def _computer_use_action_guard(
    *,
    action_type: str,
    target: dict,
    tool_call_id: str,
) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_computer_use_action(
        action_type=action_type,
        target=target,
        runtime_context=runtime_context,
    )
    question = (
        f"Safety Guardian 检测到桌面控制动作存在风险，是否继续？\n\n"
        f"动作：{action_type}\n"
        f"目标：{json.dumps(target, ensure_ascii=False, indent=2)}"
    )
    return _enforce_safety_decision(decision, tool_call_id=tool_call_id, question=question)


def _guard_computer_use_steps(
    *,
    steps: list[dict],
    tool_call_id: str,
) -> tuple[bool, str | None]:
    for step in steps:
        action = str(step.get("action") or "").strip().lower()
        if action not in {
            "click",
            "double_click",
            "type_text",
            "hotkey",
            "scroll",
            "find_and_type",
            "scroll_list",
            "click_toolbar_action",
        }:
            continue
        effective_action = action
        if action == "find_and_type":
            effective_action = "type_text"
        elif action == "scroll_list":
            effective_action = "scroll"
        elif action == "click_toolbar_action":
            effective_action = "click"
        target = {
            "app_id": step.get("app_id"),
            "selector_key": step.get("selector_key"),
            "action_name": step.get("action_name"),
            "element_id": step.get("element_id"),
            "name": step.get("name"),
            "name_contains": step.get("name_contains"),
            "automation_id": step.get("automation_id"),
            "control_type": step.get("control_type"),
            "class_name": step.get("class_name"),
            "window_title": step.get("window_title"),
            "window_handle": step.get("window_handle"),
            "sequence": step.get("sequence"),
            "amount": step.get("amount"),
            "text_preview": str(step.get("text") or "")[:80] if effective_action == "type_text" else None,
        }
        allowed, error_message = _computer_use_action_guard(
            action_type=effective_action,
            target=target,
            tool_call_id=tool_call_id,
        )
        if not allowed:
            return False, error_message or f"Safety Guardian 已阻止 computer use 步骤：{action}"
    return True, None


def _computer_use_cache_app_resolution(query: str | None, resolved_app: dict[str, Any] | None) -> None:
    normalized = str(query or "").strip().lower()
    if not normalized or not isinstance(resolved_app, dict) or not resolved_app:
        return
    _COMPUTER_USE_APP_RESOLUTION_CACHE[normalized] = (time.monotonic(), dict(resolved_app))


def _computer_use_resolve_app(
    app: str | None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    query = str(app or "").strip()
    if not query:
        return None
    normalized = query.lower()
    cached_entry = _COMPUTER_USE_APP_RESOLUTION_CACHE.get(normalized)
    if (
        not force_refresh
        and isinstance(cached_entry, tuple)
        and len(cached_entry) == 2
        and (time.monotonic() - float(cached_entry[0])) * 1000 <= _COMPUTER_USE_APP_RESOLUTION_CACHE_TTL_MS
    ):
        return dict(cached_entry[1])
    payload = _get_computer_use_runtime().list_apps(
        query=query,
        limit=5,
        include_running=True,
        force_refresh=bool(force_refresh),
    )
    apps = list(payload.get("apps") or [])
    if not apps:
        return None

    def _score(entry: dict[str, Any]) -> tuple[int, int]:
        score = 0
        app_id = str(entry.get("appId") or "").strip().lower()
        display_name = str(entry.get("displayName") or "").strip().lower()
        aliases = [str(item).strip().lower() for item in list(entry.get("aliases") or []) if str(item).strip()]
        titles = [str(item).strip().lower() for item in list(entry.get("titlePatterns") or []) if str(item).strip()]
        process_names = [str(item).strip().lower() for item in list(entry.get("processNames") or []) if str(item).strip()]
        haystack = [app_id, display_name, *aliases, *titles, *process_names]
        if normalized == app_id:
            score += 180
        if normalized == display_name:
            score += 160
        if normalized in aliases:
            score += 120
        if normalized in titles:
            score += 90
        if any(normalized and normalized in item for item in haystack):
            score += 36
        if bool(entry.get("isRunning")):
            score += 12
        if bool(entry.get("profileBound")):
            score += 6
        return score, int(bool(entry.get("launchable")))

    ranked = sorted(
        (dict(entry) for entry in apps if isinstance(entry, dict)),
        key=lambda item: _score(item),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    _computer_use_cache_app_resolution(query, best)
    return best


def _computer_use_running_window_title(resolved_app: dict[str, Any] | None) -> str | None:
    if not isinstance(resolved_app, dict):
        return None
    windows = list(resolved_app.get("runningWindows") or [])
    for item in windows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            return title
    return None


def _computer_use_effective_window_title(
    explicit_title: str | None,
    resolved_app: dict[str, Any] | None,
) -> str | None:
    normalized = str(explicit_title or "").strip()
    if normalized:
        return normalized
    return _computer_use_running_window_title(resolved_app)


def _computer_use_refresh_resolved_app_window(
    *,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    app_id: str | None = None,
    window_title: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(resolved_app, dict) or not resolved_app:
        return resolved_app
    refreshed = dict(resolved_app)
    normalized_title = str(window_title or "").strip()
    normalized_app_id = str(app_id or "").strip()
    if normalized_app_id and not str(refreshed.get("appId") or "").strip():
        refreshed["appId"] = normalized_app_id
    if normalized_title:
        refreshed["runningWindows"] = [{"title": normalized_title}]
    _computer_use_cache_app_resolution(app_query, refreshed)
    return refreshed


def _computer_use_prebind_window(
    *,
    action_name: str,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    window_title: str | None,
    window_handle: int | None = None,
    class_name: str | None = None,
    target_path: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, int | None, str | None]:
    normalized_title = str(window_title or "").strip() or None
    normalized_class = str(class_name or "").strip() or None
    normalized_target_path = str(target_path or "").strip() or None
    if not app_query and not normalized_title and not normalized_class and window_handle in (None, ""):
        return resolved_app, normalized_title, window_handle, None
    try:
        raw_result = _get_computer_use_runtime().focus_window(
            **_computer_use_runtime_kwargs(f"{action_name}:{app_query or normalized_title or normalized_class or 'desktop'}"),
            app_id=(resolved_app or {}).get("appId"),
            target_path=normalized_target_path,
            window_title=normalized_title,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            class_name=normalized_class,
            require_visual_guard=False,
            prefer_fast_path=True,
            post_action_settle_timeout_ms=220,
            post_action_settle_poll_ms=120,
            post_action_stable_rounds=1,
        )
    except Exception as exc:
        return resolved_app, normalized_title, window_handle, f"Error ensuring desktop window: {exc}"
    updated_app = _computer_use_update_resolved_app_from_raw_result(
        app_query=app_query,
        resolved_app=resolved_app,
        raw_result=raw_result,
    )
    result = dict(raw_result.get("result") or {})
    observation = dict(result.get("observation") or {})
    target = dict(result.get("target") or {})
    metadata = dict(observation.get("metadata") or {})
    bound_title = (
        str(target.get("windowTitle") or "").strip()
        or str(observation.get("windowTitle") or "").strip()
        or normalized_title
    )
    bound_handle = (
        target.get("windowHandle")
        or target.get("handle")
        or metadata.get("windowHandle")
        or window_handle
    )
    if bound_handle not in (None, "") or bound_title:
        try:
            _get_computer_use_runtime().driver.focus_window(
                window_title=bound_title or None,
                window_handle=int(bound_handle) if bound_handle not in (None, "") else None,
            )
        except Exception:
            pass
    return updated_app, bound_title or None, int(bound_handle) if bound_handle not in (None, "") else None, None


def _computer_use_update_resolved_app_from_raw_result(
    *,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    raw_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = dict(raw_result or {})
    result = dict(payload.get("result") or {})
    target = dict(result.get("target") or {})
    observation = dict(result.get("observation") or payload.get("observation") or {})
    metadata = dict(observation.get("metadata") or {})
    window_title = (
        str(target.get("windowTitle") or "").strip()
        or str(observation.get("windowTitle") or "").strip()
    )
    app_id = (
        str(target.get("appId") or "").strip()
        or str(metadata.get("appId") or "").strip()
    )
    return _computer_use_refresh_resolved_app_window(
        app_query=app_query,
        resolved_app=resolved_app,
        app_id=app_id or None,
        window_title=window_title or None,
    )


def _computer_use_launch_target_override(
    *,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    target: str | None,
) -> dict[str, Any]:
    normalized_target = str(target or "").strip()
    if not normalized_target:
        return {}
    resolved_app_id = str((resolved_app or {}).get("appId") or "").strip().lower()
    normalized_app_query = str(app_query or "").strip().lower()
    if resolved_app_id != "explorer" and normalized_app_query not in {"explorer", "文件资源管理器", "file explorer"}:
        return {}
    target_path = Path(normalized_target).expanduser()
    try:
        target_path = target_path.resolve(strict=False)
    except Exception:
        target_path = target_path.absolute()
    if not target_path.exists() or not target_path.is_dir():
        return {}
    expected_window_title = str(target_path.name or target_path.drive or target_path).strip()
    quoted_target = str(target_path)
    return {
        "command": f'explorer.exe /e,/root,"{quoted_target}"',
        "expected_window_title": expected_window_title,
        "strict_expected_window_title": True,
        "resolved_target_path": str(target_path),
    }


def _computer_use_apply_visual_locator_step(
    step: dict[str, Any],
    *,
    visual_locator: str | None = None,
    visual_locator_scope: str | None = None,
    visual_locator_scope_padding: list[int] | None = None,
    visual_locator_scope_seed_strategy: str | None = None,
    visual_locator_confidence: float | None = None,
    visual_locator_timeout_ms: int | None = None,
    visual_locator_read_text: bool | None = None,
    visual_locator_multiple: bool | None = None,
    prefix: str = "",
) -> None:
    locator = str(visual_locator or "").strip()
    if not locator:
        return
    snake_prefix = str(prefix or "")
    step[f"{snake_prefix}visual_locator"] = locator
    scope_locator = str(visual_locator_scope or "").strip()
    if scope_locator:
        step[f"{snake_prefix}visual_locator_scope"] = scope_locator
    if isinstance(visual_locator_scope_padding, list) and len(visual_locator_scope_padding) == 4:
        step[f"{snake_prefix}visual_locator_scope_padding"] = [int(v) for v in visual_locator_scope_padding]
    scope_seed_strategy = str(visual_locator_scope_seed_strategy or "").strip()
    if scope_seed_strategy:
        step[f"{snake_prefix}visual_locator_scope_seed_strategy"] = scope_seed_strategy
    if visual_locator_confidence not in (None, ""):
        step[f"{snake_prefix}visual_locator_confidence"] = float(visual_locator_confidence)
    if visual_locator_timeout_ms not in (None, ""):
        step[f"{snake_prefix}visual_locator_timeout_ms"] = max(250, int(visual_locator_timeout_ms))
    if visual_locator_read_text is True:
        step[f"{snake_prefix}visual_locator_read_text"] = True
    if visual_locator_multiple is True:
        step[f"{snake_prefix}visual_locator_multiple"] = True


def _computer_use_apply_post_action_visual_check_step(
    step: dict[str, Any],
    *,
    post_action_visual_locator: str | None = None,
    post_action_visual_locator_confidence: float | None = None,
    post_action_visual_locator_timeout_ms: int | None = None,
    post_action_visual_locator_read_text: bool | None = None,
    post_action_visual_locator_multiple: bool | None = None,
    post_action_expect_text: str | list[str] | None = None,
) -> None:
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=post_action_visual_locator,
        visual_locator_confidence=post_action_visual_locator_confidence,
        visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        visual_locator_read_text=post_action_visual_locator_read_text,
        visual_locator_multiple=post_action_visual_locator_multiple,
        prefix="post_action_",
    )
    if isinstance(post_action_expect_text, list):
        normalized = [str(item).strip() for item in post_action_expect_text if str(item).strip()]
        if normalized:
            step["post_action_expect_texts"] = normalized
    else:
        token = str(post_action_expect_text or "").strip()
        if token:
            step["post_action_expect_text"] = token


def _computer_use_apply_environment_probe_step(
    step: dict[str, Any],
    *,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: str | None = None,
) -> None:
    if observe_notifications:
        step["observe_notifications"] = True
    if observe_sound:
        step["observe_sound"] = True
    mode = str(environment_probe_mode or "").strip().lower()
    if mode:
        step["environment_probe_mode"] = mode


def _computer_use_guard_failure_response(
    *,
    action: str,
    summary: str,
    app_hint: str | None = None,
    target_hint: str | None = None,
    resolved_app: dict[str, Any] | None = None,
    window_title: str | None = None,
) -> str:
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    return json.dumps(
        {
            "ok": False,
            "action": action,
            "status": "blocked",
            "blocked": True,
            "summary": summary,
            "blockedReason": summary,
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId"),
            },
            "target": {
                "requested": target_hint,
            },
            "window": {
                "title": effective_window_title,
                "handle": None,
                "appId": (resolved_app or {}).get("appId"),
                "profileId": (resolved_app or {}).get("profileId"),
                "focusedElementId": None,
            },
            "verification": {
                "passed": False,
                "status": "guardian_blocked",
                "reason": summary,
                "level": "review_required",
            },
            "scene": {
                "pageIdentity": None,
                "blockerState": "guardian_blocked",
                "transitionState": "blocked",
                "confidence": "high",
                "reasons": ["safety_guardian"],
            },
            "budget": {
                "withinBudget": True,
            },
            "executionMode": None,
            "learningLoop": None,
            "updateRequest": {
                "requested": True,
                "kind": "human_approval_required",
                "reason": summary,
            },
            "evidence": {
                "message": summary,
                "artifacts": [],
                "screenHash": None,
                "treeHash": None,
                "selectorStats": None,
                "stabilityWait": None,
                "focusedElementId": None,
            },
            "recommendedNextAction": "request_human_confirmation",
            "sessionId": None,
            "runId": None,
        },
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_artifacts_from_result(action_result: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    direct_artifact = action_result.get("artifact")
    if isinstance(direct_artifact, dict) and direct_artifact:
        artifacts.append(dict(direct_artifact))
    observation = action_result.get("observation")
    screenshot_artifact = ((observation or {}).get("screenshotArtifact") or {}) if isinstance(observation, dict) else {}
    if isinstance(screenshot_artifact, dict) and screenshot_artifact:
        artifacts.append(dict(screenshot_artifact))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in artifacts:
        normalized = dict(item or {})
        normalized.setdefault("filePath", item.get("sourcePath") or item.get("source_path") or item.get("path") or item.get("file_path"))
        normalized.setdefault("workspacePath", item.get("workspacePath") or item.get("workspace_path"))
        normalized.setdefault("previewUrl", item.get("previewUrl") or item.get("preview_url"))
        key = (
            str(normalized.get("artifactId") or ""),
            str(normalized.get("filePath") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _computer_use_primary_action(raw_result: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(raw_result, dict):
        return None, None
    if isinstance(raw_result.get("result"), dict):
        return raw_result, None
    steps = list(raw_result.get("steps") or [])
    if not steps:
        return None, None
    primary_step = next((item for item in reversed(steps) if isinstance(item, dict)), None)
    if not isinstance(primary_step, dict):
        return None, None
    inner_result = primary_step.get("result")
    if isinstance(inner_result, dict) and isinstance(inner_result.get("result"), dict):
        return inner_result, primary_step
    return None, primary_step


def _computer_use_recommended_next_action(
    *,
    action_result: dict[str, Any],
    verification: dict[str, Any],
    update_request: dict[str, Any] | None,
) -> str:
    recommended = recommended_next_action_payload(
        action_type=str(action_result.get("actionType") or action_result.get("action_type") or "").strip(),
        status=str(action_result.get("status") or "").strip(),
        verification=verification,
        scene=dict((action_result.get("metadata") or {}).get("scene") or {}),
        update_request=update_request,
    )
    if recommended == "ensure_window_then_retry":
        return "rebind_window_then_retry"
    return recommended


def _computer_use_compact_response(
    *,
    action: str,
    raw_result: dict[str, Any],
    app_hint: str | None = None,
    target_hint: str | None = None,
    resolved_app: dict[str, Any] | None = None,
    expected_window_title: str | None = None,
    strict_expected_window_title: bool = False,
) -> str:
    primary_result, primary_step = _computer_use_primary_action(raw_result)
    if not isinstance(primary_result, dict):
        return json.dumps(
            {
                "ok": False,
                "action": action,
                "status": "error",
                "blocked": False,
                "summary": "未能从 computer use 返回结果中解析出主动作结果。",
                "raw": raw_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    action_result = dict(primary_result.get("result") or {})
    verification = dict(action_result.get("verification") or {})
    metadata = dict(action_result.get("metadata") or {})
    observation = dict(action_result.get("observation") or {})
    target = dict(action_result.get("target") or {})
    scene = dict(metadata.get("scene") or {})
    budget = dict(metadata.get("budget") or {})
    update_request = dict(metadata.get("updateRequest") or {}) if isinstance(metadata.get("updateRequest"), dict) else None
    status = str(action_result.get("status") or "unknown").strip()
    verification_passed = bool(verification.get("passed"))
    blocked = status == "blocked" or bool(update_request and update_request.get("requested")) or str(verification.get("level") or "").strip().lower() == "review_required"
    ok = status == "completed" and verification_passed and not blocked
    window = {
        "title": observation.get("windowTitle") or target.get("windowTitle") or target.get("title"),
        "handle": observation.get("metadata", {}).get("windowHandle") if isinstance(observation.get("metadata"), dict) else None,
        "appId": target.get("appId") or target.get("profileId") or (resolved_app or {}).get("appId"),
        "profileId": target.get("profileId") or (resolved_app or {}).get("profileId"),
        "focusedElementId": observation.get("focusedElementId"),
    }
    if window["handle"] is None:
        window["handle"] = target.get("windowHandle") or target.get("handle")
    evidence = {
        "message": action_result.get("message"),
        "artifacts": _computer_use_artifacts_from_result(action_result),
        "screenHash": observation.get("screenHash"),
        "treeHash": observation.get("treeHash"),
        "selectorStats": metadata.get("selectorStats"),
        "stabilityWait": metadata.get("stabilityWait"),
        "focusedElementId": observation.get("focusedElementId"),
        "visualLocator": dict(metadata.get("visualLocator") or {}),
        "postActionVisualLocator": dict(metadata.get("postActionVisualLocator") or {}),
        "startVisualLocator": dict(metadata.get("startVisualLocator") or {}),
        "endVisualLocator": dict(metadata.get("endVisualLocator") or {}),
    }
    primary_visual_locator = dict(evidence.get("visualLocator") or {})
    evidence["visualObservation"] = dict(primary_visual_locator.get("visualObservation") or {})
    evidence["visualJudge"] = dict(primary_visual_locator.get("visualJudge") or {})
    evidence["visualSemanticCandidates"] = [
        dict(item or {})
        for item in list(primary_visual_locator.get("visualSemanticCandidates") or [])[:6]
        if isinstance(item, dict)
    ]
    if evidence["artifacts"]:
        primary_artifact = dict(evidence["artifacts"][0] or {})
        evidence["primaryArtifact"] = primary_artifact
        evidence["location"] = {
            "kind": "runtime_artifact",
            "artifactPath": primary_artifact.get("filePath"),
            "workspacePath": primary_artifact.get("workspacePath"),
            "previewUrl": primary_artifact.get("previewUrl"),
            "artifactRootHint": ".v8-agent-os/artifacts",
        }
    runtime_recommended = str(metadata.get("recommendedNextAction") or "").strip() or None
    visual_decision = dict(metadata.get("visualDecision") or {})
    if not visual_decision:
        visual_decision = {
            "role": evidence["visualObservation"].get("role"),
            "candidateCount": evidence["visualObservation"].get("candidateCount"),
            "ambiguityLevel": evidence["visualObservation"].get("ambiguityLevel"),
            "judgeDecision": evidence["visualJudge"].get("decision"),
            "judgeConfidence": evidence["visualJudge"].get("confidence"),
        }
    visual_signal_summary = dict(metadata.get("visualSignalSummary") or evidence.get("visualSignalSummary") or {})
    if not visual_signal_summary:
        visual_signal_summary = build_visual_signal_summary_payload(
            metadata=metadata,
            visual_decision=visual_decision,
            verification=verification,
            evidence_summary=evidence,
        )
    timing_signal_summary = dict(metadata.get("timingSignalSummary") or evidence.get("timingSignalSummary") or {})
    if not timing_signal_summary:
        timing_signal_summary = build_timing_signal_summary_payload(
            metadata=metadata,
            scene=scene,
            evidence_summary=evidence,
        )
    environment_signal_summary = dict(metadata.get("environmentSignalSummary") or evidence.get("environmentSignalSummary") or {})
    if not environment_signal_summary:
        environment_signal_summary = build_environment_signal_summary_payload(
            metadata=metadata,
            observation=observation,
            evidence_summary=evidence,
        )
    browser_automation = dict(observation.get("metadata", {}).get("browserAutomation") or metadata.get("browserAutomation") or {})
    if metadata.get("browserLaneProvider") and not browser_automation.get("provider"):
        browser_automation["provider"] = metadata.get("browserLaneProvider")
    if metadata.get("browserTargetFamily") and not browser_automation.get("family"):
        browser_automation["family"] = metadata.get("browserTargetFamily")
    if metadata.get("browserTargetId") and not browser_automation.get("targetId"):
        browser_automation["targetId"] = metadata.get("browserTargetId")
    if metadata.get("route") and not browser_automation.get("route"):
        browser_automation["route"] = metadata.get("route")
    browser_session_mode = str(browser_automation.get("profilePersistenceMode") or "").strip() or None
    if browser_session_mode:
        browser_automation["preservesLoginState"] = browser_session_mode in {
            "reused_existing_window",
            "attached_existing_debug_browser",
            "default_user_profile_launch",
        }
    response = {
        "ok": ok,
        "action": action,
        "status": status,
        "blocked": blocked,
        "summary": action_result.get("message") or verification.get("reason") or "",
        "blockedReason": metadata.get("blockedReason"),
        "app": {
            "requested": app_hint,
            "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
            "appId": (resolved_app or {}).get("appId") or target.get("appId") or target.get("profileId"),
            "controlClass": metadata.get("controlClass") or (resolved_app or {}).get("controlClass"),
            "appAdapterId": metadata.get("appAdapterId") or (resolved_app or {}).get("appAdapterId"),
            "launchSelectionReason": metadata.get("launchSelectionReason"),
            "launchCandidateSource": metadata.get("launchCandidateSource"),
            "launchCandidateRole": metadata.get("launchCandidateRole"),
            "launchCandidateScore": metadata.get("launchCandidateScore"),
            "restoreStrategy": metadata.get("restoreStrategy"),
            "spawnSuppressedByRestore": bool(metadata.get("spawnSuppressedByRestore")),
        },
        "target": {
            "requested": target_hint,
            "name": target.get("name") or target.get("automationId") or target.get("elementId"),
            "selectorKey": metadata.get("selectorKey") or metadata.get("profileSelectorKey"),
            "clickedPoint": target.get("clickedPoint"),
            "bounds": target.get("bounds"),
            "visualSemanticRole": evidence["visualObservation"].get("role"),
        },
        "window": window,
        "verification": verification,
        "scene": scene,
        "budget": budget,
        "executionMode": metadata.get("executionMode"),
        "learningLoop": metadata.get("learningLoop"),
        "updateRequest": update_request,
        "evidence": evidence,
        "visualDecision": visual_decision,
        "visualSignalSummary": visual_signal_summary,
        "timingSignalSummary": timing_signal_summary,
        "environmentSignalSummary": environment_signal_summary,
        "browserAutomation": browser_automation,
        "appAdapter": dict(metadata.get("appAdapter") or {}),
        "browserSession": {
            "mode": browser_session_mode,
            "preservesLoginState": bool(browser_automation.get("preservesLoginState")),
            "attachedExistingBrowser": bool(browser_automation.get("attachedExistingBrowser")),
            "reusedExistingBrowserWindow": bool(browser_automation.get("reusedExistingBrowserWindow")),
        } if browser_automation else None,
        "recommendedNextAction": runtime_recommended
        or _computer_use_recommended_next_action(
            action_result=action_result,
            verification=verification,
            update_request=update_request,
        ),
        "sessionId": primary_result.get("sessionId") or raw_result.get("sessionId"),
        "runId": primary_result.get("runId") or raw_result.get("runId"),
    }
    if action == "paste_files" and status == "completed":
        response["summary"] = "文件粘贴动作已执行。"
    if isinstance(primary_step, dict):
        response["planStep"] = {
            "index": primary_step.get("index"),
            "status": primary_step.get("status"),
            "attemptCount": primary_step.get("attemptCount"),
            "elapsedSeconds": primary_step.get("elapsedSeconds"),
        }
    normalized_expected_title = re.sub(r"\s+", "", str(expected_window_title or "").replace("\u200b", "").strip()).lower()
    normalized_actual_title = re.sub(r"\s+", "", str(window.get("title") or "").replace("\u200b", "").strip()).lower()
    if (
        strict_expected_window_title
        and normalized_expected_title
        and normalized_actual_title
        and normalized_expected_title not in normalized_actual_title
        and normalized_actual_title not in normalized_expected_title
    ):
        mismatch_reason = f"动作执行后窗口上下文漂移，期望窗口“{expected_window_title}”，实际窗口“{window.get('title')}”。"
        response["ok"] = False
        response["blocked"] = True
        response["status"] = "blocked"
        response["summary"] = mismatch_reason
        response["blockedReason"] = mismatch_reason
        response["verification"] = {
            **dict(response.get("verification") or {}),
            "passed": False,
            "status": "post_action_window_binding_mismatch",
            "reason": mismatch_reason,
            "level": "review_required",
        }
        response["scene"] = {
            **dict(response.get("scene") or {}),
            "transitionState": "blocked",
            "blockerState": "window_context_drift",
        }
        response["updateRequest"] = {
            "requested": True,
            "kind": "ui_update_request",
            "reason": mismatch_reason,
        }
        response["recommendedNextAction"] = "ensure_window_then_retry"
    return json.dumps(response, ensure_ascii=False, indent=2)


def _computer_use_compact_observation(
    *,
    raw_result: dict[str, Any],
    app_hint: str | None = None,
    resolved_app: dict[str, Any] | None = None,
) -> str:
    observation = dict(raw_result.get("observation") or {})
    metadata = dict(observation.get("metadata") or {})
    scene_assessment = dict(observation.get("sceneAssessment") or metadata.get("sceneAssessment") or {})
    binding_assessment = dict(observation.get("bindingAssessment") or metadata.get("bindingAssessment") or {})
    environment_signal_summary = build_environment_signal_summary_payload(
        metadata={**metadata, "sceneAssessment": scene_assessment, "bindingAssessment": binding_assessment},
        observation=observation,
        evidence_summary={
            "sceneAssessment": scene_assessment,
            "bindingAssessment": binding_assessment,
        },
    )
    elements = []
    for item in list(observation.get("elements") or [])[:40]:
        if not isinstance(item, dict):
            continue
        elements.append(
            {
                "elementId": item.get("elementId"),
                "role": item.get("role"),
                "name": item.get("name"),
                "automationId": item.get("automationId"),
                "className": item.get("className"),
                "actions": list(item.get("actions") or []),
                "bounds": item.get("bounds"),
                "confidence": item.get("confidence"),
            }
        )
    payload = {
        "ok": True,
        "action": "observe_scene",
        "summary": "已完成当前窗口观察。",
        "app": {
            "requested": app_hint,
            "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
            "appId": (resolved_app or {}).get("appId") or observation.get("metadata", {}).get("appId"),
            "controlClass": observation.get("metadata", {}).get("controlClass") or (resolved_app or {}).get("controlClass"),
            "appAdapterId": observation.get("metadata", {}).get("appAdapterId") or (resolved_app or {}).get("appAdapterId"),
        },
        "window": {
            "title": observation.get("windowTitle"),
            "handle": metadata.get("windowHandle"),
            "focusedElementId": observation.get("focusedElementId"),
        },
        "scene": {
            "pageIdentity": scene_assessment.get("pageIdentity") or metadata.get("pageIdentity"),
            "blockerState": scene_assessment.get("blockerState") or "none",
            "transitionState": scene_assessment.get("transitionState") or "observed",
            "confidence": scene_assessment.get("confidence") or metadata.get("pageIdentityConfidence") or "low",
            "reasons": list(scene_assessment.get("reasons") or []),
            "screenHash": observation.get("screenHash"),
            "treeHash": observation.get("treeHash"),
            "elementCount": len(list(observation.get("elements") or [])),
        },
        "bindingAssessment": {
            "status": binding_assessment.get("status"),
            "confidence": binding_assessment.get("confidence"),
            "score": binding_assessment.get("score"),
            "strictBindingRequired": bool(binding_assessment.get("strictBindingRequired")),
            "requiresUpdateRequest": bool(binding_assessment.get("requiresUpdateRequest")),
            "matches": dict(binding_assessment.get("matches") or {}),
            "reasons": list(binding_assessment.get("reasons") or []),
        },
        "environmentSignalSummary": environment_signal_summary,
        "browserAutomation": dict(metadata.get("browserAutomation") or {}),
        "appAdapter": dict(metadata.get("appAdapter") or {}),
        "elements": elements,
        "screenshotArtifact": observation.get("screenshotArtifact"),
        "sessionId": raw_result.get("sessionId"),
        "runId": raw_result.get("runId"),
    }
    browser_automation = dict(payload.get("browserAutomation") or {})
    browser_session_mode = str(browser_automation.get("profilePersistenceMode") or "").strip() or None
    if browser_session_mode:
        browser_automation["preservesLoginState"] = browser_session_mode in {
            "reused_existing_window",
            "attached_existing_debug_browser",
            "default_user_profile_launch",
        }
        payload["browserAutomation"] = browser_automation
    payload["browserSession"] = {
        "mode": browser_session_mode,
        "preservesLoginState": bool(browser_automation.get("preservesLoginState")),
        "attachedExistingBrowser": bool(browser_automation.get("attachedExistingBrowser")),
        "reusedExistingBrowserWindow": bool(browser_automation.get("reusedExistingBrowserWindow")),
    } if browser_automation else None
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _computer_use_parse_variables_json(variables_json: str | None) -> Dict[str, Any]:
    if variables_json in (None, ""):
        return {}
    payload = json.loads(str(variables_json))
    if not isinstance(payload, dict):
        raise ValueError("variables_json 必须是 JSON 对象。")
    return {str(key).strip(): value for key, value in payload.items() if str(key).strip()}


def _computer_use_compact_memory_lookup(
    *,
    goal: str,
    app_hint: str | None,
    resolved_app: dict[str, Any] | None,
    route: dict[str, Any],
) -> str:
    recommended_match = dict(route.get("recommendedMatch") or {})
    response = build_compact_execution_route(
        action="lookup_muscle_memory",
        goal=goal,
        app_hint=app_hint,
        target_hint=None,
        resolved_app=resolved_app,
        route=route,
    )
    if recommended_match:
        response["recommendedToolSummary"] = (
            response.get("recommendedToolSummary")
            or "已找到可复用肌肉记忆，请先按推荐执行路径路由，而不是直接进入学习模式。"
        )
    return json.dumps(response, ensure_ascii=False, indent=2)


def _computer_use_plan_step_contract(step_payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(step_payload, dict):
        return None
    nested_result = dict(step_payload.get("result") or {})
    action_result = dict(nested_result.get("result") or {})
    if not action_result:
        return None
    metadata = dict(action_result.get("metadata") or {})
    observation = dict(action_result.get("observation") or {})
    verification = dict(action_result.get("verification") or {})
    scene = dict(metadata.get("scene") or {})
    update_request = dict(metadata.get("updateRequest") or {}) if isinstance(metadata.get("updateRequest"), dict) else None
    evidence_summary = dict(metadata.get("evidenceSummary") or {})
    if not evidence_summary:
        evidence_summary = build_evidence_summary_payload(
            message=action_result.get("message"),
            observation=observation,
            metadata=metadata,
            artifact=action_result.get("artifact"),
        )
    visual_decision = dict(metadata.get("visualDecision") or evidence_summary.get("visualDecision") or {})
    visual_signal_summary = dict(metadata.get("visualSignalSummary") or {})
    if not visual_signal_summary:
        visual_signal_summary = build_visual_signal_summary_payload(
            metadata=metadata,
            visual_decision=visual_decision,
            verification=verification,
            evidence_summary=evidence_summary,
        )
    timing_signal_summary = dict(metadata.get("timingSignalSummary") or {})
    if not timing_signal_summary:
        timing_signal_summary = build_timing_signal_summary_payload(
            metadata=metadata,
            scene=scene,
            evidence_summary=evidence_summary,
        )
    environment_signal_summary = dict(metadata.get("environmentSignalSummary") or {})
    if not environment_signal_summary:
        environment_signal_summary = build_environment_signal_summary_payload(
            metadata=metadata,
            observation=observation,
            evidence_summary=evidence_summary,
        )
    action_type = str(action_result.get("actionType") or action_result.get("action_type") or step_payload.get("action") or "").strip()
    return {
        "index": step_payload.get("index"),
        "action": str(step_payload.get("action") or action_type or "").strip() or None,
        "status": str(step_payload.get("status") or action_result.get("status") or "").strip() or None,
        "attemptCount": int(step_payload.get("attemptCount") or 0),
        "elapsedSeconds": step_payload.get("elapsedSeconds"),
        "summary": action_result.get("message") or verification.get("reason") or "",
        "blockedReason": metadata.get("blockedReason"),
        "recommendedNextAction": str(metadata.get("recommendedNextAction") or "").strip()
        or recommended_next_action_payload(
            action_type=action_type,
            status=str(action_result.get("status") or step_payload.get("status") or ""),
            verification=verification,
            scene=scene,
            update_request=update_request,
        ),
        "verification": verification,
        "scene": scene,
        "visualSignalSummary": _compact_visual_signal_summary(visual_signal_summary),
        "timingSignalSummary": _compact_timing_signal_summary(timing_signal_summary),
        "environmentSignalSummary": _compact_environment_signal_summary(
            draft_environment_signal_summary(metadata=environment_signal_summary)
        ),
    }


def _computer_use_aggregate_plan_step_contracts(step_contracts: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = {
        "completed": 0,
        "blocked": 0,
        "update_requested": 0,
        "failed": 0,
        "other": 0,
    }
    visual_roles: list[str] = []
    visual_providers: list[str] = []
    timing_transition_states: list[str] = []
    timing_stability_statuses: list[str] = []
    environment_page_identities: list[str] = []
    environment_affordances: list[str] = []
    environment_transition_states: list[str] = []
    environment_blocker_states: list[str] = []
    environment_dialog_confidence_levels: list[str] = []
    environment_notification_providers: list[str] = []
    environment_sound_providers: list[str] = []
    notification_modes: list[str] = []
    sound_modes: list[str] = []

    visual_locator_backed_steps = 0
    verified_visual_locator_steps = 0
    visual_judge_steps = 0
    visual_judge_selected_steps = 0
    wait_sensitive_steps = 0
    loading_sensitive_steps = 0
    stability_wait_observed_steps = 0
    stability_wait_timeout_steps = 0
    budget_exceeded_steps = 0
    time_budget_exceeded_steps = 0
    max_settle_budget_ms = 0
    max_elapsed_ms = 0
    max_post_action_settle_timeout_ms = 0
    max_post_action_settle_poll_ms = 0
    max_post_action_stable_rounds = 0
    ambient_observation_backed_steps = 0
    window_binding_verified_steps = 0
    dialog_aware_steps = 0
    focus_aware_steps = 0
    blocking_aware_steps = 0
    transition_aware_steps = 0
    notification_observed_steps = 0
    max_notification_candidate_count = 0
    sound_observed_steps = 0
    max_sound_active_session_count = 0
    notification_sensing_requested_count = 0
    notification_sensing_available_count = 0
    sound_sensing_requested_count = 0
    sound_sensing_available_count = 0
    notification_sensing_requested = False
    notification_sensing_available = False
    sound_sensing_requested = False
    sound_sensing_available = False

    def _extend_unique(target: list[str], values: list[Any]) -> None:
        for item in values:
            text = str(item or "").strip()
            if text and text not in target:
                target.append(text)

    for step_contract in step_contracts:
        status = str(step_contract.get("status") or "").strip().lower()
        if status in status_counts:
            status_counts[status] += 1
        elif status:
            status_counts["other"] += 1

        visual_signal_summary = dict(step_contract.get("visualSignalSummary") or {})
        if bool(visual_signal_summary.get("visualLocatorBacked")):
            visual_locator_backed_steps += int(visual_signal_summary.get("visualLocatorBackedSteps") or 0) or 1
        verified_visual_locator_steps += int(visual_signal_summary.get("verifiedVisualLocatorSteps") or 0)
        if bool(visual_signal_summary.get("visualJudgeBacked")):
            visual_judge_steps += int(visual_signal_summary.get("visualJudgeSteps") or 0) or 1
        visual_judge_selected_steps += int(visual_signal_summary.get("visualJudgeSelectedSteps") or 0)
        _extend_unique(visual_roles, list(visual_signal_summary.get("visualSemanticRoles") or []))
        _extend_unique(visual_providers, list(visual_signal_summary.get("visualLocatorProviders") or []))

        timing_signal_summary = dict(step_contract.get("timingSignalSummary") or {})
        if bool(timing_signal_summary.get("waitSensitive")):
            wait_sensitive_steps += int(timing_signal_summary.get("waitSensitiveSteps") or 0) or 1
        loading_sensitive_steps += int(timing_signal_summary.get("loadingSensitiveSteps") or 0)
        stability_wait_observed_steps += int(timing_signal_summary.get("stabilityWaitObservedSteps") or 0)
        stability_wait_timeout_steps += int(timing_signal_summary.get("stabilityWaitTimeoutSteps") or 0)
        budget_exceeded_steps += int(timing_signal_summary.get("budgetExceededSteps") or 0)
        time_budget_exceeded_steps += int(timing_signal_summary.get("timeBudgetExceededSteps") or 0)
        max_settle_budget_ms = max(max_settle_budget_ms, int(timing_signal_summary.get("maxSettleBudgetMs") or 0))
        max_elapsed_ms = max(max_elapsed_ms, int(timing_signal_summary.get("maxElapsedMs") or 0))
        max_post_action_settle_timeout_ms = max(
            max_post_action_settle_timeout_ms,
            int(timing_signal_summary.get("maxPostActionSettleTimeoutMs") or 0),
        )
        max_post_action_settle_poll_ms = max(
            max_post_action_settle_poll_ms,
            int(timing_signal_summary.get("maxPostActionSettlePollMs") or 0),
        )
        max_post_action_stable_rounds = max(
            max_post_action_stable_rounds,
            int(timing_signal_summary.get("maxPostActionStableRounds") or 0),
        )
        _extend_unique(timing_transition_states, list(timing_signal_summary.get("transitionStates") or []))
        _extend_unique(timing_stability_statuses, list(timing_signal_summary.get("stabilityWaitStatuses") or []))

        environment_signal_summary = dict(step_contract.get("environmentSignalSummary") or {})
        if bool(environment_signal_summary.get("desktopEnvironmentAware")):
            ambient_observation_backed_steps += int(environment_signal_summary.get("ambientObservationBackedSteps") or 0) or 1
        window_binding_verified_steps += int(environment_signal_summary.get("windowBindingVerifiedSteps") or 0)
        dialog_aware_steps += int(environment_signal_summary.get("dialogAwareSteps") or 0)
        focus_aware_steps += int(environment_signal_summary.get("focusAwareSteps") or 0)
        blocking_aware_steps += int(environment_signal_summary.get("blockingAwareSteps") or 0)
        transition_aware_steps += int(environment_signal_summary.get("transitionAwareSteps") or 0)
        _extend_unique(environment_page_identities, list(environment_signal_summary.get("pageIdentities") or []))
        _extend_unique(environment_affordances, list(environment_signal_summary.get("affordances") or []))
        _extend_unique(environment_transition_states, list(environment_signal_summary.get("transitionStates") or []))
        _extend_unique(environment_blocker_states, list(environment_signal_summary.get("blockerStates") or []))
        _extend_unique(
            environment_dialog_confidence_levels,
            list(environment_signal_summary.get("dialogConfidenceLevels") or []),
        )
        notification_sensing_requested = notification_sensing_requested or bool(
            environment_signal_summary.get("notificationSensingRequested")
        )
        notification_sensing_available = notification_sensing_available or bool(
            environment_signal_summary.get("notificationSensingAvailable")
        )
        sound_sensing_requested = sound_sensing_requested or bool(environment_signal_summary.get("soundSensingRequested"))
        sound_sensing_available = sound_sensing_available or bool(environment_signal_summary.get("soundSensingAvailable"))
        notification_sensing_requested_count += 1 if bool(
            environment_signal_summary.get("notificationSensingRequested")
        ) else 0
        notification_sensing_available_count += 1 if bool(
            environment_signal_summary.get("notificationSensingAvailable")
        ) else 0
        sound_sensing_requested_count += 1 if bool(environment_signal_summary.get("soundSensingRequested")) else 0
        sound_sensing_available_count += 1 if bool(environment_signal_summary.get("soundSensingAvailable")) else 0
        _extend_unique(
            notification_modes,
            [
                environment_signal_summary.get("notificationSensingMode"),
                *(list(environment_signal_summary.get("notificationSensingModes") or [])),
            ],
        )
        _extend_unique(
            sound_modes,
            [
                environment_signal_summary.get("soundSensingMode"),
                *(list(environment_signal_summary.get("soundSensingModes") or [])),
            ],
        )
        _extend_unique(
            environment_notification_providers,
            list(environment_signal_summary.get("notificationSignalProviders") or []),
        )
        _extend_unique(environment_sound_providers, list(environment_signal_summary.get("soundSignalProviders") or []))
        notification_observed_steps += int(environment_signal_summary.get("notificationObservedSteps") or 0)
        max_notification_candidate_count = max(
            max_notification_candidate_count,
            int(environment_signal_summary.get("maxNotificationCandidateCount") or 0),
        )
        sound_observed_steps += int(environment_signal_summary.get("soundObservedSteps") or 0)
        max_sound_active_session_count = max(
            max_sound_active_session_count,
            int(environment_signal_summary.get("maxSoundActiveSessionCount") or 0),
        )

    visual_signal_summary = _compact_visual_signal_summary(
        {
            "visualLocatorBacked": visual_locator_backed_steps > 0,
            "visualLocatorBackedSteps": visual_locator_backed_steps,
            "verifiedVisualLocatorSteps": verified_visual_locator_steps,
            "visualJudgeBacked": visual_judge_steps > 0,
            "visualJudgeSteps": visual_judge_steps,
            "visualJudgeSelectedSteps": visual_judge_selected_steps,
            "visualSemanticRoles": visual_roles,
            "visualLocatorProviders": visual_providers,
        }
    )
    timing_signal_summary = _compact_timing_signal_summary(
        {
            "waitSensitive": wait_sensitive_steps > 0,
            "waitSensitiveSteps": wait_sensitive_steps,
            "loadingSensitiveSteps": loading_sensitive_steps,
            "transitionStates": timing_transition_states,
            "stabilityWaitObservedSteps": stability_wait_observed_steps,
            "stabilityWaitTimeoutSteps": stability_wait_timeout_steps,
            "stabilityWaitStatuses": timing_stability_statuses,
            "budgetExceededSteps": budget_exceeded_steps,
            "timeBudgetExceededSteps": time_budget_exceeded_steps,
            "maxSettleBudgetMs": max_settle_budget_ms,
            "maxElapsedMs": max_elapsed_ms,
            "maxPostActionSettleTimeoutMs": max_post_action_settle_timeout_ms,
            "maxPostActionSettlePollMs": max_post_action_settle_poll_ms,
            "maxPostActionStableRounds": max_post_action_stable_rounds,
        }
    )
    environment_signal_summary = _compact_environment_signal_summary(
        {
            "desktopEnvironmentAware": ambient_observation_backed_steps > 0
            or window_binding_verified_steps > 0
            or dialog_aware_steps > 0
            or focus_aware_steps > 0
            or blocking_aware_steps > 0
            or transition_aware_steps > 0
            or notification_sensing_requested
            or sound_sensing_requested
            or notification_sensing_available
            or sound_sensing_available,
            "ambientObservationBackedSteps": ambient_observation_backed_steps,
            "windowBindingVerifiedSteps": window_binding_verified_steps,
            "dialogAwareSteps": dialog_aware_steps,
            "focusAwareSteps": focus_aware_steps,
            "blockingAwareSteps": blocking_aware_steps,
            "transitionAwareSteps": transition_aware_steps,
            "pageIdentities": environment_page_identities,
            "affordances": environment_affordances,
            "transitionStates": environment_transition_states,
            "blockerStates": environment_blocker_states,
            "dialogConfidenceLevels": environment_dialog_confidence_levels,
            "notificationSensingRequested": notification_sensing_requested,
            "notificationSensingAvailable": notification_sensing_available,
            "notificationSensingRequestedCount": notification_sensing_requested_count,
            "notificationSensingAvailableCount": notification_sensing_available_count,
            "notificationSensingMode": notification_modes[0] if len(notification_modes) == 1 else ("mixed" if notification_modes else None),
            "notificationSensingModes": notification_modes,
            "notificationSignalProviders": environment_notification_providers,
            "notificationObserved": notification_observed_steps > 0,
            "notificationObservedSteps": notification_observed_steps,
            "notificationCandidateCount": max_notification_candidate_count,
            "maxNotificationCandidateCount": max_notification_candidate_count,
            "soundSensingRequested": sound_sensing_requested,
            "soundSensingAvailable": sound_sensing_available,
            "soundSensingRequestedCount": sound_sensing_requested_count,
            "soundSensingAvailableCount": sound_sensing_available_count,
            "soundSensingMode": sound_modes[0] if len(sound_modes) == 1 else ("mixed" if sound_modes else None),
            "soundSensingModes": sound_modes,
            "soundSignalProviders": environment_sound_providers,
            "soundObserved": sound_observed_steps > 0,
            "soundObservedSteps": sound_observed_steps,
            "soundActiveSessionCount": max_sound_active_session_count,
            "maxSoundActiveSessionCount": max_sound_active_session_count,
        }
    )
    execution_summary = {
        "ok": status_counts["blocked"] == 0 and status_counts["update_requested"] == 0 and status_counts["failed"] == 0,
        "totalSteps": len(step_contracts),
        "completedSteps": status_counts["completed"],
        "blockedSteps": status_counts["blocked"],
        "updateRequestedSteps": status_counts["update_requested"],
        "failedSteps": status_counts["failed"],
        "otherSteps": status_counts["other"],
    }
    return {
        "executionSummary": execution_summary,
        "visualSignalSummary": visual_signal_summary,
        "timingSignalSummary": timing_signal_summary,
        "environmentSignalSummary": environment_signal_summary,
        "steps": step_contracts[:5],
    }


def _computer_use_attach_plan_contract_summary(
    *,
    payload: dict[str, Any],
    action: str,
    goal: str | None = None,
) -> dict[str, Any]:
    execution_payload = dict(payload.get("execution") or payload)
    step_contracts = [
        item
        for item in (
            _computer_use_plan_step_contract(dict(step or {}))
            for step in list(execution_payload.get("steps") or [])
            if isinstance(step, dict)
        )
        if isinstance(item, dict)
    ]
    contract_summary = _computer_use_aggregate_plan_step_contracts(step_contracts)
    execution_summary = dict(contract_summary.get("executionSummary") or {})
    payload["ok"] = bool(execution_summary.get("ok"))
    payload["action"] = action
    if goal:
        payload["goal"] = goal
    payload["executionSummary"] = execution_summary
    payload["visualSignalSummary"] = dict(contract_summary.get("visualSignalSummary") or {})
    payload["timingSignalSummary"] = dict(contract_summary.get("timingSignalSummary") or {})
    payload["environmentSignalSummary"] = dict(contract_summary.get("environmentSignalSummary") or {})
    payload["contractSummary"] = contract_summary
    return payload


def _computer_use_execute_task_step_samples(step_contracts: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for step in list(step_contracts or [])[: max(1, limit)]:
        if not isinstance(step, dict):
            continue
        samples.append(
            {
                "index": step.get("index"),
                "action": step.get("action"),
                "status": step.get("status"),
                "summary": step.get("summary"),
                "recommendedNextAction": step.get("recommendedNextAction"),
            }
        )
    return samples


def _computer_use_execute_task_next_action(
    *,
    ok: bool,
    requires_human_attention: bool,
    step_contracts: list[dict[str, Any]] | None = None,
) -> str:
    for step in list(step_contracts or []):
        if not isinstance(step, dict):
            continue
        recommended = str(step.get("recommendedNextAction") or "").strip()
        if recommended and str(step.get("status") or "").strip().lower() != "completed":
            return recommended
    if ok:
        return "observe_scene_verify"
    if requires_human_attention:
        return "request_human_attention"
    return "resolve_route_then_retry"


def _computer_use_execute_task_compact_computer_use_result(
    *,
    payload: dict[str, Any],
    execution_ready_mode: str,
    goal: str,
    app_hint: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    success_criteria: str | None,
) -> dict[str, Any]:
    execution_summary = dict(payload.get("executionSummary") or {})
    contract_summary = dict(payload.get("contractSummary") or {})
    step_contracts = [
        dict(item)
        for item in list(contract_summary.get("steps") or [])
        if isinstance(item, dict)
    ]
    ok = bool(payload.get("ok"))
    blocked_steps = int(execution_summary.get("blockedSteps") or 0)
    update_requested_steps = int(execution_summary.get("updateRequestedSteps") or 0)
    failed_steps = int(execution_summary.get("failedSteps") or 0)
    requires_human_attention = blocked_steps > 0 or update_requested_steps > 0
    requires_retry = not ok and not requires_human_attention
    summary = "已通过 ComputerUseRuntime 任务执行链完成桌面任务。"
    if not ok:
        summary = "ComputerUseRuntime 已完成本轮任务尝试，但当前结果仍需复查或重试。"
    return {
        "ok": ok,
        "executionReadyMode": execution_ready_mode,
        "executedBy": "computer_use",
        "summary": summary,
        "verification": {
            "passed": ok,
            "status": "completed" if ok else ("review_required" if requires_human_attention else "retry_required"),
            "successCriteria": str(success_criteria or "").strip() or None,
            "executionSummary": execution_summary,
            "visualSignalSummary": dict(payload.get("visualSignalSummary") or {}),
            "timingSignalSummary": dict(payload.get("timingSignalSummary") or {}),
            "environmentSignalSummary": dict(payload.get("environmentSignalSummary") or {}),
        },
        "evidence": {
            "goal": goal,
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId"),
            },
            "target": target_hint,
            "stepSamples": _computer_use_execute_task_step_samples(step_contracts),
        },
        "recommendedNextAction": _computer_use_execute_task_next_action(
            ok=ok,
            requires_human_attention=requires_human_attention,
            step_contracts=step_contracts,
        ),
        "requiresRetry": requires_retry,
        "requiresHumanAttention": requires_human_attention,
    }


def _computer_use_execute_task_compact_rpa_result(
    *,
    raw_result: dict[str, Any],
    execution_ready_mode: str,
    goal: str,
    app_hint: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    success_criteria: str | None,
) -> dict[str, Any]:
    status = str(raw_result.get("status") or "").strip() or "unknown"
    fallback = dict(raw_result.get("fallback") or {})
    template_policy = dict(raw_result.get("templateExecutionPolicy") or {})
    prepared = dict(raw_result.get("prepared") or {})
    script = dict(raw_result.get("script") or prepared.get("script") or {})
    review_required = status in {"review_required", "blocked"}
    ok = status in {"completed", "completed_with_fallback"}
    executed_by = "hybrid" if execution_ready_mode == "hybrid_mode" or status == "completed_with_fallback" else "rpa"
    summary = "已通过 RPA 复用链执行桌面任务。"
    if executed_by == "hybrid" and ok:
        summary = "已通过 RPA 骨架执行，并在需要时由 ComputerUseRuntime 做局部修补与恢复。"
    elif not ok and review_required:
        summary = "RPA 执行链返回了需要人工处理的结果。"
    elif not ok:
        summary = "RPA 执行链未满足当前任务目标，需要重新路由或重试。"
    return {
        "ok": ok,
        "executionReadyMode": execution_ready_mode,
        "executedBy": executed_by,
        "summary": summary,
        "verification": {
            "passed": ok,
            "status": status,
            "successCriteria": str(success_criteria or "").strip() or None,
            "reviewRequired": review_required,
            "templateExecutionPolicy": {
                "executionPath": template_policy.get("executionPath"),
                "requiresHumanReview": template_policy.get("requiresHumanReview"),
                "trustStatus": template_policy.get("trustStatus"),
            },
            "fallbackUsed": bool(fallback),
        },
        "evidence": {
            "goal": goal,
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId") or script.get("appId") or prepared.get("appId"),
            },
            "target": target_hint,
            "scriptId": raw_result.get("scriptId") or prepared.get("scriptId"),
            "scriptName": script.get("name") or prepared.get("scriptName"),
            "fallback": {
                "mode": fallback.get("mode"),
                "recoveredStepCount": fallback.get("recoveredStepCount"),
                "fallbackStepId": fallback.get("fallbackStepId"),
            } if fallback else None,
        },
        "recommendedNextAction": (
            "observe_scene_verify"
            if ok
            else ("request_human_attention" if review_required else "resolve_route_then_retry")
        ),
        "requiresRetry": not ok and not review_required,
        "requiresHumanAttention": review_required,
    }


def _computer_use_compact_memory_list(
    *,
    templates: list[dict[str, Any]],
    app_hint: str | None,
    resolved_app: dict[str, Any] | None,
    status: str | None,
) -> str:
    items = []
    visual_locator_backed = 0
    visual_judge_backed = 0
    wait_sensitive = 0
    loading_sensitive = 0
    environment_aware = 0
    dialog_aware = 0
    focus_aware = 0
    notification_aware = 0
    sound_aware = 0
    notification_sensing_requested_count = 0
    notification_sensing_available_count = 0
    sound_sensing_requested_count = 0
    sound_sensing_available_count = 0
    notification_observed = 0
    sound_observed = 0
    max_notification_candidate_count = 0
    max_sound_active_session_count = 0
    notification_signal_providers: list[str] = []
    sound_signal_providers: list[str] = []
    notification_sensing_modes: list[str] = []
    sound_sensing_modes: list[str] = []

    def _extend_unique(target: list[str], values: list[Any]) -> None:
        for item in values:
            normalized = str(item or "").strip()
            if normalized and normalized not in target:
                target.append(normalized)

    for item in templates:
        if not isinstance(item, dict):
            continue
        governance = dict(item.get("governance") or {})
        view = dict(item.get("view") or {})
        metadata = dict(item.get("metadata") or {})
        timing_signal_summary = dict(view.get("timingSignalSummary") or {})
        if not timing_signal_summary:
            timing_signal_summary = draft_timing_signal_summary(
                {"signals": dict(view.get("promotionGateSignals") or {})},
                metadata=metadata,
            )
        environment_signal_summary = dict(view.get("environmentSignalSummary") or {})
        if not environment_signal_summary:
            environment_signal_summary = draft_environment_signal_summary(
                {"signals": dict(view.get("promotionGateSignals") or {})},
                metadata=metadata,
            )
        if bool(dict(view.get("visualSignalSummary") or {}).get("visualLocatorBacked")):
            visual_locator_backed += 1
        if bool(dict(view.get("visualSignalSummary") or {}).get("visualJudgeBacked")):
            visual_judge_backed += 1
        if bool(timing_signal_summary.get("waitSensitive")):
            wait_sensitive += 1
        if int(timing_signal_summary.get("loadingSensitiveSteps") or 0) > 0:
            loading_sensitive += 1
        environment_aware = environment_aware + 1 if bool(environment_signal_summary.get("desktopEnvironmentAware")) else environment_aware
        dialog_aware = dialog_aware + 1 if int(environment_signal_summary.get("dialogAwareSteps") or 0) > 0 else dialog_aware
        focus_aware = focus_aware + 1 if int(environment_signal_summary.get("focusAwareSteps") or 0) > 0 else focus_aware
        notification_aware = notification_aware + 1 if (
            bool(environment_signal_summary.get("notificationSensingRequested"))
            or bool(environment_signal_summary.get("notificationSensingAvailable"))
        ) else notification_aware
        sound_aware = sound_aware + 1 if (
            bool(environment_signal_summary.get("soundSensingRequested"))
            or bool(environment_signal_summary.get("soundSensingAvailable"))
        ) else sound_aware
        notification_sensing_requested_count = notification_sensing_requested_count + 1 if bool(
            environment_signal_summary.get("notificationSensingRequested")
        ) else notification_sensing_requested_count
        notification_sensing_available_count = notification_sensing_available_count + 1 if bool(
            environment_signal_summary.get("notificationSensingAvailable")
        ) else notification_sensing_available_count
        sound_sensing_requested_count = sound_sensing_requested_count + 1 if bool(
            environment_signal_summary.get("soundSensingRequested")
        ) else sound_sensing_requested_count
        sound_sensing_available_count = sound_sensing_available_count + 1 if bool(
            environment_signal_summary.get("soundSensingAvailable")
        ) else sound_sensing_available_count
        notification_observed = notification_observed + 1 if int(
            environment_signal_summary.get("notificationObservedSteps") or 0
        ) > 0 else notification_observed
        sound_observed = sound_observed + 1 if int(
            environment_signal_summary.get("soundObservedSteps") or 0
        ) > 0 else sound_observed
        max_notification_candidate_count = max(
            max_notification_candidate_count,
            int(environment_signal_summary.get("maxNotificationCandidateCount") or 0),
        )
        max_sound_active_session_count = max(
            max_sound_active_session_count,
            int(environment_signal_summary.get("maxSoundActiveSessionCount") or 0),
        )
        _extend_unique(
            notification_signal_providers,
            list(environment_signal_summary.get("notificationSignalProviders") or []),
        )
        _extend_unique(
            sound_signal_providers,
            list(environment_signal_summary.get("soundSignalProviders") or []),
        )
        _extend_unique(
            notification_sensing_modes,
            [
                environment_signal_summary.get("notificationSensingMode"),
                *(list(environment_signal_summary.get("notificationSensingModes") or [])),
            ],
        )
        _extend_unique(
            sound_sensing_modes,
            [
                environment_signal_summary.get("soundSensingMode"),
                *(list(environment_signal_summary.get("soundSensingModes") or [])),
            ],
        )
        items.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "appId": item.get("appId"),
                "goal": item.get("goal"),
                "status": item.get("status"),
                "stage": governance.get("stage"),
                "rolloutMode": governance.get("rolloutMode"),
                "confidence": governance.get("confidence"),
                "reviewSummary": view.get("reviewSummary"),
                "riskFlags": list(view.get("riskFlags") or []),
                "visualSignalSummary": dict(view.get("visualSignalSummary") or {}),
                "timingSignalSummary": timing_signal_summary,
                "environmentSignalSummary": environment_signal_summary,
            }
        )
    return json.dumps(
        {
            "ok": True,
            "action": "list_muscle_memories",
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId"),
            },
            "status": status,
            "count": len(items),
            "templateSummary": {
                "total": len(items),
                "visualLocatorBackedCount": visual_locator_backed,
                "visualJudgeBackedCount": visual_judge_backed,
                "waitSensitiveCount": wait_sensitive,
                "loadingSensitiveCount": loading_sensitive,
                "environmentAwareCount": environment_aware,
                "dialogAwareCount": dialog_aware,
                "focusAwareCount": focus_aware,
                "notificationAwareCount": notification_aware,
                "soundAwareCount": sound_aware,
                "notificationSensingRequested": notification_sensing_requested_count > 0,
                "notificationSensingAvailable": notification_sensing_available_count > 0,
                "soundSensingRequested": sound_sensing_requested_count > 0,
                "soundSensingAvailable": sound_sensing_available_count > 0,
                "notificationSensingRequestedCount": notification_sensing_requested_count,
                "notificationSensingAvailableCount": notification_sensing_available_count,
                "soundSensingRequestedCount": sound_sensing_requested_count,
                "soundSensingAvailableCount": sound_sensing_available_count,
                "notificationObserved": notification_observed > 0,
                "soundObserved": sound_observed > 0,
                "notificationObservedCount": notification_observed,
                "soundObservedCount": sound_observed,
                "notificationSignalProviders": notification_signal_providers,
                "soundSignalProviders": sound_signal_providers,
                "notificationSensingModes": notification_sensing_modes,
                "soundSensingModes": sound_sensing_modes,
                "maxNotificationCandidateCount": max_notification_candidate_count,
                "maxSoundActiveSessionCount": max_sound_active_session_count,
            },
            "manualControls": {
                "humanCanApprove": True,
                "humanCanFreeze": True,
                "humanCanRollback": True,
                "humanCanReject": True,
            },
            "templates": items,
        },
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_compact_primitive_catalog(*, category: str | None = None) -> str:
    matrix = primitive_validation_matrix()
    return json.dumps(
        {
            "ok": True,
            "action": "list_primitives",
            "category": category,
            "summary": dict(matrix.get("summary") or {}),
            "categories": dict(matrix.get("categories") or {}),
            "primitives": list_computer_use_primitives(category=category),
        },
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_compact_driver_capabilities() -> str:
    runtime_descriptor = _get_computer_use_runtime().runtime_descriptor()
    computer_use_runtime = _get_computer_use_runtime()
    availability = (
        computer_use_runtime.availability()
        if hasattr(computer_use_runtime, "availability")
        else {}
    )
    availability_details = dict((availability or {}).get("details") or {})
    capabilities = dict(availability_details.get("capabilities") or {})
    raw_capability_truth = dict(availability_details.get("capabilityTruth") or {})
    compact_truth_platforms: dict[str, Any] = {}
    for platform_key, platform_payload in dict(raw_capability_truth.get("platforms") or {}).items():
        platform = dict(platform_payload or {})
        compact_truth_platforms[str(platform_key)] = {
            "displayPlatform": platform.get("displayPlatform") or platform_key,
            "currentHost": bool(platform.get("currentHost")),
            "statusCounts": dict(platform.get("statusCounts") or {}),
            "facets": [
                {
                    "key": facet.get("key"),
                    "status": facet.get("status"),
                    "available": bool(facet.get("available")),
                    "validationLevel": facet.get("validationLevel"),
                }
                for facet in list(platform.get("facets") or [])
                if isinstance(facet, dict)
            ],
        }
    capability_truth = {
        "version": raw_capability_truth.get("version"),
        "currentPlatform": raw_capability_truth.get("currentPlatform"),
        "platforms": compact_truth_platforms,
        "browserLaneTruth": dict(raw_capability_truth.get("browserLaneTruth") or {}),
        "knownGaps": list(raw_capability_truth.get("knownGaps") or []),
        "portableChecklist": list(raw_capability_truth.get("portableChecklist") or []),
        "screenWakePolicy": dict(raw_capability_truth.get("screenWakePolicy") or {}),
        "evidenceRefs": list(raw_capability_truth.get("evidenceRefs") or []),
    }
    experience_assets = dict(availability_details.get("experienceAssets") or {})
    return json.dumps(
        {
            "ok": True,
            "action": "desktop_capabilities",
            "driver": capabilities,
            "capabilityMatrix": dict(availability_details.get("capabilityMatrix") or {}),
            "capabilityTruth": capability_truth,
            "browserLaneTruth": dict(availability_details.get("browserLaneTruth") or {}),
            "knownGaps": list(availability_details.get("knownGaps") or []),
            "portableChecklist": list(availability_details.get("portableChecklist") or []),
            "screenWakePolicy": dict(availability_details.get("screenWakePolicy") or {}),
            "builtInPlaybookSeeds": list(availability_details.get("builtInPlaybookSeeds") or []),
            "experienceAssets": {
                "policy": experience_assets.get("policy"),
                "sources": list(experience_assets.get("sources") or []),
                "externalReferences": list(experience_assets.get("externalReferences") or []),
            },
            "routePolicy": dict(availability_details.get("routePolicy") or {}),
            "runtime": {
                "kind": runtime_descriptor.get("kind"),
                "displayName": runtime_descriptor.get("displayName"),
                "summary": runtime_descriptor.get("summary"),
                "responsibilities": list(runtime_descriptor.get("responsibilities") or []),
                "promptHints": list(runtime_descriptor.get("promptHints") or []),
                "offlineVisualBenchmark": dict(
                    ((runtime_descriptor.get("metadata") or {}).get("offlineVisualBenchmark")) or {}
                ),
                "onlineVisualLocator": dict(
                    ((runtime_descriptor.get("metadata") or {}).get("onlineVisualLocator")) or {}
                ),
                "environmentProbes": dict(
                    ((runtime_descriptor.get("metadata") or {}).get("environmentProbes")) or {}
                ),
                "browserLane": dict(availability_details.get("browserLane") or {}),
                "appAdapter": dict(availability_details.get("appAdapter") or {}),
            },
            "primitiveMatrix": dict((primitive_validation_matrix().get("summary")) or {}),
        },
        ensure_ascii=False,
        indent=2,
    )


def _rpa_compact_script_list(
    *,
    scripts: list[dict[str, Any]],
    limit: int,
) -> str:
    items: list[dict[str, Any]] = []
    for item in list(scripts or []):
        items.append(
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "updatedAt": item.get("updatedAt"),
                "size": item.get("size"),
            }
        )
    return json.dumps(
        {
            "ok": True,
            "action": "list_robot_scripts",
            "count": len(items),
            "limit": limit,
            "scripts": items,
        },
        ensure_ascii=False,
        indent=2,
    )


def _rpa_compact_run_existing_flow_response(
    *,
    raw_result: dict[str, Any],
    robot_file: str,
    cwd: str | None,
    output_dir: str | None,
) -> str:
    execution = dict(raw_result.get("execution") or {})
    status = str(raw_result.get("status") or "").strip() or "unknown"
    returncode = execution.get("returncode")
    stdout = str(execution.get("stdout") or "")
    stderr = str(execution.get("stderr") or "")
    output_path = (
        str(raw_result.get("outputDir") or "").strip()
        or str(output_dir or "").strip()
        or str((dict(raw_result.get("prepared") or {}).get("outputDir")) or "").strip()
    )
    return json.dumps(
        {
            "ok": status not in {"failed", "review_required", "blocked"},
            "action": "run_existing_rpa_flow",
            "status": status,
            "robotFile": str(raw_result.get("robotFile") or robot_file),
            "cwd": cwd,
            "outputDir": output_path or None,
            "runId": raw_result.get("runId"),
            "sessionId": raw_result.get("sessionId"),
            "execution": {
                "returncode": returncode,
                "stdoutTail": stdout[-4000:] if stdout else "",
                "stderrTail": stderr[-4000:] if stderr else "",
                "command": list(execution.get("command") or []),
            },
            "templateExecutionPolicy": dict(raw_result.get("templateExecutionPolicy") or {}),
            "review": {
                "required": status == "review_required",
                "approvalId": raw_result.get("approvalId"),
                "requiredApprovals": raw_result.get("requiredApprovals"),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _rpa_compact_run_draft_response(
    *,
    raw_result: dict[str, Any],
    script_id: str,
    cwd: str | None,
    output_dir: str | None,
) -> str:
    execution = dict(raw_result.get("execution") or {})
    status = str(raw_result.get("status") or "").strip() or "unknown"
    stdout = str(execution.get("stdout") or "")
    stderr = str(execution.get("stderr") or "")
    prepared = dict(raw_result.get("prepared") or {})
    script = dict(raw_result.get("script") or prepared.get("script") or {})
    output_path = (
        str(raw_result.get("outputDir") or "").strip()
        or str(output_dir or "").strip()
        or str(prepared.get("outputDir") or "").strip()
    )
    return json.dumps(
        {
            "ok": status not in {"failed", "review_required", "blocked"},
            "action": "run_rpa_draft",
            "status": status,
            "scriptId": str(raw_result.get("scriptId") or script_id),
            "scriptName": script.get("name") or prepared.get("scriptName"),
            "goal": script.get("goal") or prepared.get("goal"),
            "appId": script.get("appId") or prepared.get("appId"),
            "cwd": cwd,
            "outputDir": output_path or None,
            "runId": raw_result.get("runId"),
            "sessionId": raw_result.get("sessionId"),
            "execution": {
                "returncode": execution.get("returncode"),
                "stdoutTail": stdout[-4000:] if stdout else "",
                "stderrTail": stderr[-4000:] if stderr else "",
                "command": list(execution.get("command") or []),
            },
            "templateExecutionPolicy": dict(raw_result.get("templateExecutionPolicy") or {}),
            "review": {
                "required": status == "review_required",
                "approvalId": raw_result.get("approvalId"),
                "requiredApprovals": raw_result.get("requiredApprovals"),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_execute_single_step(
    *,
    action: str,
    step: dict[str, Any],
    goal: str,
) -> dict[str, Any]:
    return _get_computer_use_runtime().execute_plan(
        **_computer_use_runtime_kwargs(goal),
        steps=[step],
        continue_on_error=False,
        max_steps=1,
    )

@tool
def rpa_list_robot_scripts(
    limit: int = 20,
) -> str:
    """List locally available .robot scripts managed by the active RPA script store."""
    try:
        scripts = _get_rpa_runtime().script_store.list_robot_scripts(limit=max(1, min(limit, 100)))
        return _rpa_compact_script_list(scripts=list(scripts or []), limit=max(1, min(limit, 100)))
    except Exception as e:
        return f"Error listing RPA robot scripts: {e}"


@tool
def rpa_run_existing_flow(
    robot_file: str,
    *,
    variables_json: Optional[str] = None,
    timeout_ms: int = 600000,
    cwd: Optional[str] = None,
    output_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    user_id: Optional[str] = "anonymous",
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    trigger_source: Optional[str] = "manual",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Run an existing .robot flow through RPARuntime without requiring trace compilation."""
    normalized_robot_file = str(robot_file or "").strip()
    if not normalized_robot_file:
        return "Error: robot_file 不能为空。"
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="rpa_run_existing_flow",
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    try:
        variables = _computer_use_parse_variables_json(variables_json)
        raw_result = _get_rpa_runtime().run_existing_flow(
            robot_file=normalized_robot_file,
            variables=variables,
            output_dir=output_dir,
            timeout_ms=max(1000, min(timeout_ms, 3_600_000)),
            cwd=cwd,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id or "anonymous",
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            trigger_source=trigger_source or "manual",
        )
        response = _rpa_compact_run_existing_flow_response(
            raw_result=dict(raw_result or {}),
            robot_file=normalized_robot_file,
            cwd=cwd,
            output_dir=output_dir,
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error running existing .robot flow: {e}"


@tool
def rpa_run_draft(
    script_id: str,
    *,
    variables_json: Optional[str] = None,
    timeout_ms: int = 600000,
    cwd: Optional[str] = None,
    output_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    user_id: Optional[str] = "anonymous",
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    trigger_source: Optional[str] = "manual",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Run an existing RPA draft script through RPARuntime."""
    normalized_script_id = str(script_id or "").strip()
    if not normalized_script_id:
        return "Error: script_id 不能为空。"
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="rpa_run_draft",
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    runtime_context = get_runtime_context()
    try:
        variables = _computer_use_parse_variables_json(variables_json)
        raw_result = _get_rpa_runtime().run_draft(
            script_id=normalized_script_id,
            variables=variables,
            output_dir=output_dir,
            timeout_ms=max(1000, min(timeout_ms, 3_600_000)),
            cwd=cwd,
            session_id=session_id or runtime_context.get("session_id"),
            run_id=run_id or runtime_context.get("run_id"),
            user_id=user_id or runtime_context.get("user_id") or "anonymous",
            project_id=project_id or runtime_context.get("project_id"),
            workspace_id=workspace_id or runtime_context.get("workspace_id"),
            workspace_path=workspace_path or runtime_context.get("workspace_path"),
            trigger_source=trigger_source or "manual",
        )
        response = _rpa_compact_run_draft_response(
            raw_result=dict(raw_result or {}),
            script_id=normalized_script_id,
            cwd=cwd,
            output_dir=output_dir,
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error running RPA draft: {e}"


@tool
def execute_system_command(
    command: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Execute a synchronous system command (bash on Linux/Mac, cmd/powershell on Windows) and return its output.
    
    CRITICAL USAGE RULES:
    1. This tool blocks execution. If the command asks for user input (e.g., 'y/n', selecting from a menu), IT WILL HANG AND TIMEOUT.
    2. Therefore, you MUST ALWAYS provide non-interactive flags (like `-y`, `--no-fund`, `--silent`) when using this tool.
    3. If you CANNOT avoid interaction, or if the command is a long-running server/process, you MUST use `run_system_command(mode="session")` instead.
    
    Arguments:
        command (str): The command to execute natively.
    """
    try:
        interactive_reason = _detect_interactive_command(command)
        if interactive_reason:
            return (
                f"Error: {interactive_reason}\n"
                "请改用 `command_session_broker(mode=start)` 启动命令会话；"
                "后续观察、继续输入和终止都统一走 `command_session_broker`。"
            )

        runtime_context = get_runtime_context()
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_system_command(command, runtime_context=runtime_context),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到系统命令存在风险，是否继续执行？\n\n命令：{command}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止命令执行。"

        # Use shell=True to allow complex commands (pipes, redirects) if needed.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120  # Prevent infinite hangs
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\n[STDERR]:\n{result.stderr}"
            
        if not output.strip() and result.returncode == 0:
            return "Command executed successfully with no output."
            
        # Truncate if insanely long (protect LLM context)
        if len(output) > 20000:
            output = output[:20000] + f"\n\n...[OUTPUT TRUNCATED] ({len(output)} chars total). Use grep_search or read_native_file with lines to analyze further."

        safety_guardian.observe_post_action(
            action_family="command",
            summary=f"已执行系统命令：{command}",
            details={"command": command, "return_code": result.returncode},
            runtime_context=runtime_context,
        )
        if result.returncode == 0:
            _notify_skills_inventory_command_completed(command)
        return output
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after 120 seconds."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error executing command: {str(e)}"

@tool
def read_native_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read contents of a text file on the host filesystem.
    
    If the file is binary, it will refuse to read it to protect context.
    If the file is very large (> 2000 lines), you MUST specify start_line and end_line, otherwise it will be truncated.
    
    Arguments:
        path (str): Absolute path to the file.
        start_line (int, optional): The 1-indexed line number to start reading from.
        end_line (int, optional): The 1-indexed line number to stop reading at.
    """
    try:
        target_path = Path(path)
        if not target_path.exists() or not target_path.is_file():
            return f"Error: File '{path}' does not exist or is not a file."
            
        if is_binary(str(target_path)):
            return (
                f"Error: '{path}' appears to be a binary file. "
                "如果这是图片，请优先用 `vision_media_analyzer`；如果是分享页或视频，请优先用 "
                "`download_media_for_vision`。"
            )
            
        with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            
        total_lines = len(lines)
        
        # Apply strict pagination if requested or if file is massive
        start_idx = max(0, start_line - 1) if start_line else 0
        end_idx = min(total_lines, end_line) if end_line else total_lines
        
        # Default truncation if the agent tries to read a massive file at once
        if end_idx - start_idx > 2000:
            end_idx = start_idx + 2000
            truncated = True
        else:
            truncated = False
            
        content = "".join(lines[start_idx:end_idx])
        
        header = f"--- File: {path} (Lines {start_idx + 1} to {end_idx} of {total_lines}) ---\n"
        footer = "\n--- [TRUNCATED] Read exceeded 2000 lines limit. Use start_line/end_line to read more. ---" if truncated else ""
        
        return header + content + footer
        
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"


@tool
def share_workspace_file(path: str, mode: str = "auto") -> dict[str, Any]:
    """Share a file from the current main/project workspace as a remote session resource for preview or download.

    Compatibility wrapper: the file is adopted into the runtime artifact store with origin=workspace_adopted.

    Arguments:
        path: Absolute or relative path inside the current workspace/project workspace.
        mode: auto, preview, or download. Defaults to auto.
    """
    try:
        runtime_context = get_runtime_context()
        artifact = artifact_store.adopt_workspace_file(
            path=path,
            mode=mode,
            session_id=str(runtime_context.get("session_id") or "").strip() or None,
            run_id=str(runtime_context.get("run_id") or "").strip() or None,
            message_id=str(runtime_context.get("message_id") or "").strip() or None,
            source_component="share_workspace_file_compat",
            node="share_workspace_file",
        )
        adopted_from = artifact.get("adoptedFrom") if isinstance(artifact.get("adoptedFrom"), dict) else {}
        return {
            "ok": True,
            "artifact": artifact,
            "artifactId": artifact.get("artifactId") or artifact.get("id"),
            "origin": "workspace_adopted",
            "filename": adopted_from.get("filename") or artifact.get("title"),
            "mimeType": artifact.get("mimeType"),
            "mode": adopted_from.get("mode") or str(mode or "auto").strip() or "auto",
            "url": artifact.get("contentUrl") or artifact.get("previewUrl"),
            "previewable": bool(adopted_from.get("previewable", artifact.get("hasPreview"))),
            "downloadable": bool(adopted_from.get("downloadable", True)),
            "viewerKind": adopted_from.get("viewerKind") or (artifact.get("metadata") or {}).get("viewerKind"),
            "workspaceRelativePath": artifact.get("workspaceRelativePath") or artifact.get("workspacePath"),
            "workspaceId": artifact.get("workspaceId"),
            "projectId": artifact.get("projectId"),
            "message": "File adopted into the runtime artifact system.",
        }
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return {
            "ok": False,
            "error": str(e),
            "filename": Path(str(path or "")).name if str(path or "").strip() else None,
            "mode": str(mode or "auto").strip() or "auto",
            "previewable": False,
            "downloadable": False,
            "viewerKind": "download",
        }


@tool
def write_native_file(
    path: str,
    content: str,
    append: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Write or append text content to a native file on the host filesystem.
    
    Arguments:
        path (str): Absolute path to the file.
        content (str): The string content to write.
        append (bool): If True, appends to the end of the file. If False, overwrites the entire file.
    """
    try:
        runtime_context = get_runtime_context()
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_file_write(path, append=append, runtime_context=runtime_context),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到写文件动作需要确认，是否继续？\n\n路径：{path}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止文件写入。"

        target_path = Path(path)
        mode = 'a' if append else 'w'
        
        # Create parent directories if they don't exist
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(target_path, mode, encoding='utf-8') as f:
            f.write(content)

        safety_guardian.observe_post_action(
            action_family="file_write",
            summary=f"已写入文件：{path}",
            details={"path": str(target_path), "append": append, "content_length": len(content)},
            runtime_context=runtime_context,
        )
        action = "Appended" if append else "Created/Overwritten"
        return f"Successfully {action} file: {path} ({len(content)} chars written)"
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error writing file '{path}': {str(e)}"

@tool
def grep_search(query: str, path: str, regex: bool = False, ignore_case: bool = True) -> str:
    """Search for a specific string pattern within a file or directory recursively.
    
    This operates completely natively in Python without requiring the GNU `grep` utility, 
    making it fully compatible with Windows.
    
    Arguments:
        query (str): The string or regex pattern to search for.
        path (str): The absolute path to a file or directory to search in.
        regex (bool): Whether the query should be treated as a Regular Expression.
        ignore_case (bool): Whether the search is case-insensitive.
    """
    try:
        target_path = Path(path)
        if not target_path.exists():
            return f"Error: Path '{path}' does not exist."
            
        flags = re.IGNORECASE if ignore_case else 0
        if not regex:
            query = re.escape(query)
            
        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            return f"Error compiling regex pattern: {str(e)}"
            
        results = []
        max_results = 200  # Prevent context explosion
        
        def search_file(filepath: Path):
            if is_binary(str(filepath)):
                return
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f):
                        if pattern.search(line):
                            results.append(f"{filepath}:{i+1}:{line.strip()}")
                            if len(results) >= max_results:
                                return
            except Exception:
                pass

        if target_path.is_file():
            search_file(target_path)
            
        elif target_path.is_dir():
            # Recursive search with limits
            files_scanned = 0
            for root, _, files in os.walk(target_path):
                for file in files:
                    files_scanned += 1
                    if files_scanned > 1000:  # Hard limit on files scanned
                        break
                    
                    filepath = Path(root) / file
                    search_file(filepath)
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results or files_scanned > 1000:
                    break

        if not results:
            return f"No matches found for '{query}' in {path}."
            
        output = "\n".join(results)
        if len(results) >= max_results:
            output += f"\n\n[WARNING: Results truncated at {max_results} matches.]"
            
        return output
    except Exception as e:
        return f"Error performing search: {str(e)}"

def _launch_background_command(
    command: str,
    *,
    tool_call_id: str = "",
    profile: str = "auto",
) -> dict[str, Any]:
    interactive_reason = _detect_interactive_command(command)
    resolved_profile, profile_reason = _detect_background_command_profile(command, requested_profile=profile)
    interactive_mode = interactive_reason is not None
    if sys.platform == "win32" and interactive_mode and not HAS_WINPTY:
        raise RuntimeError(
            "当前 Windows 环境缺少 `winpty/PTY` 适配层，无法稳定自动化交互式 CLI。"
        )

    runtime_context = get_runtime_context()
    allowed, error_message = _enforce_safety_decision(
        safety_guardian.assess_background_command(command, runtime_context=runtime_context),
        tool_call_id=tool_call_id,
        question=f"Safety Guardian 检测到后台命令需要确认，是否继续？\n\n命令：{command}",
    )
    if not allowed:
        raise RuntimeError(error_message or "Safety Guardian 已阻止后台命令启动。")

    cmd_id = str(uuid.uuid4())[:8]
    bg_proc = BackgroundProcess(
        command,
        session_id=runtime_context.get("session_id"),
        run_id=runtime_context.get("run_id"),
        interactive=interactive_mode,
        profile=resolved_profile,
        profile_reason=profile_reason,
    )
    bg_proc.command_id = cmd_id
    _bg_processes[cmd_id] = bg_proc

    initial_chunks = []
    initial_capture_seconds = 0.6 if resolved_profile == "chat_cli" else 3.0
    initial_capture_limit = 192 if resolved_profile == "chat_cli" else 512
    deadline = time.time() + initial_capture_seconds
    while time.time() < deadline:
        chunk = bg_proc.get_new_output()
        if chunk:
            initial_chunks.append(chunk)
            if len("".join(initial_chunks)) >= initial_capture_limit:
                break
        if not bg_proc.is_running:
            break
        time.sleep(0.2)
    initial_out = "".join(initial_chunks).strip()
    status = bg_proc.status_snapshot()
    tty_label = "pty" if bg_proc.uses_tty else "pipe"
    safety_guardian.observe_post_action(
        action_family="background_command",
        summary=f"已启动后台命令：{command}",
        details={
            "command": command,
            "command_id": cmd_id,
            "interactive": interactive_mode,
            "tty": tty_label,
            "run_id": runtime_context.get("run_id"),
            "profile": resolved_profile,
            "profile_reason": profile_reason,
            "chat_cli_variant": bg_proc.chat_cli_variant if resolved_profile == "chat_cli" else "",
        },
        runtime_context=runtime_context,
    )
    return {
        "commandId": cmd_id,
        "mode": "interactive" if interactive_mode else "background",
        "tty": tty_label,
        "sessionId": runtime_context.get("session_id"),
        "runId": runtime_context.get("run_id"),
        "status": status,
        "interactive": interactive_mode,
        "profile": resolved_profile,
        "profileReason": profile_reason,
        "chatCliVariant": bg_proc.chat_cli_variant if resolved_profile == "chat_cli" else "",
        "initialOutput": initial_out,
    }

# ==========================================
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
            safety_guardian.assess_http_request(method, url, body=body, runtime_context=runtime_context),
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
def wait(seconds: int, note: str = "") -> str:
    """Pause briefly for a bounded number of seconds, then continue with an optional reminder note.

    Good for:
    - Re-checking a just-submitted async task after a short delay
    - Giving installs, service startup, or file generation a short stabilization window

    Not for:
    - Unbounded waiting
    - Managing long-running background processes

    Arguments:
        seconds (int): Number of seconds to wait. Must be between 1 and 120.
        note (str, optional): Short reminder for what to do after waking up.
    """
    try:
        normalized_seconds = int(seconds)
    except Exception:
        return "wait 工具参数错误：seconds 必须是 1 到 120 之间的整数。"

    if normalized_seconds < 1 or normalized_seconds > 120:
        return (
            "wait 工具参数错误：seconds 仅允许 1 到 120 秒。"
            "如果需要更久，请拆成多次短等待。"
        )

    normalized_note = str(note or "").strip()
    if len(normalized_note) > 120:
        normalized_note = normalized_note[:120].rstrip()

    try:
        time.sleep(normalized_seconds)
    except Exception as e:
        return f"wait 工具执行失败：{str(e)}"

    if normalized_note:
        return f"已等待 {normalized_seconds} 秒。备注：{normalized_note}"
    return f"已等待 {normalized_seconds} 秒。"

@tool
def list_processes(name_pattern: str = None, port: int = None) -> str:
    """List running processes on the host machine.
    
    Arguments:
        name_pattern (str, optional): Substring to match in process name or command line.
        port (int, optional): Filter processes listening on this specific port.
    """
    try:
        results = []
        for p in psutil.process_iter(['pid', 'name', 'status', 'create_time', 'cmdline']):
            try:
                if name_pattern:
                    name = p.info.get('name', '') or ''
                    cmdline = " ".join(p.info.get('cmdline', []) or [])
                    if name_pattern.lower() not in name.lower() and name_pattern.lower() not in cmdline.lower():
                        continue
                
                if port:
                    found_port = False
                    for conn in p.connections(kind='inet'):
                        if conn.laddr.port == port:
                            found_port = True
                            break
                    if not found_port:
                        continue
                        
                results.append(f"PID: {p.pid} | Name: {p.info.get('name')} | Status: {p.info.get('status')}")
                if len(results) >= 50:
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
                
        if not results:
            return "No matching processes found."
        
        output = "\n".join(results)
        if len(results) >= 50:
            output += "\n...[TRUNCATED 50 MATCHES MAX]"
        return output
    except Exception as e:
        return f"Error listing processes: {str(e)}"

@tool
def manage_process(
    pid: int,
    action: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage a running process by its PID.
    
    Arguments:
        pid (int): The process ID.
        action (str): The action to perform (must be 'kill' or 'terminate').
    """
    try:
        runtime_context = get_runtime_context()
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_process_action(pid, action, runtime_context=runtime_context),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到进程操作需要确认，是否继续？\n\n动作：{action}\nPID：{pid}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止进程操作。"

        p = psutil.Process(pid)
        if action.lower() == 'kill':
            p.kill()
            safety_guardian.observe_post_action(
                action_family="process",
                summary=f"已强制结束进程：{pid}",
                details={"pid": pid, "action": action, "name": p.name()},
                runtime_context=runtime_context,
            )
            return f"Successfully killed process {pid} ({p.name()})."
        elif action.lower() == 'terminate':
            p.terminate()
            p.wait(timeout=3)
            safety_guardian.observe_post_action(
                action_family="process",
                summary=f"已终止进程：{pid}",
                details={"pid": pid, "action": action, "name": p.name()},
                runtime_context=runtime_context,
            )
            return f"Successfully terminated process {pid} ({p.name()})."
        else:
            return f"Invalid action: {action}. Must be 'kill' or 'terminate'."
    except psutil.NoSuchProcess:
        return f"Process with PID {pid} not found."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error managing process: {str(e)}"

@tool
def manage_cron(
    action: str,
    job_id: str = None,
    expression: str = None,
    target: str = None,
    action_type: str = None,
    payload: dict = None,
    name: str = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage scheduled cron tasks in the V8Chat Engine.
    
    Arguments:
        action (str): "list", "add", or "remove".
        job_id (str, optional): The ID of the job to remove, or a new unique ID to add.
        expression (str, optional): Standard 5-part cron expression (e.g. "0 11 * * *" for daily 11am).
        target (str, optional): The execution target. Format depends on action_type:
            - action_type="command": A shell command string (e.g. "python script.py", "echo hello").
            - action_type="python": A dotted Python module path with a `run()` function (e.g. "apps.engine.scripts.cron_nightly_memo").
            - action_type="agent": A dotted Python module path containing a LangGraph `compiled_graph` (e.g. "agents.memory_agent"). 
              NOTE: This must be a valid importable Python module, NOT a display name or .md filename.
        action_type (str, optional): "command", "python", or "agent". Auto-inferred from target if omitted.
        payload (dict, optional): Keyword arguments or standard input for the target.
        name (str, optional): Human readable display name for the task.
    
    IMPORTANT - To schedule tasks that require the Supervisor team (with sub-agents and all tools):
        Use action_type="agent", target="supervisor", and put the task description in payload:
        Example: manage_cron(action="add", job_id="daily-news", expression="0 11 * * *", 
                 target="supervisor", action_type="agent", name="每日新闻简报",
                 payload={"task": "搜索今天的科技新闻头条，生成简报", "channel_id": "weixin"})
        NOTE: You can pass a `channel_id` (例如插件声明的渠道标识，如 "weixin") inside the `payload` dictionary to automatically broadcast the finished task summary to that channel.
    """
    try:
        if action in {"add", "remove"}:
            allowed, error_message = _enforce_safety_decision(
                safety_guardian.assess_cron_mutation(action, runtime_context=get_runtime_context()),
                tool_call_id=tool_call_id,
                question=f"Safety Guardian 检测到定时任务配置变更，是否继续？\n\n动作：{action}\n任务：{job_id or name or target or 'unknown'}",
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止定时任务变更。"

        from core.storage import storage
        from core.cron_manager import cron_manager
        
        config = storage.get_cron_config()
        jobs = config.get("jobs", [])
        
        if action == "list":
            if not jobs:
                return "No cron jobs scheduled."
            ret = []
            for j in jobs:
                ret.append(f"[{j.get('id')}] {j.get('name')} | {j.get('cron_expression')} | Target: {j.get('action_target')} ({j.get('action_type', '?')})")
            return "\n".join(ret)
            
        elif action == "add":
            if not job_id or not expression or not target or not name:
                return "Missing required arguments for 'add' action."
            
            # Improved action_type inference
            if action_type in ["command", "python", "agent"]:
                inferred_type = action_type
            elif target.startswith("agents.") or target.startswith("graph."):
                inferred_type = "agent"
            elif "." in target:
                inferred_type = "python"
            else:
                inferred_type = "command"
                
            new_job = {
                "id": job_id,
                "name": name,
                "cron_expression": expression,
                "action_type": inferred_type,
                "action_target": target,
                "payload": payload or {},
                "enabled": True
            }
            jobs.append(new_job)
            storage.save_cron_config({"jobs": jobs})
            cron_manager.sync_jobs_to_scheduler()
            safety_guardian.observe_post_action(
                action_family="cron_mutation",
                summary=f"已新增定时任务：{job_id}",
                details={"action": action, "job_id": job_id, "expression": expression, "target": target, "action_type": inferred_type},
                runtime_context=get_runtime_context(),
            )
            return f"Successfully added cron job '{name}' (type={inferred_type}, target={target})."
            
        elif action == "remove":
            if not job_id:
                return "Missing job_id for 'remove' action."
                
            filtered_jobs = [j for j in jobs if j.get("id") != job_id]
            if len(filtered_jobs) == len(jobs):
                return f"Job with ID '{job_id}' not found."
                
            storage.save_cron_config({"jobs": filtered_jobs})
            cron_manager.sync_jobs_to_scheduler()
            safety_guardian.observe_post_action(
                action_family="cron_mutation",
                summary=f"已删除定时任务：{job_id}",
                details={"action": action, "job_id": job_id},
                runtime_context=get_runtime_context(),
            )
            return f"Successfully removed cron job '{job_id}'."
        else:
            return "Invalid action."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error managing cron: {str(e)}"

@tool
def manage_hook(
    action: str,
    event: str = None,
    target: str = None,
    action_type: str = None,
    name: str = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage lifecycle event hooks in the V8Chat Engine.
    
    Arguments:
        action (str): "list" or "add".
        event (str, optional): The engine event to hook into (e.g. "on_chat_end", "on_agent_start").
        target (str, optional): The execution target. Format depends on action_type:
            - action_type="command": A shell command string.
            - action_type="python": A dotted Python module path with a `run()` function.
            - action_type="agent": A dotted Python module path containing a LangGraph `compiled_graph` (e.g. "agents.memory_agent").
              NOTE: This must be a valid importable Python module, NOT a display name or .md filename.
        action_type (str, optional): "command", "python", or "agent". Auto-inferred from target if omitted.
        name (str, optional): Human readable display name for the hook.
    """
    try:
        if action == "add":
            allowed, error_message = _enforce_safety_decision(
                safety_guardian.assess_hook_mutation(action, runtime_context=get_runtime_context()),
                tool_call_id=tool_call_id,
                question=f"Safety Guardian 检测到生命周期 Hook 变更，是否继续？\n\n事件：{event}\n目标：{target}",
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止 Hook 变更。"

        from core.storage import storage
        config = storage.get_hooks_config()
        hooks = config.get("hooks", [])
        
        if action == "list":
            if not hooks:
                return "No hooks configured."
            ret = []
            for h in hooks:
                evs = h.get('events', [])
                ret.append(f"[{h.get('name')}] Events: {evs} | Target: {h.get('target')} ({h.get('type', '?')})")
            return "\n".join(ret)
            
        elif action == "add":
            import uuid
            
            if not event or not target or not name:
                return "Missing required arguments for 'add' action."
            
            # Improved action_type inference
            if action_type in ["command", "python", "agent"]:
                inferred_type = action_type
            elif target.startswith("agents.") or target.startswith("graph."):
                inferred_type = "agent"
            elif "." in target:
                inferred_type = "python"
            else:
                inferred_type = "command"

            new_hook = {
                "id": str(uuid.uuid4()),
                "name": name,
                "events": [event],
                "type": inferred_type,
                "target": target,
                "async": True,
                "enabled": True
            }
            hooks.append(new_hook)
            storage.save_hooks_config({"hooks": hooks})
            safety_guardian.observe_post_action(
                action_family="hook_mutation",
                summary=f"已新增 Hook：{name}",
                details={"action": action, "event": event, "target": target, "action_type": inferred_type},
                runtime_context=get_runtime_context(),
            )
            return f"Successfully added hook '{name}' for event '{event}' (type={inferred_type}, target={target})."
        else:
             return "Invalid action. Only 'list' and 'add' are supported."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error managing hooks: {str(e)}"

@tool
def read_audit_log(limit: int = 5, source_type: str = None, status: str = None) -> str:
    """Read the system audit log to check the execution results of background tasks, chron jobs, and hooks.
    
    Arguments:
        limit (int): Maximum number of log entries to retrieve. Defaults to 5. Maximum 50.
        source_type (str, optional): Filter by source type (e.g. 'CRON', 'HOOK', 'SYSTEM').
        status (str, optional): Filter by status (e.g. 'SUCCESS', 'ERROR', 'SKIPPED').
    """
    try:
        from core.audit_logger import audit_logger
        
        limit = min(max(1, limit), 50)
        logs = audit_logger.get_logs(limit=limit, source_type=source_type, status=status)
        
        if not logs:
            return "No audit logs found matching the criteria."
            
        results = []
        for log in logs:
            ts = log.get('timestamp', '')
            src = log.get('source_type', 'UNKNOWN')
            act = log.get('action', '')
            st = log.get('status', '')
            det = log.get('details') or ''
            results.append(f"[{ts}] [{src}] {act} - {st}: {det}")
            
        return "\n".join(results)
    except Exception as e:
        return f"Error reading audit log: {str(e)}"

# ==========================================
# Memory Tools (Lightweight for Supervisor)
# ==========================================

@tool
def memory_recall(query: str, limit: int = 5) -> str:
    """Unified hybrid memory retrieval tool. Call this to search the memory system for facts, code snippets, or user preferences.
    It automatically routes your query through Full-Text Search, Vector Similarity Search, and Knowledge Graph traversal, returning reranked results.
    
    Arguments:
        query (str): Search query or natural language question (e.g. "What is the project architecture?", "React hooks preference").
        limit (int): Max number of distinct memory fragments to return. Default: 5.
    """
    try:
        results = _get_memory_runtime().unified_recall(query=query, limit=limit)
        
        if not results:
            return f"No relevant memory found for '{query}'."
            
        lines = [f"Hybrid recall results for '{query}' (Top {len(results)}):"]
        for r in results:
            s = r.get('scope', 'global')
            c = r.get('category', 'unknown')
            t = r.get('text') or r.get('fact') or ''
            lines.append(f"- [{s}|{c}] {t} (id: {r.get('id', 'N/A')})")
            
        return "\n".join(lines)
    except Exception as e:
        return f"Error executing memory_recall: {str(e)}"





@tool
def mem_delete(fact_id: str) -> str:
    """Compatibility wrapper for deleting a memory item by ID.

    Prefer `mem_update(..., mode=\"delete\")` in supervisor-facing flows.
    """
    return mem_update(fact_id=fact_id, mode="delete")

@tool
def mem_update(fact_id: str, mode: str = "update", new_content: Optional[str] = None) -> str:
    """Update or delete an existing knowledge item by ID.
    Use mode=\"update\" to replace incorrect content, or mode=\"delete\" to remove a completely false or obsolete item.

    Arguments:
        fact_id (str): The unique ID of the fact to modify (e.g. "fact-a1b2c3d4").
        mode (str): Either "update" or "delete".
        new_content (str, optional): The full corrected text when mode="update".
    """
    normalized_mode = str(mode or "update").strip().lower()
    try:
        if normalized_mode == "delete":
            success = _get_memory_runtime().delete_knowledge(fact_id=fact_id)
            if success:
                return f"✓ Deleted '{fact_id}' from memory."
            return f"Error: Knowledge item '{fact_id}' not found."

        if normalized_mode != "update":
            return "Error: mode must be either 'update' or 'delete'."

        normalized_content = str(new_content or "").strip()
        if not normalized_content:
            return "Error: new_content is required when mode='update'."

        success = _get_memory_runtime().update_knowledge(fact_id=fact_id, new_fact=normalized_content)
        if success:
            return f"✓ Updated '{fact_id}' with new content."
        return f"Error: Knowledge item '{fact_id}' not found."
    except Exception as e:
        action = "deleting" if normalized_mode == "delete" else "updating"
        return f"Error {action} memory: {str(e)}"


        
@tool
def mem_summary(tier: str, date: Optional[str] = None) -> str:
    """Retrieve historical hierarchical memory summaries (year, month, week, or day level).
    
    This is extremely useful to restore long-term context beyond the last 2 days. 
    Use the high-level summary (e.g. week/month) to find interesting dates, then call again with tier='day' and date='YYYY-MM-DD' to load the exact daily log that details the task.
    
    Arguments:
        tier (str): The level of summary to retrieve. Must be one of "day", "week", "month", or "year".
        date (str, optional): A target date in "YYYY-MM-DD" format. If omitted, uses the current date as reference to fetch the current week/month/year.
    """
    try:
        return _get_memory_runtime().read_memory_summary(tier=tier, date_str=date)
    except Exception as e:
        return f"Error retrieving memory summary: {str(e)}"


@tool
def memory_map(anchor_date: Optional[str] = None) -> str:
    """Return the brokered memory navigation map. Use this instead of raw filesystem paths."""
    try:
        payload = _get_memory_runtime().build_memory_map(anchor_date=anchor_date)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error building memory map: {str(e)}"


@tool
def memory_map_expand(memory_ref: str) -> str:
    """Expand a brokered memory map node and return its children."""
    try:
        payload = _get_memory_runtime().expand_memory_map(memory_ref=memory_ref)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error expanding memory map: {str(e)}"


@tool
def memory_read_day(memory_ref_or_date: str) -> str:
    """Read a single memory day log by brokered memoryRef or YYYY-MM-DD date."""
    try:
        return _get_memory_runtime().read_memory_day(memory_ref_or_date=memory_ref_or_date)
    except Exception as e:
        return f"Error reading memory day: {str(e)}"


def _runtime_broker_payload(
    *,
    mode: str,
    ok: bool,
    summary: str,
    grants: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    rejected: list[str] | None = None,
    error: str | None = None,
) -> str:
    payload = {
        "mode": mode,
        "ok": ok,
        "summary": summary,
        "grants": list(grants or []),
        "groups": list(groups or []),
        "rejected": list(rejected or []),
    }
    if error:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool
def runtime_broker(
    mode: str = "list",
    runtime_kind: Optional[str] = None,
    tool_group: Optional[str] = None,
    tool_groups: Optional[list[str]] = None,
    reason: Optional[str] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Supervisor-only broker for listing, granting, checking, and revoking runtime tool groups for the current run."""
    normalized_mode = str(mode or "list").strip().lower()
    route_context = dict((state or {}).get("current_route_context") or {})

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
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
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
                            groups=runtime_tool_groups_catalog(),
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
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
                            groups=runtime_tool_groups_catalog(),
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
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "current_route_context": route_context,
        },
    )


@tool
def creative_media_catalog() -> str:
    """Return the CreativeMediaRuntime provider catalog and adapter capabilities."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps(creative_media_runtime.catalog(), ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia catalog: {str(e)}"


@tool
def creative_media_resolutions() -> str:
    """Return CreativeMediaRuntime resolution presets for image and video generation."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps(creative_media_runtime.resolutions(), ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia resolutions: {str(e)}"


@tool
async def creative_media_create_job(request: dict[str, Any]) -> str:
    """Create an image, video, or voice job through CreativeMediaRuntime."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        job = await creative_media_runtime.create_job(dict(request or {}))
        return json.dumps({"job": job}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia job: {str(e)}"


@tool
async def creative_media_get_job(job_id: str, refresh: bool = True) -> str:
    """Get a CreativeMediaRuntime job by id; refresh polls resumable provider state when supported."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        job = await creative_media_runtime.refresh_job(job_id) if refresh else creative_media_runtime.get_job(job_id, refresh=False)
        if not job:
            return f"Error: CreativeMedia job not found: {job_id}"
        return json.dumps({"job": job}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia job: {str(e)}"


@tool
def creative_media_list_jobs(modality: Optional[str] = None, status: Optional[str] = None) -> str:
    """List CreativeMediaRuntime jobs, optionally filtered by modality or status."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        jobs = creative_media_runtime.list_jobs(modality=modality, status=status)
        return json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia jobs: {str(e)}"


@tool
def creative_media_job_artifacts(job_id: str) -> str:
    """List artifacts recorded by a CreativeMediaRuntime job."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps({"artifacts": creative_media_runtime.job_artifacts(job_id)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia job artifacts: {str(e)}"


@tool
def creative_media_compile_recipe(request: dict[str, Any]) -> str:
    """Compile an image, video, voice, or music recipe without calling an LLM or media provider."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        recipe = creative_media_runtime.compile_recipe(dict(request or {}))
        return json.dumps({"recipe": recipe}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error compiling CreativeMedia recipe: {str(e)}"


@tool
def creative_media_get_recipe(recipe_id: str) -> str:
    """Read a compiled CreativeMedia recipe by recipe id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        recipe = creative_media_runtime.get_recipe(recipe_id)
        if not recipe:
            return f"Error: CreativeMedia recipe not found: {recipe_id}"
        return json.dumps({"recipe": recipe}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia recipe: {str(e)}"


@tool
def creative_media_list_recipes(modality: Optional[str] = None, recipe_kind: Optional[str] = None) -> str:
    """List compiled CreativeMedia recipes, optionally filtered by modality or recipe kind."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        recipes = creative_media_runtime.list_recipes(modality=modality, recipe_kind=recipe_kind)
        return json.dumps({"recipes": recipes}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia recipes: {str(e)}"


@tool
def creative_media_register_asset(request: dict[str, Any]) -> str:
    """Register an existing artifact/path as a CreativeMedia asset ledger entry without copying the file."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        asset = creative_media_runtime.register_asset(dict(request or {}))
        return json.dumps({"asset": asset}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error registering CreativeMedia asset: {str(e)}"


@tool
def creative_media_list_assets(modality: Optional[str] = None, role: Optional[str] = None) -> str:
    """List CreativeMedia asset ledger entries, optionally filtered by modality and role."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        assets = creative_media_runtime.list_assets(modality=modality, role=role)
        return json.dumps({"assets": assets}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia assets: {str(e)}"


@tool
def creative_media_create_character_bible(request: dict[str, Any]) -> str:
    """Create or update a CreativeMedia character bible entry for character consistency."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        bible = creative_media_runtime.create_character_bible(dict(request or {}))
        return json.dumps({"characterBible": bible}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia character bible: {str(e)}"


@tool
def creative_media_get_character_bible(character_bible_id: str) -> str:
    """Read a CreativeMedia character bible by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        bible = creative_media_runtime.get_character_bible(character_bible_id)
        if not bible:
            return f"Error: CreativeMedia character bible not found: {character_bible_id}"
        return json.dumps({"characterBible": bible}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia character bible: {str(e)}"


@tool
def creative_media_list_character_bibles() -> str:
    """List CreativeMedia character bibles."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps({"characterBibles": creative_media_runtime.list_character_bibles()}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia character bibles: {str(e)}"


@tool
def creative_media_register_keyframe(request: dict[str, Any]) -> str:
    """Register an artifact/path as a CreativeMedia keyframe without copying the file."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        keyframe = creative_media_runtime.register_keyframe(dict(request or {}))
        return json.dumps({"keyframe": keyframe}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error registering CreativeMedia keyframe: {str(e)}"


@tool
def creative_media_get_keyframe(keyframe_id: str) -> str:
    """Read a CreativeMedia keyframe by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        keyframe = creative_media_runtime.get_keyframe(keyframe_id)
        if not keyframe:
            return f"Error: CreativeMedia keyframe not found: {keyframe_id}"
        return json.dumps({"keyframe": keyframe}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia keyframe: {str(e)}"


@tool
def creative_media_list_keyframes(
    recipe_id: Optional[str] = None,
    role: Optional[str] = None,
    character_bible_id: Optional[str] = None,
) -> str:
    """List CreativeMedia keyframes with optional recipe, role, or character bible filters."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        keyframes = creative_media_runtime.list_keyframes(
            recipe_id=recipe_id,
            role=role,
            character_bible_id=character_bible_id,
        )
        return json.dumps({"keyframes": keyframes}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia keyframes: {str(e)}"


@tool
def creative_media_create_edit_plan(request: dict[str, Any]) -> str:
    """Create a CreativeMedia P3 edit plan from registered video/audio assets and optional subtitles."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        plan = creative_media_runtime.create_edit_plan(dict(request or {}))
        return json.dumps({"editPlan": plan}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia edit plan: {str(e)}"


@tool
def creative_media_get_edit_plan(plan_id: str) -> str:
    """Read a CreativeMedia edit plan by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        plan = creative_media_runtime.get_edit_plan(plan_id)
        if not plan:
            return f"Error: CreativeMedia edit plan not found: {plan_id}"
        return json.dumps({"editPlan": plan}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia edit plan: {str(e)}"


@tool
def creative_media_list_edit_plans(recipe_id: Optional[str] = None) -> str:
    """List CreativeMedia edit plans, optionally filtered by recipe id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps({"editPlans": creative_media_runtime.list_edit_plans(recipe_id=recipe_id)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia edit plans: {str(e)}"


@tool
def creative_media_render_edit_plan(request: dict[str, Any]) -> str:
    """Render a CreativeMedia edit plan locally through ffmpeg and record output artifacts."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        render = creative_media_runtime.render_edit_plan(dict(request or {}))
        return json.dumps({"render": render}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error rendering CreativeMedia edit plan: {str(e)}"


@tool
def creative_media_get_render(render_job_id: str) -> str:
    """Read a CreativeMedia render job by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        render = creative_media_runtime.get_render(render_job_id)
        if not render:
            return f"Error: CreativeMedia render job not found: {render_job_id}"
        return json.dumps({"render": render}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia render job: {str(e)}"


@tool
def creative_media_list_renders(plan_id: Optional[str] = None, status: Optional[str] = None) -> str:
    """List CreativeMedia render jobs, optionally filtered by edit plan or status."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        renders = creative_media_runtime.list_renders(plan_id=plan_id, status=status)
        return json.dumps({"renders": renders}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia render jobs: {str(e)}"


@tool
def creative_media_create_quality_job(request: dict[str, Any]) -> str:
    """Run lightweight deterministic quality checks for a CreativeMedia job or artifacts."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        quality_job = creative_media_runtime.create_quality_job(dict(request or {}))
        return json.dumps({"qualityJob": quality_job}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia quality job: {str(e)}"


@tool
def creative_media_list_quality_jobs(status: Optional[str] = None) -> str:
    """List CreativeMedia quality jobs, optionally filtered by status."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps({"qualityJobs": creative_media_runtime.list_quality_jobs(status=status)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing CreativeMedia quality jobs: {str(e)}"


@tool
def creative_media_get_quality_job(quality_job_id: str) -> str:
    """Read a CreativeMedia quality job by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        quality_job = creative_media_runtime.get_quality_job(quality_job_id)
        if not quality_job:
            return f"Error: CreativeMedia quality job not found: {quality_job_id}"
        return json.dumps({"qualityJob": quality_job}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia quality job: {str(e)}"


@tool
async def creative_media_retry_job(job_id: str, request: Optional[dict[str, Any]] = None) -> str:
    """Retry a CreativeMedia job within the same operationKind using runtime retry policy."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        job = await creative_media_runtime.retry_job(job_id, dict(request or {}))
        return json.dumps({"job": job}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error retrying CreativeMedia job: {str(e)}"


@tool
def creative_media_cost_ledger() -> str:
    """List CreativeMedia provider cost and usage ledger entries."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps({"entries": creative_media_runtime.list_cost_ledger()}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia cost ledger: {str(e)}"


@tool
def creative_media_safety_events() -> str:
    """List CreativeMedia prompt safety rewrite and provider policy events."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps({"events": creative_media_runtime.list_safety_events()}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia safety events: {str(e)}"


@tool
def ask_user(question: str, details: Optional[str] = None, tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
    """Ask the user for mandatory input or confirmation and pause the graph until a response is provided."""
    request = {
        "question": question,
        "prompt": question,
        "toolCallId": tool_call_id,
        "interactionKind": "ask_user",
    }
    if details:
        request["details"] = details

    response = interrupt(request)
    if isinstance(response, dict):
        if isinstance(response.get("answer"), str) and response["answer"].strip():
            return response["answer"].strip()
        return json.dumps(response, ensure_ascii=False)
    return str(response)

# ==========================================
# Task Planning Tools (write_todos / update_todo)
# ==========================================

@tool
def write_todos(task_name: str, plan_markdown: str, todos: list[str], tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Create a structured task plan ONLY after user requirements are fully clarified.
    
    ⚠️ MANDATORY PRE-CONDITIONS — You MUST meet ALL of these before calling this tool:
    1. You have asked the user clarifying questions about ambiguous requirements
    2. The user has explicitly confirmed or given a clear execution instruction
    3. Each todo item maps to a concrete, actionable step (not "research X" vaguely)
    
    ❌ DO NOT call this tool if:
    - The user's request is vague or open-ended (ask follow-up questions first)
    - You haven't confirmed key details (format, destination, scope, preferences)
    - You are unsure about any step in the plan
    
    ✅ WHEN to call this tool:
    - User says "开始吧", "执行", "就这样做" or gives explicit go-ahead
    - All parameters/preferences have been discussed and agreed upon
    
    Arguments:
        task_name: A short, english, dash-separated folder name for the task (e.g. 'crawler-feature')
        plan_markdown: A comprehensive markdown document outlining the architectural decisions and strategy details.
        todos: List of specific, actionable task descriptions.
              Example: ["搜索5条AI领域最新新闻", "为每条新闻生成配图", "创建飞书文档并上传"]
    """
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command
    
    runtime_context = get_runtime_context()
    now_iso = datetime.now(timezone.utc).isoformat()
    task_id = f"task_{uuid.uuid4().hex[:12]}"

    normalized_todos: list[str] = []
    seen_texts: set[str] = set()
    for raw in list(todos or []):
        text = str(raw or "").strip()
        if not text:
            continue
        if len(text) > 240:
            text = text[:237].rstrip() + "..."
        lowered = text.lower()
        if lowered in seen_texts:
            continue
        seen_texts.add(lowered)
        normalized_todos.append(text)

    if not normalized_todos:
        normalized_todos = ["Clarify and continue the task plan."]

    # Emit a special initialization marker plus the actual todo items
    init_marker = {
        "_task_init": True,
        "taskId": task_id,
        "name": task_name,
        "plan": plan_markdown,
        "runId": runtime_context.get("run_id"),
        "sessionId": runtime_context.get("session_id"),
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    todo_items = [
        {
            "id": f"{task_id}-item-{idx}",
            "text": text,
            "status": "pending",
            "order": idx,
            "createdAt": now_iso,
            "updatedAt": now_iso,
        }
        for idx, text in enumerate(normalized_todos)
    ]
    
    payload = [init_marker] + todo_items
    checklist = "\n".join([f"  [ ] {t}" for t in normalized_todos])
    
    return Command(
        update={
            "todos": payload,
            "messages": [ToolMessage(
                content=f"✓ Persistent Task plan '{task_name}' created with {len(normalized_todos)} items:\n{checklist}",
                tool_call_id=tool_call_id
            )]
        }
    )


@tool
def update_todo(index: int, status: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Mark a todo item's status to track progress.
    
    Arguments:
        index: 0-based index of the todo item to update.
        status: New status — must be 'done', 'in_progress', or 'skipped'.
    """
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command
    
    if status not in ("done", "in_progress", "skipped"):
        return Command(
            update={
                "messages": [ToolMessage(
                    content=f"Error: Invalid status '{status}'. Must be 'done', 'in_progress', or 'skipped'.",
                    tool_call_id=tool_call_id
                )]
            }
        )
    
    icon = {"done": "✓", "in_progress": "→", "skipped": "⊘"}.get(status, "?")
    
    # We can't directly modify existing todos from a tool (reducer is operator.add).
    # Instead, return a special marker that supervisor_node will interpret.
    return Command(
        update={
            "todos": [{"_update": True, "index": index, "status": status, "updatedAt": datetime.now(timezone.utc).isoformat()}],
            "messages": [ToolMessage(
                content=f"{icon} Todo #{index} marked as '{status}'.",
                tool_call_id=tool_call_id
            )]
        }
    )




# --- Background Command Manager ---
_BACKGROUND_PROCESS_RETENTION_SECONDS = 300
_SKILLS_ADD_COMMAND_PATTERN = re.compile(r"(?i)(?:^|[;&|]\s*)npx\s+skills\s+add\b")
_PROMPT_HINT_PATTERN = re.compile(
    r"(^|\n)\s*(?:[>$#»❯]\s*|输入您的消息|Type your message|Press \? for shortcuts|按 \? 查看快捷键)",
    re.IGNORECASE,
)
_BUSY_HINT_PATTERN = re.compile(
    r"(thinking|generating|loading|connecting|initializing|authorizing|处理中|思考中|生成中|连接中|esc to cancel|pressing 'a' to continue|i'm feeling lucky|channeling the force|magic smoke|\.\.\.|⋯|█)",
    re.IGNORECASE,
)
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_RAW_FRAME_HISTORY_LIMIT = 12
_RAW_FRAME_PREVIEW_LIMIT = 240
_TERMINAL_TEXT_SNIPPET_LIMIT = 4000
_SPACED_CJK_SEQUENCE_PATTERN = re.compile(r"(?:[\u3400-\u9fff]\s){4,}[\u3400-\u9fff]")
_REPEATED_CJK_PATTERN = re.compile(r"([\u3400-\u9fff])\1{7,}")
_BOX_DRAWING_ONLY_LINE_PATTERN = re.compile(r"^[\s┌┐└┘├┤┬┴┼─│╭╮╰╯═║╔╗╚╝╠╣╦╩╬]+$")
_BACKGROUND_COMMAND_PROFILES = {"auto", "chat_cli", "shell"}
_CHAT_CLI_VARIANT_ALIASES = {
    "qwen": "qwen",
    "claude": "claude",
    "claude-code": "claude",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "codex": "codex",
    "aider": "aider",
}
_KNOWN_CHAT_CLI_COMMANDS = set(_CHAT_CLI_VARIANT_ALIASES.keys())
_CHAT_CLI_IDLE_COMPLETE_SECONDS = 1.2
_CHAT_CLI_CHUNK_TARGET = 320
_CHAT_CLI_CHUNK_MIN = 200
_CHAT_CLI_CHUNK_MAX = 500
_CHAT_CLI_SENTENCE_BOUNDARIES = "。！？!?；;\n"
_CHAT_CLI_SOFT_BOUNDARIES = "，,、:： "
_CHAT_CLI_SHARED_NOISE_LINE_PATTERNS = (
    re.compile(r"^\s*(?:[⠁-⣿]+\s+)?(?:connecting to mcp servers?|loading|spinning)\b.*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[⠁-⣿]+\s+)?just a tick, i'm polishing my wit\b.*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[⠁-⣿]+\s+)?(?:thinking|generating|processing|initializing|authorizing)\b.*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[⠁-⣿]+\s+)?[^(]{0,160}\(\d+s\s+·\s+.*(?:cancel|取消)\)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:type your message|输入您的消息)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:\?|按\s*\?)\s+.*(?:shortcuts|快捷键)", re.IGNORECASE),
    re.compile(r"^\s*>\s+type your message\b.*$", re.IGNORECASE),
)
_CHAT_CLI_VARIANT_NOISE_LINE_PATTERNS = {
    "qwen": (
        re.compile(r"^\s*(?:[⠁-⣿]+\s+)?(?:pressing 'a' to continue|i'm feeling lucky|channeling the force|ensuring the magic smoke stays inside the wires|tasting the snozberries|just a moment, i'm tuning the algorithms)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*qwen(?:\s+code)?(?:\s*\(v[\w.\-]+\))?\s*$", re.IGNORECASE),
        re.compile(r"^\s*qwen oauth\b.*$", re.IGNORECASE),
        re.compile(r"^\s*tips:\s*(?:switch auth type quickly|start a fresh idea)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*✦\s*(?:用户|user)\s*$", re.IGNORECASE),
        re.compile(r"^\s*[│|].*\bqwen(?:\s+code|\s+oauth)?\b.*[│|]\s*$", re.IGNORECASE),
    ),
    "claude": (
        re.compile(r"^\s*(?:[⠁-⣿]+\s+)?(?:thinking|working|analyzing|tooling up)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*(?:claude|claude code)(?:\s*\(v[\w.\-]+\))?\s*$", re.IGNORECASE),
        re.compile(r"^\s*(?:press\s+esc\s+to\s+interrupt|esc\s+to\s+cancel)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*model\s*:\s*claude\b.*$", re.IGNORECASE),
        re.compile(r"^\s*[│|].*\bclaude(?:\s+code)?\b.*[│|]\s*$", re.IGNORECASE),
    ),
    "gemini": (
        re.compile(r"^\s*(?:[⠁-⣿]+\s+)?(?:thinking|loading|connecting|authorizing|switching models?)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*(?:gemini|gemini cli)(?:\s*\(v[\w.\-]+\))?\s*$", re.IGNORECASE),
        re.compile(r"^\s*model\s*:\s*gemini\b.*$", re.IGNORECASE),
        re.compile(r"^\s*[│|].*\bgemini(?:\s+cli)?\b.*[│|]\s*$", re.IGNORECASE),
    ),
    "codex": (
        re.compile(r"^\s*(?:[⠁-⣿]+\s+)?(?:thinking|loading|connecting|working)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*codex(?:\s+cli)?(?:\s*\(v[\w.\-]+\))?\s*$", re.IGNORECASE),
        re.compile(r"^\s*model\s*:\s*codex\b.*$", re.IGNORECASE),
        re.compile(r"^\s*(?:press\s+/|type\s+/help)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*[│|].*\bcodex(?:\s+cli)?\b.*[│|]\s*$", re.IGNORECASE),
    ),
}


def _extract_chat_cli_command_head(command: str) -> str:
    stripped = str(command or "").strip()
    if not stripped:
        return ""
    try:
        tokens = shlex.split(stripped, posix=False)
    except Exception:
        tokens = stripped.split()
    if not tokens:
        return ""
    candidate = str(tokens[0] or "").strip()
    if not candidate:
        return ""
    path_value = Path(candidate)
    normalized = path_value.stem.lower() or path_value.name.lower()
    return _CHAT_CLI_VARIANT_ALIASES.get(normalized, normalized)


def _normalize_background_command_profile(profile: str | None) -> str:
    normalized = str(profile or "auto").strip().lower() or "auto"
    if normalized not in _BACKGROUND_COMMAND_PROFILES:
        raise ValueError("profile 必须是 auto、chat_cli 或 shell。")
    return normalized


def _detect_background_command_profile(command: str, *, requested_profile: str = "auto") -> tuple[str, str]:
    normalized_profile = _normalize_background_command_profile(requested_profile)
    if normalized_profile in {"chat_cli", "shell"}:
        return normalized_profile, ("显式指定 chat_cli profile。" if normalized_profile == "chat_cli" else "显式指定 shell profile。")

    stripped = str(command or "").strip()
    if not stripped:
        return "shell", "命令为空，回退到 shell profile。"

    head = _extract_chat_cli_command_head(stripped)
    if head in _KNOWN_CHAT_CLI_COMMANDS:
        return "chat_cli", f"检测到 AI CLI `{head}`，自动启用 chat_cli profile。"
    return "shell", "默认 shell profile。"


def _detect_chat_cli_variant(command: str) -> str:
    head = _extract_chat_cli_command_head(command)
    return _CHAT_CLI_VARIANT_ALIASES.get(head, head if head in _CHAT_CLI_VARIANT_NOISE_LINE_PATTERNS else "")


def _normalize_chat_cli_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    normalized = _ANSI_ESCAPE_PATTERN.sub("", normalized)
    normalized = normalized.strip()
    if not normalized:
        return ""
    return normalized


def _looks_like_prompt_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    return bool(_PROMPT_HINT_PATTERN.search(f"\n{stripped}"))


def _strip_chat_cli_prompt_tail(text: str) -> str:
    lines = _normalize_chat_cli_text(text).splitlines()
    while lines and _looks_like_prompt_line(lines[-1]):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _looks_like_chat_cli_border_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if len(stripped) < 12:
        return False
    if _BOX_DRAWING_ONLY_LINE_PATTERN.fullmatch(stripped):
        return True
    unique_chars = {char for char in stripped if not char.isspace()}
    return len(unique_chars) <= 2


def _looks_like_chat_cli_noise_line(line: str, *, variant: str = "") -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if _looks_like_prompt_line(stripped):
        return True
    if _looks_like_chat_cli_border_line(stripped):
        return True
    if any(pattern.search(stripped) for pattern in _CHAT_CLI_SHARED_NOISE_LINE_PATTERNS):
        return True
    return any(pattern.search(stripped) for pattern in _CHAT_CLI_VARIANT_NOISE_LINE_PATTERNS.get(str(variant or "").lower(), ()))


def _sanitize_chat_cli_semantic_text(text: str, *, variant: str = "") -> str:
    lines = _normalize_chat_cli_text(text).splitlines()
    if not lines:
        return ""
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if filtered and filtered[-1] != "":
                filtered.append("")
            continue
        if _looks_like_chat_cli_noise_line(stripped, variant=variant):
            continue
        cleaned_line = re.sub(r"^\s*[✦◆◉●]\s*", "", line).rstrip()
        filtered.append(cleaned_line)
    while filtered and not filtered[0].strip():
        filtered.pop(0)
    while filtered and not filtered[-1].strip():
        filtered.pop()
    return "\n".join(filtered).strip()


def _collapse_chat_cli_cumulative_lines(text: str, *, variant: str = "") -> str:
    lines = _sanitize_chat_cli_semantic_text(text, variant=variant).splitlines()
    if not lines:
        return ""
    collapsed: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if collapsed and collapsed[-1] != "":
                collapsed.append("")
            continue
        future_candidates = [candidate.strip() for candidate in lines[index + 1 : index + 4] if candidate.strip()]
        if any(len(candidate) > len(stripped) and candidate.startswith(stripped) for candidate in future_candidates):
            continue
        if collapsed:
            previous = collapsed[-1].strip()
            if previous and len(stripped) > len(previous) and stripped.startswith(previous):
                collapsed[-1] = line
                continue
            if previous and len(previous) > len(stripped) and previous.startswith(stripped):
                continue
        collapsed.append(line)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return "\n".join(collapsed).strip()


def _longest_overlap_suffix_prefix(left: str, right: str) -> int:
    max_overlap = min(len(left), len(right))
    for size in range(max_overlap, 0, -1):
        if left.endswith(right[:size]):
            return size
    return 0


def _consume_chat_cli_semantic_suffix(previous: str, current: str) -> str:
    previous_value = _normalize_chat_cli_text(previous)
    current_value = _normalize_chat_cli_text(current)
    if not current_value:
        return ""
    if not previous_value:
        return current_value
    if current_value.startswith(previous_value):
        return current_value[len(previous_value):]
    marker_index = current_value.rfind(previous_value)
    if marker_index >= 0:
        return current_value[marker_index + len(previous_value):]
    overlap = _longest_overlap_suffix_prefix(previous_value, current_value)
    if overlap > 0:
        return current_value[overlap:]
    return current_value


def _merge_chat_cli_turn_text(previous: str, current: str) -> str:
    previous_value = _normalize_chat_cli_text(previous)
    current_value = _normalize_chat_cli_text(current)
    if not current_value:
        return previous_value
    if not previous_value:
        return current_value
    if current_value == previous_value:
        return previous_value
    if current_value.startswith(previous_value):
        return current_value
    if previous_value.startswith(current_value):
        return previous_value
    marker_index = current_value.rfind(previous_value)
    if marker_index >= 0:
        return current_value
    overlap = _longest_overlap_suffix_prefix(previous_value, current_value)
    if overlap > 0:
        return previous_value + current_value[overlap:]
    return current_value if len(current_value) >= len(previous_value) else previous_value


def _strip_chat_cli_input_echo(text: str, input_text: str) -> str:
    normalized_text = _normalize_chat_cli_text(text)
    normalized_input = _normalize_chat_cli_text(input_text)
    if not normalized_text or not normalized_input:
        return normalized_text

    input_lines = [line.strip() for line in normalized_input.splitlines() if line.strip()]
    if not input_lines:
        return normalized_text

    lines = normalized_text.splitlines()
    while lines and input_lines:
        first_line = re.sub(r"^[>#»❯]\s*", "", lines[0].strip())
        expected = input_lines[0]
        if (
            first_line == expected
            or first_line.endswith(expected)
            or (len(expected) >= 4 and expected in first_line)
        ):
            lines.pop(0)
            input_lines.pop(0)
            continue
        break
    if not input_lines:
        return "\n".join(lines).strip()

    filtered_lines: list[str] = []
    remaining_inputs = list(input_lines)
    normalized_expected_lines = {line.strip() for line in input_lines if line.strip()}
    for line in lines:
        stripped = re.sub(r"^[>#»❯]\s*", "", line.strip())
        if stripped in normalized_expected_lines:
            continue
        if remaining_inputs:
            expected = remaining_inputs[0]
            if (
                stripped == expected
                or stripped.endswith(expected)
                or (len(expected) >= 4 and expected in stripped)
            ):
                remaining_inputs.pop(0)
                continue
        filtered_lines.append(line)
    return "\n".join(filtered_lines).strip()


def _digest_chat_cli_text(text: str) -> str:
    normalized = _normalize_chat_cli_text(text)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()[:12]


def _slice_chat_cli_delta_chunk(
    text: str,
    *,
    target: int = _CHAT_CLI_CHUNK_TARGET,
    minimum: int = _CHAT_CLI_CHUNK_MIN,
    maximum: int = _CHAT_CLI_CHUNK_MAX,
) -> str:
    normalized = _normalize_chat_cli_text(text)
    if len(normalized) <= maximum:
        return normalized

    upper_bound = normalized[:maximum]
    for boundaries in ("\n\n", _CHAT_CLI_SENTENCE_BOUNDARIES, _CHAT_CLI_SOFT_BOUNDARIES):
        best_index = -1
        if boundaries == "\n\n":
            best_index = upper_bound.rfind(boundaries)
            if best_index >= minimum:
                return upper_bound[: best_index + len(boundaries)].rstrip()
            continue
        for idx, char in enumerate(upper_bound):
            if char in boundaries and idx + 1 >= minimum:
                best_index = idx + 1
        if best_index >= minimum:
            return upper_bound[:best_index].rstrip()
    return upper_bound.rstrip()


def _is_skills_add_command(command: str) -> bool:
    return bool(_SKILLS_ADD_COMMAND_PATTERN.search(str(command or "")))


def _notify_skills_inventory_command_completed(command: str) -> None:
    if not _is_skills_add_command(command):
        return
    try:
        from core.extensions_runtime import extensions_runtime_service

        extensions_runtime_service.request_skill_inventory_refresh(reason="skills_add_completed")
    except Exception as exc:
        print(f"[SkillsInventory] Failed to request refresh after skills install: {type(exc).__name__}: {exc}")


def _extract_command_head(command: str) -> str:
    raw = str(command or "").strip()
    if not raw:
        return ""
    try:
        parts = shlex.split(raw, posix=False)
    except Exception:
        parts = raw.split()
    return str(parts[0] if parts else "").strip()


def _build_command_diagnostics_snapshot(command: str) -> dict[str, Any]:
    command_head = _extract_command_head(command)
    path_value = str(os.environ.get("PATH") or "")
    appdata = str(os.environ.get("APPDATA") or "").strip()
    roaming_npm = str(Path(appdata) / "npm") if appdata else ""
    local_npm = str(Path.home() / "AppData" / "Roaming" / "npm")
    resolved_path = shutil.which(command_head, path=path_value) if command_head else None
    oauth_candidates = {
        "qwen": Path.home() / ".qwen" / "oauth_creds.json",
        "codex": Path.home() / ".codex" / "auth.json",
        "gemini": Path.home() / ".gemini" / "oauth_creds.json",
        "claude": Path.home() / ".claude" / "oauth_creds.json",
    }
    normalized_path_entries = [item.strip().lower() for item in path_value.split(os.pathsep) if item.strip()]
    cli_health_matrix: dict[str, dict[str, Any]] = {}
    cli_heads = {
        "qwen": ["qwen", "qwen.cmd", "qwen.ps1"],
        "codex": ["codex", "codex.cmd", "codex.ps1"],
        "gemini": ["gemini", "gemini.cmd", "gemini.ps1", "gemini-cli", "gemini-cli.cmd", "gemini-cli.ps1"],
        "claude": ["claude", "claude.cmd", "claude.ps1", "claude-code", "claude-code.cmd", "claude-code.ps1"],
    }
    for cli_name, candidates in cli_heads.items():
        resolved_candidates = []
        for candidate in candidates:
            resolved_candidate = shutil.which(candidate, path=path_value)
            if resolved_candidate:
                resolved_candidates.append(resolved_candidate)
        oauth_path = oauth_candidates.get(cli_name)
        cli_health_matrix[cli_name] = {
            "available": bool(resolved_candidates),
            "resolvedExecutables": resolved_candidates,
            "oauthFile": str(oauth_path) if oauth_path and oauth_path.exists() else "",
        }
    return {
        "commandHead": command_head,
        "resolvedExecutable": resolved_path or "",
        "currentWorkingDirectory": os.getcwd(),
        "shellPath": str(os.environ.get("COMSPEC") or ""),
        "nodeExecutable": shutil.which("node", path=path_value) or "",
        "preferredEncoding": locale.getpreferredencoding(False),
        "stdinEncoding": getattr(sys.stdin, "encoding", "") or "",
        "stdoutEncoding": getattr(sys.stdout, "encoding", "") or "",
        "filesystemEncoding": sys.getfilesystemencoding() or "",
        "appData": appdata,
        "roamingNpmPath": roaming_npm,
        "localNpmPath": local_npm,
        "pathContainsRoamingNpm": roaming_npm.lower() in normalized_path_entries if roaming_npm else False,
        "pathContainsLocalNpm": local_npm.lower() in normalized_path_entries if local_npm else False,
        "pathEntryCount": len(normalized_path_entries),
        "pathEntriesHead": [item for item in path_value.split(os.pathsep) if item.strip()][:8],
        "oauthFiles": {
            key: str(path) for key, path in oauth_candidates.items() if path.exists()
        },
        "cliHealthMatrix": cli_health_matrix,
    }


def _locale_looks_utf8(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return "utf-8" in normalized or "utf8" in normalized


def _preferred_utf8_locale() -> str:
    if sys.platform == "linux":
        return "C.UTF-8"
    return "en_US.UTF-8"


def _build_terminal_env_overrides() -> dict[str, str]:
    overrides: dict[str, str] = {
        "TERM": "xterm-256color",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    preferred_locale = _preferred_utf8_locale()
    if sys.platform == "win32":
        overrides["LANG"] = preferred_locale
        overrides["LC_ALL"] = preferred_locale
        return overrides

    current_lang = str(os.environ.get("LANG") or "").strip()
    if not _locale_looks_utf8(current_lang):
        overrides["LANG"] = preferred_locale
    current_lc_all = str(os.environ.get("LC_ALL") or "").strip()
    if current_lc_all and not _locale_looks_utf8(current_lc_all):
        overrides["LC_ALL"] = preferred_locale
    return overrides


def _build_winpty_bootstrap_commands(env_overrides: dict[str, str]) -> list[str]:
    commands = ["@echo off", "chcp 65001 >NUL"]
    for key, value in env_overrides.items():
        commands.append(f"set {key}={value}")
    return commands


def _extend_command_diagnostics_for_terminal(
    diagnostics: dict[str, Any],
    *,
    env_overrides: dict[str, str],
    uses_winpty: bool,
) -> dict[str, Any]:
    next_diagnostics = dict(diagnostics or {})
    next_diagnostics["targetTextEncoding"] = "utf-8"
    next_diagnostics["terminalEnvOverrides"] = dict(env_overrides)
    if uses_winpty:
        next_diagnostics["ptyShell"] = "cmd.exe /q /d"
        next_diagnostics["winptyBootstrapCodePage"] = "65001"
        next_diagnostics["winptyBootstrapCommands"] = _build_winpty_bootstrap_commands(env_overrides)
    return next_diagnostics


def _run_winpty_bootstrap(pty_win: Any, env_overrides: dict[str, str]) -> None:
    for command in _build_winpty_bootstrap_commands(env_overrides):
        _write_winpty_input(pty_win, f"{command}\n")
        time.sleep(0.05)


def _strip_terminal_bootstrap_noise(text: str, env_overrides: dict[str, str] | None = None) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""
    removable_lines = {"@echo off", "chcp 65001 >nul"}
    for key, value in (env_overrides or {}).items():
        removable_lines.add(f"set {key}={value}".lower())
    filtered_lines: list[str] = []
    for line in normalized.split("\n"):
        if line.strip().lower() in removable_lines:
            continue
        filtered_lines.append(line)
    while filtered_lines and not filtered_lines[0].strip():
        filtered_lines.pop(0)
    while filtered_lines and not filtered_lines[-1].strip():
        filtered_lines.pop()
    return "\n".join(filtered_lines)


_TERMINAL_STABLE_DUPLICATE_LIMIT = 1
_TERMINAL_VOLATILE_LINE_PATTERNS = (
    re.compile(r"^\s*[✦●]\s+"),
    re.compile(r"^\s*(?:\?|按\s*\?)\s+.*(?:shortcuts|快捷键)", re.IGNORECASE),
    re.compile(r"^\s*.*(?:verbose|used\s*\|)", re.IGNORECASE),
    re.compile(r"^\s*(?:connecting to mcp|tasting the snozberries|spinning)", re.IGNORECASE),
)


def _normalize_terminal_snapshot_lines(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u200b", "")
    return [line.rstrip() for line in normalized.split("\n")]


def _looks_like_terminal_volatile_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    return any(pattern.search(stripped) for pattern in _TERMINAL_VOLATILE_LINE_PATTERNS)


def _build_stable_terminal_snapshot(text: str) -> str:
    lines = _normalize_terminal_snapshot_lines(text)
    if not lines:
        return ""

    stabilized: list[str] = []
    duplicate_counts: dict[str, int] = {}
    last_non_empty_line: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if stabilized and stabilized[-1] == "":
                continue
            stabilized.append("")
            continue
        if last_non_empty_line == stripped:
            continue
        if stabilized and stabilized[-1] == line:
            continue
        if _looks_like_terminal_volatile_line(line):
            count = duplicate_counts.get(stripped, 0)
            if count >= _TERMINAL_STABLE_DUPLICATE_LIMIT:
                continue
            duplicate_counts[stripped] = count + 1
        stabilized.append(line)
        last_non_empty_line = stripped

    while stabilized and not stabilized[0].strip():
        stabilized.pop(0)
    while stabilized and not stabilized[-1].strip():
        stabilized.pop()

    return "\n".join(stabilized)


def _looks_like_terminal_mojibake(text: str) -> bool:
    normalized = str(text or "")
    if not normalized.strip():
        return False
    cjk_count = sum(1 for char in normalized if "\u3400" <= char <= "\u9fff")
    if cjk_count < 8:
        return False
    if _SPACED_CJK_SEQUENCE_PATTERN.search(normalized):
        return True
    if _REPEATED_CJK_PATTERN.search(normalized):
        return True
    return False


def _derive_terminal_encoding_status(
    *,
    screen_snapshot: str,
    raw_preview: str,
    diagnostics: dict[str, Any] | None,
) -> tuple[str, str | None, str]:
    normalized_screen = str(screen_snapshot or "")
    normalized_raw = str(raw_preview or "")
    resolved_encoding = str((diagnostics or {}).get("targetTextEncoding") or "utf-8").strip() or "utf-8"
    notes: list[str] = []
    state = "clean"

    if "\ufffd" in normalized_screen or "\ufffd" in normalized_raw:
        state = "undecodable"
        notes.append("终端输出包含替换字符，说明当前文本解码不完整。")
    elif _looks_like_terminal_mojibake(normalized_screen) or _looks_like_terminal_mojibake(normalized_raw):
        state = "suspect_mojibake"
        notes.append("检测到疑似 mojibake 特征，当前终端文本可能发生了编码失真。")

    stdin_encoding = str((diagnostics or {}).get("stdinEncoding") or "").strip().lower()
    stdout_encoding = str((diagnostics or {}).get("stdoutEncoding") or "").strip().lower()
    if stdin_encoding and stdout_encoding and stdin_encoding != stdout_encoding:
        notes.append(f"父进程标准输入/输出编码不一致（{stdin_encoding} / {stdout_encoding}）。")

    if sys.platform == "win32" and str((diagnostics or {}).get("winptyBootstrapCodePage") or "") != "65001":
        notes.append("Windows PTY 未明确切换到 UTF-8 代码页。")

    return state, " ".join(dict.fromkeys(note for note in notes if note)) or None, resolved_encoding


def _terminal_snapshot_looks_like_prompt(snapshot: str) -> bool:
    normalized = str(snapshot or "").strip()
    if not normalized:
        return False
    tail = "\n".join(normalized.splitlines()[-4:])
    return bool(_PROMPT_HINT_PATTERN.search(tail))


def _terminal_snapshot_looks_busy(snapshot: str) -> bool:
    normalized = str(snapshot or "").strip()
    if not normalized:
        return False
    tail = "\n".join(normalized.splitlines()[-6:])
    return bool(_BUSY_HINT_PATTERN.search(tail))


def _normalize_status_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _preview_terminal_frame(text: str, *, limit: int = _RAW_FRAME_PREVIEW_LIMIT) -> str:
    normalized = str(text or "").replace("\r", "\\r").replace("\n", "\\n")
    normalized = normalized.replace("\x1b", "\\u001b")
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def _contains_terminal_escape(text: str) -> bool:
    return bool(_ANSI_ESCAPE_PATTERN.search(str(text or "")))


def _truncate_terminal_text(text: str, *, limit: int = _TERMINAL_TEXT_SNIPPET_LIMIT) -> str:
    normalized = str(text or "")
    if len(normalized) <= limit:
        return normalized
    omitted = len(normalized) - limit
    return f"{normalized[-limit:]}\n\n[Truncated {omitted} earlier characters]"


class _SimpleTerminalScreen:
    def __init__(self, cols: int = 80, rows: int = 24):
        self.cols = max(int(cols or 80), 20)
        self.rows = max(int(rows or 24), 4)
        self.cursor_row = 0
        self.cursor_col = 0
        self.alternate_screen = False
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self._saved_primary_cursor = (0, 0)
        self._saved_alternate_cursor = (0, 0)
        self._primary = self._new_buffer()
        self._alternate = self._new_buffer()

    def _new_buffer(self) -> list[list[str]]:
        return [[" "] * self.cols for _ in range(self.rows)]

    def _blank_line(self) -> list[str]:
        return [" "] * self.cols

    def _buffer(self) -> list[list[str]]:
        return self._alternate if self.alternate_screen else self._primary

    def _saved_cursor(self) -> tuple[int, int]:
        return self._saved_alternate_cursor if self.alternate_screen else self._saved_primary_cursor

    def _set_saved_cursor(self, row: int, col: int) -> None:
        value = (max(0, min(self.rows - 1, row)), max(0, min(self.cols - 1, col)))
        if self.alternate_screen:
            self._saved_alternate_cursor = value
        else:
            self._saved_primary_cursor = value

    def _clamp_cursor(self) -> None:
        self.cursor_row = max(0, min(self.rows - 1, self.cursor_row))
        self.cursor_col = max(0, min(self.cols - 1, self.cursor_col))

    def _scroll_if_needed(self) -> None:
        buffer = self._buffer()
        while self.cursor_row >= self.rows:
            buffer.pop(0)
            buffer.append([" "] * self.cols)
            self.cursor_row -= 1

    def _line_feed(self) -> None:
        if self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            if self.cursor_row == self.scroll_bottom:
                self._scroll_region_up(1)
                return
        self.cursor_row += 1
        self._scroll_if_needed()

    def _set_cursor(self, row: int | None = None, col: int | None = None) -> None:
        if row is not None:
            self.cursor_row = max(0, min(self.rows - 1, row))
        if col is not None:
            self.cursor_col = max(0, min(self.cols - 1, col))
        self._clamp_cursor()

    def _clear_screen(self) -> None:
        buffer = self._buffer()
        for row in range(self.rows):
            buffer[row] = self._blank_line()
        self.cursor_row = 0
        self.cursor_col = 0
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1

    def _clear_screen_mode(self, mode: int = 0) -> None:
        buffer = self._buffer()
        if mode in {2, 3}:
            for row in range(self.rows):
                buffer[row] = self._blank_line()
            if mode == 2:
                self.cursor_row = 0
                self.cursor_col = 0
            return
        if mode == 1:
            for row in range(0, self.cursor_row):
                buffer[row] = self._blank_line()
            self._clear_line(1)
            return
        for row in range(self.cursor_row + 1, self.rows):
            buffer[row] = self._blank_line()
        self._clear_line(0)

    def _clear_line(self, mode: int = 0) -> None:
        buffer = self._buffer()
        line = buffer[self.cursor_row]
        if mode == 1:
            start, end = 0, self.cursor_col + 1
        elif mode == 2:
            start, end = 0, self.cols
        else:
            start, end = self.cursor_col, self.cols
        for index in range(max(0, start), min(self.cols, end)):
            line[index] = " "

    def _insert_blank_chars(self, count: int) -> None:
        count = max(1, min(int(count or 1), self.cols))
        line = self._buffer()[self.cursor_row]
        start = self.cursor_col
        preserved = line[start : self.cols - count]
        line[start:] = [" "] * count + preserved

    def _delete_chars(self, count: int) -> None:
        count = max(1, min(int(count or 1), self.cols))
        line = self._buffer()[self.cursor_row]
        start = self.cursor_col
        tail = line[min(self.cols, start + count) :]
        line[start:] = tail + [" "] * min(count, self.cols - start)

    def _erase_chars(self, count: int) -> None:
        count = max(1, min(int(count or 1), self.cols - self.cursor_col))
        line = self._buffer()[self.cursor_row]
        for index in range(self.cursor_col, min(self.cols, self.cursor_col + count)):
            line[index] = " "

    def _scroll_region_up(self, count: int = 1) -> None:
        count = max(1, int(count or 1))
        buffer = self._buffer()
        top = self.scroll_top
        bottom = self.scroll_bottom
        for _ in range(count):
            if top >= bottom:
                break
            buffer.pop(top)
            buffer.insert(bottom, self._blank_line())
        self.cursor_row = min(max(self.cursor_row, top), bottom)

    def _scroll_region_down(self, count: int = 1) -> None:
        count = max(1, int(count or 1))
        buffer = self._buffer()
        top = self.scroll_top
        bottom = self.scroll_bottom
        for _ in range(count):
            if top >= bottom:
                break
            buffer.pop(bottom)
            buffer.insert(top, self._blank_line())
        self.cursor_row = min(max(self.cursor_row, top), bottom)

    def _insert_lines(self, count: int = 1) -> None:
        if not (self.scroll_top <= self.cursor_row <= self.scroll_bottom):
            return
        count = max(1, min(int(count or 1), self.scroll_bottom - self.cursor_row + 1))
        buffer = self._buffer()
        for _ in range(count):
            buffer.pop(self.scroll_bottom)
            buffer.insert(self.cursor_row, self._blank_line())

    def _delete_lines(self, count: int = 1) -> None:
        if not (self.scroll_top <= self.cursor_row <= self.scroll_bottom):
            return
        count = max(1, min(int(count or 1), self.scroll_bottom - self.cursor_row + 1))
        buffer = self._buffer()
        for _ in range(count):
            buffer.pop(self.cursor_row)
            buffer.insert(self.scroll_bottom, self._blank_line())

    def _set_scroll_region(self, top: int | None, bottom: int | None) -> None:
        next_top = max(0, min(self.rows - 1, top if top is not None else 0))
        next_bottom = max(next_top, min(self.rows - 1, bottom if bottom is not None else self.rows - 1))
        self.scroll_top = next_top
        self.scroll_bottom = next_bottom
        self.cursor_row = self.scroll_top
        self.cursor_col = 0

    def _save_cursor(self) -> None:
        self._set_saved_cursor(self.cursor_row, self.cursor_col)

    def _restore_cursor(self) -> None:
        row, col = self._saved_cursor()
        self.cursor_row = row
        self.cursor_col = col
        self._clamp_cursor()

    def _reverse_index(self) -> None:
        if self.scroll_top <= self.cursor_row <= self.scroll_bottom and self.cursor_row == self.scroll_top:
            self._scroll_region_down(1)
            return
        self.cursor_row = max(0, self.cursor_row - 1)

    def _char_width(self, char: str) -> int:
        if not char:
            return 0
        if unicodedata.combining(char):
            return 0
        if unicodedata.east_asian_width(char) in {"W", "F"}:
            return 2
        return 1

    def _write_char(self, char: str) -> None:
        if not char:
            return
        if unicodedata.combining(char):
            return
        buffer = self._buffer()
        width = self._char_width(char)
        if width <= 0:
            return
        if width == 2 and self.cursor_col >= self.cols - 1:
            self.cursor_col = 0
            self.cursor_row += 1
            self._scroll_if_needed()
        buffer[self.cursor_row][self.cursor_col] = char
        if width == 2 and self.cursor_col + 1 < self.cols:
            buffer[self.cursor_row][self.cursor_col + 1] = ""
        self.cursor_col += width
        if self.cursor_col >= self.cols:
            self.cursor_col = 0
            self.cursor_row += 1
            self._scroll_if_needed()

    def _handle_csi(self, parameters: str, final: str) -> None:
        private_mode = parameters.startswith("?")
        parameter_text = parameters[1:] if private_mode else parameters
        parts = [part for part in parameter_text.split(";")] if parameter_text else []
        values = [int(part) if part.isdigit() else 0 for part in parts]

        if private_mode and final in {"h", "l"}:
            enable = final == "h"
            for value in values:
                if value == 1048:
                    if enable:
                        self._save_cursor()
                    else:
                        self._restore_cursor()
                elif value in {47, 1047, 1049}:
                    self.alternate_screen = enable
                    if enable:
                        self._alternate = self._new_buffer()
                    self.cursor_row = 0
                    self.cursor_col = 0
                elif value == 25:
                    continue
            return

        if final in {"H", "f"}:
            row = (values[0] - 1) if len(values) >= 1 and values[0] > 0 else 0
            col = (values[1] - 1) if len(values) >= 2 and values[1] > 0 else 0
            self._set_cursor(row=row, col=col)
            return
        if final == "s":
            self._save_cursor()
            return
        if final == "u":
            self._restore_cursor()
            return
        if final == "A":
            self._set_cursor(row=self.cursor_row - max(values[0] if values else 1, 1))
            return
        if final == "B":
            self._set_cursor(row=self.cursor_row + max(values[0] if values else 1, 1))
            return
        if final == "C":
            self._set_cursor(col=self.cursor_col + max(values[0] if values else 1, 1))
            return
        if final == "D":
            self._set_cursor(col=self.cursor_col - max(values[0] if values else 1, 1))
            return
        if final == "G":
            self._set_cursor(col=(values[0] - 1) if values and values[0] > 0 else 0)
            return
        if final == "d":
            self._set_cursor(row=(values[0] - 1) if values and values[0] > 0 else 0)
            return
        if final == "E":
            self.cursor_col = 0
            self._set_cursor(row=self.cursor_row + max(values[0] if values else 1, 1))
            return
        if final == "F":
            self.cursor_col = 0
            self._set_cursor(row=self.cursor_row - max(values[0] if values else 1, 1))
            return
        if final == "a":
            self._set_cursor(col=self.cursor_col + max(values[0] if values else 1, 1))
            return
        if final == "J":
            self._clear_screen_mode(values[0] if values else 0)
            return
        if final == "K":
            self._clear_line(values[0] if values else 0)
            return
        if final == "L":
            self._insert_lines(values[0] if values else 1)
            return
        if final == "M":
            self._delete_lines(values[0] if values else 1)
            return
        if final == "@":
            self._insert_blank_chars(values[0] if values else 1)
            return
        if final == "P":
            self._delete_chars(values[0] if values else 1)
            return
        if final == "X":
            self._erase_chars(values[0] if values else 1)
            return
        if final == "S":
            self._scroll_region_up(values[0] if values else 1)
            return
        if final == "T":
            self._scroll_region_down(values[0] if values else 1)
            return
        if final == "r":
            top = (values[0] - 1) if len(values) >= 1 and values[0] > 0 else 0
            bottom = (values[1] - 1) if len(values) >= 2 and values[1] > 0 else self.rows - 1
            self._set_scroll_region(top, bottom)
            return
        if final == "m":
            return

    def feed(self, text: str) -> None:
        index = 0
        length = len(text or "")
        while index < length:
            char = text[index]
            if char == "\x1b":
                if index + 1 < length and text[index + 1] == "[":
                    cursor = index + 2
                    while cursor < length and not ("@" <= text[cursor] <= "~"):
                        cursor += 1
                    if cursor < length:
                        self._handle_csi(text[index + 2:cursor], text[cursor])
                        index = cursor + 1
                        continue
                if index + 1 < length and text[index + 1] == "]":
                    cursor = index + 2
                    while cursor < length:
                        if text[cursor] == "\x07":
                            cursor += 1
                            break
                        if text[cursor] == "\x1b" and cursor + 1 < length and text[cursor + 1] == "\\":
                            cursor += 2
                            break
                        cursor += 1
                    index = cursor
                    continue
                if index + 1 < length and text[index + 1] == "7":
                    self._save_cursor()
                    index += 2
                    continue
                if index + 1 < length and text[index + 1] == "8":
                    self._restore_cursor()
                    index += 2
                    continue
                if index + 1 < length and text[index + 1] == "D":
                    self._line_feed()
                    index += 2
                    continue
                if index + 1 < length and text[index + 1] == "E":
                    self.cursor_col = 0
                    self._line_feed()
                    index += 2
                    continue
                if index + 1 < length and text[index + 1] == "M":
                    self._reverse_index()
                    index += 2
                    continue
                if index + 1 < length and text[index + 1] == "c":
                    self._clear_screen()
                    index += 2
                    continue
                index += 1
                continue

            if char == "\r":
                self.cursor_col = 0
                index += 1
                continue
            if char == "\n":
                self._line_feed()
                index += 1
                continue
            if char == "\b":
                self._set_cursor(col=self.cursor_col - 1)
                index += 1
                continue
            if char == "\t":
                next_stop = ((self.cursor_col // 4) + 1) * 4
                self._set_cursor(col=min(next_stop, self.cols - 1))
                index += 1
                continue
            if ord(char) < 32:
                index += 1
                continue
            self._write_char(char)
            index += 1

    def snapshot(self) -> str:
        lines = ["".join(row).rstrip() for row in self._buffer()]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines).strip("\n")


class BackgroundProcess:
    def __init__(
        self,
        command: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        interactive: bool = False,
        profile: str = "shell",
        profile_reason: str = "",
    ):
        self.command = command
        self.session_id = session_id
        self.run_id = run_id
        self.interactive = interactive
        self.profile = profile if profile in {"shell", "chat_cli"} else "shell"
        self.profile_reason = str(profile_reason or "")
        self.chat_cli_variant = _detect_chat_cli_variant(command) if self.profile == "chat_cli" else ""
        self.output_queue = queue.Queue()
        self.output_history = []
        self.is_running = True
        self.pty_win = None
        self.proc = None
        self.fd = None
        self.uses_tty = False
        self.cols = 80
        self.rows = 24
        self.screen = _SimpleTerminalScreen(self.cols, self.rows)
        self.screen_snapshot_cache = ""
        self.screen_version = 0
        self.last_reported_screen_version = -1
        self.last_screen_at = None
        self.raw_frame_version = 0
        self.last_reported_raw_frame_version = -1
        self.raw_bytes = 0
        self.last_raw_frame_at = None
        self.last_raw_frame_preview = ""
        self.raw_frame_history = deque(maxlen=_RAW_FRAME_HISTORY_LIMIT)
        self.started_at = time.time()
        self.last_output_at = self.started_at
        self.last_input_at = None
        self.completed_at = None
        self.return_code = None
        self.conversation_turns: list[dict[str, Any]] = []
        self.active_turn_index: int = -1
        self.current_turn_role: str | None = None
        self.current_turn_text = ""
        self.reported_offset = 0
        self.turn_completed = True
        self.current_turn_baseline = ""
        self.last_semantic_digest = ""
        self.last_semantic_view = ""
        self.last_semantic_update_at: float | None = None
        self.pending_input_echo = ""
        self.terminal_env_overrides = _build_terminal_env_overrides()
        self.command_diagnostics = _extend_command_diagnostics_for_terminal(
            _build_command_diagnostics_snapshot(command),
            env_overrides=self.terminal_env_overrides,
            uses_winpty=bool(sys.platform == "win32" and HAS_WINPTY),
        )
        
        if sys.platform == "win32" and HAS_WINPTY:
            self.pty_win = PTY(self.cols, self.rows)
            self.uses_tty = True
            self.pty_win.spawn("cmd.exe /q /d")
            time.sleep(0.5)
            _run_winpty_bootstrap(self.pty_win, self.terminal_env_overrides)
            _write_winpty_input(self.pty_win, f"{command}\n")
        elif sys.platform != "win32":
            pid, self.fd = pty.fork()
            if pid == 0:
                child_env = dict(os.environ)
                child_env.update(self.terminal_env_overrides)
                os.execvpe("sh", ["sh", "-c", command], child_env)
            else:
                self.proc = pid
                self.uses_tty = True
        else:
            # Fallback for Windows without pywinpty
            child_env = dict(os.environ)
            child_env.update(self.terminal_env_overrides)
            self.proc = subprocess.Popen(
                command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, env=child_env
            )

        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _chat_cli_turn(self) -> dict[str, Any] | None:
        if self.active_turn_index < 0 or self.active_turn_index >= len(self.conversation_turns):
            return None
        turn = self.conversation_turns[self.active_turn_index]
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            return None
        return turn

    def _append_chat_cli_user_turn(self, input_text: str, *, now: float | None = None) -> None:
        if self.profile != "chat_cli":
            return
        normalized_input = _normalize_chat_cli_text(input_text)
        if not normalized_input:
            return
        stamp = now or time.time()
        self.conversation_turns.append(
            {
                "role": "user",
                "text": normalized_input,
                "created_at": stamp,
                "completed": True,
            }
        )

    def _complete_chat_cli_turn(self, *, now: float | None = None, reason: str = "") -> None:
        turn = self._chat_cli_turn()
        if not turn:
            return
        stamp = now or time.time()
        turn["completed"] = True
        turn["completed_at"] = stamp
        if reason:
            turn["completed_reason"] = reason
        self.current_turn_text = str(turn.get("text") or "")
        self.reported_offset = int(turn.get("reported_offset") or 0)
        self.turn_completed = True
        self.current_turn_role = "assistant"
        self.last_semantic_digest = str(turn.get("digest") or self.last_semantic_digest or "")
        turn["pending_input_echo"] = ""
        self.pending_input_echo = ""

    def _start_chat_cli_assistant_turn(
        self,
        *,
        baseline_text: str,
        pending_input_echo: str = "",
        now: float | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        stamp = now or time.time()
        turn = {
            "role": "assistant",
            "text": "",
            "baseline_text": _normalize_chat_cli_text(baseline_text),
            "reported_offset": 0,
            "completed": False,
            "created_at": stamp,
            "pending_input_echo": _normalize_chat_cli_text(pending_input_echo),
            "reason": reason,
            "digest": "",
        }
        self.conversation_turns.append(turn)
        self.active_turn_index = len(self.conversation_turns) - 1
        self.current_turn_role = "assistant"
        self.current_turn_text = ""
        self.reported_offset = 0
        self.turn_completed = False
        self.current_turn_baseline = str(turn["baseline_text"])
        self.pending_input_echo = str(turn["pending_input_echo"])
        self.last_semantic_digest = ""
        return turn

    def _ensure_chat_cli_assistant_turn(self, *, baseline_text: str = "", now: float | None = None, reason: str = "") -> dict[str, Any]:
        turn = self._chat_cli_turn()
        if turn:
            return turn
        return self._start_chat_cli_assistant_turn(
            baseline_text=baseline_text,
            pending_input_echo="",
            now=now,
            reason=reason or "implicit_assistant_turn",
        )

    def _select_chat_cli_semantic_source(self, *, status: dict[str, Any], appended_output: str) -> str:
        if not self.uses_tty and str(appended_output or "").strip():
            return str(appended_output or "")
        stable_snapshot = str(status.get("stable_screen_snapshot") or "").strip()
        screen_snapshot = str(status.get("screen_snapshot") or "").strip()
        if stable_snapshot:
            return stable_snapshot
        if screen_snapshot:
            return screen_snapshot
        return str(appended_output or "")

    def _prepare_chat_cli_for_input(self, input_text: str, *, status_before: dict[str, Any] | None = None) -> None:
        if self.profile != "chat_cli":
            return
        normalized_input = _normalize_chat_cli_text(_decode_background_input_escapes(input_text))
        stamp = time.time()
        baseline_status = status_before or self.status_snapshot()
        baseline_text = _normalize_chat_cli_text(self._select_chat_cli_semantic_source(status=baseline_status, appended_output=""))
        current_turn = self._chat_cli_turn()
        if current_turn and not bool(current_turn.get("completed")):
            self._complete_chat_cli_turn(now=stamp, reason="user_input")
        self._append_chat_cli_user_turn(normalized_input, now=stamp)
        self._start_chat_cli_assistant_turn(
            baseline_text=baseline_text,
            pending_input_echo=normalized_input,
            now=stamp,
            reason="after_user_input",
        )

    def _update_chat_cli_semantic_state(
        self,
        *,
        status: dict[str, Any],
        appended_output: str,
        screen_changed: bool,
        raw_changed: bool,
    ) -> dict[str, Any]:
        now = time.time()
        observation_state = str(status.get("observation_state") or "idle")
        prompt_ready = bool(status.get("awaiting_input"))
        turn = self._chat_cli_turn()
        source_text = self._select_chat_cli_semantic_source(status=status, appended_output=appended_output)
        raw_semantic_view = _normalize_chat_cli_text(source_text)
        semantic_view = _strip_chat_cli_prompt_tail(
            _collapse_chat_cli_cumulative_lines(raw_semantic_view, variant=self.chat_cli_variant)
        )
        self.last_semantic_view = semantic_view

        has_user_turn = any(isinstance(item, dict) and item.get("role") == "user" for item in self.conversation_turns)
        should_bootstrap_assistant_turn = False
        if turn is None and semantic_view:
            if has_user_turn:
                should_bootstrap_assistant_turn = True
            elif not prompt_ready and not _terminal_snapshot_looks_like_prompt(semantic_view):
                should_bootstrap_assistant_turn = True
        if should_bootstrap_assistant_turn:
            turn = self._start_chat_cli_assistant_turn(
                baseline_text="",
                pending_input_echo="",
                now=now,
                reason="initial_observation",
            )

        delta_text = ""
        has_more = False
        if turn:
            baseline_text = str(turn.get("baseline_text") or "")
            candidate_text = _consume_chat_cli_semantic_suffix(baseline_text, semantic_view) if semantic_view else ""
            if not candidate_text and appended_output:
                candidate_text = _strip_chat_cli_prompt_tail(
                    _collapse_chat_cli_cumulative_lines(str(appended_output or ""), variant=self.chat_cli_variant)
                )
            candidate_text = _strip_chat_cli_input_echo(candidate_text, str(turn.get("pending_input_echo") or self.pending_input_echo or ""))
            candidate_text = _strip_chat_cli_prompt_tail(candidate_text)
            candidate_text = _collapse_chat_cli_cumulative_lines(candidate_text, variant=self.chat_cli_variant)

            merged_text = _merge_chat_cli_turn_text(str(turn.get("text") or ""), candidate_text)
            merged_text = _strip_chat_cli_input_echo(
                merged_text,
                str(turn.get("pending_input_echo") or self.pending_input_echo or ""),
            )
            if merged_text != str(turn.get("text") or ""):
                turn["text"] = merged_text
                turn["digest"] = _digest_chat_cli_text(merged_text)
                turn["updated_at"] = now
                self.last_semantic_update_at = now
                self.current_turn_text = merged_text
                self.last_semantic_digest = str(turn.get("digest") or "")
                self.turn_completed = False
            else:
                self.current_turn_text = str(turn.get("text") or "")
                self.last_semantic_digest = str(turn.get("digest") or self.last_semantic_digest or "")

            if prompt_ready:
                self._complete_chat_cli_turn(now=now, reason="awaiting_input")
            elif not self.is_running:
                self._complete_chat_cli_turn(now=now, reason="process_exit")
            elif (
                not bool(turn.get("completed"))
                and observation_state in {"idle", "awaiting_input"}
                and not screen_changed
                and not raw_changed
                and self.last_semantic_update_at is not None
                and (now - self.last_semantic_update_at) >= _CHAT_CLI_IDLE_COMPLETE_SECONDS
            ):
                self._complete_chat_cli_turn(now=now, reason="stable_idle")

            reported_offset = int(turn.get("reported_offset") or 0)
            current_text = str(turn.get("text") or "")
            pending_delta = current_text[reported_offset:]
            if pending_delta:
                delta_text = _slice_chat_cli_delta_chunk(pending_delta)
                turn["reported_offset"] = reported_offset + len(delta_text)
                self.reported_offset = int(turn["reported_offset"])
            else:
                self.reported_offset = reported_offset

            has_more = int(turn.get("reported_offset") or 0) < len(current_text) or not bool(turn.get("completed"))
            self.turn_completed = bool(turn.get("completed"))
            self.current_turn_role = "assistant"
            self.current_turn_baseline = str(turn.get("baseline_text") or "")

        return {
            "profile": self.profile,
            "turn_index": self.active_turn_index,
            "delta_text": delta_text,
            "has_more": bool(has_more),
            "turn_completed": bool(self.turn_completed),
            "awaiting_input": prompt_ready,
            "observation_state": observation_state,
            "screen_changed": bool(screen_changed),
            "raw_changed": bool(raw_changed),
            "semantic_view": semantic_view,
        }

    def _read_return_code(self) -> int | None:
        if self.proc is not None and hasattr(self.proc, "poll"):
            try:
                return self.proc.poll()
            except Exception:
                return None
        return None

    def _ingest_output(self, data: str) -> None:
        if not data:
            return
        now = time.time()
        self.last_output_at = now
        self.output_history.append(data)
        self.raw_frame_version += 1
        self.raw_bytes += len(data.encode("utf-8", "replace"))
        self.last_raw_frame_at = now
        self.last_raw_frame_preview = _preview_terminal_frame(data)
        self.raw_frame_history.append(
            {
                "ts": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "bytes": len(data.encode("utf-8", "replace")),
                "containsAnsi": _contains_terminal_escape(data),
                "preview": self.last_raw_frame_preview,
            }
        )
        if self.uses_tty:
            previous_snapshot = self.screen_snapshot_cache
            self.screen.feed(data)
            next_snapshot = self._compute_screen_snapshot()
            if next_snapshot != previous_snapshot:
                self.screen_snapshot_cache = next_snapshot
                self.last_screen_at = now
                self.screen_version += 1
        for char in data:
            self.output_queue.put(char)

    def _read_output(self):
        try:
            if sys.platform == "win32" and HAS_WINPTY:
                while self.is_running and self.pty_win.isalive():
                    try:
                        data = self.pty_win.read()
                        if data:
                            self._ingest_output(data)
                        else:
                            time.sleep(0.05)
                    except Exception:
                        break
            elif sys.platform != "win32" and self.fd is not None:
                while self.is_running:
                    try:
                        data = os.read(self.fd, 4096).decode('utf-8', 'replace')
                        if data:
                            self._ingest_output(data)
                        else:
                            break
                    except OSError:
                        break
            else:
                while self.is_running and self.proc:
                    char = self.proc.stdout.read(1)
                    if not char:
                        break
                    self._ingest_output(char)
        except Exception:
            pass
        finally:
            self.is_running = False
            self.completed_at = time.time()
            self.return_code = self._read_return_code()
            if self.return_code in (None, 0):
                _notify_skills_inventory_command_completed(self.command)

    def get_new_output(self) -> str:
        chars = []
        while not self.output_queue.empty():
            chars.append(self.output_queue.get())
        return "".join(chars)

    def discard_pending_output(self) -> str:
        return self.get_new_output()

    def has_unreported_screen_change(self) -> bool:
        return bool(self.uses_tty and self.screen_version > self.last_reported_screen_version)

    def mark_screen_reported(self) -> None:
        if self.uses_tty:
            self.last_reported_screen_version = self.screen_version

    def has_unreported_raw_frame_change(self) -> bool:
        return bool(self.raw_frame_version > self.last_reported_raw_frame_version)

    def mark_raw_frame_reported(self) -> None:
        self.last_reported_raw_frame_version = self.raw_frame_version

    def write_input(self, data: str):
        self.last_input_at = time.time()
        normalized_input = _decode_background_input_escapes(data)
        if sys.platform == "win32" and HAS_WINPTY:
            _write_winpty_input(self.pty_win, normalized_input)
        elif sys.platform != "win32" and self.fd is not None:
            normalized_data = _normalize_background_input(normalized_input)
            os.write(self.fd, normalized_data.encode('utf-8'))
        elif self.proc and self.proc.stdin:
            normalized_data = _normalize_background_input(normalized_input)
            self.proc.stdin.write(normalized_data)
            self.proc.stdin.flush()

    def _compute_screen_snapshot(self) -> str:
        if self.uses_tty:
            snapshot = self.screen.snapshot()
            if len(snapshot) > 6000:
                return snapshot[-6000:]
            return snapshot
        history = "".join(self.output_history[-8:]).strip()
        if len(history) > 4000:
            return history[-4000:]
        return history

    def _render_screen_snapshot(self) -> str:
        if self.uses_tty:
            if not self.screen_snapshot_cache:
                self.screen_snapshot_cache = self._compute_screen_snapshot()
            return _strip_terminal_bootstrap_noise(self.screen_snapshot_cache, self.terminal_env_overrides)
        return _strip_terminal_bootstrap_noise(self._compute_screen_snapshot(), self.terminal_env_overrides)

    def _derive_observation_state(self) -> str:
        if not (self.interactive and self.is_running):
            return "idle"
        snapshot = self._render_screen_snapshot()
        now = time.time()
        since_raw = now - self.last_raw_frame_at if self.last_raw_frame_at else float("inf")
        since_screen = now - self.last_screen_at if self.last_screen_at else float("inf")
        raw_recent = since_raw <= 0.9
        screen_recent = since_screen <= 0.9

        if _terminal_snapshot_looks_like_prompt(snapshot) and not _terminal_snapshot_looks_busy(snapshot):
            return "awaiting_input"
        if raw_recent and not screen_recent:
            return "render_stalled"
        if raw_recent or screen_recent:
            return "busy"
        if _terminal_snapshot_looks_busy(snapshot):
            return "busy"
        return "idle"

    def _derive_awaiting_input(self) -> bool:
        return self._derive_observation_state() == "awaiting_input"

    def status_snapshot(self) -> dict:
        tty_mode = "pty" if self.uses_tty else "pipe"
        observation_state = self._derive_observation_state()
        screen_snapshot = self._render_screen_snapshot()
        stable_screen_snapshot = _build_stable_terminal_snapshot(screen_snapshot)
        encoding_state, encoding_notes, text_encoding = _derive_terminal_encoding_status(
            screen_snapshot=screen_snapshot,
            raw_preview=self.last_raw_frame_preview,
            diagnostics=self.command_diagnostics,
        )
        return {
            "command_id": getattr(self, "command_id", None),
            "command": self.command,
            "session_id": self.session_id,
            "is_running": self.is_running,
            "uses_tty": self.uses_tty,
            "interactive": self.interactive,
            "profile": self.profile,
            "profile_reason": self.profile_reason,
            "chat_cli_variant": self.chat_cli_variant if self.profile == "chat_cli" else None,
            "tty_mode": tty_mode,
            "screen_mode": "terminal_screen" if self.uses_tty else "append_only",
            "screen_snapshot": screen_snapshot,
            "stable_screen_snapshot": stable_screen_snapshot,
            "screen_version": int(self.screen_version),
            "raw_frame_version": int(self.raw_frame_version),
            "raw_bytes": int(self.raw_bytes),
            "cursor": {
                "row": int(self.screen.cursor_row),
                "col": int(self.screen.cursor_col),
            } if self.uses_tty else None,
            "cols": self.cols,
            "rows": self.rows,
            "alternate_screen": bool(self.screen.alternate_screen) if self.uses_tty else False,
            "awaiting_input": observation_state == "awaiting_input",
            "observation_state": observation_state,
            "text_encoding": text_encoding,
            "encoding_state": encoding_state,
            "encoding_notes": encoding_notes,
            "last_screen_at": None if self.last_screen_at is None else datetime.fromtimestamp(self.last_screen_at, timezone.utc).isoformat(),
            "last_raw_frame_at": None if self.last_raw_frame_at is None else datetime.fromtimestamp(self.last_raw_frame_at, timezone.utc).isoformat(),
            "last_raw_frame_preview": self.last_raw_frame_preview,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "return_code": self.return_code,
            "seconds_since_output": round(max(0.0, time.time() - self.last_output_at), 2),
            "seconds_since_input": None
            if self.last_input_at is None
            else round(max(0.0, time.time() - self.last_input_at), 2),
            "chat_cli_turn_index": self.active_turn_index if self.profile == "chat_cli" else None,
            "chat_cli_turn_completed": self.turn_completed if self.profile == "chat_cli" else None,
            "chat_cli_total_chars": len(self.current_turn_text) if self.profile == "chat_cli" else None,
            "chat_cli_reported_chars": int(self.reported_offset) if self.profile == "chat_cli" else None,
            "chat_cli_last_digest": self.last_semantic_digest if self.profile == "chat_cli" else None,
            "command_diagnostics": dict(self.command_diagnostics),
        }

    def terminate(self):
        self.is_running = False
        if sys.platform == "win32" and HAS_WINPTY:
            del self.pty_win
        elif sys.platform != "win32" and self.proc is not None:
            try:
                import signal
                os.kill(self.proc, signal.SIGKILL)
            except Exception:
                pass
        elif self.proc:
            self.proc.terminate()

_bg_processes = {}


def _prune_stale_background_processes(*, max_age_seconds: int = _BACKGROUND_PROCESS_RETENTION_SECONDS) -> None:
    now = time.time()
    for command_id, bg_proc in list(_bg_processes.items()):
        if bg_proc.is_running:
            continue
        completed_at = getattr(bg_proc, "completed_at", None)
        if completed_at is None:
            continue
        if now - float(completed_at) > max_age_seconds:
            _bg_processes.pop(command_id, None)


def list_background_process_snapshots(
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    _prune_stale_background_processes()
    snapshots: list[dict] = []
    normalized_session_id = str(session_id or "").strip() or None
    normalized_run_id = str(run_id or "").strip() or None
    for command_id, bg_proc in list(_bg_processes.items()):
        process_session_id = str(getattr(bg_proc, "session_id", "") or "").strip() or None
        process_run_id = str(getattr(bg_proc, "run_id", "") or "").strip() or None
        if normalized_session_id and process_session_id and process_session_id != normalized_session_id:
            continue
        if normalized_run_id and not normalized_session_id and process_run_id and process_run_id != normalized_run_id:
            continue
        status = dict(bg_proc.status_snapshot())
        command = str(getattr(bg_proc, "command", "") or "").strip()
        command_preview = command if len(command) <= 240 else f"{command[:237]}..."
        return_code = status.get("return_code")
        is_running = bool(status.get("is_running"))
        process_status = "running" if is_running else (
            "failed" if return_code not in (None, 0, "0") else "completed" if status.get("completed_at") else "stopped"
        )
        snapshots.append({
            "processId": str(command_id),
            "commandId": str(command_id),
            "sessionId": process_session_id,
            "runId": process_run_id,
            "title": command.splitlines()[0][:96] if command else str(command_id),
            "commandPreview": command_preview,
            "command": command,
            "status": process_status,
            "interactive": bool(status.get("interactive")),
            "usesTty": bool(status.get("uses_tty")),
            "ttyMode": status.get("tty_mode"),
            "screenMode": status.get("screen_mode"),
            "screenSnapshot": status.get("screen_snapshot"),
            "stableScreenSnapshot": status.get("stable_screen_snapshot"),
            "screenVersion": status.get("screen_version"),
            "rawFrameVersion": status.get("raw_frame_version"),
            "rawBytes": status.get("raw_bytes"),
            "cursor": status.get("cursor"),
            "cols": status.get("cols"),
            "rows": status.get("rows"),
            "alternateScreen": bool(status.get("alternate_screen")),
            "awaitingInput": bool(status.get("awaiting_input")),
            "observationState": status.get("observation_state"),
            "textEncoding": status.get("text_encoding"),
            "encodingState": status.get("encoding_state"),
            "encodingNotes": status.get("encoding_notes"),
            "lastScreenAt": _normalize_status_timestamp(status.get("last_screen_at")),
            "lastRawFrameAt": _normalize_status_timestamp(status.get("last_raw_frame_at")),
            "lastRawFramePreview": status.get("last_raw_frame_preview"),
            "commandDiagnostics": status.get("command_diagnostics"),
            "canTerminate": True,
            "canInput": bool(status.get("interactive")),
            "startedAt": _normalize_status_timestamp(status.get("started_at") or time.time()),
            "completedAt": _normalize_status_timestamp(status.get("completed_at")),
            "secondsSinceOutput": status.get("seconds_since_output"),
            "secondsSinceInput": status.get("seconds_since_input"),
        })
    snapshots.sort(key=lambda item: str(item.get("startedAt") or ""))
    return snapshots

@tool
def start_background_command(
    command: str,
    profile: str = "auto",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Start a long-running or interactive system command in the background.
    
    USE THIS TOOL WHEN:
    1. The command requires interactive input (like `npx skills find` asking for selection).
    2. The command takes a long time and you want to poll it.
    3. Interactive CLIs such as `qwen`, `python`, `node`, `bash`, `pwsh/powershell`, or shells/REPLs.

    新主入口建议优先使用 `run_system_command(mode="session")`，这个工具保留为兼容薄壳。
    
    Returns a CommandId. You MUST subsequently use `read_background_output` to check its progress, 
    and `send_background_input` (like 'y\\n' or arrow keys) to interact with it.
    
    Arguments:
        command (str): The interactive or long-running command to execute.
        profile (str): auto、chat_cli 或 shell。chat_cli 适用于 AI CLI 的多轮对话语义增量读取。
    """
    try:
        launched = _launch_background_command(command, tool_call_id=tool_call_id, profile=profile)
        guidance = (
            "\nNext step: 使用 `read_background_output` 观察输出；若 CLI 等待输入，使用 "
            "`send_background_input` 发送文本或回车；结束时使用 `terminate_background_command`。"
            if launched["interactive"]
            else ""
        )
        initial_section = launched["initialOutput"] if launched["initialOutput"] else "[No initial output yet]"
        return (
            f"Command started in background with ID: {launched['commandId']}\n"
            f"Mode: {launched['mode']}\n"
            f"Profile: {launched['profile']}\n"
            f"TTY: {launched['tty']}\n"
            f"RunId: {launched['runId'] or 'n/a'}\n"
            f"Status: {json.dumps(launched['status'], ensure_ascii=False)}\n"
            f"Initial output:\n{initial_section}{guidance}"
        )
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error starting background command: {e}"


@tool
def run_system_command(
    command: str,
    mode: str = "auto",
    profile: str = "auto",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Run a system command through a unified command surface.

    mode=auto:
    - 短命令/非交互命令直接同步执行并返回结果
    - 交互式或长驻命令返回 redirect，要求使用 command_session_broker(mode=start)

    mode=sync:
    - 强制同步执行，适合短命令

    mode=session:
    - 兼容模式：强制后台/交互模式，建议新调用改用 command_session_broker(mode=start)

    profile:
    - auto: 自动识别 shell / chat_cli
    - chat_cli: 把 AI CLI 作为对话终端处理，只向 supervisor 暴露最新语义增量
    - shell: 普通终端模式
    """
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode not in {"auto", "sync", "session"}:
        return "Error: mode 必须是 auto、sync 或 session。"
    try:
        normalized_profile = _normalize_background_command_profile(profile)
    except ValueError as exc:
        return f"Error: {exc}"

    interactive_reason = _detect_interactive_command(command)
    session_reason = _detect_session_preferred_command(command)
    prefer_session = interactive_reason is not None or session_reason is not None
    effective_mode = normalized_mode
    if normalized_mode == "auto":
        if prefer_session:
            return json.dumps(
                {
                    "ok": True,
                    "mode": "auto",
                    "kind": "command_session_redirect",
                    "summary": "检测到命令更适合命令会话 broker。",
                    "reason": interactive_reason or session_reason or "命令更适合后台会话模式",
                    "redirect": {
                        "tool": "command_session_broker",
                        "args": {
                            "mode": "start",
                            "command": command,
                            "profile": normalized_profile,
                        },
                    },
                },
                ensure_ascii=False,
            )
        effective_mode = "sync"

    if effective_mode == "sync":
        return execute_system_command.func(command=command, tool_call_id=tool_call_id)

    if effective_mode == "session":
        try:
            launched = _launch_background_command(command, tool_call_id=tool_call_id, profile=normalized_profile)
            payload = {
                "kind": "command_session",
                "mode": "session",
                "commandId": launched["commandId"],
                "sessionId": launched["commandId"],
                "interactive": bool(launched["interactive"]),
                "profile": launched["profile"],
                "tty": launched["tty"],
                "runId": launched["runId"],
                "reason": interactive_reason or session_reason or "显式 session 模式",
            }
            if launched["initialOutput"]:
                payload["initialOutput"] = launched["initialOutput"]
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            _raise_runtime_governance_exception_if_needed(exc)
            return f"Error starting session command: {exc}"

    return "Error: 未能解析命令执行模式。"


def _resolve_command_session_process(
    *,
    command_id: str = "",
    session_id: str = "",
) -> tuple[str, Any | None]:
    _prune_stale_background_processes()
    normalized = str(command_id or session_id or "").strip()
    if not normalized:
        return "", None
    return normalized, _bg_processes.get(normalized)


def _command_session_state_from_status(status: dict[str, Any]) -> str:
    is_running = bool(status.get("is_running"))
    if is_running:
        if bool(status.get("awaiting_input")):
            return "awaiting_input"
        observation_state = str(status.get("observation_state") or "").strip().lower()
        if observation_state == "render_stalled":
            return "render_stalled"
        return "running"
    return_code = status.get("return_code")
    return "failed" if return_code not in (None, 0, "0") else "completed"


def _command_session_summary_for_state(
    *,
    mode: str,
    state: str,
    interactive: bool,
    delta_text: str = "",
    terminated: bool = False,
) -> str:
    if mode == "start":
        return "已启动交互式命令会话。" if interactive else "已启动后台命令会话。"
    if mode == "terminate":
        return "命令会话已终止。" if terminated else "命令会话终止请求已发送。"
    if delta_text:
        if state == "awaiting_input":
            return "终端有新增输出，当前已等待输入。"
        return "终端有新增输出。"
    if state == "awaiting_input":
        return "终端当前等待输入。"
    if state == "render_stalled":
        return "终端有原始数据，但屏幕尚未稳定刷新。"
    if state == "completed":
        return "命令会话已完成。"
    if state == "failed":
        return "命令会话已异常结束。"
    return "命令会话仍在运行。"


def _command_session_recommended_next_action(
    *,
    mode: str,
    state: str,
    awaiting_input: bool,
    has_more: bool,
) -> str:
    if mode == "terminate" or state in {"completed", "failed"}:
        return "none"
    if awaiting_input:
        return "input"
    if has_more or state in {"running", "render_stalled"}:
        return "observe"
    return "wait_then_observe"


def _command_session_preview_text(value: str, *, limit: int = 1200) -> tuple[str, bool]:
    normalized = str(value or "").strip()
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit].rstrip(), True


def _command_session_debug_payload(
    *,
    status: dict[str, Any],
    screen_preview: str = "",
    raw_frame_preview: str = "",
    delta_text: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
    }
    if screen_preview:
        payload["screenPreview"] = screen_preview
    if raw_frame_preview:
        payload["rawFramePreview"] = raw_frame_preview
    if delta_text:
        payload["deltaPreview"] = delta_text
    return payload


def _command_session_payload(
    *,
    mode: str,
    session_id: str,
    command_id: str,
    summary: str,
    recommended_next_action: str,
    ok: bool = True,
    **extra: Any,
) -> str:
    payload: dict[str, Any] = {
        "ok": ok,
        "mode": mode,
        "kind": "command_session",
        "sessionId": session_id,
        "commandId": command_id,
        "summary": summary,
        "recommendedNextAction": recommended_next_action,
    }
    payload.update(extra)
    return json.dumps(
        {key: value for key, value in payload.items() if value not in (None, "", [], {})},
        ensure_ascii=False,
    )


def _delegation_broker_payload(
    *,
    mode: str,
    summary: str,
    items: list[dict[str, Any]] | None = None,
    recommended_next_action: str = "none",
    ok: bool = True,
    error: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "ok": ok,
        "mode": mode,
        "summary": summary,
        "items": list(items or []),
        "recommendedNextAction": recommended_next_action,
    }
    if error:
        payload["error"] = error
    return json.dumps(
        {key: value for key, value in payload.items() if value not in (None, "", [], {})},
        ensure_ascii=False,
    )


def _delegation_external_worker_descriptors() -> list[dict[str, Any]]:
    supervisor_config = storage.get_supervisor_config() or {}
    delegation = dict(supervisor_config.get("delegation") or {})
    descriptors = normalize_external_worker_descriptors(delegation.get("externalWorkers"))
    return descriptors or default_external_worker_descriptors()


def _delegation_acceptance_hint(value: Any = None) -> str:
    normalized = str(value or "").strip()
    return normalized or "Supervisor must explicitly accept, retry, or ignore this delegated result."


def _delegation_trace_ref(*, run_id: str | None, invocation_id: str | None, branch_index: int | None = None, command_id: str | None = None) -> dict[str, Any]:
    trace: dict[str, Any] = {}
    if str(run_id or "").strip():
        trace["runId"] = str(run_id).strip()
    if str(invocation_id or "").strip():
        trace["invocationId"] = str(invocation_id).strip()
    if branch_index is not None:
        trace["branchIndex"] = int(branch_index)
    if str(command_id or "").strip():
        trace["commandId"] = str(command_id).strip()
    return trace


def _delegation_planner_context(plan: Any, task_brief: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan:
        return None
    task_id = str(task_brief.get("taskBriefId") or "").strip()
    dependency_rows: list[dict[str, Any]] = []
    for row in list(plan.get("dependencies") or []):
        if not isinstance(row, dict):
            continue
        if task_id and str(row.get("taskBriefId") or "").strip() not in {"", task_id}:
            continue
        dependency_rows.append(
            {
                "taskBriefId": str(row.get("taskBriefId") or "").strip(),
                "dependsOn": [
                    str(item).strip()
                    for item in list(row.get("dependsOn") or row.get("dependency") or [])
                    if str(item).strip()
                ],
            }
        )
    return {
        "planId": str(plan.get("planId") or "").strip(),
        "executionStrategy": str(plan.get("executionStrategy") or "").strip(),
        "planSummary": str(plan.get("planSummary") or "").strip(),
        "globalAcceptanceContract": plan.get("globalAcceptanceContract")
        if isinstance(plan.get("globalAcceptanceContract"), dict)
        else str(plan.get("globalAcceptanceContract") or "").strip(),
        "riskFlags": [
            str(item).strip()
            for item in list(plan.get("riskFlags") or [])
            if str(item).strip()
        ],
        "dependencies": dependency_rows,
        "taskCount": len(list(plan.get("taskBriefs") or [])),
    }


def _delegation_compact_item(
    *,
    delegation_id: str,
    task_brief: dict[str, Any],
    lane: str,
    target_id: str,
    target_label: str,
    status: str,
    trace_ref: dict[str, Any] | None = None,
    artifact_refs: list[Any] | None = None,
    local_self_check: str | None = None,
    acceptance_hint: str | None = None,
    worker_type: str | None = None,
    command_session: dict[str, Any] | None = None,
    result_schema_matched: bool | None = None,
    selection_reason: str | None = None,
    selection_confidence: float | None = None,
    match_signals: list[Any] | None = None,
    compat_source: str | None = None,
    supervisor_acceptance: dict[str, Any] | None = None,
    adopted_artifact_refs: list[Any] | None = None,
    auto_dispatch_source: str | None = None,
    invocation_id: str | None = None,
    branch_index: int | None = None,
    workset_dispatch_decision: dict[str, Any] | None = None,
    workset_conflict_group: list[Any] | None = None,
    engineering_capsule_attached: bool | None = None,
    dispatch_blocked_reason: str | None = None,
    repair_suggestion: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "delegationId": delegation_id,
        "taskBriefId": str(task_brief.get("taskBriefId") or "").strip(),
        "taskGoal": str(task_brief.get("goal") or "").strip(),
        "writeSet": [str(item).strip() for item in list(task_brief.get("writeSet") or []) if str(item).strip()],
        "readSet": [str(item).strip() for item in list(task_brief.get("readSet") or []) if str(item).strip()],
        "lane": lane,
        "targetId": target_id,
        "targetLabel": target_label,
        "agentId": target_id,
        "agentName": target_label,
        "status": status,
        "traceRef": trace_ref or {},
        "artifactRefs": list(artifact_refs or []),
        "localSelfCheck": local_self_check,
        "acceptanceHint": _delegation_acceptance_hint(acceptance_hint),
        "supervisorAcceptance": supervisor_acceptance or {
            "status": "pending",
            "summary": "Supervisor has not accepted, retried, or ignored this delegated result yet.",
        },
        "adoptedArtifactRefs": list(adopted_artifact_refs or []),
        "selectionReason": selection_reason,
        "selectionConfidence": selection_confidence,
        "matchSignals": list(match_signals or []),
        "compatSource": compat_source,
        "autoDispatchSource": auto_dispatch_source,
        "invocationId": invocation_id,
        "branchIndex": branch_index,
    }
    if workset_dispatch_decision:
        decision = dict(workset_dispatch_decision)
        decision.setdefault("delegationId", delegation_id)
        decision.setdefault("taskBriefId", item.get("taskBriefId"))
        item["worksetDispatchDecision"] = decision
        item["worksetConflictGroup"] = list(workset_conflict_group or decision.get("worksetConflictGroup") or [])
        item["dispatchBlockedReason"] = dispatch_blocked_reason or (
            str(decision.get("reason") or "").strip()
            if bool(decision.get("blocked"))
            else None
        )
        item["repairSuggestion"] = repair_suggestion or str(decision.get("repairSuggestion") or "").strip() or None
    if engineering_capsule_attached is not None:
        item["engineeringCapsuleAttached"] = bool(engineering_capsule_attached)
    if isinstance(task_brief.get("engineeringTaskCapsule"), dict) and task_brief.get("engineeringTaskCapsule"):
        item["engineeringTaskCapsule"] = task_brief.get("engineeringTaskCapsule")
    if worker_type:
        item["workerType"] = worker_type
    if command_session:
        item["commandSession"] = command_session
    if result_schema_matched is not None:
        item["resultSchemaMatched"] = bool(result_schema_matched)
    if error:
        item["error"] = error
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


@tool
def command_session_broker(
    mode: str = "observe",
    command: str = "",
    session_id: str = "",
    command_id: str = "",
    input_text: str = "",
    profile: str = "auto",
    debug: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Unified command-session broker for long-running or interactive CLI work: start, observe, input, or terminate a session with compact JSON by default.

    Modes:
    - start: launch a long-running or interactive command session
    - observe: read the latest delta and detect whether input is needed
    - input: send input into the active session
    - terminate: stop the session

    Usage guidance:
    - Keep using run_system_command for short synchronous commands; run_system_command(mode=auto) redirects long-running or interactive commands here.
    - Use this broker for long-running tasks, interactive CLIs/REPLs, AI CLIs, and server/dev processes.
    - Treat summary, recommendedNextAction, awaitingInput, hasMore, state/status, and returnCode as the compact truth for the next step.
    - profile=auto may enable chat_cli semantics for known AI CLIs so observe reports the latest semantic delta instead of replaying the whole screen.
    - If awaitingInput=true, send follow-up text with mode=input; if hasMore=true, observe again after a short wait.
    - Use debug=true only for raw terminal diagnostics such as screenPreview, rawFramePreview, render_stalled, or encodingState/mojibake.
    """
    normalized_mode = str(mode or "observe").strip().lower()
    if normalized_mode not in {"start", "observe", "input", "terminate"}:
        return _command_session_payload(
            mode=normalized_mode or "unknown",
            session_id="",
            command_id="",
            ok=False,
            summary=f"Unsupported command_session_broker mode: {normalized_mode}",
            recommended_next_action="none",
            error=f"Unsupported command_session_broker mode: {normalized_mode}",
        )

    try:
        if normalized_mode == "start":
            normalized_command = str(command or "").strip()
            if not normalized_command:
                return _command_session_payload(
                    mode=normalized_mode,
                    session_id="",
                    command_id="",
                    ok=False,
                    summary="command_session_broker(mode=start) 需要提供 command。",
                    recommended_next_action="none",
                    error="missing_command",
                )
            try:
                normalized_profile = _normalize_background_command_profile(profile)
            except ValueError as exc:
                return _command_session_payload(
                    mode=normalized_mode,
                    session_id="",
                    command_id="",
                    ok=False,
                    summary=str(exc),
                    recommended_next_action="none",
                    error="invalid_profile",
                )
            launched = _launch_background_command(
                normalized_command,
                tool_call_id=tool_call_id,
                profile=normalized_profile,
            )
            status = dict(launched.get("status") or {})
            state = _command_session_state_from_status(status)
            initial_preview, initial_truncated = _command_session_preview_text(
                str(launched.get("initialOutput") or "").strip()
            )
            debug_payload = None
            if debug:
                debug_payload = _command_session_debug_payload(
                    status=status,
                    screen_preview=str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip(),
                    raw_frame_preview=str(status.get("last_raw_frame_preview") or "").strip(),
                    delta_text=initial_preview,
                )
            return _command_session_payload(
                mode=normalized_mode,
                session_id=str(launched.get("commandId") or ""),
                command_id=str(launched.get("commandId") or ""),
                summary=_command_session_summary_for_state(
                    mode=normalized_mode,
                    state=state,
                    interactive=bool(launched.get("interactive")),
                    delta_text=initial_preview,
                ),
                recommended_next_action=_command_session_recommended_next_action(
                    mode=normalized_mode,
                    state=state,
                    awaiting_input=bool(status.get("awaiting_input")),
                    has_more=bool(launched.get("interactive")),
                ),
                interactive=bool(launched.get("interactive")),
                profile=launched.get("profile"),
                reason=launched.get("profileReason") or launched.get("reason") or _detect_interactive_command(normalized_command) or _detect_session_preferred_command(normalized_command),
                awaitingInput=bool(status.get("awaiting_input")),
                state=state,
                initialPreview=initial_preview or None,
                initialPreviewTruncated=initial_truncated if initial_preview else None,
                linkedProcess={
                    "processId": str(launched.get("commandId") or ""),
                    "commandId": str(launched.get("commandId") or ""),
                    "sessionId": str(launched.get("commandId") or ""),
                    "chatSessionId": launched.get("sessionId"),
                    "runId": launched.get("runId"),
                },
                runId=launched.get("runId"),
                debug=debug_payload,
            )

        resolved_session_id, bg_proc = _resolve_command_session_process(
            command_id=command_id,
            session_id=session_id,
        )
        if not resolved_session_id or bg_proc is None:
            return _command_session_payload(
                mode=normalized_mode,
                session_id=resolved_session_id,
                command_id=resolved_session_id,
                ok=False,
                summary="未找到对应的命令会话。",
                recommended_next_action="start",
                error="session_not_found",
            )

        if normalized_mode == "observe":
            new_output = bg_proc.get_new_output()
            status = bg_proc.status_snapshot()
            screen_changed = bool(bg_proc.has_unreported_screen_change())
            raw_changed = bool(bg_proc.has_unreported_raw_frame_change())
            delta_text = str(new_output or "").strip()
            has_more = False
            if bg_proc.profile == "chat_cli":
                semantic_state = bg_proc._update_chat_cli_semantic_state(
                    status=status,
                    appended_output=new_output,
                    screen_changed=screen_changed,
                    raw_changed=raw_changed,
                )
                delta_text = str(semantic_state.get("delta_text") or "").strip()
                has_more = bool(semantic_state.get("has_more"))
            else:
                has_more = bool(bg_proc.is_running and (screen_changed or raw_changed or delta_text) and not status.get("awaiting_input"))
            if screen_changed:
                bg_proc.mark_screen_reported()
            if raw_changed:
                bg_proc.mark_raw_frame_reported()
            state = _command_session_state_from_status(status)
            delta_preview, delta_truncated = _command_session_preview_text(delta_text)
            screen_preview = str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip()
            raw_frame_preview = str(status.get("last_raw_frame_preview") or "").strip()
            debug_payload = None
            if debug:
                debug_payload = _command_session_debug_payload(
                    status=status,
                    screen_preview=screen_preview,
                    raw_frame_preview=raw_frame_preview,
                    delta_text=delta_preview,
                )
            return _command_session_payload(
                mode=normalized_mode,
                session_id=resolved_session_id,
                command_id=resolved_session_id,
                summary=_command_session_summary_for_state(
                    mode=normalized_mode,
                    state=state,
                    interactive=bool(status.get("interactive")),
                    delta_text=delta_preview,
                ),
                recommended_next_action=_command_session_recommended_next_action(
                    mode=normalized_mode,
                    state=state,
                    awaiting_input=bool(status.get("awaiting_input")),
                    has_more=has_more,
                ),
                state=state,
                deltaText=delta_preview or None,
                deltaTruncated=delta_truncated if delta_preview else None,
                awaitingInput=bool(status.get("awaiting_input")),
                hasMore=has_more,
                returnCode=status.get("return_code"),
                debug=debug_payload,
            )

        if normalized_mode == "input":
            if not bg_proc.is_running:
                status = bg_proc.status_snapshot()
                return _command_session_payload(
                    mode=normalized_mode,
                    session_id=resolved_session_id,
                    command_id=resolved_session_id,
                    ok=False,
                    summary="命令会话已经结束，无法继续输入。",
                    recommended_next_action="none",
                    state=_command_session_state_from_status(status),
                    returnCode=status.get("return_code"),
                    error="session_not_running",
                )
            normalized_input = _decode_background_input_escapes(input_text)
            if not normalized_input:
                return _command_session_payload(
                    mode=normalized_mode,
                    session_id=resolved_session_id,
                    command_id=resolved_session_id,
                    ok=False,
                    summary="command_session_broker(mode=input) 需要提供 input_text。",
                    recommended_next_action="none",
                    error="missing_input_text",
                )
            previous_status = bg_proc.status_snapshot()
            previous_screen_version = int(previous_status.get("screen_version") or 0)
            previous_raw_frame_version = int(previous_status.get("raw_frame_version") or 0)
            bg_proc.discard_pending_output()
            bg_proc._prepare_chat_cli_for_input(normalized_input, status_before=previous_status)
            bg_proc.write_input(normalized_input)
            time.sleep(0.5)
            new_output = bg_proc.get_new_output()
            status = bg_proc.status_snapshot()
            screen_changed = int(status.get("screen_version") or 0) > previous_screen_version
            raw_changed = int(status.get("raw_frame_version") or 0) > previous_raw_frame_version
            delta_text = str(new_output or "").strip()
            has_more = False
            if bg_proc.profile == "chat_cli":
                semantic_state = bg_proc._update_chat_cli_semantic_state(
                    status=status,
                    appended_output=new_output,
                    screen_changed=screen_changed,
                    raw_changed=raw_changed,
                )
                delta_text = str(semantic_state.get("delta_text") or "").strip()
                has_more = bool(semantic_state.get("has_more"))
            else:
                has_more = bool(bg_proc.is_running and (screen_changed or raw_changed or delta_text) and not status.get("awaiting_input"))
            if screen_changed:
                bg_proc.mark_screen_reported()
            if raw_changed:
                bg_proc.mark_raw_frame_reported()
            state = _command_session_state_from_status(status)
            delta_preview, delta_truncated = _command_session_preview_text(delta_text)
            input_preview, input_truncated = _command_session_preview_text(normalized_input, limit=200)
            debug_payload = None
            if debug:
                debug_payload = _command_session_debug_payload(
                    status=status,
                    screen_preview=str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip(),
                    raw_frame_preview=str(status.get("last_raw_frame_preview") or "").strip(),
                    delta_text=delta_preview,
                )
            return _command_session_payload(
                mode=normalized_mode,
                session_id=resolved_session_id,
                command_id=resolved_session_id,
                summary=_command_session_summary_for_state(
                    mode=normalized_mode,
                    state=state,
                    interactive=bool(status.get("interactive")),
                    delta_text=delta_preview,
                ).replace("终端", "已发送输入后终端", 1),
                recommended_next_action=_command_session_recommended_next_action(
                    mode=normalized_mode,
                    state=state,
                    awaiting_input=bool(status.get("awaiting_input")),
                    has_more=has_more,
                ),
                state=state,
                acceptedInputPreview=input_preview,
                acceptedInputTruncated=input_truncated if input_preview else None,
                deltaText=delta_preview or None,
                deltaTruncated=delta_truncated if delta_preview else None,
                awaitingInput=bool(status.get("awaiting_input")),
                hasMore=has_more,
                returnCode=status.get("return_code"),
                debug=debug_payload,
            )

        status_before = bg_proc.status_snapshot()
        bg_proc.terminate()
        time.sleep(0.15)
        final_output = bg_proc.get_new_output()
        status = bg_proc.status_snapshot()
        _bg_processes.pop(resolved_session_id, None)
        final_preview, final_truncated = _command_session_preview_text(str(final_output or "").strip())
        debug_payload = None
        if debug:
            debug_payload = _command_session_debug_payload(
                status=status or status_before,
                screen_preview=str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip(),
                raw_frame_preview=str(status.get("last_raw_frame_preview") or "").strip(),
                delta_text=final_preview,
            )
        return _command_session_payload(
            mode=normalized_mode,
            session_id=resolved_session_id,
            command_id=resolved_session_id,
            summary=_command_session_summary_for_state(
                mode=normalized_mode,
                state="completed",
                interactive=bool(status.get("interactive")),
                delta_text=final_preview,
                terminated=True,
            ),
            recommended_next_action="none",
            terminated=True,
            state=_command_session_state_from_status(status),
            returnCode=status.get("return_code"),
            finalPreview=final_preview or None,
            finalPreviewTruncated=final_truncated if final_preview else None,
            debug=debug_payload,
        )
    except Exception as exc:
        _raise_runtime_governance_exception_if_needed(exc)
        normalized_session = str(command_id or session_id or "").strip()
        return _command_session_payload(
            mode=normalized_mode,
            session_id=normalized_session,
            command_id=normalized_session,
            ok=False,
            summary=str(exc),
            recommended_next_action="none",
            error=str(exc),
        )


@tool
def delegation_broker(
    mode: str = "observe",
    tasks: list[dict[str, Any]] | None = None,
    delegation_id: str = "",
    followup: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Unified delegation broker for local subagents and external workers: dispatch, observe, resume, or interrupt delegated work."""
    normalized_mode = str(mode or "observe").strip().lower()
    if normalized_mode not in {"dispatch", "observe", "resume", "interrupt"}:
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode or "unknown",
                            ok=False,
                            summary=f"Unsupported delegation_broker mode: {normalized_mode}",
                            recommended_next_action="none",
                            error="unsupported_mode",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    base_state = dict(state or {})
    base_messages = list(base_state.get("messages") or [])
    base_todos = list(base_state.get("todos") or [])
    base_contexts = list(base_state.get("delegation_contexts") or [])
    planner_plan = dict(base_state.get("planner_plan") or {}) if isinstance(base_state.get("planner_plan"), dict) else {}
    inherited_context = dict(base_state.get("current_route_context") or {})
    if not inherited_context:
        inherited_context = latest_delegation_context(base_contexts, agent_id=None)

    if normalized_mode == "dispatch":
        normalized_tasks = normalize_task_briefs(tasks or [])
        if not normalized_tasks:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="delegation_broker(mode=dispatch) 需要提供 tasks。",
                                recommended_next_action="none",
                                error="missing_tasks",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )

        invocation_id = f"delegation_{uuid.uuid4().hex[:12]}"
        loaded_agents = storage.get_all_agents()
        external_descriptors = _delegation_external_worker_descriptors()
        dispatch_source = str(base_state.get("delegationDispatchSource") or inherited_context.get("delegationDispatchSource") or "").strip()
        compat_source = str(base_state.get("delegationCompatSource") or inherited_context.get("delegationCompatSource") or "").strip()
        auto_dispatch_source = dispatch_source if dispatch_source.startswith("planner_auto") else ""
        workset_decisions = build_workset_dispatch_decisions(
            normalized_tasks,
            auto_dispatch=bool(auto_dispatch_source),
            decision_source="planner_auto" if auto_dispatch_source else "supervisor_manual",
        )
        blocked_decisions = [item for item in workset_decisions if bool(item.get("blocked"))]
        if blocked_decisions:
            blocked_items: list[dict[str, Any]] = []
            for index, task_brief in enumerate(normalized_tasks):
                decision = workset_decisions[index] if index < len(workset_decisions) else {}
                lane_hint = str(task_brief.get("executionLaneHint") or "auto").strip().lower() or "auto"
                blocked_items.append(
                    _delegation_compact_item(
                        delegation_id=f"blocked::workset::{str(task_brief.get('taskBriefId') or index)}::{lane_hint}",
                        task_brief=task_brief,
                        lane="external_worker" if lane_hint == "external_worker" else "subagent",
                        target_id=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or "unassigned").strip() or "unassigned",
                        target_label=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or "unassigned").strip() or "unassigned",
                        status="blocked",
                        invocation_id=invocation_id,
                        branch_index=index,
                        trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                        compat_source=compat_source or None,
                        auto_dispatch_source=auto_dispatch_source or None,
                        workset_dispatch_decision=decision,
                        workset_conflict_group=list(decision.get("worksetConflictGroup") or []),
                        engineering_capsule_attached=bool(decision.get("engineeringCapsuleAttached")),
                        dispatch_blocked_reason=str(decision.get("reason") or "workset_dispatch_blocked").strip(),
                        repair_suggestion=str(decision.get("repairSuggestion") or "Repair planner writeSet before automatic dispatch.").strip(),
                        error="workset_dispatch_blocked",
                    )
                )
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=(
                                    "delegation_broker blocked planner auto-dispatch because Engineering Lane "
                                    "work-set governance found missing or conflicting write sets."
                                ),
                                items=blocked_items,
                                recommended_next_action="repair_plan",
                                error="workset_dispatch_blocked",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )
        sends: list[Send] = []
        items: list[dict[str, Any]] = []
        parallel_results: list[dict[str, Any]] = []

        for index, task_brief in enumerate(normalized_tasks):
            workset_decision = workset_decisions[index] if index < len(workset_decisions) else {}
            task_query = task_brief_query_text(task_brief) or str(task_brief.get("goal") or "").strip()
            task_goal = str(task_brief.get("goal") or "").strip() or task_query or f"Task {index + 1}"
            lane_hint = str(task_brief.get("executionLaneHint") or "auto").strip().lower() or "auto"
            local_agent = None
            local_diagnostics: dict[str, Any] = {}
            external_diagnostics: dict[str, Any] = {}
            if lane_hint in {"subagent", "auto"}:
                local_agent, local_diagnostics = choose_best_local_agent_with_diagnostics(task_brief, loaded_agents)
            external_worker = None
            if lane_hint == "external_worker":
                external_worker, external_diagnostics = choose_best_external_worker_with_diagnostics(task_brief, external_descriptors)
            elif lane_hint == "auto" and local_agent is None:
                external_worker, external_diagnostics = choose_best_external_worker_with_diagnostics(task_brief, external_descriptors)

            if local_agent and lane_hint != "external_worker":
                agent_id = str(local_agent.get("id") or "").strip()
                agent_name = str(local_agent.get("name") or agent_id).strip() or agent_id
                delegation_id_value = make_local_delegation_id(
                    invocation_id=invocation_id,
                    branch_index=index,
                    task_brief_id=str(task_brief.get("taskBriefId") or ""),
                    agent_id=agent_id,
                )
                branch_context = build_delegation_context(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    query=task_query,
                    mode="parallel" if len(normalized_tasks) > 1 else "serial",
                    source_runtime_kind=inherited_context.get("sourceRuntimeKind"),
                    selected_skill_ids=inherited_context.get("selectedSkillIds"),
                    selected_skill_names=inherited_context.get("selectedSkillNames"),
                    selected_skill_entries=inherited_context.get("selectedSkillEntries"),
                    skill_root_descriptors=inherited_context.get("skillRootDescriptors"),
                    selected_mcp_tools=inherited_context.get("selectedMcpTools"),
                    selected_plugin_host_tools=inherited_context.get("selectedPluginHostTools"),
                    selected_baseline_tools=inherited_context.get("selectedBaselineTools"),
                    prompt_addition=inherited_context.get("promptAddition"),
                    invocation_id=invocation_id,
                    task_brief=task_brief,
                    planner_context=_delegation_planner_context(planner_plan, task_brief),
                )
                branch_state = dict(base_state)
                branch_state["messages"] = base_messages + [
                    HumanMessage(content=f"[Supervisor Delegated Task to {agent_name}]:\n{task_query or task_goal}")
                ]
                branch_state["todos"] = list(base_todos)
                branch_state["delegation_contexts"] = base_contexts + [branch_context]
                branch_state["current_route_context"] = branch_context
                branch_state["parallel_branch"] = {
                    "invocationId": invocation_id,
                    "branchIndex": index,
                    "agentId": agent_id,
                    "agentName": agent_name,
                    "reason": task_goal,
                    "taskBriefId": str(task_brief.get("taskBriefId") or f"{invocation_id}:{index}").strip(),
                    "taskBrief": task_brief,
                    "delegationId": delegation_id_value,
                    "lane": "subagent",
                    "acceptanceHint": _delegation_acceptance_hint(task_brief.get("acceptanceContract")),
                    "initialMessageCount": len(base_messages) + 1,
                    "initialTodoCount": len(base_todos),
                }
                sends.append(Send("parallel_delegate_task", branch_state))
                items.append(
                    _delegation_compact_item(
                        delegation_id=delegation_id_value,
                        task_brief=task_brief,
                        lane="subagent",
                        target_id=agent_id,
                        target_label=agent_name,
                        status="queued",
                        invocation_id=invocation_id,
                        branch_index=index,
                        trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                        selection_reason=str(local_diagnostics.get("selectionReason") or "").strip() or None,
                        selection_confidence=local_diagnostics.get("selectionConfidence"),
                        match_signals=list(local_diagnostics.get("matchSignals") or []),
                        compat_source=compat_source or None,
                        auto_dispatch_source=auto_dispatch_source or None,
                        workset_dispatch_decision=workset_decision,
                        workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                        engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                        repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                    )
                )
                continue

            if external_worker:
                rendered_command = render_external_worker_command(
                    descriptor=external_worker,
                    task_brief=task_brief,
                    workspace_path=str(base_state.get("workspace_path") or ""),
                )
                if not rendered_command:
                    item = _delegation_compact_item(
                        delegation_id=make_external_delegation_id(
                            command_id=f"missing-command-{index}",
                            task_brief_id=str(task_brief.get("taskBriefId") or ""),
                            worker_id=str(external_worker.get("id") or ""),
                        ),
                        task_brief=task_brief,
                        lane="external_worker",
                        target_id=str(external_worker.get("id") or ""),
                        target_label=str(external_worker.get("name") or external_worker.get("id") or "external-worker").strip(),
                        status="error",
                        invocation_id=invocation_id,
                        branch_index=index,
                        worker_type=str(external_worker.get("workerType") or "").strip() or None,
                        trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                        selection_reason=str(external_diagnostics.get("selectionReason") or "").strip() or None,
                        selection_confidence=external_diagnostics.get("selectionConfidence"),
                        match_signals=list(external_diagnostics.get("matchSignals") or []),
                        compat_source=compat_source or None,
                        auto_dispatch_source=auto_dispatch_source or None,
                        workset_dispatch_decision=workset_decision,
                        workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                        engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                        repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                        error="missing_command_template",
                    )
                    items.append(item)
                    parallel_results.append(item)
                    continue

                raw_start_payload = command_session_broker.func(
                    mode="start",
                    command=rendered_command,
                    profile="auto",
                    tool_call_id=tool_call_id,
                )
                start_payload = json.loads(str(raw_start_payload or "{}"))
                command_id = str(start_payload.get("commandId") or start_payload.get("sessionId") or "").strip()
                worker_result = parse_external_worker_result_block(
                    start_payload.get("initialPreview"),
                    markers=((external_worker.get("resultSchema") or {}).get("markers") or []),
                )
                delegation_id_value = make_external_delegation_id(
                    command_id=command_id or f"pending-{uuid.uuid4().hex[:8]}",
                    task_brief_id=str(task_brief.get("taskBriefId") or ""),
                    worker_id=str(external_worker.get("id") or ""),
                )
                worker_item = _delegation_compact_item(
                    delegation_id=delegation_id_value,
                    task_brief=task_brief,
                    lane="external_worker",
                    target_id=str(external_worker.get("id") or ""),
                    target_label=str(external_worker.get("name") or external_worker.get("id") or "external-worker").strip(),
                    status=str(start_payload.get("state") or "running").strip() or "running",
                    invocation_id=invocation_id,
                    branch_index=index,
                    worker_type=str(external_worker.get("workerType") or "").strip() or None,
                    command_session={
                        "commandId": command_id,
                        "sessionId": str(start_payload.get("sessionId") or command_id).strip() or command_id,
                        "runId": start_payload.get("runId"),
                    },
                    trace_ref=_delegation_trace_ref(
                        run_id=start_payload.get("runId") or base_state.get("run_id"),
                        invocation_id=invocation_id,
                        branch_index=index,
                        command_id=command_id,
                    ),
                    local_self_check=str((worker_result or {}).get("localSelfCheck") or "").strip() or None,
                    artifact_refs=list((worker_result or {}).get("artifactRefs") or []),
                    acceptance_hint=(worker_result or {}).get("acceptanceHint"),
                    result_schema_matched=bool(worker_result),
                    selection_reason=str(external_diagnostics.get("selectionReason") or "").strip() or None,
                    selection_confidence=external_diagnostics.get("selectionConfidence"),
                    match_signals=list(external_diagnostics.get("matchSignals") or []),
                    compat_source=compat_source or None,
                    auto_dispatch_source=auto_dispatch_source or None,
                    workset_dispatch_decision=workset_decision,
                    workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                    engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                    repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                    error=None if bool(start_payload.get("ok", True)) else str(start_payload.get("error") or "external_worker_start_failed"),
                )
                items.append(worker_item)
                parallel_results.append(worker_item)
                continue

            unresolved_lane = "external_worker" if lane_hint == "external_worker" else "subagent"
            item = _delegation_compact_item(
                delegation_id=f"{unresolved_lane}::unresolved::{str(task_brief.get('taskBriefId') or index)}::{lane_hint}",
                task_brief=task_brief,
                lane=unresolved_lane,
                target_id=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or lane_hint).strip() or unresolved_lane,
                target_label=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or lane_hint).strip() or unresolved_lane,
                status="error",
                invocation_id=invocation_id,
                branch_index=index,
                trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                selection_reason=str((external_diagnostics or local_diagnostics).get("selectionReason") or "no_matching_target").strip(),
                selection_confidence=(external_diagnostics or local_diagnostics).get("selectionConfidence", 0.0),
                match_signals=list((external_diagnostics or local_diagnostics).get("matchSignals") or []),
                compat_source=compat_source or None,
                auto_dispatch_source=auto_dispatch_source or None,
                workset_dispatch_decision=workset_decision,
                workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                error="no_matching_target",
            )
            items.append(item)
            parallel_results.append(item)

        summary = f"Delegation broker queued {len(items)} task(s): " + ", ".join(
            task_brief_summary(task_brief) or f"task-{index + 1}"
            for index, task_brief in enumerate(normalized_tasks)
        )
        update: dict[str, Any] = {
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode=normalized_mode,
                        summary=summary,
                        items=items,
                        recommended_next_action="observe" if any(item.get("lane") == "external_worker" for item in items) else "review",
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
        if sends:
            update["parallel_invocations"] = [
                {
                    "invocationId": invocation_id,
                    "expected": len(sends),
                    "createdAt": utc_now_iso(),
                }
            ]
        if parallel_results:
            update["parallel_results"] = parallel_results
        return Command(goto=sends if sends else "supervisor", update=update)

    parsed = parse_delegation_id(delegation_id)
    if str(parsed.get("lane") or "").strip() != "external_worker":
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode,
                            ok=False,
                            summary="当前 observe/resume/interrupt 仅支持 external_worker delegationId。",
                            recommended_next_action="dispatch",
                            error="unsupported_lane",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    external_descriptors = _delegation_external_worker_descriptors()
    descriptor = next(
        (
            item
            for item in external_descriptors
            if str(item.get("id") or "").strip() == str(parsed.get("targetId") or "").strip()
        ),
        None,
    )
    task_brief = normalize_task_brief({"taskBriefId": str(parsed.get("taskBriefId") or "").strip(), "goal": ""})
    command_id = str(parsed.get("commandId") or "").strip()

    if normalized_mode == "resume":
        followup_text = str(followup or "").strip()
        if not followup_text:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="delegation_broker(mode=resume) 需要提供 followup。",
                                recommended_next_action="none",
                                error="missing_followup",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )
        raw_payload = command_session_broker.func(
            mode="input",
            session_id=command_id,
            input_text=followup_text,
            tool_call_id=tool_call_id,
        )
    elif normalized_mode == "interrupt":
        raw_payload = command_session_broker.func(
            mode="terminate",
            session_id=command_id,
            tool_call_id=tool_call_id,
        )
    else:
        raw_payload = command_session_broker.func(
            mode="observe",
            session_id=command_id,
            tool_call_id=tool_call_id,
        )

    payload = json.loads(str(raw_payload or "{}"))
    markers = ((descriptor or {}).get("resultSchema") or {}).get("markers") or []
    worker_result = parse_external_worker_result_block(
        payload.get("deltaText") or payload.get("finalPreview") or payload.get("initialPreview"),
        markers=markers,
    )
    worker_item = _delegation_compact_item(
        delegation_id=delegation_id,
        task_brief=task_brief,
        lane="external_worker",
        target_id=str((descriptor or {}).get("id") or parsed.get("targetId") or "").strip(),
        target_label=str((descriptor or {}).get("name") or parsed.get("targetId") or "external-worker").strip(),
        status=str(payload.get("state") or ("terminated" if normalized_mode == "interrupt" else "running")).strip() or "running",
        worker_type=str((descriptor or {}).get("workerType") or "").strip() or None,
        command_session={
            "commandId": command_id,
            "sessionId": str(payload.get("sessionId") or command_id).strip() or command_id,
            "runId": payload.get("runId"),
        },
        trace_ref=_delegation_trace_ref(
            run_id=payload.get("runId") or base_state.get("run_id"),
            invocation_id=None,
            command_id=command_id,
        ),
        local_self_check=str((worker_result or {}).get("localSelfCheck") or "").strip() or None,
        artifact_refs=list((worker_result or {}).get("artifactRefs") or []),
        acceptance_hint=(worker_result or {}).get("acceptanceHint"),
        result_schema_matched=bool(worker_result),
        error=None if bool(payload.get("ok", True)) else str(payload.get("error") or f"{normalized_mode}_failed"),
    )
    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode=normalized_mode,
                        ok=bool(payload.get("ok", True)),
                        summary=str(payload.get("summary") or f"Delegation {normalized_mode} completed.").strip(),
                        items=[worker_item],
                        recommended_next_action=str(payload.get("recommendedNextAction") or "none").strip() or "none",
                        error=str(payload.get("error") or "").strip() or None,
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "parallel_results": [worker_item],
        },
    )


def _build_terminal_status_tool_view(status: dict) -> dict:
    if not isinstance(status, dict):
        return {}
    compact: dict = {}
    for key in (
        "command_id",
        "command",
        "session_id",
        "is_running",
        "profile",
        "profile_reason",
        "chat_cli_variant",
        "uses_tty",
        "interactive",
        "tty_mode",
        "screen_mode",
        "screen_version",
        "raw_frame_version",
        "observation_state",
        "awaiting_input",
        "text_encoding",
        "encoding_state",
        "encoding_notes",
        "cols",
        "rows",
        "alternate_screen",
        "run_id",
        "started_at",
        "completed_at",
        "return_code",
        "seconds_since_output",
        "seconds_since_input",
        "last_screen_at",
        "last_raw_frame_at",
        "chat_cli_turn_index",
        "chat_cli_turn_completed",
        "chat_cli_total_chars",
        "chat_cli_reported_chars",
        "chat_cli_last_digest",
    ):
        value = status.get(key)
        if value is None or value == "":
            continue
        compact[key] = value
    cursor = status.get("cursor")
    if isinstance(cursor, dict) and cursor:
        compact["cursor"] = cursor
    return compact


def _extract_preferred_terminal_snapshot(status: dict) -> str:
    stable_snapshot = str(status.get("stable_screen_snapshot") or "").strip()
    if stable_snapshot:
        return stable_snapshot
    return str(status.get("screen_snapshot") or "").strip()

@tool
def read_background_output(command_id: str) -> str:
    """Read the latest output from a background command.
    
    Arguments:
        command_id (str): The ID returned by `run_system_command(mode="session")`.
    """
    _prune_stale_background_processes()
    if command_id not in _bg_processes:
        return f"Error: No active background command with ID {command_id}."
    bg_proc = _bg_processes[command_id]
    new_out = bg_proc.get_new_output()
    status = bg_proc.status_snapshot()
    screen_snapshot = _extract_preferred_terminal_snapshot(status)
    screen_changed = bool(bg_proc.has_unreported_screen_change())
    raw_changed = bool(bg_proc.has_unreported_raw_frame_change())
    appended_output = new_out.strip()
    observation_state = str(status.get("observation_state") or "idle")
    raw_frame_preview = str(status.get("last_raw_frame_preview") or "").strip()
    prompt_ready = observation_state == "awaiting_input"
    encoding_state = str(status.get("encoding_state") or "").strip().lower()
    include_raw_preview = observation_state == "render_stalled" or encoding_state not in {"", "clean"}
    include_appended_delta = not screen_snapshot
    compact_status = _build_terminal_status_tool_view(status)

    if bg_proc.profile == "chat_cli":
        semantic_state = bg_proc._update_chat_cli_semantic_state(
            status=status,
            appended_output=new_out,
            screen_changed=screen_changed,
            raw_changed=raw_changed,
        )
        chat_cli_status = dict(compact_status)
        chat_cli_status["chat_cli_turn_index"] = semantic_state.get("turn_index")
        chat_cli_status["chat_cli_turn_completed"] = semantic_state.get("turn_completed")
        chat_cli_status["chat_cli_total_chars"] = len(bg_proc.current_turn_text)
        chat_cli_status["chat_cli_reported_chars"] = int(bg_proc.reported_offset)
        if bg_proc.last_semantic_digest:
            chat_cli_status["chat_cli_last_digest"] = bg_proc.last_semantic_digest
        delta_text = str(semantic_state.get("delta_text") or "").strip()
        turn_completed = bool(semantic_state.get("turn_completed"))
        has_more = bool(semantic_state.get("has_more"))
        turn_index = int(semantic_state.get("turn_index") or 0)

        def _format_chat_cli_response(observation: str) -> str:
            sections = [f"Observation: {observation}"]
            if delta_text:
                sections.append(f"CLI 新增回复（turn {turn_index}）:\n{delta_text}")
                if turn_completed:
                    sections.append("Turn Status: 本轮回复已结束，可继续输入。")
                elif has_more:
                    sections.append("Turn Status: 本轮回复仍在继续输出，本次只返回最新语义增量。")
                else:
                    sections.append("Turn Status: 已捕获本轮最新语义增量。")
            else:
                if turn_completed and prompt_ready:
                    sections.append("Turn Status: 当前没有新的语义回复，prompt 已就绪，可继续输入。")
                elif screen_changed or raw_changed:
                    sections.append("Turn Status: 当前只有屏幕变化，没有新的语义回复。")
                else:
                    sections.append("Turn Status: 当前没有新的语义回复。")
            if include_raw_preview and raw_frame_preview:
                sections.append(f"Raw Frame Preview:\n{raw_frame_preview}")
            if include_appended_delta and appended_output:
                sections.append(f"Appended Output:\n{_truncate_terminal_text(new_out)}")
            sections.append(f"Status: {json.dumps(chat_cli_status, ensure_ascii=False)}")
            return "\n\n".join(section for section in sections if section)

        if screen_changed:
            bg_proc.mark_screen_reported()
        if raw_changed:
            bg_proc.mark_raw_frame_reported()

        if not bg_proc.is_running:
            rcode = bg_proc.return_code
            if rcode is None and hasattr(bg_proc.proc, "poll"):
                try:
                    rcode = bg_proc.proc.poll()
                except Exception:
                    rcode = None
            return f"{_format_chat_cli_response('chat_cli 会话已结束。')}\n\n[Process terminated with exit code {rcode}]"

        if delta_text:
            observation = "CLI 有新增回复。"
            if turn_completed and prompt_ready:
                observation = "CLI 有新增回复，且本轮回复已结束，可继续输入。"
            elif has_more:
                observation = "CLI 有新增回复，本轮尚未结束。"
            return _format_chat_cli_response(observation)
        if prompt_ready:
            return _format_chat_cli_response("当前没有新的语义回复，prompt 已就绪，可继续输入。")
        if screen_changed or raw_changed:
            return _format_chat_cli_response("当前只有屏幕变化，没有新的语义回复。")
        if appended_output:
            return _format_chat_cli_response("观测到新的终端文本增量，但尚未确认新的语义回复。")
        return _format_chat_cli_response("当前未观测到新的终端变化。")

    def _format_interactive_screen_response(observation: str) -> str:
        sections = [f"Observation: {observation}"]
        if include_raw_preview and raw_frame_preview:
            sections.append(f"Raw Frame Preview:\n{raw_frame_preview}")
        if screen_snapshot:
            sections.append(f"Screen Snapshot:\n{screen_snapshot}")
        if include_appended_delta and appended_output:
            sections.append(f"Appended Output:\n{_truncate_terminal_text(new_out)}")
        sections.append(f"Status: {json.dumps(compact_status, ensure_ascii=False)}")
        return "\n\n".join(section for section in sections if section)

    if not bg_proc.is_running:
        rcode = bg_proc.return_code
        if rcode is None and hasattr(bg_proc.proc, "poll"):
            try:
                rcode = bg_proc.proc.poll()
            except Exception:
                rcode = None
        if screen_changed:
            bg_proc.mark_screen_reported()
        if raw_changed:
            bg_proc.mark_raw_frame_reported()
        if bg_proc.interactive and bg_proc.uses_tty:
            return f"{_format_interactive_screen_response('终端会话已结束。')}\n\n[Process terminated with exit code {rcode}]"
        terminal_state = (
            f"\n\nScreen Snapshot:\n{screen_snapshot}\n\nStatus: {json.dumps(compact_status, ensure_ascii=False)}"
            if screen_snapshot
            else f"\n\nStatus: {json.dumps(compact_status, ensure_ascii=False)}"
        )
        return f"{new_out}{terminal_state}\n\n[Process terminated with exit code {rcode}]"

    if bg_proc.interactive and bg_proc.uses_tty and screen_snapshot:
        if screen_changed:
            bg_proc.mark_screen_reported()
            if raw_changed:
                bg_proc.mark_raw_frame_reported()
            observation = "观测到新的终端屏幕变化。"
            if prompt_ready:
                observation = "观测到新的终端屏幕变化，prompt 已就绪，可继续输入。"
            return _format_interactive_screen_response(observation)
        if raw_changed:
            bg_proc.mark_raw_frame_reported()
            if observation_state == "render_stalled":
                return _format_interactive_screen_response("观测到新的原始终端数据，但屏幕渲染没有前进。V8 观测链可能失真。")
            return _format_interactive_screen_response("观测到新的原始终端数据，但当前屏幕快照未变化。")
        if appended_output:
            return _format_interactive_screen_response("观测到新的终端文本增量，但当前屏幕快照未变化。")
        if prompt_ready:
            return _format_interactive_screen_response("当前未观测到新的 terminal change，prompt 已就绪，可继续输入。")
        return _format_interactive_screen_response("当前未观测到新的 terminal change。")

    if not new_out:
        if bg_proc.interactive:
            return (
                f"当前未观测到新的终端文本增量。Observation: {observation_state}\n"
                f"Status: {json.dumps(compact_status, ensure_ascii=False)}"
            )
        return f"No new output yet. Status: {json.dumps(status, ensure_ascii=False)}"
    if raw_changed:
        bg_proc.mark_raw_frame_reported()
    if screen_changed:
        bg_proc.mark_screen_reported()
    return new_out

@tool
def send_background_input(command_id: str, input_text: str) -> str:
    """Send input (like 'y' or option choices) to an interactive background command.
    You can include a real newline or a common escaped sequence like '\\n' to simulate Enter.
    
    Arguments:
        command_id (str): The ID of the command.
        input_text (str): The text to send to stdin.
    """
    if command_id not in _bg_processes:
        return f"Error: No active background command with ID {command_id}."
    bg_proc = _bg_processes[command_id]
    if not bg_proc.is_running:
        return "Error: The process has already terminated."
    try:
        discarded_backlog = bg_proc.discard_pending_output()
        status_before = bg_proc.status_snapshot()
        previous_screen_version = int(status_before.get("screen_version") or 0)
        previous_raw_frame_version = int(status_before.get("raw_frame_version") or 0)
        normalized_input = _decode_background_input_escapes(input_text)
        bg_proc._prepare_chat_cli_for_input(normalized_input, status_before=status_before)
        bg_proc.write_input(normalized_input)
        time.sleep(0.5)  # Wait for response
        resp = bg_proc.get_new_output()
        status = bg_proc.status_snapshot()
        screen_snapshot = _extract_preferred_terminal_snapshot(status)
        appended_output = resp.strip()
        screen_changed = int(status.get("screen_version") or 0) > previous_screen_version
        raw_changed = int(status.get("raw_frame_version") or 0) > previous_raw_frame_version
        if raw_changed:
            bg_proc.mark_raw_frame_reported()
        if screen_changed:
            bg_proc.mark_screen_reported()
        observation_state = str(status.get("observation_state") or "idle")
        prompt_ready = observation_state == "awaiting_input"
        encoding_state = str(status.get("encoding_state") or "").strip().lower()
        raw_frame_preview = str(status.get("last_raw_frame_preview") or "").strip()
        include_raw_preview = observation_state == "render_stalled" or encoding_state not in {"", "clean"}
        include_appended_delta = not screen_snapshot
        compact_status = _build_terminal_status_tool_view(status)
        observation = "已发送输入，当前未观测到新的终端变化。"
        if screen_changed:
            observation = "已发送输入，并观测到新的终端屏幕变化。"
            if prompt_ready:
                observation = "已发送输入，并观测到新的终端屏幕变化。当前 prompt 已就绪，可继续对话。"
        elif raw_changed:
            observation = (
                "已发送输入，并观测到新的原始终端数据，但屏幕渲染没有前进。"
                if observation_state == "render_stalled"
                else "已发送输入，并观测到新的原始终端数据。"
            )
        elif prompt_ready:
            observation = "已发送输入，当前终端回到可输入状态。V8 尚未确认新的终端回复。"
        sections = [f"Observation: {observation}"]
        if normalized_input != input_text:
            sections.append("Input normalization: 已将常见转义序列按终端控制字符解释（例如 \\n => Enter）。")
        if discarded_backlog.strip():
            sections.append(f"Pre-send backlog discarded: {len(discarded_backlog)} chars")
        if include_raw_preview and raw_frame_preview:
            sections.append(f"Raw Frame Preview:\n{raw_frame_preview}")
        if screen_snapshot:
            sections.append(f"Screen Snapshot:\n{screen_snapshot}")
        if include_appended_delta and appended_output:
            sections.append(f"Appended Output:\n{_truncate_terminal_text(resp)}")
        if bg_proc.profile == "chat_cli":
            sections.append("Profile: chat_cli（后续请用 `read_background_output` 读取最新语义增量，而不是期待整屏重放）。")
        sections.append(f"Status: {json.dumps(compact_status, ensure_ascii=False)}")
        return "\n\n".join(section for section in sections if section)
    except Exception as e:
        return f"Error sending input: {e}"

@tool
def terminate_background_command(command_id: str) -> str:
    """Terminate a background command if it is stuck or no longer needed.
    
    Arguments:
        command_id (str): The ID of the command.
    """
    if command_id not in _bg_processes:
        return f"Error: No active background command with ID {command_id}."
    bg_proc = _bg_processes[command_id]
    try:
        bg_proc.terminate()
        final_out = bg_proc.get_new_output()
        del _bg_processes[command_id]
        if final_out:
            return f"Command {command_id} terminated successfully.\nFinal output:\n{final_out}"
        return f"Command {command_id} terminated successfully."
    except Exception as e:
        return f"Error terminating: {e}"


@tool
def computer_use_list_windows(title_filter: Optional[str] = None, limit: int = 20) -> str:
    """List current top-level Windows desktop windows via UI Automation.

    Use this before selecting a target application/window for structured desktop control.
    """
    try:
        result = _get_computer_use_runtime().list_windows(title_filter=title_filter, limit=max(1, min(limit, 50)))
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing windows: {e}"


@tool
def computer_use_observe(
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    include_screenshot: bool = True,
    depth_limit: int = 4,
    element_limit: int = 80,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Observe the current desktop/window and return a structured UI tree plus optional screenshot artifact.

    Prefer this tool before click/type actions so the agent can inspect available controls.
    """
    try:
        result = _get_computer_use_runtime().observe(
            **_computer_use_runtime_kwargs("observe_desktop"),
            window_title=window_title,
            window_handle=window_handle,
            include_screenshot=include_screenshot,
            depth_limit=max(1, min(depth_limit, 8)),
            element_limit=max(1, min(element_limit, 150)),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_observation(
            raw_result=result,
            app_hint=None,
            resolved_app=None,
        )
    except Exception as e:
        return f"Error observing desktop: {e}"


@tool
def computer_use_find_element(
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    limit: int = 10,
) -> str:
    """Find UI elements within a target window by name, automation_id, control type, or class name."""
    try:
        result = _get_computer_use_runtime().find_elements(
            name=name,
            name_contains=name_contains,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            window_title=window_title,
            window_handle=window_handle,
            limit=max(1, min(limit, 30)),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error finding element: {e}"


@tool
def computer_use_click(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    element_id: Optional[str] = None,
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    double: bool = False,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Perform a real desktop click on a structured UI element resolved via Windows UIA."""
    target = {
        "element_id": element_id,
        "name": name,
        "name_contains": name_contains,
        "automation_id": automation_id,
        "control_type": control_type,
        "class_name": class_name,
        "window_title": window_title,
        "window_handle": window_handle,
        "double": double,
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="double_click" if double else "click",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="double_click" if double else "click",
            summary=error_message or "Safety Guardian 已阻止桌面点击动作。",
            target_hint=automation_id or name or name_contains or element_id,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().click(
            **_computer_use_runtime_kwargs("computer_use_click"),
            **target,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="double_click" if double else "click",
            raw_result=raw_result,
            target_hint=automation_id or name or name_contains or element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error clicking element: {e}"


@tool
def computer_use_type_text(
    text: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    element_id: Optional[str] = None,
    name: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = False,
    press_enter: bool = False,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Type text into a structured UI element using Windows UIA, with optional clear/enter behavior."""
    target = {
        "element_id": element_id,
        "name": name,
        "automation_id": automation_id,
        "control_type": control_type,
        "class_name": class_name,
        "window_title": window_title,
        "window_handle": window_handle,
        "clear_first": clear_first,
        "press_enter": press_enter,
        "text_preview": text[:80],
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="type_text",
            summary=error_message or "Safety Guardian 已阻止文本输入动作。",
            target_hint=automation_id or name or element_id,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().type_text(
            **_computer_use_runtime_kwargs("computer_use_type_text"),
            element_id=element_id,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            window_title=window_title,
            window_handle=window_handle,
            text=text,
            clear_first=clear_first,
            press_enter=press_enter,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="type_text",
            raw_result=raw_result,
            target_hint=automation_id or name or element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error typing text: {e}"


@tool
def computer_use_hotkey(
    sequence: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Send a desktop hotkey sequence using Windows UIA/keyboard injection, e.g. '^a' or '%{F4}'."""
    target = {
        "sequence": sequence,
        "window_title": window_title,
        "window_handle": window_handle,
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="hotkey",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="hotkey",
            summary=error_message or "Safety Guardian 已阻止热键动作。",
            target_hint=sequence,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().hotkey(
            **_computer_use_runtime_kwargs("computer_use_hotkey"),
            sequence=sequence,
            window_title=window_title,
            window_handle=window_handle,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="hotkey",
            raw_result=raw_result,
            target_hint=sequence,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error sending hotkey: {e}"


@tool
def computer_use_scroll(
    amount: int,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    element_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Scroll a window or structured UI element using the mouse wheel. Positive goes up, negative goes down."""
    target = {
        "amount": amount,
        "element_id": element_id,
        "window_title": window_title,
        "window_handle": window_handle,
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="scroll",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="scroll",
            summary=error_message or "Safety Guardian 已阻止滚动动作。",
            target_hint=element_id,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().scroll(
            **_computer_use_runtime_kwargs("computer_use_scroll"),
            amount=amount,
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="scroll",
            raw_result=raw_result,
            target_hint=element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error scrolling: {e}"


@tool
def computer_use_wait_for_element(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    timeout_ms: int = 10000,
    poll_ms: int = 300,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Wait until a structured UI element appears, then return the resolved target descriptor."""
    target = {
        "name": name,
        "name_contains": name_contains,
        "automation_id": automation_id,
        "control_type": control_type,
        "class_name": class_name,
        "window_title": window_title,
        "window_handle": window_handle,
        "timeout_ms": timeout_ms,
        "poll_ms": poll_ms,
    }
    try:
        raw_result = _get_computer_use_runtime().wait_for_element(
            **_computer_use_runtime_kwargs("computer_use_wait_for_element"),
            **target,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="wait_for_element",
            raw_result=raw_result,
            target_hint=automation_id or name or name_contains,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error waiting for element: {e}"


@tool
def computer_use_capture_screenshot(
    *,
    element_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Capture a desktop, window, or element screenshot and record it as a runtime artifact."""
    try:
        raw_result = _get_computer_use_runtime().capture_screenshot(
            **_computer_use_runtime_kwargs("computer_use_capture_screenshot"),
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="capture_screenshot",
            raw_result=raw_result,
            target_hint=element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error capturing screenshot: {e}"


@tool
def computer_use_open_app(
    *,
    app_id: Optional[str] = None,
    command: Optional[str] = None,
    window_title: Optional[str] = None,
    class_name: Optional[str] = None,
    wait_timeout_ms: int = 12000,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Open an application window for desktop automation. Prefer app_id so runtime can use app-specific profile."""
    try:
        raw_result = _get_computer_use_runtime().open_app(
            **_computer_use_runtime_kwargs(f"computer_use_open_app:{app_id or command or 'unknown'}"),
            app_id=app_id,
            command=command,
            window_title=window_title,
            class_name=class_name,
            wait_timeout_ms=max(2000, min(wait_timeout_ms, 30000)),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="open_app",
            raw_result=raw_result,
            app_hint=app_id or command,
            target_hint=class_name,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error opening app: {e}"


@tool
def computer_use_focus_window(
    *,
    app_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    class_name: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Focus an existing application window before multi-step desktop actions."""
    try:
        raw_result = _get_computer_use_runtime().focus_window(
            **_computer_use_runtime_kwargs(f"computer_use_focus_window:{app_id or window_title or window_handle or 'unknown'}"),
            app_id=app_id,
            window_title=window_title,
            window_handle=window_handle,
            class_name=class_name,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="focus_window",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=class_name,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error focusing window: {e}"


@tool
def computer_use_find_and_type(
    *,
    text: str,
    app_id: Optional[str] = None,
    selector_key: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = False,
    press_enter: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """High-level desktop input action. Runtime will resolve the best selector from app profile or current window context."""
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target={
            "app_id": app_id,
            "selector_key": selector_key,
            "window_title": window_title,
            "window_handle": window_handle,
            "text_preview": text[:80],
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="find_and_type",
            summary=error_message or "Safety Guardian 已阻止 computer use 输入动作。",
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().find_and_type(
            **_computer_use_runtime_kwargs("computer_use_find_and_type"),
            app_id=app_id,
            selector_key=selector_key,
            window_title=window_title,
            window_handle=window_handle,
            text=text,
            clear_first=clear_first,
            press_enter=press_enter,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="find_and_type",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error typing via high-level computer use action: {e}"


@tool
def computer_use_scroll_list(
    *,
    amount: int,
    app_id: Optional[str] = None,
    selector_key: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """High-level list scroll action. Runtime resolves the list container and verifies scroll result."""
    allowed, error_message = _computer_use_action_guard(
        action_type="scroll",
        target={
            "app_id": app_id,
            "selector_key": selector_key,
            "window_title": window_title,
            "window_handle": window_handle,
            "amount": amount,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="scroll_list",
            summary=error_message or "Safety Guardian 已阻止 computer use 滚动动作。",
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().scroll_list(
            **_computer_use_runtime_kwargs("computer_use_scroll_list"),
            app_id=app_id,
            selector_key=selector_key,
            window_title=window_title,
            window_handle=window_handle,
            amount=int(amount),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="scroll_list",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error scrolling list via computer use: {e}"


@tool
def computer_use_click_toolbar_action(
    *,
    action_name: str,
    app_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    class_name: Optional[str] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """High-level toolbar click action. Runtime uses app profile toolbar selectors and recovery strategy."""
    allowed, error_message = _computer_use_action_guard(
        action_type="click",
        target={
            "app_id": app_id,
            "action_name": action_name,
            "window_title": window_title,
            "window_handle": window_handle,
            "class_name": class_name,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="click_toolbar_action",
            summary=error_message or "Safety Guardian 已阻止 computer use 工具栏动作。",
            app_hint=app_id,
            target_hint=action_name,
            resolved_app={"appId": app_id} if app_id else None,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().click_toolbar_action(
            **_computer_use_runtime_kwargs(f"computer_use_click_toolbar_action:{action_name}"),
            app_id=app_id,
            action_name=action_name,
            window_title=window_title,
            window_handle=window_handle,
            class_name=class_name,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="click_toolbar_action",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=action_name,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error clicking toolbar action via computer use: {e}"


@tool
def computer_use_execute_plan(
    steps_json: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    goal: Optional[str] = None,
    app_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    continue_on_error: bool = False,
    max_steps: int = 5,
) -> str:
    """Execute a short horizon desktop loop.

    Preferred mode:
    - Provide `goal`, let the planner run: 观察 -> 规划 2~5 步 -> 执行 -> 验证。

    Advanced mode:
    - Provide `steps_json` with a JSON array when the caller already knows the exact steps.

    Supported actions:
    observe / find / click / double_click / type_text / hotkey / scroll / wait / screenshot /
    open_app / focus_window / find_and_type / scroll_list / click_toolbar_action
    """
    effective_max_steps = max(1, min(max_steps, 8))

    if goal and goal.strip():
        try:
            runtime_kwargs = _computer_use_runtime_kwargs(goal.strip())
            planning = _get_computer_use_runtime().plan(
                **runtime_kwargs,
                app_id=app_id,
                window_title=window_title,
                window_handle=window_handle,
                max_steps=effective_max_steps,
                include_screenshot=False,
            )
            planned_steps = list(((planning.get("planner") or {}).get("steps")) or [])
            if not planned_steps:
                return "Error: planner 没有生成任何可执行步骤。"
            allowed, error_message = _guard_computer_use_steps(
                steps=planned_steps,
                tool_call_id=tool_call_id,
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止 planner 生成的桌面动作。"
            execution = _get_computer_use_runtime().execute_plan(
                **runtime_kwargs,
                steps=planned_steps,
                continue_on_error=continue_on_error,
                max_steps=effective_max_steps,
            )
            payload = _computer_use_attach_plan_contract_summary(
                payload={
                    **planning,
                    "execution": execution,
                },
                action="execute_plan",
                goal=goal.strip(),
            )
            return json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error planning/executing computer use goal: {e}"

    if not steps_json.strip():
        return "Error: 必须提供 goal 或 steps_json。"

    try:
        raw_steps = json.loads(steps_json)
    except Exception as e:
        return f"Error parsing plan JSON: {e}"

    if not isinstance(raw_steps, list):
        return "Error: steps_json 必须是 JSON 数组。"
    if not raw_steps:
        return "Error: 执行计划不能为空。"

    steps: list[dict] = []
    for raw_step in raw_steps[:effective_max_steps]:
        if not isinstance(raw_step, dict):
            return "Error: 计划中的每一步都必须是 JSON 对象。"
        step = dict(raw_step)
        if window_title is not None and "window_title" not in step:
            step["window_title"] = window_title
        if window_handle is not None and "window_handle" not in step:
            step["window_handle"] = window_handle
        steps.append(step)

    allowed, error_message = _guard_computer_use_steps(
        steps=steps,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return error_message or "Safety Guardian 已阻止 computer use 计划步骤。"

    try:
        result = _get_computer_use_runtime().execute_plan(
            **_computer_use_runtime_kwargs("computer_use_execute_plan"),
            steps=steps,
            continue_on_error=continue_on_error,
            max_steps=effective_max_steps,
        )
        payload = _computer_use_attach_plan_contract_summary(
            payload=dict(result or {}),
            action="execute_plan",
            goal=goal.strip() if goal else None,
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error executing computer use plan: {e}"


@tool
def computer_use_list_apps(
    app_query: Optional[str] = None,
    limit: int = 8,
    include_running: bool = True,
    force_refresh: bool = False,
) -> str:
    """List desktop applications in a Supervisor-friendly way.

    Prefer this before launch/focus when you only know an approximate app name.
    """
    try:
        payload = _get_computer_use_runtime().list_apps(
            query=app_query,
            limit=max(1, min(limit, 20)),
            include_running=include_running,
            force_refresh=force_refresh,
        )
        apps = []
        for item in list(payload.get("apps") or [])[: max(1, min(limit, 20))]:
            if not isinstance(item, dict):
                continue
            apps.append(
                {
                    "appId": item.get("appId"),
                    "displayName": item.get("displayName"),
                    "isRunning": item.get("isRunning"),
                    "launchable": item.get("launchable"),
                    "profileBound": item.get("profileBound"),
                    "runningWindows": list(item.get("runningWindows") or [])[:3],
                    "aliases": list(item.get("aliases") or [])[:8],
                }
            )
        return json.dumps(
            {
                "ok": True,
                "query": str(app_query or "").strip() or None,
                "apps": apps,
                "summary": payload.get("summary"),
                "platform": payload.get("platform"),
                "backend": payload.get("backend"),
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing desktop apps: {e}"


@tool
def computer_use_list_primitives(
    category: Optional[str] = None,
) -> str:
    """List the canonical desktop primitives exposed to Supervisor and ComputerUseRuntime.

    Use this to understand the stable tool vocabulary before attempting exploratory GUI work.
    """
    try:
        normalized = str(category or "").strip().lower() or None
        return _computer_use_compact_primitive_catalog(category=normalized)
    except Exception as e:
        return f"Error listing desktop primitives: {e}"


@tool
def computer_use_desktop_capabilities() -> str:
    """Return the current desktop driver/runtime capability summary in a compact format."""
    try:
        return _computer_use_compact_driver_capabilities()
    except Exception as e:
        return f"Error reading desktop capabilities: {e}"


@tool
def computer_use_lookup_muscle_memory(
    goal: str,
    *,
    app: Optional[str] = None,
    variables_json: Optional[str] = None,
    limit: int = 5,
) -> str:
    """Look up reusable desktop muscle memory before entering Computer Use learning mode.

    This is a read-only route advisor. It checks approved templates, candidate templates, and draft traces to decide
    whether the task should go to reuse_mode, hybrid_mode, or learn_mode.
    """
    normalized_goal = str(goal or "").strip()
    if not normalized_goal:
        return "Error: goal 不能为空。"
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        variables = _computer_use_parse_variables_json(variables_json)
        runtime_context = get_runtime_context()
        route = _get_rpa_runtime().recommend_execution_route(
            goal=normalized_goal,
            app_id=(resolved_app or {}).get("appId") or app_query,
            variables=variables,
            session_id=runtime_context.get("session_id"),
            run_id=runtime_context.get("run_id"),
            limit=max(1, min(limit, 10)),
            allow_materialization=False,
        )
        return _computer_use_compact_memory_lookup(
            goal=normalized_goal,
            app_hint=app_query,
            resolved_app=resolved_app,
            route=route,
        )
    except Exception as e:
        return f"Error looking up desktop muscle memory: {e}"


@tool
def computer_use_list_muscle_memories(
    app: Optional[str] = None,
    *,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List existing desktop muscle memories/templates in a human-readable, Supervisor-safe format."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        templates = _get_rpa_runtime().list_templates(
            limit=max(1, min(limit, 50)),
            app_id=(resolved_app or {}).get("appId") or app_query,
            status=status,
        )
        return _computer_use_compact_memory_list(
            templates=list(templates or []),
            app_hint=app_query,
            resolved_app=resolved_app,
            status=status,
        )
    except Exception as e:
        return f"Error listing desktop muscle memories: {e}"


@tool
def computer_use_resolve_execution_route(
    goal: str,
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    variables_json: Optional[str] = None,
    limit: int = 5,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command | str:
    """Resolve whether the desktop task should reuse muscle memory, run hybrid, or enter learning mode.

    This is the preferred Wave 6 entrypoint for Supervisor before invoking any concrete desktop primitive.
    """
    normalized_goal = str(goal or "").strip()
    if not normalized_goal:
        return "Error: goal 不能为空。"
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        variables = _computer_use_parse_variables_json(variables_json)
        runtime_context = get_runtime_context()
        route = _get_rpa_runtime().recommend_execution_route(
            goal=normalized_goal,
            app_id=(resolved_app or {}).get("appId") or app_query,
            variables=variables,
            session_id=runtime_context.get("session_id"),
            run_id=runtime_context.get("run_id"),
            limit=max(1, min(limit, 10)),
            allow_materialization=True,
        )
        payload = build_compact_execution_route(
            action="resolve_execution_route",
            goal=normalized_goal,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            route=route,
        )
        latest_human_id, _ = _desktop_route_latest_bound_human_message(state if isinstance(state, dict) else {})
        desktop_route = {
            "goal": normalized_goal,
            "appId": payload.get("app", {}).get("appId") or route.get("appId"),
            "requestedApp": app_query,
            "target": target_hint,
            "recommendedMode": payload.get("recommendedMode"),
            "executionReadyMode": payload.get("executionReadyMode") or determine_execution_ready_mode(route),
            "recommendedRuntime": payload.get("recommendedRuntime"),
            "recommendedTool": payload.get("recommendedTool"),
            "recommendedDraftId": payload.get("recommendedDraftId"),
            "recommendedTemplateId": payload.get("recommendedTemplateId"),
            "routeAction": payload.get("recommendedAction"),
            "boundHumanMessageId": latest_human_id,
            "source": _DESKTOP_ROUTE_SOURCE,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        payload.update(
            _desktop_route_compact_metadata(
                desktop_route,
                route_gate_applied=False,
                runtime_governed=True,
            )
        )
        payload_str = json.dumps(payload, ensure_ascii=False, indent=2)
        if not isinstance(state, dict):
            return payload_str
        updated_route_context = dict(state.get("current_route_context") or {})
        updated_route_context["desktopRoute"] = desktop_route
        return Command(
            update={
                "messages": [ToolMessage(content=payload_str, tool_call_id=tool_call_id)],
                "current_route_context": updated_route_context,
            }
        )
    except Exception as e:
        return f"Error resolving desktop execution route: {e}"


@tool
def computer_use_execute_task(
    goal: str = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    successCriteria: Optional[str] = None,
    variablesJson: Optional[str] = None,
    maxSteps: int = 5,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Execute a route-approved desktop task through the unified task-level broker and return a compact verification summary."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    route_goal = str(goal or "").strip()
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_execute_task",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"

    desktop_route = dict(desktop_route or {})
    mismatch_reason = _desktop_route_task_mismatch_reason(
        desktop_route=desktop_route,
        goal=route_goal,
        target=target_hint,
    )
    if mismatch_reason:
        return _desktop_route_gate_failure_response(
            gate_error_code="ROUTE_MISMATCH",
            summary=mismatch_reason,
            desktop_route=desktop_route,
            recommended_next_tool="computer_use_resolve_execution_route",
        )

    effective_goal = route_goal or str(desktop_route.get("goal") or "").strip()
    if not effective_goal:
        return "Error: goal 不能为空，且当前桌面路由也未绑定 goal。"

    effective_target = target_hint or str(desktop_route.get("target") or "").strip() or None
    execution_ready_mode = str(desktop_route.get("executionReadyMode") or "").strip() or "learn_mode"
    effective_app_id = (
        str((resolved_app or {}).get("appId") or "").strip()
        or str(desktop_route.get("appId") or "").strip()
        or None
    )
    effective_max_steps = max(1, min(int(maxSteps or 5), 8))
    success_criteria = str(successCriteria or "").strip() or None

    try:
        variables = _computer_use_parse_variables_json(variablesJson)
        runtime_context = get_runtime_context()
        if execution_ready_mode in {"reuse_mode", "hybrid_mode"}:
            draft_id = _desktop_route_executable_draft_id(desktop_route)
            if not draft_id:
                return _desktop_route_gate_failure_response(
                    gate_error_code="STALE_ROUTE_CONTEXT",
                    summary="当前桌面路由缺少可执行的 RPA 骨架，请重新调用 computer_use_resolve_execution_route。",
                    desktop_route=desktop_route,
                    recommended_next_tool="computer_use_resolve_execution_route",
                )
            raw_result = _get_rpa_runtime().run_draft(
                script_id=draft_id,
                variables=variables,
                session_id=runtime_context.get("session_id"),
                run_id=runtime_context.get("run_id"),
                user_id=runtime_context.get("user_id") or "anonymous",
                project_id=runtime_context.get("project_id"),
                workspace_id=runtime_context.get("workspace_id"),
                workspace_path=runtime_context.get("workspace_path"),
                trigger_source="computer_use_execute_task",
            )
            payload = _computer_use_execute_task_compact_rpa_result(
                raw_result=dict(raw_result or {}),
                execution_ready_mode=execution_ready_mode,
                goal=effective_goal,
                app_hint=app_query,
                target_hint=effective_target,
                resolved_app=resolved_app,
                success_criteria=success_criteria,
            )
        else:
            planner_goal = effective_goal
            if effective_target:
                planner_goal = f"{planner_goal}\nTarget: {effective_target}"
            if success_criteria:
                planner_goal = f"{planner_goal}\nSuccess criteria: {success_criteria}"
            planning = _get_computer_use_runtime().plan(
                **_computer_use_runtime_kwargs(planner_goal),
                app_id=effective_app_id,
                max_steps=effective_max_steps,
                include_screenshot=False,
            )
            planned_steps = list(((planning.get("planner") or {}).get("steps")) or [])
            if not planned_steps:
                return "Error: ComputerUseRuntime planner 没有生成任何可执行步骤。"
            allowed, error_message = _guard_computer_use_steps(
                steps=planned_steps,
                tool_call_id=tool_call_id,
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止 planner 生成的桌面动作。"
            execution = _get_computer_use_runtime().execute_plan(
                **_computer_use_runtime_kwargs(planner_goal),
                steps=planned_steps,
                continue_on_error=False,
                max_steps=effective_max_steps,
            )
            execution_payload = _computer_use_attach_plan_contract_summary(
                payload={
                    **planning,
                    "execution": execution,
                },
                action="execute_task",
                goal=effective_goal,
            )
            payload = _computer_use_execute_task_compact_computer_use_result(
                payload=execution_payload,
                execution_ready_mode=execution_ready_mode,
                goal=effective_goal,
                app_hint=app_query,
                target_hint=effective_target,
                resolved_app=resolved_app,
                success_criteria=success_criteria,
            )

        return _desktop_route_merge_into_response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error executing desktop task via broker: {e}"


@tool
def computer_use_launch_app(
    app: str,
    *,
    target: Optional[str] = None,
    wait_timeout_ms: int = 12000,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Launch a desktop app with minimal parameters.

    Provide a human app name or alias. The tool resolves the best app match, opens it, and returns
    verification/blocking/evidence in a compact format.
    """
    app_query = str(app or "").strip()
    if not app_query:
        return "Error: app 不能为空。"
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_launch_app",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    launch_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target,
    )
    expected_window_title = (
        str(launch_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(None, resolved_app)
    )
    try:
        raw_result = _get_computer_use_runtime().open_app(
            **_computer_use_runtime_kwargs(f"launch_app:{app_query}"),
            app_id=(resolved_app or {}).get("appId"),
            app_name=app_query,
            command=str(launch_override.get("command") or "").strip() or None,
            launch_target_path=str(launch_override.get("resolved_target_path") or "").strip() or None,
            window_title=expected_window_title,
            strict_window_title_match=bool(launch_override.get("strict_expected_window_title")),
            wait_timeout_ms=max(2000, min(wait_timeout_ms, 30000)),
            require_visual_guard=False,
            prefer_fast_path=True,
            post_action_settle_timeout_ms=650,
            post_action_settle_poll_ms=160,
            post_action_stable_rounds=1,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        resolved_app = _computer_use_update_resolved_app_from_raw_result(
            app_query=app_query,
            resolved_app=resolved_app,
            raw_result=raw_result,
        )
        response = _computer_use_compact_response(
            action="launch_app",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target,
            resolved_app=resolved_app,
            expected_window_title=expected_window_title,
            strict_expected_window_title=bool(launch_override.get("strict_expected_window_title")),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        fallback_target_path = str(launch_override.get("resolved_target_path") or "").strip() or None
        resolved_app_id = str((resolved_app or {}).get("appId") or "").strip().lower()
        if resolved_app_id == "explorer" and fallback_target_path:
            try:
                recovered_result = _get_computer_use_runtime().focus_window(
                    **_computer_use_runtime_kwargs(f"launch_app_recover:{app_query}"),
                    app_id=(resolved_app or {}).get("appId"),
                    target_path=fallback_target_path,
                    window_title=expected_window_title,
                    require_visual_guard=False,
                    prefer_fast_path=True,
                    post_action_settle_timeout_ms=220,
                    post_action_settle_poll_ms=120,
                    post_action_stable_rounds=1,
                    observe_notifications=observe_notifications,
                    observe_sound=observe_sound,
                    environment_probe_mode=environment_probe_mode,
                )
                resolved_app = _computer_use_update_resolved_app_from_raw_result(
                    app_query=app_query,
                    resolved_app=resolved_app,
                    raw_result=recovered_result,
                )
                response = _computer_use_compact_response(
                    action="launch_app",
                    raw_result=recovered_result,
                    app_hint=app_query,
                    target_hint=target,
                    resolved_app=resolved_app,
                    expected_window_title=expected_window_title,
                    strict_expected_window_title=bool(launch_override.get("strict_expected_window_title")),
                )
                return _desktop_route_merge_into_response(
                    response,
                    desktop_route=desktop_route,
                    route_gate_applied=isinstance(state, dict),
                )
            except Exception:
                pass
        return f"Error launching desktop app: {e}"


@tool
def computer_use_ensure_window(
    app: Optional[str] = None,
    *,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    class_name: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Ensure a desktop window is bound and focused before the next action.

    Use this to rebind/focus the correct application window instead of continuing with a stale foreground context.
    """
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_ensure_window",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    launch_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target,
    )
    effective_window_title = (
        str(launch_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(window_title, resolved_app)
    )
    try:
        raw_result = _get_computer_use_runtime().focus_window(
            **_computer_use_runtime_kwargs(f"ensure_window:{app_query or effective_window_title or class_name or 'desktop'}"),
            app_id=(resolved_app or {}).get("appId"),
            target_path=str(launch_override.get("resolved_target_path") or "").strip() or None,
            window_title=effective_window_title,
            class_name=class_name,
            require_visual_guard=False,
            prefer_fast_path=True,
            post_action_settle_timeout_ms=220,
            post_action_settle_poll_ms=120,
            post_action_stable_rounds=1,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        resolved_app = _computer_use_update_resolved_app_from_raw_result(
            app_query=app_query,
            resolved_app=resolved_app,
            raw_result=raw_result,
        )
        response = _computer_use_compact_response(
            action="ensure_window",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=str(target or "").strip() or effective_window_title or class_name,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(
                str(launch_override.get("strict_expected_window_title") or "").strip()
                or str(window_title or "").strip()
            ),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error ensuring desktop window: {e}"


@tool
def computer_use_observe_scene(
    app: Optional[str] = None,
    *,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    include_screenshot: bool = True,
    depth_limit: int = 4,
    element_limit: int = 60,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Observe the current desktop scene in a compact, Supervisor-friendly format."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    launch_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target,
    )
    inferred_title = (
        str(launch_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(window_title, resolved_app)
    )
    try:
        raw_result = _get_computer_use_runtime().observe(
            **_computer_use_runtime_kwargs(f"observe_scene:{app_query or inferred_title or 'desktop'}"),
            app_id=(resolved_app or {}).get("appId"),
            window_title=inferred_title,
            include_screenshot=include_screenshot,
            depth_limit=max(1, min(depth_limit, 8)),
            element_limit=max(1, min(element_limit, 120)),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        resolved_app = _computer_use_update_resolved_app_from_raw_result(
            app_query=app_query,
            resolved_app=resolved_app,
            raw_result=raw_result,
        )
        return _computer_use_compact_observation(
            raw_result=raw_result,
            app_hint=app_query,
            resolved_app=resolved_app,
        )
    except Exception as e:
        return f"Error observing desktop scene: {e}"


@tool
def computer_use_click_target(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    target_text: Optional[str] = None,
    double: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Click a semantic desktop target with built-in verification and blocking.

    Prefer short semantic hints such as 'primary_input', 'address_bar', 'confirm_action', or a visible element name.
    The runtime will decide whether to use structured lookup, anchor targeting, or a guarded fallback path.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_click_target",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="click_target_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "target_text": target_text,
        "double": bool(double),
    }
    point_hint = _computer_use_parse_point_tag(target_hint) or _computer_use_parse_point_tag(target_text)
    if point_hint:
        guard_target["point"] = list(point_hint)
    allowed, error_message = _computer_use_action_guard(
        action_type="double_click" if double else "click",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="double_click" if double else "click",
                summary=error_message or "Safety Guardian 已阻止桌面点击动作。",
                app_hint=app_query,
                target_hint=target_hint or target_text,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "double_click" if double else "click",
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if point_hint:
        step["point"] = list(point_hint)
        step["coordinate_source"] = "vision_point_tag"
    elif target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if target_text:
        step["target_text"] = target_text
    step["require_visual_guard"] = False
    step["prefer_fast_path"] = True
    step["post_action_settle_timeout_ms"] = 220
    step["post_action_settle_poll_ms"] = 120
    step["post_action_stable_rounds"] = 1
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action=step["action"],
            step=step,
            goal=f"{step['action']}:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action=step["action"],
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error clicking desktop target: {e}"


@tool
def computer_use_input_text(
    text: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = True,
    submit: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Input text into a desktop target using the simplest possible interface.

    If `target` is provided, it is treated as a semantic target hint. If `target` is omitted, the tool falls back to
    window-level typing and blocks when editable focus cannot be confirmed.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_input_text",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="input_text_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "text_preview": text[:80],
        "clear_first": bool(clear_first),
        "submit": bool(submit),
    }
    point_hint = _computer_use_parse_point_tag(target_hint)
    if point_hint:
        guard_target["point"] = list(point_hint)
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="input_text",
                summary=error_message or "Safety Guardian 已阻止桌面输入动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "type_text",
        "text": text,
        "clear_first": bool(clear_first),
        "press_enter": bool(submit),
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if point_hint:
        step["point"] = list(point_hint)
        step["coordinate_source"] = "vision_point_tag"
    elif target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    else:
        step["window_typing"] = True
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="type_text",
            step=step,
            goal=f"input_text:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="input_text",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error inputting text on desktop: {e}"


@tool
def computer_use_paste_text(
    text: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = False,
    submit: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Paste text via clipboard-first desktop input, with built-in focus confirmation and verification."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_paste_text",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="paste_text_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "text_preview": text[:80],
        "mode": "paste_text",
    }
    point_hint = _computer_use_parse_point_tag(target_hint)
    if point_hint:
        guard_target["point"] = list(point_hint)
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="paste_text",
                summary=error_message or "Safety Guardian 已阻止文本粘贴动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "type_text",
        "text": text,
        "clear_first": bool(clear_first),
        "press_enter": bool(submit),
        "window_typing": True,
        "prefer_sendinput_text": True,
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if point_hint:
        step["point"] = list(point_hint)
        step["coordinate_source"] = "vision_point_tag"
    elif target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="type_text",
            step=step,
            goal=f"paste_text:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="paste_text",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error pasting text on desktop: {e}"


@tool
def computer_use_paste_files(
    paths_json: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    target_path: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    text: Optional[str] = None,
    submit: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Paste files into a desktop target via clipboard file payload, optionally with accompanying text."""
    try:
        raw_paths = json.loads(paths_json)
    except Exception as e:
        return f"Error parsing paths_json: {e}"
    if isinstance(raw_paths, str):
        file_paths = [raw_paths]
    elif isinstance(raw_paths, list):
        file_paths = [str(item) for item in raw_paths if str(item).strip()]
    else:
        return "Error: paths_json 必须是 JSON 数组或字符串。"
    if not file_paths:
        return "Error: 至少需要一个文件路径。"
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_paste_files",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    target_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target_path,
    )
    effective_window_title = (
        str(window_title or "").strip()
        or str(target_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(None, resolved_app)
    )
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="paste_files_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
        target_path=str(target_override.get("resolved_target_path") or "").strip() or None,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "target_path": str(target_override.get("resolved_target_path") or target_path or "").strip() or None,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "file_count": len(file_paths),
        "text_preview": str(text or "")[:80] or None,
        "mode": "paste_files",
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="paste_files",
                summary=error_message or "Safety Guardian 已阻止文件粘贴动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "type_text",
        "text": str(text or ""),
        "file_paths": file_paths,
        "press_enter": bool(submit),
        "window_typing": True,
        "prefer_sendinput_text": True,
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if target_override.get("resolved_target_path"):
        step["target_path"] = str(target_override.get("resolved_target_path"))
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if str((resolved_app or {}).get("appId") or "").strip().lower() == "explorer" and target_hint == "content_receiver":
        step["file_paste_strategy"] = "sendinput"
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="type_text",
            step=step,
            goal=f"paste_files:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="paste_files",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error pasting files on desktop: {e}"


@tool
def computer_use_right_click_target(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    target_text: Optional[str] = None,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Right-click a desktop target with the same guarded semantics as click_target."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_right_click_target",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="right_click_target_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    allowed, error_message = _computer_use_action_guard(
        action_type="click",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "target": target_hint,
            "window_title": effective_window_title,
            "window_handle": window_handle,
            "target_text": target_text,
            "button": "right",
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="right_click",
                summary=error_message or "Safety Guardian 已阻止桌面右键动作。",
                app_hint=app_query,
                target_hint=target_hint or target_text,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {"action": "right_click"}
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if target_text:
        step["target_text"] = target_text
    step["require_visual_guard"] = False
    step["prefer_fast_path"] = True
    step["post_action_settle_timeout_ms"] = 220
    step["post_action_settle_poll_ms"] = 120
    step["post_action_stable_rounds"] = 1
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="right_click",
            step=step,
            goal=f"right_click:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="right_click",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error right-clicking desktop target: {e}"


@tool
def computer_use_hover_target(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    target_text: Optional[str] = None,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Hover over a desktop target while keeping verification, evidence, and blocking semantics."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_hover_target",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="hover_target_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    allowed, error_message = _computer_use_action_guard(
        action_type="click",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "target": target_hint,
            "window_title": effective_window_title,
            "window_handle": window_handle,
            "target_text": target_text,
            "gesture": "hover",
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="hover",
                summary=error_message or "Safety Guardian 已阻止桌面悬停动作。",
                app_hint=app_query,
                target_hint=target_hint or target_text,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {"action": "hover"}
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if target_text:
        step["target_text"] = target_text
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="hover",
            step=step,
            goal=f"hover:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="hover",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error hovering desktop target: {e}"


@tool
def computer_use_send_hotkey(
    sequence: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    window_title: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Send a desktop hotkey with compact verification and evidence output."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_send_hotkey",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    allowed, error_message = _computer_use_action_guard(
        action_type="hotkey",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "window_title": effective_window_title,
            "sequence": sequence,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="send_hotkey",
                summary=error_message or "Safety Guardian 已阻止桌面热键动作。",
                app_hint=app_query,
                target_hint=sequence,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "hotkey",
        "sequence": sequence,
    }
    if effective_window_title:
        step["window_title"] = effective_window_title
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="hotkey",
            step=step,
            goal=f"hotkey:{app_query or effective_window_title or sequence}",
        )
        response = _computer_use_compact_response(
            action="send_hotkey",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=sequence,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error sending desktop hotkey: {e}"


@tool
def computer_use_scroll_view(
    amount: int,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    by_page: bool = False,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Scroll a desktop view with built-in verification and evidence.

    Use wheel-style scroll by default. Set `by_page=true` to use page up/down semantics.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_scroll_view",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    action_name = "page_scroll" if by_page else "scroll"
    allowed, error_message = _computer_use_action_guard(
        action_type="scroll",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "target": target_hint,
            "window_title": effective_window_title,
            "amount": amount,
            "mode": action_name,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action=action_name,
                summary=error_message or "Safety Guardian 已阻止桌面滚动动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": action_name,
    }
    if by_page:
        step["direction"] = "down" if int(amount) < 0 else "up"
        step["count"] = max(1, abs(int(amount)))
    else:
        step["amount"] = int(amount)
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
        else:
            step["name"] = target_hint
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action=action_name,
            step=step,
            goal=f"{action_name}:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action=action_name,
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error scrolling desktop view: {e}"


@tool
def computer_use_drag_pointer(
    start_point_json: str = "",
    end_point_json: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    window_title: Optional[str] = None,
    steps: int = 12,
    start_visual_locator: Optional[str] = None,
    end_visual_locator: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Drag from one point to another with built-in verification and blocking."""
    start_point = None
    end_point = None
    if str(start_point_json or "").strip():
        try:
            start_point = json.loads(start_point_json)
        except Exception as e:
            return f"Error parsing start_point_json: {e}"
    if str(end_point_json or "").strip():
        try:
            end_point = json.loads(end_point_json)
        except Exception as e:
            return f"Error parsing end_point_json: {e}"
    if not (
        (isinstance(start_point, list) and len(start_point) == 2 and isinstance(end_point, list) and len(end_point) == 2)
        or (str(start_visual_locator or "").strip() and str(end_visual_locator or "").strip())
    ):
        return "Error: drag_pointer 需要提供坐标起终点，或同时提供 start_visual_locator 和 end_visual_locator。"
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_drag_pointer",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    allowed, error_message = _computer_use_action_guard(
        action_type="drag",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "window_title": effective_window_title,
            "start_point": start_point,
            "end_point": end_point,
            "start_visual_locator": start_visual_locator,
            "end_visual_locator": end_visual_locator,
            "steps": steps,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="drag_pointer",
                summary=error_message or "Safety Guardian 已阻止拖拽动作。",
                app_hint=app_query,
                target_hint="drag",
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "drag",
        "drag_steps": max(2, int(steps)),
    }
    if isinstance(start_point, list) and len(start_point) == 2:
        step["start_point"] = [int(start_point[0]), int(start_point[1])]
    if isinstance(end_point, list) and len(end_point) == 2:
        step["end_point"] = [int(end_point[0]), int(end_point[1])]
    if effective_window_title:
        step["window_title"] = effective_window_title
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=start_visual_locator,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
        prefix="start_",
    )
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=end_visual_locator,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
        prefix="end_",
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="drag",
            step=step,
            goal=f"drag:{app_query or effective_window_title or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="drag_pointer",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint="drag",
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error dragging pointer: {e}"


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


# Export all tools for easier binding
NATIVE_TOOLS = [
    run_system_command,
    command_session_broker,
    runtime_broker,
    delegation_broker,
    read_background_output,
    send_background_input,
    terminate_background_command,
    rpa_list_robot_scripts,
    rpa_run_draft,
    rpa_run_existing_flow,
    creative_media_catalog,
    creative_media_resolutions,
    creative_media_create_job,
    creative_media_get_job,
    creative_media_list_jobs,
    creative_media_job_artifacts,
    creative_media_compile_recipe,
    creative_media_get_recipe,
    creative_media_list_recipes,
    creative_media_register_asset,
    creative_media_list_assets,
    creative_media_create_character_bible,
    creative_media_get_character_bible,
    creative_media_list_character_bibles,
    creative_media_register_keyframe,
    creative_media_get_keyframe,
    creative_media_list_keyframes,
    creative_media_create_edit_plan,
    creative_media_get_edit_plan,
    creative_media_list_edit_plans,
    creative_media_render_edit_plan,
    creative_media_get_render,
    creative_media_list_renders,
    creative_media_create_quality_job,
    creative_media_list_quality_jobs,
    creative_media_get_quality_job,
    creative_media_retry_job,
    creative_media_cost_ledger,
    creative_media_safety_events,
    computer_use_list_apps,
    computer_use_list_primitives,
    computer_use_desktop_capabilities,
    computer_use_lookup_muscle_memory,
    computer_use_list_muscle_memories,
    computer_use_resolve_execution_route,
    computer_use_execute_task,
    computer_use_launch_app,
    computer_use_ensure_window,
    computer_use_observe_scene,
    computer_use_click_target,
    computer_use_input_text,
    computer_use_paste_text,
    computer_use_paste_files,
    computer_use_right_click_target,
    computer_use_hover_target,
    computer_use_send_hotkey,
    computer_use_scroll_view,
    computer_use_drag_pointer,
    computer_use_list_windows,
    computer_use_observe,
    computer_use_find_element,
    computer_use_click,
    computer_use_type_text,
    computer_use_hotkey,
    computer_use_scroll,
    computer_use_wait_for_element,
    computer_use_capture_screenshot,
    computer_use_open_app,
    computer_use_focus_window,
    computer_use_find_and_type,
    computer_use_scroll_list,
    computer_use_click_toolbar_action,
    computer_use_execute_plan,
    read_native_file,
    share_workspace_file,
    write_native_file,
    grep_search,
    download_media_for_vision,
    web_broker,
    web_fetch,
    web_read,
    web_extract,
    web_search,
    delegate_network_task,
    http_request,
    s3_broker,
    s3_upload_file,
    s3_list_objects,
    s3_download_file,
    wait,
    list_processes,
    manage_process,
    manage_cron,
    manage_hook,
    read_audit_log,
    memory_recall,
    mem_delete,
    mem_update,
    mem_summary,
    memory_map,
    memory_map_expand,
    memory_read_day,
    ask_user,
    write_todos,
    update_todo,
    vision_media_analyzer
]
