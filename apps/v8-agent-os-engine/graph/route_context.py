from __future__ import annotations

from typing import Any


def merge_route_context(
    base: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge graph route context without discarding runtime-specific payloads.

    `current_route_context` currently carries both delegation metadata and
    runtime-owned route artifacts such as `desktopRoute`. Graph nodes that only
    refresh delegation context must not wipe the desktop route written by a
    previous tool call.
    """

    merged = dict(base or {})
    for key, value in dict(overlay or {}).items():
        if value is None:
            continue
        merged[key] = value
    return merged
