from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage

import core.tools.research_broker as research_module


_AS_OF = "2026-07-29T03:00:00Z"


class _Candidate:
    def __init__(
        self,
        *,
        max_tokens: int,
        model_ref: str,
        supports_no_think: bool = False,
    ) -> None:
        self._meta = {
            "model_ref": model_ref,
            "global_max_tokens": max_tokens,
            "global_context_window": 1_000_000,
            "thinking_control": {"supportsNoThink": supports_no_think},
        }


class _DualInvocationCandidate(_Candidate):
    def __init__(self) -> None:
        super().__init__(max_tokens=4096, model_ref="fixture::dual")
        self.lock = threading.Lock()
        self.sync_calls = 0
        self.async_calls = 0

    def invoke(self, _messages, **_kwargs):  # noqa: ANN001
        with self.lock:
            self.sync_calls += 1
        return AIMessage(content="sync")

    async def ainvoke(self, _messages, **_kwargs):  # noqa: ANN001
        with self.lock:
            self.async_calls += 1
        return AIMessage(content="async")


def test_segmented_writer_profile_prefers_one_answer_with_a_normal_output_budget() -> None:
    low = research_module._architect_segmented_writer_profile(
        (_Candidate(max_tokens=4096, model_ref="fixture::low"), "fixture::low", "summary")
    )
    high = research_module._architect_segmented_writer_profile(
        (
            _Candidate(
                max_tokens=32_768,
                model_ref="fixture::high",
                supports_no_think=True,
            ),
            "fixture::high",
            "summary",
        )
    )
    unknown = research_module._architect_segmented_writer_profile(
        (type("UnknownCandidate", (), {"_meta": {"model_ref": "fixture::unknown"}})(), "fixture::unknown", "summary")
    )

    assert low == {
        "enabled": False,
        "configuredMaxTokens": 4096,
        "sectionCount": 4,
        "sectionMaxTokens": research_module._RESEARCH_ARCHITECT_SECTION_MAX_TOKENS,
        "targetMinChars": 1625,
        "targetMaxChars": 2750,
    }
    assert high["configuredMaxTokens"] == 32_768
    assert high["enabled"] is False
    assert unknown["configuredMaxTokens"] is None
    assert unknown["enabled"] is True


def test_architect_parallel_workers_use_sync_client_instead_of_cross_loop_async_client() -> None:
    llm = _DualInvocationCandidate()
    candidate = (llm, "fixture::dual", "summary")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _index: research_module._invoke_architect_candidate_with_deadline(
                    candidate,
                    [],
                    seconds=2,
                    max_tokens=128,
                ),
                range(2),
            )
        )

    assert [response.content for response in responses] == ["sync", "sync"]
    assert llm.sync_calls == 2
    assert llm.async_calls == 0


def test_segment_tasks_are_stable_disjoint_and_enforce_their_citation_scope() -> None:
    claims = [
        {
            "claimId": f"claim-{index}",
            "claim": f"Distinct verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 9)
    ]
    plan = {
        "claimTable": claims,
        "answerOutline": [
            {
                "sectionId": f"planned-{index}",
                "title": title,
                "objective": title,
                "claimIds": [f"claim-{index * 2 - 1}", f"claim-{index * 2}"],
            }
            for index, title in enumerate(("one", "two", "three", "four"), start=1)
        ],
        "compositeInferences": [
            {"inferenceId": "combined", "premiseClaimIds": ["claim-3", "claim-4"]},
            {"inferenceId": "cross-section", "premiseClaimIds": ["claim-2", "claim-3"]},
        ],
        "conflictMatrix": [{"topic": "known boundary"}],
        "temporalAssessment": {"status": "current"},
    }

    first = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1600,
        target_max_chars=2800,
    )
    second = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1600,
        target_max_chars=2800,
    )

    assert first == second
    assert [task["sectionId"] for task in first] == [
        "section_1",
        "section_2",
        "section_3",
        "section_4",
    ]
    assert [[claim["claimId"] for claim in task["assignedClaims"]] for task in first] == [
        ["claim-1", "claim-2"],
        ["claim-3", "claim-4"],
        ["claim-5", "claim-6"],
        ["claim-7", "claim-8"],
    ]
    assert [task["requiredCitationKeys"] for task in first] == [
        ["S1", "S2"],
        ["S3", "S4"],
        ["S5", "S6"],
        ["S7", "S8"],
    ]
    assert first[0]["claimCitationChecklist"] == [
        {"claimId": "claim-1", "citationKeys": ["S1"]},
        {"claimId": "claim-2", "citationKeys": ["S2"]},
    ]
    assert all("minimumAcceptableChars" not in task for task in first)
    assert first[1]["compositeInferences"] == [plan["compositeInferences"][0]]
    assert all(
        inference.get("inferenceId") != "cross-section"
        for task in first
        for inference in task["compositeInferences"]
    )
    assert research_module._architect_outline_inference_issues(
        plan["answerOutline"],
        claims,
        plan["compositeInferences"],
    ) == ["composite_inference_2_crosses_sections"]
    assert first[-1]["conflictMatrix"] == plan["conflictMatrix"]
    assert first[0]["directConclusionSection"] is True


    assert first[-1]["limitationsAndActionSection"] is True

    scoped_task = {
        **first[0],
        "targetMinChars": 0,
        "targetMaxChars": 1000,
        "minimumAcceptableChars": 0,
    }
    issues = research_module._architect_section_issues(
        "A sufficiently detailed local claim [S1] also cites an unassigned source [S3].",
        scoped_task,
        complete=True,
    )
    assert "section_citation_missing:S2" in issues
    assert "section_citation_out_of_scope:S3" in issues

    uneven_but_substantive = "A concise conclusion [S1] and its limitation [S2]."
    assert research_module._architect_section_issues(
        uneven_but_substantive,
        first[0],
        complete=True,
    ) == []


