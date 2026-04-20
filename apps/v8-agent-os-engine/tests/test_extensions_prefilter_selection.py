from __future__ import annotations

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
from runtimes.extensions import runtime as extensions_runtime_module
from runtimes.extensions.runtime import ExtensionsRuntimeService
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
                "triggers": [],
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


if __name__ == "__main__":
    unittest.main()
