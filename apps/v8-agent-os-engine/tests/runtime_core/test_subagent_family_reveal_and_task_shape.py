from __future__ import annotations

from core.delegation_broker import (
    choose_best_local_agent_with_diagnostics,
    normalize_task_brief,
    reveal_subagent_family,
)
from core.task_shape_classifier import classify_task_shape
from api.models import ChatRequest
from graph.agent_factories import _format_delegated_task_contract
from graph.parallel_support import _compact_transcript, _extract_tool_names
from langchain_core.messages import AIMessage, ToolMessage
from runtimes.chat.runtime import ChatRuntime
from types import SimpleNamespace
from unittest.mock import patch


def _agent(agent_id: str, family: str, ops: list[str]) -> dict:
    return {
        "id": agent_id,
        "name": agent_id,
        "isEnabled": True,
        "description": "specialist",
        "capabilitySnapshot": {
            "specialistFamily": family,
            "agentClass": "executor",
            "domainTags": [family],
            "operationCapabilities": ops,
        },
    }


def test_remotion_is_project_coding_with_creative_media_secondary() -> None:
    hint = classify_task_shape("帮我用 Remotion 做一个短视频")

    assert hint["primaryTaskShape"] == "project_coding"
    assert "creative_media" in hint["secondaryTaskShapes"]
    assert hint["suggestedFamilies"][0] == "engineering"
    assert hint["autoRevealRecommendation"]["eligible"] is True
    assert hint["autoRevealRecommendation"]["families"] == ["engineering"]
    assert hint["boundaryDecision"]["primaryRuntime"] == "engineering"
    assert hint["boundaryDecision"]["executionMode"] == "code_video_runtime"
    assert "creative_media" in hint["boundaryDecision"]["supportingRuntimes"]
    assert hint["policy"] == "hint_only_conservative_auto_reveal_recommendation_no_grant"


def test_seedance_provider_request_is_creative_media_hint_only() -> None:
    hint = classify_task_shape("用 Seedance 2.0 生成一个视频镜头")

    assert hint["primaryTaskShape"] == "creative_media"
    assert "creative_media" in hint["suggestedFamilies"]
    assert "creative_media.core" in hint["optionalRuntimeGrants"]
    assert hint["autoRevealRecommendation"]["eligible"] is True
    assert hint["autoRevealRecommendation"]["families"] == ["creative_media"]
    assert hint["boundaryDecision"]["primaryRuntime"] == "creative_media"
    assert hint["boundaryDecision"]["executionMode"] == "provider_video_generation"


def test_research_request_recommends_research_family_and_runtime_grant() -> None:
    hint = classify_task_shape("联网调研最新的 OpenAI API 官方文档，给出引用来源")

    assert hint["primaryTaskShape"] == "research"
    assert hint["suggestedFamilies"][0] == "research"
    assert "research.core" in hint["optionalRuntimeGrants"]
    assert hint["autoRevealRecommendation"]["families"] == ["research"]


def test_project_coding_with_latest_docs_keeps_engineering_primary_and_research_secondary() -> None:
    hint = classify_task_shape("修复 Next.js 项目问题，先查最新官方文档再改代码")

    assert hint["primaryTaskShape"] == "project_coding"
    assert hint["suggestedFamilies"][0] == "engineering"
    assert "research" in hint["secondaryTaskShapes"]
    assert "research.core" in hint["optionalRuntimeGrants"]


def test_research_plus_new_frontend_app_is_project_coding_with_research_secondary() -> None:
    hint = classify_task_shape(
        "调研狼人杀的玩法，以及配套狼人杀风格的前端界面以及图标，做一个AI狼人杀web应用，可以接入6个不同供应商的LLM"
    )

    assert hint["primaryTaskShape"] == "project_coding"
    assert hint["suggestedFamilies"][0] == "engineering"
    assert "research" in hint["secondaryTaskShapes"]
    assert "research.core" in hint["optionalRuntimeGrants"]


