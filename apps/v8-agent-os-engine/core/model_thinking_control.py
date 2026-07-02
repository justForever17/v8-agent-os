from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Mapping


_OPENAI_REASONING_PROVIDERS = {"openai"}
_OPENROUTER_PROVIDERS = {"openrouter"}
_ANTHROPIC_PROVIDERS = {"anthropic"}
_GEMINI_PROVIDERS = {"gemini", "gemini-api", "google"}
_DEEPSEEK_PROVIDERS = {"deepseek"}
_DASHSCOPE_PROVIDERS = {"dashscope", "qwen"}
_GLM_PROVIDERS = {"zhipu", "zai-coding", "bigmodel"}
_MINIMAX_PROVIDERS = {"minimax", "minimax-cn"}
_XIAOMI_MIMO_PROVIDERS = {"xiaomi-mimo", "xiaomi-mimo-tokenplan"}
_VOLCENGINE_ARK_PROVIDERS = {"volcengine-ark", "volcengine-coding"}

_DEEPSEEK_NO_THINK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
_MINIMAX_NO_THINK_MODELS = {"minimax-m3"}
_XIAOMI_MIMO_NO_THINK_MODELS = {"mimo-v2.5", "mimo-v2.5-flash", "mimo-v2.5-pro"}
_REASONING_EFFORT_LEVELS = ("auto", "low", "medium", "high")
_REASONING_EFFORT_PROVIDER_STYLES = {
    "openai": "openai_reasoning_effort",
    "openrouter": "openrouter_reasoning_effort",
}
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
        "none": "auto",
        "off": "auto",
        "balanced": "medium",
        "mid": "medium",
        "normal": "medium",
        "deep": "high",
        "strong": "high",
        "max": "high",
        "maximum": "high",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _REASONING_EFFORT_LEVELS else "auto"


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


def _is_volcengine_ark_no_think_model(model_id: str) -> bool:
    return (
        model_id.startswith("doubao-seed-")
        or model_id.startswith("deepseek-v4-")
        or model_id.startswith("deepseek-v3-2")
        or model_id.startswith("glm-4-7")
    )


def _is_anthropic_effort_model(model_id: str) -> bool:
    model = _normalize_model_id(model_id).strip().lower().replace("_", "-")
    if any(name in model for name in {"fable-5", "mythos-5", "mythos-preview", "sonnet-5"}):
        return True
    if re.search(r"claude-(?:opus|sonnet)-4[.-]6\b", model):
        return True
    if re.search(r"claude-opus-4[.-](?:5|7|8)\b", model):
        return True
    return False


def _anthropic_reasoning_effort_request_style(model_id: str, model_record: Mapping[str, Any] | None) -> str:
    if _is_anthropic_effort_model(model_id):
        return "anthropic_effort"
    surface = _as_dict(_as_dict(model_record).get("reasoningSurface") or _as_dict(model_record).get("reasoning_surface"))
    if str(surface.get("requestStyle") or surface.get("request_style") or "").strip() == "anthropic_thinking":
        return "anthropic_thinking_budget"
    return ""


def _gemini_reasoning_effort_request_style(model_id: str) -> str:
    model = _normalize_model_id(model_id).strip().lower().replace("_", "-")
    if model.startswith("gemini-2.5-") or model.startswith("gemini-2-5-"):
        return "gemini_thinking_budget"
    return "gemini_thinking_level"


def _request_style_for_model(
    *,
    provider_id: str,
    model_id: str,
    model_record: Mapping[str, Any] | None = None,
) -> str:
    provider = _normalize_provider_id(provider_id)
    model = _normalize_model_id(model_id)
    model_lower = model.lower()

    if provider in _DASHSCOPE_PROVIDERS and model_lower.startswith(("qwen", "qwq")):
        return "dashscope_enable_thinking_false"
    if provider in _GLM_PROVIDERS and model_lower.startswith("glm-"):
        return "openai_thinking_disabled"
    if provider in _DEEPSEEK_PROVIDERS and model_lower in _DEEPSEEK_NO_THINK_MODELS:
        return "openai_thinking_disabled"
    if provider in _MINIMAX_PROVIDERS and model_lower in _MINIMAX_NO_THINK_MODELS:
        return "openai_thinking_disabled"
    if provider in _XIAOMI_MIMO_PROVIDERS and model_lower in _XIAOMI_MIMO_NO_THINK_MODELS:
        return "openai_thinking_disabled"
    if provider in _VOLCENGINE_ARK_PROVIDERS and _is_volcengine_ark_no_think_model(model_lower):
        return "openai_thinking_disabled"
    if provider in _OPENAI_REASONING_PROVIDERS and _model_supports_reasoning(model_record):
        return "openai_reasoning_effort_none"
    if provider in _OPENROUTER_PROVIDERS and _model_supports_reasoning(model_record):
        return "openrouter_reasoning_effort_none"
    return ""


