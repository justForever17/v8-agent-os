from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core import llm_tree_prefilter
from runtimes.extensions import runtime as extensions_runtime_module
from runtimes.extensions.runtime import ExtensionsRuntimeService


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
            [item["key"] for item in captured_skill_families],
            ["C:/skills/remotion-video", "C:/skills/huashu-nuwa"],
        )
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


if __name__ == "__main__":
    unittest.main()