def test_research_plus_game_ui_design_is_project_coding_with_research_secondary() -> None:
    hint = classify_task_shape(
        "联网调研中国象棋无法和细节，思考如何接入一款 AI VS 人类或者纯 AI 互拍的象棋游戏，需要有精美的前端动态 UI"
    )

    assert hint["primaryTaskShape"] == "project_coding"
    assert hint["reason"] in {"research_plus_project_build_intent", "engineering_action_terms"}
    assert hint["suggestedFamilies"][0] == "engineering"
    assert "research" in hint["secondaryTaskShapes"]
    assert "research.core" in hint["optionalRuntimeGrants"]


def test_research_plus_subagent_orchestration_is_multi_runtime_project_coding() -> None:
    hint = classify_task_shape(
        "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。"
    )

    assert hint["primaryTaskShape"] == "project_coding"
    assert hint["reason"] == "multi_runtime_orchestration_terms"
    assert "research" in hint["secondaryTaskShapes"]
    assert "delegation" in hint["secondaryTaskShapes"]
    assert "research.core" in hint["optionalRuntimeGrants"]
    assert "delegation.recursive" in hint["optionalRuntimeGrants"]
    assert any(signal.startswith("delegation_action:") for signal in hint["signals"])


def test_multilingual_aliases_feed_task_shape_classifier() -> None:
    remotion_hint = classify_task_shape("Implementa un vídeo con Remotion")
    seedance_hint = classify_task_shape("Seedanceで動画を生成して")

    assert remotion_hint["primaryTaskShape"] == "project_coding"
    assert remotion_hint["autoRevealRecommendation"]["families"] == ["engineering"]
    assert seedance_hint["primaryTaskShape"] == "creative_media"
    assert seedance_hint["autoRevealRecommendation"]["families"] == ["creative_media"]


def test_output_modality_only_does_not_auto_reveal() -> None:
    hint = classify_task_shape("做一个视频")

    assert hint["primaryTaskShape"] == "creative_media"
    assert "output_modality_only" in hint["ambiguityFlags"]
    assert hint["autoRevealRecommendation"]["eligible"] is False
    assert hint["boundaryDecision"]["askUserNeeded"] is True
    assert hint["boundaryDecision"]["executionMode"] == "clarify_video_route"


def test_explainer_video_prefers_engineering_with_creative_media_support() -> None:
    hint = classify_task_shape("帮我做一个 2 分钟的科普讲解视频，主题是量子纠缠")

    assert hint["boundaryDecision"]["primaryRuntime"] == "engineering"
    assert hint["boundaryDecision"]["executionMode"] == "code_video_runtime"
    assert hint["boundaryDecision"]["reason"] == "explainer_or_course_video_prefers_editable_code_timeline"
    assert "creative_media" in hint["boundaryDecision"]["supportingRuntimes"]
    assert "creative_media_as_primary_unless_provider_named" in hint["boundaryDecision"]["forbiddenRoutes"]


def test_negated_rpa_in_explainer_video_does_not_override_engineering_route() -> None:
    hint = classify_task_shape("请设计一个 60 秒科普讲解视频，不要调用桌面或 RPA，判断工程链路还是 Creative Media。")

    assert hint["boundaryDecision"]["primaryRuntime"] == "engineering"
    assert hint["boundaryDecision"]["executionMode"] == "code_video_runtime"
    assert "video:explainer_code_video" in hint["boundaryDecision"]["signals"]
    assert "desktop:rpa_reusable" not in hint["boundaryDecision"]["signals"]


def test_literal_terminal_request_prefers_native_command_not_computer_use() -> None:
    hint = classify_task_shape("打开终端帮我安装 huashu-nuwa skill")

    assert hint["boundaryDecision"]["primaryRuntime"] == "engineering"
    assert hint["boundaryDecision"]["executionMode"] == "native_terminal_command"
    assert "computer_use_for_literal_terminal_only" in hint["boundaryDecision"]["forbiddenRoutes"]
    assert hint["boundaryDecision"]["routeCorrections"][0]["to"] == "run_system_command_or_command_session_broker"


def test_visible_gui_terminal_request_routes_to_computer_use() -> None:
    hint = classify_task_shape("让我看着真实终端窗口启动 Claude")

    assert hint["boundaryDecision"]["primaryRuntime"] == "computer_use"
    assert hint["boundaryDecision"]["executionMode"] == "gui_terminal_session"


