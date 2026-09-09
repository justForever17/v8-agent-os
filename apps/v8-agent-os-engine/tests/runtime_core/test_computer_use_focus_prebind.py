from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from runtimes.computer_use.runtime import ComputerUseRuntime


def _focus_result(*, status="completed", passed=True, level="verified"):
    return {"result": {"status": status, "message": "Focus outcome",
        "target": {"windowTitle": "Owned Window", "windowHandle": 42},
        "verification": {"passed": passed, "status": "focus_check", "level": level,
                         "reason": "Focus outcome"}}}


def _prebind_runtime(monkeypatch, result):
    native = importlib.import_module("core.tools.native.computer_use")
    calls = []
    runtime = SimpleNamespace(focus_window=lambda **kw: calls.append(kw) or result,
        driver=SimpleNamespace(focus_window=lambda **_: pytest.fail("No unaudited second focus is allowed")),
        execute_plan=lambda **_: pytest.fail("A failed focus must not proceed to a desktop action"))
    monkeypatch.setattr(native, "_get_computer_use_runtime", lambda: runtime)
    monkeypatch.setattr(native, "_computer_use_runtime_kwargs", lambda *_: {})
    return native, calls


@pytest.mark.parametrize("result", [
    _focus_result(status="blocked"),
    _focus_result(passed=False),
    _focus_result(level="review_required"),
    {"status": "failed", "error": "focus unavailable"},
])
def test_prebind_respects_failed_focus_without_updating_binding_or_refocusing(monkeypatch, result):
    native, calls = _prebind_runtime(monkeypatch, result)
    monkeypatch.setattr(native, "_computer_use_update_resolved_app_from_raw_result",
                        lambda **_: pytest.fail("Failed focus must not publish a successful binding"))
    app = {"appId": "owned-app"}
    actual_app, title, handle, error = native._computer_use_prebind_window(
        action_name="owned", app_query="owned-app", resolved_app=app, window_title="Owned Window", window_handle=42)
    assert actual_app is app and title == "Owned Window" and handle == 42
    assert json.loads(error)["ok"] is False
    assert len(calls) == 1


def test_successful_governed_focus_is_not_repeated_by_the_raw_driver(monkeypatch):
    native, calls = _prebind_runtime(monkeypatch, _focus_result())
    _app, title, handle, error = native._computer_use_prebind_window(
        action_name="owned", app_query=None, resolved_app=None, window_title="Owned Window", window_handle=42)
    assert error is None and title == "Owned Window" and handle == 42
    assert len(calls) == 1
    assert calls[0]["prefer_fast_path"] is True


def test_public_input_does_not_continue_after_a_blocked_focus(monkeypatch):
    native, calls = _prebind_runtime(monkeypatch, _focus_result(status="blocked", passed=False))
    monkeypatch.setattr(native, "_computer_use_resolve_app", lambda *_: None)
    monkeypatch.setattr(native, "_desktop_route_gate", lambda **_: (True, None, {}))
    monkeypatch.setattr(native, "_computer_use_action_guard",
                        lambda **_: pytest.fail("Input must stop at the failed focus receipt"))
    result = native.computer_use_input_text.invoke({"type": "tool_call", "id": "owned-input", "name": "computer_use_input_text",
        "args": {"text": "fixture", "window_title": "Owned Window", "automation_id": "OwnedInput", "control_type": "Edit"}})
    assert json.loads(result.content)["status"] == "blocked"
    assert len(calls) == 1


@pytest.mark.parametrize("foreground,observed,target,blocker,expected", [
    ({"handle": 42, "title": "Owned Window"}, 42, 42, "none", True),
    ({"handle": 99, "title": "Owned Window"}, 42, 42, "none", False),
    (None, 42, 42, "none", False),
    ({"handle": 42}, None, 42, "none", False),
    ({"handle": 42}, 42, None, "none", False),
    ({"handle": 42}, 99, 42, "none", False),
    ({"handle": 42}, 42, 42, "confirmation_required", False),
    ({"handle": 42}, 42, 42, None, False),
])
def test_focus_skip_requires_the_current_foreground_and_resolved_observation_to_match(
    foreground, observed, target, blocker, expected,
):
    runtime = object.__new__(ComputerUseRuntime)
    runtime.driver = SimpleNamespace(foreground_window=lambda: foreground)
    assert runtime._should_skip_for_already_in_target_state(action_type="focus_window",
        scene_assessment={"blockerState": blocker, "transitionState": "already_in_target_state"},
        action_payload={"window_handle": target, "require_visual_guard": False},
        before_observation={"metadata": {"windowHandle": observed}}) is expected


def test_foreground_reuse_does_not_skip_an_explorer_directory_navigation():
    runtime = object.__new__(ComputerUseRuntime)
    runtime.driver = SimpleNamespace(foreground_window=lambda: {"handle": 42})
    assert not runtime._should_skip_for_already_in_target_state(action_type="focus_window",
        scene_assessment={"blockerState": "none", "transitionState": "already_in_target_state"},
        action_payload={"window_handle": 42, "target_path": "requested-directory", "require_visual_guard": False},
        before_observation={"metadata": {"windowHandle": 42}})


