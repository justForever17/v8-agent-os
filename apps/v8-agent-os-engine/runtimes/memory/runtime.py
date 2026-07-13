from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Dict, List, Optional

from core.database import db
from erc.kernel import erc_kernel
from erc.run_service import run_service
from erc.runtime_registry import runtime_registry
from erc.workflow_ledger import workflow_ledger_service
from runtimes.memory.health_service import memory_health_service
from runtimes.memory.injection_service import injection_service
from runtimes.memory.knowledge_service import knowledge_service
from runtimes.memory.profile_service import profile_service
from runtimes.memory.recall_service import recall_service
from runtimes.memory.workflow_service import workflow_memory_service
from runtimes.memory.workflow_evidence import workflow_evidence_collector


class MemoryRuntime:
    """统一 Memory Runtime 入口，避免 API / Agent / Runtime 各自直连底层细节。"""
    kind = "memory"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "MemoryRuntime",
            "summary": "负责记忆 provenance、长期记忆提取、时序日志与 RAG 注入；长期记忆由同步运行的 Memory Agent/Memory Runtime 在 on_chat_end、周期维护和显式 memory 任务中写入维护。",
            "responsibilities": [
                "维护分层记忆和知识图谱",
                "执行带 provenance 的记忆提取、聚合与健康检查",
                "向 ChatRuntime 提供上下文注入能力",
            ],
            "routingKeywords": ["记忆", "偏好", "知识", "RAG", "摘要", "图谱"],
            "acceptedInputs": ["session_extraction", "periodic_summary", "memory query"],
            "producedOutputs": ["session context", "knowledge facts", "memory dashboard"],
            "ownedSteps": ["memory.session_extraction", "memory.periodic_summary", "memory.maintenance"],
            "supportsPause": False,
            "supportsResume": False,
            "supportsApproval": False,
            "supportsRepair": False,
            "visibility": "internal",
            "promptHints": [
                "Memory Agent 会在 on_chat_end、周期维护和显式 memory 任务中抽取、写入、维护长期记忆。",
                "Supervisor 默认只查询记忆、消费注入或请求受控维护；不要直接伪写 persistent memory。",
                "memory.maintain 是受管工具组，只有通过 MemoryRuntime/授权路径才能执行维护写入。",
            ],
            "capabilities": [
                {
                    "key": "memory.maintain",
                    "label": "记忆维护与注入",
                    "summary": "负责会话提取、周期摘要和被动 RAG 注入。",
                    "accepts": ["session_id", "query", "task_kind"],
                    "outputs": ["context bundle", "knowledge records"],
                    "examples": ["会话结束提取记忆", "构建上下文注入"],
                    "risk_level": "low",
                },
                {
                    "key": "memory.workflow",
                    "label": "行为链记忆",
                    "summary": "维护可验证、可清洗、可渐进注入的重复动作链提示。",
                    "accepts": ["session evidence", "task query", "candidate edits"],
                    "outputs": ["workflow episodes", "workflow candidates", "workflow hints"],
                    "examples": ["会后提取成功动作链", "夜间整理 golden path", "为相似任务注入下一步提示"],
                    "risk_level": "medium",
                }
            ],
            "metadata": {
                "managedToolNames": [
                    "memory_broker",
                    "memory_recall",
                    "mem_update",
                    "memory_map_expand",
                    "memory_read_day",
                ],
                "managedToolGroups": ["memory.read", "memory.maintain"],
            },
        }

    def _memory_session_id(self, *, task_kind: str, tier: Optional[str] = None) -> str:
        if task_kind == "periodic_summary":
            return f"memory:summary:{tier or 'general'}"
        return f"memory:{task_kind}"

    def _session_title(self, *, task_kind: str, session_id: str, tier: Optional[str] = None) -> str:
        if task_kind == "session_extraction":
            return f"Memory Agent · Session {session_id[:12]}"
        if task_kind == "periodic_summary":
            return f"Memory Agent · {tier or 'general'} Summary"
        return f"Memory Agent · {task_kind}"

    def begin_run(
        self,
        *,
        task_kind: str,
        trigger_source: Optional[str],
        session_id: Optional[str] = None,
        user_id: str = "system",
        metadata: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        tier: Optional[str] = None,
    ):
        effective_session_id = session_id or self._memory_session_id(task_kind=task_kind, tier=tier)
        extra_metadata = dict(metadata or {})
        existing_session = db.get_session(effective_session_id) or {}
        existing_metadata = existing_session.get("metadata")
        if isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except Exception:
                existing_metadata = {}
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}
        db.create_or_update_session(
            session_id=effective_session_id,
            title=existing_session.get("title")
            or self._session_title(task_kind=task_kind, session_id=effective_session_id, tier=tier),
            user_id=user_id,
            metadata={
                **existing_metadata,
                "runtime": "memory",
                "task_kind": task_kind,
                "tier": tier,
                "trigger_source": trigger_source,
                **extra_metadata,
            },
        )
        run_handle = erc_kernel.submit_run(
            session_id=effective_session_id,
            conversation_id=effective_session_id,
            user_id=user_id,
            runtime_kind="memory",
            trigger_source=trigger_source,
            agent_id="memory_agent",
            metadata={
                "runtime": "memory",
                "task_kind": task_kind,
                "tier": tier,
                **extra_metadata,
            },
            run_id=run_id,
            initial_status="queued",
            component="memory_runtime",
            node="maintenance_runner",
        )
        workflow_ledger_service.activate_runtime_step(
            run_handle.run_id,
            owner_runtime="memory",
            step_key=f"memory.{task_kind}",
            title=self._session_title(task_kind=task_kind, session_id=effective_session_id, tier=tier),
            owner_agent_id="memory_agent",
            input_payload={
                "task_kind": task_kind,
                "tier": tier,
                "trigger_source": trigger_source,
            },
        )
        return run_handle

    def attach_run(self, run_id: str):
        return erc_kernel.attach_run(run_id, component="memory_runtime", node="maintenance_runner")

    def update_run_metadata(self, run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return run_service.update_metadata(run_id, updates)

    def get_extraction_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        return db.get_memory_extraction_state(session_id)

    def save_extraction_state(
        self,
        *,
        session_id: str,
        last_processed_message_id: Optional[str],
        last_processed_message_count: int,
        last_content_hash: Optional[str],
        last_run_id: Optional[str],
        last_processed_at: Optional[str] = None,
    ) -> None:
        db.upsert_memory_extraction_state(
            {
                "session_id": session_id,
                "last_processed_message_id": last_processed_message_id,
                "last_processed_message_count": last_processed_message_count,
                "last_content_hash": last_content_hash,
                "last_run_id": last_run_id,
                "last_processed_at": last_processed_at,
            }
        )

    def get_dashboard(self) -> Dict[str, Any]:
        prefs = profile_service.list_preferences()
        scopes = profile_service.list_scopes()
        total_prefs = profile_service.get_preference_count()
        quarantined_global_preferences = profile_service.list_global_preference_quarantine()
        quarantined_global_knowledge = knowledge_service.list_recent_knowledge(scope="global", limit=200, status="quarantined")
        knowledge_count = knowledge_service.get_knowledge_count()
        graph_stats = knowledge_service.get_graph_stats()
        health = memory_health_service.check()
        extraction_runs: List[Dict[str, Any]] = []
        recent_memory_runs = db.list_run_records(run_type="memory", limit=40)
        recent_invocations = db.list_model_invocations(
            capability_class="memory_extraction",
            request_kind="memory_extraction",
            limit=60,
        )
        invocation_by_run_id = {
            str(item.get("run_id") or "").strip(): item
            for item in recent_invocations
            if str(item.get("run_id") or "").strip()
        }
        extraction_summary = {
            "completed": 0,
            "skipped": 0,
            "persisted": 0,
            "policyFiltered": 0,
            "llmResponseEmpty": 0,
            "parserFailed": 0,
            "repairParserFailed": 0,
            "llmInvokeFailed": 0,
            "extractorConfigMissing": 0,
            "duplicateTranscript": 0,
            "duplicateIncrement": 0,
            "noSemanticContent": 0,
        }

        for run in recent_memory_runs:
            metadata = run.get("metadata") or {}
            if str(metadata.get("task_kind") or "").strip().lower() != "session_extraction":
                continue
            extraction_meta = metadata.get("memory_extraction") if isinstance(metadata.get("memory_extraction"), dict) else {}
            no_persisted_reason = str(extraction_meta.get("noPersistedMemoryReason") or "").strip()
            failure_stage = str(extraction_meta.get("extractionFailureStage") or "").strip()
            skip_reason = str(extraction_meta.get("skipReason") or "").strip()
            extraction_mode = str(extraction_meta.get("extractionMode") or "").strip()
            persisted_knowledge = int(extraction_meta.get("persistedKnowledgeCount") or 0)
            persisted_preferences = int(extraction_meta.get("persistedPreferenceCount") or 0)
            invocation = invocation_by_run_id.get(str(run.get("id") or "").strip()) or {}

            if str(run.get("status") or "").strip().lower() == "completed":
                extraction_summary["completed"] += 1
            if str(run.get("status") or "").strip().lower() == "skipped":
                extraction_summary["skipped"] += 1
            if persisted_knowledge > 0 or persisted_preferences > 0:
                extraction_summary["persisted"] += 1
            if no_persisted_reason == "policy_filtered":
                extraction_summary["policyFiltered"] += 1
            if skip_reason == "duplicate_transcript" or extraction_mode == "duplicate_transcript":
                extraction_summary["duplicateTranscript"] += 1
            if skip_reason == "duplicate_increment" or extraction_mode == "duplicate_increment":
                extraction_summary["duplicateIncrement"] += 1
            if skip_reason == "no_semantic_content":
                extraction_summary["noSemanticContent"] += 1
            if failure_stage == "llm_response_empty":
                extraction_summary["llmResponseEmpty"] += 1
            elif failure_stage == "parser_failed":
                extraction_summary["parserFailed"] += 1
            elif failure_stage == "repair_parser_failed":
                extraction_summary["repairParserFailed"] += 1
            elif failure_stage == "llm_invoke_failed":
                extraction_summary["llmInvokeFailed"] += 1
            elif failure_stage == "extractor_config_missing":
                extraction_summary["extractorConfigMissing"] += 1

            extraction_runs.append(
                {
                    "runId": run.get("id"),
                    "sessionId": run.get("session_id"),
                    "status": run.get("status"),
                    "startedAt": run.get("started_at"),
                    "finishedAt": run.get("finished_at"),
                    "triggerSource": run.get("trigger_source"),
                    "extractorModel": extraction_meta.get("extractorModel") or invocation.get("model_id"),
                    "extractionFailureStage": failure_stage or None,
                    "extractionFailureReason": extraction_meta.get("extractionFailureReason"),
                    "skipReason": skip_reason or None,
                    "extractionMode": extraction_mode or None,
                    "transcriptSource": extraction_meta.get("transcriptSource"),
                    "latestSeq": extraction_meta.get("latestSeq"),
                    "rawOutputPreview": extraction_meta.get("rawOutputPreview"),
                    "parserErrorPreview": extraction_meta.get("parserErrorPreview"),
                    "summary": extraction_meta.get("summary"),
                    "resolvedScope": extraction_meta.get("resolvedScope"),
                    "effectiveMemoryScope": extraction_meta.get("effectiveMemoryScope"),
                    "memoryPolicy": extraction_meta.get("memoryPolicy"),
                    "provenanceClass": extraction_meta.get("provenanceClass"),
                    "noPersistedMemoryReason": no_persisted_reason or None,
                    "extractedPreferenceCount": int(extraction_meta.get("extractedPreferenceCount") or 0),
                    "extractedKnowledgeCount": int(extraction_meta.get("extractedKnowledgeCount") or 0),
                    "persistedPreferenceCount": persisted_preferences,
                    "persistedKnowledgeCount": persisted_knowledge,
                    "persistedRelationCount": int(extraction_meta.get("persistedRelationCount") or 0),
                    "filterReasons": extraction_meta.get("filterReasons") or {},
                    "invocationStatus": invocation.get("status"),
                    "invocationError": invocation.get("error_message"),
                }
            )
            if len(extraction_runs) >= 12:
                break
        return {
            "preferences": {
                "scopes": scopes,
                "total": total_prefs,
                "data": prefs,
                "globalQuarantineCount": len(quarantined_global_preferences),
                "canonicalLongTermScopes": [
                    "global",
                    "project:{id}",
                    "channel:{type}:{remote_id}",
                ],
            },
            "knowledge": {
                "count": knowledge_count,
                "globalQuarantineCount": len(quarantined_global_knowledge),
            },
            "graph": graph_stats,
            "health": health,
            "memoryMap": health.get("memoryMap") or {},
            "extractions": {
                "recent": extraction_runs,
                "summary": extraction_summary,
            },
            "maintenance": self._build_maintenance_dashboard(recent_memory_runs),
            "workflows": workflow_memory_service.dashboard_summary(),
            "provenance": {
                "classes": [
                    "human_dialogue",
                    "assistant_final",
                    "assistant_reasoning_summary",
                    "tool_semantic_result",
                    "governance_or_control",
                    "mechanical_automation",
                    "machine_observation",
                ],
                "policies": ["durable", "daily_summary_only", "skipped"],
            },
        }

    def clear_diagnostics(self) -> Dict[str, Any]:
        return db.clear_memory_runtime_diagnostics()

    def _build_maintenance_dashboard(self, recent_memory_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
        recent_runs: List[Dict[str, Any]] = []
        summary = {
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "summaryMissing": 0,
            "summaryBackfilled": 0,
            "summarySkipped": 0,
            "knowledgeCandidates": 0,
            "knowledgeSuperseded": 0,
            "knowledgeMergeSuggestions": 0,
            "graphCandidates": 0,
            "graphRewiredRelations": 0,
            "graphPrunedEntities": 0,
            "workflowCandidates": 0,
            "workflowActivated": 0,
            "workflowQuarantined": 0,
            "workflowMergeSuggestions": 0,
            "budgetStopped": 0,
        }

        for run in recent_memory_runs:
            metadata = run.get("metadata") or {}
            if str(metadata.get("task_kind") or "").strip().lower() != "maintenance":
                continue
            maintenance_meta = metadata.get("memory_maintenance") if isinstance(metadata.get("memory_maintenance"), dict) else {}
            status = str(run.get("status") or "").strip().lower()
            if status == "completed":
                summary["completed"] += 1
            elif status == "failed":
                summary["failed"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            summary["summaryBackfilled"] += int(maintenance_meta.get("summaryBackfilledCount") or 0)
            summary["summaryMissing"] += int(maintenance_meta.get("summaryMissingCountBefore") or 0)
            summary["summarySkipped"] += int(maintenance_meta.get("skippedTargetCount") or 0)
            summary["knowledgeCandidates"] += int(maintenance_meta.get("knowledgeCandidateCount") or 0)
            summary["knowledgeSuperseded"] += int(maintenance_meta.get("knowledgeSupersededCount") or 0)
            summary["knowledgeMergeSuggestions"] += int(maintenance_meta.get("knowledgeMergeSuggestionCount") or 0)
            summary["graphCandidates"] += int(maintenance_meta.get("graphCandidateCount") or 0)
            summary["graphRewiredRelations"] += int(maintenance_meta.get("graphRewiredRelationCount") or 0)
            summary["graphPrunedEntities"] += int(maintenance_meta.get("graphPrunedIsolatedEntityCount") or 0)
            summary["workflowCandidates"] += int(maintenance_meta.get("workflowCandidateCount") or 0)
            summary["workflowActivated"] += int(maintenance_meta.get("workflowActiveHintCount") or 0)
            summary["workflowQuarantined"] += int(maintenance_meta.get("workflowQuarantinedCount") or 0)
            summary["workflowMergeSuggestions"] += int(maintenance_meta.get("workflowMergeSuggestionCount") or 0)
            summary["budgetStopped"] += 1 if maintenance_meta.get("budgetStopped") else 0
            recent_runs.append(
                {
                    "runId": run.get("id"),
                    "status": run.get("status"),
                    "startedAt": run.get("started_at"),
                    "finishedAt": run.get("finished_at"),
                    "triggerSource": run.get("trigger_source"),
                    "summaryMissingCountBefore": int(maintenance_meta.get("summaryMissingCountBefore") or 0),
                    "summaryMissingCountAfter": int(maintenance_meta.get("summaryMissingCountAfter") or 0),
                    "summaryBackfilledCount": int(maintenance_meta.get("summaryBackfilledCount") or 0),
                    "skippedTargetCount": int(maintenance_meta.get("skippedTargetCount") or 0),
                    "summaryStaleCountBefore": int(maintenance_meta.get("summaryStaleCountBefore") or 0),
                    "summaryStaleCountAfter": int(maintenance_meta.get("summaryStaleCountAfter") or 0),
                    "knowledgeCandidateCount": int(maintenance_meta.get("knowledgeCandidateCount") or 0),
                    "knowledgeSupersededCount": int(maintenance_meta.get("knowledgeSupersededCount") or 0),
                    "knowledgeMergeSuggestionCount": int(maintenance_meta.get("knowledgeMergeSuggestionCount") or 0),
                    "graphCandidateCount": int(maintenance_meta.get("graphCandidateCount") or 0),
                    "graphRewiredRelationCount": int(maintenance_meta.get("graphRewiredRelationCount") or 0),
                    "graphOrphanedRelationCount": int(maintenance_meta.get("graphOrphanedRelationCount") or 0),
                    "graphPrunedIsolatedEntityCount": int(maintenance_meta.get("graphPrunedIsolatedEntityCount") or 0),
                    "workflowCandidateCount": int(maintenance_meta.get("workflowCandidateCount") or 0),
                    "workflowCandidateUpdatedCount": int(maintenance_meta.get("workflowCandidateUpdatedCount") or 0),
                    "workflowActiveHintCount": int(maintenance_meta.get("workflowActiveHintCount") or 0),
                    "workflowQuarantinedCount": int(maintenance_meta.get("workflowQuarantinedCount") or 0),
                    "workflowMergeSuggestionCount": int(maintenance_meta.get("workflowMergeSuggestionCount") or 0),
                    "budgetStopped": bool(maintenance_meta.get("budgetStopped")),
                    "noOpReason": maintenance_meta.get("noOpReason"),
                    "touchedRefs": maintenance_meta.get("touchedRefs") or [],
                    "resultReason": maintenance_meta.get("resultReason"),
                }
            )
            if len(recent_runs) >= 8:
                break
        return {"summary": summary, "recent": recent_runs}

    def list_preferences(self) -> Dict[str, Dict[str, str]]:
        return profile_service.list_preferences()

    def get_preference_summary(self) -> Dict[str, Any]:
        return {
            "preferences": profile_service.list_preferences(),
            "scopes": profile_service.list_scopes(),
            "total": profile_service.get_preference_count(),
            "globalQuarantine": profile_service.list_global_preference_quarantine(),
            "globalProfile": profile_service.get_global_profile_schema(),
        }

    def load_preferences(
        self,
        *,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        return profile_service.load_preferences(scope=scope, scope_chain=scope_chain)

    def upsert_preference(self, *, key: str, value: str, scope: str = "global", source: str = "human_admin") -> None:
        profile_service.update_preference(key=key, value=value, scope=scope, source=source)

    def delete_preference(self, *, key: str, scope: str = "global") -> bool:
        return profile_service.delete_preference(key=key, scope=scope)

    def restore_global_preference_quarantine(self, *, record_id: str) -> Optional[Dict[str, object]]:
        return profile_service.restore_global_preference_quarantine(record_id=record_id)

    def delete_global_preference_quarantine(self, *, record_id: str) -> bool:
        return profile_service.delete_global_preference_quarantine(record_id=record_id)

    def list_knowledge(
        self,
        *,
        scope: Optional[str] = None,
        limit: int = 50,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        return knowledge_service.list_recent_knowledge(scope=scope, limit=limit, status=status)

    def get_knowledge_count(self) -> int:
        return knowledge_service.get_knowledge_count()

    def search_full_text(self, *, query: str, scope: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        return knowledge_service.search_full_text(query=query, scope=scope, limit=limit)

    def query_knowledge(
        self,
        *,
        query: Optional[str] = None,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return knowledge_service.query_knowledge(
            query=query,
            scope=scope,
            scopes=scopes,
            category=category,
            limit=limit,
        )

    def add_knowledge(
        self,
        *,
        fact: str,
        category: str = "general",
        scope: str = "global",
        source_session: Optional[str] = None,
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
        importance: int = 50,
        durability: str = "operational",
        source_run: Optional[str] = None,
        source_message_ids: Optional[List[str]] = None,
        transcript_hash: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> str:
        return knowledge_service.add_knowledge(
            fact=fact,
            category=category,
            scope=scope,
            source_session=source_session,
            maintainer_source=maintainer_source,
            confidence=confidence,
            importance=importance,
            durability=durability,
            source_run=source_run,
            source_message_ids=source_message_ids,
            transcript_hash=transcript_hash,
            evidence_refs=evidence_refs,
        )

    def write_knowledge(
        self,
        *,
        fact: str,
        category: str = "general",
        scope: str = "global",
        relation: str = "new",
        target_fact_id: Optional[str] = None,
        source_session: Optional[str] = None,
        source_run: Optional[str] = None,
        source_message_ids: Optional[List[str]] = None,
        transcript_hash: Optional[str] = None,
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
        importance: int = 50,
        durability: str = "operational",
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, object]:
        return knowledge_service.write_knowledge(
            fact=fact,
            category=category,
            scope=scope,
            relation=relation,
            target_fact_id=target_fact_id,
            source_session=source_session,
            source_run=source_run,
            source_message_ids=source_message_ids,
            transcript_hash=transcript_hash,
            maintainer_source=maintainer_source,
            confidence=confidence,
            importance=importance,
            durability=durability,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def update_knowledge(
        self,
        *,
        fact_id: str,
        new_fact: str,
        category: Optional[str] = None,
        scope: Optional[str] = None,
        maintainer_source: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> bool:
        return knowledge_service.update_knowledge(
            fact_id=fact_id,
            new_fact=new_fact,
            category=category,
            scope=scope,
            maintainer_source=maintainer_source,
            confidence=confidence,
        )

    def delete_knowledge(
        self,
        *,
        fact_id: str,
        actor: str = "human_admin",
        reason: str = "manual_delete",
        evidence_refs: Optional[List[str]] = None,
    ) -> bool:
        return knowledge_service.delete_knowledge(
            fact_id=fact_id,
            actor=actor,
            reason=reason,
            evidence_refs=evidence_refs,
        )

    def mark_knowledge_injected(self, *, fact_ids: List[str], verified: bool = False) -> int:
        return knowledge_service.mark_knowledge_injected(fact_ids=fact_ids, verified=verified)

    def create_knowledge_cleanup_plan(
        self,
        *,
        unused_days: int = 180,
        low_evidence_confidence: float = 0.55,
        max_candidates: int = 1000,
    ) -> Dict[str, object]:
        return knowledge_service.create_cleanup_plan(
            unused_days=unused_days,
            low_evidence_confidence=low_evidence_confidence,
            max_candidates=max_candidates,
        )

    def restore_knowledge(self, *, fact_id: str) -> bool:
        return knowledge_service.restore_knowledge(fact_id=fact_id)

    def revalidate_knowledge(self, *, fact_id: str, maintainer_source: str = "human_admin") -> bool:
        return knowledge_service.revalidate_knowledge(fact_id=fact_id, maintainer_source=maintainer_source)

    def get_graph_stats(self) -> Dict[str, Any]:
        return knowledge_service.get_graph_stats()

    def get_full_graph(self, *, limit: int = 100) -> Dict[str, Any]:
        return knowledge_service.get_full_graph(limit=limit)

    def get_projection_health(self) -> Dict[str, Any]:
        return knowledge_service.get_projection_health()

    def list_knowledge_resolution_candidates(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        return knowledge_service.list_resolution_candidates(limit=limit)

    def resolve_knowledge_candidate(self, *, candidate_id: str, resolution: str) -> Dict[str, object]:
        return knowledge_service.resolve_candidate(candidate_id=candidate_id, resolution=resolution)

    def query_entity(self, *, entity: str, scopes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return knowledge_service.query_entity(entity=entity, scopes=scopes)

    def query_multi_hop(
        self,
        *,
        entity: str,
        hops: int = 2,
        scopes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return knowledge_service.query_multi_hop(entity=entity, hops=hops, scopes=scopes)

    def search_entities(self, *, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        return knowledge_service.search_entities(keyword=keyword, limit=limit)

    def add_entity(
        self,
        *,
        name: str,
        entity_type: str = "concept",
        maintainer_source: str = "memory_runtime",
        confidence: float = 1.0,
    ) -> None:
        knowledge_service.add_entity(
            name=name,
            entity_type=entity_type,
            maintainer_source=maintainer_source,
            confidence=confidence,
        )

    def delete_entity(self, *, name: str, scope: Optional[str] = None) -> bool:
        return knowledge_service.delete_entity(name=name, scope=scope)

    def add_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_name: str,
        scope: str,
        source_fact_ids: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        confidence: float = 1.0,
        maintainer_source: str = "memory_runtime",
    ) -> None:
        knowledge_service.add_relation(
            subject=subject,
            predicate=predicate,
            object_name=object_name,
            scope=scope,
            source_fact_ids=source_fact_ids,
            evidence_refs=evidence_refs,
            confidence=confidence,
            maintainer_source=maintainer_source,
        )

    def delete_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_name: str,
        scope: Optional[str] = None,
    ) -> bool:
        return knowledge_service.delete_relation(
            subject=subject,
            predicate=predicate,
            object_name=object_name,
            scope=scope,
        )

    def build_session_context(
        self,
        *,
        user_query: str,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        suppress_daily_memory: bool = False,
        suppress_memory_map: bool = False,
        target_role: str = "supervisor",
    ) -> str:
        return injection_service.build_session_context(
            user_query=user_query,
            scope=scope,
            scope_chain=scope_chain,
            session_id=session_id,
            run_id=run_id,
            suppress_daily_memory=suppress_daily_memory,
            suppress_memory_map=suppress_memory_map,
            target_role=target_role,
        )

    def build_memory_injection_pack(
        self,
        *,
        user_query: str,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        target_role: str = "supervisor",
        latency_tier: str = "balanced",
        visual_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return injection_service.build_memory_injection_pack(
            user_query=user_query,
            scope=scope,
            scope_chain=scope_chain,
            session_id=session_id,
            run_id=run_id,
            target_role=target_role,
            latency_tier=latency_tier,
            visual_evidence=visual_evidence,
        )

    def record_workflow_episode(
        self,
        *,
        payload: Dict[str, Any],
        session_id: str,
        run_id: Optional[str] = None,
        scope: str = "global",
        extraction_source: str = "memory_agent",
    ) -> Dict[str, Any]:
        episode = workflow_memory_service.normalize_episode_payload(
            payload,
            session_id=session_id,
            run_id=run_id,
            scope=scope,
            extraction_source=extraction_source,
        )
        return workflow_memory_service.add_episode(episode)

    def collect_workflow_evidence(
        self,
        *,
        session_id: str,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return workflow_evidence_collector.collect_session(session_id=session_id, run_id=run_id)

    def run_workflow_maintenance(self) -> Dict[str, Any]:
        return workflow_memory_service.maintenance_consolidate()

    def build_workflow_hints_block(
        self,
        *,
        query: str,
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        return workflow_memory_service.build_hints_block(
            query=query,
            scope_chain=scope_chain,
            session_id=session_id,
            run_id=run_id,
        )

    def list_workflow_candidates(
        self,
        *,
        status: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 50,
        workflow_class: Optional[str] = None,
        proof_backed: Optional[bool] = None,
        verification_status: Optional[str] = None,
        source_runtime: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return workflow_memory_service.list_candidates(
            status=status,
            query=query,
            limit=limit,
            workflow_class=workflow_class,
            proof_backed=proof_backed,
            verification_status=verification_status,
            source_runtime=source_runtime,
        )

    def get_workflow_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        return workflow_memory_service.get_candidate(candidate_id)

    def update_workflow_candidate(self, candidate_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        return workflow_memory_service.update_candidate(candidate_id, updates)

    def delete_workflow_candidate(self, candidate_id: str) -> bool:
        return workflow_memory_service.delete_candidate(candidate_id)

    def merge_workflow_candidates(self, *, target_id: str, source_ids: List[str]) -> Dict[str, Any]:
        return workflow_memory_service.merge_candidates(target_id, source_ids)

    def list_workflow_episodes(
        self,
        *,
        candidate_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return workflow_memory_service.list_episodes(candidate_id=candidate_id, session_id=session_id, limit=limit)

    def list_workflow_hint_events(
        self,
        *,
        candidate_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return workflow_memory_service.list_hint_events(candidate_id=candidate_id, limit=limit)

    def record_workflow_hint_event(
        self,
        *,
        candidate_id: str,
        query: str,
        hint: Dict[str, Any],
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        outcome: str = "injected",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return workflow_memory_service.record_hint_event(
            candidate_id=candidate_id,
            query=query,
            hint=hint,
            session_id=session_id,
            run_id=run_id,
            outcome=outcome,
            metadata=metadata,
        )

    def get_recent_logs(self, *, days: int = 2, scope_chain: Optional[List[str]] = None) -> str:
        return injection_service.get_recent_logs(days=days, scope_chain=scope_chain)

    def read_memory_summary(self, *, tier: str, date_str: Optional[str] = None) -> str:
        return injection_service.read_memory_summary(tier=tier, date_str=date_str)

    def build_memory_map(self, *, anchor_date: Optional[str] = None) -> Dict[str, Any]:
        return injection_service.build_memory_map(anchor_date=anchor_date)

    def expand_memory_map(self, *, memory_ref: str) -> Dict[str, Any]:
        return injection_service.expand_memory_map(memory_ref=memory_ref)

    def read_memory_day(self, *, memory_ref_or_date: str) -> str:
        return injection_service.read_memory_day(memory_ref_or_date=memory_ref_or_date)

    def get_memory_map_health(self) -> Dict[str, Any]:
        return injection_service.get_memory_map_health()

    def list_summary_targets(self, *, states: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return injection_service.list_summary_targets(states=states)

    def get_logs_for_period(self, *, tier: str, dt: Optional[datetime] = None, scope_chain: Optional[List[str]] = None) -> str:
        return injection_service.get_logs_for_period(tier=tier, dt=dt, scope_chain=scope_chain)

    def save_periodic_summary(
        self,
        *,
        tier: str,
        payload: dict,
        dt: Optional[datetime] = None,
    ) -> None:
        injection_service.save_periodic_summary(tier=tier, payload=payload, dt=dt)

    def backfill_periodic_summaries(self) -> dict:
        return injection_service.backfill_periodic_summaries()

    def append_daily_log(self, *, content: str, tags: Optional[List[str]] = None) -> None:
        injection_service.append_daily_log(content=content, tags=tags)

    def append_daily_log_with_yaml(
        self,
        *,
        content: str,
        session_summary: str,
        session_tags: List[str],
        entry_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        injection_service.append_daily_log_with_yaml(
            content=content,
            session_summary=session_summary,
            session_tags=session_tags,
            entry_metadata=entry_metadata,
        )

    def unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return recall_service.unified_recall(query=query, limit=limit, scope=scope, scopes=scopes)

    def preview_unified_recall(
        self,
        *,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return recall_service.preview_unified_recall(query=query, limit=limit, scope=scope, scopes=scopes)

    def health_check(self) -> Dict[str, Any]:
        return memory_health_service.check()

    def list_artifacts(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return db.list_runtime_artifacts(session_id=session_id, run_id=run_id, limit=limit)

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return db.get_runtime_artifact(artifact_id)


memory_runtime = runtime_registry.register(MemoryRuntime())
