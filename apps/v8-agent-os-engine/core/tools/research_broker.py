from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState

from core.background_context_guard import prepare_background_model_messages
from core.background_model_output import sanitize_background_model_output
from core.storage import storage
from core.tools.research_ledger import (
    archive_experience_pack,
    delete_experience_pack,
    get_evidence_bundle,
    get_experience_pack,
    list_evidence_bundles,
    promote_experience_pack,
    research_ledger_summary,
    restore_experience_pack,
    search_experience_packs_with_options,
    store_evidence_bundle,
)
from core.tools.tool_execution_envelope import ToolExecutionEnvelope, classify_failure
from core.tools.web_fetcher import source_router_search, web_read, web_search


_EVIDENCE_TTL_SECONDS = 6 * 60 * 60
_RESEARCH_TOOL_DEADLINE_MS = 120_000
_RESEARCH_SHARD_DEADLINE_MS = 45_000
_RESEARCH_SOURCE_READ_DEADLINE_MS = 35_000
_RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS = 60_000
_EVIDENCE_LEDGER: dict[str, dict[str, Any]] = {}
_AUTHORITATIVE_HOST_HINTS = (
    "docs.",
    "developer.",
    "developers.",
    "platform.",
    "api.",
    "learn.microsoft.com",
    "cloud.google.com",
    "docs.aws.amazon.com",
    "github.com",
)
_LOW_QUALITY_HOST_HINTS = (
    "pinterest.",
    "quora.",
    "reddit.",
    "medium.",
    "zhihu.",
    "csdn.",
    "stackoverflow.com/questions",
)
_RESEARCH_SOURCE_CATALOG_PATH = Path(__file__).resolve().parents[2] / "runtimes" / "research" / "assets" / "source_quality_catalog.json"
_VIDEO_RESEARCH_TERMS = (
    "video",
    "youtube",
    "bilibili",
    "tiktok",
    "douyin",
    "shorts",
    "reel",
    "views",
    "likes",
    "ranking",
    "leaderboard",
    "trending",
    "视频",
    "短视频",
    "榜单",
    "排行",
    "播放",
    "点赞",
    "爆款",
)
_POPULARITY_RE = re.compile(
    r"(?P<number>\d+(?:[\.,]\d+)?)\s*(?P<unit>k|m|b|万|亿)?\s*(?P<label>views?|likes?|播放|观看|点赞|赞|收藏|shares?|comments?|评论|弹幕)",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _research_config() -> dict[str, Any]:
    try:
        raw = dict((storage.get_supervisor_config() or {}).get("research") or {})
    except Exception:
        raw = {}
    enabled = raw.get("enabled")
    default_shards = _as_int(raw.get("defaultShardCount"), 10)
    max_shards = _as_int(raw.get("maxShardCount"), 30)
    max_rounds = _as_int(raw.get("maxRounds"), 5)
    return {
        "enabled": True if enabled is None else bool(enabled),
        "defaultShardCount": max(1, min(default_shards, 30)),
        "maxShardCount": max(1, min(max_shards, 30)),
        "maxRounds": max(1, min(max_rounds, 5)),
        "evidenceTtlSeconds": max(60, _as_int(raw.get("evidenceTtlSeconds"), _EVIDENCE_TTL_SECONDS)),
        "architectAgentSynthesisEnabled": bool(raw.get("architectAgentSynthesisEnabled", True)),
        "architectAgentTimeoutSeconds": max(5, min(_as_int(raw.get("architectAgentTimeoutSeconds"), 60), 90)),
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = re.split(r"[,\n;]+", values)
    else:
        raw_values = list(values or [])
    result: list[str] = []
    for item in raw_values:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
    return result


@lru_cache(maxsize=1)
def _source_catalog() -> dict[str, Any]:
    try:
        payload = json.loads(_RESEARCH_SOURCE_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {"entries": []}
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    return {
        "version": payload.get("version"),
        "entries": [entry for entry in entries if isinstance(entry, dict)],
    }


def _catalog_match(url: str) -> dict[str, Any] | None:
    host = _host(url)
    if not host:
        return None
    for entry in _source_catalog().get("entries") or []:
        for raw_host in list(entry.get("hosts") or []):
            catalog_host = str(raw_host or "").strip().lower()
            if catalog_host and (host == catalog_host or host.endswith(f".{catalog_host}")):
                return entry
    return None


def _is_video_research(*values: Any) -> bool:
    text = " ".join(str(value or "").lower() for value in values)
    return any(term in text for term in _VIDEO_RESEARCH_TERMS)


def _catalog_hosts_by_category(category: str) -> list[str]:
    hosts: list[str] = []
    for entry in _source_catalog().get("entries") or []:
        if str(entry.get("category") or "") != category:
            continue
        for host in list(entry.get("hosts") or []):
            normalized = str(host or "").strip().lower()
            if normalized and normalized not in hosts:
                hosts.append(normalized)
    return hosts


def _popularity_signals(text: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for match in _POPULARITY_RE.finditer(str(text or "")[:1200]):
        signals.append(
            {
                "metric": match.group("label").lower(),
                "valueText": match.group(0),
            }
        )
        if len(signals) >= 4:
            break
    return signals


def _host(value: str) -> str:
    parsed = urlparse(value)
    return (parsed.hostname or "").lower()


def _domain_from_seed(value: str) -> str:
    host = _host(value)
    if host.startswith("www."):
        host = host[4:]
    return host


def _query_slug(value: str) -> str:
    normalized = re.sub(r"\s+", " ", _safe_text(value).lower())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]


def _source_quality(
    url: str,
    *,
    allowed_domains: list[str],
    source_policy: str,
    title: str = "",
    snippet: str = "",
    video_research: bool = False,
) -> dict[str, Any]:
    normalized_host = _host(url)
    allowed_match = any(normalized_host == domain or normalized_host.endswith(f".{domain}") for domain in allowed_domains)
    catalog_entry = _catalog_match(url)
    authoritative_hint = allowed_match or any(hint in normalized_host or hint in url.lower() for hint in _AUTHORITATIVE_HOST_HINTS)
    low_quality_hint = any(hint in normalized_host or hint in url.lower() for hint in _LOW_QUALITY_HOST_HINTS)
    popularity = _popularity_signals(f"{title}\n{snippet}")
    score = 50
    reasons: list[str] = []
    if allowed_match:
        score += 30
        reasons.append("allowed_domain_match")
    if catalog_entry:
        boost = _as_int(catalog_entry.get("authorityBoost"), 0)
        score += boost
        reasons.append(f"source_catalog:{catalog_entry.get('id')}")
    if authoritative_hint:
        score += 20
        reasons.append("authoritative_host_hint")
    if str(source_policy or "").strip().lower() in {"official", "authoritative", "primary"} and authoritative_hint:
        score += 10
        reasons.append("source_policy_match")
    if video_research and catalog_entry and str(catalog_entry.get("category") or "") in {"video_platform", "creative_showcase"}:
        score += 15
        reasons.append("video_popularity_source")
    if video_research and popularity:
        score += 10
        reasons.append("popularity_signal_detected")
    if low_quality_hint:
        score -= 25
        reasons.append("low_quality_host_hint")
    score = max(0, min(score, 100))
    return {
        "host": normalized_host,
        "authorityScore": score,
        "tier": "primary" if score >= 80 else ("secondary" if score >= 55 else "weak"),
        "reasons": reasons,
        "catalogSourceId": catalog_entry.get("id") if catalog_entry else None,
        "catalogCategory": catalog_entry.get("category") if catalog_entry else None,
        "authorityTier": catalog_entry.get("authorityTier") if catalog_entry else None,
        "popularitySignals": popularity,
    }


def _cleanup_ledger() -> None:
    now = time.time()
    for key, entry in list(_EVIDENCE_LEDGER.items()):
        expires_at = float(entry.get("_expiresAt") or 0)
        if expires_at and expires_at < now:
            _EVIDENCE_LEDGER.pop(key, None)


def _ledger_scope(state: dict[str, Any] | None) -> str:
    context = dict(state or {})
    for key in ("run_id", "runId", "conversation_id", "conversationId", "session_id", "sessionId"):
        value = _safe_text(context.get(key))
        if value:
            return value
    return "global"


def _store_evidence(bundle: dict[str, Any], *, state: dict[str, Any] | None) -> dict[str, Any]:
    _cleanup_ledger()
    config = _research_config()
    scope = _ledger_scope(state)
    bundle_id = _safe_text(bundle.get("evidenceBundleId")) or f"research_{uuid.uuid4().hex[:12]}"
    stored = {
        **bundle,
        "evidenceBundleId": bundle_id,
        "scope": scope,
        "createdAt": _utc_now_iso(),
        "retention": "run_scoped_memory",
        "_expiresAt": time.time() + config["evidenceTtlSeconds"],
    }
    _EVIDENCE_LEDGER[bundle_id] = stored
    return store_evidence_bundle(stored, ttl_seconds=config["evidenceTtlSeconds"], scope=scope)


def _visible_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bundle.items() if not str(key).startswith("_")}


def _build_shards(
    *,
    question: str,
    research_intent: str,
    source_policy: str,
    seed_urls: list[str],
    allowed_domains: list[str],
    max_shards: int,
) -> list[dict[str, Any]]:
    base_question = _safe_text(question)
    intent = _safe_text(research_intent) or "general_research"
    policy = _safe_text(source_policy).lower()
    video_research = _is_video_research(base_question, intent, policy)
    seed_domains = [_domain_from_seed(url) for url in seed_urls]
    domains = [domain for domain in [*allowed_domains, *seed_domains] if domain]
    queries: list[tuple[str, str]] = []
    queries.append((base_question, "baseline"))
    if policy in {"official", "authoritative", "primary", ""}:
        queries.append((f"{base_question} official documentation", "official_docs"))
        queries.append((f"{base_question} API reference", "api_reference"))
    if intent:
        queries.append((f"{base_question} {intent}", "intent"))
    if video_research:
        queries.append((f"{base_question} top videos views likes ranking", "video_popularity"))
        queries.append((f"{base_question} 榜单 播放量 点赞", "video_popularity_cn"))
        for domain in _catalog_hosts_by_category("video_platform")[:6]:
            queries.append((f"site:{domain} {base_question}", f"video_site:{domain}"))
        for domain in _catalog_hosts_by_category("creative_showcase")[:4]:
            queries.append((f"site:{domain} {base_question}", f"creative_site:{domain}"))
    for domain in domains[: max(0, max_shards - len(queries))]:
        queries.append((f"site:{domain} {base_question}", f"site:{domain}"))

    shards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query, shard_kind in queries:
        normalized = _safe_text(query)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        shards.append(
            {
                "shardId": f"shard_{len(shards) + 1}_{_query_slug(normalized)}",
                "kind": shard_kind,
                "query": normalized,
                "reason": shard_kind,
            }
        )
        if len(shards) >= max_shards:
            break
    return shards


def _parse_tool_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "rawPreview": str(value or "")[:800]}
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "non_object_tool_result", "rawPreview": str(value or "")[:800]}


