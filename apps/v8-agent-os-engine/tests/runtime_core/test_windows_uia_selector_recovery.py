from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from runtimes.computer_use.drivers.windows_uia import WindowsUIADriver, WindowsUIADriverError
from runtimes.computer_use.types import ComputerUseElement


def _element(**changes):
    values = dict(element_id="old-submit", backend="windows_uia", role="Button", name="Submit",
                  bounds=[0, 0, 20, 20], automation_id="OwnedSubmit", class_name="Button",
                  window_handle=42, metadata={"handle": 7})
    values.update(changes)
    return ComputerUseElement(**values)


class _Wrapper:
    def __init__(self, element):
        self.element = element
        self.element_info = SimpleNamespace(handle=element.metadata.get("handle"), name=element.name,
                                            automation_id=element.automation_id,
                                            control_type=element.role, class_name=element.class_name)

    def top_level_parent(self):
        return SimpleNamespace(element_info=SimpleNamespace(handle=self.element.window_handle))

    def window_text(self):
        return self.element.name

    def rectangle(self):
        return SimpleNamespace(**dict(zip(("left", "top", "right", "bottom"), self.element.bounds)))

    def parent(self):
        return None

    def is_visible(self):
        return True

    def is_enabled(self):
        return True


@pytest.fixture
def driver():
    # Only OS wrapper acquisition is replaced; element construction, identity
    # matching, recovery and the click dispatch path all use production code.
    return WindowsUIADriver()


@pytest.mark.parametrize("changes", [
    {"automation_id": "OwnedInput", "role": "Edit", "class_name": "Edit"},
    {"automation_id": "OtherSubmit"},
    {"automation_id": ""},
    {"role": "Edit"},
    {"class_name": "OtherButton"},
    {"window_handle": 99},
])
def test_reused_handle_cannot_override_observed_control_identity(driver, monkeypatch, changes):
    expected = _element()
    replacement = _Wrapper(replace(expected, **changes))
    monkeypatch.setattr(driver, "_desktop_for_backend", lambda _: SimpleNamespace(
        window=lambda **_: SimpleNamespace(wrapper_object=lambda: replacement)))
    assert driver._resolve_wrapper_by_handle(expected=expected) is None


@pytest.mark.parametrize("same_id,same_handle", [(True, False), (False, True), (False, False)])
def test_recovery_shortcuts_do_not_override_different_automation_id(driver, same_id, same_handle):
    expected = _element()
    other = replace(expected, element_id=expected.element_id if same_id else "other",
                    automation_id="OtherSubmit", metadata={"handle": 7 if same_handle else 8})
    assert driver._wrapper_candidate_matches(other, expected) is False
    assert driver._pick_best_wrapper([_Wrapper(other)], expected=expected) is None


def test_click_never_focuses_or_acts_on_a_reused_cached_handle(driver, monkeypatch):
    expected = _element()
    wrong = _Wrapper(replace(expected, element_id="input", automation_id="OwnedInput", role="Edit"))
    root = SimpleNamespace(element_info=SimpleNamespace(handle=42))
    driver._cache_window_index(42, elements=[expected], observed_at=time.time())
    monkeypatch.setattr(driver, "_resolve_root", lambda **_: root)
    monkeypatch.setattr(driver, "_desktop_for_backend", lambda _: SimpleNamespace(
        window=lambda **_: SimpleNamespace(wrapper_object=lambda: wrong)))
    monkeypatch.setattr(driver, "_query_wrappers_fast", lambda *_a, **_k: [])
    monkeypatch.setattr(driver, "_find_elements_via_backend_scan", lambda **_: [])
    effects = []
    monkeypatch.setattr(driver, "_focus_wrapper", lambda *_: effects.append("focus"))
    monkeypatch.setattr(driver, "_perform_click_strategy", lambda *_a, **_k: effects.append("click") or "mock")
    with pytest.raises(WindowsUIADriverError):
        driver.click_element(window_handle=42, automation_id="OwnedSubmit", control_type="Button")
    assert effects == []


