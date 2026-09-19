from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import core.tools.research_broker as research_module
from core.agents import default_subagent_configs
from core.runtime_tool_access import RUNTIME_TOOL_GROUPS, filter_visible_tools_for_actor
from core.tools.research_quality import (
    MIN_RESEARCH_CLAIM_COUNT,
    MIN_RESEARCH_SOURCE_COUNT,
    TARGET_RESEARCH_ANSWER_CHARS,
    TARGET_RESEARCH_DATED_SOURCE_COUNT,
    TARGET_RESEARCH_DISTINCT_HOST_COUNT,
    TARGET_RESEARCH_SOURCE_COUNT,
    build_research_review_binding,
    research_acceptance_metrics,
    research_bundle_is_high_quality,
    research_source_has_dated_evidence,
)
from runtimes.research.evidence import normalize_citation_tokens


@pytest.fixture(autouse=True)
def _isolated_research_ledger(monkeypatch, tmp_path, request):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))


class _ToolRef:
    def __init__(self, name: str):
        self.name = name


def _script_agent(monkeypatch, actions, reviews=()):
    from tests.core.test_research_agent import ScriptedTransport
    transport = ScriptedTransport(actions, reviews)

    class Model:
        _meta = {"model_ref": "fixture-model", "global_max_tokens": 4096}

        def __init__(self, reviewer):
            self.reviewer = reviewer

        def bind_tools(self, tools, **kwargs):
            self.tools = tools
            assert kwargs.get("tool_choice") == "required"
            return self

        def invoke(self, messages, **kwargs):
            return transport(messages, self.tools, reviewer=self.reviewer, seconds=kwargs["timeout"])

    monkeypatch.setattr(research_module, "_create_web_research_architect_llm_candidates", lambda: [(Model(False), "fixture-writer", "web-research-architect")])
    monkeypatch.setattr(research_module, "_create_web_research_reviewer_llm_candidates", lambda _: [(Model(True), "fixture-reviewer", "agent_reviewer:verification-engineer")])
    return transport


def _search_then_no_answer(monkeypatch, query):
    from tests.core.test_research_agent import call
    return _script_agent(monkeypatch, [
        call("search_research_sources", queries=[query]),
        call("submit_research_answer", answer="The available pages do not answer the question.", coverage="none", limitations=["No supported answer."]),
    ])


def _high_quality_answer(source_count: int = TARGET_RESEARCH_SOURCE_COUNT) -> str:
    citations = " ".join(f"[S{index}]" for index in range(1, source_count + 1))
    subjects = ("定义", "架构", "来源", "数据", "时效", "冲突", "限制", "风险", "案例", "决策")
    aspects = ("事实基础", "证据一致性", "适用条件", "版本变化", "反例检验", "因果边界", "执行影响", "验证办法")
    paragraphs = [
        (
            f"围绕{subject}的{aspect}，材料给出了可核验事实，并区分原始记录、来源解释和综合判断。"
            f"本节具体说明{subject}受哪些{aspect}条件约束、何种新证据会改变结论、相反说法为何成立或不成立，以及用户据此应采取的下一步。"
        )
        for subject in subjects
        for aspect in aspects
    ]
    citation_keys = citations.split()
    paragraphs = [
        f"{paragraph.rstrip('。.')} {citation_keys[index % len(citation_keys)]}。"
        for index, paragraph in enumerate(paragraphs)
    ]
    return f"结论：这些来源共同回答了用户问题，并给出事实、差异、边界与时效判断。{citations}\n\n" + "\n\n".join(paragraphs)


def _test_source_body(source: dict, index: int) -> str:
    return str(
        source.get("text")
        or f"Source {index} records a concrete research fact, the condition under which it applies, and its evidence boundary. " * 20
    )


def _test_source_text_map(sources: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, source in enumerate(sources, start=1):
        body = _test_source_body(source, index)
        for alias in (source.get("url"), source.get("sourceId"), source.get("citationKey") or f"S{index}"):
            if alias:
                result[str(alias).strip("[]")] = body
                result[str(alias)] = body
    return result



def _unique_search_result_batch(call_index: int, query: str = "") -> list[dict]:
    authoritative_hosts = (
        "platform.openai.com",
        "docs.anthropic.com",
        "ai.google.dev",
        "learn.microsoft.com",
        "docs.aws.amazon.com",
        "docs.nvidia.com",
        "cloud.google.com",
        "docs.python.org",
        "developer.mozilla.org",
        "go.dev",
    )
    return [
        {
            "title": f"Research source {call_index}-{offset}",
            "url": (
                f"https://{authoritative_hosts[((call_index - 1) * 2 + offset - 1) % len(authoritative_hosts)]}"
                f"/research/{call_index}-{offset}"
            ),
            "snippet": (
                f"{query} primary source analysis limitations"
                if query
                else "research runtime evidence contract primary source analysis limitations"
            ),
            "publishedAt": f"2026-07-{10 + call_index + offset:02d}T00:00:00Z",
        }
        for offset in (1, 2)
    ]


def test_research_broker_is_runtime_granted_tool():
    assert "research.core" in RUNTIME_TOOL_GROUPS
    assert RUNTIME_TOOL_GROUPS["research.core"]["toolNames"] == ["research_broker"]

    tools = [_ToolRef("runtime_broker"), _ToolRef("research_broker")]
    visible = filter_visible_tools_for_actor(tools, actor="supervisor")
    assert [tool.name for tool in visible] == ["runtime_broker", "research_broker"]
    assert "run" not in visible[1].tool_call_schema.model_json_schema()["properties"]["mode"]["enum"]

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
    assert "evidence-based research" in research_architect.system_prompt
    assert "Research Runtime" not in research_architect.system_prompt
    assert "Query-plan stage" not in research_architect.system_prompt
    assert "hard rejection floor" not in research_architect.system_prompt
    assert "3000" not in research_architect.system_prompt
    assert "5000" not in research_architect.system_prompt



def test_cli_relevance_uses_word_boundaries_and_workflow_aliases():
    question = "What are the current best practices for Python pathlib in CLI tools?"

    assert research_module._source_relevance_score(
        question,
        title="Using pathlib.Path with argparse command-line arguments",
    ) == 100
    assert research_module._source_relevance_score(
        question,
        title="Click changes",
    ) == 50
    assert research_module._source_relevance_score(
        question,
        title="Entry points specification for console scripts",
    ) == 50
    assert research_module._source_relevance_score(
        question,
        title="zipfile archive format",
    ) == 0
    assert research_module._source_relevance_score(
        "pathlib",
        text="Objects implementing os.PathLike represent a file system path through __fspath__.",
    ) == 100


def test_pathlib_cli_question_adds_direct_parser_and_path_focused_framework_facets():
    facets = research_module._build_question_facet_queries(
        "What are the current best practices for using Python pathlib in CLI tools? cite official sources."
    )

    assert [kind for _query, kind in facets] == [
        "facet_cli_parser",
        "facet_cli_framework",
        "facet_cli_typer",
        "facet_pathlib_api",
    ]
    assert all("pathlib" in query.lower() or "path" in query.lower() for query, _kind in facets)
    assert "argparse" in facets[0][0]
    assert "click.Path" in facets[1][0]
    assert "typer.tiangolo.com" in facets[2][0]
    assert "docs.python.org/3/library/pathlib.html" in facets[3][0]


def test_deterministic_facet_search_fallback_removes_task_verbs_and_citation_tail():
    query = research_module._deterministic_facet_search_query(
        "Verify, as of 2026-07-29, the exact application dates for GPAI providers under "
        "Regulation (EU) 2024/1689 (AI Act) Articles 113 and 51-55, including the transition "
        "milestones. Cite OJ text, Commission Q&A, and AI Office pages."
    )

    assert not query.lower().startswith("verify")
    assert "cite oj" not in query.lower()
    assert "GPAI" in query
    assert "2024/1689" in query
    assert len(query) <= 180


def test_research_search_query_normalization_removes_chinese_task_verbs_and_drops_combined_brief():
    assert research_module._normalize_research_search_query(
        "梳理 GPAI 透明度义务与行业实践差距"
    ) == "GPAI 透明度义务与行业实践差距"
    assert research_module._normalize_research_search_query(
        "Research every item below as one evidence bundle. "
        "1. [timeline] Verify the dates. 2. [penalties] Verify the penalty tiers."
    ) == ""



def test_source_temporal_evidence_extracts_labeled_and_url_dates():
    labeled = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "A practical pathlib guide",
            "text": "Last updated: January 11, 2025. The guide targets Python 3.13.",
        },
        {"url": "https://realpython.com/python-pathlib/"},
    )
    url_dated = research_module._source_temporal_evidence(
        {"ok": True, "title": "Pathlib operational note", "text": "A versioned operational note."},
        {"url": "https://oneuptime.com/blog/post/2026-01-27-use-pathlib-for-file-paths-python/view"},
    )

    assert labeled["updatedAt"] == "2025-01-11"
    assert "version" not in labeled
    assert labeled["applicableVersion"] == "3.13"
    assert url_dated["publishedAt"] == "2026-01-27"


def test_source_temporal_evidence_supports_day_first_full_month_labels():
    updated = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "GPAI Code of Practice",
            "text": "Last update:\u00a010 July 2025. Official publication record.",
        },
        {"url": "https://digital-strategy.ec.europa.eu/gpai-code"},
    )
    published = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "GPAI guidance",
            "text": "Published 10 July 2025. Official guidance.",
        },
        {"url": "https://digital-strategy.ec.europa.eu/gpai-guidance"},
    )

    assert updated["updatedAt"] == "2025-07-10"
    assert "publishedAt" not in updated
    assert published["publishedAt"] == "2025-07-10"


def test_source_temporal_evidence_does_not_treat_historical_body_version_as_current():
    historical = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "Using pathlib in applications",
            "text": "pathlib was introduced in Python 3.4. This article discusses current usage.",
        },
        {"url": "https://example.com/pathlib-guide"},
    )
    title_wins = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "Parameter Types - Click Documentation (8.5.x)",
            "text": "A navigation fragment mentions version 8.4.0 elsewhere in the page.",
        },
        {"url": "https://click.palletsprojects.com/en/stable/parameter-types"},
    )

    assert "version" not in historical
    assert title_wins["version"] == "8.5.x"


def test_source_temporal_evidence_does_not_promote_reader_fetch_time_to_publication_date():
    temporal = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "Pathlib operational guidance",
            "text": "Current pathlib operational guidance with no publication metadata.",
            "metadata": {
                "readerPublishedTime": "Fri, 17 Jul 2026 20:39:03 GMT",
                "last-modified": "Fri, 17 Jul 2026 20:39:03 GMT",
            },
            "retrievedAt": "2026-07-29T12:00:00Z",
        },
        {"url": "https://example.com/pathlib-guidance"},
    )

    assert temporal == {"retrievedAt": "2026-07-29T12:00:00Z"}


def test_source_applicable_version_does_not_count_as_document_temporal_evidence():
    temporal = research_module._source_temporal_evidence(
        {
            "ok": True,
            "title": "Pathlib compatibility guide",
            "text": "This command supports Python 3.8 and later.",
            "retrievedAt": "2026-07-29T12:00:00Z",
        },
        {"url": "https://example.com/pathlib-compatibility"},
    )

    assert temporal["applicableVersion"] == "3.8"
    assert "version" not in temporal
    assert research_source_has_dated_evidence({"temporalEvidence": temporal}) is False


