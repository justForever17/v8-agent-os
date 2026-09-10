"""Explicit Admin component setup. OS elevation is always Windows' own UAC."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
import sys
import threading

from .service import SystemOperationError

_lock = threading.Lock()
_pending = None
_last = {"state": "idle"}
COMPONENTS = {"unlock": "v8-session-unlock", "privilege": "v8-system-operations"}


def setup_status() -> dict:
    global _pending, _last
    with _lock:
        if _pending is not None:
            import win32event
            import win32process
            if win32event.WaitForSingleObject(_pending, 0) == win32event.WAIT_OBJECT_0:
                code = win32process.GetExitCodeProcess(_pending)
                _pending.Close()
                _pending = None
                _last = {**_last, "state": "completed" if code == 0 else "failed", "exitCode": code}
        return dict(_last)


def begin_setup(component: str, action: str) -> dict:
    global _pending, _last
    if sys.platform != "win32" or component not in COMPONENTS or action not in {"install", "uninstall"}:
        raise SystemOperationError("system_setup_unavailable", "当前平台或组件不支持该安装动作。", 422)
    if setup_status().get("state") in {"awaiting_os_approval", "running"}:
        raise SystemOperationError("system_setup_pending", "已有组件安装正在等待系统确认或执行。")
    component_root = Path(__file__).resolve().parents[2] / "native" / COMPONENTS[component]
    script = component_root / "install.ps1"
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    required = [COMPONENTS[component] + ".exe"] + (["V8SessionUnlock.dll"] if component == "unlock" else [])
    if not script.is_file() or (action == "install" and any(not (component_root / "build" / arch / name).is_file() for name in required)):
        raise SystemOperationError("system_setup_assets_missing", "当前安装缺少匹配架构的原生组件。")
    import win32api
    import win32con
    import win32security
    command = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Action", action.title(), "-Arch", arch, "-Unattended"]
    if component == "privilege" and action == "install":
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        command.extend(["-ClientSid", sid])
    executable = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    with _lock:
        if _last.get("state") in {"awaiting_os_approval", "running"}:
            raise SystemOperationError("system_setup_pending", "已有组件安装正在执行。")
        _last = {"state": "awaiting_os_approval", "component": component, "action": action}
    threading.Thread(target=_launch_setup, args=(executable, command), name="v8-system-component-setup", daemon=True).start()
    return {"requested": True, "state": "awaiting_os_approval"}


def _launch_setup(executable: str, command: list[str]) -> None:
    global _pending, _last
    initialized = False
    try:
        import pythoncom
        from win32com.shell import shell, shellcon
        pythoncom.CoInitialize()
        initialized = True
        # Only a fixed repository/package-owned installer is elevated. No model
        # command, password or arbitrary executable can enter this setup API.
        result = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas", lpFile=executable, lpParameters=subprocess.list2cmdline(command), nShow=0)
        with _lock:
            _pending = result["hProcess"]
            _last = {**_last, "state": "running"}
    except Exception as exc:
        cancelled = getattr(exc, "winerror", None) == 1223
        with _lock:
            _last = {**_last, "state": "cancelled" if cancelled else "failed"}
    finally:
        if initialized:
            pythoncom.CoUninitialize()