@pytest.mark.parametrize("replacement_matches", [False, True])
def test_fresh_observation_recovery_preserves_original_selector(driver, monkeypatch, replacement_matches):
    expected = _element(metadata={})
    replacement = replace(expected, element_id="new", metadata={"handle": 8},
                          automation_id="OwnedSubmit" if replacement_matches else "OtherSubmit")
    wrapper = _Wrapper(replacement)
    root = SimpleNamespace(element_info=SimpleNamespace(handle=42))
    monkeypatch.setattr(driver, "_resolve_root", lambda **_: root)
    monkeypatch.setattr(driver, "_resolve_wrapper_by_handle", lambda **_: None)
    monkeypatch.setattr(driver, "_find_cached_elements", lambda **_: [])
    monkeypatch.setattr(driver, "_scan_wrapper_tree_for_element", lambda *_a, **_k: None)
    monkeypatch.setattr(driver, "observe_desktop", lambda **_: SimpleNamespace(elements=[replacement]))
    monkeypatch.setattr(driver, "_query_wrappers_fast", lambda *_a, **_k: [wrapper])
    if replacement_matches:
        assert driver._resolve_wrapper_from_element(expected) is wrapper
    else:
        with pytest.raises(WindowsUIADriverError):
            driver._resolve_wrapper_from_element(expected)


def test_exact_identity_still_recovers_changed_text_and_handle(driver, monkeypatch):
    expected = _element(role="Edit", class_name="Edit", automation_id="OwnedInput", name="old text")
    current = replace(expected, element_id="new", name="new text", metadata={"handle": 8})
    wrapper = _Wrapper(current)
    assert driver._wrapper_candidate_matches(current, expected) is True
    assert driver._pick_best_wrapper([wrapper], expected=expected) is wrapper
    monkeypatch.setattr(driver, "_desktop_for_backend", lambda _: SimpleNamespace(
        window=lambda **_: SimpleNamespace(wrapper_object=lambda: wrapper)))
    assert driver._resolve_wrapper_by_handle(expected=current) is wrapper


def test_role_only_recovery_retains_legacy_unique_name_matching(driver):
    expected = _element(automation_id="", metadata={})
    current = replace(expected, element_id="new", metadata={"handle": 8})
    assert driver._wrapper_candidate_matches(current, expected) is True
    assert driver._pick_best_wrapper([_Wrapper(current)], expected=expected).element is current


def test_cached_click_uses_current_bounds_and_returns_actual_control(driver, monkeypatch):
    expected = _element()
    current = replace(expected, element_id="moved", bounds=[100, 100, 140, 140])
    wrapper = _Wrapper(current)
    monkeypatch.setattr(driver, "_resolve_target_with_recovery", lambda **_: (wrapper, expected))
    monkeypatch.setattr(driver, "_focus_wrapper", lambda *_: None)
    effects = []
    monkeypatch.setattr(driver, "_perform_click_strategy", lambda **kw:
                        effects.append(list(kw["element"].bounds)) or "mock")
    actual = driver.click_element(window_handle=42, automation_id="OwnedSubmit", control_type="Button")
    assert effects == [[100, 100, 140, 140]]
    assert actual.element_id == driver._build_element(wrapper).element_id
    assert actual.automation_id == "OwnedSubmit"


def test_recovery_cannot_relax_current_explicit_name(driver, monkeypatch):
    expected = _element()
    current = replace(expected, name="Different action")
    monkeypatch.setattr(driver, "_resolve_target_with_recovery", lambda **_: (_Wrapper(current), expected))
    with pytest.raises(WindowsUIADriverError, match="选择条件"):
        driver._resolve_target(window_handle=42, automation_id="OwnedSubmit", name="Submit")


def test_unknown_element_id_never_falls_through_to_an_arbitrary_control(driver, monkeypatch):
    resolved = []
    monkeypatch.setattr(driver, "_resolve_target_with_recovery", lambda **_: resolved.append(True) or
                        (_Wrapper(_element()), _element()))
    with pytest.raises(WindowsUIADriverError):
        driver._resolve_target(element_id="expired-observation")
    assert resolved == []
