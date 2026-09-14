from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.database import DatabaseManager
from core.delegation_result_contract import delegation_result_acceptance
from core.runtime_episodes import build_runtime_episode
from core.tools.native.delegation_surface import supervisor_delegation_broker
from erc.runtime_context import bind_runtime_context
from runtimes.chat.supervisor_completion_gate import _delegation_acceptance_missing


@pytest.fixture
def database(tmp_path, monkeypatch):
    from core import runtime_episode_control
    from core.tools.native import delegation
    instance = DatabaseManager(tmp_path / "review.db")
    instance.create_or_update_session("session", "review", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="running")
    monkeypatch.setattr(delegation, "db", instance)
    monkeypatch.setattr(runtime_episode_control, "db", instance)
    instance.upsert_runtime_episode_record(build_runtime_episode(
        need={"episodeId": "episode", "kind": "delegation"}, kind="delegation", state="queued"),
        session_id="session", run_id="run", enqueue=False)
    deliver(instance, "v1")
    return instance


def deliver(database, version, *, status="ok"):
    return database.commit_runtime_episode_delivery("episode", state="completed", session_id="session", run_id="run",
        handoff={"handoffId": version, "kind": "delegation", "status": "ready", "results": [
            {"taskBriefId": "A", "delegationId": "episode", "status": "ok", "resultText": "A result"},
            {"taskBriefId": "B", "delegationId": "episode", "status": status, "resultText": "B result"}]})


def review(database, task="A", decision="accept", **overrides):
    kwargs = dict(episode_id="episode", session_id="session", run_id="run", handoff_id="v1",
        task_brief_id=task, decision=decision, reason="Checked the selected result", request_id=f"review-{task}")
    return database.review_runtime_delegation_result(**{**kwargs, **overrides})


def pending(database, text="Web验收决定：ACCEPT，Phone尚未核验"):
    return _delegation_acceptance_missing([database.get_runtime_episode("episode")],
        {"episode": database.list_runtime_episode_handoffs("episode")}, final_text=text)


def test_mixed_decisions_are_durable_and_never_apply_to_another_result(database):
    assert pending(database) == ["episode"]
    accepted = review(database)
    assert pending(database) == ["episode"]
    retry = review(database, "B", "retry")
    assert accepted["receipt"]["status"] == "accepted"
    assert retry["receipt"]["status"] == "retry"
    restored = DatabaseManager(database.db_path)
    head = restored.get_runtime_episode("episode")["metadata"]["supervisorAcceptance"]
    assert head["results"]["A"]["status"] == "accepted" and head["results"]["B"]["status"] == "retry"
    assert pending(restored, "验收决定：ACCEPT") == ["episode"]
    assert restored.get_runtime_episode("episode")["state"] == "completed"
    assert restored.list_runtime_episode_messages(run_id="run", recipient="acceptance:episode", pending_only=True) == []


def test_accept_and_ignore_clear_only_the_reviewed_current_version(database):
    review(database)
    review(database, "B", "ignore")
    assert pending(database) == []
    deliver(database, "v2")
    assert pending(database) == ["episode"]
    with pytest.raises(ValueError, match="version_superseded"):
        review(database)


@pytest.mark.parametrize("overrides,error", [({"session_id": "foreign"}, "scope_mismatch"),
    ({"run_id": "foreign"}, "scope_mismatch"), ({"task_brief_id": "missing"}, "task_not_unique"),
    ({"reason": ""}, "requires_version")])
def test_wrong_identity_and_empty_basis_do_not_record_acceptance(database, overrides, error):
    with pytest.raises(ValueError, match=error):
        review(database, **overrides)
    assert "supervisorAcceptance" not in database.get_runtime_episode("episode")["metadata"]


def test_concurrent_duplicate_is_one_receipt_and_conflicting_vote_fails(database):
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(lambda _: review(database), range(3)))
    assert len({item["messageId"] for item in receipts}) == 1
    with pytest.raises(ValueError, match="already_reviewed"):
        review(database, decision="retry", request_id="different-request")
    assert len(database.list_runtime_episode_messages(run_id="run", recipient="acceptance:episode", pending_only=False)) == 1


def test_failed_execution_cannot_be_accepted_even_with_success_prose(database):
    deliver(database, "v2", status="blocked")
    with pytest.raises(ValueError, match="execution_evidence_missing"):
        review(database, "B", handoff_id="v2")
    assert pending(database) == ["episode"]


def test_native_review_is_supervisor_only_and_exposes_exact_receipt(database):
    with bind_runtime_context(actor_role="direct_subagent", agent_id="child", subagent_id="child",
                              delegation_id="child", delegation_depth=1, session_id="session", run_id="run"):
        rejected = supervisor_delegation_broker.func(mode="review_result", delegation_id="episode", handoff_id="v1",
            task_brief_id="A", decision="accept", followup="Evidence", tool_call_id="child-vote")
    assert json.loads(rejected.update["messages"][0].content)["error"] == "delegation_mode_not_available_to_subagent"
    with bind_runtime_context(actor_role="supervisor", session_id="session", run_id="run"):
        accepted = supervisor_delegation_broker.func(mode="review_result", delegation_id="episode", handoff_id="v1",
            task_brief_id="A", decision="accept", followup="Evidence", tool_call_id="parent-vote")
    payload = json.loads(accepted.update["messages"][0].content)
    assert payload["receipt"]["taskBriefId"] == "A" and payload["receipt"]["handoffRefId"] == "v1"
    from core.runtime_episode_control import inspect_episode
    inspection = inspect_episode("episode", session_id="session", run_id="run")
    assert inspection["handoffs"][0]["results"][0]["supervisorAcceptance"]["status"] == "accepted"
    assert inspection["handoffs"][0]["results"][1]["supervisorAcceptance"]["status"] == "pending"


def test_mutated_handoff_digest_cannot_reuse_review(database):
    review(database)
    ep = database.get_runtime_episode("episode")
    row = database.list_runtime_episode_handoffs("episode")[0]
    payload = {**row["payload"], "payloadDigest": "another"}
    assert delegation_result_acceptance(ep, payload, "A")["status"] == "pending"
