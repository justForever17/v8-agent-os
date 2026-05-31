from __future__ import annotations

from types import SimpleNamespace

from core.runtime_tool_access import filter_visible_tools_for_actor
from core.task_shape_classifier import classify_task_shape
from graph.workflow_assembly import build_planner_auto_dispatch_node
from runtimes.chat.runtime import ChatRuntime
from runtimes.engineering.service import engineering_lane_service


def test_explicit_engineering_request_is_detected() -> None:
    runtime = ChatRuntime()

    assert runtime._detect_explicit_engineering_runtime_request("请使用 Engineering Runtime 开发这个项目")
    assert runtime._detect_explicit_engineering_runtime_request("这次必须进入工程运行时，不要主管盲写")
    assert runtime._detect_explicit_engineering_runtime_request("用工程模式做前端实现")
    assert not runtime._detect_explicit_engineering_runtime_request("做一个小的文字说明")
    assert not runtime._detect_explicit_engineering_runtime_request("只写正文，不调用工程运行时")


def test_planner_list_payload_is_wrapped_as_valid_plan() -> None:
    fallback = {"executionStrategy": "direct", "taskBriefs": []}
    payload = ChatRuntime._normalize_planner_plan_payload(
        [
            {"taskBriefId": "research", "taskGoal": "调研规则"},
            {"taskBriefId": "implementation", "taskGoal": "实现项目"},
        ],
        fallback_plan=fallback,
    )

    assert payload["executionStrategy"] == "mixed"
    assert len(payload["taskBriefs"]) == 2
    assert "planner_list_payload_wrapped" in payload["qualityFlags"]


def test_project_research_fallback_includes_runtime_capability_plan() -> None:
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(
            latest_user_content="调研规则并开发一个 Web 项目",
            planner_intent_diagnostics={"signals": []},
            task_shape_hint={
                "primaryTaskShape": "project_coding",
                "secondaryTaskShapes": ["research"],
                "optionalRuntimeGrants": ["research.core"],
            },
            planner_mode="auto",
        )
    )

    plan = runtime._fallback_planner_plan(chat_run=chat_run, reason="structured_empty")

    assert plan["executionStrategy"] == "mixed"
    assert [item["kind"] for item in plan["capabilityPlan"]] == ["research", "engineering"]
    assert plan["handoffPlan"][0]["fromTaskBriefId"] == "task-1"
    assert plan["handoffPlan"][0]["toTaskBriefId"] == "task-2"


def _chat_run_for_query(query: str) -> SimpleNamespace:
    return SimpleNamespace(
        prepared=SimpleNamespace(
            latest_user_content=query,
            planner_intent_diagnostics={"signals": []},
            task_shape_hint=classify_task_shape(query),
            planner_mode="auto",
        )
    )


def test_writing_fallback_ambiguous_document_asks_user_before_routing() -> None:
    runtime = ChatRuntime()
    plan = runtime._fallback_planner_plan(chat_run=_chat_run_for_query("帮我写一篇文档"), reason="structured_empty")

    assert plan["executionStrategy"] == "direct"
    assert plan["taskBriefs"][0]["requiredCapabilities"] == ["ask_user", "requirements_clarification"]
    assert plan["taskBriefs"][0]["context"]["askUserOptions"] == ["只要正文", "先调研再写", "保存为文件/仓库文档"]
    assert "writing_clarification_required" in plan["riskFlags"]


def test_writing_fallback_skill_task_delegates_with_execution_brief() -> None:
    runtime = ChatRuntime()
    plan = runtime._fallback_planner_plan(
        chat_run=_chat_run_for_query("用 doc-coauthoring skill 写一篇技术方案"),
        reason="structured_empty",
    )

    task = plan["taskBriefs"][0]
    brief = task["context"]["writingExecutionBrief"]
    assert plan["executionStrategy"] == "delegate"
    assert plan["capabilityPlan"][0]["kind"] == "delegation"
    assert task["familyHint"] == "writing"
    assert "fetch_skill_instructions" in task["requiredCapabilities"]
    assert brief["skill"]["idOrName"] == "doc-coauthoring"
    assert brief["subagentFirstAction"] == "fetch_skill_instructions"
    assert "skill_first_action_required" in plan["qualityFlags"]


