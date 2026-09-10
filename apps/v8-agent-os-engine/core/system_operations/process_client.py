"""Private request transport. Never log stdin, helper stderr or raw exceptions."""
from __future__ import annotations

import json
import subprocess
import time

from core.process_launch import windowless_subprocess_kwargs


def _stop_requested(context: dict) -> bool:
    from core.tools.native.command import _peek_command_control_signal
    signal = _peek_command_control_signal(context) or {}
    return str(signal.get("command") or "") in {"cancel", "interrupt", "pause"}


def call_private_helper(argv: list[str], payload: dict, *, timeout: float, context: dict) -> dict:
    if _stop_requested(context):
        return {"ok": False, "verified": False, "executed": False, "code": "system_operation_cancelled_before_start", "summary": "运行已暂停或取消，未启动系统操作。"}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **windowless_subprocess_kwargs())
    deadline = time.monotonic() + timeout
    first = True
    try:
        while True:
            try:
                stdout, _ = process.communicate(input=encoded if first else None, timeout=0.25)
                if len(stdout) > 2_000_000:
                    raise ValueError("oversize helper response")
                result = json.loads(stdout.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("invalid helper response")
                if process.returncode != 0:
                    result["ok"] = False
                if _stop_requested(context):
                    result.update({"ok": False, "code": "system_operation_interrupted", "summary": "运行已暂停或取消；保留实际系统结果，请核对后续操作。"})
                return result
            except subprocess.TimeoutExpired:
                first = False
                if _stop_requested(context) or time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    return {"ok": False, "verified": False, "code": "system_operation_interrupted", "summary": "操作已请求停止；若系统已接受操作，需核对实际结果，不会自动重试。"}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()
