from __future__ import annotations

import json
import runpy
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core import llm_tree_prefilter
from erc.safety_guardian import safety_guardian
from runtimes.extensions import runtime as extensions_runtime_module
from runtimes.extensions.mcp.client import MCPManager
from runtimes.extensions.runtime import ExtensionRouteBundle, ExtensionsRuntimeService
from runtimes.extensions.skills.lexicons import ExtensionLexiconRegistry
from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    def __init__(self, content: str, calls: list[str]) -> None:
        self._content = content
        self._calls = calls

    def invoke(self, messages, config=None):  # noqa: ANN001
        self._calls.append(str(messages[-1].content))
        return _FakeResponse(self._content)


class _FakeLlmFactory:
    def __init__(self, content: str, calls: list[str]) -> None:
        self._content = content
        self._calls = calls

    def create_for_role(self, role: str, **kwargs):  # noqa: ANN001
        return _FakeModel(self._content, self._calls)


class _FakeTool:
    def __init__(self, name: str, description: str, server_name: str) -> None:
        self.name = name
        self.description = description
        self.metadata = {"server_name": server_name}


class _FakePluginHostTool:
    def __init__(
        self,
        *,
        canonical_name: str,
        raw_name: str,
        description: str,
        plugin_id: str = "openclaw-lark",
        managed_channels: list[str] | None = None,
        bridge_ready: bool = True,
        inventory_source: str = "gateway_rpc",
    ) -> None:
        self.name = canonical_name
        self.description = description
        self.metadata = {
            "pluginHost": True,
            "pluginId": plugin_id,
            "canonicalName": canonical_name,
            "rawName": raw_name,
            "bridgeReady": bridge_ready,
            "toolInventorySource": inventory_source,
            "toolInventoryHealth": "healthy",
            "managedChannels": managed_channels or ["feishu"],
        }


class ExtensionsPrefilterSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        llm_tree_prefilter._PREFILTER_CACHE.clear()

    def test_llm_prefilter_bypasses_when_candidate_count_is_under_limit(self):
        calls: list[str] = []
        original_factory = llm_tree_prefilter.llm_factory
        llm_tree_prefilter.llm_factory = _FakeLlmFactory(
            '{"selected":["jimeng_visual_generation"],"reason":"视觉生成相关"}',
            calls,
        )
        try:
            selected, state = llm_tree_prefilter.select_family_keys_with_llm(
                role="extensions_prefilter",
                user_query=f"图像/视频生成 {time.time_ns()}",
                family_label="mcp_servers",
                families=[
                    {
                        "key": "context7",
                        "serverName": "context7",
                        "tools": [{"name": "query-docs", "description": "documentation lookup"}],
                    },
                    {
                        "key": "jimeng_visual_generation",
                        "serverName": "jimeng_visual_generation",
                        "tools": [{"name": "generate_video", "description": "video generation"}],
                    },
                ],
                max_families=2,
                timeout_seconds=1.0,
            )
        finally:
            llm_tree_prefilter.llm_factory = original_factory

        self.assertEqual(selected, ["context7", "jimeng_visual_generation"])
        self.assertEqual(state.get("mode"), "lexical")
        self.assertTrue(state.get("bypassed"))
        self.assertEqual(calls, [], "候选数未超过上限时不应强制调用 LLM")

    def test_extensions_runtime_selects_mcp_servers_and_exposes_full_tool_tree(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakeTool("query-docs", "Retrieves documentation from Context7.", "context7"),
            _FakeTool("resolve-library-id", "Resolves a package name to a Context7 library id.", "context7"),
            _FakeTool("generate_image", "Generate images using Volcengine visual generation API.", "jimeng_visual_generation"),
            _FakeTool("generate_video", "Create a video generation task using Volcengine API.", "jimeng_visual_generation"),
            _FakeTool("send_mail", "Send an email message.", "mail"),
        ]
        captured_mcp_families: list[dict[str, object]] = []

        def fake_select_family_keys_with_llm(**kwargs):  # noqa: ANN003
            if kwargs.get("family_label") == "mcp":
                captured_mcp_families.extend(list(kwargs.get("families") or []))
                return ["jimeng_visual_generation"], {"mode": "llm_tree", "reason": "video generation", "timedOut": False, "cacheHit": False}
            return [], {"mode": "lexical", "reason": "empty", "timedOut": False, "cacheHit": False}

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 2, "llmEnabled": True, "stage2TopK": 1, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ), patch.object(
            extensions_runtime_module,
            "select_family_keys_with_llm",
            side_effect=fake_select_family_keys_with_llm,
        ):
            bundle = service.build_contextual_route(
                user_query="documentation video",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=2,
                plugin_host_limit=0,
            )

        self.assertEqual({item["key"] for item in captured_mcp_families}, {"context7", "jimeng_visual_generation"})
        jimeng_payload = next(item for item in captured_mcp_families if item["key"] == "jimeng_visual_generation")
        self.assertEqual(jimeng_payload["name"], "jimeng_visual_generation")
        self.assertIn("Generate images using Volcengine", jimeng_payload["description"])
        self.assertIn("Create a video generation task", jimeng_payload["description"])
        self.assertIn("artifacts=", jimeng_payload["description"])
        self.assertEqual(bundle.candidate_summary.get("mcpSelectedServers"), ["jimeng_visual_generation"])
        self.assertEqual(
            set(bundle.candidate_summary.get("mcpTools") or []),
            {"generate_image", "generate_video"},
        )
        self.assertEqual(
            set(tool.name for tool in bundle.filtered_tools if getattr(tool, "metadata", {}).get("server_name") == "jimeng_visual_generation"),
            {"generate_image", "generate_video"},
        )

    def test_video_skill_selection_uses_all_skill_name_description_candidates(self):
        service = ExtensionsRuntimeService()
        skills = [
            {"name": "ai-avatar-video", "description": "talking head avatar video", "path": "C:/skills/ai-avatar-video"},
            {"name": "brand-guidelines", "description": "brand color and typography", "path": "C:/skills/brand-guidelines"},
            {"name": "canvas-design", "description": "static visual design", "path": "C:/skills/canvas-design"},
            {"name": "building-native-ui", "description": "Expo app UI", "path": "C:/skills/building-native-ui"},
            {"name": "remotion-video", "description": "使用 Remotion 框架以编程方式创建视频。", "path": "C:/skills/remotion-video"},
            {"name": "seedance2-api", "description": "Out-of-the-box Seedance 2.0 API skill to generate AI videos.", "path": "C:/skills/seedance2-api"},
            {"name": "llm-video", "description": "Enterprise-grade AI video generation pipeline.", "path": "C:/skills/llm-video"},
        ]
        captured_skill_families: list[dict[str, object]] = []

        def fake_select_family_keys_with_llm(**kwargs):  # noqa: ANN003
            if kwargs.get("family_label") == "skills":
                captured_skill_families.extend(list(kwargs.get("families") or []))
                return [
                    "C:/skills/remotion-video",
                    "C:/skills/seedance2-api",
                    "C:/skills/llm-video",
                ], {"mode": "llm_tree", "reason": "video skills", "timedOut": False, "cacheHit": False}
            return [], {"mode": "lexical", "reason": "empty", "timedOut": False, "cacheHit": False}

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1TopK": 4, "llmEnabled": True, "stage2TopK": 3, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ), patch.object(
            extensions_runtime_module,
            "select_family_keys_with_llm",
            side_effect=fake_select_family_keys_with_llm,
        ):
            bundle = service.build_contextual_route(
                user_query="视频生成",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=2,
                plugin_host_limit=0,
            )

        family_names = {str(item.get("name") or item.get("title") or "") for item in captured_skill_families}
        self.assertIn("remotion-video", family_names)
        self.assertIn("seedance2-api", family_names)
        self.assertIn("llm-video", family_names)
        self.assertLessEqual(len(bundle.selected_skill_names), 5)
        self.assertEqual(
            bundle.selected_skill_names,
            ["remotion-video", "seedance2-api", "llm-video"],
        )

    def test_extensions_runtime_prompt_includes_skill_description_and_hides_root_when_skill_is_unique(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:67cb9ebfa7543040",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "女娲造人：输入人名或模糊需求，自动深度调研并生成可运行的人物 Skill。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "skillRoot": "C:/skills/huashu-nuwa",
                "instructionPath": "C:/skills/huashu-nuwa/SKILL.md",
                "sourceType": "global",
                "visibility": "global",
                "referencesDir": "C:/skills/huashu-nuwa/references",
                "scriptsDir": "C:/skills/huashu-nuwa/scripts",
                "examplesDir": "C:/skills/huashu-nuwa/examples",
                "availableFiles": [
                    "references/",
                    "references/extraction-framework.md",
                    "scripts/",
                    "scripts/merge_research.py",
                ],
            }
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": None,
                "role": None,
                "reason": "disabled",
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="我想用 huashu-nuwa 造一个人物 skill",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.candidate_summary["skillEntries"][0]["description"], skills[0]["description"])
        self.assertIn("Skill description: 女娲造人：输入人名或模糊需求", bundle.prompt_addition)
        self.assertNotIn("Root: C:/skills/huashu-nuwa", bundle.prompt_addition)
        self.assertNotIn("Skill ID: global:67cb9ebfa7543040", bundle.prompt_addition)
        self.assertNotIn("Instruction: C:/skills/huashu-nuwa/SKILL.md", bundle.prompt_addition)
        self.assertNotIn("Examples: C:/skills/huashu-nuwa/examples", bundle.prompt_addition)
        self.assertNotIn("按当前 skill 的要求去做。", bundle.prompt_addition)
        self.assertNotIn("references/extraction-framework.md", bundle.prompt_addition)
        self.assertNotIn("scripts/merge_research.py", bundle.prompt_addition)

    def test_extensions_runtime_prompt_shows_root_when_duplicate_skill_names_exist(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:67cb9ebfa7543040",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "global version",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "skillRoot": "C:/skills/huashu-nuwa",
                "instructionPath": "C:/skills/huashu-nuwa/SKILL.md",
                "sourceType": "global",
                "visibility": "global",
            },
            {
                "skillId": "scoped:67cb9ebfa7543041",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "scoped version",
                "path": "D:/project/.agents/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "skillRoot": "D:/project/.agents/skills/huashu-nuwa",
                "instructionPath": "D:/project/.agents/skills/huashu-nuwa/SKILL.md",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": None,
                "role": None,
                "reason": "disabled",
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="我想用 huashu-nuwa 造一个人物 skill",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertIn("Root: C:/skills/huashu-nuwa", bundle.prompt_addition)
        self.assertIn("Root: D:/project/.agents/skills/huashu-nuwa", bundle.prompt_addition)

    def test_extensions_runtime_prompt_uses_explicit_placeholder_for_blank_mcp_description(self):
        service = ExtensionsRuntimeService()
        tools = [_FakeTool("query-docs", "", "context7")]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": None,
                "role": None,
                "reason": "disabled",
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="查 Context7 文档",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=2,
                plugin_host_limit=0,
            )

        self.assertIn("当前暴露给本轮的 MCP 工具：", bundle.prompt_addition)
        self.assertIn("query-docs (context7): 暂无说明。", bundle.prompt_addition)

    def test_extensions_runtime_shortlist_hits_nuwa_skill_by_raw_name_query(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:67cb9ebfa7543040",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "女娲造人：输入人名/主题/甚至只是模糊需求，自动深度调研并生成人物 Skill。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
            },
            {
                "skillId": "global:other",
                "name": "brand-guidelines",
                "folder": "brand-guidelines",
                "description": "brand color and typography",
                "path": "C:/skills/brand-guidelines",
                "skillName": "brand-guidelines",
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical_shortlist",
                "modelId": None,
                "role": None,
                "reason": "disabled",
                "skills": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="nuwa",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names, ["huashu-nuwa"])
        self.assertEqual(bundle.candidate_summary.get("skillStage1HitCount"), 1)

    def test_extensions_runtime_shortlist_supports_chinese_split_backoff(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:video",
                "name": "seedance2-api",
                "folder": "seedance2-api",
                "description": "使用即梦 API 生成 AI 视频，支持生成视频和任务查询。",
                "path": "C:/skills/seedance2-api",
                "skillName": "seedance2-api",
            },
            {
                "skillId": "global:ui",
                "name": "building-native-ui",
                "folder": "building-native-ui",
                "description": "Expo app UI",
                "path": "C:/skills/building-native-ui",
                "skillName": "building-native-ui",
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical_shortlist",
                "modelId": None,
                "role": None,
                "reason": "disabled",
                "skills": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="视频生成",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names, ["seedance2-api"])
        self.assertEqual(bundle.candidate_summary.get("skillStage1HitCount"), 1)

    def test_extensions_runtime_zero_hit_no_longer_returns_arbitrary_skills(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:a",
                "name": "brand-guidelines",
                "folder": "brand-guidelines",
                "description": "brand color and typography",
                "path": "C:/skills/brand-guidelines",
                "skillName": "brand-guidelines",
            },
            {
                "skillId": "global:b",
                "name": "building-native-ui",
                "folder": "building-native-ui",
                "description": "Expo app UI",
                "path": "C:/skills/building-native-ui",
                "skillName": "building-native-ui",
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical_shortlist",
                "modelId": None,
                "role": None,
                "reason": "disabled",
                "skills": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="量子烹饪",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names, [])
        self.assertEqual(bundle.candidate_summary.get("skillStage1HitCount"), 0)

    def test_extensions_runtime_stage2_rerank_receives_only_shortlist_families(self):
        service = ExtensionsRuntimeService()
        skills = [
            {"name": "huashu-nuwa", "folder": "huashu-nuwa", "description": "女娲造人，生成人物 skill", "path": "C:/skills/huashu-nuwa"},
            {"name": "remotion-video", "folder": "remotion-video", "description": "用代码创建视频", "path": "C:/skills/remotion-video"},
            {"name": "brand-guidelines", "folder": "brand-guidelines", "description": "brand color and typography", "path": "C:/skills/brand-guidelines"},
        ]
        captured_skill_families: list[dict[str, object]] = []

        def fake_select_family_keys_with_llm(**kwargs):  # noqa: ANN003
            if kwargs.get("family_label") == "skills":
                captured_skill_families.extend(list(kwargs.get("families") or []))
                return ["C:/skills/huashu-nuwa"], {"mode": "llm_tree", "reason": "nuwa best", "timedOut": False, "cacheHit": False}
            return [], {"mode": "lexical_shortlist", "reason": "skip", "timedOut": False, "cacheHit": False}

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1TopK": 2, "llmEnabled": True, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ), patch.object(
            extensions_runtime_module,
            "select_family_keys_with_llm",
            side_effect=fake_select_family_keys_with_llm,
        ):
            bundle = service.build_contextual_route(
                user_query="nuwa 视频",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(
            {item["key"] for item in captured_skill_families},
            {"C:/skills/remotion-video", "C:/skills/huashu-nuwa"},
        )
        self.assertEqual(len(captured_skill_families), 2)
        self.assertEqual(bundle.selected_skill_names, ["huashu-nuwa"])

    def test_extensions_runtime_stage1_only_uses_stage1_topk_without_old_skill_limit_cap(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": f"global:{index}",
                "name": f"video-skill-{index}",
                "folder": f"video-skill-{index}",
                "description": "视频 生成 video generation",
                "path": f"C:/skills/video-skill-{index}",
                "skillName": f"video-skill-{index}",
            }
            for index in range(12)
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 10, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="视频生成",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(len(bundle.selected_skill_names), 10)
        self.assertEqual(bundle.candidate_summary.get("routingMode"), "stage1_only")
        self.assertEqual(bundle.candidate_summary.get("skillStage1ShortlistCount"), 10)
        self.assertEqual(bundle.candidate_summary.get("skillFinalExposedCount"), 10)

    def test_inherited_skills_are_pinned_but_do_not_exceed_configured_exposure_cap(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": f"global:video-{index}",
                "name": f"video-skill-{index}",
                "folder": f"video-skill-{index}",
                "description": "视频 生成 video generation",
                "path": f"C:/skills/video-skill-{index}",
                "skillName": f"video-skill-{index}",
            }
            for index in range(14)
        ] + [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "女娲造人，生成可运行的人物 Skill。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
            }
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 10, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="视频生成",
                available_tools=[],
                loaded_agents=None,
                inherited_skill_ids=["global:nuwa"],
                skill_limit=10,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(len(bundle.selected_skill_names), 10)
        self.assertEqual(bundle.selected_skill_names[0], "huashu-nuwa")
        self.assertEqual(bundle.candidate_summary.get("skillFinalExposedCount"), 10)

    def test_extensions_runtime_stage1_and_stage2_disabled_exposes_full_skill_inventory(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": f"global:{index}",
                "name": f"misc-skill-{index}",
                "folder": f"misc-skill-{index}",
                "description": "misc",
                "path": f"C:/skills/misc-skill-{index}",
                "skillName": f"misc-skill-{index}",
            }
            for index in range(7)
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": False, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="任意请求",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(len(bundle.selected_skill_names), 7)
        self.assertEqual(bundle.candidate_summary.get("routingMode"), "mixed")
        self.assertEqual(bundle.candidate_summary.get("skillsRoutingMode"), "unfiltered")
        self.assertEqual(bundle.candidate_summary.get("skillInventoryCount"), 7)
        self.assertEqual(bundle.candidate_summary.get("skillFinalExposedCount"), 7)

    def test_extensions_runtime_stage2_full_inventory_rerank_receives_all_skills_when_stage1_disabled(self):
        service = ExtensionsRuntimeService()
        skills = [
            {"name": "huashu-nuwa", "folder": "huashu-nuwa", "description": "女娲造人，生成人物 skill", "path": "C:/skills/huashu-nuwa"},
            {"name": "remotion-video", "folder": "remotion-video", "description": "用代码创建视频", "path": "C:/skills/remotion-video"},
            {"name": "brand-guidelines", "folder": "brand-guidelines", "description": "brand color and typography", "path": "C:/skills/brand-guidelines"},
        ]
        captured_skill_families: list[dict[str, object]] = []

        def fake_select_family_keys_with_llm(**kwargs):  # noqa: ANN003
            if kwargs.get("family_label") == "skills":
                captured_skill_families.extend(list(kwargs.get("families") or []))
                return ["C:/skills/huashu-nuwa"], {"mode": "llm_tree", "reason": "nuwa best", "timedOut": False, "cacheHit": False}
            return [], {"mode": "lexical_shortlist", "reason": "skip", "timedOut": False, "cacheHit": False}

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": False, "stage1TopK": 20, "llmEnabled": True, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ), patch.object(
            extensions_runtime_module,
            "select_family_keys_with_llm",
            side_effect=fake_select_family_keys_with_llm,
        ):
            bundle = service.build_contextual_route(
                user_query="nuwa",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(
            [item["key"] for item in captured_skill_families],
            ["C:/skills/huashu-nuwa", "C:/skills/remotion-video", "C:/skills/brand-guidelines"],
        )
        self.assertEqual(bundle.selected_skill_names, ["huashu-nuwa"])
        self.assertEqual(bundle.candidate_summary.get("skillsRoutingMode"), "llm_rerank_full_inventory")

    def test_extensions_runtime_stage2_timeout_without_stage1_falls_back_to_full_inventory(self):
        service = ExtensionsRuntimeService()
        skills = [
            {"name": "huashu-nuwa", "folder": "huashu-nuwa", "description": "女娲造人，生成人物 skill", "path": "C:/skills/huashu-nuwa"},
            {"name": "remotion-video", "folder": "remotion-video", "description": "用代码创建视频", "path": "C:/skills/remotion-video"},
            {"name": "brand-guidelines", "folder": "brand-guidelines", "description": "brand color and typography", "path": "C:/skills/brand-guidelines"},
        ]

        def fake_select_family_keys_with_llm(**kwargs):  # noqa: ANN003
            if kwargs.get("family_label") == "skills":
                return [], {"mode": "fallback", "reason": "timeout", "timedOut": True, "cacheHit": False}
            return [], {"mode": "lexical_shortlist", "reason": "skip", "timedOut": False, "cacheHit": False}

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": False, "stage1TopK": 20, "llmEnabled": True, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ), patch.object(
            extensions_runtime_module,
            "select_family_keys_with_llm",
            side_effect=fake_select_family_keys_with_llm,
        ):
            bundle = service.build_contextual_route(
                user_query="nuwa",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(len(bundle.selected_skill_names), 3)
        self.assertEqual(bundle.candidate_summary.get("skillsRoutingMode"), "fallback_unfiltered")
        self.assertEqual(bundle.candidate_summary.get("skillFinalExposedCount"), 3)

    def test_extensions_runtime_mcp_stage1_disabled_and_stage2_disabled_exposes_full_inventory(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakeTool("query-docs", "Retrieves documentation from Context7.", "context7"),
            _FakeTool("resolve-library-id", "Resolves a package name to a Context7 library id.", "context7"),
            _FakeTool("generate_video", "Create a video generation task using Volcengine API.", "jimeng_visual_generation"),
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": False, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="documentation video",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=2,
                plugin_host_limit=0,
            )

        self.assertEqual(
            set(bundle.candidate_summary.get("mcpSelectedServers") or []),
            {"context7", "jimeng_visual_generation"},
        )
        self.assertEqual(bundle.candidate_summary.get("mcpRoutingMode"), "unfiltered")
        self.assertEqual(bundle.candidate_summary.get("mcpInventoryCount"), 2)

    def test_extensions_runtime_preview_returns_stage1_and_final_lists_separately(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": f"global:{index}",
                "name": f"video-skill-{index}",
                "folder": f"video-skill-{index}",
                "description": "视频 生成 video generation",
                "path": f"C:/skills/video-skill-{index}",
                "skillName": f"video-skill-{index}",
            }
            for index in range(6)
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 4, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            import asyncio

            payload = asyncio.run(service.build_prefilter_preview(user_query="视频生成"))

        self.assertEqual(len(payload.get("skillStage1Entries") or []), 4)
        self.assertEqual(len(payload.get("skillEntries") or []), 4)
        self.assertEqual(payload.get("counts", {}).get("skillStage1ShortlistCount"), 4)
        self.assertEqual(payload.get("counts", {}).get("skillFinalExposedCount"), 4)

    def test_extensions_preview_binds_project_workspace_context(self):
        service = ExtensionsRuntimeService()
        captured_context: dict[str, str] = {}

        def fake_route(**kwargs):  # noqa: ANN003
            captured_context.update(service._resolve_event_context())
            return ExtensionRouteBundle(
                prompt_addition="",
                filtered_tools=[],
                selected_skill_names=[],
                selected_skill_ids=[],
                skill_root_descriptors=[],
                exposed_mcp_tool_names=[],
                candidate_summary={"skillRootDescriptors": []},
            )

        with patch.object(service, "build_contextual_route", side_effect=fake_route):
            import asyncio

            asyncio.run(
                service.build_prefilter_preview(
                    user_query="nuwa",
                    workspace_path=r"C:\workspaces\test1",
                    workspace_id="test1",
                    project_id="project-test1",
                )
            )

        self.assertEqual(captured_context.get("workspace_path"), r"C:\workspaces\test1")
        self.assertEqual(captured_context.get("workspace_id"), "test1")
        self.assertEqual(captured_context.get("project_id"), "project-test1")
        self.assertEqual(captured_context.get("runtime_kind"), "extensions_preview")

    def test_extensions_preview_exposes_top_level_scoped_skill_entries_and_routing(self):
        service = ExtensionsRuntimeService()

        def fake_route(**kwargs):  # noqa: ANN003
            return ExtensionRouteBundle(
                prompt_addition="",
                filtered_tools=[],
                selected_skill_names=["wechat-account-articles"],
                selected_skill_ids=["scoped:wechat-account-articles"],
                skill_root_descriptors=[
                    {
                        "rootPath": r"E:\Projects\test1\.agents\skills",
                        "workspacePath": r"E:\Projects\test1",
                        "workspaceId": "test1",
                        "projectId": "project-test1",
                        "sourceType": "scoped_workspace",
                        "visibility": "scoped",
                    }
                ],
                exposed_mcp_tool_names=[],
                candidate_summary={
                    "mode": "stage1_only",
                    "routingMode": "stage1_only",
                    "skillInventoryRevision": "rev:test1",
                    "visibleRootSignature": "visible:global+project-test1",
                    "changedRoots": [r"E:\Projects\test1\.agents\skills"],
                    "scopedRefreshMode": "delta",
                    "skillStage1Entries": [
                        {
                            "id": "wechat-account-articles",
                            "name": "wechat-account-articles",
                        }
                    ],
                    "skillEntries": [
                        {
                            "id": "wechat-account-articles",
                            "name": "wechat-account-articles",
                        }
                    ],
                    "skillRootDescriptors": [
                        {
                            "rootPath": r"E:\Projects\test1\.agents\skills",
                            "workspacePath": r"E:\Projects\test1",
                            "workspaceId": "test1",
                            "projectId": "project-test1",
                            "sourceType": "scoped_workspace",
                            "visibility": "scoped",
                        }
                    ],
                    "skills": ["wechat-account-articles"],
                    "selectedSkillIds": ["scoped:wechat-account-articles"],
                    "stage1Enabled": {"skills": True, "mcp": True},
                    "stage1TopK": {"skills": 10, "mcp": 10},
                    "stage2Enabled": {"skills": False, "mcp": False},
                    "stage2TopK": {"skills": 5, "mcp": 2},
                    "llmTimeoutSeconds": {"skills": 10, "mcp": 5},
                    "skillStage1HitCount": 1,
                    "skillStage1ShortlistCount": 1,
                    "skillFinalExposedCount": 1,
                    "skillInventoryCount": 37,
                    "skillPoolSize": 37,
                },
            )

        with patch.object(service, "build_contextual_route", side_effect=fake_route):
            import asyncio

            payload = asyncio.run(
                service.build_prefilter_preview(
                    user_query="wechat-account-articles",
                    workspace_path=r"E:\Projects\test1",
                    workspace_id="test1",
                    project_id="project-test1",
                )
            )

        self.assertEqual(
            payload.get("skillEntries"),
            [{"id": "wechat-account-articles", "name": "wechat-account-articles"}],
        )
        self.assertEqual(
            payload.get("skillStage1Entries"),
            [{"id": "wechat-account-articles", "name": "wechat-account-articles"}],
        )
        self.assertEqual(
            payload.get("skillRootDescriptors"),
            [
                {
                    "rootPath": r"E:\Projects\test1\.agents\skills",
                    "workspacePath": r"E:\Projects\test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                }
            ],
        )
        self.assertEqual(payload.get("routing", {}).get("selectedSkills"), ["wechat-account-articles"])
        self.assertEqual(payload.get("routing", {}).get("selectedSkillIds"), ["scoped:wechat-account-articles"])
        self.assertEqual(payload.get("routing", {}).get("visibleRootSignature"), "visible:global+project-test1")

    def test_supervisor_route_uses_bound_scoped_context_and_exposes_project_skill(self):
        service = ExtensionsRuntimeService()
        captured_inventory_kwargs: list[dict[str, object]] = []
        scoped_inventory = {
            "items": [
                {
                    "skillId": "scoped:wechat-account-articles",
                    "skillName": "wechat-account-articles",
                    "name": "wechat-account-articles",
                    "folder": "wechat-account-articles",
                    "description": "微信公众号文章调研、选题、写作与复盘工作流。",
                    "path": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                    "skillRoot": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                    "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                    "workspacePath": "E:/Projects/test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                    "capabilityProfile": {
                        "skillClass": "workflow_or_script",
                        "primaryArtifactTypes": ["article"],
                        "primaryOperations": ["research", "write"],
                        "interactionMode": "workflow",
                        "capabilityConfidence": 0.92,
                        "profileSource": "rules",
                        "secondaryArtifactHints": ["wechat", "公众号文章"],
                        "secondaryOperationHints": [],
                    },
                    "themeProfile": {
                        "primaryThemes": ["content_marketing"],
                        "secondaryThemeTags": ["wechat_official_account"],
                        "themeConfidence": 0.86,
                        "themeSource": "rules",
                        "themeEvidenceSignals": {},
                    },
                }
            ],
            "rootDescriptors": [
                {"rootPath": "C:/Users/sunny/.agents/skills", "sourceType": "global", "visibility": "global"},
                {
                    "rootPath": "E:/Projects/test1/.agents/skills",
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                    "workspacePath": "E:/Projects/test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                },
            ],
            "revision": "skills:scoped:test1",
            "visibleRootSignature": "visible:global+project-test1",
            "visibleRootRevisionKey": "visible-rev:test1",
            "changedRoots": ["E:/Projects/test1/.agents/skills"],
            "scopedRefreshMode": "delta",
            "recentSkillDiscovery": [],
        }

        def fake_resolve_skill_inventory(**kwargs):  # noqa: ANN003
            captured_inventory_kwargs.append(dict(kwargs))
            return dict(scoped_inventory)

        prefilter_policy = {
            "enabled": False,
            "available": False,
            "mode": "lexical_only",
            "modelId": "",
            "role": "",
            "reason": "test",
            "skills": {"stage1Enabled": True, "stage1TopK": 10, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
            "mcp": {"stage1Enabled": True, "stage1TopK": 10, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
        }
        token = service.bind_execution_context(
            session_id="session-project-test1",
            workspace_id="test1",
            workspace_path=r"E:\Projects\test1",
            project_id="project-test1",
            runtime_kind="chat",
        )
        inventory_freshness = {
            "skillContext": {
                "session_id": "session-project-test1",
                "explicit_workspace_id": "test1",
                "explicit_workspace_path": r"E:\Projects\test1",
                "explicit_project_id": "project-test1",
                "runtime_kind": "chat",
            },
            "visibleDescriptors": list(scoped_inventory.get("rootDescriptors") or []),
            "visibleRootSignature": "visible:global+project-test1",
            "visibleRootRevisionKey": "visible-rev:test1",
            "inventoryReadyState": "ready",
            "snapshotFreshness": "live",
            "inventoryBarrierApplied": True,
            "inventoryBarrierWaitMs": 0,
            "inventoryBarrierTimedOut": False,
            "dirtyVisibleRoots": [],
            "excludeRootPaths": set(),
            "waitBudgetMs": 0,
        }
        try:
            with patch.object(service, "_apply_inventory_freshness_mode", return_value=dict(inventory_freshness)), patch.object(
                service,
                "_resolve_skill_inventory",
                side_effect=fake_resolve_skill_inventory,
            ), patch.object(
                service,
                "_resolve_prefilter_policy",
                return_value=prefilter_policy,
            ):
                bundle = service.build_supervisor_route(
                    user_query="写一个微信公众号文章选题和成稿流程",
                    supervisor_tools=[],
                    loaded_agents=[],
                )
        finally:
            service.reset_execution_context(token)

        self.assertEqual(len(captured_inventory_kwargs), 1)
        self.assertEqual(captured_inventory_kwargs[0].get("explicit_project_id"), "project-test1")
        self.assertEqual(captured_inventory_kwargs[0].get("explicit_workspace_path"), r"E:\Projects\test1")
        self.assertEqual(captured_inventory_kwargs[0].get("exclude_root_paths"), set())
        self.assertIn("wechat-account-articles", bundle.selected_skill_names)
        self.assertEqual(bundle.candidate_summary.get("visibleRootSignature"), "visible:global+project-test1")
        self.assertEqual(bundle.candidate_summary.get("visibleRootRevisionKey"), "visible-rev:test1")
        self.assertEqual(bundle.candidate_summary.get("scopedRefreshMode"), "delta")
        self.assertTrue(any(item.get("projectId") == "project-test1" for item in bundle.skill_root_descriptors))

    def test_skill_loader_extracts_hint_fields_and_rule_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_root = Path(temp_dir) / "pptx"
            (skill_root / "scripts").mkdir(parents=True, exist_ok=True)
            (skill_root / "examples").mkdir(parents=True, exist_ok=True)
            skill_file = skill_root / "SKILL.md"
            content = """---
name: pptx
description: Presentation creation, editing, and analysis for .pptx PowerPoint slide decks.
aliases:
  - ppt
triggers:
  - 幻灯片
keywords:
  - slides
tags:
  - presentation
---
Use this skill to create and edit presentation decks and PowerPoint slides.
"""
            skill_file.write_text(content, encoding="utf-8")
            entry = SkillLoader._build_skill_entry(
                folder_name="pptx",
                file_path=skill_file,
                descriptor={"sourceType": "global", "visibility": "global", "rootPath": str(skill_root.parent)},
                content=content,
            )

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.get("aliases"), ["ppt"])
        self.assertEqual(entry.get("triggers"), ["幻灯片"])
        self.assertEqual(entry.get("keywords"), ["slides"])
        self.assertEqual(entry.get("tags"), ["presentation"])
        profile = dict(entry.get("capabilityProfile") or {})
        self.assertEqual(profile.get("skillClass"), "artifact_producer")
        self.assertEqual(list(profile.get("primaryArtifactTypes") or []), ["presentation"])
        self.assertIn("create", list(profile.get("primaryOperations") or []))
        self.assertNotIn("video", list(profile.get("primaryArtifactTypes") or []))
        capability_tags = dict(entry.get("capabilityTags") or {})
        self.assertIn("presentation", list(capability_tags.get("artifactTypes") or []))
        self.assertNotIn("skill", list(capability_tags.get("artifactTypes") or []))

    def test_skill_loader_extracts_description_triggers_for_nuwa_skill_authoring(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_root = Path(temp_dir) / "huashu-nuwa"
            (skill_root / "scripts").mkdir(parents=True, exist_ok=True)
            skill_file = skill_root / "SKILL.md"
            content = """---
name: huashu-nuwa
description: 女娲造人：输入人名/主题/甚至只是模糊需求，自动深度调研→思维框架提炼→生成可运行的人物Skill。 触发词：「造skill」「蒸馏XX」「女娲」「造人」「XX的思维方式」「做个XX视角」「更新XX的skill」。
---
先调研，再提炼框架，最后创建 persona skill。
"""
            skill_file.write_text(content, encoding="utf-8")
            entry = SkillLoader._build_skill_entry(
                folder_name="huashu-nuwa",
                file_path=skill_file,
                descriptor={"sourceType": "global", "visibility": "global", "rootPath": str(skill_root.parent)},
                content=content,
            )

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertIn("女娲", list(entry.get("triggers") or []))
        self.assertIn("造skill", list(entry.get("triggers") or []))
        profile = dict(entry.get("capabilityProfile") or {})
        self.assertEqual(profile.get("skillClass"), "skill_authoring")
        self.assertIn("skill", list(profile.get("primaryArtifactTypes") or []))
        capability_tags = dict(entry.get("capabilityTags") or {})
        self.assertIn("skill_authoring", list(capability_tags.get("capabilityKind") or []))
        self.assertIn("persona_skill", list(capability_tags.get("artifactTypes") or []))
        self.assertIn("writes_skill_home", list(capability_tags.get("sideEffectLevel") or []))

    def test_resolve_skill_matches_supports_alias_and_controlled_fuzzy_lookup(self):
        skills = [
            {
                "skillId": "global:pptx",
                "skillName": "pptx",
                "name": "pptx",
                "folder": "pptx",
                "description": "Presentation creation and editing for PowerPoint slide decks.",
                "skillRoot": "C:/skills/pptx",
                "instructionPath": "C:/skills/pptx/SKILL.md",
                "aliases": [],
                "triggers": [],
                "keywords": [],
                "tags": [],
            },
            {
                "skillId": "global:nuwa",
                "skillName": "huashu-nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "女娲造人。模糊需求也触发：我需要一个思维顾问。",
                "skillRoot": "C:/skills/huashu-nuwa",
                "instructionPath": "C:/skills/huashu-nuwa/SKILL.md",
                "aliases": [],
                "triggers": ["女娲", "蒸馏XX"],
                "keywords": [],
                "tags": [],
            },
            {
                "skillId": "global:elon",
                "skillName": "elon-musk-perspective",
                "name": "elon-musk-perspective",
                "folder": "elon-musk-perspective",
                "description": "Use when the user asks for elon perspective on cost structure and first principles.",
                "skillRoot": "C:/skills/elon-musk-perspective",
                "instructionPath": "C:/skills/elon-musk-perspective/SKILL.md",
                "aliases": [],
                "triggers": [],
                "keywords": [],
                "tags": [],
            },
        ]

        with patch.object(SkillLoader, "get_inventory", return_value={"items": skills, "rootDescriptors": []}):
            self.assertEqual(SkillLoader.resolve_skill_matches("ppt")[0]["skillName"], "pptx")
            self.assertEqual(SkillLoader.resolve_skill_matches("slides")[0]["skillName"], "pptx")
            self.assertEqual(SkillLoader.resolve_skill_matches("演示稿")[0]["skillName"], "pptx")
            self.assertEqual(SkillLoader.resolve_skill_matches("女娲")[0]["skillName"], "huashu-nuwa")
            self.assertEqual(SkillLoader.resolve_skill_matches("思维顾问")[0]["skillName"], "huashu-nuwa")
            self.assertEqual(SkillLoader.resolve_skill_matches("蒸馏爱因斯坦")[0]["skillName"], "huashu-nuwa")
            self.assertEqual(SkillLoader.resolve_skill_matches("elon perspective")[0]["skillName"], "elon-musk-perspective")

    def test_fetch_skill_instructions_returns_ambiguity_for_near_fuzzy_ties(self):
        skills = [
            {
                "skillId": "global:elon",
                "skillName": "elon-musk-perspective",
                "name": "elon-musk-perspective",
                "folder": "elon-musk-perspective",
                "description": "Elon perspective for first principles and business decisions.",
                "instructions": "# Elon",
                "skillRoot": "C:/skills/elon-musk-perspective",
                "instructionPath": "C:/skills/elon-musk-perspective/SKILL.md",
                "aliases": [],
                "triggers": [],
                "keywords": [],
                "tags": [],
            },
            {
                "skillId": "global:munger",
                "skillName": "munger-perspective",
                "name": "munger-perspective",
                "folder": "munger-perspective",
                "description": "Munger perspective for judgment and business decisions.",
                "instructions": "# Munger",
                "skillRoot": "C:/skills/munger-perspective",
                "instructionPath": "C:/skills/munger-perspective/SKILL.md",
                "aliases": [],
                "triggers": [],
                "keywords": [],
                "tags": [],
            },
        ]

        with patch.object(SkillLoader, "get_inventory", return_value={"items": skills, "rootDescriptors": []}):
            matches = SkillLoader.resolve_skill_matches("perspective")
            self.assertGreaterEqual(len(matches), 2)
            result = fetch_skill_instructions.invoke({"skill_name": "perspective"})

        self.assertIn("Error: 找到了多个同名或同引用的 skill", result)
        self.assertIn("elon-musk-perspective", result)
        self.assertIn("munger-perspective", result)

    def test_skill_loader_can_overlay_llm_assisted_profile(self):
        with patch.object(SkillLoader, "_should_attempt_llm_profile_inference", return_value=True), patch.object(
            SkillLoader,
            "_infer_profile_with_llm",
            return_value={
                "skillClass": "methodology_or_tutorial",
                "primaryArtifactTypes": [],
                "primaryOperations": ["guide"],
                "interactionMode": "reference_guidance",
                "capabilityConfidence": 0.77,
            },
        ):
            profile = SkillLoader._derive_capability_profile(
                name="general-guide",
                description="A guide for improving decision quality.",
                body="Use this guide when you need a better framework.",
                folder="general-guide",
                available_files=[],
                aliases=[],
                triggers=[],
                keywords=[],
                tags=[],
                has_scripts=False,
                has_templates=False,
                has_examples=False,
                has_assets=False,
            )

        self.assertEqual(profile.get("profileSource"), "llm_assisted")
        self.assertEqual(profile.get("skillClass"), "methodology_or_tutorial")
        self.assertEqual(profile.get("interactionMode"), "reference_guidance")
        self.assertEqual(profile.get("primaryOperations"), ["guide"])

    def test_skill_loader_derives_theme_profile_for_advisor_skills_without_polluting_artifact_skills(self):
        with patch.object(SkillLoader, "_should_attempt_llm_theme_inference", return_value=False):
            advisor_profile = SkillLoader._derive_theme_profile(
                name="elon-musk-perspective",
                folder="elon-musk-perspective",
                description="用马斯克的视角分析成本结构、第一性原理、垂直整合与商业化增长。",
                body="当用户提到第一性原理、成本结构、垂直整合、增长与财富时使用。",
                available_files=[],
                aliases=["elon perspective"],
                triggers=["第一性原理", "成本结构"],
                keywords=["vertical integration", "growth", "财富"],
                tags=["product strategy"],
                skill_class="advisor_or_perspective",
            )
            artifact_profile = SkillLoader._derive_theme_profile(
                name="pptx",
                folder="pptx",
                description="Presentation creation, editing, and analysis for .pptx PowerPoint slide decks.",
                body="Use this skill to create and edit presentation decks and PowerPoint slides.",
                available_files=["templates/pitch-deck.pptx"],
                aliases=["ppt", "slides"],
                triggers=["演示稿"],
                keywords=["presentation"],
                tags=["deck"],
                skill_class="artifact_producer",
            )

        self.assertIn("product_strategy", list(advisor_profile.get("primaryThemes") or []))
        self.assertIn("first_principles", list(advisor_profile.get("secondaryThemeTags") or []))
        self.assertEqual(list(artifact_profile.get("primaryThemes") or []), [])

    def test_skill_loader_can_overlay_llm_assisted_theme_profile(self):
        with patch.object(SkillLoader, "_should_attempt_llm_theme_inference", return_value=True), patch.object(
            SkillLoader,
            "_infer_theme_with_llm",
            return_value={
                "primaryThemes": ["wealth_money", "startup_growth"],
                "secondaryThemeTags": ["specific_knowledge", "leverage"],
                "themeConfidence": 0.79,
            },
        ):
            theme_profile = SkillLoader._derive_theme_profile(
                name="naval-perspective",
                folder="naval-perspective",
                description="Naval Ravikant 的财富与杠杆视角。",
                body="讨论财富、特定知识、杠杆与创业增长。",
                available_files=[],
                aliases=["naval"],
                triggers=["杠杆"],
                keywords=["wealth"],
                tags=["specific knowledge"],
                skill_class="advisor_or_perspective",
            )

        self.assertEqual(theme_profile.get("themeSource"), "llm_assisted")
        self.assertEqual(theme_profile.get("primaryThemes"), ["wealth_money", "startup_growth"])
        self.assertIn("leverage", list(theme_profile.get("secondaryThemeTags") or []))

    def test_capability_aware_stage1_prioritizes_presentation_artifact_over_generic_generation(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:video",
                "name": "ai-video-generation",
                "folder": "ai-video-generation",
                "description": "Generate AI videos with many models.",
                "path": "C:/skills/ai-video-generation",
                "skillName": "ai-video-generation",
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create"],
                    "interactionMode": "media_workflow",
                    "capabilityConfidence": 0.94,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
            },
            {
                "skillId": "global:llm-video",
                "name": "llm-video",
                "folder": "llm-video",
                "description": "Enterprise-grade AI video generation pipeline.",
                "path": "C:/skills/llm-video",
                "skillName": "llm-video",
                "capabilityProfile": {
                    "skillClass": "workflow_or_script",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create", "automate"],
                    "interactionMode": "workflow",
                    "capabilityConfidence": 0.86,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
            },
            {
                "skillId": "global:pptx",
                "name": "pptx",
                "folder": "pptx",
                "description": "Presentation creation, editing, and analysis for PowerPoint slide decks.",
                "path": "C:/skills/pptx",
                "skillName": "pptx",
                "aliases": ["ppt", "slides"],
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["presentation"],
                    "primaryOperations": ["create", "edit", "analyze"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["slides", "deck"],
                    "secondaryOperationHints": [],
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="帮我生成ppt",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names[0], "pptx")
        self.assertEqual(bundle.candidate_summary.get("artifactIntent"), "presentation")
        self.assertEqual(bundle.candidate_summary.get("operationIntent"), "create")

    def test_capability_aware_stage1_prioritizes_pptx_for_presentation_alias_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:video",
                "name": "llm-video",
                "folder": "llm-video",
                "description": "Enterprise-grade AI video generation pipeline.",
                "path": "C:/skills/llm-video",
                "skillName": "llm-video",
                "capabilityProfile": {
                    "skillClass": "workflow_or_script",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create", "automate"],
                    "interactionMode": "workflow",
                    "capabilityConfidence": 0.86,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
            },
            {
                "skillId": "global:theme-factory",
                "name": "theme-factory",
                "folder": "theme-factory",
                "description": "Toolkit for styling artifacts with a theme. These artifacts can be slides, docs, and HTML pages.",
                "path": "C:/skills/theme-factory",
                "skillName": "theme-factory",
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["presentation"],
                    "primaryOperations": ["create"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.93,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                    "evidenceSignals": {
                        "artifactMatches": {"presentation": ["slides", "slide", "deck"]},
                        "operationMatches": {"create": ["generate"]},
                        "classMatches": {},
                        "secondaryArtifacts": {},
                        "secondaryOperations": {},
                    },
                },
            },
            {
                "skillId": "global:pptx",
                "name": "pptx",
                "folder": "pptx",
                "description": "Presentation creation and editing for PowerPoint slide decks and演示稿.",
                "path": "C:/skills/pptx",
                "skillName": "pptx",
                "aliases": ["ppt", "slides", "presentation deck"],
                "triggers": ["演示稿"],
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["presentation"],
                    "primaryOperations": ["create", "edit", "analyze"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["slides", "deck"],
                    "secondaryOperationHints": [],
                    "evidenceSignals": {
                        "artifactMatches": {"presentation": ["pptx", ".pptx", "powerpoint", "slide"]},
                        "operationMatches": {"create": ["create"], "edit": ["edit"]},
                        "classMatches": {},
                        "secondaryArtifacts": {},
                        "secondaryOperations": {},
                    },
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 2, "llmEnabled": False, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            slide_bundle = service.build_contextual_route(
                user_query="slides",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )
            deck_bundle = service.build_contextual_route(
                user_query="演示稿",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(slide_bundle.selected_skill_names[0], "pptx")
        self.assertEqual(deck_bundle.selected_skill_names[0], "pptx")
        self.assertEqual(slide_bundle.candidate_summary.get("artifactIntent"), "presentation")
        self.assertEqual(deck_bundle.candidate_summary.get("artifactIntent"), "presentation")

    def test_document_subintent_prioritizes_docx_for_word_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:docx",
                "name": "docx",
                "folder": "docx",
                "description": "Create and edit Word documents with tracked changes and comments.",
                "path": "C:/skills/docx",
                "skillName": "docx",
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["document"],
                    "primaryOperations": ["create", "edit", "analyze"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.97,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:doc-coauthoring",
                "name": "doc-coauthoring",
                "folder": "doc-coauthoring",
                "description": "Structured workflow for writing documentation, proposals, specs, design docs, and RFCs.",
                "path": "C:/skills/doc-coauthoring",
                "skillName": "doc-coauthoring",
                "capabilityProfile": {
                    "skillClass": "methodology_or_tutorial",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["guide", "edit"],
                    "interactionMode": "reference_guidance",
                    "capabilityConfidence": 0.89,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["document"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="word文档",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.candidate_summary.get("artifactIntent"), "document")
        self.assertEqual(bundle.candidate_summary.get("documentSubIntent"), "office_document")
        self.assertEqual(bundle.selected_skill_names[0], "docx")

    def test_document_subintent_prioritizes_doc_workflows_for_documentation_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:docx",
                "name": "docx",
                "folder": "docx",
                "description": "Create and edit Word documents with tracked changes and comments.",
                "path": "C:/skills/docx",
                "skillName": "docx",
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["document"],
                    "primaryOperations": ["create", "edit", "analyze"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.97,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:doc-coauthoring",
                "name": "doc-coauthoring",
                "folder": "doc-coauthoring",
                "description": "Structured workflow for writing documentation, proposals, specs, design docs, and RFCs.",
                "path": "C:/skills/doc-coauthoring",
                "skillName": "doc-coauthoring",
                "capabilityProfile": {
                    "skillClass": "methodology_or_tutorial",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["guide", "edit"],
                    "interactionMode": "reference_guidance",
                    "capabilityConfidence": 0.89,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["document"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="design doc",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.candidate_summary.get("artifactIntent"), "document")
        self.assertEqual(bundle.candidate_summary.get("documentSubIntent"), "documentation")
        self.assertEqual(bundle.selected_skill_names[0], "doc-coauthoring")

    def test_skill_authoring_query_prioritizes_nuwa_over_template_skill_noise(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "女娲造人：调研人物并生成可运行的人物Skill。触发词：「造skill」「蒸馏XX」「女娲」「造人」。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "aliases": [],
                "triggers": ["女娲", "造skill", "蒸馏XX", "造人"],
                "keywords": ["persona skill"],
                "tags": [],
                "capabilityProfile": {
                    "skillClass": "skill_authoring",
                    "primaryArtifactTypes": ["skill"],
                    "primaryOperations": ["create", "search", "analyze"],
                    "interactionMode": "guided_workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                    "evidenceSignals": {"artifactMatches": {"skill": ["女娲", "造skill", "人物skill"]}},
                },
                "capabilityTags": {
                    "capabilityKind": ["skill_authoring", "research_workflow", "advisor"],
                    "artifactTypes": ["skill", "persona_skill"],
                    "operationTags": ["search", "extract", "synthesize", "create", "verify", "orchestrate"],
                },
                "themeProfile": {
                    "primaryThemes": ["decision_quality"],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.5,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:frontend",
                "name": "frontend-design",
                "folder": "frontend-design",
                "description": "Use this skill when users need frontend UI design. This skill should create polished components.",
                "path": "C:/skills/frontend-design",
                "skillName": "frontend-design",
                "aliases": [],
                "triggers": [],
                "keywords": ["frontend", "design"],
                "tags": [],
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["code"],
                    "primaryOperations": ["create"],
                    "interactionMode": "general",
                    "capabilityConfidence": 0.88,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {"primaryThemes": [], "secondaryThemeTags": [], "themeConfidence": 0.1, "themeSource": "rules", "themeEvidenceSignals": {}},
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 2, "llmEnabled": False, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="使用女娲技能调研爱因斯坦生成一个爱因斯坦skill",
                available_tools=[],
                loaded_agents=None,
                skill_limit=2,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names[0], "huashu-nuwa")

    def test_capability_aware_stage1_keeps_methodology_skills_for_decision_quality_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "模糊需求也触发：我想提升决策质量，我需要一个思维顾问。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["skill", "persona"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": ["decision_quality"],
                    "secondaryThemeTags": ["cognitive_bias", "inversion"],
                    "themeConfidence": 0.92,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:pptx",
                "name": "pptx",
                "folder": "pptx",
                "description": "Presentation creation and editing.",
                "path": "C:/skills/pptx",
                "skillName": "pptx",
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["presentation"],
                    "primaryOperations": ["create", "edit"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.93,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.12,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 2, "llmEnabled": False, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="我想提升决策质量",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names[0], "huashu-nuwa")

    def test_theme_aware_stage1_prioritizes_wealth_perspective_skills_for_money_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:elon",
                "name": "elon-musk-perspective",
                "folder": "elon-musk-perspective",
                "description": "用马斯克的视角分析成本结构、商业化、增长和财富积累。",
                "path": "C:/skills/elon-musk-perspective",
                "skillName": "elon-musk-perspective",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": ["wealth_money", "startup_growth"],
                    "secondaryThemeTags": ["first_principles", "cost_structure", "leverage"],
                    "themeConfidence": 0.94,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:munger",
                "name": "munger-perspective",
                "folder": "munger-perspective",
                "description": "芒格的逆向思考与认知偏误视角。",
                "path": "C:/skills/munger-perspective",
                "skillName": "munger-perspective",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.9,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": ["decision_quality", "wealth_money"],
                    "secondaryThemeTags": ["inversion", "cognitive_bias"],
                    "themeConfidence": 0.9,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:pptx",
                "name": "pptx",
                "folder": "pptx",
                "description": "Presentation creation and editing.",
                "path": "C:/skills/pptx",
                "skillName": "pptx",
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["presentation"],
                    "primaryOperations": ["create", "edit"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.93,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.12,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="我想赚钱",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names[0], "elon-musk-perspective")
        self.assertIn("wealth_money", bundle.candidate_summary.get("primaryThemeIntents") or [])

    def test_theme_fallback_keeps_advisory_skills_for_organization_queries_without_primary_theme_hits(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "模糊需求也触发：我需要一个思维顾问，先诊断推荐，再生成合适视角。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "create"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["skill"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.24,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:elon",
                "name": "elon-musk-perspective",
                "folder": "elon-musk-perspective",
                "description": "Use this perspective for leadership, hiring, talent density, and organizational design tradeoffs.",
                "path": "C:/skills/elon-musk-perspective",
                "skillName": "elon-musk-perspective",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.88,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": ["organizational_design", "talent_density"],
                    "themeConfidence": 0.52,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:video",
                "name": "ai-video-generation",
                "folder": "ai-video-generation",
                "description": "Generate AI videos and avatars.",
                "path": "C:/skills/ai-video-generation",
                "skillName": "ai-video-generation",
                "capabilityProfile": {
                    "skillClass": "workflow_or_script",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create"],
                    "interactionMode": "workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="怎么提高组织效率",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertIn("organization_leadership", bundle.candidate_summary.get("primaryThemeIntents") or [])
        self.assertEqual(bundle.selected_skill_names[0], "huashu-nuwa")
        self.assertIn("elon-musk-perspective", bundle.selected_skill_names[:3])
        self.assertNotEqual(bundle.selected_skill_names[0], "ai-video-generation")

    def test_theme_fallback_keeps_growth_advisors_ahead_of_generic_creative_skills(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "模糊需求也触发：我需要一个思维顾问，先诊断推荐，再生成合适视角。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "create"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["skill"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.24,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:elon",
                "name": "elon-musk-perspective",
                "folder": "elon-musk-perspective",
                "description": "Use this perspective for commercialization, distribution, startup growth, and product strategy.",
                "path": "C:/skills/elon-musk-perspective",
                "skillName": "elon-musk-perspective",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.88,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.3,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:video",
                "name": "ai-video-generation",
                "folder": "ai-video-generation",
                "description": "Generate AI videos and avatars.",
                "path": "C:/skills/ai-video-generation",
                "skillName": "ai-video-generation",
                "capabilityProfile": {
                    "skillClass": "workflow_or_script",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create"],
                    "interactionMode": "workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="我想做增长",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertIn("startup_growth", bundle.candidate_summary.get("primaryThemeIntents") or [])
        self.assertEqual(bundle.selected_skill_names[:2], ["huashu-nuwa", "elon-musk-perspective"])

    def test_theme_fallback_keeps_negotiation_advisors_ahead_of_generic_creative_skills(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "模糊需求也触发：我需要一个思维顾问，先诊断推荐，再生成合适视角。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "create"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["skill"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.24,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:munger",
                "name": "munger-perspective",
                "folder": "munger-perspective",
                "description": "Use this perspective for incentive alignment, persuasion, negotiation, and judgment.",
                "path": "C:/skills/munger-perspective",
                "skillName": "munger-perspective",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.88,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.3,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:video",
                "name": "ai-video-generation",
                "folder": "ai-video-generation",
                "description": "Generate AI videos and avatars.",
                "path": "C:/skills/ai-video-generation",
                "skillName": "ai-video-generation",
                "capabilityProfile": {
                    "skillClass": "workflow_or_script",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create"],
                    "interactionMode": "workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 3, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="谈判怎么做",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertIn("negotiation_persuasion", bundle.candidate_summary.get("primaryThemeIntents") or [])
        self.assertEqual(bundle.selected_skill_names[:2], ["huashu-nuwa", "munger-perspective"])

    def test_query_theme_faceting_maps_first_principles_and_decision_quality(self):
        query_tokens = extensions_runtime_module._query_tokens_for_extensions("从第一性原理想想，顺便提升决策质量")
        profile = extensions_runtime_module._detect_query_intents("从第一性原理想想，顺便提升决策质量", query_tokens)

        self.assertIn("decision_quality", list(profile.get("primaryThemeIntents") or []))
        self.assertIn("product_strategy", list(profile.get("primaryThemeIntents") or []))
        self.assertIn("first_principles", list(profile.get("secondaryThemeHints") or []))

    def test_theme_aware_stage1_does_not_override_artifact_chain_for_ppt_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:elon",
                "name": "elon-musk-perspective",
                "folder": "elon-musk-perspective",
                "description": "用马斯克的视角分析成本结构、第一性原理与财富增长。",
                "path": "C:/skills/elon-musk-perspective",
                "skillName": "elon-musk-perspective",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "analyze"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": ["wealth_money", "product_strategy"],
                    "secondaryThemeTags": ["first_principles", "cost_structure"],
                    "themeConfidence": 0.91,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:pptx",
                "name": "pptx",
                "folder": "pptx",
                "description": "Presentation creation and editing for PowerPoint slide decks and演示稿.",
                "path": "C:/skills/pptx",
                "skillName": "pptx",
                "aliases": ["ppt", "slides", "presentation deck"],
                "capabilityProfile": {
                    "skillClass": "artifact_producer",
                    "primaryArtifactTypes": ["presentation"],
                    "primaryOperations": ["create", "edit", "analyze"],
                    "interactionMode": "file_workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["slides", "deck"],
                    "secondaryOperationHints": [],
                    "evidenceSignals": {
                        "artifactMatches": {"presentation": ["pptx", ".pptx", "powerpoint", "slide"]},
                        "operationMatches": {"create": ["create"], "edit": ["edit"]},
                        "classMatches": {},
                        "secondaryArtifacts": {},
                        "secondaryOperations": {},
                    },
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.15,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 2, "llmEnabled": False, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="帮我生成ppt",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names[0], "pptx")
        self.assertEqual(bundle.candidate_summary.get("artifactIntent"), "presentation")

    def test_theme_fallback_does_not_override_artifact_chain_for_video_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "模糊需求也触发：我需要一个思维顾问，先诊断推荐，再生成合适视角。",
                "path": "C:/skills/huashu-nuwa",
                "skillName": "huashu-nuwa",
                "capabilityProfile": {
                    "skillClass": "advisor_or_perspective",
                    "primaryArtifactTypes": [],
                    "primaryOperations": ["advise", "create"],
                    "interactionMode": "advisory",
                    "capabilityConfidence": 0.92,
                    "profileSource": "rules",
                    "secondaryArtifactHints": ["skill"],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.24,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
            {
                "skillId": "global:video",
                "name": "ai-video-generation",
                "folder": "ai-video-generation",
                "description": "Generate AI videos and avatars.",
                "path": "C:/skills/ai-video-generation",
                "skillName": "ai-video-generation",
                "capabilityProfile": {
                    "skillClass": "workflow_or_script",
                    "primaryArtifactTypes": ["video"],
                    "primaryOperations": ["create"],
                    "interactionMode": "workflow",
                    "capabilityConfidence": 0.95,
                    "profileSource": "rules",
                    "secondaryArtifactHints": [],
                    "secondaryOperationHints": [],
                },
                "themeProfile": {
                    "primaryThemes": [],
                    "secondaryThemeTags": [],
                    "themeConfidence": 0.1,
                    "themeSource": "rules",
                    "themeEvidenceSignals": {},
                },
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 2, "llmEnabled": False, "stage2TopK": 1, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="帮我生成视频",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.selected_skill_names[0], "ai-video-generation")

    def test_extensions_runtime_mcp_office_document_queries_prefer_word_servers(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakeTool("create_word_doc", "Create and edit Word DOCX documents.", "cloud_doc"),
            _FakeTool("comment_word_doc", "Comment on Word documents.", "cloud_doc"),
            _FakeTool("search_docs", "Search design docs, RFCs, and proposals.", "doc_wiki"),
            _FakeTool("generate_video", "Generate a short video.", "video_gen"),
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 1, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="帮我处理一个 word文档",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=1,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.candidate_summary.get("artifactIntent"), "document")
        self.assertEqual(bundle.candidate_summary.get("documentSubIntent"), "office_document")
        self.assertEqual(bundle.candidate_summary.get("mcpSelectedServers"), ["cloud_doc"])
        self.assertEqual(bundle.candidate_summary.get("mcpDocumentSubIntentMatched"), 1)

    def test_extensions_runtime_mcp_documentation_queries_prefer_docs_servers(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakeTool("create_word_doc", "Create and edit Word DOCX documents.", "cloud_doc"),
            _FakeTool("search_docs", "Search design docs, RFCs, and proposals.", "doc_wiki"),
            _FakeTool("review_rfc", "Review documentation, specs, and PRDs.", "doc_wiki"),
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 1, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="帮我写一个 design doc",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=1,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.candidate_summary.get("documentSubIntent"), "documentation")
        self.assertEqual(bundle.candidate_summary.get("mcpSelectedServers"), ["doc_wiki"])
        self.assertEqual(bundle.candidate_summary.get("mcpDocumentSubIntentMatched"), 1)

    def test_extensions_runtime_plugin_host_document_queries_prefer_doc_family_after_gate(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakePluginHostTool(
                canonical_name="openclaw-lark.feishu_doc_create",
                raw_name="feishu_doc_create",
                description="Create and edit Feishu docs, design docs, RFCs and proposals.",
            ),
            _FakePluginHostTool(
                canonical_name="openclaw-lark.feishu_doc_comment",
                raw_name="feishu_doc_comment",
                description="Comment on Feishu docs and documentation.",
            ),
            _FakePluginHostTool(
                canonical_name="openclaw-lark.feishu_video_generate",
                raw_name="feishu_video_generate",
                description="Generate short videos for campaigns.",
            ),
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": None,
                "role": None,
                "reason": "disabled",
                "skills": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="请用 OpenClaw 的 feishu docs 帮我写技术文档",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=0,
                plugin_host_limit=1,
            )

        self.assertEqual(bundle.candidate_summary.get("documentSubIntent"), "documentation")
        self.assertEqual(bundle.candidate_summary.get("pluginHostSelectedFamilies"), ["openclaw-lark::feishu_doc"])
        self.assertIn("openclaw-lark.feishu_doc_create", bundle.candidate_summary.get("pluginHostTools") or [])
        self.assertNotIn("openclaw-lark.feishu_video_generate", bundle.candidate_summary.get("pluginHostTools") or [])
        self.assertEqual(bundle.candidate_summary.get("pluginHostDocumentSubIntentMatched"), 1)

    def test_extensions_runtime_mcp_theme_queries_prefer_advisory_servers_over_generators(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakeTool(
                "growth_strategy_playbook",
                "Founder growth strategy framework for GTM, monetization, conversion, and distribution.",
                "growth_advisor",
            ),
            _FakeTool(
                "review_growth_loop",
                "Analyze startup growth loops and acquisition efficiency.",
                "growth_advisor",
            ),
            _FakeTool("generate_video", "Generate social videos.", "video_gen"),
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 1, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ):
            bundle = service.build_contextual_route(
                user_query="我想做增长",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=1,
                plugin_host_limit=0,
            )

        self.assertEqual(bundle.candidate_summary.get("primaryThemeIntents"), ["startup_growth"])
        self.assertEqual(bundle.candidate_summary.get("mcpSelectedServers"), ["growth_advisor"])
        self.assertGreaterEqual(int(bundle.candidate_summary.get("mcpThemeMatchedCount") or 0), 1)

    def test_extensions_runtime_plugin_host_stage2_payload_includes_profile_summary(self):
        service = ExtensionsRuntimeService()
        tools = [
            _FakePluginHostTool(
                canonical_name="openclaw-lark.feishu_doc_create",
                raw_name="feishu_doc_create",
                description="Create and edit Feishu docs, design docs, RFCs and proposals.",
            ),
            _FakePluginHostTool(
                canonical_name="openclaw-lark.feishu_doc_comment",
                raw_name="feishu_doc_comment",
                description="Comment on documentation and specs.",
            ),
            _FakePluginHostTool(
                canonical_name="openclaw-lark.feishu_video_generate",
                raw_name="feishu_video_generate",
                description="Generate short videos for campaigns.",
            ),
        ]
        captured_plugin_families: list[dict[str, object]] = []

        def fake_select_family_keys_with_llm(**kwargs):  # noqa: ANN003
            if kwargs.get("family_label") == "plugin_host":
                captured_plugin_families.extend(list(kwargs.get("families") or []))
                return ["openclaw-lark::feishu_doc"], {"mode": "llm_tree", "reason": "documentation", "timedOut": False, "cacheHit": False}
            return [], {"mode": "lexical", "reason": "empty", "timedOut": False, "cacheHit": False}

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": True,
                "available": True,
                "mode": "two_stage",
                "modelId": "test-prefilter",
                "role": "extensions_prefilter",
                "reason": "",
                "skills": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1Enabled": True, "stage1TopK": 20, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": [], "rootDescriptors": []},
        ), patch.object(
            extensions_runtime_module,
            "select_family_keys_with_llm",
            side_effect=fake_select_family_keys_with_llm,
        ):
            bundle = service.build_contextual_route(
                user_query="请用 OpenClaw 的 feishu docs 帮我写 design doc",
                available_tools=tools,
                loaded_agents=None,
                skill_limit=0,
                mcp_limit=0,
                plugin_host_limit=1,
            )

        family_payload = next(item for item in captured_plugin_families if item["key"] == "openclaw-lark::feishu_doc")
        self.assertIn("documentSubIntent=", family_payload["description"])
        self.assertIn("artifacts=", family_payload["description"])
        self.assertEqual(bundle.candidate_summary.get("pluginHostSelectedFamilies"), ["openclaw-lark::feishu_doc"])

    def test_rule_profile_keeps_primary_artifacts_narrow_for_pptx(self):
        profile = SkillLoader._derive_capability_profile(
            name="pptx",
            description="Presentation creation, editing, and analysis for .pptx PowerPoint slide decks.",
            body="Use this skill to create and edit presentation decks and PowerPoint slides. It can export .pptx files and review speaker notes.",
            folder="pptx",
            available_files=["templates/pitch-deck.pptx", "examples/demo.pptx"],
            aliases=["ppt", "slides"],
            triggers=["演示稿"],
            keywords=["presentation"],
            tags=["deck"],
            has_scripts=False,
            has_templates=True,
            has_examples=True,
            has_assets=False,
        )

        self.assertEqual(profile.get("skillClass"), "artifact_producer")
        self.assertEqual(
            list(profile.get("primaryArtifactTypes") or []),
            ["presentation"],
        )
        self.assertNotIn("video", list(profile.get("primaryArtifactTypes") or []))
        self.assertNotIn("document", list(profile.get("primaryArtifactTypes") or []))
        self.assertNotIn("skill", list(profile.get("primaryArtifactTypes") or []))

    def test_rule_profile_ignores_skill_template_noise_for_generic_skills(self):
        profile = SkillLoader._derive_capability_profile(
            name="frontend-design",
            description="Use this skill when users need high-quality frontend UI/UX design.",
            body="This SKILL.md explains how to use this skill. It should create polished React components.",
            folder="frontend-design",
            available_files=["SKILL.md", "references/design-guide.md"],
            aliases=[],
            triggers=[],
            keywords=["frontend", "ui", "design"],
            tags=["frontend"],
            has_scripts=False,
            has_templates=False,
            has_examples=False,
            has_assets=False,
        )

        self.assertNotIn("skill", list(profile.get("primaryArtifactTypes") or []))
        self.assertNotIn("skill", list(profile.get("secondaryArtifactHints") or []))

    def test_rule_profile_keeps_perspective_skills_out_of_artifact_competition(self):
        profile = SkillLoader._derive_capability_profile(
            name="huashu-nuwa",
            description="模糊需求也触发：我想提升决策质量，我需要一个思维顾问。",
            body="用人物视角和方法论框架帮用户分析问题、给出建议，不负责生成视频或文档产物。",
            folder="huashu-nuwa",
            available_files=[],
            aliases=["女娲", "思维顾问"],
            triggers=["提升决策质量"],
            keywords=["perspective", "advisor"],
            tags=["methodology"],
            has_scripts=False,
            has_templates=False,
            has_examples=True,
            has_assets=False,
        )

        self.assertEqual(profile.get("skillClass"), "advisor_or_perspective")
        self.assertEqual(list(profile.get("primaryArtifactTypes") or []), [])
        self.assertIn("advise", list(profile.get("primaryOperations") or []))

    def test_rule_profile_does_not_misclassify_remotion_video_as_advisor(self):
        profile = SkillLoader._derive_capability_profile(
            name="remotion-video",
            description="使用 Remotion 框架以编程方式创建视频。",
            body="适合程序化视频、教程讲解视频和 3D 视频制作。",
            folder="remotion-video",
            available_files=["scripts/render-video.ts"],
            aliases=["remotion"],
            triggers=["编程视频"],
            keywords=["video"],
            tags=["animation"],
            has_scripts=True,
            has_templates=False,
            has_examples=True,
            has_assets=False,
        )

        self.assertNotEqual(profile.get("skillClass"), "advisor_or_perspective")
        self.assertIn("video", list(profile.get("primaryArtifactTypes") or []))

    def test_rule_profile_keeps_primary_document_artifact_for_workflow_publishers(self):
        profile = SkillLoader._derive_capability_profile(
            name="wechat-account-articles",
            description=(
                "End-to-end workflow for creating WeChat Official Account articles. "
                "Handles copywriting and HTML generation, and returns a publish-ready article."
            ),
            body=(
                "This workflow creates a draft.html and output.html article package, "
                "gathers screenshots, writes copy, and generates final HTML for publication."
            ),
            folder="wechat-account-articles",
            available_files=["assets/template.html", "scripts/process_html.py", "draft.html", "output.html"],
            aliases=[],
            triggers=["公众号文章"],
            keywords=["wechat", "article", "html"],
            tags=["workflow"],
            has_scripts=True,
            has_templates=True,
            has_examples=False,
            has_assets=True,
        )

        self.assertEqual(profile.get("skillClass"), "workflow_or_script")
        self.assertIn("document", list(profile.get("primaryArtifactTypes") or []))
        self.assertIn("create", list(profile.get("primaryOperations") or []))

    def test_safety_guardian_does_not_block_skill_read_on_template_like_noise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_root = Path(temp_dir) / "huashu-nuwa"
            (skill_root / "scripts").mkdir(parents=True, exist_ok=True)
            skill_file = skill_root / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text("---\nname: huashu-nuwa\ndescription: 女娲造人\n---\n说明。", encoding="utf-8")
            (skill_root / "README.md").write_text("Star History and skill usage notes.", encoding="utf-8")
            (skill_root / "scripts" / "download_subtitles.sh").write_text("yt-dlp --write-subs --sub-format srt URL", encoding="utf-8")

            decision = safety_guardian.assess_skill_directory(
                skill_name="huashu-nuwa",
                skill_root=str(skill_root),
                instruction_path=str(skill_file),
            )

        self.assertNotEqual(decision.get("verdict"), "block")
        self.assertNotIn("destructive_fs", list(decision.get("findingCategories") or []))
        self.assertNotIn("browser_profile_access", list(decision.get("findingCategories") or []))

    def test_load_cached_registry_rejects_legacy_profile_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "skills_inventory_cache.json"
            cache_path.write_text(
                """
{"version":7,"fingerprint":"legacy","items":[{"skillId":"global:legacy","skillName":"pptx","path":"C:/skills/pptx","folder":"pptx","description":"legacy","capabilityProfile":{"skillClass":"artifact_producer","artifactTypes":["presentation"],"operations":["create"],"interactionMode":"file_workflow","capabilityConfidence":0.8,"profileSource":"rules"}}]}
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(SkillLoader, "_cache_file", return_value=cache_path):
                self.assertFalse(SkillLoader._load_cached_registry())

    def test_load_cached_registry_rejects_cache_without_theme_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "skills_inventory_cache.json"
            cache_path.write_text(
                """
{"version":7,"fingerprint":"legacy","items":[{"skillId":"global:legacy","skillName":"pptx","path":"C:/skills/pptx","folder":"pptx","description":"legacy","capabilityProfile":{"skillClass":"artifact_producer","primaryArtifactTypes":["presentation"],"primaryOperations":["create"],"interactionMode":"file_workflow","capabilityConfidence":0.8,"profileSource":"rules","secondaryArtifactHints":[],"secondaryOperationHints":[],"evidenceSignals":{}}}]}
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(SkillLoader, "_cache_file", return_value=cache_path):
                self.assertFalse(SkillLoader._load_cached_registry())

    def test_skill_loader_delta_rebuilds_only_changed_entries(self):
        original_registry = SkillLoader._skills_registry
        original_manifest = SkillLoader._skills_manifest
        original_root_descriptors = SkillLoader._skills_root_descriptors
        original_root_signature = SkillLoader._skills_root_signature
        original_fingerprint = SkillLoader._skills_fingerprint
        original_revision = SkillLoader._skills_revision
        original_root_states = SkillLoader._root_inventory_states
        original_visible_cache = SkillLoader._visible_inventory_cache
        original_dirty_root_paths = SkillLoader._dirty_root_paths
        try:
            SkillLoader._skills_registry = {
                "global:unchanged": {
                    "skillId": "global:unchanged",
                    "skillName": "unchanged",
                    "instructionPath": "C:/skills/unchanged/SKILL.md",
                    "skillRoot": "C:/skills/unchanged",
                    "rootPath": "C:/skills",
                    "capabilityProfile": {"skillClass": "workflow_or_script", "primaryArtifactTypes": [], "primaryOperations": [], "interactionMode": "guided", "capabilityConfidence": 0.7, "profileSource": "rules"},
                    "themeProfile": {"primaryThemes": [], "secondaryThemeTags": [], "themeConfidence": 0.0, "themeSource": "rules", "themeEvidenceSignals": {}},
                },
                "global:updated": {
                    "skillId": "global:updated",
                    "skillName": "updated",
                    "instructionPath": "C:/skills/updated/SKILL.md",
                    "skillRoot": "C:/skills/updated",
                    "rootPath": "C:/skills",
                    "capabilityProfile": {"skillClass": "workflow_or_script", "primaryArtifactTypes": [], "primaryOperations": [], "interactionMode": "guided", "capabilityConfidence": 0.7, "profileSource": "rules"},
                    "themeProfile": {"primaryThemes": [], "secondaryThemeTags": [], "themeConfidence": 0.0, "themeSource": "rules", "themeEvidenceSignals": {}},
                },
                "global:removed": {
                    "skillId": "global:removed",
                    "skillName": "removed",
                    "instructionPath": "C:/skills/removed/SKILL.md",
                    "skillRoot": "C:/skills/removed",
                    "rootPath": "C:/skills",
                    "capabilityProfile": {"skillClass": "workflow_or_script", "primaryArtifactTypes": [], "primaryOperations": [], "interactionMode": "guided", "capabilityConfidence": 0.7, "profileSource": "rules"},
                    "themeProfile": {"primaryThemes": [], "secondaryThemeTags": [], "themeConfidence": 0.0, "themeSource": "rules", "themeEvidenceSignals": {}},
                },
            }
            unchanged_key = SkillLoader._normalize_path("C:/skills/unchanged/SKILL.md")
            updated_key = SkillLoader._normalize_path("C:/skills/updated/SKILL.md")
            removed_key = SkillLoader._normalize_path("C:/skills/removed/SKILL.md")
            added_key = SkillLoader._normalize_path("C:/skills/added/SKILL.md")
            SkillLoader._skills_manifest = {
                unchanged_key: {"mtimeNs": 1, "size": 10, "rootPath": "C:/skills", "instructionPath": "C:/skills/unchanged/SKILL.md", "folder": "unchanged"},
                updated_key: {"mtimeNs": 1, "size": 10, "rootPath": "C:/skills", "instructionPath": "C:/skills/updated/SKILL.md", "folder": "updated"},
                removed_key: {"mtimeNs": 1, "size": 10, "rootPath": "C:/skills", "instructionPath": "C:/skills/removed/SKILL.md", "folder": "removed"},
            }
            SkillLoader._skills_root_descriptors = [{"rootPath": "C:/skills", "sourceType": "global", "visibility": "global"}]
            SkillLoader._skills_root_signature = "roots:v1"
            SkillLoader._skills_fingerprint = "old"
            SkillLoader._skills_revision = "old"
            SkillLoader._root_inventory_states = {
                SkillLoader._normalize_path("C:/skills"): {
                    "descriptor": {"rootPath": "C:/skills", "sourceType": "global", "visibility": "global"},
                    "descriptorSignature": "roots:v1",
                    "manifest": {key: dict(value) for key, value in SkillLoader._skills_manifest.items()},
                    "registry": {key: dict(value) for key, value in SkillLoader._skills_registry.items()},
                    "rootRevision": "root:old",
                    "lastScanAt": "2026-04-24T00:00:00+00:00",
                    "dirty": False,
                }
            }
            SkillLoader._visible_inventory_cache = {}
            SkillLoader._dirty_root_paths = set()
            new_manifest = {
                unchanged_key: {"mtimeNs": 1, "size": 10, "instructionPath": "C:/skills/unchanged/SKILL.md", "rootPath": "C:/skills", "folder": "unchanged"},
                updated_key: {"mtimeNs": 2, "size": 12, "instructionPath": "C:/skills/updated/SKILL.md", "rootPath": "C:/skills", "folder": "updated"},
                added_key: {"mtimeNs": 1, "size": 8, "instructionPath": "C:/skills/added/SKILL.md", "rootPath": "C:/skills", "folder": "added"},
            }
            new_root_registry = {
                "global:unchanged": dict(SkillLoader._skills_registry["global:unchanged"]),
                "global:updated": {
                    **dict(SkillLoader._skills_registry["global:updated"]),
                    "description": "updated skill after root-aware refresh",
                },
                "global:added": {
                    "skillId": "global:added",
                    "skillName": "added",
                    "instructionPath": "C:/skills/added/SKILL.md",
                    "skillRoot": "C:/skills/added",
                    "rootPath": "C:/skills",
                    "capabilityProfile": {"skillClass": "workflow_or_script", "primaryArtifactTypes": [], "primaryOperations": [], "interactionMode": "guided", "capabilityConfidence": 0.7, "profileSource": "rules"},
                    "themeProfile": {"primaryThemes": [], "secondaryThemeTags": [], "themeConfidence": 0.0, "themeSource": "rules", "themeEvidenceSignals": {}},
                },
            }

            with patch.object(
                SkillLoader,
                "_discovery_root_descriptors",
                return_value=[{"rootPath": "C:/skills", "sourceType": "global", "visibility": "global"}],
            ), patch.object(
                SkillLoader, "_root_descriptors_signature", return_value="roots:v1"
            ), patch.object(
                SkillLoader, "_compute_root_manifest", return_value=new_manifest
            ), patch.object(
                SkillLoader, "_root_manifest_fingerprint", return_value="root:new"
            ), patch.object(
                SkillLoader, "_scan_single_root_descriptor", return_value=new_root_registry
            ), patch.object(
                SkillLoader, "_persist_cache"
            ):
                change = SkillLoader.reload_if_changed()

            self.assertTrue(change.get("changed"))
            self.assertEqual(change.get("refreshMode"), "delta")
            self.assertEqual(set(change.get("addedSkills") or []), {"global:added"})
            self.assertEqual(set(change.get("updatedSkills") or []), {"global:updated"})
            self.assertEqual(set(change.get("removedSkills") or []), {"global:removed"})
            self.assertEqual(set(change.get("changedRoots") or []), {SkillLoader._normalize_path("C:/skills")})
            self.assertIn("global:unchanged", SkillLoader._skills_registry)
            self.assertNotIn("global:removed", SkillLoader._skills_registry)
        finally:
            SkillLoader._skills_registry = original_registry
            SkillLoader._skills_manifest = original_manifest
            SkillLoader._skills_root_descriptors = original_root_descriptors
            SkillLoader._skills_root_signature = original_root_signature
            SkillLoader._skills_fingerprint = original_fingerprint
            SkillLoader._skills_revision = original_revision
            SkillLoader._root_inventory_states = original_root_states
            SkillLoader._visible_inventory_cache = original_visible_cache
            SkillLoader._dirty_root_paths = original_dirty_root_paths

    def test_skill_loader_visible_inventory_keeps_project_skill_out_of_default_scope(self):
        original_registry = SkillLoader._skills_registry
        original_manifest = SkillLoader._skills_manifest
        original_root_descriptors = SkillLoader._skills_root_descriptors
        original_root_signature = SkillLoader._skills_root_signature
        original_fingerprint = SkillLoader._skills_fingerprint
        original_revision = SkillLoader._skills_revision
        original_root_states = SkillLoader._root_inventory_states
        original_visible_cache = SkillLoader._visible_inventory_cache
        original_dirty_root_paths = SkillLoader._dirty_root_paths
        try:
            global_descriptor = {"rootPath": "C:/Users/sunny/.agents/skills", "sourceType": "global", "visibility": "global"}
            main_descriptor = {
                "rootPath": "C:/Users/sunny/.v8-agent-os/workspace/.agents/skills",
                "sourceType": "main_workspace",
                "visibility": "global",
                "workspacePath": "C:/Users/sunny/.v8-agent-os/workspace",
            }
            scoped_descriptor = {
                "rootPath": "E:/Projects/test1/.agents/skills",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": "E:/Projects/test1",
                "workspaceId": "test1",
                "projectId": "project-test1",
            }
            SkillLoader._skills_registry = {
                "global:global-skill": {
                    "skillId": "global:global-skill",
                    "skillName": "global-skill",
                    "instructionPath": "C:/Users/sunny/.agents/skills/global-skill/SKILL.md",
                    "skillRoot": "C:/Users/sunny/.agents/skills/global-skill",
                    "sourceType": "global",
                    "visibility": "global",
                    "rootPath": "C:/Users/sunny/.agents/skills",
                },
                "main:default-skill": {
                    "skillId": "main:default-skill",
                    "skillName": "default-skill",
                    "instructionPath": "C:/Users/sunny/.v8-agent-os/workspace/.agents/skills/default-skill/SKILL.md",
                    "skillRoot": "C:/Users/sunny/.v8-agent-os/workspace/.agents/skills/default-skill",
                    "sourceType": "main_workspace",
                    "visibility": "global",
                    "workspacePath": "C:/Users/sunny/.v8-agent-os/workspace",
                    "rootPath": "C:/Users/sunny/.v8-agent-os/workspace/.agents/skills",
                },
                "scoped:wechat-account-articles": {
                    "skillId": "scoped:wechat-account-articles",
                    "skillName": "wechat-account-articles",
                    "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                    "skillRoot": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                    "workspacePath": "E:/Projects/test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                    "rootPath": "E:/Projects/test1/.agents/skills",
                },
            }
            SkillLoader._skills_manifest = {}
            SkillLoader._skills_root_descriptors = [global_descriptor, main_descriptor, scoped_descriptor]
            SkillLoader._skills_root_signature = "roots:global-main-scoped"
            SkillLoader._skills_fingerprint = "discovery:all"
            SkillLoader._skills_revision = "discovery:all"
            SkillLoader._rebuild_root_inventory_states_from_registry()
            SkillLoader._visible_inventory_cache = {}
            SkillLoader._dirty_root_paths = set()

            with patch.object(SkillLoader, "_global_root_descriptor", return_value=global_descriptor), patch.object(
                SkillLoader, "_main_workspace_root_descriptor", return_value=main_descriptor
            ), patch.object(SkillLoader, "_scoped_workspace_root_descriptor", return_value=scoped_descriptor):
                default_inventory = SkillLoader.get_inventory(force_refresh=False, include_scoped=False)
                scoped_inventory = SkillLoader.get_inventory(
                    force_refresh=False,
                    include_scoped=True,
                    explicit_workspace_path=r"E:\Projects\test1",
                    explicit_project_id="project-test1",
                )

            default_names = {item.get("skillName") for item in default_inventory.get("items") or []}
            scoped_names = {item.get("skillName") for item in scoped_inventory.get("items") or []}
            self.assertNotIn("wechat-account-articles", default_names)
            self.assertIn("wechat-account-articles", scoped_names)
            self.assertNotEqual(default_inventory.get("visibleRootSignature"), scoped_inventory.get("visibleRootSignature"))
            self.assertIn(scoped_inventory.get("scopedRefreshMode"), (None, "base"))
            self.assertTrue(any(item.get("projectId") == "project-test1" for item in scoped_inventory.get("rootDescriptors") or []))
        finally:
            SkillLoader._skills_registry = original_registry
            SkillLoader._skills_manifest = original_manifest
            SkillLoader._skills_root_descriptors = original_root_descriptors
            SkillLoader._skills_root_signature = original_root_signature
            SkillLoader._skills_fingerprint = original_fingerprint
            SkillLoader._skills_revision = original_revision
            SkillLoader._root_inventory_states = original_root_states
            SkillLoader._visible_inventory_cache = original_visible_cache
            SkillLoader._dirty_root_paths = original_dirty_root_paths

    def test_extensions_runtime_expands_common_chinese_query_domains(self):
        wechat_tokens = extensions_runtime_module._query_tokens_for_extensions("公众号文章")
        wechat_profile = extensions_runtime_module._detect_query_intents("公众号文章", wechat_tokens)
        self.assertIn("wechat-account-article", wechat_tokens)
        self.assertIn("wechat-account", wechat_tokens)
        self.assertIn("article", wechat_tokens)
        self.assertIn("document", wechat_profile.get("artifactIntents") or [])

        finance_tokens = extensions_runtime_module._query_tokens_for_extensions("财报分析")
        finance_profile = extensions_runtime_module._detect_query_intents("财报分析", finance_tokens)
        self.assertIn("finance", finance_tokens)
        self.assertIn("analyze", finance_profile.get("operationIntents") or [])
        self.assertIn("finance_research", finance_profile.get("primaryThemeIntents") or [])

        code_tokens = extensions_runtime_module._query_tokens_for_extensions("代码审查")
        code_profile = extensions_runtime_module._detect_query_intents("代码审查", code_tokens)
        self.assertIn("code", code_profile.get("artifactIntents") or [])
        self.assertIn("analyze", code_profile.get("operationIntents") or [])

        medical_tokens = extensions_runtime_module._query_tokens_for_extensions("医学检查报告解读")
        medical_profile = extensions_runtime_module._detect_query_intents("医学检查报告解读", medical_tokens)
        self.assertIn("document", medical_profile.get("artifactIntents") or [])
        self.assertIn("healthcare_medical", medical_profile.get("primaryThemeIntents") or [])

    def test_extension_lexicon_registry_fail_soft_on_invalid_optional_locale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "en.json").write_text(
                '{"locale":"en","querySynonyms":{"wechat":["weixin","official account"]}}',
                encoding="utf-8",
            )
            (root / "zh-CN.json").write_text(
                '{"locale":"zh-CN","querySynonyms":{"公众号":["wechat","official account"]}}',
                encoding="utf-8",
            )
            (root / "ru.json").write_text("{invalid json", encoding="utf-8")

            registry = ExtensionLexiconRegistry(root_dir=root)
            snapshot = registry.ensure_fresh()

        self.assertEqual(snapshot.get("locales"), ["en", "zh-CN"])
        self.assertTrue(any("ru.json" in item for item in snapshot.get("loadErrors") or []))
        self.assertIn("公众号", snapshot.get("querySynonyms") or {})
        self.assertIn("wechat", snapshot.get("querySynonymsExact") or {})

    def test_extension_lexicon_registry_loads_market_layer_without_polluting_core_layer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "en.json").write_text(
                '{"locale":"en","querySynonyms":{"wechat":["official account","article"]}}',
                encoding="utf-8",
            )
            (root / "zh-CN.json").write_text(
                '{"locale":"zh-CN","querySynonyms":{"公众号":["wechat","article"]}}',
                encoding="utf-8",
            )
            provider_dir = root / "market" / "skills-sh"
            provider_dir.mkdir(parents=True, exist_ok=True)
            (provider_dir / "manifest.json").write_text(
                json.dumps({"provider": "skills-sh", "locales": ["en", "zh-CN"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (provider_dir / "skills-sh-top1000.en.json").write_text(
                json.dumps(
                    {
                        "locale": "en",
                        "querySynonyms": {"hyperframes": ["website", "animation", "video"]},
                        "primaryThemeSynonyms": {"content_media": ["hyperframes website"]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (provider_dir / "skills-sh-top1000.zh-CN.json").write_text(
                json.dumps(
                    {
                        "locale": "zh-CN",
                        "querySynonyms": {"超帧": ["hyperframes", "website", "animation"]},
                        "primaryThemeSynonyms": {"content_media": ["超帧网站"]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            registry = ExtensionLexiconRegistry(root_dir=root)
            snapshot = registry.ensure_fresh()

        self.assertEqual(snapshot.get("locales"), ["en", "zh-CN"])
        self.assertTrue(snapshot.get("marketEnabled"))
        self.assertIn("skills-sh", [item.get("provider") for item in snapshot.get("marketProviders") or []])
        self.assertEqual((snapshot.get("market") or {}).get("locales"), ["en", "zh-CN"])
        self.assertIn("hyperframes", (snapshot.get("market") or {}).get("querySynonymsExact") or {})
        self.assertNotIn("hyperframes", snapshot.get("querySynonymsExact") or {})

    def test_skills_sh_market_builder_keeps_priority_translation_aliases(self):
        builder = runpy.run_path(
            str(
                ENGINE_ROOT
                / "runtimes"
                / "extensions"
                / "skills"
                / "lexicons"
                / "market"
                / "skills-sh"
                / "build_skills_sh_market_lexicons.py"
            )
        )
        SkillEntry = builder["SkillEntry"]
        manifest, en_payload, zh_payload = builder["_build_market_lexicons"](
            [
                SkillEntry(
                    key="demo::wechat",
                    source="demo",
                    skill_id="wechat-publisher",
                    name="wechat-publisher",
                    detail_url="https://example.test/wechat",
                    score=1_800_000,
                    views={"all-time": {"rank": 1, "installs": 100000, "change": 0}},
                    summary_text="Publish articles and image-text posts to WeChat Official Accounts via browser automation.",
                    skill_text="Create a WeChat article and official account post workflow.",
                )
            ]
        )

        self.assertEqual(manifest.get("provider"), "skills-sh")
        self.assertIn("wechat official accounts", dict(en_payload.get("querySynonyms") or {}))
        self.assertIn("wechat official account", dict(en_payload.get("querySynonyms") or {}))
        self.assertIn("official accounts", dict(en_payload.get("querySynonyms") or {}))
        self.assertEqual(zh_payload.get("locale"), "zh-CN")

    def test_market_lexicon_phrase_bridge_is_weak_weight_helper(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "scoped:hyperframes-website",
                "name": "hyperframes-website",
                "folder": "hyperframes-website",
                "description": "Create hyperframes website animations and landing page motion systems for campaigns.",
                "path": "E:/Projects/test1/.agents/skills/hyperframes-website",
                "skillName": "hyperframes-website",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": "E:/Projects/test1",
                "projectId": "test1",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "en.json").write_text(
                '{"locale":"en","querySynonyms":{"wechat":["official account","article"]}}',
                encoding="utf-8",
            )
            (root / "zh-CN.json").write_text(
                '{"locale":"zh-CN","querySynonyms":{"公众号":["wechat","article"]}}',
                encoding="utf-8",
            )
            provider_dir = root / "market" / "skills-sh"
            provider_dir.mkdir(parents=True, exist_ok=True)
            (provider_dir / "manifest.json").write_text(
                json.dumps({"provider": "skills-sh", "locales": ["en", "zh-CN"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (provider_dir / "skills-sh-top1000.en.json").write_text(
                json.dumps(
                    {
                        "locale": "en",
                        "querySynonyms": {"hyperframes": ["website", "animation", "landing"]},
                        "primaryThemeSynonyms": {"content_media": ["hyperframes website"]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (provider_dir / "skills-sh-top1000.zh-CN.json").write_text(
                json.dumps(
                    {
                        "locale": "zh-CN",
                        "querySynonyms": {"超帧": ["hyperframes", "website", "animation", "landing"]},
                        "primaryThemeSynonyms": {"content_media": ["超帧网站"]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            registry = ExtensionLexiconRegistry(root_dir=root)
            old_registry = extensions_runtime_module._EXTENSION_LEXICON_REGISTRY
            try:
                extensions_runtime_module._EXTENSION_LEXICON_REGISTRY = registry
                extensions_runtime_module._apply_extension_lexicon_state(registry.ensure_fresh())
                extensions_runtime_module._QUERY_ANALYSIS_CACHE.clear()

                with patch.object(
                    service,
                    "_resolve_prefilter_policy",
                    return_value={
                        "enabled": False,
                        "available": False,
                        "mode": "lexical_shortlist",
                        "modelId": None,
                        "role": None,
                        "reason": "disabled",
                        "skills": {"stage1TopK": 10, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                        "mcp": {"stage1TopK": 10, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
                    },
                ), patch.object(
                    service,
                    "_resolve_skill_inventory",
                    return_value={"items": skills, "rootDescriptors": []},
                ):
                    bundle = service.build_contextual_route(
                        user_query="超帧",
                        available_tools=[],
                        loaded_agents=None,
                        skill_limit=5,
                        mcp_limit=0,
                        plugin_host_limit=0,
                    )
                    query_tokens, query_profile, _cache_hit, _lexicon_state, market_state = extensions_runtime_module._analyze_extensions_query("超帧")
            finally:
                extensions_runtime_module._EXTENSION_LEXICON_REGISTRY = old_registry
                extensions_runtime_module._apply_extension_lexicon_state(old_registry.ensure_fresh())
                extensions_runtime_module._QUERY_ANALYSIS_CACHE.clear()

        self.assertIn("hyperframes-website", bundle.selected_skill_names)
        self.assertTrue(bundle.candidate_summary.get("marketLexiconEnabled"))
        self.assertEqual(bundle.candidate_summary.get("marketLexiconHitTerms"), ["超帧"])
        self.assertGreater(int(bundle.candidate_summary.get("marketLexiconContributionScore") or 0), 0)
        self.assertIn("hyperframes", query_tokens)
        self.assertIn("website", query_tokens)
        self.assertIsNone(query_profile.get("artifactIntent"))
        self.assertEqual(market_state.get("matchedTerms"), ["超帧"])

    def test_workspace_wechat_article_skill_matches_chinese_alias_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "global:wechat-article-writer",
                "name": "wechat-article-writer",
                "folder": "wechat-article-writer",
                "description": "公众号文章自动化写作流程。当用户提到写公众号、微信文章、自媒体写作、内容创作时使用。",
                "path": "C:/Users/sunny/.agents/skills/wechat-article-writer",
                "skillName": "wechat-article-writer",
                "sourceType": "global",
                "visibility": "global",
            },
            {
                "skillId": "global:wechat-studio",
                "name": "wechat-studio",
                "folder": "wechat-studio",
                "description": "微信公众号内容生产工作台，覆盖选题、排版、封面图、发布前检查与运营协作。",
                "path": "C:/Users/sunny/.agents/skills/wechat-studio",
                "skillName": "wechat-studio",
                "sourceType": "global",
                "visibility": "global",
            },
            {
                "skillId": "scoped:wechat-account-articles",
                "name": "wechat-account-articles",
                "folder": "wechat-account-articles",
                "description": "End-to-end workflow for creating WeChat Official Account articles for open-source projects or tech concepts. Handles research, visual asset auditing, copywriting, and HTML generation.",
                "path": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                "skillName": "wechat-account-articles",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": "E:/Projects/test1",
                "projectId": "test1",
            },
            {
                "skillId": "global:doc-coauthoring",
                "name": "doc-coauthoring",
                "folder": "doc-coauthoring",
                "description": "Guide users through structured workflow for co-authoring documentation and proposals.",
                "path": "C:/Users/sunny/.agents/skills/doc-coauthoring",
                "skillName": "doc-coauthoring",
                "sourceType": "global",
                "visibility": "global",
            },
            {
                "skillId": "global:skill-creator",
                "name": "skill-creator",
                "folder": "skill-creator",
                "description": "Guide for creating effective skills and reusable agent workflows.",
                "path": "C:/Users/sunny/.agents/skills/skill-creator",
                "skillName": "skill-creator",
                "sourceType": "global",
                "visibility": "global",
            },
            {
                "skillId": "global:huashu-speech-coach",
                "name": "huashu-speech-coach",
                "folder": "huashu-speech-coach",
                "description": "演讲与分享教练，帮助准备培训、讲课、分享和演讲结构。",
                "path": "C:/Users/sunny/.agents/skills/huashu-speech-coach",
                "skillName": "huashu-speech-coach",
                "sourceType": "global",
                "visibility": "global",
            },
        ]

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical_shortlist",
                "modelId": None,
                "role": None,
                "reason": "disabled",
                "skills": {"stage1TopK": 10, "llmEnabled": True, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 20, "llmEnabled": True, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            for query in ("微信", "公众号", "公众号文章", "微信公众号", "公众号写作", "公众号发文", "开源项目公众号文章"):
                with self.subTest(query=query):
                    bundle = service.build_contextual_route(
                        user_query=query,
                        available_tools=[],
                        loaded_agents=None,
                        skill_limit=5,
                        mcp_limit=0,
                        plugin_host_limit=0,
                    )
                    self.assertIn("wechat-account-articles", bundle.selected_skill_names)
                    self.assertEqual(bundle.selected_skill_names[0], "wechat-account-articles")
                    self.assertIn("zh-CN", bundle.candidate_summary.get("lexiconLocales") or [])
                    self.assertIn("en", bundle.candidate_summary.get("lexiconLocales") or [])
                    self.assertEqual(
                        bundle.candidate_summary.get("primaryCanonicalFamily"),
                        "wechat" if query == "微信" else "wechat-account" if query in {"公众号", "微信公众号"} else "wechat-account-article" if query == "开源项目公众号文章" else "wechat-account-writing" if query in {"公众号写作", "公众号发文"} else "wechat-account-article",
                    )
                    self.assertTrue(bundle.candidate_summary.get("canonicalFamilies"))

            short_query_bundle = service.build_contextual_route(
                user_query="微信",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )
            self.assertTrue(short_query_bundle.candidate_summary.get("shortCanonicalNarrowing"))
            self.assertTrue(short_query_bundle.candidate_summary.get("shortCanonicalNarrowingApplied"))
            self.assertEqual(
                set(short_query_bundle.selected_skill_names),
                {"wechat-account-articles", "wechat-article-writer", "wechat-studio"},
            )
            self.assertNotIn("skill-creator", short_query_bundle.selected_skill_names)
            self.assertNotIn("huashu-speech-coach", short_query_bundle.selected_skill_names)

    def test_extensions_runtime_query_analysis_cache_hits_on_repeat_queries(self):
        service = ExtensionsRuntimeService()
        skills = [
            {
                "skillId": "scoped:wechat-account-articles",
                "name": "wechat-account-articles",
                "folder": "wechat-account-articles",
                "description": "End-to-end workflow for creating WeChat Official Account articles for open-source projects or tech concepts.",
                "path": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                "skillName": "wechat-account-articles",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": "E:/Projects/test1",
                "projectId": "test1",
            }
        ]
        extensions_runtime_module._QUERY_ANALYSIS_CACHE.clear()

        with patch.object(
            service,
            "_resolve_prefilter_policy",
            return_value={
                "enabled": False,
                "available": False,
                "mode": "lexical_shortlist",
                "modelId": None,
                "role": None,
                "reason": "disabled",
                "skills": {"stage1TopK": 10, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
                "mcp": {"stage1TopK": 10, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
            },
        ), patch.object(
            service,
            "_resolve_skill_inventory",
            return_value={"items": skills, "rootDescriptors": []},
        ):
            first_bundle = service.build_contextual_route(
                user_query="公众号文章",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )
            second_bundle = service.build_contextual_route(
                user_query="公众号文章",
                available_tools=[],
                loaded_agents=None,
                skill_limit=5,
                mcp_limit=0,
                plugin_host_limit=0,
            )

        self.assertFalse(first_bundle.candidate_summary.get("queryAnalysisCacheHit"))
        self.assertTrue(second_bundle.candidate_summary.get("queryAnalysisCacheHit"))

    def test_extensions_preview_reports_dirty_visible_roots_without_barrier(self):
        service = ExtensionsRuntimeService()
        root_path = SkillLoader._normalize_path(r"E:\Projects\test1\.agents\skills")
        visible_descriptors = [
            {
                "rootPath": root_path,
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": r"E:\Projects\test1",
                "workspaceId": "test1",
                "projectId": "project-test1",
            }
        ]
        skill_context = {
            "session_id": "session-project-test1",
            "explicit_workspace_id": "test1",
            "explicit_workspace_path": r"E:\Projects\test1",
            "explicit_project_id": "project-test1",
            "runtime_kind": "chat",
        }
        with patch.object(service, "_resolve_skill_loader_context", return_value=dict(skill_context)), patch.object(
            service,
            "_resolve_visible_skill_descriptors",
            return_value=list(visible_descriptors),
        ), patch.object(
            service,
            "_skill_inventory_status",
            return_value={
                "startupState": "ready",
                "snapshotFreshness": "live",
                "backgroundRefreshInProgress": False,
                "skillCount": 37,
            },
        ), patch.object(
            SkillLoader,
            "_dirty_root_paths_for_descriptors",
            return_value=[root_path],
        ), patch.object(
            SkillLoader,
            "refresh_root_descriptors_if_changed",
        ) as refresh_mock:
            result = service._apply_inventory_freshness_mode(
                freshness_mode=extensions_runtime_module._INVENTORY_FRESHNESS_PREVIEW,
                reason="prefilter_preview",
                include_scoped=True,
                session_id="session-project-test1",
                explicit_workspace_id="test1",
                explicit_workspace_path=r"E:\Projects\test1",
                explicit_project_id="project-test1",
                runtime_kind="chat",
            )

        self.assertFalse(result.get("inventoryBarrierApplied"))
        self.assertFalse(result.get("inventoryBarrierTimedOut"))
        self.assertEqual(result.get("inventoryBarrierWaitMs"), 0.0)
        self.assertEqual(result.get("inventoryReadyState"), "ready")
        self.assertEqual(result.get("snapshotFreshness"), "live")
        self.assertEqual(result.get("dirtyVisibleRoots"), [root_path])
        self.assertEqual(result.get("excludeRootPaths"), set())
        refresh_mock.assert_not_called()

    def test_guarded_inventory_timeout_fail_closes_dirty_visible_roots(self):
        service = ExtensionsRuntimeService()
        root_path = SkillLoader._normalize_path(r"E:\Projects\test1\.agents\skills")
        visible_descriptors = [
            {
                "rootPath": root_path,
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": r"E:\Projects\test1",
                "workspaceId": "test1",
                "projectId": "project-test1",
            }
        ]
        skill_context = {
            "session_id": "session-project-test1",
            "explicit_workspace_id": "test1",
            "explicit_workspace_path": r"E:\Projects\test1",
            "explicit_project_id": "project-test1",
            "runtime_kind": "chat",
        }
        dirty_side_effect = [[root_path], [root_path]]
        with patch.object(service, "_resolve_skill_loader_context", return_value=dict(skill_context)), patch.object(
            service,
            "_resolve_visible_skill_descriptors",
            return_value=list(visible_descriptors),
        ), patch.object(
            service,
            "_skill_inventory_status",
            return_value={
                "startupState": "ready",
                "snapshotFreshness": "live",
                "backgroundRefreshInProgress": False,
                "skillCount": 37,
            },
        ), patch.object(
            SkillLoader,
            "_dirty_root_paths_for_descriptors",
            side_effect=dirty_side_effect,
        ), patch.object(
            SkillLoader,
            "refresh_root_descriptors_if_changed",
            return_value={"changed": False, "timedOut": True, "timedOutRoots": [root_path]},
        ) as refresh_mock:
            result = service._apply_inventory_freshness_mode(
                freshness_mode=extensions_runtime_module._INVENTORY_FRESHNESS_GUARDED,
                reason="supervisor_route",
                include_scoped=True,
                session_id="session-project-test1",
                explicit_workspace_id="test1",
                explicit_workspace_path=r"E:\Projects\test1",
                explicit_project_id="project-test1",
                runtime_kind="chat",
            )

        self.assertTrue(result.get("inventoryBarrierApplied"))
        self.assertTrue(result.get("inventoryBarrierTimedOut"))
        self.assertEqual(result.get("dirtyVisibleRoots"), [root_path])
        self.assertEqual(result.get("excludeRootPaths"), {root_path})
        self.assertEqual(result.get("waitBudgetMs"), 800)
        self.assertGreater(result.get("inventoryBarrierWaitMs") or 0, 0)
        refresh_mock.assert_called_once()
        refresh_kwargs = refresh_mock.call_args.kwargs
        self.assertEqual(refresh_kwargs.get("compare_existing"), False)
        self.assertGreater(int(refresh_kwargs.get("timeout_ms") or 0), 0)

    def test_skill_loader_visible_inventory_cache_hits_on_repeat_scoped_inventory(self):
        original_registry = SkillLoader._skills_registry
        original_manifest = SkillLoader._skills_manifest
        original_root_descriptors = SkillLoader._skills_root_descriptors
        original_root_signature = SkillLoader._skills_root_signature
        original_fingerprint = SkillLoader._skills_fingerprint
        original_revision = SkillLoader._skills_revision
        original_root_states = SkillLoader._root_inventory_states
        original_visible_cache = SkillLoader._visible_inventory_cache
        original_dirty_root_paths = SkillLoader._dirty_root_paths
        try:
            global_descriptor = {
                "rootPath": "C:/Users/sunny/.agents/skills",
                "sourceType": "global",
                "visibility": "global",
            }
            scoped_descriptor = {
                "rootPath": "E:/Projects/test1/.agents/skills",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": "E:/Projects/test1",
                "workspaceId": "test1",
                "projectId": "project-test1",
            }
            SkillLoader._skills_registry = {
                "global:wechat-studio": {
                    "skillId": "global:wechat-studio",
                    "skillName": "wechat-studio",
                    "instructionPath": "C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md",
                    "skillRoot": "C:/Users/sunny/.agents/skills/wechat-studio",
                    "sourceType": "global",
                    "visibility": "global",
                    "rootPath": "C:/Users/sunny/.agents/skills",
                },
                "scoped:wechat-account-articles": {
                    "skillId": "scoped:wechat-account-articles",
                    "skillName": "wechat-account-articles",
                    "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                    "skillRoot": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                    "workspacePath": "E:/Projects/test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                    "rootPath": "E:/Projects/test1/.agents/skills",
                },
            }
            SkillLoader._skills_manifest = {}
            SkillLoader._skills_root_descriptors = [global_descriptor, scoped_descriptor]
            SkillLoader._skills_root_signature = SkillLoader._root_descriptors_signature([global_descriptor, scoped_descriptor])
            SkillLoader._skills_fingerprint = "skills:cache"
            SkillLoader._skills_revision = "skills:cache"
            SkillLoader._visible_inventory_cache = {}
            SkillLoader._dirty_root_paths = set()
            SkillLoader._rebuild_root_inventory_states_from_registry()

            with patch.object(
                SkillLoader,
                "_resolve_inventory_descriptors",
                return_value=[global_descriptor, scoped_descriptor],
            ):
                first_inventory = SkillLoader.get_inventory(force_refresh=False, include_scoped=True)
                second_inventory = SkillLoader.get_inventory(force_refresh=False, include_scoped=True)

            self.assertFalse(first_inventory.get("visibleRegistryCacheHit"))
            self.assertTrue(second_inventory.get("visibleRegistryCacheHit"))
            self.assertEqual(
                {item.get("skillName") for item in list(second_inventory.get("items") or [])},
                {"wechat-studio", "wechat-account-articles"},
            )
            self.assertEqual(second_inventory.get("dirtyVisibleRoots"), [])
        finally:
            SkillLoader._skills_registry = original_registry
            SkillLoader._skills_manifest = original_manifest
            SkillLoader._skills_root_descriptors = original_root_descriptors
            SkillLoader._skills_root_signature = original_root_signature
            SkillLoader._skills_fingerprint = original_fingerprint
            SkillLoader._skills_revision = original_revision
            SkillLoader._root_inventory_states = original_root_states
            SkillLoader._visible_inventory_cache = original_visible_cache
            SkillLoader._dirty_root_paths = original_dirty_root_paths

    def test_skill_loader_root_aware_reload_keeps_unrelated_visible_cache(self):
        original_registry = SkillLoader._skills_registry
        original_manifest = SkillLoader._skills_manifest
        original_root_descriptors = SkillLoader._skills_root_descriptors
        original_root_signature = SkillLoader._skills_root_signature
        original_fingerprint = SkillLoader._skills_fingerprint
        original_revision = SkillLoader._skills_revision
        original_root_states = SkillLoader._root_inventory_states
        original_visible_cache = SkillLoader._visible_inventory_cache
        original_dirty_root_paths = SkillLoader._dirty_root_paths
        try:
            global_descriptor = {"rootPath": "C:/Users/sunny/.agents/skills", "sourceType": "global", "visibility": "global"}
            scoped_descriptor = {
                "rootPath": "E:/Projects/test1/.agents/skills",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspacePath": "E:/Projects/test1",
                "workspaceId": "test1",
                "projectId": "project-test1",
            }
            SkillLoader._skills_registry = {
                "global:wechat-studio": {
                    "skillId": "global:wechat-studio",
                    "skillName": "wechat-studio",
                    "instructionPath": "C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md",
                    "skillRoot": "C:/Users/sunny/.agents/skills/wechat-studio",
                    "sourceType": "global",
                    "visibility": "global",
                    "rootPath": "C:/Users/sunny/.agents/skills",
                },
                "scoped:wechat-account-articles": {
                    "skillId": "scoped:wechat-account-articles",
                    "skillName": "wechat-account-articles",
                    "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                    "skillRoot": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                    "workspacePath": "E:/Projects/test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                    "rootPath": "E:/Projects/test1/.agents/skills",
                },
            }
            global_root_path = SkillLoader._normalize_path(global_descriptor["rootPath"])
            scoped_root_path = SkillLoader._normalize_path(scoped_descriptor["rootPath"])
            SkillLoader._skills_manifest = {
                SkillLoader._normalize_path("C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md"): {
                    "mtimeNs": 1,
                    "size": 10,
                    "rootPath": global_descriptor["rootPath"],
                    "instructionPath": "C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md",
                    "folder": "wechat-studio",
                },
                SkillLoader._normalize_path("E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md"): {
                    "mtimeNs": 1,
                    "size": 10,
                    "rootPath": scoped_descriptor["rootPath"],
                    "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                    "folder": "wechat-account-articles",
                },
            }
            SkillLoader._skills_root_descriptors = [global_descriptor, scoped_descriptor]
            SkillLoader._skills_root_signature = SkillLoader._root_descriptors_signature([global_descriptor, scoped_descriptor])
            SkillLoader._skills_fingerprint = "skills:old"
            SkillLoader._skills_revision = "skills:old"
            SkillLoader._rebuild_root_inventory_states_from_registry()
            SkillLoader._root_inventory_states[global_root_path]["rootRevision"] = "root:global-old"
            SkillLoader._root_inventory_states[scoped_root_path]["rootRevision"] = "root:scoped-old"
            global_cache_key = SkillLoader._visible_inventory_cache_key([global_descriptor])
            scoped_cache_key = SkillLoader._visible_inventory_cache_key([global_descriptor, scoped_descriptor])
            SkillLoader._visible_inventory_cache = {
                global_cache_key: {"rootDescriptors": [global_descriptor], "items": [{"skillName": "wechat-studio"}]},
                scoped_cache_key: {
                    "rootDescriptors": [global_descriptor, scoped_descriptor],
                    "items": [{"skillName": "wechat-studio"}, {"skillName": "wechat-account-articles"}],
                },
            }
            SkillLoader._dirty_root_paths = {scoped_root_path}
            scoped_manifest = {
                SkillLoader._normalize_path("E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md"): {
                    "mtimeNs": 2,
                    "size": 12,
                    "rootPath": scoped_descriptor["rootPath"],
                    "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                    "folder": "wechat-account-articles",
                }
            }
            with patch.object(
                SkillLoader,
                "_discovery_root_descriptors",
                return_value=[global_descriptor, scoped_descriptor],
            ), patch.object(
                SkillLoader,
                "_compute_root_manifest",
                side_effect=lambda descriptor: (
                    {SkillLoader._normalize_path("C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md"): dict(SkillLoader._skills_manifest[SkillLoader._normalize_path('C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md')])}
                    if SkillLoader._normalize_path(descriptor.get("rootPath")) == global_root_path
                    else dict(scoped_manifest)
                ),
            ), patch.object(
                SkillLoader,
                "_root_manifest_fingerprint",
                side_effect=lambda descriptor, manifest: "root:global-old"
                if SkillLoader._normalize_path(descriptor.get("rootPath")) == global_root_path
                else "root:scoped-new",
            ), patch.object(
                SkillLoader,
                "_scan_single_root_descriptor",
                side_effect=lambda descriptor: (
                    {"global:wechat-studio": dict(SkillLoader._skills_registry["global:wechat-studio"])}
                    if SkillLoader._normalize_path(descriptor.get("rootPath")) == global_root_path
                    else {"scoped:wechat-account-articles": dict(SkillLoader._skills_registry["scoped:wechat-account-articles"])}
                ),
            ), patch.object(
                SkillLoader,
                "_persist_cache",
            ):
                change = SkillLoader.reload_if_changed()

            self.assertEqual(set(change.get("changedRoots") or []), {scoped_root_path})
            self.assertIn(global_cache_key, SkillLoader._visible_inventory_cache)
            self.assertNotIn(scoped_cache_key, SkillLoader._visible_inventory_cache)
        finally:
            SkillLoader._skills_registry = original_registry
            SkillLoader._skills_manifest = original_manifest
            SkillLoader._skills_root_descriptors = original_root_descriptors
            SkillLoader._skills_root_signature = original_root_signature
            SkillLoader._skills_fingerprint = original_fingerprint
            SkillLoader._skills_revision = original_revision
            SkillLoader._root_inventory_states = original_root_states
            SkillLoader._visible_inventory_cache = original_visible_cache
            SkillLoader._dirty_root_paths = original_dirty_root_paths

    def test_supervisor_route_fail_closes_dirty_visible_root_on_barrier_timeout(self):
        service = ExtensionsRuntimeService()
        captured_inventory_kwargs: list[dict[str, object]] = []
        project_root = SkillLoader._normalize_path(r"E:\Projects\test1\.agents\skills")
        inventory_freshness = {
            "skillContext": {
                "session_id": "session-project-test1",
                "explicit_workspace_id": "test1",
                "explicit_workspace_path": r"E:\Projects\test1",
                "explicit_project_id": "project-test1",
                "runtime_kind": "chat",
            },
            "visibleDescriptors": [
                {"rootPath": "C:/Users/sunny/.agents/skills", "sourceType": "global", "visibility": "global"},
                {
                    "rootPath": project_root,
                    "sourceType": "scoped_workspace",
                    "visibility": "scoped",
                    "workspacePath": r"E:\Projects\test1",
                    "workspaceId": "test1",
                    "projectId": "project-test1",
                },
            ],
            "visibleRootSignature": "visible:global+project-test1",
            "visibleRootRevisionKey": "visible-rev:test1",
            "inventoryReadyState": "refreshing",
            "snapshotFreshness": "cached",
            "inventoryBarrierApplied": True,
            "inventoryBarrierWaitMs": 800,
            "inventoryBarrierTimedOut": True,
            "dirtyVisibleRoots": [project_root],
            "excludeRootPaths": {project_root},
            "waitBudgetMs": 800,
        }
        prefilter_policy = {
            "enabled": False,
            "available": False,
            "mode": "lexical_only",
            "modelId": "",
            "role": "",
            "reason": "test",
            "skills": {"stage1Enabled": True, "stage1TopK": 10, "llmEnabled": False, "stage2TopK": 5, "llmTimeoutSeconds": 5},
            "mcp": {"stage1Enabled": True, "stage1TopK": 10, "llmEnabled": False, "stage2TopK": 2, "llmTimeoutSeconds": 5},
        }

        def fake_resolve_skill_inventory(**kwargs):  # noqa: ANN003
            captured_inventory_kwargs.append(dict(kwargs))
            excluded = {SkillLoader._normalize_path(item) for item in list(kwargs.get("exclude_root_paths") or set())}
            items = [
                {
                    "skillId": "global:wechat-studio",
                    "skillName": "wechat-studio",
                    "name": "wechat-studio",
                    "folder": "wechat-studio",
                    "description": "微信公众号运营工作台",
                    "path": "C:/Users/sunny/.agents/skills/wechat-studio",
                    "skillRoot": "C:/Users/sunny/.agents/skills/wechat-studio",
                    "instructionPath": "C:/Users/sunny/.agents/skills/wechat-studio/SKILL.md",
                    "sourceType": "global",
                    "visibility": "global",
                }
            ]
            if project_root not in excluded:
                items.append(
                    {
                        "skillId": "scoped:wechat-account-articles",
                        "skillName": "wechat-account-articles",
                        "name": "wechat-account-articles",
                        "folder": "wechat-account-articles",
                        "description": "微信公众号文章调研、选题、写作与复盘工作流。",
                        "path": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                        "skillRoot": "E:/Projects/test1/.agents/skills/wechat-account-articles",
                        "instructionPath": "E:/Projects/test1/.agents/skills/wechat-account-articles/SKILL.md",
                        "sourceType": "scoped_workspace",
                        "visibility": "scoped",
                        "workspacePath": r"E:\Projects\test1",
                        "workspaceId": "test1",
                        "projectId": "project-test1",
                    }
                )
            return {
                "items": items,
                "rootDescriptors": list(inventory_freshness.get("visibleDescriptors") or []),
                "revision": "skills:scoped:test1",
                "visibleRootSignature": "visible:global+project-test1",
                "visibleRootRevisionKey": "visible-rev:test1",
                "changedRoots": [project_root],
                "scopedRefreshMode": "delta",
                "dirtyVisibleRoots": [project_root],
                "inventoryReadyState": "refreshing",
                "snapshotFreshness": "cached",
            }

        token = service.bind_execution_context(
            session_id="session-project-test1",
            workspace_id="test1",
            workspace_path=r"E:\Projects\test1",
            project_id="project-test1",
            runtime_kind="chat",
        )
        try:
            with patch.object(service, "_apply_inventory_freshness_mode", return_value=dict(inventory_freshness)), patch.object(
                service,
                "_resolve_skill_inventory",
                side_effect=fake_resolve_skill_inventory,
            ), patch.object(
                service,
                "_resolve_prefilter_policy",
                return_value=prefilter_policy,
            ):
                bundle = service.build_supervisor_route(
                    user_query="写一个公众号文章提纲",
                    supervisor_tools=[],
                    loaded_agents=[],
                )
        finally:
            service.reset_execution_context(token)

        self.assertEqual(len(captured_inventory_kwargs), 1)
        self.assertEqual(captured_inventory_kwargs[0].get("exclude_root_paths"), {project_root})
        self.assertNotIn("wechat-account-articles", bundle.selected_skill_names)
        self.assertIn("wechat-studio", bundle.selected_skill_names)
        self.assertTrue(bundle.candidate_summary.get("inventoryBarrierTimedOut"))
        self.assertEqual(bundle.candidate_summary.get("dirtyVisibleRoots"), [project_root])
        self.assertEqual(bundle.candidate_summary.get("snapshotFreshness"), "cached")


class MCPManagerDeltaReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reload_if_changed_only_restarts_changed_servers(self):
        manager = MCPManager()
        stable_config = {"command": "node", "args": ["stable.js"]}
        changed_old_config = {"command": "node", "args": ["old.js"]}
        changed_new_config = {"command": "node", "args": ["new.js"]}
        added_config = {"command": "node", "args": ["added.js"]}
        manager._server_config_fingerprints = {
            "stable": manager._server_config_fingerprint("stable", stable_config),
            "changed": manager._server_config_fingerprint("changed", changed_old_config),
            "removed": manager._server_config_fingerprint("removed", {"command": "node", "args": ["removed.js"]}),
        }
        manager._server_state = {
            "stable": {"status": "connected"},
            "changed": {"status": "connected"},
            "removed": {"status": "connected"},
        }
        manager._server_tools = {
            "stable": [_FakeTool("stable_tool", "stable", "stable")],
            "changed": [_FakeTool("changed_tool", "changed", "changed")],
            "removed": [_FakeTool("removed_tool", "removed", "removed")],
        }
        manager.tools = [
            *manager._server_tools["stable"],
            *manager._server_tools["changed"],
            *manager._server_tools["removed"],
        ]
        started: list[str] = []
        stopped: list[str] = []

        async def fake_start(name, srv_config):  # noqa: ANN001
            started.append(name)
            manager._server_tools[name] = [_FakeTool(f"{name}_tool", "tool", name)]
            manager._set_server_state(name, status="connected", toolCount=1)

        async def fake_stop(name, *, cancel=False):  # noqa: ANN001
            stopped.append(name)

        with patch("runtimes.extensions.mcp.client.storage.get_mcp_config", return_value={
            "mcpServers": {
                "stable": stable_config,
                "changed": changed_new_config,
                "added": added_config,
            }
        }), patch.object(manager, "_start_server", side_effect=fake_start), patch.object(
            manager, "_stop_server_task", side_effect=fake_stop
        ):
            result = await manager.reload_if_changed()

        self.assertTrue(result.get("changed"))
        self.assertEqual(set(started), {"changed", "added"})
        self.assertNotIn("stable", started)
        self.assertIn("removed", stopped)
        self.assertEqual(set((result.get("mcpChangedServers") or {}).get("added") or []), {"added"})
        self.assertEqual(set((result.get("mcpChangedServers") or {}).get("updated") or []), {"changed"})
        self.assertEqual(set((result.get("mcpChangedServers") or {}).get("removed") or []), {"removed"})
        self.assertIn("stable", manager._server_config_fingerprints)
        self.assertNotIn("removed", manager._server_config_fingerprints)


if __name__ == "__main__":
    unittest.main()
