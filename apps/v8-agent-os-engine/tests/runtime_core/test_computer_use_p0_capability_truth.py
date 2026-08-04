from __future__ import annotations

import builtins
import copy
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from runtimes.computer_use.browser_automation import BrowserAutomationProvider
from runtimes.computer_use.app_catalog import ComputerUseAppCatalog
from runtimes.computer_use.capability_truth import build_capability_truth
from runtimes.computer_use.coordinate_anchor import resolve_absolute_click_point, spatial_anchor_compatibility
from runtimes.computer_use.playbooks import built_in_playbook_seeds
from runtimes.computer_use.runtime import ComputerUseRuntime
from runtimes.computer_use.drivers.windows_uia import WindowsUIADriver
from runtimes.computer_use.drivers.windows_hotkeys import (
    VK_CONTROL,
    VK_MENU,
    normalize_hotkey_sequence,
    parse_hotkey_sequence,
)
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


def test_browser_availability_uses_short_cached_health_probe(monkeypatch):
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": True, "connectTimeoutMs": 3000}})
    provider._node_path = None
    observed_timeouts = []
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: False)
    monkeypatch.setattr(provider, "_is_loopback_port_open", lambda _port: True)

    def unavailable_health(*, timeout_seconds=None):
        observed_timeouts.append(timeout_seconds)
        raise TimeoutError("local proxy unavailable")

    monkeypatch.setattr(provider, "_health", unavailable_health)

    first = provider.availability_summary()
    second = provider.availability_summary()

    assert first["connected"] is False
    assert first["helperHealth"]["status"] == "unreachable"
    assert second["helperHealth"] == first["helperHealth"]
    assert observed_timeouts == [0.25]


def test_browser_availability_skips_http_health_when_proxy_port_is_closed(monkeypatch):
    provider = BrowserAutomationProvider()
    provider.configure({"browserLane": {"enabled": True}})
    provider._node_path = None
    monkeypatch.setattr(provider, "_is_debug_port_reachable", lambda _port: False)
    monkeypatch.setattr(provider, "_is_loopback_port_open", lambda _port: False)
    monkeypatch.setattr(
        provider,
        "_health",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("closed ports must not use an HTTP timeout")),
    )

    summary = provider.availability_summary()

    assert summary["helperHealth"] == {
        "connected": False,
        "status": "unreachable",
        "errorClass": "ConnectionError",
    }


def test_platform_capability_inputs_rechecks_current_driver_truth(monkeypatch):
    calls = 0

    class CurrentDriver:
        platform = "windows"

        def capability_summary(self):
            nonlocal calls
            calls += 1
            return {"platform": "windows", "revision": calls}

    class PortableDriver:
        def __init__(self, platform: str):
            self.platform = platform

        def capability_summary(self):
            return {"platform": self.platform}

    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    runtime.driver = CurrentDriver()
    real_import = builtins.__import__

    def controlled_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "runtimes.computer_use.drivers.mac_ax":
            return SimpleNamespace(MacAXUIDriver=lambda: PortableDriver("macos"))
        if name == "runtimes.computer_use.drivers.linux_atspi":
            return SimpleNamespace(LinuxATSPIADriver=lambda: PortableDriver("linux"))
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", controlled_import)

    first = runtime._platform_capability_inputs()
    second = runtime._platform_capability_inputs()

    assert first["windows"]["revision"] == 1
    assert second["windows"]["revision"] == 2
    assert calls == 2


def test_running_app_catalog_does_not_rewrite_unchanged_snapshot(monkeypatch):
    catalog = ComputerUseAppCatalog.__new__(ComputerUseAppCatalog)
    catalog.static_ttl_seconds = 300
    catalog.running_ttl_seconds = 5
    catalog._static_entries = {"demo": {"appId": "demo", "isRunning": False}}
    catalog._runtime_entries = copy.deepcopy(catalog._static_entries)
    catalog._last_static_refresh_ts = time.time()
    catalog._last_running_refresh_ts = 0.0
    catalog._refresh_lock = threading.RLock()
    catalog.platform_providers = [SimpleNamespace(discover_running_apps=lambda: [])]
    saves: list[bool] = []
    monkeypatch.setattr(catalog, "_save_cache", lambda: saves.append(True))

    catalog._ensure_running(force=False)

    assert saves == []


