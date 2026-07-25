from __future__ import annotations

from typing import Any


_EXECUTION_RUNTIMES = {
    "engineering",
    "research",
    "creative_media",
    "computer_use",
    "rpa",
    "delegation",
}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _unique_text(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _as_list(values):
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _neutral_boundary() -> dict[str, Any]:
    """Return a compatibility-shaped boundary without choosing a route.

    Natural-language interpretation belongs to the Supervisor.  This adapter
    exists only because Spec and same-session governed flows still carry a
    ``boundaryDecision`` field in their state contract; it must not recreate
    the deleted lexical task classifier under another name.
    """

    return {
        "schema": "v8.task_boundary.v1",
        "primaryRuntime": "",
        "supportingRuntimes": [],
        "executionMode": "supervisor_decides",
        "reason": "no_code_route_preselection",
        "askUserNeeded": False,
        "forbiddenRoutes": [],
        "routeCorrections": [],
        "signals": [],
        "source": "supervisor_first",
        "policy": "advisory_context_only_no_lexical_routing",
    }


def _normalize_explicit_boundary(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return _neutral_boundary()

    primary_runtime = str(value.get("primaryRuntime") or "").strip()
    if primary_runtime not in _EXECUTION_RUNTIMES:
        return _neutral_boundary()

    normalized = _neutral_boundary()
    normalized.update(
        {
            "primaryRuntime": primary_runtime,
            "supportingRuntimes": [
                item
                for item in _unique_text(value.get("supportingRuntimes"))
                if item in _EXECUTION_RUNTIMES and item != primary_runtime
            ],
            "executionMode": str(value.get("executionMode") or "governed_runtime").strip(),
            "reason": str(value.get("reason") or "authoritative_governed_state").strip(),
            "askUserNeeded": bool(value.get("askUserNeeded")),
            "forbiddenRoutes": _unique_text(value.get("forbiddenRoutes")),
            "routeCorrections": [
                dict(item)
                for item in _as_list(value.get("routeCorrections"))
                if isinstance(item, dict)
            ],
            "signals": _unique_text(value.get("signals")),
            "source": str(value.get("source") or "authoritative_governed_state").strip(),
            "policy": "explicit_governed_state_no_lexical_routing",
        }
    )
    return normalized


def resolve_task_boundary(
    user_query: str,
    *,
    task_shape_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve an explicit governed boundary without parsing user prose.

    ``user_query`` remains in the signature for compatibility, but is
    deliberately ignored.  Ordinary requests reach the intelligent
    Supervisor without a code-authored route verdict.
    """

    del user_query
    hint = dict(task_shape_hint or {})
    return _normalize_explicit_boundary(
        hint.get("boundaryDecision") if isinstance(hint.get("boundaryDecision"), dict) else None
    )


def attach_task_boundary_decision(
    task_shape_hint: dict[str, Any] | None,
    *,
    user_query: str,
) -> dict[str, Any]:
    hint = dict(task_shape_hint or {})
    hint["boundaryDecision"] = resolve_task_boundary(
        user_query,
        task_shape_hint=hint,
    )
    return hint


def build_supervisor_task_context(user_query: str) -> dict[str, Any]:
    """Build neutral live-run context without classifying the request."""

    del user_query
    context = {
        "schema": "v8.supervisor_task_context.v1",
        "primaryTaskShape": "unknown",
        "secondaryTaskShapes": [],
        "confidence": 0.0,
        "reason": "supervisor_first",
        "suggestedFamilies": [],
        "optionalRuntimeGrants": [],
        "familyScores": {},
        "topFamily": "",
        "scoreMargin": 0.0,
        "ambiguityFlags": [],
        "signals": [],
        "writingRoute": {},
        "source": "supervisor_first",
        "policy": "supervisor_decides_runtime_no_lexical_routing",
        "boundaryDecision": _neutral_boundary(),
    }
    return context


def render_task_boundary_hint(boundary: dict[str, Any] | None) -> str:
    normalized = _normalize_explicit_boundary(boundary)
    primary_runtime = str(normalized.get("primaryRuntime") or "").strip()
    if primary_runtime not in _EXECUTION_RUNTIMES:
        return ""

    support = ", ".join(normalized.get("supportingRuntimes") or []) or "none"
    forbidden = ", ".join(normalized.get("forbiddenRoutes") or []) or "none"
    signals = ", ".join(normalized.get("signals") or []) or "none"
    lines = [
        "<task_boundary>",
        "This boundary comes from explicit governed state, not natural-language classification.",
        f"primaryRuntime={primary_runtime}; supportingRuntimes={support}; executionMode={normalized.get('executionMode') or 'governed_runtime'}",
        f"askUserNeeded={bool(normalized.get('askUserNeeded'))}; reason={normalized.get('reason') or 'authoritative_governed_state'}",
        f"forbiddenRoutes={forbidden}; signals={signals}; source={normalized.get('source') or 'authoritative_governed_state'}",
        "policy=respect explicit governance; this context does not grant tools or permissions.",
        "</task_boundary>",
    ]
    return "\n".join(lines) + "\n"
