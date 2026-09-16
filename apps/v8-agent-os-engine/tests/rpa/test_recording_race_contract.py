from __future__ import annotations

from copy import deepcopy

import pytest

from runtimes.computer_use.trace_store import ComputerUseTraceStore
from runtimes.computer_use.types import ComputerUseTraceStep
from runtimes.rpa.capture_v2 import CaptureBroker
from runtimes.rpa.recording import RPARecordingManager


def _fixture(tmp_path):
    traces = ComputerUseTraceStore(tmp_path / "traces")
    manager = RPARecordingManager(root_dir=tmp_path / "recordings", trace_store_instance=traces)
    return traces, manager


def _step(key):
    return ComputerUseTraceStep(step_id=key, app_id="fixture", action="click", intent="fixture")


def test_same_second_pause_resume_rejects_a_late_source_callback(tmp_path, monkeypatch):
    # A timestamp filter alone cannot distinguish all three operations.
    stamp = "2026-09-16T00:00:00+00:00"
    monkeypatch.setattr("runtimes.rpa.recording.utc_now_iso", lambda: stamp)
    monkeypatch.setattr("runtimes.computer_use.trace_store.utc_now_iso", lambda: stamp)
    traces, manager = _fixture(tmp_path)
    recording = manager.start({"source": "computer_use", "sessionId": "fixture"})
    rid = recording["recordingSessionId"]
    manager.pause(rid)
    paused = _step("during-pause")
    traces.append_step(run_id="run", session_id="fixture", goal="fixture", runtime_kind="computer_use", step=paused)
    manager.resume(rid)
    manager.record_runtime_step(run_id="run", session_id="fixture", step=paused)
    assert manager.get(rid).get("stepCount", 0) == 0
    active = _step("after-resume")
    traces.append_step(run_id="run", session_id="fixture", goal="fixture", runtime_kind="computer_use", step=active)
    manager.record_runtime_step(run_id="run", session_id="fixture", step=active)
    manager.stop(rid)
    assert [step["stepId"] for step in traces.get_trace(recording["traceRunId"])["steps"]] == ["after-resume"]


def test_cancel_then_repeated_stop_cannot_recover_actions_after_cancellation(tmp_path):
    traces, manager = _fixture(tmp_path)
    recording = manager.start({"source": "computer_use", "sessionId": "fixture"})
    rid = recording["recordingSessionId"]
    manager.cancel(rid)
    assert manager.stop(rid)["state"] == "cancelled"
    traces.append_step(run_id="run", session_id="fixture", goal="fixture", runtime_kind="computer_use", step=_step("after-cancel"))
    assert manager.stop(rid)["state"] == "cancelled"
    assert not (traces.get_trace(recording["traceRunId"]) or {}).get("steps")


def test_stale_inspector_write_cannot_undo_stop_or_reenable_its_token(tmp_path):
    _, manager = _fixture(tmp_path)
    recording = manager.start({"targetMode": "desktop_window"})
    rid = recording["recordingSessionId"]
    broker = CaptureBroker(manager, request_root=tmp_path / "requests", engine_base_url="http://127.0.0.1:19630/v1")
    started = broker.start_session(rid, {"platform": "windows", "sidecarReady": True})
    sid = started["session"]["sessionId"]
    stale = deepcopy(manager.get_inspector_session(rid, sid))
    assert broker.stop(rid)["ok"]
    stale.update(state="candidate_received", status="candidate_received")
    with pytest.raises(ValueError):
        manager.upsert_inspector_session(rid, stale)
    assert manager.get_inspector_session(rid, sid)["state"] == "stopped"
    with pytest.raises(ValueError, match="no longer active"):
        broker.ingest_event(rid, sid, {"type": "heartbeat", "token": stale["oneTimeToken"], "seq": 1})