def test_running_app_catalog_coalesces_concurrent_refreshes(monkeypatch):
    catalog = ComputerUseAppCatalog.__new__(ComputerUseAppCatalog)
    catalog.static_ttl_seconds = 300
    catalog.running_ttl_seconds = 5
    catalog._static_entries = {"demo": {"appId": "demo", "isRunning": False}}
    catalog._runtime_entries = copy.deepcopy(catalog._static_entries)
    catalog._last_static_refresh_ts = time.time()
    catalog._last_running_refresh_ts = 0.0
    catalog._refresh_lock = threading.RLock()
    catalog.app_profiles = SimpleNamespace(get=lambda _profile_id: None, infer=lambda **_kwargs: None)
    catalog.app_adapters = None
    calls = 0

    def discover_running_apps():
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return [{"appId": "demo", "profileId": "demo", "runningWindows": [{"handle": 1}]}]

    catalog.platform_providers = [SimpleNamespace(discover_running_apps=discover_running_apps)]
    monkeypatch.setattr(catalog, "_save_cache", lambda: None)
    workers = [threading.Thread(target=catalog._ensure_running, kwargs={"force": False}) for _ in range(2)]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=1)

    assert calls == 1
    assert catalog._runtime_entries["demo"]["isRunning"] is True


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


def test_capability_truth_requires_a_compatible_system_browser():
    truth = build_capability_truth(
        capability_matrix={"currentPlatform": "windows", "platforms": {}},
        browser_lane={
            "enabled": True,
            "nodeAvailable": True,
            "helperScriptPath": "browser_cdp_proxy.mjs",
            "helperScriptExists": True,
            "playwrightAvailable": True,
            "systemBrowserAvailable": False,
        },
    )

    assert truth["browserLaneTruth"]["status"] == "blocked_by_missing_system_browser"
    assert any(gap["code"] == "compatible_system_browser_missing" for gap in truth["knownGaps"])


def test_capability_truth_blocks_a_cdp_endpoint_outside_the_v8os_profile():
    truth = build_capability_truth(
        capability_matrix={"currentPlatform": "windows", "platforms": {}},
        browser_lane={
            "enabled": True,
            "nodeAvailable": True,
            "helperScriptPath": "browser_cdp_proxy.mjs",
            "helperScriptExists": True,
            "playwrightAvailable": True,
            "systemBrowserAvailable": True,
            "helperHealth": {"connected": False, "status": "profile_mismatch"},
        },
    )

    assert truth["browserLaneTruth"]["status"] == "blocked_by_profile_mismatch"
    assert any(gap["code"] == "agent_browser_profile_mismatch" for gap in truth["knownGaps"])


def test_explicit_bound_coordinate_input_uses_application_surface_focus_mode():
    payload = {
        "window_typing": True,
        "point": [640, 48],
        "_binding_mode": "explicit",
        "_binding_confidence": 1.0,
        "_resolved_app_id": "app_custom_drawn",
    }

    assert ComputerUseRuntime._window_typing_focus_mode(payload) == "application_surface"
    assert ComputerUseRuntime._window_typing_focus_mode({**payload, "_binding_confidence": 0.8}) == ""
    assert ComputerUseRuntime._window_typing_focus_mode({**payload, "window_typing_focus_mode": "content_receiver"}) == "content_receiver"


