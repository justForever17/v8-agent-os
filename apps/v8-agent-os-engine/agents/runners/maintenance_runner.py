from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from agents import memory_agent
from erc.run_service import run_service
from erc.runtime_stability import runtime_stability_service
from erc.session_admission_service import session_admission_service
from core.storage import storage
from runtimes.memory.runtime import memory_runtime


class MemoryAgentRunner:
    agent_id = "memory_agent"

    def _session_extraction_runtime_session_id(self, source_session_id: str) -> str:
        digest = hashlib.md5(str(source_session_id).encode("utf-8")).hexdigest()[:10]
        return f"hook:on_chat_end:memory:{digest}"

    def _emit_run_created(self, run_handle, *, task_kind: str, trigger_source: Optional[str], extra: Optional[Dict[str, Any]] = None) -> None:
        run_handle.emit(
            "run.created",
            {
                "run_id": run_handle.run_id,
                "transport": "maintenance",
                "trigger_source": trigger_source,
                "task_kind": task_kind,
                **(extra or {}),
            },
        )

    def _finalize_run(self, run_handle, *, reason: str, result: Dict[str, Any]) -> Dict[str, Any]:
        memory_runtime.update_run_metadata(
            run_handle.run_id,
            {
                "memory_result": result,
                "memory_task_kind": result.get("task_kind"),
                "memory_status": result.get("status"),
            },
        )
        run_handle.complete(reason=reason, node="maintenance_runner")
        return {
            "run_id": run_handle.run_id,
            "session_id": run_handle.session_id,
            "result": result,
        }

    def run_session_extraction(
        self,
        session_id: str,
        *,
        trigger_source: str = "SYSTEM",
        run_id: Optional[str] = None,
        user_id: str = "system",
        parent_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        memory_config = storage.get_memory_config() or {}
        if not bool(memory_config.get("extraction_enabled", True)):
            return {
                "run_id": None,
                "session_id": session_id,
                "result": {
                    "status": "skipped",
                    "task_kind": "session_extraction",
                    "session_id": session_id,
                    "reason": "extraction_disabled",
                    "parent_run_id": parent_run_id,
                },
            }

        extraction_session_id = self._session_extraction_runtime_session_id(session_id)
        run_handle = memory_runtime.begin_run(
            task_kind="session_extraction",
            trigger_source=trigger_source,
            session_id=extraction_session_id,
            user_id=user_id,
            run_id=run_id,
            metadata={
                "parent_run_id": parent_run_id,
                "parent_session_id": session_id,
                "source_session_id": session_id,
                "source": "hook:on_chat_end",
                "hiddenFromHistory": True,
            },
        )
        self._emit_run_created(
            run_handle,
            task_kind="session_extraction",
            trigger_source=trigger_source,
            extra={
                "parent_run_id": parent_run_id,
                "parent_session_id": session_id,
                "source_session_id": session_id,
            },
        )
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_decision = session_admission_service.acquire(
            extraction_session_id,
            run_handle.run_id,
            policy=lane_policy,
            runtime_kind="memory",
            metadata={
                "taskKind": "session_extraction",
                "triggerSource": trigger_source,
                "sourceSessionId": session_id,
                "parentRunId": parent_run_id,
                "hiddenFromHistory": True,
            },
        )
        if not lane_decision.acquired:
            error_message = (
                f"Session lane busy: session '{extraction_session_id}' is already running "
                f"'{lane_decision.rejected_by_run_id or lane_decision.active_run_id}'."
            )
            run_handle.emit(
                "run.lane.rejected",
                {
                    "policy": lane_decision.policy,
                    "busy_run_id": lane_decision.rejected_by_run_id or lane_decision.active_run_id,
                    "session_id": extraction_session_id,
                },
            )
            run_service.transition_run(run_handle.run_id, status="cancelled", error_message=error_message)
            return {
                "run_id": run_handle.run_id,
                "session_id": extraction_session_id,
                "result": {
                    "status": "rejected",
                    "task_kind": "session_extraction",
                    "reason": error_message,
                    "source_session_id": session_id,
                },
            }
        if lane_decision.waited:
            run_handle.emit(
                "run.lane.queued",
                {
                    "policy": lane_decision.policy,
                    "blocked_by_run_id": lane_decision.active_run_id,
                    "interrupted_run_id": lane_decision.interrupted_run_id,
                },
            )
            run_handle.emit(
                "run.liveness.blocked",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": f"lane_busy:{lane_decision.active_run_id}",
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        run_handle.emit(
            "run.lane.acquired",
            {
                "policy": lane_decision.policy,
                "waited": lane_decision.waited,
                "previous_run_id": lane_decision.active_run_id,
                "interrupted_run_id": lane_decision.interrupted_run_id,
            },
        )
        if lane_decision.waited:
            run_handle.emit(
                "run.liveness.recovered",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        run_service.transition_run(run_handle.run_id, status="running")
        run_handle.transition("running", reason=trigger_source, node="maintenance_runner")
        run_handle.emit(
            "memory.run.started",
            {
                "task_kind": "session_extraction",
                "session_id": session_id,
                "runtime_session_id": extraction_session_id,
                "parent_run_id": parent_run_id,
            },
        )

        try:
            result = memory_agent.analyze_session_memory(
                session_id,
                trigger_source=trigger_source,
                run_handle=run_handle,
                parent_run_id=parent_run_id,
            )
        except Exception as exc:
            run_handle.fail(str(exc), node="maintenance_runner")
            raise
        finally:
            try:
                session_admission_service.release(extraction_session_id, run_handle.run_id)
                run_handle.emit(
                    "run.lane.released",
                    {
                        "policy": lane_decision.policy,
                        "session_id": extraction_session_id,
                    },
                )
            except Exception:
                pass

        status = result.get("status", "completed")
        reason = "memory_session_extraction_completed"
        if status == "skipped":
            reason = "memory_session_extraction_skipped"
        elif status == "failed":
            run_handle.fail(result.get("reason") or "memory extraction failed", node="maintenance_runner")
            return {
                "run_id": run_handle.run_id,
                "session_id": run_handle.session_id,
                "result": result,
            }
        return self._finalize_run(run_handle, reason=reason, result=result)

    async def run_periodic_summary(
        self,
        tier: str,
        *,
        target_date: Optional[datetime] = None,
        trigger_source: str = "SYSTEM",
        run_id: Optional[str] = None,
        user_id: str = "system",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        effective_session_id = session_id or f"memory:summary:{tier}"
        run_handle = memory_runtime.begin_run(
            task_kind="periodic_summary",
            trigger_source=trigger_source,
            session_id=effective_session_id,
            user_id=user_id,
            run_id=run_id,
            tier=tier,
            metadata={"target_date": target_date.isoformat() if target_date else None},
        )
        self._emit_run_created(
            run_handle,
            task_kind="periodic_summary",
            trigger_source=trigger_source,
            extra={"tier": tier},
        )
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_decision = session_admission_service.acquire(
            effective_session_id,
            run_handle.run_id,
            policy=lane_policy,
            runtime_kind="memory",
            metadata={
                "taskKind": "periodic_summary",
                "triggerSource": trigger_source,
                "tier": tier,
                "hiddenFromHistory": effective_session_id.startswith("memory:summary:"),
            },
        )
        if not lane_decision.acquired:
            error_message = (
                f"Session lane busy: session '{effective_session_id}' is already running "
                f"'{lane_decision.rejected_by_run_id or lane_decision.active_run_id}'."
            )
            run_handle.emit(
                "run.lane.rejected",
                {
                    "policy": lane_decision.policy,
                    "busy_run_id": lane_decision.rejected_by_run_id or lane_decision.active_run_id,
                    "session_id": effective_session_id,
                },
            )
            run_service.transition_run(run_handle.run_id, status="cancelled", error_message=error_message)
            return {
                "run_id": run_handle.run_id,
                "session_id": effective_session_id,
                "result": {
                    "status": "rejected",
                    "task_kind": "periodic_summary",
                    "reason": error_message,
                    "tier": tier,
                },
            }
        if lane_decision.waited:
            run_handle.emit(
                "run.lane.queued",
                {
                    "policy": lane_decision.policy,
                    "blocked_by_run_id": lane_decision.active_run_id,
                    "interrupted_run_id": lane_decision.interrupted_run_id,
                },
            )
            run_handle.emit(
                "run.liveness.blocked",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": f"lane_busy:{lane_decision.active_run_id}",
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        run_handle.emit(
            "run.lane.acquired",
            {
                "policy": lane_decision.policy,
                "waited": lane_decision.waited,
                "previous_run_id": lane_decision.active_run_id,
                "interrupted_run_id": lane_decision.interrupted_run_id,
            },
        )
        if lane_decision.waited:
            run_handle.emit(
                "run.liveness.recovered",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        run_service.transition_run(run_handle.run_id, status="running")
        run_handle.transition("running", reason=trigger_source, node="maintenance_runner")
        run_handle.emit(
            "memory.run.started",
            {
                "task_kind": "periodic_summary",
                "tier": tier,
                "target_date": target_date.isoformat() if target_date else None,
            },
        )

        try:
            result = await memory_agent.generate_periodic_summary(
                tier=tier,
                target_date=target_date,
                trigger_source=trigger_source,
                run_handle=run_handle,
            )
        except Exception as exc:
            run_handle.fail(str(exc), node="maintenance_runner")
            raise
        finally:
            try:
                session_admission_service.release(effective_session_id, run_handle.run_id)
                run_handle.emit(
                    "run.lane.released",
                    {
                        "policy": lane_decision.policy,
                        "session_id": effective_session_id,
                    },
                )
            except Exception:
                pass

        status = result.get("status", "completed")
        reason = "memory_periodic_summary_completed"
        if status == "skipped":
            reason = "memory_periodic_summary_skipped"
        elif status == "failed":
            run_handle.fail(result.get("reason") or "periodic summary failed", node="maintenance_runner")
            return {
                "run_id": run_handle.run_id,
                "session_id": run_handle.session_id,
                "result": result,
            }
        return self._finalize_run(run_handle, reason=reason, result=result)

memory_agent_runner = MemoryAgentRunner()
