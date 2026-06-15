from __future__ import annotations

from erc.run_service import RunService


def test_transition_run_merges_metadata(monkeypatch):
    service = RunService()
    stored = {
        "id": "run_demo",
        "session_id": "session_demo",
        "run_type": "chat",
        "metadata": {"provider": "doubao", "model": "doubao-seed-2.0-pro", "specId": "spec_demo"},
    }
    captured: dict[str, object] = {}

    def fake_get_run_record(_run_id):
        return dict(stored)

    def fake_update_run_record(_run_id, **kwargs):
        captured.update(kwargs)
        stored["metadata"] = kwargs.get("metadata")
        stored["status"] = kwargs.get("status")

    monkeypatch.setattr("erc.run_service.db.get_run_record", fake_get_run_record)
    monkeypatch.setattr("erc.run_service.db.update_run_record", fake_update_run_record)
    monkeypatch.setattr("erc.run_service.run_ledger_service.record_event", lambda **_kwargs: None)

    service.transition_run("run_demo", status="running", metadata={"resume_reason": "spec_auto_approved"})

    assert captured["metadata"] == {
        "provider": "doubao",
        "model": "doubao-seed-2.0-pro",
        "specId": "spec_demo",
        "resume_reason": "spec_auto_approved",
    }
