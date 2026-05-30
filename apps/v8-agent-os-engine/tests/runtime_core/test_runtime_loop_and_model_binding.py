import asyncio
import threading

from core.llm_chat_adapter import V8ChatModelAdapter
from erc.checkpoint_store import CheckpointStore


class _NativeModel:
    def bind_tools(self, _tools, **_kwargs):
        return self


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
