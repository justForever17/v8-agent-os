from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable


INITIAL_STREAM_IDLE_TIMEOUT_SECONDS = 90.0
ACTIVE_MODEL_STREAM_IDLE_TIMEOUT_SECONDS = 45.0
ACTIVE_TOOL_STREAM_IDLE_TIMEOUT_SECONDS = 360.0
_IGNORED_CHAIN_START_NAMES = {"LangGraph", "__start__", "supervisor_tools"}


class GraphStreamIdleTimeoutError(TimeoutError):
    def __init__(self, *, run_id: str, session_id: str, idle_seconds: float, phase: str, last_event: str | None) -> None:
        last_event_hint = f", last_event={last_event}" if last_event else ""
        super().__init__(
            "Supervisor event stream timeout after "
            f"{int(idle_seconds)}s of inactivity while waiting in phase={phase} "
            f"(session={session_id}, run={run_id}{last_event_hint})."
        )


class GraphStreamDownstreamTimeoutError(TimeoutError):
    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        phase: str,
        last_event: str | None,
        message: str,
    ) -> None:
        last_event_hint = f", last_event={last_event}" if last_event else ""
        detail = message.strip() or "Downstream stream iterator raised timeout."
        super().__init__(
            "Downstream stream timeout while waiting in "
            f"phase={phase} (session={session_id}, run={run_id}{last_event_hint}): {detail}"
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


async def _cancel_task_safely(task: asyncio.Task[Any]) -> None:
    if task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, StopAsyncIteration, Exception):
        return


def normalize_stream_iterator_exception(
    exc: Exception,
    *,
    session_id: str,
    run_id: str,
    phase: str,
    last_event: str | None,
) -> Exception:
    if isinstance(exc, (GraphStreamIdleTimeoutError, GraphStreamDownstreamTimeoutError)):
        return exc
    if isinstance(exc, asyncio.TimeoutError):
        return GraphStreamDownstreamTimeoutError(
            run_id=run_id,
            session_id=session_id,
            phase=phase,
            last_event=last_event,
            message=str(exc),
        )
    return exc


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
    next_event_task = asyncio.create_task(anext(stream_iter))
    done, _ = await asyncio.wait({next_event_task}, timeout=idle_timeout)
    if next_event_task not in done:
        await _cancel_task_safely(next_event_task)
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
        )
    try:
        event = next_event_task.result()
    except Exception as exc:
        raise normalize_stream_iterator_exception(
            exc,
            session_id=session_id,
            run_id=run_id,
            phase=phase,
            last_event=state.last_observed_event,
        ) from exc
    state.observe_event(event)
    return event
