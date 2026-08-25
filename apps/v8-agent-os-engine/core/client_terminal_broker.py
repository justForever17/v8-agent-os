from __future__ import annotations

import os
import json
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.tools.native.command import BackgroundProcess, _bg_processes, _prune_stale_background_processes
from core.workspace_capability import build_workspace_binding, workspace_side_effect_block_payload
from core.workspace_resolution import workspace_resolution_service


MANUAL_TERMINAL_SESSION_PREFIX = "manual-terminal:"
TERMINAL_WS_TICKET_TTL_SECONDS = 60
_manual_terminal_sessions: dict[str, dict[str, Any]] = {}
_terminal_ws_tickets: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quote_command(executable: str, args: list[str] | None = None) -> str:
    parts = [executable, *(args or [])]
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def _terminal_profile(profile_id: str, label: str, executable: str, args: list[str] | None = None) -> dict[str, Any] | None:
    resolved = shutil.which(executable)
    if not resolved:
        return None
    return {
        "id": profile_id,
        "label": label,
        "command": _quote_command(resolved, args or []),
        "executable": resolved,
    }


def list_terminal_profiles() -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    if sys.platform == "win32":
        for item in (
            _terminal_profile("pwsh", "PowerShell 7", "pwsh.exe", ["-NoLogo"]),
            _terminal_profile("powershell", "Windows PowerShell", "powershell.exe", ["-NoLogo"]),
            _terminal_profile("cmd", "Command Prompt", "cmd.exe"),
        ):
            if item:
                profiles.append(item)
    else:
        shell = os.environ.get("SHELL")
        if shell and Path(shell).exists():
            profiles.append({
                "id": "default",
                "label": Path(shell).name,
                "command": _quote_command(shell, ["-l"]),
                "executable": shell,
            })
        for executable in ("zsh", "bash", "fish", "sh"):
            item = _terminal_profile(executable, executable, executable, ["-l"] if executable != "fish" else [])
            if item and not any(existing["executable"] == item["executable"] for existing in profiles):
                profiles.append(item)

    return {
        "enabled": bool(profiles),
        "profiles": profiles,
        "defaultProfileId": profiles[0]["id"] if profiles else "",
    }


def _resolve_profile(profile_id: str | None) -> dict[str, Any]:
    catalog = list_terminal_profiles()
    profiles = catalog.get("profiles") or []
    requested = str(profile_id or catalog.get("defaultProfileId") or "").strip()
    for profile in profiles:
        if profile.get("id") == requested:
            return profile
    if profiles:
        return profiles[0]
    raise RuntimeError("No local terminal profile is available on this machine.")


def _resolve_cwd(cwd: str | None) -> Path:
    raw = str(cwd or "").strip()
    resolved = Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise RuntimeError(f"Terminal workspace does not exist or is not a directory: {resolved}")
    return resolved


def _snapshot_for_session(session_id: str, *, output_delta: str = "") -> dict[str, Any]:
    session = _manual_terminal_sessions.get(session_id)
    if not session:
        return {
            "ok": False,
            "sessionId": session_id,
            "status": "not_found",
            "outputDelta": "",
            "screenSnapshot": "",
            "isRunning": False,
        }

    _prune_stale_background_processes()
    command_id = str(session.get("commandId") or "")
    process = _bg_processes.get(command_id)
    if not process:
        session["status"] = "not_found"
        return {
            "ok": False,
            "sessionId": session_id,
            "commandId": command_id,
            "profileId": session.get("profileId"),
            "cwd": session.get("cwd"),
            "status": "not_found",
            "outputDelta": output_delta,
            "screenSnapshot": "",
            "isRunning": False,
            "createdAt": session.get("createdAt"),
            "updatedAt": _now_iso(),
        }

    status = dict(process.status_snapshot())
    session["updatedAt"] = _now_iso()
    return {
        "ok": True,
        "sessionId": session_id,
        "commandId": command_id,
        "profileId": session.get("profileId"),
        "profileLabel": session.get("profileLabel"),
        "cwd": session.get("cwd"),
        "status": "running" if process.is_running else "stopped",
        "outputDelta": output_delta,
        "screenSnapshot": status.get("stable_screen_snapshot") or status.get("screen_snapshot") or "",
        "rawScreenSnapshot": status.get("screen_snapshot") or "",
        "isRunning": bool(process.is_running),
        "awaitingInput": bool(status.get("awaiting_input")),
        "usesTty": bool(status.get("uses_tty")),
        "ttyMode": status.get("tty_mode"),
        "cols": status.get("cols"),
        "rows": status.get("rows"),
        "returnCode": status.get("return_code"),
        "startedAt": status.get("started_at") or session.get("createdAt"),
        "completedAt": status.get("completed_at"),
        "createdAt": session.get("createdAt"),
        "updatedAt": session.get("updatedAt"),
    }


