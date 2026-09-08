"""Read-only preparation for an ordered delegation, without completing its route."""

import re
from typing import Any


def research_handoff_read_targets(state: dict[str, Any], *, user_query: str = "") -> dict[str, set[str]]:
    context = state.get("current_route_context") or {}
    targets: dict[str, set[str]] = {}
    # Explicit saved identifiers can precede a new run's first handoff. These
    # are navigation targets only; the bound reader still owns read permission.
    for prefix, tool_key in (("research", "research_broker"), ("rxp", "research_experience")):
        refs = set(re.findall(rf"\b{prefix}_[a-zA-Z0-9]{{8,}}\b", user_query))
        if refs:
            targets[tool_key] = refs
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
            if args.get("mode") == "get_experience":
                if set(args) - {"mode", "experiencePackId"}:
                    return False
                name = "research_experience"
                target = args.get("experiencePackId")
            elif args.get("mode") == "get_evidence":
                if set(args) - {"mode", "evidenceBundleId", "readAnswer", "sourceKey", "startChar", "maxChars"}:
                    return False
                target = args.get("evidenceBundleId")
            else:
                return False
        else:
            return False
        if not isinstance(target, str) or target not in targets.get(name, set()):
            return False
    return True
