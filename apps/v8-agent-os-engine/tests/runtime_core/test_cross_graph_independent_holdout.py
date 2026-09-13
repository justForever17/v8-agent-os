"""Independent integration counterexamples for the cross-graph release."""
import pytest
from langchain_core.messages import HumanMessage

from core.database import DatabaseManager, RuntimeEpisodeHandoffConflict
from core.runtime_episodes import build_runtime_episode
from core import runtime_episode_control as control


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "holdout.sqlite")
    database.create_or_update_session("holdout-session", "holdout", user_id="synthetic-owner")
    database.create_run_record(run_id="holdout-run", session_id="holdout-session", run_type="chat", status="running")
    database.upsert_runtime_episode_record(build_runtime_episode(
        need={"episodeId": "holdout-A", "kind": "research", "inputs": {}}, kind="research", state="queued"),
        session_id="holdout-session", run_id="holdout-run", enqueue=True)
    monkeypatch.setattr(control, "db", database)
    monkeypatch.setattr(control, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    return database


def request(**extra):
    return {"episode_id": "holdout-A", "session_id": "holdout-session", "run_id": "holdout-run", **extra}


def test_partial_version_replay_cannot_replace_published_evidence(ledger):
    claimed = ledger.claim_runtime_episode(worker_id="holdout-worker", lease_seconds=60)
    evidence = {"outputKey": "partition", "version": "v1", "sourceVersion": "immutable-source-1",
        "usableFor": ["consumer-B"], "compactSummary": "First stable partition", "proofRefs": ["fixture://source-1"]}
    options = {"worker_id": "holdout-worker", "lease_generation": claimed["leaseGeneration"]}
    first = control.publish_partial("holdout-A", handoff=evidence, **options)
    replay = control.publish_partial("holdout-A", handoff=dict(evidence), **options)
    assert replay["handoffRefId"] == first["handoffRefId"]
    with pytest.raises((RuntimeEpisodeHandoffConflict, ValueError)):
        control.publish_partial("holdout-A", handoff={**evidence, "sourceVersion": "different-source", "proofRefs": ["fixture://different"]}, **options)
    rows = ledger.list_runtime_episode_handoffs("holdout-A")
    stored = next(row for row in rows if row["payload"]["handoffRefId"] == first["handoffRefId"])
    assert stored["payload"]["sourceVersion"] == "immutable-source-1"
    assert stored["payload"]["proofRefs"] == ["fixture://source-1"]


def test_completed_cancel_replay_keeps_receipt_without_weakening_scope(ledger):
    first = control.request_control(**request(kind="cancel", request_id="cancel-once"))
    ledger.cancel_runtime_episode("holdout-A", reason="synthetic executor already stopped")
    control.acknowledge_stopped("holdout-A", run_id="holdout-run")
    replay = control.request_control(**request(kind="cancel", request_id="cancel-once"))
    assert replay["messageId"] == first["messageId"]
    assert replay["deliveryState"] == "stopped"
    with pytest.raises(ValueError, match="scope"):
        control.request_control(**{**request(kind="cancel", request_id="cancel-once"), "session_id": "another-owner-session"})
    with pytest.raises(ValueError, match="terminal"):
        control.request_control(**request(kind="steer", request_id="new-steer", followup="write after completion"))


def test_new_guidance_remains_reachable_after_more_than_one_history_page(ledger):
    state = {"messages": [HumanMessage(content="initial instruction")]}
    for index in range(140):
        receipt = control.request_control(**request(kind="steer", request_id=f"old-{index}", followup=f"old instruction {index}"))
        ledger.acknowledge_runtime_episode_message(receipt["messageId"], recipient="holdout-A", state="applied", result={"fixture": True})
    # The worker can reconstruct old applied guidance, but must also see the
    # newest pending edit rather than permanently rereading its first page.
    newest = control.request_control(**request(kind="steer", request_id="latest", followup="CURRENT-CHOICE"))
    for _ in range(4):
        control.apply_worker_controls(state, episode_id="holdout-A", run_id="holdout-run")
    assert any(message.id == newest["messageId"] and "CURRENT-CHOICE" in str(message.content) for message in state["messages"])