def test_segment_tasks_carry_required_facet_goals_from_verified_claim_lineage() -> None:
    claims = [
        {
            "claimId": "claim-timeline",
            "claim": "The timeline has a verified effective date.",
            "supportingSources": [
                {"citationKey": "S1", "researchFacetId": "timeline"}
            ],
        },
        {
            "claimId": "claim-penalties",
            "claim": "The penalty framework has a verified ceiling.",
            "supportingSources": [
                {"citationKey": "S2", "researchFacetId": "penalties"}
            ],
        },
    ]
    tasks = research_module._architect_segment_tasks(
        {
            "question": (
                "Research every item below.\n"
                "1. [timeline] Verify the effective date.\n"
                "2. [penalties] Verify the penalty framework."
            ),
            "requiredFacets": [
                {"facetId": "timeline", "goal": "Verify the effective date."},
                {"facetId": "penalties", "goal": "Verify the penalty framework."},
            ],
            "claimTable": claims,
            "answerOutline": [
                {
                    "sectionId": "timeline-section",
                    "title": "Timeline",
                    "objective": "Answer the timeline facet.",
                    "claimIds": ["claim-timeline"],
                },
                {
                    "sectionId": "penalty-section",
                    "title": "Penalties",
                    "objective": "Answer the penalties facet.",
                    "claimIds": ["claim-penalties"],
                },
            ],
        },
        section_count=2,
        target_min_chars=20,
        target_max_chars=400,
    )

    assert tasks[0]["facetIds"] == ["timeline"]
    assert tasks[0]["facetGoals"] == [
        {"facetId": "timeline", "goal": "Verify the effective date."}
    ]
    assert tasks[1]["facetIds"] == ["penalties"]


def test_sparse_outline_keeps_topic_claim_ownership_instead_of_balancing_by_count() -> None:
    claims = [
        {"claimId": f"claim-{index}", "claim": f"Topic-specific fact {index}",
         "supportingSources": [{"citationKey": f"S{index}"}]}
        for index in range(1, 7)
    ]
    outline = [
        {"sectionId": "first", "title": "First document", "claimIds": ["claim-1"]},
        {"sectionId": "second", "title": "Second document", "claimIds": ["claim-2", "claim-3", "claim-4"]},
        {"sectionId": "third", "title": "Third document", "claimIds": ["claim-5", "claim-6"]},
    ]
    tasks = research_module._architect_segment_tasks(
        {"claimTable": claims, "answerOutline": outline}, section_count=3,
        target_min_chars=1000, target_max_chars=1800,
    )
    assert [[claim["claimId"] for claim in task["assignedClaims"]] for task in tasks] == [
        section["claimIds"] for section in outline
    ]
    assert [[key for key in task["requiredCitationKeys"]] for task in tasks] == [
        ["S1"], ["S2", "S3", "S4"], ["S5", "S6"],
    ]
    assert [section["claimId"] for section in claims] == [f"claim-{i}" for i in range(1, 7)]


