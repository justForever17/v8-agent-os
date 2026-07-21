from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

import core.tools.native.delegation as native_delegation
import core.tools.native.runtime as native_runtime
from core.actor_identity import (
    DIRECT_SUBAGENT_ACTOR,
    GRANDCHILD_ACTOR,
    RUNTIME_INTERNAL_ACTOR,
    SUPERVISOR_ACTOR,
    resolve_collaboration_actor,
)
from core.delegation_broker import normalize_task_brief
from core.native_tools import (
    _decode_completed_process_bytes,
    _windows_shell_syntax_violation_payload,
    creative_media_assets,
    creative_media_capabilities,
    creative_media_edit,
    creative_media_jobs,
    creative_media_plan,
    creative_media_quality,
    memory_broker,
    delegation_broker,
    runtime_broker,
)
from core.runtime_tool_access import (
    RUNTIME_TOOL_GROUPS,
    filter_visible_tools_for_actor,
    normalize_subagent_runtime_bindings,
    resolve_subagent_runtime_access,
    runtime_access_from_route_context,
)
from core.system_tools.baseline import BASELINE_SYSTEM_TOOL_NAME_ORDER
from core.spec_service import spec_service
from erc.runtime_context import bind_runtime_context
from erc.capability_registry import CapabilityRegistry, RuntimePolicy, capability_registry
from graph.agent_factories import (
    _align_extension_route_to_task_tools,
    _build_atomic_worker_extension_route,
    _format_collaboration_identity_contract,
    _format_delegated_task_contract,
    _preserve_direct_worker_extension_candidates,
    _select_contextual_subagent_native_tools,
)


def _set_pack_runtime_installed(monkeypatch, installed: bool) -> None:
    def _runtime_family_installed(kind: str, *, profile: str | None = None) -> bool:
        if kind in {"computer_use", "desktop_live", "rpa"}:
            return installed
        return True

    monkeypatch.setattr("core.runtime.startup_profile.runtime_family_installed", _runtime_family_installed)


def _tool(name: str):
    return SimpleNamespace(name=name)


def test_child_delegation_normalization_preserves_unset_vs_explicit_false():
    defaulted = normalize_task_brief({"taskBriefId": "default", "goal": "Implement one change."})
    forbidden = normalize_task_brief(
        {
            "taskBriefId": "forbidden",
            "goal": "Implement without further delegation.",
            "allowChildDelegation": False,
        }
    )
    renormalized = normalize_task_brief(defaulted)

    assert defaulted["allowChildDelegation"] is False
    assert defaulted["childDelegationPolicyExplicit"] is False
    assert renormalized["childDelegationPolicyExplicit"] is False
    assert forbidden["allowChildDelegation"] is False
    assert forbidden["childDelegationPolicyExplicit"] is True


def test_must_acceptance_names_required_grandchild_as_structured_contract():
    brief = normalize_task_brief(
        {
            "taskBriefId": "required-grandchild",
            "goal": "Implement one change and verify it independently.",
            "acceptanceContract": {
                "must": [
                    "Implementation Engineer 必须委派孙 Agent 独立验证最终文件。",
                    "The command exits successfully.",
                ],
                "should": ["Keep the handoff compact."],
            },
        }
    )

    assert brief["requireChildDelegation"] is True
    assert brief["allowChildDelegation"] is True
    assert brief["childDelegationPolicyExplicit"] is False
    assert brief["childDelegationBudget"] == {"maxChildren": 1, "maxDepth": 1}
    rendered = _format_delegated_task_contract(
        {**brief, "delegationDepth": 1, "runtimeAccess": ["delegation.recursive"]}
    )
    assert "REQUIRED BY ACCEPTANCE" in rendered
    assert "delegation_broker(mode='dispatch')" in rendered
    assert "complete your own assigned write" in rendered
    assert "run your own local self-check" in rendered
    assert "disposable mirror rule is authoritative" in rendered
    assert "do not pass targetAgentName" in rendered


def test_runtime_episode_preserves_managed_workspace_authority_and_parent_worktree(monkeypatch):
    monkeypatch.setattr(
        native_runtime,
        "enqueue_runtime_episode",
        lambda episode, **_kwargs: {**episode, "state": "queued"},
    )
    with bind_runtime_context(
        runtime_kind="chat",
        actor_role="supervisor",
        session_id="session-managed-route",
        run_id="run-managed-route",
        workspace_path=r"C:\Users\test\.v8-agent-os\worktrees\repo\run\supervisor",
        original_workspace_path=r"E:\Projects\example",
        repository_root=r"E:\Projects\example",
        worktree_root=r"C:\Users\test\.v8-agent-os\worktrees\repo\run\supervisor",
        worktree_id="supervisor-worktree",
        sandbox_lease_id="sandbox-lease",
        sandbox_policy_digest="policy-digest",
        managed_engineering_execution=True,
    ):
        _updated, episode = native_runtime._append_runtime_episode(
            {},
            need={
                "kind": "engineering",
                "reason": "Implement a focused change.",
                "inputs": {"taskBriefs": [{"taskBriefId": "task-1", "goal": "Implement it."}]},
            },
            kind="engineering",
            groups=[],
            allow_direct_fallback=False,
        )

    inputs = episode["inputs"]
    assert inputs["workspacePath"].endswith("supervisor")
    assert inputs["originalWorkspacePath"] == r"E:\Projects\example"
    assert inputs["parentWorktreeId"] == "supervisor-worktree"
    assert inputs["engineeringWorkspace"]["worktree_id"] == "supervisor-worktree"
    assert inputs["engineeringWorkspace"]["original_workspace_path"] == r"E:\Projects\example"


def test_collaboration_actor_identity_separates_user_facing_tree_from_internal_models():
    assert resolve_collaboration_actor(actor="supervisor").role == SUPERVISOR_ACTOR
    assert resolve_collaboration_actor(
        runtime_context={
            "runtime_kind": "delegation",
            "actor_role": "supervisor",
            "agent_id": "supervisor",
            "delegation_depth": 0,
        }
    ).role == SUPERVISOR_ACTOR
    assert resolve_collaboration_actor(
        runtime_context={
            "runtime_kind": "delegation",
            "actor_role": "direct_subagent",
            "agent_id": "implementation-engineer",
            "delegation_depth": 1,
        }
    ).role == DIRECT_SUBAGENT_ACTOR
    assert resolve_collaboration_actor(
        actor="subagent",
        route_context={"delegationDepth": 1},
    ).role == DIRECT_SUBAGENT_ACTOR
    assert resolve_collaboration_actor(
        actor="subagent",
        route_context={"delegationDepth": 2},
    ).role == GRANDCHILD_ACTOR
    assert resolve_collaboration_actor(actor="computer_use_visual_actor").role == RUNTIME_INTERNAL_ACTOR
    assert resolve_collaboration_actor(
        runtime_context={"runtime_kind": "memory", "agent_id": "memory_agent"},
    ).role == RUNTIME_INTERNAL_ACTOR


def test_common_default_tool_package_matches_product_contract():
    assert BASELINE_SYSTEM_TOOL_NAME_ORDER == (
        "read_native_file",
        "write_native_file",
        "grep_search",
        "run_system_command",
        "command_session_broker",
        "read_background_output",
        "send_background_input",
        "terminate_background_command",
        "web_broker",
        "http_request",
        "download_media_for_vision",
        "vision_media_analyzer",
        "fetch_skill_instructions",
        "tool_observation_detail",
        "wait",
    )


def test_collaboration_tree_receives_common_and_plugin_tools_with_bounded_delegation():
    tool_names = list(BASELINE_SYSTEM_TOOL_NAME_ORDER) + [
        "ask_user",
        "agent_broker",
        "delegation_broker",
        "plugin_broker",
        "plugin_cli",
        "runtime_broker",
    ]
    tools = [_tool(name) for name in tool_names]

    supervisor = {
        item.name
        for item in filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    }
    direct_child = {
        item.name
        for item in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            route_context={"delegationDepth": 1},
            runtime_access=[],
        )
    }
    grandchild = {
        item.name
        for item in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            route_context={"delegationDepth": 2},
            runtime_access=[],
        )
    }
    internal_visual_actor = filter_visible_tools_for_actor(
        tools,
        actor="computer_use_visual_actor",
        runtime_access=[],
    )

    common = set(BASELINE_SYSTEM_TOOL_NAME_ORDER)
    assert common.issubset(supervisor)
    assert common.issubset(direct_child)
    assert common.issubset(grandchild)
    assert {"ask_user", "runtime_broker", "agent_broker", "delegation_broker", "plugin_broker", "plugin_cli"}.issubset(supervisor)
    assert {"delegation_broker", "plugin_broker", "plugin_cli"}.issubset(direct_child)
    assert "delegation_broker" not in grandchild
    assert {"plugin_broker", "plugin_cli"}.issubset(grandchild)
    assert "ask_user" not in direct_child | grandchild
    assert "agent_broker" not in direct_child | grandchild
    assert "runtime_broker" not in direct_child | grandchild
    assert internal_visual_actor == []


def test_supervisor_default_surface_hides_runtime_groups_but_keeps_broker_and_common_tools():
    tools = [
        _tool("runtime_broker"),
        _tool("agent_broker"),
        _tool("delegation_broker"),
        _tool("spec_broker"),
        _tool("read_native_file"),
        _tool("run_system_command"),
        _tool("http_request"),
        _tool("delegate_network_task"),
        _tool("memory_broker"),
        _tool("memory_recall"),
        _tool("mem_update"),
        _tool("computer_use_execute_task"),
        _tool("rpa_run_draft"),
        _tool("creative_media_create_job"),
        _tool("creative_media_compile_recipe"),
        _tool("creative_media_create_character_bible"),
        _tool("creative_media_create_edit_plan"),
        _tool("creative_media_alpha_inspect"),
        _tool("creative_media_psd_compose_template"),
    ]

    visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    names = {tool.name for tool in visible}

    assert {"runtime_broker", "agent_broker", "delegation_broker", "read_native_file", "http_request"}.issubset(names)
    assert "run_system_command" in names
    assert "spec_broker" not in names
    assert "delegate_network_task" not in names
    assert "memory_broker" in names
    assert "memory_recall" not in names
    assert "mem_update" not in names
    assert "computer_use_execute_task" not in names
    assert "rpa_run_draft" not in names
    assert "creative_media_create_job" not in names
    assert "creative_media_compile_recipe" not in names
    assert "creative_media_create_character_bible" not in names
    assert "creative_media_create_edit_plan" not in names
    assert "creative_media_alpha_inspect" not in names
    assert "creative_media_psd_compose_template" not in names


def test_supervisor_spec_broker_requires_request_spec_mode():
    tools = [
        _tool("runtime_broker"),
        _tool("spec_broker"),
        _tool("memory_broker"),
    ]

    default_visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    assert "spec_broker" not in {tool.name for tool in default_visible}

    visible_after_spec_mode = filter_visible_tools_for_actor(
        tools,
        actor="supervisor",
        route_context={"specMode": True},
    )
    assert "spec_broker" in {tool.name for tool in visible_after_spec_mode}


def test_network_supervisor_delegate_requires_explicit_runtime_grant():
    tools = [
        _tool("runtime_broker"),
        _tool("delegate_network_task"),
    ]

    default_visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    assert "delegate_network_task" not in {tool.name for tool in default_visible}

    command = runtime_broker.func(
        mode="grant",
        tool_group="network_supervisor.delegate",
        reason="explicit remote peer delegation",
        state={"current_route_context": {}},
        tool_call_id="call-network-supervisor-grant",
    )
    updated_context = command.update["current_route_context"]

    visible_after_grant = filter_visible_tools_for_actor(tools, actor="supervisor", route_context=updated_context)
    assert "delegate_network_task" in {tool.name for tool in visible_after_grant}


def test_runtime_broker_grant_makes_group_visible_for_same_run_next_step(monkeypatch):
    _set_pack_runtime_installed(monkeypatch, True)
    assert RUNTIME_TOOL_GROUPS["computer_use.control"]["toolNames"] == [
        "computer_use_desktop_capabilities",
        "computer_use_observe_scene",
        "computer_use_execute_task",
    ]

    command = runtime_broker.func(
        mode="grant",
        tool_group="computer_use.control",
        reason="need screen inspection",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker",
    )
    updated_context = command.update["current_route_context"]
    assert runtime_access_from_route_context(updated_context) == ["computer_use.control"]

    tools = [
        _tool("runtime_broker"),
        _tool("computer_use_desktop_capabilities"),
        _tool("computer_use_observe_scene"),
        _tool("computer_use_execute_task"),
        _tool("computer_use_click"),
    ]
    visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context=updated_context)
    names = {tool.name for tool in visible}

    assert {"computer_use_desktop_capabilities", "computer_use_observe_scene", "computer_use_execute_task"}.issubset(names)
    assert "computer_use_click" not in names


