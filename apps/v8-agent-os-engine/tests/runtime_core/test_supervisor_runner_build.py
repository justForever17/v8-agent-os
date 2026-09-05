from __future__ import annotations

import asyncio
import importlib
import threading
from types import SimpleNamespace

from agents.runners.supervisor_runner import SupervisorAgentRunner


runner_module = importlib.import_module("agents.runners.supervisor_runner")


def test_graph_build_keeps_event_loop_responsive_and_caches_once(monkeypatch) -> None:
    async def exercise() -> None:
        runner = SupervisorAgentRunner()
        entered = threading.Event()
        release = threading.Event()
        build_threads: list[int] = []
        graph = object()
        inventory_revision = {"value": "mcp-a"}

        async def get_checkpointer():
            return object()

        def build_graph(_config, *, checkpointer):
            assert checkpointer is not None
            build_threads.append(threading.get_ident())
            entered.set()
            assert release.wait(timeout=2.0)
            return graph

        monkeypatch.setattr(runner_module.checkpoint_store, "get_async_sqlite_saver", get_checkpointer)
        monkeypatch.setattr(runner_module, "create_supervisor_graph", build_graph)
        monkeypatch.setattr(
            runner_module.extensions_runtime_service,
            "get_mcp_startup_status",
            lambda: {"inventoryRevision": inventory_revision["value"]},
        )
        config = SimpleNamespace(model_dump=lambda **_kwargs: {"model": "test"})
        main_thread = threading.get_ident()

        first = asyncio.create_task(runner.build_graph(config))
        second = asyncio.create_task(runner.build_graph(config))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()

        # Compilation is still blocked, while the Engine event loop remains live.
        await asyncio.sleep(0)
        assert not first.done()
        assert len(build_threads) == 1
        assert build_threads[0] != main_thread

        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result[0] is graph
        assert second_result[0] is graph
        assert len(build_threads) == 1
        assert {first_result[1]["graphCacheHit"], second_result[1]["graphCacheHit"]} == {False, True}

        inventory_revision["value"] = "mcp-b"
        refreshed_result = await runner.build_graph(config)
        assert refreshed_result[0] is graph
        assert refreshed_result[1]["graphCacheHit"] is False
        assert len(build_threads) == 2

    asyncio.run(exercise())
