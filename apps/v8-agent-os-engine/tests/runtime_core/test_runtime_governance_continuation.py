import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, Send

from core.database import DatabaseManager
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.runtime_episodes import build_runtime_episode
from erc.checkpoint_store import CheckpointStore
from graph.tool_routing import create_routed_tool_node


@pytest.fixture
def database(tmp_path, monkeypatch):
    import core.database as database_module
    import core.runtime_episode_runner as runner
    import core.runtime_episode_control as control
    import core.runtime_episodes as episodes
    import erc.side_effect_idempotency as idempotency
    import erc.kernel as kernel
    import erc.run_service as runs
    import erc.event_bus as events
    import erc.workflow_ledger as workflows
    instance = DatabaseManager(tmp_path / "governance.db")
    instance.create_or_update_session("session", "governance", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="running")
    for module in (database_module, runner, control, episodes, idempotency, kernel, runs, events, workflows):
        monkeypatch.setattr(module, "db", instance)
    return instance


def test_runtime_keeps_governance_wait_and_dependent_pending(database, tmp_path, monkeypatch):
    import core.runtime_episode_runner as runner_module
    import graph.parallel_support as parallel
    import erc.kernel as kernel_module
    from erc.command_service import command_service
    from erc.models import ApprovalRequest
    root = database.upsert_runtime_episode_record(build_runtime_episode(
        need={"episodeId": "root", "kind": "delegation"}, kind="delegation", state="queued"),
        session_id="session", run_id="run")
    sends = []
    for task, deps in [("B", []), ("C", ["B"])]:
        database.upsert_runtime_episode_record(build_runtime_episode(need={"episodeId": task, "kind": "delegation"},
            kind="delegation", state="queued", parent_episode_id="root"), session_id="session", run_id="run")
        sends.append(Send("parallel_delegate_task", {"run_id": "run", "session_id": "session", "messages": [],
            "parallel_branch": {"agentId": "worker", "delegationId": task, "taskBriefId": task,
                "taskBrief": {"taskBriefId": task, "dependency": deps}, "dependency": deps}}))
    runner = runner_module.RuntimeEpisodeRunner()
    monkeypatch.setattr(runner, "_build_agent_nodes_map", lambda **kwargs: {"worker": {"node_func": lambda state: None}})
    monkeypatch.setattr(runner, "_emit", lambda *args, **kwargs: None)
    async def heartbeat(_episode, operation, **kwargs):
        return await operation
    monkeypatch.setattr(runner, "_await_with_heartbeat", heartbeat)
    async def interrupt(state, _agent, **kwargs):
        assert state["parallel_branch"]["taskBriefId"] == "B"
        error = ModelGovernanceInterventionRequired("review", approval_kind="safety_review", question="Review operation")
        error.runtime_continuation = {"episodeId": "B", "taskBriefId": "B", "node": "worker_tools",
            "checkpoint": {"configurable": {"thread_id": "runtime:run", "checkpoint_ns": "delegation:B", "checkpoint_id": "one"}}}
        raise error
    monkeypatch.setattr(parallel, "_run_parallel_agent_branch", interrupt)
    def request_approval(*, approval_kind, request):
        return command_service.request_approval(ApprovalRequest(approval_id="review-B", run_id="run", session_id="session",
            approval_kind=approval_kind, request=request))
    monkeypatch.setattr(kernel_module.erc_kernel, "attach_run", lambda *args, **kwargs: SimpleNamespace(request_approval=request_approval))
    monkeypatch.setattr(parallel, "_fail_managed_branch_workspace", lambda *args: pytest.fail("Governance must not fail the worktree"))
    results, _children = asyncio.run(runner._execute_local_delegation_sends(Command(goto=sends), root))
    assert [row["status"] for row in results] == ["waiting_approval", "waiting_dependency"]
    assert database.get_runtime_episode("B")["state"] == "waiting_approval"
    assert database.get_runtime_episode("C")["state"] in {"waiting", "waiting_child", "waiting_dependency"}
    assert database.get_pending_approval("review-B")["status"] == "pending"
    assert results[0]["governanceWait"]["approvalId"] == "review-B"


