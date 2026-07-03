from __future__ import annotations

import json
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import Mock, patch

from core import memory_store as memory_store_module
from core.knowledge_db import KnowledgeDB
from core.memory_canonicalization import (
    canonicalize_knowledge_category,
    canonicalize_memory_extraction_result,
    canonicalize_preference_key,
)
from runtimes.memory.workflow_service import workflow_memory_service
from runtimes.network_supervisor.memory_adapter import network_supervisor_memory_adapter


@contextmanager
def isolated_memory_store() -> Iterator[memory_store_module.MemoryStore]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        memory_root = temp_path / "memory"
        with patch.object(memory_store_module, "CONFIG_DIR", temp_path), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ):
            yield memory_store_module.MemoryStore()


@contextmanager
def isolated_graph_store(memory_config: dict[str, Any] | None = None) -> Iterator[memory_store_module.MemoryStore]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        memory_root = temp_path / "memory"
        knowledge_db = KnowledgeDB(db_path=temp_path / "knowledge.db")
        config = {
            "recall_strategy": "keyword",
            "fts_enabled": True,
            "graph_enabled": True,
            "retrieval_threshold": 0.05,
            "recall_top_k": 5,
            "max_context_tokens": 4000,
            "passive_summary_enabled": False,
            "passive_memory_map_enabled": False,
            "passive_recent_activity_teaser_enabled": False,
            "passive_knowledge_graph_summary_enabled": True,
            "passive_knowledge_graph_summary_max_relations": 5,
            "passive_knowledge_graph_summary_max_chars": 720,
        }
        config.update(memory_config or {})
        with patch.object(memory_store_module, "CONFIG_DIR", temp_path), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ), patch("core.knowledge_db.knowledge_db", knowledge_db), patch(
            "core.storage.storage.get_memory_config",
            return_value=config,
        ):
            yield memory_store_module.MemoryStore()


def seed_graph_fixture(knowledge_db: KnowledgeDB, *, scope: str = "global", fact_id: str = "fact_graph_seed") -> None:
    knowledge_db.add_knowledge(
        fact_id,
        "memory graph improves contextual recall for supervisor context",
        category="runtime_governance",
        scope=scope,
    )
    knowledge_db.add_relation("memory", "RELATED_TO", "graph", source_fact_id=fact_id)
    knowledge_db.add_relation("graph", "SUPPORTS", "contextual recall", source_fact_id=fact_id)
    knowledge_db.add_relation("contextual recall", "SUPPORTS", "supervisor context", source_fact_id=fact_id)


def graph_summary_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        memory_root = temp_path / "memory"
        knowledge_db = KnowledgeDB(db_path=temp_path / "knowledge.db")
        seed_graph_fixture(knowledge_db)
        with patch.object(memory_store_module, "CONFIG_DIR", temp_path), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ), patch("core.knowledge_db.knowledge_db", knowledge_db), patch(
            "core.storage.storage.get_memory_config",
            return_value={
                "recall_strategy": "keyword",
                "fts_enabled": True,
                "graph_enabled": True,
                "retrieval_threshold": 0.05,
                "recall_top_k": 5,
                "max_context_tokens": 4000,
                "passive_summary_enabled": False,
                "passive_memory_map_enabled": False,
                "passive_recent_activity_teaser_enabled": False,
                "passive_knowledge_graph_summary_enabled": True,
            },
        ):
            store = memory_store_module.MemoryStore()
            context = store.build_session_context(
                user_query="memory graph contextual recall",
                scope="global",
                scope_chain=["global"],
            )
            diagnostics = dict(store._last_session_context_diagnostics)
    passed = "[KNOWLEDGE GRAPH SUMMARY]" in context and "contextual recall" in context
    return {
        "id": "graph_summary_injection",
        "status": "pass" if passed else "fail",
        "details": {
            "contextPreview": context[:800],
            "diagnostics": diagnostics,
        },
    }


