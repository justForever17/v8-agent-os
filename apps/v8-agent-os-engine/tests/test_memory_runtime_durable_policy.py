from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


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
from runtimes.memory.scope_resolution import ScopeResolutionService


POLICY = {
    "preference_importance_threshold": 70,
    "preference_confidence_threshold": 0.75,
    "knowledge_importance_threshold": 60,
    "knowledge_confidence_threshold": 0.70,
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

    def test_align_scopes_uses_effective_project_scope_without_workspace_scope(self):
        result = MemoryExtractionResult(
            summary="scope test",
            tags=["scope"],
            preferences=[
                PreferenceExtraction(scope="global", key="surface", value="os-phone", importance=90, confidence=0.9),
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

        memory_agent._align_extraction_scopes(result, "project:v8")

        self.assertEqual(result.preferences[0].scope, "project:v8")
        self.assertEqual(result.knowledge[0].scope, "project:v8")

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

    def test_default_global_hint_stays_global_for_unbound_main_workspace(self):
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

        self.assertEqual(result.binding.resolved_scope, "global")
        self.assertIsNone(result.binding.project_id)

    def test_cached_global_binding_is_not_reused_when_workspace_project_binding_exists(self):
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

        result = service.resolve(
            session_id="session-rebind",
            workspace_path=r"E:\Projects\v8chat\v8-agent-os",
            scope_hint="global",
        )

        self.assertFalse(result.reused_existing_binding)
        self.assertEqual(result.binding.resolved_scope, "project:v8")


if __name__ == "__main__":
    unittest.main()