def test_parallel_search_shards_read_duplicate_url_only_once(monkeypatch):
    read_urls: list[str] = []

    def fake_search(**kwargs):
        query_slug = "one" if "one" in kwargs["query"] else "two"
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Shared Python CLI documentation",
                        "url": "https://docs.python.org/shared-pathlib-cli",
                        "snippet": f"Python pathlib CLI {query_slug} documentation",
                    },
                    {
                        "title": f"Unique {query_slug} Python CLI documentation A",
                        "url": f"https://docs.python.org/{query_slug}-pathlib-cli-a",
                        "snippet": "Python pathlib CLI documentation",
                    },
                    {
                        "title": f"Unique {query_slug} Python CLI documentation B",
                        "url": f"https://docs.python.org/{query_slug}-pathlib-cli-b",
                        "snippet": "Python pathlib CLI documentation",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps({"ok": True, "status": 200, "text": "Python pathlib CLI documentation evidence. " * 20})

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    ledger = research_module._ResearchReadAttemptLedger(
        question="Python pathlib CLI evidence"
    )

    completed = research_module._run_search_shards(
        [
            {"shardId": "one", "kind": "baseline", "query": "Python pathlib CLI one"},
            {"shardId": "two", "kind": "baseline", "query": "Python pathlib CLI two"},
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="parallel-dedupe-test",
        read_attempt_ledger=ledger,
    )

    assert len(completed) == 2
    assert read_urls.count("https://docs.python.org/shared-pathlib-cli") == 1
    assert len(set(read_urls)) >= 2
    assert all(shard.get("fetchedTopSources") for shard in completed)


def test_parallel_search_shards_try_distinct_candidate_while_shared_read_is_inflight(monkeypatch):
    shared_started = threading.Event()
    alternate_started = threading.Event()

    def fake_search(**kwargs):
        slug = "one" if "one" in kwargs["query"] else "two"
        return json.dumps(
            {
                "ok": True,
                "provider": "fake",
                "results": [
                    {
                        "title": "Python pathlib one two shared evidence",
                        "url": "https://docs.python.org/shared-pathlib",
                        "snippet": "Python pathlib one two evidence",
                    },
                    {
                        "title": f"Distinct fallback {slug}",
                        "url": f"https://docs.python.org/{slug}-pathlib",
                        "snippet": "Read the documentation.",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        if kwargs["url"].endswith("shared-pathlib"):
            shared_started.set()
            assert alternate_started.wait(timeout=1.0)
        else:
            assert shared_started.wait(timeout=1.0)
            alternate_started.set()
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "text": "Python pathlib evidence for current command line behavior. " * 20,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shards(
        [
            {"shardId": "one", "kind": "baseline", "query": "Python pathlib one"},
            {"shardId": "two", "kind": "baseline", "query": "Python pathlib two"},
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="parallel-inflight-alternative",
        read_attempt_ledger=research_module._ResearchReadAttemptLedger(
            question="Python pathlib evidence"
        ),
    )

    assert len(completed) == 2
    assert alternate_started.is_set()
    assert all(shard.get("fetchedTopSources") for shard in completed)


def test_search_snippet_does_not_block_relevant_readable_body(monkeypatch):
    read_urls: list[str] = []

    monkeypatch.setattr(
        research_module,
        "web_search",
        SimpleNamespace(
            func=lambda **_kwargs: json.dumps(
                {
                    "ok": True,
                    "provider": "fake",
                    "results": [
                        {
                            "title": "Overview",
                            "url": "https://docs.python.org/3/library/pathlib.html",
                            "snippet": "Read the documentation.",
                        }
                    ],
                }
            )
        ),
    )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "title": "pathlib command line behavior",
                "text": "Python pathlib command line behavior and current API evidence. " * 24,
            }
        )

    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shards(
        [{"shardId": "body-first", "kind": "baseline", "query": "Python pathlib command line evidence"}],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="body-first-gate",
    )

    assert read_urls == ["https://docs.python.org/3/library/pathlib.html"]
    assert research_module._research_read_observations(completed)[0]["text"]


def test_parallel_search_shards_reports_human_safe_search_and_read_progress(monkeypatch):
    def fake_run_search_shard(shard, **_kwargs):
        return {
            **shard,
            "ok": True,
            "fetchedTopSources": [
                {
                    "ok": True,
                    "title": "权威指南",
                    "url": "https://authority.example/guide?private=1",
                    "text": "可读正文",
                }
            ],
        }

    monkeypatch.setattr(research_module, "_run_search_shard", fake_run_search_shard)
    progress: list[dict] = []

    with research_module.bind_research_progress_reporter(progress.append):
        completed = research_module._run_search_shards(
            [{"shardId": "shard-1", "query": "权威膳食指南"}],
            allowed_domains=[],
            blocked_domains=[],
            source_policy="authoritative",
            max_rounds=1,
            use_agent_browser_profile=False,
            tool_call_id="call-1",
        )

    assert len(completed) == 1
    assert [item["stage"] for item in progress] == ["source_search", "source_read", "source_search"]
    assert progress[0]["summary"] == "正在搜索：权威膳食指南"
    assert progress[1]["summary"] == "已读取 权威指南"
    assert progress[0]["nodeId"] == progress[-1]["nodeId"]
    assert progress[0]["status"] == "active"
    assert progress[-1]["status"] == "completed"
    assert "private=1" not in str(progress)


def test_parallel_search_shards_are_bounded_without_becoming_serial(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_run_search_shard(shard, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return {
            **shard,
            "ok": True,
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": [],
        }

    monkeypatch.setattr(research_module, "_run_search_shard", fake_run_search_shard)
    shards = [
        {"shardId": f"bounded-{index}", "kind": "baseline", "query": f"query {index}"}
        for index in range(18)
    ]

    completed = research_module._run_search_shards(
        shards,
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="bounded-parallel-search-test",
    )

    assert len(completed) == len(shards)
    assert 1 < peak <= research_module._RESEARCH_MAX_PARALLEL_SEARCH_SHARDS


def test_source_router_finishes_before_the_research_shard_envelope():
    assert research_module._RESEARCH_SEARCH_DEADLINE_MS < research_module._RESEARCH_SHARD_DEADLINE_MS
    assert research_module._RESEARCH_SOURCE_READ_DEADLINE_MS < research_module._RESEARCH_SHARD_DEADLINE_MS


def test_research_shard_reuses_allowlisted_browser_profile_for_cn_routing(monkeypatch):
    search_calls: list[dict] = []
    read_calls: list[dict] = []

    def fake_search(**kwargs):
        search_calls.append(dict(kwargs))
        return json.dumps(
            {
                "ok": True,
                "provider": "bing_cn",
                "networkRoute": "cn_direct",
                "results": [
                    {
                        "title": "Python pathlib documentation",
                        "url": "https://docs.python.org/3/library/pathlib.html",
                        "snippet": "Official pathlib API documentation for Path.resolve.",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        read_calls.append(dict(kwargs))
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "pathlib",
                "text": "Path.resolve resolves a path and handles symlinks according to strict mode. " * 20,
            }
        )

    monkeypatch.setattr(research_module, "source_router_search", fake_search)
    monkeypatch.setattr(research_module, "source_router_read", fake_read)

    completed = research_module._run_search_shard(
        {
            "shardId": "bounded-cn-route",
            "kind": "facet:pathlib",
            "query": "Python pathlib Path.resolve strict symlink behavior",
            "sourceIntent": "official_primary",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=True,
        tool_call_id="bounded-cn-route-test",
        preferred_language="zh-CN",
    )

    assert completed["provider"] == "bing_cn"
    assert search_calls[0]["mode"] == "auto"
    assert search_calls[0]["locale_hint"] == "zh-CN"
    assert search_calls[0]["allow_browser_profile_fallback"] is True
    # Search keeps profile fallback enabled but does not force every provider
    # (and every later public result URL) through the login-backed browser.
    assert search_calls[0]["useAgentBrowserProfile"] is False
    assert read_calls[0]["mode"] == "auto"
    # Result URLs are not necessarily on the login allowlist.  The fetcher
    # auto-promotes matching hosts while keeping public sources readable.
    assert read_calls[0]["useAgentBrowserProfile"] is False
    assert 0 < read_calls[0]["timeout_seconds"] <= 8


def test_research_shard_keeps_public_static_route_when_browser_profile_is_disabled(monkeypatch):
    search_calls: list[dict] = []

    def fake_search(**kwargs):
        search_calls.append(dict(kwargs))
        return json.dumps({"ok": True, "provider": "bing_cn", "results": []})

    monkeypatch.setattr(research_module, "source_router_search", fake_search)

    research_module._run_search_shard(
        {"shardId": "public-cn", "kind": "baseline", "query": "国内公开资料"},
        allowed_domains=[],
        blocked_domains=[],
        source_policy="mixed",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="public-cn-route-test",
        preferred_language="zh-CN",
    )

    assert search_calls[0]["mode"] == "static"
    assert search_calls[0]["allow_browser_profile_fallback"] is False
    assert search_calls[0]["useAgentBrowserProfile"] is False


def test_research_plan_inherits_governed_browser_profile_when_tool_flag_is_omitted(monkeypatch):
    monkeypatch.setattr(
        research_module,
        "get_web_fetch_config",
        lambda: {
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn", "baidu.com"],
        },
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="plan",
            question="核对国内公开资料",
            maxShards=1,
            maxRounds=1,
            tool_call_id="configured-profile-plan",
        )
    )

    assert payload["shardDefaults"]["useAgentBrowserProfile"] is True
    assert payload["shardDefaults"]["agentBrowserProfileSource"] == "system_base"


def test_research_plan_keeps_browser_profile_disabled_without_governed_allowlist(monkeypatch):
    monkeypatch.setattr(
        research_module,
        "get_web_fetch_config",
        lambda: {
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": [],
        },
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="plan",
            question="核对国内公开资料",
            maxShards=1,
            maxRounds=1,
            tool_call_id="missing-allowlist-plan",
        )
    )

    assert payload["shardDefaults"]["useAgentBrowserProfile"] is False
    assert payload["shardDefaults"]["agentBrowserProfileSource"] == "disabled"



def test_parallel_search_shards_do_not_repeat_a_transient_failure_in_same_round(
    monkeypatch,
):
    url = "https://docs.python.org/shared-transient-source"
    read_urls: list[str] = []

    def fake_search(**_kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Shared transient Python source",
                        "url": url,
                        "snippet": "Python pathlib CLI official documentation evidence",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": False,
                "failureClass": "network_timeout",
                "error": "TLS connection timed out",
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    monkeypatch.setattr(research_module, "_jina_api_key", lambda: "")

    research_module._run_search_shards(
        [
            {"shardId": "one", "kind": "baseline", "query": "Python pathlib CLI one"},
            {"shardId": "two", "kind": "baseline", "query": "Python pathlib CLI two"},
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="same-round-transient-dedupe",
    )

    assert read_urls == [url]


def test_transient_read_retries_once_in_a_later_round_and_then_stops(monkeypatch):
    url = "https://docs.python.org/transient-retry-source"
    read_urls: list[str] = []

    def fake_search(**_kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Transient retry Python source",
                        "url": url,
                        "snippet": "Python pathlib CLI official documentation evidence",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": False,
                "status": 503,
                "failureClass": "service_unavailable",
                "error": "temporary upstream failure",
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    monkeypatch.setattr(research_module, "_jina_api_key", lambda: "")
    ledger = research_module._ResearchReadAttemptLedger(
        question="Python pathlib CLI evidence"
    )
    shard = {
        "shardId": "retry",
        "kind": "baseline",
        "query": "Python pathlib CLI evidence",
    }

    for round_index in (1, 2, 3):
        research_module._run_search_shards(
            [shard],
            allowed_domains=[],
            blocked_domains=[],
            source_policy="authoritative",
            max_rounds=3,
            use_agent_browser_profile=False,
            tool_call_id=f"transient-retry-{round_index}",
            read_attempt_ledger=ledger,
            read_round=round_index,
        )

    assert read_urls == [url, url]
    assert ledger.snapshot()["networkAttemptCount"] == 2
    assert ledger.snapshot()["terminalIdentityCount"] == 1


def test_retryable_host_failures_open_per_run_circuit_without_blocking_other_hosts(monkeypatch):
    failing_urls = [
        "https://docs.python.org/failure-one",
        "https://docs.python.org/failure-two",
        "https://docs.python.org/failure-three",
    ]
    success_url = "https://docs.pydantic.dev/latest/success"
    urls_by_query = {
        f"Python pathlib CLI official documentation evidence {index}": url
        for index, url in enumerate([*failing_urls, success_url], start=1)
    }
    read_urls: list[str] = []

    def fake_search(**kwargs):
        query = kwargs["query"]
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": query,
                        "url": urls_by_query[query],
                        "snippet": query,
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        url = kwargs["url"]
        read_urls.append(url)
        if "docs.python.org" in url:
            return json.dumps(
                {
                    "ok": False,
                    "status": 503,
                    "failureClass": "service_unavailable",
                    "error": "temporary upstream failure",
                }
            )
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": url,
                "title": "Pydantic official documentation",
                "text": "Python pathlib CLI official documentation evidence. " * 20,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    monkeypatch.setattr(research_module, "_jina_api_key", lambda: "")
    ledger = research_module._ResearchReadAttemptLedger(
        question="Python pathlib CLI official documentation evidence"
    )
    completed = []
    for index, query in enumerate(urls_by_query, start=1):
        completed.append(
            research_module._run_search_shards(
                [{"shardId": f"host-{index}", "kind": "baseline", "query": query}],
                allowed_domains=[],
                blocked_domains=[],
                source_policy="authoritative",
                max_rounds=4,
                use_agent_browser_profile=False,
                tool_call_id=f"host-circuit-{index}",
                read_attempt_ledger=ledger,
                read_round=index,
            )[0]
        )

    assert read_urls == [failing_urls[0], failing_urls[1], success_url]
    assert completed[2]["sourceHostCircuitOpen"] == ["docs.python.org"]
    assert completed[3]["fetchedTopSources"][0]["ok"] is True
    assert ledger.snapshot()["openHostCircuitCount"] == 1
    assert ledger.snapshot()["hostCircuitSkipCount"] == 1


def test_seed_and_search_result_share_the_same_read_attempt_identity(monkeypatch):
    url = "https://docs.python.org/shared-seed-source"
    read_urls: list[str] = []

    def fake_search(**_kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Shared seed Python source",
                        "url": url,
                        "snippet": "Python pathlib CLI official documentation evidence",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": url,
                "title": "Shared seed Python source",
                "text": "Python pathlib CLI official documentation evidence. " * 20,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    research_module._run_search_shards(
        [
            {
                "shardId": "seed",
                "kind": "seed_url",
                "query": url,
                "seedUrl": url,
                "evidenceQuery": "Python pathlib CLI evidence",
            },
            {
                "shardId": "search",
                "kind": "baseline",
                "query": "Python pathlib CLI evidence",
            },
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="seed-search-shared-identity",
    )

    assert read_urls == [url]


def test_successful_redirect_registers_final_url_alias_across_rounds(monkeypatch):
    requested_url = "https://example.com/official-short-link"
    final_url = "https://docs.python.org/final-official-source"
    current_url = requested_url
    read_urls: list[str] = []

    def fake_search(**_kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Redirected official source",
                        "url": current_url,
                        "snippet": "Python pathlib CLI official documentation evidence",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": final_url,
                "title": "Redirected official source",
                "text": "Python pathlib CLI official documentation evidence. " * 20,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    ledger = research_module._ResearchReadAttemptLedger(
        question="Python pathlib CLI evidence"
    )
    shard = {
        "shardId": "redirect",
        "kind": "baseline",
        "query": "Python pathlib CLI evidence",
    }

    research_module._run_search_shards(
        [shard],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="redirect-round-1",
        read_attempt_ledger=ledger,
        read_round=1,
    )
    current_url = final_url
    research_module._run_search_shards(
        [shard],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="redirect-round-2",
        read_attempt_ledger=ledger,
        read_round=2,
    )

    assert read_urls == [requested_url]


def test_search_shard_preserves_source_router_failure_diagnostics(monkeypatch):
    def fake_search(**_kwargs):
        return json.dumps(
            {
                "ok": False,
                "failureClass": "search_failed",
                "retryable": True,
                "elapsedMs": 4321,
                "error": "tavily_http_status_432",
                "providerAttemptMatrix": [
                    {
                        "provider": "tavily",
                        "status": "error",
                        "failureClass": "provider_http_error",
                    }
                ],
                "sourceRouter": {"selectedProvider": None},
                "results": [],
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))

    completed = research_module._run_search_shard(
        {
            "shardId": "failed-discovery",
            "kind": "facet:timeline",
            "query": "EU AI Act GPAI compliance timeline",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="multi_source_evidence",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="failed-discovery-test",
    )

    assert completed["ok"] is False
    assert completed["failureClass"] == "search_failed"
    assert completed["retryable"] is True
    assert completed["elapsedMs"] == 4321
    assert completed["sourceRouter"]["selectedProvider"] is None
    assert completed["providerAttemptMatrix"][0]["provider"] == "tavily"
    assert completed["errors"] == ["tavily_http_status_432"]


def test_search_shard_enforces_site_operator_before_read(monkeypatch):
    read_urls: list[str] = []

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "AI Act Article 51 systemic risk unofficial copy",
                        "url": "https://example.com/ai-act-article-51",
                        "snippet": "AI Act Article 51 systemic risk 10^25 FLOPs threshold.",
                    },
                    {
                        "title": "Regulation (EU) 2024/1689 Article 51",
                        "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
                        "snippet": "Official AI Act Article 51 systemic risk classification and threshold.",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "Official AI Act",
                "text": "Article 51 classifies general-purpose AI models with systemic risk and records the 10^25 FLOPs threshold. " * 12,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shard(
        {
            "shardId": "eu-site-filter",
            "kind": "facet:systemic-risk",
            "query": "site:europa.eu AI Act Article 51 systemic risk 10^25 FLOPs",
            "sourceIntent": "official_primary",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="multi_source_evidence",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="site-filter-test",
    )

    assert read_urls == ["https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng"]
    assert completed["siteDomains"] == ["europa.eu"]
    assert research_module._research_read_observations([completed])[0]["text"]


def test_bing_cn_site_query_can_read_a_marked_domestic_mirror_after_official_timeout(monkeypatch):
    read_urls: list[str] = []

    def fake_search(**_kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "bing_cn",
                "networkRoute": "cn_direct",
                "results": [
                    {
                        "title": "LangChain v1 official reference",
                        "url": "https://docs.langchain.com/oss/python/releases/langchain-v1",
                        "snippet": "LangChain v1 current capabilities and migration changes.",
                    },
                    {
                        "title": "LangChain v1 中文镜像",
                        "url": "https://langchain-doc.cn/releases/langchain-v1",
                        "snippet": "LangChain v1 当前能力、迁移路径和兼容性变化。",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        if "docs.langchain.com" in kwargs["url"]:
            return json.dumps(
                {
                    "ok": False,
                    "failureClass": "network_timeout",
                    "error": "international route unavailable",
                }
            )
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "LangChain v1 中文镜像",
                "text": "LangChain v1 当前能力包括 Agent、工具调用、中间件和迁移兼容说明。 " * 24,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shard(
        {
            "shardId": "domestic-site-fallback",
            "kind": "facet:capabilities",
            "query": "site:docs.langchain.com LangChain v1 current capabilities",
            "sourceIntent": "official_primary",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="domestic-site-fallback-test",
    )

    assert read_urls == [
        "https://docs.langchain.com/oss/python/releases/langchain-v1",
        "https://langchain-doc.cn/releases/langchain-v1",
    ]
    mirror = next(item for item in completed["results"] if item["url"].startswith("https://langchain-doc.cn"))
    assert mirror["siteConstraintRelaxed"] is True
    assert mirror["sourceIntent"] == "mixed"
    fetched_mirror = next(
        item for item in completed["fetchedTopSources"] if "langchain-doc.cn" in item["finalUrl"]
    )
    assert fetched_mirror["ok"] and fetched_mirror["text"]
    assert "domestic_mirror_for_unreachable_site_constraint" in mirror["sourceQualityHints"]["reasons"]


def test_source_noise_gate_does_not_reject_an_ordinary_cloudflare_mention():
    ordinary = (
        "LangChain is used by engineering teams at Replit, Cloudflare, Workday, and other companies. "
        "This page documents agents, tools, middleware, and migration behavior. "
    ) * 12
    challenge = "Performance & Security by Cloudflare. Cloudflare Ray ID: abc123."

    assert research_module._source_noise_reasons(ordinary) == []
    assert "noise:cloudflare ray id" in research_module._source_noise_reasons(challenge)


def test_search_shard_reads_explicit_official_site_before_applying_evidence_gate(monkeypatch):
    read_urls: list[str] = []

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "LangChain v1 release notes",
                        "url": "https://docs.langchain.com/oss/python/releases/langchain-v1",
                        "snippet": "Official LangChain v1 release notes and migration changes.",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "LangChain v1 release notes",
                "text": (
                    "LangChain v1 release notes describe the current package, migration path, "
                    "agent APIs, middleware, and compatibility changes. " * 18
                ),
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shard(
        {
            "shardId": "langchain-official-site",
            "kind": "facet:release-changes",
            "query": "site:docs.langchain.com LangChain v1 release notes migration changes",
            "sourceIntent": "official_primary",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="multi_source_evidence",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="langchain-official-site-test",
    )

    assert read_urls == ["https://docs.langchain.com/oss/python/releases/langchain-v1"]
    fetched = completed["fetchedTopSources"][0]
    assert "first_party_domain_subject_match" in completed["results"][0]["sourceQualityHints"]["reasons"]
    assert fetched["ok"] and fetched["text"]


def test_research_catalog_rewrites_retired_langchain_host_and_exposes_current_hint():
    assert research_module._normalize_research_search_query(
        "site:python.langchain.com LangChain v1 release notes"
    ).startswith("site:docs.langchain.com ")

    hints = research_module._catalog_official_host_hints(
        "调研 LangChain 最新版本的能力范围"
    )
    assert any(item["host"] == "docs.langchain.com" for item in hints)



def test_site_operator_alone_does_not_promote_an_unrelated_domain_to_primary():
    quality = research_module._source_quality(
        "https://docs.example.net/langchain-v1",
        allowed_domains=[],
        source_policy="multi_source_evidence",
        title="LangChain v1 notes",
        snippet="LangChain release changes",
        question="site:docs.example.net LangChain v1 release changes",
    )

    assert "first_party_domain_subject_match" not in quality["reasons"]
    assert quality["authorityTier"] is None
    assert research_module._source_matches_intent(quality, "official_primary") is False


def test_search_shard_official_primary_skips_secondary_sources(monkeypatch):
    read_urls: list[str] = []

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Article 51 practical analysis",
                        "url": "https://artificialintelligenceact.eu/article/51/",
                        "snippet": "Article 51 systemic risk threshold and Commission designation analysis.",
                    },
                    {
                        "title": "Regulation (EU) 2024/1689 Article 51",
                        "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
                        "snippet": "Article 51 systemic risk threshold and Commission designation official text.",
                    },
                ],
            }
        )

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "Official AI Act",
                "text": "Article 51 systemic risk threshold and Commission designation procedure. " * 16,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shard(
        {
            "shardId": "official-primary-filter",
            "kind": "facet:systemic-risk",
            "query": "AI Act Article 51 systemic risk threshold Commission designation",
            "sourceIntent": "official_primary",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="multi_source_evidence",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="official-primary-filter-test",
    )

    assert read_urls == ["https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng"]
    assert research_module._research_read_observations([completed])[0]["text"]


def test_search_batch_deadline_does_not_wait_for_running_shard_cleanup(monkeypatch):
    worker_started = threading.Event()
    worker_released = threading.Event()

    def slow_shard(shard, **kwargs):
        cancel_event = kwargs["cancel_event"]
        worker_started.set()
        assert cancel_event.wait(timeout=1.0)
        time.sleep(0.35)
        worker_released.set()
        return {
            **shard,
            "ok": False,
            "provider": None,
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["research_shard_cancelled"],
        }

    monkeypatch.setattr(research_module, "_run_search_shard", slow_shard)
    monkeypatch.setattr(research_module, "_RESEARCH_TOOL_DEADLINE_MS", 30)

    started_at = time.perf_counter()
    completed = research_module._run_search_shards(
        [{"shardId": "slow", "kind": "baseline", "query": "slow research shard"}],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="balanced",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="search-batch-timeout-test",
    )
    elapsed = time.perf_counter() - started_at

    assert worker_started.is_set()
    assert elapsed < 0.2
    assert completed[0]["errors"] == ["research_shard_deadline_exceeded"]
    assert worker_released.wait(timeout=1.0)


def test_search_shard_selects_query_focused_excerpt_after_twenty_thousand_chars(monkeypatch):
    relevant_paragraph = (
        "Path.resolve with strict=False reports symlink loops consistently in Python 3.13, "
        "which is the behavior this research question needs to verify."
    )
    irrelevant_prefix = (
        "Generic deployment schedule and unrelated meeting notes without filesystem details.\n" * 320
    )
    body = (
        f"{irrelevant_prefix}\n\n{relevant_paragraph}\n\n"
        + ("Unrelated appendix inventory and acknowledgements.\n" * 100).rstrip()
    )
    assert body.index(relevant_paragraph) > 20_000

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Path.resolve strict behavior",
                        "url": "https://docs.python.org/3/library/pathlib.html",
                        "snippet": "Path.resolve strict symlink loops Python 3.13",
                    }
                ],
            }
        )

    def fake_read(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "text": body,
                "metadata": {"fixture": "long-query-focused-body"},
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    completed = research_module._run_search_shard(
        {
            "shardId": "query-focused-excerpt",
            "kind": "baseline",
            "query": "Path.resolve strict symlink loops Python 3.13",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="query-focused-excerpt-test",
    )

    fetched = completed["fetchedTopSources"][0]
    assert relevant_paragraph in fetched["text"]
    assert 0 < len(fetched["text"]) <= research_module._RESEARCH_SOURCE_CAPTURE_CHARS
    assert fetched["contentChars"] == len(fetched["text"])
    assert fetched["originalContentChars"] == len(body)
    assert fetched["omittedChars"] == len(body) - len(fetched["text"])
    assert fetched["evidenceSelection"] == "query_focused_excerpt"
    assert fetched["textPreview"] == fetched["text"][:1200]
    assert fetched["metadata"] == {"fixture": "long-query-focused-body"}


def test_search_shard_budgets_transport_reads_without_semantic_source_veto(monkeypatch):
    query = "Python pathlib CLI path validation"
    urls = (
        "https://docs.python.org/3/using/index.html",
        "https://docs.python.org/3/reference/index.html",
        "https://docs.python.org/3/library/argparse.html",
        "https://click.palletsprojects.com/en/stable/parameter-types/",
    )
    read_calls: list[dict] = []

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": f"Python pathlib CLI evidence {index}",
                        "url": url,
                        "snippet": "Python pathlib CLI path validation official documentation",
                    }
                    for index, url in enumerate(urls, start=1)
                ],
            }
        )

    def fake_read(**kwargs):
        read_calls.append(dict(kwargs))
        index = urls.index(kwargs["url"])
        text = (
            "Unrelated interpreter startup inventory and generic release administration details. " * 12
            if index < 2
            else "Python pathlib CLI path validation converts and checks filesystem path inputs with explicit error boundaries. " * 12
        )
        return json.dumps({"ok": True, "status": 200, "title": f"Fixture {index + 1}", "text": text})

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    monkeypatch.setattr(research_module, "_jina_api_key", lambda: "")

    completed = research_module._run_search_shard(
        {"shardId": "read-until-quality", "kind": "facet_cli_parser", "query": query},
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=1,
        use_agent_browser_profile=False,
        tool_call_id="read-until-quality-test",
    )

    assert [call["url"] for call in read_calls] == list(urls[:2])
    assert all(call["maxTextChars"] == research_module._RESEARCH_SOURCE_READ_CHARS for call in read_calls)
    observations = research_module._research_read_observations([completed])
    assert len(observations) == 2
    assert all("Unrelated interpreter" in item["text"] for item in observations)



def test_python_packaging_document_family_collapses_latest_locale_aliases():
    canonical = "https://packaging.python.org/specifications/entry-points"
    latest = "https://packaging.python.org/en/latest/specifications/entry-points/"

    assert research_module._research_document_identity(canonical) == research_module._research_document_identity(latest)


def test_product_docs_document_family_collapses_locale_variants_and_prefers_english():
    families = (
        (
            "https://docs.anthropic.com/en/docs/claude-code/quickstart",
            "https://docs.anthropic.com/ja/docs/claude-code/quickstart",
        ),
        (
            "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli",
            "https://docs.github.com/fr/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli",
        ),
        (
            "https://code.claude.com/docs/en/setup",
            "https://code.claude.com/docs/ja/setup",
        ),
    )

    for english, translated in families:
        assert research_module._research_document_identity(english) == research_module._research_document_identity(translated)
        assert research_module._research_document_priority(english) > research_module._research_document_priority(translated)
        assert research_module._research_document_identity(
            english,
            question="Compare the English and translated documentation.",
        ) != research_module._research_document_identity(
            translated,
            question="Compare the English and translated documentation.",
        )


def test_github_latest_enterprise_cloud_route_is_the_generic_cloud_document():
    generic = (
        "https://docs.github.com/en/copilot/how-tos/copilot-cli/"
        "set-up-copilot-cli/install-copilot-cli"
    )
    latest_cloud_translation = (
        "https://docs.github.com/fr/enterprise-cloud@latest/copilot/how-tos/"
        "copilot-cli/set-up-copilot-cli/install-copilot-cli"
    )
    pinned_server = (
        "https://docs.github.com/en/enterprise-server@3.17/copilot/how-tos/"
        "copilot-cli/set-up-copilot-cli/install-copilot-cli"
    )

    assert research_module._research_document_identity(
        generic
    ) == research_module._research_document_identity(latest_cloud_translation)
    assert research_module._research_document_identity(
        generic
    ) != research_module._research_document_identity(pinned_server)
    assert research_module._research_document_priority(
        generic
    ) > research_module._research_document_priority(latest_cloud_translation)



def test_compact_research_text_uses_complete_sentence_or_word_boundary():
    sentence = "A complete sentence establishes the evidence boundary. "
    compact = research_module._compact_research_text(sentence + "unfinished_token" * 30, limit=80)

    assert compact == "A complete sentence establishes the evidence boundary...."
    assert len(compact) <= 80


def test_deterministic_query_keeps_cjk_subject_that_starts_with_an_imperative_word():
    assert research_module._deterministic_facet_search_query(
        "生成式人工智能服务管理暂行办法 第七条 训练数据合法性"
    ).startswith("生成式人工智能")
    assert research_module._deterministic_facet_search_query(
        "研究方法的可重复性与证据边界"
    ).startswith("研究方法")


def test_deterministic_query_removes_explicit_cjk_imperative_prefix():
    assert research_module._deterministic_facet_search_query(
        "请研究：生成式人工智能服务管理暂行办法"
    ) == "生成式人工智能服务管理暂行办法"



def test_evidence_excerpt_citation_tokens_normalize_to_source_citations():
    normalized = normalize_citation_tokens(
        "The converter is pathlib.Path [S1:E1], with another fact 【s2:e4】."
    )

    assert normalized == "The converter is pathlib.Path [S1], with another fact [S2]."


def test_research_source_excerpt_finds_visible_variant_of_query_identifier():
    body = (
        "Navigation and unrelated parameter material. " * 180
        + "\n\nThe Path type validates file-system paths and can return a pathlib.Path value.\n\n"
        + "Additional unrelated reference material. " * 180
    )

    excerpt = research_module._research_source_excerpt(
        body,
        "click.Path path_type resolve_path",
        limit=2400,
    )

    assert len(excerpt) <= 2400
    assert "The Path type validates file-system paths" in excerpt



def test_research_source_excerpt_reserves_late_structured_anchors_across_unicode_whitespace():
    article_113 = (
        "Article\u00a0\n113 Entry into force and application. The obligations for "
        "general-purpose AI models apply from 2\u00a0August\u00a02025; providers of "
        "models placed on the market before that date shall comply by "
        "2\u00a0August\u00a02027."
    )
    body = (
        "Regulation 2024/1689 general-purpose AI recital and navigation material. "
        * 6_000
        + article_113
        + " Unrelated closing annex index. " * 3_000
    )

    excerpt = research_module._research_source_excerpt(
        body,
        (
            "Regulation 2024/1689 Article 113 GPAI effective dates "
            "2 August 2025 and 2 August 2027"
        ),
        limit=12_000,
    )

    assert len(body) > 500_000
    assert len(excerpt) <= 12_000
    assert article_113 in excerpt
    assert "2\u00a0August\u00a02025" in excerpt
    assert "2\u00a0August\u00a02027" in excerpt


def test_web_read_explicit_research_budget_preserves_late_long_document_text():
    from core.tools import web_fetcher

    late_article = (
        "Article 99 Penalties. Providers of general-purpose AI models may be fined "
        "for infringements of their obligations."
    )
    full_text = ("Regulatory recital material without the operative article. " * 10_000) + late_article
    assert 500_000 < len(full_text) < web_fetcher.MAX_RESEARCH_TEXT_CHARS
    page = web_fetcher.WebPagePayload(
        url="https://eur-lex.europa.eu/example",
        final_url="https://eur-lex.europa.eu/example",
        requested_mode="auto",
        referer_mode="none",
        referer_url="",
        fetch_mode="static",
        attempted_modes=["static"],
        available_modes={},
        status=200,
        tls_strategy="system",
        ca_bundle_path="",
        proxy_bypass_used=False,
        title="Regulation",
        text=full_text[: web_fetcher.MAX_TEXT_CHARS]
        + f"\n\n...[TRUNCATED] ({len(full_text)} chars total)",
        html=f"<html><body><main><p>{full_text}</p></main></body></html>",
        metadata={},
        links=[],
        media=[],
        warnings=[],
    )

    rendered = web_fetcher._render_page_summary(
        page,
        max_text_chars=web_fetcher.MAX_RESEARCH_TEXT_CHARS,
    )

    assert late_article in rendered["text"]
    assert len(rendered["text"]) > 500_000
    assert research_module._RESEARCH_SOURCE_READ_CHARS == web_fetcher.MAX_RESEARCH_TEXT_CHARS



def _staged_outline_repair_fixture(*, critical_missing: list[str] | None = None):
    sources: list[dict] = []
    claims: list[dict] = []
    retrieved_at = "2026-07-29T12:00:00Z"
    facts = (
        "The architecture source defines the component boundary for the research runtime evidence contract.",
        "The routing source describes how a query reaches evidence collection in the research runtime contract.",
        "The citation source binds claim identities to read documents in the research runtime evidence contract.",
        "The temporal source separates retrieval timestamps from publication dates in the research runtime evidence contract.",
        "The conflict source keeps contradictory material visible within the research runtime evidence contract.",
        "The recovery source records resumable state for an interrupted research runtime evidence contract execution.",
        "The governance source defines the review boundary for a research runtime evidence contract answer.",
        "The delivery source retains supporting citations in the final research runtime evidence contract answer.",
    )
    for index in range(1, TARGET_RESEARCH_SOURCE_COUNT + 1):
        fact = facts[index - 1]
        body = " ".join([fact] * 8)
        sources.append(
            {
                "sourceId": f"outline-repair-{index}",
                "citationKey": f"S{index}",
                "title": f"Outline repair source {index}",
                "url": f"https://outline-repair-{index}.example/docs",
                "authorityScore": 85,
                "tier": "primary",
                "selectedForEvidence": True,
                "retrievedAt": retrieved_at,
                "contentChars": len(body),
                "readEvidence": {
                    "verified": True,
                    "contentChars": len(body),
                    "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "retrievedAt": retrieved_at,
                },
                "text": body,
            }
        )
        claims.append(
            {
                "claimId": f"C{index}",
                "claim": fact,
                "claimType": "source_fact",
                "supportingSources": [f"S{index}"],
                "evidenceExcerptKey": f"S{index}:E1",
                "confidence": "high",
            }
        )
    plan = {
        "reviewDecision": "accept",
        "reviewReasons": [],
        "headline": "Runtime-verified claims with an invalid outline",
        "claimTable": claims,
        "answerOutline": [
            {
                "sectionId": "broken",
                "title": "Incomplete binding",
                "objective": "This intentionally omits verified claims.",
                "claimIds": ["C1"],
            }
        ],
        "compositeInferences": [],
        "conflictMatrix": [],
        "missingEvidence": [],
        "criticalMissingEvidence": list(critical_missing or []),
        "recommendedNextQueries": [],
        "assumptions": [],
        "temporalAssessment": {"asOf": retrieved_at},
    }
    return sources, plan



def test_research_source_pack_preserves_multi_facet_lineage():
    packed = research_module._research_source_pack(
        {
            "url": "https://example.test/evidence",
            "title": "Evidence",
            "researchFacetId": "timeline",
            "researchFacetIds": ["timeline", "penalties"],
        }
    )

    assert packed["researchFacetId"] == "timeline"
    assert packed["researchFacetIds"] == ["timeline", "penalties"]



def test_architect_fallback_models_require_explicit_configuration(monkeypatch):
    monkeypatch.delenv("V8_RESEARCH_ARCHITECT_MODEL_FALLBACKS", raising=False)
    assert research_module._research_architect_fallback_model_refs() == []

    monkeypatch.setenv(
        "V8_RESEARCH_ARCHITECT_MODEL_FALLBACKS",
        "provider-a::model-a, provider-b::model-b;provider-a::model-a",
    )
    assert research_module._research_architect_fallback_model_refs() == [
        "provider-a::model-a",
        "provider-b::model-b",
    ]


def test_architect_candidate_keeps_exact_model_ref_for_context_lookup():
    candidate = (
        SimpleNamespace(_meta={"model_ref": "minimax-cn::MiniMax-M3"}),
        "minimax-cn::MiniMax-M3",
        "web-research-architect",
    )

    assert research_module._architect_candidate_identity(candidate) == "minimax-cn::minimax-m3"
    assert (
        research_module._architect_candidate_context_model_ref(candidate)
        == "minimax-cn::MiniMax-M3"
    )



def test_production_reviewer_candidates_prefer_configured_verifier_before_supervisor(monkeypatch):
    from core.llm_factory import llm_factory

    created: list[tuple[str, str]] = []

    class DummyLLM:
        def __init__(self, model_ref: str, origin: str = ""):
            self._meta = {"model_ref": model_ref}
            if origin:
                self._meta["research_candidate_origin"] = origin

    architect_candidates = [
        (
            DummyLLM("deepseek::deepseek-v4-flash", "agent_binding"),
            "deepseek::deepseek-v4-flash",
            "web-research-architect",
        ),
        (
            DummyLLM("deepseek::deepseek-chat", "role_fallback:summary"),
            "role:summary",
            "summary",
        ),
    ]
    monkeypatch.setattr(
        research_module.storage,
        "get_agent_model_binding",
        lambda agent_id: (
            "deepseek::deepseek-v4-pro" if agent_id == "verification-engineer" else ""
        ),
    )
    monkeypatch.setattr(
        llm_factory,
        "create_for_role",
        lambda role, **_kwargs: (
            created.append(("role", role))
            or DummyLLM("minimax-cn::MiniMax-M3")
        ),
    )
    monkeypatch.setattr(
        llm_factory,
        "create_chat_model",
        lambda model_ref, **kwargs: (
            created.append(("agent", kwargs.get("_role", "")))
            or DummyLLM(model_ref)
        ),
    )

    reviewers = research_module._create_web_research_reviewer_llm_candidates(
        architect_candidates
    )

    assert [research_module._architect_candidate_identity(item) for item in reviewers] == [
        "deepseek::deepseek-v4-pro",
        "minimax-cn::minimax-m3",
        "deepseek::deepseek-v4-flash",
        "deepseek::deepseek-chat",
    ]
    assert [research_module._architect_candidate_selection_origin(item) for item in reviewers] == [
        "agent_reviewer:verification-engineer",
        "role_reviewer:supervisor",
        "agent_binding",
        "role_fallback:summary",
    ]
    assert created == [
        ("role", "supervisor"),
        ("agent", "verification-engineer"),
    ]


def test_fixture_reviewer_candidates_keep_supplied_order_without_real_binding(monkeypatch):
    from core.llm_factory import llm_factory

    class DummyLLM:
        def __init__(self, model_ref: str):
            self._meta = {"model_ref": model_ref}

    candidates = [
        (DummyLLM("test::first"), "test::first", "writer"),
        (DummyLLM("test::second"), "test::second", "reviewer"),
    ]
    monkeypatch.setattr(
        llm_factory,
        "create_for_role",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    assert research_module._create_web_research_reviewer_llm_candidates(candidates) == candidates


def test_architect_candidates_use_registered_subagent_role_only_when_binding_is_absent(monkeypatch):
    from core.llm_factory import llm_factory

    created: list[str] = []

    class DummyLLM:
        def __init__(self, model_ref: str):
            self._meta = {"model_ref": model_ref}

    role_models = {
        "subagent": "provider-d::subagent-model",
        "summary": "provider-b::summary-model",
        "supervisor": "provider-c::supervisor-model",
    }
    monkeypatch.delenv("V8_RESEARCH_ARCHITECT_MODEL_FALLBACKS", raising=False)
    monkeypatch.setattr(research_module.storage, "get_agent_model_binding", lambda _agent_id: "")
    monkeypatch.setattr(
        research_module.storage,
        "get_role_model_id",
        lambda role: role_models.get(role, ""),
    )
    monkeypatch.setattr(
        llm_factory,
        "create_for_role",
        lambda role, **_kwargs: created.append(role_models[role]) or DummyLLM(role_models[role]),
    )

    candidates = research_module._create_web_research_architect_llm_candidates()

    assert [(model_id, role) for _llm, model_id, role in candidates] == [
        ("role:subagent", "subagent"),
    ]
    assert created == ["provider-d::subagent-model"]



def test_architect_async_deadline_cancels_the_provider_coroutine():
    class SlowAsyncLLM:
        cancelled = False

        async def ainvoke(self, *_args, **_kwargs):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    llm = SlowAsyncLLM()
    started_at = time.perf_counter()

    with pytest.raises(TimeoutError):
        research_module._invoke_architect_candidate_with_deadline(
            (llm, "slow-model", "research"),
            [],
            seconds=0.5,
            max_tokens=100,
        )

    assert time.perf_counter() - started_at < 1.5
    assert llm.cancelled is True


def test_baidu_baike_is_ranked_as_background_encyclopedic_source():
    quality = research_module._source_quality(
        "https://baike.baidu.com/item/%E6%9D%8E%E7%99%BD/1043",
        allowed_domains=[],
        source_policy="authoritative",
        title="李白_百度百科",
        snippet="唐代诗人。",
    )

    assert quality["catalogSourceId"] == "encyclopedic_background"
    assert quality["catalogCategory"] == "background"
    assert quality["authorityTier"] == "background"
    assert "source_catalog:encyclopedic_background" in quality["reasons"]


def test_non_video_research_penalizes_video_platform_candidates():
    url = "https://www.youtube.com/watch?v=pathlib"
    normal = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Python pathlib tutorial",
        snippet="Path handling video",
        video_research=False,
    )
    video = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Python pathlib tutorial with 100K views",
        snippet="Path handling video with 5K likes",
        video_research=True,
    )

    assert "non_video_research_source_penalty" in normal["reasons"]
    assert normal["authorityScore"] < 45
    assert video["authorityScore"] > normal["authorityScore"]


def test_relevance_ignores_generic_research_words_and_understands_cli_alias():
    question = "What are the current best practices for using Python pathlib in CLI tools? cite official sources."
    irrelevant = research_module._source_relevance_score(
        question,
        title="Pillow (PIL Fork)",
        snippet="The current conventions describe a Python codec setup function.",
        text="Image codecs, encoders, decoders, and extension modules.",
    )
    argparse = research_module._source_relevance_score(
        question,
        title="argparse - Parser for command-line options",
        text="The command line parser converts argument values and reports errors.",
    )

    assert irrelevant == 0
    assert argparse > irrelevant


def test_finance_crypto_and_shopping_catalog_sources_are_ranked_by_authority_tier():
    primary_cases = {
        "https://www.sec.gov/Archives/edgar/data/example": "us_equity_primary",
        "https://www.sse.com.cn/disclosure/listedinfo/announcement/": "cn_equity_primary",
        "https://www.cninfo.com.cn/new/disclosure/detail": "cn_equity_primary",
        "https://www.binance.com/en/support/announcement/example": "crypto_market_primary",
        "https://etherscan.io/tx/0x123": "crypto_onchain_primary",
        "https://www.amazon.com/dp/B000000": "shopping_platform_primary",
        "https://item.jd.com/100000.html": "shopping_platform_primary",
    }
    secondary_cases = {
        "https://finance.yahoo.com/quote/AAPL": "market_data_secondary",
        "https://www.coingecko.com/en/coins/bitcoin": "crypto_aggregate_secondary",
        "https://defillama.com/protocol/example": "crypto_aggregate_secondary",
    }

    for url, catalog_id in primary_cases.items():
        quality = research_module._source_quality(
            url,
            allowed_domains=[],
            source_policy="authoritative",
            title="official source",
            snippet="official announcement or product listing",
        )

        assert quality["catalogSourceId"] == catalog_id
        assert quality["authorityTier"] == "primary"
        assert quality["tier"] == "primary"
        assert quality["authorityScore"] >= 80
        assert f"source_catalog:{catalog_id}" in quality["reasons"]

    for url, catalog_id in secondary_cases.items():
        quality = research_module._source_quality(
            url,
            allowed_domains=[],
            source_policy="authoritative",
            title="aggregated market quote",
            snippet="timely supporting market data",
        )

        assert quality["catalogSourceId"] == catalog_id
        assert quality["authorityTier"] == "secondary"
        assert quality["tier"] == "secondary"
        assert 55 <= quality["authorityScore"] < 80
        assert f"source_catalog:{catalog_id}" in quality["reasons"]


def test_academic_sources_split_primary_papers_from_discovery_and_benchmarks():
    primary_cases = {
        "https://arxiv.org/abs/1706.03762": "academic_paper_primary",
        "https://openreview.net/forum?id=example": "academic_paper_primary",
        "https://aclanthology.org/2024.acl-long.1/": "academic_paper_primary",
        "https://pubmed.ncbi.nlm.nih.gov/12345678/": "academic_paper_primary",
        "https://dl.acm.org/doi/10.1145/example": "academic_paper_primary",
    }
    secondary_cases = {
        "https://scholar.google.com/scholar?q=transformer": "academic_discovery_secondary",
        "https://paperswithcode.com/paper/example": "academic_benchmark_secondary",
    }

    for url, catalog_id in primary_cases.items():
        quality = research_module._source_quality(
            url,
            allowed_domains=[],
            source_policy="authoritative",
            title="paper abstract",
            snippet="method and publication metadata",
        )

        assert quality["catalogSourceId"] == catalog_id
        assert quality["catalogCategory"] == "academic_paper"
        assert quality["authorityTier"] == "primary"
        assert quality["tier"] == "primary"
        assert quality["authorityScore"] >= 80
        assert f"source_catalog:{catalog_id}" in quality["reasons"]

    for url, catalog_id in secondary_cases.items():
        quality = research_module._source_quality(
            url,
            allowed_domains=[],
            source_policy="authoritative",
            title="paper discovery or benchmark context",
            snippet="supporting discovery data",
        )

        assert quality["catalogSourceId"] == catalog_id
        assert quality["authorityTier"] == "secondary"
        assert quality["tier"] == "secondary"
        assert 55 <= quality["authorityScore"] < 80
        assert f"source_catalog:{catalog_id}" in quality["reasons"]


def test_hacker_news_is_higher_scored_developer_signal_but_not_primary_evidence():
    hn_quality = research_module._source_quality(
        "https://news.ycombinator.com/item?id=123",
        allowed_domains=[],
        source_policy="authoritative",
        title="Show HN: V8 Agent OS",
        snippet="123 points and 42 comments",
    )
    generic_community_quality = research_module._source_quality(
        "https://lobste.rs/s/example",
        allowed_domains=[],
        source_policy="authoritative",
        title="Developer discussion",
        snippet="field report",
    )

    assert hn_quality["catalogSourceId"] == "hacker_news_developer_signal"
    assert hn_quality["catalogCategory"] == "developer_signal"
    assert hn_quality["authorityTier"] == "secondary"
    assert hn_quality["tier"] == "secondary"
    assert hn_quality["authorityScore"] > generic_community_quality["authorityScore"]
    assert "source_catalog:hacker_news_developer_signal" in hn_quality["reasons"]


def test_removed_paywall_and_tradingview_hosts_are_not_catalog_ranked():
    urls = (
        "https://www.nature.com/articles/example",
        "https://www.science.org/doi/10.1126/science.example",
        "https://ieeexplore.ieee.org/document/1234567",
        "https://britannica.com/topic/example",
        "https://www.britannica.com/topic/example",
        "https://www.tradingview.com/symbols/NASDAQ-AAPL/",
    )

    for url in urls:
        quality = research_module._source_quality(
            url,
            allowed_domains=[],
            source_policy="authoritative",
            title="removed source",
            snippet="removed from trusted catalog",
        )

        assert quality["catalogSourceId"] is None
        assert quality["catalogCategory"] is None
        assert quality["authorityTier"] is None
        assert quality["tier"] == "weak"
        assert not any(str(reason).startswith("source_catalog:") for reason in quality["reasons"])


def test_primary_sources_rank_above_secondary_market_portals():
    sec_quality = research_module._source_quality(
        "https://www.sec.gov/Archives/edgar/data/example",
        allowed_domains=[],
        source_policy="authoritative",
        title="10-K filing",
        snippet="annual report filing",
    )
    quote_quality = research_module._source_quality(
        "https://finance.yahoo.com/quote/AAPL",
        allowed_domains=[],
        source_policy="authoritative",
        title="AAPL quote",
        snippet="stock price chart",
    )

    assert sec_quality["authorityScore"] > quote_quality["authorityScore"]
    assert sec_quality["tier"] == "primary"
    assert quote_quality["tier"] == "secondary"



def test_chinese_community_sources_stay_low_confidence_evidence():
    urls = (
        "https://www.zhihu.com/question/123/answer/456",
        "https://juejin.cn/post/123",
        "https://blog.csdn.net/example/article/details/123",
        "https://www.cnblogs.com/example/p/123.html",
    )

    for url in urls:
        quality = research_module._source_quality(
            url,
            allowed_domains=[],
            source_policy="authoritative",
            title="社区经验文章",
            snippet="实践记录与个人经验。",
        )

        assert quality["tier"] == "weak"
        assert "low_quality_host_hint" in quality["reasons"]



def test_narrow_technical_english_query_drops_delivery_wording() -> None:
    question = (
        "Using only official documentation, explain the LangChain Python v1 create_agent "
        "and middleware core APIs, a minimal usage pattern, and migration notes. "
        "Return a directly usable answer in Simplified Chinese."
    )

    shards = research_module._build_shards(
        question=question,
        research_intent="",
        source_policy="authoritative",
        seed_urls=[],
        allowed_domains=[],
        max_shards=6,
    )
    query = str(shards[0].get("query") or "")

    assert query == "LangChain Python v1 create_agent middleware APIs minimal usage migration notes"
    assert "using only" not in query
    assert "simplified chinese" not in query



def test_single_named_authoritative_subject_uses_task_shaped_source_floor() -> None:
    requirements = research_module._research_delivery_requirements(
        "依据官方资料，说明《生成式人工智能服务管理暂行办法》的适用范围、"
        "训练数据、内容标识、安全评估和投诉要求。"
    )

    assert requirements == {
        "mode": "single_authoritative_subject",
        "countPolicy": "advisory",
        "explicitUserSourceCount": 0,
        "minimumSources": 2,
        "minimumDistinctHosts": 1,
        "minimumClaims": research_module.MIN_RESEARCH_CLAIM_COUNT,
        "minimumAnswerChars": research_module.MIN_RESEARCH_ANSWER_CHARS,
        "targetSources": 3,
        "targetDistinctHosts": 1,
        "targetClaims": 6,
        "targetAnswerChars": 3_000,
    }


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
    assert set(payload["shards"][0]) == {"shardId", "kind", "query", "evidenceQuery", "reason"}


def test_research_broker_plan_uses_explicit_user_visible_language(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 2, "maxShardCount": 4, "maxRounds": 2}},
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="plan",
            question="Verify the current LangChain API",
            preferredLanguage="zh-CN",
            state={"run_id": "run-language"},
        )
    )

    assert payload["preferredLanguage"] == "zh-CN"


def test_narrow_technical_plan_caps_discovery_to_task_shaped_source_target(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {
            "research": {
                "enabled": True,
                "defaultShardCount": 10,
                "maxShardCount": 30,
                "maxRounds": 5,
            }
        },
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="plan",
            question=(
                "What are the current best practices for using Python pathlib "
                "in CLI tools? Cite official sources."
            ),
            maxShards=10,
            state={"run_id": "run-task-shaped-shard-cap"},
        )
    )

    assert payload["deliveryRequirements"]["targetSources"] == 4
    assert payload["limits"]["configuredMaxShards"] == 10
    assert payload["limits"]["effectiveMaxShards"] == 4
    assert len(payload["shards"]) <= 4


def test_research_broker_plan_splits_structured_bundle_into_atomic_facets(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 3, "maxShardCount": 6, "maxRounds": 2}},
    )
    question = (
        "Research every item below as one evidence bundle.\n"
        "1. [timeline] Verify the current GPAI compliance timeline.\n"
        "2. [systemic-risk] Verify the systemic-risk threshold and exceptions.\n"
        "3. [penalties] Verify enforcement powers and penalty tiers."
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="plan",
            question=question,
            maxShards=3,
            state={"run_id": "run-structured-facets"},
        )
    )

    assert [item["kind"] for item in payload["shards"]] == [
        "facet:timeline",
        "facet:systemic-risk",
        "facet:penalties",
    ]
    assert [item["query"] for item in payload["shards"]] == [
        "the current GPAI compliance timeline",
        "the systemic-risk threshold and exceptions",
        "enforcement powers and penalty tiers",
    ]



def test_research_broker_uses_source_router_by_default(monkeypatch):
    _search_then_no_answer(monkeypatch, "source router contract")
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
    monkeypatch.setattr(
        research_module,
        "web_read",
        SimpleNamespace(func=lambda **kwargs: json.dumps({"ok": True, "text": "source router contract " * 80})),
    )

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
    assert payload["sourceMatrix"][0]["url"] == "https://docs.router.example/page"
    assert payload["providerAttemptMatrix"][0]["provider"] == "router"


def test_research_broker_reads_explicit_seed_before_search_provider(monkeypatch):
    from tests.core.test_research_agent import read, submit, approve
    _script_agent(monkeypatch, [read(), submit(answer="SQLite FTS5 is documented. [S1]")], [approve()])
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 2, "maxShardCount": 3, "maxRounds": 2}},
    )
    search_calls: list[str] = []
    read_calls: list[str] = []

    def fake_search(**kwargs):
        search_calls.append(str(kwargs.get("query") or ""))
        return json.dumps({"ok": False, "results": [], "error": "search provider unavailable"})

    def fake_read(**kwargs):
        read_calls.append(str(kwargs.get("url") or ""))
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "title": "SQLite FTS5 Extension",
                "text": "Official SQLite FTS5 documentation. " * 80,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="What is the current SQLite FTS5 support contract?",
            sourcePolicy="authoritative",
            seedUrls=["https://sqlite.org/fts5.html"],
            maxShards=2,
            maxRounds=2,
            state={"run_id": "run-seed-first"},
        )
    )

    assert read_calls[0] == "https://sqlite.org/fts5.html"
    assert search_calls == []
    seed_source = next(item for item in payload["sourceMatrix"] if item["url"] == "https://sqlite.org/fts5.html")
    assert seed_source["selectedForEvidence"] is True
    assert seed_source["readEvidence"]["verified"] is True
    assert payload["researchAnswerPack"]["sources"][0]["url"] == "https://sqlite.org/fts5.html"



