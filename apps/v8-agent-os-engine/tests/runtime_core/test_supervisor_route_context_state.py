from __future__ import annotations

from typing import get_args, get_type_hints

from agents.runners.supervisor_runner import SupervisorAgentRunner
from graph.route_context import merge_route_context
from graph.supervisor import AgentState
from langchain_core.messages import HumanMessage


def test_current_route_context_uses_merge_reducer_for_parallel_updates():
    hints = get_type_hints(AgentState, include_extras=True)
    args = get_args(hints["current_route_context"])

    assert args[0] is dict
    assert args[1] is merge_route_context


def test_supervisor_initial_state_promotes_identity_to_top_level_fields():
    runner = SupervisorAgentRunner()
    state = runner.create_state(
        [HumanMessage(content="test")],
        current_route_context={
            "session_id": "session-1",
            "sessionId": "session-1",
            "run_id": "run-1",
            "runId": "run-1",
            "workspace_path": r"E:\Projects\test7",
            "workspacePath": r"E:\Projects\test7",
            "workspace_id": "workspace:test7",
            "workspaceId": "workspace:test7",
            "resolved_scope": "workspace:test7",
            "resolvedScope": "workspace:test7",
        },
    )

    assert state["session_id"] == "session-1"
    assert state["sessionId"] == "session-1"
    assert state["run_id"] == "run-1"
    assert state["runId"] == "run-1"
    assert state["workspace_path"] == r"E:\Projects\test7"
    assert state["workspacePath"] == r"E:\Projects\test7"
    assert state["workspace_id"] == "workspace:test7"
    assert state["workspaceId"] == "workspace:test7"
    assert state["resolved_scope"] == "workspace:test7"
    assert state["resolvedScope"] == "workspace:test7"
