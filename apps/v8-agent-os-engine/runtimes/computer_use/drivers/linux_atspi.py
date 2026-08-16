from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from PIL import Image

from runtimes.computer_use.types import ComputerUseElement, ComputerUseObservation
from runtimes.computer_use.window_scene import window_title_match_score

from .contracts import (
    DesktopAccessibilityCapabilities,
    DesktopDriverCapabilities,
    DesktopDriverError,
    DesktopExecutionRouteCapabilities,
    DesktopInputCapabilities,
    DesktopObservationCapabilities,
    DesktopPermissionCapabilities,
    DesktopPointerCapabilities,
    DesktopVerificationCapabilities,
    DesktopViewportCapabilities,
    DesktopWindowCapabilities,
)
from .posix_common import build_observation, capture_with_mss, hash_file, normalize_bounds, run_command, tool_exists

try:
    import pyatspi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pyatspi = None


class LinuxATSPIError(DesktopDriverError):
    pass


class LinuxATSPIADriver:
    platform = "linux"
    backend = "atspi"

    def __init__(self) -> None:
        self._element_cache: Dict[str, ComputerUseElement] = {}
        self._accessible_cache: Dict[str, Any] = {}
        self._window_cache: Dict[int, Dict[str, Any]] = {}
        self._window_nodes: Dict[int, Any] = {}
        self._selector_hint_cache: Dict[int, List[Dict[str, Any]]] = {}
        self._selector_metrics: Dict[str, int] = {
            "resolveCalls": 0,
            "resolveFastHits": 0,
            "resolveFailures": 0,
        }

    def is_available(self) -> bool:
        return sys_platform_linux()

    def ensure_available(self) -> None:
        if not sys_platform_linux():
            raise LinuxATSPIError("Linux ATSPI driver 仅支持 Linux 主机。")

    def capability_summary(self) -> Dict[str, Any]:
        session_type = self._session_type()
        compositor = self._compositor()
        atspi_available = pyatspi is not None
        xdotool_available = tool_exists("xdotool")
        wmctrl_available = tool_exists("wmctrl")
        screenshot_available = bool(
            session_type == "x11"
            and (tool_exists("gnome-screenshot") or self._mss_available())
        )
        semantic_input_available = session_type == "x11" and xdotool_available
        return DesktopDriverCapabilities(
            platform=self.platform,
            backend=self.backend,
            input=DesktopInputCapabilities(
                strategy_order=["atspi_semantic", "xdotool", "command"],
                supports_send_keys=semantic_input_available,
                supports_sendinput=False,
                supports_window_message=False,
                supports_clipboard_text=bool(tool_exists("wl-paste") or tool_exists("xclip") or tool_exists("xsel")),
                supports_clipboard_files=False,
                supports_modifier_normalization=True,
                supports_coordinate_typing=session_type == "x11" and xdotool_available,
                notes=[
                    "Linux 首版输入优先依赖 X11 + xdotool；Wayland 下若无显式输入工具链，将返回 blocked。",
                ],
            ),
            accessibility=DesktopAccessibilityCapabilities(
                primary_backend="atspi",
                fallback_backends=["wmctrl", "xdotool"],
                supports_window_enumeration=atspi_available or wmctrl_available,
                supports_element_observation=atspi_available,
                supports_visual_fallback=screenshot_available,
                supports_foreground_window=xdotool_available or atspi_available,
                supports_root_capture_recovery=screenshot_available,
                future_platform_targets=[],
                notes=[
                    "Linux 语义观察优先走 AT-SPI；X11 工具只作为窗口与输入补偿链。",
                    "Wayland / portal 是一等约束，不能继续按 X11 单一路径假设设计。",
                ],
            ),
            window=DesktopWindowCapabilities(
                supports_focus=session_type == "x11" and (xdotool_available or wmctrl_available),
                supports_activate=session_type == "x11" and (xdotool_available or wmctrl_available),
                supports_dialog_detection=False,
                supports_window_candidates=atspi_available or wmctrl_available,
                supports_foreground_window=xdotool_available or atspi_available,
                supports_root_capture_recovery=screenshot_available,
            ),
            pointer=DesktopPointerCapabilities(
                supports_move=session_type == "x11" and xdotool_available,
                supports_click=session_type == "x11" and xdotool_available,
                supports_double_click=session_type == "x11" and xdotool_available,
                supports_right_click=session_type == "x11" and xdotool_available,
                supports_hover=session_type == "x11" and xdotool_available,
                supports_drag=session_type == "x11" and xdotool_available,
                notes=["Wayland 首版不默认提供不透明坐标输入；若缺输入工具链应显式 blocked。"],
            ),
            viewport=DesktopViewportCapabilities(
                supports_wheel=session_type == "x11" and xdotool_available,
                supports_page_scroll=session_type == "x11" and xdotool_available,
                supports_scrollbar_drag=session_type == "x11" and xdotool_available,
                supports_ensure_visible=False,
            ),
            observation=DesktopObservationCapabilities(
                supports_scene_identity=True,
                supports_blocker_detection=False,
                supports_goal_state_detection=False,
                supports_keyframe_visual_fallback=screenshot_available,
            ),
            verification=DesktopVerificationCapabilities(
                supports_window_verification=True,
                supports_focus_verification=True,
                supports_text_verification=atspi_available,
                supports_file_verification=False,
                supports_viewport_verification=True,
                supports_business_verification=False,
            ),
            execution=DesktopExecutionRouteCapabilities(
                supports_native_command=True,
                supports_semantic_route=atspi_available,
                supports_visual_route=screenshot_available,
                supports_coordinate_fallback=session_type == "x11" and xdotool_available,
                preferred_route_order=[
                    "native_command",
                    "structured_accessibility",
                    "visual_locator",
                    "coordinate_fallback",
                    "human_approval",
                ],
                notes=["Linux GUI automation 不应默认从视觉点击开始；X11/Wayland 差异必须压到 adapter 层。"],
            ),
            permission=DesktopPermissionCapabilities(
                accessibility_status="granted" if atspi_available else "unknown",
                automation_status="not_used",
                screenshot_status="granted" if screenshot_available else "blocked",
                input_synthesis_status="granted" if semantic_input_available else "blocked",
                portal_capture_status="unsupported",
                portal_input_status="unsupported",
                session_type=session_type,
                compositor=compositor,
                notes=[
                    "Linux 适配必须把 Wayland / portal 当一等约束。",
                    "若 session/compositor 不允许输入或截图，driver 应返回 blocked/unsupported，而不是偷偷坐标乱试。",
                ],
            ),
        ).as_dict()

    def invalidate_window_cache(self, window_handle: int | None = None) -> None:
        if window_handle in (None, ""):
            self._window_cache.clear()
            self._window_nodes.clear()
            return
        try:
            self._window_cache.pop(int(window_handle), None)
            self._window_nodes.pop(int(window_handle), None)
        except Exception:
            return

    def record_selector_hint(
        self,
        *,
        window_handle: int | None,
        selector: Dict[str, Any],
        source: str = "runtime_hint",
        reason: str | None = None,
        weight: int = 32,
    ) -> None:
        if window_handle in (None, ""):
            return
        key = int(window_handle)
        bucket = list(self._selector_hint_cache.get(key) or [])
        bucket.insert(0, {"selector": dict(selector or {}), "source": source, "reason": reason, "weight": max(8, min(int(weight), 96)), "observedAt": time.time()})
        self._selector_hint_cache[key] = bucket[:16]

    def invalidate_element_cache(self, element_id: str | None = None) -> None:
        if element_id:
            self._element_cache.pop(str(element_id), None)
            self._accessible_cache.pop(str(element_id), None)
        else:
            self._element_cache.clear()
            self._accessible_cache.clear()

    def selector_metrics(self, *, reset: bool = False) -> Dict[str, Any]:
        payload = dict(self._selector_metrics)
        if reset:
            for key in list(self._selector_metrics):
                self._selector_metrics[key] = 0
        return payload

    def list_windows(
        self,
        *,
        title_filter: str | None = None,
        title_filters: Iterable[str] | None = None,
        class_name: str | None = None,
        class_names: Iterable[str] | None = None,
        process_ids: Iterable[int] | None = None,
        process_names: Iterable[str] | None = None,
        backend_name: str = "atspi",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        self.ensure_available()
        windows = self._list_windows_via_atspi() or self._list_windows_via_wmctrl()
        process_filter = {int(item) for item in (process_ids or []) if item not in (None, "")}
        process_name_filter = {str(item).strip().lower() for item in (process_names or []) if str(item).strip()}
        title_tokens = [str(item).strip() for item in (title_filters or []) if str(item).strip()]
        if str(title_filter or "").strip():
            title_tokens.append(str(title_filter).strip())
        class_tokens = [str(item).strip().lower() for item in (class_names or []) if str(item).strip()]
        if str(class_name or "").strip():
            class_tokens.append(str(class_name).strip().lower())
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for window in windows:
            title = str(window.get("title") or "").strip()
            process_name = str(window.get("processName") or "").strip().lower()
            process_id = window.get("processId")
            class_value = str(window.get("className") or "").strip().lower()
            if process_filter and process_id not in process_filter:
                continue
            if process_name_filter and process_name not in process_name_filter:
                continue
            if class_tokens and class_value not in class_tokens:
                continue
            title_score = 0 if not title_tokens else window_title_match_score(title, title_tokens)
            if title_tokens and title_score <= 0:
                continue
            score = 0
            if process_filter and process_id in process_filter:
                score += 42
            if process_name_filter and process_name in process_name_filter:
                score += 28
            if class_tokens and class_value in class_tokens:
                score += 18
            if title_score > 0:
                score += title_score
            if window.get("isVisible"):
                score += 6
            window["matchScore"] = score
            ranked.append((score, window))
            if window.get("handle") not in (None, ""):
                self._window_cache[int(window["handle"])] = dict(window)
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [window for _score, window in ranked[: max(1, limit)]]

    def wait_for_window(
        self,
        *,
        title_filter: str | None = None,
        title_filters: Iterable[str] | None = None,
        class_name: str | None = None,
        class_names: Iterable[str] | None = None,
        process_ids: Iterable[int] | None = None,
        process_names: Iterable[str] | None = None,
        backend_name: str = "atspi",
        timeout_ms: int = 12000,
        poll_ms: int = 250,
    ) -> Dict[str, Any]:
        deadline = time.time() + (max(timeout_ms, 250) / 1000.0)
        while time.time() < deadline:
            windows = self.list_windows(title_filter=title_filter, title_filters=title_filters, class_name=class_name, class_names=class_names, process_ids=process_ids, process_names=process_names, backend_name=backend_name, limit=1)
            if windows:
                return windows[0]
            time.sleep(max(50, poll_ms) / 1000.0)
        raise LinuxATSPIError("等待 Linux 窗口超时。")

    def focus_window(
        self,
        *,
        window_title: str | None = None,
        window_title_candidates: Iterable[str] | None = None,
        window_handle: int | None = None,
        class_name: str | None = None,
        class_name_candidates: Iterable[str] | None = None,
        process_ids: Iterable[int] | None = None,
        process_names: Iterable[str] | None = None,
        backend_name: str = "atspi",
    ) -> Dict[str, Any]:
        self.ensure_available()
        if self._session_type() != "x11":
            raise LinuxATSPIError("当前 Linux session 不是 X11，已阻止不可靠的窗口聚焦。")
        if window_handle not in (None, ""):
            cached = self._window_cache.get(int(window_handle))
            if self._session_type() == "x11" and tool_exists("xdotool"):
                run_command(["xdotool", "windowactivate", str(int(window_handle))], check=False, timeout_seconds=4.0)
                return dict(cached or {"handle": int(window_handle), "title": window_title, "windowHandle": int(window_handle), "windowTitle": window_title})
            if tool_exists("wmctrl"):
                run_command(["wmctrl", "-ia", self._wmctrl_handle(window_handle)], check=False, timeout_seconds=4.0)
                return dict(cached or {"handle": int(window_handle), "title": window_title, "windowHandle": int(window_handle), "windowTitle": window_title})
        window = self.wait_for_window(title_filter=window_title, title_filters=window_title_candidates, class_name=class_name, class_names=class_name_candidates, process_ids=process_ids, process_names=process_names, backend_name=backend_name, timeout_ms=4000, poll_ms=180)
        if self._session_type() == "x11" and tool_exists("xdotool") and window.get("handle") not in (None, ""):
            run_command(["xdotool", "windowactivate", str(int(window["handle"]))], check=False, timeout_seconds=4.0)
            return window
        if tool_exists("wmctrl") and window.get("handle") not in (None, ""):
            run_command(["wmctrl", "-ia", self._wmctrl_handle(window["handle"])], check=False, timeout_seconds=4.0)
            return window
        if self._focus_via_atspi(window):
            return window
        raise LinuxATSPIError("当前 Linux session 无法可靠聚焦目标窗口。")

    def foreground_window(self, *, backend_name: str = "atspi") -> Dict[str, Any] | None:
        self.ensure_available()
        if self._session_type() == "x11" and tool_exists("xdotool"):
            completed = run_command(["xdotool", "getwindowfocus"], check=False, timeout_seconds=2.5)
            handle = str(completed.stdout or "").strip()
            if handle:
                windows = self.list_windows(limit=200)
                for window in windows:
                    try:
                        if int(window.get("handle") or 0) == int(handle):
                            return window
                    except Exception:
                        continue
        windows = self.list_windows(limit=1)
        return windows[0] if windows else None

    def observe_desktop(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        depth_limit: int = 4,
        element_limit: int = 80,
        use_cache: bool = True,
    ) -> ComputerUseObservation:
        self.ensure_available()
        window = self.foreground_window()
        if window_handle not in (None, ""):
            window = dict(self._window_cache.get(int(window_handle)) or {"handle": int(window_handle), "title": window_title})
        if window_handle not in (None, "") or str(window_title or "").strip():
            try:
                if str(window_title or "").strip():
                    window = self.wait_for_window(title_filter=window_title, timeout_ms=1200, poll_ms=120)
            except Exception:
                window = self.foreground_window()
        normalized_window = dict(window or {})
        elements = self._snapshot_elements(window=normalized_window, depth_limit=depth_limit, element_limit=element_limit)
        return build_observation(
            platform=self.platform,
            backend=self.backend,
            window=normalized_window,
            elements=elements,
            metadata={
                "accessibilityAvailable": pyatspi is not None,
                "observationMode": "atspi_snapshot" if pyatspi is not None else "window_only",
                "sessionType": self._session_type(),
                "compositor": self._compositor(),
            },
        )

    def find_elements(
        self,
        *,
        element_id: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        target_text: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
        depth_limit: int = 6,
        limit: int = 20,
    ) -> List[ComputerUseElement]:
        self._selector_metrics["resolveCalls"] += 1
        if element_id and element_id in self._element_cache:
            return [self._element_cache[element_id]]
        observation = self.observe_desktop(
            window_title=window_title,
            window_handle=window_handle,
            depth_limit=depth_limit,
            element_limit=max(limit * 6, 80),
            use_cache=False,
        )
        ranked: List[tuple[int, ComputerUseElement]] = []
        for element in observation.elements:
            score = self._element_match_score(
                element,
                name=name,
                name_contains=name_contains,
                target_text=target_text,
                automation_id=automation_id,
                control_type=control_type,
                class_name=class_name,
            )
            if score <= 0:
                continue
            ranked.append((score, element))
            self._element_cache[element.element_id] = element
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked:
            self._selector_metrics["resolveFastHits"] += 1
        else:
            self._selector_metrics["resolveFailures"] += 1
        return [element for _score, element in ranked[: max(1, limit)]]

    def wait_for_element(self, timeout_ms: int = 10000, poll_ms: int = 300, **query: Any) -> ComputerUseElement:
        deadline = time.time() + (max(timeout_ms, 100) / 1000.0)
        while time.time() < deadline:
            elements = self.find_elements(limit=1, **query)
            if elements:
                return elements[0]
            time.sleep(max(poll_ms, 50) / 1000.0)
        raise LinuxATSPIError("等待 Linux 元素超时。")

    def click_element(self, *, double: bool = False, **query: Any) -> ComputerUseElement:
        element = self.wait_for_element(**query)
        if not double and self._invoke_accessible_action(element, preferred_names=("click", "press", "activate", "open", "jump")):
            updated = self._with_element_action_metadata(
                element,
                {
                    "windowHandle": query.get("window_handle") or element.window_handle,
                    "windowTitle": query.get("window_title"),
                    "metadata": {"route": "structured_accessibility"},
                },
            )
            self._element_cache[updated.element_id] = updated
            return updated
        payload = self.click_point(
            point=self._element_center(element),
            window_title=query.get("window_title"),
            window_handle=query.get("window_handle") or element.window_handle,
            double=double,
        )
        updated = self._with_element_action_metadata(element, payload)
        self._element_cache[updated.element_id] = updated
        return updated

    def right_click_element(self, **query: Any) -> ComputerUseElement:
        element = self.wait_for_element(**query)
        payload = self.right_click_point(
            point=self._element_center(element),
            window_title=query.get("window_title"),
            window_handle=query.get("window_handle") or element.window_handle,
        )
        updated = self._with_element_action_metadata(element, payload)
        self._element_cache[updated.element_id] = updated
        return updated

    def click_point(
        self,
        *,
        point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
        double: bool = False,
        prefer_sendinput_click: bool = False,
    ) -> Dict[str, Any]:
        _ = prefer_sendinput_click
        self._require_coordinate_input("点击")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        run_command(["xdotool", "mousemove", "--sync", str(int(point[0])), str(int(point[1]))], check=False, timeout_seconds=4.0)
        click_count = 2 if double else 1
        run_command(["xdotool", "click", "--repeat", str(click_count), "1"], check=False, timeout_seconds=4.0)
        return self._normalize_point_result(
            {
                "clickedPoint": [int(point[0]), int(point[1])],
                "metadata": {"route": "coordinate_fallback", "inputBackend": "xdotool", "doubleClick": bool(double)},
            },
            point,
        )

    def hover_point(
        self,
        *,
        point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._require_coordinate_input("悬停")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        run_command(["xdotool", "mousemove", "--sync", str(int(point[0])), str(int(point[1]))], check=False, timeout_seconds=4.0)
        return self._normalize_point_result(
            {
                "clickedPoint": [int(point[0]), int(point[1])],
                "metadata": {"route": "coordinate_fallback", "inputBackend": "xdotool", "hover": True},
            },
            point,
        )

    def right_click_point(
        self,
        *,
        point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._require_coordinate_input("右键")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        run_command(["xdotool", "mousemove", "--sync", str(int(point[0])), str(int(point[1]))], check=False, timeout_seconds=4.0)
        run_command(["xdotool", "click", "3"], check=False, timeout_seconds=4.0)
        return self._normalize_point_result(
            {
                "clickedPoint": [int(point[0]), int(point[1])],
                "metadata": {"route": "coordinate_fallback", "inputBackend": "xdotool", "rightClick": True},
            },
            point,
        )

    def drag_between_points(
        self,
        *,
        start_point: Sequence[int],
        end_point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
        steps: int = 12,
    ) -> Dict[str, Any]:
        _ = steps
        self._require_coordinate_input("拖拽")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        run_command(["xdotool", "mousemove", "--sync", str(int(start_point[0])), str(int(start_point[1]))], check=False, timeout_seconds=4.0)
        run_command(["xdotool", "mousedown", "1"], check=False, timeout_seconds=4.0)
        run_command(["xdotool", "mousemove", "--sync", str(int(end_point[0])), str(int(end_point[1]))], check=False, timeout_seconds=4.0)
        run_command(["xdotool", "mouseup", "1"], check=False, timeout_seconds=4.0)
        return {
            "startPoint": [int(start_point[0]), int(start_point[1])],
            "endPoint": [int(end_point[0]), int(end_point[1])],
            "metadata": {"route": "coordinate_fallback", "inputBackend": "xdotool"},
        }

    def type_text(
        self,
        *,
        text: str,
        file_paths: Sequence[str] | None = None,
        clear_first: bool = False,
        press_enter: bool = False,
        **query: Any,
    ) -> ComputerUseElement:
        element = self.wait_for_element(**query)
        if self._set_element_text(element, text=text, clear_first=clear_first, press_enter=press_enter):
            refreshed = self.wait_for_element(
                element_id=element.element_id,
                window_title=query.get("window_title"),
                window_handle=query.get("window_handle") or element.window_handle,
                name=query.get("name"),
                name_contains=query.get("name_contains"),
                target_text=query.get("target_text"),
                automation_id=query.get("automation_id"),
                control_type=query.get("control_type"),
                class_name=query.get("class_name"),
                timeout_ms=1200,
                poll_ms=120,
            )
            refreshed.metadata = {**dict(refreshed.metadata or {}), "route": "structured_accessibility"}
            self._element_cache[refreshed.element_id] = refreshed
            return refreshed
        self.type_text_in_window(
            text=text,
            file_paths=file_paths,
            window_title=query.get("window_title"),
            window_handle=query.get("window_handle") or element.window_handle,
            point=self._element_center(element),
            clear_first=clear_first,
            press_enter=press_enter,
        )
        return self.wait_for_element(
            element_id=element.element_id,
            window_title=query.get("window_title"),
            window_handle=query.get("window_handle") or element.window_handle,
            name=query.get("name"),
            name_contains=query.get("name_contains"),
            target_text=query.get("target_text"),
            automation_id=query.get("automation_id"),
            control_type=query.get("control_type"),
            class_name=query.get("class_name"),
            timeout_ms=1200,
            poll_ms=120,
        )

    def type_text_in_window(
        self,
        *,
        text: str,
        file_paths: Sequence[str] | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        point: Sequence[int] | None = None,
        point_candidates: Sequence[Sequence[int]] | None = None,
        clear_first: bool = False,
        press_enter: bool = False,
        prefer_sendinput_click: bool = False,
        prefer_sendinput_text: bool = False,
        focus_probe_mode: str | None = None,
        file_paste_strategy: str | None = None,
    ) -> Dict[str, Any]:
        _ = (prefer_sendinput_click, prefer_sendinput_text, focus_probe_mode, file_paste_strategy)
        if file_paths:
            raise LinuxATSPIError("Linux 首版 common-core 不支持文件载荷粘贴，请改走文本或后续原生适配。")
        self._require_coordinate_input("文本输入")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        click_point = point or (list(point_candidates or [])[0] if point_candidates else None)
        if isinstance(click_point, (list, tuple)) and len(click_point) == 2:
            self.click_point(point=click_point, window_title=window_title, window_handle=window_handle)
            time.sleep(0.05)
        if clear_first:
            run_command(["xdotool", "key", "--clearmodifiers", "ctrl+a", "BackSpace"], check=False, timeout_seconds=4.0)
        if str(text or ""):
            run_command(
                ["xdotool", "type", "--delay", "1", str(text or "")],
                check=False,
                timeout_seconds=max(8.0, min(30.0, len(str(text or "")) / 8.0 + 4.0)),
            )
        if press_enter:
            run_command(["xdotool", "key", "--clearmodifiers", "Return"], check=False, timeout_seconds=4.0)
        foreground = self.foreground_window() or {}
        return {
            "windowHandle": foreground.get("handle") or window_handle,
            "windowTitle": foreground.get("title") or window_title,
            "clickedPoint": list(click_point) if isinstance(click_point, (list, tuple)) and len(click_point) == 2 else None,
            "metadata": {
                "route": "coordinate_fallback",
                "inputBackend": "xdotool",
                "clearFirst": bool(clear_first),
                "pressEnter": bool(press_enter),
            },
        }

    def hotkey(self, sequence: str, *, window_title: str | None = None, window_handle: int | None = None) -> Dict[str, Any]:
        self._require_coordinate_input("快捷键")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        for combo in self._parse_hotkey_sequence(sequence):
            key_combo = "+".join([*combo["modifiers"], combo["key"]])
            run_command(["xdotool", "key", "--clearmodifiers", key_combo], check=False, timeout_seconds=4.0)
            time.sleep(0.03)
        foreground = self.foreground_window() or {}
        return {
            "windowHandle": foreground.get("handle") or window_handle,
            "windowTitle": foreground.get("title") or window_title,
            "metadata": {"sequence": sequence, "route": "coordinate_fallback", "inputBackend": "xdotool"},
        }

    def read_selected_text_via_clipboard(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self.hotkey("^c", window_title=window_title, window_handle=window_handle)
        return {"text": self._read_clipboard_text(), "metadata": {"route": "coordinate_fallback", "inputBackend": "xdotool"}}

    def scroll(
        self,
        *,
        amount: int,
        point: Sequence[int] | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._require_coordinate_input("滚动")
        self._maybe_focus_window(window_title=window_title, window_handle=window_handle)
        if isinstance(point, (list, tuple)) and len(point) == 2:
            run_command(["xdotool", "mousemove", "--sync", str(int(point[0])), str(int(point[1]))], check=False, timeout_seconds=4.0)
        button = "4" if int(amount or 0) > 0 else "5"
        repeat = max(1, abs(int(amount or 0)) // 120 or 1)
        run_command(["xdotool", "click", "--repeat", str(repeat), button], check=False, timeout_seconds=4.0)
        return {
            "amount": int(amount),
            "metadata": {"viewportStrategy": "scroll_wheel", "route": "coordinate_fallback", "inputBackend": "xdotool"},
        }

    def page_scroll(
        self,
        *,
        direction: str,
        count: int,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        normalized_direction = str(direction or "down").strip().lower() or "down"
        key_name = "{PGUP}" if normalized_direction in {"up", "page_up", "pgup"} else "{PGDN}"
        repeat = max(1, int(count or 1))
        for _ in range(repeat):
            self.hotkey(key_name, window_title=window_title, window_handle=window_handle)
        return {
            "direction": normalized_direction,
            "count": repeat,
            "metadata": {"viewportStrategy": "page_scroll", "route": "coordinate_fallback", "inputBackend": "xdotool"},
        }

    def capture_screenshot(
        self,
        output_path: str | Path,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        element_id: str | None = None,
    ) -> Dict[str, Any]:
        self.ensure_available()
        if self._session_type() != "x11":
            raise LinuxATSPIError(
                "Wayland 截图必须通过用户授权的 ScreenCast portal；当前 driver 尚未实现该会话，已阻止直接抓屏。"
            )
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        bounds = None
        if element_id and element_id in self._element_cache:
            bounds = list(self._element_cache[element_id].bounds)
        elif window_handle not in (None, "") or str(window_title or "").strip():
            try:
                window = self.wait_for_window(title_filter=window_title, timeout_ms=1200, poll_ms=120)
                bounds = normalize_bounds(window.get("bounds"))
            except Exception:
                bounds = None
        if tool_exists("grim") or tool_exists("gnome-screenshot"):
            full_capture = target_path.with_name(f"{target_path.stem}_full_{int(time.time() * 1000)}.png")
            if tool_exists("grim"):
                run_command(["grim", str(full_capture)], check=False, timeout_seconds=8.0)
            else:
                run_command(["gnome-screenshot", "-f", str(full_capture)], check=False, timeout_seconds=8.0)
            if full_capture.exists():
                if bounds and len(bounds) == 4:
                    with Image.open(full_capture) as image:
                        image.crop((bounds[0], bounds[1], bounds[2], bounds[3])).save(target_path)
                else:
                    shutil.copyfile(full_capture, target_path)
                try:
                    full_capture.unlink(missing_ok=True)
                except Exception:
                    pass
                return {"path": str(target_path), "bounds": bounds, "sha256": hash_file(target_path)}
        return capture_with_mss(target_path, bounds=bounds)

    def verify_action(
        self,
        *,
        action_type: str,
        target: Dict[str, Any],
        text: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        before_observation: Dict[str, Any] | None = None,
        after_observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "actionType": action_type,
            "windowHandle": window_handle or target.get("windowHandle") or target.get("window_handle"),
            "windowTitle": window_title or target.get("windowTitle") or target.get("window_title"),
            "beforeTreeHash": (before_observation or {}).get("treeHash"),
            "afterTreeHash": (after_observation or {}).get("treeHash"),
            "beforeScreenHash": (before_observation or {}).get("screenHash"),
            "afterScreenHash": (after_observation or {}).get("screenHash"),
        }
        if action_type in {"click", "double_click", "right_click", "hover"}:
            changed = self._observation_hash_changed(before_observation, after_observation)
            focused = self._window_matches(after_observation, title=details["windowTitle"], handle=details["windowHandle"])
            if changed:
                return {"passed": True, "status": "verified", "reason": "动作后界面状态已变化。", "details": {**details, "changeObserved": True}, "level": "verified"}
            if focused:
                return {"passed": True, "status": "focus_verified", "reason": "动作后目标窗口仍处于前台。", "details": {**details, "foregroundMatched": True}, "level": "soft_verified"}
            return {"passed": True, "status": "soft_verified_target_only", "reason": "动作已执行，但缺少更强的业务结果证据。", "details": details, "level": "soft_verified"}
        if action_type == "type_text":
            normalized_text = str(text or "").strip()
            if normalized_text and self._observation_contains_text(after_observation, normalized_text):
                return {"passed": True, "status": "text_verified", "reason": "输入后的界面中已出现目标文本。", "details": {**details, "targetTextVisible": True}, "level": "verified"}
            if self._observation_hash_changed(before_observation, after_observation):
                return {"passed": True, "status": "soft_verified_target_only", "reason": "输入动作后界面状态发生变化，但未获得稳定文本回读。", "details": details, "level": "soft_verified"}
            return {"passed": False, "status": "review_required_unconfirmed_input", "reason": "输入动作缺少稳定文本回读或明确界面变化证据。", "details": details, "level": "review_required"}
        if action_type in {"hotkey", "scroll"}:
            if self._observation_hash_changed(before_observation, after_observation):
                return {"passed": True, "status": "verified", "reason": "动作后界面状态发生变化。", "details": details, "level": "verified"}
            return {"passed": False, "status": "soft_verified_target_only" if action_type == "hotkey" else "scroll_no_change", "reason": "动作已执行，但界面状态未提供足够变化证据。", "details": details, "level": "soft_verified" if action_type == "hotkey" else "failed"}
        return {"passed": True, "status": "verified", "reason": "动作已执行。", "details": details, "level": "verified"}

    def _session_type(self) -> str:
        explicit = str(os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
        if explicit:
            return explicit
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"
        if os.environ.get("DISPLAY"):
            return "x11"
        return "unknown"

    def _compositor(self) -> str:
        return (
            str(os.environ.get("XDG_CURRENT_DESKTOP") or "").strip()
            or str(os.environ.get("DESKTOP_SESSION") or "").strip()
            or ("wayland" if self._session_type() == "wayland" else "unknown")
        )

    def _mss_available(self) -> bool:
        try:
            import mss  # type: ignore
            return mss is not None
        except Exception:
            return False

    def _wmctrl_handle(self, handle: Any) -> str:
        try:
            return hex(int(handle))
        except Exception:
            return str(handle or "")

    def _synthetic_window_handle(self, *, title: str, process_name: str) -> int:
        digest = hashlib.md5(f"{process_name}::{title}".encode("utf-8")).hexdigest()[:8]
        return int(digest, 16)

    def _list_windows_via_wmctrl(self) -> List[Dict[str, Any]]:
        if not tool_exists("wmctrl"):
            return []
        completed = run_command(["wmctrl", "-lpGx"], check=False, timeout_seconds=4.0)
        windows: List[Dict[str, Any]] = []
        for raw_line in str(completed.stdout or "").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            try:
                handle = int(parts[0], 16)
                process_id = int(parts[2]) if str(parts[2]).isdigit() else None
                x = int(parts[3])
                y = int(parts[4])
                width = int(parts[5])
                height = int(parts[6])
            except Exception:
                continue
            wm_class = str(parts[7] or "").strip()
            title = str(parts[8] or "").strip()
            process_name = wm_class.split(".")[0] if wm_class else ""
            windows.append(
                {
                    "handle": handle,
                    "title": title,
                    "processName": process_name,
                    "processId": process_id,
                    "className": wm_class.lower(),
                    "bounds": [x, y, x + width, y + height],
                    "isVisible": True,
                }
            )
        return windows

    def _list_windows_via_atspi(self) -> List[Dict[str, Any]]:
        if pyatspi is None:
            return []
        wmctrl_windows = self._list_windows_via_wmctrl()
        windows: List[Dict[str, Any]] = []
        self._window_nodes.clear()
        desktop_count = int(getattr(pyatspi.Registry, "getDesktopCount", lambda: 0)() or 0)
        for desktop_index in range(desktop_count):
            try:
                desktop = pyatspi.Registry.getDesktop(desktop_index)
            except Exception:
                continue
            for app in list(desktop):
                app_name = str(getattr(app, "name", "") or "").strip()
                try:
                    process_id = int(getattr(app, "get_process_id", lambda: 0)() or 0) or None
                except Exception:
                    process_id = None
                for child in list(app):
                    role_name = self._accessible_role_name(child)
                    if role_name not in {"frame", "dialog", "window", "alert", "file chooser"}:
                        continue
                    title = str(getattr(child, "name", "") or "").strip() or app_name
                    bounds = self._accessible_bounds(child)
                    matched = self._match_wmctrl_window(title=title, process_name=app_name, windows=wmctrl_windows)
                    handle = int((matched or {}).get("handle") or self._synthetic_window_handle(title=title, process_name=app_name))
                    window_payload = {
                        "handle": handle,
                        "title": title,
                        "processName": str((matched or {}).get("processName") or app_name).strip(),
                        "processId": (matched or {}).get("processId") or process_id,
                        "className": str((matched or {}).get("className") or app_name).strip().lower(),
                        "bounds": bounds or normalize_bounds((matched or {}).get("bounds")) or [0, 0, 0, 0],
                        "isVisible": self._accessible_is_visible(child),
                    }
                    windows.append(window_payload)
                    self._window_nodes[handle] = child
                    self._window_cache[handle] = dict(window_payload)
        return windows

    def _match_wmctrl_window(self, *, title: str, process_name: str, windows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        title_lower = str(title or "").strip().lower()
        process_lower = str(process_name or "").strip().lower()
        exact_match = None
        fuzzy_match = None
        for item in windows:
            item_title = str(item.get("title") or "").strip().lower()
            item_process = str(item.get("processName") or "").strip().lower()
            if title_lower and item_title == title_lower:
                if process_lower and item_process and process_lower != item_process:
                    continue
                return item
            if title_lower and item_title and (title_lower in item_title or item_title in title_lower):
                fuzzy_match = fuzzy_match or item
            if process_lower and item_process == process_lower:
                exact_match = exact_match or item
        return fuzzy_match or exact_match

    def _snapshot_elements(self, *, window: Dict[str, Any], depth_limit: int, element_limit: int) -> List[ComputerUseElement]:
        if pyatspi is None:
            return []
        root = self._resolve_window_accessible(window)
        if root is None:
            return []
        elements: List[ComputerUseElement] = []
        queue: List[tuple[Any, int, List[str]]] = [(root, 0, [str(window.get("title") or "").strip() or "window"])]
        while queue and len(elements) < max(1, element_limit):
            accessible, depth, path = queue.pop(0)
            if depth > max(0, depth_limit):
                continue
            element = self._normalize_accessible_element(accessible, window_handle=window.get("handle"), path=path)
            if element is not None:
                elements.append(element)
                self._element_cache[element.element_id] = element
                self._accessible_cache[element.element_id] = accessible
            try:
                children = [accessible[index] for index in range(int(getattr(accessible, "childCount", 0) or 0))]
            except Exception:
                children = []
            for child in children:
                child_name = str(getattr(child, "name", "") or getattr(child, "getRoleName", lambda: "")() or "").strip()
                queue.append((child, depth + 1, [*path, child_name or self._accessible_role_name(child) or "node"]))
        return elements

    def _resolve_window_accessible(self, window: Dict[str, Any]) -> Any | None:
        handle = window.get("handle")
        if handle not in (None, ""):
            try:
                return self._window_nodes.get(int(handle))
            except Exception:
                pass
        title = str(window.get("title") or "").strip().lower()
        if not title or pyatspi is None:
            return None
        desktop_count = int(getattr(pyatspi.Registry, "getDesktopCount", lambda: 0)() or 0)
        for desktop_index in range(desktop_count):
            try:
                desktop = pyatspi.Registry.getDesktop(desktop_index)
            except Exception:
                continue
            for app in list(desktop):
                for child in list(app):
                    child_title = str(getattr(child, "name", "") or "").strip().lower()
                    if child_title and (child_title == title or title in child_title or child_title in title):
                        return child
        return None

    def _focus_via_atspi(self, window: Dict[str, Any]) -> bool:
        accessible = self._resolve_window_accessible(window)
        if accessible is None:
            return False
        try:
            component = accessible.queryComponent()
            if hasattr(component, "grabFocus") and component.grabFocus():
                return True
        except Exception:
            pass
        try:
            action = accessible.queryAction()
            for index in range(int(getattr(action, "nActions", 0) or 0)):
                action_name = str(action.getName(index) or "").strip().lower()
                if action_name in {"activate", "raise", "select", "focus"}:
                    return bool(action.doAction(index))
        except Exception:
            pass
        return False

    def _normalize_accessible_element(self, accessible: Any, *, window_handle: Any, path: List[str]) -> ComputerUseElement | None:
        bounds = self._accessible_bounds(accessible)
        if not bounds or len(bounds) != 4 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            return None
        role = self._accessible_role_name(accessible) or "unknown"
        name = str(getattr(accessible, "name", "") or "").strip()
        description = str(getattr(accessible, "description", "") or "").strip()
        value = self._accessible_value(accessible)
        payload = {
            "role": role,
            "name": name or description or role,
            "description": description,
            "value": value,
            "windowHandle": window_handle,
            "path": [item for item in path if str(item or "").strip()],
        }
        element_id = self._synthetic_element_id(payload)
        return ComputerUseElement(
            element_id=element_id,
            backend=self.backend,
            role=role,
            name=name or description or role,
            bounds=list(bounds),
            actions=self._accessible_actions(accessible),
            confidence=1.0,
            path=[item for item in path if str(item or "").strip()],
            automation_id=name or description,
            class_name=role,
            window_handle=int(window_handle or 0) or None,
            metadata={
                "description": description,
                "value": value,
                "isVisible": self._accessible_is_visible(accessible),
                "editable": self._accessible_has_editable_text(accessible),
                "route": "structured_accessibility",
            },
        )

    def _accessible_role_name(self, accessible: Any) -> str:
        try:
            return str(accessible.getRoleName() or "").strip().lower()
        except Exception:
            return ""

    def _accessible_bounds(self, accessible: Any) -> List[int] | None:
        try:
            component = accessible.queryComponent()
            coords_mode = getattr(pyatspi, "XY_SCREEN", 0) if pyatspi is not None else 0
            extents = component.getExtents(coords_mode)
            left = int(getattr(extents, "x", 0))
            top = int(getattr(extents, "y", 0))
            width = int(getattr(extents, "width", 0))
            height = int(getattr(extents, "height", 0))
            if width <= 0 or height <= 0:
                return None
            return [left, top, left + width, top + height]
        except Exception:
            return None

    def _accessible_is_visible(self, accessible: Any) -> bool:
        try:
            state = accessible.getState()
            showing = getattr(pyatspi, "STATE_SHOWING", None)
            visible = getattr(pyatspi, "STATE_VISIBLE", None)
            if showing is not None and state.contains(showing):
                return True
            if visible is not None and state.contains(visible):
                return True
        except Exception:
            pass
        return True

    def _accessible_actions(self, accessible: Any) -> List[str]:
        try:
            action = accessible.queryAction()
        except Exception:
            return []
        names: List[str] = []
        try:
            for index in range(int(getattr(action, "nActions", 0) or 0)):
                action_name = str(action.getName(index) or "").strip().lower()
                if action_name:
                    names.append(action_name)
        except Exception:
            return names
        return names

    def _accessible_value(self, accessible: Any) -> str:
        for query_name in ("queryText", "queryValue"):
            try:
                interface = getattr(accessible, query_name)()
            except Exception:
                continue
            try:
                if query_name == "queryText":
                    return str(interface.getText(0, -1) or "").strip()
                return str(getattr(interface, "currentValue", "") or "").strip()
            except Exception:
                continue
        return ""

    def _accessible_has_editable_text(self, accessible: Any) -> bool:
        try:
            accessible.queryEditableText()
            return True
        except Exception:
            return False

    def _synthetic_element_id(self, payload: Dict[str, Any]) -> str:
        return f"atspi_{hashlib.md5(str(payload).encode('utf-8')).hexdigest()[:16]}"

    def _element_match_score(
        self,
        element: ComputerUseElement,
        *,
        name: str | None,
        name_contains: str | None,
        target_text: str | None,
        automation_id: str | None,
        control_type: str | None,
        class_name: str | None,
    ) -> int:
        score = 0
        if automation_id and element.automation_id:
            if automation_id == element.automation_id:
                score += 120
            else:
                return 0
        if name:
            if name == element.name:
                score += 80
            else:
                return 0
        if name_contains:
            if name_contains.lower() in element.name.lower():
                score += 48
            else:
                return 0
        if target_text:
            haystack = " ".join([element.name, str(element.metadata.get("value") or ""), str(element.metadata.get("description") or "")]).lower()
            if target_text.lower() in haystack:
                score += 56
            else:
                return 0
        if control_type:
            if str(control_type).strip().lower() == element.role.strip().lower():
                score += 28
            else:
                return 0
        if class_name:
            if str(class_name).strip().lower() == element.class_name.strip().lower():
                score += 16
            else:
                return 0
        if element.metadata.get("isVisible", True):
            score += 4
        return score

    def _element_center(self, element: ComputerUseElement) -> List[int]:
        bounds = list(element.bounds or [])
        if len(bounds) != 4:
            raise LinuxATSPIError("目标元素缺少可用 bounds，无法计算点击坐标。")
        return [int((bounds[0] + bounds[2]) / 2), int((bounds[1] + bounds[3]) / 2)]

    def _normalize_point_result(self, payload: Dict[str, Any], fallback_point: Sequence[int]) -> Dict[str, Any]:
        point = list(payload.get("clickedPoint") or [int(fallback_point[0]), int(fallback_point[1])])
        foreground = self.foreground_window() or {}
        return {
            "clickedPoint": point,
            "handle": foreground.get("handle"),
            "title": foreground.get("title"),
            "windowHandle": foreground.get("handle"),
            "windowTitle": foreground.get("title"),
            "role": "CoordinatePoint",
            "metadata": dict(payload.get("metadata") or {"route": "coordinate_fallback"}),
        }

    def _with_element_action_metadata(self, element: ComputerUseElement, payload: Dict[str, Any]) -> ComputerUseElement:
        metadata = dict(element.metadata or {})
        metadata.update({"clickedPoint": payload.get("clickedPoint"), "route": dict(payload.get("metadata") or {}).get("route") or "structured_accessibility", "windowTitle": payload.get("windowTitle"), "windowHandle": payload.get("windowHandle")})
        return ComputerUseElement(element_id=element.element_id, backend=element.backend, role=element.role, name=element.name, bounds=list(element.bounds), actions=list(element.actions), confidence=element.confidence, path=list(element.path), automation_id=element.automation_id, class_name=element.class_name, window_handle=element.window_handle, metadata=metadata)

    def _invoke_accessible_action(self, element: ComputerUseElement, *, preferred_names: Sequence[str]) -> bool:
        accessible = self._accessible_cache.get(element.element_id)
        if accessible is None:
            return False
        try:
            action = accessible.queryAction()
        except Exception:
            return False
        preferred = {str(item).strip().lower() for item in preferred_names if str(item).strip()}
        try:
            for index in range(int(getattr(action, "nActions", 0) or 0)):
                action_name = str(action.getName(index) or "").strip().lower()
                if action_name in preferred or any(token in action_name for token in preferred):
                    return bool(action.doAction(index))
        except Exception:
            return False
        return False

    def _set_element_text(self, element: ComputerUseElement, *, text: str, clear_first: bool, press_enter: bool) -> bool:
        accessible = self._accessible_cache.get(element.element_id)
        if accessible is None:
            return False
        try:
            editable = accessible.queryEditableText()
            _ = clear_first
            editable.setTextContents(str(text or ""))
            if press_enter:
                self.hotkey("{ENTER}", window_handle=element.window_handle)
            return True
        except Exception:
            return False

    def _parse_hotkey_sequence(self, sequence: str) -> List[Dict[str, Any]]:
        raw = str(sequence or "").strip()
        if not raw:
            raise LinuxATSPIError("快捷键序列不能为空。")
        if raw.startswith("{") and raw.endswith("}"):
            return [{"key": self._normalize_xdotool_key(raw.strip("{}").lower()), "modifiers": []}]
        modifiers: List[str] = []
        index = 0
        while index < len(raw) and raw[index] in {"^", "%", "+", "#"}:
            token = raw[index]
            modifiers.append("ctrl" if token == "^" else "alt" if token == "%" else "shift" if token == "+" else "super")
            index += 1
        key = raw[index:]
        if key.startswith("{") and key.endswith("}"):
            key = key.strip("{}").lower()
        return [{"key": self._normalize_xdotool_key(key.lower()), "modifiers": modifiers}]

    def _normalize_xdotool_key(self, key: str) -> str:
        mapping = {
            "pgup": "Prior",
            "page_up": "Prior",
            "pgdn": "Next",
            "page_down": "Next",
            "enter": "Return",
            "return": "Return",
            "esc": "Escape",
            "space": "space",
            "tab": "Tab",
            "backspace": "BackSpace",
            "del": "Delete",
            "delete": "Delete",
        }
        return mapping.get(str(key or "").strip().lower(), key)

    def _observation_hash_changed(self, before_observation: Dict[str, Any] | None, after_observation: Dict[str, Any] | None) -> bool:
        for key in ("treeHash", "screenHash"):
            before_hash = (before_observation or {}).get(key)
            after_hash = (after_observation or {}).get(key)
            if before_hash and after_hash and before_hash != after_hash:
                return True
        return False

    def _window_matches(self, observation: Dict[str, Any] | None, *, title: Any, handle: Any) -> bool:
        if not isinstance(observation, dict):
            return False
        metadata = dict(observation.get("metadata") or {})
        observed_handle = metadata.get("windowHandle") or observation.get("windowHandle")
        observed_title = str(observation.get("windowTitle") or "").strip().lower()
        target_title = str(title or "").strip().lower()
        if handle not in (None, "") and observed_handle not in (None, ""):
            try:
                if int(handle) == int(observed_handle):
                    return True
            except Exception:
                pass
        if target_title and observed_title:
            return target_title in observed_title or observed_title in target_title
        return False

    def _observation_contains_text(self, observation: Dict[str, Any] | None, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized or not isinstance(observation, dict):
            return False
        for element in list(observation.get("elements") or [])[:80]:
            if not isinstance(element, dict):
                continue
            candidates = [element.get("name"), element.get("automationId"), element.get("className"), dict(element.get("metadata") or {}).get("value"), dict(element.get("metadata") or {}).get("description")]
            haystack = " ".join(str(item or "").strip().lower() for item in candidates if str(item or "").strip())
            if normalized in haystack:
                return True
        return False

    def _require_coordinate_input(self, action_label: str) -> None:
        if self._session_type() != "x11" or not tool_exists("xdotool"):
            raise LinuxATSPIError(f"当前 Linux session 无法对 {action_label} 提供受控坐标输入。请优先走 AT-SPI 语义路径，或在 Wayland/portal 受限场景下人工审批。")

    def _maybe_focus_window(self, *, window_title: str | None = None, window_handle: int | None = None) -> None:
        if window_handle in (None, "") and not str(window_title or "").strip():
            return
        try:
            self.focus_window(window_title=window_title, window_handle=window_handle)
        except Exception:
            return

    def _read_clipboard_text(self) -> str:
        if tool_exists("wl-paste"):
            completed = run_command(["wl-paste", "--no-newline"], check=False, timeout_seconds=3.0)
            return str(completed.stdout or "")
        if tool_exists("xclip"):
            completed = run_command(["xclip", "-selection", "clipboard", "-o"], check=False, timeout_seconds=3.0)
            return str(completed.stdout or "")
        if tool_exists("xsel"):
            completed = run_command(["xsel", "--clipboard", "--output"], check=False, timeout_seconds=3.0)
            return str(completed.stdout or "")
        return ""


def sys_platform_linux() -> bool:
    return sys.platform.startswith("linux")
