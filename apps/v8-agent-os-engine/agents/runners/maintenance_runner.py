from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from agents import memory_agent
from core import memory_store as memory_store_module
from core.knowledge_db import knowledge_db
from erc.run_service import run_service
from erc.runtime_context import bind_runtime_context
from erc.runtime_stability import runtime_stability_service
from erc.session_admission_service import session_admission_service
from core.storage import storage
from runtimes.memory.runtime import memory_runtime


class MemoryAgentRunner:
    agent_id = "memory_agent"

    def _quarantine_global_high_risk_memory(self) -> Dict[str, Any]:
        quarantined_preferences = 0
        quarantined_preference_keys: list[str] = []
        quarantined_knowledge = 0
        quarantined_knowledge_ids: list[str] = []

        global_preferences = memory_store_module.memory_store.get_scope_preferences_raw("global")
        for key, value in global_preferences.items():
            reason = memory_agent.classify_global_preference_risk(key, value)
            if not reason:
                continue
            memory_store_module.memory_store.quarantine_global_preference(
                key=key,
                value=value,
                reason=reason,
                metadata={"source": "maintenance", "scope": "global"},
            )
            quarantined_preferences += 1
            quarantined_preference_keys.append(str(key))

        for item in knowledge_db.get_all_knowledge(scope=None, limit=500, status="active"):
            if str(item.get("scope") or "").strip() != "global":
                continue
            reason = memory_agent.classify_global_knowledge_risk(
                str(item.get("fact") or ""),
                str(item.get("category") or ""),
            )
            if not reason:
                continue
            fact_id = str(item.get("id") or "").strip()
            if not fact_id:
                continue
            if knowledge_db.quarantine_knowledge(fact_id):
                quarantined_knowledge += 1
                quarantined_knowledge_ids.append(fact_id)

        return {
            "quarantinedPreferenceCount": quarantined_preferences,
            "quarantinedPreferenceKeys": quarantined_preference_keys,
            "quarantinedKnowledgeCount": quarantined_knowledge,
            "quarantinedKnowledgeIds": quarantined_knowledge_ids,
        }

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

    def _maintenance_runtime_session_id(self) -> str:
        return "memory:maintenance"

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
            with bind_runtime_context(
                runtime_kind="memory",
                session_id=session_id,
                run_id=run_handle.run_id,
                user_id=user_id,
                trigger_source=trigger_source,
                agent_id=self.agent_id,
            ):
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

    async def run_maintenance(
        self,
        *,
        trigger_source: str = "SYSTEM",
        run_id: Optional[str] = None,
        user_id: str = "system",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        effective_session_id = session_id or self._maintenance_runtime_session_id()
        run_handle = memory_runtime.begin_run(
            task_kind="maintenance",
            trigger_source=trigger_source,
            session_id=effective_session_id,
            user_id=user_id,
            run_id=run_id,
            metadata={"hiddenFromHistory": True},
        )
        self._emit_run_created(run_handle, task_kind="maintenance", trigger_source=trigger_source)
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_decision = session_admission_service.acquire(
            effective_session_id,
            run_handle.run_id,
            policy=lane_policy,
            runtime_kind="memory",
            metadata={
                "taskKind": "maintenance",
                "triggerSource": trigger_source,
                "hiddenFromHistory": True,
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
                    "task_kind": "maintenance",
                    "reason": error_message,
                },
            }
        run_service.transition_run(run_handle.run_id, status="running")
        run_handle.transition("running", reason=trigger_source, node="maintenance_runner")
        run_handle.emit(
            "memory.run.started",
            {
                "task_kind": "maintenance",
                "runtime_session_id": effective_session_id,
            },
        )

        before_health = memory_runtime.get_memory_map_health()
        targets = memory_runtime.list_summary_targets(states=["missing", "stale"])
        touched_refs: list[str] = []
        summary_backfilled_count = 0
        failed_targets: list[dict[str, str]] = []
        legacy_summary_touched_refs: list[str] = []
        legacy_summary_backfilled_count = 0
        workflow_maintenance_result: Dict[str, Any] = {}
        global_quarantine_result: Dict[str, Any] = {}

        try:
            for target in targets:
                latest_day = str(target.get("latestDay") or "").strip()
                if not latest_day:
                    continue
                tier = str(target.get("kind") or "").strip()
                memory_ref = str(target.get("memoryRef") or "").strip()
                result = await memory_agent.generate_periodic_summary(
                    tier=tier,
                    target_date=datetime.fromisoformat(latest_day),
                    trigger_source=trigger_source,
                    run_handle=run_handle,
                )
                if str(result.get("status") or "").strip().lower() == "completed":
                    summary_backfilled_count += 1
                    touched_refs.append(memory_ref)
                    run_handle.emit(
                        "memory.summary_backfilled",
                        {
                            "memory_ref": memory_ref,
                            "tier": tier,
                            "latest_day": latest_day,
                        },
                    )
                else:
                    failed_targets.append(
                        {
                            "memoryRef": memory_ref,
                            "tier": tier,
                            "reason": str(result.get("reason") or result.get("status") or "unknown"),
                        }
                    )
            backfill_result = memory_runtime.backfill_periodic_summaries() or {}
            legacy_summary_touched_refs = [
                str(item).strip()
                for item in list(backfill_result.get("touchedRefs") or [])
                if str(item).strip()
            ]
            legacy_summary_backfilled_count = int(backfill_result.get("updatedCount") or 0)
            global_quarantine_result = self._quarantine_global_high_risk_memory()
            workflow_maintenance_result = memory_runtime.run_workflow_maintenance() or {}
            run_handle.emit(
                "memory.workflow.maintenance.completed",
                {
                    "candidate_count": int(workflow_maintenance_result.get("candidateCount") or 0),
                    "updated_count": int(workflow_maintenance_result.get("updatedCount") or 0),
                    "activated_count": int(workflow_maintenance_result.get("activatedCount") or 0),
                    "quarantined_count": int(workflow_maintenance_result.get("quarantinedCount") or 0),
                },
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

        after_health = memory_runtime.get_memory_map_health()
        maintenance_meta = {
            "summaryMissingCountBefore": int(((before_health.get("counts") or {}).get("missing")) or 0),
            "summaryMissingCountAfter": int(((after_health.get("counts") or {}).get("missing")) or 0),
            "summaryStaleCountBefore": int(((before_health.get("counts") or {}).get("stale")) or 0),
            "summaryStaleCountAfter": int(((after_health.get("counts") or {}).get("stale")) or 0),
            "summaryBackfilledCount": summary_backfilled_count,
            "touchedRefs": touched_refs,
            "legacySummaryBackfilledCount": legacy_summary_backfilled_count,
            "legacySummaryTouchedRefs": legacy_summary_touched_refs,
            "failedTargets": failed_targets,
            "globalQuarantinedPreferenceCount": int(global_quarantine_result.get("quarantinedPreferenceCount") or 0),
            "globalQuarantinedPreferenceKeys": list(global_quarantine_result.get("quarantinedPreferenceKeys") or []),
            "globalQuarantinedKnowledgeCount": int(global_quarantine_result.get("quarantinedKnowledgeCount") or 0),
            "globalQuarantinedKnowledgeIds": list(global_quarantine_result.get("quarantinedKnowledgeIds") or []),
            "workflowCandidateCount": int(workflow_maintenance_result.get("candidateCount") or 0),
            "workflowCandidateUpdatedCount": int(workflow_maintenance_result.get("updatedCount") or 0),
            "workflowActiveHintCount": int(workflow_maintenance_result.get("activatedCount") or 0),
            "workflowQuarantinedCount": int(workflow_maintenance_result.get("quarantinedCount") or 0),
            "summaryHealthBefore": before_health,
            "summaryHealthAfter": after_health,
        }
        memory_runtime.update_run_metadata(run_handle.run_id, {"memory_maintenance": maintenance_meta})
        result = {
            "status": "completed" if not failed_targets else "partial",
            "task_kind": "maintenance",
            "summary_missing_count_before": maintenance_meta["summaryMissingCountBefore"],
            "summary_missing_count_after": maintenance_meta["summaryMissingCountAfter"],
            "summary_stale_count_before": maintenance_meta["summaryStaleCountBefore"],
            "summary_stale_count_after": maintenance_meta["summaryStaleCountAfter"],
            "summary_backfilled_count": summary_backfilled_count,
            "legacy_summary_backfilled_count": legacy_summary_backfilled_count,
            "global_quarantined_preference_count": maintenance_meta["globalQuarantinedPreferenceCount"],
            "global_quarantined_knowledge_count": maintenance_meta["globalQuarantinedKnowledgeCount"],
            "workflow_candidate_count": maintenance_meta["workflowCandidateCount"],
            "workflow_candidate_updated_count": maintenance_meta["workflowCandidateUpdatedCount"],
            "workflow_active_hint_count": maintenance_meta["workflowActiveHintCount"],
            "workflow_quarantined_count": maintenance_meta["workflowQuarantinedCount"],
            "touched_refs": touched_refs,
            "legacy_summary_touched_refs": legacy_summary_touched_refs,
            "failed_targets": failed_targets,
        }
        return self._finalize_run(run_handle, reason="memory_maintenance_completed", result=result)

memory_agent_runner = MemoryAgentRunner()
