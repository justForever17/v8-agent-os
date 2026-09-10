from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from PIL import Image, ImageDraw


SCRIPT_ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("owned_window_probe_test_module", SCRIPT_ROOT / "computer_use_owned_window_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_capture_size_and_multiple_colors_do_not_prove_owned_window(tmp_path):
    image = Image.new("RGB", (1350, 750), "black")
    ImageDraw.Draw(image).rectangle((900, 100, 1300, 200), fill="white")
    path = tmp_path / "occluded.png"
    image.save(path)

    proof = probe.fixture_image_proof(path, expected_color=(24, 58, 82))

    assert proof["imageSize"] == [1350, 750]
    assert proof["fixturePixelsPresent"] is False
    assert proof["fixtureColorCoverage"] == 0


def test_owned_fixture_pixels_survive_titlebar_and_input_controls(tmp_path):
    image = Image.new("RGB", (900, 500), (24, 58, 82))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 899, 40), fill="white")
    draw.rectangle((40, 110, 660, 145), fill="white")
    path = tmp_path / "owned.png"
    image.save(path)

    assert probe.fixture_image_proof(path, expected_color=(24, 58, 82))["fixturePixelsPresent"] is True


def test_owned_probe_rejects_real_state_root_before_runtime_imports():
    env = {**os.environ, "V8_AGENT_OS_HOME": str(Path.home() / ".v8-agent-os")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT_ROOT / "run_computer_use_real_host_matrix.py"), "--owned-window-probe"],
        env=env, capture_output=True, text=True, timeout=10,
    )

    assert result.returncode != 0
    assert "before runtime imports" in result.stderr


def test_native_cli_requires_live_before_creating_state_or_windows(tmp_path, monkeypatch):
    target = tmp_path / "must-not-exist"
    monkeypatch.setattr(probe, "run_owned_window_probe", lambda *_a, **_k: pytest.fail("No window without --live"))
    with pytest.raises(SystemExit) as stopped:
        probe.main(["--isolated-root", str(target)])
    assert stopped.value.code == 2
    assert not target.exists()


def test_stage_measurements_preserve_real_returns_errors_and_restore_methods():
    calls = []

    def focus_window(value):
        calls.append(value)
        if value == "fail":
            raise ValueError("fixture failure")
        return {"handle": value}

    runtime = SimpleNamespace(driver=SimpleNamespace(focus_window=focus_window))
    with probe.measure_native_stages(runtime) as stages:
        assert runtime.driver.focus_window(42) == {"handle": 42}
        with pytest.raises(ValueError, match="fixture failure"):
            runtime.driver.focus_window("fail")
    assert runtime.driver.focus_window is focus_window
    assert calls == [42, "fail"]
    assert stages["driver.focus_window"]["calls"] == 2
    assert stages["driver.focus_window"]["totalMs"] >= stages["driver.focus_window"]["selfMs"] >= 0
    assert set(stages["driver.focus_window"]) == {"calls", "totalMs", "selfMs"}
