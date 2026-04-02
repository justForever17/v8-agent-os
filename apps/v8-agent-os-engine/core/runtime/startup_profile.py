from __future__ import annotations

import os
import sys
from typing import Any

from core.storage import storage


INSTALL_PROFILES = ("minimal", "desktop")
LEGACY_STARTUP_PROFILE_ALIASES = {
    "minimal": "minimal",
    "standard": "minimal",
    "desktop": "desktop",
}
KNOWN_RUNTIME_FAMILIES = (
    "chat",
    "memory",
    "extensions",
    "automation",
    "network_supervisor",
    "plugin_host",
    "computer_use",
    "rpa",
    "desktop_live",
)
DEFAULT_RUNTIME_FAMILIES_BY_PROFILE = {
    "minimal": ("chat", "memory", "extensions", "automation", "network_supervisor"),
    "desktop": (
        "chat",
        "memory",
        "extensions",
        "automation",
        "network_supervisor",
        "computer_use",
        "rpa",
        "desktop_live",
    ),
}
_FEATURE_RUNTIME_FAMILY = {
    "core": "chat",
    "audio": "chat",
    "skills": "extensions",
    "mcp": "extensions",
    "extensions": "extensions",
    "cron": "automation",
    "knowledge": "memory",
    "ops": "automation",
    "plugin_host": "plugin_host",
    "network_supervisor": "network_supervisor",
    "computer_use": "computer_use",
    "desktop_live": "desktop_live",
    "rpa": "rpa",
}
_DISABLED_REASON_LABELS = {
    "installed": "已安装",
    "not_installed": "未安装",
    "disabled_by_config": "配置关闭",
    "disabled_by_runtime_policy": "runtime policy 已禁用",
}
_LEGACY_EXTRA_FAMILIES_BY_STARTUP_PROFILE = {
    "standard": ("plugin_host",),
    "desktop": ("plugin_host",),
}
_RUNTIME_CLUSTER_COMPAT_ORDER = (
    ("chatruntime", "chat"),
    ("memoryruntime", "memory"),
    ("extensionsruntime", "extensions"),
    ("autoruntime", "automation"),
    ("networksupervisorruntime", "network_supervisor"),
    ("desktopcluster", "computer_use"),
)


def normalize_install_profile(value: Any) -> str:
    normalized = str(value or "minimal").strip().lower()
    return LEGACY_STARTUP_PROFILE_ALIASES.get(normalized, "minimal")


def normalize_startup_profile(value: Any) -> str:
    return normalize_install_profile(value)


def normalize_install_platform(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"windows", "macos", "linux"}:
        return normalized
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _default_runtime_families_for_profile(profile: str) -> list[str]:
    normalized_profile = normalize_install_profile(profile)
    return list(DEFAULT_RUNTIME_FAMILIES_BY_PROFILE.get(normalized_profile, DEFAULT_RUNTIME_FAMILIES_BY_PROFILE["minimal"]))


def _normalize_runtime_families(items: Any) -> list[str]:
    families: list[str] = []
    for item in list(items or []):
        normalized = str(item or "").strip()
        if not normalized or normalized in families:
            continue
        if normalized not in KNOWN_RUNTIME_FAMILIES:
            continue
        families.append(normalized)
    return families


def _resolve_legacy_runtime_families(*, startup_profile: str, install_profile: str) -> list[str]:
    families = _default_runtime_families_for_profile(install_profile)
    for family in _LEGACY_EXTRA_FAMILIES_BY_STARTUP_PROFILE.get(startup_profile, ()):
        if family not in families:
            families.append(family)
    return families


def get_runtime_registry_state() -> dict[str, Any]:
    try:
        payload = storage.get_runtime_registry_config()
    except Exception:
        payload = {}

    env_install_profile = str(os.getenv("ENGINE_INSTALL_PROFILE") or "").strip()
    env_startup_profile = str(os.getenv("ENGINE_STARTUP_PROFILE") or "").strip()
    install_profile = normalize_install_profile(
        env_install_profile
        or payload.get("installProfile")
        or payload.get("startupProfile")
        or env_startup_profile
        or "minimal"
    )
    install_platform = normalize_install_platform(
        os.getenv("ENGINE_INSTALL_PLATFORM") or payload.get("installPlatform")
    )
    configured_families = _normalize_runtime_families(payload.get("installedRuntimeFamilies"))
    if configured_families:
        installed_runtime_families = configured_families
    else:
        legacy_startup_profile = str(payload.get("startupProfile") or "").strip().lower()
        installed_runtime_families = _resolve_legacy_runtime_families(
            startup_profile=legacy_startup_profile,
            install_profile=install_profile,
        )

    return {
        "version": int(payload.get("version") or 1),
        "installProfile": install_profile,
        "installPlatform": install_platform,
        "installedRuntimeFamilies": installed_runtime_families,
        "bootstrapManaged": bool(payload.get("bootstrapManaged", False)),
        "lastUpgradeAt": str(payload.get("lastUpgradeAt") or "").strip() or None,
        "startupProfile": normalize_install_profile(payload.get("startupProfile") or install_profile),
        "policies": dict(payload.get("policies") or {}),
    }


