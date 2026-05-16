from __future__ import annotations

from pathlib import Path

from runtimes.computer_use.browser_automation import BrowserAutomationProvider
from runtimes.computer_use.capability_truth import build_capability_truth
from runtimes.computer_use.coordinate_anchor import resolve_absolute_click_point, spatial_anchor_compatibility
from runtimes.computer_use.playbooks import built_in_playbook_seeds
from runtimes.computer_use.runtime import ComputerUseRuntime
from erc.capability_registry import capability_registry


def _facet(implemented: bool, available: bool, validation_level: str = "fixture_only", **details):
    return {
        "implemented": implemented,
        "available": available,
        "validationLevel": validation_level,
        "details": dict(details),
    }


def test_capability_truth_keeps_non_host_platforms_theory_or_fixture_backed():
    matrix = {
        "currentPlatform": "windows",
        "platforms": {
            "windows": {
                "backend": "uia",
                "facets": {
                    "window": _facet(True, True, "real_host"),
                    "accessibility": _facet(True, True, "real_host"),
                    "observation": _facet(True, True, "real_host"),
                    "input": _facet(True, True, "real_host"),
                    "pointer": _facet(True, True, "real_host"),
                    "viewport": _facet(True, True, "real_host"),
                    "verification": _facet(True, True, "real_host"),
                    "browserAutomation": _facet(True, False, "fixture_only"),
                    "permissionsSession": _facet(True, True, "real_host"),
                },
            },
            "macos": {
                "backend": "ax",
                "facets": {
                    "window": _facet(True, False, "fixture_only"),
                    "accessibility": _facet(True, False, "fixture_only"),
                    "observation": _facet(True, False, "fixture_only"),
                    "input": _facet(True, False, "fixture_only"),
                    "pointer": _facet(True, False, "fixture_only"),
                    "viewport": _facet(True, False, "fixture_only"),
                    "verification": _facet(True, False, "fixture_only"),
                    "browserAutomation": _facet(False, False, "not_validated"),
                    "permissionsSession": _facet(True, False, "fixture_only"),
                },
            },
            "linux": {
                "backend": "atspi",
                "facets": {
                    "window": _facet(True, False, "fixture_only"),
                    "accessibility": _facet(True, False, "fixture_only"),
                    "observation": _facet(True, False, "fixture_only"),
                    "input": _facet(True, False, "fixture_only"),
                    "pointer": _facet(True, False, "fixture_only"),
                    "viewport": _facet(True, False, "fixture_only"),
                    "verification": _facet(True, False, "fixture_only"),
                    "browserAutomation": _facet(False, False, "not_validated"),
                    "permissionsSession": _facet(True, False, "fixture_only", sessionType="x11"),
                },
            },
        },
    }
    truth = build_capability_truth(
        capability_matrix=matrix,
        browser_lane={"enabled": True, "nodeAvailable": True, "helperScriptPath": "missing.mjs", "helperScriptExists": False},
    )

    windows_statuses = {facet["key"]: facet["status"] for facet in truth["platforms"]["windows"]["facets"]}
    mac_statuses = {facet["key"]: facet["status"] for facet in truth["platforms"]["macos"]["facets"]}
    linux_statuses = {facet["key"]: facet["status"] for facet in truth["platforms"]["linux-x11"]["facets"]}

    assert windows_statuses["window"] == "real_host_passed"
    assert mac_statuses["window"] == "theory_aligned"
    assert linux_statuses["window"] == "theory_aligned"
    assert truth["browserLaneTruth"]["status"] == "blocked_by_missing_helper"
    assert any(gap["code"] == "browser_cdp_proxy_missing" for gap in truth["knownGaps"])


def test_browser_lane_requires_cdp_helper_even_when_node_is_present(monkeypatch, tmp_path: Path):
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": True}})
    provider._node_path = "node"
    monkeypatch.setattr(provider, "_helper_script_path", lambda: tmp_path / "browser_cdp_proxy.mjs")

    capabilities = provider.lane_capabilities()
    summary = provider.availability_summary()

    assert capabilities["browserLaneImplemented"] is False
    assert capabilities["browserLaneAvailable"] is False
    assert capabilities["helperScriptExists"] is False
    assert summary["helperScriptExists"] is False


