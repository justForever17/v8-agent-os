import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.chat_runtime.test_session_command_service import (
    ROOT, USER, harness, create, send, publish_result, use_real_delivery, invoke,
)


@pytest.fixture
def stop_harness(harness, monkeypatch):
    import api.run_control_routes as api
    import core.database as database
    import erc.event_bus as events
    import erc.kernel as kernel
    import erc.run_service as runs
    import erc.command_router as router
    import runtimes.chat.runtime as runtime
    import core.engineering_sandbox.service as sandbox
    from erc.session_coordination_service import SessionCoordinationService
    for module in (api, database, events, kernel, runs, router, runtime):
        monkeypatch.setattr(module, "db", harness.db)
    monkeypatch.setattr(kernel.workflow_ledger_service, "sync_run_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(runs.run_ledger_service, "record_event", lambda **kwargs: None)
    monkeypatch.setattr(sandbox, "get_engineering_sandbox_service", lambda: SimpleNamespace(abort_run_workspaces=lambda **kwargs: {}))
    monkeypatch.setattr(harness.service, "_emit_transition", SessionCoordinationService._emit_transition.__get__(harness.service))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        harness.client = client
        yield harness


def stop(h, run_id="run-root-001", command="cancel"):
    response = h.client.post(f"/runs/{run_id}/commands/{command}", json={"reason": "human_stopped_this_task"})
    assert response.status_code == 200, response.text
    with h.db.get_connection() as conn:
        event = conn.execute("SELECT 1 FROM runtime_events WHERE run_id=? AND topic=?", (run_id, f"run.{command}led" if command == "cancel" else "run.interrupted")).fetchone()
    assert event, "The actual API/kernel producer must persist the stop event"


def child_request(h):
    assignment = create()["assignment"]
    sent = send(assignment)
    h.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    scheduled = use_real_delivery(h)
    return assignment, sent, scheduled


@pytest.mark.parametrize("command", ["cancel", "interrupt"])
def test_actual_stop_then_native_result_keeps_background_and_never_wakes_stopped_request(stop_harness, command):
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    stop(h, command=command)
    result = publish_result(h, assignment, sent, version=1, status="acknowledged", content="Background task still running.")
    assert result["ok"]
    assert not scheduled
    assert len(h.db.list_run_records(session_id=ROOT)) == 1
    assert h.db.get_session_command_assignment(assignment["assignmentId"])["status"] == "active"
    assert h.db.get_run_record("run-child-0")["status"] == "queued"
    row = h.db.get_session_coordination_message(result["message"]["messageId"])
    assert row["content"] == "Background task still running." and row["state"] == "blocked"
    with h.db.get_connection() as conn:
        assert conn.execute("SELECT 1 FROM runtime_events WHERE topic='session_coordination.result' AND payload_json LIKE ?", (f"%{row['id']}%",)).fetchone()
    h.service.recover_pending()
    assert not scheduled
    # A real new command producer binds the continuing background assignment to
    # a new user run. The old request's stop is not a session-wide blacklist.
    h.db.create_run_record("new-user-run", ROOT, user_id=USER, run_type="chat", status="running")
    h.db.update_run_record("run-child-0", status="completed")
    continued = invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": 1,
        "content": "Continue the existing task after my new instruction.", "idempotencyKey": "new-human-turn"}, run="new-user-run")
    assert continued["ok"], continued
    request = h.db.get_session_coordination_message(continued["message"]["messageId"])
    assert request["sourceRunId"] == "new-user-run"
    h.service.mark_injected(request["id"], target_run_id=request["targetRunId"])
    scheduled.clear()
    new_result = publish_result(h, assignment, continued, version=1, status="partial", content="New authorized result.", evidence=["artifact:new"])
    assert new_result["ok"] and len(scheduled) == 1


def test_cancel_between_schedule_and_execution_blocks_the_new_run_owner(stop_harness):
    from runtimes.chat.runtime import ChatRuntime
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    result = publish_result(h, assignment, sent, version=1, status="partial", content="Review this result.", evidence=["artifact:partial"])
    assert result["ok"] and len(scheduled) == 1
    request, target = scheduled[0]
    stop(h)
    active = SimpleNamespace(active_run_id=target, session_id=ROOT, user_id=USER, request=request,
        transport="session_coordination", is_resume_request=False,
        run_handle=SimpleNamespace(descriptor=SimpleNamespace(status="queued")))
    activation = ChatRuntime()._activate_run_for_execution(active)
    assert activation["updated"] is False
    assert h.db.get_run_record(target)["status"] != "running"


