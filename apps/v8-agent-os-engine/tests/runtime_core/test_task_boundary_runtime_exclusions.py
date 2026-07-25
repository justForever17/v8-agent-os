from __future__ import annotations

from core.task_boundary_resolver import resolve_task_boundary


def test_task_boundary_does_not_parse_runtime_terms_or_exclusions() -> None:
    decision = resolve_task_boundary(
        "请规划一个不含 Computer Use/RPA 的任务：调研、工程计划、子代理复核风险，不要真实写文件。",
        task_shape_hint={"primaryTaskShape": "research", "secondaryTaskShapes": ["delegation"]},
    )

    assert decision["primaryRuntime"] == ""
    assert decision["executionMode"] == "supervisor_decides"
    assert decision["forbiddenRoutes"] == []
    assert decision["policy"] == "advisory_context_only_no_lexical_routing"
