from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import hashlib
import importlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core.database import db
from core.background_model_output import parse_background_json_object, sanitize_background_model_output
from core.model_control_plane import model_control_plane
from core.tools.plugin_cli import plugin_cli
from core.skills_install_service import get_skill_dependency_policy
from core.storage import storage
from core.extensions_capability_index import (
    extensions_runtime_cache_path,
    legacy_extensions_runtime_cache_path,
    write_capability_index,
)
from erc.event_bus import event_bus
from erc.models import RuntimeSource
from erc.runtime_context import get_runtime_context
from erc.runtime_registry import runtime_registry
from runtimes.extensions.skills.lexicons import get_extension_lexicon_registry
from runtimes.extensions.skills.loader import SkillLoader


logger = logging.getLogger(__name__)


class _LazyMcpManager:
    def __getattr__(self, name: str):
        manager = importlib.import_module("runtimes.extensions.mcp.client").mcp_manager
        return getattr(manager, name)


mcp_manager = _LazyMcpManager()


def __getattr__(name: str):
    if name == "select_family_keys_with_llm":
        from core.llm_tree_prefilter import select_family_keys_with_llm

        return select_family_keys_with_llm
    raise AttributeError(name)


def _get_family_selector():
    patched = globals().get("select_family_keys_with_llm")
    if patched is not None:
        return patched
    from core.llm_tree_prefilter import select_family_keys_with_llm

    return select_family_keys_with_llm


_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]{2,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "help",
    "please",
    "skill",
    "skills",
    "tool",
    "tools",
    "mcp",
    "服务",
    "工具",
    "一下",
    "一个",
    "一些",
    "我想",
    "想要",
    "给我",
    "请帮",
    "如何",
    "怎么",
    "请问",
    "帮忙",
    "想想",
    "使用",
    "帮我",
}
_SKILL_RERANK_POOL_FLOOR = 10
_MCP_RERANK_POOL_FLOOR = 12
_DYNAMIC_PROFILE_CACHE_LIMIT = 256
_DYNAMIC_PROFILE_LLM_TIMEOUT_SECONDS = 6.0
_DYNAMIC_THEME_FALLBACK_LIMIT = 2
_EXTENSION_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "v8_agent_os_extensions_runtime_context",
    default={},
)
_THEME_HEAVY_CLASSES = {"advisor_or_perspective", "methodology_or_tutorial", "skill_authoring"}
_THEME_FALLBACK_TARGETS = {"organization_leadership", "startup_growth", "negotiation_persuasion"}
_QUERY_ANALYSIS_CACHE_LIMIT = 512
_QUERY_ANALYSIS_CACHE: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {}
_EXTENSION_LEXICON_REGISTRY = get_extension_lexicon_registry()
_EXTENSION_LEXICON_SIGNATURE = "lexicon:uninitialized"
_EXTENSION_LEXICON_CORE_SIGNATURE = "lexicon-core:uninitialized"
_EXTENSION_LEXICON_LOCALES: tuple[str, ...] = ()
_EXTENSION_LEXICON_LOAD_ERRORS: tuple[str, ...] = ()
_MARKET_LEXICON_SIGNATURE = "lexicon-market:uninitialized"
_MARKET_LEXICON_ENABLED = False
_MARKET_LEXICON_LOCALES: tuple[str, ...] = ()
_MARKET_LEXICON_LOAD_ERRORS: tuple[str, ...] = ()
_CROSS_RUNTIME_ESCAPE_TOKENS: tuple[str, ...] = ()
_EXTENSION_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {}
_EXTENSION_QUERY_SYNONYMS_EXACT: dict[str, tuple[str, ...]] = {}
_EXTENSION_QUERY_SYNONYMS_PHRASE: dict[str, tuple[str, ...]] = {}
_QUERY_ARTIFACT_INTENT_SYNONYMS: dict[str, tuple[str, ...]] = {}
_QUERY_OPERATION_INTENT_SYNONYMS: dict[str, tuple[str, ...]] = {}
_QUERY_PRIMARY_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {}
_QUERY_SECONDARY_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {}
_SECONDARY_THEME_PRIMARY_MAP: dict[str, tuple[str, ...]] = {}
_QUERY_DOCUMENT_SUBINTENT_SYNONYMS: dict[str, dict[str, int]] = {}
_META_ADVISORY_HINTS: tuple[str, ...] = ()
_FAMILY_ADVISORY_HINTS: tuple[str, ...] = ()
_INTEGRATION_OR_TOOLING_HINTS: tuple[str, ...] = ()
_SKILL_DOCUMENT_SUBINTENT_SYNONYMS: dict[str, dict[str, int]] = {}
_MARKET_QUERY_SYNONYMS_EXACT: dict[str, tuple[str, ...]] = {}
_MARKET_QUERY_SYNONYMS_PHRASE: dict[str, tuple[str, ...]] = {}
_MARKET_QUERY_PRIMARY_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {}
_MARKET_QUERY_SECONDARY_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {}
_MARKET_SECONDARY_THEME_PRIMARY_MAP: dict[str, tuple[str, ...]] = {}
_CANONICAL_FAMILIES: dict[str, tuple[str, ...]] = {}
_CANONICAL_FAMILY_PARENTS: dict[str, tuple[str, ...]] = {}
_SKILL_CANONICAL_FAMILY_CACHE_LIMIT = 1024
_SKILL_CANONICAL_FAMILY_CACHE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
_INVENTORY_FRESHNESS_PREVIEW = "preview_best_effort"
_INVENTORY_FRESHNESS_GUARDED = "agent_launch_guarded"
_PROFILE_INFERENCE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="v8-ext-profile",
)
_ARTIFACT_MISMATCH_GROUPS: dict[str, set[str]] = {
    "presentation": {"video", "image", "audio", "skill"},
    "video": {"presentation", "document", "pdf", "spreadsheet", "skill"},
    "image": {"presentation", "spreadsheet", "audio", "skill"},
    "document": {"video", "audio", "skill"},
    "pdf": {"video", "audio", "skill"},
    "spreadsheet": {"video", "image", "audio", "skill"},
    "audio": {"presentation", "spreadsheet", "document", "skill"},
    "skill": {"presentation", "video", "image", "document", "pdf", "spreadsheet", "audio", "code"},
}
_ARTIFACT_PROFILE_ANCHORS: dict[str, set[str]] = {}


def _clear_query_analysis_cache() -> None:
    _QUERY_ANALYSIS_CACHE.clear()
    _SKILL_CANONICAL_FAMILY_CACHE.clear()


def _apply_extension_lexicon_state(state: dict[str, Any]) -> None:
    global _EXTENSION_LEXICON_SIGNATURE
    global _EXTENSION_LEXICON_CORE_SIGNATURE
    global _EXTENSION_LEXICON_LOCALES
    global _EXTENSION_LEXICON_LOAD_ERRORS
    global _MARKET_LEXICON_SIGNATURE
    global _MARKET_LEXICON_ENABLED
    global _MARKET_LEXICON_LOCALES
    global _MARKET_LEXICON_LOAD_ERRORS
    global _CROSS_RUNTIME_ESCAPE_TOKENS
    global _EXTENSION_QUERY_SYNONYMS
    global _EXTENSION_QUERY_SYNONYMS_EXACT
    global _EXTENSION_QUERY_SYNONYMS_PHRASE
    global _QUERY_ARTIFACT_INTENT_SYNONYMS
    global _QUERY_OPERATION_INTENT_SYNONYMS
    global _QUERY_PRIMARY_THEME_SYNONYMS
    global _QUERY_SECONDARY_THEME_SYNONYMS
    global _SECONDARY_THEME_PRIMARY_MAP
    global _QUERY_DOCUMENT_SUBINTENT_SYNONYMS
    global _META_ADVISORY_HINTS
    global _FAMILY_ADVISORY_HINTS
    global _INTEGRATION_OR_TOOLING_HINTS
    global _SKILL_DOCUMENT_SUBINTENT_SYNONYMS
    global _ARTIFACT_PROFILE_ANCHORS
    global _MARKET_QUERY_SYNONYMS_EXACT
    global _MARKET_QUERY_SYNONYMS_PHRASE
    global _MARKET_QUERY_PRIMARY_THEME_SYNONYMS
    global _MARKET_QUERY_SECONDARY_THEME_SYNONYMS
    global _MARKET_SECONDARY_THEME_PRIMARY_MAP
    global _CANONICAL_FAMILIES
    global _CANONICAL_FAMILY_PARENTS

    _EXTENSION_LEXICON_SIGNATURE = str(state.get("signature") or "lexicon:empty").strip() or "lexicon:empty"
    _EXTENSION_LEXICON_CORE_SIGNATURE = (
        str(state.get("coreSignature") or _EXTENSION_LEXICON_SIGNATURE).strip() or _EXTENSION_LEXICON_SIGNATURE
    )
    _EXTENSION_LEXICON_LOCALES = tuple(str(item).strip() for item in list(state.get("locales") or []) if str(item).strip())
    _EXTENSION_LEXICON_LOAD_ERRORS = tuple(
        str(item).strip() for item in list(state.get("loadErrors") or []) if str(item).strip()
    )
    _MARKET_LEXICON_SIGNATURE = str(state.get("marketSignature") or "lexicon-market:empty").strip() or "lexicon-market:empty"
    _MARKET_LEXICON_ENABLED = bool(state.get("marketEnabled"))
    _MARKET_LEXICON_LOCALES = tuple(
        str(item).strip() for item in list(state.get("marketLocales") or []) if str(item).strip()
    )
    _MARKET_LEXICON_LOAD_ERRORS = tuple(
        str(item).strip() for item in list(state.get("marketLoadErrors") or []) if str(item).strip()
    )
    _CROSS_RUNTIME_ESCAPE_TOKENS = tuple(state.get("crossRuntimeEscapeTokens") or ())
    _EXTENSION_QUERY_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(state.get("querySynonyms") or {}).items()
    }
    _EXTENSION_QUERY_SYNONYMS_EXACT = {
        key: tuple(values)
        for key, values in dict(state.get("querySynonymsExact") or {}).items()
    }
    _EXTENSION_QUERY_SYNONYMS_PHRASE = {
        key: tuple(values)
        for key, values in dict(state.get("querySynonymsPhrase") or {}).items()
    }
    _QUERY_ARTIFACT_INTENT_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(state.get("artifactIntentSynonyms") or {}).items()
    }
    _QUERY_OPERATION_INTENT_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(state.get("operationIntentSynonyms") or {}).items()
    }
    _QUERY_PRIMARY_THEME_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(state.get("primaryThemeSynonyms") or {}).items()
    }
    _QUERY_SECONDARY_THEME_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(state.get("secondaryThemeSynonyms") or {}).items()
    }
    _SECONDARY_THEME_PRIMARY_MAP = {
        key: tuple(values)
        for key, values in dict(state.get("secondaryThemePrimaryMap") or {}).items()
    }
    _QUERY_DOCUMENT_SUBINTENT_SYNONYMS = {
        key: {name: int(weight) for name, weight in dict(values).items()}
        for key, values in dict(state.get("documentSubIntentSynonyms") or {}).items()
    }
    _META_ADVISORY_HINTS = tuple(state.get("metaAdvisoryHints") or ())
    _FAMILY_ADVISORY_HINTS = tuple(state.get("familyAdvisoryHints") or ())
    _INTEGRATION_OR_TOOLING_HINTS = tuple(state.get("integrationOrToolingHints") or ())
    _SKILL_DOCUMENT_SUBINTENT_SYNONYMS = {
        key: {name: int(weight) for name, weight in dict(values).items()}
        for key, values in dict(state.get("skillDocumentSubIntentSynonyms") or {}).items()
    }
    _ARTIFACT_PROFILE_ANCHORS = {
        key: set(values)
        for key, values in dict(state.get("artifactProfileAnchors") or {}).items()
    }
    market_state = dict(state.get("market") or {})
    _MARKET_QUERY_SYNONYMS_EXACT = {
        key: tuple(values)
        for key, values in dict(market_state.get("querySynonymsExact") or {}).items()
    }
    _MARKET_QUERY_SYNONYMS_PHRASE = {
        key: tuple(values)
        for key, values in dict(market_state.get("querySynonymsPhrase") or {}).items()
    }
    _MARKET_QUERY_PRIMARY_THEME_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(market_state.get("primaryThemeSynonyms") or {}).items()
    }
    _MARKET_QUERY_SECONDARY_THEME_SYNONYMS = {
        key: tuple(values)
        for key, values in dict(market_state.get("secondaryThemeSynonyms") or {}).items()
    }
    _MARKET_SECONDARY_THEME_PRIMARY_MAP = {
        key: tuple(values)
        for key, values in dict(market_state.get("secondaryThemePrimaryMap") or {}).items()
    }
    _CANONICAL_FAMILIES = {
        key: tuple(values)
        for key, values in dict(state.get("canonicalFamilies") or {}).items()
    }
    _CANONICAL_FAMILY_PARENTS = {
        key: tuple(values)
        for key, values in dict(state.get("canonicalFamilyParents") or {}).items()
    }


def _ensure_extension_lexicon_state() -> dict[str, Any]:
    state = _EXTENSION_LEXICON_REGISTRY.ensure_fresh()
    signature = str(state.get("signature") or "lexicon:empty").strip() or "lexicon:empty"
    if signature != _EXTENSION_LEXICON_SIGNATURE:
        _apply_extension_lexicon_state(state)
        _clear_query_analysis_cache()
    return state


_ensure_extension_lexicon_state()


@dataclass(slots=True)
class ExtensionRouteBundle:
    prompt_addition: str
    filtered_tools: list[Any]
    selected_skill_names: list[str]
    selected_skill_ids: list[str]
    skill_root_descriptors: list[dict[str, Any]]
    exposed_mcp_tool_names: list[str]
    candidate_summary: dict[str, Any]


def _tokenize(text: str) -> list[str]:
    lowered = str(text or "").lower()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(lowered):
        stripped = token.strip().lower()
        if len(stripped) <= 1 or stripped in _STOPWORDS:
            continue
        tokens.append(stripped)
    return tokens


def _unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = str(item or "").strip().lower()
        if not normalized or normalized in seen or normalized in _STOPWORDS:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _normalize_text_for_match(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _text_matches_phrase(text: str, phrase: str) -> bool:
    normalized_text = _normalize_text_for_match(text)
    normalized_phrase = _normalize_text_for_match(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized_phrase):
        return normalized_phrase in normalized_text
    escaped = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![0-9a-z]){escaped}(?![0-9a-z])", normalized_text) is not None


def _query_matches_synonym(query_text: str, query_tokens: list[str], synonym: str) -> bool:
    normalized_synonym = _normalize_text_for_match(synonym)
    if not normalized_synonym:
        return False
    if normalized_synonym in query_tokens:
        return True
    return _text_matches_phrase(query_text, normalized_synonym)


def _weighted_query_hit_score(query_text: str, query_tokens: list[str], rules: dict[str, int]) -> int:
    score = 0
    for synonym, weight in rules.items():
        if _query_matches_synonym(query_text, query_tokens, synonym):
            score += int(weight)
    return score


def _weighted_text_hit_score(text: str, rules: dict[str, int]) -> int:
    score = 0
    for synonym, weight in rules.items():
        if _text_matches_phrase(text, synonym):
            score += int(weight)
    return score


def _expand_query_token_variants(query_tokens: list[str]) -> list[str]:
    expanded: list[str] = list(query_tokens)
    for token in list(query_tokens):
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
            if len(token) >= 4:
                expanded.append(token[:2])
                expanded.append(token[-2:])
            for index in range(0, len(token) - 1):
                expanded.append(token[index : index + 2])
    return _unique_preserve_order(expanded)


def _expand_query_tokens_with_synonyms(
    *,
    query_text: str,
    base_tokens: list[str],
    exact_map: dict[str, tuple[str, ...]],
    phrase_map: dict[str, tuple[str, ...]],
) -> tuple[list[str], list[str]]:
    matched_terms: list[str] = []
    expanded: list[str] = list(base_tokens)
    for token in list(base_tokens):
        synonyms = exact_map.get(token, ())
        if not synonyms:
            continue
        matched_terms.append(token)
        expanded.extend(synonyms)
    for hint, synonyms in phrase_map.items():
        if hint not in query_text:
            continue
        matched_terms.append(hint)
        expanded.extend(synonyms)
    return _unique_preserve_order(expanded), _unique_preserve_order(matched_terms)


