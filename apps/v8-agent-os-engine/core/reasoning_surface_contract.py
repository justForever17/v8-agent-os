from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from core.reasoning_payload_contract import REASONING_KEYS


ReasoningSurface = dict[str, Any]

DEFAULT_HIDDEN_REASONING_SURFACE: ReasoningSurface = {
    "mode": "hidden",
    "trust": "unknown",
    "requestStyle": "none",
    "responseFields": [],
    "displayKind": "hidden",
    "sourceRefs": [],
    "notes": "No verified reasoning surface contract is configured for this provider/model.",
}

_VALID_MODES = {"typed_thinking", "reasoning_summary", "provider_reasoning", "hidden"}
_VALID_DISPLAY_KINDS = {"raw_thinking", "summary", "provider_reasoning", "hidden"}
_VALID_TRUST = {"official", "adapter_verified", "catalog_only", "unverified", "unknown"}
_PROVIDER_CATALOG_PATH = Path(__file__).resolve().parent / "model_catalog" / "provider_catalog.json"
_MODEL_CAPABILITY_REGISTRY_PATH = Path(__file__).resolve().parent / "model_catalog" / "model_capability_registry.json"

_COMMON_REASONING_FIELDS = (
    "content[type=thinking]",
    "content[type=reasoning]",
    "content[inline_think]",
    *tuple(f"additional_kwargs.{key}" for key in REASONING_KEYS),
    *tuple(f"response_metadata.{key}" for key in REASONING_KEYS),
    *tuple(f"generation_info.{key}" for key in REASONING_KEYS),
    "reasoning.summary",
    *REASONING_KEYS,
)