def test_feature_pack_gated_runtime_groups_hide_when_not_installed(monkeypatch):
    _set_pack_runtime_installed(monkeypatch, False)

    catalog = runtime_broker.func(
        mode="list",
        detail_level="catalog",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker-catalog",
    )
    catalog_payload = _tool_message_payload(catalog)
    catalog_groups = {item.get("group") for item in catalog_payload["availableGroups"]}
    assert "computer_use.control" not in catalog_groups
    assert "rpa.run" not in catalog_groups

    grant = runtime_broker.func(
        mode="grant",
        tool_group="computer_use.control",
        reason="need screen inspection",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker-missing-pack-grant",
    )
    grant_payload = _tool_message_payload(grant)
    assert grant_payload["activeGrants"] == []
    assert grant_payload["rejected"] == ["computer_use.control"]

    route = runtime_broker.func(
        mode="route",
        need={"kind": "computer_use", "source": "supervisor", "reason": "need real desktop"},
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker-missing-pack-route",
    )
    route_payload = _tool_message_payload(route)
    assert route_payload["ok"] is False
    assert route_payload["error"] == "runtime_feature_pack_required"
    assert route_payload["detailRef"] == "runtimeRegistry.featurePacks.computer_use_desktop"
    assert route.update["runtime_dispatch_status"]["blocked"] is True

    summary = capability_registry.build_supervisor_summary(user_query="操作真实桌面窗口")
    assert "kind=computer_use" not in summary
    assert "computer_use.control" not in summary


def _tool_message_payload(command):
    return json.loads(command.update["messages"][0].content)


def test_runtime_broker_default_list_is_compact_and_catalog_is_explicit():
    compact = runtime_broker.func(
        mode="list",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker",
    )
    payload = _tool_message_payload(compact)
    rendered = compact.update["messages"][0].content

    assert len(rendered) < 1500
    assert payload["detailMode"] == "summary"
    assert payload["availableGroups"]
    assert len(payload["availableGroups"]) <= 6
    assert all("toolNames" not in item for item in payload["availableGroups"])
    assert payload["omitted"]["toolNames"] > 0
    assert payload["omitted"]["availableGroups"] >= 0
    assert "mode='route'" in payload["recommendedNextAction"]

    catalog = runtime_broker.func(
        mode="list",
        detail_level="catalog",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker",
    )
    catalog_payload = _tool_message_payload(catalog)
    assert catalog_payload["detailMode"] == "catalog"
    assert any(item.get("toolNames") for item in catalog_payload["availableGroups"])


def test_runtime_broker_grant_does_not_repeat_catalog_by_default():
    command = runtime_broker.func(
        mode="grant",
        tool_group="research.core",
        reason="need research plan",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-broker",
    )
    payload = _tool_message_payload(command)
    rendered = command.update["messages"][0].content

    assert len(rendered) < 1500
    assert payload["activeGrants"]
    assert payload["changed"]
    assert payload["availableGroups"] == []


def test_runtime_broker_route_creates_episode_and_grants_access():
    command = runtime_broker.func(
        mode="route",
        need={"kind": "research", "source": "supervisor", "reason": "need multi-source evidence"},
        state={"current_route_context": {}},
        tool_call_id="call-runtime-route",
    )
    payload = _tool_message_payload(command)
    updated_context = command.update["current_route_context"]

    assert payload["mode"] == "route"
    assert payload["episode"]["kind"] == "research"
    assert payload["episode"]["state"] == "queued"
    assert payload["episode"]["continuationTarget"] == "runtime_episode_runner"
    assert payload["queuedEpisodeId"] == payload["episode"]["episodeId"]
    assert payload["episodeKind"] == "research"
    assert payload["nextAction"] == "runtime_episode"
    assert runtime_access_from_route_context(updated_context) == ["research.core"]
    assert updated_context["capabilityEpisodes"][-1]["kind"] == "research"
    assert updated_context["capabilityEpisodes"][-1]["state"] == "queued"
    assert command.update["runtime_dispatch_status"]["nextAction"] == "wait_episode"


def _ready_engineering_handoff_state() -> dict:
    artifact_ref = "git://repo_demo/commit_verified"
    handoff = {
        "handoffRefId": "handoff-engineering-ready",
        "producerEpisodeId": "episode-engineering-ready",
        "kind": "engineering_patch_bundle",
        "status": "ready",
        "artifactRefs": [{"kind": "git_changeset", "ref": artifact_ref, "accepted": True}],
        "delegationHandoff": {
            "results": [
                {
                    "status": "ok",
                    "artifactRefs": [{"kind": "git_changeset", "ref": artifact_ref}],
                    "verificationResults": [{"status": "verified", "passed": True}],
                }
            ]
        },
    }
    return {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "handoff_ready",
            "handoffCount": 1,
        },
        "current_route_context": {"handoffRefs": [handoff]},
        "messages": [
            HumanMessage(content="修复并验证目标文件。"),
            HumanMessage(
                content="[Runtime Episode Handoff Ready]",
                additional_kwargs={"v8_governance_type": "runtime_handoff"},
            ),
        ],
    }


def _read_only_prior_handoff_need() -> dict:
    return {
        "kind": "engineering",
        "source": "supervisor",
        "reason": "Re-check the evidence already returned by the worker.",
        "inputs": {
            "taskBriefs": [
                {
                    "taskBriefId": "duplicate-read-only-verification",
                    "goal": "Re-run the same read-only verification.",
                    "context": {"priorRefs": ["git://repo_demo/commit_verified"]},
                    "readOnly": True,
                    "writeRequired": False,
                    "writeSet": [],
                    "expectedOutputs": ["Repeated verification output"],
                    "acceptanceContract": ["The prior output is reproduced."],
                }
            ]
        },
    }


