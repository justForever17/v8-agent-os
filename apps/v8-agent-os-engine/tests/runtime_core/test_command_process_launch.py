from __future__ import annotations

import io
import json
import subprocess

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
    captured: list[dict[str, object]] = []

    monkeypatch.setattr(
        command_module,
        "_windowless_subprocess_kwargs",
        lambda: {"creationflags": create_no_window},
    )

    def fake_run(argv, **kwargs):
        captured.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

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

    assert len(captured) == launch_pairs * 2
    assert {kwargs["creationflags"] for kwargs in captured} == {create_no_window}


def test_windows_command_session_fallback_uses_windowless_kwargs(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(command_module, "HAS_WINPTY", False)
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
    assert captured["creationflags"] == create_no_window
