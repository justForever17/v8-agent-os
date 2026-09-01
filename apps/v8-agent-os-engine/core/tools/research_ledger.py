from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.tools.research_quality import (
    research_acceptance_metrics,
    research_as_of,
    research_bundle_is_high_quality,
    research_high_quality_issues,
    research_quality_tier,
    research_requires_dated_sources,
    research_review_decision,
    research_selected_sources,
)
from core.v8_agent_os_paths import runtime_private_root


_LOCK = threading.RLock()
_VERSION = 1
_DEFAULT_EXPERIENCE_MAX_AGE_DAYS = 180
_FRESHNESS_WARNING_RATIO = 0.75
_EXPERIENCE_SOURCE_FLOOR = 8
_EXPERIENCE_SOURCE_DIGEST_LIMIT = 16


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _ledger_path() -> Path:
    override = os.getenv("V8_RESEARCH_LEDGER_PATH")
    if override:
        return Path(override).expanduser()
    return runtime_private_root("research") / "research_ledger.json"


def _empty_store() -> dict[str, Any]:
    return {"version": _VERSION, "evidenceBundles": [], "experiencePacks": []}


def _read_store() -> dict[str, Any]:
    path = _ledger_path()
    if not path.exists():
        return _empty_store()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    if not isinstance(payload, dict):
        return _empty_store()
    payload.setdefault("version", _VERSION)
    payload.setdefault("evidenceBundles", [])
    payload.setdefault("experiencePacks", [])
    if not isinstance(payload["evidenceBundles"], list):
        payload["evidenceBundles"] = []
    if not isinstance(payload["experiencePacks"], list):
        payload["experiencePacks"] = []
    return payload


def _write_store(payload: dict[str, Any]) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _visible(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not str(key).startswith("_")}


def _parse_datetime(value: Any) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness_max_age_days(value: Any) -> int:
    if isinstance(value, dict):
        for key in ("maxAgeDays", "max_age_days", "days"):
            try:
                if value.get(key) is not None:
                    return max(1, min(int(value.get(key)), 3650))
            except (TypeError, ValueError):
                continue
        value = value.get("value") or value.get("window") or value.get("policy")
    text = _safe_text(value).lower()
    match = re.fullmatch(r"(\d+)\s*(h|hr|hour|hours|d|day|days|w|week|weeks|m|month|months|y|year|years)", text)
    if match:
        amount = max(1, int(match.group(1)))
        unit = match.group(2)
        if unit.startswith("h"):
            return max(1, math.ceil(amount / 24))
        if unit.startswith("w"):
            return min(amount * 7, 3650)
        if unit.startswith("m"):
            return min(amount * 30, 3650)
        if unit.startswith("y"):
            return min(amount * 365, 3650)
        return min(amount, 3650)
    if text in {"latest", "current", "fresh", "实时", "最新"}:
        return 7
    if text in {"recent", "近期"}:
        return 30
    if text in {"stable", "evergreen", "长期", "常青"}:
        return 365
    return _DEFAULT_EXPERIENCE_MAX_AGE_DAYS


