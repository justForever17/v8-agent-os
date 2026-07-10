from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from api import session_workflow_routes
from core.database import DatabaseManager
from erc import chat_canonical_transcript


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


def test_timeline_sync_reprojects_historical_inline_think_without_rewriting_row(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "message-sync-inline-think.sqlite3")
    session_id = "session-sync-inline-think"
    since = "1970-01-01T00:00:00+00:00"
    test_db.create_or_update_session(session_id, "Inline think sync")
    test_db.create_run_record(run_id="run_1", session_id=session_id, run_type="chat", status="completed")
    test_db.create_chat_canonical_message(
        message_id="canon_msg_1",
        session_id=session_id,
        run_id="run_1",
        ordinal=1,
        role="assistant",
        state="completed",
        nodes=[
            {
                "id": "canon_msg_1:text",
                "kind": "narrative",
                "role": "assistant",
                "content": "<think>private chain</think>Visible answer",
            }
        ],
        content_text="<think>private chain</think>Visible answer",
    )
    stored_before = test_db.get_chat_canonical_message("canon_msg_1")

    with (
        patch.object(session_workflow_routes, "db", test_db),
        patch.object(chat_canonical_transcript, "db", test_db),
    ):
        payload = asyncio.run(session_workflow_routes.get_session_timeline_sync(session_id, since))

    assistant = payload["messages"][0]
    narrative_nodes = [node for node in assistant["nodes"] if node.get("kind") == "narrative"]
    reasoning_nodes = [node for node in assistant["nodes"] if node.get("executionType") == "reasoning"]
    assert assistant["content"] == "Visible answer"
    assert assistant["reasoningContent"] == "private chain"
    assert narrative_nodes[0]["content"] == "Visible answer"
    assert reasoning_nodes[0]["content"] == "private chain"
    assert "<think" not in json.dumps(narrative_nodes, ensure_ascii=False).lower()

    stored_after = test_db.get_chat_canonical_message("canon_msg_1")
    assert stored_after["version"] == stored_before["version"] == 1
    assert stored_after["created_at"] == stored_before["created_at"]
    assert stored_after["updated_at"] == stored_before["updated_at"]
    assert stored_after["nodes"] == stored_before["nodes"]
