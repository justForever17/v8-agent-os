from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = types.SimpleNamespace(PersistentClient=_FakeChromaClient)

from agents import memory_agent
from agents.memory_agent import MemoryExtractionAttempt, MemoryExtractionResult
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS
from runtimes.memory.models import SessionScopeBinding


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "memory" / "session_replay_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(payload.get("cases") or [])


def _policy_with_defaults() -> dict[str, Any]:
    return {
        "extraction_enabled": True,
        **MEMORY_DURABLE_POLICY_DEFAULTS,
    }


class _Recorder:
    def __init__(self) -> None:
        self.preferences: list[dict[str, Any]] = []
        self.knowledge: list[dict[str, Any]] = []
        self.entities: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.daily_logs: list[dict[str, Any]] = []
        self.saved_state: dict[str, Any] | None = None

    def upsert_preference(self, *, key: str, value: str, scope: str = "global", source: str = "memory_agent") -> None:
        self.preferences.append({"key": key, "value": value, "scope": scope, "source": source})

    def add_knowledge(self, *, fact: str, category: str = "general", scope: str = "global", source_session: str | None = None) -> None:
        self.knowledge.append(
            {
                "fact": fact,
                "category": category,
                "scope": scope,
                "source_session": source_session,
            }
        )

    def add_entity(self, name: str, entity_type: str = "concept") -> None:
        self.entities.append({"name": name, "type": entity_type})

    def add_relation(self, subject: str, predicate: str, obj: str) -> None:
        self.relations.append({"subject": subject, "predicate": predicate, "object": obj})

    def append_daily_log_with_yaml(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.daily_logs.append(kwargs)

    def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    def save_extraction_state(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.saved_state = kwargs


class MemorySessionReplayTests(unittest.TestCase):
    maxDiff = None

    def _attempt_from_case(self, case: dict[str, Any]) -> MemoryExtractionAttempt:
        failure = case.get("extractionFailure")
        if isinstance(failure, dict):
            return MemoryExtractionAttempt(
                result=None,
                failure_stage=str(failure.get("failureStage") or ""),
                failure_reason=str(failure.get("failureReason") or ""),
                extractor_model=str(failure.get("extractorModel") or ""),
                raw_output_preview=str(failure.get("rawOutputPreview") or ""),
                parser_error_preview=str(failure.get("parserErrorPreview") or ""),
            )
        result = MemoryExtractionResult.model_validate(case["extraction"])
        return MemoryExtractionAttempt(result=result, extractor_model="fixture-memory-extractor")

    def _binding_from_case(self, case: dict[str, Any]) -> SessionScopeBinding:
        binding = SessionScopeBinding.model_validate(case["binding"])
        if not binding.scope_hint:
            binding.scope_hint = binding.resolved_scope
        return binding

    def _transcript_from_case(self, case: dict[str, Any]) -> dict[str, Any]:
        transcript = dict(case["transcript"])
        entries = list(transcript.get("entries") or [])
        transcript["durable_message_count"] = len(entries)
        transcript["runtime_event_count"] = 0
        transcript["user_message_count"] = sum(1 for item in entries if str(item.get("role") or "").lower() == "user")
        return transcript

    def test_memory_session_replay_fixtures_cover_persistence_and_failure_boundaries(self) -> None:
        for case in _load_cases():
            with self.subTest(case=case["id"]):
                recorder = _Recorder()
                transcript = self._transcript_from_case(case)
                attempt = self._attempt_from_case(case)
                binding = self._binding_from_case(case)
                expected = case["expected"]

                with patch.object(memory_agent, "_load_memory_policy", return_value=_policy_with_defaults()), patch.object(memory_agent.db, "get_messages", return_value=[]), patch.object(memory_agent, "_build_canonical_session_transcript", return_value=transcript), patch.object(memory_agent, "_generate_quick_summary", return_value=f"fixture summary {case['id']}"), patch.object(memory_agent, "_build_historical_context", return_value="No prior knowledge retrieved."), patch.object(memory_agent, "_extract_with_llm", return_value=attempt), patch.object(memory_agent.memory_runtime, "query_knowledge", return_value=[]), patch.object(memory_agent.memory_runtime, "get_extraction_state", return_value=None), patch.object(memory_agent.memory_runtime, "save_extraction_state", side_effect=recorder.save_extraction_state), patch.object(memory_agent.memory_runtime, "upsert_preference", side_effect=recorder.upsert_preference), patch.object(memory_agent.memory_runtime, "add_knowledge", side_effect=recorder.add_knowledge), patch.object(memory_agent.memory_runtime, "append_daily_log_with_yaml", side_effect=recorder.append_daily_log_with_yaml), patch.object(memory_agent.knowledge_db, "add_entity", side_effect=recorder.add_entity), patch.object(memory_agent.knowledge_db, "add_relation", side_effect=recorder.add_relation), patch.object(memory_agent.audit_logger, "log", return_value=None), patch.object(memory_agent, "_run_incremental_index", return_value=None), patch.object(memory_agent.session_scope_binding_service, "get_binding", return_value=binding):
                    result = memory_agent.analyze_session_memory(
                        case["sessionId"],
                        trigger_source="SYSTEM",
                        run_handle=recorder,
                        parent_run_id=f"parent-{case['id']}",
                    )

                self.assertEqual(result["status"], expected["status"])
                if expected["status"] == "failed":
                    self.assertEqual(result["reason"], expected["reason"])
                    self.assertEqual(result["extractionFailureStage"], expected["reason"])
                    self.assertTrue(any(topic == "memory.session_extraction.failed" for topic, _ in recorder.events))
                    self.assertIsNone(recorder.saved_state)
                    continue

                self.assertEqual(result["persisted_preference_count"], expected["persistedPreferenceCount"])
                self.assertEqual(result["persisted_knowledge_count"], expected["persistedKnowledgeCount"])
                self.assertEqual(result["persisted_relation_count"], expected["persistedRelationCount"])
                self.assertEqual(result["no_persisted_memory_reason"], expected["noPersistedMemoryReason"])
                self.assertEqual(len(recorder.preferences), expected["persistedPreferenceCount"])
                self.assertEqual(len(recorder.knowledge), expected["persistedKnowledgeCount"])
                self.assertEqual(len(recorder.relations), expected["persistedRelationCount"])
                self.assertIsNotNone(recorder.saved_state)
                self.assertTrue(any(topic == "memory.session_extraction.finished" for topic, _ in recorder.events))

    def test_graph_relations_only_grow_when_knowledge_was_truly_persisted(self) -> None:
        cases = {case["id"]: case for case in _load_cases()}
        noisy_case = cases["noise_is_policy_filtered"]
        recorder = _Recorder()

        with patch.object(memory_agent, "_load_memory_policy", return_value=_policy_with_defaults()), patch.object(memory_agent.db, "get_messages", return_value=[]), patch.object(memory_agent, "_build_canonical_session_transcript", return_value=self._transcript_from_case(noisy_case)), patch.object(memory_agent, "_generate_quick_summary", return_value="fixture summary noise"), patch.object(memory_agent, "_build_historical_context", return_value="No prior knowledge retrieved."), patch.object(memory_agent, "_extract_with_llm", return_value=self._attempt_from_case(noisy_case)), patch.object(memory_agent.memory_runtime, "query_knowledge", return_value=[]), patch.object(memory_agent.memory_runtime, "get_extraction_state", return_value=None), patch.object(memory_agent.memory_runtime, "save_extraction_state", side_effect=recorder.save_extraction_state), patch.object(memory_agent.memory_runtime, "upsert_preference", side_effect=recorder.upsert_preference), patch.object(memory_agent.memory_runtime, "add_knowledge", side_effect=recorder.add_knowledge), patch.object(memory_agent.memory_runtime, "append_daily_log_with_yaml", side_effect=recorder.append_daily_log_with_yaml), patch.object(memory_agent.knowledge_db, "add_entity", side_effect=recorder.add_entity), patch.object(memory_agent.knowledge_db, "add_relation", side_effect=recorder.add_relation), patch.object(memory_agent.audit_logger, "log", return_value=None), patch.object(memory_agent, "_run_incremental_index", return_value=None), patch.object(memory_agent.session_scope_binding_service, "get_binding", return_value=self._binding_from_case(noisy_case)):
            result = memory_agent.analyze_session_memory(noisy_case["sessionId"], trigger_source="SYSTEM", run_handle=recorder)

        self.assertEqual(result["persisted_knowledge_count"], 0)
        self.assertEqual(result["persisted_relation_count"], 0)
        self.assertEqual(recorder.entities, [])
        self.assertEqual(recorder.relations, [])
        self.assertEqual(result["no_persisted_memory_reason"], "policy_filtered")


if __name__ == "__main__":
    unittest.main()


