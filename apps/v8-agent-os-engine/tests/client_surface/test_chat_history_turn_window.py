from __future__ import annotations

from pathlib import Path

import pytest

from core.database import DatabaseManager
from erc import chat_canonical_transcript as transcript
from erc.chat_canonical_transcript import (
    CanonicalTurnNotFoundError,
    build_canonical_chat_turn_index,
    build_canonical_chat_turn_window,
    build_canonical_turn_index_entries,
    group_canonical_turn_rows,
    select_canonical_turn_window_rows,
)


def test_turn_routes_expose_index_and_random_seek_contract() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "api" / "session_workflow_routes.py"
    ).read_text(encoding="utf-8")
    assert '@router.get("/sessions/{session_id}/turn-index")' in source
    assert "await asyncio.to_thread" in source
    assert "around_turn_id=normalized_around or None" in source
    assert "radius requires around" in source


def _row(message_id: str, role: str, ordinal: int, run_id: str | None = None) -> dict:
    return {
        "id": message_id,
        "role": role,
        "ordinal": ordinal,
        "run_id": run_id,
        "state": "final",
        "content_text": f"content {message_id}",
        "content_preview": f"content {message_id}",
        "created_at": f"2026-07-06T00:00:{ordinal:02d}Z",
        "updated_at": f"2026-07-06T00:00:{ordinal:02d}Z",
    }


class _FakeTurnDb:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.range_reads: list[tuple[int, int]] = []
        self.before_reads: list[tuple[int | None, int]] = []

    def get_chat_canonical_turn_index_rows(self, session_id: str) -> list[dict]:
        return list(self.rows)

    def get_chat_canonical_messages_in_ordinal_range(
        self,
        session_id: str,
        *,
        first_ordinal: int,
        last_ordinal: int,
    ) -> list[dict]:
        self.range_reads.append((first_ordinal, last_ordinal))
        return [
            dict(row)
            for row in self.rows
            if first_ordinal <= int(row["ordinal"]) <= last_ordinal
        ]

    def get_chat_canonical_messages_before_ordinal(
        self,
        session_id: str,
        before_ordinal: int | None = None,
        *,
        limit: int = 500,
    ) -> list[dict]:
        self.before_reads.append((before_ordinal, limit))
        candidates = [
            row
            for row in self.rows
            if before_ordinal is None or int(row["ordinal"]) < before_ordinal
        ]
        return [dict(row) for row in reversed(candidates[-limit:])]

    def has_chat_canonical_message_before_ordinal(self, session_id: str, before_ordinal: int) -> bool:
        return any(int(row["ordinal"]) < before_ordinal for row in self.rows)

    def list_runtime_artifacts(self, *, session_id: str, limit: int) -> list[dict]:
        return []


def test_turn_window_prefers_run_id_grouping() -> None:
    rows_desc = [
        _row("a2", "assistant", 4, "run-b"),
        _row("u2", "user", 3, "run-b"),
        _row("a1", "assistant", 2, "run-a"),
        _row("u1", "user", 1, "run-a"),
    ]

    selected, loaded_turns = select_canonical_turn_window_rows(rows_desc, limit_turns=1)

    assert loaded_turns == 1
    assert [row["id"] for row in selected] == ["u2", "a2"]


def test_turn_window_falls_back_to_user_message_boundaries_without_run_id() -> None:
    rows_asc = [
        _row("u1", "user", 1),
        _row("a1", "assistant", 2),
        _row("t1", "tool", 3),
        _row("u2", "user", 4),
        _row("a2", "assistant", 5),
    ]

    groups = group_canonical_turn_rows(rows_asc)
    selected, loaded_turns = select_canonical_turn_window_rows(list(reversed(rows_asc)), limit_turns=1)

    assert [[row["id"] for row in group] for group in groups] == [["u1", "a1", "t1"], ["u2", "a2"]]
    assert loaded_turns == 1
    assert [row["id"] for row in selected] == ["u2", "a2"]


def test_turn_keeps_user_message_with_complete_supervisor_run_when_user_run_id_is_missing() -> None:
    rows_asc = [
        _row("u1", "user", 1),
        _row("a1-progress", "assistant", 2, "run-a"),
        _row("t1", "tool", 3, "run-a"),
        _row("a1-final", "assistant", 4, "run-a"),
        _row("u2", "user", 5),
        _row("a2", "assistant", 6, "run-b"),
    ]

    groups = group_canonical_turn_rows(rows_asc)

    assert [[row["id"] for row in group] for group in groups] == [
        ["u1", "a1-progress", "t1", "a1-final"],
        ["u2", "a2"],
    ]


def test_turn_index_is_stable_opaque_and_reasoning_free() -> None:
    rows = [
        {
            **_row("u1", "user", 1, "run-sensitive-a"),
            "content_preview": "请整理交付内容 <think>不应泄露</think>",
            "reasoning_text": "private reasoning",
            "nodes_json": "private nodes",
        },
        _row("a1", "assistant", 2, "run-sensitive-a"),
        _row("u2", "user", 3, "run-sensitive-b"),
        _row("a2", "assistant", 4, "run-sensitive-b"),
    ]

    first = build_canonical_turn_index_entries("session-a", rows)
    second = build_canonical_turn_index_entries("session-a", rows)
    rows_without_run_fk = [{**row, "run_id": None} for row in rows]
    after_run_retention = build_canonical_turn_index_entries("session-a", rows_without_run_fk)

    assert first == second
    assert first[0]["turnId"] == after_run_retention[0]["turnId"]
    assert [entry["position"] for entry in first] == [1, 2]
    assert all(entry["turnId"].startswith("turn_") for entry in first)
    assert all("run-sensitive" not in entry["turnId"] for entry in first)
    assert first[0]["preview"] == "请整理交付内容"
    assert "reasoning" not in str(first)
    assert "nodes" not in str(first)


