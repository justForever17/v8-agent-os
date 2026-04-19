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
from runtimes.memory.project_registry import project_registry_service
from runtimes.memory.recall_service import recall_service


class MemoryRuntime:
    """统一 Memory Runtime 入口，避免 API / Agent / Runtime 各自直连底层细节。"""
    kind = "memory"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "MemoryRuntime",
            "summary": "负责记忆 provenance、长期记忆提取、时序日志与 RAG 注入，不承担通用对话编排。",
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
                "需要写入或维护记忆时，交给 MemoryRuntime；不要让 Supervisor 自己承担脏数据写入。",
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
                }
            ],
            "metadata": {
                "managedToolNames": [
                    "memory_recall",
                    "mem_delete",
                    "mem_update",
                    "memory_map_expand",
                    "memory_read_day",
                ],
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
        knowledge_count = knowledge_service.get_knowledge_count()
        graph_stats = knowledge_service.get_graph_stats()
        recent_logs = injection_service.get_recent_logs(days=3)
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
                "canonicalLongTermScopes": [
                    "global",
                    "project:{id}",
                    "channel:{type}:{remote_id}",
                ],
            },
            "knowledge": {
                "count": knowledge_count,
            },
            "graph": graph_stats,
            "recent_logs": recent_logs,
            "health": health,
            "memoryMap": health.get("memoryMap") or {},
            "extractions": {
                "recent": extraction_runs,
                "summary": extraction_summary,
            },
            "maintenance": self._build_maintenance_dashboard(recent_memory_runs),
            "projects": {
                "count": len(project_registry_service.list_projects()),
            },
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
                    "summaryStaleCountBefore": int(maintenance_meta.get("summaryStaleCountBefore") or 0),
                    "summaryStaleCountAfter": int(maintenance_meta.get("summaryStaleCountAfter") or 0),
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
        }

    def load_preferences(
        self,
        *,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        return profile_service.load_preferences(scope=scope, scope_chain=scope_chain)

    def upsert_preference(self, *, key: str, value: str, scope: str = "global") -> None:
        profile_service.update_preference(key=key, value=value, scope=scope)

    def delete_preference(self, *, key: str, scope: str = "global") -> bool:
        return profile_service.delete_preference(key=key, scope=scope)

    def list_knowledge(self, *, scope: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return knowledge_service.list_recent_knowledge(scope=scope, limit=limit)

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
    ) -> str:
        return knowledge_service.add_knowledge(
            fact=fact,
            category=category,
            scope=scope,
            source_session=source_session,
        )

    def update_knowledge(
        self,
        *,
        fact_id: str,
        new_fact: str,
        category: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> bool:
        return knowledge_service.update_knowledge(
            fact_id=fact_id,
            new_fact=new_fact,
            category=category,
            scope=scope,
        )

    def delete_knowledge(self, *, fact_id: str) -> bool:
        return knowledge_service.delete_knowledge(fact_id=fact_id)

    def get_graph_stats(self) -> Dict[str, Any]:
        return knowledge_service.get_graph_stats()

    def get_full_graph(self, *, limit: int = 100) -> Dict[str, Any]:
        return knowledge_service.get_full_graph(limit=limit)

    def query_entity(self, *, entity: str) -> List[Dict[str, Any]]:
        return knowledge_service.query_entity(entity=entity)

    def query_multi_hop(self, *, entity: str, hops: int = 2) -> List[Dict[str, Any]]:
        return knowledge_service.query_multi_hop(entity=entity, hops=hops)

    def search_entities(self, *, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        return knowledge_service.search_entities(keyword=keyword, limit=limit)

    def add_entity(self, *, name: str, entity_type: str = "concept") -> None:
        knowledge_service.add_entity(name=name, entity_type=entity_type)

    def delete_entity(self, *, name: str) -> bool:
        return knowledge_service.delete_entity(name=name)

    def add_relation(
        self,
        *,
        subject: str,
        predicate: str,
        object_name: str,
        confidence: float = 1.0,
    ) -> None:
        knowledge_service.add_relation(
            subject=subject,
            predicate=predicate,
            object_name=object_name,
            confidence=confidence,
        )

    def delete_relation(self, *, subject: str, predicate: str, object_name: str) -> bool:
        return knowledge_service.delete_relation(subject=subject, predicate=predicate, object_name=object_name)

    def build_session_context(
        self,
        *,
        user_query: str,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        return injection_service.build_session_context(
            user_query=user_query,
            scope=scope,
            scope_chain=scope_chain,
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