def _collect_market_query_enrichment(
    *,
    query_text: str,
    query_tokens: list[str],
) -> dict[str, Any]:
    if not _MARKET_LEXICON_ENABLED:
        return {
            "matchedTerms": [],
            "expandedTokens": [],
            "primaryThemeHints": [],
            "secondaryThemeHints": [],
            "contributionScore": 0,
        }
    expanded_tokens, matched_terms = _expand_query_tokens_with_synonyms(
        query_text=query_text,
        base_tokens=query_tokens,
        exact_map=_MARKET_QUERY_SYNONYMS_EXACT,
        phrase_map=_MARKET_QUERY_SYNONYMS_PHRASE,
    )
    added_tokens = [token for token in expanded_tokens if token not in query_tokens]
    primary_theme_hints = [
        key
        for key, synonyms in _MARKET_QUERY_PRIMARY_THEME_SYNONYMS.items()
        if any(_query_matches_synonym(query_text, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    secondary_theme_hints = [
        key
        for key, synonyms in _MARKET_QUERY_SECONDARY_THEME_SYNONYMS.items()
        if any(_query_matches_synonym(query_text, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    for tag in list(secondary_theme_hints):
        for implied_theme in _MARKET_SECONDARY_THEME_PRIMARY_MAP.get(tag, ()):
            if implied_theme not in primary_theme_hints:
                primary_theme_hints.append(implied_theme)
    contribution_score = (2 * len(matched_terms)) + len(added_tokens) + len(primary_theme_hints)
    return {
        "matchedTerms": matched_terms,
        "expandedTokens": added_tokens,
        "primaryThemeHints": _unique_preserve_order(primary_theme_hints),
        "secondaryThemeHints": _unique_preserve_order(secondary_theme_hints),
        "contributionScore": int(contribution_score),
    }


def _canonical_family_depth(family: str, _seen: set[str] | None = None) -> int:
    normalized_family = _normalize_text_for_match(family)
    if not normalized_family:
        return 0
    seen = set(_seen or set())
    if normalized_family in seen:
        return 0
    seen.add(normalized_family)
    parents = _CANONICAL_FAMILY_PARENTS.get(normalized_family, ())
    if not parents:
        return 1
    return 1 + max(_canonical_family_depth(parent, seen) for parent in parents)


def _sort_canonical_families(families: Iterable[str]) -> list[str]:
    ranked = {
        _normalize_text_for_match(family)
        for family in families
        if _normalize_text_for_match(family)
    }
    return sorted(ranked, key=lambda item: (-_canonical_family_depth(item), item))


def _expand_canonical_families(families: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _visit(family: str) -> None:
        normalized_family = _normalize_text_for_match(family)
        if not normalized_family or normalized_family in seen:
            return
        seen.add(normalized_family)
        ordered.append(normalized_family)
        for parent in _CANONICAL_FAMILY_PARENTS.get(normalized_family, ()):
            _visit(parent)

    for family in _sort_canonical_families(families):
        _visit(family)
    return ordered


def _detect_query_canonical_families(query_text: str, query_tokens: list[str]) -> tuple[list[str], list[str]]:
    direct_matches = [
        family
        for family, synonyms in _CANONICAL_FAMILIES.items()
        if any(_query_matches_synonym(query_text, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    ordered_direct = _sort_canonical_families(direct_matches)
    return ordered_direct, _expand_canonical_families(ordered_direct)


def _skill_canonical_families(skill: dict[str, Any]) -> tuple[list[str], list[str]]:
    skill_id = str(skill.get("skillId") or skill.get("skillRoot") or skill.get("skillName") or skill.get("name") or skill.get("folder") or "").strip()
    text_corpus = _skill_text_corpus(skill)
    text_fingerprint = hashlib.sha1(text_corpus.encode("utf-8")).hexdigest()
    cache_key = f"{_EXTENSION_LEXICON_SIGNATURE}|{skill_id}|{text_fingerprint}"
    cached = _SKILL_CANONICAL_FAMILY_CACHE.get(cache_key)
    if cached is not None:
        direct_families, expanded_families = cached
        return list(direct_families), list(expanded_families)

    direct_matches = [
        family
        for family, synonyms in _CANONICAL_FAMILIES.items()
        if any(_text_matches_phrase(text_corpus, str(synonym)) for synonym in synonyms)
    ]
    ordered_direct = _sort_canonical_families(direct_matches)
    expanded_families = _expand_canonical_families(ordered_direct)
    _SKILL_CANONICAL_FAMILY_CACHE[cache_key] = (tuple(ordered_direct), tuple(expanded_families))
    if len(_SKILL_CANONICAL_FAMILY_CACHE) > _SKILL_CANONICAL_FAMILY_CACHE_LIMIT:
        oldest_key = next(iter(_SKILL_CANONICAL_FAMILY_CACHE))
        _SKILL_CANONICAL_FAMILY_CACHE.pop(oldest_key, None)
    return ordered_direct, expanded_families


def _skill_matches_query_canonical_family(skill: dict[str, Any], query_profile: dict[str, Any]) -> bool:
    query_families = set(_normalize_profile_items(query_profile.get("canonicalFamilies")))
    if not query_families:
        return False
    _direct_skill_families, skill_families = _skill_canonical_families(skill)
    return bool(query_families.intersection(set(skill_families)))


def _skill_query_canonical_family_match(skill: dict[str, Any], query_profile: dict[str, Any]) -> dict[str, Any]:
    query_direct = set(_normalize_profile_items(query_profile.get("directCanonicalFamilies")))
    query_expanded = set(_normalize_profile_items(query_profile.get("canonicalFamilies")))
    if not query_expanded:
        return {
            "matched": False,
            "matchedFamilies": [],
            "directMatchedFamilies": [],
            "exactDirectMatchedFamilies": [],
            "bestFamily": None,
            "bestDepth": 0,
        }
    skill_direct_families, skill_expanded_families = _skill_canonical_families(skill)
    skill_direct = set(skill_direct_families)
    skill_expanded = set(skill_expanded_families)
    exact_direct_overlap = _sort_canonical_families(query_direct.intersection(skill_direct))
    direct_overlap = _sort_canonical_families(query_direct.intersection(skill_expanded))
    expanded_overlap = _sort_canonical_families(query_expanded.intersection(skill_expanded))
    best_family = (
        exact_direct_overlap[0]
        if exact_direct_overlap
        else direct_overlap[0]
        if direct_overlap
        else expanded_overlap[0]
        if expanded_overlap
        else None
    )
    return {
        "matched": bool(expanded_overlap),
        "matchedFamilies": expanded_overlap,
        "directMatchedFamilies": direct_overlap,
        "exactDirectMatchedFamilies": exact_direct_overlap,
        "bestFamily": best_family,
        "bestDepth": _canonical_family_depth(best_family) if best_family else 0,
    }


def _query_tokens_for_extensions(text: str) -> list[str]:
    _ensure_extension_lexicon_state()
    base_tokens = _expand_query_token_variants(_tokenize(text))
    query_text = str(text or "").strip().lower()
    core_tokens, _matched_terms = _expand_query_tokens_with_synonyms(
        query_text=query_text,
        base_tokens=base_tokens,
        exact_map=_EXTENSION_QUERY_SYNONYMS_EXACT,
        phrase_map=_EXTENSION_QUERY_SYNONYMS_PHRASE,
    )
    market_enrichment = _collect_market_query_enrichment(
        query_text=query_text,
        query_tokens=core_tokens,
    )
    return _unique_preserve_order([*core_tokens, *list(market_enrichment.get("expandedTokens") or [])])


def _clone_query_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifactIntent": profile.get("artifactIntent"),
        "artifactIntents": list(profile.get("artifactIntents") or []),
        "directCanonicalFamilies": list(profile.get("directCanonicalFamilies") or []),
        "canonicalFamilies": list(profile.get("canonicalFamilies") or []),
        "primaryCanonicalFamily": profile.get("primaryCanonicalFamily"),
        "shortCanonicalNarrowing": bool(profile.get("shortCanonicalNarrowing")),
        "documentSubIntent": profile.get("documentSubIntent"),
        "operationIntent": profile.get("operationIntent"),
        "operationIntents": list(profile.get("operationIntents") or []),
        "primaryThemeIntents": list(profile.get("primaryThemeIntents") or []),
        "secondaryThemeHints": list(profile.get("secondaryThemeHints") or []),
        "marketPrimaryThemeHints": list(profile.get("marketPrimaryThemeHints") or []),
        "marketSecondaryThemeHints": list(profile.get("marketSecondaryThemeHints") or []),
        "marketLexiconHitTerms": list(profile.get("marketLexiconHitTerms") or []),
        "marketLexiconContributionScore": int(profile.get("marketLexiconContributionScore") or 0),
        "topicTokens": list(profile.get("topicTokens") or []),
    }


def _analyze_extensions_query(text: str) -> tuple[list[str], dict[str, Any], bool, dict[str, Any], dict[str, Any]]:
    lexicon_state = _ensure_extension_lexicon_state()
    normalized_query = " ".join(str(text or "").strip().lower().split())
    cache_key = f"{_EXTENSION_LEXICON_SIGNATURE}|{normalized_query}"
    cached = _QUERY_ANALYSIS_CACHE.get(cache_key)
    if cached is not None:
        cached_tokens, cached_profile = cached
        cloned_profile = _clone_query_profile(cached_profile)
        market_state = {
            "matchedTerms": list(cloned_profile.get("marketLexiconHitTerms") or []),
            "expandedTokens": [],
            "primaryThemeHints": list(cloned_profile.get("marketPrimaryThemeHints") or []),
            "secondaryThemeHints": list(cloned_profile.get("marketSecondaryThemeHints") or []),
            "contributionScore": int(cloned_profile.get("marketLexiconContributionScore") or 0),
        }
        return list(cached_tokens), cloned_profile, True, lexicon_state, market_state

    raw_query_text = str(text or "")
    normalized_lower = raw_query_text.strip().lower()
    base_tokens = _expand_query_token_variants(_tokenize(raw_query_text))
    core_tokens, _core_hit_terms = _expand_query_tokens_with_synonyms(
        query_text=normalized_lower,
        base_tokens=base_tokens,
        exact_map=_EXTENSION_QUERY_SYNONYMS_EXACT,
        phrase_map=_EXTENSION_QUERY_SYNONYMS_PHRASE,
    )
    market_enrichment = _collect_market_query_enrichment(
        query_text=normalized_lower,
        query_tokens=core_tokens,
    )
    query_tokens = _unique_preserve_order([*core_tokens, *list(market_enrichment.get("expandedTokens") or [])])
    query_profile = _detect_query_intents(raw_query_text, core_tokens)
    direct_canonical_families, canonical_families = _detect_query_canonical_families(raw_query_text, core_tokens)
    query_profile["directCanonicalFamilies"] = direct_canonical_families
    query_profile["canonicalFamilies"] = canonical_families
    query_profile["primaryCanonicalFamily"] = direct_canonical_families[0] if direct_canonical_families else None
    query_profile["shortCanonicalNarrowing"] = bool(
        direct_canonical_families
        and not query_profile.get("artifactIntents")
        and not query_profile.get("operationIntents")
        and len(_tokenize(raw_query_text)) <= 1
    )
    query_profile["marketPrimaryThemeHints"] = list(market_enrichment.get("primaryThemeHints") or [])
    query_profile["marketSecondaryThemeHints"] = list(market_enrichment.get("secondaryThemeHints") or [])
    query_profile["marketLexiconHitTerms"] = list(market_enrichment.get("matchedTerms") or [])
    query_profile["marketLexiconContributionScore"] = int(market_enrichment.get("contributionScore") or 0)
    _QUERY_ANALYSIS_CACHE[cache_key] = (tuple(query_tokens), _clone_query_profile(query_profile))
    if len(_QUERY_ANALYSIS_CACHE) > _QUERY_ANALYSIS_CACHE_LIMIT:
        oldest_key = next(iter(_QUERY_ANALYSIS_CACHE))
        _QUERY_ANALYSIS_CACHE.pop(oldest_key, None)
    return query_tokens, query_profile, False, lexicon_state, market_enrichment


def _detect_query_intents(text: str, query_tokens: list[str]) -> dict[str, Any]:
    lowered = str(text or "").strip().lower()
    artifact_intents = [
        key
        for key, synonyms in _QUERY_ARTIFACT_INTENT_SYNONYMS.items()
        if any(_query_matches_synonym(lowered, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    operation_intents = [
        key
        for key, synonyms in _QUERY_OPERATION_INTENT_SYNONYMS.items()
        if any(_query_matches_synonym(lowered, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    primary_theme_intents = [
        key
        for key, synonyms in _QUERY_PRIMARY_THEME_SYNONYMS.items()
        if any(_query_matches_synonym(lowered, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    secondary_theme_hints = [
        key
        for key, synonyms in _QUERY_SECONDARY_THEME_SYNONYMS.items()
        if any(_query_matches_synonym(lowered, query_tokens, str(synonym)) for synonym in synonyms)
    ]
    office_document_score = _weighted_query_hit_score(
        lowered,
        query_tokens,
        _QUERY_DOCUMENT_SUBINTENT_SYNONYMS.get("office_document", {}),
    )
    documentation_score = _weighted_query_hit_score(
        lowered,
        query_tokens,
        _QUERY_DOCUMENT_SUBINTENT_SYNONYMS.get("documentation", {}),
    )
    document_sub_intent: str | None = None
    if office_document_score > documentation_score and office_document_score > 0:
        document_sub_intent = "office_document"
    elif documentation_score > office_document_score and documentation_score > 0:
        document_sub_intent = "documentation"
    if document_sub_intent and "document" not in artifact_intents:
        artifact_intents.append("document")
    for tag in secondary_theme_hints:
        for primary_theme in _SECONDARY_THEME_PRIMARY_MAP.get(tag, ()):
            if primary_theme not in primary_theme_intents:
                primary_theme_intents.append(primary_theme)
    matched_terms = {
        str(synonym).lower()
        for key in artifact_intents
        for synonym in _QUERY_ARTIFACT_INTENT_SYNONYMS.get(key, ())
        if str(synonym).strip()
    }
    matched_terms.update(
        str(synonym).lower()
        for key in operation_intents
        for synonym in _QUERY_OPERATION_INTENT_SYNONYMS.get(key, ())
        if str(synonym).strip()
    )
    matched_terms.update(
        str(synonym).lower()
        for key in primary_theme_intents
        for synonym in _QUERY_PRIMARY_THEME_SYNONYMS.get(key, ())
        if str(synonym).strip()
    )
    matched_terms.update(
        str(synonym).lower()
        for key in secondary_theme_hints
        for synonym in _QUERY_SECONDARY_THEME_SYNONYMS.get(key, ())
        if str(synonym).strip()
    )
    topic_tokens = [token for token in query_tokens if token not in matched_terms]
    return {
        "artifactIntent": artifact_intents[0] if artifact_intents else None,
        "artifactIntents": artifact_intents,
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "primaryCanonicalFamily": None,
        "shortCanonicalNarrowing": False,
        "documentSubIntent": document_sub_intent,
        "operationIntent": operation_intents[0] if operation_intents else None,
        "operationIntents": operation_intents,
        "primaryThemeIntents": primary_theme_intents,
        "secondaryThemeHints": secondary_theme_hints,
        "topicTokens": topic_tokens,
    }


_SKILL_TEMPLATE_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\buse\s+this\s+skill\b", re.IGNORECASE),
    re.compile(r"\buse\s+when\b", re.IGNORECASE),
    re.compile(r"\bwhen\s+claude\s+needs\b", re.IGNORECASE),
    re.compile(r"\bthis\s+skill\s+should\b", re.IGNORECASE),
    re.compile(r"\bskill\s+description\b", re.IGNORECASE),
    re.compile(r"\bSKILL\.md\b", re.IGNORECASE),
    re.compile(r"使用该技能"),
    re.compile(r"使用技能"),
    re.compile(r"当用户需要"),
)


def _clean_skill_template_noise(text: str) -> str:
    cleaned = str(text or "")
    for pattern in _SKILL_TEMPLATE_NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _score_text(*, query_tokens: list[str], title: str, description: str) -> int:
    if not query_tokens:
        return 0

    title_tokens = _tokenize(title)
    description_tokens = _tokenize(description)
    title_set = set(title_tokens)
    description_set = set(description_tokens)

    score = 0
    for token in query_tokens:
        if token in title_set:
            score += 4
        if token in description_set:
            score += 2
        if token in str(title or "").lower():
            score += 2
        if token in str(description or "").lower():
            score += 1
    return score


def _normalize_hint_items(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,\n/|]+", value)
    else:
        raw_items = list(value or [])
    return [
        str(item).strip()
        for item in raw_items
        if str(item).strip()
    ]


def _skill_recall_hints(skill: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("aliases", "triggers", "keywords", "tags"):
        hints.extend(_normalize_hint_items(skill.get(key)))
    capability_tags = dict(skill.get("capabilityTags") or {})
    hints.extend(_normalize_hint_items(capability_tags.get("languageAliases")))
    prefilter_payload = dict(skill.get("prefilter") or {})
    hints.extend(_normalize_hint_items(prefilter_payload.get("aliases")))
    return hints


def _normalize_profile_items(value: Any) -> list[str]:
    return [
        str(item).strip().lower()
        for item in list(value or [])
        if str(item).strip()
    ]


def _skill_text_corpus(skill: dict[str, Any]) -> str:
    parts: list[str] = [
        str(skill.get("name") or skill.get("folder") or "").strip(),
        str(skill.get("folder") or "").strip(),
        _clean_skill_template_noise(str(skill.get("description") or "").strip()),
    ]
    for key in ("aliases", "triggers", "keywords", "tags"):
        parts.extend(str(item).strip() for item in _normalize_hint_items(skill.get(key)))
    return " ".join(part for part in parts if part)


def _detect_skill_document_subintent(skill: dict[str, Any]) -> str | None:
    text = _skill_text_corpus(skill)
    office_score = _weighted_text_hit_score(text, _SKILL_DOCUMENT_SUBINTENT_SYNONYMS.get("office_document", {}))
    documentation_score = _weighted_text_hit_score(text, _SKILL_DOCUMENT_SUBINTENT_SYNONYMS.get("documentation", {}))
    if office_score > documentation_score and office_score > 0:
        return "office_document"
    if documentation_score > office_score and documentation_score > 0:
        return "documentation"
    return None


def _has_meta_advisory_signal(skill: dict[str, Any]) -> bool:
    text = _skill_text_corpus(skill)
    return any(_text_matches_phrase(text, hint) for hint in _META_ADVISORY_HINTS)


def _has_direct_primary_theme_match(skill: dict[str, Any], query_profile: dict[str, Any]) -> bool:
    query_themes = set(_normalize_profile_items(query_profile.get("primaryThemeIntents")))
    skill_themes = set(_normalize_profile_items((skill.get("themeProfile") or {}).get("primaryThemes")))
    return bool(query_themes.intersection(skill_themes))


def _score_advisory_fallback_entry(
    *,
    query_text: str,
    query_tokens: list[str],
    query_profile: dict[str, Any],
    skill: dict[str, Any],
) -> int:
    profile = dict(skill.get("capabilityProfile") or {})
    theme_profile = dict(skill.get("themeProfile") or {})
    skill_class = str(profile.get("skillClass") or "").strip().lower()
    if skill_class not in _THEME_HEAVY_CLASSES:
        return 0
    query_themes = set(_normalize_profile_items(query_profile.get("primaryThemeIntents"))).intersection(_THEME_FALLBACK_TARGETS)
    if not query_themes:
        return 0
    implied_primary_themes = {
        implied_theme
        for tag in _normalize_profile_items(theme_profile.get("secondaryThemeTags"))
        for implied_theme in _SECONDARY_THEME_PRIMARY_MAP.get(tag, ())
    }
    secondary_hints = set(_normalize_profile_items(query_profile.get("secondaryThemeHints")))
    secondary_theme_tags = set(_normalize_profile_items(theme_profile.get("secondaryThemeTags")))
    score = 0
    if _has_meta_advisory_signal(skill):
        score += 40
    matched_implied_themes = query_themes.intersection(implied_primary_themes)
    if matched_implied_themes:
        score += 11 + (4 * len(matched_implied_themes))
    matched_secondary_tags = secondary_hints.intersection(secondary_theme_tags)
    if matched_secondary_tags:
        score += 8 + (2 * len(matched_secondary_tags))
    lexical_score = _score_text(
        query_tokens=query_tokens,
        title=str(skill.get("name") or skill.get("folder") or "").strip(),
        description=_skill_text_corpus(skill),
    )
    if lexical_score > 0:
        score += min(lexical_score, 14)
    capability_confidence = float(profile.get("capabilityConfidence") or 0.0)
    theme_confidence = float(theme_profile.get("themeConfidence") or 0.0)
    score += int(round(capability_confidence * 4))
    score += int(round(theme_confidence * 3))
    if skill_class == "advisor_or_perspective":
        score += 4
    return score


def _merge_keepalive_skills(primary: list[dict[str, Any]], keepalive: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in [*keepalive, *primary]:
        skill_key = str(entry.get("skillId") or entry.get("skillRoot") or entry.get("path") or "").strip()
        if not skill_key or skill_key in seen:
            continue
        seen.add(skill_key)
        merged.append(entry)
        if limit > 0 and len(merged) >= limit:
            break
    return merged


def _merge_recent_keepalive_skills(primary: list[dict[str, Any]], keepalive: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Recent skills are only recall aids; direct current-query hits must stay
    # ahead of them so old sessions do not crowd out the current request.
    for entry in [*primary, *keepalive]:
        skill_key = str(entry.get("skillId") or entry.get("skillRoot") or entry.get("path") or "").strip()
        if not skill_key or skill_key in seen:
            continue
        seen.add(skill_key)
        merged.append(entry)
        if limit > 0 and len(merged) >= limit:
            break
    return merged


def _score_skill_entry(
    *,
    query_text: str,
    query_tokens: list[str],
    query_profile: dict[str, Any],
    skill: dict[str, Any],
) -> tuple[int, bool, bool]:
    def _profile_artifact_anchor_bonus(profile_payload: dict[str, Any], matched_artifacts: list[str]) -> int:
        evidence = profile_payload.get("evidenceSignals") or {}
        artifact_matches = evidence.get("artifactMatches") if isinstance(evidence, dict) else {}
        if not isinstance(artifact_matches, dict):
            return 0
        bonus = 0
        for artifact in matched_artifacts:
            terms = {
                str(term or "").strip().lower()
                for term in list(artifact_matches.get(artifact) or [])
                if str(term or "").strip()
            }
            anchors = _ARTIFACT_PROFILE_ANCHORS.get(artifact, set())
            if anchors.intersection(terms):
                bonus = max(bonus, 6)
            elif len(terms) >= 4:
                bonus = max(bonus, 2)
        return bonus

    name = str(skill.get("name") or skill.get("folder") or "").strip()
    folder = str(skill.get("folder") or "").strip()
    description = str(skill.get("description") or "").strip()
    cleaned_description = _clean_skill_template_noise(description)
    normalized_query = str(query_text or "").strip().lower()
    score = _score_text(query_tokens=query_tokens, title=name or folder, description=cleaned_description)
    has_query_signal = score > 0
    for candidate in (name, folder, str(skill.get("skillId") or ""), str(skill.get("skillName") or "")):
        normalized_candidate = str(candidate or "").strip().lower()
        if normalized_candidate and normalized_candidate in normalized_query:
            score += 48 if normalized_candidate in {str(skill.get("skillId") or "").strip().lower(), str(skill.get("skillName") or "").strip().lower()} else 36
            has_query_signal = True
    for hint in _skill_recall_hints(skill):
        normalized_hint = str(hint or "").strip().lower()
        if not normalized_hint:
            continue
        if normalized_hint in normalized_query:
            score += 42
            has_query_signal = True
        hint_score = _score_text(query_tokens=query_tokens, title=normalized_hint, description="")
        score += hint_score
        if hint_score > 0:
            has_query_signal = True

    profile = dict(skill.get("capabilityProfile") or {})
    theme_profile = dict(skill.get("themeProfile") or {})
    primary_artifact_types = _normalize_profile_items(profile.get("primaryArtifactTypes"))
    primary_operations = _normalize_profile_items(profile.get("primaryOperations"))
    secondary_artifact_hints = _normalize_profile_items(profile.get("secondaryArtifactHints"))
    secondary_operation_hints = _normalize_profile_items(profile.get("secondaryOperationHints"))
    skill_class = str(profile.get("skillClass") or "").strip().lower()
    capability_tags = dict(skill.get("capabilityTags") or {})
    capability_kinds = set(_normalize_profile_items(capability_tags.get("capabilityKind")))
    capability_artifacts = set(_normalize_profile_items(capability_tags.get("artifactTypes")))
    confidence = float(profile.get("capabilityConfidence") or 0.0)
    primary_themes = _normalize_profile_items(theme_profile.get("primaryThemes"))
    secondary_theme_tags = _normalize_profile_items(theme_profile.get("secondaryThemeTags"))
    implied_primary_themes = _unique_preserve_order(
        implied_theme
        for tag in secondary_theme_tags
        for implied_theme in _SECONDARY_THEME_PRIMARY_MAP.get(tag, ())
    )
    theme_confidence = float(theme_profile.get("themeConfidence") or 0.0)

    artifact_intents = _normalize_profile_items(query_profile.get("artifactIntents"))
    operation_intents = _normalize_profile_items(query_profile.get("operationIntents"))
    primary_theme_intents = _normalize_profile_items(query_profile.get("primaryThemeIntents"))
    secondary_theme_hints = _normalize_profile_items(query_profile.get("secondaryThemeHints"))
    market_primary_theme_hints = _normalize_profile_items(query_profile.get("marketPrimaryThemeHints"))
    market_secondary_theme_hints = _normalize_profile_items(query_profile.get("marketSecondaryThemeHints"))
    canonical_family_match = _skill_query_canonical_family_match(skill, query_profile)
    topic_tokens = list(query_profile.get("topicTokens") or [])
    document_sub_intent = str(query_profile.get("documentSubIntent") or "").strip().lower() or None
    no_artifact_anchor = not artifact_intents
    pure_theme_query = no_artifact_anchor and bool(primary_theme_intents)
    skill_authoring_candidate = (
        skill_class == "skill_authoring"
        or "skill_authoring" in capability_kinds
        or "persona_skill" in capability_artifacts
    )
    skill_authoring_query = "skill" in artifact_intents and (
        "create" in operation_intents
        or any(token in normalized_query for token in ("女娲", "造skill", "造 skill", "造人", "蒸馏", "create skill", "generate skill", "persona skill"))
    )

    artifact_match = False
    if artifact_intents:
        matched_artifacts = [item for item in artifact_intents if item in primary_artifact_types]
        if skill_authoring_query and skill_authoring_candidate and "skill" not in matched_artifacts:
            matched_artifacts.append("skill")
        if matched_artifacts:
            artifact_match = True
            has_query_signal = True
            score += 36 + (8 * len(matched_artifacts))
            score += _profile_artifact_anchor_bonus(profile, matched_artifacts)
        elif primary_artifact_types:
            mismatched = False
            for artifact_intent in artifact_intents:
                conflicting = _ARTIFACT_MISMATCH_GROUPS.get(artifact_intent, set())
                if conflicting.intersection(primary_artifact_types):
                    mismatched = True
                    break
            if mismatched:
                score -= 18
    if skill_authoring_query and skill_authoring_candidate:
        score += 30
        has_query_signal = True
    if canonical_family_match["matched"]:
        matched_families = list(canonical_family_match.get("matchedFamilies") or [])
        direct_matches = list(canonical_family_match.get("directMatchedFamilies") or [])
        exact_direct_matches = list(canonical_family_match.get("exactDirectMatchedFamilies") or [])
        family_bonus = 12 + min(int(canonical_family_match.get("bestDepth") or 0) * 4, 12)
        family_bonus += min(len(matched_families), 2) * 2
        if direct_matches:
            family_bonus += 10
        if exact_direct_matches:
            family_bonus += 8
        score += family_bonus
        has_query_signal = True
        if str(skill.get("sourceType") or "").strip() == "scoped_workspace":
            scoped_family_boost = 12
            if direct_matches:
                scoped_family_boost += 6
            if exact_direct_matches:
                scoped_family_boost += 6
            score += scoped_family_boost
    if "document" in artifact_intents and document_sub_intent:
        skill_document_sub_intent = _detect_skill_document_subintent(skill)
        if skill_document_sub_intent == document_sub_intent:
            artifact_match = True
            score += 28
            has_query_signal = True
            if document_sub_intent == "documentation" and skill_class in {"methodology_or_tutorial", "advisor_or_perspective"}:
                score += 14
        elif skill_document_sub_intent and skill_document_sub_intent != document_sub_intent:
            score -= 18

    operation_match = False
    if operation_intents:
        matched_operations = [item for item in operation_intents if item in primary_operations]
        if pure_theme_query:
            matched_operations = [item for item in matched_operations if item in {"advise", "analyze", "guide"}]
        if matched_operations:
            operation_match = True
            has_query_signal = True
            if pure_theme_query:
                score += 10 + (3 * len(matched_operations))
            else:
                score += 14 + (4 * len(matched_operations))
        elif skill_class in {"advisor_or_perspective", "methodology_or_tutorial"} and "advise" in operation_intents:
            operation_match = True
            has_query_signal = True
            score += 8

    theme_match = False
    matched_primary_themes = [item for item in primary_theme_intents if item in primary_themes]
    matched_secondary_tags = [item for item in secondary_theme_hints if item in secondary_theme_tags]
    matched_implied_themes = [item for item in primary_theme_intents if item in implied_primary_themes]
    if skill_class in _THEME_HEAVY_CLASSES:
        if matched_primary_themes:
            theme_match = True
            has_query_signal = True
            if no_artifact_anchor:
                score += 26 + (8 * len(matched_primary_themes)) + 12
            else:
                score += 6 + (2 * len(matched_primary_themes))
        elif matched_implied_themes:
            theme_match = True
            has_query_signal = True
            if no_artifact_anchor:
                score += 14 + (5 * len(matched_implied_themes)) + 6
            else:
                score += 4 + len(matched_implied_themes)
        if matched_secondary_tags:
            has_query_signal = True
            if no_artifact_anchor:
                score += 8 + (3 * len(matched_secondary_tags)) + 4
            else:
                score += 2 + len(matched_secondary_tags)
    else:
        if matched_primary_themes:
            has_query_signal = True
            score += 6 + (2 * len(matched_primary_themes))
        elif matched_implied_themes:
            has_query_signal = True
            score += 3 + len(matched_implied_themes)
        if matched_secondary_tags:
            has_query_signal = True
            score += 3 + len(matched_secondary_tags)

    if score > 0 and market_primary_theme_hints:
        weak_market_primary_matches = [item for item in market_primary_theme_hints if item in primary_themes or item in implied_primary_themes]
        if weak_market_primary_matches:
            score += 2 + min(len(weak_market_primary_matches), 2)
    if score > 0 and market_secondary_theme_hints:
        weak_market_secondary_matches = [item for item in market_secondary_theme_hints if item in secondary_theme_tags]
        if weak_market_secondary_matches:
            score += 1 + min(len(weak_market_secondary_matches), 2)

    if topic_tokens:
        topic_score = _score_text(
            query_tokens=topic_tokens,
            title=name or folder,
            description=" ".join(
                part
                for part in [
                    description,
                    cleaned_description,
                    " ".join(primary_artifact_types),
                    " ".join(primary_operations),
                    " ".join(secondary_artifact_hints),
                    " ".join(secondary_operation_hints),
                ]
                if str(part or "").strip()
            ),
        )
        score += min(topic_score, 18)
        if topic_score > 0:
            has_query_signal = True

    if not has_query_signal:
        return 0, artifact_match, operation_match

    confidence_bonus = int(round(confidence * 6))
    score += confidence_bonus
    score += int(round(theme_confidence * 4))

    if artifact_match and skill_class == "artifact_producer":
        score += 10
    if artifact_match and skill_class == "artifact_editor_or_analyzer":
        score += 6
    if artifact_intents and skill_class in {"advisor_or_perspective", "methodology_or_tutorial"} and not artifact_match:
        score -= 8
    if no_artifact_anchor and theme_match and skill_class in _THEME_HEAVY_CLASSES:
        score += 6

    return score, artifact_match, operation_match


def _score_mcp_server_entry(
    *,
    query_text: str,
    query_tokens: list[str],
    query_profile: dict[str, Any],
    server_name: str,
    items: list[Any],
    profile: dict[str, Any],
) -> int:
    tool_names = [_tool_name(tool) for tool in items if _tool_name(tool)]
    tool_descriptions = [_tool_description(tool) for tool in items if _tool_description(tool)]
    server_description = " | ".join(
        part
        for part in [
            f"MCP server {server_name}",
            " ".join(tool_names),
            " ".join(tool_descriptions),
            _build_family_profile_summary(profile),
        ]
        if str(part or "").strip()
    )
    return _score_dynamic_family_entry(
        query_text=query_text,
        query_tokens=query_tokens,
        query_profile=query_profile,
        family_name=server_name,
        description=server_description,
        profile=profile,
    )


def _truncate(text: str, limit: int = 100) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _single_line_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _tool_name(tool_ref: Any) -> str:
    return getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")).strip()


def _tool_description(tool_ref: Any) -> str:
    raw = getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or ""
    lines = str(raw).strip().splitlines()
    return lines[0] if lines else ""


def _tool_family_fingerprint(parts: Iterable[str]) -> str:
    normalized = "\n".join(
        str(part or "").strip()
        for part in parts
        if str(part or "").strip()
    )
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _tool_family_text_corpus(
    *,
    family_name: str,
    descriptions: Iterable[str],
    tool_names: Iterable[str],
    managed_channels: Iterable[str] | None = None,
) -> str:
    parts: list[str] = [str(family_name or "").strip()]
    parts.extend(str(item or "").strip() for item in tool_names if str(item or "").strip())
    parts.extend(str(item or "").strip() for item in descriptions if str(item or "").strip())
    parts.extend(str(item or "").strip() for item in list(managed_channels or []) if str(item or "").strip())
    return " ".join(part for part in parts if part)


def _score_grouped_synonym_matches(
    text: str,
    grouped_synonyms: dict[str, tuple[str, ...]],
) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {}
    matches: dict[str, list[str]] = {}
    for key, synonyms in grouped_synonyms.items():
        score = 0.0
        matched_terms: list[str] = []
        for synonym in synonyms:
            normalized = str(synonym or "").strip()
            if not normalized or not _text_matches_phrase(text, normalized):
                continue
            matched_terms.append(normalized)
            token_count = len(_tokenize(normalized))
            if re.search(r"[\u4e00-\u9fff]", normalized) or " " in normalized or token_count >= 2 or len(normalized) >= 8:
                score += 2.0
            else:
                score += 1.0
        if score > 0:
            scores[key] = score
            matches[key] = matched_terms
    return scores, matches


def _select_profile_keys(
    *,
    scores: dict[str, float],
    max_items: int,
    minimum: float,
) -> list[str]:
    ordered = sorted(scores.items(), key=lambda item: (-float(item[1]), item[0]))
    if not ordered:
        return []
    selected: list[str] = []
    for key, score in ordered[:max_items]:
        if float(score) < minimum:
            continue
        selected.append(str(key))
    return selected


def _detect_text_document_subintent_hints(text: str) -> list[str]:
    office_score = _weighted_text_hit_score(text, _QUERY_DOCUMENT_SUBINTENT_SYNONYMS.get("office_document", {}))
    documentation_score = _weighted_text_hit_score(text, _QUERY_DOCUMENT_SUBINTENT_SYNONYMS.get("documentation", {}))
    hints: list[str] = []
    if office_score > 0 and office_score >= documentation_score:
        hints.append("office_document")
    if documentation_score > 0 and documentation_score >= office_score:
        hints.append("documentation")
    return hints[:2]


def _has_family_advisory_signal(text: str) -> bool:
    return any(_text_matches_phrase(text, hint) for hint in _FAMILY_ADVISORY_HINTS)


def _has_integration_or_tooling_signal(text: str, *, managed_channels: Iterable[str] | None = None) -> bool:
    if any(str(item or "").strip() for item in list(managed_channels or [])):
        return True
    return any(_text_matches_phrase(text, hint) for hint in _INTEGRATION_OR_TOOLING_HINTS)


def _derive_tool_family_profile_rules(
    *,
    family_name: str,
    descriptions: list[str],
    tool_names: list[str],
    managed_channels: list[str] | None = None,
) -> dict[str, Any]:
    text = _tool_family_text_corpus(
        family_name=family_name,
        descriptions=descriptions,
        tool_names=tool_names,
        managed_channels=managed_channels,
    )
    artifact_scores, artifact_matches = _score_grouped_synonym_matches(text, _QUERY_ARTIFACT_INTENT_SYNONYMS)
    operation_scores, operation_matches = _score_grouped_synonym_matches(text, _QUERY_OPERATION_INTENT_SYNONYMS)
    theme_scores, theme_matches = _score_grouped_synonym_matches(text, _QUERY_PRIMARY_THEME_SYNONYMS)
    secondary_scores, secondary_matches = _score_grouped_synonym_matches(text, _QUERY_SECONDARY_THEME_SYNONYMS)
    document_subintent_hints = _detect_text_document_subintent_hints(text)

    primary_artifact_types = _select_profile_keys(scores=artifact_scores, max_items=2, minimum=2.0)
    primary_operations = _select_profile_keys(scores=operation_scores, max_items=3, minimum=1.0)
    primary_themes = _select_profile_keys(scores=theme_scores, max_items=2, minimum=2.0)
    secondary_theme_tags = _select_profile_keys(scores=secondary_scores, max_items=5, minimum=1.0)

    advisory_signal = _has_family_advisory_signal(text)
    integration_signal = _has_integration_or_tooling_signal(text, managed_channels=managed_channels)

    skill_class_like = "integration_or_tooling"
    if advisory_signal:
        if any(_text_matches_phrase(text, hint) for hint in ("perspective", "advisor", "advisory", "视角", "顾问", "strategy", "决策")):
            skill_class_like = "advisor_or_perspective"
        else:
            skill_class_like = "methodology_or_tutorial"
    elif primary_themes and any(item in primary_operations for item in ("advise", "guide", "analyze")):
        skill_class_like = "methodology_or_tutorial"
    elif primary_artifact_types:
        if "create" in primary_operations:
            skill_class_like = "artifact_producer"
        elif any(item in primary_operations for item in ("edit", "analyze", "convert")):
            skill_class_like = "artifact_editor_or_analyzer"
        else:
            skill_class_like = "artifact_producer"
    elif integration_signal:
        skill_class_like = "integration_or_tooling"

    if skill_class_like in {"advisor_or_perspective", "methodology_or_tutorial", "integration_or_tooling"} and not primary_artifact_types:
        primary_artifact_types = []

    evidence_strength = max(
        list(artifact_scores.values())
        + list(operation_scores.values())
        + list(theme_scores.values())
        + list(secondary_scores.values())
        + [0.0]
    )
    confidence = 0.18
    if primary_artifact_types:
        confidence += 0.2 + (0.05 * min(len(primary_artifact_types), 2))
    if primary_operations:
        confidence += 0.12 + (0.04 * min(len(primary_operations), 3))
    if document_subintent_hints:
        confidence += 0.08
    if primary_themes:
        confidence += 0.12 + (0.04 * min(len(primary_themes), 2))
    if secondary_theme_tags:
        confidence += 0.06 + (0.02 * min(len(secondary_theme_tags), 3))
    if skill_class_like in _THEME_HEAVY_CLASSES:
        confidence += 0.08
    elif skill_class_like == "integration_or_tooling":
        confidence += 0.05
    if evidence_strength >= 6.0:
        confidence += 0.12
    elif evidence_strength >= 3.5:
        confidence += 0.06

    return {
        "skillClassLike": skill_class_like,
        "primaryArtifactTypes": primary_artifact_types,
        "primaryOperations": primary_operations,
        "documentSubIntentHints": document_subintent_hints,
        "primaryThemes": primary_themes,
        "secondaryThemeTags": secondary_theme_tags,
        "profileConfidence": round(max(0.0, min(confidence, 0.98)), 3),
        "profileSource": "rules",
        "evidenceSignals": {
            "artifactMatches": {key: artifact_matches.get(key, []) for key in primary_artifact_types},
            "operationMatches": {key: operation_matches.get(key, []) for key in primary_operations},
            "primaryThemeMatches": {key: theme_matches.get(key, []) for key in primary_themes},
            "secondaryThemeMatches": {key: secondary_matches.get(key, []) for key in secondary_theme_tags},
        },
    }


def _should_attempt_dynamic_profile_inference(*, base_profile: dict[str, Any], allow_llm: bool) -> bool:
    if not allow_llm:
        return False
    confidence = float(base_profile.get("profileConfidence") or 0.0)
    primary_themes = list(base_profile.get("primaryThemes") or [])
    document_hints = list(base_profile.get("documentSubIntentHints") or [])
    skill_class_like = str(base_profile.get("skillClassLike") or "").strip().lower()
    if len(document_hints) > 1:
        return True
    if skill_class_like in _THEME_HEAVY_CLASSES and not primary_themes:
        return True
    if confidence < 0.48:
        return True
    if len(primary_themes) > 1 and confidence < 0.7:
        return True
    return False


def _normalize_dynamic_profile_with_fallback(
    *,
    payload: dict[str, Any] | None,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(fallback)
    if not isinstance(payload, dict):
        return normalized
    allowed_classes = {
        "artifact_producer",
        "artifact_editor_or_analyzer",
        "workflow_or_script",
        "methodology_or_tutorial",
        "advisor_or_perspective",
        "integration_or_tooling",
    }
    skill_class_like = str(payload.get("skillClassLike") or "").strip()
    if skill_class_like in allowed_classes:
        normalized["skillClassLike"] = skill_class_like
    artifact_types = [
        str(item).strip()
        for item in list(payload.get("primaryArtifactTypes") or [])
        if str(item).strip() in _QUERY_ARTIFACT_INTENT_SYNONYMS
    ][:2]
    operations = [
        str(item).strip()
        for item in list(payload.get("primaryOperations") or [])
        if str(item).strip() in _QUERY_OPERATION_INTENT_SYNONYMS
    ][:3]
    document_hints = [
        str(item).strip()
        for item in list(payload.get("documentSubIntentHints") or [])
        if str(item).strip() in {"office_document", "documentation"}
    ][:2]
    primary_themes = [
        str(item).strip()
        for item in list(payload.get("primaryThemes") or [])
        if str(item).strip() in _QUERY_PRIMARY_THEME_SYNONYMS
    ][:2]
    secondary_theme_tags = [
        str(item).strip()
        for item in list(payload.get("secondaryThemeTags") or [])
        if str(item).strip() in _QUERY_SECONDARY_THEME_SYNONYMS
    ][:5]
    if artifact_types:
        normalized["primaryArtifactTypes"] = artifact_types
    if operations:
        normalized["primaryOperations"] = operations
    if document_hints:
        normalized["documentSubIntentHints"] = document_hints
    if primary_themes:
        normalized["primaryThemes"] = primary_themes
    if secondary_theme_tags:
        normalized["secondaryThemeTags"] = secondary_theme_tags
    try:
        confidence = float(payload.get("profileConfidence"))
        normalized["profileConfidence"] = round(max(0.0, min(confidence, 0.99)), 3)
    except (TypeError, ValueError):
        pass
    normalized["profileSource"] = "llm_assisted"
    normalized.setdefault("evidenceSignals", {})
    return normalized


def _build_family_profile_summary(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    skill_class_like = str(profile.get("skillClassLike") or "").strip()
    if skill_class_like:
        parts.append(f"class={skill_class_like}")
    if list(profile.get("primaryArtifactTypes") or []):
        parts.append(f"artifacts={','.join(profile.get('primaryArtifactTypes') or [])}")
    if list(profile.get("documentSubIntentHints") or []):
        parts.append(f"documentSubIntent={','.join(profile.get('documentSubIntentHints') or [])}")
    if list(profile.get("primaryOperations") or []):
        parts.append(f"operations={','.join(profile.get('primaryOperations') or [])}")
    if list(profile.get("primaryThemes") or []):
        parts.append(f"themes={','.join(profile.get('primaryThemes') or [])}")
    return " | ".join(parts)


def _family_profile_text_for_scoring(
    *,
    family_name: str,
    description: str,
    profile: dict[str, Any],
) -> str:
    return " ".join(
        part
        for part in [
            str(family_name or "").strip(),
            str(description or "").strip(),
            " ".join(str(item).strip() for item in list(profile.get("primaryArtifactTypes") or []) if str(item).strip()),
            " ".join(str(item).strip() for item in list(profile.get("primaryOperations") or []) if str(item).strip()),
            " ".join(str(item).strip() for item in list(profile.get("documentSubIntentHints") or []) if str(item).strip()),
            " ".join(str(item).strip() for item in list(profile.get("primaryThemes") or []) if str(item).strip()),
            " ".join(str(item).strip() for item in list(profile.get("secondaryThemeTags") or []) if str(item).strip()),
        ]
        if part
    )


def _has_direct_dynamic_theme_match(profile: dict[str, Any], query_profile: dict[str, Any]) -> bool:
    query_themes = set(_normalize_profile_items(query_profile.get("primaryThemeIntents")))
    family_themes = set(_normalize_profile_items(profile.get("primaryThemes")))
    return bool(query_themes.intersection(family_themes))


def _score_dynamic_family_entry(
    *,
    query_text: str,
    query_tokens: list[str],
    query_profile: dict[str, Any],
    family_name: str,
    description: str,
    profile: dict[str, Any],
) -> int:
    scoring_text = _family_profile_text_for_scoring(
        family_name=family_name,
        description=description,
        profile=profile,
    )
    score = _score_text(query_tokens=query_tokens, title=family_name, description=scoring_text)
    has_query_signal = score > 0
    normalized_query = str(query_text or "").strip().lower()
    if str(family_name or "").strip().lower() in normalized_query:
        score += 10
        has_query_signal = True

    artifact_intents = _normalize_profile_items(query_profile.get("artifactIntents"))
    operation_intents = _normalize_profile_items(query_profile.get("operationIntents"))
    primary_theme_intents = _normalize_profile_items(query_profile.get("primaryThemeIntents"))
    secondary_theme_hints = _normalize_profile_items(query_profile.get("secondaryThemeHints"))
    topic_tokens = list(query_profile.get("topicTokens") or [])
    document_sub_intent = str(query_profile.get("documentSubIntent") or "").strip().lower() or None
    no_artifact_anchor = not artifact_intents
    pure_theme_query = no_artifact_anchor and bool(primary_theme_intents)

    primary_artifact_types = _normalize_profile_items(profile.get("primaryArtifactTypes"))
    primary_operations = _normalize_profile_items(profile.get("primaryOperations"))
    document_subintent_hints = _normalize_profile_items(profile.get("documentSubIntentHints"))
    primary_themes = _normalize_profile_items(profile.get("primaryThemes"))
    secondary_theme_tags = _normalize_profile_items(profile.get("secondaryThemeTags"))
    skill_class_like = str(profile.get("skillClassLike") or "").strip().lower()
    confidence = float(profile.get("profileConfidence") or 0.0)

    artifact_match = False
    if artifact_intents:
        matched_artifacts = [item for item in artifact_intents if item in primary_artifact_types]
        if matched_artifacts:
            artifact_match = True
            has_query_signal = True
            score += 26 + (6 * len(matched_artifacts))
        elif primary_artifact_types:
            mismatched = False
            for artifact_intent in artifact_intents:
                conflicting = _ARTIFACT_MISMATCH_GROUPS.get(artifact_intent, set())
                if conflicting.intersection(primary_artifact_types):
                    mismatched = True
                    break
            if mismatched:
                score -= 16
    if "document" in artifact_intents and document_sub_intent:
        if document_sub_intent in document_subintent_hints:
            artifact_match = True
            has_query_signal = True
            score += 24
        elif document_subintent_hints:
            score -= 16

    if operation_intents:
        matched_operations = [item for item in operation_intents if item in primary_operations]
        if pure_theme_query:
            matched_operations = [item for item in matched_operations if item in {"advise", "analyze", "guide"}]
        if matched_operations:
            has_query_signal = True
            score += (9 if pure_theme_query else 12) + (3 * len(matched_operations))

    theme_match = False
    matched_primary_themes = [item for item in primary_theme_intents if item in primary_themes]
    matched_secondary_tags = [item for item in secondary_theme_hints if item in secondary_theme_tags]
    if skill_class_like in _THEME_HEAVY_CLASSES:
        if matched_primary_themes:
            theme_match = True
            has_query_signal = True
            score += (24 if no_artifact_anchor else 6) + (6 * len(matched_primary_themes))
        if matched_secondary_tags:
            has_query_signal = True
            score += (10 if no_artifact_anchor else 3) + (2 * len(matched_secondary_tags))
    else:
        if matched_primary_themes:
            has_query_signal = True
            score += 5 + (2 * len(matched_primary_themes))
        if matched_secondary_tags:
            has_query_signal = True
            score += 2 + len(matched_secondary_tags)

    if topic_tokens:
        topic_score = _score_text(query_tokens=topic_tokens, title=family_name, description=scoring_text)
        score += min(topic_score, 14)
        if topic_score > 0:
            has_query_signal = True

    if pure_theme_query and skill_class_like not in _THEME_HEAVY_CLASSES:
        score -= 10
    if artifact_intents and skill_class_like in _THEME_HEAVY_CLASSES and not artifact_match:
        score -= 8
    if no_artifact_anchor and theme_match and skill_class_like in _THEME_HEAVY_CLASSES:
        score += 6

    if not has_query_signal:
        return 0
    score += int(round(confidence * 5))
    return score


def _score_dynamic_family_fallback_entry(
    *,
    query_text: str,
    query_tokens: list[str],
    query_profile: dict[str, Any],
    family_name: str,
    description: str,
    profile: dict[str, Any],
) -> int:
    query_themes = set(_normalize_profile_items(query_profile.get("primaryThemeIntents")))
    if not query_themes:
        return 0
    skill_class_like = str(profile.get("skillClassLike") or "").strip().lower()
    profile_text = _family_profile_text_for_scoring(
        family_name=family_name,
        description=description,
        profile=profile,
    )
    advisory_signal = _has_family_advisory_signal(profile_text)
    collaboration_signal = any(
        _text_matches_phrase(profile_text, hint)
        for hint in ("coauthor", "collaboration", "knowledge", "wiki", "docs", "文档协作", "知识库", "协作")
    )
    if skill_class_like not in {*_THEME_HEAVY_CLASSES, "integration_or_tooling"}:
        return 0
    if skill_class_like == "integration_or_tooling" and not collaboration_signal:
        return 0

    primary_themes = set(_normalize_profile_items(profile.get("primaryThemes")))
    secondary_theme_tags = set(_normalize_profile_items(profile.get("secondaryThemeTags")))
    matched_primary_themes = query_themes.intersection(primary_themes)
    implied_themes = {
        implied_theme
        for tag in secondary_theme_tags
        for implied_theme in _SECONDARY_THEME_PRIMARY_MAP.get(tag, ())
    }
    matched_implied_themes = query_themes.intersection(implied_themes)
    lexical_score = _score_text(query_tokens=query_tokens, title=family_name, description=profile_text)

    score = 0
    if advisory_signal:
        score += 18
    if collaboration_signal:
        score += 10
    if matched_primary_themes:
        score += 14 + (4 * len(matched_primary_themes))
    if matched_implied_themes:
        score += 8 + (3 * len(matched_implied_themes))
    if lexical_score > 0:
        score += min(lexical_score, 10)
    score += int(round(float(profile.get("profileConfidence") or 0.0) * 4))
    if skill_class_like in _THEME_HEAVY_CLASSES:
        score += 4
    return score


def _merge_keepalive_keys(primary: list[str], keepalive: list[str], limit: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for entry in [*keepalive, *primary]:
        normalized = str(entry or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
        if limit > 0 and len(merged) >= limit:
            break
    return merged


def _is_mcp_tool(tool_ref: Any) -> bool:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return bool(metadata.get("server_name"))


def _build_skill_rerank_document(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"skill: {str(skill.get('name') or skill.get('folder') or '').strip()}",
            f"description: {str(skill.get('description') or '').strip() or '暂无说明。'}",
            f"path: {str(skill.get('path') or '').strip()}",
        ]
    ).strip()


def _skill_entry_payload(skill: dict[str, Any]) -> dict[str, Any]:
    direct_canonical_families, canonical_families = _skill_canonical_families(skill)
    return {
        "skillId": str(skill.get("skillId") or "").strip(),
        "skillName": str(skill.get("skillName") or skill.get("name") or skill.get("folder") or "").strip(),
        "description": str(skill.get("description") or "").strip(),
        "skillRoot": str(skill.get("skillRoot") or skill.get("path") or "").strip(),
        "instructionPath": str(skill.get("instructionPath") or "").strip(),
        "sourceType": str(skill.get("sourceType") or "").strip(),
        "visibility": str(skill.get("visibility") or "").strip(),
        "workspacePath": str(skill.get("workspacePath") or "").strip(),
        "workspaceId": str(skill.get("workspaceId") or "").strip(),
        "projectId": str(skill.get("projectId") or "").strip(),
        "rootPath": str(skill.get("rootPath") or skill.get("skillRoot") or skill.get("path") or "").strip(),
        "referencesDir": str(skill.get("referencesDir") or "").strip(),
        "scriptsDir": str(skill.get("scriptsDir") or "").strip(),
        "assetsDir": str(skill.get("assetsDir") or "").strip(),
        "templatesDir": str(skill.get("templatesDir") or "").strip(),
        "examplesDir": str(skill.get("examplesDir") or "").strip(),
        "availableFiles": [
            str(item).strip()
            for item in list(skill.get("availableFiles") or [])
            if str(item).strip()
        ],
        "aliases": [str(item).strip() for item in list(skill.get("aliases") or []) if str(item).strip()],
        "triggers": [str(item).strip() for item in list(skill.get("triggers") or []) if str(item).strip()],
        "keywords": [str(item).strip() for item in list(skill.get("keywords") or []) if str(item).strip()],
        "tags": [str(item).strip() for item in list(skill.get("tags") or []) if str(item).strip()],
        "directCanonicalFamilies": direct_canonical_families,
        "canonicalFamilies": canonical_families,
        "capabilityProfile": dict(skill.get("capabilityProfile") or {}),
        "themeProfile": dict(skill.get("themeProfile") or {}),
        "capabilityTags": dict(skill.get("capabilityTags") or {}),
        "safety": dict(skill.get("safety") or {}),
    }


def _build_mcp_rerank_document(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return "\n".join(
        [
            f"tool: {_tool_name(tool_ref)}",
            f"server: {str(metadata.get('server_name') or '').strip() or 'unknown'}",
            f"description: {_tool_description(tool_ref) or '暂无说明。'}",
        ]
    ).strip()


def _mcp_tool_server_name(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("server_name") or "unknown").strip() or "unknown"


def _normalize_tool_name_parts(tool_name: str) -> list[str]:
    return [part for part in str(tool_name or "").strip().split("_") if part]


def _family_prefix_for_tool(*, tool_name: str, sibling_tool_names: Iterable[str]) -> str | None:
    parts = _normalize_tool_name_parts(tool_name)
    if len(parts) < 2:
        return None
    normalized_siblings = [str(item or "").strip() for item in sibling_tool_names if str(item or "").strip()]
    if len(normalized_siblings) < 2:
        return None
    for width in range(len(parts) - 1, 0, -1):
        prefix = "_".join(parts[:width])
        matches = [item for item in normalized_siblings if item == prefix or item.startswith(f"{prefix}_")]
        if len(matches) >= 2:
            return prefix
    return None


def _mcp_tool_identity(tool_ref: Any) -> str:
    return f"{_mcp_tool_server_name(tool_ref)}::{_tool_name(tool_ref)}"


def _mcp_tool_family_prefix(tool_ref: Any, sibling_tool_names: Iterable[str]) -> str | None:
    return _family_prefix_for_tool(
        tool_name=_tool_name(tool_ref),
        sibling_tool_names=sibling_tool_names,
    )


def _mcp_tool_family_key(tool_ref: Any, sibling_tool_names: Iterable[str]) -> str:
    server_name = _mcp_tool_server_name(tool_ref)
    family_prefix = _mcp_tool_family_prefix(tool_ref, sibling_tool_names)
    if family_prefix:
        return f"{server_name}::{family_prefix}"
    return f"{server_name}::{_tool_name(tool_ref)}"


def _mcp_tool_family_title(tool_ref: Any, sibling_tool_names: Iterable[str]) -> str:
    family_prefix = _mcp_tool_family_prefix(tool_ref, sibling_tool_names)
    return family_prefix or _tool_name(tool_ref) or _mcp_tool_server_name(tool_ref)


def _mcp_family_payload(
    family_key: str,
    items: list[Any],
    mcp_sibling_names_by_server: dict[str, list[str]],
) -> dict[str, Any]:
    ordered_items = sorted(
        list(items or []),
        key=lambda tool: (
            _mcp_tool_server_name(tool).lower(),
            _tool_name(tool).lower(),
        ),
    )
    if not ordered_items:
        return {
            "familyKey": family_key,
            "serverName": "unknown",
            "title": family_key,
            "toolCount": 0,
            "toolNames": [],
            "descriptions": [],
        }
    seed_tool = ordered_items[0]
    server_name = _mcp_tool_server_name(seed_tool)
    sibling_names = mcp_sibling_names_by_server.get(server_name, [])
    descriptions = [
        _tool_description(tool)
        for tool in ordered_items
        if _tool_description(tool)
    ]
    unique_descriptions: list[str] = []
    seen_descriptions: set[str] = set()
    for item in descriptions:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen_descriptions:
            continue
        seen_descriptions.add(normalized)
        unique_descriptions.append(normalized)
    return {
        "familyKey": family_key,
        "serverName": server_name,
        "title": _mcp_tool_family_title(seed_tool, sibling_names),
        "toolCount": len(ordered_items),
        "toolNames": [_tool_name(tool) for tool in ordered_items],
        "descriptions": unique_descriptions[:4],
    }


def _mcp_server_payload(server_name: str, items: list[Any]) -> dict[str, Any]:
    normalized_server_name = str(server_name or "").strip() or "unknown"
    ordered_items = sorted(
        list(items or []),
        key=lambda tool: (
            _mcp_tool_server_name(tool).lower(),
            _tool_name(tool).lower(),
        ),
    )
    tool_entries: list[dict[str, str]] = []
    unique_descriptions: list[str] = []
    seen_descriptions: set[str] = set()
    for tool in ordered_items:
        tool_name = _tool_name(tool)
        tool_description = _tool_description(tool)
        if tool_name:
            tool_entries.append(
                {
                    "name": tool_name,
                    "description": tool_description,
                }
            )
        normalized_description = str(tool_description or "").strip()
        if normalized_description and normalized_description not in seen_descriptions:
            seen_descriptions.add(normalized_description)
            unique_descriptions.append(normalized_description)
    return {
        "serverKey": normalized_server_name,
        "familyKey": normalized_server_name,
        "serverName": normalized_server_name,
        "title": normalized_server_name,
        "toolCount": len(tool_entries),
        "toolNames": [item["name"] for item in tool_entries],
        "tools": tool_entries,
        "descriptions": unique_descriptions,
    }


def _should_enable_cross_runtime_escape(query_tokens: list[str]) -> bool:
    if not query_tokens:
        return False
    return any(token in _CROSS_RUNTIME_ESCAPE_TOKENS for token in query_tokens)


def _extension_runtime_source(node: str = "extensions_runtime") -> RuntimeSource:
    runtime_context = get_runtime_context()
    return RuntimeSource(
        plane="engine",
        component="extensions_runtime",
        node=node,
        agent_id=str(runtime_context.get("agent_id") or "supervisor"),
    )


class ExtensionsRuntimeService:
    def __init__(self) -> None:
        self._startup_state = "cold"
        self._snapshot_freshness = "cold"
        self._last_refresh_at: str | None = None
        self._last_refresh_error: str | None = None
        self._cached_catalog: dict[str, Any] | None = None
        self._cached_health: dict[str, Any] | None = None
        self._background_refresh_task: asyncio.Task | None = None
        self._skills_inventory_watcher_task: asyncio.Task | None = None
        self._mcp_inventory_watcher_task: asyncio.Task | None = None
        self._refresh_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._route_cache: dict[str, tuple[float, ExtensionRouteBundle]] = {}
        self._route_cache_ttl_seconds = 20.0
        self._last_skill_inventory_change: dict[str, Any] | None = None
        self._last_mcp_inventory_change: dict[str, Any] | None = None
        self._last_inventory_guard_at: float = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._blocked_skill_records: list[dict[str, Any]] = []
        self._mcp_family_profile_cache: dict[str, dict[str, Any]] = {}

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _clear_dynamic_family_profile_caches(self) -> None:
        self._mcp_family_profile_cache.clear()

    def _skill_inventory_status(self) -> dict[str, Any]:
        try:
            return dict(SkillLoader.get_startup_status() or {})
        except Exception:
            return {}

    def _mcp_inventory_status(self) -> dict[str, Any]:
        try:
            return dict(mcp_manager.get_startup_status() or {})
        except Exception:
            return {}

    def _inventory_revision_key(self, skill_inventory: dict[str, Any] | None = None) -> str:
        skill_status = self._skill_inventory_status()
        mcp_status = self._mcp_inventory_status()
        skill_revision = str(
            (skill_inventory or {}).get("revision")
            or skill_status.get("revision")
            or skill_status.get("fingerprint")
            or "skills:cold"
        )
        visible_root_signature = str(
            (skill_inventory or {}).get("visibleRootSignature")
            or skill_status.get("rootSignature")
            or "roots:cold"
        )
        visible_root_revision_key = str(
            (skill_inventory or {}).get("visibleRootRevisionKey")
            or skill_status.get("visibleRootRevisionKey")
            or visible_root_signature
        )
        mcp_revision = str(
            mcp_status.get("inventoryRevision")
            or getattr(mcp_manager, "get_inventory_revision", lambda: "mcp:cold")()
            or "mcp:cold"
        )
        return (
            f"skills:{skill_revision}|roots:{visible_root_signature}"
            f"|visible:{visible_root_revision_key}|mcp:{mcp_revision}"
        )

    def _resolve_visible_skill_descriptors(
        self,
        *,
        include_scoped: bool,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        return SkillLoader.resolve_root_descriptors(
            include_scoped=include_scoped,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
            runtime_kind=runtime_kind,
        )

    def _apply_inventory_freshness_mode(
        self,
        *,
        freshness_mode: str,
        reason: str,
        include_scoped: bool,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> dict[str, Any]:
        skill_context = self._resolve_skill_loader_context(
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
            runtime_kind=runtime_kind,
        )
        visible_descriptors = self._resolve_visible_skill_descriptors(
            include_scoped=include_scoped,
            session_id=skill_context.get("session_id"),
            explicit_workspace_id=skill_context.get("explicit_workspace_id"),
            explicit_workspace_path=skill_context.get("explicit_workspace_path"),
            explicit_project_id=skill_context.get("explicit_project_id"),
            runtime_kind=skill_context.get("runtime_kind"),
        )
        skill_status = self._skill_inventory_status()
        dirty_visible_roots = list(
            SkillLoader._dirty_root_paths_for_descriptors(visible_descriptors)  # noqa: SLF001
        )
        snapshot_freshness = str(skill_status.get("snapshotFreshness") or "cold")
        inventory_ready_state = str(skill_status.get("startupState") or "cold")
        barrier_applied = freshness_mode == _INVENTORY_FRESHNESS_GUARDED
        wait_budget_ms = 0
        wait_started_at = time.perf_counter()

        if barrier_applied:
            if inventory_ready_state == "cold" or not skill_status.get("skillCount"):
                wait_budget_ms = 1200
            elif dirty_visible_roots or inventory_ready_state == "refreshing" or snapshot_freshness != "live":
                wait_budget_ms = 800

            deadline = time.perf_counter() + (wait_budget_ms / 1000.0) if wait_budget_ms > 0 else 0.0
            if wait_budget_ms > 0 and skill_status.get("backgroundRefreshInProgress"):
                while time.perf_counter() < deadline:
                    current_status = self._skill_inventory_status()
                    if not current_status.get("backgroundRefreshInProgress"):
                        break
                    time.sleep(0.05)
                skill_status = self._skill_inventory_status()
                inventory_ready_state = str(skill_status.get("startupState") or inventory_ready_state or "cold")
                snapshot_freshness = str(skill_status.get("snapshotFreshness") or snapshot_freshness or "cold")
                dirty_visible_roots = list(
                    SkillLoader._dirty_root_paths_for_descriptors(visible_descriptors)  # noqa: SLF001
                )

            if wait_budget_ms > 0 and (
                inventory_ready_state in {"cold", "refreshing"} or dirty_visible_roots
            ):
                remaining_ms = max(0, int((deadline - time.perf_counter()) * 1000))
                if remaining_ms > 0:
                    skill_change = SkillLoader.refresh_root_descriptors_if_changed(
                        visible_descriptors,
                        compare_existing=False,
                        timeout_ms=remaining_ms,
                    )
                    if skill_change.get("changed"):
                        self._last_skill_inventory_change = {
                            **skill_change,
                            "changedAt": self._now_iso(),
                            "reason": reason,
                        }
                        self._cached_catalog = None
                        self._cached_health = None
                skill_status = self._skill_inventory_status()
                inventory_ready_state = str(skill_status.get("startupState") or inventory_ready_state or "cold")
                snapshot_freshness = str(skill_status.get("snapshotFreshness") or snapshot_freshness or "cold")
                dirty_visible_roots = list(
                    SkillLoader._dirty_root_paths_for_descriptors(visible_descriptors)  # noqa: SLF001
                )

        inventory_barrier_wait_ms = round((time.perf_counter() - wait_started_at) * 1000, 2) if barrier_applied else 0.0
        inventory_barrier_timed_out = bool(barrier_applied and wait_budget_ms > 0 and dirty_visible_roots)
        exclude_root_paths = set(dirty_visible_roots) if inventory_barrier_timed_out else set()
        return {
            "skillContext": skill_context,
            "visibleDescriptors": visible_descriptors,
            "visibleRootSignature": SkillLoader._root_descriptors_signature(visible_descriptors),  # noqa: SLF001
            "visibleRootRevisionKey": SkillLoader._visible_root_revision_key(visible_descriptors),  # noqa: SLF001
            "inventoryReadyState": inventory_ready_state,
            "snapshotFreshness": snapshot_freshness,
            "inventoryBarrierApplied": barrier_applied,
            "inventoryBarrierWaitMs": inventory_barrier_wait_ms,
            "inventoryBarrierTimedOut": inventory_barrier_timed_out,
            "dirtyVisibleRoots": dirty_visible_roots,
            "excludeRootPaths": exclude_root_paths,
            "waitBudgetMs": wait_budget_ms,
        }

    def _cache_path(self) -> Path:
        configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_CACHE_FILE") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return extensions_runtime_cache_path()

    def _legacy_cache_path(self) -> Path:
        return legacy_extensions_runtime_cache_path()

    def _controls_payload(self) -> list[dict[str, str]]:
        return [
            {"id": "refresh", "label": "刷新扩展"},
            {"id": "rebuild_catalog", "label": "重建目录"},
            {"id": "retry_subsystem", "label": "重试子系统"},
            {"id": "acknowledge_block", "label": "确认阻断"},
        ]

    def _recent_blocked_skill_records(self) -> list[dict[str, Any]]:
        return list(self._blocked_skill_records[-12:])

    def _build_mcp_server_families(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        family_map: dict[str, list[dict[str, Any]]] = {}
        sibling_names = [str(tool.get("name") or "").strip() for tool in tools if str(tool.get("name") or "").strip()]
        for tool in tools:
            tool_name = str(tool.get("name") or "").strip()
            if not tool_name:
                continue
            family_key = _family_prefix_for_tool(tool_name=tool_name, sibling_tool_names=sibling_names) or tool_name
            family_entry = {
                "name": tool_name,
                "description": str(tool.get("description") or "").strip(),
            }
            family_map.setdefault(family_key, []).append(family_entry)
        families: list[dict[str, Any]] = []
        for family_key, family_tools in sorted(family_map.items(), key=lambda item: item[0].lower()):
            families.append(
                {
                    "key": family_key,
                    "title": family_key.replace("_", " "),
                    "toolCount": len(family_tools),
                    "tools": sorted(family_tools, key=lambda item: str(item.get("name") or "").lower()),
                }
            )
        return families

    def _cached_skill_entries(self) -> list[dict[str, Any]]:
        cached_items = list((((self._cached_catalog or {}).get("skills") or {}).get("items") or []))
        normalized: list[dict[str, Any]] = []
        for item in cached_items:
            if not isinstance(item, dict):
                continue
            entry = dict(item.get("entry") or {})
            normalized.append(
                {
                    "skillId": str(item.get("skillId") or entry.get("skillId") or "").strip(),
                    "name": str(item.get("name") or entry.get("skillName") or "").strip(),
                    "folder": str(item.get("name") or entry.get("skillName") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "path": str(item.get("path") or entry.get("skillRoot") or "").strip(),
                    "skillName": str(entry.get("skillName") or item.get("name") or "").strip(),
                    "skillRoot": str(entry.get("skillRoot") or item.get("path") or "").strip(),
                    "instructionPath": str(entry.get("instructionPath") or "").strip(),
                    "sourceType": str(item.get("sourceType") or entry.get("sourceType") or "").strip(),
                    "visibility": str(item.get("visibility") or entry.get("visibility") or "").strip(),
                    "workspacePath": str(item.get("workspacePath") or entry.get("workspacePath") or "").strip(),
                    "workspaceId": str(item.get("workspaceId") or entry.get("workspaceId") or "").strip(),
                    "projectId": str(item.get("projectId") or entry.get("projectId") or "").strip(),
                    "rootPath": str(item.get("rootPath") or entry.get("rootPath") or entry.get("skillRoot") or item.get("path") or "").strip(),
                    "referencesDir": str(entry.get("referencesDir") or "").strip(),
                    "scriptsDir": str(entry.get("scriptsDir") or "").strip(),
                    "assetsDir": str(entry.get("assetsDir") or "").strip(),
                    "templatesDir": str(entry.get("templatesDir") or "").strip(),
                    "examplesDir": str(entry.get("examplesDir") or "").strip(),
                    "availableFiles": list(entry.get("availableFiles") or []),
                    "capabilityTags": dict(entry.get("capabilityTags") or item.get("capabilityTags") or {}),
                    "safety": dict(entry.get("safety") or item.get("safety") or {}),
                }
            )
        return normalized

    def _resolve_skill_loader_context(
        self,
        *,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> dict[str, Any]:
        context_payload = self._resolve_event_context()
        return {
            "session_id": session_id if session_id is not None else str(context_payload.get("session_id") or "").strip() or None,
            "explicit_workspace_id": explicit_workspace_id if explicit_workspace_id is not None else str(context_payload.get("workspace_id") or "").strip() or None,
            "explicit_workspace_path": explicit_workspace_path if explicit_workspace_path is not None else str(context_payload.get("workspace_path") or "").strip() or None,
            "explicit_project_id": explicit_project_id if explicit_project_id is not None else str(context_payload.get("project_id") or "").strip() or None,
            "runtime_kind": runtime_kind if runtime_kind is not None else str(context_payload.get("runtime_kind") or "chat").strip() or "chat",
        }

    def _resolve_skill_inventory(
        self,
        *,
        force_refresh: bool = False,
        prefer_cached_ready_inventory: bool = False,
        include_scoped: bool = True,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
        exclude_root_paths: set[str] | None = None,
    ) -> dict[str, Any]:
        skill_context = self._resolve_skill_loader_context(
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
            runtime_kind=runtime_kind,
        )
        has_scoped_context = include_scoped and any(
            skill_context.get(key)
            for key in ("session_id", "explicit_workspace_id", "explicit_workspace_path", "explicit_project_id")
        )
        if (
            prefer_cached_ready_inventory
            and self._cached_catalog is not None
            and self._startup_state == "refreshing"
            and not force_refresh
            and not has_scoped_context
            and not include_scoped
        ):
            cached_entries = self._cached_skill_entries()
            if cached_entries:
                skills_payload = dict((self._cached_catalog or {}).get("skills") or {})
                root_descriptors = list(skills_payload.get("rootDescriptors") or [])
                roots = [str(item.get("rootPath") or "") for item in root_descriptors] or list(skills_payload.get("roots") or [])
                return {
                    "registry": {
                        str(item.get("skillId") or item.get("skillRoot") or item.get("skillName") or ""): item
                        for item in cached_entries
                        if str(item.get("skillId") or item.get("skillRoot") or item.get("skillName") or "").strip()
                    },
                    "items": cached_entries,
                    "rootDescriptors": root_descriptors,
                    "roots": roots,
                    "fingerprint": str(skills_payload.get("fingerprint") or "").strip(),
                    "revision": str(skills_payload.get("revision") or skills_payload.get("fingerprint") or "").strip(),
                    "recentSkillDiscovery": list(skills_payload.get("recentSkillDiscovery") or []),
                }
        return SkillLoader.get_inventory(
            force_refresh=force_refresh,
            include_scoped=include_scoped,
            runtime_kind=skill_context.get("runtime_kind"),
            session_id=skill_context.get("session_id"),
            explicit_workspace_id=skill_context.get("explicit_workspace_id"),
            explicit_workspace_path=skill_context.get("explicit_workspace_path"),
            explicit_project_id=skill_context.get("explicit_project_id"),
            exclude_root_paths=exclude_root_paths,
        )

    def list_skills(
        self,
        *,
        force_refresh: bool = False,
        prefer_cached_ready_inventory: bool = False,
        include_blocked: bool = False,
        include_scoped: bool = True,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        inventory = self._resolve_skill_inventory(
            force_refresh=force_refresh,
            prefer_cached_ready_inventory=prefer_cached_ready_inventory,
            include_scoped=include_scoped,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
            runtime_kind=runtime_kind,
        )
        skills = list(inventory.get("items") or [])
        if include_blocked:
            return skills
        blocked_keys = {
            str(item.get("skillId") or item.get("skillPath") or item.get("rootPath") or item.get("skillName") or "").strip()
            for item in self._recent_blocked_skill_records()
            if str(item.get("skillId") or item.get("skillPath") or item.get("rootPath") or item.get("skillName") or "").strip()
        }
        try:
            from erc.safety_guardian import safety_guardian

            for review in safety_guardian.list_skill_safety_reviews(status="disabled", limit=500):
                for key in (
                    review.get("skill_id"),
                    review.get("skill_path"),
                    review.get("skill_name"),
                ):
                    normalized = str(key or "").strip()
                    if normalized:
                        blocked_keys.add(normalized)
        except Exception:
            pass
        if not blocked_keys:
            return skills
        return [
            item
            for item in skills
            if str(item.get("skillId") or item.get("skillRoot") or item.get("skillName") or "").strip() not in blocked_keys
            and str(item.get("path") or "").strip() not in blocked_keys
            and str(item.get("name") or "").strip() not in blocked_keys
        ]

    def get_skill_startup_status(self) -> dict[str, Any]:
        return SkillLoader.get_startup_status()

    def prime_skill_cache(self) -> bool:
        return bool(SkillLoader.prime_startup_cache())

    def schedule_skill_refresh(self) -> asyncio.Task:
        return SkillLoader.schedule_background_refresh()

    def get_mcp_tools(self) -> list[Any]:
        return list(mcp_manager.get_tools())

    def get_mcp_status(self) -> dict[str, Any]:
        return dict(mcp_manager.get_status())

    def get_mcp_health_summary(self) -> dict[str, Any]:
        return dict(mcp_manager.get_health_summary())

    def get_mcp_startup_status(self) -> dict[str, Any]:
        return dict(mcp_manager.get_startup_status())

    def _remember_dynamic_profile(
        self,
        *,
        cache: dict[str, dict[str, Any]],
        fingerprint: str,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        cache[fingerprint] = dict(profile)
        while len(cache) > _DYNAMIC_PROFILE_CACHE_LIMIT:
            oldest_key = next(iter(cache))
            cache.pop(oldest_key, None)
        return dict(cache[fingerprint])

    def _infer_dynamic_family_profile_with_llm(
        self,
        *,
        family_kind: str,
        family_name: str,
        descriptions: list[str],
        tool_names: list[str],
        managed_channels: list[str],
        base_profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        prompt_payload = json.dumps(
            {
                "familyKind": family_kind,
                "familyName": family_name,
                "toolNames": tool_names[:12],
                "descriptions": descriptions[:8],
                "managedChannels": managed_channels[:8],
                "currentProfile": base_profile,
                "allowedClasses": [
                    "artifact_producer",
                    "artifact_editor_or_analyzer",
                    "workflow_or_script",
                    "methodology_or_tutorial",
                    "advisor_or_perspective",
                    "integration_or_tooling",
                ],
                "allowedArtifacts": list(_QUERY_ARTIFACT_INTENT_SYNONYMS.keys()),
                "allowedOperations": list(_QUERY_OPERATION_INTENT_SYNONYMS.keys()),
                "allowedDocumentSubIntents": ["office_document", "documentation"],
                "allowedPrimaryThemes": list(_QUERY_PRIMARY_THEME_SYNONYMS.keys()),
                "allowedSecondaryThemeTags": list(_QUERY_SECONDARY_THEME_SYNONYMS.keys()),
            },
            ensure_ascii=False,
        )

        def _invoke() -> dict[str, Any] | None:
            from core.background_context_guard import prepare_background_model_messages
            from core.llm_factory import llm_factory

            model = llm_factory.create_for_role("extensions_prefilter", streaming=False, temperature=0)
            prepared = prepare_background_model_messages(
                system_prompt=(
                    "你是 V8 Agent OS 的扩展族级画像归一器。\n"
                    "任务：为 MCP server 工具族输出轻量画像。\n"
                    "只返回 JSON："
                    "{\"skillClassLike\":\"...\",\"primaryArtifactTypes\":[...],"
                    "\"primaryOperations\":[...],\"documentSubIntentHints\":[...],"
                    "\"primaryThemes\":[...],\"secondaryThemeTags\":[...],"
                    "\"profileConfidence\":0.0}\n"
                    "要求：只允许一个主类；主产物最多 2 个；主操作最多 3 个；"
                    "主主题最多 2 个；不要输出解释文本。"
                ),
                instruction="根据已准备的扩展族材料输出唯一 JSON 对象。",
                materials=[
                    {
                        "title": "Extension family profile payload",
                        "kind": "extensions_profile_payload",
                        "content": prompt_payload,
                    }
                ],
                runtime_kind="extensions",
                target_role="extensions:family_profile",
                resolved_model_id="extensions_prefilter",
                component="extensions",
                node="family_profile_context",
            )
            response = model.invoke(prepared.messages, config={"callbacks": []})
            payload, _sanitized, _error = parse_background_json_object(response)
            return payload if isinstance(payload, dict) else None

        try:
            return _PROFILE_INFERENCE_EXECUTOR.submit(_invoke).result(timeout=_DYNAMIC_PROFILE_LLM_TIMEOUT_SECONDS)
        except Exception:
            return None

    def _get_mcp_server_profile(
        self,
        *,
        server_name: str,
        items: list[Any],
        allow_llm: bool,
    ) -> dict[str, Any]:
        ordered_items = sorted(
            list(items or []),
            key=lambda tool: (
                _mcp_tool_server_name(tool).lower(),
                _tool_name(tool).lower(),
            ),
        )
        tool_names = [_tool_name(tool) for tool in ordered_items if _tool_name(tool)]
        descriptions = [_tool_description(tool) for tool in ordered_items if _tool_description(tool)]
        fingerprint = _tool_family_fingerprint([server_name, *tool_names, *descriptions])
        cached = self._mcp_family_profile_cache.get(fingerprint)
        if cached and (not _should_attempt_dynamic_profile_inference(base_profile=cached, allow_llm=allow_llm)):
            return dict(cached)
        base_profile = dict(cached or _derive_tool_family_profile_rules(
            family_name=server_name,
            descriptions=descriptions,
            tool_names=tool_names,
            managed_channels=[],
        ))
        final_profile = dict(base_profile)
        if _should_attempt_dynamic_profile_inference(base_profile=base_profile, allow_llm=allow_llm):
            llm_profile = self._infer_dynamic_family_profile_with_llm(
                family_kind="mcp_server",
                family_name=server_name,
                descriptions=descriptions,
                tool_names=tool_names,
                managed_channels=[],
                base_profile=base_profile,
            )
            final_profile = _normalize_dynamic_profile_with_fallback(payload=llm_profile, fallback=base_profile)
        return self._remember_dynamic_profile(
            cache=self._mcp_family_profile_cache,
            fingerprint=fingerprint,
            profile=final_profile,
        )

    def _derive_phase(self, *, skills_state: dict[str, Any], mcp_state: dict[str, Any]) -> tuple[str, list[str], list[str]]:
        blocked_reasons: list[str] = []
        degraded_reasons: list[str] = []
        blocked_records = self._recent_blocked_skill_records()
        if blocked_records:
            blocked_reasons.extend(
                [
                    f"skill:{str(item.get('skillName') or 'unknown').strip()}"
                    for item in blocked_records
                    if str(item.get("skillName") or "").strip()
                ]
            )
        if self._last_refresh_error:
            degraded_reasons.append(f"refresh:{self._last_refresh_error}")
        mcp_health = self.get_mcp_health_summary()
        if int(mcp_health.get("degraded") or 0) > 0:
            for item in list(mcp_health.get("degradedServers") or []):
                name = str(item.get("name") or "unknown").strip()
                degraded_reasons.append(f"mcp:{name}")
        if blocked_reasons:
            return "blocked", blocked_reasons[:12], degraded_reasons[:12]
        if self._startup_state == "refreshing":
            return "refreshing", [], degraded_reasons[:12]
        if self._startup_state == "cold":
            return "cold", [], degraded_reasons[:12]
        if degraded_reasons:
            return "degraded", [], degraded_reasons[:12]
        return "ready", [], []

    def _build_runtime_state(self) -> dict[str, Any]:
        skills_state = self.get_skill_startup_status()
        mcp_state = self.get_mcp_startup_status()
        phase, blocked_reasons, degraded_reasons = self._derive_phase(skills_state=skills_state, mcp_state=mcp_state)
        catalog_summary = dict(((self._cached_catalog or {}).get("summary") or {}))
        health_summary = dict(((self._cached_health or {}).get("summary") or {}))
        return {
            "phase": phase,
            "startupState": self._startup_state,
            "snapshotFreshness": self._snapshot_freshness,
            "lastRefreshAt": self._last_refresh_at,
            "lastRefreshError": self._last_refresh_error,
            "skillsStartupState": str(skills_state.get("startupState") or "cold"),
            "mcpStartupState": str(mcp_state.get("startupState") or "cold"),
            "inventoryFreshness": self._snapshot_freshness,
            "exposureFreshness": self._snapshot_freshness,
            "skillsState": skills_state,
            "mcpState": mcp_state,
            "skills": skills_state,
            "mcp": mcp_state,
            "catalogSummary": catalog_summary,
            "healthSummary": health_summary,
            "blockedReasons": blocked_reasons,
            "degradedReasons": degraded_reasons,
            "controls": self._controls_payload(),
            "lastSkillInventoryChange": self._last_skill_inventory_change,
        }

    def _decorate_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload or {})
        runtime_state = self._build_runtime_state()
        decorated.update(
            {
                "phase": runtime_state["phase"],
                "startupState": runtime_state["startupState"],
                "snapshotFreshness": runtime_state["snapshotFreshness"],
                "lastRefreshAt": runtime_state["lastRefreshAt"],
                "lastRefreshError": runtime_state["lastRefreshError"],
                "skillsStartupState": runtime_state["skillsStartupState"],
                "mcpStartupState": runtime_state["mcpStartupState"],
                "catalogSummary": runtime_state["catalogSummary"],
                "healthSummary": runtime_state["healthSummary"],
                "blockedReasons": runtime_state["blockedReasons"],
                "degradedReasons": runtime_state["degradedReasons"],
                "controls": runtime_state["controls"],
                "runtime": runtime_state,
            }
        )
        return decorated

    def _decorate_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload or {})
        runtime_state = self._build_runtime_state()
        decorated.update(
            {
                "phase": runtime_state["phase"],
                "startupState": runtime_state["startupState"],
                "snapshotFreshness": runtime_state["snapshotFreshness"],
                "lastRefreshAt": runtime_state["lastRefreshAt"],
                "lastRefreshError": runtime_state["lastRefreshError"],
                "skillsStartupState": runtime_state["skillsStartupState"],
                "mcpStartupState": runtime_state["mcpStartupState"],
                "catalogSummary": runtime_state["catalogSummary"],
                "healthSummary": runtime_state["healthSummary"],
                "blockedReasons": runtime_state["blockedReasons"],
                "degradedReasons": runtime_state["degradedReasons"],
                "controls": runtime_state["controls"],
                "runtime": runtime_state,
            }
        )
        return decorated

    def _persist_cache(self) -> None:
        payload = self._cache_payload_snapshot()
        if payload is None:
            return
        self._persist_cache_payload(payload)

    def _cache_payload_snapshot(self) -> dict[str, Any] | None:
        if self._cached_catalog is None or self._cached_health is None:
            return None
        return {
            "version": 1,
            "updatedAt": self._last_refresh_at or self._now_iso(),
            "catalog": json.loads(json.dumps(self._cached_catalog, ensure_ascii=False)),
            "health": json.loads(json.dumps(self._cached_health, ensure_ascii=False)),
        }

    def _persist_cache_payload(self, payload: dict[str, Any]) -> None:
        cache_path = self._cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_name(
            f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, cache_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_cache(self) -> bool:
        cache_path = self._cache_path()
        if not cache_path.exists():
            legacy_cache_path = self._legacy_cache_path()
            if legacy_cache_path.exists():
                cache_path = legacy_cache_path
        if not cache_path.exists():
            return False
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        catalog = payload.get("catalog")
        health = payload.get("health")
        if not isinstance(catalog, dict) or not isinstance(health, dict):
            return False
        self._cached_catalog = catalog
        self._cached_health = health
        self._startup_state = "ready"
        self._snapshot_freshness = "cached"
        self._last_refresh_at = str(payload.get("updatedAt") or "").strip() or None
        self._last_refresh_error = None
        if cache_path == self._legacy_cache_path():
            try:
                self._persist_cache()
            except Exception:
                pass
        return True

    def _build_catalog_live(
        self,
        *,
        workspace_path: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        scoped_catalog = bool(workspace_path or workspace_id or project_id)
        skill_inventory = self._resolve_skill_inventory(
            force_refresh=False,
            include_scoped=scoped_catalog,
            explicit_workspace_path=workspace_path,
            explicit_workspace_id=workspace_id,
            explicit_project_id=project_id,
            runtime_kind="extensions_catalog" if scoped_catalog else None,
        )
        skills = list(skill_inventory.get("items") or [])
        skills_sorted = sorted(skills, key=lambda item: str(item.get("name") or "").lower())
        mcp_status = self.get_mcp_status()
        skills_state = self.get_skill_startup_status()

        servers: list[dict[str, Any]] = []
        total_tools = 0
        connected_servers = 0
        total_families = 0
        for server_name, payload in sorted(mcp_status.items(), key=lambda item: item[0].lower()):
            tools = list(payload.get("tools") or [])
            status = str(payload.get("status") or "error")
            if status == "connected":
                connected_servers += 1
            total_tools += len(tools)
            families = self._build_mcp_server_families(tools)
            total_families += len(families)
            config = dict(payload.get("config") or {})
            target = str(config.get("url") or config.get("command") or "")
            if config.get("args"):
                args_text = " ".join(str(item) for item in list(config.get("args") or []))
                target = f"{target} {args_text}".strip()
            servers.append(
                {
                    "name": server_name,
                    "status": status,
                    "toolCount": len(tools),
                    "tools": tools,
                    "families": families,
                    "transport": str(config.get("type") or ("stdio" if config.get("command") else "sse")),
                    "target": target,
                    "disabled": bool(config.get("disabled", False)),
                    "appsSupported": bool(payload.get("appsSupported")),
                    "appToolCount": int(payload.get("appToolCount") or 0),
                    "uiResourceCount": int(payload.get("uiResourceCount") or 0),
                    "lastAppsError": payload.get("lastAppsError"),
                }
            )

        root_descriptors = list(skill_inventory.get("rootDescriptors") or list(skills_state.get("rootDescriptors") or []))
        roots = [str(item.get("rootPath") or "") for item in root_descriptors]
        skills_fingerprint = str(skills_state.get("fingerprint") or "").strip()
        skills_revision = str(skill_inventory.get("revision") or skills_state.get("revision") or skills_fingerprint).strip()
        visible_root_signature = str(
            skill_inventory.get("visibleRootSignature")
            or skills_state.get("rootSignature")
            or ""
        ).strip()
        recent_skill_discovery = list(skill_inventory.get("recentSkillDiscovery") or skills_state.get("recentSkillDiscovery") or [])
        changed_at = str(
            ((self._last_skill_inventory_change or {}).get("changedAt"))
            or skills_state.get("lastRefreshAt")
            or "",
        ).strip() or None
        catalog = {
            "fingerprint": skills_fingerprint,
            "revision": skills_revision,
            "visibleRootSignature": visible_root_signature,
            "changedAt": changed_at,
            "lastSkillInventoryChange": self._last_skill_inventory_change,
            "lastMcpInventoryChange": self._last_mcp_inventory_change,
            "catalogScope": {
                "mode": "project_workspace" if project_id else ("workspace" if workspace_path or workspace_id else "default_workspace"),
                "workspacePath": str(workspace_path or "").strip(),
                "workspaceId": str(workspace_id or "").strip(),
                "projectId": str(project_id or "").strip(),
            },
            "summary": {
                "skillCount": len(skills_sorted),
                "blockedSkillCount": len(self._recent_blocked_skill_records()),
                "mcpServerCount": len(servers),
                "connectedMcpServerCount": connected_servers,
                "mcpToolCount": total_tools,
                "mcpFamilyCount": total_families,
            },
            "skillDependencyPolicy": get_skill_dependency_policy(),
            "skills": {
                "root": str(roots[0]) if roots else "",
                "roots": [str(root) for root in roots],
                "rootDescriptors": root_descriptors,
                "fingerprint": skills_fingerprint,
                "revision": skills_revision,
                "visibleRootSignature": visible_root_signature,
                "discoveryRevision": str(skill_inventory.get("discoveryRevision") or skills_revision).strip(),
                "changedRoots": list(skill_inventory.get("changedRoots") or []),
                "scopedRefreshMode": skill_inventory.get("scopedRefreshMode"),
                "recentSkillDiscovery": recent_skill_discovery,
                "changedAt": changed_at,
                "items": [
                    {
                        "skillId": str(item.get("skillId") or "").strip(),
                        "name": str(item.get("name") or item.get("folder") or ""),
                        "description": str(item.get("description") or "暂无说明。"),
                        "path": str(item.get("path") or ""),
                        "sourceType": str(item.get("sourceType") or "").strip(),
                        "visibility": str(item.get("visibility") or "").strip(),
                        "workspacePath": str(item.get("workspacePath") or "").strip(),
                        "workspaceId": str(item.get("workspaceId") or "").strip(),
                        "projectId": str(item.get("projectId") or "").strip(),
                        "rootPath": str(item.get("rootPath") or item.get("skillRoot") or item.get("path") or "").strip(),
                        "entry": _skill_entry_payload(item),
                    }
                    for item in skills_sorted
                ],
            },
            "mcp": {
                "servers": servers,
                "inventoryRevision": str(self._mcp_inventory_status().get("inventoryRevision") or ""),
                "lastInventoryChange": self._last_mcp_inventory_change,
            },
        }
        try:
            write_capability_index(
                skills=skills_sorted,
                mcp_servers=servers,
                lexicon_state=_ensure_extension_lexicon_state(),
                source="extensions_runtime_catalog",
            )
        except Exception as exc:
            print(f"[ExtensionsRuntime] Failed to write capability index: {exc}")
        return catalog

    def _build_health_live(self, catalog: dict[str, Any]) -> dict[str, Any]:
        status_breakdown = Counter()
        for server in list(((catalog.get("mcp") or {}).get("servers") or [])):
            status_breakdown[str(server.get("status") or "error")] += 1

        skills_payload = dict(catalog.get("skills") or {})
        root = str(skills_payload.get("root") or "")
        return {
            "summary": dict(catalog.get("summary") or {}),
            "skillDependencyPolicy": dict(catalog.get("skillDependencyPolicy") or {}),
            "skills": {
                "root": root,
                "roots": list(skills_payload.get("roots") or []),
                "rootDescriptors": list(skills_payload.get("rootDescriptors") or []),
                "available": bool(root),
                "blockedCount": len(self._recent_blocked_skill_records()),
            },
            "mcp": {
                "statusBreakdown": dict(status_breakdown),
                "health": self.get_mcp_health_summary(),
            },
        }

    def _build_live_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        catalog = self._build_catalog_live()
        return catalog, self._build_health_live(catalog)

    async def _build_live_snapshot_async(self) -> tuple[dict[str, Any], dict[str, Any]]:
        snapshot: tuple[dict[str, Any], dict[str, Any]] | None = None
        for _attempt in range(2):
            revision_before = self._inventory_revision_key()
            snapshot = await asyncio.to_thread(self._build_live_snapshot)
            if revision_before == self._inventory_revision_key():
                break
        assert snapshot is not None
        return snapshot

    async def _wait_optional_task(self, task: asyncio.Task | None, *, timeout: float, label: str) -> None:
        if not task:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[ExtensionsRuntime] {label} is still refreshing in background; catalog warmup will continue with current state.")

    async def _refresh_runtime_snapshot(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
        wait_for_optional_tasks: bool = True,
        force_skill_reload: bool = False,
        force_mcp_reload: bool = False,
        clear_route_cache: bool = True,
    ) -> dict[str, Any]:
        async with self._refresh_lock:
            self._startup_state = "refreshing"
            self._snapshot_freshness = "cached" if self._cached_catalog else "cold"
            self._last_refresh_error = None
            if force_skill_reload:
                await asyncio.to_thread(SkillLoader.reload_skills)
            elif wait_for_optional_tasks:
                await self._wait_optional_task(skill_refresh_task, timeout=12.0, label="SkillLoader")
            if force_mcp_reload:
                await mcp_manager.cleanup()
                await mcp_manager.initialize()
            elif wait_for_optional_tasks:
                await self._wait_optional_task(mcp_init_task, timeout=12.0, label="MCP")

            catalog, health = await self._build_live_snapshot_async()
            self._cached_catalog = catalog
            self._cached_health = health
            self._startup_state = "ready"
            self._snapshot_freshness = "live"
            self._last_refresh_at = self._now_iso()
            self._last_refresh_error = None
            if clear_route_cache:
                self._route_cache.clear()
            self._clear_dynamic_family_profile_caches()
            cache_payload = self._cache_payload_snapshot()
            if cache_payload is not None:
                await asyncio.to_thread(self._persist_cache_payload, cache_payload)
            return self._decorate_health(health)

    async def _refresh_skill_inventory_if_changed(
        self,
        *,
        reason: str = "watcher",
        refresh_snapshot: bool = True,
    ) -> dict[str, Any]:
        change = await asyncio.to_thread(SkillLoader.reload_if_changed)
        if not change.get("changed"):
            return change
        self._last_skill_inventory_change = {
            **change,
            "changedAt": self._now_iso(),
            "reason": reason,
        }
        if refresh_snapshot:
            await self._refresh_runtime_snapshot(clear_route_cache=False)
        print(
            "[ExtensionsRuntime] Skills inventory changed: "
            f"reason={reason}, "
            f"added={change.get('addedSkills') or []}, "
            f"removed={change.get('removedSkills') or []}, "
            f"updated={change.get('updatedSkills') or []}"
        )
        return change

    def request_skill_inventory_refresh(self, *, reason: str = "manual") -> None:
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._refresh_skill_inventory_if_changed(reason=reason),
                loop,
            )
            return
        try:
            change = SkillLoader.reload_if_changed()
            if change.get("changed"):
                self._last_skill_inventory_change = {
                    **change,
                    "changedAt": self._now_iso(),
                    "reason": reason,
                }
                self._clear_dynamic_family_profile_caches()
                self._cached_catalog = None
                self._cached_health = None
        except Exception as exc:
            self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
            print(f"[ExtensionsRuntime] Immediate skills inventory refresh failed: {type(exc).__name__}: {exc}")

    def _ensure_skill_inventory_watcher(self) -> None:
        task = self._skills_inventory_watcher_task
        if task and not task.done():
            return

        async def _runner() -> None:
            while True:
                await asyncio.sleep(2.0)
                try:
                    await self._refresh_skill_inventory_if_changed(reason="watcher")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                    print(f"[ExtensionsRuntime] Skills inventory watcher failed: {type(exc).__name__}: {exc}")

        self._skills_inventory_watcher_task = asyncio.create_task(_runner(), name="extensions_runtime:skills_inventory_watcher")

    async def _refresh_mcp_inventory_if_changed(
        self,
        *,
        reason: str = "watcher",
        refresh_snapshot: bool = True,
    ) -> dict[str, Any]:
        change = await mcp_manager.reload_if_changed()
        if not change.get("changed"):
            return change
        self._last_mcp_inventory_change = {
            **change,
            "changedAt": self._now_iso(),
            "reason": reason,
        }
        if refresh_snapshot:
            await self._refresh_runtime_snapshot(clear_route_cache=False)
        self._clear_dynamic_family_profile_caches()
        print(
            "[ExtensionsRuntime] MCP inventory changed: "
            f"reason={reason}, "
            f"servers={change.get('mcpChangedServers') or {}}"
        )
        return change

    async def refresh_inventory_if_changed(self, *, reason: str = "manual") -> dict[str, Any]:
        skill_change = await self._refresh_skill_inventory_if_changed(reason=reason, refresh_snapshot=False)
        mcp_change = await self._refresh_mcp_inventory_if_changed(reason=reason, refresh_snapshot=False)
        changed = bool(skill_change.get("changed") or mcp_change.get("changed"))
        if changed:
            await self._refresh_runtime_snapshot(clear_route_cache=False)
        return {
            "changed": changed,
            "skills": skill_change,
            "mcp": mcp_change,
            "revision": self._inventory_revision_key(),
        }

    async def force_refresh_after_mcp_config_change(
        self,
        *,
        reason: str = "mcp_config_change",
        mcp_change: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        change = dict(mcp_change or {})
        self._last_mcp_inventory_change = {
            **change,
            "changed": True,
            "changedAt": self._now_iso(),
            "reason": reason,
        }
        await self._refresh_runtime_snapshot(clear_route_cache=False)
        self._clear_dynamic_family_profile_caches()
        return {
            "changed": True,
            "mcp": self._last_mcp_inventory_change,
            "revision": self._inventory_revision_key(),
        }

    def request_mcp_inventory_refresh(self, *, reason: str = "manual") -> None:
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._refresh_mcp_inventory_if_changed(reason=reason),
                loop,
            )

    def _ensure_mcp_inventory_watcher(self) -> None:
        task = self._mcp_inventory_watcher_task
        if task and not task.done():
            return

        async def _runner() -> None:
            while True:
                await asyncio.sleep(2.0)
                try:
                    await self._refresh_mcp_inventory_if_changed(reason="watcher")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                    print(f"[ExtensionsRuntime] MCP inventory watcher failed: {type(exc).__name__}: {exc}")

        self._mcp_inventory_watcher_task = asyncio.create_task(_runner(), name="extensions_runtime:mcp_inventory_watcher")

    async def start(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
        wait_for_initial_refresh: bool = True,
    ) -> None:
        async with self._start_lock:
            await self._start_unlocked(
                skill_refresh_task=skill_refresh_task,
                mcp_init_task=mcp_init_task,
                wait_for_initial_refresh=wait_for_initial_refresh,
            )

    async def _start_unlocked(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
        wait_for_initial_refresh: bool = True,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        if self._cached_catalog is None or self._cached_health is None:
            self._load_cache()
        if self._cached_catalog is None or self._cached_health is None:
            cold_catalog = await asyncio.to_thread(self._build_catalog_live)
            self._cached_catalog = cold_catalog
            self._cached_health = await asyncio.to_thread(self._build_health_live, cold_catalog)
        self._startup_state = "refreshing"
        self._snapshot_freshness = "cached" if self._last_refresh_at else "cold"
        self._last_refresh_error = None

        if self._background_refresh_task and not self._background_refresh_task.done():
            self._ensure_skill_inventory_watcher()
            self._ensure_mcp_inventory_watcher()
            return

        async def _runner() -> None:
            try:
                await self._refresh_runtime_snapshot(
                    skill_refresh_task=skill_refresh_task,
                    mcp_init_task=mcp_init_task,
                    wait_for_optional_tasks=wait_for_initial_refresh,
                )
            except Exception as exc:
                self._startup_state = "error"
                self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                print(f"[ExtensionsRuntime] Background refresh failed: {type(exc).__name__}: {exc}")

        self._background_refresh_task = asyncio.create_task(_runner(), name="extensions_runtime:refresh")
        self._ensure_skill_inventory_watcher()
        self._ensure_mcp_inventory_watcher()

    async def stop(self) -> None:
        self._loop = None
        watcher_task = self._skills_inventory_watcher_task
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        self._skills_inventory_watcher_task = None
        mcp_watcher_task = self._mcp_inventory_watcher_task
        if mcp_watcher_task and not mcp_watcher_task.done():
            mcp_watcher_task.cancel()
            try:
                await mcp_watcher_task
            except asyncio.CancelledError:
                pass
        self._mcp_inventory_watcher_task = None
        task = self._background_refresh_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_runtime_snapshot(self) -> dict[str, Any]:
        return self._build_runtime_state()

    def get_startup_status(self) -> dict[str, Any]:
        return self.get_runtime_snapshot()

    def _resolve_prefilter_policy(self) -> dict[str, Any]:
        config = storage.get_extensions_config() or {}
        policy = dict(config.get("prefilterPolicy") or config.get("rerankPolicy") or {})
        default_policy = ((storage.get_extensions_config() or {}).get("prefilterPolicy") or {})
        skills_policy = dict(policy.get("skills") or default_policy.get("skills") or {})
        mcp_policy = dict(policy.get("mcp") or default_policy.get("mcp") or {})
        enabled = bool(policy.get("enabled", False))

        def _stage_policy(raw: dict[str, Any], *, default_top: int, default_llm_top: int) -> dict[str, Any]:
            return {
                "stage1TopK": max(1, min(int(raw.get("stage1TopK") or default_top), 100)),
                "llmEnabled": bool(raw.get("llmEnabled", True)),
                "stage2TopK": max(1, min(int(raw.get("stage2TopK") or default_llm_top), 50)),
                "llmTimeoutSeconds": max(5, min(int(raw.get("llmTimeoutSeconds") or 5), 10)),
            }

        if not enabled:
            return {
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": "",
                "role": "",
                "reason": "disabled",
                "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
                "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
            }

        for role in ("extensions_prefilter", "extensions_reranker"):
            try:
                resolved = model_control_plane.resolve_model_for_role(role)
            except Exception as exc:
                return {
                    "enabled": True,
                    "available": False,
                    "mode": "fallback",
                    "modelId": "",
                    "role": role,
                    "reason": str(exc),
                    "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
                    "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
                }
            model_id = str(resolved.get("resolvedModelId") or "").strip()
            if model_id:
                return {
                    "enabled": True,
                    "available": True,
                    "mode": str(policy.get("mode") or "two_stage").strip() or "two_stage",
                    "modelId": model_id,
                    "role": role,
                    "reason": "",
                    "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
                    "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
                }

        return {
            "enabled": True,
            "available": False,
            "mode": "fallback",
            "modelId": "",
            "role": "extensions_prefilter",
            "reason": "No extension-prefilter model is currently bound.",
            "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
            "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
        }

    def build_catalog(
        self,
        *,
        workspace_path: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        scoped_catalog = bool(workspace_path or workspace_id or project_id)
        payload = dict(
            self._build_catalog_live(
                workspace_path=workspace_path,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if scoped_catalog
            else (self._cached_catalog or self._build_catalog_live())
        )
        return self._decorate_catalog(payload)

    def build_health(self) -> dict[str, Any]:
        payload = dict(self._cached_health or self._build_health_live(self._cached_catalog or self._build_catalog_live()))
        return self._decorate_health(payload)

    def delete_skill(
        self,
        skill_id: str,
        *,
        scope: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        project_id: str | None = None,
        initiated_by: str | None = None,
    ) -> dict[str, Any]:
        result = SkillLoader.delete_skill(
            skill_id,
            scope=scope,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            project_id=project_id,
            initiated_by=initiated_by,
        )
        self._cached_catalog = None
        self._cached_health = None
        self._route_cache.clear()
        self._last_skill_inventory_change = {
            **dict(result.get("refresh") or {}),
            "changedAt": self._now_iso(),
            "reason": "skill_delete",
        }
        catalog = self._build_catalog_live(
            workspace_path=workspace_path,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        health = self._build_health_live(catalog)
        if not (workspace_path or workspace_id or project_id):
            self._cached_catalog = catalog
            self._cached_health = health
        return {
            "status": "success",
            **result,
            "catalogSummary": dict(catalog.get("summary") or {}),
            "healthSummary": dict(health.get("summary") or {}),
        }

    async def reload(self) -> dict[str, Any]:
        return await self._refresh_runtime_snapshot(force_skill_reload=True, force_mcp_reload=True)

    async def build_prefilter_preview(
        self,
        *,
        user_query: str,
        refresh: bool = False,
        workspace_path: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_query = str(user_query or "").strip()
        if not normalized_query:
            return {
                "queryPreview": "",
                "skillStage1Entries": [],
                "skillEntries": [],
                "mcpStage1Servers": [],
                "mcpServers": [],
                "mcpFamilies": [],
                "counts": {},
                "routing": {},
            }
        context_token = None
        if workspace_path or workspace_id or project_id:
            context_token = self.bind_execution_context(
                workspace_path=workspace_path,
                workspace_id=workspace_id,
                project_id=project_id,
                runtime_kind="extensions_preview",
            )
        try:
            if refresh:
                await self.refresh_inventory_if_changed(reason="preview")
            route_bundle = self.build_contextual_route(
                user_query=normalized_query,
                available_tools=list(self.get_mcp_tools()),
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=2,
                freshness_mode=_INVENTORY_FRESHNESS_PREVIEW,
            )
        finally:
            if context_token is not None:
                self.reset_execution_context(context_token)
        candidate_summary = route_bundle.candidate_summary
        return {
            "queryPreview": _truncate(normalized_query, 160),
            "skillStage1Entries": candidate_summary.get("skillStage1Entries") or [],
            "skillEntries": candidate_summary.get("skillEntries") or [],
            "skillRootDescriptors": candidate_summary.get("skillRootDescriptors") or [],
            "mcpStage1Servers": candidate_summary.get("mcpStage1Servers") or [],
            "mcpServers": candidate_summary.get("mcpServers") or [],
            "mcpFamilies": candidate_summary.get("mcpFamilies") or [],
            "counts": candidate_summary,
            "routing": {
                "mode": candidate_summary.get("mode"),
                "routingMode": candidate_summary.get("routingMode"),
                "skillsRoutingMode": candidate_summary.get("skillsRoutingMode"),
                "mcpRoutingMode": candidate_summary.get("mcpRoutingMode"),
                "skillInventoryRevision": candidate_summary.get("skillInventoryRevision"),
                "visibleRootSignature": candidate_summary.get("visibleRootSignature"),
                "changedRoots": candidate_summary.get("changedRoots") or [],
                "scopedRefreshMode": candidate_summary.get("scopedRefreshMode"),
                "mcpInventoryRevision": candidate_summary.get("mcpInventoryRevision"),
                "skillRefreshMode": candidate_summary.get("skillRefreshMode"),
                "mcpRefreshMode": candidate_summary.get("mcpRefreshMode"),
                "recentSkillKeepaliveCount": candidate_summary.get("recentSkillKeepaliveCount"),
                "mcpChangedServers": candidate_summary.get("mcpChangedServers") or {},
                "inventoryRefreshDurationMs": candidate_summary.get("inventoryRefreshDurationMs") or {},
                "lexiconSignature": candidate_summary.get("lexiconSignature"),
                "lexiconCoreSignature": candidate_summary.get("lexiconCoreSignature"),
                "lexiconLocales": candidate_summary.get("lexiconLocales") or [],
                "lexiconLoadErrors": candidate_summary.get("lexiconLoadErrors") or [],
                "marketLexiconEnabled": candidate_summary.get("marketLexiconEnabled"),
                "marketLexiconSignature": candidate_summary.get("marketLexiconSignature"),
                "marketLexiconLocales": candidate_summary.get("marketLexiconLocales") or [],
                "marketLexiconLoadErrors": candidate_summary.get("marketLexiconLoadErrors") or [],
                "marketLexiconHitTerms": candidate_summary.get("marketLexiconHitTerms") or [],
                "marketLexiconContributionScore": candidate_summary.get("marketLexiconContributionScore"),
                "queryAnalysisCacheHit": candidate_summary.get("queryAnalysisCacheHit"),
                "directCanonicalFamilies": candidate_summary.get("directCanonicalFamilies") or [],
                "canonicalFamilies": candidate_summary.get("canonicalFamilies") or [],
                "primaryCanonicalFamily": candidate_summary.get("primaryCanonicalFamily"),
                "shortCanonicalNarrowing": candidate_summary.get("shortCanonicalNarrowing"),
                "shortCanonicalNarrowingApplied": candidate_summary.get("shortCanonicalNarrowingApplied"),
                "modelId": candidate_summary.get("modelId"),
                "role": candidate_summary.get("role"),
                "reason": candidate_summary.get("reason"),
                "prefilterTimedOut": candidate_summary.get("prefilterTimedOut"),
                "prefilterCacheHit": candidate_summary.get("prefilterCacheHit"),
                "stage1Enabled": candidate_summary.get("stage1Enabled") or {},
                "stage1TopK": candidate_summary.get("stage1TopK") or {},
                "stage2Enabled": candidate_summary.get("stage2Enabled") or {},
                "stage2TopK": candidate_summary.get("stage2TopK") or {},
                "llmTimeoutSeconds": candidate_summary.get("llmTimeoutSeconds") or {},
                "skillStage1HitCount": candidate_summary.get("skillStage1HitCount"),
                "skillStage1ShortlistCount": candidate_summary.get("skillStage1ShortlistCount"),
                "skillFinalExposedCount": candidate_summary.get("skillFinalExposedCount"),
                "mcpStage1HitCount": candidate_summary.get("mcpStage1HitCount"),
                "mcpStage1ShortlistCount": candidate_summary.get("mcpStage1ShortlistCount"),
                "mcpFinalExposedCount": candidate_summary.get("mcpFinalExposedCount"),
                "skillInventoryCount": candidate_summary.get("skillInventoryCount"),
                "mcpInventoryCount": candidate_summary.get("mcpInventoryCount"),
                "skillPoolSize": candidate_summary.get("skillPoolSize"),
                "mcpPoolSize": candidate_summary.get("mcpPoolSize"),
                "mcpServerPoolSize": candidate_summary.get("mcpServerPoolSize"),
                "mcpFamilyPoolSize": candidate_summary.get("mcpFamilyPoolSize"),
                "selectedSkills": candidate_summary.get("skills") or [],
                "selectedSkillIds": candidate_summary.get("selectedSkillIds") or [],
                "selectedMcpServers": candidate_summary.get("mcpSelectedServers") or [],
                "selectedMcpFamilies": candidate_summary.get("mcpSelectedFamilies") or [],
                "selectedMcpTools": candidate_summary.get("mcpTools") or [],
                "primaryThemeIntents": candidate_summary.get("primaryThemeIntents") or [],
                "themeMatchedCount": candidate_summary.get("themeMatchedCount"),
                "themeBackfilledCount": candidate_summary.get("themeBackfilledCount"),
                "themeRankingSignals": candidate_summary.get("themeRankingSignals") or {},
                "mcpProfileMatchedCount": candidate_summary.get("mcpProfileMatchedCount"),
                "mcpThemeMatchedCount": candidate_summary.get("mcpThemeMatchedCount"),
                "mcpDocumentSubIntentMatched": candidate_summary.get("mcpDocumentSubIntentMatched"),
                "mcpThemeFallbackInjectedCount": candidate_summary.get("mcpThemeFallbackInjectedCount"),
            },
        }

    def build_contextual_route(
        self,
        *,
        user_query: str,
        available_tools: list[Any],
        loaded_agents: list[dict[str, Any]] | None = None,
        inherited_skill_ids: list[str] | None = None,
        inherited_skill_names: list[str] | None = None,
        skill_limit: int = 5,
        mcp_limit: int = 2,
        freshness_mode: str = _INVENTORY_FRESHNESS_GUARDED,
        _pre_resolved_skill_inventory: dict[str, Any] | None = None,
        _pre_resolved_inventory_freshness: dict[str, Any] | None = None,
    ) -> ExtensionRouteBundle:
        query_text = str(user_query or "").strip()
        query_tokens, query_profile, query_analysis_cache_hit, lexicon_state, market_enrichment = _analyze_extensions_query(query_text)
        context_payload = self._resolve_event_context()
        plugin_projection: dict[str, Any] = {"grants": [], "skills": [], "mcpTools": [], "cliProfiles": [], "uiAdapters": []}
        plugin_channel: dict[str, Any] = {
            "active": False,
            "mode": "extensions_default",
            "prefilterBypassed": False,
            "requestedPluginIds": [],
            "installedPluginIds": [],
            "projectedPluginIds": [],
            "blocked": [],
        }
        plugin_owned_skill_roots: set[str] = set()
        plugin_owned_mcp_servers: set[str] = set()
        explicit_plugin_references = list(
            context_payload.get("plugin_references")
            or context_payload.get("pluginReferences")
            or []
        )
        try:
            from runtimes.plugin_manager.service import plugin_manager_service

            plugin_owned_skill_roots, plugin_owned_mcp_servers = plugin_manager_service.plugin_owned_components()
            session_id = str(context_payload.get("session_id") or "").strip()
            run_id = str(context_payload.get("run_id") or "").strip() or None
            runtime_kind = str(context_payload.get("runtime_kind") or "chat").strip()
            agent_id = str(context_payload.get("agent_id") or "supervisor").strip() or "supervisor"
            delegation_id = str(context_payload.get("delegation_id") or context_payload.get("delegationId") or "").strip() or None
            grantee_type = "subagent" if runtime_kind == "subagent" and agent_id != "supervisor" else "supervisor"
            if session_id:
                plugin_projection = plugin_manager_service.projection_for(
                    session_id=session_id,
                    run_id=run_id,
                    grantee_type=grantee_type,
                    grantee_id=agent_id,
                    delegation_id=delegation_id,
                )
            plugin_channel = plugin_manager_service.resolve_privileged_channel(
                plugin_references=explicit_plugin_references,
                session_id=session_id,
                run_id=run_id,
                grantee_type=grantee_type,
                grantee_id=agent_id,
                delegation_id=delegation_id,
                projection=plugin_projection,
            )
            if plugin_channel.get("active"):
                plugin_projection = dict(plugin_channel.get("projection") or {})
        except Exception:
            logger.exception("Plugin package route resolution failed")
            if explicit_plugin_references:
                requested_plugin_ids = []
                for item in explicit_plugin_references:
                    if not isinstance(item, dict):
                        continue
                    plugin_id = str(item.get("pluginId") or item.get("plugin_id") or "").strip().lower()
                    if plugin_id and plugin_id not in requested_plugin_ids:
                        requested_plugin_ids.append(plugin_id)
                plugin_projection = {
                    "grants": [],
                    "skills": [],
                    "mcpTools": [],
                    "cliProfiles": [],
                    "uiAdapters": [],
                    "providerAdapters": [],
                }
                plugin_channel = {
                    "active": True,
                    "mode": "privileged_bundle_error",
                    "prefilterBypassed": True,
                    "requestedPluginIds": requested_plugin_ids,
                    "installedPluginIds": [],
                    "projectedPluginIds": [],
                    "blocked": [
                        {
                            "pluginId": plugin_id,
                            "status": "invalid",
                            "reason": "plugin_route_unavailable",
                        }
                        for plugin_id in requested_plugin_ids
                    ],
                    "projection": plugin_projection,
                }
        privileged_plugin_channel_active = bool(plugin_channel.get("active"))
        cross_runtime_escape = _should_enable_cross_runtime_escape(query_tokens)
        prefilter_policy = self._resolve_prefilter_policy()
        skill_policy = dict(prefilter_policy.get("skills") or {})
        mcp_policy = dict(prefilter_policy.get("mcp") or {})
        skill_stage1_enabled = bool(skill_policy.get("stage1Enabled", True))
        skill_stage1_top_k = max(1, int(skill_policy.get("stage1TopK") or 20))
        skill_stage2_configured = bool(skill_policy.get("llmEnabled", True))
        skill_stage2_top_k = max(1, int(skill_policy.get("stage2TopK") or 5))
        skill_stage2_timeout = max(5, min(int(skill_policy.get("llmTimeoutSeconds") or 5), 10))
        mcp_stage1_enabled = bool(mcp_policy.get("stage1Enabled", True))
        mcp_stage1_top_k = max(1, int(mcp_policy.get("stage1TopK") or 20))
        mcp_stage2_configured = bool(mcp_policy.get("llmEnabled", True))
        mcp_stage2_top_k = max(1, int(mcp_policy.get("stage2TopK") or 2))
        mcp_stage2_timeout = max(5, min(int(mcp_policy.get("llmTimeoutSeconds") or 5), 10))
        effective_skill_stage1_limit = skill_stage1_top_k
        effective_mcp_stage1_limit = mcp_stage1_top_k
        prefilter_model_id = str(prefilter_policy.get("modelId") or "").strip()
        prefilter_role = str(prefilter_policy.get("role") or "").strip()
        prefilter_reason = str(prefilter_policy.get("reason") or "").strip()
        stage2_runtime_available = bool(
            prefilter_policy.get("enabled") and prefilter_policy.get("available") and prefilter_model_id
        )

        skill_status = self._skill_inventory_status()
        mcp_status = self._mcp_inventory_status()
        skill_last_reload = dict(skill_status.get("lastReloadResult") or {})
        mcp_last_reload = dict(mcp_status.get("lastReloadResult") or {})
        inventory_freshness = dict(_pre_resolved_inventory_freshness or {})
        if not inventory_freshness:
            inventory_freshness = self._apply_inventory_freshness_mode(
                freshness_mode=freshness_mode,
                reason="route" if freshness_mode == _INVENTORY_FRESHNESS_GUARDED else "preview",
                include_scoped=True,
            )
        skill_inventory = _pre_resolved_skill_inventory or self._resolve_skill_inventory(
            force_refresh=False,
            prefer_cached_ready_inventory=True,
            include_scoped=True,
            session_id=str((inventory_freshness.get("skillContext") or {}).get("session_id") or "").strip() or None,
            explicit_workspace_id=str((inventory_freshness.get("skillContext") or {}).get("explicit_workspace_id") or "").strip() or None,
            explicit_workspace_path=str((inventory_freshness.get("skillContext") or {}).get("explicit_workspace_path") or "").strip() or None,
            explicit_project_id=str((inventory_freshness.get("skillContext") or {}).get("explicit_project_id") or "").strip() or None,
            runtime_kind=str((inventory_freshness.get("skillContext") or {}).get("runtime_kind") or "").strip() or None,
            exclude_root_paths=set(inventory_freshness.get("excludeRootPaths") or set()),
        )
        raw_skill_entries = list(skill_inventory.get("items") or [])
        skill_entries = [
            item
            for item in raw_skill_entries
            if not bool((item.get("safety") or {}).get("disabled"))
            and str(((item.get("safety") or {}).get("effectiveVerdict") or (item.get("safety") or {}).get("verdict") or "")).strip().lower() != "block"
            and not any(
                str(Path(str(item.get("skillRoot") or item.get("path") or "")).resolve()).startswith(root)
                for root in plugin_owned_skill_roots
                if str(item.get("skillRoot") or item.get("path") or "").strip()
            )
        ]
        if privileged_plugin_channel_active:
            skill_entries = []
        skill_root_descriptors = list(skill_inventory.get("rootDescriptors") or [])
        skill_inventory_revision = str(
            skill_inventory.get("revision")
            or skill_status.get("revision")
            or skill_status.get("fingerprint")
            or ""
        )
        recent_skill_discovery = list(skill_inventory.get("recentSkillDiscovery") or skill_status.get("recentSkillDiscovery") or [])
        ranked_skills = sorted(
            (
                (
                    *_score_skill_entry(
                        query_text=query_text,
                        query_tokens=query_tokens,
                        query_profile=query_profile,
                        skill=item,
                    ),
                    str(item.get("name") or item.get("folder") or ""),
                    item,
                )
                for item in skill_entries
            ),
            key=lambda row: (-int(bool(row[1])), -row[0], row[3].lower()),
        )
        skill_stage1_hits = [row[4] for row in ranked_skills if row[0] > 0] if skill_stage1_enabled else []
        short_canonical_narrowing_applied = False
        theme_fallback_candidates: list[dict[str, Any]] = []
        if (
            skill_stage1_enabled
            and not query_profile.get("artifactIntent")
            and set(_normalize_profile_items(query_profile.get("primaryThemeIntents"))).intersection(_THEME_FALLBACK_TARGETS)
        ):
            direct_theme_hits = [
                item
                for item in skill_entries
                if str((item.get("capabilityProfile") or {}).get("skillClass") or "").strip().lower() in _THEME_HEAVY_CLASSES
                and _has_direct_primary_theme_match(item, query_profile)
            ]
            if not direct_theme_hits:
                ranked_fallback_candidates = sorted(
                    (
                        (
                            _score_advisory_fallback_entry(
                                query_text=query_text,
                                query_tokens=query_tokens,
                                query_profile=query_profile,
                                skill=item,
                            ),
                            str(item.get("name") or item.get("folder") or "").lower(),
                            item,
                        )
                        for item in skill_entries
                        if str((item.get("capabilityProfile") or {}).get("skillClass") or "").strip().lower() in _THEME_HEAVY_CLASSES
                    ),
                    key=lambda row: (-row[0], row[1]),
                )
                theme_fallback_candidates = [row[2] for row in ranked_fallback_candidates if row[0] > 0][:3]
                if theme_fallback_candidates:
                    skill_stage1_hits = _merge_keepalive_skills(
                        skill_stage1_hits,
                        theme_fallback_candidates,
                        max(len(skill_stage1_hits) + len(theme_fallback_candidates), effective_skill_stage1_limit),
                    )
        recent_keepalive_candidates: list[dict[str, Any]] = []
        recent_skill_keys = {
            str(item.get("skillId") or item.get("skillName") or item.get("skillRoot") or "").strip()
            for item in recent_skill_discovery
            if str(item.get("skillId") or item.get("skillName") or item.get("skillRoot") or "").strip()
        }
        if skill_stage1_enabled and recent_skill_keys:
            ranked_recent = sorted(
                (
                    (
                        _score_skill_entry(
                            query_text=query_text,
                            query_tokens=query_tokens,
                            query_profile=query_profile,
                            skill=item,
                        )[0],
                        str(item.get("name") or item.get("folder") or "").lower(),
                        item,
                    )
                    for item in skill_entries
                    if str(item.get("skillId") or item.get("skillName") or item.get("skillRoot") or "").strip() in recent_skill_keys
                ),
                key=lambda row: (-row[0], row[1]),
            )
            recent_keepalive_candidates = [row[2] for row in ranked_recent if row[0] > 0][:_DYNAMIC_THEME_FALLBACK_LIMIT]
            if recent_keepalive_candidates:
                skill_stage1_hits = _merge_recent_keepalive_skills(
                    skill_stage1_hits,
                    recent_keepalive_candidates,
                    max(len(skill_stage1_hits) + len(recent_keepalive_candidates), effective_skill_stage1_limit),
                )
        if skill_stage1_enabled and bool(query_profile.get("shortCanonicalNarrowing")):
            narrowed_skill_stage1_hits = [
                item
                for item in skill_stage1_hits
                if _skill_matches_query_canonical_family(item, query_profile)
            ]
            if narrowed_skill_stage1_hits:
                short_canonical_narrowing_applied = True
                skill_stage1_hits = narrowed_skill_stage1_hits
        skill_stage1_hit_count = len(skill_stage1_hits)
        skill_pool = skill_stage1_hits[:effective_skill_stage1_limit] if effective_skill_stage1_limit > 0 else []
        skill_stage1_shortlist = list(skill_pool)
        skill_stage2_candidate_entries = list(skill_stage1_shortlist) if skill_stage1_enabled else list(skill_entries)
        selected_skills = list(skill_stage1_shortlist) if skill_stage1_enabled else list(skill_entries)
        skill_routing_mode = "stage1_only" if skill_stage1_enabled else "unfiltered"

        mcp_tools = [
            tool
            for tool in available_tools
            if _is_mcp_tool(tool) and _mcp_tool_server_name(tool) not in plugin_owned_mcp_servers
        ]
        if privileged_plugin_channel_active:
            mcp_tools = []
        base_tools = [
            tool
            for tool in available_tools
            if not _is_mcp_tool(tool) and _tool_name(tool) != "plugin_cli"
        ]
        mcp_server_map: dict[str, list[Any]] = {}
        for tool in mcp_tools:
            server_name = _mcp_tool_server_name(tool)
            mcp_server_map.setdefault(server_name, []).append(tool)
        mcp_server_profiles: dict[str, dict[str, Any]] = {}

        ranked_mcp_servers: list[tuple[int, str, list[Any]]] = []
        for server_name, raw_items in mcp_server_map.items():
            items = sorted(
                raw_items,
                key=lambda tool: (
                    _mcp_tool_server_name(tool).lower(),
                    _tool_name(tool).lower(),
                ),
            )
            if not items:
                continue
            profile = self._get_mcp_server_profile(
                server_name=server_name,
                items=items,
                allow_llm=stage2_runtime_available,
            )
            mcp_server_profiles[server_name] = profile
            server_score = _score_mcp_server_entry(
                query_text=query_text,
                query_tokens=query_tokens,
                query_profile=query_profile,
                server_name=server_name,
                items=items,
                profile=profile,
            )
            ranked_mcp_servers.append((server_score, server_name, items))

        ranked_mcp_servers.sort(
            key=lambda row: (
                -row[0],
                row[1].lower(),
            ),
        )

        mcp_stage1_hits = [
            server_name
            for score, server_name, _items in ranked_mcp_servers
            if score > 0
        ] if mcp_stage1_enabled else []
        mcp_theme_fallback_keys: list[str] = []
        if (
            mcp_stage1_enabled
            and not query_profile.get("artifactIntent")
            and list(query_profile.get("primaryThemeIntents") or [])
        ):
            direct_theme_hits = [
                server_name
                for server_name, profile in mcp_server_profiles.items()
                if str(profile.get("skillClassLike") or "").strip().lower() in _THEME_HEAVY_CLASSES
                and _has_direct_dynamic_theme_match(profile, query_profile)
            ]
            if not direct_theme_hits:
                ranked_mcp_fallback = sorted(
                    (
                        (
                            _score_dynamic_family_fallback_entry(
                                query_text=query_text,
                                query_tokens=query_tokens,
                                query_profile=query_profile,
                                family_name=server_name,
                                description=" | ".join(
                                    part
                                    for part in [
                                        " ".join(_tool_name(tool) for tool in items if _tool_name(tool)),
                                        " ".join(_tool_description(tool) for tool in items if _tool_description(tool)),
                                    ]
                                    if str(part or "").strip()
                                ),
                                profile=mcp_server_profiles.get(server_name, {}),
                            ),
                            server_name.lower(),
                            server_name,
                        )
                        for server_name, items in mcp_server_map.items()
                    ),
                    key=lambda row: (-row[0], row[1]),
                )
                mcp_theme_fallback_keys = [row[2] for row in ranked_mcp_fallback if row[0] > 0][:_DYNAMIC_THEME_FALLBACK_LIMIT]
                if mcp_theme_fallback_keys:
                    mcp_stage1_hits = _merge_keepalive_keys(
                        mcp_stage1_hits,
                        mcp_theme_fallback_keys,
                        max(len(mcp_stage1_hits) + len(mcp_theme_fallback_keys), effective_mcp_stage1_limit),
                    )
        mcp_stage1_hit_count = len(mcp_stage1_hits)
        mcp_pool_server_keys = mcp_stage1_hits[:effective_mcp_stage1_limit] if effective_mcp_stage1_limit > 0 else []
        mcp_pool = list(mcp_pool_server_keys)
        mcp_stage1_shortlist_keys = list(mcp_pool_server_keys)
        mcp_stage2_candidate_keys = list(mcp_stage1_shortlist_keys) if mcp_stage1_enabled else list(mcp_server_map.keys())
        selected_mcp_server_keys = list(mcp_stage1_shortlist_keys) if mcp_stage1_enabled else list(mcp_server_map.keys())
        mcp_routing_mode = "stage1_only" if mcp_stage1_enabled else "unfiltered"

        def _expand_mcp_server_keys(server_keys: list[str]) -> list[Any]:
            expanded: list[Any] = []
            seen: set[str] = set()
            for server_key in server_keys:
                items = sorted(
                    mcp_server_map.get(server_key, []),
                    key=lambda tool: (
                        _mcp_tool_server_name(tool).lower(),
                        _tool_name(tool).lower(),
                    ),
                )
                for tool in items:
                    identity = _mcp_tool_identity(tool)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    expanded.append(tool)
            return expanded

        selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)

        skill_family_map = {
            str(item.get("path") or item.get("name") or item.get("folder") or "").strip(): item
            for item in skill_stage2_candidate_entries
            if str(item.get("path") or item.get("name") or item.get("folder") or "").strip()
        }
        skill_state: dict[str, Any] = {
            "mode": skill_routing_mode,
            "reason": prefilter_reason or (
                "Stage 2 is disabled; using the Stage 1 shortlist directly."
                if skill_stage1_enabled
                else "Stages 1 and 2 are disabled; exposing the available inventory directly."
            ),
            "timedOut": False,
            "cacheHit": False,
            "bypassed": not stage2_runtime_available or not skill_stage2_configured,
        }
        mcp_state: dict[str, Any] = {
            "mode": mcp_routing_mode,
            "reason": prefilter_reason or (
                "Stage 2 is disabled; using the Stage 1 shortlist directly."
                if mcp_stage1_enabled
                else "Stages 1 and 2 are disabled; exposing the available inventory directly."
            ),
            "timedOut": False,
            "cacheHit": False,
            "bypassed": not stage2_runtime_available or not mcp_stage2_configured,
        }
        selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
        skill_stage2_enabled = bool(stage2_runtime_available and skill_stage2_configured)
        mcp_stage2_enabled = bool(stage2_runtime_available and mcp_stage2_configured)
        if skill_stage2_enabled:
            if len(skill_family_map) > skill_stage2_top_k:
                skill_state = {}
            else:
                selected_skills = list(skill_stage2_candidate_entries[:skill_stage2_top_k])
                skill_routing_mode = "llm_rerank_shortlist" if skill_stage1_enabled else "llm_rerank_full_inventory"
                skill_state = {
                    "mode": skill_routing_mode,
                    "reason": "The candidate set is already small enough to use directly.",
                    "timedOut": False,
                    "cacheHit": False,
                    "bypassed": True,
                }
        if mcp_stage2_enabled:
            if len(mcp_stage2_candidate_keys) > mcp_stage2_top_k:
                mcp_state = {}
            else:
                selected_mcp_server_keys = list(mcp_stage2_candidate_keys[:mcp_stage2_top_k])
                selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
                mcp_routing_mode = "llm_rerank_shortlist" if mcp_stage1_enabled else "llm_rerank_full_inventory"
                mcp_state = {
                    "mode": mcp_routing_mode,
                    "reason": "The candidate set is already small enough to use directly.",
                    "timedOut": False,
                    "cacheHit": False,
                    "bypassed": True,
                }
        if stage2_runtime_available:
            rerank_specs: list[tuple[str, dict[str, Any]]] = []
            if skill_stage2_enabled and len(skill_family_map) > skill_stage2_top_k:
                rerank_specs.append((
                    "skills",
                    {
                        "role": prefilter_role or "extensions_prefilter",
                        "user_query": query_text,
                        "family_label": "skills",
                        "families": [
                            {
                                "key": key,
                                "name": str(item.get("name") or item.get("folder") or "").strip() or key,
                                "description": str(item.get("description") or "").strip(),
                            }
                            for key, item in skill_family_map.items()
                        ],
                        "max_families": skill_stage2_top_k,
                        "timeout_seconds": skill_stage2_timeout,
                    },
                ))
            if mcp_stage2_enabled and len(mcp_stage2_candidate_keys) > mcp_stage2_top_k:
                rerank_specs.append((
                    "mcp",
                    {
                        "role": prefilter_role or "extensions_prefilter",
                        "user_query": query_text,
                        "family_label": "mcp",
                        "families": [
                            {
                                "key": server_name,
                                "name": server_name,
                                "description": " | ".join(
                                    part
                                    for part in [
                                        " ".join(_tool_name(tool) for tool in items if _tool_name(tool)),
                                        " ".join(_tool_description(tool) for tool in items if _tool_description(tool)),
                                        _build_family_profile_summary(mcp_server_profiles.get(server_name, {})),
                                    ]
                                    if str(part or "").strip()
                                ),
                            }
                            for server_name in mcp_stage2_candidate_keys
                            for items in [mcp_server_map.get(server_name, [])]
                            if items
                        ],
                        "max_families": mcp_stage2_top_k,
                        "timeout_seconds": mcp_stage2_timeout,
                    },
                ))
            rerank_results: dict[str, tuple[list[str], dict[str, Any]]] = {}
            if rerank_specs:
                try:
                    select_family_keys_with_llm = _get_family_selector()

                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rerank_specs), thread_name_prefix="v8-ext-route") as executor:
                        future_map = {
                            executor.submit(select_family_keys_with_llm, **kwargs): family_label
                            for family_label, kwargs in rerank_specs
                        }
                        for future in concurrent.futures.as_completed(future_map):
                            family_label = future_map[future]
                            rerank_results[family_label] = future.result()
                except Exception as exc:
                    prefilter_reason = str(exc).strip() or exc.__class__.__name__
            if "skills" in rerank_results:
                skill_keys, skill_state = rerank_results["skills"]
                selected_skills = [skill_family_map[key] for key in skill_keys if key in skill_family_map][:skill_stage2_top_k]
                skill_routing_mode = "llm_rerank_shortlist" if skill_stage1_enabled else "llm_rerank_full_inventory"
            if "mcp" in rerank_results:
                mcp_keys, mcp_state = rerank_results["mcp"]
                selected_mcp_server_keys = [key for key in mcp_keys if key in mcp_server_map]
                selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
                mcp_routing_mode = "llm_rerank_shortlist" if mcp_stage1_enabled else "llm_rerank_full_inventory"
            if bool(skill_state.get("timedOut")):
                prefilter_reason = "timeout"
                if skill_stage1_enabled:
                    selected_skills = list(skill_stage1_shortlist)
                    skill_routing_mode = "fallback_stage1"
                else:
                    selected_skills = list(skill_entries)
                    skill_routing_mode = "fallback_unfiltered"
            if bool(mcp_state.get("timedOut")):
                prefilter_reason = "timeout"
                if mcp_stage1_enabled:
                    selected_mcp_server_keys = list(mcp_stage1_shortlist_keys)
                    mcp_routing_mode = "fallback_stage1"
                else:
                    selected_mcp_server_keys = list(mcp_server_map.keys())
                    mcp_routing_mode = "fallback_unfiltered"
                selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
        selected_mcp_server_keys = _unique_preserve_order(selected_mcp_server_keys)
        selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)

        inherited_skill_ids_ordered = [
            str(item or "").strip()
            for item in list(inherited_skill_ids or [])
            if str(item or "").strip()
        ]
        inherited_skill_names_ordered = [
            str(item or "").strip()
            for item in list(inherited_skill_names or [])
            if str(item or "").strip()
        ]
        if inherited_skill_ids_ordered or inherited_skill_names_ordered:
            skill_by_id: dict[str, dict[str, Any]] = {}
            skill_by_name: dict[str, dict[str, Any]] = {}
            for item in skill_entries:
                skill_id = str(item.get("skillId") or "").strip()
                if skill_id and skill_id not in skill_by_id:
                    skill_by_id[skill_id] = item
                for candidate in (
                    item.get("name"),
                    item.get("folder"),
                    item.get("skillName"),
                ):
                    normalized_candidate = str(candidate or "").strip()
                    if normalized_candidate and normalized_candidate not in skill_by_name:
                        skill_by_name[normalized_candidate] = item
                    lower_candidate = normalized_candidate.lower()
                    if lower_candidate and lower_candidate not in skill_by_name:
                        skill_by_name[lower_candidate] = item
            inherited_skill_entries: list[dict[str, Any]] = []
            inherited_entry_keys: set[str] = set()

            def _append_inherited_skill(item: dict[str, Any] | None) -> None:
                if not isinstance(item, dict):
                    return
                key = str(item.get("skillId") or item.get("path") or item.get("name") or item.get("folder") or "").strip()
                if not key or key in inherited_entry_keys:
                    return
                inherited_entry_keys.add(key)
                inherited_skill_entries.append(item)

            for inherited_id in inherited_skill_ids_ordered:
                _append_inherited_skill(skill_by_id.get(inherited_id))
            for inherited_name in inherited_skill_names_ordered:
                _append_inherited_skill(skill_by_name.get(inherited_name) or skill_by_name.get(inherited_name.lower()))
            if inherited_skill_entries:
                merged_skills: list[dict[str, Any]] = []
                seen_skill_keys: set[str] = set()
                if skill_routing_mode in {"unfiltered", "fallback_unfiltered"}:
                    skill_exposure_cap = len(skill_entries)
                elif skill_routing_mode == "fallback_stage1":
                    skill_exposure_cap = effective_skill_stage1_limit
                elif str(skill_routing_mode or "").startswith("llm_rerank"):
                    skill_exposure_cap = skill_stage2_top_k
                elif skill_stage1_enabled:
                    skill_exposure_cap = effective_skill_stage1_limit
                elif skill_stage2_configured:
                    skill_exposure_cap = skill_stage2_top_k
                else:
                    skill_exposure_cap = len(skill_entries)
                skill_exposure_cap = max(0, int(skill_exposure_cap or 0))
                for item in inherited_skill_entries + list(selected_skills):
                    key = str(item.get("skillId") or item.get("path") or item.get("name") or item.get("folder") or "").strip()
                    if not key or key in seen_skill_keys:
                        continue
                    seen_skill_keys.add(key)
                    merged_skills.append(item)
                    if skill_exposure_cap and len(merged_skills) >= skill_exposure_cap:
                        break
                selected_skills = merged_skills

        plugin_skill_roots: set[str] = set()
        for item in list(plugin_projection.get("skills") or []):
            installed_roots = [
                str(Path(str(root)).resolve())
                for root in list(item.get("installedRoots") or [])
                if str(root).strip()
            ]
            if installed_roots:
                plugin_skill_roots.update(installed_roots)
                continue
            target_directory = str(item.get("targetDirectory") or "").strip()
            if target_directory:
                plugin_skill_roots.add(str((Path.home() / ".agents" / "skills" / target_directory).resolve()))
        if plugin_skill_roots:
            selected_keys = {
                str(item.get("skillId") or item.get("skillRoot") or item.get("path") or "").strip()
                for item in selected_skills
            }
            for item in raw_skill_entries:
                item_path = str(item.get("skillRoot") or item.get("path") or "").strip()
                if not item_path:
                    continue
                resolved_item_path = Path(item_path).resolve()
                if not any(root == str(resolved_item_path) or root in {str(parent) for parent in resolved_item_path.parents} for root in plugin_skill_roots):
                    continue
                key = str(item.get("skillId") or item.get("skillRoot") or item.get("path") or "").strip()
                if key and key not in selected_keys:
                    selected_keys.add(key)
                    selected_skills.append(item)

        plugin_mcp_tools = list(plugin_projection.get("mcpTools") or [])
        if plugin_mcp_tools:
            seen_mcp_tool_names = {_tool_name(tool) for tool in selected_mcp_tools}
            for tool in plugin_mcp_tools:
                tool_name = _tool_name(tool)
                if tool_name and tool_name not in seen_mcp_tool_names:
                    seen_mcp_tool_names.add(tool_name)
                    selected_mcp_tools.append(tool)
                server_name = _mcp_tool_server_name(tool)
                mcp_server_map.setdefault(server_name, []).append(tool)
                if server_name not in selected_mcp_server_keys:
                    selected_mcp_server_keys.append(server_name)

        if privileged_plugin_channel_active:
            skill_routing_mode = "privileged_plugin_bundle"
            mcp_routing_mode = "privileged_plugin_bundle"
            skill_stage2_enabled = False
            mcp_stage2_enabled = False
            skill_state = {
                "mode": skill_routing_mode,
                "reason": "The current request explicitly selected an installed plugin package; generic Skill prefiltering and injection were bypassed.",
                "timedOut": False,
                "cacheHit": False,
                "bypassed": True,
            }
            mcp_state = {
                "mode": mcp_routing_mode,
                "reason": "The current request explicitly selected an installed plugin package; generic MCP prefiltering and injection were bypassed.",
                "timedOut": False,
                "cacheHit": False,
                "bypassed": True,
            }

        selected_skill_ids = [str(item.get("skillId") or "").strip() for item in selected_skills if str(item.get("skillId") or "").strip()]
        selected_skill_names = [str(item.get("name") or item.get("folder") or "") for item in selected_skills]
        skill_stage1_entries = [_skill_entry_payload(item) for item in skill_stage1_shortlist]
        selected_skill_entries = [_skill_entry_payload(item) for item in selected_skills]
        skill_name_counts: dict[str, int] = {}
        for item in skill_entries:
            normalized_skill_name = str(item.get("skillName") or item.get("name") or item.get("folder") or "").strip().lower()
            if not normalized_skill_name:
                continue
            skill_name_counts[normalized_skill_name] = skill_name_counts.get(normalized_skill_name, 0) + 1
        exposed_mcp_tool_names = [_tool_name(tool) for tool in selected_mcp_tools]
        mcp_stage1_servers = [
            _mcp_server_payload(server_key, mcp_server_map.get(server_key, []))
            for server_key in mcp_stage1_shortlist_keys
            if mcp_server_map.get(server_key)
        ]
        selected_mcp_servers = [
            _mcp_server_payload(server_key, mcp_server_map.get(server_key, []))
            for server_key in selected_mcp_server_keys
            if mcp_server_map.get(server_key)
        ]
        selected_mcp_profiles = [
            dict(mcp_server_profiles.get(server_key) or {})
            for server_key in selected_mcp_server_keys
            if mcp_server_profiles.get(server_key)
        ]
        query_document_sub_intent = str(query_profile.get("documentSubIntent") or "").strip().lower() or None
        mcp_document_subintent_matched = len(
            [
                profile
                for profile in selected_mcp_profiles
                if query_document_sub_intent
                and query_document_sub_intent in _normalize_profile_items(profile.get("documentSubIntentHints"))
            ]
        )
        privileged_plugin_tools = [plugin_cli] if plugin_projection.get("cliProfiles") else []
        filtered_tools = base_tools + selected_mcp_tools + privileged_plugin_tools
        route_modes = [skill_routing_mode, mcp_routing_mode]
        distinct_route_modes = _unique_preserve_order([mode for mode in route_modes if str(mode or "").strip()])
        prefilter_mode = distinct_route_modes[0] if len(distinct_route_modes) == 1 else "mixed"
        prefilter_reason = (
            str(skill_state.get("reason") or "")
            if privileged_plugin_channel_active
            else (
                prefilter_reason
                or skill_state.get("reason")
                or mcp_state.get("reason")
                or ""
            )
        )

        if privileged_plugin_channel_active:
            if str(plugin_channel.get("mode") or "") == "privileged_bundle_error":
                lines = [
                    "\n[Plugin Package]",
                    "- The current request explicitly selected a plugin package, but its registered/install/grant projection could not be resolved.",
                    "- Generic Extensions Skill/MCP prefiltering and injection are disabled for this route so the failure cannot silently fall back to an unrelated capability.",
                    "- Do not claim the plugin was authorized or invoked. Report the blocked package route and use the plugin status/configuration path to recover.",
                ]
            else:
                lines = [
                    "\n[Plugin Package]",
                    "- The current request explicitly selected a registered, installed plugin package.",
                    "- Generic Extensions Skill/MCP prefiltering and injection are disabled for this route; only the grant-bounded package projection below is exposed.",
                    "- Installation, selection, and authorization are distinct facts. Only projected components may be invoked, and every invocation is revalidated.",
                ]
            blocked_plugins = [
                dict(item)
                for item in list(plugin_channel.get("blocked") or [])
                if isinstance(item, dict)
            ]
            for item in blocked_plugins:
                lines.append(
                    f"- Blocked plugin component: {item.get('pluginId') or 'unknown'} | status={item.get('status') or 'invalid'} | reason={item.get('reason') or 'unavailable'}"
                )
        else:
            lines = [
                "\n[Extensions Runtime]",
                "- Extension candidates are optional discovery references, not a selected route or a reason to ask the user. Prefer the owning local/native capability; fetch or invoke a candidate only when the user named it or it materially improves the method or evidence.",
                f"- Skill candidates: {len(selected_skill_names)} / installed: {len(skill_entries)}",
                f"- MCP tool candidates: {len(exposed_mcp_tool_names)} / connected tools: {len(mcp_tools)}",
            ]
        if cross_runtime_escape:
            lines.append("- Cross-runtime escape is active because the task indicates a blocked or runtime-switching flow.")
        if any(mode.startswith("llm_rerank") for mode in (skill_routing_mode, mcp_routing_mode)):
            lines.append(f"- Candidate prefilter: two-stage ranking via {prefilter_model_id}.")
        elif any(mode.startswith("fallback") for mode in (skill_routing_mode, mcp_routing_mode)):
            details = prefilter_reason or "No extension-prefilter model is currently bound."
            lines.append(f"- Candidate prefilter: lexical fallback ({_truncate(details, 120)}).")
        else:
            if not (skill_stage1_enabled or mcp_stage1_enabled):
                lines.append("- Candidate prefilter is disabled; the connected inventory remains available.")
        if selected_skill_names:
            lines.append("- Relevant Skill catalog entries:")
            for entry in selected_skill_entries:
                source_label = str(entry.get("sourceType") or "global").strip() or "global"
                normalized_entry_name = str(entry.get("skillName") or "").strip().lower()
                has_duplicate_skill_name = skill_name_counts.get(normalized_entry_name, 0) > 1
                lines.append(f"  - {entry.get('skillName') or 'unknown'} [{source_label}]")
                lines.append(
                    f"    - Skill description: {_truncate(_single_line_text(entry.get('description') or '') or 'No description.', 180)}"
                )
                if has_duplicate_skill_name and entry.get("skillRoot"):
                    lines.append(f"    - Root: {entry.get('skillRoot')}")
        if exposed_mcp_tool_names:
            lines.append("- Relevant MCP servers (a selected server exposes its complete tool tree):")
            for server in selected_mcp_servers:
                lines.append(f"  - {server.get('serverName')} ({server.get('toolCount')} tools)")
            lines.append("- MCP tools exposed for this turn:")
            for tool in selected_mcp_tools:
                server_name = _mcp_tool_server_name(tool)
                lines.append(
                    f"  - {_tool_name(tool)} ({server_name}): {_truncate(_tool_description(tool) or 'No description.', 80)}"
                )
        if plugin_projection.get("grants"):
            lines.append("- Privileged plugin projection from an explicit user reference or a minimal Supervisor task grant:")
            for grant in list(plugin_projection.get("grants") or []):
                lines.append(
                    f"  - {grant.get('pluginId')} | {grant.get('scope')} | components={len(list(grant.get('componentIds') or []))}"
                )
            if plugin_projection.get("cliProfiles"):
                lines.append("- Authorized CLI actions use plugin_cli actionId plus typed parameters; arbitrary argv and shell concatenation are forbidden.")
        lines.append("[/Plugin Package]" if privileged_plugin_channel_active else "[/Extensions Runtime]")

        return ExtensionRouteBundle(
            prompt_addition="\n".join(lines),
            filtered_tools=filtered_tools,
            selected_skill_names=selected_skill_names,
            selected_skill_ids=selected_skill_ids,
            skill_root_descriptors=skill_root_descriptors,
            exposed_mcp_tool_names=exposed_mcp_tool_names,
            candidate_summary={
                "mode": prefilter_mode,
                "skillInventoryRevision": skill_inventory_revision,
                "visibleRootSignature": skill_inventory.get("visibleRootSignature") or skill_inventory_revision,
                "visibleRootRevisionKey": skill_inventory.get("visibleRootRevisionKey"),
                "visibleRegistryCacheHit": bool(skill_inventory.get("visibleRegistryCacheHit")),
                "inventoryReadyState": str(
                    inventory_freshness.get("inventoryReadyState")
                    or skill_inventory.get("inventoryReadyState")
                    or skill_status.get("startupState")
                    or "cold"
                ),
                "snapshotFreshness": str(
                    inventory_freshness.get("snapshotFreshness")
                    or skill_status.get("snapshotFreshness")
                    or "cold"
                ),
                "inventoryBarrierApplied": bool(inventory_freshness.get("inventoryBarrierApplied")),
                "inventoryBarrierWaitMs": inventory_freshness.get("inventoryBarrierWaitMs"),
                "inventoryBarrierTimedOut": bool(inventory_freshness.get("inventoryBarrierTimedOut")),
                "dirtyVisibleRoots": list(
                    inventory_freshness.get("dirtyVisibleRoots")
                    or skill_inventory.get("dirtyVisibleRoots")
                    or []
                ),
                "changedRoots": list(skill_inventory.get("changedRoots") or []),
                "scopedRefreshMode": skill_inventory.get("scopedRefreshMode"),
                "mcpInventoryRevision": str(mcp_status.get("inventoryRevision") or getattr(mcp_manager, "get_inventory_revision", lambda: "")() or ""),
                "skillRefreshMode": str(skill_last_reload.get("refreshMode") or ""),
                "mcpRefreshMode": str(mcp_last_reload.get("refreshMode") or ""),
                "mcpChangedServers": dict(mcp_last_reload.get("mcpChangedServers") or {}),
                "recentSkillDiscoveryCount": len(recent_skill_discovery),
                "recentSkillKeepaliveCount": len(recent_keepalive_candidates),
                "inventoryRefreshDurationMs": {
                    "skills": skill_last_reload.get("durationMs"),
                    "mcp": mcp_last_reload.get("durationMs"),
                },
                "skillsRoutingMode": skill_routing_mode,
                "mcpRoutingMode": mcp_routing_mode,
                "pluginGrantIds": [str(item.get("grantId") or "") for item in list(plugin_projection.get("grants") or []) if str(item.get("grantId") or "")],
                "pluginSkillCount": len(list(plugin_projection.get("skills") or [])),
                "pluginMcpToolCount": len(plugin_mcp_tools),
                "pluginCliProfileCount": len(list(plugin_projection.get("cliProfiles") or [])),
                "pluginChannelMode": str(plugin_channel.get("mode") or "extensions_default"),
                "pluginPrefilterBypassed": bool(plugin_channel.get("prefilterBypassed")),
                "pluginRequestedIds": list(plugin_channel.get("requestedPluginIds") or []),
                "pluginInstalledIds": list(plugin_channel.get("installedPluginIds") or []),
                "pluginProjectedIds": list(plugin_channel.get("projectedPluginIds") or []),
                "pluginBlockedCount": len(list(plugin_channel.get("blocked") or [])),
                "modelId": prefilter_model_id,
                "role": prefilter_role,
                "reason": prefilter_reason or None,
                "prefilterTimedOut": bool(any(bool(state.get("timedOut")) for state in (skill_state, mcp_state))),
                "prefilterCacheHit": bool(any(bool(state.get("cacheHit")) for state in (skill_state, mcp_state))),
                "queryAnalysisCacheHit": query_analysis_cache_hit,
                "lexiconSignature": str(lexicon_state.get("signature") or _EXTENSION_LEXICON_SIGNATURE),
                "lexiconCoreSignature": str(lexicon_state.get("coreSignature") or _EXTENSION_LEXICON_CORE_SIGNATURE),
                "lexiconLocales": list(lexicon_state.get("locales") or _EXTENSION_LEXICON_LOCALES),
                "lexiconLoadErrors": list(lexicon_state.get("loadErrors") or _EXTENSION_LEXICON_LOAD_ERRORS),
                "marketLexiconEnabled": bool(lexicon_state.get("marketEnabled") or _MARKET_LEXICON_ENABLED),
                "marketLexiconSignature": str(lexicon_state.get("marketSignature") or _MARKET_LEXICON_SIGNATURE),
                "marketLexiconLocales": list(lexicon_state.get("marketLocales") or _MARKET_LEXICON_LOCALES),
                "marketLexiconLoadErrors": list(lexicon_state.get("marketLoadErrors") or _MARKET_LEXICON_LOAD_ERRORS),
                "marketLexiconHitTerms": list(market_enrichment.get("matchedTerms") or []),
                "marketLexiconContributionScore": int(market_enrichment.get("contributionScore") or 0),
                "stage1Enabled": {
                    "skills": skill_stage1_enabled,
                    "mcp": mcp_stage1_enabled,
                },
                "stage1TopK": {
                    "skills": effective_skill_stage1_limit,
                    "mcp": effective_mcp_stage1_limit,
                },
                "stage2Enabled": {
                    "skills": skill_stage2_enabled,
                    "mcp": mcp_stage2_enabled,
                },
                "stage2TopK": {
                    "skills": skill_stage2_top_k,
                    "mcp": mcp_stage2_top_k,
                },
                "llmTimeoutSeconds": {
                    "skills": skill_stage2_timeout,
                    "mcp": mcp_stage2_timeout,
                },
                "routingMode": prefilter_mode,
                "skills": selected_skill_names,
                "selectedSkillIds": selected_skill_ids,
                "artifactIntent": query_profile.get("artifactIntent"),
                "documentSubIntent": query_profile.get("documentSubIntent"),
                "operationIntent": query_profile.get("operationIntent"),
                "directCanonicalFamilies": list(query_profile.get("directCanonicalFamilies") or []),
                "canonicalFamilies": list(query_profile.get("canonicalFamilies") or []),
                "primaryCanonicalFamily": query_profile.get("primaryCanonicalFamily"),
                "shortCanonicalNarrowing": bool(query_profile.get("shortCanonicalNarrowing")),
                "shortCanonicalNarrowingApplied": short_canonical_narrowing_applied,
                "primaryThemeIntents": list(query_profile.get("primaryThemeIntents") or []),
                "secondaryThemeHints": list(query_profile.get("secondaryThemeHints") or []),
                "rankingSignals": {
                    "artifactAnchor": bool(query_profile.get("artifactIntent")),
                    "documentSubIntent": str(query_profile.get("documentSubIntent") or "").strip() or None,
                    "operationIntent": bool(query_profile.get("operationIntent")),
                    "topicTokenCount": len(list(query_profile.get("topicTokens") or [])),
                },
                "themeRankingSignals": {
                    "themeIntent": bool(query_profile.get("primaryThemeIntents")),
                    "secondaryThemeHints": len(list(query_profile.get("secondaryThemeHints") or [])),
                    "artifactAnchorPresent": bool(query_profile.get("artifactIntent")),
                    "fallbackInjectedCount": len(theme_fallback_candidates),
                },
                "profileMatchedCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if bool((item.get("capabilityProfile") or {}).get("primaryArtifactTypes"))
                        or bool((item.get("capabilityProfile") or {}).get("primaryOperations"))
                    ]
                ),
                "profileBackfilledCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if str((item.get("capabilityProfile") or {}).get("profileSource") or "").strip() == "llm_assisted"
                    ]
                ),
                "themeMatchedCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if bool((item.get("themeProfile") or {}).get("primaryThemes"))
                        or bool((item.get("themeProfile") or {}).get("secondaryThemeTags"))
                    ]
                ),
                "themeBackfilledCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if str((item.get("themeProfile") or {}).get("themeSource") or "").strip() == "llm_assisted"
                    ]
                ),
                "mcpProfileMatchedCount": len(
                    [
                        profile
                        for profile in selected_mcp_profiles
                        if bool(profile.get("primaryArtifactTypes"))
                        or bool(profile.get("primaryOperations"))
                        or bool(profile.get("documentSubIntentHints"))
                    ]
                ),
                "mcpThemeMatchedCount": len(
                    [
                        profile
                        for profile in selected_mcp_profiles
                        if bool(profile.get("primaryThemes")) or bool(profile.get("secondaryThemeTags"))
                    ]
                ),
                "mcpDocumentSubIntentMatched": mcp_document_subintent_matched,
                "mcpThemeFallbackInjectedCount": len(mcp_theme_fallback_keys),
                "skillStage1Entries": skill_stage1_entries,
                "skillEntries": selected_skill_entries,
                "skillRootDescriptors": skill_root_descriptors,
                "mcpTools": exposed_mcp_tool_names,
                "mcpStage1Servers": mcp_stage1_servers,
                "mcpServers": selected_mcp_servers,
                "mcpFamilies": selected_mcp_servers,
                "seedUnit": "skill_or_mcp_server",
                "skillCandidates": len(selected_skill_names),
                "mcpCandidates": len(exposed_mcp_tool_names),
                "mcpServerCandidates": len(selected_mcp_servers),
                "skillInventoryCount": len(skill_entries),
                "skillPoolSize": len(skill_entries),
                "skillStage1HitCount": skill_stage1_hit_count,
                "skillStage1ShortlistCount": len(skill_stage1_shortlist),
                "skillLexicalPoolSize": len(skill_stage1_shortlist),
                "skillFinalExposedCount": len(selected_skill_entries),
                "mcpInventoryCount": len(mcp_server_map),
                "mcpPoolSize": len(mcp_server_map),
                "mcpStage1HitCount": mcp_stage1_hit_count,
                "mcpStage1ShortlistCount": len(mcp_stage1_shortlist_keys),
                "mcpLexicalPoolSize": len(mcp_stage1_shortlist_keys),
                "mcpFinalExposedCount": len(selected_mcp_servers),
                "mcpServerPoolSize": len(mcp_server_map),
                "mcpServerCount": len(mcp_server_map),
                "mcpFamilyPoolSize": len(mcp_server_map),
                "mcpFamilyCount": len(mcp_server_map),
                "mcpExpandedToolCount": len(exposed_mcp_tool_names),
                "mcpSelectedServers": list(selected_mcp_server_keys),
                "mcpSelectedFamilies": list(selected_mcp_server_keys),
                "requestedSkillLimit": skill_limit,
                "requestedMcpLimit": mcp_limit,
                "effectiveSkillLimit": len(selected_skill_entries),
                "effectiveMcpLimit": len(selected_mcp_servers),
                "crossRuntimeEscape": cross_runtime_escape,
                "totalInstalledSkills": len(skill_entries),
                "totalConnectedMcpTools": len(mcp_tools),
                "agentCount": len(list(loaded_agents or [])),
            },
        )

    def build_supervisor_route(
        self,
        *,
        user_query: str,
        supervisor_tools: list[Any],
        loaded_agents: list[dict[str, Any]] | None = None,
        skill_limit: int = 5,
        mcp_limit: int = 2,
    ) -> ExtensionRouteBundle:
        context_payload = self._resolve_event_context()
        session_id = str(context_payload.get("session_id") or "").strip() or "global"
        inventory_freshness = self._apply_inventory_freshness_mode(
            freshness_mode=_INVENTORY_FRESHNESS_GUARDED,
            reason="supervisor_route",
            include_scoped=True,
            session_id=str(context_payload.get("session_id") or "").strip() or None,
            explicit_workspace_id=str(context_payload.get("workspace_id") or "").strip() or None,
            explicit_workspace_path=str(context_payload.get("workspace_path") or "").strip() or None,
            explicit_project_id=str(context_payload.get("project_id") or "").strip() or None,
            runtime_kind=str(context_payload.get("runtime_kind") or "chat").strip() or "chat",
        )
        skill_inventory = self._resolve_skill_inventory(
            force_refresh=False,
            include_scoped=True,
            session_id=str((inventory_freshness.get("skillContext") or {}).get("session_id") or "").strip() or None,
            explicit_workspace_id=str((inventory_freshness.get("skillContext") or {}).get("explicit_workspace_id") or "").strip() or None,
            explicit_workspace_path=str((inventory_freshness.get("skillContext") or {}).get("explicit_workspace_path") or "").strip() or None,
            explicit_project_id=str((inventory_freshness.get("skillContext") or {}).get("explicit_project_id") or "").strip() or None,
            runtime_kind=str((inventory_freshness.get("skillContext") or {}).get("runtime_kind") or "chat").strip() or "chat",
            exclude_root_paths=set(inventory_freshness.get("excludeRootPaths") or set()),
        )
        lexicon_signature = str(_ensure_extension_lexicon_state().get("signature") or _EXTENSION_LEXICON_SIGNATURE)
        normalized_query = " ".join(_tokenize(user_query)) or str(user_query or "").strip().lower()
        tool_signature = ",".join(sorted(_tool_name(tool) for tool in supervisor_tools if _tool_name(tool)))
        inventory_revision = self._inventory_revision_key(skill_inventory)
        plugin_reference_signature = json.dumps(
            context_payload.get("plugin_references")
            or context_payload.get("pluginReferences")
            or [],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        cache_key = "|".join(
            [
                session_id,
                str(context_payload.get("project_id") or ""),
                str(context_payload.get("workspace_id") or ""),
                str(context_payload.get("workspace_path") or ""),
                normalized_query,
                inventory_revision,
                str(len(list(loaded_agents or []))),
                str(skill_limit),
                str(mcp_limit),
                tool_signature,
                lexicon_signature,
                plugin_reference_signature,
                ",".join(
                    str(item.get("grantId") or "")
                    for item in __import__("runtimes.plugin_manager.service", fromlist=["plugin_manager_service"])
                    .plugin_manager_service.active_grants(
                        session_id=str(context_payload.get("session_id") or ""),
                        run_id=str(context_payload.get("run_id") or "").strip() or None,
                        grantee_type=(
                            "subagent"
                            if str(context_payload.get("runtime_kind") or "chat").strip() == "subagent"
                            and str(context_payload.get("agent_id") or "supervisor").strip() != "supervisor"
                            else "supervisor"
                        ),
                        grantee_id=str(context_payload.get("agent_id") or "supervisor").strip() or "supervisor",
                        delegation_id=str(
                            context_payload.get("delegation_id")
                            or context_payload.get("delegationId")
                            or ""
                        ).strip() or None,
                    )
                ) if str(context_payload.get("session_id") or "").strip() else "",
            ]
        )
        now = time.monotonic()
        cache_allowed = not bool(inventory_freshness.get("inventoryBarrierTimedOut"))
        cached = self._route_cache.get(cache_key) if cache_allowed else None
        if cached and (now - cached[0]) <= self._route_cache_ttl_seconds:
            return cached[1]

        bundle = self.build_contextual_route(
            user_query=user_query,
            available_tools=supervisor_tools,
            loaded_agents=loaded_agents,
            skill_limit=skill_limit,
            mcp_limit=mcp_limit,
            freshness_mode=_INVENTORY_FRESHNESS_GUARDED,
            _pre_resolved_skill_inventory=skill_inventory,
            _pre_resolved_inventory_freshness=inventory_freshness,
        )
        if cache_allowed:
            self._route_cache[cache_key] = (now, bundle)
            if len(self._route_cache) > 128:
                stale_keys = sorted(self._route_cache.items(), key=lambda item: item[1][0])[:32]
                for stale_key, _ in stale_keys:
                    self._route_cache.pop(stale_key, None)
        return bundle

    def bind_execution_context(self, **context: Any):
        current = dict(_EXTENSION_CONTEXT.get() or {})
        current.update({key: value for key, value in context.items() if value is not None})
        return _EXTENSION_CONTEXT.set(current)

    def reset_execution_context(self, token: contextvars.Token) -> None:
        _EXTENSION_CONTEXT.reset(token)

    def _resolve_event_context(self) -> dict[str, Any]:
        payload = dict(_EXTENSION_CONTEXT.get() or {})
        runtime_context = get_runtime_context()
        for key in (
            "session_id",
            "conversation_id",
            "run_id",
            "agent_id",
            "workspace_id",
            "workspace_path",
            "project_id",
            "runtime_kind",
            "delegation_id",
            "delegationId",
            "delegation_depth",
            "delegationDepth",
            "plugin_references",
            "pluginReferences",
            "plugin_authorizations",
            "pluginAuthorizations",
        ):
            if payload.get(key) is None and runtime_context.get(key) is not None:
                payload[key] = runtime_context.get(key)
        return payload

    def _emit(self, topic: str, payload: dict[str, Any], *, node: str) -> None:
        context_payload = self._resolve_event_context()
        session_id = str(context_payload.get("session_id") or "")
        if not session_id:
            return
        run_id = str(context_payload.get("run_id") or "").strip()
        if run_id:
            try:
                run_status = str((db.get_run_record(run_id) or {}).get("status") or "").strip().lower()
            except Exception:
                run_status = ""
            if run_status in {"cancelled", "canceled"}:
                return
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=str(context_payload.get("conversation_id") or session_id),
            run_id=run_id or None,
            source=_extension_runtime_source(node=node),
        )
        emitter.emit(topic, payload, source=_extension_runtime_source(node=node))

    def emit_route_selected(self, *, user_query: str, route_bundle: ExtensionRouteBundle) -> None:
        candidate_summary = route_bundle.candidate_summary if isinstance(route_bundle.candidate_summary, dict) else {}
        compact_counts: dict[str, Any] = {}
        for key, value in candidate_summary.items():
            if isinstance(value, list):
                compact_counts[f"{key}Count"] = len(value)
            elif isinstance(value, dict):
                compact_counts[f"{key}KeyCount"] = len(value)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                compact_counts[key] = value
        self._emit(
            "extension.route.selected",
            {
                "queryPreview": _truncate(user_query, 160),
                "boundToolNames": [
                    str(getattr(tool_ref, "name", "") or "").strip()
                    for tool_ref in list(route_bundle.filtered_tools or [])
                    if str(getattr(tool_ref, "name", "") or "").strip()
                ],
                "skillCandidates": route_bundle.selected_skill_names,
                "selectedSkillIds": route_bundle.selected_skill_ids,
                "mcpToolCandidates": route_bundle.exposed_mcp_tool_names,
                "counts": compact_counts,
                "omittedFields": [
                    "skillStage1Entries",
                    "skillEntries",
                    "skillRootDescriptors",
                    "mcpStage1Servers",
                    "mcpServers",
                ],
                "routing": {
                    "mode": candidate_summary.get("mode"),
                    "routingMode": candidate_summary.get("routingMode"),
                    "skillsRoutingMode": candidate_summary.get("skillsRoutingMode"),
                    "mcpRoutingMode": candidate_summary.get("mcpRoutingMode"),
                    "skillInventoryRevision": candidate_summary.get("skillInventoryRevision"),
                    "visibleRootSignature": candidate_summary.get("visibleRootSignature"),
                    "changedRootCount": len(list(candidate_summary.get("changedRoots") or [])),
                    "scopedRefreshMode": candidate_summary.get("scopedRefreshMode"),
                    "mcpInventoryRevision": candidate_summary.get("mcpInventoryRevision"),
                    "skillRefreshMode": candidate_summary.get("skillRefreshMode"),
                    "mcpRefreshMode": candidate_summary.get("mcpRefreshMode"),
                    "recentSkillKeepaliveCount": candidate_summary.get("recentSkillKeepaliveCount"),
                    "mcpChangedServerCount": len(list(candidate_summary.get("mcpChangedServers") or [])),
                    "inventoryRefreshDurationMs": candidate_summary.get("inventoryRefreshDurationMs"),
                    "lexiconSignature": candidate_summary.get("lexiconSignature"),
                    "lexiconCoreSignature": candidate_summary.get("lexiconCoreSignature"),
                    "lexiconLocaleCount": len(list(candidate_summary.get("lexiconLocales") or [])),
                    "lexiconLoadErrorCount": len(list(candidate_summary.get("lexiconLoadErrors") or [])),
                    "marketLexiconEnabled": candidate_summary.get("marketLexiconEnabled"),
                    "marketLexiconSignature": candidate_summary.get("marketLexiconSignature"),
                    "marketLexiconLocaleCount": len(list(candidate_summary.get("marketLexiconLocales") or [])),
                    "marketLexiconLoadErrorCount": len(list(candidate_summary.get("marketLexiconLoadErrors") or [])),
                    "marketLexiconHitTermCount": len(list(candidate_summary.get("marketLexiconHitTerms") or [])),
                    "marketLexiconContributionScore": candidate_summary.get("marketLexiconContributionScore"),
                    "queryAnalysisCacheHit": candidate_summary.get("queryAnalysisCacheHit"),
                    "directCanonicalFamilyCount": len(list(candidate_summary.get("directCanonicalFamilies") or [])),
                    "canonicalFamilyCount": len(list(candidate_summary.get("canonicalFamilies") or [])),
                    "primaryCanonicalFamily": candidate_summary.get("primaryCanonicalFamily"),
                    "shortCanonicalNarrowing": candidate_summary.get("shortCanonicalNarrowing"),
                    "shortCanonicalNarrowingApplied": candidate_summary.get("shortCanonicalNarrowingApplied"),
                    "modelId": candidate_summary.get("modelId"),
                    "role": candidate_summary.get("role"),
                    "stage1Enabled": candidate_summary.get("stage1Enabled"),
                    "stage1TopK": candidate_summary.get("stage1TopK"),
                    "stage2Enabled": candidate_summary.get("stage2Enabled"),
                    "stage2TopK": candidate_summary.get("stage2TopK"),
                    "llmTimeoutSeconds": candidate_summary.get("llmTimeoutSeconds"),
                    "skillInventoryCount": candidate_summary.get("skillInventoryCount"),
                    "skillStage1ShortlistCount": candidate_summary.get("skillStage1ShortlistCount"),
                    "skillFinalExposedCount": candidate_summary.get("skillFinalExposedCount"),
                    "mcpInventoryCount": candidate_summary.get("mcpInventoryCount"),
                    "mcpStage1ShortlistCount": candidate_summary.get("mcpStage1ShortlistCount"),
                    "mcpFinalExposedCount": candidate_summary.get("mcpFinalExposedCount"),
                    "skillPoolSize": candidate_summary.get("skillPoolSize"),
                    "mcpPoolSize": candidate_summary.get("mcpPoolSize"),
                },
            },
            node="route_selected",
        )
        if route_bundle.exposed_mcp_tool_names:
            self._emit(
                "extension.mcp.candidate_exposed",
                {
                    "toolNames": route_bundle.exposed_mcp_tool_names,
                    "count": len(route_bundle.exposed_mcp_tool_names),
                },
                node="mcp_candidate_exposed",
            )

    def emit_skill_loaded(self, *, skill_id: str | None = None, skill_name: str, skill_path: str) -> None:
        normalized_identity = str(skill_id or skill_path or skill_name or "").strip()
        if normalized_identity:
            self._blocked_skill_records = [
                item
                for item in self._blocked_skill_records
                if str(item.get("skillId") or item.get("skillPath") or item.get("skillName") or "").strip() != normalized_identity
            ]
        self._emit(
            "extension.skill.loaded",
            {
                "skillId": skill_id,
                "skillName": skill_name,
                "skillPath": skill_path,
            },
            node="skill_loaded",
        )

    def emit_skill_blocked(
        self,
        *,
        skill_id: str,
        skill_name: str,
        skill_path: str,
        root_path: str,
        source_type: str,
        verdict: str,
        confidence: float,
        skill_trust_score: int,
        audit_id: str,
        reasons: list[str],
        flagged_files: list[dict[str, Any]],
    ) -> None:
        payload = {
            "skillId": skill_id,
            "skillName": skill_name,
            "skillPath": skill_path,
            "rootPath": root_path,
            "sourceType": source_type,
            "verdict": verdict,
            "confidence": confidence,
            "skillTrustScore": skill_trust_score,
            "auditId": audit_id,
            "reasons": list(reasons or []),
            "flaggedFiles": list(flagged_files or []),
        }
        self._blocked_skill_records.append(
            {
                **payload,
                "blockedAt": self._now_iso(),
            }
        )
        self._blocked_skill_records = self._blocked_skill_records[-24:]
        self._route_cache.clear()
        self._clear_dynamic_family_profile_caches()
        self._emit("extension.skill.blocked", payload, node="skill_blocked")
        self._emit("safety.skill_blocked", payload, node="skill_blocked")

    def emit_response_tool_calls(self, response: Any) -> None:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        if not tool_calls:
            return
        current_mcp_tools = {tool.name for tool in self.get_mcp_tools()}
        invoked = [str(item.get("name") or "") for item in tool_calls if str(item.get("name") or "") in current_mcp_tools]
        if not invoked:
            return
        self._emit(
            "extension.mcp.invoked",
            {
                "toolNames": invoked,
                "count": len(invoked),
            },
            node="mcp_invoked",
        )

    def emit_execution_completed(self, *, response: Any) -> None:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        visible_text = _truncate(sanitize_background_model_output(response).text, 200)
        tool_names = [str(item.get("name") or "") for item in tool_calls]
        payload: dict[str, Any] = {
            "hasToolCalls": bool(tool_calls),
            "toolNames": tool_names,
            "activityKind": "tool_calls_issued" if tool_calls else "model_text",
            "toolResultPending": bool(tool_calls),
        }
        if visible_text:
            payload["messagePreview"] = visible_text
        elif tool_calls:
            payload["activitySummary"] = "Requested tool execution; agent-visible knowledge arrives in the matching tool result."
        self._emit(
            "extension.execution.completed",
            payload,
            node="execution_completed",
        )

    def emit_supervisor_diagnostics(self, payload: dict[str, Any]) -> None:
        self._emit("supervisor.turn.diagnostics", payload, node="supervisor_diagnostics")

    def build_usage_summary(self, *, window_hours: int = 24) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        threshold = (now - timedelta(hours=max(window_hours, 1))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        current_mcp_tools = {tool.name for tool in self.get_mcp_tools()}

        skill_counter: Counter[str] = Counter()
        mcp_counter: Counter[str] = Counter()
        recent_events: list[dict[str, Any]] = []
        exposure_counter: Counter[str] = Counter()
        failure_count = 0

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT topic, payload_json, event_ts
                FROM runtime_events
                WHERE event_ts >= ?
                  AND (topic LIKE 'extension.%' OR topic = 'tool.started')
                ORDER BY event_ts DESC
                LIMIT 300
                """,
                (threshold,),
            )
            rows = cursor.fetchall()

        for row in rows:
            topic = str(row["topic"] or "")
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            event_ts = str(row["event_ts"] or "")
            if topic == "extension.skill.loaded":
                skill_name = str(payload.get("skillName") or "").strip()
                if skill_name:
                    skill_counter[skill_name] += 1
                    recent_events.append({"kind": "skill", "name": skill_name, "ts": event_ts})
            elif topic == "extension.skill.blocked":
                skill_name = str(payload.get("skillName") or "").strip()
                verdict = str(payload.get("verdict") or "").strip()
                if skill_name:
                    recent_events.append(
                        {
                            "kind": "skill_blocked",
                            "name": skill_name,
                            "status": verdict or "blocked",
                            "ts": event_ts,
                        }
                    )
            elif topic == "extension.mcp.invoked":
                for tool_name in list(payload.get("toolNames") or []):
                    normalized = str(tool_name or "").strip()
                    if normalized:
                        mcp_counter[normalized] += 1
                        recent_events.append({"kind": "mcp", "name": normalized, "ts": event_ts})
            elif topic == "extension.mcp.candidate_exposed":
                for tool_name in list(payload.get("toolNames") or []):
                    normalized = str(tool_name or "").strip()
                    if normalized:
                        exposure_counter[normalized] += 1
            elif topic == "extension.execution.completed":
                if not payload.get("hasToolCalls") and "失败" in str(payload.get("messagePreview") or ""):
                    failure_count += 1
            elif topic == "tool.started":
                tool = payload.get("tool") or payload
                tool_name = str(tool.get("toolName") or tool.get("tool_name") or "").strip()
                if tool_name and tool_name in current_mcp_tools:
                    mcp_counter[tool_name] += 1
                    recent_events.append({"kind": "mcp", "name": tool_name, "ts": event_ts})

        candidate_summary = {
            "skills": sum(skill_counter.values()),
            "mcpTools": sum(mcp_counter.values()),
            "currentExposedSkillCandidates": len(skill_counter),
            "currentExposedMcpCandidates": len(exposure_counter),
        }

        return {
            "windowHours": max(window_hours, 1),
            "skills": {
                "totalUses": sum(skill_counter.values()),
                "topItems": [{"name": name, "count": count} for name, count in skill_counter.most_common(6)],
            },
            "mcp": {
                "totalUses": sum(mcp_counter.values()),
                "topItems": [{"name": name, "count": count} for name, count in mcp_counter.most_common(8)],
            },
            "recentHits": recent_events[:10],
            "degradationSummary": {
                "recentFailures": failure_count,
            },
            "candidateExposure": candidate_summary,
        }

class ExtensionsRuntime:
    kind = "extensions"

    def __init__(self, service: ExtensionsRuntimeService) -> None:
        self.service = service

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "ExtensionsRuntime",
            "summary": "负责普通 Skills 与 MCP 的编目、健康、候选暴露和扩展治理；插件能力由独立特权授权链投影。",
            "responsibilities": [
                "统一管理 skills 与 MCP inventory",
                "维护扩展 catalog / health / phase / refresh 控制面",
                "为 chat / supervisor / delegation 暴露统一的扩展候选真相",
                "汇总 skill / MCP 安全与降级状态",
            ],
            "routingKeywords": ["skills", "mcp", "extensions", "扩展", "候选工具", "技能"],
            "acceptedInputs": ["catalog refresh", "inventory snapshot", "tool exposure request"],
            "producedOutputs": ["extensions snapshot", "candidate summary", "health summary"],
            "ownedSteps": ["extensions.refresh", "extensions.catalog", "extensions.route", "extensions.health"],
            "supportsPause": False,
            "supportsResume": False,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "primary",
            "promptHints": [
                "skills 和 MCP 的候选、健康与暴露语义，都应先看 ExtensionsRuntime，而不是各自直连 loader/manager。",
            ],
            "capabilities": [
                {
                    "key": "extensions.inventory",
                    "label": "扩展目录与候选暴露",
                    "summary": "统一输出 skills 与 MCP 的 catalog、health、候选筛选与热更新状态。",
                    "accepts": ["refresh request", "route query", "inventory change"],
                    "outputs": ["catalog", "health", "candidate bundle"],
                    "examples": ["扩展刷新后重建候选", "基于上下文筛选 skill 与 MCP families"],
                    "risk_level": "medium",
                }
            ],
            "metadata": {
                "umbrellaScope": ["skills", "mcp", "catalog", "health", "exposure", "safety_mediation"],
                "installMode": "background_reconcile",
                "hotInsertMode": "soft",
                "controls": [item["id"] for item in self.service._controls_payload()],
                "selectionUnits": {
                    "skills": "item",
                    "mcp": "server",
                },
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return self.service.get_runtime_snapshot()


extensions_runtime_service = ExtensionsRuntimeService()
extensions_runtime = runtime_registry.register(ExtensionsRuntime(extensions_runtime_service))

__all__ = [
    "ExtensionRouteBundle",
    "ExtensionsRuntime",
    "ExtensionsRuntimeService",
    "extensions_runtime",
    "extensions_runtime_service",
]
