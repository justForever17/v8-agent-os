from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.v8_agent_os_paths import CHECKPOINT_DB_PATH

logger = logging.getLogger(__name__)


class CheckpointStore:
    """
    统一管理 LangGraph 的持久化 checkpointer。
    当前先给 SupervisorAgentRunner 使用，后续可复用到 Workflow / Computer Use 等 runtime。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._thread_lock = threading.Lock()
        self._state_by_loop: dict[int, dict[str, object | None]] = {}

    async def get_async_sqlite_saver(self) -> AsyncSqliteSaver:
        loop_id = id(asyncio.get_running_loop())
        with self._thread_lock:
            state = self._state_by_loop.get(loop_id)
            if state is None:
                state = {"conn": None, "saver": None}
                self._state_by_loop[loop_id] = state
            saver = state.get("saver")
            if isinstance(saver, AsyncSqliteSaver):
                return saver
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path, timeout=120)
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA busy_timeout=120000;")
        await conn.execute("PRAGMA wal_autocheckpoint=1000;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.commit()
        base_saver = AsyncSqliteSaver(conn)
        await base_saver.setup()
        saver = self._install_write_lock(base_saver)
        with self._thread_lock:
            state = self._state_by_loop.setdefault(loop_id, {"conn": None, "saver": None})
            state["conn"] = conn
            state["saver"] = saver
        return saver

    @staticmethod
    def _install_write_lock(saver: AsyncSqliteSaver) -> AsyncSqliteSaver:
        write_lock = asyncio.Lock()
        original_aput = saver.aput
        original_aput_writes = saver.aput_writes
        retry_delays = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 5.0)

        async def _call_with_lock_retry(operation: Any, *args: Any, **kwargs: Any) -> Any:
            last_exc: sqlite3.OperationalError | None = None
            for attempt, delay in enumerate((*retry_delays, 0.0), start=1):
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
                        len(retry_delays) + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
            if last_exc is not None:
                raise last_exc
            return await operation(*args, **kwargs)

        async def locked_aput(*args: Any, **kwargs: Any) -> Any:
            async with write_lock:
                return await _call_with_lock_retry(original_aput, *args, **kwargs)

        async def locked_aput_writes(*args: Any, **kwargs: Any) -> Any:
            async with write_lock:
                return await _call_with_lock_retry(original_aput_writes, *args, **kwargs)

        saver.aput = locked_aput  # type: ignore[method-assign]
        saver.aput_writes = locked_aput_writes  # type: ignore[method-assign]
        return saver

    async def close(self) -> None:
        with self._thread_lock:
            states = list(self._state_by_loop.values())
            self._state_by_loop.clear()
        for state in states:
            conn = state.get("conn")
            if isinstance(conn, aiosqlite.Connection):
                await conn.close()


checkpoint_store = CheckpointStore(CHECKPOINT_DB_PATH)
