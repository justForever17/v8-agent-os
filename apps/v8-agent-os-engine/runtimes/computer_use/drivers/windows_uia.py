from __future__ import annotations

import ctypes
import hashlib
import json
import os
import time
from ctypes import wintypes
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psutil

try:  # pragma: no cover - import availability depends on local machine
    from pywinauto import Desktop, mouse
    from pywinauto.keyboard import send_keys
except Exception:  # pragma: no cover - graceful runtime fallback
    Desktop = None
    mouse = None
    send_keys = None

try:  # pragma: no cover - import availability depends on local machine
    import mss
    import mss.tools
except Exception:  # pragma: no cover
    mss = None

from runtimes.computer_use.drivers.contracts import (
    DesktopAccessibilityCapabilities,
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
from runtimes.computer_use.window_scene import (
    choose_best_window_candidate,
    is_shell_surface_window,
    is_suspicious_capture_bounds,
    requires_strict_window_binding,
    window_title_match_score,
    window_satisfies_binding,
)
from runtimes.computer_use.types import ComputerUseElement, ComputerUseObservation
from .windows_sendinput import SendInputClickEngine
from .windows_hotkeys import (
    MANAGED_MODIFIER_VKS,
    ParsedHotkeyStroke,
    VK_MENU,
    analyze_hotkey_support,
)


class WindowsUIADriverError(DesktopDriverError):
    pass


@dataclass(slots=True)
class _CachedQuery:
    query: Dict[str, Any]
    window_handle: Optional[int]
    backend: str = "uia"


@dataclass(slots=True)
class _ObservationCacheEntry:
    observed_at: float
    observation: ComputerUseObservation


@dataclass(slots=True)
class _WindowIndexEntry:
    observed_at: float
    elements: List[ComputerUseElement]
    by_element_id: Dict[str, ComputerUseElement]
    by_automation_id: Dict[str, List[ComputerUseElement]]
    by_name: Dict[str, List[ComputerUseElement]]
    by_role: Dict[str, List[ComputerUseElement]]
    by_class_name: Dict[str, List[ComputerUseElement]]
    by_path_tail: Dict[str, List[ComputerUseElement]]


@dataclass(slots=True)
class _SelectorHintEntry:
    observed_at: float
    selector: Dict[str, Any]
    source: str
    reason: str | None = None
    weight: int = 24


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class WindowsUIADriver:
    backend = "windows_uia"
    platform = "windows"

    def __init__(self) -> None:
        self._desktop_uia = None
        self._desktop_win32 = None
        self._sendinput_click_engine = SendInputClickEngine()
        self._element_cache: Dict[str, _CachedQuery] = {}
        self._window_cache: Dict[str, Tuple[float, Any]] = {}
        self._observation_cache: Dict[str, _ObservationCacheEntry] = {}
        self._window_index_cache: Dict[int, _WindowIndexEntry] = {}
        self._selector_hint_cache: Dict[int, List[_SelectorHintEntry]] = {}
        self._root_cache_ttl_seconds = 1.2
        self._observation_cache_ttl_seconds = 0.8
        self._window_index_ttl_seconds = 1.2
        self._selector_hint_ttl_seconds = 180.0
        self._selector_hint_max_per_window = 12
        self._selector_metrics: Dict[str, int] = {
            "findCalls": 0,
            "findMisses": 0,
            "windowIndexHits": 0,
            "windowIndexMisses": 0,
            "fastQueryHits": 0,
            "observationScanHits": 0,
            "directQueryHits": 0,
            "selectorHintQueries": 0,
            "selectorHintHits": 0,
            "selectorHintBoosts": 0,
            "resolveCalls": 0,
            "resolveCacheHits": 0,
            "resolveFastHits": 0,
            "resolveRecoveryHits": 0,
            "resolveFailures": 0,
            "win32FallbackHits": 0,
            "win32FallbackMisses": 0,
        }

    def is_available(self) -> bool:
        return os.name == "nt" and Desktop is not None and send_keys is not None

    def ensure_available(self) -> None:
        if os.name != "nt":
            raise WindowsUIADriverError("Computer Use Windows driver 仅支持 Windows 主机。")
        if Desktop is None or send_keys is None:
            raise WindowsUIADriverError(
                "Windows UIA driver 缺少依赖 `pywinauto`。请在 engine 虚拟环境安装后重试。"
            )

    def capability_summary(self) -> Dict[str, Any]:
        return DesktopDriverCapabilities(
            platform=self.platform,
            backend=self.backend,
            input=DesktopInputCapabilities(
                strategy_order=["send_keys", "sendinput", "window_message"],
                supports_send_keys=send_keys is not None,
                supports_sendinput=bool(self._sendinput_click_engine.is_available()),
                supports_window_message=True,
                supports_clipboard_text=True,
                supports_clipboard_files=True,
                supports_modifier_normalization=True,
                supports_coordinate_typing=True,
                notes=[
                    "热键优先走 pywinauto.send_keys，失败后退到 SendInput，再退到 window_message。",
                    "SendInput 会在执行前归一化 Ctrl/Shift/Alt/Win 当前按下状态。",
                    "window_message 不处理 Win 键和浏览器/媒体类系统按键。",
                ],
            ),
            accessibility=DesktopAccessibilityCapabilities(
                primary_backend="uia",
                fallback_backends=["win32"],
                supports_window_enumeration=True,
                supports_element_observation=True,
                supports_visual_fallback=mss is not None,
                supports_foreground_window=True,
                supports_root_capture_recovery=True,
                future_platform_targets=["macos_axui", "linux_atspi"],
                notes=[
                    "窗口/元素观察优先走 UIA，必要时回退 win32。",
                    "当前驱动仍为 Windows 专用，但 runtime 已可改由工厂切换平台实现。",
                ],
            ),
            window=DesktopWindowCapabilities(
                supports_focus=True,
                supports_activate=True,
                supports_dialog_detection=True,
                supports_window_candidates=True,
                supports_foreground_window=True,
                supports_root_capture_recovery=True,
                notes=[
                    "窗口能力优先基于 UIA/Win32 句柄与前台窗口感知。",
                ],
            ),
            pointer=DesktopPointerCapabilities(
                supports_move=bool(self._sendinput_click_engine.is_available()),
                supports_click=True,
                supports_double_click=True,
                supports_right_click=True,
                supports_hover=bool(self._sendinput_click_engine.is_available()),
                supports_drag=bool(self._sendinput_click_engine.is_available()),
                notes=[
                    "点击优先走结构化语义，坐标类 pointer 原语优先走 SendInput。",
                ],
            ),
            viewport=DesktopViewportCapabilities(
                supports_wheel=True,
                supports_page_scroll=True,
                supports_scrollbar_drag=bool(self._sendinput_click_engine.is_available()),
                supports_ensure_visible=True,
                notes=[
                    "滚动可走鼠标滚轮与 PageUp/PageDown，复杂滚动条场景后续继续扩展。",
                ],
            ),
            observation=DesktopObservationCapabilities(
                supports_scene_identity=True,
                supports_blocker_detection=True,
                supports_goal_state_detection=True,
                supports_keyframe_visual_fallback=mss is not None,
            ),
            verification=DesktopVerificationCapabilities(
                supports_window_verification=True,
                supports_focus_verification=True,
                supports_text_verification=True,
                supports_file_verification=True,
                supports_viewport_verification=True,
                supports_business_verification=False,
            ),
            execution=DesktopExecutionRouteCapabilities(
                supports_native_command=True,
                supports_semantic_route=True,
                supports_visual_route=mss is not None,
                supports_coordinate_fallback=bool(self._sendinput_click_engine.is_available()),
                preferred_route_order=[
                    "native_command",
                    "structured_accessibility",
                    "visual_locator",
                    "coordinate_fallback",
                    "human_approval",
                ],
                notes=[
                    "Windows 当前优先走 UIA/Win32 语义路径，视觉定位与坐标补偿只作为降级链。",
                ],
            ),
            permission=DesktopPermissionCapabilities(
                accessibility_status="granted" if self.is_available() else "unavailable",
                automation_status="granted" if self.is_available() else "unavailable",
                screenshot_status="granted" if mss is not None else "unavailable",
                input_synthesis_status="granted" if self._sendinput_click_engine.is_available() else "partial",
                portal_capture_status="unsupported",
                portal_input_status="unsupported",
                session_type="windows_desktop",
                compositor="dwm",
                notes=[
                    "Windows 不走 portal 权限模型；可用性主要取决于 pywinauto / SendInput / mss 是否可用。",
                ],
            ),
        ).as_dict()

    @property
    def desktop(self):
        return self.desktop_uia

    @property
    def desktop_uia(self):
        self.ensure_available()
        if self._desktop_uia is None:
            self._desktop_uia = Desktop(backend="uia")
        return self._desktop_uia

    @property
    def desktop_win32(self):
        self.ensure_available()
        if self._desktop_win32 is None:
            self._desktop_win32 = Desktop(backend="win32")
        return self._desktop_win32

    def invalidate_window_cache(self, window_handle: int | None = None) -> None:
        if window_handle is None:
            self._window_cache.clear()
            self._observation_cache.clear()
            self._window_index_cache.clear()
            return
        self._window_cache.pop(int(window_handle), None)
        self._window_index_cache.pop(int(window_handle), None)
        stale_keys = [key for key in self._observation_cache if key.startswith(f"{int(window_handle)}:")]
        for key in stale_keys:
            self._observation_cache.pop(key, None)

    def record_selector_hint(
        self,
        *,
        window_handle: int | None,
        selector: Dict[str, Any] | None,
        source: str,
        reason: str | None = None,
        weight: int = 24,
    ) -> None:
        if window_handle is None or not isinstance(selector, dict):
            return
        normalized = self._normalize_selector_hint(selector)
        if not normalized:
            return
        bucket = [
            item
            for item in self._get_selector_hints(int(window_handle))
            if not (item.selector == normalized and item.source == source)
        ]
        bucket.insert(
            0,
            _SelectorHintEntry(
                observed_at=time.time(),
                selector=normalized,
                source=str(source or "runtime_hint").strip() or "runtime_hint",
                reason=str(reason).strip() if reason else None,
                weight=max(8, min(int(weight), 96)),
            ),
        )
        self._selector_hint_cache[int(window_handle)] = bucket[: self._selector_hint_max_per_window]

    def clear_selector_hints(self, *, window_handle: int | None = None) -> None:
        if window_handle is None:
            self._selector_hint_cache.clear()
            return
        self._selector_hint_cache.pop(int(window_handle), None)

    def invalidate_element_cache(self, element_id: str | None = None) -> None:
        if element_id:
            self._element_cache.pop(element_id, None)
        else:
            self._element_cache.clear()

    def list_windows(
        self,
        *,
        title_filter: str | None = None,
        title_filters: Iterable[str] | None = None,
        class_name: str | None = None,
        class_names: Iterable[str] | None = None,
        process_ids: Iterable[int] | None = None,
        process_names: Iterable[str] | None = None,
        backend_name: str = "uia",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        windows: List[Tuple[int, Dict[str, Any]]] = []
        process_filter = {int(item) for item in (process_ids or []) if item not in (None, "")}
        process_name_filter = {str(item).strip().lower() for item in (process_names or []) if str(item).strip()}
        title_filter_values = [self._normalize_window_text(item) for item in (title_filters or []) if str(item).strip()]
        if title_filter and str(title_filter).strip():
            title_filter_values.append(self._normalize_window_text(title_filter))
        class_filter_values = [str(item).strip().lower() for item in (class_names or []) if str(item).strip()]
        if class_name and str(class_name).strip():
            class_filter_values.append(str(class_name).strip().lower())
        for wrapper in self._safe_backend_windows(backend_name):
            data = self._window_dict(wrapper)
            title = (data.get("title") or "").strip()
            if not title:
                continue
            if is_shell_surface_window(data, platform=self.platform):
                continue
            lowered_title = self._normalize_window_text(title)
            title_score = 0 if not title_filter_values else window_title_match_score(lowered_title, title_filter_values)
            matched_title = not title_filter_values or title_score > 0
            if not matched_title:
                continue
            data_class_name = str(data.get("className") or "").strip().lower()
            matched_class = not class_filter_values or any(data_class_name == item for item in class_filter_values)
            if not matched_class:
                continue
            process_id = data.get("processId")
            if process_filter and process_id not in process_filter:
                continue
            process_name = str(data.get("processName") or "").strip().lower()
            if process_name_filter and process_name not in process_name_filter:
                continue
            score = 0
            if process_filter and process_id in process_filter:
                score += 40
            if process_name_filter and process_name in process_name_filter:
                score += 30
            if title_filter_values:
                score += title_score
            if class_filter_values and data_class_name in class_filter_values:
                score += 16
            if data.get("isVisible"):
                score += 4
            enriched = dict(data)
            enriched["matchScore"] = score
            windows.append((score, enriched))
        windows.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in windows[: max(1, limit)]]

    def wait_for_window(
        self,
        *,
        title_filter: str | None = None,
        title_filters: Iterable[str] | None = None,
        class_name: str | None = None,
        class_names: Iterable[str] | None = None,
        process_ids: Iterable[int] | None = None,
        process_names: Iterable[str] | None = None,
        backend_name: str = "uia",
        timeout_ms: int = 12000,
        poll_ms: int = 250,
    ) -> Dict[str, Any]:
        deadline = time.time() + (max(timeout_ms, 200) / 1000.0)
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
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
            except Exception as exc:
                last_error = exc
            time.sleep(max(50, poll_ms) / 1000.0)
        raise WindowsUIADriverError(f"等待窗口超时：{last_error or '未匹配到目标窗口'}")

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
        backend_name: str = "uia",
    ) -> Dict[str, Any]:
        if window_handle in (None, ""):
            matched = self.wait_for_window(
                title_filter=window_title,
                title_filters=window_title_candidates,
                class_name=class_name,
                class_names=class_name_candidates,
                process_ids=process_ids,
                process_names=process_names,
                backend_name=backend_name,
                timeout_ms=4000,
                poll_ms=180,
            )
            window_handle = matched.get("handle")
            window_title = matched.get("title") or window_title
        wrapper = self._resolve_root_resilient(
            window_title=window_title,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            backend_name=backend_name,
        )
        self._focus_wrapper(wrapper)
        resolved_handle = getattr(wrapper.element_info, "handle", None)
        if resolved_handle not in (None, "", 0):
            try:
                foreground_window = self.foreground_window(backend_name=backend_name)
            except Exception:
                foreground_window = None
            foreground_handle = (foreground_window or {}).get("handle") if isinstance(foreground_window, dict) else None
            if foreground_handle not in (None, "", 0) and int(foreground_handle) != int(resolved_handle):
                self._focus_message_window(int(resolved_handle))
                time.sleep(0.08)
                try:
                    wrapper = self._resolve_root_resilient(window_handle=int(resolved_handle), backend_name=backend_name)
                except Exception:
                    pass
        return self._window_dict(wrapper)

    def foreground_window(self, *, backend_name: str = "uia") -> Dict[str, Any] | None:
        if os.name != "nt":
            return None
        try:
            user32 = ctypes.windll.user32
            foreground_handle = int(user32.GetForegroundWindow() or 0)
            if foreground_handle <= 0:
                return None
            get_ancestor = getattr(user32, "GetAncestor", None)
            if callable(get_ancestor):
                GA_ROOT = 2
                root_handle = int(get_ancestor(foreground_handle, GA_ROOT) or foreground_handle)
            else:
                root_handle = foreground_handle
            wrapper = self._resolve_root_resilient(window_handle=root_handle, backend_name=backend_name)
            return self._window_dict(wrapper)
        except Exception:
            return None

    def _desktop_for_backend(self, backend_name: str):
        return self.desktop_win32 if backend_name == "win32" else self.desktop_uia

    def _safe_backend_windows(self, backend_name: str):
        try:
            return list(self._desktop_for_backend(backend_name).windows())
        except Exception as exc:
            if backend_name == "uia":
                self._bump_selector_metric("win32FallbackHits")
                try:
                    return list(self._desktop_for_backend("win32").windows())
                except Exception:
                    pass
            raise WindowsUIADriverError(str(exc))

    def _window_cache_key(self, *, backend_name: str, window_handle: int) -> str:
        return f"{backend_name}:{int(window_handle)}"

    def observe_desktop(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        depth_limit: int = 4,
        element_limit: int = 80,
        use_cache: bool = True,
    ) -> ComputerUseObservation:
        target = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        cache_key = self._observation_cache_key(
            window_handle=getattr(target.element_info, "handle", None),
            depth_limit=depth_limit,
            element_limit=element_limit,
        )
        observed_at = time.time()
        if use_cache:
            cached = self._observation_cache.get(cache_key)
            if cached and (observed_at - cached.observed_at) <= self._observation_cache_ttl_seconds:
                return cached.observation
        elements = self._enumerate_elements(target, depth_limit=depth_limit, limit=element_limit, backend_name="uia")
        focused_id = elements[0].element_id if elements else None
        window_bounds = self._rect_to_bounds(target.rectangle())
        display_context = self._display_context_for_bounds(
            window_bounds,
            window_handle=getattr(target.element_info, "handle", None),
        )
        screen_hash = self._hash_payload([item.element_id for item in elements])
        tree_hash = self._hash_payload(
            [
                {
                    "id": item.element_id,
                    "name": item.name,
                    "role": item.role,
                    "automation_id": item.automation_id,
                }
                for item in elements
            ]
        )
        observation = ComputerUseObservation(
            snapshot_id=f"snap_{hashlib.md5(f'{time.time()}:{screen_hash}'.encode('utf-8')).hexdigest()[:12]}",
            platform=self.platform,
            backend=self.backend,
            app=(target.window_text() or target.element_info.name or "").strip(),
            window_title=(target.window_text() or "").strip(),
            screen_hash=screen_hash,
            tree_hash=tree_hash,
            elements=elements,
            focused_element_id=focused_id,
            metadata={
                "windowHandle": getattr(target.element_info, "handle", None),
                "className": getattr(target.element_info, "class_name", ""),
                "windowBounds": window_bounds,
                "processId": self._window_process_id(target),
                "processName": self._window_process_name(target),
                "elementCount": len(elements),
                "selectorStats": self.selector_metrics(),
                **display_context,
            },
        )
        window_handle_value = getattr(target.element_info, "handle", None)
        if window_handle_value is not None:
            self._cache_window_index(int(window_handle_value), elements=elements, observed_at=observed_at)
        if use_cache:
            self._observation_cache[cache_key] = _ObservationCacheEntry(observed_at=observed_at, observation=observation)
        return observation

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
        use_cache: bool = True,
    ) -> List[ComputerUseElement]:
        self._bump_selector_metric("findCalls")
        root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle, backend_name="uia")
        root_handle = getattr(root.element_info, "handle", None)
        if use_cache and root_handle is not None:
            cached_matches = self._find_cached_elements(
                window_handle=int(root_handle),
                element_id=element_id,
                name=name,
                name_contains=name_contains,
                target_text=target_text,
                automation_id=automation_id,
                control_type=control_type,
                class_name=class_name,
                limit=limit,
            )
            if cached_matches:
                self._bump_selector_metric("windowIndexHits")
                return cached_matches[: max(1, limit)]
            self._bump_selector_metric("windowIndexMisses")
        fast_matches = self._query_elements_fast(
            root,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            limit=limit,
        )
        if fast_matches:
            self._bump_selector_metric("fastQueryHits")
            return fast_matches[: max(1, limit)]
        ranked_matches: List[tuple[int, ComputerUseElement]] = []
        observation = self.observe_desktop(
            window_title=window_title,
            window_handle=window_handle,
            depth_limit=depth_limit,
            element_limit=max(limit * 3, 60),
            use_cache=use_cache,
        )
        for element in observation.elements:
            if element_id and element.element_id != element_id:
                continue
            score = self._score_cached_element(
                element,
                name=name,
                name_contains=name_contains,
                target_text=target_text,
                automation_id=automation_id,
                control_type=control_type,
                class_name=class_name,
            )
            if score > 0:
                ranked_matches.append((score, element))
        ranked_matches.sort(
            key=lambda item: (
                -item[0],
                item[1].metadata.get("isVisible") is False,
                item[1].metadata.get("isEnabled") is False,
            )
        )
        matches = [element for _score, element in ranked_matches[: max(1, limit)]]
        if matches:
            self._bump_selector_metric("observationScanHits")
            return matches
        uia_scan_matches = self._find_elements_via_backend_scan(
            backend_name="uia",
            window_title=window_title,
            window_handle=window_handle,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            depth_limit=depth_limit,
            limit=limit,
        )
        if uia_scan_matches:
            self._bump_selector_metric("observationScanHits")
            return uia_scan_matches[: max(1, limit)]
        win32_matches = self._find_elements_via_backend_scan(
            backend_name="win32",
            window_title=window_title,
            window_handle=window_handle,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            depth_limit=depth_limit,
            limit=limit,
        )
        if win32_matches:
            self._bump_selector_metric("win32FallbackHits")
            return win32_matches
        self._bump_selector_metric("win32FallbackMisses")
        self._bump_selector_metric("findMisses")
        return matches

    def click_element(
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
        double: bool = False,
        prefer_sendinput_click: bool = False,
    ) -> ComputerUseElement:
        wrapper, element = self._resolve_target(
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )
        self._focus_wrapper(wrapper)
        strategy = self._perform_click_strategy(
            wrapper=wrapper,
            element=element,
            double=double,
            prefer_sendinput_click=bool(prefer_sendinput_click),
        )
        element.metadata["clickStrategy"] = strategy
        element.metadata["lowIntrusion"] = strategy in {"invoke", "select", "toggle", "keyboard_enter", "keyboard_space"}
        element.metadata["sendInputPreferred"] = bool(prefer_sendinput_click)
        return element

    def right_click_element(
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
    ) -> ComputerUseElement:
        wrapper, element = self._resolve_target(
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )
        self._focus_wrapper(wrapper)
        center = self._center_of_bounds(element.bounds)
        strategy = ""
        try:
            if hasattr(wrapper, "right_click_input"):
                wrapper.right_click_input()
                strategy = "right_click_input"
            elif mouse is not None:
                mouse.click(button="right", coords=center)
                strategy = "mouse_right_click"
            elif self._sendinput_click_engine.is_available():
                strategy = self._sendinput_click_engine.right_click(center)
            else:
                self._message_click(window_handle=element.window_handle, point=list(center), double=False, button="right")
                strategy = "message_right_click"
        except Exception:
            self._message_click(window_handle=element.window_handle, point=list(center), double=False, button="right")
            strategy = "message_right_click"
        element.metadata["clickStrategy"] = strategy
        return element

    def click_point(
        self,
        *,
        point: List[int] | Tuple[int, int],
        window_title: str | None = None,
        window_handle: int | None = None,
        double: bool = False,
        prefer_sendinput_click: bool = False,
    ) -> Dict[str, Any]:
        resolved_point = [int(point[0]), int(point[1])]
        root = None
        if window_title or window_handle:
            root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        if double:
            try:
                strategy = self._coordinate_click(
                    point=resolved_point,
                    root=root,
                    double=True,
                    prefer_sendinput_click=bool(prefer_sendinput_click),
                )
            except Exception:
                self._message_click(
                    window_handle=getattr(getattr(root, "element_info", None), "handle", None),
                    point=resolved_point,
                    double=True,
                )
                strategy = "coordinate_double_click_message"
        else:
            try:
                strategy = self._coordinate_click(
                    point=resolved_point,
                    root=root,
                    double=False,
                    prefer_sendinput_click=bool(prefer_sendinput_click),
                )
            except Exception:
                self._message_click(
                    window_handle=getattr(getattr(root, "element_info", None), "handle", None),
                    point=resolved_point,
                    double=False,
                )
                strategy = "coordinate_click_message"
        window_payload = self._window_dict(root) if root is not None else {}
        return {
            **window_payload,
            "clickedPoint": list(resolved_point),
            "bounds": [
                resolved_point[0] - 2,
                resolved_point[1] - 2,
                resolved_point[0] + 2,
                resolved_point[1] + 2,
            ],
            "metadata": {
                "clickStrategy": strategy,
                "coordinateFallback": True,
                "sendInputPreferred": bool(prefer_sendinput_click),
            },
        }

    def hover_point(
        self,
        *,
        point: List[int] | Tuple[int, int],
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        resolved_point = [int(point[0]), int(point[1])]
        root = None
        if window_title or window_handle:
            root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        if root is not None:
            self._focus_wrapper(root)
        if not self._sendinput_click_engine.is_available():
            raise WindowsUIADriverError("当前环境不可用 SendInput move，无法执行 hover。")
        strategy = self._sendinput_click_engine.move(resolved_point)
        window_payload = self._window_dict(root) if root is not None else {}
        return {
            **window_payload,
            "hoverPoint": list(resolved_point),
            "bounds": [
                resolved_point[0] - 2,
                resolved_point[1] - 2,
                resolved_point[0] + 2,
                resolved_point[1] + 2,
            ],
            "metadata": {
                "pointerStrategy": strategy,
                "coordinateFallback": True,
            },
        }

    def right_click_point(
        self,
        *,
        point: List[int] | Tuple[int, int],
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        resolved_point = [int(point[0]), int(point[1])]
        root = None
        if window_title or window_handle:
            root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        if root is not None:
            self._focus_wrapper(root)
        strategy = ""
        try:
            if self._sendinput_click_engine.is_available():
                strategy = self._sendinput_click_engine.right_click(resolved_point)
            elif mouse is not None:
                mouse.click(button="right", coords=tuple(resolved_point))
                strategy = "mouse_right_click"
            else:
                self._message_click(
                    window_handle=getattr(getattr(root, "element_info", None), "handle", None),
                    point=resolved_point,
                    double=False,
                    button="right",
                )
                strategy = "message_right_click"
        except Exception:
            self._message_click(
                window_handle=getattr(getattr(root, "element_info", None), "handle", None),
                point=resolved_point,
                double=False,
                button="right",
            )
            strategy = "message_right_click"
        window_payload = self._window_dict(root) if root is not None else {}
        return {
            **window_payload,
            "clickedPoint": list(resolved_point),
            "bounds": [
                resolved_point[0] - 2,
                resolved_point[1] - 2,
                resolved_point[0] + 2,
                resolved_point[1] + 2,
            ],
            "metadata": {
                "clickStrategy": strategy,
                "coordinateFallback": True,
            },
        }

    def drag_between_points(
        self,
        *,
        start_point: List[int] | Tuple[int, int],
        end_point: List[int] | Tuple[int, int],
        window_title: str | None = None,
        window_handle: int | None = None,
        steps: int = 12,
    ) -> Dict[str, Any]:
        if not self._sendinput_click_engine.is_available():
            raise WindowsUIADriverError("当前环境不可用 SendInput drag，无法执行拖拽。")
        root = None
        if window_title or window_handle:
            root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        if root is not None:
            self._focus_wrapper(root)
        resolved_start = [int(start_point[0]), int(start_point[1])]
        resolved_end = [int(end_point[0]), int(end_point[1])]
        strategy = self._sendinput_click_engine.drag(resolved_start, resolved_end, steps=max(2, int(steps)))
        window_payload = self._window_dict(root) if root is not None else {}
        return {
            **window_payload,
            "startPoint": list(resolved_start),
            "endPoint": list(resolved_end),
            "bounds": [
                min(resolved_start[0], resolved_end[0]),
                min(resolved_start[1], resolved_end[1]),
                max(resolved_start[0], resolved_end[0]),
                max(resolved_start[1], resolved_end[1]),
            ],
            "metadata": {
                "pointerStrategy": strategy,
                "coordinateFallback": True,
            },
        }

    def type_text(
        self,
        *,
        text: str,
        file_paths: List[str] | None = None,
        element_id: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
        name: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
        clear_first: bool = False,
        press_enter: bool = False,
    ) -> ComputerUseElement:
        wrapper, element = self._resolve_target(
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )
        self._focus_wrapper(wrapper)
        input_capability = self._text_input_capability(wrapper, element)
        element.metadata["textInputCapability"] = input_capability
        if not input_capability.get("allowed"):
            raise WindowsUIADriverError(
                str(input_capability.get("reason") or "目标控件不是可编辑输入控件，已阻止输入。")
            )
        element.metadata["focusProbe"] = self._confirm_wrapper_text_focus(
            wrapper=wrapper,
            input_capability=input_capability,
        )
        supports_direct_text = bool(input_capability.get("direct"))
        if clear_first:
            try:
                if supports_direct_text:
                    self._set_direct_text(wrapper=wrapper, element=element, text="", clear_first=True)
                else:
                    wrapper.type_keys("^a{DELETE}", set_foreground=True)
                    wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)
            except Exception:
                wrapper.type_keys("^a{DELETE}", set_foreground=True)
                wrapper.type_keys("^a{BACKSPACE}", set_foreground=True)

        try:
            if list(file_paths or []):
                self._sendinput_type_text(
                    text=text,
                    file_paths=file_paths,
                    clear_first=bool(clear_first),
                    press_enter=bool(press_enter),
                )
            elif supports_direct_text:
                self._set_direct_text(
                    wrapper=wrapper,
                    element=element,
                    text=text,
                    clear_first=bool(clear_first),
                )
            else:
                wrapper.type_keys(text, with_spaces=True, set_foreground=True, pause=0.01)
        except Exception:
            if supports_direct_text:
                raise
            if list(file_paths or []):
                raise WindowsUIADriverError("当前目标不支持文件剪贴板发送回退。")
            handle = getattr(getattr(wrapper, "element_info", None), "handle", None)
            if handle not in (None, 0):
                self._message_type_text(
                    window_handle=int(handle),
                    text=text,
                    clear_first=bool(clear_first),
                    press_enter=bool(press_enter),
                    point=None,
                )
                return element
            wrapper.type_keys(text, with_spaces=True, set_foreground=True, pause=0.01, vk_packet=False)

        if press_enter:
            wrapper.type_keys("{ENTER}", set_foreground=True)
        return element

    def type_text_in_window(
        self,
        *,
        text: str,
        window_title: str | None = None,
        window_handle: int | None = None,
        point: List[int] | None = None,
        point_candidates: List[List[int]] | None = None,
        file_paths: List[str] | None = None,
        clear_first: bool = False,
        press_enter: bool = False,
        prefer_sendinput_click: bool = False,
        prefer_sendinput_text: bool = False,
        focus_probe_mode: str | None = None,
        file_paste_strategy: str | None = None,
    ) -> Dict[str, Any]:
        root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        try:
            self._focus_wrapper(root)
        except Exception:
            root_handle_for_focus = getattr(getattr(root, "element_info", None), "handle", None) or window_handle
            if root_handle_for_focus not in (None, 0):
                self._focus_message_window(int(root_handle_for_focus))
        root_handle = getattr(getattr(root, "element_info", None), "handle", None)
        candidate_points: List[List[int]] = []
        for raw_point in [point, *(point_candidates or [])]:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                continue
            normalized = [int(raw_point[0]), int(raw_point[1])]
            if normalized not in candidate_points:
                candidate_points.append(normalized)
        clicked_point = list(point) if point is not None else None
        focus_probes: List[Dict[str, Any]] = []
        normalized_focus_mode = str(focus_probe_mode or "").strip().lower() or None
        expects_file_receiver_focus = bool(list(file_paths or [])) and normalized_focus_mode == "content_receiver"
        focus_requirement_label = "文件接收区焦点" if expects_file_receiver_focus else "可编辑焦点"
        focus_confirmed = False
        initial_probe = self._window_typing_probe(root_handle=int(root_handle) if root_handle not in (None, 0) else None)
        initial_probe["candidatePoint"] = None
        initial_probe["attemptIndex"] = 0
        initial_probe["focusProbeMode"] = normalized_focus_mode
        initial_probe["probeSource"] = "initial_window_focus"
        focus_probes.append(initial_probe)
        focus_confirmed = self._accept_window_typing_probe(initial_probe, focus_mode=normalized_focus_mode)
        if candidate_points:
            for index, candidate in enumerate(candidate_points, start=1):
                try:
                    self._coordinate_click(
                        point=candidate,
                        root=root,
                        double=False,
                        prefer_sendinput_click=bool(prefer_sendinput_click),
                    )
                except Exception:
                    self._message_click(
                        window_handle=root_handle,
                        point=candidate,
                        double=False,
                    )
                time.sleep(0.06)
                probe = self._window_typing_probe(root_handle=int(root_handle) if root_handle not in (None, 0) else None)
                probe["candidatePoint"] = list(candidate)
                probe["attemptIndex"] = index
                probe["focusProbeMode"] = normalized_focus_mode
                probe["probeSource"] = "candidate_click"
                focus_probes.append(probe)
                clicked_point = list(candidate)
                if self._accept_window_typing_probe(probe, focus_mode=normalized_focus_mode):
                    focus_confirmed = True
                    break
        if not focus_confirmed:
            raise WindowsUIADriverError(f"窗口级输入前未确认{focus_requirement_label}，已阻止继续输入。")
        input_strategy = "send_keys"
        try:
            if list(file_paths or []):
                if str(file_paste_strategy or "").strip().lower() == "window_message":
                    raise RuntimeError("clipboard_files_require_window_message")
                raise RuntimeError("clipboard_files_require_sendinput")
            if prefer_sendinput_text:
                raise RuntimeError("text_prefers_sendinput")
            if clear_first:
                send_keys("^a", with_spaces=True, pause=0.015)
                time.sleep(0.05)
                send_keys("{DELETE}", with_spaces=True, pause=0.015)
                time.sleep(0.05)
                send_keys("^a", with_spaces=True, pause=0.015)
                time.sleep(0.05)
                send_keys("{BACKSPACE}", with_spaces=True, pause=0.015)
                time.sleep(0.08)
            send_keys(text, with_spaces=True, pause=0.015)
            if press_enter:
                time.sleep(0.05)
                send_keys("{ENTER}", with_spaces=True, pause=0.015)
        except Exception:
            try:
                if list(file_paths or []) and str(file_paste_strategy or "").strip().lower() == "window_message":
                    self._message_paste_payload(
                        window_handle=getattr(getattr(root, "element_info", None), "handle", None),
                        point=clicked_point,
                        text=text,
                        file_paths=file_paths,
                        clear_first=bool(clear_first),
                        press_enter=bool(press_enter),
                    )
                    input_strategy = "window_message_clipboard_files"
                else:
                    input_strategy = self._sendinput_type_text(
                        text=text,
                        file_paths=file_paths,
                        clear_first=bool(clear_first),
                        press_enter=bool(press_enter),
                    )
            except Exception:
                if list(file_paths or []):
                    raise
                self._message_type_text(
                    window_handle=getattr(getattr(root, "element_info", None), "handle", None),
                    text=text,
                    clear_first=bool(clear_first),
                    press_enter=bool(press_enter),
                    point=point,
                )
                input_strategy = "window_message"
        window_payload = self._window_dict(root)
        window_payload["windowTitle"] = window_payload.get("title")
        window_payload["windowHandle"] = window_payload.get("handle")
        window_payload["role"] = "CoordinateTextInput"
        window_payload["clickedPoint"] = clicked_point
        bounds = window_payload.get("bounds") or []
        if clicked_point is not None:
            window_payload["bounds"] = [clicked_point[0] - 2, clicked_point[1] - 2, clicked_point[0] + 2, clicked_point[1] + 2]
        elif isinstance(bounds, list) and len(bounds) == 4:
            center = self._center_of_bounds(bounds)
            window_payload["bounds"] = [center[0] - 2, center[1] - 2, center[0] + 2, center[1] + 2]
        window_payload["metadata"] = {
            "coordinateFallback": clicked_point is not None,
            "inputStrategy": input_strategy,
            "sendInputPreferred": bool(prefer_sendinput_click),
            "sendInputTextPreferred": bool(prefer_sendinput_text),
            "focusProbeMode": normalized_focus_mode,
            "focusProbeAccepted": focus_confirmed,
            "focusRequirement": "content_receiver" if expects_file_receiver_focus else "editable_text",
            "filePasteStrategy": str(file_paste_strategy or "").strip().lower() or None,
            "inputPointCandidates": [list(item) for item in candidate_points],
            "inputFocusProbes": focus_probes,
            "textInputCapability": {
                "allowed": True,
                "direct": False,
                "status": "coordinate_window_file_receiver" if expects_file_receiver_focus else "coordinate_window_target",
                "controlType": "window",
                "reason": "通过窗口聚焦和坐标点击执行文件粘贴。"
                if expects_file_receiver_focus
                else "通过窗口聚焦和坐标点击执行键盘输入。",
            },
        }
        return window_payload

    def _confirm_wrapper_text_focus(
        self,
        *,
        wrapper,
        input_capability: Dict[str, Any],
    ) -> Dict[str, Any]:
        if bool(input_capability.get("direct")):
            return {
                "accepted": True,
                "status": "direct_input_target",
                "reason": "目标支持 direct text input，无需键盘焦点探针。",
            }
        focus_state = self._focus_state(wrapper)
        accepted = bool(focus_state.get("hasKeyboardFocus"))
        if not accepted and input_capability.get("status") in {"editable_keyboard_target", "editable_document_target"}:
            accepted = bool(focus_state.get("isActiveWindow"))
        probe = {
            "accepted": accepted,
            "status": "keyboard_focus_confirmed" if accepted else "keyboard_focus_unconfirmed",
            "focusState": focus_state,
            "controlType": input_capability.get("controlType"),
        }
        if not accepted:
            raise WindowsUIADriverError("目标输入控件未获得键盘焦点，已阻止输入。")
        return probe

    def hotkey(self, sequence: str, *, window_title: str | None = None, window_handle: int | None = None) -> Dict[str, Any]:
        target = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        try:
            self._focus_wrapper(target)
        except Exception:
            target_handle_for_focus = getattr(getattr(target, "element_info", None), "handle", None) or window_handle
            if target_handle_for_focus not in (None, 0):
                self._focus_message_window(int(target_handle_for_focus))
        support_plan = analyze_hotkey_support(sequence)
        strategy = "send_keys"
        try:
            send_keys(sequence, with_spaces=True, pause=0.01)
        except Exception:
            try:
                if not support_plan.supports_sendinput:
                    raise WindowsUIADriverError("当前快捷键序列不支持 SendInput 回退。")
                self._sendinput_click_engine.send_hotkey_sequence(sequence, settle_ms=20)
                strategy = "sendinput"
            except Exception:
                if not support_plan.supports_window_message:
                    raise WindowsUIADriverError(
                        "; ".join(support_plan.reasons) or "当前快捷键序列不支持 window_message 回退。"
                    )
                if not self._message_hotkey(
                    window_handle=getattr(getattr(target, "element_info", None), "handle", None),
                    sequence=sequence,
                ):
                    raise
                strategy = "window_message"
        payload = self._window_dict(target)
        payload["metadata"] = {
            "hotkeyStrategy": strategy,
            "hotkeySupport": {
                "supportsSendInput": support_plan.supports_sendinput,
                "supportsWindowMessage": support_plan.supports_window_message,
                "requiresForeground": support_plan.requires_foreground,
                "reasons": list(support_plan.reasons),
            },
        }
        return payload

    def read_selected_text_via_clipboard(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        select_hotkey: str | None = None,
        settle_ms: int = 90,
    ) -> Dict[str, Any]:
        target = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        target_handle = getattr(getattr(target, "element_info", None), "handle", None) or window_handle
        try:
            self._focus_wrapper(target)
        except Exception:
            if target_handle not in (None, 0):
                self._focus_message_window(int(target_handle))
        previous_payload = self._sendinput_click_engine._snapshot_clipboard_payload()
        selected_text = ""
        try:
            normalized_select_hotkey = str(select_hotkey or "").strip()
            if normalized_select_hotkey:
                self.hotkey(
                    normalized_select_hotkey,
                    window_title=window_title,
                    window_handle=int(target_handle) if target_handle not in (None, 0) else None,
                )
                time.sleep(max(20, int(settle_ms)) / 1000.0)
            self.hotkey(
                "^c",
                window_title=window_title,
                window_handle=int(target_handle) if target_handle not in (None, 0) else None,
            )
            time.sleep(max(20, int(settle_ms)) / 1000.0)
            current_payload = self._sendinput_click_engine._snapshot_clipboard_payload()
            selected_text = str(current_payload.get("text") or "").strip()
        finally:
            self._sendinput_click_engine._restore_clipboard_payload(previous_payload)
        payload = self._window_dict(target)
        payload["selectedText"] = selected_text
        payload["windowHandle"] = payload.get("handle")
        payload["windowTitle"] = payload.get("title")
        payload["metadata"] = {
            **dict(payload.get("metadata") or {}),
            "clipboardProbe": True,
            "selectHotkey": str(select_hotkey or "").strip() or None,
        }
        return payload

    def scroll(
        self,
        *,
        amount: int,
        element_id: str | None = None,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        if mouse is None:
            raise WindowsUIADriverError("当前环境缺少 pywinauto.mouse，无法执行滚轮动作。")
        wrapper = None
        bounds = None
        if element_id:
            wrapper, element = self._resolve_target(element_id=element_id)
            bounds = element.bounds
        else:
            wrapper = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
            bounds = self._rect_to_bounds(wrapper.rectangle())
        self._focus_wrapper(wrapper)
        mouse.scroll(wheel_dist=amount, coords=self._center_of_bounds(bounds))
        return {
            "windowHandle": getattr(wrapper.element_info, "handle", None),
            "bounds": bounds,
            "amount": amount,
        }

    def page_scroll(
        self,
        *,
        direction: str,
        count: int = 1,
        window_title: str | None = None,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        wrapper = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
        self._focus_wrapper(wrapper)
        normalized_direction = str(direction or "down").strip().lower()
        sequence = "{PGUP}" if normalized_direction in {"up", "page_up", "pgup"} else "{PGDN}"
        for _ in range(max(1, int(count))):
            try:
                send_keys(sequence, with_spaces=True, pause=0.01)
            except Exception:
                if not self._sendinput_click_engine.is_available():
                    raise
                self._sendinput_click_engine.send_hotkey_sequence(sequence, settle_ms=20)
        return {
            "windowHandle": getattr(wrapper.element_info, "handle", None),
            "bounds": self._rect_to_bounds(wrapper.rectangle()),
            "direction": normalized_direction,
            "count": max(1, int(count)),
            "metadata": {
                "viewportStrategy": "page_scroll",
            },
        }

    def wait_for_element(self, timeout_ms: int = 10000, poll_ms: int = 300, **query: Any) -> ComputerUseElement:
        deadline = time.time() + (max(timeout_ms, 100) / 1000.0)
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                wrapper, element = self._resolve_target(**query)
                return element
            except Exception as exc:  # pragma: no cover - timing-based
                last_error = exc
                time.sleep(max(poll_ms, 50) / 1000.0)
        raise WindowsUIADriverError(f"等待元素超时：{last_error or '未找到目标元素'}")

    def capture_screenshot(
        self,
        output_path: str | Path,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        element_id: str | None = None,
    ) -> Dict[str, Any]:
        if mss is None:
            raise WindowsUIADriverError("当前环境缺少 `mss`，无法捕获屏幕截图。")

        bounds: List[int]
        capture_window: Dict[str, Any] | None = None
        if element_id:
            _, element = self._resolve_target(element_id=element_id)
            bounds = element.bounds
        else:
            root = self._resolve_root_resilient(window_title=window_title, window_handle=window_handle)
            capture_window = self._window_dict(root)
            bounds = self._rect_to_bounds(root.rectangle())
            capture_window, bounds = self._stabilize_capture_window(
                root=root,
                window=capture_window,
                bounds=bounds,
                requested_title=window_title,
                requested_handle=window_handle,
            )
            capture_window, bounds = self._recover_capture_window_if_needed(
                window=capture_window,
                bounds=bounds,
                requested_title=window_title,
                requested_handle=window_handle,
            )
            capture_window, bounds = self._prepare_capture_window_foreground(
                window=capture_window,
                bounds=bounds,
                requested_title=window_title,
                requested_handle=window_handle,
            )

        left, top, right, bottom = bounds
        monitor = {
            "left": max(0, left),
            "top": max(0, top),
            "width": max(1, right - left),
            "height": max(1, bottom - top),
        }
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with mss.mss() as sct:
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=str(output))
        return {
            "path": str(output),
            "bounds": bounds,
            "size": {"width": monitor["width"], "height": monitor["height"]},
            "window": capture_window,
        }

    def _prepare_capture_window_foreground(
        self,
        *,
        window: Dict[str, Any] | None,
        bounds: List[int],
        requested_title: str | None,
        requested_handle: int | None,
    ) -> tuple[Dict[str, Any] | None, List[int]]:
        normalized_window = dict(window or {})
        target_handle = requested_handle if requested_handle not in (None, "", 0) else normalized_window.get("handle")
        target_title = str(requested_title or normalized_window.get("title") or "").strip()
        root = None
        if target_handle not in (None, "", 0):
            try:
                self._focus_message_window(int(target_handle))
            except Exception:
                pass
            try:
                root = self._resolve_root_resilient(window_handle=int(target_handle))
            except Exception:
                root = None
        elif target_title:
            try:
                root = self._resolve_root_resilient(window_title=target_title)
            except Exception:
                root = None
        if root is not None:
            try:
                self._focus_wrapper(root)
            except Exception:
                pass
            time.sleep(0.14)
            try:
                refreshed_root = self._resolve_root_resilient(
                    window_handle=int(getattr(root.element_info, "handle", 0) or 0)
                )
            except Exception:
                refreshed_root = root
            refreshed_window = self._window_dict(refreshed_root)
            refreshed_bounds = self._rect_to_bounds(refreshed_root.rectangle())
            foreground_window = self.foreground_window()
            foreground_handle = (foreground_window or {}).get("handle")
            refreshed_handle = refreshed_window.get("handle")
            if (
                refreshed_handle not in (None, "", 0)
                and foreground_handle not in (None, "", 0)
                and int(refreshed_handle) != int(foreground_handle)
            ):
                try:
                    self._focus_message_window(int(refreshed_handle))
                    time.sleep(0.12)
                except Exception:
                    pass
                try:
                    refreshed_root = self._resolve_root_resilient(window_handle=int(refreshed_handle))
                    refreshed_window = self._window_dict(refreshed_root)
                    refreshed_bounds = self._rect_to_bounds(refreshed_root.rectangle())
                except Exception:
                    pass
            return refreshed_window, refreshed_bounds
        return normalized_window or None, bounds

    def _stabilize_capture_window(
        self,
        *,
        root,
        window: Dict[str, Any] | None,
        bounds: List[int],
        requested_title: str | None,
        requested_handle: int | None,
    ) -> tuple[Dict[str, Any] | None, List[int]]:
        normalized_window = dict(window or {})
        display_bounds = self._display_context_for_bounds(
            bounds,
            window_handle=normalized_window.get("handle"),
        ).get("displayBounds")
        window_visible = bool(normalized_window.get("isVisible"))
        if window_visible and not is_suspicious_capture_bounds(bounds, display_bounds=display_bounds):
            return normalized_window or None, bounds
        handle = requested_handle if requested_handle not in (None, "", 0) else normalized_window.get("handle")
        if handle not in (None, "", 0):
            try:
                self._focus_message_window(int(handle))
            except Exception:
                pass
        try:
            self._focus_wrapper(root)
        except Exception:
            pass
        time.sleep(0.12)
        try:
            refreshed_root = self._resolve_root_resilient(
                window_title=requested_title,
                window_handle=int(handle) if handle not in (None, "", 0) else None,
            )
        except Exception:
            return normalized_window or None, bounds
        refreshed_window = self._window_dict(refreshed_root)
        refreshed_bounds = self._rect_to_bounds(refreshed_root.rectangle())
        return refreshed_window, refreshed_bounds

    def _recover_capture_window_if_needed(
        self,
        *,
        window: Dict[str, Any] | None,
        bounds: List[int],
        requested_title: str | None,
        requested_handle: int | None,
    ) -> tuple[Dict[str, Any] | None, List[int]]:
        normalized_window = dict(window or {})
        display_bounds = self._display_context_for_bounds(
            bounds,
            window_handle=normalized_window.get("handle"),
        ).get("displayBounds")
        if not (
            is_shell_surface_window(normalized_window, platform=self.platform)
            or is_suspicious_capture_bounds(bounds, display_bounds=display_bounds)
        ):
            return normalized_window or None, bounds
        expected_titles: List[str] = []
        if str(requested_title or "").strip():
            expected_titles.append(str(requested_title).strip())
        elif str(normalized_window.get("title") or "").strip():
            expected_titles.append(str(normalized_window.get("title") or "").strip())
        expected_classes = [str(normalized_window.get("className") or "").strip()] if str(normalized_window.get("className") or "").strip() else []
        expected_process_names = [str(normalized_window.get("processName") or "").strip()] if str(normalized_window.get("processName") or "").strip() else []
        strict_binding_required = requires_strict_window_binding(
            expected_titles=expected_titles,
            expected_classes=expected_classes,
        )
        candidates = self._capture_recovery_candidates(
            expected_titles=expected_titles,
            expected_classes=expected_classes,
            expected_process_names=expected_process_names,
            preferred_handle=requested_handle if requested_handle not in (None, "", 0) else normalized_window.get("handle"),
        )
        foreground_window = self.foreground_window()
        if foreground_window:
            candidates.append(foreground_window)
        replacement = choose_best_window_candidate(
            candidates,
            expected_titles=expected_titles,
            expected_classes=expected_classes,
            expected_process_names=expected_process_names,
            preferred_handle=requested_handle if requested_handle not in (None, "", 0) else None,
            platform=self.platform,
        )
        if replacement is None or is_shell_surface_window(replacement, platform=self.platform):
            return normalized_window or None, bounds
        replacement_handle = replacement.get("handle")
        requested_or_current_handle = requested_handle if requested_handle not in (None, "", 0) else normalized_window.get("handle")
        exact_handle_match = (
            replacement_handle not in (None, "", 0)
            and requested_or_current_handle not in (None, "", 0)
            and int(replacement_handle) == int(requested_or_current_handle)
        )
        if strict_binding_required and not exact_handle_match:
            if not window_satisfies_binding(
                replacement,
                expected_titles=expected_titles,
                expected_classes=expected_classes,
                expected_process_names=expected_process_names,
                platform=self.platform,
                require_title_or_class_match=True,
            ):
                return normalized_window or None, bounds
        if replacement_handle in (None, "", 0):
            return normalized_window or None, bounds
        try:
            replacement_root = self._resolve_root_resilient(window_handle=int(replacement_handle))
        except Exception:
            return normalized_window or None, bounds
        replacement_bounds = self._rect_to_bounds(replacement_root.rectangle())
        replacement_display_bounds = self._display_context_for_bounds(
            replacement_bounds,
            window_handle=replacement_handle,
        ).get("displayBounds")
        if is_suspicious_capture_bounds(replacement_bounds, display_bounds=replacement_display_bounds):
            return normalized_window or None, bounds
        return dict(replacement), replacement_bounds

    def _capture_recovery_candidates(
        self,
        *,
        expected_titles: Iterable[str] | None = None,
        expected_classes: Iterable[str] | None = None,
        expected_process_names: Iterable[str] | None = None,
        preferred_handle: int | None = None,
    ) -> List[Dict[str, Any]]:
        strict_binding_required = requires_strict_window_binding(
            expected_titles=expected_titles,
            expected_classes=expected_classes,
        )
        deduped: Dict[str, Dict[str, Any]] = {}

        def _remember(candidate: Dict[str, Any] | None) -> None:
            payload = dict(candidate or {})
            if not payload:
                return
            handle = payload.get("handle")
            if handle not in (None, "", 0):
                key = f"handle:{int(handle)}"
            else:
                key = "|".join(
                    [
                        str(payload.get("title") or "").strip(),
                        str(payload.get("className") or "").strip(),
                        str(payload.get("processName") or "").strip(),
                    ]
                )
            if key not in deduped:
                deduped[key] = payload

        for backend_name in ("uia", "win32"):
            if preferred_handle not in (None, "", 0):
                try:
                    for wrapper in self._safe_backend_windows(backend_name):
                        payload = self._window_dict(wrapper)
                        if payload.get("handle") not in (None, "", 0) and int(payload.get("handle")) == int(preferred_handle):
                            _remember(payload)
                except Exception:
                    pass
            try:
                for payload in self.list_windows(
                    title_filters=expected_titles,
                    class_names=expected_classes,
                    process_names=expected_process_names,
                    backend_name=backend_name,
                    limit=8,
                ):
                    _remember(payload)
            except Exception:
                pass
            try:
                for wrapper in self._safe_backend_windows(backend_name):
                    payload = self._window_dict(wrapper)
                    if window_satisfies_binding(
                        payload,
                        expected_titles=expected_titles,
                        expected_classes=expected_classes,
                        expected_process_names=expected_process_names,
                        platform=self.platform,
                        require_title_or_class_match=strict_binding_required,
                    ):
                        _remember(payload)
            except Exception:
                pass
        return list(deduped.values())

    def refresh_element(self, *, element_id: str | None = None, **query: Any) -> ComputerUseElement:
        _wrapper, element = self._resolve_target(element_id=element_id, **query)
        return element

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
            "windowHandle": window_handle,
            "windowTitle": window_title,
            "beforeTreeHash": (before_observation or {}).get("treeHash"),
            "afterTreeHash": (after_observation or {}).get("treeHash"),
            "beforeScreenHash": (before_observation or {}).get("screenHash"),
            "afterScreenHash": (after_observation or {}).get("screenHash"),
        }
        target_query_name = target.get("name")
        if action_type == "type_text":
            target_class_name = str(target.get("className") or target.get("class_name") or "").strip().lower()
            has_stable_selector = bool(target.get("automationId") or target.get("automation_id"))
            if has_stable_selector or target_class_name in {"omniboxviewviews", "textfield"}:
                target_query_name = None
        try:
            refreshed = self.refresh_element(
                element_id=target.get("elementId") or target.get("element_id"),
                window_title=window_title or target.get("windowTitle") or target.get("window_title"),
                window_handle=window_handle or target.get("windowHandle") or target.get("window_handle"),
                name=target_query_name,
                name_contains=target.get("nameContains") or target.get("name_contains"),
                automation_id=target.get("automationId") or target.get("automation_id"),
                control_type=target.get("role") or target.get("control_type"),
                class_name=target.get("className") or target.get("class_name"),
            )
            details["resolvedTarget"] = refreshed.as_dict()
            details["requestedTarget"] = dict(target or {})
            if action_type == "type_text":
                wrapper = self._resolve_wrapper_from_element(refreshed)
                return self._verify_type_text(
                    refreshed,
                    wrapper,
                    text=text,
                    details=details,
                    requested_target=target,
                    before_observation=before_observation,
                    after_observation=after_observation,
                )
            if action_type in {"click", "double_click"}:
                wrapper = self._resolve_wrapper_from_element(refreshed)
                return self._verify_click_focus(refreshed, wrapper, details=details)
            if action_type == "hotkey":
                wrapper = self._resolve_wrapper_from_element(refreshed)
                return self._verify_window_focus(refreshed, wrapper, details=details, action_type=action_type)
            if action_type == "scroll":
                return self._verify_scroll_change(
                    refreshed,
                    details=details,
                    before_observation=before_observation,
                    after_observation=after_observation,
                )
            return {
                "passed": True,
                "status": "verified",
                "reason": "动作完成后已重新定位到目标元素。",
                "details": details,
                "level": "verified",
            }
        except Exception as exc:
            details["error"] = str(exc)
            return {
                "passed": False,
                "status": "target_unresolved",
                "reason": f"动作完成后未能重新定位目标元素：{exc}",
                "details": details,
                "level": "failed",
            }

    def _resolve_target(self, *, element_id: str | None = None, **query: Any):
        if element_id:
            cached = self._element_cache.get(element_id)
            if cached:
                merged = dict(cached.query)
                merged.update({key: value for key, value in query.items() if value is not None})
                query = merged
                query.setdefault("preferred_backend", cached.backend)
            query["element_id"] = element_id
        wrapper, element = self._resolve_target_with_recovery(**query)
        if element_id and element.element_id != element_id:
            self._element_cache.pop(element_id, None)
        return wrapper, element

    def _resolve_target_with_recovery(self, **query: Any):
        self._bump_selector_metric("resolveCalls")
        preferred_backend = str(query.get("preferred_backend") or "uia").lower()
        backend_order = ["uia", "win32"]
        if preferred_backend == "win32":
            backend_order = ["win32", "uia"]

        last_error: Exception | None = None
        for backend_name in backend_order:
            try:
                return self._resolve_target_for_backend(backend_name=backend_name, **query)
            except Exception as exc:
                last_error = exc
                continue
        self._bump_selector_metric("resolveFailures")
        raise WindowsUIADriverError(str(last_error or "目标元素已失效，请重新观察界面。"))

    def _resolve_target_for_backend(self, *, backend_name: str, **query: Any):
        root = self._resolve_root(
            window_title=query.get("window_title"),
            window_handle=query.get("window_handle"),
            backend_name=backend_name,
        )
        root_handle = getattr(root.element_info, "handle", None)
        if backend_name == "uia" and root_handle is not None:
            cached_matches = self._find_cached_elements(
                window_handle=int(root_handle),
                element_id=query.get("element_id"),
                name=query.get("name"),
                name_contains=query.get("name_contains"),
                target_text=query.get("target_text"),
                automation_id=query.get("automation_id"),
                control_type=query.get("control_type"),
                class_name=query.get("class_name"),
                limit=6,
            )
            for element in cached_matches:
                wrapper = self._resolve_cached_wrapper(root, expected=element, backend_name=backend_name)
                if wrapper is not None:
                    self._bump_selector_metric("resolveCacheHits")
                    return wrapper, element
        wrappers = self._query_wrappers_fast(
            root,
            backend_name=backend_name,
            name=query.get("name"),
            name_contains=query.get("name_contains"),
            target_text=query.get("target_text"),
            automation_id=query.get("automation_id"),
            control_type=query.get("control_type"),
            class_name=query.get("class_name"),
            limit=6,
        )
        for wrapper in wrappers:
            try:
                element = self._build_element(wrapper, backend_name=backend_name)
                if backend_name == "win32":
                    self._bump_selector_metric("win32FallbackHits")
                else:
                    self._bump_selector_metric("resolveFastHits")
                return wrapper, element
            except Exception:
                continue

        matches = self._find_elements_via_backend_scan(
            backend_name=backend_name,
            window_title=query.get("window_title"),
            window_handle=query.get("window_handle"),
            name=query.get("name"),
            name_contains=query.get("name_contains"),
            target_text=query.get("target_text"),
            automation_id=query.get("automation_id"),
            control_type=query.get("control_type"),
            class_name=query.get("class_name"),
            depth_limit=12 if backend_name == "uia" and (query.get("control_type") or query.get("class_name")) else 6,
            limit=4,
        )
        if not matches:
            if backend_name == "win32":
                self._bump_selector_metric("win32FallbackMisses")
            raise WindowsUIADriverError("未找到匹配的 UI 元素。")
        for element in matches:
            try:
                wrapper = self._resolve_wrapper_from_element(element)
                if backend_name == "win32":
                    self._bump_selector_metric("win32FallbackHits")
                else:
                    self._bump_selector_metric("resolveRecoveryHits")
                return wrapper, element
            except Exception:
                continue
        if backend_name == "win32":
            self._bump_selector_metric("win32FallbackMisses")
        raise WindowsUIADriverError("目标元素已失效，请重新观察界面。")

    def _resolve_wrapper_from_element(self, element: ComputerUseElement):
        backend_name = "win32" if element.backend.endswith("win32") else "uia"
        root = self._resolve_root(window_handle=element.window_handle, backend_name=backend_name)
        direct_wrapper = self._resolve_wrapper_by_handle(expected=element, backend_name=backend_name)
        if direct_wrapper is not None:
            return direct_wrapper
        selector = self._selector_from_element(element, backend_name=backend_name)
        if element.window_handle is not None:
            cached_matches = self._find_cached_elements(
                window_handle=int(element.window_handle),
                element_id=element.element_id,
                name=selector.get("name"),
                automation_id=selector.get("automation_id"),
                control_type=selector.get("control_type"),
                class_name=selector.get("class_name"),
                limit=8,
            )
            for candidate in cached_matches:
                wrapper = self._resolve_cached_wrapper(root, expected=candidate, backend_name=backend_name)
                if wrapper is not None:
                    return wrapper
        cached = self._element_cache.get(element.element_id)
        query = dict(cached.query) if cached else {}
        if query:
            if backend_name == "win32" and query.get("automation_id"):
                role_lower = (element.role or "").lower()
                class_lower = (element.class_name or "").lower()
                if "textbox" in role_lower or ".edit." in class_lower:
                    query["name"] = None
            wrappers = self._query_wrappers_fast(
                root,
                backend_name=backend_name,
                name=query.get("name"),
                automation_id=query.get("automation_id"),
                control_type=query.get("control_type"),
                class_name=query.get("class_name"),
                limit=8,
            )
            best = self._pick_best_wrapper(wrappers, expected=element, backend_name=backend_name)
            if best is not None:
                return best
        scanned_wrapper = self._scan_wrapper_tree_for_element(root, expected=element, backend_name=backend_name)
        if scanned_wrapper is not None:
            return scanned_wrapper

        observation = self.observe_desktop(
            window_handle=element.window_handle,
            depth_limit=max(len(element.path) + 1, 4),
            element_limit=160,
            use_cache=False,
        )
        candidates = [
            item
            for item in observation.elements
            if (
                item.automation_id
                and element.automation_id
                and item.automation_id == element.automation_id
            )
            or (item.name == element.name and item.role == element.role)
        ]
        for candidate in candidates:
            candidate_selector = self._selector_from_element(candidate, backend_name=backend_name)
            wrappers = self._query_wrappers_fast(
                root,
                backend_name=backend_name,
                name=candidate_selector.get("name"),
                automation_id=candidate_selector.get("automation_id"),
                control_type=candidate_selector.get("control_type"),
                class_name=candidate_selector.get("class_name"),
                limit=6,
            )
            best = self._pick_best_wrapper(wrappers, expected=candidate, backend_name=backend_name)
            if best is not None:
                return best
        if backend_name == "uia":
            win32_element = ComputerUseElement(
                element_id=element.element_id,
                backend="windows_win32",
                role=element.role,
                name=element.name,
                bounds=list(element.bounds),
                actions=list(element.actions),
                confidence=element.confidence,
                path=list(element.path),
                automation_id=element.automation_id,
                class_name=element.class_name,
                window_handle=element.window_handle,
                metadata=dict(element.metadata),
            )
            return self._resolve_wrapper_from_element(win32_element)
        raise WindowsUIADriverError("目标元素已失效，请重新观察界面。")

    def _scan_wrapper_tree_for_element(self, root, *, expected: ComputerUseElement, backend_name: str):
        queue: deque[tuple[Any, int]] = deque([(root, 0)])
        visited: set[str] = set()
        max_depth = 12 if backend_name == "uia" else 8
        while queue:
            wrapper, depth = queue.popleft()
            signature = self._wrapper_signature(wrapper)
            if signature in visited:
                continue
            visited.add(signature)
            try:
                candidate = self._build_element(wrapper, backend_name=backend_name)
            except Exception:
                candidate = None
            if candidate is not None and self._wrapper_candidate_matches(candidate, expected):
                return wrapper
            if depth >= max_depth:
                continue
            try:
                children = wrapper.children()
            except Exception:
                children = []
            for child in children:
                queue.append((child, depth + 1))
        return None

    def _wrapper_candidate_matches(self, candidate: ComputerUseElement, expected: ComputerUseElement) -> bool:
        if candidate.element_id == expected.element_id:
            return True
        candidate_handle = (candidate.metadata or {}).get("handle")
        expected_handle = (expected.metadata or {}).get("handle")
        if candidate_handle not in (None, 0) and candidate_handle == expected_handle:
            return True
        if candidate.automation_id and expected.automation_id and candidate.automation_id == expected.automation_id:
            if candidate.class_name.lower() == expected.class_name.lower():
                return True
        candidate_role = self._normalize_control_type(candidate.role, candidate.class_name)
        expected_role = self._normalize_control_type(expected.role, expected.class_name)
        if candidate_role != expected_role:
            return False
        if candidate.class_name.lower() != expected.class_name.lower():
            return False
        if expected.name and candidate.name != expected.name:
            return False
        return candidate.window_handle == expected.window_handle

    def _cache_window_index(self, window_handle: int, *, elements: List[ComputerUseElement], observed_at: float) -> None:
        by_element_id: Dict[str, ComputerUseElement] = {}
        by_automation_id: Dict[str, List[ComputerUseElement]] = {}
        by_name: Dict[str, List[ComputerUseElement]] = {}
        by_role: Dict[str, List[ComputerUseElement]] = {}
        by_class_name: Dict[str, List[ComputerUseElement]] = {}
        by_path_tail: Dict[str, List[ComputerUseElement]] = {}
        for element in elements:
            by_element_id[element.element_id] = element
            if element.automation_id:
                by_automation_id.setdefault(element.automation_id, []).append(element)
            if element.name:
                by_name.setdefault(element.name.lower(), []).append(element)
            if element.role:
                by_role.setdefault(element.role.lower(), []).append(element)
            if element.class_name:
                by_class_name.setdefault(element.class_name.lower(), []).append(element)
            for path_key in self._path_index_keys(element):
                by_path_tail.setdefault(path_key, []).append(element)
        self._window_index_cache[window_handle] = _WindowIndexEntry(
            observed_at=observed_at,
            elements=list(elements),
            by_element_id=by_element_id,
            by_automation_id=by_automation_id,
            by_name=by_name,
            by_role=by_role,
            by_class_name=by_class_name,
            by_path_tail=by_path_tail,
        )

    def _get_window_index(self, window_handle: int | None) -> _WindowIndexEntry | None:
        if window_handle is None:
            return None
        cached = self._window_index_cache.get(int(window_handle))
        if cached is None:
            return None
        if (time.time() - cached.observed_at) > self._window_index_ttl_seconds:
            self._window_index_cache.pop(int(window_handle), None)
            return None
        return cached

    def _find_cached_elements(
        self,
        *,
        window_handle: int | None,
        element_id: str | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        target_text: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
        limit: int = 20,
    ) -> List[ComputerUseElement]:
        index = self._get_window_index(window_handle)
        if index is None:
            return []
        candidates: List[ComputerUseElement]
        if element_id and element_id in index.by_element_id:
            candidates = [index.by_element_id[element_id]]
        elif automation_id and automation_id in index.by_automation_id:
            candidates = list(index.by_automation_id.get(automation_id) or [])
        elif name and name.lower() in index.by_name:
            candidates = list(index.by_name.get(name.lower()) or [])
        elif target_text:
            exact_key = self._normalize_window_text(target_text)
            candidates = list(index.by_name.get(exact_key) or [])
            if not candidates:
                compact_target = self._compact_match_text(target_text)
                candidates = [
                    item
                    for item in index.elements
                    if self._compact_match_text(item.name) == compact_target
                ]
            if not candidates and name_contains:
                token = name_contains.lower()
                for key, items in index.by_path_tail.items():
                    if token in key:
                        candidates.extend(items)
                if not candidates:
                    candidates = [item for item in index.elements if token in item.name.lower()]
            elif not candidates:
                candidates = list(index.elements)
        elif name_contains:
            token = name_contains.lower()
            candidates = []
            for key, items in index.by_path_tail.items():
                if token in key:
                    candidates.extend(items)
            if not candidates:
                candidates = [item for item in index.elements if token in item.name.lower()]
        elif class_name and class_name.lower() in index.by_class_name:
            candidates = list(index.by_class_name.get(class_name.lower()) or [])
        elif control_type and control_type.lower() in index.by_role:
            candidates = list(index.by_role.get(control_type.lower()) or [])
        else:
            candidates = list(index.elements)

        ranked: List[tuple[int, ComputerUseElement]] = []
        for candidate in candidates:
            score = self._score_cached_element(
                candidate,
                name=name,
                name_contains=name_contains,
                target_text=target_text,
                automation_id=automation_id,
                control_type=control_type,
                class_name=class_name,
            )
            if score > 0:
                ranked.append((score, candidate))
        ranked.sort(
            key=lambda item: (
                -item[0],
                item[1].metadata.get("isVisible") is False,
                item[1].metadata.get("isEnabled") is False,
            )
        )
        return [candidate for _score, candidate in ranked[: max(1, limit)]]

    def selector_metrics(self, *, reset: bool = False) -> Dict[str, Any]:
        metrics = dict(self._selector_metrics)
        find_calls = max(1, metrics.get("findCalls", 0))
        resolve_calls = max(1, metrics.get("resolveCalls", 0))
        metrics["windowIndexHitRate"] = round(metrics.get("windowIndexHits", 0) / find_calls, 4)
        metrics["fastQueryHitRate"] = round(metrics.get("fastQueryHits", 0) / find_calls, 4)
        metrics["resolveRecoveryRate"] = round(metrics.get("resolveRecoveryHits", 0) / resolve_calls, 4)
        metrics["windowIndexSize"] = len(self._window_index_cache)
        metrics["selectorHintCacheSize"] = sum(len(items) for items in self._selector_hint_cache.values())
        if reset:
            for key in self._selector_metrics:
                self._selector_metrics[key] = 0
        return metrics

    def _bump_selector_metric(self, key: str, delta: int = 1) -> None:
        self._selector_metrics[key] = self._selector_metrics.get(key, 0) + delta

    def _get_selector_hints(self, window_handle: int | None) -> List[_SelectorHintEntry]:
        if window_handle is None:
            return []
        bucket = self._selector_hint_cache.get(int(window_handle)) or []
        now = time.time()
        active = [item for item in bucket if (now - item.observed_at) <= self._selector_hint_ttl_seconds]
        if len(active) != len(bucket):
            if active:
                self._selector_hint_cache[int(window_handle)] = active
            else:
                self._selector_hint_cache.pop(int(window_handle), None)
        return active

    def _normalize_selector_hint(self, selector: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for source_key, target_key in (
            ("name", "name"),
            ("automation_id", "automation_id"),
            ("automationId", "automation_id"),
            ("control_type", "control_type"),
            ("controlType", "control_type"),
            ("class_name", "class_name"),
            ("className", "class_name"),
            ("handle", "handle"),
        ):
            value = selector.get(source_key)
            if isinstance(value, str) and value.strip():
                normalized[target_key] = value.strip()
            elif target_key == "handle" and value not in (None, ""):
                try:
                    normalized[target_key] = int(value)
                except Exception:
                    continue
        if normalized.get("control_type") or normalized.get("class_name"):
            normalized["control_type"] = self._normalize_control_type(
                normalized.get("control_type"),
                normalized.get("class_name"),
            )
        return normalized

    def _path_index_keys(self, element: ComputerUseElement) -> List[str]:
        path = [str(item).strip().lower() for item in element.path if str(item).strip()]
        if not path:
            return []
        keys = {path[-1]}
        if len(path) >= 2:
            keys.add(" > ".join(path[-2:]))
        if len(path) >= 3:
            keys.add(" > ".join(path[-3:]))
        return sorted(keys)

    def _score_cached_element(
        self,
        element: ComputerUseElement,
        *,
        name: str | None = None,
        name_contains: str | None = None,
        target_text: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
    ) -> int:
        expected_control_type = self._normalize_control_type(control_type, class_name)
        actual_control_type = self._normalize_control_type(element.role, element.class_name)
        if automation_id and element.automation_id != automation_id:
            return 0
        if name and element.name != name:
            return 0
        if name_contains and name_contains.lower() not in element.name.lower():
            return 0
        if expected_control_type and actual_control_type != expected_control_type:
            return 0
        if class_name and element.class_name.lower() != class_name.lower():
            return 0
        target_text_score = self._target_text_match_score(element.name, target_text)
        if target_text and target_text_score < 0:
            return 0

        score = 0
        if automation_id:
            score += 90
        if name:
            score += 70
        if name_contains:
            score += 50
        if target_text_score > 0:
            score += target_text_score
        if control_type:
            score += 30
        if class_name:
            score += 20
        if element.metadata.get("isVisible"):
            score += 6
        if element.metadata.get("isEnabled"):
            score += 4
        return score

    def _resolve_cached_wrapper(self, root, *, expected: ComputerUseElement, backend_name: str = "uia"):
        direct_wrapper = self._resolve_wrapper_by_handle(expected=expected, backend_name=backend_name)
        if direct_wrapper is not None:
            return direct_wrapper
        selector = self._selector_from_element(expected, backend_name=backend_name)
        wrappers = self._query_wrappers_fast(
            root,
            backend_name=backend_name,
            name=selector.get("name"),
            automation_id=selector.get("automation_id"),
            control_type=selector.get("control_type"),
            class_name=selector.get("class_name"),
            limit=8,
        )
        return self._pick_best_wrapper(wrappers, expected=expected, backend_name=backend_name)

    def _selector_from_element(self, element: ComputerUseElement, *, backend_name: str) -> Dict[str, Any]:
        selector = {
            "name": element.name or None,
            "automation_id": element.automation_id or None,
            "control_type": self._normalize_control_type(element.role, element.class_name) or None,
            "class_name": element.class_name or None,
            "handle": (element.metadata or {}).get("handle"),
        }
        if backend_name == "win32" and selector["automation_id"]:
            role_lower = (element.role or "").lower()
            class_lower = (element.class_name or "").lower()
            if "textbox" in role_lower or ".edit." in class_lower:
                selector["name"] = None
        return selector

    def _resolve_wrapper_by_handle(self, *, expected: ComputerUseElement, backend_name: str = "uia"):
        handle = (expected.metadata or {}).get("handle")
        if handle in (None, ""):
            return None
        try:
            wrapper = self._desktop_for_backend(backend_name).window(handle=int(handle)).wrapper_object()
        except Exception:
            return None
        try:
            top = wrapper.top_level_parent()
            top_handle = getattr(top.element_info, "handle", None)
            if expected.window_handle is not None and top_handle not in (None, int(expected.window_handle)):
                return None
        except Exception:
            return None
        return wrapper

    def _normalize_control_type(self, role: str | None, class_name: str | None = None) -> str:
        role_lower = str(role or "").strip().lower()
        class_lower = str(class_name or "").strip().lower()
        if not role_lower and not class_lower:
            return ""
        if "textbox" in role_lower or role_lower == "edit" or ".edit." in class_lower:
            return "edit"
        if "button" in role_lower:
            return "button"
        if "document" in role_lower:
            return "document"
        if "combobox" in role_lower:
            return "combobox"
        if "window" in role_lower:
            return "window"
        if "pane" in role_lower:
            return "pane"
        return role_lower or class_lower

    def _value_pattern_for_text_input(self, wrapper):
        try:
            iface_value = getattr(wrapper, "iface_value", None)
        except Exception:
            return None
        if iface_value is None or not hasattr(iface_value, "SetValue"):
            return None
        try:
            if bool(getattr(iface_value, "CurrentIsReadOnly", False)):
                return None
        except Exception:
            pass
        return iface_value

    def _supports_direct_text_input(self, wrapper, element: ComputerUseElement) -> bool:
        control_type = self._normalize_control_type(element.role, element.class_name)
        if control_type not in {"edit", "document", "combobox"}:
            return False
        if hasattr(wrapper, "set_edit_text"):
            return True
        return self._value_pattern_for_text_input(wrapper) is not None

    def _looks_like_browser_input(
        self,
        metadata: Dict[str, Any],
        control_type: str,
        *,
        class_name: str | None = None,
        process_name: str | None = None,
    ) -> bool:
        if metadata.get("isEditable") is True or metadata.get("isContentEditable") is True:
            return True
        tag_name = str(metadata.get("tagName") or metadata.get("htmlTag") or "").strip().lower()
        input_type = str(metadata.get("inputType") or "").strip().lower()
        role_hint = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        class_lower = str(class_name or "").strip().lower()
        process_lower = str(process_name or "").strip().lower()
        if tag_name in {"input", "textarea", "select"}:
            return True
        if input_type:
            return True
        if role_hint in {"textbox", "searchbox", "combobox", "spinbutton"}:
            return True
        if control_type != "document":
            return False
        browser_classes = (
            "chrome_renderwidgethosthwnd",
            "internet explorer_server",
            "cef",
            "webview",
            "chrome widget",
        )
        if any(token in class_lower for token in browser_classes):
            return True
        browser_processes = {
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "brave.exe",
            "opera.exe",
            "vivaldi.exe",
            "qqbrowser.exe",
            "360se.exe",
            "electron.exe",
        }
        return process_lower in browser_processes

    def _text_input_capability(self, wrapper, element: ComputerUseElement) -> Dict[str, Any]:
        metadata = dict(element.metadata or {})
        control_type = self._normalize_control_type(element.role, element.class_name)
        keyboard_supported = hasattr(wrapper, "type_keys")
        try:
            top = wrapper.top_level_parent()
        except Exception:
            top = wrapper
        process_name = self._window_process_name(top)
        value_pattern = self._value_pattern_for_text_input(wrapper)
        if self._supports_direct_text_input(wrapper, element):
            return {
                "allowed": True,
                "direct": True,
                "status": "editable_direct_target",
                "controlType": control_type,
                "directMethod": "value_pattern" if value_pattern is not None else "set_edit_text",
                "processName": process_name or None,
            }
        if keyboard_supported and control_type in {"edit", "combobox"}:
            return {
                "allowed": True,
                "direct": False,
                "status": "editable_keyboard_target",
                "controlType": control_type,
                "processName": process_name or None,
            }
        if keyboard_supported and control_type == "document" and not self._looks_like_browser_input(
            metadata,
            control_type,
            class_name=element.class_name,
            process_name=process_name,
        ):
            return {
                "allowed": True,
                "direct": False,
                "status": "editable_document_target",
                "controlType": control_type,
                "processName": process_name or None,
            }
        if keyboard_supported and self._looks_like_browser_input(
            metadata,
            control_type,
            class_name=element.class_name,
            process_name=process_name,
        ):
            return {
                "allowed": True,
                "direct": False,
                "status": "review_required_dynamic_input",
                "controlType": control_type,
                "tagName": metadata.get("tagName") or metadata.get("htmlTag"),
                "inputType": metadata.get("inputType"),
                "roleHint": metadata.get("role") or metadata.get("ariaRole"),
                "processName": process_name or None,
                "reason": "目标控件属于动态或浏览器输入区域，仅允许保守键盘输入。",
            }
        return {
            "allowed": False,
            "direct": False,
            "status": "blocked_non_editable_target",
            "controlType": control_type,
            "reason": f"目标控件类型 `{control_type or element.role or 'unknown'}` 不是可编辑输入控件。",
        }

    def _set_direct_text(
        self,
        *,
        wrapper,
        element: ComputerUseElement,
        text: str,
        clear_first: bool,
    ) -> None:
        current_text = self._read_wrapper_text(wrapper)
        target_text = text if clear_first else f"{current_text}{text}"
        value_pattern = self._value_pattern_for_text_input(wrapper)
        if value_pattern is not None:
            value_pattern.SetValue(target_text)
            return
        set_edit_text = getattr(wrapper, "set_edit_text", None)
        if callable(set_edit_text):
            set_edit_text(target_text)
            return
        raise WindowsUIADriverError(
            f"目标控件 `{self._normalize_control_type(element.role, element.class_name) or element.role or 'unknown'}` 缺少可用的 direct text writer。"
        )

    def _is_dynamic_text_control(
        self,
        *,
        role: str | None,
        class_name: str | None,
        automation_id: str | None = None,
        handle: Any | None = None,
    ) -> bool:
        control_type = self._normalize_control_type(role, class_name)
        if control_type not in {"edit", "document", "combobox"}:
            return False
        return bool(automation_id or handle)

    def _resolve_root_resilient(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        backend_name: str = "uia",
    ):
        try:
            return self._resolve_root(window_title=window_title, window_handle=window_handle, backend_name=backend_name)
        except Exception as exc:
            if backend_name == "uia":
                self._bump_selector_metric("win32FallbackHits")
                return self._resolve_root(window_title=window_title, window_handle=window_handle, backend_name="win32")
            raise WindowsUIADriverError(str(exc))

    def _resolve_root(self, *, window_title: str | None = None, window_handle: int | None = None, backend_name: str = "uia"):
        now = time.time()
        if window_handle:
            cache_key = self._window_cache_key(backend_name=backend_name, window_handle=int(window_handle))
            cached = self._window_cache.get(cache_key)
            if cached and (now - cached[0]) <= self._root_cache_ttl_seconds:
                return cached[1]
            wrapper = self._desktop_for_backend(backend_name).window(handle=int(window_handle)).wrapper_object()
            self._window_cache[cache_key] = (now, wrapper)
            return wrapper
        if window_title:
            expected_title = self._normalize_window_text(window_title)
            for wrapper in self._safe_backend_windows(backend_name):
                title = (wrapper.window_text() or "").strip()
                title_score = window_title_match_score(title, [expected_title]) if expected_title else 0
                if title_score >= 100:
                    handle = getattr(wrapper.element_info, "handle", None)
                    if handle is not None:
                        cache_key = self._window_cache_key(backend_name=backend_name, window_handle=int(handle))
                        self._window_cache[cache_key] = (now, wrapper)
                    return wrapper
            raise WindowsUIADriverError(f"未找到匹配窗口：{window_title}")
        windows = self._safe_backend_windows(backend_name)
        if not windows:
            raise WindowsUIADriverError("当前桌面上没有可访问的顶层窗口。")
        visible = [item for item in windows if (item.window_text() or "").strip()]
        wrapper = visible[0] if visible else windows[0]
        handle = getattr(wrapper.element_info, "handle", None)
        if handle is not None:
            cache_key = self._window_cache_key(backend_name=backend_name, window_handle=int(handle))
            self._window_cache[cache_key] = (now, wrapper)
        return wrapper

    def _enumerate_elements(self, root, *, depth_limit: int, limit: int, backend_name: str = "uia") -> List[ComputerUseElement]:
        items: List[ComputerUseElement] = []
        root_title = (root.window_text() or "").strip()
        queue: deque[tuple[Any, int]] = deque([(root, 0)])
        visited: set[str] = set()
        max_depth = max(depth_limit, 1)
        max_items = max(limit, 1)

        while queue and len(items) < max_items:
            wrapper, depth = queue.popleft()
            signature = self._wrapper_signature(wrapper)
            if signature in visited:
                continue
            visited.add(signature)
            element = self._build_element(wrapper, root_title=root_title, backend_name=backend_name)
            if not element.name and element.role.lower() not in {"edit", "document", "pane", "window"}:
                pass
            elif depth <= max_depth:
                items.append(element)

            if depth >= max_depth:
                continue
            try:
                children = wrapper.children()
            except Exception:
                children = []
            for child in children:
                queue.append((child, depth + 1))
        return items

    def _build_element(self, wrapper, *, root_title: str | None = None, backend_name: str = "uia") -> ComputerUseElement:
        info = wrapper.element_info
        name = (wrapper.window_text() or getattr(info, "name", "") or "").strip()
        role = (
            getattr(info, "control_type", "")
            or getattr(info, "friendly_class_name", "")
            or getattr(wrapper, "friendly_class_name", lambda: "")()
            or "unknown"
        )
        class_name = getattr(info, "class_name", "") or ""
        automation_id = getattr(info, "automation_id", "") or ""
        bounds = self._rect_to_bounds(wrapper.rectangle())
        path = []
        try:
            current = wrapper
            while current is not None:
                current_info = current.element_info
                current_name = (current.window_text() or getattr(current_info, "name", "") or "").strip()
                current_role = getattr(current_info, "control_type", "") or "node"
                label = current_name or current_role
                path.append(f"{current_role}:{label}")
                parent = current.parent()
                if parent is None or parent == current:
                    break
                current = parent
            path = list(reversed(path))
        except Exception:
            if root_title:
                path = [f"window:{root_title}", f"{role}:{name or class_name or automation_id or 'element'}"]

        handle = getattr(info, "handle", None)
        top_handle = None
        try:
            top_handle = getattr(wrapper.top_level_parent().element_info, "handle", None)
        except Exception:
            top_handle = handle
        is_dynamic_text_control = self._is_dynamic_text_control(
            role=role,
            class_name=class_name,
            automation_id=automation_id,
            handle=handle,
        )
        identity_name = None if is_dynamic_text_control else name
        normalized_control_type = self._normalize_control_type(role, class_name)
        key_payload = {
            "backend": backend_name,
            "window_handle": top_handle,
            "name": identity_name,
            "automation_id": automation_id,
            "role": normalized_control_type or role,
            "class_name": class_name,
            "bounds": bounds,
            "handle": handle if is_dynamic_text_control else None,
        }
        element_id = hashlib.md5(json.dumps(key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        self._element_cache[element_id] = _CachedQuery(
            query={
                "window_handle": top_handle,
                "name": None if is_dynamic_text_control else (name or None),
                "automation_id": automation_id or None,
                "control_type": normalized_control_type or role or None,
                "class_name": class_name or None,
                "handle": handle,
            },
            window_handle=top_handle,
            backend=backend_name,
        )
        return ComputerUseElement(
            element_id=element_id,
            backend=f"windows_{backend_name}",
            role=role,
            name=name,
            bounds=bounds,
            actions=self._infer_actions(role, wrapper),
            confidence=0.99 if automation_id else 0.92,
            path=path,
            automation_id=automation_id,
            class_name=class_name,
            window_handle=top_handle,
            metadata={
                "handle": handle,
                "isEnabled": self._safe_bool(wrapper, "is_enabled"),
                "isVisible": self._safe_bool(wrapper, "is_visible"),
            },
        )

    def _window_dict(self, wrapper) -> Dict[str, Any]:
        info = wrapper.element_info
        return {
            "title": (wrapper.window_text() or "").strip(),
            "handle": getattr(info, "handle", None),
            "processId": self._window_process_id(wrapper),
            "processName": self._window_process_name(wrapper),
            "className": getattr(info, "class_name", "") or "",
            "controlType": getattr(info, "control_type", "") or "Window",
            "bounds": self._rect_to_bounds(wrapper.rectangle()),
            "isVisible": self._safe_bool(wrapper, "is_visible"),
            "isEnabled": self._safe_bool(wrapper, "is_enabled"),
        }

    def _window_process_id(self, wrapper) -> int | None:
        try:
            process_method = getattr(wrapper, "process_id", None)
            if callable(process_method):
                value = process_method()
                if value not in (None, ""):
                    return int(value)
        except Exception:
            pass
        value = getattr(getattr(wrapper, "element_info", None), "process_id", None)
        if value not in (None, ""):
            try:
                return int(value)
            except Exception:
                return None
        return None

    def _window_process_name(self, wrapper) -> str:
        pid = self._window_process_id(wrapper)
        if pid in (None, 0):
            return ""
        try:
            return str(psutil.Process(int(pid)).name() or "").strip()
        except Exception:
            return ""

    def _display_context_for_bounds(
        self,
        bounds: List[int],
        *,
        window_handle: int | None = None,
    ) -> Dict[str, Any]:
        display_bounds = self._primary_display_bounds()
        display_id = "DISPLAY1"
        center_x, center_y = self._center_of_bounds(bounds)
        if mss is not None:
            try:
                with mss.mss() as sct:
                    monitors = list(sct.monitors)[1:] or list(sct.monitors)
                    for index, monitor in enumerate(monitors, start=1):
                        left = int(monitor.get("left", 0))
                        top = int(monitor.get("top", 0))
                        right = left + int(monitor.get("width", 0))
                        bottom = top + int(monitor.get("height", 0))
                        if left <= center_x <= right and top <= center_y <= bottom:
                            display_bounds = [left, top, right, bottom]
                            display_id = f"DISPLAY{index}"
                            break
            except Exception:
                pass
        dpi_scale = 1.0
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                get_dpi = getattr(user32, "GetDpiForWindow", None)
                if callable(get_dpi) and window_handle not in (None, ""):
                    dpi_value = int(get_dpi(int(window_handle)))
                    if dpi_value > 0:
                        dpi_scale = round(dpi_value / 96.0, 3)
            except Exception:
                pass
        return {
            "displayId": display_id,
            "displayBounds": display_bounds,
            "dpiScale": dpi_scale,
        }

    def _primary_display_bounds(self) -> List[int]:
        if os.name != "nt":
            return [0, 0, 1920, 1080]
        try:
            user32 = ctypes.windll.user32
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            return [0, 0, max(1, width), max(1, height)]
        except Exception:
            return [0, 0, 1920, 1080]

    def _query_elements_fast(
        self,
        root,
        *,
        backend_name: str = "uia",
        name: str | None = None,
        name_contains: str | None = None,
        target_text: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
        limit: int,
    ) -> List[ComputerUseElement]:
        wrappers = self._query_wrappers_fast(
            root,
            backend_name=backend_name,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            limit=limit,
        )
        elements: List[ComputerUseElement] = []
        for wrapper in wrappers:
            try:
                elements.append(self._build_element(wrapper, backend_name=backend_name))
            except Exception:
                continue
        return elements[: max(1, limit)]

    def _query_wrappers_fast(
        self,
        root,
        *,
        backend_name: str = "uia",
        name: str | None = None,
        name_contains: str | None = None,
        target_text: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
        limit: int = 8,
    ) -> List[Any]:
        selector = {
            "name": name,
            "name_contains": name_contains,
            "target_text": target_text,
            "automation_id": automation_id,
            "control_type": control_type,
            "class_name": class_name,
        }
        root_handle = getattr(getattr(root, "element_info", None), "handle", None)
        selector_hints = self._get_selector_hints(root_handle)
        hard_limit = max(max(1, limit) * 18, 120)
        search_depth = 2 if automation_id else 3
        if name_contains and not automation_id:
            search_depth = 4
        if backend_name == "uia" and (control_type or class_name) and not automation_id:
            search_depth = max(search_depth, 5)

        candidates: List[tuple[int, int, Any]] = []
        seen: set[str] = set()

        def _remember(candidate, score: int, depth: int) -> None:
            signature = self._wrapper_signature(candidate)
            if signature in seen:
                return
            seen.add(signature)
            candidates.append((score, depth, candidate))

        for direct_depth, candidate in self._query_wrappers_direct(root, selector, backend_name=backend_name).items():
            score = self._score_wrapper_for_selector(candidate, selector)
            if score > 0:
                self._bump_selector_metric("directQueryHits")
                _remember(candidate, score + 40, direct_depth)

        if selector_hints:
            self._bump_selector_metric("selectorHintQueries")
        for hint in selector_hints:
            hinted_selector = {
                "name": hint.selector.get("name") or name,
                "automation_id": hint.selector.get("automation_id") or automation_id,
                "control_type": hint.selector.get("control_type") or control_type,
                "class_name": hint.selector.get("class_name") or class_name,
                "handle": hint.selector.get("handle"),
                "target_text": target_text,
            }
            for direct_depth, candidate in self._query_wrappers_direct(
                root,
                hinted_selector,
                backend_name=backend_name,
            ).items():
                hinted_score = self._score_wrapper_for_selector(candidate, hinted_selector)
                primary_score = self._score_wrapper_for_selector(candidate, selector)
                if hinted_score > 0 and primary_score > 0:
                    self._bump_selector_metric("selectorHintHits")
                    _remember(candidate, primary_score + 60 + hint.weight, max(0, direct_depth - 1))

        queue: deque[tuple[Any, int]] = deque([(root, 0)])
        scanned = 0
        while queue and scanned < hard_limit:
            wrapper, depth = queue.popleft()
            if depth > search_depth:
                continue
            try:
                children = wrapper.children()
            except Exception:
                children = []
            for child in children:
                scanned += 1
                score = self._score_wrapper_for_selector(child, selector)
                score += self._score_wrapper_against_hints(child, selector_hints)
                if score > 0:
                    _remember(child, score, depth + 1)
                if depth + 1 < search_depth:
                    queue.append((child, depth + 1))
                if scanned >= hard_limit:
                    break

        candidates.sort(
            key=lambda item: (
                -item[0],
                item[1],
                self._safe_bool(item[2], "is_visible") is False,
                self._safe_bool(item[2], "is_enabled") is False,
            )
        )
        return [candidate for _score, _depth, candidate in candidates[: max(1, limit)]]

    def _score_wrapper_against_hints(self, wrapper, hints: List[_SelectorHintEntry]) -> int:
        best_boost = 0
        for hint in hints:
            score = self._score_wrapper_for_selector(wrapper, hint.selector)
            if score <= 0:
                continue
            boost = min(90, 16 + hint.weight + max(1, score // 6))
            if boost > best_boost:
                best_boost = boost
        if best_boost > 0:
            self._bump_selector_metric("selectorHintBoosts")
        return best_boost

    def _pick_best_wrapper(self, wrappers: List[Any], *, expected: ComputerUseElement, backend_name: str = "uia"):
        best_wrapper = None
        best_score = -1
        expected_control_type = self._normalize_control_type(expected.role, expected.class_name)
        for wrapper in wrappers:
            try:
                candidate = self._build_element(wrapper, backend_name=backend_name)
            except Exception:
                continue
            score = 0
            if (candidate.metadata or {}).get("handle") == (expected.metadata or {}).get("handle"):
                score += 12
            if candidate.automation_id and candidate.automation_id == expected.automation_id:
                score += 8
            if candidate.name and candidate.name == expected.name:
                score += 4
            if self._normalize_control_type(candidate.role, candidate.class_name) == expected_control_type:
                score += 3
            if candidate.class_name and candidate.class_name == expected.class_name:
                score += 2
            if candidate.window_handle == expected.window_handle:
                score += 1
            if score > best_score:
                best_score = score
                best_wrapper = wrapper
        return best_wrapper

    def _query_wrappers_direct(self, root, selector: Dict[str, Any], *, backend_name: str = "uia") -> Dict[Any, int]:
        queries: List[Dict[str, Any]] = []
        handle = selector.get("handle")
        automation_id = selector.get("automation_id")
        name = selector.get("name")
        control_type = selector.get("control_type")
        class_name = selector.get("class_name")

        if handle not in (None, ""):
            try:
                candidate = self._desktop_for_backend(backend_name).window(handle=int(handle)).wrapper_object()
                return {candidate: 0}
            except Exception:
                pass

        if backend_name == "win32":
            if automation_id and class_name:
                queries.append({"auto_id": automation_id, "class_name": class_name})
            if automation_id:
                queries.append({"auto_id": automation_id})
            if name and class_name:
                queries.append({"title": name, "class_name": class_name})
            if name:
                queries.append({"title": name})
            if class_name:
                queries.append({"class_name": class_name})
        else:
            if automation_id and control_type and class_name:
                queries.append({"auto_id": automation_id, "control_type": control_type, "class_name": class_name})
            if automation_id and control_type:
                queries.append({"auto_id": automation_id, "control_type": control_type})
            if automation_id:
                queries.append({"auto_id": automation_id})
            if name and control_type and class_name:
                queries.append({"title": name, "control_type": control_type, "class_name": class_name})
            if name and control_type:
                queries.append({"title": name, "control_type": control_type})
            if name:
                queries.append({"title": name})

        results: Dict[Any, int] = {}
        for depth, query in enumerate(queries, start=1):
            try:
                candidate = root.child_window(**query).wrapper_object()
            except Exception:
                continue
            results[candidate] = min(results.get(candidate, depth), depth)
        return results

    def _score_wrapper_for_selector(self, wrapper, selector: Dict[str, Any]) -> int:
        try:
            info = wrapper.element_info
        except Exception:
            return 0
        name = (wrapper.window_text() or getattr(info, "name", "") or "").strip()
        automation_id = getattr(info, "automation_id", "") or ""
        control_type = (
            getattr(info, "control_type", "")
            or getattr(info, "friendly_class_name", "")
            or getattr(wrapper, "friendly_class_name", lambda: "")()
            or ""
        )
        class_name = getattr(info, "class_name", "") or ""

        expected_name = str(selector.get("name") or "").strip()
        name_contains = str(selector.get("name_contains") or "").strip()
        target_text = str(selector.get("target_text") or "").strip()
        expected_automation_id = str(selector.get("automation_id") or "").strip()
        expected_control_type = self._normalize_control_type(selector.get("control_type"), selector.get("class_name"))
        expected_class_name = str(selector.get("class_name") or "").strip()
        expected_handle = selector.get("handle")
        actual_control_type = self._normalize_control_type(control_type, class_name)
        actual_handle = getattr(info, "handle", None)

        if expected_automation_id and automation_id != expected_automation_id:
            return 0
        if expected_name and name != expected_name:
            return 0
        if name_contains and name_contains.lower() not in name.lower():
            return 0
        if expected_control_type and actual_control_type != expected_control_type:
            return 0
        if expected_class_name and class_name.lower() != expected_class_name.lower():
            return 0
        if expected_handle not in (None, "") and actual_handle != expected_handle:
            return 0
        target_text_score = self._target_text_match_score(name, target_text)
        if target_text and target_text_score < 0:
            return 0

        score = 0
        if expected_handle not in (None, ""):
            score += 120
        if expected_automation_id:
            score += 90
        if expected_name:
            score += 70
        if name_contains:
            score += 50
        if target_text_score > 0:
            score += target_text_score
        if expected_control_type:
            score += 30
        if expected_class_name:
            score += 20
        if self._safe_bool(wrapper, "is_visible"):
            score += 6
        if self._safe_bool(wrapper, "is_enabled"):
            score += 4
        return score

    def _find_elements_via_backend_scan(
        self,
        *,
        backend_name: str,
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
        effective_depth_limit = max(depth_limit, 1)
        if backend_name == "uia" and (control_type or class_name) and not automation_id:
            effective_depth_limit = max(effective_depth_limit, 12)
        root = self._resolve_root(
            window_title=window_title,
            window_handle=window_handle,
            backend_name=backend_name,
        )
        wrappers = self._query_wrappers_fast(
            root,
            backend_name=backend_name,
            name=name,
            name_contains=name_contains,
            target_text=target_text,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            limit=limit,
        )
        if wrappers:
            elements: List[ComputerUseElement] = []
            for wrapper in wrappers:
                try:
                    elements.append(self._build_element(wrapper, backend_name=backend_name))
                except Exception:
                    continue
            if elements:
                return elements[: max(1, limit)]

        elements = self._enumerate_elements(
            root,
            depth_limit=effective_depth_limit,
            limit=max(limit * 8 if backend_name == "uia" else limit * 4, 80),
            backend_name=backend_name,
        )
        ranked: List[tuple[int, ComputerUseElement]] = []
        for element in elements:
            score = self._score_cached_element(
                element,
                name=name,
                name_contains=name_contains,
                target_text=target_text,
                automation_id=automation_id,
                control_type=control_type,
                class_name=class_name,
            )
            if score > 0:
                ranked.append((score, element))
        ranked.sort(
            key=lambda item: (
                -item[0],
                item[1].metadata.get("isVisible") is False,
                item[1].metadata.get("isEnabled") is False,
            )
        )
        return [element for _score, element in ranked[: max(1, limit)]]

    def _wrapper_signature(self, wrapper) -> str:
        try:
            info = wrapper.element_info
            handle = getattr(info, "handle", None)
            if handle is not None:
                return f"handle:{handle}"
            runtime_id = getattr(info, "runtime_id", None)
            if runtime_id is not None:
                return f"runtime:{runtime_id}"
            return hashlib.md5(
                json.dumps(
                    {
                        "name": (wrapper.window_text() or getattr(info, "name", "") or "").strip(),
                        "automation_id": getattr(info, "automation_id", "") or "",
                        "control_type": getattr(info, "control_type", "") or "",
                        "class_name": getattr(info, "class_name", "") or "",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:16]
        except Exception:
            return f"object:{id(wrapper)}"

    def _verify_type_text(
        self,
        refreshed: ComputerUseElement,
        wrapper,
        *,
        text: str | None,
        details: Dict[str, Any],
        requested_target: Dict[str, Any] | None = None,
        before_observation: Dict[str, Any] | None = None,
        after_observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        input_capability = (
            dict((((requested_target or {}).get("metadata") or {}).get("textInputCapability") or {}))
            if isinstance((requested_target or {}).get("metadata"), dict)
            else {}
        )
        if input_capability:
            details["inputCapability"] = input_capability
        actual_text = self._read_wrapper_text(wrapper)
        expected_target_text = str(
            (requested_target or {}).get("targetText")
            or (requested_target or {}).get("target_text")
            or text
            or ""
        ).strip()
        if actual_text:
            details["actualText"] = actual_text
            if expected_target_text and expected_target_text not in actual_text:
                return {
                    "passed": False,
                    "status": "text_mismatch",
                    "reason": "输入动作已执行，但控件文本未匹配预期。",
                    "details": details,
                    "level": "failed",
                }
            return {
                "passed": True,
                "status": "text_verified",
                "reason": "输入后已读取到匹配的控件文本。",
                "details": details,
                "level": "verified",
            }
        focus_state = self._focus_state(wrapper)
        details["focusState"] = focus_state
        tree_changed = self._observation_hash_changed(before_observation, after_observation, key="treeHash")
        screen_changed = self._observation_hash_changed(before_observation, after_observation, key="screenHash")
        observation_target_visible = self._observation_contains_text_hint(after_observation, expected_target_text)
        details["changeEvidence"] = {
            "treeChanged": tree_changed,
            "screenChanged": screen_changed,
            "observationTargetVisible": observation_target_visible,
        }
        if expected_target_text and observation_target_visible:
            return {
                "passed": True,
                "status": "soft_verified_target_visible",
                "reason": "虽然无法直接回读输入框文本，但界面中已出现与目标文本匹配的结果或状态变化。",
                "details": details,
                "level": "soft_verified",
            }
        if (tree_changed or screen_changed or focus_state.get("hasKeyboardFocus") or focus_state.get("isActiveWindow")) and not expected_target_text:
            return {
                "passed": True,
                "status": "soft_verified_target_only",
                "reason": "输入后目标焦点或界面结构发生变化，但无法直接读取文本值。",
                "details": details,
                "level": "soft_verified",
            }
        if input_capability.get("allowed") and input_capability.get("status") != "review_required_dynamic_input":
            return {
                "passed": True,
                "status": "soft_verified_editable_target",
                "reason": "目标仍是可编辑输入控件，但当前驱动无法稳定回读文本，按原生输入控件保守确认。",
                "details": details,
                "level": "soft_verified",
            }
        review_status = "review_required_dynamic_input" if input_capability.get("status") == "review_required_dynamic_input" else "review_required_unconfirmed_input"
        return {
            "passed": False,
            "status": review_status,
            "reason": "输入后无法读取文本值，且缺少足够的焦点或界面变化证据，需要人工复核。",
            "details": details,
            "level": "review_required",
        }

    def _observation_contains_text_hint(
        self,
        observation: Dict[str, Any] | None,
        target_text: str | None,
    ) -> bool:
        normalized_target = self._normalize_window_text(target_text)
        if not normalized_target or not isinstance(observation, dict):
            return False
        compact_target = self._compact_match_text(normalized_target)
        for element in list(observation.get("elements") or []):
            if not isinstance(element, dict):
                continue
            candidates = [
                element.get("name"),
                element.get("automationId"),
                element.get("className"),
            ]
            metadata = element.get("metadata")
            if isinstance(metadata, dict):
                candidates.extend(
                    [
                        metadata.get("value"),
                        metadata.get("richText"),
                        metadata.get("title"),
                    ]
                )
            merged_text = " ".join(str(item or "").strip() for item in candidates if str(item or "").strip())
            normalized_candidate = self._normalize_window_text(merged_text)
            if not normalized_candidate:
                continue
            compact_candidate = self._compact_match_text(normalized_candidate)
            if normalized_target in normalized_candidate:
                return True
            if compact_target and compact_candidate and compact_target in compact_candidate:
                return True
        return False

    def _verify_click_focus(self, refreshed: ComputerUseElement, wrapper, *, details: Dict[str, Any]) -> Dict[str, Any]:
        focus_state = self._focus_state(wrapper)
        details["focusState"] = focus_state
        if focus_state.get("hasKeyboardFocus") or focus_state.get("isActiveWindow"):
            return {
                "passed": True,
                "status": "focus_verified",
                "reason": "点击后目标控件或其窗口已获得焦点。",
                "details": details,
                "level": "verified",
            }
        return {
            "passed": True,
            "status": "soft_verified_target_only",
            "reason": "点击后已重新定位目标控件。",
            "details": details,
            "level": "soft_verified",
        }

    def _verify_window_focus(
        self,
        refreshed: ComputerUseElement,
        wrapper,
        *,
        details: Dict[str, Any],
        action_type: str,
    ) -> Dict[str, Any]:
        focus_state = self._focus_state(wrapper)
        details["focusState"] = focus_state
        if focus_state.get("isActiveWindow") or focus_state.get("hasKeyboardFocus"):
            return {
                "passed": True,
                "status": "focus_verified",
                "reason": f"{action_type} 后目标窗口保持焦点。",
                "details": details,
                "level": "verified",
            }
        return {
            "passed": True,
            "status": "soft_verified_target_only",
            "reason": f"{action_type} 已执行，但无法强验证窗口焦点。",
            "details": details,
            "level": "soft_verified",
        }

    def _verify_scroll_change(
        self,
        refreshed: ComputerUseElement,
        *,
        details: Dict[str, Any],
        before_observation: Dict[str, Any] | None = None,
        after_observation: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        before_hash = (before_observation or {}).get("treeHash") or (before_observation or {}).get("screenHash")
        after_hash = (after_observation or {}).get("treeHash") or (after_observation or {}).get("screenHash")
        if before_hash and after_hash and before_hash == after_hash:
            return {
                "passed": False,
                "status": "scroll_no_change",
                "reason": "滚动前后界面状态没有变化。",
                "details": details,
                "level": "failed",
            }
        return {
            "passed": True,
            "status": "scroll_verified",
            "reason": "滚动后界面状态发生变化，动作已生效。",
            "details": details,
            "level": "verified",
        }

    def _observation_hash_changed(
        self,
        before_observation: Dict[str, Any] | None,
        after_observation: Dict[str, Any] | None,
        *,
        key: str,
    ) -> bool:
        before_hash = (before_observation or {}).get(key)
        after_hash = (after_observation or {}).get(key)
        return bool(before_hash and after_hash and before_hash != after_hash)

    def _focus_state(self, wrapper) -> Dict[str, Any]:
        try:
            top = wrapper.top_level_parent()
        except Exception:
            top = wrapper
        return {
            "hasKeyboardFocus": self._truthy_attr(wrapper, ["has_keyboard_focus", "has_focus"]),
            "isActiveWindow": self._truthy_attr(top, ["is_active", "has_focus"]),
            "windowTitle": (top.window_text() or getattr(top.element_info, "name", "") or "").strip(),
            "windowHandle": getattr(top.element_info, "handle", None),
        }

    def _truthy_attr(self, wrapper, attr_names: List[str]) -> bool:
        for attr_name in attr_names:
            try:
                attr = getattr(wrapper, attr_name)
            except Exception:
                continue
            try:
                value = attr() if callable(attr) else attr
            except Exception:
                continue
            if value is not None:
                return bool(value)
        return False

    def _focus_wrapper(self, wrapper) -> None:
        root = wrapper
        try:
            root = wrapper.top_level_parent()
        except Exception:
            root = wrapper
        root_handle = getattr(getattr(root, "element_info", None), "handle", None)
        wrapper_handle = getattr(getattr(wrapper, "element_info", None), "handle", None)
        target_handle = root_handle if root_handle not in (None, "", 0) else wrapper_handle
        if target_handle not in (None, "", 0):
            self._focus_message_window(int(target_handle))
        try:
            wrapper.set_focus()
        except Exception:
            try:
                root.set_focus()
            except Exception:
                pass
        if target_handle not in (None, "", 0):
            self._focus_message_window(int(target_handle))

    def _perform_click_strategy(
        self,
        *,
        wrapper,
        element: ComputerUseElement,
        double: bool,
        prefer_sendinput_click: bool = False,
    ) -> str:
        center = self._center_of_bounds(element.bounds)
        if prefer_sendinput_click:
            try:
                self._sendinput_click(center, double=double)
                return "sendinput_double_click" if double else "sendinput_click"
            except Exception:
                pass
        if double:
            try:
                wrapper.double_click_input()
                return "double_click_input"
            except Exception:
                try:
                    if hasattr(wrapper, "double_click"):
                        wrapper.double_click()
                        return "double_click"
                except Exception:
                    pass
                if mouse is not None:
                    try:
                        mouse.double_click(coords=center)
                        return "mouse_double_click"
                    except Exception:
                        pass
                self._message_click(window_handle=element.window_handle, point=list(center), double=True)
                return "message_double_click"

        normalized_role = self._normalize_control_type(element.role, element.class_name)
        strategy = self._try_non_intrusive_click(wrapper=wrapper, normalized_role=normalized_role)
        if strategy:
            return strategy
        try:
            if hasattr(wrapper, "click"):
                wrapper.click()
                return "click"
        except Exception:
            pass
        try:
            wrapper.click_input()
            return "click_input"
        except Exception:
            if mouse is not None:
                try:
                    mouse.click(coords=center)
                    return "mouse_click"
                except Exception:
                    pass
            self._message_click(window_handle=element.window_handle, point=list(center), double=False)
            return "message_click"

    def _coordinate_click(
        self,
        *,
        point: List[int] | Tuple[int, int],
        root,
        double: bool,
        prefer_sendinput_click: bool,
    ) -> str:
        resolved_point = [int(point[0]), int(point[1])]
        if root is not None:
            self._focus_wrapper(root)
        if double:
            if prefer_sendinput_click:
                self._sendinput_click(resolved_point, double=True)
                return "coordinate_sendinput_double_click"
            if mouse is None:
                raise RuntimeError("mouse unavailable")
            mouse.double_click(coords=tuple(resolved_point))
            return "coordinate_double_click"
        if prefer_sendinput_click:
            self._sendinput_click(resolved_point, double=False)
            return "coordinate_sendinput_click"
        if mouse is None:
            raise RuntimeError("mouse unavailable")
        mouse.click(coords=tuple(resolved_point))
        return "coordinate_click"

    def _window_typing_probe(self, *, root_handle: int | None) -> Dict[str, Any]:
        probe: Dict[str, Any] = {
            "rootHandle": int(root_handle) if root_handle not in (None, 0) else None,
            "foregroundHandle": None,
            "foregroundWithinRoot": False,
            "foregroundClassName": None,
            "focusHandle": None,
            "caretHandle": None,
            "focusWithinRoot": False,
            "caretWithinRoot": False,
            "focusClassName": None,
            "caretClassName": None,
            "accepted": False,
        }
        try:
            user32 = ctypes.windll.user32
            foreground_handle = int(user32.GetForegroundWindow() or 0)
            probe["foregroundHandle"] = foreground_handle or None
            if foreground_handle in (None, 0):
                return probe
            probe["foregroundClassName"] = self._class_name_for_handle(foreground_handle) if foreground_handle else None
            process_id = wintypes.DWORD()
            thread_id = int(user32.GetWindowThreadProcessId(foreground_handle, ctypes.byref(process_id)) or 0)
            if thread_id <= 0:
                return probe
            gui_info = GUITHREADINFO()
            gui_info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not bool(user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui_info))):
                return probe
            focus_handle = int(gui_info.hwndFocus or 0)
            caret_handle = int(gui_info.hwndCaret or 0)
            probe["focusHandle"] = focus_handle or None
            probe["caretHandle"] = caret_handle or None
            probe["focusClassName"] = self._class_name_for_handle(focus_handle) if focus_handle else None
            probe["caretClassName"] = self._class_name_for_handle(caret_handle) if caret_handle else None
            if root_handle not in (None, 0):
                root_value = int(root_handle)
                probe["foregroundWithinRoot"] = self._handle_within_root(root_value, foreground_handle)
                probe["focusWithinRoot"] = self._handle_within_root(root_value, focus_handle)
                probe["caretWithinRoot"] = self._handle_within_root(root_value, caret_handle)
            probe["accepted"] = self._accept_window_typing_probe(probe)
            return probe
        except Exception:
            return probe

    def _accept_window_typing_probe(self, probe: Dict[str, Any], *, focus_mode: str | None = None) -> bool:
        normalized_mode = str(focus_mode or probe.get("focusProbeMode") or "").strip().lower()
        if bool(probe.get("caretWithinRoot")):
            return True
        if not bool(probe.get("focusWithinRoot")):
            if normalized_mode != "content_receiver" or not bool(probe.get("foregroundWithinRoot")):
                return False
        focus_class = str(probe.get("focusClassName") or "").strip().lower()
        foreground_class = str(probe.get("foregroundClassName") or "").strip().lower()
        if normalized_mode == "content_receiver":
            focus_tokens = ("directui", "shelldll_defview", "syslistview32", "listview", "workerw")
            if any(token in focus_class for token in focus_tokens):
                return True
            return bool(probe.get("foregroundWithinRoot")) and any(token in foreground_class for token in focus_tokens)
        return any(token in focus_class for token in ("edit", "rich", "chrome", "text"))

    def _handle_within_root(self, root_handle: int, candidate_handle: int | None) -> bool:
        if candidate_handle in (None, 0):
            return False
        if int(candidate_handle) == int(root_handle):
            return True
        try:
            return bool(ctypes.windll.user32.IsChild(int(root_handle), int(candidate_handle)))
        except Exception:
            return False

    def _sendinput_click(self, point: List[int] | Tuple[int, int], *, double: bool) -> str:
        if not self._sendinput_click_engine.is_available():
            raise WindowsUIADriverError("当前环境不可用 SendInput 真点击引擎。")
        result = self._sendinput_click_engine.click(point, double=bool(double))
        return str(result.strategy)

    def _sendinput_type_text(
        self,
        *,
        text: str,
        file_paths: List[str] | None = None,
        clear_first: bool,
        press_enter: bool,
    ) -> str:
        if not self._sendinput_click_engine.is_available():
            raise WindowsUIADriverError("当前环境不可用 SendInput 文本输入引擎。")
        return str(
            self._sendinput_click_engine.type_text(
                text=text,
                file_paths=file_paths,
                clear_first=bool(clear_first),
                press_enter=bool(press_enter),
            )
        )

    def _try_non_intrusive_click(self, *, wrapper, normalized_role: str) -> str | None:
        try:
            if hasattr(wrapper, "invoke"):
                wrapper.invoke()
                return "invoke"
        except Exception:
            pass
        if normalized_role in {"tabitem", "listitem", "menuitem", "treeitem"}:
            try:
                if hasattr(wrapper, "select"):
                    wrapper.select()
                    return "select"
            except Exception:
                pass
        if normalized_role in {"checkbox", "radiobutton"}:
            try:
                if hasattr(wrapper, "toggle"):
                    wrapper.toggle()
                    return "toggle"
            except Exception:
                pass
        for key_sequence, strategy in (("{ENTER}", "keyboard_enter"), (" ", "keyboard_space")):
            try:
                if hasattr(wrapper, "type_keys"):
                    wrapper.type_keys(key_sequence, set_foreground=True, pause=0.01)
                    return strategy
            except Exception:
                continue
        return None

    def _infer_actions(self, role: str, wrapper) -> List[str]:
        actions = ["focus"]
        role_lower = self._normalize_control_type(role, getattr(wrapper.element_info, "class_name", "") or "")
        if role_lower in {"button", "hyperlink", "tabitem", "listitem", "menuitem", "checkbox", "radiobutton"}:
            actions.append("click")
        if role_lower in {"edit", "document", "combobox"}:
            actions.extend(["click", "type_text"])
        if role_lower in {"pane", "window"}:
            actions.extend(["click", "scroll", "screenshot"])
        if hasattr(wrapper, "invoke"):
            actions.append("invoke")
        return sorted(set(actions))

    def _rect_to_bounds(self, rect) -> List[int]:
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]

    def _center_of_bounds(self, bounds: Iterable[int]) -> tuple[int, int]:
        left, top, right, bottom = [int(value) for value in bounds]
        return (left + max(1, right - left) // 2, top + max(1, bottom - top) // 2)

    def _normalize_window_text(self, value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]+", "", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    def _compact_match_text(self, value: Any) -> str:
        normalized = self._normalize_window_text(value)
        if not normalized:
            return ""
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)

    def _target_text_match_score(self, candidate_name: Any, target_text: str | None) -> int:
        target_normalized = self._normalize_window_text(target_text)
        candidate_normalized = self._normalize_window_text(candidate_name)
        if not target_normalized:
            return 0
        if not candidate_normalized:
            return -120
        target_compact = self._compact_match_text(target_normalized)
        candidate_compact = self._compact_match_text(candidate_normalized)
        if candidate_normalized == target_normalized:
            return 180
        if target_compact and candidate_compact and candidate_compact == target_compact:
            return 180
        contains_match = target_normalized in candidate_normalized
        compact_contains_match = bool(target_compact and candidate_compact and target_compact in candidate_compact)
        if not contains_match and not compact_contains_match:
            return -160
        candidate_basis = candidate_compact or candidate_normalized
        target_basis = target_compact or target_normalized
        extra_length = max(0, len(candidate_basis) - len(target_basis))
        prefix_bonus = 10 if candidate_basis.startswith(target_basis) else 0
        suffix_penalty = 18 if candidate_basis.endswith(target_basis) else 0
        contains_score = 72 + prefix_bonus - min(64, extra_length * 8) - suffix_penalty
        return max(8, contains_score)

    def _message_click(
        self,
        *,
        window_handle: int | None,
        point: List[int] | Tuple[int, int],
        double: bool,
        button: str = "left",
    ) -> None:
        handle = self._resolve_message_target_handle(window_handle=window_handle, point=point)
        if handle in (None, 0):
            raise WindowsUIADriverError("无法解析坐标点击的消息目标窗口。")
        self._focus_message_window(int(handle))
        client_x, client_y = self._screen_to_client(int(handle), int(point[0]), int(point[1]))
        lparam = (client_y << 16) | (client_x & 0xFFFF)
        user32 = ctypes.windll.user32
        WM_MOUSEMOVE = 0x0200
        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONUP = 0x0202
        WM_RBUTTONDOWN = 0x0204
        WM_RBUTTONUP = 0x0205
        MK_LBUTTON = 0x0001
        MK_RBUTTON = 0x0002
        normalized_button = str(button or "left").strip().lower()
        if normalized_button == "right":
            down_message = WM_RBUTTONDOWN
            up_message = WM_RBUTTONUP
            down_wparam = MK_RBUTTON
        else:
            down_message = WM_LBUTTONDOWN
            up_message = WM_LBUTTONUP
            down_wparam = MK_LBUTTON
        click_count = 2 if double else 1
        user32.PostMessageW(int(handle), WM_MOUSEMOVE, 0, lparam)
        for _ in range(click_count):
            user32.PostMessageW(int(handle), down_message, down_wparam, lparam)
            user32.PostMessageW(int(handle), up_message, 0, lparam)
            time.sleep(0.03)

    def _message_type_text(
        self,
        *,
        window_handle: int | None,
        text: str,
        clear_first: bool,
        press_enter: bool,
        point: List[int] | Tuple[int, int] | None,
    ) -> None:
        handle = self._resolve_message_target_handle(window_handle=window_handle, point=point)
        if handle in (None, 0):
            raise WindowsUIADriverError("无法解析消息输入的目标窗口。")
        target_handle = int(handle)
        self._focus_message_window(target_handle)
        class_name = self._class_name_for_handle(target_handle)
        if clear_first:
            if not self._clear_text_via_message(target_handle, class_name=class_name):
                self._message_hotkey(window_handle=target_handle, sequence="^a")
                self._post_virtual_key(target_handle, 0x2E)
                self._message_hotkey(window_handle=target_handle, sequence="^a")
                self._post_virtual_key(target_handle, 0x08)
        if not self._set_text_via_message(target_handle, text, class_name=class_name):
            for char in str(text or ""):
                ctypes.windll.user32.PostMessageW(target_handle, 0x0102, ord(char), 0)
                time.sleep(0.002)
        if press_enter:
            self._post_virtual_key(target_handle, 0x0D)

    def _message_paste_payload(
        self,
        *,
        window_handle: int | None,
        point: List[int] | Tuple[int, int] | None,
        text: str,
        file_paths: List[str] | None,
        clear_first: bool,
        press_enter: bool,
    ) -> None:
        handle = self._resolve_message_target_handle(window_handle=window_handle, point=point)
        if handle in (None, 0):
            raise WindowsUIADriverError("无法解析剪贴板粘贴的目标窗口。")
        target_handle = int(handle)
        self._focus_message_window(target_handle)
        previous_payload = self._sendinput_click_engine._snapshot_clipboard_payload()
        try:
            if clear_first:
                self._message_hotkey(window_handle=target_handle, sequence="^a")
                self._post_virtual_key(target_handle, 0x2E)
            self._sendinput_click_engine._set_clipboard_payload(
                text=str(text or "") or None,
                file_paths=list(file_paths or []),
            )
            ctypes.windll.user32.PostMessageW(target_handle, 0x0302, 0, 0)
            if press_enter:
                self._post_virtual_key(target_handle, 0x0D)
        finally:
            self._sendinput_click_engine._restore_clipboard_payload(previous_payload)

    def _set_text_via_message(self, handle: int, text: str, *, class_name: str) -> bool:
        normalized = class_name.lower()
        supports_settext = normalized.startswith("edit") or "richedit" in normalized or "omniboxviewviews" in normalized
        if not supports_settext:
            return False
        try:
            ctypes.windll.user32.SendMessageW(int(handle), 0x000C, 0, str(text or ""))
            return True
        except Exception:
            return False

    def _clear_text_via_message(self, handle: int, *, class_name: str) -> bool:
        return self._set_text_via_message(handle, "", class_name=class_name)

    def _message_hotkey(self, *, window_handle: int | None, sequence: str) -> bool:
        handle = int(window_handle) if window_handle not in (None, "", 0) else None
        if handle is None:
            return False
        try:
            support_plan = analyze_hotkey_support(sequence)
            strokes = list(support_plan.strokes)
        except Exception:
            return False
        if not support_plan.supports_window_message:
            return False
        try:
            self._focus_message_window(handle)
            pressed_modifiers: List[int] = []
            held_modifiers: List[int] = []
            for stroke in strokes:
                for _ in range(max(1, int(stroke.repeat))):
                    target_modifiers: List[int] = []
                    for modifier in [*tuple(stroke.modifiers), *tuple(held_modifiers)]:
                        value = int(modifier)
                        if value not in target_modifiers:
                            target_modifiers.append(value)
                    self._sync_message_modifiers(handle, pressed_modifiers=pressed_modifiers, target_modifiers=target_modifiers)
                    self._post_hotkey_stroke(handle, stroke)
                    if stroke.key_vk in MANAGED_MODIFIER_VKS:
                        if stroke.event_type == "down":
                            if int(stroke.key_vk) not in held_modifiers:
                                held_modifiers.append(int(stroke.key_vk))
                            if int(stroke.key_vk) not in pressed_modifiers:
                                pressed_modifiers.append(int(stroke.key_vk))
                        elif stroke.event_type == "up":
                            held_modifiers = [item for item in held_modifiers if int(item) != int(stroke.key_vk)]
                            pressed_modifiers = [item for item in pressed_modifiers if int(item) != int(stroke.key_vk)]
            self._sync_message_modifiers(handle, pressed_modifiers=pressed_modifiers, target_modifiers=[])
            return True
        except Exception:
            return False

    def _sync_message_modifiers(
        self,
        handle: int,
        *,
        pressed_modifiers: List[int],
        target_modifiers: List[int],
    ) -> None:
        for modifier in list(reversed(pressed_modifiers)):
            if modifier in target_modifiers:
                continue
            self._post_keyboard_message(handle, 0x0105 if modifier == VK_MENU else 0x0101, modifier)
            pressed_modifiers.remove(modifier)
        for modifier in target_modifiers:
            if modifier in pressed_modifiers:
                continue
            self._post_keyboard_message(handle, 0x0104 if modifier == VK_MENU else 0x0100, modifier)
            pressed_modifiers.append(modifier)

    def _post_hotkey_stroke(self, handle: int, stroke: ParsedHotkeyStroke) -> None:
        key_down = 0x0104 if VK_MENU in stroke.modifiers else 0x0100
        key_up = 0x0105 if VK_MENU in stroke.modifiers else 0x0101
        if stroke.event_type == "down":
            self._post_keyboard_message(handle, key_down, stroke.key_vk)
            return
        if stroke.event_type == "up":
            self._post_keyboard_message(handle, key_up, stroke.key_vk)
            return
        self._post_keyboard_message(handle, key_down, stroke.key_vk)
        self._post_keyboard_message(handle, key_up, stroke.key_vk)

    def _post_keyboard_message(self, handle: int, message: int, key_vk: int) -> None:
        ctypes.windll.user32.PostMessageW(int(handle), int(message), int(key_vk), 0)

    def _post_virtual_key(self, handle: int, key_vk: int) -> None:
        self._post_keyboard_message(handle, 0x0100, key_vk)
        self._post_keyboard_message(handle, 0x0101, key_vk)

    def _resolve_message_target_handle(
        self,
        *,
        window_handle: int | None,
        point: List[int] | Tuple[int, int] | None,
    ) -> int | None:
        if point is not None:
            point_handle = self._deepest_window_from_point(int(point[0]), int(point[1]))
            if point_handle not in (None, 0):
                return int(point_handle)
        if window_handle not in (None, "", 0):
            return int(window_handle)
        return None

    def _deepest_window_from_point(self, x: int, y: int) -> int | None:
        user32 = ctypes.windll.user32
        screen_point = wintypes.POINT(int(x), int(y))
        handle = int(user32.WindowFromPoint(screen_point) or 0)
        if handle <= 0:
            return None
        get_ancestor = getattr(user32, "GetAncestor", None)
        current = handle
        while True:
            client_point = wintypes.POINT(int(x), int(y))
            if not bool(user32.ScreenToClient(int(current), ctypes.byref(client_point))):
                break
            lparam_point = wintypes.POINT(client_point.x, client_point.y)
            next_handle = 0
            real_child_window_from_point = getattr(user32, "RealChildWindowFromPoint", None)
            if callable(real_child_window_from_point):
                next_handle = int(real_child_window_from_point(int(current), lparam_point) or 0)
            if next_handle <= 0:
                child_window_from_point_ex = getattr(user32, "ChildWindowFromPointEx", None)
                if callable(child_window_from_point_ex):
                    CWP_SKIPINVISIBLE = 0x0001
                    CWP_SKIPDISABLED = 0x0002
                    next_handle = int(
                        child_window_from_point_ex(
                            int(current),
                            lparam_point,
                            CWP_SKIPINVISIBLE | CWP_SKIPDISABLED,
                        )
                        or 0
                    )
            if next_handle <= 0 or next_handle == current:
                break
            current = next_handle
        if callable(get_ancestor):
            GA_ROOT = 2
            root_handle = int(get_ancestor(int(current), GA_ROOT) or 0)
            if root_handle > 0 and root_handle not in (None, current):
                class_name = self._class_name_for_handle(int(current)).strip().lower()
                if class_name in {"workerw", "progman"}:
                    return root_handle
        return int(current)

    def _focus_message_window(self, handle: int) -> None:
        try:
            user32 = ctypes.windll.user32
            is_iconic = getattr(user32, "IsIconic", None)
            show_cmd = 9 if callable(is_iconic) and bool(is_iconic(int(handle))) else 5
            user32.ShowWindow(int(handle), int(show_cmd))
            bring_window_to_top = getattr(user32, "BringWindowToTop", None)
            if callable(bring_window_to_top):
                bring_window_to_top(int(handle))
            user32.SetForegroundWindow(int(handle))
            user32.SetActiveWindow(int(handle))
            set_focus = getattr(user32, "SetFocus", None)
            if callable(set_focus):
                set_focus(int(handle))
        except Exception:
            return

    def _screen_to_client(self, handle: int, x: int, y: int) -> Tuple[int, int]:
        point = wintypes.POINT(int(x), int(y))
        ctypes.windll.user32.ScreenToClient(int(handle), ctypes.byref(point))
        return int(point.x), int(point.y)

    def _class_name_for_handle(self, handle: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        try:
            ctypes.windll.user32.GetClassNameW(int(handle), buffer, len(buffer))
            return str(buffer.value or "").strip()
        except Exception:
            return ""

    def _safe_bool(self, wrapper, attr: str) -> bool:
        try:
            method = getattr(wrapper, attr)
            return bool(method() if callable(method) else method)
        except Exception:
            return False

    def _read_wrapper_text(self, wrapper) -> str:
        candidates: List[str] = []
        try:
            value_reader = getattr(wrapper, "get_value", None)
            if callable(value_reader):
                value = value_reader()
                if value:
                    candidates.append(str(value))
        except Exception:
            pass
        try:
            iface_value = getattr(wrapper, "iface_value", None)
            current_value = getattr(iface_value, "CurrentValue", None)
            if current_value:
                candidates.append(str(current_value))
        except Exception:
            pass
        try:
            rich_text = getattr(wrapper.element_info, "rich_text", "") or ""
            if rich_text:
                candidates.append(str(rich_text))
        except Exception:
            pass
        try:
            raw_value = getattr(wrapper.element_info, "value", "") or ""
            if raw_value:
                candidates.append(str(raw_value))
        except Exception:
            pass
        try:
            title = wrapper.window_text()
            if title:
                candidates.append(str(title))
        except Exception:
            pass
        try:
            texts = wrapper.texts()
            if isinstance(texts, list):
                candidates.extend(str(item) for item in texts if item)
        except Exception:
            pass
        try:
            legacy = wrapper.legacy_properties()
            if isinstance(legacy, dict):
                for key in ("Value", "Name"):
                    if legacy.get(key):
                        candidates.append(str(legacy[key]))
        except Exception:
            pass
        unique_values: List[str] = []
        for item in candidates:
            normalized = str(item).strip()
            if normalized and normalized not in unique_values:
                unique_values.append(normalized)
        return " ".join(unique_values)

    def _observation_cache_key(self, *, window_handle: int | None, depth_limit: int, element_limit: int) -> str:
        return f"{window_handle or 'desktop'}:{depth_limit}:{element_limit}"

    def _hash_payload(self, value: Any) -> str:
        return hashlib.md5(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
