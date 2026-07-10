from __future__ import annotations

import os
import sys
from pathlib import Path

from core import engine_runtime_health


def _create_runtime(root: Path) -> tuple[Path, Path]:
    executable_root = root / "Scripts" if os.name == "nt" else root / "bin"
    executable_root.mkdir(parents=True)
    console = executable_root / ("python.exe" if os.name == "nt" else "python")
    windowed = executable_root / ("pythonw.exe" if os.name == "nt" else "python")
    console.touch()
    windowed.touch()
    return console, windowed


def test_windowed_python_in_same_managed_runtime_is_not_drift(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / ".venv"
    console, windowed = _create_runtime(runtime_root)
    monkeypatch.setattr(engine_runtime_health, "ENGINE_RUNTIME_DIRS", (runtime_root,))
    monkeypatch.setattr(engine_runtime_health, "EXPECTED_ENGINE_PYTHON", console)
    monkeypatch.setattr(sys, "executable", str(windowed))
    monkeypatch.setattr(sys, "prefix", str(runtime_root))

    health = engine_runtime_health.inspect_engine_runtime()

    assert health["interpreterDrift"] is False
    assert health["expectedInterpreterPath"] == str(console.resolve())
    assert health["managedRuntimeRoot"] == str(runtime_root.resolve())
    assert health["warnings"] == []


def test_python_outside_managed_runtime_is_reported_as_drift(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / ".venv"
    console, _windowed = _create_runtime(runtime_root)
    external_root = tmp_path / "external"
    external_python, _ = _create_runtime(external_root)
    monkeypatch.setattr(engine_runtime_health, "ENGINE_RUNTIME_DIRS", (runtime_root,))
    monkeypatch.setattr(engine_runtime_health, "EXPECTED_ENGINE_PYTHON", console)
    monkeypatch.setattr(sys, "executable", str(external_python))
    monkeypatch.setattr(sys, "prefix", str(external_root))

    health = engine_runtime_health.inspect_engine_runtime()

    assert health["interpreterDrift"] is True
    assert health["managedRuntimeRoot"] is None
    assert health["warnings"]
