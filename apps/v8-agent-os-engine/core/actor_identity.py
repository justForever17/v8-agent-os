from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPERVISOR_ACTOR = "supervisor"
DIRECT_SUBAGENT_ACTOR = "direct_subagent"
GRANDCHILD_ACTOR = "grandchild"
RUNTIME_INTERNAL_ACTOR = "runtime_internal"


@dataclass(frozen=True, slots=True)
class CollaborationActorIdentity:
    role: str
    delegation_depth: int
    runtime_kind: str
    agent_id: str
    delegation_id: str

    @property
    def is_supervisor(self) -> bool:
        return self.role == SUPERVISOR_ACTOR

    @property
    def is_direct_subagent(self) -> bool:
        return self.role == DIRECT_SUBAGENT_ACTOR

    @property
    def is_grandchild(self) -> bool:
        return self.role == GRANDCHILD_ACTOR

    @property
    def is_collaboration_actor(self) -> bool:
        return self.role in {SUPERVISOR_ACTOR, DIRECT_SUBAGENT_ACTOR, GRANDCHILD_ACTOR}


def _normalized_depth(*values: Any, default: int = 0) -> int:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return max(0, int(default))


def _merged_context(
    runtime_context: dict[str, Any] | None,
    route_context: dict[str, Any] | None,
) -> dict[str, Any]:
    route = dict(route_context or {})
    nested = route.get("current_route_context")
    if isinstance(nested, dict):
        route = {**dict(nested), **route}
    return {**route, **dict(runtime_context or {})}


def resolve_collaboration_actor(
    *,
    actor: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    route_context: dict[str, Any] | None = None,
) -> CollaborationActorIdentity:
    """Resolve only the user-facing collaboration plane.

    Computer Use visual actors, Memory/Safety workers, RPA recognizers and
    other runtime-internal model roles are deliberately outside this policy.
    They resolve to ``runtime_internal`` unless a graph entry point explicitly
    identifies them as Supervisor/direct child/grandchild.
    """

    context = _merged_context(runtime_context, route_context)
    # Runtime callers bind ``actor_role`` as the authoritative identity when a
    # tool is invoked from an episode runner. Previously only the optional
    # function argument was considered here, so a valid top-level runtime
    # delegation with ``actor_role=supervisor`` was downgraded to
    # ``runtime_internal`` merely because its ``runtime_kind`` was
    # ``delegation``. The broker then returned ``delegation_depth_terminal``
    # before it ever inspected the task contract.
    explicit = str(
        actor
        or context.get("actor_role")
        or context.get("actorRole")
        or ""
    ).strip().lower().replace("-", "_")
    runtime_kind = str(context.get("runtime_kind") or context.get("runtimeKind") or "").strip().lower()
    agent_id = str(
        context.get("agent_id")
        or context.get("agentId")
        or context.get("subagent_id")
        or context.get("subagentId")
        or ""
    ).strip()
    delegation_id = str(context.get("delegation_id") or context.get("delegationId") or "").strip()
    depth = _normalized_depth(
        context.get("delegation_depth"),
        context.get("delegationDepth"),
        default=1 if explicit in {"subagent", DIRECT_SUBAGENT_ACTOR} else 0,
    )

    if explicit in {SUPERVISOR_ACTOR, "chat"}:
        role = SUPERVISOR_ACTOR
        depth = 0
    elif explicit in {GRANDCHILD_ACTOR, "descendant", "descendant_agent", "sun_agent"}:
        role = GRANDCHILD_ACTOR
        depth = max(2, depth)
    elif explicit in {DIRECT_SUBAGENT_ACTOR, "child", "child_agent"}:
        role = DIRECT_SUBAGENT_ACTOR
        depth = 1
    elif explicit == "subagent":
        role = GRANDCHILD_ACTOR if depth >= 2 else DIRECT_SUBAGENT_ACTOR
        depth = max(1, depth)
    elif explicit in {RUNTIME_INTERNAL_ACTOR, "internal", "service", "guardian", "control"}:
        role = RUNTIME_INTERNAL_ACTOR
    elif runtime_kind in {"chat", "supervisor"} and agent_id.lower() in {"", "supervisor"}:
        role = SUPERVISOR_ACTOR
        depth = 0
    elif runtime_kind in {"subagent", "delegation"} and agent_id.lower() not in {"", "supervisor"}:
        role = GRANDCHILD_ACTOR if depth >= 2 else DIRECT_SUBAGENT_ACTOR
        depth = max(1, depth)
    else:
        role = RUNTIME_INTERNAL_ACTOR

    return CollaborationActorIdentity(
        role=role,
        delegation_depth=depth,
        runtime_kind=runtime_kind,
        agent_id=agent_id,
        delegation_id=delegation_id,
    )


__all__ = [
    "CollaborationActorIdentity",
    "DIRECT_SUBAGENT_ACTOR",
    "GRANDCHILD_ACTOR",
    "RUNTIME_INTERNAL_ACTOR",
    "SUPERVISOR_ACTOR",
    "resolve_collaboration_actor",
]
