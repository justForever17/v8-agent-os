from __future__ import annotations

import hashlib

from core.tools.research_quality import (
    MIN_RESEARCH_ANSWER_CHARS,
    MIN_RESEARCH_SOURCE_COUNT,
    TARGET_RESEARCH_ANSWER_CHARS,
    TARGET_RESEARCH_SOURCE_COUNT,
    _source_supports_current_claim,
    build_research_review_binding,
    research_acceptance_issues,
    research_acceptance_metrics,
    research_bundle_is_accepted,
    research_bundle_is_high_quality,
    research_high_quality_issues,
    research_quality_tier,
    research_selected_sources,
    research_source_has_dated_evidence,
)


def _detailed_answer(citations: str) -> str:
    subjects = ("定义", "范围", "机制", "数据", "方法", "案例", "差异", "风险", "限制", "决策")
    aspects = ("事实基础", "来源一致性", "时间边界", "适用条件", "反例检验", "因果解释", "执行影响", "后续验证")
    paragraphs = [
        (
            f"在{subject}的{aspect}方面，已读取资料共同给出可核验事实，同时区分直接观察、来源解释和综合判断。"
            f"这一部分具体说明{subject}如何受{aspect}约束、哪些条件会改变结论、相反证据应如何处理，以及用户据此能够采取什么行动。"
        )
        for subject in subjects
        for aspect in aspects
    ]
    citation_keys = citations.split()
    paragraphs = [
        f"{paragraph.rstrip('。.')} {citation_keys[index % len(citation_keys)]}。"
        for index, paragraph in enumerate(paragraphs)
    ]
    return f"截至 2026-07-28，以下结论来自已读取且可追溯的资料。{citations}\n\n" + "\n\n".join(paragraphs)


def _accepted_payload(*, current: bool = True, source_count: int = MIN_RESEARCH_SOURCE_COUNT) -> dict:
    claim_topics = (
        "定义范围",
        "架构机制",
        "来源权威",
        "数据证据",
        "时效变化",
        "冲突反例",
        "风险限制",
        "决策影响",
    )
    sources = [
        {
            "sourceId": f"src_{index}",
            "citationKey": f"S{index}",
            "title": f"Authoritative source {index}",
            "url": f"https://source{index}.example/research",
            "host": f"source{index}.example",
            "selectedForEvidence": True,
            "retrievedAt": "2026-07-28T12:00:00Z",
            "publishedAt": f"2026-07-{20 + index:02d}T00:00:00Z",
            "contentChars": 6000,
            "readEvidence": {
                "verified": True,
                "contentChars": 6000,
                "contentSha256": "a" * 64,
                "retrievedAt": "2026-07-28T12:00:00Z",
            },
        }
        for index in range(1, source_count + 1)
    ]
    citations = " ".join(f"[S{index}]" for index in range(1, source_count + 1))
    answer = _detailed_answer(citations)
    claims = []
    for index in range(1, source_count + 1):
        excerpt = (
            f"Authoritative source {index} records a concrete fact, its operating condition, "
            "and the evidence boundary used for this research conclusion."
        )
        claims.append({
            "claim": f"{claim_topics[index - 1]}结论具备足够具体的事实内容、适用条件和证据边界。",
            "claimType": "source_fact",
            "supportingSources": [
                {
                    "sourceId": f"src_{index}",
                    "citationKey": f"S{index}",
                    "url": f"https://source{index}.example/research",
                }
            ],
            "confidence": "high",
            "evidenceExcerptKey": f"S{index}:E1",
            "evidenceExcerpt": excerpt,
            "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
            "evidenceVerified": True,
        })
    payload = {
        "ok": True,
        "question": "截至目前，这项技术的最新实践是什么？" if current else "解释这项技术的基本原理。",
        "freshness": "current" if current else "timeless",
        "asOf": "2026-07-28T12:00:00Z",
        "reviewDecision": "accept",
        "independentReview": {
            "reviewDecision": "accept",
            "reviewReasons": ["The answer covers the question and every claim is entailed by its verified excerpt."],
            "questionCoverage": True,
            "claimEntailment": True,
            "freshnessAdequacy": True,
            "unsupportedClaims": [],
        },
        "researchAnswerPack": {
            "reviewDecision": "accept",
            "answer": answer,
            "sources": sources,
            "claimTable": claims,
            "asOf": "2026-07-28T12:00:00Z",
            "criticalMissingEvidence": [],
        },
    }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "quality-test-reviewer"),
        ("adversarial", "quality-test-adversarial-reviewer"),
    ):
        review = {**payload["independentReview"], "reviewMode": review_mode}
        review.update(
            build_research_review_binding(
                payload,
                reviewer_model_id=reviewer_model_id,
                reviewed_at="2026-07-28T12:00:00Z",
            )
        )
        consensus_reviews.append(review)
    payload["independentReview"] = {
        **consensus_reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": len(consensus_reviews),
        "consensusReviewerModelIds": [
            review["reviewerModelId"] for review in consensus_reviews
        ],
        "consensusReviews": consensus_reviews,
    }
    return payload


