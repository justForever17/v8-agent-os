from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from runtimes.computer_use.platform_parity import build_platform_parity_matrix


FACET_LABELS: dict[str, str] = {
    "window": "Window",
    "accessibility": "Accessibility",
    "screenshot": "Screenshot",
    "input": "Input",
    "clipboard": "Clipboard",
    "pointer": "Pointer",
    "scroll": "Scroll",
    "hotkey": "Hotkey",
    "drag": "Drag",
    "visualLocator": "Visual locator",
    "verification": "Verification",
    "browserLane": "Browser lane",
    "permissionProbe": "Permission probe",
}

PORTABLE_CHECKLIST = [
    "take_screenshot",
    "list_windows",
    "foreground_window",
    "focus_window",
    "click",
    "double_click",
    "right_click",
    "drag",
    "scroll",
    "type_text",
    "press_key",
    "hotkey",
    "clipboard_text",
    "clipboard_files",
    "permission_probe",
    "post_action_verify",
]


def screen_wake_policy() -> dict[str, Any]:
    return {
        "enabled": True,
        "triggerSignals": [
            "lock_screen",
            "desktop_wallpaper",
            "blank_fullscreen_wallpaper",
            "app_not_visible_after_launch",
        ],
        "wakeKey": "Space",
        "maxAttemptsPerRun": 1,
        "waitSeconds": 2.5,
        "afterWake": "observe_again_then_continue_or_request_human_attention",
        "credentialBoundary": "if login/password/credential screen is observed, stop and ask the user",
        "highRiskBoundary": "never use coordinate fallback while the capture still looks locked or wallpaper-only",
    }


def _bool(payload: dict[str, Any], key: str) -> bool:
    return bool(payload.get(key))


def _permission_blocked(details: dict[str, Any]) -> bool:
    blocked_tokens = {"blocked", "denied", "unavailable"}
    for key, value in details.items():
        if not str(key).lower().endswith("status"):
            continue
        if str(value or "").strip().lower() in blocked_tokens:
            return True
    return False


def _status_for_facet(
    *,
    implemented: bool,
    available: bool,
    validation_level: str,
    details: dict[str, Any],
    platform_name: str,
    current_platform: str,
) -> str:
    if _permission_blocked(details):
        return "blocked_by_permission"
    if available and platform_name == current_platform:
        return "real_host_passed"
    if not implemented:
        return "unsupported"
    if validation_level == "fixture_only":
        return "theory_aligned"
    return "fixture_backed"


def _facet_from_matrix(
    *,
    key: str,
    source_key: str,
    raw_facets: dict[str, Any],
    platform_name: str,
    current_platform: str,
) -> dict[str, Any]:
    facet = dict(raw_facets.get(source_key) or {})
    details = dict(facet.get("details") or {})
    implemented = bool(facet.get("implemented"))
    available = bool(facet.get("available"))
    validation_level = str(facet.get("validationLevel") or "").strip() or "not_validated"
    return {
        "key": key,
        "label": FACET_LABELS.get(key, key),
        "status": _status_for_facet(
            implemented=implemented,
            available=available,
            validation_level=validation_level,
            details=details,
            platform_name=platform_name,
            current_platform=current_platform,
        ),
        "implemented": implemented,
        "available": available,
        "validationLevel": validation_level,
        "evidence": _compact_evidence(details),
    }


def _derived_facet(
    *,
    key: str,
    source_keys: list[str],
    raw_facets: dict[str, Any],
    platform_name: str,
    current_platform: str,
) -> dict[str, Any]:
    implemented = False
    available = False
    details: dict[str, Any] = {}
    validation_level = "not_validated"
    for source_key in source_keys:
        source = dict(raw_facets.get(source_key) or {})
        source_details = dict(source.get("details") or {})
        implemented = implemented or bool(source.get("implemented"))
        available = available or bool(source.get("available"))
        if source.get("validationLevel"):
            validation_level = str(source.get("validationLevel"))
        details.update(source_details)
    return {
        "key": key,
        "label": FACET_LABELS.get(key, key),
        "status": _status_for_facet(
            implemented=implemented,
            available=available,
            validation_level=validation_level,
            details=details,
            platform_name=platform_name,
            current_platform=current_platform,
        ),
        "implemented": implemented,
        "available": available,
        "validationLevel": validation_level,
        "evidence": _compact_evidence(details),
    }


