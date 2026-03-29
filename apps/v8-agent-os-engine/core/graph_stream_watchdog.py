from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable


INITIAL_STREAM_IDLE_TIMEOUT_SECONDS = 90.0
ACTIVE_MODEL_STREAM_IDLE_TIMEOUT_SECONDS = 45.0
ACTIVE_TOOL_STREAM_IDLE_TIMEOUT_SECONDS = 300.0
_IGNORED_CHAIN_START_NAMES = {"LangGraph", "__start__", "supervisor_tools"}


class GraphStreamIdleTimeoutError(TimeoutError):
    def __init__(self, *, run_id: str, session_id: str, idle_seconds: float, phase: str, last_event: str | None) -> None:
        last_event_hint = f", last_event={last_event}" if last_event else ""
        super().__init__(
            "Supervisor event stream timeout after "
            f"{int(idle_seconds)}s of inactivity while waiting in phase={phase} "
            f"(session={session_id}, run={run_id}{last_event_hint})."
        )


@dataclass(slots=True)
class GraphStreamWatchdogState:
    active_tool_call_ids: set[str] = field(default_factory=set)
    has_productive_stream_activity: bool = False
    last_observed_event: str | None = None

    def idle_phase(self) -> str:
        if self.active_tool_call_ids:
            return "tool_wait"
        if self.has_productive_stream_activity:
            return "stream_progress"
        return "stream_start"

    def idle_timeout_seconds(self) -> float:
        if self.active_tool_call_ids:
            return ACTIVE_TOOL_STREAM_IDLE_TIMEOUT_SECONDS
        if self.has_productive_stream_activity:
            return ACTIVE_MODEL_STREAM_IDLE_TIMEOUT_SECONDS
        return INITIAL_STREAM_IDLE_TIMEOUT_SECONDS

    def observe_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event") or "").strip()
        name = str(event.get("name") or "").strip()
        self.last_observed_event = f"{kind}:{name}" if name else kind or None

    def finish_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event") or "").strip()
        name = str(event.get("name") or "").strip()
        if kind == "on_tool_end":
            if not self.active_tool_call_ids:
                self.has_productive_stream_activity = False
            return
        if kind == "on_chat_model_start":
            if not self.active_tool_call_ids:
                self.has_productive_stream_activity = False
            return
        if kind == "on_chat_model_end":
            if not self.active_tool_call_ids:
                self.has_productive_stream_activity = False
            return
        if kind != "on_chain_start":
            return
        if not name or name in _IGNORED_CHAIN_START_NAMES or name.endswith("_tools"):
            return
        if not self.active_tool_call_ids:
            self.has_productive_stream_activity = False

    def note_text_progress(self) -> None:
        self.has_productive_stream_activity = True

    def note_tool_start(self, tool_call_id: str | None) -> None:
        self.has_productive_stream_activity = True
        if tool_call_id:
            self.active_tool_call_ids.add(tool_call_id)

    def note_tool_end(self, tool_call_id: str | None) -> None:
        self.has_productive_stream_activity = True
        if tool_call_id:
            self.active_tool_call_ids.discard(tool_call_id)


async def next_graph_stream_event(
    stream_iter,
    *,
    state: GraphStreamWatchdogState,
    session_id: str,
    run_id: str,
    on_timeout: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    idle_timeout = state.idle_timeout_seconds()
    phase = state.idle_phase()
    try:
        event = await asyncio.wait_for(anext(stream_iter), timeout=idle_timeout)
    except asyncio.TimeoutError as exc:
        payload = {
            "idleTimeoutSeconds": idle_timeout,
            "phase": phase,
            "activeToolCount": len(state.active_tool_call_ids),
            "lastObservedEvent": state.last_observed_event,
        }
        if on_timeout is not None:
            on_timeout(payload)
        raise GraphStreamIdleTimeoutError(
            run_id=run_id,
            session_id=session_id,
            idle_seconds=idle_timeout,
            phase=phase,
            last_event=state.last_observed_event,
        ) from exc
    state.observe_event(event)
    return event
