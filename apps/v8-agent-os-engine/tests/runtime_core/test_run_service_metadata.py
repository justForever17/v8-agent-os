from __future__ import annotations

from core.database import DatabaseManager
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


def test_runtime_episode_resume_schedule_claim_is_atomic(tmp_path):
    manager = DatabaseManager(tmp_path / "state.db")
    manager.create_or_update_session("session_runtime", "Runtime Resume")
    manager.create_run_record(
        run_id="run_runtime",
        session_id="session_runtime",
        run_type="chat",
        status="running",
        metadata={"runtimeEpisodeResume": {"state": "waiting"}},
    )
    manager.upsert_runtime_episode_record(
        {
            "episodeId": "episode_runtime",
            "kind": "engineering",
            "state": "completed",
        },
        session_id="session_runtime",
        run_id="run_runtime",
    )

    first = manager.claim_runtime_episode_resume_schedule(
        "run_runtime",
        marker_key="runtimeEpisodeResume",
        next_marker={"state": "scheduled", "episodeId": "episode_runtime"},
        terminal_states={"completed", "failed", "cancelled", "degraded", "merged"},
        active_states={"active", "queued", "waiting"},
    )
    second = manager.claim_runtime_episode_resume_schedule(
        "run_runtime",
        marker_key="runtimeEpisodeResume",
        next_marker={"state": "scheduled", "episodeId": "episode_runtime"},
        terminal_states={"completed", "failed", "cancelled", "degraded", "merged"},
        active_states={"active", "queued", "waiting"},
    )

    assert first["claimed"] is True
    assert first["run_record"]["metadata"]["runtimeEpisodeResume"]["state"] == "scheduled"
    assert second["claimed"] is False
    assert second["reason"] == "runtime_episode_resume_already_scheduled"


def test_runtime_episode_resume_schedule_claim_rejects_active_top_level(tmp_path):
    manager = DatabaseManager(tmp_path / "state.db")
    manager.create_or_update_session("session_runtime", "Runtime Resume")
    manager.create_run_record(
        run_id="run_runtime",
        session_id="session_runtime",
        run_type="chat",
        status="running",
        metadata={"runtimeEpisodeResume": {"state": "waiting"}},
    )
    manager.upsert_runtime_episode_record(
        {
            "episodeId": "episode_active",
            "kind": "engineering",
            "state": "active",
        },
        session_id="session_runtime",
        run_id="run_runtime",
    )

    result = manager.claim_runtime_episode_resume_schedule(
        "run_runtime",
        marker_key="runtimeEpisodeResume",
        next_marker={"state": "scheduled", "episodeId": "episode_active"},
        terminal_states={"completed", "failed", "cancelled", "degraded", "merged"},
        active_states={"active", "queued", "waiting"},
    )

    assert result["claimed"] is False
    assert result["reason"] == "top_level_runtime_episode_still_active"
    assert manager.get_run_record("run_runtime")["metadata"]["runtimeEpisodeResume"]["state"] == "waiting"
