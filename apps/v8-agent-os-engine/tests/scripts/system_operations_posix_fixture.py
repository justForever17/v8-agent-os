"""Disposable WSL account fixture, explicitly invoked as root for --live only."""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import tempfile
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    args = parser.parse_args()
    if not args.live or not args.allow_side_effects or os.geteuid() != 0:
        parser.error("Explicit --live --allow-side-effects and disposable-environment root required")
    name = "v8optest" + uuid.uuid4().hex[:10]
    policy = Path("/etc/sudoers.d") / name
    timestamp = Path("/run/sudo/ts") / name
    assert not policy.exists()
    worker = Path(__file__).resolve().parents[2] / "core/system_operations/sudo_worker.py"
    password = secrets.token_urlsafe(30)
    created = False
    results = []
    try:
        subprocess.run(["useradd", "--system", "--no-create-home", "--shell", "/bin/sh", name], check=True, capture_output=True)
        created = True
        account_uid = pwd.getpwnam(name).pw_uid
        subprocess.run(["chpasswd"], input=f"{name}:{password}\n".encode(), check=True, capture_output=True)
        with policy.open("x") as stream:
            stream.write(f"Defaults:{name} timestamp_type=ppid,passwd_tries=1\n{name} ALL=(root) /usr/bin/python3\n")
        policy.chmod(0o440)
        subprocess.run(["visudo", "-c", "-f", str(policy)], check=True, capture_output=True)

        def invoke(label, argv, provided_password, timeout=5):
            request = {"action": "run_privileged", "argv": argv, "cwd": "/tmp", "timeoutSeconds": timeout, "username": name, "domain": "", "password": provided_password, "requestId": str(uuid.uuid4()), "expiresAt": int(time.time() * 1000) + 60000}
            start = time.monotonic()
            process = subprocess.run(["runuser", "-u", name, "--", "/usr/bin/python3", "-I", "-S", str(worker)], input=json.dumps(request, ensure_ascii=False).encode("utf-8"), capture_output=True, timeout=timeout+22)
            assert password.encode() not in process.stdout + process.stderr
            data = json.loads(process.stdout)
            results.append({"case": label, "elapsedMs": round((time.monotonic()-start)*1000), "resultCode": data.get("code"), "verified": data.get("verified", False)})
            return data

        inspect = ["/usr/bin/python3", "-c", "import os,sys,json; print(json.dumps({'uid':os.geteuid(),'stdinBytes':len(sys.stdin.buffer.read())}))"]
        data = invoke("password_auth_then_empty_command_stdin", inspect, password)
        assert data["ok"] and data["verified"] and account_uid != 0
        assert json.loads(data["stdout"]) == {"uid": 0, "stdinBytes": 0}
        data = invoke("wrong_password_no_command", inspect, "wrong-fixture-password")
        assert not data["ok"] and data["code"] == "sudo_authentication_failed"
        data = invoke("nonzero_not_success", ["/usr/bin/python3", "-c", "raise SystemExit(7)"], password)
        assert not data["ok"] and data["exitCode"] == 7
        data = invoke("deadline", ["/usr/bin/python3", "-c", "import time; time.sleep(30)"], password)
        assert not data["ok"] and data["code"] == "privileged_command_stopped"
        for chars in (7005, 15005):
            text = "# " + "中" * chars + "\nprint(42)"
            data = invoke(f"unicode_request_{chars}", ["/usr/bin/python3", "-c", text], password)
            assert data["ok"] and data["stdout"].strip() == "42"
        # Test cancellation after both pipes reached EOF: the old blocking wait
        # lost the parent liveness signal until the full command deadline.
        with tempfile.TemporaryDirectory(prefix="v8-system-cancel-") as directory:
            pid_file = Path(directory) / "pid"
            parent = subprocess.Popen(["/bin/sleep", "30"])
            child = None
            try:
                source = f"import os,time,pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); os.close(1); os.close(2); time.sleep(30)"
                request = {"argv": ["/usr/bin/python3", "-c", source], "cwd": directory, "timeoutSeconds": 30, "parentPid": parent.pid}
                child = subprocess.Popen(["/usr/bin/python3", "-I", "-S", str(worker.with_name("privileged_worker.py"))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                child.stdin.write(json.dumps(request).encode())
                child.stdin.close()
                child.stdin = None
                deadline = time.monotonic() + 5
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                assert pid_file.exists()
                time.sleep(.2)
                start = time.monotonic()
                parent.terminate()
                parent.wait(timeout=2)
                output, _ = child.communicate(timeout=3)
                data = json.loads(output)
                assert not data["ok"] and data["code"] == "privileged_command_stopped"
                assert not Path(f"/proc/{pid_file.read_text()}").exists()
                results.append({"case": "cancel_after_output_eof", "elapsedMs": round((time.monotonic()-start)*1000), "verified": False, "resultCode": data["code"]})
            finally:
                for process in (parent, child):
                    if process and process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
        # Change only this fixture's policy. If sudo no longer consumes stdin,
        # the synthetic credential must still never become application input.
        policy.write_text(f"Defaults:{name} timestamp_type=ppid\n{name} ALL=(root) NOPASSWD: /usr/bin/python3\n")
        subprocess.run(["visudo", "-c", "-f", str(policy)], check=True, capture_output=True)
        data = invoke("nopasswd_empty_command_stdin", inspect, "synthetic-password-not-for-application")
        assert data["ok"] and json.loads(data["stdout"])["stdinBytes"] == 0
    finally:
        if policy.is_file() and policy.parent == Path("/etc/sudoers.d") and policy.name == name:
            policy.unlink()
        if created:
            subprocess.run(["userdel", name], check=True, capture_output=True)
        if timestamp.is_file() and timestamp.name == name:
            timestamp.unlink()
        password = ""
    assert not policy.exists()
    try:
        pwd.getpwnam(name)
    except KeyError:
        pass
    else:
        raise AssertionError("fixture account remains")
    print(json.dumps({"layer": "real WSL Linux sudo/PAM", "passed": len(results), "cases": results, "fixtureAccountRemoved": True, "fixtureSudoPolicyRemoved": True}))


if __name__ == "__main__":
    main()
