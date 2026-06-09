from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import core.tools.research_broker as research_module
from core.agents import default_subagent_configs
from core.runtime_tool_access import RUNTIME_TOOL_GROUPS, filter_visible_tools_for_actor


@pytest.fixture(autouse=True)
def _isolated_research_ledger(monkeypatch, tmp_path, request):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    if request.node.name != "test_web_research_architect_agent_falls_back_across_model_candidates":
        monkeypatch.setattr(research_module, "_invoke_web_research_architect_agent", lambda **kwargs: None)


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


def test_source_quality_gate_keeps_authoritative_docs_with_soft_nav_noise():
    gate = research_module._source_quality_gate(
        question="Python pathlib CLI best practices",
        result={
            "title": "pathlib — Object-oriented filesystem paths — Python documentation",
            "url": "https://docs.python.org/3/library/pathlib.html",
            "sourceQualityHints": {"authorityScore": 80},
        },
        read_payload={
            "ok": True,
            "text": "Theme Auto Light Dark\n" + ("Path classes represent filesystem paths and support concrete IO operations. " * 40),
        },
        source_policy="official_docs_first",
    )

    assert gate["selectedForEvidence"] is True
    assert not gate["rejectedReason"]


def test_context7_mcp_error_payload_is_not_usable_text():
    payload = {
        "result": {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "MCP error -32602: Input validation error: Invalid arguments for tool query-docs",
                }
            ],
        }
    }

    assert research_module._mcp_payload_is_error(payload) is True


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
    assert payload["researchAnswerPack"]["answer"] == payload["answer"]
    assert payload["researchAnswerPack"]["sources"][0]["url"] == "https://docs.example.com/research"
    assert payload["researchAnswerPack"]["score"]["confidence"] in {"medium", "high"}
    assert payload["claimTable"]
    assert payload["researchLoopState"]["phase"] == "research_loop"
    assert payload["researchLoopState"]["readSources"]
    assert payload["experienceReuse"]["reuseDecision"] in {"ignore", "refresh"}

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
    assert matches["reuseDecision"]["reuseDecision"] in {"reuse", "refresh"}


def test_research_broker_uses_source_router_by_default(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 1, "maxRounds": 1}},
    )
    calls: list[str] = []

    def fake_source_router_search(**kwargs):
        calls.append(kwargs["query"])
        return json.dumps(
            {
                "ok": True,
                "provider": "router",
                "networkRoute": "global",
                "providerAttemptMatrix": [{"provider": "router", "ok": True}],
                "results": [
                    {
                        "title": "Router source",
                        "url": "https://docs.router.example/page",
                        "snippet": "Router sourced result.",
                    }
                ],
            }
        )

    monkeypatch.setattr(research_module, "source_router_search", fake_source_router_search)

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="source router contract",
            maxShards=1,
            maxRounds=1,
            state={"run_id": "run-router"},
        )
    )

    assert calls
    assert payload["sourceMatrix"][0]["provider"] == "router"
    assert payload["providerAttemptMatrix"][0]["provider"] == "router"


def test_research_broker_uses_web_research_architect_agent_when_available(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {
            "research": {
                "enabled": True,
                "defaultShardCount": 1,
                "maxShardCount": 1,
                "maxRounds": 1,
                "architectAgentSynthesisEnabled": True,
            }
        },
    )

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Official Architect Source",
                        "url": "https://docs.example.com/architect",
                        "snippet": "Official source snippet.",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "title": "Official Architect Source",
                "status": 200,
                "text": "The official page says the architecture uses a source router, research loop, and synthesis pass.",
            }
        )

    def fake_architect(**kwargs):
        return {
            "headline": "模型提纯后的 Web Research Architect 结论",
            "researchResult": "最终结论：Research Runtime 应由 Source Router、Research Agent Loop 和 Web Research Architect 三层组成。",
            "claimTable": [
                {
                    "claim": "Research Runtime 应分为三层。",
                    "sourceURL": "https://docs.example.com/architect",
                    "refutingSources": [],
                    "confidence": "high",
                }
            ],
            "conflictMatrix": [],
            "missingEvidence": [],
            "assumptions": [],
        }

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    monkeypatch.setattr(research_module, "_invoke_web_research_architect_agent", fake_architect)

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="research runtime architect synthesis",
            maxShards=1,
            state={"run_id": "run-architect"},
        )
    )

    assert payload["finalExperiencePack"]["synthesisMode"] == "model_agent"
    assert payload["finalExperiencePack"]["modelSynthesis"]["agentId"] == "web-research-architect"
    assert payload["answer"].startswith("最终结论")
    assert payload["researchAnswerPack"]["answer"].startswith("最终结论")
    assert payload["researchAnswerPack"]["sources"][0]["url"] == "https://docs.example.com/architect"
    assert payload["claimTable"][0]["supportingSources"][0]["url"] == "https://docs.example.com/architect"