def _rebind_review_consensus(payload: dict) -> None:
    independent_review = payload["independentReview"]
    consensus_reviews = independent_review["consensusReviews"]
    for review in consensus_reviews:
        review.update(
            build_research_review_binding(
                payload,
                reviewer_model_id=review["reviewerModelId"],
                reviewed_at=review["reviewedAt"],
            )
        )
    independent_review.update(consensus_reviews[0])


def test_research_quality_accepts_detailed_current_five_source_answer() -> None:
    payload = _accepted_payload()

    assert research_bundle_is_accepted(payload)
    assert research_acceptance_issues(payload) == []
    metrics = research_acceptance_metrics(payload)
    assert metrics["effectiveAnswerChars"] >= MIN_RESEARCH_ANSWER_CHARS
    assert metrics["selectedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["distinctHostCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["retrievedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["readVerifiedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["datedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["supportedClaimCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["evidenceVerifiedClaimCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["claimSupportedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["answerCitedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["answerCitedContentUnitCount"] >= MIN_RESEARCH_SOURCE_COUNT


def test_research_quality_rejects_incomplete_structured_brief_coverage() -> None:
    payload = _accepted_payload()
    payload["briefCoverageRequired"] = True
    payload["briefCoverageComplete"] = False
    payload["briefCoverage"] = [
        {"taskBriefId": "covered", "status": "supported"},
        {"taskBriefId": "missing", "status": "evidence_only"},
    ]

    issues = research_acceptance_issues(payload)
    metrics = research_acceptance_metrics(payload)

    assert "research_brief_coverage_incomplete" in issues
    assert metrics["coveredTaskBriefCount"] == 1
    assert metrics["missingTaskBriefCount"] == 1


def test_research_quality_rejects_a_legacy_single_reviewer_pack() -> None:
    payload = _accepted_payload()
    payload["independentReview"] = dict(payload["independentReview"]["consensusReviews"][0])

    issues = research_acceptance_issues(payload)

    assert "independent_semantic_review_not_accepted" in issues


def test_research_quality_does_not_count_rejected_or_unread_sources() -> None:
    payload = _accepted_payload()
    payload["researchAnswerPack"]["sources"][0]["selectedForEvidence"] = False
    payload["researchAnswerPack"]["sources"][0]["sourceQualityGate"] = {"selectedForEvidence": False}

    selected = research_selected_sources(payload)
    issues = research_acceptance_issues(payload)

    assert len(selected) == MIN_RESEARCH_SOURCE_COUNT - 1
    assert f"evidence_source_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues
    assert f"retrieval_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_uses_reviewed_final_sources_without_unioning_diagnostic_candidates() -> None:
    payload = _accepted_payload()
    canonical_sources = [dict(source) for source in payload["researchAnswerPack"]["sources"]]
    payload["finalExperiencePack"] = {
        "answer": payload["researchAnswerPack"]["answer"],
        "researchResult": payload["researchAnswerPack"]["answer"],
        "sourceUrls": canonical_sources,
        "claimTable": payload["researchAnswerPack"]["claimTable"],
        "asOf": payload["researchAnswerPack"]["asOf"],
    }
    payload["researchEvidenceBank"] = {
        "selectedSources": [
            {
                **dict(source),
                "url": f"https://diagnostic-{index}.example/unreviewed",
                "sourceId": f"diagnostic-{index}",
            }
            for index, source in enumerate(canonical_sources, start=1)
        ]
    }
    payload["sourceMatrix"] = [
        {
            **dict(canonical_sources[0]),
            "url": "https://matrix-extra.example/unreviewed",
            "sourceId": "matrix-extra",
        }
    ]

    selected = research_selected_sources(payload)

    assert [source["url"] for source in selected] == [source["url"] for source in canonical_sources]


def test_research_quality_rejects_short_or_uncited_answer() -> None:
    payload = _accepted_payload()
    payload["researchAnswerPack"]["answer"] = "这是一个很短、没有来源编号的答案。"

    issues = research_acceptance_issues(payload)

    assert f"detailed_answer_floor_not_met:{MIN_RESEARCH_ANSWER_CHARS}" in issues
    assert f"answer_citation_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_rejects_citation_list_without_inline_evidence_spread() -> None:
    payload = _accepted_payload()
    citation_keys = [f"[S{index}]" for index in range(1, MIN_RESEARCH_SOURCE_COUNT + 1)]
    answer = payload["researchAnswerPack"]["answer"]
    for key in citation_keys:
        answer = answer.replace(key, "")
    payload["researchAnswerPack"]["answer"] = " ".join(citation_keys) + "\n" + answer

    metrics = research_acceptance_metrics(payload)
    issues = research_acceptance_issues(payload)

    assert metrics["answerCitedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT
    assert metrics["answerCitedContentUnitCount"] < MIN_RESEARCH_SOURCE_COUNT
    assert f"answer_citation_spread_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_does_not_count_runtime_source_appendix_as_body_citations() -> None:
    payload = _accepted_payload()
    answer = payload["researchAnswerPack"]["answer"]
    for index in range(1, MIN_RESEARCH_SOURCE_COUNT + 1):
        answer = answer.replace(f"[S{index}]", "")
    appendix = "\n".join(
        [
            "## Sources",
            *[
                f"- [S{index}] Authoritative source {index} - https://source{index}.example/research"
                for index in range(1, MIN_RESEARCH_SOURCE_COUNT + 1)
            ],
        ]
    )
    payload["researchAnswerPack"]["answer"] = f"{answer}\n\n{appendix}"

    metrics = research_acceptance_metrics(payload)
    issues = research_acceptance_issues(payload)

    assert metrics["answerCitedSourceCount"] == 0
    assert metrics["answerCitedContentUnitCount"] == 0
    assert f"answer_citation_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues
    assert f"answer_citation_spread_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_does_not_count_repeated_padding_as_effective_content() -> None:
    payload = _accepted_payload()
    citations = " ".join(f"[S{index}]" for index in range(1, MIN_RESEARCH_SOURCE_COUNT + 1))
    payload["researchAnswerPack"]["answer"] = citations + ("同一句话反复出现不能增加研究答案的有效内容。" * 300)

    metrics = research_acceptance_metrics(payload)
    issues = research_acceptance_issues(payload)

    assert metrics["rawAnswerChars"] >= MIN_RESEARCH_ANSWER_CHARS
    assert metrics["effectiveAnswerChars"] < MIN_RESEARCH_ANSWER_CHARS
    assert metrics["uniqueContentRatio"] < 0.7
    assert "answer_repetition_excessive" in issues


def test_research_quality_does_not_count_near_duplicate_suffix_padding() -> None:
    payload = _accepted_payload()
    citations = " ".join(f"[S{index}]" for index in range(1, MIN_RESEARCH_SOURCE_COUNT + 1))
    repeated = [
        "同一段填充内容即使追加不同字母也没有新事实、条件、反例、时效证据或行动价值"
        + chr(97 + (index // 26))
        + chr(97 + (index % 26))
        + "。"
        for index in range(120)
    ]
    payload["researchAnswerPack"]["answer"] = citations + "\n" + "\n".join(repeated)

    metrics = research_acceptance_metrics(payload)
    issues = research_acceptance_issues(payload)

    assert metrics["rawAnswerChars"] >= MIN_RESEARCH_ANSWER_CHARS
    assert metrics["effectiveAnswerChars"] < MIN_RESEARCH_ANSWER_CHARS
    assert "answer_repetition_excessive" in issues


def test_research_quality_requires_full_body_read_evidence_for_each_source() -> None:
    payload = _accepted_payload()
    payload["researchAnswerPack"]["sources"][0].pop("contentChars")

    metrics = research_acceptance_metrics(payload)
    issues = research_acceptance_issues(payload)

    assert metrics["readVerifiedSourceCount"] == MIN_RESEARCH_SOURCE_COUNT - 1
    assert f"read_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_deduplicates_url_fragments_as_one_document() -> None:
    payload = _accepted_payload(source_count=TARGET_RESEARCH_SOURCE_COUNT)
    first = payload["researchAnswerPack"]["sources"][0]
    duplicate = payload["researchAnswerPack"]["sources"][1]
    duplicate["url"] = first["url"] + "#another-section"

    selected = research_selected_sources(payload)
    issues = research_high_quality_issues(payload)

    assert len(selected) == TARGET_RESEARCH_SOURCE_COUNT - 1
    assert f"target_source_count_not_met:{TARGET_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_deduplicates_python_version_and_locale_variants() -> None:
    payload = _accepted_payload(source_count=TARGET_RESEARCH_SOURCE_COUNT)
    variants = (
        "https://docs.python.org/3/library/pathlib.html",
        "https://docs.python.org/3.14/library/pathlib.html",
        "https://docs.python.org/3.13/library/pathlib.html",
        "https://docs.python.org/3.11/library/pathlib.html",
        "https://docs.python.org/fr/3/library/pathlib.html",
        "https://docs.python.org/uk/3/library/pathlib.html",
        "https://docs.python.org/zh-cn/3/library/pathlib.html",
        "https://docs.python.org/3/library/pathlib.html?highlight=resolve",
    )
    for source, url in zip(payload["researchAnswerPack"]["sources"], variants):
        source["url"] = url

    selected = research_selected_sources(payload)
    issues = research_high_quality_issues(payload)

    assert len(selected) == 1
    assert f"target_source_count_not_met:{TARGET_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_lets_reviewer_judge_undated_sources_with_clear_retrieval_time() -> None:
    timeless = _accepted_payload(current=False)
    for source in timeless["researchAnswerPack"]["sources"]:
        source.pop("publishedAt")
    _rebind_review_consensus(timeless)
    assert research_bundle_is_accepted(timeless)

    current = _accepted_payload(current=True)
    for source in current["researchAnswerPack"]["sources"]:
        source.pop("publishedAt")
    _rebind_review_consensus(current)
    assert research_acceptance_metrics(current)["datedSourceCount"] == 0
    assert research_bundle_is_accepted(current)


def test_research_quality_exposes_time_context_and_leaves_currency_to_reviewer() -> None:
    payload = _accepted_payload(current=True)
    claim = payload["researchAnswerPack"]["claimTable"][0]
    claim["claim"] = "截至目前，这项技术的最新支持状态已有明确结论。"
    source = payload["researchAnswerPack"]["sources"][0]
    source.pop("publishedAt")
    _rebind_review_consensus(payload)

    metrics = research_acceptance_metrics(payload)
    assert metrics["timeSensitiveClaimCount"] == 1
    assert metrics["temporallySupportedTimeSensitiveClaimCount"] == 0
    assert metrics["temporallyContextualizedTimeSensitiveClaimCount"] == 1
    assert "time_sensitive_claim_without_temporal_context" not in research_acceptance_issues(payload)

    source["publishedAt"] = "2017-01-01"
    source["temporalEvidence"] = {
        "status": "current_applicable",
        "applicabilityBasis": "stable_current_primary_route",
    }
    _rebind_review_consensus(payload)

    metrics = research_acceptance_metrics(payload)
    assert metrics["temporallySupportedTimeSensitiveClaimCount"] == 1
    assert metrics["temporallyContextualizedTimeSensitiveClaimCount"] == 1
    assert "time_sensitive_claim_without_temporal_context" not in research_acceptance_issues(payload)


def test_attributed_secondary_relative_time_is_not_a_runtime_current_claim() -> None:
    payload = _accepted_payload(current=True)
    claim = payload["researchAnswerPack"]["claimTable"][0]
    claim["claim"] = (
        'Secondary source "Field notes" states: I recently published an article about pathlib.'
    )
    claim["supportingSources"][0]["tier"] = "secondary"
    source = payload["researchAnswerPack"]["sources"][0]
    source["tier"] = "secondary"
    source.pop("publishedAt")
    _rebind_review_consensus(payload)

    metrics = research_acceptance_metrics(payload)
    assert metrics["timeSensitiveClaimCount"] == 0
    assert metrics["temporallySupportedTimeSensitiveClaimCount"] == 0
    assert "time_sensitive_claim_without_temporal_context" not in research_acceptance_issues(payload)


def test_runtime_current_user_phrase_is_not_a_document_freshness_claim() -> None:
    payload = _accepted_payload(current=True)
    claim = payload["researchAnswerPack"]["claimTable"][0]
    claim["claim"] = (
        "The application directory stores configuration for the current user "
        "on each operating system."
    )
    source = payload["researchAnswerPack"]["sources"][0]
    source.pop("publishedAt")
    _rebind_review_consensus(payload)

    metrics = research_acceptance_metrics(payload)
    assert metrics["timeSensitiveClaimCount"] == 0
    assert metrics["temporallySupportedTimeSensitiveClaimCount"] == 0
    assert "time_sensitive_claim_without_temporal_context" not in research_acceptance_issues(payload)


def test_research_quality_rejects_malformed_temporal_and_read_receipts() -> None:
    payload = _accepted_payload(current=True)
    payload["asOf"] = "not-a-date"
    payload["researchAnswerPack"]["asOf"] = "not-a-date"
    for source in payload["researchAnswerPack"]["sources"]:
        source["retrievedAt"] = "not-a-date"
        source["publishedAt"] = "also-not-a-date"
        source["readEvidence"]["retrievedAt"] = "not-a-date"

    issues = research_acceptance_issues(payload)

    assert f"retrieval_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues
    assert f"read_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues
    assert "research_as_of_invalid" in issues


def test_research_quality_rejects_future_evidence_but_leaves_visible_old_evidence_to_reviewer() -> None:
    future = _accepted_payload(current=True)
    future["asOf"] = "2099-01-01T00:00:00Z"
    future["researchAnswerPack"]["asOf"] = "2099-01-01T00:00:00Z"
    for source in future["researchAnswerPack"]["sources"]:
        source["retrievedAt"] = "2099-01-01T00:00:00Z"
        source["publishedAt"] = "2099-01-01T00:00:00Z"
        source["readEvidence"]["retrievedAt"] = "2099-01-01T00:00:00Z"
    future_issues = research_acceptance_issues(future)

    stale = _accepted_payload(current=True)
    stale["asOf"] = "2025-01-01T00:00:00Z"
    stale["researchAnswerPack"]["asOf"] = "2025-01-01T00:00:00Z"
    for source in stale["researchAnswerPack"]["sources"]:
        source["retrievedAt"] = "2025-01-01T00:00:00Z"
        source["readEvidence"]["retrievedAt"] = "2025-01-01T00:00:00Z"
    _rebind_review_consensus(stale)
    stale_issues = research_acceptance_issues(stale)

    assert "research_as_of_invalid" in future_issues
    assert f"retrieval_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in future_issues
    assert f"read_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in future_issues
    assert "research_as_of_stale:7" not in stale_issues
    assert f"fresh_retrieval_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" not in stale_issues
    assert research_acceptance_metrics(stale)["asOfCurrent"] is False
    assert research_acceptance_metrics(stale)["freshRetrievedSourceCount"] == 0
    assert research_bundle_is_accepted(stale)


def test_research_quality_requires_independent_review_and_valid_excerpt_digest() -> None:
    missing_review = _accepted_payload()
    missing_review.pop("independentReview")
    assert "independent_semantic_review_not_accepted" in research_acceptance_issues(missing_review)

    forged_excerpt = _accepted_payload()
    forged_excerpt["researchAnswerPack"]["claimTable"][0]["evidenceExcerptSha256"] = "f" * 64
    assert "unverified_claim_excerpt_present" in research_acceptance_issues(forged_excerpt)

    rebound_to_new_question = _accepted_payload()
    rebound_to_new_question["question"] = "这个答案能否覆盖一个完全不同的安全风险问题？"
    assert "independent_semantic_review_not_accepted" in research_acceptance_issues(rebound_to_new_question)

    rebound_to_new_time = _accepted_payload()
    rebound_to_new_time["asOf"] = "2025-01-01T00:00:00Z"
    rebound_to_new_time["researchAnswerPack"]["asOf"] = "2025-01-01T00:00:00Z"
    assert "independent_semantic_review_not_accepted" in research_acceptance_issues(rebound_to_new_time)


def test_research_review_binding_rejects_citation_key_and_normative_cue_drift() -> None:
    citation_drift = _accepted_payload()
    first, second = citation_drift["researchAnswerPack"]["sources"][:2]
    first["citationKey"], second["citationKey"] = second["citationKey"], first["citationKey"]
    assert "independent_semantic_review_not_accepted" in research_acceptance_issues(citation_drift)

    normative_drift = _accepted_payload()
    claim = normative_drift["researchAnswerPack"]["claimTable"][0]
    claim["claimType"] = "explicit_normative"
    claim["normativeCue"] = "records a concrete fact"
    _rebind_review_consensus(normative_drift)
    assert research_bundle_is_accepted(normative_drift)
    claim["normativeCue"] = "a different normative cue"
    assert "independent_semantic_review_not_accepted" in research_acceptance_issues(normative_drift)


def test_research_review_binding_is_stable_when_temporal_fields_are_compacted() -> None:
    payload = _accepted_payload()
    before = build_research_review_binding(
        payload,
        reviewer_model_id="quality-test-reviewer",
        reviewed_at="2026-07-28T12:00:00Z",
    )
    for source in payload["researchAnswerPack"]["sources"]:
        source["temporalEvidence"] = {
            "retrievedAt": source.pop("retrievedAt"),
            "publishedAt": source.pop("publishedAt"),
        }
    after = build_research_review_binding(
        payload,
        reviewer_model_id="quality-test-reviewer",
        reviewed_at="2026-07-28T12:00:00Z",
    )

    assert before == after
    assert before["bindingVersion"] == 6

    payload["researchAnswerPack"]["sources"][0]["temporalEvidence"]["applicableVersion"] = "3.12"
    with_applicability = build_research_review_binding(
        payload,
        reviewer_model_id="quality-test-reviewer",
        reviewed_at="2026-07-28T12:00:00Z",
    )
    assert with_applicability["temporalDigest"] != after["temporalDigest"]


def test_research_quality_parses_labeled_english_document_dates() -> None:
    assert research_source_has_dated_evidence({"publishedAt": "January 11, 2025"})
    assert research_source_has_dated_evidence({"updatedAt": "Jun 15, 2026"})
    assert research_source_has_dated_evidence({"updatedAt": "1696032739"})


def test_research_quality_does_not_treat_observation_time_as_a_document_date() -> None:
    for kind in ("retrieved_at", "crawled_at", "indexed_at"):
        assert not research_source_has_dated_evidence(
            {
                "retrievedAt": "2026-07-29T12:00:00Z",
                "sourceDate": "2026-07-29",
                "sourceDateKind": kind,
                "temporalEvidence": {
                    "sourceDate": "2026-07-29",
                    "sourceDateKind": kind,
                },
            }
        )
    assert not research_source_has_dated_evidence({"publishedAt": "2999-01-01"})


def test_current_applicability_accepts_primary_stable_route_but_not_secondary_current_url() -> None:
    assert _source_supports_current_claim(
        {
            "tier": "primary",
            "url": "https://official.example/stable/path-api/",
            "publishedAt": "2017-01-01",
            "retrievedAt": "2026-07-29T12:00:00Z",
        }
    )
    assert not _source_supports_current_claim(
        {
            "tier": "secondary",
            "url": "https://community.example/current/path-notes/",
            "retrievedAt": "2026-07-29T12:00:00Z",
        }
    )


def test_document_age_and_version_are_context_for_reviewer_not_current_proof() -> None:
    for published_at in ("2026-07-01", "2021-07-01", "2011-07-01"):
        assert not _source_supports_current_claim(
            {
                "tier": "primary",
                "url": "https://archive.example/path-api",
                "publishedAt": published_at,
                "retrievedAt": "2026-07-29T12:00:00Z",
            }
        )
    assert not _source_supports_current_claim(
        {
            "tier": "primary",
            "url": "https://archive.example/path-api-v3",
            "version": "3.14.6",
            "retrievedAt": "2026-07-29T12:00:00Z",
        }
    )
    assert _source_supports_current_claim(
        {
            "tier": "primary",
            "url": "https://archive.example/path-api",
            "temporalEvidence": {
                "status": "current_applicable",
                "applicabilityBasis": "explicit_current_applicability",
            },
        }
    )


def test_research_quality_exposes_old_localized_current_context_without_fixed_age_rejection() -> None:
    payload = _accepted_payload(current=False)
    payload["freshness"] = "实时"
    payload["asOf"] = "2025-01-01T00:00:00Z"
    payload["researchAnswerPack"]["asOf"] = "2025-01-01T00:00:00Z"
    for source in payload["researchAnswerPack"]["sources"]:
        source["retrievedAt"] = "2025-01-01T00:00:00Z"
        source["readEvidence"]["retrievedAt"] = "2025-01-01T00:00:00Z"
    _rebind_review_consensus(payload)

    issues = research_acceptance_issues(payload)

    assert "research_as_of_stale:7" not in issues
    assert f"fresh_retrieval_evidence_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" not in issues
    metrics = research_acceptance_metrics(payload)
    assert metrics["requiresDatedSources"] is True
    assert metrics["asOfCurrent"] is False
    assert metrics["freshRetrievedSourceCount"] == 0
    assert research_bundle_is_accepted(payload)


def test_research_quality_accepts_an_older_bound_review_when_timestamps_remain_visible() -> None:
    payload = _accepted_payload(current=True)
    consensus_reviews = payload["independentReview"]["consensusReviews"]
    for review in consensus_reviews:
        review.update(
            build_research_review_binding(
                payload,
                reviewer_model_id=review["reviewerModelId"],
                reviewed_at="2024-01-15T12:00:00Z",
            )
        )
    payload["independentReview"] = {
        **consensus_reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": len(consensus_reviews),
        "consensusReviewerModelIds": [
            review["reviewerModelId"] for review in consensus_reviews
        ],
        "consensusReviews": consensus_reviews,
    }

    assert research_bundle_is_accepted(payload)


def test_research_quality_treats_current_year_question_as_time_sensitive() -> None:
    payload = _accepted_payload(current=False)
    payload["question"] = "请总结 2026 年这项技术的适用范围。"
    payload["freshness"] = "auto"

    assert research_acceptance_metrics(payload)["requiresDatedSources"] is True


def test_research_quality_requires_every_claim_to_have_known_support() -> None:
    payload = _accepted_payload()
    payload["researchAnswerPack"]["claimTable"][2]["supportingSources"] = [
        {"sourceId": "src_unknown", "url": "https://unknown.example/research"}
    ]

    issues = research_acceptance_issues(payload)

    assert "unsupported_claim_present" in issues


def test_research_quality_rejects_numbered_duplicates_as_distinct_claims() -> None:
    payload = _accepted_payload()
    for index, claim in enumerate(payload["researchAnswerPack"]["claimTable"], start=1):
        claim["claim"] = f"相同结论 {index} 仅改变编号和后缀，事实内容、适用条件和证据边界完全一致{chr(96 + index)}。"

    metrics = research_acceptance_metrics(payload)
    issues = research_acceptance_issues(payload)

    assert metrics["uniqueClaimCount"] == 1
    assert f"distinct_claim_floor_not_met:{MIN_RESEARCH_SOURCE_COUNT}" in issues


def test_research_quality_distinguishes_minimum_from_high_quality() -> None:
    minimum = _accepted_payload()
    assert research_bundle_is_accepted(minimum)
    assert not research_bundle_is_high_quality(minimum)
    assert research_quality_tier(minimum) == "minimum_qualified"
    assert f"target_source_count_not_met:{TARGET_RESEARCH_SOURCE_COUNT}" in research_high_quality_issues(minimum)

    high_quality = _accepted_payload(source_count=TARGET_RESEARCH_SOURCE_COUNT)
    assert research_acceptance_metrics(high_quality)["effectiveAnswerChars"] >= TARGET_RESEARCH_ANSWER_CHARS
    assert research_bundle_is_high_quality(high_quality)
    assert research_quality_tier(high_quality) == "high_quality"
