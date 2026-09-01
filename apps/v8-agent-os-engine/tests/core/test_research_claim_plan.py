from __future__ import annotations

import copy

import pytest

from core.tools.research_claim_plan import (
    CANONICAL_CLAIM_PLAN_VERSION,
    CanonicalClaimPlanError,
    apply_structure_projection,
    build_canonical_claim_plan,
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
