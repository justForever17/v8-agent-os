from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import api.chat_realtime_routes as routes
from api.models import ChatMessage, ChatRequest, ChatRequestData
from core.database import DatabaseManager
from core.native_tools import memory_broker
from graph.supervisor_turn import (
    _memory_broker_first_guidance,
    _memory_no_match_since_latest_human,
    _should_force_memory_broker_first,
)


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _install_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DatabaseManager:
    test_db = DatabaseManager(tmp_path / "agent-quality-queue.sqlite3")
    monkeypatch.setattr(routes, "db", test_db)
    return test_db


def _request(
    *,
    session_id: str | None = None,
    data_session_id: str | None = None,
    client_message_id: str = "client-1",
    content: str = "继续",
    workspace_path: str | None = None,
) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=content)],
        session_id=session_id,
        clientMessageId=client_message_id,
        workspacePath=workspace_path,
        data=ChatRequestData(conversationId=data_session_id, clientMessageId=client_message_id),
    )


def _active_run(db: DatabaseManager, *, session_id: str, run_id: str, status: str = "streaming") -> None:
    db.create_or_update_session(session_id, "Agent Quality Context")
    db.create_run_record(run_id=run_id, session_id=session_id, run_type="chat", status=status)
    db.upsert_session_lane_record(
        session_id=session_id,
        active_run_id=run_id,
        queued_run_id=None,
        blocked_by_run_id=None,
        policy="queue_when_busy",
        state=status,
        last_transition="started",
        last_transition_ts="2026-05-27T00:00:00Z",
        metadata={},
    )


def test_active_session_message_queues_by_data_conversation_id_without_new_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = _install_db(monkeypatch, tmp_path)
    _active_run(db, session_id="session-quality-context", run_id="run-quality-context")

    first = asyncio.run(
        routes.chat_submit(
            _request(
                data_session_id="session-quality-context",
                client_message_id="client-same",
                content="这条应该排队",
                workspace_path=str(tmp_path),
            )
        )
    )
    second = asyncio.run(
        routes.chat_submit(
            _request(
                data_session_id="session-quality-context",
                client_message_id="client-same",
                content="这条应该排队",
                workspace_path=str(tmp_path),
            )
        )
    )

    assert first["queued"] is True
    assert first["session_id"] == "session-quality-context"
    assert first["run_id"] == "run-quality-context"
    assert second["queuedMessage"]["id"] == first["queuedMessage"]["id"]
    assert len(db.list_chat_user_message_queue(session_id="session-quality-context")) == 1


def test_injected_guidance_is_not_replayed_as_next_user_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = _install_db(monkeypatch, tmp_path)
    db.create_or_update_session("session-quality-guidance", "Guidance")
    db.create_run_record(run_id="run-done", session_id="session-quality-guidance", run_type="chat", status="completed")
    db.add_chat_user_message_queue_item(
        queue_id="queue-injected",
        session_id="session-quality-guidance",
        run_id="run-done",
        client_message_id="client-injected",
        content="作为引导注入当前循环",
        request_payload=_request(session_id="session-quality-guidance", client_message_id="client-injected").model_dump(mode="json", by_alias=True),
    )
    db.update_chat_user_message_queue_item("queue-injected", state="injected", run_id="run-done", timestamp_field="injected_at")
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)

    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-quality-guidance", "run-done")

    assert scheduled == []
    assert db.get_chat_user_message_queue_item("queue-injected")["state"] == "injected"


def test_memory_broker_explain_injection_preserves_read_only_boundary() -> None:
    payload = memory_broker.func(mode="explain_injection")

    assert '"mode": "explain_injection"' in payload
    assert "memory_broker(recall/get_item/read_day/graph_neighbors)" in payload
    assert "mutation" not in payload.lower()


def test_recall_cue_forces_memory_broker_before_workspace_tools() -> None:
    assert _should_force_memory_broker_first(
        user_query="继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。",
        passive_rag_diagnostics={"has_recall_cue": True, "reject_reason": "no_recall_results"},
        selected_tools=[_Tool("grep_search"), _Tool("memory_broker"), _Tool("read_native_file")],
    )

    guidance = _memory_broker_first_guidance("继续上一轮上下文")
    assert "first tool call MUST be `memory_broker`" in guidance.content
    assert "Do not call `grep_search`" in guidance.content


def test_recall_cue_does_not_force_memory_when_tool_is_not_available() -> None:
    assert not _should_force_memory_broker_first(
        user_query="继续上一轮上下文",
        passive_rag_diagnostics={"has_recall_cue": True},
        selected_tools=[_Tool("read_native_file")],
    )


def test_recall_cue_does_not_force_memory_twice_in_same_user_turn() -> None:
    state = {
        "messages": [
            HumanMessage(content="继续上一轮上下文"),
            AIMessage(content="", tool_calls=[{"id": "call_memory", "name": "memory_broker", "args": {"mode": "recall"}}]),
            ToolMessage(
                content="Memory: no matching prior evidence.\nContinue with the current request.",
                name="memory_broker",
                tool_call_id="call_memory",
            ),
        ]
    }

    assert not _should_force_memory_broker_first(
        user_query="继续上一轮上下文",
        passive_rag_diagnostics={"has_recall_cue": True, "reject_reason": "no_recall_results"},
        selected_tools=[_Tool("read_native_file"), _Tool("memory_broker")],
        state=state,
    )
    assert _memory_no_match_since_latest_human(state)


def test_memory_match_keeps_deeper_reads_available_in_same_turn() -> None:
    state = {
        "messages": [
            HumanMessage(content="继续上一轮上下文"),
            ToolMessage(content="Memory broker: recall\nFound 1 relevant memory item.", name="memory_broker", tool_call_id="call_memory"),
        ]
    }

    assert not _memory_no_match_since_latest_human(state)