def test_runtime_broker_reuses_current_governed_handoff_for_duplicate_read_only_check(monkeypatch):
    monkeypatch.setattr(
        native_runtime,
        "enqueue_runtime_episode",
        lambda *_args, **_kwargs: pytest.fail("duplicate verification must not enqueue a new episode"),
    )
    monkeypatch.setattr(native_runtime, "_emit_runtime_episode_event", lambda *_args, **_kwargs: None)

    command = runtime_broker.func(
        mode="route",
        need=_read_only_prior_handoff_need(),
        state=_ready_engineering_handoff_state(),
        tool_call_id="call-runtime-reuse-ready-handoff",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is True
    assert payload["changed"] == []
    assert payload["routeBriefQuality"] == {
        "status": "reused",
        "reason": "current_governed_handoff_evidence",
        "blocking": False,
    }
    assert "No duplicate runtime episode was created" in payload["summary"]
    assert command.update["current_route_context"]["runtimeHandoffReuse"]["reason"] == (
        "same_run_read_only_evidence_reuse"
    )
    assert "runtime_dispatch_status" not in command.update


def test_runtime_broker_allows_explicit_later_user_reverification(monkeypatch):
    monkeypatch.setattr(
        native_runtime,
        "enqueue_runtime_episode",
        lambda episode, **_kwargs: {**episode, "state": "queued"},
    )
    monkeypatch.setattr(native_runtime, "_emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    state = _ready_engineering_handoff_state()
    state["messages"].append(HumanMessage(content="请基于这份证据重新独立验证一次。"))

    command = runtime_broker.func(
        mode="route",
        need=_read_only_prior_handoff_need(),
        state=state,
        tool_call_id="call-runtime-explicit-reverify",
    )
    payload = _tool_message_payload(command)

    assert payload["episodeKind"] == "engineering"
    assert payload["queuedEpisodeId"]
    assert command.update["runtime_dispatch_status"]["nextAction"] == "wait_episode"


def test_runtime_broker_rejects_manual_wait_episode_polling():
    command = runtime_broker.func(
        mode="wait_episode",
        state={
            "current_route_context": {
                "capabilityEpisodes": [
                    {
                        "episodeId": "episode-waitable",
                        "kind": "engineering",
                        "state": "queued",
                        "reason": "approved_spec_runtime_execution",
                    }
                ]
            }
        },
        tool_call_id="call-runtime-wait",
    )
    payload = _tool_message_payload(command)

    assert payload["mode"] == "wait_episode"
    assert payload["ok"] is False
    assert payload["error"] == "manual_runtime_polling_forbidden"
    assert "graph" in payload["summary"].lower()


def test_runtime_broker_accepts_explicit_empty_string_write_set_for_read_only_episode():
    command = runtime_broker.func(
        mode="route",
        need={
            "kind": "engineering",
            "source": "supervisor",
            "reason": "produce a read-only engineering plan",
            "inputs": {
                "taskBriefs": [
                    {
                        "taskBriefId": "plan-only",
                        "goal": "Return a reviewed engineering plan without writing files.",
                        "readOnly": True,
                        "writeRequired": False,
                        "writeSet": "",
                        "dependency": "",
                        "expectedOutputs": ["engineering plan handoff"],
                        "acceptance": {"must": ["No workspace files are written."]},
                    }
                ]
            },
        },
        state={"current_route_context": {}},
        tool_call_id="call-runtime-read-only-empty-write-set",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is True
    assert payload["episodeKind"] == "engineering"
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    assert episode["inputs"]["taskBriefs"][0]["writeSet"] == []
    assert episode["inputs"]["taskBriefs"][0]["dependency"] == []


def test_runtime_broker_accepts_canonical_plural_dependencies_and_keeps_internal_lineage_key():
    command = runtime_broker.func(
        mode="route",
        need={
            "kind": "engineering",
            "source": "supervisor",
            "reason": "run an ordered two-step verification",
            "inputs": {
                "taskBriefs": [
                    {
                        "taskBriefId": "step-1",
                        "goal": "Prepare the evidence.",
                        "readOnly": True,
                        "expectedOutputs": ["evidence"],
                        "acceptanceContract": ["evidence is present"],
                    },
                    {
                        "taskBriefId": "step-2",
                        "goal": "Review the evidence.",
                        "readOnly": True,
                        "dependencies": ["step-1"],
                        "expectedOutputs": ["review"],
                        "acceptanceContract": ["review is complete"],
                    },
                ]
            },
        },
        state={"current_route_context": {}},
        tool_call_id="call-runtime-canonical-dependencies",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is True
    task_briefs = command.update["current_route_context"]["capabilityEpisodes"][-1]["inputs"]["taskBriefs"]
    assert task_briefs[1]["dependency"] == ["step-1"]


def test_runtime_broker_rejects_nonempty_string_write_set_as_invalid_typed_need():
    command = runtime_broker.func(
        mode="route",
        need={
            "kind": "engineering",
            "source": "supervisor",
            "reason": "invalid write set shape",
            "inputs": {
                "taskBriefs": [
                    {
                        "taskBriefId": "invalid-write-set",
                        "goal": "Write one file.",
                        "writeRequired": True,
                        "writeSet": "result.md",
                        "expectedOutputs": ["result.md"],
                        "acceptance": {"must": ["result.md exists"]},
                    }
                ]
            },
        },
        state={"current_route_context": {}},
        tool_call_id="call-runtime-invalid-write-set",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "typed_need_invalid"
    assert payload["routeBriefQuality"]["validationErrors"][0]["field"].endswith("writeSet")
    assert payload["parameterGuidance"]["canonicalTaskArray"] == "need.inputs.taskBriefs"


def test_runtime_broker_advertises_one_canonical_task_array_with_typed_descriptions():
    schema = convert_to_openai_tool(runtime_broker)["function"]["parameters"]
    need = schema["properties"]["need"]["anyOf"][0]
    inputs = need["properties"]["inputs"]

    assert set(inputs["properties"]) == {"workspacePath", "taskBriefs", "proofExpectations"}
    task_properties = inputs["properties"]["taskBriefs"]["items"]["properties"]
    assert all(str(item.get("description") or "").strip() for item in task_properties.values())
    assert "dependency" not in task_properties
    assert task_properties["dependencies"]["type"] == "array"
    assert "never pass an empty string" in task_properties["dependencies"]["description"]


def test_runtime_broker_keeps_legacy_worker_briefs_read_compatible_without_advertising_alias():
    command = runtime_broker.func(
        mode="route",
        need={
            "kind": "research",
            "reason": "verify legacy route compatibility",
            "inputs": {
                "workerBriefs": [
                    {
                        "taskBriefId": "legacy-research-task",
                        "goal": "Collect one bounded evidence result.",
                        "readOnly": True,
                        "expectedOutputs": ["evidence summary"],
                        "acceptanceContract": ["one evidence result is returned"],
                    }
                ]
            },
        },
        state={"current_route_context": {}},
        tool_call_id="call-runtime-legacy-worker-briefs",
    )

    payload = _tool_message_payload(command)
    assert payload["ok"] is True
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    assert episode["inputs"]["workerBriefs"][0]["taskBriefId"] == "legacy-research-task"


def test_delegation_broker_rejects_manual_local_observation_polling():
    command = delegation_broker.func(
        mode="observe",
        delegation_id="delegation_local_task",
        state={"current_route_context": {}},
        tool_call_id="call-delegation-observe-local",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "manual_local_delegation_polling_forbidden"
    assert payload["recommendedNextAction"] == "wait_for_graph_handoff"


@pytest.mark.parametrize(
    ("missing_key", "expected_missing"),
    [
        ("writeSet", "writeSet"),
        ("expectedOutputs", "expectedOutputs"),
        ("acceptanceContract", "acceptance"),
    ],
)
def test_runtime_broker_blocks_incomplete_engineering_write_contract(missing_key, expected_missing):
    task = {
        "taskBriefId": "write-task",
        "goal": "Write result.md and verify it.",
        "context": {"source": "supervisor_current_turn"},
        "writeRequired": True,
        "writeSet": ["result.md"],
        "expectedOutputs": ["result.md"],
        "acceptanceContract": {"must": ["result.md exists and matches the request."]},
    }
    task.pop(missing_key)

    command = runtime_broker.func(
        mode="route",
        need={
            "kind": "engineering",
            "reason": "write_contract_test",
            "inputs": {"taskBriefs": [task]},
        },
        state={"current_route_context": {}},
        tool_call_id=f"call-runtime-missing-{missing_key}",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "write_task_contract_incomplete"
    failures = payload["routeBriefQuality"]["tasks"]
    assert failures[0]["taskBriefId"] == "write-task"
    assert expected_missing in failures[0]["missingFields"]


def test_runtime_broker_route_does_not_inherit_legacy_planner_tasks():
    command = runtime_broker.func(
        mode="route",
        need={"kind": "delegation", "source": "supervisor", "reason": "parallel implementation"},
        state={
            "current_route_context": {},
            "planner_plan": {
                "taskBriefs": [
                    {
                        "title": "Implement UI shell",
                        "goal": "Build the visible application shell.",
                        "runtimeAccess": ["memory.read"],
                    }
                ]
            },
        },
        tool_call_id="call-runtime-route",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "task_brief_required"
    assert command.update["current_route_context"] == {}


def test_runtime_broker_route_uses_only_explicit_supervisor_briefs():
    command = runtime_broker.func(
        mode="route",
        need={
            "kind": "delegation",
            "source": "supervisor",
            "reason": "parallel implementation",
            "inputs": {
                "workerBriefs": [
                    {
                        "taskBriefId": "supervisor-explicit-task",
                        "title": "Use the user-approved brief",
                        "goal": "Implement the exact current user request.",
                        "context": {"source": "supervisor_current_turn"},
                        "expectedOutput": "A concise proof-backed handoff.",
                        "acceptance": "Matches the current user request.",
                        "detailRefs": ["conversation://current-turn"],
                    }
                ]
            },
        },
        state={"current_route_context": {}},
        tool_call_id="call-runtime-explicit-supervisor",
    )
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert episode["inputs"]["workerBriefs"][0]["taskBriefId"] == "supervisor-explicit-task"
    assert episode["inputs"]["workerBriefs"][0]["goal"] == "Implement the exact current user request."
    assert episode["inputs"]["workerBriefs"][0]["context"]["source"] == "supervisor_current_turn"


def test_delegation_broker_description_requires_complete_explicit_task_briefs():
    description = str(getattr(delegation_broker, "description", "") or "")

    for phrase in ("goal", "context", "expected output", "acceptance criteria", "constraints", "detailRefs"):
        assert phrase in description
    assert "Do not dispatch vague ID-only tasks" in description


def test_runtime_broker_route_rejects_untyped_json_need_string():
    command = runtime_broker.func(
        mode="route",
        need=json.dumps(
            {
                "tool": "write_native_file",
                "reason": "blocked direct project mutation",
                "inputs": {"workspacePath": r"E:\Projects\test7"},
            }
        ),
        state={"current_route_context": {}},
        tool_call_id="call-runtime-route-json",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "typed_need_required"


@pytest.mark.parametrize(
    "need, expected_field",
    [
        ({"kind": "engineering"}, "reason"),
        ({"kind": "unknown", "reason": "route work"}, "kind"),
        (
            {
                "kind": "engineering",
                "reason": "route work",
                "inputs": {"taskBriefs": [{"goal": "Write result.md"}]},
            },
            "inputs.taskBriefs.0.taskBriefId",
        ),
    ],
)
def test_runtime_broker_route_rejects_invalid_typed_need_contract(need, expected_field):
    command = runtime_broker.func(
        mode="route",
        need=need,
        state={"current_route_context": {}},
        tool_call_id="call-runtime-route-invalid-typed-need",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "typed_need_invalid"
    fields = [item["field"] for item in payload["routeBriefQuality"]["validationErrors"]]
    assert expected_field in fields
    assert command.update["runtime_dispatch_status"]["nextAction"] == "repair_task_contract"
    assert command.update["current_route_context"] == {}
    assert command.update["runtime_dispatch_status"]["nextAction"] == "repair_task_contract"


def test_runtime_broker_route_requires_task_brief_before_enqueue_for_engineering():
    with bind_runtime_context(
        session_id="session-route-binding",
        run_id="run-route-binding",
        rootRunId="root-route-binding",
        workspace_path=r"E:\Projects\test7",
    ):
        command = runtime_broker.func(
            mode="route",
            need={"kind": "engineering", "source": "supervisor", "reason": "blocked write"},
            state={"current_route_context": {}},
            tool_call_id="call-runtime-route-binding",
        )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "task_brief_required"
    assert "goal" in payload["routeBriefQuality"]["requiredFields"]
    assert "context" in payload["routeBriefQuality"]["requiredFields"]
    assert command.update["runtime_dispatch_status"]["blocked"] is True


def test_runtime_broker_route_binds_session_run_root_and_workspace_with_explicit_brief():
    with bind_runtime_context(
        session_id="session-route-binding",
        run_id="run-route-binding",
        rootRunId="root-route-binding",
        workspace_path=r"E:\Projects\test7",
    ):
        command = runtime_broker.func(
            mode="route",
            need={
                "kind": "engineering",
                "source": "supervisor",
                "reason": "bounded implementation",
                "inputs": {
                    "workerBriefs": [
                        {
                            "taskBriefId": "explicit-engineering-task",
                            "goal": "Patch the requested file after reading it.",
                            "context": {"workspacePath": r"E:\Projects\test7"},
                            "writeRequired": True,
                            "writeSet": ["src/feature.ts"],
                            "expectedOutputs": ["src/feature.ts"],
                            "acceptance": "Tests pass or blockers are reported.",
                            "detailRefs": ["conversation://current-turn"],
                        }
                    ]
                },
            },
            state={"current_route_context": {}},
            tool_call_id="call-runtime-route-binding-explicit",
        )
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert episode["sessionId"] == "session-route-binding"
    assert episode["session_id"] == "session-route-binding"
    assert episode["runId"] == "run-route-binding"
    assert episode["run_id"] == "run-route-binding"
    assert episode["rootRunId"] == "root-route-binding"
    assert episode["inputs"]["workspacePath"] == r"E:\Projects\test7"
    assert episode["inputs"]["workspace_path"] == r"E:\Projects\test7"
    assert episode["inputs"]["workerBriefs"][0]["taskBriefId"] == "explicit-engineering-task"


def test_runtime_broker_hydrates_approved_spec_into_execution_bundle(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="Build a tiny counter app.",
        feature_name="counter-app",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: Build a browser counter app with the marker SPEC_DRY_RUN.\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: Implement the UI in index.html and document usage in README.md.\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "### TASK-001: Implement counter artifact\n\n"
            "- runtimeLane: Engineering\n"
            "- dependsOn: []\n"
            "- specRefs: REQ-001, DES-001\n"
            "- inputRefs: approved requirements and design\n"
            "- expectedOutput: index.html and README.md\n"
            "- acceptance: index.html contains SPEC_DRY_RUN and a clickable increment button.\n"
            "- proofRequired: report touched files and smoke verification.\n"
            "- mvpSlice: index.html counter works before README polish.\n"
            "- independentAcceptance: reviewer can open index.html and inspect SPEC_DRY_RUN.\n"
        ),
    )
    approved = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approved["ok"] is True, (approved.get("analysis") or {}).get("hardBlockers")

    with bind_runtime_context(
        session_id="session-spec-distribution",
        run_id="run-spec-distribution",
        rootRunId="root-spec-distribution",
        workspace_path=str(workspace),
    ):
        command = runtime_broker.func(
            mode="route",
            runtime_kind="engineering",
            need={
                "kind": "engineering",
                "reason": "approved_spec_runtime_execution",
                "specId": spec_id,
            },
            state={"current_route_context": {}},
            tool_call_id="call-runtime-spec-distribution",
        )

    payload = _tool_message_payload(command)
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    inputs = episode["inputs"]
    bundle = inputs["specExecutionBundle"]
    task = inputs["workerBriefs"][0]

    assert payload["episodeKind"] == "engineering"
    assert bundle["status"] == "ready"
    assert bundle["specId"] == spec_id
    assert bundle["documents"]["requirements"]["content"].count("REQ-001") == 1
    assert bundle["documents"]["design"]["content"].count("DES-001") == 1
    assert bundle["tasks"][0]["taskId"] == "TASK-001"
    assert task["taskBriefId"] == "TASK-001"
    assert task["context"]["specId"] == spec_id
    assert task["context"]["taskDetailRef"] == f"spec://{spec_id}/tasks#TASK-001"
    assert task["context"]["mvpSlice"] == "index.html counter works before README polish."
    assert task["context"]["independentAcceptance"] == "reviewer can open index.html and inspect SPEC_DRY_RUN."
    assert "REQ-001" in task["context"]["stageContent"]["requirements"]
    assert "DES-001" in task["context"]["stageContent"]["design"]
    assert "SPEC_DRY_RUN" in task["context"]["taskExcerpt"]
    execution_contract = task["context"]["engineeringExecutionContract"]
    assert execution_contract["workspacePath"] == str(workspace)
    assert execution_contract["taskId"] == "TASK-001"
    assert "index.html" in execution_contract["allowedWorkset"]
    assert "README.md" in execution_contract["allowedWorkset"]
    assert execution_contract["sourceRefs"]["detailRefs"] == [
        bundle["documents"]["requirements"]["detailRef"],
        bundle["documents"]["design"]["detailRef"],
        f"spec://{spec_id}/tasks#TASK-001",
    ]
    assert any("outside the Active Workspace Root" in item for item in execution_contract["forbiddenScopes"])
    handoff_contract = task["context"]["handoffContract"]
    assert handoff_contract["type"] == "engineering_typed_handoff"
    assert "changedFiles" in handoff_contract["requiredFields"]
    assert "testResults" in handoff_contract["requiredFields"]
    assert task["engineeringTaskCapsule"]["specId"] == spec_id
    assert task["engineeringTaskCapsule"]["taskId"] == "TASK-001"
    assert task["engineeringTaskCapsule"]["allowedWorkset"] == ["index.html", "README.md"]
    assert "changedFiles" in task["engineeringTaskCapsule"]["handoffRequired"]
    assert runtime_access_from_route_context(command.update["current_route_context"]) == ["delegation.recursive"]


def test_runtime_broker_parses_chinese_t_id_spec_tasks_into_lane_briefs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="生成玲的 skill。",
        feature_name="ling-skill",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: 生成工作区 skill。\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: 六维调研后生成 SKILL.md。\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# 任务分解\n\n"
            "| 阶段 | 平行度 | 说明 |\n"
            "|------|--------|------|\n"
            "| T-03 ~ T-08 调研 Agent Swarm | 6路并行 | 六维信息采集 |\n\n"
            "### T-02: 创建 Skill 目录结构\n\n"
            "| 属性 | 值 |\n|------|-----|\n"
            "| **Runtime** | `engineering` |\n"
            "| **依赖** | T-01 |\n"
            "| **需求引用** | REQ-001 |\n"
            "| **设计引用** | DES-001 |\n\n"
            "**输出文件**：\n- `.agents/skills/ling-perspective/`\n\n"
            "**证明材料**：目录列表和路径存在性检查。\n\n"
            "**MVP 切片**：目录结构先可检查。\n\n"
            "**独立验收**：Reviewer 可以列出目录确认路径存在。\n\n"
            "### T-03: 调研 Agent 1 — 著作与系统性设定\n\n"
            "| 属性 | 值 |\n|------|-----|\n"
            "| **Runtime** | `research` (via delegation_broker) |\n"
            "| **依赖** | T-02 |\n"
            "| **需求引用** | REQ-001 |\n"
            "| **设计引用** | DES-001 |\n\n"
            "**输出文件**：\n- `references/research/01-writings.md`\n\n"
            "**验收标准**：\n- [ ] 来源数 >= 5\n\n"
            "**证明材料**：调研文档路径和来源数量。\n\n"
            "**MVP 切片**：先交付可读证据包。\n\n"
            "**独立验收**：Reviewer 可以打开 01-writings.md 抽查来源。\n\n"
            "### T-04: 调研 Agent 2 — 对话与剧情台词\n\n"
            "| 属性 | 值 |\n|------|-----|\n"
            "| **Runtime** | `research` (via delegation_broker) |\n"
            "| **依赖** | T-02 |\n"
            "| **需求引用** | REQ-001 |\n"
            "| **设计引用** | DES-001 |\n\n"
            "**输出文件**：\n- `references/research/02-conversations.md`\n\n"
            "**证明材料**：调研文档路径。\n\n"
            "**MVP 切片**：先交付可读证据包。\n\n"
            "**独立验收**：Reviewer 可以打开 02-conversations.md 抽查引用。\n\n"
            "### T-12: Skill 构建\n\n"
            "| 属性 | 值 |\n|------|-----|\n"
            "| **Runtime** | `engineering` |\n"
            "| **依赖** | T-04 |\n"
            "| **需求引用** | REQ-001 |\n"
            "| **设计引用** | DES-001 |\n\n"
            "**输出文件**：\n- `SKILL.md`\n"
            "**证明材料**：SKILL.md 路径和加载验证。\n\n"
            "**MVP 切片**：SKILL.md 能被加载。\n\n"
            "**独立验收**：Reviewer 可以检查 frontmatter 和入口说明。\n\n"
            "### T-13: 质量验证\n\n"
            "| 属性 | 值 |\n|------|-----|\n"
            "| **Runtime** | `engineering` (子 agent 执行测试) |\n"
            "| **依赖** | T-12 |\n"
            "| **需求引用** | REQ-001 |\n"
            "| **设计引用** | DES-001 |\n\n"
            "**输出文件**：\n- `verification-report.md`\n"
            "**证明材料**：验证报告路径。\n\n"
            "**MVP 切片**：核心检查项先通过。\n\n"
            "**独立验收**：Reviewer 可以阅读报告确认测试结果。\n"
        ),
    )
    approved = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approved["ok"] is True, (approved.get("analysis") or {}).get("hardBlockers")

    with bind_runtime_context(
        session_id="session-spec-tid",
        run_id="run-spec-tid",
        rootRunId="root-spec-tid",
        workspace_path=str(workspace),
    ):
        command = runtime_broker.func(
            mode="route",
            runtime_kind="engineering",
            need={"kind": "engineering", "reason": "approved_spec_runtime_execution", "specId": spec_id},
            state={"current_route_context": {}},
            tool_call_id="call-runtime-spec-tid",
        )

    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    inputs = episode["inputs"]
    bundle = inputs["specExecutionBundle"]
    worker_briefs = inputs["workerBriefs"]

    assert [task["taskId"] for task in bundle["tasks"]] == ["TASK-002", "TASK-003", "TASK-004", "TASK-012", "TASK-013"]
    by_id = {item["taskBriefId"]: item for item in worker_briefs}
    assert "TASK-RESEARCH" not in by_id
    for research_id, expected_title, expected_file in [
        ("TASK-003", "著作与系统性设定", "01-writings.md"),
        ("TASK-004", "剧情台词", "02-conversations.md"),
    ]:
        brief = by_id[research_id]
        assert brief["familyHint"] == "research"
        assert brief["deliverableKind"] == "evidence"
        assert brief["allowChildDelegation"] is False
        assert brief["context"]["taskDetailRef"] == f"spec://{spec_id}/tasks#{research_id}"
        assert expected_title in brief["context"]["taskExcerpt"]
        assert expected_file in "\n".join(brief["acceptanceTiers"]["must"])
        assert brief["routeQuery"] == brief["context"]["extensionsRouteQuery"]
        assert "Shared Spec context" in brief["context"]["specExecutionSummary"]
        research_contract = brief["context"]["engineeringExecutionContract"]
        assert research_contract["runtimeFamily"] == "research"
        assert research_contract["sourceRefs"]["taskId"] == research_id
        assert research_contract["expectedArtifacts"]
    assert by_id["TASK-012"]["familyHint"] == "engineering"
    assert by_id["TASK-012"]["deliverableKind"] == "skill_artifact"
    assert by_id["TASK-012"]["validateSkillArtifact"] is True
    assert by_id["TASK-012"]["writeRequired"] is True
    assert by_id["TASK-012"]["allowChildDelegation"] is False
    assert by_id["TASK-013"]["allowChildDelegation"] is True

    with bind_runtime_context(
        session_id="session-spec-tid-repair",
        run_id="run-spec-tid-repair",
        rootRunId="root-spec-tid-repair",
        workspace_path=str(workspace),
    ):
        repair_command = runtime_broker.func(
            mode="route",
            runtime_kind="engineering",
            need={
                "kind": "engineering",
                "reason": "repair_p0_skill_file_missing",
                "specId": spec_id,
                "taskRef": "T-12",
            },
            state={"current_route_context": {}},
            tool_call_id="call-runtime-spec-tid-repair",
        )

    repair_episode = repair_command.update["current_route_context"]["capabilityEpisodes"][-1]
    repair_inputs = repair_episode["inputs"]
    repair_briefs = repair_inputs["workerBriefs"]
    assert repair_inputs["targetCount"] == 1
    assert repair_inputs["selectedSpecTaskIds"] == ["TASK-012"]
    assert repair_inputs["specTaskFilter"]["omittedTaskCount"] == 4
    assert [task["taskBriefId"] for task in repair_briefs] == ["TASK-012"]
    assert repair_briefs[0]["context"]["taskDetailRef"] == f"spec://{spec_id}/tasks#TASK-012"


def test_runtime_broker_parses_bold_markdown_task_fields_without_ref_noise(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        feature_name="ling-perspective-skill",
        user_request="create ling perspective skill",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: Deliver the skill.\n- AC-REQ-001: Skill exists.\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: Use huashu-nuwa structure.\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "## TASK-001: Create Skill Directory Structure\n\n"
            "- **Lane:** engineering\n"
            "- **Depends:** —\n"
            "- **Refs:** Design §3 (Directory Structure), REQ-001\n"
            "- **Output:** `.agents/skills/ling-perspective/`\n"
            "- **Acceptance:** Directory listing confirms all paths exist.\n\n"
            "- **Proof:** Directory listing output.\n"
            "- **MVP Slice:** directory exists before content polish.\n"
            "- **Independent Acceptance:** reviewer can list the directory.\n\n"
            "## TASK-002: Research — Agent 1\n\n"
            "- **Lane:** research\n"
            "- **Depends:** TASK-001\n"
            "- **Refs:** REQ-001, Design §5 Agent 1, huashu-nuwa Phase 1\n"
            "- **Output:** `references/research/01-writings.md`\n"
            "- **Acceptance:** File exists with cited claims.\n\n"
            "- **Proof:** file path and source count.\n"
            "- **MVP Slice:** readable research note exists.\n"
            "- **Independent Acceptance:** reviewer can open the markdown and inspect citations.\n\n"
            "## TASK-003: SKILL.md Construction\n\n"
            "- **Lane:** engineering\n"
            "- **Depends:** TASK-002\n"
            "- **Refs:** REQ-001, Design §4, skill-template, skill-creator\n"
            "- **Output:** `SKILL.md`\n"
            "- **Acceptance:** Valid YAML frontmatter.\n"
            "- **Proof:** file path and frontmatter validation.\n"
            "- **MVP Slice:** SKILL.md loads before optional examples.\n"
            "- **Independent Acceptance:** reviewer can inspect YAML frontmatter.\n"
        ),
    )
    approved = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approved["ok"] is True, approved
    spec_brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=spec_id)
    pipeline = spec_brief["documents"]["tasks"]["pipelineDiagnostics"]
    assert pipeline["valid"] is True
    assert pipeline["missingFields"] == []

    with bind_runtime_context(
        session_id="session-spec-bold",
        run_id="run-spec-bold",
        rootRunId="root-spec-bold",
        workspace_path=str(workspace),
    ):
        command = runtime_broker.func(
            mode="route",
            runtime_kind="engineering",
            need={"kind": "engineering", "reason": "approved_spec_runtime_execution", "specId": spec_id},
            state={"current_route_context": {}},
            tool_call_id="call-runtime-spec-bold",
        )

    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    by_id = {item["taskBriefId"]: item for item in episode["inputs"]["workerBriefs"]}
    assert by_id["TASK-001"]["dependency"] == []
    assert by_id["TASK-001"]["familyHint"] == "engineering"
    assert "TASK-RESEARCH" not in by_id
    assert by_id["TASK-002"]["familyHint"] == "research"
    assert by_id["TASK-002"]["dependency"] == ["TASK-001"]
    assert by_id["TASK-002"]["context"]["taskDetailRef"] == f"spec://{spec_id}/tasks#TASK-002"
    assert by_id["TASK-002"]["routeQuery"] == by_id["TASK-002"]["context"]["extensionsRouteQuery"]
    assert "Shared Spec context" in by_id["TASK-002"]["context"]["specExecutionSummary"]
    assert by_id["TASK-003"]["dependency"] == ["TASK-002"]
    assert by_id["TASK-003"]["writeRequired"] is True


def test_runtime_broker_parses_live_style_chinese_role_and_output_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        feature_name="ling-perspective-skill",
        user_request="生成玲的 skill。",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: 生成工作区 skill。\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: 创建目录、调研并生成 SKILL.md。\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# 任务详情\n\n"
            "### TASK-001: 目录初始化与脚本复制\n\n"
            "| 字段 | 内容 |\n"
            "|------|------|\n"
            "| **任务ID** | TASK-001 |\n"
            "| **任务名称** | 目录初始化与脚本复制 |\n"
            "| **执行角色** | Engineering Agent |\n"
            "| **依赖关系** | 无（首个任务） |\n"
            "| **需求引用** | REQ-001 / 需求文档 §2.1 输出规范 |\n"
            "| **设计引用** | DES-001 / 设计文档 §2 目录结构设计 |\n\n"
            "**任务描述**：\n"
            "创建完整的自包含目录结构，并从huashu-nuwa skill复制必要的工具脚本。\n\n"
            "**预期输出路径**：\n"
            "- `.agents/skills/ling-perspective/`（根目录）\n"
            "- `.agents/skills/ling-perspective/scripts/`（脚本目录）\n"
            "- `.agents/skills/ling-perspective/references/research/`（调研结果目录）\n\n"
            "**验收标准**：\n"
            "1. 所有要求的目录都已创建\n"
            "2. `scripts/merge_research.py` 已复制\n\n"
            "**证明材料**：目录列表和文件存在性检查。\n\n"
            "**MVP 切片**：目录和脚本复制可独立验收。\n\n"
            "**独立验收**：Reviewer 可以检查三个目录和脚本文件。\n\n"
            "---\n\n"
            "### TASK-002: 官方设定与角色档案调研\n\n"
            "| 字段 | 内容 |\n"
            "|------|------|\n"
            "| **任务ID** | TASK-002 |\n"
            "| **任务名称** | 官方设定与角色档案调研 |\n"
            "| **执行角色** | Research Agent 1 |\n"
            "| **依赖关系** | TASK-001 |\n\n"
            "| **需求引用** | REQ-001 / 需求文档 §2.1 输出规范 |\n"
            "| **设计引用** | DES-001 / 设计文档 §2 调研目录设计 |\n\n"
            "**预期输出路径**：\n"
            "- `.agents/skills/ling-perspective/references/research/01-writings.md`\n"
            "**验收标准**：文档存在且包含来源摘要。\n"
            "**证明材料**：调研文档路径和摘要。\n"
            "**MVP 切片**：先交付可读角色档案。\n"
            "**独立验收**：Reviewer 可以打开文档抽查来源。\n"
        ),
    )
    approved = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approved["ok"] is True, (approved.get("analysis") or {}).get("hardBlockers")

    with bind_runtime_context(
        session_id="session-live-style-spec",
        run_id="run-live-style-spec",
        rootRunId="root-live-style-spec",
        workspace_path=str(workspace),
    ):
        command = runtime_broker.func(
            mode="route",
            runtime_kind="engineering",
            need={"kind": "engineering", "reason": "approved_spec_runtime_execution", "specId": spec_id},
            state={"current_route_context": {}},
            tool_call_id="call-runtime-live-style-spec",
        )

    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    by_id = {item["taskBriefId"]: item for item in episode["inputs"]["workerBriefs"]}

    assert by_id["TASK-001"]["familyHint"] == "engineering"
    assert by_id["TASK-001"]["deliverableKind"] == "artifact"
    assert by_id["TASK-001"]["writeRequired"] is True
    must_text = "\n".join(by_id["TASK-001"]["acceptanceTiers"]["must"])
    assert ".agents/skills/ling-perspective/scripts/" in must_text
    assert "TASK-RESEARCH" not in by_id
    assert by_id["TASK-002"]["familyHint"] == "research"
    assert by_id["TASK-002"]["context"]["taskDetailRef"] == f"spec://{spec_id}/tasks#TASK-002"
    assert "官方设定与角色档案调研" in by_id["TASK-002"]["context"]["taskExcerpt"]
    assert "01-writings.md" in "\n".join(by_id["TASK-002"]["acceptanceTiers"]["must"])
    assert by_id["TASK-002"]["routeQuery"] == by_id["TASK-002"]["context"]["extensionsRouteQuery"]
    assert "Shared Spec context" in by_id["TASK-002"]["context"]["specExecutionSummary"]


def test_runtime_broker_preserves_spec_research_fanout_without_route_compression(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        feature_name="ling-perspective-skill",
        user_request="生成玲的 skill。",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: 生成工作区 skill。\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: 创建目录、调研并生成 SKILL.md。\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
    research_tasks = "\n\n".join(
        [
            (
                f"#### TASK-{index:03d}: Research Agent {index - 1}\n"
                f"- **ID**: TASK-{index:03d}\n"
                "- **执行层级**: Research Runtime / Web Research Architect\n"
                "- **依赖**: TASK-001 完成\n"
                "- **需求引用**: REQ-001, DES-001\n"
                f"- **输出文件**: `.agents/skills/ling-perspective/references/research/0{index - 1}-part.md`\n"
                "- **验收标准**: 文件存在且满足质量要求\n"
            )
            for index in range(2, 8)
        ]
    )
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# 绝区零角色「玲」视角 Skill 执行任务清单\n\n"
            "#### TASK-001: 创建 Skill 目录结构\n"
            "- **ID**: TASK-001\n"
            "- **执行层级**: Supervisor 直接执行\n"
            "- **任务描述**: 创建完整的自包含目录结构\n"
            "- **需求引用**: REQ-001, DES-001\n"
            "- **预期输出**: `.agents/skills/ling-perspective/scripts/`\n\n"
            f"{research_tasks}\n\n"
            "#### TASK-009: 思维框架提炼\n"
            "- **ID**: TASK-009\n"
            "- **执行层级**: Engineering Runtime / 综合 Agent\n"
            "- **需求引用**: REQ-001, DES-001\n"
            "- **输出文件**: `.agents/skills/ling-perspective/references/framework.md`\n\n"
            "#### TASK-010: SKILL.md 构建\n"
            "- **ID**: TASK-010\n"
            "- **执行层级**: Engineering Runtime / 构建 Agent\n"
            "- **需求引用**: REQ-001, DES-001\n"
            "- **输出文件**: `.agents/skills/ling-perspective/SKILL.md`\n\n"
            "#### TASK-011: 三项测试验证\n"
            "- **ID**: TASK-011\n"
            "- **执行层级**: Engineering Runtime / 验证 Agent\n"
            "- **需求引用**: REQ-001, DES-001\n"
            "- **输出文件**: `.agents/skills/ling-perspective/verification-report.md`\n\n"
            "#### TASK-012: 生成最终交付文档\n"
            "- **ID**: TASK-012\n"
            "- **执行层级**: Supervisor 执行\n"
            "- **任务描述**: 生成最终交付文档\n"
            "- **需求引用**: REQ-001, DES-001\n"
            "- **输出文件**: `.agents/skills/ling-perspective/delivery-summary.md`\n"
        ),
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")

    with bind_runtime_context(
        session_id="session-spec-research-fanout",
        run_id="run-spec-research-fanout",
        rootRunId="root-spec-research-fanout",
        workspace_path=str(workspace),
    ):
        command = runtime_broker.func(
            mode="route",
            runtime_kind="engineering",
            need={"kind": "engineering", "reason": "approved_spec_runtime_execution", "specId": spec_id},
            state={"current_route_context": {}},
            tool_call_id="call-runtime-spec-research-fanout",
        )

    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    briefs = episode["inputs"]["workerBriefs"]
    ids = [item["taskBriefId"] for item in briefs]
    assert len(briefs) == 11
    assert "TASK-001" in ids
    assert "TASK-RESEARCH" not in ids
    assert "TASK-010" in ids
    assert all(task_id in ids for task_id in ["TASK-002", "TASK-003", "TASK-004", "TASK-005", "TASK-006", "TASK-007"])
    by_id = {item["taskBriefId"]: item for item in briefs}
    for index in range(2, 8):
        task_id = f"TASK-{index:03d}"
        research_brief = by_id[task_id]
        assert research_brief["familyHint"] == "research"
        assert research_brief["context"]["taskDetailRef"] == f"spec://{spec_id}/tasks#{task_id}"
        assert f"0{index - 1}-part.md" in "\n".join(research_brief["acceptanceTiers"]["must"])
        assert research_brief["routeQuery"] == research_brief["context"]["extensionsRouteQuery"]


def test_delegation_broker_refuses_generic_dispatch_for_ready_spec_episode():
    active_episode = {
        "kind": "engineering",
        "reason": "approved_spec_runtime_execution",
        "inputs": {
            "specExecutionBundle": {"kind": "SpecExecutionBundle", "status": "ready", "specId": "spec_ready"},
        },
    }
    with bind_runtime_context(session_id="session-spec-delegation", run_id="run-spec-delegation"):
        command = delegation_broker.func(
            mode="dispatch",
            state={"current_route_context": {"capabilityEpisodes": [active_episode]}},
            tool_call_id="call-delegation-spec-missing",
        )

    payload = _tool_message_payload(command)
    assert payload["ok"] is False
    assert payload["error"] == "spec_delegation_missing_tasks"
    assert payload["dispatchStatus"] == "missing_tasks"


def test_runtime_broker_list_with_episode_intent_requires_brief_but_catalog_stays_list():
    catalog = runtime_broker.func(
        mode="list",
        runtime_kind="engineering",
        reason="plan_only episode creation",
        detail_level="catalog",
        state={"current_route_context": {}},
        tool_call_id="call-runtime-catalog",
    )
    assert _tool_message_payload(catalog)["mode"] == "list"

    with bind_runtime_context(
        session_id="session-auto-route",
        run_id="run-auto-route",
        rootRunId="root-auto-route",
        workspace_path=r"E:\Projects\test7",
    ):
        command = runtime_broker.func(
            mode="list",
            runtime_kind="engineering",
            reason="plan_only episode creation for Engineering runtime",
            state={"current_route_context": {}},
            tool_call_id="call-runtime-list-route",
        )
    payload = _tool_message_payload(command)

    assert payload["mode"] == "route"
    assert payload["ok"] is False
    assert payload["error"] == "typed_need_required"
    assert command.update["current_route_context"] == {}
    assert command.update["runtime_dispatch_status"]["nextAction"] == "repair_task_contract"


def test_delegation_broker_missing_tasks_is_structured_and_diagnostic_only():
    command = delegation_broker.func(
        mode="dispatch",
        state={"current_route_context": {}},
        tool_call_id="call-delegation-empty",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "missing_tasks"
    assert payload["dispatchStatus"] == "missing_tasks"
    assert payload["missingTasks"] is True
    assert payload["diagnosticKey"] == "delegation_missing_tasks"
    assert payload["exampleTasks"]


def test_supervisor_manual_local_dispatch_requires_exact_registered_name():
    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor"):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "unnamed-local",
                    "goal": "Review one result.",
                    "expectedOutputs": ["A concise review."],
                    "acceptanceContract": "Report pass or fail with evidence.",
                    "toolPolicy": {"mode": "none"},
                }
            ],
            state={"current_route_context": {}},
            tool_call_id="call-unnamed-local",
        )

    payload = _tool_message_payload(command)
    assert payload["error"] == "target_agent_name_required"
    assert payload["missingTaskBriefIds"] == ["unnamed-local"]
    assert payload["availableAgents"]
    assert all(item.get("name") and "description" in item for item in payload["availableAgents"])


def test_direct_subagent_missing_tasks_returns_to_same_agent_for_one_contract_repair():
    state = {
        "current_route_context": {
            "delegationId": "subagent::parent-contract-repair",
            "delegationDepth": 1,
        }
    }
    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="direct_subagent",
        agent_id="implementation-engineer",
        delegation_id="subagent::parent-contract-repair",
        delegation_depth=1,
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks={},
            worker_briefs={},
            state=state,
            tool_call_id="call-direct-child-empty",
        )

    payload = _tool_message_payload(command)
    assert command.goto == "implementation-engineer"
    assert payload["error"] == "missing_tasks"
    assert payload["recommendedNextAction"] == "retry_dispatch_with_complete_flat_task"
    assert payload["exampleTasks"][0]["taskBriefId"] == "child-check-1"
    assert payload["exampleTasks"][0]["expectedOutputs"]


