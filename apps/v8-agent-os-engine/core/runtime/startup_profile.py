from __future__ import annotations

import os
from typing import Any

from core.storage import storage


STARTUP_PROFILES = ("minimal", "standard", "desktop")
_PROFILE_ORDER = {name: index for index, name in enumerate(STARTUP_PROFILES)}
_FEATURE_MIN_PROFILE = {
    "core": "minimal",
    "audio": "standard",
    "skills": "minimal",
    "mcp": "standard",
    "extensions": "minimal",
    "cron": "standard",
    "knowledge": "standard",
    "ops": "standard",
    "plugin_host": "standard",
    "network_supervisor": "standard",
    "computer_use": "desktop",
    "desktop_live": "desktop",
    "rpa": "desktop",
}
_DISABLED_REASON_LABELS = {
    "disabled_by_config": "配置关闭",
    "disabled_by_profile": "启动档位未开启",
    "disabled_by_runtime_policy": "runtime policy 已禁用",
    "enabled": "已启用",
}
_RUNTIME_CLUSTERS = {
    "chatruntime": {
        "minimumProfile": "minimal",
        "features": ("core",),
        "minimalSubmode": "full",
    },
    "extensionsruntime": {
        "minimumProfile": "minimal",
        "features": ("skills", "extensions"),
        "minimalSubmode": "lite",
    },
    "memoryruntime": {
        "minimumProfile": "minimal",
        "features": ("knowledge",),
        "minimalSubmode": "lite",
    },
    "autoruntime": {
        "minimumProfile": "minimal",
        "features": ("cron", "ops"),
        "minimalSubmode": "lite",
    },
    "servicecluster": {
        "minimumProfile": "standard",
        "features": ("audio", "mcp", "plugin_host", "network_supervisor"),
        "minimalSubmode": "off",
    },
    "desktopcluster": {
        "minimumProfile": "desktop",
        "features": ("computer_use", "desktop_live", "rpa"),
        "minimalSubmode": "off",
    },
}


def normalize_startup_profile(value: Any) -> str:
    normalized = str(value or "standard").strip().lower()
    if normalized not in _PROFILE_ORDER:
        return "standard"
    return normalized


def get_configured_startup_profile() -> str:
    try:
        payload = storage.get_runtime_registry_config()
    except Exception:
        payload = {}
    return normalize_startup_profile(payload.get("startupProfile"))


def resolve_startup_profile() -> str:
    env_value = os.getenv("ENGINE_STARTUP_PROFILE")
    if str(env_value or "").strip():
        return normalize_startup_profile(env_value)
    return get_configured_startup_profile()


def profile_at_least(profile: str, minimum: str) -> bool:
    normalized_profile = normalize_startup_profile(profile)
    normalized_minimum = normalize_startup_profile(minimum)
    return _PROFILE_ORDER[normalized_profile] >= _PROFILE_ORDER[normalized_minimum]


def startup_feature_enabled(feature: str, profile: str | None = None) -> bool:
    normalized_profile = normalize_startup_profile(profile or resolve_startup_profile())
    minimum = normalize_startup_profile(_FEATURE_MIN_PROFILE.get(str(feature or "").strip(), "standard"))
    return profile_at_least(normalized_profile, minimum)


def runtime_policy_enabled(kind: str | None) -> bool:
    normalized_kind = str(kind or "").strip()
    if not normalized_kind:
        return True
    try:
        from erc.kernel import erc_kernel

        policy = erc_kernel.get_runtime_registry().get_policy(normalized_kind)
        return bool(policy.enabled)
    except Exception:
        return True


def service_enabled(
    feature: str,
    *,
    profile: str | None = None,
    runtime_kind: str | None = None,
    config_enabled: bool = True,
) -> bool:
    if not config_enabled:
        return False
    if not startup_feature_enabled(feature, profile):
        return False
    if runtime_kind is not None and not runtime_policy_enabled(runtime_kind):
        return False
    return True