def test_segment_tasks_allocate_depth_by_claim_weight_without_lowering_total_target() -> None:
    claims = [
        {
            "claimId": f"claim-{index}",
            "claim": f"Distinct verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 11)
    ]
    plan = {
        "claimTable": claims,
        "answerOutline": [
            {"sectionId": "one", "claimIds": ["claim-1", "claim-2"]},
            {
                "sectionId": "two",
                "claimIds": ["claim-3", "claim-4", "claim-5", "claim-6"],
            },
            {"sectionId": "three", "claimIds": ["claim-7", "claim-8"]},
            {"sectionId": "four", "claimIds": ["claim-9", "claim-10"]},
        ],
        "compositeInferences": [],
        "conflictMatrix": [],
    }

    tasks = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1625,
        target_max_chars=2750,
    )

    assert [task["targetMinChars"] for task in tasks] == [1300, 2600, 1300, 1300]
    assert all("minimumAcceptableChars" not in task for task in tasks)
    assert sum(task["targetMinChars"] for task in tasks) == 6500

    five_section_plan = {
        **plan,
        "answerOutline": [
            {"sectionId": "one", "claimIds": ["claim-1", "claim-2"]},
            {"sectionId": "two", "claimIds": ["claim-3", "claim-4"]},
            {"sectionId": "three", "claimIds": ["claim-5", "claim-6"]},
            {"sectionId": "four", "claimIds": ["claim-7", "claim-8"]},
            {"sectionId": "five", "claimIds": ["claim-9", "claim-10"]},
        ],
    }
    five_section_tasks = research_module._architect_segment_tasks(
        five_section_plan,
        section_count=4,
        target_min_chars=1625,
        target_max_chars=2750,
    )

    assert len(five_section_tasks) == 5
    assert 6500 <= sum(task["targetMinChars"] for task in five_section_tasks) < 7000


def test_single_claim_section_accepts_evidence_without_a_length_quota() -> None:
    claim = {
        "claimId": "claim-one",
        "claim": "Path methods return Path objects and permit method chaining.",
        "supportingSources": [{"citationKey": "S1"}],
        "evidenceExcerpt": "Path methods return Path objects, which allows for method chaining.",
    }
    tasks = research_module._architect_segment_tasks(
        {
            "claimTable": [claim],
            "answerOutline": [{"sectionId": "one", "claimIds": ["claim-one"]}],
            "compositeInferences": [],
            "conflictMatrix": [],
        },
        section_count=4,
        target_min_chars=1625,
        target_max_chars=2750,
    )

    assert len(tasks) == 1
    assert "minimumAcceptableChars" not in tasks[0]
    assert research_module._architect_section_issues(
        claim["claim"] + " [S1]", tasks[0], complete=True,
    ) == []


def test_segment_tasks_do_not_promote_incidental_excerpt_anchors_to_writer_permissions() -> None:
    claim = {
        "claimId": "claim-install",
        "claim": "The product documents a native Windows installation path.",
        "supportingSources": [{"citationKey": "S1"}],
        "evidenceExcerpt": (
            "The adjacent setup example mentions Node.js 22, gpt-5.6-sol, "
            "and a 4096 tokens limit, but those details are not part of this claim."
        ),
    }

    tasks = research_module._architect_segment_tasks(
        {
            "claimTable": [claim],
            "answerOutline": [{"sectionId": "install", "claimIds": ["claim-install"]}],
            "compositeInferences": [],
        },
        section_count=1,
        target_min_chars=20,
        target_max_chars=400,
    )

    assert research_module._architect_hard_assertion_anchors(claim["claim"]) == []
    assert research_module._architect_hard_assertion_anchors(claim["evidenceExcerpt"])
    assert tasks[0]["permittedHardAnchors"] == []


def test_runtime_source_appendix_is_deterministic_and_numeric() -> None:
    sources = [
        {
            "citationKey": "S10",
            "title": "Tenth Source",
            "url": "https://example.com/ten",
            "publishedAt": "2026-07-10",
        },
        {
            "citationKey": "S2",
            "title": "Second Source",
            "url": "https://example.com/two",
            "updatedAt": "2026-07-20",
        },
        {
            "citationKey": "S1",
            "title": "First Source",
            "url": "https://example.com/one",
            "version": "v1.4",
        },
        {
            "citationKey": "S3",
            "title": "Older Foundation",
            "url": "https://example.com/older",
            "publishedAt": "2018-07-20",
        },
        {
            "citationKey": "S4",
            "title": "Undated Experience",
            "url": "https://example.com/undated",
            "tier": "secondary",
            "sourceRole": "secondary",
        },
        {
            "citationKey": "S5",
            "title": "Epoch Metadata",
            "url": "https://example.com/epoch",
            "updatedAt": "1696032739",
        },
        {
            "citationKey": "S6",
            "title": "Retrieval Alias",
            "url": "https://example.com/retrieval-alias",
            "retrievedAt": "2026-07-29T12:00:00Z",
            "sourceDate": "2026-07-29",
            "sourceDateKind": "retrieved_at",
        },
        {
            "citationKey": "S12",
            "title": "Unreferenced Source",
            "url": "https://example.com/unreferenced",
            "updatedAt": "2026-07-21",
        },
    ]
    kwargs = {
        "question": "What changed?",
        "verified_plan": {"headline": "Stable report", "asOf": _AS_OF},
        "sections": ["## Finding\n\nA source-backed finding [S1][S2][S3][S4][S5][S6][S10]."],
        "sources": sources,
    }

    first = research_module._assemble_architect_sections(**kwargs)
    second = research_module._assemble_architect_sections(**kwargs)

    assert first == second
    assert "## Evidence currency and applicability" in first
    assert (
        "[S1] First Source: applicable version v1.4; evidence status version-bounded; "
        "the version bounds the evidence scope; broader or current applicability is left to the independent Reviewer."
    ) in first
    assert "[S2] Second Source: updated 2026-07-20; evidence status source-reported document dates are shown explicitly" in first
    assert "[S3] Older Foundation: published 2018-07-20; evidence status source-reported document dates are shown explicitly" in first
    assert "[S4] Undated Experience (secondary/experience source): evidence status Secondary material must remain attributed" in first
    assert "no page publication date or version was resolved" in first
    assert "non-temporal API" not in first
    assert "[S5] Epoch Metadata: updated 2023-09-30" in first
    assert "[S6] Retrieval Alias: retrieved 2026-07-29T12:00:00Z; evidence status no page publication date or version was resolved" in first
    assert "Retrieval Alias - https://example.com/retrieval-alias (2026-07-29)" not in first
    assert "[S10] Tenth Source: published 2026-07-10; evidence status source-reported document dates are shown explicitly" in first
    assert "Unreferenced Source" not in first
    assert "No fixed document-age cutoff is applied" in first
    assert "Retrieval time records when the evidence was fetched" in first
    assert "broad window" not in first.lower()
    assert first.index("- [S1] First Source - https://example.com/one (version v1.4)") < first.index(
        "- [S2] Second Source - https://example.com/two (updated 2026-07-20)"
    )
    assert first.index("- [S2] Second Source - https://example.com/two (updated 2026-07-20)") < first.index(
        "- [S10] Tenth Source - https://example.com/ten (published 2026-07-10)"
    )
    assert first.count("## Sources") == 1


