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


@pytest.fixture
def dispatched_database(tmp_path, monkeypatch):
    import core.runtime_episodes as episodes
    import core.tools.native.delegation as delegation
    from core import runtime_episode_control
    instance = DatabaseManager(tmp_path / "dispatch-review.db")
    instance.create_or_update_session("session", "review", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="running")
    for module in (episodes, delegation, runtime_episode_control):
        monkeypatch.setattr(module, "db", instance)
    return instance


def dispatch(database, tasks, call_id, *, owner=None):
    state = {"messages": [], "run_id": "run", "session_id": "session"}
    if owner:
        state.update(delegationDispatchSource="runtime_episode_runner", current_route_context={
            "activeCapabilityEpisodeId": owner["episodeId"], "capabilityEpisodes": [owner]})
    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor",
                              session_id="session", run_id="run"):
        command = supervisor_delegation_broker.func(mode="dispatch", tasks=[{
            "taskBriefId": task, "targetAgentName": "Verification Engineer", "goal": "Return the requested observation",
            "readOnly": True, "writeRequired": False, "writeSet": [], "toolPolicy": {"mode": "none"},
            "expectedOutputs": ["observation"], "acceptanceContract": ["observation supplied"],
        } for task in tasks], tool_call_id=call_id, state=state)
    payload = json.loads(command.update["messages"][0].content)
    assert payload.get("ok") is not False, payload
    rows = [database.get_runtime_episode(item["delegationId"]) for item in payload["items"]]
    assert len(rows) == len(tasks) and all(rows)
    return rows


def deliver_dispatched(database, episode, task, *, status="ok"):
    ref = "result-" + episode["episodeId"]
    assert database.commit_runtime_episode_delivery(episode["episodeId"], state="completed", session_id="session", run_id="run",
        expected_state=database.get_runtime_episode(episode["episodeId"])["state"],
        handoff={"handoffId": ref, "kind": "delegation", "status": "ready", "results": [
            {"taskBriefId": task, "delegationId": episode["episodeId"], "status": status, "resultText": "Observation"}]})
    return ref


def tool_review(episode, ref, task, decision):
    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor",
                              session_id="session", run_id="run"):
        command = supervisor_delegation_broker.func(mode="review_result", delegation_id=episode["episodeId"],
            handoff_id=ref, task_brief_id=task, decision=decision, followup="Checked the evidence", tool_call_id=ref + decision)
    result = json.loads(command.update["messages"][0].content)
    assert result["ok"], result
    return result


def completion(database):
    from runtimes.chat.supervisor_completion_gate import evaluate_supervisor_completion
    episodes = database.list_runtime_episodes(run_id="run")
    return evaluate_supervisor_completion(episodes=episodes, handoffs_by_episode={
        row["episodeId"]: database.list_runtime_episode_handoffs(row["episodeId"]) for row in episodes},
        final_text="The requested observations are reviewed.")


def test_real_dispatch_retry_accepts_only_new_B_and_preserves_A(dispatched_database):
    database = dispatched_database
    a, b = dispatch(database, ["A", "B"], "dispatch-AB")
    a_ref = deliver_dispatched(database, a, "A")
    b_ref = deliver_dispatched(database, b, "B", status="blocked")
    a_vote = tool_review(a, a_ref, "A", "accept")
    tool_review(b, b_ref, "B", "retry")
    assert completion(database).action != "complete"
    b2, = dispatch(database, ["B"], "retry-B")
    assert b2["metadata"].get("retryOfResults"), b2
    b2_ref = deliver_dispatched(database, b2, "B")
    assert completion(database).action != "complete"
    tool_review(b2, b2_ref, "B", "accept")
    restored = DatabaseManager(database.db_path)
    from core.delegation_result_contract import resolved_delegation_retries
    rows = restored.list_runtime_episodes(run_id="run")
    handoffs = {row["episodeId"]: restored.list_runtime_episode_handoffs(row["episodeId"]) for row in rows}
    assert b["episodeId"] in resolved_delegation_retries(rows, handoffs), [(row["episodeId"], row["metadata"]) for row in rows]
    assert completion(restored).action == "complete", completion(restored)
    assert len(restored.list_runtime_episodes(run_id="run")) == 3
    assert len(restored.list_runtime_episode_queue()) == 3
    assert restored.get_runtime_episode(a["episodeId"])["resultRef"] == a_ref
    assert tool_review(a, a_ref, "A", "accept")["messageId"] == a_vote["messageId"]
    assert delegation_result_acceptance(restored.get_runtime_episode(b["episodeId"]),
        restored.list_runtime_episode_handoffs(b["episodeId"])[0], "B")["status"] == "retry"


def test_real_dispatch_supervisor_worker_under_capability_can_be_reviewed(dispatched_database):
    database = dispatched_database
    owner = database.upsert_runtime_episode_record(build_runtime_episode(
        need={"episodeId": "root-capability", "kind": "engineering"}, kind="engineering", state="active"),
        session_id="session", run_id="run")
    child, = dispatch(database, ["A"], "dispatch-owned", owner=owner)
    assert child["parentEpisodeId"] == owner["episodeId"]
    ref = deliver_dispatched(database, child, "A")
    assert database.get_runtime_episode(child["episodeId"])["state"] == "completed", database.get_runtime_episode(child["episodeId"])
    assert tool_review(child, ref, "A", "accept")["receipt"]["status"] == "accepted"


