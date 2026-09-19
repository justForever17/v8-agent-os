from __future__ import annotations

import hashlib
import asyncio
import json
import re
import threading
import time
import uuid
import concurrent.futures
import os
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator
from urllib.parse import urlparse

import requests
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState

from runtimes.research.access_scope import ResearchAccessScope, research_access_denied
from core.model_thinking_control import no_think_request_patch
from core.storage import storage
from core.system_base import get_web_fetch_config
from core.user_language import infer_preferred_language, normalize_preferred_language
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
from core.tools.research_acquisition_schedule import DEFAULT_RESEARCH_ACQUISITION_SCHEDULE
from core.tools.research_readability import has_readable_table
from core.tools.research_quality import (
    MIN_RESEARCH_ANSWER_CHARS,
    MIN_RESEARCH_CLAIM_COUNT,
    MIN_RESEARCH_DISTINCT_HOST_COUNT,
    MIN_RESEARCH_SOURCE_BODY_CHARS,
    MIN_RESEARCH_SOURCE_COUNT,
    TARGET_RESEARCH_ANSWER_CHARS,
    TARGET_RESEARCH_CLAIM_COUNT,
    TARGET_RESEARCH_DISTINCT_HOST_COUNT,
    TARGET_RESEARCH_SOURCE_COUNT,
    research_acceptance_metrics,
    research_acceptance_issues,
    research_answer_is_usable,
    research_high_quality_issues,
    research_quality_tier,
    research_review_decision,
    research_selected_sources,
)
from core.tools.research_source_identity import (
    source_attribution_role,
    research_document_identity,
    research_document_priority,
    research_source_is_navigation,
)
from core.tools.tool_execution_envelope import ToolExecutionEnvelope, classify_failure
from core.tools.research_source_catalog import match_source_catalog
from core.tools.web_fetcher import (
    MAX_RESEARCH_TEXT_CHARS,
    source_router_read,
    source_router_search,
    web_read,
    web_search,
)
from runtimes.extensions.mcp.client import mcp_manager


_EVIDENCE_TTL_SECONDS = 6 * 60 * 60
# Per parallel search batch. Architect synthesis has its own downstream
# budget and is intentionally not wrapped by this value.
_RESEARCH_TOOL_DEADLINE_MS = 45_000
_RESEARCH_SHARD_DEADLINE_MS = 30_000
_RESEARCH_SEARCH_DEADLINE_MS = 14_000
_RESEARCH_SOURCE_READ_DEADLINE_MS = 8_000
_RESEARCH_MAX_PARALLEL_SEARCH_SHARDS = (
    DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.max_parallel_shards
)
# Overall deadline for the Research Agent's acquisition, writing and review.
_RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS = 480_000
_MAX_RESEARCH_READ_ATTEMPTS = 2
_MAX_RESEARCH_HOST_RETRYABLE_FAILURES = 2
_RESEARCH_SOURCE_READ_CHARS = MAX_RESEARCH_TEXT_CHARS
_RESEARCH_SOURCE_CAPTURE_CHARS = 32_000


_RESEARCH_PROGRESS_REPORTER: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "research_progress_reporter",
    default=None,
)


@contextmanager
def bind_research_progress_reporter(
    reporter: Callable[[dict[str, Any]], None] | None,
) -> Iterator[None]:
    """Bind a managed-episode progress sink without changing the public tool schema."""

    token = _RESEARCH_PROGRESS_REPORTER.set(reporter)
    try:
        yield
    finally:
        _RESEARCH_PROGRESS_REPORTER.reset(token)


def _report_research_progress(**payload: Any) -> None:
    reporter = _RESEARCH_PROGRESS_REPORTER.get()
    if reporter is None:
        return
    compact = {
        str(key): value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    try:
        reporter(compact)
    except Exception:
        # Product progress must never alter Research evidence or retry truth.
        return
_RESEARCH_ARCHITECT_MAX_CLAIM_COUNT = 24
# Presentation limits; full read bodies and receipts remain in the ledger.
_RESEARCH_ARCHITECT_MAX_SOURCE_COUNT = 24
# Visible answer budget; saved answer pages provide the complete document.
_MAX_RESEARCH_VISIBLE_ANSWER_CHARS = 24_000
_EVIDENCE_LEDGER: dict[str, dict[str, Any]] = {}


class _ResearchReadAttemptLedger:
    """Coordinate URL reads across parallel shards and bounded repair rounds."""

    def __init__(
        self,
        *,
        question: str = "",
        terminal_identities: set[str] | None = None,
    ) -> None:
        self.question = _safe_text(question)
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}
        self._dedupe_skips = 0
        self._cached_projection_count = 0
        self._host_retryable_failures: dict[str, int] = {}
        self._host_circuit_skips = 0
        self._search_provider_records: dict[str, dict[str, int]] = {}
        self._search_provider_sequence = 0
        self._search_provider_circuit_skips = 0
        self._alternate_search_provider_attempts = 0
        for identity in set(terminal_identities or set()):
            normalized = _safe_text(identity)
            if normalized:
                self._records[normalized] = {
                    "attempts": 1,
                    "lastRound": 0,
                    "state": "terminal",
                    "ready": threading.Event(),
                }
                self._records[normalized]["ready"].set()

    def identity(self, url: Any) -> str:
        return _research_document_identity(
            _safe_text(url),
            question=self.question,
        )

    def _root(self, identity: str) -> str:
        root = identity
        visited: set[str] = set()
        while root in self._aliases and root not in visited:
            visited.add(root)
            root = self._aliases[root]
        return root

    def claim(self, url: Any, *, round_index: int) -> str:
        identity = self.identity(url)
        if not identity:
            return ""
        host = _host(_safe_text(url))
        bounded_round = max(1, int(round_index or 1))
        with self._lock:
            root = self._root(identity)
            record = self._records.get(root)
            if record is not None:
                state = _safe_text(record.get("state"))
                attempts = int(record.get("attempts") or 0)
                last_round = int(record.get("lastRound") or 0)
                if (
                    state in {"inflight", "terminal"}
                    or attempts >= _MAX_RESEARCH_READ_ATTEMPTS
                    or last_round >= bounded_round
                ):
                    self._dedupe_skips += 1
                    return ""
                if state != "retryable":
                    self._dedupe_skips += 1
                    return ""
            if (
                host
                and self._host_retryable_failures.get(host, 0)
                >= _MAX_RESEARCH_HOST_RETRYABLE_FAILURES
            ):
                self._host_circuit_skips += 1
                return ""
            else:
                if record is None:
                    record = {
                        "attempts": 0,
                        "lastRound": 0,
                        "state": "unseen",
                        "ready": threading.Event(),
                    }
                    self._records[root] = record
            record["attempts"] = int(record.get("attempts") or 0) + 1
            record["lastRound"] = bounded_round
            record["state"] = "inflight"
            record["host"] = host
            ready = record.get("ready")
            if isinstance(ready, threading.Event):
                ready.clear()
            self._aliases[identity] = root
            return root

    def has_record(self, url: Any) -> bool:
        identity = self.identity(url)
        if not identity:
            return False
        with self._lock:
            return self._root(identity) in self._records

    def claim_alternate_provider_attempt(self, *, limit: int) -> bool:
        """Reserve one bounded provider-switch attempt for the whole run."""

        bounded_limit = max(0, int(limit or 0))
        with self._lock:
            if self._alternate_search_provider_attempts >= bounded_limit:
                return False
            self._alternate_search_provider_attempts += 1
            return True

    def finish(
        self,
        claim_identity: str,
        *,
        final_url: Any = "",
        retryable: bool,
        succeeded: bool = False,
        record_host_failure: bool = True,
        cache_payload: dict[str, Any] | None = None,
    ) -> None:
        if not claim_identity:
            return
        with self._lock:
            root = self._root(claim_identity)
            record = self._records.get(root)
            if record is None:
                return
            record["state"] = (
                "retryable"
                if retryable
                and int(record.get("attempts") or 0) < _MAX_RESEARCH_READ_ATTEMPTS
                else "terminal"
            )
            if cache_payload:
                record["cachePayload"] = dict(cache_payload)
            host = _safe_text(record.get("host"))
            final_host = _host(_safe_text(final_url))
            if succeeded:
                if host:
                    self._host_retryable_failures.pop(host, None)
                if final_host:
                    self._host_retryable_failures.pop(final_host, None)
            elif retryable and host and record_host_failure:
                self._host_retryable_failures[host] = (
                    self._host_retryable_failures.get(host, 0) + 1
                )
            final_identity = self.identity(final_url)
            if final_identity:
                existing_root = self._root(final_identity)
                existing = self._records.get(existing_root)
                if existing is not None and existing_root != root:
                    existing["state"] = "terminal"
                    record["state"] = "terminal"
                self._aliases[final_identity] = root
            ready = record.get("ready")
            if isinstance(ready, threading.Event):
                ready.set()

    def host_circuit_open(self, url: Any, *, register_skip: bool = False) -> bool:
        host = _host(_safe_text(url))
        if not host:
            return False
        with self._lock:
            is_open = (
                self._host_retryable_failures.get(host, 0)
                >= _MAX_RESEARCH_HOST_RETRYABLE_FAILURES
            )
            if is_open and register_skip:
                self._host_circuit_skips += 1
            return is_open

    def cached_payload(
        self,
        url: Any,
        *,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any] | None:
        """Reuse one successful document read for another facet projection."""

        identity = self.identity(url)
        if not identity:
            return None
        with self._lock:
            root = self._root(identity)
            record = self._records.get(root)
            ready = record.get("ready") if record is not None else None
        if isinstance(ready, threading.Event) and wait_seconds > 0:
            ready.wait(timeout=max(0.0, float(wait_seconds)))
        with self._lock:
            root = self._root(identity)
            record = self._records.get(root)
            payload = record.get("cachePayload") if record is not None else None
            if not isinstance(payload, dict):
                return None
            self._cached_projection_count += 1
            return dict(payload)

    def mark_terminal(self, url_or_identity: Any, *, already_identity: bool = False) -> None:
        identity = (
            _safe_text(url_or_identity)
            if already_identity
            else self.identity(url_or_identity)
        )
        if not identity:
            return
        with self._lock:
            root = self._root(identity)
            record = self._records.setdefault(
                root,
                {
                    "attempts": 0,
                    "lastRound": 0,
                    "state": "terminal",
                    "ready": threading.Event(),
                },
            )
            record["state"] = "terminal"
            ready = record.get("ready")
            if isinstance(ready, threading.Event):
                ready.set()
            self._aliases[identity] = root

    def search_route_hints(self) -> dict[str, list[str]]:
        """Return per-run provider hints without mutating configured order."""

        with self._lock:
            preferred = [
                provider
                for provider, record in sorted(
                    self._search_provider_records.items(),
                    key=lambda item: int(item[1].get("lastSuccessSequence") or 0),
                    reverse=True,
                )
                if int(record.get("successes") or 0) > 0
            ]
            excluded = [
                provider
                for provider, record in self._search_provider_records.items()
                if (
                    int(record.get("successes") or 0) == 0
                    and (
                        int(record.get("terminalFailures") or 0) > 0
                        or int(record.get("retryableFailures") or 0) >= 1
                    )
                )
            ]
            return {
                "preferredProviders": preferred,
                "excludedProviders": excluded,
            }

    def record_search_payload(self, payload: dict[str, Any]) -> None:
        attempts = payload.get("providerAttemptMatrix") or payload.get("attemptedProviders") or []
        attempts = [item for item in list(attempts) if isinstance(item, dict)]
        selected_provider = _safe_text(payload.get("provider")).lower()
        if payload.get("ok") is True and selected_provider and not any(
            _safe_text(item.get("provider")).lower() == selected_provider
            and _safe_text(item.get("status")).lower() in {"ok", "success"}
            for item in attempts
        ):
            attempts.append({"provider": selected_provider, "status": "ok"})
        with self._lock:
            for attempt in attempts:
                provider = _safe_text(attempt.get("provider")).lower()
                if not provider:
                    continue
                record = self._search_provider_records.setdefault(
                    provider,
                    {
                        "attempts": 0,
                        "successes": 0,
                        "evidenceSuccesses": 0,
                        "evidenceFailures": 0,
                        "terminalFailures": 0,
                        "retryableFailures": 0,
                        "lastSuccessSequence": 0,
                    },
                )
                status = _safe_text(attempt.get("status")).lower()
                failure_class = _safe_text(
                    attempt.get("failureClass") or attempt.get("reason")
                ).lower()
                if failure_class == "provider_circuit_open":
                    self._search_provider_circuit_skips += 1
                    continue
                record["attempts"] += 1
                if status in {"ok", "success"}:
                    self._search_provider_sequence += 1
                    record["successes"] += 1
                    record["lastSuccessSequence"] = self._search_provider_sequence
                elif failure_class in _RESEARCH_PROVIDER_TERMINAL_FAILURES:
                    record["terminalFailures"] += 1
                elif failure_class in _RESEARCH_PROVIDER_BOUNDED_RETRY_FAILURES:
                    record["retryableFailures"] += 1

    def record_search_evidence_outcome(
        self,
        provider: Any,
        *,
        accepted_evidence_count: int,
    ) -> None:
        """Record query-level evidence yield separately from transport health.

        Unreadable result hosts or an irrelevant query do not make the search
        provider unavailable for every other facet. The current shard can
        still try its bounded alternate provider without poisoning run-wide
        routing. Only actual provider/configuration failures open that gate.
        """

        normalized_provider = _safe_text(provider).lower()
        if not normalized_provider:
            return
        with self._lock:
            record = self._search_provider_records.setdefault(
                normalized_provider,
                {
                    "attempts": 0,
                    "successes": 0,
                    "evidenceSuccesses": 0,
                    "evidenceFailures": 0,
                    "terminalFailures": 0,
                    "retryableFailures": 0,
                    "lastSuccessSequence": 0,
                },
            )
            if int(accepted_evidence_count or 0) > 0:
                record["evidenceSuccesses"] = int(
                    record.get("evidenceSuccesses") or 0
                ) + 1
            else:
                record["evidenceFailures"] = int(
                    record.get("evidenceFailures") or 0
                ) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            records = list(self._records.values())
            provider_records = {
                provider: dict(record)
                for provider, record in self._search_provider_records.items()
            }
            return {
                "uniqueIdentityCount": len(records),
                "networkAttemptCount": sum(int(item.get("attempts") or 0) for item in records),
                "retryableIdentityCount": sum(
                    _safe_text(item.get("state")) == "retryable" for item in records
                ),
                "terminalIdentityCount": sum(
                    _safe_text(item.get("state")) == "terminal" for item in records
                ),
                "deduplicatedReadCount": self._dedupe_skips,
                "cachedProjectionCount": self._cached_projection_count,
                "openHostCircuitCount": sum(
                    count >= _MAX_RESEARCH_HOST_RETRYABLE_FAILURES
                    for count in self._host_retryable_failures.values()
                ),
                "hostCircuitSkipCount": self._host_circuit_skips,
                "alternateSearchProviderAttemptCount": self._alternate_search_provider_attempts,
                "searchProviderCircuitSkipCount": self._search_provider_circuit_skips,
                "searchProviderStates": provider_records,
            }
_AUTHORITATIVE_HOST_HINTS = (
    "learn.microsoft.com",
    "cloud.google.com",
    "docs.aws.amazon.com",
)
_PUBLIC_INSTITUTION_HOST_SUFFIXES = (
    ".gov",
    ".gov.uk",
    ".gov.cn",
    ".gov.au",
    ".govt.nz",
    ".gouv.fr",
    ".go.jp",
    ".go.kr",
    ".gc.ca",
    ".admin.ch",
    ".edu",
    ".edu.cn",
    ".ac.uk",
    ".ac.jp",
    ".int",
)
_LOW_QUALITY_HOST_HINTS = (
    "pinterest.",
    "quora.",
    "reddit.",
    "medium.",
    "zhihu.",
    "juejin.",
    "csdn.",
    "cnblogs.",
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
_RELEVANCE_STOPWORDS = {
    "about",
    "analysis",
    "are",
    "best",
    "cite",
    "compare",
    "comparison",
    "current",
    "documentation",
    "evidence",
    "for",
    "how",
    "latest",
    "official",
    "practice",
    "practices",
    "primary",
    "python",
    "report",
    "research",
    "should",
    "source",
    "sources",
    "statement",
    "the",
    "tools",
    "update",
    "used",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
}
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)
_POPULARITY_RE = re.compile(
    r"(?P<number>\d+(?:[\.,]\d+)?)\s*(?P<unit>k|m|b|万|亿)?\s*(?P<label>views?|likes?|播放|观看|点赞|赞|收藏|shares?|comments?|评论|弹幕)",
    re.IGNORECASE,
)
_SOURCE_NOISE_MARKERS = (
    "about press copyright contact us creators",
    "privacy policy & safety how youtube works",
    "security check required",
    "we've detected unusual activity",
    "unusual activity from your network",
    "verify you are human",
    "just a moment",
    "cloudflare ray id",
    "performance & security by cloudflare",
    "checking your browser before accessing",
    "captcha",
    "access denied",
    "enable javascript",
    "please enable cookies",
    "navigation - index - modules",
    "theme auto light dark",
)
_JINA_READER_URL_PREFIX = "https://r.jina.ai/"


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


def _research_score_label(confidence: Any, authority_score: Any, *, reuse_decision: Any = "") -> str:
    confidence_text = _safe_text(confidence) or "unknown"
    try:
        authority = round(float(authority_score), 1)
    except (TypeError, ValueError):
        authority = None
    parts = [f"confidence={confidence_text}"]
    if authority is not None:
        parts.append(f"authority={authority}")
    if reuse_decision:
        parts.append(f"reuse={_safe_text(reuse_decision)}")
    return " / ".join(parts)


