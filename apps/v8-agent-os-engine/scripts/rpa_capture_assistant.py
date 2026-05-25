from __future__ import annotations

import argparse
import ctypes
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from typing import Any


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
GA_ROOT = 2
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
VK_ESCAPE = 0x1B
HC_ACTION = 0
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080

_DPI_AWARENESS_SET = False


def _ulong_ptr_type() -> type[ctypes.c_ulong] | type[ctypes.c_ulonglong]:
    return ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


ULONG_PTR = _ulong_ptr_type()


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


_HOOK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
HOOKPROC = _HOOK_FACTORY(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def _ensure_windows_dpi_awareness() -> None:
    """Make overlay geometry and cursor coordinates use physical pixels on high-DPI displays."""
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET or not sys.platform.startswith("win"):
        return
    _DPI_AWARENESS_SET = True
    try:
        set_context = getattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext", None)
        if callable(set_context) and set_context(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except Exception:
        pass
    try:
        shcore = ctypes.windll.shcore
        set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
        if callable(set_awareness):
            set_awareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_VK_KEY_NAMES: dict[str, tuple[int, str]] = {
    "space": (0x20, "Space"),
    "enter": (0x0D, "Enter"),
    "return": (0x0D, "Enter"),
    "esc": (0x1B, "Esc"),
    "escape": (0x1B, "Esc"),
    "tab": (0x09, "Tab"),
    "backspace": (0x08, "Backspace"),
    "delete": (0x2E, "Delete"),
    "del": (0x2E, "Delete"),
    "insert": (0x2D, "Insert"),
    "ins": (0x2D, "Insert"),
    "home": (0x24, "Home"),
    "end": (0x23, "End"),
    "pageup": (0x21, "PageUp"),
    "pgup": (0x21, "PageUp"),
    "pagedown": (0x22, "PageDown"),
    "pgdn": (0x22, "PageDown"),
    "left": (0x25, "Left"),
    "up": (0x26, "Up"),
    "right": (0x27, "Right"),
    "down": (0x28, "Down"),
    "plus": (0xBB, "Plus"),
    "minus": (0xBD, "Minus"),
    "comma": (0xBC, "Comma"),
    "period": (0xBE, "Period"),
    "dot": (0xBE, "Period"),
    "slash": (0xBF, "Slash"),
    "backslash": (0xDC, "Backslash"),
    "semicolon": (0xBA, "Semicolon"),
    "quote": (0xDE, "Quote"),
    "backtick": (0xC0, "Backtick"),
    "grave": (0xC0, "Backtick"),
    "leftbracket": (0xDB, "LeftBracket"),
    "rightbracket": (0xDD, "RightBracket"),
    "[": (0xDB, "["),
    "lbracket": (0xDB, "["),
    "openbracket": (0xDB, "["),
    "]": (0xDD, "]"),
    "rbracket": (0xDD, "]"),
    "closebracket": (0xDD, "]"),
}


def _parse_windows_hotkey(value: str | None, *, default: str) -> tuple[int, int, str]:
    raw = str(value or default).strip() or default
    tokens = [token.strip().lower() for token in re.split(r"\s*\+\s*|\s+", raw) if token.strip()]
    if not tokens:
        tokens = [token.strip().lower() for token in re.split(r"\s+", default) if token.strip()]
    modifiers = 0
    modifier_labels: list[str] = []
    key_vk: int | None = None
    key_label = ""
    for token in tokens:
        compact = re.sub(r"[\s_-]+", "", token)
        if compact in {"ctrl", "control"}:
            modifiers |= MOD_CONTROL
            if "Ctrl" not in modifier_labels:
                modifier_labels.append("Ctrl")
            continue
        if compact in {"alt", "option"}:
            modifiers |= MOD_ALT
            if "Alt" not in modifier_labels:
                modifier_labels.append("Alt")
            continue
        if compact == "shift":
            modifiers |= MOD_SHIFT
            if "Shift" not in modifier_labels:
                modifier_labels.append("Shift")
            continue
        if compact in {"win", "windows", "meta", "cmd", "command", "super"}:
            modifiers |= MOD_WIN
            if "Win" not in modifier_labels:
                modifier_labels.append("Win")
            continue
        if key_vk is not None:
            raise ValueError(f"Hotkey '{raw}' has more than one key token.")
        if len(compact) == 1 and compact.isalpha():
            key_vk = ord(compact.upper())
            key_label = compact.upper()
        elif len(compact) == 1 and compact.isdigit():
            key_vk = ord(compact)
            key_label = compact
        elif compact.startswith("f") and compact[1:].isdigit() and 1 <= int(compact[1:]) <= 24:
            key_vk = 0x70 + int(compact[1:]) - 1
            key_label = compact.upper()
        elif compact in _VK_KEY_NAMES:
            key_vk, key_label = _VK_KEY_NAMES[compact]
        else:
            raise ValueError(f"Unsupported hotkey token '{token}' in '{raw}'.")
    if key_vk is None:
        raise ValueError(f"Hotkey '{raw}' is missing a key token.")
    normalized = "+".join([*modifier_labels, key_label])
    return modifiers | MOD_NOREPEAT, key_vk, normalized


def _post_event(engine_url: str, recording_id: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    base_url = engine_url.rstrip("/")
    if base_url.endswith("/v1"):
        candidate_urls = [f"{base_url}/rpa/recordings/{recording_id}/capture-assistant/capture"]
    else:
        candidate_urls = [
            f"{base_url}/rpa/recordings/{recording_id}/capture-assistant/capture",
            f"{base_url}/v1/rpa/recordings/{recording_id}/capture-assistant/capture",
        ]
    last_error: Exception | None = None
    for url in candidate_urls:
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - localhost/Admin-controlled URL.
                response.read()
                return
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 404:
                raise
        except Exception as exc:
            last_error = exc
            raise
    if last_error:
        raise last_error


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _emit_status(event: str, **payload: Any) -> None:
    data = {
        "event": f"rpa_capture_assistant.{event}",
        "ok": payload.pop("ok", True),
        "timestamp": _utc_now(),
        **payload,
    }
    print(json.dumps(data, ensure_ascii=False), flush=True)


def _base_payload(args: argparse.Namespace, *, backend: str) -> dict[str, Any]:
    return {
        "source": "rpa_capture_assistant",
        "action": args.action or "click",
        "intent": "capture target element or coordinate",
        "captureBackend": backend,
        "fragileCoordinateFallback": True,
        "coordinateFallback": True,
        "metadata": {
            "source": "host_capture_assistant",
            "captureAssistant": True,
            "captureBackend": backend,
            "nativeInspectorSessionId": args.native_inspector_session_id or None,
            "captureMode": args.mode,
            "targetLabel": args.target_label,
            "targetWindowTitle": args.target_window_title or None,
            "targetWindowHandle": args.target_window_handle or None,
            "targetWindowProcessId": args.target_window_process_id or None,
            "recordAndForward": bool(args.record_and_forward),
            "persistent": bool(args.persistent),
            "hotkey": args.hotkey,
            "cancelHotkey": args.cancel_hotkey,
            "highlightOverlay": bool(getattr(args, "highlight_overlay", False)),
            "capturedAt": _utc_now(),
        },
    }


def _windows_text(hwnd: int, kind: str) -> str:
    user32 = ctypes.windll.user32
    buffer = ctypes.create_unicode_buffer(512)
    if kind == "class":
        user32.GetClassNameW(wintypes.HWND(hwnd), buffer, len(buffer))
    else:
        user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value


def _windows_window_info(x: int, y: int) -> dict[str, Any]:
    user32 = ctypes.windll.user32
    point = wintypes.POINT(x, y)
    hwnd = int(user32.WindowFromPoint(point))
    root = int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)) if hwnd else 0
    if not root:
        root = int(user32.GetForegroundWindow())
    rect = wintypes.RECT()
    if root:
        user32.GetWindowRect(wintypes.HWND(root), ctypes.byref(rect))
    pid = wintypes.DWORD()
    if root:
        user32.GetWindowThreadProcessId(wintypes.HWND(root), ctypes.byref(pid))
    dpi = 96
    try:
        dpi = int(user32.GetDpiForWindow(wintypes.HWND(root))) if root and hasattr(user32, "GetDpiForWindow") else 96
    except Exception:
        dpi = 96
    return {
        "handle": root or hwnd or None,
        "childHandle": hwnd or None,
        "title": _windows_text(root, "title") if root else "",
        "className": _windows_text(root, "class") if root else "",
        "processId": int(pid.value) if pid else None,
        "bounds": {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
            "width": max(0, int(rect.right - rect.left)),
            "height": max(0, int(rect.bottom - rect.top)),
        },
        "dpi": dpi,
    }


def _windows_cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def _windows_screen() -> dict[str, Any]:
    _ensure_windows_dpi_awareness()
    user32 = ctypes.windll.user32
    left = int(user32.GetSystemMetrics(76))
    top = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    if width <= 0 or height <= 0:
        left = 0
        top = 0
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "width": width,
        "height": height,
        "devicePixelRatio": 1,
        "monitorId": "virtual",
    }


