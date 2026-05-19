from __future__ import annotations

from types import SimpleNamespace

from core.runtime_tool_access import filter_visible_tools_for_actor
from graph.workflow_assembly import build_planner_auto_dispatch_node
from runtimes.chat.runtime import ChatRuntime
from runtimes.engineering.service import engineering_lane_service


def test_explicit_engineering_request_is_detected() -> None:
    runtime = ChatRuntime()

    assert runtime._detect_explicit_engineering_runtime_request("请使用 Engineering Runtime 开发这个项目")
    assert runtime._detect_explicit_engineering_runtime_request("这次必须进入工程运行时，不要主管盲写")
    assert runtime._detect_explicit_engineering_runtime_request("用工程模式做前端实现")
    assert not runtime._detect_explicit_engineering_runtime_request("做一个小的文字说明")


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
