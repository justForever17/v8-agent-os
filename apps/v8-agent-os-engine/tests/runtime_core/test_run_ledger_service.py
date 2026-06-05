from core.database import DatabaseManager
from core.observability_db import ObservabilityDatabaseManager
from core.run_ledger import RunLedgerService


def test_run_ledger_aggregates_core_refs(tmp_path, monkeypatch):
    import core.run_ledger as module

    state_db = DatabaseManager(tmp_path / "state.db")
    obs_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(module, "db", state_db)
    monkeypatch.setattr(module, "observability_db", obs_db)
    service = RunLedgerService()
    state_db.create_or_update_session("session_1", "Run Ledger Test")

    state_db.create_run_record(
        run_id="run_1",
        session_id="session_1",
        conversation_id=None,
        thread_id=None,
        user_id=None,
        run_type="chat",
        status="running",
        trigger_source="test",
        agent_id=None,
        workflow_id=None,
        channel_type="test",
        metadata={
            "task": "demo",
            "pendingExternalTools": {
                "openai:global:call_wire_1": {
                    "protocol": "openai",
                    "wireToolCallId": "call_wire_1",
                    "internalAliasName": "network_write",
                    "externalWireName": "Write",
                    "status": "waiting_external_tool",
                    "createdAt": "2026-05-01T00:00:00+00:00",
                }
            },
        },
    )
    state_db.upsert_runtime_episode_record(
        {
            "episodeId": "episode_engineering_1",
            "kind": "engineering",
            "state": "queued",
            "source": "runtime_broker",
            "reason": "project_change_required",
            "inputs": {"workspace": "E:/Projects/test7"},
            "requiredRuntimeAccess": ["engineering.write"],
            "metadata": {"deliverableKind": "patch"},
        },
        session_id="session_1",
        run_id="run_1",
        priority=5,
        enqueue=True,
    )
    state_db.add_runtime_episode_handoff(
        episode_id="episode_engineering_1",
        session_id="session_1",
        run_id="run_1",
        handoff={
            "handoffId": "handoff_patch_1",
            "kind": "engineering_patch_bundle",
            "status": "ready",
            "compactSummary": "Patch bundle ready",
            "rawRef": "toolobs://patch_raw_1",
            "detailTool": "tool_observation_detail",
        },
    )
    state_db.upsert_runtime_episode_record(
        {
            "episodeId": "episode_orphan_session_1",
            "kind": "delegation",
            "state": "failed",
            "reason": "legacy_missing_run_id",
            "errorMessage": "task dispatch failed",
        },
        session_id="session_1",
        run_id=None,
    )
    service.record_event(
        event_type="compat.ingress",
        run_id="run_1",
        runtime_kind="network_supervisor",
        source="test",
        summary="external request normalized",
        refs={"rawRef": "toolobs://raw_1"},
    )
    obs_db.add_tool_observation_record(
        {
            "id": "raw_1",
            "raw_ref": "toolobs://raw_1",
            "tool_name": "network_write",
            "tool_call_id": "call_1",
            "runtime_kind": "network_supervisor",
            "surface": "compat",
            "raw_chars": 42,
            "visible_chars": 10,
            "raw_sha256": "abc",
            "raw_body": "secret token=sk-test1234567890 should be redacted",
            "metadata": {"runId": "run_1"},
        }
    )

    ledger = service.get_run_ledger("run_1")

    assert ledger["runId"] == "run_1"
    event_types = {item["type"] for item in ledger["timeline"]}
    assert "run.started" in event_types
    assert "compat.ingress" in event_types
    assert "tool.observation" in event_types
    assert "runtime_episode.queued" in event_types
    assert "runtime_episode_queue.queued" in event_types
    assert "runtime_episode.handoff.engineering_patch_bundle" in event_types
    assert "external_tool.waiting_external_tool" in event_types
    assert "toolobs://raw_1" in ledger["refs"]["rawEvidenceRefs"]
    assert "episode_engineering_1" in ledger["refs"]["episodeRefs"]
    assert "episode_orphan_session_1" in ledger["refs"]["episodeRefs"]
    assert "handoff_patch_1" in ledger["refs"]["handoffRefs"]
    assert "openai:global:call_wire_1" in ledger["refs"]["externalToolRefs"]


def test_run_ledger_list_summarizes_runs(tmp_path, monkeypatch):
    import core.run_ledger as module

    state_db = DatabaseManager(tmp_path / "state.db")
    obs_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(module, "db", state_db)
    monkeypatch.setattr(module, "observability_db", obs_db)
    service = RunLedgerService()
    state_db.create_or_update_session("session_list", "Run Ledger List Test")

    state_db.create_run_record(
        run_id="run_list",
        session_id="session_list",
        conversation_id=None,
        thread_id=None,
        user_id=None,
        run_type="computer_use",
        status="completed",
        trigger_source="test",
        agent_id=None,
        workflow_id=None,
        channel_type="test",
        metadata={},
    )

    payload = service.list_ledgers(limit=5)

    assert payload["count"] == 1
    assert payload["items"][0]["runId"] == "run_list"
    assert payload["items"][0]["status"] == "completed"
