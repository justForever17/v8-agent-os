from __future__ import annotations

import sys
import types
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


def _policy_with_defaults() -> dict[str, Any]:
    return {
        "extraction_enabled": True,
        **MEMORY_DURABLE_POLICY_DEFAULTS,
    }


class _RunRecorder:
    run_id = "memory-run-visual"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.saved_state: dict[str, Any] | None = None
        self.knowledge: list[dict[str, Any]] = []

    def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    def save_extraction_state(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.saved_state = kwargs

    def write_knowledge(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.knowledge.append(kwargs)
        fact_id = f"visual-fact-{len(self.knowledge)}"
        return {"action": "new", "factId": fact_id, "canonicalFactId": fact_id}


def _binding(session_id: str) -> SessionScopeBinding:
    return SessionScopeBinding(
        session_id=session_id,
        resolved_scope="project:test7",
        scope_source="fixture",
        workspace_id="test7",
        workspace_path="E:/Projects/test7",
        project_id="test7",
    )


def test_memory_visual_enrichment_generates_text_evidence_for_unanalyzed_image(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake image bytes")
    transcript_entries = [
        {
            "id": "msg-1",
            "role": "user",
            "content": f"请记住这张截图里的偏好。\n[User uploaded file: {image}]",
        }
    ]

    with patch.object(memory_agent.storage, "get_memory_config", return_value={"visual_enrichment": {"enabled": True, "max_images": 2}}), patch.object(
        memory_agent,
        "_invoke_memory_visual_analyzer",
        return_value="--- Vision Analysis Complete ---\nOCR: 项目名 test7，主题偏好深色。",
    ) as analyzer:
        block, diagnostics = memory_agent._build_memory_visual_enrichment(
            session_id="session-visual",
            durable_messages=[],
            transcript_entries=transcript_entries,
            transcript_text="USER: 请记住这张截图里的偏好。",
            run_handle=None,
        )

    analyzer.assert_called_once()
    assert "Memory Visual Evidence" in block
    assert "主题偏好深色" in block
    assert diagnostics["enrichedCount"] == 1
    assert diagnostics["errorCount"] == 0


def test_memory_visual_enrichment_skips_already_analyzed_image(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake image bytes")
    transcript_entries = [
        {
            "id": "msg-1",
            "role": "user",
            "content": f"[User uploaded file: {image}]",
        }
    ]

    with patch.object(memory_agent.storage, "get_memory_config", return_value={"visual_enrichment": {"enabled": True, "max_images": 2}}), patch.object(
        memory_agent,
        "_invoke_memory_visual_analyzer",
    ) as analyzer:
        block, diagnostics = memory_agent._build_memory_visual_enrichment(
            session_id="session-visual",
            durable_messages=[],
            transcript_entries=transcript_entries,
            transcript_text=f"tool_result vision_media_analyzer: --- Vision Analysis Complete ---\nSource: {image}\nOCR: 已经分析过",
            run_handle=None,
        )

    analyzer.assert_not_called()
    assert block == ""
    assert diagnostics["skippedCount"] == 1
    assert diagnostics["items"][0]["reason"] == "already_analyzed"


def test_analyze_session_memory_feeds_visual_text_to_cheap_extractor(tmp_path: Path) -> None:
    session_id = "session-visual-analysis"
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake image bytes")
    transcript = {
        "session_id": session_id,
        "source": "fixture",
        "entries": [
            {
                "id": "msg-1",
                "role": "user",
                "content": f"请把截图里的长期偏好记下来。\n[User uploaded file: {image}]",
                "source": "fixture",
            }
        ],
        "latest_seq": 1,
        "durable_message_count": 1,
        "runtime_event_count": 0,
        "user_message_count": 1,
        "content_length": 80,
        "hash": "fixture-hash",
        "text": f"USER: 请把截图里的长期偏好记下来。\n[User uploaded file: {image}]",
    }
    captured_chat_text: dict[str, str] = {}

    def _fake_extract(chat_text: str, context_text: str, **kwargs: Any) -> MemoryExtractionAttempt:  # noqa: ARG001
        captured_chat_text["value"] = chat_text
        result = MemoryExtractionResult(
            summary="用户上传截图并要求记住偏好。",
            tags=["visual", "preference"],
            knowledge=[
                {
                    "fact": "截图视觉证据显示项目 test7 偏好深色界面。",
                    "category": "User preference",
                    "scope": "project:test7",
                    "importance": 80,
                    "confidence": 0.8,
                }
            ],
        )
        return MemoryExtractionAttempt(result=result, extractor_model="cheap-text-memory-model")

    recorder = _RunRecorder()
    with patch.object(memory_agent, "_load_memory_policy", return_value=_policy_with_defaults()), patch.object(memory_agent.db, "get_messages", return_value=[]), patch.object(memory_agent, "_build_canonical_session_transcript", return_value=transcript), patch.object(memory_agent.memory_runtime, "get_extraction_state", return_value=None), patch.object(memory_agent.storage, "get_memory_config", return_value={"visual_enrichment": {"enabled": True, "max_images": 2}}), patch.object(memory_agent, "_invoke_memory_visual_analyzer", return_value="--- Vision Analysis Complete ---\nOCR: 项目 test7，偏好深色界面。"), patch.object(memory_agent, "_generate_quick_summary", return_value="用户截图偏好"), patch.object(memory_agent, "_build_historical_context", return_value="No prior knowledge retrieved."), patch.object(memory_agent, "_extract_with_llm", side_effect=_fake_extract), patch.object(memory_agent.memory_runtime, "query_knowledge", return_value=[]), patch.object(memory_agent.memory_runtime, "save_extraction_state", side_effect=recorder.save_extraction_state), patch.object(memory_agent.memory_runtime, "upsert_preference", return_value=None), patch.object(memory_agent.memory_runtime, "write_knowledge", side_effect=recorder.write_knowledge), patch.object(memory_agent.memory_runtime, "append_daily_log_with_yaml", return_value=None), patch.object(memory_agent.knowledge_db, "add_entity", return_value=None), patch.object(memory_agent.knowledge_db, "add_scoped_relation", return_value=None), patch.object(memory_agent.audit_logger, "log", return_value=None), patch.object(memory_agent, "_run_incremental_index", return_value=None), patch.object(memory_agent.session_scope_binding_service, "get_binding", return_value=_binding(session_id)):
        result = memory_agent.analyze_session_memory(session_id, trigger_source="SYSTEM", run_handle=recorder)

    assert result["status"] == "completed"
    assert result["visual_enrichment"]["enrichedCount"] == 1
    assert "Memory Visual Evidence" in captured_chat_text["value"]
    assert "偏好深色界面" in captured_chat_text["value"]
    assert recorder.knowledge
    assert any(topic == "memory.visual_enrichment.completed" for topic, _ in recorder.events)