def test_retry_does_not_retire_another_required_task_or_unreviewed_replacement(dispatched_database):
    database = dispatched_database
    a, b = dispatch(database, ["A", "B"], "original")
    a_ref = deliver_dispatched(database, a, "A")
    b_ref = deliver_dispatched(database, b, "B")
    tool_review(b, b_ref, "B", "retry")
    b2, = dispatch(database, ["B"], "second")
    b2_ref = deliver_dispatched(database, b2, "B", status="blocked")
    with pytest.raises(AssertionError, match="execution_evidence_missing"):
        tool_review(b2, b2_ref, "B", "accept")
    tool_review(b2, b2_ref, "B", "retry")
    b3, = dispatch(database, ["B"], "third")
    b3_ref = deliver_dispatched(database, b3, "B")
    assert completion(database).action != "complete"
    tool_review(b3, b3_ref, "B", "accept")
    assert completion(database).action != "complete"  # A still requires its own review.
    tool_review(a, a_ref, "A", "accept")
    assert completion(database).action == "complete"
    # A subsequent result version revokes reuse of the prior vote for B3.
    database.commit_runtime_episode_delivery(b3["episodeId"], state="completed", session_id="session", run_id="run",
        handoff={"handoffId": "changed-third-result", "kind": "delegation", "status": "ready", "results": [
            {"taskBriefId": "B", "status": "ok", "resultText": "Changed observation"}]})
    assert completion(database).action != "complete"


def test_same_task_name_without_prior_retry_does_not_replace_required_attempt(dispatched_database):
    database = dispatched_database
    old, = dispatch(database, ["B"], "old")
    deliver_dispatched(database, old, "B", status="blocked")
    new, = dispatch(database, ["B"], "unrelated")
    assert not new["metadata"]["retryOfResults"]
    new_ref = deliver_dispatched(database, new, "B")
    tool_review(new, new_ref, "B", "accept")
    assert completion(database).action != "complete"


def test_stale_graph_handoff_does_not_demand_review_of_a_resolved_retry(dispatched_database, monkeypatch):
    import core.database as database_module
    from graph.supervisor_turn import _state_has_pending_delegation_acceptance
    from langchain_core.messages import HumanMessage
    database = dispatched_database
    monkeypatch.setattr(database_module, "db", database)
    old, = dispatch(database, ["B"], "old")
    old_ref = deliver_dispatched(database, old, "B")
    tool_review(old, old_ref, "B", "retry")
    stale = {"messages": [HumanMessage(content="Review B", additional_kwargs={
        "v8_governance_type": "delegation_handoff", "v8_delegation_handoffs": [{
            "delegationId": old["episodeId"], "taskBriefId": "B", "supervisorAcceptance": {"status": "pending"}}]})]}
    assert _state_has_pending_delegation_acceptance(stale)
    new, = dispatch(database, ["B"], "repair")
    new_ref = deliver_dispatched(database, new, "B")
    tool_review(new, new_ref, "B", "accept")
    assert not _state_has_pending_delegation_acceptance(stale)


def test_parented_recursive_worker_remains_outside_supervisor_review(dispatched_database):
    database = dispatched_database
    parent, = dispatch(database, ["parent"], "parent")
    child = database.upsert_runtime_episode_record(build_runtime_episode(
        need={"episodeId": "grandchild", "kind": "delegation"}, kind="delegation", state="queued",
        parent_episode_id=parent["episodeId"]), session_id="session", run_id="run")
    ref = deliver_dispatched(database, child, "grandchild")
    with pytest.raises(AssertionError, match="scope_mismatch"):
        tool_review(child, ref, "grandchild", "accept")


@pytest.mark.parametrize("review_A", [False, True])
def test_retry_of_one_result_in_batch_preserves_other_required_review(dispatched_database, review_A):
    database = dispatched_database
    batch = database.upsert_runtime_episode_record(build_runtime_episode(need={"episodeId": "batch", "kind": "delegation",
        "inputs": {"workerBriefs": [{"taskBriefId": "A"}, {"taskBriefId": "B"}]}}, kind="delegation", state="queued"),
        session_id="session", run_id="run")
    database.commit_runtime_episode_delivery("batch", state="completed", session_id="session", run_id="run",
        handoff={"handoffId": "batch-result", "kind": "delegation", "status": "ready", "results": [
            {"taskBriefId": "A", "status": "ok"}, {"taskBriefId": "B", "status": "blocked"}]})
    if review_A:
        tool_review(batch, "batch-result", "A", "accept")
    tool_review(batch, "batch-result", "B", "retry")
    new, = dispatch(database, ["B"], "repair-only-B")
    ref = deliver_dispatched(database, new, "B")
    tool_review(new, ref, "B", "accept")
    assert (completion(database).action == "complete") is review_A


def test_lifecycle_upsert_cannot_erase_durable_review_or_retry_provenance(dispatched_database):
    database = dispatched_database
    old, = dispatch(database, ["B"], "original")
    ref = deliver_dispatched(database, old, "B")
    tool_review(old, ref, "B", "retry")
    new, = dispatch(database, ["B"], "repair")
    new_ref = deliver_dispatched(database, new, "B")
    tool_review(new, new_ref, "B", "accept")
    stale = {**database.get_runtime_episode(new["episodeId"]), "metadata": {"progress": "late callback"}}
    database.upsert_runtime_episode_record(stale, session_id="session", run_id="run")
    assert completion(database).action == "complete"
    assert database.get_runtime_episode(new["episodeId"])["metadata"]["retryOfResults"]
