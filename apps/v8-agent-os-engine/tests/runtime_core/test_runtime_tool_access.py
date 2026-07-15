from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import core.tools.native.delegation as native_delegation
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
from core.spec_service import spec_service
from erc.runtime_context import bind_runtime_context
from erc.capability_registry import CapabilityRegistry, RuntimePolicy, capability_registry
from graph.agent_factories import _format_delegated_task_contract, _select_contextual_subagent_native_tools


def _set_pack_runtime_installed(monkeypatch, installed: bool) -> None:
    def _runtime_family_installed(kind: str, *, profile: str | None = None) -> bool:
        if kind in {"computer_use", "desktop_live", "rpa"}:
            return installed
        return True

    monkeypatch.setattr("core.runtime.startup_profile.runtime_family_installed", _runtime_family_installed)


def _tool(name: str):
    return SimpleNamespace(name=name)


def test_supervisor_default_surface_hides_runtime_groups_but_keeps_broker_and_common_tools():
    tools = [
        _tool("runtime_broker"),
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

    assert {"runtime_broker", "delegation_broker", "read_native_file", "run_system_command", "http_request"}.issubset(names)
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

    assert {"read_native_file", "run_system_command", "web_broker"}.issubset(names)
    assert "ask_user" in names
    assert "runtime_broker" not in names
    assert "delegation_broker" not in names
    assert "s3_broker" not in names
    assert "http_request" not in names
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
    assert "delegation_broker" not in names


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
    assert names == {"read_native_file"}


def test_local_subagent_dispatch_only_adds_recursive_grant_when_child_delegation_allowed():
    from core.tools.native.delegation import _with_recursive_delegation_access

    plain = _with_recursive_delegation_access({"taskBriefId": "task-1", "goal": "Review the patch"})
    recursive = _with_recursive_delegation_access(
        {
            "taskBriefId": "task-2",
            "goal": "Split this task further",
            "delegationPolicy": {"allowChildDelegation": True},
        }
    )

    assert plain["runtimeAccess"] == []
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

    assert "absence of `delegation_broker` and `request_peer_help` is intentional" in blocked
    assert "Use `request_peer_help`" in allowed
    assert "Supervisor-only `delegation_broker`" in allowed


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

    assert {"run_system_command", "read_native_file"}.issubset(names)
    assert {"creative_media_capabilities", "creative_media_jobs"}.issubset(names)
    assert "memory_recall" not in names
    assert "http_request" not in names