def _compact_evidence(details: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "primaryBackend",
        "fallbackBackends",
        "strategyOrder",
        "supportsClipboardText",
        "supportsClipboardFiles",
        "supportsSendInput",
        "supportsWheel",
        "supportsDrag",
        "supportsTextVerification",
        "accessibilityStatus",
        "screenshotStatus",
        "inputSynthesisStatus",
        "sessionType",
        "compositor",
        "browserLaneProvider",
        "browserLaneAvailable",
        "helperScriptExists",
        "helperScriptPath",
        "playwrightAvailable",
    ]
    evidence = {key: details.get(key) for key in keys if key in details}
    notes = details.get("notes")
    if isinstance(notes, list) and notes:
        evidence["notes"] = [str(item) for item in notes[:2]]
    return evidence


def _browser_lane_truth(browser_lane: dict[str, Any]) -> dict[str, Any]:
    helper_path = str(browser_lane.get("helperScriptPath") or "").strip()
    helper_exists = bool(browser_lane.get("helperScriptExists"))
    node_available = bool(browser_lane.get("nodeAvailable"))
    playwright_available = bool(browser_lane.get("playwrightAvailable"))
    enabled = bool(browser_lane.get("enabled"))
    connected = bool(browser_lane.get("connected"))
    if not node_available:
        status = "blocked_by_missing_node"
    elif helper_path and not helper_exists:
        status = "blocked_by_missing_helper"
    elif helper_exists and not playwright_available:
        status = "blocked_by_missing_playwright"
    elif enabled and connected:
        status = "real_host_passed"
    elif enabled:
        status = "theory_aligned"
    else:
        status = "unsupported"
    return {
        "status": status,
        "enabled": enabled,
        "provider": browser_lane.get("provider"),
        "nodeAvailable": node_available,
        "helperScriptExists": helper_exists,
        "helperScriptPath": helper_path or None,
        "playwrightAvailable": playwright_available,
        "connected": connected,
        "profileMode": browser_lane.get("profileMode"),
        "profileRoot": browser_lane.get("profileRoot"),
        "defaultUserDataDir": browser_lane.get("defaultUserDataDir"),
        "targetPort": browser_lane.get("targetPort"),
        "reason": (
            "browser_cdp_proxy.mjs missing"
            if status == "blocked_by_missing_helper"
            else "playwright module missing"
            if status == "blocked_by_missing_playwright"
            else None
        ),
    }


def _platform_aliases(platform_name: str, platform_payload: dict[str, Any]) -> list[str]:
    if platform_name != "linux":
        return [platform_name]
    permission = dict(((platform_payload.get("facets") or {}).get("permissionsSession") or {}).get("details") or {})
    session_type = str(permission.get("sessionType") or "").strip().lower()
    if session_type == "wayland":
        return ["linux-wayland", "linux-x11"]
    if session_type == "x11":
        return ["linux-x11", "linux-wayland"]
    return ["linux-x11", "linux-wayland"]


