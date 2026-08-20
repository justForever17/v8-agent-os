from __future__ import annotations

import importlib
import hashlib
import json
from datetime import datetime, timedelta, timezone

from core.tools.research_quality import build_research_review_binding


_AS_OF_DT = datetime.now(timezone.utc).replace(microsecond=0)
_AS_OF = _AS_OF_DT.isoformat().replace("+00:00", "Z")
_AS_OF_DATE = _AS_OF_DT.date().isoformat()


def _accepted_research_bundle(
    *,
    evidence_bundle_id: str = "bundle-accepted",
    source_count: int = 8,
) -> tuple[dict, str]:
    claim_topics = (
        "Scope",
        "Architecture",
        "Source authority",
        "Data evidence",
        "Freshness",
        "Conflicting evidence",
        "Operational risk",
        "Decision impact",
    )
    sources = [
        {
            "sourceId": f"source-{index}",
            "citationKey": f"S{index}",
            "title": f"Primary research source {index}",
            "url": f"https://source{index}.example/research",
            "host": f"source{index}.example",
            "selectedForEvidence": True,
            "retrievedAt": _AS_OF,
            "publishedAt": (
                _AS_OF_DT - timedelta(days=index)
            ).isoformat().replace("+00:00", "Z"),
            "contentChars": 6000 + index,
            "readEvidence": {
                "verified": True,
                "contentChars": 6000 + index,
                "contentSha256": "b" * 64,
                "retrievedAt": _AS_OF,
            },
            "authorityScore": 80 + index,
            "tier": "primary",
        }
        for index in range(1, source_count + 1)
    ]
    claim_table = []
    for index in range(1, 9):
        excerpt = f"Primary research source {index} records a distinct observed fact, its condition, and its evidence boundary."
        claim_table.append({
            "claim": f"{claim_topics[index - 1]} is independently supported with explicit conditions and evidence boundaries.",
            "claimType": "source_fact",
            "supportingSources": [f"S{index}"],
            "confidence": "high",
            "evidenceExcerptKey": f"S{index}:E1",
            "evidenceExcerpt": excerpt,
            "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
            "evidenceVerified": True,
        })
    subjects = ("scope", "architecture", "sources", "data", "freshness", "conflicts", "risks", "decisions")
    aspects = ("facts", "causality", "conditions", "counterevidence", "versions", "operations", "verification", "refresh")
    sections = [
        (
            f"## {subject.title()} and {aspect}\n"
            f"The {subject} evidence for {aspect} establishes a distinct observed fact, explains its causal boundary, "
            f"and separates the source's statement from the synthesis. This section records the applicable condition, "
            f"the counter-evidence that would overturn the {subject} conclusion, the operational consequence for {aspect}, "
            "and the exact future signal that requires a refresh"
        )
        for subject in subjects
        for aspect in aspects
    ]
    sections = [
        f"{section.rstrip('.')} [S{(index % len(sources)) + 1}]."
        for index, section in enumerate(sections)
    ]
    full_result = "# Detailed research answer\n\n" + "\n\n".join(sections)
    review_reasons = ["The answer passed source, citation, claim, freshness, and completeness review."]
    quality_metrics = {"architectCoverageScore": 0.97}
    independent_review = {
        "reviewDecision": "accept",
        "reviewReasons": ["The answer covers the question and all claims are entailed by verified evidence."],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    answer_pack = {
        "answer": full_result,
        "sources": sources,
        "claimTable": claim_table,
        "reviewDecision": "accept",
        "reviewReasons": review_reasons,
        "independentReview": independent_review,
        "asOf": _AS_OF,
        "qualityTier": "high_quality",
        "qualityMetrics": quality_metrics,
        "criticalMissingEvidence": [],
        "score": {
            "qualityStatus": "usable_answer",
            "confidence": "high",
            "authorityScore": 91,
            "qualityTier": "high_quality",
        },
    }
    final_pack = {
        "researchResult": full_result,
        "sourceUrls": sources,
        "claimTable": claim_table,
        "reviewDecision": "accept",
        "reviewReasons": review_reasons,
        "independentReview": independent_review,
        "asOf": _AS_OF,
        "qualityTier": "high_quality",
        "qualityMetrics": quality_metrics,
        "criticalMissingEvidence": [],
    }
    bundle = {
            "evidenceBundleId": evidence_bundle_id,
            "question": f"截至 {_AS_OF_DATE}，V8 research runtime 应如何复用完整调研结果？",
            "answer": full_result,
            "researchResult": full_result,
            "summary": "Reuse only a fully reviewed, source-backed research result.",
            "confidence": "high",
            "authorityScore": 91,
            "freshness": "current",
            "completedAt": _AS_OF,
            "asOf": _AS_OF,
            "reviewDecision": "accept",
            "reviewReasons": review_reasons,
            "independentReview": independent_review,
            "qualityTier": "high_quality",
            "qualityMetrics": quality_metrics,
            "briefCoverageRequired": False,
            "briefCoverageComplete": True,
            "sourceMatrix": sources,
            "claimTable": claim_table,
            "criticalMissingEvidence": [],
            "researchAnswerPack": answer_pack,
            "finalExperiencePack": final_pack,
        }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "ledger-test-reviewer"),
        ("adversarial", "ledger-test-adversarial-reviewer"),
    ):
        review = {**independent_review, "reviewMode": review_mode}
        review.update(
            build_research_review_binding(
                bundle,
                reviewer_model_id=reviewer_model_id,
                reviewed_at=_AS_OF,
            )
        )
        consensus_reviews.append(review)
    independent_review.clear()
    independent_review.update(
        {
            **consensus_reviews[0],
            "consensusAccepted": True,
            "consensusReviewCount": 2,
            "consensusReviewerModelIds": [review["reviewerModelId"] for review in consensus_reviews],
            "consensusReviews": consensus_reviews,
        }
    )
    return bundle, full_result


