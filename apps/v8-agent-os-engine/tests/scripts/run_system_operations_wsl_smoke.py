"""Explicit local Linux privilege smoke; sudo must already allow NOPASSWD.

Creates no accounts and edits no sudo policy. A synthetic password sentinel proves
that cached/NOPASSWD authentication cannot leak into the command's stdin.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--distribution", default="Ubuntu-22.04")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for real sudo execution")
    def wsl(*command, **kwargs):
        return subprocess.run(["wsl", "-d", args.distribution, "--", *command], capture_output=True, timeout=30, **kwargs)
    if wsl("sudo", "-n", "-v").returncode:
        raise SystemExit("Existing noninteractive sudo authority required; no password or policy changes performed.")
    username = wsl("id", "-un").stdout.decode().strip()
    worker = Path(__file__).resolve().parents[2] / "core/system_operations/sudo_worker.py"
    linux_path = "/mnt/" + worker.drive[0].lower() + worker.as_posix()[2:]
    sentinel = "synthetic-credential-must-not-reach-command"
    cases = {
        "elevated_empty_stdin": ["/usr/bin/python3", "-c", "import os,sys,json; print(json.dumps({'uid':os.geteuid(),'stdinBytes':len(sys.stdin.buffer.read())}))"],
        "nonzero_is_failure": ["/usr/bin/python3", "-c", "raise SystemExit(7)"],
        "deadline_kills_tree": ["/usr/bin/python3", "-c", "import time; time.sleep(30)"],
    }
    results = []
    for name, argv in cases.items():
        request = {"action": "run_privileged", "argv": argv, "cwd": "/tmp", "timeoutSeconds": 5, "username": username, "domain": "", "password": sentinel, "requestId": str(uuid.uuid4()), "expiresAt": int(time.time() * 1000) + 60000}
        start = time.monotonic()
        proc = wsl("python3", "-I", "-S", linux_path, input=json.dumps(request).encode())
        assert sentinel.encode() not in proc.stdout + proc.stderr
        data = json.loads(proc.stdout)
        if name == "elevated_empty_stdin":
            assert data["ok"] and data["verified"] and data["elevated"]
            assert json.loads(data["stdout"]) == {"uid": 0, "stdinBytes": 0}
        elif name == "nonzero_is_failure":
            assert not data["ok"] and data["exitCode"] == 7
        else:
            assert not data["ok"] and data["code"] == "privileged_command_stopped"
        results.append({"case": name, "passed": True, "elapsedMs": round((time.monotonic() - start) * 1000)})
    print(json.dumps({"layer": "real WSL Linux sudo NOPASSWD", "cases": results, "passwordAuthenticationTested": False}))


if __name__ == "__main__":
    main()
