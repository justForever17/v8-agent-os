from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from PIL import Image

from core.v8_agent_os_paths import ensure_v8_agent_os_tmp_path
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
from .posix_common import (
    build_observation,
    capture_with_mss,
    hash_file,
    json_command,
    normalize_bounds,
    run_command,
    tool_exists,
)


class MacAXUIDriverError(DesktopDriverError):
    pass


SCREEN_CAPTURE_PERMISSION_BLOCKED = (
    "permission_blocked: macOS 未授予 Screen Recording 权限，已阻止截图。"
)


class MacAXUIDriver:
    platform = "macos"
    backend = "axui"

    def __init__(self) -> None:
        self._element_cache: Dict[str, ComputerUseElement] = {}
        self._window_cache: Dict[int, Dict[str, Any]] = {}
        self._selector_hint_cache: Dict[int, List[Dict[str, Any]]] = {}
        self._selector_metrics: Dict[str, int] = {
            "resolveCalls": 0,
            "resolveFastHits": 0,
            "resolveFailures": 0,
        }

    def is_available(self) -> bool:
        return sys.platform == "darwin"

    def ensure_available(self) -> None:
        if sys.platform != "darwin":
            raise MacAXUIDriverError("Mac AX driver 仅支持 macOS 主机。")
        if not self._packaged_helper_binary_path().is_file() and not tool_exists("swiftc"):
            raise MacAXUIDriverError("当前环境缺少已打包的 macOS AX helper 和 swiftc，无法启动 AX sidecar。")

    def capability_summary(self) -> Dict[str, Any]:
        probe = self._probe()
        accessibility_granted = bool(probe.get("accessibilityGranted"))
        screen_capture_granted = bool(probe.get("screenCaptureGranted"))
        screenshot_available = bool(tool_exists("screencapture") and screen_capture_granted)
        input_available = accessibility_granted
        return DesktopDriverCapabilities(
            platform=self.platform,
            backend=self.backend,
            input=DesktopInputCapabilities(
                strategy_order=["axui_semantic", "cg_event"],
                supports_send_keys=input_available,
                supports_sendinput=False,
                supports_window_message=False,
                supports_clipboard_text=bool(tool_exists("pbcopy") and tool_exists("pbpaste")),
                supports_clipboard_files=False,
                supports_modifier_normalization=True,
                supports_coordinate_typing=False,
                notes=["macOS 输入仅走 AXUIElement 与 CGEvent；当前未实现 Apple Events 自动化。"],
            ),
            accessibility=DesktopAccessibilityCapabilities(
                primary_backend="axui",
                fallback_backends=["cgwindow"],
                supports_window_enumeration=True,
                supports_element_observation=accessibility_granted,
                supports_visual_fallback=screenshot_available,
                supports_foreground_window=True,
                supports_root_capture_recovery=True,
                future_platform_targets=["linux_atspi"],
                notes=["AXUIElement 是 macOS 结构化观察主链；TCC Accessibility 未授权时只保留窗口与截图能力。"],
            ),
            window=DesktopWindowCapabilities(
                supports_focus=True,
                supports_activate=True,
                supports_dialog_detection=False,
                supports_window_candidates=True,
                supports_foreground_window=True,
                supports_root_capture_recovery=True,
                notes=["窗口聚焦通过 NSRunningApplication.activate 与 AXRaise 协同完成。"],
            ),
            pointer=DesktopPointerCapabilities(
                supports_move=input_available,
                supports_click=input_available,
                supports_double_click=input_available,
                supports_right_click=input_available,
                supports_hover=input_available,
                supports_drag=input_available,
                notes=["指针事件通过 CGEvent 合成；未授予 Accessibility 时应返回 blocked。"],
            ),
            viewport=DesktopViewportCapabilities(
                supports_wheel=input_available,
                supports_page_scroll=input_available,
                supports_scrollbar_drag=input_available,
                supports_ensure_visible=False,
                notes=["首版只保证滚轮与分页滚动 common-core primitive。"],
            ),
            observation=DesktopObservationCapabilities(
                supports_scene_identity=True,
                supports_blocker_detection=False,
                supports_goal_state_detection=False,
                supports_keyframe_visual_fallback=screenshot_available,
                notes=["scene identity 主要来自窗口身份与 AX tree，不承诺 Windows 级 dialog heuristics。"],
            ),
            verification=DesktopVerificationCapabilities(
                supports_window_verification=True,
                supports_focus_verification=True,
                supports_text_verification=accessibility_granted,
                supports_file_verification=False,
                supports_viewport_verification=True,
                supports_business_verification=False,
            ),
            execution=DesktopExecutionRouteCapabilities(
                supports_native_command=True,
                supports_semantic_route=accessibility_granted,
                supports_visual_route=screenshot_available,
                supports_coordinate_fallback=input_available,
                preferred_route_order=[
                    "native_command",
                    "structured_accessibility",
                    "visual_locator",
                    "coordinate_fallback",
                    "human_approval",
                ],
                notes=["macOS 默认优先 AXUIElement/CGEvent，visual 与 coordinate 只是降级链。"],
            ),
            permission=DesktopPermissionCapabilities(
                accessibility_status="granted" if accessibility_granted else "blocked",
                automation_status="not_used",
                screenshot_status="granted" if screenshot_available else "blocked",
                input_synthesis_status="granted" if input_available else "blocked",
                portal_capture_status="unsupported",
                portal_input_status="unsupported",
                session_type="aqua",
                compositor="windowserver",
                notes=[
                    "TCC Accessibility 未授权时，点击、输入、拖拽等副作用动作必须 blocked。",
                    "Screen Recording 权限不足时，截图链路可能失败。",
                ],
            ),
        ).as_dict()

    def invalidate_window_cache(self, window_handle: int | None = None) -> None:
        if window_handle in (None, ""):
            self._window_cache.clear()
            return
        try:
            self._window_cache.pop(int(window_handle), None)
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
        bucket.insert(
            0,
            {
                "selector": dict(selector or {}),
                "source": str(source or "runtime_hint").strip() or "runtime_hint",
                "reason": str(reason or "").strip() or None,
                "weight": max(8, min(int(weight), 96)),
                "observedAt": time.time(),
            },
        )
        self._selector_hint_cache[key] = bucket[:16]

    def invalidate_element_cache(self, element_id: str | None = None) -> None:
        if element_id:
            self._element_cache.pop(str(element_id), None)
        else:
            self._element_cache.clear()

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
        backend_name: str = "axui",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        self.ensure_available()
        payload = self._helper_command("list_windows")
        windows = [self._normalize_window(item) for item in list(payload.get("windows") or []) if isinstance(item, dict)]
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
        backend_name: str = "axui",
        timeout_ms: int = 12000,
        poll_ms: int = 250,
    ) -> Dict[str, Any]:
        deadline = time.time() + (max(timeout_ms, 250) / 1000.0)
        while time.time() < deadline:
            windows = self.list_windows(
                title_filter=title_filter,
                title_filters=title_filters,
                class_name=class_name,
                class_names=class_names,
                process_ids=process_ids,
                process_names=process_names,
                backend_name=backend_name,
                limit=1,
            )
            if windows:
                return windows[0]
            time.sleep(max(50, poll_ms) / 1000.0)
        raise MacAXUIDriverError("等待 macOS 窗口超时。")

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
        backend_name: str = "axui",
    ) -> Dict[str, Any]:
        self.ensure_available()
        matched = self.wait_for_window(
            title_filter=window_title,
            title_filters=window_title_candidates,
            class_name=class_name,
            class_names=class_name_candidates,
            process_ids=process_ids,
            process_names=process_names,
            backend_name=backend_name,
            timeout_ms=4000,
            poll_ms=160,
        )
        payload = self._helper_command(
            "focus_window",
            {
                "window_handle": window_handle or matched.get("handle"),
                "window_title": matched.get("title") or window_title,
                "process_id": matched.get("processId"),
                "process_name": matched.get("processName"),
            },
        )
        focused = dict(payload.get("window") or {})
        if not focused:
            raise MacAXUIDriverError("macOS 窗口聚焦失败。")
        return self._normalize_window(focused)

    def foreground_window(self, *, backend_name: str = "axui") -> Dict[str, Any] | None:
        self.ensure_available()
        payload = self._helper_command("foreground_window")
        window = payload.get("window")
        if not isinstance(window, dict):
            return None
        return self._normalize_window(window)

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
        if window_handle not in (None, "") or str(window_title or "").strip():
            try:
                window = self.wait_for_window(title_filter=window_title, timeout_ms=1200, poll_ms=120)
            except Exception:
                window = self.foreground_window()
        snapshot = self._helper_command(
            "ax_snapshot",
            {
                "window_handle": window_handle or (window or {}).get("handle"),
                "window_title": window_title or (window or {}).get("title"),
                "depth_limit": depth_limit,
                "element_limit": element_limit,
            },
            timeout_seconds=18.0,
        )
        normalized_window = self._normalize_window(dict(snapshot.get("window") or window or {}))
        elements = [
            self._normalize_element(item, window_handle=normalized_window.get("handle"))
            for item in list(snapshot.get("elements") or [])
            if isinstance(item, dict)
        ]
        return build_observation(
            platform=self.platform,
            backend=self.backend,
            window=normalized_window,
            elements=elements,
            metadata={
                "accessibilityAvailable": bool(snapshot.get("available")),
                "observationMode": "ax_snapshot" if snapshot.get("available") else "window_only",
                "reason": snapshot.get("reason"),
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
        raise MacAXUIDriverError("等待 macOS 元素超时。")

    def click_element(self, *, double: bool = False, **query: Any) -> ComputerUseElement:
        element = self.wait_for_element(**query)
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
        self._ensure_input_granted()
        if window_handle not in (None, "") or str(window_title or "").strip():
            try:
                self.focus_window(window_title=window_title, window_handle=window_handle)
            except Exception:
                pass
        payload = self._helper_command("click_point", {"point": [int(point[0]), int(point[1])]})
        clicked = self._normalize_point_result(payload, point)
        if double:
            time.sleep(0.06)
            second = self._helper_command("click_point", {"point": [int(point[0]), int(point[1])]})
            clicked["metadata"]["doubleClick"] = True
            clicked["metadata"]["secondClick"] = self._normalize_point_result(second, point)
        return clicked

    def hover_point(
        self,
        *,
        point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._ensure_input_granted()
        payload = self._helper_command("hover_point", {"point": [int(point[0]), int(point[1])]})
        return self._normalize_point_result(payload, point)

    def right_click_point(
        self,
        *,
        point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._ensure_input_granted()
        payload = self._helper_command("right_click_point", {"point": [int(point[0]), int(point[1])]})
        return self._normalize_point_result(payload, point)

    def drag_between_points(
        self,
        *,
        start_point: Sequence[int],
        end_point: Sequence[int],
        window_title: str | None = None,
        window_handle: int | None = None,
        steps: int = 12,
    ) -> Dict[str, Any]:
        self._ensure_input_granted()
        payload = self._helper_command(
            "drag_between_points",
            {
                "start_point": [int(start_point[0]), int(start_point[1])],
                "end_point": [int(end_point[0]), int(end_point[1])],
                "steps": max(4, int(steps)),
            },
        )
        return {
            "startPoint": list(payload.get("startPoint") or [int(start_point[0]), int(start_point[1])]),
            "endPoint": list(payload.get("endPoint") or [int(end_point[0]), int(end_point[1])]),
            "metadata": dict(payload.get("metadata") or {"route": "coordinate_fallback"}),
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
        self._ensure_input_granted()
        if file_paths:
            raise MacAXUIDriverError("macOS 首版 common-core 不支持文件载荷粘贴，请改走文本或后续原生适配。")
        if window_handle not in (None, "") or str(window_title or "").strip():
            try:
                self.focus_window(window_title=window_title, window_handle=window_handle)
            except Exception:
                pass
        click_point = point or (list(point_candidates or [])[0] if point_candidates else None)
        if isinstance(click_point, (list, tuple)) and len(click_point) == 2:
            self.click_point(point=click_point, window_title=window_title, window_handle=window_handle)
            time.sleep(0.05)
        payload = {
            "text": str(text or ""),
            "clear_first": bool(clear_first),
            "press_enter": bool(press_enter),
        }
        result = self._helper_command("type_text", payload, timeout_seconds=max(12.0, min(30.0, len(text) / 8.0 + 6.0)))
        target_window = self.foreground_window() or {}
        return {
            "windowHandle": target_window.get("handle") or window_handle,
            "windowTitle": target_window.get("title") or window_title,
            "clickedPoint": list(click_point) if isinstance(click_point, (list, tuple)) and len(click_point) == 2 else None,
            "metadata": {
                **dict(result.get("metadata") or {}),
                "focusProbeMode": focus_probe_mode,
                "inputStrategy": "cg_event_unicode",
                "filePasteStrategy": file_paste_strategy,
            },
        }

    def hotkey(self, sequence: str, *, window_title: str | None = None, window_handle: int | None = None) -> Dict[str, Any]:
        self._ensure_input_granted()
        if window_handle not in (None, "") or str(window_title or "").strip():
            try:
                self.focus_window(window_title=window_title, window_handle=window_handle)
            except Exception:
                pass
        for combo in self._parse_hotkey_sequence(sequence):
            self._helper_command("hotkey", {"key": combo["key"], "modifiers": combo["modifiers"]})
            time.sleep(0.03)
        foreground = self.foreground_window() or {}
        return {
            "windowHandle": foreground.get("handle") or window_handle,
            "windowTitle": foreground.get("title") or window_title,
            "metadata": {"sequence": sequence, "route": "structured_accessibility"},
        }

    def read_selected_text_via_clipboard(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._ensure_input_granted()
        self.hotkey("^c", window_title=window_title, window_handle=window_handle)
        text = ""
        if tool_exists("pbpaste"):
            completed = run_command(["pbpaste"], check=False, timeout_seconds=3.0)
            text = str(completed.stdout or "")
        return {"text": text, "metadata": {"route": "structured_accessibility"}}

    def scroll(
        self,
        *,
        amount: int,
        point: Sequence[int] | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        self._ensure_input_granted()
        if isinstance(point, (list, tuple)) and len(point) == 2:
            try:
                self.hover_point(point=point, window_title=window_title, window_handle=window_handle)
            except Exception:
                pass
        payload = self._helper_command("scroll", {"delta": int(amount)})
        return {"amount": int(amount), "metadata": dict(payload.get("metadata") or {"viewportStrategy": "scroll_wheel"})}

    def page_scroll(
        self,
        *,
        direction: str,
        count: int,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        sequence = "{PGUP}" if str(direction or "down").strip().lower() in {"up", "page_up", "pgup"} else "{PGDN}"
        repeat = max(1, int(count or 1))
        for _ in range(repeat):
            self.hotkey(sequence, window_title=window_title, window_handle=window_handle)
        return {
            "direction": str(direction or "down").strip().lower() or "down",
            "count": repeat,
            "metadata": {"viewportStrategy": "page_scroll", "route": "structured_accessibility"},
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
        self._ensure_screen_capture_granted()
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if tool_exists("screencapture"):
            full_capture = ensure_v8_agent_os_tmp_path(scope="computer_use") / f"mac_full_{int(time.time() * 1000)}.png"
            run_command(["screencapture", "-x", str(full_capture)], check=False, timeout_seconds=8.0)
            if not full_capture.exists():
                return capture_with_mss(target_path)
            bounds = None
            if element_id and element_id in self._element_cache:
                bounds = list(self._element_cache[element_id].bounds)
            elif window_handle not in (None, "") or str(window_title or "").strip():
                window = self.wait_for_window(title_filter=window_title, timeout_ms=1200, poll_ms=120)
                bounds = normalize_bounds(window.get("bounds"))
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
        return capture_with_mss(target_path)

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

    def _probe(self) -> Dict[str, Any]:
        if sys.platform != "darwin":
            return {"accessibilityGranted": False}
        try:
            return self._helper_command("probe", timeout_seconds=6.0)
        except Exception:
            return {"accessibilityGranted": False}

    def _ensure_input_granted(self) -> None:
        if not bool(self._probe().get("accessibilityGranted")):
            raise MacAXUIDriverError("macOS 未授予 Accessibility 权限，当前无法执行点击、输入或拖拽。")

    def _ensure_screen_capture_granted(self) -> None:
        if not bool(self._probe().get("screenCaptureGranted")):
            raise MacAXUIDriverError(SCREEN_CAPTURE_PERMISSION_BLOCKED)

    def _helper_source_path(self) -> Path:
        return Path(__file__).with_name("mac_ax_helper.swift")

    def _helper_binary_path(self) -> Path:
        return ensure_v8_agent_os_tmp_path(scope="computer_use") / "mac_ax_helper"

    def _packaged_helper_binary_path(self) -> Path:
        machine = str(platform.machine() or "").strip().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"x86_64", "amd64"} else ""
        if not arch:
            return Path()
        return Path(__file__).with_name("bin") / f"macos-{arch}" / "mac_ax_helper"

    def _ensure_helper_binary(self) -> Path:
        self.ensure_available()
        source_path = self._helper_source_path()
        packaged_binary = self._packaged_helper_binary_path()
        if packaged_binary.is_file():
            return packaged_binary
        binary_path = self._helper_binary_path()
        if binary_path.exists() and binary_path.stat().st_mtime >= source_path.stat().st_mtime:
            return binary_path
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        completed = run_command(["swiftc", str(source_path), "-O", "-o", str(binary_path)], check=False, timeout_seconds=40.0)
        if completed.returncode != 0 or not binary_path.exists():
            raise MacAXUIDriverError(f"编译 macOS AX helper 失败：{completed.stderr or completed.stdout or '未知错误'}")
        return binary_path

    def _helper_command(self, command: str, payload: Dict[str, Any] | None = None, *, timeout_seconds: float = 12.0) -> Dict[str, Any]:
        binary = self._ensure_helper_binary()
        result = json_command([str(binary), command], payload or {}, timeout_seconds=timeout_seconds)
        if result.get("error"):
            raise MacAXUIDriverError(str(result.get("error")))
        return result

    def _normalize_window(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": str(payload.get("title") or "").strip(),
            "handle": payload.get("handle"),
            "processId": payload.get("processId"),
            "processName": str(payload.get("processName") or payload.get("ownerName") or "").strip().lower(),
            "bundleIdentifier": str(payload.get("bundleIdentifier") or "").strip(),
            "className": str(payload.get("className") or "").strip() or "AXWindow",
            "controlType": str(payload.get("controlType") or "Window").strip() or "Window",
            "bounds": normalize_bounds(payload.get("bounds")) or [],
            "isVisible": bool(payload.get("isVisible", True)),
            "isEnabled": bool(payload.get("isEnabled", True)),
            "ownerName": str(payload.get("ownerName") or "").strip(),
        }

    def _normalize_element(self, payload: Dict[str, Any], *, window_handle: Any = None) -> ComputerUseElement:
        element = ComputerUseElement(
            element_id=str(payload.get("elementId") or self._synthetic_element_id(payload)),
            backend="macos_axui",
            role=str(payload.get("role") or "unknown").strip() or "unknown",
            name=str(payload.get("name") or "").strip(),
            bounds=list(normalize_bounds(payload.get("bounds")) or []),
            actions=[str(item).strip() for item in list(payload.get("actions") or []) if str(item).strip()],
            confidence=float(payload.get("confidence") or 0.9),
            path=[str(item).strip() for item in list(payload.get("path") or []) if str(item).strip()],
            automation_id=str(payload.get("automationId") or "").strip(),
            class_name=str(payload.get("className") or "").strip(),
            window_handle=int(window_handle or payload.get("windowHandle") or 0) or None,
            metadata=dict(payload.get("metadata") or {}),
        )
        self._element_cache[element.element_id] = element
        return element

    def _synthetic_element_id(self, payload: Dict[str, Any]) -> str:
        return f"macax_{hashlib.md5(str(payload).encode('utf-8')).hexdigest()[:16]}"

    def _element_match_score(self, element: ComputerUseElement, *, name: str | None, name_contains: str | None, target_text: str | None, automation_id: str | None, control_type: str | None, class_name: str | None) -> int:
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
            raise MacAXUIDriverError("目标元素缺少可用 bounds，无法计算点击坐标。")
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

    def _parse_hotkey_sequence(self, sequence: str) -> List[Dict[str, Any]]:
        raw = str(sequence or "").strip()
        if not raw:
            raise MacAXUIDriverError("快捷键序列不能为空。")
        if raw.startswith("{") and raw.endswith("}"):
            return [{"key": raw.strip("{}").lower(), "modifiers": []}]
        modifiers: List[str] = []
        index = 0
        while index < len(raw) and raw[index] in {"^", "%", "+", "#"}:
            token = raw[index]
            modifiers.append("command" if token in {"^", "#"} else "option" if token == "%" else "shift")
            index += 1
        key = raw[index:]
        if key.startswith("{") and key.endswith("}"):
            key = key.strip("{}").lower()
        return [{"key": key.lower(), "modifiers": modifiers}]

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
