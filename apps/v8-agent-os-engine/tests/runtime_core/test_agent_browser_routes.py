from __future__ import annotations

import asyncio
from types import SimpleNamespace

import psutil

from api import computer_use_routes
from api.models import ComputerUseAgentBrowserOpenPayload
from core.agent_browser_profile import debug_port_owned_by_profile, discover_system_agent_browser
from runtimes.computer_use.browser_automation import BrowserAutomationProvider, BrowserLaneDecision
from runtimes.rpa.compiler import RPATraceCompiler
from runtimes.rpa.robot_adapter import RobotFrameworkAdapter


def test_shared_agent_browser_route_uses_automatic_system_browser_selection(monkeypatch):
    calls: list[dict] = []

    class _BrowserProvider:
        def open_agent_browser(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "browserKind": kwargs.get("browser_kind")}

    class _Runtime:
        browser_automation = _BrowserProvider()

    monkeypatch.setattr(computer_use_routes, "_computer_use_runtime", lambda: _Runtime())

    result = asyncio.run(
        computer_use_routes.open_agent_browser(
            ComputerUseAgentBrowserOpenPayload(browserKind="edge", url="about:blank")
        )
    )

    assert result["ok"] is True
    assert result["browserKind"] == "auto"
    assert calls == [{"browser_kind": "auto", "url": "about:blank"}]


def test_manual_agent_browser_profile_setup_is_not_gated_by_computer_use_lane(monkeypatch, tmp_path):
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": False, "allowManagedLaunch": True}})
    helper = tmp_path / "browser-helper.js"
    helper.write_text("// smoke", encoding="utf-8")

    monkeypatch.setattr(provider, "_node_path", "node")
    monkeypatch.setattr(provider, "_helper_script_path", lambda: helper)
    monkeypatch.setattr(provider, "_probe_playwright_dependency", lambda: {"available": True})
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: False)
    monkeypatch.setattr(provider, "_dedicated_user_data_dir", lambda _kind: tmp_path / "profile")
    monkeypatch.setattr(
        provider,
        "discover_system_browser",
        lambda _kind=None: {"available": True, "browserKind": "chrome", "executable": "chrome.exe"},
    )

    unchanged_command, _, unchanged_metadata = provider.prepare_launch(
        app_id="chrome",
        launch_command=["chrome.exe"],
    )
    assert unchanged_command == ["chrome.exe"]
    assert unchanged_metadata is None

    manual_command, _, manual_metadata = provider.prepare_launch(
        app_id="chrome",
        launch_command=["chrome.exe"],
        allow_when_disabled=True,
    )
    assert f"--remote-debugging-port={provider._target_port}" in manual_command
    assert any(item.startswith("--user-data-dir=") for item in manual_command)
    assert manual_metadata["managedLaunch"] is True

    monkeypatch.setattr(
        provider,
        "_start_managed_chromium_debug_browser",
        lambda **_kwargs: BrowserLaneDecision(
            enabled=True,
            available=True,
            family="chromium",
            reason="manual_profile_setup",
            target_port=provider._target_port,
            managed_launch=True,
        ),
    )
    monkeypatch.setattr(provider, "open_tab", lambda **_kwargs: {"opened": True})
    monkeypatch.setattr(provider, "_ensure_proxy", lambda **_kwargs: None)
    monkeypatch.setattr(provider, "_health", lambda **_kwargs: {"connected": True})
    monkeypatch.setattr(
        provider,
        "agent_browser_profile_summary",
        lambda _kind=None: {"kind": "chrome", "persistent": True},
    )

    result = provider.open_agent_browser(browser_kind="chrome", url="about:blank")

    assert result["ok"] is True
    assert result["browserKind"] == "chrome"
    assert result["profile"] == {"kind": "chrome", "persistent": True}
    assert provider.lane_capabilities()["browserLaneEnabled"] is False


def test_windows_auto_selection_prefers_edge_then_chrome_then_chromium(monkeypatch):
    provider = BrowserAutomationProvider()
    monkeypatch.setattr("runtimes.computer_use.browser_automation.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        provider,
        "_platform_browser_candidates",
        lambda family: [[f"{family}.exe"]],
    )
    monkeypatch.setattr(
        provider,
        "_resolve_executable_command",
        lambda command: command if command[0] == "edge.exe" else None,
    )

    probe = provider.discover_system_browser("auto")

    assert probe["available"] is True
    assert probe["browserKind"] == "edge"
    assert probe["candidateOrder"] == ["edge", "chrome", "chromium"]


def test_shared_system_browser_discovery_uses_the_same_windows_order(monkeypatch):
    monkeypatch.setattr("core.agent_browser_profile.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "core.agent_browser_profile.system_agent_browser_candidates",
        lambda kind, _system_name=None: [f"{kind}.exe"],
    )
    monkeypatch.setattr(
        "core.agent_browser_profile.shutil.which",
        lambda candidate: candidate if candidate == "edge.exe" else None,
    )

    probe = discover_system_agent_browser("auto")

    assert probe["browserKind"] == "edge"
    assert probe["candidateOrder"] == ["edge", "chrome", "chromium"]