def create_terminal_session(
    *,
    profile_id: str | None = None,
    cwd: str | None = None,
    conversation_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    profile = _resolve_profile(profile_id)
    requested_cwd = str(cwd or "").strip() or workspace_resolution_service.get_main_workspace_path()
    binding = build_workspace_binding(
        {
            "runtime_kind": "chat",
            "workspace_path": requested_cwd,
            "workspace_id": str(workspace_id or "").strip() or None,
            "project_id": str(project_id or "").strip() or None,
        },
        runtime_kind="chat",
    )
    if not binding.side_effects_allowed:
        raise RuntimeError(
            json.dumps(
                workspace_side_effect_block_payload(binding, operation="manual_terminal", subject=requested_cwd),
                ensure_ascii=False,
            )
        )
    resolved_cwd = _resolve_cwd(str(binding.active_workspace_root))
    terminal_id = f"term_{uuid.uuid4().hex[:12]}"
    command_id = terminal_id
    marker_session_id = f"{MANUAL_TERMINAL_SESSION_PREFIX}{terminal_id}"

    process = BackgroundProcess(
        str(profile["command"]),
        session_id=marker_session_id,
        run_id=None,
        interactive=True,
        profile="shell",
        profile_reason="manual_terminal",
        cwd=str(resolved_cwd),
    )
    process.command_id = command_id
    _bg_processes[command_id] = process
    created_at = _now_iso()
    _manual_terminal_sessions[terminal_id] = {
        "sessionId": terminal_id,
        "commandId": command_id,
        "conversationId": str(conversation_id or ""),
        "profileId": profile.get("id"),
        "profileLabel": profile.get("label"),
        "cwd": str(resolved_cwd),
        "status": "running",
        "createdAt": created_at,
        "updatedAt": created_at,
    }
    return _snapshot_for_session(terminal_id, output_delta=process.get_new_output())


def create_managed_command_session(
    *,
    command: str,
    cwd: str | None = None,
    conversation_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    profile_reason: str = "managed_command",
    timeout_seconds: float | int | None = None,
) -> dict[str, Any]:
    """Start a governed, observable non-interactive command without allocating a PTY."""

    normalized_command = str(command or "").strip()
    if not normalized_command:
        raise RuntimeError("Managed command cannot be empty.")
    requested_cwd = str(cwd or "").strip() or workspace_resolution_service.get_main_workspace_path()
    binding = build_workspace_binding(
        {
            "runtime_kind": "chat",
            "workspace_path": requested_cwd,
            "workspace_id": str(workspace_id or "").strip() or None,
            "project_id": str(project_id or "").strip() or None,
        },
        runtime_kind="chat",
    )
    if not binding.side_effects_allowed:
        raise RuntimeError(
            json.dumps(
                workspace_side_effect_block_payload(
                    binding,
                    operation="managed_command_session",
                    subject=normalized_command,
                ),
                ensure_ascii=False,
            )
        )
    resolved_cwd = _resolve_cwd(str(binding.active_workspace_root))
    terminal_id = f"term_{uuid.uuid4().hex[:12]}"
    marker_session_id = f"{MANUAL_TERMINAL_SESSION_PREFIX}{terminal_id}"
    reason = str(profile_reason or "managed_command").strip() or "managed_command"
    process = BackgroundProcess(
        normalized_command,
        session_id=marker_session_id,
        run_id=None,
        interactive=False,
        terminal_mode="pipe",
        profile="shell",
        profile_reason=reason,
        cwd=str(resolved_cwd),
        timeout_seconds=timeout_seconds,
    )
    process.command_id = terminal_id
    _bg_processes[terminal_id] = process
    created_at = _now_iso()
    _manual_terminal_sessions[terminal_id] = {
        "sessionId": terminal_id,
        "commandId": terminal_id,
        "conversationId": str(conversation_id or ""),
        "profileId": "managed-pipe",
        "profileLabel": "Managed command",
        "cwd": str(resolved_cwd),
        "status": "running",
        "createdAt": created_at,
        "updatedAt": created_at,
    }
    return _snapshot_for_session(terminal_id, output_delta=process.get_new_output())


def list_terminal_sessions(*, conversation_id: str | None = None) -> dict[str, Any]:
    normalized_conversation_id = str(conversation_id or "").strip()
    sessions: list[dict[str, Any]] = []
    for session_id, session in list(_manual_terminal_sessions.items()):
        if normalized_conversation_id and str(session.get("conversationId") or "").strip() != normalized_conversation_id:
            continue
        snapshot = _snapshot_for_session(session_id)
        if not snapshot.get("ok") and snapshot.get("status") == "not_found":
            continue
        sessions.append(snapshot)
    sessions.sort(key=lambda item: str(item.get("createdAt") or ""))
    return {"ok": True, "sessions": sessions}


def read_terminal_session(session_id: str) -> dict[str, Any]:
    session_id = str(session_id or "").strip()
    session = _manual_terminal_sessions.get(session_id)
    output_delta = ""
    if session:
        process = _bg_processes.get(str(session.get("commandId") or ""))
        if process:
            output_delta = process.get_new_output()
    return _snapshot_for_session(session_id, output_delta=output_delta)


def consume_terminal_session_output(session_id: str) -> dict[str, Any]:
    return read_terminal_session(session_id)


def write_terminal_session_input(session_id: str, input_text: str) -> dict[str, Any]:
    session = _manual_terminal_sessions.get(str(session_id or "").strip())
    if not session:
        return _snapshot_for_session(session_id)
    process = _bg_processes.get(str(session.get("commandId") or ""))
    if not process:
        return _snapshot_for_session(session_id)
    process.write_input(str(input_text or ""))
    return _snapshot_for_session(str(session_id))


def resize_terminal_session(session_id: str, *, cols: int | None = None, rows: int | None = None) -> dict[str, Any]:
    session = _manual_terminal_sessions.get(str(session_id or "").strip())
    if not session:
        return _snapshot_for_session(session_id)
    process = _bg_processes.get(str(session.get("commandId") or ""))
    if not process:
        return _snapshot_for_session(session_id)
    resize = getattr(process, "resize_terminal", None)
    if callable(resize):
        resize(int(cols or 80), int(rows or 24))
    return _snapshot_for_session(str(session_id))


def send_terminal_input(session_id: str, input_text: str) -> dict[str, Any]:
    session = _manual_terminal_sessions.get(str(session_id or "").strip())
    if not session:
        return _snapshot_for_session(session_id)
    process = _bg_processes.get(str(session.get("commandId") or ""))
    if not process:
        return _snapshot_for_session(session_id)
    process.write_input(str(input_text or ""))
    return _snapshot_for_session(str(session_id), output_delta=process.get_new_output())


def terminate_terminal_session(session_id: str) -> dict[str, Any]:
    session = _manual_terminal_sessions.get(str(session_id or "").strip())
    if not session:
        return _snapshot_for_session(session_id)
    process = _bg_processes.get(str(session.get("commandId") or ""))
    if process:
        process.terminate()
    session["status"] = "stopped"
    session["updatedAt"] = _now_iso()
    return _snapshot_for_session(str(session_id), output_delta=process.get_new_output() if process else "")


def _prune_terminal_ws_tickets(now: float | None = None) -> None:
    current = float(now if now is not None else time.time())
    for ticket, record in list(_terminal_ws_tickets.items()):
        if bool(record.get("used")) or float(record.get("expiresAtEpoch") or 0) <= current:
            _terminal_ws_tickets.pop(ticket, None)


def issue_terminal_ws_ticket(
    session_id: str,
    *,
    user_email: str,
    ttl_seconds: int = TERMINAL_WS_TICKET_TTL_SECONDS,
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    normalized_user_email = str(user_email or "").strip()
    if not normalized_session_id or normalized_session_id not in _manual_terminal_sessions:
        raise RuntimeError("Terminal session not found.")
    if not normalized_user_email:
        raise RuntimeError("Terminal user is required.")

    _prune_terminal_ws_tickets()
    ticket = secrets.token_urlsafe(32)
    ttl = max(5, min(int(ttl_seconds or TERMINAL_WS_TICKET_TTL_SECONDS), 300))
    expires_at_epoch = time.time() + ttl
    _terminal_ws_tickets[ticket] = {
        "sessionId": normalized_session_id,
        "userEmail": normalized_user_email,
        "expiresAtEpoch": expires_at_epoch,
        "used": False,
    }
    return {
        "ok": True,
        "sessionId": normalized_session_id,
        "ticket": ticket,
        "expiresAt": datetime.fromtimestamp(expires_at_epoch, timezone.utc).isoformat(),
        "ttlSeconds": ttl,
    }


def consume_terminal_ws_ticket(session_id: str, ticket: str) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    normalized_ticket = str(ticket or "").strip()
    if not normalized_session_id or not normalized_ticket:
        return {"ok": False, "reason": "missing_ticket"}

    _prune_terminal_ws_tickets()
    record = _terminal_ws_tickets.get(normalized_ticket)
    if not record:
        return {"ok": False, "reason": "invalid_ticket"}
    if float(record.get("expiresAtEpoch") or 0) <= time.time():
        _terminal_ws_tickets.pop(normalized_ticket, None)
        return {"ok": False, "reason": "expired_ticket"}
    if not secrets.compare_digest(str(record.get("sessionId") or ""), normalized_session_id):
        return {"ok": False, "reason": "session_mismatch"}

    _terminal_ws_tickets.pop(normalized_ticket, None)
    return {
        "ok": True,
        "sessionId": normalized_session_id,
        "userEmail": str(record.get("userEmail") or ""),
    }
