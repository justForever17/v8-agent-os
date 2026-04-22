from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

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
from agents.memory_agent import (
    EntityExtraction,
    KnowledgeExtraction,
    MemoryExtractionResult,
    PreferenceExtraction,
    RelationExtraction,
)
from runtimes.memory.models import ProjectDescriptor, SessionScopeBinding
from runtimes.memory.scope_resolution import ScopeBindingConflictError, ScopeResolutionService


POLICY = {
    "preference_importance_threshold": 65,
    "preference_confidence_threshold": 0.70,
    "knowledge_importance_threshold": 55,
    "knowledge_confidence_threshold": 0.65,
    "global_knowledge_importance_threshold": 62,
    "global_knowledge_confidence_threshold": 0.72,
    "global_operational_importance_threshold": 58,
    "global_operational_confidence_threshold": 0.68,
}


class _FakeProjectRegistry:
    def __init__(self, project: ProjectDescriptor | None = None) -> None:
        self.project = project

    def get_project(self, project_id: str):  # noqa: ANN201
        if self.project and self.project.project_id == project_id:
            return self.project
        return None

    def find_project_for_workspace(self, *, workspace_id=None, workspace_path=None):  # noqa: ANN001, ANN201
        if self.project and (
            (workspace_id and workspace_id == self.project.workspace_id)
            or (workspace_path and workspace_path == self.project.workspace_path)
        ):
            return self.project
        return None

    def find_project_for_workflow(self, workflow_id):  # noqa: ANN001, ANN201
        return None

    def find_project_for_channel(self, channel_type, channel_remote_id):  # noqa: ANN001, ANN201
        return None


class _FakeBindingService:
    def __init__(self) -> None:
        self.binding = None

    def get_binding(self, session_id):  # noqa: ANN001, ANN201
        return self.binding

    def upsert_binding(self, binding):  # noqa: ANN001, ANN201
        self.binding = binding
        return binding


class _FakeResolutionRepo:
    def __init__(self) -> None:
        self.events = []

    def append_event(self, event):  # noqa: ANN001
        self.events.append(event)


