from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except Exception:  # pragma: no cover - packaging is expected but keep fallback
    SpecifierSet = None  # type: ignore[assignment]
    Version = None  # type: ignore[assignment]


PLUGIN_HOST_VERSION = str(os.getenv("V8_AGENT_OS_PLUGIN_HOST_VERSION") or "1.0.0").strip() or "1.0.0"


def _evaluate_compatibility(range_expr: str | None) -> dict[str, Any]:
    normalized = str(range_expr or "").strip()
    if not normalized:
        return {"compatible": True, "hostVersion": PLUGIN_HOST_VERSION, "reason": None}
    if normalized.startswith("^"):
        required = normalized[1:]
        host_major = PLUGIN_HOST_VERSION.split(".", 1)[0]
        req_major = required.split(".", 1)[0]
        compatible = bool(required) and host_major == req_major
        return {
            "compatible": compatible,
            "hostVersion": PLUGIN_HOST_VERSION,
            "reason": None if compatible else f"宿主版本 {PLUGIN_HOST_VERSION} 不满足 {normalized}",
        }
    if normalized.startswith("~"):
        required = normalized[1:]
        host_parts = PLUGIN_HOST_VERSION.split(".")
        req_parts = required.split(".")
        compatible = host_parts[:2] == req_parts[:2]
        return {
            "compatible": compatible,
            "hostVersion": PLUGIN_HOST_VERSION,
            "reason": None if compatible else f"宿主版本 {PLUGIN_HOST_VERSION} 不满足 {normalized}",
        }
    if SpecifierSet is not None and Version is not None:
        try:
            spec = SpecifierSet(normalized)
            compatible = spec.contains(Version(PLUGIN_HOST_VERSION), prereleases=True)
            return {
                "compatible": compatible,
                "hostVersion": PLUGIN_HOST_VERSION,
                "reason": None if compatible else f"宿主版本 {PLUGIN_HOST_VERSION} 不满足 {normalized}",
            }
        except Exception:
            pass
    compatible = PLUGIN_HOST_VERSION == normalized
    return {
        "compatible": compatible,
        "hostVersion": PLUGIN_HOST_VERSION,
        "reason": None if compatible else f"宿主版本 {PLUGIN_HOST_VERSION} 不满足 {normalized}",
    }


def build_unavailable_reasons(plugin: dict[str, Any], latest_job: dict[str, Any] | None = None) -> list[str]:
    reasons: list[str] = []
    install_path = Path(str(plugin.get("installPath") or ""))
    if not install_path.exists():
        reasons.append("稳定安装路径不存在，宿主无法从正式插件根加载该插件。")

    compatibility = dict(plugin.get("compatibilitySurface") or {})
    if compatibility and not compatibility.get("compatible", True):
        reasons.append(str(compatibility.get("reason") or "当前宿主版本与插件声明范围不兼容。"))

    setup_state = str(plugin.get("setupState") or "").strip().lower()
    if setup_state == "needs_user_action":
        reasons.append("仍需扫码、打开链接或完成额外 onboarding。")
    elif setup_state == "failed":
        reasons.append("最近一次安装或接入流程失败。")
    elif setup_state not in {"onboarded", "active"}:
        setup_surface = dict(plugin.get("setupSurface") or {})
        if setup_surface.get("requiresWizard") or setup_surface.get("requiresConfiguration"):
            reasons.append("插件已安装，但还未完成接入向导或配置。")

    if str(plugin.get("activationState") or "").strip().lower() == "disabled":
        reasons.append("插件当前处于手动停用状态。")

    latest_job_payload = dict(latest_job or {})
    user_action = dict(latest_job_payload.get("userAction") or {})
    if user_action.get("requiresUserAction"):
        reasons.append("安装任务仍在等待人工操作完成。")
    if str(latest_job_payload.get("status") or "").strip().lower() == "failed":
        reasons.append("最近一次安装任务失败，请检查任务日志。")

    warnings = [str(item).strip() for item in list(plugin.get("warnings") or []) if str(item).strip()]
    for warning in warnings:
        if warning == "plugin path missing from stable root":
            continue
        reasons.append(warning)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in reasons:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def evaluate_plugin_health(
    plugin: dict[str, Any],
    *,
    latest_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = dict(plugin or {})
    compatibility = _evaluate_compatibility(record.get("compatibleHostVersion"))
    install_path = Path(str(record.get("installPath") or ""))
    setup_state = str(record.get("setupState") or "installed").strip().lower() or "installed"
    activation_state = str(record.get("activationState") or "active").strip().lower() or "active"
    capability_surface = dict(record.get("capabilitySurface") or {})
    requires_more_setup = bool(capability_surface.get("supportsSetupWizard")) or bool(capability_surface.get("configFieldCount"))

    if activation_state == "disabled":
        health_state = "disabled"
        lifecycle_state = "disabled"
    elif not install_path.exists():
        health_state = "missing"
        lifecycle_state = "degraded"
    elif not compatibility.get("compatible", True):
        health_state = "incompatible"
        lifecycle_state = "incompatible"
    elif setup_state == "failed":
        health_state = "failed"
        lifecycle_state = "degraded"
    elif latest_job and dict(latest_job.get("userAction") or {}).get("requiresUserAction"):
        health_state = "needs_user_action"
        lifecycle_state = "degraded"
    elif setup_state == "needs_user_action":
        health_state = "needs_user_action"
        lifecycle_state = "degraded"
    elif setup_state in {"onboarded", "active"}:
        health_state = "healthy"
        lifecycle_state = "active"
    elif requires_more_setup:
        health_state = "setup_pending"
        lifecycle_state = "installed"
    else:
        health_state = "healthy"
        lifecycle_state = "installed"

    warnings = [str(item).strip() for item in list(record.get("warnings") or []) if str(item).strip()]
    if not install_path.exists() and "plugin path missing from stable root" not in warnings:
        warnings.append("plugin path missing from stable root")
    if install_path.exists():
        warnings = [item for item in warnings if item != "plugin path missing from stable root"]

    health_surface = {
        "installPathExists": install_path.exists(),
        "onboardingCompleted": setup_state in {"onboarded", "active"},
        "activationState": activation_state,
        "compatible": bool(compatibility.get("compatible", True)),
        "hostVersion": compatibility.get("hostVersion"),
        "compatibilityReason": compatibility.get("reason"),
    }

    preview_record = dict(record)
    preview_record.update(
        {
            "compatibilitySurface": compatibility,
            "setupState": setup_state,
            "activationState": activation_state,
            "warnings": warnings,
        }
    )
    unavailable_reasons = build_unavailable_reasons(preview_record, latest_job)

    return {
        "healthState": health_state,
        "lifecycleState": lifecycle_state,
        "warnings": warnings,
        "compatibilitySurface": compatibility,
        "healthSurface": health_surface,
        "unavailableReasons": unavailable_reasons,
    }