def _render_payload(payload: dict[str, Any], *, max_chars: int = 12000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    compact = dict(payload)
    omitted = dict(compact.get("omitted") or {})
    omitted["agentVisibleOriginalChars"] = len(text)
    omitted["agentVisibleBudgetChars"] = max_chars
    compact["omitted"] = omitted
    compact["truncatedForAgentVisibleOutput"] = True
    for key, limit in (("sourceMatrix", 5), ("citations", 5), ("shards", 4), ("rawRefs", 6), ("items", 10)):
        value = compact.get(key)
        if isinstance(value, list) and len(value) > limit:
            omitted[f"{key}OmittedForBudget"] = len(value) - limit
            compact[key] = value[:limit]
    text = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    fallback = {
        "ok": bool(payload.get("ok")),
        "kind": payload.get("kind") or payload.get("mode") or "research_payload",
        "summary": _safe_text(payload.get("summary"))[:600],
        "answer": _safe_text(payload.get("answer") or payload.get("resultPreview"))[:1600],
        "finalExperiencePack": {
            key: value
            for key, value in dict(payload.get("finalExperiencePack") or payload.get("researchResult") or {}).items()
            if key
            in {
                "kind",
                "architectAgentId",
                "architectName",
                "headline",
                "researchResult",
                "confidence",
                "sourceUrls",
                "synthesisMode",
                "modelSynthesis",
            }
        },
        "evidenceBundleId": payload.get("evidenceBundleId"),
        "confidence": payload.get("confidence"),
        "authorityScore": payload.get("authorityScore"),
        "researchLoopState": payload.get("researchLoopState"),
        "experienceReuse": payload.get("experienceReuse"),
        "claimTable": list(payload.get("claimTable") or [])[:5],
        "conflictMatrix": list(payload.get("conflictMatrix") or [])[:5],
        "sourceMatrix": list(payload.get("sourceMatrix") or [])[:3],
        "omitted": {
            **omitted,
            "fallback": "agent_visible_budget_fallback",
            "detailTool": "research_broker(mode='get_evidence', evidenceBundleId=...)",
        },
        "recommendedNextAction": payload.get("recommendedNextAction") or "get_evidence",
    }
    return json.dumps(fallback, ensure_ascii=False, indent=2)


def _compact_research_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _research_sentences(value: Any, *, limit: int = 3) -> list[str]:
    text = _compact_research_text(value, limit=900)
    if not text:
        return []
    parts = [
        item.strip(" \t\r\n-•。；;")
        for item in re.split(r"(?<=[。！？!?\.])\s+|[；;]\s*", text)
        if item.strip(" \t\r\n-•。；;")
    ]
    if not parts:
        parts = [text]
    result: list[str] = []
    for part in parts:
        if len(part) < 12 and len(parts) > 1:
            continue
        result.append(_compact_research_text(part, limit=260))
        if len(result) >= limit:
            break
    return result or [_compact_research_text(text, limit=260)]


def _fetched_source_map(shards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    fetched: dict[str, dict[str, Any]] = {}
    for shard in shards:
        for item in list(shard.get("fetchedTopSources") or []):
            if not isinstance(item, dict):
                continue
            url = _safe_text(item.get("url"))
            if url and url not in fetched:
                fetched[url] = item
    return fetched


def _confidence_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(_safe_text(value).lower(), 0)


def _topic_fingerprint(value: str) -> str:
    normalized = re.sub(r"\s+", " ", _safe_text(value).lower())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


_REUSE_STOP_WORDS = {
    "what",
    "are",
    "is",
    "the",
    "a",
    "an",
    "for",
    "in",
    "to",
    "and",
    "or",
    "of",
    "on",
    "by",
    "as",
    "about",
    "compare",
    "claims",
    "that",
    "when",
    "where",
    "which",
    "how",
    "why",
    "best",
    "current",
    "latest",
    "using",
    "use",
    "uses",
    "with",
    "from",
    "source",
    "sources",
    "cite",
    "official",
    "please",
    "请",
    "说明",
    "来源",
    "最新",
    "当前",
    "如何",
}


def _reuse_tokens(value: str) -> set[str]:
    text = re.sub(r"\s+", " ", _safe_text(value).lower())
    tokens = {
        token
        for token in re.split(r"[^\w\u4e00-\u9fff]+", text)
        if len(token) >= 2 and token not in _REUSE_STOP_WORDS
    }
    # CJK questions are often one long token after punctuation splitting. Add
    # coarse bigrams so near-identical Chinese titles/queries can still match
    # without letting generic words dominate.
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    if len(cjk) >= 4:
        tokens.update(cjk[index : index + 2] for index in range(0, min(len(cjk) - 1, 24)))
    return tokens


def _reuse_topic_match(question: str, pack: dict[str, Any]) -> tuple[bool, str]:
    normalized_question = re.sub(r"\s+", " ", _safe_text(question).lower()).strip()
    candidate_text = " ".join(
        [
            _safe_text(pack.get("query")),
            _safe_text(pack.get("title")),
            _safe_text(pack.get("topicFingerprint")),
        ]
    ).lower()
    if not normalized_question:
        return False, "empty_question"
    if _safe_text(pack.get("topicFingerprint")) == _topic_fingerprint(question):
        return True, "topic_fingerprint_match"
    if normalized_question and normalized_question in candidate_text:
        return True, "exact_question_contained_in_candidate"
    q_tokens = _reuse_tokens(question)
    p_tokens = _reuse_tokens(candidate_text)
    if not q_tokens or not p_tokens:
        return False, "insufficient_topic_tokens"
    overlap = q_tokens.intersection(p_tokens)
    ratio = len(overlap) / max(1, min(len(q_tokens), len(p_tokens)))
    if len(overlap) >= 2 and ratio >= 0.35:
        return True, f"topic_overlap:{ratio:.2f}"
    return False, f"topic_overlap_too_low:{ratio:.2f}"


def _source_urls_from_matrix(source_matrix: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for item in source_matrix:
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        if url and url not in urls:
            urls.append(url)
    return urls


def _flatten_provider_attempts(shards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for shard in shards:
        matrix = shard.get("providerAttemptMatrix")
        if isinstance(matrix, list):
            for item in matrix:
                if isinstance(item, dict):
                    attempts.append({"shardId": shard.get("shardId"), "query": shard.get("query"), **item})
        elif matrix:
            attempts.append({"shardId": shard.get("shardId"), "query": shard.get("query"), "attempt": matrix})
        elif shard.get("provider") or shard.get("networkRoute"):
            attempts.append(
                {
                    "shardId": shard.get("shardId"),
                    "query": shard.get("query"),
                    "provider": shard.get("provider"),
                    "networkRoute": shard.get("networkRoute"),
                    "ok": shard.get("ok"),
                    "fallbackReason": shard.get("errors"),
                }
            )
    return attempts[:40]


def _experience_reuse_decision(
    packs: list[dict[str, Any]],
    *,
    question: str,
    source_policy: str,
    freshness: str,
    min_confidence: str = "",
) -> dict[str, Any]:
    if not packs:
        return {
            "reuseDecision": "ignore",
            "reason": "no_matching_experience_pack",
            "candidatePackId": None,
            "topicFingerprint": _topic_fingerprint(question),
        }
    min_rank = _confidence_rank(min_confidence) or 2
    normalized_policy = _safe_text(source_policy).lower()
    normalized_freshness = _safe_text(freshness).lower()
    for pack in packs:
        matched, match_reason = _reuse_topic_match(question, pack)
        if not matched:
            continue
        quality_status = _safe_text(pack.get("qualityStatus")).lower()
        invalidation_reason = _safe_text(pack.get("invalidationReason"))
        has_final_result = bool(_safe_text(pack.get("researchResult")) or list(pack.get("claimDigest") or []))
        has_sources = bool(list(pack.get("sourceUrls") or []) or list(pack.get("sourceMatrixDigest") or []))
        try:
            authority_score = float(pack.get("authorityScore") or 0)
        except (TypeError, ValueError):
            authority_score = 0.0
        if quality_status == "low_quality_pack" or invalidation_reason or not has_final_result or not has_sources or authority_score < 35:
            reasons = []
            if quality_status == "low_quality_pack":
                reasons.append("low_quality_pack")
            if invalidation_reason:
                reasons.append(invalidation_reason)
            if not has_final_result:
                reasons.append("missing_final_research_result")
            if not has_sources:
                reasons.append("missing_sources")
            if authority_score < 35:
                reasons.append("low_authority")
            return {
                "reuseDecision": "refresh",
                "reason": "refresh_required_due_to_pack_quality",
                "qualityReasons": reasons,
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "candidateConfidence": pack.get("confidence"),
                "topicFingerprint": pack.get("topicFingerprint") or _topic_fingerprint(question),
            }
        confidence = _safe_text(pack.get("confidence")).lower()
        if _confidence_rank(confidence) < min_rank:
            return {
                "reuseDecision": "refresh",
                "reason": "candidate_confidence_below_threshold",
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "candidateConfidence": confidence,
                "requiredConfidence": min_confidence or "medium",
                "topicFingerprint": _topic_fingerprint(question),
            }
        pack_policy = _safe_text(pack.get("sourcePolicy")).lower()
        if pack_policy and normalized_policy and pack_policy != normalized_policy:
            return {
                "reuseDecision": "refresh",
                "reason": "source_policy_changed",
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "previousSourcePolicy": pack_policy,
                "requestedSourcePolicy": normalized_policy,
                "topicFingerprint": _topic_fingerprint(question),
            }
        if normalized_freshness in {"latest", "current", "fresh", "recent", "实时", "最新"}:
            return {
                "reuseDecision": "refresh",
                "reason": "freshness_requires_delta_research",
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "freshness": freshness,
                "topicFingerprint": _topic_fingerprint(question),
            }
        return {
            "reuseDecision": "reuse",
            "reason": "high_confidence_pack_matches_topic_and_policy",
            "matchReason": match_reason,
            "candidatePackId": pack.get("experiencePackId"),
            "candidateTitle": pack.get("title"),
            "candidateConfidence": confidence,
            "skippedSearches": True,
            "topicFingerprint": pack.get("topicFingerprint") or _topic_fingerprint(question),
        }
    return {
        "reuseDecision": "ignore",
        "reason": "no_topic_matched_reusable_candidate_after_filtering",
        "candidatePackId": None,
        "topicFingerprint": _topic_fingerprint(question),
    }


def _bundle_from_reused_pack(pack: dict[str, Any], *, question: str, reuse: dict[str, Any], deliverable: str) -> dict[str, Any]:
    source_matrix = list(pack.get("sourceMatrixDigest") or [])
    source_urls = _source_urls_from_matrix(source_matrix)
    result = _safe_text(pack.get("researchResult") or pack.get("resultPreview") or pack.get("summary"))
    if not result:
        result = "Reused research experience pack, but no detailed research result was stored."
    claim_table = list(pack.get("claimDigest") or [])
    architect_pack = {
        "kind": "research_result_pack",
        "architectAgentId": "web-research-architect",
        "architectName": "Web Research Architect",
        "question": question,
        "headline": f"Reused experience pack: {pack.get('title') or pack.get('experiencePackId')}",
        "answer": result,
        "researchResult": result,
        "claimTable": claim_table,
        "sourceUrls": [{"url": url} for url in source_urls],
        "confidence": pack.get("confidence"),
        "authorityScore": pack.get("authorityScore"),
        "createdAt": _utc_now_iso(),
    }
    return {
        "ok": True,
        "kind": "research_evidence_bundle",
        "summary": architect_pack["headline"],
        "question": question,
        "researchIntent": "experience_reuse",
        "sourcePolicy": pack.get("sourcePolicy"),
        "freshness": pack.get("freshnessWindow"),
        "deliverable": deliverable,
        "answer": result,
        "resultPreview": result,
        "researchResult": architect_pack,
        "finalExperiencePack": architect_pack,
        "claimTable": claim_table,
        "conflictMatrix": [],
        "missingEvidence": [],
        "assumptions": [],
        "sourceMatrix": source_matrix,
        "sourceUrls": source_urls,
        "providerAttemptMatrix": [],
        "researchLoopState": {
            "phase": "experience_reused",
            "rounds": [],
            "stopReason": "experience_reused",
            "questions": [question],
            "readSources": source_urls,
            "uncoveredClaims": [],
            "conflictClaims": [],
            "nextQueries": [],
        },
        "experienceReuse": {**reuse, "pack": pack},
        "confidence": pack.get("confidence") or "medium",
        "authorityScore": pack.get("authorityScore"),
        "recommendedNextAction": "use_reused_experience_pack",
    }


def _deterministic_web_research_architect_pack(
    *,
    question: str,
    source_matrix: list[dict[str, Any]],
    shards: list[dict[str, Any]],
    confidence: str,
    average_authority: float,
) -> dict[str, Any]:
    """Build the final research-result pack consumed by Supervisor and detail tools.

    This deterministic synthesis is intentionally source-backed and compact. It
    represents the Research Runtime's Web Research Architect handoff: the model
    sees conclusions and source URLs, while raw search/debug payloads stay behind
    rawRef/detail tooling.
    """

    fetched = _fetched_source_map(shards)
    source_urls: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    claim_table: list[dict[str, Any]] = []
    missing_evidence: list[str] = []
    conflict_matrix: list[dict[str, Any]] = []
    for source in source_matrix[:3]:
        url = _safe_text(source.get("url"))
        if not url:
            continue
        read_payload = fetched.get(url) or {}
        title = _safe_text(read_payload.get("title") or source.get("title") or url)
        host = _safe_text(source.get("host")) or _host(url)
        source_entry = {
            "title": title,
            "url": url,
            "host": host,
            "tier": source.get("tier"),
            "authorityScore": source.get("authorityScore"),
        }
        source_urls.append({key: value for key, value in source_entry.items() if value not in (None, "", [], {})})
        source_text = (
            _safe_text(read_payload.get("text"))
            or _safe_text(read_payload.get("markdown"))
            or _safe_text(read_payload.get("textPreview"))
            or _safe_text(source.get("snippet"))
        )
        if not source_text:
            missing_evidence.append(f"{title or url}: no readable body text")
        for sentence in _research_sentences(source_text, limit=2):
            if not sentence:
                continue
            finding = {
                "claim": sentence,
                "sourceTitle": title,
                "sourceUrl": url,
                "host": host,
            }
            findings.append(finding)
            claim_table.append(
                {
                    "claim": sentence,
                    "supportingSources": [source_entry],
                    "refutingSources": [],
                    "confidence": source.get("tier") or "secondary",
                }
            )
            lowered = sentence.lower()
            if any(token in lowered for token in ("deprecated", "not supported", "unsupported", "conflict", "不支持", "已弃用", "冲突")):
                conflict_matrix.append(
                    {
                        "claim": sentence,
                        "kind": "possible_conflict_or_limitation",
                        "sources": [source_entry],
                    }
                )
            if len(findings) >= 8:
                break
        if len(findings) >= 8:
            break

    if findings:
        headline = f"Web Research Architect synthesized {len(findings)} source-backed finding(s) from {len(source_urls)} source(s)."
        answer_lines = ["Web Research Architect final result:"]
        for item in findings[:6]:
            title = _compact_research_text(item.get("sourceTitle"), limit=88)
            claim = _compact_research_text(item.get("claim"), limit=280)
            answer_lines.append(f"- {claim} ({title})")
        if len(findings) > 6:
            answer_lines.append(f"- ... {len(findings) - 6} more source-backed finding(s) omitted from the visible pack.")
        limitations: list[str] = []
        if confidence == "low":
            limitations.append("Evidence confidence is low; verify with more authoritative or primary sources before making final decisions.")
        if len(source_urls) < 2:
            limitations.append("Only one usable source was available; treat conclusions as provisional.")
        if missing_evidence:
            limitations.append("Some ranked sources could not be read as clean full text; detail/refetch may be needed.")
    else:
        headline = "Web Research Architect could not synthesize a reliable result because no usable source text was collected."
        answer_lines = [
            "Web Research Architect final result:",
            "- No reliable source-backed findings were collected. Revise the query, grant Research Runtime, or provide authoritative seed URLs.",
        ]
        limitations = ["No usable source text was collected."]
        missing_evidence.append("No usable source-backed findings were collected.")

    return {
        "kind": "research_result_pack",
        "architectAgentId": "web-research-architect",
        "architectName": "Web Research Architect",
        "synthesisMode": "deterministic_fallback",
        "question": question,
        "headline": headline,
        "answer": "\n".join(answer_lines),
        "researchResult": "\n".join(answer_lines),
        "keyFindings": findings,
        "claimTable": claim_table,
        "conflictMatrix": conflict_matrix,
        "missingEvidence": missing_evidence,
        "assumptions": [] if findings else ["Research result is provisional until readable sources are supplied."],
        "sourceUrls": source_urls,
        "confidence": confidence,
        "authorityScore": average_authority,
        "limitations": limitations,
        "createdAt": _utc_now_iso(),
    }


def _research_architect_sources_for_prompt(source_matrix: list[dict[str, Any]], shards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fetched = _fetched_source_map(shards)
    sources: list[dict[str, Any]] = []
    prioritized_sources = sorted(
        source_matrix[:10],
        key=lambda source: float(source.get("authorityScore") or 0),
        reverse=True,
    )
    for source in prioritized_sources:
        url = _safe_text(source.get("url"))
        if not url:
            continue
        read_payload = fetched.get(url) or {}
        source_text = (
            _safe_text(read_payload.get("text"))
            or _safe_text(read_payload.get("markdown"))
            or _safe_text(read_payload.get("textPreview"))
            or _safe_text(source.get("snippet"))
        )
        if "About Press Copyright Contact us Creators" in source_text and "YouTube works" in source_text:
            continue
        sources.append(
            {
                "title": _safe_text(read_payload.get("title") or source.get("title") or url),
                "url": url,
                "host": _safe_text(source.get("host")) or _host(url),
                "tier": source.get("tier"),
                "authorityScore": source.get("authorityScore"),
                "freshness": source.get("freshness"),
                "provider": source.get("provider"),
                "networkRoute": source.get("networkRoute"),
                "text": _compact_research_text(source_text, limit=600),
            }
        )
        if len(sources) >= 5:
            break
    return sources


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _safe_text(text)
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        candidates.append(raw[first : last + 1])
    candidates.append(raw)
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            try:
                import yaml  # type: ignore

                payload = yaml.safe_load(candidate)
            except Exception:
                continue
        if isinstance(payload, dict):
            return payload
    return None


def _extract_jsonish_string_field(text: str, field: str) -> str:
    raw = str(text or "")
    marker = f'"{field}"'
    marker_index = raw.find(marker)
    if marker_index < 0:
        return ""
    colon_index = raw.find(":", marker_index + len(marker))
    if colon_index < 0:
        return ""
    quote_index = raw.find('"', colon_index + 1)
    if quote_index < 0:
        return ""
    end_index = -1
    delimiter_match = re.search(
        r'",\s*"(?:headline|researchResult|claimTable|conflictMatrix|missingEvidence|assumptions)"\s*:',
        raw[quote_index + 1 :],
        re.DOTALL,
    )
    if delimiter_match:
        end_index = quote_index + 1 + delimiter_match.start()
    if end_index < 0:
        end_index = raw.find('"}', quote_index + 1)
    if end_index < 0:
        end_index = min(len(raw), quote_index + 1200)
    value = raw[quote_index + 1 : end_index]
    return value.replace('\\"', '"').replace("\\n", "\n").strip()


def _create_web_research_architect_llm() -> tuple[Any, str, str]:
    from core.llm_factory import llm_factory

    agent_id = "web-research-architect"
    try:
        model_id = _safe_text(storage.get_agent_model_binding(agent_id))
    except Exception:
        model_id = ""
    if model_id:
        return llm_factory.create_chat_model(model_id, temperature=0.1, max_tokens=1800, _role=agent_id), model_id, agent_id
    last_error: Exception | None = None
    for role in ("research", "summary", "supervisor"):
        try:
            return llm_factory.create_for_role(role, temperature=0.1, max_tokens=1800), "", role
        except Exception as exc:  # noqa: BLE001 - role fallback is intentional.
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError("No model configured for Web Research Architect synthesis.")


def _invoke_web_research_architect_agent(
    *,
    question: str,
    source_matrix: list[dict[str, Any]],
    shards: list[dict[str, Any]],
    confidence: str,
    average_authority: float,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    sources = _research_architect_sources_for_prompt(source_matrix, shards)
    if not sources:
        return None

    def _call() -> dict[str, Any] | None:
        llm, model_id, role = _create_web_research_architect_llm()

        def _parse_response(raw_content: str, *, parse_mode: str) -> dict[str, Any] | None:
            parsed = _extract_json_object(raw_content)
            if parsed:
                parsed["_modelRole"] = role
                parsed["_modelId"] = model_id
                parsed["_modelParseMode"] = parse_mode
                return parsed
            extracted_result = _extract_jsonish_string_field(raw_content, "researchResult")
            if extracted_result:
                return {
                    "headline": _extract_jsonish_string_field(raw_content, "headline")
                    or "Web Research Architect synthesized final evidence.",
                    "researchResult": extracted_result,
                    "claimTable": [],
                    "conflictMatrix": [],
                    "missingEvidence": [],
                    "assumptions": [],
                    "_modelRole": role,
                    "_modelId": model_id,
                    "_modelParseMode": f"{parse_mode}_jsonish_text",
                }
            return None

        compact_sources = [
            {
                "title": _safe_text(source.get("title")),
                "url": _safe_text(source.get("url")),
                "tier": source.get("tier"),
                "authorityScore": source.get("authorityScore"),
                "text": _compact_research_text(_safe_text(source.get("text")), limit=420),
            }
            for source in sources[:4]
        ]
        system_prompt = (
            "你是 Web Research Architect。根据已读取的 SOURCES 做冲突归纳和结论压缩。"
            "只能使用 SOURCES 中的内容和 URL，不要引入外部事实。"
        )
        prompts = [
            (
                "必须在最终回答内容里输出一个 JSON 对象，不要 Markdown。\n"
                "字段：headline, researchResult, claimTable, conflictMatrix, missingEvidence, assumptions。\n"
                "claimTable 每项至少包含 claim 和 supportingSources；如果更方便，也可写 sourceURL，但 URL 必须来自 SOURCES。\n"
                "researchResult 必须面向后续 agent 阅读：完整、紧凑、标注限制，不能照搬导航栏或搜索摘要。\n"
                f"QUESTION: {question}"
            ),
            (
                "只根据已准备的 SOURCES 输出 JSON，不要 Markdown。字段 headline, researchResult, claimTable, conflictMatrix, missingEvidence, assumptions。"
                "不要引入外部事实；不确定就写 missingEvidence。\n"
                f"QUESTION: {question}"
            ),
        ]
        raw_previews: list[str] = []
        for index, prompt in enumerate(prompts):
            source_limit = 4 if index == 0 else 3
            prepared_context = prepare_background_model_messages(
                system_prompt=system_prompt,
                instruction=prompt,
                materials=[
                    {
                        "title": "Research source matrix",
                        "kind": "research_sources",
                        "content": json.dumps(compact_sources[:source_limit], ensure_ascii=False),
                    }
                ],
                runtime_kind="research",
                target_role="web-research-architect",
                resolved_model_id=model_id,
                component="research",
                node="web_research_architect",
            )
            response = llm.invoke(prepared_context.messages, config={"callbacks": []})
            raw_content = sanitize_background_model_output(response).text
            if raw_content:
                raw_previews.append(raw_content[:500])
            parsed = _parse_response(raw_content, parse_mode="json" if index == 0 else "retry_json")
            if parsed:
                return parsed
        return {
            "_agentError": "architect_agent_no_json",
            "_rawPreview": "\n--- retry ---\n".join(raw_previews)[:800],
            "_modelRole": role,
            "_modelId": model_id,
        }

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call)
    try:
        return future.result(timeout=max(5, timeout_seconds))
    except concurrent.futures.TimeoutError:
        return {"_agentError": "architect_agent_timeout"}
    except Exception as exc:  # noqa: BLE001 - fallback must keep research runtime alive.
        return {"_agentError": f"{type(exc).__name__}: {exc}"}
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _merge_web_research_architect_agent_pack(
    base_pack: dict[str, Any],
    agent_pack: dict[str, Any] | None,
    *,
    question: str,
) -> dict[str, Any]:
    if not isinstance(agent_pack, dict) or agent_pack.get("_agentError"):
        fallback_reason = "architect_agent_no_result"
        raw_preview = ""
        if isinstance(agent_pack, dict) and agent_pack.get("_agentError"):
            fallback_reason = _safe_text(agent_pack.get("_agentError")) or fallback_reason
            raw_preview = _safe_text(agent_pack.get("_rawPreview"))[:500]
        return {
            **base_pack,
            "synthesisMode": "deterministic_fallback",
            "modelSynthesis": {
                "used": False,
                "agentId": "web-research-architect",
                "fallbackReason": fallback_reason,
                **({"rawPreview": raw_preview} if raw_preview else {}),
            },
        }
    research_result = _safe_text(agent_pack.get("researchResult") or agent_pack.get("answer") or agent_pack.get("summary"))
    if not research_result:
        return {
            **base_pack,
            "modelSynthesis": {
                "used": False,
                "agentId": "web-research-architect",
                "fallbackReason": "architect_agent_missing_research_result",
            },
        }
    known_urls = {item.get("url") for item in list(base_pack.get("sourceUrls") or []) if isinstance(item, dict) and item.get("url")}
    claim_table: list[dict[str, Any]] = []
    for raw_item in list(agent_pack.get("claimTable") or [])[:12]:
        if not isinstance(raw_item, dict):
            continue
        supporting: list[dict[str, Any]] = []
        for source in list(raw_item.get("supportingSources") or [])[:4]:
            if not isinstance(source, dict):
                continue
            url = _safe_text(source.get("url"))
            if url and url in known_urls:
                supporting.append({"title": _safe_text(source.get("title")) or url, "url": url})
        if not supporting:
            candidate_urls: list[str] = []
            for field in ("sourceURL", "sourceUrl", "url"):
                value = _safe_text(raw_item.get(field))
                if value:
                    candidate_urls.append(value)
            for value in list(raw_item.get("sourceUrls") or [])[:4]:
                if isinstance(value, str):
                    candidate_urls.append(value)
                elif isinstance(value, dict):
                    candidate_urls.append(_safe_text(value.get("url")))
            for url in candidate_urls[:4]:
                if url and url in known_urls:
                    supporting.append({"title": url, "url": url})
        if not supporting:
            continue
        claim = _safe_text(raw_item.get("claim"))
        if claim:
            claim_table.append(
                {
                    "claim": claim,
                    "supportingSources": supporting,
                    "refutingSources": list(raw_item.get("refutingSources") or [])[:4],
                    "confidence": _safe_text(raw_item.get("confidence")) or base_pack.get("confidence"),
                }
            )
    if not claim_table:
        claim_table = list(base_pack.get("claimTable") or [])
    headline = _safe_text(agent_pack.get("headline")) or f"Web Research Architect synthesized final evidence for: {question}"
    return {
        **base_pack,
        "headline": headline,
        "answer": research_result,
        "researchResult": research_result,
        "claimTable": claim_table,
        "conflictMatrix": list(agent_pack.get("conflictMatrix") or base_pack.get("conflictMatrix") or [])[:12],
        "missingEvidence": list(agent_pack.get("missingEvidence") or base_pack.get("missingEvidence") or [])[:12],
        "assumptions": list(agent_pack.get("assumptions") or base_pack.get("assumptions") or [])[:12],
        "synthesisMode": "model_agent",
        "modelSynthesis": {
            "used": True,
            "agentId": "web-research-architect",
            "agentName": "Web Research Architect",
            "modelRole": agent_pack.get("_modelRole"),
            "modelId": agent_pack.get("_modelId"),
            "parseMode": agent_pack.get("_modelParseMode") or "json",
        },
    }


def _web_research_architect_pack(
    *,
    question: str,
    source_matrix: list[dict[str, Any]],
    shards: list[dict[str, Any]],
    confidence: str,
    average_authority: float,
) -> dict[str, Any]:
    base_pack = _deterministic_web_research_architect_pack(
        question=question,
        source_matrix=source_matrix,
        shards=shards,
        confidence=confidence,
        average_authority=average_authority,
    )
    config = _research_config()
    if not config.get("architectAgentSynthesisEnabled", True):
        return {
            **base_pack,
            "modelSynthesis": {
                "used": False,
                "agentId": "web-research-architect",
                "fallbackReason": "architect_agent_synthesis_disabled",
            },
        }
    agent_pack = _invoke_web_research_architect_agent(
        question=question,
        source_matrix=source_matrix,
        shards=shards,
        confidence=confidence,
        average_authority=average_authority,
        timeout_seconds=max(
            int(_RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS / 1000),
            int(config.get("architectAgentTimeoutSeconds") or (_RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS / 1000)),
        ),
    )
    return _merge_web_research_architect_agent_pack(base_pack, agent_pack, question=question)


def _deadline_failure(
    *,
    tool_name: str,
    family: str,
    deadline_ms: int,
    summary: str,
    failure_class: str,
    error: str,
    recommended_next_action: str,
) -> dict[str, Any]:
    with ToolExecutionEnvelope(tool_name=tool_name, family=family, deadline_ms=deadline_ms, retry_limit=1) as envelope:
        return envelope.failure_payload(
            summary=summary,
            failure_class=failure_class,
            error=error,
            retryable=False,
            recommended_next_action=recommended_next_action,
        )


def _call_with_deadline(
    func,
    *,
    deadline_ms: int,
    tool_name: str,
    family: str,
    recommended_next_action: str,
) -> dict[str, Any]:
    with ToolExecutionEnvelope(tool_name=tool_name, family=family, deadline_ms=deadline_ms, retry_limit=1) as envelope:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"v8-{tool_name}")
        future = executor.submit(func)
        try:
            return _parse_tool_json(future.result(timeout=max(deadline_ms / 1000.0, 0.1)))
        except concurrent.futures.TimeoutError:
            future.cancel()
            return envelope.failure_payload(
                summary=f"{tool_name} exceeded its deadline.",
                failure_class="deadline_exceeded",
                error=f"{tool_name} exceeded {deadline_ms}ms deadline",
                retryable=False,
                recommended_next_action=recommended_next_action,
            )
        except Exception as exc:
            failure_class = classify_failure(exc)
            return envelope.failure_payload(
                summary=f"{tool_name} failed.",
                failure_class=failure_class,
                error=str(exc),
                retryable=False if failure_class in {"network_timeout", "provider_error", "auth_failed", "blocked_by_safety", "policy_reject", "unsupported_operation"} else True,
                recommended_next_action=recommended_next_action,
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _source_router_search(**kwargs: Any) -> str:
    # Keep legacy tests monkeypatchable with a SimpleNamespace(func=...), while
    # production routes through the Source Router rather than the raw web_search
    # primitive.
    if web_search is not None and getattr(web_search, "func", None) and not getattr(web_search, "name", None):
        return web_search.func(**kwargs)
    return source_router_search(**kwargs)


def _run_search_shard(
    shard: dict[str, Any],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    source_policy: str,
    max_rounds: int,
    use_agent_browser_profile: bool,
    tool_call_id: str,
) -> dict[str, Any]:
    query = _safe_text(shard.get("query"))
    video_research = _is_video_research(query, source_policy, shard.get("kind"))
    search_payload = _call_with_deadline(
        lambda: _source_router_search(
            query=query,
            limit=5,
            search_engine="auto",
            mode="auto",
            referer_mode="none",
            referer_url="",
            useAgentBrowserProfile=bool(use_agent_browser_profile),
            tool_call_id=tool_call_id,
        ),
        deadline_ms=min(_RESEARCH_SHARD_DEADLINE_MS, 45_000),
        tool_name="source_router_search",
        family="research",
        recommended_next_action="换关键词、限定权威域名，或保留该 shard 为 failed_source。",
    )
    if search_payload.get("kind") == "tool_deadline_envelope":
        return {
            **shard,
            "ok": False,
            "provider": None,
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": [_safe_text(search_payload.get("error")) or "search_deadline_exceeded"],
            "toolExecution": search_payload.get("toolExecution"),
        }
    raw_results = search_payload.get("results") if isinstance(search_payload.get("results"), list) else []
    results: list[dict[str, Any]] = []
    for index, result in enumerate(raw_results, start=1):
        url = _safe_text((result or {}).get("url"))
        host = _host(url)
        if blocked_domains and any(host == domain or host.endswith(f".{domain}") for domain in blocked_domains):
            continue
        if allowed_domains and not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
            continue
        title = _safe_text((result or {}).get("title"))[:300]
        snippet = _safe_text((result or {}).get("snippet"))[:600]
        quality = _source_quality(
            url,
            allowed_domains=allowed_domains,
            source_policy=source_policy,
            title=title,
            snippet=snippet,
            video_research=video_research,
        )
        results.append(
            {
                "resultRank": index,
                "title": title,
                "url": url,
                "finalUrl": url,
                "snippet": snippet,
                "sourceQualityHints": quality,
            }
        )
    top_results = sorted(results, key=lambda item: int((item.get("sourceQualityHints") or {}).get("authorityScore") or 0), reverse=True)
    fetched: list[dict[str, Any]] = []
    if max_rounds > 1:
        for result in top_results[:2]:
            url = _safe_text(result.get("url"))
            if not url:
                continue
            read_payload = _call_with_deadline(
                lambda: web_read.func(
                    url=url,
                    mode="auto",
                    headless=True,
                    referer_mode="none",
                    referer_url="",
                    useAgentBrowserProfile=bool(use_agent_browser_profile),
                    tool_call_id=tool_call_id,
                ),
                deadline_ms=_RESEARCH_SOURCE_READ_DEADLINE_MS,
                tool_name="web_read",
                family="research",
                recommended_next_action="标记该 source unavailable，换可访问来源或降低置信度。",
            )
            text = _safe_text(read_payload.get("text") or read_payload.get("markdown") or read_payload.get("textPreview"))
            extraction_quality = read_payload.get("extractionQuality")
            if not extraction_quality:
                extraction_quality = "readable" if read_payload.get("ok") and text else "unreadable"
            fetched.append(
                {
                    "url": url,
                    "ok": bool(read_payload.get("ok")),
                    "title": _safe_text(read_payload.get("title"))[:300],
                    "status": read_payload.get("status"),
                    "text": text[:6000],
                    "textPreview": text[:1200],
                    "contentChars": len(text),
                    "omittedChars": max(0, len(text) - 6000),
                    "extractionQuality": extraction_quality,
                    "sourceCapability": read_payload.get("sourceCapability"),
                    "providerAttemptMatrix": read_payload.get("providerAttemptMatrix") or read_payload.get("attemptedProviders"),
                    "rawRef": read_payload.get("rawRef") or read_payload.get("detailRawRef"),
                    "missingContentReason": read_payload.get("missingContentReason"),
                    "warnings": read_payload.get("warnings") if isinstance(read_payload.get("warnings"), list) else [],
                    "failureClass": read_payload.get("failureClass") or (read_payload.get("toolExecution") or {}).get("failureClass"),
                    "toolExecution": read_payload.get("toolExecution"),
                }
            )
    return {
        **shard,
        "ok": bool(search_payload.get("ok")),
        "provider": search_payload.get("provider"),
        "networkRoute": search_payload.get("networkRoute"),
        "sourceCapability": search_payload.get("sourceCapability"),
        "sourceRouter": search_payload.get("sourceRouter"),
        "providerAttemptMatrix": search_payload.get("providerAttemptMatrix") or search_payload.get("attemptedProviders"),
        "resultCount": len(results),
        "results": top_results,
        "fetchedTopSources": fetched,
        "errors": [] if search_payload.get("ok") else [_safe_text(search_payload.get("error")) or "search_failed"],
    }


def _run_search_shards(
    shards: list[dict[str, Any]],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    source_policy: str,
    max_rounds: int,
    use_agent_browser_profile: bool,
    tool_call_id: str,
) -> list[dict[str, Any]]:
    if not shards:
        return []
    completed: list[dict[str, Any] | None] = [None] * len(shards)
    with ThreadPoolExecutor(max_workers=max(1, len(shards))) as executor:
        futures = {
            executor.submit(
                _run_search_shard,
                shard,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
                source_policy=source_policy,
                max_rounds=max_rounds,
                use_agent_browser_profile=bool(use_agent_browser_profile),
                tool_call_id=tool_call_id,
            ): index
            for index, shard in enumerate(shards)
        }
        try:
            for future in as_completed(futures, timeout=max(_RESEARCH_TOOL_DEADLINE_MS / 1000.0, 1.0)):
                index = futures[future]
                shard = shards[index]
                try:
                    completed[index] = future.result()
                except Exception as exc:
                    completed[index] = {
                        **shard,
                        "ok": False,
                        "provider": None,
                        "resultCount": 0,
                        "results": [],
                        "fetchedTopSources": [],
                        "errors": [str(exc) or "research_shard_failed"],
                    }
        except TimeoutError:
            for future, index in futures.items():
                if future.done():
                    continue
                future.cancel()
                shard = shards[index]
                completed[index] = {
                    **shard,
                    "ok": False,
                    "provider": None,
                    "resultCount": 0,
                    "results": [],
                    "fetchedTopSources": [],
                    "errors": ["research_shard_deadline_exceeded"],
                    "toolExecution": _deadline_failure(
                        tool_name="research_broker",
                        family="research",
                        deadline_ms=_RESEARCH_TOOL_DEADLINE_MS,
                        summary="Research shard exceeded total tool deadline.",
                        failure_class="deadline_exceeded",
                        error="research_broker total shard execution deadline exceeded",
                        recommended_next_action="Use partial evidence, narrow the query, or run another focused research pass.",
                    ).get("toolExecution"),
                }
    return [item for item in completed if item is not None]


def _read_source_count(shards: list[dict[str, Any]]) -> int:
    count = 0
    for shard in shards:
        for item in list(shard.get("fetchedTopSources") or []):
            if isinstance(item, dict) and item.get("ok") and _safe_text(item.get("text") or item.get("textPreview")):
                count += 1
    return count


def _build_refinement_shards(question: str, shards: list[dict[str, Any]], *, source_policy: str, limit: int = 2) -> list[dict[str, Any]]:
    seen = {_safe_text(shard.get("query")).lower() for shard in shards if _safe_text(shard.get("query"))}
    candidates = [
        (f"{question} official documentation primary source", "gap_primary_source", "补读官方/一手来源"),
        (f"{question} limitations conflict comparison", "gap_conflict_check", "补查限制、冲突和反例"),
        (f"{question} latest updates source", "gap_freshness_check", "补查新鲜度和最新变化"),
    ]
    refined: list[dict[str, Any]] = []
    for query, kind, reason in candidates:
        normalized = query.lower()
        if normalized in seen:
            continue
        if source_policy in {"official", "primary", "authoritative"} and "official" not in normalized:
            continue
        refined.append({"shardId": f"shard_refine_{len(refined)+1}", "kind": kind, "query": query, "reason": reason})
        if len(refined) >= max(1, limit):
            break
    return refined


def _run_research_loop(
    *,
    question: str,
    initial_shards: list[dict[str, Any]],
    allowed_domains: list[str],
    blocked_domains: list[str],
    source_policy: str,
    max_rounds: int,
    use_agent_browser_profile: bool,
    tool_call_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    loop_state: dict[str, Any] = {
        "phase": "research_loop",
        "questions": [question],
        "rounds": [],
        "readSources": [],
        "uncoveredClaims": [],
        "conflictClaims": [],
        "nextQueries": [],
        "stopReason": "",
    }
    all_shards = _run_search_shards(
        initial_shards,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        source_policy=source_policy,
        max_rounds=max_rounds,
        use_agent_browser_profile=bool(use_agent_browser_profile),
        tool_call_id=tool_call_id,
    )
    loop_state["rounds"].append(
        {
            "round": 1,
            "queries": [shard.get("query") for shard in initial_shards],
            "resultCount": sum(int(shard.get("resultCount") or 0) for shard in all_shards),
            "readSourceCount": _read_source_count(all_shards),
        }
    )
    read_urls = _source_urls_from_matrix(
        [
            {"url": item.get("url")}
            for shard in all_shards
            for item in list(shard.get("fetchedTopSources") or [])
            if isinstance(item, dict) and item.get("ok")
        ]
    )
    loop_state["readSources"] = read_urls
    if _read_source_count(all_shards) >= 2 or max_rounds <= 1:
        loop_state["stopReason"] = "sufficient_evidence" if _read_source_count(all_shards) else "max_rounds_reached_without_readable_sources"
        return all_shards, loop_state

    refined_shards = _build_refinement_shards(question, all_shards, source_policy=source_policy)
    if not refined_shards:
        loop_state["stopReason"] = "no_refinement_queries_available"
        loop_state["uncoveredClaims"].append("Insufficient readable full-text sources.")
        return all_shards, loop_state
    loop_state["nextQueries"] = [shard.get("query") for shard in refined_shards]
    second_round = _run_search_shards(
        refined_shards,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        source_policy=source_policy,
        max_rounds=max_rounds,
        use_agent_browser_profile=bool(use_agent_browser_profile),
        tool_call_id=tool_call_id,
    )
    all_shards.extend(second_round)
    loop_state["rounds"].append(
        {
            "round": 2,
            "queries": [shard.get("query") for shard in refined_shards],
            "resultCount": sum(int(shard.get("resultCount") or 0) for shard in second_round),
            "readSourceCount": _read_source_count(second_round),
        }
    )
    loop_state["readSources"] = _source_urls_from_matrix(
        [
            {"url": item.get("url")}
            for shard in all_shards
            for item in list(shard.get("fetchedTopSources") or [])
            if isinstance(item, dict) and item.get("ok")
        ]
    )
    loop_state["stopReason"] = "sufficient_evidence_after_refinement" if _read_source_count(all_shards) >= 2 else "max_rounds_reached_with_gaps"
    if _read_source_count(all_shards) < 2:
        loop_state["uncoveredClaims"].append("Need at least two readable independent sources for stable reuse.")
    return all_shards, loop_state


def _synthesize_bundle(
    *,
    question: str,
    research_intent: str,
    source_policy: str,
    freshness: str,
    shards: list[dict[str, Any]],
    deliverable: str,
    research_loop_state: dict[str, Any] | None = None,
    experience_reuse: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_matrix: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for shard in shards:
        for result in list(shard.get("results") or []):
            url = _safe_text(result.get("url"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            quality = dict(result.get("sourceQualityHints") or {})
            source_matrix.append(
                {
                    "title": result.get("title"),
                    "url": url,
                    "host": quality.get("host") or _host(url),
                    "authorityScore": quality.get("authorityScore"),
                    "tier": quality.get("tier"),
                    "authorityTier": quality.get("authorityTier"),
                    "catalogSourceId": quality.get("catalogSourceId"),
                    "catalogCategory": quality.get("catalogCategory"),
                    "popularitySignals": quality.get("popularitySignals") or [],
                    "matchedSignals": quality.get("reasons") or [],
                    "snippet": _safe_text(result.get("snippet"))[:240],
                    "provider": shard.get("provider"),
                    "networkRoute": shard.get("networkRoute"),
                    "sourceRouterLocale": ((shard.get("sourceRouter") or {}).get("locale") if isinstance(shard.get("sourceRouter"), dict) else None),
                }
            )
            citations.append({"title": result.get("title"), "url": url})
    source_matrix.sort(key=lambda item: int(item.get("authorityScore") or 0), reverse=True)
    authority_scores = [int(item.get("authorityScore") or 0) for item in source_matrix[:5]]
    average_authority = round(sum(authority_scores) / len(authority_scores), 1) if authority_scores else 0
    confidence = "high" if average_authority >= 75 and len(source_matrix) >= 2 else ("medium" if source_matrix else "low")
    if not source_matrix:
        conflicts.append({"kind": "no_sources", "summary": "No usable source result was collected."})
    visible_shards = shards[:12]
    provider_attempt_matrix = _flatten_provider_attempts(shards)
    architect_pack = _web_research_architect_pack(
        question=question,
        source_matrix=source_matrix,
        shards=shards,
        confidence=confidence,
        average_authority=average_authority,
    )
    claim_table = list(architect_pack.get("claimTable") or [])
    conflict_matrix = list(architect_pack.get("conflictMatrix") or [])
    if conflicts:
        conflict_matrix.extend(conflicts)
    source_urls = [item.get("url") for item in list(architect_pack.get("sourceUrls") or []) if isinstance(item, dict) and item.get("url")]
    return {
        "ok": bool(source_matrix),
        "kind": "research_evidence_bundle",
        "summary": architect_pack.get("headline") or f"Collected {len(source_matrix)} ranked source(s) across {len(shards)} research shard(s).",
        "question": question,
        "topicFingerprint": _topic_fingerprint(question),
        "researchIntent": research_intent,
        "sourcePolicy": source_policy,
        "freshness": freshness,
        "deliverable": deliverable,
        "answer": architect_pack.get("answer") or "No source-backed research result was synthesized.",
        "resultPreview": architect_pack.get("answer") or architect_pack.get("headline"),
        "researchResult": architect_pack,
        "finalExperiencePack": architect_pack,
        "claimTable": claim_table,
        "conflictMatrix": conflict_matrix,
        "missingEvidence": list(architect_pack.get("missingEvidence") or []),
        "assumptions": list(architect_pack.get("assumptions") or []),
        "sourceUrls": source_urls,
        "providerAttemptMatrix": provider_attempt_matrix,
        "researchLoopState": research_loop_state
        or {
            "phase": "single_pass",
            "rounds": [],
            "stopReason": "single_pass_synthesis",
            "questions": [question],
            "readSources": source_urls,
            "uncoveredClaims": [],
            "conflictClaims": [],
            "nextQueries": [],
        },
        "experienceReuse": experience_reuse
        or {
            "reuseDecision": "ignore",
            "reason": "not_checked",
            "candidatePackId": None,
            "topicFingerprint": _topic_fingerprint(question),
        },
        "refinedBy": {
            "agentId": "web-research-architect",
            "agentName": "Web Research Architect",
            "role": "research_runtime_synthesis",
        },
        "confidence": confidence,
        "authorityScore": average_authority,
        "sourceMatrix": source_matrix[:8],
        "conflicts": conflict_matrix,
        "citations": citations[:8],
        "shards": [
            {
                "shardId": shard.get("shardId"),
                "kind": shard.get("kind"),
                "query": shard.get("query"),
                "ok": shard.get("ok"),
                "resultCount": shard.get("resultCount"),
                "provider": shard.get("provider"),
                "networkRoute": shard.get("networkRoute"),
                "sourceCapability": shard.get("sourceCapability"),
                "fetchedTopSources": shard.get("fetchedTopSources") or [],
                "errors": shard.get("errors") or [],
            }
            for shard in visible_shards
        ],
        "rawRefs": [{"kind": "web_search_query", "query": shard.get("query"), "shardId": shard.get("shardId")} for shard in visible_shards],
        "omitted": {
            "rawHtml": "stored in web fetch cache when available; not returned to agent-visible output",
            "fullSearchPages": "omitted",
            "shardsOmitted": max(0, len(shards) - len(visible_shards)),
            "rawRefsOmitted": max(0, len(shards) - len(visible_shards)),
            "sourceCatalogRef": "research_source_quality_catalog:v1",
            "popularityMetrics": "best-effort extraction from search title/snippet; verify on-page metrics for final claims",
            "providerAttemptsOmitted": max(0, len(provider_attempt_matrix) - 40),
        },
        "recommendedNextAction": "use_evidence_bundle" if source_matrix else "revise_queries",
    }


@tool
def research_broker(
    mode: str = "plan",
    question: str = "",
    query: str = "",
    researchIntent: str = "",
    freshness: str = "auto",
    sourcePolicy: str = "authoritative",
    seedUrls: list[str] | None = None,
    allowedDomains: list[str] | None = None,
    blockedDomains: list[str] | None = None,
    maxShards: int | None = None,
    maxRounds: int | None = None,
    deliverable: str = "evidence_bundle",
    evidenceBundleId: str = "",
    experiencePackId: str = "",
    title: str = "",
    tags: list[str] | str | None = None,
    minConfidence: str = "",
    limit: int = 20,
    includeArchived: bool = False,
    confirm: bool = False,
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Plan and run read-only web research as isolated shards with persistent evidence.

    Use this instead of ad-hoc web_search for multi-source facts, current provider/API details, source confidence,
    or research that benefits from parallel query decomposition. Search experience packs first for repeat topics;
    run new research only when prior packs are missing, stale, low confidence, or conflict with the current need.

    useAgentBrowserProfile defaults to false. Set it to true only for an explicit login-backed research need;
    the target source domains must match systemBase.webFetch.agentBrowserProfileAllowlist.
    """
    config = _research_config()
    normalized_mode = _safe_text(mode).lower() or "plan"
    supported_modes = {
        "plan",
        "run",
        "observe",
        "get_evidence",
        "search_experience",
        "get_experience",
        "promote_experience",
        "archive_experience",
        "restore_experience",
        "delete_experience",
    }
    if normalized_mode not in supported_modes:
        return _render_payload(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": f"Unsupported research_broker mode: {normalized_mode}",
                "recommendedNextAction": "use plan, search_experience, get_experience, run, observe, get_evidence, promote_experience, archive_experience, restore_experience, or delete_experience",
            }
        )
    if not config["enabled"] and normalized_mode in {"plan", "run"}:
        return _render_payload(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": "Research Runtime is disabled by supervisor.research.enabled=false.",
                "recommendedNextAction": "enable_research_runtime",
            }
        )

    scope = _ledger_scope(state)
    if normalized_mode == "observe":
        items = list_evidence_bundles(scope=scope, limit=limit)
        summary = research_ledger_summary(scope=scope, include_archived=includeArchived)
        return _render_payload(
            {
                "ok": True,
                "mode": normalized_mode,
                "summary": f"{len(items)} research evidence bundle(s) are available in persistent scope {scope}.",
                "counts": summary.get("counts") or {},
                "items": [
                    {
                        "evidenceBundleId": item.get("evidenceBundleId"),
                        "question": item.get("question"),
                        "confidence": item.get("confidence"),
                        "authorityScore": item.get("authorityScore"),
                        "createdAt": item.get("createdAt"),
                    }
                    for item in items
                ],
                "detailTool": "research_broker(mode='get_evidence', evidenceBundleId=...)",
                "recommendedNextAction": "get_evidence" if items else "search_experience_then_run",
            }
        )

    if normalized_mode == "get_evidence":
        bundle = get_evidence_bundle(_safe_text(evidenceBundleId))
        return _render_payload(
            {
                "ok": bool(bundle),
                "mode": normalized_mode,
                "summary": "Evidence bundle found in persistent research ledger." if bundle else "Evidence bundle not found or expired.",
                **({"item": bundle} if bundle else {}),
                "detailTool": "research_broker(mode='get_experience', experiencePackId=...)",
                "recommendedNextAction": "use_evidence_bundle" if bundle else "search_experience_then_run",
            }
        )

    if normalized_mode == "search_experience":
        clean_query = _safe_text(query) or _safe_text(question)
        if not clean_query:
            return _render_payload(
                {
                    "ok": False,
                    "mode": normalized_mode,
                    "summary": "search_experience requires query or question.",
                    "recommendedNextAction": "provide_query",
                }
            )
        packs = search_experience_packs_with_options(
            query=clean_query,
            scope=scope,
            tags=_as_list(tags),
            min_confidence=minConfidence,
            limit=limit,
            include_archived=includeArchived,
        )
        reuse_decision = _experience_reuse_decision(
            packs,
            question=clean_query,
            source_policy=sourcePolicy,
            freshness=freshness,
            min_confidence=minConfidence,
        )
        return _render_payload(
            {
                "ok": True,
                "mode": normalized_mode,
                "kind": "research_experience_search",
                "summary": f"Found {len(packs)} reusable research experience pack(s) for scope {scope}.",
                "query": clean_query,
                "reuseDecision": reuse_decision,
                "items": [
                    {
                        "experiencePackId": item.get("experiencePackId"),
                        "title": item.get("title"),
                        "status": item.get("status"),
                        "confidence": item.get("confidence"),
                        "authorityScore": item.get("authorityScore"),
                        "topicFingerprint": item.get("topicFingerprint"),
                        "sourcePolicy": item.get("sourcePolicy"),
                        "freshnessWindow": item.get("freshnessWindow"),
                        "qualityStatus": item.get("qualityStatus"),
                        "invalidationReason": item.get("invalidationReason"),
                        "missingEvidence": list(item.get("missingEvidence") or [])[:3],
                        "sourceUrls": list(item.get("sourceUrls") or [])[:4],
                        "usageCount": item.get("usageCount"),
                        "lastUsedAt": item.get("lastUsedAt"),
                        "tags": item.get("tags") or [],
                        "sourceMatrixDigest": list(item.get("sourceMatrixDigest") or [])[:4],
                    }
                    for item in packs
                ],
                "detailTool": "research_broker(mode='get_experience', experiencePackId=...)",
                "omitted": {"fullExperiencePack": "use get_experience for the selected pack"},
                "recommendedNextAction": "get_experience" if packs else "run",
            }
        )

    if normalized_mode == "get_experience":
        pack = get_experience_pack(_safe_text(experiencePackId), include_archived=includeArchived)
        return _render_payload(
            {
                "ok": bool(pack),
                "mode": normalized_mode,
                "kind": "research_experience_pack",
                "summary": "Experience pack found." if pack else "Experience pack not found.",
                **({"item": pack} if pack else {}),
                "detailTool": "research_broker(mode='get_evidence', evidenceBundleId=item.createdFromBundleId)",
                "recommendedNextAction": "reuse_experience" if pack else "search_experience_then_run",
            },
            max_chars=8000,
        )

    if normalized_mode == "archive_experience":
        pack = archive_experience_pack(_safe_text(experiencePackId), initiated_by="research_broker")
        return _render_payload(
            {
                "ok": bool(pack),
                "mode": normalized_mode,
                "kind": "research_experience_archive",
                "summary": "Experience pack archived." if pack else "Experience pack not found.",
                **({"item": pack} if pack else {}),
                "recommendedNextAction": "search_experience",
            },
            max_chars=8000,
        )

    if normalized_mode == "restore_experience":
        pack = restore_experience_pack(_safe_text(experiencePackId), initiated_by="research_broker")
        return _render_payload(
            {
                "ok": bool(pack),
                "mode": normalized_mode,
                "kind": "research_experience_restore",
                "summary": "Experience pack restored." if pack else "Experience pack not found.",
                **({"item": pack} if pack else {}),
                "recommendedNextAction": "get_experience" if pack else "search_experience",
            },
            max_chars=8000,
        )

    if normalized_mode == "delete_experience":
        deleted = delete_experience_pack(_safe_text(experiencePackId), confirm=confirm)
        return _render_payload(
            {
                "ok": bool(deleted),
                "mode": normalized_mode,
                "kind": "research_experience_delete",
                "summary": "Experience pack permanently deleted." if deleted else "Experience pack not deleted. Set confirm=true and provide an existing experiencePackId.",
                "experiencePackId": _safe_text(experiencePackId),
                "recommendedNextAction": "search_experience",
            },
            max_chars=4000,
        )

    if normalized_mode == "promote_experience":
        pack = promote_experience_pack(_safe_text(evidenceBundleId), title=title, tags=_as_list(tags))
        return _render_payload(
            {
                "ok": bool(pack),
                "mode": normalized_mode,
                "kind": "research_experience_promotion",
                "summary": "Evidence bundle promoted to reusable experience pack." if pack else "Evidence bundle not found; no experience pack promoted.",
                **({"item": pack} if pack else {}),
                "recommendedNextAction": "get_experience" if pack else "observe_or_run",
            },
            max_chars=8000,
        )

    clean_question = _safe_text(question)
    if not clean_question:
        return _render_payload(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": "research_broker requires question.",
                "recommendedNextAction": "provide_question",
            }
        )

    shard_cap = max(1, min(_as_int(maxShards, config["defaultShardCount"]), config["maxShardCount"]))
    round_cap = max(1, min(_as_int(maxRounds, config["maxRounds"]), config["maxRounds"]))
    seed_urls = _as_list(seedUrls)
    allowed_domains = _as_list(allowedDomains)
    blocked_domains = _as_list(blockedDomains)
    shards = _build_shards(
        question=clean_question,
        research_intent=researchIntent,
        source_policy=sourcePolicy,
        seed_urls=seed_urls,
        allowed_domains=allowed_domains,
        max_shards=shard_cap,
    )

    if normalized_mode == "plan":
        plan = {
            "ok": True,
            "mode": normalized_mode,
            "kind": "research_plan",
            "summary": f"Planned {len(shards)} read-only research shard(s).",
            "question": clean_question,
            "researchIntent": researchIntent,
            "freshness": freshness,
            "sourcePolicy": sourcePolicy,
            "sourceCatalogRef": "research_source_quality_catalog:v1",
            "experienceFirstPolicy": {
                "summary": "Before running new research, search reusable experience packs for repeat topics.",
                "searchTool": "research_broker(mode='search_experience', query=question)",
                "reuseWhen": ["confidence is medium/high", "scope and freshness still fit", "no material conflict"],
            },
            "shardDefaults": {
                "contextIsolation": "atomic_brief_only",
                "allowedTools": ["source_router_search", "web_read"],
                "sideEffects": "read_only",
                "deadlineMs": _RESEARCH_SHARD_DEADLINE_MS,
                "sourceReadDeadlineMs": _RESEARCH_SOURCE_READ_DEADLINE_MS,
                "useAgentBrowserProfile": bool(useAgentBrowserProfile),
            },
            "limits": {
                "defaultShardCount": config["defaultShardCount"],
                "requestedMaxShards": maxShards,
                "effectiveMaxShards": shard_cap,
                "hardMaxShardCount": config["maxShardCount"],
                "effectiveMaxRounds": round_cap,
                "toolDeadlineMs": _RESEARCH_TOOL_DEADLINE_MS,
            },
            "shards": shards,
            "recommendedNextAction": "search_experience_then_run",
        }
        return _render_payload(plan)

    experience_candidates = search_experience_packs_with_options(
        query=clean_question,
        scope=scope,
        tags=_as_list(tags),
        min_confidence=minConfidence,
        limit=3,
        include_archived=False,
    )
    experience_reuse = _experience_reuse_decision(
        experience_candidates,
        question=clean_question,
        source_policy=sourcePolicy,
        freshness=freshness,
        min_confidence=minConfidence,
    )
    if experience_reuse.get("reuseDecision") == "reuse" and experience_candidates:
        pack_id = _safe_text(experience_reuse.get("candidatePackId"))
        pack = get_experience_pack(pack_id) if pack_id else experience_candidates[0]
        if pack:
            return _render_payload(
                _bundle_from_reused_pack(
                    pack,
                    question=clean_question,
                    reuse=experience_reuse,
                    deliverable=deliverable,
                ),
                max_chars=12000,
            )

    completed_shards, research_loop_state = _run_research_loop(
        question=clean_question,
        initial_shards=shards,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        source_policy=sourcePolicy,
        max_rounds=round_cap,
        use_agent_browser_profile=bool(useAgentBrowserProfile),
        tool_call_id=tool_call_id,
    )
    bundle = _synthesize_bundle(
        question=clean_question,
        research_intent=researchIntent,
        source_policy=sourcePolicy,
        freshness=freshness,
        shards=completed_shards,
        deliverable=deliverable,
        research_loop_state=research_loop_state,
        experience_reuse=experience_reuse,
    )
    stored = _store_evidence(bundle, state=state)
    return _render_payload(_visible_bundle(stored))