def _platform_truth(
    *,
    platform_name: str,
    platform_payload: dict[str, Any],
    current_platform: str,
) -> dict[str, Any]:
    raw_facets = dict(platform_payload.get("facets") or {})
    facets = [
        _facet_from_matrix(key="window", source_key="window", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="accessibility", source_key="accessibility", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _derived_facet(key="screenshot", source_keys=["observation"], raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="input", source_key="input", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _derived_facet(key="clipboard", source_keys=["input"], raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="pointer", source_key="pointer", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="scroll", source_key="viewport", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _derived_facet(key="hotkey", source_keys=["input"], raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _derived_facet(key="drag", source_keys=["pointer"], raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _derived_facet(key="visualLocator", source_keys=["observation"], raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="verification", source_key="verification", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="browserLane", source_key="browserAutomation", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
        _facet_from_matrix(key="permissionProbe", source_key="permissionsSession", raw_facets=raw_facets, platform_name=platform_name, current_platform=current_platform),
    ]
    status_counts: dict[str, int] = {}
    for facet in facets:
        status = str(facet.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "platform": platform_name,
        "backend": platform_payload.get("backend"),
        "currentHost": platform_name == current_platform,
        "statusCounts": status_counts,
        "facets": facets,
    }


def build_capability_truth(
    *,
    capability_matrix: dict[str, Any],
    browser_lane: dict[str, Any],
    app_catalog_summary: dict[str, Any] | None = None,
    app_adapter_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_platform = str(capability_matrix.get("currentPlatform") or "").strip()
    platforms_payload = dict(capability_matrix.get("platforms") or {})
    platforms: dict[str, Any] = {}
    for platform_name, platform_payload in platforms_payload.items():
        aliases = _platform_aliases(str(platform_name), dict(platform_payload or {}))
        for index, alias in enumerate(aliases):
            payload = _platform_truth(
                platform_name=str(platform_name),
                platform_payload=dict(platform_payload or {}),
                current_platform=current_platform,
            ) | {"displayPlatform": alias}
            if str(platform_name) == "linux":
                payload["currentHost"] = current_platform == "linux" and index == 0
            platforms[alias] = payload
    browser_truth = _browser_lane_truth(dict(browser_lane or {}))
    known_gaps: list[dict[str, Any]] = []
    if browser_truth["status"] == "blocked_by_missing_helper":
        known_gaps.append(
            {
                "code": "browser_cdp_proxy_missing",
                "summary": "Browser lane has a code entrypoint, but the CDP helper script is not present.",
                "impact": "browser/electron/webview tasks must fall back to structured accessibility or visual routes",
            }
        )
    if browser_truth["status"] == "blocked_by_missing_playwright":
        known_gaps.append(
            {
                "code": "browser_playwright_missing",
                "summary": "Browser lane helper exists, but the Node Playwright dependency is not resolvable from the engine helper.",
                "impact": "CDP/DOM browser tasks cannot run until Playwright is installed or exposed to the helper process",
            }
        )
    if "macos" in platforms and not platforms["macos"].get("currentHost"):
        known_gaps.append({"code": "macos_real_host_not_run", "summary": "macOS driver is theory-aligned; real host validation is still required."})
    if "linux-x11" in platforms and not platforms["linux-x11"].get("currentHost"):
        known_gaps.append({"code": "linux_real_host_not_run", "summary": "Linux X11/Wayland parity is documented but needs real host validation."})
    return {
        "version": 1,
        "currentPlatform": current_platform,
        "platforms": platforms,
        "platformParity": build_platform_parity_matrix(
            current_platform=current_platform,
            platforms=platforms,
        ),
        "browserLaneTruth": browser_truth,
        "portableChecklist": list(PORTABLE_CHECKLIST),
        "knownGaps": known_gaps,
        "screenWakePolicy": screen_wake_policy(),
        "evidenceRefs": [
            "runtimes/computer_use/capability_matrix.py",
            "runtimes/computer_use/drivers/contracts.py",
            "runtimes/computer_use/browser_automation.py",
            "runtimes/computer_use/app_profiles.py",
            "runtimes/computer_use/environment_probes.py",
            "runtimes/computer_use/platform_parity.py",
        ],
        "appCatalog": dict(app_catalog_summary or {}),
        "appAdapter": dict(app_adapter_summary or {}),
    }


def helper_script_exists(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).exists()
    except Exception:
        return False
