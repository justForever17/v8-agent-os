from __future__ import annotations

import platform
from typing import Any


PROBE_CHECKS = [
    "window_enumeration",
    "foreground_focus",
    "screenshot",
    "click",
    "type_text",
    "clipboard_text",
    "scroll",
    "drag",
    "hotkey",
    "browser_cdp",
    "permission_probe",
]


def build_platform_probe_matrix(
    *,
    current_platform: str | None = None,
    driver_summary: dict[str, Any] | None = None,
    browser_summary: dict[str, Any] | None = None,
    mode: str = "dry_run",
) -> dict[str, Any]:
    host = _normalize_platform(current_platform or platform.system())
    driver = dict(driver_summary or {})
    browser = dict(browser_summary or {})
    platforms = {
        key: _platform_payload(
            platform_key=key,
            current_host=(key == host),
            driver=driver,
            browser=browser,
            mode=mode,
        )
        for key in ("windows", "macos", "linux-x11", "linux-wayland")
    }
    return {
        "version": 1,
        "mode": mode,
        "currentPlatform": host,
        "checks": list(PROBE_CHECKS),
        "platforms": platforms,
        "notes": [
            "无真实 host 时只输出 theory/fixture，不升级为 real_host_passed。",
            "同一 runner 可在真实 macOS/Linux 上执行后升级对应 check 状态。",
        ],
    }


def _normalize_platform(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered.startswith("win"):
        return "windows"
    if lowered in {"darwin", "mac", "macos"}:
        return "macos"
    if "linux" in lowered:
        return "linux-x11"
    return lowered or "unknown"


def _platform_payload(
    *,
    platform_key: str,
    current_host: bool,
    driver: dict[str, Any],
    browser: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    checks = []
    for check in PROBE_CHECKS:
        if current_host:
            status = _current_host_status(check, driver=driver, browser=browser)
        else:
            status = "fixture_backed" if check in {"screenshot", "click", "type_text", "hotkey"} else "theory_aligned"
        checks.append(
            {
                "key": check,
                "status": status,
                "currentHost": current_host,
                "validationMode": mode,
                "blockingReason": None if status not in {"unsupported", "blocked_by_permission"} else "host_or_dependency_unavailable",
            }
        )
    return {
        "currentHost": current_host,
        "statusCounts": {
            status: sum(1 for item in checks if item["status"] == status)
            for status in sorted({item["status"] for item in checks})
        },
        "checks": checks,
    }


def _current_host_status(check: str, *, driver: dict[str, Any], browser: dict[str, Any]) -> str:
    if check == "browser_cdp":
        if browser.get("connected") or browser.get("helperScriptExists"):
            return "real_host_passed" if browser.get("connected") else "theory_aligned"
        return "blocked_by_missing_helper"
    if check == "permission_probe":
        return "theory_aligned"
    if driver.get("available") is False:
        return "unsupported"
    return "real_host_passed" if driver else "theory_aligned"
