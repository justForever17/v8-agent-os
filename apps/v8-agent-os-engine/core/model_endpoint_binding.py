from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from urllib.parse import urlparse

from core.model_ref import make_model_ref
from core.model_provider_channels import public_provider_channels, resolve_provider_channel
from core.model_protocol_registry import endpoint_path_for_protocol, suggest_model_protocol
from core.provider_hosted_tools import normalize_provider_hosted_tools


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
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or raw.startswith("//"):
        raise ValueError("model endpoint paths must be relative without query or fragment")
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


def join_model_endpoint_url(base_url: Any, endpoint_path: Any) -> str:
    """Join the user-visible channel base and canonical wire endpoint exactly."""

    root = str(base_url or "").strip().rstrip("/")
    path = _clean_relative_path(endpoint_path)
    if not root or not path:
        return root or path
    return f"{root}/{path}"


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
    is_media_model = _is_media_model(model)
    requested_wire_protocol = str(explicit.get("wireProtocol") or explicit.get("wire_protocol") or "").strip()
    channel = resolve_provider_channel(
        provider,
        channel_id=explicit.get("channelId") or explicit.get("channel_id") or "",
        wire_protocol=requested_wire_protocol,
    )
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
    configured_operation_values = (
        media_limits.get("operationKinds")
        if "operationKinds" in media_limits
        else model.get("operationKinds")
    )
    configured_operations = [
        str(item or "").strip()
        for item in list(configured_operation_values or [])
        if str(item or "").strip()
    ] if isinstance(configured_operation_values, list) else []
    operation_scope_declared = (
        "operationKind" in explicit
        or "operation_kind" in explicit
        or "operationKind" in media_limits
        or "operationKinds" in media_limits
        or "operationKinds" in model
    )
    if "operationKind" in explicit or "operation_kind" in explicit:
        operation_kind = str(explicit.get("operationKind") or explicit.get("operation_kind") or "").strip()
    else:
        operation_kind = str(
            media_limits.get("operationKind")
            or (configured_operations[0] if len(configured_operations) == 1 else "")
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
    if not operation_kind and known_operation and not operation_scope_declared:
        operation_kind = known_operation
        inferred = True

    if is_media_model:
        if not provider_model_id:
            provider_model_id = route
        if not endpoint_path and provider_model_id and route.endswith(f"/{provider_model_id}"):
            endpoint_path = route[: -(len(provider_model_id) + 1)].strip("/")
            inferred = bool(endpoint_path)
    else:
        provider_model_id = provider_model_id or route

    channel_api_standard = str(channel.get("apiStandard") or "").strip()
    api_standard = str(
        channel_api_standard
        if channel.get("source") == "configured" or channel.get("selectionSource") == "legacy_default_alias"
        else explicit.get("apiStandard")
        or media_limits.get("apiStandard")
        or channel_api_standard
        or provider.get("api_standard")
        or provider.get("apiStandard")
        or "openai"
    ).strip()
    if "adapter" in explicit:
        adapter = str(explicit.get("adapter") or "").strip()
    elif "adapter" in media_limits:
        adapter = str(media_limits.get("adapter") or "").strip()
    else:
        adapter = str(model.get("adapter") or "").strip()
    wire_protocol = requested_wire_protocol
    if not wire_protocol and channel.get("source") == "configured" and not is_media_model:
        wire_protocol = str(channel.get("defaultWireProtocol") or "").strip()
    provider_hosted_tools = normalize_provider_hosted_tools(explicit.get("providerHostedTools"))
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
    if wire_protocol and not endpoint_path and not is_media_model:
        endpoint_path = endpoint_path_for_protocol(wire_protocol)
    base_url = str(
        channel.get("baseUrl")
        or provider.get("base_url")
        or provider.get("baseUrl")
        or ""
    ).strip().rstrip("/")
    api_version = str(channel.get("apiVersion") or "").strip().strip("/")
    request_base_url = (
        f"{base_url}/{api_version}"
        if base_url and api_version and not base_url.lower().endswith(f"/{api_version.lower()}")
        else base_url
    )
    request_url = join_model_endpoint_url(request_base_url, endpoint_path)
    auth_contract = dict(
        explicit.get("authContract")
        or channel.get("authContract")
        or provider.get("authContract")
        or {}
    )
    persisted = bool(explicit)
    provenance = dict(explicit.get("provenance") or {})
    provenance.setdefault("source", source if persisted else "legacy_projection")
    provenance.setdefault("confidence", "authoritative" if persisted else "reviewed" if inferred else "hint")

    return {
        "version": 2,
        "modelRef": make_model_ref(provider_id, route),
        "providerId": str(provider_id or "").strip(),
        "modelId": route,
        "route": route,
        "endpointPath": endpoint_path,
        "providerModelId": provider_model_id,
        "operationKind": operation_kind,
        "channelId": str(channel.get("id") or ""),
        "channelLabel": str(channel.get("label") or ""),
        "channelSource": str(channel.get("selectionSource") or channel.get("source") or ""),
        "availableChannelIds": list(channel.get("availableChannelIds") or []),
        "baseUrl": base_url,
        "apiVersion": api_version,
        "apiStandard": api_standard,
        "adapter": adapter,
        "wireProtocol": wire_protocol,
        "authContract": auth_contract,
        "providerHostedTools": provider_hosted_tools,
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
    if not binding.get("channelId"):
        raise ValueError("model endpoint binding requires a valid Provider channel")
    requested_channel_id = str(
        ((model.get("endpointBinding") or {}).get("channelId") or "")
    ).strip().lower()
    if requested_channel_id and requested_channel_id != binding.get("channelId") and requested_channel_id != "default":
        raise ValueError(f"unknown Provider channel: {requested_channel_id}")
    selected_channel = resolve_provider_channel(
        provider_meta,
        channel_id=binding.get("channelId"),
        wire_protocol=binding.get("wireProtocol"),
    )
    allowed_protocols = list(selected_channel.get("wireProtocols") or [])
    if binding.get("wireProtocol") and allowed_protocols and binding["wireProtocol"] not in allowed_protocols:
        raise ValueError(
            f"wireProtocol '{binding['wireProtocol']}' is not supported by Provider channel '{binding['channelId']}'"
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
        provider_meta.update(public_provider_channels(provider_meta))
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
    "join_model_endpoint_url",
    "persist_model_endpoint_binding",
    "public_models_config",
]