def test_research_answer_pack_rejects_footer_and_security_noise():
    pack = research_module._research_answer_pack(
        {
            "evidenceBundleId": "research-noisy",
            "confidence": "high",
            "authorityScore": 82,
            "finalExperiencePack": {
                "researchResult": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
                "sourceUrls": [{"title": "Noisy video page", "url": "https://www.youtube.com/watch?v=noise"}],
            },
        }
    )

    assert pack["answer"] == ""
    assert pack["score"]["qualityStatus"] == "refresh_required"
    assert "low_quality_answer_surface" in pack["missingOrStaleReasons"]
    assert pack["recommendedNextAction"] == "refresh_research"


def test_research_evidence_bank_rejects_noisy_sources(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 1, "maxRounds": 2}},
    )

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Security check required",
                        "url": "https://www.youtube.com/watch?v=noisy",
                        "snippet": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
                    }
                ],
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(
        research_module,
        "web_read",
        SimpleNamespace(
            func=lambda **kwargs: json.dumps(
                {
                    "ok": True,
                    "title": "YouTube footer",
                    "status": 200,
                    "text": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
                }
            )
        ),
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="low quality source gate",
            maxShards=1,
            maxRounds=2,
            state={"run_id": "run-noisy"},
        )
    )

    assert payload["researchAnswerPack"]["answer"] == ""
    assert payload["researchAnswerPack"]["score"]["qualityStatus"] == "refresh_required"
    assert payload["researchEvidenceBank"]["selectedSources"] == []
    assert payload["researchEvidenceBank"]["rejectedSources"]
    assert payload["rejectedSources"][0]["reason"]


def test_research_jina_reader_fallback_when_builtin_read_is_noisy(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina-test")
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 1, "maxRounds": 2}},
    )

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Official Jina-backed docs",
                        "url": "https://docs.example.com/jina",
                        "snippet": "Official docs snippet.",
                    }
                ],
            }
        )

    class FakeResponse:
        status_code = 200
        text = "Jina reader extracted the official documentation body with a stable source-backed implementation detail."

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(
        research_module,
        "web_read",
        SimpleNamespace(
            func=lambda **kwargs: json.dumps(
                {
                    "ok": True,
                    "title": "Noisy fallback",
                    "status": 200,
                    "text": "Security check required. We've detected unusual activity from your network.",
                }
            )
        ),
    )
    monkeypatch.setattr(research_module.requests, "get", lambda *args, **kwargs: FakeResponse())

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="Jina reader fallback path",
            allowedDomains=["docs.example.com"],
            maxShards=1,
            maxRounds=2,
            state={"run_id": "run-jina"},
        )
    )

    fetched = next(
        item
        for shard in payload["shards"]
        for item in shard.get("fetchedTopSources", [])
        if item.get("extractionQuality") == "jina_reader_markdown"
    )
    assert fetched["extractionQuality"] == "jina_reader_markdown"
    assert any(item.get("provider") == "jina" and item.get("status") == "success" for item in fetched["providerAttemptMatrix"])
    assert payload["researchEvidenceBank"]["selectedSources"]
    assert "Jina reader extracted" in payload["answer"]


