from __future__ import annotations

from types import SimpleNamespace

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
