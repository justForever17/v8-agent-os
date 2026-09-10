"""Streaming separation of native-tool protocol text from ordinary Markdown.

No textual invocation is executed or repaired here. Markdown code examples are
retained. A protocol leak is rejected by the adapter's existing response check.
"""
import re

_WRAPPER = re.compile(r"\]<\][A-Za-z0-9_-]{1,40}\[>\[")
_TOOL_OPEN = "<tool_call>"
_FENCE_LEADER = re.compile(r"(?: {0,3}> ?)*[ \t]{0,3}(?:(?:[-+*]|\d{1,9}[.)]) +)?")


class NativeToolTextGuard:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.pending = ""
        self.code_ticks = 0
        self.fence = None
        self.line_prefix = ""
        self.indented = False
        self.escaped = False
        self.leaked = False

    def _advance(self, text):
        for char in text:
            if char == '\n':
                if not self.line_prefix.strip() and not self.fence:
                    self.code_ticks = 0
                self.line_prefix, self.indented, self.escaped = '', False, False
            elif len(self.line_prefix) < 128:
                self.line_prefix += char

    def feed(self, text: str, *, final=False) -> str:
        if not self.enabled:
            return text
        if self.leaked:
            return ""
        text, self.pending = self.pending + text, ""
        output, i = [], 0
        while i < len(text):
            prefix = self.line_prefix
            if prefix and not prefix.strip() and len(prefix.expandtabs(4)) >= 4:
                self.indented = True
            if self.escaped:
                self.escaped = False
                output.append(text[i]); self._advance(text[i]); i += 1
                continue
            if text[i] == '\\' and not (self.code_ticks or self.fence or self.indented):
                self.escaped = True
                output.append(text[i]); self._advance(text[i]); i += 1
                continue
            if text[i] in '`~' and not self.indented:
                char = text[i]
                end = i + 1
                while end < len(text) and text[end] == char:
                    end += 1
                if end == len(text) and not final:
                    self.pending = text[i:]
                    break
                count = end - i
                at_fence = bool(_FENCE_LEADER.fullmatch(prefix))
                if self.fence:
                    if char == self.fence[0] and count >= self.fence[1] and at_fence:
                        remainder = text[end:].split('\n', 1)[0]
                        if not remainder.strip():
                            if '\n' not in text[end:] and not final:
                                self.pending = text[i:]
                                break
                            self.fence = None
                elif count >= 3 and at_fence and not self.code_ticks:
                    self.fence = (char, count)
                elif char == '`':
                    self.code_ticks = 0 if count == self.code_ticks else (self.code_ticks or count)
                output.append(text[i:end])
                self._advance(text[i:end])
                i = end
                continue
            if not (self.code_ticks or self.fence or self.indented) and text[i] in '<]':
                rest = text[i:]
                if rest.lower().startswith(_TOOL_OPEN) or _WRAPPER.match(rest):
                    self.leaked = True
                    break
                partial_tool = _TOOL_OPEN.startswith(rest.lower())
                partial_wrapper = (']<]'.startswith(rest) or
                    bool(re.fullmatch(r"\]<\][A-Za-z0-9_-]{0,40}(?:\[|\[>)?", rest)))
                if partial_tool or partial_wrapper:
                    if not final:
                        self.pending = rest
                        break
                    if rest.lower().startswith('<tool_call') or rest.startswith(']<]'):
                        self.leaked = True
                        break
            output.append(text[i])
            self._advance(text[i])
            i += 1
        return ''.join(output)

    def chunk(self, message):
        if not self.enabled:
            return message
        content = message.content
        if isinstance(content, str):
            content = self.feed(content)
        elif isinstance(content, list):
            content = [{**block, 'text': self.feed(block['text'])}
                       if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)
                       else self.feed(block) if isinstance(block, str) else block for block in content]
        return message.model_copy(update={'content': content})


def has_native_tool_text(text: str) -> bool:
    guard = NativeToolTextGuard()
    guard.feed(text, final=True)
    return guard.leaked