def _tk_geometry_offset(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _windows_client_rect(hwnd: int) -> dict[str, int]:
    user32 = ctypes.windll.user32
    if not hwnd:
        return {}
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return {}
    top_left = wintypes.POINT(0, 0)
    bottom_right = wintypes.POINT(int(rect.right), int(rect.bottom))
    try:
        user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(top_left))
        user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(bottom_right))
    except Exception:
        return {}
    left = int(top_left.x)
    top = int(top_left.y)
    right = int(bottom_right.x)
    bottom = int(bottom_right.y)
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": max(0, right - left),
        "height": max(0, bottom - top),
    }


def _rect_to_bounds(rect: Any) -> dict[str, int]:
    left = int(getattr(rect, "left", getattr(rect, "Left", 0)) or 0)
    top = int(getattr(rect, "top", getattr(rect, "Top", 0)) or 0)
    right = int(getattr(rect, "right", getattr(rect, "Right", left)) or left)
    bottom = int(getattr(rect, "bottom", getattr(rect, "Bottom", top)) or top)
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": max(0, right - left),
        "height": max(0, bottom - top),
    }


def _is_admin_surface(window: dict[str, Any]) -> bool:
    text = " ".join(
        str(window.get(key) or "")
        for key in ("title", "className")
    ).lower()
    return any(marker in text for marker in ("v8 agent os", "v8 os", "localhost:9528", "127.0.0.1:9528", "desktop live"))


