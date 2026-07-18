from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from urllib.parse import urlparse

from core.model_ref import make_model_ref
from core.model_protocol_registry import endpoint_path_for_protocol, suggest_model_protocol


_MEDIA_MODEL_TYPES = {
    "MEDIA",
    "IMAGE",
    "VIDEO",
    "AUDIO",
    "VOICE",
    "MUSIC",
    "WORKFLOW",
    "MODEL3D",
}

_KNOWN_MEDIA_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("images/generations", "image.generate"),
    ("images/edits", "image.edit"),
    ("services/aigc/multimodal-generation/generation", "image.generate"),
    ("contents/generations/tasks", "video.text_to_video"),
    ("image_generation", "image.generate"),
    ("videos/generations", "video.text_to_video"),
    ("video_generation", "video.text_to_video"),
    ("audio/speech", "voice.tts"),
    ("t2a_v2", "voice.tts"),
    ("music_generation", "music.generate"),
)


def _clean_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        raise ValueError("model endpoint paths must be relative to the Provider base URL")
    clean = "/".join(part for part in raw.strip("/").split("/") if part not in {"", "."})
    if any(part == ".." for part in clean.split("/")):
        raise ValueError("model endpoint paths cannot traverse parent directories")
    return clean


def _relative_submit_path(base_url: Any, submit_path: Any) -> str:
    submit = _clean_relative_path(submit_path)
    if not submit:
        return ""
    base_path = _clean_relative_path(urlparse(str(base_url or "").strip()).path)
    if base_path and (submit == base_path or submit.startswith(f"{base_path}/")):
        return submit[len(base_path):].strip("/")
    return submit


def _is_media_model(model_meta: Dict[str, Any]) -> bool:
    model_type = str(model_meta.get("type") or "").strip().upper()
    capability_class = str(model_meta.get("capabilityClass") or "").strip().lower()
    return model_type in _MEDIA_MODEL_TYPES or capability_class == "media_generation" or bool(model_meta.get("mediaLimits"))


def _known_route_split(route: str) -> tuple[str, str, str]:
    clean = _clean_relative_path(route)
    for prefix, operation_kind in _KNOWN_MEDIA_ENDPOINTS:
        if clean == prefix:
            return prefix, "", operation_kind
        if clean.startswith(f"{prefix}/"):
            return prefix, clean[len(prefix) + 1 :], operation_kind
    return "", "", ""


def build_model_endpoint_binding(
    provider_id: str,
    model_id: str,
    provider_meta: Dict[str, Any] | None,
    model_meta: Dict[str, Any] | None,
    *,
    source: str = "stored",
) -> Dict[str, Any]:
    """Build the single auditable endpoint contract for one configured model.

    `modelId` remains the human-visible Provider-relative route. `providerModelId`
    is the value placed in the provider request body. Old records are projected
    without being rewritten; callers may persist the returned binding explicitly.
    """

    provider = dict(provider_meta or {})
    model = dict(model_meta or {})
    explicit = dict(model.get("endpointBinding") or {})
    media_limits = dict(model.get("mediaLimits") or {})
    route = _clean_relative_path(explicit.get("route") or model_id)
    provider_model_id = str(
        explicit.get("providerModelId")
        or media_limits.get("providerModelId")
        or ""
    ).strip().strip("/")
    endpoint_path = _clean_relative_path(
        explicit.get("endpointPath")
        or media_limits.get("endpointPath")
        or _relative_submit_path(
            provider.get("base_url") or provider.get("baseUrl") or "",
            media_limits.get("submitPath") or "",
        )
    )
    operation_kind = str(
        explicit.get("operationKind")
        or media_limits.get("operationKind")
        or ((media_limits.get("operationKinds") or [""])[0] if isinstance(media_limits.get("operationKinds"), list) else "")
        or ""
    ).strip()

    known_endpoint, known_provider_model_id, known_operation = _known_route_split(route)
    inferred = False
    if not endpoint_path and known_endpoint:
        endpoint_path = known_endpoint
        inferred = True
    if not provider_model_id and known_provider_model_id:
        provider_model_id = known_provider_model_id
        inferred = True
    if not operation_kind and known_operation:
        operation_kind = known_operation
        inferred = True

    if _is_media_model(model):
        if not provider_model_id:
            provider_model_id = route
        if not endpoint_path and provider_model_id and route.endswith(f"/{provider_model_id}"):
            endpoint_path = route[: -(len(provider_model_id) + 1)].strip("/")
            inferred = bool(endpoint_path)
    else:
        provider_model_id = provider_model_id or route

    api_standard = str(
        explicit.get("apiStandard")
        or media_limits.get("apiStandard")
        or provider.get("api_standard")
        or provider.get("apiStandard")
        or "openai"
    ).strip()
    adapter = str(
        explicit.get("adapter")
        or media_limits.get("adapter")
        or model.get("adapter")
        or ""
    ).strip()
    wire_protocol = str(explicit.get("wireProtocol") or explicit.get("wire_protocol") or "").strip()
    protocol_advice = suggest_model_protocol(
        provider_id,
        api_standard,
        provider_model_id or route,
        provider_meta=provider,
        model_meta=model,
    )
    if wire_protocol:
        protocol_advice = {
            **protocol_advice,
            "wireProtocol": wire_protocol,
            "endpointPath": endpoint_path_for_protocol(wire_protocol),
            "confidence": str(explicit.get("protocolConfidence") or "authoritative"),
            "source": str(explicit.get("protocolSource") or "manual"),
            "sourceRefs": list(explicit.get("protocolSourceRefs") or protocol_advice.get("sourceRefs") or []),
            "warning": str(explicit.get("protocolWarning") or ""),
        }
    if wire_protocol and not endpoint_path and not _is_media_model(model):
        endpoint_path = endpoint_path_for_protocol(wire_protocol)
    base_url = str(provider.get("base_url") or provider.get("baseUrl") or "").strip().rstrip("/")
    request_url = f"{base_url}/{endpoint_path}" if base_url and endpoint_path else base_url
    persisted = bool(explicit)
    provenance = dict(explicit.get("provenance") or {})
    provenance.setdefault("source", source if persisted else "legacy_projection")
    provenance.setdefault("confidence", "authoritative" if persisted else "reviewed" if inferred else "hint")

    return {
        "version": 1,
        "modelRef": make_model_ref(provider_id, route),
        "providerId": str(provider_id or "").strip(),
        "modelId": route,
        "route": route,
        "endpointPath": endpoint_path,
        "providerModelId": provider_model_id,
        "operationKind": operation_kind,
        "apiStandard": api_standard,
        "adapter": adapter,
        "wireProtocol": wire_protocol,
        "protocolSuggestion": str(protocol_advice.get("wireProtocol") or ""),
        "protocolEndpointPath": str(protocol_advice.get("endpointPath") or ""),
        "protocolConfidence": str(protocol_advice.get("confidence") or ""),
        "protocolSource": str(protocol_advice.get("source") or ""),
        "protocolSourceRefs": list(protocol_advice.get("sourceRefs") or []),
        "protocolWarning": str(protocol_advice.get("warning") or ""),
        "requestUrlPreview": request_url,
        "persisted": persisted,
        "provenance": provenance,
    }


