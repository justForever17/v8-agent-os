from __future__ import annotations

from typing import Any, Dict, Iterable, List

from core.model_eligibility import evaluate_model_eligibility, model_kind


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        number = int(float(value))
        return number if number > 0 else None
    except Exception:
        return None


def diagnose_model_role(model: Dict[str, Any], *, role: str | None = None) -> Dict[str, Any]:
    """Return role suitability diagnostics without mutating model config."""
    role_name = str(role or "").strip() or "unassigned"
    kind = model_kind(model)
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

    eligibility = evaluate_model_eligibility(model, role=role_name)
    issues.extend(dict(item) for item in eligibility.get("reasons") or [])
    warnings.extend(dict(item) for item in eligibility.get("warnings") or [])
    if role_name in {"supervisor", "subagent", "summary"} and not caps.get("streaming"):
        warnings.append(
            {
                "code": "streaming_not_declared",
                "message": "Streaming support is not declared for an interactive text role.",
            }
        )
    return {
        "role": role_name,
        "modelKind": kind,
        "ok": bool(eligibility.get("selectable")),
        "blocking": bool(eligibility.get("blocking")),
        "issues": issues,
        "warnings": warnings,
        "effectiveInputLimit": context_window,
        "notes": [],
        "eligibility": eligibility,
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