def test_writing_fallback_existing_perspective_skill_stays_direct_with_fetch() -> None:
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(
            latest_user_content="用 sanyueqi-perspective skill 回答：朋友迷路害怕时应该怎么安慰？",
            planner_intent_diagnostics={"signals": []},
            planner_mode="auto",
            task_shape_hint={
                "primaryTaskShape": "writing",
                "secondaryTaskShapes": [],
                "writingRoute": {
                    "present": True,
                    "mode": "direct_supervisor",
                    "requiresSkillExecution": True,
                    "requiresResearch": False,
                    "requiresArtifact": False,
                    "recommendedFamily": "",
                    "skillName": "sanyueqi-perspective",
                    "firstActionTool": "fetch_skill_instructions",
                    "allowCreateSubagentOnMismatch": False,
                },
            },
        )
    )

    plan = runtime._fallback_planner_plan(chat_run=chat_run, reason="structured_empty")

    task = plan["taskBriefs"][0]
    assert plan["executionStrategy"] == "direct"
    assert plan["capabilityPlan"] == []
    assert task["requiredCapabilities"] == ["fetch_skill_instructions", "writing"]
    assert task["context"]["writingExecutionBrief"]["skill"]["idOrName"] == "sanyueqi-perspective"


def test_writing_fallback_selected_skill_overrides_false_engineering_signals() -> None:
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(
            latest_user_content="使用已选择的 huashu-nuwa skill，蒸馏一个测试人物视角，只输出计划，不写文件、不创建 skill。",
            planner_intent_diagnostics={"signals": []},
            planner_mode="force",
            task_shape_hint={
                "primaryTaskShape": "project_coding",
                "secondaryTaskShapes": ["creative_media"],
                "signals": ["code_action:测试", "media_output:测试"],
                "writingRoute": {
                    "present": True,
                    "mode": "skill_subagent",
                    "requiresSkillExecution": True,
                    "requiresResearch": False,
                    "requiresArtifact": False,
                    "recommendedFamily": "writing",
                    "skillName": "huashu-nuwa",
                    "firstActionTool": "fetch_skill_instructions",
                    "allowCreateSubagentOnMismatch": True,
                },
            },
        )
    )

    plan = runtime._fallback_planner_plan(chat_run=chat_run, reason="structured_empty")

    task = plan["taskBriefs"][0]
    assert plan["executionStrategy"] == "delegate"
    assert plan["capabilityPlan"][0]["kind"] == "delegation"
    assert task["familyHint"] == "writing"
    assert "fetch_skill_instructions" in task["requiredCapabilities"]
    assert task["context"]["writingExecutionBrief"]["skill"]["idOrName"] == "huashu-nuwa"


def test_writing_fallback_skill_with_research_routes_evidence_before_delegation() -> None:
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(
            latest_user_content="在 E:\\Projects\\test7 里用 huashu-nuwa skill 调研三月七并生成 skill。",
            planner_intent_diagnostics={"signals": []},
            planner_mode="force",
            task_shape_hint={
                "primaryTaskShape": "writing",
                "secondaryTaskShapes": ["research", "delegation"],
                "writingRoute": {
                    "present": True,
                    "mode": "skill_subagent",
                    "requiresSkillExecution": True,
                    "requiresResearch": True,
                    "requiresArtifact": True,
                    "recommendedFamily": "engineering",
                    "preferredAgentId": "skill-workflow-curator",
                    "skillName": "huashu-nuwa",
                    "firstActionTool": "fetch_skill_instructions",
                    "allowCreateSubagentOnMismatch": False,
                },
            },
        )
    )

    plan = runtime._fallback_planner_plan(chat_run=chat_run, reason="structured_empty")

    assert plan["executionStrategy"] == "mixed"
    assert [item["kind"] for item in plan["capabilityPlan"]] == ["research", "delegation"]
    assert plan["taskBriefs"][0]["familyHint"] == "research"
    assert plan["taskBriefs"][1]["dependency"] == ["task-1"]
    assert plan["taskBriefs"][1]["familyHint"] == "engineering"
    assert plan["taskBriefs"][1]["preferredAgentId"] == "skill-workflow-curator"
    assert plan["taskBriefs"][1]["researchRefs"] == ["task-1:evidenceBundleId", "task-1:sourceMatrix", "task-1:claimTable"]
    brief = plan["taskBriefs"][1]["context"]["writingExecutionBrief"]
    assert brief["skill"]["idOrName"] == "huashu-nuwa"
    assert brief["authorizedRefs"]["researchRefs"]
    assert plan["handoffPlan"][0]["fromTaskBriefId"] == "task-1"
    assert "research_before_skill_writing" in plan["qualityFlags"]


