from __future__ import annotations

import asyncio
import importlib
import threading
from types import SimpleNamespace

from agents.runners.supervisor_runner import SupervisorAgentRunner


runner_module = importlib.import_module("agents.runners.supervisor_runner")


def test_config_allows_a_real_multistep_tool_loop_but_still_bounds_runaway_graph():
    import pytest
    from typing_extensions import TypedDict
    from langgraph.graph import END, StateGraph
    from langgraph.errors import GraphRecursionError

    class State(TypedDict):
        observations: int
        actions: int
        runaway: bool

    graph = StateGraph(State)
    graph.add_node("observe", lambda state: {"observations": state["observations"] + 1})
    graph.add_node("act", lambda state: {"actions": state["actions"] + 1})
    graph.set_entry_point("observe")
    graph.add_conditional_edges("observe", lambda state: "act" if state["runaway"] or state["actions"] < 15 else END)
    graph.add_edge("act", "observe")
    compiled = graph.compile()
    initial = {"observations": 0, "actions": 0, "runaway": False}
    with pytest.raises(GraphRecursionError):
        compiled.invoke(initial, config={"recursion_limit": 25})
    config = SupervisorAgentRunner().build_graph_config("owned-fixture")
    completed = compiled.invoke(initial, config=config)
    assert completed["actions"] == 15 and completed["observations"] == 16
    with pytest.raises(GraphRecursionError):
        compiled.invoke({**initial, "runaway": True}, config=config)


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


def test_request_waits_for_registered_graph_prewarm_and_exposes_safe_status() -> None:
    async def exercise() -> None:
        runner = SupervisorAgentRunner()
        release = asyncio.Event()

        async def prewarm() -> dict[str, object]:
            await release.wait()
            return {"ok": True, "graphCacheHit": False, "graphBuildMs": 42.5}

        task = asyncio.create_task(prewarm())
        runner.register_prewarm_task(task)
        assert runner.prewarm_status()["state"] == "warming"

        waiter = asyncio.create_task(runner.wait_for_prewarm(timeout_seconds=1))
        await asyncio.sleep(0)
        assert not waiter.done()
        release.set()
        result = await waiter
        assert result["waited"] is True
        assert result["warmup"]["state"] == "ready"
        assert result["warmup"]["graphBuildMs"] == 42.5
        assert runner.prewarm_status() == {
            "state": "ready",
            "graphCacheHit": False,
            "graphBuildMs": 42.5,
            "inventoryFollowupAttempted": False,
            "inventoryFollowupCacheHit": False,
            "inventoryFollowupBuildMs": 0.0,
            "taskDone": True,
        }

    asyncio.run(exercise())


def test_graph_cache_miss_reasons_are_category_only() -> None:
    previous = '{"api_key":"opaque-a","model":"m","_runtimeInventory":{"subagentsHash":"a","mcpRevision":"one"}}'
    current = '{"api_key":"opaque-b","model":"m","_runtimeInventory":{"subagentsHash":"b","mcpRevision":"two"}}'
    assert SupervisorAgentRunner._graph_cache_miss_reasons(previous, current) == [
        "config",
        "subagents",
        "mcp_inventory",
    ]
