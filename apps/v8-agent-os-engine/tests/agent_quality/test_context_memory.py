from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import api.chat_realtime_routes as routes
from api.models import ChatMessage, ChatRequest, ChatRequestData
from core.database import DatabaseManager
from core.native_tools import memory_broker


def _install_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DatabaseManager:
    test_db = DatabaseManager(tmp_path / "agent-quality-queue.sqlite3")
    monkeypatch.setattr(routes, "db", test_db)
    return test_db


def _request(*, session_id: str | None = None, data_session_id: str | None = None, client_message_id: str = "client-1", content: str = "继续") -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=content)],
        session_id=session_id,
        clientMessageId=client_message_id,
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
            _request(data_session_id="session-quality-context", client_message_id="client-same", content="这条应该排队")
        )
    )
    second = asyncio.run(
        routes.chat_submit(
            _request(data_session_id="session-quality-context", client_message_id="client-same", content="这条应该排队")
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