def test_writing_fallback_research_then_write_has_handoff_dependency() -> None:
    runtime = ChatRuntime()
    plan = runtime._fallback_planner_plan(
        chat_run=_chat_run_for_query("先调研官方资料，然后写一份报告，带来源"),
        reason="structured_empty",
    )

    assert plan["executionStrategy"] == "mixed"
    assert [item["kind"] for item in plan["capabilityPlan"]] == ["research", "delegation"]
    assert plan["taskBriefs"][1]["dependency"] == ["task-1"]
    assert plan["taskBriefs"][1]["context"]["writingExecutionBrief"]["authorizedRefs"]["researchRefs"]
    assert plan["handoffPlan"][0]["fromTaskBriefId"] == "task-1"


def test_writing_fallback_file_artifact_routes_engineering() -> None:
    runtime = ChatRuntime()
    plan = runtime._fallback_planner_plan(
        chat_run=_chat_run_for_query("写一篇方案并保存到 docs/plan.md"),
        reason="structured_empty",
    )

    assert plan["executionStrategy"] == "mixed"
    assert plan["capabilityPlan"][0]["kind"] == "engineering"
    assert plan["taskBriefs"][0]["familyHint"] == "engineering"
    assert "document_artifact" in plan["taskBriefs"][0]["requiredCapabilities"]


def test_engineering_project_creation_workspace_activates_without_git(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(engineering_lane_service, "get_config", lambda: {"enabled": True, "triggerMode": "auto"})
    decision = engineering_lane_service.trigger_decision(
        user_query="请使用 Engineering Runtime 开发一个 AI 狼人杀 Web 应用",
        mode="auto",
        workspace_descriptor={"workspaceRoot": str(tmp_path)},
    )

    assert decision["matched"] is True
    assert decision["active"] is True
    assert decision["workspaceMode"] == "project_creation_workspace"
    assert decision["reason"] == "project_creation_workspace"


def test_subagent_delegation_broker_requires_recursive_grant() -> None:
    tool = SimpleNamespace(name="delegation_broker")

    hidden = filter_visible_tools_for_actor([tool], actor="subagent")
    granted = filter_visible_tools_for_actor(
        [tool],
        actor="subagent",
        runtime_access=["delegation.recursive"],
    )

    assert hidden == []
    assert [item.name for item in granted] == ["delegation_broker"]


def test_planner_auto_dispatch_blocks_when_explicit_engineering_is_disabled() -> None:
    node = build_planner_auto_dispatch_node()
    command = node(
        {
            "current_route_context": {
                "explicitEngineeringRequested": True,
                "engineeringTriggerDecision": {"reason": "engineering_lane_disabled"},
            },
            "planner_plan": {
                "autoDispatchDecision": {"mode": "auto", "eligible": True, "willDispatch": True},
                "taskBriefs": [{"taskBriefId": "task-1", "goal": "write files"}],
            },
        }
    )

    update = command.update
    assert update["planner_dispatch_status"]["blocked"] is True
    assert update["planner_dispatch_status"]["blockedReason"] == "engineering_runtime_disabled"
    assert "用户显式要求 Engineering Runtime" in update["messages"][0].content
