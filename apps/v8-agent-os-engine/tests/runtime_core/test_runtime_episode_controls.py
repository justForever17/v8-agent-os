from __future__ import annotations

import asyncio
import threading
import sys
import time
import sqlite3

import pytest
from langchain_core.messages import AIMessage
from typing_extensions import Annotated, TypedDict
from langgraph.graph.message import add_messages

from core.database import DatabaseManager
from core.runtime_episodes import build_runtime_episode
from core import runtime_episode_control as control
from core import runtime_episode_runner as runner_module


@pytest.fixture
def database(tmp_path, monkeypatch):
    instance = DatabaseManager(tmp_path / "controls.db")
    instance.create_or_update_session("session", "controls", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="running")
    monkeypatch.setattr(control, "db", instance)
    monkeypatch.setattr(runner_module, "db", instance)
    monkeypatch.setattr(control, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    return instance


def enqueue(database, name="A"):
    return database.upsert_runtime_episode_record(
        build_runtime_episode(need={"episodeId": name, "kind": "research", "inputs": {}}, kind="research", state="queued"),
        session_id="session", run_id="run", enqueue=True,
    )


def test_controls_survive_restart_and_replay_has_one_identity(database):
    enqueue(database)
    kwargs = dict(episode_id="A", session_id="session", run_id="run", kind="steer", request_id="edit-1", followup="Use version B")
    first = control.request_control(**kwargs)
    again = control.request_control(**kwargs)
    assert first["messageId"] == again["messageId"]
    restored = DatabaseManager(database.db_path)
    assert len(restored.list_runtime_episode_messages(run_id="run", recipient="A")) == 1
    state = {"messages": [AIMessage(content="", tool_calls=[{"id": "old-call", "name": "write", "args": {}}])]}
    assert control.apply_worker_controls(state, episode_id="A", run_id="run")
    assert state["messages"][1].tool_call_id == "old-call"
    assert "not executed" in state["messages"][1].content
    assert "Use version B" in state["messages"][-1].content
    assert not control.apply_worker_controls(state, episode_id="A", run_id="run")
    assert restored.list_runtime_episode_messages(run_id="run", recipient="A", pending_only=False)[0]["deliveryState"] == "applied"
    # A restarted worker reconstructs the original guidance instead of losing
    # it after a receipt was committed but before its next model invocation.
    restarted_state = {"messages": []}
    assert control.apply_worker_controls(restarted_state, episode_id="A", run_id="run")
    with pytest.raises(ValueError, match="scope"):
        control.inspect_episode("A", session_id="another-session", run_id="run")
    with pytest.raises(ValueError, match="idempotency"):
        control.request_control(**{**kwargs, "followup": "different"})


def test_cancel_receipt_waits_for_executor_cleanup_and_stops_writes(database, tmp_path, monkeypatch):
    enqueue(database)
    runner = runner_module.RuntimeEpisodeRunner()
    episode = database.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_maybe_schedule_chat_handoff_resume", lambda *_args: None)
    output = tmp_path / "writes.txt"

    async def scenario():
        started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def execute(_episode):
            try:
                output.write_text("before cancel")
                started.set()
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()
                output.write_text("cleanup complete")

        monkeypatch.setattr(runner, "_execute_research", execute)
        task = asyncio.create_task(runner._execute_episode(episode))
        await started.wait()
        receipt = control.request_control("A", session_id="session", run_id="run", kind="cancel", request_id="cancel-1")
        await asyncio.wait_for(cleanup.wait(), 3)
        assert database.get_runtime_episode("A")["state"] == "active"
        assert database.list_runtime_episode_messages(run_id="run", recipient="A")[0]["deliveryState"] == "pending"
        release.set()
        await asyncio.wait_for(task, 3)
        assert database.get_runtime_episode("A")["state"] == "cancelled"
        messages = database.list_runtime_episode_messages(run_id="run", recipient="A", pending_only=False)
        assert messages[0]["messageId"] == receipt["messageId"]
        assert messages[0]["deliveryState"] == "stopped"
        assert messages[0]["receipt"]["writesTerminated"] is True
        assert output.read_text() == "cleanup complete"

    asyncio.run(scenario())


def test_message_sequence_and_duplicate_request_are_atomic(database):
    enqueue(database)
    barrier = threading.Barrier(4)
    results = []

    def submit():
        barrier.wait()
        results.append(control.request_control("A", session_id="session", run_id="run", kind="steer", request_id="same", followup="once"))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 4
    assert {item["deliverySeq"] for item in results} == {1}
    assert len(database.list_runtime_episode_messages(run_id="run", recipient="A")) == 1


def test_cancel_real_research_thread_waits_for_actual_writer_exit(database, tmp_path, monkeypatch):
    enqueue(database)
    runner = runner_module.RuntimeEpisodeRunner()
    claimed = database.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_maybe_schedule_chat_handoff_resume", lambda *_args: None)
    monkeypatch.setattr(runner_module, "_EPISODE_CANCEL_SETTLE_SECONDS", 0.05)
    started, release, exited = threading.Event(), threading.Event(), threading.Event()
    output = tmp_path / "real-thread.txt"

    def blocking_research(_episode):
        try:
            with output.open("a") as stream:
                started.set()
                while not release.wait(0.01):
                    stream.write("write\n")
                    stream.flush()
        finally:
            exited.set()
        return {"status": "ready"}

    monkeypatch.setattr(runner, "_execute_research_sync", blocking_research)

    async def scenario():
        task = asyncio.create_task(runner._execute_episode(claimed))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            control.request_control("A", session_id="session", run_id="run", kind="cancel", request_id="cancel-thread")
            await asyncio.sleep(0.35)
            assert not task.done() and not exited.is_set()
            assert database.get_runtime_episode("A")["state"] == "active"
            assert database.list_runtime_episode_messages(run_id="run", recipient="A")[0]["deliveryState"] == "pending"
            with pytest.raises(control.EpisodeControlCancelled):
                control.assert_episode_execution_allowed({"run_id": "run", "delegation_id": "A"})
            # The cancellation is scoped to A, so B may still perform work.
            control.assert_episode_execution_allowed({"run_id": "run", "delegation_id": "B"})
            (tmp_path / "B.txt").write_text("independent B completed")
        finally:
            release.set()
            await asyncio.wait_for(task, 5)
        assert exited.is_set()
        size = output.stat().st_size
        await asyncio.sleep(0.1)
        assert output.stat().st_size == size
        assert database.list_runtime_episode_messages(run_id="run", recipient="A", pending_only=False)[0]["deliveryState"] == "stopped"

    asyncio.run(scenario())


def test_cancel_managed_process_tree_keeps_independent_episode_running(database, tmp_path, monkeypatch):
    from core.tools.native import command
    enqueue(database)
    enqueue(database, "B")
    script = tmp_path / "writer.py"
    script.write_text("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile True:\n with p.open('a') as f: f.write('tick\\n'); f.flush()\n time.sleep(.02)\n")
    processes = {}
    monkeypatch.setattr(command, "_bg_processes", processes)
    try:
        for episode in ("A", "B"):
            output = tmp_path / f"{episode}.log"
            if sys.platform == "win32":
                cmd = f"& '{sys.executable}' '{script}' '{output}'"
                dialect = "powershell"
            else:
                import shlex
                cmd = " ".join(shlex.quote(str(value)) for value in (sys.executable, script, output))
                dialect = "bash"
            processes[episode] = command.BackgroundProcess(cmd, cwd=str(tmp_path), shell_dialect=dialect,
                session_id="session", run_id="run", runtime_context={"session_id": "session", "run_id": "run", "delegation_id": episode}, timeout_seconds=20)
        deadline = time.monotonic() + 8
        while (not (tmp_path / "A.log").exists() or not (tmp_path / "B.log").exists()) and time.monotonic() < deadline:
            time.sleep(0.03)
        assert (tmp_path / "A.log").exists() and (tmp_path / "B.log").exists()
        result = command.terminate_episode_background_commands("A")
        assert result["confirmed"] and result["stoppedCommandIds"] == ["A"]
        a_size, b_size = (tmp_path / "A.log").stat().st_size, (tmp_path / "B.log").stat().st_size
        time.sleep(0.15)
        assert (tmp_path / "A.log").stat().st_size == a_size
        assert (tmp_path / "B.log").stat().st_size > b_size
    finally:
        for process in processes.values():
            process.terminate()


def test_partial_dependency_requires_explicit_scoped_current_version_acceptance(database):
    from runtimes.chat.supervisor_completion_gate import evaluate_supervisor_completion
    for episode_id, task_id, deps in (("A", "source", []), ("B", "consumer", ["source"]), ("C", "other", ["source"])):
        database.upsert_runtime_episode_record(build_runtime_episode(need={
            "episodeId": episode_id, "kind": "research", "inputs": {"workerBriefs": [{"taskBriefId": task_id, "goal": task_id, "dependencies": deps}]},
        }, kind="research", state="queued"), session_id="session", run_id="run", enqueue=True)
    claimed = database.claim_runtime_episode(worker_id="owner", lease_seconds=30)
    assert claimed["episodeId"] == "A"
    runner = runner_module.RuntimeEpisodeRunner()
    _, before = runner._prepare_cross_episode_dependencies(database.get_runtime_episode("B"))
    assert before["state"] == "waiting_dependency"
    handoff = {"outputKey": "dataset", "version": "1", "sourceVersion": "source-hash-1", "usableFor": ["consumer"],
               "compactSummary": "First partition is usable; the rest is still running", "proofRefs": ["proof://partition-1"]}
    partial = control.publish_partial("A", handoff=handoff, worker_id="owner", lease_generation=claimed["leaseGeneration"])
    _, unpublished = runner._prepare_cross_episode_dependencies(database.get_runtime_episode("B"))
    assert unpublished["state"] == "waiting_dependency"
    receipt = control.accept_partial("A", session_id="session", run_id="run", handoff_id=partial["handoffRefId"],
                                     consumers=["consumer"], reason="Partition 1 is sufficient for B", request_id="accept-1")
    prepared, gate = runner._prepare_cross_episode_dependencies(database.get_runtime_episode("B"))
    assert gate is None
    dependency = prepared["inputs"]["dependencyResults"][0]
    assert dependency["resultPhase"] == "accepted_partial" and dependency["executionTerminal"] is False
    assert dependency["partialAcceptanceId"] == receipt["messageId"]
    _, outside_scope = runner._prepare_cross_episode_dependencies(database.get_runtime_episode("C"))
    assert outside_scope["state"] == "waiting_dependency"
    assert database.get_runtime_episode("A")["state"] == "active"
    assert evaluate_supervisor_completion(episodes=database.list_runtime_episodes(run_id="run"), final_text="All done").action == "waiting_runtime"
    control.publish_partial("A", handoff={**handoff, "version": "2", "sourceVersion": "source-hash-2"}, worker_id="owner", lease_generation=claimed["leaseGeneration"])
    _, invalidated = runner._prepare_cross_episode_dependencies(database.get_runtime_episode("B"))
    assert invalidated["state"] == "waiting_dependency"
    with pytest.raises(ValueError, match="superseded"):
        control.accept_partial("A", session_id="session", run_id="run", handoff_id=partial["handoffRefId"],
                               consumers=["consumer"], reason="old", request_id="old-accept")


def test_attention_flood_never_enters_decision_inbox_and_claim_is_single_writer(database, monkeypatch):
    enqueue(database)
    runner = runner_module.RuntimeEpisodeRunner()
    episode = database.get_runtime_episode("A")
    # Exercise the real event emitter and DB. Only its public realtime
    # transport is stubbed; persistence and decision classification run here.
    for index in range(120):
        runner._emit("runtime.episode.progress", episode=episode, session_id="session", run_id="run",
                     progress={"stage": "heartbeat", "summary": str(index)})
    assert database.list_runtime_episode_messages(run_id="run", recipient="supervisor:run") == []
    control.publish_attention(episode, kind="input_required", detail={"question": "choose source"})
    control.publish_attention(episode, kind="input_required", detail={"question": "choose source"})
    assert len(database.list_runtime_episode_messages(run_id="run", recipient="supervisor:run")) == 1
    database.update_run_record("run", status="running", metadata={"runtimeEpisodeResume": {"state": "waiting", "episodeIds": ["A"]}})
    barrier = threading.Barrier(4)
    claims = []

    def claim():
        barrier.wait()
        claims.append(database.claim_runtime_episode_resume_schedule("run", marker_key="runtimeEpisodeResume",
            next_marker={"state": "scheduled"}, terminal_states={"completed"}, active_states={"active", "queued"}))

    threads = [threading.Thread(target=claim) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(bool(item["claimed"]) for item in claims) == 1
    messages = control.parent_attention_messages({"messages": []}, run_id="run")
    assert len(messages) == 1
    # A crash before checkpointing replays the same delivery identity.
    assert control.parent_attention_messages({"messages": []}, run_id="run")[0].id == messages[0].id
    assert control.parent_attention_messages({"messages": messages}, run_id="run") == []
    assert database.list_runtime_episode_messages(run_id="run", recipient="supervisor:run", pending_only=False)[0]["deliveryState"] == "processed"


def test_running_worker_steer_changes_real_tool_effect_before_old_call_executes(database, tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.types import Command
    from graph.parallel_support import _run_parallel_agent_branch
    from graph.tool_routing import create_routed_tool_node
    enqueue(database)
    started, release = threading.Event(), threading.Event()
    output = tmp_path / "steered.txt"
    model_calls, writes = [], []

    @tool
    async def save_choice(value: str) -> str:
        """Save the selected fixture value."""
        output.write_text(value)
        writes.append(value)
        return "saved " + value

    def provider_node(state):
        model_calls.append(state)
        if len(model_calls) == 1:
            started.set()
            release.wait(5)
            return Command(goto="worker_tools", update={"messages": [AIMessage(content="", tool_calls=[{"id": "old", "name": "save_choice", "args": {"value": "old"}}])]})
        if not writes:
            assert any("choose new" in str(message.content) for message in state["messages"])
            return Command(goto="worker_tools", update={"messages": [AIMessage(content="", tool_calls=[{"id": "new", "name": "save_choice", "args": {"value": "new"}}])]})
        return Command(goto="supervisor", update={"messages": [AIMessage(content="Saved the new choice.")]})

    async def scenario():
        state = {"messages": [HumanMessage(content="choose old")], "run_id": "run", "session_id": "session",
                 "workspace_path": str(tmp_path), "parallel_branch": {"agentId": "worker", "delegationId": "A",
                 "taskBriefId": "choice", "reason": "choose a value", "allowChildDelegation": False, "initialMessageCount": 1}}
        task = asyncio.create_task(_run_parallel_agent_branch(state, {"node_func": provider_node,
            "tool_node_func": create_routed_tool_node([save_choice], "worker_tools", "worker"), "tool_mode": "native"}))
        assert await asyncio.to_thread(started.wait, 3)
        control.request_control("A", session_id="session", run_id="run", kind="steer", request_id="steer-real", followup="choose new")
        release.set()
        await asyncio.wait_for(task, 15)
        assert writes == ["new"] and output.read_text() == "new"
        assert database.list_runtime_episode_messages(run_id="run", recipient="A", pending_only=False)[0]["deliveryState"] == "applied"

    asyncio.run(scenario())


def test_real_graph_background_dispatch_completes_B_before_A_and_checkpoints_await(database, tmp_path, monkeypatch):
    import json
    import core.runtime_episodes as episodes_module
    import core.tools.native.delegation as delegation_module
    import core.tools.native.runtime as runtime_module
    import graph.workflow_assembly as workflow
    from erc.run_service import run_service
    from erc.runtime_context import bind_runtime_context
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import StateGraph, START
    from langgraph.types import Command
    from typing_extensions import TypedDict, Annotated
    from langgraph.graph.message import add_messages
    monkeypatch.setattr(episodes_module, "db", database)
    monkeypatch.setattr(delegation_module, "db", database)
    monkeypatch.setattr(runtime_module, "db", database)
    monkeypatch.setattr(workflow, "db", database)
    monkeypatch.setattr(run_service, "update_metadata", lambda run_id, value: database.update_run_record(run_id, status="running", metadata=value))
    started, release = threading.Event(), threading.Event()
    runner = runner_module.RuntimeEpisodeRunner()

    def worker_provider(_state):
        started.set()
        release.wait(20)
        return Command(goto="supervisor", update={"messages": [AIMessage(content="The requested observation is complete.")]})

    monkeypatch.setattr(runner, "_build_agent_nodes_map", lambda **_kwargs: {
        str(item.get("targetId") or item.get("target_id")): {"node_func": worker_provider, "tool_mode": "native"}
        for item in database.list_runtime_episodes(run_id="run")
    })

    class State(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        phase: int
        current_route_context: dict
        runtime_dispatch_status: dict
        run_id: str
        session_id: str
        workspace_path: str
        parallel_invocations: list

    def supervisor(state):
        if not state.get("phase"):
            with bind_runtime_context(run_id="run", session_id="session", workspace_path=str(tmp_path),
                                      runtime_kind="chat", actor_role="supervisor", agent_id="supervisor"):
                tasks = [{
                    "taskBriefId": "long-A", "targetAgentName": "Verification Engineer", "goal": "Return a concise observation",
                    "readOnly": True, "writeSet": [], "expectedOutputs": ["observation"], "acceptanceContract": ["observation supplied"],
                    "allowChildDelegation": False, "toolPolicy": {"mode": "none"},
                }]
                command = delegation_module.delegation_broker.func(mode="dispatch", tasks=tasks, tool_call_id="dispatch-A", state=state)
                # Replay the dispatch after persistence but before the parent
                # checkpoint, as a crash at this boundary would do.
                replay = delegation_module.delegation_broker.func(mode="dispatch", tasks=tasks, tool_call_id="dispatch-A", state=state)
                assert len(database.list_runtime_episodes(run_id="run")) == 1
                assert replay.goto == "supervisor"
            assert command.goto == "supervisor", json.dumps(str(command))
            assert command.update["runtime_dispatch_status"]["nextAction"] == "continue_supervisor"
            return Command(goto="supervisor", update={**command.update, "phase": 1})
        assert started.wait(5), database.list_runtime_episodes(run_id="run")
        (tmp_path / "B-complete.txt").write_text("B completed while A was running")
        assert database.list_runtime_episodes(run_id="run")[0]["state"] == "active"
        episode_id = database.list_runtime_episodes(run_id="run")[0]["episodeId"]
        return workflow._route_runtime_tool_commands(runtime_module.runtime_broker.func(
            mode="await", episode_ids=[episode_id], tool_call_id="await-A", state=state))

    async def scenario():
        await runner.start()
        checkpoint_path = str(tmp_path / "parent.sqlite")
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
            graph = StateGraph(State)
            graph.add_node("supervisor", supervisor)
            graph.add_node("runtime_episode", workflow.build_runtime_episode_wait_node())
            graph.add_edge(START, "supervisor")
            compiled = graph.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": "parent-run"}}
            try:
                await compiled.ainvoke({"phase": 0, "messages": [], "run_id": "run", "session_id": "session", "workspace_path": str(tmp_path)}, config)
            finally:
                release.set()
                for _ in range(150):
                    if all(item["state"] in {"completed", "failed", "degraded"} for item in database.list_runtime_episodes(run_id="run")):
                        break
                    await asyncio.sleep(0.02)
                await runner.stop()
            saved = await compiled.aget_state(config)
            assert saved.next == () and saved.values["runtime_dispatch_status"]["state"] == "background_wait"
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as restored:
            checkpoint = await restored.aget_tuple(config)
            assert checkpoint.checkpoint["channel_values"]["phase"] == 1
        assert (tmp_path / "B-complete.txt").read_text().startswith("B completed")
        assert len(database.list_runtime_episode_queue()) == 1
        assert database.list_runtime_episodes(run_id="run")[0]["state"] == "completed"

    asyncio.run(scenario())


def test_long_control_queue_prioritizes_cancel_and_terminal_retry_returns_same_receipt(database):
    enqueue(database)
    for index in range(140):
        database.append_runtime_episode_message(episode_id="A", session_id="session", run_id="run", recipient="A",
            kind="steer", request_id=f"edit-{index}", content={"followup": f"version {index}"})
    state = {"messages": []}
    assert control.apply_worker_controls(state, episode_id="A", run_id="run")
    assert control.apply_worker_controls(state, episode_id="A", run_id="run")
    assert "version 139" in state["messages"][-1].content
    assert len(state["messages"]) == 140
    request = dict(episode_id="A", session_id="session", run_id="run", kind="cancel", request_id="cancel-after-window")
    original = control.request_control(**request)
    assert control.cancellation_requested("A", "run")
    with pytest.raises(control.EpisodeControlCancelled):
        control.apply_worker_controls(state, episode_id="A", run_id="run")
    database.cancel_runtime_episode("A")
    control.acknowledge_stopped("A", run_id="run")
    replay = control.request_control(**request)
    assert replay["messageId"] == original["messageId"] and replay["deliveryState"] == "stopped"
    assert database.list_runtime_episode_messages(run_id="run", recipient="A") == []
    with pytest.raises(ValueError, match="terminal"):
        control.request_control(**{**request, "request_id": "new-cancel"})


def test_restart_recovers_lost_parent_wake_once_without_redispatch(database, monkeypatch):
    import erc.command_router as router_module
    import erc.run_service as run_module
    from erc.session_lane_scheduler import SessionLaneScheduler
    import erc.session_lane_scheduler as lane_module
    enqueue(database)
    episode = database.get_runtime_episode("A")
    control.publish_attention(episode, kind="input_required", detail={"required": "source"})
    database.update_run_record("run", status="running", metadata={"runtimeEpisodeResume": {"state": "scheduled", "episodeIds": ["A"], "waitGeneration": 1}})
    restored = DatabaseManager(database.db_path)
    for module in (control, runner_module, router_module, run_module):
        monkeypatch.setattr(module, "db", restored)
    monkeypatch.setattr(lane_module, "session_lane_scheduler", SessionLaneScheduler())
    router = router_module.RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda request, **kwargs: scheduled.append((request, kwargs)) or "run")
    # External context/config construction is not the scheduling mechanism.
    monkeypatch.setattr(router, "_build_runtime_handoff_resume_chat_request", lambda *_args, **_kwargs: {"resume": "A"})
    monkeypatch.setattr(router, "_emit_resume_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router_module, "runtime_command_router", router)
    restarted_runner = runner_module.RuntimeEpisodeRunner()
    restarted_runner._recover_parent_wakes(restart=True)
    for _ in range(10):
        restarted_runner._recover_parent_wakes()
    assert len(scheduled) == 1
    assert len(restored.list_runtime_episode_queue()) == 1
    assert restored.get_runtime_episode("A")["state"] == "queued"
    marker = restored.get_run_record("run")["metadata"]["runtimeEpisodeResume"]
    assert marker["state"] == "scheduled" and marker["recoveredAfterRestart"]
    # Single parent writer even for the same run ID; neither duplicate graph
    # invocation nor a competing user turn can acquire the active lane.
    lane = lane_module.session_lane_scheduler
    assert lane.try_acquire("session", "run").acquired
    assert not lane.try_acquire("session", "run").acquired
    assert not lane.try_acquire("session", "user-B").acquired
    lane.release("session", "run")
    assert lane.try_acquire("session", "user-B").acquired


def test_wait_claim_respects_selected_dependency_and_pending_graph_writer(database):
    enqueue(database, "A")
    enqueue(database, "B")
    database.complete_runtime_episode("B", state="completed")
    database.update_run_record("run", status="running", metadata={"runtimeEpisodeResume": {
        "state": "waiting", "episodeIds": ["B"], "awaitExplicit": True,
    }})
    claim = database.claim_runtime_episode_resume_schedule("run", marker_key="runtimeEpisodeResume",
        next_marker={"state": "scheduled"}, terminal_states={"completed"}, active_states={"queued", "active"})
    assert claim["claimed"]
    assert database.get_runtime_episode("A")["state"] == "queued"


def test_parallel_write_conflict_blocks_manual_dispatch_but_dependency_order_is_allowed():
    from core.delegation_broker import build_workset_dispatch_decisions
    tasks = [{"taskBriefId": "A", "goal": "Implement A", "writeSet": ["src/shared.py"], "expectedOutputs": ["src/shared.py"], "acceptanceContract": ["A verified"]},
             {"taskBriefId": "B", "goal": "Implement B", "writeSet": ["src/"], "expectedOutputs": ["src/shared.py"], "acceptanceContract": ["B verified"]}]
    assert all(item["blocked"] for item in build_workset_dispatch_decisions(tasks))
    ordered = [tasks[0], {**tasks[1], "dependency": ["A"]}]
    decisions = build_workset_dispatch_decisions(ordered)
    assert not any(item["blocked"] for item in decisions), decisions


def test_root_research_guidance_reaches_original_model_owner(database):
    from runtimes.research.agent import ResearchAgent
    from erc.runtime_context import bind_runtime_context
    from langchain_core.messages import HumanMessage
    enqueue(database)
    controls = control.request_control("A", session_id="session", run_id="run", kind="steer", request_id="research-steer", followup="Use the revised source")
    observed = []

    def invoke(messages, *_args, **_kwargs):
        observed.extend(message.content for message in messages)
        return AIMessage(content="Revised source selected")

    agent = ResearchAgent(invoke=invoke, acquire=None, progress=lambda **_kwargs: None, writer_id="fixture-writer", reviewer_id="fixture-reviewer")
    with bind_runtime_context(episode_id="A", run_id="run", session_id="session", runtime_kind="research"):
        response = agent.call([HumanMessage(content="Original source")], [])
    assert response.content == "Revised source selected"
    assert any("Use the revised source" in value for value in observed)
    assert database.list_runtime_episode_messages(run_id="run", recipient="A", pending_only=False)[0]["messageId"] == controls["messageId"]
    assert not database.list_runtime_episode_messages(run_id="run", recipient="A")


def test_attention_does_not_replace_simultaneous_user_guidance(database):
    from langchain_core.messages import HumanMessage
    from graph.supervisor_context import resolve_supervisor_request_context
    enqueue(database)
    control.publish_attention(database.get_runtime_episode("A"), kind="partial", detail={"summary": "earlier result"})
    user = HumanMessage(content="Use the revised requirement for B")
    attention = control.parent_attention_messages({"messages": [user]}, run_id="run")
    context = resolve_supervisor_request_context([user, *attention], None)
    assert context["user_query"] == user.content
    assert database.list_runtime_episode_messages(run_id="run", recipient="supervisor:run")[0]["deliveryState"] == "pending"


def test_restart_reconstructs_attention_from_committed_handoff_when_notification_was_lost(database):
    enqueue(database)
    database.complete_runtime_episode("A", state="completed")
    episode = database.get_runtime_episode("A")
    assert database.list_runtime_episode_messages(run_id="run", recipient="supervisor:run") == []
    control.reconcile_episode_attention(episode)
    first = control.parent_attention_messages({"messages": []}, run_id="run")
    assert len(first) == 1
    control.acknowledge_parent_messages({"messages": first}, run_id="run")
    control.reconcile_episode_attention(episode)
    assert control.parent_attention_messages({"messages": first}, run_id="run") == []


def test_cancel_sync_native_tool_waits_for_thread_completion(database, tmp_path, monkeypatch):
    from langchain_core.tools import tool
    from graph.tool_routing import create_routed_tool_node
    from erc.runtime_context import bind_runtime_context
    enqueue(database)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    output = tmp_path / "sync-native.txt"

    @tool
    def write_until_released() -> str:
        """Controlled synchronous fixture writer."""
        with output.open("a") as stream:
            started.set()
            while not release.wait(0.01):
                stream.write("write\n")
                stream.flush()
        finished.set()
        return "settled"

    runner = runner_module.RuntimeEpisodeRunner()
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_maybe_schedule_chat_handoff_resume", lambda *_args: None)
    claimed = database.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30)
    node = create_routed_tool_node([write_until_released], "fixture_tools", "fixture")

    async def execute(_episode):
        with bind_runtime_context(episode_id="A", session_id="session", run_id="run"):
            await node({"messages": [AIMessage(content="", tool_calls=[{"id": "sync", "name": "write_until_released", "args": {}}])]})
        return {"status": "ready"}

    monkeypatch.setattr(runner, "_execute_research", execute)

    async def scenario():
        task = asyncio.create_task(runner._execute_episode(claimed))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            control.request_control("A", session_id="session", run_id="run", kind="cancel", request_id="cancel-sync")
            await asyncio.sleep(0.35)
            assert not task.done() and not finished.is_set()
            assert database.get_runtime_episode("A")["state"] == "active"
        finally:
            release.set()
            await asyncio.wait_for(task, 10)
        assert finished.is_set()
        size = output.stat().st_size
        await asyncio.sleep(0.1)
        assert size == output.stat().st_size
        assert database.list_runtime_episode_messages(run_id="run", recipient="A", pending_only=False)[0]["deliveryState"] == "stopped"

    asyncio.run(scenario())


def test_partial_projection_keeps_live_owner_and_all_versions():
    from core.runtime_episodes import append_handoff_ref
    state = {"capabilityEpisodes": [{"episodeId": "A", "state": "active", "resultRef": None}]}
    for version in ("v1", "v2"):
        state = append_handoff_ref(state, {"producerEpisodeId": "A", "handoffRefId": version, "status": "partial"})
    assert state["capabilityEpisodes"][0]["state"] == "active"
    assert state["capabilityEpisodes"][0]["resultRef"] is None
    assert [item["handoffRefId"] for item in state["handoffRefs"]] == ["v1", "v2"]


def test_cancel_parked_parent_and_queued_child_never_dispatches_either(database, monkeypatch):
    parent = enqueue(database, "parent")
    database.complete_runtime_episode("parent", state="waiting_child")
    database.upsert_runtime_episode_record(build_runtime_episode(need={"episodeId": "child", "kind": "research", "parentEpisodeId": "parent"},
        kind="research", state="queued", parent_episode_id="parent"), session_id="session", run_id="run", enqueue=True)
    enqueue(database, "independent-B")
    control.request_control("parent", session_id="session", run_id="run", kind="cancel", request_id="cancel-tree")
    assert control.cancellation_requested("child", "run")
    assert not control.cancellation_requested("independent-B", "run")
    runner = runner_module.RuntimeEpisodeRunner()
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    async def scenario():
        await runner._settle_parked_episode_cancellations()
        await runner._settle_parked_episode_cancellations()
    asyncio.run(scenario())
    assert database.get_runtime_episode("parent")["state"] == "cancelled"
    assert database.get_runtime_episode("child")["state"] == "cancelled"
    assert database.claim_runtime_episode(worker_id="worker")["episodeId"] == "independent-B"
    assert database.list_runtime_episode_messages(run_id="run", recipient="parent", pending_only=False)[0]["deliveryState"] == "stopped"


def test_partial_version_flood_is_one_parent_decision_with_latest_output_and_preserved_critical_events(database, monkeypatch):
    import erc.command_router as router_module
    import erc.run_service as run_module
    import erc.session_lane_scheduler as lane_module
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    enqueue(database)
    episode = database.get_runtime_episode("A")
    database.update_run_record("run", status="running", metadata={"runtimeEpisodeResume": {"state": "waiting", "episodeIds": ["A"]}})
    monkeypatch.setattr(router_module, "db", database)
    monkeypatch.setattr(run_module, "db", database)
    lane = lane_module.SessionLaneScheduler()
    monkeypatch.setattr(lane_module, "session_lane_scheduler", lane)
    router = router_module.RuntimeCommandRouter()
    wakeups = []
    router.configure(schedule_chat_run=lambda request, **_kwargs: wakeups.append(request) or "run")
    monkeypatch.setattr(router, "_build_runtime_handoff_resume_chat_request", lambda *_args, **_kwargs: {"run": "run"})
    monkeypatch.setattr(router, "_emit_resume_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router_module, "runtime_command_router", router)
    runner = runner_module.RuntimeEpisodeRunner()
    assert lane.try_acquire("session", "B").acquired
    for version in range(1, 21):
        control.publish_attention(episode, kind="partial", detail={"outputKey": "report", "version": str(version), "handoffRefId": f"p{version}"})
        runner._recover_parent_wakes()
    assert wakeups == []
    control.publish_attention(episode, kind="input_required", detail={"question": "Which source?"})
    control.publish_attention(episode, kind="partial_invalidated", detail={"handoffRefId": "accepted-p0", "consumers": ["downstream"]})
    lane.release("session", "B")
    for _ in range(20): runner._recover_parent_wakes()
    assert len(wakeups) == 1
    model_inputs = []
    class ParentState(TypedDict):
        messages: Annotated[list, add_messages]
    def model_step(state):
        pending = control.parent_attention_messages(state, run_id="run")
        model_inputs.append(pending)
        return {"messages": [*pending, AIMessage(content="Use p20; handle the required input and invalidation.")]}
    graph = StateGraph(ParentState)
    graph.add_node("decision", model_step)
    graph.add_edge(START, "decision")
    graph.add_edge("decision", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    result = compiled.invoke({"messages": []}, {"configurable": {"thread_id": "run"}})
    control.acknowledge_parent_messages(result, run_id="run")
    assert len(model_inputs) == 1 and len(model_inputs[0]) == 3
    contents = "\n".join(message.content for message in model_inputs[0])
    assert '"version": "20"' in contents and '"version": "1"' not in contents
    assert "Which source?" in contents and "accepted-p0" in contents
    assert database.list_runtime_parent_attention("run") == []
    rows = database.list_runtime_episode_messages(run_id="run", recipient="supervisor:run", pending_only=False)
    assert len(rows) == 22 and all(item["deliveryState"] == "processed" for item in rows)
    assert all(item["receipt"].get("supersededByMessageId") for item in rows[:20])


def test_parked_parent_wakes_for_durable_user_guidance_even_when_signal_is_lost(database, monkeypatch):
    import erc.command_router as router_module
    import erc.run_service as run_module
    import erc.session_lane_scheduler as lane_module
    import runtimes.chat.runtime as chat_module
    enqueue(database)
    database.claim_runtime_episode(worker_id="long-A", lease_seconds=30)
    database.update_run_record("run", status="running", metadata={"runtimeEpisodeResume": {"state": "waiting", "episodeIds": ["A"]}})
    for index in range(2):
        database.add_chat_user_message_queue_item(queue_id=f"guide-{index}", session_id="session", run_id="run", client_message_id=f"client-{index}", content=f"Revised requirement {index}")
        database.update_chat_user_message_queue_item(f"guide-{index}", state="promoted", timestamp_field="promoted_at")
    for module in (router_module, run_module, chat_module):
        monkeypatch.setattr(module, "db", database)
    monkeypatch.setattr(lane_module, "session_lane_scheduler", lane_module.SessionLaneScheduler())
    router = router_module.RuntimeCommandRouter()
    requests = []
    router.configure(schedule_chat_run=lambda request, **kwargs: requests.append(kwargs) or "run")
    monkeypatch.setattr(router, "_build_runtime_handoff_resume_chat_request", lambda *_args, **_kwargs: {"sameRun": "run"})
    monkeypatch.setattr(router, "_emit_resume_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router_module, "runtime_command_router", router)
    runner = runner_module.RuntimeEpisodeRunner()
    runner._recover_parent_wakes()
    runner._recover_parent_wakes()
    assert len(requests) == 1 and requests[0]["run_id"] == "run"
    monkeypatch.setattr(chat_module.erc_kernel, "consume_control_signal", lambda _run: None)
    runtime = chat_module.ChatRuntime()
    first = runtime.consume_control_signal("run")
    assert first == {"command": "guidance", "payload": {"queueMessageId": "guide-0"}}
    database.update_chat_user_message_queue_item("guide-0", state="injected", timestamp_field="injected_at")
    assert runtime.consume_control_signal("run")["payload"]["queueMessageId"] == "guide-1"
    monkeypatch.setattr(chat_module.erc_kernel, "consume_control_signal", lambda _run: {"command": "cancel"})
    assert runtime.consume_control_signal("run") == {"command": "cancel"}
    assert database.get_runtime_episode("A")["state"] == "active"
    assert len(database.list_runtime_episodes(run_id="run")) == 1


def test_partial_acceptance_rechecks_current_version_after_inspection_gap(database, monkeypatch):
    enqueue(database)
    claim = database.claim_runtime_episode(worker_id="producer", lease_seconds=30)
    output = {"outputKey": "report", "version": "v1", "sourceVersion": "s1", "usableFor": ["B"], "compactSummary": "first", "proofRefs": ["proof:1"]}
    v1 = control.publish_partial("A", handoff=output, worker_id="producer", lease_generation=claim["leaseGeneration"])
    assert control.inspect_episode("A", session_id="session", run_id="run")["handoffs"][-1]["version"] == "v1"
    accept_transaction = database.accept_runtime_episode_partial
    def interleaved_accept(**kwargs):
        control.publish_partial("A", handoff={**output, "version": "v2", "sourceVersion": "s2"}, worker_id="producer", lease_generation=claim["leaseGeneration"])
        return accept_transaction(**kwargs)
    monkeypatch.setattr(database, "accept_runtime_episode_partial", interleaved_accept)
    with pytest.raises(ValueError, match="superseded"):
        control.accept_partial("A", session_id="session", run_id="run", handoff_id=v1["handoffRefId"], consumers=["B"], reason="use inspected result", request_id="stale-accept")
    assert database.list_runtime_episode_messages(run_id="run", recipient="partial:A", pending_only=False) == []


def test_partial_version_check_and_acceptance_append_hold_one_db_writer(database, monkeypatch):
    enqueue(database)
    claim = database.claim_runtime_episode(worker_id="producer", lease_seconds=30)
    output = {"outputKey": "report", "version": "v1", "sourceVersion": "s1", "usableFor": ["B"], "compactSummary": "first", "proofRefs": ["proof:1"]}
    v1 = control.publish_partial("A", handoff=output, worker_id="producer", lease_generation=claim["leaseGeneration"])
    append = database.append_runtime_episode_message
    observed = []
    def at_append(**kwargs):
        if kwargs["kind"] == "accept_partial":
            assert kwargs["_connection"].in_transaction
            # Deterministic injection exactly after the latest check and
            # before append: a competing writer cannot publish in this gap.
            with sqlite3.connect(database.db_path, timeout=0) as competitor:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competitor.execute("BEGIN IMMEDIATE")
            observed.append("producer fenced until acceptance commit")
        return append(**kwargs)
    monkeypatch.setattr(database, "append_runtime_episode_message", at_append)
    accepted = control.accept_partial("A", session_id="session", run_id="run", handoff_id=v1["handoffRefId"], consumers=["B"], reason="use v1", request_id="accept-v1")
    assert observed and accepted["deliveryState"] == "processed"
    control.publish_partial("A", handoff={**output, "version": "v2"}, worker_id="producer", lease_generation=claim["leaseGeneration"])
    invalidations = [item for item in database.list_runtime_parent_attention("run") if item["kind"] == "partial_invalidated"]
    assert len(invalidations) == 1 and invalidations[0]["content"]["acceptanceId"] == accepted["messageId"]


def test_restart_rebuilds_invalidation_after_canonical_partial_commit_crash(database, monkeypatch):
    enqueue(database)
    claim = database.claim_runtime_episode(worker_id="producer", lease_seconds=30)
    output = {"outputKey": "report", "version": "v1", "sourceVersion": "s1", "usableFor": ["B"], "compactSummary": "first", "proofRefs": ["proof:1"]}
    v1 = control.publish_partial("A", handoff=output, worker_id="producer", lease_generation=claim["leaseGeneration"])
    accepted = control.accept_partial("A", session_id="session", run_id="run", handoff_id=v1["handoffRefId"], consumers=["B"], reason="use v1", request_id="accept-before-crash")
    def crash_after_handoff(_episode):
        raise RuntimeError("crash after canonical handoff commit")
    with monkeypatch.context() as crash:
        crash.setattr(control, "reconcile_partial_invalidations", crash_after_handoff)
        with pytest.raises(RuntimeError, match="canonical"):
            control.publish_partial("A", handoff={**output, "version": "v2"}, worker_id="producer", lease_generation=claim["leaseGeneration"])
    assert not [item for item in database.list_runtime_parent_attention("run") if item["kind"] == "partial_invalidated"]
    restored = DatabaseManager(database.db_path)
    monkeypatch.setattr(control, "db", restored)
    for _ in range(2): control.reconcile_episode_attention(restored.get_runtime_episode("A"))
    pending = restored.list_runtime_parent_attention("run")
    invalidated = [item for item in pending if item["kind"] == "partial_invalidated"]
    assert len(invalidated) == 1 and invalidated[0]["content"]["acceptanceId"] == accepted["messageId"]
    assert invalidated[0]["content"]["handoffRefId"] == v1["handoffRefId"]
    assert len(restored.list_runtime_episode_handoffs("A")) == 2
    messages = control.parent_attention_messages({"messages": []}, run_id="run")
    control.acknowledge_parent_messages({"messages": messages}, run_id="run")
    control.reconcile_episode_attention(restored.get_runtime_episode("A"))
    assert restored.list_runtime_parent_attention("run") == []
