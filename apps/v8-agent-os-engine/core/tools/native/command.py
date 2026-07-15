import hashlib
import json
import locale
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import psutil

if sys.platform == "win32":
    try:
        from winpty import PTY
        HAS_WINPTY = True
    except ImportError:
        HAS_WINPTY = False
else:
    import pty
    HAS_WINPTY = False

from langchain_core.tools import InjectedToolCallId, tool

from core.tools.native.command_governance import (
    _detect_interactive_command,
    _detect_session_preferred_command,
    _strip_leading_shell_cwd,
    _windows_shell_syntax_violation_payload,
)
from core.tools.native.tool_governance import (
    _enforce_safety_decision,
    _raise_runtime_governance_exception_if_needed,
)
from core.tools.native.workspace_governance import (
    _workspace_inventory_block_payload,
    _workspace_inventory_gate_required,
    _workspace_inventory_status,
)
from core.tools.tool_execution_envelope import ToolExecutionEnvelope
from core.workspace_capability import preflight_command_workspace
from core.workspace_state_digest import command_may_change_workspace, mark_workspace_state_stale
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian

__all__ = [
    "execute_governed_argv",
    "_suggest_npx_yes_command",
    "_normalize_background_input",
    "_decode_background_input_escapes",
    "_normalize_terminal_key_name",
    "_terminal_keys_from_text",
    "_terminal_key_sequence",
    "_terminal_input_bytes_preview",
    "_write_winpty_input",
    "_terminate_run_background_commands",
    "execute_system_command",
    "_launch_background_command",
    "_strip_command_internal_markers",
    "_extract_chat_cli_command_head",
    "_normalize_background_command_profile",
    "_detect_background_command_profile",
    "_detect_chat_cli_variant",
    "_normalize_chat_cli_text",
    "_looks_like_prompt_line",
    "_strip_chat_cli_prompt_tail",
    "_looks_like_chat_cli_border_line",
    "_contains_v8_worker_result_marker",
    "_extract_v8_worker_result_block_text",
    "_count_chat_cli_noise_lines",
    "_looks_like_chat_cli_noise_line",
    "_sanitize_chat_cli_semantic_text",
    "_collapse_chat_cli_cumulative_lines",
    "_longest_overlap_suffix_prefix",
    "_consume_chat_cli_semantic_suffix",
    "_merge_chat_cli_turn_text",
    "_strip_chat_cli_input_echo",
    "_digest_chat_cli_text",
    "_slice_chat_cli_delta_chunk",
    "_is_skills_add_command",
    "_notify_skills_inventory_command_completed",
    "_extract_command_head",
    "_build_command_diagnostics_snapshot",
    "_locale_looks_utf8",
    "_preferred_utf8_locale",
    "_build_terminal_env_overrides",
    "_build_winpty_bootstrap_commands",
    "_extend_command_diagnostics_for_terminal",
    "_run_winpty_bootstrap",
    "_strip_terminal_bootstrap_noise",
    "_normalize_terminal_snapshot_lines",
    "_looks_like_terminal_volatile_line",
    "_build_stable_terminal_snapshot",
    "_looks_like_terminal_mojibake",
    "_derive_terminal_encoding_status",
    "_terminal_snapshot_looks_like_prompt",
    "_terminal_snapshot_looks_like_menu",
    "_terminal_menu_suggested_keys",
    "_terminal_snapshot_looks_busy",
    "_windows_text_encoding_candidates",
    "_normalize_shell_dialect",
    "_resolve_shell_dialect",
    "_shell_command_argv",
    "_decode_completed_process_bytes",
    "_normalize_status_timestamp",
    "_preview_terminal_frame",
    "_contains_terminal_escape",
    "_truncate_terminal_text",
    "_SimpleTerminalScreen",
    "BackgroundProcess",
    "_bg_processes",
    "_prune_stale_background_processes",
    "list_background_process_snapshots",
    "start_background_command",
    "run_system_command",
    "_resolve_command_session_process",
    "_command_session_state_from_status",
    "_command_session_summary_for_state",
    "_command_session_recommended_next_action",
    "_command_session_preview_text",
    "_strip_command_echo_noise",
    "_looks_like_terminal_spinner_noise",
    "_command_session_debug_payload",
    "_command_session_result_preview_fields",
    "_command_session_payload",
    "command_session_broker",
    "_build_terminal_status_tool_view",
    "_extract_preferred_terminal_snapshot",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
]


_SHELL_DIALECTS = {"auto", "powershell", "pwsh", "cmd", "bash", "sh"}


def _normalize_shell_dialect(value: str | None) -> str:
    dialect = str(value or "auto").strip().lower().replace("powershell.exe", "powershell").replace("cmd.exe", "cmd")
    if dialect not in _SHELL_DIALECTS:
        raise ValueError("shell_dialect 必须是 auto、powershell、pwsh、cmd、bash 或 sh。")
    return dialect


def _resolve_shell_dialect(command: str, requested: str | None = "auto") -> str:
    dialect = _normalize_shell_dialect(requested)
    if sys.platform != "win32":
        if dialect in {"auto", "bash"}:
            return "bash" if shutil.which("bash") else "sh"
        if dialect == "sh":
            return "sh"
        raise ValueError(f"当前平台不支持 Windows shell dialect: {dialect}")
    if dialect != "auto":
        return dialect

    stripped = _strip_leading_shell_cwd(command)
    lowered = str(stripped or "").strip().lower()
    if re.match(r"^(?:cmd(?:\.exe)?\s+/[dqs]*c\b)", lowered):
        return "cmd"
    if re.match(r"^(?:pwsh(?:\.exe)?\b)", lowered):
        return "pwsh"
    if re.match(r"^(?:powershell(?:\.exe)?\b)", lowered):
        return "powershell"
    if re.match(r"^(?:bash(?:\.exe)?\b|sh\b)", lowered):
        return "bash"
    if (
        re.search(r"\$env:[A-Za-z_]", stripped, re.IGNORECASE)
        or re.search(r"(^|[;&|]\s*)(?:Get|Set|New|Remove|Copy|Move|Test)-[A-Za-z]+", stripped, re.IGNORECASE)
        or re.search(r"\|\s*(?:Where|ForEach|Select)-Object\b", stripped, re.IGNORECASE)
    ):
        return "powershell"
    if (
        re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", stripped)
        or re.search(r"(^|[;&|]\s*)set\s+[A-Za-z_][A-Za-z0-9_]*=", stripped, re.IGNORECASE)
        or re.search(r"(^|[;&|]\s*)dir\s+/[A-Za-z]", stripped, re.IGNORECASE)
        or "&&" in stripped
        or "||" in stripped
    ):
        return "cmd"
    return "powershell"


def _shell_command_argv(command: str, shell_dialect: str) -> list[str]:
    dialect = _normalize_shell_dialect(shell_dialect)
    if dialect == "auto":
        dialect = _resolve_shell_dialect(command, dialect)
    if dialect == "cmd":
        if sys.platform != "win32":
            raise ValueError("cmd dialect 仅支持 Windows。")
        return [str(os.environ.get("COMSPEC") or "cmd.exe"), "/d", "/s", "/c", command]
    if dialect in {"powershell", "pwsh"}:
        executable = "pwsh" if dialect == "pwsh" else "powershell.exe"
        resolved = shutil.which(executable)
        if not resolved:
            raise ValueError(f"未找到 {executable}，无法执行 {dialect} 命令。")
        args = [resolved, "-NoLogo", "-NoProfile", "-NonInteractive"]
        if dialect == "powershell":
            args.extend(["-ExecutionPolicy", "Bypass"])
        return [*args, "-Command", command]
    executable = shutil.which("bash" if dialect == "bash" else "sh")
    if not executable:
        raise ValueError(f"未找到 {dialect} shell。")
    return [executable, "-lc" if dialect == "bash" else "-c", command]