def _windows_uia_sample(x: int, y: int) -> dict[str, Any]:
    try:
        from pywinauto import Desktop  # type: ignore

        element = Desktop(backend="uia").from_point(x, y)
        info = getattr(element, "element_info", None)
        rect = getattr(info, "rectangle", None)
        bounds = _rect_to_bounds(rect) if rect is not None else {}
        name = str(getattr(info, "name", "") or "")
        automation_id = str(getattr(info, "automation_id", "") or "")
        control_type = str(getattr(info, "control_type", "") or "")
        class_name = str(getattr(info, "class_name", "") or "")
        candidates = []
        if automation_id or name or control_type or class_name:
            candidates.append(
                {
                    "kind": "uia",
                    "automationId": automation_id or None,
                    "name": name or None,
                    "controlType": control_type or None,
                    "className": class_name or None,
                    "selector": " | ".join(part for part in [control_type, automation_id, name] if part),
                }
            )
        return {
            "ok": bool(candidates),
            "backend": "uia",
            "name": name,
            "automationId": automation_id,
            "controlType": control_type,
            "className": class_name,
            "bounds": bounds,
            "confidence": 0.82 if candidates else 0.45,
            "selectorCandidates": candidates,
        }
    except Exception as exc:
        return {
            "ok": False,
            "backend": "win32",
            "error": str(exc),
            "confidence": 0.35,
            "selectorCandidates": [],
        }


def _windows_hover_sample(args: argparse.Namespace, x: int, y: int) -> dict[str, Any]:
    window = _windows_window_info(x, y)
    bounds = dict(window.get("bounds") or {})
    client_rect = dict(window.get("clientRect") or {}) or _windows_client_rect(int(window.get("handle") or 0))
    if client_rect:
        window["clientRect"] = client_rect
    anchor_rect = client_rect or bounds
    rel_x = int(x - int(anchor_rect.get("left") or 0))
    rel_y = int(y - int(anchor_rect.get("top") or 0))
    uia = _windows_uia_sample(x, y)
    highlight_bounds = dict(uia.get("bounds") or {}) if uia.get("ok") and uia.get("bounds") else bounds
    if not highlight_bounds.get("width") or not highlight_bounds.get("height"):
        highlight_bounds = {"left": x - 24, "top": y - 24, "right": x + 24, "bottom": y + 24, "width": 48, "height": 48}
    return {
        "x": int(x),
        "y": int(y),
        "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
        "windowTitle": window.get("title"),
        "className": window.get("className"),
        "targetLabel": args.target_label,
        "targetWindow": window,
        "element": uia,
        "selectorCandidates": list(uia.get("selectorCandidates") or []),
        "highlightBounds": highlight_bounds,
        "ignored": _is_admin_surface(window) and bool(getattr(args, "ignore_admin_surface", True)),
    }


def _windows_light_hover_sample(args: argparse.Namespace, x: int, y: int) -> dict[str, Any]:
    """Cheap hover sample for live highlighting; deep UIA is reserved for capture."""
    window = _windows_window_info(x, y)
    bounds = dict(window.get("bounds") or {})
    client_rect = dict(window.get("clientRect") or {}) or _windows_client_rect(int(window.get("handle") or 0))
    if client_rect:
        window["clientRect"] = client_rect
    anchor_rect = client_rect or bounds
    rel_x = int(x - int(anchor_rect.get("left") or 0))
    rel_y = int(y - int(anchor_rect.get("top") or 0))
    highlight_bounds = bounds if bounds.get("width") and bounds.get("height") else {
        "left": x - 24,
        "top": y - 24,
        "right": x + 24,
        "bottom": y + 24,
        "width": 48,
        "height": 48,
    }
    return {
        "x": int(x),
        "y": int(y),
        "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
        "windowTitle": window.get("title"),
        "className": window.get("className"),
        "targetLabel": args.target_label,
        "targetWindow": window,
        "element": {
            "ok": False,
            "backend": "win32_hover",
            "name": window.get("title") or args.target_label,
            "controlType": "Window",
            "className": window.get("className"),
            "bounds": highlight_bounds,
            "confidence": 0.45,
            "selectorCandidates": [],
        },
        "selectorCandidates": [],
        "highlightBounds": highlight_bounds,
        "ignored": _is_admin_surface(window) and bool(getattr(args, "ignore_admin_surface", True)),
        "lightSample": True,
    }


