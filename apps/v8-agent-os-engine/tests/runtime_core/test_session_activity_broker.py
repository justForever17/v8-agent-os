from unittest import mock

from core.database import DatabaseManager
from core.session_activity import SessionActivityBroker, is_session_activity_topic


def test_activity_topic_filter_ignores_high_frequency_runtime_noise() -> None:
    assert is_session_activity_topic("run.started") is True
    assert is_session_activity_topic("runtime.episode.completed") is True
    assert is_session_activity_topic("subagent.task.failed") is True
    assert is_session_activity_topic("run.text.delta") is False
    assert is_session_activity_topic("creative_media.tool.finished") is False
    assert is_session_activity_topic("run.liveness.heartbeat") is False


def test_activity_broker_is_owner_scoped_and_advances_over_other_owner_events() -> None:
    broker = SessionActivityBroker(capacity=64)
    first = broker.publish(owner_id="sunny@example.com", session_id="one", topic="run.started")
    second = broker.publish(owner_id="other@example.com", session_id="two", topic="run.started")

    assert first == 1
    assert second == 2
    cursor, sunny_events = broker.wait(owner_id="SUNNY@example.com", after_seq=0, timeout_seconds=0.05)
    assert cursor == 2
    assert [event["sessionId"] for event in sunny_events] == ["one"]

    cursor, no_events = broker.wait(
        owner_id="sunny@example.com",
        after_seq=cursor,
        timeout_seconds=0.01,
    )
    assert cursor == 2
    assert no_events == []


def test_durable_runtime_event_publishes_only_compact_activity_signal(tmp_path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-1", "Activity", user_id="sunny@example.com")

    with mock.patch("core.session_activity.session_activity_broker.publish") as publish:
        database.add_runtime_event({
            "event_id": "event-1",
            "session_id": "session-1",
            "topic": "run.started",
            "ts": "2026-07-26T00:00:00Z",
            "payload": {"secret": "must-not-be-forwarded"},
        })
        database.add_runtime_event({
            "event_id": "event-2",
            "session_id": "session-1",
            "topic": "tool.finished",
            "ts": "2026-07-26T00:00:01Z",
            "payload": {"output": "runtime-noise"},
        })

    publish.assert_called_once_with(
        owner_id="sunny@example.com",
        session_id="session-1",
        topic="run.started",
    )
    assert [event["topic"] for event in database.get_runtime_events("session-1")] == [
        "run.started",
        "tool.finished",
    ]


def test_episode_cancellation_does_not_depend_on_client_activity_delivery(tmp_path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-1", "Activity", user_id="sunny@example.com")
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO run_records (id, session_id, run_type, status) VALUES (?, ?, ?, ?)",
            ("run-1", "session-1", "chat", "running"),
        )
        conn.commit()
    database.upsert_runtime_episode_record({
        "episodeId": "episode-1",
        "sessionId": "session-1",
        "runId": "run-1",
        "kind": "engineering",
        "state": "active",
    })

    cancelled = database.cancel_active_runtime_episodes_for_run("run-1", reason="test")

    assert [episode["episodeId"] for episode in cancelled] == ["episode-1"]
    assert cancelled[0]["state"] == "cancelled"
