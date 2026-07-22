from __future__ import annotations

import ctypes
from dataclasses import dataclass


VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_INSERT = 0x2D
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_SELECT = 0x29
VK_EXECUTE = 0x2B
VK_SNAPSHOT = 0x2C
VK_HELP = 0x2F
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91
VK_BROWSER_BACK = 0xA6
VK_BROWSER_FORWARD = 0xA7
VK_BROWSER_REFRESH = 0xA8
VK_BROWSER_STOP = 0xA9
VK_BROWSER_SEARCH = 0xAA
VK_BROWSER_FAVORITES = 0xAB
VK_BROWSER_HOME = 0xAC
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_LAUNCH_MAIL = 0xB4
VK_LAUNCH_MEDIA_SELECT = 0xB5
VK_LAUNCH_APP1 = 0xB6
VK_LAUNCH_APP2 = 0xB7
VK_OEM_PLUS = 0xBB
VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE


MODIFIER_TOKEN_MAP = {
    "^": VK_CONTROL,
    "+": VK_SHIFT,
    "%": VK_MENU,
    "#": VK_LWIN,
}

MANAGED_MODIFIER_VKS = (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)

NAMED_KEY_VKS = {
    "ENTER": VK_RETURN,
    "RETURN": VK_RETURN,
    "TAB": VK_TAB,
    "ESC": VK_ESCAPE,
    "ESCAPE": VK_ESCAPE,
    "SHIFT": VK_SHIFT,
    "CTRL": VK_CONTROL,
    "CONTROL": VK_CONTROL,
    "ALT": VK_MENU,
    "MENU": VK_MENU,
    "SPACE": VK_SPACE,
    "BACKSPACE": VK_BACK,
    "BKSP": VK_BACK,
    "BS": VK_BACK,
    "DELETE": VK_DELETE,
    "DEL": VK_DELETE,
    "INSERT": VK_INSERT,
    "INS": VK_INSERT,
    "HOME": VK_HOME,
    "END": VK_END,
    "PGUP": VK_PRIOR,
    "PRIOR": VK_PRIOR,
    "PAGEUP": VK_PRIOR,
    "PGDN": VK_NEXT,
    "NEXT": VK_NEXT,
    "PAGEDOWN": VK_NEXT,
    "LEFT": VK_LEFT,
    "RIGHT": VK_RIGHT,
    "UP": VK_UP,
    "DOWN": VK_DOWN,
    "PAUSE": VK_PAUSE,
    "BREAK": VK_PAUSE,
    "CAPSLOCK": VK_CAPITAL,
    "SELECT": VK_SELECT,
    "EXECUTE": VK_EXECUTE,
    "PRINT": VK_SNAPSHOT,
    "PRINTSCREEN": VK_SNAPSHOT,
    "SNAPSHOT": VK_SNAPSHOT,
    "HELP": VK_HELP,
    "LWIN": VK_LWIN,
    "RWIN": VK_RWIN,
    "WIN": VK_LWIN,
    "LSHIFT": VK_SHIFT,
    "RSHIFT": VK_SHIFT,
    "LCONTROL": VK_CONTROL,
    "LCTRL": VK_CONTROL,
    "RCONTROL": VK_CONTROL,
    "RCTRL": VK_CONTROL,
    "LMENU": VK_MENU,
    "LALT": VK_MENU,
    "RMENU": VK_MENU,
    "RALT": VK_MENU,
    "APPS": VK_APPS,
    "NUMLOCK": VK_NUMLOCK,
    "SCROLLLOCK": VK_SCROLL,
    "BROWSER_BACK": VK_BROWSER_BACK,
    "BROWSER_FORWARD": VK_BROWSER_FORWARD,
    "BROWSER_REFRESH": VK_BROWSER_REFRESH,
    "BROWSER_STOP": VK_BROWSER_STOP,
    "BROWSER_SEARCH": VK_BROWSER_SEARCH,
    "BROWSER_FAVORITES": VK_BROWSER_FAVORITES,
    "BROWSER_HOME": VK_BROWSER_HOME,
    "VOLUME_MUTE": VK_VOLUME_MUTE,
    "VOLUME_DOWN": VK_VOLUME_DOWN,
    "VOLUME_UP": VK_VOLUME_UP,
    "MEDIA_NEXT_TRACK": VK_MEDIA_NEXT_TRACK,
    "MEDIA_PREV_TRACK": VK_MEDIA_PREV_TRACK,
    "MEDIA_STOP": VK_MEDIA_STOP,
    "MEDIA_PLAY_PAUSE": VK_MEDIA_PLAY_PAUSE,
    "LAUNCH_MAIL": VK_LAUNCH_MAIL,
    "LAUNCH_MEDIA_SELECT": VK_LAUNCH_MEDIA_SELECT,
    "LAUNCH_APP1": VK_LAUNCH_APP1,
    "LAUNCH_APP2": VK_LAUNCH_APP2,
    "ADD": VK_OEM_PLUS,
    "PLUS": VK_OEM_PLUS,
    "SUBTRACT": VK_OEM_MINUS,
    "MINUS": VK_OEM_MINUS,
    "DIVIDE": 0x6F,
    "MULTIPLY": 0x6A,
    "DECIMAL": 0x6E,
    "SEPARATOR": 0x6C,
    "COMMA": VK_OEM_COMMA,
    "PERIOD": VK_OEM_PERIOD,
    "DOT": VK_OEM_PERIOD,
    "NUMPAD0": 0x60,
    "NUMPAD1": 0x61,
    "NUMPAD2": 0x62,
    "NUMPAD3": 0x63,
    "NUMPAD4": 0x64,
    "NUMPAD5": 0x65,
    "NUMPAD6": 0x66,
    "NUMPAD7": 0x67,
    "NUMPAD8": 0x68,
    "NUMPAD9": 0x69,
}
for _index in range(1, 25):
    NAMED_KEY_VKS[f"F{_index}"] = 0x6F + _index


