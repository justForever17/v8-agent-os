from __future__ import annotations

from pathlib import Path

from core.database import DatabaseManager


def test_delete_canonical_message_by_node_alias_removes_durable_transcript(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "message-delete.sqlite3")
    session_id = "session-delete-alias"
    test_db.create_or_update_session(session_id, "Delete alias")
    test_db.create_run_record(run_id="run_1", session_id=session_id, run_type="chat", status="completed")
    test_db.create_chat_canonical_message(
        message_id="canon_msg_1",
        session_id=session_id,
        run_id="run_1",
        ordinal=1,
        role="assistant",
        state="completed",
        nodes=[{"id": "canon_msg_1:text", "type": "text", "content": "hello"}],
        content_text="hello",
    )

    result = test_db.delete_message("canon_msg_1:text", session_id=session_id)

    assert result["deleted"] is True
    assert result["canonical_message_id"] == "canon_msg_1"
    assert test_db.get_chat_canonical_messages(session_id) == []
    deleted_ids = test_db.get_deleted_chat_message_ids(session_id)
    assert "canon_msg_1" in deleted_ids
    assert "canon_msg_1:text" in deleted_ids


def test_delete_projection_only_message_creates_session_tombstone(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "message-delete-projection.sqlite3")
    session_id = "session-delete-projection"
    test_db.create_or_update_session(session_id, "Delete projected")

    result = test_db.delete_message("assistant-local-placeholder", session_id=session_id)

    assert result["deleted"] is True
    assert result["physical_delete"] is False
    assert result["source"] == "client_projection"
    assert "assistant-local-placeholder" in test_db.get_deleted_chat_message_ids(session_id)


def test_timeline_sync_never_returns_tombstoned_canonical_message(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "message-delete-sync.sqlite3")
    session_id = "session-delete-sync"
    since = "1970-01-01T00:00:00+00:00"
    test_db.create_or_update_session(session_id, "Delete sync")
    test_db.create_run_record(run_id="run_1", session_id=session_id, run_type="chat", status="completed")
    test_db.create_chat_canonical_message(
        message_id="canon_msg_1",
        session_id=session_id,
        run_id="run_1",
        ordinal=1,
        role="assistant",
        state="completed",
        nodes=[{"id": "canon_msg_1:text", "type": "text", "content": "hello"}],
        content_text="hello",
    )

    assert [item["id"] for item in test_db.get_chat_canonical_messages_since(session_id, since)] == ["canon_msg_1"]

    result = test_db.delete_message("canon_msg_1:text", session_id=session_id)

    assert result["deleted"] is True
    assert test_db.get_chat_canonical_messages_since(session_id, since) == []
    assert "canon_msg_1" in test_db.get_chat_message_deletions_since(session_id, since)


def test_timeline_sync_returns_deleted_alias_and_canonical_ids(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "message-delete-sync-alias.sqlite3")
    session_id = "session-delete-sync-alias"
    since = "1970-01-01T00:00:00+00:00"
    test_db.create_or_update_session(session_id, "Delete sync alias")
    test_db.create_run_record(run_id="run_1", session_id=session_id, run_type="chat", status="completed")
    test_db.create_chat_canonical_message(
        message_id="canon_msg_1",
        session_id=session_id,
        run_id="run_1",
        ordinal=1,
        role="assistant",
        state="completed",
        nodes=[{"id": "canon_msg_1:text", "type": "text", "content": "hello"}],
        content_text="hello",
    )

    result = test_db.delete_message("canon_msg_1:text", session_id=session_id)
    deletions = test_db.get_chat_message_deletions_since(session_id, since)

    assert result["deleted"] is True
    assert set(deletions) == {"canon_msg_1", "canon_msg_1:text"}
    assert len(deletions) == 2


def test_projection_tombstone_blocks_future_incremental_resurrection(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "message-delete-sync-projection.sqlite3")
    session_id = "session-delete-sync-projection"
    since = "1970-01-01T00:00:00+00:00"
    test_db.create_or_update_session(session_id, "Delete sync projection")
    test_db.create_run_record(run_id="run_1", session_id=session_id, run_type="chat", status="completed")

    result = test_db.delete_message("assistant-local-placeholder", session_id=session_id)
    test_db.create_chat_canonical_message(
        message_id="assistant-local-placeholder",
        session_id=session_id,
        run_id="run_1",
        ordinal=1,
        role="assistant",
        state="completed",
        nodes=[{"id": "assistant-local-placeholder:text", "type": "text", "content": "stale"}],
        content_text="stale",
    )

    assert result["deleted"] is True
    assert test_db.get_chat_canonical_messages_since(session_id, since) == []
    assert "assistant-local-placeholder" in test_db.get_chat_message_deletions_since(session_id, since)
