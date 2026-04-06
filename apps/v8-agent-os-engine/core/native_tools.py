import os
import json
import shutil
import subprocess
import mimetypes
import platform
import re
import time
from datetime import datetime, timezone
import httpx
import psutil
import threading
import queue
import uuid
import sys
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

from langchain_core.tools import tool, InjectedToolCallId
from langgraph.types import Command, interrupt
from core.artifact_store import artifact_store
from core.computer_use_execution_route import (
    _compact_environment_signal_summary,
    _compact_timing_signal_summary,
    _compact_visual_signal_summary,
    build_compact_execution_route,
)
from core.storage import StorageManager
from runtimes.computer_use.primitives import list_computer_use_primitives, primitive_validation_matrix
from runtimes.rpa.promotion_gate import draft_environment_signal_summary, draft_timing_signal_summary
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import SafetyDecision, safety_guardian
from core.workspace_guard import ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from core.tools.media_downloader import download_media_for_vision
from core.tools.vision_media_analyzer import vision_media_analyzer
from core.tools.web_fetcher import web_extract, web_fetch, web_read, web_search
from runtimes.computer_use.verification_contract import (
    build_evidence_summary_payload,
    build_environment_signal_summary_payload,
    build_timing_signal_summary_payload,
    build_visual_signal_summary_payload,
    recommended_next_action_payload,
)

storage = StorageManager()

_COMPUTER_USE_APP_RESOLUTION_CACHE_TTL_MS = 3000
_COMPUTER_USE_APP_RESOLUTION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COMPUTER_USE_POINT_TAG_PATTERN = re.compile(
    r"<point>\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*</point>",
    re.IGNORECASE,
)

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
    if decision.is_allow():
        return True, None

    if decision.is_block() or not decision.allow_override:
        return False, f"Safety Guardian 已阻止该操作：{decision.reason}"

    try:
        response = interrupt(decision.to_interrupt_request(question=question, tool_call_id=tool_call_id))
    except RuntimeError as exc:
        message = str(exc)
        if "Called get_config outside of a runnable context" in message:
            return (
                False,
                f"Safety Guardian 需要审批上下文，当前验证环境已改为非中断阻断：{decision.reason}",
            )
        raise

    approved = True
    if isinstance(response, dict):
        approved = bool(response.get("approved", True))

    if not approved:
        return False, f"Safety Guardian 未获得批准，操作已取消：{decision.reason}"

    return True, None

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

    if head in {"qwen", "claude", "gemini"}:
        return f"检测到 `{head}` 可能需要 TTY 或交互输入。"

    return None


