from __future__ import annotations

import sqlite3
import threading

import pytest

from core.database import (
    DatabaseManager,
    RuntimeEpisodeIdempotencyConflict,
    _runtime_episode_payload_fingerprint,
)
from core.runtime_episodes import (
    RuntimeEpisodeDurabilityError,
    build_runtime_episode,
    heartbeat_runtime_episode,
    persist_handoff_ref,
    persist_runtime_episode,
)
import core.runtime_episodes as runtime_episodes_module


def _episode(*, episode_id: str, key: str, reason: str = "same") -> dict:
    return build_runtime_episode(
        need={
            "episodeId": episode_id,
            "idempotencyKey": key,
            "kind": "research",
            "source": "test",
            "reason": reason,
            "inputs": {"question": "same"},
        },
        kind="research",
        state="queued",
    )


def _create_binding(database: DatabaseManager, *, session_id: str, run_id: str) -> None:
    database.create_or_update_session(session_id, "Episode durability", user_id="test-user")
    database.create_run_record(
        run_id=run_id,
        session_id=session_id,
        run_type="runtime_episode_test",
        status="running",
    )


def test_episode_idempotency_ledger_returns_original_and_rejects_payload_reuse(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency.db")
    first = database.upsert_runtime_episode_record(_episode(episode_id="episode-a", key="request-1"))
    retry = database.upsert_runtime_episode_record(_episode(episode_id="episode-b", key="request-1"))

    assert first["episodeId"] == "episode-a"
    assert retry["episodeId"] == "episode-a"
    with pytest.raises(RuntimeEpisodeIdempotencyConflict):
        database.upsert_runtime_episode_record(
            _episode(episode_id="episode-c", key="request-1", reason="different")
        )

    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT episode_id FROM runtime_episode_idempotency WHERE idempotency_key = ?",
            ("request-1",),
        ).fetchone()
    assert row["episode_id"] == "episode-a"


def test_episode_idempotency_key_is_session_scoped(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-scope.db")
    _create_binding(database, session_id="session-a", run_id="run-a")
    _create_binding(database, session_id="session-a", run_id="run-retry")
    _create_binding(database, session_id="session-b", run_id="run-b")
    first = database.upsert_runtime_episode_record(
        _episode(episode_id="episode-session-a", key="same-request"),
        session_id="session-a",
        run_id="run-a",
    )
    second = database.upsert_runtime_episode_record(
        _episode(episode_id="episode-session-b", key="same-request"),
        session_id="session-b",
        run_id="run-b",
    )
    retry_same_session = database.upsert_runtime_episode_record(
        _episode(episode_id="episode-retry", key="same-request"),
        session_id="session-a",
        run_id="run-retry",
    )

    assert first["episodeId"] == "episode-session-a"
    assert second["episodeId"] == "episode-session-b"
    assert retry_same_session["episodeId"] == "episode-session-a"


def test_legacy_unbound_idempotency_row_can_bind_same_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-backfill.db")
    episode = _episode(episode_id="episode-legacy", key="legacy-request")
    database.upsert_runtime_episode_record(episode)
    legacy_payload = {
        "sessionId": None,
        "parentEpisodeId": None,
        "kind": "research",
        "source": "test",
        "reason": "same",
        "inputs": {"question": "same"},
        "requiredRuntimeAccess": [],
        "handoffRefs": [],
        "targetKind": None,
        "targetId": None,
    }
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_idempotency SET payload_fingerprint = ? WHERE idempotency_key = ?",
            (_runtime_episode_payload_fingerprint(legacy_payload), "legacy-request"),
        )
        conn.commit()
    _create_binding(database, session_id="session-bound", run_id="run-bound")

    rebound = database.upsert_runtime_episode_record(
        episode,
        session_id="session-bound",
        run_id="run-bound",
    )

    assert rebound["episodeId"] == "episode-legacy"
    assert rebound["session_id"] == "session-bound"
    assert rebound["run_id"] == "run-bound"
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT session_id, run_id, payload_fingerprint FROM runtime_episode_idempotency WHERE idempotency_key = ?",
            ("legacy-request",),
        ).fetchone()
    assert row["session_id"] == "session-bound"
    assert row["run_id"] == "run-bound"
    assert row["payload_fingerprint"] != _runtime_episode_payload_fingerprint(legacy_payload)


