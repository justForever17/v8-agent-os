"""Isolated sudo authentication parent. No application ever receives a password.

Both sudo invocations share this short-lived parent/session for sudo's default
ppid timestamp scope. Authentication-only -v has no command to inherit stdin;
the subsequent -n command receives a fresh stream containing no credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys


def main() -> dict:
    data = sys.stdin.buffer.read(1_048_577)
    if len(data) > 1_048_576:
        return {"ok": False, "code": "privileged_request_oversize"}
    request = json.loads(data)
    data = b""
    sudo = shutil.which("sudo")
    if not sudo or request.get("domain") or request.get("username") != pwd.getpwuid(os.getuid()).pw_name:
        return {"ok": False, "code": "sudo_account_unavailable", "summary": "请配置当前登录账户的 sudo 密码；域账户切换不受支持。"}
    # A new session without a tty keeps the default sudo cache scoped to this
    # worker. Never globally invalidate the user's existing authentication cache.
    os.setsid()
    password = request.pop("password")
    auth = subprocess.run([sudo, "-S", "-p", "", "-v"], input=(password + "\n").encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    password = ""
    if auth.returncode:
        return {"ok": False, "code": "sudo_authentication_failed", "summary": "sudo 认证未通过或需要额外验证，未执行命令。"}
    request["parentPid"] = os.getpid()
    result = subprocess.run([sudo, "-n", "--", sys.executable, "-I", "-S", str(Path(__file__).with_name("privileged_worker.py"))], input=json.dumps(request, ensure_ascii=False).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=float(request["timeoutSeconds"]) + 5)
    if result.returncode or not result.stdout:
        return {"ok": False, "code": "sudo_command_denied", "summary": "系统未授予本次提权执行，未确认命令结果。"}
    return json.loads(result.stdout)


if __name__ == "__main__":
    try:
        answer = main()
    except Exception:
        answer = {"ok": False, "code": "sudo_operation_failed", "summary": "sudo 操作未完成；未记录认证内容。"}
    print(json.dumps(answer, ensure_ascii=False))
