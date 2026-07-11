from __future__ import annotations

from typing import Any


# Runtime episodes are executable only when the Engine has an explicit local
# executor for the kind. Unknown/removed kinds remain durable evidence, but are
# never claimed by the background runner.
EXECUTABLE_RUNTIME_EPISODE_KINDS = frozenset(
    {
        "research",
        "engineering",
        "creative_media",
        "computer_use",
        "rpa",
        "delegation",
    }
)


def runtime_episode_kind_supported(value: Any) -> bool:
    return str(value or "").strip() in EXECUTABLE_RUNTIME_EPISODE_KINDS


def project_runtime_episode_compatibility(record: dict[str, Any]) -> dict[str, Any]:
    projected = dict(record or {})
    kind = str(projected.get("kind") or projected.get("runtimeKind") or "unknown").strip() or "unknown"
    if runtime_episode_kind_supported(kind):
        projected.setdefault("executionSupported", True)
        return projected

    persisted_state = str(projected.get("state") or "unknown").strip() or "unknown"
    projected["persistedState"] = persisted_state
    projected["displayState"] = "archived"
    projected["projectedState"] = "archived"
    projected["archived"] = True
    projected["executionSupported"] = False
    projected["compatibilityStatus"] = "unsupported_archived_runtime"
    projected["compatibilityReason"] = (
        f"Runtime kind '{kind}' is retained as historical evidence but is not supported by this Engine version."
    )
    return projected