def persist_model_endpoint_binding(
    provider_id: str,
    model_id: str,
    provider_meta: Dict[str, Any] | None,
    model_meta: Dict[str, Any] | None,
    *,
    source: str,
) -> Dict[str, Any]:
    model = deepcopy(dict(model_meta or {}))
    binding = build_model_endpoint_binding(
        provider_id,
        model_id,
        provider_meta,
        model,
        source=source,
    )
    binding["persisted"] = True
    binding["provenance"] = {
        **dict(binding.get("provenance") or {}),
        "source": source,
        "confidence": "authoritative",
    }
    model["endpointBinding"] = binding
    media_limits = dict(model.get("mediaLimits") or {})
    if binding.get("providerModelId"):
        media_limits["providerModelId"] = binding["providerModelId"]
    if binding.get("endpointPath"):
        media_limits["endpointPath"] = binding["endpointPath"]
    if binding.get("operationKind"):
        operations = list(media_limits.get("operationKinds") or [])
        if binding["operationKind"] not in operations:
            operations.insert(0, binding["operationKind"])
        media_limits["operationKinds"] = operations
    if binding.get("adapter"):
        media_limits["adapter"] = binding["adapter"]
    model["mediaLimits"] = media_limits
    return model


def public_models_config(config: Dict[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(config or {}))
    for provider_id, provider_data in dict(payload.get("providers") or {}).items():
        if not isinstance(provider_data, dict):
            continue
        provider_meta = dict(provider_data.get("provider") or {})
        secret_values = []
        for key in (
            "api_key",
            "apiKey",
            "secret_key",
            "secretKey",
            "access_token",
            "accessToken",
            "oauth_access_token",
            "refresh_token",
            "refreshToken",
            "client_secret",
            "clientSecret",
            "authorization",
            "Authorization",
            "token",
            "password",
        ):
            if key in provider_meta:
                secret_values.append(str(provider_meta.pop(key) or ""))
        raw_credential = next((value for value in secret_values if value), "")
        if raw_credential.startswith("oauth:"):
            provider_meta["oauthPath"] = raw_credential[6:]
        provider_meta["credentialConfigured"] = bool(
            raw_credential or provider_meta.get("credentialRef") or provider_meta.get("oauth_ref")
        )
        provider_meta["credentialMode"] = str(
            provider_meta.get("credential_mode")
            or provider_meta.get("credentialMode")
            or ("oauthFile" if raw_credential.startswith("oauth:") else "apiKey")
        )
        provider_data["provider"] = provider_meta
        models = dict(provider_data.get("models") or {})
        for model_id, model_meta_raw in models.items():
            model_meta = dict(model_meta_raw or {})
            model_meta["endpointBinding"] = build_model_endpoint_binding(
                str(provider_id),
                str(model_id),
                provider_meta,
                model_meta,
            )
            models[model_id] = model_meta
        provider_data["models"] = models
    return payload


__all__ = [
    "build_model_endpoint_binding",
    "persist_model_endpoint_binding",
    "public_models_config",
]
