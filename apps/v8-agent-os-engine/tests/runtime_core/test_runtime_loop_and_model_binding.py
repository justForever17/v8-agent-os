import asyncio
import sqlite3
import threading

import pytest

from core.llm_chat_adapter import V8ChatModelAdapter
from erc.checkpoint_store import CheckpointStore


class _NativeModel:
    def bind_tools(self, _tools, **_kwargs):
        return self


class _FlakySaver:
    def __init__(self) -> None:
        self.aput_calls = 0
        self.aput_writes_calls = 0

    async def aput(self, *_args, **_kwargs):
        self.aput_calls += 1
        if self.aput_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"ok": True, "operation": "aput"}

    async def aput_writes(self, *_args, **_kwargs):
        self.aput_writes_calls += 1
        if self.aput_writes_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"ok": True, "operation": "aput_writes"}


class _BrokenSaver:
    async def aput(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("no such table: checkpoints")

    async def aput_writes(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("no such table: checkpoint_writes")


def test_bind_tools_does_not_deepcopy_runtime_client_locks():
    adapter = V8ChatModelAdapter(
        model_id="test-model",
        provider_standard="openai",
        role="supervisor",
        meta={"api_standard": "openai"},
        model_kwargs={},
        builder=lambda: _NativeModel(),
    )
    adapter._base_model = {"lock": threading.RLock()}

    bound = adapter.bind_tools([{"name": "demo_tool"}])

    assert bound._base_model is None
    assert len(bound._bound_tools or []) == 1


def test_checkpoint_store_isolates_savers_by_event_loop(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoints.sqlite3")

    async def _open_and_close():
        saver = await store.get_async_sqlite_saver()
        assert saver is not None
        await store.close()

    asyncio.run(_open_and_close())
    asyncio.run(_open_and_close())


def test_checkpoint_store_retries_transient_sqlite_write_locks():
    saver = CheckpointStore._install_write_lock(_FlakySaver())  # type: ignore[arg-type]

    async def _run():
        assert await saver.aput("config", "checkpoint", "metadata", "versions") == {"ok": True, "operation": "aput"}
        assert await saver.aput_writes("config", "writes", "task-id", "task-path") == {
            "ok": True,
            "operation": "aput_writes",
        }

    asyncio.run(_run())
    assert saver.aput_calls == 2
    assert saver.aput_writes_calls == 2


def test_checkpoint_store_does_not_retry_non_lock_sqlite_errors():
    saver = CheckpointStore._install_write_lock(_BrokenSaver())  # type: ignore[arg-type]

    async def _run():
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            await saver.aput("config", "checkpoint", "metadata", "versions")

    asyncio.run(_run())
