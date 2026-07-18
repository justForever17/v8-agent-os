from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import pytest
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.channels import DeltaChannel
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

import erc.checkpoint_governance as governance_module
from core.database import DatabaseManager
from erc.command_router import RuntimeCommandRouter
from erc.checkpoint_governance import CheckpointGovernanceError, CheckpointGovernanceService
from erc.checkpoint_store import CheckpointStore
from erc.models import RuntimeCommand
from graph.state_channels import reduce_message_deltas


class _GovernanceState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], DeltaChannel(reduce_message_deltas, snapshot_frequency=4)]
    phase: str
    count: int
    session_id: str
    sessionId: str
    run_id: str
    runId: str
    current_route_context: dict
    plugin_authorizations: list
    pluginAuthorizations: list
    context_session_refs: list
    contextSessionRefs: list
    session_coordination: dict
    sessionCoordination: dict
    engineering_context: dict


async def _seed_governed_graph(path: Path, side_effects: list[str]):
    def prepare(_state: _GovernanceState) -> dict:
        return {"phase": "prepared"}

    def execute(state: _GovernanceState) -> dict:
        side_effects.append(str(state.get("session_id") or "unknown"))
        return {"phase": "completed", "count": int(state.get("count") or 0) + 1}

    store = CheckpointStore(path)
    saver = await store.get_async_sqlite_saver()
    graph = (
        StateGraph(_GovernanceState)
        .add_node("prepare", prepare)
        .add_node("execute", execute)
        .add_edge(START, "prepare")
        .add_edge("prepare", "execute")
        .add_edge("execute", END)
        .compile(checkpointer=saver)
    )
    config = {"configurable": {"thread_id": "source-session"}}
    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="start")],
            "phase": "created",
            "count": 0,
            "session_id": "source-session",
            "sessionId": "source-session",
            "run_id": "source-run",
            "runId": "source-run",
            "current_route_context": {
                "session_id": "source-session",
                "run_id": "source-run",
                "workspace_path": "E:/workspace",
            },
            "plugin_authorizations": [{"pluginId": "figma", "grantId": "grant-old"}],
            "pluginAuthorizations": [{"pluginId": "figma", "grantId": "grant-old"}],
            "engineering_context": {},
        },
        config,
    )
    history = [snapshot async for snapshot in graph.aget_state_history(config)]
    before_execute = next(snapshot for snapshot in history if snapshot.next == ("execute",))
    checkpoint_id = str(before_execute.config["configurable"]["checkpoint_id"])
    return store, graph, checkpoint_id


def _patch_governance(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: DatabaseManager,
    graph,
) -> None:
    monkeypatch.setattr(governance_module, "db", database)

    async def build_graph(_config):
        return graph, {"graphCacheHit": True}

    monkeypatch.setattr(governance_module.supervisor_runner, "build_graph", build_graph)
    monkeypatch.setattr(
        governance_module,
        "resolve_engine_config_for_role",
        lambda _role: {"engine_config": object()},
    )


def _seed_database(path: Path, *, status: str = "completed") -> DatabaseManager:
    database = DatabaseManager(path)
    database.create_or_update_session("source-session", "Source", user_id="owner")
    database.create_run_record(
        "source-run",
        "source-session",
        thread_id="source-session",
        user_id="owner",
        run_type="chat",
        status=status,
    )
    database.upsert_session_scope_binding(
        {
            "session_id": "source-session",
            "conversation_id": "source-session",
            "thread_id": "source-session",
            "user_id": "owner",
            "workspace_id": "workspace-1",
            "workspace_path": "E:/workspace",
            "project_id": "project-1",
            "resolved_scope": "project:project-1",
            "scope_source": "test",
            "scope_confidence": 1.0,
            "status": "active",
        }
    )
    return database


def test_replay_requires_approval_and_reexecutes_only_after_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "checkpoints.db"
    side_effects: list[str] = []

    async def run() -> None:
        store, graph, checkpoint_id = await _seed_governed_graph(checkpoint_path, side_effects)
        database = _seed_database(tmp_path / "state.db")
        _patch_governance(monkeypatch, database=database, graph=graph)
        service = CheckpointGovernanceService(checkpoint_path)

        operation = await service.plan(
            mode="replay",
            source_session_id="source-session",
            source_checkpoint_id=checkpoint_id,
            user_id="owner",
        )
        assert operation["state"] == "awaiting_approval"
        assert side_effects == ["source-session"]
        with pytest.raises(CheckpointGovernanceError, match="尚未获得人工批准"):
            await service.execute_approved(operation["operationId"])

        database.update_pending_approval(operation["approvalId"], status="approved", response={"confirmed": True})
        completed = await service.execute_approved(operation["operationId"])
        assert completed["state"] == "completed"
        assert side_effects == ["source-session", "source-session"]

        repeated = await service.execute_approved(operation["operationId"])
        assert repeated["state"] == "completed"
        assert side_effects == ["source-session", "source-session"]
        await store.close()

    asyncio.run(run())


