from __future__ import annotations

import os
from typing import Any, Dict


def _truthy_count(payload: Dict[str, Any], keys: list[str]) -> int:
    return sum(1 for key in keys if bool(payload.get(key)))


def _implemented_from_input(payload: Dict[str, Any]) -> bool:
    return bool(payload.get("strategyOrder")) or _truthy_count(
        payload,
        [
            "supportsSendKeys",
            "supportsSendInput",
            "supportsWindowMessage",
            "supportsClipboardText",
            "supportsClipboardFiles",
            "supportsCoordinateTyping",
        ],
    ) > 0


def _implemented_from_accessibility(payload: Dict[str, Any]) -> bool:
    return bool(str(payload.get("primaryBackend") or "").strip()) and str(payload.get("primaryBackend")) != "unsupported"


def _implemented_from_generic(payload: Dict[str, Any], keys: list[str]) -> bool:
    return bool(payload.get("notes")) or _truthy_count(payload, keys) > 0


def _available_from_permission(permission: Dict[str, Any], *keys: str) -> bool:
    blocked_states = {"blocked", "unsupported"}
    statuses = [str(permission.get(key) or "").strip().lower() for key in keys if str(permission.get(key) or "").strip()]
    if not statuses:
        return False
    return any(status not in blocked_states for status in statuses)


def _facet_state(
    *,
    implemented: bool,
    available: bool,
    validation_level: str,
    details: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "implemented": bool(implemented),
        "available": bool(available),
        "validationLevel": str(validation_level),
        "details": dict(details or {}),
    }


def _validation_level(*, platform_name: str, current_platform: str, implemented: bool, available: bool) -> str:
    if not implemented:
        return "not_validated"
    if platform_name == current_platform and available:
        return "real_host"
    return "fixture_only"


def build_platform_capability_matrix(
    *,
    platform_name: str,
    current_platform: str,
    raw_capabilities: Dict[str, Any],
    browser_lane: Dict[str, Any] | None = None,
    app_adapter: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    caps = dict(raw_capabilities or {})
    input_caps = dict(caps.get("input") or {})
    accessibility_caps = dict(caps.get("accessibility") or {})
    window_caps = dict(caps.get("window") or {})
    pointer_caps = dict(caps.get("pointer") or {})
    viewport_caps = dict(caps.get("viewport") or {})
    observation_caps = dict(caps.get("observation") or {})
    verification_caps = dict(caps.get("verification") or {})
    permission_caps = dict(caps.get("permission") or {})
    execution_caps = dict(caps.get("execution") or {})
    browser_lane_caps = dict(browser_lane or {})
    app_adapter_caps = dict(app_adapter or {})

    input_implemented = _implemented_from_input(input_caps)
    input_available = (
        platform_name == current_platform
        and input_implemented
        and _available_from_permission(permission_caps, "inputSynthesisStatus")
    )

    accessibility_implemented = _implemented_from_accessibility(accessibility_caps)
    accessibility_available = (
        platform_name == current_platform
        and accessibility_implemented
        and _available_from_permission(permission_caps, "accessibilityStatus")
    )

    window_implemented = _implemented_from_generic(
        window_caps,
        [
            "supportsFocus",
            "supportsActivate",
            "supportsWindowCandidates",
            "supportsForegroundWindow",
            "supportsRootCaptureRecovery",
        ],
    )
    window_available = platform_name == current_platform and window_implemented

    pointer_implemented = _implemented_from_generic(
        pointer_caps,
        [
            "supportsMove",
            "supportsClick",
            "supportsDoubleClick",
            "supportsRightClick",
            "supportsHover",
            "supportsDrag",
        ],
    )
    pointer_available = platform_name == current_platform and pointer_implemented and input_available

    viewport_implemented = _implemented_from_generic(
        viewport_caps,
        [
            "supportsWheel",
            "supportsPageScroll",
            "supportsScrollbarDrag",
            "supportsEnsureVisible",
        ],
    )
    viewport_available = platform_name == current_platform and viewport_implemented and input_available

    observation_implemented = _implemented_from_generic(
        observation_caps,
        [
            "supportsSceneIdentity",
            "supportsBlockerDetection",
            "supportsGoalStateDetection",
            "supportsKeyframeVisualFallback",
            "frameSequenceSamplingAvailable",
            "frameSequenceSemanticVerificationAvailable",
        ],
    )
    observation_available = platform_name == current_platform and observation_implemented

    verification_implemented = _implemented_from_generic(
        verification_caps,
        [
            "supportsWindowVerification",
            "supportsFocusVerification",
            "supportsTextVerification",
            "supportsFileVerification",
            "supportsViewportVerification",
            "supportsBusinessVerification",
        ],
    )
    verification_available = platform_name == current_platform and verification_implemented

    browser_implemented = bool(
        browser_lane_caps.get("browserLaneImplemented")
        or browser_lane_caps.get("supportsBrowserAutomation")
        or browser_lane_caps.get("browserLaneProvider")
    )
    browser_available = platform_name == current_platform and bool(browser_lane_caps.get("browserLaneAvailable"))

    app_adapter_implemented = bool(app_adapter_caps.get("implemented"))
    app_adapter_available = platform_name == current_platform and bool(app_adapter_caps.get("available"))

    permission_implemented = bool(permission_caps)
    permission_available = platform_name == current_platform and permission_implemented

    matrix = {
        "input": _facet_state(
            implemented=input_implemented,
            available=input_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=input_implemented, available=input_available),
            details=input_caps,
        ),
        "accessibility": _facet_state(
            implemented=accessibility_implemented,
            available=accessibility_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=accessibility_implemented, available=accessibility_available),
            details=accessibility_caps,
        ),
        "window": _facet_state(
            implemented=window_implemented,
            available=window_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=window_implemented, available=window_available),
            details=window_caps,
        ),
        "pointer": _facet_state(
            implemented=pointer_implemented,
            available=pointer_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=pointer_implemented, available=pointer_available),
            details=pointer_caps,
        ),
        "viewport": _facet_state(
            implemented=viewport_implemented,
            available=viewport_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=viewport_implemented, available=viewport_available),
            details=viewport_caps,
        ),
        "observation": _facet_state(
            implemented=observation_implemented,
            available=observation_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=observation_implemented, available=observation_available),
            details=observation_caps,
        ),
        "verification": _facet_state(
            implemented=verification_implemented,
            available=verification_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=verification_implemented, available=verification_available),
            details=verification_caps,
        ),
        "browserAutomation": _facet_state(
            implemented=browser_implemented,
            available=browser_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=browser_implemented, available=browser_available),
            details=browser_lane_caps,
        ),
        "appAdapter": _facet_state(
            implemented=app_adapter_implemented,
            available=app_adapter_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=app_adapter_implemented, available=app_adapter_available),
            details=app_adapter_caps,
        ),
        "permissionsSession": _facet_state(
            implemented=permission_implemented,
            available=permission_available,
            validation_level=_validation_level(platform_name=platform_name, current_platform=current_platform, implemented=permission_implemented, available=permission_available),
            details=permission_caps,
        ),
    }
    return {
        "platform": platform_name,
        "backend": str(caps.get("backend") or ""),
        "hostPlatform": current_platform,
        "validationLevel": "real_host" if platform_name == current_platform else "fixture_only",
        "facets": matrix,
        "execution": execution_caps,
    }


