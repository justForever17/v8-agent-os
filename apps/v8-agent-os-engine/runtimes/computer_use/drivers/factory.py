from __future__ import annotations

import os
import sys
from typing import Any

from .contracts import (
    DesktopAccessibilityCapabilities,
    DesktopControlDriver,
    DesktopDriverError,
    DesktopDriverCapabilities,
    DesktopExecutionRouteCapabilities,
    DesktopInputCapabilities,
    DesktopObservationCapabilities,
    DesktopPermissionCapabilities,
    DesktopPointerCapabilities,
    DesktopVerificationCapabilities,
    DesktopViewportCapabilities,
    DesktopWindowCapabilities,
)
from .windows_uia import WindowsUIADriver


class UnsupportedDesktopDriver:
    backend = "unsupported"
    platform = os.name

    def __init__(self, *, reason: str | None = None) -> None:
        self.reason = str(reason or "当前平台暂未接入桌面控制驱动。").strip()

    def is_available(self) -> bool:
        return False

    def ensure_available(self) -> None:
        raise DesktopDriverError(self.reason)

    def list_windows(self, **_kwargs) -> list[dict[str, Any]]:
        return []

    def observe_desktop(self, **_kwargs):
        self.ensure_available()

    def foreground_window(self, **_kwargs) -> dict[str, Any] | None:
        return None

    def selector_metrics(self) -> dict[str, int]:
        return {}

    def invalidate_window_cache(self, window_handle: int | None = None) -> None:
        _ = window_handle

    def invalidate_element_cache(self, element_id: str | None = None) -> None:
        _ = element_id

    def record_selector_hint(self, **_kwargs) -> None:
        return None

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)

        def _unsupported(*_args, **_kwargs):
            raise DesktopDriverError(self.reason)

        return _unsupported

    def capability_summary(self) -> dict[str, Any]:
        return DesktopDriverCapabilities(
            platform=self.platform,
            backend=self.backend,
            input=DesktopInputCapabilities(
                strategy_order=[],
                notes=[
                    "当前运行环境未接入桌面输入驱动。",
                    "后续平台接入建议：macOS 通过 AXUIElement，Linux 通过 AT-SPI。",
                ],
            ),
            accessibility=DesktopAccessibilityCapabilities(
                primary_backend="unsupported",
                fallback_backends=[],
                supports_window_enumeration=False,
                supports_element_observation=False,
                supports_visual_fallback=False,
                supports_foreground_window=False,
                supports_root_capture_recovery=False,
                future_platform_targets=["macos_axui", "linux_atspi"],
                notes=[self.reason],
            ),
            window=DesktopWindowCapabilities(
                notes=["当前平台没有可用的窗口控制实现。"],
            ),
            pointer=DesktopPointerCapabilities(
                notes=["当前平台没有可用的指针控制实现。"],
            ),
            viewport=DesktopViewportCapabilities(
                notes=["当前平台没有可用的视口控制实现。"],
            ),
            observation=DesktopObservationCapabilities(
                notes=["当前平台没有可用的场景理解适配器。"],
            ),
            verification=DesktopVerificationCapabilities(
                notes=["当前平台没有可用的验证适配器。"],
            ),
            execution=DesktopExecutionRouteCapabilities(
                supports_native_command=False,
                supports_semantic_route=False,
                supports_visual_route=False,
                supports_coordinate_fallback=False,
                preferred_route_order=[],
                notes=["当前平台没有可用的桌面执行路由。"],
            ),
            permission=DesktopPermissionCapabilities(
                accessibility_status="unsupported",
                automation_status="unsupported",
                screenshot_status="unsupported",
                input_synthesis_status="unsupported",
                session_type=os.environ.get("XDG_SESSION_TYPE", "unknown"),
                compositor=os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
                notes=["当前平台没有可用的权限或可用性探针。"],
            ),
        ).as_dict()


def create_desktop_driver() -> DesktopControlDriver:
    if os.name == "nt":
        return WindowsUIADriver()
    try:
        if sys.platform == "darwin":
            from .mac_ax import MacAXUIDriver

            return MacAXUIDriver()
        if sys.platform.startswith("linux"):
            from .linux_atspi import LinuxATSPIADriver

            return LinuxATSPIADriver()
    except Exception as exc:
        return UnsupportedDesktopDriver(reason=f"桌面驱动初始化失败：{exc}")
    return UnsupportedDesktopDriver(reason="Computer Use 当前仅完整支持 Windows；macOS/Linux 兼容层仍在推进中。")
