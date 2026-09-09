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
    old_run = str(merged.get("runId") or merged.get("run_id") or "")
    new_run = str((overlay or {}).get("runId") or (overlay or {}).get("run_id") or "")
    if old_run and new_run and old_run != new_run:
        # These are run-scoped execution choices, not persistent user grants.
        merged["runtimeToolGrants"] = []
        merged.pop("desktopRoute", None)
    for key, value in dict(overlay or {}).items():
        if value is None:
            continue
        merged[key] = value
    return merged