def graph_scope_isolation_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        memory_root = temp_path / "memory"
        knowledge_db = KnowledgeDB(db_path=temp_path / "knowledge.db")
        seed_graph_fixture(knowledge_db, scope="project:a", fact_id="fact_project_a_graph")
        with patch.object(memory_store_module, "CONFIG_DIR", temp_path), patch.object(
            memory_store_module,
            "MEMORY_ROOT",
            memory_root,
        ), patch("core.knowledge_db.knowledge_db", knowledge_db), patch(
            "core.storage.storage.get_memory_config",
            return_value={
                "recall_strategy": "keyword",
                "fts_enabled": True,
                "graph_enabled": True,
                "retrieval_threshold": 0.05,
                "recall_top_k": 5,
                "max_context_tokens": 4000,
                "passive_summary_enabled": False,
                "passive_memory_map_enabled": False,
                "passive_recent_activity_teaser_enabled": False,
                "passive_knowledge_graph_summary_enabled": True,
            },
        ):
            store = memory_store_module.MemoryStore()
            context = store.build_session_context(
                user_query="memory graph contextual recall",
                scope="project:b",
                scope_chain=["global", "project:b"],
            )
            diagnostics = dict(store._last_session_context_diagnostics)
    passed = "[KNOWLEDGE GRAPH SUMMARY]" not in context and not diagnostics.get("graphSummaryInjected")
    return {
        "id": "graph_scope_isolation",
        "status": "pass" if passed else "fail",
        "details": {
            "contextPreview": context[:800],
            "diagnostics": diagnostics,
        },
    }


def summary_contamination_case() -> dict[str, Any]:
    with isolated_memory_store() as store:
        store.update_preference("favorite_shoe_brand", "阿迪达斯", scope="workspace:main")
        store.update_preference("shoe_brand_preference", "耐克", scope="workspace:main")
        note, diagnostics = store._build_memory_consistency_note_for_injection(
            active_preferences=store.load_preferences(
                scope="workspace:main",
                scope_chain=["global", "workspace:main"],
            ),
            passive_text="1 月用户说他喜欢阿迪达斯鞋。",
        )
    passed = "MEMORY CONSISTENCY NOTE" in note and "耐克" in note and diagnostics.get("consistencyNoteInjected")
    return {
        "id": "summary_contamination_audit",
        "status": "pass" if passed else "fail",
        "details": {
            "note": note,
            "diagnostics": diagnostics,
        },
    }


def canonical_registry_case() -> dict[str, Any]:
    result = SimpleNamespace(
        preferences=[
            SimpleNamespace(scope="workspace:main", key="preferred_ide", value="Cursor"),
            SimpleNamespace(scope="workspace:main", key="editor_preference", value="VS Code"),
            SimpleNamespace(scope="workspace:main", key="model_provider_preference", value="OpenAI"),
        ],
        knowledge=[
            SimpleNamespace(scope="workspace:main", category="repo convention", fact="Use pnpm."),
            SimpleNamespace(scope="workspace:main", category="testing", fact="Run pytest."),
        ],
    )
    diagnostics = canonicalize_memory_extraction_result(result)
    keys = [item.key for item in result.preferences]
    categories = [item.category for item in result.knowledge]
    passed = (
        canonicalize_preference_key("shoe_brand_preference") == "favorite_shoe_brand"
        and keys == ["preferred_editor", "preferred_ai_provider"]
        and canonicalize_knowledge_category("api integration") == "api_integration"
        and categories == ["project_convention", "testing_strategy"]
    )
    return {
        "id": "canonical_registry_expansion",
        "status": "pass" if passed else "fail",
        "details": {
            "keys": keys,
            "categories": categories,
            "diagnostics": diagnostics,
        },
    }