def service_state(
    feature: str,
    *,
    profile: str | None = None,
    runtime_kind: str | None = None,
    config_enabled: bool = True,
) -> dict[str, Any]:
    normalized_profile = normalize_startup_profile(profile or resolve_startup_profile())
    minimum_profile = normalize_startup_profile(_FEATURE_MIN_PROFILE.get(str(feature or "").strip(), "standard"))
    policy_enabled = True if runtime_kind is None else runtime_policy_enabled(runtime_kind)

    if not config_enabled:
        reason = "disabled_by_config"
    elif not profile_at_least(normalized_profile, minimum_profile):
        reason = "disabled_by_profile"
    elif runtime_kind is not None and not policy_enabled:
        reason = "disabled_by_runtime_policy"
    else:
        reason = "enabled"

    return {
        "feature": str(feature or "").strip(),
        "enabled": reason == "enabled",
        "reason": reason,
        "reasonLabel": _DISABLED_REASON_LABELS.get(reason, reason),
        "profile": normalized_profile,
        "minimumProfile": minimum_profile,
        "runtimeKind": str(runtime_kind or "").strip() or None,
        "configEnabled": bool(config_enabled),
        "policyEnabled": bool(policy_enabled),
    }


def startup_bundle_summary(profile: str | None = None) -> dict[str, bool]:
    normalized_profile = normalize_startup_profile(profile or resolve_startup_profile())
    return {
        "audio": startup_feature_enabled("audio", normalized_profile),
        "skills": startup_feature_enabled("skills", normalized_profile),
        "mcp": startup_feature_enabled("mcp", normalized_profile),
        "extensions": startup_feature_enabled("extensions", normalized_profile),
        "cron": startup_feature_enabled("cron", normalized_profile),
        "knowledge": startup_feature_enabled("knowledge", normalized_profile),
        "ops": startup_feature_enabled("ops", normalized_profile),
        "pluginHost": startup_feature_enabled("plugin_host", normalized_profile),
        "networkSupervisor": startup_feature_enabled("network_supervisor", normalized_profile),
        "computerUse": startup_feature_enabled("computer_use", normalized_profile),
        "desktopLive": startup_feature_enabled("desktop_live", normalized_profile),
        "rpa": startup_feature_enabled("rpa", normalized_profile),
    }


def startup_bundle_diagnostics(profile: str | None = None) -> dict[str, dict[str, Any]]:
    normalized_profile = normalize_startup_profile(profile or resolve_startup_profile())
    return {
        "audio": service_state("audio", profile=normalized_profile),
        "skills": service_state("skills", profile=normalized_profile),
        "mcp": service_state("mcp", profile=normalized_profile),
        "extensions": service_state("extensions", profile=normalized_profile),
        "cron": service_state("cron", profile=normalized_profile),
        "knowledge": service_state("knowledge", profile=normalized_profile),
        "ops": service_state("ops", profile=normalized_profile),
        "plugin_host": service_state("plugin_host", profile=normalized_profile),
        "network_supervisor": service_state("network_supervisor", profile=normalized_profile),
        "computer_use": service_state("computer_use", profile=normalized_profile),
        "desktop_live": service_state("desktop_live", profile=normalized_profile),
        "rpa": service_state("rpa", profile=normalized_profile),
    }


def runtime_cluster_summary(profile: str | None = None) -> dict[str, bool]:
    normalized_profile = normalize_startup_profile(profile or resolve_startup_profile())
    return {
        cluster_name: profile_at_least(normalized_profile, cluster_config["minimumProfile"])
        for cluster_name, cluster_config in _RUNTIME_CLUSTERS.items()
    }


def runtime_submode_summary(profile: str | None = None) -> dict[str, str]:
    normalized_profile = normalize_startup_profile(profile or resolve_startup_profile())
    submodes: dict[str, str] = {}
    for cluster_name, cluster_config in _RUNTIME_CLUSTERS.items():
        minimum_profile = normalize_startup_profile(cluster_config["minimumProfile"])
        if not profile_at_least(normalized_profile, minimum_profile):
            submodes[cluster_name] = "off"
            continue
        if normalized_profile == "minimal":
            submodes[cluster_name] = str(cluster_config.get("minimalSubmode") or "lite")
        else:
            submodes[cluster_name] = "full"
    return submodes


def disabled_reason_summary(profile: str | None = None) -> dict[str, dict[str, Any]]:
    return startup_bundle_diagnostics(profile)
