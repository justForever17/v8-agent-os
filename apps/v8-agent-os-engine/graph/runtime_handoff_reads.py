"""Read-only preparation for an ordered delegation, without completing its route."""

from typing import Any


def research_handoff_read_targets(state: dict[str, Any]) -> dict[str, set[str]]:
    context = state.get("current_route_context") or {}
    targets: dict[str, set[str]] = {}
    for handoff in context.get("effectiveHandoffRefs") or context.get("handoffRefs") or []:
        if not isinstance(handoff, dict) or handoff.get("kind") != "research":
            continue
        if handoff.get("status") not in {"ready", "completed", "degraded"}:
            continue
        raw_ref = str(handoff.get("rawRef") or "")
        if raw_ref.startswith("toolobs://"):
            targets.setdefault("tool_observation_detail", set()).add(raw_ref)
        for ref in handoff.get("researchRefs") or []:
            if isinstance(ref, str) and ref.startswith("research://bundle/"):
                targets.setdefault("research_broker", set()).add(ref.removeprefix("research://bundle/"))
    return targets


def is_research_handoff_read(response: Any, targets: dict[str, set[str]]) -> bool:
    calls = getattr(response, "tool_calls", None) or []
    if not calls:
        return False
    for call in calls:
        name, args = call.get("name"), call.get("args")
        if not isinstance(args, dict):
            return False
        if name == "tool_observation_detail":
            if set(args) - {"raw_ref", "start_char", "max_chars"}:
                return False
            target = args.get("raw_ref")
        elif name == "research_broker":
            if args.get("mode") != "get_evidence" or set(args) - {"mode", "evidenceBundleId"}:
                return False
            target = args.get("evidenceBundleId")
        else:
            return False
        if not isinstance(target, str) or target not in targets.get(name, set()):
            return False
    return True
