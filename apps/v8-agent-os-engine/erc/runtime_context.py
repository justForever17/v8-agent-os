from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator


_RUNTIME_CONTEXT: ContextVar[Dict[str, Any]] = ContextVar("v8_agent_os_runtime_context", default={})


def get_runtime_context() -> Dict[str, Any]:
    return dict(_RUNTIME_CONTEXT.get() or {})


def build_runtime_callback_config(**overrides: Any) -> Dict[str, Any]:
    """Project durable runtime ownership into LangChain callback events.

    ContextVars are reliable while a worker is executing, but graph callback
    events may be consumed later from a different task/thread.  The callback
    metadata travels with those events and is therefore the authoritative
    ownership handoff for the chat stream projector.
    """

    context = get_runtime_context()
    context.update({key: value for key, value in overrides.items() if value is not None})
    runtime_kind = str(context.get("runtime_kind") or context.get("runtimeKind") or "").strip()
    agent_id = str(
        context.get("agent_id")
        or context.get("agentId")
        or context.get("subagent_id")
        or context.get("subagentId")
        or ""
    ).strip()
    subagent_id = str(context.get("subagent_id") or context.get("subagentId") or "").strip()
    delegation_id = str(context.get("delegation_id") or context.get("delegationId") or "").strip()
    metadata = {
        key: value
        for key, value in {
            "v8_owner_runtime_kind": runtime_kind,
            "v8_owner_agent_id": agent_id,
            "v8_owner_subagent_id": subagent_id,
            "v8_owner_delegation_id": delegation_id,
            "v8_owner_trigger_source": str(
                context.get("trigger_source") or context.get("triggerSource") or ""
            ).strip(),
        }.items()
        if value
    }
    tags = ["v8:runtime-owner"]
    if runtime_kind:
        tags.append(f"v8:runtime:{runtime_kind}")
    if agent_id:
        tags.append(f"v8:agent:{agent_id}")
    return {"metadata": metadata, "tags": tags}


@contextmanager
def bind_runtime_context(**context: Any) -> Iterator[Dict[str, Any]]:
    previous = get_runtime_context()
    current = dict(previous)
    current.update({key: value for key, value in context.items() if value is not None})
    token = _RUNTIME_CONTEXT.set(current)
    try:
        yield current
    finally:
        try:
            _RUNTIME_CONTEXT.reset(token)
        except ValueError:
            _RUNTIME_CONTEXT.set(previous)