@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, 60), (3, 5), (7, 7), (120, 90)],
)
def test_research_config_preserves_architect_per_call_timeout_contract(
    monkeypatch,
    configured,
    expected,
):
    research = {}
    if configured is not None:
        research["architectAgentTimeoutSeconds"] = configured
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": research},
    )

    assert research_module._research_config()["architectAgentTimeoutSeconds"] == expected


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
    assert pack["score"]["qualityStatus"] == "insufficient"
    assert "low_quality_answer_surface" in pack["missingOrStaleReasons"]
    assert pack["recommendedNextAction"] == "continue_research"


def test_detailed_answer_can_state_that_this_research_did_not_cover_one_boundary():
    answer = (
        "本次调研未提供某个次要配置项，因此该项保持为明确限制。\n\n"
        + _high_quality_answer(TARGET_RESEARCH_SOURCE_COUNT)
    )

    assert research_module._is_low_quality_research_answer(answer) is False


def test_research_delivery_surfaces_keep_all_twelve_architect_sources():
    sources = [
        {
            "sourceId": f"source-{index}",
            "title": f"Source {index}",
            "url": f"https://host-{index}.example/evidence",
            "selectedForEvidence": True,
        }
        for index in range(1, research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT + 1)
    ]

    compact_bank = research_module._compact_visible_evidence_bank(
        {"selectedSources": sources, "claims": [], "stats": {}}
    )
    compact_pack = research_module._compact_visible_answer_pack(
        {"answer": "evidence-backed answer", "sources": sources}
    )
    visible_bundle = research_module._visible_bundle({"sourceMatrix": sources})

    assert len(compact_bank["selectedSources"]) == research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT
    assert len(compact_pack["sources"]) == research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT
    assert len(visible_bundle["sourceMatrix"]) == research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT


def test_research_delivery_budget_preserves_answer_index_and_runtime_proof_ref():
    question = "How should a verified research runtime preserve evidence for its Supervisor?"
    answer = _high_quality_answer(TARGET_RESEARCH_SOURCE_COUNT)
    as_of = "2026-07-29T12:00:00Z"
    sources = []
    claims = []
    claim_labels = (
        "architecture",
        "authority",
        "retrieval",
        "provenance",
        "temporal",
        "versioning",
        "attribution",
        "entailment",
        "coverage",
        "conflict",
        "limitations",
        "verification",
        "reusability",
        "observability",
        "governance",
        "delivery",
    )
    for source_index in range(1, TARGET_RESEARCH_SOURCE_COUNT + 1):
        source_text = " ".join([(
            f"Source {source_index} records a distinct implementation fact, its applicability boundary, "
            "and the exact verification procedure for a reusable research answer."
        )] * 3)
        source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        source = {
            "sourceId": f"source-{source_index}",
            "citationKey": f"S{source_index}",
            "title": f"Verified source {source_index}",
            "url": f"https://host-{source_index}.example/evidence",
            "host": f"host-{source_index}.example",
            "tier": "primary",
            "authorityScore": 90,
            "selectedForEvidence": True,
            "retrievedAt": as_of,
            "updatedAt": f"2026-07-{10 + source_index:02d}T00:00:00Z",
            "temporalEvidence": {
                "updatedAt": f"2026-07-{10 + source_index:02d}T00:00:00Z",
                "status": "dated_context",
            },
            "contentChars": len(source_text),
            "readEvidence": {
                "verified": True,
                "contentSha256": source_digest,
                "contentChars": len(source_text),
                "retrievedAt": as_of,
            },
        }
        sources.append(source)
        for claim_offset in range(2):
            claim_index = (source_index - 1) * 2 + claim_offset + 1
            claim_label = claim_labels[claim_index - 1]
            excerpt = (
                f"Verified {claim_label} excerpt: {source_text} "
                f"This observation is independently scoped to the {claim_label} evidence unit."
            )
            normalized_excerpt = re.sub(r"\s+", " ", excerpt).strip().lower()
            claims.append(
                {
                    "claimId": f"C{claim_index}",
                    "claim": (
                        f"The {claim_label} claim describes a distinct evidence-preservation "
                        "boundary that the Supervisor can apply without another search."
                    ),
                    "claimType": "source_fact",
                    "supportingSources": [
                        {
                            "sourceId": source["sourceId"],
                            "url": source["url"],
                            "citationKey": source["citationKey"],
                        }
                    ],
                    "evidenceExcerptKey": f"S{source_index}:E{claim_offset + 1}",
                    "evidenceExcerpt": excerpt,
                    "evidenceExcerptSha256": hashlib.sha256(
                        normalized_excerpt.encode("utf-8")
                    ).hexdigest(),
                    "evidenceVerified": True,
                }
            )

    base = {
        "ok": True,
        "kind": "research_evidence_bundle",
        "evidenceBundleId": "research-budget-projection",
        "summary": "Verified delivery projection",
        "question": question,
        "freshness": "latest",
        "reviewDecision": "accept",
        "answer": answer,
        "asOf": as_of,
        "sourceUrls": sources,
        "sourceMatrix": sources,
        "claimTable": claims,
        "criticalMissingEvidence": [],
    }
    review_template = {
        "reviewDecision": "accept",
        "reviewReasons": ["The exact final answer and all verified claims are accepted."],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "fixture::semantic"),
        ("adversarial", "fixture::adversarial"),
    ):
        review = {**review_template, "reviewMode": review_mode}
        review.update(
            build_research_review_binding(
                base,
                reviewer_model_id=reviewer_model_id,
                reviewed_at=as_of,
            )
        )
        consensus_reviews.append(review)
    independent_review = {
        **consensus_reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": 2,
        "consensusReviewerModelIds": [
            review["reviewerModelId"] for review in consensus_reviews
        ],
        "consensusReviews": consensus_reviews,
    }
    base["independentReview"] = independent_review
    base["finalExperiencePack"] = {
        "question": question,
        "freshness": "latest",
        "reviewDecision": "accept",
        "researchResult": answer,
        "sourceUrls": sources,
        "claimTable": claims,
        "independentReview": independent_review,
        "asOf": as_of,
        "synthesisMode": "model_agent",
        "modelSynthesis": {
            "used": True,
            "writerMode": "segmented",
            "writerSectionCount": 4,
            "writerRevisionCount": 0,
            "sameEvidenceReviewRejected": False,
            "claimPlanMode": "runtime_canonical",
            "claimPlanVersion": "v8.research_claim_plan.v1",
            "claimPlanDigest": "a" * 64,
            "claimPlanElapsedMs": 2,
            "structureStatus": "accepted",
            "structureElapsedMs": 140,
            "writerElapsedMs": 1200,
            "reviewElapsedMs": 750,
            "modelPlanCallCount": 0,
            "planAttempts": [{"largeRuntimeTrace": "x" * 20_000}],
        },
    }
    base["researchAnswerPack"] = research_module._research_answer_pack(base)
    score = base["researchAnswerPack"]["score"]
    assert score["deliveryReady"] is True, (
        base["researchAnswerPack"]["missingOrStaleReasons"],
        len(answer),
    )
    base["deliveryReady"] = True
    base["qualityTier"] = score["qualityTier"]
    base["qualityMetrics"] = score["acceptanceMetrics"]
    canonical_metrics = research_acceptance_metrics(base)
    assert canonical_metrics == score["acceptanceMetrics"]

    rendered_text = research_module._render_payload(
        research_module._visible_bundle(copy.deepcopy(base)),
        max_chars=36_000,
    )
    rendered = json.loads(rendered_text)

    assert len(rendered_text) <= 36_000
    if "answer" in rendered:
        assert rendered["answer"] == answer
    assert rendered["researchAnswerPack"]["answer"] == answer
    assert rendered["question"] == question
    assert rendered["freshness"] == "latest"
    assert len(rendered["researchAnswerPack"]["sources"]) == 8
    assert "claimTable" not in rendered["researchAnswerPack"]
    assert rendered["researchAnswerPack"]["claimTableSummary"] == {
        "claimCount": 16,
        "proofLocation": "evidenceBundleId",
    }
    assert "planAttempts" not in rendered["finalExperiencePack"]["modelSynthesis"]
    assert rendered["finalExperiencePack"]["modelSynthesis"]["claimPlanMode"] == (
        "runtime_canonical"
    )
    assert rendered["finalExperiencePack"]["modelSynthesis"]["modelPlanCallCount"] == 0
    assert rendered["researchAnswerPack"]["score"]["acceptanceMetrics"] == canonical_metrics
    assert rendered["researchAnswerPack"]["detailRef"]["evidenceBundleId"] == (
        "research-budget-projection"
    )
    assert "get_evidence" in rendered["researchAnswerPack"]["detailRef"]["tool"]
    assert rendered["omitted"]["runtimeProof"] == "ledger_only"