def test_cancelled_result_consumer_is_not_replayed_or_reopened_by_next_version(stop_harness):
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    result = publish_result(h, assignment, sent, version=1, status="acknowledged", content="Work accepted.")
    _, target = scheduled[0]
    h.service.mark_injected(result["message"]["messageId"], target_run_id=target)
    stop(h, run_id=target)
    h.service.on_run_terminal(ROOT, target, status="cancelled")
    scheduled.clear()
    later = publish_result(h, assignment, sent, version=2, status="completed", content="Completed in background.", evidence=["artifact:final"])
    assert later["ok"] and not scheduled, later
    h.service.recover_pending()
    assert not scheduled
    assert len(h.db.list_session_project_results(ROOT)) == 2


def test_pause_defers_result_until_explicit_resume_without_revoking_background(stop_harness):
    from erc.command_service import command_service
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    assert h.client.post("/runs/run-root-001/commands/pause", json={"reason": "human_pause"}).status_code == 200
    result = publish_result(h, assignment, sent, version=1, status="acknowledged", content="Background is alive.")
    assert result["ok"] and not scheduled
    row = h.db.get_session_coordination_message(result["message"]["messageId"])
    assert row["state"] == "queued"
    command_service.resume_run("run-root-001", reason="human_resume")
    h.service.dispatch_message(row["id"])
    assert len(scheduled) == 1


def test_consumed_cancel_signal_still_stops_old_result_delivery(stop_harness):
    from erc.command_service import command_service
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    stop(h)
    assert command_service.consume_control_signal("run-root-001")["command"] == "cancel"
    result = publish_result(h, assignment, sent, version=1, status="completed", content="Finished independently.", evidence=["artifact:final"])
    assert result["ok"] and not scheduled
    assert h.db.get_session_coordination_message(result["message"]["messageId"])["errorCode"] == "project_result_source_stopped"


def test_cancel_after_dispatch_read_is_rechecked_in_promotion_transaction(stop_harness, monkeypatch):
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    original_create = h.db.create_run_record
    def cancel_at_admission(*args, **kwargs):
        value = original_create(*args, **kwargs)
        if (kwargs.get("metadata") or {}).get("source") == "session_coordination_messages":
            stop(h)
        return value
    monkeypatch.setattr(h.db, "create_run_record", cancel_at_admission)
    result = publish_result(h, assignment, sent, version=1, status="completed", content="Finished before delivery race.", evidence=["artifact:final"])
    assert result["ok"] and not scheduled
    assert h.db.get_session_coordination_message(result["message"]["messageId"])["state"] == "blocked"
    assert all(row["status"] == "cancelled" for row in h.db.list_run_records(session_id=ROOT))


def test_revoked_revision_cannot_be_injected_after_result_was_scheduled(stop_harness):
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    result = publish_result(h, assignment, sent, version=1, status="completed", content="Before revocation.", evidence=["artifact:final"])
    _, target = scheduled[0]
    revoked = invoke({"mode": "revoke", "assignmentId": assignment["assignmentId"], "revision": 1})
    assert revoked["ok"]
    injected = h.service.mark_injected(result["message"]["messageId"], target_run_id=target)
    assert injected["state"] == "blocked" and injected["errorCode"] == "project_result_assignment_changed"
    with h.db.get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM runtime_events WHERE topic='session_coordination.injected' AND payload_json LIKE ?",
            (f"%{result['message']['messageId']}%",)).fetchone()
    h.service.recover_pending()
    assert len(scheduled) == 1
    assert len(h.db.list_session_project_results(ROOT)) == 1


def test_normal_completion_keeps_background_result_continuation_valid(stop_harness):
    import erc.run_service as runs
    from runtimes.chat.runtime import ChatRuntime
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    runs.run_service.transition_run("run-root-001", status="completed")
    result = publish_result(h, assignment, sent, version=1, status="completed", content="The independent work completed.", evidence=["artifact:final"])
    assert result["ok"] and len(scheduled) == 1
    request, target = scheduled[0]
    active = SimpleNamespace(active_run_id=target, session_id=ROOT, user_id=USER, request=request,
        transport="session_coordination", is_resume_request=False,
        run_handle=SimpleNamespace(descriptor=SimpleNamespace(status="queued")))
    assert ChatRuntime()._activate_run_for_execution(active)["updated"]