def get_configured_install_profile() -> str:
    return str(get_runtime_registry_state()["installProfile"])


def get_configured_startup_profile() -> str:
    return get_configured_install_profile()


def resolve_install_profile() -> str:
    return str(get_runtime_registry_state()["installProfile"])


def resolve_startup_profile() -> str:
    return resolve_install_profile()


def resolve_install_platform() -> str:
    return str(get_runtime_registry_state()["installPlatform"])


def installed_runtime_families(profile: str | None = None) -> list[str]:
    state = get_runtime_registry_state()
    normalized_profile = normalize_install_profile(profile or state["installProfile"])
    families = _normalize_runtime_families(state["installedRuntimeFamilies"])
    if normalized_profile != state["installProfile"]:
        return _default_runtime_families_for_profile(normalized_profile)
    return families


def runtime_family_installed(kind: str | None, *, profile: str | None = None) -> bool:
    normalized_kind = str(kind or "").strip()
    if not normalized_kind:
        return True
    return normalized_kind in installed_runtime_families(profile)


def service_enabled(
    feature: str,
    *,
    profile: str | None = None,
    runtime_kind: str | None = None,
    config_enabled: bool = True,
) -> bool:
    if not config_enabled:
        return False
    family = _FEATURE_RUNTIME_FAMILY.get(str(feature or "").strip())
    if family and not runtime_family_installed(family, profile=profile):
        return False
    if runtime_kind is not None and not runtime_policy_enabled(runtime_kind):
        return False
    return True


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


def service_state(
    feature: str,
    *,
    profile: str | None = None,
    runtime_kind: str | None = None,
    config_enabled: bool = True,
) -> dict[str, Any]:
    normalized_profile = normalize_install_profile(profile or resolve_install_profile())
    family = _FEATURE_RUNTIME_FAMILY.get(str(feature or "").strip())
    policy_enabled = True if runtime_kind is None else runtime_policy_enabled(runtime_kind)

    if family and not runtime_family_installed(family, profile=normalized_profile):
        reason = "not_installed"
    elif not config_enabled:
        reason = "disabled_by_config"
    elif runtime_kind is not None and not policy_enabled:
        reason = "disabled_by_runtime_policy"
    else:
        reason = "installed"

    return {
        "feature": str(feature or "").strip(),
        "enabled": reason == "installed",
        "reason": reason,
        "reasonLabel": _DISABLED_REASON_LABELS.get(reason, reason),
        "installProfile": normalized_profile,
        "installPlatform": resolve_install_platform(),
        "runtimeFamily": family,
        "runtimeKind": str(runtime_kind or "").strip() or None,
        "configEnabled": bool(config_enabled),
        "policyEnabled": bool(policy_enabled),
    }


def startup_bundle_summary(profile: str | None = None) -> dict[str, bool]:
    normalized_profile = normalize_install_profile(profile or resolve_install_profile())
    return {
        "audio": service_enabled("audio", profile=normalized_profile),
        "skills": service_enabled("skills", profile=normalized_profile),
        "mcp": service_enabled("mcp", profile=normalized_profile),
        "extensions": service_enabled("extensions", profile=normalized_profile),
        "cron": service_enabled("cron", profile=normalized_profile),
        "knowledge": service_enabled("knowledge", profile=normalized_profile),
        "ops": service_enabled("ops", profile=normalized_profile),
        "pluginHost": service_enabled("plugin_host", profile=normalized_profile),
        "networkSupervisor": service_enabled("network_supervisor", profile=normalized_profile),
        "computerUse": service_enabled("computer_use", profile=normalized_profile),
        "desktopLive": service_enabled("desktop_live", profile=normalized_profile),
        "rpa": service_enabled("rpa", profile=normalized_profile),
    }


def startup_bundle_diagnostics(profile: str | None = None) -> dict[str, dict[str, Any]]:
    normalized_profile = normalize_install_profile(profile or resolve_install_profile())
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
    normalized_profile = normalize_install_profile(profile or resolve_install_profile())
    return {
        cluster_name: runtime_family_installed(family, profile=normalized_profile)
        for cluster_name, family in _RUNTIME_CLUSTER_COMPAT_ORDER
    }


def runtime_submode_summary(profile: str | None = None) -> dict[str, str]:
    normalized_profile = normalize_install_profile(profile or resolve_install_profile())
    return {
        cluster_name: "installed" if runtime_family_installed(family, profile=normalized_profile) else "off"
        for cluster_name, family in _RUNTIME_CLUSTER_COMPAT_ORDER
    }


def disabled_reason_summary(profile: str | None = None) -> dict[str, dict[str, Any]]:
    return startup_bundle_diagnostics(profile)


def build_installation_snapshot() -> dict[str, Any]:
    state = get_runtime_registry_state()
    return {
        "installProfile": state["installProfile"],
        "installPlatform": state["installPlatform"],
        "installedRuntimeFamilies": list(state["installedRuntimeFamilies"]),
        "bootstrapManaged": bool(state["bootstrapManaged"]),
        "lastUpgradeAt": state["lastUpgradeAt"],
    }