def external_api_isolation_case() -> dict[str, Any]:
    memory_runtime = SimpleNamespace(add_knowledge=Mock(return_value="fact_external_eval"))
    run_service = SimpleNamespace(update_metadata=Mock())
    db = SimpleNamespace(add_runtime_event=Mock(), get_next_runtime_seq=Mock(return_value=1))
    with patch("runtimes.network_supervisor.memory_adapter.memory_runtime", memory_runtime), patch(
        "runtimes.network_supervisor.memory_adapter.run_service",
        run_service,
    ), patch("runtimes.network_supervisor.memory_adapter.db", db):
        result_a = network_supervisor_memory_adapter.record_openai_compat_delta(
            payload={"messages": [{"role": "user", "content": "请记住：thread A 喜欢简短回答。"}]},
            chat_request=SimpleNamespace(session_id="sess_ext_a", config=SimpleNamespace(external_tools=[])),
            run_id="run_ext_a",
            events=[{"type": "text_chunk", "content": "已记录"}, {"type": "done", "status": "completed"}],
            response_payload={"choices": [{"finish_reason": "stop"}]},
            external_thread_id="thread-a",
            external_user_id="user-a",
            allow_persist=True,
        )
        _, kwargs_a = memory_runtime.add_knowledge.call_args
        memory_runtime.add_knowledge.reset_mock()
        result_b = network_supervisor_memory_adapter.record_openai_compat_delta(
            payload={"messages": [{"role": "user", "content": "请记住：thread B 喜欢详细回答。"}]},
            chat_request=SimpleNamespace(session_id="sess_ext_b", config=SimpleNamespace(external_tools=[])),
            run_id="run_ext_b",
            events=[{"type": "text_chunk", "content": "已记录"}, {"type": "done", "status": "completed"}],
            response_payload={"choices": [{"finish_reason": "stop"}]},
            external_thread_id="thread-b",
            external_user_id="user-b",
            allow_persist=True,
        )
        _, kwargs_b = memory_runtime.add_knowledge.call_args
    passed = (
        result_a.get("resolvedScope") == "external_api_thread:thread-a"
        and result_b.get("resolvedScope") == "external_api_thread:thread-b"
        and kwargs_a.get("scope") == "external_api_thread:thread-a"
        and kwargs_b.get("scope") == "external_api_thread:thread-b"
    )
    return {
        "id": "external_api_thread_isolation",
        "status": "pass" if passed else "fail",
        "details": {
            "resultA": result_a,
            "resultB": result_b,
            "scopeA": kwargs_a.get("scope"),
            "scopeB": kwargs_b.get("scope"),
        },
    }


def _workflow_test_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "hintInjectionEnabled": True,
        "progressiveHintsEnabled": True,
        "minSuccessCount": 1,
        "errorfulSuccessRequiresUserAcceptance": True,
        "maxInjectedHints": 2,
        "maxHintChars": 1200,
        "maxActiveWorkflowGuidesPerRun": 1,
        "quarantineOnNegativeFeedback": True,
        "requireApprovalForSideEffects": True,
        "riskTierActivationPolicy": {
            "read_only": "auto",
            "low": "auto",
            "medium": "approval",
            "high": "approval",
            "critical": "quarantine",
        },
        "engineering": {
            "enabled": True,
            "extractFromProofLedger": True,
            "requireEngineeringModeForInjection": True,
            "requireVerifiedProofForActivation": True,
            "learnFailedVerificationAsAntiPattern": True,
            "minVerifiedSuccessCount": 1,
        },
    }


