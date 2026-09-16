from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json
import subprocess
import sys
import threading
import time

import pytest

from core.process_launch import ProcessCancelled, run_windowless_bounded
from runtimes.computer_use.trace_store import ComputerUseTraceStore
from runtimes.computer_use.types import ComputerUseTraceStep, ComputerUseTraceTarget
from runtimes.rpa.capture_v2 import CaptureBroker
from runtimes.rpa.recording import RPARecordingManager, CaptureVerificationRequired
from runtimes.rpa.store import RPAScriptStore, DraftRevisionConflict
from runtimes.rpa.robot_adapter import RobotFrameworkAdapter


def step(key="action-1", handle=123):
    return ComputerUseTraceStep(step_id=key, app_id="fixture", action="click", intent="increment",
                               target=ComputerUseTraceTarget(window={"windowHandle": handle}))


def test_runtime_transcription_deduplicates_stops_and_recovers_crash(tmp_path):
    traces = ComputerUseTraceStore(tmp_path / "traces")
    manager = RPARecordingManager(root_dir=tmp_path / "recordings", trace_store_instance=traces)
    recording = manager.start({"source": "computer_use", "sessionId": "owned", "windowHandle": 123})
    rid = recording["recordingSessionId"]
    traces.append_step(run_id="run", session_id="owned", goal="fixture", runtime_kind="computer_use", step=step())
    manager.record_runtime_step(run_id="run", session_id="other", step=step())
    assert manager.get(rid).get("stepCount", 0) == 0
    for _ in range(2):
        manager.record_runtime_step(run_id="run", session_id="owned", step=step())
    assert manager.get(rid)["stepCount"] == 1
    manager.record_runtime_step(run_id="run", session_id="owned", step=step("wrong-target", 999))
    # Durable original trace landed but projection callback was lost during a crash.
    traces.append_step(run_id="run", session_id="owned", goal="fixture", runtime_kind="computer_use", step=step("recovered"))
    stopped = manager.stop(rid)
    assert stopped["stepCount"] == 2
    manager.record_runtime_step(run_id="run", session_id="owned", step=step("late"))
    assert manager.get(rid)["stepCount"] == 2
    assert [s["stepId"] for s in traces.get_trace(recording["traceRunId"])["steps"]] == ["action-1", "recovered"]


def test_pause_excludes_actions_and_cancel_cannot_be_resurrected(tmp_path):
    traces = ComputerUseTraceStore(tmp_path / "traces")
    manager = RPARecordingManager(root_dir=tmp_path / "recordings", trace_store_instance=traces)
    recording = manager.start({"source": "computer_use", "sessionId": "owned"})
    rid = recording["recordingSessionId"]
    manager.pause(rid)
    traces.append_step(run_id="run", session_id="owned", goal="fixture", runtime_kind="computer_use", step=step("paused"))
    manager.resume(rid)
    manager.record_runtime_step(run_id="run", session_id="owned", step=step("paused"))
    assert manager.stop(rid)["stepCount"] == 0
    manager.cancel(rid)
    assert manager.resume(rid)["state"] == "cancelled"
    traces.append_step(run_id="run", session_id="owned", goal="fixture", runtime_kind="computer_use", step=step("after-cancel"))
    assert manager.stop(rid)["state"] == "cancelled"
    assert manager.stop(rid)["stepCount"] == 0
    with pytest.raises(ValueError, match="not active"):
        manager.add_capture_pool_item(rid, {"tempElementId": "late"})


def test_trace_atomic_concurrent_append_and_duplicate(tmp_path):
    trace = ComputerUseTraceStore(tmp_path)
    def append(index):
        return trace.append_step(run_id="run", session_id="session", goal="fixture", runtime_kind="computer_use", step=step(str(index)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, list(range(16)) * 2))
    saved = trace.get_trace("run")
    assert saved["stepCount"] == 16
    assert [s["index"] for s in saved["steps"]] == list(range(1, 17))