def _rebind_bundle_review(bundle: dict) -> None:
    independent_review = bundle["independentReview"]
    consensus_reviews = independent_review["consensusReviews"]
    for review in consensus_reviews:
        review.update(
            build_research_review_binding(
                bundle,
                reviewer_model_id=review["reviewerModelId"],
                reviewed_at=review["reviewedAt"],
            )
        )
    independent_review.update(consensus_reviews[0])


def _legacy_low_quality_pack(
    pack_id: str,
    *,
    status: str = "active",
    created_at: str | None = None,
    topic_fingerprint: str = "legacy-topic",
    scope: str = "global",
) -> dict:
    timestamp = created_at or _AS_OF
    return {
        "experiencePackId": pack_id,
        "status": status,
        "title": "legacy research",
        "query": "legacy research",
        "summary": "legacy unverified result",
        "researchResult": "legacy unverified result",
        "qualityStatus": "reusable_candidate",
        "confidence": "high",
        "authorityScore": 90,
        "sourceUrls": ["https://example.test/legacy"],
        "topicFingerprint": topic_fingerprint,
        "scope": scope,
        "evidenceCheckedAt": timestamp,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "usageCount": 0,
    }


def test_experience_pack_archive_restore_and_hard_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, full_result = _accepted_research_bundle()

    bundle = ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="project:test")
    auto_promoted = ledger.search_experience_packs(query="research runtime", scope="project:test")
    assert len(auto_promoted) == 1

    pack = ledger.promote_experience_pack(bundle["evidenceBundleId"], title="Research reuse", tags=["research"])
    assert pack
    assert pack["status"] == "active"
    assert pack["reuseEligible"] is True
    assert pack["qualityAccepted"] is True
    assert pack["researchResult"] == full_result
    assert pack["reusableExperiencePack"]["researchResult"] == full_result
    assert pack["reviewDecision"] == "accept"
    assert pack["reviewReasons"] == source_bundle["reviewReasons"]
    assert pack["asOf"] == _AS_OF
    assert pack["qualityTier"] == "high_quality"
    assert pack["qualityMetrics"]["architectCoverageScore"] == 0.97
    assert pack["qualityMetrics"]["selectedSourceCount"] == 8
    assert pack["qualityMetrics"]["answerCitedSourceCount"] == 8
    assert len(pack["sourceMatrixDigest"]) == 8
    assert all(source["selectedForEvidence"] is True for source in pack["sourceMatrixDigest"])
    assert all(source.get("citationKey") for source in pack["sourceMatrixDigest"])
    assert all(source.get("retrievedAt") for source in pack["sourceMatrixDigest"])
    assert all(source.get("publishedAt") for source in pack["sourceMatrixDigest"])

    archived = ledger.archive_experience_pack(pack["experiencePackId"], initiated_by="test", reason="unit")
    assert archived and archived["status"] == "archived"
    assert archived["reuseEligible"] is False
    assert ledger.search_experience_packs(query="research", scope="project:test") == []
    archived_matches = ledger.search_experience_packs_with_options(
        query="research",
        scope="project:test",
        include_archived=True,
    )
    assert len(archived_matches) == 1

    restored = ledger.restore_experience_pack(pack["experiencePackId"], initiated_by="test")
    assert restored and restored["status"] == "active"
    assert restored["reuseEligible"] is True
    assert ledger.search_experience_packs(query="research", scope="project:test")

    assert ledger.delete_experience_pack(pack["experiencePackId"], confirm=True) is True
    assert ledger.get_experience_pack(pack["experiencePackId"], include_archived=True) is None
    assert ledger.get_evidence_bundle(bundle["evidenceBundleId"])


