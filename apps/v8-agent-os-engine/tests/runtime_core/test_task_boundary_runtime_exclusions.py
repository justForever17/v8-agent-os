from __future__ import annotations

from core.task_boundary_resolver import resolve_task_boundary
from runtimes.chat.planner_contract_verifier import verify_and_repair_planner_contract


def test_task_boundary_respects_explicit_rpa_and_computer_use_exclusion() -> None:
    decision = resolve_task_boundary(
        "请规划一个不含 Computer Use/RPA 的任务：调研、工程计划、子代理复核风险，不要真实写文件。",
        task_shape_hint={"primaryTaskShape": "research", "secondaryTaskShapes": ["delegation"]},
        planner_plan={
            "capabilityPlan": [
                {"kind": "research"},
                {"kind": "engineering"},
                {"kind": "rpa", "reason": "repeatable_workflow_or_object_library_request"},
            ]
        },
    )

    assert decision["primaryRuntime"] != "rpa"
    assert "rpa_explicitly_excluded" in decision["forbiddenRoutes"]
    assert "computer_use_explicitly_excluded" in decision["forbiddenRoutes"]


def test_planner_verifier_removes_excluded_runtime_capabilities() -> None:
    plan = {
        "taskBriefs": [
            {"taskBriefId": "task-1", "goal": "Research memory evidence pack"},
            {"taskBriefId": "task-2", "goal": "Review risk", "familyHint": "rpa"},
        ],
        "capabilityPlan": [
            {"kind": "research", "taskBriefId": "task-1"},
            {"kind": "rpa", "taskBriefId": "task-2"},
            {"kind": "computer_use", "taskBriefId": "task-2"},
        ],
    }
    task_shape_hint = {
        "boundaryDecision": {
            "primaryRuntime": "research",
            "executionMode": "research_runtime",
            "reason": "user_explicitly_excluded_desktop_runtimes",
            "forbiddenRoutes": ["rpa_explicitly_excluded", "computer_use_explicitly_excluded"],
        }
    }

    repaired = verify_and_repair_planner_contract(plan, task_shape_hint=task_shape_hint)
    kinds = {item["kind"] for item in repaired["capabilityPlan"]}

    assert "research" in kinds
    assert "rpa" not in kinds
    assert "computer_use" not in kinds
    assert "planner_boundary_rpa_exclusion_repaired" in repaired["qualityFlags"]
    assert "planner_boundary_computer_use_exclusion_repaired" in repaired["qualityFlags"]