def test_draft_conflict_preserves_winner(tmp_path):
    store = RPAScriptStore(tmp_path)
    first = store.save_draft({"id": "draft", "name": "initial"})
    winner = store.save_draft({**first, "name": "winner"}, expected_updated_at=first["updatedAt"])
    with pytest.raises(DraftRevisionConflict):
        store.save_draft({**first, "name": "late"}, expected_updated_at=first["updatedAt"])
    assert store.get_draft("draft") == winner


def test_callback_auth_proof_sequence_and_save_idempotency(tmp_path):
    manager = RPARecordingManager(root_dir=tmp_path / "recordings", trace_store_instance=ComputerUseTraceStore(tmp_path / "traces"))
    recording = manager.start({"targetMode": "desktop_window"})
    rid = recording["recordingSessionId"]
    broker = CaptureBroker(manager, request_root=tmp_path / "inspector", engine_base_url="http://127.0.0.1:19630/v1")
    started = broker.start_session(rid, {"platform": "windows", "sidecarReady": True})
    sid = started["session"]["sessionId"]
    private = manager.get_inspector_session(rid, sid)
    candidate = {"selector": {"automationId": "increment"}, "proof": {"status": "verified", "verifier": "rpa_replay_verifier_v2"}}
    with pytest.raises(PermissionError):
        broker.ingest_event(rid, sid, {"type": "candidate", "candidate": candidate})
    event = {"type": "candidate", "candidate": candidate, "oneTimeToken": private["oneTimeToken"], "seq": 1}
    result = broker.ingest_event(rid, sid, event)
    item = result["capturePoolItem"]
    assert item["proof"]["status"] == "unverified"
    assert private["oneTimeToken"] not in json.dumps(result)
    assert broker.ingest_event(rid, sid, event)["duplicate"] is True
    with pytest.raises(ValueError, match="sequence gap"):
        broker.ingest_event(rid, sid, {**event, "seq": 3})
    with pytest.raises(CaptureVerificationRequired):
        manager.save_capture_pool_item(rid, item["tempElementId"])
    broker.verifier.resolver = lambda item, payload: {"findCount": 0, "complete": True}
    assert broker.verifier.verify(rid, item["tempElementId"], {"findCount": 1})["status"] == "locator_unresolved"
    broker.verifier.resolver = lambda item, payload: {"findCount": 1, "complete": True}
    assert broker.verifier.verify(rid, item["tempElementId"], {})["ok"]
    saved = manager.save_capture_pool_item(rid, item["tempElementId"])
    assert manager.save_capture_pool_item(rid, item["tempElementId"])["element"]["elementId"] == saved["element"]["elementId"]
    request = json.loads((tmp_path / "inspector" / f"{sid}.request.json").read_text())
    assert request["callback"]["url"].count("/v1/") == 1
    manager.cancel(rid)
    with pytest.raises(ValueError, match="no longer active"):
        broker.ingest_event(rid, sid, {**event, "seq": 2})


@pytest.mark.parametrize("cancel", [False, True])
def test_bounded_process_preserves_single_effect_and_stops_descendant(tmp_path, cancel):
    effect = tmp_path / "effect.txt"
    late = tmp_path / "late.txt"
    child = f"import time,pathlib; time.sleep(2); pathlib.Path({str(late)!r}).write_text('late')"
    program = f"import pathlib,subprocess,sys,time; pathlib.Path({str(effect)!r}).write_text('1'); subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    stopped = threading.Event()
    timer = threading.Timer(0.5, stopped.set)
    timer.start()
    try:
        with pytest.raises(ProcessCancelled if cancel else subprocess.TimeoutExpired):
            run_windowless_bounded([sys.executable, "-c", program], timeout=10 if cancel else 0.5,
                                   cancel_requested=stopped.is_set if cancel else None, capture_output=True)
    finally:
        timer.cancel()
    time.sleep(2.2)
    assert effect.read_text() == "1"
    assert not late.exists()


