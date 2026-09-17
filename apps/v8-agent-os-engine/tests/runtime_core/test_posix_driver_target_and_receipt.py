from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtimes.computer_use.drivers import linux_atspi, mac_ax
from runtimes.computer_use.types import ComputerUseElement


@pytest.fixture(params=[mac_ax.MacAXUIDriver, linux_atspi.LinuxATSPIADriver], ids=["mac", "linux"])
def driver(request, monkeypatch):
    instance = request.param()
    monkeypatch.setattr(instance, "ensure_available", lambda: None)
    return instance


def test_fresh_element_resolution_honors_id_window_and_name(driver, monkeypatch):
    old = ComputerUseElement("element", driver.backend, "entry", "Old", [0, 0, 10, 10], window_handle=10)
    fresh = ComputerUseElement("element", driver.backend, "entry", "New", [0, 0, 10, 10], window_handle=10, metadata={"value": "new text"})
    driver._element_cache[old.element_id] = old
    calls = []
    monkeypatch.setattr(driver, "observe_desktop", lambda **kwargs: calls.append(kwargs) or SimpleNamespace(elements=[fresh]))
    assert driver.find_elements(element_id="element", window_handle=999, name="New") == []
    assert driver.find_elements(element_id="element", window_handle=10, name="Old") == []
    result = driver.find_elements(element_id="element", window_handle=10, name="New")
    assert result == [fresh]
    assert calls and all(call["use_cache"] is False for call in calls)


@pytest.mark.parametrize("action", ["click", "hover", "hotkey", "scroll", "unrecognized_action"])
def test_animation_hash_change_never_proves_action_or_business_success(driver, action):
    result = driver.verify_action(action_type=action, target={}, before_observation={"screenHash": "clock-1"}, after_observation={"screenHash": "clock-2"})
    assert result["passed"] is False
    assert result["level"] == "review_required"


def test_same_title_does_not_override_a_different_window_identity(driver):
    assert not driver._window_matches({"windowTitle": "Editor", "metadata": {"windowHandle": 22}}, title="Editor", handle=11)


@pytest.mark.parametrize("action,kwargs", [
    ("click_point", {"point": [1, 1]}), ("hover_point", {"point": [1, 1]}),
    ("right_click_point", {"point": [1, 1]}),
    ("drag_between_points", {"start_point": [1, 1], "end_point": [2, 2]}),
    ("type_text_in_window", {"text": "fixture"}),
    ("hotkey", {"sequence": "^a"}), ("scroll", {"amount": 120}),
])
def test_mac_target_loss_prevents_global_input(monkeypatch, action, kwargs):
    instance = mac_ax.MacAXUIDriver()
    monkeypatch.setattr(instance, "_ensure_input_granted", lambda: None)
    def lost(**_kwargs):
        raise mac_ax.MacAXUIDriverError("target_lost")
    monkeypatch.setattr(instance, "focus_window", lost)
    calls = []
    monkeypatch.setattr(instance, "_helper_command", lambda *args, **kwargs: calls.append(args) or {})
    monkeypatch.setattr(instance, "foreground_window", lambda: {})
    with pytest.raises(mac_ax.MacAXUIDriverError, match="target_lost"):
        getattr(instance, action)(window_handle=42, **kwargs)
    assert calls == []


