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
)


_should_try_context7_source_impl = research_module._should_try_context7_source


@pytest.fixture(autouse=True)
def _isolated_research_ledger(monkeypatch, tmp_path, request):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    monkeypatch.setattr(research_module, "_should_try_context7_source", lambda *args, **kwargs: False)


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


def _high_quality_architect_pack(**kwargs):
    sources = list(kwargs.get("source_matrix") or [])[:TARGET_RESEARCH_SOURCE_COUNT]
    fetched = research_module._fetched_source_map(list(kwargs.get("shards") or []))
    claim_labels = (
        "scope alpha",
        "architecture bravo",
        "authority charlie",
        "dataset delta",
        "timeline echo",
        "conflict foxtrot",
        "risk golf",
        "decision hotel",
    )
    claims = []
    for index in range(1, max(len(sources), MIN_RESEARCH_CLAIM_COUNT) + 1):
        source_position = ((index - 1) % len(sources)) + 1
        source = sources[source_position - 1]
        citation_key = str(source.get("citationKey") or f"S{index}").strip("[]")
        body = str(
            (fetched.get(source.get("url")) or {}).get("text")
            or _test_source_body(source, source_position)
        )
        excerpt = " ".join(body.split())
        if len(excerpt) > 180:
            excerpt = excerpt[:180].rsplit(" ", 1)[0]
        claims.append({
            "claimId": f"claim-{index}",
            "claim": (
                f"{claim_labels[index - 1]} source record: "
                f"{excerpt[:60].rstrip()}"
            ),
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "sourceId": source.get("sourceId"),
                    "url": source.get("url"),
                    "citationKey": citation_key,
                }
            ],
            "confidence": "high",
            "evidenceExcerptKey": f"{citation_key}:E1",
            "evidenceExcerpt": excerpt,
        })
    pack = {
        "reviewDecision": "accept",
        "reviewReasons": ["八个来源分别支撑关键结论，答案达到正常深度目标。"],
        "headline": "高质量 Web Research Architect 结论",
        "researchResult": _high_quality_answer(len(sources)),
        "claimTable": claims,
        "answerOutline": [
            {
                "sectionId": f"planned-{index}",
                "title": title,
                "objective": title,
                "claimIds": [f"claim-{index * 2 - 1}", f"claim-{index * 2}"],
            }
            for index, title in enumerate(("结论与范围", "机制与证据", "时效与冲突", "风险与行动"), start=1)
        ],
        "conflictMatrix": [],
        "missingEvidence": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "assumptions": [],
        "asOf": "2026-07-28T12:00:00Z",
    }
    review_template = {
            "reviewDecision": "accept",
            "reviewReasons": ["Question coverage, claim entailment, and freshness are adequate."],
            "questionCoverage": True,
            "claimEntailment": True,
            "freshnessAdequacy": True,
            "unsupportedClaims": [],
            "criticalMissingEvidence": [],
            "recommendedNextQueries": [],
    }
    validation_payload = {
        "question": kwargs.get("question") or "research runtime evidence contract",
        "freshness": kwargs.get("freshness") or "auto",
        "answer": pack["researchResult"],
        "reviewDecision": "accept",
        "sourceUrls": sources,
        "claimTable": claims,
        "asOf": pack["asOf"],
    }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "test-independent-reviewer"),
        ("adversarial", "test-adversarial-reviewer"),
    ):
        review = {**review_template, "reviewMode": review_mode}
        review.update(
            research_module.build_research_review_binding(
                validation_payload,
                reviewer_model_id=reviewer_model_id,
                reviewed_at="2026-07-28T12:00:00Z",
            )
        )
        consensus_reviews.append(review)
    independent_review = {
        **consensus_reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": len(consensus_reviews),
        "consensusReviewerModelIds": [review["reviewerModelId"] for review in consensus_reviews],
        "consensusReviews": consensus_reviews,
    }
    pack["_independentReview"] = independent_review
    return pack


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
    assert "Research orchestration, quality policy, stage schemas" in research_architect.system_prompt
    assert "Query-plan stage" not in research_architect.system_prompt
    assert "hard rejection floor" not in research_architect.system_prompt
    assert "3000" not in research_architect.system_prompt
    assert "5000" not in research_architect.system_prompt


def test_research_runtime_stage_prompt_does_not_read_managed_agent_markdown(monkeypatch):
    def fail_if_loaded(_agent_id):
        raise AssertionError("managed Agent Markdown must not enter a Runtime-bound stage")

    monkeypatch.setattr(research_module.storage, "get_agent", fail_if_loaded)

    prompt = research_module._research_agent_stage_system_prompt(
        "Return only the active test schema.",
        stage="evidence_plan",
    )

    assert "[RESEARCH-RUNTIME-CONTRACT " in prompt
    assert "stage=evidence_plan" in prompt
    assert "Return only the active test schema." in prompt


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


def test_source_quality_gate_rejects_pep_zero_navigation_index():
    url = "https://peps.python.org/pep-0000/"
    quality = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="PEP 0 - Index of Python Enhancement Proposals",
        snippet="Navigation index for Python packaging metadata proposals and their status.",
    )
    index_rows = "\n".join(
        f"PEP {number} - Python packaging metadata proposal {number} - Active"
        for number in range(1, 90)
    )
    body = (
        "PEP 0 - Index of Python Enhancement Proposals\n"
        "Navigation - Index - Modules\n"
        "This page lists proposal numbers, titles, owners, and status values.\n"
        f"{index_rows}"
    )

    assert len(body) >= 1200
    gate = research_module._source_quality_gate(
        question="What is the current status of Python packaging metadata proposals?",
        result={
            "title": "PEP 0 - Index of Python Enhancement Proposals",
            "url": url,
            "snippet": "Python packaging metadata proposal status index",
            "sourceQualityHints": quality,
        },
        read_payload={"ok": True, "status": 200, "text": body},
        source_policy="authoritative",
    )

    assert gate["qualityDimensions"]["relevance"] >= 8
    assert gate["selectedForEvidence"] is False
    assert any(marker in gate["rejectedReason"].lower() for marker in ("navigation", "index"))


def test_authoritative_policy_keeps_relevant_secondary_body_without_promoting_authority():
    url = "https://docs.random-unverified.example/python-pathlib"
    quality = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Python pathlib command-line guidance",
        snippet="Python pathlib CLI error handling",
    )
    gate = research_module._source_quality_gate(
        question="Python pathlib CLI error handling",
        result={
            "title": "Python pathlib command-line guidance",
            "url": url,
            "snippet": "Python pathlib CLI error handling",
            "sourceQualityHints": quality,
        },
        read_payload={"ok": True, "text": "Python pathlib CLI error handling and path validation. " * 30},
        source_policy="authoritative",
    )

    assert quality["authorityScore"] == 40
    assert gate["selectedForEvidence"] is True
    assert gate["rejectedReason"] == ""


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


def test_context7_is_only_used_for_software_documentation_not_generic_primary_policy():
    assert _should_try_context7_source_impl(
        "How does Python pathlib integrate with argparse?",
        "authoritative",
    ) is True
    assert _should_try_context7_source_impl(
        "EU AI Act GPAI technical documentation obligations under Annex XI",
        "official_primary",
    ) is False


def test_authoritative_policy_accepts_public_institution_host_with_readable_evidence():
    url = "https://www.nist.gov/publications/current-security-guidance"
    quality = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Current security guidance",
        snippet="Official security controls and implementation guidance",
    )
    gate = research_module._source_quality_gate(
        question="What are the current official security controls and implementation requirements?",
        result={
            "title": "Current security guidance",
            "url": url,
            "snippet": "Official security controls and implementation requirements",
            "sourceQualityHints": quality,
        },
        read_payload={
            "ok": True,
            "text": "Official security controls, implementation requirements, scope, dates, and limitations. " * 30,
        },
        source_policy="authoritative",
    )

    assert quality["authorityTier"] == "primary"
    assert "official_public_institution_host" in quality["reasons"]
    assert gate["selectedForEvidence"] is True


def test_cataloged_secondary_long_markdown_table_is_not_mislabeled_as_navigation():
    url = "https://treyhunner.com/2018/12/why-you-should-be-using-pathlib/"
    quality = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Why you should be using pathlib",
        snippet="Practical pathlib guidance for Python command-line programs",
    )
    body = (
        "Practical pathlib guidance for Python CLI programs, including path joining, resolution, and errors.\n"
        + "\n".join(f"| Path operation {index} | portable pathlib behavior |" for index in range(25))
    )
    gate = research_module._source_quality_gate(
        question="Python pathlib CLI path joining resolution and error handling",
        result={
            "title": "Why you should be using pathlib",
            "url": url,
            "snippet": "Practical pathlib guidance for Python command-line programs",
            "sourceQualityHints": quality,
        },
        read_payload={"ok": True, "text": body},
        source_policy="authoritative",
    )

    assert quality["catalogSourceId"] == "developer_education_secondary"
    assert gate["selectedForEvidence"] is True


def test_source_quality_gate_rejects_short_title_shell_as_unreadable_body():
    quality = research_module._source_quality(
        "https://click.palletsprojects.com/en/stable/parameters/",
        allowed_domains=[],
        source_policy="authoritative",
        title="Parameters - Click Documentation",
        snippet="Click parameter types and path arguments.",
    )
    gate = research_module._source_quality_gate(
        question="Python CLI pathlib and Click parameter best practices",
        result={
            "title": "Parameters - Click Documentation",
            "url": "https://click.palletsprojects.com/en/stable/parameters/",
            "sourceQualityHints": quality,
        },
        read_payload={
            "ok": True,
            "text": "Parameters - Click Documentation. This page requires JavaScript to display the full documentation. " * 2,
        },
        source_policy="authoritative",
    )

    assert quality["authorityTier"] == "primary"
    assert gate["selectedForEvidence"] is False
    assert gate["rejectedReason"] == "readable_body_required"


def test_source_quality_gate_rejects_readable_length_error_page():
    url = "https://click.palletsprojects.com/en/stable/typing/"
    quality = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Page Not Found - Click Documentation",
        snippet="Click pathlib typing guidance",
    )
    gate = research_module._source_quality_gate(
        question="Click pathlib typing guidance",
        result={
            "title": "Page Not Found - Click Documentation",
            "url": url,
            "sourceQualityHints": quality,
        },
        read_payload={
            "ok": True,
            "status": 200,
            "title": "Page Not Found - Click Documentation",
            "text": "Click pathlib typing guidance was not found on this documentation route. " * 12,
        },
        source_policy="authoritative",
    )

    assert gate["selectedForEvidence"] is False
    assert gate["rejectedReason"] == "http_error_page"


@pytest.mark.parametrize(
    ("body_chars", "selected"),
    ((399, False), (400, True)),
)
def test_source_quality_gate_enforces_readable_body_boundary(body_chars, selected):
    url = "https://docs.python.org/3/library/pathlib.html"
    quality = research_module._source_quality(
        url,
        allowed_domains=[],
        source_policy="authoritative",
        title="Python pathlib path handling",
        snippet="Python pathlib path handling",
    )
    gate = research_module._source_quality_gate(
        question="Python pathlib path handling",
        result={
            "title": "Python pathlib path handling",
            "url": url,
            "sourceQualityHints": quality,
        },
        read_payload={"ok": True, "text": ("Python pathlib path handling " * 30)[:body_chars].ljust(body_chars, "x")},
        source_policy="authoritative",
    )

    assert gate["selectedForEvidence"] is selected


def test_explicit_developer_education_catalog_is_secondary_not_fake_primary():
    quality = research_module._source_quality(
        "https://realpython.com/python-pathlib/",
        allowed_domains=[],
        source_policy="authoritative",
        title="Python pathlib tutorial",
        snippet="Practical path handling examples and caveats for Python applications.",
    )
    gate = research_module._source_quality_gate(
        question="Python pathlib practical path handling examples and caveats",
        result={
            "title": "Python pathlib tutorial",
            "url": "https://realpython.com/python-pathlib/",
            "snippet": "Practical path handling examples and caveats for Python applications.",
            "sourceQualityHints": quality,
        },
        read_payload={
            "ok": True,
            "text": "Python pathlib practical path handling examples and caveats for command-line applications. " * 20,
        },
        source_policy="authoritative",
    )

    assert quality["catalogSourceId"] == "developer_education_secondary"
    assert quality["authorityTier"] == "secondary"
    assert 55 <= quality["authorityScore"] < 80
    assert gate["selectedForEvidence"] is True


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
    assert research_module.research_source_has_dated_evidence({"temporalEvidence": temporal}) is False


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


def test_parallel_facets_reuse_one_long_document_read_for_distinct_excerpts(monkeypatch):
    url = "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng"
    body = (
        "Navigation and recital material. " * 3_000
        + "\nArticle 51 systemic-risk threshold is 10^25 FLOPs for a general-purpose AI model.\n"
        + "Unrelated operative provisions. " * 3_000
        + "\nArticle 53 copyright policy must account for the Article 4(3) opt-out.\n"
    )
    read_urls: list[str] = []

    def fake_search(**kwargs):
        return json.dumps(
            {
                "ok": True,
                "provider": "offline-fixture",
                "results": [
                    {
                        "title": "Regulation (EU) 2024/1689",
                        "url": url,
                        "snippet": kwargs["query"],
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
                "title": "Regulation (EU) 2024/1689",
                "text": body,
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(research_module, "web_read", SimpleNamespace(func=fake_read))
    question = (
        "1. [systemic-risk] Verify Article 51 systemic-risk threshold 10^25 FLOPs.\n"
        "2. [copyright] Verify Article 53 copyright policy and Article 4(3) opt-out."
    )
    ledger = research_module._ResearchReadAttemptLedger(question=question)
    completed = research_module._run_search_shards(
        [
            {
                "shardId": "systemic",
                "kind": "facet:systemic-risk",
                "researchFacetId": "systemic-risk",
                "query": "Article 51 systemic-risk threshold 10^25 FLOPs",
                "evidenceQuery": "Article 51 systemic-risk threshold 10^25 FLOPs",
            },
            {
                "shardId": "copyright",
                "kind": "facet:copyright",
                "researchFacetId": "copyright",
                "query": "Article 53 copyright policy Article 4(3) opt-out",
                "evidenceQuery": "Article 53 copyright policy Article 4(3) opt-out",
            },
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="parallel-facet-projection-test",
        read_attempt_ledger=ledger,
    )

    assert read_urls == [url]
    assert "10^25 FLOPs" in completed[0]["fetchedTopSources"][0]["text"]
    assert "Article 4(3) opt-out" in completed[1]["fetchedTopSources"][0]["text"]
    candidates = research_module._research_source_candidates(
        question,
        completed,
        source_policy="authoritative",
    )
    assert len(candidates) == 1
    assert candidates[0]["researchFacetIds"] == ["systemic-risk", "copyright"]
    facet_stats = research_module._selected_facet_evidence_stats(
        question,
        completed,
        source_policy="authoritative",
    )
    assert facet_stats["complete"] is True
    assert facet_stats["missingFacetIds"] == []
    assert ledger.snapshot()["networkAttemptCount"] == 1
    assert ledger.snapshot()["cachedProjectionCount"] == 1


@pytest.mark.parametrize("read_title", ["Project compatibility guide (draft)", "Project compatibility guide v2", ""])
def test_source_candidate_title_comes_from_read_document_not_search_label(read_title):
    import copy

    url = "https://docs.example.org/project/compatibility"
    shard = {
        "shardId": "read-title", "query": "Project compatibility guide",
        "results": [{"url": url, "title": "Project compatibility guide FINAL v1", "snippet": "Compatibility guide."}],
        "fetchedTopSources": [{
            "ok": True, "url": url, "title": read_title,
            "text": "Project compatibility guide: versions and limitations are described in this draft document. " * 8,
        }],
    }
    original = copy.deepcopy(shard)
    candidates = research_module._research_source_candidates(
        "Project compatibility guide", [shard], source_policy="balanced",
    )
    assert len(candidates) == 1
    assert candidates[0]["result"]["title"] == (read_title or original["results"][0]["title"])
    assert candidates[0]["result"]["url"] == url
    assert shard == original


def test_read_source_projects_body_evidence_to_other_explicit_facets():
    url = "https://digital-strategy.ec.europa.eu/en/faqs/gpai-evidence"
    body = (
        "The GPAI compliance dates are 2 August 2025, 2 August 2026, and 2 August 2027. "
        "Article 51 sets the GPAI systemic-risk threshold at 10^25 FLOPs and permits "
        "Commission designation. Existing GPAI models have a transition deadline. "
    ) * 12
    question = (
        "1. [timeline] Verify GPAI compliance dates: 2 August 2025, 2 August 2026, and 2 August 2027.\n"
        "2. [systemic-risk] Verify the Article 51 GPAI systemic-risk threshold of 10^25 FLOPs and Commission designation.\n"
        "3. [transition] Verify the existing GPAI model transition deadline."
    )
    shards = [
        {
            "shardId": "transition-only-discovery",
            "kind": "facet:transition",
            "researchFacetId": "transition",
            "query": "existing GPAI model transition deadline",
            "evidenceQuery": "existing GPAI model transition deadline",
            "provider": "offline-fixture",
            "results": [
                {
                    "title": "Official GPAI evidence",
                    "url": url,
                    "snippet": "Existing GPAI model transition deadline.",
                    "sourceQualityHints": {"authorityScore": 90, "tier": "primary"},
                }
            ],
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": url,
                    "finalUrl": url,
                    "title": "Official GPAI evidence",
                    "text": body,
                }
            ],
        }
    ]

    candidates = research_module._research_source_candidates(
        question,
        shards,
        source_policy="authoritative",
    )

    assert len(candidates) == 1
    assert set(candidates[0]["researchFacetIds"]) == {
        "timeline",
        "systemic-risk",
        "transition",
    }
    assert {
        view["researchFacetId"]: view["evidenceQuery"]
        for view in candidates[0]["evidenceViews"]
    } == {
        "timeline": "Verify GPAI compliance dates: 2 August 2025, 2 August 2026, and 2 August 2027.",
        "systemic-risk": "Verify the Article 51 GPAI systemic-risk threshold of 10^25 FLOPs and Commission designation.",
        "transition": "existing GPAI model transition deadline",
    }
    facet_stats = research_module._selected_facet_evidence_stats(
        question,
        shards,
        source_policy="authoritative",
    )
    assert facet_stats["complete"] is True


def test_source_gate_rejects_obvious_root_subject_collision_across_all_shards():
    question = (
        "1. [timeline] Verify the GPAI obligation timeline.\n"
        "2. [threshold] Verify the GPAI systemic-risk threshold.\n"
        "3. [code] Verify the GPAI Code of Practice."
    )
    repair_query = "Article regulation implementation evidence"
    ema_url = "https://www.ema.europa.eu/article-30-referral"
    commission_url = "https://digital-strategy.ec.europa.eu/general-purpose-ai-obligations"
    ema_body = (
        "Article 30 referral regulation implementation evidence for medicinal products. "
        "The European Medicines Agency coordinates the referral procedure. "
    ) * 20
    commission_body = (
        "General-purpose AI obligations under the AI Act. Article regulation implementation "
        "evidence covers providers, systemic risk, and the Code of Practice. "
    ) * 20
    repair_shard = {
        "shardId": "architect-repair",
        "kind": "architect_evidence_repair",
        "query": repair_query,
        "evidenceQuery": repair_query,
        "results": [
            {
                "title": "Article 30 referral",
                "url": ema_url,
                "snippet": "Article regulation implementation evidence",
                "sourceQualityHints": {"authorityScore": 90, "tier": "primary"},
            },
            {
                "title": "General-purpose AI obligations",
                "url": commission_url,
                "snippet": "Article regulation implementation evidence",
                "sourceQualityHints": {"authorityScore": 90, "tier": "primary"},
            },
        ],
        "fetchedTopSources": [
            {"ok": True, "url": ema_url, "finalUrl": ema_url, "title": "Article 30 referral", "text": ema_body},
            {
                "ok": True,
                "url": commission_url,
                "finalUrl": commission_url,
                "title": "General-purpose AI obligations",
                "text": commission_body,
            },
        ],
    }

    candidates = research_module._research_source_candidates(
        question,
        [repair_shard],
        source_policy="authoritative",
    )
    by_url = {candidate["url"]: candidate for candidate in candidates}

    assert by_url[ema_url]["gate"]["selectedForEvidence"] is False
    assert by_url[ema_url]["gate"]["rejectedReason"] == "research_root_subject_mismatch"
    assert by_url[commission_url]["gate"]["selectedForEvidence"] is True

    initial_shard = {**repair_shard, "kind": "facet:timeline"}
    initial_candidates = research_module._research_source_candidates(
        question,
        [initial_shard],
        source_policy="authoritative",
    )
    assert {
        candidate["url"]: candidate["gate"]["selectedForEvidence"]
        for candidate in initial_candidates
    }[ema_url] is False


def test_chinese_document_title_is_a_root_subject_anchor_across_quality_layers():
    question = (
        "依据中国政府公开资料，概括《生成式人工智能服务管理暂行办法》对训练数据、"
        "生成内容治理、用户权益和投诉举报提出的要求。"
    )
    relevant = {
        "title": "生成式人工智能服务管理暂行办法",
        "url": "https://www.gov.cn/zhengce/content/2023-07/13/content_6891752.htm",
        "evidenceCandidates": [
            {
                "text": "生成式人工智能服务管理暂行办法规定了训练数据、生成内容治理和投诉举报要求。",
                "relevanceScore": 100,
            }
        ],
    }
    unrelated = {
        "title": "个人信息处理活动一般合规提示",
        "url": "https://court.gov.cn/privacy-guidance.html",
        "evidenceCandidates": [
            {
                "text": "个人信息处理者应当向用户说明处理目的，并提供一般投诉渠道。",
                "relevanceScore": 30,
            }
        ],
    }

    assert "生成式人工智能服务管理暂行办法" in research_module._research_root_subject_anchors(question)
    assert research_module._research_source_matches_root_subject(
        question,
        title=relevant["title"],
        url=relevant["url"],
        text=relevant["evidenceCandidates"][0]["text"],
    ) is True
    assert research_module._research_source_matches_root_subject(
        question,
        title=unrelated["title"],
        url=unrelated["url"],
        text=unrelated["evidenceCandidates"][0]["text"],
    ) is False
    assert research_module._architect_source_subject_focused(relevant, question) is True
    assert research_module._architect_source_subject_focused(unrelated, question) is False
    assert research_module._research_source_matches_root_subject(
        question,
        title="国家网信办等七部门联合公布《 生成式人工智能服务管理暂行 ...",
        url="https://www.cac.gov.cn/2023-07/13/example.htm",
    ) is True
    assert research_module._research_source_matches_root_subject(
        question,
        title="国家网信办发布生成式人工智能产业发展与安全治理专题 ...",
        url="https://www.cac.gov.cn/topic/example.htm",
    ) is False
    assert research_module._research_source_matches_root_subject(
        question,
        title="中华人民共和国司法部",
        url="https://www.moj.gov.cn/policy/example.htm",
        text=(
            "《生成式人工智能服务管理暂行办法》\n"
            "第一条 为了促进生成式人工智能健康发展，制定本办法。\n"
            "第二条 向境内公众提供相关服务适用本办法。\n"
            "第三条 国家坚持发展和安全并重。"
        ),
    ) is True
    assert research_module._research_source_matches_root_subject(
        question,
        title="政务领域人工智能大模型部署应用指引",
        url="https://www.cac.gov.cn/guidance/example.htm",
        text="政务部门应当遵守《生成式人工智能服务管理暂行办法》等相关规定。",
    ) is False


def test_authority_cannot_replace_body_relevance_for_chinese_research():
    question = (
        "概括《生成式人工智能服务管理暂行办法》对训练数据、个人信息、内容标识、"
        "算法备案、用户权益和投诉举报的要求。"
    )
    unrelated_body = (
        "本页介绍传统医药管理机构的行政职责、年度工作计划和一般政务公开事项。"
        "公众可以通过网站提交意见，工作人员会依法处理。"
    ) * 30
    gate = research_module._source_quality_gate(
        question=question,
        result={
            "url": "https://www.natcm.gov.cn/policy/article-15.html",
            "title": "第十五条",
            "snippet": "政府部门公开的政策页面",
            "sourceQualityHints": {
                "authorityScore": 95,
                "authorityTier": "primary",
            },
        },
        read_payload={"ok": True, "status": 200, "text": unrelated_body},
        source_policy="authoritative",
    )

    assert gate["selectedForEvidence"] is False
    assert gate["rejectedReason"] == "relevance_too_low"


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


@pytest.mark.parametrize(
    "diagnostic",
    [
        f"Research should continue until at least 8 readable sources are available; {research_module.MIN_RESEARCH_SOURCE_COUNT} is only the rejection floor.",
        f"Research needs at least {research_module.MIN_RESEARCH_SOURCE_COUNT} readable sources before a reviewed answer can be delivered.",
        f"Architect-answerable source minimum not met: 3/{research_module.MIN_RESEARCH_SOURCE_COUNT}.",
        "No source-backed claims were extracted from readable page bodies.",
        "research_source_transport_exhausted",
    ],
)
def test_runtime_gate_diagnostics_never_become_research_queries(diagnostic):
    assert research_module._normalize_research_search_query(diagnostic) == ""
    assert research_module._research_critical_gap_queries(
        "LangChain latest capabilities",
        [diagnostic],
    ) == []


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


def test_python_docs_document_family_prefers_current_english_canonical_url(monkeypatch):
    old_version = "https://docs.python.org/3.11/library/pathlib.html"
    french = "https://docs.python.org/fr/3/library/pathlib.html?highlight=resolve"
    ukrainian = "https://docs.python.org/uk/3/library/pathlib.html"
    canonical = "https://docs.python.org/3/library/pathlib.html"
    variants = [old_version, french, ukrainian, canonical]
    identities = [research_module._research_document_identity(url) for url in variants]

    assert len(set(identities)) == 1
    assert research_module._canonical_source_url(french) == french
    assert research_module._canonical_source_url(old_version) != research_module._canonical_source_url(canonical)

    body = "Python pathlib Path.resolve behavior, version boundaries, and filesystem semantics. " * 20
    results = [
        {
            "title": f"Python pathlib documentation variant {index}",
            "url": url,
            "snippet": "Python pathlib Path.resolve behavior and version boundaries",
            "sourceQualityHints": {
                "host": "docs.python.org",
                "authorityScore": 85,
                "authorityTier": "primary",
                "tier": "A",
            },
        }
        for index, url in enumerate(variants, start=1)
    ]
    fetched = [
        {
            "url": url,
            "ok": True,
            "status": 200,
            "title": result["title"],
            "text": body,
            "textPreview": body[:1200],
            "contentChars": len(body),
        }
        for url, result in zip(variants, results)
    ]
    monkeypatch.setattr(
        research_module,
        "_web_research_architect_pack",
        lambda **kwargs: {
            "reviewDecision": "revise",
            "headline": "Offline document-family fixture",
            "answer": "",
            "claimTable": [],
            "conflictMatrix": [],
            "missingEvidence": ["Only one document family is present."],
            "criticalMissingEvidence": ["Independent corroboration is required."],
            "recommendedNextQueries": [],
            "assumptions": [],
        },
    )

    bundle = research_module._synthesize_bundle(
        question="What are the current pathlib Path.resolve behavior and version boundaries?",
        research_intent="offline document-family regression",
        source_policy="authoritative",
        freshness="latest",
        shards=[
            {
                "shardId": "python-doc-family",
                "provider": "offline-fixture",
                "results": results,
                "fetchedTopSources": fetched,
            }
        ],
        deliverable="detailed answer",
    )

    family_sources = [
        source
        for source in bundle["sourceMatrix"]
        if research_module._research_document_identity(source["url"]) == identities[0]
    ]
    assert [source["url"] for source in family_sources] == [canonical]


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


def test_source_candidate_dedup_prefers_question_focused_read_over_longer_generic_read():
    question = "How should pathlib and Click validate CLI path arguments?"
    generic_url = "https://click.palletsprojects.com/en/stable/parameter-types?from=generic-search"
    focused_url = "https://click.palletsprojects.com/en/stable/parameter-types/"
    generic_body = (
        "Click exposes many CLI parameter types and command-line helpers. "
        "This generic overview discusses application structure and console output. "
        * 80
    )
    focused_body = (
        "Click Path validates CLI path arguments. Its path_type option can return pathlib.Path objects, "
        "while file_okay, dir_okay, exists, and resolve_path control accepted filesystem inputs. "
        * 12
    )
    shards = [
        {
            "shardId": "generic-first",
            "results": [
                {
                    "url": generic_url,
                    "title": "Click parameter types overview",
                    "snippet": "Generic CLI parameter documentation.",
                    "sourceQualityHints": {"authorityScore": 85},
                }
            ],
            "fetchedTopSources": [
                {"url": generic_url, "ok": True, "title": "Click parameter types overview", "text": generic_body}
            ],
        },
        {
            "shardId": "exact-seed-later",
            "results": [
                {
                    "url": focused_url,
                    "title": "Click Path parameter type",
                    "snippet": "pathlib.Path-backed CLI path validation.",
                    "sourceQualityHints": {"authorityScore": 85},
                }
            ],
            "fetchedTopSources": [
                {"url": focused_url, "ok": True, "title": "Click Path parameter type", "text": focused_body}
            ],
        },
    ]

    candidates = research_module._research_source_candidates(
        question,
        shards,
        source_policy="authoritative",
    )

    assert len(candidates) == 1
    assert candidates[0]["requestedUrl"] == focused_url
    assert candidates[0]["readPayload"]["text"] == focused_body
    assert candidates[0]["bodyRelevance"] > research_module._source_relevance_score(
        question,
        title="Click parameter types overview",
        snippet="Generic CLI parameter documentation.",
        text=generic_body,
    )

    prompt_sources = research_module._research_architect_sources_for_prompt(
        [
            {
                "sourceId": "click-path",
                "title": "Click Path parameter type",
                "url": focused_url,
                "host": "click.palletsprojects.com",
                "authorityScore": 85,
                "selectedForEvidence": True,
                "sourceQualityGate": {"selectedForEvidence": True},
                "version": "8.5.x",
            }
        ],
        shards,
        question=question,
        freshness="current",
    )

    assert len(prompt_sources) == 1
    assert "path_type option can return pathlib.Path objects" in prompt_sources[0]["text"]
    assert "generic overview" not in prompt_sources[0]["text"].lower()


def test_research_architect_excludes_body_incidental_source_without_original_question_discovery_signal():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    weak_url = "https://guides.example.edu/apa"
    strong_url = "https://docs.example.org/pathlib-cli"
    source_matrix = [
        {
            "sourceId": "weak",
            "title": "APA citation style guide",
            "url": weak_url,
            "host": "guides.example.edu",
            "authorityScore": 90,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True, "qualityDimensions": {"relevance": 50}},
        },
        {
            "sourceId": "strong",
            "title": "pathlib for command-line applications",
            "url": strong_url,
            "host": "docs.example.org",
            "authorityScore": 85,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True, "qualityDimensions": {"relevance": 100}},
        },
    ]
    weak_body = ("APA references, citation paths, and one incidental CLI token. " * 20).strip()
    strong_body = ("pathlib Path values in a command-line interface with concrete CLI behavior. " * 20).strip()
    shards = [
        {
            "fetchedTopSources": [
                {"url": weak_url, "ok": True, "title": source_matrix[0]["title"], "text": weak_body},
                {"url": strong_url, "ok": True, "title": source_matrix[1]["title"], "text": strong_body},
            ]
        }
    ]

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
    )

    assert [source["url"] for source in selected] == [strong_url]


def test_research_architect_multi_facet_prompt_source_keeps_valid_read_receipt():
    question = "Compare the current GPAI timeline and transparency obligations."
    url = "https://commission.example/gpai-obligations"
    bodies = {
        "timeline": (
            "The GPAI compliance timeline records the applicable dates and transition boundary. "
            * 12
        ).strip(),
        "transparency": (
            "The GPAI transparency obligations cover documentation and copyright policy evidence. "
            * 12
        ).strip(),
    }
    source_matrix = [
        {
            "sourceId": "commission-gpai",
            "citationKey": "S1",
            "title": "GPAI obligations",
            "url": url,
            "host": "commission.example",
            "tier": "primary",
            "authorityScore": 95,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True},
            "researchFacetIds": ["timeline", "transparency"],
            "evidenceViews": [
                {
                    "shardId": "timeline",
                    "researchFacetId": "timeline",
                    "evidenceQuery": "GPAI compliance timeline",
                },
                {
                    "shardId": "transparency",
                    "researchFacetId": "transparency",
                    "evidenceQuery": "GPAI transparency obligations",
                },
            ],
        }
    ]
    shards = [
        {
            "shardId": shard_id,
            "evidenceQuery": f"GPAI {shard_id}",
            "fetchedTopSources": [
                {
                    "url": url,
                    "ok": True,
                    "title": "GPAI obligations",
                    "text": body,
                    "retrievedAt": "2026-07-29T00:00:00Z",
                }
            ],
        }
        for shard_id, body in bodies.items()
    ]

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    assert len(selected) == 1
    source = selected[0]
    assert source["text"] == source["text"].strip()
    assert source["contentChars"] == source["readEvidence"]["contentChars"]
    metrics = research_acceptance_metrics(
        {
            "question": question,
            "freshness": "current",
            "reviewDecision": "accept",
            "answer": "GPAI timeline and transparency evidence [S1].",
            "sourceUrls": selected,
            "claimTable": [],
            "asOf": "2026-07-29T00:00:00Z",
        }
    )
    assert metrics["readVerifiedSourceCount"] == 1


def test_research_architect_preserves_explicit_python_version_comparison_sources():
    urls = [
        "https://docs.python.org/3.8/library/pathlib.html",
        "https://docs.python.org/3.14/library/pathlib.html",
    ]
    body = "Version-specific pathlib behavior, dates, limitations, and migration evidence. " * 20
    source_matrix = [
        {
            "sourceId": f"source-{index}",
            "title": f"Python {version} pathlib",
            "url": url,
            "host": "docs.python.org",
            "tier": "A",
            "authorityScore": 95,
            "selectedForEvidence": True,
        }
        for index, (version, url) in enumerate(zip(("3.8", "3.14"), urls), start=1)
    ]
    shards = [
        {
            "fetchedTopSources": [
                {
                    "url": url,
                    "ok": True,
                    "title": source["title"],
                    "text": body,
                    "contentChars": len(body),
                }
                for url, source in zip(urls, source_matrix)
            ]
        }
    ]

    generic = research_module._research_architect_sources_for_prompt(source_matrix, shards)
    comparison = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question="Compare pathlib in Python 3.8 and Python 3.14.",
    )

    assert len(generic) == 1
    assert {source["url"] for source in comparison} == set(urls)


def test_research_architect_prompt_prioritizes_directly_relevant_sources():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    whats_new = [
        {
            "sourceId": f"whats-new-{version}",
            "title": f"What's New In Python {version}",
            "url": f"https://docs.python.org/3/whatsnew/{version}.html",
            "host": "docs.python.org",
            "authorityScore": 95,
            "selectedForEvidence": True,
            "sourceQualityGate": {
                "selectedForEvidence": True,
                "qualityDimensions": {"relevance": 100},
            },
        }
        for version in ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14")
    ]
    directly_relevant = [
        {
            "sourceId": f"direct-{index}",
            "title": title,
            "url": url,
            "host": url.split("/")[2],
            "authorityScore": authority,
            "selectedForEvidence": True,
            "sourceQualityGate": {
                "selectedForEvidence": True,
                "qualityDimensions": {"relevance": 50},
            },
        }
        for index, (title, url, authority) in enumerate(
            (
                ("pathlib object-oriented filesystem paths", "https://docs.python.org/3/library/pathlib.html", 95),
                ("argparse command-line arguments", "https://docs.python.org/3/library/argparse.html", 95),
                ("Python pathlib guide", "https://realpython.com/python-pathlib/", 65),
                ("Python command-line interfaces with argparse", "https://realpython.com/python-cli/", 65),
                ("Why you should use pathlib", "https://treyhunner.com/pathlib/", 60),
            ),
            start=1,
        )
    ]
    source_matrix = whats_new + directly_relevant
    body = "Python pathlib and command-line interface evidence with concrete behavior and limitations. " * 20
    shards = [
        {
            "fetchedTopSources": [
                {"url": source["url"], "ok": True, "title": source["title"], "text": body}
                for source in source_matrix
            ]
        }
    ]

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
    )

    selected_urls = {source["url"] for source in selected}
    assert len(selected) == min(
        len(source_matrix),
        research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT,
    )
    assert {source["url"] for source in directly_relevant}.issubset(selected_urls)
    assert len(selected_urls & {source["url"] for source in whats_new}) == len(whats_new)
    assert {
        source["url"] for source in selected[: len(directly_relevant)]
    } == {source["url"] for source in directly_relevant}


def test_research_architect_prompt_reserves_each_explicit_facet_before_source_cap():
    facet_ids = [
        "timeline",
        "threshold",
        "transparency",
        "transition",
        "code",
        "penalties",
        "practice",
    ]
    question = "Research every item below.\n" + "\n".join(
        f"{index}. [{facet_id}] Verify the current {facet_id} obligation and limitation."
        for index, facet_id in enumerate(facet_ids, start=1)
    )
    source_matrix: list[dict] = []
    shards: list[dict] = []
    source_index = 0
    for facet_index, facet_id in enumerate(facet_ids):
        source_count = 2 if facet_index < 5 else 1
        for duplicate_index in range(source_count):
            source_index += 1
            url = f"https://facet-{facet_index}-{duplicate_index}.example/{facet_id}"
            query = f"Verify the current {facet_id} obligation and limitation."
            title = f"Current {facet_id} evidence {duplicate_index}"
            body = (
                f"The {facet_id} obligation has a documented current rule, applicability "
                "condition, implementation consequence, and limitation. "
            ) * 20
            source_matrix.append(
                {
                    "sourceId": f"facet-source-{source_index}",
                    "title": title,
                    "url": url,
                    "host": f"facet-{facet_index}-{duplicate_index}.example",
                    "authorityScore": 95 - facet_index,
                    "selectedForEvidence": True,
                    "sourceQualityGate": {
                        "selectedForEvidence": True,
                        "qualityDimensions": {"relevance": 100},
                    },
                    "shardId": f"facet-shard-{source_index}",
                    "researchFacetId": facet_id,
                    "researchFacetIds": [facet_id],
                    "evidenceQuery": query,
                }
            )
            shards.append(
                {
                    "shardId": f"facet-shard-{source_index}",
                    "researchFacetId": facet_id,
                    "evidenceQuery": query,
                    "query": query,
                    "fetchedTopSources": [
                        {
                            "url": url,
                            "ok": True,
                            "title": title,
                            "text": body,
                        }
                    ],
                }
            )

    for extra_index in range(8):
        source_index += 1
        facet_id = facet_ids[extra_index % 5]
        url = f"https://high-rank-extra-{extra_index}.example/{facet_id}"
        query = f"Verify the current {facet_id} obligation and limitation."
        title = f"Highly ranked {facet_id} evidence {extra_index}"
        body = (
            f"The {facet_id} obligation has current official evidence and a documented limitation. "
        ) * 20
        source_matrix.append(
            {
                "sourceId": f"facet-source-{source_index}",
                "title": title,
                "url": url,
                "host": f"high-rank-extra-{extra_index}.example",
                "authorityScore": 100,
                "selectedForEvidence": True,
                "sourceQualityGate": {
                    "selectedForEvidence": True,
                    "qualityDimensions": {"relevance": 100},
                },
                "shardId": f"facet-shard-{source_index}",
                "researchFacetId": facet_id,
                "researchFacetIds": [facet_id],
                "evidenceQuery": query,
            }
        )
        shards.append(
            {
                "shardId": f"facet-shard-{source_index}",
                "researchFacetId": facet_id,
                "evidenceQuery": query,
                "query": query,
                "fetchedTopSources": [
                    {"url": url, "ok": True, "title": title, "text": body}
                ],
            }
        )

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    selected_facets = {
        facet_id
        for source in selected
        for facet_id in source.get("researchFacetIds") or []
    }
    expected_count = min(
        len(source_matrix),
        research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT,
    )
    assert len(selected) == expected_count
    assert selected_facets == set(facet_ids)


def test_research_architect_prompt_reserves_each_named_entity_for_a_shared_facet():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, models, tools, MCP, account pricing, privacy, and limitations."
    )
    entities = (
        ("OpenAI Codex CLI", "learn.chatgpt.com", "codex"),
        ("Claude Code", "docs.anthropic.com", "claude"),
        ("Gemini CLI", "ai.google.dev", "gemini"),
        ("GitHub Copilot CLI", "docs.github.com", "copilot"),
    )
    shared_query = (
        "OpenAI Codex CLI Claude Code Gemini CLI GitHub Copilot CLI "
        "account subscription pricing privacy limitations"
    )
    source_matrix: list[dict] = []
    shards: list[dict] = []
    governance_urls: set[str] = set()

    def add_source(
        *,
        label: str,
        host: str,
        slug: str,
        facet_id: str,
        query: str,
        suffix: str,
        authority: int,
    ) -> None:
        url = f"https://{host}/{slug}/{suffix}"
        title = f"{label} {suffix.replace('-', ' ')}"
        body = (
            f"{label} documents Windows installation, models, tools, MCP, account pricing, "
            f"privacy boundaries, limitations, and repository workflow for {suffix}. "
        ) * 18
        shard_id = f"{slug}-{suffix}"
        source_matrix.append(
            {
                "sourceId": shard_id,
                "title": title,
                "url": url,
                "host": host,
                "tier": "primary",
                "authorityScore": authority,
                "selectedForEvidence": True,
                "sourceQualityGate": {
                    "selectedForEvidence": True,
                    "qualityDimensions": {"relevance": 100},
                },
                "shardId": shard_id,
                "researchFacetId": facet_id,
                "researchFacetIds": [facet_id],
                "evidenceQuery": query,
            }
        )
        shards.append(
            {
                "shardId": shard_id,
                "kind": f"facet:{facet_id}",
                "researchFacetId": facet_id,
                "evidenceQuery": query,
                "query": query,
                "fetchedTopSources": [
                    {"url": url, "ok": True, "title": title, "text": body}
                ],
            }
        )
        if suffix == "account-pricing-privacy":
            governance_urls.add(url)

    for label, host, slug in entities:
        add_source(
            label=label,
            host=host,
            slug=slug,
            facet_id=f"{slug}-operations",
            query=f"{label} Windows installation models tools MCP repository workflow",
            suffix="operations",
            authority=92,
        )
        add_source(
            label=label,
            host=host,
            slug=slug,
            facet_id="account-pricing-privacy",
            query=shared_query,
            suffix="account-pricing-privacy",
            authority=88,
        )

    for index in range(20):
        add_source(
            label="GitHub Copilot CLI",
            host="docs.github.com",
            slug="copilot",
            facet_id="copilot-operations",
            query="GitHub Copilot CLI Windows installation models tools MCP repository workflow",
            suffix=f"high-rank-{index}",
            authority=100,
        )

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    assert len(selected) == research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT
    selected_urls = {source["url"] for source in selected}
    assert governance_urls.issubset(selected_urls)
    hints = research_module._catalog_official_entity_hints(question)
    covered_entities = set().union(
        *(
            research_module._research_source_entity_indexes(
                hints,
                host=source.get("host"),
                url=source.get("url"),
                title=source.get("title"),
                text=source.get("text"),
            )
            for source in selected
        )
    )
    assert covered_entities == set(range(4))


def test_research_architect_prompt_preserves_runtime_catalog_seed_reads():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, models, tools, MCP, account pricing, privacy, and limitations."
    )
    seeds = (
        (
            "openai-codex-cli",
            "operations",
            "OpenAI Codex CLI",
            "https://learn.chatgpt.com/docs/codex/cli",
        ),
        (
            "claude-code",
            "operations",
            "Claude Code",
            "https://code.claude.com/docs/en/quickstart",
        ),
        (
            "gemini-cli",
            "operations",
            "Gemini CLI",
            "https://geminicli.com/docs/get-started/installation/",
        ),
        (
            "github-copilot-cli",
            "operations",
            "GitHub Copilot CLI",
            "https://docs.github.com/en/copilot/how-tos/copilot-cli/"
            "set-up-copilot-cli/install-copilot-cli",
        ),
        (
            "github-copilot-cli",
            "governance",
            "GitHub Copilot CLI",
            "https://github.com/features/copilot/plans",
        ),
    )
    source_matrix: list[dict] = []
    shards: list[dict] = []
    seed_urls: set[str] = set()

    for entity_id, dimension, label, url in seeds:
        shard_id = f"shard_catalog_seed_{entity_id}-{dimension}"
        facet_id = f"{entity_id}-{dimension}"
        query = (
            f"{label} Windows installation models tools MCP repository workflow"
            if dimension == "operations"
            else f"{label} account subscription pricing privacy limitations"
        )
        title = f"{label} official {dimension} documentation"
        body = (
            f"{label} documents current Windows installation, models, tools, MCP, "
            f"account subscription pricing, privacy boundaries, and limitations. "
        ) * 24
        source_matrix.append(
            {
                "sourceId": f"seed-{entity_id}-{dimension}",
                "title": title,
                "url": url,
                "host": url.split("/")[2],
                "tier": "secondary",
                "authorityTier": "primary",
                "catalogCategory": (
                    "source_repo" if url.startswith("https://github.com/") else "official_docs"
                ),
                "catalogSourceId": "official-seed-fixture",
                "authorityScore": 10,
                "selectedForEvidence": True,
                "sourceQualityGate": {
                    "selectedForEvidence": True,
                    "qualityDimensions": {"relevance": 1},
                },
                "evidenceViews": [
                    {
                        "shardId": shard_id,
                        "researchFacetId": facet_id,
                        "evidenceQuery": query,
                    }
                ],
            }
        )
        shards.append(
            {
                "shardId": shard_id,
                "kind": "seed_url",
                "researchFacetId": facet_id,
                "evidenceQuery": query,
                "query": url,
                "fetchedTopSources": [
                    {"url": url, "ok": True, "title": title, "text": body}
                ],
            }
        )
        seed_urls.add(url)

    distractor_query = (
        "GitHub Copilot CLI Windows installation models tools MCP repository workflow"
    )
    for index in range(20):
        url = f"https://docs.github.com/en/copilot/reference/high-rank-{index}"
        title = f"GitHub Copilot CLI highly ranked reference {index}"
        body = (
            "GitHub Copilot CLI documents current Windows installation, models, tools, "
            "MCP, repository workflow, account pricing, privacy, and limitations. "
        ) * 24
        shard_id = f"copilot-high-rank-{index}"
        source_matrix.append(
            {
                "sourceId": shard_id,
                "title": title,
                "url": url,
                "host": "docs.github.com",
                "tier": "primary",
                "authorityScore": 100,
                "selectedForEvidence": True,
                "sourceQualityGate": {
                    "selectedForEvidence": True,
                    "qualityDimensions": {"relevance": 100},
                },
                "shardId": shard_id,
                "researchFacetId": "github-copilot-cli-operations",
                "evidenceQuery": distractor_query,
            }
        )
        shards.append(
            {
                "shardId": shard_id,
                "researchFacetId": "github-copilot-cli-operations",
                "evidenceQuery": distractor_query,
                "query": distractor_query,
                "fetchedTopSources": [
                    {"url": url, "ok": True, "title": title, "text": body}
                ],
            }
        )

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    assert len(selected) == research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT
    assert seed_urls.issubset({source["url"] for source in selected})
    selected_seeds = [source for source in selected if source["url"] in seed_urls]
    assert all(source["runtimeOfficialSeed"] is True for source in selected_seeds)
    assert all(
        research_module._architect_support_role(source) == "unknown"
        for source in selected_seeds
    )


def test_research_source_candidate_rejects_a_different_named_product():
    question = "Compare OpenAI Codex CLI and Claude Code on Windows installation."
    url = "https://learn.chatgpt.com/docs/codex/cli"
    body = (
        "OpenAI Codex CLI Windows installation npm command line agent workflow. "
        * 40
    )
    candidates = research_module._research_source_candidates(
        question,
        [
            {
                "shardId": "claude-windows",
                "kind": "facet:claude-windows",
                "researchFacetId": "claude-windows",
                "query": "Claude Code Windows installation official docs",
                "evidenceQuery": "Claude Code Windows installation official docs",
                "results": [
                    {
                        "url": url,
                        "title": "Codex CLI Windows installation",
                        "snippet": "OpenAI Codex CLI setup",
                        "sourceQualityHints": {
                            "authorityScore": 95,
                            "tier": "primary",
                        },
                    }
                ],
                "fetchedTopSources": [
                    {
                        "url": url,
                        "finalUrl": url,
                        "ok": True,
                        "title": "Codex CLI Windows installation",
                        "text": body,
                        "contentChars": len(body),
                    }
                ],
            }
        ],
        source_policy="multi_source_evidence",
    )

    assert len(candidates) == 1
    assert candidates[0]["gate"]["selectedForEvidence"] is False
    assert candidates[0]["gate"]["rejectedReason"] == "research_query_entity_mismatch"
    assert candidates[0]["researchFacetIds"] == []


def test_cli_comparison_source_identity_rejects_vendor_pages_and_incidental_body_mentions():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, MCP, pricing, privacy, and limitations."
    )
    hints = research_module._catalog_official_entity_hints(question)
    incidental_body = (
        "Navigation and comparison links mention OpenAI Codex CLI, Claude Code, "
        "Gemini CLI, and GitHub Copilot CLI. "
    ) * 30

    assert research_module._research_source_entity_indexes(
        hints,
        host="ai.google.dev",
        url="https://ai.google.dev/gemini-api/docs/api-key",
        title="Using Gemini API keys | Google AI for Developers",
        text=incidental_body,
        evidence_query="Gemini CLI account pricing privacy telemetry and limitations",
    ) == set()
    assert research_module._research_source_entity_indexes(
        hints,
        host="learn.microsoft.com",
        url="https://learn.microsoft.com/en-us/visualstudio/releases/2026/release-notes",
        title="Visual Studio 2026 release notes",
        text=incidental_body,
    ) == set()
    assert research_module._research_source_entity_indexes(
        hints,
        host="github.com",
        url="https://github.com/CursorTouch/Windows-MCP",
        title="Windows MCP",
        text=incidental_body,
    ) == set()


def test_cli_comparison_source_identity_accepts_product_scoped_pages_and_repositories():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, MCP, pricing, privacy, and limitations."
    )
    hints = research_module._catalog_official_entity_hints(question)
    cases = (
        (
            0,
            "learn.chatgpt.com",
            "https://learn.chatgpt.com/docs/codex/cli",
            "Codex CLI | ChatGPT Learn",
        ),
        (
            0,
            "userjot.com",
            "https://userjot.com/blog/openai-codex-pricing",
            "OpenAI Codex pricing and plan limits",
        ),
        (
            1,
            "code.claude.com",
            "https://code.claude.com/docs/en/setup",
            "Set up Claude Code",
        ),
        (
            2,
            "geminicli.com",
            "https://geminicli.com/extensions/",
            "Gemini CLI extensions",
        ),
        (
            2,
            "github.com",
            "https://github.com/google-gemini/gemini-cli",
            "Google Gemini CLI",
        ),
        (
            3,
            "docs.github.com",
            "https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli",
            "About GitHub Copilot CLI",
        ),
    )

    for entity_index, host, url, title in cases:
        assert research_module._research_source_entity_indexes(
            hints,
            host=host,
            url=url,
            title=title,
            text="Product-specific operational and governance evidence. " * 20,
        ) == {entity_index}


def test_cli_comparison_source_identity_accepts_relevant_generic_page_on_official_host():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on account pricing, privacy, and limitations."
    )
    hints = research_module._catalog_official_entity_hints(question)

    assert research_module._research_source_entity_indexes(
        hints,
        host="learn.chatgpt.com",
        url="https://learn.chatgpt.com/docs/pricing",
        title="Pricing | ChatGPT Learn",
        text="Codex plan and usage limit details. " * 20,
        evidence_query="OpenAI Codex CLI account subscription pricing privacy and limitations",
    ) == {0}


def test_cli_comparison_source_identity_accepts_dominant_product_body_on_official_host():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, MCP, pricing, privacy, and limitations."
    )
    hints = research_module._catalog_official_entity_hints(question)

    assert research_module._research_source_entity_indexes(
        hints,
        host="learn.chatgpt.com",
        url="https://learn.chatgpt.com/docs/sandboxing",
        title="Sandbox | ChatGPT Learn",
        text=(
            "The sandbox constrains commands run by Codex CLI on Windows and WSL2. "
            "Codex CLI uses platform-native enforcement and an approval policy. "
        )
        * 12,
        evidence_query=(
            "OpenAI Codex CLI Windows installation tools repository workflow "
            "privacy limitations"
        ),
    ) == {0}


def test_cli_comparison_source_identity_rejects_equal_competitor_mentions_in_official_body():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, MCP, pricing, privacy, and limitations."
    )
    hints = research_module._catalog_official_entity_hints(question)
    navigation = (
        "Navigation: OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI. "
        * 12
    )

    assert research_module._research_source_entity_indexes(
        hints,
        host="ai.google.dev",
        url="https://ai.google.dev/gemini-api/docs/api-key",
        title="Using Gemini API keys | Google AI for Developers",
        text=navigation,
        evidence_query="Gemini CLI Windows installation MCP extensions",
    ) == set()


def test_research_source_candidate_rejects_same_vendor_api_page_for_cli_facet():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation and MCP extensions."
    )
    url = "https://ai.google.dev/gemini-api/docs/api-key"
    body = (
        "Gemini API keys authenticate SDK and API requests. Navigation also links to Gemini CLI. "
        * 40
    )
    candidates = research_module._research_source_candidates(
        question,
        [
            {
                "shardId": "gemini-cli-windows",
                "kind": "facet:gemini-cli-windows",
                "researchFacetId": "gemini-cli-windows",
                "query": "Gemini CLI Windows installation MCP extensions official docs",
                "evidenceQuery": "Gemini CLI Windows installation MCP extensions official docs",
                "results": [
                    {
                        "url": url,
                        "title": "Using Gemini API keys",
                        "snippet": "Gemini API authentication and SDK setup",
                        "sourceQualityHints": {
                            "authorityScore": 95,
                            "tier": "primary",
                        },
                    }
                ],
                "fetchedTopSources": [
                    {
                        "url": url,
                        "finalUrl": url,
                        "ok": True,
                        "title": "Using Gemini API keys",
                        "text": body,
                        "contentChars": len(body),
                    }
                ],
            }
        ],
        source_policy="multi_source_evidence",
    )

    assert len(candidates) == 1
    assert candidates[0]["gate"]["selectedForEvidence"] is False
    assert candidates[0]["gate"]["rejectedReason"] == "research_query_entity_mismatch"
    assert candidates[0]["researchFacetIds"] == []


def test_source_candidate_does_not_project_one_named_product_into_competitor_facets():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, models, tools, MCP, repository workflow, pricing, privacy, and limitations."
    )
    url = "https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli"
    hints = research_module._catalog_official_entity_hints(question)
    assert research_module._research_entity_indexes_in_text(
        hints,
        "site:github.com openai codex README pricing privacy telemetry",
    ) == {0}
    body = (
        "GitHub Copilot CLI supports Windows terminal installation, model selection, tools, MCP, "
        "repository workflows, account plans, privacy boundaries, and documented limitations. "
        * 30
    )
    candidates = research_module._research_source_candidates(
        question,
        [
            {
                "shardId": "copilot-operations",
                "kind": "facet:entity-github-copilot-cli-operations",
                "researchFacetId": "entity-github-copilot-cli-operations",
                "query": "GitHub Copilot CLI Windows installation models tools MCP repository workflow",
                "evidenceQuery": "GitHub Copilot CLI Windows installation models tools MCP repository workflow",
                "results": [
                    {
                        "url": url,
                        "title": "About GitHub Copilot CLI",
                        "snippet": "Official Copilot CLI capabilities",
                        "sourceQualityHints": {"authorityScore": 95, "tier": "primary"},
                    }
                ],
                "fetchedTopSources": [
                    {
                        "url": url,
                        "finalUrl": url,
                        "ok": True,
                        "title": "About GitHub Copilot CLI",
                        "text": body,
                        "contentChars": len(body),
                    }
                ],
            }
        ],
        source_policy="multi_source_evidence",
    )

    assert len(candidates) == 1
    assert candidates[0]["gate"]["selectedForEvidence"] is True
    assert candidates[0]["researchFacetIds"]
    assert all(
        "github-copilot-cli" in facet_id
        for facet_id in candidates[0]["researchFacetIds"]
    )


def test_research_architect_projection_keeps_product_pages_not_incidental_or_locale_duplicates():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, MCP extensions, pricing, privacy, and limitations."
    )
    rows = (
        (
            "codex",
            "https://learn.chatgpt.com/docs/codex/cli",
            "Codex CLI Windows installation",
            "OpenAI Codex CLI Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "claude-en",
            "https://docs.anthropic.com/en/docs/claude-code/quickstart",
            "Quickstart - Claude Code Docs",
            "Claude Code Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "claude-ja",
            "https://docs.anthropic.com/ja/docs/claude-code/quickstart",
            "Quickstart - Claude Code Docs",
            "Claude Code Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "gemini",
            "https://geminicli.com/extensions/",
            "Gemini CLI extensions",
            "Gemini CLI Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "copilot",
            "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli",
            "Installing GitHub Copilot CLI",
            "GitHub Copilot CLI Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "gemini-api",
            "https://ai.google.dev/gemini-api/docs/api-key",
            "Using Gemini API keys",
            "Gemini CLI Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "visual-studio",
            "https://learn.microsoft.com/en-us/visualstudio/releases/2026/release-notes",
            "Visual Studio 2026 release notes",
            "GitHub Copilot CLI Windows installation MCP extensions pricing privacy limitations",
        ),
        (
            "windows-mcp",
            "https://github.com/CursorTouch/Windows-MCP",
            "Windows MCP",
            "Gemini CLI Windows installation MCP extensions pricing privacy limitations",
        ),
    )
    source_matrix: list[dict] = []
    shards: list[dict] = []
    for source_id, url, title, query in rows:
        facet_id = f"{source_id}-operations"
        body = (
            f"{title}. {query}. The page states concrete setup commands, account boundaries, "
            "tool behavior, and documented limitations. "
        ) * 20
        source_matrix.append(
            {
                "sourceId": source_id,
                "title": title,
                "url": url,
                "host": research_module._host(url),
                "authorityScore": 95,
                "selectedForEvidence": True,
                "sourceQualityGate": {
                    "selectedForEvidence": True,
                    "qualityDimensions": {"relevance": 100},
                },
                "shardId": source_id,
                "researchFacetId": facet_id,
                "researchFacetIds": [facet_id],
                "evidenceQuery": query,
                "evidenceViews": [
                    {
                        "shardId": source_id,
                        "researchFacetId": facet_id,
                        "evidenceQuery": query,
                    }
                ],
            }
        )
        shards.append(
            {
                "shardId": source_id,
                "kind": f"facet:{facet_id}",
                "researchFacetId": facet_id,
                "evidenceQuery": query,
                "query": query,
                "fetchedTopSources": [
                    {"url": url, "ok": True, "title": title, "text": body}
                ],
            }
        )

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    assert {source["url"] for source in selected} == {
        "https://learn.chatgpt.com/docs/codex/cli",
        "https://docs.anthropic.com/en/docs/claude-code/quickstart",
        "https://geminicli.com/extensions/",
        "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli",
    }


def test_research_architect_excludes_rejected_historical_documents_from_current_prompt():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    sources = [
        {
            "sourceId": "rejected-pep",
            "title": "PEP 355 – Path - Object oriented filesystem paths",
            "url": "https://peps.python.org/pep-0355/",
            "host": "peps.python.org",
            "authorityScore": 85,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True},
        },
        {
            "sourceId": "current-doc",
            "title": "Current pathlib CLI documentation",
            "url": "https://docs.example.org/pathlib-cli",
            "host": "docs.example.org",
            "authorityScore": 85,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True},
            "version": "3.14",
        },
    ]
    body = "pathlib CLI command-line evidence and current filesystem behavior. " * 25
    rejected_body = "Status: Rejected\n" + body
    shards = [
        {
            "fetchedTopSources": [
                {"url": sources[0]["url"], "ok": True, "title": sources[0]["title"], "text": rejected_body},
                {"url": sources[1]["url"], "ok": True, "title": sources[1]["title"], "text": body},
            ]
        }
    ]

    selected = research_module._research_architect_sources_for_prompt(
        sources,
        shards,
        question=question,
        freshness="current",
    )

    assert [source["url"] for source in selected] == [sources[1]["url"]]


def test_research_architect_prompt_preserves_temporal_and_host_targets():
    question = "What are the current pathlib CLI best practices in 2026?"
    dated_sources = [
        {
            "sourceId": f"dated-{index}",
            "title": f"Official release evidence {index}",
            "url": f"https://official-{min(index, 2)}.example/releases/{index}",
            "host": f"official-{min(index, 2)}.example",
            "authorityScore": 95,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True, "qualityDimensions": {"relevance": 40}},
            "version": f"2026.{index}",
        }
        for index in range(1, TARGET_RESEARCH_DATED_SOURCE_COUNT + 1)
    ]
    topical_sources = [
        {
            "sourceId": f"topical-{index}",
            "title": f"Pathlib command-line practical evidence {index}",
            "url": f"https://analysis-{index}.example/pathlib-cli",
            "host": f"analysis-{index}.example",
            "authorityScore": 65,
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True, "qualityDimensions": {"relevance": 100}},
        }
        for index in range(1, 8)
    ]
    source_matrix = dated_sources + topical_sources
    body = "Current pathlib command-line evidence, applicability, limitations, and concrete behavior. " * 20
    shards = [
        {
            "fetchedTopSources": [
                {"url": source["url"], "ok": True, "title": source["title"], "text": body}
                for source in source_matrix
            ]
        }
    ]

    selected = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    expected_count = min(
        len(source_matrix),
        research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT,
    )
    assert len(selected) == expected_count
    assert sum(bool(source.get("version")) for source in selected) == TARGET_RESEARCH_DATED_SOURCE_COUNT
    assert len({source["host"] for source in selected}) >= TARGET_RESEARCH_DISTINCT_HOST_COUNT
    topical_urls = {source["url"] for source in topical_sources}
    selected_topical_urls = [source["url"] for source in selected if source["url"] in topical_urls]
    assert [source["url"] for source in selected[: len(selected_topical_urls)]] == selected_topical_urls
    assert [source["citationKey"] for source in selected] == [
        f"S{index}"
        for index in range(1, expected_count + 1)
    ]
    assert research_module._research_architect_mode(question, selected, freshness="current") == "full_synthesis"




def test_research_architect_synthesizes_at_minimum_floor_without_claiming_target_quality():
    def sources(count: int) -> list[dict]:
        return [
            {
                "url": f"https://source-{index % 5}.example/doc-{index}",
                "host": f"source-{index % 5}.example",
                "selectedForEvidence": True,
                "publishedAt": f"2026-07-{index + 1:02d}T00:00:00Z",
            }
            for index in range(count)
        ]

    assert research_module._research_architect_mode(
        "current runtime contract",
        sources(MIN_RESEARCH_SOURCE_COUNT - 1),
        freshness="current",
    ) == "gap_review"
    minimum_sources = sources(MIN_RESEARCH_SOURCE_COUNT)
    minimum_stats = research_module._research_architect_structural_stats(
        "current runtime contract",
        minimum_sources,
        freshness="current",
    )
    assert minimum_stats["structuralMinimumMet"] is True
    assert minimum_stats["structuralTargetMet"] is False
    assert research_module._research_architect_mode(
        "current runtime contract",
        minimum_sources,
        freshness="current",
    ) == "full_synthesis"


def test_research_architect_does_not_turn_document_date_count_into_a_synthesis_gate():
    sources = [
        {
            "url": f"https://source-{index % 5}.example/doc-{index}",
            "host": f"source-{index % 5}.example",
            "selectedForEvidence": True,
            "publishedAt": "unknown" if index < 3 else None,
            "version": "latest" if 3 <= index < 5 else None,
        }
        for index in range(TARGET_RESEARCH_SOURCE_COUNT)
    ]

    stats = research_module._research_architect_structural_stats(
        "current runtime contract",
        sources,
        freshness="current",
    )
    assert stats["datedSourceCount"] == 0
    assert stats["requiresDatedSources"] is True
    assert stats["structuralTargetMet"] is True
    assert research_module._research_architect_mode(
        "current runtime contract",
        sources,
        freshness="current",
    ) == "full_synthesis"

    for index, source in enumerate(sources[:TARGET_RESEARCH_DATED_SOURCE_COUNT], start=1):
        source["publishedAt"] = None
        source["version"] = f"2026.{index}"

    assert research_module._research_architect_mode(
        "current runtime contract",
        sources,
        freshness="current",
    ) == "full_synthesis"




def test_research_architect_quality_target_counts_only_subject_focused_answerable_sources():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    sources = [
        {
            "url": f"https://source-{index % 5}.example/doc-{index}",
            "host": f"source-{index % 5}.example",
            "selectedForEvidence": True,
            "version": f"2026.{index}" if index < TARGET_RESEARCH_DATED_SOURCE_COUNT else None,
            "evidenceCandidates": [
                {
                    "text": "pathlib Path evidence for command-line tools.",
                    "relevanceScore": 100,
                }
            ],
            "subjectFocused": index < 3,
        }
        for index in range(TARGET_RESEARCH_SOURCE_COUNT)
    ]

    stats = research_module._research_architect_structural_stats(
        question,
        sources,
        freshness="current",
    )
    assert stats["structuralMinimumMet"] is True
    assert stats["structuralTargetMet"] is False
    assert research_module._research_architect_mode(
        question,
        sources,
        freshness="current",
    ) == "full_synthesis"

    sources[-1]["subjectFocused"] = True
    assert research_module._research_architect_mode(
        question,
        sources,
        freshness="current",
    ) == "full_synthesis"


def test_pathlib_cli_subject_focus_rejects_incidental_virtual_environment_mentions():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    incidental = {
        "title": "PEP 832 - Virtual environment discovery",
        "url": "https://peps.python.org/pep-0832/",
        "evidenceCandidates": [
            {
                "text": "The venv CLI gains a default and a helper returns pathlib.Path.",
                "relevanceScore": 100,
            }
        ],
    }
    foundational = {
        "title": "PEP 519 - Adding a file system path protocol",
        "url": "https://peps.python.org/pep-0519/",
        "evidenceCandidates": [
            {
                "text": "The file system path protocol supports pathlib path objects.",
                "relevanceScore": 100,
            }
        ],
    }

    assert research_module._architect_source_subject_focused(incidental, question) is False
    assert research_module._architect_source_subject_focused(foundational, question) is True


def test_runtime_claim_supplement_order_excludes_unfocused_sources_even_if_requested():
    sources = [
        {"citationKey": "S1", "subjectFocused": True, "title": "Focused primary"},
        {
            "citationKey": "S2",
            "subjectFocused": False,
            "title": "OFF_TOPIC_SENTINEL_729",
        },
        {"citationKey": "S3", "subjectFocused": True, "title": "Focused experience"},
    ]

    ordered = research_module._architect_supplement_source_order(
        sources,
        ["S2", "S1"],
    )

    assert [source["citationKey"] for source in ordered] == ["S1", "S3"]
    assert all("OFF_TOPIC_SENTINEL_729" not in source["title"] for source in ordered)
    assert sources[1]["title"] == "OFF_TOPIC_SENTINEL_729"


def test_pathlib_cli_subject_focus_accepts_pep519_protocol_vocabulary_without_literal_pathlib():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    source = {
        "title": "PEP 519 - Adding a file system path protocol",
        "url": "https://peps.python.org/pep-0519/",
    }

    assert research_module._architect_source_subject_focused(source, question) is True

    source["evidenceCandidates"] = [
        {
            "text": "The os.PathLike interface and __fspath__ define the file system path protocol.",
            "relevanceScore": 100,
        }
    ]
    assert research_module._architect_source_subject_focused(source, question) is True


def test_pathlib_cli_subject_focus_accepts_click_path_type_bridge_without_literal_pathlib():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    source = {
        "title": "Click Parameter Types",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "evidenceCandidates": [
            {
                "text": "The path_type parameter changes the object type returned by click.Path.",
                "relevanceScore": 100,
            }
        ],
    }

    assert research_module._architect_source_subject_focused(source, question) is True


def test_pathlib_cli_subject_focus_accepts_argparse_path_converter_with_token_spacing():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    source = {
        "title": "argparse - Parser for command-line options",
        "url": "https://docs.python.org/3/library/argparse.html",
        "evidenceCandidates": [
            {
                "text": "parser . add_argument ( 'datapath' , type = pathlib . Path )",
                "relevanceScore": 100,
            }
        ],
    }

    assert research_module._architect_source_subject_focused(source, question) is True


def test_pathlib_cli_subject_focus_accepts_typer_application_directory_example():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    source = {
        "title": "CLI Application Directory - Typer",
        "url": "https://typer.tiangolo.com/tutorial/app-dir/",
        "evidenceCandidates": [
            {
                "text": (
                    "After importing Path from pathlib, a CLI application can call typer.get_app_dir and combine the result with "
                    "Path(app_dir) / 'config.json' for its configuration file."
                ),
                "relevanceScore": 100,
            }
        ],
    }

    assert research_module._architect_source_subject_focused(source, question) is True


def test_pathlib_cli_subject_focus_rejects_generic_click_type_page_without_path_bridge():
    question = "What are the current best practices for using Python pathlib in CLI tools?"
    source = {
        "title": "Click Choice Parameter Type",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/#choice",
        "evidenceCandidates": [
            {
                "text": "Choice validates a command-line value against a fixed collection.",
                "relevanceScore": 100,
            }
        ],
    }

    assert research_module._architect_source_subject_focused(source, question) is False




def test_entity_query_focus_parts_keep_explicit_comparison_dimensions_atomic():
    question = (
        "截至 2026 年 7 月，比较 Windows 上的四款 CLI，包括安装运行、模型与工具调用、"
        "MCP 扩展和代码库工作流、账号价格、隐私边界与常见局限。"
    )

    assert [
        dimension
        for dimension, _focus in research_module._research_entity_query_focus_parts(question)
    ] == [
        "setup",
        "models_tools",
        "extensions_workflow",
        "accounts_pricing",
        "privacy_limits",
    ]


def test_catalog_official_entity_hints_merge_duplicate_vendor_hosts():
    hints = research_module._catalog_official_entity_hints(
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI."
    )

    assert [hint["label"] for hint in hints] == [
        "OpenAI Codex CLI",
        "Claude Code",
        "Gemini CLI",
        "GitHub Copilot CLI",
    ]
    assert hints[0]["host"] == "learn.chatgpt.com"
    assert {"learn.chatgpt.com", "platform.openai.com", "openai.com"}.issubset(
        set(hints[0]["hosts"])
    )
    assert {"docs.anthropic.com", "docs.claude.com"}.issubset(set(hints[1]["hosts"]))


def test_named_decision_audiences_extracts_only_explicit_grouped_recommendations():
    assert research_module._research_named_decision_audiences(
        "请比较四款 CLI，并给出按个人开发者、小团队和已有平台订阅者划分的可执行选型建议。"
    ) == ["个人开发者", "小团队", "已有平台订阅者"]
    assert research_module._research_named_decision_audiences(
        "请比较四款 CLI，并给出适合普通用户的建议。"
    ) == []
    assert research_module._research_named_decision_audiences(
        "请比较四款 CLI，并按个人开发者、小团队和已有平台订阅者给出选型建议。"
    ) == ["个人开发者", "小团队", "已有平台订阅者"]


def test_named_decision_inference_normalizer_keeps_one_disjoint_row_per_audience():
    audiences = ["个人开发者", "小团队", "已有平台订阅者"]
    rows = [
        {"audience": "个人开发者", "inference": "individual a", "premiseClaimIds": ["C1", "C2"]},
        {"audience": "个人开发者", "inference": "individual b", "premiseClaimIds": ["C3", "C4"]},
        {"audience": "小团队", "inference": "team", "premiseClaimIds": ["C5", "C6"]},
        {"audience": "已有平台订阅者", "inference": "subscriber", "premiseClaimIds": ["C3", "C7"]},
        {"audience": "已有平台订阅者", "inference": "subscriber disjoint", "premiseClaimIds": ["C8", "C9"]},
    ]

    normalized = research_module._research_normalize_named_decision_inferences(
        rows,
        audiences,
    )

    assert len(normalized) == 3
    assert [row["audience"] for row in normalized] == audiences
    premise_sets = [set(row["premiseClaimIds"]) for row in normalized]
    assert all(
        not left.intersection(right)
        for index, left in enumerate(premise_sets)
        for right in premise_sets[index + 1 :]
    )


def test_runtime_named_decision_inferences_use_disjoint_primary_sources():
    audiences = ["个人开发者", "小团队", "已有平台订阅者"]
    facts = [
        ("OpenAI Codex CLI", "Codex CLI 在 Windows 使用原生沙箱。"),
        ("OpenAI Codex CLI", "Codex CLI 的本地命令默认运行在受限环境中。"),
        ("Claude Code", "Claude Code 提供 PowerShell 一行安装命令。"),
        ("Claude Code", "Claude Code 的 Windows 二进制带 Anthropic 签名。"),
        ("Gemini CLI", "Gemini CLI 在 Windows 运行要求 Node.js 20.0.0+。"),
        ("Gemini CLI", "Gemini CLI 支持使用 Google 账号登录。"),
        ("GitHub Copilot CLI", "GitHub Copilot CLI 可以添加 MCP 服务器。"),
        ("GitHub Copilot CLI", "GitHub Copilot CLI 依赖 GitHub Copilot 账号方案。"),
    ]
    claims = [
        {
            "claimId": f"C{index}",
            "claim": fact,
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": f"S{index}",
                    "title": f"{product} official documentation",
                    "tier": "primary", "sourceRole": "primary",
                    "authorityScore": 85,
                }
            ],
        }
        for index, (product, fact) in enumerate(facts, start=1)
    ]
    claims.append(
        {
            "claimId": "secondary",
            "claim": "A secondary article describes an additional opinion.",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": "S10",
                    "title": "Secondary article",
                    "tier": "secondary", "sourceRole": "secondary",
                    "authorityScore": 50,
                }
            ],
        }
    )

    inferences = research_module._architect_runtime_named_decision_inferences(
        claims,
        audiences,
        question=(
            "请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 GitHub Copilot CLI，"
            "并按个人开发者、小团队和已有平台订阅者给出选型建议。"
        ),
    )

    assert len(inferences) == 3
    premise_ids = [
        claim_id
        for inference in inferences
        for claim_id in inference["premiseClaimIds"]
    ]
    assert len(premise_ids) == len(set(premise_ids)) == 6
    assert "secondary" not in premise_ids
    assert all(
        audience in inference["inference"]
        for audience, inference in zip(audiences, inferences, strict=True)
    )
    assert all("优先试用" in inference["inference"] for inference in inferences)
    assert all("筛选条件" not in inference["inference"] for inference in inferences)


def test_runtime_named_decision_fallback_excludes_explicit_product_comparisons():
    assert not research_module._architect_runtime_named_decision_fallback_allowed(
        "请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 GitHub Copilot CLI，"
        "并按个人开发者、小团队和已有平台订阅者给出选型建议。"
    )
    assert research_module._architect_runtime_named_decision_fallback_allowed(
        "How should teams choose an evidence contract? Provide recommendations grouped "
        "by individual developers, small teams, and platform subscribers."
    )


def test_architect_support_role_does_not_promote_catalog_rank_to_document_provenance():
    assert research_module._architect_support_role(
        {
            "tier": "secondary",
            "authorityTier": "primary",
            "catalogCategory": "official_docs",
        }
    ) == "unknown"
    assert research_module._architect_support_role(
        {
            "tier": "secondary",
            "authorityTier": "primary",
            "catalogCategory": "source_repo",
            "runtimeOfficialSeed": True,
        }
    ) == "unknown"
    assert research_module._architect_support_role(
        {
            "tier": "secondary",
            "authorityTier": "primary",
            "catalogCategory": "source_repo",
        }
    ) == "unknown"


def test_runtime_named_decision_inferences_reject_unbound_generic_facts():
    claims = [
        {
            "claimId": f"C{index}",
            "claim": f"Primary source {index} records a condition for the compared tool.",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": f"S{index}",
                    "title": f"Primary tool documentation {index}",
                    "tier": "primary",
                    "authorityScore": 85,
                }
            ],
        }
        for index in range(1, 10)
    ]

    assert research_module._architect_runtime_named_decision_inferences(
        claims,
        ["个人开发者", "小团队", "已有平台订阅者"],
        question=(
            "请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 GitHub Copilot CLI，"
            "并按个人开发者、小团队和已有平台订阅者给出选型建议。"
        ),
    ) == []


def test_runtime_named_decision_inferences_use_word_boundaries_and_attributed_secondary():
    claims = [
        {
            "claimId": "C1",
            "claim": "Codex CLI 提供 PowerShell 安装方式。",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": "S1",
                    "title": "OpenAI Codex CLI documentation",
                    "tier": "primary", "sourceRole": "primary",
                }
            ],
        },
        {
            "claimId": "C2",
            "claim": (
                "二手来源《GitHub Copilot Plans》的表述：GitHub Copilot Pro 用户可选择特定账号方案。"
            ),
            "sourceClaim": "GitHub Copilot Pro 用户可选择特定账号方案。",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": "S2",
                    "title": "GitHub Copilot Plans",
                    "tier": "secondary", "sourceRole": "secondary",
                }
            ],
        },
    ]

    inferences = research_module._architect_runtime_named_decision_inferences(
        claims,
        ["已有平台订阅者"],
        question=(
            "请比较 OpenAI Codex CLI 和 GitHub Copilot CLI，"
            "并按已有平台订阅者给出选型建议。"
        ),
    )

    assert len(inferences) == 1
    assert inferences[0]["premiseClaimIds"] == ["C2"]
    assert inferences[0]["inference"].startswith("含二手来源的综合判断：")
    assert "PowerShell" not in inferences[0]["inference"]


def test_critical_gap_queries_preserve_named_entities_and_gap_dimensions():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, models, tools, MCP, pricing, privacy, and limitations."
    )
    queries = research_module._research_critical_gap_queries(
        question,
        [
            "Per-CLI Windows install commands and PowerShell/WSL specifics for Gemini CLI and GitHub Copilot CLI",
            "Per-CLI MCP/extension support details for all four tools",
            "Per-CLI model selection and tool-calling mechanics",
        ],
        limit=3,
    )

    assert len(queries) == 3
    assert "Gemini CLI" in queries[0]
    assert "GitHub Copilot CLI" in queries[0]
    assert "Windows" in queries[0]
    assert "MCP" in queries[1]
    assert all("202" in query and "official documentation" in query for query in queries)


def test_architect_plan_failure_gaps_split_named_entities_before_repair_search():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, models, tools, MCP, pricing, privacy, and limitations."
    )
    gaps = research_module._research_architect_plan_failure_gaps(
        question,
        {"fallbackReason": "architect_evidence_plan_unavailable"},
    )

    assert len(gaps) == 4
    assert "OpenAI Codex CLI" in gaps[0] and "Claude Code" in gaps[0]
    assert "Gemini CLI" in gaps[1] and "GitHub Copilot CLI" in gaps[1]
    assert "MCP" in gaps[2] and "privacy" in gaps[3]
    assert research_module._research_architect_plan_failure_gaps(
        question,
        {"fallbackReason": "architect_agent_missing_research_result"},
    ) == []


def test_architect_plan_failure_gaps_keep_single_technical_entity_atomic():
    question = (
        "Using official documentation, explain LangChain Python v1 create_agent and "
        "middleware APIs, minimal usage, and migration notes."
    )

    gaps = research_module._research_architect_plan_failure_gaps(
        question,
        {"fallbackReason": "architect_evidence_plan_unavailable"},
    )

    assert len(gaps) == 2
    assert all("LangChain Python" in gap for gap in gaps)
    assert "API reference signatures examples" in gaps[0]
    assert "migration guide release notes" in gaps[1]
    assert all("Windows installation" not in gap for gap in gaps)
    assert all("account pricing privacy" not in gap for gap in gaps)


def test_catalog_official_entity_seeds_replace_matching_atomic_dimension_queries():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows installation, models, tools, MCP, pricing, privacy, and limitations."
    )
    planned = []
    for label, slug in (
        ("OpenAI Codex CLI", "openai-codex-cli"),
        ("Claude Code", "claude-code"),
        ("Gemini CLI", "gemini-cli"),
        ("GitHub Copilot CLI", "github-copilot-cli"),
    ):
        for dimension in (
            "setup",
            "models_tools",
            "extensions_workflow",
            "accounts_pricing",
            "privacy_limits",
        ):
            planned.append(
                {
                    "shardId": f"{slug}-{dimension}",
                    "kind": f"facet:entity-{slug}-{dimension}",
                    "researchFacetId": f"entity-{slug}-{dimension}",
                    "facetGoal": f"{label} {dimension}",
                    "query": f"{label} {dimension} official documentation",
                    "evidenceQuery": f"{label} {dimension} official documentation",
                    "sourceIntent": "official_primary",
                    "verification": False,
                }
            )

    seeded, audit = research_module._apply_catalog_official_entity_seeds(
        question,
        planned,
    )

    assert len(seeded) == len(planned)
    assert audit["replacedCount"] == 20
    assert all(row["kind"] == "seed_url" for row in seeded)
    assert {row["catalogSeedDimension"] for row in seeded} == {
        "setup",
        "models_tools",
        "extensions_workflow",
        "accounts_pricing",
        "privacy_limits",
    }
    assert {row["seedUrl"] for row in seeded} >= {
        "https://learn.chatgpt.com/docs/codex/cli",
        "https://learn.chatgpt.com/docs/extend/mcp?surface=cli",
        "https://learn.chatgpt.com/docs/pricing",
            "https://learn.chatgpt.com/docs/agent-approvals-security",
        "https://code.claude.com/docs/en/installation",
        "https://code.claude.com/docs/en/model-config",
        "https://code.claude.com/docs/en/mcp",
        "https://code.claude.com/docs/en/costs",
        "https://code.claude.com/docs/en/data-usage",
        "https://geminicli.com/docs/cli/model/",
        "https://geminicli.com/docs/cli/tutorials/mcp-setup/",
        "https://geminicli.com/docs/resources/quota-and-pricing/",
        "https://geminicli.com/docs/resources/tos-privacy/",
        "https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli",
        "https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/overview",
        "https://docs.github.com/en/copilot/how-tos/manage-your-account/manage-policies",
        "https://github.com/features/copilot/plans",
    }
    assert all(row["evidenceQuery"].endswith("official documentation") for row in seeded)
    assert len({row["shardId"] for row in seeded}) == 20
    assert audit["seedIds"] == [
        f"{entity}:{dimension}"
        for entity in (
            "openai-codex-cli",
            "claude-code",
            "gemini-cli",
            "github-copilot-cli",
        )
        for dimension in (
            "setup",
            "models_tools",
            "extensions_workflow",
            "accounts_pricing",
            "privacy_limits",
        )
    ]


def test_catalog_official_entity_seed_restores_facet_query_when_planner_omits_optional_fields():
    question = "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI on installation and pricing."
    planned = [
        {
            "kind": "facet:entity-openai-codex-cli-setup",
            "query": "OpenAI Codex CLI installation official documentation",
            "sourceIntent": "official_primary",
            "verification": False,
        },
        {
            "kind": "facet:entity-claude-code-setup",
            "query": "Claude Code installation official documentation",
            "sourceIntent": "official_primary",
            "verification": False,
        },
    ]

    seeded, audit = research_module._apply_catalog_official_entity_seeds(
        question,
        planned,
    )

    assert audit["replacedCount"] == 2
    codex = next(row for row in seeded if row.get("seedUrl", "").endswith("/codex/cli"))
    assert codex["researchFacetId"] == "entity-openai-codex-cli-setup"
    assert codex["evidenceQuery"] == "OpenAI Codex CLI installation official documentation"
    assert codex["facetGoal"] == codex["evidenceQuery"]


def test_root_question_decomposition_is_reserved_for_multi_part_briefs():
    assert research_module._research_should_decompose_root_question(
        "截至 2026 年 7 月，请分别核对欧盟 AI Act 对 GPAI 提供者的适用时间、系统性风险门槛、"
        "开源例外、执法权限和罚款，并区分法规原文、主管机构解释与行业经验，给出当前仍有效的实施建议。"
    )
    assert research_module._research_should_decompose_root_question(
        "Compare the current provider obligations, including the implementation timeline; "
        "distinguish official requirements from industry experience."
    )
    assert not research_module._research_should_decompose_root_question(
        "How should pathlib and argparse be combined in a CLI?"
    )
    assert not research_module._research_should_decompose_root_question(
        "research runtime evidence contract"
    )


def test_structured_repair_preserves_architect_gap_queries_before_deterministic_fallbacks():
    deterministic = [
        {"query": "missing facet official primary source 2026"},
        {"query": "independent corroboration evidence 2026"},
    ]

    merged = research_module._merge_research_repair_queries(
        [
            "Fetch EUR-Lex CELEX 32024R1689 Article 101",
            'site:eur-lex.europa.eu "32024R1689" Article 101',
            "missing facet official primary source 2026",
        ],
        deterministic,
    )

    assert merged == [
        "EUR-Lex CELEX 32024R1689 Article 101",
        'site:eur-lex.europa.eu "32024R1689" Article 101',
        "missing facet official primary source 2026",
        "independent corroboration evidence 2026",
    ]


def test_structured_refinement_prioritizes_uncovered_facets_without_repeating_bundle():
    question = (
        "Research every item below as one evidence bundle.\n"
        "1. [covered] Verify covered behavior.\n"
        "2. [missing] Verify missing legal requirement."
    )
    shards = [
        {
            "shardId": "covered-shard",
            "kind": "facet:covered",
            "researchFacetId": "covered",
            "query": "covered behavior official reference",
            "evidenceQuery": "covered behavior official reference",
            "results": [],
            "fetchedTopSources": [],
        },
        {
            "shardId": "missing-shard",
            "kind": "facet:missing",
            "researchFacetId": "missing",
            "query": "missing legal requirement regulator guidance",
            "evidenceQuery": "missing legal requirement regulator guidance",
            "results": [],
            "fetchedTopSources": [],
        },
    ]

    refined = research_module._build_refinement_shards(
        question,
        shards,
        source_policy="multi_source_evidence",
        limit=2,
        round_index=2,
    )

    assert len(refined) == 2
    assert {item["researchFacetId"] for item in refined} == {"covered", "missing"}
    assert all("Research every item below" not in item["query"] for item in refined)
    assert all(item["verification"] is True for item in refined)


def test_research_brief_coverage_requires_each_structured_facet_in_claims_and_answer():
    question = (
        "Research every item below as one evidence bundle.\n"
        "1. [timeline] Verify the effective date.\n"
        "2. [practice] Compare implementation experience."
    )
    sources = [
        {
            "sourceId": "timeline-source",
            "citationKey": "S1",
            "url": "https://official.example/timeline",
            "researchFacetId": "timeline",
            "selectedForEvidence": True,
        },
        {
            "sourceId": "practice-source",
            "citationKey": "S2",
            "url": "https://independent.example/practice",
            "researchFacetId": "practice",
            "selectedForEvidence": True,
        },
    ]
    claims = [
        {
            "claimId": "timeline-claim",
            "claim": "The rule applies on the stated date.",
            "supportingSources": [{"citationKey": "S1"}],
        },
        {
            "claimId": "practice-claim",
            "claim": "The implementation report describes operational experience.",
            "supportingSources": [{"sourceId": "practice-source"}],
        },
    ]

    complete = research_module._research_brief_coverage(
        question=question,
        source_matrix=sources,
        claim_table=claims,
        answer="Timeline finding [S1]. Practice finding [S2].",
    )
    incomplete = research_module._research_brief_coverage(
        question=question,
        source_matrix=sources,
        claim_table=claims,
        answer="Timeline finding only [S1].",
    )

    assert complete["complete"] is True
    assert complete["coveredFacetIds"] == ["timeline", "practice"]
    assert incomplete["complete"] is False
    assert incomplete["missingFacetIds"] == ["practice"]
    practice = next(item for item in incomplete["items"] if item["taskBriefId"] == "practice")
    assert practice["status"] == "evidence_only"


def test_claim_excerpt_verifier_uses_supporting_source_body_not_model_flags():
    source = {
        "sourceId": "src-1",
        "citationKey": "S1",
        "url": "https://docs.example/one",
        "text": "The runtime validates each quoted excerpt against the already-read source body before accepting a claim.",
    }
    good_excerpt = "validates each quoted excerpt against the already-read source body"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "Each excerpt is checked against source text.",
                "supportingSources": ["S1"],
                "evidenceExcerpt": good_excerpt,
                "evidenceVerified": False,
                "evidenceExcerptSha256": "0" * 64,
            }
        ],
        [source],
    )
    rejected, rejected_issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "This assertion is unsupported.",
                "supportingSources": ["S1"],
                "evidenceExcerpt": "A fabricated sentence that does not occur in the source body.",
                "evidenceVerified": True,
                "evidenceExcerptSha256": "f" * 64,
            }
        ],
        [source],
    )

    assert issues == []
    assert verified[0]["evidenceVerified"] is True
    assert verified[0]["evidenceExcerptSha256"] != "0" * 64
    assert verified[0]["claimType"] == "source_fact"
    assert verified[0]["supportingSources"] == [
        {
            "sourceId": "src-1",
            "title": "",
            "url": "https://docs.example/one",
            "citationKey": "S1",
        }
    ]
    assert rejected == []
    assert rejected_issues == ["claim_1_evidence_excerpt_not_in_supporting_source"]


def test_claim_excerpt_verifier_preserves_runtime_facet_lineage():
    excerpt = "Article 101 sets the fine framework for providers of general-purpose AI models."
    source = {
        "sourceId": "src-penalties",
        "citationKey": "S1",
        "title": "Article 101",
        "url": "https://official.example/article-101",
        "researchFacetId": "enforcement-penalties",
        "researchFacetIds": ["timeline", "enforcement-penalties"],
        "tier": "primary",
        "authorityScore": 90,
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S1:E1",
                "text": excerpt,
                "researchFacetId": "enforcement-penalties",
                "researchFacetGoal": "Verify the GPAI enforcement and penalty regime.",
            }
        ],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claimId": "C1",
                "claim": excerpt,
                "claimType": "source_fact",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert verified[0]["supportingSources"][0]["researchFacetId"] == "enforcement-penalties"
    assert verified[0]["supportingSources"][0]["researchFacetIds"] == [
        "enforcement-penalties"
    ]
    assert verified[0]["supportingSources"][0]["researchFacetGoal"] == (
        "Verify the GPAI enforcement and penalty regime."
    )


def test_claim_excerpt_verifier_resolves_runtime_evidence_candidate_key():
    source = {
        "sourceId": "src-1",
        "citationKey": "S1",
        "url": "https://docs.example/one",
        "text": (
            "Pathlib represents filesystem paths with operating-system-specific semantics. "
            "Argparse accepts a callable that converts each command-line argument before storing it."
        ),
    }
    source["evidenceCandidates"] = research_module._architect_evidence_candidates(
        source,
        "How should pathlib be used with argparse in a CLI?",
        limit=4,
    )
    candidate = source["evidenceCandidates"][0]

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "A directly supported atomic premise is available.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": candidate["evidenceExcerptKey"],
                "evidenceExcerpt": "model-authored paraphrase that must not be trusted",
            }
        ],
        [source],
    )

    assert issues == []
    assert verified[0]["evidenceExcerpt"] == candidate["text"]
    assert verified[0]["evidenceExcerptKey"] == candidate["evidenceExcerptKey"]
    assert verified[0]["evidenceVerified"] is True

    rebound, rebound_issues = research_module._verify_architect_claim_excerpts(
        [verified[0]],
        [{**source, "citationKey": "S8", "evidenceCandidates": []}],
    )
    assert rebound_issues == []
    assert rebound[0]["supportingSources"][0]["url"] == source["url"]
    assert rebound[0]["supportingSources"][0]["citationKey"] == "S8"

    rejected, rejected_issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "The key cannot be paired with another source.",
                "supportingSources": ["S2"],
                "evidenceExcerptKey": candidate["evidenceExcerptKey"],
            }
        ],
        [
            source,
            {
                "sourceId": "src-2",
                "citationKey": "S2",
                "url": "https://docs.example/two",
                "text": "A separate source body with enough content for the mismatch fixture.",
            },
        ],
    )
    assert rejected == []
    assert rejected_issues == ["claim_1_evidence_key_source_mismatch"]

    corrected, corrected_issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "A directly supported atomic premise is available.",
                "supportingSources": ["S2"],
                "evidenceExcerptKey": candidate["evidenceExcerptKey"],
            }
        ],
        [
            source,
            {
                "sourceId": "src-2",
                "citationKey": "S2",
                "url": "https://docs.example/two",
                "text": "A separate source body with enough content for the mismatch fixture.",
            },
        ],
        require_evidence_key=True,
    )
    assert corrected_issues == []
    assert corrected[0]["supportBindingCorrected"] is True
    assert corrected[0]["supportingSources"][0]["citationKey"] == "S1"
    assert corrected[0]["evidenceExcerpt"] == candidate["text"]


def test_evidence_candidate_relevance_ignores_markdown_url_destinations():
    source = {
        "sourceId": "click-parameter-types",
        "citationKey": "S1",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "text": (
            "Integer ranges clamp values between a minimum and maximum. "
            "[Integer reference](https://click.palletsprojects.com/en/stable/parameter-types/#int-range)\n\n"
            "The Click path type is click.Path, and path_type can convert the result to pathlib.Path."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "How should pathlib be used in a CLI?",
        limit=2,
    )

    assert "click.Path" in candidates[0]["text"]
    assert candidates[0]["relevanceScore"] == 100
    assert len(candidates) == 1


def test_evidence_candidates_keep_adjacent_parameter_contract_after_focused_head():
    source = {
        "sourceId": "click-path-contract",
        "citationKey": "S1",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "text": (
            "The CLI documentation introduces pathlib path handling for command-line tools.\n\n"
            "The path_type parameter converts an incoming path value to pathlib.Path, while exists and "
            "file_okay control validation behavior."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "How should pathlib be used in a CLI?",
        limit=2,
    )

    assert [candidate["relevanceScore"] for candidate in candidates] == [100, 50]
    assert "path_type" in candidates[1]["text"]
    assert "file_okay" in candidates[1]["text"]


def test_evidence_candidates_recover_complete_boundaries_around_query_excerpt_marker():
    source = {
        "sourceId": "argparse-query-excerpt",
        "citationKey": "S1",
        "title": "argparse — Python documentation",
        "url": "https://docs.python.org/3/library/argparse.html",
        "text": (
            "The argparse CLI accepts path arguments. parser . add_argumen\n"
            "[... query-focused excerpt ...]\n"
            "ce() is a clipped continuation that must not be exposed.\n"
            "### type\n"
            "By default, arguments are strings, while a callable converter can return a Path value "
            "for the command-line application without changing unrelated parser behavior. "
            "parser . add_argument ( 'datapath' , type = pathlib . Path )."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "How should pathlib be used in a CLI?",
        limit=8,
    )

    candidate_texts = [candidate["text"] for candidate in candidates]
    assert any("type = pathlib . Path" in text for text in candidate_texts)
    assert any(text.startswith("### type") for text in candidate_texts)
    assert not any(
        text.lower().startswith(("ser .", "he default", "n perform", "ce()"))
        for text in candidate_texts
    )


def test_evidence_candidates_reserve_typer_validation_contract_with_limit_four():
    generic = "\n\n".join(
        f"Typer CLI pathlib Path example {index} converts a command-line path for a callback."
        for index in range(1, 6)
    )
    source = {
        "sourceId": "typer-path",
        "citationKey": "S7",
        "title": "Path - Typer",
        "url": "https://typer.tiangolo.com/tutorial/parameter-types/path/",
        "text": (
            f"{generic}\n\n"
            "## Path validations\n"
            "You can perform several validations for Path CLI parameters: "
            "exists requires the path to exist, file_okay controls files, "
            "dir_okay controls directories, and resolve_path resolves the value."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "What are current pathlib best practices in CLI tools?",
        limit=4,
    )

    assert len(candidates) == 4
    assert any(
        "Path validations" in candidate["text"]
        and "exists" in candidate["text"]
        and "file_okay" in candidate["text"]
        for candidate in candidates
    )


def test_evidence_candidates_reserve_click_path_type_contract_with_limit_four():
    generic = "\n\n".join(
        f"Click CLI pathlib Path example {index} passes a command-line path to a callback."
        for index in range(1, 6)
    )
    source = {
        "sourceId": "click-path-api",
        "citationKey": "S8",
        "title": "API — Click Documentation",
        "url": "https://click.palletsprojects.com/en/stable/api/",
        "text": (
            f"{generic}\n\n"
            "### click.Path\n"
            "The path_type parameter converts the incoming value to the requested type. "
            "Passing path_type=pathlib.Path returns a pathlib.Path value."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "What are current pathlib best practices in CLI tools?",
        limit=4,
    )

    assert len(candidates) == 4
    assert any(
        "path_type" in candidate["text"] and "pathlib.Path" in candidate["text"]
        for candidate in candidates
    )


def test_evidence_truncation_guard_does_not_reject_valid_short_identifiers():
    assert research_module._architect_evidence_fragment_is_truncated("p . resolve() returns a Path.") is False
    assert research_module._architect_evidence_fragment_is_truncated("os . PathLike defines the protocol.") is False
    assert research_module._architect_evidence_fragment_is_truncated("n perform several validations") is True
    assert research_module._architect_evidence_fragment_is_truncated("and effective application of the rules.") is True
    assert research_module._architect_evidence_fragment_is_truncated("general entry into application on 2 August 2026.") is True
    assert research_module._architect_evidence_fragment_is_truncated(
        "Quotas and pricing are based on a fixed price subscription with"
    ) is True
    assert research_module._architect_evidence_fragment_is_truncated(
        "Quotas and pricing are based on a fixed price subscription."
    ) is False


def test_evidence_candidates_recover_commands_from_oversized_install_block():
    source = {
        "sourceId": "claude-install",
        "citationKey": "S1",
        "title": "Claude Code installation",
        "url": "https://code.claude.com/docs/en/installation",
        "researchFacetId": "entity-claude-code-setup",
        "evidenceQuery": '"Claude Code" Windows PowerShell WSL installation runtime',
        "text": (
            "## Install Claude Code\n"
            "```\n"
            "curl -fsSL https://claude.ai/install.sh | bash\n"
            "irm https://claude.ai/install.ps1 | iex\n"
            "winget install Anthropic.ClaudeCode\n"
            "npm install -g @anthropic-ai/claude-code\n"
            + "echo install complete\n" * 120
            + "```\n"
            "### Set up on Windows\n"
            "Native Windows uses PowerShell; WSL 2 is available for Linux toolchains."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "Compare Windows installation and runtime for local CLI agents.",
        limit=4,
    )

    candidate_text = "\n".join(candidate["text"] for candidate in candidates)
    assert "winget install Anthropic.ClaudeCode" in candidate_text
    assert "npm install -g @anthropic-ai/claude-code" in candidate_text
    assert "WSL 2" in candidate_text


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


def test_cross_section_composite_inferences_are_dropped_without_dropping_verified_claims():
    claims = [
        {"claimId": "C1", "claim": "First verified premise."},
        {"claimId": "C2", "claim": "Second verified premise."},
    ]
    outline = [
        {"sectionId": "one", "claimIds": ["C1"]},
        {"sectionId": "two", "claimIds": ["C2"]},
    ]
    inferences = [
        {"inferenceId": "I1", "premiseClaimIds": ["C1", "C2"], "inference": "Cross section."},
        {
            "inferenceId": "I2",
            "premiseClaimIds": ["C2"],
            "inference": "Local conclusion based on the documented premise.",
        },
    ]

    accepted, dropped = research_module._architect_filter_composite_inferences(
        outline,
        claims,
        inferences,
    )

    assert accepted == [inferences[1]]
    assert dropped == ["composite_inference_1_crosses_sections"]


def test_composite_inference_rejects_behavior_and_assignment_not_in_premise_evidence():
    claims = [
        {
            "claimId": "C1",
            "claim": "Typer documents a path_type option.",
            "evidenceExcerpt": "Argument(*, path_type=None)",
            "supportingSources": [
                {"citationKey": "S1", "title": "Typer reference", "tier": "primary", "authorityScore": 85}
            ],
        },
        {
            "claimId": "C2",
            "claim": "Typer documents an exists option.",
            "evidenceExcerpt": "Argument(*, exists=False)",
            "supportingSources": [
                {"citationKey": "S1", "title": "Typer reference", "tier": "primary", "authorityScore": 85}
            ],
        },
        {
            "claimId": "C3",
            "claim": "A separate verified limitation remains in scope.",
            "evidenceExcerpt": "A separate verified limitation remains in scope.",
            "supportingSources": [
                {"citationKey": "S2", "title": "Separate reference", "tier": "primary", "authorityScore": 85}
            ],
        },
    ]
    outline = [
        {"sectionId": "one", "claimIds": ["C1", "C2"]},
        {"sectionId": "two", "claimIds": ["C3"]},
    ]

    proposed_inferences = [
        {
            "inferenceId": "I1",
            "inference": "Typer should validate and convert CLI inputs with path_type=Path.",
            "premiseClaimIds": ["C1", "C2"],
        }
    ]

    accepted, dropped = research_module._architect_filter_composite_inferences(
        outline,
        claims,
        proposed_inferences,
    )

    assert accepted == []
    assert dropped[0].startswith("composite_inference_1_unsupported:")

    structurally_accepted, structurally_dropped = (
        research_module._architect_filter_composite_inferences(
            outline,
            claims,
            proposed_inferences,
            validate_semantics=False,
        )
    )

    assert structurally_accepted == proposed_inferences
    assert structurally_dropped == []


def test_synthesis_unit_requires_declared_inference_and_all_premise_citations():
    claims = [
        {
            "claimId": "C1",
            "claim": "Path values provide the internal boundary representation.",
            "evidenceExcerpt": "Path values provide the internal boundary representation.",
            "supportingSources": [
                {"citationKey": "S1", "title": "Official path docs", "tier": "primary", "authorityScore": 85}
            ],
        },
        {
            "claimId": "C2",
            "claim": "The CLI parser accepts typed boundary values.",
            "evidenceExcerpt": "The CLI parser accepts typed boundary values.",
            "supportingSources": [
                {"citationKey": "S2", "title": "Official CLI docs", "tier": "primary", "authorityScore": 85}
            ],
        },
    ]
    task = {
        "assignedClaims": claims,
        "compositeInferences": [
            {
                "inferenceId": "I1",
                "inference": "The practical recommendation is to use typed Path values at the CLI boundary.",
                "premiseClaimIds": ["C1", "C2"],
            }
        ],
    }

    assert research_module._architect_synthesis_unit_supported(
        "Practical synthesis: the tool should use typed Path values at the CLI boundary. [S1][S2]",
        task,
    ) is True
    assert research_module._architect_synthesis_unit_supported(
        "Practical synthesis: the tool should use typed Path values at the CLI boundary. [S1]",
        task,
    ) is False
    assert research_module._architect_synthesis_unit_supported(
        "Practical synthesis: the tool should add an unrelated database cache. [S1][S2]",
        task,
    ) is False


def test_section_content_units_bind_heading_and_post_code_citation_to_their_fact():
    units = research_module._architect_section_content_units(
        "## pathlib.Path conversion\n\n"
        "The parser can use this converter [S1].\n\n"
        "```python\nvalue = pathlib.Path(raw)\n```\n\n"
        "Evidence: [S1]"
    )

    assert len(units) == 2
    assert units[0].startswith("## pathlib.Path conversion\n")
    assert "[S1]" in units[0]
    assert units[1].endswith("Evidence: [S1]")


def test_hard_assertion_anchor_parser_ignores_backticked_prose_but_keeps_identifiers():
    anchors = research_module._architect_hard_assertion_anchors(
        "Use `operator, which replaces the traditional` prose and `pathlib.Path` in the example."
    )

    assert ("api", "pathlib.path") in anchors
    assert all("operator" not in value for _kind, value in anchors)


def test_hard_assertion_anchor_parser_ignores_source_filenames_and_bare_domains():
    anchors = research_module._architect_hard_assertion_anchors(
        "Secondary source EU AI Act Guide Chinese Version.pdf and artificialintelligenceact.eu "
        "are compared with pathlib.Path."
    )

    assert ("api", "pathlib.path") in anchors
    assert ("api", "version.pdf") not in anchors
    assert ("api", "artificialintelligenceact.eu") not in anchors


def test_hard_assertion_verifies_spaced_assignment_and_metadata_module_name():
    issues = research_module._architect_hard_assertion_issues(
        "Use `type=pathlib.Path` with `argparse`.",
        "The converter accepts type = pathlib . Path for command-line arguments.",
        source_metadata={"title": "argparse documentation"},
    )

    assert issues == []


def test_hard_assertion_rejects_assignment_and_behavior_not_shown_by_signature():
    issues = research_module._architect_hard_assertion_issues(
        "Typer validates CLI paths with path_type=Path and returns Path objects.",
        "Argument(*, exists=False, file_okay=True, dir_okay=True, path_type=None)",
        source_metadata={"title": "Typer Argument reference"},
    )

    assert "api_anchor_not_in_evidence:path_type=path" in issues
    assert "conversion_behavior_not_entailed" in issues
    assert "validation_behavior_not_entailed" in issues


def test_hard_assertion_does_not_treat_evidence_verification_as_api_validation():
    issues = research_module._architect_hard_assertion_issues(
        "This verified evidence supports using pathlib.Path in the parser.",
        "The parser accepts pathlib.Path as a converter.",
        source_metadata={"title": "Argparse documentation"},
    )
    passive_issues = research_module._architect_hard_assertion_issues(
        "This recommendation is verified by the cited pathlib.Path evidence.",
        "The parser accepts pathlib.Path as a converter.",
        source_metadata={"title": "Argparse documentation"},
    )

    assert "validation_behavior_not_entailed" not in issues
    assert "validation_behavior_not_entailed" not in passive_issues
    heading_issues = research_module._architect_hard_assertion_issues(
        "## Path Validation Foundations\n\npathlib.Path represents filesystem paths.",
        "pathlib.Path represents filesystem paths.",
        source_metadata={"title": "pathlib documentation"},
    )
    assert "validation_behavior_not_entailed" not in heading_issues
    numbered_heading_issues = research_module._architect_hard_assertion_issues(
        "## Runtime section 3\n\nSecondary source states that pathlib.Path represents filesystem paths.",
        "pathlib.Path represents filesystem paths.",
        source_metadata={"title": "pathlib documentation"},
    )
    assert not any(issue.startswith("quantity_anchor_not_in_evidence:") for issue in numbered_heading_issues)


def test_hard_assertion_requires_evidence_for_stability_portability_and_replacement_relations():
    issues = research_module._architect_hard_assertion_issues(
        "pathlib.Path is a stable, version-agnostic, cross-platform abstraction that replaces os.path.",
        "pathlib.Path represents concrete filesystem paths.",
        source_metadata={"title": "pathlib documentation"},
    )

    assert "stability_behavior_not_entailed" in issues
    assert "portability_behavior_not_entailed" in issues
    assert "replacement_behavior_not_entailed" in issues


def test_hard_assertion_requires_evidence_for_priority_equivalence_and_category_relations():
    issues = research_module._architect_hard_assertion_issues(
        (
            "Using pathlib at the CLI boundary is the highest-leverage foundational design decision; "
            "it occupies its own category and exposes the same call surfaces regardless of whether the OS is touched."
        ),
        "Pure path classes provide purely computational operations without I/O.",
        source_metadata={"title": "pathlib documentation"},
    )

    assert "priority_behavior_not_entailed" in issues
    assert "equivalence_behavior_not_entailed" in issues
    assert "categorization_behavior_not_entailed" in issues


def test_hard_assertion_gate_rejects_unmentioned_bare_module_replacement_claims():
    issues = research_module._architect_hard_assertion_issues(
        "pathlib replaces os, glob, and shutil for these operations.",
        "The Path object provides a cross-platform way to read, write, move, and delete files.",
        source_metadata={"title": "Python pathlib guide"},
    )

    assert "api_anchor_not_in_evidence:glob" in issues
    assert "api_anchor_not_in_evidence:os" in issues
    assert "api_anchor_not_in_evidence:shutil" in issues


def test_hard_assertion_requires_explicit_evidence_for_pure_path_no_io_semantics():
    unsupported = research_module._architect_hard_assertion_issues(
        "Pure paths provide path-handling operations without filesystem access.",
        "PureWindowsPath('c:/Program Files/').drive returns 'c:'.",
        source_metadata={"title": "pathlib documentation"},
    )
    supported = research_module._architect_hard_assertion_issues(
        "Pure paths provide path-handling operations without filesystem access.",
        (
            "Pure path objects provide path-handling operations which don't "
            "actually access a filesystem."
        ),
        source_metadata={"title": "pathlib documentation"},
    )

    assert "pure_path_no_filesystem_access_not_entailed" in unsupported
    assert "pure_path_no_filesystem_access_not_entailed" not in supported


def test_hard_assertion_does_not_promote_framework_example_to_path_api_ownership():
    issues = research_module._architect_hard_assertion_issues(
        "pathlib.Path provides read_text(), is_file(), is_dir(), and exists().",
        "config_file.read_text(); config_file.is_file(); config_file.exists()",
        source_metadata={
            "title": "Typer Path parameter example",
            "url": "https://typer.tiangolo.com/tutorial/parameter-types/path/",
        },
    )

    assert "api_ownership_not_entailed" in issues


def test_required_claim_sources_prefer_answerable_primary_current_evidence():
    sources = [
        {
            "citationKey": "S1",
            "tier": "secondary",
            "authorityScore": 60,
            "answerabilityScore": 50,
            "evidenceCandidates": [{"text": "weak"}],
        },
        {
            "citationKey": "S2",
            "tier": "primary",
            "authorityScore": 90,
            "answerabilityScore": 100,
            "version": "3.14",
            "evidenceCandidates": [{"text": "focused"}],
        },
        {
            "citationKey": "S3",
            "tier": "primary",
            "authorityScore": 85,
            "answerabilityScore": 50,
            "publishedAt": "2026-07-01",
            "evidenceCandidates": [{"text": "current"}],
        },
        {
            "citationKey": "S4",
            "tier": "primary",
            "authorityScore": 99,
            "answerabilityScore": 100,
            "subjectFocused": False,
            "evidenceCandidates": [{"text": "wrong subject"}],
        },
    ]

    assert research_module._architect_required_claim_source_keys(sources, limit=2) == ["S2", "S3"]


def test_required_claim_sources_reserve_one_answerable_source_per_explicit_facet():
    sources = [
        {
            "citationKey": "S1",
            "researchFacetId": "timeline",
            "tier": "primary",
            "authorityScore": 95,
            "answerabilityScore": 100,
            "evidenceCandidates": [{"text": "Timeline fact one."}],
        },
        {
            "citationKey": "S2",
            "researchFacetId": "timeline",
            "tier": "primary",
            "authorityScore": 94,
            "answerabilityScore": 99,
            "evidenceCandidates": [{"text": "Timeline fact two."}],
        },
        {
            "citationKey": "S3",
            "researchFacetId": "penalties",
            "tier": "primary",
            "authorityScore": 90,
            "answerabilityScore": 80,
            "evidenceCandidates": [{"text": "Penalty fact."}],
        },
        {
            "citationKey": "S4",
            "researchFacetId": "open-questions",
            "tier": "secondary",
            "authorityScore": 65,
            "answerabilityScore": 60,
            "evidenceCandidates": [{"text": "Open question evidence."}],
        },
    ]

    selected = research_module._architect_required_claim_source_keys(sources, limit=3)

    assert set(selected) == {"S1", "S3", "S4"}
    assert "S2" not in selected


def test_required_claim_sources_reserve_named_product_dimension_matrix():
    question = (
        "截至 2026 年 7 月，请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 "
        "GitHub Copilot CLI 的 Windows 安装、模型、MCP、价格、隐私与限制。"
    )
    products = [
        ("OpenAI Codex CLI", "learn.chatgpt.com", "openai-codex-cli"),
        ("Claude Code", "code.claude.com", "claude-code"),
        ("Gemini CLI", "geminicli.com", "gemini-cli"),
        ("GitHub Copilot CLI", "docs.github.com", "github-copilot-cli"),
    ]
    sources = []
    citation_index = 1
    for product, host, slug in products:
        for dimension in ("operations", "governance"):
            sources.append(
                {
                    "citationKey": f"S{citation_index}",
                    "title": f"{product} {dimension} documentation",
                    "url": f"https://{host}/{slug}/{dimension}",
                    "tier": "primary",
                    "authorityScore": 90,
                    "answerabilityScore": 70,
                    "researchFacetId": f"entity-{slug}-{dimension}",
                    "evidenceCandidates": [
                        {
                            "researchFacetId": f"entity-{slug}-{dimension}",
                            "text": f"{product} has a documented {dimension} boundary.",
                        }
                    ],
                }
            )
            citation_index += 1
    sources.append(
        {
            "citationKey": "S9",
            "title": "Claude Code extra high-rank page",
            "url": "https://code.claude.com/docs/en/extra",
            "tier": "primary",
            "authorityScore": 99,
            "answerabilityScore": 100,
            "researchFacetId": "entity-claude-code-operations",
            "evidenceCandidates": [
                {
                    "researchFacetId": "entity-claude-code-operations",
                    "text": "Claude Code has another operational detail.",
                }
            ],
        }
    )

    selected = research_module._architect_required_claim_source_keys(
        sources,
        limit=TARGET_RESEARCH_SOURCE_COUNT,
        question=question,
    )

    selected_sources = [
        source for source in sources if source["citationKey"] in selected
    ]
    assert len(selected) == TARGET_RESEARCH_SOURCE_COUNT
    assert {
        source["researchFacetId"] for source in selected_sources
    } == {
        f"entity-{slug}-{dimension}"
        for _product, _host, slug in products
        for dimension in ("operations", "governance")
    }
    assert not {"S3", "S9"}.issubset(selected)


def test_required_claim_sources_retain_attributed_experience_at_normal_depth():
    sources = [
        {
            "citationKey": f"S{index}",
            "tier": "primary",
            "authorityScore": 85,
            "answerabilityScore": 50,
            "publishedAt": f"201{index}-01-01",
            "evidenceCandidates": [
                {"text": f"Historical protocol fact {index} supports a path interface."}
            ],
        }
        for index in range(1, 9)
    ]
    sources.append(
        {
            "citationKey": "S9",
            "tier": "secondary",
            "authorityScore": 60,
            "answerabilityScore": 50,
            "evidenceCandidates": [
                {
                    "text": (
                        "I recommend using Path objects for this on-topic workflow; "
                        "the article explains the implementation trade-off."
                    )
                }
            ],
        }
    )
    sources.append(
        {
            "citationKey": "S10",
            "tier": "secondary",
            "authorityScore": 60,
            "answerabilityScore": 50,
            "evidenceCandidates": [
                {
                    "text": (
                        "This field guide recommends a different on-topic practice "
                        "and records the operational trade-off."
                    )
                }
            ],
        }
    )

    selected = research_module._architect_required_claim_source_keys(
        sources,
        limit=TARGET_RESEARCH_SOURCE_COUNT,
    )

    assert len(selected) == TARGET_RESEARCH_SOURCE_COUNT
    assert "S9" in selected
    assert "S10" in selected
    assert len([key for key in selected if key not in {"S9", "S10"}]) == 6


def test_section_normative_attribution_requires_cue_or_bound_composite_inference():
    task = {
        "targetMinChars": 0,
        "targetMaxChars": 2000,
        "minimumAcceptableChars": 0,
        "requiredCitationKeys": ["S1"],
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Path objects represent filesystem paths.",
                "claimType": "source_fact",
                "supportingSources": [
                    {
                        "citationKey": "S1",
                        "tier": "primary",
                        "authorityScore": 90,
                        "version": "3.14",
                    }
                ],
                "evidenceExcerpt": "Path objects represent filesystem paths.",
            }
        ],
        "compositeInferences": [
            {
                "inferenceId": "I1",
                "inference": "use Path as the default representation",
                "premiseClaimIds": ["C1"],
            }
        ],
    }
    unsupported = research_module._architect_section_issues(
        "Using Path is the official best practice [S1].",
        task,
        complete=True,
    )
    supported = research_module._architect_section_issues(
        "Practical synthesis: use Path as the default representation [S1].",
        task,
        complete=True,
    )
    prefer_unsupported = research_module._architect_section_issues(
        "Prefer Path as the default representation [S1].",
        task,
        complete=True,
    )

    assert "section_normative_attribution_not_entailed" in unsupported
    assert "section_normative_attribution_not_entailed" in prefer_unsupported
    assert "section_normative_attribution_not_entailed" not in supported

    unbound = research_module._architect_section_issues(
        "Practical synthesis: prefer Path for every CLI boundary [S1].",
        {**task, "compositeInferences": []},
        complete=True,
    )
    assert "section_normative_attribution_not_entailed" in unbound
    assert "section_synthesis_inference_not_bound" in unbound
    heading_unbound = research_module._architect_section_issues(
        "## Practical synthesis for CLI authors\n\nPrefer Path for every CLI boundary [S1].",
        {**task, "compositeInferences": []},
        complete=True,
    )
    assert "section_synthesis_inference_not_bound" in heading_unbound

    synthesis_task = {**task, "requiresSynthesisConclusion": True}
    assert "section_synthesis_conclusion_missing" not in research_module._architect_section_issues(
        "Path objects represent filesystem paths [S1].",
        synthesis_task,
        complete=True,
    )
    assert "section_synthesis_conclusion_missing" not in research_module._architect_section_issues(
        "Practical synthesis: use Path as the default representation [S1].",
        synthesis_task,
        complete=True,
    )


def test_section_omissions_defer_registered_composite_inferences_to_assembly():
    task = {
        "targetMinChars": 0,
        "targetMaxChars": 3000,
        "minimumAcceptableChars": 0,
        "requiredCitationKeys": ["S1", "S2"],
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Codex provides a local sandbox boundary.",
                "evidenceExcerpt": "Codex provides a local sandbox boundary.",
                "supportingSources": [{"citationKey": "S1", "tier": "primary"}],
            },
            {
                "claimId": "C2",
                "claim": "Claude provides a signed Windows binary.",
                "evidenceExcerpt": "Claude provides a signed Windows binary.",
                "supportingSources": [{"citationKey": "S2", "tier": "primary"}],
            },
        ],
        "compositeInferences": [
            {
                "inferenceId": "I-individual",
                "inference": "For an individual, prefer Codex when the local sandbox matters.",
                "premiseClaimIds": ["C1"],
            },
            {
                "inferenceId": "I-team",
                "inference": "For a team, prefer Claude when a signed Windows binary matters.",
                "premiseClaimIds": ["C2"],
            },
        ],
        "requiresSynthesisConclusion": True,
    }
    partial = (
        "Practical synthesis: For an individual, prefer Codex when the local sandbox matters [S1].\n\n"
        "Claude provides a signed Windows binary [S2]."
    )

    issues = research_module._architect_section_issues(partial, task, complete=True)

    assert "section_composite_inference_missing:I-individual" not in issues
    assert "section_composite_inference_missing:I-team" not in issues


def test_current_or_official_guidance_requires_current_primary_source_role():
    secondary_claim = {
        "claimId": "C1",
        "claim": "An editorial recommends pathlib.",
        "claimType": "explicit_normative",
        "normativeCue": "should use pathlib",
        "supportingSources": [
            {
                "citationKey": "S1",
                "tier": "secondary", "sourceRole": "secondary",
                "authorityScore": 90,
                "publishedAt": "2018-12-01",
            }
        ],
        "evidenceExcerpt": "Developers should use pathlib for this example.",
    }
    primary_claim = {
        **secondary_claim,
        "supportingSources": [
            {
                "citationKey": "S2",
                "tier": "primary", "sourceRole": "primary",
                "authorityScore": 90,
                "version": "3.14.6",
                "url": "https://docs.python.org/3/library/pathlib.html",
            }
        ],
    }
    old_version_claim = {
        **primary_claim,
        "supportingSources": [
            {
                "citationKey": "S3",
                "tier": "primary", "sourceRole": "primary",
                "authorityScore": 90,
                "version": "3.6",
                "publishedAt": "2016-05-11",
                "url": "https://peps.python.org/pep-0519/",
            }
        ],
    }

    assert "primary_source_role_required" in research_module._architect_source_role_issues(
        "This is the current official guidance.",
        [secondary_claim],
    )
    assert "current_document_evidence_required" not in research_module._architect_source_role_issues(
        "This is the current official guidance.",
        [secondary_claim],
    )
    assert research_module._architect_source_role_issues(
        "Historical secondary commentary recommends pathlib.",
        [secondary_claim],
    ) == []
    assert "secondary_source_role_required" in research_module._architect_source_role_issues(
        "This recommends pathlib for CLI tools.",
        [secondary_claim],
    )
    assert "secondary_source_role_required" in research_module._architect_source_role_issues(
        "Pathlib joins filesystem paths.",
        [secondary_claim],
    )
    assert research_module._architect_source_role_issues(
        "This is the current official guidance.",
        [primary_claim],
    ) == []
    assert research_module._architect_source_role_issues(
        "This is the current official guidance.",
        [old_version_claim],
    ) == []


def test_current_runtime_context_is_not_misclassified_as_freshness_claim():
    secondary_claim = {
        "claimId": "C1",
        "claim": "The separator is selected for the current system.",
        "claimType": "source_fact",
        "supportingSources": [
            {
                "citationKey": "S1",
                "title": "Path tutorial",
                "tier": "secondary", "sourceRole": "secondary",
                "authorityScore": 60,
            }
        ],
        "evidenceExcerpt": "The separator is selected for the current system.",
    }
    attributed = (
        "Secondary source “Path tutorial” states: "
        "The separator is selected for the current system [S1]."
    )

    assert research_module._architect_source_role_issues(
        attributed,
        [secondary_claim],
    ) == []
    assert "primary_source_role_required" in (
        research_module._architect_source_role_issues(
            "This is the current guidance [S1].",
            [secondary_claim],
        )
    )
    assert research_module._ARCHITECT_CURRENT_OR_OFFICIAL_RE.search(
        "当前系统使用平台对应的路径分隔符。"
    ) is None
    assert research_module._ARCHITECT_CURRENT_OR_OFFICIAL_RE.search(
        "这是当前官方指南。"
    ) is not None

    historical_claim = {
        **secondary_claim,
        "claim": "As of Python 3.6, Path objects are accepted by the path protocol.",
        "evidenceExcerpt": "As of Python 3.6, Path objects are accepted by the path protocol.",
    }
    historical_attribution = (
        "- Secondary source “Path tutorial” states: As of Python 3.6, "
        "Path objects are accepted by the path protocol [S1]."
    )
    assert research_module._architect_source_role_issues(
        historical_attribution,
        [historical_claim],
    ) == []
    assert research_module._ARCHITECT_CURRENT_OR_OFFICIAL_RE.search(
        "截至 Python 3.6，该教程记录了这一行为。"
    ) is None


def test_runtime_temporal_boundary_note_is_kept_as_cited_governance_metadata():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "The documented command is /mcp.",
                "supportingSources": [
                    {
                        "citationKey": "S32",
                        "title": "Undated command documentation",
                    }
                ],
                "evidenceExcerpt": "Use /mcp to inspect the documented integration.",
            }
        ],
        "compositeInferences": [],
        "requiredCitationKeys": ["S32"],
        "targetMinChars": 0,
        "targetMaxChars": 2_000,
        "minimumAcceptableChars": 0,
    }
    note = (
        "边界说明：本节引用的来源未标年份，因此上述 `/mcp` 名称只能按该页面既定写法呈现，"
        "不能据此推断它是截至 2026 年 7 月的最新命令形态；适用性需读者自行核验。[S32]"
    )

    filtered, dropped = research_module._architect_drop_unsupported_section_units(
        note,
        task,
    )
    issues = research_module._architect_section_issues(note, task, complete=True)

    assert dropped == 0
    assert filtered == note
    assert not any("source_role_required" in issue for issue in issues)
    assert not any("unsupported_hard_fact" in issue for issue in issues)


def test_runtime_can_add_an_explicit_secondary_role_label_without_changing_the_fact():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Pathlib joins filesystem paths.",
                "claimType": "source_fact",
                "supportingSources": [
                    {
                        "citationKey": "S1",
                        "tier": "secondary", "sourceRole": "secondary",
                        "authorityScore": 60,
                    }
                ],
                "evidenceExcerpt": "Pathlib joins filesystem paths.",
            }
        ]
    }

    repaired, count = research_module._architect_repair_source_role_labels(
        "## Joining paths\n\nPathlib joins filesystem paths [S1].",
        task,
    )

    assert count == 1
    assert repaired.startswith("## Joining paths\nSecondary source states: ")
    assert "Pathlib joins filesystem paths [S1]." in repaired
    assert "secondary_source_role_required" not in research_module._architect_section_issues(
        repaired,
        {**task, "targetMinChars": 0, "targetMaxChars": 2000, "requiredCitationKeys": ["S1"]},
        complete=True,
    )


def test_runtime_secondary_role_repair_names_the_source_and_does_not_double_prefix():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Path objects provide a cross-platform way to manipulate files.",
                "claimType": "source_fact",
                "supportingSources": [
                    {
                        "citationKey": "S2",
                        "title": "Python pathlib tutorial",
                        "tier": "secondary", "sourceRole": "secondary",
                        "authorityScore": 60,
                    }
                ],
                "evidenceExcerpt": "Path objects provide a cross-platform way to manipulate files.",
            }
        ]
    }

    repaired, count = research_module._architect_repair_source_role_labels(
        "Path objects provide a cross-platform way to manipulate files [S2].",
        task,
    )
    repaired_again, second_count = research_module._architect_repair_source_role_labels(repaired, task)

    assert count == 1
    assert repaired.startswith("Secondary source “Python pathlib tutorial” states: ")
    assert repaired_again == repaired
    assert second_count == 0


def test_mundane_secondary_fact_is_governed_even_without_a_high_risk_anchor():
    claim = {
        "claimId": "C1",
        "claim": "Path methods return Path objects and permit method chaining.",
        "claimType": "source_fact",
        "supportingSources": [
            {
                "citationKey": "S2",
                "title": "Path tutorial",
                "tier": "secondary", "sourceRole": "secondary",
            }
        ],
        "evidenceExcerpt": "Path methods return Path objects, which allows for method chaining.",
    }
    task = {
        "assignedClaims": [claim],
        "requiredCitationKeys": ["S2"],
        "targetMinChars": 0,
        "targetMaxChars": 2000,
        "minimumAcceptableChars": 0,
        "compositeInferences": [],
    }
    plain = "Path methods return Path objects and permit method chaining [S2]."

    dropped, drop_count = research_module._architect_drop_unsupported_section_units(plain, task)
    repaired, repair_count = research_module._architect_repair_source_role_labels(plain, task)
    governed, governed_drops = research_module._architect_drop_unsupported_section_units(repaired, task)

    assert dropped == ""
    assert drop_count == 1
    assert repair_count == 1
    assert governed_drops == 0
    assert governed == repaired
    assert governed.startswith("Secondary source “Path tutorial” states: ")


def test_cross_source_inference_cue_requires_an_explicit_synthesis_label():
    claims = [
        {
            "claimId": "C1",
            "claim": "The parser accepts pathlib.Path as a converter.",
            "supportingSources": [{"citationKey": "S1", "tier": "primary"}],
            "evidenceExcerpt": "The parser accepts pathlib.Path as a converter.",
        },
        {
            "claimId": "C2",
            "claim": "The CLI option exists=True checks for an existing path.",
            "supportingSources": [{"citationKey": "S3", "tier": "primary"}],
            "evidenceExcerpt": "The CLI option exists=True checks for an existing path.",
        },
    ]
    inference = (
        "Using pathlib.Path as a converter combined with exists=True provides typed, validated input."
    )
    task = {
        "assignedClaims": claims,
        "compositeInferences": [
            {"inferenceId": "I1", "inference": inference, "premiseClaimIds": ["C1", "C2"]}
        ],
        "requiredCitationKeys": ["S1", "S3"],
        "targetMinChars": 0,
        "targetMaxChars": 2000,
        "minimumAcceptableChars": 0,
    }

    unlabeled, unlabeled_drops = research_module._architect_drop_unsupported_section_units(
        f"{inference} [S1][S3]",
        task,
    )
    labeled, labeled_drops = research_module._architect_drop_unsupported_section_units(
        f"Practical synthesis: {inference} [S1][S3]",
        task,
    )

    assert unlabeled == ""
    assert unlabeled_drops == 1
    assert labeled_drops == 0
    assert labeled.startswith("Practical synthesis:")


def test_single_source_combined_with_path_separator_is_not_cross_source_synthesis():
    claim = {
        "claimId": "C1",
        "claim": (
            "When the first element is a Path object, following str elements can be "
            "combined with / to create a new Path object."
        ),
        "supportingSources": [{"citationKey": "S5", "tier": "primary"}],
        "evidenceExcerpt": (
            "If the first element is a Path object the next ones can be str, and it will "
            "create a new Path object from that."
        ),
    }
    task = {
        "assignedClaims": [claim],
        "compositeInferences": [],
        "requiredCitationKeys": ["S5"],
        "targetMinChars": 0,
        "targetMaxChars": 2_000,
        "minimumAcceptableChars": 0,
    }
    section = f"{claim['claim']} [S5]"

    issues = research_module._architect_section_issues(section, task, complete=True)
    filtered, drops = research_module._architect_drop_unsupported_section_units(
        section,
        task,
    )

    assert "section_synthesis_label_required" not in issues
    assert drops == 0
    assert filtered == section


def test_section_length_is_advisory_across_previous_threshold():
    task = {
        "assignedClaims": [],
        "compositeInferences": [],
        "requiredCitationKeys": ["S1"],
        "targetMinChars": 800,
        "targetMaxChars": 2_000,
        "minimumAcceptableChars": 528,
    }

    near_floor = research_module._architect_section_issues(
        ("a" * 525) + " [S1]",
        task,
        complete=True,
    )
    shallow = research_module._architect_section_issues(
        ("a" * 450) + " [S1]",
        task,
        complete=True,
    )

    assert not any(issue.startswith("section_depth_not_met:") for issue in near_floor)
    assert shallow == []


def test_runtime_source_role_repair_normalizes_a_chinese_label_in_english_prose():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Path methods return Path objects.",
                "claimType": "source_fact",
                "supportingSources": [
                    {
                        "citationKey": "S1",
                        "title": "Python documentation",
                        "tier": "primary",
                    },
                    {
                        "citationKey": "S2",
                        "title": "Python pathlib tutorial",
                        "tier": "secondary",
                    },
                ],
                "evidenceExcerpt": "Path methods return Path objects.",
            }
        ]
    }
    section = (
        "含二手来源《Python pathlib tutorial》的综合判断："
        "**本报告的综合判断**: Path methods return Path objects [S1] [S2]."
    )

    repaired, count = research_module._architect_repair_source_role_labels(section, task)
    repaired_again, second_count = research_module._architect_repair_source_role_labels(repaired, task)

    assert count == 1
    assert repaired.startswith(
        "Mixed-source synthesis with secondary context from “Python pathlib tutorial”: "
    )
    assert "本报告" not in repaired
    assert repaired_again == repaired
    assert second_count == 0


@pytest.mark.parametrize("title,body", [
    ("四部门发布《内容标识办法》的解读", "所读资料记录了内容标识的适用范围与实施条件。"),
    ('Why “official” and "required" need context', "The article records a bounded implementation example."),
])
def test_nested_title_attribution_is_recognized_and_repair_is_idempotent(title, body):
    claim = {"claimId": "C1", "claim": body, "claimType": "source_fact",
             "evidenceExcerpt": body,
             "supportingSources": [{"citationKey": "S1", "title": title, "tier": "secondary", "sourceRole": "secondary"}]}
    task = {"assignedClaims": [claim]}
    repaired, count = research_module._architect_repair_source_role_labels(body + " [S1]", task)
    assert count == 1
    assert title in repaired
    assert research_module._architect_source_role_issues(repaired, [claim]) == []
    assert research_module._architect_repair_source_role_labels(repaired, task) == (repaired, 0)
    _, fragments = research_module._architect_split_unit_for_validation(
        repaired.rstrip(".") + ". Another bounded detail is recorded [S1]."
    )
    assert len(fragments) >= 2
    assert all(research_module._architect_source_role_issues(fragment, [claim]) == [] for fragment in fragments)
    # Quoted title words are not assertions; actual unsupported official claims
    # after the label remain governed and must still fail.
    unsupported = repaired.replace(body, "This is the current official standard.")
    assert "primary_source_role_required" in research_module._architect_source_role_issues(unsupported, [claim])


def test_secondary_source_title_does_not_manufacture_a_normative_assertion():
    claim = {
        "claimId": "C13",
        "claim": "Path methods return Path objects and allow method chaining.",
        "claimType": "source_fact",
        "supportingSources": [
            {
                "citationKey": "S10",
                "title": "Why you should be using pathlib",
                "tier": "secondary", "sourceRole": "secondary",
                "authorityScore": 60,
            }
        ],
        "evidenceExcerpt": "Path methods return Path objects, which allows for method chaining.",
    }
    task = {
        "assignedClaims": [claim],
        "requiredCitationKeys": ["S10"],
        "targetMinChars": 0,
        "targetMaxChars": 2000,
        "minimumAcceptableChars": 0,
        "compositeInferences": [],
    }
    repaired, repair_count = research_module._architect_repair_source_role_labels(
        "Path methods return Path objects and allow method chaining [S10].",
        task,
    )

    assert repair_count == 1
    assert "Why you should be using pathlib" in repaired
    assert research_module._architect_source_role_issues(repaired, [claim]) == []
    assert "section_normative_attribution_not_entailed" not in (
        research_module._architect_section_issues(repaired, task, complete=True)
    )
    headed = f"## Best Practices for Path Objects\n\n{repaired}"
    assert "section_normative_attribution_not_entailed" not in (
        research_module._architect_section_issues(headed, task, complete=True)
    )
    parenthetical_label = (
        'Secondary source "Why you should be using pathlib" (S10) states: '
        "Path methods return Path objects and allow method chaining [S10]."
    )
    assert research_module._ARCHITECT_NORMATIVE_ASSERTION_RE.search(
        research_module._architect_assertion_text(parenthetical_label)
    ) is None
    filtered, dropped = research_module._architect_drop_unsupported_section_units(
        headed,
        task,
    )
    assert dropped == 0
    assert filtered.split() == headed.split()
    governed_claim = {
        **claim,
        "claim": (
            "Secondary source “Why you should be using pathlib” states: "
            "Path methods return Path objects and allow method chaining."
        ),
        "sourceRole": "secondary",
        "sourceClaim": claim["claim"],
    }
    governed_task = {**task, "assignedClaims": [governed_claim]}
    governed_restored = "- " + governed_claim["claim"] + " [S1]"
    assert "- Secondary source" in governed_restored
    assert "section_normative_attribution_not_entailed" not in (
        research_module._architect_section_issues(
            governed_restored,
            governed_task,
            complete=True,
        )
    )

    actual_recommendation = (
        "Secondary source “Why you should be using pathlib” states: "
        "You should use pathlib for every CLI boundary [S10]."
    )
    assert "section_normative_attribution_not_entailed" in (
        research_module._architect_section_issues(
            actual_recommendation,
            task,
            complete=True,
        )
    )


def test_official_source_question_retains_and_attributes_secondary_experience():
    secondary_fact = {
        "claimId": "C1",
        "claim": "Path objects demonstrate filesystem behavior.",
        "claimType": "source_fact",
        "supportingSources": [
            {"citationKey": "S1", "tier": "secondary", "sourceRole": "secondary", "authorityScore": 90}
        ],
    }
    secondary_recommendation = {
        "claimId": "C2",
        "claim": "Pathlib should be used everywhere.",
        "claimType": "explicit_normative",
        "normativeCue": "should use pathlib",
        "supportingSources": [
            {"citationKey": "S1", "tier": "secondary", "sourceRole": "secondary", "authorityScore": 90}
        ],
    }
    primary_recommendation = {
        "claimId": "C3",
        "claim": "The official documentation recommends Path for this task.",
        "claimType": "explicit_normative",
        "normativeCue": "most likely what you need",
        "supportingSources": [
            {"citationKey": "S2", "tier": "primary", "sourceRole": "primary", "authorityScore": 85}
        ],
    }

    filtered, governed = (
        research_module._architect_govern_claim_source_roles_for_question(
            [secondary_fact, secondary_recommendation, primary_recommendation],
            "Compare current best practices and cite official sources.",
        )
    )
    assert [claim["claimId"] for claim in filtered] == ["C1", "C2", "C3"]
    assert filtered[0]["claim"].startswith("Secondary source")
    assert filtered[0]["sourceClaim"] == secondary_fact["claim"]
    assert filtered[0]["sourceRole"] == "secondary"
    assert filtered[1]["claim"].startswith("Secondary source")
    assert filtered[1]["claimType"] == "explicit_normative"
    assert governed == ["C1", "C2"]

    unscoped, unscoped_dropped = (
        research_module._architect_govern_claim_source_roles_for_question(
            [secondary_fact, secondary_recommendation, primary_recommendation],
            "What do experienced authors recommend?",
        )
    )
    assert unscoped == [secondary_fact, secondary_recommendation, primary_recommendation]
    assert unscoped_dropped == []


def test_hard_assertion_allows_only_formatting_changes_inside_code_blocks():
    excerpt = "parser . add_argument ( 'path' , type = pathlib . Path )"
    assert research_module._architect_hard_assertion_issues(
        "```python\nparser.add_argument('path', type=pathlib.Path)\n```",
        excerpt,
    ) == []
    assert research_module._architect_hard_assertion_issues(
        "```python\nparser.add_argument('other', type=pathlib.Path)\n```",
        excerpt,
    )


def test_cross_platform_evidence_does_not_entail_specific_path_separator_behavior():
    excerpt = "Path objects provide a cross-platform way to manipulate files."

    assert "path_separator_behavior_not_entailed" in (
        research_module._architect_hard_assertion_issues(
            "Path objects handle both forward and backward slashes.",
            excerpt,
        )
    )
    assert "path_separator_behavior_not_entailed" in (
        research_module._architect_hard_assertion_issues(
            "Path objects fix path separator issues.",
            excerpt,
        )
    )
    assert research_module._architect_hard_assertion_issues(
        "Path objects provide a cross-platform way to manipulate files.",
        excerpt,
    ) == []


def test_bringing_functionality_together_does_not_entail_centralization_or_consolidation():
    excerpt = "The pathlib module brings together functionality from os, glob, and shutil."

    assert "centralization_behavior_not_entailed" in (
        research_module._architect_hard_assertion_issues(
            "Pathlib centralizes path operations in a single coherent API.",
            excerpt,
        )
    )
    assert "replacement_behavior_not_entailed" in (
        research_module._architect_hard_assertion_issues(
            "Pathlib consolidates functions from os, glob, and shutil.",
            excerpt,
        )
    )
    assert research_module._architect_hard_assertion_issues(
        "Pathlib brings together functionality from os, glob, and shutil.",
        excerpt,
    ) == []


def test_pep_proposal_does_not_entail_release_introduction_or_unquoted_function_calls():
    excerpt = "This PEP proposes a file system path protocol based on os.PathLike."

    issues = research_module._architect_hard_assertion_issues(
        "PEP 519 added the protocol and allows Path objects to be accepted by open().",
        excerpt,
    )

    assert "introduction_behavior_not_entailed" in issues
    assert "api_anchor_not_in_evidence:open" in issues


def test_path_join_operator_and_named_open_function_require_direct_evidence():
    unrelated_path_excerpt = (
        "PureWindowsPath exposes drive, root, and anchor attributes for lexical path inspection."
    )
    pep_task_excerpt = "Add a protocol and update library functions in later implementation tasks."

    assert "path_join_operator_behavior_not_entailed" in research_module._architect_hard_assertion_issues(
        "The / operator joins path components in pathlib.",
        unrelated_path_excerpt,
    )
    assert "api_anchor_not_in_evidence:open" in research_module._architect_hard_assertion_issues(
        "Path objects can be used with the built-in open function.",
        pep_task_excerpt,
    )
    assert not research_module._architect_hard_assertion_issues(
        "The / operator joins path components in pathlib.",
        "You can join a Path with a child component using the / operator.",
    )


def test_qualitative_readability_claim_requires_explicit_readability_evidence():
    comparison_only = "os.path.join(base, name) and Path(base) / name are two equivalent examples."

    assert "readability_behavior_not_entailed" in research_module._architect_hard_assertion_issues(
        "The Path form is more readable and explicit.",
        comparison_only,
    )
    assert not research_module._architect_hard_assertion_issues(
        "The Path form is more readable and explicit.",
        "The Path form is more readable and more explicit than the string-based form.",
    )


def test_historical_protocol_does_not_entail_continued_current_support():
    historical_excerpt = "PEP 519 added a file system path protocol in Python 3.6."

    assert "continuity_behavior_not_entailed" in research_module._architect_hard_assertion_issues(
        "The protocol remains current and fully supported in Python 3.14.",
        historical_excerpt,
        source_metadata={"version": "3.6", "publishedAt": "2016-05-11"},
    )


def test_runtime_temporal_assessment_exposes_facts_without_a_fixed_age_cutoff():
    assessment = research_module._architect_runtime_temporal_assessment(
        [
            {
                "citationKey": "S1",
                "tier": "primary", "sourceRole": "primary",
                "version": "3.14.6",
                "url": "https://docs.python.org/3/library/pathlib.html",
            },
            {
                "citationKey": "S2",
                "tier": "primary", "sourceRole": "primary",
                "version": "3.6",
                "publishedAt": "2016-05-11",
                "url": "https://peps.python.org/pep-0519/",
            },
            {
                "citationKey": "S3",
                "tier": "secondary", "sourceRole": "secondary",
                "sourceDate": "2026-07-29",
                "sourceDateKind": "retrieved_at",
            },
            {
                "citationKey": "S4",
                "tier": "secondary", "sourceRole": "secondary",
                "url": "https://community.example/current/path-notes",
            },
            {
                "citationKey": "S5",
                "tier": "primary", "sourceRole": "primary",
                "publishedAt": "2017-01-01",
                "url": "https://official.example/stable/path-api",
            },
        ]
    )

    statuses = {item["citationKey"]: item["status"] for item in assessment["sources"]}
    assert statuses == {
        "S1": "stable_route_observed",
        "S2": "dated_context",
        "S3": "undated",
        "S4": "undated",
        "S5": "stable_route_observed",
    }
    by_key = {item["citationKey"]: item for item in assessment["sources"]}
    assert by_key["S4"].get("applicabilityBasis") is None
    assert by_key["S5"]["applicabilityBasis"] == "stable_current_primary_route"
    assert by_key["S5"]["documentDateStatus"] == "present"
    assert by_key["S5"]["stableCurrentRouteObserved"] is True
    assert by_key["S2"]["applicabilityRequiresReviewerJudgment"] is True
    assert assessment["retrievalTimeIsNotDocumentDate"] is True
    assert assessment["fixedDocumentAgeCutoffApplied"] is False
    assert assessment["applicabilityRequiresReviewerJudgment"] is True
    assert "broadCurrencyWindowDays" not in assessment


@pytest.mark.parametrize("age_days", [8, 31, 366, 1_825, 3_650, 7_300])
def test_runtime_temporal_assessment_does_not_change_status_at_age_boundaries(age_days):
    document_date = (datetime.now(timezone.utc) - timedelta(days=age_days)).date().isoformat()

    assessment = research_module._architect_runtime_temporal_assessment(
        [
            {
                "citationKey": "S1",
                "tier": "primary",
                "publishedAt": document_date,
                "url": "https://archive.example/path-api",
            }
        ]
    )

    source = assessment["sources"][0]
    assert source["status"] == "dated_context"
    assert source["documentDateStatus"] == "present"
    assert source["documentDates"] == [document_date]
    assert source["applicabilityRequiresReviewerJudgment"] is True


def test_runtime_temporal_assessment_flags_date_anomalies_before_stable_route_context():
    future_date = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()

    assessment = research_module._architect_runtime_temporal_assessment(
        [
            {
                "citationKey": "S1",
                "tier": "primary", "sourceRole": "primary",
                "publishedAt": future_date,
                "url": "https://official.example/stable/path-api",
            },
            {
                "citationKey": "S2",
                "tier": "primary", "sourceRole": "primary",
                "publishedAt": "2026-02-30",
                "url": "https://official.example/current/path-api",
            },
        ]
    )

    by_key = {item["citationKey"]: item for item in assessment["sources"]}
    assert by_key["S1"]["status"] == "future_anomaly"
    assert by_key["S1"]["documentDateStatus"] == "future_anomaly"
    assert by_key["S1"]["stableCurrentRouteObserved"] is True
    assert by_key["S2"]["status"] == "malformed_date"
    assert by_key["S2"]["documentDateStatus"] == "malformed"
    assert by_key["S2"]["malformedDocumentDates"] == ["2026-02-30"]


def test_deterministic_claim_report_preserves_source_role_and_temporal_boundaries():
    sources = [
        {
            "citationKey": "S1",
            "title": "Current official documentation",
            "url": "https://docs.python.org/3/library/pathlib.html",
            "tier": "primary", "sourceRole": "primary",
            "version": "3.14",
            "publishedAt": "2017-01-01",
            "retrievedAt": "2026-07-29T00:00:00Z",
        },
        {
            "citationKey": "S2",
            "title": "Historical protocol specification",
            "url": "https://peps.python.org/pep-0519/",
            "tier": "primary", "sourceRole": "primary",
            "version": "3.6",
            "publishedAt": "2016-05-11",
        },
        {
            "citationKey": "S3",
            "title": "Version-bounded official reference",
            "url": "https://reference.example/v2/path-api",
            "tier": "primary", "sourceRole": "primary",
            "version": "2.0",
        },
        {
            "citationKey": "S4",
            "title": "Maintainer field notes",
            "url": "https://experience.example/path-notes",
            "tier": "secondary", "sourceRole": "secondary",
            "retrievedAt": "2088-04-03T12:00:00Z",
        },
        {
            "citationKey": "S5",
            "title": "Unused source",
            "url": "https://unused.example/source",
            "tier": "secondary", "sourceRole": "secondary",
        },
    ]
    claims = [
        {
            "claimId": "C1",
            "claim": "The current documentation records a concrete path API and its operating boundary.",
            "claimType": "source_fact",
            "supportingSources": [sources[0]],
            "evidenceExcerpt": "The reference documents the concrete path API, accepted input boundary, and observable result.",
        },
        {
            "claimId": "C2",
            "claim": "The protocol specification records the Python 3.6 introduction boundary.",
            "claimType": "source_fact",
            "supportingSources": [sources[1]],
            "evidenceExcerpt": "The protocol was introduced for Python 3.6 and describes the historical interoperability contract.",
        },
        {
            "claimId": "C3",
            "claim": "The versioned reference describes behavior scoped to version 2.0.",
            "claimType": "source_fact",
            "supportingSources": [sources[2]],
            "evidenceExcerpt": "Version 2.0 accepts the documented input form and returns the documented path representation.",
        },
        {
            "claimId": "C4",
            "claim": "The maintainer notes report a practical migration experience.",
            "claimType": "source_fact",
            "supportingSources": [sources[3]],
            "evidenceExcerpt": "The maintainer reports the migration sequence, the encountered compatibility issue, and the local workaround.",
        },
    ]

    answer = research_module._assemble_architect_claim_report(
        question="What should a CLI maintainer conclude from the current and historical evidence?",
        verified_plan={
            "claimTable": claims,
            "compositeInferences": [],
            "asOf": "2026-07-29T00:00:00Z",
        },
        sources=sources,
    )
    deduped, repeated = research_module._architect_dedupe_repeated_content_units(answer)
    rededuped, second_pass_repeats = research_module._architect_dedupe_repeated_content_units(
        deduped
    )

    assert repeated >= 0
    assert rededuped == deduped
    assert second_pass_repeats == 0
    answer = deduped
    assert all(claim["claim"] in answer for claim in claims)
    assert 'Attributed statement from secondary source "Maintainer field notes"' in answer
    assert "they do not establish an official rule" in answer
    assert "source-reported document date shown; no fixed age cutoff is applied" in answer
    assert "A date or version label does not itself mean that material is superseded or deprecated" in answer
    assert "version-bounded without a parseable document date" in answer
    assert "Secondary material must remain attributed" in answer
    assert "no page publication date or version was resolved" in answer
    assert "non-temporal API" not in answer
    assert "version 3.6" in answer
    assert "document date 2016-05-11" in answer
    assert "No fixed document-age cutoff is applied" in answer
    assert "research observation time, not a publication or update date" in answer
    assert "stable/current documentation route observed at retrieval time" in answer
    assert "independent Reviewer judges adequacy for this claim" in answer
    assert "version 3.6; published 2016-05-11" in answer
    assert "retrieved 2026-07-29T00:00:00Z" in answer
    assert "Retrieval time records when the evidence was fetched" in answer
    assert "broad window" not in answer.lower()
    assert "in-window" not in answer.lower()
    assert "older document date" not in answer.lower()
    assert "2088-04-03" not in answer
    assert "[S1]" in answer and "[S2]" in answer and "[S3]" in answer and "[S4]" in answer
    assert "[S5]" not in answer
    assert "https://unused.example/source" not in answer



def test_deterministic_claim_report_never_injects_cli_guidance_into_an_unrelated_topic():
    sources = [
        {
            "citationKey": "S1",
            "title": "European Commission AI Act timeline",
            "url": "https://digital-strategy.ec.europa.eu/example-timeline",
            "tier": "primary",
        },
        {
            "citationKey": "S2",
            "title": "AI Act Article 101",
            "url": "https://ai-act-service-desk.ec.europa.eu/example-101",
            "tier": "primary",
        },
    ]
    claims = [
        {
            "claimId": "C1",
            "claim": "GPAI obligations became applicable on 2 August 2025.",
            "claimType": "source_fact",
            "supportingSources": [sources[0]],
            "evidenceExcerpt": "GPAI obligations became applicable on 2 August 2025.",
        },
        {
            "claimId": "C2",
            "claim": "Article 101 sets a maximum fine of EUR 15 million or 3% of worldwide turnover.",
            "claimType": "source_fact",
            "supportingSources": [sources[1]],
            "evidenceExcerpt": "Article 101 sets a maximum fine of EUR 15 million or 3% of worldwide turnover.",
        },
    ]

    answer = research_module._assemble_architect_claim_report(
        question="What EU AI Act obligations apply to GPAI providers in 2026?",
        verified_plan={"claimTable": claims, "compositeInferences": []},
        sources=sources,
    )

    assert all(claim["claim"] in answer for claim in claims)
    assert "## Direct answer" in answer
    for stale_term in ("argparse", "Click", "Typer", "pathlib", "CLI path", "PEP entries"):
        assert stale_term not in answer


def test_deterministic_claim_report_does_not_treat_product_configuration_as_typer_guidance():
    source = {
        "citationKey": "S1",
        "title": "Gemini CLI configuration",
        "url": "https://geminicli.com/docs/reference/configuration/",
        "tier": "primary",
    }
    claim = {
        "claimId": "C1",
        "claim": "Gemini CLI exposes telemetry configuration fields and traces default to false.",
        "claimType": "source_fact",
        "supportingSources": [source],
        "evidenceExcerpt": (
            "The telemetry configuration has enabled, traces, and target fields; "
            "traces defaults to false."
        ),
    }

    answer = research_module._assemble_architect_claim_report(
        question="Compare Windows coding agents for individual developers and teams.",
        verified_plan={"claimTable": [claim], "compositeInferences": []},
        sources=[source],
    )

    assert claim["claim"] in answer
    assert "应用配置目录" not in answer
    assert "Typer" not in answer
    assert "pathlib" not in answer


def test_deterministic_claim_report_builds_citation_bound_practical_guidance():
    sources = [
        {
            "citationKey": "S1",
            "title": "argparse command-line parser",
            "url": "https://docs.python.org/3/library/argparse.html",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S2",
            "title": "Click Parameter Types",
            "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S3",
            "title": "Typer CLI Application Directory",
            "url": "https://typer.tiangolo.com/tutorial/app-dir/",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S4",
            "title": "Typer Parameters",
            "url": "https://typer.tiangolo.com/reference/parameters/",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S5",
            "title": "pathlib filesystem paths",
            "url": "https://docs.python.org/3/library/pathlib.html",
            "tier": "primary", "sourceRole": "primary",
        },
    ]
    claim_texts = [
        "argparse parses positional arguments and options for a command-line application.",
        "Click path_type can convert an incoming path value to pathlib.Path.",
        "Typer get_app_dir provides an application directory for a CLI configuration file.",
        "Typer exists and file_okay parameters validate a Path argument at the CLI boundary.",
        "pathlib.Path provides concrete filesystem path operations.",
    ]
    claims = [
        {
            "claimId": f"C{index}",
            "claim": claim,
            "claimType": "source_fact",
            "supportingSources": [source],
            "evidenceExcerpt": claim,
        }
        for index, (claim, source) in enumerate(zip(claim_texts, sources, strict=True), start=1)
    ]
    claims.append(
        {
            "claimId": "C7",
            "claim": "Allow passing path_type=pathlib.Path.",
            "claimType": "source_fact",
            "supportingSources": [sources[1]],
            "evidenceExcerpt": "Allow passing path_type=pathlib.Path.",
        }
    )
    secondary_source = {
        "citationKey": "S6",
        "title": "Python pathlib tutorial",
        "url": "https://tutorial.example/pathlib",
        "tier": "secondary", "sourceRole": "secondary",
    }
    sources.append(secondary_source)
    claims.append(
        {
            "claimId": "C6",
            "claim": (
                "Secondary source “Python pathlib tutorial” states: You should "
                "use Path objects anywhere you work with file paths."
            ),
            "claimType": "explicit_normative",
            "normativeCue": "should use Path objects anywhere you work with file paths",
            "supportingSources": [secondary_source],
            "evidenceExcerpt": "You should use Path objects anywhere you work with file paths.",
        }
    )

    answer = research_module._assemble_architect_claim_report(
        question="What are the current best practices for pathlib in CLI tools?",
        verified_plan={"claimTable": claims, "compositeInferences": [], "asOf": "2026-07-29"},
        sources=sources,
    )
    units = research_module._architect_section_content_units(answer)
    framework_unit = next(
        unit for unit in units if "CLI parsing and framework integration" in unit
    )
    choice_unit = next(unit for unit in units if "Practical synthesis — actionable choice" in unit)
    validation_unit = next(
        unit for unit in units if "Recommended implementation (report synthesis)" in unit
    )

    assert "## Evidence-backed practical guidance" in answer
    assert "### [S1] argparse command-line parser — claim 1" in answer
    assert "### Evidence item" not in answer
    assert "**Bound evidence detail:** argparse parses positional arguments" not in answer
    assert "organizes the separate framework contracts into implementable branches" in choice_unit
    assert choice_unit.rstrip().endswith("[S1][S2][S3][S4]")
    assert "**Recommended implementation pattern (report synthesis):**" in framework_unit
    assert all(f"[S{index}]" in framework_unit for index in (2, 4, 5))
    assert "[S1]" not in framework_unit
    assert "[S3]" not in framework_unit
    assert framework_unit.rstrip().endswith("[S2][S4][S5]")
    assert "[S2]" in validation_unit
    assert "[S4]" in validation_unit
    assert "sources establish those controls individually" in validation_unit
    assert "this report composes the implementation sequence" in validation_unit
    assert "**Documented basis:**" in validation_unit
    assert "output path that has not been created" not in answer
    assert "Path operations documented by separately cited primary sources" not in answer
    integration_unit = next(
        unit
        for unit in units
        if "once the selected framework's input boundary produces a Path" in unit
    )
    assert "[S6]" not in integration_unit
    assert "makes no claim about path_type's default value" in answer
    assert "**Recommended implementation:** when the handler should receive a Path" in answer
    assert "**Recommended implementation:** receive the value as a Path-typed argument" in answer
    assert "Python pathlib tutorial" in answer
    assert "[S6]" in answer
    assert "not premises for the framework-scoped actions above" in answer
    configuration_unit = next(unit for unit in units if "Do not attribute application-directory" in unit)
    assert "[S3]" in configuration_unit
    assert "[S5]" not in configuration_unit
    assert "Source-backed application pattern" in configuration_unit



def test_cli_integration_uses_real_path_operations_not_path_protocol_facts():
    sources = [
        {
            "citationKey": "S1",
            "title": "Click Parameter Types",
            "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S2",
            "title": "os.PathLike protocol",
            "url": "https://docs.python.org/3/library/os.html",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S3",
            "title": "PurePath protocol details",
            "url": "https://docs.python.org/3/library/pathlib.html#pure-paths",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S4",
            "title": "Path filesystem operations",
            "url": "https://docs.python.org/3/library/pathlib.html",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S5",
            "title": "Typer CLI Application Directory",
            "url": "https://typer.tiangolo.com/tutorial/app-dir/",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S6",
            "title": "os module navigation",
            "url": "https://docs.python.org/3/library/os.html",
            "tier": "primary", "sourceRole": "primary",
        },
    ]
    claim_texts = [
        "Click path_type can convert an incoming path value to pathlib.Path.",
        "os.PathLike is an abstract base class for filesystem path objects.",
        "PurePath.__fspath__ may return NotImplementedError for an unsupported representation.",
        "The official example calls exists(), is_dir(), and open() on a Path object.",
        "A Path object can be combined with a string using / to create a new Path object.",
        "If you just want to read or write a file see open().",
    ]
    claims = [
        {
            "claimId": f"C{index}",
            "claim": claim,
            "claimType": "source_fact",
            "supportingSources": [source],
            "evidenceExcerpt": claim,
        }
        for index, (claim, source) in enumerate(zip(claim_texts, sources, strict=True), start=1)
    ]

    answer = research_module._assemble_architect_claim_report(
        question="What are the current best practices for pathlib in CLI tools?",
        verified_plan={"claimTable": claims, "compositeInferences": [], "asOf": "2026-07-29"},
        sources=sources,
    )
    integration_unit = next(
        unit
        for unit in research_module._architect_section_content_units(answer)
        if "once the selected framework's input boundary produces a Path" in unit
    )
    protocol_unit = next(
        unit
        for unit in research_module._architect_section_content_units(answer)
        if "Path protocol and API compatibility boundary" in unit
    )

    assert "[S1]" in integration_unit
    assert "[S4]" in integration_unit
    assert "[S5]" not in integration_unit
    assert "[S2]" not in integration_unit
    assert "[S3]" not in integration_unit
    assert "[S6]" not in integration_unit
    assert "[S2]" in protocol_unit
    assert "does not claim that any unverified API accepts or rejects PathLike" in protocol_unit


def test_deterministic_click_guidance_exposes_verified_none_default_contract():
    source = {
        "citationKey": "S1",
        "title": "Parameter Types — Click Documentation",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "tier": "primary", "sourceRole": "primary",
    }
    claims = [
        {
            "claimId": "C1",
            "claim": "Allow passing path_type=pathlib.Path.",
            "claimType": "source_fact",
            "supportingSources": [source],
            "evidenceExcerpt": "Allow passing path_type=pathlib.Path.",
        },
        {
            "claimId": "C2",
            "claim": "Click.Path's documented signature sets path_type=None.",
            "claimType": "source_fact",
            "supportingSources": [source],
            "evidenceExcerpt": "class click.Path(path_type=None)",
        },
        {
            "claimId": "C3",
            "claim": (
                "Click's path_type converts the incoming path value to the specified "
                "type; if None, it keeps Python's default, which is str; it is useful "
                "to convert to pathlib.Path."
            ),
            "claimType": "source_fact",
            "supportingSources": [source],
            "evidenceExcerpt": (
                "path_type converts the incoming path value to this type. If None, "
                "keep Python's default, which is str. Useful to convert to pathlib.Path."
            ),
        },
    ]

    answer = research_module._assemble_architect_claim_report(
        question="What are current pathlib best practices in CLI tools?",
        verified_plan={
            "claimTable": claims,
            "compositeInferences": [],
            "asOf": "2026-07-29",
        },
        sources=[source],
    )

    assert "verified signature records path_type=None" in answer
    assert "None keeps Python's default str" in answer
    assert "makes no claim about path_type's default value" not in answer


def test_claim_supplement_balance_prevents_one_secondary_source_from_crowding_out_primary_depth():
    primary = {
        "citationKey": "S1",
        "title": "Official reference",
        "tier": "primary", "sourceRole": "primary",
    }
    secondary = {
        "citationKey": "S2",
        "title": "One tutorial",
        "tier": "secondary", "sourceRole": "secondary",
    }
    claims = [
        {
            "claimId": "P1",
            "claim": "The official reference documents one contract.",
            "supportingSources": [primary],
        },
        {
            "claimId": "P2",
            "claim": "The official reference documents a second contract.",
            "supportingSources": [primary],
        },
        {
            "claimId": "P3",
            "claim": "The official reference documents a third contract.",
            "supportingSources": [primary],
        },
        *[
            {
                "claimId": f"S{index}",
                "claim": f"The tutorial reports experience {index}.",
                "supportingSources": [secondary],
            }
            for index in range(1, 5)
        ],
    ]

    balanced = research_module._architect_balance_base_claims_for_supplement(claims)

    assert [claim["claimId"] for claim in balanced] == ["P1", "P2", "S1"]


def test_deterministic_claim_report_removes_unsupported_official_recommendation_headline():
    source = {
        "citationKey": "S1",
        "title": "Official API reference",
        "url": "https://docs.example/reference",
        "tier": "primary",
    }
    answer = research_module._assemble_architect_claim_report(
        question="What are the current practices? Cite official sources.",
        verified_plan={
            "headline": "Official Best Practices",
            "asOf": "2026-07-29",
            "claimTable": [
                {
                    "claimId": "C1",
                    "claim": "The reference documents a supported API behavior.",
                    "claimType": "source_fact",
                    "supportingSources": [source],
                    "evidenceExcerpt": "The API supports the documented behavior.",
                }
            ],
            "compositeInferences": [],
        },
        sources=[source],
    )

    assert answer.startswith("# Evidence-backed practices and applicability boundaries")
    assert "# Official Best Practices" not in answer


def test_deterministic_guidance_does_not_invent_validation_from_path_conversion_only():
    sources = [
        {
            "citationKey": "S1",
            "title": "Typer CLI Application Directory",
            "url": "https://typer.tiangolo.com/tutorial/app-dir/",
            "tier": "primary", "sourceRole": "primary",
        },
        {
            "citationKey": "S2",
            "title": "Click Parameter Types",
            "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
            "tier": "primary", "sourceRole": "primary",
        },
    ]
    claims = [
        {
            "claimId": "C1",
            "claim": "Typer get_app_dir provides an application directory for a CLI configuration file.",
            "claimType": "source_fact",
            "supportingSources": [sources[0]],
            "evidenceExcerpt": "get_app_dir returns an application directory used with a Path value.",
        },
        {
            "claimId": "C2",
            "claim": "Click path_type converts an incoming value to pathlib.Path.",
            "claimType": "source_fact",
            "supportingSources": [sources[1]],
            "evidenceExcerpt": "path_type can be set to pathlib.Path to convert the incoming value.",
        },
    ]

    answer = research_module._assemble_architect_claim_report(
        question="How should pathlib be integrated into a CLI?",
        verified_plan={"claimTable": claims, "compositeInferences": [], "asOf": "2026-07-29"},
        sources=sources,
    )
    validation_unit = next(
        unit
        for unit in research_module._architect_section_content_units(answer)
        if "only the path conversion or validation capability" in unit
    )

    assert "path conversion" in validation_unit
    assert "existence" not in validation_unit
    assert "file/directory" not in validation_unit


def test_unambiguous_section_citation_repair_is_exact_excerpt_bound():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "The parser uses pathlib.Path.",
                "supportingSources": [{"citationKey": "S1", "title": "Argparse docs"}],
                "evidenceExcerpt": "The parser accepts pathlib.Path as a converter.",
            }
        ]
    }
    repaired, count = research_module._architect_repair_unambiguous_section_citations(
        "The parser uses `pathlib.Path` as a converter.",
        task,
    )

    assert count == 1
    assert repaired.endswith("[S1]")

    ambiguous_task = {
        "assignedClaims": [
            task["assignedClaims"][0],
            {
                **task["assignedClaims"][0],
                "claimId": "C2",
                "supportingSources": [{"citationKey": "S2", "title": "A second source"}],
            },
        ]
    }
    unchanged, ambiguous_count = research_module._architect_repair_unambiguous_section_citations(
        "The parser uses `pathlib.Path` as a converter.",
        ambiguous_task,
    )
    assert ambiguous_count == 0
    assert unchanged == "The parser uses `pathlib.Path` as a converter."


def test_section_unit_filter_drops_only_unsupported_hard_fact_units():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "The parser accepts pathlib.Path.",
                "supportingSources": [{"citationKey": "S1", "title": "Argparse docs"}],
                "evidenceExcerpt": "The parser accepts pathlib.Path as a converter.",
            }
        ]
    }
    filtered, dropped = research_module._architect_drop_unsupported_section_units(
        "The parser accepts `pathlib.Path` as a converter [S1].\n\n"
        "It also accepts `UnsupportedType` [S1].\n\n"
        "A plain-language boundary remains visible.",
        task,
    )

    assert dropped == 1
    assert "pathlib.Path" in filtered
    assert "UnsupportedType" not in filtered
    assert "plain-language boundary" in filtered


def test_section_unit_filter_drops_only_the_unsupported_sentence_inside_one_paragraph():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "The parser accepts pathlib.Path as a converter.",
                "supportingSources": [
                    {
                        "citationKey": "S1",
                        "title": "Argparse docs",
                        "tier": "primary",
                        "authorityScore": 90,
                        "version": "3.14.6",
                    }
                ],
                "evidenceExcerpt": "The parser accepts pathlib.Path as a converter.",
            }
        ]
    }
    diagnostics: list[str] = []
    reason_counts: dict[str, int] = {}

    filtered, dropped = research_module._architect_drop_unsupported_section_units(
        "The parser accepts `pathlib.Path` as a converter. "
        "It also accepts `UnsupportedType`. "
        "This boundary remains explicit. [S1]",
        task,
        diagnostics=diagnostics,
        reason_counts=reason_counts,
    )

    assert dropped == 1
    assert "pathlib.Path" in filtered
    assert "UnsupportedType" not in filtered
    assert "boundary remains explicit" in filtered
    assert filtered.count("[S1]") >= 2
    assert reason_counts == {"api_anchor_not_in_evidence:unsupportedtype": 1}
    assert diagnostics[0].startswith("api_anchor_not_in_evidence:unsupportedtype:")


def test_section_unit_filter_can_drop_an_unsupported_current_heading_without_losing_supported_body():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Path objects represent filesystem paths.",
                "supportingSources": [
                    {
                        "citationKey": "S1",
                        "title": "Historical pathlib article",
                        "tier": "secondary", "sourceRole": "secondary",
                        "authorityScore": 60,
                        "publishedAt": "2018-01-01",
                    }
                ],
                "evidenceExcerpt": "Path objects represent filesystem paths.",
            }
        ]
    }
    repaired, repair_count = research_module._architect_repair_source_role_labels(
        "## Current official guidance\n\nPath objects represent filesystem paths [S1].",
        task,
    )
    filtered, dropped = research_module._architect_drop_unsupported_section_units(repaired, task)

    assert repair_count == 1
    assert dropped == 1
    assert "Current official guidance" not in filtered
    assert "Secondary source" in filtered
    assert "Path objects represent filesystem paths [S1]." in filtered


def test_section_unit_filter_reports_at_most_three_bounded_drop_previews():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "The parser accepts pathlib.Path.",
                "supportingSources": [{"citationKey": "S1", "title": "Argparse docs"}],
                "evidenceExcerpt": "The parser accepts pathlib.Path as a converter.",
            }
        ]
    }
    diagnostics: list[str] = []
    _filtered, dropped = research_module._architect_drop_unsupported_section_units(
        "\n\n".join(
            f"UnsupportedType{index} is accepted by the parser [S1]." for index in range(5)
        ),
        task,
        diagnostics=diagnostics,
    )

    assert dropped == 5
    assert len(diagnostics) == 3
    assert all("\n" not in item and len(item) <= 180 for item in diagnostics)
    assert all(item.startswith("api_anchor_not_in_evidence:") for item in diagnostics)


def test_section_unit_filter_drops_markdown_blockquotes_even_when_cited():
    task = {
        "assignedClaims": [
            {
                "claimId": "C1",
                "claim": "Path objects represent filesystem paths.",
                "supportingSources": [{"citationKey": "S1"}],
                "evidenceExcerpt": "Path objects represent filesystem paths.",
            }
        ]
    }
    diagnostics: list[str] = []
    filtered, dropped = research_module._architect_drop_unsupported_section_units(
        "> Path objects represent filesystem paths. [S1]\n\nA concise paraphrase remains [S1].",
        task,
        diagnostics=diagnostics,
    )

    assert dropped == 1
    assert "> Path objects" not in filtered
    assert "concise paraphrase" in filtered
    assert diagnostics[0].startswith("blockquote_not_allowed:")


def test_repeated_content_unit_dedupe_removes_a_replayed_continuation_only_once():
    section = (
        "### Using pathlib with argparse\n\n"
        "The parser accepts pathlib.Path as a converter [S1].\n\n"
        "### Using pathlib with argparse\n\n"
        "The parser accepts pathlib.Path as a converter [S1].\n\n"
        "A distinct boundary remains visible [S2]."
    )

    deduped, count = research_module._architect_dedupe_repeated_content_units(section)

    assert count == 1
    assert deduped.count("The parser accepts pathlib.Path") == 1
    assert "A distinct boundary remains visible [S2]." in deduped


def test_evidence_excerpt_citation_tokens_normalize_to_source_citations():
    normalized = research_module._normalize_research_citation_tokens(
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


def test_research_source_excerpt_keeps_late_chinese_noun_fact_from_verbose_facet_query():
    body = (
        "页面导航和膳食指南背景介绍。" * 120
        + "\n\n在温和气候条件下，低身体活动水平成年男性每天饮水1700毫升，"
        "成年女性每天饮水1500毫升。\n\n"
        + "其他健康科普内容。" * 120
    )
    query = (
        "查证《中国居民膳食指南（2022）》对成年人（一般人群/一般成年人）每日饮水量的"
        "权威建议数值与适用人群范围，并定位可追溯的中文官方/学会来源。"
    )

    excerpt = research_module._research_source_excerpt(body, query, limit=2400)
    candidates = research_module._architect_evidence_candidates(
        {
            "citationKey": "S1",
            "title": "中国居民平衡膳食宝塔",
            "url": "https://example.gov.cn/dietary-guidelines",
            "text": excerpt,
            "evidenceQuery": query,
            "researchFacetId": "water-recommendation-2022",
        },
        query,
        limit=4,
    )

    assert "成年男性每天饮水1700毫升" in excerpt
    assert "成年女性每天饮水1500毫升" in excerpt
    assert any(
        "饮水" in candidate["text"]
        and ("1500" in candidate["text"] or "1700" in candidate["text"])
        for candidate in candidates
    )


def test_exact_excerpt_repair_keeps_chinese_daily_measurement_after_candidate_punctuation_normalization():
    query = (
        "查证《中国居民膳食指南（2022）》对成年人每日饮水量的权威建议数值与适用人群范围。"
    )
    source = {
        "sourceId": "dietary-guideline-water",
        "citationKey": "S4",
        "title": "中国居民平衡膳食宝塔",
        "url": "https://example.gov.cn/dietary-guidelines",
        "tier": "primary",
        "text": (
            "在温和气候条件下，低身体活动水平成年男性每天饮水1700毫升，"
            "成年女性每天饮水1500毫升。"
        ),
    }
    candidates = research_module._architect_multi_query_evidence_candidates(
        source,
        [query],
        facet_by_query={query: "water-intake"},
    )
    source["evidenceCandidates"] = candidates

    assert candidates
    assert not candidates[0]["text"].endswith("。")
    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        query,
        required_facet_id="water-intake",
    )

    assert claim is not None
    assert claim["claim"] == candidates[0]["text"] + "。"
    assert claim["claimType"] == "source_fact"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert verified[0]["supportingSources"][0]["researchFacetId"] == "water-intake"


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


def test_multi_query_evidence_candidates_reserve_each_long_document_facet():
    queries = [
        "Article 51 systemic risk threshold",
        "Article 53 open-source exception",
        "Article 88 enforcement authority",
        "Article 99 provider penalties",
    ]
    source = {
        "citationKey": "S10",
        "title": "Regulation (EU) 2024/1689",
        "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
        "text": "\n\n".join(
            [
                "Article 51 classifies general-purpose AI models with systemic risk using specified criteria.",
                "Article 53 establishes provider duties and an open-source exception with defined limits.",
                "Article 88 assigns enforcement authority for general-purpose AI model providers.",
                "Article 99 sets administrative fines for infringements by providers.",
            ]
        ),
    }
    facet_by_query = {
        query: f"facet-{index}" for index, query in enumerate(queries, start=1)
    }

    candidates = research_module._architect_multi_query_evidence_candidates(
        source,
        queries,
        facet_by_query=facet_by_query,
    )

    assert {item["researchFacetGoal"] for item in candidates} == set(queries)
    assert {item["researchFacetId"] for item in candidates} == set(facet_by_query.values())
    assert any("Article 99" in item["text"] for item in candidates)


def test_multi_query_evidence_candidates_reserve_chinese_article_facets():
    queries = [
        "第七条 训练数据 合法来源 个人信息",
        "第十一条 生成内容 标识",
        "第十五条 投诉 举报 用户权利",
        "第十七条 安全评估 算法备案",
    ]
    source = {
        "citationKey": "S11",
        "title": "生成式人工智能服务管理暂行办法",
        "url": "https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm",
        "text": "\n\n".join(
            [
                "首页 - 机构设置 - 机构职能 - 机关厅局 - 直属单位 - 政务服务 - 办事指南 - 表格下载 - 网站地图。",
                "第一条 为了促进生成式人工智能健康发展和规范应用，制定本办法。",
                "第二条 利用生成式人工智能技术向境内公众提供服务，适用本办法。",
                "第七条 生成式人工智能服务提供者应当依法开展预训练、优化训练等训练数据处理活动，涉及个人信息的，应当取得个人同意或者符合法律规定的其他情形。",
                "第十一条 提供者应当按照网络信息内容生态治理有关规定，依法承担网络信息内容生产者责任，并对生成内容进行标识。",
                "第十五条 提供者应当建立健全投诉、举报机制，设置便捷的投诉、举报入口，公布处理流程和反馈时限，及时处理公众投诉举报。",
                "第十七条 提供具有舆论属性或者社会动员能力的生成式人工智能服务，应当按照国家有关规定开展安全评估，并履行算法备案手续。",
            ]
        ),
    }
    facet_by_query = {
        query: f"facet-cn-{index}" for index, query in enumerate(queries, start=1)
    }

    candidates = research_module._architect_multi_query_evidence_candidates(
        source,
        queries,
        facet_by_query=facet_by_query,
    )

    assert {item["researchFacetGoal"] for item in candidates} == set(queries)
    assert {item["researchFacetId"] for item in candidates} == set(facet_by_query.values())
    assert any("第十五条" in item["text"] and "投诉" in item["text"] for item in candidates)
    assert any("第十七条" in item["text"] and "安全评估" in item["text"] for item in candidates)
    assert all("机构设置" not in item["text"] for item in candidates)


def test_multi_query_candidates_do_not_inherit_another_facet_or_projection_label():
    user_query = "用户权益 投诉举报 处理流程"
    security_query = "安全评估 算法备案 手续"
    source = {
        "citationKey": "S12",
        "title": "生成式人工智能服务管理暂行办法",
        "url": "https://example.gov.cn/generative-ai-rules",
        "evidenceQuery": "适用范围 境内公众",
        "researchFacetGoal": "训练数据 个人信息",
        "researchFacetId": "training-data",
        "text": "\n\n".join(
            [
                "[Facet evidence: scope] 首页 机构设置 政务服务 政策法规 互动交流 网站地图。",
                "[Facet evidence: training-data] 第七条 提供者应当依法开展训练数据处理活动。",
                "[Facet evidence: user-rights] 第十五条 提供者应当建立健全投诉举报机制，公布处理流程和反馈时限。",
                "[Facet evidence: security] 第十七条 提供相关服务应当开展安全评估，并履行算法备案手续。",
            ]
        ),
    }
    facets = {
        user_query: "user-rights",
        security_query: "security",
    }

    candidates = research_module._architect_multi_query_evidence_candidates(
        source,
        [user_query, security_query],
        facet_by_query=facets,
    )

    user_rows = [item for item in candidates if item["researchFacetId"] == "user-rights"]
    security_rows = [item for item in candidates if item["researchFacetId"] == "security"]
    assert any("第十五条" in item["text"] for item in user_rows)
    assert any("第十七条" in item["text"] for item in security_rows)
    assert all("[Facet evidence:" not in item["text"] for item in candidates)


def test_long_document_candidates_ignore_projection_labels_and_rank_operative_clause():
    title = "Regulation (EU) 2024/1689 of the European Parliament and of the Council"
    operative = (
        "In order to strengthen and harmonise administrative penalties for infringement "
        "of this Regulation, the upper limits for setting the administrative fines for "
        "certain specific infringements should be laid down."
    )
    source = {
        "citationKey": "S5",
        "title": title,
        "url": "https://eur-lex.example/regulation",
        "text": (
            "[Facet evidence: enforcement-penalties-fines]\n"
            f"# {title}\n"
            "National authorities cooperate under the Regulation.\n\n"
            f"{operative}\n\n"
            "Administrative procedures may also be governed by national law."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "administrative penalties and fines under the AI Act",
        limit=3,
    )

    assert candidates
    assert candidates[0]["text"] == operative
    assert all("Facet evidence" not in item["text"] for item in candidates)
    assert all(title not in item["text"] for item in candidates)


def test_long_document_candidates_demote_article_index_and_keep_full_exception_clause():
    exception_clause = (
        "The obligations set out in paragraph 1, points (a) and (b), shall not apply "
        "to providers of AI models that are released under a free and open-source "
        "licence that allows for the access, usage, modification, and distribution of "
        "the model, and whose parameters, including the weights, the information on "
        "the model architecture, and the information on model usage, are made publicly "
        "available."
    )
    source = {
        "sourceId": "article-53",
        "citationKey": "S5",
        "title": "Article 53: Obligations for providers of general-purpose AI models",
        "url": "https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-53",
        "tier": "primary",
        "text": (
            "Article 51: Classification. Article 52: Procedure. "
            "Article 53: Obligations. Article 54: Authorised representatives. "
            "Article 55: Systemic-risk obligations.\n\n"
            f"{exception_clause}\n\n"
            "This exception shall not apply to general-purpose AI models with systemic risks."
        ),
    }
    query = "EU AI Act Article 53 open-source GPAI exception obligations"

    candidates = research_module._architect_evidence_candidates(
        source,
        query,
        limit=4,
    )
    source_with_candidates = {**source, "evidenceCandidates": candidates}
    claim = research_module._architect_exact_excerpt_source_fact(
        source_with_candidates,
        query,
    )

    assert candidates
    assert "Article 51: Classification" not in candidates[0]["text"]
    assert any(exception_clause == item["text"] for item in candidates[:2])
    assert claim is not None
    assert claim["claim"] == exception_clause
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source_with_candidates],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 1


def test_exact_excerpt_source_fact_preserves_verified_normative_clause():
    excerpt = (
        "Providers must publish a sufficiently detailed summary about the content used "
        "for training the general-purpose AI model."
    )
    source = {
        "sourceId": "regulation",
        "citationKey": "S5",
        "title": "Regulation (EU) 2024/1689",
        "url": "https://eur-lex.example/regulation",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S5:E1",
                "text": excerpt,
                "relevanceScore": 100,
                "researchFacetId": "provider-obligations",
                "researchFacetGoal": "GPAI provider publication obligations",
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What obligations apply to GPAI providers?",
    )

    assert claim is not None
    assert claim["claimType"] == "explicit_normative"
    assert claim["normativeCue"] == "must"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 1


def test_exact_excerpt_source_fact_accepts_unpunctuated_chinese_timeline_fact():
    excerpt = "2027年8月2日，既有通用人工智能（GPAI）模型的合规要求开始适用"
    source = {
        "sourceId": "timeline-guide",
        "citationKey": "S9",
        "title": "欧盟人工智能法案合规时间线",
        "url": "https://example.cn/ai-act-timeline",
        "tier": "secondary",
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S9:E1",
                "text": excerpt,
                "relevanceScore": 100,
                "researchFacetId": "timeline-application-dates",
                "researchFacetGoal": "GPAI 2027 application date",
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "截至 2026 年，GPAI 的分阶段适用日期是什么？",
    )

    assert claim is not None
    assert claim["claim"] == f"{excerpt}。"
    assert claim["claimType"] == "source_fact"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 1


def test_exact_excerpt_source_fact_keeps_operative_legal_clause_before_enumeration():
    cap_clause = (
        "The Commission may impose on providers of general-purpose AI models fines not "
        "exceeding 3 % of their annual total worldwide turnover in the preceding financial "
        "year or EUR 15 000 000, whichever is higher, when the Commission finds that the "
        "provider intentionally or negligently:"
    )
    principle = (
        "Fines imposed in accordance with this Article shall be effective, proportionate "
        "and dissuasive."
    )
    source = {
        "sourceId": "article-101",
        "citationKey": "S3",
        "title": "Article 101: Fines for providers of general-purpose AI models",
        "url": "https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-101",
        "tier": "primary",
        "text": f"{cap_clause}\n\n{principle}",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S3:E1",
                "text": cap_clause,
                "relevanceScore": 100,
                "researchFacetId": "enforcement-fines",
                "researchFacetGoal": "GPAI provider fines and enforcement",
            },
            {
                "evidenceExcerptKey": "S3:E2",
                "text": principle,
                "relevanceScore": 80,
                "researchFacetId": "enforcement-fines",
                "researchFacetGoal": "GPAI provider fines and enforcement",
            },
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What fines and enforcement rules apply to GPAI providers?",
        required_facet_id="enforcement-fines",
    )

    assert claim is not None
    assert claim["claim"] == cap_clause[:-1] + "."
    assert claim["evidenceExcerptKey"] == "S3:E1"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 1


def test_exact_excerpt_source_fact_keeps_verbatim_install_command_for_install_facet():
    command = "npm install -g @google/gemini-cli"
    excerpt = (
        "Install Gemini CLI globally via npm (inside the environment) "
        f"{command}"
    )
    source = {
        "sourceId": "gemini-cli",
        "citationKey": "S9",
        "title": "google-gemini/gemini-cli",
        "url": "https://github.com/google-gemini/gemini-cli",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S9:E1",
                "text": excerpt,
                "researchFacetId": "gemini-cli-windows-install",
                "researchFacetGoal": "Gemini CLI installation prerequisites and commands",
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "How can Gemini CLI be installed on Windows?",
        required_facet_id="gemini-cli-windows-install",
    )

    assert claim is not None
    assert claim["claim"] == command
    assert claim["evidenceExcerptKey"] == "S9:E1"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert verified[0]["supportingSources"][0]["researchFacetId"] == (
        "gemini-cli-windows-install"
    )


def test_exact_excerpt_source_fact_ignores_navigation_and_vague_product_fragments():
    facet_id = "entity-github-copilot-cli-operations"
    source = {
        "sourceId": "copilot-cli",
        "citationKey": "S14",
        "title": "Getting started with GitHub Copilot CLI - GitHub Docs",
        "url": "https://docs.github.com/copilot/how-tos/copilot-cli/cli-getting-started",
        "tier": "primary",
        "text": "",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S14:E1",
                "text": "[Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-cli) 5.",
                "researchFacetId": facet_id,
                "researchFacetGoal": "GitHub Copilot CLI Windows installation runtime models tools",
            },
            {
                "evidenceExcerptKey": "S14:E2",
                "text": "Click Next on each screen to accept the defaults.",
                "researchFacetId": facet_id,
                "researchFacetGoal": "GitHub Copilot CLI Windows installation runtime models tools",
            },
            {
                "evidenceExcerptKey": "S14:E3",
                "text": (
                    "On Windows, GitHub Copilot CLI requires PowerShell v6 or higher "
                    "for installation."
                ),
                "researchFacetId": facet_id,
                "researchFacetGoal": "GitHub Copilot CLI Windows installation runtime models tools",
            },
        ],
    }
    source["text"] = "\n".join(
        candidate["text"] for candidate in source["evidenceCandidates"]
    )
    question = (
        "截至 2026 年 7 月，请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 "
        "GitHub Copilot CLI 的 Windows 安装、模型、MCP、价格、隐私与限制。"
    )

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        question,
        required_facet_id=facet_id,
    )

    assert claim is not None
    assert claim["evidenceExcerptKey"] == "S14:E3"
    assert claim["claim"].startswith("On Windows, GitHub Copilot CLI requires")


def test_exact_excerpt_source_fact_rejects_wrong_dimension_catalog_item():
    facet_id = "entity-gemini-cli-governance"
    catalog_item = (
        "antigravity-windows-notifier Source-timestamped oil, gas, refined-product, "
        "futures, and related energy data for Gemini CLI."
    )
    source = {
        "sourceId": "gemini-extensions",
        "citationKey": "S18",
        "title": "Browse Extensions | Gemini CLI",
        "url": "https://geminicli.com/extensions/",
        "tier": "primary",
        "text": catalog_item,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S18:E1",
                "text": catalog_item,
                "researchFacetId": facet_id,
                "researchFacetGoal": (
                    "Gemini CLI account subscription pricing privacy telemetry "
                    "data boundary limitations"
                ),
            }
        ],
    }
    question = (
        "截至 2026 年 7 月，请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 "
        "GitHub Copilot CLI 的 Windows 安装、模型、MCP、价格、隐私与限制。"
    )

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        question,
        required_facet_id=facet_id,
    )

    assert claim is None


def test_exact_excerpt_source_fact_rejects_wrong_product_facet_assignment():
    facet_id = "entity-openai-codex-cli-operations"
    source = {
        "sourceId": "generic-chatgpt-release-notes",
        "citationKey": "S19",
        "title": "ChatGPT release notes",
        "url": "https://help.openai.com/en/articles/chatgpt-release-notes",
        "tier": "primary",
        "text": "ChatGPT can install tools and run commands from a dedicated workspace.",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S19:E1",
                "text": "ChatGPT can install tools and run commands from a dedicated workspace.",
                "researchFacetId": facet_id,
                "researchFacetGoal": (
                    "OpenAI Codex CLI Windows installation runtime models tools MCP extensions"
                ),
            }
        ],
    }
    question = (
        "截至 2026 年 7 月，请比较 OpenAI Codex CLI、Claude Code、Gemini CLI 和 "
        "GitHub Copilot CLI 的 Windows 安装、模型、MCP、价格、隐私与限制。"
    )

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        question,
        required_facet_id=facet_id,
    )

    assert claim is None


def test_supplement_claim_balancing_repairs_duplicate_ids_and_drops_duplicate_facts():
    claims = [
        {
            "claimId": "C1",
            "claim": "First verified installation fact.",
            "supportingSources": [{"citationKey": "S1", "tier": "primary"}],
        },
        {
            "claimId": "C1",
            "claim": "Second verified installation fact.",
            "supportingSources": [{"citationKey": "S2", "tier": "primary"}],
        },
        {
            "claimId": "C3",
            "claim": "First verified installation fact.",
            "supportingSources": [{"citationKey": "S3", "tier": "primary"}],
        },
    ]

    balanced = research_module._architect_balance_base_claims_for_supplement(claims)

    assert [claim["claim"] for claim in balanced] == [
        "First verified installation fact.",
        "Second verified installation fact.",
    ]
    assert len({claim["claimId"] for claim in balanced}) == 2
    assert balanced[1]["claimId"].startswith("C1_")


def test_exact_excerpt_source_fact_can_target_one_multifacet_document_view():
    timeline = (
        "Providers of general-purpose AI models placed on the market before "
        "2 August 2025 shall comply by 2 August 2027."
    )
    enforcement = (
        "The request for information shall indicate the fines provided for in "
        "Article 101 for supplying incorrect, incomplete or misleading information."
    )
    source = {
        "sourceId": "eu-ai-act",
        "citationKey": "S2",
        "title": "Regulation (EU) 2024/1689",
        "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
        "tier": "primary",
        "text": f"{timeline}\n\n{enforcement}",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S2:E1",
                "text": timeline,
                "researchFacetId": "applicable-dates",
                "researchFacetGoal": "GPAI application dates and transition",
            },
            {
                "evidenceExcerptKey": "S2:E2",
                "text": enforcement,
                "researchFacetId": "enforcement-penalties",
                "researchFacetGoal": "GPAI enforcement powers and Article 101 fines",
            },
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the GPAI dates, obligations, enforcement powers and fines?",
        required_facet_id="enforcement-penalties",
    )

    assert claim is not None
    assert claim["claim"] == enforcement
    assert claim["evidenceExcerptKey"] == "S2:E2"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert verified[0]["supportingSources"][0]["researchFacetId"] == (
        "enforcement-penalties"
    )


def test_query_architect_facets_drive_gap_stats_and_refinement_for_fuzzy_question():
    question = "What must a GPAI provider schedule for 2026?"
    shards = [
        {
            "shardId": "open-source",
            "kind": "facet:open-source-exception",
            "query": "EU AI Act Article 53 open-source GPAI exception",
            "evidenceQuery": "EU AI Act Article 53 open-source GPAI exception",
            "results": [],
            "fetchedTopSources": [],
        },
        {
            "shardId": "fines",
            "kind": "facet:enforcement-fines",
            "query": "EU AI Act Article 99 GPAI provider penalties",
            "evidenceQuery": "EU AI Act Article 99 GPAI provider penalties",
            "results": [],
            "fetchedTopSources": [],
        },
    ]

    stats = research_module._selected_facet_evidence_stats(
        question,
        shards,
        source_policy="authoritative",
    )
    refined = research_module._build_refinement_shards(
        question,
        shards,
        source_policy="authoritative",
        limit=4,
        round_index=2,
    )

    assert stats["requiredFacetIds"] == ["open-source-exception", "enforcement-fines"]
    assert stats["missingFacetIds"] == ["open-source-exception", "enforcement-fines"]
    assert {item["researchFacetId"] for item in refined} == {
        "open-source-exception",
        "enforcement-fines",
    }


def test_claim_gate_excludes_search_strategy_but_keeps_substantive_facets():
    assert not research_module._research_facet_requires_atomic_claim(
        "legal-analysis-cross-check"
    )
    assert not research_module._research_facet_requires_atomic_claim(
        "source-authority-distinction"
    )
    assert not research_module._research_facet_requires_atomic_claim(
        "legal-analysis-comparison"
    )
    assert not research_module._research_facet_requires_atomic_claim(
        "gpai-legal-analysis"
    )
    assert research_module._research_facet_requires_atomic_claim(
        "open-source-exception"
    )
    assert research_module._research_facet_requires_atomic_claim(
        "enforcement-penalties"
    )


def test_hard_assertion_requires_structured_and_strong_qualifier_evidence():
    issues = research_module._architect_hard_assertion_issues(
        "Article 53 defines an open-source exception under Annex XIII in non-binding guidance.",
        "Article 53 defines an open-source exception for qualifying providers.",
        source_metadata={"title": "Article 53 obligations"},
    )

    assert "structured_anchor_not_in_evidence:annex xiii" in issues
    assert "non_binding_not_entailed" in issues
    assert "exception_not_entailed" not in issues


def test_recent_research_discovery_reuses_only_verified_urls_for_live_reread(monkeypatch):
    question = "Current GPAI obligations and enforcement timeline"
    planned = [
        {
            "kind": "facet:obligations",
            "researchFacetId": "obligations",
            "query": "GPAI Article 53 provider obligations",
            "sourceIntent": "official_primary",
        },
        {
            "kind": "facet:penalties",
            "researchFacetId": "penalties",
            "query": "GPAI Article 101 provider penalties",
            "sourceIntent": "official_primary",
        },
    ]
    monkeypatch.setattr(
        research_module,
        "list_evidence_bundles",
        lambda **_kwargs: [
            {
                "evidenceBundleId": "matching-bundle",
                "topicFingerprint": research_module._topic_fingerprint(question),
                "sourceMatrix": [
                    {
                        "url": "https://eur-lex.example/regulation",
                        "host": "eur-lex.example",
                        "title": "Article 53 provider obligations",
                        "authorityScore": 95,
                        "tier": "primary",
                        "authorityTier": "primary",
                        "contentChars": 50_000,
                        "originalContentChars": 150_000,
                        "selectedForEvidence": True,
                        "retrievedAt": "2026-07-31T08:00:00Z",
                        "readEvidence": {"verified": True},
                        "evidenceViews": [
                            {"evidenceQuery": "GPAI Article 53 provider obligations"}
                        ],
                    },
                    {
                        "url": "https://law.example/penalties",
                        "host": "law.example",
                        "title": "Article 101 provider penalties",
                        "authorityScore": 55,
                        "tier": "secondary",
                        "contentChars": 12_000,
                        "selectedForEvidence": True,
                        "retrievedAt": "2026-07-31T08:00:00Z",
                        "readEvidence": {"verified": True},
                        "evidenceViews": [
                            {"evidenceQuery": "GPAI Article 101 provider penalties"}
                        ],
                    },
                    {
                        "url": "https://unreadable.example/result",
                        "selectedForEvidence": True,
                        "readEvidence": {"verified": False},
                    },
                    {
                        "url": "mcp://context7/python/cpython",
                        "selectedForEvidence": True,
                        "readEvidence": {"verified": True},
                    },
                ],
            },
            {
                "evidenceBundleId": "unrelated-bundle",
                "topicFingerprint": "different-topic",
                "sourceMatrix": [
                    {
                        "url": "https://unrelated.example/source",
                        "selectedForEvidence": True,
                        "readEvidence": {"verified": True},
                    }
                ],
            },
        ],
    )

    seeds = research_module._recent_research_discovery_seed_shards(
        question,
        planned,
        scope="global",
    )

    assert {seed["seedUrl"] for seed in seeds} == {
        "https://eur-lex.example/regulation",
        "https://law.example/penalties",
    }
    assert {seed["researchFacetId"] for seed in seeds} == {"obligations", "penalties"}
    assert all(seed["kind"] == "seed_url" for seed in seeds)
    assert all(not seed["seedUrl"].startswith("mcp://") for seed in seeds)
    assert all(seed["discoveryReuse"]["contentReused"] is False for seed in seeds)
    assert all(seed["discoveryReuse"]["requiresLiveRead"] is True for seed in seeds)
    regulation_seeds = [
        seed
        for seed in seeds
        if seed["seedUrl"] == "https://eur-lex.example/regulation"
    ]
    assert {seed["researchFacetId"] for seed in regulation_seeds} == {
        "obligations",
        "penalties",
    }
    assert next(
        seed
        for seed in seeds
        if seed["seedUrl"] == "https://law.example/penalties"
    )["sourceIntent"] == "independent_secondary"


def test_recent_discovery_reserves_facet_match_before_generic_authority_ranking(
    monkeypatch,
):
    question = "Current GPAI timeline and Code of Practice"
    planned = [
        {
            "kind": "facet:timeline",
            "researchFacetId": "timeline",
            "query": "EU AI Act GPAI application timeline Article 113",
            "sourceIntent": "official_primary",
        },
        {
            "kind": "facet:code-of-practice",
            "researchFacetId": "code-of-practice",
            "query": "AI Office GPAI Code of Practice signatories obligations",
            "sourceIntent": "official_primary",
        },
    ]
    sources = [
        {
            "url": "https://eur-lex.example/regulation",
            "host": "eur-lex.example",
            "title": "Regulation Article 113 application timeline",
            "authorityScore": 100,
            "tier": "primary",
            "authorityTier": "primary",
            "contentChars": 500_000,
            "originalContentChars": 500_000,
            "selectedForEvidence": True,
            "readEvidence": {"verified": True},
            "evidenceViews": [
                {"evidenceQuery": "EU AI Act GPAI application timeline Article 113"}
            ],
        },
        {
            "url": "https://commission.example/general-ai-policy",
            "host": "commission.example",
            "title": "General artificial intelligence policy portal",
            "authorityScore": 99,
            "tier": "primary",
            "authorityTier": "primary",
            "contentChars": 400_000,
            "originalContentChars": 400_000,
            "selectedForEvidence": True,
            "readEvidence": {"verified": True},
        },
        {
            "url": "https://commission.example/gpai-code-of-practice",
            "host": "commission.example",
            "title": "GPAI Code of Practice signatories and obligations",
            "authorityScore": 95,
            "tier": "primary",
            "authorityTier": "primary",
            "contentChars": 4_000,
            "originalContentChars": 4_000,
            "selectedForEvidence": True,
            "readEvidence": {"verified": True},
            "evidenceViews": [
                {
                    "evidenceQuery": (
                        "AI Office GPAI Code of Practice signatories obligations"
                    )
                }
            ],
        },
    ]
    monkeypatch.setattr(
        research_module,
        "list_evidence_bundles",
        lambda **_kwargs: [
            {
                "evidenceBundleId": "matching-bundle",
                "topicFingerprint": research_module._topic_fingerprint(question),
                "sourceMatrix": sources,
            }
        ],
    )

    seeds = research_module._recent_research_discovery_seed_shards(
        question,
        planned,
        scope="global",
        source_limit=2,
    )

    assert any(
        seed["seedUrl"] == "https://commission.example/gpai-code-of-practice"
        and seed["researchFacetId"] == "code-of-practice"
        for seed in seeds
    )


def test_recent_discovery_does_not_cross_bind_product_sources_between_facets(
    monkeypatch,
):
    question = (
        "Compare Claude Code and GitHub Copilot CLI account pricing, privacy, "
        "telemetry, and limitations."
    )
    planned = [
        {
            "kind": "facet:entity-claude-code-governance",
            "researchFacetId": "entity-claude-code-governance",
            "query": "Claude Code account pricing privacy telemetry limitations",
            "sourceIntent": "official_primary",
        },
        {
            "kind": "facet:entity-github-copilot-cli-governance",
            "researchFacetId": "entity-github-copilot-cli-governance",
            "query": (
                "GitHub Copilot CLI account pricing privacy telemetry limitations"
            ),
            "sourceIntent": "official_primary",
        },
    ]
    sources = [
        {
            "url": "https://code.claude.com/docs/en/settings",
            "host": "code.claude.com",
            "title": "Claude Code settings",
            "snippet": "Claude Code account and telemetry settings",
            "authorityScore": 85,
            "tier": "primary",
            "authorityTier": "primary",
            "contentChars": 12_000,
            "originalContentChars": 12_000,
            "selectedForEvidence": True,
            "readEvidence": {"verified": True},
        },
        {
            "url": "https://github.com/features/copilot/plans",
            "host": "github.com",
            "title": "GitHub Copilot Plans & pricing",
            "snippet": "Copilot CLI plans, credits, models, and pricing",
            "authorityScore": 85,
            "tier": "primary",
            "authorityTier": "primary",
            "contentChars": 12_000,
            "originalContentChars": 12_000,
            "selectedForEvidence": True,
            "readEvidence": {"verified": True},
        },
    ]
    monkeypatch.setattr(
        research_module,
        "list_evidence_bundles",
        lambda **_kwargs: [
            {
                "evidenceBundleId": "matching-product-bundle",
                "topicFingerprint": research_module._topic_fingerprint(question),
                "sourceMatrix": sources,
            }
        ],
    )

    seeds = research_module._recent_research_discovery_seed_shards(
        question,
        planned,
        scope="global",
    )

    bound_pairs = {
        (seed["seedUrl"], seed.get("researchFacetId"))
        for seed in seeds
        if seed.get("researchFacetId")
    }
    assert (
        "https://code.claude.com/docs/en/settings",
        "entity-claude-code-governance",
    ) in bound_pairs
    assert (
        "https://github.com/features/copilot/plans",
        "entity-github-copilot-cli-governance",
    ) in bound_pairs
    assert (
        "https://github.com/features/copilot/plans",
        "entity-claude-code-governance",
    ) not in bound_pairs
    assert (
        "https://code.claude.com/docs/en/settings",
        "entity-github-copilot-cli-governance",
    ) not in bound_pairs


def test_architect_projects_precise_gaps_onto_same_run_long_primary_document():
    question = (
        "1. [obligations] Verify general-purpose AI (GPAI) provider obligations.\n"
        "2. [penalties] Verify GPAI provider penalties."
    )
    url = "https://eur-lex.example/regulation-2024-1689"
    body = (
        "Regulation (EU) 2024/1689 governs general-purpose AI (GPAI) providers. "
        "Article 53 defines provider obligations and Article 101 defines fines. "
    ) * 20
    completed = [
        {
            "shardId": "initial-obligations",
            "kind": "facet:obligations",
            "researchFacetId": "obligations",
            "query": "GPAI Article 53 provider obligations",
            "evidenceQuery": "GPAI Article 53 provider obligations",
            "sourceIntent": "official_primary",
            "results": [
                {
                    "title": "Regulation (EU) 2024/1689",
                    "url": url,
                    "snippet": "General-purpose AI provider obligations",
                    "sourceQualityHints": {
                        "authorityScore": 95,
                        "tier": "primary",
                        "authorityTier": "primary",
                    },
                }
            ],
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": url,
                    "finalUrl": url,
                    "title": "Regulation (EU) 2024/1689",
                    "text": body,
                    "originalContentChars": 180_000,
                    "retrievedAt": "2026-07-31T08:00:00Z",
                }
            ],
        }
    ]

    projections = research_module._architect_existing_document_projection_shards(
        question,
        [
            "Regulation EU 2024/1689 Article 55 provider obligations full text",
            "Regulation EU 2024/1689 Article 101 fines full text",
        ],
        completed,
        source_policy="authoritative",
        round_index=3,
    )

    assert len(projections) == 2
    assert {projection["seedUrl"] for projection in projections} == {url}
    assert all(projection["kind"] == "seed_url" for projection in projections)
    assert all(
        projection["existingDocumentProjection"]["networkReadRequired"] is False
        for projection in projections
    )
    assert {
        projection["evidenceQuery"] for projection in projections
    } == {
        "Regulation EU 2024/1689 Article 55 provider obligations full text",
        "Regulation EU 2024/1689 Article 101 fines full text",
    }


def test_architect_projects_a_precise_gap_onto_a_fully_captured_short_official_document():
    question = "1. [gemini-features] Verify Gemini CLI MCP extensions and tool support."
    query = "Gemini CLI MCP extensions tools official primary source 2026"
    url = "https://github.com/google-gemini/gemini-cli"
    body = (
        "Gemini CLI supports MCP servers, extensions, tool calls, and repository workflows. "
        * 120
    ).strip()
    completed = [
        {
            "shardId": "gemini-install",
            "kind": "facet:gemini-install",
            "researchFacetId": "gemini-install",
            "query": "Gemini CLI Windows installation",
            "evidenceQuery": "Gemini CLI Windows installation",
            "sourceIntent": "official_primary",
            "results": [
                {
                    "title": "Google Gemini CLI",
                    "url": url,
                    "snippet": "Official Gemini CLI repository",
                    "sourceQualityHints": {
                        "authorityScore": 75,
                        "tier": "secondary",
                        "authorityTier": "primary",
                    },
                }
            ],
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": url,
                    "finalUrl": url,
                    "title": "Google Gemini CLI",
                    "text": body,
                    "originalContentChars": len(body),
                    "retrievedAt": "2026-07-31T08:00:00Z",
                }
            ],
        }
    ]

    projections = research_module._architect_existing_document_projection_shards(
        question,
        [query],
        completed,
        source_policy="authoritative",
        round_index=3,
    )

    assert len(projections) == 1
    assert projections[0]["seedUrl"] == url
    assert projections[0]["researchFacetId"] == "gemini-features"
    assert projections[0]["existingDocumentProjection"]["networkReadRequired"] is False


def test_precise_claim_plan_gap_forces_primary_refinement_for_only_that_facet():
    shards = [
        {
            "shardId": "gemini-install",
            "kind": "facet:gemini-install",
            "researchFacetId": "gemini-install",
            "query": "Gemini CLI Windows installation",
        },
        {
            "shardId": "gemini-features",
            "kind": "facet:gemini-features",
            "researchFacetId": "gemini-features",
            "query": "Gemini CLI MCP extensions tools",
        },
    ]

    refined = research_module._build_refinement_shards(
        "Compare Gemini CLI installation and features.",
        shards,
        source_policy="authoritative",
        only_facet_ids={"gemini-features"},
        force_primary_facet_ids={"gemini-features"},
    )

    assert len(refined) == 1
    assert refined[0]["researchFacetId"] == "gemini-features"
    assert refined[0]["sourceIntent"] == "official_primary"
    assert "official primary source" in refined[0]["query"]
    assert research_module._architect_model_failure_without_evidence_gap(
        {
            "used": False,
            "mode": "full_synthesis",
            "fallbackReason": "architect_evidence_plan_unavailable",
            "missingFacetIds": ["gemini-features"],
        }
    ) is False
    assert research_module._architect_model_failure_without_evidence_gap(
        {
            "used": False,
            "mode": "full_synthesis",
            "fallbackReason": "architect_evidence_plan_unavailable",
            "missingFacetIds": ["gemini-features"],
            "structuralTargetMet": True,
            "structuralStats": {
                "structuralTargetMet": True,
                "missingFacetIds": [],
            },
        }
    ) is True


def test_deterministic_fallback_keeps_all_read_source_refs_after_claim_cap():
    retrieved_at = "2026-08-01T12:00:00Z"
    sources = []
    for index in range(1, 21):
        body = (
            f"Source {index} provides a readable current fact with an explicit condition. "
            * 12
        )
        sources.append(
            {
                "sourceId": f"source-{index}",
                "citationKey": f"S{index}",
                "title": f"Source {index}",
                "url": f"https://source-{index}.example/evidence",
                "host": f"source-{index}.example",
                "tier": "primary",
                "authorityScore": 90,
                "selectedForEvidence": True,
                "retrievedAt": retrieved_at,
                "text": body,
            }
        )

    pack = research_module._deterministic_web_research_architect_pack(
        question="Compare all current source conditions.",
        source_matrix=sources,
        shards=[],
        confidence="high",
        average_authority=90,
    )

    assert len(pack["sourceUrls"]) == 20
    assert len(pack["claimTable"]) == research_module._RESEARCH_ARCHITECT_PLAN_MAX_CLAIM_COUNT
    assert pack["sourceUrls"][-1]["citationKey"] == "S20"


def test_bundle_shards_preserve_preferred_and_distinct_same_document_views():
    question = "Current GPAI provider obligations, exceptions, and penalties"
    regulation_url = "https://eur-lex.example/regulation"
    guide_url = "https://commission.example/guide"

    def shard(shard_id, url, facet_id, query):
        text = (f"{query} provides directly relevant evidence. " * 12).strip()
        return {
            "shardId": shard_id,
            "kind": f"facet:{facet_id}",
            "researchFacetId": facet_id,
            "query": query,
            "evidenceQuery": query,
            "results": [{"title": shard_id, "url": url}],
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": url,
                    "finalUrl": url,
                    "title": shard_id,
                    "text": text,
                    "retrievedAt": "2026-07-31T08:00:00Z",
                }
            ],
        }

    shards = [
        shard("regulation-generic", regulation_url, "obligations", "GPAI provider obligations"),
        shard("regulation-article-101", regulation_url, "penalties", "Article 101 GPAI fines"),
        shard("regulation-article-53", regulation_url, "open-source", "Article 53 open-source exception"),
        shard("commission-guide", guide_url, "guidance", "AI Office GPAI guidance"),
    ]
    selected_sources = [
        {
            "url": regulation_url,
            "shardId": "regulation-article-101",
        },
        {
            "url": guide_url,
            "shardId": "commission-guide",
        },
    ]

    persisted = research_module._research_shards_for_bundle(
        question,
        shards,
        selected_sources,
    )
    persisted_ids = [item["shardId"] for item in persisted]

    assert persisted_ids[:2] == ["regulation-article-101", "commission-guide"]
    assert set(persisted_ids) == {
        "regulation-generic",
        "regulation-article-101",
        "regulation-article-53",
        "commission-guide",
    }


def test_architect_source_views_do_not_borrow_text_from_another_facet_shard():
    question = "Current GPAI provider penalties"
    url = "https://eur-lex.example/regulation"
    penalties_query = "Article 101 GPAI provider penalties"
    penalties_text = (
        "Article 101 sets administrative fines for infringements by providers of "
        "general-purpose AI models. "
    ) * 8
    source_matrix = [
        {
            "sourceId": "regulation",
            "citationKey": "S1",
            "title": "Regulation (EU) 2024/1689",
            "url": url,
            "host": "eur-lex.example",
            "tier": "primary",
            "authorityScore": 95,
            "selectedForEvidence": True,
            "researchFacetIds": ["obligations", "penalties"],
            "evidenceViews": [
                {
                    "shardId": "missing-obligations-shard",
                    "researchFacetId": "obligations",
                    "evidenceQuery": "Article 53 GPAI provider obligations",
                },
                {
                    "shardId": "penalties-shard",
                    "researchFacetId": "penalties",
                    "evidenceQuery": penalties_query,
                },
            ],
        }
    ]
    shards = [
        {
            "shardId": "penalties-shard",
            "kind": "facet:penalties",
            "researchFacetId": "penalties",
            "query": penalties_query,
            "evidenceQuery": penalties_query,
            "results": [{"title": "Regulation (EU) 2024/1689", "url": url}],
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": url,
                    "finalUrl": url,
                    "title": "Regulation (EU) 2024/1689",
                    "text": penalties_text,
                    "retrievedAt": "2026-07-31T08:00:00Z",
                }
            ],
        }
    ]

    sources = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    assert len(sources) == 1
    assert sources[0]["evidenceQueries"] == [penalties_query]
    assert sources[0]["researchFacetIds"] == ["penalties"]
    assert "Article 53" not in sources[0]["text"]


def test_research_loop_live_rereads_recent_discovery_after_search_shortfall(monkeypatch):
    calls: list[list[dict]] = []

    def fake_run_search_shards(shards, **_kwargs):
        calls.append([dict(shard) for shard in shards])
        return [dict(shard) for shard in shards]

    def fake_source_stats(_question, shards, **_kwargs):
        used_recent_seed = any(shard.get("kind") == "seed_url" for shard in shards)
        return {
            "selectedSourceCount": 8 if used_recent_seed else 0,
            "distinctHostCount": 5 if used_recent_seed else 0,
            "datedSourceCount": 5 if used_recent_seed else 0,
            "sourceUrls": [f"https://source-{index}.example" for index in range(8)] if used_recent_seed else [],
        }

    monkeypatch.setattr(research_module, "_run_search_shards", fake_run_search_shards)
    monkeypatch.setattr(research_module, "_selected_source_stats", fake_source_stats)
    monkeypatch.setattr(
        research_module,
        "_selected_facet_evidence_stats",
        lambda *_args, **_kwargs: {
            "required": False,
            "complete": True,
            "requiredFacetIds": [],
            "coveredFacetIds": [],
            "missingFacetIds": [],
            "sourceCountByFacet": {},
        },
    )
    monkeypatch.setattr(
        research_module,
        "_research_loop_report",
        lambda *_args, **_kwargs: {
            "outline": [],
            "coveredClaims": [],
            "uncoveredClaims": [],
            "rejectedSources": [],
            "researchLoopReport": {},
        },
    )

    _shards, state = research_module._run_research_loop(
        question="current policy question",
        initial_shards=[{"shardId": "search", "kind": "baseline", "query": "current policy"}],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        freshness="current",
        max_rounds=3,
        use_agent_browser_profile=False,
        tool_call_id="recent-discovery-live-reread-test",
        recent_discovery_seed_shards=[
            {
                "shardId": "recent-seed",
                "kind": "seed_url",
                "seedUrl": "https://official.example/current-policy",
                "query": "current policy",
                "discoveryReuse": {
                    "originBundleId": "prior-rejected-bundle",
                    "contentReused": False,
                },
            }
        ],
    )

    assert [batch[0]["kind"] for batch in calls] == ["baseline", "seed_url"]
    assert state["stopReason"] == "high_quality_evidence_target_met"
    assert state["discoveryReuse"]["contentReused"] is False
    assert state["discoveryReuse"]["liveReadRequired"] is True


def test_research_loop_continues_when_global_targets_hide_a_missing_facet(monkeypatch):
    question = (
        "1. [timeline] Verify the compliance timeline.\n"
        "2. [copyright] Verify the copyright obligation."
    )

    def fake_run_search_shards(shards, **_kwargs):
        return [dict(shard) for shard in shards]

    def fake_source_stats(_question, _shards, **_kwargs):
        return {
            "selectedSourceCount": 8,
            "distinctHostCount": 5,
            "datedSourceCount": 5,
            "sourceUrls": [f"https://example-{index}.com" for index in range(8)],
        }

    def fake_facet_stats(_question, shards, **_kwargs):
        complete = len(shards) >= 3
        return {
            "required": True,
            "complete": complete,
            "requiredFacetIds": ["timeline", "copyright"],
            "coveredFacetIds": ["timeline", "copyright"] if complete else ["timeline"],
            "missingFacetIds": [] if complete else ["copyright"],
            "sourceCountByFacet": {
                "timeline": 1,
                "copyright": 1 if complete else 0,
            },
        }

    monkeypatch.setattr(research_module, "_run_search_shards", fake_run_search_shards)
    monkeypatch.setattr(research_module, "_selected_source_stats", fake_source_stats)
    monkeypatch.setattr(research_module, "_selected_facet_evidence_stats", fake_facet_stats)
    monkeypatch.setattr(
        research_module,
        "_build_refinement_shards",
        lambda *_args, **_kwargs: [
            {
                "shardId": "copyright-repair",
                "kind": "facet:copyright",
                "researchFacetId": "copyright",
                "query": "copyright official source",
            }
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_research_loop_report",
        lambda *_args, **_kwargs: {
            "outline": [],
            "coveredClaims": [],
            "uncoveredClaims": [],
            "rejectedSources": [],
            "researchLoopReport": {},
        },
    )

    _shards, state = research_module._run_research_loop(
        question=question,
        initial_shards=[
            {"shardId": "timeline", "query": "timeline"},
            {"shardId": "copyright", "query": "copyright"},
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        freshness="current",
        max_rounds=3,
        use_agent_browser_profile=False,
        tool_call_id="facet-aware-loop-test",
    )

    assert len(state["rounds"]) == 2
    assert state["rounds"][0]["facetEvidence"]["missingFacetIds"] == ["copyright"]
    assert state["rounds"][1]["facetEvidence"]["complete"] is True
    assert state["stopReason"] == "high_quality_evidence_target_met"


def test_research_loop_stops_when_all_transports_are_exhausted_for_the_run(monkeypatch):
    calls = 0

    def fake_run_search_shards(shards, **_kwargs):
        nonlocal calls
        calls += 1
        return [
            {
                **dict(shard),
                "ok": False,
                "resultCount": 0,
                "providerAttemptMatrix": [
                    {
                        "provider": "brave",
                        "status": "skipped",
                        "failureClass": "credential_missing",
                    },
                    {
                        "provider": "google",
                        "status": "error",
                        "failureClass": "network_timeout",
                    },
                    {
                        "provider": "metaso",
                        "status": "skipped",
                        "failureClass": "needs_agent_browser_login",
                    },
                ],
            }
            for shard in shards
        ]

    monkeypatch.setattr(research_module, "_run_search_shards", fake_run_search_shards)
    monkeypatch.setattr(
        research_module,
        "_selected_source_stats",
        lambda *_args, **_kwargs: {
            "selectedSourceCount": 0,
            "distinctHostCount": 0,
            "datedSourceCount": 0,
            "sourceUrls": [],
        },
    )
    monkeypatch.setattr(
        research_module,
        "_selected_facet_evidence_stats",
        lambda *_args, **_kwargs: {
            "required": False,
            "complete": False,
            "requiredFacetIds": [],
            "coveredFacetIds": [],
            "missingFacetIds": [],
            "sourceCountByFacet": {},
        },
    )
    monkeypatch.setattr(
        research_module,
        "_research_loop_report",
        lambda *_args, **_kwargs: {
            "outline": [],
            "coveredClaims": [],
            "uncoveredClaims": [],
            "rejectedSources": [],
            "researchLoopReport": {},
        },
    )

    _shards, state = research_module._run_research_loop(
        question="strict network research",
        initial_shards=[
            {"shardId": "one", "query": "one"},
            {"shardId": "two", "query": "two"},
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        freshness="current",
        max_rounds=4,
        use_agent_browser_profile=False,
        tool_call_id="transport-circuit-test",
    )

    assert calls == 1
    assert state["stopReason"] == "source_transport_exhausted"
    assert state["transportSummary"]["exhaustedForRun"] is True
    assert state["nextQueries"] == []


def test_research_transport_treats_missing_packaged_fetcher_as_terminal() -> None:
    summary = research_module._research_transport_failure_summary(
        [
            {
                "providerAttemptMatrix": [
                    {
                        "provider": "metaso",
                        "status": "error",
                        "failureClass": "runtime_dependency_missing",
                    }
                ]
            }
        ]
    )

    assert summary["exhaustedForRun"] is True
    assert summary["providers"] == [
        {
            "provider": "metaso",
            "state": "unavailable_until_configuration_changes",
            "attemptCount": 1,
            "failureClasses": ["runtime_dependency_missing"],
        }
    ]


def test_research_loop_stops_when_reachable_results_are_transport_exhausted(monkeypatch):
    calls = 0

    def fake_run_search_shards(shards, **_kwargs):
        nonlocal calls
        calls += 1
        return [
            {
                **dict(shard),
                "ok": False,
                "resultCount": 0,
                "providerAttemptMatrix": [
                    {
                        "provider": "bing",
                        "status": "irrelevant",
                        "failureClass": "irrelevant_results",
                    }
                ],
            }
            for shard in shards
        ]

    monkeypatch.setattr(research_module, "_run_search_shards", fake_run_search_shards)
    monkeypatch.setattr(
        research_module,
        "_selected_source_stats",
        lambda *_args, **_kwargs: {
            "selectedSourceCount": 0,
            "distinctHostCount": 0,
            "datedSourceCount": 0,
            "sourceUrls": [],
        },
    )
    monkeypatch.setattr(
        research_module,
        "_selected_facet_evidence_stats",
        lambda *_args, **_kwargs: {
            "required": False,
            "complete": False,
            "requiredFacetIds": [],
            "coveredFacetIds": [],
            "missingFacetIds": [],
            "sourceCountByFacet": {},
        },
    )
    monkeypatch.setattr(
        research_module,
        "_build_refinement_shards",
        lambda *_args, **_kwargs: [
            {"shardId": "repair-one", "query": "repair one"},
            {"shardId": "repair-two", "query": "repair two"},
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_research_loop_report",
        lambda *_args, **_kwargs: {
            "outline": [],
            "coveredClaims": [],
            "uncoveredClaims": [],
            "rejectedSources": [],
            "researchLoopReport": {},
        },
    )

    _shards, state = research_module._run_research_loop(
        question="query refinement research",
        initial_shards=[
            {"shardId": "one", "query": "one"},
            {"shardId": "two", "query": "two"},
        ],
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        freshness="current",
        max_rounds=4,
        use_agent_browser_profile=False,
        tool_call_id="transport-relevance-test",
    )

    assert calls == 1
    assert len(state["rounds"]) == 1
    assert state["stopReason"] == "source_transport_exhausted"
    assert state["transportSummary"]["reachableButIrrelevant"] is True


def test_architect_structural_target_requires_each_explicit_facet(monkeypatch):
    question = (
        "1. [timeline] Verify the compliance timeline.\n"
        "2. [copyright] Verify the copyright obligation."
    )
    sources = [
        {
            "sourceId": f"source-{index}",
            "citationKey": f"S{index}",
            "url": f"https://host-{index % 5}.example.com/source-{index}",
            "host": f"host-{index % 5}.example.com",
            "selectedForEvidence": True,
            "text": "Substantive source evidence for the requested compliance question. " * 10,
            "evidenceCandidates": [
                {
                    "evidenceExcerptKey": f"S{index}:E1",
                    "text": "Substantive timeline evidence from this source.",
                    "relevanceScore": 50,
                    "researchFacetId": "timeline",
                }
            ],
        }
        for index in range(1, 9)
    ]
    monkeypatch.setattr(
        research_module,
        "_architect_source_subject_focused",
        lambda *_args, **_kwargs: True,
    )

    stats = research_module._research_architect_structural_stats(
        question,
        sources,
        freshness="current",
    )

    assert stats["selectedSourceCount"] == 8
    assert stats["distinctHostCount"] == 5
    assert stats["structuralTargetMet"] is False
    assert stats["coveredFacetIds"] == ["timeline"]
    assert stats["missingFacetIds"] == ["copyright"]


def test_evidence_candidates_preserve_spaced_attribute_access_inside_code_blocks():
    source = {
        "sourceId": "argparse-docs",
        "citationKey": "S1",
        "url": "https://docs.python.org/3/library/argparse.html",
        "text": (
            "Common built-in types can be used as converters:\n"
            "```\n"
            "import argparse import pathlib parser = argparse . ArgumentParser () "
            + "parser . add_argument ( 'value' , type = str ) " * 14
            + "parser . add_argument ( 'datapath' , type = pathlib . Path )\n"
            "```\n"
            "The parsed value is available to the command-line application."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "How should pathlib be used in a CLI?",
        limit=2,
    )

    assert "type = pathlib . Path" in candidates[0]["text"]
    assert candidates[0]["relevanceScore"] > 0
    assert not any(
        re.search(r"\b(?:argparse|parser|pathlib|p|q)\s*\.\s*$", candidate["text"])
        for candidate in candidates
    )


def test_exact_excerpt_source_fact_uses_descriptive_sentence_not_normative_copy():
    source = {
        "sourceId": "pathlib-article",
        "citationKey": "S7",
        "title": "Path compatibility examples",
        "url": "https://example.com/pathlib",
        "text": "Path objects work with this function. You should use pathlib everywhere.",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S7:E1",
                "text": "Path objects work with this function. You should use pathlib everywhere.",
                "relevanceScore": 50,
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "How should pathlib be used in a CLI?",
    )

    assert claim is not None
    assert claim["claim"] == "Path objects work with this function."
    assert claim["claimType"] == "source_fact"
    assert claim["evidenceExcerptKey"] == "S7:E1"


def test_evidence_candidates_keep_parameter_default_and_continuation_together():
    source = {
        "sourceId": "click-path-types",
        "citationKey": "S6",
        "title": "Parameter Types — Click Documentation",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "text": (
            "path_type (type | None) – Convert the incoming path value to this type. "
            "If None, keep Python's default, which is str. "
            "Useful to convert to pathlib.Path. "
            "Changed in version 8.0: Allow passing path_type=pathlib.Path."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "What are current pathlib best practices in CLI tools?",
        limit=4,
    )

    parameter_contract = next(
        candidate["text"]
        for candidate in candidates
        if "Convert the incoming path value" in candidate["text"]
    )
    assert "If None, keep Python's default, which is str." in parameter_contract
    assert "Useful to convert to pathlib.Path." in parameter_contract


def test_exact_excerpt_source_fact_preserves_click_default_and_none_behavior():
    source = {
        "sourceId": "click-path-types",
        "citationKey": "S6",
        "title": "Parameter Types — Click Documentation",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "tier": "primary",
        "text": (
            "class click.Path(_exists=False_, _path\\_type=None_, "
            "_executable=False_) The Path type returns the filename. "
            "path_type (type | None) – Convert the incoming path value to this "
            "type. If None, keep Python's default, which is str. Useful to "
            "convert to pathlib.Path."
        ),
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S6:E1",
                "text": (
                    "class click.Path(_exists=False_, _path\\_type=None_, "
                    "_executable=False_) The Path type returns the filename."
                ),
                "relevanceScore": 100,
            },
            {
                "evidenceExcerptKey": "S6:E2",
                "text": (
                    "path_type (type | None) – Convert the incoming path value to "
                    "this type. If None, keep Python's default, which is str. "
                    "Useful to convert to pathlib.Path."
                ),
                "relevanceScore": 50,
            },
        ],
    }

    signature = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are current pathlib best practices in CLI tools?",
    )
    assert signature is not None
    assert signature["claim"] == (
        "Click.Path's documented signature sets path_type=None."
    )

    behavior = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are current pathlib best practices in CLI tools?",
        excluded_facts={
            research_module._normalized_evidence_text(signature["claim"])
        },
    )
    assert behavior is not None
    assert "if None, it keeps Python's default, which is str" in behavior["claim"]

    verified, issues = research_module._verify_architect_claim_excerpts(
        [signature, behavior],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 2


def test_exact_excerpt_source_fact_skips_historical_future_promise_for_current_question():
    source = {
        "sourceId": "pep-519",
        "citationKey": "S11",
        "title": "PEP 519 - Adding a file system path protocol",
        "url": "https://peps.python.org/pep-0519/",
        "tier": "primary",
        "publishedAt": "2016-05-11",
        "temporalEvidence": {"publishedAt": "2016-05-11"},
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S11:E1",
                "text": (
                    "pathlib.Path will be updated to support PathLike and os.fspath. "
                    "PathLike objects represent filesystem paths."
                ),
                "relevanceScore": 100,
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
    )

    assert claim is not None
    assert "will be updated" not in claim["claim"]
    assert claim["claim"] == "PathLike objects represent filesystem paths."


def test_exact_excerpt_source_fact_preserves_argparse_callable_path_contract_from_code_example():
    source = {
        "sourceId": "argparse-docs",
        "citationKey": "S1",
        "title": "argparse documentation",
        "url": "https://docs.python.org/3/library/argparse.html",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S1:E1",
                "text": (
                    "The argument to type can be a callable that accepts a single string. "
                    "Common converters include parser.add_argument('datapath', type = pathlib . Path)."
                ),
                "relevanceScore": 100,
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
    )

    assert claim is not None
    assert claim["claim"] == (
        "argparse's documented type example passes pathlib.Path as the converter "
        "for a CLI path argument."
    )
    assert claim["evidenceExcerptKey"] == "S1:E1"


def test_exact_excerpt_source_fact_preserves_attributed_secondary_path_recommendation():
    source = {
        "sourceId": "python-morsels",
        "citationKey": "S9",
        "title": "Python's pathlib module - Python Morsels",
        "url": "https://www.pythonmorsels.com/pathlib-module/",
        "tier": "secondary", "sourceRole": "secondary",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S9:E1",
                "text": (
                    "Python's pathlib.Path objects represent a file path. In my humble opinion, "
                    "you should use Path objects anywhere you work with file paths."
                ),
                "relevanceScore": 100,
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
    )

    assert claim is not None
    assert claim["claimType"] == "explicit_normative"
    assert claim["claim"] == "You should use Path objects anywhere you work with file paths."
    assert claim["normativeCue"] == "should use Path objects anywhere you work with file paths"
    assert claim["evidenceExcerptKey"] == "S9:E1"


def test_exact_excerpt_source_fact_preserves_named_validation_from_parameter_table():
    source = {
        "sourceId": "typer-parameters",
        "citationKey": "S3",
        "title": "Parameters - Typer",
        "url": "https://typer.tiangolo.com/reference/parameters/",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S3:E1",
                "text": (
                    "False | file_okay | Determine whether or not a Path argument is allowed to refer to a file. "
                    "When this is set to False, passing a file produces a validation error."
                ),
                "relevanceScore": 25,
            }
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
    )

    assert claim is not None
    assert claim["claim"].startswith("file_okay:")
    assert "Path argument" in claim["claim"]
    assert claim["evidenceExcerptKey"] == "S3:E1"


def test_exact_excerpt_source_fact_recovers_typer_path_contract_from_docs_and_code():
    source = {
        "sourceId": "typer-path",
        "citationKey": "S3",
        "title": "Path - Typer",
        "url": "https://typer.tiangolo.com/tutorial/parameter-types/path/",
        "tier": "primary",
        "text": (
            "``` from pathlib import Path from typing import Annotated import typer "
            "def main(config: Annotated[Path, typer.Option(exists=True, "
            "file_okay=True, dir_okay=False, writable=False, readable=True, "
            "resolve_path=True)]): pass ```\n\n"
            "## Path validations You can perform several validations for Path CLI parameters: "
            "exists checks presence; file_okay controls files; dir_okay controls directories; "
            "writable performs a writable check; readable performs a readable check; "
            "resolve_path fully resolves the path before it is passed onwards."
        ),
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S3:E1",
                "text": (
                    "``` from pathlib import Path from typing import Annotated import typer "
                    "def main(config: Annotated[Path, typer.Option(exists=True, "
                    "file_okay=True, dir_okay=False, writable=False, readable=True, "
                    "resolve_path=True)]): pass ```"
                ),
                "relevanceScore": 100,
            },
            {
                "evidenceExcerptKey": "S3:E5",
                "text": (
                    "## Path validations You can perform several validations for Path CLI parameters: "
                    "exists checks presence; file_okay controls files; dir_okay controls directories; "
                    "writable performs a writable check; readable performs a readable check; "
                    "resolve_path fully resolves the path before it is passed onwards."
                ),
                "relevanceScore": 50,
            },
        ],
    }

    first = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
    )
    assert first is not None
    second = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
        excluded_facts={research_module._normalized_evidence_text(first["claim"])},
    )

    assert first["claim"] == (
        "Typer documents exists, file_okay, dir_okay, writable, readable, and "
        "resolve_path as validation controls for Path CLI parameters."
    )
    assert first["evidenceExcerptKey"] == "S3:E5"
    assert second is not None
    assert second["claim"] == (
        "Typer's example annotates the CLI parameter as Path and configures "
        "exists=True, file_okay=True, dir_okay=False, writable=False, "
        "readable=True, and resolve_path=True through typer.Option."
    )
    assert second["evidenceExcerptKey"] == "S3:E1"
    verified, issues = research_module._verify_architect_claim_excerpts(
        [first, second],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 2


def test_exact_excerpt_source_fact_prefers_clean_source_bound_path_facts():
    cases = [
        (
            {
                "sourceId": "python-os-pathlike",
                "citationKey": "S2",
                "title": "os — Miscellaneous operating system interfaces",
                "url": "https://docs.python.org/3/library/os.html#os.PathLike",
                "tier": "primary",
                "version": "3.14.6",
                "text": (
                    "Changed in version 3.6: Support added to accept objects "
                    "implementing the os.PathLike interface."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S2:E2",
                        "text": (
                            "Changed in version 3.6: Support added to accept objects "
                            "implementing the os.PathLike interface."
                        ),
                    },
                ],
            },
            [
                "Version 3.6 added support for accepting objects that implement "
                "the os.PathLike interface.",
            ],
        ),
        (
            {
                "sourceId": "python-pathlib",
                "citationKey": "S6",
                "title": "pathlib — Object-oriented filesystem paths",
                "url": "https://docs.python.org/3/library/pathlib.html",
                "tier": "primary",
                "version": "3.14.6",
                "text": (
                    "Path classes are divided between pure paths, which provide purely "
                    "computational operations without I/O, and concrete paths, which "
                    "inherit from pure paths but also provide I/O operations. If you’ve "
                    "never used this module before or just aren’t sure which class is "
                    "right for your task, Path is most likely what you need."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S6:E2",
                        "text": (
                            "Path classes are divided between pure paths, which provide "
                            "purely computational operations without I/O, and concrete "
                            "paths, which inherit from pure paths but also provide I/O "
                            "operations. If you’ve never used this module before or just "
                            "aren’t sure which class is right for your task, Path is most "
                            "likely what you need."
                        ),
                    }
                ],
            },
            [
                "Path classes are divided between pure paths, which provide computational "
                "operations without I/O, and concrete paths, which also provide I/O operations.",
                "Python's pathlib documentation says Path is most likely what a reader "
                "needs when unsure which path class fits the task.",
            ],
        ),
        (
            {
                "sourceId": "trey-hunner-pathlib",
                "citationKey": "S9",
                "title": "No really, pathlib is great",
                "url": "https://treyhunner.com/2019/01/no-really-pathlib-is-great/",
                "tier": "secondary",
                "text": (
                    "The / separators in pathlib.Path strings are automatically converted "
                    "to the correct path separator based on the operating system you’re on. "
                    "This is the same code using pathlib.Path: from pathlib import Path; "
                    "import sys; directory = Path(sys.argv[1]); ignore_path = directory / "
                    "'.gitignore'; if ignore_path.is_file(): print(ignore_path.read_text())."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S9:E2",
                        "text": (
                            "The / separators in pathlib.Path strings are automatically "
                            "converted to the correct path separator based on the operating "
                            "system you’re on."
                        ),
                    },
                    {
                        "evidenceExcerptKey": "S9:E6",
                        "text": (
                            "This is the same code using pathlib.Path: from pathlib import "
                            "Path; import sys; directory = Path(sys.argv[1]); ignore_path = "
                            "directory / '.gitignore'; if ignore_path.is_file(): "
                            "print(ignore_path.read_text())."
                        ),
                    },
                ],
            },
            [
                "The article states that / separators in pathlib.Path strings are "
                "automatically converted to the operating system's path separator.",
                "The article's CLI example constructs a Path from sys.argv[1], joins "
                "'.gitignore' with /, calls is_file(), and calls read_text().",
            ],
        ),
        (
            {
                "sourceId": "python-morsels-pathlib",
                "citationKey": "S10",
                "title": "Python's pathlib module - Python Morsels",
                "url": "https://www.pythonmorsels.com/pathlib-module/",
                "tier": "secondary",
                "text": (
                    "Using Path objects for these operations also results in code that is "
                    "self-descriptive and cross-platform compatible."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S10:E3",
                        "text": (
                            "Using Path objects for these operations also results in code "
                            "that is self-descriptive and cross-platform compatible."
                        ),
                    }
                ],
            },
            [
                "Python Morsels states that using Path objects for these operations "
                "produces self-descriptive, cross-platform-compatible code."
            ],
        ),
    ]

    question = "What are the current best practices for using Python pathlib in CLI tools?"
    for source, expected_facts in cases:
        excluded: set[str] = set()
        for expected_fact in expected_facts:
            raw_claim = research_module._architect_exact_excerpt_source_fact(
                source,
                question,
                excluded_facts=excluded,
            )
            assert raw_claim is not None
            assert raw_claim["claim"] == expected_fact
            excluded.add(research_module._normalized_evidence_text(expected_fact))
            verified, issues = research_module._verify_architect_claim_excerpts(
                [raw_claim],
                [source],
                require_evidence_key=True,
            )
            assert issues == []
            assert len(verified) == 1


def test_exact_excerpt_source_fact_rejects_incomplete_and_polluted_fragments():
    source = {
        "sourceId": "noisy-path-doc",
        "citationKey": "S4",
        "title": "Filesystem path protocol notes",
        "url": "https://example.test/path-notes",
        "tier": "primary",
        "text": (
            "An abstract base class for objects representing a file system path, e.g. "
            "## Built-in compatibility Path values are â€ automatically converted."
        ),
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S4:E1",
                "text": "An abstract base class for objects representing a file system path, e.g.",
            },
            {
                "evidenceExcerptKey": "S4:E2",
                "text": "## Built-in compatibility Path values are â€ automatically converted.",
            },
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are best practices for Python filesystem paths in CLI tools?",
    )

    assert claim is None


def test_exact_excerpt_source_fact_cleans_live_typer_and_secondary_evidence():
    cases = [
        (
            {
                "sourceId": "typer-parameters",
                "citationKey": "S3",
                "title": "Parameters - Typer",
                "url": "https://typer.tiangolo.com/reference/parameters/",
                "tier": "primary",
                "text": (
                    "Example from pathlib import Path def main(config: Annotated[Path, "
                    "typer.Argument(exists=True, file_okay=False)]): pass. When this is "
                    "set to False, the application will raise a validation error when a "
                    "path to a file is given. Example: typer.Argument(exists=True). When "
                    "set to True for a Path argument, additional validation is performed "
                    "to check that the file or directory exists. If not, the value will be invalid."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S3:E1",
                        "text": (
                            "Example from pathlib import Path def main(config: Annotated[Path, "
                            "typer.Argument(exists=True, file_okay=False)]): pass. When this "
                            "is set to False, the application will raise a validation error "
                            "when a path to a file is given."
                        ),
                    },
                    {
                        "evidenceExcerptKey": "S3:E3",
                        "text": (
                            "Example: typer.Argument(exists=True). When set to True for a Path "
                            "argument, additional validation is performed to check that the "
                            "file or directory exists. If not, the value will be invalid."
                        ),
                    },
                ],
            },
            [
                "In Typer's documented Path argument example, file_okay=False causes "
                "a validation error when a file path is supplied.",
                "In Typer's documented Path argument example, exists=True checks that "
                "the file or directory exists; a missing path is invalid.",
            ],
        ),
        (
            {
                "sourceId": "real-python-pathlib",
                "citationKey": "S2",
                "title": "Python's pathlib Module: Taming the File System - Real Python",
                "url": "https://realpython.com/python-pathlib/",
                "tier": "secondary",
                "text": (
                    "Python’s pathlib module helps streamline your work with file and "
                    "directory paths. Its flexible Path class paves the way for intuitive semantics."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S2:E6",
                        "text": (
                            "Python’s pathlib module helps streamline your work with file "
                            "and directory paths."
                        ),
                    },
                    {
                        "evidenceExcerptKey": "S2:E2",
                        "text": (
                            "Its flexible Path class paves the way for intuitive semantics."
                        ),
                    },
                ],
            },
            [
                "Python's pathlib module helps streamline work with file and directory paths.",
                "The Path class provides flexible, intuitive path semantics.",
            ],
        ),
        (
            {
                "sourceId": "trey-hunner-why-pathlib",
                "citationKey": "S8",
                "title": "Why you should be using pathlib",
                "url": "https://treyhunner.com/2018/12/why-you-should-be-using-pathlib/",
                "tier": "secondary",
                "text": (
                    "While you can pass Path objects (and path-like objects) to the higher-level "
                    "shutil functions for copying/deleting/moving files and directories, "
                    "there’s no equivalent to these functions on Path objects. The methods "
                    "in this Path namespace return Path objects, which allows for method chaining."
                ),
                "evidenceCandidates": [
                    {
                        "evidenceExcerptKey": "S8:E1",
                        "text": (
                            "While you can pass Path objects (and path-like objects) to the "
                            "higher-level shutil functions for copying/deleting/moving files "
                            "and directories, there’s no equivalent to these functions on Path objects."
                        ),
                    },
                    {
                        "evidenceExcerptKey": "S8:E4",
                        "text": (
                            "The methods in this Path namespace return Path objects, which "
                            "allows for method chaining."
                        ),
                    },
                ],
            },
            [
                "The article notes that higher-level shutil copy, delete, and move functions "
                "accept Path or path-like objects, while Path has no equivalent methods.",
                "The article states that methods in the Path namespace return Path objects, "
                "enabling method chaining.",
            ],
        ),
    ]

    question = "What are the current best practices for using Python pathlib in CLI tools?"
    for source, expected_facts in cases:
        excluded: set[str] = set()
        for expected_fact in expected_facts:
            raw_claim = research_module._architect_exact_excerpt_source_fact(
                source,
                question,
                excluded_facts=excluded,
            )
            assert raw_claim is not None
            assert raw_claim["claim"] == expected_fact
            excluded.add(research_module._normalized_evidence_text(expected_fact))
            verified, issues = research_module._verify_architect_claim_excerpts(
                [raw_claim],
                [source],
                require_evidence_key=True,
            )
            assert issues == []
            assert len(verified) == 1


def test_claim_verifier_makes_split_path_join_fact_self_contained():
    excerpt = (
        "If the first element is a Path object the next ones (after the / ) can be "
        "str . And it will create a new Path object from that."
    )
    source = {
        "sourceId": "typer-app-dir",
        "citationKey": "S5",
        "title": "CLI Application Directory - Typer",
        "url": "https://typer.tiangolo.com/tutorial/app-dir/",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S5:E4",
                "text": excerpt,
            }
        ],
    }
    raw_claim = {
        "claimId": "claim_s5_split_antecedent",
        "claim": "And it will create a new Path object from that.",
        "claimType": "source_fact",
        "supportingSources": ["S5"],
        "evidenceExcerptKey": "S5:E4",
        "confidence": "high",
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [raw_claim],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert verified[0]["claim"] == (
        "When the first element is a Path object, following str elements can be "
        "combined with / to create a new Path object."
    )


def test_exact_excerpt_source_fact_names_typer_app_dir_instead_of_using_it():
    excerpt = (
        "from pathlib import Path import typer APP_NAME = 'my-cli' "
        "app_dir = typer.get_app_dir(APP_NAME) config_path = Path(app_dir) / "
        "'config.json'. It will give you a directory for storing configurations "
        "appropriate for your CLI program for the current user in each operating system."
    )
    source = {
        "sourceId": "typer-app-dir",
        "citationKey": "S5",
        "title": "CLI Application Directory - Typer",
        "url": "https://typer.tiangolo.com/tutorial/app-dir/",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S5:E1",
                "text": excerpt,
            }
        ],
    }

    raw_claim = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
    )

    assert raw_claim is not None
    assert raw_claim["claim"] == (
        "Typer's get_app_dir() provides an operating-system-appropriate directory "
        "for storing per-user CLI configuration."
    )
    verified, issues = research_module._verify_architect_claim_excerpts(
        [raw_claim],
        [source],
        require_evidence_key=True,
    )
    assert issues == []
    assert len(verified) == 1
    duplicate = research_module._architect_exact_excerpt_source_fact(
        source,
        "What are the current best practices for using Python pathlib in CLI tools?",
        excluded_facts={
            research_module._normalized_evidence_text(raw_claim["claim"])
        },
    )
    assert duplicate is None


def test_claim_verifier_rejects_unresolved_anaphora_without_antecedent():
    excerpt = (
        "Otherwise __fspath__() is called and its value is returned as long as it "
        "is a str or bytes object."
    )
    source = {
        "sourceId": "python-os-pathlike",
        "citationKey": "S2",
        "title": "os — Miscellaneous operating system interfaces",
        "url": "https://docs.python.org/3/library/os.html#os.PathLike",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S2:E3",
                "text": excerpt,
            }
        ],
    }
    raw_claim = {
        "claimId": "claim_s2_unresolved_otherwise",
        "claim": excerpt,
        "claimType": "source_fact",
        "supportingSources": ["S2"],
        "evidenceExcerptKey": "S2:E3",
        "confidence": "high",
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [raw_claim],
        [source],
        require_evidence_key=True,
    )

    assert verified == []
    assert issues == ["claim_1_unresolved_anaphora"]


def test_exact_excerpt_source_fact_prioritizes_named_cli_path_contracts_over_neighbor_noise():
    source = {
        "sourceId": "click-path",
        "citationKey": "S7",
        "title": "Parameter Types - Click Documentation",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S7:E1",
                "text": (
                    "If clamp is enabled, a value outside the range is clamped to the boundary instead of failing. "
                    "The Path type returns the filename instead of an open file. "
                    "Various checks can be enabled to validate the type of file and permissions. "
                    "exists ( bool ) - The file or directory needs to exist for the value to be valid. "
                    "file_okay ( bool ) - Allow a file as a value. "
                    "dir_okay ( bool ) - Allow a directory as a value. "
                    "path_type ( type | None ) - Convert the incoming path value to this type. Useful to convert to pathlib.Path."
                ),
                "relevanceScore": 50,
            }
        ],
    }
    excluded: set[str] = set()
    claims: list[str] = []
    for _index in range(4):
        claim = research_module._architect_exact_excerpt_source_fact(
            source,
            "What are the current best practices for using Python pathlib in CLI tools?",
            excluded_facts=excluded,
        )
        assert claim is not None
        claims.append(claim["claim"])
        excluded.add(research_module._normalized_evidence_text(claim["claim"]))

    joined = " ".join(claims)
    assert "file_okay" in joined
    assert "dir_okay" in joined
    assert "path_type" in joined
    assert "clamp" not in joined


def test_exact_excerpt_source_fact_can_select_a_distinct_supplemental_fact():
    source = {
        "sourceId": "pathlib-docs",
        "citationKey": "S4",
        "title": "pathlib documentation",
        "url": "https://docs.python.org/3/library/pathlib.html",
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S4:E1",
                "text": (
                    "Path objects represent filesystem paths. "
                    "Pure path objects provide path-handling operations without filesystem access."
                ),
                "relevanceScore": 50,
            }
        ],
    }

    first = research_module._architect_exact_excerpt_source_fact(source, "How should pathlib be used?")
    assert first is not None
    second = research_module._architect_exact_excerpt_source_fact(
        source,
        "How should pathlib be used?",
        excluded_facts={research_module._normalized_evidence_text(first["claim"])},
    )

    assert second is not None
    assert second["claim"] != first["claim"]
    assert second["claimId"] != first["claimId"]


def test_repair_outline_redistributes_all_verified_claim_ids_once():
    claims = [{"claimId": f"C{index}"} for index in range(1, 9)]
    outline = [
        {"sectionId": "intro", "title": "Introduction", "claimIds": ["missing"]},
        {"sectionId": "details", "title": "Details", "claimIds": []},
    ]

    repaired = research_module._architect_normalized_repair_outline(outline, claims)

    assert len(repaired) == 4
    assert [claim_id for section in repaired for claim_id in section["claimIds"]] == [
        f"C{index}" for index in range(1, 9)
    ]
    assert repaired[0]["title"] == "Evidence-backed findings 1"
    assert repaired[1]["title"] == "Evidence-backed findings 2"


def test_repair_outline_preserves_heading_only_when_claim_membership_is_unchanged():
    claims = [{"claimId": f"C{index}"} for index in range(1, 9)]
    outline = [
        {
            "sectionId": "first",
            "title": "First verified topic",
            "objective": "Explain claims one and two.",
            "claimIds": ["C1", "C2"],
        },
        {
            "sectionId": "second",
            "title": "Second verified topic",
            "objective": "Explain claims three and four.",
            "claimIds": ["C3", "C4"],
        },
        {
            "sectionId": "third",
            "title": "Third verified topic",
            "objective": "Explain claims five and six.",
            "claimIds": ["C5", "C6"],
        },
        {
            "sectionId": "fourth",
            "title": "Fourth verified topic",
            "objective": "Explain claims seven and eight.",
            "claimIds": ["C7", "C8"],
        },
    ]

    repaired = research_module._architect_normalized_repair_outline(outline, claims)

    assert repaired[0]["title"] == "First verified topic"
    assert repaired[0]["objective"] == "Explain claims one and two."
    assert repaired[1]["title"] == "Second verified topic"


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




def test_exact_excerpt_fallback_accepts_atomic_markdown_list_facts_for_comparison_facets():
    question = (
        "Compare Claude Code, Gemini CLI, and GitHub Copilot CLI MCP, privacy, account, "
        "and limitation boundaries."
    )
    cases = (
        (
            "claude-code-mcp-workflow",
            "Plugins define MCP servers in .mcp.json at the plugin root",
        ),
        (
            "claude-code-privacy-limits",
            "Privacy settings can be changed at any time at claude.ai/settings/data-privacy-controls",
        ),
        (
            "gemini-cli-privacy-limits",
            "Your use of Gemini CLI is governed by the Google Cloud Platform Service Terms",
        ),
        (
            "copilot-cli-models-tools",
            "Copilot delegates to these agents automatically when appropriate and can run multiple agents in parallel.",
        ),
        (
            "entity-github-copilot-cli-privacy_limits",
            "GitHub does not use Copilot Business or Copilot Enterprise customer data to train AI models.",
        ),
    )

    for index, (facet_id, fact) in enumerate(cases, start=1):
        citation_key = f"S{index}"
        is_copilot_privacy = facet_id == "entity-github-copilot-cli-privacy_limits"
        source = {
            "sourceId": f"list-source-{index}",
            "citationKey": citation_key,
            "title": (
                "Managing GitHub Copilot policies as an individual subscriber"
                if is_copilot_privacy
                else f"Official comparison source {index}"
            ),
            "url": (
                "https://docs.github.com/en/copilot/how-tos/manage-your-account/manage-policies"
                if is_copilot_privacy
                else f"https://official-{index}.example/docs"
            ),
            "tier": "primary",
            "authorityScore": 90,
            "evidenceCandidates": [
                {
                    "evidenceExcerptKey": f"{citation_key}:E1",
                    "researchFacetId": facet_id,
                    "researchFacetGoal": f"{facet_id} official evidence",
                    "text": f"## Current behavior - {fact} - Additional documented boundary",
                    "relevanceScore": 80,
                }
            ],
        }

        claim = research_module._architect_exact_excerpt_source_fact(
            source,
            question,
            required_facet_id=facet_id,
        )

        assert claim is not None, facet_id
        assert fact in claim["claim"]
        assert claim["evidenceExcerptKey"] == f"{citation_key}:E1"

    copilot_limit = {
        "sourceId": "copilot-limit-source",
        "citationKey": "S5",
        "title": "About GitHub Copilot CLI",
        "url": "https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli",
        "tier": "primary",
        "authorityScore": 95,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S5:E1",
                "researchFacetId": "copilot-cli-privacy-limits",
                "researchFacetGoal": "GitHub Copilot CLI privacy and known limitations",
                "text": (
                    "### Known MCP server policy limitations Copilot CLI can't currently "
                    "support the following organization-level MCP server policies: - MCP "
                    "servers in Copilot, which controls whether MCP servers can be used at all."
                ),
                "relevanceScore": 80,
            }
        ],
    }

    limit_claim = research_module._architect_exact_excerpt_source_fact(
        copilot_limit,
        question,
        required_facet_id="copilot-cli-privacy-limits",
    )

    assert limit_claim is not None
    assert "can't currently support" in limit_claim["claim"]


def test_exact_excerpt_fallback_binds_codex_security_guidance_to_privacy_facet():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on Windows setup, models, tools, MCP, pricing, privacy, and limitations."
    )
    source = {
        "sourceId": "codex-security-source",
        "citationKey": "S19",
        "title": "Agent approvals & security | ChatGPT Learn",
        "url": "https://learn.chatgpt.com/docs/agent-approvals-security",
        "tier": "primary",
        "authorityScore": 95,
        "text": (
            "Codex CLI uses sandboxing, approvals, and network controls. "
            "Codex CLI security guidance documents telemetry and local data retention. "
        )
        * 8,
        "evidenceCandidates": [
            {
                "evidenceExcerptKey": "S19:E1",
                "researchFacetId": "entity-openai-codex-cli-privacy_limits",
                "researchFacetGoal": "OpenAI Codex CLI privacy data boundaries limitations",
                "text": (
                    "### Security and privacy guidance - Keep log_user_prompt = false "
                    "unless policy explicitly permits storing prompt contents. Prompts "
                    "can include source code and sensitive data. Route telemetry only "
                    "to collectors you control; apply retention limits and access "
                    "controls aligned with your compliance requirements."
                ),
                "relevanceScore": 80,
            },
            {
                "evidenceExcerptKey": "S19:E2",
                "researchFacetId": "entity-openai-codex-cli-privacy_limits",
                "researchFacetGoal": "OpenAI Codex CLI privacy data boundaries limitations",
                "text": (
                    "Codex uses an OS-enforced sandbox that limits what it can touch "
                    "and an approval policy that controls when it must stop and ask."
                ),
                "relevanceScore": 70,
            },
        ],
    }

    claim = research_module._architect_exact_excerpt_source_fact(
        source,
        question,
        required_facet_id="entity-openai-codex-cli-privacy_limits",
    )

    assert claim is not None
    assert claim["evidenceExcerptKey"] == "S19:E1"
    assert "sensitive data" in claim["claim"]


def test_reviewer_claim_ledger_keeps_locked_citation_binding_and_drops_plan_candidates():
    sources = [
        {
            "sourceId": "copilot-install",
            "citationKey": "S12",
            "title": "Installing GitHub Copilot CLI",
            "url": "https://docs.github.com/copilot/install",
            "tier": "primary",
            "retrievedAt": "2026-08-01T14:00:00Z",
            "evidenceCandidates": [
                {
                    "evidenceExcerptKey": "S12:E9",
                    "text": "Distracting planning excerpt about PowerShell prerequisites.",
                }
            ],
        },
        {
            "sourceId": "codex-security",
            "citationKey": "S22",
            "title": "Agent approvals and security",
            "url": "https://learn.chatgpt.com/docs/agent-approvals-security",
            "tier": "primary",
            "retrievedAt": "2026-08-01T14:01:00Z",
        },
    ]
    claims = [
        {
            "claimId": "claim-copilot-winget",
            "claim": "winget install GitHub.Copilot.Prerelease",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": "S12",
                    "researchFacetId": "entity-github-copilot-cli-setup",
                }
            ],
            "evidenceExcerptKey": "S12:E1",
            "evidenceExcerpt": "``` winget install GitHub.Copilot.Prerelease ```",
            "evidenceExcerptSha256": "a" * 64,
        },
        {
            "claimId": "claim-codex-privacy",
            "claim": "Prompts can include source code and sensitive data.",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "citationKey": "S22",
                    "researchFacetId": "entity-openai-codex-cli-privacy_limits",
                }
            ],
            "evidenceExcerptKey": "S22:E1",
            "evidenceExcerpt": "Prompts can include source code and sensitive data.",
            "evidenceExcerptSha256": "b" * 64,
        },
    ]

    ledger = research_module._architect_review_claim_ledger(claims, sources)

    assert [row["citationKey"] for row in ledger["citationIndex"]] == ["S12", "S22"]
    assert ledger["claims"][0]["citationKeys"] == ["S12"]
    assert ledger["claims"][0]["evidenceExcerptKey"] == "S12:E1"
    assert "winget install" in ledger["claims"][0]["exactEvidenceExcerpt"]
    serialized = json.dumps(ledger, ensure_ascii=False)
    assert "evidenceCandidates" not in serialized
    assert "Distracting planning excerpt" not in serialized
    assert {
        row["facetId"] for row in ledger["facetCoverage"]
    } == {
        "entity-github-copilot-cli-setup",
        "entity-openai-codex-cli-privacy_limits",
    }


def test_architect_prompt_views_do_not_cross_bind_broad_multi_product_query():
    question = (
        "Compare OpenAI Codex CLI, Claude Code, Gemini CLI, and GitHub Copilot CLI "
        "on models, tools, MCP, pricing, privacy, and limitations."
    )
    url = "https://github.com/features/copilot/plans"
    copilot_body = (
        "GitHub Copilot plans define subscription pricing and process GitHub Copilot "
        "account data under documented product policies. "
    ) * 40
    source_matrix = [
        {
            "sourceId": "copilot-plans",
            "citationKey": "S1",
            "title": "GitHub Copilot plans and pricing",
            "url": url,
            "host": "github.com",
            "tier": "primary",
            "authorityScore": 95,
            "selectedForEvidence": True,
            "evidenceViews": [
                {
                    "shardId": "copilot-pricing",
                    "researchFacetId": "copilot-cli-account-pricing",
                    "evidenceQuery": "GitHub Copilot CLI subscription plans and pricing",
                },
                {
                    "shardId": "broad-repair",
                    "researchFacetId": "gemini-cli-mcp-workflow",
                    "evidenceQuery": (
                        "Gemini CLI and GitHub Copilot CLI MCP extensions, pricing, privacy, "
                        "and limitations"
                    ),
                },
            ],
        }
    ]
    shards = [
        {
            "shardId": shard_id,
            "evidenceQuery": evidence_query,
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": url,
                    "finalUrl": url,
                    "title": "GitHub Copilot plans and pricing",
                    "text": copilot_body,
                    "retrievedAt": "2026-08-01T00:00:00Z",
                }
            ],
        }
        for shard_id, evidence_query in (
            ("copilot-pricing", "GitHub Copilot CLI subscription plans and pricing"),
            (
                "broad-repair",
                "Gemini CLI and GitHub Copilot CLI MCP extensions, pricing, privacy, and limitations",
            ),
        )
    ]

    sources = research_module._research_architect_sources_for_prompt(
        source_matrix,
        shards,
        question=question,
        freshness="current",
    )

    assert len(sources) == 1
    assert sources[0]["researchFacetIds"] == ["copilot-cli-account-pricing"]
    assert [view["researchFacetId"] for view in sources[0]["evidenceViews"]] == [
        "copilot-cli-account-pricing"
    ]
    assert all("Gemini CLI and GitHub" not in query for query in sources[0]["evidenceQueries"])


def test_claim_verifier_allows_qualified_api_from_source_identity_and_exact_symbol():
    excerpt = "Path constructs a concrete path for the current operating system."
    source = {
        "sourceId": "pathlib-docs",
        "citationKey": "S1",
        "title": "pathlib - Object-oriented filesystem paths",
        "url": "https://docs.python.org/3/library/pathlib.html",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "pathlib.Path constructs a concrete operating-system path.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert len(verified) == 1


def test_claim_verifier_allows_spaced_qualified_api_in_exact_excerpt():
    excerpt = "parser . add_argument ( 'datapath' , type = pathlib . Path )"
    source = {
        "sourceId": "argparse-docs",
        "citationKey": "S1",
        "title": "argparse documentation",
        "url": "https://docs.python.org/3/library/argparse.html",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "argparse can use pathlib.Path as an argument converter.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert len(verified) == 1


def test_claim_verifier_narrows_click_filename_claim_to_its_bound_excerpt():
    excerpt = (
        "The Path type is similar to the File type, but returns the filename "
        "instead of an open file. Various checks can be enabled."
    )
    source = {
        "sourceId": "click-path-api",
        "citationKey": "S8",
        "title": "Click Path API",
        "url": "https://click.palletsprojects.com/en/stable/api/#click.Path",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S8:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "Click's Path type returns the filename string by default.",
                "supportingSources": ["S8"],
                "evidenceExcerptKey": "S8:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert verified[0]["claim"] == (
        "Click's Path type returns the filename instead of an open file."
    )


def test_claim_verifier_preserves_click_signature_defaults_and_typer_option_scope():
    click_excerpt = (
        "click.Path(_exists=False_, _file\\_okay=True_, _dir\\_okay=True_, "
        "_writable=False_, _readable=True_, _resolve\\_path=False_, "
        "_allow\\_dash=False_, _path\\_type=None_, _executable=False_)"
    )
    typer_excerpt = (
        "from pathlib import Path\nfrom typing import Annotated\nimport typer\n"
        "def main(config: Annotated[Path, typer.Option(exists=True, "
        "file_okay=True, dir_okay=False, writable=False, readable=True, "
        "resolve_path=True)]): pass"
    )
    sources = [
        {
            "sourceId": "click-path-signature",
            "citationKey": "S8",
            "title": "Click Parameter Types",
            "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
            "text": click_excerpt,
            "evidenceCandidates": [{"evidenceExcerptKey": "S8:E1", "text": click_excerpt}],
        },
        {
            "sourceId": "typer-path-example",
            "citationKey": "S3",
            "title": "Path - Typer",
            "url": "https://typer.tiangolo.com/tutorial/parameter-types/path/",
            "text": typer_excerpt,
            "evidenceCandidates": [{"evidenceExcerptKey": "S3:E1", "text": typer_excerpt}],
        },
    ]

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": (
                    "Click's Path type has parameters: exists, file_okay, dir_okay, "
                    "writable, readable, resolve_path, allow_dash, path_type, and executable."
                ),
                "supportingSources": ["S8"],
                "evidenceExcerptKey": "S8:E1",
            },
            {
                "claim": (
                    "Typer allows pathlib.Path as a type annotation for CLI parameters "
                    "with validations like exists, file_okay, dir_okay, writable, "
                    "readable, and resolve_path."
                ),
                "supportingSources": ["S3"],
                "evidenceExcerptKey": "S3:E1",
            },
        ],
        sources,
        require_evidence_key=True,
    )

    assert issues == []
    assert "exists=False" in verified[0]["claim"]
    assert "path_type=None" in verified[0]["claim"]
    assert "through typer.Option" in verified[1]["claim"]
    assert "dir_okay=False" in verified[1]["claim"]

    validation_verified, validation_issues = (
        research_module._verify_architect_claim_excerpts(
            [
                {
                    "claim": (
                        "Click's Path type supports validations through parameters "
                        "such as exists, file_okay, and dir_okay."
                    ),
                    "supportingSources": ["S8"],
                    "evidenceExcerptKey": "S8:E1",
                }
            ],
            sources,
            require_evidence_key=True,
        )
    )

    assert validation_issues == []
    assert "exists=False" in validation_verified[0]["claim"]
    assert "path_type=None" in validation_verified[0]["claim"]


def test_claim_verifier_keeps_argparse_example_and_pep_proposal_source_near():
    argparse_excerpt = "parser.add_argument('path', type=pathlib.Path)"
    argparse_recommendation_excerpt = (
        "While argparse is the default recommended standard library module for "
        "implementing basic command line applications, authors with more exacting "
        "requirements for exactly how their command line applications behave may "
        "find it doesn't provide the necessary level of control."
    )
    pep_excerpt = (
        "The pathlib.PurePath and pathlib.Path constructors will be updated "
        "to accept PathLike objects."
    )
    sources = [
        {
            "sourceId": "argparse-example",
            "citationKey": "S1",
            "title": "argparse documentation",
            "url": "https://docs.python.org/3/library/argparse.html",
            "text": argparse_excerpt,
            "evidenceCandidates": [
                {"evidenceExcerptKey": "S1:E1", "text": argparse_excerpt}
            ],
        },
        {
            "sourceId": "pep-519",
            "citationKey": "S2",
            "title": "PEP 519",
            "url": "https://peps.python.org/pep-0519/",
            "text": pep_excerpt,
            "evidenceCandidates": [
                {"evidenceExcerptKey": "S2:E1", "text": pep_excerpt}
            ],
        },
        {
            "sourceId": "argparse-recommendation",
            "citationKey": "S3",
            "title": "argparse documentation",
            "url": "https://docs.python.org/3/library/argparse.html",
            "text": argparse_recommendation_excerpt,
            "evidenceCandidates": [
                {
                    "evidenceExcerptKey": "S3:E1",
                    "text": argparse_recommendation_excerpt,
                }
            ],
        },
    ]

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": (
                    "argparse supports pathlib.Path as a type converter via the "
                    "type parameter."
                ),
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            },
            {
                "claim": (
                    "PEP 519 updated pathlib.PurePath and pathlib.Path constructors "
                    "to accept PathLike objects."
                ),
                "supportingSources": ["S2"],
                "evidenceExcerptKey": "S2:E1",
            },
            {
                "claim": (
                    "argparse is the default recommended standard library module "
                    "for basic CLI applications."
                ),
                "claimType": "explicit_normative",
                "normativeCue": "default recommended standard library module",
                "supportingSources": ["S3"],
                "evidenceExcerptKey": "S3:E1",
            },
        ],
        sources,
        require_evidence_key=True,
    )

    assert issues == []
    assert [claim["claim"] for claim in verified] == [
        (
            "argparse's documented type example passes pathlib.Path as the "
            "converter for a CLI path argument."
        ),
        (
            "PEP 519 proposed updating pathlib.PurePath and pathlib.Path "
            "constructors to accept PathLike objects."
        ),
        (
            "argparse is the default recommended standard library module for "
            "implementing basic command-line applications; more exacting behavior "
            "requirements may call for a parser with finer-grained control."
        ),
    ]


def test_claim_verifier_marks_secondary_normative_advice_as_attributed_opinion():
    excerpt = "You should use Path objects anywhere you work with file paths."
    source = {
        "sourceId": "python-morsels-pathlib",
        "citationKey": "S1",
        "title": "Why you should be using pathlib",
        "url": "https://www.pythonmorsels.com/why-you-should-be-using-pathlib/",
        "tier": "secondary", "sourceRole": "secondary",
        "text": excerpt,
        "evidenceCandidates": [
            {"evidenceExcerptKey": "S1:E1", "text": excerpt}
        ],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": (
                    "Secondary source “Why you should be using pathlib” states: "
                    "You should use Path objects anywhere you work with file paths."
                ),
                "claimType": "explicit_normative",
                "normativeCue": "should use Path objects",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert verified[0]["sourceRole"] == "secondary"
    assert verified[0]["authorityBoundary"] == (
        "attributed_secondary_opinion_not_official_rule"
    )
    assert verified[0]["supportingSources"][0]["tier"] == "secondary"


def test_claim_verifier_describes_path_calls_without_expanding_example_semantics():
    excerpt = (
        "p = Path.cwd()\n"
        "if p.exists() and p.is_dir():\n"
        "    with p.open() as stream:\n"
        "        stream.read()"
    )
    source = {
        "sourceId": "pathlib-example",
        "citationKey": "S6",
        "title": "pathlib documentation",
        "url": "https://docs.python.org/3/library/pathlib.html",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S6:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": (
                    "pathlib.Path provides methods like exists(), is_dir(), and "
                    "open() for filesystem operations."
                ),
                "supportingSources": ["S6"],
                "evidenceExcerptKey": "S6:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert verified[0]["claim"] == (
        "The official pathlib example calls exists(), is_dir(), and open() on a Path object."
    )


def test_claim_verifier_allows_qualified_api_expressed_as_from_import():
    excerpt = "from os import chdir; chdir(Path('/tmp'))"
    source = {
        "sourceId": "pathlib-article",
        "citationKey": "S1",
        "title": "Path compatibility examples",
        "url": "https://example.com/pathlib",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "A Path value can be passed to os.chdir.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert len(verified) == 1


def test_claim_verifier_rejects_negative_capability_not_in_excerpt():
    excerpt = "IntRange restricts a value to an integer range and optionally clamps it."
    source = {
        "sourceId": "click-types",
        "citationKey": "S1",
        "title": "Click Parameter Types",
        "url": "https://click.palletsprojects.com/en/stable/parameter-types/",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "Click has no built-in Path type.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert verified == []
    assert "claim_1_negative_capability_not_entailed" in issues


def test_claim_verifier_rejects_unsupported_performance_effect_and_magnitude():
    excerpt = "Lazy imports defer importing a module until the imported name is first used."
    source = {
        "sourceId": "python-315",
        "citationKey": "S1",
        "title": "What is new in Python 3.15",
        "url": "https://docs.python.org/3.15/whatsnew/3.15.html",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "Python 3.15 lazy imports significantly improve startup performance.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert verified == []
    assert "claim_1_performance_effect_not_entailed" in issues
    assert "claim_1_magnitude_not_entailed" in issues


def test_claim_verifier_allows_explicit_quantified_performance_effect():
    excerpt = "Python 3.15 lazy imports reduce interpreter startup time by 10%."
    source = {
        "sourceId": "python-315",
        "citationKey": "S1",
        "title": "What is new in Python 3.15",
        "url": "https://docs.python.org/3.15/whatsnew/3.15.html",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "Python 3.15 lazy imports reduce startup time by 10%.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert len(verified) == 1


def test_architect_evidence_candidates_drop_navigation_and_download_promotions():
    source = {
        "citationKey": "S1",
        "text": (
            "### Navigation - index - next | previous | Parameter Types.\n\n"
            "Free Download: Click here to claim your pathlib cheat sheet.\n\n"
            "Click Path converts command-line path arguments and can require that the resulting path exists before callback execution."
        ),
    }

    candidates = research_module._architect_evidence_candidates(
        source,
        "How does Click validate pathlib-style CLI path arguments?",
        limit=6,
    )

    assert [candidate["text"] for candidate in candidates] == [
        "Click Path converts command-line path arguments and can require that the resulting path exists before callback execution."
    ]


def test_architect_json_parser_rejects_truncated_outer_object_and_accepts_wrapped_complete_json():
    truncated = (
        '{"reviewDecision":"accept","claimTable":['
        '{"claim":"nested object closes but the response does not"}'
    )
    wrapped = (
        "Provider preface\n"
        '{"reviewDecision":"accept","claimTable":[],"answerOutline":[]}\n'
        "Provider suffix"
    )

    assert research_module._extract_json_object(truncated) is None
    assert research_module._extract_json_object(wrapped) == {
        "reviewDecision": "accept",
        "claimTable": [],
        "answerOutline": [],
    }


def test_claim_excerpt_verifier_rejects_unattributed_normative_overclaim():
    source = {
        "sourceId": "src-pathlib",
        "citationKey": "S1",
        "url": "https://docs.python.org/3/library/pathlib.html",
        "text": "Path objects provide filesystem operations and implement the os.PathLike interface.",
    }

    rejected, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "pathlib.Path is the officially recommended way to represent every filesystem path.",
                "claimType": "explicit_normative",
                "supportingSources": ["S1"],
                "evidenceExcerpt": source["text"],
            }
        ],
        [source],
    )

    assert rejected == []
    assert issues == ["claim_1_normative_cue_not_in_evidence"]


def test_claim_excerpt_verifier_requires_known_key_and_does_not_inflate_support_coverage():
    excerpt = "The verified excerpt is present in both mirrored bodies but represents one exact evidence binding."
    sources = [
        {
            "sourceId": f"src-{index}",
            "citationKey": f"S{index}",
            "url": f"https://docs{index}.example/one",
            "text": excerpt,
            "evidenceCandidates": [{"evidenceExcerptKey": f"S{index}:E1", "text": excerpt}],
        }
        for index in (1, 2)
    ]

    missing, missing_issues = research_module._verify_architect_claim_excerpts(
        [{"claim": "A missing evidence key cannot pass.", "supportingSources": ["S1"], "evidenceExcerpt": excerpt}],
        sources,
        require_evidence_key=True,
    )
    unknown, unknown_issues = research_module._verify_architect_claim_excerpts(
        [{"claim": "An unknown evidence key cannot pass.", "supportingSources": ["S1"], "evidenceExcerptKey": "S1:E9"}],
        sources,
        require_evidence_key=True,
    )
    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "One exact excerpt proves only its keyed source binding.",
                "supportingSources": ["S1", "S2"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        sources,
        require_evidence_key=True,
    )

    assert missing == []
    assert missing_issues == ["claim_1_evidence_key_required"]
    assert unknown == []
    assert unknown_issues == ["claim_1_evidence_key_unknown"]
    assert issues == []
    assert [source["citationKey"] for source in verified[0]["supportingSources"]] == ["S1"]


def test_independent_review_schema_requires_complete_empty_accept_contract():
    incomplete = {
        "reviewDecision": "accept",
        "reviewReasons": [],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
    }
    accepted_with_gap = {
        **incomplete,
        "criticalMissingEvidence": ["A required premise is missing."],
        "recommendedNextQueries": [],
    }
    complete = {
        **incomplete,
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }

    assert research_module._independent_architect_review_schema_valid(incomplete) is False
    assert research_module._independent_architect_review_schema_valid(accepted_with_gap) is True
    assert research_module._independent_architect_review_accepts(accepted_with_gap) is False
    assert research_module._independent_architect_review_schema_valid(complete) is True
    assert research_module._independent_architect_review_accepts(complete) is True


def test_independent_review_schema_safely_adapts_explicit_rich_accept_assessments():
    rich_accept = {
        "reviewDecision": "accept",
        "reviewReasons": ["Every premise is individually supported."],
        "questionCoverage": {
            "asked": "Current pathlib CLI practices",
            "answeredAspects": ["CLI conversion", "downstream Path operations"],
            "unansweredAspects": [
                "A universal framework ranking is deliberately excluded and is not a gap."
            ],
        },
        "claimEntailment": [
            {"claimId": "C1", "decision": "supported"},
            {"claimId": "C2", "decision": "supported_as_history_only"},
            {"claimId": "C3", "entailed": True},
        ],
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }

    assert research_module._independent_architect_review_schema_valid(rich_accept) is True
    normalized = research_module._normalize_independent_architect_review(rich_accept)
    assert normalized["questionCoverage"] is True
    assert normalized["claimEntailment"] is True
    assert research_module._independent_architect_review_accepts(rich_accept) is True


def test_independent_review_schema_rich_assessments_fail_closed_on_negative_or_unknown_items():
    base = {
        "reviewDecision": "accept",
        "reviewReasons": [],
        "questionCoverage": {"coversQuestion": True, "note": "Complete."},
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    explicit_negative = {
        **base,
        "claimEntailment": [
            {"claimId": "C1", "decision": "supported"},
            {"claimId": "C2", "decision": "unsupported"},
        ],
    }
    unknown_shape = {
        **base,
        "claimEntailment": [{"claimId": "C1", "notes": "Looks plausible."}],
    }

    assert research_module._independent_architect_review_schema_valid(explicit_negative) is True
    assert research_module._normalize_independent_architect_review(explicit_negative)["claimEntailment"] is False
    assert research_module._independent_architect_review_accepts(explicit_negative) is False
    assert research_module._independent_architect_review_schema_valid(unknown_shape) is False
    assert research_module._independent_architect_review_accepts(unknown_shape) is False


def test_independent_review_accept_with_warnings_only_adapts_when_contract_is_strictly_positive():
    positive = {
        "reviewDecision": "accept_with_warnings",
        "reviewReasons": [
            "Every source role is correctly bounded and every synthesis is appropriately attributed."
        ],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    actual_warning = {
        **positive,
        "reviewReasons": ["The answer should tighten one unsupported synthesis."],
    }

    assert research_module._independent_architect_review_schema_valid(positive) is True
    normalized = research_module._normalize_independent_architect_review(positive)
    assert normalized["reviewDecision"] == "accept"
    assert normalized["providerReviewDecision"] == "accept_with_warnings"
    assert research_module._independent_architect_review_accepts(positive) is True
    assert research_module._independent_architect_review_schema_valid(actual_warning) is False
    assert research_module._independent_architect_review_accepts(actual_warning) is False


def test_independent_review_repairs_conflicting_decision_only_for_explicit_positive_contract():
    contradictory = {
        "reviewDecision": "reject",
        "reviewReasons": [
            "A minor overstatement is not a critical unsupported claim; the remaining checks pass."
        ],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }

    normalized = research_module._normalize_independent_architect_review(contradictory)

    assert normalized["reviewDecision"] == "accept"
    assert normalized["providerReviewDecision"] == "reject"
    assert normalized["providerReviewReasons"] == contradictory["reviewReasons"]
    assert normalized["reviewSchemaConsistencyRepair"] == (
        "positive_fields_override_conflicting_decision"
    )
    assert research_module._independent_architect_review_schema_valid(contradictory) is True
    assert research_module._independent_architect_review_accepts(contradictory) is True

    negative_boolean = {**contradictory, "claimEntailment": False}
    normalized_negative = research_module._normalize_independent_architect_review(
        negative_boolean
    )
    assert normalized_negative["reviewDecision"] == "reject"
    assert "reviewSchemaConsistencyRepair" not in normalized_negative
    assert research_module._independent_architect_review_accepts(negative_boolean) is False

    blocker = "A concrete source-backed claim remains unsupported."
    nonempty_blocker = {
        **contradictory,
        "unsupportedClaims": [blocker],
    }
    normalized_blocker = research_module._normalize_independent_architect_review(
        nonempty_blocker
    )
    assert normalized_blocker["reviewDecision"] == "reject"
    assert "reviewSchemaConsistencyRepair" not in normalized_blocker
    assert research_module._independent_architect_review_accepts(nonempty_blocker) is False

    readability_reject = {
        **contradictory,
        "reviewReasons": [
            "The answer contains truncated sentences and OCR fragments that make it unreadable."
        ],
    }
    normalized_readability = research_module._normalize_independent_architect_review(
        readability_reject
    )
    assert normalized_readability["reviewDecision"] == "reject"
    assert "reviewSchemaConsistencyRepair" not in normalized_readability
    assert research_module._independent_architect_review_accepts(readability_reject) is False

    extra_blocker_surface = {
        **contradictory,
        "citationIssues": ["The central conclusion lacks an inline citation."],
    }
    normalized_extra_blocker = research_module._normalize_independent_architect_review(
        extra_blocker_surface
    )
    assert normalized_extra_blocker["reviewDecision"] == "reject"
    assert "reviewSchemaConsistencyRepair" not in normalized_extra_blocker
    assert research_module._independent_architect_review_accepts(extra_blocker_surface) is False

    string_boolean = {**contradictory, "questionCoverage": "true"}
    assert research_module._independent_architect_review_schema_valid(string_boolean) is False
    assert research_module._independent_architect_review_accepts(string_boolean) is False

    missing_blocker_list = dict(contradictory)
    missing_blocker_list.pop("criticalMissingEvidence")
    assert research_module._independent_architect_review_schema_valid(missing_blocker_list) is False
    assert research_module._independent_architect_review_accepts(missing_blocker_list) is False

    empty_reasons = {**contradictory, "reviewReasons": []}
    assert research_module._independent_architect_review_accepts(empty_reasons) is False


def test_independent_review_repairs_run37_provider_payload_without_losing_diagnostics():
    reasons = [
        (
            "Answer claims Click's Path type 'can convert incoming path values to "
            "pathlib.Path via path_type parameter' (C2). The exact excerpt (S8:E3) "
            "says 'Convert the incoming path value to this type. If None, keep "
            "Python's default, which is str. Useful to convert to pathlib.Path.' "
            "This is a documented parameter description, not a guarantee that the "
            "conversion always succeeds or that pathlib.Path is a supported value. "
            "The claim is semantically supported as a documented capability, but the "
            "answer later uses this to recommend 'explicitly configure Click's "
            "Path/path_type' without noting that the default is None and the "
            "conversion is optional. This is a minor overstatement but not a "
            "critical unsupported claim."
        ),
        (
            "Answer states 'Click.Path's documented signature sets path_type=None' "
            "(claim_runtime_s8_cb8d2711). The exact excerpt (S8:E2) shows the "
            "signature with path_type=None. This is directly supported by the "
            "official API signature. No issue."
        ),
        (
            "Answer synthesizes 'Convert each CLI path once at the input boundary' "
            "as a cross-source engineering recommendation. Each premise is cited "
            "(S1 for argparse type=pathlib.Path, S8 for Click path_type, S3 for "
            "Typer Path typing). The synthesis is clearly labeled as 'Practical "
            "synthesis' and 'report's engineering recommendation'. This is "
            "acceptable as a conservative cross-source inference."
        ),
        (
            "Answer includes secondary source claims (S9, S10) with explicit "
            "attribution prefixes. These are properly bounded as attributed "
            "experience and not used to establish official rules. No issue."
        ),
    ]
    provider_payload = {
        "reviewDecision": "reject",
        "reviewReasons": reasons,
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }

    normalized = research_module._normalize_independent_architect_review(
        provider_payload
    )

    assert normalized["reviewDecision"] == "accept"
    assert normalized["providerReviewDecision"] == "reject"
    assert normalized["providerReviewReasons"] == reasons
    assert normalized["reviewSchemaConsistencyRepair"] == (
        "positive_fields_override_conflicting_decision"
    )
    assert research_module._independent_architect_review_accepts(provider_payload) is True


def test_repaired_review_cannot_bypass_an_independent_consensus_rejection():
    accepted = {
        "reviewDecision": "accept",
        "reviewReasons": ["All checked claims are directly supported. No issue."],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "reviewMode": "semantic",
        "reviewerModelId": "minimax-reviewer",
        "reviewedAt": "2026-07-30T00:00:00Z",
    }
    repaired = {
        **accepted,
        "reviewDecision": "reject",
        "reviewReasons": ["The cross-source synthesis is acceptable and properly bounded."],
        "reviewMode": "adversarial",
        "reviewerModelId": "no-think-reviewer",
        "reviewedAt": "2026-07-30T00:00:01Z",
    }
    consensus = {
        **accepted,
        "consensusAccepted": True,
        "consensusReviewCount": 2,
        "consensusReviewerModelIds": [
            accepted["reviewerModelId"],
            repaired["reviewerModelId"],
        ],
        "consensusReviews": [accepted, repaired],
    }

    assert research_module._independent_architect_review_consensus_accepts(consensus) is True

    rejected = {
        **accepted,
        "reviewDecision": "reject",
        "claimEntailment": False,
        "unsupportedClaims": ["A central claim lacks support."],
        "reviewReasons": ["A central claim lacks support."],
    }
    rejected_consensus = {
        **consensus,
        "consensusReviewerModelIds": [
            rejected["reviewerModelId"],
            repaired["reviewerModelId"],
        ],
        "consensusReviews": [rejected, repaired],
    }
    assert (
        research_module._independent_architect_review_consensus_accepts(
            rejected_consensus
        )
        is False
    )

    repaired_first = {
        **repaired,
        "reviewMode": "semantic",
        "reviewerModelId": "no-think-semantic-reviewer",
    }
    rejected_second = {
        **rejected,
        "reviewMode": "adversarial",
        "reviewerModelId": "rejecting-adversarial-reviewer",
        "reviewedAt": "2026-07-30T00:00:02Z",
    }
    reversed_consensus = {
        **repaired_first,
        "consensusAccepted": True,
        "consensusReviewCount": 2,
        "consensusReviewerModelIds": [
            repaired_first["reviewerModelId"],
            rejected_second["reviewerModelId"],
        ],
        "consensusReviews": [repaired_first, rejected_second],
    }
    assert (
        research_module._independent_architect_review_consensus_accepts(
            reversed_consensus
        )
        is False
    )

    repaired_only = {
        **repaired_first,
        "consensusAccepted": True,
        "consensusReviewCount": 1,
        "consensusReviewerModelIds": [repaired_first["reviewerModelId"]],
        "consensusReviews": [repaired_first],
    }
    assert (
        research_module._independent_architect_review_consensus_accepts(repaired_only)
        is False
    )


def test_reviewer_exact_official_wording_gap_is_reclassified_as_same_evidence_revision():
    review = {
        "reviewDecision": "reject",
        "reviewReasons": ["The synthesis needs clearer attribution."],
        "questionCoverage": False,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": ["One recommendation is not labeled as a synthesis."],
        "criticalMissingEvidence": [
            "Official Python documentation guidance on pathlib best practices for CLI tools.",
            "Typer official guidance explicitly framing pathlib as the recommended path type.",
        ],
        "recommendedNextQueries": [
            "site:docs.python.org pathlib best practices command line",
            "site:typer.tiangolo.com pathlib recommended CLI",
        ],
    }

    normalized = research_module._architect_reclassify_review_evidence_gaps(
        review,
        question="What are the current pathlib CLI best practices? cite official sources.",
    )

    assert normalized["reviewDecision"] == "reject"
    assert normalized["unsupportedClaims"] == review["unsupportedClaims"]
    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert len(normalized["reclassifiedSameEvidenceIssues"]) == 2


def test_reviewer_answer_organization_gaps_trigger_same_evidence_revision() -> None:
    delivery_gaps = [
        "Windows-specific installation facts are not systematically delivered in the answer.",
        "Actionable guidance segmented by individual developer and small team is missing from the answer body.",
        "The cross-product MCP comparison is not consolidated.",
        "Pricing tier comparison is absent from the report.",
    ]

    normalized = research_module._architect_reclassify_review_evidence_gaps(
        {
            "reviewDecision": "reject",
            "reviewReasons": ["The evidence is present but the answer is poorly organized."],
            "criticalMissingEvidence": delivery_gaps,
            "recommendedNextQueries": ["site:example.com unnecessary follow-up"],
        },
        question="Compare four CLI tools and give audience-specific recommendations.",
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == delivery_gaps

    actual_gap = "The verified evidence contains no Windows installation command for Product X."
    retained = research_module._architect_reclassify_review_evidence_gaps(
        {
            "reviewDecision": "retry",
            "criticalMissingEvidence": [actual_gap],
            "recommendedNextQueries": ["Product X official Windows install command"],
        },
        question="How do I install Product X on Windows?",
    )

    assert retained["criticalMissingEvidence"] == [actual_gap]
    assert retained["recommendedNextQueries"] == [
        "Product X official Windows install command"
    ]


def test_reviewer_requested_official_prescription_gap_is_reclassified_for_cited_synthesis():
    gaps = [
        (
            "An official Python documentation passage that explicitly prescribes pathlib "
            "usage patterns specifically for CLI tools"
        ),
        (
            "Current Typer tutorial or docs page date/version showing the Path-Annotated + "
            "typer.Option example is still the recommended 2026 pattern."
        ),
        (
            "Click changelog or current docs note establishing pathlib.Path as the preferred "
            "path_type today."
        ),
        (
            "Any source explicitly framing per-user config via get_app_dir + Path as the "
            "documented best practice."
        ),
    ]
    normalized = research_module._architect_reclassify_review_evidence_gaps(
        {
            "reviewDecision": "reject",
            "reviewReasons": [
                "Official sources do not contain explicit normative guidance tying pathlib use to CLI tool design"
            ],
            "criticalMissingEvidence": gaps,
            "recommendedNextQueries": ["Search docs.python.org for a prescriptive CLI page"],
        },
        question="What are current pathlib best practices for CLI tools? Cite official sources.",
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == gaps


def test_reviewer_normative_absence_variants_are_same_evidence_synthesis_issues():
    critical = [
        "No excerpt from official docs contains prescriptive language recommending pathlib.Path for CLI tooling.",
        "The argparse excerpt does not mention pathlib as a recommendation; no normative cue exists.",
        "PEP 519 and PEP 428 do not prescribe pathlib for CLI tools.",
        "The Typer and Click excerpts do not contain prescriptive best-practice language.",
    ]
    review = {
        "reviewDecision": "retry",
        "reviewReasons": ["Official facts are present but no page states the final synthesis verbatim."],
        "criticalMissingEvidence": critical,
        "recommendedNextQueries": ["No such primary source excerpt is present."],
    }

    normalized = research_module._architect_reclassify_review_evidence_gaps(
        review,
        question="What are the current best practices for pathlib in CLI tools? cite official sources.",
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == critical


def test_reviewer_cannot_demand_the_same_verified_cue_from_a_preferred_page():
    gap = (
        "{'facetId': 'f-click-path-type-pathlib', "
        "'neededClaimType': 'explicit_normative', "
        "'neededPhrase': 'Click documentation states path_type=pathlib.Path converts to pathlib.Path', "
        "'blockingReason': 'S8 lacks click.Path excerpts; S6 carries the cue but the host differs "
        "from the API-reference page the question implies.'}"
    )
    normalized = research_module._architect_reclassify_review_evidence_gaps(
        {
            "reviewDecision": "retry",
            "reviewReasons": ["A preferred API page does not repeat the same cue."],
            "criticalMissingEvidence": [gap],
            "recommendedNextQueries": [
                "click.Path path_type pathlib.Path site:click.palletsprojects.com"
            ],
        },
        question="What are the current best practices for using Python pathlib in CLI tools? cite official sources.",
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == [gap]


def test_reviewer_structured_normative_authority_gap_is_same_evidence_synthesis_issue():
    missing_premise = (
        "Standards-body or Python Steering Council guidance equating pathlib.Path with "
        "the canonical PathLike protocol for CLI tools."
    )
    review = {
        "reviewDecision": "reject",
        "reviewReasons": ["The synthesis asks for normative standing not needed by the question."],
        "criticalMissingEvidence": [
            {
                "missingPremise": missing_premise,
                "whyRequired": "Needed to elevate PathLike to a normative CLI contract.",
            }
        ],
        "recommendedNextQueries": [
            {
                "query": "site:peps.python.org pathlib CLI guidance",
                "purpose": "Find an explicit normative statement.",
            }
        ],
    }

    assert research_module._research_text_list(review["criticalMissingEvidence"]) == [
        missing_premise
    ]
    assert research_module._research_text_list(review["recommendedNextQueries"]) == [
        "site:peps.python.org pathlib CLI guidance"
    ]
    normalized = research_module._architect_reclassify_review_evidence_gaps(
        review,
        question="What are the current pathlib CLI best practices? cite official sources.",
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == [missing_premise]


def test_reviewer_reason_only_official_best_practice_gap_is_same_evidence_issue():
    reason = "Evidence is fragmented for a direct official best-practices claim."
    normalized = research_module._architect_reclassify_review_evidence_gaps(
        {
            "reviewDecision": "reject",
            "reviewReasons": [reason],
            "criticalMissingEvidence": [],
            "recommendedNextQueries": ["site:docs.python.org pathlib recommended CLI"],
        },
        question="What are the current pathlib CLI best practices? cite official sources.",
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == [reason]


def test_reviewer_keeps_official_normative_gap_when_question_explicitly_asks_for_it():
    review = {
        "reviewDecision": "retry",
        "reviewReasons": [],
        "questionCoverage": False,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [
            "Official Python documentation guidance explicitly recommending pathlib for CLI tools."
        ],
        "recommendedNextQueries": ["site:docs.python.org pathlib recommended CLI"],
    }

    normalized = research_module._architect_reclassify_review_evidence_gaps(
        review,
        question="What does the official Python documentation recommend for CLI path handling?",
    )

    assert normalized == review


def test_partial_plan_can_continue_when_all_research_facets_have_broad_evidence():
    source_count = research_module.TARGET_RESEARCH_SOURCE_COUNT
    claim_count = research_module.TARGET_RESEARCH_CLAIM_COUNT
    facet_ids = ["timeline", "threshold", "documentation"]
    prompt_sources = [
        {
            "citationKey": f"S{index + 1}",
            "researchFacetIds": [facet_ids[index % len(facet_ids)]],
            "evidenceCandidates": [{"key": f"E{index + 1}", "excerpt": "verified evidence"}],
        }
        for index in range(source_count)
    ]
    plan = {
        "reviewDecision": "retry",
        "claimTable": [
            {"claimId": f"claim_{index + 1}", "claim": f"Candidate fact {index + 1}"}
            for index in range(claim_count)
        ],
        "criticalMissingEvidence": ["One requested subpoint remains unresolved."],
    }

    assert research_module._architect_partial_plan_evidence_ready(
        plan,
        required_claim_sources=[f"S{index + 1}" for index in range(source_count)],
        required_facet_ids=facet_ids,
        prompt_sources=prompt_sources,
    )


def test_partial_plan_still_blocks_when_a_required_facet_has_no_evidence():
    source_count = research_module.TARGET_RESEARCH_SOURCE_COUNT
    claim_count = research_module.TARGET_RESEARCH_CLAIM_COUNT
    prompt_sources = [
        {
            "citationKey": f"S{index + 1}",
            "researchFacetIds": ["timeline"],
            "evidenceCandidates": [{"key": f"E{index + 1}", "excerpt": "verified evidence"}],
        }
        for index in range(source_count)
    ]

    assert not research_module._architect_partial_plan_evidence_ready(
        {
            "claimTable": [
                {"claimId": f"claim_{index + 1}", "claim": f"Candidate fact {index + 1}"}
                for index in range(claim_count)
            ]
        },
        required_claim_sources=[f"S{index + 1}" for index in range(source_count)],
        required_facet_ids=["timeline", "threshold"],
        prompt_sources=prompt_sources,
    )


@pytest.mark.parametrize(
    ("question", "gap"),
    [
        (
            "What are the current pathlib CLI best practices? Cite official sources.",
            "A current version pathlib best practice guide is still missing.",
        ),
        (
            "请综合官方资料说明当前 pathlib CLI 的最佳实践。",
            "仍缺少当前版本 pathlib 最佳实践指南。",
        ),
        (
            "请基于一手资料给出当前 pathlib CLI 的实践判断。",
            "仍缺少当前版本 pathlib 最佳实践指导。",
        ),
    ],
)
def test_reviewer_current_version_best_practice_guide_gap_is_same_evidence_revision(
    question,
    gap,
):
    review = {
        "reviewDecision": "retry",
        "reviewReasons": ["The answer should label its synthesis more clearly."],
        "questionCoverage": False,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [gap],
        "recommendedNextQueries": ["official current pathlib CLI best practice guide"],
    }

    normalized = research_module._architect_reclassify_review_evidence_gaps(
        review,
        question=question,
    )

    assert normalized["criticalMissingEvidence"] == []
    assert normalized["recommendedNextQueries"] == []
    assert normalized["reclassifiedSameEvidenceIssues"] == [gap]


def test_segment_tasks_cover_claims_once_and_enforce_scoped_inline_citations():
    claims = [
        {
            "claimId": f"claim-{index}",
            "claim": f"Verified atomic claim {index} includes a concrete condition and evidence boundary.",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 9)
    ]
    tasks = research_module._architect_segment_tasks(
        {
            "claimTable": claims,
            "answerOutline": [
                {
                    "sectionId": f"section-plan-{index}",
                    "title": title,
                    "objective": title,
                    "claimIds": [f"claim-{index * 2 - 1}", f"claim-{index * 2}"],
                }
                for index, title in enumerate(("结论", "机制", "比较", "风险与行动"), start=1)
            ],
        },
        section_count=4,
        target_min_chars=20,
        target_max_chars=400,
    )

    assigned_ids = [claim["claimId"] for task in tasks for claim in task["assignedClaims"]]
    assert assigned_ids == [f"claim-{index}" for index in range(1, 9)]
    assert [task["requiredCitationKeys"] for task in tasks] == [
        ["S1", "S2"],
        ["S3", "S4"],
        ["S5", "S6"],
        ["S7", "S8"],
    ]

    valid = "本节只陈述分配范围内的事实、条件和证据边界 [S1]，并说明相应限制与适用范围 [S2]。"
    assert research_module._architect_section_issues(valid, tasks[0], complete=True) == []

    combined, complete = research_module._research_answer_section_from_model_output(
        "本节分别陈述两个事实 [S1, S2]。\n<!-- research-section-complete:section_1 -->",
        section_id="section_1",
    )
    assert complete is True
    assert combined.endswith("[S1][S2]。")
    citation_only_task = {**tasks[0], "minimumAcceptableChars": 0}
    assert research_module._architect_section_issues(combined, citation_only_task, complete=complete) == []

    fullwidth, complete = research_module._research_answer_section_from_model_output(
        "本节分别陈述两个事实【S1】【S2】。\n<!-- research-section-complete:section_1 -->",
        section_id="section_1",
    )
    assert complete is True
    assert "[S1][S2]" in fullwidth
    invalid = "本节漏掉一个必要引用，并错误引用范围外资料 [S1] [S8]。https://invented.example"
    invalid_issues = research_module._architect_section_issues(invalid, tasks[0], complete=False)
    assert "section_incomplete" in invalid_issues
    assert "section_citation_missing:S2" in invalid_issues
    assert "section_citation_out_of_scope:S8" in invalid_issues
    assert "section_url_not_in_evidence" in invalid_issues


def test_single_claim_leaf_accepts_available_evidence_without_padding():
    claim = {
        "claimId": "C1",
        "claim": "A narrow dated obligation applies.",
        "evidenceExcerpt": "A narrow dated obligation applies.",
        "supportingSources": [{"citationKey": "S1"}],
    }

    tasks = research_module._architect_segment_tasks(
        {"claimTable": [claim]}, section_count=1,
        target_min_chars=700, target_max_chars=1400,
    )
    assert "minimumAcceptableChars" not in tasks[0]
    assert research_module._architect_section_issues(
        claim["claim"] + " [S1]", tasks[0], complete=True,
    ) == []


def test_deliverable_requirements_reach_only_the_final_writer_segment():
    question = (
        "Research every item below.\n"
        "1. [timeline] Verify the current application timeline with dated evidence.\n"
        "2. [penalties] Verify the current penalty regime and enforcement authority.\n"
        "Deliverable requirements:\n"
        "- h2-checklist: Produce an actionable H2 checklist using only the verified evidence."
    )
    deliverables = research_module._build_explicit_question_deliverables(question)
    facets = research_module._build_explicit_question_facets(question)
    claims = [
        {
            "claimId": f"C{index}",
            "claim": f"Verified evidence claim {index} with an explicit applicability boundary.",
            "supportingSources": [
                {
                    "citationKey": f"S{index}",
                    "researchFacetId": "timeline" if index <= 2 else "penalties",
                }
            ],
        }
        for index in range(1, 5)
    ]
    tasks = research_module._architect_segment_tasks(
        {
            "question": question,
            "claimTable": claims,
            "requiredFacets": [
                {"facetId": "timeline", "goal": facets[0][0]},
                {"facetId": "penalties", "goal": facets[1][0]},
            ],
            "requiredDeliverables": deliverables,
        },
        section_count=2,
        target_min_chars=800,
        target_max_chars=1400,
    )

    assert [item[1] for item in facets] == ["facet:timeline", "facet:penalties"]
    assert deliverables == [
        {
            "deliverableId": "h2-checklist",
            "goal": "Produce an actionable H2 checklist using only the verified evidence.",
        }
    ]
    assert tasks[0]["deliverableGoals"] == []
    assert tasks[-1]["deliverableGoals"] == deliverables


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


def test_max_claims_survive_verification_pack_and_segment_assignment():
    claim_cap = research_module._RESEARCH_ARCHITECT_MAX_CLAIM_COUNT
    assert claim_cap % 4 == 0
    section_count = claim_cap // 4
    sources = []
    raw_claims = []
    for index in range(1, claim_cap + 2):
        excerpt = (
            f"Filesystem path behavior {index} remains a distinct verified boundary "
            "for command line tooling."
        )
        source = {
            "sourceId": f"source-{index}",
            "citationKey": f"S{index}",
            "title": f"Source {index}",
            "url": f"https://docs.example.test/path-{index}",
            "tier": "primary",
            "authorityScore": 90,
            "text": excerpt,
            "evidenceCandidates": [
                {"evidenceExcerptKey": f"S{index}:E1", "text": excerpt}
            ],
        }
        sources.append(source)
        raw_claims.append(
            {
                "claimId": f"C{index}",
                "claim": excerpt,
                "claimType": "source_fact",
                "supportingSources": [f"S{index}"],
                "evidenceExcerptKey": f"S{index}:E1",
            }
        )

    verified, issues = research_module._verify_architect_claim_excerpts(
        raw_claims,
        sources,
        require_evidence_key=True,
    )
    assert issues == []
    assert [claim["claimId"] for claim in verified] == [
        f"C{index}" for index in range(1, claim_cap + 1)
    ]

    outline = [
        {
            "sectionId": f"section-{section_index}",
            "title": f"Section {section_index}",
            "objective": "Explain four verified claims.",
            "claimIds": [
                f"C{claim_index}"
                for claim_index in range(
                    (section_index - 1) * 4 + 1,
                    section_index * 4 + 1,
                )
            ],
        }
        for section_index in range(1, section_count + 1)
    ]
    tasks = research_module._architect_segment_tasks(
        {"claimTable": verified, "answerOutline": outline},
        section_count=section_count,
        target_min_chars=1600,
        target_max_chars=2600,
    )

    assigned_ids = [
        claim["claimId"]
        for task in tasks
        for claim in task["assignedClaims"]
    ]
    assert [len(task["assignedClaims"]) for task in tasks] == [4] * section_count
    assert assigned_ids == [f"C{index}" for index in range(1, claim_cap + 1)]
    assert len(assigned_ids) == len(set(assigned_ids))

    answer_pack = research_module._research_answer_pack(
        {
            "question": "How should CLI paths be handled?",
            "finalExperiencePack": {
                "answer": "Detailed verified answer. " * 300,
                "claimTable": verified,
                "sourceUrls": sources,
            },
            "claimTable": verified,
            "sourceMatrix": sources,
        }
    )
    assert [claim["claimId"] for claim in answer_pack["claimTable"]] == [
        f"C{index}" for index in range(1, claim_cap + 1)
    ]


def test_segment_tasks_preserve_a_single_claim_tail_section():
    claims = [
        {
            "claimId": f"claim-{index}",
            "claim": f"Verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 11)
    ]
    outline_counts = (3, 3, 3, 1)
    cursor = 0
    outline = []
    for index, group_size in enumerate(outline_counts, start=1):
        outline.append(
            {
                "sectionId": f"planned-{index}",
                "title": f"Planned section {index}",
                "objective": f"Explain planned evidence group {index}",
                "claimIds": [
                    f"claim-{claim_index}"
                    for claim_index in range(cursor + 1, cursor + group_size + 1)
                ],
            }
        )
        cursor += group_size

    tasks = research_module._architect_segment_tasks(
        {"claimTable": claims, "answerOutline": outline},
        section_count=4,
        target_min_chars=1600,
        target_max_chars=2600,
    )

    assert [len(task["assignedClaims"]) for task in tasks] == [3, 3, 3, 1]
    assert [
        claim["claimId"]
        for task in tasks
        for claim in task["assignedClaims"]
    ] == [f"claim-{index}" for index in range(1, 11)]
    assert tasks[-1]["requiredCitationKeys"] == ["S10"]


def test_segment_tasks_preserve_inference_groups_and_require_each_recommendation_section():
    claims = [
        {
            "claimId": f"C{index}",
            "claim": f"Verified decision fact {index}.",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 7)
    ]
    plan = {
        "question": "请比较这些工具，并按个人开发者和小团队给出选型建议。",
        "claimTable": claims,
        "answerOutline": [
            {"sectionId": "individual", "claimIds": ["C1", "C2"]},
            {"sectionId": "fact", "claimIds": ["C3"]},
            {"sectionId": "team", "claimIds": ["C4", "C5"]},
            {"sectionId": "limit", "claimIds": ["C6"]},
        ],
        "compositeInferences": [
            {
                "inferenceId": "I-individual",
                "inference": "面向个人开发者，按本地约束选择方案。",
                "premiseClaimIds": ["C1", "C2"],
            },
            {
                "inferenceId": "I-team",
                "inference": "面向小团队，按治理约束选择方案。",
                "premiseClaimIds": ["C4", "C5"],
            },
        ],
    }

    tasks = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1600,
        target_max_chars=2600,
    )

    assert [[claim["claimId"] for claim in task["assignedClaims"]] for task in tasks] == [
        ["C1", "C2"],
        ["C3"],
        ["C4", "C5"],
        ["C6"],
    ]
    assert [task["requiresSynthesisConclusion"] for task in tasks] == [
        True,
        False,
        True,
        False,
    ]


def test_segment_tasks_only_allow_code_that_entails_the_assigned_claim():
    unrelated_code = research_module._architect_segment_tasks(
        {
            "claimTable": [
                {
                    "claimId": "C1",
                    "claim": "pathlib.Path is the main concrete path class.",
                    "supportingSources": [{"citationKey": "S1"}],
                    "evidenceExcerpt": "```python\np = PureWindowsPath('c:/Windows')\n```",
                }
            ]
        },
        section_count=1,
        target_min_chars=20,
        target_max_chars=400,
    )
    entailing_code = research_module._architect_segment_tasks(
        {
            "claimTable": [
                {
                    "claimId": "C1",
                    "claim": "argparse can use pathlib.Path as its converter.",
                    "supportingSources": [{"citationKey": "S1"}],
                    "evidenceExcerpt": "```python\nparser = argparse.ArgumentParser()\nparser.add_argument('path', type=pathlib.Path)\n```",
                }
            ]
        },
        section_count=1,
        target_min_chars=20,
        target_max_chars=400,
    )

    assert unrelated_code[0]["verbatimCodeBlocks"] == []
    assert entailing_code[0]["verbatimCodeBlocks"] == [
        "parser = argparse.ArgumentParser()\nparser.add_argument('path', type=pathlib.Path)"
    ]


def test_section_rejects_citation_laundered_api_and_negative_capability():
    task = {
        "assignedClaims": [
            {
                "claimId": "claim-int-range",
                "claim": "IntRange restricts a value to a configured integer range.",
                "supportingSources": [{"citationKey": "S1", "title": "Click Parameter Types"}],
                "evidenceExcerpt": "IntRange restricts a value to an integer range and optionally clamps it.",
            }
        ],
        "requiredCitationKeys": ["S1"],
        "minimumAcceptableChars": 0,
        "targetMaxChars": 2_000,
    }
    section = (
        "Click has no built-in Path type [S1].\n\n"
        "```python\nclick.Path(path_type=Path)  # [S1]\n```"
    )

    issues = research_module._architect_section_issues(section, task, complete=True)

    assert "section_unsupported_hard_fact:negative_capability_not_entailed" in issues
    assert any("code_anchor_not_in_evidence:click.path(path_type=path)" in issue for issue in issues)


def test_hard_assertion_rejects_unverified_compound_duration():
    issues = research_module._architect_hard_assertion_issues(
        "The setting has a 30-day default cleanup period.",
        "The setting enables telemetry and exports OTLP metrics.",
    )

    assert "duration_anchor_not_in_evidence:30-day" in issues


def test_hard_assertion_rejects_product_specific_version_and_cross_context_associations():
    version_issues = research_module._architect_hard_assertion_issues(
        "Claude Code requires PowerShell v7+ on native Windows.",
        "Your prompt shows PS C:\\ when you are in PowerShell and C:\\ without the PS when you are in CMD.",
        source_metadata={"title": "Claude Code quickstart"},
    )
    association_issues = research_module._architect_hard_assertion_issues(
        "On Windows, the first run signs the user in to the service.",
        "Open a project directory and run the CLI. The first time you run it, choose a sign-in method.",
        source_metadata={"title": "CLI quickstart"},
    )

    assert "version_anchor_not_in_evidence:powershell v7+" in version_issues
    assert "cross_context_association_behavior_not_entailed" in association_issues


def test_hard_assertion_rejects_unverified_configuration_scope_and_shared_usage():
    scope_issues = research_module._architect_hard_assertion_issues(
        "The telemetry settings.json user/project/system scope controls the current session.",
        "telemetry has enabled, traces, and target fields; traces default to false.",
    )
    sharing_issues = research_module._architect_hard_assertion_issues(
        "Work and Codex share usage and credits.",
        "Business plans include access to ChatGPT and Codex and allow extending usage with ChatGPT credits.",
    )

    assert "configuration_scope_behavior_not_entailed" in scope_issues
    assert "sharing_behavior_not_entailed" in sharing_issues


def test_claim_verifier_records_undated_month_day_boundary():
    excerpt = "Starting on April 24, interactions may be used to train and improve models unless users opt out."
    source = {
        "sourceId": "copilot-plans",
        "citationKey": "S1",
        "title": "Copilot plans",
        "url": "https://example.com/copilot-plans",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "The source says interactions may be used for training starting on April 24.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert issues == []
    assert verified[0]["temporalBoundary"] == (
        "source_month_day_without_year; do_not_infer_current_year"
    )


def test_claim_verifier_rejects_year_inferred_from_undated_month_day():
    excerpt = "Starting on April 24, interactions may be used to train models unless users opt out."
    source = {
        "sourceId": "copilot-plans",
        "citationKey": "S1",
        "title": "Copilot plans",
        "url": "https://example.com/copilot-plans",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "Starting on 2026-04-24, interactions may be used for model training.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert verified == []
    assert "claim_1_source_month_day_year_inferred" in issues


def test_claim_verifier_rejects_truncated_evidence_fragment():
    excerpt = "direction; an agent’s message is still never treated as the user’s approval …18 tokens truncated"
    source = {
        "sourceId": "changelog",
        "citationKey": "S1",
        "title": "CLI changelog",
        "url": "https://example.com/changelog",
        "tier": "primary",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }

    verified, issues = research_module._verify_architect_claim_excerpts(
        [
            {
                "claim": "The changelog says an agent message is not user approval.",
                "supportingSources": ["S1"],
                "evidenceExcerptKey": "S1:E1",
            }
        ],
        [source],
        require_evidence_key=True,
    )

    assert verified == []
    assert "claim_1_evidence_excerpt_truncated" in issues


def test_section_rejects_partial_or_unregistered_code_blocks_and_api_absence_claims():
    allowed_code = "parser = argparse.ArgumentParser()\nparser.add_argument('path', type=pathlib.Path)"
    task = {
        "assignedClaims": [
            {
                "claimId": "claim-argparse-path",
                "claim": "argparse can use pathlib.Path as its converter.",
                "supportingSources": [{"citationKey": "S1", "title": "argparse documentation"}],
                "evidenceExcerpt": f"```python\n{allowed_code}\n```",
            }
        ],
        "requiredCitationKeys": ["S1"],
        "verbatimCodeBlocks": [allowed_code],
        "minimumAcceptableChars": 0,
        "targetMaxChars": 2_000,
    }

    partial = (
        "## argparse path input\n\n"
        "```python\nparser.add_argument('path', type=pathlib.Path)\n```\n\n"
        "Tree copying is not part of argparse's documented API [S1]."
    )
    exact = f"## argparse path input\n\n```python\n{allowed_code}\n```\n\n[S1]"

    partial_issues = research_module._architect_section_issues(partial, task, complete=True)
    exact_issues = research_module._architect_section_issues(exact, task, complete=True)
    cleaned_partial, dropped_blocks = research_module._architect_strip_unverified_section_code_blocks(
        partial,
        task,
    )
    cleaned_exact, exact_drops = research_module._architect_strip_unverified_section_code_blocks(
        exact,
        task,
    )

    assert "section_code_block_not_verbatim" in partial_issues
    assert "section_unsupported_hard_fact:negative_capability_not_entailed" in partial_issues
    assert "section_code_block_not_verbatim" not in exact_issues
    assert "section_code_block_incomplete" not in exact_issues
    assert dropped_blocks == 1
    assert "```" not in cleaned_partial
    assert "not part of argparse's documented API" in cleaned_partial
    assert exact_drops == 0
    assert allowed_code in cleaned_exact
    assert "[S1]" in cleaned_exact


def test_section_allows_hard_facts_present_in_exact_excerpt():
    excerpt = "Click provides click.Path, and path_type can convert the result to pathlib.Path."
    task = {
        "assignedClaims": [
            {
                "claimId": "claim-click-path",
                "claim": "click.Path accepts path_type for result conversion.",
                "supportingSources": [{"citationKey": "S1", "title": "Click Parameter Types"}],
                "evidenceExcerpt": excerpt,
            }
        ],
        "requiredCitationKeys": ["S1"],
        "minimumAcceptableChars": 0,
        "targetMaxChars": 2_000,
    }
    section = "Use `click.Path` with `path_type` for the documented conversion [S1]."

    assert research_module._architect_section_issues(section, task, complete=True) == []


def test_segmented_source_appendix_is_runtime_owned_and_stably_ordered():
    answer = research_module._assemble_architect_sections(
        question="当前应该如何采用这项能力？",
        verified_plan={"headline": "采用判断", "asOf": "2026-07-29T00:00:00Z"},
        sections=["## 第一部分\n证据结论 [S1]。", "## 第二部分\n风险判断 [S2] [S3]。"],
        sources=[
            {"citationKey": "S3", "title": "Third", "url": "https://three.example", "version": "3.0"},
            {"citationKey": "S1", "title": "First", "url": "https://one.example", "publishedAt": "2026-07-01"},
            {"citationKey": "S2", "title": "Second", "url": "https://two.example", "updatedAt": "2026-07-02"},
        ],
    )

    appendix = answer.split("## 来源", 1)[1]
    assert appendix.index("[S1] First") < appendix.index("[S2] Second") < appendix.index("[S3] Third")
    assert appendix.count("https://one.example") == 1
    assert "2026-07-29T00:00:00Z" in answer




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


def test_architect_candidates_keep_dedicated_binding_without_summary_fallback(monkeypatch):
    from core.llm_factory import llm_factory

    created: list[str] = []

    class DummyLLM:
        def __init__(self, model_ref: str):
            self._meta = {"model_ref": model_ref}

    role_models = {
        "research": "provider-z::unregistered-ghost-model",
        "subagent": "provider-d::subagent-model",
        "summary": "provider-b::summary-model",
        "supervisor": "provider-c::supervisor-model",
    }
    monkeypatch.delenv("V8_RESEARCH_ARCHITECT_MODEL_FALLBACKS", raising=False)
    monkeypatch.setattr(
        research_module.storage,
        "get_agent_model_binding",
        lambda _agent_id: "provider-a::shared-model",
    )
    monkeypatch.setattr(
        research_module.storage,
        "get_role_model_id",
        lambda role: role_models.get(role, ""),
    )
    monkeypatch.setattr(
        llm_factory,
        "create_chat_model",
        lambda model_ref, **_kwargs: created.append(model_ref) or DummyLLM(model_ref),
    )
    monkeypatch.setattr(
        llm_factory,
        "create_for_role",
        lambda role, **_kwargs: created.append(role_models[role]) or DummyLLM(role_models[role]),
    )

    candidates = research_module._create_web_research_architect_llm_candidates()

    assert [(model_id, role) for _llm, model_id, role in candidates] == [
        ("provider-a::shared-model", "web-research-architect"),
    ]
    assert [
        research_module._architect_candidate_selection_origin(candidate)
        for candidate in candidates
    ] == ["agent_binding"]
    assert research_module._ordered_architect_candidates(candidates) == candidates
    assert created == ["provider-a::shared-model"]


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


def test_research_answer_completion_marker_is_required_and_removed():
    answer, complete = research_module._research_answer_from_model_output("Detailed answer without a marker")
    assert answer == "Detailed answer without a marker"
    assert complete is False

    answer, complete = research_module._research_answer_from_model_output(
        "Detailed answer with a marker\n\n" + research_module._RESEARCH_ARCHITECT_ANSWER_COMPLETE_MARKER
    )
    assert answer == "Detailed answer with a marker"
    assert complete is True


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


def test_shopping_source_gate_does_not_select_empty_or_noisy_product_pages():
    gate = research_module._source_quality_gate(
        question="Compare this product price and availability",
        result={
            "title": "Example Product",
            "url": "https://www.amazon.com/dp/B000000",
            "sourceQualityHints": {
                "authorityScore": 80,
                "authorityTier": "primary",
                "catalogSourceId": "shopping_platform_primary",
            },
        },
        read_payload={
            "ok": True,
            "text": "Customers also bought | Sponsored | Recommended | Footer | Navigation | Cookie | Related | Ads | Popup",
            "missingContentReason": "low_text_content",
        },
        source_policy="authoritative",
    )

    assert gate["selectedForEvidence"] is False
    assert gate["rejectedReason"]


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


def test_context7_calls_current_required_argument_schema(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeManager:
        async def initialize(self):
            return None

        async def call_tool(self, *, server_name, tool_name, arguments):
            calls.append((tool_name, dict(arguments)))
            if tool_name == "resolve-library-id":
                return {"content": [{"text": "Context7-compatible library ID: /langchain-ai/langchain"}]}
            return {"content": [{"text": "LangChain create_agent and middleware documentation. " * 20}]}

    monkeypatch.setattr(research_module, "mcp_manager", FakeManager())
    monkeypatch.setattr(
        research_module,
        "_context7_tool_names",
        lambda: ("context7", "resolve-library-id", "query-docs"),
    )

    result = asyncio.run(
        research_module._call_context7_source_async(
            "LangChain Python v1 create_agent middleware API"
        )
    )

    assert result["ok"] is True
    assert set(calls[0][1]) == {"query", "libraryName"}
    assert calls[0][1]["libraryName"] == "LangChain Python"
    assert set(calls[1][1]) == {"libraryId", "query"}
    assert calls[1][1]["libraryId"] == "/langchain-ai/langchain"


def test_narrow_technical_shards_use_compact_official_queries() -> None:
    question = (
        "核查 LangChain Python v1 当前 create_agent 与 middleware API 的官方用法、"
        "版本边界和安装方式，并给出有来源的简体中文结论。"
    )

    shards = research_module._build_shards(
        question=question,
        research_intent="",
        source_policy="authoritative",
        seed_urls=[],
        allowed_domains=[],
        max_shards=6,
    )
    queries = [str(item.get("query") or "") for item in shards]

    assert queries[0] == "LangChain Python v1 create_agent middleware API"
    assert all("并给出" not in query for query in queries)
    assert all("research report data statistics" not in query for query in queries)
    assert all("independent analysis comparison" not in query for query in queries)
    assert any(query.startswith("site:docs.langchain.com ") for query in queries)
    assert any("API reference examples" in query for query in queries)

    requirements = research_module._research_delivery_requirements(question)
    assert requirements["mode"] == "narrow_authoritative_technical"
    assert requirements["minimumSources"] == 2
    assert requirements["minimumDistinctHosts"] == 1
    assert requirements["minimumAnswerChars"] == research_module.MIN_RESEARCH_ANSWER_CHARS
    assert requirements["targetSources"] == 4
    assert requirements["targetDistinctHosts"] == 1
    assert requirements["targetClaims"] == 6
    assert requirements["targetAnswerChars"] == 3_000

    entity_hints = research_module._catalog_official_entity_hints(question)
    assert [item["label"] for item in entity_hints] == ["LangChain Python"]


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


def test_architect_rejects_answer_citations_without_verified_claim_binding() -> None:
    claims = [
        {
            "claimId": "C1",
            "supportingSources": [{"citationKey": "S1"}],
        }
    ]

    assert research_module._architect_unbound_answer_citations(
        "Supported [S1], but the writer invented [S8].",
        claims,
    ) == ["S8"]


def test_comparative_or_explicit_multifacet_research_keeps_standard_floor() -> None:
    comparison = research_module._research_delivery_requirements(
        "Compare React versus Vue runtime behavior and migration limits."
    )
    explicit = research_module._research_delivery_requirements(
        "1. [api] Verify LangChain API behavior.\n2. [install] Verify package installation."
    )

    for requirements in (comparison, explicit):
        assert requirements["mode"] == "standard_research"
        assert requirements["minimumSources"] == research_module.MIN_RESEARCH_SOURCE_COUNT
        assert requirements["minimumDistinctHosts"] == research_module.MIN_RESEARCH_DISTINCT_HOST_COUNT

    assert research_module._research_facet_requires_atomic_claim("architect-gap-1a0fc13d") is False


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


def test_source_candidate_quality_gate_uses_atomic_facet_query(monkeypatch):
    observed_questions: list[str] = []

    def fake_gate(*, question, result, read_payload, source_policy):
        observed_questions.append(question)
        return {"selectedForEvidence": True, "qualityDimensions": {}}

    monkeypatch.setattr(research_module, "_source_quality_gate", fake_gate)
    facet_query = "Verify the GPAI systemic-risk threshold."
    candidates = research_module._research_source_candidates(
        "A much larger combined question spanning unrelated compliance facets.",
        [
            {
                "shardId": "facet-systemic-risk",
                "kind": "facet:systemic-risk",
                "query": facet_query,
                "provider": "fake",
                "results": [
                    {
                        "title": "Official systemic-risk guidance",
                        "url": "https://example.eu/systemic-risk",
                        "snippet": "The threshold and designation procedure.",
                        "sourceQualityHints": {"authorityScore": 90},
                    }
                ],
                "fetchedTopSources": [
                    {
                        "ok": True,
                        "url": "https://example.eu/systemic-risk",
                        "finalUrl": "https://example.eu/systemic-risk",
                        "title": "Official systemic-risk guidance",
                        "text": "The threshold and designation procedure are defined here. " * 80,
                    }
                ],
            }
        ],
        source_policy="authoritative",
    )

    assert candidates
    assert observed_questions == [facet_query]
    assert candidates[0]["evidenceQuery"] == facet_query




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


def test_research_bundle_shards_prioritize_bodies_for_selected_sources():
    selected_sources = [
        {"url": f"https://selected-{index}.example/evidence"}
        for index in range(research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT)
    ]
    diagnostic_shards = [
        {
            "shardId": f"diagnostic-{index}",
            "fetchedTopSources": [],
        }
        for index in range(2)
    ]
    source_shards = [
        {
            "shardId": f"selected-{index}",
            "fetchedTopSources": [
                {
                    "ok": True,
                    "url": source["url"],
                    "text": f"Readable selected evidence {index}",
                }
            ],
        }
        for index, source in enumerate(selected_sources)
    ]

    retained = research_module._research_shards_for_bundle(
        "selected evidence question",
        [*diagnostic_shards, *source_shards],
        selected_sources,
    )

    assert [shard["shardId"] for shard in retained] == [
        f"selected-{index}"
        for index in range(research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT)
    ]


def test_research_bundle_source_matrix_prioritizes_architect_projection_before_cap():
    source_count = research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT + 8
    source_matrix = [
        {
            "sourceId": f"source-{index}",
            "citationKey": f"R{index}",
            "url": f"https://source-{index}.example/evidence",
            "selectedForEvidence": True,
            "sourceQualityGate": {"selectedForEvidence": True},
        }
        for index in range(1, source_count + 1)
    ]
    architect_sources = [
        {
            "url": source_matrix[index]["url"],
            "citationKey": f"S{position}",
        }
        for position, index in enumerate(
            range(
                source_count - 1,
                source_count - 1 - research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT,
                -1,
            ),
            start=1,
        )
    ]

    persisted = research_module._research_source_matrix_for_bundle(
        "comparison question",
        source_matrix,
        architect_sources,
    )

    assert [source["url"] for source in persisted] == [
        source["url"] for source in architect_sources
    ]
    assert [source["citationKey"] for source in persisted] == [
        f"S{index}"
        for index in range(1, research_module._RESEARCH_ARCHITECT_MAX_SOURCE_COUNT + 1)
    ]


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


def test_architect_delivery_gate_does_not_promote_quality_targets_to_refusal(monkeypatch):
    monkeypatch.setattr(
        research_module,
        "research_acceptance_issues",
        lambda _payload: ["minimum_contract_issue"],
    )
    monkeypatch.setattr(
        research_module,
        "research_high_quality_issues",
        lambda _payload: (_ for _ in ()).throw(AssertionError("target gate must stay diagnostic")),
    )

    assert research_module._architect_delivery_quality_issues({}) == ["minimum_contract_issue"]


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


def test_temporal_refinement_queries_use_subject_and_authoritative_host():
    source_matrix = [
        {
            "url": "https://docs.python.org/3/library/pathlib.html",
            "host": "docs.python.org",
            "tier": "primary", "sourceRole": "primary",
            "authorityScore": 95,
            "selectedForEvidence": True,
        },
        {
            "url": "https://realpython.com/python-pathlib/",
            "host": "realpython.com",
            "tier": "secondary", "sourceRole": "secondary",
            "authorityScore": 65,
            "selectedForEvidence": True,
        },
    ]

    queries = research_module._build_temporal_refinement_queries(
        "What are the current best practices for using Python pathlib in CLI tools? cite official sources.",
        source_matrix,
        seen_queries=set(),
        limit=2,
    )

    assert len(queries) == 2
    assert queries == [
        "https://peps.python.org/pep-0428/ pathlib object-oriented filesystem paths PEP 428",
        "https://peps.python.org/pep-0519/ pathlib PathLike filesystem path protocol PEP 519",
    ]
    assert all("release notes changelog version history" not in query for query in queries)
    assert all("what are the current best practices" not in query.lower() for query in queries)

    next_queries = research_module._build_temporal_refinement_queries(
        "What are the current best practices for using Python pathlib in CLI tools? cite official sources.",
        source_matrix,
        seen_queries={query.lower() for query in queries},
        limit=2,
    )

    assert next_queries == [
        "https://docs.python.org/3/library/os.html pathlib PathLike fspath current Python documentation",
        "https://click.palletsprojects.com/en/stable/parameter-types path_type pathlib.Path version",
    ]

    change_queries = research_module._build_temporal_refinement_queries(
        "What changed in the latest Python pathlib release notes?",
        source_matrix,
        seen_queries=set(),
        limit=2,
    )
    assert any("release notes changelog version history" in query for query in change_queries)


def test_architect_direct_url_repair_skips_only_sources_that_were_successfully_read():
    question = "What are the current pathlib CLI practices?"
    pep_428 = "https://peps.python.org/pep-0428/"
    pep_519 = "https://peps.python.org/pep-0519/"
    read_identities = {
        research_module._research_document_identity(pep_428, question=question)
    }

    direct_urls, unread = research_module._research_unread_direct_urls(
        f"{pep_428} pathlib history and {pep_519} PathLike protocol",
        question=question,
        read_document_identities=read_identities,
    )

    assert direct_urls == [pep_428, pep_519]
    assert unread == [pep_519]
    _direct_urls, fully_read = research_module._research_unread_direct_urls(
        f"{pep_428} pathlib history",
        question=question,
        read_document_identities=read_identities,
    )
    assert fully_read == []


def test_architect_direct_url_readability_requires_successful_nonempty_content():
    assert research_module._research_shard_has_readable_fetch(
        {"fetchedTopSources": [{"ok": True, "text": "direct source body"}]}
    ) is True
    assert research_module._research_shard_has_readable_fetch(
        {"fetchedTopSources": [{"ok": True, "text": "  "}]}
    ) is False
    assert research_module._research_shard_has_readable_fetch(
        {"fetchedTopSources": [{"ok": False, "text": "error page"}]}
    ) is False


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


def test_search_shard_tries_one_alternate_provider_when_first_links_have_no_evidence(
    monkeypatch,
):
    search_calls: list[dict] = []

    def fake_source_router_search(**kwargs):
        search_calls.append(dict(kwargs))
        excluded = set(kwargs.get("excluded_providers") or [])
        provider = "bing_cn" if "metaso" in excluded else "metaso"
        url = (
            "https://second.gov.cn/official-rule"
            if provider == "bing_cn"
            else "https://first.gov.cn/unreadable-rule"
        )
        return json.dumps(
            {
                "ok": True,
                "provider": provider,
                "results": [
                    {
                        "title": "生成式人工智能服务官方规定",
                        "url": url,
                        "snippet": "官方发布的适用范围、日期和提供者义务。",
                    }
                ],
                "providerAttemptMatrix": [
                    {"provider": provider, "status": "ok", "resultCount": 1}
                ],
            }
        )

    def fake_source_router_read(**kwargs):
        if "first.gov.cn" in kwargs["url"]:
            return json.dumps(
                {
                    "ok": False,
                    "failureClass": "network_timeout",
                    "error": "first provider result was not readable",
                }
            )
        return json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "生成式人工智能服务管理规定",
                "text": (
                    "本规定明确适用范围、施行日期、服务提供者义务、内容标识与安全评估要求。"
                    * 80
                ),
            }
        )

    monkeypatch.setattr(research_module, "source_router_search", fake_source_router_search)
    monkeypatch.setattr(research_module, "source_router_read", fake_source_router_read)
    monkeypatch.setattr(research_module, "_jina_api_key", lambda: "")
    monkeypatch.setattr(
        research_module,
        "_source_quality_gate",
        lambda **_kwargs: {"selectedForEvidence": True, "reasons": []},
    )
    ledger = research_module._ResearchReadAttemptLedger(
        question="生成式人工智能服务规定关键日期与义务"
    )

    completed = research_module._run_search_shard(
        {
            "shardId": "alternate-provider",
            "kind": "official_source",
            "query": "生成式人工智能服务规定关键日期与义务",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="alternate-provider",
        read_attempt_ledger=ledger,
    )

    assert len(search_calls) == 2, completed
    assert "metaso" in search_calls[1]["excluded_providers"]
    assert completed["provider"] == "bing_cn"
    assert completed["alternateProviderAttempted"] is True
    assert completed["discardedSearchProviders"] == ["metaso"]
    assert completed["searchProviderAttempts"] == 2
    assert any(
        item.get("ok") and item.get("text")
        for item in completed["fetchedTopSources"]
    )
    provider_states = ledger.snapshot()["searchProviderStates"]
    assert provider_states["metaso"]["evidenceFailures"] == 1
    assert provider_states["bing_cn"]["evidenceSuccesses"] == 1
    # Only this shard excludes the first provider. A different query may
    # produce useful sources on the same reachable search engine.
    hints = ledger.search_route_hints()
    assert "metaso" not in hints["excludedProviders"]
    assert "metaso" in hints["preferredProviders"]


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


def test_chinese_refinement_does_not_append_english_or_current_year_noise():
    shards = [{
        "kind": "facet:labeling-law", "researchFacetId": "labeling-law",
        "query": "人工智能生成合成内容标识办法 全文",
        "fetchedTopSources": [],
    }]
    refined = research_module._build_refinement_shards(
        "核对人工智能生成合成内容标识办法的适用范围",
        shards, source_policy="authoritative", limit=1,
    )
    assert refined[0]["query"] == '"人工智能生成合成内容标识办法"'
    assert "人工智能生成合成内容标识办法 全文" in refined[0]["evidenceQuery"]
    assert "official" not in refined[0]["query"]
    assert str(datetime.now(timezone.utc).year) not in refined[0]["query"]


def test_short_cited_answer_is_not_erased_by_process_word_heuristic():
    answer = "本次调研确认：该办法未提供统一的申请期限，不能据此断言无需申请。[S1]"
    assert research_module._is_low_quality_research_answer(answer) is False
    assert research_module._is_low_quality_research_answer("本次调研无法获取目标文献，建议重新调研。") is True
    # Padding a process-only failure does not turn it into an answer.
    assert research_module._is_low_quality_research_answer("本次调研无法获取目标文献。" * 150) is True


@pytest.mark.parametrize("question", ["历史制度沿革", "historical administrative rules"])
def test_refinement_without_facets_keeps_language_and_question_dates(monkeypatch, question):
    monkeypatch.setattr(research_module, "_build_question_facet_queries", lambda _q: [])
    monkeypatch.setattr(research_module, "_required_research_facet_queries", lambda *_args: [])
    refined = research_module._build_refinement_shards(
        question, [], source_policy="authoritative", limit=6,
    )
    assert len(refined) == 6
    for shard in refined:
        assert shard["query"].startswith(question)
        assert str(datetime.now(timezone.utc).year) not in shard["query"]
    if "历史" in question:
        assert all(not re.search(r"[A-Za-z]", item["query"]) for item in refined)
    else:
        assert all(not re.search(r"[\u4e00-\u9fff]", item["query"]) for item in refined)


def test_search_shard_keeps_first_provider_when_it_yields_evidence(monkeypatch):
    search_calls: list[dict] = []

    def fake_source_router_search(**kwargs):
        search_calls.append(dict(kwargs))
        return json.dumps(
            {
                "ok": True,
                "provider": "metaso",
                "results": [
                    {
                        "title": "官方规定",
                        "url": "https://first.gov.cn/readable-rule",
                        "snippet": "官方规定全文。",
                    }
                ],
                "providerAttemptMatrix": [
                    {"provider": "metaso", "status": "ok", "resultCount": 1}
                ],
            }
        )

    monkeypatch.setattr(research_module, "source_router_search", fake_source_router_search)
    monkeypatch.setattr(
        research_module,
        "source_router_read",
        lambda **kwargs: json.dumps(
            {
                "ok": True,
                "status": 200,
                "finalUrl": kwargs["url"],
                "title": "官方规定",
                "text": "官方正文包含适用范围、日期和义务。" * 80,
            }
        ),
    )
    monkeypatch.setattr(
        research_module,
        "_source_quality_gate",
        lambda **_kwargs: {"selectedForEvidence": True, "reasons": []},
    )

    completed = research_module._run_search_shard(
        {
            "shardId": "first-provider-ready",
            "kind": "official_source",
            "query": "官方规定适用范围日期义务",
        },
        allowed_domains=[],
        blocked_domains=[],
        source_policy="authoritative",
        max_rounds=2,
        use_agent_browser_profile=False,
        tool_call_id="first-provider-ready",
        read_attempt_ledger=research_module._ResearchReadAttemptLedger(
            question="官方规定适用范围日期义务"
        ),
    )

    assert len(search_calls) == 1
    assert completed["provider"] == "metaso"
    assert completed["searchProviderAttempts"] == 1
    assert "alternateProviderAttempted" not in completed


def test_narrow_research_stops_before_architect_repair_when_no_body_qualifies(
    monkeypatch,
):
    _search_then_no_answer(monkeypatch, "Python pathlib API")
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
    monkeypatch.setattr(research_module, "list_evidence_bundles", lambda **_kwargs: [])
    monkeypatch.setattr(
        research_module,
        "_research_should_decompose_root_question",
        lambda _question: False,
    )
    search_calls = 0

    def fake_search(**kwargs):
        nonlocal search_calls
        search_calls += 1
        return json.dumps(
            {
                "ok": True,
                "provider": "reachable-search",
                "results": _unique_search_result_batch(
                    search_calls,
                    kwargs.get("query", ""),
                ),
            }
        )

    monkeypatch.setattr(research_module, "web_search", SimpleNamespace(func=fake_search))
    monkeypatch.setattr(
        research_module,
        "web_read",
        SimpleNamespace(
            func=lambda **_kwargs: json.dumps(
                {
                    "ok": False,
                    "failureClass": "network_timeout",
                    "error": "fixture_body_timeout",
                }
            )
        ),
    )

    payload = json.loads(
        research_module.research_broker.func(
            mode="run",
            question="Verify the current Python pathlib API from official docs.",
            maxShards=10,
            maxRounds=5,
            forceRefresh=True,
            state={"run_id": "run-no-qualified-body"},
        )
    )

    assert payload["deliveryReady"] is False
    assert payload["researchLoopState"]["stopReason"] == (
        "research_no_supported_answer"
    )
    assert len(payload["researchLoopState"]["rounds"]) <= 2
    assert search_calls <= 8
    assert payload["researchAnswerPack"]["answer"] == ""