def test_legacy_unbound_key_cannot_create_scoped_duplicate_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-legacy-duplicate.db")
    database.upsert_runtime_episode_record(
        _episode(episode_id="episode-original", key="legacy-duplicate")
    )
    _create_binding(database, session_id="session-bound", run_id="run-bound")

    with pytest.raises(RuntimeEpisodeIdempotencyConflict):
        database.upsert_runtime_episode_record(
            _episode(episode_id="episode-duplicate", key="legacy-duplicate"),
            session_id="session-bound",
            run_id="run-bound",
        )

    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_episode_idempotency"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_episodes"
        ).fetchone()[0] == 1


def test_episode_persistence_does_not_fabricate_missing_session_or_run(tmp_path):
    database = DatabaseManager(tmp_path / "episode-binding-fail-closed.db")

    with pytest.raises(sqlite3.IntegrityError):
        database.upsert_runtime_episode_record(
            _episode(episode_id="episode-unbound", key="request-unbound"),
            session_id="missing-session",
            run_id="missing-run",
        )

    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM sessions WHERE id = 'missing-session'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM run_records WHERE id = 'missing-run'"
        ).fetchone() is None


def test_concurrent_episode_ingress_claims_one_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-concurrent.db")
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[Exception] = []

    def worker(episode_id: str):
        try:
            barrier.wait()
            record = database.upsert_runtime_episode_record(_episode(episode_id=episode_id, key="request-2"))
            results.append(record["episodeId"])
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"episode-{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 2
    assert len(set(results)) == 1


def test_episode_persistence_and_heartbeat_fail_closed(monkeypatch):
    class BrokenDb:
        def upsert_runtime_episode_record(self, *args, **kwargs):
            raise OSError("disk unavailable")

        def heartbeat_runtime_episode(self, *args, **kwargs):
            return False

    monkeypatch.setattr(runtime_episodes_module, "db", BrokenDb())
    episode = _episode(episode_id="episode-broken", key="request-broken")

    with pytest.raises(RuntimeEpisodeDurabilityError):
        persist_runtime_episode(episode)
    with pytest.raises(RuntimeEpisodeDurabilityError):
        heartbeat_runtime_episode("episode-broken")


def test_handoff_persistence_fails_closed_on_rejection_and_database_error(monkeypatch):
    handoff = {
        "handoffRefId": "handoff-broken",
        "producerEpisodeId": "episode-broken",
        "kind": "research",
    }

    class RejectedDb:
        def add_runtime_episode_handoff(self, *args, **kwargs):
            return None

    monkeypatch.setattr(runtime_episodes_module, "db", RejectedDb())
    with pytest.raises(RuntimeEpisodeDurabilityError, match="persistence was rejected"):
        persist_handoff_ref(handoff)

    class BrokenDb:
        def add_runtime_episode_handoff(self, *args, **kwargs):
            raise OSError("disk unavailable")

    monkeypatch.setattr(runtime_episodes_module, "db", BrokenDb())
    with pytest.raises(RuntimeEpisodeDurabilityError, match="failed to persist"):
        persist_handoff_ref(handoff)


