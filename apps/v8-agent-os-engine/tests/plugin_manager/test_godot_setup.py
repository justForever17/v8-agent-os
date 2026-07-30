from __future__ import annotations

from pathlib import Path

import pytest

from runtimes.plugin_manager import godot_setup


def test_godot_executable_and_project_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / ("Godot_v4.7.1-stable_win64.exe" if godot_setup.os.name == "nt" else "godot")
    executable.write_bytes(b"")
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.godot").write_text("[application]\n", encoding="utf-8")

    monkeypatch.setattr(
        godot_setup.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "4.7.1.stable.official", "stderr": ""},
        )(),
    )

    application = godot_setup.validate_godot_executable(str(executable))
    project_status = godot_setup.validate_godot_project(str(project))

    assert application["state"] == "ready"
    assert application["version"] == "4.7.1"
    assert application["upgradeRecommended"] is False
    assert project_status["state"] == "ready"
    assert project_status["projectFile"] == str((project / "project.godot").resolve())


def test_godot_setup_separates_offline_prerequisites_from_live_editor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / ("godot.exe" if godot_setup.os.name == "nt" else "godot")
    executable.write_bytes(b"")
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.godot").write_text("[application]\n", encoding="utf-8")
    monkeypatch.setattr(
        godot_setup.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "4.6.stable.official", "stderr": ""},
        )(),
    )
    monkeypatch.setattr(
        godot_setup,
        "probe_godot_mcp",
        lambda: {
            "state": "ready",
            "endpoint": godot_setup.GODOT_MCP_URL,
            "serverName": "godot-ai",
            "toolCount": 43,
        },
    )

    unchecked = godot_setup.evaluate_godot_setup(
        {
            "godotExecutable": str(executable),
            "projectPath": str(project),
            "scenario": "2.5d",
        },
        probe_mcp=False,
    )
    assert unchecked["offlinePrerequisitesReady"] is True
    assert unchecked["editorOnline"] is False
    assert unchecked["readyForInstall"] is False
    assert unchecked["steps"]["mcp"]["state"] == "unchecked"

    ready = godot_setup.evaluate_godot_setup(
        {
            "godotExecutable": str(executable),
            "projectPath": str(project),
            "scenario": "2.5d",
        },
        probe_mcp=True,
    )
    assert ready["offlinePrerequisitesReady"] is True
    assert ready["editorOnline"] is True
    assert ready["readyForInstall"] is True


def test_godot_version_below_45_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / ("godot.exe" if godot_setup.os.name == "nt" else "godot")
    executable.write_bytes(b"")
    monkeypatch.setattr(
        godot_setup.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "4.4.2.stable.official", "stderr": ""},
        )(),
    )

    result = godot_setup.validate_godot_executable(str(executable))

    assert result["state"] == "invalid"
    assert result["detail"] == "godot_version_too_old"
    assert result["minimumVersion"] == "4.5"
