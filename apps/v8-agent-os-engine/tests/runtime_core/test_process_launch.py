from __future__ import annotations

import importlib
import subprocess

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


def test_media_process_entrypoints_share_windowless_runner() -> None:
    creative_runtime = importlib.import_module("runtimes.creative_media.runtime")
    production_pack = importlib.import_module("runtimes.creative_media.production_pack")
    vision_media_analyzer = importlib.import_module("core.tools.vision_media_analyzer")
    host_load = importlib.import_module("core.host_load")
    workspace_state_digest = importlib.import_module("core.workspace_state_digest")
    v8_link = importlib.import_module("core.v8_link")

    assert creative_runtime.run_windowless is process_launch.run_windowless
    assert production_pack.run_windowless is process_launch.run_windowless
    assert vision_media_analyzer.run_windowless is process_launch.run_windowless
    assert host_load.run_windowless is process_launch.run_windowless
    assert workspace_state_digest.run_windowless is process_launch.run_windowless
    assert v8_link.run_windowless is process_launch.run_windowless


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