def test_expected_state_handoff_cannot_write_through_another_worker_lease(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-cas.db")
    episode = _episode(episode_id="episode-handoff-cas", key="request-handoff-cas")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    started = database.complete_runtime_episode(
        episode["episodeId"],
        state="active",
        expected_state="queued",
    )
    assert started is not None
    with database.get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_episodes
            SET worker_id = 'worker-owner', lease_generation = 1,
                lease_expires_at = '2999-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (episode["episodeId"],),
        )
        conn.commit()

    assert database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff={
            "handoffRefId": "handoff-stale-unfenced",
            "producerEpisodeId": episode["episodeId"],
            "kind": "research",
        },
    ) is None

    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff={
            "handoffRefId": "handoff-stale",
            "producerEpisodeId": episode["episodeId"],
            "kind": "research",
        },
        expected_state="active",
    )

    assert persisted is None
    assert database.list_runtime_episode_handoffs(episode["episodeId"]) == []


def test_delete_session_cleans_episode_and_side_effect_ledgers(tmp_path):
    database = DatabaseManager(tmp_path / "episode-delete-session.db")
    _create_binding(database, session_id="session-delete", run_id="run-delete")
    database.upsert_runtime_episode_record(
        _episode(episode_id="episode-delete", key="request-delete"),
        session_id="session-delete",
        run_id="run-delete",
        enqueue=True,
    )
    database.claim_side_effect_receipt(
        session_id="session-delete",
        run_id="run-delete",
        idempotency_key="side-effect-delete",
        effect_kind="test.effect",
        step_key="delete.step",
        target_identity="target",
        payload_fingerprint="payload",
        owner_id="owner",
    )

    database.delete_session("session-delete")

    assert database.get_session("session-delete") is None
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_episodes").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_episode_idempotency"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_side_effect_receipts"
        ).fetchone()[0] == 0


def test_unfenced_cancel_cannot_overwrite_a_reclaimed_active_lease(tmp_path):
    database = DatabaseManager(tmp_path / "episode-cancel-fence.db")
    episode = _episode(episode_id="episode-cancel-fence", key="request-cancel-fence")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    first = database.claim_runtime_episode(worker_id="worker-a", lease_seconds=30, kinds=["research"])
    assert first is not None
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00.000Z", episode["episodeId"]),
        )
        conn.execute(
            "UPDATE runtime_episode_queue SET lease_expires_at = ? WHERE episode_id = ?",
            ("2000-01-01T00:00:00.000Z", episode["episodeId"]),
        )
        conn.commit()
    second = database.claim_runtime_episode(worker_id="worker-b", lease_seconds=30, kinds=["research"])
    assert second is not None

    assert database.heartbeat_runtime_episode(
        episode["episodeId"],
        progress="stale unfenced heartbeat",
    ) is False
    assert database.resume_runtime_episode(
        episode["episodeId"],
        resume_token={"stale": True},
    ) is None
    assert database.cancel_runtime_episode(episode["episodeId"], reason="stale request") is None
    assert database.complete_runtime_episode(
        episode["episodeId"],
        state="failed",
        error_message="stale completion",
    ) is None
    current = database.get_runtime_episode(episode["episodeId"])
    assert current is not None
    assert current["state"] == "active"
    assert current["worker_id"] == "worker-b"
    assert current["lastProgress"] != "stale unfenced heartbeat"