def test_research_evidence_bank_rejects_noisy_sources(monkeypatch):
    _search_then_no_answer(monkeypatch, "low quality source gate")
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
    assert payload["researchAnswerPack"]["score"]["qualityStatus"] == "insufficient"
    assert payload["researchEvidenceBank"]["selectedSources"] == []
    assert payload["deliveryReady"] is False
    assert payload["researchLoopState"]["stopReason"] == "research_no_supported_answer"


def test_research_jina_reader_fallback_when_builtin_read_is_noisy(monkeypatch):
    _search_then_no_answer(monkeypatch, "Jina reader fallback path")
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
        text = (
            "Jina reader extracted the official documentation body with a stable source-backed implementation detail. "
            * 8
        )

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
    assert payload["sourceMatrix"]
    assert payload["answer"] == ""
    assert payload["deliveryReady"] is False
    assert payload["researchAnswerPack"]["score"]["qualityTier"] == "insufficient"



def test_auto_freshness_reuses_just_completed_exact_time_sensitive_question():
    question = "截至 2026 年 7 月，这项服务的最新状态是什么？"
    decision = research_module._experience_reuse_decision(
        [
            {
                "experiencePackId": "rxp-stale-current-question",
                "query": question,
                "title": question,
                "topicFingerprint": research_module._topic_fingerprint(question),
                "freshnessState": "current",
                "asOf": "2026-07-29T00:00:00Z",
                "qualityStatus": "high_quality",
                "researchResult": "Previously accepted result",
                "claimDigest": [{"claim": "A previously supported conclusion with a clear evidence boundary."}],
                "sourceUrls": [{"url": "https://official.example/status"}],
                "authorityScore": 90,
                "confidence": "high",
                "sourcePolicy": "authoritative",
            }
        ],
        question=question,
        source_policy="authoritative",
        freshness="auto",
    )

    assert decision["reuseDecision"] == "reuse"
    assert decision["skippedSearches"] is True


