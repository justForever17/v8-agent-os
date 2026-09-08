"""Resolve user budgets without promoting catalog estimates to model limits."""

from typing import Any, Mapping


def positive_token_count(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
        return parsed if parsed > 0 and float(value) == parsed else None
    except (TypeError, ValueError, OverflowError):
        return None


def output_token_mode(model: Mapping[str, Any]) -> str:
    explicit = model.get("outputTokenMode")
    if explicit in ("auto", "fixed"):
        return str(explicit)
    facts = model.get("factProvenance")
    provenance = facts.get("maxTokens") if isinstance(facts, Mapping) else None
    provenance = provenance if isinstance(provenance, Mapping) else {}
    # Only this exact, documented legacy estimate is known to be synthetic.
    # A changed value with stale provenance is not safe to reclassify.
    if (provenance.get("source") == "v8_conservative_2026_default"
            and provenance.get("confidence") == "estimated" and model.get("maxTokens") == 4096):
        return "auto"
    return "fixed" if positive_token_count(model.get("maxTokens")) else "auto"


def normalize_output_token_policy(model: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(model)
    if str(model.get("type") or "TEXT").upper() not in {"TEXT", "MULTIMODAL", "VISION", "CHAT"}:
        return normalized
    if output_token_mode(model) == "auto":
        normalized["outputTokenMode"] = "auto"
    # Retain the old numeric value for old readers / rollback, but new runtime
    # consumers must resolve its mode rather than reading maxTokens directly.
    return normalized


def resolve_output_token_budget(meta: Mapping[str, Any], requested: Any = None, *, requires_value: bool = False) -> dict[str, Any]:
    record = dict(meta.get("model_record") or {})
    if not record:
        record = {"maxTokens": meta.get("global_max_tokens"),
                  **({"outputTokenMode": meta["output_token_mode"]} if meta.get("output_token_mode") else {})}
    mode = output_token_mode(record)
    configured = positive_token_count(record.get("maxTokens")) if mode == "fixed" else None
    requested_count = positive_token_count(requested)
    value = min(configured, requested_count) if configured and requested_count else configured or requested_count
    source = "user_or_legacy_fixed" if configured and value == configured else "request_budget" if value else "provider_default"
    if value is None and requires_value:
        # Reuse a per-model verified fact only at a protocol's mandatory field.
        # Auto on protocols with an optional field still omits it. An old user
        # cap or catalog estimate is not evidence of provider capacity.
        provenance = dict(record.get("factProvenance") or {}).get("maxTokens") or {}
        confirmed = (isinstance(provenance, Mapping)
                     and provenance.get("source") in {"online_provider_metadata", "official_docs"}
                     and provenance.get("confidence") == "authoritative")
        value = positive_token_count(record.get("maxTokens")) if confirmed else None
        if value is not None:
            source = "protocol_required_verified_capacity"
        else:
            # Compatibility endpoints may omit model limits entirely. Preserve
            # a usable request, explicitly marked as a fallback (not a model
            # fact); do not derive output capacity from the input window.
            value, source = 32768, "required_parameter_default"
    return {"mode": mode, "maxTokens": value, "source": source}


OUTPUT_TOKEN_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens", "max_tokens_to_sample")


def prepare_output_token_kwargs(meta: Mapping[str, Any], kwargs: Mapping[str, Any], *,
                                key: str = "max_tokens", requires_value: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve aliases once so nested provider arguments cannot bypass a user cap."""
    prepared = dict(kwargs)
    containers = [prepared]
    for name in ("model_kwargs", "extra_body"):
        if isinstance(prepared.get(name), Mapping):
            prepared[name] = dict(prepared[name])
            containers.append(prepared[name])
    requested = []
    for container in containers:
        for alias in OUTPUT_TOKEN_KEYS:
            if alias not in container:
                continue
            value = container.pop(alias)
            if value is None:
                continue
            count = positive_token_count(value)
            if count is None:
                raise ValueError(f"{alias} must be a positive integer")
            requested.append(count)
    budget = resolve_output_token_budget(meta, min(requested) if requested else None, requires_value=requires_value)
    if budget["maxTokens"] is not None:
        prepared[key] = budget["maxTokens"]
    return prepared, budget