def test_encrypted_branch_resume_executes_pending_operation_without_repeating_completed_sibling(database, tmp_path, monkeypatch):
    import erc.checkpoint_store as store_module
    store = CheckpointStore(tmp_path / "checkpoints.db")
    monkeypatch.setattr(store_module, "checkpoint_store", store)
    model_calls = []
    effects = tmp_path / "effects.txt"

    @tool
    def save_first() -> Command:
        """Record the first authorized effect."""
        with effects.open("a") as stream:
            stream.write("first\n")
        return Command(goto="worker", update={"messages": [ToolMessage(content="First effect recorded.", name="save_first", tool_call_id="first")],
                                               "firstEffectObserved": True})

    @tool
    def save_reviewed() -> str:
        """Record the effect that requires its exact approval."""
        operations = (database.get_run_record("run").get("metadata") or {}).get("approvedSafetyOperations") or []
        if not any(item.get("fingerprint") == "op-second" for item in operations):
            raise ModelGovernanceInterventionRequired("review", approval_kind="safety_review", question="Review second effect",
                request_payload={"toolCallId": "second", "operationFingerprint": "op-second"})
        with effects.open("a") as stream:
            stream.write("second\n")
        return "Second effect recorded."

    def model(state):
        model_calls.append(len(state["messages"]))
        if len(model_calls) == 1:
            return Command(goto="worker_tools", update={"messages": [AIMessage(content="", tool_calls=[
                {"id": "first", "name": "save_first", "args": {}}, {"id": "second", "name": "save_reviewed", "args": {}}])]})
        assert state["firstEffectObserved"] is True
        return Command(goto="supervisor", update={"messages": [AIMessage(content="Both effects are recorded.")]})

    episode_id = "subagent::test::0::B::worker"
    database.upsert_runtime_episode_record(build_runtime_episode(need={"episodeId": episode_id, "kind": "delegation",
        "source": "delegation_broker", "inputs": {"workerBriefs": [{"taskBriefId": "B", "goal": "Save both effects PRIVATE_BRANCH_CONTENT",
            "allowChildDelegation": False, "childDelegationPolicyExplicit": True}], "workspacePath": str(tmp_path)}},
        kind="delegation", state="queued", extra={"targetId": "worker"}), session_id="session", run_id="run", enqueue=True)
    agent = {"node_func": model, "tool_node_func": create_routed_tool_node([save_first, save_reviewed], "worker_tools", "worker")}

    async def scenario():
        import core.runtime_episode_runner as runner_module
        runner = runner_module.RuntimeEpisodeRunner()
        monkeypatch.setattr(runner, "_build_agent_nodes_map", lambda **kwargs: {"worker": agent})
        monkeypatch.setattr(runner, "_emit", lambda *args, **kwargs: None)
        await runner._execute_episode(database.claim_runtime_episode(worker_id=runner.worker_id))
        assert database.get_runtime_episode(episode_id)["state"] == "waiting_approval", database.get_runtime_episode(episode_id)
        approval, = database.list_pending_approvals(run_id="run", status="pending")
        continuation = approval["request"]["runtimeContinuation"]
        assert effects.read_text().splitlines() == ["first"]
        assert len(model_calls) == 1 and continuation["node"] == "worker_tools"
        await store.close()
        assert b"PRIVATE_BRANCH_CONTENT" not in (tmp_path / "checkpoints.db").read_bytes()
        restored_store = CheckpointStore(tmp_path / "checkpoints.db")
        monkeypatch.setattr(store_module, "checkpoint_store", restored_store)
        from erc.kernel import erc_kernel
        assert erc_kernel.approve(approval["id"], response={"decision": "approved"})
        # Simulate process loss after the durable decision, before router scheduling.
        runner._recover_approved_runtime_continuations()
        runner._recover_approved_runtime_continuations()
        assert len(database.list_runtime_episode_queue()) == 1
        claimed = database.claim_runtime_episode(worker_id=runner.worker_id)
        assert claimed["episodeId"] == episode_id
        try:
            await runner._execute_episode(claimed)
            assert database.get_runtime_episode(episode_id)["state"] == "completed", database.get_runtime_episode(episode_id)
            assert effects.read_text().splitlines() == ["first", "second"]
            assert len(model_calls) == 2
        finally:
            await restored_store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("crash_after_effect", [False, True])
