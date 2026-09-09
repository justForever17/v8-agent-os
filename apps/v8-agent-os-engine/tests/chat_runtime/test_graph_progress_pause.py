"""Graph budget exhaustion is a resumable pause, never a completed delivery."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langgraph.errors import GraphRecursionError

from api.chat_realtime_routes import _find_active_chat_run
from core import database as database_module
from core.database import DatabaseManager
from erc.command_router import runtime_command_router
from erc.command_service import command_service
from erc.kernel import RunHandle, erc_kernel
from erc.models import RunDescriptor, RuntimeCommand
from erc.recovery_policy import derive_recovery_class
from runtimes.chat.runtime import ChatRuntime, ChatStreamState, canonical_transcript_builder


@pytest.mark.parametrize("early_guard", [False, True])
def test_graph_boundary_pauses_real_run_and_message_without_cancelling_episodes(monkeypatch, tmp_path, early_guard):
    original_db = database_module.db
    isolated = DatabaseManager(tmp_path / "state.db")
    for module in list(sys.modules.values()):
        if module is not None and getattr(module, "db", None) is original_db:
            monkeypatch.setattr(module, "db", isolated)
    session, run = "graph-pause-session", "graph-pause-run"
    isolated.create_or_update_session(session, "Graph pause fixture", user_id="fixture-owner")
    isolated.create_run_record(run, session, run_type="chat", status="running", user_id="fixture-owner")
    isolated.upsert_runtime_episode_record({"episodeId": "already-active", "kind": "engineering", "state": "active"},
                                           session_id=session, run_id=run)
    canonical_transcript_builder.create_message(message_id="assistant-partial", session_id=session, run_id=run,
        ordinal=1, role="assistant", state="streaming",
        nodes=[{"id": "known-result", "kind": "narrative", "content": "已有文件完成第一步修改，尚待验证。"}])
    emitted = []
    emitter = SimpleNamespace(emit=lambda topic, payload, **kwargs: emitted.append({"topic": topic, "payload": payload}) or emitted[-1])
    monkeypatch.setattr(erc_kernel, "_emitter_for_run", lambda *_args, **_kwargs: emitter)
    handle = RunHandle(RunDescriptor(run, session, session, "fixture-owner", "chat", status="running"), emitter)
    chat_run = SimpleNamespace(active_run_id=run, session_id=session, user_id="fixture-owner", run_handle=handle,
                               emit_runtime_event=lambda topic, payload, **kwargs: emitter.emit(topic, payload),
                               prepared=SimpleNamespace())
    stream_state = ChatStreamState(assistant_message_id="assistant-partial")
    runtime = ChatRuntime()
    abort, expire = Mock(), Mock()
    monkeypatch.setattr(runtime, "_abort_engineering_workspaces", abort)
    monkeypatch.setattr(runtime, "_expire_plugin_task_grants", expire)
    monkeypatch.setattr(runtime, "_clear_chat_projection_safe", lambda *_args: None)
    error = GraphRecursionError("framework step boundary")
    if early_guard:
        error.execution_progress_guard = {"reason": "insufficient_remaining_steps_for_tool_round",
            "remaining_steps": 6, "suppressed_tool_names": ["run_system_command"],
            "suppressed_tool_call_ids": ["never-executed"], "suppressed_tools_executed": False}

    events = runtime.finalize_failed_run(chat_run, error, stream_state)

    stored = isolated.get_run_record(run)
    assert stored["status"] == "paused"
    assert stored["metadata"]["recoverable"] is True
    assert stored["metadata"]["automaticGraphRestart"] is False
    assert derive_recovery_class(stored)["canResume"] is True
    # Once the stream releases its lane, paused history must not queue a new
    # user message forever behind a run that is no longer executing.
    assert _find_active_chat_run(session) is None
    assert isolated.get_runtime_episode("already-active")["state"] == "active"
    message = isolated.get_chat_canonical_message("assistant-partial")
    assert message["state"] == "paused"
    assert message["content_text"] == "已有文件完成第一步修改，尚待验证。"
    assert message["metadata"]["terminalReason"] == "graph_progress_ceiling"
    if early_guard:
        assert message["metadata"]["executionProgressGuard"]["suppressed_tool_call_ids"] == ["never-executed"]
    assert not any(node.get("executionType") == "tool_call" for node in message["nodes"])
    assert events[-1]["status"] == "paused"
    assert events[0]["name"] == "run_controlled"
    assert "尚未完成" in events[0]["data"]["reason"]
    assert {event["topic"] for event in emitted} >= {"run.paused", "run.state.changed"}
    assert not any(event["topic"] in {"run.completed", "run.failed", "tool.started"} for event in emitted)
    abort.assert_not_called()
    expire.assert_not_called()

    # Resume uses the existing governed command owner. Nothing is scheduled
    # automatically during finalization and no stale pause signal is replayed.
    schedule = Mock()
    monkeypatch.setattr(runtime_command_router, "_schedule_resume", schedule)
    result = runtime_command_router.dispatch_run_command(RuntimeCommand(topic="run.resume", run_id=run))
    assert result is not None
    schedule.assert_called_once()
    assert isolated.get_run_record(run)["status"] == "running"
    assert _find_active_chat_run(session)["id"] == run
    assert command_service.consume_control_signal(run) is None
    assert isolated.get_runtime_episode("already-active")["state"] == "active"


@pytest.mark.parametrize("current_status", ["cancelled", "completed", "waiting_input"])
def test_late_graph_boundary_cannot_overwrite_newer_run_control(monkeypatch, current_status):
    from runtimes.chat import runtime as runtime_module

    pause = Mock(return_value={"updated": False, "run_record": {"status": current_status}})
    monkeypatch.setattr(runtime_module.run_service, "transition_run_if_status", pause)
    emitted = Mock()
    chat_run = SimpleNamespace(active_run_id="controlled-run", run_handle=SimpleNamespace(refresh_chat_snapshot=Mock()),
                               emit_runtime_event=emitted)
    runtime = ChatRuntime()
    persist = Mock()
    monkeypatch.setattr(runtime, "persist_final_assistant_message", persist)
    events = runtime.finalize_failed_run(chat_run, GraphRecursionError("late boundary"), ChatStreamState())
    assert events == [{"type": "done", "status": current_status, "run_id": "controlled-run"}]
    assert pause.call_args.kwargs["expected_statuses"] == {"running", "queued"}
    persist.assert_not_called()
    emitted.assert_not_called()