def summarize_current_capability_truth(matrix: Dict[str, Any]) -> Dict[str, Any]:
    facets = dict(matrix.get("facets") or {})
    observation = dict((facets.get("observation") or {}).get("details") or {})
    browser = dict((facets.get("browserAutomation") or {}).get("details") or {})
    execution = dict(matrix.get("execution") or {})
    return {
        "supportsKeyframeVisualFallback": bool(
            observation.get("frameSequenceSamplingAvailable")
            and observation.get("frameSequenceSemanticVerificationAvailable")
        ),
        "frameSequenceSamplingAvailable": bool(observation.get("frameSequenceSamplingAvailable")),
        "frameSequenceSemanticVerificationAvailable": bool(observation.get("frameSequenceSemanticVerificationAvailable")),
        "supportsBrowserAutomation": bool(browser.get("supportsBrowserAutomation")),
        "browserLaneAvailable": bool(browser.get("browserLaneAvailable")),
        "browserLaneProvider": browser.get("browserLaneProvider"),
        "preferredRouteOrder": list(execution.get("preferredRouteOrder") or []),
        "currentPlatform": str(matrix.get("platform") or os.name),
    }


def build_runtime_capability_matrix(
    *,
    current_platform: str,
    platform_capabilities: Dict[str, Dict[str, Any]],
    browser_lane: Dict[str, Any] | None = None,
    app_adapter: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    browser_lane_payload = dict(browser_lane or {})
    app_adapter_payload = dict(app_adapter or {})
    platforms: Dict[str, Dict[str, Any]] = {}
    for platform_name, raw_caps in dict(platform_capabilities or {}).items():
        matrix = build_platform_capability_matrix(
            platform_name=platform_name,
            current_platform=current_platform,
            raw_capabilities=dict(raw_caps or {}),
            browser_lane=browser_lane_payload,
            app_adapter=app_adapter_payload,
        )
        platforms[platform_name] = matrix
    current_matrix = dict(platforms.get(current_platform) or {})
    current_truth = summarize_current_capability_truth(current_matrix) if current_matrix else {}
    return {
        "currentPlatform": current_platform,
        "platforms": platforms,
        "current": current_matrix,
        "truth": current_truth,
    }
