from __future__ import annotations

import asyncio

from api import computer_use_routes
from api.models import ComputerUseAgentBrowserOpenPayload
from runtimes.computer_use.browser_automation import BrowserAutomationProvider, BrowserLaneDecision


def test_shared_agent_browser_route_uses_one_canonical_chrome_profile(monkeypatch):
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
    assert result["browserKind"] == "chrome"
    assert calls == [{"browser_kind": "chrome", "url": "about:blank"}]


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
