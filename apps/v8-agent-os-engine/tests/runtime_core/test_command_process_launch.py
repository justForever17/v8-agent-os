from __future__ import annotations

import asyncio
import io
import json
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
from fastapi import HTTPException

from api import ops_routes
from core.tools.native import command as command_module


def _allow_command_launch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(command_module, "get_runtime_context", lambda: {})
    monkeypatch.setattr(
        command_module,
        "preflight_command_workspace",
        lambda *_args, **_kwargs: {"ok": True, "cwd": str(tmp_path), "binding": {}},
    )
    monkeypatch.setattr(command_module.safety_guardian, "assess_system_command", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(command_module.safety_guardian, "observe_post_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(command_module, "_enforce_safety_decision", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(command_module, "_sandbox_launch", lambda _context, argv: (list(argv), None))
    monkeypatch.setattr(command_module, "mark_workspace_state_stale", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(command_module, "_resolve_shell_dialect", lambda *_args, **_kwargs: "sh")
    monkeypatch.setattr(command_module, "_shell_command_argv", lambda command, _dialect: ["sh", "-c", command])
    monkeypatch.setattr(command_module, "_windows_shell_syntax_violation_payload", lambda *_args, **_kwargs: None)


def _allow_native_command_launch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(command_module, "get_runtime_context", lambda: {})
    monkeypatch.setattr(
        command_module,
        "preflight_command_workspace",
        lambda *_args, **_kwargs: {"ok": True, "cwd": str(tmp_path), "binding": {}},
    )
    monkeypatch.setattr(command_module.safety_guardian, "assess_system_command", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(command_module.safety_guardian, "observe_post_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(command_module, "_enforce_safety_decision", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(command_module, "_sandbox_launch", lambda _context, argv: (list(argv), None))
    monkeypatch.setattr(command_module, "mark_workspace_state_stale", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(command_module, "_windows_shell_syntax_violation_payload", lambda *_args, **_kwargs: None)


def _observe_until_terminal(session_id: str, *, timeout_seconds: float = 10) -> tuple[dict, list[dict]]:
    observations: list[dict] = []
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        payload = json.loads(
            command_module.command_session_broker.func(
                mode="observe",
                session_id=session_id,
            )
        )
        observations.append(payload)
        if payload.get("state") in {"completed", "failed", "timed_out", "terminated"}:
            return payload, observations
        time.sleep(0.05)
    raise AssertionError(f"command session did not reach a terminal state: {observations[-1] if observations else {}}")


def _terminate_test_session(session_id: str) -> None:
    process = command_module._bg_processes.get(session_id)
    if process is not None and process.is_running:
        command_module.command_session_broker.func(mode="terminate", session_id=session_id)
    command_module._bg_processes.pop(session_id, None)


def test_windowless_subprocess_kwargs_are_windows_only(monkeypatch) -> None:
    create_no_window = 0x08000000
    monkeypatch.setattr(command_module.subprocess, "CREATE_NO_WINDOW", create_no_window, raising=False)

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    assert command_module._windowless_subprocess_kwargs() == {"creationflags": create_no_window}

    monkeypatch.setattr(command_module.sys, "platform", "linux")
    assert command_module._windowless_subprocess_kwargs() == {}


def test_repeated_sync_system_and_skill_launches_share_windowless_kwargs(monkeypatch, tmp_path) -> None:
    _allow_command_launch(monkeypatch, tmp_path)
    create_no_window = 0x08000000
    captured_runs: list[dict[str, object]] = []
    captured_processes: list[dict[str, object]] = []

    monkeypatch.setattr(
        command_module,
        "_windowless_subprocess_kwargs",
        lambda: {"creationflags": create_no_window},
    )

    def fake_run(argv, **kwargs):
        captured_runs.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b"ok\n", stderr=b"")

    class FakeProcess:
        returncode = 0
        pid = 424242

        @staticmethod
        def communicate(*, timeout):
            assert timeout == 90
            return b"ok\n", b""

    def fake_popen(argv, **kwargs):
        captured_processes.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)
    monkeypatch.setattr(command_module.subprocess, "Popen", fake_popen)

    launch_pairs = 16
    for _ in range(launch_pairs):
        skill_result = command_module.execute_governed_argv(
            ["python", "script.py"],
            cwd=str(tmp_path),
            action_family="skill_script",
        )
        system_result = json.loads(
            command_module.run_system_command.func(
                command="echo ok",
                mode="sync",
                shell_dialect="sh",
            )
        )
        assert skill_result["ok"] is True
        assert system_result["ok"] is True

    assert len(captured_runs) == launch_pairs
    assert len(captured_processes) == launch_pairs
    assert {kwargs["creationflags"] for kwargs in [*captured_runs, *captured_processes]} == {create_no_window}


def test_windows_noninteractive_command_session_always_uses_pipe(monkeypatch, tmp_path) -> None:
    create_no_window = 0x08000000
    captured: dict[str, object] = {}

    class FakeProcess:
        stdout = io.StringIO("")
        stdin = None

        @staticmethod
        def poll() -> int:
            return 0

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module, "HAS_WINPTY", True)
    monkeypatch.setattr(
        command_module,
        "PTY",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("non-interactive command used WinPTY")),
    )
    monkeypatch.setattr(command_module, "_resolve_shell_dialect", lambda *_args, **_kwargs: "cmd")
    monkeypatch.setattr(command_module, "_shell_command_argv", lambda command, _dialect: ["cmd.exe", "/c", command])
    monkeypatch.setattr(command_module, "_sandbox_launch", lambda _context, argv: (list(argv), None))
    monkeypatch.setattr(command_module, "_build_command_diagnostics_snapshot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        command_module,
        "_windowless_subprocess_kwargs",
        lambda: {"creationflags": create_no_window},
    )
    monkeypatch.setattr(command_module.subprocess, "Popen", fake_popen)

    process = command_module.BackgroundProcess("echo ok", cwd=str(tmp_path), shell_dialect="cmd")
    process.reader_thread.join(timeout=1)

    assert process.return_code == 0
    assert process.backend == "pipe"
    assert process.uses_tty is False
    assert captured["creationflags"] == create_no_window


@pytest.mark.skipif(sys.platform != "win32", reason="requires cmd.exe quoting semantics")
def test_windows_noninteractive_cmd_preserves_nested_quotes_and_exit_code(tmp_path) -> None:
    command = subprocess.list2cmdline(
        [sys.executable, "-I", "-u", "-c", "print('V8OS_CMD_QUOTES_OK'); raise SystemExit(7)"]
    )
    process = command_module.BackgroundProcess(
        command,
        interactive=False,
        cwd=str(tmp_path),
        shell_dialect="cmd",
        timeout_seconds=5,
    )
    process.reader_thread.join(timeout=5)

    output = process.get_new_output()
    status = process.status_snapshot()
    assert "V8OS_CMD_QUOTES_OK" in output
    assert status["backend"] == "pipe"
    assert status["return_code"] == 7
    assert status["failure_kind"] == "command_failed"


@pytest.mark.skipif(sys.platform != "win32", reason="requires cmd.exe quoting semantics")
def test_windows_sync_cmd_preserves_nested_quotes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(command_module, "get_runtime_context", lambda: {})
    monkeypatch.setattr(
        command_module,
        "preflight_command_workspace",
        lambda *_args, **_kwargs: {"ok": True, "cwd": str(tmp_path), "binding": {}},
    )
    monkeypatch.setattr(command_module.safety_guardian, "assess_system_command", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(command_module.safety_guardian, "observe_post_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(command_module, "_enforce_safety_decision", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(command_module, "mark_workspace_state_stale", lambda *_args, **_kwargs: None)
    command = subprocess.list2cmdline(
        [sys.executable, "-I", "-u", "-c", "print('V8OS_SYNC_CMD_QUOTES_OK')"]
    )

    payload = json.loads(
        command_module.execute_system_command.func(
            command=command,
            cwd=str(tmp_path),
            shell_dialect="cmd",
        )
    )

    assert payload["ok"] is True
    assert payload["returnCode"] == 0
    assert "V8OS_SYNC_CMD_QUOTES_OK" in payload["keyOutput"]


def test_windows_cmd_keeps_structured_argv_inside_governed_sandbox(monkeypatch) -> None:
    command = 'python -c "print(\'sandboxed\')"'
    base_argv = ["cmd.exe", "/d", "/s", "/c", command]
    context = {"sandbox_policy": {"leaseId": "lease_test"}}
    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module, "_shell_command_argv", lambda *_args: list(base_argv))
    monkeypatch.setattr(command_module, "_sandbox_launch", lambda _context, argv: (list(argv), {"V8_TEST": "1"}))

    argv, environment = command_module._shell_subprocess_launch(command, "cmd", context)

    assert argv == base_argv
    assert isinstance(argv, list)
    assert environment == {"V8_TEST": "1"}


def test_sync_timeout_terminates_process_tree(monkeypatch, tmp_path) -> None:
    _allow_command_launch(monkeypatch, tmp_path)
    terminated: list[int] = []

    class FakeProcess:
        pid = 515151
        returncode = None
        calls = 0

        def communicate(self, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="echo slow", timeout=timeout)
            return b"", b""

    monkeypatch.setattr(command_module.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(command_module, "_terminate_process_tree", lambda pid: terminated.append(pid))

    result = json.loads(
        command_module.run_system_command.func(
            command="echo slow",
            mode="sync",
            shell_dialect="sh",
        )
    )

    assert result["toolExecution"]["failureClass"] == "deadline_exceeded"
    assert terminated == [515151]


def test_windows_interactive_backend_panic_is_normalized(monkeypatch, tmp_path) -> None:
    class NativePanic(BaseException):
        pass

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module, "HAS_WINPTY", True)
    monkeypatch.setattr(
        command_module,
        "PTY",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(NativePanic("conpty path not found")),
    )
    monkeypatch.setattr(command_module, "_resolve_shell_dialect", lambda *_args, **_kwargs: "cmd")
    monkeypatch.setattr(command_module, "_build_command_diagnostics_snapshot", lambda *_args, **_kwargs: {})

    with pytest.raises(command_module.CommandSessionBackendError) as caught:
        command_module.BackgroundProcess(
            "python",
            interactive=True,
            cwd=str(tmp_path),
            shell_dialect="cmd",
        )

    assert caught.value.backend == "winpty"
    assert caught.value.operation == "initialize"
    assert "NativePanic" in str(caught.value)


@pytest.mark.skipif(
    sys.platform != "win32" or not command_module.HAS_WINPTY,
    reason="requires the real Windows WinPTY backend",
)
def test_windows_interactive_backend_round_trip_uses_real_winpty(tmp_path) -> None:
    marker = "V8OS_REAL_WINPTY_ROUND_TRIP"
    command = subprocess.list2cmdline([sys.executable, "-I", "-u", "-i", "-q"])
    process = command_module.BackgroundProcess(
        command,
        interactive=True,
        cwd=str(tmp_path),
        shell_dialect="cmd",
        timeout_seconds=20,
    )
    process_id = process._process_id()
    output = ""
    try:
        process.write_input(f"print({marker!r})\n")
        deadline = time.time() + 10
        while marker not in output and process.is_running and time.time() < deadline:
            output += process.get_new_output()
            if marker not in output:
                time.sleep(0.05)
        output += process.get_new_output()
        status = process.status_snapshot()
        assert status["backend"] == "winpty"
        assert status["uses_tty"] is True
        assert marker in output or marker in str(status["screen_snapshot"])
    finally:
        process.terminate(reason="test_cleanup")
        process.reader_thread.join(timeout=5)

    assert process_id is not None
    deadline = time.time() + 5
    while psutil.pid_exists(process_id) and time.time() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(process_id)


@pytest.mark.skipif(
    sys.platform != "win32" or not command_module.HAS_WINPTY,
    reason="requires the real Windows WinPTY backend",
)
@pytest.mark.parametrize("return_code", [0, 7])
def test_windows_finite_interactive_command_reports_exact_exit(tmp_path, return_code) -> None:
    marker = f"V8OS_REAL_WINPTY_EXIT_{return_code}"
    command = subprocess.list2cmdline(
        [
            sys.executable,
            "-I",
            "-u",
            "-c",
            f"print({marker!r}); raise SystemExit({return_code})",
        ]
    )
    process = command_module.BackgroundProcess(
        command,
        interactive=True,
        cwd=str(tmp_path),
        shell_dialect="cmd",
        timeout_seconds=20,
    )
    process.reader_thread.join(timeout=10)

    output = process.get_new_output()
    status = process.status_snapshot()
    assert process.reader_thread.is_alive() is False
    assert status["is_running"] is False
    assert status["return_code"] == return_code
    assert status["failure_kind"] == (None if return_code == 0 else "command_failed")
    assert marker in output or marker in str(status["screen_snapshot"])


def test_command_diagnostics_probe_cannot_block_process_start(monkeypatch, tmp_path) -> None:
    original_builder = command_module._build_command_diagnostics_snapshot

    def slow_builder(*args, **kwargs):
        if kwargs.get("probe_executables"):
            time.sleep(3)
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(command_module, "_build_command_diagnostics_snapshot", slow_builder)
    started_at = time.monotonic()
    process = command_module.BackgroundProcess(
        shlex.join([sys.executable, "-I", "-u", "-c", "print('V8OS_DIAGNOSTICS_NONBLOCKING')"])
        if sys.platform != "win32"
        else subprocess.list2cmdline([sys.executable, "-I", "-u", "-c", "print('V8OS_DIAGNOSTICS_NONBLOCKING')"]),
        cwd=str(tmp_path),
        shell_dialect="sh" if sys.platform != "win32" else "cmd",
        timeout_seconds=10,
    )
    assert time.monotonic() - started_at < 1
    process.reader_thread.join(timeout=5)
    assert process.status_snapshot()["return_code"] == 0
    assert process.status_snapshot()["command_diagnostics"]["diagnosticsState"] == "deferred"


@pytest.mark.skipif(
    sys.platform != "win32" or not command_module.HAS_WINPTY,
    reason="requires the real Windows WinPTY backend",
)
def test_windows_missing_interactive_command_fails_without_waiting_for_deadline(tmp_path) -> None:
    process = command_module.BackgroundProcess(
        "v8os-command-that-does-not-exist-43",
        interactive=True,
        cwd=str(tmp_path),
        shell_dialect="cmd",
        timeout_seconds=20,
    )
    process.reader_thread.join(timeout=10)

    status = process.status_snapshot()
    assert process.reader_thread.is_alive() is False
    assert status["is_running"] is False
    assert status["return_code"] not in (None, 0)
    assert status["failure_kind"] == "command_failed"
    assert status["timed_out"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX PTY backend")
@pytest.mark.parametrize("return_code", [0, 7])
def test_posix_finite_interactive_command_reports_exact_exit(tmp_path, return_code) -> None:
    marker = f"V8OS_POSIX_PTY_EXIT_{return_code}"
    command = shlex.join(
        [
            sys.executable,
            "-I",
            "-u",
            "-c",
            f"print({marker!r}); raise SystemExit({return_code})",
        ]
    )
    process = command_module.BackgroundProcess(
        command,
        interactive=True,
        cwd=str(tmp_path),
        shell_dialect="sh",
        timeout_seconds=10,
    )
    process.reader_thread.join(timeout=5)

    output = process.get_new_output()
    status = process.status_snapshot()
    assert process.reader_thread.is_alive() is False
    assert status["is_running"] is False
    assert status["return_code"] == return_code
    assert status["failure_kind"] == (None if return_code == 0 else "command_failed")
    assert marker in output
    assert process.fd is None


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX PTY backend")
def test_posix_interactive_round_trip_and_termination_close_master(tmp_path) -> None:
    marker = "V8OS_POSIX_PTY_ROUND_TRIP"
    command = shlex.join([sys.executable, "-I", "-u", "-i", "-q"])
    process = command_module.BackgroundProcess(
        command,
        interactive=True,
        cwd=str(tmp_path),
        shell_dialect="sh",
        timeout_seconds=10,
    )
    process_id = process._process_id()
    output = ""
    try:
        process.write_input(f"print({marker!r})\n")
        deadline = time.time() + 5
        while marker not in output and process.is_running and time.time() < deadline:
            output += process.get_new_output()
            if marker not in output:
                time.sleep(0.05)
        output += process.get_new_output()
    finally:
        process.terminate(reason="test_cleanup")
        process.reader_thread.join(timeout=5)

    assert marker in output or marker in str(process.status_snapshot()["screen_snapshot"])
    assert process.reader_thread.is_alive() is False
    assert process.fd is None
    assert process_id is not None
    deadline = time.time() + 5
    while psutil.pid_exists(process_id) and time.time() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(process_id)


def test_windows_interactive_spawn_panic_cleans_spawned_process(monkeypatch, tmp_path) -> None:
    class NativePanic(BaseException):
        pass

    class FakePty:
        pid = 616161

        def spawn(self, _command, **_kwargs):
            raise NativePanic("direct command spawn failed")

        def cancel_io(self):
            return None

    terminated: list[int] = []
    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module, "HAS_WINPTY", True)
    monkeypatch.setattr(command_module, "PTY", lambda *_args, **_kwargs: FakePty())
    monkeypatch.setattr(command_module, "_resolve_shell_dialect", lambda *_args, **_kwargs: "cmd")
    monkeypatch.setattr(command_module, "_sandbox_launch", lambda _context, argv: (list(argv), None))
    monkeypatch.setattr(command_module, "_build_command_diagnostics_snapshot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(command_module, "_terminate_process_tree", lambda pid: terminated.append(pid))

    with pytest.raises(command_module.CommandSessionBackendError) as caught:
        command_module.BackgroundProcess(
            "python",
            interactive=True,
            cwd=str(tmp_path),
            shell_dialect="cmd",
        )

    assert caught.value.operation == "spawn"
    assert terminated == [616161]


def test_command_session_read_backend_failure_terminates_process_tree(monkeypatch) -> None:
    class NativePanic(BaseException):
        pass

    process = object.__new__(command_module.BackgroundProcess)
    process.backend = "winpty"
    process.failure_kind = None
    process.failure_message = None
    terminated: list[tuple[str, int]] = []
    process.terminate = lambda *, reason, return_code: terminated.append((reason, return_code))

    process._mark_backend_failure(NativePanic("conpty read failed"), operation="read")

    assert process.failure_kind == "command_session_backend_failure"
    assert "NativePanic" in str(process.failure_message)
    assert terminated == [("backend_failure", 1)]


def test_python_pip_install_is_observable_but_not_interactive() -> None:
    command = "python -m pip install python-docx --index-url https://pypi.org/simple"

    assert command_module._detect_session_preferred_command(command)
    assert command_module._detect_interactive_command(command) is None


def test_explicit_pipe_rejects_known_interactive_command() -> None:
    with pytest.raises(RuntimeError) as caught:
        command_module._launch_background_command("python", terminal_mode="pipe")

    payload = json.loads(str(caught.value))
    assert payload["ok"] is False
    assert payload["kind"] == "command_terminal_mode_conflict"
    assert payload["terminalMode"] == "pipe"
    assert payload["recommendedNextAction"]


def test_explicit_pty_forces_terminal_backend_for_unrecognized_command(monkeypatch, tmp_path) -> None:
    _allow_command_launch(monkeypatch, tmp_path)
    monkeypatch.setattr(command_module.safety_guardian, "assess_background_command", lambda *_args, **_kwargs: object())
    captured: dict[str, object] = {}

    class FakeBackgroundProcess:
        uses_tty = True
        backend = "test_pty"
        timeout_seconds = 12.0
        chat_cli_variant = ""
        is_running = False

        def __init__(self, _command, **kwargs):
            captured.update(kwargs)
            self.command_id = ""
            self.workspace_binding = {}

        @staticmethod
        def get_new_output() -> str:
            return ""

        @staticmethod
        def status_snapshot() -> dict[str, object]:
            return {
                "is_running": False,
                "interactive": True,
                "terminal_mode": "pty",
                "resolved_terminal_mode": "pty",
                "backend": "test_pty",
                "return_code": 0,
            }

    monkeypatch.setattr(command_module, "BackgroundProcess", FakeBackgroundProcess)

    launched = command_module._launch_background_command(
        "v8os-custom-repl",
        terminal_mode="pty",
        timeout_seconds=12,
    )

    assert captured["interactive"] is True
    assert captured["terminal_mode"] == "pty"
    assert launched["terminalMode"] == "pty"
    assert launched["resolvedTerminalMode"] == "pty"


def test_command_session_input_rejects_pipe_without_writing(monkeypatch) -> None:
    writes: list[str] = []

    class FakePipeProcess:
        is_running = True
        completed_at = None

        @staticmethod
        def status_snapshot() -> dict[str, object]:
            return {
                "command": "python -c \"print('done')\"",
                "is_running": True,
                "interactive": False,
                "terminal_mode": "auto",
                "resolved_terminal_mode": "pipe",
                "backend": "pipe",
                "return_code": None,
            }

        @staticmethod
        def write_input(value: str) -> None:
            writes.append(value)

    monkeypatch.setattr(command_module, "get_runtime_context", lambda: {})
    command_module._bg_processes["pipe-session"] = FakePipeProcess()
    try:
        payload = json.loads(
            command_module.command_session_broker.func(
                mode="input",
                session_id="pipe-session",
                input_text="should-not-land",
            )
        )
    finally:
        command_module._bg_processes.pop("pipe-session", None)

    assert payload["ok"] is False
    assert payload["kind"] == "command_session_not_interactive"
    assert payload["error"] == "command_session_not_interactive"
    assert payload["resolvedTerminalMode"] == "pipe"
    assert payload["recommendedNextAction"] == "terminate_then_restart_with_pty"
    assert writes == []


def test_background_process_rejects_direct_pipe_input() -> None:
    process = object.__new__(command_module.BackgroundProcess)
    process.interactive = False
    process.terminal_mode = "auto"
    process.resolved_terminal_mode = "pipe"
    process.backend = "pipe"

    with pytest.raises(RuntimeError) as caught:
        process.write_input("must-not-land")

    payload = json.loads(str(caught.value))
    assert payload["kind"] == "command_session_not_interactive"
    assert payload["resolvedTerminalMode"] == "pipe"


def test_http_terminal_input_does_not_wrap_rejection_as_success(monkeypatch) -> None:
    from core import native_tools

    rejection = {
        "ok": False,
        "kind": "command_session_not_interactive",
        "error": "command_session_not_interactive",
        "summary": "pipe sessions do not accept input",
    }
    monkeypatch.setattr(
        native_tools,
        "send_background_input",
        SimpleNamespace(invoke=lambda *_args, **_kwargs: json.dumps(rejection)),
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            ops_routes.send_bg_process_input(
                "pipe-session",
                ops_routes.TerminalInputRequest(input_text="must-not-land"),
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == rejection


def test_run_system_command_preserves_terminal_mode_conflict(monkeypatch) -> None:
    monkeypatch.setattr(command_module, "_engineering_command_scope_block", lambda *_args, **_kwargs: None)

    payload = json.loads(
        command_module.run_system_command.func(
            command="python",
            mode="session",
            terminal_mode="pipe",
        )
    )

    assert payload["ok"] is False
    assert payload["kind"] == "command_terminal_mode_conflict"
    assert payload["terminalMode"] == "pipe"


def test_auto_install_session_reports_immediate_failure(monkeypatch) -> None:
    command = "python -m pip install python-docx --index-url https://pypi.org/simple"

    def fake_launch(*_args, **_kwargs):
        return {
            "commandId": "pip-failed",
            "runId": "run-pip-failed",
            "interactive": False,
            "observableSession": True,
            "profile": "shell",
            "shellDialect": "cmd",
            "cwd": "C:\\workspace",
            "initialOutput": "ERROR: package install failed",
            "status": {
                "is_running": False,
                "interactive": False,
                "return_code": 1,
                "backend": "pipe",
                "failure_kind": "command_failed",
                "failure_message": "Command exited with code 1.",
            },
        }

    monkeypatch.setattr(command_module, "_engineering_command_scope_block", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        command_module,
        "_compat_native_attr",
        lambda name, local_value=None: fake_launch if name == "_launch_background_command" else local_value,
    )

    payload = json.loads(command_module.run_system_command.func(command=command, mode="auto"))

    assert payload["ok"] is False
    assert payload["state"] == "failed"
    assert payload["failureKind"] == "command_failed"
    assert payload["recommendedNextAction"] == "none"
    assert payload["summary"] == "命令会话已异常结束。"
    assert payload["finalPreview"] == "ERROR: package install failed"


def test_noninteractive_nonzero_exit_is_a_failed_session(tmp_path) -> None:
    shell_dialect = "cmd" if sys.platform == "win32" else "sh"
    command = "exit /b 7" if sys.platform == "win32" else "exit 7"
    process = command_module.BackgroundProcess(
        command,
        interactive=False,
        cwd=str(tmp_path),
        shell_dialect=shell_dialect,
        timeout_seconds=5,
    )
    process.reader_thread.join(timeout=5)

    status = process.status_snapshot()
    assert status["is_running"] is False
    assert status["return_code"] == 7
    assert status["failure_kind"] == "command_failed"
    assert status["failure_message"] == "Command exited with code 7."
    assert command_module._command_session_state_from_status(status) == "failed"


def test_noninteractive_command_session_deadline_kills_process_tree(tmp_path) -> None:
    shell_dialect = "cmd" if sys.platform == "win32" else "sh"
    command = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(30)"])
    process = command_module.BackgroundProcess(
        command,
        interactive=False,
        cwd=str(tmp_path),
        shell_dialect=shell_dialect,
        timeout_seconds=0.25,
    )
    process_id = process._process_id()
    deadline = time.time() + 8
    while process.is_running and time.time() < deadline:
        time.sleep(0.05)
    process.deadline_thread.join(timeout=5)
    process.reader_thread.join(timeout=3)

    status = process.status_snapshot()
    assert process_id is not None
    assert status["is_running"] is False
    assert status["timed_out"] is True
    assert status["failure_kind"] == "deadline_exceeded"
    assert status["termination_reason"] == "deadline_exceeded"
    assert status["return_code"] == 124
    assert command_module._command_session_state_from_status(status) == "timed_out"
    assert not psutil.pid_exists(process_id)


def test_public_run_system_command_sync_executes_real_process(monkeypatch, tmp_path) -> None:
    _allow_native_command_launch(monkeypatch, tmp_path)
    shell_dialect = "cmd" if sys.platform == "win32" else "sh"
    command = subprocess.list2cmdline([sys.executable, "-c", "print('V8_SYNC_OK')"])

    payload = json.loads(
        command_module.run_system_command.func(
            command=command,
            mode="sync",
            cwd=str(tmp_path),
            shell_dialect=shell_dialect,
        )
    )

    assert payload["ok"] is True
    assert payload["kind"] == "command_result"
    assert payload["returnCode"] == 0
    assert "V8_SYNC_OK" in payload["keyOutput"]


def test_public_pipe_session_reaches_real_terminal_result(monkeypatch, tmp_path) -> None:
    _allow_native_command_launch(monkeypatch, tmp_path)
    shell_dialect = "cmd" if sys.platform == "win32" else "sh"
    command = subprocess.list2cmdline(
        [sys.executable, "-u", "-c", "import time; print('V8_PIPE_START', flush=True); time.sleep(0.2); print('V8_PIPE_DONE', flush=True)"]
    )
    session_id = ""
    try:
        started = json.loads(
            command_module.run_system_command.func(
                command=command,
                mode="session",
                terminal_mode="pipe",
                timeout_seconds=5,
                cwd=str(tmp_path),
                shell_dialect=shell_dialect,
            )
        )
        session_id = str(started.get("sessionId") or "")
        assert session_id
        assert started["observableSession"] is True
        assert started["resolvedTerminalMode"] == "pipe"

        terminal, observations = _observe_until_terminal(session_id)
        visible_output = "\n".join(
            str(item.get(key) or "")
            for item in [started, *observations]
            for key in ("initialPreview", "deltaText", "finalPreview")
        )
        assert terminal["state"] == "completed"
        assert terminal["returnCode"] == 0
        assert terminal["recommendedNextAction"] == "none"
        assert "V8_PIPE_START" in visible_output
        assert "V8_PIPE_DONE" in visible_output
    finally:
        if session_id:
            _terminate_test_session(session_id)


@pytest.mark.skipif(sys.platform != "win32" or not command_module.HAS_WINPTY, reason="real WinPTY contract requires Windows and pywinpty")
def test_public_pty_session_accepts_real_input_and_exits(monkeypatch, tmp_path) -> None:
    _allow_native_command_launch(monkeypatch, tmp_path)
    command = subprocess.list2cmdline(
        [
            sys.executable,
            "-u",
            "-c",
            "print('V8_PTY_READY', flush=True); value=input(); print('V8_PTY_ECHO:'+value, flush=True)",
        ]
    )
    session_id = ""
    try:
        started = json.loads(
            command_module.run_system_command.func(
                command=command,
                mode="session",
                terminal_mode="pty",
                timeout_seconds=10,
                cwd=str(tmp_path),
                shell_dialect="cmd",
            )
        )
        session_id = str(started.get("sessionId") or "")
        assert session_id
        assert started["interactive"] is True
        assert started["resolvedTerminalMode"] == "pty"

        ready_observations: list[dict] = []
        ready_deadline = time.time() + 8
        while time.time() < ready_deadline:
            observed = json.loads(command_module.command_session_broker.func(mode="observe", session_id=session_id))
            ready_observations.append(observed)
            ready_text = "\n".join(str(observed.get(key) or "") for key in ("deltaText", "outputPreview", "finalPreview"))
            if "V8_PTY_READY" in ready_text or observed.get("awaitingInput"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"PTY session did not become ready: {ready_observations[-1] if ready_observations else {}}")

        input_result = json.loads(
            command_module.command_session_broker.func(
                mode="input",
                session_id=session_id,
                input_text="hello-from-pty",
                submit=True,
            )
        )
        assert input_result["ok"] is True
        assert input_result["submittedEnter"] is True

        terminal, observations = _observe_until_terminal(session_id)
        visible_output = "\n".join(
            str(item.get(key) or "")
            for item in [input_result, *observations]
            for key in ("deltaText", "semanticTextTail", "finalPreview")
        )
        assert terminal["state"] == "completed"
        assert terminal["returnCode"] == 0
        assert "V8_PTY_ECHO:hello-from-pty" in visible_output
    finally:
        if session_id:
            _terminate_test_session(session_id)