def test_explicit_computer_use_contract_ignores_negated_shell_rpa_and_skips_engineering_workspace() -> None:
    hint = classify_task_shape(
        "这是 Computer Use runtime 验收：启动 QQ音乐，搜索‘晴天 周杰伦’，点击播放并关闭进程。"
        "不得使用 shell 或 RPA，不要用测试说明替代真实桌面操作。"
    )
    prepared = SimpleNamespace(
        task_shape_hint=hint,
        explicit_engineering_requested=False,
        engineering_mode="force",
        engineering_trigger_decision={"active": True},
    )
    chat_run = SimpleNamespace(prepared=prepared)

    assert hint["boundaryDecision"]["primaryRuntime"] == "computer_use"
    assert hint["boundaryDecision"]["executionMode"] == "agent_browser_or_desktop_gui"
    assert "rpa_explicitly_excluded" in hint["boundaryDecision"]["forbiddenRoutes"]
    assert ChatRuntime._supervisor_direct_scope_requires_engineering_route(chat_run) is False


def test_safety_eval_language_does_not_trigger_engineering_or_media_from_single_cjk_terms() -> None:
    hint = classify_task_shape("如果没有证据，请明确说不知道；不要声称 research/subagent 已成功。")

    assert hint["primaryTaskShape"] not in {"project_coding", "creative_media"}
    assert "media_output:声" not in hint.get("signals", [])
    assert "engineering_action:做" not in hint.get("signals", [])


def test_simple_writing_route_stays_direct_supervisor() -> None:
    hint = classify_task_shape("帮我写一段产品说明")

    assert hint["primaryTaskShape"] == "writing"
    assert hint["writingRoute"]["mode"] == "direct_supervisor"
    assert hint["writingRoute"]["needsClarification"] is False


def test_negated_runtime_terms_do_not_force_writing_into_runtime() -> None:
    hint = classify_task_shape("只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。")

    assert hint["primaryTaskShape"] == "writing"
    assert hint["writingRoute"]["mode"] == "direct_supervisor"
    assert "research" not in hint["secondaryTaskShapes"]
    assert "project_coding" not in hint["secondaryTaskShapes"]
    assert not any(str(item).startswith("code_action:") for item in hint["signals"])
    assert not any(str(item).startswith("research_") for item in hint["signals"])


def test_ambiguous_document_writing_requires_clarification() -> None:
    hint = classify_task_shape("帮我写一篇文档")

    assert hint["primaryTaskShape"] == "writing"
    assert hint["writingRoute"]["mode"] == "ask_user_clarify"
    assert hint["writingRoute"]["needsClarification"] is True
    assert hint["writingRoute"]["clarificationOptions"] == ["direct_body", "research_backed", "save_as_file"]


def test_source_backed_writing_routes_research_then_write() -> None:
    hint = classify_task_shape("先调研官方资料，然后写一份报告，带来源")

    assert hint["primaryTaskShape"] == "writing"
    assert "research" in hint["secondaryTaskShapes"]
    assert hint["writingRoute"]["mode"] == "research_then_write"
    assert hint["writingRoute"]["requiresResearch"] is True


def test_source_backed_writing_with_negated_file_save_stays_research_then_write() -> None:
    hint = classify_task_shape("联网查找 2-3 个来源，写一段关于 V8OS runtime-first 设计价值的短报告，必须保留来源，不保存文件。")

    assert hint["primaryTaskShape"] == "writing"
    assert "research" in hint["secondaryTaskShapes"]
    assert "project_coding" not in hint["secondaryTaskShapes"]
    assert hint["writingRoute"]["mode"] == "research_then_write"
    assert hint["writingRoute"]["requiresArtifact"] is False


def test_file_backed_writing_routes_artifact_runtime() -> None:
    hint = classify_task_shape("写一篇方案并保存到 docs/plan.md")

    assert hint["primaryTaskShape"] == "writing"
    assert hint["writingRoute"]["mode"] == "artifact_runtime"
    assert hint["writingRoute"]["requiresArtifact"] is True
    assert hint["writingRoute"]["recommendedFamily"] == "engineering"


