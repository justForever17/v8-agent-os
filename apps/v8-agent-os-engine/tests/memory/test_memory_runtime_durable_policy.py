from __future__ import annotations

import json
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace


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
    IdentityExtraction,
    KnowledgeExtraction,
    MemoryExtractionResult,
    PreferenceExtraction,
    RelationExtraction,
)
from core import memory_store as memory_store_module
from core.memory_canonicalization import canonicalize_memory_extraction_result
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS, storage
from runtimes.memory.models import ProjectDescriptor, SessionScopeBinding
from runtimes.memory.scope_resolution import ScopeBindingConflictError, ScopeResolutionService
from runtimes.memory.workspace_scope import canonical_workspace_scope, legacy_external_workspace_scope


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
    def test_legacy_scoped_identity_is_promoted_to_typed_global_identity(self):
        result = MemoryExtractionResult(
            summary="user named the supervisor",
            tags=["identity"],
            preferences=[
                PreferenceExtraction(
                    scope="workspace:test8",
                    key="assistant_name",
                    value="张三",
                    importance=90,
                    confidence=0.95,
                    durability="stable",
                )
            ],
        )

        decisions = memory_agent._align_extraction_scopes(result, "workspace:test8")

        self.assertEqual(result.identity.assistant_name, "张三")
        self.assertEqual(result.preferences, [])
        self.assertTrue(
            any(item.get("scopeDecision") == "legacy_identity_promoted_global" for item in decisions)
        )

    def test_identity_persistence_is_global_and_carries_canonical_evidence(self):
        result = MemoryExtractionResult(
            summary="user named the supervisor",
            tags=["identity"],
            identity=IdentityExtraction(assistant_name="张三"),
        )
        captured = {}

        def _store_identity(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"scope": "global", "updates": {"assistant_name": {"changed": True}}}

        with patch.object(memory_agent.memory_runtime, "update_supervisor_identity", side_effect=_store_identity):
            persisted = memory_agent._store_identity(
                result,
                POLICY,
                session_id="session-identity",
                source_run="run-identity",
                source_message_ids=["message-identity"],
            )

        self.assertTrue(persisted["stored"])
        self.assertEqual(captured["assistant_name"], "张三")
        self.assertEqual(captured["source"], "memory_agent")
        self.assertEqual(captured["reason"], "explicit_identity_assignment")
        self.assertEqual(
            captured["evidence_refs"],
            ["message:message-identity", "run:run-identity", "session:session-identity"],
        )

    def test_explicit_correction_mode_surfaces_exact_active_and_stale_candidates(self):
        candidates = [
            {"id": "fact-active", "scope": "workspace:test8", "fact": "LangChain 使用 0.x", "lifecycle_state": "active"},
            {"id": "fact-stale", "scope": "workspace:test8", "fact": "旧版本约束", "lifecycle_state": "stale"},
        ]
        with patch.object(memory_agent.memory_runtime, "query_knowledge", return_value=[]), patch.object(
            memory_agent.memory_runtime, "load_preferences", return_value={}
        ), patch.object(memory_agent.memory_runtime, "list_knowledge", return_value=candidates):
            context = memory_agent._build_historical_context(
                quick_summary="请更正此前的 LangChain 版本规则",
                scope_chain=["global", "workspace:test8"],
                correction_mode=True,
            )

        self.assertTrue(memory_agent._has_explicit_knowledge_correction("请更正此前的 LangChain 版本规则"))
        self.assertFalse(memory_agent._has_explicit_knowledge_correction("请新增一条 LangChain 版本规则"))
        self.assertIn("Explicit Correction Candidates", context)
        self.assertIn("[id: fact-active]", context)
        self.assertIn("[id: fact-stale]", context)

    def test_preference_canonicalization_overwrites_old_alias_with_latest_value(self):
        result = MemoryExtractionResult(
            summary="shoe preference",
            tags=["preference"],
            preferences=[
                PreferenceExtraction(
                    scope="global",
                    key="favorite_shoe_brand",
                    value="阿迪达斯",
                    importance=70,
                    confidence=0.8,
                ),
                PreferenceExtraction(
                    scope="global",
                    key="shoe_brand_preference",
                    value="耐克",
                    importance=90,
                    confidence=0.95,
                ),
            ],
        )

        diagnostics = canonicalize_memory_extraction_result(result)

        self.assertEqual(len(result.preferences), 1)
        self.assertEqual(result.preferences[0].key, "favorite_shoe_brand")
        self.assertEqual(result.preferences[0].value, "耐克")
        self.assertGreaterEqual(diagnostics["preferenceCanonicalizationCount"], 1)
        self.assertGreaterEqual(diagnostics["preferenceMergeCount"], 1)

    def test_global_preference_noise_classification_rejects_path_like_values(self):
        self.assertEqual(
            memory_agent.classify_global_preference_risk("favorite_download_path", r"C:\Users\sunny\Downloads"),
            "path_like_global_preference",
        )
        self.assertIsNone(memory_agent.classify_global_preference_risk("favorite_shoe_brand", "耐克"))

    def test_legacy_low_memory_policy_is_auto_migrated_to_balanced_defaults(self):
        raw_config = {
            "memory": {
                "preference_importance_threshold": 18,
                "preference_confidence_threshold": 0.18,
                "knowledge_importance_threshold": 20,
                "knowledge_confidence_threshold": 0.20,
                "global_knowledge_importance_threshold": 20,
                "global_knowledge_confidence_threshold": 0.20,
                "global_operational_importance_threshold": 20,
                "global_operational_confidence_threshold": 0.20,
            }
        }
        with patch.object(storage, "_ensure_config_json_exists"), patch.object(
            storage,
            "_read_raw_config_payload",
            return_value=raw_config,
        ), patch.object(storage, "_write_config_payload") as write_mock:
            applied = storage.ensure_memory_runtime_defaults()

        self.assertEqual(applied.get("durable_policy_preset"), "balanced")
        self.assertEqual(applied.get("retrieval_threshold"), 0.20)
        write_mock.assert_called()
        written_payload = write_mock.call_args.args[0]
        written_memory = written_payload["memory"]
        for key, expected in MEMORY_DURABLE_POLICY_DEFAULTS.items():
            self.assertEqual(written_memory[key], expected)

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

    def test_extract_with_llm_accepts_no_think_visible_json(self):
        payload = {
            "summary": "用户希望后台记忆抽取在 no-think 模式下仍能稳定工作。",
            "tags": ["memory", "no-think", "sanitizer"],
            "preferences": [],
            "knowledge": [
                {
                    "fact": "后台 Memory Agent 应只消费可见文本或 JSON，不依赖 reasoning 字段。",
                    "category": "runtime_quality",
                    "scope": "global",
                    "importance": 75,
                    "confidence": 0.9,
                    "durability": "stable",
                    "target_store": "knowledge",
                }
            ],
            "entities": [],
            "relations": [],
            "workflow_episodes": [],
        }

        class _FakeLlm:
            model_id = "memory-extractor-no-think-test"

            def invoke(self, _messages):  # noqa: ANN001, ANN201
                return SimpleNamespace(content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}])

        with patch.object(memory_agent, "_get_background_llm", return_value=_FakeLlm()):
            attempt = memory_agent._extract_with_llm(
                "USER: 后台记忆抽取不要依赖模型思考内容",
                "No prior knowledge retrieved.",
                resolved_scope="global",
                scope_chain=["global"],
            )

        self.assertIsNotNone(attempt.result)
        self.assertEqual(attempt.failure_stage, "")
        self.assertEqual(attempt.extractor_model, "memory-extractor-no-think-test")
        self.assertIn("no-think", attempt.raw_output_preview)
        self.assertEqual(attempt.result.knowledge[0].scope, "global")

    def test_extract_with_llm_uses_request_local_no_think_for_supported_background_model(self):
        payload = {
            "summary": "后台结构化抽取应使用可见输出，而不是把推理内容当作事实。",
            "tags": ["memory", "visible-output"],
            "preferences": [],
            "knowledge": [],
            "entities": [],
            "relations": [],
            "workflow_episodes": [],
        }
        calls = []

        class _FakeLlm:
            model_id = "memory-extractor-supported-no-think"
            _meta = {
                "thinking_control": {
                    "supportsNoThink": True,
                    "disabled": False,
                    "requestStyle": "openai_thinking_disabled",
                }
            }

            def invoke(self, _messages, **kwargs):  # noqa: ANN001, ANN201
                calls.append(dict(kwargs))
                return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

        llm = _FakeLlm()
        with patch.object(memory_agent, "_get_background_llm", return_value=llm):
            attempt = memory_agent._extract_with_llm(
                "USER: 后台记忆不可消费模型隐藏推理。",
                "No prior knowledge retrieved.",
                resolved_scope="global",
                scope_chain=["global"],
            )

        self.assertIsNotNone(attempt.result)
        self.assertEqual(calls, [{"extra_body": {"thinking": {"type": "disabled"}}}])
        self.assertFalse(llm._meta["thinking_control"]["disabled"])

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
                PreferenceExtraction(scope="global", key="command", value="这个项目以后都默认运行 pytest。", importance=90, confidence=0.9),
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
        self.assertEqual(result.preferences[2].scope, "project:v8")
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
    def test_memory_write_scope_prefers_workspace_project_over_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "alpha"
            workspace.mkdir()
            binding = SimpleNamespace(
                project_id="alpha",
                workspace_id="alpha-workspace",
                workspace_path=str(workspace),
                channel_type="phone",
                channel_remote_id="same-device",
            )

            self.assertEqual(
                memory_agent._effective_memory_scope(binding, "channel:phone:same-device"),
                canonical_workspace_scope(str(workspace)),
            )

    def test_memory_write_rejects_missing_or_unproven_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "deleted"
            binding = SimpleNamespace(
                project_id="alpha",
                workspace_id="alpha-workspace",
                workspace_path=str(missing),
            )
            self.assertEqual(memory_agent._effective_memory_scope(binding, "project:alpha"), "")

        unproven = SimpleNamespace(
            project_id="unknown-project",
            workspace_id="unknown-workspace",
        )
        self.assertEqual(memory_agent._effective_memory_scope(unproven, "project:unknown-project"), "")

    def test_explicit_global_memory_remains_global(self):
        self.assertEqual(memory_agent._effective_memory_scope(None, "global"), "global")

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

        self.assertEqual(
            result.binding.resolved_scope,
            canonical_workspace_scope(r"E:\Projects\v8chat\v8-agent-os"),
        )
        self.assertEqual(result.binding.project_id, "v8")
        self.assertEqual(result.binding.scope_source, "request_explicit")

    def test_default_global_hint_resolves_to_main_workspace_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            main_workspace = Path(temp_dir) / "main"
            main_workspace.mkdir()
            service = ScopeResolutionService(
                project_registry=_FakeProjectRegistry(None),
                binding_service=_FakeBindingService(),
                resolution_repo=_FakeResolutionRepo(),
            )
            with patch.object(
                storage,
                "get_workspace_config",
                return_value={"agent_workspace_path": str(main_workspace)},
            ):
                result = service.resolve(
                    session_id="session-main",
                    workspace_path=str(main_workspace),
                    scope_hint="global",
                )

        expected_scope = canonical_workspace_scope(str(main_workspace))
        self.assertEqual(result.binding.resolved_scope, expected_scope)
        self.assertEqual(
            result.scope_chain,
            ["global", legacy_external_workspace_scope(str(main_workspace)), expected_scope],
        )
        self.assertNotIn("workspace:main", result.scope_chain)
        self.assertIsNone(result.binding.project_id)

    def test_implicit_default_binding_persists_physical_main_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            main_workspace = Path(temp_dir) / "main"
            main_workspace.mkdir()
            service = ScopeResolutionService(
                project_registry=_FakeProjectRegistry(None),
                binding_service=_FakeBindingService(),
                resolution_repo=_FakeResolutionRepo(),
            )
            with patch.object(
                storage,
                "get_workspace_config",
                return_value={"agent_workspace_path": str(main_workspace)},
            ):
                result = service.resolve(session_id="session-implicit-main", scope_hint="global")

        self.assertEqual(result.binding.workspace_path, str(main_workspace.resolve()))
        self.assertEqual(result.binding.resolved_scope, canonical_workspace_scope(str(main_workspace)))
        self.assertNotIn("workspace:main", result.scope_chain)

    def test_unregistered_explicit_workspace_path_does_not_impersonate_main_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            main_workspace = Path(temp_dir) / "main"
            external_workspace = Path(temp_dir) / "external"
            main_workspace.mkdir()
            external_workspace.mkdir()
            service = ScopeResolutionService(
                project_registry=_FakeProjectRegistry(None),
                binding_service=_FakeBindingService(),
                resolution_repo=_FakeResolutionRepo(),
            )
            with patch.object(
                storage,
                "get_workspace_config",
                return_value={"agent_workspace_path": str(main_workspace)},
            ):
                result = service.resolve(
                    session_id="session-external",
                    workspace_path=str(external_workspace),
                    scope_hint="global",
                )

        self.assertEqual(
            result.binding.resolved_scope,
            canonical_workspace_scope(str(external_workspace)),
        )
        self.assertNotEqual(result.binding.resolved_scope, "workspace:main")
        self.assertIn(result.binding.resolved_scope, result.scope_chain)
        self.assertEqual(result.binding.workspace_path, str(external_workspace))

    def test_bound_global_scope_with_physical_workspace_upgrades_in_place(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_path = str(Path(temp_dir) / "workspace")
            Path(workspace_path).mkdir()
            project = ProjectDescriptor(
                id="v8",
                name="V8",
                workspaceId="workspace-v8",
                workspacePath=workspace_path,
            ).normalized()
            binding_service = _FakeBindingService()
            binding_service.binding = SessionScopeBinding(
                session_id="session-rebind",
                conversation_id="session-rebind",
                project_id="v8",
                workspace_id="workspace-v8",
                workspace_path=workspace_path,
                scope_hint="global",
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
                project_id="v8",
                workspace_id="workspace-v8",
                workspace_path=workspace_path,
                scope_hint="global",
            )

        self.assertFalse(result.reused_existing_binding)
        self.assertEqual(result.binding.resolved_scope, canonical_workspace_scope(workspace_path))
        self.assertNotEqual(result.binding.resolved_scope, "global")
        self.assertEqual(result.evidence["rebind_reason"], "physical_workspace_scope_upgrade")

    def test_bound_project_scope_with_physical_workspace_upgrades_in_place(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_path = str(Path(temp_dir) / "workspace")
            Path(workspace_path).mkdir()
            binding_service = _FakeBindingService()
            binding_service.binding = SessionScopeBinding(
                session_id="session-project-followup",
                conversation_id="session-project-followup",
                project_id="v8-agent-os",
                workspace_id="v8-agent-os",
                workspace_path=workspace_path,
                scope_hint="global",
                resolved_scope="project:v8-agent-os",
                scope_source="request_explicit",
            )
            service = ScopeResolutionService(
                project_registry=_FakeProjectRegistry(None),
                binding_service=binding_service,
                resolution_repo=_FakeResolutionRepo(),
            )

            result = service.resolve(
                session_id="session-project-followup",
                scope_hint="project:v8-agent-os",
            )

        self.assertFalse(result.reused_existing_binding)
        self.assertEqual(result.binding.resolved_scope, canonical_workspace_scope(workspace_path))
        self.assertEqual(result.evidence["rebind_reason"], "physical_workspace_scope_upgrade")

    def test_bound_project_session_still_rejects_unrelated_scope_hint(self):
        binding_service = _FakeBindingService()
        binding_service.binding = SessionScopeBinding(
            session_id="session-project-wrong-hint",
            conversation_id="session-project-wrong-hint",
            project_id="v8-agent-os",
            workspace_id="v8-agent-os",
            workspace_path=r"E:\Projects\v8chat\v8-agent-os",
            scope_hint="global",
            resolved_scope="project:v8-agent-os",
            scope_source="request_explicit",
        )
        service = ScopeResolutionService(
            project_registry=_FakeProjectRegistry(None),
            binding_service=binding_service,
            resolution_repo=_FakeResolutionRepo(),
        )

        with self.assertRaises(ScopeBindingConflictError) as raised:
            service.resolve(
                session_id="session-project-wrong-hint",
                project_id="v8-agent-os",
                workspace_id="v8-agent-os",
                workspace_path=r"E:\Projects\v8chat\v8-agent-os",
                scope_hint="project:other",
            )

        self.assertIn("scope_hint", raised.exception.payload["changedAnchors"])

    def test_bound_session_reuses_equivalent_windows_workspace_path(self):
        workspace_path = r"E:\Projects\v8chat\v8-agent-os"
        binding_service = _FakeBindingService()
        binding_service.binding = SessionScopeBinding(
            session_id="session-path-separators",
            conversation_id="session-path-separators",
            project_id="v8-agent-os",
            workspace_id="v8-agent-os",
            workspace_path=workspace_path,
            scope_hint="global",
            resolved_scope=canonical_workspace_scope(workspace_path),
            scope_source="request_explicit",
        )
        service = ScopeResolutionService(
            project_registry=_FakeProjectRegistry(None),
            binding_service=binding_service,
            resolution_repo=_FakeResolutionRepo(),
        )

        result = service.resolve(
            session_id="session-path-separators",
            project_id="v8-agent-os",
            workspace_id="v8-agent-os",
            workspace_path="E:/Projects/v8chat/v8-agent-os",
            scope_hint="global",
        )

        self.assertTrue(result.reused_existing_binding)
        self.assertEqual(result.binding.workspace_path, workspace_path)


class MemoryStoreGovernanceTests(unittest.TestCase):
    def test_typed_identity_revoke_returns_to_placeholder_and_records_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.update_supervisor_identity(
                    assistant_name="张三",
                    source="human_admin",
                    reason="test_identity_assignment",
                )
                self.assertTrue(
                    store.clear_supervisor_identity(
                        key="assistant_name",
                        source="human_admin",
                        reason="test_identity_revoke",
                    )
                )
                effective = store.load_preferences(scope="global", scope_chain=["global"])
                history = store.list_preference_history(key="assistant_name")

        self.assertEqual(effective["assistant_name"], "Please help me come up with a name.")
        self.assertEqual([item["action"] for item in history[:2]], ["deleted", "updated"])
        self.assertEqual(history[0]["reason"], "test_identity_revoke")

    def test_identity_is_global_only_and_migration_preserves_append_only_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                raw = store._load_raw_preferences()
                raw["workspace:test8"] = {"assistant_name": "张三"}
                store._save_preferences(raw)

                # A legacy value never shadows the placeholder or a valid global name.
                before_migration = store.load_preferences(
                    scope="workspace:test8",
                    scope_chain=["global", "workspace:test8"],
                )
                self.assertEqual(before_migration["assistant_name"], "Please help me come up with a name.")
                with self.assertRaises(ValueError):
                    store.update_preference("assistant_name", "局部名称", scope="workspace:test8")
                with self.assertRaises(ValueError):
                    store.update_preference("assistant_name", "绕过类型入口", scope="global")
                with self.assertRaises(ValueError):
                    store.delete_preference("assistant_name", scope="global")

                migrated = store.migrate_scoped_identity_to_global(
                    "workspace:test8",
                    source="human_admin",
                    reason="test_legacy_identity_migration",
                    evidence_refs=["session:test8"],
                )
                merged = store.load_preferences(
                    scope="workspace:test8",
                    scope_chain=["global", "workspace:test8"],
                )
                raw_after = store._load_raw_preferences()
                history = store.list_preference_history(key="assistant_name")

        self.assertEqual(migrated["toScope"], "global")
        self.assertEqual(merged["assistant_name"], "张三")
        self.assertNotIn("assistant_name", raw_after["workspace:test8"])
        self.assertEqual(history[0]["action"], "deleted")
        self.assertEqual(history[0]["scope"], "workspace:test8")
        self.assertEqual(history[1]["action"], "updated")
        self.assertEqual(history[1]["scope"], "global")
        self.assertEqual(history[1]["oldValue"], "Please help me come up with a name.")
        self.assertEqual(history[1]["newValue"], "张三")

    def test_preference_overwrite_uses_latest_value_for_same_scope_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.update_preference("favorite_shoe_brand", "阿迪达斯", scope="workspace:main")
                store.update_preference("favorite_shoe_brand", "耐克", scope="workspace:main")

                merged = store.load_preferences(
                    scope="workspace:main",
                    scope_chain=["global", "workspace:main"],
                )
                raw = store._load_raw_preferences()
                memory_text = store.memory_path.read_text(encoding="utf-8")

        self.assertEqual(merged["favorite_shoe_brand"], "耐克")
        self.assertEqual(raw["workspace:main"]["favorite_shoe_brand"], "耐克")
        self.assertNotIn("阿迪达斯", memory_text)

    def test_project_scope_preferences_do_not_bleed_across_projects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.update_preference("preferred_framework", "React", scope="project:project-a")
                store.update_preference("preferred_framework", "Vue", scope="project:project-b")
                store.update_preference("language", "zh-CN", scope="global")

                project_a = store.load_preferences(
                    scope="project:project-a",
                    scope_chain=["global", "project:project-a"],
                )
                project_b = store.load_preferences(
                    scope="project:project-b",
                    scope_chain=["global", "project:project-b"],
                )

        self.assertEqual(project_a["preferred_framework"], "React")
        self.assertEqual(project_b["preferred_framework"], "Vue")
        self.assertEqual(project_a["preferred_language"], "zh-CN")
        self.assertEqual(project_b["preferred_language"], "zh-CN")

    def test_global_profile_migrates_legacy_keys_and_removes_voice_protocol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.memory_path.write_text(
                    """---
type: user_preferences
version: "2.0"
last_updated: "2026-05-17"
---

[global]
language: zh-CN
system_name: V8 Agent OS
system_slug: v8-agent-os
voice_interaction_protocol: 开心时使用<voice>语音内容</voice>
expression_style: prefer_yanwenzi_over_emoji
custom_human_note: 保持这个人工条目

[project:test]
preferred_framework: React
""",
                    encoding="utf-8",
                )
                raw = store._load_raw_preferences()
                memory_text = store.memory_path.read_text(encoding="utf-8")
                quarantine = store.load_global_preference_quarantine()

        self.assertEqual(raw["global"]["preferred_language"], "zh-CN")
        self.assertEqual(raw["global"]["assistant_name"], "Please help me come up with a name.")
        self.assertEqual(raw["global"]["user_call_name"], "master")
        self.assertEqual(raw["global"]["relationship_tone"], "Warm and friendly")
        self.assertEqual(raw["global"]["response_language_style"], "prefer_yanwenzi_over_emoji")
        self.assertEqual(raw["global"]["custom_human_note"], "保持这个人工条目")
        self.assertEqual(raw["project:test"]["preferred_framework"], "React")
        self.assertNotIn("voice_interaction_protocol:", memory_text)
        self.assertTrue(any(item.get("key") == "voice_interaction_protocol" for item in quarantine))

    def test_memory_agent_cannot_write_unmapped_global_preference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.update_preference("random_global_field", "不要让 agent 自由造 key", scope="global", source="memory_agent")
                raw = store._load_raw_preferences()
                quarantine = store.load_global_preference_quarantine()

        self.assertNotIn("random_global_field", raw["global"])
        self.assertTrue(any(item.get("key") == "random_global_field" for item in quarantine))

    def test_human_can_add_custom_global_preference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.update_preference("custom_global_field", "人工维护内容", scope="global", source="human_admin")
                raw = store._load_raw_preferences()

        self.assertEqual(raw["global"]["custom_global_field"], "人工维护内容")

    def test_malformed_global_lines_are_quarantined_without_touching_project_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                store.memory_path.write_text(
                    """---
type: user_preferences
version: "2.0"
last_updated: "2026-05-17"
---

[global]
preferred_language: zh-CN
broken global line without kv

[project:test]
preferred_framework: React
""",
                    encoding="utf-8",
                )
                raw = store._load_raw_preferences()
                quarantine = store.load_global_preference_quarantine()

        self.assertEqual(raw["project:test"]["preferred_framework"], "React")
        self.assertTrue(any(item.get("key") == "invalid_global_line" for item in quarantine))

    def test_default_memory_template_only_initializes_seeded_global_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "CONFIG_DIR", Path(temp_dir)), patch.object(
                memory_store_module,
                "MEMORY_ROOT",
                memory_root,
            ):
                store = memory_store_module.MemoryStore()
                raw = store._load_raw_preferences()
                memory_text = store.memory_path.read_text(encoding="utf-8")

        self.assertEqual(raw["global"]["assistant_name"], "Please help me come up with a name.")
        self.assertEqual(raw["global"]["user_call_name"], "master")
        self.assertEqual(raw["global"]["relationship_tone"], "Warm and friendly")
        self.assertNotIn("preferred_language:", memory_text)
        self.assertNotIn("system_identity_reference:", memory_text)
        self.assertNotIn("assistant_persona:", memory_text)


if __name__ == "__main__":
    unittest.main()

