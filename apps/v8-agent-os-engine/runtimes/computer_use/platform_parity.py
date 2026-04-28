from __future__ import annotations

from typing import Any, Dict


ACTION_CHECKLIST = [
    "permission_probe",
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
    "post_action_verify",
]


PLATFORM_REFERENCES: dict[str, dict[str, Any]] = {
    "windows": {
        "driver": "runtimes/computer_use/drivers/windows_uia.py",
        "expectedDependencies": ["pywinauto", "pywin32", "mss", "Pillow", "SendInput"],
        "permissionProbe": ["accessibility/uia_available", "screenshot_available", "input_synthesis_available"],
        "turiXRefs": ["origin/multi-agent-windows src/windows/actions.py"],
        "notes": ["当前开发主机可真实验证；UIA + Win32 fallback 是 V8OS 主链。"],
    },
    "macos": {
        "driver": "runtimes/computer_use/drivers/mac_ax.py",
        "expectedDependencies": ["AXUIElement", "Quartz", "CGEvent", "Screen Recording permission", "Accessibility permission"],
        "permissionProbe": ["TCC Accessibility", "Screen Recording", "Input Monitoring/Automation"],
        "turiXRefs": ["origin/mac_mcp src/mac/actions.py"],
        "notes": ["无 macOS real-host 时只按 contract 与 fixture 对齐，不宣称成熟。"],
    },
    "linux-x11": {
        "driver": "runtimes/computer_use/drivers/linux_atspi.py",
        "expectedDependencies": ["AT-SPI", "X11", "xdotool", "xclip/xsel", "screenshot backend"],
        "permissionProbe": ["DISPLAY", "AT_SPI_BUS_ADDRESS", "X11 input permission"],
        "turiXRefs": ["origin/multi-agent-linux src/linux/actions.py"],
        "notes": ["X11 下可对齐点击/输入/窗口枚举；仍需真实主机 live matrix。"],
    },
    "linux-wayland": {
        "driver": "runtimes/computer_use/drivers/linux_atspi.py",
        "expectedDependencies": ["AT-SPI", "xdg-desktop-portal", "wlroots/gnome portal capture", "clipboard portal"],
        "permissionProbe": ["WAYLAND_DISPLAY", "desktop portal capture", "compositor input restrictions"],
        "turiXRefs": ["origin/multi-agent-linux src/linux/actions.py"],
        "notes": ["Wayland 需要区分可访问性、截图 portal 与输入受限；不能套用 X11 结论。"],
    },
}


def _status_for_platform(platform_key: str, truth_platform: dict[str, Any] | None) -> str:
    payload = dict(truth_platform or {})
    if payload.get("currentHost"):
        counts = dict(payload.get("statusCounts") or {})
        if counts.get("blocked_by_permission"):
            return "blocked_by_permission"
        if counts.get("real_host_passed"):
            return "real_host_passed"
    counts = dict(payload.get("statusCounts") or {})
    if counts.get("theory_aligned"):
        return "theory_aligned"
    if counts.get("fixture_backed"):
        return "fixture_backed"
    if platform_key in PLATFORM_REFERENCES:
        return "theory_aligned"
    return "unsupported"


def build_platform_parity_matrix(*, current_platform: str, platforms: dict[str, Any]) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for platform_key, reference in PLATFORM_REFERENCES.items():
        truth_platform = dict(platforms.get(platform_key) or {})
        entries[platform_key] = {
            "platform": platform_key,
            "currentHost": bool(truth_platform.get("currentHost")),
            "status": _status_for_platform(platform_key, truth_platform),
            "driverContract": reference["driver"],
            "expectedDependencies": list(reference["expectedDependencies"]),
            "permissionProbe": list(reference["permissionProbe"]),
            "actionChecklist": list(ACTION_CHECKLIST),
            "blockingReasons": [
                gap.get("code")
                for gap in list(truth_platform.get("knownGaps") or [])
                if isinstance(gap, dict) and gap.get("code")
            ],
            "turiXRefs": list(reference["turiXRefs"]),
            "notes": list(reference["notes"]),
        }
    return {
        "version": 1,
        "currentPlatform": current_platform,
        "policy": "real-host facts override fixture/theory; non-host platforms stay theory_aligned or fixture_backed until live validated",
        "actions": list(ACTION_CHECKLIST),
        "platforms": entries,
    }
