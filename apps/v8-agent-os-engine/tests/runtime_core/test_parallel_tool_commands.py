import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, Send

from graph.parallel_support import _run_parallel_agent_branch
from graph.tool_routing import create_routed_tool_node
from core.tools.native.delegation import delegation_broker


@pytest.mark.parametrize("mode,error", [("inspect", "delegation_mode_not_available_to_subagent"),
                                         ("request_input", "runtime_continuation_input_kind_invalid")])
def test_toolnode_command_rejection_returns_to_worker_for_correction(mode, error, monkeypatch):
    turns = []
    errors = []
    original = delegation_broker.func
    def broker(**kwargs):
        result = original(**kwargs)
        errors.append(json.loads(result.update["messages"][0].content).get("error"))
        return result
    monkeypatch.setattr(delegation_broker, "func", broker)
    @tool
    def companion() -> str:
        """Return an ordinary sibling observation in the same tool batch."""
        return "Companion evidence"
    def model(state):
        turns.append(state)
        if len(turns) == 1:
            return Command(goto="worker_tools", update={"messages": [AIMessage(content="", tool_calls=[
                {"id": "rejected", "name": "delegation_broker", "args": {"mode": mode,
                    **({"required_inputs": [{"id": "choice", "question": "Choose a value", "kind": "invalid-kind"}]} if mode == "request_input" else {})}},
                {"id": "companion", "name": "companion", "args": {}}])]})
        observations = [message for message in state["messages"] if isinstance(message, ToolMessage)]
        assert len(observations) == 2 and errors == [error]
        assert any(message.name == "delegation_broker" and message.content for message in observations)
        assert any("Companion evidence" in message.content for message in observations)
        return Command(goto="supervisor", update={"messages": [AIMessage(content="The attempted control was rejected; return its diagnostic.")]})
    result = asyncio.run(_run_parallel_agent_branch({"messages": [], "todos": [], "parallel_branch": {
        "agentId": "worker", "delegationId": "branch", "taskBriefId": "B", "allowChildDelegation": False}},
        {"node_func": model, "tool_node_func": create_routed_tool_node([delegation_broker, companion], "worker_tools", "worker")}))
    assert len(turns) == 2
    assert result[3] == [] and result[2].get("error") != "child_delegation_not_allowed"
    assert any(message.name == "delegation_broker" for message in result[0] if isinstance(message, ToolMessage))


def test_real_grandchild_send_remains_blocked_without_child_authority():
    child_state = {"messages": [], "todos": [], "parallel_branch": {"agentId": "grandchild", "delegationId": "child",
        "taskBriefId": "C", "taskBrief": {"taskBriefId": "C", "goal": "Verify independently"}}}
    result = asyncio.run(_run_parallel_agent_branch({"messages": [], "todos": [], "parallel_branch": {
        "agentId": "worker", "delegationId": "parent", "taskBriefId": "B", "allowChildDelegation": False}},
        {"node_func": lambda state: [Command(goto=[Send("parallel_delegate_task", child_state)])]}))
    assert result[2]["error"] == "child_delegation_not_allowed"
    assert result[2]["blockedChildDelegationCount"] == 1 and result[3] == []
