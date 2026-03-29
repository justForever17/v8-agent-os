from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.v8_agent_os_paths import CHECKPOINT_DB_PATH


class CheckpointStore:
    """
    统一管理 LangGraph 的持久化 checkpointer。
    当前先给 SupervisorAgentRunner 使用，后续可复用到 Workflow / Computer Use 等 runtime。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._thread_lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None
        self._conn: aiosqlite.Connection | None = None
        self._saver: AsyncSqliteSaver | None = None

    def _ensure_async_lock(self) -> asyncio.Lock:
        with self._thread_lock:
            if self._async_lock is None:
                self._async_lock = asyncio.Lock()
            return self._async_lock

    async def get_async_sqlite_saver(self) -> AsyncSqliteSaver:
        lock = self._ensure_async_lock()
        async with lock:
            if self._saver is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(self._path, timeout=5)
                await conn.execute("PRAGMA foreign_keys=ON;")
                saver = AsyncSqliteSaver(conn)
                await saver.setup()
                self._conn = conn
                self._saver = saver
            return self._saver

    async def close(self) -> None:
        lock = self._ensure_async_lock()
        async with lock:
            conn = self._conn
            self._saver = None
            self._conn = None
            if conn is not None:
                await conn.close()


checkpoint_store = CheckpointStore(CHECKPOINT_DB_PATH)
