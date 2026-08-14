from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
from urllib.parse import urlparse

from core.model_provider_channels import resolve_provider_channel
from core.model_protocol_registry import suggest_model_protocol
from core.prompt_cache_gateway import prompt_cache_profile_id_for_provider


_MEDIA_MODEL_TYPES = {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}
_MODEL_TYPES = _MEDIA_MODEL_TYPES | {"TEXT", "MULTIMODAL", "EMBEDDING", "RERANK", "RERANKER"}
_TRUSTED_OAUTH_FILE_CONTRACTS = {
    "codex": {
        "path": "~/.codex/auth.json",
        "preset": "codex",
        "origin": "https://chatgpt.com/backend-api",
    }
}


def _mapping(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_path(value: Any) -> str:
    return _clean(value).strip("/")


def _validated_endpoint_path(value: Any) -> str:
    raw = _clean(value).replace("\\", "/").strip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.netloc or parsed.query or parsed.fragment or raw.startswith(("//", "/")):
        raise ValueError("catalog endpoint paths must be relative without query or fragment")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("catalog endpoint paths cannot traverse parent directories")
    return "/".join(parts)


def _validated_connection_url(value: Any) -> str:
    raw = _clean(value)
    parsed = urlparse(raw)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("catalog Provider does not expose a connectable HTTP(S) endpoint") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("catalog Provider does not expose a connectable HTTP(S) endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("catalog Provider endpoint must not contain URL userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("catalog Provider endpoint must not contain URL query or fragment")
    return raw.rstrip("/")


def _materialize_endpoint_path(endpoint_path: str, provider_model_id: str) -> str:
    path = _clean_path(endpoint_path)
    model_id = _clean_path(provider_model_id)
    if "{model}" in path:
        if not model_id:
            raise ValueError("catalog endpointPath requires a provider model id")
        return path.replace("{model}", model_id)
    return path


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(_clean(item) for item in value if _clean(item)))


def provider_auth_contract(provider: Mapping[str, Any]) -> dict[str, str]:
    """Return only non-secret fields that define how a credential is sent."""

    auth = _mapping(provider.get("auth"))
    contract = {
        "type": _clean(auth.get("type") or "api_key").lower(),
        "header": _clean(auth.get("header")),
        "scheme": _clean(auth.get("scheme")),
        "query": _clean(auth.get("query")),
        "preset": _clean(auth.get("preset")),
    }
    if contract["type"] == "oauth_file":
        contract["path"] = _clean(auth.get("path"))
    return {key: value for key, value in contract.items() if value}


def _media_endpoint_defaults(
    *,
    provider: Mapping[str, Any],
    model: Mapping[str, Any],
    model_id: str,
) -> dict[str, str]:
    media_limits = _mapping(model.get("mediaLimits"))
    endpoint_path = _clean_path(
        media_limits.get("endpointPath")
        or media_limits.get("requestPath")
        or media_limits.get("submitPath")
        or _mapping(provider.get("request")).get("submitPath")
    )
    provider_model_id = _clean_path(media_limits.get("providerModelId"))
    if not provider_model_id:
        provider_model_id = model_id[len(endpoint_path) + 1 :] if endpoint_path and model_id.startswith(f"{endpoint_path}/") else model_id
    operation_kinds = _string_list(media_limits.get("operationKinds") or model.get("operationKinds"))
    return {
        "endpointPath": endpoint_path,
        "providerModelId": provider_model_id,
        "operationKind": operation_kinds[0] if operation_kinds else "",
        "adapter": _clean(media_limits.get("adapter") or model.get("adapter") or provider.get("adapter")),
    }


def build_catalog_model_connection_plan(
    *,
    provider: Mapping[str, Any],
    model: Mapping[str, Any],
    model_id: str,
    existing_provider: Mapping[str, Any] | None = None,
    existing_model: Mapping[str, Any] | None = None,
    base_url: str = "",
    api_standard: str = "",
    requested_model_type: str = "",
    endpoint_path: str = "",
    provider_model_id: str = "",
    operation_kind: str = "",
    adapter: str = "",
    wire_protocol: str = "",
    channel_id: str = "",
    channels: list[dict[str, Any]] | None = None,
    default_channel_id: str = "",
    voice_app_id: str = "",
    voice_resource_id: str = "",
    credential_value: str = "",
    credential_ref: str = "",
    use_catalog_default_channel: bool = False,
    source: str = "quick_connect",
) -> dict[str, Any]:
    """Build the one canonical provider/model patch used by every connect entry point.

    This function never reads or writes storage and never resolves a credential
    reference. Callers may supply either a one-shot credential value (Admin) or
    an existing exact-provider reference (Config Broker), but neither is
    returned in the public summary.
    """

    provider_row = _mapping(provider)
    model_row = _mapping(model)
    existing = _mapping(existing_provider)
    existing_model_row = _mapping(existing_model)
    existing = {
        key: value
        for key, value in existing.items()
        if key not in {"api_key", "credentialRef", "credentialSource"}
    }
    provider_id = _clean(provider_row.get("id"))
    requested_model_id = _clean_path(model_id)
    if not provider_id or not requested_model_id:
        raise ValueError("providerId and modelId are required")

    configured_channels = deepcopy(channels) if isinstance(channels, list) and channels else deepcopy(provider_row.get("channels") or [])
    requested_channel_id = _clean(channel_id).lower()
    if use_catalog_default_channel and not requested_channel_id:
        requested_channel_id = _clean(default_channel_id or provider_row.get("defaultChannelId")).lower()
    requested_wire_protocol = _clean(wire_protocol)
    channel_provider = {
        **provider_row,
        **({"channels": configured_channels} if configured_channels else {}),
        **({"defaultChannelId": _clean(default_channel_id or provider_row.get("defaultChannelId"))} if configured_channels else {}),
    }
    selected_channel = resolve_provider_channel(
        channel_provider,
        channel_id=requested_channel_id,
        wire_protocol=requested_wire_protocol,
    )
    if requested_channel_id and selected_channel.get("id") != requested_channel_id:
        raise ValueError(f"unknown Provider channel: {requested_channel_id}")
    supported_protocols = _string_list(selected_channel.get("wireProtocols"))
    if requested_wire_protocol and supported_protocols and requested_wire_protocol not in supported_protocols:
        raise ValueError(
            f"wireProtocol '{requested_wire_protocol}' is not supported by Provider channel '{selected_channel.get('id') or 'default'}'"
        )
    if not requested_wire_protocol and selected_channel.get("source") == "configured":
        requested_wire_protocol = _clean(selected_channel.get("defaultWireProtocol"))

    requested_type = _clean(requested_model_type).upper()
    catalog_type = _clean(model_row.get("type") or "TEXT").upper()
    capability_class = _clean(model_row.get("capabilityClass"))
    if capability_class == "media_generation" and requested_type in {"", "TEXT", "MULTIMODAL"}:
        normalized_model_type = catalog_type
    else:
        normalized_model_type = requested_type if requested_type in _MODEL_TYPES else catalog_type
    if normalized_model_type == "RERANKER":
        normalized_model_type = "RERANK"
    is_media_provider = _clean(provider_row.get("providerKind")) == "media_generation" or normalized_model_type in _MEDIA_MODEL_TYPES
    is_retrieval_model = normalized_model_type in {"EMBEDDING", "RERANK"} or capability_class.lower() in {"embedding", "reranker", "rerank"}

    media_defaults = _media_endpoint_defaults(provider=provider_row, model=model_row, model_id=requested_model_id)
    raw_endpoint_path = _validated_endpoint_path(
        endpoint_path or (media_defaults.get("endpointPath") if is_media_provider else "")
    )
    resolved_provider_model_id = _clean_path(provider_model_id or (media_defaults.get("providerModelId") if is_media_provider else requested_model_id))
    resolved_endpoint_path = _materialize_endpoint_path(raw_endpoint_path, resolved_provider_model_id)
    resolved_operation_kind = _clean(operation_kind or (media_defaults.get("operationKind") if is_media_provider else ""))
    resolved_adapter = _clean(adapter or (media_defaults.get("adapter") if is_media_provider else ""))
    resolved_model_id = requested_model_id
    if is_media_provider and resolved_endpoint_path and resolved_provider_model_id:
        if "{model}" in raw_endpoint_path:
            resolved_model_id = resolved_endpoint_path
        else:
            resolved_model_id = (
                requested_model_id
                if requested_model_id == resolved_endpoint_path
                or requested_model_id.startswith(f"{resolved_endpoint_path}/")
                else f"{resolved_endpoint_path}/{resolved_provider_model_id}"
            )

    selected_api_standard = _clean(api_standard or provider_row.get("apiStandard") or "openai")
    selected_base_url = _clean(base_url or provider_row.get("baseUrl")).rstrip("/")
    if selected_channel.get("source") == "configured" and (requested_channel_id or use_catalog_default_channel):
        selected_base_url = _clean(selected_channel.get("baseUrl") or selected_base_url).rstrip("/")
        selected_api_standard = _clean(selected_channel.get("apiStandard") or selected_api_standard)
    catalog_api_standard = _clean(provider_row.get("apiStandard") or provider_row.get("api_standard")).lower()
    catalog_adapter = _clean(provider_row.get("adapter")).lower()
    model_adapter = _clean(model_row.get("adapter")).lower()
    if (
        selected_api_standard.lower() == "catalog_only"
        or catalog_api_standard == "catalog_only"
        or catalog_adapter == "catalog_only"
        or resolved_adapter.lower() == "catalog_only"
        or model_adapter == "catalog_only"
    ):
        raise ValueError("catalog Provider does not expose an executable runtime adapter")
    selected_base_url = _validated_connection_url(selected_base_url)
    parsed_base_url = urlparse(selected_base_url)

    auth = _mapping(provider_row.get("auth"))
    selected_channel_auth = _mapping(selected_channel.get("authContract") or selected_channel.get("auth"))
    effective_auth = selected_channel_auth or auth
    auth_type = _clean(effective_auth.get("type") or "api_key").lower()
    if auth_type not in {"api_key", "none", "oauth_file"}:
        raise ValueError("catalog Provider auth.type is not supported")
    if auth_type == "oauth_file":
        if _clean(auth.get("type")).lower() != "oauth_file" or effective_auth != auth:
            raise ValueError("catalog channel cannot introduce or change oauth_file auth")
        trusted = _TRUSTED_OAUTH_FILE_CONTRACTS.get(provider_id)
        trusted_origin = _clean((trusted or {}).get("origin")).rstrip("/")
        if (
            not trusted
            or _clean(effective_auth.get("path")) != _clean(trusted.get("path"))
            or _clean(effective_auth.get("preset")) != _clean(trusted.get("preset"))
            or selected_base_url != trusted_origin
        ):
            raise ValueError("catalog oauth_file auth is not a locked builtin contract")
    auth_contract = provider_auth_contract(
        {"auth": effective_auth}
    )
    credential_mode = (
        "oauthFile"
        if auth_type == "oauth_file"
        else "none"
        if auth_type == "none"
        else "apiKey"
    )
    oauth_path = _clean(effective_auth.get("path"))
    provider_patch: dict[str, Any] = {
        **existing,
        "name": _clean(provider_row.get("name") or provider_id),
        "base_url": selected_base_url,
        "api_standard": selected_api_standard,
        "providerKind": _clean(provider_row.get("providerKind") or existing.get("providerKind") or "chat"),
        "mediaModality": _clean(provider_row.get("mediaModality") or existing.get("mediaModality")),
        "type": "PLATFORM" if auth_type == "oauth_file" else "API",
        "voice_app_id": _clean(voice_app_id or existing.get("voice_app_id")),
        "voice_resource_id": _clean(voice_resource_id or existing.get("voice_resource_id")),
        "credential_mode": credential_mode,
        "oauth_preset": _clean(effective_auth.get("preset") or existing.get("oauth_preset")),
        "logoAsset": _clean(provider_row.get("logoAsset") or existing.get("logoAsset")),
        "credentialRealm": _clean(provider_row.get("credentialRealm") or existing.get("credentialRealm")),
        "authContract": auth_contract,
        "promptCachingProfileId": _clean(
            provider_row.get("promptCachingProfileId")
            or existing.get("promptCachingProfileId")
            or prompt_cache_profile_id_for_provider(provider_id)
        ),
        "is_enabled": bool(existing.get("is_enabled", existing.get("isEnabled", True))),
    }
    if auth_type == "oauth_file":
        provider_patch["api_key"] = f"oauth:{oauth_path}"
    elif _clean(credential_value):
        provider_patch["api_key"] = _clean(credential_value)
    elif _clean(credential_ref):
        provider_patch["credentialRef"] = _clean(credential_ref)
        provider_patch["credentialSource"] = "os_credential_store"
    if configured_channels:
        provider_patch["channels"] = configured_channels
        provider_patch["defaultChannelId"] = _clean(default_channel_id or provider_row.get("defaultChannelId") or selected_channel.get("id"))

    is_custom_provider = bool(provider_row.get("isCustom"))
    is_oauth_provider = auth_type == "oauth_file"
    registry_known_chat_model = bool(model_row.get("capabilityRegistryMatched")) and not is_media_provider
    clear_runtime_budget = is_media_provider or is_oauth_provider or (is_custom_provider and not registry_known_chat_model and not is_retrieval_model)
    managed_context_window = None if clear_runtime_budget else model_row.get("contextWindow")
    managed_max_tokens = None if clear_runtime_budget or is_retrieval_model else model_row.get("maxTokens")

    model_patch: dict[str, Any] = {
        "type": normalized_model_type or "TEXT",
        "contextWindow": managed_context_window,
        "maxTokens": managed_max_tokens,
        "factProvenance": _mapping(model_row.get("factProvenance")),
        "capabilities": _mapping(model_row.get("capabilities")),
        "capabilityClass": capability_class
        or ("media_generation" if is_media_provider else "vision_multimodal" if _mapping(model_row.get("capabilities")).get("vision") else "chat_general"),
        "capabilitySource": _clean(model_row.get("capabilitySource") or "manual"),
        "parameterProfile": _clean(model_row.get("parameterProfile") or ("media_generation" if is_media_provider else "chat")),
        "mediaLimits": _mapping(model_row.get("mediaLimits")),
        "logoAsset": _clean(model_row.get("logoAsset")),
        "capabilityRegistry": _mapping(model_row.get("capabilityRegistry")),
        "pricing": _mapping(model_row.get("pricing")),
        "driftWarnings": deepcopy(model_row.get("driftWarnings") or []),
        "reasoningSurface": _mapping(model_row.get("reasoningSurface")),
        "thinkingControl": _mapping(model_row.get("thinkingControl")),
        "reasoningEffortControl": _mapping(model_row.get("reasoningEffortControl")),
        "promptCachingProfileId": _clean(
            model_row.get("promptCachingProfileId")
            or provider_patch.get("promptCachingProfileId")
            or prompt_cache_profile_id_for_provider(provider_id)
        ),
        "isEnabled": bool(existing_model_row.get("isEnabled", True)),
    }
    for key in ("operationKinds", "adapter", "rerankApiFlavor", "availability", "sourceRefs"):
        if model_row.get(key) not in (None, "", [], {}):
            model_patch[key] = deepcopy(model_row.get(key))

    protocol_advice = suggest_model_protocol(
        provider_id,
        selected_api_standard,
        resolved_provider_model_id or resolved_model_id,
        provider_meta=provider_row,
        model_meta=model_row,
    )
    protocol_endpoint_path = _materialize_endpoint_path(
        _validated_endpoint_path(protocol_advice.get("endpointPath")),
        resolved_provider_model_id or resolved_model_id,
    )
    has_explicit_endpoint = bool(
        resolved_endpoint_path
        or resolved_operation_kind
        or resolved_adapter
        or requested_wire_protocol
        or requested_channel_id
        or use_catalog_default_channel
    )
    if has_explicit_endpoint:
        explicit_protocol = requested_wire_protocol
        protocol_is_authoritative = bool(explicit_protocol and selected_channel.get("source") == "configured")
        model_patch["endpointBinding"] = {
            "route": resolved_model_id,
            "endpointPath": resolved_endpoint_path or (protocol_endpoint_path if explicit_protocol else ""),
            "providerModelId": resolved_provider_model_id,
            "operationKind": resolved_operation_kind,
            "adapter": resolved_adapter,
            "wireProtocol": explicit_protocol,
            "channelId": _clean(selected_channel.get("id") or requested_channel_id),
            "authContract": deepcopy(auth_contract),
            "protocolConfidence": "authoritative" if protocol_is_authoritative else _clean(protocol_advice.get("confidence") or "hint"),
            "protocolSource": "channel" if protocol_is_authoritative else _clean(protocol_advice.get("source") or "fallback"),
            "protocolSourceRefs": deepcopy(protocol_advice.get("sourceRefs") or []),
            "protocolWarning": "" if protocol_is_authoritative else _clean(protocol_advice.get("warning")),
            "provenance": {"source": source, "confidence": "authoritative"},
        }

    has_credential = bool(_clean(credential_value) or _clean(credential_ref) or auth_type in {"none", "oauth_file"})
    return {
        "providerId": provider_id,
        "catalogModelId": requested_model_id,
        "modelId": resolved_model_id,
        "providerPatch": provider_patch,
        "modelPatch": model_patch,
        "replaceProviderModels": bool(provider_row.get("singleActiveModel")),
        "credentialRequired": auth_type == "api_key",
        "credentialConfigured": has_credential,
        "credentialMode": credential_mode,
        "selectedChannel": {
            "id": selected_channel.get("id"),
            "wireProtocol": requested_wire_protocol,
            "baseUrl": selected_base_url,
            "apiStandard": selected_api_standard,
        },
        "protocolAdvice": protocol_advice,
    }


__all__ = ["build_catalog_model_connection_plan", "provider_auth_contract"]
