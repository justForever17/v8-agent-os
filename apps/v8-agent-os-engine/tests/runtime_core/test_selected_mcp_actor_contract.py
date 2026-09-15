from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from core.delegation_broker import normalize_task_brief
from erc.runtime_context import bind_runtime_context
from graph import agent_factories


@pytest.mark.parametrize(
    ("selected", "policy", "expected_executions"),
    [
        (True, {"mode": "allowlist", "allowedTools": ["fixture_mcp_lookup"]}, 1),
        (False, {"mode": "allowlist", "allowedTools": ["fixture_mcp_lookup"]}, 0),
        (
            True,
            {
                "mode": "allowlist",
                "allowedTools": ["fixture_mcp_lookup"],
                "forbiddenTools": ["fixture_mcp_lookup"],
            },
            0,
        ),
        (True, {"mode": "allowlist", "allowedTools": []}, 0),
    ],
    ids=["selected-and-task-allowed", "not-selected", "explicitly-forbidden", "explicit-empty"],
)
def test_contextual_mcp_selection_and_task_policy_reach_real_tool_node(
    monkeypatch, selected, policy, expected_executions
):
    executions = []

    def lookup(query: str) -> str:
        """Read a public in-memory fixture; never access external services."""
        executions.append(query)
        return "Public fixture result"

    tool = StructuredTool.from_function(lookup, name="fixture_mcp_lookup")
    task = normalize_task_brief(
        {
            "taskBriefId": "fixture-mcp-task",
            "goal": "Read the public fixture through the selected MCP tool.",
            "runtimeAccess": [],
            "toolPolicy": policy,
        }
    )
    captured_pools = []
    original_create = agent_factories.create_routed_tool_node

    def record_actual_pool(tools, name, fallback_goto):
        captured_pools.append([item.name for item in tools])
        return original_create(tools, name, fallback_goto)

    monkeypatch.setattr(agent_factories, "create_routed_tool_node", record_actual_pool)
    node = agent_factories.build_contextual_auto_tool_node(
        base_tools=[],
        all_native_tools=[],
        static_extra_tools=[],
        all_mcp_tools=[tool],
        name="fixture_worker_tools",
        fallback_goto="fixture_worker",
    )
    state = {
        "messages": [
            HumanMessage(content="Read the public fixture."),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": tool.name, "args": {"query": "public"}, "id": "fixture-mcp-call"}
                ],
            ),
        ],
        "current_route_context": {
            "selectedMcpTools": [tool.name] if selected else [],
            "taskBrief": task,
            "delegationDepth": 1,
        },
    }
    with bind_runtime_context(actor_role="subagent", delegation_depth=1):
        result = asyncio.run(node(state))
    commands = result if isinstance(result, list) else [result]
    replies = [
        message
        for command in commands
        for message in (command.update or {}).get("messages", [])
        if isinstance(message, ToolMessage)
    ]

    assert captured_pools == [[tool.name] if expected_executions else []]
    assert executions == (["public"] if expected_executions else [])
    assert len(replies) == 1
    assert replies[0].tool_call_id == "fixture-mcp-call"
    assert replies[0].status == ("success" if expected_executions else "error")
    if expected_executions:
        assert replies[0].content == "Public fixture result"
