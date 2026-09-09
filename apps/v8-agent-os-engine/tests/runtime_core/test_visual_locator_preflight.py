from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from runtimes.computer_use import visual_locator_runtime as visual


@pytest.mark.parametrize("tesseract", [False, True])
def test_missing_optional_rpa_parent_is_reported_without_hiding_independent_ocr(monkeypatch, tesseract):
    def missing(_name):
        raise ModuleNotFoundError("No module named 'RPA'")

    runtime = visual.RPADesktopVisualLocatorRuntime()
    monkeypatch.setattr(visual.importlib.util, "find_spec", missing)
    monkeypatch.setattr(runtime, "_desktop_class", lambda: missing("RPA.Desktop"))
    monkeypatch.setattr(visual, "_resolve_tesseract_executable", lambda: "fixture-tesseract" if tesseract else None)
    monkeypatch.setattr(visual, "_available_tesseract_languages", lambda: ["eng"])
    monkeypatch.setattr(visual, "_preferred_tesseract_lang", lambda: "eng")
    result = runtime.availability_summary()
    assert result["status"] == "missing_dependency"
    assert result["runtimeAvailable"] is False
    assert result["supportsOcrLocator"] is tesseract
    assert result["supportsReadText"] is tesseract
    assert result["recognitionAvailable"] is False
    assert runtime._recognition_templates_module() is None


@pytest.mark.parametrize("locator,captured,read_text,availability,missing", [
    ("OwnedInput", False, False, {}, ["RPA.Desktop"]),
    ("configured-submit", False, False, {"runtimeAvailable": True}, []),
    ("ocr:Ready", True, False, {"tesseractAvailable": True}, []),
    ("text:Ready", True, False, {}, ["Tesseract"]),
    ("ocr:Ready", False, False, {"tesseractAvailable": True}, ["RPA.Desktop", "RPA.recognition"]),
    ("image:fixture.png", True, False, {"recognitionAvailable": True}, []),
    ("image:fixture.png", True, True, {"recognitionAvailable": True}, ["Tesseract"]),
])
def test_preflight_tracks_the_requested_locator_path(locator, captured, read_text, availability, missing):
    result = visual.visual_locator_dependency_status(locator, availability,
                                                     captured_image=captured, read_text=read_text)
    assert result["missingDependencies"] == missing
    assert result["ok"] is (not missing)


def test_existing_captured_ocr_does_not_import_rpa(monkeypatch, tmp_path):
    from PIL import Image
    frame = tmp_path / "owned-frame.png"
    Image.new("RGB", (20, 20), "white").save(frame)
    runtime = visual.RPADesktopVisualLocatorRuntime()
    monkeypatch.setattr(runtime, "_desktop_class", lambda: pytest.fail("Captured OCR must not import RPA"))
    observed = []
    monkeypatch.setattr(visual, "_locate_ocr_query_in_image", lambda **kw:
                        observed.append(kw) or {"matchCount": 1, "matches": [{"bbox": [0, 0, 10, 10]}]})
    result = runtime.locate(locator="ocr:Ready", search_image_path=str(frame))
    assert result["matchCount"] == 1
    assert observed[0]["query"] == "Ready"


def _fake_runtime(monkeypatch, native, availability):
    executed = []
    checks = []
    provider = SimpleNamespace(availability_summary=lambda: checks.append(True) or availability)
    runtime = SimpleNamespace(visual_locator_runtime=provider,
        execute_plan=lambda **kw: executed.append(kw) or {"result": {
            "status": "completed", "verification": {"passed": True, "status": "verified"}}})
    monkeypatch.setattr(native, "_get_computer_use_runtime", lambda: runtime)
    monkeypatch.setattr(native, "_computer_use_runtime_kwargs", lambda *_: {})
    return executed, checks


