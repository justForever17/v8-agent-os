from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from runtimes.computer_use.semantic_targets import is_input_target_key, is_result_target_key


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_tokens(values: Any) -> List[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    normalized: List[str] = []
    for item in values:
        token = _normalize_text(item)
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def is_search_selector_key(selector_key: Any) -> bool:
    return is_input_target_key(selector_key)


def is_result_selector_key(selector_key: Any) -> bool:
    return is_result_target_key(selector_key)


def infer_query_mode(query_text: Any, target_text: Any) -> str:
    query = str(query_text or "").strip()
    target = str(target_text or "").strip()
    if not query:
        return ""
    if not target:
        return "exact"
    query_key = _normalize_text(query)
    target_key = _normalize_text(target)
    if not query_key or not target_key:
        return "exact"
    if query_key == target_key:
        return "exact"
    if target_key.startswith(query_key):
        return "prefix"
    if query_key in target_key:
        return "contains"
    return "alias"


def result_region_from_point(point: Any) -> str | None:
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    try:
        y = float(point[1])
    except Exception:
        return None
    if not (0.0 <= y <= 1.0):
        return None
    if y <= 0.4:
        return "upper"
    if y <= 0.72:
        return "middle"
    return "lower"


def normalize_target_strategy(strategy: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(strategy, dict):
        return {}
    normalized: Dict[str, Any] = {}
    query_text = str(strategy.get("query_text") or strategy.get("queryText") or "").strip()
    if query_text:
        normalized["query_text"] = query_text
        query_mode = str(strategy.get("query_mode") or strategy.get("queryMode") or "").strip().lower()
        normalized["query_mode"] = query_mode or infer_query_mode(query_text, strategy.get("target_text"))
    elif strategy.get("query_mode") or strategy.get("queryMode"):
        query_mode = str(strategy.get("query_mode") or strategy.get("queryMode") or "").strip().lower()
        if query_mode:
            normalized["query_mode"] = query_mode
    preferred_region = str(
        strategy.get("preferred_result_region")
        or strategy.get("preferredResultRegion")
        or ""
    ).strip().lower()
    if preferred_region in {"upper", "middle", "lower"}:
        normalized["preferred_result_region"] = preferred_region
    preferred_section = str(
        strategy.get("preferred_result_section")
        or strategy.get("preferredResultSection")
        or ""
    ).strip().lower()
    if preferred_section:
        normalized["preferred_result_section"] = preferred_section
    preferred_hit_zone = str(
        strategy.get("preferred_hit_zone")
        or strategy.get("preferredHitZone")
        or ""
    ).strip().lower()
    if preferred_hit_zone:
        normalized["preferred_hit_zone"] = preferred_hit_zone
    activation_gesture = str(
        strategy.get("activation_gesture")
        or strategy.get("activationGesture")
        or ""
    ).strip().lower()
    if activation_gesture:
        normalized["activation_gesture"] = activation_gesture
    preferred_index = strategy.get("preferred_result_index")
    if preferred_index in (None, ""):
        preferred_index = strategy.get("preferredResultIndex")
    if preferred_index not in (None, ""):
        try:
            normalized["preferred_result_index"] = max(0, int(preferred_index))
        except Exception:
            pass
    forbidden_tokens = _normalize_tokens(
        strategy.get("forbidden_result_tokens") or strategy.get("forbiddenResultTokens")
    )
    if forbidden_tokens:
        normalized["forbidden_result_tokens"] = forbidden_tokens
    if "required_exact_match" in strategy or "requiredExactMatch" in strategy:
        normalized["required_exact_match"] = bool(
            strategy.get("required_exact_match")
            if "required_exact_match" in strategy
            else strategy.get("requiredExactMatch")
        )
    for source_key, target_key in (
        ("search_selector_key", "search_selector_key"),
        ("searchSelectorKey", "search_selector_key"),
        ("result_selector_key", "result_selector_key"),
        ("resultSelectorKey", "result_selector_key"),
        ("target_text", "target_text"),
        ("targetText", "target_text"),
    ):
        value = str(strategy.get(source_key) or "").strip()
        if value:
            normalized[target_key] = value
    return normalized


def merge_target_strategies(*strategies: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for item in strategies:
        normalized = normalize_target_strategy(item)
        if not normalized:
            continue
        for key in (
            "query_text",
            "query_mode",
            "preferred_result_region",
            "preferred_result_section",
            "preferred_hit_zone",
            "activation_gesture",
            "preferred_result_index",
            "search_selector_key",
            "result_selector_key",
            "target_text",
        ):
            if merged.get(key) in (None, "") and normalized.get(key) not in (None, ""):
                merged[key] = normalized.get(key)
        if normalized.get("required_exact_match"):
            merged["required_exact_match"] = True
        tokens = _normalize_tokens(
            list(merged.get("forbidden_result_tokens") or []) + list(normalized.get("forbidden_result_tokens") or [])
        )
        if tokens:
            merged["forbidden_result_tokens"] = tokens
    return merged


def apply_target_strategy(
    *,
    action_payload: Dict[str, Any],
    strategy: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any] | None]:
    normalized_strategy = normalize_target_strategy(strategy)
    if not normalized_strategy:
        return dict(action_payload), None
    selector_key = str(action_payload.get("selector_key") or "").strip().lower()
    patched = dict(action_payload)
    changes: Dict[str, Any] = {}
    target_text = str(patched.get("target_text") or normalized_strategy.get("target_text") or "").strip()
    current_text = str(patched.get("text") or "").strip()
    query_text = str(normalized_strategy.get("query_text") or "").strip()

    if is_search_selector_key(selector_key):
        if target_text and not patched.get("target_text"):
            patched["target_text"] = target_text
            changes["target_text"] = target_text
        if query_text and (not current_text or current_text == target_text):
            patched["text"] = query_text
            changes["text"] = query_text
        if normalized_strategy.get("query_mode") and patched.get("query_mode") in (None, ""):
            patched["query_mode"] = normalized_strategy.get("query_mode")
            changes["query_mode"] = normalized_strategy.get("query_mode")
        if normalized_strategy.get("search_selector_key") and patched.get("search_selector_key") in (None, ""):
            patched["search_selector_key"] = normalized_strategy.get("search_selector_key")
            changes["search_selector_key"] = normalized_strategy.get("search_selector_key")

    if is_result_selector_key(selector_key):
        if target_text and not patched.get("target_text"):
            patched["target_text"] = target_text
            changes["target_text"] = target_text
        if normalized_strategy.get("preferred_result_region") and patched.get("preferred_result_region") in (None, ""):
            patched["preferred_result_region"] = normalized_strategy.get("preferred_result_region")
            changes["preferred_result_region"] = normalized_strategy.get("preferred_result_region")
        if normalized_strategy.get("preferred_result_section") and patched.get("preferred_result_section") in (None, ""):
            patched["preferred_result_section"] = normalized_strategy.get("preferred_result_section")
            changes["preferred_result_section"] = normalized_strategy.get("preferred_result_section")
        if normalized_strategy.get("preferred_hit_zone") and patched.get("preferred_hit_zone") in (None, ""):
            patched["preferred_hit_zone"] = normalized_strategy.get("preferred_hit_zone")
            changes["preferred_hit_zone"] = normalized_strategy.get("preferred_hit_zone")
        if normalized_strategy.get("activation_gesture") and patched.get("activation_gesture") in (None, ""):
            patched["activation_gesture"] = normalized_strategy.get("activation_gesture")
            changes["activation_gesture"] = normalized_strategy.get("activation_gesture")
        if normalized_strategy.get("preferred_result_index") is not None and patched.get("preferred_result_index") in (None, ""):
            patched["preferred_result_index"] = normalized_strategy.get("preferred_result_index")
            changes["preferred_result_index"] = normalized_strategy.get("preferred_result_index")
        if normalized_strategy.get("required_exact_match") and patched.get("required_exact_match") is None:
            patched["required_exact_match"] = True
            changes["required_exact_match"] = True
        forbidden_tokens = list(normalized_strategy.get("forbidden_result_tokens") or [])
        if forbidden_tokens and not list(patched.get("forbidden_result_tokens") or []):
            patched["forbidden_result_tokens"] = forbidden_tokens
            changes["forbidden_result_tokens"] = forbidden_tokens
        if normalized_strategy.get("result_selector_key") and patched.get("result_selector_key") in (None, ""):
            patched["result_selector_key"] = normalized_strategy.get("result_selector_key")
            changes["result_selector_key"] = normalized_strategy.get("result_selector_key")

    if not changes:
        return dict(action_payload), None
    return patched, {"strategy": normalized_strategy, "changes": changes}
