from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from core.database import DatabaseManager
from erc.run_service import RunService
import erc.run_service as run_module


@pytest.mark.parametrize("new_status", ["paused", "completed", "cancelled"])
def test_control_signal_preserves_concurrent_wait_human_metadata_and_terminal_status(tmp_path, monkeypatch, new_status):
    database = DatabaseManager(tmp_path / "control.sqlite3")
    database.create_or_update_session("session", "Fixture", user_id="owner")
    database.create_run_record("run", "session", user_id="owner", run_type="chat", status="running",
                               metadata={"sessionResultWait": {"state": "waiting", "generation": "old"}})
    concurrent_database = DatabaseManager(database.db_path)
    monkeypatch.setattr(run_module, "db", database)
    writer_ready, concurrent_done = Event(), Event()
    original_write = database._run_write_with_retry

    def delayed_control_write(write):
        # Both the old whole-record setter and the atomic field setter reach
        # this real SQLite write seam. The old setter already captured stale
        # metadata/status here; the new setter reads inside its transaction.
        writer_ready.set()
        assert concurrent_done.wait(10), "concurrent writer failed to finish"
        return original_write(write)

    monkeypatch.setattr(database, "_run_write_with_retry", delayed_control_write)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(RunService().set_control_signal, "run", command="cancel", reason="fixture-request")
        assert writer_ready.wait(10), "control write did not start"
        concurrent_database.update_run_record("run", status=new_status, metadata={
            "sessionResultWait": {"state": "waiting", "generation": "new"},
            "humanRevision": "user-correction-2", "pause_reason": "human_pause",
        })
        concurrent_done.set()
        assert future.result(timeout=10)
    actual = concurrent_database.get_run_record("run")
    assert actual["status"] == new_status
    assert actual["metadata"]["sessionResultWait"]["generation"] == "new"
    assert actual["metadata"]["humanRevision"] == "user-correction-2"
    assert actual["metadata"]["pause_reason"] == "human_pause"
    assert actual["metadata"]["control_signal"]["command"] == "cancel"


def test_stale_clear_preserves_new_control_scope_and_paused_status(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "clear.sqlite3")
    database.create_or_update_session("session", "Fixture", user_id="owner")
    initial = {"control_signal": {"command": "guidance", "reason": "old"}}
    database.create_run_record("run", "session", run_type="chat", status="running", metadata=initial)
    concurrent = DatabaseManager(database.db_path)
    monkeypatch.setattr(run_module, "db", database)
    ready, changed = Event(), Event()
    original_write = database._run_write_with_retry
    def delayed(write):
        ready.set()
        assert changed.wait(10)
        return original_write(write)
    monkeypatch.setattr(database, "_run_write_with_retry", delayed)
    latest = {
        "control_signal": {"command": "cancel", "reason": "new"},
        "sessionAssignment": {"state": "active", "cancelRequested": True},
        "runtimeEpisodeResume": {"state": "waiting", "waitGeneration": 2},
        "humanRevision": "new-goal",
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(RunService().clear_control_signal, "run")
        assert ready.wait(10)
        concurrent.update_run_record("run", status="paused", metadata=latest)
        changed.set()
        future.result(timeout=10)
    actual = concurrent.get_run_record("run")
    assert actual["status"] == "paused"
    assert actual["metadata"] == latest


def test_separate_control_service_consumes_latest_persisted_signal_once(tmp_path, monkeypatch):
    from erc.command_service import CommandService
    database = DatabaseManager(tmp_path / "instances.sqlite3")
    database.create_or_update_session("session", "Fixture", user_id="owner")
    database.create_run_record("run", "session", run_type="chat", status="running")
    monkeypatch.setattr(run_module, "db", database)
    first, second = CommandService(), CommandService()
    first.issue_control_signal("run", command="guidance", reason="old")
    second.issue_control_signal("run", command="cancel", reason="new")
    assert first.peek_control_signal("run")["command"] == "cancel"
    assert first.consume_control_signal("run")["command"] == "cancel"
    assert second.consume_control_signal("run") is None
    assert first.peek_control_signal("run") is None


def test_control_consumption_is_single_claim_across_service_instances(tmp_path, monkeypatch):
    from threading import Barrier
    from erc.command_service import CommandService
    database = DatabaseManager(tmp_path / "consume.sqlite3")
    database.create_or_update_session("session", "Fixture", user_id="owner")
    database.create_run_record("run", "session", run_type="chat", status="running")
    monkeypatch.setattr(run_module, "db", database)
    control = CommandService()
    control.issue_control_signal("run", command="pause", reason="single-consumption")
    barrier = Barrier(4)
    def consume():
        barrier.wait()
        return CommandService().consume_control_signal("run")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: consume(), range(4)))
    assert sum(result is not None for result in results) == 1


def test_empty_control_poll_does_not_wait_for_an_unrelated_writer(tmp_path):
    """Streaming observes no command without competing for the episode writer."""
    database = DatabaseManager(tmp_path / "idle-control.sqlite3")
    database.create_or_update_session("session", "Fixture", user_id="owner")
    database.create_run_record("run", "session", run_type="chat", status="running",
                               metadata={"humanRevision": "keep"})
    finished = Event()

    def poll():
        try:
            return database.consume_run_control_signal("run")
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool, database.get_connection() as writer:
        writer.execute("BEGIN IMMEDIATE")
        future = pool.submit(poll)
        try:
            # The writer stays locked until this assertion resolves. This is a
            # deadlock counterexample, not a wall-clock performance threshold.
            assert finished.wait(3), "empty stream poll waits for the SQLite writer"
        finally:
            writer.rollback()
        result = future.result(timeout=5)
    assert result["signal"] is None
    assert database.get_run_record("run")["metadata"] == {"humanRevision": "keep"}
