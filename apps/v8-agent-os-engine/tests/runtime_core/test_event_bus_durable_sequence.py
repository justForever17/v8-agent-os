from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import erc.event_bus as event_bus_module
import pytest
from core.database import DatabaseManager
from erc.event_bus import RuntimeEventBus
from erc.models import RuntimeSource


def _emitter(bus: RuntimeEventBus, *, session_id: str, run_id: str):
    return bus.create_emitter(
        session_id=session_id,
        conversation_id=session_id,
        run_id=run_id,
        source=RuntimeSource(component="event_bus_test", node=run_id),
    )


def test_interleaved_emitters_return_and_advance_their_durable_sequences(
    tmp_path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(event_bus_module, "db", database)
    bus = RuntimeEventBus()
    database.create_or_update_session("session-seq", "Sequence test", user_id="test-user")
    database.create_run_record("run-a", "session-seq", user_id="test-user")
    database.create_run_record("run-b", "session-seq", user_id="test-user")

    first = _emitter(bus, session_id="session-seq", run_id="run-a")
    second = _emitter(bus, session_id="session-seq", run_id="run-b")

    emitted = [
        first.emit("run.started", {"emitter": "a"}),
        second.emit("run.started", {"emitter": "b"}),
        second.emit("run.progress", {"emitter": "b"}),
        first.emit("run.progress", {"emitter": "a"}),
    ]

    assert [event["seq"] for event in emitted if event] == [1, 2, 3, 4]
    assert first.next_seq == 5
    assert second.next_seq == 4
    durable = database.get_runtime_events("session-seq")
    assert [event["seq"] for event in durable] == [1, 2, 3, 4]
    assert [event["id"] for event in durable] == [event["event_id"] for event in emitted if event]


def test_add_runtime_event_returns_the_sequence_it_committed(tmp_path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-direct", "Direct sequence test", user_id="test-user")

    first_event = {
        "event_id": "event-direct-a",
        "session_id": "session-direct",
        "seq": 99,
        "topic": "run.started",
        "ts": "2026-08-17T00:00:00Z",
        "payload": {},
    }
    second_event = {
        "event_id": "event-direct-b",
        "session_id": "session-direct",
        "seq": 99,
        "topic": "run.progress",
        "ts": "2026-08-17T00:00:01Z",
        "payload": {},
    }

    first_seq = database.add_runtime_event(first_event)
    second_seq = database.add_runtime_event(second_event)

    assert (first_seq, second_seq) == (1, 2)
    assert (first_event["seq"], second_event["seq"]) == (1, 2)


def test_runtime_event_history_preserves_live_envelope_identity(tmp_path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-history", "History identity test", user_id="test-user")
    database.create_run_record("run-history", "session-history", user_id="test-user")
    event = {
        "event_id": "event-history-a",
        "session_id": "session-history",
        "run_id": "run-history",
        "seq": 1,
        "topic": "runtime.episode.progress",
        "ts": "2026-08-17T00:00:02Z",
        "payload": {"status": "running"},
    }

    database.add_runtime_event(event)

    session_history = database.get_runtime_events("session-history")
    run_history = database.get_runtime_events_for_run(
        "run-history",
        session_id="session-history",
    )
    for historical in (*session_history, *run_history):
        assert historical["event_id"] == event["event_id"]
        assert historical["ts"] == event["ts"]
        assert historical["seq"] == event["seq"]


def test_runtime_event_sequence_does_not_reuse_pruned_snapshot_watermark(tmp_path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-retention", "Retention sequence test", user_id="test-user")
    database.create_run_record("run-retention", "session-retention", user_id="test-user")

    for index in (1, 2):
        database.add_runtime_event({
            "event_id": f"event-retention-{index}",
            "session_id": "session-retention",
            "run_id": "run-retention",
            "seq": index,
            "topic": "run.progress",
            "ts": f"2026-08-17T00:00:0{index}Z",
            "payload": {"index": index},
        })
    database.add_runtime_snapshot(
        snapshot_id="snapshot-retention",
        session_id="session-retention",
        run_id="run-retention",
        latest_seq=2,
        snapshot_type="chat_projection",
        snapshot={"latestSeq": 2},
    )
    with database.get_connection() as conn:
        conn.execute("DELETE FROM runtime_events WHERE id = ?", ("event-retention-2",))
        conn.commit()

    next_event = {
        "event_id": "event-retention-3",
        "session_id": "session-retention",
        "run_id": "run-retention",
        "seq": 2,
        "topic": "run.progress",
        "ts": "2026-08-17T00:00:03Z",
        "payload": {"index": 3},
    }
    allocated = database.add_runtime_event(next_event)

    assert allocated == 3
    assert next_event["seq"] == 3
    assert database.get_latest_runtime_seq("session-retention") == 3
    assert [event["event_id"] for event in database.get_runtime_events("session-retention", after_seq=2)] == [
        "event-retention-3"
    ]


def test_runtime_event_sequence_is_monotonic_across_database_managers(tmp_path) -> None:
    path = tmp_path / "state.db"
    first_database = DatabaseManager(path)
    first_database.create_or_update_session("session-concurrent", "Concurrent sequence test", user_id="test-user")
    second_database = DatabaseManager(path)
    barrier = threading.Barrier(2)

    def append(database: DatabaseManager, event_id: str) -> int:
        event = {
            "event_id": event_id,
            "session_id": "session-concurrent",
            "seq": 1,
            "topic": "run.progress",
            "ts": "2026-08-17T00:00:04Z",
            "payload": {"eventId": event_id},
        }
        barrier.wait(timeout=5)
        committed = database.add_runtime_event(event)
        assert event["seq"] == committed
        return committed

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(append, first_database, "event-concurrent-a"),
            executor.submit(append, second_database, "event-concurrent-b"),
        ]
        committed = [future.result(timeout=10) for future in futures]

    assert sorted(committed) == [1, 2]
    history = first_database.get_runtime_events("session-concurrent")
    assert [event["seq"] for event in history] == [1, 2]
    assert first_database.get_latest_runtime_seq("session-concurrent") == 2


def test_emitter_does_not_misclassify_lineage_integrity_errors_as_sequence_races(
    tmp_path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(event_bus_module, "db", database)
    emitter = _emitter(RuntimeEventBus(), session_id="missing-session", run_id="missing-run")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        emitter.emit("run.started", {})
