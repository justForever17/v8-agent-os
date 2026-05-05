from __future__ import annotations

from typing import List


class TextChunkAggregator:
    """Buffers tiny text deltas into more stable chunks for the web client."""

    def __init__(self, soft_limit: int = 24, hard_limit: int = 72):
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self._buffer = ""

    def push(self, delta: str) -> List[str]:
        if not delta:
            return []

        self._buffer += delta
        flushed: List[str] = []

        if self.should_flush_now():
            flushed.append(self._flush())
            return flushed

        return flushed

    def flush(self) -> str:
        return self._flush()

    def flush_if_ready(self) -> str:
        if not self.should_flush_now():
            return ""
        return self._flush()

    def has_buffered_content(self) -> bool:
        return bool(self._buffer)

    def buffered_length(self) -> int:
        return len(self._buffer)

    def should_flush_now(self) -> bool:
        if not self._buffer:
            return False
        if len(self._buffer) >= self.hard_limit:
            return True
        if self._ends_with_strong_boundary(self._buffer):
            return True
        return len(self._buffer) >= self.soft_limit and self._ends_with_boundary(self._buffer)

    @staticmethod
    def _ends_with_boundary(text: str) -> bool:
        return text.endswith(("\n", "\r", "。", "！", "？", ".", "!", "?", "；", ";", "：", ":", "，", ",", "、", " "))

    @staticmethod
    def _ends_with_strong_boundary(text: str) -> bool:
        return text.endswith(("\n", "\r", "。", "！", "？", ".", "!", "?"))

    def _flush(self) -> str:
        if not self._buffer:
            return ""
        chunk = self._buffer
        self._buffer = ""
        return chunk