def test_fork_creates_isolated_session_copies_scope_and_drops_plugin_grants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "checkpoints.db"
    side_effects: list[str] = []

    async def run() -> None:
        store, graph, checkpoint_id = await _seed_governed_graph(checkpoint_path, side_effects)
        database = _seed_database(tmp_path / "state.db")
        _patch_governance(monkeypatch, database=database, graph=graph)
        service = CheckpointGovernanceService(checkpoint_path)

        operation = await service.plan(
            mode="fork",
            source_session_id="source-session",
            source_checkpoint_id=checkpoint_id,
            user_id="owner",
            state_patch={"engineering_context": {"forked": True}},
        )
        with sqlite3.connect(checkpoint_path) as conn:
            stored_patch = str(
                conn.execute(
                    "SELECT state_patch_json FROM v8_checkpoint_operations WHERE operation_id = ?",
                    (operation["operationId"],),
                ).fetchone()[0]
            )
        assert "forked" not in stored_patch
        assert "+v8aesgcm1" in stored_patch
        database.update_pending_approval(operation["approvalId"], status="approved", response={"confirmed": True})
        completed = await service.execute_approved(operation["operationId"])
        target_session_id = completed["targetSessionId"]

        assert target_session_id != "source-session"
        assert database.get_session(target_session_id)["user_id"] == "owner"
        target_binding = database.get_session_scope_binding(target_session_id)
        assert target_binding["workspace_path"] == "E:/workspace"
        assert target_binding["thread_id"] == target_session_id
        target_snapshot = await graph.aget_state({"configurable": {"thread_id": target_session_id}})
        assert target_snapshot.values["count"] == 1
        assert target_snapshot.values["engineering_context"] == {"forked": True}
        assert target_snapshot.values["plugin_authorizations"] == []
        assert target_snapshot.values["pluginAuthorizations"] == []
        source_snapshot = await graph.aget_state({"configurable": {"thread_id": "source-session"}})
        assert source_snapshot.values["plugin_authorizations"][0]["grantId"] == "grant-old"
        assert side_effects[-1] == target_session_id
        await store.close()

    asyncio.run(run())


@pytest.mark.parametrize("status", ["running", "waiting_approval", "waiting_input", "paused"])
def test_plan_blocks_active_or_human_waiting_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    checkpoint_path = tmp_path / f"{status}.db"

    async def run() -> None:
        store, graph, checkpoint_id = await _seed_governed_graph(checkpoint_path, [])
        database = _seed_database(tmp_path / f"state-{status}.db", status=status)
        _patch_governance(monkeypatch, database=database, graph=graph)
        with pytest.raises(CheckpointGovernanceError, match="仍在运行或等待人工处理"):
            await CheckpointGovernanceService(checkpoint_path).plan(
                mode="replay",
                source_session_id="source-session",
                source_checkpoint_id=checkpoint_id,
                user_id="owner",
            )
        await store.close()

    asyncio.run(run())


def test_plan_rejects_cross_user_and_authority_state_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "governance-errors.db"

    async def run() -> None:
        store, graph, checkpoint_id = await _seed_governed_graph(checkpoint_path, [])
        database = _seed_database(tmp_path / "state.db")
        _patch_governance(monkeypatch, database=database, graph=graph)
        service = CheckpointGovernanceService(checkpoint_path)
        with pytest.raises(CheckpointGovernanceError, match="不能跨用户"):
            await service.plan(
                mode="replay",
                source_session_id="source-session",
                source_checkpoint_id=checkpoint_id,
                user_id="intruder",
            )
        with pytest.raises(CheckpointGovernanceError, match="不得修改权限或身份字段"):
            await service.plan(
                mode="fork",
                source_session_id="source-session",
                source_checkpoint_id=checkpoint_id,
                user_id="owner",
                state_patch={"workspace_path": "E:/other"},
            )
        await store.close()

    asyncio.run(run())