def test_experience_pack_keeps_all_sources_bound_to_the_reviewer_receipt(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, _full_result = _accepted_research_bundle(
        evidence_bundle_id="bundle-reviewed-24-sources",
        source_count=24,
    )

    ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="project:test")
    matches = ledger.search_experience_packs(
        query=source_bundle["question"],
        scope="project:test",
    )

    assert len(matches) == 1
    assert matches[0]["reuseEligible"] is True
    assert len(matches[0]["researchAnswerPack"]["sources"]) == 24
    assert len(matches[0]["sourceMatrixDigest"]) == 16
    assert matches[0]["briefCoverageRequired"] is False
    assert matches[0]["briefCoverageComplete"] is True


def test_time_sensitive_auto_freshness_is_persisted_as_current_window(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, _ = _accepted_research_bundle(evidence_bundle_id="bundle-auto-current")
    source_bundle["freshness"] = "auto"
    _rebind_bundle_review(source_bundle)

    stored = ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="session:test")
    packs = ledger.search_experience_packs(
        query=source_bundle["question"],
        scope="session:test",
    )

    assert stored["evidenceBundleId"] == "bundle-auto-current"
    assert packs[0]["freshnessWindow"] == "current"
    assert packs[0]["requestedFreshness"] == "auto"
    assert packs[0]["maxAgeDays"] == 7


def test_rejected_bundle_remains_auditable_but_cannot_be_promoted(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, _ = _accepted_research_bundle(evidence_bundle_id="bundle-retry")
    source_bundle["reviewDecision"] = "retry"
    source_bundle["researchAnswerPack"]["reviewDecision"] = "retry"
    source_bundle["finalExperiencePack"]["reviewDecision"] = "retry"

    stored = ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="project:test")

    assert stored["reviewDecision"] == "retry"
    assert ledger.get_evidence_bundle(stored["evidenceBundleId"])["reviewDecision"] == "retry"
    assert ledger.promote_experience_pack(stored["evidenceBundleId"]) is None
    assert ledger.list_experience_packs(scope="project:test", include_archived=True) == []
    assert ledger.search_experience_packs(query="research runtime", scope="project:test") == []


def test_experience_pack_keeps_selected_sources_when_answer_pack_did_not_own_them(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, full_result = _accepted_research_bundle(evidence_bundle_id="bundle-external-sources")
    source_bundle["researchAnswerPack"].pop("sources")
    for key in ("reviewDecision", "reviewReasons", "asOf", "qualityTier", "qualityMetrics"):
        source_bundle.pop(key)
        source_bundle["researchAnswerPack"].pop(key)
    source_bundle["researchResult"] = source_bundle.pop("finalExperiencePack")

    bundle = ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="project:test")
    pack = ledger.promote_experience_pack(bundle["evidenceBundleId"])

    assert pack
    assert pack["status"] == "active"
    assert pack["reuseEligible"] is True
    assert pack["researchResult"] == full_result
    assert pack["reviewDecision"] == "accept"
    assert pack["reviewReasons"]
    assert pack["asOf"] == _AS_OF
    assert pack["qualityTier"] == "high_quality"
    assert pack["qualityMetrics"]["architectCoverageScore"] == 0.97
    assert len(pack["researchAnswerPack"]["sources"]) == 8
    assert all(source["selectedForEvidence"] is True for source in pack["researchAnswerPack"]["sources"])


