from __future__ import annotations

import copy
import hashlib

from core.tools.research_quality import (
    build_research_review_binding,
    research_acceptance_issues,
    research_bundle_is_accepted,
)


def reviewed_answer(*, partial: bool = False) -> dict:
    text = "Version 2 retains the existing on-disk format."
    source = {
        "sourceId": "format-spec",
        "citationKey": "S1",
        "url": "https://example.org/spec/v2",
        "title": "Format specification v2",
        "selectedForEvidence": True,
        "retrievedAt": "2026-09-05T08:00:00Z",
        "publishedAt": "2021-01-01",
        "contentChars": len(text),
        "readEvidence": {
            "verified": True,
            "contentChars": len(text),
            "contentSha256": hashlib.sha256(text.encode()).hexdigest(),
            "retrievedAt": "2026-09-05T08:00:00Z",
        },
    }
    claim = {
        "claimId": "format-evidence",
        "claim": text,
        "claimType": "source_excerpt",
        "supportingSources": [source],
        "evidenceExcerptKey": "S1:0:48",
        "evidenceExcerpt": text,
        "evidenceExcerptSha256": hashlib.sha256(text.lower().encode()).hexdigest(),
        "evidenceVerified": True,
    }
    result = {
        "question": "Does version 2 change the file format?",
        "answer": "No. Version 2 retains the existing on-disk format. [S1]",
        "sourceUrls": [source],
        "sourceMatrix": [source],
        "claimTable": [claim],
        "asOf": "2026-09-05T08:00:00Z",
        "reviewDecision": "accept",
        "deliveryScope": "partial" if partial else "complete",
        "limitations": ["Migration tooling has not been verified."] if partial else [],
    }
    review = {
        "reviewContract": "research-agent-review.v1",
        "reviewDecision": "accept",
        "questionCoverage": not partial,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "deliveryScope": result["deliveryScope"],
        "limitations": result["limitations"],
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    result["independentReview"] = review
    review.update(build_research_review_binding(
        result, reviewer_model_id="test-reviewer", reviewed_at="2026-09-05T08:01:00Z",
    ))
    return result


def test_one_short_old_but_applicable_source_can_answer_a_question():
    result = reviewed_answer()
    assert research_acceptance_issues(result) == []
    assert research_bundle_is_accepted(result)


def test_more_irrelevant_sources_are_not_needed_to_accept_a_reviewed_answer():
    result = reviewed_answer()
    result["deliveryRequirements"] = {
        "minimumSources": 4, "targetSources": 8, "minimumClaims": 5,
        "minimumDistinctHosts": 3, "targetAnswerChars": 5000,
    }
    assert research_acceptance_issues(result) == []


def test_mutating_the_reviewed_answer_does_not_inherit_acceptance():
    result = reviewed_answer()
    result["answer"] = "Version 2 destroys all existing data. [S1]"
    assert not research_bundle_is_accepted(result)


def test_partial_answer_is_not_mislabeled_as_complete():
    assert not research_bundle_is_accepted(reviewed_answer(partial=True))


def test_review_coverage_cannot_be_upgraded_without_rebinding():
    result = reviewed_answer(partial=True)
    result["deliveryScope"] = "complete"
    result["limitations"] = []
    result["independentReview"].update(questionCoverage=True, deliveryScope="complete", limitations=[])
    assert not research_bundle_is_accepted(result)


def test_missing_read_receipt_is_not_repaired_by_a_model_acceptance():
    result = copy.deepcopy(reviewed_answer())
    result["sourceMatrix"][0]["readEvidence"] = {}
    assert not research_bundle_is_accepted(result)


def test_generation_failure_does_not_invent_semantic_or_acquisition_defects():
    result = {"researchContract": "agent-research.v1", "answer": "", "deliveryScope": "none",
              "reviewDecision": "retry", "modelSynthesis": {"fallbackReason": "research_model_response_incomplete"}}
    assert research_acceptance_issues(result) == ["research_model_response_incomplete", "research_answer_missing"]