def resolve_thinking_control_for_metadata(
    metadata: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    meta = _as_dict(metadata)
    provider_id = _normalize_provider_id(meta.get("provider_id") or meta.get("providerId"))
    model_id = _normalize_model_id(meta.get("model_id") or meta.get("modelId"))
    provider_record = _as_dict(meta.get("provider_record") or meta.get("providerRecord"))
    model_record = _as_dict(meta.get("model_record") or meta.get("modelRecord"))
    explicit = _as_dict(model_record.get("thinkingControl") or model_record.get("thinking_control"))
    request_style = str(explicit.get("requestStyle") or "").strip() or _request_style_for_model(
        provider_id=provider_id,
        model_id=model_id,
        model_record=model_record,
    )

    supports = bool(explicit.get("supportsNoThink")) or bool(request_style)
    disabled = bool(explicit.get("disabled") or explicit.get("noThinkDisabled") or explicit.get("thinkingDisabled"))
    if not supports:
        return {}

    return {
        "supportsNoThink": True,
        "disabled": disabled,
        "requestStyle": request_style,
        "source": explicit.get("source") or "provider_model_match",
        "defaultDisabled": bool(explicit.get("defaultDisabled", False)),
    }


def resolve_reasoning_effort_control_for_metadata(
    metadata: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    meta = _as_dict(metadata)
    provider_id = _normalize_provider_id(meta.get("provider_id") or meta.get("providerId"))
    api_standard = _normalize_provider_id(meta.get("api_standard") or meta.get("apiStandard"))
    model_id = _normalize_model_id(meta.get("model_id") or meta.get("modelId"))
    model_record = _as_dict(meta.get("model_record") or meta.get("modelRecord"))
    if meta.get("capabilities") and not model_record.get("capabilities"):
        model_record["capabilities"] = meta.get("capabilities")
    if meta.get("capability_class") and not model_record.get("capabilityClass"):
        model_record["capabilityClass"] = meta.get("capability_class")
    if meta.get("reasoning_surface") and not model_record.get("reasoningSurface"):
        model_record["reasoningSurface"] = meta.get("reasoning_surface")

    if provider_id in _ANTHROPIC_PROVIDERS or api_standard == "anthropic":
        request_style = _anthropic_reasoning_effort_request_style(model_id, model_record)
    elif provider_id in _GEMINI_PROVIDERS or api_standard in {"gemini", "google"}:
        request_style = _gemini_reasoning_effort_request_style(model_id)
    else:
        request_style = _REASONING_EFFORT_PROVIDER_STYLES.get(provider_id, "")
    if not request_style:
        return {}
    if _excluded_from_reasoning_effort(model_record):
        return {}
    if not _model_supports_reasoning(model_record):
        return {}

    return {
        "supportsReasoningEffort": True,
        "requestStyle": request_style,
        "levels": list(_REASONING_EFFORT_LEVELS),
        "defaultLevel": "auto",
        "source": "provider_model_match",
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
    if style in {"openai_reasoning_effort_none", "openrouter_reasoning_effort_none"}:
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

    style = str(control.get("requestStyle") or "").strip()
    if style in {"openai_reasoning_effort", "openrouter_reasoning_effort"}:
        return {"reasoning": {"effort": level}}
    if style == "anthropic_effort":
        return {"effort": level}
    if style == "anthropic_thinking_budget":
        return {"thinking": {"type": "enabled", "budget_tokens": _ANTHROPIC_THINKING_BUDGET_BY_LEVEL[level]}}
    if style == "gemini_thinking_level":
        return {"thinking_level": level}
    if style == "gemini_thinking_budget":
        return {"thinking_budget": _GEMINI_THINKING_BUDGET_BY_LEVEL[level]}
    return {}


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
        elif isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**_as_dict(merged.get(key)), **_as_dict(value)}
        else:
            merged[key] = value
    return merged
