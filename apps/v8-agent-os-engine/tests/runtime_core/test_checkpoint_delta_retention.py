from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Annotated, get_args, get_type_hints

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage
from langgraph.channels import DeltaChannel
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

import core.storage_retention as storage_retention_module
from core.database import DatabaseManager
from core.storage_retention import StorageRetentionService
from erc.checkpoint_security import (
    CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY,
    build_checkpoint_serializer,
)
from erc.checkpoint_store import CheckpointStore
from graph.state_channels import (
    MESSAGE_DELTA_SNAPSHOT_FREQUENCY,
    reduce_message_deltas,
)
from graph.supervisor import AgentState


class _DeltaState(TypedDict):
    messages: Annotated[list[AnyMessage], DeltaChannel(reduce_message_deltas, snapshot_frequency=4)]


class _LegacyState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _build_graph(state_type: type[TypedDict], saver):
    def respond(_state):
        return {"messages": [AIMessage(content="ack")]}

    return (
        StateGraph(state_type)
        .add_node("respond", respond)
        .add_edge(START, "respond")
        .add_edge("respond", END)
        .compile(checkpointer=saver)
    )


def _payload_bytes(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        checkpoint_bytes = conn.execute(
            "SELECT COALESCE(SUM(length(checkpoint)), 0) FROM checkpoints"
        ).fetchone()[0]
        write_bytes = conn.execute("SELECT COALESCE(SUM(length(value)), 0) FROM writes").fetchone()[0]
    return int(checkpoint_bytes or 0) + int(write_bytes or 0)


def test_supervisor_message_state_uses_anymessage_delta_channel() -> None:
    annotation = get_type_hints(AgentState, include_extras=True)["messages"]
    value_type, channel = get_args(annotation)

    assert get_args(value_type)[0] is AnyMessage
    assert isinstance(channel, DeltaChannel)
    assert channel.reducer is reduce_message_deltas
    assert channel.snapshot_frequency == MESSAGE_DELTA_SNAPSHOT_FREQUENCY


def test_bulk_message_reducer_is_batching_invariant_and_honors_message_ids() -> None:
    original = HumanMessage(content="old", id="same")
    replacement = HumanMessage(content="new", id="same")
    assistant = AIMessage(content="answer", id="assistant")
    writes = [[original, assistant], [replacement], [RemoveMessage(id="assistant")]]

    once = reduce_message_deltas([], writes)
    folded = reduce_message_deltas(reduce_message_deltas([], writes[:1]), writes[1:])

    assert [(message.id, message.content) for message in once] == [("same", "new")]
    assert [(message.id, message.content) for message in folded] == [("same", "new")]


@pytest.mark.parametrize(
    ("marker", "expected"),
    [("seed", (True, True)), ("delta", (True, False)), ("none", (False, False))],
)
def test_retention_metadata_marker_avoids_checkpoint_blob_decryption(
    marker: str,
    expected: tuple[bool, bool],
) -> None:
    class _FailingSerializer:
        def loads_typed(self, _data):
            raise AssertionError("retention marker should avoid checkpoint blob decryption")

    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT 'encrypted' AS type, X'00' AS checkpoint, ? AS metadata",
            (
                json.dumps({CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY: marker}).encode("utf-8"),
            ),
        ).fetchone()
    assert StorageRetentionService._checkpoint_message_delta_flags(
        row,
        serializer=_FailingSerializer(),
    ) == expected


def test_legacy_full_message_checkpoint_resumes_under_delta_channel(tmp_path: Path) -> None:
    path = tmp_path / "legacy-to-delta.db"

    async def run() -> None:
        legacy_store = CheckpointStore(path)
        legacy_graph = _build_graph(_LegacyState, await legacy_store.get_async_sqlite_saver())
        config = {"configurable": {"thread_id": "legacy-thread"}}
        await legacy_graph.ainvoke({"messages": [HumanMessage(content="legacy")]}, config)
        await legacy_store.close()

        delta_store = CheckpointStore(path)
        delta_graph = _build_graph(_DeltaState, await delta_store.get_async_sqlite_saver())
        before = await delta_graph.aget_state(config)
        assert [message.content for message in before.values["messages"]] == ["legacy", "ack"]
        await delta_graph.ainvoke({"messages": [HumanMessage(content="modern")]}, config)
        after = await delta_graph.aget_state(config)
        assert [message.content for message in after.values["messages"]] == [
            "legacy",
            "ack",
            "modern",
            "ack",
        ]
        await delta_store.close()

    asyncio.run(run())