def test_direct_subagent_child_budget_is_enforced_before_episode_projection():
    state = {
        "current_route_context": {
            "delegationId": "subagent::parent-child-budget",
            "delegationDepth": 1,
            "taskBrief": {
                "taskBriefId": "parent-child-budget",
                "goal": "Complete the implementation and request one independent check.",
                "readSet": ["src/result.py"],
                "writeSet": [],
                "acceptanceContract": "Return one independent check.",
            },
        },
        "parallel_branch": {
            "childDelegationBudget": {"maxChildren": 1},
        },
    }
    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="direct_subagent",
        agent_id="implementation-engineer",
        delegation_id="subagent::parent-child-budget",
        delegation_depth=1,
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "verify-a",
                    "goal": "Independently verify result A.",
                    "expectedOutputs": ["A verification"],
                    "acceptanceContract": "Return evidence for A.",
                    "toolPolicy": {"mode": "none"},
                },
                {
                    "taskBriefId": "verify-b",
                    "goal": "Independently verify result B.",
                    "expectedOutputs": ["B verification"],
                    "acceptanceContract": "Return evidence for B.",
                    "toolPolicy": {"mode": "none"},
                },
            ],
            state=state,
            tool_call_id="call-child-budget-two",
        )

    payload = _tool_message_payload(command)
    assert command.goto == "implementation-engineer"
    assert payload["error"] == "delegation_budget_exceeded"
    assert payload["reason"] == "max_children_per_delegation_exceeded"
    assert payload["budget"]["maxChildrenPerDelegation"] == 1
    assert "parallel_invocations" not in command.update


