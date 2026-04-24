from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


_PREFERENCE_KEY_ALIASES: dict[str, str] = {
    "shoe_brand_preference": "favorite_shoe_brand",
    "preferred_shoe_brand": "favorite_shoe_brand",
    "shoe_brand_like": "favorite_shoe_brand",
    "favorite_brand": "favorite_brand",
    "brand_preference": "favorite_brand",
    "preferred_brand": "favorite_brand",
    "preferred_model": "preferred_ai_model",
    "model_preference": "preferred_ai_model",
    "ai_model_preference": "preferred_ai_model",
    "llm_preference": "preferred_ai_model",
    "provider_preference": "preferred_ai_provider",
    "model_provider_preference": "preferred_ai_provider",
    "ai_provider_preference": "preferred_ai_provider",
    "preferred_language": "language_preference",
    "response_language": "language_preference",
    "reply_language": "language_preference",
    "writing_language": "language_preference",
    "tone_preference": "response_tone_preference",
    "reply_tone": "response_tone_preference",
    "response_tone": "response_tone_preference",
    "communication_tone": "response_tone_preference",
    "coding_language_preference": "preferred_programming_language",
    "programming_language_preference": "preferred_programming_language",
    "preferred_coding_language": "preferred_programming_language",
    "framework_preference": "preferred_framework",
    "preferred_frontend_framework": "preferred_framework",
    "preferred_backend_framework": "preferred_framework",
    "library_preference": "preferred_library",
    "editor_preference": "preferred_editor",
    "ide_preference": "preferred_editor",
    "preferred_ide": "preferred_editor",
    "package_manager_preference": "preferred_package_manager",
    "preferred_pm": "preferred_package_manager",
    "os_preference": "preferred_operating_system",
    "platform_preference": "preferred_operating_system",
    "theme_preference": "ui_theme_preference",
    "ui_preference": "ui_theme_preference",
    "communication_channel_preference": "preferred_communication_channel",
    "channel_preference": "preferred_communication_channel",
}

_KNOWLEDGE_CATEGORY_ALIASES: dict[str, str] = {
    "api integration": "api_integration",
    "api_integration": "api_integration",
    "api contract": "api_contract",
    "api_contract": "api_contract",
    "business rule": "business_logic",
    "business_rule": "business_logic",
    "business logic": "business_logic",
    "business_logic": "business_logic",
    "deployment": "deployment_ops",
    "deploy": "deployment_ops",
    "ops": "operations",
    "operations": "operations",
    "operational": "operations",
    "runtime contract": "runtime_contract",
    "runtime_contract": "runtime_contract",
    "runtime governance": "runtime_governance",
    "runtime_governance": "runtime_governance",
    "operational workflow": "operational_workflow",
    "operational_workflow": "operational_workflow",
    "project convention": "project_convention",
    "project_convention": "project_convention",
    "repo convention": "project_convention",
    "repo_convention": "project_convention",
    "repository convention": "project_convention",
    "repository_convention": "project_convention",
    "security policy": "security",
    "security_policy": "security",
    "data policy": "data_governance",
    "data_policy": "data_governance",
    "test strategy": "testing_strategy",
    "test_strategy": "testing_strategy",
    "testing": "testing_strategy",
    "code style": "code_style",
    "code_style": "code_style",
    "coding style": "code_style",
    "coding_style": "code_style",
}


