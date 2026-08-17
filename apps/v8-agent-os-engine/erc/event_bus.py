from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.database import db
from core.realtime_protocol import build_runtime_event

from erc.models import RuntimeSource


_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.35)
_EMIT_LOCK = threading.Lock()


def _is_runtime_sequence_conflict(exc: sqlite3.IntegrityError) -> bool:
    return "runtime_events.session_id, runtime_events.seq" in str(exc)


def _next_seq_with_retry(session_id: str, current_seq: int) -> int:
    next_seq = current_seq
    for delay in (0.0, *_LOCK_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return db.get_next_runtime_seq(session_id)
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
    return next_seq


@dataclass(slots=True)
class SessionEventEmitter:
    session_id: str
    conversation_id: str
    run_id: Optional[str]
    default_source: RuntimeSource
    _seq: int

    def emit(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        kind: str = "event",
        source: Optional[RuntimeSource] = None,
        canvas_outbox_id: Optional[str] = None,
        event_id: Optional[str] = None,
        event_ts: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        def append(candidate: Dict[str, Any]) -> Optional[int]:
            if canvas_outbox_id:
                return db.project_canvas_graph_run_event_outbox(candidate, outbox_id=canvas_outbox_id)
            return db.add_runtime_event(candidate)

        if canvas_outbox_id:
            event = build_runtime_event(
                kind=kind,
                topic=topic,
                payload=payload,
                session_id=self.session_id,
                conversation_id=self.conversation_id,
                run_id=self.run_id,
                seq=self._seq,
                source=(source or self.default_source).as_dict(),
                event_id=event_id,
                ts=event_ts,
            )
            durable_seq = append(event)
            if not durable_seq:
                return None
            event["seq"] = int(durable_seq)
            self._seq = int(durable_seq) + 1
            return event

        with _EMIT_LOCK:
            while True:
                event = build_runtime_event(
                    kind=kind,
                    topic=topic,
                    payload=payload,
                    session_id=self.session_id,
                    conversation_id=self.conversation_id,
                    run_id=self.run_id,
                    seq=self._seq,
                    source=(source or self.default_source).as_dict(),
                    event_id=event_id,
                    ts=event_ts,
                )
                try:
                    durable_seq = append(event)
                    if not durable_seq:
                        return None
                    event["seq"] = int(durable_seq)
                    self._seq = int(durable_seq) + 1
                    return event
                except sqlite3.IntegrityError as exc:
                    if not _is_runtime_sequence_conflict(exc):
                        raise
                    self._seq = _next_seq_with_retry(self.session_id, self._seq)
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc).lower():
                        raise
                    recovered = False
                    for delay in _LOCK_RETRY_DELAYS:
                        time.sleep(delay)
                        self._seq = _next_seq_with_retry(self.session_id, self._seq)
                        event = build_runtime_event(
                            kind=kind,
                            topic=topic,
                            payload=payload,
                            session_id=self.session_id,
                            conversation_id=self.conversation_id,
                            run_id=self.run_id,
                            seq=self._seq,
                            source=(source or self.default_source).as_dict(),
                            event_id=event_id,
                            ts=event_ts,
                        )
                        try:
                            durable_seq = append(event)
                            if not durable_seq:
                                return None
                            event["seq"] = int(durable_seq)
                            self._seq = int(durable_seq) + 1
                            recovered = True
                            return event
                        except sqlite3.IntegrityError as exc:
                            if not _is_runtime_sequence_conflict(exc):
                                raise
                            self._seq = _next_seq_with_retry(self.session_id, self._seq)
                            continue
                        except sqlite3.OperationalError as retry_exc:
                            if "database is locked" not in str(retry_exc).lower():
                                raise
                    if not recovered:
                        raise

    @property
    def next_seq(self) -> int:
        return self._seq


class RuntimeEventBus:
    def create_emitter(
        self,
        *,
        session_id: str,
        conversation_id: Optional[str],
        run_id: Optional[str],
        source: RuntimeSource,
    ) -> SessionEventEmitter:
        return SessionEventEmitter(
            session_id=session_id,
            conversation_id=conversation_id or session_id,
            run_id=run_id,
            default_source=source,
            _seq=db.get_next_runtime_seq(session_id),
        )


event_bus = RuntimeEventBus()