def execute_governed_argv(
    argv: list[str],
    *,
    cwd: str = "",
    allowed_extra_roots: list[str] | tuple[str, ...] = (),
    tool_call_id: str = "",
    timeout_seconds: int = 120,
    action_family: str = "command",
    action_subject: str = "",
) -> dict[str, Any]:
    """Execute a pre-resolved argv without a shell while preserving command governance."""
    normalized_argv = [str(item) for item in list(argv or [])]
    if not normalized_argv or not normalized_argv[0].strip():
        return {
            "ok": False,
            "kind": "command_invalid_argv",
            "summary": "缺少可执行程序。",
            "recommendedNextAction": "提供由 Engine 解析后的程序和参数列表。",
        }

    timeout_seconds = max(1, min(int(timeout_seconds or 120), 900))
    command = subprocess.list2cmdline(normalized_argv) if os.name == "nt" else shlex.join(normalized_argv)
    runtime_context = get_runtime_context()
    existing_extra_roots = runtime_context.get("allowed_extra_roots") or runtime_context.get("allowedExtraRoots") or []
    if isinstance(existing_extra_roots, str):
        existing_extra_roots = [existing_extra_roots]
    governed_context = dict(runtime_context)
    governed_context["allowed_extra_roots"] = [
        *[str(item) for item in list(existing_extra_roots or []) if str(item or "").strip()],
        *[str(item) for item in list(allowed_extra_roots or []) if str(item or "").strip()],
    ]

    workspace_preflight = preflight_command_workspace(
        command,
        cwd=cwd or None,
        runtime_context=governed_context,
    )
    if not workspace_preflight.get("ok"):
        return {
            "ok": False,
            "kind": "workspace_boundary_block",
            "summary": workspace_preflight.get("summary"),
            "error": workspace_preflight.get("error"),
            "violations": workspace_preflight.get("violations") or [],
            "recommendedNextAction": "仅使用当前工作区文件作为脚本输入，或由用户显式授予额外 workspace/root。",
        }

    inventory_status = _workspace_inventory_status(governed_context)
    if (
        not inventory_status.get("hasInventoryToken")
        and inventory_status.get("nonEmpty")
        and _workspace_inventory_gate_required(command, workspace_root=str(inventory_status.get("workspaceRoot") or ""))
    ):
        return _workspace_inventory_block_payload(governed_context, operation="command", subject=command)

    allowed, error_message = _enforce_safety_decision(
        safety_guardian.assess_system_command(command, runtime_context=governed_context),
        tool_call_id=tool_call_id,
        question=f"Safety Guardian 检测到脚本执行存在风险，是否继续？\n\n命令：{command}",
    )
    if not allowed:
        return {
            "ok": False,
            "kind": "safety_blocked",
            "summary": error_message or "Safety Guardian 已阻止脚本执行。",
            "recommendedNextAction": "按 Safety Guardian 的原因调整输入，或改用已批准的方法。",
        }

    resolved_cwd = str(workspace_preflight.get("cwd") or "").strip() or None
    deadline_ms = timeout_seconds * 1000
    with ToolExecutionEnvelope(
        tool_name="run_skill_script" if action_family == "skill_script" else "run_system_command",
        family="command",
        deadline_ms=deadline_ms,
        retry_limit=1,
    ) as envelope:
        try:
            result = subprocess.run(
                normalized_argv,
                shell=False,
                capture_output=True,
                cwd=resolved_cwd,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return envelope.failure_payload(
                summary=f"脚本执行超过 {timeout_seconds} 秒上限。",
                failure_class="deadline_exceeded",
                error="script execution timed out",
                retryable=False,
                recommended_next_action="缩小脚本任务，或改用具备可恢复会话的专项 runtime。",
            )

    stdout, stdout_encoding = _decode_completed_process_bytes(result.stdout or b"", stream_name="stdout")
    stderr, stderr_encoding = _decode_completed_process_bytes(result.stderr or b"", stream_name="stderr")
    output_limit = 220_000
    stdout_truncated = len(stdout) > output_limit
    stderr_truncated = len(stderr) > output_limit
    visible_stdout = stdout[:output_limit]
    visible_stderr = stderr[:output_limit]

    safety_guardian.observe_post_action(
        action_family=action_family,
        summary=f"已执行受治理脚本：{action_subject or Path(normalized_argv[-1]).name}",
        details={
            "command": command,
            "cwd": resolved_cwd,
            "workspaceBinding": workspace_preflight.get("binding"),
            "return_code": result.returncode,
            "encodingDiagnostics": {"stdout": stdout_encoding, "stderr": stderr_encoding},
        },
        runtime_context=governed_context,
    )
    mark_workspace_state_stale(governed_context, reason=action_family, subject=command)
    if result.returncode == 0:
        _notify_skills_inventory_command_completed(command)

    return {
        "ok": result.returncode == 0,
        "kind": "skill_script_result" if action_family == "skill_script" else "command_result",
        "summary": "脚本执行成功。" if result.returncode == 0 else f"脚本执行失败，退出码 {result.returncode}。",
        "returnCode": result.returncode,
        "stdout": visible_stdout,
        "stderr": visible_stderr,
        "stdoutChars": len(stdout),
        "stderrChars": len(stderr),
        "stdoutTruncated": stdout_truncated,
        "stderrTruncated": stderr_truncated,
        "recommendedNextAction": "继续按 SKILL.md 验证后续产物。" if result.returncode == 0 else "根据错误输出修复输入或环境后再运行。",
    }


def _agent_preview_text(value: Any, *, limit: int = 700) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


_AGENT_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[78]|\x1b[@-_]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _compat_native_attr(name: str, local_value: Any | None = None) -> Any:
    native = sys.modules.get("core.native_tools")
    if native is not None and hasattr(native, name):
        value = getattr(native, name)
        if local_value is None or value is not local_value:
            return value
    if local_value is not None:
        return local_value
    return globals().get(name)


def _suggest_npx_yes_command(command: str) -> str | None:
    stripped = str(command or "").strip()
    if re.search(r"(?i)(^|\s)(--yes|-y)(\s|$)", stripped):
        return None
    if re.search(r"(?i)(^|[;&|]\s*)npx\s+create-", stripped):
        return re.sub(r"(?i)\bnpx\s+", "npx --yes ", stripped, count=1)
    if re.search(r"(?i)(^|[;&|]\s*)npm\s+(?:create|init)\s+(?:vite|create-vite)", stripped):
        return re.sub(r"(?i)\bnpm\s+(create|init)\s+", r"npm \1 --yes ", stripped, count=1)
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


def _normalize_terminal_key_name(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    token = token.replace("-", "").replace("_", "").replace(" ", "")
    return {
        "arrowup": "up",
        "uparrow": "up",
        "arrowdown": "down",
        "downarrow": "down",
        "↓": "down",
        "arrowleft": "left",
        "leftarrow": "left",
        "←": "left",
        "arrowright": "right",
        "rightarrow": "right",
        "→": "right",
        "↑": "up",
        "newline": "enter",
        "submit": "enter",
    }.get(token, token)


def _terminal_keys_from_text(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if all(char in "↑↓←→ \t\r\n" for char in text):
        return [_normalize_terminal_key_name(char) for char in text if char in "↑↓←→"]
    normalized = re.sub(r"[,，、+/]+", " ", text)
    parts = [part for part in normalized.split() if part]
    if not parts:
        return []
    keys = [_normalize_terminal_key_name(part) for part in parts]
    if all(key in _TERMINAL_KEY_ALIASES for key in keys):
        return keys
    return []


def _terminal_key_sequence(
    *,
    input_text: str,
    keys: list[str] | None,
    submit: bool,
) -> tuple[str, list[str], bool, bool]:
    explicit_keys = [_normalize_terminal_key_name(item) for item in (keys or []) if _normalize_terminal_key_name(item)]
    inferred_keys = explicit_keys or _terminal_keys_from_text(input_text)
    if not inferred_keys:
        return "", [], False, False
    accepted_keys = [key for key in inferred_keys if key in _TERMINAL_KEY_ALIASES]
    if len(accepted_keys) != len(inferred_keys):
        return "", [], False, False
    submitted_enter = False
    if submit and "enter" not in accepted_keys and "return" not in accepted_keys and "回车" not in accepted_keys:
        accepted_keys = [*accepted_keys, "enter"]
        submitted_enter = True
    sequence = "".join(_TERMINAL_KEY_ALIASES[key] for key in accepted_keys)
    return sequence, accepted_keys, submitted_enter, True


def _terminal_input_bytes_preview(data: str, *, limit: int = 160) -> str:
    raw = str(data or "").encode("utf-8", errors="replace")
    preview = raw[:limit].hex(" ")
    if len(raw) > limit:
        preview += f" ... (+{len(raw) - limit} bytes)"
    return preview


def _write_winpty_input(pty_win: Any, data: str) -> None:
    """Feed Windows PTY input in a way that mimics a user pressing Enter.

    Some TUI CLIs (notably AI coding agents) do not reliably submit prompts when a
    full `text + CRLF` payload is written in one shot. Splitting text and Enter
    into separate writes makes the interaction behave much closer to a real user.
    """
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n")
    segments = normalized.split("\n")
    last_index = len(segments) - 1

    def _write_segment(segment: str) -> None:
        cursor = 0
        while cursor < len(segment):
            if segment.startswith("\x1b[", cursor) and cursor + 2 < len(segment):
                sequence = segment[cursor:cursor + 3]
                pty_win.write(sequence)
                time.sleep(0.05)
                cursor += 3
                continue
            pty_win.write(segment[cursor])
            cursor += 1
            time.sleep(0.005)

    for index, segment in enumerate(segments):
        if segment:
            _write_segment(segment)
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


@tool
def execute_system_command(
    command: str,
    cwd: str = "",
    shell_dialect: str = "auto",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Run one short, non-interactive shell command and return a bounded result.
    
    CRITICAL USAGE RULES:
    1. Use this for quick checks such as versions, directory listing, git status, grep/rg, or one short script.
    2. This tool blocks execution. If the command asks for user input, it can hang and time out.
    3. For installers, scaffolding, dev servers, TUI menus, password prompts, or long-running/watch commands, use `run_system_command(mode="auto")` so V8OS can open an observable terminal session.
    4. Do not use shell writes as a shortcut for known source/text edits; read the file first and use file tools when possible.
    5. On Windows set shell_dialect explicitly when syntax is shell-specific. Do not mix cmd (`%VAR%`, `dir /b`) and PowerShell (`$env:VAR`, `Get-ChildItem`) syntax in one command.
    
    Arguments:
        command (str): The command to execute natively.
    """
    try:
        resolved_shell_dialect = _resolve_shell_dialect(command, shell_dialect)
        shell_violation = _windows_shell_syntax_violation_payload(command, shell_dialect=resolved_shell_dialect)
        if shell_violation:
            return json.dumps(shell_violation, ensure_ascii=False, indent=2)
        interactive_reason = _detect_interactive_command(command)
        if interactive_reason:
            return (
                f"Error: {interactive_reason}\n"
                "请改用 `command_session_broker(mode=start)` 启动命令会话；"
                "后续观察、继续输入和终止都统一走 `command_session_broker`。"
            )
        session_reason = _detect_session_preferred_command(command)
        if session_reason:
            return (
                f"Error: {session_reason}\n"
                "请改用 `command_session_broker(mode=start)` 启动命令会话；"
                "脚手架、依赖安装、dev server 和可能交互的 CLI 需要可观察、可恢复的 session。"
            )

        runtime_context = get_runtime_context()
        workspace_preflight = preflight_command_workspace(command, cwd=cwd or None, runtime_context=runtime_context)
        if not workspace_preflight.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "kind": "workspace_boundary_block",
                    "summary": workspace_preflight.get("summary"),
                    "error": workspace_preflight.get("error"),
                    "cwd": cwd or None,
                    "resolvedCwd": workspace_preflight.get("resolvedCwd"),
                    "violations": workspace_preflight.get("violations") or [],
                    "recommendedNextAction": "在当前 Active Workspace Root 内重试，或由用户显式授予额外 workspace/root。",
                },
                ensure_ascii=False,
            )
        resolved_cwd = str(workspace_preflight.get("cwd") or "").strip() or None
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_system_command(command, runtime_context=runtime_context),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到系统命令存在风险，是否继续执行？\n\n命令：{command}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止命令执行。"

        sync_deadline_ms = 90_000
        with ToolExecutionEnvelope(tool_name="run_system_command", family="command", deadline_ms=sync_deadline_ms, retry_limit=1) as envelope:
            try:
                result = subprocess.run(
                    _shell_command_argv(command, resolved_shell_dialect),
                    shell=False,
                    capture_output=True,
                    cwd=resolved_cwd,
                    timeout=sync_deadline_ms / 1000,
                )
            except subprocess.TimeoutExpired:
                return json.dumps(
                    envelope.failure_payload(
                        summary="Synchronous command exceeded its tool deadline.",
                        failure_class="deadline_exceeded",
                        error=f"Command timed out after {sync_deadline_ms // 1000} seconds.",
                        retryable=False,
                        recommended_next_action="改用 command_session_broker(mode='start') 以可观察、可恢复的 session 运行，或缩小命令范围。",
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
        stdout, stdout_encoding = _decode_completed_process_bytes(result.stdout or b"", stream_name="stdout")
        stderr, stderr_encoding = _decode_completed_process_bytes(result.stderr or b"", stream_name="stderr")
        encoding_diagnostics = {
            "stdout": stdout_encoding,
            "stderr": stderr_encoding,
        }
        noisy_encoding_states = {
            str(stdout_encoding.get("state") or ""),
            str(stderr_encoding.get("state") or ""),
        } - {"", "empty", "clean"}

        safety_guardian.observe_post_action(
            action_family="command",
            summary=f"已执行系统命令：{command}",
            details={
                "command": command,
                "cwd": resolved_cwd,
                "shellDialect": resolved_shell_dialect,
                "workspaceBinding": workspace_preflight.get("binding"),
                "return_code": result.returncode,
                "encodingDiagnostics": encoding_diagnostics,
            },
            runtime_context=runtime_context,
        )
        if command_may_change_workspace(command):
            mark_workspace_state_stale(
                runtime_context,
                reason="command_sync",
                subject=command,
            )
        if result.returncode == 0:
            _notify_skills_inventory_command_completed(command)
        stdout_preview = _agent_preview_text(stdout, limit=5000)
        stderr_preview = _agent_preview_text(stderr, limit=5000)
        stdout_chars = len(stdout or "")
        stderr_chars = len(stderr or "")
        payload: dict[str, Any] = {
            "ok": result.returncode == 0,
            "kind": "command_result",
            "command": command,
            "summary": "命令执行成功。" if result.returncode == 0 else f"命令执行失败，退出码 {result.returncode}。",
            "cwd": resolved_cwd,
            "shellDialect": resolved_shell_dialect,
            "returnCode": result.returncode,
            "keyOutput": stdout_preview,
            "keyErrors": stderr_preview,
            "recommendedNextAction": "none" if result.returncode == 0 else "根据 stderr/stdout 摘要修复问题后重跑；若输出不足，请缩小命令范围或使用更具体的检查命令。",
        }
        if stdout_preview and "...[omitted " in stdout_preview:
            payload["stdoutTruncated"] = True
            payload["stdoutChars"] = stdout_chars
        if stderr_preview and "...[omitted " in stderr_preview:
            payload["stderrTruncated"] = True
            payload["stderrChars"] = stderr_chars
        if noisy_encoding_states:
            payload["encodingDiagnostics"] = {
                "state": sorted(noisy_encoding_states),
                "stdoutEncoding": stdout_encoding.get("encoding"),
                "stderrEncoding": stderr_encoding.get("encoding"),
            }
        return json.dumps(
            {key: val for key, val in payload.items() if val not in (None, "", [], {})},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error executing command: {str(e)}"


def _launch_background_command(
    command: str,
    *,
    tool_call_id: str = "",
    profile: str = "auto",
    cwd: str = "",
    shell_dialect: str = "auto",
) -> dict[str, Any]:
    resolved_shell_dialect = _resolve_shell_dialect(command, shell_dialect)
    shell_violation = _windows_shell_syntax_violation_payload(command, shell_dialect=resolved_shell_dialect)
    if shell_violation:
        raise RuntimeError(json.dumps(shell_violation, ensure_ascii=False))
    interactive_reason = _detect_interactive_command(command)
    session_reason = _detect_session_preferred_command(command)
    resolved_profile, profile_reason = _detect_background_command_profile(command, requested_profile=profile)
    # "Needs a session" is not the same as "is an interactive REPL".
    # Installs/build checks should be observable and recoverable, but they still need
    # an exit sentinel so observe can return the final result instead of leaving a
    # persistent shell marked as running forever.
    interactive_mode = interactive_reason is not None
    observable_session = session_reason is not None
    if sys.platform == "win32" and interactive_mode and not HAS_WINPTY:
        raise RuntimeError(
            "当前 Windows 环境缺少 `winpty/PTY` 适配层，无法稳定自动化交互式 CLI。"
        )

    runtime_context = get_runtime_context()
    workspace_preflight = preflight_command_workspace(command, cwd=cwd or None, runtime_context=runtime_context)
    if not workspace_preflight.get("ok"):
        raise RuntimeError(
            json.dumps(
                {
                    "ok": False,
                    "kind": "workspace_boundary_block",
                    "summary": workspace_preflight.get("summary"),
                    "error": workspace_preflight.get("error"),
                    "cwd": cwd or None,
                    "resolvedCwd": workspace_preflight.get("resolvedCwd"),
                    "violations": workspace_preflight.get("violations") or [],
                    "workspaceBinding": workspace_preflight.get("binding"),
                    "recommendedNextAction": "在当前 Active Workspace Root 内重试，或由用户显式授予额外 workspace/root。",
                },
                ensure_ascii=False,
            )
        )
    inventory_status = _workspace_inventory_status(runtime_context)
    if (
        not inventory_status.get("hasInventoryToken")
        and inventory_status.get("nonEmpty")
        and _workspace_inventory_gate_required(command, workspace_root=str(inventory_status.get("workspaceRoot") or ""))
    ):
        raise RuntimeError(json.dumps(_workspace_inventory_block_payload(runtime_context, operation="command", subject=command), ensure_ascii=False))
    suggested_command = _suggest_npx_yes_command(command)
    if suggested_command:
        raise RuntimeError(
            json.dumps(
                {
                    "ok": False,
                    "kind": "scaffold_requires_noninteractive_confirmation",
                    "summary": "脚手架命令缺少 --yes，可能卡在 Ok to proceed? (y)。请使用 suggestedCommand 重试。",
                    "command": command,
                    "suggestedCommand": suggested_command,
                    "recommendedNextAction": "用 suggestedCommand 重新启动 command_session_broker(mode=\"start\")。",
                    "workspaceBinding": inventory_status.get("binding"),
                },
                ensure_ascii=False,
            )
        )
    resolved_cwd = str(workspace_preflight.get("cwd") or "").strip() or None
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
        profile_reason=profile_reason or interactive_reason or session_reason,
        cwd=resolved_cwd,
        shell_dialect=resolved_shell_dialect,
    )
    bg_proc.command_id = cmd_id
    bg_proc.workspace_binding = workspace_preflight.get("binding")
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
            "observableSession": observable_session,
            "tty": tty_label,
            "run_id": runtime_context.get("run_id"),
            "cwd": resolved_cwd,
            "workspaceBinding": workspace_preflight.get("binding"),
            "profile": resolved_profile,
            "profile_reason": profile_reason,
            "shellDialect": resolved_shell_dialect,
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
        "cwd": resolved_cwd,
        "workspaceBinding": workspace_preflight.get("binding"),
        "status": status,
        "interactive": interactive_mode,
        "observableSession": observable_session,
        "sessionReason": session_reason,
        "interactiveReason": interactive_reason,
        "profile": resolved_profile,
        "profileReason": profile_reason or interactive_reason or session_reason,
        "shellDialect": resolved_shell_dialect,
        "chatCliVariant": bg_proc.chat_cli_variant if resolved_profile == "chat_cli" else "",
        "initialOutput": initial_out,
    }

# ==========================================


_BACKGROUND_PROCESS_RETENTION_SECONDS = 300
_SKILLS_ADD_COMMAND_PATTERN = re.compile(r"(?i)(?:^|[;&|]\s*)npx\s+skills\s+add\b")
_PROMPT_HINT_PATTERN = re.compile(
    r"((^|\n)\s*(?:[>$#»❯]\s*|输入您的消息|Type your message|Press \? for shortcuts|按 \? 查看快捷键)|Ok to proceed\?\s*\(y\)|Proceed\?\s*(?:\([YyNn]/?[Nn]?\)|\[Y/n\]|\(y\))?|\[Y/n\]|Press Enter(?:\s+to\s+\w+)?)",
    re.IGNORECASE,
)
_TERMINAL_MENU_HINT_PATTERN = re.compile(
    r"(Current directory is not empty|Please choose how to proceed|Use arrow-keys|Use arrow keys|Select an option|Choose an option|^\s*[│|]\s*[●○]\s+.+$|^\s*[◆?]\s+.+$)",
    re.IGNORECASE | re.MULTILINE,
)
_BUSY_HINT_PATTERN = re.compile(
    r"(thinking|generating|loading|connecting|initializing|authorizing|处理中|思考中|生成中|连接中|esc to cancel|pressing 'a' to continue|i'm feeling lucky|channeling the force|magic smoke|\.\.\.|⋯|█)",
    re.IGNORECASE,
)
_COMMAND_EXIT_SENTINEL_PATTERN = re.compile(r"__V8_COMMAND_EXIT_[0-9a-fA-F]{12}__:-?\d+")
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_RAW_FRAME_HISTORY_LIMIT = 12
_RAW_FRAME_PREVIEW_LIMIT = 240
_TERMINAL_TEXT_SNIPPET_LIMIT = 4000
_SPACED_CJK_SEQUENCE_PATTERN = re.compile(r"(?:[\u3400-\u9fff]\s){4,}[\u3400-\u9fff]")
_REPEATED_CJK_PATTERN = re.compile(r"([\u3400-\u9fff])\1{7,}")
_BOX_DRAWING_ONLY_LINE_PATTERN = re.compile(r"^[\s┌┐└┘├┤┬┴┼─│╭╮╰╯═║╔╗╚╝╠╣╦╩╬]+$")
_BACKGROUND_COMMAND_PROFILES = {"auto", "chat_cli", "shell"}
_TERMINAL_KEY_ALIASES = {
    "up": "\x1b[A",
    "arrowup": "\x1b[A",
    "↑": "\x1b[A",
    "down": "\x1b[B",
    "arrowdown": "\x1b[B",
    "↓": "\x1b[B",
    "left": "\x1b[D",
    "arrowleft": "\x1b[D",
    "←": "\x1b[D",
    "right": "\x1b[C",
    "arrowright": "\x1b[C",
    "→": "\x1b[C",
    "enter": "\r",
    "return": "\r",
    "回车": "\r",
    "confirm": "\r",
    "tab": "\t",
    "escape": "\x1b",
    "esc": "\x1b",
}


def _strip_command_internal_markers(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    value = _COMMAND_EXIT_SENTINEL_PATTERN.sub("", value)
    lines = []
    for line in value.splitlines():
        if _COMMAND_EXIT_SENTINEL_PATTERN.search(line):
            continue
        cleaned = line.rstrip()
        if cleaned:
            lines.append(cleaned)
        elif lines and lines[-1] != "":
            lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()
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
_V8_WORKER_RESULT_START_MARKER = "<V8_WORKER_RESULT>"
_V8_WORKER_RESULT_END_MARKER = "</V8_WORKER_RESULT>"
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
        re.compile(r"^\s*(?:tool|using|running|reading|writing|editing|calling)\b.{0,180}(?:…|\.\.\.)?\s*$", re.IGNORECASE),
        re.compile(r"^\s*[✻✶✷✸⏺●◦○]\s+.*(?:esc|interrupt|thinking|running|reading|writing|tool)\b.*$", re.IGNORECASE),
        re.compile(r"^\s*⎿\s+.*$", re.IGNORECASE),
        re.compile(r"^\s*(?:tokens?|cost|session|context)\s*[:：].*$", re.IGNORECASE),
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
    stripped = _strip_leading_shell_cwd(command)
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


def _contains_v8_worker_result_marker(text: str) -> bool:
    value = str(text or "")
    return _V8_WORKER_RESULT_START_MARKER in value or _V8_WORKER_RESULT_END_MARKER in value


def _extract_v8_worker_result_block_text(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    for candidate in (re.sub(r"[\r\n]+", "", value), value):
        end = candidate.rfind(_V8_WORKER_RESULT_END_MARKER)
        start = candidate.rfind(_V8_WORKER_RESULT_START_MARKER, 0, end) if end >= 0 else -1
        if start < 0 or end < 0:
            continue
        end += len(_V8_WORKER_RESULT_END_MARKER)
        return candidate[start:end].strip()
    return ""


def _count_chat_cli_noise_lines(text: str, *, variant: str = "") -> int:
    count = 0
    for line in _normalize_chat_cli_text(text).splitlines():
        stripped = line.strip()
        if stripped and not _contains_v8_worker_result_marker(stripped) and _looks_like_chat_cli_noise_line(stripped, variant=variant):
            count += 1
    return count


def _looks_like_chat_cli_noise_line(line: str, *, variant: str = "") -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if _contains_v8_worker_result_marker(stripped):
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
        if _contains_v8_worker_result_marker(stripped):
            filtered.append(line.rstrip())
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


def _build_command_diagnostics_snapshot(command: str, *, cwd: str | None = None) -> dict[str, Any]:
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
        "currentWorkingDirectory": str(cwd or os.getcwd()),
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


def _build_winpty_bootstrap_commands(env_overrides: dict[str, str], *, shell_dialect: str = "cmd") -> list[str]:
    dialect = _normalize_shell_dialect(shell_dialect)
    if dialect in {"powershell", "pwsh"}:
        commands = ["[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()"]
        for key, value in env_overrides.items():
            escaped = str(value).replace("'", "''")
            commands.append(f"$env:{key} = '{escaped}'")
        return commands
    commands = ["@echo off", "chcp 65001 >NUL"]
    for key, value in env_overrides.items():
        commands.append(f"set {key}={value}")
    return commands


def _extend_command_diagnostics_for_terminal(
    diagnostics: dict[str, Any],
    *,
    env_overrides: dict[str, str],
    uses_winpty: bool,
    shell_dialect: str = "cmd",
) -> dict[str, Any]:
    next_diagnostics = dict(diagnostics or {})
    next_diagnostics["targetTextEncoding"] = "utf-8"
    next_diagnostics["terminalEnvOverrides"] = dict(env_overrides)
    next_diagnostics["shellDialect"] = shell_dialect
    if uses_winpty:
        next_diagnostics["ptyShell"] = shell_dialect
        next_diagnostics["winptyBootstrapCodePage"] = "65001"
        next_diagnostics["winptyBootstrapCommands"] = _build_winpty_bootstrap_commands(
            env_overrides,
            shell_dialect=shell_dialect,
        )
    return next_diagnostics


def _run_winpty_bootstrap(pty_win: Any, env_overrides: dict[str, str], *, shell_dialect: str = "cmd") -> None:
    for command in _build_winpty_bootstrap_commands(env_overrides, shell_dialect=shell_dialect):
        _write_winpty_input(pty_win, f"{command}\n")
        time.sleep(0.05)


def _strip_terminal_bootstrap_noise(
    text: str,
    env_overrides: dict[str, str] | None = None,
    *,
    shell_dialect: str = "auto",
) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""
    normalized = _AGENT_ANSI_ESCAPE_PATTERN.sub("", normalized)
    removable_lines = {"@echo off", "chcp 65001 >nul"}
    for key, value in (env_overrides or {}).items():
        powershell_value = str(value).replace("'", "''")
        removable_lines.add(f"set {key}={value}".lower())
        removable_lines.add(f"export {key}={shlex.quote(str(value))}".lower())
        removable_lines.add(f"$env:{key} = '{powershell_value}'".lower())
    removable_lines.add("[console]::outputencoding = [system.text.utf8encoding]::new()")
    filtered_lines: list[str] = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered in removable_lines:
            continue
        if stripped.startswith("0;"):
            continue
        if re.match(r"^[a-z]:\\.*>", stripped, flags=re.IGNORECASE):
            continue
        if re.match(r"^ps\s+[a-z]:\\.*>", stripped, flags=re.IGNORECASE):
            continue
        if lowered.startswith("microsoft windows [") or lowered.startswith("(c) microsoft corporation"):
            continue
        if "cmd.exe" in lowered and any(marker in lowered for marker in removable_lines):
            continue
        if lowered.startswith("cd /d "):
            continue
        if lowered.startswith("set-location -literalpath ") or lowered.startswith("cd "):
            continue
        if "__v8_command_exit_" in lowered:
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


def _terminal_snapshot_looks_like_menu(snapshot: str) -> bool:
    normalized = str(snapshot or "").strip()
    if not normalized:
        return False
    tail = "\n".join(normalized.splitlines()[-10:])
    return bool(_TERMINAL_MENU_HINT_PATTERN.search(tail))


def _terminal_menu_suggested_keys(snapshot: str) -> list[str]:
    text = str(snapshot or "")
    lowered = text.lower()
    if "current directory is not empty" in lowered and "ignore files and continue" in lowered:
        return ["down", "down", "enter"]
    if "remove existing files and continue" in lowered:
        return ["down", "enter"]
    if _terminal_snapshot_looks_like_menu(text):
        return ["enter"]
    return []


def _terminal_snapshot_looks_busy(snapshot: str) -> bool:
    normalized = str(snapshot or "").strip()
    if not normalized:
        return False
    tail = "\n".join(normalized.splitlines()[-6:])
    return bool(_BUSY_HINT_PATTERN.search(tail))


def _windows_text_encoding_candidates() -> list[str]:
    candidates = [
        "utf-8",
        locale.getpreferredencoding(False),
        "mbcs",
        "cp936",
        "gbk",
    ]
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        item = str(candidate or "").strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            normalized.append(item)
    return normalized


def _decode_completed_process_bytes(data: bytes, *, stream_name: str) -> tuple[str, dict[str, Any]]:
    raw = bytes(data or b"")
    if not raw:
        return "", {"stream": stream_name, "encoding": "utf-8", "state": "empty"}
    if sys.platform != "win32":
        try:
            return raw.decode("utf-8"), {"stream": stream_name, "encoding": "utf-8", "state": "clean"}
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            return text, {"stream": stream_name, "encoding": "utf-8", "state": "undecodable"}

    attempted: list[str] = []
    utf8_failed = False
    for encoding in _windows_text_encoding_candidates():
        attempted.append(encoding)
        try:
            text = raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            if encoding.lower() == "utf-8":
                utf8_failed = True
            continue
        state = "mojibake_recovered" if utf8_failed and encoding.lower() != "utf-8" else "clean"
        if _looks_like_terminal_mojibake(text):
            state = "mojibake_suspected"
        return text, {
            "stream": stream_name,
            "encoding": encoding,
            "state": state,
            "attempted": attempted,
        }

    fallback_encoding = locale.getpreferredencoding(False) or "utf-8"
    text = raw.decode(fallback_encoding, errors="replace")
    state = "undecodable"
    if _looks_like_terminal_mojibake(text):
        state = "mojibake_suspected"
    return text, {
        "stream": stream_name,
        "encoding": fallback_encoding,
        "state": state,
        "attempted": attempted,
    }


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

    def resize(self, cols: int, rows: int) -> None:
        next_cols = max(int(cols or self.cols or 80), 20)
        next_rows = max(int(rows or self.rows or 24), 4)
        if next_cols == self.cols and next_rows == self.rows:
            return

        def _resize_buffer(buffer: list[list[str]]) -> list[list[str]]:
            source = list(buffer or [])
            if len(source) < next_rows:
                source = source + [[" "] * self.cols for _ in range(next_rows - len(source))]
            else:
                source = source[-next_rows:]
            resized: list[list[str]] = []
            for row in source:
                next_row = list(row[:next_cols])
                if len(next_row) < next_cols:
                    next_row.extend([" "] * (next_cols - len(next_row)))
                resized.append(next_row)
            return resized

        self._primary = _resize_buffer(self._primary)
        self._alternate = _resize_buffer(self._alternate)
        self.cols = next_cols
        self.rows = next_rows
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.cursor_row = max(0, min(self.rows - 1, self.cursor_row))
        self.cursor_col = max(0, min(self.cols - 1, self.cursor_col))
        self._saved_primary_cursor = (
            max(0, min(self.rows - 1, self._saved_primary_cursor[0])),
            max(0, min(self.cols - 1, self._saved_primary_cursor[1])),
        )
        self._saved_alternate_cursor = (
            max(0, min(self.rows - 1, self._saved_alternate_cursor[0])),
            max(0, min(self.cols - 1, self._saved_alternate_cursor[1])),
        )

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
        cwd: str | None = None,
        shell_dialect: str = "auto",
    ):
        self.command = command
        self.cwd = str(cwd or os.getcwd())
        self.session_id = session_id
        self.run_id = run_id
        self.interactive = interactive
        self.profile = profile if profile in {"shell", "chat_cli"} else "shell"
        self.profile_reason = str(profile_reason or "")
        self.shell_dialect = _resolve_shell_dialect(command, shell_dialect)
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
        self.worker_result_raw_buffer = ""
        self.pending_input_echo = ""
        self.terminal_env_overrides = _build_terminal_env_overrides()
        self.exit_sentinel = (
            f"__V8_COMMAND_EXIT_{uuid.uuid4().hex[:12]}__"
            if sys.platform == "win32" and HAS_WINPTY and not interactive
            else ""
        )
        self.command_completed_by_sentinel = False
        self.command_diagnostics = _extend_command_diagnostics_for_terminal(
            _build_command_diagnostics_snapshot(command, cwd=self.cwd),
            env_overrides=self.terminal_env_overrides,
            uses_winpty=bool(sys.platform == "win32" and HAS_WINPTY),
            shell_dialect=self.shell_dialect,
        )
        
        if sys.platform == "win32" and HAS_WINPTY:
            self.pty_win = PTY(self.cols, self.rows)
            self.uses_tty = True
            if self.shell_dialect == "cmd":
                shell_command = f'"{os.environ.get("COMSPEC") or "cmd.exe"}" /q /d'
            elif self.shell_dialect == "pwsh":
                shell_command = f'"{shutil.which("pwsh") or "pwsh"}" -NoLogo -NoProfile'
            elif self.shell_dialect == "powershell":
                shell_command = f'"{shutil.which("powershell.exe") or "powershell.exe"}" -NoLogo -NoProfile -ExecutionPolicy Bypass'
            else:
                shell_command = f'"{shutil.which("bash") or "bash"}" --noprofile --norc'
            self.pty_win.spawn(shell_command)
            time.sleep(0.5)
            _run_winpty_bootstrap(
                self.pty_win,
                self.terminal_env_overrides,
                shell_dialect=self.shell_dialect,
            )
            if self.shell_dialect == "cmd":
                cwd_command = f'cd /d "{self.cwd}"'
            elif self.shell_dialect in {"powershell", "pwsh"}:
                escaped_cwd = self.cwd.replace("'", "''")
                cwd_command = f"Set-Location -LiteralPath '{escaped_cwd}'"
            else:
                posix_cwd = self.cwd.replace("\\", "/")
                cwd_command = f"cd {shlex.quote(posix_cwd)}"
            _write_winpty_input(self.pty_win, f"{cwd_command}\n")
            time.sleep(0.2)
            _write_winpty_input(self.pty_win, f"{command}\n")
            if self.exit_sentinel:
                if self.shell_dialect == "cmd":
                    sentinel_command = f"echo {self.exit_sentinel}:%ERRORLEVEL%"
                elif self.shell_dialect in {"powershell", "pwsh"}:
                    sentinel_command = f'Write-Output "{self.exit_sentinel}:$LASTEXITCODE"'
                else:
                    sentinel_command = f'printf "{self.exit_sentinel}:%s\\n" "$?"'
                _write_winpty_input(self.pty_win, f"{sentinel_command}\n")
        elif sys.platform != "win32":
            pid, self.fd = pty.fork()
            if pid == 0:
                child_env = dict(os.environ)
                child_env.update(self.terminal_env_overrides)
                os.chdir(self.cwd)
                os.execvpe("sh", ["sh", "-c", command], child_env)
            else:
                self.proc = pid
                self.uses_tty = True
        else:
            # Fallback for Windows without pywinpty
            child_env = dict(os.environ)
            child_env.update(self.terminal_env_overrides)
            self.proc = subprocess.Popen(
                _shell_command_argv(command, self.shell_dialect), shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, env=child_env, cwd=self.cwd
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

    def _maybe_mark_command_exit_sentinel(self, *, now: float | None = None) -> None:
        sentinel = str(getattr(self, "exit_sentinel", "") or "")
        if not sentinel or self.command_completed_by_sentinel:
            return
        match = re.search(rf"{re.escape(sentinel)}:(-?\d+)", str(self.worker_result_raw_buffer or ""))
        if not match:
            return
        try:
            self.return_code = int(match.group(1))
        except Exception:
            self.return_code = 1
        self.command_completed_by_sentinel = True
        self.is_running = False
        self.completed_at = now or time.time()

    def _ingest_output(self, data: str) -> None:
        if not data:
            return
        now = time.time()
        self.last_output_at = now
        self.output_history.append(data)
        self.worker_result_raw_buffer = (self.worker_result_raw_buffer + data)[-50000:]
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
        self._maybe_mark_command_exit_sentinel(now=now)
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
            read_return_code = self._read_return_code()
            if self.return_code is None:
                self.return_code = read_return_code
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

    def resize_terminal(self, cols: int, rows: int) -> dict[str, int]:
        next_cols = max(20, min(int(cols or self.cols or 80), 240))
        next_rows = max(4, min(int(rows or self.rows or 24), 120))
        if next_cols == self.cols and next_rows == self.rows:
            return {"cols": self.cols, "rows": self.rows}

        self.cols = next_cols
        self.rows = next_rows
        try:
            self.screen.resize(next_cols, next_rows)
            self.screen_snapshot_cache = self._compute_screen_snapshot()
            self.screen_version += 1
            self.last_screen_at = time.time()
        except Exception:
            pass

        if sys.platform == "win32" and HAS_WINPTY and self.pty_win is not None:
            for method_name in ("set_size", "resize"):
                method = getattr(self.pty_win, method_name, None)
                if not callable(method):
                    continue
                try:
                    method(next_cols, next_rows)
                    break
                except TypeError:
                    try:
                        method(next_rows, next_cols)
                        break
                    except Exception:
                        continue
                except Exception:
                    continue
        elif sys.platform != "win32" and self.fd is not None:
            try:
                import fcntl
                import struct
                import termios

                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", next_rows, next_cols, 0, 0))
            except Exception:
                pass
        return {"cols": self.cols, "rows": self.rows}

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
            return _strip_command_internal_markers(
                _strip_terminal_bootstrap_noise(
                    self.screen_snapshot_cache,
                    self.terminal_env_overrides,
                    shell_dialect=self.shell_dialect,
                )
            )
        return _strip_command_internal_markers(
            _strip_terminal_bootstrap_noise(
                self._compute_screen_snapshot(),
                self.terminal_env_overrides,
                shell_dialect=self.shell_dialect,
            )
        )

    def _derive_observation_state(self) -> str:
        if not self.is_running:
            return "idle"
        snapshot = self._render_screen_snapshot()
        now = time.time()
        since_raw = now - self.last_raw_frame_at if self.last_raw_frame_at else float("inf")
        since_screen = now - self.last_screen_at if self.last_screen_at else float("inf")
        raw_recent = since_raw <= 0.9
        screen_recent = since_screen <= 0.9

        if (
            _terminal_snapshot_looks_like_prompt(snapshot)
            or _terminal_snapshot_looks_like_menu(snapshot)
        ) and not _terminal_snapshot_looks_busy(snapshot):
            return "awaiting_input"
        if not self.interactive:
            return "idle"
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
        suggested_keys = _terminal_menu_suggested_keys(stable_screen_snapshot or screen_snapshot)
        encoding_state, encoding_notes, text_encoding = _derive_terminal_encoding_status(
            screen_snapshot=screen_snapshot,
            raw_preview=self.last_raw_frame_preview,
            diagnostics=self.command_diagnostics,
        )
        return {
            "command_id": getattr(self, "command_id", None),
            "command": self.command,
            "cwd": self.cwd,
            "workspace_binding": dict(getattr(self, "workspace_binding", {}) or {}),
            "session_id": self.session_id,
            "is_running": self.is_running,
            "uses_tty": self.uses_tty,
            "interactive": self.interactive,
            "command_completed_by_sentinel": bool(self.command_completed_by_sentinel),
            "profile": self.profile,
            "profile_reason": self.profile_reason,
            "shell_dialect": self.shell_dialect,
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
            "terminal_menu": {
                "detected": bool(suggested_keys or _terminal_snapshot_looks_like_menu(stable_screen_snapshot or screen_snapshot)),
                "suggested_keys": suggested_keys,
            },
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
            "cwd": status.get("cwd"),
            "workspaceBinding": status.get("workspace_binding"),
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
    cwd: str = "",
    shell_dialect: str = "auto",
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
        launch_background_command = _compat_native_attr("_launch_background_command", _launch_background_command)
        launched = launch_background_command(
            command,
            tool_call_id=tool_call_id,
            profile=profile,
            cwd=cwd,
            shell_dialect=shell_dialect,
        )
        if command_may_change_workspace(command):
            mark_workspace_state_stale(
                get_runtime_context(),
                reason="background_command_started",
                subject=command,
            )
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
            f"Shell dialect: {launched.get('shellDialect') or 'n/a'}\n"
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
    cwd: str = "",
    shell_dialect: str = "auto",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Run shell work with V8OS choosing the safest command path.

    Use this for shell work the task actually needs: checking the environment, running tests, launching scripts,
    installing dependencies, starting dev servers, or executing a user-requested command. Keep mode=auto by
    default. V8OS will run quick commands directly, and will open an observable terminal session for installers,
    scaffolding, dev servers, TUI prompts, password/confirmation flows, and long-running/watch commands.

    Do not use this just to read or write a known text/JSON/Markdown/source file. Use `read_native_file` and
    `write_native_file` for file content. If the same command purpose fails twice, stop changing shell wrappers;
    switch to the right tool or return the blocker/degraded reason.

    mode=auto:
    - 短命令/非交互命令直接同步执行并返回结果
    - 交互式、脚手架、依赖安装或长驻命令自动启动可观察 command session 并返回 sessionId/commandId
    - 后续需要输入、观察或终止时再使用 command_session_broker

    mode=sync:
    - 强制同步执行，适合短命令
    - 不允许用于脚手架、依赖安装、dev server 或可能交互的命令

    mode=session:
    - 兼容模式：强制后台/交互模式

    profile:
    - auto: 自动识别 shell / chat_cli
    - chat_cli: 把 AI CLI 作为对话终端处理，只向 supervisor 暴露最新语义增量
    - shell: 普通终端模式

    shell_dialect:
    - Windows 推荐显式选择 powershell、pwsh 或 cmd；auto 仅用于兼容并会返回实际选择。
    - 不要在同一命令中混用 cmd 与 PowerShell 语法。
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
            effective_mode = "session"
        else:
            effective_mode = "sync"

    if effective_mode == "sync":
        if prefer_session:
            return json.dumps(
                {
                    "ok": False,
                    "mode": "sync",
                    "kind": "command_session_required",
                    "command": command,
                    "summary": "该命令不能通过阻塞式 sync 执行。",
                    "reason": interactive_reason or session_reason or "命令需要可观察、可恢复的后台会话",
                    "recommendedNextAction": "改用 command_session_broker(mode=start)，然后 observe/input/terminate。",
                    "redirect": {
                        "tool": "command_session_broker",
                        "args": {
                            "mode": "start",
                            "command": command,
                            "profile": normalized_profile,
                            "cwd": cwd,
                            "shell_dialect": shell_dialect,
                        },
                    },
                },
                ensure_ascii=False,
            )
        return execute_system_command.func(
            command=command,
            cwd=cwd,
            shell_dialect=shell_dialect,
            tool_call_id=tool_call_id,
        )

    if effective_mode == "session":
        try:
            launch_background_command = _compat_native_attr("_launch_background_command", _launch_background_command)
            launched = launch_background_command(
                command,
                tool_call_id=tool_call_id,
                profile=normalized_profile,
                cwd=cwd,
                shell_dialect=shell_dialect,
            )
            if command_may_change_workspace(command):
                mark_workspace_state_stale(
                    get_runtime_context(),
                    reason="command_session_started",
                    subject=command,
                )
            status = dict(launched.get("status") or {})
            state = _command_session_state_from_status(status)
            payload = {
                "ok": True,
                "kind": "command_session",
                "mode": "session",
                "command": command,
                "shellDialect": launched.get("shellDialect"),
                "commandId": launched["commandId"],
                "sessionId": launched["commandId"],
                "interactive": bool(launched["interactive"]),
                "observableSession": bool(launched.get("observableSession")),
                "profile": launched["profile"],
                "cwd": launched.get("cwd"),
                "runId": launched["runId"],
                "reason": interactive_reason or session_reason or "显式 session 模式",
                "summary": _command_session_summary_for_state(
                    mode="start",
                    state=state,
                    interactive=bool(launched["interactive"]),
                    delta_text=str(launched.get("initialOutput") or "").strip(),
                ),
                "recommendedNextAction": _command_session_recommended_next_action(
                    mode="start",
                    state=state,
                    awaiting_input=bool(status.get("awaiting_input")),
                    has_more=bool(state not in {"completed", "failed"}),
                ),
                "state": state,
                "awaitingInput": bool(status.get("awaiting_input")),
                "returnCode": status.get("return_code"),
            }
            if launched["initialOutput"]:
                initial_preview, initial_truncated = _command_session_preview_text(str(launched["initialOutput"] or ""))
                if initial_preview:
                    payload["finalPreview" if state in {"completed", "failed"} else "initialPreview"] = initial_preview
                    payload["finalPreviewTruncated" if state in {"completed", "failed"} else "initialPreviewTruncated"] = initial_truncated
            return json.dumps(payload, ensure_ascii=False, indent=2)
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
        if observation_state == "idle" and float(status.get("seconds_since_output") or 0) >= 90:
            return "recoverable_stalled"
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
    if state == "recoverable_stalled":
        return "命令会话长时间无新增输出，处于可恢复停滞状态。"
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
    if state == "recoverable_stalled":
        return "observe_or_terminate"
    if has_more or state == "render_stalled":
        return "observe"
    if state == "running":
        return "wait_then_observe"
    return "wait_then_observe"


def _command_session_preview_text(value: str, *, limit: int = 1200) -> tuple[str, bool]:
    normalized = _agent_preview_text(
        _strip_command_internal_markers(
            _strip_terminal_bootstrap_noise(str(value or ""), _build_terminal_env_overrides())
        ),
        limit=limit,
    ) or ""
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit].rstrip(), True


def _strip_command_echo_noise(value: str, *, command: str = "") -> str:
    text = str(value or "").strip()
    rendered_command = str(command or "").strip()
    if not text or not rendered_command:
        return text
    command_compact = re.sub(r"\s+", "", rendered_command)
    lines = text.splitlines()
    index = 0
    consumed = ""
    while index < len(lines):
        candidate = lines[index].strip()
        candidate_compact = re.sub(r"\s+", "", candidate)
        if not candidate_compact:
            index += 1
            continue
        next_consumed = consumed + candidate_compact
        if command_compact.startswith(next_consumed):
            consumed = next_consumed
            index += 1
            continue
        if candidate_compact == command_compact or candidate.endswith(rendered_command):
            consumed = command_compact
            index += 1
            continue
        break
    return "\n".join(lines[index:]).strip()


_TERMINAL_SPINNER_CHARS = set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏|/-\\")


def _looks_like_terminal_spinner_noise(value: str) -> bool:
    compact = re.sub(r"[\s\r\n\t\b]+", "", str(value or ""))
    if not compact:
        return False
    if len(compact) > 240:
        return False
    return all(char in _TERMINAL_SPINNER_CHARS for char in compact)


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


def _command_session_result_preview_fields(
    *,
    state: str,
    interactive: bool,
    command: str = "",
    delta_text: str = "",
    screen_preview: str = "",
    raw_frame_preview: str = "",
    raw_buffer: str = "",
    limit: int = 1200,
) -> dict[str, Any]:
    """Build the compact result surface an agent needs when a command has no fresh delta."""
    if state not in {"completed", "failed", "recoverable_stalled", "render_stalled"} and not delta_text:
        return {}
    if interactive and state not in {"completed", "failed"}:
        return {}
    source_candidates = (
        (
            str(raw_buffer or "").strip(),
            str(delta_text or "").strip(),
            str(screen_preview or "").strip(),
            str(raw_frame_preview or "").strip(),
        )
        if state in {"completed", "failed"}
        else (
            str(delta_text or "").strip(),
            str(raw_buffer or "").strip(),
            str(screen_preview or "").strip(),
            str(raw_frame_preview or "").strip(),
        )
    )
    source = ""
    for part in source_candidates:
        if part:
            source = part
            break
    preview, truncated = _command_session_preview_text(source, limit=limit)
    preview = _strip_command_echo_noise(preview, command=command)
    if not preview:
        return {}
    field_name = "finalPreview" if state in {"completed", "failed"} else "outputPreview"
    return {
        field_name: preview,
        f"{field_name}Truncated": truncated,
    }


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
        indent=2,
    )




@tool
def command_session_broker(
    mode: str = "observe",
    command: str = "",
    session_id: str = "",
    command_id: str = "",
    input_text: str = "",
    keys: list[str] | None = None,
    submit: bool = True,
    profile: str = "auto",
    cwd: str = "",
    shell_dialect: str = "auto",
    debug: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage an observable terminal session after one is needed.

    Use this for long-running or interactive terminal work: dev servers, dependency installers, TUI menus,
    password prompts, AI CLIs, or commands already returned with a sessionId/commandId. For ordinary one-shot
    checks, use `run_system_command(mode=auto)` instead; it will start a terminal session only when necessary.

    Modes:
    - start: launch a long-running or interactive command session
    - observe: read the latest delta and detect whether input is needed
    - input: send input into the active session; submit=true appends Enter when input_text has no newline
      or when keys do not already include Enter.
    - terminate: stop the session

    Usage guidance:
    - Prefer run_system_command(mode=auto) as the first shell entry; it starts this broker internally when needed.
    - Use this broker directly only after you already have a sessionId/commandId, or when you need explicit observe/input/terminate control.
    - Use observe until the session completes, reports the next required input, or returns stdout/stderr/final output.
    - Treat stdout/stderr/exit code as the command truth. Broker status fields only describe waiting, timeout, backgrounding, or recovery.
    - profile=auto may enable chat_cli semantics for known AI CLIs so observe reports the latest semantic delta instead of replaying the whole screen.
    - If awaitingInput=true, send follow-up text with mode=input; if hasMore=true, observe again after a short wait.
    - For TUI menus, prefer keys=["down","down","enter"]. Common shorthand like input_text="↓↓" maps to arrow keys and appends Enter by default.
    - Use debug=true only for raw terminal diagnostics such as screenPreview, rawFramePreview, render_stalled, or encodingState/mojibake.
    - On Windows choose shell_dialect explicitly for shell-specific commands; never mix cmd and PowerShell syntax in one session.
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
            launch_background_command = _compat_native_attr("_launch_background_command", _launch_background_command)
            launched = launch_background_command(
                normalized_command,
                tool_call_id=tool_call_id,
                profile=normalized_profile,
                cwd=cwd,
                shell_dialect=shell_dialect,
            )
            if command_may_change_workspace(normalized_command):
                mark_workspace_state_stale(
                    get_runtime_context(),
                    reason="command_session_started",
                    subject=normalized_command,
                )
            status = dict(launched.get("status") or {})
            terminal_menu = status.get("terminal_menu") if isinstance(status.get("terminal_menu"), dict) and status.get("terminal_menu", {}).get("detected") else {}
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
                    has_more=bool(state not in {"completed", "failed"}),
                ),
                interactive=bool(launched.get("interactive")),
                observableSession=bool(launched.get("observableSession")),
                profile=launched.get("profile"),
                shellDialect=launched.get("shellDialect"),
                reason=launched.get("interactiveReason") or launched.get("sessionReason") or launched.get("profileReason") or launched.get("reason") or _detect_interactive_command(normalized_command) or _detect_session_preferred_command(normalized_command),
                command=normalized_command,
                cwd=launched.get("cwd"),
                awaitingInput=bool(status.get("awaiting_input")),
                terminalMenu=terminal_menu or None,
                state=state,
                initialPreview=initial_preview or None,
                initialPreviewTruncated=initial_truncated if initial_preview else None,
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
                command="",
                error="session_not_found",
            )

        if normalized_mode == "observe":
            new_output = bg_proc.get_new_output()
            status = bg_proc.status_snapshot()
            terminal_menu = status.get("terminal_menu") if isinstance(status.get("terminal_menu"), dict) and status.get("terminal_menu", {}).get("detected") else {}
            screen_changed = bool(bg_proc.has_unreported_screen_change())
            raw_changed = bool(bg_proc.has_unreported_raw_frame_change())
            delta_text = str(new_output or "").strip()
            if _looks_like_terminal_spinner_noise(delta_text):
                delta_text = ""
            has_more = False
            semantic_state: dict[str, Any] = {}
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
                has_more = bool(bg_proc.is_running and delta_text and not status.get("awaiting_input"))
            if screen_changed:
                bg_proc.mark_screen_reported()
            if raw_changed:
                bg_proc.mark_raw_frame_reported()
            state = _command_session_state_from_status(status)
            delta_preview, delta_truncated = _command_session_preview_text(delta_text)
            screen_preview = str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip()
            raw_frame_preview = str(status.get("last_raw_frame_preview") or "").strip()
            semantic_text = str(bg_proc.current_turn_text or semantic_state.get("semantic_view") or "").strip()
            semantic_tail, semantic_tail_truncated = _command_session_preview_text(semantic_text, limit=2000)
            marker_source = "\n".join(
                part
                for part in [
                    str(getattr(bg_proc, "worker_result_raw_buffer", "") or ""),
                    semantic_text,
                    delta_text,
                    screen_preview,
                    raw_frame_preview,
                ]
                if str(part or "").strip()
            )
            worker_result_block = _extract_v8_worker_result_block_text(marker_source)
            worker_result_detected = bool(worker_result_block or _contains_v8_worker_result_marker(marker_source))
            noise_filtered_count = _count_chat_cli_noise_lines(
                str(semantic_state.get("semantic_view") or screen_preview or new_output or ""),
                variant=bg_proc.chat_cli_variant,
            ) if bg_proc.profile == "chat_cli" else 0
            result_fields = _command_session_result_preview_fields(
                state=state,
                interactive=bool(status.get("interactive")),
                command=str(status.get("command") or ""),
                delta_text=delta_preview,
                screen_preview=screen_preview,
                raw_frame_preview=raw_frame_preview,
                raw_buffer=str(getattr(bg_proc, "worker_result_raw_buffer", "") or ""),
            )
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
                command=status.get("command"),
                cwd=status.get("cwd"),
                deltaText=delta_preview or None,
                deltaTruncated=delta_truncated if delta_preview else None,
                semanticTextTail=semantic_tail or None,
                semanticTextTailTruncated=semantic_tail_truncated if semantic_tail else None,
                workerResultDetected=worker_result_detected or None,
                workerResultBlock=worker_result_block or None,
                noiseFilteredLineCount=noise_filtered_count or None,
                awaitingInput=bool(status.get("awaiting_input")),
                terminalMenu=terminal_menu or None,
                hasMore=has_more,
                returnCode=status.get("return_code"),
                debug=debug_payload,
                **result_fields,
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
                    command=status.get("command"),
                    returnCode=status.get("return_code"),
                    error="session_not_running",
                )
            key_input, accepted_keys, key_submitted_enter, used_key_input = _terminal_key_sequence(
                input_text=input_text,
                keys=keys,
                submit=submit,
            )
            normalized_input = key_input if used_key_input else _decode_background_input_escapes(input_text)
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
            submitted_enter = key_submitted_enter
            if not used_key_input and submit and "\n" not in normalized_input and "\r" not in normalized_input:
                normalized_input = f"{normalized_input}\n"
                submitted_enter = True
            previous_status = bg_proc.status_snapshot()
            previous_screen_version = int(previous_status.get("screen_version") or 0)
            previous_raw_frame_version = int(previous_status.get("raw_frame_version") or 0)
            bg_proc.discard_pending_output()
            bg_proc._prepare_chat_cli_for_input(normalized_input, status_before=previous_status)
            bg_proc.write_input(normalized_input)
            time.sleep(0.5)
            new_output = bg_proc.get_new_output()
            status = bg_proc.status_snapshot()
            terminal_menu = status.get("terminal_menu") if isinstance(status.get("terminal_menu"), dict) and status.get("terminal_menu", {}).get("detected") else {}
            screen_changed = int(status.get("screen_version") or 0) > previous_screen_version
            raw_changed = int(status.get("raw_frame_version") or 0) > previous_raw_frame_version
            delta_text = str(new_output or "").strip()
            if _looks_like_terminal_spinner_noise(delta_text):
                delta_text = ""
            has_more = False
            semantic_state: dict[str, Any] = {}
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
                has_more = bool(bg_proc.is_running and delta_text and not status.get("awaiting_input"))
            if screen_changed:
                bg_proc.mark_screen_reported()
            if raw_changed:
                bg_proc.mark_raw_frame_reported()
            state = _command_session_state_from_status(status)
            delta_preview, delta_truncated = _command_session_preview_text(delta_text)
            input_label = f"keys:{','.join(accepted_keys)}" if used_key_input else normalized_input
            input_preview, input_truncated = _command_session_preview_text(input_label, limit=200)
            input_bytes_preview = _terminal_input_bytes_preview(normalized_input)
            screen_after_preview, screen_after_truncated = _command_session_preview_text(
                str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip(),
                limit=1000,
            )
            semantic_text = str(bg_proc.current_turn_text or semantic_state.get("semantic_view") or "").strip()
            semantic_tail, semantic_tail_truncated = _command_session_preview_text(semantic_text, limit=2000)
            marker_source = "\n".join(
                part
                for part in [
                    str(getattr(bg_proc, "worker_result_raw_buffer", "") or ""),
                    semantic_text,
                    delta_text,
                    str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip(),
                    str(status.get("last_raw_frame_preview") or "").strip(),
                ]
                if str(part or "").strip()
            )
            worker_result_block = _extract_v8_worker_result_block_text(marker_source)
            worker_result_detected = bool(worker_result_block or _contains_v8_worker_result_marker(marker_source))
            noise_filtered_count = _count_chat_cli_noise_lines(
                str(semantic_state.get("semantic_view") or new_output or ""),
                variant=bg_proc.chat_cli_variant,
            ) if bg_proc.profile == "chat_cli" else 0
            result_fields = _command_session_result_preview_fields(
                state=state,
                interactive=bool(status.get("interactive")),
                command=str(status.get("command") or ""),
                delta_text=delta_preview,
                screen_preview=str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip(),
                raw_frame_preview=str(status.get("last_raw_frame_preview") or "").strip(),
                raw_buffer=str(getattr(bg_proc, "worker_result_raw_buffer", "") or ""),
            )
            # For input calls the agent needs whether the input landed and the immediate delta.
            # Full screen/final previews are available via observe/rawRef and otherwise duplicate the key output.
            result_fields = {}
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
                command=status.get("command"),
                cwd=status.get("cwd"),
                acceptedInputPreview=input_preview,
                acceptedInputTruncated=input_truncated if input_preview else None,
                submittedEnter=submitted_enter,
                acceptedKeys=accepted_keys or None,
                keyOutput=delta_preview or semantic_tail or None,
                keyOutputTruncated=bool(delta_truncated or semantic_tail_truncated) if (delta_preview or semantic_tail) else None,
                workerResultDetected=worker_result_detected or None,
                noiseFilteredLineCount=noise_filtered_count or None,
                awaitingInput=bool(status.get("awaiting_input")),
                terminalMenu=terminal_menu or None,
                hasMore=has_more,
                returnCode=status.get("return_code"),
                debug=debug_payload,
                **result_fields,
            )

        status_before = bg_proc.status_snapshot()
        bg_proc.terminate()
        time.sleep(0.15)
        final_output = bg_proc.get_new_output()
        status = bg_proc.status_snapshot()
        _bg_processes.pop(resolved_session_id, None)
        screen_preview = str(status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "").strip()
        raw_frame_preview = str(status.get("last_raw_frame_preview") or "").strip()
        final_source = str(final_output or getattr(bg_proc, "worker_result_raw_buffer", "") or "").strip()
        final_preview, final_truncated = _command_session_preview_text(final_source, limit=600)
        debug_payload = None
        if debug:
            debug_payload = _command_session_debug_payload(
                status=status or status_before,
                screen_preview=screen_preview,
                raw_frame_preview=raw_frame_preview,
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
            command=status.get("command"),
            cwd=status.get("cwd"),
            returnCode=status.get("return_code"),
            keyOutput=final_preview or None if debug else None,
            keyOutputTruncated=final_truncated if debug and final_preview else None,
            debug=debug_payload,
        )
    except Exception as exc:
        _raise_runtime_governance_exception_if_needed(exc)
        normalized_session = str(command_id or session_id or "").strip()
        error_text = str(exc)
        structured_error: dict[str, Any] | None = None
        try:
            parsed_error = json.loads(error_text)
            if isinstance(parsed_error, dict):
                structured_error = parsed_error
        except Exception:
            structured_error = None
        if structured_error:
            return _command_session_payload(
                mode=normalized_mode,
                session_id=normalized_session,
                command_id=normalized_session,
                ok=False,
                summary=str(structured_error.get("summary") or structured_error.get("error") or error_text),
                recommended_next_action=str(structured_error.get("recommendedNextAction") or "none"),
                error=str(structured_error.get("error") or structured_error.get("kind") or error_text),
                kind=structured_error.get("kind"),
                suggestedCommand=structured_error.get("suggestedCommand"),
                detailTool=structured_error.get("detailTool"),
            )
        return _command_session_payload(
            mode=normalized_mode,
            session_id=normalized_session,
            command_id=normalized_session,
            ok=False,
            summary=error_text,
            recommended_next_action="none",
            error=error_text,
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
    """Observe the latest output from an existing terminal session.

    This is the compatibility shortcut for command_session_broker(mode="observe"). Use it only after a command
    has returned a commandId/sessionId. For new commands, start with run_system_command(mode=auto).
    """
    broker = _compat_native_attr("command_session_broker", command_session_broker)
    return broker.func(mode="observe", command_id=command_id)

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
            sections.append(f"Status:\n{json.dumps(chat_cli_status, ensure_ascii=False, indent=2)}")
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
        sections.append(f"Status:\n{json.dumps(compact_status, ensure_ascii=False, indent=2)}")
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
            f"\n\nScreen Snapshot:\n{screen_snapshot}\n\nStatus:\n{json.dumps(compact_status, ensure_ascii=False, indent=2)}"
            if screen_snapshot
            else f"\n\nStatus:\n{json.dumps(compact_status, ensure_ascii=False, indent=2)}"
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
                f"Status:\n{json.dumps(compact_status, ensure_ascii=False, indent=2)}"
            )
        return f"No new output yet. Status:\n{json.dumps(_build_terminal_status_tool_view(status), ensure_ascii=False, indent=2)}"
    if raw_changed:
        bg_proc.mark_raw_frame_reported()
    if screen_changed:
        bg_proc.mark_screen_reported()
    return new_out

@tool
def send_background_input(command_id: str, input_text: str) -> str:
    """Send text or choices to an interactive terminal session.

    This is the compatibility shortcut for command_session_broker(mode="input"). Use it only when observation
    shows the session is waiting for input. Include a real newline or '\\n' to simulate Enter.
    """
    broker = _compat_native_attr("command_session_broker", command_session_broker)
    return broker.func(mode="input", command_id=command_id, input_text=input_text)

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
        sections.append(f"Status:\n{json.dumps(compact_status, ensure_ascii=False, indent=2)}")
        return "\n\n".join(section for section in sections if section)
    except Exception as e:
        return f"Error sending input: {e}"

@tool
def terminate_background_command(command_id: str) -> str:
    """Stop an existing terminal session.

    Use this when the user asks to stop, the command is stuck, the dev server is no longer needed, or a safer
    route has replaced the current terminal work. For normal completed commands, prefer observing the final
    output instead of terminating.
    """
    broker = _compat_native_attr("command_session_broker", command_session_broker)
    return broker.func(mode="terminate", command_id=command_id)

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