WINDOW_MESSAGE_DISALLOWED_KEYS = {
    VK_LWIN,
    VK_RWIN,
    VK_APPS,
    VK_BROWSER_BACK,
    VK_BROWSER_FORWARD,
    VK_BROWSER_REFRESH,
    VK_BROWSER_STOP,
    VK_BROWSER_SEARCH,
    VK_BROWSER_FAVORITES,
    VK_BROWSER_HOME,
    VK_VOLUME_MUTE,
    VK_VOLUME_DOWN,
    VK_VOLUME_UP,
    VK_MEDIA_NEXT_TRACK,
    VK_MEDIA_PREV_TRACK,
    VK_MEDIA_STOP,
    VK_MEDIA_PLAY_PAUSE,
    VK_LAUNCH_MAIL,
    VK_LAUNCH_MEDIA_SELECT,
    VK_LAUNCH_APP1,
    VK_LAUNCH_APP2,
}


@dataclass(frozen=True, slots=True)
class ParsedHotkeyStroke:
    key_vk: int
    modifiers: tuple[int, ...]
    token: str
    repeat: int = 1
    event_type: str = "press"


@dataclass(frozen=True, slots=True)
class HotkeySupportPlan:
    strokes: tuple[ParsedHotkeyStroke, ...]
    supports_sendinput: bool
    supports_window_message: bool
    requires_foreground: bool
    reasons: tuple[str, ...] = ()


class HotkeyParseError(ValueError):
    pass


VALID_EVENT_TYPES = {"press", "down", "up"}

_HUMAN_MODIFIER_TOKENS = {
    "CTRL": "^",
    "CONTROL": "^",
    "ALT": "%",
    "OPTION": "%",
    "SHIFT": "+",
    "WIN": "#",
    "WINDOWS": "#",
    "META": "#",
}


