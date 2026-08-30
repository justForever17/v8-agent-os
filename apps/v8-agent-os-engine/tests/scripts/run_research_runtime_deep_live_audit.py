from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
TOKEN_RE = re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+|((?:api[_-]?key|token|cookie|authorization)[\"'\s:=]+)[^\"'\s,;]+")
PURE_RESEARCH_QUESTION = (
    "截至 2026 年 7 月，Windows 用户如果想选择一款本地命令行 AI 编程 Agent，OpenAI Codex CLI、"
    "Claude Code、Gemini CLI 和 GitHub Copilot CLI 各自适合什么人？请基于官方资料和可信的实际使用经验，"
    "比较 Windows 安装与运行方式、模型和工具调用、MCP 或扩展、代码库工作流、账号或价格依赖、隐私边界、"
    "常见局限，并给出按个人开发者、小团队和已有平台订阅者划分的可执行选型建议。"
    "对较旧或未标日期的经验显著标注时间边界。"
)

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class AuditCaseResult:
    case_id: str
    title: str
    status: str = "pending"
    summary: str = ""
    elapsed_ms: int = 0
    providers: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _redact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = TOKEN_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", text)
    text = text.replace(str(Path.home()), "~")
    return text


def _research_run(
    question: str,
    *,
    source_policy: str = "authoritative",
    freshness: str = "auto",
    max_shards: int = 10,
    max_rounds: int = 5,
    force_refresh: bool = False,
) -> dict[str, Any]:
    from core.tools.research_broker import research_broker

    raw = research_broker.func(
        mode="run",
        question=question,
        sourcePolicy=source_policy,
        freshness=freshness,
        maxShards=max_shards,
        maxRounds=max_rounds,
        forceRefresh=force_refresh,
        tool_call_id=f"live-research-deep-{int(time.time())}",
    )
    return json.loads(raw)


def _persisted_research_bundle(payload: dict[str, Any]) -> dict[str, Any] | None:
    evidence_bundle_id = str(payload.get("evidenceBundleId") or "").strip()
    if not evidence_bundle_id:
        return None
    from core.tools.research_ledger import get_evidence_bundle

    bundle = get_evidence_bundle(evidence_bundle_id)
    return bundle if isinstance(bundle, dict) else None


def _persisted_research_diagnostic(
    payload: dict[str, Any],
    *,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact persisted-ledger diagnostics without source or answer bodies."""

    evidence_bundle_id = str(payload.get("evidenceBundleId") or "").strip()
    diagnostic: dict[str, Any] = {
        "kind": "persisted_research_diagnostic",
        "evidenceBundleId": evidence_bundle_id or None,
    }
    if not evidence_bundle_id:
        return {**diagnostic, "status": "evidence_bundle_id_missing"}

    try:
        bundle = bundle or _persisted_research_bundle(payload)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not replace the live case result.
        return {
            **diagnostic,
            "status": "ledger_read_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(bundle, dict):
        return {**diagnostic, "status": "evidence_bundle_not_found"}

    original_chars_by_url: dict[str, int] = {}
    for shard in list(bundle.get("shards") or []):
        if not isinstance(shard, dict):
            continue
        for fetched in list(shard.get("fetchedTopSources") or []):
            if not isinstance(fetched, dict):
                continue
            url = str(fetched.get("url") or fetched.get("finalUrl") or "").strip()
            if not url:
                continue
            raw_chars = fetched.get("originalContentChars")
            if raw_chars is None:
                raw_chars = fetched.get("contentChars")
            try:
                chars = int(raw_chars or 0)
                original_chars_by_url[url] = max(original_chars_by_url.get(url, 0), chars)
                final_url = str(fetched.get("finalUrl") or "").strip()
                if final_url:
                    original_chars_by_url[final_url] = max(original_chars_by_url.get(final_url, 0), chars)
            except (TypeError, ValueError):
                continue

    sources: list[dict[str, Any]] = []
    for source in list(bundle.get("sourceMatrix") or []):
        if not isinstance(source, dict):
            continue
        gate = source.get("sourceQualityGate") if isinstance(source.get("sourceQualityGate"), dict) else {}
        temporal = source.get("temporalEvidence") if isinstance(source.get("temporalEvidence"), dict) else {}
        url = str(source.get("url") or source.get("sourceUrl") or "").strip()
        selected = source.get("selectedForEvidence")
        if selected is None:
            selected = gate.get("selectedForEvidence")
        sources.append(
            {
                "sourceId": source.get("sourceId"),
                "url": url or None,
                "documentId": source.get("documentId"),
                "title": source.get("title") or source.get("sourceTitle"),
                "host": source.get("host"),
                "tier": source.get("tier") or source.get("authorityTier"),
                "contentChars": source.get("contentChars"),
                "originalContentChars": (
                    source.get("originalContentChars")
                    if source.get("originalContentChars") is not None
                    else original_chars_by_url.get(url, source.get("contentChars"))
                ),
                "omittedChars": source.get("omittedChars"),
                "evidenceSelection": source.get("evidenceSelection"),
                "readEvidence": source.get("readEvidence") if isinstance(source.get("readEvidence"), dict) else {},
                "retrieved": source.get("retrievedAt") or source.get("fetchedAt") or temporal.get("retrievedAt"),
                "published": source.get("publishedAt") or temporal.get("publishedAt"),
                "updated": source.get("updatedAt") or temporal.get("updatedAt"),
                "version": source.get("version") or temporal.get("version"),
                "rejectedReason": source.get("rejectedReason") or gate.get("rejectedReason"),
                "selected": bool(selected),
            }
        )

    loop_state = bundle.get("researchLoopState") if isinstance(bundle.get("researchLoopState"), dict) else {}
    rounds: list[dict[str, Any]] = []
    round_fields = (
        "round",
        "kind",
        "queries",
        "resultCount",
        "readSourceCount",
        "selectedSourceCount",
        "distinctHostCount",
        "datedSourceCount",
        "rejectedSourceCount",
        "sourceUrls",
    )
    for item in list(loop_state.get("rounds") or []):
        if not isinstance(item, dict):
            continue
        rounds.append({key: item.get(key) for key in round_fields if item.get(key) not in (None, "", [], {})})

    final_pack = bundle.get("finalExperiencePack") if isinstance(bundle.get("finalExperiencePack"), dict) else {}
    if not final_pack and isinstance(bundle.get("researchResult"), dict):
        final_pack = bundle.get("researchResult") or {}
    model_synthesis = final_pack.get("modelSynthesis") if isinstance(final_pack.get("modelSynthesis"), dict) else {}
    synthesis_fields = (
        "used",
        "agentId",
        "agentName",
        "mode",
        "modelRole",
        "modelId",
        "writerModelRole",
        "writerModelId",
        "reviewerModelRole",
        "reviewerModelId",
        "reviewerConsensusCount",
        "reviewerConsensusModelIds",
        "writerMode",
        "writerSectionCount",
        "writerRevisionCount",
        "sameEvidenceReviewRejected",
        "parseMode",
        "fallbackReason",
        "fallbackAttempts",
        "attemptBudgetExhausted",
    )
    compact_synthesis = {
        key: model_synthesis.get(key)
        for key in synthesis_fields
        if model_synthesis.get(key) not in (None, "", [], {})
    }
    search_receipts: list[dict[str, Any]] = []
    for shard in list(bundle.get("shards") or []):
        if not isinstance(shard, dict):
            continue
        search_receipts.append(
            {
                key: value
                for key, value in {
                    "shardId": shard.get("shardId"),
                    "kind": shard.get("kind"),
                    "query": shard.get("query"),
                    "ok": shard.get("ok"),
                    "provider": shard.get("provider"),
                    "networkRoute": shard.get("networkRoute"),
                    "resultCount": shard.get("resultCount"),
                    "readCount": len(
                        [item for item in list(shard.get("fetchedTopSources") or []) if isinstance(item, dict)]
                    ),
                }.items()
                if value not in (None, "", [], {})
            }
        )
    read_receipts = [
        {
            "sourceId": source.get("sourceId"),
            "url": source.get("url"),
            "selected": source.get("selected"),
            "contentChars": source.get("contentChars"),
            "readEvidence": source.get("readEvidence") or {},
        }
        for source in sources
        if source.get("selected")
    ]
    independent_review = (
        final_pack.get("independentReview")
        if isinstance(final_pack.get("independentReview"), dict)
        else bundle.get("independentReview")
        if isinstance(bundle.get("independentReview"), dict)
        else {}
    )

    return {
        **diagnostic,
        "status": "ok",
        "sourceMatrix": sources,
        "researchLoop": {
            "phase": loop_state.get("phase"),
            "stopReason": loop_state.get("stopReason"),
            "rounds": rounds,
        },
        "experienceReuse": bundle.get("experienceReuse") or {},
        "searchReceipts": search_receipts,
        "readReceipts": read_receipts,
        "finalPack": {
            "reviewDecision": final_pack.get("reviewDecision"),
            "reviewReasons": list(final_pack.get("reviewReasons") or []),
            "criticalMissingEvidence": list(final_pack.get("criticalMissingEvidence") or []),
            "recommendedNextQueries": list(final_pack.get("recommendedNextQueries") or []),
            "modelSynthesis": compact_synthesis,
            "independentReview": independent_review,
        },
    }


def _high_quality_delivery_failures(payload: dict[str, Any]) -> list[str]:
    from core.tools.research_quality import (
        research_answer_text,
        research_acceptance_metrics,
        research_high_quality_issues,
        research_review_decision,
    )

    answer_pack = payload.get("researchAnswerPack") if isinstance(payload.get("researchAnswerPack"), dict) else {}
    score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
    canonical_payload = payload
    try:
        persisted = _persisted_research_bundle(payload)
    except Exception:  # noqa: BLE001 - the checks below expose missing proof.
        persisted = None
    if isinstance(persisted, dict):
        canonical_payload = persisted
    recomputed_metrics = research_acceptance_metrics(canonical_payload)
    failures = [
        f"recomputed_quality_issue:{issue}"
        for issue in research_high_quality_issues(canonical_payload)
    ]
    if research_answer_text(payload) != research_answer_text(canonical_payload):
        failures.append("agent_surface_answer_does_not_match_persisted_evidence")
    checks = {
        "broker_not_ok": payload.get("ok") is True,
        "delivery_not_ready": payload.get("deliveryReady") is True and score.get("deliveryReady") is True,
        "quality_tier_not_high": (
            payload.get("qualityTier") == "high_quality"
            and (score.get("qualityTier") or score.get("qualityStatus")) == "high_quality"
        ),
        "review_not_accepted": (
            research_review_decision(payload) == "accept"
            and research_review_decision(canonical_payload) == "accept"
        ),
        "independent_review_not_accepted": recomputed_metrics.get("independentReviewAccepted") is True,
    }
    for code, passed in checks.items():
        if not passed:
            failures.append(code)
    reported_metrics = score.get("acceptanceMetrics") if isinstance(score.get("acceptanceMetrics"), dict) else {}
    if reported_metrics != recomputed_metrics:
        failures.append("answer_pack_acceptance_metrics_do_not_match_recomputed")
    top_level_metrics = payload.get("qualityMetrics") if isinstance(payload.get("qualityMetrics"), dict) else {}
    if top_level_metrics and top_level_metrics != recomputed_metrics:
        failures.append("top_level_quality_metrics_do_not_match_recomputed")
    return list(dict.fromkeys(failures))


def _surface_source_keys(items: Any, *, question: str, selected_only: bool) -> list[str]:
    from core.tools.research_source_identity import research_document_identity

    keys: set[str] = set()
    for item in list(items or []):
        if isinstance(item, str):
            identity = research_document_identity(item, question=question)
            if identity:
                keys.add(identity)
            continue
        if not isinstance(item, dict):
            continue
        gate = item.get("sourceQualityGate") if isinstance(item.get("sourceQualityGate"), dict) else {}
        if selected_only and item.get("selectedForEvidence") is not True and gate.get("selectedForEvidence") is not True:
            continue
        url = str(item.get("url") or item.get("sourceUrl") or "").strip()
        source_id = str(item.get("sourceId") or "").strip()
        identity = research_document_identity(url, question=question) if url else ""
        key = identity or source_id
        if key:
            keys.add(key)
    return sorted(keys)


def _surface_claim_digest(items: Any, *, question: str) -> str:
    claims: list[dict[str, Any]] = []
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        supporting = _surface_source_keys(item.get("supportingSources"), question=question, selected_only=False)
        claims.append(
            {
                "claim": re.sub(r"\s+", " ", str(item.get("claim") or item.get("summary") or "").strip()),
                "claimType": str(item.get("claimType") or item.get("claimKind") or "").strip(),
                "normativeCue": re.sub(r"\s+", " ", str(item.get("normativeCue") or "").strip()),
                "supportingSources": supporting,
                "evidenceExcerptKey": str(item.get("evidenceExcerptKey") or "").strip(),
                "evidenceExcerptSha256": str(item.get("evidenceExcerptSha256") or "").strip().lower(),
                "evidenceVerified": item.get("evidenceVerified") is True,
            }
        )
    encoded = json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _technical_surface_parity_failures(bundle: dict[str, Any]) -> list[str]:
    answer_pack = bundle.get("researchAnswerPack") if isinstance(bundle.get("researchAnswerPack"), dict) else {}
    final_pack = bundle.get("finalExperiencePack") if isinstance(bundle.get("finalExperiencePack"), dict) else {}
    question = str(bundle.get("question") or final_pack.get("question") or "").strip()
    failures: list[str] = []
    if not answer_pack:
        failures.append("persisted_answer_pack_missing")
    if not final_pack:
        failures.append("persisted_final_pack_missing")
    if failures:
        return failures

    answers = {
        "top": str(bundle.get("answer") or "").strip(),
        "answer_pack": str(answer_pack.get("answer") or "").strip(),
        "final_pack": str(final_pack.get("researchResult") or final_pack.get("answer") or "").strip(),
    }
    if not all(answers.values()) or len(set(answers.values())) != 1:
        failures.append("answer_surface_parity_mismatch")

    claim_digests = {
        _surface_claim_digest(bundle.get("claimTable"), question=question),
        _surface_claim_digest(answer_pack.get("claimTable"), question=question),
        _surface_claim_digest(final_pack.get("claimTable"), question=question),
    }
    if len(claim_digests) != 1:
        failures.append("claim_surface_parity_mismatch")

    source_keys = {
        tuple(_surface_source_keys(bundle.get("sourceUrls"), question=question, selected_only=False)),
        tuple(_surface_source_keys(answer_pack.get("sources"), question=question, selected_only=False)),
        tuple(_surface_source_keys(final_pack.get("sourceUrls"), question=question, selected_only=False)),
    }
    if any(not value for value in source_keys) or len(source_keys) != 1:
        failures.append("source_surface_parity_mismatch")

    reviews = (bundle.get("independentReview"), answer_pack.get("independentReview"), final_pack.get("independentReview"))
    if any(not isinstance(review, dict) or not review for review in reviews) or len(
        {json.dumps(review, ensure_ascii=False, sort_keys=True) for review in reviews}
    ) != 1:
        failures.append("independent_review_surface_parity_mismatch")

    for field, failure_code in (
        ("reviewDecision", "review_decision_surface_parity_mismatch"),
        ("asOf", "as_of_surface_parity_mismatch"),
    ):
        values = (bundle.get(field), answer_pack.get(field), final_pack.get(field))
        if any(value in (None, "") for value in values) or len({str(value) for value in values}) != 1:
            failures.append(failure_code)

    for field, failure_code in (
        ("criticalMissingEvidence", "critical_evidence_gap_not_empty"),
        ("recommendedNextQueries", "recommended_next_queries_not_empty"),
    ):
        values = (bundle.get(field), answer_pack.get(field), final_pack.get(field))
        if any(list(value or []) for value in values):
            failures.append(failure_code)
    return failures


def _technical_runtime_failures(payload: dict[str, Any], bundle: dict[str, Any] | None) -> list[str]:
    from core.tools.research_quality import TARGET_RESEARCH_SOURCE_COUNT, build_research_review_binding

    if not isinstance(bundle, dict):
        return ["persisted_evidence_bundle_missing"]
    failures = _technical_surface_parity_failures(bundle)
    reuse = bundle.get("experienceReuse") if isinstance(bundle.get("experienceReuse"), dict) else {}
    if not reuse and isinstance(payload.get("experienceReuse"), dict):
        reuse = payload.get("experienceReuse") or {}
    if reuse.get("reuseDecision") == "reuse" or reuse.get("skippedSearches") is True:
        failures.append("technical_live_reused_prior_experience")

    loop_state = bundle.get("researchLoopState") if isinstance(bundle.get("researchLoopState"), dict) else {}
    rounds = [item for item in list(loop_state.get("rounds") or []) if isinstance(item, dict)]
    if loop_state.get("phase") == "experience_reused" or not rounds:
        failures.append("technical_live_missing_fresh_research_round")

    final_pack = bundle.get("finalExperiencePack") if isinstance(bundle.get("finalExperiencePack"), dict) else {}
    model_synthesis = final_pack.get("modelSynthesis") if isinstance(final_pack.get("modelSynthesis"), dict) else {}
    if final_pack.get("synthesisMode") != "model_agent" or model_synthesis.get("used") is not True:
        failures.append("architect_agent_not_used:model_synthesis_missing")
    writer_mode = str(model_synthesis.get("writerMode") or "")
    if not writer_mode:
        failures.append("technical_writer_mode_missing")
    section_count = model_synthesis.get("writerSectionCount")
    if isinstance(section_count, bool) or not isinstance(section_count, int) or section_count < 0:
        failures.append("technical_writer_section_count_invalid")
    elif writer_mode.startswith("segmented") and section_count < 2:
        failures.append("technical_segmented_writer_section_count_invalid")
    revision_count = model_synthesis.get("writerRevisionCount")
    if isinstance(revision_count, bool) or not isinstance(revision_count, int) or revision_count < 0:
        failures.append("technical_writer_revision_count_invalid")
    same_evidence_rejected = model_synthesis.get("sameEvidenceReviewRejected")
    if not isinstance(same_evidence_rejected, bool):
        failures.append("technical_same_evidence_review_state_missing")
    elif same_evidence_rejected and (not isinstance(revision_count, int) or revision_count < 1):
        failures.append("technical_review_rejection_missing_revision")
    for field in ("writerModelRole", "writerModelId", "reviewerModelRole", "reviewerModelId"):
        if not str(model_synthesis.get(field) or "").strip():
            failures.append(f"technical_{field}_missing")
    consensus_count = model_synthesis.get("reviewerConsensusCount")
    consensus_model_ids = model_synthesis.get("reviewerConsensusModelIds")
    if isinstance(consensus_count, bool) or not isinstance(consensus_count, int) or consensus_count < 2:
        failures.append("technical_reviewer_consensus_count_invalid")
    if not isinstance(consensus_model_ids, list) or len(consensus_model_ids) != consensus_count:
        failures.append("technical_reviewer_consensus_models_invalid")

    independent_review = (
        final_pack.get("independentReview") if isinstance(final_pack.get("independentReview"), dict) else {}
    )
    required_review_flags = ("questionCoverage", "claimEntailment", "freshnessAdequacy")
    required_review_lists = ("reviewReasons", "unsupportedClaims", "criticalMissingEvidence", "recommendedNextQueries")
    if independent_review.get("reviewDecision") != "accept":
        failures.append("technical_independent_review_not_accepted")
    if any(independent_review.get(field) is not True for field in required_review_flags):
        failures.append("technical_independent_review_flags_incomplete")
    if any(not isinstance(independent_review.get(field), list) for field in required_review_lists):
        failures.append("technical_independent_review_schema_incomplete")
    if any(independent_review.get(field) for field in ("unsupportedClaims", "criticalMissingEvidence", "recommendedNextQueries")):
        failures.append("technical_independent_review_has_open_gaps")
    consensus_reviews = [
        item
        for item in list(independent_review.get("consensusReviews") or [])
        if isinstance(item, dict)
    ]
    if independent_review.get("consensusAccepted") is not True:
        failures.append("technical_independent_review_consensus_not_accepted")
    if independent_review.get("consensusReviewCount") != len(consensus_reviews) or len(consensus_reviews) < 2:
        failures.append("technical_independent_review_consensus_count_invalid")
    if {str(item.get("reviewMode") or "").strip().lower() for item in consensus_reviews} != {
        "semantic",
        "adversarial",
    }:
        failures.append("technical_independent_review_modes_invalid")
    reviewer_model_id = str(independent_review.get("reviewerModelId") or "").strip()
    if reviewer_model_id != str(model_synthesis.get("reviewerModelId") or "").strip():
        failures.append("technical_reviewer_identity_binding_mismatch")
    expected_binding = build_research_review_binding(
        bundle,
        reviewer_model_id=reviewer_model_id,
        reviewed_at=str(independent_review.get("reviewedAt") or ""),
    )
    if any(independent_review.get(key) != value for key, value in expected_binding.items()):
        failures.append("technical_independent_review_binding_mismatch")
    for review in consensus_reviews:
        nested_binding = build_research_review_binding(
            bundle,
            reviewer_model_id=str(review.get("reviewerModelId") or ""),
            reviewed_at=str(review.get("reviewedAt") or ""),
        )
        if any(review.get(key) != value for key, value in nested_binding.items()):
            failures.append("technical_consensus_review_binding_mismatch")
            break

    search_receipts = [
        shard
        for shard in list(bundle.get("shards") or [])
        if isinstance(shard, dict)
        and shard.get("ok") is True
        and str(shard.get("query") or "").strip()
        and int(shard.get("resultCount") or 0) > 0
        and str(shard.get("provider") or "").strip().lower() not in {"", "context7", "explicit_seed_url"}
        and str(shard.get("kind") or "").strip().lower() != "seed_url"
    ]
    if not search_receipts:
        failures.append("technical_live_search_receipt_missing")

    read_receipts = []
    for source in list(bundle.get("sourceMatrix") or []):
        if not isinstance(source, dict):
            continue
        gate = source.get("sourceQualityGate") if isinstance(source.get("sourceQualityGate"), dict) else {}
        if source.get("selectedForEvidence") is not True and gate.get("selectedForEvidence") is not True:
            continue
        receipt = source.get("readEvidence") if isinstance(source.get("readEvidence"), dict) else {}
        digest = str(receipt.get("contentSha256") or "").strip().lower()
        if (
            receipt.get("verified") is True
            and int(receipt.get("contentChars") or 0) == int(source.get("contentChars") or 0)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
            and str(receipt.get("retrievedAt") or "").strip()
        ):
            read_receipts.append(receipt)
    delivery_requirements = (
        bundle.get("deliveryRequirements")
        if isinstance(bundle.get("deliveryRequirements"), dict)
        else {}
    )
    try:
        target_sources = int(
            delivery_requirements.get("targetSources")
            or TARGET_RESEARCH_SOURCE_COUNT
        )
    except (TypeError, ValueError):
        target_sources = TARGET_RESEARCH_SOURCE_COUNT
    target_sources = max(1, target_sources)
    if len(read_receipts) < target_sources:
        failures.append(
            f"technical_live_read_receipts_below_target:{len(read_receipts)}/{target_sources}"
        )
    return list(dict.fromkeys(failures))


def _pure_research_semantic_failures(payload: dict[str, Any]) -> list[str]:
    answer_pack = payload.get("researchAnswerPack") if isinstance(payload.get("researchAnswerPack"), dict) else {}
    answer = str(answer_pack.get("answer") or payload.get("answer") or "")
    product_groups = {
        "codex_cli": r"Codex CLI|OpenAI Codex",
        "claude_code": r"Claude Code",
        "gemini_cli": r"Gemini CLI",
        "github_copilot_cli": r"GitHub Copilot CLI|Copilot CLI",
    }
    decision_groups = {
        "windows": r"Windows|PowerShell|WSL",
        "tools_extensions": r"MCP|工具调用|扩展|extension|tool use|tool calling",
        "repository_workflow": r"代码库|仓库|repository|codebase|git",
        "account_price": r"账号|订阅|价格|费用|pricing|subscription|account",
        "privacy_limits": r"隐私|数据|局限|限制|privacy|data|limitation",
        "recommendation": r"适合|推荐|选型|建议|choose|recommend",
    }
    failures: list[str] = []
    missing_products = [
        name
        for name, pattern in product_groups.items()
        if not re.search(pattern, answer, re.IGNORECASE)
    ]
    if missing_products:
        failures.append(
            "pure_research_product_coverage_missing:" + ",".join(missing_products)
        )
    covered_decisions = [
        name
        for name, pattern in decision_groups.items()
        if re.search(pattern, answer, re.IGNORECASE)
    ]
    if len(covered_decisions) < 4:
        failures.append(
            f"pure_research_decision_coverage_below_target:{len(covered_decisions)}/6"
        )
    if not re.search(r"OpenAI|Anthropic|Google|GitHub", answer, re.IGNORECASE):
        failures.append("pure_research_primary_authority_not_explained")
    return failures


def _compact_delivery_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    answer_pack = payload.get("researchAnswerPack") if isinstance(payload.get("researchAnswerPack"), dict) else {}
    score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
    metrics = score.get("acceptanceMetrics") if isinstance(score.get("acceptanceMetrics"), dict) else {}
    answer = str(answer_pack.get("answer") or payload.get("answer") or "")
    sources = [item for item in list(answer_pack.get("sources") or []) if isinstance(item, dict)]
    return {
        "evidenceBundleId": payload.get("evidenceBundleId"),
        "deliveryReady": payload.get("deliveryReady") or score.get("deliveryReady"),
        "qualityTier": payload.get("qualityTier") or score.get("qualityTier"),
        "reviewDecision": payload.get("reviewDecision") or answer_pack.get("reviewDecision"),
        "effectiveAnswerChars": metrics.get("effectiveAnswerChars"),
        "selectedSourceCount": metrics.get("selectedSourceCount"),
        "distinctHostCount": metrics.get("distinctHostCount"),
        "datedSourceCount": metrics.get("datedSourceCount"),
        "uniqueClaimCount": metrics.get("uniqueClaimCount"),
        "answerCitedSourceCount": metrics.get("answerCitedSourceCount"),
        "independentReviewAccepted": metrics.get("independentReviewAccepted"),
        "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest() if answer else None,
        "sourceUrls": [str(source.get("url") or "") for source in sources],
        "experienceReuse": payload.get("experienceReuse"),
    }


def _run_technical_case() -> AuditCaseResult:
    result = AuditCaseResult("technical", "公开技术问题：Source Router + 全文读取 + claimTable")
    started = time.perf_counter()
    try:
        payload = _research_run(
            "What are the current best practices for using Python pathlib in CLI tools? cite official sources.",
            freshness="latest",
            force_refresh=True,
        )
        persisted_bundle = _persisted_research_bundle(payload)
        final_pack = payload.get("finalExperiencePack") if isinstance(payload.get("finalExperiencePack"), dict) else {}
        synthesis_mode = str(final_pack.get("synthesisMode") or "")
        result.failures.extend(_high_quality_delivery_failures(payload))
        result.failures.extend(_technical_runtime_failures(payload, persisted_bundle))
        result.status = "ok" if not result.failures else "failed"
        result.summary = f"{str(payload.get('summary') or '')[:520]} synthesis={synthesis_mode or 'unknown'}"
        result.providers = sorted({str(item.get("provider") or "") for item in payload.get("sourceMatrix") or [] if isinstance(item, dict) and item.get("provider")})
        if synthesis_mode != "model_agent":
            result.status = "failed"
            result.failures.append(f"architect_agent_not_used:{synthesis_mode or 'unknown'}")
        result.evidence.append(_redact(payload))
        result.evidence.append(_redact(_persisted_research_diagnostic(payload, bundle=persisted_bundle)))
    except Exception as exc:  # noqa: BLE001 - live audit reports diagnostic failure.
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_cn_case() -> AuditCaseResult:
    result = AuditCaseResult("cn", "中文国内问题：CN/global provider 自动切换与降级")
    started = time.perf_counter()
    try:
        payload = _research_run("截至目前，秘塔搜索 API 的 search scope 支持哪些类型？请比较官方说明、限制与可验证案例。", freshness="current")
        result.failures.extend(_high_quality_delivery_failures(payload))
        result.status = "ok" if not result.failures else "failed"
        result.summary = str(payload.get("summary") or payload.get("answer") or "")[:600]
        routes = {str(item.get("networkRoute") or "") for item in payload.get("sourceMatrix") or [] if isinstance(item, dict)}
        if not routes and not payload.get("providerAttemptMatrix") and not payload.get("deliveryReady"):
            result.failures.append("missing_network_route_or_provider_attempts")
        result.evidence.append(_redact(payload))
        result.evidence.append(_redact(_persisted_research_diagnostic(payload)))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_domestic_delivery_case() -> AuditCaseResult:
    result = AuditCaseResult(
        "domestic_delivery",
        "仅依赖国内可达网络：检索、正文证据、模型综合与最低合格交付",
    )
    started = time.perf_counter()
    try:
        from core.tools.research_quality import research_acceptance_issues
        from core.tools import web_fetcher
        from core.tools.research_broker import _research_source_matches_root_subject

        domestic_providers = ("bing_cn", "metaso", "baidu")
        domestic_question = (
            "依据中国政府公开资料，概括《生成式人工智能服务管理暂行办法》对面向境内公众提供"
            "生成式人工智能服务的适用范围、训练数据与个人信息、生成内容治理与标识、"
            "安全评估或算法备案、用户权益和投诉举报分别提出了什么要求。"
            "请明确区分法规或官方解读中的事实与面向产品团队的实施建议。"
        )
        original_provider_order = web_fetcher._configured_source_provider_order
        web_fetcher._configured_source_provider_order = lambda _locale: list(domestic_providers)
        try:
            payload = _research_run(
                domestic_question,
                source_policy="authoritative",
                freshness="current",
                max_shards=8,
                max_rounds=2,
                force_refresh=True,
            )
        finally:
            web_fetcher._configured_source_provider_order = original_provider_order
        persisted_bundle = _persisted_research_bundle(payload)
        answer_pack = (
            payload.get("researchAnswerPack")
            if isinstance(payload.get("researchAnswerPack"), dict)
            else {}
        )
        score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
        route_rows = [
            *list((persisted_bundle or payload).get("sourceMatrix") or []),
            *list((persisted_bundle or payload).get("shards") or []),
        ]
        routes = {
            str(item.get("networkRoute") or "")
            for item in route_rows
            if isinstance(item, dict) and item.get("networkRoute")
        }
        providers = {
            str(item.get("provider") or "")
            for item in route_rows
            if isinstance(item, dict)
            and str(item.get("provider") or "").strip().lower()
            not in {"", "explicit_seed_url", "context7"}
        }
        validation_payload = persisted_bundle if isinstance(persisted_bundle, dict) else payload
        result.failures.extend(research_acceptance_issues(validation_payload))
        if payload.get("deliveryReady") is not True:
            result.failures.append("domestic_research_not_delivery_ready")
        if not str(answer_pack.get("answer") or "").strip():
            result.failures.append("domestic_research_answer_missing")
        if "cn_direct" not in routes and not providers.intersection({"bing_cn", "metaso", "baidu"}):
            result.failures.append("domestic_source_route_not_observed")
        unexpected_providers = sorted(providers.difference(domestic_providers))
        if unexpected_providers:
            result.failures.append(
                "non_domestic_provider_selected:" + ",".join(unexpected_providers)
            )
        selected_sources = [
            item
            for item in list(validation_payload.get("sourceMatrix") or [])
            if isinstance(item, dict)
            and (
                item.get("selectedForEvidence") is True
                or bool((item.get("sourceQualityGate") or {}).get("selectedForEvidence"))
            )
        ]
        off_topic_hosts = sorted(
            {
                str(item.get("host") or "unknown")
                for item in selected_sources
                if not _research_source_matches_root_subject(
                    domestic_question,
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("snippet") or ""),
                )
            }
        )
        if off_topic_hosts:
            result.failures.append(
                "domestic_selected_source_subject_mismatch:" + ",".join(off_topic_hosts)
            )
        result.status = "ok" if not result.failures else "failed"
        result.providers = sorted(providers)
        result.summary = (
            f"quality={score.get('qualityTier') or payload.get('qualityTier')} "
            f"sources={((score.get('acceptanceMetrics') or {}).get('selectedSourceCount'))} "
            f"routes={','.join(sorted(routes)) or 'none'}"
        )
        result.evidence.append(_redact(_compact_delivery_evidence(payload)))
        result.evidence.append(
            _redact(
                _persisted_research_diagnostic(
                    payload,
                    bundle=persisted_bundle,
                )
            )
        )
    except Exception as exc:  # noqa: BLE001 - live audit reports diagnostic failure.
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_continuation_case() -> AuditCaseResult:
    result = AuditCaseResult("continuation", "长页面读取：raw_ref 续读稳定")
    started = time.perf_counter()
    try:
        from langchain_core.messages import ToolMessage

        from core.native_tools import tool_observation_detail
        from core.tool_surface import apply_tool_surface_budget
        from core.tools.web_fetcher import web_read

        raw = web_read.func(url="https://docs.python.org/3/library/pathlib.html", mode="static", tool_call_id="live-research-continuation")
        message = ToolMessage(content=raw, tool_call_id="live-research-continuation", name="web_read")
        visible = apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 900, "contextWindowTokens": 16000},
            tool_name="web_read",
            runtime_kind="research",
        )
        content = str(visible.content)
        match = re.search(r"tool_observation_detail\(raw_ref='([^']+)'", content)
        if not match:
            result.status = "failed"
            result.failures.append("visible_surface_missing_tool_observation_detail")
            result.evidence.append(_redact(content[:1600]))
        else:
            detail = tool_observation_detail.invoke({"raw_ref": match.group(1), "max_chars": 8000})
            result.status = "ok" if "pathlib" in str(detail).lower() else "warning"
            result.evidence.append(_redact({"visible": content[:1600], "detailPreview": str(detail)[:3000]}))
            if result.status != "ok":
                result.failures.append("detail_did_not_include_expected_page_content")
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_reuse_case() -> AuditCaseResult:
    result = AuditCaseResult("reuse", "同一高质量纯调研：经验包必须直接复用且证据绑定一致")
    started = time.perf_counter()
    try:
        first = _research_run(
            PURE_RESEARCH_QUESTION,
            freshness="current",
            max_shards=20,
            force_refresh=True,
        )
        second = _research_run(PURE_RESEARCH_QUESTION, freshness="current", max_shards=20)
        reuse = second.get("experienceReuse") or {}
        result.failures.extend(f"first:{item}" for item in _high_quality_delivery_failures(first))
        result.failures.extend(f"second:{item}" for item in _high_quality_delivery_failures(second))
        result.failures.extend(f"first:{item}" for item in _pure_research_semantic_failures(first))
        result.failures.extend(f"second:{item}" for item in _pure_research_semantic_failures(second))
        first_evidence = _compact_delivery_evidence(first)
        second_evidence = _compact_delivery_evidence(second)
        if reuse.get("reuseDecision") != "reuse":
            result.failures.append("second_run_did_not_reuse_high_quality_experience")
        if first_evidence.get("answerSha256") != second_evidence.get("answerSha256"):
            result.failures.append("reused_answer_binding_changed")
        if first_evidence.get("sourceUrls") != second_evidence.get("sourceUrls"):
            result.failures.append("reused_source_binding_changed")
        result.status = "ok" if not result.failures else "failed"
        result.summary = f"reuseDecision={reuse.get('reuseDecision')} reason={reuse.get('reason')}"
        result.evidence.append(_redact({"first": first_evidence, "second": second_evidence}))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_pure_research_case() -> AuditCaseResult:
    result = AuditCaseResult("pure", "纯调研：时效政策问题的完整高质量用户答案")
    started = time.perf_counter()
    try:
        payload = _research_run(
            PURE_RESEARCH_QUESTION,
            freshness="current",
            max_shards=20,
            force_refresh=True,
        )
        result.failures.extend(_high_quality_delivery_failures(payload))
        result.failures.extend(_pure_research_semantic_failures(payload))
        result.status = "ok" if not result.failures else "failed"
        compact = _compact_delivery_evidence(payload)
        result.summary = (
            f"quality={compact.get('qualityTier')} chars={compact.get('effectiveAnswerChars')} "
            f"sources={compact.get('selectedSourceCount')} claims={compact.get('uniqueClaimCount')}"
        )
        result.evidence.append(_redact(compact))
        result.evidence.append(_redact(_persisted_research_diagnostic(payload)))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_memory_route_research_experience_case() -> AuditCaseResult:
    result = AuditCaseResult("memory_route_research_experience", "Memory route：Research Experience 答案卷宗")
    started = time.perf_counter()
    try:
        from core.native_tools import memory_broker

        question = "Research Runtime answer pack memory route validation"
        research_payload = _research_run(question)
        result.failures.extend(_high_quality_delivery_failures(research_payload))
        payload = json.loads(memory_broker.func(mode="route", query=f"之前调研过 {question} 吗", scope="global", limit=3))
        packs = payload.get("evidencePacks") if isinstance(payload.get("evidencePacks"), list) else []
        research_pack = next((pack for pack in packs if isinstance(pack, dict) and pack.get("sourceDomain") == "research_experience"), {})
        selected = research_pack.get("selectedEvidence") if isinstance(research_pack.get("selectedEvidence"), list) else []
        result.status = "ok" if not result.failures and selected and selected[0].get("answer") and len(selected[0].get("sources") or []) >= 8 else "failed"
        result.summary = f"selectedDomains={payload.get('selectedDomains')} selectedResearch={len(selected)}"
        if not selected:
            result.failures.append("missing_research_experience_selected_evidence")
        elif not selected[0].get("answer"):
            result.failures.append("selected_research_evidence_missing_answer")
        result.evidence.append(_redact(payload))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_runtime_broker_list_compact_case() -> AuditCaseResult:
    result = AuditCaseResult("runtime_broker_list_compact", "runtime_broker(list)：默认路由菜单降噪")
    started = time.perf_counter()
    try:
        from core.native_tools import runtime_broker

        payload = json.loads(
            runtime_broker.func(
                mode="list",
                state={"current_route_context": {}},
                tool_call_id="live-runtime-broker-list-compact",
            ).update["messages"][0].content
        )
        groups = payload.get("availableGroups") if isinstance(payload.get("availableGroups"), list) else []
        serialized = json.dumps(payload, ensure_ascii=False)
        group_has_tool_names = any(isinstance(group, dict) and "toolNames" in group for group in groups)
        result.status = "ok" if len(serialized) < 1800 and len(groups) <= 6 and not group_has_tool_names else "warning"
        result.summary = f"bytes={len(serialized)} groups={len(groups)} detailMode={payload.get('detailMode')}"
        if len(serialized) >= 1800:
            result.failures.append("runtime_broker_list_too_large")
        if len(groups) > 6:
            result.failures.append("runtime_broker_list_too_many_groups")
        if group_has_tool_names:
            result.failures.append("runtime_broker_list_contains_tool_names")
        result.evidence.append(_redact(payload))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_low_quality_source_gate_case() -> AuditCaseResult:
    result = AuditCaseResult("low_quality_source_gate", "低质量来源：captcha/footer/snippet 被拒绝")
    started = time.perf_counter()
    try:
        from core.tools.research_broker import _research_answer_pack, _source_quality_gate

        source = {
            "title": "Security check required",
            "url": "https://www.youtube.com/watch?v=noisy",
            "snippet": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
        }
        read_payload = {
            "ok": True,
            "title": "YouTube footer",
            "text": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
        }
        gate = _source_quality_gate(question="low quality gate validation", result=source, read_payload=read_payload, source_policy="authoritative")
        pack = _research_answer_pack(
            {
                "researchEvidenceBank": {
                    "selectedSources": [],
                    "rejectedSources": [
                        {
                            "title": source["title"],
                            "url": source["url"],
                            "reason": gate.get("rejectedReason"),
                            "qualityDimensions": gate.get("qualityDimensions"),
                        }
                    ],
                    "claims": [],
                    "stats": {"selectedSourceCount": 0, "rejectedSourceCount": 1, "claimCount": 0},
                },
                "sourceMatrix": [],
                "finalExperiencePack": {"researchResult": "No reliable source-backed findings were collected."},
            }
        )
        result.status = "ok" if not gate.get("selectedForEvidence") and pack.get("score", {}).get("qualityStatus") == "insufficient" else "failed"
        result.summary = f"selected={gate.get('selectedForEvidence')} reason={gate.get('rejectedReason')} quality={pack.get('score', {}).get('qualityStatus')}"
        if gate.get("selectedForEvidence"):
            result.failures.append("noisy_source_passed_quality_gate")
        if pack.get("score", {}).get("qualityStatus") != "insufficient":
            result.failures.append("low_quality_pack_not_marked_refresh_required")
        result.evidence.append(_redact({"gate": gate, "answerPack": pack}))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_admin_hover_agent_surface_case() -> AuditCaseResult:
    result = AuditCaseResult("admin_hover_agent_surface", "Admin hover：使用 agent-visible answer/sources/score")
    started = time.perf_counter()
    try:
        payload = _research_run("ResearchAnswerPack admin hover field consistency validation")
        answer_pack = payload.get("researchAnswerPack") if isinstance(payload.get("researchAnswerPack"), dict) else {}
        required = {
            "answer": bool(answer_pack.get("answer")),
            "sources": len(answer_pack.get("sources") or []) >= 8,
            "score": bool(answer_pack.get("score")),
        }
        result.failures.extend(_high_quality_delivery_failures(payload))
        result.status = "ok" if all(required.values()) and not result.failures else "failed"
        result.summary = f"hoverFields={required} quality={((answer_pack.get('score') or {}).get('qualityStatus') or 'unknown')}"
        for key, ok in required.items():
            if not ok:
                result.failures.append(f"missing_hover_field:{key}")
        result.evidence.append(_redact({"researchAnswerPack": answer_pack, "evidenceBundleId": payload.get("evidenceBundleId")}))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_conflict_case() -> AuditCaseResult:
    result = AuditCaseResult("conflict", "冲突来源：conflictMatrix 与不确定性表达")
    started = time.perf_counter()
    try:
        payload = _research_run(
            "截至目前，Python pathlib 是否被弃用，还是仍被官方推荐？比较官方文档、版本证据、限制与相反说法。",
            freshness="current",
        )
        result.failures.extend(_high_quality_delivery_failures(payload))
        result.status = "ok" if not result.failures else "failed"
        result.summary = str(payload.get("summary") or "")[:600]
        result.evidence.append(_redact(payload))
        result.evidence.append(_redact(_persisted_research_diagnostic(payload)))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


CASES = {
    "technical": _run_technical_case,
    "cn": _run_cn_case,
    "domestic_delivery": _run_domestic_delivery_case,
    "pure": _run_pure_research_case,
    "continuation": _run_continuation_case,
    "reuse": _run_reuse_case,
    "memory_route_research_experience": _run_memory_route_research_experience_case,
    "runtime_broker_list_compact": _run_runtime_broker_list_compact_case,
    "low_quality_source_gate": _run_low_quality_source_gate_case,
    "admin_hover_agent_surface": _run_admin_hover_agent_surface_case,
    "conflict": _run_conflict_case,
}


def _write_report(results: list[AuditCaseResult], output_root: Path) -> Path:
    report_dir = output_root / "research_runtime_deep" / datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "RESEARCH_RUNTIME_DEEP_LIVE_AUDIT_ZH.md"
    lines = [
        "# Research Runtime Deep Live Audit",
        "",
        f"- generatedAt: {datetime.now().isoformat()}",
        f"- cases: {len(results)}",
        "",
    ]
    for item in results:
        lines.extend(
            [
                f"## {item.case_id} - {item.title}",
                "",
                f"- status: {item.status}",
                f"- elapsedMs: {item.elapsed_ms}",
                f"- providers: {', '.join(item.providers) if item.providers else 'n/a'}",
                f"- summary: {item.summary or 'n/a'}",
            ]
        )
        if item.failures:
            lines.append(f"- failures: {'; '.join(item.failures)}")
        lines.append("")
        lines.append("<details><summary>Evidence</summary>")
        lines.append("")
        for evidence in item.evidence:
            lines.append("```json")
            lines.append(evidence)
            lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Research Runtime deep audit.")
    parser.add_argument("--live", action="store_true", help="Required to perform network/provider live calls.")
    parser.add_argument("--case", choices=[*CASES.keys(), "all"], default="all")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()
    if not args.live:
        print("Refusing to run live audit without --live.")
        return 2
    selected = list(CASES.keys()) if args.case == "all" else [args.case]
    results = [CASES[case_id]() for case_id in selected]
    for item in results:
        print(f"[{item.status}] {item.case_id}: {item.summary or '; '.join(item.failures) or item.title}")
    if args.write_report:
        path = _write_report(results, args.output_dir)
        print(f"report={path}")
    return 1 if any(item.status != "ok" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
