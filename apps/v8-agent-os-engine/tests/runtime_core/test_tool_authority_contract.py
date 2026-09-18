from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.delegation_broker import normalize_task_brief
from core.tool_authority import filter_authorized_tools, resolve_tool_authority
from core.tools.native.delegation import _terminalize_grandchild_task_brief
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


@pytest.mark.parametrize("task, expected", [
    ({"allowedTools": ["read_native_file"], "toolPolicy": {"allowedTools": []}}, []),
    ({"allowedTools": [], "toolPolicy": {"allowedTools": ["read_native_file"]}}, ["read_native_file"]),
    ({"toolPolicy": {"forbiddenTools": ["web_broker"]}, "forbiddenTools": []}, ["read_native_file"]),
    ({"toolPolicy": {"forbiddenTools": []}, "forbiddenTools": ["web_broker"]}, ["read_native_file", "web_broker"]),
    ({"toolPolicy": {"noTools": True}, "noTools": False}, []),
    ({"toolPolicy": {"noTools": False}, "noTools": True}, ["read_native_file", "web_broker"]),
    ({"toolPolicyMode": "none"}, []),
    ({"toolPolicy": {"mode": "default", "allowedTools": []}}, ["read_native_file", "web_broker"]),
    ({"tool_policy": {"allowed_tools": [], "selectionReason": "explicit boundary"}}, []),
])
def test_normalization_and_recovery_preserve_tool_authority(task, expected):
    # A durable brief is normalized again on resume; neither pass may rewrite
    # an explicit policy using the other copy's empty/nonempty fields.
    for brief in (task, normalize_task_brief(task), normalize_task_brief(normalize_task_brief(task))):
        assert [tool.name for tool in _apply_task_tool_policy(tools("read_native_file", "web_broker"), brief)] == expected


@pytest.mark.parametrize("parent", [
    {"toolPolicy": {"mode": "allowlist", "allowedTools": []}},
    {"allowedTools": []},
    {"allowedTools": ["read_native_file"], "toolPolicy": {"allowedTools": []}},
    {"toolPolicy": {"noTools": True}},
    {"tool_policy": {"allowed_tools": []}},
])
def test_terminal_worker_never_expands_parent_denial_using_resolved_candidates(parent):
    terminal = _terminalize_grandchild_task_brief(
        {"toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file"]}},
        parent_task_brief=parent,
        parent_resolved_tools=["read_native_file"],
    )
    assert terminal["allowedTools"] == []
    assert _apply_task_tool_policy(tools("read_native_file"), terminal) == []


@pytest.mark.parametrize("child", [
    {"allowedTools": ["read_native_file"], "toolPolicy": {"allowedTools": []}},
    {"toolPolicy": {"noTools": True}},
    {"allowedTools": []},
])
def test_terminal_worker_preserves_child_denial(child):
    terminal = _terminalize_grandchild_task_brief(
        child,
        parent_task_brief={"toolPolicy": {"mode": "default"}},
        parent_resolved_tools=["read_native_file"],
    )
    assert _apply_task_tool_policy(tools("read_native_file"), terminal) == []


def test_terminal_worker_keeps_parent_forbidden_tools_after_recovery():
    terminal = _terminalize_grandchild_task_brief(
        {"toolPolicy": {"mode": "default", "selectionReason": "independent check"}},
        parent_task_brief={"forbiddenTools": [], "toolPolicy": {"forbiddenTools": ["web_broker"]}},
        parent_resolved_tools=["read_native_file", "web_broker"],
    )
    recovered = normalize_task_brief(terminal)
    assert [tool.name for tool in _apply_task_tool_policy(tools("read_native_file", "web_broker", "delegation_broker"), recovered)] == ["read_native_file"]
    assert recovered["toolPolicy"]["selectionReason"] == "independent check"
    assert recovered["allowChildDelegation"] is False


def test_terminal_worker_does_not_promote_context_policy_into_authority():
    terminal = _terminalize_grandchild_task_brief(
        {"context": {"toolPolicy": {"noTools": True}}},
        parent_task_brief={"toolPolicy": {"mode": "default"}},
        parent_resolved_tools=["read_native_file"],
    )
    assert [tool.name for tool in _apply_task_tool_policy(tools("read_native_file"), terminal)] == ["read_native_file"]
    assert terminal["context"]["toolPolicy"] == {"noTools": True}
    assert "noTools" not in terminal["toolPolicy"]


def test_episode_reload_cannot_restore_tools_from_stale_top_level_projection(tmp_path):
    from core.database import DatabaseManager
    from core.runtime_episode_runner import RuntimeEpisodeRunner
    from core.runtime_episodes import build_runtime_episode

    database = DatabaseManager(tmp_path / "authority.db")
    brief = normalize_task_brief({
        "taskBriefId": "verify-denied",
        "goal": "Verify supplied evidence only",
        "toolPolicy": {"mode": "allowlist", "allowedTools": [], "selectionReason": "no grant"},
    })
    # Simulate a conflicting projection in a durable episode. Reload must use
    # the explicit nested policy even if candidate discovery has stale tools.
    brief["allowedTools"] = ["read_native_file"]
    episode = build_runtime_episode(
        need={"episodeId": "authority-episode", "kind": "delegation", "inputs": {"taskBriefs": [brief]}},
        kind="delegation",
        state="queued",
    )
    database.upsert_runtime_episode_record(episode)
    reopened = DatabaseManager(database.db_path)
    restored = RuntimeEpisodeRunner._episode_task_briefs(reopened.get_runtime_episode("authority-episode"))[0]
    assert _apply_task_tool_policy(tools("read_native_file"), restored) == []
    terminal = _terminalize_grandchild_task_brief(
        {"allowedTools": ["read_native_file"]},
        parent_task_brief=restored,
        parent_resolved_tools=["read_native_file"],
    )
    assert _apply_task_tool_policy(tools("read_native_file"), terminal) == []
    assert restored["toolPolicy"]["selectionReason"] == "no grant"