def test_fenced_waiting_transition_releases_lease_for_immediate_resume(tmp_path):
    database = DatabaseManager(tmp_path / "episode-waiting-resume.db")
    episode = _episode(episode_id="episode-waiting-resume", key="request-waiting-resume")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    claimed = database.claim_runtime_episode(
        worker_id="worker-waiting",
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None

    waiting = database.complete_runtime_episode(
        episode["episodeId"],
        state="waiting_child",
        worker_id="worker-waiting",
        lease_generation=int(claimed["leaseGeneration"]),
    )

    assert waiting is not None
    assert waiting["state"] == "waiting_child"
    assert not waiting["worker_id"]
    assert not waiting["lease_expires_at"]
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
    assert dict(queue) == {
        "state": "waiting_child",
        "locked_by": None,
        "lease_expires_at": None,
    }

    resumed = database.resume_runtime_episode(
        episode["episodeId"],
        resume_token={"source": "child_handoff"},
        expected_state="waiting_child",
    )
    assert resumed is not None
    assert resumed["state"] == "queued"


def test_retry_clears_episode_and_queue_ownership_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-retry-release.db")
    episode = _episode(episode_id="episode-retry-release", key="request-retry-release")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    claimed = database.claim_runtime_episode(
        worker_id="worker-retry",
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None

    retried = database.retry_runtime_episode(
        episode["episodeId"],
        worker_id="worker-retry",
        lease_generation=int(claimed["leaseGeneration"]),
    )

    assert retried is not None
    assert retried["state"] == "queued"
    assert retried["worker_id"] is None
    assert retried["lease_expires_at"] is None
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
    assert dict(queue) == {
        "state": "retry",
        "locked_by": None,
        "lease_expires_at": None,
    }


def test_unfenced_retry_cannot_reset_a_live_or_owned_waiting_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-retry-fence.db")
    active = _episode(episode_id="episode-retry-active", key="request-retry-active")
    waiting = _episode(episode_id="episode-retry-waiting", key="request-retry-waiting")
    database.upsert_runtime_episode_record(active, enqueue=True)
    database.upsert_runtime_episode_record(waiting, enqueue=True)
    with database.get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_episodes
            SET state = 'active', worker_id = 'worker-active',
                lease_expires_at = '2999-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (active["episodeId"],),
        )
        conn.execute(
            """
            UPDATE runtime_episodes
            SET state = 'waiting', worker_id = 'worker-waiting',
                lease_expires_at = '2999-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (waiting["episodeId"],),
        )
        conn.commit()

    assert database.retry_runtime_episode(active["episodeId"], error_message="stale") is None
    assert database.retry_runtime_episode(waiting["episodeId"], error_message="stale") is None

    active_current = database.get_runtime_episode(active["episodeId"])
    waiting_current = database.get_runtime_episode(waiting["episodeId"])
    assert active_current["state"] == "active"
    assert active_current["worker_id"] == "worker-active"
    assert waiting_current["state"] == "waiting"
    assert waiting_current["worker_id"] == "worker-waiting"


def test_bulk_cancel_clears_active_episode_ownership_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-bulk-cancel-release.db")
    _create_binding(database, session_id="session-cancel", run_id="run-cancel")
    episode = _episode(episode_id="episode-bulk-cancel", key="request-bulk-cancel")
    database.upsert_runtime_episode_record(
        episode,
        session_id="session-cancel",
        run_id="run-cancel",
        enqueue=True,
    )
    claimed = database.claim_runtime_episode(
        worker_id="worker-cancel",
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None

    cancelled = database.cancel_active_runtime_episodes_for_run(
        "run-cancel",
        reason="test cancellation",
    )

    current = next(item for item in cancelled if item["episodeId"] == episode["episodeId"])
    assert current["state"] == "cancelled"
    assert current["worker_id"] is None
    assert current["lease_expires_at"] is None


def test_resume_clears_legacy_episode_and_queue_ownership_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-resume-release.db")
    episode = _episode(episode_id="episode-resume-release", key="request-resume-release")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    with database.get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_episodes
            SET state = 'waiting', worker_id = 'legacy-worker',
                lease_expires_at = '2000-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (episode["episodeId"],),
        )
        conn.execute(
            """
            UPDATE runtime_episode_queue
            SET state = 'leased', locked_by = 'legacy-worker',
                lease_expires_at = '2000-01-01T00:00:00.000Z'
            WHERE episode_id = ?
            """,
            (episode["episodeId"],),
        )
        conn.commit()

    resumed = database.resume_runtime_episode(
        episode["episodeId"],
        resume_token={"source": "legacy_recovery"},
        expected_state="waiting",
    )

    assert resumed is not None
    assert resumed["state"] == "queued"
    assert resumed["worker_id"] is None
    assert resumed["lease_expires_at"] is None
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
    assert dict(queue) == {
        "state": "queued",
        "locked_by": None,
        "lease_expires_at": None,
    }