def test_approved_plan_is_invalidated_when_source_checkpoint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "changed-source.db"

    async def run() -> None:
        store, graph, checkpoint_id = await _seed_governed_graph(checkpoint_path, [])
        database = _seed_database(tmp_path / "state.db")
        _patch_governance(monkeypatch, database=database, graph=graph)
        service = CheckpointGovernanceService(checkpoint_path)
        operation = await service.plan(
            mode="replay",
            source_session_id="source-session",
            source_checkpoint_id=checkpoint_id,
            user_id="owner",
        )
        database.update_pending_approval(operation["approvalId"], status="approved", response={"confirmed": True})
        with sqlite3.connect(checkpoint_path) as conn:
            conn.execute(
                "UPDATE checkpoints SET metadata = ? WHERE thread_id = ? AND checkpoint_id = ?",
                (b'{"tampered":true}', "source-session", checkpoint_id),
            )
            conn.commit()
        with pytest.raises(CheckpointGovernanceError, match="计划已失效"):
            await service.execute_approved(operation["operationId"])
        await store.close()

    asyncio.run(run())


def test_rejected_operation_is_audited_and_never_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_path = tmp_path / "rejected.db"
    side_effects: list[str] = []

    async def run() -> None:
        store, graph, checkpoint_id = await _seed_governed_graph(checkpoint_path, side_effects)
        database = _seed_database(tmp_path / "state.db")
        _patch_governance(monkeypatch, database=database, graph=graph)
        service = CheckpointGovernanceService(checkpoint_path)
        operation = await service.plan(
            mode="fork",
            source_session_id="source-session",
            source_checkpoint_id=checkpoint_id,
            user_id="owner",
        )
        cancelled = service.reject_operation(operation["operationId"], reason="not_needed")
        assert cancelled["state"] == "cancelled"
        assert cancelled["result"]["reason"] == "not_needed"
        assert side_effects == ["source-session"]
        await store.close()

    asyncio.run(run())


def test_parallel_checkpoint_defers_ambiguous_node_inference_to_langgraph() -> None:
    def left(_state: _GovernanceState) -> dict:
        return {"phase": "left"}

    def right(_state: _GovernanceState) -> dict:
        return {"phase": "right"}

    def join(_state: _GovernanceState) -> dict:
        return {"phase": "joined"}

    graph = (
        StateGraph(_GovernanceState)
        .add_node("left", left)
        .add_node("right", right)
        .add_node("join", join)
        .add_edge(START, "left")
        .add_edge(START, "right")
        .add_edge("left", "join")
        .add_edge("right", "join")
        .add_edge("join", END)
        .compile()
    )
    snapshot = SimpleNamespace(metadata={"source": "loop"}, next=("join",))
    assert CheckpointGovernanceService._infer_as_node(snapshot, graph=graph) == ""


def test_explicit_as_node_must_exist_in_current_graph() -> None:
    graph = (
        StateGraph(_GovernanceState)
        .add_node("prepare", lambda _state: {"phase": "prepared"})
        .add_edge(START, "prepare")
        .add_edge("prepare", END)
        .compile()
    )
    snapshot = SimpleNamespace(metadata={}, next=())
    with pytest.raises(CheckpointGovernanceError, match="有效节点"):
        CheckpointGovernanceService._infer_as_node(snapshot, requested="removed_node", graph=graph)


def test_checkpoint_approval_routes_to_governance_without_generic_chat_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = {
        "approval_kind": "checkpoint_replay",
        "request": {"operationId": "checkpoint_op_1"},
    }
    monkeypatch.setattr(
        "erc.command_router.db.get_pending_approval",
        lambda _approval_id: approval,
    )
    monkeypatch.setattr(
        "erc.command_router.erc_kernel.approve",
        lambda *_args, **_kwargs: {"approval": approval},
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        governance_module.checkpoint_governance_service,
        "schedule_approved_operation",
        lambda operation_id: scheduled.append(operation_id) or {"scheduled": True},
    )
    router = RuntimeCommandRouter()
    monkeypatch.setattr(
        router,
        "_resume_from_approval",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic resume must not run")),
    )

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.approve",
            approval_id="approval-1",
            response={"confirmed": True},
        )
    )

    assert scheduled == ["checkpoint_op_1"]
    assert result and result["checkpoint_operation"] == {"scheduled": True}


def test_checkpoint_rejection_cancels_governed_operation_without_chat_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = {
        "approval_kind": "checkpoint_fork",
        "request": {"operationId": "checkpoint_op_2"},
    }
    monkeypatch.setattr(
        "erc.command_router.erc_kernel.reject",
        lambda *_args, **_kwargs: {"approval": approval},
    )
    rejected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        governance_module.checkpoint_governance_service,
        "reject_operation",
        lambda operation_id, *, reason: rejected.append((operation_id, reason)) or {"state": "cancelled"},
    )
    router = RuntimeCommandRouter()

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.reject",
            approval_id="approval-2",
            response={"reason": "user_changed_mind"},
        )
    )

    assert rejected == [("checkpoint_op_2", "user_changed_mind")]
    assert result and result["checkpoint_operation"] == {"state": "cancelled"}