def test_capability_truth_flags_missing_playwright_separately():
    matrix = {
        "currentPlatform": "windows",
        "platforms": {
            "windows": {
                "backend": "uia",
                "facets": {
                    "window": _facet(True, True, "real_host"),
                    "accessibility": _facet(True, True, "real_host"),
                    "observation": _facet(True, True, "real_host"),
                    "input": _facet(True, True, "real_host"),
                    "pointer": _facet(True, True, "real_host"),
                    "viewport": _facet(True, True, "real_host"),
                    "verification": _facet(True, True, "real_host"),
                    "browserAutomation": _facet(True, False, "fixture_only"),
                    "permissionsSession": _facet(True, True, "real_host"),
                },
            }
        },
    }
    truth = build_capability_truth(
        capability_matrix=matrix,
        browser_lane={
            "enabled": True,
            "nodeAvailable": True,
            "helperScriptPath": "browser_cdp_proxy.mjs",
            "helperScriptExists": True,
            "playwrightAvailable": False,
        },
    )

    assert truth["browserLaneTruth"]["status"] == "blocked_by_missing_playwright"
    assert any(gap["code"] == "browser_playwright_missing" for gap in truth["knownGaps"])
    assert "platformParity" in truth


def test_github_star_playbook_seed_is_runtime_native_and_source_tracked():
    seeds = built_in_playbook_seeds()
    seed = next(item for item in seeds if item["id"] == "github.star_repository")

    assert seed["runtimeNative"] is True
    assert seed["preferredLane"] == "browser_cdp_dom"
    assert seed["goldenCase"]["repoUrl"] == "https://github.com/TurixAI/TuriX-CUA"
    assert seed["successState"]["buttonState"] == "Starred"
    assert seed["sourceRefs"][0]["license"] == "MIT"


def test_playbook_seed_is_not_injected_into_supervisor_capability_summary():
    summary = capability_registry.build_supervisor_summary(user_query="去 GitHub 给 TuriX 点星标")

    assert "github.star_repository" not in summary
    assert "skills/github-web-actions.md" not in summary


class _FakeObservation:
    def as_dict(self):
        return {"windowTitle": "Sign in", "elements": [{"name": "Password"}], "treeHash": "a", "screenHash": "b"}


class _FakeDriver:
    def __init__(self):
        self.hotkeys: list[str] = []

    def hotkey(self, sequence: str, **_kwargs):
        self.hotkeys.append(sequence)
        return {"metadata": {"sequence": sequence}}

    def observe_desktop(self, **_kwargs):
        return _FakeObservation()


class _FakeRunHandle:
    run_id = "run-screen-wake"
    session_id = "session-screen-wake"

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict):
        self.events.append((event_type, payload))


def test_screen_wake_attempt_is_once_per_run_and_stops_on_credentials(monkeypatch):
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    runtime.driver = _FakeDriver()
    runtime._screen_wake_attempts = {}
    monkeypatch.setattr("runtimes.computer_use.runtime.time.sleep", lambda _seconds: None)
    run_handle = _FakeRunHandle()
    visual_guard = {"status": "analyzed", "confirmed": False, "reason": "lock screen desktop wallpaper"}

    first = runtime._attempt_screen_wake_recovery(
        run_handle=run_handle,
        visual_guard=visual_guard,
        action="click",
    )
    second = runtime._attempt_screen_wake_recovery(
        run_handle=run_handle,
        visual_guard=visual_guard,
        action="click",
    )

    assert first["attempted"] is True
    assert first["requiresHumanAttention"] is True
    assert runtime.driver.hotkeys == ["{SPACE}"]
    assert second["attempted"] is False
    assert second["alreadyAttempted"] is True
    assert run_handle.events[0][0] == "computer_use.screen_wake_attempted"


def test_spatial_anchor_blocks_reuse_when_display_or_dpi_changes():
    anchor = {
        "screenRelativePoint": [0.5, 0.5],
        "windowRelativeRect": [0.4, 0.4, 0.6, 0.6],
        "displayBounds": [0, 0, 1920, 1080],
        "windowBounds": [100, 100, 900, 700],
        "dpiScale": 1.0,
    }
    observation = {
        "metadata": {
            "displayBounds": [0, 0, 1366, 768],
            "windowBounds": [100, 100, 900, 700],
            "dpiScale": 1.25,
        }
    }

    compatibility = spatial_anchor_compatibility(spatial_anchor=anchor, observation=observation)
    point = resolve_absolute_click_point(
        suggested_point=None,
        spatial_anchor=anchor,
        observation=observation,
    )

    assert compatibility["compatible"] is False
    assert "display_bounds_changed" in compatibility["reasons"]
    assert "dpi_scale_changed" in compatibility["reasons"]
    assert point is None
