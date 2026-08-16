from __future__ import annotations

from pathlib import Path

import pytest

from runtimes.computer_use.drivers import mac_ax as mac_ax_module
from runtimes.computer_use.drivers.mac_ax import MacAXUIDriver, MacAXUIDriverError


def _summary(monkeypatch, *, accessibility: bool, screen_capture: bool, tool_present: bool = True):
    driver = MacAXUIDriver()
    monkeypatch.setattr(
        driver,
        "_probe",
        lambda: {
            "accessibilityGranted": accessibility,
            "screenCaptureGranted": screen_capture,
        },
    )
    monkeypatch.setattr(
        "runtimes.computer_use.drivers.mac_ax.tool_exists",
        lambda name: tool_present and name in {"screencapture", "pbcopy", "pbpaste"},
    )
    return driver.capability_summary()


def test_screen_capture_requires_tcc_preflight_and_tool(monkeypatch) -> None:
    blocked = _summary(monkeypatch, accessibility=True, screen_capture=False)
    assert blocked["permission"]["screenshotStatus"] == "blocked"
    assert blocked["observation"]["supportsKeyframeVisualFallback"] is False
    assert blocked["execution"]["supportsVisualRoute"] is False

    granted = _summary(monkeypatch, accessibility=True, screen_capture=True)
    assert granted["permission"]["screenshotStatus"] == "granted"
    assert granted["observation"]["supportsKeyframeVisualFallback"] is True
    assert granted["execution"]["supportsVisualRoute"] is True


def test_automation_is_not_inferred_from_osascript_presence(monkeypatch) -> None:
    summary = _summary(monkeypatch, accessibility=True, screen_capture=True)
    assert summary["permission"]["automationStatus"] == "not_used"
    assert "applescript" not in summary["input"]["strategyOrder"]
    assert "applescript" not in summary["accessibility"]["fallbackBackends"]


def test_swift_probe_uses_screen_capture_tcc_preflight() -> None:
    helper = Path(__file__).parents[2] / "runtimes" / "computer_use" / "drivers" / "mac_ax_helper.swift"
    source = helper.read_text(encoding="utf-8")
    assert '"screenCaptureGranted": CGPreflightScreenCaptureAccess()' in source


def test_capture_screenshot_blocks_before_backend_or_artifact(monkeypatch, tmp_path: Path) -> None:
    driver = MacAXUIDriver()
    monkeypatch.setattr(driver, "ensure_available", lambda: None)
    monkeypatch.setattr(
        driver,
        "_probe",
        lambda: {"accessibilityGranted": True, "screenCaptureGranted": False},
    )

    def unexpected_backend(*_args, **_kwargs):
        raise AssertionError("截图权限被拒绝时不得调用截图后端")

    monkeypatch.setattr(mac_ax_module, "tool_exists", unexpected_backend)
    monkeypatch.setattr(mac_ax_module, "run_command", unexpected_backend)
    monkeypatch.setattr(mac_ax_module, "capture_with_mss", unexpected_backend)

    output_path = tmp_path / "capture" / "blocked.png"
    with pytest.raises(MacAXUIDriverError, match=r"^permission_blocked:"):
        driver.capture_screenshot(output_path)

    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_capture_screenshot_keeps_granted_backend_path(monkeypatch, tmp_path: Path) -> None:
    driver = MacAXUIDriver()
    monkeypatch.setattr(driver, "ensure_available", lambda: None)
    monkeypatch.setattr(
        driver,
        "_probe",
        lambda: {"accessibilityGranted": True, "screenCaptureGranted": True},
    )
    monkeypatch.setattr(mac_ax_module, "tool_exists", lambda _name: False)

    backend_calls: list[Path] = []

    def capture(output_path):
        path = Path(output_path)
        backend_calls.append(path)
        path.write_bytes(b"granted")
        return {"path": str(path), "sha256": "test-sha256"}

    monkeypatch.setattr(mac_ax_module, "capture_with_mss", capture)

    output_path = tmp_path / "capture" / "granted.png"
    result = driver.capture_screenshot(output_path)

    assert backend_calls == [output_path]
    assert output_path.read_bytes() == b"granted"
    assert result == {"path": str(output_path), "sha256": "test-sha256"}
