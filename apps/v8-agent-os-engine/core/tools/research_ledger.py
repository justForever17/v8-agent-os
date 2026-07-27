from __future__ import annotations

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

from core.v8_agent_os_paths import runtime_private_root


_LOCK = threading.RLock()
_VERSION = 1
_DEFAULT_EXPERIENCE_MAX_AGE_DAYS = 180
_FRESHNESS_WARNING_RATIO = 0.75


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
    return {**_visible(item), **_experience_freshness(item, now=now)}


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


def _source_digest(bundle: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    digest: list[dict[str, Any]] = []
    for item in _as_list(bundle.get("sourceMatrix"))[:limit]:
        if not isinstance(item, dict):
            continue
        digest.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "host": item.get("host"),
                "authorityScore": item.get("authorityScore"),
                "tier": item.get("tier"),
            }
        )
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
_REUSABLE_ANSWER_QUALITIES = {"usable_answer", "usable_with_limitations"}
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


def _answer_pack_quality(bundle: dict[str, Any]) -> str:
    answer_pack = bundle.get("researchAnswerPack")
    if not isinstance(answer_pack, dict):
        return ""
    score = answer_pack.get("score")
    return _safe_text(score.get("qualityStatus")).lower() if isinstance(score, dict) else ""


def _has_reusable_answer_pack(bundle: dict[str, Any]) -> bool:
    bundle = _normalize_bundle_kinds(bundle)
    if bundle.get("questionKind") in _TASK_LIKE_KINDS or bundle.get("sourceKind") in _TASK_LIKE_KINDS:
        return False
    answer_pack = bundle.get("researchAnswerPack")
    if not isinstance(answer_pack, dict):
        return False
    if _answer_pack_quality(bundle) not in _REUSABLE_ANSWER_QUALITIES:
        return False
    if not _valid_research_text(answer_pack.get("answer"), min_chars=16):
        return False
    if not _as_list(answer_pack.get("sources")):
        return False
    return True


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


def _claim_digest_from_pack(pack: dict[str, Any]) -> list[dict[str, Any]]:
    claim_digest: list[dict[str, Any]] = []
    for item in _as_list(pack.get("claimTable"))[:8]:
        if not isinstance(item, dict):
            continue
        claim = _valid_research_text(item.get("claim"), min_chars=8)
        if not claim:
            continue
        claim_digest.append(
            {
                "claim": claim[:400],
                "supportingSources": _as_list(item.get("supportingSources"))[:3],
                "confidence": item.get("confidence"),
            }
        )
    return claim_digest


