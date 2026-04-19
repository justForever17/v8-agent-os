from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 1_000_000_000_000:
            timestamp /= 1000.0
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            numeric = float(text)
            if abs(numeric) > 1_000_000_000_000:
                numeric /= 1000.0
            dt = datetime.fromtimestamp(numeric, tz=timezone.utc)
        else:
            normalized = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_utc_iso(value: Any) -> Optional[str]:
    dt = _coerce_datetime(value)
    if dt is None:
        return None
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def latest_utc_iso(*values: Any) -> Optional[str]:
    latest: Optional[datetime] = None
    for value in values:
        current = _coerce_datetime(value)
        if current is None:
            continue
        if latest is None or current > latest:
            latest = current
    if latest is None:
        return None
    return latest.isoformat(timespec="milliseconds").replace("+00:00", "Z")
