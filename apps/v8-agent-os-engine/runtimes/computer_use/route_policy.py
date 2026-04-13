from __future__ import annotations

from typing import Any, Dict, List


def _unique(items: List[str]) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def build_platform_route_policy(
    *,
    platform_name: str,
    capability_truth: Dict[str, Any],
) -> Dict[str, Any]:
    preferred: List[str] = ["native_command"]
    browser_available = bool(capability_truth.get("supportsBrowserAutomation"))
    observation_available = bool(capability_truth.get("frameSequenceSamplingAvailable"))
    if browser_available:
        preferred.append("browser_automation")
    preferred.append("structured_accessibility")
    if observation_available:
        preferred.append("visual_locator")
    preferred.append("coordinate_fallback")
    preferred.append("human_approval")
    blocked_conditions = [
        "driver_unavailable",
        "permission_blocked",
    ]
    degraded_conditions = [
        "browser_lane_unavailable",
        "accessibility_partial",
        "observation_partial",
    ]
    manual_approval_conditions = [
        "high_risk_visual_confirmation_required",
        "high_risk_pre_action_confirmation_required",
    ]
    if platform_name == "windows":
        degraded_conditions.append("tray_or_shell_recovery_required")
    if platform_name == "linux":
        blocked_conditions.append("wayland_input_blocked")
    return {
        "platform": platform_name,
        "preferredRouteOrder": _unique(preferred),
        "blockedConditions": _unique(blocked_conditions),
        "degradedConditions": _unique(degraded_conditions),
        "manualApprovalConditions": _unique(manual_approval_conditions),
    }


def decide_execution_route(
    *,
    action_type: str,
    current_platform: str,
    capability_truth: Dict[str, Any],
    control_class: str | None,
    browser_lane_available: bool,
    browser_target_family: str | None,
    browser_lane_reason: str | None,
    has_visual_locator: bool,
    coordinate_fallback: bool,
    human_approval_required: bool,
    existing_route: str | None = None,
) -> Dict[str, Any]:
    allowed_routes = {
        "native_command",
        "browser_automation",
        "structured_accessibility",
        "visual_locator",
        "coordinate_fallback",
        "human_approval",
    }
    route = str(existing_route or "").strip().lower()
    if route not in allowed_routes:
        route = ""
    preferred = list(build_platform_route_policy(platform_name=current_platform, capability_truth=capability_truth).get("preferredRouteOrder") or [])
    normalized_class = str(control_class or "").strip().lower() or None
    route_source = "existing_metadata" if route else "policy_inference"
    if not route:
        if action_type == "open_app":
            route = "native_command"
        elif browser_lane_available and normalized_class in {"browser_host_app", "electron_shell_app"}:
            route = "browser_automation"
        elif browser_lane_available and "browser_automation" in preferred:
            route = "browser_automation"
        elif human_approval_required:
            route = "human_approval"
        elif has_visual_locator:
            route = "visual_locator"
        elif coordinate_fallback:
            route = "coordinate_fallback"
        else:
            route = "structured_accessibility"
    return {
        "route": route,
        "source": route_source,
        "controlClass": normalized_class,
        "browserTargetFamily": browser_target_family,
        "browserLaneReason": browser_lane_reason,
        "browserLaneAvailable": bool(browser_lane_available),
        "preferredRouteOrder": preferred,
        "humanApprovalRequired": bool(human_approval_required),
        "visualLocatorBacked": bool(has_visual_locator),
        "coordinateFallback": bool(coordinate_fallback),
    }
