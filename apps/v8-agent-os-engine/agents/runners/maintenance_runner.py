from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from agents import memory_agent
from core import memory_store as memory_store_module
from core.knowledge_db import knowledge_db
from core.memory_observability import log_memory_observation
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

    def _maintenance_compaction_options(self) -> Dict[str, Any]:
        memory_config = storage.get_memory_config() or {}
        maintenance = memory_config.get("maintenance") if isinstance(memory_config.get("maintenance"), dict) else {}
        compaction = maintenance.get("compaction") if isinstance(maintenance.get("compaction"), dict) else {}

        def _int_option(key: str, fallback: int, minimum: int, maximum: int) -> int:
            try:
                return max(minimum, min(int(compaction.get(key) or fallback), maximum))
            except (TypeError, ValueError):
                return fallback

        def _float_option(key: str, fallback: float, minimum: float, maximum: float) -> float:
            try:
                return max(minimum, min(float(compaction.get(key) or fallback), maximum))
            except (TypeError, ValueError):
                return fallback

        return {
            "maxCandidatesPerRun": _int_option("maxCandidatesPerRun", 500, 1, 2000),
            "maxClustersPerRun": _int_option("maxClustersPerRun", 80, 1, 500),
            "autoSupersedeThreshold": _float_option("autoSupersedeThreshold", 0.985, 0.95, 1.0),
            "llmReviewEnabled": bool(compaction.get("llmReviewEnabled", False)),
        }

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
            log_memory_observation(
                "session_extraction",
                "SKIPPED",
                trigger=trigger_source,
                sessionId=session_id,
                parentRunId=parent_run_id,
                callsLlm=False,
                skipReason="extraction_disabled",
            )
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
            log_memory_observation(
                "session_extraction",
                "REJECTED",
                trigger=trigger_source,
                sessionId=session_id,
                runId=run_handle.run_id,
                parentRunId=parent_run_id,
                callsLlm=False,
                skipReason="session_lane_busy",
                error=error_message,
            )
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
        log_memory_observation(
            "session_extraction",
            "SKIPPED" if status == "skipped" else "FAILED" if status == "failed" else "SUCCESS",
            trigger=trigger_source,
            sessionId=session_id,
            runId=run_handle.run_id,
            parentRunId=parent_run_id,
            callsLlm=status not in {"skipped", "rejected"},
            skipReason=result.get("reason") if status == "skipped" else None,
            inputCharEstimate=result.get("content_length") or result.get("input_char_estimate"),
            messageCount=result.get("message_count") or result.get("entry_count"),
            extractedPreferenceCount=result.get("extracted_preference_count"),
            extractedKnowledgeCount=result.get("extracted_knowledge_count"),
            persistedPreferenceCount=result.get("persisted_preference_count") or result.get("preference_count"),
            persistedKnowledgeCount=result.get("persisted_knowledge_count") or result.get("knowledge_count"),
            persistedRelationCount=result.get("persisted_relation_count") or result.get("relation_count"),
            memoryPolicy=result.get("memory_policy"),
            transcriptSource=result.get("transcript_source"),
        )
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
            log_memory_observation(
                "periodic_summary",
                "REJECTED",
                trigger=trigger_source,
                tier=tier,
                runId=run_handle.run_id,
                sessionId=effective_session_id,
                callsLlm=False,
                skipReason="session_lane_busy",
                error=error_message,
            )
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
        log_memory_observation(
            "periodic_summary",
            "SKIPPED" if status == "skipped" else "FAILED" if status == "failed" else "SUCCESS",
            trigger=trigger_source,
            tier=tier,
            runId=run_handle.run_id,
            sessionId=effective_session_id,
            targetDate=result.get("target_date"),
            callsLlm=status not in {"skipped", "rejected"},
            dailyLogCount=result.get("daily_log_count"),
            inputCharEstimate=result.get("input_content_length"),
            outputCharEstimate=result.get("content_length"),
            summaryLength=result.get("summary_length"),
            skipReason=result.get("reason") if status == "skipped" else None,
        )
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
            log_memory_observation(
                "maintenance",
                "REJECTED",
                trigger=trigger_source,
                runId=run_handle.run_id,
                sessionId=effective_session_id,
                callsLlm=False,
                skipReason="session_lane_busy",
                error=error_message,
            )
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
        log_memory_observation(
            "maintenance_targets",
            "INFO",
            trigger=trigger_source,
            runId=run_handle.run_id,
            sessionId=effective_session_id,
            callsLlm=False,
            targetCount=len(targets),
            missingCount=int(((before_health.get("counts") or {}).get("missing")) or 0),
            staleCount=int(((before_health.get("counts") or {}).get("stale")) or 0),
        )
        touched_refs: list[str] = []
        summary_backfilled_count = 0
        failed_targets: list[dict[str, str]] = []
        skipped_targets: list[dict[str, str]] = []
        summary_llm_candidate_count = 0
        legacy_summary_touched_refs: list[str] = []
        legacy_summary_backfilled_count = 0
        workflow_maintenance_result: Dict[str, Any] = {}
        global_quarantine_result: Dict[str, Any] = {}
        knowledge_compaction_result: Dict[str, Any] = {}
        graph_maintenance_result: Dict[str, Any] = {}

        try:
            tier_order = {"week": 0, "month": 1, "year": 2}
            for target in sorted(targets, key=lambda item: tier_order.get(str(item.get("kind") or ""), 99)):
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
                try:
                    input_content_length = int(result.get("input_content_length") or 0)
                except (TypeError, ValueError):
                    input_content_length = 0
                if input_content_length > 0:
                    summary_llm_candidate_count += 1
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
                elif str(result.get("status") or "").strip().lower() == "skipped":
                    skipped_targets.append(
                        {
                            "memoryRef": memory_ref,
                            "tier": tier,
                            "latestDay": latest_day,
                            "reason": str(result.get("reason") or "skipped"),
                        }
                    )
                    run_handle.emit(
                        "memory.summary_skipped",
                        {
                            "memory_ref": memory_ref,
                            "tier": tier,
                            "latest_day": latest_day,
                            "reason": str(result.get("reason") or "skipped"),
                        },
                    )
                else:
                    failed_targets.append(
                        {
                            "memoryRef": memory_ref,
                            "tier": tier,
                            "latestDay": latest_day,
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
            compaction_options = self._maintenance_compaction_options()
            knowledge_compaction_result = knowledge_db.maintenance_compact_knowledge(
                limit=int(compaction_options.get("maxCandidatesPerRun") or 500),
                auto_supersede_threshold=float(compaction_options.get("autoSupersedeThreshold") or 0.985),
                max_clusters=int(compaction_options.get("maxClustersPerRun") or 80),
            )
            graph_maintenance_result = (
                dict(knowledge_compaction_result.get("graph") or {})
                if isinstance(knowledge_compaction_result.get("graph"), dict)
                else {}
            )
            run_handle.emit(
                "memory.knowledge.maintenance.completed",
                {
                    "candidate_count": int(knowledge_compaction_result.get("candidateCount") or 0),
                    "superseded_count": int(knowledge_compaction_result.get("supersededCount") or 0),
                    "merge_suggestion_count": int(knowledge_compaction_result.get("mergeSuggestionCount") or 0),
                    "pruned_isolated_entity_count": int(graph_maintenance_result.get("prunedIsolatedEntityCount") or 0),
                    "budget_stopped": bool(knowledge_compaction_result.get("budgetStopped")),
                },
            )
            workflow_maintenance_result = memory_runtime.run_workflow_maintenance() or {}
            run_handle.emit(
                "memory.workflow.maintenance.completed",
                {
                    "candidate_count": int(workflow_maintenance_result.get("candidateCount") or 0),
                    "updated_count": int(workflow_maintenance_result.get("updatedCount") or 0),
                    "activated_count": int(workflow_maintenance_result.get("activatedCount") or 0),
                    "quarantined_count": int(workflow_maintenance_result.get("quarantinedCount") or 0),
                    "merge_suggestion_count": int(workflow_maintenance_result.get("mergeSuggestionCount") or 0),
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
        graph_result = graph_maintenance_result
        knowledge_superseded_count = int(knowledge_compaction_result.get("supersededCount") or 0)
        knowledge_candidate_count = int(knowledge_compaction_result.get("candidateCount") or 0)
        knowledge_merge_suggestion_count = int(knowledge_compaction_result.get("mergeSuggestionCount") or 0)
        graph_pruned_entity_count = int(graph_result.get("prunedIsolatedEntityCount") or 0)
        workflow_candidate_updated_count = int(workflow_maintenance_result.get("updatedCount") or 0)
        workflow_merge_suggestion_count = int(workflow_maintenance_result.get("mergeSuggestionCount") or 0)
        has_mutation = bool(
            summary_backfilled_count
            or legacy_summary_backfilled_count
            or touched_refs
            or legacy_summary_touched_refs
            or int(global_quarantine_result.get("quarantinedPreferenceCount") or 0)
            or int(global_quarantine_result.get("quarantinedKnowledgeCount") or 0)
            or knowledge_superseded_count
            or knowledge_merge_suggestion_count
            or int(graph_result.get("rewiredRelationCount") or 0)
            or graph_pruned_entity_count
            or workflow_candidate_updated_count
            or workflow_merge_suggestion_count
        )
        no_op_reason = ""
        if not failed_targets and not has_mutation:
            if skipped_targets and not knowledge_candidate_count and not int(workflow_maintenance_result.get("candidateCount") or 0):
                no_op_reason = "summary_targets_skipped_and_no_compaction_candidates"
            elif skipped_targets:
                no_op_reason = "summary_targets_skipped_no_safe_mutations"
            else:
                no_op_reason = "no_maintenance_changes"
        phases = [
            {
                "name": "summary_maintenance",
                "status": "failed" if failed_targets else ("completed" if summary_backfilled_count or legacy_summary_backfilled_count else ("skipped" if skipped_targets else "no_op")),
                "completedCount": summary_backfilled_count + legacy_summary_backfilled_count,
                "skippedCount": len(skipped_targets),
                "failedCount": len(failed_targets),
            },
            {
                "name": "knowledge_compaction",
                "status": "completed" if knowledge_superseded_count or knowledge_merge_suggestion_count else ("no_op" if not knowledge_candidate_count else "skipped"),
                "candidateCount": knowledge_candidate_count,
                "supersededCount": knowledge_superseded_count,
                "mergeSuggestionCount": knowledge_merge_suggestion_count,
            },
            {
                "name": "graph_compaction",
                "status": "completed" if int(graph_result.get("rewiredRelationCount") or 0) or graph_pruned_entity_count else "no_op",
                "candidateCount": int(graph_result.get("relationCandidateCount") or 0),
                "rewiredCount": int(graph_result.get("rewiredRelationCount") or 0),
                "orphanedCount": int(graph_result.get("orphanedRelationCount") or 0),
                "prunedIsolatedEntityCount": graph_pruned_entity_count,
            },
            {
                "name": "workflow_chain_maintenance",
                "status": "completed" if workflow_candidate_updated_count or workflow_merge_suggestion_count else "no_op",
                "candidateCount": int(workflow_maintenance_result.get("candidateCount") or 0),
                "updatedCount": workflow_candidate_updated_count,
                "mergeSuggestionCount": workflow_merge_suggestion_count,
            },
        ]
        maintenance_meta = {
            "summary": {
                "status": "partial" if failed_targets else ("completed" if has_mutation else "no_op"),
                "noOpReason": no_op_reason,
            },
            "phases": phases,
            "summaryMissingCountBefore": int(((before_health.get("counts") or {}).get("missing")) or 0),
            "summaryMissingCountAfter": int(((after_health.get("counts") or {}).get("missing")) or 0),
            "summaryStaleCountBefore": int(((before_health.get("counts") or {}).get("stale")) or 0),
            "summaryStaleCountAfter": int(((after_health.get("counts") or {}).get("stale")) or 0),
            "summaryBackfilledCount": summary_backfilled_count,
            "summaryLlmCandidateCount": summary_llm_candidate_count,
            "touchedRefs": touched_refs,
            "legacySummaryBackfilledCount": legacy_summary_backfilled_count,
            "legacySummaryTouchedRefs": legacy_summary_touched_refs,
            "skippedTargets": skipped_targets,
            "skippedTargetCount": len(skipped_targets),
            "failedTargets": failed_targets,
            "failedTargetCount": len(failed_targets),
            "globalQuarantinedPreferenceCount": int(global_quarantine_result.get("quarantinedPreferenceCount") or 0),
            "globalQuarantinedPreferenceKeys": list(global_quarantine_result.get("quarantinedPreferenceKeys") or []),
            "globalQuarantinedKnowledgeCount": int(global_quarantine_result.get("quarantinedKnowledgeCount") or 0),
            "globalQuarantinedKnowledgeIds": list(global_quarantine_result.get("quarantinedKnowledgeIds") or []),
            "knowledgeCandidateCount": knowledge_candidate_count,
            "knowledgeSupersededCount": knowledge_superseded_count,
            "knowledgeMergeSuggestionCount": knowledge_merge_suggestion_count,
            "knowledgeSupersededPairs": list(knowledge_compaction_result.get("supersededPairs") or [])[:20],
            "knowledgeMergeSuggestions": list(knowledge_compaction_result.get("mergeSuggestions") or [])[:20],
            "graphCandidateCount": int(graph_result.get("relationCandidateCount") or 0),
            "graphRewiredRelationCount": int(graph_result.get("rewiredRelationCount") or 0),
            "graphOrphanedRelationCount": int(graph_result.get("orphanedRelationCount") or 0),
            "graphIsolatedEntityCountBefore": int(graph_result.get("isolatedEntityCountBefore") or 0),
            "graphIsolatedEntityCount": int(graph_result.get("isolatedEntityCount") or 0),
            "graphPrunedIsolatedEntityCount": graph_pruned_entity_count,
            "workflowCandidateCount": int(workflow_maintenance_result.get("candidateCount") or 0),
            "workflowCandidateUpdatedCount": workflow_candidate_updated_count,
            "workflowActiveHintCount": int(workflow_maintenance_result.get("activatedCount") or 0),
            "workflowQuarantinedCount": int(workflow_maintenance_result.get("quarantinedCount") or 0),
            "workflowMergeSuggestionCount": workflow_merge_suggestion_count,
            "budgetStopped": bool(knowledge_compaction_result.get("budgetStopped") or workflow_maintenance_result.get("budgetStopped")),
            "noOpReason": no_op_reason,
            "summaryHealthBefore": before_health,
            "summaryHealthAfter": after_health,
        }
        result_status = "partial" if failed_targets else ("completed" if has_mutation else "no_op")
        memory_runtime.update_run_metadata(run_handle.run_id, {"memory_maintenance": maintenance_meta})
        result = {
            "status": result_status,
            "task_kind": "maintenance",
            "summary_missing_count_before": maintenance_meta["summaryMissingCountBefore"],
            "summary_missing_count_after": maintenance_meta["summaryMissingCountAfter"],
            "summary_stale_count_before": maintenance_meta["summaryStaleCountBefore"],
            "summary_stale_count_after": maintenance_meta["summaryStaleCountAfter"],
            "summary_backfilled_count": summary_backfilled_count,
            "summary_llm_candidate_count": summary_llm_candidate_count,
            "legacy_summary_backfilled_count": legacy_summary_backfilled_count,
            "global_quarantined_preference_count": maintenance_meta["globalQuarantinedPreferenceCount"],
            "global_quarantined_knowledge_count": maintenance_meta["globalQuarantinedKnowledgeCount"],
            "knowledge_candidate_count": maintenance_meta["knowledgeCandidateCount"],
            "knowledge_superseded_count": maintenance_meta["knowledgeSupersededCount"],
            "knowledge_merge_suggestion_count": maintenance_meta["knowledgeMergeSuggestionCount"],
            "graph_candidate_count": maintenance_meta["graphCandidateCount"],
            "graph_rewired_relation_count": maintenance_meta["graphRewiredRelationCount"],
            "graph_pruned_isolated_entity_count": maintenance_meta["graphPrunedIsolatedEntityCount"],
            "workflow_candidate_count": maintenance_meta["workflowCandidateCount"],
            "workflow_candidate_updated_count": maintenance_meta["workflowCandidateUpdatedCount"],
            "workflow_active_hint_count": maintenance_meta["workflowActiveHintCount"],
            "workflow_quarantined_count": maintenance_meta["workflowQuarantinedCount"],
            "workflow_merge_suggestion_count": maintenance_meta["workflowMergeSuggestionCount"],
            "touched_refs": touched_refs,
            "legacy_summary_touched_refs": legacy_summary_touched_refs,
            "skipped_targets": skipped_targets,
            "failed_targets": failed_targets,
            "phases": phases,
            "no_op_reason": no_op_reason,
        }
        log_memory_observation(
            "maintenance",
            "WARNING" if failed_targets else ("NO_OP" if result_status == "no_op" else "SUCCESS"),
            trigger=trigger_source,
            runId=run_handle.run_id,
            sessionId=effective_session_id,
            callsLlm=bool(summary_llm_candidate_count),
            targetCount=len(targets),
            summaryLlmCandidateCount=summary_llm_candidate_count,
            summaryBackfilledCount=summary_backfilled_count,
            legacySummaryBackfilledCount=legacy_summary_backfilled_count,
            touchedRefs=touched_refs[:20],
            skippedTargetCount=len(skipped_targets),
            skippedTargets=skipped_targets[:8],
            failedTargetCount=len(failed_targets),
            failedTargets=failed_targets[:8],
            failedReasons=sorted({str(item.get("reason") or "unknown") for item in failed_targets})[:8],
            noOpReason=no_op_reason,
            knowledgeCandidateCount=maintenance_meta["knowledgeCandidateCount"],
            knowledgeSupersededCount=maintenance_meta["knowledgeSupersededCount"],
            knowledgeMergeSuggestionCount=maintenance_meta["knowledgeMergeSuggestionCount"],
            graphCandidateCount=maintenance_meta["graphCandidateCount"],
            graphRewiredRelationCount=maintenance_meta["graphRewiredRelationCount"],
            graphPrunedIsolatedEntityCount=maintenance_meta["graphPrunedIsolatedEntityCount"],
            workflowCandidateCount=maintenance_meta["workflowCandidateCount"],
            workflowCandidateUpdatedCount=maintenance_meta["workflowCandidateUpdatedCount"],
            workflowActiveHintCount=maintenance_meta["workflowActiveHintCount"],
            workflowQuarantinedCount=maintenance_meta["workflowQuarantinedCount"],
            workflowMergeSuggestionCount=maintenance_meta["workflowMergeSuggestionCount"],
            budgetStopped=maintenance_meta["budgetStopped"],
        )
        return self._finalize_run(run_handle, reason="memory_maintenance_completed", result=result)

memory_agent_runner = MemoryAgentRunner()