def test_recovering_an_earlier_missing_step_preserves_source_execution_order(tmp_path):
    traces, manager = _fixture(tmp_path)
    recording = manager.start({"source": "computer_use", "sessionId": "fixture"})
    first, second = _step("type-first"), _step("submit-second")
    for item in [first, second]:
        traces.append_step(run_id="run", session_id="fixture", goal="fixture", runtime_kind="computer_use", step=item)
    # A crash lost the first projection, but delivery of the next step resumed.
    manager.record_runtime_step(run_id="run", session_id="fixture", step=second)
    manager.stop(recording["recordingSessionId"])
    assert [step["stepId"] for step in traces.get_trace(recording["traceRunId"])["steps"]] == ["type-first", "submit-second"]


def _record_trace_fixture(tmp_path, monkeypatch, *, payload, target, verification, result_metadata=None):
    from types import SimpleNamespace
    from runtimes.computer_use import runtime as module
    from runtimes.computer_use.types import ComputerUseActionResult, ComputerUseTraceRecovery
    from runtimes.rpa.runtime import rpa_runtime

    runtime = module.ComputerUseRuntime.__new__(module.ComputerUseRuntime)
    runtime.trace_store = ComputerUseTraceStore(tmp_path / "trace")
    runtime._infer_app_id_from_payloads = lambda **kwargs: "fixture"
    runtime._trace_recovery = lambda **kwargs: ComputerUseTraceRecovery()
    runtime._trace_phase = lambda **kwargs: "action"
    runtime._trace_signals = lambda **kwargs: {}
    runtime._should_abort_on_major_deviation = lambda **kwargs: False
    runtime._binding_metadata = lambda _: {}
    monkeypatch.setattr(module, "promotion_allowed_for_invocation", lambda **kwargs: True)
    monkeypatch.setattr(rpa_runtime.recording_manager, "record_runtime_step", lambda **kwargs: [])
    runtime._record_trace_step(
        run_handle=SimpleNamespace(run_id="run", session_id="session", emit=lambda *args, **kwargs: None),
        goal="fixture", action_type="type_text", action_payload=payload,
        result=ComputerUseActionResult(action_id="typed", action_type="type_text", status="completed", message="fixture", target=target, verification=verification, metadata=result_metadata or {}),
        snapshot={}, high_risk_action=False, visual_guard_requested=False,
        pre_action_guard_requested=False, max_attempts=1, invocation=None, binding_decision=None,
    )
    return runtime.trace_store.get_trace("run")["steps"][0]


def test_sensitive_trace_never_persists_literal_in_selector_variable_or_risk(tmp_path, monkeypatch):
    import json
    from runtimes.computer_use.types import ComputerUseVerification

    secret = "synthetic-password-only"
    step = _record_trace_fixture(tmp_path, monkeypatch,
        payload={"text": secret, "target_text": secret, "sensitive_input": True, "credential_ref": "cred:fixture"},
        target={"name": secret, "isPassword": True, "elementId": "entry"},
        verification=ComputerUseVerification(passed=True, status="verified", reason="fixture"))
    assert secret not in json.dumps(step)
    assert step["params"]["credentialRef"] == "cred:fixture"
    assert step["primitive"]["supportsRpaPromotion"] is False


def test_opaque_postcondition_cannot_upgrade_focus_evidence_to_business_verified(tmp_path, monkeypatch):
    from runtimes.computer_use.types import ComputerUseVerification

    step = _record_trace_fixture(tmp_path, monkeypatch,
        payload={"postcondition": {"form_saved": True}}, target={"elementId": "entry"},
        verification=ComputerUseVerification(passed=True, status="focus_verified", level="soft_verified", reason="only focus was observed"))
    assert step["metadata"]["evidence"]["businessVerified"] is False


