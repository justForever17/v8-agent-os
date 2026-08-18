"""Small, stable provider boundary shared by Creative Media adapters.

The runtime still owns provider-specific request construction for now.  This
module only centralizes the contracts that must not drift between adapters:
remote status normalization and redacted HTTP failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_CANCELLED = {"cancelled", "canceled", "stopped", "terminated", "aborted"}
_FAILED = {
    "failed",
    "fail",
    "failure",
    "error",
    "errored",
    "rejected",
}
_SUCCEEDED = {
    "succeeded",
    "success",
    "completed",
    "complete",
    "done",
    "finished",
    "finish",
}
_QUEUED = {"queued", "queueing", "pending", "created", "submitted", "ordered", "waiting"}
_RUNNING = {"running", "processing", "in_progress", "started", "preparing"}


def normalize_remote_status(value: Any, *, provider: str | None = None) -> str:
    """Return the canonical provider state without collapsing cancellation.

    ``provider`` only controls the unknown-state fallback.  A provider's
    explicit ``cancelled``/``canceled`` response is never a generic failure.
    """

    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _CANCELLED:
        return "cancelled"
    if normalized in _FAILED:
        return "failed"
    if normalized in _SUCCEEDED:
        return "succeeded"
    if normalized in _QUEUED:
        return "queued"
    if normalized in _RUNNING:
        return "running"
    return "running" if provider else "unknown"


def normalize_async_status(value: Any) -> str:
    """Normalize adapter polling responses for the durable job state."""

    return normalize_remote_status(value) if value not in (None, "") else "running"


def _safe_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if not hostname:
            return "<redacted-provider-url>"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = parsed.port
        netloc = f"{host}:{port}" if port is not None else host
        # Userinfo, query parameters, and fragments can all carry credentials.
        # A bounded path is sufficient to identify the failing provider route.
        path = str(parsed.path or "")[:256]
        return urlunsplit((parsed.scheme, netloc, path, "", ""))
    except (TypeError, ValueError):
        return "<redacted-provider-url>"


def _redact_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    text = re.sub(r"https?://[^\s]+", "<redacted-provider-url>", text, flags=re.IGNORECASE)
    text = re.sub(
        r'''(?ix)
        (["'](?:authorization|api[_-]?key|access[_-]?token|token|secret)["']\s*:\s*)
        (["'])[^"']*\2
        ''',
        r'\1"<redacted>"',
        text,
    )
    text = re.sub(
        r"(?i)(authorization|api[_-]?key|access[_-]?token|token|secret)\s*[:=]\s*['\"]?(?:bearer\s+)?[^\s,;}'\"]+['\"]?",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)\bbearer\s+[a-z0-9._~+/=-]+",
        "Bearer <redacted>",
        text,
    )
    return text[:limit]


@dataclass(eq=False)
class ProviderHttpError(RuntimeError):
    """Structured, redacted provider transport failure."""

    method: str
    url: str
    status_code: int | None = None
    detail_code: str = "provider_http_error"
    response_excerpt: str = ""

    def __post_init__(self) -> None:
        self.method = str(self.method or "GET").upper()
        self.url = _safe_url(self.url)
        self.status_code = int(self.status_code) if self.status_code is not None else None
        self.detail_code = str(self.detail_code or "provider_http_error")
        self.response_excerpt = _redact_text(self.response_excerpt)
        RuntimeError.__init__(self, self.__str__())

    def __str__(self) -> str:
        status = f" ({self.status_code})" if self.status_code is not None else ""
        suffix = f": {self.response_excerpt}" if self.response_excerpt else ""
        return f"Provider request failed{status} [{self.detail_code}] at {self.url or '<provider-url>'}{suffix}"


__all__ = ["ProviderHttpError", "normalize_async_status", "normalize_remote_status"]