_KNOWN_MODEL_ALIASES = {
    "doubao-seed-2.0-pro": "doubao-seed-2-0-pro-260215",
    "doubao-seed-2-0-pro": "doubao-seed-2-0-pro-260215",
    "doubao-seed-2.0-code-preview": "doubao-seed-2-0-code-preview-260215",
    "doubao-seed-2-0-code-preview": "doubao-seed-2-0-code-preview-260215",
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def normalize_model_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9.+()-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def normalize_reasoning_surface(value: Any) -> ReasoningSurface:
    raw = _as_dict(value)
    if not raw:
        return dict(DEFAULT_HIDDEN_REASONING_SURFACE)

    mode = _safe_text(raw.get("mode")).lower() or "hidden"
    if mode not in _VALID_MODES:
        mode = "hidden"
    trust = _safe_text(raw.get("trust")).lower() or "unknown"
    if trust not in _VALID_TRUST:
        trust = "unknown"
    display_kind = _safe_text(raw.get("displayKind") or raw.get("display_kind")).lower()
    if not display_kind:
        display_kind = {
            "typed_thinking": "raw_thinking",
            "reasoning_summary": "summary",
            "provider_reasoning": "provider_reasoning",
        }.get(mode, "hidden")
    if display_kind not in _VALID_DISPLAY_KINDS:
        display_kind = "hidden"

    return {
        **dict(DEFAULT_HIDDEN_REASONING_SURFACE),
        **raw,
        "mode": mode,
        "trust": trust,
        "requestStyle": _safe_text(raw.get("requestStyle") or raw.get("request_style") or "none"),
        "responseFields": [str(item).strip() for item in _as_list(raw.get("responseFields") or raw.get("response_fields")) if str(item).strip()],
        "displayKind": display_kind,
        "sourceRefs": [item for item in _as_list(raw.get("sourceRefs") or raw.get("source_refs")) if item],
        "notes": _safe_text(raw.get("notes")),
    }


def is_reasoning_surface_hidden(surface: Any) -> bool:
    normalized = normalize_reasoning_surface(surface)
    return (
        normalized.get("mode") == "hidden"
        and normalized.get("displayKind") == "hidden"
    )


def is_explicit_reasoning_disabled(surface: Any) -> bool:
    raw = _as_dict(surface)
    if not raw:
        return False
    if raw.get("disabled") is True or raw.get("userDisabled") is True or raw.get("disableReasoningSurface") is True:
        return True
    source = _safe_text(raw.get("source") or raw.get("reasoningSurfaceSource")).lower()
    if source in {"user_disabled", "user", "manual_disable", "manual_disabled"}:
        return True
    notes = _safe_text(raw.get("notes")).lower()
    return "user disabled" in notes or "disabled by user" in notes


def is_stale_auto_hidden_reasoning_surface(surface: Any) -> bool:
    raw = _as_dict(surface)
    if not raw or is_explicit_reasoning_disabled(raw):
        return False
    normalized = normalize_reasoning_surface(raw)
    return (
        normalized.get("mode") == "hidden"
        and normalized.get("trust") == "unknown"
        and normalized.get("requestStyle") == "none"
        and not normalized.get("responseFields")
        and normalized.get("displayKind") == "hidden"
    )


def is_trusted_reasoning_surface(surface: Any) -> bool:
    normalized = normalize_reasoning_surface(surface)
    return (
        normalized.get("mode") != "hidden"
        and normalized.get("trust") in {"official", "adapter_verified"}
        and bool(normalized.get("responseFields"))
    )


def merge_reasoning_surface(provider_surface: Any, model_surface: Any) -> ReasoningSurface:
    provider = normalize_reasoning_surface(provider_surface)
    model = _as_dict(model_surface)
    if not model:
        return provider
    if is_explicit_reasoning_disabled(model):
        return normalize_reasoning_surface(model)
    if is_stale_auto_hidden_reasoning_surface(model) and is_trusted_reasoning_surface(provider):
        return provider
    return normalize_reasoning_surface({**provider, **model})


@lru_cache(maxsize=1)
def _builtin_reasoning_surfaces() -> dict[str, Any]:
    providers: dict[str, Any] = {}
    global_models: dict[str, Any] = {}
    try:
        payload = json.loads(_PROVIDER_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if isinstance(payload, Mapping):
        for provider in payload.get("providers") or []:
            if not isinstance(provider, Mapping):
                continue
            provider_id = _safe_text(provider.get("id"))
            if not provider_id:
                continue
            models = {}
            for model in provider.get("models") or []:
                if not isinstance(model, Mapping) or not _safe_text(model.get("id")):
                    continue
                model_id = _safe_text(model.get("id"))
                surface = model.get("reasoningSurface")
                models[model_id] = surface
                models[normalize_model_key(model_id)] = surface
            providers[provider_id] = {
                "provider": provider.get("reasoningSurface"),
                "models": models,
            }

    try:
        registry_payload = json.loads(_MODEL_CAPABILITY_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        registry_payload = {}
    if isinstance(registry_payload, Mapping):
        for item in registry_payload.get("models") or []:
            if not isinstance(item, Mapping):
                continue
            surface = item.get("reasoningSurface")
            if not surface:
                continue
            keys = [
                item.get("canonicalModelId"),
                item.get("displayName"),
                *(_as_list(item.get("aliases"))),
            ]
            for key in keys:
                normalized = normalize_model_key(key)
                if normalized and normalized not in global_models:
                    global_models[normalized] = surface

    return {"providers": providers, "models": global_models}


def _model_alias_keys(model_id: str) -> list[str]:
    base = _safe_text(model_id)
    keys = [base, normalize_model_key(base)]
    mapped = _KNOWN_MODEL_ALIASES.get(base.lower()) or _KNOWN_MODEL_ALIASES.get(normalize_model_key(base))
    if mapped:
        keys.extend([mapped, normalize_model_key(mapped)])
    if "." in base:
        keys.append(base.replace(".", "-"))
        keys.append(normalize_model_key(base.replace(".", "-")))
    deduped: list[str] = []
    for key in keys:
        normalized = str(key or "").strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _lookup_builtin_surfaces(provider_id: str, model_id: str) -> tuple[Any, Any]:
    builtin = _builtin_reasoning_surfaces()
    providers = builtin.get("providers") if isinstance(builtin, Mapping) else {}
    models = builtin.get("models") if isinstance(builtin, Mapping) else {}
    provider_entry = _as_dict((providers or {}).get(provider_id)) if isinstance(providers, Mapping) else {}
    provider_surface = provider_entry.get("provider")
    provider_models = provider_entry.get("models") if isinstance(provider_entry.get("models"), Mapping) else {}
    model_surface = None
    for key in _model_alias_keys(model_id):
        model_surface = provider_models.get(key) or (models or {}).get(normalize_model_key(key))
        if model_surface:
            break
    return provider_surface, model_surface


def detect_unverified_reasoning_field(payload: Any) -> str:
    return next((field for field in _COMMON_REASONING_FIELDS if _path_exists(payload, field)), "")


def resolve_reasoning_surface_for_metadata(metadata: Mapping[str, Any] | None) -> ReasoningSurface:
    meta = dict(metadata or {})
    provider_record = _as_dict(meta.get("provider_record") or meta.get("provider"))
    model_record = _as_dict(meta.get("model_record") or meta.get("model"))
    provider_id = _safe_text(meta.get("provider_id") or meta.get("providerId"))
    model_id = _safe_text(meta.get("model_id") or meta.get("modelId") or meta.get("model_name") or meta.get("modelName"))
    explicit = _as_dict(meta.get("reasoning_surface") or meta.get("reasoningSurface"))
    builtin_provider, builtin_model = _lookup_builtin_surfaces(provider_id, model_id)
    provider_surface = provider_record.get("reasoningSurface") or builtin_provider

    if explicit:
        if is_explicit_reasoning_disabled(explicit):
            return normalize_reasoning_surface(explicit)
        if is_stale_auto_hidden_reasoning_surface(explicit) and is_trusted_reasoning_surface(builtin_model):
            return merge_reasoning_surface(provider_surface, builtin_model)
        return merge_reasoning_surface(provider_surface, explicit)

    model_surface = model_record.get("reasoningSurface")
    if is_explicit_reasoning_disabled(model_surface):
        return normalize_reasoning_surface(model_surface)
    if is_stale_auto_hidden_reasoning_surface(model_surface) and is_trusted_reasoning_surface(builtin_model):
        model_surface = builtin_model
    elif not model_surface and builtin_model:
        model_surface = builtin_model

    return merge_reasoning_surface(
        provider_surface,
        model_surface,
    )


def _iter_content_blocks(payload: Any) -> list[Any]:
    if payload is None:
        return []
    content_blocks = getattr(payload, "content_blocks", None)
    if content_blocks is None and isinstance(payload, Mapping):
        content_blocks = payload.get("content_blocks")
    if content_blocks is None:
        content = getattr(payload, "content", None)
        if content is None and isinstance(payload, Mapping):
            content = payload.get("content")
        content_blocks = content if isinstance(content, list) else None
    return content_blocks if isinstance(content_blocks, list) else []


def _path_exists(payload: Any, field_path: str) -> bool:
    path = field_path.strip()
    if not path:
        return False
    if path.startswith("content["):
        if "type=thinking" in path:
            return any(_safe_text((block.get("type") if isinstance(block, Mapping) else getattr(block, "type", ""))).lower() == "thinking" for block in _iter_content_blocks(payload))
        if "type=reasoning" in path:
            return any(_safe_text((block.get("type") if isinstance(block, Mapping) else getattr(block, "type", ""))).lower() == "reasoning" for block in _iter_content_blocks(payload))
        if "inline_think" in path:
            content = getattr(payload, "content", None)
            if content is None and isinstance(payload, Mapping):
                content = payload.get("content")
            return isinstance(content, str) and "<think" in content.lower()
    current: Any = payload
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, Mapping):
            if part not in current:
                return False
            current = current.get(part)
            continue
        if not hasattr(current, part):
            return False
        current = getattr(current, part)
    return current not in (None, "", [], {})


def reasoning_kind_for_surface(surface: Mapping[str, Any]) -> str:
    display_kind = _safe_text(surface.get("displayKind")).lower()
    if display_kind == "raw_thinking":
        return "raw_thinking"
    if display_kind == "summary":
        return "summary"
    if display_kind == "provider_reasoning":
        return "provider_reasoning"
    return "hidden"


def evaluate_reasoning_payload(surface_value: Any, payload: Any) -> dict[str, Any]:
    surface = normalize_reasoning_surface(surface_value)
    mode = _safe_text(surface.get("mode")).lower()
    fields = [str(item).strip() for item in surface.get("responseFields") or [] if str(item).strip()]
    matched_field = next((field for field in fields if _path_exists(payload, field)), "")
    accepted = mode != "hidden" and bool(matched_field)
    if not accepted:
        unverified_field = "" if is_explicit_reasoning_disabled(surface) else detect_unverified_reasoning_field(payload)
        if unverified_field:
            return {
                "accepted": True,
                "matchedField": unverified_field,
                "reasoningKind": "provider_reasoning",
                "reasoningSurfaceMode": "unverified",
                "reasoningSurfaceTrust": "unverified",
                "reasoningDisplayKind": "provider_reasoning",
                "reasoningRequestStyle": surface.get("requestStyle") or "none",
                "reasoningUnverified": True,
                "reasoningSurface": {
                    **surface,
                    "mode": "provider_reasoning",
                    "trust": "unverified",
                    "displayKind": "provider_reasoning",
                    "responseFields": [unverified_field],
                    "notes": "V8 detected a separated reasoning field that is not yet registered in the provider/model contract.",
                    "unverified": True,
                },
            }
    reasoning_kind = reasoning_kind_for_surface(surface) if accepted else "hidden"
    return {
        "accepted": accepted,
        "matchedField": matched_field,
        "reasoningKind": reasoning_kind,
        "reasoningSurfaceMode": mode,
        "reasoningSurfaceTrust": surface.get("trust") or "unknown",
        "reasoningDisplayKind": surface.get("displayKind") or "hidden",
        "reasoningRequestStyle": surface.get("requestStyle") or "none",
        "reasoningSurface": surface,
    }
