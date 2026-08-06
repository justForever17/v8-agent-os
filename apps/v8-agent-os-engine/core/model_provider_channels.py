from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable


_CHANNEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_WIRE_PROTOCOLS_BY_STANDARD: dict[str, tuple[str, ...]] = {
    "openai": ("openai.chat_completions", "openai.responses"),
    "anthropic": ("anthropic.messages",),
    "gemini": ("gemini.generate_content",),
}


def _as_channel_list(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [
            {"id": str(channel_id), **dict(item)}
            for channel_id, item in value.items()
            if isinstance(item, dict)
        ]
    return []


def default_wire_protocols(api_standard: Any) -> list[str]:
    normalized = str(api_standard or "openai").strip().lower()
    return list(_WIRE_PROTOCOLS_BY_STANDARD.get(normalized, ()))


def _normalize_wire_protocols(value: Any, api_standard: str) -> list[str]:
    incoming: Iterable[Any]
    if isinstance(value, (list, tuple, set)):
        incoming = value
    elif value:
        incoming = [value]
    else:
        incoming = default_wire_protocols(api_standard)
    result: list[str] = []
    for item in incoming:
        protocol = str(item or "").strip()
        if protocol and protocol not in result:
            result.append(protocol)
    return result


def _normalize_auth_contract(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    allowed = {"type", "header", "scheme", "query", "preset", "path"}
    return {
        str(key): str(item or "").strip()
        for key, item in value.items()
        if str(key) in allowed and str(item or "").strip()
    }


def normalize_provider_channels(provider_meta: Dict[str, Any] | None) -> Dict[str, Any]:
    """Project configured and legacy Provider endpoints into one channel contract.

    A legacy Provider is never rewritten. It is exposed as a read-only projected
    ``default`` channel whose base URL is preserved byte-for-byte (apart from
    surrounding whitespace and a trailing slash). No protocol is inferred from
    model names or URL suffixes.
    """

    provider = dict(provider_meta or {})
    raw_channels = _as_channel_list(provider.get("channels"))
    projected_source = str(provider.get("channelsSource") or provider.get("channels_source") or "").strip()
    source = projected_source if projected_source in {"configured", "legacy_projection"} else ("configured" if raw_channels else "legacy_projection")
    if not raw_channels:
        raw_channels = [
            {
                "id": "default",
                "label": "Default",
                "apiStandard": provider.get("api_standard") or provider.get("apiStandard") or "openai",
                "baseUrl": provider.get("base_url") or provider.get("baseUrl") or "",
                "apiVersion": provider.get("api_version") or provider.get("apiVersion") or "",
            }
        ]

    channels: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_channels):
        channel_id = str(raw.get("id") or f"channel-{index + 1}").strip().lower()
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        api_standard = str(
            raw.get("apiStandard")
            or raw.get("api_standard")
            or provider.get("api_standard")
            or provider.get("apiStandard")
            or "openai"
        ).strip().lower()
        wire_protocols = _normalize_wire_protocols(
            raw.get("wireProtocols") or raw.get("wire_protocols"),
            api_standard,
        )
        default_wire_protocol = str(
            raw.get("defaultWireProtocol") or raw.get("default_wire_protocol") or ""
        ).strip()
        if not default_wire_protocol and len(wire_protocols) == 1:
            default_wire_protocol = wire_protocols[0]
        channels.append(
            {
                "id": channel_id,
                "label": str(raw.get("label") or channel_id).strip() or channel_id,
                "apiStandard": api_standard,
                "baseUrl": str(raw.get("baseUrl") or raw.get("base_url") or "").strip().rstrip("/"),
                "apiVersion": str(raw.get("apiVersion") or raw.get("api_version") or "").strip().strip("/"),
                "wireProtocols": wire_protocols,
                "defaultWireProtocol": default_wire_protocol,
                "authContract": _normalize_auth_contract(
                    raw.get("authContract") or raw.get("auth")
                ),
                "credentialSource": "provider",
                "source": source,
            }
        )

    requested_default = str(
        provider.get("defaultChannelId") or provider.get("default_channel_id") or ""
    ).strip().lower()
    available = {item["id"] for item in channels}
    default_channel_id = requested_default if requested_default in available else (channels[0]["id"] if channels else "")
    return {
        "channels": channels,
        "defaultChannelId": default_channel_id,
        "source": source,
    }


def resolve_provider_channel(
    provider_meta: Dict[str, Any] | None,
    *,
    channel_id: Any = "",
    wire_protocol: Any = "",
) -> Dict[str, Any]:
    projection = normalize_provider_channels(provider_meta)
    channels = list(projection.get("channels") or [])
    requested_channel_id = str(channel_id or "").strip().lower()
    requested_wire_protocol = str(wire_protocol or "").strip()

    selected = next((item for item in channels if item.get("id") == requested_channel_id), None)
    selection_source = "model_binding" if selected else ""
    if not selected and requested_channel_id == "default":
        selected = next(
            (item for item in channels if item.get("id") == projection.get("defaultChannelId")),
            channels[0] if channels else {},
        )
        selection_source = "legacy_default_alias"
    if not selected and not requested_channel_id and requested_wire_protocol:
        matches = [item for item in channels if requested_wire_protocol in (item.get("wireProtocols") or [])]
        if len(matches) == 1:
            selected = matches[0]
            selection_source = "wire_protocol_projection"
    if not selected:
        selected = next(
            (item for item in channels if item.get("id") == projection.get("defaultChannelId")),
            channels[0] if channels else {},
        )
        selection_source = selection_source or str(projection.get("source") or "default")

    result = deepcopy(dict(selected or {}))
    result["selectionSource"] = selection_source
    result["availableChannelIds"] = [str(item.get("id") or "") for item in channels]
    result["valid"] = bool(result) and (
        not requested_channel_id
        or result.get("id") == requested_channel_id
        or selection_source == "legacy_default_alias"
    )
    return result


def validate_provider_channels(provider_meta: Dict[str, Any] | None) -> None:
    provider = dict(provider_meta or {})
    if "channels" not in provider:
        return
    raw_channels = _as_channel_list(provider.get("channels"))
    if not raw_channels:
        raise ValueError("Provider channels must contain at least one channel")
    ids: set[str] = set()
    for raw in raw_channels:
        channel_id = str(raw.get("id") or "").strip().lower()
        if not _CHANNEL_ID_RE.fullmatch(channel_id):
            raise ValueError("Provider channel id must use lowercase letters, numbers, '.', '_' or '-'")
        if channel_id in ids:
            raise ValueError(f"Duplicate Provider channel id: {channel_id}")
        ids.add(channel_id)
        base_url = str(raw.get("baseUrl") or raw.get("base_url") or "").strip()
        if not base_url:
            raise ValueError(f"Provider channel '{channel_id}' requires baseUrl")
        api_standard = str(raw.get("apiStandard") or raw.get("api_standard") or "").strip().lower()
        if not api_standard:
            raise ValueError(f"Provider channel '{channel_id}' requires apiStandard")
        protocols = _normalize_wire_protocols(
            raw.get("wireProtocols") or raw.get("wire_protocols"),
            api_standard,
        )
        default_protocol = str(raw.get("defaultWireProtocol") or raw.get("default_wire_protocol") or "").strip()
        if default_protocol and default_protocol not in protocols:
            raise ValueError(
                f"Provider channel '{channel_id}' defaultWireProtocol must be listed in wireProtocols"
            )
        raw_auth = raw.get("authContract") or raw.get("auth")
        if raw_auth is not None:
            if not isinstance(raw_auth, dict):
                raise ValueError(f"Provider channel '{channel_id}' auth contract must be an object")
            unknown_auth = sorted(
                str(key)
                for key in raw_auth
                if str(key) not in {"type", "header", "scheme", "query", "preset", "path"}
            )
            if unknown_auth:
                raise ValueError(
                    f"Provider channel '{channel_id}' auth contract contains unsupported fields"
                )
    default_channel_id = str(
        provider.get("defaultChannelId") or provider.get("default_channel_id") or ""
    ).strip().lower()
    if default_channel_id and default_channel_id not in ids:
        raise ValueError("defaultChannelId must reference a configured Provider channel")


def public_provider_channels(provider_meta: Dict[str, Any] | None) -> Dict[str, Any]:
    projection = normalize_provider_channels(provider_meta)
    return {
        "channels": [
            {key: value for key, value in channel.items() if key not in {"selectionSource", "availableChannelIds", "valid"}}
            for channel in projection.get("channels") or []
        ],
        "defaultChannelId": projection.get("defaultChannelId") or "",
        "channelsSource": projection.get("source") or "",
    }


__all__ = [
    "default_wire_protocols",
    "normalize_provider_channels",
    "public_provider_channels",
    "resolve_provider_channel",
    "validate_provider_channels",
]
