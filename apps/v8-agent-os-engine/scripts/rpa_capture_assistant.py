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
    request = urllib.request.Request(
        f"{engine_url.rstrip('/')}/rpa/recordings/{recording_id}/capture-assistant/capture",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - localhost/Admin-controlled URL.
        response.read()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
    user32 = ctypes.windll.user32
    return {
        "width": int(user32.GetSystemMetrics(0)),
        "height": int(user32.GetSystemMetrics(1)),
        "devicePixelRatio": 1,
        "monitorId": "primary",
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
    rel_x = int(x - int(bounds.get("left") or 0))
    rel_y = int(y - int(bounds.get("top") or 0))
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


def _build_windows_payload(args: argparse.Namespace, *, x: int, y: int, backend: str, hover_sample: dict[str, Any] | None = None) -> dict[str, Any]:
    hover = hover_sample or _windows_hover_sample(args, x, y)
    window = dict(hover.get("targetWindow") or _windows_window_info(x, y))
    bounds = dict(window.get("bounds") or {})
    rel_x = int(x - int(bounds.get("left") or 0))
    rel_y = int(y - int(bounds.get("top") or 0))
    selector_candidates = list(hover.get("selectorCandidates") or [])
    has_selector = bool(selector_candidates)
    element = dict(hover.get("element") or {})
    highlight_bounds = dict(hover.get("highlightBounds") or element.get("bounds") or bounds)
    payload = _base_payload(args, backend=backend)
    payload["fragileCoordinateFallback"] = not has_selector
    payload["coordinateFallback"] = not has_selector
    payload.update(
        {
            "nativeInspectorSessionId": args.native_inspector_session_id or None,
            "coordinate": {"x": int(x), "y": int(y)},
            "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
            "screen": _windows_screen(),
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
                **({"spatialAnchor": {
                    "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
                    "windowBounds": bounds,
                    "fallback": True,
                    "source": "windows_register_hotkey",
                }} if not has_selector else {}),
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
            },
        }
    )
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "targetWindow": window,
            "windowRelativeCoordinate": {"x": rel_x, "y": rel_y},
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
    user32 = ctypes.windll.user32
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
        print(json.dumps({"ok": False, "warning": f"RegisterHotKey {normalized_cancel_hotkey} failed; Admin stop remains available."}, ensure_ascii=False), file=sys.stderr, flush=True)
    print(json.dumps({"ok": True, "backend": "windows_register_hotkey", "hotkey": args.hotkey, "cancelHotkey": args.cancel_hotkey}, ensure_ascii=False), flush=True)

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
            state: dict[str, Any] = {"capture": False, "cancel": False, "lastHover": None, "lastPoint": None, "error": ""}
            stop_event = threading.Event()
            ready_event = threading.Event()

            def message_loop() -> None:
                thread_cancel_registered = False
                if not user32.RegisterHotKey(None, capture_id, capture_mods, capture_vk):
                    state["error"] = f"RegisterHotKey {normalized_hotkey} failed; the hotkey may be occupied by another app."
                    ready_event.set()
                    return
                thread_cancel_registered = bool(user32.RegisterHotKey(None, cancel_id, cancel_mods, cancel_vk))
                state["cancelRegistered"] = thread_cancel_registered
                ready_event.set()
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

            thread = threading.Thread(target=message_loop, name="v8-rpa-native-inspector-hotkey", daemon=True)
            thread.start()
            ready_event.wait(timeout=1.5)
            if state.get("error"):
                raise RuntimeError(str(state["error"]))

            root = tk.Tk()
            root.title("V8 RPA Native Inspector")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            transparent = "#ff00ff"
            try:
                root.attributes("-transparentcolor", transparent)
            except Exception:
                try:
                    root.attributes("-alpha", 0.18)
                except Exception:
                    pass
            screen = _windows_screen()
            screen_w = int(screen.get("width") or root.winfo_screenwidth())
            screen_h = int(screen.get("height") or root.winfo_screenheight())
            root.geometry(f"{screen_w}x{screen_h}+0+0")
            root.configure(bg=transparent)
            canvas = tk.Canvas(root, width=screen_w, height=screen_h, highlightthickness=0, bg=transparent)
            canvas.place(x=0, y=0)
            rect_id = canvas.create_rectangle(0, 0, 0, 0, outline="#22d3ee", width=3)
            label_id = canvas.create_text(16, 16, text="", fill="#ffffff", anchor="nw", font=("Segoe UI", 10, "bold"))
            label_bg_id = canvas.create_rectangle(10, 10, 420, 44, fill="#111827", outline="#111827")
            canvas.tag_raise(label_bg_id)
            canvas.tag_raise(label_id)
            try:
                hwnd = int(root.winfo_id())
                ex_style = int(user32.GetWindowLongW(wintypes.HWND(hwnd), -20))
                user32.SetWindowLongW(wintypes.HWND(hwnd), -20, ex_style | 0x00080000 | 0x00000020 | 0x00000008 | 0x00000080 | 0x08000000)
            except Exception:
                pass

            interval = max(34, int(1000 / max(2, min(30, float(getattr(args, "hover_sample_hz", 12) or 12)))))

            def close() -> None:
                stop_event.set()
                try:
                    root.destroy()
                except Exception:
                    pass

            def post_capture(hover: dict[str, Any], x: int, y: int) -> None:
                if hover.get("ignored"):
                    canvas.itemconfig(label_id, text="V8 Admin / Desktop Live ignored. Move to target app, then press capture again.")
                    return
                payload = _build_windows_payload(args, x=x, y=y, backend="windows_register_hotkey", hover_sample=hover)
                try:
                    _post_event(args.engine_url, args.recording_id, payload)
                    posted_state["posted"] = True
                    canvas.itemconfig(label_id, text=f"Captured: {hover.get('windowTitle') or args.target_label}")
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
                if not args.persistent:
                    close()

            def tick() -> None:
                if state.get("cancel"):
                    close()
                    return
                x, y = _windows_cursor_position()
                hover = _windows_hover_sample(args, x, y)
                state["lastHover"] = hover
                state["lastPoint"] = {"x": x, "y": y}
                bounds = dict(hover.get("highlightBounds") or {})
                outline = "#fb7185" if hover.get("ignored") else ("#22c55e" if hover.get("selectorCandidates") else "#f59e0b")
                canvas.itemconfig(rect_id, outline=outline)
                canvas.coords(
                    rect_id,
                    int(bounds.get("left") or x - 24),
                    int(bounds.get("top") or y - 24),
                    int(bounds.get("right") or x + 24),
                    int(bounds.get("bottom") or y + 24),
                )
                element = dict(hover.get("element") or {})
                label = element.get("name") or hover.get("windowTitle") or args.target_label
                kind = element.get("controlType") or element.get("backend") or ("coordinate fallback" if not hover.get("selectorCandidates") else "element")
                status = "ignored admin surface" if hover.get("ignored") else f"{kind} · {args.hotkey} capture · {args.cancel_hotkey} cancel"
                canvas.itemconfig(label_id, text=f"{label}\n{status}")
                bbox = canvas.bbox(label_id) or (10, 10, 420, 44)
                canvas.coords(label_bg_id, bbox[0] - 8, bbox[1] - 6, bbox[2] + 8, bbox[3] + 6)
                canvas.tag_raise(label_bg_id)
                canvas.tag_raise(label_id)
                if state.get("capture"):
                    state["capture"] = False
                    post_capture(hover, x, y)
                root.after(interval, tick)

            root.bind("<Escape>", lambda _event=None: close())
            root.after(50, tick)
            root.mainloop()
            stop_event.set()
            return 0 if posted_state["posted"] else 1

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
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    root.geometry(f"{screen_w}x{screen_h}+0+0")
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
    highlight_id = canvas.create_rectangle(0, 0, 0, 0, outline="#22d3ee", width=3)
    crosshair_h = canvas.create_line(0, 0, 0, 0, fill="#f8fafc", width=1, dash=(4, 4))
    crosshair_v = canvas.create_line(0, 0, 0, 0, fill="#f8fafc", width=1, dash=(4, 4))
    status = {"posted": False, "last": {"x": screen_w // 2, "y": screen_h // 2}}

    def finish_without_event(_event=None) -> None:
        root.destroy()

    def hover(event) -> None:
        x = int(event.x_root)
        y = int(event.y_root)
        status["last"] = {"x": x, "y": y}
        box = 36
        canvas.coords(highlight_id, max(0, event.x - box), max(0, event.y - box), min(screen_w, event.x + box), min(screen_h, event.y + box))
        canvas.coords(crosshair_h, 0, event.y, screen_w, event.y)
        canvas.coords(crosshair_v, event.x, 0, event.x, screen_h)

    def capture(event=None) -> None:
        last = dict(status.get("last") or {})
        if event is not None:
            last = {"x": int(event.x_root), "y": int(event.y_root)}
        payload = _base_payload(args, backend="fallback_overlay")
        payload.update(
            {
                "coordinate": {"x": int(last.get("x") or 0), "y": int(last.get("y") or 0)},
                "screen": {"width": int(screen_w), "height": int(screen_h), "devicePixelRatio": 1, "monitorId": "primary"},
                "target": {
                    "window": {
                        "title": args.target_window_title or args.target_label,
                        **({"handle": args.target_window_handle} if args.target_window_handle else {}),
                    }
                },
                "hoverSample": {
                    "x": int(last.get("x") or 0),
                    "y": int(last.get("y") or 0),
                    "highlight": "cursor_box",
                    "targetLabel": args.target_label,
                },
            }
        )
        try:
            _post_event(args.engine_url, args.recording_id, payload)
            status["posted"] = True
            label.configure(text=f"Captured {last.get('x')},{last.get('y')} for {args.target_label}   |   Esc to finish")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
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
    if backend in {"auto", "native_hotkey", "windows_register_hotkey"} and sys.platform.startswith("win"):
        try:
            return _run_windows_native(args)
        except Exception as exc:
            if backend != "auto":
                print(json.dumps({"ok": False, "backend": "windows_register_hotkey", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
                return 2
            print(json.dumps({"ok": False, "backend": "windows_register_hotkey", "fallback": "fallback_overlay", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    if backend in {"native_hotkey", "mac_ax"} and sys.platform == "darwin":
        print(json.dumps({"ok": False, "backend": "mac_ax", "error": "requires_accessibility_permission"}, ensure_ascii=False), file=sys.stderr)
        return 2
    if backend in {"native_hotkey", "linux_portal", "x11_atspi"} and sys.platform.startswith("linux"):
        print(json.dumps({"ok": False, "backend": "linux_portal", "error": "wayland_portal_limited_or_atspi_unavailable"}, ensure_ascii=False), file=sys.stderr)
        return 2
    return _run_overlay(args)


if __name__ == "__main__":
    raise SystemExit(main())
