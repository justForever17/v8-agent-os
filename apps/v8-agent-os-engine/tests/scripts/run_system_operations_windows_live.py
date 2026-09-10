"""Explicit local Windows acceptance. Credentials stay inside the product owner."""
from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import time
import urllib.request
import uuid

ENGINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENGINE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["status", "install-privilege", "install-unlock", "privilege", "unlock", "lifecycle"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--source-unlock-client", action="store_true", help="Use the newly built native client with the already installed provider for a candidate test")
    args = parser.parse_args()
    if sys.platform != "win32" or not args.live or (args.stage != "status" and not args.allow_side_effects):
        parser.error("Windows --live required; changes additionally need --allow-side-effects")
    from core.database import db
    from core.system_base import get_internal_secret
    from core.system_operations.platform import platform_status
    from core.system_operations.service import system_operation_service
    if args.stage == "lifecycle":
        import os
        import subprocess
        import win32api
        import win32con
        import win32event
        import win32process
        import win32security
        from win32com.shell import shell, shellcon
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        directory = Path(tempfile.mkdtemp(prefix="v8-system-lifecycle-"))
        report = directory / "v8-system-lifecycle-result.json"
        script = ENGINE / "tests/fixtures/system_operations_lifecycle.ps1"
        command = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Live", "-AllowSideEffects", "-ClientSid", sid, "-ReportPath", str(report)]
        handle = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas", lpFile=str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"), lpParameters=subprocess.list2cmdline(command), nShow=0)["hProcess"]
        try:
            result = win32event.WaitForSingleObject(handle, 60000)
            if result != win32event.WAIT_OBJECT_0:
                raise RuntimeError("Lifecycle installer still pending; do not retry until actual state is checked")
            value = json.loads(report.read_text(encoding="utf-8-sig")) if report.is_file() else {}
            print(json.dumps({"layer": "physical Windows component removal and restoration", "exitCode": win32process.GetExitCodeProcess(handle), "checks": value}))
            if not value or not all(value.values()) or win32process.GetExitCodeProcess(handle):
                raise RuntimeError("Windows lifecycle acceptance failed")
        finally:
            handle.Close()
        return
    if args.source_unlock_client:
        if args.stage != "unlock":
            parser.error("Source unlock client applies only to the unlock stage")
        from core.system_operations import platform as operation_platform
        import platform
        arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
        candidate = ENGINE / "native/v8-session-unlock/build" / arch / "v8-session-unlock.exe"
        if not candidate.is_file():
            raise RuntimeError("Built native client is missing")
        operation_platform.windows_unlock_helper = lambda: candidate
    with db.get_connection() as conn:
        owners = [row[0] for row in conn.execute("SELECT DISTINCT owner_id FROM system_operation_credentials")]
    if len(owners) != 1:
        raise RuntimeError("Exactly one explicitly configured acceptance owner is required")
    owner = owners[0]

    def api(path, method="GET"):
        request = urllib.request.Request("http://127.0.0.1:9530/v1/system-operations/" + path, data=b"" if method == "POST" else None, method=method,
            headers={"x-v8-agent-os-secret": get_internal_secret(), "x-v8-agent-os-user-email": owner, "x-v8-admin-role": "ADMIN"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    if args.stage == "status":
        value = api("settings")
        print(json.dumps({"platform": value["platform"], "configured": {key: profile["configured"] for key, profile in value["profiles"].items()}}, ensure_ascii=False))
        return
    if args.stage.startswith("install-"):
        component = args.stage.removeprefix("install-")
        print(json.dumps(api(f"components/{component}/install", "POST")), flush=True)
        return

    status = platform_status()
    if not status["privilege" if args.stage == "privilege" else "unlock"].get("registered"):
        raise RuntimeError("Component is not registered; no authentication or locking attempted")
    with tempfile.TemporaryDirectory(prefix="v8-system-live-") as directory:
        identity = "system-operations-live-" + uuid.uuid4().hex
        session, run = identity, identity + "-run"
        db.create_or_update_session(session, "Controlled system operations live", user_id=owner)
        db.create_run_record(run, session, user_id=owner)
        context = {"user_id": owner, "session_id": session, "run_id": run, "agent_id": "supervisor", "runtime_kind": "chat", "safety_approval_mode": "minimal", "workspace_path": directory}
        result = None
        try:
            if args.stage == "unlock":
                if not system_operation_service.settings(owner)["profiles"]["unlock"]["configured"]:
                    raise RuntimeError("Unlock credential is not configured")
                if not ctypes.windll.user32.LockWorkStation():
                    raise RuntimeError("Lock request rejected")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    observation = platform_status()["unlock"]
                    if observation.get("locked") is True and observation.get("available") is True:
                        break
                    time.sleep(.3)
                else:
                    raise RuntimeError("Locked-session provider not ready; use the normal Windows sign-in method")
                print(json.dumps({"beforeLocked": True, "providerReady": True}), flush=True)
                payload = {"action": "unlock"}
            else:
                # No write target outside the owned fixture, no account data in
                # stdout; report an independently observed actual process token.
                command = "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if(!$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 19}; [Console]::Write('ELEVATED-LIVE-OK')"
                payload = {"action": "run_privileged", "command": command, "argv": [str(Path(__import__('os').environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'), "-NoProfile", "-NonInteractive", "-Command", command], "cwd": directory, "timeoutSeconds": 10}

            def authorize(op):
                from core.tools.native.tool_governance import _enforce_safety_decision
                from erc.safety_guardian import SafetyDecision, safety_guardian
                decision = safety_guardian.assess_system_command(payload.get("command", ""), runtime_context={**context, "command_cwd": directory})
                if decision.is_allow():
                    decision = SafetyDecision(verdict="review", risk_code="controlled_system_operation", governance_target="system_operation")
                decision.details = {**(decision.details or {}), "operationId": op["operationId"], "runtime_context": context, "command": payload.get("command", "unlock")}
                allowed, _ = _enforce_safety_decision(decision, tool_call_id="live-call", question="Explicitly authorized Windows acceptance")
                if not allowed:
                    raise RuntimeError("Real policy rejected the acceptance operation")

            from erc.runtime_context import bind_runtime_context
            with bind_runtime_context(**context):
                result = system_operation_service.execute(payload=payload, context=context, tool_call_id="live-call", authorize=authorize)
            projection = {key: result.get(key) for key in ("ok", "code", "status", "verified", "elevated", "executed", "locked", "elapsedMs", "exitCode", "processTreeStopped") if key in result}
            if args.stage == "privilege":
                projection["actualAdminOutput"] = result.get("stdout") == "ELEVATED-LIVE-OK"
            else:
                projection["afterLocked"] = platform_status()["unlock"].get("locked")
            print(json.dumps(projection, ensure_ascii=False), flush=True)
            if not result.get("ok") or (args.stage == "privilege" and not projection["actualAdminOutput"]):
                raise RuntimeError("The actual operation did not pass; no automatic retry")
        finally:
            with db.get_connection() as conn:
                conn.execute("UPDATE run_records SET status=? WHERE id=?", ("completed" if result and result.get("ok") else "failed", run))
                conn.commit()


if __name__ == "__main__":
    main()