def _detect_session_preferred_command(command: str) -> str | None:
    lowered = str(command or "").strip().lower()
    if not lowered:
        return None
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
            "artifactRootHint": ".v8-agent-os-artifacts/computer_use",
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
        "elements": elements,
        "screenshotArtifact": observation.get("screenshotArtifact"),
        "sessionId": raw_result.get("sessionId"),
        "runId": raw_result.get("runId"),
    }
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
    capabilities = {}
    computer_use_runtime = _get_computer_use_runtime()
    if hasattr(computer_use_runtime, "driver") and hasattr(computer_use_runtime.driver, "capability_summary"):
        capabilities = dict(computer_use_runtime.driver.capability_summary() or {})
    return json.dumps(
        {
            "ok": True,
            "action": "desktop_capabilities",
            "driver": capabilities,
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
) -> str:
    """Run an existing .robot flow through RPARuntime without requiring trace compilation."""
    normalized_robot_file = str(robot_file or "").strip()
    if not normalized_robot_file:
        return "Error: robot_file 不能为空。"
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
        return _rpa_compact_run_existing_flow_response(
            raw_result=dict(raw_result or {}),
            robot_file=normalized_robot_file,
            cwd=cwd,
            output_dir=output_dir,
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
) -> str:
    """Run an existing RPA draft script through RPARuntime."""
    normalized_script_id = str(script_id or "").strip()
    if not normalized_script_id:
        return "Error: script_id 不能为空。"
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
        return _rpa_compact_run_draft_response(
            raw_result=dict(raw_result or {}),
            script_id=normalized_script_id,
            cwd=cwd,
            output_dir=output_dir,
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
                "请改用 `run_system_command` 并设置 `mode=session` 启动它，随后配合 "
                "`read_background_output`、`send_background_input` 和 "
                "`terminate_background_command` 完成交互与收尾。"
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
        return output
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after 120 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"

@tool
def list_native_directory(path: str) -> str:
    """List the contents of a directory on the host filesystem.
    
    Arguments:
        path (str): The absolute path of the directory to list.
    """
    try:
        target_path = Path(path)
        if not target_path.exists():
            return f"Error: Path '{path}' does not exist."
        if not target_path.is_dir():
            return f"Error: Path '{path}' is not a directory."
            
        items = []
        for item in target_path.iterdir():
            file_type = "DIR" if item.is_dir() else "FILE"
            size = item.stat().st_size if item.is_file() else "-"
            items.append(f"[{file_type}] {item.name} (Size: {size} bytes)")
            
        return "\n".join(items) if items else "Directory is empty."
    except Exception as e:
        return f"Error listing directory: {str(e)}"

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

@tool
def inspect_and_move_media(path: str) -> str:
    """[Compatibility] Inspect a binary media file and move it into the workspace.
    
    If the user asks you to analyze or show an image/video located randomly on their OS, 
    use this tool. It will extract its size, and copy it into the active workspace so you 
    can return the HTTP URL to the user.
    
    Arguments:
        path (str): Absolute native path to the media file.
    """
    try:
        source_path = Path(path)
        if not source_path.exists() or not source_path.is_file():
            return f"Error: Media file '{path}' does not exist."
            
        size_bytes = source_path.stat().st_size
        mime_type, _ = mimetypes.guess_type(path)
        
        runtime_context = get_runtime_context()
        workspace_dir = Path(
            workspace_resolution_service.resolve_workspace_path(
                runtime_kind=str(runtime_context.get("runtime_kind") or "chat"),
                session_id=str(runtime_context.get("session_id") or "") or None,
                explicit_workspace_path=str(runtime_context.get("workspace_path") or "") or None,
            )
        )
        workspace_dir = ensure_workspace_auto_create_allowed(
            workspace_dir,
            source="native_tools.inspect_and_move_media",
            allow_missing=True,
        )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy to workspace
        target_path = workspace_dir / source_path.name
        
        # Avoid unnecessary copy if it's already in the workspace
        if str(source_path.absolute()) != str(target_path.absolute()):
            shutil.copy2(source_path, target_path)
            
        artifact = artifact_store.record_local_file(
            file_path=source_path,
            session_id=runtime_context.get("session_id"),
            run_id=runtime_context.get("run_id"),
            workspace_path=str(target_path),
            metadata={"source": "inspect_and_move_media"},
            source_component="native_tools",
            node="inspect_and_move_media",
        )
        preview_url = str(artifact.get("previewUrl") or artifact.get("contentUrl") or "")
        
        return (
            f"Media Inspected & Mounted!\n"
            f"Original Path: {path}\n"
            f"Size: {size_bytes / (1024*1024):.2f} MB\n"
            f"MIME Type: {mime_type or 'Unknown'}\n"
            f"Artifact ID: {artifact['artifactId']}\n"
            f"--- ACTION REQUIRED ---\n"
            f"To show this media to the user in the Chat UI, return the following markdown element in your final response:\n"
            f"![{source_path.name}]({preview_url})\n"
        )
            
    except Exception as e:
        return f"Error moving media file: {str(e)}"


def _launch_background_command(
    command: str,
    *,
    tool_call_id: str = "",
) -> dict[str, Any]:
    interactive_reason = _detect_interactive_command(command)
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
        run_id=runtime_context.get("run_id"),
        interactive=interactive_mode,
    )
    bg_proc.command_id = cmd_id
    _bg_processes[cmd_id] = bg_proc

    initial_chunks = []
    deadline = time.time() + 3.0
    while time.time() < deadline:
        chunk = bg_proc.get_new_output()
        if chunk:
            initial_chunks.append(chunk)
            if len("".join(initial_chunks)) >= 512:
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
        },
        runtime_context=runtime_context,
    )
    return {
        "commandId": cmd_id,
        "mode": "interactive" if interactive_mode else "background",
        "tty": tty_label,
        "runId": runtime_context.get("run_id"),
        "status": status,
        "interactive": interactive_mode,
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
        return f"Request failed: {str(e)}"

@tool
def wait(seconds: int) -> str:
    """Suspends execution for the given number of seconds.
    Use this to pause and wait for a background service to start or a state to change.
    
    Arguments:
        seconds (int): Number of seconds to wait.
    """
    try:
        time.sleep(seconds)
        return f"Successfully waited for {seconds} seconds."
    except Exception as e:
        return f"Error waiting: {str(e)}"

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
    """Delete a completely false or severely outdated knowledge item from memory by its ID. 
    This performs a deep cascade delete across the filesystem, Vector Store, and Knowledge Graph.
    
    Arguments:
        fact_id (str): The unique ID of the fact to delete (e.g. "fact-a1b2c3d4").
    """
    try:
        success = _get_memory_runtime().delete_knowledge(fact_id=fact_id)
        if success:
            return f"✓ Completely purged '{fact_id}' from memory."
        else:
            return f"Error: Knowledge item '{fact_id}' not found."
    except Exception as e:
        return f"Error deleting from memory: {str(e)}"

@tool
def mem_update(fact_id: str, new_content: str) -> str:
    """Update an existing knowledge item to correct erroneous information or append new context.
    This replaces the text of the existing fact across all storage layers.
    
    Arguments:
        fact_id (str): The unique ID of the fact to update (e.g. "fact-a1b2c3d4").
        new_content (str): The full corrected text for this memory item.
    """
    try:
        success = _get_memory_runtime().update_knowledge(fact_id=fact_id, new_fact=new_content)
        if success:
            return f"✓ Updated '{fact_id}' with new content."
        else:
            return f"Error: Knowledge item '{fact_id}' not found."
    except Exception as e:
        return f"Error updating memory: {str(e)}"


        
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
class BackgroundProcess:
    def __init__(self, command: str, *, run_id: str | None = None, interactive: bool = False):
        self.command = command
        self.run_id = run_id
        self.interactive = interactive
        self.output_queue = queue.Queue()
        self.output_history = []
        self.is_running = True
        self.pty_win = None
        self.proc = None
        self.fd = None
        self.uses_tty = False
        self.started_at = time.time()
        self.last_output_at = self.started_at
        self.last_input_at = None
        
        if sys.platform == "win32" and HAS_WINPTY:
            self.pty_win = PTY(80, 24)
            self.uses_tty = True
            self.pty_win.spawn("cmd.exe")
            time.sleep(0.5)
            # Send the command to cmd
            self.pty_win.write(command + "\r\n")
        elif sys.platform != "win32":
            pid, self.fd = pty.fork()
            if pid == 0:
                os.execlp("sh", "sh", "-c", command)
            else:
                self.proc = pid
                self.uses_tty = True
        else:
            # Fallback for Windows without pywinpty
            self.proc = subprocess.Popen(
                command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1
            )

        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _read_output(self):
        try:
            if sys.platform == "win32" and HAS_WINPTY:
                while self.is_running and self.pty_win.isalive():
                    try:
                        data = self.pty_win.read()
                        if data:
                            self.last_output_at = time.time()
                            for char in data:
                                self.output_queue.put(char)
                        else:
                            time.sleep(0.05)
                    except Exception:
                        break
            elif sys.platform != "win32" and self.fd is not None:
                while self.is_running:
                    try:
                        data = os.read(self.fd, 4096).decode('utf-8', 'replace')
                        if data:
                            self.last_output_at = time.time()
                            for char in data:
                                self.output_queue.put(char)
                        else:
                            break
                    except OSError:
                        break
            else:
                while self.is_running and self.proc:
                    char = self.proc.stdout.read(1)
                    if not char:
                        break
                    self.last_output_at = time.time()
                    self.output_queue.put(char)
        except Exception:
            pass
        finally:
            self.is_running = False

    def get_new_output(self) -> str:
        chars = []
        while not self.output_queue.empty():
            chars.append(self.output_queue.get())
        new_text = "".join(chars)
        if new_text:
            self.output_history.append(new_text)
        return new_text

    def write_input(self, data: str):
        normalized_data = _normalize_background_input(data)
        self.last_input_at = time.time()
        if sys.platform == "win32" and HAS_WINPTY:
            self.pty_win.write(normalized_data)
        elif sys.platform != "win32" and self.fd is not None:
            os.write(self.fd, normalized_data.encode('utf-8'))
        elif self.proc and self.proc.stdin:
            self.proc.stdin.write(normalized_data)
            self.proc.stdin.flush()

    def status_snapshot(self) -> dict:
        return {
            "command_id": getattr(self, "command_id", None),
            "command": self.command,
            "is_running": self.is_running,
            "uses_tty": self.uses_tty,
            "interactive": self.interactive,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "seconds_since_output": round(max(0.0, time.time() - self.last_output_at), 2),
            "seconds_since_input": None
            if self.last_input_at is None
            else round(max(0.0, time.time() - self.last_input_at), 2),
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


def list_background_process_snapshots(run_id: str | None = None) -> list[dict]:
    snapshots: list[dict] = []
    normalized_run_id = str(run_id or "").strip() or None
    for command_id, bg_proc in list(_bg_processes.items()):
        process_run_id = str(getattr(bg_proc, "run_id", "") or "").strip() or None
        if normalized_run_id and process_run_id != normalized_run_id:
            continue
        status = dict(bg_proc.status_snapshot())
        command = str(getattr(bg_proc, "command", "") or "").strip()
        command_preview = command if len(command) <= 240 else f"{command[:237]}..."
        snapshots.append({
            "processId": str(command_id),
            "commandId": str(command_id),
            "runId": process_run_id,
            "title": command.splitlines()[0][:96] if command else str(command_id),
            "commandPreview": command_preview,
            "status": "running" if bool(status.get("is_running")) else "stopped",
            "interactive": bool(status.get("interactive")),
            "usesTty": bool(status.get("uses_tty")),
            "canTerminate": True,
            "canInput": bool(status.get("interactive")),
            "startedAt": datetime.fromtimestamp(
                float(status.get("started_at") or time.time()),
                tz=timezone.utc,
            ).isoformat(),
            "secondsSinceOutput": status.get("seconds_since_output"),
            "secondsSinceInput": status.get("seconds_since_input"),
        })
    snapshots.sort(key=lambda item: str(item.get("startedAt") or ""))
    return snapshots

@tool
def start_background_command(
    command: str,
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
    """
    try:
        launched = _launch_background_command(command, tool_call_id=tool_call_id)
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
            f"TTY: {launched['tty']}\n"
            f"RunId: {launched['runId'] or 'n/a'}\n"
            f"Status: {json.dumps(launched['status'], ensure_ascii=False)}\n"
            f"Initial output:\n{initial_section}{guidance}"
        )
    except Exception as e:
        return f"Error starting background command: {e}"


@tool
def run_system_command(
    command: str,
    mode: str = "auto",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Run a system command through a unified command surface.

    mode=auto:
    - 短命令/非交互命令直接同步执行并返回结果
    - 交互式或长驻命令自动切到 session 模式并返回 commandId

    mode=sync:
    - 强制同步执行，适合短命令

    mode=session:
    - 强制后台/交互模式，返回 commandId
    """
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode not in {"auto", "sync", "session"}:
        return "Error: mode 必须是 auto、sync 或 session。"

    interactive_reason = _detect_interactive_command(command)
    session_reason = _detect_session_preferred_command(command)
    prefer_session = interactive_reason is not None or session_reason is not None
    effective_mode = normalized_mode
    if normalized_mode == "auto":
        effective_mode = "session" if prefer_session else "sync"

    if effective_mode == "sync":
        return execute_system_command.func(command=command, tool_call_id=tool_call_id)

    if effective_mode == "session":
        try:
            launched = _launch_background_command(command, tool_call_id=tool_call_id)
            payload = {
                "kind": "command_session",
                "mode": "session",
                "commandId": launched["commandId"],
                "sessionId": launched["commandId"],
                "interactive": bool(launched["interactive"]),
                "tty": launched["tty"],
                "runId": launched["runId"],
                "reason": interactive_reason or session_reason or "显式 session 模式",
            }
            if launched["initialOutput"]:
                payload["initialOutput"] = launched["initialOutput"]
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            return f"Error starting session command: {exc}"

    return "Error: 未能解析命令执行模式。"

@tool
def read_background_output(command_id: str) -> str:
    """Read the latest output from a background command.
    
    Arguments:
        command_id (str): The ID returned by `run_system_command(mode="session")` or `start_background_command`.
    """
    if command_id not in _bg_processes:
        return f"Error: No active background command with ID {command_id}."
    bg_proc = _bg_processes[command_id]
    new_out = bg_proc.get_new_output()
    if not bg_proc.is_running:
        rcode = bg_proc.process.poll() if hasattr(bg_proc.proc, "poll") else 0
        del _bg_processes[command_id]
        return f"{new_out}\n\n[Process terminated with exit code {rcode}]"
    if not new_out:
        status = bg_proc.status_snapshot()
        if bg_proc.interactive:
            return (
                "No new output yet. 该交互式命令可能正在等待输入或初始化终端。\n"
                f"Status: {json.dumps(status, ensure_ascii=False)}"
            )
        return f"No new output yet. Status: {json.dumps(status, ensure_ascii=False)}"
    return new_out

@tool
def send_background_input(command_id: str, input_text: str) -> str:
    """Send input (like 'y' or option choices) to an interactive background command.
    Remember to include '\\n' if simulating the Enter key.
    
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
        bg_proc.write_input(input_text)
        time.sleep(0.5)  # Wait for response
        resp = bg_proc.get_new_output()
        status = bg_proc.status_snapshot()
        return f"Input sent. New output:\n{resp}\n\nStatus: {json.dumps(status, ensure_ascii=False)}"
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
) -> str:
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
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error resolving desktop execution route: {e}"


@tool
def computer_use_launch_app(
    app: str,
    *,
    target: Optional[str] = None,
    wait_timeout_ms: int = 12000,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Launch a desktop app with minimal parameters.

    Provide a human app name or alias. The tool resolves the best app match, opens it, and returns
    verification/blocking/evidence in a compact format.
    """
    app_query = str(app or "").strip()
    if not app_query:
        return "Error: app 不能为空。"
    resolved_app = _computer_use_resolve_app(app_query)
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
        return _computer_use_compact_response(
            action="launch_app",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target,
            resolved_app=resolved_app,
            expected_window_title=expected_window_title,
            strict_expected_window_title=bool(launch_override.get("strict_expected_window_title")),
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
                return _computer_use_compact_response(
                    action="launch_app",
                    raw_result=recovered_result,
                    app_hint=app_query,
                    target_hint=target,
                    resolved_app=resolved_app,
                    expected_window_title=expected_window_title,
                    strict_expected_window_title=bool(launch_override.get("strict_expected_window_title")),
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
) -> str:
    """Ensure a desktop window is bound and focused before the next action.

    Use this to rebind/focus the correct application window instead of continuing with a stale foreground context.
    """
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
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
        return _computer_use_compact_response(
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
) -> str:
    """Click a semantic desktop target with built-in verification and blocking.

    Prefer short semantic hints such as 'primary_input', 'address_bar', 'confirm_action', or a visible element name.
    The runtime will decide whether to use structured lookup, anchor targeting, or a guarded fallback path.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action="double_click" if double else "click",
            summary=error_message or "Safety Guardian 已阻止桌面点击动作。",
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action=step["action"],
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
) -> str:
    """Input text into a desktop target using the simplest possible interface.

    If `target` is provided, it is treated as a semantic target hint. If `target` is omitted, the tool falls back to
    window-level typing and blocks when editable focus cannot be confirmed.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action="input_text",
            summary=error_message or "Safety Guardian 已阻止桌面输入动作。",
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="input_text",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
) -> str:
    """Paste text via clipboard-first desktop input, with built-in focus confirmation and verification."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action="paste_text",
            summary=error_message or "Safety Guardian 已阻止文本粘贴动作。",
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="paste_text",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
        return _computer_use_guard_failure_response(
            action="paste_files",
            summary=error_message or "Safety Guardian 已阻止文件粘贴动作。",
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="paste_files",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
) -> str:
    """Right-click a desktop target with the same guarded semantics as click_target."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action="right_click",
            summary=error_message or "Safety Guardian 已阻止桌面右键动作。",
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="right_click",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
) -> str:
    """Hover over a desktop target while keeping verification, evidence, and blocking semantics."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action="hover",
            summary=error_message or "Safety Guardian 已阻止桌面悬停动作。",
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="hover",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
) -> str:
    """Send a desktop hotkey with compact verification and evidence output."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action="send_hotkey",
            summary=error_message or "Safety Guardian 已阻止桌面热键动作。",
            app_hint=app_query,
            target_hint=sequence,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="send_hotkey",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=sequence,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
) -> str:
    """Scroll a desktop view with built-in verification and evidence.

    Use wheel-style scroll by default. Set `by_page=true` to use page up/down semantics.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
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
        return _computer_use_guard_failure_response(
            action=action_name,
            summary=error_message or "Safety Guardian 已阻止桌面滚动动作。",
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action=action_name,
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
        return _computer_use_guard_failure_response(
            action="drag_pointer",
            summary=error_message or "Safety Guardian 已阻止拖拽动作。",
            app_hint=app_query,
            target_hint="drag",
            resolved_app=resolved_app,
            window_title=effective_window_title,
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
        return _computer_use_compact_response(
            action="drag_pointer",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint="drag",
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
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
    """向受信任的远端 V8 节点显式委派任务，并等待最终结果返回。"""
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
    read_background_output,
    send_background_input,
    terminate_background_command,
    execute_system_command,
    start_background_command,
    rpa_list_robot_scripts,
    rpa_run_draft,
    rpa_run_existing_flow,
    computer_use_list_apps,
    computer_use_list_primitives,
    computer_use_desktop_capabilities,
    computer_use_lookup_muscle_memory,
    computer_use_list_muscle_memories,
    computer_use_resolve_execution_route,
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
    list_native_directory,
    read_native_file,
    write_native_file,
    grep_search,
    inspect_and_move_media,
    download_media_for_vision,
    web_fetch,
    web_read,
    web_extract,
    web_search,
    delegate_network_task,
    http_request,
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
    ask_user,
    write_todos,
    update_todo,
    vision_media_analyzer
]
