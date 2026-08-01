from __future__ import annotations

import hashlib
import json

from core.native_tools import memory_broker
from core.tools.research_broker import research_broker
from core.tools.research_ledger import promote_experience_pack, store_evidence_bundle
from core.tools.research_quality import (
    build_research_review_binding,
    research_high_quality_issues,
)


class _FakeMemoryRuntime:
    def unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: str | None = None,
        scopes: list[str] | None = None,
    ):
        assert not scopes or (scope or "global") in scopes
        return [
            {
                "id": "mem-project-preference",
                "scope": scope or "global",
                "category": "project_preference",
                "confidence": 0.86,
                "text": f"Relevant memory for {query}: prefer compact source-backed evidence packs.",
            }
        ][:limit]


def test_memory_broker_catalog_lists_domains_without_raw_ledgers() -> None:
    payload = json.loads(memory_broker.func(mode="catalog", scope="E:/Projects/test7"))

    domains = {item["domain"] for item in payload["domains"]}
    assert payload["ok"] is True
    assert "memory_core" in domains
    assert "research_experience" in domains
    assert "workflow_memory" in domains
    assert "raw" not in json.dumps(payload, ensure_ascii=False).lower()


def test_memory_broker_route_returns_compact_evidence_pack(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.native_tools._get_memory_runtime", lambda: _FakeMemoryRuntime())
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    sources = [
        {
            "sourceId": f"march7-source-{index}",
            "citationKey": f"S{index}",
            "title": f"三月七角色资料 {index}",
            "url": f"https://march7-source-{index}.example/character",
            "host": f"march7-source-{index}.example",
            "selectedForEvidence": True,
            "retrievedAt": "2026-07-28T12:00:00Z",
            "publishedAt": f"2026-07-{10 + index:02d}T00:00:00Z",
            "contentChars": 6000,
            "readEvidence": {
                "verified": True,
                "contentChars": 6000,
                "contentSha256": "e" * 64,
                "retrievedAt": "2026-07-28T12:00:00Z",
            },
        }
        for index in range(1, 9)
    ]
    citations = " ".join(f"[S{index}]" for index in range(1, 9))
    subjects = ("身份", "经历", "关系", "语言", "行为", "动机", "冲突", "成长")
    aspects = ("官方事实", "剧情证据", "时间边界", "来源差异", "反例", "适用条件", "表达影响", "后续验证")
    paragraphs = [
            (
                f"在{subject}的{aspect}方面，资料给出了独立可核验的角色事实，并把原始剧情、官方解释和综合判断区分开来。"
                f"这一部分说明{subject}结论受哪些{aspect}条件约束、相反证据何时会改变判断，以及这些细节如何影响对角色表达和行为的理解。"
            )
            for subject in subjects
            for aspect in aspects
    ]
    paragraphs = [
        f"{paragraph.rstrip('。.')} [S{(index % len(sources)) + 1}]。"
        for index, paragraph in enumerate(paragraphs)
    ]
    answer = f"三月七是《崩坏：星穹铁道》的列车组成员，以下结论以已读取资料为依据。{citations}\n\n" + "\n\n".join(paragraphs)
    claim_topics = ("身份定位", "关键经历", "人物关系", "语言风格", "行为模式", "核心动机", "内在冲突", "角色成长")
    claims = []
    for index, source in enumerate(sources, start=1):
        excerpt = f"三月七角色资料 {index} 记录了对应角色事实、适用剧情条件和用于判断的证据边界。"
        claims.append({
            "claim": f"{claim_topics[index - 1]}结论由对应已读取资料支撑，并说明事实、条件和表达边界。",
            "supportingSources": [{"sourceId": source["sourceId"], "citationKey": source["citationKey"], "url": source["url"]}],
            "confidence": "high",
            "evidenceExcerpt": excerpt,
            "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
            "evidenceVerified": True,
        })
    independent_review = {
        "reviewDecision": "accept",
        "reviewReasons": ["问题覆盖、结论蕴含关系和资料时效均已独立核验。"],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
    }
    source_bundle = {
            "evidenceBundleId": "bundle-sanyueqi",
            "question": "之前调研过三月七吗",
            "confidence": "high",
            "authorityScore": 78,
            "freshness": "current",
            "asOf": "2026-07-28T12:00:00Z",
            "reviewDecision": "accept",
            "independentReview": independent_review,
            "sourceMatrix": sources,
            "claimTable": claims,
            "researchAnswerPack": {
                "answer": answer,
                "sources": sources,
                "reviewDecision": "accept",
                "independentReview": independent_review,
                "asOf": "2026-07-28T12:00:00Z",
                "score": {"qualityStatus": "high_quality", "qualityTier": "high_quality", "confidence": "high", "authorityScore": 78},
                "claimTable": claims,
            },
            "finalExperiencePack": {
                "researchResult": answer,
                "sourceUrls": sources,
                "reviewDecision": "accept",
                "independentReview": independent_review,
                "asOf": "2026-07-28T12:00:00Z",
                "claimTable": claims,
            },
        }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "memory-test-reviewer"),
        ("adversarial", "memory-test-adversarial-reviewer"),
    ):
        review = {**independent_review, "reviewMode": review_mode}
        review.update(
            build_research_review_binding(
                source_bundle,
                reviewer_model_id=reviewer_model_id,
                reviewed_at="2026-07-28T12:00:00Z",
            )
        )
        consensus_reviews.append(review)
    independent_review.clear()
    independent_review.update(
        {
            **consensus_reviews[0],
            "consensusAccepted": True,
            "consensusReviewCount": len(consensus_reviews),
            "consensusReviewerModelIds": [
                review["reviewerModelId"] for review in consensus_reviews
            ],
            "consensusReviews": consensus_reviews,
        }
    )
    bundle = store_evidence_bundle(
        source_bundle,
        ttl_seconds=3600,
        scope="global",
    )
    promoted = promote_experience_pack(bundle["evidenceBundleId"], title="三月七角色调研")
    assert promoted
    assert research_high_quality_issues(promoted) == []

    payload = json.loads(memory_broker.func(mode="route", query="之前调研过三月七吗", scope="global", limit=3))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert "research_experience" in payload["selectedDomains"]
    assert "memory_core" in payload["selectedDomains"]
    assert "evidencePacks" in payload
    research_pack = next(pack for pack in payload["evidencePacks"] if pack["sourceDomain"] == "research_experience")
    assert research_pack.get("selectedEvidence"), research_pack
    selected = research_pack["selectedEvidence"][0]
    assert selected["answer"].startswith("三月七是《崩坏：星穹铁道》")
    assert selected["sources"][0]["url"].startswith("https://march7-source-")
    assert selected["score"]["confidence"] == "high"
    assert "三月七" in serialized
    assert "sourceMatrix" not in serialized
    assert "rawLedgers" in serialized


def test_low_quality_research_pack_requires_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    store_evidence_bundle(
        {
            "evidenceBundleId": "bundle-noisy-youtube",
            "question": "Research the external facts needed before implementation.",
            "confidence": "high",
            "authorityScore": 80,
            "summary": "Collected 4 ranked source(s).",
            "sourceMatrix": [
                {
                    "title": "How to Rethink and Reshape Your API Documentation - YouTube",
                    "url": "https://www.youtube.com/watch?v=noise",
                    "host": "www.youtube.com",
                    "snippet": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
                }
            ],
        },
        ttl_seconds=3600,
        scope="global",
    )

    payload = json.loads(
        research_broker.func(
            mode="search_experience",
            query="Research the external facts needed before implementation.",
            includeArchived=True,
        )
    )

    assert payload["ok"] is True
    assert payload["items"] == []
    assert payload["reuseDecision"]["reuseDecision"] == "ignore"
    assert payload["reuseDecision"]["reason"] == "no_matching_experience_pack"
