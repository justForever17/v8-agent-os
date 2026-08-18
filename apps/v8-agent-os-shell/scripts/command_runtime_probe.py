"""Offline command-backend probe for packaged desktop smoke.

The probe executes only fixed Python snippets inside a temporary directory. It
validates the real packaged pipe and PTY backends without network access or
user workspace writes, and emits one bounded JSON result with no command text,
paths, output, or exception details.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil


class ProbeInputError(ValueError):
    """The packaged command runtime cannot be probed safely."""


def _resolve_engine_root() -> Path:
    repo_value = str(os.environ.get("V8_REPO_ROOT") or "").strip()
    state_value = str(os.environ.get("V8_AGENT_OS_HOME") or "").strip()
    if not repo_value:
        raise ProbeInputError("engine_root_missing")
    if not state_value:
        raise ProbeInputError("state_root_missing")
    repo_root = Path(repo_value).expanduser().resolve(strict=False)
    state_root = Path(state_value).expanduser().resolve(strict=False)
    engine_root = repo_root / "apps" / "v8-agent-os-engine"
    if not engine_root.is_dir():
        raise ProbeInputError("engine_root_invalid")
    if not state_root.is_dir():
        raise ProbeInputError("state_root_invalid")
    return engine_root


def _load_command_backend(engine_root: Path):
    engine_text = str(engine_root)
    if engine_text not in sys.path:
        sys.path.insert(0, engine_text)
    from core.tools.native.command import BackgroundProcess

    return BackgroundProcess


def _python_command(*arguments: str) -> str:
    argv = [sys.executable, "-I", "-u", *arguments]
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def _shell_dialect() -> str:
    return "cmd" if sys.platform == "win32" else "sh"


def _process_stopped(process_id: int | None, *, timeout_seconds: float = 5.0) -> bool:
    if process_id is None:
        return False
    deadline = time.time() + timeout_seconds
    while psutil.pid_exists(process_id) and time.time() < deadline:
        time.sleep(0.05)
    return not psutil.pid_exists(process_id)


def _wait_for_terminal(process: Any, *, timeout_seconds: float = 10.0) -> tuple[str, dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    output = ""
    while process.is_running and time.time() < deadline:
        output += process.get_new_output()
        time.sleep(0.05)
    process.reader_thread.join(timeout=2)
    output += process.get_new_output()
    return output, dict(process.status_snapshot())


def _ordinary_probe(background_process: type, cwd: Path) -> dict[str, Any]:
    marker = "V8OS_PACKAGED_PIPE_OK"
    process = background_process(
        _python_command("-c", f"print({marker!r})"),
        interactive=False,
        cwd=str(cwd),
        shell_dialect=_shell_dialect(),
        timeout_seconds=10,
    )
    output, status = _wait_for_terminal(process)
    return {
        "backend": status.get("backend"),
        "completed": status.get("is_running") is False,
        "exitCodeObserved": status.get("return_code") == 0,
        "outputObserved": marker in output,
    }


def _failure_probe(background_process: type, cwd: Path) -> dict[str, Any]:
    process = background_process(
        _python_command("-c", "raise SystemExit(7)"),
        interactive=False,
        cwd=str(cwd),
        shell_dialect=_shell_dialect(),
        timeout_seconds=10,
    )
    _output, status = _wait_for_terminal(process)
    return {
        "backend": status.get("backend"),
        "completed": status.get("is_running") is False,
        "exitCodeObserved": status.get("return_code") == 7,
        "failureClassified": status.get("failure_kind") == "command_failed",
    }


def _timeout_probe(background_process: type, cwd: Path) -> dict[str, Any]:
    process = background_process(
        _python_command("-c", "import time; time.sleep(30)"),
        interactive=False,
        cwd=str(cwd),
        shell_dialect=_shell_dialect(),
        timeout_seconds=0.5,
    )
    process_id = process._process_id()
    _output, status = _wait_for_terminal(process)
    return {
        "backend": status.get("backend"),
        "completed": status.get("is_running") is False,
        "timedOut": status.get("timed_out") is True and status.get("return_code") == 124,
        "deadlineClassified": status.get("failure_kind") == "deadline_exceeded",
        "processTreeStopped": _process_stopped(process_id),
    }


def _interactive_probe(background_process: type, cwd: Path) -> dict[str, Any]:
    marker = "V8OS_PACKAGED_INTERACTIVE_OK"
    process = background_process(
        _python_command("-i", "-q"),
        interactive=True,
        cwd=str(cwd),
        shell_dialect=_shell_dialect(),
        timeout_seconds=20,
    )
    process_id = process._process_id()
    output = ""
    round_trip = False
    status: dict[str, Any] = {}
    try:
        process.write_input(f"print({marker!r})\n")
        deadline = time.time() + 10
        while marker not in output and process.is_running and time.time() < deadline:
            output += process.get_new_output()
            status = dict(process.status_snapshot())
            if marker not in output and marker not in str(status.get("screen_snapshot") or ""):
                time.sleep(0.05)
        output += process.get_new_output()
        status = dict(process.status_snapshot())
        round_trip = marker in output or marker in str(status.get("screen_snapshot") or "")
    finally:
        process.terminate(reason="probe_cleanup")
        process.reader_thread.join(timeout=3)
    expected_backend = "winpty" if sys.platform == "win32" else "posix_pty"
    return {
        "backend": status.get("backend"),
        "backendExpected": status.get("backend") == expected_backend,
        "usesTty": status.get("uses_tty") is True,
        "roundTrip": round_trip,
        "processTreeStopped": _process_stopped(process_id),
    }


def _interactive_exit_probe(background_process: type, cwd: Path) -> dict[str, Any]:
    process = background_process(
        _python_command("-c", "raise SystemExit(7)"),
        interactive=True,
        cwd=str(cwd),
        shell_dialect=_shell_dialect(),
        timeout_seconds=10,
    )
    _output, status = _wait_for_terminal(process)
    expected_backend = "winpty" if sys.platform == "win32" else "posix_pty"
    return {
        "backend": status.get("backend"),
        "backendExpected": status.get("backend") == expected_backend,
        "completed": status.get("is_running") is False,
        "exitCodeObserved": status.get("return_code") == 7,
        "failureClassified": status.get("failure_kind") == "command_failed",
        "timedOut": status.get("timed_out") is True,
    }


def _result_ok(result: dict[str, Any]) -> bool:
    ordinary = dict(result.get("ordinary") or {})
    failure = dict(result.get("failure") or {})
    timeout = dict(result.get("timeout") or {})
    interactive = dict(result.get("interactive") or {})
    interactive_exit = dict(result.get("interactiveExit") or {})
    return bool(
        ordinary.get("backend") == "pipe"
        and ordinary.get("completed")
        and ordinary.get("exitCodeObserved")
        and ordinary.get("outputObserved")
        and failure.get("backend") == "pipe"
        and failure.get("completed")
        and failure.get("exitCodeObserved")
        and failure.get("failureClassified")
        and timeout.get("backend") == "pipe"
        and timeout.get("completed")
        and timeout.get("timedOut")
        and timeout.get("deadlineClassified")
        and timeout.get("processTreeStopped")
        and interactive.get("backendExpected")
        and interactive.get("usesTty")
        and interactive.get("roundTrip")
        and interactive.get("processTreeStopped")
        and interactive_exit.get("backendExpected")
        and interactive_exit.get("completed")
        and interactive_exit.get("exitCodeObserved")
        and interactive_exit.get("failureClassified")
        and interactive_exit.get("timedOut") is False
    )


def main() -> int:
    result: dict[str, Any] = {
        "ok": False,
        "mode": "packaged_command_runtime_probe",
        "ordinary": {},
        "failure": {},
        "timeout": {},
        "interactive": {},
        "interactiveExit": {},
        "error": None,
    }
    try:
        background_process = _load_command_backend(_resolve_engine_root())
        with tempfile.TemporaryDirectory(prefix="v8os-command-runtime-probe-") as temporary:
            cwd = Path(temporary)
            result["ordinary"] = _ordinary_probe(background_process, cwd)
            result["failure"] = _failure_probe(background_process, cwd)
            result["timeout"] = _timeout_probe(background_process, cwd)
            result["interactive"] = _interactive_probe(background_process, cwd)
            result["interactiveExit"] = _interactive_exit_probe(background_process, cwd)
        result["ok"] = _result_ok(result)
        if not result["ok"]:
            result["error"] = "command_runtime_unhealthy"
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        result["error"] = "command_runtime_probe_failed"
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
