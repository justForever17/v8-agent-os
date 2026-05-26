from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import api.chat_realtime_routes as routes
from api.models import ChatMessage, ChatRequest, ChatRequestData
from core.database import DatabaseManager


def _install_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DatabaseManager:
    test_db = DatabaseManager(tmp_path / "queue-guidance.sqlite3")
    monkeypatch.setattr(routes, "db", test_db)
    return test_db


def _create_active_chat_run(test_db: DatabaseManager, *, session_id: str, run_id: str, status: str = "streaming") -> None:
    test_db.create_or_update_session(session_id, "Queue Session")
    test_db.create_run_record(run_id=run_id, session_id=session_id, run_type="chat", status=status)
    test_db.upsert_session_lane_record(
        session_id=session_id,
        active_run_id=run_id,
        queued_run_id=None,
        blocked_by_run_id=None,
        policy="queue_when_busy",
        state=status,
        last_transition="started",
        last_transition_ts="2026-05-25T00:00:00Z",
        metadata={},
    )


def _chat_request(*, session_id: str | None = None, data_session_id: str | None = None, client_message_id: str = "client-1", content: str = "continue this") -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=content)],
        session_id=session_id,
        clientMessageId=client_message_id,
        data=ChatRequestData(conversationId=data_session_id, clientMessageId=client_message_id),
    )


def test_chat_submit_queues_against_data_conversation_id_and_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    _create_active_chat_run(test_db, session_id="session-queue", run_id="run-active", status="streaming")

    request = _chat_request(data_session_id="session-queue", client_message_id="client-repeat", content="第二条消息")
    first = asyncio.run(routes.chat_submit(request))
    second = asyncio.run(routes.chat_submit(_chat_request(data_session_id="session-queue", client_message_id="client-repeat", content="第二条消息")))

    assert first["queued"] is True
    assert first["session_id"] == "session-queue"
    assert first["conversationId"] == "session-queue"
    assert first["run_id"] == "run-active"
    assert second["queued"] is True
    assert second["queuedMessage"]["id"] == first["queuedMessage"]["id"]

    items = test_db.list_chat_user_message_queue(session_id="session-queue")
    assert len(items) == 1
    assert items[0]["client_message_id"] == "client-repeat"
    assert items[0]["content"] == "第二条消息"
    assert items[0]["state"] == "pending"


def test_completed_run_consumes_next_pending_message_in_same_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-drain", "Queue Drain")
    test_db.create_run_record(run_id="run-done", session_id="session-drain", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-next",
        session_id="session-drain",
        run_id="run-done",
        client_message_id="client-next",
        content="下一条继续",
        request_payload=_chat_request(session_id="session-drain", client_message_id="client-next", content="下一条继续").model_dump(mode="json", by_alias=True),
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-drain", "run-done")

    assert len(scheduled) == 1
    queued_request, transport, next_run_id = scheduled[0]
    assert transport == "queued_user_message"
    assert next_run_id and next_run_id.startswith("run_")
    assert queued_request.session_id == "session-drain"
    assert queued_request.conversation_id == "session-drain"
    assert queued_request.client_message_id == "client-next"
    assert queued_request.messages[-1].content == "下一条继续"
    assert test_db.get_chat_user_message_queue_item("queue-next")["state"] == "consumed"


def test_promoted_guidance_requeues_when_run_completes_before_injection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-guidance", "Guidance")
    test_db.create_run_record(run_id="run-done", session_id="session-guidance", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-guidance",
        session_id="session-guidance",
        run_id="run-done",
        client_message_id="client-guidance",
        content="请当前轮修正方向",
        request_payload=_chat_request(session_id="session-guidance", client_message_id="client-guidance", content="请当前轮修正方向").model_dump(mode="json", by_alias=True),
    )
    test_db.update_chat_user_message_queue_item(
        "queue-guidance",
        state="promoted",
        run_id="run-done",
        timestamp_field="promoted_at",
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-guidance", "run-done")

    assert len(scheduled) == 1
    assert scheduled[0][0].session_id == "session-guidance"
    assert scheduled[0][0].client_message_id == "client-guidance"
    item = test_db.get_chat_user_message_queue_item("queue-guidance")
    assert item["state"] == "consumed"
    assert item["metadata"]["requeuedReason"] == "run_completed_before_guidance_injection"


def test_injected_guidance_is_not_consumed_as_next_user_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-injected", "Injected")
    test_db.create_run_record(run_id="run-done", session_id="session-injected", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-injected",
        session_id="session-injected",
        run_id="run-done",
        client_message_id="client-injected",
        content="已注入的引导",
        request_payload=_chat_request(session_id="session-injected", client_message_id="client-injected", content="已注入的引导").model_dump(mode="json", by_alias=True),
    )
    test_db.update_chat_user_message_queue_item(
        "queue-injected",
        state="injected",
        run_id="run-done",
        timestamp_field="injected_at",
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-injected", "run-done")

    assert scheduled == []
    assert test_db.get_chat_user_message_queue_item("queue-injected")["state"] == "injected"
