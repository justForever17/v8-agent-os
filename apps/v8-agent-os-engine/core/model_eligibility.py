from __future__ import annotations

from typing import Any, Mapping


MIN_TEXT_CONTEXT_WINDOW_TOKENS = 262_144

_MEDIA_TYPES = {
    "MEDIA",
    "IMAGE",
    "VIDEO",
    "AUDIO",
    "VOICE",
    "MUSIC",
    "WORKFLOW",
    "MODEL3D",
}
_RETRIEVAL_TYPES = {"EMBEDDING", "RERANK", "RERANKER", "VECTOR"}
_RETRIEVAL_CLASSES = {"embedding", "rerank", "reranker"}


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def model_kind(model: Mapping[str, Any]) -> str:
    model_type = str(model.get("type") or "TEXT").strip().upper()
    capability_class = str(model.get("capabilityClass") or "").strip().lower()
    capabilities = dict(model.get("capabilities") or {})
    if model_type in _MEDIA_TYPES or capability_class == "media_generation":
        return "media"
    if model_type in _RETRIEVAL_TYPES or capability_class in _RETRIEVAL_CLASSES:
        if model_type in {"RERANK", "RERANKER"} or capability_class in {"rerank", "reranker"} or capabilities.get("rerank"):
            return "rerank"
        return "embedding"
    return "text_generation"


def model_category(model: Mapping[str, Any]) -> str:
    """Return a compact human/Agent-facing inventory category."""

    kind = model_kind(model)
    if kind != "text_generation":
        return kind
    model_type = str(model.get("type") or "TEXT").strip().upper()
    capability_class = str(model.get("capabilityClass") or "").strip().lower()
    capabilities = dict(model.get("capabilities") or {})
    if (
        model_type in {"MULTIMODAL", "VISION"}
        or capability_class == "vision_multimodal"
        or bool(capabilities.get("vision") or capabilities.get("multimodal"))
    ):
        return "vision"
    return "text"


def evaluate_model_eligibility(
    model: Mapping[str, Any],
    *,
    role: str | None = None,
    minimum_context_window: int = MIN_TEXT_CONTEXT_WINDOW_TOKENS,
) -> dict[str, Any]:
    """Return the single model-selection truth used by Doctor, runtime and Admin.

    This service only validates declared facts and runtime health hints. It does
    not mutate configuration, send credentials or perform network I/O.
    """

    kind = model_kind(model)
    context_window = _positive_int(model.get("contextWindow"))
    max_tokens = _positive_int(model.get("maxTokens"))
    enabled = bool(model.get("isEnabled", True))
    runtime_ready = bool(model.get("runtimeReady", True))
    health = str(model.get("healthStatus") or model.get("health") or "").strip().lower()
    provenance = dict(model.get("factProvenance") or {})
    reasons: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_facts: list[str] = []

    if not enabled:
        reasons.append({"code": "model_disabled", "message": "Model is disabled."})
    if not runtime_ready:
        reasons.append({"code": "runtime_adapter_unavailable", "message": "The configured runtime adapter is unavailable."})

    if kind in {"embedding", "rerank"}:
        if not context_window:
            required_facts.append("contextWindow")
            reasons.append(
                {
                    "code": "missing_input_window",
                    "message": "Retrieval models require a confirmed maximum input window.",
                }
            )
    elif kind == "text_generation":
        if not context_window:
            required_facts.append("contextWindow")
            reasons.append(
                {
                    "code": "missing_context_window",
                    "message": "Text and vision models require a confirmed context window.",
                }
            )
        elif context_window < int(minimum_context_window):
            reasons.append(
                {
                    "code": "below_min_context_window",
                    "message": f"Context window {context_window} is below the required {minimum_context_window} tokens.",
                    "configured": context_window,
                    "minimum": int(minimum_context_window),
                }
            )
        if not max_tokens:
            required_facts.append("maxTokens")
            reasons.append(
                {
                    "code": "missing_max_output_tokens",
                    "message": "Text and vision models require a confirmed maximum output token value.",
                }
            )

    governed_fact_keys = (
        ["contextWindow"]
        if kind in {"embedding", "rerank"}
        else ["contextWindow", "maxTokens"]
        if kind == "text_generation"
        else []
    )
    unverified_facts = [
        key
        for key in governed_fact_keys
        if _positive_int(model.get(key))
        and str(dict(provenance.get(key) or {}).get("confidence") or "").strip().lower()
        not in {"authoritative", "reviewed"}
    ]
    if unverified_facts:
        warnings.append(
            {
                "code": "model_facts_unverified",
                "message": "Configured model limits are usable but their source has not been reviewed.",
                "facts": unverified_facts,
            }
        )

    if health in {"offline", "blocked", "circuit_open", "unhealthy"}:
        reasons.append(
            {
                "code": "recent_invocation_failures",
                "message": "Recent calls failed too often; verify the provider before assigning this model.",
            }
        )
    elif health in {"degraded", "warning"}:
        warnings.append(
            {
                "code": "provider_degraded",
                "message": "The provider is responding with degraded health.",
            }
        )

    if reasons:
        reason_codes = {str(item.get("code") or "") for item in reasons}
        if required_facts:
            status = "needs_facts"
            short_label = "需补全模型参数"
        elif "model_disabled" in reason_codes:
            status = "disabled"
            short_label = "已停用"
        elif "recent_invocation_failures" in reason_codes:
            status = "unhealthy"
            short_label = "近期调用异常"
        else:
            status = "unavailable"
            short_label = "当前不可用"
    else:
        status = "ready"
        warning_codes = {str(item.get("code") or "") for item in warnings}
        if "provider_degraded" in warning_codes:
            short_label = "可用 · 服务波动"
        elif "model_facts_unverified" in warning_codes:
            short_label = "可用 · 参数待复核"
        else:
            short_label = "可用"

    return {
        "status": status,
        "selectable": not reasons,
        "blocking": bool(reasons),
        "shortLabel": short_label,
        "modelKind": kind,
        "role": str(role or "").strip() or None,
        "requiredFacts": required_facts,
        "reasons": reasons,
        "warnings": warnings,
        "contextWindow": context_window,
        "maxTokens": max_tokens,
        "minimumContextWindow": int(minimum_context_window) if kind == "text_generation" else None,
        "factProvenance": provenance,
    }


__all__ = [
    "MIN_TEXT_CONTEXT_WINDOW_TOKENS",
    "evaluate_model_eligibility",
    "model_category",
    "model_kind",
]