def test_approved_operation_receipt_prevents_replay_after_completed_or_unknown_outcome(database, tmp_path, monkeypatch, crash_after_effect):
    import erc.checkpoint_store as store_module
    from erc.side_effect_idempotency import side_effect_idempotency_service as service, SideEffectReconciliationRequired
    store = CheckpointStore(tmp_path / "effect-checkpoints.db")
    monkeypatch.setattr(store_module, "checkpoint_store", store)
    context = {"session_id": "session", "run_id": "run", "delegation_id": "branch"}
    call = {"id": "approved-call", "name": "save", "args": {"path": "result.txt"}}
    target = tmp_path / "result.txt"
    async def execute():
        with target.open("a") as stream:
            stream.write("effect\n")
        return ToolMessage(content="Written", name="save", tool_call_id="approved-call")

    async def scenario():
        try:
            if crash_after_effect:
                original = service.complete
                def crash(**kwargs):
                    raise RuntimeError("simulated process loss after effect, before receipt")
                monkeypatch.setattr(service, "complete", crash)
                with pytest.raises(RuntimeError, match="simulated process loss"):
                    await service.execute_approved_tool_continuation(context=context, tool_call=call, execute=execute)
                monkeypatch.setattr(service, "complete", original)
                with database.get_connection() as conn:
                    conn.execute("UPDATE runtime_side_effect_receipts SET lease_expires_at='2000-01-01T00:00:00Z'")
                    conn.commit()
                with pytest.raises(SideEffectReconciliationRequired) as unknown:
                    await service.execute_approved_tool_continuation(context=context, tool_call=call, execute=execute)
                assert unknown.value.receipt["state"] == "indeterminate"
            else:
                first = await service.execute_approved_tool_continuation(context=context, tool_call=call, execute=execute)
                await store.close()
                restarted = CheckpointStore(tmp_path / "effect-checkpoints.db")
                monkeypatch.setattr(store_module, "checkpoint_store", restarted)
                try:
                    duplicate = await service.execute_approved_tool_continuation(context=context, tool_call=call, execute=execute)
                    assert duplicate == first
                    with pytest.raises(ValueError, match="different payload"):
                        await service.execute_approved_tool_continuation(context=context, tool_call={**call, "args": {"path": "other.txt"}}, execute=execute)
                finally:
                    await restarted.close()
            assert target.read_text().splitlines() == ["effect"]
        finally:
            await store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["cancel", "interrupt", "pause"])
def test_approved_continuation_cannot_bypass_new_run_control(database, control):
    continuation = {"episodeId": "B", "checkpoint": {"configurable": {"checkpoint_id": "one"}}}
    database.upsert_runtime_episode_record(build_runtime_episode(need={"episodeId": "B", "kind": "delegation"},
        kind="delegation", state="waiting_approval", extra={"metadata": {"governanceWait": {**continuation, "approvalId": "approval"}}}),
        session_id="session", run_id="run")
    database.add_pending_approval(approval_id="approval", session_id="session", run_id="run", approval_kind="safety_review",
        status="approved", request={"runtimeContinuation": continuation})
    database.update_run_record("run", status="running", metadata={"control_signal": {"command": control}})
    result = database.resume_runtime_episode_after_approval("approval")
    assert not result["resume_scheduled"]
    assert database.get_runtime_episode("B")["state"] == "waiting_approval"
    assert database.list_runtime_episode_queue() == []