def _normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _normalize_fact_text(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def canonicalize_preference_key(raw_key: str) -> str:
    normalized = _normalize_key(raw_key)
    if not normalized:
        return "preference"
    alias = _PREFERENCE_KEY_ALIASES.get(normalized)
    if alias:
        return alias
    tokens = set(part for part in normalized.split("_") if part)
    if {"shoe", "brand"} <= tokens and tokens & {"preference", "preferred", "favorite", "like"}:
        return "favorite_shoe_brand"
    if "brand" in tokens and tokens & {"preference", "preferred", "favorite", "like"}:
        return "favorite_brand"
    if tokens & {"model", "llm"} and tokens & {"preference", "preferred", "favorite"}:
        return "preferred_ai_model"
    if "provider" in tokens and tokens & {"preference", "preferred", "favorite", "model", "ai"}:
        return "preferred_ai_provider"
    if "language" in tokens and tokens & {"preference", "preferred", "response", "reply", "writing"}:
        return "language_preference"
    if "tone" in tokens and tokens & {"preference", "reply", "response"}:
        return "response_tone_preference"
    if "framework" in tokens and tokens & {"preference", "preferred", "favorite"}:
        return "preferred_framework"
    if "library" in tokens and tokens & {"preference", "preferred", "favorite"}:
        return "preferred_library"
    if tokens & {"editor", "ide"} and tokens & {"preference", "preferred", "favorite"}:
        return "preferred_editor"
    if "package" in tokens and "manager" in tokens and tokens & {"preference", "preferred", "favorite"}:
        return "preferred_package_manager"
    if tokens & {"os", "platform"} and tokens & {"preference", "preferred", "favorite"}:
        return "preferred_operating_system"
    if "theme" in tokens and tokens & {"preference", "preferred", "favorite", "ui"}:
        return "ui_theme_preference"
    if "channel" in tokens and tokens & {"communication", "preference", "preferred", "favorite"}:
        return "preferred_communication_channel"
    return normalized


def canonicalize_knowledge_category(raw_category: str) -> str:
    normalized = _normalize_key(raw_category)
    if not normalized:
        return "general"
    return _KNOWLEDGE_CATEGORY_ALIASES.get(normalized, normalized)


def canonicalize_memory_extraction_result(result: Any) -> Dict[str, Any]:
    preference_decisions: List[Dict[str, Any]] = []
    canonical_preferences: List[Any] = []
    preference_groups: dict[Tuple[str, str], int] = {}

    for index, pref in enumerate(list(getattr(result, "preferences", []) or [])):
        requested_scope = str(getattr(pref, "scope", "") or "").strip() or "global"
        raw_key = str(getattr(pref, "key", "") or "").strip()
        canonical_key = canonicalize_preference_key(raw_key)
        pref.key = canonical_key
        group_key = (requested_scope, canonical_key)
        prior_index = preference_groups.get(group_key)
        if prior_index is None:
            preference_groups[group_key] = len(canonical_preferences)
            canonical_preferences.append(pref)
            preference_decisions.append(
                {
                    "index": index,
                    "scope": requested_scope,
                    "rawKey": raw_key,
                    "canonicalKey": canonical_key,
                    "decision": "kept",
                }
            )
            continue
        canonical_preferences[prior_index] = pref
        preference_decisions.append(
            {
                "index": index,
                "scope": requested_scope,
                "rawKey": raw_key,
                "canonicalKey": canonical_key,
                "decision": "overwrite_previous_alias",
            }
        )
    result.preferences = canonical_preferences

    knowledge_decisions: List[Dict[str, Any]] = []
    canonical_knowledge: List[Any] = []
    knowledge_groups: dict[Tuple[str, str, str], int] = {}

    for index, fact in enumerate(list(getattr(result, "knowledge", []) or [])):
        requested_scope = str(getattr(fact, "scope", "") or "").strip() or "global"
        raw_category = str(getattr(fact, "category", "") or "").strip()
        canonical_category = canonicalize_knowledge_category(raw_category)
        fact.category = canonical_category
        fact_text = _normalize_fact_text(getattr(fact, "fact", ""))
        group_key = (requested_scope, canonical_category, fact_text)
        prior_index = knowledge_groups.get(group_key)
        if prior_index is None:
            knowledge_groups[group_key] = len(canonical_knowledge)
            canonical_knowledge.append(fact)
            knowledge_decisions.append(
                {
                    "index": index,
                    "scope": requested_scope,
                    "rawCategory": raw_category,
                    "canonicalCategory": canonical_category,
                    "decision": "kept",
                }
            )
            continue
        current = canonical_knowledge[prior_index]
        current_score = float(getattr(current, "confidence", 0.0) or 0.0) + (int(getattr(current, "importance", 0) or 0) / 100.0)
        next_score = float(getattr(fact, "confidence", 0.0) or 0.0) + (int(getattr(fact, "importance", 0) or 0) / 100.0)
        if next_score >= current_score:
            canonical_knowledge[prior_index] = fact
            decision = "overwrite_duplicate_fact"
        else:
            decision = "drop_duplicate_fact"
        knowledge_decisions.append(
            {
                "index": index,
                "scope": requested_scope,
                "rawCategory": raw_category,
                "canonicalCategory": canonical_category,
                "decision": decision,
            }
        )
    result.knowledge = canonical_knowledge

    preference_canonicalization_count = sum(
        1
        for item in preference_decisions
        if str(item.get("rawKey") or "").strip() != str(item.get("canonicalKey") or "").strip()
    )
    preference_merge_count = sum(
        1 for item in preference_decisions if str(item.get("decision") or "").strip() == "overwrite_previous_alias"
    )
    knowledge_canonicalization_count = sum(
        1
        for item in knowledge_decisions
        if str(item.get("rawCategory") or "").strip() != str(item.get("canonicalCategory") or "").strip()
    )
    knowledge_merge_count = sum(
        1
        for item in knowledge_decisions
        if str(item.get("decision") or "").strip() in {"overwrite_duplicate_fact", "drop_duplicate_fact"}
    )

    return {
        "preferences": preference_decisions,
        "knowledge": knowledge_decisions,
        "preferenceCount": len(canonical_preferences),
        "knowledgeCount": len(canonical_knowledge),
        "preferenceCanonicalizationCount": preference_canonicalization_count,
        "preferenceMergeCount": preference_merge_count,
        "knowledgeCanonicalizationCount": knowledge_canonicalization_count,
        "knowledgeMergeCount": knowledge_merge_count,
    }