def test_application_surface_focus_requires_the_verified_root_to_remain_foreground():
    driver = object.__new__(WindowsUIADriver)

    assert driver._accept_window_typing_probe(
        {"foregroundWithinRoot": True, "focusWithinRoot": False, "caretWithinRoot": False},
        focus_mode="application_surface",
    ) is True
    assert driver._accept_window_typing_probe(
        {"foregroundWithinRoot": False, "focusWithinRoot": True, "caretWithinRoot": False},
        focus_mode="application_surface",
    ) is False


def test_human_hotkey_chords_normalize_before_keyboard_injection():
    assert normalize_hotkey_sequence("ALT+F4") == "%{F4}"
    assert normalize_hotkey_sequence("CTRL+L") == "^l"
    assert normalize_hotkey_sequence("%{F4}") == "%{F4}"

    close_stroke = parse_hotkey_sequence("ALT+F4")[0]
    address_stroke = parse_hotkey_sequence("CTRL+L")[0]
    assert close_stroke.token == "F4"
    assert close_stroke.modifiers == (VK_MENU,)
    assert address_stroke.token == "l"
    assert address_stroke.modifiers == (VK_CONTROL,)


def test_windows_hotkey_prefers_sendinput_and_never_types_human_chord(monkeypatch):
    driver = object.__new__(WindowsUIADriver)
    sent: list[str] = []
    target = SimpleNamespace(element_info=SimpleNamespace(handle=42))
    driver._resolve_root_resilient = lambda **_kwargs: target
    driver._focus_wrapper = lambda _target: None
    driver._window_dict = lambda _target: {"handle": 42}
    driver._sendinput_click_engine = SimpleNamespace(
        is_available=lambda: True,
        send_hotkey_sequence=lambda sequence, **_kwargs: sent.append(sequence),
    )
    monkeypatch.setattr(
        "runtimes.computer_use.drivers.windows_uia.send_keys",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("text fallback must not run")),
    )

    result = driver.hotkey("ALT+F4", window_handle=42)

    assert sent == ["%{F4}"]
    assert result["metadata"]["hotkeyStrategy"] == "sendinput"
    assert result["metadata"]["requestedSequence"] == "ALT+F4"
    assert result["metadata"]["canonicalSequence"] == "%{F4}"


def test_explicit_coordinate_click_is_not_replaced_by_a_learned_window_selector():
    runtime = object.__new__(ComputerUseRuntime)
    clicked: list[list[int]] = []

    class _BrowserDecision:
        available = False

    class _CoordinateDriver:
        def click_point(self, *, point, **_kwargs):
            clicked.append(list(point))
            return {"handle": 42, "metadata": {}}

        def click_element(self, **_kwargs):
            raise AssertionError("learned window selector must not replace an explicit coordinate")

    runtime.driver = _CoordinateDriver()
    runtime._browser_lane_decision = lambda **_kwargs: _BrowserDecision()
    runtime._resolve_runtime_click_points = lambda _payload: ([[320, 180]], None, [[0.5, 0.5]], None)

    result = runtime._click_target_from_payload(
        {
            "point": [0.5, 0.5],
            "class_name": "TXGuiFoundation",
            "_explicit_coordinate_target": True,
            "window_handle": 42,
            "prefer_sendinput_click": True,
        }
    )

    assert clicked == [[320, 180]]
    assert result["metadata"]["coordinateFallback"] is True


def test_explicit_coordinate_payload_can_be_restored_after_learned_patches():
    explicit_coordinates = {"point": [0.2, 0.4], "point_candidates": [[0.3, 0.5]]}
    patched = {
        "point": [0.9, 0.9],
        "point_rect": [0.0, 0.0, 1.0, 1.0],
        "class_name": "TXGuiFoundation",
    }
    restored = ComputerUseRuntime._restore_explicit_coordinate_payload(patched, explicit_coordinates)

    assert restored["point"] == [0.2, 0.4]
    assert restored["point_candidates"] == [[0.3, 0.5]]
    assert "point_rect" not in restored
    assert restored["class_name"] == "TXGuiFoundation"


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