def test_delta_channel_reduces_long_thread_checkpoint_payload_and_restores(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.db"
    delta_path = tmp_path / "delta.db"

    async def seed(path: Path, state_type: type[TypedDict], thread_id: str) -> None:
        store = CheckpointStore(path)
        graph = _build_graph(state_type, await store.get_async_sqlite_saver())
        config = {"configurable": {"thread_id": thread_id}}
        for index in range(24):
            await graph.ainvoke(
                {"messages": [HumanMessage(content=f"message-{index}-" + "x" * 160)]},
                config,
            )
        await store.close()

    asyncio.run(seed(legacy_path, _LegacyState, "legacy"))
    asyncio.run(seed(delta_path, _DeltaState, "delta"))

    assert _payload_bytes(delta_path) < _payload_bytes(legacy_path) * 0.65
    with sqlite3.connect(delta_path) as conn:
        checkpoint_modes = {
            json.loads(bytes(row[0]).decode("utf-8"))[CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY]
            for row in conn.execute("SELECT metadata FROM checkpoints")
        }
    assert checkpoint_modes <= {"seed", "delta", "none"}
    assert "delta" in checkpoint_modes

    async def restore() -> None:
        store = CheckpointStore(delta_path)
        graph = _build_graph(_DeltaState, await store.get_async_sqlite_saver())
        snapshot = await graph.aget_state({"configurable": {"thread_id": "delta"}})
        assert len(snapshot.values["messages"]) == 48
        assert snapshot.values["messages"][-2].content.startswith("message-23-")
        await store.close()

    asyncio.run(restore())


def test_delta_retention_keeps_snapshot_anchor_and_semantic_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "state.db"
    checkpoint_path = tmp_path / "checkpoints.db"
    monkeypatch.setattr(storage_retention_module, "STATE_DB_PATH", state_path)
    monkeypatch.setattr(storage_retention_module, "CHECKPOINT_DB_PATH", checkpoint_path)
    db = DatabaseManager(state_path)
    db.create_or_update_session("delta-session", "delta", user_id="user")
    db.create_run_record(
        "delta-run",
        "delta-session",
        thread_id="delta-session",
        run_type="chat",
        status="completed",
    )

    async def seed_and_read() -> list[str]:
        store = CheckpointStore(checkpoint_path)
        graph = _build_graph(_DeltaState, await store.get_async_sqlite_saver())
        config = {"configurable": {"thread_id": "delta-session"}}
        for index in range(11):
            await graph.ainvoke({"messages": [HumanMessage(content=f"turn-{index}")]}, config)
        snapshot = await graph.aget_state(config)
        values = [message.content for message in snapshot.values["messages"]]
        await store.close()
        return values

    before = asyncio.run(seed_and_read())
    with sqlite3.connect(checkpoint_path) as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'delta-session'"
        ).fetchone()[0]

    action = StorageRetentionService()._prune_old_checkpoints(dry_run=False)

    async def read_after() -> list[str]:
        store = CheckpointStore(checkpoint_path)
        graph = _build_graph(_DeltaState, await store.get_async_sqlite_saver())
        snapshot = await graph.aget_state({"configurable": {"thread_id": "delta-session"}})
        values = [message.content for message in snapshot.values["messages"]]
        await store.close()
        return values

    after = asyncio.run(read_after())
    with sqlite3.connect(checkpoint_path) as conn:
        rows = conn.execute(
            "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints WHERE thread_id = 'delta-session' ORDER BY checkpoint_id"
        ).fetchall()

    assert action and action[0]["deltaAnchorCheckpoints"] >= 1
    assert len(rows) < count_before
    assert rows[0][1] is None
    assert after == before


