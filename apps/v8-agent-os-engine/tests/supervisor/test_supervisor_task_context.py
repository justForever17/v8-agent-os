from core.task_boundary_resolver import (
    attach_task_boundary_decision,
    build_supervisor_task_context,
    render_task_boundary_hint,
)


def test_ordinary_request_stays_neutral_for_supervisor_reasoning() -> None:
    context = build_supervisor_task_context(
        "我们准备给一个 20 人的开发团队换协作工具，Notion、Linear 和飞书之间一直拿不定。"
        "帮我做一份能拿去开会决策的建议，费用、迁移成本、权限和数据风险都别漏。"
    )

    assert context["schema"] == "v8.supervisor_task_context.v1"
    assert context["primaryTaskShape"] == "unknown"
    assert context["secondaryTaskShapes"] == []
    assert context["suggestedFamilies"] == []
    assert context["optionalRuntimeGrants"] == []
    assert context["source"] == "supervisor_first"
    assert context["boundaryDecision"]["primaryRuntime"] == ""
    assert context["boundaryDecision"]["executionMode"] == "supervisor_decides"
    assert render_task_boundary_hint(context["boundaryDecision"]) == ""


def test_natural_language_never_recreates_a_code_route_classifier() -> None:
    context = build_supervisor_task_context("打开真实桌面终端，让我看着运行命令。")

    assert context["primaryTaskShape"] == "unknown"
    assert context["suggestedFamilies"] == []
    assert context["boundaryDecision"]["primaryRuntime"] == ""
    assert context["boundaryDecision"]["signals"] == []


def test_explicit_governed_boundary_is_preserved_without_reading_user_prose() -> None:
    context = attach_task_boundary_decision(
        {
            "boundaryDecision": {
                "primaryRuntime": "computer_use",
                "executionMode": "governed_desktop_episode",
                "reason": "approved_runtime_resume",
                "source": "runtime_episode_state",
            }
        },
        user_query="这段自然语言不能改变显式治理状态",
    )

    boundary = context["boundaryDecision"]
    assert boundary["primaryRuntime"] == "computer_use"
    assert boundary["executionMode"] == "governed_desktop_episode"
    assert boundary["source"] == "runtime_episode_state"
    assert "not natural-language classification" in render_task_boundary_hint(boundary)
