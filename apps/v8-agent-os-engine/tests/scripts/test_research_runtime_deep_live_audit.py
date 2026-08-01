from __future__ import annotations

import copy
import hashlib
import json

from core.tools.research_quality import build_research_review_binding
from tests.scripts import run_research_runtime_deep_live_audit as audit


def _technical_bundle() -> dict:
    reviewed_at = "2026-07-29T04:00:00Z"
    sources = []
    claims = []
    for index in range(1, 9):
        source_id = f"src_{index}"
        url = f"https://source{index}.example/pathlib"
        source_text = f"Verified source body {index} with enough concrete pathlib guidance."
        sources.append(
            {
                "sourceId": source_id,
                "citationKey": f"S{index}",
                "url": url,
                "host": f"source{index}.example",
                "selectedForEvidence": True,
                "sourceQualityGate": {"selectedForEvidence": True},
                "contentChars": 500,
                "retrievedAt": reviewed_at,
                "readEvidence": {
                    "verified": True,
                    "contentChars": 500,
                    "contentSha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    "retrievedAt": reviewed_at,
                },
            }
        )
        excerpt = f"Source {index} states a concrete pathlib recommendation and its boundary."
        claims.append(
            {
                "claim": f"Pathlib practice {index} is supported by an independently read source.",
                "claimType": "source_fact",
                "supportingSources": [
                    {
                        "sourceId": source_id,
                        "citationKey": f"S{index}",
                        "url": url,
                    }
                ],
                "evidenceExcerptKey": f"S{index}:E1",
                "evidenceExcerpt": excerpt,
                "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
                "evidenceVerified": True,
            }
        )
    answer = "\n\n".join(
        f"## Practice {index}\nConcrete pathlib guidance with operating details and evidence [S{index}]."
        for index in range(1, 9)
    )
    model_synthesis = {
        "used": True,
        "agentId": "web-research-architect",
        "mode": "full_synthesis",
        "writerModelRole": "summary",
        "writerModelId": "fixture-reviewer",
        "reviewerModelRole": "summary",
        "reviewerModelId": "fixture-reviewer",
        "reviewerConsensusCount": 2,
        "reviewerConsensusModelIds": ["fixture-reviewer", "fixture-adversarial-reviewer"],
        "writerMode": "segmented",
        "writerSectionCount": 4,
        "writerRevisionCount": 0,
        "sameEvidenceReviewRejected": False,
    }
    final_pack = {
        "question": "Current pathlib CLI practices",
        "freshness": "current",
        "answer": answer,
        "researchResult": answer,
        "claimTable": copy.deepcopy(claims),
        "sourceUrls": copy.deepcopy(sources),
        "reviewDecision": "accept",
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "asOf": reviewed_at,
        "synthesisMode": "model_agent",
        "modelSynthesis": model_synthesis,
    }
    answer_pack = {
        "answer": answer,
        "sources": copy.deepcopy(sources),
        "claimTable": copy.deepcopy(claims),
        "reviewDecision": "accept",
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "asOf": reviewed_at,
    }
    bundle = {
        "question": "Current pathlib CLI practices",
        "freshness": "current",
        "answer": answer,
        "claimTable": copy.deepcopy(claims),
        "sourceMatrix": copy.deepcopy(sources),
        "sourceUrls": [source["url"] for source in sources],
        "researchAnswerPack": answer_pack,
        "finalExperiencePack": final_pack,
        "reviewDecision": "accept",
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "asOf": reviewed_at,
        "experienceReuse": {"reuseDecision": "refresh", "reason": "forced_live_validation"},
        "researchLoopState": {
            "phase": "research_loop",
            "rounds": [{"round": 1, "queries": ["pathlib current practices"], "readSourceCount": 8}],
        },
        "shards": [
            {
                "shardId": "shard_1",
                "kind": "official_docs",
                "query": "pathlib current practices",
                "ok": True,
                "provider": "fixture-search",
                "networkRoute": "global",
                "resultCount": 8,
                "fetchedTopSources": [{"url": source["url"], "ok": True} for source in sources],
            }
        ],
    }
    review_template = {
        "reviewDecision": "accept",
        "reviewReasons": ["The detailed answer is fully supported by the bound evidence."],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "fixture-reviewer"),
        ("adversarial", "fixture-adversarial-reviewer"),
    ):
        item = {**review_template, "reviewMode": review_mode}
        item.update(
            build_research_review_binding(
                bundle,
                reviewer_model_id=reviewer_model_id,
                reviewed_at=reviewed_at,
            )
        )
        consensus_reviews.append(item)
    review = {
        **consensus_reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": 2,
        "consensusReviewerModelIds": [item["reviewerModelId"] for item in consensus_reviews],
        "consensusReviews": consensus_reviews,
    }
    bundle["independentReview"] = copy.deepcopy(review)
    answer_pack["independentReview"] = copy.deepcopy(review)
    final_pack["independentReview"] = copy.deepcopy(review)
    return bundle


