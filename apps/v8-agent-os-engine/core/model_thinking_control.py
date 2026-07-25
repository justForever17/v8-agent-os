from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping


_THINKING_PROFILE_PATH = Path(__file__).resolve().parent / "model_catalog" / "model_thinking_profiles.json"
_KNOWN_REASONING_LEVELS = ("auto", "none", "minimal", "low", "medium", "high", "xhigh", "max")
_REASONING_EFFORT_EXCLUDED_CLASSES = {"embedding", "reranker", "rerank", "media_generation"}
_ANTHROPIC_THINKING_BUDGET_BY_LEVEL = {
    "low": 4096,
    "medium": 8192,
    "high": 16000,
}
_GEMINI_THINKING_BUDGET_BY_LEVEL = {
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_provider_id(provider_id: Any) -> str:
    return str(provider_id or "").strip().lower()


def _normalize_model_id(model_id: Any) -> str:
    return str(model_id or "").strip()


def _capability_enabled(capabilities: Any, key: str) -> bool:
    wanted = str(key or "").strip()
    if isinstance(capabilities, Mapping):
        return bool(capabilities.get(wanted))
    if isinstance(capabilities, (list, tuple, set)):
        return wanted.lower() in {str(item).strip().lower() for item in capabilities}
    return False


def _model_supports_reasoning(model_record: Mapping[str, Any] | None) -> bool:
    record = _as_dict(model_record)
    capabilities = record.get("capabilities")
    tags = {str(item).strip().lower() for item in record.get("capabilityTags") or []}
    raw_type = str(record.get("capabilityClass") or record.get("type") or "").lower()
    return bool(_capability_enabled(capabilities, "reasoning") or "reasoning" in tags or "reasoning" in raw_type)


def normalize_reasoning_effort(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "": "auto",
        "default": "auto",
        "off": "none",
        "balanced": "medium",
        "mid": "medium",
        "normal": "medium",
        "deep": "high",
        "strong": "high",
        "maximum": "max",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _KNOWN_REASONING_LEVELS else "auto"


def resolve_session_reasoning_effort_override(session_record: Mapping[str, Any] | None) -> str:
    """Return the durable per-session override, or ``auto`` to inherit the model default."""

    record = _as_dict(session_record)
    metadata = record.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    metadata = _as_dict(metadata)
    return normalize_reasoning_effort(metadata.get("supervisorReasoningEffortOverride"))


def _excluded_from_reasoning_effort(model_record: Mapping[str, Any] | None) -> bool:
    record = _as_dict(model_record)
    raw_type = str(record.get("type") or "").strip().lower()
    capability_class = str(record.get("capabilityClass") or record.get("capability_class") or "").strip().lower()
    capabilities = record.get("capabilities")
    return (
        capability_class in _REASONING_EFFORT_EXCLUDED_CLASSES
        or raw_type in _REASONING_EFFORT_EXCLUDED_CLASSES
        or bool(
            _capability_enabled(capabilities, "embedding")
            or _capability_enabled(capabilities, "rerank")
            or _capability_enabled(capabilities, "mediaGeneration")
        )
    )


@lru_cache(maxsize=1)
def _thinking_profiles() -> tuple[Dict[str, Any], ...]:
    try:
        payload = json.loads(_THINKING_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    profiles = payload.get("profiles") if isinstance(payload, Mapping) else []
    return tuple(dict(item) for item in profiles or [] if isinstance(item, Mapping))


def _matches_any_pattern(value: str, patterns: Any) -> bool:
    import re

    normalized = str(value or "").strip().lower().replace("_", "-")
    candidates = [str(item).strip() for item in patterns or [] if str(item).strip()]
    if not candidates:
        return True
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) is not None for pattern in candidates)


def _matching_profiles(*, provider_id: str, model_id: str) -> list[Dict[str, Any]]:
    return [
        profile
        for profile in _thinking_profiles()
        if _matches_any_pattern(model_id, profile.get("modelPatterns"))
        and _matches_any_pattern(provider_id, profile.get("providerPatterns"))
    ]


def _model_wire_protocol(meta: Mapping[str, Any], model_record: Mapping[str, Any]) -> str:
    endpoint_binding = _as_dict(model_record.get("endpointBinding") or model_record.get("endpoint_binding"))
    return str(
        meta.get("wire_protocol")
        or meta.get("wireProtocol")
        or endpoint_binding.get("wireProtocol")
        or endpoint_binding.get("wire_protocol")
        or ""
    ).strip().lower()


def _transport_kind(
    *,
    meta: Mapping[str, Any],
    model_record: Mapping[str, Any],
    native_family: str,
) -> str:
    provider_id = _normalize_provider_id(meta.get("provider_id") or meta.get("providerId"))
    api_standard = _normalize_provider_id(meta.get("api_standard") or meta.get("apiStandard"))
    wire_protocol = _model_wire_protocol(meta, model_record)
    if provider_id == "openrouter":
        return "openrouter"
    if wire_protocol == "gemini.generate_content":
        return "gemini"
    if wire_protocol == "anthropic.messages":
        return "anthropic"
    if wire_protocol.startswith("openai."):
        return "openai"
    if api_standard in {"gemini", "google"}:
        return "gemini"
    if api_standard == "anthropic":
        return "anthropic"
    return native_family or "openai"


def _effort_request_style(*, transport: str, effort: Mapping[str, Any]) -> str:
    if transport == "openrouter":
        return "openrouter_reasoning_effort"
    configured = str(effort.get("nativeRequestStyle") or "").strip()
    if configured:
        return configured
    if transport == "anthropic":
        return "anthropic_effort"
    if transport == "gemini":
        return "gemini_thinking_budget" if _as_dict(effort.get("budgetByLevel")) else "gemini_thinking_level"
    return "openai_reasoning_effort"


def _no_think_request_style(*, transport: str, profile: Mapping[str, Any]) -> str:
    no_think = _as_dict(profile.get("noThink"))
    if transport == "openrouter":
        return "openrouter_reasoning_effort_none"
    configured = str(no_think.get("nativeRequestStyle") or "").strip()
    if configured:
        return configured
    if transport == "gemini":
        return "gemini_thinking_budget_zero"
    if transport == "anthropic":
        return "anthropic_thinking_disabled"
    return "openai_reasoning_effort_none"


def resolve_thinking_control_for_metadata(
    metadata: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    meta = _as_dict(metadata)
    provider_id = _normalize_provider_id(meta.get("provider_id") or meta.get("providerId"))
    model_id = _normalize_model_id(meta.get("model_id") or meta.get("modelId"))
    provider_record = _as_dict(meta.get("provider_record") or meta.get("providerRecord"))
    model_record = _as_dict(meta.get("model_record") or meta.get("modelRecord"))
    explicit = _as_dict(model_record.get("thinkingControl") or model_record.get("thinking_control"))
    matched_profile = next(
        (
            profile
            for profile in _matching_profiles(provider_id=provider_id, model_id=model_id)
            if "noThink" in profile
        ),
        {},
    )
    no_think_profile = _as_dict(matched_profile.get("noThink"))
    native_family = str(matched_profile.get("nativeFamily") or "").strip()
    transport = _transport_kind(meta=meta, model_record=model_record, native_family=native_family)
    profile_supports = no_think_profile.get("supported") is True
    request_style = str(explicit.get("requestStyle") or "").strip()
    if not request_style and profile_supports:
        request_style = _no_think_request_style(transport=transport, profile=matched_profile)

    supports = bool(explicit.get("supportsNoThink")) or profile_supports
    disabled = bool(explicit.get("disabled") or explicit.get("noThinkDisabled") or explicit.get("thinkingDisabled"))
    if not supports:
        return {}

    return {
        "supportsNoThink": True,
        "disabled": disabled,
        "requestStyle": request_style,
        "source": explicit.get("source") or "model_thinking_profile",
        "defaultDisabled": bool(explicit.get("defaultDisabled", False)),
        "profileId": str(matched_profile.get("id") or ""),
        "sourceRefs": list(matched_profile.get("sourceRefs") or []),
        "wireProtocol": _model_wire_protocol(meta, model_record),
    }


def resolve_reasoning_effort_control_for_metadata(
    metadata: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    meta = _as_dict(metadata)
    provider_id = _normalize_provider_id(meta.get("provider_id") or meta.get("providerId"))
    model_id = _normalize_model_id(meta.get("model_id") or meta.get("modelId"))
    model_record = _as_dict(meta.get("model_record") or meta.get("modelRecord"))
    if meta.get("capabilities") and not model_record.get("capabilities"):
        model_record["capabilities"] = meta.get("capabilities")
    if meta.get("capability_class") and not model_record.get("capabilityClass"):
        model_record["capabilityClass"] = meta.get("capability_class")
    if meta.get("reasoning_surface") and not model_record.get("reasoningSurface"):
        model_record["reasoningSurface"] = meta.get("reasoning_surface")
    explicit = _as_dict(model_record.get("reasoningEffortControl") or model_record.get("reasoning_effort_control"))
    matched_profile = next(
        (
            profile
            for profile in _matching_profiles(provider_id=provider_id, model_id=model_id)
            if _as_dict(profile.get("effort"))
        ),
        {},
    )
    effort_profile = _as_dict(matched_profile.get("effort"))
    if _excluded_from_reasoning_effort(model_record):
        return {}
    supports = bool(explicit.get("supportsReasoningEffort")) or bool(effort_profile)
    if not supports:
        return {}
    if not effort_profile and not _model_supports_reasoning(model_record):
        return {}

    native_family = str(matched_profile.get("nativeFamily") or "").strip()
    transport = _transport_kind(meta=meta, model_record=model_record, native_family=native_family)
    request_style = str(explicit.get("requestStyle") or "").strip() or _effort_request_style(
        transport=transport,
        effort=effort_profile,
    )
    declared_levels = explicit.get("levels") if isinstance(explicit.get("levels"), list) else effort_profile.get("levels")
    levels = [
        level
        for level in (normalize_reasoning_effort(item) for item in declared_levels or [])
        if level != "auto"
    ]
    levels = list(dict.fromkeys(levels))
    if not levels:
        return {}
    selected_level = normalize_reasoning_effort(explicit.get("selectedLevel") or explicit.get("level") or "auto")
    if selected_level != "auto" and selected_level not in levels:
        selected_level = "auto"
    default_level = normalize_reasoning_effort(explicit.get("defaultLevel") or effort_profile.get("defaultLevel") or "auto")
    if default_level != "auto" and default_level not in levels:
        default_level = "auto"

    return {
        "supportsReasoningEffort": True,
        "requestStyle": request_style,
        "levels": ["auto", *levels],
        "defaultLevel": default_level,
        "selectedLevel": selected_level,
        "mandatory": bool(explicit.get("mandatory", effort_profile.get("mandatory", False))),
        "budgetByLevel": _as_dict(explicit.get("budgetByLevel") or effort_profile.get("budgetByLevel")),
        "source": explicit.get("source") or "model_thinking_profile",
        "profileId": str(matched_profile.get("id") or ""),
        "sourceRefs": list(matched_profile.get("sourceRefs") or []),
        "wireProtocol": _model_wire_protocol(meta, model_record),
        "providerId": provider_id,
        "modelId": model_id,
    }


def no_think_request_patch(thinking_control: Mapping[str, Any] | None) -> Dict[str, Any]:
    control = _as_dict(thinking_control)
    if not control.get("supportsNoThink") or not control.get("disabled"):
        return {}

    style = str(control.get("requestStyle") or "").strip()
    if style == "dashscope_enable_thinking_false":
        return {"extra_body": {"enable_thinking": False}}
    if style == "openai_thinking_disabled":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    if style == "anthropic_thinking_disabled":
        return {"thinking": {"type": "disabled"}}
    if style == "gemini_thinking_budget_zero":
        return {"thinking_budget": 0}
    if style == "openai_reasoning_effort_none":
        if str(control.get("wireProtocol") or "").strip() == "openai.chat_completions":
            return {"reasoning_effort": "none"}
        return {"reasoning": {"effort": "none"}}
    if style == "openrouter_reasoning_effort_none":
        return {"reasoning": {"effort": "none"}}
    return {}


def reasoning_effort_request_patch(
    reasoning_effort_control: Mapping[str, Any] | None,
    requested_effort: Any,
) -> Dict[str, Any]:
    control = _as_dict(reasoning_effort_control)
    if not control.get("supportsReasoningEffort"):
        return {}
    level = normalize_reasoning_effort(requested_effort)
    if level == "auto":
        return {}
    declared_levels = control.get("levels") or ("low", "medium", "high")
    supported_levels = {
        normalize_reasoning_effort(item)
        for item in declared_levels
        if normalize_reasoning_effort(item) != "auto"
    }
    if level not in supported_levels:
        return {}

    style = str(control.get("requestStyle") or "").strip()
    if style == "openai_reasoning_effort":
        if str(control.get("wireProtocol") or "").strip() == "openai.chat_completions":
            return {"reasoning_effort": level}
        return {"reasoning": {"effort": level}}
    if style == "openrouter_reasoning_effort":
        return {"reasoning": {"effort": level}}
    if style == "anthropic_effort":
        return {"effort": level}
    if style == "anthropic_thinking_budget":
        budget = _as_dict(control.get("budgetByLevel")).get(level) or _ANTHROPIC_THINKING_BUDGET_BY_LEVEL.get(level)
        return {"thinking": {"type": "enabled", "budget_tokens": int(budget)}} if budget else {}
    if style == "gemini_thinking_level":
        return {"thinking_level": level}
    if style == "gemini_thinking_budget":
        budget = _as_dict(control.get("budgetByLevel")).get(level) or _GEMINI_THINKING_BUDGET_BY_LEVEL.get(level)
        return {"thinking_budget": int(budget)} if budget is not None else {}
    return {}


def reasoning_summary_request_patch(metadata: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Request a provider-visible reasoning summary without exposing hidden tokens.

    This is deliberately narrower than the effort control plane.  OpenAI
    Responses models advertise ``reasoning.summary`` in their reasoning
    surface; Chat Completions, Anthropic and Gemini have different native
    contracts and must not receive this field by inference.
    """

    meta = _as_dict(metadata)
    model_record = _as_dict(meta.get("model_record") or meta.get("modelRecord"))
    surface = _as_dict(
        meta.get("reasoning_surface")
        or meta.get("reasoningSurface")
        or model_record.get("reasoningSurface")
        or model_record.get("reasoning_surface")
    )
    wire_protocol = _model_wire_protocol(meta, model_record)
    response_fields = {str(item).strip() for item in list(surface.get("responseFields") or surface.get("response_fields") or [])}
    request_style = str(surface.get("requestStyle") or surface.get("request_style") or "").strip()
    if (
        wire_protocol != "openai.responses"
        or str(surface.get("mode") or "").strip() != "reasoning_summary"
        or request_style not in {"", "openai_reasoning"}
        or "reasoning.summary" not in response_fields
    ):
        return {}
    return {
        "reasoning": {"summary": "auto"},
        # ``store=false`` is used by V8OS; encrypted reasoning items are the
        # provider-supported replay handle for the next turn.
        "include": ["reasoning.encrypted_content"],
    }


def provider_reasoning_transport_patch(metadata: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Enable a provider's documented reasoning transport when explicitly bound.

    MiniMax-M3 otherwise embeds ``<think>`` in normal Chat Completions content.
    Its documented ``reasoning_split`` option keeps visible text/JSON parseable
    while preserving ``reasoning_details`` for interleaved-thinking replay.
    """

    meta = _as_dict(metadata)
    model_record = _as_dict(meta.get("model_record") or meta.get("modelRecord"))
    surface = _as_dict(
        meta.get("reasoning_surface")
        or meta.get("reasoningSurface")
        or model_record.get("reasoningSurface")
        or model_record.get("reasoning_surface")
    )
    wire_protocol = _model_wire_protocol(meta, model_record)
    api_standard = str(meta.get("api_standard") or meta.get("apiStandard") or "").strip().lower()
    if (
        not (
            wire_protocol == "openai.chat_completions"
            or (not wire_protocol and api_standard == "openai")
        )
        or str(surface.get("trust") or "").strip() != "official"
        or str(surface.get("requestStyle") or surface.get("request_style") or "").strip()
        != "minimax_interleaved_thinking"
        or "reasoning_details"
        not in {
            str(item).strip()
            for item in list(surface.get("responseFields") or surface.get("response_fields") or [])
        }
    ):
        return {}
    return {"extra_body": {"reasoning_split": True}}


def ensure_anthropic_thinking_budget_headroom(kwargs: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Keep Anthropic output tokens above its explicit thinking budget."""

    normalized = _as_dict(kwargs)
    thinking = _as_dict(normalized.get("thinking"))
    try:
        budget_value = int(thinking.get("budget_tokens") or 0)
    except (TypeError, ValueError):
        budget_value = 0
    if budget_value <= 0:
        return normalized

    current_max = normalized.get("max_tokens_to_sample") or normalized.get("max_tokens")
    try:
        current_max_value = int(current_max or 0)
    except (TypeError, ValueError):
        current_max_value = 0
    if current_max_value <= budget_value:
        normalized["max_tokens_to_sample"] = budget_value + 1024
    return normalized


def merge_model_request_patch(kwargs: Dict[str, Any], patch: Mapping[str, Any] | None) -> Dict[str, Any]:
    merged = dict(kwargs)
    for key, value in _as_dict(patch).items():
        if key == "extra_body":
            existing_extra = _as_dict(merged.get("extra_body"))
            next_extra = deepcopy(existing_extra)
            for nested_key, nested_value in _as_dict(value).items():
                if isinstance(nested_value, Mapping) and isinstance(next_extra.get(nested_key), Mapping):
                    next_extra[nested_key] = {**_as_dict(next_extra.get(nested_key)), **_as_dict(nested_value)}
                else:
                    next_extra[nested_key] = nested_value
            merged["extra_body"] = next_extra
        elif key == "include" and isinstance(value, (list, tuple, set)):
            existing = merged.get(key) if isinstance(merged.get(key), (list, tuple, set)) else []
            merged[key] = list(dict.fromkeys([*list(existing), *list(value)]))
        elif isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**_as_dict(merged.get(key)), **_as_dict(value)}
        else:
            merged[key] = value
    return merged