class MemoryDurablePolicyTests(unittest.TestCase):
    def test_extract_with_llm_reports_empty_response_instead_of_generic_model_empty(self):
        class _FakeParser:
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
                pass

            def get_format_instructions(self):  # noqa: ANN201
                return "{}"

            def invoke(self, value):  # noqa: ANN001, ANN201
                raise AssertionError("blank output should not reach parser.invoke")

        class _FakeLlm:
            model_id = "memory-extractor-test"

            def invoke(self, _messages):  # noqa: ANN001, ANN201
                return SimpleNamespace(content="   ")

        with patch.object(memory_agent, "_get_background_llm", return_value=_FakeLlm()), patch.object(memory_agent, "PydanticOutputParser", _FakeParser):
            attempt = memory_agent._extract_with_llm(
                "USER: 你要记住不要使用 emoji",
                "No prior knowledge retrieved.",
                resolved_scope="global",
                scope_chain=["global"],
            )

        self.assertIsNone(attempt.result)
        self.assertEqual(attempt.failure_stage, "llm_response_empty")
        self.assertEqual(attempt.extractor_model, "memory-extractor-test")

    def test_extract_with_llm_reports_repair_parser_failed_when_fixing_parser_still_fails(self):
        class _FakeParser:
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
                pass

            def get_format_instructions(self):  # noqa: ANN201
                return "{}"

            def invoke(self, value):  # noqa: ANN001, ANN201
                raise memory_agent.OutputParserException("json schema mismatch")

        class _FakeFixingParser:
            def parse(self, value):  # noqa: ANN001, ANN201
                raise ValueError("repair parser still failed")

        class _FakeOutputFixingParser:
            @staticmethod
            def from_llm(parser, llm):  # noqa: ANN001, ANN201
                return _FakeFixingParser()

        class _FakeLlm:
            model_id = "memory-extractor-test"

            def invoke(self, _messages):  # noqa: ANN001, ANN201
                return SimpleNamespace(content='{"summary": "oops"')

        with patch.object(memory_agent, "_get_background_llm", return_value=_FakeLlm()), patch.object(memory_agent, "PydanticOutputParser", _FakeParser), patch.dict(sys.modules, {"langchain.output_parsers": types.SimpleNamespace(OutputFixingParser=_FakeOutputFixingParser)}):
            attempt = memory_agent._extract_with_llm(
                "USER: 你要记住不要使用 emoji",
                "No prior knowledge retrieved.",
                resolved_scope="global",
                scope_chain=["global"],
            )

        self.assertIsNone(attempt.result)
        self.assertEqual(attempt.failure_stage, "repair_parser_failed")
        self.assertIn("json schema mismatch", attempt.parser_error_preview)
        self.assertIn('"summary"', attempt.raw_output_preview)

    def test_global_path_like_fact_is_filtered_but_project_operational_path_is_allowed(self):
        global_fact = KnowledgeExtraction(
            fact=r"工作区媒体缓存位于 C:\Users\sunny\.v8-agent-os\workspace\downloaded_media",
            category="workspace",
            scope="global",
            importance=95,
            confidence=0.99,
            durability="stable",
        )
        project_fact = KnowledgeExtraction(
            fact=r"项目视频产物默认保存在 C:\Users\sunny\.v8-agent-os\workspace\downloaded_media",
            category="workspace",
            scope="project:v8",
            importance=65,
            confidence=0.75,
            durability="operational",
        )

        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(global_fact, POLICY),
            (False, "path_like_global"),
        )
        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(project_fact, POLICY),
            (True, "persisted"),
        )

    def test_global_stable_fact_and_explicit_preference_are_allowed(self):
        fact = KnowledgeExtraction(
            fact="V8 Agent OS uses runtime_events as the authoritative runtime event timeline.",
            category="architecture",
            scope="global",
            importance=90,
            confidence=0.95,
            durability="stable",
        )
        pref = PreferenceExtraction(
            scope="global",
            key="primary_mobile_surface",
            value="用户要求以 os-phone 作为主验收面。",
            importance=80,
            confidence=0.9,
            durability="stable",
        )

        self.assertEqual(memory_agent._evaluate_knowledge_persistence(fact, POLICY), (True, "persisted"))
        self.assertEqual(memory_agent._evaluate_preference_persistence(pref, POLICY), (True, "persisted"))

    def test_single_occurrence_global_fact_can_persist_without_old_hardcoded_bar(self):
        fact = KnowledgeExtraction(
            fact="V8 Agent OS 会将当前工作区内且成功资源化的 artifact 尽量转换成远程可访问资源供手机端预览或下载。",
            category="runtime_contract",
            scope="global",
            importance=64,
            confidence=0.74,
            durability="stable",
        )

        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(fact, POLICY),
            (True, "persisted"),
        )

    def test_global_operational_workflow_is_allowed_without_stable_durability(self):
        workflow = KnowledgeExtraction(
            fact="Computer Use 浏览器任务应先复用已有窗口，再选择 managed Chrome，避免同时打开默认 Edge 和 Chrome。",
            category="operational_workflow",
            scope="global",
            importance=75,
            confidence=0.82,
            durability="operational",
        )
        path_like = KnowledgeExtraction(
            fact=r"Computer Use 测试产物位于 C:\Users\sunny\.v8-agent-os\workspace\foo.mp4",
            category="operational_workflow",
            scope="global",
            importance=90,
            confidence=0.95,
            durability="operational",
        )

        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(workflow, POLICY),
            (True, "persisted_operational_workflow"),
        )
        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(path_like, POLICY),
            (False, "path_like_global"),
        )

    def test_global_operational_fact_can_persist_without_being_workflow_named(self):
        fact = KnowledgeExtraction(
            fact="V8 Agent OS 将 session-realtime 作为 admin、web、phone 共享 contract 层。",
            category="architecture",
            scope="global",
            importance=60,
            confidence=0.70,
            durability="operational",
        )

        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(fact, POLICY),
            (True, "persisted_global_operational"),
        )

    def test_transient_debug_noise_is_filtered(self):
        fact = KnowledgeExtraction(
            fact="临时连通性测试验证通过。",
            category="debug",
            scope="project:v8",
            importance=80,
            confidence=0.9,
            durability="stable",
        )
        transient = KnowledgeExtraction(
            fact="本轮命令输出包含一个临时报错。",
            category="debug",
            scope="project:v8",
            importance=80,
            confidence=0.9,
            durability="transient",
        )

        self.assertEqual(memory_agent._evaluate_knowledge_persistence(fact, POLICY), (False, "noise_hint"))
        self.assertEqual(
            memory_agent._evaluate_knowledge_persistence(transient, POLICY),
            (False, "durability_transient"),
        )

    def test_align_scopes_uses_effective_project_scope_and_requires_explicit_global_promotion(self):
        result = MemoryExtractionResult(
            summary="scope test",
            tags=["scope"],
            preferences=[
                PreferenceExtraction(scope="global", key="surface", value="os-phone", importance=90, confidence=0.9),
                PreferenceExtraction(scope="global", key="language", value="所有项目默认使用中文回复。", importance=90, confidence=0.9),
            ],
            knowledge=[
                KnowledgeExtraction(
                    scope="channel:feishu:old",
                    fact="项目规则测试",
                    category="architecture",
                    importance=80,
                    confidence=0.9,
                )
            ],
        )

        decisions = memory_agent._align_extraction_scopes(result, "project:v8")

        self.assertEqual(result.preferences[0].scope, "project:v8")
        self.assertEqual(result.preferences[1].scope, "global")
        self.assertEqual(result.knowledge[0].scope, "project:v8")
        self.assertTrue(any(item.get("scopeDecision") == "global_promoted" for item in decisions))

    def test_graph_counts_zero_when_no_knowledge_was_persisted(self):
        result = MemoryExtractionResult(
            summary="graph test",
            tags=["graph"],
            entities=[EntityExtraction(name="runtime_events", type="table")],
            relations=[RelationExtraction(subject="runtime_events", predicate="STORES", object="events")],
        )

        self.assertEqual(memory_agent._build_knowledge_graph(result, stored_knowledge_items=[]), {"entities": 0, "relations": 0})

    def test_session_scope_hints_read_nested_session_metadata(self):
        with patch.object(
            memory_agent.db,
            "get_session",
            return_value={
                "metadata": {
                    "workspace": {"path": r"E:\Projects\v8chat\v8-agent-os"},
                    "project": {"id": "v8"},
                    "channel": {"type": "feishu", "remoteId": "chat-1"},
                    "scopeHint": "global",
                }
            },
        ):
            hints = memory_agent._session_scope_hints("session-meta")

        self.assertEqual(hints["project_id"], "v8")
        self.assertEqual(hints["workspace_path"], r"E:\Projects\v8chat\v8-agent-os")
        self.assertEqual(hints["channel_type"], "feishu")
        self.assertEqual(hints["channel_remote_id"], "chat-1")
        self.assertEqual(hints["scope_hint"], "global")

    def test_daily_log_distinguishes_candidates_persisted_and_filtered(self):
        result = MemoryExtractionResult(
            summary="daily audit",
            tags=["memory"],
            preferences=[
                PreferenceExtraction(
                    scope="global",
                    key="voice_protocol",
                    value="<voice>...</voice> 是合法输出协议。",
                    importance=90,
                    confidence=0.95,
                )
            ],
            knowledge=[
                KnowledgeExtraction(
                    fact=r"临时文件在 C:\Users\sunny\.v8-agent-os\workspace\foo.mp4",
                    category="workspace",
                    scope="global",
                    importance=90,
                    confidence=0.95,
                    durability="stable",
                )
            ],
        )
        captured = {}

        def fake_append_daily_log_with_yaml(**kwargs):  # noqa: ANN003
            captured.update(kwargs)

        with patch.object(memory_agent.memory_runtime, "append_daily_log_with_yaml", side_effect=fake_append_daily_log_with_yaml):
            memory_agent._append_session_log(
                result,
                effective_memory_scope="global",
                session_id="session-test",
                source_runtime="chat",
                provenance_class="human_dialogue",
                memory_policy="durable",
                stored_preference_items=[result.preferences[0]],
                stored_knowledge_items=[],
                policy=POLICY,
            )

        content = captured["content"]
        self.assertIn("**Extracted candidates:**", content)
        self.assertIn("**Persisted long-term memory:**", content)
        self.assertIn("[preference][global] voice_protocol", content)
        self.assertIn("**Filtered out (policy reason):**", content)
        self.assertIn("[path_like_global]", content)
        self.assertEqual(captured["entry_metadata"]["persisted_preference_count"], 1)
        self.assertEqual(captured["entry_metadata"]["filtered_knowledge_count"], 1)


