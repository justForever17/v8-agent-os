from __future__ import annotations

import json
import sys
from typing import Any


def ensure_utf8_stdout() -> None:
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def emit_json(payload: Any, *, indent: int = 2) -> None:
    ensure_utf8_stdout()
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8", errors="replace"))
        buffer.write(b"\n")
        buffer.flush()
        return
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