def test_cancel_after_activation_is_rechecked_before_recording_model_inputs(stop_harness):
    from runtimes.chat.runtime import ChatRuntime
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    result = publish_result(h, assignment, sent, version=1, status="partial", content="Pending consumption.", evidence=["artifact:partial"])
    request, target = scheduled[0]
    active = SimpleNamespace(active_run_id=target, session_id=ROOT, user_id=USER, request=request,
        transport="session_coordination", is_resume_request=False,
        run_handle=SimpleNamespace(descriptor=SimpleNamespace(status="queued")))
    assert ChatRuntime()._activate_run_for_execution(active)["updated"]
    active.prepared = SimpleNamespace(session_coordination_message=result["message"])
    stop(h)
    with pytest.raises(ValueError, match="project_result_source_stopped"):
        ChatRuntime().record_request_inputs(active)
    row = h.db.get_session_coordination_message(result["message"]["messageId"])
    assert row["state"] != "injected"


def test_resumed_explicit_interrupt_does_not_poison_later_crash_recovery(stop_harness):
    from erc.kernel import erc_kernel
    import erc.run_service as runs
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    stop(h, command="interrupt")
    assert erc_kernel.resume_run("run-root-001", reason="human_resumed_original_task")
    # The crash owner marks interrupted without a new user stop event. The old
    # interrupt_reason must not override the later persisted resume event.
    runs.run_service.transition_run("run-root-001", status="interrupted", error_message="engine_process_lost")
    result = publish_result(h, assignment, sent, version=1, status="completed", content="Independent completion after recovery.", evidence=["artifact:final"])
    assert result["ok"] and len(scheduled) == 1


@pytest.mark.parametrize("cancel_before_activation", [True, False])
def test_stream_stops_and_releases_actual_lane_when_source_is_cancelled(stop_harness, monkeypatch, cancel_before_activation):
    import erc.session_admission_service as admission
    import erc.kernel as kernel
    import core.system_tools.native as native
    from erc.session_lane_scheduler import SessionLaneScheduler
    import runtimes.chat.runtime as runtime_module
    h = stop_harness
    assignment, sent, scheduled = child_request(h)
    result = publish_result(h, assignment, sent, version=1, status="partial", content="Not yet consumed.", evidence=["artifact:partial"])
    request, target = scheduled[0]
    handle = kernel.erc_kernel.attach_run(target)
    active = SimpleNamespace(active_run_id=target, session_id=ROOT, user_id=USER, request=request,
        transport="session_coordination", is_resume_request=False, run_handle=handle,
        prepared=SimpleNamespace(session_coordination_message=result["message"]),
        emit_runtime_event=lambda topic, payload, **kwargs: handle.emit(topic, payload))
    runtime = runtime_module.ChatRuntime()
    monkeypatch.setattr(admission, "db", h.db)
    monkeypatch.setattr(admission, "session_lane_scheduler", SessionLaneScheduler())
    lanes = admission.SessionAdmissionService()
    monkeypatch.setattr(runtime_module, "session_admission_service", lanes)
    monkeypatch.setattr(runtime_module.runtime_stability_service, "session_lane_policy", lambda: "queue")
    monkeypatch.setattr(runtime, "prepare_run_context", lambda *args, **kwargs: active)
    monkeypatch.setattr(runtime, "emit_stream_connected_events", lambda *_args: [])
    monkeypatch.setattr(runtime, "_expire_plugin_task_grants", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_abort_engineering_workspaces", lambda *args, **kwargs: None)
    monkeypatch.setattr(native, "_terminate_run_background_commands", lambda *args, **kwargs: None)
    monkeypatch.setattr(kernel.snapshot_service, "refresh_chat_projection", lambda *args, **kwargs: {})
    def activate_then_stop(run):
        if cancel_before_activation:
            stop(h)
        activation = runtime._activate_run_for_execution(run)
        assert activation["updated"] is not cancel_before_activation
        if not cancel_before_activation:
            stop(h)
        return activation
    monkeypatch.setattr(runtime, "emit_lifecycle_start_events", activate_then_stop)
    async def forbidden_model(*args, **kwargs):
        pytest.fail("Stopped result reached the model execution factory")
    monkeypatch.setattr(runtime, "resolve_execution_bundle", forbidden_model)
    async def consume():
        return [event async for event in runtime.stream_legacy_events(request, transport="session_coordination", run_id=target)]
    events = asyncio.run(consume())
    assert any(event.get("type") == "done" and event.get("status") == "cancelled" for event in events)
    assert h.db.get_run_record(target)["status"] == "cancelled"
    assert lanes.get_lane_view(ROOT)["state"] == "idle"
    assert h.db.get_session_coordination_message(result["message"]["messageId"])["state"] != "injected"
