from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from runtimes.rpa.robot_adapter import RobotFrameworkAdapter


def test_robot_command_uses_receipt_governed_target_in_isolated_child(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "pack"
    target.mkdir()
    (target / "robot.py").write_text(
        "import json,sys\nprint('V8OS_FAKE_ROBOT=' + json.dumps(sys.argv))\n",
        encoding="utf-8",
    )
    robot_file = tmp_path / "workflow.robot"
    robot_file.write_text("*** Test Cases ***\nNoop\n    No Operation\n", encoding="utf-8")

    adapter = RobotFrameworkAdapter()
    monkeypatch.setattr(adapter, "_rpa_target_dir", lambda: target)
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    command = adapter.build_command(robot_file=robot_file, dry_run=True)

    assert command[:3] == [sys.executable, "-I", "-c"]
    assert command[4] == str(target)
    assert command[5] == str(adapter._engine_root())
    assert "-m" not in command[:6]
    result = adapter.run_command(command=command, timeout_ms=10_000)
    assert result["returncode"] == 0
    payload = json.loads(result["stdout"].split("V8OS_FAKE_ROBOT=", 1)[1])
    assert "--dryrun" in payload
    assert payload[-1] == str(robot_file)


def test_robot_child_environment_drops_credentials_and_python_injection(monkeypatch, tmp_path: Path) -> None:
    adapter = RobotFrameworkAdapter()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("V8OS_TEST_TOKEN", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:must-not-leak@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://:must-not-leak@localhost:6379")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    monkeypatch.setenv("XAUTHORITY", str(tmp_path / "Xauthority"))
    monkeypatch.setenv("V8_AGENT_OS_HOME", str(tmp_path / "home"))

    environment = adapter._robot_child_environment(tmp_path)

    assert "OPENAI_API_KEY" not in environment
    assert "V8OS_TEST_TOKEN" not in environment
    assert "DATABASE_URL" not in environment
    assert "REDIS_URL" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["XAUTHORITY"] == str(tmp_path / "Xauthority")
    assert environment["V8_AGENT_OS_HOME"] == str(tmp_path / "home")
    assert environment["V8_AGENT_OS_RPA_TARGET"] == str(tmp_path)


def test_rpa_target_and_availability_are_cached_for_one_engine_boot(monkeypatch, tmp_path: Path) -> None:
    from core.runtime import feature_packs
    from core.storage import storage

    target = tmp_path / "pack"
    target.mkdir()
    status_calls = 0

    def statuses(_registry):
        nonlocal status_calls
        status_calls += 1
        return [{
            "id": "rpa_automation",
            "status": "installed",
            "restartRequired": False,
            "targetDir": str(target),
        }]

    monkeypatch.setattr(feature_packs, "build_feature_pack_statuses", statuses)
    monkeypatch.setattr(storage, "get_runtime_registry_config", lambda: {})
    adapter = RobotFrameworkAdapter()
    assert adapter._rpa_target_dir() == target.resolve()
    assert adapter._rpa_target_dir() == target.resolve()
    assert status_calls == 1

    probe_calls = []
    monkeypatch.setattr(
        adapter,
        "_probe_availability_in_child",
        lambda resolved_target: probe_calls.append(resolved_target) or {
            name: {
                "detected": True,
                "importable": True,
                "origin": str(resolved_target / name),
                "error": None,
            }
            for name in ("robot", "RPA", "RPA.Windows", "RPA.Browser.Selenium", "RPA.Excel.Files")
        },
    )
    first = adapter.availability()
    first["libraries"]["RPA.Excel.Files"] = False
    second = adapter.availability()
    assert second["libraries"]["RPA.Excel.Files"] is True
    assert probe_calls == [target.resolve()]


def test_availability_imports_feature_pack_modules_only_in_the_isolated_child(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "pack"
    for module_path in (
        "robot.py",
        "RPA/__init__.py",
        "RPA/Windows.py",
        "RPA/Browser/__init__.py",
        "RPA/Browser/Selenium.py",
        "RPA/Excel/__init__.py",
        "RPA/Excel/Files.py",
    ):
        candidate = target / module_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("READY = True\n", encoding="utf-8")

    adapter = RobotFrameworkAdapter()
    monkeypatch.setattr(adapter, "_rpa_target_dir", lambda: target)
    module_names = {"robot", "RPA", "RPA.Windows", "RPA.Browser.Selenium", "RPA.Excel.Files"}
    before = {name for name in module_names if name in sys.modules}

    payload = adapter.availability()

    assert payload["robotFramework"] is True
    assert payload["rpaFramework"] is True
    assert all(payload["libraries"].values())
    assert {name for name in module_names if name in sys.modules} == before


def test_transient_availability_probe_failure_is_retried(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "pack"
    target.mkdir()
    adapter = RobotFrameworkAdapter()
    monkeypatch.setattr(adapter, "_rpa_target_dir", lambda: target)
    calls = 0

    def probe(resolved_target: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("cold probe")
        return {
            name: {
                "detected": True,
                "importable": True,
                "origin": str(resolved_target / name),
                "error": None,
            }
            for name in ("robot", "RPA", "RPA.Windows", "RPA.Browser.Selenium", "RPA.Excel.Files")
        }

    monkeypatch.setattr(adapter, "_probe_availability_in_child", probe)
    assert adapter.availability()["robotFramework"] is False
    assert adapter._availability_cache_expires_at is not None
    adapter._availability_cache_expires_at = 0
    assert adapter.availability()["robotFramework"] is True
    assert calls == 2


def test_robot_command_rejects_unready_feature_pack(monkeypatch, tmp_path: Path) -> None:
    adapter = RobotFrameworkAdapter()
    monkeypatch.setattr(adapter, "_rpa_target_dir", lambda: None)

    assert adapter.is_available() is False
    with pytest.raises(RuntimeError, match="能力包"):
        adapter.build_command(robot_file=tmp_path / "workflow.robot")


def test_existing_robot_flow_must_pass_isolated_dependency_dry_run(monkeypatch, tmp_path: Path) -> None:
    robot_file = tmp_path / "unsupported.robot"
    robot_file.write_text(
        "*** Settings ***\nLibrary    RPA.PDF\n\n*** Tasks ***\nNoop\n    No Operation\n",
        encoding="utf-8",
    )
    adapter = RobotFrameworkAdapter()
    monkeypatch.setattr(
        adapter,
        "validate_robot_file",
        lambda **_kwargs: {"passed": False, "error": "must-not-leak"},
    )

    with pytest.raises(ValueError, match=r"Browser\.Selenium、Excel\.Files") as error:
        adapter.prepare_existing_run(robot_file=robot_file)

    assert "must-not-leak" not in str(error.value)
