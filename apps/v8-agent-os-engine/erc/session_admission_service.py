from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.database import db
from core.realtime_protocol import utc_now_iso

from erc.event_bus import event_bus
from erc.models import RuntimeSource
from erc.session_lane_scheduler import SessionLaneDecision, session_lane_scheduler

_DURABLE_IDLE_STATE = "idle"
_DURABLE_ACTIVE_STATE = "active"
_DURABLE_QUEUED_STATE = "queued"
_DURABLE_REJECTED_STATE = "rejected"


@dataclass(slots=True)
class SessionLaneView:
    session_id: str
    state: str
    policy: str
    active_run_id: Optional[str] = None
    queued_run_id: Optional[str] = None
    blocked_by_run_id: Optional[str] = None
    last_transition: Optional[str] = None
    last_transition_ts: Optional[str] = None
    metadata: Dict[str, Any] | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "policy": self.policy,
            "activeRunId": self.active_run_id,
            "queuedRunId": self.queued_run_id,
            "blockedByRunId": self.blocked_by_run_id,
            "lastTransition": self.last_transition,
            "lastTransitionTs": self.last_transition_ts,
            "metadata": dict(self.metadata or {}),
        }


class SessionAdmissionService:
    def _state_to_transition(
        self,
        *,
        state: str,
        current: SessionLaneView,
        policy: str,
        interrupted_run_id: Optional[str] = None,
    ) -> str:
        normalized_state = str(state or _DURABLE_IDLE_STATE).strip().lower() or _DURABLE_IDLE_STATE
        if normalized_state == _DURABLE_ACTIVE_STATE:
            if interrupted_run_id and str(policy or "").strip().lower() == "interrupt_then_replace":
                return "replaced"
            return "acquired"
        if normalized_state == _DURABLE_IDLE_STATE and (
            current.active_run_id or current.queued_run_id or current.blocked_by_run_id
        ):
            return "released"
        return normalized_state

    def _current_view(self, session_id: str) -> SessionLaneView:
        row = db.get_session_lane_record(session_id)
        if not row:
            return SessionLaneView(session_id=session_id, state=_DURABLE_IDLE_STATE, policy="queue", metadata={})
        return SessionLaneView(
            session_id=session_id,
            state=str(row.get("state") or _DURABLE_IDLE_STATE),
            policy=str(row.get("policy") or "queue"),
            active_run_id=row.get("active_run_id"),
            queued_run_id=row.get("queued_run_id"),
            blocked_by_run_id=row.get("blocked_by_run_id"),
            last_transition=row.get("last_transition"),
            last_transition_ts=row.get("last_transition_ts"),
            metadata=dict(row.get("metadata") or {}),
        )

    def get_lane_view(self, session_id: str) -> Dict[str, Any]:
        return self._current_view(session_id).as_dict()

    def _append_history(
        self,
        *,
        session_id: str,
        run_id: str,
        action: str,
        policy: str,
        active_run_id: Optional[str],
        interrupted_run_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        db.add_session_lane_queue_entry(
            entry_id=f"lane_{uuid.uuid4().hex}",
            session_id=session_id,
            run_id=run_id,
            action=action,
            policy=policy,
            active_run_id=active_run_id,
            interrupted_run_id=interrupted_run_id,
            metadata=metadata or {},
        )

    def _persist_decision(
        self,
        *,
        session_id: str,
        run_id: str,
        runtime_kind: str,
        policy: str,
        action: str,
        active_run_id: Optional[str],
        queued_run_id: Optional[str],
        blocked_by_run_id: Optional[str],
        interrupted_run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now_iso()
        current = self._current_view(session_id)
        transition = self._state_to_transition(
            state=action,
            current=current,
            policy=policy,
            interrupted_run_id=interrupted_run_id,
        )
        next_metadata = dict(current.metadata or {})
        next_metadata.update(metadata or {})
        next_metadata["runtimeKind"] = runtime_kind
        changed = (
            current.state != action
            or current.policy != policy
            or current.active_run_id != active_run_id
            or current.queued_run_id != queued_run_id
            or current.blocked_by_run_id != blocked_by_run_id
        )
        db.upsert_session_lane_record(
            session_id=session_id,
            active_run_id=active_run_id,
            queued_run_id=queued_run_id,
            blocked_by_run_id=blocked_by_run_id,
            policy=policy,
            state=action,
            last_transition=transition,
            last_transition_ts=now,
            metadata=next_metadata,
        )
        if changed:
            self._append_history(
                session_id=session_id,
                run_id=run_id,
                action=transition,
                policy=policy,
                active_run_id=active_run_id or blocked_by_run_id,
                interrupted_run_id=interrupted_run_id,
                metadata=next_metadata,
            )

    def try_acquire(
        self,
        session_id: str,
        run_id: str,
        *,
        policy: str,
        runtime_kind: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionLaneDecision:
        decision = session_lane_scheduler.try_acquire(session_id, run_id, policy=policy)
        normalized_policy = str(decision.policy or policy or "queue")
        if decision.acquired:
            self._persist_decision(
                session_id=session_id,
                run_id=run_id,
                runtime_kind=runtime_kind,
                policy=normalized_policy,
                action=_DURABLE_ACTIVE_STATE,
                active_run_id=run_id,
                queued_run_id=None,
                blocked_by_run_id=None,
                interrupted_run_id=decision.interrupted_run_id,
                metadata=metadata,
            )
        elif decision.waited:
            self._persist_decision(
                session_id=session_id,
                run_id=run_id,
                runtime_kind=runtime_kind,
                policy=normalized_policy,
                action=_DURABLE_QUEUED_STATE,
                active_run_id=decision.active_run_id,
                queued_run_id=run_id,
                blocked_by_run_id=decision.active_run_id,
                interrupted_run_id=decision.interrupted_run_id,
                metadata=metadata,
            )
        else:
            self._persist_decision(
                session_id=session_id,
                run_id=run_id,
                runtime_kind=runtime_kind,
                policy=normalized_policy,
                action=_DURABLE_REJECTED_STATE,
                active_run_id=decision.active_run_id,
                queued_run_id=None,
                blocked_by_run_id=decision.rejected_by_run_id or decision.active_run_id,
                interrupted_run_id=decision.interrupted_run_id,
                metadata=metadata,
            )
        return decision

    def acquire(
        self,
        session_id: str,
        run_id: str,
        *,
        policy: str,
        runtime_kind: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionLaneDecision:
        decision = session_lane_scheduler.acquire(session_id, run_id, policy=policy)
        normalized_policy = str(decision.policy or policy or "queue")
        if decision.acquired and decision.waited:
            self._persist_decision(
                session_id=session_id,
                run_id=run_id,
                runtime_kind=runtime_kind,
                policy=normalized_policy,
                action=_DURABLE_QUEUED_STATE,
                active_run_id=decision.active_run_id,
                queued_run_id=run_id,
                blocked_by_run_id=decision.active_run_id,
                interrupted_run_id=decision.interrupted_run_id,
                metadata=metadata,
            )
        self._persist_decision(
            session_id=session_id,
            run_id=run_id,
            runtime_kind=runtime_kind,
            policy=normalized_policy,
            action=_DURABLE_ACTIVE_STATE if decision.acquired else _DURABLE_REJECTED_STATE,
            active_run_id=run_id if decision.acquired else decision.active_run_id,
            queued_run_id=None if decision.acquired else run_id,
            blocked_by_run_id=None if decision.acquired else (decision.rejected_by_run_id or decision.active_run_id),
            interrupted_run_id=decision.interrupted_run_id,
            metadata={
                **(metadata or {}),
                "waited": bool(decision.waited),
            },
        )
        return decision

    async def acquire_async(
        self,
        session_id: str,
        run_id: str,
        *,
        policy: str,
        runtime_kind: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionLaneDecision:
        decision = await session_lane_scheduler.acquire_async(session_id, run_id, policy=policy)
        normalized_policy = str(decision.policy or policy or "queue")
        if decision.acquired and decision.waited:
            self._persist_decision(
                session_id=session_id,
                run_id=run_id,
                runtime_kind=runtime_kind,
                policy=normalized_policy,
                action=_DURABLE_QUEUED_STATE,
                active_run_id=decision.active_run_id,
                queued_run_id=run_id,
                blocked_by_run_id=decision.active_run_id,
                interrupted_run_id=decision.interrupted_run_id,
                metadata=metadata,
            )
        self._persist_decision(
            session_id=session_id,
            run_id=run_id,
            runtime_kind=runtime_kind,
            policy=normalized_policy,
            action=_DURABLE_ACTIVE_STATE if decision.acquired else _DURABLE_REJECTED_STATE,
            active_run_id=run_id if decision.acquired else decision.active_run_id,
            queued_run_id=None if decision.acquired else run_id,
            blocked_by_run_id=None if decision.acquired else (decision.rejected_by_run_id or decision.active_run_id),
            interrupted_run_id=decision.interrupted_run_id,
            metadata={
                **(metadata or {}),
                "waited": bool(decision.waited),
            },
        )
        return decision

    def release(
        self,
        session_id: str,
        run_id: str,
        *,
        policy: Optional[str] = None,
        runtime_kind: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        session_lane_scheduler.release(session_id, run_id)
        current = self._current_view(session_id)
        if current.active_run_id != run_id and current.queued_run_id != run_id:
            return
        effective_policy = str(policy or current.policy or "queue")
        effective_runtime_kind = str(runtime_kind or (current.metadata or {}).get("runtimeKind") or "unknown")
        self._persist_decision(
            session_id=session_id,
            run_id=run_id,
            runtime_kind=effective_runtime_kind,
            policy=effective_policy,
            action=_DURABLE_IDLE_STATE,
            active_run_id=None,
            queued_run_id=None,
            blocked_by_run_id=None,
            metadata=metadata,
        )

    async def release_async(
        self,
        session_id: str,
        run_id: str,
        *,
        policy: Optional[str] = None,
        runtime_kind: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await session_lane_scheduler.release_async(session_id, run_id)
        current = self._current_view(session_id)
        if current.active_run_id != run_id and current.queued_run_id != run_id:
            return
        effective_policy = str(policy or current.policy or "queue")
        effective_runtime_kind = str(runtime_kind or (current.metadata or {}).get("runtimeKind") or "unknown")
        self._persist_decision(
            session_id=session_id,
            run_id=run_id,
            runtime_kind=effective_runtime_kind,
            policy=effective_policy,
            action=_DURABLE_IDLE_STATE,
            active_run_id=None,
            queued_run_id=None,
            blocked_by_run_id=None,
            metadata=metadata,
        )

    def _emit_lane_reconciliation_event(
        self,
        *,
        session_id: str,
        run_id: Optional[str],
        topic: str,
        payload: Dict[str, Any],
    ) -> None:
        if not run_id:
            return
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=session_id,
            run_id=run_id,
            source=RuntimeSource(
                plane="engine",
                component="erc",
                node="session_admission_reconciler",
                agent_id=None,
            ),
        )
        emitter.emit(topic, payload)

    def reconcile_after_restart(self) -> Dict[str, int]:
        reviewed = 0
        repaired = 0
        released = 0
        rejected = 0

        for row in db.list_session_lane_records(limit=5000):
            reviewed += 1
            row_released = False
            row_rejected = False
            current = SessionLaneView(
                session_id=str(row.get("session_id") or ""),
                state=str(row.get("state") or _DURABLE_IDLE_STATE),
                policy=str(row.get("policy") or "queue"),
                active_run_id=row.get("active_run_id"),
                queued_run_id=row.get("queued_run_id"),
                blocked_by_run_id=row.get("blocked_by_run_id"),
                last_transition=row.get("last_transition"),
                last_transition_ts=row.get("last_transition_ts"),
                metadata=dict(row.get("metadata") or {}),
            )
            if current.state == _DURABLE_IDLE_STATE and not (
                current.active_run_id or current.queued_run_id or current.blocked_by_run_id
            ):
                continue

            repair_metadata = dict(current.metadata or {})
            repair_metadata.update(
                {
                    "reconciledAt": utc_now_iso(),
                    "reconciledReason": "engine_restart",
                    "previousState": current.state,
                    "previousActiveRunId": current.active_run_id,
                    "previousQueuedRunId": current.queued_run_id,
                    "previousBlockedByRunId": current.blocked_by_run_id,
                }
            )

            if current.active_run_id:
                self._append_history(
                    session_id=current.session_id,
                    run_id=current.active_run_id,
                    action="released",
                    policy=current.policy,
                    active_run_id=current.active_run_id,
                    interrupted_run_id=None,
                    metadata={**repair_metadata, "repairOutcome": "released"},
                )
                self._emit_lane_reconciliation_event(
                    session_id=current.session_id,
                    run_id=current.active_run_id,
                    topic="run.lane.released",
                    payload={
                        "policy": current.policy,
                        "session_id": current.session_id,
                        "reason": "engine_restart_reconciliation",
                        "reconciled": True,
                    },
                )
                released += 1
                row_released = True

            if current.queued_run_id and current.queued_run_id != current.active_run_id:
                self._append_history(
                    session_id=current.session_id,
                    run_id=current.queued_run_id,
                    action="rejected",
                    policy=current.policy,
                    active_run_id=current.active_run_id or current.blocked_by_run_id,
                    interrupted_run_id=None,
                    metadata={**repair_metadata, "repairOutcome": "rejected"},
                )
                self._emit_lane_reconciliation_event(
                    session_id=current.session_id,
                    run_id=current.queued_run_id,
                    topic="run.lane.rejected",
                    payload={
                        "policy": current.policy,
                        "session_id": current.session_id,
                        "busy_run_id": current.active_run_id or current.blocked_by_run_id,
                        "reason": "engine_restart_reconciliation",
                        "reconciled": True,
                    },
                )
                rejected += 1
                row_rejected = True

            repaired_transition = "idle"
            if row_released:
                repaired_transition = "released"
            elif row_rejected:
                repaired_transition = "rejected"

            db.upsert_session_lane_record(
                session_id=current.session_id,
                active_run_id=None,
                queued_run_id=None,
                blocked_by_run_id=None,
                policy=current.policy or "queue",
                state=_DURABLE_IDLE_STATE,
                last_transition=repaired_transition,
                last_transition_ts=utc_now_iso(),
                metadata=repair_metadata,
            )
            repaired += 1

        return {
            "reviewed": reviewed,
            "repaired": repaired,
            "released": released,
            "rejected": rejected,
        }


session_admission_service = SessionAdmissionService()