def test_section_assembly_restores_only_missing_verified_audience_inferences() -> None:
    claims = [
        {
            "claimId": "C1",
            "claim": "个人方案包含本地交互入口。",
            "supportingSources": [{"citationKey": "S1", "title": "个人方案文档"}],
            "evidenceExcerpt": "个人方案包含本地交互入口。",
        },
        {
            "claimId": "C2",
            "claim": "个人方案允许按需复核结果。",
            "supportingSources": [{"citationKey": "S2", "title": "复核文档"}],
            "evidenceExcerpt": "个人方案允许按需复核结果。",
        },
    ]
    inference_text = "个人开发者可把本地交互与按需复核结合为默认工作方式。"
    plan = {
        "headline": "选择建议",
        "claimTable": claims,
        "compositeInferences": [
            {
                "inferenceId": "inference-individual",
                "audience": "个人开发者",
                "inference": inference_text,
                "premiseClaimIds": ["C1", "C2"],
            }
        ],
    }
    sources = [
        {"citationKey": "S1", "title": "个人方案文档", "url": "https://one.example"},
        {"citationKey": "S2", "title": "复核文档", "url": "https://two.example"},
    ]
    fact_section = "## 已验证事实\n\n个人方案有两个已验证前提 [S1][S2]。"

    restored = research_module._assemble_architect_sections(
        question="请按使用者类型给出选型建议。",
        verified_plan=plan,
        sections=[fact_section],
        sources=sources,
    )

    assert "## 按使用者类型的可执行选型建议" in restored
    assert "### 个人开发者" in restored
    assert f"**本报告的综合判断：** {inference_text} [S1][S2]" in restored

    already_rendered = (
        f"## 个人开发者\n\n**本报告的综合判断：** {inference_text} [S1][S2]"
    )
    deduplicated = research_module._assemble_architect_sections(
        question="请按使用者类型给出选型建议。",
        verified_plan=plan,
        sections=[already_rendered, fact_section],
        sources=sources,
    )

    assert deduplicated.count(inference_text) == 1
    assert "## 按使用者类型的可执行选型建议" not in deduplicated


def test_split_section_requires_synthesis_only_where_an_inference_survives() -> None:
    claims = [
        {
            "claimId": f"C{index}",
            "claim": f"Verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 5)
    ]
    task = {
        "sectionId": "section_1",
        "sequence": 1,
        "assignedClaims": claims,
        "compositeInferences": [
            {
                "inferenceId": "I1",
                "inference": "Claims one and two support a bounded synthesis.",
                "premiseClaimIds": ["C1", "C2"],
            }
        ],
        "requiresSynthesisConclusion": True,
        "targetMinChars": 1_600,
        "targetMaxChars": 2_800,
    }

    children = research_module._split_architect_section_task(task)

    assert children[0]["compositeInferences"] == task["compositeInferences"]
    assert children[0]["requiresSynthesisConclusion"] is True
    assert children[1]["compositeInferences"] == []
    assert children[1]["requiresSynthesisConclusion"] is False