def test_turn_index_pages_from_latest_with_stable_position_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _row(f"u{turn}", "user", turn * 2 - 1, f"run-{turn}")
        for turn in range(1, 6)
    ] + [
        _row(f"a{turn}", "assistant", turn * 2, f"run-{turn}")
        for turn in range(1, 6)
    ]
    rows.sort(key=lambda row: row["ordinal"])
    monkeypatch.setattr(transcript, "db", _FakeTurnDb(rows))

    latest = build_canonical_chat_turn_index("session-a", limit_turns=2)
    older = build_canonical_chat_turn_index(
        "session-a",
        before_position=int(latest["pageInfo"]["beforeCursor"]),
        limit_turns=2,
    )

    assert [entry["position"] for entry in latest["turns"]] == [4, 5]
    assert latest["pageInfo"]["totalTurnCount"] == 5
    assert latest["pageInfo"]["hasOlder"] is True
    assert [entry["position"] for entry in older["turns"]] == [2, 3]
    assert older["pageInfo"]["hasNewer"] is True


def test_turn_window_random_seek_returns_anchor_and_neighbors(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        item
        for turn in range(1, 5)
        for item in (
            _row(f"u{turn}", "user", turn * 2 - 1, f"run-{turn}"),
            _row(f"a{turn}", "assistant", turn * 2, f"run-{turn}"),
        )
    ]
    fake_db = _FakeTurnDb(rows)
    monkeypatch.setattr(transcript, "db", fake_db)
    entries = build_canonical_turn_index_entries("session-a", rows)

    result = build_canonical_chat_turn_window(
        "session-a",
        around_turn_id=entries[2]["turnId"],
        neighbor_turns=1,
    )

    assert fake_db.range_reads == [(3, 8)]
    assert [message["id"] for message in result["messages"]] == ["u2", "a2", "u3", "a3", "u4", "a4"]
    assert result["pageInfo"]["anchorTurnId"] == entries[2]["turnId"]
    assert result["pageInfo"]["anchorPosition"] == 3
    assert result["pageInfo"]["hasOlder"] is True
    assert result["pageInfo"]["hasNewer"] is False
    assert {message["turnPosition"] for message in result["messages"]} == {2, 3, 4}


def test_turn_window_does_not_split_a_turn_larger_than_legacy_scan_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row(
            f"message-{ordinal}",
            "user" if ordinal == 1 else "assistant",
            ordinal,
            "run-large",
        )
        for ordinal in range(1, 702)
    ]
    fake_db = _FakeTurnDb(rows)
    monkeypatch.setattr(transcript, "db", fake_db)

    result = build_canonical_chat_turn_window("session-a", limit_turns=1, scan_limit=50)

    assert result["pageInfo"]["loadedTurnCount"] == 1
    assert len(result["messages"]) == 701
    assert len(fake_db.before_reads) > 1
    assert fake_db.range_reads == []
    assert len({message["turnId"] for message in result["messages"]}) == 1


def test_turn_window_rejects_unknown_random_seek_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcript, "db", _FakeTurnDb([_row("u1", "user", 1, "run-a")]))

    with pytest.raises(CanonicalTurnNotFoundError):
        build_canonical_chat_turn_window("session-a", around_turn_id="turn_missing")


def test_database_turn_index_query_does_not_hydrate_private_payloads(tmp_path) -> None:
    test_db = DatabaseManager(tmp_path / "turn-index.db")
    test_db.create_or_update_session("session-a", "Turn index test")
    test_db.create_chat_canonical_message(
        message_id="u1",
        session_id="session-a",
        run_id=None,
        ordinal=1,
        role="user",
        state="final",
        nodes=[{"kind": "narrative", "content": "public prompt"}],
        content_text="public prompt",
        reasoning_text="private reasoning",
        metadata={"private": "metadata"},
    )
    test_db.create_chat_canonical_message(
        message_id="a1",
        session_id="session-a",
        run_id=None,
        ordinal=2,
        role="assistant",
        state="final",
        nodes=[{"kind": "execution", "executionType": "tool_call", "args": {"secret": "value"}}],
        content_text="public result",
        reasoning_text="private reasoning",
        metadata={"private": "metadata"},
    )

    index_rows = test_db.get_chat_canonical_turn_index_rows("session-a")
    hydrated_rows = test_db.get_chat_canonical_messages_in_ordinal_range(
        "session-a",
        first_ordinal=1,
        last_ordinal=2,
    )

    assert len(index_rows) == 2
    assert set(index_rows[0]) == {
        "id",
        "session_id",
        "run_id",
        "ordinal",
        "role",
        "state",
        "content_preview",
        "created_at",
        "updated_at",
    }
    assert "private reasoning" not in str(index_rows)
    assert "secret" not in str(index_rows)
    assert hydrated_rows[1]["nodes"][0]["args"]["secret"] == "value"