def _proof_entry(*, proof_id: str, verification_status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = {
        "id": proof_id,
        "mode": "auto",
        "patchIntent": "Verify engineering workflow learning gates",
        "verificationStatus": verification_status,
        "changedFiles": ["apps/v8-agent-os-engine/core/memory_store.py"],
        "writeSet": ["apps/v8-agent-os-engine/core/"],
        "commands": [
            {
                "tool": "run_system_command",
                "command": "pytest tests/evals",
                "returnCode": 0 if verification_status == "verified" else 1 if verification_status == "failed_verification" else None,
                "isValidation": True,
                "summary": "evals passed" if verification_status == "verified" else "evals failed",
            }
        ],
        "diagnostics": {
            "items": [],
            "worksetCorrelation": {
                "risk": "within_write_set",
                "outsideWriteSetFiles": [],
                "manualOverride": {"present": False},
            },
        },
        "metadata": {
            "engineeringMode": "auto",
            "triggerDecision": {"active": True, "reason": "eval"},
        },
        "residualRisks": [],
    }
    if extra:
        entry.update(extra)
    return entry


def workflow_learning_case() -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    candidate_ids: list[str] = []
    episode_ids: list[str] = []
    with patch("runtimes.memory.workflow_service.workflow_memory_config", return_value=_workflow_test_config()):
        verified = workflow_memory_service.record_engineering_proof_episode(
            proof_entry=_proof_entry(
                proof_id=f"proof_eval_verified_{suffix}",
                verification_status="verified",
                extra={"scope": f"workspace:eval-verified-{suffix}"},
            ),
            workset_observations=[],
        )
        failed = workflow_memory_service.record_engineering_proof_episode(
            proof_entry=_proof_entry(
                proof_id=f"proof_eval_failed_{suffix}",
                verification_status="failed_verification",
                extra={"scope": f"workspace:eval-failed-{suffix}"},
            ),
            workset_observations=[],
        )
        risky = workflow_memory_service.record_engineering_proof_episode(
            proof_entry=_proof_entry(
                proof_id=f"proof_eval_risky_{suffix}",
                verification_status="verified",
                extra={
                    "scope": f"workspace:eval-risky-{suffix}",
                    "diagnostics": {
                        "worksetCorrelation": {
                            "risk": "outside_write_set",
                            "outsideWriteSetFiles": ["apps/v8-agent-os-admin/src/app/page.tsx"],
                            "manualOverride": {"present": True},
                        }
                    }
                },
            ),
            workset_observations=[],
        )
    for result in (verified, failed, risky):
        episode_ids.append(result["episode"]["id"])
        candidate_ids.append(result["candidate"]["id"])

    passed = (
        verified["candidate"]["status"] == "active_hint"
        and not failed["candidate"].get("goldenPathSteps")
        and bool(failed["candidate"].get("antiPatterns"))
        and risky["candidate"]["status"] != "active_hint"
    )

    from core.database import db

    with db.get_connection() as conn:
        for candidate_id in candidate_ids:
            conn.execute("DELETE FROM memory_workflow_hint_events WHERE candidate_id = ?", (candidate_id,))
            conn.execute("DELETE FROM memory_workflow_guide_states WHERE candidate_id = ?", (candidate_id,))
            conn.execute("DELETE FROM memory_workflow_candidates WHERE id = ?", (candidate_id,))
        for episode_id in episode_ids:
            conn.execute("DELETE FROM memory_workflow_episodes WHERE id = ?", (episode_id,))
        conn.commit()

    return {
        "id": "workflow_learning_eligibility",
        "status": "pass" if passed else "fail",
        "details": {
            "verifiedStatus": verified["candidate"]["status"],
            "failedStatus": failed["candidate"]["status"],
            "failedGoldenPathSteps": failed["candidate"].get("goldenPathSteps"),
            "failedAntiPatterns": failed["candidate"]["antiPatterns"],
            "riskyStatus": risky["candidate"]["status"],
        },
    }


EVAL_CASES = [
    graph_summary_case,
    graph_scope_isolation_case,
    summary_contamination_case,
    canonical_registry_case,
    external_api_isolation_case,
    workflow_learning_case,
]


def run_memory_eval_matrix() -> dict[str, Any]:
    cases = [case() for case in EVAL_CASES]
    passed = [case for case in cases if case.get("status") == "pass"]
    failed = [case for case in cases if case.get("status") != "pass"]
    pass_rate = round(len(passed) / max(len(cases), 1), 4)
    p0_cases = {
        "graph_scope_isolation",
        "summary_contamination_audit",
        "external_api_thread_isolation",
        "workflow_learning_eligibility",
    }
    p0_failed = [case for case in failed if str(case.get("id") or "") in p0_cases]
    return {
        "caseCount": len(cases),
        "passed": len(passed),
        "failed": len(failed),
        "passRate": pass_rate,
        "p0Passed": not p0_failed,
        "failedCases": [case.get("id") for case in failed],
        "riskFindings": [
            {"id": case.get("id"), "details": case.get("details")}
            for case in failed
        ],
        "benchmarkMappingScore": 9.8 if pass_rate >= 0.95 and not p0_failed else round(8.0 + pass_rate * 1.5, 1),
        "runtimeFirstScore": 9.0 if pass_rate >= 0.95 and not p0_failed else round(7.5 + pass_rate * 1.2, 1),
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(run_memory_eval_matrix(), ensure_ascii=False, indent=2))