def test_high_quality_delivery_recomputes_instead_of_trusting_reported_metrics() -> None:
    fake_metrics = {"independentReviewAccepted": True, "selectedSourceCount": 99}
    payload = {
        "ok": True,
        "deliveryReady": True,
        "qualityTier": "high_quality",
        "qualityMetrics": fake_metrics,
        "reviewDecision": "accept",
        "researchAnswerPack": {
            "reviewDecision": "accept",
            "answer": "too short",
            "sources": [],
            "score": {
                "deliveryReady": True,
                "qualityTier": "high_quality",
                "acceptanceMetrics": fake_metrics,
            },
        },
    }

    failures = audit._high_quality_delivery_failures(payload)

    assert any(item.startswith("recomputed_quality_issue:") for item in failures)
    assert "answer_pack_acceptance_metrics_do_not_match_recomputed" in failures
    assert "top_level_quality_metrics_do_not_match_recomputed" in failures


def test_research_run_passes_force_refresh_to_broker(monkeypatch) -> None:
    from core.tools.research_broker import research_broker

    captured = {}

    def fake_func(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ok": True})

    monkeypatch.setattr(research_broker, "func", fake_func)

    assert audit._research_run("question", force_refresh=True) == {"ok": True}
    assert captured["forceRefresh"] is True


def test_pure_case_forces_a_fresh_research_run(monkeypatch) -> None:
    calls = []

    def fake_research_run(question, **kwargs):
        calls.append({"question": question, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(audit, "_research_run", fake_research_run)
    monkeypatch.setattr(audit, "_high_quality_delivery_failures", lambda _payload: [])
    monkeypatch.setattr(audit, "_pure_research_semantic_failures", lambda _payload: [])

    result = audit._run_pure_research_case()

    assert result.status == "ok"
    assert calls == [
        {
            "question": audit.PURE_RESEARCH_QUESTION,
            "freshness": "current",
            "max_shards": 20,
            "force_refresh": True,
        }
    ]


def test_pure_semantic_gate_requires_every_product_and_decision_coverage() -> None:
    answer = (
        "在 Windows 和 PowerShell 上，OpenAI Codex CLI、Claude Code、Gemini CLI 与 "
        "GitHub Copilot CLI 的安装方式和代码库工作流不同。OpenAI、Anthropic、Google "
        "和 GitHub 的官方资料分别说明了工具调用与 MCP 扩展、账号订阅和价格依赖、"
        "隐私边界与常见局限。综合这些证据，以下给出按团队情况划分的选型建议。"
    )

    assert audit._pure_research_semantic_failures({"answer": answer}) == []

    incomplete = audit._pure_research_semantic_failures(
        {"answer": "Windows 用户可以考虑 OpenAI Codex CLI，并查看官方资料。"}
    )

    assert any(item.startswith("pure_research_product_coverage_missing:") for item in incomplete)
    assert "pure_research_decision_coverage_below_target:1/6" in incomplete


def test_reuse_case_refreshes_first_then_allows_zero_network_reuse(monkeypatch) -> None:
    calls = []
    responses = iter(
        [
            {"ok": True, "experienceReuse": {"reuseDecision": "refresh"}},
            {"ok": True, "experienceReuse": {"reuseDecision": "reuse"}},
        ]
    )

    def fake_research_run(question, **kwargs):
        calls.append({"question": question, **kwargs})
        return next(responses)

    monkeypatch.setattr(audit, "_research_run", fake_research_run)
    monkeypatch.setattr(audit, "_high_quality_delivery_failures", lambda _payload: [])
    monkeypatch.setattr(audit, "_pure_research_semantic_failures", lambda _payload: [])
    monkeypatch.setattr(
        audit,
        "_compact_delivery_evidence",
        lambda _payload: {"answerSha256": "bound-answer", "sourceUrls": ["https://source.example"]},
    )

    result = audit._run_reuse_case()

    assert result.status == "ok"
    assert [call.get("force_refresh", False) for call in calls] == [True, False]
    assert all(call["question"] == audit.PURE_RESEARCH_QUESTION for call in calls)
    assert all(call["freshness"] == "current" for call in calls)
    assert all(call["max_shards"] == 20 for call in calls)


def test_technical_runtime_accepts_bound_segmented_fresh_bundle() -> None:
    bundle = _technical_bundle()

    assert audit._technical_runtime_failures({"experienceReuse": bundle["experienceReuse"]}, bundle) == []

    diagnostic = audit._persisted_research_diagnostic(
        {"evidenceBundleId": "research_fixture"},
        bundle=bundle,
    )
    synthesis = diagnostic["finalPack"]["modelSynthesis"]
    assert synthesis["writerMode"] == "segmented"
    assert synthesis["writerSectionCount"] == 4
    assert synthesis["writerRevisionCount"] == 0
    assert synthesis["sameEvidenceReviewRejected"] is False
    assert diagnostic["searchReceipts"][0]["provider"] == "fixture-search"
    assert len(diagnostic["readReceipts"]) == 8
    assert diagnostic["finalPack"]["independentReview"]["bindingVersion"] == 6


def test_technical_runtime_rejects_surface_and_review_binding_drift() -> None:
    bundle = _technical_bundle()
    bundle["researchAnswerPack"]["answer"] += " drift"
    bundle["finalExperiencePack"]["independentReview"]["answerSha256"] = "0" * 64

    failures = audit._technical_runtime_failures({}, bundle)

    assert "answer_surface_parity_mismatch" in failures
    assert "independent_review_surface_parity_mismatch" in failures
    assert "technical_independent_review_binding_mismatch" in failures
