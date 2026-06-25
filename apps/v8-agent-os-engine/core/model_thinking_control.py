from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping


_OPENAI_REASONING_PROVIDERS = {"openai"}
_OPENROUTER_PROVIDERS = {"openrouter"}
_DEEPSEEK_PROVIDERS = {"deepseek"}
_DASHSCOPE_PROVIDERS = {"dashscope", "qwen"}
_GLM_PROVIDERS = {"zhipu", "zai-coding", "bigmodel"}
_MINIMAX_PROVIDERS = {"minimax", "minimax-cn"}
_XIAOMI_MIMO_PROVIDERS = {"xiaomi-mimo", "xiaomi-mimo-tokenplan"}
_VOLCENGINE_ARK_PROVIDERS = {"volcengine-ark", "volcengine-coding"}

_DEEPSEEK_NO_THINK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
_MINIMAX_NO_THINK_MODELS = {"minimax-m3"}
_XIAOMI_MIMO_NO_THINK_MODELS = {"mimo-v2.5", "mimo-v2.5-flash", "mimo-v2.5-pro"}


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_provider_id(provider_id: Any) -> str:
    return str(provider_id or "").strip().lower()


def _normalize_model_id(model_id: Any) -> str:
    return str(model_id or "").strip()


def _model_supports_reasoning(model_record: Mapping[str, Any] | None) -> bool:
    record = _as_dict(model_record)
    capabilities = _as_dict(record.get("capabilities"))
    tags = {str(item).strip().lower() for item in record.get("capabilityTags") or []}
    raw_type = str(record.get("capabilityClass") or record.get("type") or "").lower()
    return bool(capabilities.get("reasoning") or "reasoning" in tags or "reasoning" in raw_type)


def _is_volcengine_ark_no_think_model(model_id: str) -> bool:
    return (
        model_id.startswith("doubao-seed-")
        or model_id.startswith("deepseek-v4-")
        or model_id.startswith("deepseek-v3-2")
        or model_id.startswith("glm-4-7")
    )


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