def test_delta_retention_blocks_incomplete_parent_chain_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "state.db"
    checkpoint_path = tmp_path / "checkpoints.db"
    monkeypatch.setattr(storage_retention_module, "STATE_DB_PATH", state_path)
    monkeypatch.setattr(storage_retention_module, "CHECKPOINT_DB_PATH", checkpoint_path)
    db = DatabaseManager(state_path)
    db.create_or_update_session("broken", "broken", user_id="user")
    serializer = build_checkpoint_serializer()
    with sqlite3.connect(checkpoint_path) as conn:
        conn.executescript(
            """
            CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT,
                type TEXT, checkpoint BLOB, metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE TABLE writes (
                thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL,
                channel TEXT NOT NULL, type TEXT, value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            );
            """
        )
        seed = empty_checkpoint()
        seed["id"] = "001"
        seed["channel_values"] = {"messages": [HumanMessage(content="seed")]}
        seed_type, seed_blob = serializer.dumps_typed(seed)
        latest = empty_checkpoint()
        latest["id"] = "003"
        latest["channel_values"] = {}
        # A real non-snapshot DeltaChannel checkpoint still advertises the
        # messages channel version even though channel_values omits its seed.
        latest["channel_versions"] = {"messages": "00000000000000000000000000000001.0.0"}
        latest_type, latest_blob = serializer.dumps_typed(latest)
        conn.execute(
            "INSERT INTO checkpoints VALUES ('broken', '', '001', NULL, ?, ?, ?)",
            (seed_type, seed_blob, b"{}"),
        )
        conn.execute(
            "INSERT INTO checkpoints VALUES ('broken', '', '003', '002', ?, ?, ?)",
            (latest_type, latest_blob, b"{}"),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="parent chain is incomplete"):
        StorageRetentionService()._prune_old_checkpoints(dry_run=False)
    with sqlite3.connect(checkpoint_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2


def test_explicit_thread_delete_removes_only_target_checkpoint_lineage(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "delete-thread.db"

    async def run() -> dict[str, int]:
        store = CheckpointStore(checkpoint_path)
        graph = _build_graph(_DeltaState, await store.get_async_sqlite_saver())
        for thread_id in ("delete-me", "keep-me"):
            await graph.ainvoke(
                {"messages": [HumanMessage(content=f"message-{thread_id}")]},
                {"configurable": {"thread_id": thread_id}},
            )
        counts = await store.delete_thread("delete-me")
        await store.close()
        return counts

    counts = asyncio.run(run())

    assert counts["checkpoints"] > 0
    with sqlite3.connect(checkpoint_path) as conn:
        for table in ("checkpoints", "writes", "blobs"):
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not table_exists:
                assert counts[table] == 0
                continue
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?",
                ("delete-me",),
            ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            ("keep-me",),
        ).fetchone()[0] > 0


def test_runtime_snapshots_replace_same_session_projection(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    db = DatabaseManager(state_path)
    db.create_or_update_session("snapshot-session", "snapshot", user_id="user")
    db.create_run_record(
        "snapshot-run",
        "snapshot-session",
        thread_id="snapshot-session",
        run_type="chat",
        status="running",
    )

    db.add_runtime_snapshot(
        "snapshot-1",
        "snapshot-session",
        "snapshot-run",
        1,
        "chat_projection",
        {"value": 1},
    )
    db.add_runtime_snapshot(
        "snapshot-2",
        "snapshot-session",
        "snapshot-run",
        2,
        "chat_projection",
        {"value": 2},
    )

    latest = db.get_latest_runtime_snapshot("snapshot-session")
    assert latest["id"] == "snapshot-2"
    assert latest["snapshot"] == {"value": 2}
    with sqlite3.connect(state_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_snapshots WHERE session_id = ? AND snapshot_type = ?",
            ("snapshot-session", "chat_projection"),
        ).fetchone()[0] == 1