def test_web_research_architect_agent_falls_back_across_model_candidates(monkeypatch):
    class BrokenLLM:
        def invoke(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("subscription expired")

    class GoodLLM:
        def invoke(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return AIMessage(
                content=json.dumps(
                    {
                        "headline": "fallback ok",
                        "researchResult": "最终答案：使用第二候选模型完成提纯。",
                        "claimTable": [{"claim": "第二候选模型可用", "sourceURL": "https://docs.example.com/fallback"}],
                        "conflictMatrix": [],
                        "missingEvidence": [],
                        "assumptions": [],
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(BrokenLLM(), "bad-model", "research"), (GoodLLM(), "good-model", "web-research-architect")],
    )

    result = research_module._invoke_web_research_architect_agent(
        question="fallback test",
        source_matrix=[{"title": "Fallback docs", "url": "https://docs.example.com/fallback", "snippet": "第二候选模型可用。"}],
        shards=[],
        confidence="medium",
        average_authority=50,
        timeout_seconds=10,
    )

    assert result is not None
    assert result["researchResult"].startswith("最终答案")
    assert result["_modelId"] == "good-model"
    assert result["_modelFallbackAttempts"]
    assert "bad-model" in result["_modelFallbackAttempts"][0]


def test_web_research_architect_merge_keeps_string_fields_whole():
    merged = research_module._merge_web_research_architect_agent_pack(
        {
            "sourceUrls": [{"title": "Docs", "url": "https://docs.example.com/a"}],
            "confidence": "medium",
            "conflictMatrix": [],
            "missingEvidence": [],
            "assumptions": [],
        },
        {
            "headline": "answer",
            "researchResult": "最终答案。",
            "claimTable": [{"claim": "结论来自文档。", "sourceURL": "https://docs.example.com/a"}],
            "conflictMatrix": "No conflicts found.",
            "missingEvidence": "No specific CLI-only guidance was found.",
            "assumptions": "General pathlib guidance applies to CLI tools.",
            "_modelRole": "web-research-architect",
            "_modelId": "deepseek::deepseek-v4-flash",
            "_modelParseMode": "json",
        },
        question="pathlib CLI",
    )

    assert merged["synthesisMode"] == "model_agent"
    assert merged["conflictMatrix"] == ["No conflicts found."]
    assert merged["missingEvidence"] == ["No specific CLI-only guidance was found."]
    assert merged["assumptions"] == ["General pathlib guidance applies to CLI tools."]


def test_research_broker_reuses_existing_experience_pack(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 1, "maxRounds": 2}},
    )
    search_calls = 0

    def fake_search(**kwargs):
        nonlocal search_calls
        search_calls += 1
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Official repeat topic docs",
                        "url": "https://docs.example.com/repeat",
                        "snippet": "Primary repeat source.",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "title": "Official repeat topic docs",
                "status": 200,
                "text": "Repeat topic has a stable source-backed conclusion. It is reusable for future matching questions.",
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    first = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="repeat topic experience reuse",
            maxShards=1,
            state={"run_id": "run-reuse"},
        )
    )
    second = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="repeat topic experience reuse",
            maxShards=1,
            state={"run_id": "run-reuse"},
        )
    )

    assert first["ok"] is True
    assert second["experienceReuse"]["reuseDecision"] == "reuse"
    assert second["researchLoopState"]["stopReason"] == "experience_reused"
    assert search_calls == 2


def test_research_broker_does_not_reuse_unrelated_pack(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 1, "maxRounds": 1}},
    )

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "OpenClaw Plugin SDK",
                        "url": "https://docs.example.com/openclaw",
                        "snippet": "OpenClaw plugin SDK source.",
                    }
                ],
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=lambda **kwargs: json.dumps({"ok": True, "text": "OpenClaw plugin SDK documentation."})))

    first = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="OpenClaw plugin SDK patterns",
            state={"run_id": "run-unrelated"},
        )
    )
    assert first["ok"] is True

    second = json.loads(
        research_module.research_broker.func(
            mode="search_experience",
            query="Python pathlib CLI best practices",
            state={"run_id": "run-unrelated"},
        )
    )
    assert second["items"] == []
    assert second["reuseDecision"]["reuseDecision"] == "ignore"
    assert second["reuseDecision"]["reason"] in {"no_matching_experience_pack", "no_topic_matched_reusable_candidate_after_filtering"}


def test_reuse_decision_ignores_generic_stopword_overlap():
    decision = research_module._experience_reuse_decision(
        [
            {
                "experiencePackId": "rxp-openclaw",
                "title": "Research the latest OpenClaw plugin SDK patterns",
                "query": "Research the latest OpenClaw plugin SDK patterns and API exports",
                "confidence": "high",
                "sourcePolicy": "authoritative",
            }
        ],
        question="What are the current best practices for using Python pathlib in CLI tools? cite official sources.",
        source_policy="authoritative",
        freshness="auto",
    )

    assert decision["reuseDecision"] == "ignore"


def test_research_architect_jsonish_field_extraction():
    raw = (
        '{\n'
        '    "headline": "提纯标题",\n'
        '    "researchResult": "第一行结论\n第二行结论",\n'
        '    "claimTable": []\n'
        '}'
    )

    assert research_module._extract_jsonish_string_field(raw, "headline") == "提纯标题"
    assert "第二行结论" in research_module._extract_jsonish_string_field(raw, "researchResult")


def test_research_broker_refines_when_sources_are_not_readable(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 2, "maxRounds": 2}},
    )
    queries: list[str] = []

    def fake_search(**kwargs):
        query = kwargs["query"]
        queries.append(query)
        suffix = "primary" if "official documentation primary source" in query else "baseline"
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": f"{suffix} source",
                        "url": f"https://docs.example.com/{suffix}",
                        "snippet": f"{suffix} snippet",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        if "baseline" in kwargs["url"]:
            return json.dumps({"ok": False, "title": "blocked", "status": 403, "text": ""})
        return json.dumps({"ok": True, "title": "primary source", "status": 200, "text": "Primary source body with useful claim."})

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="refinement source gap",
            maxShards=1,
            maxRounds=2,
            state={"run_id": "run-refine"},
        )
    )

    assert len(payload["researchLoopState"]["rounds"]) == 2
    assert any("official documentation primary source" in query for query in queries)


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
