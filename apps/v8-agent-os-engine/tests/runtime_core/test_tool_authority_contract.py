from __future__ import annotations

from types import SimpleNamespace
import asyncio
from unittest.mock import patch

from core.tool_authority import filter_authorized_tools, resolve_tool_authority
from graph.agent_factories import _apply_task_tool_policy


def tools(*names):
    return [SimpleNamespace(name=name) for name in names]


def test_explicit_empty_allowlist_is_deny_all_and_does_not_fall_back_to_top_level():
    task = {"allowedTools": ["read_native_file"], "toolPolicy": {"mode": "allowlist", "allowedTools": []}}
    decision = resolve_tool_authority(task)
    assert decision.mode == "allowlist"
    assert decision.explicit_allowlist is True
    assert _apply_task_tool_policy(tools("read_native_file", "web_broker"), task) == []


def test_policy_precedence_and_forbidden_tools_are_explainable():
    task = {"allowedTools": ["read_native_file"], "toolPolicy": {"allowedTools": ["web_broker"], "forbiddenTools": ["web_broker"]}}
    selected, decision, denied = filter_authorized_tools(tools("read_native_file", "web_broker"), task)
    assert [item.name for item in selected] == []
    assert decision.source == "toolPolicy"
    assert denied["read_native_file"] == "policy_denied"
    assert denied["web_broker"] == "policy_denied"


def test_receipt_changes_when_policy_or_visible_surface_changes():
    first = resolve_tool_authority({"toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file"]}})
    second = resolve_tool_authority({"toolPolicy": {"mode": "allowlist", "allowedTools": ["web_broker"]}})
    assert first.policy_digest != second.policy_digest
    assert first.as_receipt(["read_native_file"]) ["visibleToolSetDigest"] != first.as_receipt(["web_broker"])["visibleToolSetDigest"]


def test_contextual_auto_production_path_does_not_bind_selector_only_mcp_candidate():
    from graph.agent_factories import build_contextual_auto_tool_node

    captured = {}

    def fake_routed(bound_tools, *, name, fallback_goto):
        captured["names"] = [getattr(item, "name", "") for item in bound_tools]

        async def invoke(state, config=None, runtime=None):
            return state

        return invoke

    node = build_contextual_auto_tool_node(
        base_tools=[], all_native_tools=[], static_extra_tools=[],
        all_mcp_tools=[SimpleNamespace(name="secret_mcp")], name="worker_tools", fallback_goto="next",
    )
    state = {"current_route_context": {"selectedMcpTools": ["secret_mcp"], "taskBrief": {"runtimeAccess": []}}}
    with patch("graph.agent_factories.create_routed_tool_node", side_effect=fake_routed):
        asyncio.run(node(state))
    assert "secret_mcp" not in captured["names"]
