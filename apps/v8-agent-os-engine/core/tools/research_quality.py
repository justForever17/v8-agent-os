from __future__ import annotations

import hashlib
import json
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from core.tools.research_source_identity import canonical_source_url, research_document_identity


# The minimum tier is a reviewed, reusable answer rather than a long-form
# report. High-quality targets below remain unchanged for broad/deep research.
MIN_RESEARCH_ANSWER_CHARS = 1200
MIN_RESEARCH_SOURCE_COUNT = 4
MIN_RESEARCH_DISTINCT_HOST_COUNT = 3
MIN_RESEARCH_CLAIM_COUNT = 5
MIN_RESEARCH_DATED_SOURCE_COUNT = 3
TARGET_RESEARCH_ANSWER_CHARS = 5000
TARGET_RESEARCH_SOURCE_COUNT = 8
TARGET_RESEARCH_DISTINCT_HOST_COUNT = 5
TARGET_RESEARCH_CLAIM_COUNT = 8
TARGET_RESEARCH_DATED_SOURCE_COUNT = 5
MIN_RESEARCH_SOURCE_BODY_CHARS = 400
MAX_RESEARCH_CLOCK_SKEW_DAYS = 1
MAX_CURRENT_RESEARCH_AGE_DAYS = 7

_OBSERVATION_DATE_KINDS = {
    "accessed",
    "accessed_at",
    "crawled",
    "crawled_at",
    "fetched",
    "fetched_at",
    "indexed",
    "indexed_at",
    "observed",
    "observed_at",
    "retrieved",
    "retrieved_at",
}

