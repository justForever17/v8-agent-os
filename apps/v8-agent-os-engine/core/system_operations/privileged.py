from __future__ import annotations

import os
from pathlib import Path
import platform
import sys
import time

def windows_privilege_helper() -> Path | None:
    installed = Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or "C:/Program Files") / "V8AgentOS/SystemOperations/v8-system-operations.exe"
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    built = Path(__file__).resolve().parents[2] / "native/v8-system-operations/build" / arch / "v8-system-operations.exe"
    return next((path for path in (installed, built) if path.is_file()), None)


def execute_privileged(payload: dict, *, username: str, domain: str, password: str, operation_id: str, context: dict) -> dict:
    if sys.platform == "win32":
        helper = windows_privilege_helper()
        if helper is None:
            return {"ok": False, "code": "privilege_component_required", "summary": "请先安装受控提权组件。"}
        argv = [str(helper), "--execute"]
    elif sys.platform in {"linux", "darwin"}:
        argv = [sys.executable, "-I", "-S", str(Path(__file__).with_name("sudo_worker.py"))]
    else:
        return {"ok": False, "code": "privilege_platform_unavailable", "summary": "当前平台未接入受控提权。"}
    request = {**payload, "username": username, "domain": domain, "password": password, "requestId": operation_id, "expiresAt": int(time.time() * 1000) + 60000}
    from .process_client import call_private_helper
    return call_private_helper(argv, request, timeout=float(payload["timeoutSeconds"]) + 25, context=context)
