from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from runtimes.chat import runtime as chat_runtime_module
from runtimes.chat.runtime import ChatRuntime, ChatStreamState


def _chat_run() -> SimpleNamespace:
    events: list[tuple[str, dict]] = []

    def emit_runtime_event(topic, payload, **_kwargs):
        events.append((topic, payload))
        return {"topic": topic, "payload": payload}

    return SimpleNamespace(
        session_id="session-test",
        active_run_id="run-test",
        emit_runtime_event=emit_runtime_event,
        events=events,
    )


def test_auxiliary_chat_projection_failure_does_not_escape_or_spam(monkeypatch) -> None:
    runtime = ChatRuntime()
    stream_state = ChatStreamState()
    chat_run = _chat_run()

    attempts = 0

    def fail_projection(**_kwargs):
        nonlocal attempts
        attempts += 1
        raise sqlite3.DatabaseError("malformed database schema")

    monkeypatch.setattr(chat_runtime_module.workflow_ledger_service, "append_chat_projection", fail_projection)

    runtime._append_chat_projection_safe(chat_run, stream_state, text_delta="one")
    runtime._append_chat_projection_safe(chat_run, stream_state, text_delta="two")

    assert [topic for topic, _payload in chat_run.events] == ["chat.projection.degraded"]
    assert attempts == 1
    assert chat_run.events[0][1]["failureClass"] == "workflow_preview_projection_unavailable"
    assert stream_state.chat_projection_failure_run_ids == {"run-test"}


def test_auxiliary_projection_cleanup_failure_does_not_invalidate_canonical_completion(monkeypatch) -> None:
    runtime = chat_runtime_module.ChatRuntime()
    chat_run = _chat_run()
    stream_state = chat_runtime_module.ChatStreamState()

    def fail_clear(_run_id: str) -> None:
        raise RuntimeError("projection cleanup unavailable")

    monkeypatch.setattr(chat_runtime_module.workflow_ledger_service, "clear_chat_projection", fail_clear)

    runtime._clear_chat_projection_safe(chat_run, stream_state)

    assert stream_state.chat_projection_failure_run_ids == {"run-test"}


def test_reasoning_delta_records_real_start_and_elapsed_milliseconds(monkeypatch) -> None:
    runtime = ChatRuntime()
    stream_state = ChatStreamState()
    chat_run = _chat_run()
    captured_nodes: list[dict] = []

    monkeypatch.setattr(runtime, "_get_agent_profile", lambda _agent_id: {
        "name": "Supervisor",
        "avatar": "",
        "roleLabel": "Supervisor",
    })
    monkeypatch.setattr(runtime, "_ensure_assistant_canonical_message", lambda *_args: "message-test")
    monkeypatch.setattr(runtime, "_stream_trace_diagnostics", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime, "_append_chat_projection_safe", lambda *_args, **_kwargs: None)

    def emit_owner_event(_chat_run, _stream_state, **kwargs):
        captured_nodes.append(kwargs["node"])
        return {"seq": len(captured_nodes), "payload": {}}

    monkeypatch.setattr(runtime, "_emit_owner_scoped_runtime_event", emit_owner_event)
    owner = {
        "displayInMessage": True,
        "ownerRuntimeId": "chat",
        "ownerAgentId": "supervisor",
    }
    runtime._note_model_reasoning_start(stream_state, model_run_id="model-run", started_at_ms=500)

    first = runtime._emit_reasoning_delta(
        chat_run,
        stream_state,
        "plan",
        model_run_id="model-run",
        canonical_event_at_ms=1_000,
        owner=owner,
    )
    second = runtime._emit_reasoning_delta(
        chat_run,
        stream_state,
        " more",
        model_run_id="model-run",
        canonical_event_at_ms=2_750,
        owner=owner,
    )

    assert first["startTime"] == 500
    assert first["durationMs"] == 500
    assert second["timestamp"] == 2_750
    assert second["startTime"] == 500
    assert second["durationMs"] == 2_250
    assert captured_nodes[-1]["time"] == 2_250
    assert captured_nodes[-1]["data"]["durationMs"] == 2_250


def test_message_targeted_event_replaces_zero_timestamp_before_canonical_write(monkeypatch) -> None:
    runtime = ChatRuntime()
    stream_state = ChatStreamState()
    chat_run = _chat_run()
    captured_nodes: list[dict] = []

    monkeypatch.setattr(runtime, "_now_timestamp_ms", lambda: 4_200)
    monkeypatch.setattr(runtime, "_ensure_assistant_canonical_message", lambda *_args: "message-test")

    def append_node(_chat_run, _stream_state, *, node, **_kwargs):
        captured_nodes.append(node)
        return node["id"], 2

    monkeypatch.setattr(runtime, "_append_canonical_node", append_node)

    emitted = runtime._emit_message_targeted_runtime_event(
        chat_run,
        stream_state,
        topic="tool.started",
        payload={"type": "tool_start", "timestamp": 0},
        node={
            "id": "message-test:tool-call:test",
            "kind": "execution",
            "executionType": "tool_call",
            "timestamp": 0,
        },
    )

    assert emitted["payload"]["timestamp"] == 4_200
    assert captured_nodes[0]["timestamp"] == 4_200


def test_message_targeted_event_preserves_existing_provider_timestamp(monkeypatch) -> None:
    runtime = ChatRuntime()
    stream_state = ChatStreamState()
    chat_run = _chat_run()
    captured_nodes: list[dict] = []

    monkeypatch.setattr(runtime, "_now_timestamp_ms", lambda: 9_999)
    monkeypatch.setattr(runtime, "_ensure_assistant_canonical_message", lambda *_args: "message-test")

    def append_node(_chat_run, _stream_state, *, node, **_kwargs):
        captured_nodes.append(node)
        return node["id"], 3

    monkeypatch.setattr(runtime, "_append_canonical_node", append_node)

    emitted = runtime._emit_message_targeted_runtime_event(
        chat_run,
        stream_state,
        topic="run.reasoning.delta",
        payload={"type": "reasoning_chunk", "timestamp": 1_234},
        node={
            "id": "message-test:reasoning:test",
            "kind": "execution",
            "executionType": "reasoning",
            "timestamp": 0,
        },
    )

    assert emitted["payload"]["timestamp"] == 1_234
    assert captured_nodes[0]["timestamp"] == 1_234
