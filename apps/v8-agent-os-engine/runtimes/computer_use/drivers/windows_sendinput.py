from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

try:  # pragma: no cover - depends on local Windows env
    import win32clipboard
except Exception:  # pragma: no cover
    win32clipboard = None

from ..clipboard_payload import normalize_clipboard_payload, normalize_file_paths
from .windows_hotkeys import MANAGED_MODIFIER_VKS, ParsedHotkeyStroke, parse_hotkey_sequence


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_TAB = 0x09
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_RETURN = 0x0D
VK_F4 = 0x73
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_A = 0x41
VK_V = 0x56
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


class DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


@dataclass(slots=True)
class SendInputClickResult:
    point: Tuple[int, int]
    double: bool
    strategy: str

    def as_dict(self) -> dict[str, object]:
        return {
            "point": [int(self.point[0]), int(self.point[1])],
            "double": bool(self.double),
            "strategy": self.strategy,
        }


@dataclass(frozen=True, slots=True)
class _ModifierNormalizationState:
    initial_pressed: frozenset[int]
    current_pressed: frozenset[int]
    released_conflicts: tuple[int, ...]


class SendInputClickEngine:
    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = ctypes.c_void_p
        self._kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalUnlock.restype = wintypes.BOOL
        self._kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalFree.restype = ctypes.c_void_p
        self._user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        self._user32.SetClipboardData.restype = ctypes.c_void_p

    def is_available(self) -> bool:
        return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32")

    def click(
        self,
        point: Iterable[int],
        *,
        double: bool = False,
        settle_ms: int = 30,
    ) -> SendInputClickResult:
        x, y = [int(item) for item in point]
        self._send_move(x, y)
        self._send_click(button="left", double=bool(double), settle_ms=max(10, int(settle_ms)))
        return SendInputClickResult(
            point=(x, y),
            double=bool(double),
            strategy="sendinput_double_click" if double else "sendinput_click",
        )

    def move(self, point: Iterable[int], *, settle_ms: int = 20) -> str:
        x, y = [int(item) for item in point]
        self._send_move(x, y)
        time.sleep(max(5, int(settle_ms)) / 1000.0)
        return "sendinput_move"

    def right_click(self, point: Iterable[int], *, settle_ms: int = 30) -> str:
        x, y = [int(item) for item in point]
        self._send_move(x, y)
        self._send_click(button="right", double=False, settle_ms=max(10, int(settle_ms)))
        return "sendinput_right_click"

    def drag(
        self,
        start_point: Iterable[int],
        end_point: Iterable[int],
        *,
        steps: int = 12,
        hold_ms: int = 80,
        settle_ms: int = 40,
    ) -> str:
        start_x, start_y = [int(item) for item in start_point]
        end_x, end_y = [int(item) for item in end_point]
        path = self._interpolate_path((start_x, start_y), (end_x, end_y), steps=max(2, int(steps)))
        self._send_move(start_x, start_y)
        self._send_inputs([self._mouse_input(flags=MOUSEEVENTF_LEFTDOWN)])
        time.sleep(max(10, int(hold_ms)) / 1000.0)
        for x, y in path[1:]:
            normalized_x, normalized_y = self._normalize_absolute_point(x=x, y=y)
            self._send_inputs(
                [
                    self._mouse_input(
                        dx=normalized_x,
                        dy=normalized_y,
                        flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    )
                ]
            )
            time.sleep(0.01)
        self._send_inputs([self._mouse_input(flags=MOUSEEVENTF_LEFTUP)])
        time.sleep(max(10, int(settle_ms)) / 1000.0)
        return "sendinput_drag"

    def scroll(self, amount: int, *, point: Iterable[int] | None = None, settle_ms: int = 30) -> str:
        if point is not None:
            x, y = [int(item) for item in point]
            self._send_move(x, y)
        self._send_inputs([self._mouse_input(flags=MOUSEEVENTF_WHEEL, mouse_data=int(amount) * 120)])
        time.sleep(max(10, int(settle_ms)) / 1000.0)
        return "sendinput_scroll"

    def type_text(
        self,
        text: str,
        *,
        file_paths: Iterable[str] | None = None,
        clear_first: bool = False,
        press_enter: bool = False,
        settle_ms: int = 40,
    ) -> str:
        clipboard_payload = normalize_clipboard_payload(text=text, file_paths=list(file_paths or []))
        previous_payload = self._snapshot_clipboard_payload()
        try:
            if clear_first:
                self.clear_text(settle_ms=settle_ms)
            if list(clipboard_payload.get("file_paths") or []):
                self._set_clipboard_payload(file_paths=clipboard_payload.get("file_paths"))
                self.hotkey(VK_CONTROL, VK_V, settle_ms=settle_ms)
                time.sleep(max(20, int(settle_ms)) / 1000.0)
            if clipboard_payload.get("text") is not None:
                if clear_first and not list(clipboard_payload.get("file_paths") or []):
                    # 某些自绘输入框会吞掉清空动作，但仍能保留选择态；粘贴前再次全选可确保覆盖残留文本。
                    self.hotkey(VK_CONTROL, VK_A, settle_ms=settle_ms)
                self._set_clipboard_payload(text=clipboard_payload.get("text"))
                self.hotkey(VK_CONTROL, VK_V, settle_ms=settle_ms)
            if press_enter:
                self.key_press(VK_RETURN, settle_ms=settle_ms)
        finally:
            self._restore_clipboard_payload(previous_payload)
        if list(clipboard_payload.get("file_paths") or []) and clipboard_payload.get("text") is not None:
            return "sendinput_clipboard_files_and_text"
        if list(clipboard_payload.get("file_paths") or []):
            return "sendinput_clipboard_files"
        return "sendinput_clipboard_text"

    def clear_text(self, *, settle_ms: int = 40) -> None:
        # 自绘搜索框经常吞掉单次 Backspace，这里做两轮清空以降低残留旧文本的概率。
        self.hotkey(VK_CONTROL, VK_A, settle_ms=settle_ms)
        self.key_press(VK_DELETE, settle_ms=settle_ms)
        self.hotkey(VK_CONTROL, VK_A, settle_ms=settle_ms)
        self.key_press(VK_BACK, settle_ms=settle_ms)

    def hotkey(self, modifier_vk: int, key_vk: int, *, settle_ms: int = 40) -> None:
        self._send_inputs(
            [
                self._keyboard_input(modifier_vk, key_up=False),
                self._keyboard_input(key_vk, key_up=False),
                self._keyboard_input(key_vk, key_up=True),
                self._keyboard_input(modifier_vk, key_up=True),
            ]
        )
        time.sleep(max(10, int(settle_ms)) / 1000.0)

    def send_hotkey_sequence(self, sequence: str, *, settle_ms: int = 40) -> str:
        strokes = parse_hotkey_sequence(sequence)
        modifier_state = self._normalize_modifier_state()
        held_modifiers: set[int] = set()
        try:
            for stroke in strokes:
                for _ in range(max(1, int(stroke.repeat))):
                    target_modifiers = frozenset(
                        int(modifier)
                        for modifier in (*tuple(getattr(stroke, "modifiers", ()) or ()), *tuple(held_modifiers))
                        if int(modifier) in MANAGED_MODIFIER_VKS
                    )
                    modifier_state = self._sync_modifier_state(
                        state=modifier_state,
                        target_pressed=target_modifiers,
                    )
                    self._send_hotkey_stroke(
                        stroke,
                        settle_ms=settle_ms,
                    )
                    if stroke.key_vk in MANAGED_MODIFIER_VKS:
                        if stroke.event_type == "down":
                            held_modifiers.add(int(stroke.key_vk))
                            modifier_state = _ModifierNormalizationState(
                                initial_pressed=modifier_state.initial_pressed,
                                current_pressed=frozenset(set(modifier_state.current_pressed) | {int(stroke.key_vk)}),
                                released_conflicts=modifier_state.released_conflicts,
                            )
                        elif stroke.event_type == "up":
                            held_modifiers.discard(int(stroke.key_vk))
                            modifier_state = _ModifierNormalizationState(
                                initial_pressed=modifier_state.initial_pressed,
                                current_pressed=frozenset(
                                    item for item in modifier_state.current_pressed if int(item) != int(stroke.key_vk)
                                ),
                                released_conflicts=modifier_state.released_conflicts,
                            )
        finally:
            self._restore_modifier_state(modifier_state)
        return "sendinput_hotkey_sequence"

    def key_press(self, key_vk: int, *, settle_ms: int = 40) -> None:
        self._send_inputs(
            [
                self._keyboard_input(key_vk, key_up=False),
                self._keyboard_input(key_vk, key_up=True),
            ]
        )
        time.sleep(max(10, int(settle_ms)) / 1000.0)

    def _send_move(self, x: int, y: int) -> None:
        normalized_x, normalized_y = self._normalize_absolute_point(x=x, y=y)
        self._send_inputs(
            [
                self._mouse_input(
                    dx=normalized_x,
                    dy=normalized_y,
                    flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                )
            ]
        )

    def _send_click(self, *, button: str, double: bool, settle_ms: int) -> None:
        normalized_button = str(button or "left").strip().lower()
        if normalized_button == "right":
            down_flag = MOUSEEVENTF_RIGHTDOWN
            up_flag = MOUSEEVENTF_RIGHTUP
        else:
            down_flag = MOUSEEVENTF_LEFTDOWN
            up_flag = MOUSEEVENTF_LEFTUP
        click_count = 2 if double else 1
        for _ in range(click_count):
            self._send_inputs(
                [
                    self._mouse_input(flags=down_flag),
                    self._mouse_input(flags=up_flag),
                ]
            )
            time.sleep(settle_ms / 1000.0)

    def _normalize_absolute_point(self, *, x: int, y: int) -> Tuple[int, int]:
        left = int(self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = max(1, int(self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
        height = max(1, int(self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
        normalized_x = int(round(((x - left) * 65535) / max(1, width - 1)))
        normalized_y = int(round(((y - top) * 65535) / max(1, height - 1)))
        normalized_x = max(0, min(65535, normalized_x))
        normalized_y = max(0, min(65535, normalized_y))
        return normalized_x, normalized_y

    def _mouse_input(self, *, dx: int = 0, dy: int = 0, flags: int, mouse_data: int = 0) -> INPUT:
        return INPUT(
            type=INPUT_MOUSE,
            union=_INPUTUNION(
                mi=MOUSEINPUT(
                    dx=int(dx),
                    dy=int(dy),
                    mouseData=int(mouse_data),
                    dwFlags=int(flags),
                    time=0,
                    dwExtraInfo=ULONG_PTR(0),
                )
            ),
        )

    def _keyboard_input(self, key_vk: int, *, key_up: bool) -> INPUT:
        KEYEVENTF_KEYUP = 0x0002
        return INPUT(
            type=1,
            union=_INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=int(key_vk),
                    wScan=0,
                    dwFlags=KEYEVENTF_KEYUP if key_up else 0,
                    time=0,
                    dwExtraInfo=ULONG_PTR(0),
                )
            ),
        )

    def _send_inputs(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        buffer = (INPUT * len(inputs))(*inputs)
        sent = int(self._user32.SendInput(len(inputs), buffer, ctypes.sizeof(INPUT)))
        if sent != len(inputs):
            error_code = int(self._kernel32.GetLastError())
            raise RuntimeError(
                f"SendInput 发送失败：期望 {len(inputs)} 个事件，实际发送 {sent} 个，GetLastError={error_code}。"
            )

    def _interpolate_path(self, start: Tuple[int, int], end: Tuple[int, int], *, steps: int) -> List[Tuple[int, int]]:
        if steps <= 1:
            return [start, end]
        path: List[Tuple[int, int]] = []
        for index in range(steps):
            ratio = index / float(steps - 1)
            x = int(round(start[0] + ((end[0] - start[0]) * ratio)))
            y = int(round(start[1] + ((end[1] - start[1]) * ratio)))
            point = (x, y)
            if not path or path[-1] != point:
                path.append(point)
        if path[-1] != end:
            path.append(end)
        return path

    def _snapshot_clipboard_payload(self) -> Dict[str, Any]:
        if win32clipboard is None:
            return {"text": None, "file_paths": [], "mode": "empty", "has_payload": False}
        try:
            win32clipboard.OpenClipboard()
            text = None
            file_paths: List[str] = []
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            if win32clipboard.IsClipboardFormatAvailable(CF_HDROP):
                file_paths = normalize_file_paths(win32clipboard.GetClipboardData(CF_HDROP))
            return normalize_clipboard_payload(text=text, file_paths=file_paths)
        except Exception:
            return {"text": None, "file_paths": [], "mode": "empty", "has_payload": False}
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _set_clipboard_payload(
        self,
        *,
        text: str | None = None,
        file_paths: Iterable[str] | None = None,
    ) -> None:
        if win32clipboard is None:
            raise RuntimeError("win32clipboard 不可用，无法通过剪贴板执行 SendInput 输入。")
        normalized_paths = normalize_file_paths(file_paths or [])
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            if text is not None:
                win32clipboard.SetClipboardText(str(text or ""), win32clipboard.CF_UNICODETEXT)
            if normalized_paths:
                payload = self._build_hdrop_payload(normalized_paths)
                win32clipboard.SetClipboardData(CF_HDROP, payload)
        finally:
            win32clipboard.CloseClipboard()

    def _send_hotkey_stroke(
        self,
        stroke: ParsedHotkeyStroke,
        *,
        settle_ms: int,
    ) -> None:
        inputs: list[INPUT] = []
        if stroke.event_type == "down":
            inputs.append(self._keyboard_input(stroke.key_vk, key_up=False))
        elif stroke.event_type == "up":
            inputs.append(self._keyboard_input(stroke.key_vk, key_up=True))
        else:
            inputs.append(self._keyboard_input(stroke.key_vk, key_up=False))
            inputs.append(self._keyboard_input(stroke.key_vk, key_up=True))
        self._send_inputs(inputs)
        time.sleep(max(10, int(settle_ms)) / 1000.0)

    def _normalize_modifier_state(self) -> _ModifierNormalizationState:
        currently_pressed = frozenset(modifier for modifier in MANAGED_MODIFIER_VKS if self._modifier_is_pressed(modifier))
        return _ModifierNormalizationState(
            initial_pressed=currently_pressed,
            current_pressed=currently_pressed,
            released_conflicts=(),
        )

    def _sync_modifier_state(
        self,
        *,
        state: _ModifierNormalizationState,
        target_pressed: frozenset[int],
    ) -> _ModifierNormalizationState:
        current_pressed = set(state.current_pressed)
        released_conflicts = list(state.released_conflicts)
        for modifier in list(current_pressed):
            if modifier in target_pressed:
                continue
            self._release_modifier(modifier)
            current_pressed.discard(modifier)
            if modifier in state.initial_pressed and modifier not in released_conflicts:
                released_conflicts.append(int(modifier))
        for modifier in target_pressed:
            if modifier in current_pressed:
                continue
            self._press_modifier(modifier)
            current_pressed.add(int(modifier))
        return _ModifierNormalizationState(
            initial_pressed=state.initial_pressed,
            current_pressed=frozenset(current_pressed),
            released_conflicts=tuple(released_conflicts),
        )

    def _restore_modifier_state(self, state: _ModifierNormalizationState) -> None:
        current_pressed = set(state.current_pressed)
        for modifier in list(current_pressed):
            if modifier in state.initial_pressed:
                continue
            self._release_modifier(modifier)
            current_pressed.discard(modifier)
        for modifier in state.initial_pressed:
            if modifier in current_pressed:
                continue
            self._press_modifier(modifier)

    def _modifier_is_pressed(self, modifier_vk: int) -> bool:
        try:
            return bool(int(self._user32.GetAsyncKeyState(int(modifier_vk))) & 0x8000)
        except Exception:
            return False

    def _release_modifier(self, modifier_vk: int) -> None:
        self._send_inputs([self._keyboard_input(modifier_vk, key_up=True)])

    def _press_modifier(self, modifier_vk: int) -> None:
        self._send_inputs([self._keyboard_input(modifier_vk, key_up=False)])

    def _restore_clipboard_payload(self, payload: Dict[str, Any] | None) -> None:
        if win32clipboard is None or not isinstance(payload, dict) or not payload.get("has_payload"):
            return
        try:
            self._set_clipboard_payload(
                text=payload.get("text"),
                file_paths=payload.get("file_paths"),
            )
        except Exception:
            return

    def _snapshot_clipboard_text(self) -> tuple[str | None, bool]:
        payload = self._snapshot_clipboard_payload()
        return payload.get("text"), bool(payload.get("text") is not None)

    def _set_clipboard_text(self, text: str) -> None:
        self._set_clipboard_payload(text=text)

    def _restore_clipboard_text(self, text: str | None, has_previous_text: bool) -> None:
        if not has_previous_text:
            return
        self._restore_clipboard_payload({"text": text, "file_paths": [], "has_payload": True})

    def _build_hdrop_payload(self, file_paths: List[str]) -> bytes:
        encoded_paths = "\0".join(file_paths) + "\0\0"
        encoded = encoded_paths.encode("utf-16le")
        dropfiles = DROPFILES()
        dropfiles.pFiles = ctypes.sizeof(DROPFILES)
        dropfiles.pt_x = 0
        dropfiles.pt_y = 0
        dropfiles.fNC = False
        dropfiles.fWide = True
        return bytes(ctypes.string_at(ctypes.addressof(dropfiles), ctypes.sizeof(DROPFILES))) + encoded