def test_direct_subagent_cannot_redelegate_its_full_write_contract_to_grandchild():
    parent_task = {
        "taskBriefId": "parent-write",
        "goal": "Implement src/result.py, then request independent verification.",
        "writeRequired": True,
        "writeSet": ["src/result.py"],
        "expectedOutputs": ["src/result.py"],
        "acceptanceContract": "src/result.py is implemented and verified.",
    }
    state = {
        "current_route_context": {
            "delegationId": "subagent::parent-write",
            "delegationDepth": 1,
            "taskBrief": parent_task,
        },
        "parallel_branch": {
            "childDelegationBudget": {"maxChildren": 1},
        },
    }
    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="direct_subagent",
        agent_id="implementation-engineer",
        delegation_id="subagent::parent-write",
        delegation_depth=1,
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "grandchild-duplicate-write",
                    "goal": "Implement src/result.py for the parent.",
                    "writeRequired": True,
                    "writeSet": ["src/result.py"],
                    "expectedOutputs": ["src/result.py"],
                    "acceptanceContract": "src/result.py is implemented.",
                    "toolPolicy": {
                        "mode": "allowlist",
                        "allowedTools": ["read_native_file", "write_native_file"],
                    },
                }
            ],
            state=state,
            tool_call_id="call-grandchild-duplicate-write",
        )

    payload = _tool_message_payload(command)
    assert command.goto == "implementation-engineer"
    assert payload["error"] == "grandchild_write_authority_not_granted"
    assert payload["blockedTaskBriefIds"] == ["grandchild-duplicate-write"]
    assert "parallel_invocations" not in command.update


def test_managed_delegation_instruction_uses_child_worktree_not_parent_checkout(monkeypatch):
    monkeypatch.setattr(native_delegation, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(native_delegation, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    parent_workspace = r"C:\Users\test\.v8-agent-os\worktrees\repo\run\supervisor"
    child_workspace = r"C:\Users\test\.v8-agent-os\worktrees\repo\run\task-child"
    monkeypatch.setattr(
        native_delegation,
        "prepare_delegated_engineering_workspace",
        lambda **_kwargs: {
            "workspace_path": child_workspace,
            "original_workspace_path": r"E:\Projects\app",
            "worktree_id": "task-child",
            "worktree_root": child_workspace,
            "managed_engineering_execution": True,
        },
    )
    state = {
        "session_id": "session-managed-child",
        "run_id": "run-managed-child",
        "workspace_path": parent_workspace,
        "current_route_context": {"workspacePath": parent_workspace},
    }

    with bind_runtime_context(
        runtime_kind="delegation",
        actor_role="supervisor",
        agent_id="supervisor",
        session_id=state["session_id"],
        run_id=state["run_id"],
        workspace_path=parent_workspace,
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "managed-write",
                    "targetAgentName": "Implementation Engineer",
                    "goal": "Fix src/app.py and return evidence.",
                    "context": {"workspacePath": parent_workspace},
                    "writeRequired": True,
                    "writeSet": ["src/app.py"],
                    "expectedOutputs": ["src/app.py"],
                    "acceptanceContract": "src/app.py is fixed.",
                    "preferredAgentId": "implementation-engineer",
                }
            ],
            state=state,
            tool_call_id="call-managed-child-workspace",
        )

    send = list(command.goto)[0]
    instruction = send.arg["messages"][-1].content
    rendered_instruction = instruction.replace("\\\\", "\\")
    task_brief = send.arg["parallel_branch"]["taskBrief"]
    assert child_workspace in rendered_instruction
    assert parent_workspace not in rendered_instruction
    assert task_brief["workspacePath"] == child_workspace
    assert task_brief["engineeringTaskCapsule"]["workspacePath"] == child_workspace


