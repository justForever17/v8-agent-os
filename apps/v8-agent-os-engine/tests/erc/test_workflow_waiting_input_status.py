from __future__ import annotations

from erc import workflow_ledger as workflow_ledger_module


class _FakeDb:
    def __init__(self):
        self.workflow_update = None
        self.step_update = None

    def get_workflow_ledger_for_run(self, run_id):
        return {
            "id": "workflow-1",
            "current_step_id": "step-1",
            "resume_strategy": None,
        }

    def update_workflow_ledger(self, workflow_id, **kwargs):
        self.workflow_update = (workflow_id, kwargs)

    def get_workflow_step(self, step_id):
        return {
            "id": step_id,
            "session_id": "session-1",
            "step_key": "supervisor",
            "title": "Supervisor",
            "status": "running",
            "sequence_index": 0,
        }

    def upsert_workflow_step(self, **kwargs):
        self.step_update = kwargs


def test_waiting_input_remains_a_waiting_state_not_a_failure(monkeypatch):
    fake_db = _FakeDb()
    monkeypatch.setattr(workflow_ledger_module, "db", fake_db)

    workflow_ledger_module.workflow_ledger_service.sync_run_status(
        "run-1",
        run_status="waiting_input",
    )

    assert fake_db.workflow_update[1]["status"] == "waiting_input"
    assert fake_db.workflow_update[1]["clear_error"] is True
    assert fake_db.step_update["status"] == "waiting_input"