def test_skill_driven_writing_requires_skill_subagent() -> None:
    hint = classify_task_shape("用 doc-coauthoring skill 写一篇技术方案")

    assert hint["primaryTaskShape"] == "writing"
    assert hint["writingRoute"]["mode"] == "skill_subagent"
    assert hint["writingRoute"]["requiresSkillExecution"] is True
    assert hint["writingRoute"]["skillName"] == "doc-coauthoring"
    assert hint["writingRoute"]["firstActionTool"] == "fetch_skill_instructions"


def test_skill_direct_usage_ignores_negated_skill_creation() -> None:
    hint = classify_task_shape("使用 huashu-nuwa skill 输出执行计划，不写文件、不创建新 skill。")

    assert hint["primaryTaskShape"] == "writing"
    assert "delegation" not in hint["secondaryTaskShapes"]
    assert hint["writingRoute"]["mode"] == "direct_supervisor"
    assert hint["writingRoute"]["requiresSkillExecution"] is True
    assert hint["writingRoute"]["skillName"] == "huashu-nuwa"
    assert hint["writingRoute"]["firstActionTool"] == "fetch_skill_instructions"


def test_existing_perspective_skill_answer_stays_direct_supervisor() -> None:
    hint = classify_task_shape("用 sanyueqi-perspective skill 回答：朋友迷路害怕时应该怎么安慰？")

    assert hint["primaryTaskShape"] == "writing"
    assert "research" not in hint["secondaryTaskShapes"]
    assert "project_coding" not in hint["secondaryTaskShapes"]
    assert "delegation" not in hint["secondaryTaskShapes"]
    assert hint["writingRoute"]["mode"] == "direct_supervisor"
    assert hint["writingRoute"]["requiresSkillExecution"] is True
    assert hint["writingRoute"]["skillName"] == "sanyueqi-perspective"
    assert hint["writingRoute"]["firstActionTool"] == "fetch_skill_instructions"


def test_huashu_nuwa_skill_creation_still_requires_runtime_artifact() -> None:
    hint = classify_task_shape("使用 huashu-nuwa skill 调研三月七并生成 skill，写入 .agents/skills/sanyueqi-perspective。")

    assert hint["primaryTaskShape"] == "writing"
    assert "research" in hint["secondaryTaskShapes"]
    assert "project_coding" in hint["secondaryTaskShapes"]
    assert hint["writingRoute"]["mode"] == "skill_subagent"
    assert hint["writingRoute"]["requiresResearch"] is True
    assert hint["writingRoute"]["requiresArtifact"] is True
    assert hint["writingRoute"]["preferredAgentId"] == "skill-workflow-curator"


def test_family_hint_filters_local_agent_selection() -> None:
    agents = [
        _agent("eng", "engineering", ["implement", "code"]),
        _agent("media", "creative_media", ["implement", "video"]),
    ]
    task = normalize_task_brief(
        {
            "goal": "Implement a Remotion scene",
            "requiredCapabilities": ["implement"],
            "familyHint": "engineering",
        }
    )

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected and selected["id"] == "eng"
    assert diagnostics["targetFamily"] == "engineering"
    assert "familyHint:engineering" in diagnostics["matchSignals"]


def test_writing_family_hint_selects_writing_agent() -> None:
    agents = [
        _agent("eng", "engineering", ["write", "fetch_skill_instructions"]),
        _agent("writer", "writing", ["write", "fetch_skill_instructions"]),
    ]
    task = normalize_task_brief(
        {
            "goal": "Write using a named skill",
            "requiredCapabilities": ["write", "fetch_skill_instructions"],
            "familyHint": "writing",
        }
    )

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected and selected["id"] == "writer"
    assert diagnostics["targetFamily"] == "writing"