def test_agent_browser_reports_missing_compatible_browser_without_downloading(monkeypatch, tmp_path):
    provider = BrowserAutomationProvider()
    helper = tmp_path / "browser-helper.js"
    helper.write_text("// smoke", encoding="utf-8")
    monkeypatch.setattr(provider, "_node_path", "node")
    monkeypatch.setattr(provider, "_helper_script_path", lambda: helper)
    monkeypatch.setattr(provider, "_probe_playwright_dependency", lambda: {"available": True})
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: False)
    monkeypatch.setattr(
        provider,
        "discover_system_browser",
        lambda _kind=None: {"available": False, "reason": "compatible_browser_missing"},
    )

    result = provider.open_agent_browser(browser_kind="auto", url="about:blank")

    assert result["ok"] is False
    assert result["failureClass"] == "compatible_browser_missing"
    assert "不会自动下载浏览器" in result["recommendedNextAction"]


def test_agent_browser_refuses_a_debug_port_owned_by_another_profile(monkeypatch, tmp_path):
    provider = BrowserAutomationProvider()
    helper = tmp_path / "browser-helper.js"
    helper.write_text("// smoke", encoding="utf-8")
    monkeypatch.setattr(provider, "_node_path", "node")
    monkeypatch.setattr(provider, "_helper_script_path", lambda: helper)
    monkeypatch.setattr(provider, "_probe_playwright_dependency", lambda: {"available": True})
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: True)
    monkeypatch.setattr(provider, "_managed_agent_browser_kind_at_port", lambda _port: None)

    result = provider.open_agent_browser(browser_kind="auto", url="about:blank")

    assert result["ok"] is False
    assert result["failureClass"] == "agent_browser_port_conflict"
    assert "不会接管用户日常 profile" in result["recommendedNextAction"]


def test_agent_browser_proxy_waits_until_playwright_is_connected(monkeypatch):
    provider = BrowserAutomationProvider()
    provider._connect_timeout_ms = 100
    provider._proxy_process = SimpleNamespace(poll=lambda: None)
    health_results = iter(
        [
            {"connected": False, "error": "starting"},
            {"connected": False, "error": "attaching"},
            {"connected": True},
        ]
    )
    health_calls: list[float | None] = []

    def _health(*, timeout_seconds=None):
        health_calls.append(timeout_seconds)
        return next(health_results)

    monkeypatch.setattr(provider, "_health", _health)
    monkeypatch.setattr("runtimes.computer_use.browser_automation.time.sleep", lambda _seconds: None)

    provider._ensure_proxy(target_port=9222, startup_timeout_seconds=1.0)

    assert len(health_calls) == 3
    assert all(timeout is not None and timeout <= 1.0 for timeout in health_calls)


def test_browser_dom_input_uses_native_setter_for_controlled_fields():
    provider = BrowserAutomationProvider()

    script = provider._browser_input_script(
        text="controlled input",
        payload={"browser_selector": "textarea"},
    )

    assert "Object.getOwnPropertyDescriptor(prototype, 'value')?.set" in script
    assert "new InputEvent('input'" in script
    assert "setter.call(target, nextValue)" in script


def test_browser_discovery_does_not_scan_arbitrary_debug_ports(monkeypatch):
    provider = BrowserAutomationProvider()
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: True)
    monkeypatch.setattr(provider, "_devtools_active_port_files", lambda _kind: [])
    monkeypatch.setattr(
        "runtimes.computer_use.browser_automation.debug_port_owned_by_profile",
        lambda **_kwargs: False,
    )

    assert provider._discover_existing_debug_port(app_id="agent_browser") is None


def test_close_managed_browser_is_idempotent_when_no_browser_is_running(monkeypatch):
    provider = BrowserAutomationProvider()
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: False)
    monkeypatch.setattr(
        provider,
        "_ensure_proxy",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cleanup must not start the proxy")),
    )

    result = provider.close_managed_browser()

    assert result["closed"] is True
    assert result["reason"] == "agent_browser_not_running"
    assert result["errors"] == []


def test_debug_port_ownership_requires_the_exact_v8os_profile_and_port(monkeypatch, tmp_path):
    profile_dir = tmp_path / "edge"
    listener = SimpleNamespace(
        pid=42,
        laddr=SimpleNamespace(port=9222),
        status="LISTEN",
    )

    class _Process:
        def cmdline(self):
            return [
                "msedge.exe",
                "--remote-debugging-port=9222",
                f"--user-data-dir={profile_dir}",
            ]

    monkeypatch.setattr(psutil, "net_connections", lambda **_kwargs: [listener])
    monkeypatch.setattr(psutil, "Process", lambda _pid: _Process())

    assert debug_port_owned_by_profile(port=9222, profile_dir=profile_dir) is True
    assert debug_port_owned_by_profile(port=9222, profile_dir=tmp_path / "daily-profile") is False


def test_rpa_auto_browser_arguments_resolve_the_installed_agent_browser(monkeypatch, tmp_path):
    for module_name in ("runtimes.rpa.compiler", "runtimes.rpa.robot_adapter"):
        monkeypatch.setattr(
            f"{module_name}.discover_system_agent_browser",
            lambda: {"available": True, "browserKind": "edge"},
        )
        monkeypatch.setattr(
            f"{module_name}.configured_agent_browser_profile_dir",
            lambda kind: tmp_path / kind,
        )

    step = {"params": {"browserKind": "auto", "url": "https://example.com"}}
    compiler_args = RPATraceCompiler.__new__(RPATraceCompiler)._browser_open_arguments(
        app_id="agent_browser",
        step=step,
    )
    adapter_args = RobotFrameworkAdapter.__new__(RobotFrameworkAdapter)._browser_open_arguments(
        app_id="agent_browser",
        step=step,
    )

    for arguments in (compiler_args, adapter_args):
        assert "browser_selection=Edge" in arguments
        assert f"profile_path={tmp_path / 'edge'}" in arguments
