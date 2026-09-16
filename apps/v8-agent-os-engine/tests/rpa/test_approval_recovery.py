from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.database import DatabaseManager
from erc.command_router import RuntimeCommandRouter


@pytest.fixture
def database(tmp_path, monkeypatch):
    import erc.command_router as router
    import erc.workflow_ledger as ledger
    import erc.snapshot_service as snapshots

    instance = DatabaseManager(tmp_path / "rpa-recovery.db")
    for module in (router, ledger, snapshots):
        monkeypatch.setattr(module, "db", instance)
    return instance


def seed(database, key, *, runtime="rpa", delivery="executing", status="running"):
    database.create_or_update_session(key, "isolated recovery fixture", user_id="fixture")
    database.create_run_record(run_id=key, session_id=key, user_id="fixture", run_type=runtime, status=status,
        metadata={"executionState": "resuming", "executionIdentity": key,
                  "resumeExecution": {"mode": "existing_robot"}, "command": ["never-launch-fixture"],
                  "control_signal": {"command": "cancel"} if status == "cancelled" else {}})
    database.add_pending_approval(approval_id=key, session_id=key, run_id=key, approval_kind="rpa_review",
                                 status="approved", request={"operationFingerprint": key})
    with database.get_connection() as connection:
        connection.execute("UPDATE pending_approvals SET resume_json=? WHERE id=?",
                           (json.dumps({"state": delivery, "generation": "fixture-generation", "attempt": 1}), key))
        connection.commit()


def test_restart_fences_rpa_execution_and_persists_unknown_events_without_replaying(database, monkeypatch):
    seed(database, "rpa")
    router = RuntimeCommandRouter()
    monkeypatch.setattr(router, "deliver_approval_resume", lambda *args, **kwargs: pytest.fail("unknown effects must not be replayed"))
    router.recover_approval_resumes()
    assert database.get_run_record("rpa")["status"] == "running", "periodic recovery must leave a live worker alone"
    router.recover_approval_resumes(restart=True)
    run = database.get_run_record("rpa")
    assert run["status"] == "failed"
    assert run["metadata"]["executionState"] == "unknown"
    assert run["metadata"]["reconciliationRequired"] is True
    assert run["metadata"]["command"] == ["never-launch-fixture"]
    assert run["finished_at"]
    approval = database.get_pending_approval("rpa")
    assert approval["status"] == "approved", "recovery does not rewrite the user's decision"
    assert json.loads(approval["resume_json"])["state"] == "blocked"
    assert not database.claim_approval_resume("rpa", restart=True)["claimed"]
    events = database.get_runtime_events_for_run("rpa")
    assert [event["topic"] for event in events] == ["run.state.changed", "rpa.execution.reconciliation_required"]
    assert events[1]["payload"]["status"] == "unknown"
    assert events[1]["payload"]["reconciliationRequired"] is True
    snapshot = database.get_latest_runtime_snapshot("rpa", snapshot_type="chat_projection")
    assert snapshot["latest_seq"] == events[-1]["seq"]
    router.recover_approval_resumes(restart=True)
    assert database.get_runtime_events_for_run("rpa") == events, "a second restart must not duplicate recovery events"


def test_recovery_does_not_change_chat_or_undispatched_deliveries(database):
    for key, runtime, delivery in [("chat", "chat", "executing"), ("scheduled", "rpa", "scheduled"), ("pending", "rpa", "pending")]:
        seed(database, key, runtime=runtime, delivery=delivery)
    before = {key: (database.get_run_record(key), database.get_pending_approval(key)) for key in ("chat", "scheduled", "pending")}
    assert database.recover_interrupted_rpa_approval_resumes() == []
    for key, records in before.items():
        assert (database.get_run_record(key), database.get_pending_approval(key)) == records
        assert not database.get_runtime_events_for_run(key)


@pytest.mark.parametrize("status", ["cancelled", "interrupted", "paused"])
def test_recovery_preserves_stop_state_and_requires_external_effect_reconciliation(database, status):
    seed(database, "stopped", status=status)
    RuntimeCommandRouter().recover_approval_resumes(restart=True)
    run = database.get_run_record("stopped")
    assert run["status"] == status
    assert run["metadata"]["reconciliationRequired"] is True
    assert run["metadata"]["executionState"] == "unknown"
    assert json.loads(database.get_pending_approval("stopped")["resume_json"])["state"] == "blocked"


def test_concurrent_startup_recovery_claims_one_transition(database):
    seed(database, "rpa")
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: database.recover_interrupted_rpa_approval_resumes(), range(2)))
    assert sum(len(result) for result in results) == 1
    assert len(database.get_runtime_events_for_run("rpa")) == 2


def test_event_write_failure_rolls_back_delivery_and_run_state_for_restart_retry(database, monkeypatch):
    seed(database, "rpa")
    original = database._allocate_runtime_event_seq
    def failed_event(*args, **kwargs):
        raise RuntimeError("fixture persistence failure")
    monkeypatch.setattr(database, "_allocate_runtime_event_seq", failed_event)
    with pytest.raises(RuntimeError, match="persistence failure"):
        database.recover_interrupted_rpa_approval_resumes()
    assert database.get_run_record("rpa")["status"] == "running"
    assert json.loads(database.get_pending_approval("rpa")["resume_json"])["state"] == "executing"
    assert not database.get_runtime_events_for_run("rpa")
    monkeypatch.setattr(database, "_allocate_runtime_event_seq", original)
    assert len(database.recover_interrupted_rpa_approval_resumes()) == 1