def _build_windows_payload(args: argparse.Namespace, *, x: int, y: int, backend: str, hover_sample: dict[str, Any] | None = None) -> dict[str, Any]:
    hover = hover_sample or _windows_hover_sample(args, x, y)
    window = dict(hover.get("targetWindow") or _windows_window_info(x, y))
    bounds = dict(window.get("bounds") or {})
    client_rect = dict(window.get("clientRect") or {}) or _windows_client_rect(int(window.get("handle") or 0))
    if client_rect:
        window["clientRect"] = client_rect
    anchor_rect = client_rect or bounds
    rel_x = int(x - int(anchor_rect.get("left") or 0))
    rel_y = int(y - int(anchor_rect.get("top") or 0))
    width = max(1, int(anchor_rect.get("width") or anchor_rect.get("right", 0) - anchor_rect.get("left", 0) or 1))
    height = max(1, int(anchor_rect.get("height") or anchor_rect.get("bottom", 0) - anchor_rect.get("top", 0) or 1))
    ratio_x = round(max(0.0, min(1.0, rel_x / width)), 4)
    ratio_y = round(max(0.0, min(1.0, rel_y / height)), 4)
    selector_candidates = list(hover.get("selectorCandidates") or [])
    has_selector = bool(selector_candidates)
    element = dict(hover.get("element") or {})
    highlight_bounds = dict(hover.get("highlightBounds") or element.get("bounds") or bounds)
    screen = _windows_screen()
    coordinate_anchor = {
        "mode": "window_client_relative",
        "x": rel_x,
        "y": rel_y,
        "ratioX": ratio_x,
        "ratioY": ratio_y,
        "windowRect": bounds,
        "clientRect": anchor_rect,
        "dpi": window.get("dpi") or 96,
        "monitorId": screen.get("monitorId") or "primary",
        "absoluteX": int(x),
        "absoluteY": int(y),
    }
    image_anchor = {
        "screenshotPatchRef": None,
        "ocrText": str(element.get("name") or window.get("title") or ""),
        "matchThreshold": 0.82,
        "bounds": highlight_bounds,
        "status": "deferred_patch",
    }
    payload = _base_payload(args, backend=backend)
    payload["fragileCoordinateFallback"] = not has_selector
    payload["coordinateFallback"] = not has_selector
    payload.update(
        {
            "nativeInspectorSessionId": args.native_inspector_session_id or None,
            "coordinate": {"x": int(x), "y": int(y)},
            "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
            "coordinateAnchor": coordinate_anchor,
            "imageAnchor": image_anchor,
            "screen": screen,
            "targetWindow": window,
            "highlightBounds": highlight_bounds,
            "selectorCandidates": selector_candidates,
            "target": {
                "window": {
                    **window,
                    **({"requestedTitle": args.target_window_title} if args.target_window_title else {}),
                    **({"requestedHandle": args.target_window_handle} if args.target_window_handle else {}),
                },
                **({"selector": selector_candidates[0]} if has_selector else {}),
                "spatialAnchor": {
                    "coordinateAnchor": coordinate_anchor,
                    "imageAnchor": image_anchor,
                    "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
                    "windowRelativePoint": [ratio_x, ratio_y],
                    "windowBounds": [
                        int(anchor_rect.get("left") or 0),
                        int(anchor_rect.get("top") or 0),
                        int(anchor_rect.get("right") or 0),
                        int(anchor_rect.get("bottom") or 0),
                    ],
                    "fallback": not has_selector,
                    "source": "windows_register_hotkey",
                },
            },
            "hoverSample": hover,
            "nativeHotkey": {
                "backend": "windows_register_hotkey",
                "hotkey": args.hotkey,
                "cancelHotkey": args.cancel_hotkey,
                "capturedAt": _utc_now(),
            },
            "screenshotAnchor": {
                "kind": "deferred",
                "reason": "native_hotkey_capture",
                "imageAnchor": image_anchor,
            },
        }
    )
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "targetWindow": window,
            "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
            "coordinateAnchor": coordinate_anchor,
            "imageAnchor": image_anchor,
            "nativeHotkey": payload["nativeHotkey"],
            "screenshotAnchor": payload["screenshotAnchor"],
            "nativeInspectorSessionId": args.native_inspector_session_id or None,
            "highlightBounds": highlight_bounds,
            "selectorCandidates": selector_candidates,
            "hoverSample": hover,
        }
    )
    payload["metadata"] = metadata
    return payload


