from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Sequence

from core.langgraph_checkpoint_bootstrap import enforce_strict_langgraph_msgpack

enforce_strict_langgraph_msgpack()

import aiosqlite
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.v8_agent_os_paths import CHECKPOINT_DB_PATH
from erc.checkpoint_security import (
    StrictCheckpointSerializer,
    build_checkpoint_serializer,
    checkpoint_encryption_key_info,
    checkpoint_retention_metadata,
    run_checkpoint_preflight,
    strict_checkpoint_serializer,
)


logger = logging.getLogger(__name__)


class V8AsyncSqliteSaver(AsyncSqliteSaver):
    """Async SQLite saver whose schema-derived clones keep V8OS write governance."""

    _RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 5.0)

    def __init__(self, conn: aiosqlite.Connection, *, serde: SerializerProtocol) -> None:
        super().__init__(conn, serde=serde)
        self._v8_write_lock = asyncio.Lock()

    def _strict_serializer(self) -> StrictCheckpointSerializer:
        return strict_checkpoint_serializer(self.serde)

    def _assert_encrypted_serializer(self) -> None:
        checkpoint_encryption_key_info(self.serde)

    @classmethod
    async def _call_with_lock_retry(cls, operation: Any, *args: Any, **kwargs: Any) -> Any:
        last_exc: sqlite3.OperationalError | None = None
        for attempt, delay in enumerate((*cls._RETRY_DELAYS, 0.0), start=1):
            try:
                return await operation(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                last_exc = exc
                if delay <= 0:
                    break
                logger.debug(
                    "LangGraph checkpoint write hit SQLite lock; retrying attempt %s/%s after %.2fs",
                    attempt,
                    len(cls._RETRY_DELAYS) + 1,
                    delay,
                )
                await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
        return await operation(*args, **kwargs)

    async def aput(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> Any:
        self._strict_serializer()
        self._assert_encrypted_serializer()
        # Every state mutation enters through aput_writes(), where the new value is
        # checked deeply. The full checkpoint is derived from those accepted writes;
        # re-walking accumulated messages here would make every step O(history).
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("channel_values"), dict):
            raise RuntimeError("LangGraph checkpoint does not match the expected mapping contract.")
        governed_metadata = checkpoint_retention_metadata(metadata, checkpoint)
        async with self._v8_write_lock:
            return await self._call_with_lock_retry(
                super().aput,
                config,
                checkpoint,
                governed_metadata,
                new_versions,
            )

    async def aput_writes(
        self,
        config: Any,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        strict_serializer = self._strict_serializer()
        self._assert_encrypted_serializer()
        materialized_writes = tuple(writes)
        for index, (channel, value) in enumerate(materialized_writes):
            strict_serializer.assert_write_safe(value, root=f"writes[{index}].{channel}")
        async with self._v8_write_lock:
            await self._call_with_lock_retry(
                super().aput_writes,
                config,
                materialized_writes,
                task_id,
                task_path,
            )


class CheckpointStore:
    """Own the strict LangGraph checkpointer and its one-time historical preflight."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._thread_lock = threading.Lock()
        self._state_by_loop: dict[int, dict[str, object | None]] = {}

    async def get_async_sqlite_saver(self) -> V8AsyncSqliteSaver:
        loop_id = id(asyncio.get_running_loop())
        with self._thread_lock:
            state = self._state_by_loop.get(loop_id)
            if state is None:
                state = {"conn": None, "saver": None, "preflight": None}
                self._state_by_loop[loop_id] = state
            saver = state.get("saver")
            if isinstance(saver, V8AsyncSqliteSaver):
                return saver

        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path, timeout=120)
        try:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA busy_timeout=120000;")
            await conn.execute("PRAGMA wal_autocheckpoint=1000;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            await conn.commit()

            serializer = build_checkpoint_serializer()
            saver = V8AsyncSqliteSaver(conn, serde=serializer)
            await saver.setup()
            preflight = await asyncio.to_thread(run_checkpoint_preflight, self._path, serializer)
        except Exception:
            await conn.close()
            with self._thread_lock:
                self._state_by_loop.pop(loop_id, None)
            raise

        with self._thread_lock:
            state = self._state_by_loop.setdefault(
                loop_id,
                {"conn": None, "saver": None, "preflight": None},
            )
            state["conn"] = conn
            state["saver"] = saver
            state["preflight"] = preflight
        return saver

    async def ensure_preflight(self) -> dict[str, Any]:
        await self.get_async_sqlite_saver()
        loop_id = id(asyncio.get_running_loop())
        with self._thread_lock:
            state = self._state_by_loop.get(loop_id) or {}
            result = state.get("preflight")
        if not isinstance(result, dict):
            raise RuntimeError("Checkpoint security preflight completed without an audit result.")
        return dict(result)

    async def delete_thread(self, thread_id: str) -> dict[str, int]:
        """Delete one explicitly removed session's checkpoint lineage."""

        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            return {"checkpoints": 0, "writes": 0, "blobs": 0}
        saver = await self.get_async_sqlite_saver()
        counts = {"checkpoints": 0, "writes": 0, "blobs": 0}
        async with saver._v8_write_lock:
            for table in ("writes", "checkpoints", "blobs"):
                exists = await saver.conn.execute_fetchall(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if not exists:
                    continue
                cursor = await saver.conn.execute(
                    f"DELETE FROM {table} WHERE thread_id = ?",
                    (normalized_thread_id,),
                )
                counts[table] = max(0, int(cursor.rowcount or 0))
            await saver.conn.commit()
        return counts

    async def close(self) -> None:
        with self._thread_lock:
            states = list(self._state_by_loop.values())
            self._state_by_loop.clear()
        for state in states:
            conn = state.get("conn")
            if isinstance(conn, aiosqlite.Connection):
                await conn.close()


checkpoint_store = CheckpointStore(CHECKPOINT_DB_PATH)
