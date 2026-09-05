from __future__ import annotations

import copy

import pytest

from core.tools.research_claim_plan import (
    CANONICAL_CLAIM_PLAN_VERSION,
    CanonicalClaimPlanError,
    apply_structure_projection,
    build_canonical_claim_plan,
    question_requires_structure,
    supported_scope_limitation_markdown,
)


def _sources() -> list[dict]:
    rows: list[dict] = []
    topics = ("architecture", "retrieval", "governance", "recovery")
    for source_index, topic in enumerate(topics, start=1):
        citation_key = f"S{source_index}"
        facet_id = f"facet-{source_index}"
        candidates = [
            {
                "evidenceExcerptKey": f"{citation_key}:E1",
                "text": (
                    f"The official {topic} record establishes its distinct operational requirement "
                    "and binds it to a stable evidence receipt."
                ),
                "relevanceScore": 90 - source_index,
                "researchFacetId": facet_id,
                "researchFacetGoal": f"Verify requirement {source_index}",
            },
            {
                "evidenceExcerptKey": f"{citation_key}:E2",
                "text": (
                    f"The official {topic} record provides a separate {topic} validation "
                    "boundary and a reproducible failure condition."
                ),
                "relevanceScore": 70 - source_index,
                "researchFacetId": facet_id,
                "researchFacetGoal": f"Verify requirement {source_index}",
            },
        ]
        rows.append(
            {
                "sourceId": f"source-{source_index}",
                "citationKey": citation_key,
                "title": f"Official source {source_index}",
                "url": f"https://example.gov/source-{source_index}",
                "tier": "primary",
                "researchFacetIds": [facet_id],
                "researchFacetGoal": f"Verify requirement {source_index}",
                "text": "\n\n".join(item["text"] for item in candidates),
                "evidenceCandidates": candidates,
            }
        )
    return rows


def _plan() -> dict:
    return build_canonical_claim_plan(
        question="Compare the four requirements and provide an implementation recommendation.",
        sources=_sources(),
        required_source_keys=["S1", "S2", "S3", "S4"],
        required_facet_ids=["facet-1", "facet-2", "facet-3", "facet-4"],
        minimum_source_count=4,
        minimum_claim_count=5,
        target_claim_count=8,
    )


@pytest.mark.parametrize("question,expected", [
    ("核对适用关系、关键日期、提供者义务和上线检查清单。", True),
    ("Compare the requirements and their relationship; provide a launch checklist.", True),
    ("What is the publication date of this document?", False),
    ("核实文档的发布日期与第十一条原文。", False),
])
def test_structure_recognizes_analytical_deliverables(question, expected):
    assert question_requires_structure(question) is expected


@pytest.mark.parametrize("disqualified", ["source", "candidates"])
def test_canonical_plan_does_not_promote_offtopic_routing_metadata(disqualified):
    sources = _sources()
    if disqualified == "source":
        sources[-1]["subjectFocused"] = False
    else:
        for candidate in sources[-1]["evidenceCandidates"]:
            candidate["relevanceScore"] = 0
    plan = build_canonical_claim_plan(
        question="Compare requirements", sources=sources,
        required_source_keys=["S1", "S2", "S3", "S4"],
        required_facet_ids=["facet-1", "facet-2", "facet-3", "facet-4"],
        minimum_source_count=3, minimum_claim_count=4, target_claim_count=8,
        allow_supported_scope=True,
    )
    assert plan["canonicalClaimPlan"]["missingSourceKeys"] == ["S4"]
    assert plan["canonicalClaimPlan"]["missingFacetIds"] == ["facet-4"]
    assert all("facet-4" not in row["researchFacetIds"] for row in plan["claimTable"])


