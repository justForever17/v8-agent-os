from __future__ import annotations


class StreamFilter:
    """Suppress known empty prefixes until the first meaningful stream chunk."""

    def __init__(self, bad_words: list[str]):
        self.bad_words = bad_words
        self.buffer = ""
        self.flushed = False

    def process(self, chunk: str) -> str:
        if self.flushed:
            return chunk

        self.buffer += chunk
        if any(item == self.buffer for item in self.bad_words):
            return ""
        if any(item.startswith(self.buffer) for item in self.bad_words):
            return ""
        self.flushed = True
        return self.buffer

    def flush(self) -> str:
        if self.flushed or not self.buffer:
            return ""
        if any(item == self.buffer for item in self.bad_words):
            return ""
        self.flushed = True
        return self.buffer


__all__ = ["StreamFilter"]
