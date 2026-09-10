from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from core.process_launch import windowless_subprocess_kwargs


def windows_unlock_helper() -> Path | None:
    if sys.platform != "win32":
        return None
    # A packaged installation is preferred; source-tree binaries are used only
    # from the checked-in native component's conventional build directory.
    installed = Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or "C:/Program Files") / "V8AgentOS/SessionUnlock/v8-session-unlock.exe"
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    built = Path(__file__).resolve().parents[2] / "native/v8-session-unlock/build" / arch / "v8-session-unlock.exe"
    return next((p for p in (installed, built) if p.is_file()), None)


def _unlock_client(mode: str, payload: dict | None = None, *, context: dict | None = None) -> dict:
    helper = windows_unlock_helper()
    if helper is None:
        return {"ok": False, "code": "unlock_component_required", "summary": "请安装受控 Windows 登录组件。"}
    if payload is not None:
        from .process_client import call_private_helper
        return call_private_helper([str(helper), mode], payload, timeout=65, context=context or {})
    try:
        process = subprocess.run(
            [str(helper), mode], input=b"",
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
            **windowless_subprocess_kwargs(),
        )
        value = json.loads(process.stdout.decode("utf-8"))
        if not isinstance(value, dict) or len(process.stdout) > 32768:
            raise ValueError("invalid helper response")
        if process.returncode != 0:
            value["ok"] = False
        return value
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "unlock_result_unverified", "summary": "未在期限内确认解锁，不会自动重试。"}
    except (OSError, ValueError):
        return {"ok": False, "code": "unlock_component_failed", "summary": "登录组件未能返回有效状态。"}


def platform_status() -> dict:
    if sys.platform == "win32":
        from .privileged import windows_privilege_helper
        from .setup import setup_status
        helper = windows_privilege_helper()
        privilege = {"registered": False, "available": False}
        if helper:
            try:
                response = subprocess.run([str(helper), "--status"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, **windowless_subprocess_kwargs())
                privilege = json.loads(response.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                privilege["code"] = "privilege_status_unavailable"
        return {"os": "windows", "unlock": _unlock_client("--status"), "privilege": privilege, "setup": setup_status()}
    return {"os": sys.platform, "unlock": {"ok": False, "code": "unlock_platform_not_integrated", "summary": "当前平台未接入受控解锁。"}}


def execute_platform_operation(payload: dict, *, username: str, domain: str, password: str, operation_id: str, context: dict) -> dict:
    if payload["action"] == "unlock":
        if sys.platform != "win32":
            return platform_status()["unlock"]
        status = _unlock_client("--status")
        session = status.get("sessionId")
        if not isinstance(session, int):
            return {"ok": False, "code": "unlock_session_unavailable", "summary": "无法确认当前控制台会话。"}
        return _unlock_client("--unlock", {
            "username": username, "domain": domain, "password": password,
            "requestId": operation_id, "expiresAt": int(time.time() * 1000) + 60000,
            "sessionId": session,
        }, context=context)
    from .privileged import execute_privileged
    return execute_privileged(payload, username=username, domain=domain, password=password, operation_id=operation_id, context=context)