def _run_windows_native(args: argparse.Namespace) -> int:
    _ensure_windows_dpi_awareness()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = wintypes.LPARAM
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    except Exception:
        pass
    capture_id = 1
    cancel_id = 2
    capture_mods, capture_vk, normalized_hotkey = _parse_windows_hotkey(args.hotkey, default="ctrl+alt+c")
    cancel_mods, cancel_vk, normalized_cancel_hotkey = _parse_windows_hotkey(args.cancel_hotkey, default="ctrl+alt+x")
    args.hotkey = normalized_hotkey
    args.cancel_hotkey = normalized_cancel_hotkey
    if not user32.RegisterHotKey(None, capture_id, capture_mods, capture_vk):
        raise RuntimeError(f"RegisterHotKey {normalized_hotkey} failed; the hotkey may be occupied by another app.")
    cancel_registered = bool(user32.RegisterHotKey(None, cancel_id, cancel_mods, cancel_vk))
    if not cancel_registered:
        _emit_status("warning", backend="windows_register_hotkey", warning=f"RegisterHotKey {normalized_cancel_hotkey} failed; Admin stop remains available.")
    _emit_status("hotkey_checked", backend="windows_register_hotkey", hotkey=args.hotkey, cancelHotkey=args.cancel_hotkey)

    if bool(getattr(args, "highlight_overlay", False)):
        try:
            import tkinter as tk
        except Exception as exc:  # pragma: no cover - host dependent.
            print(json.dumps({"ok": False, "backend": "windows_register_hotkey", "warning": f"tkinter_unavailable: {exc}"}, ensure_ascii=False), file=sys.stderr, flush=True)
        else:
            user32.UnregisterHotKey(None, capture_id)
            if cancel_registered:
                user32.UnregisterHotKey(None, cancel_id)
            posted_state = {"posted": False}
            state: dict[str, Any] = {
                "capture": False,
                "capturePoint": None,
                "cancel": False,
                "lastHover": None,
                "lastPoint": None,
                "error": "",
            }
            stop_event = threading.Event()
            hotkey_ready_event = threading.Event()
            hook_ready_event = threading.Event()

            def message_loop() -> None:
                state["hotkeyThreadId"] = int(kernel32.GetCurrentThreadId())
                thread_cancel_registered = False
                if not user32.RegisterHotKey(None, capture_id, capture_mods, capture_vk):
                    state["error"] = f"RegisterHotKey {normalized_hotkey} failed; the hotkey may be occupied by another app."
                    hotkey_ready_event.set()
                    return
                thread_cancel_registered = bool(user32.RegisterHotKey(None, cancel_id, cancel_mods, cancel_vk))
                state["cancelRegistered"] = thread_cancel_registered
                state["hotkeyRegistered"] = True
                hotkey_ready_event.set()
                try:
                    msg = wintypes.MSG()
                    while not stop_event.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                        if msg.message != WM_HOTKEY:
                            continue
                        if thread_cancel_registered and int(msg.wParam) == cancel_id:
                            state["cancel"] = True
                            break
                        if int(msg.wParam) == capture_id:
                            state["capture"] = True
                finally:
                    user32.UnregisterHotKey(None, capture_id)
                    if thread_cancel_registered:
                        user32.UnregisterHotKey(None, cancel_id)

            def hook_loop() -> None:
                state["hookThreadId"] = int(kernel32.GetCurrentThreadId())

                @HOOKPROC
                def mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
                    if n_code == HC_ACTION:
                        try:
                            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                            x = int(info.pt.x)
                            y = int(info.pt.y)
                            state["hookPoint"] = {"x": x, "y": y}
                            message = int(w_param)
                            if message == WM_LBUTTONDOWN:
                                state["capture"] = True
                                state["capturePoint"] = {"x": x, "y": y}
                                return 0 if bool(args.record_and_forward) else 1
                            if message == WM_RBUTTONDOWN:
                                state["cancel"] = True
                                return 1
                        except Exception as exc:  # pragma: no cover - depends on host hooks.
                            state["hookError"] = str(exc)
                    return int(user32.CallNextHookEx(None, n_code, w_param, l_param))

                @HOOKPROC
                def keyboard_proc(n_code: int, w_param: int, l_param: int) -> int:
                    if n_code == HC_ACTION and int(w_param) in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                        try:
                            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                            if int(info.vkCode) == VK_ESCAPE:
                                state["cancel"] = True
                                return 1
                        except Exception as exc:  # pragma: no cover - depends on host hooks.
                            state["hookError"] = str(exc)
                    return int(user32.CallNextHookEx(None, n_code, w_param, l_param))

                state["mouseHookProc"] = mouse_proc
                state["keyboardHookProc"] = keyboard_proc
                module_handle = kernel32.GetModuleHandleW(None)
                mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, module_handle, 0)
                keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_proc, module_handle, 0)
                state["mouseHook"] = int(mouse_hook or 0)
                state["keyboardHook"] = int(keyboard_hook or 0)
                if not mouse_hook:
                    state["hookError"] = "SetWindowsHookExW mouse hook failed."
                if not keyboard_hook:
                    state["hookError"] = "SetWindowsHookExW keyboard hook failed."
                state["mouseHookInstalled"] = bool(mouse_hook)
                state["keyboardHookInstalled"] = bool(keyboard_hook)
                hook_ready_event.set()
                try:
                    msg = wintypes.MSG()
                    while not stop_event.is_set():
                        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                        if result in (0, -1):
                            break
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                finally:
                    if mouse_hook:
                        user32.UnhookWindowsHookEx(mouse_hook)
                    if keyboard_hook:
                        user32.UnhookWindowsHookEx(keyboard_hook)

            thread = threading.Thread(target=message_loop, name="v8-rpa-native-inspector-hotkey", daemon=True)
            thread.start()
            hotkey_ready_event.wait(timeout=1.5)
            if state.get("error"):
                _emit_status("error", ok=False, backend="windows_register_hotkey", stage="hotkey", error=str(state["error"]))
                raise RuntimeError(str(state["error"]))
            hook_thread = threading.Thread(target=hook_loop, name="v8-rpa-native-inspector-hooks", daemon=True)
            hook_thread.start()
            hook_ready_event.wait(timeout=1.5)
            if not state.get("mouseHookInstalled") or not state.get("keyboardHookInstalled"):
                error = str(state.get("hookError") or "SetWindowsHookExW failed; mouse/keyboard hooks are unavailable.")
                _emit_status(
                    "error",
                    ok=False,
                    backend="windows_register_hotkey",
                    stage="hook",
                    error=error,
                    mouseHookInstalled=bool(state.get("mouseHookInstalled")),
                    keyboardHookInstalled=bool(state.get("keyboardHookInstalled")),
                )
                stop_event.set()
                raise RuntimeError(error)

            root = tk.Tk()
            root.title("V8 RPA Native Inspector")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            overlay_bg = "#0f172a"
            try:
                root.attributes("-alpha", 0.14)
            except Exception:
                pass
            screen = _windows_screen()
            screen_w = int(screen.get("width") or root.winfo_screenwidth())
            screen_h = int(screen.get("height") or root.winfo_screenheight())
            screen_left = int(screen.get("left") or 0)
            screen_top = int(screen.get("top") or 0)
            root.geometry(f"{screen_w}x{screen_h}{_tk_geometry_offset(screen_left)}{_tk_geometry_offset(screen_top)}")
            root.configure(bg=overlay_bg, cursor="crosshair")
            canvas = tk.Canvas(root, width=screen_w, height=screen_h, highlightthickness=0, bg=overlay_bg, cursor="crosshair")
            canvas.place(x=0, y=0)
            hint_label = tk.Label(
                root,
                text=f"V8 RPA Inspector armed · click or {args.hotkey} to capture · Esc/{args.cancel_hotkey} cancel",
                bg="#111827",
                fg="#ffffff",
                padx=12,
                pady=8,
                font=("Segoe UI", 10, "bold"),
            )
            hint_label.place(x=24, y=24)
            rect_id = canvas.create_rectangle(0, 0, 0, 0, outline="#22d3ee", width=3)
            rect_fill_id = canvas.create_rectangle(0, 0, 0, 0, outline="", fill="")
            crosshair_h = canvas.create_line(0, 0, 0, 0, fill="#f8fafc", width=1, dash=(4, 4))
            crosshair_v = canvas.create_line(0, 0, 0, 0, fill="#f8fafc", width=1, dash=(4, 4))
            cursor_dot = canvas.create_oval(0, 0, 0, 0, outline="#111827", fill="#f59e0b", width=2)
            label_id = canvas.create_text(16, 16, text="", fill="#ffffff", anchor="nw", font=("Segoe UI", 10, "bold"))
            label_bg_id = canvas.create_rectangle(10, 10, 420, 44, fill="#111827", outline="#111827")
            canvas.tag_raise(label_bg_id)
            canvas.tag_raise(label_id)

            interval = max(34, int(1000 / max(2, min(30, float(getattr(args, "hover_sample_hz", 12) or 12)))))
            overlay_hwnd = int(root.winfo_id())
            try:
                root.update_idletasks()
                original_style = int(user32.GetWindowLongW(wintypes.HWND(overlay_hwnd), -20))
                user32.SetWindowLongW(
                    wintypes.HWND(overlay_hwnd),
                    -20,
                    original_style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
                )
            except Exception as exc:  # pragma: no cover - host dependent.
                state["styleWarning"] = str(exc)
            overlay_bounds = {"left": screen_left, "top": screen_top, "width": screen_w, "height": screen_h}
            if screen_w <= 0 or screen_h <= 0:
                error = "overlay_bounds_mismatch: invalid screen bounds"
                _emit_status("error", ok=False, backend="windows_register_hotkey", stage="overlay", error=error, overlayBounds=overlay_bounds)
                stop_event.set()
                try:
                    root.destroy()
                except Exception:
                    pass
                raise RuntimeError(error)
            _emit_status(
                "ready",
                backend="windows_register_hotkey",
                hotkey=args.hotkey,
                cancelHotkey=args.cancel_hotkey,
                hotkeyRegistered=bool(state.get("hotkeyRegistered")),
                mouseHookInstalled=bool(state.get("mouseHookInstalled")),
                keyboardHookInstalled=bool(state.get("keyboardHookInstalled")),
                overlayReady=True,
                targetReady=True,
                overlayBounds=overlay_bounds,
                styleWarning=state.get("styleWarning") or None,
            )

            def to_canvas_x(value: int) -> int:
                return int(value) - screen_left

            def to_canvas_y(value: int) -> int:
                return int(value) - screen_top

            def sample_target_under_overlay(x: int, y: int) -> dict[str, Any]:
                """Sample the real target below the click-through inspector window."""
                return _windows_hover_sample(args, x, y)

            def close() -> None:
                stop_event.set()
                for thread_key in ("hotkeyThreadId", "hookThreadId"):
                    thread_id = int(state.get(thread_key) or 0)
                    if thread_id:
                        try:
                            kernel32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                        except Exception:
                            pass
                try:
                    root.destroy()
                except Exception:
                    pass

            def post_capture(hover: dict[str, Any], x: int, y: int) -> None:
                if hover.get("ignored"):
                    canvas.itemconfig(label_id, text="V8 Admin / Desktop Live ignored.\nMove to the target app, then click or press capture again.")
                    return
                payload = _build_windows_payload(args, x=x, y=y, backend="windows_register_hotkey", hover_sample=hover)
                try:
                    _post_event(args.engine_url, args.recording_id, payload)
                    posted_state["posted"] = True
                    mode = "selector" if hover.get("selectorCandidates") else "coordinate fallback"
                    _emit_status(
                        "captured",
                        backend="windows_register_hotkey",
                        mode=mode,
                        selectorAvailable=bool(hover.get("selectorCandidates")),
                        windowTitle=(hover.get("targetWindow") or {}).get("title") or hover.get("windowTitle"),
                    )
                    canvas.itemconfig(label_id, text=f"Captured {mode}: {hover.get('windowTitle') or args.target_label}")
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    _emit_status("error", ok=False, backend="windows_register_hotkey", stage="post_event", error=str(exc))
                if not args.persistent:
                    close()

            def capture_current(event=None) -> None:
                if event is not None:
                    x = int(getattr(event, "x_root", 0) or 0)
                    y = int(getattr(event, "y_root", 0) or 0)
                    hover = sample_target_under_overlay(x, y)
                else:
                    point = dict(state.get("lastPoint") or {})
                    x = int(point.get("x") or 0)
                    y = int(point.get("y") or 0)
                    hover = dict(state.get("lastHover") or {}) or sample_target_under_overlay(x, y)
                if not x and not y:
                    x, y = _windows_cursor_position()
                    hover = sample_target_under_overlay(x, y)
                post_capture(hover, x, y)

            def tick() -> None:
                if state.get("cancel"):
                    _emit_status("cancelled", backend="windows_register_hotkey", reason="user_cancel")
                    close()
                    return
                x, y = _windows_cursor_position()
                hover = _windows_light_hover_sample(args, x, y)
                state["lastHover"] = hover
                state["lastPoint"] = {"x": x, "y": y}
                bounds = dict(hover.get("highlightBounds") or {})
                canvas_x = to_canvas_x(x)
                canvas_y = to_canvas_y(y)
                left = to_canvas_x(int(bounds.get("left") or x - 24))
                top = to_canvas_y(int(bounds.get("top") or y - 24))
                right = to_canvas_x(int(bounds.get("right") or x + 24))
                bottom = to_canvas_y(int(bounds.get("bottom") or y + 24))
                outline = "#fb7185" if hover.get("ignored") else ("#22c55e" if hover.get("selectorCandidates") else "#f59e0b")
                canvas.itemconfig(rect_id, outline=outline)
                canvas.itemconfig(rect_fill_id, fill="")
                canvas.coords(rect_id, left, top, right, bottom)
                canvas.coords(rect_fill_id, left, top, right, bottom)
                canvas.coords(crosshair_h, 0, canvas_y, screen_w, canvas_y)
                canvas.coords(crosshair_v, canvas_x, 0, canvas_x, screen_h)
                canvas.coords(cursor_dot, canvas_x - 5, canvas_y - 5, canvas_x + 5, canvas_y + 5)
                canvas.tag_raise(rect_fill_id)
                canvas.tag_raise(rect_id)
                canvas.tag_raise(crosshair_h)
                canvas.tag_raise(crosshair_v)
                canvas.tag_raise(cursor_dot)
                element = dict(hover.get("element") or {})
                label = element.get("name") or hover.get("windowTitle") or args.target_label
                kind = element.get("controlType") or element.get("backend") or ("coordinate fallback" if not hover.get("selectorCandidates") else "element")
                status = "ignored admin surface" if hover.get("ignored") else f"{kind} · click/{args.hotkey} capture · Esc/{args.cancel_hotkey} cancel"
                hint_label.configure(text=f"V8 RPA Inspector · {status}")
                hint_label.lift()
                canvas.itemconfig(label_id, text=f"{label}\n{status}")
                bbox = canvas.bbox(label_id) or (10, 10, 420, 44)
                canvas.coords(label_bg_id, bbox[0] - 8, bbox[1] - 6, bbox[2] + 8, bbox[3] + 6)
                canvas.tag_raise(label_bg_id)
                canvas.tag_raise(label_id)
                if state.get("capture"):
                    state["capture"] = False
                    point = dict(state.get("capturePoint") or {})
                    capture_x = int(point.get("x") or x)
                    capture_y = int(point.get("y") or y)
                    capture_hover = sample_target_under_overlay(capture_x, capture_y)
                    post_capture(capture_hover, capture_x, capture_y)
                root.after(interval, tick)

            root.bind("<Escape>", lambda _event=None: close())
            root.bind("<Button-1>", capture_current)
            root.bind("<Button-3>", lambda _event=None: close())
            canvas.bind("<Button-1>", capture_current)
            canvas.bind("<Button-3>", lambda _event=None: close())
            hint_label.bind("<Button-1>", capture_current)
            root.bind_all("<Button-1>", capture_current)
            root.bind_all("<Button-3>", lambda _event=None: close())
            root.after(50, tick)
            root.mainloop()
            stop_event.set()
            return 0 if posted_state["posted"] else 1

    _emit_status(
        "ready",
        backend="windows_register_hotkey",
        hotkey=args.hotkey,
        cancelHotkey=args.cancel_hotkey,
        hotkeyRegistered=True,
        mouseHookInstalled=False,
        keyboardHookInstalled=False,
        overlayReady=False,
        targetReady=True,
    )
    posted = False
    try:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message != WM_HOTKEY:
                continue
            if cancel_registered and int(msg.wParam) == cancel_id:
                break
            if int(msg.wParam) != capture_id:
                continue
            x, y = _windows_cursor_position()
            hover = _windows_hover_sample(args, x, y)
            if hover.get("ignored"):
                print(json.dumps({"ok": False, "ignored": "admin_surface", "window": hover.get("targetWindow")}, ensure_ascii=False), file=sys.stderr, flush=True)
                continue
            payload = _build_windows_payload(args, x=x, y=y, backend="windows_register_hotkey", hover_sample=hover)
            try:
                _post_event(args.engine_url, args.recording_id, payload)
                posted = True
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            if not args.persistent:
                break
    finally:
        user32.UnregisterHotKey(None, capture_id)
        if cancel_registered:
            user32.UnregisterHotKey(None, cancel_id)
    return 0 if posted else 1