def test_direct_subagent_children_are_disposable_parent_mirrors_with_peer_boundaries(monkeypatch):
    monkeypatch.setattr(native_delegation, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(native_delegation, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    state = {
        "session_id": "session-mirror-workers",
        "run_id": "run-mirror-workers",
        "current_route_context": {
            "delegationId": "subagent::parent-mirror",
            "delegationDepth": 1,
            "delegationNodeCount": 1,
            "taskBrief": {
                "taskBriefId": "parent-task",
                "goal": "Coordinate two independent checks.",
                "acceptanceContract": "Both checks return evidence.",
            },
        },
    }

    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="direct_subagent",
        agent_id="implementation-engineer",
        delegation_id="subagent::parent-mirror",
        delegation_depth=1,
        session_id=state["session_id"],
        run_id=state["run_id"],
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "mirror-a",
                    "goal": "Inspect component A and return evidence.",
                    "expectedOutputs": ["A evidence"],
                    "acceptanceContract": "Return the relevant line and conclusion.",
                    "preferredAgentId": "verification-engineer",
                    "toolPolicy": {"mode": "none"},
                },
                {
                    "taskBriefId": "mirror-b",
                    "goal": "Inspect component B and return evidence.",
                    "expectedOutputs": ["B evidence"],
                    "acceptanceContract": "Return the relevant line and conclusion.",
                    "preferredAgentId": "code-review-architect",
                    "toolPolicy": {"mode": "none"},
                },
            ],
            state=state,
            tool_call_id="call-mirror-workers",
        )

    branches = [send.arg["parallel_branch"] for send in list(command.goto)]
    assert [branch["agentId"] for branch in branches] == ["implementation-engineer", "implementation-engineer"]
    assert [branch["agentName"] for branch in branches] == [
        "Implementation Engineer · worker-01",
        "Implementation Engineer · worker-02",
    ]
    assert branches[0]["targetId"].endswith("worker-01")
    assert branches[1]["targetId"].endswith("worker-02")
    assert branches[0]["taskBrief"]["targetAgentName"] == "Implementation Engineer"
    assert branches[0]["taskBrief"]["ephemeralMirror"] is True
    peers_a = branches[0]["taskBrief"]["context"]["activeCollaborators"]
    peers_b = branches[1]["taskBrief"]["context"]["activeCollaborators"]
    assert peers_a[0]["name"] == "Implementation Engineer · worker-02"
    assert peers_b[0]["name"] == "Implementation Engineer · worker-01"


def test_delegation_broker_null_task_is_structured_and_diagnostic_only():
    command = delegation_broker.func(
        mode="dispatch",
        tasks=[{"taskBriefId": None, "goal": None, "preferredAgentId": None}],
        state={"current_route_context": {}},
        tool_call_id="call-delegation-null-task",
    )
    payload = _tool_message_payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "missing_tasks"
    assert payload["dispatchStatus"] == "missing_tasks"
    assert payload["missingTasks"] is True
    assert payload["diagnosticKey"] == "delegation_missing_tasks"
    assert "parallel_invocations" not in command.update


def test_supervisor_delegation_starts_new_top_level_tree_and_routes_risk_review(monkeypatch):
    monkeypatch.setattr(native_delegation, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(native_delegation, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    stale_episode_id = "episode_engineering_terminal"
    state = {
        "session_id": "session-supervisor-risk-review",
        "run_id": "run-supervisor-risk-review",
        "current_route_context": {
            "activeCapabilityEpisodeId": stale_episode_id,
            "delegationId": "subagent::stale-parent",
            "delegationDepth": 3,
            "delegationNodeCount": 7,
            "capabilityEpisodes": [
                {
                    "episodeId": stale_episode_id,
                    "kind": "engineering",
                    "state": "degraded",
                }
            ],
        },
    }

    with bind_runtime_context(
        runtime_kind="chat",
        agent_id="supervisor",
        session_id=state["session_id"],
        run_id=state["run_id"],
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                    {
                        "taskBriefId": "risk-review",
                        "targetAgentName": "Verification Engineer",
                        "goal": "Perform a final risk review of the research and engineering handoffs without writing files.",
                    "expectedOutput": "A concise verification report with blocking risks and evidence refs.",
                    "acceptanceContract": "Verify the result against both upstream handoffs and report pass or fail.",
                    "toolPolicy": {"mode": "none"},
                }
            ],
            state=state,
            tool_call_id="call-supervisor-risk-review",
        )

    send = list(command.goto)[0]
    branch = send.arg["parallel_branch"]
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    assert branch["agentId"] == "verification-engineer"
    assert branch["parentDelegationId"] is None
    assert branch["delegationDepth"] == 1
    assert branch["taskBrief"]["targetDefaultReason"] == "verification_task_signal"
    assert episode["parentEpisodeId"] == ""


def test_runtime_episode_supervisor_can_dispatch_top_level_delegation(monkeypatch):
    monkeypatch.setattr(native_delegation, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(native_delegation, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    state = {
        "session_id": "session-engineering-runtime-dispatch",
        "run_id": "run-engineering-runtime-dispatch",
        "workspace_path": "E:/Projects/runtime-dispatch",
        "delegationDispatchSource": "runtime_episode_runner",
        "current_route_context": {
            "activeCapabilityEpisodeId": "episode-engineering-runtime-dispatch",
            "capabilityEpisodes": [
                {
                    "episodeId": "episode-engineering-runtime-dispatch",
                    "rootEpisodeId": "episode-engineering-runtime-dispatch",
                    "kind": "engineering",
                    "state": "active",
                }
            ],
        },
    }

    with bind_runtime_context(
        runtime_kind="delegation",
        actor_role="supervisor",
        agent_id="supervisor",
        delegation_depth=0,
        session_id=state["session_id"],
        run_id=state["run_id"],
        workspace_path=state["workspace_path"],
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "engineering-worker",
                    "goal": "Inspect the assigned Engineering work package and return evidence.",
                    "expectedOutputs": ["A concise result with evidence."],
                    "acceptanceContract": "Return the result and evidence used.",
                    "preferredAgentId": "implementation-engineer",
                    "readOnly": True,
                    "writeSet": [],
                    "toolPolicy": {"mode": "none"},
                }
            ],
            state=state,
            tool_call_id="call-engineering-runtime-dispatch",
        )

    send = list(command.goto)[0]
    branch = send.arg["parallel_branch"]
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    assert branch["delegationDepth"] == 1
    assert branch["parentDelegationId"] is None
    assert branch["agentId"] == "implementation-engineer"
    assert episode["parentEpisodeId"] == "episode-engineering-runtime-dispatch"
    assert episode["rootEpisodeId"] == "episode-engineering-runtime-dispatch"
    assert episode["ownerEpisodeId"] == "episode-engineering-runtime-dispatch"


def test_supervisor_dispatch_persists_recursive_policy_on_durable_task_brief(monkeypatch):
    monkeypatch.setattr(native_delegation, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(native_delegation, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    state = {
        "session_id": "session-supervisor-recursive-policy",
        "run_id": "run-supervisor-recursive-policy",
        "current_route_context": {},
    }

    with bind_runtime_context(
        runtime_kind="chat",
        actor_role="supervisor",
        agent_id="supervisor",
        session_id=state["session_id"],
        run_id=state["run_id"],
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                    {
                        "taskBriefId": "recursive-read",
                        "targetAgentName": "Implementation Engineer",
                        "goal": "Read README.md and delegate one independent read-only verification.",
                    "expectedOutputs": ["direct result", "child result"],
                    "acceptanceContract": "Both read-only results agree.",
                    "preferredAgentId": "implementation-engineer",
                    "toolPolicy": {
                        "mode": "allowlist",
                        "allowedTools": ["read_native_file", "delegation_broker"],
                    },
                }
            ],
            allow_child_delegation=True,
            child_delegation_budget={"maxChildren": 1, "maxDepth": 2},
            state=state,
            tool_call_id="call-supervisor-recursive-policy",
        )

    task_brief = list(command.goto)[0].arg["parallel_branch"]["taskBrief"]
    assert task_brief["allowChildDelegation"] is True
    assert task_brief["childDelegationBudget"] == {"maxChildren": 1, "maxDepth": 2}
    assert task_brief["delegationPolicy"]["allowChildDelegation"] is True
    assert task_brief["delegationPolicy"]["childDelegationBudget"] == {"maxChildren": 1, "maxDepth": 2}


def test_delegation_maps_verifier_worker_type_to_verification_agent():
    tasks = native_delegation._apply_delegation_target_defaults(
        [
            {
                "goal": "Review the supplied evidence and return a risk register.",
                "familyHint": "engineering",
                "preferredWorkerType": "verifier",
            }
        ]
    )

    assert tasks[0]["preferredAgentId"] == "verification-engineer"
    assert tasks[0]["targetDefaultReason"] == "preferred_worker_type_alias"


def test_delegation_retires_project_planner_target_and_routes_verification_work():
    tasks = native_delegation._apply_delegation_target_defaults(
        [
            {
                "goal": "Perform a final risk review and verify both upstream handoffs.",
                "familyHint": "engineering",
                "preferredAgentId": "project-planner",
            }
        ]
    )

    assert tasks[0]["preferredAgentId"] == "verification-engineer"
    assert tasks[0]["targetDefaultReason"] == "verification_task_signal"


def test_delegation_routes_structured_workspace_task_to_engineering_family():
    tasks = native_delegation._apply_delegation_target_defaults(
        [
            {
                "taskBriefId": "read-workspace-file",
                "goal": "Read README.md and return its first heading.",
                "readSet": ["README.md"],
                "toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file"]},
            }
        ]
    )

    assert tasks[0]["familyHint"] == "engineering"
    assert tasks[0]["targetDefaultReason"] == "structured_workspace_task"


def test_delegation_keeps_explicit_specialist_family_for_structured_task():
    tasks = native_delegation._apply_delegation_target_defaults(
        [
            {
                "taskBriefId": "creative-source-review",
                "goal": "Inspect the supplied visual source before rendering.",
                "familyHint": "creative_media",
                "readSet": ["assets/reference.png"],
                "runtimeAccess": ["creative_media.jobs"],
            }
        ]
    )

    assert tasks[0]["familyHint"] == "creative_media"
    assert tasks[0].get("targetDefaultReason") != "structured_workspace_task"


def test_handoff_only_readonly_delegation_receives_no_workspace_tools():
    tasks = native_delegation._apply_delegation_tool_defaults(
        [
            {
                "taskBriefId": "handoff-review",
                "goal": "Review the injected upstream handoffs.",
                "context": {
                    "readOnly": True,
                    "upstreamHandoffs": [{"status": "ready", "summary": "Evidence is injected."}],
                },
                "readSet": [],
                "writeSet": [],
                "toolPolicy": {"mode": "default"},
            }
        ]
    )

    assert tasks[0]["toolPolicy"] == {"mode": "none", "allowedTools": [], "forbiddenTools": []}
    assert tasks[0]["allowedTools"] == []
    assert "no readSet" in tasks[0]["context"]["handoffConsumptionDiscipline"]


def test_handoff_review_with_explicit_read_set_keeps_declared_tool_policy():
    tasks = native_delegation._apply_delegation_tool_defaults(
        [
            {
                "taskBriefId": "handoff-and-file-review",
                "goal": "Review injected evidence and one source file.",
                "context": {
                    "readOnly": True,
                    "upstreamHandoffs": [{"status": "ready", "summary": "Evidence is injected."}],
                },
                "readSet": ["src/runtime.ts"],
                "writeSet": [],
                "toolPolicy": {"mode": "default"},
            }
        ]
    )

    assert tasks[0]["toolPolicy"]["mode"] == "default"


def test_subagent_recursive_delegation_keeps_explicit_parent():
    parent = native_delegation._delegation_parent_episode_id(
        {
            "delegationId": "subagent::active-parent",
            "activeCapabilityEpisodeId": "subagent::active-parent",
            "capabilityEpisodes": [
                {
                    "episodeId": "subagent::active-parent",
                    "kind": "delegation",
                    "state": "active",
                }
            ],
        },
        {"runtime_kind": "subagent", "subagent_id": "code-review-architect"},
    )

    assert parent == "subagent::active-parent"