@pytest.mark.parametrize("field", ["visual_locator", "visual_locator_scope", "post_action_visual_locator", "start_visual_locator", "end_visual_locator"])
def test_missing_visual_dependency_blocks_before_plan_actions_or_model_recovery(monkeypatch, field):
    native = importlib.import_module("core.tools.native.computer_use")
    executed, checks = _fake_runtime(monkeypatch, native, {})
    step = {"action": "type_text", "automation_id": "OwnedInput", field: "OwnedInput"}
    result = native._computer_use_execute_single_step(action="type_text", step=step, goal="owned fixture")
    assert executed == []
    assert checks == [True]
    assert result["result"]["verification"]["status"] == "missing_dependency"
    assert result["result"]["verification"]["recoverable"] is True
    assert result["result"]["metadata"]["dependencyPreflight"]["field"] == field
    assert step[field] == "OwnedInput"  # The requested verification was not silently discarded.


@pytest.mark.parametrize("locator", [None, "configured-submit", "OwnedInput"])
def test_uia_needs_no_visual_probe_and_valid_named_locators_are_preserved(monkeypatch, locator):
    native = importlib.import_module("core.tools.native.computer_use")
    executed, checks = _fake_runtime(monkeypatch, native, {"runtimeAvailable": True})
    step = {"action": "click", "automation_id": "OwnedSubmit"}
    if locator:
        step["post_action_visual_locator"] = locator
    native._computer_use_execute_single_step(action="click", step=step, goal="owned fixture")
    assert len(executed) == 1
    assert checks == ([True] if locator else [])
    assert executed[0]["steps"] == [step]


def test_explicit_post_action_text_check_is_not_skipped_when_ocr_is_missing(monkeypatch):
    native = importlib.import_module("core.tools.native.computer_use")
    executed, _checks = _fake_runtime(monkeypatch, native, {"runtimeAvailable": True})
    result = native._computer_use_execute_single_step(action="click", goal="owned fixture", step={
        "action": "click", "post_action_visual_locator": "configured-status",
        "post_action_expect_text": "Ready",
    })
    assert executed == []
    assert result["result"]["metadata"]["dependencyPreflight"]["missingDependencies"] == ["Tesseract"]


@pytest.mark.parametrize("tool_name,args", [
    ("computer_use_input_text", {"text": "owned fixture", "automation_id": "OwnedInput", "control_type": "Edit"}),
    ("computer_use_click_target", {"automation_id": "OwnedSubmit", "control_type": "Button"}),
])
def test_public_action_returns_recoverable_dependency_error_without_executing(monkeypatch, tool_name, args):
    native = importlib.import_module("core.tools.native.computer_use")
    executed, _checks = _fake_runtime(monkeypatch, native, {})
    monkeypatch.setattr(native, "_computer_use_resolve_app", lambda *_: None)
    monkeypatch.setattr(native, "_desktop_route_gate", lambda **_: (True, None, {}))
    monkeypatch.setattr(native, "_computer_use_prebind_window", lambda **_: (None, "Owned Window", 42, None))
    monkeypatch.setattr(native, "_computer_use_action_guard", lambda **_: (True, None))
    monkeypatch.setattr(native, "_desktop_route_merge_into_response", lambda response, **_: response)
    output = getattr(native, tool_name).invoke({"type": "tool_call", "id": "owned-call", "name": tool_name,
        "args": {**args, "window_title": "Owned Window", "post_action_visual_locator": "OwnedInput"}})
    payload = json.loads(output.content)
    assert payload["status"] == "blocked"
    assert payload["ok"] is False
    assert payload["verification"]["status"] == "missing_dependency"
    assert payload["verification"]["recoverable"] is True
    assert "动作尚未执行" in payload["summary"]
    assert executed == []


def test_public_locator_parameter_schema_explains_its_identity_and_dependency_contract():
    native = importlib.import_module("core.tools.native.computer_use")
    for name in ("computer_use_input_text", "computer_use_click_target"):
        properties = convert_to_openai_tool(getattr(native, name))["function"]["parameters"]["properties"]
        assert "NOT a UIA automation_id" in properties["visual_locator"]["description"]
        assert "existing configured RPA locator name" in properties["post_action_visual_locator"]["description"]
        assert "before execution" in properties["post_action_visual_locator"]["description"]
