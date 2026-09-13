"""Secret-free Network settings for the existing Config Broker transaction owner."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, get_args, get_origin
from urllib.parse import urlsplit

from pydantic import BaseModel

from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig


SECTIONS = frozenset({"enabled", "node", "discovery", "wake", "delegation", "relay", "openaiCompat"})


def settings_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    result = NetworkSupervisorRuntimeConfig.model_validate(config).model_dump(by_alias=True)
    result = {key: value for key, value in result.items() if key in SECTIONS}
    result["node"].pop("peerId", None)
    return result


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Reject typos/identity mutation instead of Pydantic's extra=ignore default."""
    if not isinstance(patch, dict) or not patch or set(patch) - SECTIONS:
        raise ValueError("network_settings_fields_invalid")

    def check(value: dict[str, Any], model: type[BaseModel]) -> None:
        fields = {str(field.alias or name): field for name, field in model.model_fields.items()}
        if set(value) - set(fields):
            raise ValueError("network_settings_fields_invalid")
        for name, item in value.items():
            annotation = fields[name].annotation
            if item is None:
                if type(None) not in get_args(annotation):
                    raise ValueError("network_settings_null_not_allowed")
                continue
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                if not isinstance(item, dict):
                    raise ValueError("network_settings_section_invalid")
                check(item, annotation)
            elif get_origin(annotation) is list and get_args(annotation):
                child = get_args(annotation)[0]
                if isinstance(child, type) and issubclass(child, BaseModel):
                    if not isinstance(item, list) or any(not isinstance(row, dict) for row in item):
                        raise ValueError("network_settings_list_invalid")
                    for row in item:
                        check(row, child)
            if isinstance(item, int) and not isinstance(item, bool) and item < 1:
                raise ValueError("network_settings_positive_limit_required")
    check(patch, NetworkSupervisorRuntimeConfig)
    if int((patch.get("discovery") or {}).get("multicastPort", 19530)) > 65535:
        raise ValueError("network_settings_port_invalid")
    peers = (patch.get("discovery") or {}).get("wanBootstrapPeers")
    if peers is not None and (not isinstance(peers, list) or any(not isinstance(peer, str) or not re.fullmatch(r"peer_[A-Za-z0-9_-]{1,120}", peer) for peer in peers)):
        raise ValueError("network_bootstrap_requires_trusted_peer_ids")
    if "peerId" in (patch.get("node") or {}):
        raise ValueError("network_identity_requires_pairing_owner")
    # URLs are public routing configuration; no credential-bearing URL is accepted.
    def urls(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                urls(item, name)
        elif isinstance(value, list):
            for item in value:
                urls(item, key)
        elif isinstance(value, str) and "url" in key.lower() and value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("network_endpoint_invalid")
    urls(patch)
    return deepcopy(patch)


def apply_patch(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(current)
    def merge(target: dict[str, Any], changes: dict[str, Any]) -> None:
        for key, value in changes.items():
            if isinstance(value, dict):
                target.setdefault(key, {})
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)
    merge(candidate, validate_patch(patch))
    validated = NetworkSupervisorRuntimeConfig.model_validate(candidate).model_dump(by_alias=True)
    # Keep unrelated/future persisted keys and peer identity owned by other services.
    for key in patch:
        candidate[key] = validated[key]
    return candidate
