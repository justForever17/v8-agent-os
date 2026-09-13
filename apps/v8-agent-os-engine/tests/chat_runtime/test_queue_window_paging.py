from __future__ import annotations

import asyncio
import pytest
from core.database import DatabaseManager
import erc.snapshot_service as snapshots


@pytest.mark.parametrize("count", [0, 2, 25])
def test_public_compact_snapshot_preserves_queue_and_paging_without_an_active_run(monkeypatch, tmp_path, count):
    from api import session_workflow_routes
    from erc.command_router import RuntimeCommandRouter
    from erc import session_runtime

    database = DatabaseManager(tmp_path / "public-queue.sqlite3")
    monkeypatch.setattr(snapshots, "db", database)
    monkeypatch.setattr(session_runtime, "db", database)
    monkeypatch.setattr(session_workflow_routes, "runtime_command_router", RuntimeCommandRouter())
    database.create_or_update_session("public-queue-a", "Fixture")
    for index in range(count):
        database.add_chat_user_message_queue_item(queue_id=f"pending-{index}", session_id="public-queue-a", run_id=None,
                                                 client_message_id=f"client-{index}", content=f"pending text {index}")
    expected = snapshots.snapshot_service.queued_message_page("public-queue-a")
    payload = asyncio.run(session_workflow_routes.get_session_snapshot("public-queue-a", compact=1))
    assert payload["currentRun"] is None
    assert payload["queuedMessages"] == expected["queuedMessages"]
    assert payload["queuedMessagesWindow"] == expected["queuedMessagesWindow"]
    assert payload["queuedMessagesWindow"]["hasMore"] is (count > 20)
    assert payload["queuedMessagesWindow"]["complete"] is (count <= 20)


@pytest.mark.parametrize("count", [0, 20, 21, 41])
def test_queue_pages_are_bounded_and_only_initial_full_collection_is_complete(monkeypatch, tmp_path, count):
    database = DatabaseManager(tmp_path / "queue.sqlite3")
    monkeypatch.setattr(snapshots, "db", database)
    database.create_or_update_session("session-a", "Fixture")
    for index in range(count):
        database.add_chat_user_message_queue_item(queue_id=f"q-{index}", session_id="session-a", run_id=None, client_message_id=f"client-{index}", content=f"fixture {index}")
    cursor = None
    seen = []
    while True:
        page = snapshots.snapshot_service.queued_message_page("session-a", cursor)
        window = page["queuedMessagesWindow"]
        assert len(page["queuedMessages"]) <= 20
        assert window["complete"] is (cursor is None and count <= 20)
        seen.extend(item["id"] for item in page["queuedMessages"])
        if not window["hasMore"]:
            break
        cursor = window["nextOrdinal"]
    assert seen == [f"q-{index}" for index in range(count)]


def test_cursor_survives_consumption_and_does_not_mix_sessions(monkeypatch, tmp_path):
    database = DatabaseManager(tmp_path / "queue.sqlite3")
    monkeypatch.setattr(snapshots, "db", database)
    for session in ["session-a", "session-b"]:
        database.create_or_update_session(session, "Fixture")
        for index in range(41):
            database.add_chat_user_message_queue_item(queue_id=f"{session}-{index}", session_id=session, run_id=None, client_message_id=str(index), content="fixture")
    first = snapshots.snapshot_service.queued_message_page("session-a")
    database.update_chat_user_message_queue_item("session-a-0", state="consumed")
    database.update_chat_user_message_queue_item("session-a-21", state="cancelled")
    next_page = snapshots.snapshot_service.queued_message_page("session-a", first["queuedMessagesWindow"]["nextOrdinal"])
    ids = [item["id"] for item in next_page["queuedMessages"]]
    assert "session-a-20" in ids and "session-a-40" in ids
    assert "session-a-21" not in ids
    assert all(item["sessionId"] == "session-a" for item in next_page["queuedMessages"])