def test_direct_subagent_dispatches_one_grandchild_and_grandchild_is_terminal(monkeypatch):
    monkeypatch.setattr(native_delegation, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(native_delegation, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    state = {
        "session_id": "session-child-tree",
        "run_id": "run-child-tree",
        "project_id": "project-child-tree",
        "workspace_id": "workspace-child-tree",
        "workspace_path": "E:/Projects/child-tree",
        "safety_approval_mode": "reduced",
        "current_route_context": {
            "delegationId": "subagent::parent",
            "activeCapabilityEpisodeId": "subagent::parent",
            "delegationDepth": 1,
            "delegationNodeCount": 1,
            "capabilityEpisodes": [
                {
                    "episodeId": "subagent::parent",
                    "rootEpisodeId": "episode-engineering-root",
                    "kind": "delegation",
                    "state": "active",
                }
            ],
            "taskBrief": {
                "taskBriefId": "parent-task",
                "goal": "Review the implementation and return evidence.",
                "readSet": ["src/page.tsx"],
                "writeSet": [],
                "acceptanceContract": "Return a bounded review.",
            },
        },
    }

    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="direct_subagent",
        agent_id="code-review-architect",
        delegation_id="subagent::parent",
        delegation_depth=1,
        session_id=state["session_id"],
        run_id=state["run_id"],
        project_id=state["project_id"],
        workspace_id=state["workspace_id"],
        workspace_path=state["workspace_path"],
        safety_approval_mode="reduced",
    ):
        command = delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "grandchild-review",
                    "goal": "Independently verify one implementation result and return evidence.",
                    "expectedOutputs": ["A concise verification result."],
                    "acceptanceContract": {
                        "must": ["孙 Agent must report pass/fail and the evidence used."]
                    },
                    "preferredAgentId": "verification-engineer",
                    "runtimeAccess": ["delegation.recursive"],
                    "toolPolicy": {
                        "mode": "allowlist",
                        "allowedTools": ["read_native_file", "delegation_broker"],
                    },
                }
            ],
            state=state,
            tool_call_id="call-child-grandchild",
        )

    send = list(command.goto)[0]
    branch = send.arg["parallel_branch"]
    assert branch["parentDelegationId"] == "subagent::parent"
    assert branch["delegationDepth"] == 2
    assert branch["allowChildDelegation"] is False
    assert branch["taskBrief"]["delegationDepth"] == 2
    assert branch["taskBrief"]["writeSet"] == []
    assert branch["taskBrief"]["allowChildDelegation"] is False
    assert branch["taskBrief"]["requireChildDelegation"] is False
    assert branch["taskBrief"]["childDelegationPolicyExplicit"] is True
    assert branch["taskBrief"]["childDelegationBudget"] == {}
    assert "delegation.recursive" not in branch["taskBrief"]["runtimeAccess"]
    assert "delegation_broker" not in branch["taskBrief"]["allowedTools"]
    durable_episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
    assert durable_episode["rootEpisodeId"] == "episode-engineering-root"
    durable_brief = durable_episode["inputs"]["workerBriefs"][0]
    assert durable_brief["delegationDepth"] == 2
    assert durable_brief["allowChildDelegation"] is False
    assert "delegation.recursive" not in durable_brief["runtimeAccess"]
    assert "delegation_broker" not in durable_brief["allowedTools"]

    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="grandchild",
        agent_id="verification-engineer",
        delegation_id=branch["delegationId"],
        delegation_depth=2,
    ):
        terminal = delegation_broker.func(
            mode="dispatch",
            tasks=[{"taskBriefId": "forbidden", "goal": "Do not create this child."}],
            state=send.arg,
            tool_call_id="call-grandchild-forbidden",
        )
    assert _tool_message_payload(terminal)["error"] == "delegation_depth_terminal"


def test_delegation_root_uses_bound_runtime_context_when_state_projection_is_minimal():
    with bind_runtime_context(root_episode_id="episode-engineering-root"):
        root_id = native_delegation._delegation_root_episode_id(
            {},
            parent_episode_id="subagent::direct-parent",
            runtime_owner_episode_id="",
        )

    assert root_id == "episode-engineering-root"


def test_delegation_root_falls_back_to_durable_parent_lineage(monkeypatch):
    monkeypatch.setattr(
        native_delegation.db,
        "get_runtime_episode",
        lambda episode_id: {
            "episodeId": episode_id,
            "rootEpisodeId": "episode-engineering-root",
        },
    )

    root_id = native_delegation._delegation_root_episode_id(
        {},
        parent_episode_id="subagent::direct-parent",
        runtime_owner_episode_id="",
    )

    assert root_id == "episode-engineering-root"


def test_windows_shell_syntax_violation_blocks_posix_mkdir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    payload = _windows_shell_syntax_violation_payload(
        r"mkdir -p E:\Projects\test3\ai-chinese-chess\src\{components,engine}"
    )

    assert payload is not None
    assert payload["kind"] == "cross_shell_syntax_violation"
    assert {"mkdir_-p", "brace_expansion"}.issubset(set(payload["violations"]))


