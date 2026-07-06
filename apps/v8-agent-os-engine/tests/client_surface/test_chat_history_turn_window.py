from __future__ import annotations

from erc.chat_canonical_transcript import (
    group_canonical_turn_rows,
    select_canonical_turn_window_rows,
)


def _row(message_id: str, role: str, ordinal: int, run_id: str | None = None) -> dict:
    return {
        "id": message_id,
        "role": role,
        "ordinal": ordinal,
        "run_id": run_id,
        "created_at": f"2026-07-06T00:00:{ordinal:02d}Z",
    }


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