@pytest.mark.parametrize("guard", [True, None])
def test_foreground_reuse_does_not_skip_a_requested_or_unresolved_visual_guard(guard):
    runtime = object.__new__(ComputerUseRuntime)
    runtime.driver = SimpleNamespace(foreground_window=lambda: {"handle": 42})
    assert not runtime._should_skip_for_already_in_target_state(action_type="focus_window",
        scene_assessment={"blockerState": "none", "transitionState": "already_in_target_state"},
        action_payload={"window_handle": 42, "require_visual_guard": guard},
        before_observation={"metadata": {"windowHandle": 42}})


@pytest.mark.parametrize("command,status", [
    ("cancel", "cancelled"), ("pause", "paused"), ("interrupt", "interrupted"), ("approval_rejected", "paused"),
])
@pytest.mark.parametrize("prior_steps", [[], [{"status": "completed", "result": {"result": {
    "status": "completed", "verification": {"passed": True}, "message": "An earlier step completed."}}}]])
def test_canonical_controlled_result_remains_truthful_through_native_and_agent_surfaces(monkeypatch, command, status, prior_steps):
    from erc.runtime_control import control_payload
    from core.tool_surface import _decision_agent_visible_surface

    native = importlib.import_module("core.tools.native.computer_use")
    runtime = object.__new__(ComputerUseRuntime)
    monkeypatch.setattr(runtime, "_refresh_snapshot", lambda **_: {"run": {"status": status}})
    # This is the actual producer used by the owned-window cancellation probe,
    # including its top-level control/status envelope and optional prior steps.
    raw = runtime._build_controlled_result(run_handle=SimpleNamespace(session_id="owned-session", run_id="owned-run"),
        signal=control_payload({"command": command, "reason": "owned fixture cancellation counterexample"}), steps=prior_steps)
    compact = native._computer_use_compact_response(action="input_text", raw_result=raw)
    payload = json.loads(compact)
    assert payload["status"] == status
    assert payload["ok"] is False and payload["verification"]["passed"] is False
    assert payload["priorStepCount"] == len(prior_steps)
    assert payload["control"] == {key: raw["control"][key] for key in ("command", "reason", "status")}
    assert "snapshot" not in payload  # The compact surface does not dump runtime state.
    assert payload["sessionId"] == "owned-session" and payload["runId"] == "owned-run"
    visible = _decision_agent_visible_surface(tool_name="computer_use_input_text", content=compact,
        raw_ref="toolobs://owned-fixture", budget=1800)
    assert f"Status: {status}" in visible
    assert "Status: failed" not in visible and "Status: completed" not in visible
    assert "owned fixture cancellation counterexample" in visible
    assert "not proof of task completion" in visible


def test_controlled_focus_preserves_cancelled_status_and_blocks_real_public_input_path(monkeypatch):
    raw = {"status": "cancelled", "sessionId": "owned-session", "runId": "owned-run",
           "control": {"command": "cancel", "status": "cancelled", "reason": "owned fixture cancellation counterexample"},
           "snapshot": {"run": {"status": "cancelled"}}}
    native, calls = _prebind_runtime(monkeypatch, raw)
    monkeypatch.setattr(native, "_computer_use_resolve_app", lambda *_: None)
    monkeypatch.setattr(native, "_desktop_route_gate", lambda **_: (True, None, {}))
    monkeypatch.setattr(native, "_computer_use_action_guard", lambda **_: pytest.fail("No input after cancelled focus"))
    result = native.computer_use_input_text.invoke({"type": "tool_call", "id": "owned-cancelled-input", "name": "computer_use_input_text",
        "args": {"text": "MUST-NOT-BE-TYPED", "window_title": "Owned Window", "automation_id": "OwnedInput"}})
    payload = json.loads(result.content)
    assert payload["status"] == "cancelled" and payload["ok"] is False
    assert calls and payload["control"]["command"] == "cancel"


def test_blocked_control_is_not_a_parse_error_or_a_success(monkeypatch):
    native = importlib.import_module("core.tools.native.computer_use")
    payload = json.loads(native._computer_use_compact_response(action="click", raw_result={
        "status": "blocked", "control": {"status": "blocked", "reason": "fixture permission boundary"}}))
    assert payload["status"] == "blocked" and payload["blocked"] is True and payload["ok"] is False


def test_an_unrecognized_or_contradictory_control_envelope_does_not_claim_success():
    native = importlib.import_module("core.tools.native.computer_use")
    for raw in [{"status": "completed", "control": {"status": "completed"}},
                {"status": "cancelled", "control": {"status": "completed"}}]:
        payload = json.loads(native._computer_use_compact_response(action="click", raw_result=raw))
        assert payload["ok"] is False and payload["status"] == "error"