def normalize_hotkey_sequence(sequence: str) -> str:
    """Normalize human chord notation without changing the legacy send_keys grammar.

    Model-facing tools naturally produce ``CTRL+L`` and ``ALT+F4`` while the
    desktop drivers historically consumed pywinauto-style ``^l`` and
    ``%{F4}``. Passing the human form directly to ``send_keys`` types the
    letters into the focused control, which is especially visible when an IME
    is active. Only unambiguous modifier chords are rewritten; existing
    send_keys expressions remain untouched.
    """

    raw = str(sequence or "").strip()
    if not raw or raw[0] in MODIFIER_TOKEN_MAP or raw.startswith("{") or raw == "~":
        return raw
    parts = [part.strip() for part in raw.split("+")]
    if len(parts) < 2 or not all(parts):
        return raw
    modifiers = parts[:-1]
    if not all(part.upper() in _HUMAN_MODIFIER_TOKENS for part in modifiers):
        return raw
    prefix = "".join(_HUMAN_MODIFIER_TOKENS[part.upper()] for part in modifiers)
    key = parts[-1]
    upper_key = key.upper()
    if upper_key in NAMED_KEY_VKS or len(key) != 1:
        key_token = "{" + upper_key + "}"
    else:
        # Modifier state is explicit in the prefix; avoid an accidental Shift
        # generated merely because a model wrote the letter in uppercase.
        key_token = key.lower() if key.isalpha() else key
    return prefix + key_token


def parse_hotkey_sequence(sequence: str) -> list[ParsedHotkeyStroke]:
    parser = _HotkeyParser(normalize_hotkey_sequence(sequence))
    strokes = parser.parse()
    if not strokes:
        raise HotkeyParseError("快捷键序列为空。")
    return strokes


def analyze_hotkey_support(sequence: str) -> HotkeySupportPlan:
    strokes = tuple(parse_hotkey_sequence(sequence))
    supports_sendinput = True
    supports_window_message = True
    reasons: list[str] = []
    for stroke in strokes:
        if VK_LWIN in stroke.modifiers or VK_RWIN in stroke.modifiers:
            supports_window_message = False
            reasons.append("Win 键修饰组合不允许走 window_message 回退。")
        if stroke.key_vk in WINDOW_MESSAGE_DISALLOWED_KEYS:
            supports_window_message = False
            reasons.append(f"`{stroke.token}` 只允许前台注入，不允许 window_message 回退。")
    return HotkeySupportPlan(
        strokes=strokes,
        supports_sendinput=supports_sendinput,
        supports_window_message=supports_window_message,
        requires_foreground=True,
        reasons=tuple(_unique_strings(reasons)),
    )


class _HotkeyParser:
    def __init__(self, sequence: str) -> None:
        self.sequence = sequence
        self.length = len(sequence)
        self.position = 0

    def parse(self) -> list[ParsedHotkeyStroke]:
        strokes = self._parse_until(terminator=None, inherited_modifiers=())
        if self.position != self.length:
            raise HotkeyParseError(f"无法解析快捷键序列：{self.sequence!r}")
        return strokes

    def _parse_until(self, *, terminator: str | None, inherited_modifiers: tuple[int, ...]) -> list[ParsedHotkeyStroke]:
        strokes: list[ParsedHotkeyStroke] = []
        while self.position < self.length:
            current = self.sequence[self.position]
            if terminator is not None and current == terminator:
                self.position += 1
                return strokes
            modifiers = list(inherited_modifiers)
            while self.position < self.length and self.sequence[self.position] in MODIFIER_TOKEN_MAP:
                modifiers.append(MODIFIER_TOKEN_MAP[self.sequence[self.position]])
                self.position += 1
            if self.position >= self.length:
                raise HotkeyParseError("快捷键序列不能以修饰键结尾。")
            current = self.sequence[self.position]
            if current == "(":
                self.position += 1
                strokes.extend(
                    self._parse_until(
                        terminator=")",
                        inherited_modifiers=_merge_modifiers(tuple(modifiers)),
                    )
                )
                continue
            strokes.append(self._parse_token(modifiers=tuple(modifiers)))
        if terminator is not None:
            raise HotkeyParseError(f"快捷键分组缺少结束符 {terminator!r}。")
        return strokes

    def _parse_token(self, *, modifiers: tuple[int, ...]) -> ParsedHotkeyStroke:
        current = self.sequence[self.position]
        repeat = 1
        event_type = "press"
        if current == "{":
            token = self._read_braced_token()
            token, repeat, event_type = _split_repeat_or_event(token)
            key_vk, token_name, token_modifiers = _resolve_token(token)
        elif current == "~":
            self.position += 1
            key_vk, token_name, token_modifiers = VK_RETURN, "ENTER", ()
        else:
            self.position += 1
            key_vk, token_name, token_modifiers = _resolve_token(current)
        combined_modifiers = _merge_modifiers(modifiers + token_modifiers)
        return ParsedHotkeyStroke(
            key_vk=int(key_vk),
            modifiers=combined_modifiers,
            token=str(token_name),
            repeat=max(1, int(repeat)),
            event_type=event_type,
        )

    def _read_braced_token(self) -> str:
        end = self.sequence.find("}", self.position + 1)
        if end < 0:
            raise HotkeyParseError("快捷键序列缺少右花括号。")
        token = self.sequence[self.position + 1 : end]
        self.position = end + 1
        if token == "":
            raise HotkeyParseError("花括号快捷键不能为空。")
        return token