def test_windows_completed_process_decode_recovers_cp936(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    raw = "驱动器 E 中的卷没有标签".encode("cp936")

    text, diagnostics = _decode_completed_process_bytes(raw, stream_name="stdout")

    assert "驱动器 E" in text
    assert diagnostics["encoding"].lower() in {"cp936", "gbk", "mbcs"}
    assert diagnostics["state"] in {"mojibake_recovered", "clean"}


def test_automation_ops_tools_are_hidden_until_runtime_grant():
    assert RUNTIME_TOOL_GROUPS["automation.ops"]["toolNames"] == [
        "list_processes",
        "read_audit_log",
        "manage_cron",
        "manage_hook",
    ]

    tools = [
        _tool("runtime_broker"),
        _tool("list_processes"),
        _tool("read_audit_log"),
        _tool("manage_cron"),
        _tool("manage_hook"),
        _tool("run_system_command"),
    ]

    default_visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    default_names = {tool.name for tool in default_visible}
    assert {"list_processes", "read_audit_log", "manage_cron", "manage_hook"}.isdisjoint(default_names)
    assert "run_system_command" in default_names

    visible_after_grant = filter_visible_tools_for_actor(
        tools,
        actor="supervisor",
        route_context={"runtimeToolGrants": [{"group": "automation.ops", "runtimeKind": "automation"}]},
    )
    granted_names = {tool.name for tool in visible_after_grant}
    assert {"list_processes", "read_audit_log", "manage_cron", "manage_hook"}.issubset(granted_names)


def test_subagent_default_surface_hides_supervisor_only_and_runtime_tools():
    tools = [
        _tool("runtime_broker"),
        _tool("delegation_broker"),
        _tool("ask_user"),
        _tool("write_todos"),
        _tool("update_todo"),
        _tool("s3_broker"),
        _tool("http_request"),
        _tool("delegate_network_task"),
        _tool("read_native_file"),
        _tool("run_system_command"),
        _tool("web_broker"),
        _tool("web_search"),
        _tool("web_read"),
        _tool("research_broker"),
        _tool("memory_recall"),
    ]

    visible = filter_visible_tools_for_actor(tools, actor="subagent", runtime_access=[])
    names = {tool.name for tool in visible}

    assert {"read_native_file", "web_broker"}.issubset(names)
    assert "run_system_command" in names
    assert "ask_user" not in names
    assert "runtime_broker" not in names
    assert "delegation_broker" in names
    assert "s3_broker" not in names
    assert "http_request" in names
    assert "delegate_network_task" not in names
    assert "web_search" not in names
    assert "web_read" not in names
    assert "research_broker" not in names
    assert "memory_recall" not in names


def test_subagent_task_brief_runtime_access_grants_only_requested_memory_group():
    tools = [_tool("memory_broker"), _tool("memory_recall"), _tool("memory_read_day"), _tool("memory_map_expand"), _tool("mem_update")]
    brief = normalize_task_brief({"runtimeAccess": ["memory.read"]})

    visible = filter_visible_tools_for_actor(tools, actor="subagent", runtime_access=brief["runtimeAccess"])
    names = {tool.name for tool in visible}

    assert names == {"memory_broker", "memory_recall", "memory_read_day", "memory_map_expand"}


def test_subagent_runtime_binding_auto_grants_research_core():
    tools = [_tool("read_native_file"), _tool("research_broker"), _tool("creative_media_create_job"), _tool("delegation_broker")]
    agent = {
        "id": "web-research-architect",
        "capabilitySnapshot": {
            "runtimeBindings": [{"runtimeKind": "research", "source": "system_default"}],
        },
    }

    runtime_access = resolve_subagent_runtime_access(agent, [])
    visible = filter_visible_tools_for_actor(tools, actor="subagent", runtime_access=runtime_access)
    names = {tool.name for tool in visible}

    assert runtime_access == ["research.core"]
    assert "read_native_file" in names
    assert "research_broker" in names
    assert "creative_media_create_job" not in names
    assert "delegation_broker" in names


def test_subagent_runtime_binding_merges_explicit_task_grant_without_duplicates():
    agent = {
        "id": "creative-media-director",
        "capabilitySnapshot": {
            "runtimeBindings": ["creative-media"],
        },
    }

    runtime_access = resolve_subagent_runtime_access(agent, ["research.core", "creative_media.core"])

    assert runtime_access == ["research.core", "creative_media.core"]
    assert normalize_subagent_runtime_bindings(["creative-media"])[0]["runtimeKind"] == "creative_media"


def test_unbound_custom_subagent_does_not_auto_receive_runtime_tools():
    tools = [_tool("read_native_file"), _tool("research_broker"), _tool("creative_media_create_job"), _tool("delegation_broker")]
    custom_agent = {
        "id": "custom-researcher",
        "createdBy": "human",
        "capabilitySnapshot": {
            "specialistFamily": "research",
            "runtimeAffinities": ["research"],
        },
    }

    runtime_access = resolve_subagent_runtime_access(custom_agent, [])
    visible = filter_visible_tools_for_actor(tools, actor="subagent", runtime_access=runtime_access)
    names = {tool.name for tool in visible}

    assert runtime_access == []
    assert names == {"read_native_file", "delegation_broker"}


def test_local_subagent_dispatch_defaults_to_one_recursive_layer_unless_forbidden():
    from core.tools.native.delegation import _with_recursive_delegation_access

    plain = _with_recursive_delegation_access({"taskBriefId": "task-1", "goal": "Review the patch"})
    forbidden = _with_recursive_delegation_access(
        {
            "taskBriefId": "task-forbidden",
            "goal": "Review without another worker",
            "allowChildDelegation": False,
            "childDelegationPolicyExplicit": True,
        }
    )
    recursive = _with_recursive_delegation_access(
        {
            "taskBriefId": "task-2",
            "goal": "Split this task further",
            "delegationPolicy": {"allowChildDelegation": True},
        }
    )

    assert plain["runtimeAccess"] == ["delegation.recursive"]
    assert forbidden["runtimeAccess"] == []
    assert recursive["runtimeAccess"] == ["delegation.recursive"]


def test_subagent_prompt_explains_bounded_delegation_authority_without_false_missing_tool_failure():
    blocked = _format_delegated_task_contract(
        {"taskBriefId": "task-1", "goal": "Review one file"},
    )
    allowed = _format_delegated_task_contract(
        {
            "taskBriefId": "task-2",
            "goal": "Coordinate a bounded review",
            "delegationPolicy": {"allowChildDelegation": True},
        },
    )

    assert "`delegation_broker(mode='dispatch')` may create one concurrent layer" in blocked
    assert "disposable mirror workers" in blocked
    assert "`delegation_broker(mode='dispatch')` may create one concurrent layer" in allowed
    assert "Do not select another registered subagent as your grandchild" in allowed
    grandchild = _format_delegated_task_contract(
        {"taskBriefId": "task-3", "goal": "Review one result", "delegationDepth": 2},
    )
    assert "cannot create another delegation layer" in grandchild
    assert "terminal depth-two shard" in grandchild
    assert "Select from the visible tools by relevance" in grandchild


def test_grandchild_identity_contract_is_structural_and_tool_choice_is_task_driven():
    identity = _format_collaboration_identity_contract(
        actor_name="Implementation Engineer · worker-01",
        task_brief={
            "taskBriefId": "task-grandchild",
            "delegationDepth": 2,
            "context": {
                "ephemeralMirror": {
                    "name": "Implementation Engineer · worker-01",
                    "parentAgentName": "Implementation Engineer",
                }
            },
        },
        delegation_depth=2,
    )

    assert "grandchild / terminal delegated worker" in identity
    assert "Runtime identity: Implementation Engineer · worker-01" in identity
    assert "Immediate parent: Implementation Engineer" in identity
    assert "not a persistent registered Agent" in identity
    assert "task prose describe desired capability only" in identity
    assert "visible tools are a candidate toolbox, not a checklist" in identity

    task_contract = _format_delegated_task_contract(
        {
            "taskBriefId": "task-grandchild",
            "goal": "Verify one command result.",
            "delegationDepth": 2,
            "readOnly": True,
            "toolPolicy": {"mode": "default"},
        }
    )
    assert "use the command/file ToolMessage already returned in memory" in task_contract
    assert "Do not redirect output or create temporary evidence" in task_contract


def test_delegated_extension_route_matches_final_allowlisted_tools():
    route_bundle = SimpleNamespace(
        prompt_addition="[Extensions Runtime]\n- Current Skill: irrelevant-skill\n- MCP: unrelated_mcp",
        filtered_tools=[_tool("read_native_file"), _tool("fetch_skill_instructions"), _tool("unrelated_mcp")],
        selected_skill_names=["irrelevant-skill"],
        selected_skill_ids=["skill:irrelevant"],
        exposed_mcp_tool_names=["unrelated_mcp"],
        candidate_summary={
            "selectedSkills": ["irrelevant-skill"],
            "selectedSkillIds": ["skill:irrelevant"],
            "selectedMcpTools": ["unrelated_mcp"],
            "skillEntries": [{"skillName": "irrelevant-skill"}],
        },
    )

    aligned = _align_extension_route_to_task_tools(
        route_bundle,
        [_tool("read_native_file"), _tool("run_system_command")],
    )

    assert [tool.name for tool in aligned.filtered_tools] == ["read_native_file", "run_system_command"]
    assert aligned.selected_skill_names == []
    assert aligned.selected_skill_ids == []
    assert aligned.exposed_mcp_tool_names == []
    assert aligned.candidate_summary["skillEntries"] == []
    assert "Extension candidates are optional references" in aligned.prompt_addition
    assert "No Skill entry or fetch_skill_instructions tool is exposed" in aligned.prompt_addition
    assert "No MCP tool is exposed" in aligned.prompt_addition
    assert "irrelevant-skill" not in aligned.prompt_addition
    assert "unrelated_mcp" not in aligned.prompt_addition


def test_direct_worker_keeps_optional_extension_candidates_but_grandchild_remains_atomic():
    route_bundle = SimpleNamespace(
        filtered_tools=[
            _tool("read_native_file"),
            _tool("run_system_command"),
            _tool("fetch_skill_instructions"),
            _tool("query-docs"),
        ],
        exposed_mcp_tool_names=["query-docs"],
    )
    task = {
        "toolPolicy": {
            "mode": "allowlist",
            "allowedTools": ["read_native_file", "run_system_command"],
        }
    }
    bounded_native = [_tool("read_native_file"), _tool("run_system_command")]

    direct = _preserve_direct_worker_extension_candidates(
        route_bundle,
        bounded_native,
        task,
        delegation_depth=1,
    )
    grandchild = _preserve_direct_worker_extension_candidates(
        route_bundle,
        bounded_native,
        task,
        delegation_depth=2,
    )

    assert {tool.name for tool in direct} == {
        "read_native_file",
        "run_system_command",
        "fetch_skill_instructions",
        "query-docs",
    }
    assert {tool.name for tool in grandchild} == {
        "read_native_file",
        "run_system_command",
    }


def test_atomic_worker_extension_route_skips_skill_and_mcp_candidates_entirely():
    route = _build_atomic_worker_extension_route(
        [_tool("read_native_file"), _tool("run_system_command")]
    )

    assert [tool.name for tool in route.filtered_tools] == [
        "read_native_file",
        "run_system_command",
    ]
    assert route.prompt_addition == ""
    assert route.selected_skill_names == []
    assert route.selected_skill_ids == []
    assert route.exposed_mcp_tool_names == []
    assert route.candidate_summary["routingMode"] == "atomic_task_direct"
    assert route.candidate_summary["skillsRoutingMode"] == "disabled_for_atomic_worker"
    assert route.candidate_summary["mcpRoutingMode"] == "disabled_for_atomic_worker"


def test_memory_broker_is_default_supervisor_read_only_entry_but_not_default_subagent_tool():
    tools = [_tool("memory_broker"), _tool("memory_recall"), _tool("mem_update")]

    supervisor_visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    assert {tool.name for tool in supervisor_visible} == {"memory_broker"}

    subagent_visible = filter_visible_tools_for_actor(tools, actor="subagent", runtime_access=[])
    assert "memory_broker" not in {tool.name for tool in subagent_visible}


def test_session_message_broker_is_supervisor_only_even_with_subagent_runtime_access():
    tools = [_tool("session_message_broker")]

    supervisor_visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    assert {tool.name for tool in supervisor_visible} == {"session_message_broker"}

    direct_child = filter_visible_tools_for_actor(
        tools,
        actor="subagent",
        runtime_access=["conversation_coordination", "delegation.recursive"],
    )
    grandchild = filter_visible_tools_for_actor(
        tools,
        actor="subagent",
        runtime_access=["*"],
    )
    assert direct_child == []
    assert grandchild == []


def test_memory_broker_explain_injection_is_read_only_decision_surface():
    payload = json.loads(memory_broker.func(mode="explain_injection"))

    assert payload["ok"] is True
    assert payload["mode"] == "explain_injection"
    assert "snapshot" in payload["summary"].lower()
    assert "nextAction" in payload


def test_normalize_task_brief_preserves_runtime_access():
    brief = normalize_task_brief({"taskBriefId": "task-1", "runtime_access": ["creative_media.core"]})
    assert brief["runtimeAccess"] == ["creative_media.core"]


def test_normalize_task_brief_decodes_json_array_values_from_typed_tool_payloads():
    brief = normalize_task_brief(
        {
            "taskBriefId": "typed-array-repair",
            "goal": "Repair one file.",
            "writeRequired": True,
            "writeSet": ['["src/result.py"]'],
            "expectedOutputs": '["src/result.py", "test output"]',
            "behaviorScope": ['["read target", "write target"]'],
            "acceptanceContract": "The target passes its test.",
        }
    )

    assert brief["writeSet"] == ["src/result.py"]
    assert brief["expectedOutputs"] == ["src/result.py", "test output"]
    assert brief["behaviorScope"] == ["read target", "write target"]
    assert brief["engineeringTaskCapsule"]["writeSet"] == ["src/result.py"]


def test_normalize_task_brief_preserves_child_delegation_policy():
    brief = normalize_task_brief(
        {
            "taskBriefId": "task-1",
            "allow_child_delegation": "true",
            "child_delegation_budget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
            "write_set_partitions": [{"path": "src/**", "owner": "worker-a"}],
        }
    )

    assert brief["allowChildDelegation"] is True
    assert brief["childDelegationBudget"] == {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3}
    assert brief["writeSetPartitions"] == [{"path": "src/**", "owner": "worker-a"}]


def test_normalize_task_brief_adds_tiered_acceptance_contract():
    brief = normalize_task_brief(
        {
            "taskBriefId": "task-tiered",
            "acceptanceContract": {
                "must": ["Return a concrete artifact."],
                "should": ["Include residual risks."],
                "nice": ["Include benchmark numbers."],
            },
        }
    )

    assert brief["acceptanceTiers"]["must"] == ["Return a concrete artifact."]
    assert brief["acceptanceTiers"]["should"] == ["Include residual risks."]
    assert brief["acceptanceTiers"]["nice"] == ["Include benchmark numbers."]


def test_research_runtime_group_is_brokered_and_not_raw_web_tools():
    assert RUNTIME_TOOL_GROUPS["research.core"]["toolNames"] == ["research_broker"]
    tools = [
        _tool("runtime_broker"),
        _tool("web_broker"),
        _tool("web_search"),
        _tool("web_read"),
        _tool("research_broker"),
    ]

    supervisor_default = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    supervisor_default_names = {tool.name for tool in supervisor_default}
    assert {"runtime_broker", "web_broker"}.issubset(supervisor_default_names)
    assert "research_broker" not in supervisor_default_names
    assert {"web_search", "web_read"}.isdisjoint(supervisor_default_names)

    supervisor_granted = filter_visible_tools_for_actor(
        tools,
        actor="supervisor",
        route_context={"runtimeToolGrants": [{"group": "research.core", "runtimeKind": "research"}]},
    )
    assert "research_broker" in {tool.name for tool in supervisor_granted}

    subagent_granted = filter_visible_tools_for_actor(
        tools,
        actor="subagent",
        runtime_access=["research.core"],
    )
    subagent_names = {tool.name for tool in subagent_granted}
    assert {"web_broker", "research_broker"}.issubset(subagent_names)
    assert {"web_search", "web_read"}.isdisjoint(subagent_names)


def test_research_runtime_appears_in_capability_registry_summary():
    summary = capability_registry.build_supervisor_summary(user_query="联网调研最新官方文档")

    assert "kind=research" in summary
    assert "research.core" in summary


def test_internal_orchestration_runtime_cards_explain_flow_boundary_and_handoff(monkeypatch):
    _set_pack_runtime_installed(monkeypatch, True)
    summary = capability_registry.build_supervisor_summary(
        user_query="先调研官方文档，再用 Seedance 做视频，最后观察真实桌面窗口。"
    )

    assert "kind=research" in summary
    assert "ResearchAnswerPack" in summary
    assert "snippet、footer、captcha、过程日志不能当最终答案" in summary
    assert "refresh_required/degraded evidence" in summary

    assert "kind=creative_media" in summary
    assert "brief、modality、assetRole、referenceAssetIds" in summary
    assert "可编辑代码视频由 Engineering 主导" in summary
    assert "artifactRefs/jobIds/modelUsed/costEstimate/safetyStatus" in summary
    assert "provider raw response、轮询日志和内部 recipe JSON 只进 Runtime Surface" in summary

    assert "kind=computer_use" in summary
    assert "goal、app/window 线索、allowedActions" in summary
    assert "observe -> plan -> act -> verify" in summary
    assert "observedState/actionsTaken/verification/screenshotOrTraceRef" in summary

    assert "Research 不写文件、不执行系统副作用" in summary


def test_memory_runtime_card_mentions_memory_agent_maintenance():
    summary = capability_registry.build_supervisor_summary(user_query="记忆是怎么写入和维护的")

    assert "kind=memory" in summary
    assert "Memory Agent" in summary
    assert "on_chat_end" in summary
    assert "memory.maintain" in summary
    assert "不要直接伪写 persistent memory" in summary


def test_capability_registry_summary_renders_multiple_runtime_prompt_hints():
    registry = CapabilityRegistry()
    registry.register(
        {
            "kind": "creative_media",
            "displayName": "CreativeMediaRuntime",
            "summary": "media",
            "promptHints": ["第一条边界规则", "第二条边界规则"],
        }
    )

    summary = registry.build_supervisor_summary(user_query="做视频")

    assert "何时使用:" in summary
    assert "    - 第一条边界规则" in summary
    assert "    - 第二条边界规则" in summary


def test_capability_registry_summary_hides_disabled_runtime_prompt_hints():
    registry = CapabilityRegistry()
    registry.register(
        {
            "kind": "creative_media",
            "displayName": "CreativeMediaRuntime",
            "summary": "media",
            "promptHints": ["停用后不应注入"],
        }
    )

    def _policy(kind: str):
        if kind == "creative_media":
            return RuntimePolicy(enabled=False)
        return RuntimePolicy()

    with patch.object(registry, "get_policy", side_effect=_policy):
        summary = registry.build_supervisor_summary(user_query="做视频")

    assert "停用后不应注入" not in summary


def test_engineering_config_disabled_hides_runtime_boundary_hints():
    with patch("core.storage.storage.get_engineering_lane_config", return_value={"enabled": False}):
        summary = capability_registry.build_supervisor_summary(user_query="用 Remotion 做科普视频")

    assert "kind=engineering" not in summary
    assert "Remotion" not in summary


def test_creative_media_tools_can_call_runtime_facade(monkeypatch):
    fake_runtime = SimpleNamespace(catalog=lambda: {"version": 1, "modalities": {"image": []}})

    async def _create_job(request):
        return {"jobId": "cm_fake", "status": "succeeded", "request": request}

    def _compile_recipe(request):
        return {"recipeId": "cm_recipe_fake", "modality": request.get("modality")}

    fake_runtime.create_job = _create_job
    fake_runtime.compile_recipe = _compile_recipe
    fake_runtime.create_edit_plan = lambda request: {"planId": "cm_edit_fake", "request": request}
    fake_runtime.create_quality_job = lambda request: {"qualityJobId": "cm_quality_fake", "request": request}
    module = types.ModuleType("runtimes.creative_media.runtime")
    module.creative_media_runtime = fake_runtime
    monkeypatch.setitem(sys.modules, "runtimes.creative_media.runtime", module)

    catalog_payload = json.loads(creative_media_capabilities.invoke({"action": "catalog", "request": {}}))
    assert catalog_payload["ok"] is True

    job_payload = json.loads(
        asyncio.run(
            creative_media_jobs.ainvoke(
                {
                    "action": "create",
                    "request": {"modality": "image", "operationKind": "image.generate", "prompt": "hero"},
                }
            )
        )
    )
    assert "cm_fake" in job_payload["refs"]

    recipe_payload = json.loads(
        creative_media_plan.invoke(
            {"action": "compile_recipe", "request": {"modality": "music", "prompt": "soft bgm"}}
        )
    )
    assert "cm_recipe_fake" in recipe_payload["refs"]

    plan_payload = json.loads(
        creative_media_edit.invoke({"action": "create_plan", "request": {"assetIds": ["asset-video"]}})
    )
    assert "cm_edit_fake" in plan_payload["refs"]

    quality_payload = json.loads(
        creative_media_quality.invoke({"action": "create_job", "request": {"jobId": "cm_fake"}})
    )
    assert "cm_quality_fake" in quality_payload["refs"]


def test_creative_media_runtime_group_exposes_only_six_facades():
    tools = [
        _tool("creative_media_capabilities"),
        _tool("creative_media_plan"),
        _tool("creative_media_assets"),
        _tool("creative_media_jobs"),
        _tool("creative_media_edit"),
        _tool("creative_media_quality"),
        _tool("creative_media_create_job"),
    ]
    visible = filter_visible_tools_for_actor(
        tools,
        actor="subagent",
        route_context={"taskBrief": {"runtimeAccess": ["creative_media.core"]}},
    )
    names = {tool.name for tool in visible}

    assert names == {
        "creative_media_capabilities",
        "creative_media_plan",
        "creative_media_assets",
        "creative_media_jobs",
        "creative_media_edit",
        "creative_media_quality",
    }


def test_contextual_auto_subagent_base_tools_include_granted_runtime_tools():
    tools = [
        _tool("run_system_command"),
        _tool("read_native_file"),
        _tool("creative_media_capabilities"),
        _tool("creative_media_jobs"),
        _tool("memory_recall"),
        _tool("http_request"),
    ]

    selected = filter_visible_tools_for_actor(
        _select_contextual_subagent_native_tools(tools, ["creative_media.core"]),
        actor="subagent",
        runtime_access=["creative_media.core"],
    )
    names = {tool.name for tool in selected}

    assert "read_native_file" in names
    assert "run_system_command" in names
    assert {"creative_media_capabilities", "creative_media_jobs"}.issubset(names)
    assert "memory_recall" not in names
    assert "http_request" in names
