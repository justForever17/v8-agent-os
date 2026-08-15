from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


_NOISY_TOPICS = {
    "run.text.delta",
    "run.reasoning.delta",
    "run.liveness.heartbeat",
    "tool.started",
    "tool.finished",
    "message.tool.recorded",
    "workbench.document.opened",
    "workbench.document.updated",
}
_NOISY_SUFFIXES = (
    ".text.delta",
    ".reasoning.delta",
    ".tool.started",
    ".tool.finished",
    ".heartbeat",
)
_ACTIVITY_PREFIXES = (
    "run.",
    "runtime.episode.",
    "agent.",
    "subagent.",
    "message.user.",
    "message.agent.",
    "message.assistant.",
    "approval.",
    "ask_user.",
    "human_guidance.",
    "canvas.graph.",
    "session_coordination.",
    "spec.",
    "workspace.binding_",
)


def is_session_activity_topic(topic: str | None) -> bool:
    """Return whether a durable runtime event can change a session's client-visible activity.

    This is intentionally a control-signal filter. Token deltas, tool chatter and
    diagnostics stay on the per-session realtime stream and never fan out through
    the cross-client history index channel.
    """

    normalized = str(topic or "").strip().lower()
    if not normalized or normalized in _NOISY_TOPICS:
        return False
    if normalized.endswith(_NOISY_SUFFIXES) or ".diagnostic" in normalized:
        return False
    return normalized.startswith(_ACTIVITY_PREFIXES)


@dataclass(frozen=True)
class SessionActivitySignal:
    seq: int
    owner_id: str
    session_id: str
    topic: str
    emitted_at: float

    def public_payload(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "sessionId": self.session_id,
            "topic": self.topic,
            "emittedAt": self.emitted_at,
        }


class SessionActivityBroker:
    """Small in-process wakeup broker for authenticated client session indexes.

    The database remains authoritative. Signals contain no runtime payload and
    only tell a client to refresh its compact session index. A bounded buffer
    makes reconnects cheap while a fresh ``ready`` signal still forces recovery
    when the process was restarted or a cursor fell out of the buffer.
    """

    def __init__(self, *, capacity: int = 2048) -> None:
        self._capacity = max(64, int(capacity))
        self._signals: deque[SessionActivitySignal] = deque(maxlen=self._capacity)
        self._seq = 0
        self._condition = threading.Condition()

    @staticmethod
    def _owner_key(owner_id: str | None) -> str:
        return str(owner_id or "").strip().casefold()

    @property
    def current_seq(self) -> int:
        with self._condition:
            return self._seq

    def publish(self, *, owner_id: str, session_id: str, topic: str) -> int | None:
        owner_key = self._owner_key(owner_id)
        normalized_session_id = str(session_id or "").strip()
        normalized_topic = str(topic or "").strip()
        if not owner_key or not normalized_session_id or not normalized_topic:
            return None
        with self._condition:
            self._seq += 1
            signal = SessionActivitySignal(
                seq=self._seq,
                owner_id=owner_key,
                session_id=normalized_session_id,
                topic=normalized_topic,
                emitted_at=time.time(),
            )
            self._signals.append(signal)
            self._condition.notify_all()
            return signal.seq

    def wait(
        self,
        *,
        owner_id: str,
        after_seq: int,
        timeout_seconds: float = 15.0,
    ) -> tuple[int, list[dict[str, Any]]]:
        owner_key = self._owner_key(owner_id)
        cursor = max(0, int(after_seq or 0))
        deadline = time.monotonic() + max(0.05, float(timeout_seconds))
        with self._condition:
            while self._seq <= cursor:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._seq, []
                self._condition.wait(timeout=remaining)
            current_seq = self._seq
            matches = [
                signal.public_payload()
                for signal in self._signals
                if signal.seq > cursor and signal.owner_id == owner_key
            ]
            return current_seq, matches


session_activity_broker = SessionActivityBroker()