def _experience_freshness(item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    checked_at = now or datetime.now(timezone.utc)
    source_at = (
        _parse_datetime(item.get("evidenceCheckedAt"))
        or _parse_datetime(item.get("researchedAt"))
        or _parse_datetime(item.get("createdAt"))
        or checked_at
    )
    max_age_days = _freshness_max_age_days(item.get("freshnessWindow"))
    age_days = max(0, int((checked_at - source_at).total_seconds() // 86400))
    stale_at = source_at + timedelta(days=max_age_days)
    expires_at = source_at + timedelta(days=max_age_days * 2)
    if checked_at >= expires_at:
        state = "expired"
    elif checked_at >= stale_at:
        state = "stale"
    elif age_days >= max(1, math.floor(max_age_days * _FRESHNESS_WARNING_RATIO)):
        state = "aging"
    else:
        state = "current"
    return {
        "freshnessState": state,
        "ageDays": age_days,
        "maxAgeDays": max_age_days,
        "staleAt": stale_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
    }


def _visible_experience(item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    visible = {**_visible(item), **_experience_freshness(item, now=now)}
    acceptance_issues = _experience_acceptance_issues(item)
    accepted = not acceptance_issues
    stored_status = _safe_text(item.get("status")).lower() or "draft"
    visible["qualityAccepted"] = accepted
    visible["reuseEligible"] = bool(
        accepted
        and stored_status == "active"
        and visible.get("freshnessState") not in {"stale", "expired"}
    )
    if not accepted:
        effective_status = "archived" if stored_status == "archived" else "draft"
        visible["storedStatus"] = stored_status
        visible["status"] = effective_status
        visible["effectiveStatus"] = effective_status
        visible["qualityStatus"] = "refresh_required"
        existing = _safe_text(visible.get("invalidationReason"))
        visible["invalidationReason"] = ", ".join(
            dict.fromkeys([part for part in (existing, *acceptance_issues) if part])
        )
    else:
        visible["effectiveStatus"] = stored_status
    return visible


def _not_expired(item: dict[str, Any], now: float | None = None) -> bool:
    expires_at = item.get("_expiresAt")
    if not expires_at:
        return True
    try:
        return float(expires_at) >= float(now if now is not None else time.time())
    except Exception:
        return True


def _prune_expired(payload: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    payload["evidenceBundles"] = [item for item in _as_list(payload.get("evidenceBundles")) if isinstance(item, dict) and _not_expired(item, now)]
    payload["experiencePacks"] = [item for item in _as_list(payload.get("experiencePacks")) if isinstance(item, dict)]
    return payload


def _scope_matches(item: dict[str, Any], scope: str) -> bool:
    normalized_scope = _safe_text(scope) or "global"
    return normalized_scope == "global" or _safe_text(item.get("scope")) in {normalized_scope, "global"}


def _question_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[\s,.;:!?()\[\]{}<>/\\|\"'`~，。！？；：、（）【】]+", _safe_text(value).lower())
        if len(token) >= 2
    }


def _topic_fingerprint(value: str) -> str:
    normalized = re.sub(r"\s+", " ", _safe_text(value).lower())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _source_retrieved_at(item: dict[str, Any]) -> str:
    temporal = item.get("temporalEvidence") if isinstance(item.get("temporalEvidence"), dict) else {}
    return _safe_text(item.get("retrievedAt") or item.get("fetchedAt") or temporal.get("retrievedAt"))


def _source_has_reuse_metadata(item: dict[str, Any]) -> bool:
    return bool(
        _safe_text(item.get("citationKey"))
        and _source_retrieved_at(item)
    )


def _source_digest(bundle: dict[str, Any], *, limit: int = _EXPERIENCE_SOURCE_DIGEST_LIMIT) -> list[dict[str, Any]]:
    digest: list[dict[str, Any]] = []
    for item in research_selected_sources(bundle)[:limit]:
        if not isinstance(item, dict):
            continue
        temporal = item.get("temporalEvidence") if isinstance(item.get("temporalEvidence"), dict) else {}
        source = {
            key: value
            for key, value in {
                "sourceId": item.get("sourceId"),
                "citationKey": item.get("citationKey"),
                "title": item.get("title"),
                "url": item.get("url") or item.get("sourceUrl"),
                "host": item.get("host"),
                "authorityScore": item.get("authorityScore"),
                "tier": item.get("tier"),
                "authorityTier": item.get("authorityTier"),
                "selectedForEvidence": True,
                "retrievedAt": _source_retrieved_at(item),
                "publishedAt": item.get("publishedAt") or temporal.get("publishedAt"),
                "updatedAt": item.get("updatedAt") or temporal.get("updatedAt"),
                "sourceDate": item.get("sourceDate") or temporal.get("sourceDate"),
                "version": item.get("version") or temporal.get("version"),
                "contentChars": item.get("contentChars"),
                "readEvidence": item.get("readEvidence"),
                "temporalEvidence": temporal or None,
            }.items()
            if value not in (None, "", [], {})
        }
        digest.append(source)
    return digest


_GENERIC_RESEARCH_ANSWERS = {
    "evidence collected. use sourcematrix and fetchedtopsources to write the final answer.",
}


_LOW_QUALITY_RESEARCH_MARKERS = (
    "about press copyright contact us creators advertise developers terms privacy policy",
    "youtube works test new features",
    "security check required",
    "we've detected unusual activity",
    "安全验证",
    "captcha",
    "cloudflare",
    "access denied",
    "just a moment",
)


_RESEARCH_KIND_VALUES = {"research_question", "task_request", "spec_task", "runtime_handoff"}
_TASK_LIKE_KINDS = {"task_request", "spec_task"}
_DEFAULT_EXCLUDED_PACK_QUALITIES = {
    "draft",
    "low_quality_pack",
    "refresh_required",
    "missing_evidence",
    "source_read_failed",
    "source_unreadable",
}


def _normalize_research_kind(value: Any) -> str:
    text = _safe_text(value).lower().replace("-", "_")
    return text if text in _RESEARCH_KIND_VALUES else ""


def _infer_question_kind(question: Any, bundle: dict[str, Any] | None = None) -> str:
    bundle = bundle or {}
    explicit = _normalize_research_kind(bundle.get("questionKind"))
    if explicit:
        return explicit
    text = " ".join(
        _safe_text(value)
        for value in (
            question,
            bundle.get("question"),
            bundle.get("query"),
            bundle.get("title"),
            bundle.get("taskId"),
            bundle.get("specId"),
        )
        if _safe_text(value)
    ).lower()
    if re.search(r"\bspec[_ -]?[0-9a-f]{6,}\b", text) or re.search(r"\btask[-_ ]?\d{1,4}\b", text) or "approved spec" in text:
        return "spec_task"
    if any(marker in text for marker in ("task request", "execute task", "执行任务", "创建 skill 目录结构", "目录初始化", "approved task")):
        return "task_request"
    if any(marker in text for marker in ("runtime handoff", "episode handoff", "typed handoff", "运行回流")):
        return "runtime_handoff"
    return "research_question"


def _infer_source_kind(bundle: dict[str, Any]) -> str:
    explicit = _normalize_research_kind(bundle.get("sourceKind"))
    if explicit:
        return explicit
    question_kind = _infer_question_kind(bundle.get("question"), bundle)
    if question_kind in _TASK_LIKE_KINDS:
        return question_kind
    if any(_safe_text(bundle.get(key)) for key in ("episodeId", "runtimeEpisodeId", "handoffId", "createdFromEpisodeId")):
        return "runtime_handoff"
    return "research_question"


def _normalize_bundle_kinds(bundle: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(bundle)
    normalized["questionKind"] = _infer_question_kind(normalized.get("question"), normalized)
    normalized["sourceKind"] = _infer_source_kind(normalized)
    return normalized


def _experience_acceptance_issues(bundle: dict[str, Any]) -> list[str]:
    bundle = _normalize_bundle_kinds(bundle)
    issues: list[str] = []
    if bundle.get("questionKind") in _TASK_LIKE_KINDS or bundle.get("sourceKind") in _TASK_LIKE_KINDS:
        issues.append("task_kind_not_reusable")
    issues.extend(research_high_quality_issues(bundle))
    complete_sources = sum(1 for source in research_selected_sources(bundle) if _source_has_reuse_metadata(source))
    if complete_sources < _EXPERIENCE_SOURCE_FLOOR:
        issues.append(f"experience_source_metadata_floor_not_met:{_EXPERIENCE_SOURCE_FLOOR}")
    return list(dict.fromkeys(issues))


def _has_reusable_answer_pack(bundle: dict[str, Any]) -> bool:
    bundle = _normalize_bundle_kinds(bundle)
    if bundle.get("questionKind") in _TASK_LIKE_KINDS or bundle.get("sourceKind") in _TASK_LIKE_KINDS:
        return False
    if not research_bundle_is_high_quality(bundle):
        return False
    return sum(1 for source in research_selected_sources(bundle) if _source_has_reuse_metadata(source)) >= _EXPERIENCE_SOURCE_FLOOR


def _valid_research_text(value: Any, *, min_chars: int = 24) -> str:
    text = re.sub(r"\s+", " ", _safe_text(value)).strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _GENERIC_RESEARCH_ANSWERS:
        return ""
    if lowered.startswith("collected "):
        return ""
    if any(marker in lowered for marker in _LOW_QUALITY_RESEARCH_MARKERS):
        return ""
    if len(text) < min_chars:
        return ""
    return text


def _final_research_pack(bundle: dict[str, Any]) -> dict[str, Any]:
    for key in ("finalExperiencePack", "researchResult"):
        value = bundle.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _full_research_result(bundle: dict[str, Any]) -> str:
    final_pack = _final_research_pack(bundle)
    answer_pack = bundle.get("researchAnswerPack") if isinstance(bundle.get("researchAnswerPack"), dict) else {}
    candidates = (
        final_pack.get("researchResult"),
        answer_pack.get("answer"),
        final_pack.get("answer"),
        bundle.get("researchResult") if isinstance(bundle.get("researchResult"), str) else "",
        bundle.get("answer"),
    )
    for value in candidates:
        raw = _safe_text(value)
        if raw and _valid_research_text(raw):
            return raw
    return ""


def _review_reasons(bundle: dict[str, Any]) -> list[str]:
    final_pack = _final_research_pack(bundle)
    answer_pack = bundle.get("researchAnswerPack") if isinstance(bundle.get("researchAnswerPack"), dict) else {}
    review = bundle.get("researchReview") if isinstance(bundle.get("researchReview"), dict) else {}
    reasons: list[str] = []
    for values in (
        bundle.get("reviewReasons"),
        review.get("reviewReasons") or review.get("reasons"),
        answer_pack.get("reviewReasons"),
        final_pack.get("reviewReasons"),
    ):
        candidates = values if isinstance(values, list) else [values] if isinstance(values, str) else []
        for value in candidates:
            text = _safe_text(value)
            if text and text not in reasons:
                reasons.append(text)
    return reasons


def _quality_tier(bundle: dict[str, Any]) -> str:
    return research_quality_tier(bundle)


def _quality_metrics(bundle: dict[str, Any]) -> dict[str, Any]:
    final_pack = _final_research_pack(bundle)
    answer_pack = bundle.get("researchAnswerPack") if isinstance(bundle.get("researchAnswerPack"), dict) else {}
    review = bundle.get("researchReview") if isinstance(bundle.get("researchReview"), dict) else {}
    metrics: dict[str, Any] = {}
    for candidate in (
        final_pack.get("qualityMetrics"),
        answer_pack.get("qualityMetrics"),
        review.get("qualityMetrics"),
        bundle.get("qualityMetrics"),
    ):
        if isinstance(candidate, dict):
            metrics.update(candidate)
    metrics.update(research_acceptance_metrics(bundle))
    return metrics


def _claim_digest_from_pack(pack: dict[str, Any]) -> list[dict[str, Any]]:
    claim_digest: list[dict[str, Any]] = []
    for item in _as_list(pack.get("claimTable"))[:12]:
        if not isinstance(item, dict):
            continue
        claim = _valid_research_text(item.get("claim"), min_chars=8)
        if not claim:
            continue
        claim_digest.append(
            {
                "claim": claim[:320],
                "claimType": item.get("claimType") or item.get("claimKind"),
                "normativeCue": _safe_text(item.get("normativeCue"))[:160],
                "evidenceExcerptKey": item.get("evidenceExcerptKey"),
                "supportingSources": _as_list(item.get("supportingSources"))[:4],
                "confidence": item.get("confidence"),
                "evidenceExcerpt": _safe_text(item.get("evidenceExcerpt"))[:600],
                "evidenceExcerptSha256": item.get("evidenceExcerptSha256"),
                "evidenceVerified": item.get("evidenceVerified") is True,
            }
        )
    return claim_digest


def _research_result_preview(bundle: dict[str, Any], *, limit: int = 900) -> str:
    full_result = _full_research_result(bundle)
    if full_result:
        return full_result[:limit]
    final_pack = _final_research_pack(bundle)
    for key in ("researchResult", "answer", "result", "findings"):
        value = _valid_research_text(final_pack.get(key) or bundle.get(key))
        if value:
            return value[:limit]
    claims = _claim_digest_from_pack(final_pack) or _claim_digest_from_pack(bundle)
    if claims:
        return "；".join(_safe_text(item.get("claim")) for item in claims[:4] if item.get("claim"))[:limit]
    for key in ("resultPreview", "summary"):
        value = _valid_research_text(bundle.get(key))
        if value:
            return value[:limit]
    return ""


def _experience_from_bundle(bundle: dict[str, Any], *, status: str = "draft", title: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    bundle = _normalize_bundle_kinds(bundle)
    bundle_id = _safe_text(bundle.get("evidenceBundleId")) or f"research_{uuid.uuid4().hex[:12]}"
    source_digest = _source_digest(bundle)
    topic = _safe_text(title) or _safe_text(bundle.get("question"))[:120] or "Research experience"
    pack_id = f"rxp_{uuid.uuid5(uuid.NAMESPACE_URL, bundle_id).hex[:16]}"
    created_at = _utc_now_iso()
    result_preview = _research_result_preview(bundle)
    final_pack = _final_research_pack(bundle)
    answer_pack = dict(bundle.get("researchAnswerPack")) if isinstance(bundle.get("researchAnswerPack"), dict) else {}
    research_result = _full_research_result(bundle)
    reviewed_sources = [
        dict(source)
        for source in research_selected_sources(bundle)
        if isinstance(source, dict)
    ]
    if reviewed_sources:
        # sourceMatrixDigest is intentionally compact, but the governed answer
        # pack must retain the exact source set bound into its Reviewer receipt.
        # Truncating that set makes an accepted pack fail its own revalidation.
        answer_pack["sources"] = reviewed_sources
    source_urls = [
        _safe_text(source.get("url"))
        for source in source_digest
        if _safe_text(source.get("url"))
    ]
    claim_digest = (
        _claim_digest_from_pack({"claimTable": bundle.get("claimTable")})
        or _claim_digest_from_pack(answer_pack)
        or _claim_digest_from_pack(final_pack)
    )
    missing_evidence = []
    for value in _as_list(final_pack.get("missingEvidence") or answer_pack.get("missingEvidence") or bundle.get("missingEvidence")):
        text = _safe_text(value)
        if text:
            missing_evidence.append(text[:260])
    limitations = []
    for value in _as_list(final_pack.get("limitations") or answer_pack.get("limitations") or bundle.get("limitations") or bundle.get("assumptions")):
        text = _safe_text(value)
        if text:
            limitations.append(text[:260])
    quality_reasons = _experience_acceptance_issues(bundle)
    accepted = not quality_reasons
    requested_status = _safe_text(status).lower() or "draft"
    effective_status = requested_status if requested_status in {"archived", "draft"} else ("active" if accepted else "draft")
    rejected_evidence = _as_list(answer_pack.get("rejectedEvidence"))
    source_quality_score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
    if not result_preview and not claim_digest:
        missing_evidence.append("No reliable source-backed research result was synthesized.")
    quality_status = "high_quality" if accepted else "refresh_required"
    topic_fingerprint = _safe_text(bundle.get("topicFingerprint"))
    if not topic_fingerprint:
        normalized_topic = re.sub(r"\s+", " ", _safe_text(bundle.get("question") or topic).lower()).strip()
        topic_fingerprint = uuid.uuid5(uuid.NAMESPACE_URL, normalized_topic).hex[:16]
    review_decision = research_review_decision(bundle)
    review_reasons = _review_reasons(bundle)
    independent_review = (
        dict(bundle.get("independentReview"))
        if isinstance(bundle.get("independentReview"), dict)
        else dict(final_pack.get("independentReview"))
        if isinstance(final_pack.get("independentReview"), dict)
        else dict(answer_pack.get("independentReview"))
        if isinstance(answer_pack.get("independentReview"), dict)
        else {}
    )
    as_of = research_as_of(bundle)
    quality_metrics = _quality_metrics(bundle)
    quality_tier = _quality_tier(bundle)
    applicability = _safe_text(bundle.get("deliverable") or bundle.get("researchIntent") or "general_research")[:240]
    requested_freshness = _safe_text(bundle.get("freshness")) or "auto"
    effective_freshness = requested_freshness
    if requested_freshness.lower() in {"", "auto"} and research_requires_dated_sources(bundle):
        effective_freshness = "current"
    return {
        "experiencePackId": pack_id,
        "status": effective_status,
        "title": topic,
        "query": bundle.get("question"),
        "summary": result_preview[:600],
        "resultPreview": result_preview,
        "applicability": applicability,
        "researchResult": research_result,
        "researchAnswerPack": answer_pack,
        "reviewDecision": review_decision,
        "reviewReasons": review_reasons,
        "independentReview": independent_review,
        "asOf": as_of,
        "qualityMetrics": quality_metrics,
        "qualityTier": quality_tier,
        "briefCoverageRequired": bundle.get("briefCoverageRequired") is True,
        "briefCoverageComplete": bundle.get("briefCoverageComplete") is True,
        "briefCoverage": [
            dict(item)
            for item in _as_list(bundle.get("briefCoverage"))
            if isinstance(item, dict)
        ],
        "coveredTaskBriefIds": _as_list(bundle.get("coveredTaskBriefIds")),
        "missingTaskBriefIds": _as_list(bundle.get("missingTaskBriefIds")),
        "claimDigest": claim_digest,
        "qualityStatus": quality_status,
        "questionKind": bundle.get("questionKind") or "research_question",
        "sourceKind": bundle.get("sourceKind") or "research_question",
        "sourceQualityScore": source_quality_score,
        "rejectedEvidence": rejected_evidence[:8],
        "topicFingerprint": topic_fingerprint,
        "sourcePolicy": bundle.get("sourcePolicy"),
        "freshness": requested_freshness,
        "freshnessWindow": effective_freshness,
        "requestedFreshness": requested_freshness,
        "sourceUrls": source_urls[:16],
        "reusableExperiencePack": {
            "summary": result_preview[:900] if accepted else "",
            "researchResult": research_result if accepted else "",
            "applicability": applicability,
            "sourceUrls": source_urls[:16],
            "sourceMatrixDigest": source_digest,
            "claimDigest": claim_digest,
            "reviewDecision": review_decision,
            "reviewReasons": review_reasons,
            "independentReview": independent_review,
            "asOf": as_of,
            "qualityMetrics": quality_metrics,
            "qualityTier": quality_tier,
            "briefCoverageRequired": bundle.get("briefCoverageRequired") is True,
            "briefCoverageComplete": bundle.get("briefCoverageComplete") is True,
        },
        "invalidationReason": ", ".join(quality_reasons),
        "missingEvidence": missing_evidence[:8],
        "limitations": limitations[:8],
        "confidence": bundle.get("confidence"),
        "authorityScore": bundle.get("authorityScore"),
        "confidenceTimeline": [
            {
                "at": created_at,
                "confidence": bundle.get("confidence"),
                "authorityScore": bundle.get("authorityScore"),
                "evidenceBundleId": bundle_id,
            }
        ],
        "sourceMatrixDigest": source_digest,
        "createdFromBundleId": bundle_id,
        "evidenceCheckedAt": _safe_text(bundle.get("completedAt") or as_of or bundle.get("createdAt")) or created_at,
        "createdAt": created_at,
        "updatedAt": created_at,
        "lastUsedAt": None,
        "usageCount": 0,
        "tags": list(tags or []),
        "scope": bundle.get("scope") or "global",
    }


def store_evidence_bundle(bundle: dict[str, Any], *, ttl_seconds: int, scope: str) -> dict[str, Any]:
    with _LOCK:
        payload = _prune_expired(_read_store())
        bundle = _normalize_bundle_kinds(bundle)
        bundle_id = _safe_text(bundle.get("evidenceBundleId")) or f"research_{uuid.uuid4().hex[:12]}"
        created_at = _safe_text(bundle.get("createdAt")) or _utc_now_iso()
        stored = {
            **bundle,
            "evidenceBundleId": bundle_id,
            "scope": scope or "global",
            "createdAt": created_at,
            "retention": "persistent_research_ledger",
            "_expiresAt": time.time() + max(60, int(ttl_seconds or 60)),
        }
        items = [item for item in payload["evidenceBundles"] if _safe_text(item.get("evidenceBundleId")) != bundle_id]
        items.insert(0, stored)
        payload["evidenceBundles"] = items[:500]

        if _has_reusable_answer_pack(stored):
            candidate = _experience_from_bundle(stored, status="active")
            packs = [item for item in payload["experiencePacks"] if _safe_text(item.get("experiencePackId")) != candidate["experiencePackId"]]
            packs.insert(0, candidate)
            payload["experiencePacks"] = packs[:500]

        _write_store(payload)
        return _visible(stored)


def _pack_excluded_from_default_search(item: dict[str, Any]) -> bool:
    status = _safe_text(item.get("status")).lower()
    quality = _safe_text(item.get("qualityStatus")).lower()
    answer_pack = item.get("researchAnswerPack")
    score = answer_pack.get("score") if isinstance(answer_pack, dict) else {}
    answer_quality = _safe_text(score.get("qualityStatus")).lower() if isinstance(score, dict) else ""
    question_kind = _infer_question_kind(item.get("query") or item.get("title"), item)
    source_kind = _infer_source_kind(item)
    if not _has_reusable_answer_pack(item):
        return True
    if status == "draft":
        return True
    if quality in _DEFAULT_EXCLUDED_PACK_QUALITIES or answer_quality in _DEFAULT_EXCLUDED_PACK_QUALITIES:
        return True
    if question_kind in _TASK_LIKE_KINDS or source_kind in _TASK_LIKE_KINDS:
        return True
    return False


def _experience_search_score(item: dict[str, Any], *, overlap: int, now: datetime) -> float:
    freshness = _experience_freshness(item, now=now)
    try:
        authority_score = float(item.get("authorityScore") or 0)
    except (TypeError, ValueError):
        authority_score = 0.0
    try:
        usage_count = max(0, int(item.get("usageCount") or 0))
    except (TypeError, ValueError):
        usage_count = 0
    usage_bonus = min(6.0, math.log2(usage_count + 1) * 1.5)
    age_ratio = float(freshness["ageDays"]) / max(1.0, float(freshness["maxAgeDays"]))
    age_penalty = min(24.0, age_ratio * 12.0)
    state_penalty = {"current": 0.0, "aging": 3.0, "stale": 12.0, "expired": 24.0}.get(
        _safe_text(freshness.get("freshnessState")),
        0.0,
    )
    return overlap * 10.0 + authority_score / 10.0 + usage_bonus - age_penalty - state_penalty


def _search_experience_packs(
    *,
    query: str,
    scope: str,
    tags: list[str] | None,
    min_confidence: str,
    limit: int,
    include_archived: bool,
) -> list[dict[str, Any]]:
    q_tokens = _question_tokens(query)
    query_fingerprint = _topic_fingerprint(query)
    tag_set = {str(item).strip().lower() for item in list(tags or []) if str(item).strip()}
    confidence_rank = {"low": 1, "medium": 2, "high": 3}
    min_rank = confidence_rank.get(_safe_text(min_confidence).lower(), 0)
    scored: list[tuple[float, dict[str, Any]]] = []
    now = datetime.now(timezone.utc)
    with _LOCK:
        payload = _read_store()
        for item in payload["experiencePacks"]:
            if not isinstance(item, dict) or not _scope_matches(item, scope):
                continue
            if _pack_excluded_from_default_search(item):
                continue
            if not include_archived and _safe_text(item.get("status")).lower() == "archived":
                continue
            if min_rank and confidence_rank.get(_safe_text(item.get("confidence")).lower(), 0) < min_rank:
                continue
            item_tags = {str(tag).strip().lower() for tag in _as_list(item.get("tags")) if str(tag).strip()}
            if tag_set and not tag_set.intersection(item_tags):
                continue
            haystack = " ".join(
                [
                    _safe_text(item.get("title")),
                    _safe_text(item.get("query")),
                    _safe_text(item.get("summary")),
                    _safe_text(item.get("researchResult")),
                    _safe_text(item.get("applicability")),
                    _safe_text(item.get("sourcePolicy")),
                    " ".join(_safe_text(src.get("host")) for src in _as_list(item.get("sourceMatrixDigest")) if isinstance(src, dict)),
                    " ".join(_safe_text(url) for url in _as_list(item.get("sourceUrls"))),
                    " ".join(item_tags),
                ]
            ).lower()
            tokens = _question_tokens(haystack)
            overlap = len(q_tokens.intersection(tokens)) if q_tokens else 0
            if q_tokens and overlap == 0:
                continue
            topic_haystack = " ".join(
                [
                    _safe_text(item.get("title")),
                    _safe_text(item.get("query")),
                    _safe_text(item.get("applicability")),
                    " ".join(item_tags),
                ]
            ).lower()
            topic_overlap = len(q_tokens.intersection(_question_tokens(topic_haystack))) if q_tokens else 0
            if q_tokens and topic_overlap == 0:
                continue
            exact_topic_bonus = 1_000.0 if _safe_text(item.get("topicFingerprint")) == query_fingerprint else 0.0
            scored.append(
                (
                    exact_topic_bonus + _experience_search_score(item, overlap=overlap, now=now),
                    _visible_experience(item, now=now),
                )
            )
    scored.sort(key=lambda pair: (pair[0], _safe_text(pair[1].get("createdAt"))), reverse=True)
    return [item for _, item in scored[: max(1, min(int(limit or 10), 50))]]


def list_evidence_bundles(*, scope: str = "global", limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        payload = _read_store()
        previous_count = len(_as_list(payload.get("evidenceBundles")))
        previous_pack_count = len(_as_list(payload.get("experiencePacks")))
        payload = _prune_expired(payload)
        if (
            len(payload["evidenceBundles"]) != previous_count
            or len(payload["experiencePacks"]) != previous_pack_count
        ):
            _write_store(payload)
        safe_limit = max(1, min(int(limit or 50), 200))
        items: list[dict[str, Any]] = []
        for item in payload["evidenceBundles"]:
            if not isinstance(item, dict) or not _scope_matches(item, scope):
                continue
            visible = _visible(item)
            visible["promotable"] = _has_reusable_answer_pack(item)
            items.append(visible)
            if len(items) >= safe_limit:
                break
        return items


def get_evidence_bundle(evidence_bundle_id: str) -> dict[str, Any] | None:
    with _LOCK:
        payload = _prune_expired(_read_store())
        target = _safe_text(evidence_bundle_id)
        for item in payload["evidenceBundles"]:
            if _safe_text(item.get("evidenceBundleId")) == target:
                visible = _visible(item)
                visible["promotable"] = _has_reusable_answer_pack(item)
                return visible
    return None


def search_experience_packs(*, query: str, scope: str = "global", tags: list[str] | None = None, min_confidence: str = "", limit: int = 10) -> list[dict[str, Any]]:
    return _search_experience_packs(
        query=query,
        scope=scope,
        tags=tags,
        min_confidence=min_confidence,
        limit=limit,
        include_archived=False,
    )


def search_experience_packs_with_options(
    *,
    query: str,
    scope: str = "global",
    tags: list[str] | None = None,
    min_confidence: str = "",
    limit: int = 10,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    return _search_experience_packs(
        query=query,
        scope=scope,
        tags=tags,
        min_confidence=min_confidence,
        limit=limit,
        include_archived=include_archived,
    )


def get_experience_pack(
    experience_pack_id: str,
    *,
    include_archived: bool = False,
    record_usage: bool = False,
) -> dict[str, Any] | None:
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        for item in payload["experiencePacks"]:
            if _safe_text(item.get("experiencePackId")) == target:
                if _safe_text(item.get("status")).lower() == "archived" and not include_archived:
                    return None
                if record_usage:
                    item["usageCount"] = int(item.get("usageCount") or 0) + 1
                    item["lastUsedAt"] = _utc_now_iso()
                    _write_store(payload)
                return _visible_experience(item)
    return None


def promote_experience_pack(evidence_bundle_id: str, *, title: str = "", tags: list[str] | None = None) -> dict[str, Any] | None:
    with _LOCK:
        payload = _prune_expired(_read_store())
        bundle = None
        target_bundle_id = _safe_text(evidence_bundle_id)
        for item in payload["evidenceBundles"]:
            if _safe_text(item.get("evidenceBundleId")) == target_bundle_id:
                bundle = item
                break
        if not bundle:
            return None
        bundle = _normalize_bundle_kinds(bundle)
        if not _has_reusable_answer_pack(bundle):
            return None
        candidate = _experience_from_bundle(bundle, status="active", title=title, tags=tags)
        packs = [item for item in payload["experiencePacks"] if _safe_text(item.get("experiencePackId")) != candidate["experiencePackId"]]
        packs.insert(0, candidate)
        payload["experiencePacks"] = packs[:500]
        _write_store(payload)
        return _visible_experience(candidate)


def archive_experience_pack(experience_pack_id: str, *, initiated_by: str = "admin", reason: str = "") -> dict[str, Any] | None:
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        now = _utc_now_iso()
        for item in payload["experiencePacks"]:
            if _safe_text(item.get("experiencePackId")) == target:
                item["status"] = "archived"
                item["archivedAt"] = now
                item["archivedBy"] = _safe_text(initiated_by) or "admin"
                item["archiveReason"] = _safe_text(reason)
                item["updatedAt"] = now
                _write_store(payload)
                return _visible_experience(item)
    return None


def restore_experience_pack(experience_pack_id: str, *, initiated_by: str = "admin") -> dict[str, Any] | None:
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        now = _utc_now_iso()
        for item in payload["experiencePacks"]:
            if _safe_text(item.get("experiencePackId")) == target:
                issues = _experience_acceptance_issues(item)
                item["status"] = "draft" if issues else "active"
                if issues:
                    item["restoreQualityIssues"] = issues
                    existing = _safe_text(item.get("invalidationReason"))
                    item["invalidationReason"] = ", ".join(
                        dict.fromkeys([part for part in (existing, *issues) if part])
                    )
                else:
                    item.pop("restoreQualityIssues", None)
                item["restoredAt"] = now
                item["restoredBy"] = _safe_text(initiated_by) or "admin"
                item["updatedAt"] = now
                _write_store(payload)
                return _visible_experience(item)
    return None


def delete_experience_pack(experience_pack_id: str, *, confirm: bool = False) -> bool:
    if not confirm:
        return False
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        before = len(payload["experiencePacks"])
        payload["experiencePacks"] = [
            item for item in payload["experiencePacks"]
            if not (isinstance(item, dict) and _safe_text(item.get("experiencePackId")) == target)
        ]
        changed = len(payload["experiencePacks"]) != before
        if changed:
            _write_store(payload)
        return changed


def list_experience_packs(*, scope: str = "global", limit: int = 50, include_archived: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        payload = _read_store()
        now = datetime.now(timezone.utc)
        safe_limit = max(1, min(int(limit or 50), 200))
        items: list[dict[str, Any]] = []
        for item in payload["experiencePacks"]:
            if (
                not isinstance(item, dict)
                or not _scope_matches(item, scope)
                or (not include_archived and _safe_text(item.get("status")).lower() == "archived")
            ):
                continue
            items.append(_visible_experience(item, now=now))
            if len(items) >= safe_limit:
                break
        return items


def bulk_update_experience_packs(
    experience_pack_ids: list[str],
    *,
    action: str,
    initiated_by: str = "admin",
    reason: str = "",
) -> dict[str, Any]:
    normalized_action = _safe_text(action).lower()
    if normalized_action not in {"archive", "restore"}:
        raise ValueError("bulk experience action must be archive or restore")
    requested = []
    for value in list(experience_pack_ids or [])[:100]:
        pack_id = _safe_text(value)
        if pack_id and pack_id not in requested:
            requested.append(pack_id)
    if not requested:
        return {"ok": True, "action": normalized_action, "updatedCount": 0, "missingIds": [], "items": []}
    with _LOCK:
        payload = _read_store()
        now = _utc_now_iso()
        updated: list[dict[str, Any]] = []
        found: set[str] = set()
        for item in payload["experiencePacks"]:
            pack_id = _safe_text(item.get("experiencePackId")) if isinstance(item, dict) else ""
            if pack_id not in requested:
                continue
            found.add(pack_id)
            current_status = _safe_text(item.get("status")).lower()
            if normalized_action == "archive":
                if current_status != "archived":
                    item["status"] = "archived"
                    item["archivedAt"] = now
                    item["archivedBy"] = _safe_text(initiated_by) or "admin"
                    item["archiveReason"] = _safe_text(reason) or "bulk_governance"
                    item["updatedAt"] = now
                    updated.append(_visible_experience(item))
            elif current_status == "archived":
                issues = _experience_acceptance_issues(item)
                item["status"] = "draft" if issues else "active"
                if issues:
                    item["restoreQualityIssues"] = issues
                    existing = _safe_text(item.get("invalidationReason"))
                    item["invalidationReason"] = ", ".join(
                        dict.fromkeys([part for part in (existing, *issues) if part])
                    )
                else:
                    item.pop("restoreQualityIssues", None)
                item["restoredAt"] = now
                item["restoredBy"] = _safe_text(initiated_by) or "admin"
                item["updatedAt"] = now
                updated.append(_visible_experience(item))
        if updated:
            _write_store(payload)
        return {
            "ok": True,
            "action": normalized_action,
            "updatedCount": len(updated),
            "missingIds": [pack_id for pack_id in requested if pack_id not in found],
            "items": updated,
        }


def maintain_experience_packs(*, now: datetime | None = None) -> dict[str, Any]:
    """Converge reusable research packs without hard-deleting user evidence."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checked_at_text = checked_at.isoformat().replace("+00:00", "Z")
    with _LOCK:
        payload = _read_store()
        packs = [item for item in payload["experiencePacks"] if isinstance(item, dict)]
        changed = False
        expired_ids: list[str] = []
        duplicate_ids: list[str] = []
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in packs:
            state = _experience_freshness(item, now=checked_at)
            for key in ("freshnessState", "staleAt", "expiresAt"):
                if item.get(key) != state.get(key):
                    item[key] = state.get(key)
                    changed = True
            if state["freshnessState"] == "expired" and _safe_text(item.get("status")).lower() != "archived":
                item["status"] = "archived"
                item["archivedAt"] = checked_at_text
                item["archivedBy"] = "memory_maintenance"
                item["archiveReason"] = "freshness_expired"
                item["updatedAt"] = checked_at_text
                expired_ids.append(_safe_text(item.get("experiencePackId")))
                changed = True
            fingerprint = _safe_text(item.get("topicFingerprint"))
            if fingerprint and _safe_text(item.get("status")).lower() != "archived":
                groups.setdefault((_safe_text(item.get("scope")) or "global", fingerprint), []).append(item)
        for group in groups.values():
            if len(group) < 2:
                continue
            ordered = sorted(
                group,
                key=lambda item: (
                    not _pack_excluded_from_default_search(item),
                    _parse_datetime(item.get("evidenceCheckedAt")) or _parse_datetime(item.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc),
                    _safe_text(item.get("experiencePackId")),
                ),
                reverse=True,
            )
            for item in ordered[1:]:
                item["status"] = "archived"
                item["archivedAt"] = checked_at_text
                item["archivedBy"] = "memory_maintenance"
                item["archiveReason"] = "superseded_duplicate_topic"
                item["updatedAt"] = checked_at_text
                duplicate_ids.append(_safe_text(item.get("experiencePackId")))
                changed = True
        if changed:
            _write_store(payload)
        return {
            "ok": True,
            "candidateCount": len(packs),
            "expiredArchivedCount": len(expired_ids),
            "duplicateArchivedCount": len(duplicate_ids),
            "expiredArchivedIds": [value for value in expired_ids if value][:20],
            "duplicateArchivedIds": [value for value in duplicate_ids if value][:20],
            "changed": changed,
        }


def research_ledger_summary(*, scope: str = "global", include_archived: bool = False, limit: int = 30) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 30), 100))
    with _LOCK:
        payload = _read_store()
        previous_bundle_count = len(_as_list(payload.get("evidenceBundles")))
        previous_pack_count = len(_as_list(payload.get("experiencePacks")))
        payload = _prune_expired(payload)
        if (
            len(payload["evidenceBundles"]) != previous_bundle_count
            or len(payload["experiencePacks"]) != previous_pack_count
        ):
            _write_store(payload)
        scoped_bundles = [
            item
            for item in payload["evidenceBundles"]
            if isinstance(item, dict) and _scope_matches(item, scope)
        ][:200]
        scoped_packs = [
            item
            for item in payload["experiencePacks"]
            if isinstance(item, dict)
            and _scope_matches(item, scope)
            and (include_archived or _safe_text(item.get("status")).lower() != "archived")
        ][:200]

    bundles = [_visible(item) for item in scoped_bundles[:safe_limit]]
    bundles_by_id = {
        _safe_text(item.get("evidenceBundleId")): item
        for item in scoped_bundles
        if isinstance(item, dict) and _safe_text(item.get("evidenceBundleId"))
    }
    packs = []
    now = datetime.now(timezone.utc)
    for item in scoped_packs[:safe_limit]:
        enriched = _visible_experience(item, now=now)
        if not _safe_text(enriched.get("resultPreview")):
            bundle = bundles_by_id.get(_safe_text(enriched.get("createdFromBundleId")))
            if bundle:
                enriched["resultPreview"] = _research_result_preview(bundle)
        packs.append(enriched)
    timeline = [
        {
            "at": item.get("createdAt"),
            "question": item.get("question"),
            "confidence": item.get("confidence"),
            "authorityScore": item.get("authorityScore"),
            "evidenceBundleId": item.get("evidenceBundleId"),
        }
        for item in bundles[:30]
    ]
    return {
        "ok": True,
        "scope": scope,
        "counts": {"evidenceBundles": len(scoped_bundles), "experiencePacks": len(scoped_packs)},
        "evidenceBundles": bundles,
        "experiencePacks": packs,
        "confidenceTimeline": timeline,
    }
