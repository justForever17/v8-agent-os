from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

from core.agents import build_subagent_registry_snapshot, default_subagent_configs


def test_next_graph_load_rebuilds_after_role_edit_but_reuses_unchanged_input(monkeypatch):
    module = importlib.import_module("agents.runners.supervisor_runner")
    records = [default_subagent_configs()[0].model_dump()]
    builds = []
    monkeypatch.setattr(module.storage, "get_all_agents", lambda: records)
    monkeypatch.setattr(module.extensions_runtime_service, "get_mcp_startup_status", lambda: {})

    async def saver():
        return object()

    monkeypatch.setattr(module.checkpoint_store, "get_async_sqlite_saver", saver)

    def build(_config, **_kwargs):
        result = {"boundPersona": records[0]["system_prompt"]}
        builds.append(result)
        return result

    monkeypatch.setattr(module, "create_supervisor_graph", build)
    runner = module.SupervisorAgentRunner()
    config = SimpleNamespace(model_dump=lambda **_kwargs: {"model": "fixture"})

    async def exercise():
        first, _ = await runner.build_graph(config)
        same, diagnostics = await runner.build_graph(config)
        assert same is first and diagnostics["graphCacheHit"]
        records[0]["system_prompt"] = "User edit: inspect concurrency before refactoring."
        updated, diagnostics = await runner.build_graph(config)
        assert not diagnostics["graphCacheHit"]
        assert updated["boundPersona"] == records[0]["system_prompt"]
        assert first["boundPersona"] != updated["boundPersona"]
        assert len(builds) == 2

    asyncio.run(exercise())


def test_episode_node_cache_refreshes_existing_identity_after_prose_edit(monkeypatch):
    from core.runtime_episode_runner import RuntimeEpisodeRunner
    from core.storage import storage
    from core import engine_config_resolver
    from graph import supervisor_builder

    records = [default_subagent_configs()[0].model_dump()]
    builds = []
    monkeypatch.setattr(storage, "get_all_agents", lambda: records)
    monkeypatch.setattr(storage, "get_supervisor_config", lambda: {})
    monkeypatch.setattr(engine_config_resolver, "resolve_engine_config_for_role", lambda _role: object())
    monkeypatch.setattr(engine_config_resolver, "require_engine_config", lambda config, **_kwargs: config)

    def build(**_kwargs):
        result = SimpleNamespace(agent_nodes_map={records[0]["id"]: {"boundPersona": records[0]["system_prompt"]}},
                                 subagent_registry_snapshot=build_subagent_registry_snapshot(records))
        builds.append(result)
        return result

    monkeypatch.setattr(supervisor_builder, "build_supervisor_runtime_bundle", build)
    runner = RuntimeEpisodeRunner()
    first = runner._build_agent_nodes_map()
    assert runner._build_agent_nodes_map() is first
    records[0]["system_prompt"] = "User edit: verify the artifact before delivery."
    updated = runner._build_agent_nodes_map()
    assert updated is not first
    assert updated[records[0]["id"]]["boundPersona"] == records[0]["system_prompt"]
    assert len(builds) == 2
