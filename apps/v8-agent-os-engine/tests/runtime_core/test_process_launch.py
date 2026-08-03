from __future__ import annotations

import importlib
import subprocess
import sys
import threading

import pytest

from core import process_launch


def test_repeated_windowless_runner_injects_create_no_window(monkeypatch) -> None:
    create_no_window = 0x08000000
    captured: list[dict[str, object]] = []

    monkeypatch.setattr(
        process_launch,
        "windowless_subprocess_kwargs",
        lambda: {"creationflags": create_no_window},
    )

    def fake_run(argv, **kwargs):
        captured.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(process_launch.subprocess, "run", fake_run)

    for _ in range(16):
        process_launch.run_windowless(["ffprobe", "asset.mp4"], capture_output=True)
        process_launch.run_windowless(["ffmpeg", "-i", "asset.mp4", "out.mp4"], capture_output=True)

    assert len(captured) == 32
    assert {kwargs.get("creationflags") for kwargs in captured} == {create_no_window}


def test_windowless_runner_explicitly_wraps_windows_batch_launchers(monkeypatch) -> None:
    original = ["C:/Program Files/nodejs/npm.cmd", "--version"]
    captured: dict[str, object] = {}

    monkeypatch.setattr(process_launch.sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", "C:/Windows/System32/cmd.exe")
    monkeypatch.setattr(
        process_launch,
        "windowless_subprocess_kwargs",
        lambda: {"creationflags": 0x08000000},
    )

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(process_launch.subprocess, "run", fake_run)

    process_launch.run_windowless(original, capture_output=True)

    command_line = str(captured["argv"])
    assert command_line.startswith("C:/Windows/System32/cmd.exe /d /s /c ")
    assert "C:/Program^ Files/nodejs/npm.cmd" in command_line
    assert captured["kwargs"]["executable"] == "C:/Windows/System32/cmd.exe"
    assert captured["kwargs"]["creationflags"] == 0x08000000


def test_windowless_runner_rejects_batch_argument_line_breaks(monkeypatch) -> None:
    monkeypatch.setattr(process_launch.sys, "platform", "win32")
    monkeypatch.setattr(
        process_launch.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("subprocess must not start")),
    )

    with pytest.raises(ValueError, match="cannot contain line breaks"):
        process_launch.run_windowless(["C:/npm/npm.cmd", "safe\r\necho injected"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher contract")
def test_windowless_runner_executes_batch_path_and_preserves_metacharacters(tmp_path) -> None:
    script_root = tmp_path / "batch scripts"
    script_root.mkdir()
    script = script_root / "probe.cmd"
    script.write_text("@echo off\r\necho [%~1]\r\n", encoding="ascii")

    result = process_launch.run_windowless(
        [str(script), "alpha&echo injected"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == '["alpha&echo injected"]'
    assert result.stderr == ""


def test_media_process_entrypoints_share_windowless_runner() -> None:
    creative_runtime = importlib.import_module("runtimes.creative_media.runtime")
    production_pack = importlib.import_module("runtimes.creative_media.production_pack")
    vision_media_analyzer = importlib.import_module("core.tools.vision_media_analyzer")
    host_load = importlib.import_module("core.host_load")
    workspace_state_digest = importlib.import_module("core.workspace_state_digest")
    v8_link = importlib.import_module("core.v8_link")
    system_doctor = importlib.import_module("core.system_doctor")

    assert creative_runtime.run_windowless is process_launch.run_windowless
    assert production_pack.run_windowless is process_launch.run_windowless
    assert vision_media_analyzer.run_windowless is process_launch.run_windowless
    assert host_load.run_windowless is process_launch.run_windowless
    assert workspace_state_digest.run_windowless is process_launch.run_windowless
    assert v8_link.run_windowless is process_launch.run_windowless
    assert system_doctor.run_windowless is process_launch.run_windowless


def test_system_doctor_dependency_probe_uses_windowless_runner(monkeypatch) -> None:
    system_doctor = importlib.import_module("core.system_doctor")
    captured: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(system_doctor.shutil, "which", lambda command: f"C:/tools/{command}.cmd")

    def fake_run(argv, **kwargs):
        captured.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout="v1.0.0\n", stderr="")

    monkeypatch.setattr(system_doctor, "run_windowless", fake_run)

    result = system_doctor._run_version_command(["npm", "--version"])

    assert result == {"ok": True, "output": "v1.0.0", "returnCode": 0}
    assert captured == [
        (
            ["C:/tools/npm.cmd", "--version"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 5.0,
                "check": False,
            },
        )
    ]


def test_system_doctor_runs_independent_checks_concurrently_and_preserves_order(monkeypatch) -> None:
    system_doctor = importlib.import_module("core.system_doctor")
    service = system_doctor.SystemDoctorService()
    checker_names = (
        "_check_paths",
        "_check_ports",
        "_check_dependencies",
        "_check_databases",
        "_check_models",
        "_check_runtimes",
        "_check_extensions",
        "_check_network_supervisor_compat",
        "_check_storage_pressure",
    )
    gate = threading.Barrier(4)
    worker_threads: set[int] = set()

    def fake_checker(name: str, *, wait_for_peers: bool):
        def run():
            if wait_for_peers:
                worker_threads.add(threading.get_ident())
                gate.wait(timeout=2)
            return [{"id": name, "status": "ok", "title": name, "detail": "ok"}]

        return run

    for index, name in enumerate(checker_names):
        monkeypatch.setattr(service, name, fake_checker(name, wait_for_peers=index < 4))

    result = service.run()

    assert len(worker_threads) == 4
    assert [item["id"] for item in result["checks"]] == list(checker_names)


def test_engineering_context_commands_share_windowless_runner(monkeypatch, tmp_path) -> None:
    engineering_service = importlib.import_module("runtimes.engineering.service")
    captured: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        captured.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout="main\n", stderr="")

    monkeypatch.setattr(engineering_service, "run_windowless", fake_run)

    result = engineering_service._run_command(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=tmp_path,
        timeout=3.5,
    )

    assert result == {
        "ok": True,
        "returnCode": 0,
        "stdout": "main",
        "stderr": "",
    }
    assert captured == [
        (
            ["git", "rev-parse", "--show-toplevel"],
            {
                "cwd": str(tmp_path),
                "capture_output": True,
                "text": True,
                "timeout": 3.5,
                "encoding": "utf-8",
                "errors": "replace",
            },
        )
    ]
