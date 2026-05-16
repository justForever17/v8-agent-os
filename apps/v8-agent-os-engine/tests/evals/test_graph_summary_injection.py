from __future__ import annotations

from memory_eval_matrix import graph_scope_isolation_case, graph_summary_case


def test_graph_summary_is_injected_on_demand() -> None:
    result = graph_summary_case()
    assert result["status"] == "pass", result


def test_graph_summary_does_not_cross_project_scope() -> None:
    result = graph_scope_isolation_case()
    assert result["status"] == "pass", result