def test_preferred_skill_workflow_curator_wins_for_skill_review() -> None:
    agents = [
        _agent("writer", "writing", ["write", "fetch_skill_instructions"]),
        _agent("skill-workflow-curator", "engineering", ["skill_review", "fetch_skill_instructions"]),
    ]
    task = normalize_task_brief(
        {
            "goal": "Review and improve a skill workflow",
            "requiredCapabilities": ["skill_review", "fetch_skill_instructions"],
            "familyHint": "engineering",
            "preferredAgentId": "skill-workflow-curator",
        }
    )

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected and selected["id"] == "skill-workflow-curator"
    assert diagnostics["selectionReason"] == "preferredAgentId"
    assert "preferredAgentId:skill-workflow-curator" in diagnostics["matchSignals"]


def test_delegated_writing_brief_requires_fetch_skill_instructions() -> None:
    prompt = _format_delegated_task_contract(
        {
            "goal": "Write a proposal with a skill",
            "context": {
                "writingExecutionBrief": {
                    "schema": "v8.writing_execution_brief.v1",
                    "skill": {"idOrName": "doc-coauthoring", "selectionReason": "user named it"},
                    "subagentFirstAction": "fetch_skill_instructions",
                    "authorizedRefs": {"researchRefs": [], "memoryRefs": [], "workspaceRefs": []},
                    "forbiddenInventions": ["Do not invent sources."],
                    "acceptanceCriteria": ["Return final draft plus execution notes."],
                }
            },
        },
    )

    assert "Writing Execution Brief" in prompt
    assert "fetch_skill_instructions(skill_name='doc-coauthoring')" in prompt
    assert "Do not invent sources" in prompt


def test_parallel_subagent_handoff_preserves_empty_ai_tool_call_name() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_fetch_skill",
                    "name": "fetch_skill_instructions",
                    "args": {"skill_name": "huashu-nuwa"},
                }
            ],
        ),
        ToolMessage(content='{"summary":"skill loaded"}', tool_call_id="call_fetch_skill"),
    ]

    assert _extract_tool_names(messages) == ["fetch_skill_instructions"]
    assert "使用工具: fetch_skill_instructions" in _compact_transcript(messages)


def test_reveal_subagent_family_returns_compact_members() -> None:
    agents = [
        _agent("eng", "engineering", ["implement", "code"]),
        _agent("media", "creative_media", ["video"]),
    ]

    payload = reveal_subagent_family("creative_media", agents)

    assert payload["found"] is True
    assert payload["memberCount"] == 1
    assert payload["members"][0]["agentId"] == "media"
    assert payload["members"][0]["capabilitySnapshot"]["operationCapabilities"] == ["video"]


def test_chat_runtime_resolves_structured_subagent_family_mention() -> None:
    runtime = ChatRuntime()
    request = ChatRequest(
        messages=[{"role": "user", "content": "请让这个家族参与"}],
        data={
            "contextMentions": [
                {
                    "kind": "subagent_family",
                    "familyId": "creative_media",
                    "name": "Creative Media",
                }
            ]
        },
    )

    with (
        patch("runtimes.chat.runtime.storage.get_supervisor_config", return_value={"specialistRegistry": {"families": []}}),
        patch("runtimes.chat.runtime.storage.get_all_agents", return_value=[_agent("media", "creative_media", ["video"])]),
    ):
        skill_refs = runtime._normalize_skill_references(request)
        mentions = runtime._normalize_context_mentions(request, skill_references=skill_refs)
        resolved = runtime._resolve_explicit_subagent_families(request, mentions)

    assert resolved == ["creative_media"]


def test_chat_runtime_requires_at_prefix_for_raw_family_reveal() -> None:
    runtime = ChatRuntime()
    with (
        patch("runtimes.chat.runtime.storage.get_supervisor_config", return_value={"specialistRegistry": {"families": []}}),
        patch("runtimes.chat.runtime.storage.get_all_agents", return_value=[_agent("media", "creative_media", ["video"])]),
    ):
        plain = ChatRequest(messages=[{"role": "user", "content": "用 creative_media 做视频"}], data={})
        explicit = ChatRequest(messages=[{"role": "user", "content": "@creative_media 做视频"}], data={})

        assert runtime._resolve_explicit_subagent_families(plain, []) == []
        assert runtime._resolve_explicit_subagent_families(explicit, []) == ["creative_media"]