def test_stale_time_sensitive_experience_is_reused_with_supervisor_update_note():
    question = "截至 2026 年 7 月，这项服务的最新状态是什么？"
    decision = research_module._experience_reuse_decision(
        [
            {
                "experiencePackId": "rxp-stale-current-question",
                "query": question,
                "title": question,
                "topicFingerprint": research_module._topic_fingerprint(question),
                "freshnessState": "stale",
                "asOf": "2026-07-01T00:00:00Z",
                "qualityStatus": "high_quality",
                "researchResult": "Previously accepted result",
                "claimDigest": [{"claim": "A previously supported conclusion with a clear evidence boundary."}],
                "sourceUrls": [{"url": "https://official.example/status"}],
                "authorityScore": 90,
                "confidence": "high",
                "sourcePolicy": "authoritative",
            }
        ],
        question=question,
        source_policy="authoritative",
        freshness="auto",
    )

    assert decision["reuseDecision"] == "reuse"
    assert decision["reason"] == "dated_experience_pack_reused_with_supervisor_refresh_note"
    assert decision["skippedSearches"] is True
    assert decision["refreshSuggested"] is True
    assert decision["freshnessState"] == "stale"
    assert "as of 2026-07-01" in decision["supervisorContentNote"]
    assert "forceRefresh=true" in decision["supervisorContentNote"]


