from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parent / "tools" / "source_provider_registry.json"


@lru_cache(maxsize=1)
def load_source_provider_registry() -> dict[str, Any]:
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        payload["providers"] = {}
    defaults = payload.get("sourceRouterDefaults")
    if not isinstance(defaults, dict):
        payload["sourceRouterDefaults"] = {}
    return payload


def get_source_provider_capabilities() -> dict[str, dict[str, Any]]:
    providers = load_source_provider_registry().get("providers") or {}
    return deepcopy(providers)


def get_source_router_defaults() -> dict[str, Any]:
    defaults = load_source_provider_registry().get("sourceRouterDefaults") or {}
    return deepcopy(defaults)


def get_source_provider_config_defaults() -> dict[str, dict[str, Any]]:
    defaults: dict[str, dict[str, Any]] = {}
    for provider_id, provider in get_source_provider_capabilities().items():
        if not isinstance(provider, dict):
            continue
        config: dict[str, Any] = {"enabled": bool(provider.get("enabledByDefault", True))}
        auth_env = str(provider.get("authEnv") or "").strip()
        if auth_env:
            config["authEnv"] = auth_env
        base_url = str(provider.get("baseUrl") or "").strip()
        if base_url:
            config["baseUrl"] = base_url
        defaults[str(provider_id)] = config
    return defaults