def test_experience_pack_preserves_canonical_claims_and_supports_without_loss(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, _full_result = _accepted_research_bundle(evidence_bundle_id="bundle-canonical-claims")
    excerpt = "Primary research source 1 requires retaining all four supporting source identities for this additional fact."
    extra_claim = {
        "claim": "The source requires retaining all four supporting source identities through persistence.",
        "claimType": "explicit_normative",
        "normativeCue": "requires retaining all four supporting source identities",
        "supportingSources": ["S1", "S2", "S3", "S4"],
        "confidence": "high",
        "evidenceExcerptKey": "S1:E2",
        "evidenceExcerpt": excerpt,
        "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
        "evidenceVerified": True,
    }
    source_bundle["claimTable"].append(extra_claim)
    _rebind_bundle_review(source_bundle)

    bundle = ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="project:test")
    pack = ledger.promote_experience_pack(bundle["evidenceBundleId"])

    assert pack
    assert len(pack["researchAnswerPack"]["claimTable"]) == 9
    assert len(pack["claimDigest"]) == 9
    assert len(pack["claimDigest"][-1]["supportingSources"]) == 4
    assert pack["claimDigest"][-1]["claimType"] == "explicit_normative"
    assert pack["claimDigest"][-1]["normativeCue"] == "requires retaining all four supporting source identities"
    assert pack["claimDigest"][-1]["evidenceExcerptKey"] == "S1:E2"


def test_spec_task_evidence_bundle_does_not_create_or_match_experience_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")

    bundle = ledger.store_evidence_bundle(
        {
            "evidenceBundleId": "bundle-spec-task",
            "question": "TASK-001: Execute approved Spec spec_094d02189a1e4c20",
            "questionKind": "spec_task",
            "sourceKind": "spec_task",
            "answer": "The task has implementation instructions and should remain execution evidence only.",
            "summary": "Execution evidence for an approved Spec task.",
            "confidence": "high",
            "authorityScore": 90,
            "sourceMatrix": [{"title": "Spec task", "host": "local", "url": "spec://spec_094d02189a1e4c20/tasks#TASK-001"}],
            "researchAnswerPack": {
                "answer": "The task has implementation instructions and should remain execution evidence only.",
                "sources": [{"title": "Spec task", "host": "local", "url": "spec://spec_094d02189a1e4c20/tasks#TASK-001"}],
                "score": {"qualityStatus": "usable_answer", "confidence": "high", "authorityScore": 90},
            },
        },
        ttl_seconds=3600,
        scope="global",
    )

    stored = ledger.get_evidence_bundle(bundle["evidenceBundleId"])
    assert stored
    assert stored["questionKind"] == "spec_task"
    assert stored["sourceKind"] == "spec_task"
    assert ledger.promote_experience_pack(bundle["evidenceBundleId"], title="Should not promote") is None
    assert ledger.search_experience_packs(query="Execute approved Spec spec_094d02189a1e4c20", scope="global") == []
    assert ledger.search_experience_packs_with_options(
        query="Execute approved Spec spec_094d02189a1e4c20",
        scope="global",
        include_archived=True,
    ) == []