def _run_overlay(args: argparse.Namespace) -> int:
    if sys.platform.startswith("win"):
        _ensure_windows_dpi_awareness()
    try:
        import tkinter as tk
    except Exception as exc:  # pragma: no cover - depends on host Python install.
        print(json.dumps({"ok": False, "error": f"tkinter_unavailable: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2

    root = tk.Tk()
    root.title("V8 RPA Capture Assistant")
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.22)
    except Exception:
        pass
    root.overrideredirect(True)
    if sys.platform.startswith("win"):
        screen = _windows_screen()
        screen_left = int(screen.get("left") or 0)
        screen_top = int(screen.get("top") or 0)
        screen_w = int(screen.get("width") or root.winfo_screenwidth())
        screen_h = int(screen.get("height") or root.winfo_screenheight())
    else:
        screen_left = 0
        screen_top = 0
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
    root.geometry(f"{screen_w}x{screen_h}{_tk_geometry_offset(screen_left)}{_tk_geometry_offset(screen_top)}")
    root.configure(bg="#6d28d9", cursor="crosshair")

    label = tk.Label(
        root,
        text=(f"V8 RPA Capture: hover target, click to capture for {args.target_label}   |   {args.hotkey} capture   |   Esc cancel"),
        bg="#111827",
        fg="#ffffff",
        padx=16,
        pady=10,
        font=("Segoe UI", 12, "bold"),
    )
    canvas = tk.Canvas(root, width=screen_w, height=screen_h, highlightthickness=0, bg="#6d28d9")
    canvas.place(x=0, y=0)
    label.place(x=24, y=24)
    label.lift()
    _emit_status(
        "ready",
        backend="fallback_overlay",
        hotkey=args.hotkey,
        cancelHotkey=args.cancel_hotkey,
        hotkeyRegistered=False,
        mouseHookInstalled=False,
        keyboardHookInstalled=False,
        overlayReady=True,
        targetReady=True,
        overlayBounds={"left": screen_left, "top": screen_top, "width": screen_w, "height": screen_h},
    )
    highlight_id = canvas.create_rectangle(0, 0, 0, 0, outline="#22d3ee", width=3)
    crosshair_h = canvas.create_line(0, 0, 0, 0, fill="#f8fafc", width=1, dash=(4, 4))
    crosshair_v = canvas.create_line(0, 0, 0, 0, fill="#f8fafc", width=1, dash=(4, 4))
    status = {"posted": False, "last": {"x": screen_w // 2, "y": screen_h // 2}}

    def finish_without_event(_event=None) -> None:
        _emit_status("cancelled", backend="fallback_overlay", reason="user_cancel")
        root.destroy()

    def hover(event) -> None:
        x = int(event.x_root)
        y = int(event.y_root)
        canvas_x = x - screen_left
        canvas_y = y - screen_top
        status["last"] = {"x": x, "y": y}
        box = 36
        canvas.coords(highlight_id, max(0, canvas_x - box), max(0, canvas_y - box), min(screen_w, canvas_x + box), min(screen_h, canvas_y + box))
        canvas.coords(crosshair_h, 0, canvas_y, screen_w, canvas_y)
        canvas.coords(crosshair_v, canvas_x, 0, canvas_x, screen_h)

    def capture(event=None) -> None:
        last = dict(status.get("last") or {})
        if event is not None:
            last = {"x": int(event.x_root), "y": int(event.y_root)}
        x = int(last.get("x") or 0)
        y = int(last.get("y") or 0)
        if sys.platform.startswith("win"):
            hover_sample = _windows_hover_sample(args, x, y)
            payload = _build_windows_payload(args, x=x, y=y, backend="fallback_overlay", hover_sample=hover_sample)
        else:
            payload = _base_payload(args, backend="fallback_overlay")
            payload.update(
                {
                    "coordinate": {"x": x, "y": y},
                    "screen": {"width": int(screen_w), "height": int(screen_h), "devicePixelRatio": 1, "monitorId": "primary"},
                    "target": {
                        "window": {
                            "title": args.target_window_title or args.target_label,
                            **({"handle": args.target_window_handle} if args.target_window_handle else {}),
                        }
                    },
                    "hoverSample": {
                        "x": x,
                        "y": y,
                        "highlight": "cursor_box",
                        "targetLabel": args.target_label,
                    },
                }
            )
        try:
            _post_event(args.engine_url, args.recording_id, payload)
            status["posted"] = True
            _emit_status(
                "captured",
                backend="fallback_overlay",
                mode="selector" if payload.get("selectorCandidates") else "coordinate fallback",
                selectorAvailable=bool(payload.get("selectorCandidates")),
            )
            label.configure(text=f"Captured {last.get('x')},{last.get('y')} for {args.target_label}   |   Esc to finish")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _emit_status("error", ok=False, backend="fallback_overlay", stage="post_event", error=str(exc))
        finally:
            if not args.persistent:
                root.destroy()

    root.bind("<Escape>", finish_without_event)
    root.bind("<Motion>", hover)
    root.bind("<Button-1>", capture)
    root.bind("<Button-3>", finish_without_event)
    root.bind("<Control-Alt-c>", capture)
    root.bind("<Control-Alt-C>", capture)
    root.bind("<Control-Return>", capture)
    root.mainloop()
    return 0 if status["posted"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="V8 RPA capture assistant")
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--engine-url", default="http://127.0.0.1:9530")
    parser.add_argument("--action", default="click")
    parser.add_argument("--target-label", default="desktop")
    parser.add_argument("--mode", default="capture_only")
    parser.add_argument("--hotkey", default="ctrl+alt+c")
    parser.add_argument("--cancel-hotkey", default="ctrl+alt+x")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--persistent", action="store_true")
    parser.add_argument("--record-and-forward", action="store_true")
    parser.add_argument("--target-window-title", default="")
    parser.add_argument("--target-window-handle", default="")
    parser.add_argument("--target-window-process-id", default="")
    parser.add_argument("--native-inspector-session-id", default="")
    parser.add_argument("--hover-sample-hz", type=float, default=12.0)
    parser.add_argument("--highlight-overlay", dest="highlight_overlay", action="store_true", default=True)
    parser.add_argument("--no-highlight-overlay", dest="highlight_overlay", action="store_false")
    parser.add_argument("--ignore-admin-surface", dest="ignore_admin_surface", action="store_true", default=True)
    parser.add_argument("--allow-admin-surface", dest="ignore_admin_surface", action="store_false")
    args = parser.parse_args()

    backend = str(args.backend or "auto").strip().lower()
    if backend in {"auto", "native_hotkey", "windows_register_hotkey", "windows_fla_ui_helper"} and sys.platform.startswith("win"):
        print(
            json.dumps(
                {
                    "event": "rpa_capture_assistant.error",
                    "ok": False,
                    "backend": "legacy_tk_capture_assistant",
                    "stage": "legacy_tk_native_disabled",
                    "error": "Windows native capture now requires the V8.Rpa.NativeInspector .NET/FlaUI helper. Use backend=fallback_overlay only for diagnostics.",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    if backend in {"native_hotkey", "mac_ax"} and sys.platform == "darwin":
        print(json.dumps({"ok": False, "backend": "mac_ax", "error": "requires_accessibility_permission"}, ensure_ascii=False), file=sys.stderr)
        return 2
    if backend in {"native_hotkey", "linux_portal", "x11_atspi"} and sys.platform.startswith("linux"):
        print(json.dumps({"ok": False, "backend": "linux_portal", "error": "wayland_portal_limited_or_atspi_unavailable"}, ensure_ascii=False), file=sys.stderr)
        return 2
    return _run_overlay(args)


if __name__ == "__main__":
    raise SystemExit(main())