def test_adjacent_topic_cannot_reuse_review_bound_to_an_old_question():
    old_question = "Python pathlib path joining basic usage"
    new_question = "Python pathlib path joining security risks and symlink attack mitigations"
    decision = research_module._experience_reuse_decision(
        [
            {
                "experiencePackId": "rxp-pathlib-basic",
                "query": old_question,
                "title": old_question,
                "topicFingerprint": research_module._topic_fingerprint(old_question),
                "freshnessState": "fresh",
                "qualityStatus": "high_quality",
                "researchResult": "A complete previously reviewed answer.",
                "claimDigest": [{"claim": "Path joining has defined basic behavior."}],
                "sourceUrls": ["https://docs.python.org/3/library/pathlib.html"],
                "authorityScore": 90,
                "confidence": "high",
                "sourcePolicy": "authoritative",
            }
        ],
        question=new_question,
        source_policy="authoritative",
        freshness="timeless",
    )

    assert decision["reuseDecision"] == "review"
    assert decision["reason"] == "adjacent_topic_requires_fresh_semantic_review"
    assert decision["matchReason"].startswith("topic_overlap:")


def test_exact_reusable_candidate_wins_over_earlier_adjacent_candidate():
    question = "Check current SQLite FTS5 support in 2026"
    base = {
        "freshnessState": "current",
        "asOf": "2026-07-29T00:00:00Z",
        "qualityStatus": "high_quality",
        "researchResult": "A complete reviewed answer with current evidence.",
        "claimDigest": [{"claim": "A supported conclusion with a clear evidence boundary."}],
        "sourceUrls": [{"url": "https://sqlite.org/fts5.html"}],
        "authorityScore": 90,
        "confidence": "high",
        "sourcePolicy": "authoritative",
    }
    decision = research_module._experience_reuse_decision(
        [
            {
                **base,
                "experiencePackId": "rxp-adjacent",
                "query": "Check current SQLite JSONB support in 2026",
                "title": "Check current SQLite JSONB support in 2026",
                "topicFingerprint": research_module._topic_fingerprint(
                    "Check current SQLite JSONB support in 2026"
                ),
            },
            {
                **base,
                "experiencePackId": "rxp-exact",
                "query": question,
                "title": question,
                "topicFingerprint": research_module._topic_fingerprint(question),
            },
        ],
        question=question,
        source_policy="authoritative",
        freshness="current",
    )

    assert decision["reuseDecision"] == "reuse"
    assert decision["candidatePackId"] == "rxp-exact"


def test_topic_fingerprint_punctuation_variant_requires_refresh_not_unsafe_reuse():
    stored_question = "Check current SQLite FTS5 support in 2026"
    requested_question = stored_question + "?"
    matched, reason = research_module._reuse_topic_match(
        requested_question,
        {
            "query": stored_question,
            "title": stored_question,
            "topicFingerprint": research_module._topic_fingerprint(stored_question),
        },
    )

    assert matched is True
    assert reason == "topic_fingerprint_variant_requires_review"


