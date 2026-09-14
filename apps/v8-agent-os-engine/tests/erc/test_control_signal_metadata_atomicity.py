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
