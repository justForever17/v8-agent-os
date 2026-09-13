"""Append-only command output with independent, bounded UTF-8 cursor reads."""
from __future__ import annotations

import codecs
import os
import threading
import uuid
from pathlib import Path

from core.v8_agent_os_paths import runtime_private_root


class CommandOutputLog:
    def __init__(self, directory: Path | None = None):
        root = directory if directory is not None else runtime_private_root("command") / "output"
        root.mkdir(parents=True, exist_ok=True)
        self.generation = uuid.uuid4().hex
        self.path = root / f"{self.generation}.utf8.log"
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        self.size = 0
        self.lock = threading.Lock()

    def append(self, text: str) -> None:
        data = text.encode("utf-8")
        with self.lock:
            with self.path.open("ab", buffering=0) as writer:
                offset = 0
                while offset < len(data):
                    written = writer.write(data[offset:])
                    if not written: raise OSError("command_output_write_failed")
                    offset += written
            self.size += len(data)

    def read(self, cursor: int = 0, limit: int = 65536) -> dict:
        with self.lock:
            if cursor < 0 or cursor > self.size:
                raise ValueError("output_cursor_out_of_range")
            with self.path.open("rb") as reader:
                reader.seek(cursor)
                raw = reader.read(max(4, min(int(limit), 65536)))
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            text = decoder.decode(raw, final=False)
            consumed = len(raw) - len(decoder.getstate()[0])
            return {"data": text, "cursor": cursor + consumed, "startCursor": cursor, "totalBytes": self.size, "generation": self.generation, "hasMore": cursor + consumed < self.size}

    def read_remaining(self, cursor: int) -> tuple[str, int]:
        # Existing Agent consumers explicitly request their pending output. UI
        # transports use read(), never this full observation path.
        with self.lock:
            with self.path.open("rb") as reader:
                reader.seek(cursor)
                raw = reader.read()
            return raw.decode("utf-8"), cursor + len(raw)