class MemoryScopeResolutionTests(unittest.TestCase):
    def test_default_global_hint_does_not_mask_project_workspace_binding(self):
        project = ProjectDescriptor(
            id="v8",
            name="V8",
            workspaceId="workspace-v8",
            workspacePath=r"E:\Projects\v8chat\v8-agent-os",
        ).normalized()
        service = ScopeResolutionService(
            project_registry=_FakeProjectRegistry(project),
            binding_service=_FakeBindingService(),
            resolution_repo=_FakeResolutionRepo(),
        )

        result = service.resolve(
            session_id="session-project",
            workspace_path=r"E:\Projects\v8chat\v8-agent-os",
            scope_hint="global",
        )

        self.assertEqual(result.binding.resolved_scope, "project:v8")
        self.assertEqual(result.binding.project_id, "v8")
        self.assertEqual(result.binding.scope_source, "request_explicit")

    def test_default_global_hint_resolves_to_main_workspace_scope(self):
        service = ScopeResolutionService(
            project_registry=_FakeProjectRegistry(None),
            binding_service=_FakeBindingService(),
            resolution_repo=_FakeResolutionRepo(),
        )

        result = service.resolve(
            session_id="session-main",
            workspace_path=r"C:\Users\sunny\.v8-agent-os\workspace",
            scope_hint="global",
        )

        self.assertEqual(result.binding.resolved_scope, "workspace:main")
        self.assertEqual(result.scope_chain, ["global", "workspace:main"])
        self.assertIsNone(result.binding.project_id)

    def test_bound_session_rejects_workspace_project_switch(self):
        project = ProjectDescriptor(
            id="v8",
            name="V8",
            workspaceId="workspace-v8",
            workspacePath=r"E:\Projects\v8chat\v8-agent-os",
        ).normalized()
        binding_service = _FakeBindingService()
        binding_service.binding = SessionScopeBinding(
            session_id="session-rebind",
            conversationId="session-rebind",
            workspacePath=r"E:\Projects\v8chat\v8-agent-os",
            scopeHint="global",
            resolved_scope="global",
            scope_source="request_explicit",
        )
        service = ScopeResolutionService(
            project_registry=_FakeProjectRegistry(project),
            binding_service=binding_service,
            resolution_repo=_FakeResolutionRepo(),
        )

        with self.assertRaises(ScopeBindingConflictError) as raised:
            service.resolve(
                session_id="session-rebind",
                workspace_path=r"E:\Projects\v8chat\v8-agent-os",
                scope_hint="global",
            )

        self.assertEqual(raised.exception.payload["recommendedAction"], "create_new_session")


if __name__ == "__main__":
    unittest.main()
