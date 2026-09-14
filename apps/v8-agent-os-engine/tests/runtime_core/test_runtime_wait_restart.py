import pytest

from core.database import DatabaseManager
from core.runtime_episodes import build_runtime_episode
from erc.workflow_ledger import WorkflowLedgerService


@pytest.fixture
def database(tmp_path, monkeypatch):
    import erc.workflow_ledger as ledger
    instance = DatabaseManager(tmp_path / "restart.db")
    instance.create_or_update_session("session", "wait", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="running")
    monkeypatch.setattr(ledger, "db", instance)
    monkeypatch.setattr(WorkflowLedgerService, "emit_reconciliation_event", lambda *args, **kwargs: None)
    return instance


def parked(database, *, state="waiting", generation=1, ids=None):
    database.upsert_runtime_episode_record(build_runtime_episode(need={"episodeId": "A", "kind": "delegation"},
        kind="delegation", state="queued"), session_id="session", run_id="run", enqueue=True)
    marker = {"state": state, "waitGeneration": generation, "episodeIds": ["A"] if ids is None else ids,
              "reason": "runtime_episode_active_at_stream_end"}
    database.update_run_record("run", status="running", metadata={"runtimeEpisodeResume": marker})
    return marker


@pytest.mark.parametrize("state", ["waiting", "scheduled"])
def test_restart_preserves_real_parked_parent_and_its_episode(database, state):
    marker = parked(database, state=state)
    WorkflowLedgerService().reconcile_orphaned_runs()
    restored = DatabaseManager(database.db_path)
    assert restored.get_run_record("run")["status"] == "running"
    assert restored.get_run_record("run")["metadata"]["runtimeEpisodeResume"] == marker
    assert restored.get_runtime_episode("A")["state"] == "queued"


@pytest.mark.parametrize("overrides", [{"generation": None}, {"generation": 0}, {"ids": []}, {"ids": ["missing"]}, {"state": "executing"}])
def test_restart_does_not_keep_unproven_or_already_consumed_wait(database, overrides):
    parked(database, **overrides)
    WorkflowLedgerService().reconcile_orphaned_runs()
    assert database.get_run_record("run")["status"] == "interrupted"


def test_wait_cannot_claim_an_episode_from_another_run(database):
    parked(database)
    database.create_run_record(run_id="foreign", session_id="session", run_type="chat", status="completed")
    with database.get_connection() as conn:
        conn.execute("UPDATE runtime_episodes SET run_id='foreign' WHERE id='A'")
        conn.commit()
    WorkflowLedgerService().reconcile_orphaned_runs()
    assert database.get_run_record("run")["status"] == "interrupted"


def test_late_resume_claim_cannot_overwrite_new_wait_generation(database):
    marker = parked(database, generation=2)
    database.complete_runtime_episode("A", state="completed")
    result = database.claim_runtime_episode_resume_schedule("run", marker_key="runtimeEpisodeResume",
        next_marker={**marker, "state": "scheduled", "waitGeneration": 1}, terminal_states={"completed"}, active_states={"queued"})
    assert not result["claimed"]
    assert database.get_run_record("run")["metadata"]["runtimeEpisodeResume"]["waitGeneration"] == 2


@pytest.mark.parametrize("command", ["cancel", "interrupt", "pause"])
def test_terminal_handoff_does_not_schedule_over_control_signal(database, monkeypatch, command):
    import erc.command_router as router_module
    import erc.run_service as run_module
    from erc.command_router import RuntimeCommandRouter

    marker = parked(database)
    database.complete_runtime_episode("A", state="completed")
    # The executor may still be stopping: control precedes the terminal status.
    database.update_run_record("run", status="running", metadata={
        "runtimeEpisodeResume": marker, "control_signal": {"command": command},
    })
    monkeypatch.setattr(router_module, "db", database)
    monkeypatch.setattr(run_module, "db", database)
    router = RuntimeCommandRouter()
    scheduled = []
    router._schedule_chat_run = lambda *args, **kwargs: scheduled.append(kwargs) or "run"
    result = router.schedule_runtime_episode_handoff_resume(database.get_runtime_episode("A"))
    assert not result["resume_scheduled"]
    assert scheduled == []
    current = database.get_run_record("run")
    assert current["status"] == "running"
    assert current["metadata"]["control_signal"]["command"] == command
    assert current["metadata"]["runtimeEpisodeResume"] == marker


def test_late_scheduled_graph_cannot_consume_a_new_wait_generation(database):
    marker = parked(database, state="scheduled", generation=2)
    result = database.update_run_metadata_key_if_state("run", key="runtimeEpisodeResume", expected_state="scheduled",
        expected_status="running", expected_generation=1, next_value={**marker, "state": "executing", "waitGeneration": 1})
    assert not result["updated"]
    assert database.get_run_record("run")["metadata"]["runtimeEpisodeResume"] == marker


def test_terminal_result_arriving_before_restart_remains_deliverable(database):
    parked(database)
    database.commit_runtime_episode_delivery("A", state="completed", session_id="session", run_id="run",
        handoff={"handoffId": "ready", "kind": "delegation", "status": "ready", "results": [{"taskBriefId": "A", "status": "ok"}]})
    WorkflowLedgerService().reconcile_orphaned_runs()
    assert database.get_run_record("run")["status"] == "running"


def test_corrupt_episode_state_does_not_prove_a_wait(database):
    parked(database)
    with database.get_connection() as conn:
        conn.execute("UPDATE runtime_episodes SET state='unknown-state' WHERE id='A'")
        conn.commit()
    WorkflowLedgerService().reconcile_orphaned_runs()
    assert database.get_run_record("run")["status"] == "interrupted"