def _research_source_pack(source: dict[str, Any]) -> dict[str, Any]:
    url = _safe_text(source.get("url"))
    if not url:
        return {}
    payload = {
        "sourceId": _safe_text(source.get("sourceId")) or _source_id(url),
        "title": _safe_text(source.get("title") or source.get("sourceTitle") or source.get("host") or url)[:220],
        "url": url,
        "host": _safe_text(source.get("host")) or _host(url),
        "authorityScore": source.get("authorityScore"),
        "freshness": source.get("freshness") or source.get("freshnessWindow"),
        "relevance": source.get("relevanceScore") or source.get("score"),
        "tier": source.get("tier") or source.get("authorityTier"),
        "sourceRole": source_attribution_role(source),
        "sourceKind": source.get("sourceKind"),
        "acquisitionState": source.get("acquisitionState"),
        "originalContentChars": source.get("originalContentChars"),
        "omittedChars": source.get("omittedChars"),
        "provider": source.get("provider"),
        "citationKey": source.get("citationKey"),
        "selectedForEvidence": source.get("selectedForEvidence"),
        "retrievedAt": source.get("retrievedAt"),
        "publishedAt": source.get("publishedAt"),
        "updatedAt": source.get("updatedAt"),
        "sourceDate": source.get("sourceDate"),
        "sourceDateKind": source.get("sourceDateKind"),
        "version": source.get("version"),
        "temporalEvidence": source.get("temporalEvidence"),
        "subjectFocused": source.get("subjectFocused"),
        "researchFacetId": source.get("researchFacetId"),
        "researchFacetIds": list(source.get("researchFacetIds") or []),
        "researchFacetGoal": source.get("researchFacetGoal"),
        "evidenceQuery": source.get("evidenceQuery"),
        "contentChars": source.get("contentChars"),
        "readEvidence": source.get("readEvidence"),
        "qualityDimensions": (source.get("sourceQualityGate") or {}).get("qualityDimensions") or source.get("qualityDimensions"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _research_answer_from_pack(pack: dict[str, Any], payload: dict[str, Any]) -> str:
    for key in ("researchResult", "answer"):
        value = _safe_text(pack.get(key))
        if value:
            return value
    return ""


def _is_low_quality_research_answer(text: str) -> bool:
    normalized = _safe_text(text)
    lowered = normalized.lower()
    if not normalized:
        return False
    hard_noise_markers = (
        "about press copyright contact us creators",
        "privacy policy & safety how youtube works",
        "security check required",
        "we've detected unusual activity",
        "unusual activity from your network",
        "captcha",
        "access denied",
        "verify you are human",
        "cloudflare",
        "no reliable source-backed findings were collected",
        "could not synthesize a reliable result",
        "reused research experience pack, but no detailed research result",
        "no source-backed research result was synthesized",
    )
    if any(marker in lowered for marker in hard_noise_markers):
        return True
    if re.search(r"collected\s+\d+\s+ranked\s+source", lowered):
        return True
    process_markers = ("本次调研", "当前调研", "调研过程", "数据源均未产出", "未获取到有效内容", "无法获取目标文献")
    if (
        not re.search(r"\[S\d+\]", normalized)
        and any(marker in normalized for marker in process_markers)
        and any(
            marker in normalized
            for marker in ("未提供", "无法", "未产出", "安全验证", "浏览器检测", "建议重新调研")
        )
    ):
        return True
    return False


def _research_answer_pack(payload: dict[str, Any]) -> dict[str, Any]:
    pack = payload.get("finalExperiencePack") if isinstance(payload.get("finalExperiencePack"), dict) else {}
    if not pack and isinstance(payload.get("researchResult"), dict):
        pack = payload.get("researchResult") or {}
    answer = _research_answer_from_pack(pack, payload)
    agent_owned = (pack.get("independentReview") or payload.get("independentReview") or {}).get(
        "reviewContract"
    ) == "research-agent-review.v1"
    answer_was_low_quality = not agent_owned and _is_low_quality_research_answer(answer)
    low_quality_answer_note = _compact_research_text(answer, limit=360) if answer_was_low_quality else ""
    if answer_was_low_quality:
        answer = ""
    source_candidates = research_selected_sources(payload)
    evidence_bank = payload.get("researchEvidenceBank") if isinstance(payload.get("researchEvidenceBank"), dict) else {}
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in source_candidates:
        compact = _research_source_pack(source)
        url = _safe_text(compact.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(compact)
        if len(sources) >= _RESEARCH_ARCHITECT_MAX_SOURCE_COUNT:
            break
    limitations = []
    for value in list(pack.get("limitations") or []) + list(pack.get("missingEvidence") or payload.get("missingEvidence") or []) + list(pack.get("assumptions") or payload.get("assumptions") or []):
        text = _compact_research_text(value, limit=280)
        if text and text not in limitations:
            limitations.append(text)
        if len(limitations) >= 6:
            break
    if low_quality_answer_note and low_quality_answer_note not in limitations:
        limitations.insert(0, low_quality_answer_note)
    claim_table = []
    for item in list(
        pack.get("claimTable") or payload.get("claimTable") or evidence_bank.get("claims") or []
    )[:48 if agent_owned else _RESEARCH_ARCHITECT_MAX_CLAIM_COUNT]:
        if isinstance(item, dict):
            claim_table.append(
                {
                    key: value
                    for key, value in {
                        "claimId": item.get("claimId"),
                        "claim": item.get("claim") if agent_owned else _compact_research_text(item.get("claim"), limit=800),
                        "claimType": item.get("claimType") or item.get("claimKind"),
                        "normativeCue": _compact_research_text(item.get("normativeCue"), limit=160),
                        "sourceRole": item.get("sourceRole"),
                        "sourceClaim": _compact_research_text(item.get("sourceClaim"), limit=600),
                        "supportingSources": list(item.get("supportingSources") or [])[:4],
                        "confidence": item.get("confidence"),
                        "evidenceExcerptKey": item.get("evidenceExcerptKey"),
                        "evidenceExcerpt": item.get("evidenceExcerpt") if agent_owned else _compact_research_text(item.get("evidenceExcerpt"), limit=600),
                        "evidenceExcerptSha256": item.get("evidenceExcerptSha256"),
                        "evidenceVerified": item.get("evidenceVerified") is True,
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
    rejected_evidence = []
    for item in list(evidence_bank.get("rejectedSources") or payload.get("rejectedSources") or [])[:8]:
        if not isinstance(item, dict):
            continue
        rejected_evidence.append(
            {
                key: value
                for key, value in {
                    "sourceId": item.get("sourceId"),
                    "title": _compact_research_text(item.get("title"), limit=160),
                    "url": item.get("url"),
                    "reason": item.get("reason") or item.get("rejectedReason"),
                }.items()
                if value not in (None, "", [], {})
            }
        )
    reuse = payload.get("experienceReuse") if isinstance(payload.get("experienceReuse"), dict) else {}
    reuse_decision = _safe_text(reuse.get("reuseDecision"))
    confidence = pack.get("confidence") or payload.get("confidence")
    authority_score = pack.get("authorityScore") or payload.get("authorityScore")
    review_decision = research_review_decision(payload)
    validation_payload = {
        **payload,
        "reviewDecision": review_decision,
        "researchAnswerPack": {
            "reviewDecision": review_decision,
            "answer": answer,
            "sources": sources,
            "claimTable": claim_table,
            "asOf": pack.get("asOf") or payload.get("asOf"),
            "criticalMissingEvidence": pack.get("criticalMissingEvidence") or payload.get("criticalMissingEvidence") or [],
            "independentReview": pack.get("independentReview") or payload.get("independentReview") or {},
        },
    }
    minimum_issues = research_acceptance_issues(validation_payload)
    missing_reasons = research_high_quality_issues(validation_payload)
    metrics = research_acceptance_metrics(validation_payload)
    if answer_was_low_quality:
        missing_reasons.append("low_quality_answer_surface")
    quality_tier = research_quality_tier(validation_payload)
    # ``high_quality`` is the normal Research target, not the minimum useful
    # delivery contract.  Keeping a fully reviewed minimum-qualified answer
    # hidden made restricted-network runs indistinguishable from runs with no
    # readable evidence at all.
    delivery_ready = quality_tier in {"minimum_qualified", "high_quality"} and not answer_was_low_quality
    usable_answer = research_answer_is_usable(validation_payload)
    if not usable_answer:
        answer = ""
    evidence_id = _safe_text(payload.get("evidenceBundleId"))
    return {
        "kind": "research_answer_pack",
        "deliveryRequirements": payload.get("deliveryRequirements") or pack.get("deliveryRequirements") or {},
        "reviewDecision": review_decision,
        "reviewReasons": list(pack.get("reviewReasons") or payload.get("reviewReasons") or [])[:12],
        "answer": answer,
        "usableAnswer": usable_answer,
        "deliveryScope": (pack.get("independentReview") or {}).get("deliveryScope") or pack.get("deliveryScope"),
        "sources": sources,
        "claimTable": claim_table,
        "independentReview": pack.get("independentReview") or payload.get("independentReview") or {},
        "rejectedEvidence": rejected_evidence,
        "score": {
            "label": _research_score_label(confidence, authority_score, reuse_decision=reuse_decision),
            "confidence": confidence,
            "authorityScore": authority_score,
            "reuseDecision": reuse_decision or None,
            "qualityStatus": quality_tier,
            "qualityTier": quality_tier,
            "minimumQualified": not minimum_issues,
            "deliveryReady": delivery_ready,
            "acceptanceMetrics": metrics,
        },
        "limitations": limitations,
        "criticalMissingEvidence": list(pack.get("criticalMissingEvidence") or payload.get("criticalMissingEvidence") or [])[:12],
        "recommendedNextQueries": list(pack.get("recommendedNextQueries") or payload.get("recommendedNextQueries") or [])[:12],
        "asOf": pack.get("asOf") or payload.get("asOf"),
        "missingOrStaleReasons": list(dict.fromkeys(missing_reasons)),
        "reuseDecision": reuse or None,
        "detailRef": {
            "evidenceBundleId": evidence_id,
            "tool": f"research_broker(mode='get_evidence', evidenceBundleId='{evidence_id}')" if evidence_id else "research_broker(mode='get_evidence', evidenceBundleId=...)",
        },
        "recommendedNextAction": "use_research_answer_pack" if delivery_ready else "continue_research",
    }


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
    host_query_aliases = (
        payload.get("hostQueryAliases")
        if isinstance(payload.get("hostQueryAliases"), dict)
        else {}
    )
    host_query_rewrites = (
        payload.get("hostQueryRewrites")
        if isinstance(payload.get("hostQueryRewrites"), dict)
        else {}
    )
    return {
        "version": payload.get("version"),
        "entries": [entry for entry in entries if isinstance(entry, dict)],
        "hostQueryAliases": {
            _safe_text(host).lower(): _as_list(aliases)
            for host, aliases in host_query_aliases.items()
            if _safe_text(host)
        },
        "hostQueryRewrites": {
            _safe_text(host).lower(): _safe_text(replacement).lower()
            for host, replacement in host_query_rewrites.items()
            if _safe_text(host) and _safe_text(replacement)
        },
        "officialEntitySeeds": [
            seed
            for seed in list(payload.get("officialEntitySeeds") or [])
            if isinstance(seed, dict)
            and _safe_text(seed.get("url"))
            and _safe_text(seed.get("entityId"))
        ],
    }


def _catalog_match(url: str) -> dict[str, Any] | None:
    return match_source_catalog(url, _source_catalog().get("entries") or [])


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


def _catalog_official_host_hints(question: Any) -> list[dict[str, Any]]:
    lowered_question = _safe_text(question).lower()
    if not lowered_question:
        return []
    catalog = _source_catalog()
    alias_map = dict(catalog.get("hostQueryAliases") or {})
    official_hosts = list(
        dict.fromkeys(
            [
                *_catalog_hosts_by_category("official_docs"),
                *_catalog_hosts_by_category("official_blog"),
            ]
        )
    )
    hints: list[dict[str, Any]] = []
    for host in official_hosts:
        aliases = [
            _safe_text(alias).lower()
            for alias in list(alias_map.get(host) or [])
            if _safe_text(alias)
        ]
        if not aliases:
            aliases = [
                label
                for label in re.split(r"[^a-z0-9]+", host)
                if len(label) >= 4
                and label
                not in {"blog", "cloud", "developer", "developers", "docs", "help", "platform"}
            ]
        matched = [alias for alias in aliases if alias in lowered_question]
        if matched:
            hints.append({"host": host, "matchedAliases": matched})
    return hints



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



def _research_document_identity(value: str, *, question: str = "") -> str:
    return research_document_identity(value, question=question)


def _research_document_priority(value: str, *, question: str = "") -> int:
    return research_document_priority(value, question=question)


def _research_source_excerpt(text: Any, query: Any, *, limit: int = _RESEARCH_SOURCE_CAPTURE_CHARS) -> str:
    """Keep the page head plus query-relevant windows from anywhere in the body."""

    body = _safe_text(text)
    limit = max(MIN_RESEARCH_SOURCE_BODY_CHARS, int(limit or 0))
    if len(body) <= limit:
        return body

    raw_query = _safe_text(query).lower()
    terms: list[str] = []
    structured_anchor_patterns: list[re.Pattern[str]] = []
    for phrase in re.findall(r"[\"']([^\"']{3,80})[\"']", raw_query):
        normalized = phrase.strip()
        if normalized and normalized not in terms:
            terms.append(normalized)
    # Long statutes and standards repeat generic words thousands of times.
    # Preserve structured anchors before tokenising so an Article/Annex/date
    # query reaches the operative text instead of only the document preamble.
    for pattern in (
        r"\barticle\s+\d+[a-z]?(?:\s*\(\s*[a-z0-9]+\s*\)){0,3}",
        r"\bannex\s+(?:[ivxlcdm]+|\d+)\b",
        r"\b\d+(?:\.\d+)?\s*(?:\^|×\s*10\s*\^?)\s*\d+\b",
        r"\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+\d{4}\b",
    ):
        for match in re.finditer(pattern, raw_query, re.IGNORECASE):
            normalized = re.sub(r"\s+", " ", match.group(0)).strip()
            if normalized and normalized not in terms:
                terms.append(normalized)
            if normalized:
                flexible_anchor = "".join(
                    r"[\s\u00a0]+" if part.isspace() else re.escape(part)
                    for part in re.split(r"(\s+)", normalized)
                    if part
                )
                flexible_anchor = flexible_anchor.replace(
                    r"\(", r"[\s\u00a0]*\([\s\u00a0]*"
                ).replace(
                    r"\)", r"[\s\u00a0]*\)"
                ).replace(
                    r"\^", r"[\s\u00a0]*\^[\s\u00a0]*"
                )
                structured_anchor_patterns.append(
                    re.compile(
                        rf"(?<![a-z0-9]){flexible_anchor}(?![a-z0-9])",
                        re.IGNORECASE,
                    )
                )
    for term in re.split(r"[^a-z0-9_.-]+", raw_query):
        normalized = term.strip("._-")
        if len(normalized) >= 3 and normalized not in _RELEVANCE_STOPWORDS and not normalized.startswith("http"):
            if normalized not in terms:
                terms.append(normalized)
            visible_variant = re.sub(r"[_-]+", " ", normalized).strip()
            if visible_variant != normalized and visible_variant not in terms:
                terms.append(visible_variant)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", raw_query):
        if len(run) <= 8:
            candidates = [run]
        else:
            # Chinese pages often omit the query's surrounding grammar. For
            # example, ``每日饮水量`` may appear in the body only as ``饮水``.
            # Put two-character noun anchors ahead of the wider context windows
            # so they survive the bounded query-term budget.
            candidates = [run[index : index + 2] for index in range(len(run) - 1)]
            candidates.extend(
                run[index : index + 4]
                for index in range(0, len(run) - 3, 2)
            )
        for candidate in candidates:
            if candidate not in terms:
                terms.append(candidate)

    head_chars = min(3000, max(1600, limit // 5))
    window_radius = min(900, max(240, limit // 10))
    anchor_intervals: list[tuple[int, int]] = []
    for anchor_pattern in structured_anchor_patterns:
        for match in list(anchor_pattern.finditer(body))[:3]:
            anchor_intervals.append(
                (
                    max(0, match.start() - window_radius),
                    min(len(body), match.end() + window_radius),
                )
            )

    generic_intervals: list[tuple[int, int]] = []
    lowered_body = body.lower()
    for term in terms[:24]:
        start = 0
        matches = 0
        while matches < 3:
            index = lowered_body.find(term, start)
            if index < 0:
                break
            generic_intervals.append(
                (
                    max(0, index - window_radius),
                    min(len(body), index + len(term) + window_radius),
                )
            )
            start = index + max(1, len(term))
            matches += 1

    if not anchor_intervals and not generic_intervals:
        tail_chars = min(limit - head_chars, max(1200, limit // 4))
        generic_intervals.append(
            (max(head_chars, len(body) - tail_chars), len(body))
        )

    def merge_intervals(
        intervals: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        merged: list[tuple[int, int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1] + 120:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    separator_text = "\n\n[... query-focused excerpt ...]\n\n"
    selected_intervals: list[tuple[int, int]] = []
    # Query matches are evidence; the page head is only general context. Admit
    # structured and generic query windows first, then retain as much of the
    # head as the remaining budget allows. This prevents a long navigation or
    # introduction block from clipping the numeric clause immediately before
    # a late matching noun.
    for interval in [*anchor_intervals, *generic_intervals, (0, head_chars)]:
        candidate_intervals = merge_intervals([*selected_intervals, interval])
        candidate_chars = sum(end - start for start, end in candidate_intervals)
        candidate_chars += max(0, len(candidate_intervals) - 1) * len(separator_text)
        if candidate_chars <= limit:
            selected_intervals = candidate_intervals
    merged = merge_intervals(selected_intervals or [(0, min(len(body), limit))])

    chunks: list[str] = []
    used = 0
    for start, end in merged:
        if used >= limit:
            break
        separator = separator_text if chunks else ""
        available = limit - used - len(separator)
        if available <= 0:
            break
        chunk = body[start:end][:available]
        if chunk:
            chunks.append(f"{separator}{chunk}")
            used += len(separator) + len(chunk)
    return "".join(chunks)[:limit]


def _research_original_content_chars(text: Any, read_payload: dict[str, Any] | None = None) -> int:
    body = _safe_text(text)
    payload = read_payload or {}
    reported = _as_int(payload.get("originalContentChars"), 0)
    truncated_match = re.search(r"\.\.\.\[TRUNCATED\]\s*\((\d+)\s+chars total\)", body)
    if truncated_match:
        reported = max(reported, _as_int(truncated_match.group(1), 0))
    return max(len(body), reported)


def _domain_from_seed(value: str) -> str:
    host = _host(value)
    if host.startswith("www."):
        host = host[4:]
    return host


def _query_slug(value: str) -> str:
    normalized = re.sub(r"\s+", " ", _safe_text(value).lower())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]


def _query_site_domains(value: Any) -> list[str]:
    domains: list[str] = []
    for match in re.finditer(r"(?:^|\s)site:([a-z0-9.-]+)", _safe_text(value).lower()):
        domain = match.group(1).strip(".-")
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _host_matches_domains(host: str, domains: list[str]) -> bool:
    normalized = _safe_text(host).lower().removeprefix("www.")
    return any(normalized == domain or normalized.endswith(f".{domain}") for domain in domains)


def _first_party_domain_matches_question(url: Any, question: Any) -> bool:
    host = _host(_safe_text(url)).removeprefix("www.")
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return False
    compound_suffixes = {
        "ac.uk",
        "co.jp",
        "co.uk",
        "com.au",
        "com.br",
        "com.cn",
        "com.sg",
        "com.tw",
        "org.uk",
    }
    suffix = ".".join(parts[-2:])
    label_index = -3 if suffix in compound_suffixes and len(parts) >= 3 else -2
    registrable_label = parts[label_index]
    if not re.fullmatch(r"[a-z0-9]{4,}", registrable_label):
        return False
    question_without_routes = re.sub(
        r"(?<![a-z0-9])site:[^\s]+",
        " ",
        _URL_IN_TEXT_RE.sub(" ", _safe_text(question).lower()),
    )
    question_tokens = set(re.findall(r"[a-z0-9]{4,}", question_without_routes))
    return registrable_label in question_tokens


def _source_matches_intent(quality: dict[str, Any], source_intent: Any) -> bool:
    if _safe_text(source_intent).lower() != "official_primary":
        return True
    if _safe_text(quality.get("authorityTier")).lower() == "secondary":
        return False
    return bool(
        _safe_text(quality.get("authorityTier")).lower() == "primary"
        or int(quality.get("authorityScore") or 0) >= 70
    )


def _source_quality(
    url: str,
    *,
    allowed_domains: list[str],
    source_policy: str,
    title: str = "",
    snippet: str = "",
    video_research: bool = False,
    question: str = "",
) -> dict[str, Any]:
    normalized_host = _host(url)
    allowed_match = any(normalized_host == domain or normalized_host.endswith(f".{domain}") for domain in allowed_domains)
    catalog_entry = _catalog_match(url)
    public_institution = any(normalized_host.endswith(suffix) for suffix in _PUBLIC_INSTITUTION_HOST_SUFFIXES)
    first_party_subject_match = _first_party_domain_matches_question(url, question)
    authoritative_hint = (
        allowed_match
        or public_institution
        or first_party_subject_match
        or any(hint in normalized_host or hint in url.lower() for hint in _AUTHORITATIVE_HOST_HINTS)
    )
    low_quality_hint = any(hint in normalized_host or hint in url.lower() for hint in _LOW_QUALITY_HOST_HINTS)
    popularity = _popularity_signals(f"{title}\n{snippet}")
    # Unknown hosts must earn authority through an allowlist, catalog entry, or
    # an explicit host signal before authoritative-source policy can select them.
    score = 40
    reasons: list[str] = []
    if allowed_match:
        score += 30
        reasons.append("allowed_domain_match")
    if catalog_entry:
        boost = _as_int(catalog_entry.get("authorityBoost"), 0)
        # Catalog membership is an explicit trust signal; preserve the catalog's
        # established tier while keeping uncatalogued hosts below the default
        # authoritative-selection threshold.
        score += boost + 10
        reasons.append(f"source_catalog:{catalog_entry.get('id')}")
    if public_institution:
        score += 20
        reasons.append("official_public_institution_host")
    if first_party_subject_match:
        score += 15
        reasons.append("first_party_domain_subject_match")
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
    if not video_research and catalog_entry and str(catalog_entry.get("category") or "") in {"video_platform", "creative_showcase"}:
        score -= 35
        reasons.append("non_video_research_source_penalty")
    if low_quality_hint:
        score -= 25
        reasons.append("low_quality_host_hint")
    score = max(0, min(score, 100))
    return {
        "host": normalized_host,
        "authorityScore": score,
        "tier": (
            "secondary" if catalog_entry and catalog_entry.get("authorityTier") == "secondary"
            else "primary" if score >= 80 else "secondary" if score >= 55 else "weak"
        ),
        "reasons": reasons,
        "catalogSourceId": catalog_entry.get("id") if catalog_entry else None,
        "catalogCategory": catalog_entry.get("category") if catalog_entry else None,
        "authorityTier": catalog_entry.get("authorityTier") if catalog_entry else ("primary" if public_institution or first_party_subject_match else None),
        "popularitySignals": popularity,
    }


def _source_id(url: str) -> str:
    normalized = _safe_text(url).lower()
    return f"src_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]}" if normalized else f"src_{uuid.uuid4().hex[:12]}"



def _metadata_value(metadata: dict[str, Any], *keys: str) -> str:
    normalized = {str(key).strip().lower(): value for key, value in metadata.items()}
    for key in keys:
        value = _safe_text(normalized.get(key.lower()))
        if value:
            return value
    return ""


def _normalize_document_date(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{10}|\d{13}", text):
        try:
            timestamp = float(text) / (1000.0 if len(text) == 13 else 1.0)
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if 2000 <= parsed.year <= 2100:
                return parsed.date().isoformat()
        except (OverflowError, OSError, ValueError):
            pass
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
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return text


def _source_temporal_evidence(read_payload: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    result = result or {}
    metadata = dict(read_payload.get("metadata") or {}) if isinstance(read_payload.get("metadata"), dict) else {}
    published_at = _safe_text(
        read_payload.get("publishedAt")
        or result.get("publishedAt")
        or result.get("publishedDate")
        or _metadata_value(
            metadata,
            "article:published_time",
            "og:published_time",
            "datepublished",
            "date",
            "dc.date",
        )
    )
    updated_at = _safe_text(
        read_payload.get("updatedAt")
        or result.get("updatedAt")
        or result.get("updatedDate")
        or _metadata_value(
            metadata,
            "article:modified_time",
            "og:updated_time",
            "datemodified",
        )
    )
    version = _safe_text(
        read_payload.get("version")
        or result.get("version")
        or _metadata_value(metadata, "version", "softwareversion", "release", "releaseversion")
    )
    temporal_text = "\n".join(
        _safe_text(value)
        for value in (
            read_payload.get("title"),
            result.get("title"),
            result.get("url"),
            read_payload.get("text"),
            read_payload.get("markdown"),
        )
        if _safe_text(value)
    )[:4000]
    temporal_text = re.sub(r"[\s\u00a0]+", " ", temporal_text)
    version_identity_text = "\n".join(
        _safe_text(value)
        for value in (
            read_payload.get("title"),
            result.get("title"),
            result.get("url"),
        )
        if _safe_text(value)
    )[:1200]
    month_pattern = (
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    )
    date_value_pattern = (
        r"(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
        rf"(?:{month_pattern})\s+\d{{1,2}},?\s+20\d{{2}}|"
        rf"\d{{1,2}}[- ](?:{month_pattern})[- ,]20\d{{2}})"
    )
    if not updated_at:
        updated_match = re.search(
            rf"\b(?:last\s+update(?:d)?|updated|modified)\b\s*[:\-]?\s*({date_value_pattern})",
            temporal_text,
            re.IGNORECASE,
        )
        if updated_match:
            updated_at = updated_match.group(1)
    if not published_at:
        published_match = re.search(
            rf"\b(?:published|created|date)\b\s*[:\-]?\s*({date_value_pattern})",
            temporal_text,
            re.IGNORECASE,
        )
        if published_match:
            published_at = published_match.group(1)
    if not published_at:
        url_date_match = re.search(r"/(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:/|[-_])", _safe_text(result.get("url")))
        if url_date_match:
            published_at = "-".join(
                (
                    url_date_match.group(1),
                    url_date_match.group(2).zfill(2),
                    url_date_match.group(3).zfill(2),
                )
            )
    if not version:
        match = re.search(
            r"\b(?:python|version|release|v)\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b",
            version_identity_text,
            re.IGNORECASE,
        )
        if match:
            version = match.group(1)
    if not version:
        generic_version = re.search(
            r"\((\d+\.\d+(?:\.\d+|\.x)?)\)",
            version_identity_text,
            re.IGNORECASE,
        )
        if generic_version:
            version = generic_version.group(1)
    applicable_version = ""
    if not version:
        applicability_version = re.search(
            r"\b(?:targets?|supports?|applies\s+to|compatible\s+with|documentation\s+for)\s+"
            r"(?:python\s*)?([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b",
            temporal_text,
            re.IGNORECASE,
        )
        if applicability_version:
            applicable_version = applicability_version.group(1)
    published_at = _normalize_document_date(published_at)
    updated_at = _normalize_document_date(updated_at)
    retrieved_at = _safe_text(read_payload.get("retrievedAt") or read_payload.get("fetchedAt")) or _utc_now_iso()
    source_date = updated_at or published_at
    return {
        key: value
        for key, value in {
            "retrievedAt": retrieved_at,
            "publishedAt": published_at or None,
            "updatedAt": updated_at or None,
            "sourceDate": source_date or None,
            "sourceDateKind": "updated" if updated_at else ("published" if published_at else None),
            "version": version or None,
            "applicableVersion": applicable_version or None,
        }.items()
        if value not in (None, "")
    }



def _source_relevance_terms(question: str) -> list[str]:
    query = _safe_text(question).lower()
    raw_latin_terms = [term for term in re.split(r"[^a-z0-9]+", query) if len(term) >= 3]
    latin_terms = [term for term in raw_latin_terms if term not in _RELEVANCE_STOPWORDS]
    if not latin_terms:
        latin_terms = raw_latin_terms
    cjk_terms: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        if len(run) <= 4:
            cjk_terms.append(run)
        else:
            cjk_terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(latin_terms + cjk_terms))


def _source_relevance_score(question: str, *, title: str = "", snippet: str = "", text: str = "") -> int:
    # Link destinations repeat site/product names in every documentation
    # paragraph. They are transport metadata, not evidence that the visible
    # paragraph answers the question.
    visible_text = _URL_IN_TEXT_RE.sub(" ", text[:8000])
    haystack = " ".join([title, snippet, visible_text]).lower()
    query = _safe_text(question).lower()
    if not query or not haystack:
        return 0
    terms = _source_relevance_terms(question)
    if not terms:
        return 0
    def term_matches(term: str) -> bool:
        if term == "cli":
            return bool(
                re.search(r"\bcli\b", haystack)
                or any(
                    alias in haystack
                    for alias in (
                        "command-line",
                        "command line",
                        "console script",
                        "console_scripts",
                        "entry point",
                        "argparse",
                    )
                )
                or re.search(r"\b(?:click|typer)\b", haystack)
            )
        if term == "pathlib":
            return bool(
                re.search(r"\bpathlib\b", haystack)
                or "path object" in haystack
                or "filesystem path" in haystack
                or "file system path" in haystack
                or "path-like object" in haystack
                or "pathlike object" in haystack
                or "os.pathlike" in haystack
                or "__fspath__" in haystack
                or "path_type" in haystack
                or "path type" in haystack
                or re.search(r"\b(?:click|typer)[\s._-]*path\b", haystack)
            )
        if re.fullmatch(r"[a-z0-9]+", term):
            return bool(re.search(rf"\b{re.escape(term)}\b", haystack))
        return term in haystack

    hits = sum(1 for term in terms if term_matches(term))
    return max(0, min(100, int(round((hits / max(1, len(terms))) * 100))))



def _source_noise_reasons(text: Any) -> list[str]:
    normalized = _safe_text(text)
    lowered = normalized.lower()
    reasons: list[str] = []
    if not normalized:
        return ["empty_content"]
    for marker in _SOURCE_NOISE_MARKERS:
        if marker in lowered:
            reasons.append(f"noise:{marker[:32]}")
            break
    if (normalized.count("|") >= 8 or normalized.count("»") >= 5) and not has_readable_table(normalized):
        reasons.append("navigation_or_footer_like_text")
    return reasons



def _jina_api_key() -> str:
    return _safe_text(os.getenv("JINA_API_KEY") or os.getenv("JINA_API_KEYS"))


def _read_with_jina(url: str, *, timeout_seconds: float = 18.0) -> dict[str, Any]:
    key = _jina_api_key()
    if not key:
        return {"ok": False, "provider": "jina", "failureClass": "credential_missing", "reason": "missing_env:JINA_API_KEY"}
    try:
        response = requests.get(
            f"{_JINA_READER_URL_PREFIX}{url}",
            headers={"Authorization": f"Bearer {key}", "Accept": "text/plain"},
            timeout=timeout_seconds,
        )
        text = _safe_text(response.text)
        if response.status_code >= 400:
            return {
                "ok": False,
                "provider": "jina",
                "status": response.status_code,
                "failureClass": "provider_error",
                "reason": text[:300] or f"jina_http_{response.status_code}",
            }
        if not text:
            return {"ok": False, "provider": "jina", "status": response.status_code, "failureClass": "no_content", "reason": "jina_empty_body"}
        return {
            "ok": True,
            "provider": "jina",
            "status": response.status_code,
            "title": "",
            "text": text,
            "textPreview": text[:1200],
            "contentChars": len(text),
            "originalContentChars": len(text),
            "omittedChars": 0,
            "extractionQuality": "jina_reader_markdown",
            "sourceCapability": {"id": "jina", "role": "read_extract", "outputFormats": ["markdown", "text"]},
        }
    except requests.Timeout:
        return {"ok": False, "provider": "jina", "failureClass": "network_timeout", "reason": "jina_reader_timeout"}
    except Exception as exc:  # noqa: BLE001 - Jina is a fallback source, never fatal.
        return {"ok": False, "provider": "jina", "failureClass": "provider_error", "reason": f"{type(exc).__name__}: {exc}"}



def _cleanup_ledger() -> None:
    now = time.time()
    for key, entry in list(_EVIDENCE_LEDGER.items()):
        expires_at = float(entry.get("_expiresAt") or 0)
        if expires_at and expires_at < now:
            _EVIDENCE_LEDGER.pop(key, None)


def _ledger_scope(state: dict[str, Any] | None) -> str:
    from erc.runtime_context import get_runtime_context
    state = dict(state or {})
    context = {**dict(state.get("route_context") or {}), **state, **get_runtime_context()}
    # Experience reuse must survive a new run in the same conversation. The
    # evidence bundle still carries its run lineage; scope controls visibility.
    for key in ("session_id", "sessionId", "conversation_id", "conversationId", "run_id", "runId"):
        value = _safe_text(context.get(key))
        if value:
            return value
    return "global"


def _store_evidence(bundle: dict[str, Any], *, state: dict[str, Any] | None) -> dict[str, Any]:
    _cleanup_ledger()
    config = _research_config()
    scope = _ledger_scope(state)
    access = ResearchAccessScope(state)
    bundle_id = _safe_text(bundle.get("evidenceBundleId")) or f"research_{uuid.uuid4().hex[:12]}"
    stored = {
        **bundle,
        "sourceContext": access.source_context,
        "evidenceBundleId": bundle_id,
        "scope": scope,
        "createdAt": _utc_now_iso(),
        "retention": "run_scoped_memory",
        "_expiresAt": time.time() + config["evidenceTtlSeconds"],
    }
    stored["researchAnswerPack"] = _research_answer_pack(stored)
    try:
        result = store_evidence_bundle(stored, ttl_seconds=config["evidenceTtlSeconds"], scope=scope, access_check=access.allows)
    except PermissionError:
        return research_access_denied("run")
    _EVIDENCE_LEDGER[bundle_id] = stored
    return result


def _compact_visible_quality_gate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        "sourceId": value.get("sourceId"),
        "selectedForEvidence": value.get("selectedForEvidence"),
        "rejectedReason": value.get("rejectedReason"),
        "qualityDimensions": value.get("qualityDimensions"),
        "readabilityReasons": list(value.get("readabilityReasons") or [])[:3],
    }
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_visible_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        "sourceId": value.get("sourceId"),
        "documentId": value.get("documentId"),
        "title": _compact_research_text(value.get("title"), limit=160),
        "url": value.get("url"),
        "requestedUrl": value.get("requestedUrl"),
        "host": value.get("host"),
        "tier": value.get("tier"),
        "provider": value.get("provider"),
        "catalogCategory": value.get("catalogCategory"),
        "popularitySignals": value.get("popularitySignals"),
        "authorityScore": value.get("authorityScore"),
        "freshness": value.get("freshness"),
        "relevanceScore": value.get("relevanceScore"),
        "citationKey": value.get("citationKey"),
        "retrievedAt": value.get("retrievedAt"),
        "publishedAt": value.get("publishedAt"),
        "updatedAt": value.get("updatedAt"),
        "sourceDate": value.get("sourceDate"),
        "sourceDateKind": value.get("sourceDateKind"),
        "version": value.get("version"),
        "temporalEvidence": value.get("temporalEvidence"),
        "subjectFocused": value.get("subjectFocused"),
        "researchFacetId": value.get("researchFacetId"),
        "researchFacetIds": list(value.get("researchFacetIds") or [])[:12],
        "researchFacetGoal": value.get("researchFacetGoal"),
        "evidenceViews": list(value.get("evidenceViews") or [])[:12],
        "evidenceQuery": value.get("evidenceQuery"),
        "contentChars": value.get("contentChars"),
        "originalContentChars": value.get("originalContentChars"),
        "omittedChars": value.get("omittedChars"),
        "evidenceSelection": value.get("evidenceSelection"),
        "readEvidence": value.get("readEvidence"),
        "selectedForEvidence": value.get("selectedForEvidence"),
        "rejectedReason": value.get("rejectedReason") or value.get("reason"),
        "reason": value.get("reason") or value.get("rejectedReason"),
        "qualityDimensions": value.get("qualityDimensions"),
        "detailRef": value.get("detailRef"),
    }
    gate = _compact_visible_quality_gate(value.get("sourceQualityGate"))
    if gate:
        compact["sourceQualityGate"] = gate
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_visible_source_ref(value: Any) -> dict[str, Any] | str:
    """Keep only the stable source aliases needed by a visible claim binding."""

    if isinstance(value, str):
        return _safe_text(value)
    if not isinstance(value, dict):
        return ""
    compact = {
        "sourceId": value.get("sourceId"),
        "url": value.get("url") or value.get("sourceUrl"),
        "citationKey": value.get("citationKey"),
    }
    return {key: item for key, item in compact.items() if item not in (None, "")}


def _compact_visible_claim(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        "claimId": value.get("claimId"),
        "sourceId": value.get("sourceId"),
        "claim": _compact_research_text(value.get("claim"), limit=800),
        "claimType": value.get("claimType") or value.get("claimKind"),
        "normativeCue": _compact_research_text(value.get("normativeCue"), limit=160),
        "sourceRole": value.get("sourceRole"),
        "sourceClaim": _compact_research_text(value.get("sourceClaim"), limit=600),
        "evidenceExcerptKey": value.get("evidenceExcerptKey"),
        "confidence": value.get("confidence"),
        "evidenceExcerpt": _compact_research_text(value.get("evidenceExcerpt"), limit=600),
        "evidenceExcerptSha256": value.get("evidenceExcerptSha256"),
        "evidenceVerified": value.get("evidenceVerified") is True,
    }
    sources = [_compact_visible_source_ref(item) for item in list(value.get("supportingSources") or [])[:4]]
    sources = [item for item in sources if item]
    if sources:
        compact["supportingSources"] = sources
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_delivery_source(value: Any, *, minimal: bool = False) -> dict[str, Any]:
    """Project a cited source for Supervisor consumption, not proof replay.

    Full read receipts, source-quality dimensions, excerpts, and content hashes
    remain in the evidence ledger. Repeating them in every tool observation made
    a successful Research delivery several times larger than its answer.
    """

    if not isinstance(value, dict):
        return {}
    temporal = value.get("temporalEvidence") if isinstance(value.get("temporalEvidence"), dict) else {}
    read_evidence = value.get("readEvidence") if isinstance(value.get("readEvidence"), dict) else {}
    compact = {
        "sourceId": value.get("sourceId"),
        "citationKey": value.get("citationKey"),
        "title": _compact_research_text(value.get("title"), limit=100 if minimal else 160),
        "url": value.get("url") or value.get("sourceUrl"),
        "host": value.get("host"),
        "tier": value.get("tier"),
        "publishedAt": value.get("publishedAt") or temporal.get("publishedAt"),
        "updatedAt": value.get("updatedAt") or temporal.get("updatedAt"),
        "sourceDate": value.get("sourceDate") or temporal.get("sourceDate"),
        "sourceDateKind": value.get("sourceDateKind") or temporal.get("sourceDateKind"),
        "version": value.get("version") or temporal.get("version"),
        "retrievedAt": value.get("retrievedAt") or read_evidence.get("retrievedAt"),
        "selectedForEvidence": value.get("selectedForEvidence") is True,
    }
    if not minimal and read_evidence:
        compact["readEvidence"] = {
            key: item
            for key, item in {
                "verified": read_evidence.get("verified") is True,
                "contentChars": read_evidence.get("contentChars"),
                "retrievedAt": read_evidence.get("retrievedAt"),
            }.items()
            if item not in (None, "")
        }
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_delivery_claim(value: Any, *, minimal: bool = False) -> dict[str, Any]:
    """Keep a navigable claim index while ledger owns excerpt-level proof."""

    if not isinstance(value, dict):
        return {}
    compact = {
        "claimId": value.get("claimId"),
        "claim": (
            ""
            if minimal
            else _compact_research_text(value.get("claim"), limit=260)
        ),
        "claimType": value.get("claimType") or value.get("claimKind"),
        "sourceRole": value.get("sourceRole"),
        "confidence": value.get("confidence"),
        "evidenceVerified": value.get("evidenceVerified") is True,
    }
    sources = [
        _compact_visible_source_ref(item)
        for item in list(value.get("supportingSources") or [])[:4]
    ]
    sources = [item for item in sources if item]
    if sources:
        compact["supportingSources"] = sources
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_delivery_review(value: Any, *, minimal: bool = False) -> dict[str, Any]:
    """Expose the review decision and binding receipt without review transcripts."""

    if not isinstance(value, dict):
        return {}

    def receipt(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        keys = (
            "reviewDecision",
            "questionCoverage",
            "claimEntailment",
            "freshnessAdequacy",
            "bindingVersion",
            "questionFingerprint",
            "answerSha256",
            "claimDigest",
            "sourceDigest",
            "temporalDigest",
            "reviewerModelId",
            "reviewedAt",
            "reviewMode",
            "reviewContract",
            "assessmentDigest",
            "deliveryScope",
            "limitations",
        )
        return {
            key: item.get(key)
            for key in keys
            if item.get(key) not in (None, "", [], {})
        }

    compact = receipt(value)
    if not minimal:
        compact["reviewReasons"] = [
            _compact_research_text(item, limit=220)
            for item in list(value.get("reviewReasons") or [])[:4]
        ]
    for key in (
        "unsupportedClaims",
        "criticalMissingEvidence",
        "recommendedNextQueries",
    ):
        items = list(value.get(key) or [])[:4]
        if items:
            compact[key] = items
    for key in (
        "consensusAccepted",
        "consensusReviewCount",
        "consensusReviewerModelIds",
    ):
        if value.get(key) not in (None, "", [], {}):
            compact[key] = value.get(key)
    consensus = [
        receipt(item)
        for item in list(value.get("consensusReviews") or [])[:4]
    ]
    consensus = [item for item in consensus if item]
    if consensus:
        compact["consensusReviews"] = consensus
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_delivery_answer_pack(value: Any, *, minimal: bool = False) -> dict[str, Any]:
    """Single canonical Agent Surface for a delivery-ready Research result."""

    pack = dict(value or {}) if isinstance(value, dict) else {}
    if not pack:
        return {}
    compact = {
        "reviewDecision": pack.get("reviewDecision"),
        "deliveryScope": pack.get("deliveryScope"),
        "usableAnswer": pack.get("usableAnswer"),
        "answer": _truncate_research_text(
            pack.get("answer"),
            limit=_MAX_RESEARCH_VISIBLE_ANSWER_CHARS,
        ),
        "sources": [
            _compact_delivery_source(item, minimal=minimal)
            for item in list(pack.get("sources") or [])[:_RESEARCH_ARCHITECT_MAX_SOURCE_COUNT]
        ],
        "score": pack.get("score") or {},
        "claimTable": [
            _compact_delivery_claim(item, minimal=minimal)
            for item in list(pack.get("claimTable") or [])[:_RESEARCH_ARCHITECT_MAX_CLAIM_COUNT]
        ],
        "independentReview": _compact_delivery_review(
            pack.get("independentReview"),
            minimal=minimal,
        ),
        "limitations": [
            _compact_research_text(item, limit=220)
            for item in list(pack.get("limitations") or [])[:4]
        ],
        "asOf": pack.get("asOf"),
        "recommendedNextAction": pack.get("recommendedNextAction"),
        "reuseDecision": pack.get("reuseDecision") or {},
        "detailRef": pack.get("detailRef"),
    }
    compact["sources"] = [item for item in compact["sources"] if item]
    compact["claimTable"] = [item for item in compact["claimTable"] if item]
    filtered = {key: item for key, item in compact.items() if item not in (None, [], {})}
    filtered["answer"] = compact.get("answer") or ""
    return filtered


def _compact_visible_model_synthesis(value: Any) -> dict[str, Any]:
    """Project delivery identities without repeating Runtime-only attempt traces."""

    if not isinstance(value, dict):
        return {}
    keys = (
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
        "parseMode",
        "sameEvidenceReviewRejected",
        "writerRevisionCount",
        "writerMode",
        "writerSectionCount",
        "claimPlanMode",
        "claimPlanVersion",
        "claimPlanDigest",
        "claimPlanElapsedMs",
        "claimPlanCandidateCount",
        "claimPlanClaimCount",
        "claimPlanSourceCount",
        "claimPlanRequiredSourceCount",
        "claimPlanRequiredClaimCount",
        "claimPlanCoveredFacetIds",
        "claimPlanMissingSourceKeys",
        "claimPlanMissingFacetIds",
        "claimPlanCoverageComplete",
        "claimPlanSupportedScopeLimited",
        "claimPlanBlockedFacets",
        "structureStatus",
        "structureElapsedMs",
        "writerElapsedMs",
        "reviewElapsedMs",
        "modelPlanCallCount",
    )
    return {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {})
    }


def _compact_visible_evidence_bank(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"version": 1, "selectedSources": [], "rejectedSources": [], "claims": [], "stats": {}}
    selected = [
        _compact_visible_source(item)
        for item in list(value.get("selectedSources") or [])[:_RESEARCH_ARCHITECT_MAX_SOURCE_COUNT]
    ]
    rejected = [_compact_visible_source(item) for item in list(value.get("rejectedSources") or [])[:6]]
    claims = [
        _compact_visible_claim(item)
        for item in list(value.get("claims") or [])[:_RESEARCH_ARCHITECT_MAX_CLAIM_COUNT]
    ]
    return {
        "version": value.get("version"),
        "selectedSources": [item for item in selected if item],
        "rejectedSources": [item for item in rejected if item],
        "claims": [item for item in claims if item],
        "stats": value.get("stats") or {},
    }


def _compact_visible_shard(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fetched: list[dict[str, Any]] = []
    for item in list(value.get("fetchedTopSources") or [])[:3]:
        if not isinstance(item, dict):
            continue
        fetched.append(
            {
                key: val
                for key, val in {
                    "title": _compact_research_text(item.get("title"), limit=140),
                    "url": item.get("url"),
                    "status": item.get("status"),
                    "extractionQuality": item.get("extractionQuality"),
                    "textPreview": _compact_research_text(item.get("textPreview"), limit=240),
                    "providerAttemptMatrix": list(item.get("providerAttemptMatrix") or [])[:4],
                }.items()
                if val not in (None, "", [], {})
            }
        )
    compact = {
        "shardId": value.get("shardId"),
        "kind": value.get("kind"),
        "query": value.get("query"),
        "ok": value.get("ok"),
        "resultCount": value.get("resultCount"),
        "provider": value.get("provider"),
        "networkRoute": value.get("networkRoute"),
        "sourceCapability": value.get("sourceCapability"),
        "fetchedTopSources": fetched,
        "errors": list(value.get("errors") or [])[:3],
    }
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_visible_loop_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = dict(value)
    compact["coveredClaims"] = [_compact_research_text(item, limit=180) for item in list(value.get("coveredClaims") or [])[:6]]
    compact["uncoveredClaims"] = [_compact_research_text(item, limit=180) for item in list(value.get("uncoveredClaims") or [])[:6]]
    compact["conflictClaims"] = list(value.get("conflictClaims") or [])[:6]
    compact["rejectedSources"] = list(value.get("rejectedSources") or [])[:6]
    compact["nextQueries"] = list(value.get("nextQueries") or [])[:4]
    compact["rounds"] = list(value.get("rounds") or [])[:4]
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_visible_answer_pack(value: Any) -> dict[str, Any]:
    pack = dict(value or {}) if isinstance(value, dict) else {}
    if not pack:
        return {}
    compact = {
        "deliveryRequirements": pack.get("deliveryRequirements") or {},
        "deliveryScope": pack.get("deliveryScope"),
        "usableAnswer": pack.get("usableAnswer"),
        "reviewDecision": pack.get("reviewDecision"),
        "reviewReasons": list(pack.get("reviewReasons") or [])[:12],
        "answer": _truncate_research_text(pack.get("answer"), limit=_MAX_RESEARCH_VISIBLE_ANSWER_CHARS),
        "sources": [
            _compact_visible_source(item)
            for item in list(pack.get("sources") or [])[:_RESEARCH_ARCHITECT_MAX_SOURCE_COUNT]
        ],
        "score": pack.get("score") or {},
        "claimTable": [
            _compact_visible_claim(item)
            for item in list(pack.get("claimTable") or [])[:_RESEARCH_ARCHITECT_MAX_CLAIM_COUNT]
        ],
        "independentReview": pack.get("independentReview") or {},
        "limitations": [_compact_research_text(item, limit=220) for item in list(pack.get("limitations") or [])[:6]],
        "criticalMissingEvidence": list(pack.get("criticalMissingEvidence") or [])[:12],
        "recommendedNextQueries": list(pack.get("recommendedNextQueries") or [])[:12],
        "asOf": pack.get("asOf"),
        "missingOrStaleReasons": list(pack.get("missingOrStaleReasons") or [])[:16],
        "recommendedNextAction": pack.get("recommendedNextAction"),
        "rejectedEvidence": [_compact_visible_source(item) for item in list(pack.get("rejectedEvidence") or [])[:6]],
        "reuseDecision": pack.get("reuseDecision") or {},
        "detailRef": pack.get("detailRef"),
    }
    compact["sources"] = [item for item in compact["sources"] if item]
    compact["claimTable"] = [item for item in compact["claimTable"] if item]
    compact["rejectedEvidence"] = [item for item in compact["rejectedEvidence"] if item]
    filtered = {key: item for key, item in compact.items() if item not in (None, [], {})}
    filtered["answer"] = compact.get("answer") or ""
    return filtered


def _visible_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    answer_pack = _research_answer_pack(bundle)
    visible = {key: value for key, value in bundle.items() if not str(key).startswith("_")}
    visible.pop("candidateDraft", None)
    visible["sourceMatrix"] = [
        _compact_visible_source(item)
        for item in list(visible.get("sourceMatrix") or [])[:_RESEARCH_ARCHITECT_MAX_SOURCE_COUNT]
    ]
    visible["sourceMatrix"] = [item for item in visible["sourceMatrix"] if item]
    visible["researchEvidenceBank"] = _compact_visible_evidence_bank(visible.get("researchEvidenceBank"))
    visible["rejectedSources"] = [_compact_visible_source(item) for item in list(visible.get("rejectedSources") or [])[:8]]
    visible["rejectedSources"] = [item for item in visible["rejectedSources"] if item]
    visible["claimTable"] = [
        _compact_visible_claim(item)
        for item in list(visible.get("claimTable") or [])[:_RESEARCH_ARCHITECT_MAX_CLAIM_COUNT]
    ]
    visible["claimTable"] = [item for item in visible["claimTable"] if item]
    visible["researchLoopState"] = _compact_visible_loop_state(visible.get("researchLoopState"))
    raw_shards = list(visible.get("shards") or [])
    raw_shards.sort(key=lambda item: 0 if isinstance(item, dict) and item.get("fetchedTopSources") else 1)
    visible["shards"] = [_compact_visible_shard(item) for item in raw_shards[:8]]
    visible["shards"] = [item for item in visible["shards"] if item]
    visible["researchAnswerPack"] = _compact_visible_answer_pack(answer_pack)
    delivery_ready = bool(((answer_pack.get("score") or {}).get("deliveryReady")))
    for key in ("researchResult", "finalExperiencePack"):
        if isinstance(visible.get(key), dict):
            visible[key] = {**visible[key], "answer": "", "researchResult": ""}
            visible[key].pop("candidateDraft", None)
    if not answer_pack.get("usableAnswer"):
        visible["answer"] = ""
        visible["resultPreview"] = ""
    return visible


def _deterministic_facet_search_query(value: Any, *, max_chars: int = 180) -> str:
    """Turn a task brief into a bounded search-engine query.

    This is only the protocol-failure fallback for the model query planner. A
    full imperative brief often makes public search engines anchor on verbs
    such as ``Verify`` or ``Produce`` instead of the actual subject.
    """

    original = re.sub(r"\s+", " ", _safe_text(value)).strip()
    if not original:
        return ""
    query = re.sub(
        r"^(?:(?:please\s+)?(?:verify|determine|identify|research|investigate|"
        r"analy[sz]e|compare|assess|explain|check|confirm|produce|create|"
        r"generate|draft|prepare|provide)\b[\s,:-]*)+",
        "",
        original,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"^(?:as\s+of\s+(?:\d{4}(?:-\d{1,2}-\d{1,2})?|[A-Za-z]+\s+\d{4})[,]?\s*)",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(
        r"^(?:(?:请)?(?:确认|核实|核查|验证|查证|调查|研究|分析|比较|评估|说明|生成|形成|制定|整理|梳理|给出|输出|提供)[：:,，\s]+)+",
        "",
        query,
    )
    query = re.split(
        r"(?:[,，;；]\s*)(?:并(?:给出|输出|形成|提供)|给出|输出|形成|提供)"
        r"(?:有来源的|带来源的|source-backed|cited)?(?:简体中文|中文|English)?(?:结论|答案|报告|summary|answer|report).*$|"
        r"(?:[,;]\s*)(?:and\s+)?(?:provide|return|write|produce)\s+(?:a\s+)?(?:cited|source-backed|Chinese|English).*$",
        query,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,，;；.。:-")
    query = re.split(
        r"(?:[.;。；]\s*)(?:cite|consult|请?引用|请?参照)\b",
        query,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,，;；.。:-")
    if len(query) > max_chars:
        boundary = 0
        for match in re.finditer(r"[,，;；:]\s*", query[: max_chars + 1]):
            if match.start() >= 80:
                boundary = match.start()
        if not boundary:
            boundary = query.rfind(" ", 80, max_chars + 1)
        query = query[: boundary or max_chars].rstrip(" ,，;；.。:-")
    return query if len(query) >= 8 else original[:max_chars].rstrip()


def _technical_atomic_search_query(value: Any) -> str:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", _safe_text(value))
    stop_words = {
        "a",
        "an",
        "and",
        "answer",
        "chinese",
        "core",
        "current",
        "directly",
        "documentation",
        "english",
        "explain",
        "for",
        "from",
        "in",
        "official",
        "only",
        "pattern",
        "please",
        "provide",
        "report",
        "research",
        "return",
        "simplified",
        "source",
        "sources",
        "the",
        "usable",
        "using",
        "verify",
        "with",
    }
    selected: list[str] = []
    for token in tokens:
        normalized_token = token.strip("._-")
        if (
            not normalized_token
            or normalized_token.lower() in stop_words
            or normalized_token.lower() in {item.lower() for item in selected}
        ):
            continue
        selected.append(normalized_token)
        if len(selected) >= 12:
            break
    return " ".join(selected) if len(selected) >= 2 else ""


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
    search_question = _deterministic_facet_search_query(base_question) or base_question
    intent = _safe_text(research_intent) or "general_research"
    policy = _safe_text(source_policy).lower()
    video_research = _is_video_research(base_question, intent, policy)
    seed_domains = [_domain_from_seed(url) for url in seed_urls]
    domains = [domain for domain in [*allowed_domains, *seed_domains] if domain]
    shards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seed_url in seed_urls:
        normalized_seed = _safe_text(seed_url)
        if not normalized_seed or normalized_seed.lower() in seen:
            continue
        seen.add(normalized_seed.lower())
        shards.append(
            {
                "shardId": f"shard_{len(shards) + 1}_{_query_slug(normalized_seed)}",
                "kind": "seed_url",
                "query": normalized_seed,
                "seedUrl": normalized_seed,
                "evidenceQuery": base_question,
                "reason": "explicit_seed_url",
            }
        )
        if len(shards) >= max_shards:
            return shards
    queries: list[tuple[str, str]] = []
    # A managed Research episode may carry several explicit facets while still
    # requiring one shared evidence bundle and one final answer.  Search each
    # facet with its own bounded query; repeating the complete multi-page
    # question across every shard produces low-relevance discovery results and
    # defeats the point of parallel retrieval.
    facet_queries = [
        (_deterministic_facet_search_query(query), kind)
        for query, kind in _build_question_facet_queries(base_question)
    ]
    facet_queries = [(query, kind) for query, kind in facet_queries if query]
    queries.extend(facet_queries)
    if facet_queries:
        # Preserve a small source-intent dimension in the deterministic
        # fallback.  The normal run asks the bound Architect for better short
        # queries; these rows only keep provider/protocol failures from falling
        # back to repeated copies of the entire multi-facet question.
        year = datetime.now(timezone.utc).year
        for facet_query, facet_kind in facet_queries[
            : _research_authority_query_budget(len(facet_queries))
        ]:
            queries.append(
                (
                    f"{facet_query} official primary source {year}",
                    facet_kind,
                )
            )
    else:
        technical_research = _is_technical_research_question(base_question)
        atomic_search_question = (
            _technical_atomic_search_query(base_question)
            if technical_research
            else ""
        ) or search_question
        queries.append((atomic_search_question, "baseline"))
        official_host_hints = _catalog_official_host_hints(base_question)
        ranked_host_hints = sorted(
            official_host_hints,
            key=lambda item: max(
                [len(_safe_text(alias)) for alias in list(item.get("matchedAliases") or [])]
                or [0]
            ),
            reverse=True,
        )
        best_alias_length = max(
            [
                len(_safe_text(alias))
                for item in ranked_host_hints
                for alias in list(item.get("matchedAliases") or [])
            ]
            or [0]
        )
        relevant_host_hints = [
            item
            for item in ranked_host_hints
            if max(
                [len(_safe_text(alias)) for alias in list(item.get("matchedAliases") or [])]
                or [0]
            )
            >= max(6, int(best_alias_length * 0.65))
        ][:3]
        for hint in relevant_host_hints:
            host = _safe_text(hint.get("host"))
            if host:
                queries.append((f"site:{host} {atomic_search_question}", f"official_site:{host}"))
        if policy in {"official", "authoritative", "primary", ""}:
            queries.append((f"{atomic_search_question} official documentation", "official_docs"))
        if technical_research:
            if re.search(r"\b(?:install|setup|package|pip|npm|version)\b|(?:安装|版本|依赖包)", base_question, re.I):
                queries.append((f"{atomic_search_question} installation package version", "technical_installation"))
            queries.append((f"{atomic_search_question} API reference examples", "technical_api"))
            if re.search(r"\b(?:current|latest|release)\b|(?:当前|最新|发布)", base_question, re.I):
                queries.append(
                    (
                        f"{atomic_search_question} release notes {datetime.now(timezone.utc).year}",
                        "freshness_update",
                    )
                )
        else:
            if intent and intent != "general_research":
                queries.append((f"{search_question} {intent}", "intent"))
            queries.extend(
                [
                    (f"{search_question} research report data statistics", "data_report"),
                    (f"{search_question} independent analysis comparison", "independent_analysis"),
                    (f"{search_question} limitations criticism counterevidence", "counterevidence"),
                    (f"{search_question} latest update {datetime.now(timezone.utc).year}", "freshness_update"),
                ]
            )
    if video_research:
        queries.append((f"{base_question} top videos views likes ranking", "video_popularity"))
        queries.append((f"{base_question} 榜单 播放量 点赞", "video_popularity_cn"))
        for domain in _catalog_hosts_by_category("video_platform")[:6]:
            queries.append((f"site:{domain} {base_question}", f"video_site:{domain}"))
        for domain in _catalog_hosts_by_category("creative_showcase")[:4]:
            queries.append((f"site:{domain} {base_question}", f"creative_site:{domain}"))
    for domain in domains[: max(0, max_shards - len(queries))]:
        queries.append((f"site:{domain} {base_question}", f"site:{domain}"))

    for query, shard_kind in queries:
        normalized = _normalize_research_search_query(query)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        facet_id = _research_facet_id(shard_kind)
        shards.append(
            {
                "shardId": f"shard_{len(shards) + 1}_{_query_slug(normalized)}",
                "kind": shard_kind,
                "query": normalized,
                "evidenceQuery": normalized,
                **({"researchFacetId": facet_id} if facet_id else {}),
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
    raw_final_pack = dict(payload.get("finalExperiencePack") or payload.get("researchResult") or {})
    fallback_final_pack = {
        key: value
        for key, value in raw_final_pack.items()
        if key
        in {
            "kind",
            "question",
            "freshness",
            "architectAgentId",
            "architectName",
            "headline",
            "confidence",
            "sourceUrls",
            "synthesisMode",
            "modelSynthesis",
            "reviewDecision",
            "reviewReasons",
            "criticalMissingEvidence",
            "recommendedNextQueries",
            "asOf",
            "temporalAssessment",
        }
    }
    if isinstance(fallback_final_pack.get("modelSynthesis"), dict):
        fallback_final_pack["modelSynthesis"] = _compact_visible_model_synthesis(
            fallback_final_pack.get("modelSynthesis")
        )
    if raw_final_pack.get("researchResult"):
        fallback_final_pack["researchResult"] = _truncate_research_text(
            raw_final_pack.get("researchResult"),
            limit=_MAX_RESEARCH_VISIBLE_ANSWER_CHARS,
        )
    if isinstance(fallback_final_pack.get("sourceUrls"), list):
        fallback_final_pack["sourceUrls"] = list(
            fallback_final_pack.get("sourceUrls") or []
        )[:_RESEARCH_ARCHITECT_MAX_SOURCE_COUNT]

    fallback = {
        "ok": bool(payload.get("ok")),
        "kind": payload.get("kind") or payload.get("mode") or "research_payload",
        "summary": _safe_text(payload.get("summary"))[:600],
        "answer": _truncate_research_text(
            (payload.get("researchAnswerPack") or {}).get("answer")
            if isinstance(payload.get("researchAnswerPack"), dict)
            else payload.get("answer") or payload.get("resultPreview"),
            limit=_MAX_RESEARCH_VISIBLE_ANSWER_CHARS,
        ),
        "researchAnswerPack": _compact_visible_answer_pack(payload.get("researchAnswerPack") or _research_answer_pack(payload)),
        "finalExperiencePack": fallback_final_pack,
        "evidenceBundleId": payload.get("evidenceBundleId"),
        "deliveryReady": payload.get("deliveryReady"),
        "qualityTier": payload.get("qualityTier"),
        "qualityMetrics": payload.get("qualityMetrics") or {},
        "reviewDecision": payload.get("reviewDecision"),
        "reviewReasons": list(payload.get("reviewReasons") or [])[:12],
        "asOf": payload.get("asOf"),
        "confidence": payload.get("confidence"),
        "authorityScore": payload.get("authorityScore"),
        "researchLoopState": _compact_visible_loop_state(payload.get("researchLoopState")),
        "experienceReuse": payload.get("experienceReuse"),
        "claimTable": [_compact_visible_claim(item) for item in list(payload.get("claimTable") or [])[:5]],
        "conflictMatrix": list(payload.get("conflictMatrix") or [])[:5],
        "sourceMatrix": [_compact_visible_source(item) for item in list(payload.get("sourceMatrix") or [])[:3]],
        "researchEvidenceBank": _compact_visible_evidence_bank(payload.get("researchEvidenceBank")),
        "rejectedSources": [_compact_visible_source(item) for item in list(payload.get("rejectedSources") or [])[:5]],
        "shards": [_compact_visible_shard(item) for item in list(payload.get("shards") or [])[:3]],
        "providerAttemptMatrix": list(payload.get("providerAttemptMatrix") or [])[:12],
        "omitted": {
            **omitted,
            "fallback": "agent_visible_budget_fallback",
            "detailTool": "research_broker(mode='get_evidence', evidenceBundleId=...)",
        },
        "recommendedNextAction": payload.get("recommendedNextAction") or "get_evidence",
    }
    fallback_text = json.dumps(fallback, ensure_ascii=False, indent=2)
    if len(fallback_text) <= max_chars:
        return fallback_text
    if payload.get("deliveryReady") or payload.get("usableAnswer"):
        raw_answer_pack = payload.get("researchAnswerPack") or _research_answer_pack(payload)
        delivery_answer_pack = _compact_delivery_answer_pack(
            raw_answer_pack,
            minimal=True,
        )
        claim_count = len(delivery_answer_pack.get("claimTable") or [])
        delivery_answer_pack.pop("claimTable", None)
        if claim_count:
            delivery_answer_pack["claimTableSummary"] = {
                "claimCount": claim_count,
                "proofLocation": "evidenceBundleId",
            }
        evidence_bundle_id = _safe_text(payload.get("evidenceBundleId"))
        if evidence_bundle_id and not delivery_answer_pack.get("detailRef"):
            delivery_answer_pack["detailRef"] = f"research://bundle/{evidence_bundle_id}"
        delivery_final_pack = {
            key: value
            for key, value in fallback_final_pack.items()
            if key
            in {
                "kind",
                "architectAgentId",
                "architectName",
                "headline",
                "confidence",
                "synthesisMode",
                "modelSynthesis",
                "reviewDecision",
                "asOf",
            }
        }
        loop_state = {
            key: value
            for key, value in _compact_visible_loop_state(
                payload.get("researchLoopState")
            ).items()
            if key in {"phase", "stopReason", "researchLoopReport"}
        }
        priority = {
            "ok": payload.get("ok") is True,
            "kind": payload.get("kind") or "research_evidence_bundle",
            "summary": _safe_text(payload.get("summary"))[:600],
            # researchAnswerPack is the one canonical delivery copy. The full
            # proof graph remains retrievable from the governed ledger.
            "researchAnswerPack": delivery_answer_pack,
            "finalExperiencePack": delivery_final_pack,
            "question": payload.get("question") or fallback_final_pack.get("question"),
            "freshness": payload.get("freshness") or fallback_final_pack.get("freshness"),
            "evidenceBundleId": evidence_bundle_id,
            "deliveryReady": payload.get("deliveryReady") is True,
            "usableAnswer": payload.get("usableAnswer") is True or payload.get("deliveryReady") is True,
            "deliveryScope": payload.get("deliveryScope"),
            "qualityTier": payload.get("qualityTier"),
            "reviewDecision": payload.get("reviewDecision"),
            "asOf": payload.get("asOf"),
            "researchLoopState": loop_state,
            "experienceReuse": payload.get("experienceReuse"),
            "omitted": {
                "agentVisibleOriginalChars": len(text),
                "agentVisibleBudgetChars": max_chars,
                "fallback": "delivery_first_agent_visible_output",
                "runtimeProof": "ledger_only",
                "claimEvidenceOmittedForBudget": claim_count,
                "debugEvidence": "use research_broker(mode='get_evidence', evidenceBundleId=...) for excerpts, hashes, review receipts, shards, and rejected sources",
            },
            "recommendedNextAction": "use_research_answer_pack",
        }
        priority_text = json.dumps(priority, ensure_ascii=False, separators=(",", ":"))
        if len(priority_text) > max_chars:
            review = dict(delivery_answer_pack.get("independentReview") or {})
            review.pop("consensusReviews", None)
            delivery_answer_pack["independentReview"] = review
            priority["researchAnswerPack"] = delivery_answer_pack
            priority_text = json.dumps(priority, ensure_ascii=False, separators=(",", ":"))
        if len(priority_text) > max_chars:
            delivery_answer_pack["sources"] = [
                {
                    key: item.get(key)
                    for key in (
                        "sourceId",
                        "citationKey",
                        "title",
                        "url",
                        "selectedForEvidence",
                    )
                    if item.get(key) not in (None, "", [], {})
                }
                for item in list(delivery_answer_pack.get("sources") or [])
                if isinstance(item, dict)
            ]
            priority["researchAnswerPack"] = delivery_answer_pack
            priority_text = json.dumps(priority, ensure_ascii=False, separators=(",", ":"))
        compatibility = dict(priority)
        compatibility["answer"] = delivery_answer_pack.get("answer") or ""
        compatibility_text = json.dumps(
            compatibility,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(compatibility_text) <= max_chars:
            return compatibility_text
        return priority_text
    answer_pack = _compact_visible_answer_pack(fallback.get("researchAnswerPack"))
    answer_pack["answer"] = _compact_research_text(answer_pack.get("answer"), limit=700)
    answer_pack["sources"] = list(answer_pack.get("sources") or [])[:2]
    answer_pack["claimTable"] = list(answer_pack.get("claimTable") or [])[:2]
    answer_pack["rejectedEvidence"] = list(answer_pack.get("rejectedEvidence") or [])[:2]
    minimal = {
        "ok": fallback.get("ok"),
        "kind": fallback.get("kind"),
        "summary": _compact_research_text(fallback.get("summary"), limit=420),
        "answer": _compact_research_text(fallback.get("answer"), limit=700),
        "researchAnswerPack": answer_pack,
        "finalExperiencePack": {
            key: value
            for key, value in dict(fallback.get("finalExperiencePack") or {}).items()
            if key in {"kind", "architectAgentId", "architectName", "headline", "confidence", "synthesisMode", "modelSynthesis"}
        },
        "evidenceBundleId": fallback.get("evidenceBundleId"),
        "deliveryReady": fallback.get("deliveryReady"),
        "qualityTier": fallback.get("qualityTier"),
        "qualityMetrics": fallback.get("qualityMetrics") or {},
        "reviewDecision": fallback.get("reviewDecision"),
        "asOf": fallback.get("asOf"),
        "confidence": fallback.get("confidence"),
        "authorityScore": fallback.get("authorityScore"),
        "claimTable": list(fallback.get("claimTable") or [])[:3],
        "sourceMatrix": list(fallback.get("sourceMatrix") or [])[:3],
        "researchLoopState": _compact_visible_loop_state(fallback.get("researchLoopState")),
        "experienceReuse": fallback.get("experienceReuse"),
        "researchEvidenceBank": {
            "selectedSources": list((fallback.get("researchEvidenceBank") or {}).get("selectedSources") or [])[:2],
            "rejectedSources": list((fallback.get("researchEvidenceBank") or {}).get("rejectedSources") or [])[:2],
            "claims": list((fallback.get("researchEvidenceBank") or {}).get("claims") or [])[:2],
            "stats": (fallback.get("researchEvidenceBank") or {}).get("stats") or {},
        },
        "shards": list(fallback.get("shards") or [])[:1],
        "omitted": {
            **omitted,
            "fallback": "agent_visible_budget_minimal",
            "detailTool": "research_broker(mode='get_evidence', evidenceBundleId=...)",
        },
        "recommendedNextAction": fallback.get("recommendedNextAction"),
    }
    minimal_text = json.dumps(minimal, ensure_ascii=False, indent=2)
    if len(minimal_text) <= max_chars:
        return minimal_text
    answer_pack.pop("claimTable", None)
    answer_pack.pop("rejectedEvidence", None)
    minimal["researchAnswerPack"] = answer_pack
    minimal["claimTable"] = list(minimal.get("claimTable") or [])[:1]
    minimal["researchEvidenceBank"] = {
        "selectedSources": list((minimal.get("researchEvidenceBank") or {}).get("selectedSources") or [])[:1],
        "rejectedSources": list((minimal.get("researchEvidenceBank") or {}).get("rejectedSources") or [])[:1],
        "claims": [],
        "stats": (minimal.get("researchEvidenceBank") or {}).get("stats") or {},
    }
    minimal["shards"] = []
    return json.dumps(minimal, ensure_ascii=False, indent=2)


def _compact_research_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    cutoff = max(1, limit - 3)
    prefix = text[:cutoff].rstrip()
    # Visible evidence should not end halfway through a sentence or token just
    # because an internal transport field has a character budget. Prefer a
    # complete sentence when one is reasonably close to the boundary, then a
    # word boundary; very long code/URLs still retain the hard upper bound.
    sentence_boundaries = list(
        re.finditer(r"(?<!\s)[.!?。！？](?=\s|$)", prefix)
    )
    if sentence_boundaries and sentence_boundaries[-1].end() >= int(cutoff * 0.55):
        prefix = prefix[: sentence_boundaries[-1].end()].rstrip()
    else:
        word_boundary = prefix.rfind(" ", int(cutoff * 0.70))
        if word_boundary > 0:
            prefix = prefix[:word_boundary].rstrip()
    return prefix + "..."


def _truncate_research_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."



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

_REUSE_GENERIC_IDENTIFIER_TOKENS = {
    "api",
    "check",
    "cli",
    "current",
    "docs",
    "documentation",
    "guide",
    "latest",
    "official",
    "reference",
    "release",
    "releases",
    "runtime",
    "sdk",
    "support",
    "supported",
    "verify",
    "version",
    "versions",
    "windows",
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


def _reuse_identifier_tokens(value: str) -> set[str]:
    """Return distinctive Latin/technical anchors, excluding generic research vocabulary."""

    text = _safe_text(value).lower()
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9_.+-]{2,}", text)
        if token not in _REUSE_STOP_WORDS
        and token not in _REUSE_GENERIC_IDENTIFIER_TOKENS
        and not re.fullmatch(r"20\d{2}", token)
    }


def _reuse_topic_match(question: str, pack: dict[str, Any]) -> tuple[bool, str]:
    normalized_question = re.sub(r"\s+", " ", _safe_text(question).lower()).strip()
    candidate_text = " ".join(
        [
            _safe_text(pack.get("query")),
            _safe_text(pack.get("title")),
        ]
    ).lower()
    if not normalized_question:
        return False, "empty_question"
    if _safe_text(pack.get("topicFingerprint")) == _topic_fingerprint(question):
        candidate_question = re.sub(r"\s+", " ", _safe_text(pack.get("query")).lower()).strip()
        if candidate_question == normalized_question:
            return True, "topic_fingerprint_match"
        return True, "topic_fingerprint_variant_requires_review"
    if normalized_question and normalized_question in candidate_text:
        return True, "exact_question_contained_in_candidate"
    question_identifiers = _reuse_identifier_tokens(question)
    candidate_identifiers = _reuse_identifier_tokens(candidate_text)
    if question_identifiers and candidate_identifiers and not question_identifiers.intersection(candidate_identifiers):
        return False, "distinctive_identifier_mismatch"
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


def _explicit_question_as_of(question: str) -> datetime | None:
    text = _safe_text(question)
    patterns = (
        r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?!\d)",
        r"(?<!\d)(20\d{2})[-/.年](\d{1,2})(?:月)?(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3)) if match.lastindex and match.lastindex >= 3 else 1
            return datetime(year, month, day, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _experience_pack_as_of(pack: dict[str, Any]) -> datetime | None:
    answer_pack = pack.get("researchAnswerPack") if isinstance(pack.get("researchAnswerPack"), dict) else {}
    raw = _safe_text(pack.get("asOf") or answer_pack.get("asOf"))
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    exact_candidates: list[tuple[dict[str, Any], str]] = []
    adjacent_candidates: list[tuple[dict[str, Any], str]] = []
    for pack in packs:
        matched, match_reason = _reuse_topic_match(question, pack)
        if not matched:
            continue
        target = exact_candidates if match_reason == "topic_fingerprint_match" else adjacent_candidates
        target.append((pack, match_reason))

    refresh_candidates: list[dict[str, Any]] = []
    requested_as_of = _explicit_question_as_of(question)
    for pack, match_reason in exact_candidates:
        if (pack.get("independentReview") or {}).get("reviewContract") == "research-agent-review.v1":
            return {
                "reuseDecision": "reuse" if pack.get("deliveryScope") == "complete" else "review",
                "reason": "reviewed_answer_matches_question" if pack.get("deliveryScope") == "complete" else "partial_answer_requires_applicability_review",
                "candidatePackId": pack.get("experiencePackId"), "matchReason": match_reason,
                "topicFingerprint": pack.get("topicFingerprint") or _topic_fingerprint(question),
                "skippedSearches": pack.get("deliveryScope") == "complete",
                "supervisorContentNote": "已有答案保留原始资料日期；是否需要新近核查由 Supervisor 结合本次任务判断。需要更新时用 run + experiencePackId；强制重抓才用 forceRefresh。",
            }
        pack_freshness_state = _safe_text(pack.get("freshnessState")).lower()
        freshness_notice: dict[str, Any] = {}
        if pack_freshness_state in {"aging", "stale", "expired"}:
            pack_as_of_text = _safe_text(pack.get("asOf")) or "unknown"
            age_days = pack.get("ageDays")
            age_text = f", age {age_days} days" if age_days not in (None, "") else ""
            freshness_notice = {
                "freshnessState": pack_freshness_state,
                "ageDays": age_days,
                "staleAt": pack.get("staleAt"),
                "expiresAt": pack.get("expiresAt"),
                "refreshSuggested": True,
                "supervisorContentNote": (
                    f"Reused Research experience is {pack_freshness_state} "
                    f"(as of {pack_as_of_text}{age_text}). Decide whether that dated evidence is still "
                    "sufficient; if current facts could change the answer, rerun Research with forceRefresh=true."
                ),
            }
        pack_as_of = _experience_pack_as_of(pack)
        if requested_as_of and (pack_as_of is None or requested_as_of > pack_as_of):
            refresh_candidates.append({
                "reuseDecision": "refresh",
                "reason": "requested_as_of_newer_than_experience_pack",
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "requestedAsOf": requested_as_of.date().isoformat(),
                "candidateAsOf": pack_as_of.isoformat().replace("+00:00", "Z") if pack_as_of else None,
                "topicFingerprint": pack.get("topicFingerprint") or _topic_fingerprint(question),
            })
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
            refresh_candidates.append({
                "reuseDecision": "refresh",
                "reason": "refresh_required_due_to_pack_quality",
                "qualityReasons": reasons,
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "candidateConfidence": pack.get("confidence"),
                "topicFingerprint": pack.get("topicFingerprint") or _topic_fingerprint(question),
            })
            continue
        confidence = _safe_text(pack.get("confidence")).lower()
        if _confidence_rank(confidence) < min_rank:
            refresh_candidates.append({
                "reuseDecision": "refresh",
                "reason": "candidate_confidence_below_threshold",
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "candidateConfidence": confidence,
                "requiredConfidence": min_confidence or "medium",
                "topicFingerprint": _topic_fingerprint(question),
            })
            continue
        pack_policy = _safe_text(pack.get("sourcePolicy")).lower()
        if pack_policy and normalized_policy and pack_policy != normalized_policy:
            refresh_candidates.append({
                "reuseDecision": "refresh",
                "reason": "source_policy_changed",
                "matchReason": match_reason,
                "candidatePackId": pack.get("experiencePackId"),
                "previousSourcePolicy": pack_policy,
                "requestedSourcePolicy": normalized_policy,
                "topicFingerprint": _topic_fingerprint(question),
            })
            continue
        return {
            "reuseDecision": "reuse",
            "reason": (
                "dated_experience_pack_reused_with_supervisor_refresh_note"
                if freshness_notice
                else "high_confidence_pack_matches_topic_and_policy"
            ),
            "matchReason": match_reason,
            "candidatePackId": pack.get("experiencePackId"),
            "candidateTitle": pack.get("title"),
            "candidateConfidence": confidence,
            "skippedSearches": True,
            "topicFingerprint": pack.get("topicFingerprint") or _topic_fingerprint(question),
            **freshness_notice,
        }
    if refresh_candidates:
        return refresh_candidates[0]
    if adjacent_candidates:
        pack, match_reason = adjacent_candidates[0]
        return {
            "reuseDecision": "review",
            "reason": "adjacent_topic_requires_fresh_semantic_review",
            "matchReason": match_reason,
            "candidatePackId": pack.get("experiencePackId"),
            "topicFingerprint": _topic_fingerprint(question),
        }
    return {
        "reuseDecision": "ignore",
        "reason": "no_topic_matched_reusable_candidate_after_filtering",
        "candidatePackId": None,
        "topicFingerprint": _topic_fingerprint(question),
    }


def _bundle_from_reused_pack(pack: dict[str, Any], *, question: str, reuse: dict[str, Any], deliverable: str) -> dict[str, Any]:
    canonical_answer_pack = (
        dict(pack.get("researchAnswerPack"))
        if isinstance(pack.get("researchAnswerPack"), dict)
        else {}
    )
    source_matrix = [
        dict(item)
        for item in list(
            canonical_answer_pack.get("sources")
            or pack.get("sourceMatrixDigest")
            or pack.get("sourceUrls")
            or []
        )
        if isinstance(item, dict)
    ]
    source_urls = _source_urls_from_matrix(source_matrix)
    result = _safe_text(
        canonical_answer_pack.get("answer")
        or pack.get("researchResult")
        or pack.get("resultPreview")
        or pack.get("summary")
    )
    claim_table = list(canonical_answer_pack.get("claimTable") or pack.get("claimDigest") or [])
    independent_review = (
        canonical_answer_pack.get("independentReview")
        if isinstance(canonical_answer_pack.get("independentReview"), dict)
        else pack.get("independentReview") or {}
    )
    experience_pack_id = _safe_text(pack.get("experiencePackId"))
    evidence_bundle_id = _safe_text(pack.get("createdFromBundleId"))
    detail_ref = (
        f"research://bundle/{evidence_bundle_id}"
        if evidence_bundle_id
        else f"research://experience/{experience_pack_id}"
        if experience_pack_id
        else ""
    )
    architect_pack = {
        "kind": "research_result_pack",
        "researchContract": pack.get("researchContract"),
        "deliveryScope": pack.get("deliveryScope"),
        "limitations": list(pack.get("limitations") or []),
        "architectAgentId": "web-research-architect",
        "architectName": "Web Research Architect",
        "question": question,
        "headline": f"Reused experience pack: {pack.get('title') or pack.get('experiencePackId')}",
        "reviewDecision": canonical_answer_pack.get("reviewDecision") or pack.get("reviewDecision"),
        "reviewReasons": list(pack.get("reviewReasons") or []),
        "answer": result,
        "researchResult": result,
        "claimTable": claim_table,
        "independentReview": independent_review,
        "sourceUrls": source_matrix,
        "criticalMissingEvidence": list(pack.get("criticalMissingEvidence") or []),
        "recommendedNextQueries": list(pack.get("recommendedNextQueries") or []),
        "asOf": canonical_answer_pack.get("asOf") or pack.get("asOf"),
        "temporalAssessment": pack.get("temporalAssessment") if isinstance(pack.get("temporalAssessment"), dict) else {},
        "confidence": pack.get("confidence"),
        "authorityScore": pack.get("authorityScore"),
        "createdAt": _utc_now_iso(),
    }
    bundle = {
        "ok": False,
        "kind": "research_evidence_bundle",
        "researchContract": pack.get("researchContract"),
        "deliveryScope": pack.get("deliveryScope"),
        "limitations": list(pack.get("limitations") or []),
        "summary": architect_pack["headline"],
        "question": question,
        "evidenceBundleId": evidence_bundle_id or None,
        "experiencePackId": experience_pack_id or None,
        "detailRef": detail_ref or None,
        "researchIntent": "experience_reuse",
        "sourcePolicy": pack.get("sourcePolicy"),
        "freshness": pack.get("requestedFreshness") or pack.get("freshness") or pack.get("freshnessWindow"),
        "deliverable": deliverable,
        "answer": result,
        "resultPreview": result,
        "researchResult": architect_pack,
        "finalExperiencePack": architect_pack,
        "researchAnswerPack": canonical_answer_pack,
        "claimTable": claim_table,
        "briefCoverageRequired": pack.get("briefCoverageRequired") is True,
        "briefCoverageComplete": pack.get("briefCoverageComplete") is True,
        "briefCoverage": [
            dict(item)
            for item in list(pack.get("briefCoverage") or [])
            if isinstance(item, dict)
        ],
        "coveredTaskBriefIds": list(pack.get("coveredTaskBriefIds") or []),
        "missingTaskBriefIds": list(pack.get("missingTaskBriefIds") or []),
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
        "experienceReuse": {**reuse, "experiencePackId": experience_pack_id, "originalAsOf": pack.get("asOf")},
        "confidence": pack.get("confidence") or "medium",
        "authorityScore": pack.get("authorityScore"),
        "recommendedNextAction": "use_reused_experience_pack",
    }
    bundle["researchAnswerPack"] = _research_answer_pack(bundle)
    delivery_ready = bool(((bundle["researchAnswerPack"].get("score") or {}).get("deliveryReady")))
    bundle["ok"] = delivery_ready
    bundle["answer"] = bundle["researchAnswerPack"].get("answer") or ""
    bundle["resultPreview"] = bundle["answer"]
    bundle["reviewDecision"] = bundle["researchAnswerPack"].get("reviewDecision")
    bundle["reviewReasons"] = list(bundle["researchAnswerPack"].get("reviewReasons") or [])
    bundle["independentReview"] = bundle["researchAnswerPack"].get("independentReview") or {}
    bundle["asOf"] = bundle["researchAnswerPack"].get("asOf")
    bundle["qualityTier"] = (bundle["researchAnswerPack"].get("score") or {}).get("qualityTier")
    bundle["qualityMetrics"] = (
        (bundle["researchAnswerPack"].get("score") or {}).get("acceptanceMetrics")
        or {}
    )
    bundle["deliveryReady"] = delivery_ready
    bundle["recommendedNextAction"] = "use_reused_experience_pack" if delivery_ready else "refresh_research"
    return bundle



def _research_architect_fallback_model_refs() -> list[str]:
    raw = os.environ.get("V8_RESEARCH_ARCHITECT_MODEL_FALLBACKS", "")
    refs = [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()] if raw else []
    deduped: list[str] = []
    for ref in refs:
        if ref and ref not in deduped:
            deduped.append(ref)
    return deduped


def _create_web_research_architect_llm_candidates() -> list[tuple[Any, str, str]]:
    from core.llm_factory import llm_factory

    agent_id = "web-research-architect"
    candidates: list[tuple[Any, str, str]] = []
    seen: set[str] = set()

    def add_candidate(
        factory,
        *,
        model_id: str,
        role: str,
        resolved_model_ref: str = "",
        selection_origin: str,
    ) -> None:
        key = _safe_text(resolved_model_ref or model_id).lower()
        if key in seen:
            return
        seen.add(key)
        llm = factory()
        meta = getattr(llm, "_meta", None)
        if isinstance(meta, dict):
            meta.setdefault("research_candidate_origin", selection_origin)
        candidates.append((llm, model_id, role))

    try:
        model_id = _safe_text(storage.get_agent_model_binding(agent_id))
    except Exception:
        model_id = ""
    if model_id:
        add_candidate(
            lambda model_id=model_id: llm_factory.create_chat_model(model_id, temperature=0.1, max_retries=0, _role=agent_id),
            model_id=model_id,
            role=agent_id,
            resolved_model_ref=model_id,
            selection_origin="agent_binding",
        )
    last_error: Exception | None = None
    # `research` is a runtime kind, not a registered Config Broker model role.
    # Using it here silently falls through to an unrelated default model and
    # bypasses the explicit Architect binding. A generic ``summary`` role is
    # also not an Architect: letting it repair or replace a dedicated binding
    # changes the information chain and makes provider/model behavior appear
    # nondeterministic. Use the registered Subagent role only when the dedicated
    # binding is absent; additional Architect models must be explicitly listed
    # in V8_RESEARCH_ARCHITECT_MODEL_FALLBACKS.
    fallback_roles = () if model_id else ("subagent",)
    for role in fallback_roles:
        try:
            resolved_model_ref = _safe_text(storage.get_role_model_id(role))
            add_candidate(
                lambda role=role: llm_factory.create_for_role(role, temperature=0.1, max_retries=0),
                model_id=f"role:{role}",
                role=role,
                resolved_model_ref=resolved_model_ref or f"role:{role}",
                selection_origin=f"role_fallback:{role}",
            )
        except Exception as exc:  # noqa: BLE001 - role fallback is intentional.
            last_error = exc
    for model_ref in _research_architect_fallback_model_refs():
        try:
            add_candidate(
                lambda model_ref=model_ref: llm_factory.create_chat_model(model_ref, temperature=0.1, max_retries=0, _role=agent_id),
                model_id=model_ref,
                role=agent_id,
                resolved_model_ref=model_ref,
                selection_origin="configured_fallback",
            )
        except Exception as exc:  # noqa: BLE001 - try the next explicitly configured model.
            last_error = exc
    if not candidates:
        try:
            add_candidate(
                lambda: llm_factory.create_for_role("supervisor", temperature=0.1, max_retries=0),
                model_id="role:supervisor",
                role="supervisor",
                resolved_model_ref=_safe_text(storage.get_role_model_id("supervisor")) or "role:supervisor",
                selection_origin="role_fallback:supervisor",
            )
        except Exception as exc:  # noqa: BLE001 - final configured-role fallback.
            last_error = exc
    if candidates:
        return candidates
    if last_error:
        raise last_error
    raise ValueError("No model configured for Web Research Architect synthesis.")



def _create_web_research_reviewer_llm_candidates(
    architect_candidates: list[tuple[Any, str, str]],
) -> list[tuple[Any, str, str]]:
    """Resolve the configured Verification Engineer before other reviewers.

    Independent review is a separate conversation, not a requirement to use
    another provider/model or a second adversarial pass. Never overwrite an
    explicit user binding to manufacture model diversity.
    """

    candidates = list(architect_candidates)
    if not any(
        _architect_candidate_selection_origin(candidate) == "agent_binding"
        for candidate in candidates
    ):
        return candidates
    from core.llm_factory import llm_factory

    governed_reviewers: list[tuple[Any, str, str]] = []
    try:
        reviewer = llm_factory.create_for_role(
            "supervisor",
            temperature=0.0,
            max_retries=0,
        )
        meta = getattr(reviewer, "_meta", None)
        if isinstance(meta, dict):
            meta.setdefault("research_candidate_origin", "role_reviewer:supervisor")
        governed_reviewers.append((reviewer, "role:supervisor", "supervisor"))
    except Exception:
        pass

    verification_agent_id = "verification-engineer"
    try:
        verification_model_ref = _safe_text(
            storage.get_agent_model_binding(verification_agent_id)
        )
    except Exception:
        verification_model_ref = ""
    if verification_model_ref:
        try:
            reviewer = llm_factory.create_chat_model(
                verification_model_ref,
                temperature=0.0,
                max_retries=0,
                _role=verification_agent_id,
            )
            meta = getattr(reviewer, "_meta", None)
            if isinstance(meta, dict):
                meta.setdefault(
                    "research_candidate_origin",
                    "agent_reviewer:verification-engineer",
                )
            governed_reviewers.insert(0, (reviewer, verification_model_ref, verification_agent_id))
        except Exception:
            pass

    ordered: list[tuple[Any, str, str]] = []
    seen: set[str] = set()
    for candidate in [*governed_reviewers, *candidates]:
        identity = _architect_candidate_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        ordered.append(candidate)
    return ordered


def _architect_candidate_identity(candidate: tuple[Any, str, str]) -> str:
    llm, model_id, _role = candidate
    meta = getattr(llm, "_meta", None)
    if isinstance(meta, dict):
        resolved = _safe_text(meta.get("model_ref") or meta.get("modelRef"))
        if resolved:
            return resolved.lower()
        provider = _safe_text(meta.get("provider_id") or meta.get("provider_name"))
        wire_model = _safe_text(meta.get("model_id")) or _safe_text(getattr(llm, "model_id", ""))
        if provider and wire_model:
            return f"{provider}::{wire_model}".lower()
    return _safe_text(model_id).lower()


def _architect_candidate_context_model_ref(candidate: tuple[Any, str, str]) -> str:
    """Return the case-preserving model ref used by the model control plane."""

    llm, model_id, _role = candidate
    meta = getattr(llm, "_meta", None)
    if isinstance(meta, dict):
        resolved = _safe_text(meta.get("model_ref") or meta.get("modelRef"))
        if resolved:
            return resolved
        provider = _safe_text(meta.get("provider_id") or meta.get("provider_name"))
        wire_model = _safe_text(meta.get("model_id")) or _safe_text(
            getattr(llm, "model_id", "")
        )
        if provider and wire_model:
            return f"{provider}::{wire_model}"
    return _safe_text(model_id)


def _architect_candidate_selection_origin(candidate: tuple[Any, str, str]) -> str:
    meta = getattr(candidate[0], "_meta", None)
    if isinstance(meta, dict):
        origin = _safe_text(meta.get("research_candidate_origin"))
        if origin:
            return origin
    role = _safe_text(candidate[2]).lower()
    return f"role_fallback:{role}" if role else "unknown"



def _invoke_architect_candidate_with_deadline(
    candidate: tuple[Any, str, str],
    messages: list[Any],
    *,
    seconds: float,
    max_tokens: int | None,
    disable_thinking: bool = False,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    idle_timeout_seconds: float | None = None,
) -> Any:
    llm = candidate[0]
    from core.model_token_policy import resolve_output_token_budget
    output_budget = resolve_output_token_budget(getattr(llm, "_meta", None) or {}, max_tokens)
    effective_max_tokens = output_budget["maxTokens"]
    if tools is not None:
        llm = llm.bind_tools(tools, **({"tool_choice": tool_choice} if tool_choice else {}))
    request_timeout = max(0.25, min(float(seconds), float(idle_timeout_seconds or seconds)) - 0.25)
    request_kwargs: dict[str, Any] = {
        "timeout": request_timeout,
        **({"max_tokens": effective_max_tokens} if effective_max_tokens is not None else {}),
    }
    if tools is not None:
        from runtimes.research.model_call import invoke_bounded
        meta = getattr(llm, "_meta", None) or {}
        supports_streaming = (meta.get("effective_capability_matrix") or {}).get("supports_streaming", True)
        return invoke_bounded(llm, messages, seconds=seconds, request_kwargs=request_kwargs,
                              streaming=supports_streaming is True and callable(getattr(llm, "stream", None)),
                              idle_timeout_seconds=idle_timeout_seconds)
    if disable_thinking:
        meta = getattr(llm, "_meta", None)
        control = dict(meta.get("thinking_control") or {}) if isinstance(meta, dict) else {}
        if control.get("supportsNoThink"):
            control["disabled"] = True
            request_kwargs.update(no_think_request_patch(control))
    ainvoke = getattr(llm, "ainvoke", None)
    if callable(ainvoke) and threading.current_thread() is threading.main_thread():
        async def invoke_async() -> Any:
            return await asyncio.wait_for(
                ainvoke(
                    messages,
                    config={"callbacks": []},
                    **request_kwargs,
                ),
                timeout=request_timeout,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(invoke_async())
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(lambda: asyncio.run(invoke_async()))
        try:
            return future.result(timeout=max(0.5, float(seconds)))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        lambda: llm.invoke(
            messages,
            config={"callbacks": []},
            **request_kwargs,
        )
    )
    try:
        return future.result(timeout=max(0.5, float(seconds)))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)



def _research_read_observations(
    shards: list[dict[str, Any]], read_attempt_ledger: _ResearchReadAttemptLedger | None = None,
) -> list[dict[str, Any]]:
    observations = []
    for shard in shards:
        results = {
            _safe_text(item.get("url")): item
            for item in shard.get("results") or [] if isinstance(item, dict)
        }
        for read in shard.get("fetchedTopSources") or []:
            if not isinstance(read, dict) or read.get("ok") is not True:
                continue
            if read.get("missingContentReason"):
                continue
            try:
                if int(read.get("statusCode") or read.get("status") or 0) >= 400:
                    continue
            except (TypeError, ValueError):
                pass
            result = results.get(_safe_text(read.get("url"))) or {}
            temporal = _source_temporal_evidence(read, result)
            cached = read_attempt_ledger.cached_payload(read.get("url")) if read_attempt_ledger else None
            body = (cached or {}).get("originalText") or read.get("text") or read.get("markdown") or ""
            observations.append({
                **result, **read, **temporal,
                # textPreview/search snippets do not become full read evidence.
                "text": body, "omittedChars": max(0, int(read.get("originalContentChars") or len(body)) - len(body)),
                "links": ((cached or {}).get("readPayload") or {}).get("links") or read.get("links") or [],
            })
    return observations


def _execute_research_agent(
    *, question: str, shards: list[dict[str, Any]], freshness: str,
    preferred_language: str = "", acquire: Callable[..., dict[str, Any]] | None = None,
    max_searches: int = 3, state: dict[str, Any] | None = None,
    read_attempt_ledger: _ResearchReadAttemptLedger | None = None,
    previous_bundle: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    from core.context_orchestrator import context_orchestrator
    from core.context_governance import emit_context_prepared_event
    from runtimes.research.agent import ResearchAgent

    if not _research_config().get("architectAgentSynthesisEnabled", True):
        return {"researchContract": "agent-research.v1", "answer": "", "deliveryScope": "none", "reviewDecision": "retry", "reviewReasons": ["research_agent_disabled"], "modelSynthesis": {"used": False, "fallbackReason": "research_agent_disabled"}}
    try:
        candidates = _create_web_research_architect_llm_candidates()
        reviewers = _create_web_research_reviewer_llm_candidates(candidates)
        if not candidates or not reviewers:
            raise ValueError("configured_research_model_missing")
    except Exception as exc:
        return {"researchContract": "agent-research.v1", "answer": "", "deliveryScope": "none", "reviewDecision": "retry", "reviewReasons": ["research_model_configuration_unavailable"], "modelSynthesis": {"used": False, "fallbackReason": "research_model_configuration_unavailable", "errorType": type(exc).__name__}}
    writer = candidates[0]
    reviewers.sort(key=lambda candidate: _architect_candidate_selection_origin(candidate) != "agent_reviewer:verification-engineer")
    reviewer = reviewers[0]
    context_audits: list[dict[str, Any]] = []

    def invoke(messages, tools, *, reviewer: bool, seconds: float, required: bool = False):
        candidate = reviewers[0] if reviewer else writer
        role = "research-reviewer" if reviewer else "web-research-architect"
        from erc.runtime_context import bind_runtime_context

        with bind_runtime_context(
            session_id=(state or {}).get("session_id") or (state or {}).get("sessionId"),
            run_id=(state or {}).get("run_id") or (state or {}).get("runId"),
            runtime_episode_id=(state or {}).get("runtime_episode_id"),
            runtime_kind="research", agent_id=role,
        ):
            prepared = context_orchestrator.prepare(
                messages=messages, runtime_kind="research", target_role=role,
                resolved_model_id=_architect_candidate_context_model_ref(candidate),
                keep_recent_override=6,
            )
            emit_context_prepared_event(prepared.audit, component="research", node=role, agent_id=role)
            context_audits.append({
                "role": role, "estimatedInputTokens": prepared.audit.get("estimated_input_tokens"),
                "compactionApplied": prepared.audit.get("compaction_applied") is True,
                "modelId": _architect_candidate_identity(candidate),
            })
            return _invoke_architect_candidate_with_deadline(
                candidate, prepared.messages, seconds=seconds,
                max_tokens=None, tools=tools, tool_choice="required" if required else None,
                idle_timeout_seconds=float(_research_config().get("architectAgentTimeoutSeconds") or 60),
            )

    def cancelled() -> bool:
        run_id = _safe_text((state or {}).get("run_id") or (state or {}).get("runId"))
        if not run_id:
            return False
        from core.database import db
        run = db.get_run_record(run_id)
        return bool(run and _safe_text(run.get("status")) in {"cancelled", "canceled"})

    agent = ResearchAgent(
        invoke=invoke, acquire=acquire, progress=_report_research_progress,
        writer_id=_architect_candidate_identity(writer), reviewer_id=_architect_candidate_identity(reviewer),
        timeout_seconds=timeout_seconds if timeout_seconds is not None else _RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS / 1000,
        max_searches=max_searches, cancelled=cancelled,
        original_user_request=str((state or {}).get("research_original_user_request") or ""),
    )
    if previous_bundle:
        agent.store.restore((previous_bundle.get("researchEvidenceBank") or {}).get("sources") or [])
    agent.store.add(_research_read_observations(shards, read_attempt_ledger))
    previous_answer = {key: previous_bundle.get(key) for key in ("question", "answer", "deliveryScope", "limitations", "asOf")} if previous_bundle else None
    result = agent.run(question=question, freshness=freshness, language=preferred_language or infer_preferred_language(question), previous_answer=previous_answer)
    result.setdefault("modelSynthesis", {})["contextPreparations"] = context_audits
    result["readSources"] = list(agent.store.sources.values())
    return result


def _run_agent_owned_research(
    *, question: str, research_intent: str, source_policy: str, freshness: str,
    allowed_domains: list[str], blocked_domains: list[str], use_agent_browser_profile: bool,
    tool_call_id: str, max_shards: int, max_rounds: int, preferred_language: str,
    seed_urls: list[str], deliverable: str, experience_reuse: dict[str, Any],
    state: dict[str, Any] | None = None,
    previous_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    completed_shards: list[dict[str, Any]] = []
    attempt_ledger = _ResearchReadAttemptLedger(question=question)
    rounds: list[dict[str, Any]] = []

    def acquire(*, queries: list[str], urls: list[str], seconds: float, source: str = "web", search_engine: str = "auto", fetch_mode: str = "auto") -> dict[str, Any]:
        batch_deadline = time.monotonic() + seconds
        round_index = len(rounds) + 1
        requests_to_run = [
            {"kind": "seed_url", "query": url, "seedUrl": url, "evidenceQuery": question}
            for url in urls
        ] + [{"kind": "agent_query", "query": query, "searchEngine": search_engine, "fetchMode": fetch_mode} for query in queries]
        # Bound concurrency without dropping explicitly chosen URLs/queries.
        for index, shard in enumerate(requests_to_run, start=1):
            shard["shardId"] = f"research_agent_{round_index}_{index}"
        network_requests = [item for item in requests_to_run if source != "documentation" or item["kind"] == "seed_url"]
        fetched = _run_search_shards(
            network_requests, allowed_domains=allowed_domains, blocked_domains=blocked_domains,
            source_policy=source_policy, max_rounds=max_rounds,
            use_agent_browser_profile=use_agent_browser_profile, tool_call_id=tool_call_id,
            read_attempt_ledger=attempt_ledger, read_round=round_index,
            preferred_language=preferred_language, deadline_seconds=seconds,
            max_parallel_shards=max_shards,
        )
        if source == "documentation":
            for query in queries:
                remaining = batch_deadline - time.monotonic()
                if remaining <= 0:
                    fetched.append({"query": query, "ok": False, "errors": ["research_shard_deadline_exceeded"]})
                else:
                    fetched.append(_run_context7_source(query, tool_call_id=tool_call_id, timeout_seconds=min(20, remaining)))
        completed_shards.extend(fetched)
        observations = _research_read_observations(fetched, attempt_ledger)
        rounds.append({"round": round_index, "queries": queries, "urls": urls, "readSourceCount": len(observations)})
        from runtimes.research.acquisition import acquisition_feedback

        return {"sources": observations, "diagnostics": acquisition_feedback(fetched)}

    if seed_urls:
        acquire(queries=[], urls=seed_urls, seconds=_RESEARCH_TOOL_DEADLINE_MS / 1000)
    pack = _execute_research_agent(
        question=question, shards=completed_shards, freshness=freshness,
        preferred_language=preferred_language, acquire=acquire,
        max_searches=max_rounds, state=state, read_attempt_ledger=attempt_ledger,
        previous_bundle=previous_bundle,
        timeout_seconds=max(0, _RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS / 1000 - (time.perf_counter() - started)),
    )
    read_sources = pack.pop("readSources", [])
    selected_keys = {row["citationKey"] for row in pack.get("sourceUrls") or []}
    source_matrix = [
        {key: value for key, value in row.items() if key not in {"text", "links"}}
        | {"selectedForEvidence": row["citationKey"] in selected_keys}
        for row in read_sources
    ]
    loop_state = {
        "phase": "research_agent", "rounds": rounds,
        "readableSourceCount": len(read_sources), "selectedSourceCount": len(selected_keys),
        "stopReason": "answer_reviewed" if pack.get("reviewDecision") == "accept" else (
            (pack.get("modelSynthesis") or {}).get("fallbackReason") or "research_incomplete"
        ),
        "readAttemptStats": attempt_ledger.snapshot(),
        "performance": {"totalElapsedMs": int((time.perf_counter() - started) * 1000)},
        "agentTrace": (pack.get("modelSynthesis") or {}).get("trace") or [],
    }
    bundle = {
        "researchContract": "agent-research.v1", "kind": "research_evidence_bundle", "question": question,
        "topicFingerprint": _topic_fingerprint(question), "researchIntent": research_intent,
        "sourcePolicy": source_policy, "freshness": freshness, "deliverable": deliverable,
        "summary": "调研已形成有据可查的答案" if pack.get("reviewDecision") == "accept" else "调研尚未形成可交付答案",
        "deliveryRequirements": _research_delivery_requirements(question),
        "answer": pack.get("answer") or "", "resultPreview": pack.get("answer") or "",
        "researchResult": pack, "finalExperiencePack": pack,
        "claimTable": pack.get("claimTable") or [], "sourceMatrix": source_matrix,
        "sourceUrls": [row["url"] for row in pack.get("sourceUrls") or []],
        "reviewDecision": pack.get("reviewDecision"), "independentReview": pack.get("independentReview") or {},
        "deliveryScope": pack.get("deliveryScope"), "limitations": pack.get("limitations") or [],
        "criticalMissingEvidence": pack.get("criticalMissingEvidence") or [],
        "reviewReasons": pack.get("reviewReasons") or [], "recommendedNextQueries": [],
        "asOf": pack.get("asOf") or _utc_now_iso(), "confidence": pack.get("confidence") or "low",
        "researchLoopState": loop_state, "experienceReuse": experience_reuse,
        "researchEvidenceBank": {"sources": read_sources, "claims": pack.get("claimTable") or [], "rejectedSources": []},
        "shards": completed_shards, "providerAttemptMatrix": _flatten_provider_attempts(completed_shards),
        "citations": [{"title": row["title"], "url": row["url"]} for row in pack.get("sourceUrls") or []],
    }
    answer_pack = _research_answer_pack(bundle)
    bundle.update(
        researchAnswerPack=answer_pack, answer=answer_pack["answer"], resultPreview=answer_pack["answer"],
        ok=answer_pack["usableAnswer"], deliveryReady=answer_pack["score"]["deliveryReady"],
        usableAnswer=answer_pack["usableAnswer"], qualityTier=answer_pack["score"]["qualityTier"],
        qualityMetrics=answer_pack["score"]["acceptanceMetrics"],
        recommendedNextAction="use_research_answer_pack" if answer_pack["usableAnswer"] else "report_research_incomplete",
    )
    return bundle



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



def _research_read_failure_is_retryable(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or payload.get("ok") is True:
        return False
    tool_execution = (
        payload.get("toolExecution")
        if isinstance(payload.get("toolExecution"), dict)
        else {}
    )
    status_value = payload.get("status") or tool_execution.get("statusCode")
    try:
        status_code = int(status_value)
    except (TypeError, ValueError):
        status_code = 0
    if status_code in {408, 425, 429} or status_code >= 500:
        return True
    failure_class = _safe_text(
        payload.get("failureClass") or tool_execution.get("failureClass")
    ).lower()
    if failure_class in {
        "deadline_exceeded",
        "network_timeout",
        "connection_error",
        "connection_reset",
        "tls_error",
        "rate_limited",
        "service_unavailable",
    }:
        return True
    failure_text = " ".join(
        _safe_text(value)
        for value in (
            payload.get("error"),
            payload.get("reason"),
            payload.get("missingContentReason"),
            tool_execution.get("error"),
            tool_execution.get("summary"),
        )
        if _safe_text(value)
    ).lower()
    return bool(
        re.search(
            r"\b(?:timed?\s*out|timeout|connection\s+(?:reset|refused|closed)|"
            r"tls|ssl|handshake|temporar(?:y|ily) unavailable|rate\s*limit|too many requests)\b|"
            r"\b(?:408|425|429|5\d\d)\b",
            failure_text,
        )
    )


def _source_router_search(**kwargs: Any) -> str:
    # Keep legacy tests monkeypatchable with a SimpleNamespace(func=...), while
    # production routes through the Source Router rather than the raw web_search
    # primitive.
    if web_search is not None and getattr(web_search, "func", None) and not getattr(web_search, "name", None):
        legacy_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in {
                "total_timeout_seconds",
                "locale_hint",
                "allow_browser_profile_fallback",
                "preferred_providers",
                "excluded_providers",
            }
        }
        return web_search.func(**legacy_kwargs)
    return source_router_search(**kwargs)


def _source_router_read(**kwargs: Any) -> str:
    if web_read is not None and getattr(web_read, "func", None) and not getattr(web_read, "name", None):
        legacy_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key != "timeout_seconds"
        }
        return web_read.func(**legacy_kwargs)
    return source_router_read(**kwargs)


def _is_technical_research_question(question: str) -> bool:
    return bool(
        re.search(
            r"\b(?:api|sdk|framework|library|package|module|next\.js|react|expo|electron|python|typescript)\b|"
            r"(?:软件|开发框架|代码库|程序库|依赖包|接口文档|技术文档)",
            _safe_text(question),
            re.IGNORECASE,
        )
    )


def _research_delivery_requirements(question: str) -> dict[str, Any]:
    text = _safe_text(question)
    explicit_facets = _build_explicit_question_facets(text)
    comparative = bool(
        re.search(
            r"\b(?:compare|comparison|versus|vs\.?|comprehensive|deep research|landscape|market)\b|"
            r"(?:比较|对比|竞品|全面|深入调研|行业格局|市场格局|多方|多个产品)",
            text,
            re.IGNORECASE,
        )
    )
    named_authority_subjects = [
        re.sub(r"\s+", "", value)
        for value in re.findall(r"《([^》]{4,120})》", text)
        if len(re.sub(r"\s+", "", value)) >= 6
    ]
    single_authoritative_subject = bool(
        len(named_authority_subjects) == 1
        and not comparative
        and re.search(
            r"(?:办法|条例|规定|法律|法案|规章|标准|规范|政策|指南|白皮书|报告)|"
            r"\b(?:act|law|regulation|standard|specification|policy|guideline|white paper|report)\b",
            named_authority_subjects[0],
            re.IGNORECASE,
        )
    )
    broad_or_comparative = bool(
        comparative or (explicit_facets and not single_authoritative_subject)
    )
    narrow_technical = _is_technical_research_question(text) and not broad_or_comparative
    explicit_source_floor = 0
    match = re.search(
        r"(?:at\s+least|minimum(?:\s+of)?)\s*(\d+)\s+(?:independent\s+|official\s+|readable\s+)?sources?|"
        r"(?:至少|不少于)\s*(\d+)\s*(?:个|条)?(?:独立|官方|可读)?来源",
        text,
        re.IGNORECASE,
    )
    if match:
        explicit_source_floor = _as_int(match.group(1) or match.group(2), 0)
    narrow_authoritative = bool(narrow_technical or single_authoritative_subject)
    minimum_sources = max(
        explicit_source_floor,
        2 if narrow_authoritative else MIN_RESEARCH_SOURCE_COUNT,
    )
    minimum_hosts = (
        1
        if narrow_authoritative and minimum_sources <= 2
        else min(MIN_RESEARCH_DISTINCT_HOST_COUNT, minimum_sources)
    )
    target_sources = (
        max(minimum_sources, 4 if narrow_technical else 3)
        if narrow_authoritative
        else TARGET_RESEARCH_SOURCE_COUNT
    )
    target_hosts = (
        minimum_hosts if narrow_authoritative
        else TARGET_RESEARCH_DISTINCT_HOST_COUNT
    )
    target_claims = (
        max(
            MIN_RESEARCH_CLAIM_COUNT,
            min(
                TARGET_RESEARCH_CLAIM_COUNT,
                target_sources + (2 if narrow_technical else 3),
            ),
        )
        if narrow_authoritative
        else TARGET_RESEARCH_CLAIM_COUNT
    )
    return {
        "mode": (
            "narrow_authoritative_technical"
            if narrow_technical
            else "single_authoritative_subject"
            if single_authoritative_subject
            else "standard_research"
        ),
        "minimumSources": minimum_sources,
        "minimumDistinctHosts": minimum_hosts,
        "minimumClaims": MIN_RESEARCH_CLAIM_COUNT,
        "minimumAnswerChars": MIN_RESEARCH_ANSWER_CHARS,
        "targetSources": target_sources,
        "targetDistinctHosts": target_hosts,
        "targetClaims": target_claims,
        "targetAnswerChars": 3_000 if narrow_authoritative else TARGET_RESEARCH_ANSWER_CHARS,
        "countPolicy": "advisory",
        "explicitUserSourceCount": explicit_source_floor,
    }



def _run_coro_blocking(coro: Any, *, timeout_seconds: float) -> Any:
    async def _bounded() -> Any:
        return await asyncio.wait_for(coro, timeout=max(0.1, timeout_seconds))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_bounded())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(_bounded()))
        return future.result(timeout=max(timeout_seconds + 1.0, 1.0))



def _catalog_official_entity_hints(question: Any) -> list[dict[str, Any]]:
    """Merge duplicate official hosts into the named entities in the question."""

    question_text = _safe_text(question)
    lowered_question = question_text.lower()
    groups: list[dict[str, Any]] = []
    for hint in _catalog_official_host_hints(question_text):
        aliases = {
            _safe_text(alias).lower()
            for alias in list(hint.get("matchedAliases") or [])
            if _safe_text(alias)
        }
        if not aliases:
            continue
        overlapping = [group for group in groups if aliases.intersection(group["aliases"])]
        if overlapping:
            group = overlapping[0]
            group["aliases"].update(aliases)
            host = _safe_text(hint.get("host"))
            if host and host not in group["hosts"]:
                group["hosts"].append(host)
            for duplicate in overlapping[1:]:
                group["aliases"].update(duplicate["aliases"])
                for duplicate_host in duplicate["hosts"]:
                    if duplicate_host not in group["hosts"]:
                        group["hosts"].append(duplicate_host)
                groups.remove(duplicate)
            continue
        groups.append(
            {
                "aliases": set(aliases),
                "hosts": [_safe_text(hint.get("host"))],
            }
        )

    entity_hints: list[dict[str, Any]] = []
    for group in groups:
        aliases = sorted(group["aliases"], key=lambda alias: (lowered_question.find(alias), -len(alias)))
        label_aliases = [
            alias
            for alias in aliases
            if not any(alias != other and alias in other for other in aliases)
        ]
        rendered_parts: list[str] = []
        for alias in sorted(label_aliases, key=lambda value: lowered_question.find(value)):
            start = lowered_question.find(alias)
            rendered = question_text[start : start + len(alias)] if start >= 0 else alias
            if rendered and rendered.casefold() not in {part.casefold() for part in rendered_parts}:
                rendered_parts.append(rendered)
        hosts = [host for host in group["hosts"] if host]
        if not hosts or not rendered_parts:
            continue
        entity_hints.append(
            {
                "label": " ".join(rendered_parts),
                "host": hosts[0],
                "hosts": hosts,
                "matchedAliases": aliases,
            }
        )

    def alias_spans(alias: str) -> list[tuple[int, int]]:
        if not alias:
            return []
        if re.fullmatch(r"[a-z0-9][a-z0-9 ._+-]*", alias):
            pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
            return [match.span() for match in re.finditer(pattern, lowered_question)]
        return [
            (match.start(), match.end())
            for match in re.finditer(re.escape(alias), lowered_question)
        ]

    filtered: list[dict[str, Any]] = []
    for index, hint in enumerate(entity_hints):
        candidate_spans = [
            span
            for alias in list(hint.get("matchedAliases") or [])
            for span in alias_spans(_safe_text(alias).lower())
        ]
        containing_spans = [
            (start, end)
            for other_index, other in enumerate(entity_hints)
            if other_index != index
            for alias in list(other.get("matchedAliases") or [])
            if len(_safe_text(alias))
            > max([len(_safe_text(value)) for value in list(hint.get("matchedAliases") or [])] or [0])
            for start, end in alias_spans(_safe_text(alias).lower())
        ]
        has_standalone_occurrence = any(
            not any(container_start <= start and end <= container_end for container_start, container_end in containing_spans)
            for start, end in candidate_spans
        )
        if has_standalone_occurrence:
            filtered.append(hint)
    return filtered

def _compact_mcp_text(payload: Any, *, limit: int = 6000) -> str:
    chunks: list[str] = []

    def visit(value: Any, depth: int = 0) -> None:
        if len("\n".join(chunks)) >= limit or depth > 5:
            return
        if isinstance(value, str):
            text = _safe_text(value)
            if text:
                chunks.append(text)
            return
        if isinstance(value, list):
            for item in value[:20]:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        for key in ("text", "markdown", "content", "contents", "result", "docs", "document", "output"):
            if key in value:
                visit(value.get(key), depth + 1)

    visit(payload)
    text = "\n\n".join(dict.fromkeys(chunks))
    return text[:limit]

def _mcp_payload_is_error(payload: Any) -> bool:
    if isinstance(payload, dict):
        if payload.get("isError") is True:
            return True
        return any(_mcp_payload_is_error(value) for value in payload.values())
    if isinstance(payload, list):
        return any(_mcp_payload_is_error(item) for item in payload[:20])
    if isinstance(payload, str):
        lowered = payload.lower()
        return "mcp error" in lowered or "input validation error" in lowered
    return False

def _extract_context7_library_id(payload: Any) -> str:
    text = _compact_mcp_text(payload, limit=3000)
    for pattern in (
        r"(?im)^\s*(/[^/\s]+/[^/\s]+)\s*$",
        r"(?im)Context7-compatible library ID:\s*(`?)(/[^`\s]+)\1",
        r"(?im)\b(/[^/\s]+/[^/\s]+)\b",
    ):
        match = re.search(pattern, text)
        if match:
            return str(match.group(match.lastindex or 1) or "").strip("` ")
    return ""

def _context7_tool_names() -> tuple[str, str, str]:
    for server_name, tools in list((getattr(mcp_manager, "_server_tools", {}) or {}).items()):
        tool_names = [str(getattr(tool, "name", "") or "").strip() for tool in list(tools or [])]
        lowered = {name.lower(): name for name in tool_names}
        server_key = str(server_name or "").lower()
        looks_like_context7 = "context7" in server_key or any("context7" in name.lower() for name in tool_names)
        if not looks_like_context7 and not any(name in lowered for name in ("resolve-library-id", "get-library-docs", "query-docs")):
            continue
        resolve_tool = (
            lowered.get("resolve-library-id")
            or lowered.get("resolve_library_id")
            or next((name for name in tool_names if "resolve" in name.lower() and "library" in name.lower()), "")
        )
        docs_tool = (
            lowered.get("get-library-docs")
            or lowered.get("get_library_docs")
            or lowered.get("query-docs")
            or lowered.get("query_docs")
            or next((name for name in tool_names if ("docs" in name.lower() or "document" in name.lower())), "")
        )
        if docs_tool:
            return str(server_name), resolve_tool, docs_tool
    return "", "", ""


async def _call_context7_source_async(question: str) -> dict[str, Any]:
    await mcp_manager.initialize()
    server_name, resolve_tool, docs_tool = _context7_tool_names()
    if not server_name or not docs_tool:
        return {"ok": False, "error": "context7_mcp_not_available", "attempts": []}
    attempts: list[dict[str, Any]] = []
    library_id = ""
    if resolve_tool:
        entity_labels = [
            _safe_text(item.get("label"))
            for item in sorted(
                _catalog_official_entity_hints(question),
                key=lambda item: len(_safe_text(item.get("label"))),
                reverse=True,
            )
            if _safe_text(item.get("label"))
        ]
        fallback_label = _deterministic_facet_search_query(question, max_chars=80)
        library_names = list(dict.fromkeys([*entity_labels, fallback_label]))[:4]
        scoped_query = (
            _technical_atomic_search_query(question)
            or _deterministic_facet_search_query(question, max_chars=240)
        )
        for library_name in library_names:
            arguments = {
                "query": scoped_query or question[:240],
                "libraryName": library_name,
            }
            try:
                resolved = await mcp_manager.call_tool(
                    server_name=server_name,
                    tool_name=resolve_tool,
                    arguments=arguments,
                )
                is_error = _mcp_payload_is_error(resolved)
                attempts.append({"tool": resolve_tool, "ok": not is_error, "argumentsShape": sorted(arguments)})
                if is_error:
                    continue
                library_id = _extract_context7_library_id(resolved)
                if library_id:
                    break
            except Exception as exc:
                attempts.append({"tool": resolve_tool, "ok": False, "argumentsShape": sorted(arguments), "error": str(exc)[:300]})
    docs_attempts: list[dict[str, Any]] = []
    if library_id:
        docs_attempts.append(
            {
                "libraryId": library_id,
                "query": _technical_atomic_search_query(question)
                or _deterministic_facet_search_query(question, max_chars=240)
                or question[:240],
            }
        )
    else:
        return {"ok": False, "error": "context7_library_id_not_resolved", "serverName": server_name, "attempts": attempts}
    last_error = ""
    for arguments in docs_attempts:
        try:
            docs = await mcp_manager.call_tool(server_name=server_name, tool_name=docs_tool, arguments=arguments)
            text = _compact_mcp_text(docs, limit=_RESEARCH_SOURCE_CAPTURE_CHARS)
            is_error = _mcp_payload_is_error(docs) or "mcp error" in text.lower()
            attempts.append({"tool": docs_tool, "ok": bool(text) and not is_error, "argumentsShape": sorted(arguments)})
            if text and not is_error:
                return {
                    "ok": True,
                    "serverName": server_name,
                    "toolName": docs_tool,
                    "libraryId": library_id,
                    "text": text,
                    "attempts": attempts,
                }
            if is_error:
                last_error = _compact_research_text(text, limit=300) or "context7_docs_error"
        except Exception as exc:
            last_error = str(exc)
            attempts.append({"tool": docs_tool, "ok": False, "argumentsShape": sorted(arguments), "error": last_error[:300]})
    return {"ok": False, "error": last_error or "context7_docs_empty", "serverName": server_name, "attempts": attempts}


def _run_context7_source(question: str, *, tool_call_id: str, timeout_seconds: float = 20) -> dict[str, Any]:
    try:
        payload = _run_coro_blocking(_call_context7_source_async(question), timeout_seconds=timeout_seconds)
    except (asyncio.CancelledError, Exception) as exc:
        return {
            "shardId": "context7_docs",
            "kind": "technical_docs",
            "query": question,
            "ok": False,
            "provider": "context7",
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": [str(exc)[:500] or "context7_failed"],
            "sourceRouter": {"selectedProvider": "context7", "fallbackReason": "context7_exception"},
        }
    if not payload.get("ok"):
        return {
            "shardId": "context7_docs",
            "kind": "technical_docs",
            "query": question,
            "ok": False,
            "provider": "context7",
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": [_safe_text(payload.get("error")) or "context7_unavailable"],
            "providerAttemptMatrix": payload.get("attempts") or [],
            "sourceRouter": {"selectedProvider": "context7", "fallbackReason": "context7_unavailable"},
        }
    original_text = _safe_text(payload.get("text"))
    evidence_query = _technical_atomic_search_query(question) or question
    text = _research_source_excerpt(
        original_text,
        evidence_query,
        limit=_RESEARCH_SOURCE_CAPTURE_CHARS,
    )
    library_id = _safe_text(payload.get("libraryId"))
    url = f"mcp://context7/{library_id.lstrip('/')}" if library_id else "mcp://context7/docs"
    quality = {
        "host": "context7",
        "authorityScore": 92,
        "tier": "primary",
        "authorityTier": "official_docs_mcp",
        "reasons": ["context7_mcp", "official_docs_first"],
    }
    return {
        "shardId": "context7_docs",
        "kind": "technical_docs",
        "query": question,
        "evidenceQuery": evidence_query,
        "ok": True,
        "provider": "context7",
        "networkRoute": "mcp",
        "sourceCapability": "official_technical_docs",
        "sourceRouter": {"selectedProvider": "context7", "networkRoute": "mcp", "sourcePolicy": "official_docs_first"},
        "providerAttemptMatrix": payload.get("attempts") or [],
        "resultCount": 1,
        "results": [
            {
                "resultRank": 1,
                "title": f"Context7 technical docs{f' {library_id}' if library_id else ''}",
                "url": url,
                "finalUrl": url,
                "snippet": text[:600],
                "sourceQualityHints": quality,
            }
        ],
        "fetchedTopSources": [
            {
                "url": url,
                "ok": True,
                "title": f"Context7 technical docs{f' {library_id}' if library_id else ''}",
                "status": "mcp",
                "text": text,
                "textPreview": text[:1200],
                "contentChars": len(text),
                "originalContentChars": len(original_text),
                "retrievedAt": _utc_now_iso(),
                "omittedChars": max(0, len(original_text) - len(text)),
                "evidenceSelection": "query_focused_excerpt",
                "extractionQuality": "context7_mcp_docs",
                "sourceCapability": "official_technical_docs",
            }
        ],
        "errors": [],
    }


def _run_seed_url_shard(
    shard: dict[str, Any],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    source_policy: str,
    use_agent_browser_profile: bool,
    tool_call_id: str,
    read_attempt_ledger: _ResearchReadAttemptLedger | None = None,
    read_round: int = 1,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Read an explicit seed URL before depending on a search provider."""

    url = _safe_text(shard.get("seedUrl") or shard.get("query"))
    if cancel_event is not None and cancel_event.is_set():
        return {
            **shard,
            "ok": False,
            "provider": "explicit_seed_url",
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["research_shard_cancelled"],
        }
    host = _host(url)
    if blocked_domains and any(host == domain or host.endswith(f".{domain}") for domain in blocked_domains):
        return {
            **shard,
            "ok": False,
            "provider": "explicit_seed_url",
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["seed_url_blocked_by_domain_policy"],
        }
    if allowed_domains and not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        return {
            **shard,
            "ok": False,
            "provider": "explicit_seed_url",
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["seed_url_outside_allowed_domains"],
        }
    if (
        read_attempt_ledger is not None
        and not read_attempt_ledger.has_record(url)
        and read_attempt_ledger.host_circuit_open(url, register_skip=True)
    ):
        return {
            **shard,
            "ok": False,
            "provider": "explicit_seed_url",
            "failureClass": "source_host_circuit_open",
            "sourceHost": host,
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["source_host_circuit_open"],
        }

    read_claim = (
        read_attempt_ledger.claim(url, round_index=read_round)
        if read_attempt_ledger is not None
        else ""
    )
    cached_read: dict[str, Any] | None = None
    if read_attempt_ledger is not None and not read_claim:
        cached_read = read_attempt_ledger.cached_payload(
            url,
            wait_seconds=_RESEARCH_SOURCE_READ_DEADLINE_MS / 1000.0,
        )
        if cached_read is None:
            return {
                **shard,
                "ok": False,
                "provider": "explicit_seed_url",
                "resultCount": 0,
                "results": [],
                "fetchedTopSources": [],
                "errors": ["source_read_deduplicated"],
            }

    if cached_read is not None:
        read_payload = dict(cached_read.get("readPayload") or {})
        original_text = _safe_text(cached_read.get("originalText"))
    else:
        read_payload = _parse_tool_json(
            _source_router_read(
                url=url,
                mode="auto" if use_agent_browser_profile else "static",
                headless=True,
                referer_mode="none",
                referer_url="",
                maxTextChars=_RESEARCH_SOURCE_READ_CHARS,
                # Let the fetcher auto-promote only allowlisted hosts to the
                # login-backed profile; search results may point at unrelated
                # public domains and must remain readable without that profile.
                useAgentBrowserProfile=False,
                tool_call_id=tool_call_id,
                timeout_seconds=_RESEARCH_SOURCE_READ_DEADLINE_MS / 1000.0,
            )
        )
        original_text = _safe_text(
            read_payload.get("text")
            or read_payload.get("markdown")
            or read_payload.get("textPreview")
        )
    if cancel_event is not None and cancel_event.is_set():
        if read_attempt_ledger is not None:
            read_attempt_ledger.finish(read_claim, retryable=False)
        return {
            **shard,
            "ok": False,
            "provider": "explicit_seed_url",
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["research_shard_cancelled"],
        }
    evidence_query = _safe_text(shard.get("evidenceQuery") or shard.get("reason") or shard.get("query"))
    original_content_chars = _research_original_content_chars(original_text, read_payload)
    text = _research_source_excerpt(original_text, evidence_query, limit=_RESEARCH_SOURCE_CAPTURE_CHARS)
    title = _safe_text(read_payload.get("title"))[:300] or url
    quality = _source_quality(
        url,
        allowed_domains=allowed_domains,
        source_policy=source_policy,
        title=title,
        snippet=text[:600],
    )
    result = {
        "resultRank": 1,
        "title": title,
        "url": url,
        "finalUrl": _safe_text(read_payload.get("finalUrl")) or url,
        "snippet": text[:600],
        "sourceQualityHints": quality,
        "publishedAt": read_payload.get("publishedAt"),
        "updatedAt": read_payload.get("updatedAt"),
        "version": read_payload.get("version"),
    }
    temporal = _source_temporal_evidence(read_payload, result)
    fetched = {
        "url": url,
        "ok": bool(read_payload.get("ok") and text),
        "title": title,
        "status": read_payload.get("status"),
        "finalUrl": _safe_text(read_payload.get("finalUrl")) or url,
        "text": text,
        "textPreview": text[:1200],
        "contentChars": len(text),
        "originalContentChars": original_content_chars,
        "links": read_payload.get("links") or [],
        "metadata": read_payload.get("metadata") if isinstance(read_payload.get("metadata"), dict) else {},
        "retrievedAt": temporal.get("retrievedAt"),
        "publishedAt": temporal.get("publishedAt"),
        "updatedAt": temporal.get("updatedAt"),
        "version": temporal.get("version"),
        "temporalEvidence": temporal,
        "omittedChars": max(0, original_content_chars - len(text)),
        "evidenceSelection": "query_focused_excerpt",
        "extractionQuality": read_payload.get("extractionQuality") or ("readable" if text else "unreadable"),
        "sourceCapability": read_payload.get("sourceCapability"),
        "providerAttemptMatrix": read_payload.get("providerAttemptMatrix") or read_payload.get("attemptedProviders") or [],
        "rawRef": read_payload.get("rawRef") or read_payload.get("detailRawRef"),
        "missingContentReason": read_payload.get("missingContentReason"),
        "warnings": read_payload.get("warnings") if isinstance(read_payload.get("warnings"), list) else [],
        "failureClass": read_payload.get("failureClass") or (read_payload.get("toolExecution") or {}).get("failureClass"),
        "toolExecution": read_payload.get("toolExecution"),
        "readReuse": "shared_document_cache" if cached_read is not None else None,
    }
    ok = bool(read_payload.get("ok") and text)
    if read_attempt_ledger is not None:
        read_attempt_ledger.finish(
            read_claim,
            final_url=read_payload.get("finalUrl") or url,
            retryable=bool(
                not ok and _research_read_failure_is_retryable(read_payload)
            ),
            succeeded=ok,
            cache_payload=(
                {
                    "readPayload": read_payload,
                    "originalText": original_text,
                }
                if ok and cached_read is None
                else None
            ),
        )
    return {
        **shard,
        "ok": ok,
        "provider": "explicit_seed_url",
        "networkRoute": "direct_read",
        "sourceCapability": read_payload.get("sourceCapability") or "seed_url_read",
        "sourceRouter": {"selectedProvider": "explicit_seed_url", "networkRoute": "direct_read", "sourcePolicy": source_policy},
        "providerAttemptMatrix": read_payload.get("providerAttemptMatrix") or read_payload.get("attemptedProviders") or [],
        "resultCount": 1,
        "results": [result],
        "fetchedTopSources": [fetched],
        "errors": [] if ok else [_safe_text(read_payload.get("error")) or "seed_url_unreadable"],
    }


def _run_search_shard(
    shard: dict[str, Any],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    source_policy: str,
    max_rounds: int,
    use_agent_browser_profile: bool,
    tool_call_id: str,
    claimed_read_urls: set[str] | None = None,
    claimed_read_urls_lock: threading.Lock | None = None,
    read_attempt_ledger: _ResearchReadAttemptLedger | None = None,
    read_round: int = 1,
    cancel_event: threading.Event | None = None,
    preferred_language: str = "",
    excluded_search_providers: tuple[str, ...] = (),
    search_provider_attempt: int = 1,
    shard_deadline_at: float | None = None,
) -> dict[str, Any]:
    shard_deadline_at = (
        float(shard_deadline_at)
        if shard_deadline_at is not None
        else time.monotonic() + (_RESEARCH_SHARD_DEADLINE_MS / 1000.0)
    )

    def remaining_shard_ms() -> int:
        return max(0, int((shard_deadline_at - time.monotonic()) * 1000))

    if cancel_event is not None and cancel_event.is_set():
        return {
            **shard,
            "ok": False,
            "provider": None,
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["research_shard_cancelled"],
        }
    seed_url = _safe_text(shard.get("seedUrl"))
    if str(shard.get("kind") or "").strip() == "seed_url" and seed_url:
        return _run_seed_url_shard(
            shard,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            source_policy=source_policy,
            use_agent_browser_profile=use_agent_browser_profile,
            tool_call_id=tool_call_id,
            read_attempt_ledger=read_attempt_ledger,
            read_round=read_round,
            cancel_event=cancel_event,
        )
    query = _safe_text(shard.get("query"))
    source_intent = _safe_text(shard.get("sourceIntent")).lower()
    site_domains = _query_site_domains(query)
    video_research = _is_video_research(query, source_policy, shard.get("kind"))
    search_route_hints = (
        read_attempt_ledger.search_route_hints()
        if read_attempt_ledger is not None
        else {"preferredProviders": [], "excludedProviders": []}
    )
    try:
        search_payload = _parse_tool_json(
            _source_router_search(
                query=query,
                limit=8,
                search_engine=_safe_text(shard.get("searchEngine")) or "auto",
                mode=("dynamic" if shard.get("fetchMode") == "dynamic" else
                      "auto" if use_agent_browser_profile else "static"),
                referer_mode="none",
                referer_url="",
                # The router promotes only providers whose search host is
                # allowlisted. Passing true here would force every fallback
                # (including public Bing results) through that profile.
                useAgentBrowserProfile=False,
                tool_call_id=tool_call_id,
                total_timeout_seconds=min(
                    _RESEARCH_SEARCH_DEADLINE_MS,
                    remaining_shard_ms(),
                ) / 1000.0,
                locale_hint=preferred_language,
                allow_browser_profile_fallback=use_agent_browser_profile,
                preferred_providers=(search_route_hints.get("preferredProviders") or []) if shard.get("searchEngine", "auto") == "auto" else [],
                excluded_providers=list(
                    dict.fromkeys(
                        [
                            *list(search_route_hints.get("excludedProviders") or []),
                            *[
                                _safe_text(provider).lower()
                                for provider in excluded_search_providers
                                if _safe_text(provider)
                            ],
                        ]
                    )
                ),
            )
        )
    except Exception as exc:
        search_payload = _deadline_failure(
            tool_name="source_router_search",
            family="research",
            deadline_ms=_RESEARCH_SEARCH_DEADLINE_MS,
            summary="Source Router search failed.",
            failure_class=classify_failure(exc),
            error=str(exc),
            recommended_next_action="换关键词、限定权威域名，或保留该 shard 为 failed_source。",
        )
    if read_attempt_ledger is not None:
        read_attempt_ledger.record_search_payload(search_payload)
    if cancel_event is not None and cancel_event.is_set():
        return {
            **shard,
            "ok": False,
            "provider": None,
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": ["research_shard_cancelled"],
        }
    if search_payload.get("kind") == "tool_deadline_envelope":
        return {
            **shard,
            "ok": False,
            "provider": None,
            "failureClass": search_payload.get("failureClass") or "deadline_exceeded",
            "retryable": bool(search_payload.get("retryable")),
            "elapsedMs": search_payload.get("elapsedMs"),
            "resultCount": 0,
            "results": [],
            "fetchedTopSources": [],
            "errors": [_safe_text(search_payload.get("error")) or "search_deadline_exceeded"],
            "toolExecution": search_payload.get("toolExecution"),
        }
    raw_results = search_payload.get("results") if isinstance(search_payload.get("results"), list) else []
    from core.tools.web_chat_source import captured_chat_source
    captured_results, captured_reads = captured_chat_source(search_payload,
        allowed_domains=allowed_domains, blocked_domains=blocked_domains,
        site_domains=site_domains, source_intent=source_intent)
    results: list[dict[str, Any]] = list(captured_results)
    read_eligible_urls: set[str] = set()
    domestic_site_mirror_fallback = bool(
        site_domains
        and _safe_text(search_payload.get("provider")).lower() == "bing_cn"
        and _safe_text(search_payload.get("networkRoute")).lower() == "cn_direct"
    )
    for index, result in enumerate(raw_results, start=1):
        url = _safe_text((result or {}).get("url"))
        host = _host(url)
        if blocked_domains and any(host == domain or host.endswith(f".{domain}") for domain in blocked_domains):
            continue
        if allowed_domains and not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
            continue
        site_domain_match = not site_domains or _host_matches_domains(host, site_domains)
        site_constraint_relaxed = bool(domestic_site_mirror_fallback and not site_domain_match)
        if not site_domain_match and not site_constraint_relaxed:
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
            question=query,
        )
        discovery_relevance = _source_relevance_score(
            query,
            title=title,
            snippet=snippet,
        )
        effective_source_intent = "mixed" if site_constraint_relaxed else source_intent
        # Search titles/snippets are ranking hints, not evidence gates. Reading
        # only candidates whose short snippet already proves the full subject
        # starves harsh-network runs even when an alternate result has a useful
        # body. The post-read quality gate below remains authoritative.
        if (
            (site_domains or _source_matches_intent(quality, effective_source_intent))
            and not research_source_is_navigation(url, title=title)
        ):
            read_eligible_urls.add(url)
        results.append(
            {
                "resultRank": index,
                "title": title,
                "url": url,
                "finalUrl": url,
                "snippet": snippet,
                "discoveryRelevanceScore": discovery_relevance,
                "sourceQualityHints": quality,
                "publishedAt": (result or {}).get("publishedAt") or (result or {}).get("publishedDate"),
                "updatedAt": (result or {}).get("updatedAt") or (result or {}).get("updatedDate"),
                "version": (result or {}).get("version"),
                "sourceIntent": effective_source_intent or None,
                "requestedSourceIntent": source_intent or None,
                "siteConstraintRelaxed": site_constraint_relaxed,
                "siteConstraintDomains": site_domains if site_constraint_relaxed else [],
                "readSelectionReason": ("eager_read_budget_or_ranking" if url in read_eligible_urls
                                        else "navigation_candidate" if research_source_is_navigation(url, title=title)
                                        else "source_intent_hint"),
            }
        )
    top_results = sorted(
        results,
        key=lambda item: (
            int(item.get("discoveryRelevanceScore") or 0),
            int((item.get("sourceQualityHints") or {}).get("authorityScore") or 0),
            _research_document_priority(_safe_text(item.get("url")), question=query),
        ),
        reverse=True,
    )
    fetched: list[dict[str, Any]] = list(captured_reads)
    circuit_open_hosts: set[str] = set()
    accepted_read_count = 0
    accepted_evidence_count = len(captured_reads)
    for result in top_results:
            if cancel_event is not None and cancel_event.is_set():
                break
            remaining_ms = remaining_shard_ms()
            if remaining_ms < 1_000:
                break
            if (
                len(fetched)
                >= DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.max_read_attempts_per_shard
                or accepted_read_count
                >= DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.target_accepted_reads_per_shard
            ):
                break
            url = _safe_text(result.get("url"))
            if not url:
                continue
            if url not in read_eligible_urls:
                continue
            if (
                read_attempt_ledger is not None
                and not read_attempt_ledger.has_record(url)
                and read_attempt_ledger.host_circuit_open(url, register_skip=True)
            ):
                source_host = _host(url)
                if source_host:
                    circuit_open_hosts.add(source_host)
                continue
            source_identity = _research_document_identity(url, question=query)
            read_claim = ""
            cached_read: dict[str, Any] | None = None
            if read_attempt_ledger is not None:
                read_claim = read_attempt_ledger.claim(url, round_index=read_round)
                if not read_claim:
                    cached_read = read_attempt_ledger.cached_payload(
                        url,
                        wait_seconds=min(
                            DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.duplicate_cache_wait_seconds,
                            remaining_ms / 1000.0,
                        ),
                    )
                    if cached_read is None:
                        continue
            elif claimed_read_urls is not None and claimed_read_urls_lock is not None:
                with claimed_read_urls_lock:
                    if source_identity in claimed_read_urls:
                        continue
                    claimed_read_urls.add(source_identity)
            if cached_read is not None:
                read_payload = dict(cached_read.get("readPayload") or {})
                original_text = _safe_text(cached_read.get("originalText"))
            else:
                read_timeout_ms = min(
                    _RESEARCH_SOURCE_READ_DEADLINE_MS,
                    remaining_shard_ms(),
                )
                if read_timeout_ms < 1_000:
                    if read_attempt_ledger is not None:
                        read_attempt_ledger.finish(
                            read_claim,
                            retryable=True,
                            record_host_failure=False,
                        )
                    break
                read_payload = _parse_tool_json(
                    _source_router_read(
                        url=url,
                        mode="auto" if use_agent_browser_profile else "static",
                        headless=True,
                        referer_mode="none",
                        referer_url="",
                        maxTextChars=_RESEARCH_SOURCE_READ_CHARS,
                        # See the seed read above: profile use is opportunistic
                        # for allowlisted hosts, never a blanket requirement for
                        # every result URL in a research shard.
                        useAgentBrowserProfile=False,
                        tool_call_id=tool_call_id,
                        timeout_seconds=read_timeout_ms / 1000.0,
                    )
                )
                original_text = _safe_text(
                    read_payload.get("text")
                    or read_payload.get("markdown")
                    or read_payload.get("textPreview")
                )
            if cancel_event is not None and cancel_event.is_set():
                if read_attempt_ledger is not None:
                    read_attempt_ledger.finish(read_claim, retryable=False)
                break
            text = original_text
            read_attempts: list[dict[str, Any]] = []
            if read_payload.get("providerAttemptMatrix"):
                for item in list(read_payload.get("providerAttemptMatrix") or [])[:6]:
                    if isinstance(item, dict):
                        read_attempts.append(item)
            elif read_payload.get("toolExecution"):
                read_attempts.append({"provider": "builtin_scrapling", "status": "error", "failureClass": (read_payload.get("toolExecution") or {}).get("failureClass")})
            else:
                read_attempts.append({"provider": "builtin_scrapling", "status": "success" if read_payload.get("ok") else "error"})
            needs_jina_fallback = bool(not read_payload.get("ok") or not text or _source_noise_reasons(text))
            if needs_jina_fallback and _jina_api_key():
                jina_payload = _read_with_jina(url)
                read_attempts.append(
                    {
                        "provider": "jina",
                        "status": "success" if jina_payload.get("ok") else "error",
                        "failureClass": jina_payload.get("failureClass"),
                        "reason": jina_payload.get("reason"),
                    }
                )
                jina_text = _safe_text(jina_payload.get("text") or jina_payload.get("textPreview"))
                if jina_payload.get("ok") and jina_text and not _source_noise_reasons(jina_text):
                    read_payload = {
                        **read_payload,
                        **jina_payload,
                        "title": _safe_text(read_payload.get("title")) or _safe_text(jina_payload.get("title")),
                        "providerAttemptMatrix": read_attempts,
                    }
                    original_text = jina_text
                    text = jina_text
            elif needs_jina_fallback:
                read_attempts.append(
                    {
                        "provider": "jina",
                        "status": "skipped",
                        "failureClass": "credential_missing",
                        "reason": "missing_env:JINA_API_KEY",
                    }
                )
            if cancel_event is not None and cancel_event.is_set():
                if read_attempt_ledger is not None:
                    read_attempt_ledger.finish(read_claim, retryable=False)
                break
            extraction_quality = read_payload.get("extractionQuality")
            if not extraction_quality:
                extraction_quality = "readable" if read_payload.get("ok") and text else "unreadable"
            temporal = _source_temporal_evidence(read_payload, result)
            original_content_chars = _research_original_content_chars(original_text, read_payload)
            text = _research_source_excerpt(original_text, query, limit=_RESEARCH_SOURCE_CAPTURE_CHARS)
            final_url = _safe_text(read_payload.get("finalUrl")) or url
            if read_attempt_ledger is not None:
                read_attempt_ledger.finish(
                    read_claim,
                    final_url=final_url,
                    retryable=bool(
                        not (read_payload.get("ok") and text)
                        and _research_read_failure_is_retryable(read_payload)
                    ),
                    succeeded=bool(read_payload.get("ok") and text),
                    cache_payload=(
                        {
                            "readPayload": read_payload,
                            "originalText": original_text,
                        }
                        if read_payload.get("ok") and original_text and cached_read is None
                        else None
                    ),
                )
            final_host = _host(final_url)
            final_site_domain_match = not site_domains or _host_matches_domains(final_host, site_domains)
            if not final_site_domain_match and not result.get("siteConstraintRelaxed"):
                continue
            final_quality = _source_quality(
                final_url,
                allowed_domains=allowed_domains,
                source_policy=source_policy,
                title=_safe_text(read_payload.get("title") or result.get("title"))[:300],
                snippet="\n".join(
                    part
                    for part in (_safe_text(result.get("snippet")), text[:600])
                    if part
                ),
                video_research=video_research,
                question=query,
            )
            if result.get("siteConstraintRelaxed") and not final_site_domain_match:
                final_quality = {
                    **final_quality,
                    "reasons": [
                        *list(final_quality.get("reasons") or []),
                        "domestic_mirror_for_unreachable_site_constraint",
                    ],
                    "siteConstraintRelaxed": True,
                    "siteConstraintDomains": list(result.get("siteConstraintDomains") or []),
                }
            result["sourceQualityHints"] = final_quality
            if not _source_matches_intent(final_quality, result.get("sourceIntent")):
                continue
            result["finalUrl"] = final_url
            fetched.append(
                {
                    "url": url,
                    "finalUrl": final_url,
                    "ok": bool(read_payload.get("ok")),
                    "title": _safe_text(read_payload.get("title"))[:300],
                    "status": read_payload.get("status"),
                    "text": text,
                    "textPreview": text[:1200],
                    "contentChars": len(text),
                    "originalContentChars": original_content_chars,
                    "links": read_payload.get("links") or [],
                    "metadata": read_payload.get("metadata") if isinstance(read_payload.get("metadata"), dict) else {},
                    "retrievedAt": temporal.get("retrievedAt"),
                    "publishedAt": temporal.get("publishedAt"),
                    "updatedAt": temporal.get("updatedAt"),
                    "version": temporal.get("version"),
                    "temporalEvidence": temporal,
                    "omittedChars": max(0, original_content_chars - len(text)),
                    "evidenceSelection": "query_focused_excerpt",
                    "extractionQuality": extraction_quality,
                    "sourceCapability": read_payload.get("sourceCapability"),
                    "providerAttemptMatrix": read_payload.get("providerAttemptMatrix") or read_payload.get("attemptedProviders") or read_attempts,
                    "rawRef": read_payload.get("rawRef") or read_payload.get("detailRawRef"),
                    "missingContentReason": read_payload.get("missingContentReason"),
                    "warnings": read_payload.get("warnings") if isinstance(read_payload.get("warnings"), list) else [],
                    "failureClass": read_payload.get("failureClass") or (read_payload.get("toolExecution") or {}).get("failureClass"),
                    "toolExecution": read_payload.get("toolExecution"),
                    "readReuse": "shared_document_cache" if cached_read is not None else None,
                }
            )
            # A cached projection gives this facet evidence but does not add a
            # new independent document. Keep the shard's two-source network
            # budget available for additional corroboration.
            if (
                fetched[-1].get("ok") is True
                and not fetched[-1].get("missingContentReason")
                and bool(text)
            ):
                accepted_evidence_count += 1
                if cached_read is None:
                    accepted_read_count += 1
    selected_provider = _safe_text(search_payload.get("provider")).lower()
    if read_attempt_ledger is not None and selected_provider and results:
        read_attempt_ledger.record_search_evidence_outcome(
            selected_provider,
            accepted_evidence_count=accepted_evidence_count,
        )
    minimum_alternate_budget_ms = int(
        DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.min_alternate_provider_budget_seconds
        * 1000
    )
    if (
        accepted_evidence_count == 0
        and shard.get("searchEngine", "auto") == "auto"
        and search_payload.get("ok") is True
        and selected_provider
        and bool(results)
        and search_provider_attempt
        < DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.max_search_providers_per_shard
        and remaining_shard_ms() >= minimum_alternate_budget_ms
        and (cancel_event is None or not cancel_event.is_set())
        and (
            read_attempt_ledger is None
            or read_attempt_ledger.claim_alternate_provider_attempt(
                limit=DEFAULT_RESEARCH_ACQUISITION_SCHEDULE.max_alternate_provider_attempts_per_run
            )
        )
    ):
        alternate = _run_search_shard(
            shard,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            source_policy=source_policy,
            max_rounds=max_rounds,
            use_agent_browser_profile=use_agent_browser_profile,
            tool_call_id=tool_call_id,
            claimed_read_urls=claimed_read_urls,
            claimed_read_urls_lock=claimed_read_urls_lock,
            read_attempt_ledger=read_attempt_ledger,
            read_round=read_round,
            cancel_event=cancel_event,
            preferred_language=preferred_language,
            excluded_search_providers=tuple(
                dict.fromkeys([*excluded_search_providers, selected_provider])
            ),
            search_provider_attempt=search_provider_attempt + 1,
            shard_deadline_at=shard_deadline_at,
        )
        first_attempts = list(
            search_payload.get("providerAttemptMatrix")
            or search_payload.get("attemptedProviders")
            or []
        )
        alternate_attempts = list(alternate.get("providerAttemptMatrix") or [])
        alternate["providerAttemptMatrix"] = [
            *first_attempts,
            *alternate_attempts,
        ][:32]
        alternate["alternateProviderAttempted"] = True
        alternate["discardedSearchProviders"] = list(
            dict.fromkeys(
                [
                    *list(alternate.get("discardedSearchProviders") or []),
                    selected_provider,
                ]
            )
        )[:8]
        alternate["searchProviderAttempts"] = search_provider_attempt + 1
        return alternate

    return {
        **shard,
        "ok": bool(search_payload.get("ok")),
        "provider": search_payload.get("provider"),
        "failureClass": search_payload.get("failureClass"),
        "retryable": search_payload.get("retryable"),
        "elapsedMs": search_payload.get("elapsedMs"),
        "networkRoute": search_payload.get("networkRoute"),
        "sourceCapability": search_payload.get("sourceCapability"),
        "sourceRouter": search_payload.get("sourceRouter"),
        "sourceIntent": source_intent or None,
        "siteDomains": site_domains,
        "providerAttemptMatrix": search_payload.get("providerAttemptMatrix") or search_payload.get("attemptedProviders"),
        "resultCount": len(results),
        "results": top_results,
        "fetchedTopSources": fetched,
        "sourceHostCircuitOpen": sorted(circuit_open_hosts),
        "searchProviderAttempts": search_provider_attempt,
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
    already_read_urls: set[str] | None = None,
    read_attempt_ledger: _ResearchReadAttemptLedger | None = None,
    read_round: int = 1,
    preferred_language: str = "",
    deadline_seconds: float | None = None,
    max_parallel_shards: int | None = None,
) -> list[dict[str, Any]]:
    if not shards:
        return []
    completed: list[dict[str, Any] | None] = [None] * len(shards)
    claimed_read_urls = already_read_urls if already_read_urls is not None else set()
    claimed_read_urls_lock = threading.Lock()
    attempt_ledger = read_attempt_ledger or _ResearchReadAttemptLedger(
        terminal_identities=set(claimed_read_urls),
    )
    if read_attempt_ledger is not None:
        for identity in set(claimed_read_urls):
            attempt_ledger.mark_terminal(identity, already_identity=True)
    cancel_event = threading.Event()
    timed_out = False
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(len(shards), _RESEARCH_MAX_PARALLEL_SEARCH_SHARDS,
                               max_parallel_shards or _RESEARCH_MAX_PARALLEL_SEARCH_SHARDS))
    )
    try:
        futures: dict[Any, int] = {}
        for index, shard in enumerate(shards):
            query = _safe_text(shard.get("evidenceQuery") or shard.get("query"))
            seed_url = _safe_text(shard.get("seedUrl"))
            host = _host(seed_url) if seed_url else ""
            summary = (
                f"正在读取 {host}"
                if host
                else f"正在搜索：{query[:96]}"
                if query
                else "正在搜索可用来源"
            )
            _report_research_progress(
                stage="source_search",
                status="active",
                summary=summary,
                toolName="web_read" if host else "web_search",
                nodeId=f"research-search:{read_round}:{index + 1}",
            )
            future = executor.submit(
                _run_search_shard,
                shard,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
                source_policy=source_policy,
                max_rounds=max_rounds,
                use_agent_browser_profile=bool(use_agent_browser_profile),
                tool_call_id=tool_call_id,
                claimed_read_urls=claimed_read_urls,
                claimed_read_urls_lock=claimed_read_urls_lock,
                read_attempt_ledger=attempt_ledger,
                read_round=read_round,
                cancel_event=cancel_event,
                preferred_language=preferred_language,
            )
            futures[future] = index
        try:
            for future in as_completed(futures, timeout=max(0.1, min(
                _RESEARCH_TOOL_DEADLINE_MS / 1000.0,
                deadline_seconds if deadline_seconds is not None else _RESEARCH_TOOL_DEADLINE_MS / 1000.0,
            ))):
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
                completed_shard = completed[index] or {}
                readable = [
                    dict(item)
                    for item in list(completed_shard.get("fetchedTopSources") or [])
                    if isinstance(item, dict)
                    and item.get("ok") is True
                    and not item.get("missingContentReason")
                    and _safe_text(item.get("text") or item.get("markdown") or item.get("textPreview"))
                ]
                if readable:
                    for source_index, source in enumerate(readable[:3], start=1):
                        source_url = _safe_text(source.get("finalUrl") or source.get("url"))
                        source_host = _host(source_url)
                        source_title = _safe_text(source.get("title"))[:88]
                        _report_research_progress(
                            stage="source_read",
                            status="completed",
                            summary=f"已读取 {source_title or source_host or '一个来源'}",
                            toolName="web_read",
                            nodeId=f"research-read:{read_round}:{index + 1}:{source_index}",
                        )
                _report_research_progress(
                    stage="source_search",
                    status="completed" if completed_shard.get("ok") else "failed",
                    summary=f"本轮检索结束，已读取 {len(readable)} 个来源" if readable else "本轮检索结束，未读到可用正文",
                    toolName="web_read" if shard.get("seedUrl") else "web_search",
                    nodeId=f"research-search:{read_round}:{index + 1}",
                )
        except TimeoutError:
            timed_out = True
            cancel_event.set()
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
                        summary="Research shard exceeded the parallel search-batch deadline.",
                        failure_class="deadline_exceeded",
                        error="research_broker parallel search-batch deadline exceeded",
                        recommended_next_action="Use partial evidence, narrow the query, or run another focused research pass.",
                    ).get("toolExecution"),
                }
                _report_research_progress(
                    stage="source_search",
                    status="failed",
                    summary="来源检索超过本轮时限",
                    toolName="web_search",
                    nodeId=f"research-search:{read_round}:{index + 1}",
                )
    finally:
        if timed_out:
            cancel_event.set()
        executor.shutdown(wait=not timed_out, cancel_futures=True)
    return [item for item in completed if item is not None]



_RESEARCH_RUNTIME_DIAGNOSTIC_GAP_PATTERNS = (
    re.compile(r"research should continue until at least \d+ readable sources", re.IGNORECASE),
    re.compile(r"research needs at least \d+ readable sources", re.IGNORECASE),
    re.compile(r"architect-answerable (?:source|independent host) minimum not met", re.IGNORECASE),
    re.compile(r"no source-backed claims were extracted from readable page bodies", re.IGNORECASE),
    re.compile(r"(?:answer|evidence|source|host|claim|coverage)_floor_not_met", re.IGNORECASE),
    re.compile(
        r"(?:architect|independent)_review_not_accepted|research_(?:brief_coverage_incomplete|delivery_gate_not_ready|source_transport_exhausted)",
        re.IGNORECASE,
    ),
)


def _research_gap_is_runtime_diagnostic(value: Any) -> bool:
    text = re.sub(r"\s+", " ", _safe_text(value)).strip()
    return bool(text) and any(pattern.search(text) for pattern in _RESEARCH_RUNTIME_DIAGNOSTIC_GAP_PATTERNS)



def _normalize_research_search_query(value: Any) -> str:
    query = re.sub(r"\s+", " ", _safe_text(value)).strip()
    if not query:
        return ""
    if _research_gap_is_runtime_diagnostic(query):
        return ""
    # A combined runtime brief is useful planning material, but it is not one
    # literal search-engine query.  When a model echoes the whole multi-facet
    # brief as a repair query, drop it so the existing per-facet deterministic
    # repair rows can take over.
    if len(re.findall(r"\[[a-z0-9][a-z0-9_-]{1,79}\]", query, re.IGNORECASE)) >= 2:
        return ""
    if re.search(
        r"\bresearch every item below\b|\bcover each item explicitly\b|"
        r"\bdeliverable requirements?\b",
        query,
        re.IGNORECASE,
    ):
        return ""
    query = re.sub(
        r"^(?:please\s+)?(?:fetch|retrieve|read|open|visit|look\s+up|search(?:\s+for)?|find|locate|obtain|consult)\s*[:：-]?\s*",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"^(?:请)?(?:获取|抓取|读取|打开|访问|查找|搜索|检索|查询)\s*[:：-]?\s*", "", query)
    for stale_host, current_host in dict(
        _source_catalog().get("hostQueryRewrites") or {}
    ).items():
        query = re.sub(
            rf"(?<![a-z0-9.-])site:{re.escape(stale_host)}(?=\s|$)",
            f"site:{current_host}",
            query,
            flags=re.IGNORECASE,
        )
    return _deterministic_facet_search_query(query, max_chars=280).strip(" \t\r\n-:：")


def _build_question_facet_queries(question: str) -> list[tuple[str, str]]:
    structured_facets = _build_explicit_question_facets(question)
    if structured_facets:
        return structured_facets

    lowered = _safe_text(question).lower()
    subject = _research_query_subject(question)
    cli_request = bool(
        re.search(r"\bcli\b", lowered)
        or "command-line" in lowered
        or "command line" in lowered
        or "命令行" in lowered
    )
    pathlib_request = "pathlib" in lowered or "path object" in lowered or "文件路径" in lowered
    if cli_request and pathlib_request:
        return [
            (
                f"site:docs.python.org/3/library/argparse.html {subject} argparse type pathlib.Path",
                "facet_cli_parser",
            ),
            (
                f"site:click.palletsprojects.com/en/stable/parameter-types {subject} click.Path path_type resolve_path",
                "facet_cli_framework",
            ),
            (
                f"site:typer.tiangolo.com pathlib Path command line parameter type",
                "facet_cli_typer",
            ),
            (
                f"site:docs.python.org/3/library/pathlib.html {subject} Path filesystem semantics",
                "facet_pathlib_api",
            ),
        ]
    return []


def _build_explicit_question_facets(question: str) -> list[tuple[str, str]]:
    """Return only user/runtime-declared ``[facet-id]`` brief lines.

    ``_build_question_facet_queries`` also knows a few deterministic discovery
    expansions for ordinary questions.  Those are useful search hints, but they
    are not an explicit multi-brief contract and therefore must not trigger the
    Architect query planner, per-brief delivery gate, or structured repair path.
    """
    structured_facets: list[tuple[str, str]] = []
    seen_facet_queries: set[str] = set()
    for line in _safe_text(question).splitlines():
        match = re.match(
            r"^\s*(?:\d+[.)]|[-*])\s*\[([^\]\r\n]{1,80})\]\s*(.+?)\s*$",
            line,
        )
        if not match:
            continue
        facet_id = re.sub(r"[^a-z0-9_-]+", "-", match.group(1).strip().lower()).strip("-")
        facet_query = _safe_text(match.group(2))
        normalized = facet_query.casefold()
        if not facet_id or len(facet_query) < 8 or normalized in seen_facet_queries:
            continue
        seen_facet_queries.add(normalized)
        structured_facets.append((facet_query, f"facet:{facet_id[:64]}"))
    return structured_facets



def _research_facet_id(shard_kind: Any) -> str:
    kind = _safe_text(shard_kind)
    if kind.startswith("facet:"):
        return kind.split(":", 1)[1]
    return ""



def _research_authority_query_budget(facet_count: int) -> int:
    if facet_count < 2:
        return 0
    return max(2, min(5, (int(facet_count) + 2) // 3))



def _research_query_subject(question: str) -> str:
    generic_terms = {
        "about",
        "are",
        "best",
        "cite",
        "current",
        "documentation",
        "evidence",
        "for",
        "latest",
        "only",
        "official",
        "answer",
        "chinese",
        "core",
        "directly",
        "practice",
        "practices",
        "explain",
        "pattern",
        "please",
        "return",
        "simplified",
        "source",
        "sources",
        "the",
        "tools",
        "usable",
        "using",
        "what",
        "with",
    }
    terms: list[str] = []
    for term in re.split(r"[^a-z0-9_.+-]+", _safe_text(question).lower()):
        normalized = term.strip("._+-")
        if len(normalized) >= 3 and normalized not in generic_terms and normalized not in terms:
            terms.append(normalized)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", _safe_text(question))
    for run in cjk_runs[:2]:
        if run not in terms:
            terms.append(run[:40])
    return " ".join(terms[:10]) or _safe_text(question)[:160]



_RESEARCH_PROVIDER_TERMINAL_FAILURES = {
    "credential_missing",
    "provider_adapter_unavailable",
    "provider_disabled",
    "provider_unconfigured",
    "provider_unknown",
    "needs_agent_browser_login",
    "runtime_dependency_missing",
    "blocked_by_safety",
}
_RESEARCH_PROVIDER_BOUNDED_RETRY_FAILURES = {
    "deadline_exceeded",
    "network_timeout",
    "provider_challenge",
    "rate_limited",
    "search_failed",
    "service_unavailable",
    "web_fetch_failed",
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
    forceRefresh: bool = False,
    deliverable: str = "evidence_bundle",
    evidenceBundleId: str = "",
    experiencePackId: str = "",
    sourceKey: str = "",
    readAnswer: bool = False,
    startChar: int = 0,
    maxChars: int = 6000,
    title: str = "",
    tags: list[str] | str | None = None,
    minConfidence: str = "",
    limit: int = 20,
    includeArchived: bool = False,
    confirm: bool = False,
    useAgentBrowserProfile: bool = False,
    preferredLanguage: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """L2 聚焦证据工具：一个可独立验真的多源问题；返回当前回合 evidence pack，不提供受管进度、恢复或跨阶段 handoff。

    Use this for exactly one focused question needing source comparison, freshness/conflict checks, or a compact
    answer/evidence pack. Use `web_broker` for one URL or narrow lookup. Multiple websites or search-then-verification
    steps for the same question are not separate fact domains. Load research.core to call this directly when it is not
    yet exposed. Use a Research episode for several separately deliverable user questions, managed recovery/progress,
    or downstream evidence handoff; put every known domain in its
    initial researchBriefIds/researchBriefGoals arrays. A brief already owned by that episode must be repaired there, not through this direct tool.
    Reuse a suitable current experience pack; refresh stale, low-confidence, or conflicting evidence.
    run + experiencePackId rechecks/updates that saved answer using its original evidence before searching gaps.
    search_experience/get_experience inspect saved answers; archive_experience hides one; delete_experience requires confirm=true.
    Accepted answers are saved automatically. forceRefresh=true requests fresh network evidence, not merely a wording revision.
    get_evidence + evidenceBundleId recovers durable answers even if an observation rawRef is unavailable.
    Add readAnswer=true to page the complete reviewed answer, limitations and original citation list with startChar/maxChars.
    Follow the returned nextOffset until null; an answer preview is not the full document. readAnswer and sourceKey are mutually exclusive.
    Answer offsets count Unicode code points in that document, not bytes or JavaScript UTF-16 units. Compare contentSha256 across pages; restart if it changes.
    Add sourceKey (S1 etc.) to read that saved original source, with startChar/maxChars for pagination.
    This reads the original snapshot, not today's website; no search or model call is needed.
    Research inherits the governed System Base browser-profile setting when the caller omits the flag. Only
    allowlisted provider/page hosts may reuse that login state; public fallback providers remain profile-free.
    """
    config = _research_config()
    web_fetch_config = get_web_fetch_config()
    configured_agent_browser_profile = bool(
        web_fetch_config.get("useAgentBrowserProfile")
        and list(web_fetch_config.get("agentBrowserProfileAllowlist") or [])
    )
    effective_agent_browser_profile = bool(
        useAgentBrowserProfile or configured_agent_browser_profile
    )
    agent_browser_profile_source = (
        "tool_and_system_config"
        if useAgentBrowserProfile and configured_agent_browser_profile
        else "tool_request"
        if useAgentBrowserProfile
        else "system_base"
        if configured_agent_browser_profile
        else "disabled"
    )
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
    access = ResearchAccessScope(state)
    read_modes = {"observe", "search_experience", "get_experience", "get_evidence"}
    mutation_modes = {"archive_experience", "restore_experience", "delete_experience", "promote_experience"}
    if normalized_mode in read_modes and not access.valid:
        return json.dumps(research_access_denied(normalized_mode), ensure_ascii=False)
    if normalized_mode in mutation_modes or (normalized_mode == "run" and experiencePackId):
        if not access.can_mutate():
            return json.dumps(research_access_denied(normalized_mode), ensure_ascii=False)
        target = (get_evidence_bundle(_safe_text(evidenceBundleId), access_check=access.allows)
                  if normalized_mode == "promote_experience"
                  else get_experience_pack(_safe_text(experiencePackId), include_archived=True, access_check=access.allows))
        if not target:
            return json.dumps(research_access_denied(normalized_mode), ensure_ascii=False)
    if normalized_mode == "observe":
        items = list_evidence_bundles(scope=scope, limit=limit, access_check=access.allows)
        summary = research_ledger_summary(scope=scope, include_archived=includeArchived, access_check=access.allows)
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
        from core.tools.research_quality import research_answer_text, research_claims
        from core.research_verification_bindings import research_evidence_bindings
        from runtimes.research.answer_read import answer_preview_proof, answer_read_tool, saved_answer_page

        if readAnswer and sourceKey:
            return json.dumps({"ok": False, "kind": "research_answer_page", "error": "choose_answer_or_source_not_both"})
        bundle = get_evidence_bundle(_safe_text(evidenceBundleId), access_check=access.allows)
        if not bundle:
            return json.dumps(research_access_denied(normalized_mode), ensure_ascii=False)
        if bundle:
            if readAnswer:
                return json.dumps(saved_answer_page(bundle, start=startChar, max_chars=maxChars), ensure_ascii=False)
            if sourceKey:
                from runtimes.research.evidence import EvidenceStore

                store = EvidenceStore()
                try:
                    store.restore((bundle.get("researchEvidenceBank") or {}).get("sources") or [])
                    page = store.read(sourceKey, start=max(0, startChar), max_chars=max(100, min(maxChars, 12000)))
                except ValueError as exc:
                    return json.dumps({"ok": False, "kind": "research_source_page", "error": str(exc),
                                       "evidenceBundleId": evidenceBundleId, "sourceKey": sourceKey,
                                       "availableSourceKeys": list(store.sources)}, ensure_ascii=False)
                original_bindings = [
                    row for row in research_evidence_bindings({"claimTable": research_claims(bundle)})
                    if row["citationKey"] == page["citationKey"] and row["url"] == page["url"]
                ]
                # This page reopens a saved snapshot. Its transient store read
                # counter is not the original research observation's identity.
                page.pop("evidenceRef", None)
                return json.dumps({"ok": True, "kind": "research_source_page", "evidenceBundleId": evidenceBundleId,
                                   "snapshotOnly": True, "originalReadBindings": original_bindings, **page}, ensure_ascii=False)
            full_answer = research_answer_text(bundle)
            answer_pack = _compact_visible_answer_pack(_research_answer_pack(bundle))
            # A bounded summary is transport, never a replacement for the
            # reviewed text. The durable page reader is its recovery path.
            answer_pack["answer"] = full_answer[:2400] if research_answer_is_usable(bundle) else ""
            rendered = json.loads(_render_payload(
                {
                    "ok": True,
                    "mode": normalized_mode,
                    "kind": "research_evidence_bundle",
                    "summary": "Evidence bundle found in persistent research ledger.",
                    "question": bundle.get("question"),
                    "freshness": bundle.get("freshness"),
                    "asOf": answer_pack.get("asOf") or bundle.get("asOf"),
                    "reviewDecision": answer_pack.get("reviewDecision") or bundle.get("reviewDecision"),
                    "independentReview": answer_pack.get("independentReview") or bundle.get("independentReview") or {},
                    "evidenceBundleId": bundle.get("evidenceBundleId"),
                    "answer": answer_pack.get("answer") or "",
                    "researchAnswerPack": answer_pack,
                    "sourceReadTool": f"research_broker(mode='get_evidence', evidenceBundleId='{bundle.get('evidenceBundleId')}', sourceKey='S1', startChar=0)",
                    "deliveryReady": bool(((answer_pack.get("score") or {}).get("deliveryReady"))),
                    "qualityTier": (answer_pack.get("score") or {}).get("qualityTier"),
                    "qualityMetrics": (answer_pack.get("score") or {}).get("acceptanceMetrics") or {},
                    "detailTool": answer_read_tool(str(bundle.get("evidenceBundleId") or "")),
                    "recommendedNextAction": "use_evidence_bundle",
                },
                max_chars=60_000,
            ))
            # Generic proof compaction may omit optional keys; retain these
            # small recovery fields after it, without copying the full answer.
            rendered["answerPreview"] = answer_preview_proof(full_answer)
            rendered["evidenceBindings"] = research_evidence_bindings({"claimTable": research_claims(bundle)})
            rendered["detailTool"] = answer_read_tool(str(bundle.get("evidenceBundleId") or ""))
            return json.dumps(rendered, ensure_ascii=False)
        return _render_payload(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": "Evidence bundle not found or expired.",
                "recommendedNextAction": "search_experience_then_run",
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
            access_check=access.allows,
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
                        "freshnessState": item.get("freshnessState"),
                        "ageDays": item.get("ageDays"),
                        "staleAt": item.get("staleAt"),
                        "expiresAt": item.get("expiresAt"),
                        "qualityStatus": item.get("qualityStatus"),
                        "invalidationReason": item.get("invalidationReason"),
                        "missingEvidence": list(item.get("missingEvidence") or [])[:3],
                        "answerAvailable": bool(_safe_text(item.get("researchResult"))),
                        "qualityTier": item.get("qualityTier"),
                        "qualityMetrics": item.get("qualityMetrics") or {},
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
        from runtimes.research.answer_read import answer_read_tool
        pack = get_experience_pack(_safe_text(experiencePackId), include_archived=includeArchived, access_check=access.allows)
        if not pack:
            return json.dumps(research_access_denied(normalized_mode), ensure_ascii=False)
        # The generic result fallback discards `item` when proof exceeds 8K.
        # Return a bounded, explicitly labelled preview and a real durable locator.
        item = {key: value for key, value in (pack or {}).items() if key in {
            "experiencePackId", "createdFromBundleId", "title", "question", "status", "version",
            "deliveryScope", "limitations", "asOf", "updatedAt", "scope", "qualityAccepted", "reuseEligible",
        }}
        answer = _safe_text((pack or {}).get("researchResult") or (pack or {}).get("answer"))
        if pack:
            item.update(answerPreview=answer[:2400], answerChars=len(answer), answerComplete=len(answer) <= 2400)
        return json.dumps(
            {
                "ok": bool(pack),
                "mode": normalized_mode,
                "kind": "research_experience_pack",
                "summary": "Experience pack found." if pack else "Experience pack not found.",
                **({"item": item} if pack else {}),
                "detailTool": answer_read_tool(str((pack or {}).get('createdFromBundleId') or '')) if pack else None,
                "recommendedNextAction": "get_evidence" if pack else "search_experience",
            },
            ensure_ascii=False,
        )

    if normalized_mode == "archive_experience":
        pack = archive_experience_pack(_safe_text(experiencePackId), initiated_by="research_broker", access_check=access.allows)
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
        pack = restore_experience_pack(_safe_text(experiencePackId), initiated_by="research_broker", access_check=access.allows)
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
        deleted = delete_experience_pack(_safe_text(experiencePackId), confirm=confirm, access_check=access.allows)
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
        pack = promote_experience_pack(_safe_text(evidenceBundleId), title=title, tags=_as_list(tags), access_check=access.allows)
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
    run_started_at = time.perf_counter()

    route_context = (
        (state or {}).get("current_route_context")
        if isinstance((state or {}).get("current_route_context"), dict)
        else {}
    )
    preferred_language = normalize_preferred_language(
        preferredLanguage or route_context.get("preferredLanguage")
    ) or infer_preferred_language(clean_question)
    delivery_requirements = _research_delivery_requirements(clean_question)

    seed_urls = _as_list(seedUrls)
    # A URL embedded in the user's brief is an explicit read request even
    # when the caller did not populate the structured ``seedUrls`` field.
    # Keep caller-provided seeds first and append only new URLs from the text;
    # normal domain policy and the shard cap still apply in ``_build_shards``.
    seen_seed_urls = {_safe_text(value).lower() for value in seed_urls if _safe_text(value)}
    for embedded_url in _URL_IN_TEXT_RE.findall(clean_question):
        normalized_url = embedded_url.rstrip(".,;:")
        if normalized_url and normalized_url.lower() not in seen_seed_urls:
            seed_urls.append(normalized_url)
            seen_seed_urls.add(normalized_url.lower())
    allowed_domains = _as_list(allowedDomains)
    blocked_domains = _as_list(blockedDomains)
    structured_facets = _build_explicit_question_facets(clean_question)
    requested_shard_cap = maxShards is not None
    shard_cap = max(1, min(_as_int(maxShards, config["defaultShardCount"]), config["maxShardCount"]))
    if not requested_shard_cap and len(structured_facets) >= 2:
        shard_cap = min(
            config["maxShardCount"],
            max(
                shard_cap,
                len(seed_urls)
                + len(structured_facets)
                + _research_authority_query_budget(len(structured_facets)),
            ),
        )
    task_shaped_shard_cap = shard_cap
    if delivery_requirements.get("mode") == "narrow_authoritative_technical":
        task_shaped_shard_cap = min(
            shard_cap,
            max(
                1,
                len(seed_urls),
                _as_int(delivery_requirements.get("targetSources"), 4),
            ),
        )
    round_cap = max(1, min(_as_int(maxRounds, config["maxRounds"]), config["maxRounds"]))
    shards = _build_shards(
        question=clean_question,
        research_intent=researchIntent,
        source_policy=sourcePolicy,
        seed_urls=seed_urls,
        allowed_domains=allowed_domains,
        max_shards=task_shaped_shard_cap,
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
            "preferredLanguage": preferred_language,
            "deliveryRequirements": delivery_requirements,
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
                "useAgentBrowserProfile": effective_agent_browser_profile,
                "agentBrowserProfileSource": agent_browser_profile_source,
            },
            "limits": {
                "defaultShardCount": config["defaultShardCount"],
                "requestedMaxShards": maxShards,
                "effectiveMaxShards": task_shaped_shard_cap,
                "configuredMaxShards": shard_cap,
                "hardMaxShardCount": config["maxShardCount"],
                "effectiveMaxRounds": round_cap,
                "toolDeadlineMs": _RESEARCH_TOOL_DEADLINE_MS,
                "architectSynthesisDeadlineMs": _RESEARCH_ARCHITECT_SYNTHESIS_DEADLINE_MS,
                "architectAgentTimeoutSeconds": config["architectAgentTimeoutSeconds"],
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
        access_check=access.allows,
    )
    experience_reuse = _experience_reuse_decision(
        experience_candidates,
        question=clean_question,
        source_policy=sourcePolicy,
        freshness=freshness,
        min_confidence=minConfidence,
    )
    revision_pack = get_experience_pack(_safe_text(experiencePackId), access_check=access.allows) if experiencePackId else None
    if experiencePackId and not revision_pack:
        return _render_payload({"ok": False, "mode": "run", "error": "experience_not_available", "summary": "指定答案不存在或已归档，未开始调研。"}, max_chars=4000)
    if revision_pack:
        experience_reuse = {"reuseDecision": "review", "reason": "explicit_answer_revision", "candidatePackId": experiencePackId}
    if forceRefresh:
        experience_reuse = {
            **experience_reuse,
            "reuseDecision": "refresh",
            "reason": "explicit_force_refresh",
            "candidatePackId": experience_reuse.get("candidatePackId"),
            "skippedSearches": False,
        }
    if experience_reuse.get("reuseDecision") == "reuse" and experience_candidates:
        pack_id = _safe_text(experience_reuse.get("candidatePackId"))
        pack = get_experience_pack(pack_id, record_usage=True, access_check=access.allows) if pack_id else experience_candidates[0]
        if pack:
            reused_bundle = _bundle_from_reused_pack(
                pack,
                question=clean_question,
                reuse=experience_reuse,
                deliverable=deliverable,
            )
            if reused_bundle.get("deliveryReady"):
                return _render_payload(reused_bundle, max_chars=36000)
            experience_reuse = {
                **experience_reuse,
                "reuseDecision": "refresh",
                "reason": "reused_pack_revalidation_failed",
                "skippedSearches": False,
                "revalidationIssues": list(
                    (reused_bundle.get("researchAnswerPack") or {}).get("missingOrStaleReasons")
                    or []
                )[:12],
            }

    prior_pack = revision_pack or next((pack for pack in experience_candidates if pack.get("experiencePackId") == experience_reuse.get("candidatePackId")), None)
    previous_bundle = get_evidence_bundle(prior_pack.get("createdFromBundleId"), access_check=access.allows) if prior_pack and not forceRefresh else None
    bundle = _run_agent_owned_research(
        question=clean_question, research_intent=researchIntent, source_policy=sourcePolicy,
        freshness=freshness, allowed_domains=allowed_domains, blocked_domains=blocked_domains,
        use_agent_browser_profile=effective_agent_browser_profile, tool_call_id=tool_call_id,
        max_shards=task_shaped_shard_cap, max_rounds=round_cap,
        preferred_language=preferred_language, seed_urls=seed_urls, deliverable=deliverable,
        experience_reuse=experience_reuse, state=state, previous_bundle=previous_bundle,
    )
    if revision_pack:
        bundle.update(supersedesExperiencePackId=experiencePackId, supersedesBundleId=revision_pack.get("createdFromBundleId"))
    stored = _store_evidence(bundle, state=state)
    if stored.get("kind") == "research_access_denied":
        return json.dumps(stored, ensure_ascii=False)
    return _render_payload(_visible_bundle(stored), max_chars=36000)
