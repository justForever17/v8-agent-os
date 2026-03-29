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

        if len(self._buffer) >= self.hard_limit:
            flushed.append(self._flush())
            return flushed

        if len(self._buffer) >= self.soft_limit and self._ends_with_boundary(self._buffer):
            flushed.append(self._flush())

        return flushed

    def flush(self) -> str:
        return self._flush()

    @staticmethod
    def _ends_with_boundary(text: str) -> bool:
        return text.endswith(("\n", "\r", "。", "！", "？", ".", "!", "?", "；", ";", "：", ":", "，", ",", "、", " "))

    def _flush(self) -> str:
        if not self._buffer:
            return ""
        chunk = self._buffer
        self._buffer = ""
        return chunk
