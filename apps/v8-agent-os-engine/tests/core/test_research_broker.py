from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.tools.research_broker as research_module
from core.agents import default_subagent_configs
from core.runtime_tool_access import RUNTIME_TOOL_GROUPS, filter_visible_tools_for_actor


@pytest.fixture(autouse=True)
def _isolated_research_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))


class _ToolRef:
    def __init__(self, name: str):
        self.name = name


def test_research_broker_is_runtime_granted_tool():
    assert "research.core" in RUNTIME_TOOL_GROUPS
    assert RUNTIME_TOOL_GROUPS["research.core"]["toolNames"] == ["research_broker"]

    tools = [_ToolRef("runtime_broker"), _ToolRef("research_broker")]
    assert [tool.name for tool in filter_visible_tools_for_actor(tools, actor="supervisor")] == ["runtime_broker"]

    visible = filter_visible_tools_for_actor(
        tools,
        actor="supervisor",
        route_context={"runtimeToolGrants": [{"group": "research.core"}]},
    )
    assert [tool.name for tool in visible] == ["runtime_broker", "research_broker"]


def test_web_research_architect_is_global_default_subagent():
    agents = {agent.id: agent for agent in default_subagent_configs()}
    research_architect = agents["web-research-architect"]

    assert research_architect.globalExposure is True
    assert research_architect.capabilitySnapshot["specialistFamily"] == "research"


def test_research_broker_plan_clamps_shards_to_config(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 10, "maxShardCount": 30, "maxRounds": 5}},
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="plan",
            question="V8 Agent OS research runtime design",
            maxShards=99,
            state={"run_id": "run-test"},
        )
    )

    assert payload["ok"] is True
    assert payload["limits"]["effectiveMaxShards"] == 30
    assert payload["limits"]["hardMaxShardCount"] == 30
    assert len(payload["shards"]) <= 30
    assert payload["shardDefaults"]["sideEffects"] == "read_only"
    assert payload["shardDefaults"]["contextIsolation"] == "atomic_brief_only"
    assert set(payload["shards"][0]) == {"shardId", "kind", "query", "reason"}


def test_research_broker_run_returns_evidence_bundle(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 2, "maxShardCount": 3, "maxRounds": 2}},
    )

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Official docs",
                        "url": "https://docs.example.com/research",
                        "snippet": "Primary source.",
                    },
                    {
                        "title": "Forum answer",
                        "url": "https://forum.example.net/thread",
                        "snippet": "Secondary source.",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "title": "Official docs",
                "status": 200,
                "text": "This is a compact official page body.",
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="research runtime evidence contract",
            allowedDomains=["docs.example.com"],
            maxShards=2,
            state={"run_id": "run-test"},
        )
    )

    assert payload["ok"] is True
    assert payload["kind"] == "research_evidence_bundle"
    assert payload["evidenceBundleId"].startswith("research_")
    assert payload["sourceMatrix"][0]["host"] == "docs.example.com"
    assert payload["sourceMatrix"][0]["tier"] == "primary"
    assert payload["confidence"] in {"medium", "high"}
    assert payload["shards"][0]["fetchedTopSources"][0]["textPreview"]
    assert payload["finalExperiencePack"]["architectAgentId"] == "web-research-architect"
    assert "Web Research Architect final result" in payload["answer"]
    assert payload["finalExperiencePack"]["sourceUrls"][0]["url"] == "https://docs.example.com/research"

    observed = json.loads(
        research_module.research_broker.func(
            mode="observe",
            state={"run_id": "run-test"},
        )
    )
    assert observed["counts"]["evidenceBundles"] >= 1
    bundle_id = payload["evidenceBundleId"]
    fetched = json.loads(
        research_module.research_broker.func(
            mode="get_evidence",
            evidenceBundleId=bundle_id,
            state={"run_id": "run-test"},
        )
    )
    assert fetched["ok"] is True
    promoted = json.loads(
        research_module.research_broker.func(
            mode="promote_experience",
            evidenceBundleId=bundle_id,
            title="Research runtime evidence contract",
            tags=["research", "runtime"],
            state={"run_id": "run-test"},
        )
    )
    assert promoted["ok"] is True
    matches = json.loads(
        research_module.research_broker.func(
            mode="search_experience",
            query="evidence contract",
            state={"run_id": "run-test"},
        )
    )
    assert matches["items"]


def test_research_broker_video_policy_uses_popularity_signals_and_stays_compact(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 10, "maxShardCount": 30, "maxRounds": 2}},
    )

    long_snippet = "Official reference video with 1.2M views and 80K likes. " * 40

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Top Seedance reference - 1.2M views",
                        "url": "https://www.youtube.com/watch?v=seedance",
                        "snippet": long_snippet,
                    },
                    {
                        "title": "Bilibili Seedance breakdown 300万播放",
                        "url": "https://www.bilibili.com/video/BV123",
                        "snippet": "300万播放 12万点赞 creative breakdown",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        return json.dumps({"ok": True, "title": "Video page", "status": 200, "text": "video detail " * 200})

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    output = research_module.research_broker.func(
        mode="run",
        question="Seedance 2.0 video style reference",
        researchIntent="video popularity references",
        sourcePolicy="video_popularity",
        maxShards=30,
        state={"run_id": "run-video"},
    )
    payload = json.loads(output)

    assert len(output) < 12000
    assert payload["sourceMatrix"][0]["catalogCategory"] == "video_platform"
    assert payload["sourceMatrix"][0]["popularitySignals"]
    assert payload["omitted"]["shardsOmitted"] >= 0