def test_control_conditions_fail_instead_of_inventing_success():
    adapter = RobotFrameworkAdapter()
    body = {"stepId": "body", "use": "comment", "params": {"text": "body"}}
    for use, params in [("if", {}), ("loop", {"count": -1}), ("loop", {"count": "oops"})]:
        with pytest.raises(ValueError):
            adapter.render_script({"steps": [{"stepId": "control", "use": use, "params": {**params, "bodyStepKeys": ["body"]}}, body]})
    script = adapter.render_script({"steps": [{"stepId": "control", "use": "loop", "params": {"count": 0, "bodyStepKeys": ["body"]}}, body]})
    assert "IN RANGE | 0" in script
    with pytest.raises(ValueError, match="recursively"):
        adapter.render_script({"steps": [{"stepId": "a", "use": "if", "params": {"condition": True, "bodyStepKeys": ["b"]}}, {"stepId": "b", "use": "if", "params": {"condition": True, "bodyStepKeys": ["a"]}}]})


def test_cancel_before_dispatch_never_launches_process(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Cancelled operation launched a process")
    monkeypatch.setattr("core.process_launch.popen_windowless", unexpected)
    with pytest.raises(ProcessCancelled):
        run_windowless_bounded(["fixture"], timeout=1, cancel_requested=lambda: True)


def test_variable_defaults_required_and_credential_boundary():
    adapter = RobotFrameworkAdapter()
    script = {"variables": [{"name": "count", "defaultValue": 0, "required": True}],
              "steps": [{"stepId": "note", "use": "comment", "params": {"text": "fixture"}}]}
    adapter.validate_variable_inputs(script, {})
    assert "${count} | 0" in adapter.render_script(script)
    with pytest.raises(ValueError, match="count"):
        adapter.validate_variable_inputs({"variables": [{"name": "count", "required": True}]}, {})
    with pytest.raises(ValueError, match="Robot") as error:
        adapter.validate_variable_inputs({"variables": [{"name": "key", "sensitive": True}]}, {"key": "fixture-secret"})
    assert "fixture-secret" not in str(error.value)


def test_canvas_template_keeps_action_and_variable_contract():
    from runtimes.rpa.compiler import rpa_trace_compiler
    from runtimes.rpa.runtime import rpa_runtime
    script = {"id": "canvas", "name": "Owned flow", "appId": "desktop", "source": {"kind": "manual_canvas"},
              "steps": [{"stepId": "copy", "action": "file_copy", "params": {"source": "owned", "target": "copy"}}],
              "variables": [{"name": "count", "required": False, "defaultValue": 0},
                            {"name": "key", "sensitive": True, "secretName": "fixture-reference", "exampleValue": "never-copy"}]}
    template = rpa_trace_compiler.build_template_candidate(script_payload=script)
    assert template["name"] == "Owned flow"
    assert template["steps"][0]["use"] == "file_copy"
    assert template["variables"][0]["defaultValue"] == 0
    assert template["variables"][1]["sensitive"] is True
    assert "never-copy" not in json.dumps(template["variables"])
    for provenance in ({"kind": "manual_canvas"}, {"type": "computer_use_trace", "traceRunId": "old-version"}):
        script["source"] = provenance
        script["metadata"] = {"templateGovernance": {"rolloutMode": "computer_use_first"}}
        assert rpa_runtime._resolve_template_execution_policy(mode="draft", prepared={"script": script})["executionPath"] == "robot"


def test_bridge_keeps_window_and_selector_but_not_source_binding_audit():
    adapter = RobotFrameworkAdapter()
    focus = {"use": "focus_window", "params": {"_binding_mode": "exact", "requested_app_id": "old", "class_name_candidates": ["old"]},
             "target": {"window": {"windowHandle": 123, "title": "owned"}, "selector": {"handle": 123, "controlType": "Window"}}}
    row = adapter._custom_keyword_row(focus)
    assert "window_handle=123" in row and "window_title=owned" in row
    assert not any("binding" in value or "requested_app_id" in value or "candidates" in value or "control_type" in value for value in row)
    click = {"use": "click", "target": {"window": {"windowHandle": 123}, "selector": {"automationId": "owned"}}}
    assert "automation_id=owned" in adapter._custom_keyword_row(click)


def test_successful_explicitly_approved_template_is_not_frozen_for_missing_trace_history():
    from runtimes.rpa.template_service import rpa_template_service
    template = {"status": "approved", "governance": {"stage": "approved_at_risk", "signals": {}}}
    assert rpa_template_service._auto_transition_decision(template, execution_state="completed") is None
    assert rpa_template_service._auto_transition_decision(template, execution_state="unknown")["decision"] == "frozen"


def test_sensitive_draft_values_never_reach_storage(tmp_path):
    store = RPAScriptStore(tmp_path)
    with pytest.raises(ValueError):
        store.save_draft({"id": "secret", "variables": [{"name": "key", "sensitive": True, "defaultValue": "fixture-secret"}]})
    assert store.get_draft("secret") is None


def test_distinct_recorded_inputs_keep_distinct_variable_values():
    from runtimes.rpa.compiler import rpa_trace_compiler
    trace = {"runId": "owned", "steps": [{"stepId": f"input-{index}", "appId": "desktop", "action": "type_text",
              "params": {"text": "{{input_text}}"}, "variables": [{"name": "input_text", "placeholder": "{{input_text}}", "exampleValue": value},
                  {"name": "input_text", "originalKey": "prefer_sendinput_text", "exampleValue": False}]}
             for index, value in enumerate(("first", "second"))]}
    script = rpa_trace_compiler.compile_trace(trace).as_dict()
    assert [step["params"]["text"] for step in script["steps"]] == ["{{input_text}}", "{{input_text_2}}"]
    assert [variable["exampleValue"] for variable in script["variables"]] == ["first", "second"]
    assert trace["steps"][1]["variables"][0]["name"] == "input_text"


def test_robot_named_arguments_preserve_text_and_convert_execution_controls():
    from runtimes.rpa.robot_keywords import V8ChatRPAKeywords
    keywords = V8ChatRPAKeywords()
    payload = keywords._robot_kwargs(("clear_first=True",), {"text": "00123", "window_handle": "123", "post_action_stable_rounds": "2"})
    assert payload == {"clear_first": True, "text": "00123", "window_handle": 123, "post_action_stable_rounds": 2}


def test_robot_child_inherits_identity_without_arbitrary_context(tmp_path):
    from erc.runtime_context import bind_runtime_context
    from runtimes.rpa.robot_keywords import V8ChatRPAKeywords
    with bind_runtime_context(runtime_kind="rpa", run_id="parent", session_id="session", user_id="actor", token="must-not-leak"):
        environment = RobotFrameworkAdapter()._robot_child_environment(tmp_path)
        context = json.loads(environment["V8_RPA_PARENT_CONTEXT"])
        assert context["run_id"] == "parent" and context["user_id"] == "actor"
        assert "must-not-leak" not in json.dumps(environment)
        assert V8ChatRPAKeywords()._base_kwargs()["run_id"] == "parent"


def test_input_and_expected_text_do_not_overwrite_each_other():
    from runtimes.computer_use.runtime import computer_use_runtime
    from runtimes.rpa.compiler import rpa_trace_compiler
    params, raw, variables = computer_use_runtime._trace_params(action_payload={"text": "Invoice 123", "post_action_expect_text": "Submitted successfully"})
    trace = {"runId": "owned", "steps": [{"stepId": "input", "action": "type_text", "params": params, "rawParams": raw,
              "variables": [v.as_dict() for v in variables]}]}
    script = rpa_trace_compiler.compile_trace(trace).as_dict()
    values = {"{{" + v["name"] + "}}": v["exampleValue"] for v in script["variables"]}
    assert values[script["steps"][0]["params"]["text"]] == "Invoice 123"
    assert values[script["steps"][0]["params"]["post_action_expect_text"]] == "Submitted successfully"