_FAILED_ANSWER_MARKERS = (
    "web research architect final result",
    "no reliable source-backed",
    "could not synthesize",
    "no source-backed research result",
    "reused research experience pack, but no detailed",
    "无法综合出可靠",
    "未获取到有效内容",
    "未收集到可靠",
    "无法回答该问题",
)
_TIME_SENSITIVE_MARKERS = (
    "latest",
    "current",
    "recent",
    "today",
    "this year",
    "as of",
    "最新",
    "当前",
    "目前",
    "近期",
    "今年",
    "截至",
)
_URL_RE = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
_CITATION_RE = re.compile(r"\[(?:S|SRC|SOURCE)[-_ ]?\d+\]", re.IGNORECASE)
_CONTENT_UNIT_RE = re.compile(r"(?<=[。！？.!?；;])\s*|\n+")
_SOURCE_APPENDIX_HEADING_RE = re.compile(
    r"(?im)^\s{0,3}#{1,6}\s*(?:sources?|references?|来源(?:列表)?|参考(?:资料|来源|文献))\s*$"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _final_pack(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("finalExperiencePack", "researchResult"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def research_review_decision(payload: dict[str, Any]) -> str:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    final_pack = _final_pack(payload)
    for value in (
        payload.get("reviewDecision"),
        _dict(payload.get("researchReview")).get("reviewDecision"),
        answer_pack.get("reviewDecision"),
        final_pack.get("reviewDecision"),
    ):
        normalized = _text(value).lower()
        if normalized in {"accept", "retry", "reject"}:
            return normalized
    return ""


def research_independent_review(payload: dict[str, Any]) -> dict[str, Any]:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    final_pack = _final_pack(payload)
    research_review = _dict(payload.get("researchReview"))
    for value in (
        payload.get("independentReview"),
        research_review.get("independentReview"),
        answer_pack.get("independentReview"),
        final_pack.get("independentReview"),
    ):
        if isinstance(value, dict):
            return value
    return {}


def _bound_independent_review_is_accepted(payload: dict[str, Any], review: dict[str, Any]) -> bool:
    unsupported = [value for value in _list(review.get("unsupportedClaims")) if _text(value)]
    critical_missing = [value for value in _list(review.get("criticalMissingEvidence")) if _text(value)]
    next_queries = [value for value in _list(review.get("recommendedNextQueries")) if _text(value)]
    reviewer_model_id = _text(review.get("reviewerModelId"))
    reviewed_at = _text(review.get("reviewedAt"))
    expected_binding = build_research_review_binding(
        payload,
        reviewer_model_id=reviewer_model_id,
        reviewed_at=reviewed_at,
    )
    binding_matches = all(review.get(key) == value for key, value in expected_binding.items())
    review_time_valid = _is_plausible_temporal_value(reviewed_at)
    return bool(
        _text(review.get("reviewDecision")).lower() == "accept"
        and review.get("questionCoverage") is True
        and review.get("claimEntailment") is True
        and review.get("freshnessAdequacy") is True
        and not unsupported
        and not critical_missing
        and not next_queries
        and reviewer_model_id
        and review_time_valid
        and binding_matches
    )


def _independent_review_is_accepted(payload: dict[str, Any]) -> bool:
    review = research_independent_review(payload)
    try:
        reported_review_count = int(review.get("consensusReviewCount") or 0)
    except (TypeError, ValueError):
        reported_review_count = 0
    consensus_reviews = [
        item for item in _list(review.get("consensusReviews")) if isinstance(item, dict)
    ]
    reviewer_ids = [_text(item.get("reviewerModelId")) for item in consensus_reviews]
    reported_ids = [_text(value) for value in _list(review.get("consensusReviewerModelIds")) if _text(value)]
    review_modes = {_text(item.get("reviewMode")).lower() for item in consensus_reviews}
    call_identities = {
        (
            _text(item.get("reviewerModelId")),
            _text(item.get("reviewMode")).lower(),
            _text(item.get("reviewedAt")),
        )
        for item in consensus_reviews
    }
    return bool(
        _bound_independent_review_is_accepted(payload, review)
        and review.get("consensusAccepted") is True
        and reported_review_count == len(consensus_reviews)
        and len(consensus_reviews) >= 2
        and len(call_identities) == len(consensus_reviews)
        and review_modes.issuperset({"semantic", "adversarial"})
        and all(reviewer_ids)
        and reported_ids == reviewer_ids
        and all(_bound_independent_review_is_accepted(payload, item) for item in consensus_reviews)
    )


def research_answer_text(payload: dict[str, Any]) -> str:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    final_pack = _final_pack(payload)
    candidates = (
        answer_pack.get("answer"),
        final_pack.get("researchResult"),
        final_pack.get("answer"),
        payload.get("answer"),
        payload.get("researchResult") if isinstance(payload.get("researchResult"), str) else "",
    )
    return next((_text(value) for value in candidates if _text(value)), "")


def research_raw_answer_chars(payload: dict[str, Any]) -> int:
    answer = _URL_RE.sub("", research_answer_text(payload))
    return len(re.sub(r"\s+", "", answer))


def _content_unit_signature(value: str) -> str:
    normalized = _CITATION_RE.sub("", value)
    normalized = re.sub(r"^\s*(?:#{1,6}|[-*+] |\d+[.)、]\s*)", "", normalized)
    normalized = re.sub(r"\d+", "", normalized)
    return re.sub(r"[^a-z\u4e00-\u9fff]+", "", normalized.lower())


def _signature_ngrams(value: str, *, size: int = 4) -> set[str]:
    if len(value) < size:
        return set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _ngram_set_similarity(left_grams: set[str], right_grams: set[str]) -> float:
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def _unique_signatures(values: list[str]) -> list[str]:
    unique: list[tuple[str, set[str]]] = []
    for value in values:
        signature = _content_unit_signature(value)
        signature_grams = _signature_ngrams(signature)
        if not signature or any(
            signature == existing
            or (
                min(len(signature), len(existing)) >= 20
                and _ngram_set_similarity(signature_grams, existing_grams) >= 0.9
            )
            for existing, existing_grams in unique
        ):
            continue
        unique.append((signature, signature_grams))
    return [signature for signature, _ in unique]


def research_effective_answer_chars(payload: dict[str, Any]) -> int:
    """Count answer content once; citations, URLs, and repeated filler do not add depth."""

    answer = _URL_RE.sub("", research_answer_text(payload))
    units = [unit.strip() for unit in _CONTENT_UNIT_RE.split(answer) if unit.strip()]
    if not units:
        return 0
    seen: list[tuple[str, set[str]]] = []
    effective_chars = 0
    for unit in units:
        signature = _content_unit_signature(unit)
        signature_grams = _signature_ngrams(signature)
        if not signature or any(
            signature == existing
            or (
                min(len(signature), len(existing)) >= 20
                and _ngram_set_similarity(signature_grams, existing_grams) >= 0.9
            )
            for existing, existing_grams in seen
        ):
            continue
        seen.append((signature, signature_grams))
        effective_chars += len(re.sub(r"\s+", "", _CITATION_RE.sub("", unit)))
    return effective_chars


def _canonical_source_url(value: Any) -> str:
    return canonical_source_url(value)


def _source_identity(source: Any, *, question: Any = "") -> tuple[str, str, str]:
    if isinstance(source, str):
        url = _text(source)
        return url, "", research_document_identity(url, question=question)
    item = _dict(source)
    url = _text(item.get("url") or item.get("sourceUrl"))
    source_id = _text(item.get("sourceId"))
    return url, source_id, research_document_identity(url, question=question) or source_id


def _source_is_selected(source: dict[str, Any], *, trusted_selected_list: bool = False) -> bool:
    gate = _dict(source.get("sourceQualityGate"))
    explicit = source.get("selectedForEvidence")
    gate_explicit = gate.get("selectedForEvidence")
    if explicit is False or gate_explicit is False:
        return False
    if explicit is True or gate_explicit is True or trusted_selected_list:
        return True
    return False


def research_selected_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    evidence_bank = _dict(payload.get("researchEvidenceBank"))
    final_pack = _final_pack(payload)
    question = payload.get("question") or _final_pack(payload).get("question")
    candidate_groups = (
        (_list(final_pack.get("sourceUrls")), True),
        (_list(answer_pack.get("sources")), True),
        (_list(evidence_bank.get("selectedSources")), True),
        (_list(payload.get("sourceMatrix")), False),
    )
    for candidates, trusted in candidate_groups:
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in candidates:
            if not isinstance(source, dict) or not _source_is_selected(
                source,
                trusted_selected_list=trusted,
            ):
                continue
            url, source_id, identity = _source_identity(source, question=question)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            normalized = dict(source)
            if url:
                normalized["url"] = url
            if source_id:
                normalized["sourceId"] = source_id
            selected.append(normalized)
        if selected:
            return selected
    return []


def research_claims(payload: dict[str, Any]) -> list[dict[str, Any]]:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    evidence_bank = _dict(payload.get("researchEvidenceBank"))
    final_pack = _final_pack(payload)
    for value in (
        answer_pack.get("claimTable"),
        final_pack.get("claimTable"),
        payload.get("claimTable"),
        evidence_bank.get("claims"),
        payload.get("claimDigest"),
    ):
        items = [dict(item) for item in _list(value) if isinstance(item, dict)]
        if items:
            return items
    return []


def build_research_review_binding(
    payload: dict[str, Any],
    *,
    reviewer_model_id: str,
    reviewed_at: str,
) -> dict[str, Any]:
    """Bind an independent review to the exact content and temporal evidence."""

    final_pack = _final_pack(payload)
    question = re.sub(
        r"\s+",
        " ",
        _text(payload.get("question") or payload.get("query") or final_pack.get("question") or final_pack.get("query")).lower(),
    ).strip()
    answer = re.sub(r"\s+", " ", research_answer_text(payload)).strip()
    sources = research_selected_sources(payload)
    source_aliases = _known_source_aliases(sources)
    source_rows: list[dict[str, str]] = []
    temporal_rows: list[dict[str, str]] = []
    for source in sources:
        _url, source_id, identity = _source_identity(source)
        receipt = _dict(source.get("readEvidence"))
        temporal = _dict(source.get("temporalEvidence"))
        source_rows.append(
            {
                "identity": identity,
                "sourceId": source_id,
                "citationKey": _text(source.get("citationKey")),
                "contentSha256": _text(receipt.get("contentSha256")).lower(),
            }
        )
        temporal_rows.append(
            {
                "identity": identity,
                "retrievedAt": _source_retrieved_at(source),
                "receiptRetrievedAt": _text(receipt.get("retrievedAt")),
                "publishedAt": _text(source.get("publishedAt") or temporal.get("publishedAt")),
                "updatedAt": _text(source.get("updatedAt") or temporal.get("updatedAt")),
                "sourceDate": _text(source.get("sourceDate") or temporal.get("sourceDate")),
                "version": _text(source.get("version") or temporal.get("version")),
                "applicableVersion": _text(
                    source.get("applicableVersion") or temporal.get("applicableVersion")
                ),
                "temporalStatus": _text(
                    source.get("temporalStatus")
                    or source.get("applicabilityStatus")
                    or temporal.get("status")
                    or temporal.get("applicabilityStatus")
                ),
                "applicabilityBasis": _text(
                    source.get("applicabilityBasis") or temporal.get("applicabilityBasis")
                ),
            }
        )
    claim_rows: list[dict[str, Any]] = []
    for claim in research_claims(payload):
        supports = sorted(_claim_support_identities(claim, source_aliases))
        claim_rows.append(
            {
                "claim": re.sub(r"\s+", " ", _text(claim.get("claim") or claim.get("summary")).lower()).strip(),
                "claimType": _text(claim.get("claimType") or claim.get("claimKind")).lower(),
                "normativeCue": re.sub(r"\s+", " ", _text(claim.get("normativeCue")).lower()).strip(),
                "supports": supports,
                "evidenceExcerptKey": _text(claim.get("evidenceExcerptKey")),
                "evidenceExcerpt": re.sub(r"\s+", " ", _text(claim.get("evidenceExcerpt")).lower()).strip(),
            }
        )

    def digest(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    return {
        "bindingVersion": 6,
        "questionFingerprint": digest(question),
        "answerSha256": digest(answer),
        "claimDigest": digest(sorted(claim_rows, key=lambda item: (item["claim"], item["evidenceExcerpt"]))),
        "sourceDigest": digest(
            sorted(source_rows, key=lambda item: (item["citationKey"], item["identity"], item["sourceId"]))
        ),
        "temporalDigest": digest(
            {
                "freshness": _text(
                    payload.get("freshness")
                    or payload.get("freshnessWindow")
                    or final_pack.get("freshness")
                    or final_pack.get("freshnessWindow")
                ).lower(),
                "asOf": research_as_of(payload),
                "sources": sorted(temporal_rows, key=lambda item: item["identity"]),
            }
        ),
        "reviewerModelId": _text(reviewer_model_id),
        "reviewedAt": _text(reviewed_at),
    }


def research_missing_evidence(payload: dict[str, Any]) -> list[str]:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    final_pack = _final_pack(payload)
    values: list[Any] = []
    for item in (
        payload.get("missingEvidence"),
        payload.get("missingOrStaleReasons"),
        answer_pack.get("missingOrStaleReasons"),
        answer_pack.get("limitations"),
        final_pack.get("missingEvidence"),
        final_pack.get("limitations"),
    ):
        values.extend(_list(item))
    result: list[str] = []
    for value in values:
        normalized = _text(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def research_critical_missing_evidence(payload: dict[str, Any]) -> list[str]:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    final_pack = _final_pack(payload)
    values = (
        _list(payload.get("criticalMissingEvidence"))
        + _list(answer_pack.get("criticalMissingEvidence"))
        + _list(final_pack.get("criticalMissingEvidence"))
    )
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


def _known_source_aliases(sources: list[dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for source in sources:
        canonical = _text(source.get("url") or source.get("sourceUrl") or source.get("sourceId"))
        if not canonical:
            continue
        for value in (source.get("url"), source.get("sourceUrl"), source.get("sourceId"), source.get("citationKey")):
            normalized = _text(value)
            if normalized:
                aliases[normalized] = canonical
    return aliases


def _claim_support_identities(claim: dict[str, Any], known_sources: dict[str, str]) -> set[str]:
    if len(_text(claim.get("claim") or claim.get("summary"))) < 20:
        return set()
    matched: set[str] = set()
    for source in _list(claim.get("supportingSources")):
        if isinstance(source, dict):
            values = (source.get("url"), source.get("sourceUrl"), source.get("sourceId"), source.get("citationKey"))
        else:
            values = (source,)
        for value in values:
            normalized = _text(value)
            if normalized and normalized in known_sources:
                matched.add(known_sources[normalized])
    return matched


def _claim_has_verified_excerpt(claim: dict[str, Any]) -> bool:
    excerpt = _text(claim.get("evidenceExcerpt"))
    digest = _text(claim.get("evidenceExcerptSha256")).lower()
    normalized_excerpt = re.sub(r"\s+", " ", excerpt).strip().lower()
    expected_digest = hashlib.sha256(normalized_excerpt.encode("utf-8")).hexdigest()
    return bool(
        claim.get("evidenceVerified") is True
        and len(normalized_excerpt) >= 20
        and digest == expected_digest
    )


def _source_host(source: dict[str, Any]) -> str:
    host = _text(source.get("host")).lower().removeprefix("www.")
    if host:
        return host
    try:
        return _text(urlparse(_text(source.get("url") or source.get("sourceUrl"))).hostname).lower().removeprefix("www.")
    except Exception:
        return ""


def _source_retrieved_at(source: dict[str, Any]) -> str:
    temporal = _dict(source.get("temporalEvidence"))
    return _text(source.get("retrievedAt") or source.get("fetchedAt") or temporal.get("retrievedAt"))


def _parsed_temporal_value(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10}|\d{13}", text):
        try:
            epoch_seconds = int(text) / (1000 if len(text) == 13 else 1)
            return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for date_format in (
            "%B %d, %Y",
            "%b %d, %Y",
            "%B %d %Y",
            "%b %d %Y",
            "%d %B %Y",
            "%d %b %Y",
            "%d-%b-%Y",
        ):
            try:
                parsed = datetime.strptime(text, date_format)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_valid_temporal_value(value: Any) -> bool:
    return _parsed_temporal_value(value) is not None


def _is_plausible_temporal_value(value: Any) -> bool:
    parsed = _parsed_temporal_value(value)
    if parsed is None:
        return False
    return datetime(2000, 1, 1, tzinfo=timezone.utc) <= parsed <= (
        datetime.now(timezone.utc) + timedelta(days=MAX_RESEARCH_CLOCK_SKEW_DAYS)
    )


def _temporal_age_days(value: Any) -> float | None:
    parsed = _parsed_temporal_value(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86_400)


def _is_current_temporal_value(value: Any) -> bool:
    age_days = _temporal_age_days(value)
    return bool(
        age_days is not None
        and _is_plausible_temporal_value(value)
        and age_days <= MAX_CURRENT_RESEARCH_AGE_DAYS
    )


def research_document_date_candidates(source: dict[str, Any]) -> list[tuple[str, str]]:
    """Return document-authored dates, excluding retrieval/index observation time."""

    temporal = _dict(source.get("temporalEvidence"))
    candidates: list[tuple[str, Any]] = [
        ("published", source.get("publishedAt")),
        ("updated", source.get("updatedAt")),
        ("published", temporal.get("publishedAt")),
        ("updated", temporal.get("updatedAt")),
    ]
    for container in (source, temporal):
        source_date = container.get("sourceDate")
        source_date_kind = _text(container.get("sourceDateKind")).lower().replace("-", "_")
        if source_date and source_date_kind not in _OBSERVATION_DATE_KINDS:
            candidates.append(
                (
                    source_date_kind if source_date_kind in {"published", "updated"} else "document",
                    source_date,
                )
            )
    normalized: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, value in candidates:
        text = _text(value)
        item = (kind, text)
        if text and item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def research_source_has_dated_evidence(source: dict[str, Any]) -> bool:
    temporal = _dict(source.get("temporalEvidence"))
    dated_values = [value for _kind, value in research_document_date_candidates(source)]
    version_values = (source.get("version"), temporal.get("version"))
    return bool(
        any(_is_plausible_temporal_value(value) for value in dated_values)
        or any(re.search(r"\b\d+\.\d+(?:\.\d+)?\b", _text(value)) for value in version_values)
    )


def _source_has_read_evidence(source: dict[str, Any]) -> bool:
    receipt = _dict(source.get("readEvidence"))
    try:
        content_chars = int(source.get("contentChars") or 0)
    except (TypeError, ValueError):
        content_chars = 0
    try:
        receipt_chars = int(receipt.get("contentChars") or 0)
    except (TypeError, ValueError):
        receipt_chars = 0
    digest = _text(receipt.get("contentSha256")).lower()
    source_retrieved_at = _source_retrieved_at(source)
    receipt_retrieved_at = receipt.get("retrievedAt") or source_retrieved_at
    source_retrieved = _parsed_temporal_value(source_retrieved_at)
    receipt_retrieved = _parsed_temporal_value(receipt_retrieved_at)
    timestamps_match = bool(
        source_retrieved
        and receipt_retrieved
        and abs((source_retrieved - receipt_retrieved).total_seconds()) <= 300
    )
    return bool(
        receipt.get("verified") is True
        and content_chars >= MIN_RESEARCH_SOURCE_BODY_CHARS
        and receipt_chars == content_chars
        and re.fullmatch(r"[0-9a-f]{64}", digest)
        and _is_plausible_temporal_value(source_retrieved_at)
        and _is_plausible_temporal_value(receipt_retrieved_at)
        and timestamps_match
    )


def research_requires_dated_sources(payload: dict[str, Any]) -> bool:
    freshness = _text(
        payload.get("freshness")
        or payload.get("freshnessWindow")
        or _final_pack(payload).get("freshness")
        or _final_pack(payload).get("freshnessWindow")
    ).lower()
    if freshness in {
        "latest",
        "current",
        "recent",
        "today",
        "fresh",
        "最新",
        "当前",
        "实时",
        "近期",
        "最近",
        "今天",
        "时效优先",
    }:
        return True
    question = _text(
        payload.get("question")
        or payload.get("query")
        or _final_pack(payload).get("question")
        or _final_pack(payload).get("query")
    ).lower()
    return str(datetime.now(timezone.utc).year) in question or any(
        marker in question for marker in _TIME_SENSITIVE_MARKERS
    )


def _claim_requires_current_evidence(claim: dict[str, Any]) -> bool:
    text = _text(claim.get("claim") or claim.get("summary")).lower()
    support_roles = {
        _text(support.get("tier") or support.get("sourceRole") or support.get("role")).lower()
        for support in _list(claim.get("supportingSources"))
        if isinstance(support, dict)
        and _text(support.get("tier") or support.get("sourceRole") or support.get("role"))
    }
    if (
        support_roles
        and support_roles.issubset({"secondary", "community", "experience"})
        and re.search(
            r"^(?:secondary source.{0,180}states?:\s*)?"
            r"(?:i|we)\s+recently\s+(?:published|wrote|released|posted)\b",
            text,
            re.IGNORECASE,
        )
    ):
        # This is a source-attributed relative-time utterance, not a Runtime
        # claim that the reported fact is current as of the research run.
        return False
    temporal_text = re.sub(
        r"\bcurrent\s+(?:user|working\s+directory|directory|process|thread|file|path|"
        r"environment|shell|platform|operating\s+system|system)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    english_temporal = bool(
        re.search(
            r"\b(?:latest|current|currently|recent|recently|today|this\s+year|as\s+of)\b",
            temporal_text,
            re.IGNORECASE,
        )
    )
    cjk_temporal = any(
        marker in temporal_text
        for marker in ("最新", "当前", "目前", "近期", "今年", "截至")
    )
    return bool(
        str(datetime.now(timezone.utc).year) in temporal_text
        or english_temporal
        or cjk_temporal
    )


def _source_supports_current_claim(source: dict[str, Any]) -> bool:
    """Recognize explicit applicability context; never infer it from age."""

    temporal = _dict(source.get("temporalEvidence"))
    status = _text(
        source.get("temporalStatus")
        or source.get("applicabilityStatus")
        or temporal.get("status")
        or temporal.get("applicabilityStatus")
    ).lower()
    applicability_basis = _text(
        source.get("applicabilityBasis") or temporal.get("applicabilityBasis")
    ).lower()
    if status in {"current", "current_applicable", "stable_current"}:
        return True
    if (
        "stable_current" in applicability_basis
        or "current_documentation_route" in applicability_basis
        or "explicit_current" in applicability_basis
    ):
        return True
    role = _text(source.get("tier") or source.get("sourceRole") or source.get("role")).lower()
    url = _text(source.get("url") or source.get("sourceUrl")).lower()
    if role in {"primary", "official", "first_party", "first-party"} and re.search(
        r"/(?:stable|current|latest)(?:/|$)|://docs\.python\.org/3/",
        url,
    ):
        # This establishes applicability through a governed current/stable
        # first-party route, not a recent publication date.  Secondary URLs
        # containing these words deliberately do not receive this treatment.
        return True
    # A version label and a document-authored timestamp remain visible
    # temporal context, but neither automatically proves current applicability.
    # The independent Reviewer evaluates them against the claim and question.
    return False


def _source_has_temporal_context(source: dict[str, Any]) -> bool:
    """Return whether a reviewer receives an explicit time/applicability cue.

    This deliberately does not decide that the source is *current*.  A recent
    retrieval timestamp only proves when Runtime inspected the document, while
    publication/update dates, versions and applicability labels describe the
    document itself.  Keeping those meanings separate gives the semantic
    reviewer enough context without imposing a brittle age cutoff here.
    """

    temporal = _dict(source.get("temporalEvidence"))
    if _is_plausible_temporal_value(_source_retrieved_at(source)):
        return True
    if any(
        _is_plausible_temporal_value(value)
        for _kind, value in research_document_date_candidates(source)
    ):
        return True
    return bool(
        _text(
            source.get("temporalStatus")
            or source.get("applicabilityStatus")
            or temporal.get("status")
            or temporal.get("applicabilityStatus")
        )
        or _text(
            source.get("version")
            or source.get("applicableVersion")
            or temporal.get("version")
            or temporal.get("applicableVersion")
        )
    )


def research_as_of(payload: dict[str, Any]) -> str:
    answer_pack = _dict(payload.get("researchAnswerPack"))
    final_pack = _final_pack(payload)
    temporal = _dict(final_pack.get("temporalAssessment"))
    return _text(payload.get("asOf") or answer_pack.get("asOf") or final_pack.get("asOf") or temporal.get("asOf"))


def _answer_body_without_source_appendix(answer: str) -> str:
    match = _SOURCE_APPENDIX_HEADING_RE.search(answer)
    return answer[: match.start()].rstrip() if match else answer


def _answer_cited_source_count(answer: str, sources: list[dict[str, Any]]) -> int:
    answer = _answer_body_without_source_appendix(answer)
    cited = 0
    for source in sources:
        citation_key = _text(source.get("citationKey"))
        source_id = _text(source.get("sourceId"))
        url = _text(source.get("url"))
        cited_by_label = any(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", answer)
            for key in (citation_key, source_id)
            if key
        )
        if cited_by_label or (url and url in answer):
            cited += 1
    return cited


def _answer_cited_content_unit_count(answer: str, sources: list[dict[str, Any]]) -> int:
    answer = _answer_body_without_source_appendix(answer)
    markers = [
        marker
        for source in sources
        for marker in (
            _text(source.get("citationKey")),
            _text(source.get("sourceId")),
            _text(source.get("url")),
        )
        if marker
    ]
    cited_units = 0
    for unit in _CONTENT_UNIT_RE.split(answer):
        if len(_content_unit_signature(unit)) < 20:
            continue
        if any(
            marker in unit
            if marker.startswith("http")
            else re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", unit)
            for marker in markers
        ):
            cited_units += 1
    return cited_units


def research_acceptance_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    sources = research_selected_sources(payload)
    known_sources = _known_source_aliases(sources)
    claims = research_claims(payload)
    unique_claim_signatures = _unique_signatures(
        [_text(claim.get("claim") or claim.get("summary")) for claim in claims]
    )
    supported_claims = 0
    evidence_verified_claims = 0
    supported_source_identities: set[str] = set()
    source_by_identity = {
        _text(source.get("url") or source.get("sourceUrl") or source.get("sourceId")): source
        for source in sources
        if _text(source.get("url") or source.get("sourceUrl") or source.get("sourceId"))
    }
    time_sensitive_claims = 0
    temporally_supported_time_sensitive_claims = 0
    temporally_contextualized_time_sensitive_claims = 0
    for claim in claims:
        matched = _claim_support_identities(claim, known_sources)
        if matched:
            supported_claims += 1
            supported_source_identities.update(matched)
        if _claim_has_verified_excerpt(claim):
            evidence_verified_claims += 1
        if _claim_requires_current_evidence(claim):
            time_sensitive_claims += 1
            if any(
                _source_has_temporal_context(source_by_identity.get(identity) or {})
                for identity in matched
            ):
                temporally_contextualized_time_sensitive_claims += 1
            if any(
                _source_supports_current_claim(source_by_identity.get(identity) or {})
                for identity in matched
            ):
                temporally_supported_time_sensitive_claims += 1
    hosts = {_source_host(source) for source in sources if _source_host(source)}
    requires_dated_sources = research_requires_dated_sources(payload)
    retrieved_sources = sum(1 for source in sources if _is_plausible_temporal_value(_source_retrieved_at(source)))
    fresh_retrieved_sources = sum(1 for source in sources if _is_current_temporal_value(_source_retrieved_at(source)))
    read_verified_sources = sum(1 for source in sources if _source_has_read_evidence(source))
    dated_sources = sum(1 for source in sources if research_source_has_dated_evidence(source))
    answer = research_answer_text(payload)
    raw_answer_chars = research_raw_answer_chars(payload)
    effective_answer_chars = research_effective_answer_chars(payload)
    return {
        "reviewDecision": research_review_decision(payload),
        "rawAnswerChars": raw_answer_chars,
        "effectiveAnswerChars": effective_answer_chars,
        "uniqueContentRatio": round(effective_answer_chars / raw_answer_chars, 4) if raw_answer_chars else 0.0,
        "selectedSourceCount": len(sources),
        "distinctHostCount": len(hosts),
        "retrievedSourceCount": retrieved_sources,
        "freshRetrievedSourceCount": fresh_retrieved_sources,
        "readVerifiedSourceCount": read_verified_sources,
        "datedSourceCount": dated_sources,
        "claimCount": len(claims),
        "uniqueClaimCount": len(unique_claim_signatures),
        "supportedClaimCount": supported_claims,
        "evidenceVerifiedClaimCount": evidence_verified_claims,
        "claimSupportedSourceCount": len(supported_source_identities),
        "timeSensitiveClaimCount": time_sensitive_claims,
        "temporallySupportedTimeSensitiveClaimCount": temporally_supported_time_sensitive_claims,
        "temporallyContextualizedTimeSensitiveClaimCount": (
            temporally_contextualized_time_sensitive_claims
        ),
        "answerCitedSourceCount": _answer_cited_source_count(answer, sources),
        "answerCitedContentUnitCount": _answer_cited_content_unit_count(answer, sources),
        "asOf": research_as_of(payload),
        "asOfValid": _is_plausible_temporal_value(research_as_of(payload)),
        "asOfCurrent": _is_current_temporal_value(research_as_of(payload)),
        "requiresDatedSources": requires_dated_sources,
        "independentReviewAccepted": _independent_review_is_accepted(payload),
        "independentReviewCount": len(
            [
                item
                for item in _list(research_independent_review(payload).get("consensusReviews"))
                if isinstance(item, dict)
            ]
        ),
        "independentReviewerModelCount": len(
            {
                _text(item.get("reviewerModelId"))
                for item in _list(research_independent_review(payload).get("consensusReviews"))
                if isinstance(item, dict) and _text(item.get("reviewerModelId"))
            }
        ),
        "briefCoverageRequired": payload.get("briefCoverageRequired") is True,
        "briefCoverageComplete": payload.get("briefCoverageComplete") is True,
        "coveredTaskBriefCount": len(
            [
                item
                for item in _list(payload.get("briefCoverage"))
                if isinstance(item, dict) and _text(item.get("status")) == "supported"
            ]
        ),
        "missingTaskBriefCount": len(
            [
                item
                for item in _list(payload.get("briefCoverage"))
                if isinstance(item, dict) and _text(item.get("status")) != "supported"
            ]
        ),
    }


def _delivery_requirement(payload: dict[str, Any], key: str, default: int) -> int:
    candidates = [
        payload.get("deliveryRequirements"),
        (payload.get("researchAnswerPack") or {}).get("deliveryRequirements")
        if isinstance(payload.get("researchAnswerPack"), dict)
        else None,
        (payload.get("finalExperiencePack") or {}).get("deliveryRequirements")
        if isinstance(payload.get("finalExperiencePack"), dict)
        else None,
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            value = int(candidate.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return int(default)


def research_acceptance_issues(
    payload: dict[str, Any],
    *,
    min_sources: int | None = None,
    min_answer_chars: int | None = None,
    min_distinct_hosts: int | None = None,
    min_claims: int | None = None,
) -> list[str]:
    issues: list[str] = []
    metrics = research_acceptance_metrics(payload)
    source_floor = max(
        1,
        int(
            min_sources
            if min_sources is not None
            else _delivery_requirement(
                payload,
                "minimumSources",
                MIN_RESEARCH_SOURCE_COUNT,
            )
        ),
    )
    answer_floor = max(
        1,
        int(
            min_answer_chars
            if min_answer_chars is not None
            else _delivery_requirement(
                payload,
                "minimumAnswerChars",
                MIN_RESEARCH_ANSWER_CHARS,
            )
        ),
    )
    host_floor = max(
        1,
        int(
            min_distinct_hosts
            if min_distinct_hosts is not None
            else _delivery_requirement(
                payload,
                "minimumDistinctHosts",
                min(MIN_RESEARCH_DISTINCT_HOST_COUNT, source_floor),
            )
        ),
    )
    claim_floor = max(
        1,
        int(
            min_claims
            if min_claims is not None
            else _delivery_requirement(
                payload,
                "minimumClaims",
                MIN_RESEARCH_CLAIM_COUNT,
            )
        ),
    )
    if metrics["reviewDecision"] != "accept":
        issues.append("architect_review_not_accepted")
    if not metrics["independentReviewAccepted"]:
        issues.append("independent_semantic_review_not_accepted")
    if metrics["briefCoverageRequired"] and not metrics["briefCoverageComplete"]:
        issues.append("research_brief_coverage_incomplete")

    answer = research_answer_text(payload)
    if metrics["effectiveAnswerChars"] < answer_floor:
        issues.append(f"detailed_answer_floor_not_met:{answer_floor}")
    if (
        metrics["rawAnswerChars"] >= answer_floor
        and metrics["effectiveAnswerChars"] < answer_floor
        and metrics["uniqueContentRatio"] < 0.7
    ):
        issues.append("answer_repetition_excessive")
    lowered_answer = answer.lower()
    if any(marker in lowered_answer for marker in _FAILED_ANSWER_MARKERS):
        issues.append("process_or_failure_text_used_as_answer")

    if metrics["selectedSourceCount"] < source_floor:
        issues.append(f"evidence_source_floor_not_met:{source_floor}")
    if metrics["distinctHostCount"] < host_floor:
        issues.append(f"independent_host_floor_not_met:{host_floor}")
    if metrics["retrievedSourceCount"] < source_floor:
        issues.append(f"retrieval_evidence_floor_not_met:{source_floor}")
    if metrics["readVerifiedSourceCount"] < source_floor:
        issues.append(f"read_evidence_floor_not_met:{source_floor}")
    if not metrics["asOf"]:
        issues.append("research_as_of_missing")
    elif not metrics["asOfValid"]:
        issues.append("research_as_of_invalid")
    # Keep asOfCurrent and freshRetrievedSourceCount observable, but do not use
    # a fixed seven-day age or a global recent-retrieval quota as a truth gate.
    # Retrieval time is not publication time, and slow-changing/versioned
    # evidence can remain applicable. The independent reviewer receives the
    # explicit temporal fields and decides adequacy for the concrete question.
    if metrics["claimCount"] < claim_floor:
        issues.append(f"claim_floor_not_met:{claim_floor}")
    if metrics["uniqueClaimCount"] < claim_floor:
        issues.append(f"distinct_claim_floor_not_met:{claim_floor}")
    if metrics["supportedClaimCount"] != metrics["claimCount"]:
        issues.append("unsupported_claim_present")
    if metrics["evidenceVerifiedClaimCount"] != metrics["claimCount"]:
        issues.append("unverified_claim_excerpt_present")
    if (
        metrics["temporallyContextualizedTimeSensitiveClaimCount"]
        != metrics["timeSensitiveClaimCount"]
    ):
        issues.append("time_sensitive_claim_without_temporal_context")
    if metrics["claimSupportedSourceCount"] < source_floor:
        issues.append(f"claim_source_coverage_floor_not_met:{source_floor}")
    if metrics["answerCitedSourceCount"] < source_floor:
        issues.append(f"answer_citation_floor_not_met:{source_floor}")
    if metrics["answerCitedContentUnitCount"] < source_floor:
        issues.append(f"answer_citation_spread_floor_not_met:{source_floor}")

    if research_critical_missing_evidence(payload):
        issues.append("critical_evidence_gap")
    return list(dict.fromkeys(issues))


def research_bundle_is_accepted(
    payload: dict[str, Any],
    *,
    min_sources: int | None = None,
    min_answer_chars: int | None = None,
    min_distinct_hosts: int | None = None,
    min_claims: int | None = None,
) -> bool:
    return not research_acceptance_issues(
        payload,
        min_sources=min_sources,
        min_answer_chars=min_answer_chars,
        min_distinct_hosts=min_distinct_hosts,
        min_claims=min_claims,
    )


def research_high_quality_issues(payload: dict[str, Any]) -> list[str]:
    """Return gaps between minimum eligibility and a normal Research deliverable."""

    issues = research_acceptance_issues(payload)
    metrics = research_acceptance_metrics(payload)
    answer_target = max(
        _delivery_requirement(payload, "minimumAnswerChars", MIN_RESEARCH_ANSWER_CHARS),
        _delivery_requirement(payload, "targetAnswerChars", TARGET_RESEARCH_ANSWER_CHARS),
    )
    source_target = max(
        _delivery_requirement(payload, "minimumSources", MIN_RESEARCH_SOURCE_COUNT),
        _delivery_requirement(payload, "targetSources", TARGET_RESEARCH_SOURCE_COUNT),
    )
    host_target = max(
        _delivery_requirement(
            payload,
            "minimumDistinctHosts",
            min(MIN_RESEARCH_DISTINCT_HOST_COUNT, source_target),
        ),
        _delivery_requirement(
            payload,
            "targetDistinctHosts",
            TARGET_RESEARCH_DISTINCT_HOST_COUNT,
        ),
    )
    claim_target = max(
        _delivery_requirement(payload, "minimumClaims", MIN_RESEARCH_CLAIM_COUNT),
        _delivery_requirement(payload, "targetClaims", TARGET_RESEARCH_CLAIM_COUNT),
    )
    if metrics["effectiveAnswerChars"] < answer_target:
        issues.append(f"target_answer_depth_not_met:{answer_target}")
    if metrics["selectedSourceCount"] < source_target:
        issues.append(f"target_source_count_not_met:{source_target}")
    if metrics["distinctHostCount"] < host_target:
        issues.append(f"target_independent_host_count_not_met:{host_target}")
    if metrics["retrievedSourceCount"] < source_target:
        issues.append(f"target_retrieval_evidence_not_met:{source_target}")
    if metrics["readVerifiedSourceCount"] < source_target:
        issues.append(f"target_read_evidence_not_met:{source_target}")
    if metrics["claimCount"] < claim_target:
        issues.append(f"target_claim_depth_not_met:{claim_target}")
    if metrics["uniqueClaimCount"] < claim_target:
        issues.append(f"target_distinct_claim_depth_not_met:{claim_target}")
    if metrics["evidenceVerifiedClaimCount"] < claim_target:
        issues.append(f"target_verified_claim_evidence_not_met:{claim_target}")
    if metrics["claimSupportedSourceCount"] < source_target:
        issues.append(f"target_claim_source_coverage_not_met:{source_target}")
    if metrics["answerCitedSourceCount"] < source_target:
        issues.append(f"target_answer_citation_count_not_met:{source_target}")
    if metrics["answerCitedContentUnitCount"] < source_target:
        issues.append(f"target_answer_citation_spread_not_met:{source_target}")
    return list(dict.fromkeys(issues))


def research_bundle_is_high_quality(payload: dict[str, Any]) -> bool:
    return not research_high_quality_issues(payload)


def research_quality_tier(payload: dict[str, Any]) -> str:
    if research_bundle_is_high_quality(payload):
        return "high_quality"
    if research_bundle_is_accepted(payload):
        return "minimum_qualified"
    return "insufficient"
