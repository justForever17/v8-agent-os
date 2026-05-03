from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState

from core.storage import storage
from core.tools.web_fetcher import web_read, web_search


_EVIDENCE_TTL_SECONDS = 6 * 60 * 60
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
    return stored


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
        "evidenceBundleId": payload.get("evidenceBundleId"),
        "confidence": payload.get("confidence"),
        "authorityScore": payload.get("authorityScore"),
        "omitted": {
            **omitted,
            "fallback": "agent_visible_budget_fallback",
            "detailTool": "research_broker(mode='get_evidence', evidenceBundleId=...)",
        },
        "recommendedNextAction": payload.get("recommendedNextAction") or "get_evidence",
    }
    return json.dumps(fallback, ensure_ascii=False, indent=2)


def _run_search_shard(
    shard: dict[str, Any],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    source_policy: str,
    max_rounds: int,
    tool_call_id: str,
) -> dict[str, Any]:
    query = _safe_text(shard.get("query"))
    video_research = _is_video_research(query, source_policy, shard.get("kind"))
    search_payload = _parse_tool_json(
        web_search.func(
            query=query,
            limit=5,
            search_engine="auto",
            mode="auto",
            referer_mode="none",
            referer_url="",
            tool_call_id=tool_call_id,
        )
    )
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
        for result in top_results[:1]:
            url = _safe_text(result.get("url"))
            if not url:
                continue
            read_payload = _parse_tool_json(
                web_read.func(
                    url=url,
                    mode="auto",
                    headless=True,
                    referer_mode="none",
                    referer_url="",
                    tool_call_id=tool_call_id,
                )
            )
            text = _safe_text(read_payload.get("text") or read_payload.get("textPreview"))
            fetched.append(
                {
                    "url": url,
                    "ok": bool(read_payload.get("ok")),
                    "title": _safe_text(read_payload.get("title"))[:300],
                    "status": read_payload.get("status"),
                    "textPreview": text[:360],
                    "omittedChars": max(0, len(text) - 360),
                    "warnings": read_payload.get("warnings") if isinstance(read_payload.get("warnings"), list) else [],
                }
            )
    return {
        **shard,
        "ok": bool(search_payload.get("ok")),
        "provider": search_payload.get("provider"),
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
                tool_call_id=tool_call_id,
            ): index
            for index, shard in enumerate(shards)
        }
        for future in as_completed(futures):
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
    return [item for item in completed if item is not None]


def _synthesize_bundle(
    *,
    question: str,
    research_intent: str,
    source_policy: str,
    freshness: str,
    shards: list[dict[str, Any]],
    deliverable: str,
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
    return {
        "ok": bool(source_matrix),
        "kind": "research_evidence_bundle",
        "summary": f"Collected {len(source_matrix)} ranked source(s) across {len(shards)} research shard(s).",
        "question": question,
        "researchIntent": research_intent,
        "sourcePolicy": source_policy,
        "freshness": freshness,
        "deliverable": deliverable,
        "answer": "Evidence collected. Use sourceMatrix and fetchedTopSources to write the final answer.",
        "confidence": confidence,
        "authorityScore": average_authority,
        "sourceMatrix": source_matrix[:8],
        "conflicts": conflicts,
        "citations": citations[:8],
        "shards": [
            {
                "shardId": shard.get("shardId"),
                "kind": shard.get("kind"),
                "query": shard.get("query"),
                "ok": shard.get("ok"),
                "resultCount": shard.get("resultCount"),
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
        },
        "recommendedNextAction": "use_evidence_bundle" if source_matrix else "revise_queries",
    }


@tool
def research_broker(
    mode: str = "plan",
    question: str = "",
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
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Plan and run read-only web research as isolated, run-scoped shards.

    Use this instead of ad-hoc web_search for multi-source facts, current provider/API details, source confidence,
    or research that benefits from parallel query decomposition. Shards are read-only, context-isolated, and return
    a compact evidence bundle for coding, creative media, writing, or supervisor decisions.
    """
    config = _research_config()
    normalized_mode = _safe_text(mode).lower() or "plan"
    if normalized_mode not in {"plan", "run", "observe", "get_evidence"}:
        return _render_payload(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": f"Unsupported research_broker mode: {normalized_mode}",
                "recommendedNextAction": "use plan, run, observe, or get_evidence",
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

    _cleanup_ledger()
    scope = _ledger_scope(state)
    if normalized_mode == "observe":
        items = [
            _visible_bundle(entry)
            for entry in _EVIDENCE_LEDGER.values()
            if str(entry.get("scope") or "") == scope or scope == "global"
        ]
        return _render_payload(
            {
                "ok": True,
                "mode": normalized_mode,
                "summary": f"{len(items)} research evidence bundle(s) are available in scope {scope}.",
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
                "recommendedNextAction": "get_evidence" if items else "run",
            }
        )

    if normalized_mode == "get_evidence":
        bundle = _EVIDENCE_LEDGER.get(_safe_text(evidenceBundleId))
        return _render_payload(
            {
                "ok": bool(bundle),
                "mode": normalized_mode,
                "summary": "Evidence bundle found." if bundle else "Evidence bundle not found or expired.",
                **({"item": _visible_bundle(bundle)} if bundle else {}),
                "recommendedNextAction": "use_evidence_bundle" if bundle else "run",
            }
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
            "shardDefaults": {
                "contextIsolation": "atomic_brief_only",
                "allowedTools": ["web_search", "web_read"],
                "sideEffects": "read_only",
            },
            "limits": {
                "defaultShardCount": config["defaultShardCount"],
                "requestedMaxShards": maxShards,
                "effectiveMaxShards": shard_cap,
                "hardMaxShardCount": config["maxShardCount"],
                "effectiveMaxRounds": round_cap,
            },
            "shards": shards,
            "recommendedNextAction": "run",
        }
        return _render_payload(plan)

    completed_shards = _run_search_shards(
        shards,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        source_policy=sourcePolicy,
        max_rounds=round_cap,
        tool_call_id=tool_call_id,
    )
    bundle = _synthesize_bundle(
        question=clean_question,
        research_intent=researchIntent,
        source_policy=sourcePolicy,
        freshness=freshness,
        shards=completed_shards,
        deliverable=deliverable,
    )
    stored = _store_evidence(bundle, state=state)
    return _render_payload(_visible_bundle(stored))