def test_deduplicated_receipt_is_preserved_and_cannot_become_a_replay_step(tmp_path, monkeypatch):
    from runtimes.computer_use.types import ComputerUseVerification
    from runtimes.rpa.compiler import RPATraceCompiler
    from runtimes.rpa.store import RPAScriptStore

    receipt = {"execute": False, "state": "completed", "idempotencyKey": "fixture-receipt"}
    step = _record_trace_fixture(tmp_path, monkeypatch,
        payload={"text": "fixture"}, target={"elementId": "entry", "name": "Entry", "windowHandle": 42},
        verification=ComputerUseVerification(passed=True, status="side_effect_deduplicated", reason="prior receipt"),
        result_metadata={"sideEffectReceipt": receipt})
    assert step["metadata"]["sideEffectReceipt"] == receipt
    assert step["metadata"]["evidence"]["execution"] == "deduplicated"
    assert step["metadata"]["evidence"]["reusable"] is False
    compiler = RPATraceCompiler(script_store=RPAScriptStore(tmp_path / "scripts"))
    assert compiler._assessment_for_step(step, app_id="fixture", compiled_use="type_text", robot_semantic=None).excluded
    old = deepcopy(step)
    old["metadata"]["evidence"].update(execution="executed", reusable=True)
    old["primitive"]["supportsRpaPromotion"] = True
    assert not compiler._assessment_for_step(old, app_id="fixture", compiled_use="type_text", robot_semantic=None).excluded


def test_explicit_output_directory_cannot_overwrite_a_prepared_version(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    from runtimes.rpa.robot_adapter import RobotFrameworkAdapter
    from runtimes.rpa.store import RPAScriptStore

    adapter = RobotFrameworkAdapter(script_store=RPAScriptStore(tmp_path / "scripts"))
    monkeypatch.setattr(adapter, "validate_robot_file", lambda **kwargs: {"passed": True, "outputDir": str(tmp_path / "validation")})
    script = {"id": "same", "name": "fixture", "updatedAt": "v1", "steps": [{"stepId": "a", "use": "comment", "params": {"text": "first"}}]}
    first = adapter.export_script(script=script, output_dir=tmp_path / "shared-output")
    script["steps"][0]["params"]["text"] = "second"
    script["updatedAt"] = "v2"
    second = adapter.export_script(script=script, output_dir=tmp_path / "shared-output")
    assert first["path"] != second["path"]
    assert hashlib.sha256(Path(first["path"]).read_bytes()).hexdigest() == first["sha256"]
    assert first["draftUpdatedAt"] == "v1" and second["draftUpdatedAt"] == "v2"


def test_callback_retries_only_acknowledge_the_same_event_and_body(tmp_path):
    _, manager = _fixture(tmp_path)
    rid = manager.start({})["recordingSessionId"]
    broker = CaptureBroker(manager, request_root=tmp_path / "requests")
    sid = broker.start_session(rid, {"platform": "windows", "sidecarReady": True})["session"]["sessionId"]
    private = manager.get_inspector_session(rid, sid)
    private["orderedEventsRequired"] = True
    manager.upsert_inspector_session(rid, private)
    event = {"type": "candidate", "seq": 1, "eventId": "first", "generation": private["generation"],
             "token": private["oneTimeToken"], "candidate": {"selector": {"automationId": "first"}}}
    for missing in ("eventId", "generation", "seq"):
        with pytest.raises(ValueError, match="require"):
            broker.ingest_event(rid, sid, {key: value for key, value in event.items() if key != missing})
    assert broker.ingest_event(rid, sid, event)["ok"]
    assert broker.ingest_event(rid, sid, deepcopy(event))["duplicate"]
    for conflicting in ({**event, "eventId": "second"}, {**event, "candidate": {"selector": {"automationId": "different"}}}):
        with pytest.raises(ValueError, match="sequence conflict"):
            broker.ingest_event(rid, sid, conflicting)
    with pytest.raises(ValueError, match="identity"):
        broker.ingest_event(rid, sid, {**event, "seq": 2})
    assert len(manager.get(rid)["capturePool"]) == 1
    assert broker.ingest_event(rid, sid, {**event, "seq": 2, "eventId": "second", "candidate": {"selector": {"automationId": "second"}}})["ok"]
    with pytest.raises(ValueError, match="sequence conflict"):
        broker.ingest_event(rid, sid, event)