def test_legacy_active_pack_is_auditable_but_not_reusable(tmp_path, monkeypatch):
    ledger_path = tmp_path / "research_ledger.json"
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(ledger_path))
    ledger = importlib.import_module("core.tools.research_ledger")
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat().replace("+00:00", "Z")
    legacy = _legacy_low_quality_pack("rxp_old", created_at=old)
    legacy["freshnessWindow"] = "90d"
    legacy["usageCount"] = 3
    ledger_path.write_text(
        json.dumps({"version": 1, "evidenceBundles": [], "experiencePacks": [legacy]}),
        encoding="utf-8",
    )

    detail = ledger.get_experience_pack("rxp_old")
    assert detail and detail["usageCount"] == 3
    assert detail["status"] == "draft"
    assert detail["storedStatus"] == "active"
    assert detail["reuseEligible"] is False
    assert detail["qualityAccepted"] is False
    assert "reviewDecision" not in detail
    assert "architect_review_not_accepted" in detail["invalidationReason"]
    assert detail["freshnessState"] == "expired"
    assert ledger.list_experience_packs(scope="global")
    assert ledger.search_experience_packs(query="legacy research", scope="global") == []
    assert ledger.get_experience_pack("rxp_old", record_usage=True)["usageCount"] == 4

    first = ledger.maintain_experience_packs()
    second = ledger.maintain_experience_packs()
    assert first["expiredArchivedCount"] == 1
    assert second["expiredArchivedCount"] == 0
    archived = ledger.get_experience_pack("rxp_old", include_archived=True)
    assert archived and archived["status"] == "archived"
    assert archived["archiveReason"] == "freshness_expired"


def test_low_quality_restore_stays_draft_and_bulk_governance_is_recoverable(tmp_path, monkeypatch):
    ledger_path = tmp_path / "research_ledger.json"
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(ledger_path))
    ledger = importlib.import_module("core.tools.research_ledger")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    older = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    payload = {
        "version": 1,
        "evidenceBundles": [],
        "experiencePacks": [
            _legacy_low_quality_pack(
                "rxp_new",
                created_at=now,
                topic_fingerprint="same-topic",
                scope="project:test",
            ),
            _legacy_low_quality_pack(
                "rxp_old",
                created_at=older,
                topic_fingerprint="same-topic",
                scope="project:test",
            ),
        ],
    }
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    maintenance = ledger.maintain_experience_packs()
    assert maintenance["duplicateArchivedCount"] == 1
    assert ledger.get_experience_pack("rxp_old", include_archived=True)["status"] == "archived"

    restored = ledger.bulk_update_experience_packs(["rxp_old"], action="restore", initiated_by="test")
    assert restored["updatedCount"] == 1
    restored_pack = restored["items"][0]
    assert restored_pack["status"] == "draft"
    assert restored_pack["storedStatus"] == "draft"
    assert restored_pack["reuseEligible"] is False
    assert "reviewDecision" not in restored_pack
    assert "architect_review_not_accepted" in restored_pack["restoreQualityIssues"]
    assert ledger.search_experience_packs(query="legacy research", scope="project:test") == []

    archived = ledger.bulk_update_experience_packs(["rxp_old", "rxp_new"], action="archive", initiated_by="test")
    assert archived["updatedCount"] == 2


def test_maintenance_keeps_accepted_pack_over_newer_low_quality_duplicate(tmp_path, monkeypatch):
    ledger_path = tmp_path / "research_ledger.json"
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(ledger_path))
    ledger = importlib.import_module("core.tools.research_ledger")
    source_bundle, _ = _accepted_research_bundle(evidence_bundle_id="bundle-maintenance")
    ledger.store_evidence_bundle(source_bundle, ttl_seconds=3600, scope="project:test")

    now = datetime.now(timezone.utc)
    older = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    newer = now.isoformat().replace("+00:00", "Z")
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    reusable = payload["experiencePacks"][0]
    reusable.update(
        {
            "experiencePackId": "rxp_reusable",
            "topicFingerprint": "same-topic",
            "evidenceCheckedAt": older,
            "createdAt": older,
            "updatedAt": older,
        }
    )
    low_quality = _legacy_low_quality_pack(
        "rxp_low_quality",
        created_at=newer,
        topic_fingerprint="same-topic",
        scope="project:test",
    )
    low_quality["qualityStatus"] = "low_quality_pack"
    payload["experiencePacks"] = [reusable, low_quality]
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    maintenance = ledger.maintain_experience_packs(now=now)

    assert maintenance["duplicateArchivedCount"] == 1
    accepted = ledger.get_experience_pack("rxp_reusable", include_archived=True)
    assert accepted["status"] == "active"
    assert accepted["reuseEligible"] is True
    low_quality = ledger.get_experience_pack("rxp_low_quality", include_archived=True)
    assert low_quality["status"] == "archived"
    assert low_quality["archiveReason"] == "superseded_duplicate_topic"