def _research_result_preview(bundle: dict[str, Any], *, limit: int = 900) -> str:
    final_pack = bundle.get("finalExperiencePack") if isinstance(bundle.get("finalExperiencePack"), dict) else {}
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
    final_pack = bundle.get("finalExperiencePack") if isinstance(bundle.get("finalExperiencePack"), dict) else {}
    answer_pack = bundle.get("researchAnswerPack") if isinstance(bundle.get("researchAnswerPack"), dict) else {}
    research_result = _valid_research_text(answer_pack.get("answer") or final_pack.get("researchResult") or final_pack.get("answer") or bundle.get("answer") or result_preview)
    source_urls = []
    def add_source_url(value: Any) -> None:
        if isinstance(value, dict):
            text = _safe_text(value.get("url"))
        else:
            text = _safe_text(value)
        if text and text not in source_urls:
            source_urls.append(text)

    for source in list(answer_pack.get("sources") or []):
        add_source_url(source)
    for source in list(final_pack.get("sourceUrls") or []):
        add_source_url(source)
    for url in list(bundle.get("sourceUrls") or []):
        add_source_url(url)
    for source in list(bundle.get("sourceMatrix") or []):
        if isinstance(source, dict):
            add_source_url(source)
    claim_digest = _claim_digest_from_pack({"claimTable": bundle.get("claimTable")}) or _claim_digest_from_pack(final_pack)
    missing_evidence = []
    for value in _as_list(final_pack.get("missingEvidence") or bundle.get("missingEvidence")):
        text = _safe_text(value)
        if text:
            missing_evidence.append(text[:260])
    limitations = []
    for value in _as_list(final_pack.get("limitations") or bundle.get("limitations") or bundle.get("assumptions")):
        text = _safe_text(value)
        if text:
            limitations.append(text[:260])
    quality_reasons: list[str] = []
    if not research_result and not claim_digest:
        quality_reasons.append("missing_final_research_result")
    if not source_urls and not source_digest:
        quality_reasons.append("missing_sources")
    answer_quality = _safe_text((answer_pack.get("score") or {}).get("qualityStatus")).lower() if isinstance(answer_pack.get("score"), dict) else ""
    if answer_quality in {"refresh_required", "low_quality_pack"}:
        quality_reasons.append(answer_quality)
    rejected_evidence = _as_list(answer_pack.get("rejectedEvidence"))
    source_quality_score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
    if not result_preview and not claim_digest:
        missing_evidence.append("No reliable source-backed research result was synthesized.")
    quality_status = "low_quality_pack" if quality_reasons else "reusable_candidate"
    topic_fingerprint = _safe_text(bundle.get("topicFingerprint"))
    if not topic_fingerprint:
        normalized_topic = re.sub(r"\s+", " ", _safe_text(bundle.get("question") or topic).lower()).strip()
        topic_fingerprint = uuid.uuid5(uuid.NAMESPACE_URL, normalized_topic).hex[:16]
    return {
        "experiencePackId": pack_id,
        "status": status,
        "title": topic,
        "query": bundle.get("question"),
        "summary": result_preview[:600],
        "resultPreview": result_preview,
        "applicability": _safe_text(bundle.get("deliverable") or bundle.get("researchIntent") or "general_research")[:240],
        "researchResult": research_result,
        "researchAnswerPack": answer_pack,
        "claimDigest": claim_digest,
        "qualityStatus": quality_status,
        "questionKind": bundle.get("questionKind") or "research_question",
        "sourceKind": bundle.get("sourceKind") or "research_question",
        "sourceQualityScore": source_quality_score,
        "rejectedEvidence": rejected_evidence[:8],
        "topicFingerprint": topic_fingerprint,
        "sourcePolicy": bundle.get("sourcePolicy"),
        "freshnessWindow": bundle.get("freshness"),
        "sourceUrls": source_urls[:16],
        "reusableExperiencePack": {
            "summary": result_preview[:900] if quality_status != "low_quality_pack" else "",
            "researchResult": research_result,
            "applicability": _safe_text(bundle.get("deliverable") or bundle.get("researchIntent") or "general_research")[:240],
            "sourceUrls": source_urls[:16],
            "claimDigest": claim_digest,
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
        "evidenceCheckedAt": _safe_text(bundle.get("completedAt") or bundle.get("createdAt")) or created_at,
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

        if _has_reusable_answer_pack(stored) and stored.get("confidence") in {"medium", "high"} and _as_list(stored.get("sourceMatrix")):
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
            scored.append((_experience_search_score(item, overlap=overlap, now=now), _visible_experience(item, now=now)))
    scored.sort(key=lambda pair: (pair[0], _safe_text(pair[1].get("createdAt"))), reverse=True)
    return [item for _, item in scored[: max(1, min(int(limit or 10), 50))]]


def list_evidence_bundles(*, scope: str = "global", limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        payload = _prune_expired(_read_store())
        _write_store(payload)
        items = [_visible(item) for item in payload["evidenceBundles"] if isinstance(item, dict) and _scope_matches(item, scope)]
        return items[: max(1, min(int(limit or 50), 200))]


def get_evidence_bundle(evidence_bundle_id: str) -> dict[str, Any] | None:
    with _LOCK:
        payload = _prune_expired(_read_store())
        target = _safe_text(evidence_bundle_id)
        for item in payload["evidenceBundles"]:
            if _safe_text(item.get("evidenceBundleId")) == target:
                return _visible(item)
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
                item["status"] = "active" if _safe_text(item.get("status")).lower() == "archived" else (_safe_text(item.get("status")) or "active")
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
        items = [
            _visible_experience(item, now=now)
            for item in payload["experiencePacks"]
            if isinstance(item, dict)
            and _scope_matches(item, scope)
            and (include_archived or _safe_text(item.get("status")).lower() != "archived")
        ]
        return items[: max(1, min(int(limit or 50), 200))]


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
                item["status"] = "active"
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


def research_ledger_summary(*, scope: str = "global", include_archived: bool = False) -> dict[str, Any]:
    bundles = list_evidence_bundles(scope=scope, limit=200)
    bundles_by_id = {
        _safe_text(item.get("evidenceBundleId")): item
        for item in bundles
        if isinstance(item, dict) and _safe_text(item.get("evidenceBundleId"))
    }
    packs = []
    for item in list_experience_packs(scope=scope, limit=200, include_archived=include_archived):
        enriched = dict(item)
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
        "counts": {"evidenceBundles": len(bundles), "experiencePacks": len(packs)},
        "evidenceBundles": bundles[:30],
        "experiencePacks": packs[:30],
        "confidenceTimeline": timeline,
    }