def test_reader_order_survives_coarse_public_relevance_reranking():
    sources = _sources()[:1]
    candidates = sources[0]["evidenceCandidates"]
    candidates[0]["relevanceScore"] = 50
    candidates[1]["relevanceScore"] = 100
    plan = build_canonical_claim_plan(
        question="Verify the actual requirement", sources=sources,
        required_source_keys=["S1"], required_facet_ids=["facet-1"],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    assert plan["claimTable"][0]["evidenceExcerptKey"] == "S1:E1"


def test_news_navigation_cannot_fill_a_missing_claim_source():
    sources = _sources()
    navigation = "关于标识办法的通知 2025-03-14 - 答记者问 2025-03-14 - 新闻头条丨春耕 - 科技快讯丨新材料 - 记者观察丨何以火出圈？它有这些不同 - 健康新闻丨零近视 - 国际观察丨贷款制度"
    sources[-1]["text"] = navigation
    sources[-1]["evidenceCandidates"] = [{
        "text": navigation, "evidenceExcerptKey": "S4:E1", "relevanceScore": 100,
        "researchFacetId": "facet-4",
    }]
    plan = build_canonical_claim_plan(
        question="Verify actual requirements", sources=sources,
        required_source_keys=["S1", "S2", "S3", "S4"],
        required_facet_ids=["facet-1", "facet-2", "facet-3", "facet-4"],
        minimum_source_count=3, minimum_claim_count=4, target_claim_count=8,
        allow_supported_scope=True,
    )
    assert plan["canonicalClaimPlan"]["missingSourceKeys"] == ["S4"]
    assert not any(navigation in row["evidenceExcerpt"] for row in plan["claimTable"])


def test_canonical_claim_plan_is_stable_complete_and_exact_excerpt_bound() -> None:
    first = _plan()
    second = _plan()

    assert first == second
    assert first["canonicalClaimPlan"]["version"] == CANONICAL_CLAIM_PLAN_VERSION
    assert first["canonicalClaimPlan"]["mode"] == "runtime_canonical"
    assert first["canonicalClaimPlan"]["claimCount"] == 8
    assert first["canonicalClaimPlan"]["sourceCount"] == 4
    assert first["canonicalClaimPlan"]["missingSourceKeys"] == []
    assert first["canonicalClaimPlan"]["missingFacetIds"] == []
    assert len({claim["claimId"] for claim in first["claimTable"]}) == 8
    assert all(claim["evidenceVerified"] is True for claim in first["claimTable"])
    assert all(
        claim["claim"] in claim["evidenceExcerpt"]
        for claim in first["claimTable"]
    )
    assert {
        source["citationKey"]
        for claim in first["claimTable"]
        for source in claim["supportingSources"]
    } == {"S1", "S2", "S3", "S4"}
    outline_claim_ids = [
        claim_id
        for section in first["answerOutline"]
        for claim_id in section["claimIds"]
    ]
    assert len(outline_claim_ids) == len(set(outline_claim_ids)) == 8
    assert set(outline_claim_ids) == {
        claim["claimId"] for claim in first["claimTable"]
    }


def test_canonical_claim_plan_binds_cjk_normative_cue_without_model_rewrite() -> None:
    sources = _sources()
    sources[0]["evidenceCandidates"][0]["text"] = (
        "The provider shall retain the exact evidence receipt and must not replace it with an unverified summary."
    )
    sources[0]["text"] = "\n\n".join(
        item["text"] for item in sources[0]["evidenceCandidates"]
    )
    plan = build_canonical_claim_plan(
        question="What does the source require?",
        sources=sources,
        required_source_keys=["S1"],
        required_facet_ids=["facet-1"],
        minimum_source_count=1,
        minimum_claim_count=1,
        target_claim_count=1,
    )

    claim = plan["claimTable"][0]
    assert claim["claimType"] == "explicit_normative"
    assert claim["normativeCue"] in claim["evidenceExcerpt"]


@pytest.mark.parametrize("text", [
    "提供具有舆论属性或者社会动员能力的服务的，提供者应当按照国家有关规定开展安全评估，并履行备案和变更、注销备案手续。",
    "Only when operating a public service, the provider must retain the complete evidence receipt, unless the user has withdrawn consent.",
])
def test_normative_claim_keeps_subject_conditions_and_exceptions(text):
    source = _sources()[0]
    source["text"] = text
    source["evidenceCandidates"] = [{"text": text, "evidenceExcerptKey": "S1:E1"}]
    plan = build_canonical_claim_plan(
        question="What is required and when?", sources=[source],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    assert plan["claimTable"][0]["claim"] == text


def test_canonical_dedup_preserves_conflicting_dates_and_thresholds():
    source = _sources()[0]
    texts = [
        "The published service limit is 100 requests per minute, effective from 2025-09-01.",
        "The published service limit is 200 requests per minute, effective from 2026-09-01.",
    ]
    source["text"] = "\n".join(texts)
    source["evidenceCandidates"] = [
        {"text": text, "evidenceExcerptKey": f"S1:E{index}"}
        for index, text in enumerate([*texts, texts[0]], 1)
    ]
    plan = build_canonical_claim_plan(
        question="Verify limits and dates", sources=[source],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=2, target_claim_count=3,
    )
    assert [row["claim"] for row in plan["claimTable"]] == texts


@pytest.mark.parametrize("path,is_navigation", [
    ("/news/columns/policy/index.html", True),
    ("/column/security/index.htm", True),
    ("/docs/architecture/index.html", False),
    ("/news/columns/policy/article123.html", False),
])
def test_cms_navigation_teasers_do_not_count_as_read_articles(path, is_navigation):
    sources = _sources()
    sources[-1]["url"] = "https://example.gov" + path
    plan = build_canonical_claim_plan(
        question="Compare requirements", sources=sources,
        required_source_keys=["S1", "S2", "S3", "S4"], required_facet_ids=[],
        minimum_source_count=3, minimum_claim_count=4, target_claim_count=8,
        allow_supported_scope=True,
    )
    assert ("S4" in plan["canonicalClaimPlan"]["missingSourceKeys"]) is is_navigation


def test_canonical_claim_plan_does_not_treat_descriptive_not_as_normative() -> None:
    sources = _sources()
    sources[0]["evidenceCandidates"][0]["text"] = (
        "The documented boundary records where this evidence does not apply to later releases."
    )
    sources[0]["text"] = "\n\n".join(
        item["text"] for item in sources[0]["evidenceCandidates"]
    )
    plan = build_canonical_claim_plan(
        question="What boundary does the source document?",
        sources=sources,
        required_source_keys=["S1"],
        required_facet_ids=["facet-1"],
        minimum_source_count=1,
        minimum_claim_count=1,
        target_claim_count=1,
    )

    claim = plan["claimTable"][0]
    assert claim["claimType"] == "source_fact"
    assert "normativeCue" not in claim


def test_source_already_selected_for_a_facet_is_not_duplicated_for_coverage() -> None:
    plan = build_canonical_claim_plan(
        question="Compare all required source facets.",
        sources=_sources(),
        required_source_keys=["S1", "S2", "S3", "S4"],
        required_facet_ids=["facet-1", "facet-2", "facet-3", "facet-4"],
        minimum_source_count=4,
        minimum_claim_count=4,
        target_claim_count=4,
    )

    assert len(plan["claimTable"]) == 4
    assert plan["canonicalClaimPlan"]["sourceCount"] == 4
    assert plan["canonicalClaimPlan"]["coveredFacetIds"] == [
        "facet-1",
        "facet-2",
        "facet-3",
        "facet-4",
    ]


def test_multi_facet_source_requires_one_exact_candidate_binding_per_facet() -> None:
    sources = _sources()
    source = sources[0]
    source["researchFacetIds"] = ["facet-1", "facet-security"]
    source["evidenceCandidates"][1].update(
        {
            "researchFacetId": "facet-security",
            "researchFacetGoal": "Verify the security boundary",
        }
    )
    plan = build_canonical_claim_plan(
        question="Verify both architecture and security facets.",
        sources=sources,
        required_source_keys=["S1"],
        required_facet_ids=["facet-1", "facet-security"],
        minimum_source_count=1,
        minimum_claim_count=1,
        target_claim_count=1,
    )

    assert len(plan["claimTable"]) == 2
    assert [claim["researchFacetIds"] for claim in plan["claimTable"]] == [
        ["facet-1"],
        ["facet-security"],
    ]


def test_structure_projection_cannot_replace_or_duplicate_canonical_claims() -> None:
    plan = _plan()
    original_claims = copy.deepcopy(plan["claimTable"])
    first_id = original_claims[0]["claimId"]
    projection = {
        "claimTable": [{"claimId": "forged", "claim": "forged"}],
        "answerOutline": [
            {
                "sectionId": "bad",
                "title": "Bad",
                "objective": "Bad",
                "claimIds": [first_id, first_id],
            },
            {
                "sectionId": "missing",
                "title": "Missing",
                "objective": "Missing",
                "claimIds": [],
            },
        ],
        "compositeInferences": [
            {
                "inferenceId": "forged",
                "inference": "This inference references a claim that does not exist.",
                "premiseClaimIds": ["forged"],
            }
        ],
    }

    projected, issues = apply_structure_projection(plan, projection)

    assert projected["claimTable"] == original_claims
    assert projected["answerOutline"] == plan["answerOutline"]
    assert projected["compositeInferences"] == []
    assert "structure_section_1_claim_binding_invalid" in issues
    assert "structure_inference_1_binding_invalid" in issues


def test_structure_projection_accepts_only_bound_outline_and_inference() -> None:
    plan = _plan()
    claim_ids = [claim["claimId"] for claim in plan["claimTable"]]
    projection = {
        "answerOutline": [
            {
                "sectionId": "facts",
                "title": "Verified facts",
                "objective": "Explain verified facts.",
                "claimIds": claim_ids[:4],
            },
            {
                "sectionId": "decision",
                "title": "Implementation decision",
                "objective": "Derive one bounded recommendation.",
                "claimIds": claim_ids[4:],
            },
        ],
        "compositeInferences": [
            {
                "inferenceId": "decision-1",
                "inference": "The implementation should preserve the four verified boundaries together.",
                "premiseClaimIds": claim_ids[4:6],
            }
        ],
    }

    projected, issues = apply_structure_projection(plan, projection)

    assert issues == []
    assert projected["claimTable"] == plan["claimTable"]
    assert projected["answerOutline"] == projection["answerOutline"]
    assert projected["compositeInferences"] == projection["compositeInferences"]


def test_canonical_claim_plan_fails_closed_when_required_source_has_no_candidate() -> None:
    sources = _sources()
    sources[-1]["evidenceCandidates"] = []

    with pytest.raises(CanonicalClaimPlanError) as captured:
        build_canonical_claim_plan(
            question="Compare all required sources.",
            sources=sources,
            required_source_keys=["S1", "S2", "S3", "S4"],
            required_facet_ids=["facet-1", "facet-2", "facet-3", "facet-4"],
            minimum_source_count=4,
            minimum_claim_count=4,
            target_claim_count=8,
        )

    assert captured.value.code == "canonical_claim_plan_incomplete"
    assert captured.value.diagnostics["missingSourceKeys"] == ["S4"]
    assert captured.value.diagnostics["missingFacetIds"] == ["facet-4"]


def test_canonical_claim_plan_keeps_verified_minimum_when_one_target_facet_is_missing() -> None:
    sources = _sources()
    sources[-1]["evidenceCandidates"] = []

    plan = build_canonical_claim_plan(
        question="Compare all available requirements and identify any unresolved scope.",
        sources=sources,
        required_source_keys=["S1", "S2", "S3", "S4"],
        required_facet_ids=["facet-1", "facet-2", "facet-3", "facet-4"],
        minimum_source_count=3,
        minimum_claim_count=4,
        target_claim_count=8,
        allow_supported_scope=True,
    )

    diagnostics = plan["canonicalClaimPlan"]
    assert diagnostics["minimumFloorMet"] is True
    assert diagnostics["coverageComplete"] is False
    assert diagnostics["supportedScopeLimited"] is True
    assert diagnostics["missingSourceKeys"] == ["S4"]
    assert diagnostics["missingFacetIds"] == ["facet-4"]
    assert plan["blockedFacets"] == [
        {"facetId": "facet-4", "goal": "Verify requirement 4"}
    ]
    assert len(plan["claimTable"]) >= 4
    assert {
        source["citationKey"]
        for claim in plan["claimTable"]
        for source in claim["supportingSources"]
    } == {"S1", "S2", "S3"}
    limitation = supported_scope_limitation_markdown(
        plan,
        preferred_language="zh-CN",
    )
    assert "本轮证据限制" in limitation
    assert "Verify requirement 4" in limitation
    assert "S4" not in limitation


def test_canonical_claim_plan_rejects_candidate_not_contiguous_in_source_text() -> None:
    sources = _sources()
    sources[0]["evidenceCandidates"] = [
        {
            **sources[0]["evidenceCandidates"][0],
            "text": (
                "The official architecture record establishes its distinct operational requirement "
                "and a sentence from a separate truncated window."
            ),
        }
    ]

    with pytest.raises(CanonicalClaimPlanError) as captured:
        build_canonical_claim_plan(
            question="Verify the architecture requirement.",
            sources=sources,
            required_source_keys=["S1"],
            required_facet_ids=["facet-1"],
            minimum_source_count=1,
            minimum_claim_count=1,
            target_claim_count=1,
        )

    assert captured.value.code == "canonical_claim_plan_incomplete"
    assert captured.value.diagnostics["missingSourceKeys"] == ["S1"]
