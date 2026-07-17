from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from pydantic import BaseModel
from typing_extensions import NotRequired, TypedDict

from erc.checkpoint_security import (
    CHECKPOINT_SECURITY_AUDIT_TABLE,
    V8_STABLE_MSGPACK_ALLOWLIST,
    CheckpointDeserializationBlocked,
    CheckpointPreflightError,
    CheckpointWriteContractError,
    StrictCheckpointSerializer,
    build_checkpoint_serializer,
    run_checkpoint_preflight,
)
from erc.checkpoint_store import CheckpointStore


@dataclass
class _BlockedPayload:
    value: str


class _StablePayload(BaseModel):
    label: str
    count: int


class _TypedState(TypedDict):
    payload: _StablePayload


class _RecoveryState(TypedDict):
    messages: Annotated[list, add_messages]
    phase: str
    gate_kind: str
    runtime_handoff: dict
    delegation_contexts: list
    pending_child_delegations: list
    answer: NotRequired[dict]


class _CrashState(TypedDict):
    phase: str
    proof: NotRequired[dict]


class _UnsafeReadState(TypedDict):
    payload: object


def _create_checkpoint_tables(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint BLOB,
            metadata BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        );
        CREATE TABLE writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            value BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        );
        """
    )
    return conn


def test_engine_main_enables_strict_msgpack_before_langgraph_import() -> None:
    engine_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["LANGGRAPH_STRICT_MSGPACK"] = "false"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import main; "
                "from langgraph.checkpoint.serde import _msgpack; "
                "assert _msgpack.STRICT_MSGPACK_ENABLED is True"
            ),
        ],
        cwd=engine_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_serializer_is_strict_without_pickle_or_broad_v8_allowlist() -> None:
    serializer = build_checkpoint_serializer()

    assert serializer.pickle_fallback is False
    assert getattr(serializer, "_allowed_msgpack_modules") is None
    assert V8_STABLE_MSGPACK_ALLOWLIST == ()


def test_blocked_deserialization_fails_instead_of_returning_raw_dict() -> None:
    permissive = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=True)
    strict = build_checkpoint_serializer()
    encoded = permissive.dumps_typed(_BlockedPayload(value="unsafe"))

    with pytest.raises(CheckpointDeserializationBlocked, match="_BlockedPayload"):
        strict.loads_typed(encoded)


def test_write_contract_rejects_nested_unregistered_object_before_persistence(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoints.db")

    async def _run() -> None:
        saver = await store.get_async_sqlite_saver()
        with pytest.raises(CheckpointWriteContractError, match="_BlockedPayload"):
            await saver.aput_writes(
                {
                    "configurable": {
                        "thread_id": "contract-thread",
                        "checkpoint_ns": "",
                        "checkpoint_id": "checkpoint-1",
                    }
                },
                (("runtime_handoff", {"payload": _BlockedPayload(value="unsafe")}),),
                "task-1",
            )
        await store.close()

    asyncio.run(_run())
    with sqlite3.connect(tmp_path / "checkpoints.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0


def test_preflight_scans_once_and_reuses_database_marker(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.db"
    serializer = build_checkpoint_serializer()
    checkpoint_type, checkpoint_blob = serializer.dumps_typed(
        {"v": 4, "id": "checkpoint-1", "channel_values": {"phase": "running"}}
    )
    write_type, write_blob = serializer.dumps_typed({"status": "waiting_approval"})
    with _create_checkpoint_tables(path) as conn:
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, '', ?, NULL, ?, ?, ?)",
            ("thread-1", "checkpoint-1", checkpoint_type, checkpoint_blob, b"{}"),
        )
        conn.execute(
            "INSERT INTO writes VALUES (?, '', ?, ?, 0, ?, ?, ?)",
            ("thread-1", "checkpoint-1", "task-1", "approval", write_type, write_blob),
        )
        conn.commit()

    first = run_checkpoint_preflight(path, serializer)
    second = run_checkpoint_preflight(path, build_checkpoint_serializer())

    assert first["mode"] == "full_scan"
    assert first["checkpointRows"] == 1
    assert first["writeRows"] == 1
    assert second["mode"] == "previously_completed"
    with sqlite3.connect(path) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {CHECKPOINT_SECURITY_AUDIT_TABLE}").fetchone()[0] == 1


def test_preflight_rejects_historical_unknown_type_without_marking_complete(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.db"
    permissive = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=True)
    blocked_type, blocked_blob = permissive.dumps_typed(_BlockedPayload(value="historical"))
    with _create_checkpoint_tables(path) as conn:
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, '', ?, NULL, ?, ?, ?)",
            ("thread-1", "checkpoint-1", blocked_type, blocked_blob, b"{}"),
        )
        conn.commit()

    with pytest.raises(CheckpointPreflightError, match="_BlockedPayload"):
        run_checkpoint_preflight(path, build_checkpoint_serializer())

    with sqlite3.connect(path) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {CHECKPOINT_SECURITY_AUDIT_TABLE}").fetchone()[0] == 0


def test_runtime_read_blocks_tampering_added_after_completed_preflight(tmp_path: Path) -> None:
    path = tmp_path / "tampered-after-audit.db"

    async def _run() -> None:
        initial_store = CheckpointStore(path)
        assert (await initial_store.ensure_preflight())["mode"] == "full_scan"
        await initial_store.close()

        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"payload": _BlockedPayload(value="tampered")}
        checkpoint["channel_versions"] = {"payload": "0001"}
        permissive = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=True)
        serialization_type, blob = permissive.dumps_typed(checkpoint)
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO checkpoints VALUES (?, '', ?, NULL, ?, ?, ?)",
                ("tampered-thread", checkpoint["id"], serialization_type, blob, b"{}"),
            )
            conn.commit()

        restarted_store = CheckpointStore(path)
        saver = await restarted_store.get_async_sqlite_saver()
        assert (await restarted_store.ensure_preflight())["mode"] == "previously_completed"
        graph = (
            StateGraph(_UnsafeReadState)
            .add_node("noop", lambda _state: {})
            .add_edge(START, "noop")
            .add_edge("noop", END)
            .compile(checkpointer=saver)
        )
        with pytest.raises(CheckpointDeserializationBlocked, match="_BlockedPayload"):
            await graph.aget_state({"configurable": {"thread_id": "tampered-thread"}})
        await restarted_store.close()

    asyncio.run(_run())


def test_schema_derived_allowlist_survives_store_restart(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.db"

    def keep_payload(_state: _TypedState) -> dict:
        return {}

    async def _run() -> None:
        store = CheckpointStore(path)
        saver = await store.get_async_sqlite_saver()
        graph = (
            StateGraph(_TypedState)
            .add_node("keep", keep_payload)
            .add_edge(START, "keep")
            .add_edge("keep", END)
            .compile(checkpointer=saver)
        )
        effective_serializer = graph.checkpointer.serde
        assert isinstance(effective_serializer, StrictCheckpointSerializer)
        assert (_StablePayload.__module__, _StablePayload.__name__) in getattr(
            effective_serializer,
            "_allowed_msgpack_modules",
        )
        config = {"configurable": {"thread_id": "typed-thread"}}
        await graph.ainvoke({"payload": _StablePayload(label="ready", count=2)}, config)
        await store.close()

        restarted_store = CheckpointStore(path)
        restarted_saver = await restarted_store.get_async_sqlite_saver()
        restarted_graph = (
            StateGraph(_TypedState)
            .add_node("keep", keep_payload)
            .add_edge(START, "keep")
            .add_edge("keep", END)
            .compile(checkpointer=restarted_saver)
        )
        snapshot = await restarted_graph.aget_state(config)
        assert isinstance(snapshot.values["payload"], _StablePayload)
        assert snapshot.values["payload"].label == "ready"
        await restarted_store.close()

    asyncio.run(_run())


@pytest.mark.parametrize("gate_kind", ["waiting_approval", "ask_user"])
def test_interrupt_runtime_and_child_lineage_resume_after_store_restart(
    tmp_path: Path,
    gate_kind: str,
) -> None:
    path = tmp_path / f"{gate_kind}.db"

    def prepare(_state: _RecoveryState) -> dict:
        return {
            "phase": "running",
            "runtime_handoff": {
                "runtime": "engineering",
                "status": "accepted",
                "artifactRefs": ["artifact://runtime/result"],
            },
            "delegation_contexts": [
                {
                    "delegationId": "delegation-child",
                    "status": "completed",
                    "acceptanceHint": "review child proof",
                }
            ],
            "pending_child_delegations": [
                {
                    "delegationId": "delegation-child",
                    "grandchildren": [
                        {
                            "delegationId": "delegation-grandchild",
                            "status": "completed",
                            "artifactRefs": ["artifact://grandchild/proof"],
                        }
                    ],
                }
            ],
            "messages": [
                AIMessage(
                    content="runtime prepared",
                    tool_calls=[
                        {
                            "name": "runtime_broker",
                            "args": {"mode": "route"},
                            "id": "call-runtime",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(content="accepted", tool_call_id="call-runtime"),
            ],
        }

    def wait_for_human(state: _RecoveryState) -> dict:
        answer = interrupt({"kind": state["gate_kind"], "summary": "human decision required"})
        return {"phase": "resumed", "answer": dict(answer)}

    def finish(_state: _RecoveryState) -> dict:
        return {"phase": "completed"}

    def build_graph(saver):
        return (
            StateGraph(_RecoveryState)
            .add_node("prepare", prepare)
            .add_node("human_gate", wait_for_human)
            .add_node("finish", finish)
            .add_edge(START, "prepare")
            .add_edge("prepare", "human_gate")
            .add_edge("human_gate", "finish")
            .add_edge("finish", END)
            .compile(checkpointer=saver)
        )

    async def _run() -> None:
        config = {"configurable": {"thread_id": f"thread-{gate_kind}"}}
        store = CheckpointStore(path)
        graph = build_graph(await store.get_async_sqlite_saver())
        interrupted = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "phase": "created",
                "gate_kind": gate_kind,
                "runtime_handoff": {},
                "delegation_contexts": [],
                "pending_child_delegations": [],
            },
            config,
        )
        assert interrupted["phase"] == "running"
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("human_gate",)
        assert snapshot.values["runtime_handoff"]["status"] == "accepted"
        await store.close()

        restarted_store = CheckpointStore(path)
        restarted_graph = build_graph(await restarted_store.get_async_sqlite_saver())
        completed = await restarted_graph.ainvoke(
            Command(resume={"accepted": True}),
            config,
        )
        assert completed["phase"] == "completed"
        assert completed["answer"] == {"accepted": True}
        assert completed["delegation_contexts"][0]["delegationId"] == "delegation-child"
        assert completed["pending_child_delegations"][0]["grandchildren"][0]["delegationId"] == (
            "delegation-grandchild"
        )
        await restarted_store.close()

    asyncio.run(_run())


def test_failed_node_resumes_from_last_successful_checkpoint_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "crash-resume.db"
    should_fail = {"value": True}

    def prepare(_state: _CrashState) -> dict:
        return {"phase": "prepared", "proof": {"ref": "artifact://prepared"}}

    def unstable(_state: _CrashState) -> dict:
        if should_fail["value"]:
            raise RuntimeError("simulated process failure")
        return {"phase": "completed"}

    def build_graph(saver):
        return (
            StateGraph(_CrashState)
            .add_node("prepare", prepare)
            .add_node("unstable", unstable)
            .add_edge(START, "prepare")
            .add_edge("prepare", "unstable")
            .add_edge("unstable", END)
            .compile(checkpointer=saver)
        )

    async def _run() -> None:
        config = {"configurable": {"thread_id": "crash-thread"}}
        store = CheckpointStore(path)
        graph = build_graph(await store.get_async_sqlite_saver())
        with pytest.raises(RuntimeError, match="simulated process failure"):
            await graph.ainvoke({"phase": "created"}, config)
        snapshot = await graph.aget_state(config)
        assert snapshot.values["phase"] == "prepared"
        assert snapshot.next == ("unstable",)
        await store.close()

        should_fail["value"] = False
        restarted_store = CheckpointStore(path)
        restarted_graph = build_graph(await restarted_store.get_async_sqlite_saver())
        completed = await restarted_graph.ainvoke(None, config)
        assert completed["phase"] == "completed"
        assert completed["proof"] == {"ref": "artifact://prepared"}
        await restarted_store.close()

    asyncio.run(_run())
