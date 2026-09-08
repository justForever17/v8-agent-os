from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtimes.computer_use.app_binding_policy import AppBindingDecision
from runtimes.computer_use.browser_automation import BrowserAutomationProvider, BrowserLaneDecision
from runtimes.computer_use.drivers.windows_uia import WindowsUIADriver, WindowsUIADriverError
from runtimes.computer_use.runtime import ComputerUseRuntime
from runtimes.computer_use.playbook_executors import create_default_playbook_executor_registry
from runtimes.computer_use.task_loop import prepare_task_loop
from runtimes.computer_use.types import ComputerUseObservation
from runtimes.computer_use.window_scene import choose_best_window_candidate, is_passive_overlay_window


def _binding(app_id: str, control_class: str, title: str = "Example") -> AppBindingDecision:
    return AppBindingDecision(
        requested_app_id=app_id, resolved_app_id=app_id, binding_mode="explicit",
        binding_confidence=1.0, binding_evidence={"source": "test_catalog"}, profile_eligible=True,
        catalog_entry={"appId": app_id, "displayName": title, "controlClass": control_class,
                       "titlePatterns": [title], "processNames": [f"{app_id}.exe"]},
    )


def _task_runtime(binding: AppBindingDecision | None = None) -> ComputerUseRuntime:
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    runtime._resolve_app_binding = Mock(return_value=binding)
    runtime.browser_automation = BrowserAutomationProvider()
    runtime._browser_lane_decision = Mock(return_value=BrowserLaneDecision(
        enabled=True, available=True, family="chromium", reason="test_browser", target_port=9222,
    ))
    runtime._task_loop_web_searcher = Mock(side_effect=AssertionError("unexpected web search"))
    return runtime


@pytest.mark.parametrize("control_class", ["native_semantic_app", "electron_app", "custom_drawn_app"])
def test_native_application_submission_never_probes_or_replaces_target_with_browser(control_class):
    runtime = _task_runtime(_binding("example", control_class))

    loop = runtime.prepare_task_loop(
        goal="在桌面应用里输入测试文本并提交，完成后截图。", app_id="example",
        target_url="https://example.com/", playbook_inputs={"fields": {"text": "test"}},
    )

    assert loop["domain"]["selectedPlaybook"] is None
    assert loop["status"] == "generic_planner"
    runtime._browser_lane_decision.assert_not_called()
    runtime._task_loop_web_searcher.assert_not_called()


def test_unresolved_named_application_does_not_become_default_browser():
    runtime = _task_runtime()

    loop = runtime.prepare_task_loop(goal="输入并提交测试文本", app_name="Unknown desktop app")

    assert loop["domain"]["selectedPlaybook"] is None
    runtime._browser_lane_decision.assert_not_called()
    runtime._task_loop_web_searcher.assert_not_called()


@pytest.mark.parametrize("inputs", [None, {}, {"fields": {}}, {"fields": ["text"]}])
def test_web_form_without_typed_fields_remains_an_agent_task(inputs):
    browser_probe = Mock(side_effect=AssertionError("inapplicable playbook must not open a browser"))
    loop = prepare_task_loop(
        "填写并提交 https://example.com/form", browser_target=True,
        playbook_inputs=inputs, browser_decision=browser_probe,
    ).as_dict()

    assert loop["domain"]["selectedPlaybook"] is None
    assert loop["status"] == "generic_planner"
    browser_probe.assert_not_called()


def test_bound_browser_form_uses_actual_target_and_executor_inputs():
    runtime = _task_runtime(_binding("chrome", "browser_host_app", "Chrome"))

    loop = runtime.prepare_task_loop(
        goal="填写并提交表单", app_id="chrome", target_url="https://example.com/form",
        playbook_inputs={"fields": {"email": "test@example.com"}},
    )

    assert loop["status"] == "ready"
    assert loop["domain"]["selectedPlaybook"] == "web.form_submit"
    assert loop["plan"]["targetUrl"] == "https://example.com/form"
    assert loop["goal"] == "填写并提交表单"
    probe = runtime._browser_lane_decision.call_args.kwargs
    assert probe["app_id"] == "chrome"
    assert probe["action_payload"] == {"app_id": "chrome", "app_name": "Chrome"}


def test_unbound_explicit_url_can_use_governed_agent_browser():
    runtime = _task_runtime()

    loop = runtime.prepare_task_loop(goal="打开文档", target_url="https://example.com/docs")

    assert loop["domain"]["selectedPlaybook"] == "web.search_and_open_result"
    assert loop["plan"]["targetUrl"] == "https://example.com/docs"
    assert runtime._browser_lane_decision.call_args.kwargs["app_id"] == "agent_browser"


