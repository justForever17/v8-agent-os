"""Root-side, bounded command runner. Called by sudo, never takes credentials."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time


def main() -> dict:
    data = sys.stdin.buffer.read(1_048_577)
    if len(data) > 1_048_576:
        return {"ok": False, "code": "privileged_request_oversize"}
    request = json.loads(data)
    if os.geteuid() != 0 or "password" in request:
        return {"ok": False, "code": "privileged_context_invalid"}
    argv = request.get("argv")
    timeout = request.get("timeoutSeconds")
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or "\x00" in item for item in argv) or not isinstance(timeout, int) or not 5 <= timeout <= 600:
        return {"ok": False, "code": "privileged_request_invalid"}
    parent = int(request["parentPid"])
    process = subprocess.Popen(argv, cwd=request.get("cwd"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    selector = selectors.DefaultSelector()
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    deadline = time.monotonic() + timeout
    stopped = False
    try:
        while True:
            try:
                os.kill(parent, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            if not alive or time.monotonic() >= deadline:
                stopped = True
                break
            if not selector.get_map() and process.poll() is not None:
                break
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                remaining = 262144 - len(output[key.data])
                output[key.data].extend(chunk[:remaining])
                truncated[key.data] |= len(chunk) > remaining
    finally:
        # Background descendants are not an authorized durable service. Clean
        # the entire private process group after either completion or failure.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return {"ok": not stopped and process.returncode == 0, "verified": not stopped, "elevated": True, "exitCode": process.returncode,
            "code": "privileged_command_stopped" if stopped else "privileged_command_completed",
            "stdout": output["stdout"].decode("utf-8", errors="replace"), "stderr": output["stderr"].decode("utf-8", errors="replace"), "outputTruncated": truncated}


if __name__ == "__main__":
    try:
        answer = main()
    except Exception:
        answer = {"ok": False, "code": "privileged_execution_failed"}
    print(json.dumps(answer, ensure_ascii=False))