def test_research_broker_search_experience_excludes_spec_task_evidence(monkeypatch, tmp_path):
    from tests.core.research_scope_fixture import research_sessions

    _, create, _ = research_sessions(monkeypatch, tmp_path)
    context = create("spec-task-search-session")
    stored = research_module.store_evidence_bundle(
        {
            "evidenceBundleId": "bundle-approved-spec-task",
            "question": "TASK-001: Execute approved Spec spec_094d02189a1e4c20",
            "questionKind": "spec_task",
            "sourceKind": "spec_task",
            "confidence": "high",
            "authorityScore": 91,
            "summary": "Execution task evidence only.",
            "sourceMatrix": [{"title": "Approved Spec task", "url": "spec://spec_094d02189a1e4c20/tasks#TASK-001", "host": "local"}],
            "researchAnswerPack": {
                "answer": "Execution task evidence only.",
                "sources": [{"title": "Approved Spec task", "url": "spec://spec_094d02189a1e4c20/tasks#TASK-001", "host": "local"}],
                "score": {"qualityStatus": "usable_answer", "confidence": "high", "authorityScore": 91},
            },
        },
        ttl_seconds=3600,
        scope="spec-task-search-session",
    )
    assert stored["questionKind"] == "spec_task"

    payload = json.loads(
        research_module.research_broker.func(
            mode="search_experience",
            query="Execute approved Spec spec_094d02189a1e4c20",
            includeArchived=True,
            state={**context, "run_id": "run-spec-task-search"},
        )
    )

    assert payload["ok"] is True
    assert payload["items"] == []
    assert payload["reuseDecision"]["reuseDecision"] == "ignore"


def test_reuse_decision_ignores_generic_stopword_overlap():
    decision = research_module._experience_reuse_decision(
        [
            {
                "experiencePackId": "rxp-vendor-plugin",
                "title": "Research the latest vendor plugin SDK patterns",
                "query": "Research the latest vendor plugin SDK patterns and API exports",
                "confidence": "high",
                "sourcePolicy": "authoritative",
            }
        ],
        question="What are the current best practices for using Python pathlib in CLI tools? cite official sources.",
        source_policy="authoritative",
        freshness="auto",
    )

    assert decision["reuseDecision"] == "ignore"


def test_reuse_topic_match_rejects_adjacent_technical_topics_but_keeps_exact_topic():
    fts5_pack = {
        "experiencePackId": "rxp-fts5",
        "title": "SQLite FTS5 current official support",
        "query": "Check current SQLite FTS5 support in 2026",
        "topicFingerprint": research_module._topic_fingerprint(
            "Check current SQLite FTS5 support in 2026"
        ),
    }

    jsonb_match, jsonb_reason = research_module._reuse_topic_match(
        "Check current PostgreSQL JSONB support in 2026",
        fts5_pack,
    )
    python_match, python_reason = research_module._reuse_topic_match(
        "Check current Python support on Windows in 2026",
        fts5_pack,
    )
    exact_match, exact_reason = research_module._reuse_topic_match(
        "Check current SQLite FTS5 support in 2026",
        fts5_pack,
    )

    assert jsonb_match is False
    assert jsonb_reason == "distinctive_identifier_mismatch"
    assert python_match is False
    assert python_reason == "distinctive_identifier_mismatch"
    assert exact_match is True
    assert exact_reason == "topic_fingerprint_match"


def test_reused_experience_bundle_preserves_evidence_lineage():
    payload = research_module._bundle_from_reused_pack(
        {
            "experiencePackId": "rxp-lineage",
            "createdFromBundleId": "research-source-bundle",
            "title": "SQLite FTS5 support",
            "researchResult": "FTS5 support is documented by the cited sources.",
            "sourceMatrixDigest": [
                {"title": "SQLite FTS5", "url": "https://sqlite.org/fts5.html"}
            ],
            "claimDigest": [{"claim": "FTS5 is documented."}],
            "confidence": "high",
            "authorityScore": 90,
        },
        question="Check SQLite FTS5 support",
        reuse={"reuseDecision": "reuse"},
        deliverable="evidence_bundle",
    )

    assert payload["evidenceBundleId"] == "research-source-bundle"
    assert payload["experiencePackId"] == "rxp-lineage"
    assert payload["detailRef"] == "research://bundle/research-source-bundle"


def test_research_broker_refines_when_sources_are_not_readable(monkeypatch):
    from tests.core.test_research_agent import call
    _script_agent(monkeypatch, [
        call("search_research_sources", queries=["refinement source gap"]),
        call("search_research_sources", queries=["official primary source evidence"]),
        call("submit_research_answer", answer="Not enough evidence.", coverage="none", limitations=["No supported answer."]),
    ])
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 2, "maxRounds": 2}},
    )
    queries: list[str] = []

    def fake_search(**kwargs):
        query = kwargs["query"]
        queries.append(query)
        suffix = "primary" if "official primary source evidence" in query else "baseline"
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
    assert any("official primary source evidence" in query for query in queries)
    assert list(dict.fromkeys(queries)) == ["refinement source gap", "official primary source evidence"]
    assert len(queries) <= 3  # One bounded transport fallback may repeat a query.



def test_research_broker_does_not_inject_date_quota_repairs_before_semantic_review(monkeypatch):
    _search_then_no_answer(monkeypatch, "Python pathlib CLI semantics")
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 2, "maxRounds": 2}},
    )
    queries: list[str] = []
    read_urls: list[str] = []

    def fake_search(**kwargs):
        queries.append(kwargs["query"])
        return json.dumps({"ok": True, "provider": "fake", "results": []})

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps({"ok": False, "error": "fixture unavailable"})

    monkeypatch.setattr(
        research_module,
        "web_read",
        SimpleNamespace(func=fake_read),
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="What are the current Python pathlib CLI semantics?",
            maxShards=1,
            maxRounds=2,
            state={"run_id": "run-temporal-refine"},
        )
    )

    repair_queries = payload["researchLoopState"]["rounds"][0]["queries"]
    assert len(payload["researchLoopState"]["rounds"]) == 1
    assert queries == ["Python pathlib CLI semantics"]
    assert all(
        "last updated" not in query
        and "release notes changelog version history" not in query
        and "current official documentation" not in query
        for query in repair_queries
    )
    assert read_urls == []
    assert not any("site:peps.python.org" in query for query in queries)


def test_research_broker_reads_explicit_direct_official_url_without_search_fallback(monkeypatch):
    monkeypatch.setattr(
        research_module.storage,
        "get_supervisor_config",
        lambda: {"research": {"enabled": True, "defaultShardCount": 1, "maxShardCount": 2, "maxRounds": 2}},
    )
    queries: list[str] = []
    read_urls: list[str] = []

    def fake_search(**kwargs):
        queries.append(kwargs["query"])
        return json.dumps({"ok": True, "provider": "fake", "results": []})

    def fake_read(**kwargs):
        read_urls.append(kwargs["url"])
        return json.dumps(
            {
                "ok": True,
                "title": "Official path protocol",
                "status": 200,
                "text": "Pathlib PathLike command-line path protocol evidence. " * 40,
                "publishedAt": "2026-07-01T00:00:00Z",
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question=(
                "Read https://docs.python.org/3/library/pathlib.html and explain "
                "the current Python pathlib CLI semantics."
            ),
            maxShards=1,
            maxRounds=2,
            state={"run_id": "run-temporal-direct-success"},
        )
    )

    assert "https://docs.python.org/3/library/pathlib.html" in read_urls
    assert not any("site:peps.python.org" in query for query in queries)
    assert len(payload["researchLoopState"]["rounds"]) == 1
    assert payload["researchLoopState"]["stopReason"] != "architect_evidence_repair_completed"


def test_research_broker_video_policy_uses_popularity_signals_and_stays_compact(monkeypatch):
    _search_then_no_answer(monkeypatch, "Seedance video reference")
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

    assert len(output) < 36000
    assert payload["sourceMatrix"][0]["url"].startswith("https://www.youtube.com/")
    assert payload["deliveryReady"] is False
    assert payload.get("omitted", {}).get("shardsOmitted", 0) >= 0


@pytest.mark.parametrize("_iteration", range(10))
def test_parallel_search_shards_prefer_working_provider_and_bound_failed_route(
    monkeypatch,
    _iteration,
):
    slow_provider_attempts = 0
    fast_provider_attempts = 0
    observed_hints: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    counter_lock = threading.Lock()
    initial_barrier = threading.Barrier(
        research_module._RESEARCH_MAX_PARALLEL_SEARCH_SHARDS
    )

    def fake_source_router_search(**kwargs):
        nonlocal slow_provider_attempts, fast_provider_attempts
        preferred = tuple(kwargs.get("preferred_providers") or ())
        excluded = tuple(kwargs.get("excluded_providers") or ())
        observed_hints.append((preferred, excluded))
        if not preferred and not excluded:
            initial_barrier.wait(timeout=2)
        attempts = []
        with counter_lock:
            if "slow" not in excluded:
                slow_provider_attempts += 1
                attempts.append(
                    {
                        "provider": "slow",
                        "status": "error",
                        "failureClass": "network_timeout",
                    }
                )
            else:
                attempts.append(
                    {
                        "provider": "slow",
                        "status": "skipped",
                        "failureClass": "provider_circuit_open",
                    }
                )
            fast_provider_attempts += 1
        attempts.append({"provider": "fast", "status": "ok", "resultCount": 0})
        return json.dumps(
            {
                "ok": True,
                "provider": "fast",
                "results": [],
                "providerAttemptMatrix": attempts,
            }
        )

    monkeypatch.setattr(research_module, "source_router_search", fake_source_router_search)
    ledger = research_module._ResearchReadAttemptLedger(question="provider pressure")
    shards = [
        {"shardId": f"pressure-{index}", "kind": "baseline", "query": f"query {index}"}
        for index in range(30)
    ]

    completed = research_module._run_search_shards(
        shards,
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="provider-pressure",
        read_attempt_ledger=ledger,
    )

    assert len(completed) == 30
    assert fast_provider_attempts == 30
    assert slow_provider_attempts == research_module._RESEARCH_MAX_PARALLEL_SEARCH_SHARDS
    assert any(hints[0][:1] == ("fast",) for hints in observed_hints[2:])
    assert any("slow" in hints[1] for hints in observed_hints[2:])
    snapshot = ledger.snapshot()
    assert snapshot["searchProviderStates"]["fast"]["successes"] == 30
    assert snapshot["searchProviderCircuitSkipCount"] >= 1



def test_empty_query_evidence_does_not_disable_last_reachable_search_provider():
    ledger = research_module._ResearchReadAttemptLedger(question="比较两个互不相同的法规")
    ledger.record_search_payload({"ok": True, "provider": "bing_cn"})
    ledger.record_search_evidence_outcome("bing_cn", accepted_evidence_count=0)
    ledger.record_search_payload({
        "ok": False,
        "providerAttemptMatrix": [{"provider": "private_search", "status": "failed", "failureClass": "credential_missing"}],
    })
    hints = ledger.search_route_hints()
    assert hints["preferredProviders"] == ["bing_cn"]
    assert "bing_cn" not in hints["excludedProviders"]
    assert "private_search" in hints["excludedProviders"]
    assert ledger.snapshot()["searchProviderStates"]["bing_cn"]["evidenceFailures"] == 1



def test_short_cited_answer_is_not_erased_by_process_word_heuristic():
    answer = "本次调研确认：该办法未提供统一的申请期限，不能据此断言无需申请。[S1]"
    assert research_module._is_low_quality_research_answer(answer) is False
    assert research_module._is_low_quality_research_answer("本次调研无法获取目标文献，建议重新调研。") is True
    # Padding a process-only failure does not turn it into an answer.
    assert research_module._is_low_quality_research_answer("本次调研无法获取目标文献。" * 150) is True