def test_unresolved_web_target_does_not_launch_browser_before_fact_resolution():
    browser_probe = Mock(side_effect=AssertionError("target must resolve before browser launch"))

    loop = prepare_task_loop(
        "给 GitHub 上一个不存在的测试项目点星标", browser_target=True,
        browser_decision=browser_probe, web_searcher=lambda _query: {"results": []},
    ).as_dict()

    assert loop["status"] == "needs_fact_resolution"
    browser_probe.assert_not_called()


def test_generic_plan_reuses_target_decision_without_reclassifying_acceptance_text():
    runtime = _task_runtime()
    runtime.prepare_task_loop = Mock(side_effect=AssertionError("must reuse the original target decision"))
    runtime.playbook_executor_registry = create_default_playbook_executor_registry()
    runtime.observe = Mock(return_value={"observation": {"windowTitle": "Example"}})
    runtime._plan_steps = Mock(return_value={"plannerOutput": "desktop plan", "steps": [{"action": "observe"}]})

    result = runtime.plan(
        goal="截图当前桌面。Success criteria: 不提交 https://example.com/form",
        task_loop={"domain": {"selectedPlaybook": None}, "status": "generic_planner"},
    )

    assert result["planner"]["steps"] == [{"action": "observe"}]
    runtime.observe.assert_called_once()
    runtime.prepare_task_loop.assert_not_called()


def _window(handle, title, class_name, process_name, bounds):
    return {"handle": handle, "title": title, "className": class_name,
            "processName": process_name, "bounds": bounds, "isVisible": True}


def _watermark():
    return _window(123, "System watermark", "Worker Window", "explorer.exe", [2233, 1323, 2442, 1370])


def test_passive_overlay_cannot_win_even_with_a_preferred_handle():
    overlay = _watermark()
    desktop = _window(124, "Program Manager", "Progman", "explorer.exe", [0, 0, 2560, 1440])

    selected = choose_best_window_candidate([overlay, desktop], preferred_handle=123)

    assert selected["handle"] == 124
    assert choose_best_window_candidate([overlay]) is None
    assert is_passive_overlay_window(overlay)
    assert not is_passive_overlay_window({**overlay, "processName": "example.exe"})


def _window_driver(windows, foreground=None):
    driver = WindowsUIADriver.__new__(WindowsUIADriver)
    wrappers = [SimpleNamespace(element_info=SimpleNamespace(handle=w["handle"]),
                                window_text=lambda w=w: w["title"], payload=w) for w in windows]
    driver._window_cache = {}
    driver._root_cache_ttl_seconds = 1
    driver._safe_backend_windows = lambda _backend: wrappers
    driver._window_dict = lambda wrapper: dict(wrapper.payload)
    driver.foreground_window = lambda: foreground
    driver._desktop_for_backend = lambda _backend: SimpleNamespace(
        window=lambda handle: SimpleNamespace(wrapper_object=lambda: next(w for w in wrappers if w.payload["handle"] == handle)),
    )
    return driver


def test_automatic_root_skips_watermark_and_preserves_foreground_application():
    application = _window(125, "Application", "Chrome_WidgetWin_1", "example.exe", [-1600, 0, 0, 1000])
    driver = _window_driver([_watermark(), application], foreground=application)

    assert driver._resolve_root().payload == application


def test_automatic_root_reports_missing_target_when_only_overlay_exists():
    driver = _window_driver([_watermark()])

    with pytest.raises(WindowsUIADriverError, match="没有可访问"):
        driver._resolve_root()


def test_explicit_small_dialog_is_kept_even_when_a_large_application_exists():
    dialog = _window(126, "Confirm", "#32770", "example.exe", [-350, -100, -141, -53])
    application = _window(125, "Application", "Window", "example.exe", [0, 0, 2560, 1440])
    driver = _window_driver([_watermark(), application, dialog], foreground=application)

    assert driver._resolve_root(window_handle=126).payload == dialog
    assert driver._resolve_root(window_title="Confirm").payload == dialog


