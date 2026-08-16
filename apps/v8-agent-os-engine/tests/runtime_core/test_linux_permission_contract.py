from __future__ import annotations

from pathlib import Path

import pytest

from runtimes.computer_use.drivers.linux_atspi import LinuxATSPIADriver, LinuxATSPIError


def test_wayland_capabilities_fail_closed_without_portal_session(monkeypatch) -> None:
    driver = LinuxATSPIADriver()
    monkeypatch.setattr(driver, "_session_type", lambda: "wayland")
    monkeypatch.setattr(driver, "_compositor", lambda: "GNOME")
    monkeypatch.setattr(driver, "_mss_available", lambda: True)
    monkeypatch.setattr(
        "runtimes.computer_use.drivers.linux_atspi.tool_exists",
        lambda _name: True,
    )

    summary = driver.capability_summary()

    assert summary["permission"]["automationStatus"] == "not_used"
    assert summary["permission"]["screenshotStatus"] == "blocked"
    assert summary["permission"]["portalCaptureStatus"] == "unsupported"
    assert summary["permission"]["inputSynthesisStatus"] == "blocked"
    assert summary["observation"]["supportsKeyframeVisualFallback"] is False
    assert summary["execution"]["supportsVisualRoute"] is False
    assert summary["execution"]["supportsCoordinateFallback"] is False
    assert summary["window"]["supportsFocus"] is False
    assert summary["window"]["supportsActivate"] is False


def test_wayland_capture_does_not_bypass_portal(monkeypatch, tmp_path: Path) -> None:
    driver = LinuxATSPIADriver()
    monkeypatch.setattr("runtimes.computer_use.drivers.linux_atspi.sys_platform_linux", lambda: True)
    monkeypatch.setattr(driver, "_session_type", lambda: "wayland")

    with pytest.raises(LinuxATSPIError, match="ScreenCast portal"):
        driver.capture_screenshot(tmp_path / "blocked.png")


def test_wayland_focus_fails_before_window_or_tool_side_effects(monkeypatch) -> None:
    driver = LinuxATSPIADriver()
    monkeypatch.setattr("runtimes.computer_use.drivers.linux_atspi.sys_platform_linux", lambda: True)
    monkeypatch.setattr(driver, "_session_type", lambda: "wayland")
    monkeypatch.setattr(
        driver,
        "wait_for_window",
        lambda **_kwargs: pytest.fail("Wayland focus must not enumerate windows"),
    )
    monkeypatch.setattr(
        "runtimes.computer_use.drivers.linux_atspi.run_command",
        lambda *_args, **_kwargs: pytest.fail("Wayland focus must not invoke X11 tools"),
    )

    with pytest.raises(LinuxATSPIError, match="X11"):
        driver.focus_window(window_handle=42, window_title="Blocked")


def test_x11_screenshot_capability_requires_a_real_capture_backend(monkeypatch) -> None:
    driver = LinuxATSPIADriver()
    monkeypatch.setattr(driver, "_session_type", lambda: "x11")
    monkeypatch.setattr(driver, "_compositor", lambda: "X11")
    monkeypatch.setattr(driver, "_mss_available", lambda: False)
    monkeypatch.setattr(
        "runtimes.computer_use.drivers.linux_atspi.tool_exists",
        lambda name: name in {"xdotool", "wmctrl"},
    )

    summary = driver.capability_summary()

    assert summary["permission"]["screenshotStatus"] == "blocked"
    assert summary["pointer"]["supportsClick"] is True
    assert summary["execution"]["supportsCoordinateFallback"] is True
