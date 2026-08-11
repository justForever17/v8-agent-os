from __future__ import annotations

import os
from urllib.parse import urlsplit


DEFAULT_WEB_PORT = 9527
WEB_FALLBACK_PORT_START = 19527
WEB_FALLBACK_PORT_END = 19546


def governed_web_port(value: str | None = None) -> int:
    candidate = str(value if value is not None else os.getenv("V8_WEB_BASE_URL", "")).strip()
    if not candidate:
        return DEFAULT_WEB_PORT
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return DEFAULT_WEB_PORT
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or port is None
    ):
        return DEFAULT_WEB_PORT
    if port == DEFAULT_WEB_PORT or WEB_FALLBACK_PORT_START <= port <= WEB_FALLBACK_PORT_END:
        return port
    return DEFAULT_WEB_PORT


def governed_web_origins(value: str | None = None) -> list[str]:
    port = governed_web_port(value)
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