def test_screenshot_keeps_negative_monitor_coordinates(monkeypatch, tmp_path):
    import runtimes.computer_use.drivers.windows_uia as driver_module

    bounds = [-1600, -200, 0, 800]
    window = _window(125, "Application", "Window", "example.exe", bounds)
    driver = _window_driver([window])
    driver._resolve_root_resilient = lambda **_kwargs: SimpleNamespace(rectangle=lambda: bounds)
    driver._window_dict = lambda _root: window
    driver._rect_to_bounds = lambda rectangle: rectangle
    for name in ("_stabilize_capture_window", "_recover_capture_window_if_needed", "_prepare_capture_window_foreground"):
        monkeypatch.setattr(driver, name, lambda **kwargs: (kwargs["window"], kwargs["bounds"]))
    grabbed = []
    class Capture:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def grab(self, monitor):
            grabbed.append(monitor)
            return SimpleNamespace(rgb=b"", size=(1600, 1000))

    monkeypatch.setattr(driver_module, "mss", SimpleNamespace(mss=Capture, tools=SimpleNamespace(to_png=lambda *_args, **_kwargs: None)))

    result = driver.capture_screenshot(tmp_path / "capture.png", window_handle=125)

    assert grabbed == [{"left": -1600, "top": -200, "width": 1600, "height": 1000}]
    assert result["bounds"] == bounds
    assert result["size"] == {"width": 1600, "height": 1000}


@pytest.mark.parametrize("observed_title", ["Requested App", "Unrelated App"])
def test_observation_checks_original_target_and_keeps_rebinding_separate_from_block(observed_title, monkeypatch):
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    runtime._ensure_runtime_ready = lambda: None
    requested = _binding("example", "native_semantic_app", "Requested App")
    observed = _binding("example", "native_semantic_app", observed_title)
    runtime._resolve_app_binding = Mock(side_effect=[requested, observed])
    run_handle = SimpleNamespace(session_id="test-session", run_id="test-run", emit=Mock())
    runtime.begin_or_attach_run = lambda **_kwargs: run_handle
    runtime._preflight = lambda **_kwargs: None
    runtime._raise_if_controlled = lambda **_kwargs: None
    runtime._run_context = lambda **_kwargs: {}
    rebound = {"reason": "window_context_rebound", "window": {"title": observed_title, "handle": 456}}
    runtime._prepare_action_window_context = lambda **kwargs: (
        {**kwargs["action_payload"], "window_title": observed_title, "window_handle": 456}, rebound,
    )
    observation = ComputerUseObservation(
        snapshot_id="snapshot", platform="windows", backend="windows_uia", app=observed_title,
        window_title=observed_title, screen_hash="screen", tree_hash="tree",
        metadata={"windowHandle": 456, "className": "Window", "processName": "example.exe", "dpiScale": 1.5},
    )
    runtime.driver = SimpleNamespace(platform="windows", observe_desktop=lambda **_kwargs: observation)
    runtime._browser_lane_decision = lambda **_kwargs: BrowserLaneDecision(enabled=False, available=False)
    runtime._collect_environment_probe_snapshot = lambda **_kwargs: {}
    runtime._prime_selector_context = lambda **_kwargs: None
    runtime._emit_environment_signal = Mock()
    runtime._request_environment_interrupt = Mock()
    runtime._refresh_snapshot = lambda **_kwargs: {}
    runtime._record_observation_screenshot = Mock(return_value={"artifactId": "test-image"})
    monkeypatch.setattr("runtimes.computer_use.runtime.build_scene_assessment", lambda **_kwargs: {"blockerState": "none"})

    result = runtime.observe(app_id="example", window_title="Requested App", include_screenshot=True)

    metadata = result["observation"]["metadata"]
    assert metadata["bindingAssessment"]["status"] == ("verified" if observed_title == "Requested App" else "unresolved")
    assert metadata["requestedTarget"] == {"appId": "example", "windowTitle": "Requested App", "windowHandle": None}
    assert metadata["observedTarget"]["windowTitle"] == observed_title
    assert metadata["bindingAssessment"]["expected"]["titles"] == ["Requested App"]
    assert metadata["windowContextPatch"] == rebound
    assert "bindingBlock" not in metadata
    assert metadata["dpiScale"] == 1.5
    runtime._record_observation_screenshot.assert_called_once_with(
        run_handle=run_handle, workspace_path=None, window_title=observed_title, window_handle=456,
    )
    if observed_title == "Requested App":
        assert metadata["bindingAssessment"]["status"] == "verified"
        runtime._request_environment_interrupt.assert_not_called()
    else:
        assert metadata["bindingAssessment"]["status"] == "unresolved"
        runtime._request_environment_interrupt.assert_called_once()