def test_linux_target_loss_prevents_coordinate_input(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    monkeypatch.setattr(instance, "_require_coordinate_input", lambda *_: None)
    def lost(**_kwargs):
        raise linux_atspi.LinuxATSPIError("target_lost")
    monkeypatch.setattr(instance, "focus_window", lost)
    calls = []
    monkeypatch.setattr(linux_atspi, "run_command", lambda *args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(instance, "foreground_window", lambda: {})
    with pytest.raises(linux_atspi.LinuxATSPIError, match="target_lost"):
        instance.click_point(point=[1, 1], window_handle=42)
    assert calls == []


def test_linux_failed_move_does_not_click_or_report_success(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    monkeypatch.setattr(instance, "_require_coordinate_input", lambda *_: None)
    monkeypatch.setattr(instance, "foreground_window", lambda: {})
    calls = []
    monkeypatch.setattr(linux_atspi, "run_command", lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=1, stderr="private diagnostic", stdout=""))
    with pytest.raises(linux_atspi.LinuxATSPIError, match="execution_unknown") as caught:
        instance.click_point(point=[1, 1])
    assert len(calls) == 1
    assert "private diagnostic" not in str(caught.value)


def test_linux_unknown_focus_is_not_the_first_window(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    monkeypatch.setattr(instance, "ensure_available", lambda: None)
    monkeypatch.setattr(instance, "_session_type", lambda: "x11")
    monkeypatch.setattr(linux_atspi, "tool_exists", lambda _: True)
    monkeypatch.setattr(linux_atspi, "run_command", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(instance, "list_windows", lambda **kwargs: [{"handle": 12, "title": "Unrelated"}])
    assert instance.foreground_window() is None


def test_linux_semantic_append_preserves_existing_text_and_false_receipt_fails(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    element = ComputerUseElement("entry", "atspi", "entry", "Entry", [0, 0, 10, 10], window_handle=1)
    writes = []
    editable = SimpleNamespace(setTextContents=lambda value: writes.append(value) or True)
    accessible = SimpleNamespace(queryEditableText=lambda: editable, queryText=lambda: SimpleNamespace(getText=lambda *_: "prefix "))
    instance._accessible_cache[element.element_id] = accessible
    assert instance._set_element_text(element, text="suffix", clear_first=False, press_enter=False)
    assert writes == ["prefix suffix"]
    editable.setTextContents = lambda _: False
    with pytest.raises(linux_atspi.LinuxATSPIError, match="execution_unknown"):
        instance._set_element_text(element, text="replacement", clear_first=True, press_enter=False)


def test_linux_enter_failure_after_text_write_cannot_trigger_coordinate_replay(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    element = ComputerUseElement("entry", "atspi", "entry", "Entry", [0, 0, 10, 10], window_handle=1)
    writes = []
    instance._accessible_cache[element.element_id] = SimpleNamespace(queryEditableText=lambda: SimpleNamespace(setTextContents=lambda value: writes.append(value) or True))
    monkeypatch.setattr(instance, "wait_for_element", lambda **kwargs: element)
    def rejected(*args, **kwargs):
        raise linux_atspi.LinuxATSPIError("Enter failed")
    monkeypatch.setattr(instance, "hotkey", rejected)
    monkeypatch.setattr(instance, "type_text_in_window", lambda **kwargs: pytest.fail("text already written must not be replayed"))
    with pytest.raises(linux_atspi.LinuxATSPIError, match="partial_input"):
        instance.type_text(text="once", clear_first=True, press_enter=True)
    assert writes == ["once"]


def test_bound_screenshot_loss_never_expands_to_the_full_desktop(driver, monkeypatch, tmp_path):
    module = mac_ax if isinstance(driver, mac_ax.MacAXUIDriver) else linux_atspi
    if module is mac_ax:
        monkeypatch.setattr(driver, "_ensure_screen_capture_granted", lambda: None)
    else:
        monkeypatch.setattr(driver, "_session_type", lambda: "x11")
    def lost(**_kwargs):
        raise RuntimeError("target_lost")
    monkeypatch.setattr(driver, "wait_for_window", lost)
    monkeypatch.setattr(module, "tool_exists", lambda _: False)
    calls = []
    monkeypatch.setattr(module, "capture_with_mss", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    with pytest.raises(RuntimeError, match="target_lost"):
        driver.capture_screenshot(tmp_path / "bound.png", window_handle=42)
    assert calls == []


def test_bound_screenshot_passes_exact_handle_and_bounds_to_capture(driver, monkeypatch, tmp_path):
    module = mac_ax if isinstance(driver, mac_ax.MacAXUIDriver) else linux_atspi
    if module is mac_ax:
        monkeypatch.setattr(driver, "_ensure_screen_capture_granted", lambda: None)
    else:
        monkeypatch.setattr(driver, "_session_type", lambda: "x11")
    bindings, captures = [], []
    monkeypatch.setattr(driver, "wait_for_window", lambda **kwargs: bindings.append(kwargs) or {"handle": 42, "bounds": [20, 30, 120, 130]})
    monkeypatch.setattr(module, "capture_with_mss", lambda output, **kwargs: captures.append(kwargs) or {"path": str(output)})
    driver.capture_screenshot(tmp_path / "exact.png", window_handle=42)
    assert bindings[0]["window_handle"] == 42
    assert captures == [{"bounds": [20, 30, 120, 130]}]


def test_bound_element_without_bounds_cannot_fall_back_to_full_screen(driver, monkeypatch, tmp_path):
    module = mac_ax if isinstance(driver, mac_ax.MacAXUIDriver) else linux_atspi
    if module is mac_ax:
        monkeypatch.setattr(driver, "_ensure_screen_capture_granted", lambda: None)
    else:
        monkeypatch.setattr(driver, "_session_type", lambda: "x11")
    element = ComputerUseElement("entry", driver.backend, "entry", "Entry", [], window_handle=42)
    monkeypatch.setattr(driver, "find_elements", lambda **kwargs: [element])
    monkeypatch.setattr(module, "capture_with_mss", lambda *args, **kwargs: pytest.fail("missing element bounds must not capture desktop"))
    with pytest.raises(RuntimeError, match="target_lost"):
        driver.capture_screenshot(tmp_path / "empty.png", element_id="entry", window_handle=42)


def test_text_verification_requires_fresh_value_on_the_same_element_and_window(driver):
    target = {"windowHandle": 42, "elementId": "entry"}
    observation = {"metadata": {"windowHandle": 42}, "elements": [{"elementId": "other", "name": "expected", "metadata": {"value": "expected"}}]}
    assert not driver.verify_action(action_type="type_text", target=target, text="expected", after_observation=observation)["passed"]
    observation["elements"][0]["elementId"] = "entry"
    assert driver.verify_action(action_type="type_text", target=target, text="expected", after_observation=observation)["passed"]
    observation["metadata"]["windowHandle"] = 43
    assert not driver.verify_action(action_type="type_text", target=target, text="expected", after_observation=observation)["passed"]


def test_linux_drag_failure_releases_button_without_replaying_motion(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    monkeypatch.setattr(instance, "_require_coordinate_input", lambda *_: None)
    calls = []
    def command(argv, **kwargs):
        calls.append(argv[1])
        return SimpleNamespace(returncode=1 if len(calls) == 3 else 0)
    monkeypatch.setattr(linux_atspi, "run_command", command)
    with pytest.raises(linux_atspi.LinuxATSPIError, match="execution_unknown"):
        instance.drag_between_points(start_point=[1, 1], end_point=[2, 2])
    assert calls == ["mousemove", "mousedown", "mousemove", "mouseup"]


def test_linux_semantic_action_failure_does_not_fall_back_to_coordinate_click(monkeypatch):
    instance = linux_atspi.LinuxATSPIADriver()
    element = ComputerUseElement("button", "atspi", "button", "Button", [0, 0, 10, 10], window_handle=1)
    instance._accessible_cache[element.element_id] = SimpleNamespace(queryAction=lambda: SimpleNamespace(nActions=1, getName=lambda _: "click", doAction=lambda _: False))
    monkeypatch.setattr(instance, "wait_for_element", lambda **kwargs: element)
    monkeypatch.setattr(instance, "click_point", lambda **kwargs: pytest.fail("unconfirmed semantic invocation must not be replayed"))
    with pytest.raises(linux_atspi.LinuxATSPIError, match="execution_unknown"):
        instance.click_element(element_id=element.element_id)


def test_mac_helper_transport_loss_after_dispatch_is_unknown_and_sanitized(monkeypatch):
    instance = mac_ax.MacAXUIDriver()
    monkeypatch.setattr(instance, "_ensure_helper_binary", lambda: "fixture-helper")
    def timeout(*args, **kwargs):
        raise TimeoutError("private payload")
    monkeypatch.setattr(mac_ax, "json_command", timeout)
    with pytest.raises(mac_ax.MacAXUIDriverError, match="execution_unknown") as caught:
        instance._helper_command("type_text", {"text": "private payload"})
    assert "private payload" not in str(caught.value)


def test_mac_enter_failure_preserves_known_text_dispatch(monkeypatch):
    instance = mac_ax.MacAXUIDriver()
    monkeypatch.setattr(instance, "_ensure_input_granted", lambda: None)
    monkeypatch.setattr(instance, "_focus_bound_input", lambda **kwargs: None)
    calls = []
    monkeypatch.setattr(instance, "_helper_command", lambda command, payload, **kwargs: calls.append((command, payload)) or {"textLength": 4})
    def failed(*args, **kwargs):
        raise mac_ax.MacAXUIDriverError("Enter failed")
    monkeypatch.setattr(instance, "hotkey", failed)
    with pytest.raises(mac_ax.MacAXUIDriverError, match="partial_input"):
        instance.type_text_in_window(text="once", press_enter=True)
    assert len(calls) == 1
    assert calls[0][1]["press_enter"] is False
