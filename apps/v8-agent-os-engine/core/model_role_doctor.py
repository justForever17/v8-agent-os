from __future__ import annotations

from typing import Any, Dict, Iterable, List


TEXT_CAPABILITY_CLASSES = {"chat_general", "chat_reasoning", "chat_tool_calling", "vision_multimodal"}
RETRIEVAL_CAPABILITY_CLASSES = {"embedding", "reranker"}
MEDIA_TYPES = {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}
LONG_CONTEXT_MIN_TOKENS = 262_144


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        number = int(float(value))
        return number if number > 0 else None
    except Exception:
        return None


def _model_kind(model: Dict[str, Any]) -> str:
    model_type = str(model.get("type") or "").upper()
    capability_class = str(model.get("capabilityClass") or "").strip()
    capabilities = dict(model.get("capabilities") or {})
    if model_type in MEDIA_TYPES or capability_class == "media_generation":
        return "media"
    if model_type == "EMBEDDING" or capability_class == "embedding" or capabilities.get("embedding"):
        return "embedding"
    if model_type in {"RERANK", "RERANKER"} or capability_class == "reranker" or capabilities.get("rerank"):
        return "rerank"
    return "text_generation"


def diagnose_model_role(model: Dict[str, Any], *, role: str | None = None) -> Dict[str, Any]:
    """Return role suitability diagnostics without mutating model config."""
    role_name = str(role or "").strip() or "unassigned"
    kind = _model_kind(model)
    context_window = _safe_int(model.get("contextWindow"))
    max_tokens = _safe_int(model.get("maxTokens"))
    observed_input = _safe_int(model.get("observedInputTokenLimit"))
    observed_rerank_query = _safe_int(model.get("observedRerankQueryTokenLimit"))
    caps = dict(model.get("capabilities") or {})
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if kind == "media":
        return {
            "role": role_name,
            "modelKind": kind,
            "ok": True,
            "blocking": False,
            "issues": [],
            "warnings": [],
            "effectiveInputLimit": None,
            "notes": ["media models are excluded from text context-window governance"],
        }

    if kind in {"embedding", "rerank"}:
        if not context_window:
            issues.append(
                {
                    "code": "missing_input_window",
                    "message": "Retrieval model is missing contextWindow; it is used as maximum input tokens.",
                }
            )
        if kind == "embedding" and observed_input and context_window and observed_input < context_window:
            warnings.append(
                {
                    "code": "observed_input_limit_lower_than_config",
                    "message": f"Provider observed input limit {observed_input} is lower than configured {context_window}.",
                    "observedLimit": observed_input,
                }
            )
        if kind == "rerank" and observed_rerank_query:
            warnings.append(
                {
                    "code": "observed_rerank_query_limit",
                    "message": f"Provider observed rerank query limit is {observed_rerank_query}.",
                    "observedLimit": observed_rerank_query,
                }
            )
        return {
            "role": role_name,
            "modelKind": kind,
            "ok": not issues,
            "blocking": bool(issues),
            "issues": issues,
            "warnings": warnings,
            "effectiveInputLimit": min([value for value in (context_window, observed_input) if value], default=context_window),
            "notes": ["maxTokens is not required for embedding/rerank models"],
        }

    if not context_window:
        issues.append(
            {
                "code": "missing_context_window",
                "message": "Text generation model is missing contextWindow.",
            }
        )
    elif context_window < LONG_CONTEXT_MIN_TOKENS:
        issues.append(
            {
                "code": "below_min_context_window",
                "message": f"Text generation runtime roles require at least {LONG_CONTEXT_MIN_TOKENS} context tokens.",
                "configured": context_window,
            }
        )
    if not max_tokens:
        warnings.append(
            {
                "code": "missing_max_output_tokens",
                "message": "maxTokens is missing; output budget may be conservative.",
            }
        )
    if role_name in {"supervisor", "subagent", "summary", "plugin_host"} and not caps.get("streaming"):
        warnings.append(
            {
                "code": "streaming_not_declared",
                "message": "Streaming support is not declared for an interactive text role.",
            }
        )
    return {
        "role": role_name,
        "modelKind": kind,
        "ok": not issues,
        "blocking": bool(issues),
        "issues": issues,
        "warnings": warnings,
        "effectiveInputLimit": context_window,
        "notes": [],
    }


def diagnose_models(models: Iterable[Dict[str, Any]], *, role: str | None = None) -> List[Dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for model in models:
        diagnostics.append(
            {
                "modelRef": model.get("modelRef") or model.get("id"),
                "providerId": model.get("providerId"),
                "modelId": model.get("modelId"),
                "providerName": model.get("providerName"),
                "type": model.get("type"),
                "capabilityClass": model.get("capabilityClass"),
                "contextWindow": model.get("contextWindow"),
                "maxTokens": model.get("maxTokens"),
                "diagnostic": diagnose_model_role(model, role=role),
            }
        )
    return diagnostics
