from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Optional

from erc.command_service import command_service


@dataclass(slots=True)
class SessionLaneDecision:
    acquired: bool
    policy: str
    waited: bool = False
    active_run_id: Optional[str] = None
    rejected_by_run_id: Optional[str] = None
    interrupted_run_id: Optional[str] = None


class SessionLaneScheduler:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_runs: dict[str, str] = {}

    def try_acquire(self, session_id: str, run_id: str, *, policy: str = "queue") -> SessionLaneDecision:
        normalized_policy = str(policy or "queue").strip().lower() or "queue"
        with self._condition:
            active_run_id = self._active_runs.get(session_id)
            if active_run_id in {None, run_id}:
                self._active_runs[session_id] = run_id
                return SessionLaneDecision(acquired=True, policy=normalized_policy, active_run_id=active_run_id)

            if normalized_policy == "reject":
                return SessionLaneDecision(
                    acquired=False,
                    policy=normalized_policy,
                    active_run_id=active_run_id,
                    rejected_by_run_id=active_run_id,
                )

            interrupted_run_id = None
            if normalized_policy == "interrupt_then_replace":
                interrupted_run_id = active_run_id
                command_service.interrupt_run(
                    active_run_id,
                    reason=f"session_lane_replaced_by:{run_id}",
                )

            return SessionLaneDecision(
                acquired=False,
                policy=normalized_policy,
                waited=True,
                active_run_id=active_run_id,
                interrupted_run_id=interrupted_run_id,
            )

    def acquire(self, session_id: str, run_id: str, *, policy: str = "queue") -> SessionLaneDecision:
        normalized_policy = str(policy or "queue").strip().lower() or "queue"
        with self._condition:
            active_run_id = self._active_runs.get(session_id)
            if active_run_id in {None, run_id}:
                self._active_runs[session_id] = run_id
                return SessionLaneDecision(acquired=True, policy=normalized_policy, active_run_id=active_run_id)

            if normalized_policy == "reject":
                return SessionLaneDecision(
                    acquired=False,
                    policy=normalized_policy,
                    active_run_id=active_run_id,
                    rejected_by_run_id=active_run_id,
                )

            interrupted_run_id = None
            if normalized_policy == "interrupt_then_replace":
                interrupted_run_id = active_run_id
                command_service.interrupt_run(
                    active_run_id,
                    reason=f"session_lane_replaced_by:{run_id}",
                )

            waited = True
            while True:
                current = self._active_runs.get(session_id)
                if current in {None, run_id}:
                    self._active_runs[session_id] = run_id
                    return SessionLaneDecision(
                        acquired=True,
                        policy=normalized_policy,
                        waited=waited,
                        active_run_id=active_run_id,
                        interrupted_run_id=interrupted_run_id,
                    )
                self._condition.wait()

    async def acquire_async(self, session_id: str, run_id: str, *, policy: str = "queue") -> SessionLaneDecision:
        return await asyncio.to_thread(self.acquire, session_id, run_id, policy=policy)

    def release(self, session_id: str, run_id: str) -> None:
        with self._condition:
            current = self._active_runs.get(session_id)
            if current == run_id:
                self._active_runs.pop(session_id, None)
                self._condition.notify_all()

    async def release_async(self, session_id: str, run_id: str) -> None:
        await asyncio.to_thread(self.release, session_id, run_id)

    def get_active_run(self, session_id: str) -> Optional[str]:
        with self._condition:
            return self._active_runs.get(session_id)


session_lane_scheduler = SessionLaneScheduler()