def _split_repeat_or_event(token: str) -> tuple[str, int, str]:
    stripped = token.strip()
    if " " not in stripped:
        return stripped, 1, "press"
    name, suffix = stripped.rsplit(" ", 1)
    lowered_suffix = suffix.strip().lower()
    if lowered_suffix in VALID_EVENT_TYPES:
        return name.strip(), 1, lowered_suffix
    if suffix.isdigit():
        return name.strip(), max(1, int(suffix)), "press"
    return stripped, 1, "press"


def _resolve_token(token: str) -> tuple[int, str, tuple[int, ...]]:
    normalized = str(token or "").strip()
    if not normalized:
        raise HotkeyParseError("空快捷键 token 不合法。")
    if normalized in {"{{}", "{}}"}:
        literal = "{" if normalized == "{{}" else "}"
        return _resolve_character(literal)
    upper = normalized.upper()
    if upper in NAMED_KEY_VKS:
        return NAMED_KEY_VKS[upper], upper, ()
    if upper.startswith("VK_"):
        alias = upper[3:]
        if alias in NAMED_KEY_VKS:
            return NAMED_KEY_VKS[alias], upper, ()
        try:
            return int(alias, 0), upper, ()
        except ValueError as exc:
            raise HotkeyParseError(f"无法解析虚拟键：{token!r}") from exc
    if len(normalized) == 1:
        return _resolve_character(normalized)
    raise HotkeyParseError(f"不支持的快捷键 token：{token!r}")


def _resolve_character(char: str) -> tuple[int, str, tuple[int, ...]]:
    try:
        user32 = ctypes.windll.user32
        scan = int(user32.VkKeyScanW(ord(char)))
    except Exception:
        scan = -1
    if scan != -1:
        vk = scan & 0xFF
        shift_state = (scan >> 8) & 0xFF
        modifiers: list[int] = []
        if shift_state & 0x01:
            modifiers.append(VK_SHIFT)
        if shift_state & 0x02:
            modifiers.append(VK_CONTROL)
        if shift_state & 0x04:
            modifiers.append(VK_MENU)
        return vk, char, tuple(modifiers)
    if char == " ":
        return VK_SPACE, "SPACE", ()
    if char.isalpha():
        modifiers = (VK_SHIFT,) if char.isupper() else ()
        return ord(char.upper()), char, modifiers
    if char.isdigit():
        return ord(char), char, ()
    raise HotkeyParseError(f"当前布局下无法解析字符按键：{char!r}")


def _merge_modifiers(modifiers: tuple[int, ...]) -> tuple[int, ...]:
    ordered: list[int] = []
    for modifier in modifiers:
        value = int(modifier)
        if value not in ordered:
            ordered.append(value)
    return tuple(ordered)


def _unique_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in ordered:
            ordered.append(item)
    return ordered
