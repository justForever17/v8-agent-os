from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from core.delegation_broker import normalize_task_brief
from core.native_tools import (
    _decode_completed_process_bytes,
    _windows_shell_syntax_violation_payload,
    creative_media_catalog,
    creative_media_compile_recipe,
    creative_media_create_edit_plan,
    creative_media_create_job,
    creative_media_create_quality_job,
    memory_broker,
    delegation_broker,
    runtime_broker,
)
from core.runtime_tool_access import (
    RUNTIME_TOOL_GROUPS,
    filter_visible_tools_for_actor,
    runtime_access_from_route_context,
)
from erc.runtime_context import bind_runtime_context
from erc.capability_registry import CapabilityRegistry, RuntimePolicy, capability_registry
from graph.agent_factories import _select_contextual_subagent_native_tools


def _tool(name: str):
    return SimpleNamespace(name=name)


def test_supervisor_default_surface_hides_runtime_groups_but_keeps_broker_and_common_tools():
    tools = [
        _tool("runtime_broker"),
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
    ]

    visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    names = {tool.name for tool in visible}

    assert {"runtime_broker", "read_native_file", "run_system_command", "http_request"}.issubset(names)
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


def test_runtime_broker_grant_makes_group_visible_for_same_run_next_step():
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
    assert payload["nextAction"] == "wait_episode"
    assert runtime_access_from_route_context(updated_context) == ["research.core"]
    assert updated_context["capabilityEpisodes"][-1]["kind"] == "research"
    assert updated_context["capabilityEpisodes"][-1]["state"] == "queued"
    assert command.update["planner_dispatch_status"]["nextAction"] == "wait_episode"


def test_runtime_broker_route_fills_delegation_tasks_from_planner_plan():
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
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert payload["episodeKind"] == "delegation"
    assert payload["nextAction"] == "wait_episode"
    assert episode["inputs"]["tasks"][0]["goal"] == "Build the visible application shell."
    assert episode["inputs"]["workerBriefs"][0]["goal"] == "Build the visible application shell."


def test_runtime_broker_route_accepts_json_need_string_and_infers_engineering():
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
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert payload["episodeKind"] == "engineering"
    assert payload["nextAction"] == "wait_episode"
    assert episode["kind"] == "engineering"
    assert episode["inputs"]["workspacePath"] == r"E:\Projects\test7"
    assert episode["inputs"]["workerBriefs"][0]["context"]["blockedTool"] == "write_native_file"


def test_runtime_broker_route_binds_session_run_root_and_workspace_before_enqueue():
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
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert episode["sessionId"] == "session-route-binding"
    assert episode["session_id"] == "session-route-binding"
    assert episode["runId"] == "run-route-binding"
    assert episode["run_id"] == "run-route-binding"
    assert episode["rootRunId"] == "root-route-binding"
    assert episode["inputs"]["workspacePath"] == r"E:\Projects\test7"
    assert episode["inputs"]["workspace_path"] == r"E:\Projects\test7"


def test_runtime_broker_list_with_episode_intent_auto_routes_but_catalog_stays_list():
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
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert payload["mode"] == "route"
    assert payload["episodeKind"] == "engineering"
    assert payload["nextAction"] == "wait_episode"
    assert episode["kind"] == "engineering"
    assert episode["sessionId"] == "session-auto-route"
    assert episode["runId"] == "run-auto-route"
    assert episode["rootRunId"] == "root-auto-route"
    assert episode["inputs"]["workspacePath"] == r"E:\Projects\test7"


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


def test_memory_broker_is_default_supervisor_read_only_entry_but_not_default_subagent_tool():
    tools = [_tool("memory_broker"), _tool("memory_recall"), _tool("mem_update")]

    supervisor_visible = filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    assert {tool.name for tool in supervisor_visible} == {"memory_broker"}

    subagent_visible = filter_visible_tools_for_actor(tools, actor="subagent", runtime_access=[])
    assert "memory_broker" not in {tool.name for tool in subagent_visible}


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

    catalog_payload = creative_media_catalog.invoke({})
    assert "modalities" in catalog_payload

    job_payload = asyncio.run(creative_media_create_job.ainvoke({"request": {"modality": "image"}}))
    assert "cm_fake" in job_payload

    recipe_payload = creative_media_compile_recipe.invoke({"request": {"modality": "music", "prompt": "soft bgm"}})
    assert "cm_recipe_fake" in recipe_payload

    plan_payload = creative_media_create_edit_plan.invoke({"request": {"assetIds": ["asset-video"]}})
    assert "cm_edit_fake" in plan_payload

    quality_payload = creative_media_create_quality_job.invoke({"request": {"jobId": "cm_fake"}})
    assert "cm_quality_fake" in quality_payload


def test_creative_media_runtime_group_includes_p2_p3_recipe_asset_and_render_tools():
    tools = [
        _tool("creative_media_catalog"),
        _tool("creative_media_compile_recipe"),
        _tool("creative_media_get_recipe"),
        _tool("creative_media_list_recipes"),
        _tool("creative_media_register_asset"),
        _tool("creative_media_list_assets"),
        _tool("creative_media_create_character_bible"),
        _tool("creative_media_get_character_bible"),
        _tool("creative_media_list_character_bibles"),
        _tool("creative_media_register_keyframe"),
        _tool("creative_media_get_keyframe"),
        _tool("creative_media_list_keyframes"),
        _tool("creative_media_create_edit_plan"),
        _tool("creative_media_get_edit_plan"),
        _tool("creative_media_list_edit_plans"),
        _tool("creative_media_render_edit_plan"),
        _tool("creative_media_get_render"),
        _tool("creative_media_list_renders"),
        _tool("creative_media_create_quality_job"),
        _tool("creative_media_list_quality_jobs"),
        _tool("creative_media_get_quality_job"),
        _tool("creative_media_retry_job"),
        _tool("creative_media_cost_ledger"),
        _tool("creative_media_safety_events"),
    ]
    visible = filter_visible_tools_for_actor(
        tools,
        actor="subagent",
        route_context={"taskBrief": {"runtimeAccess": ["creative_media.core"]}},
    )
    names = {tool.name for tool in visible}

    assert names == {
        "creative_media_catalog",
        "creative_media_compile_recipe",
        "creative_media_get_recipe",
        "creative_media_list_recipes",
        "creative_media_register_asset",
        "creative_media_list_assets",
        "creative_media_create_character_bible",
        "creative_media_get_character_bible",
        "creative_media_list_character_bibles",
        "creative_media_register_keyframe",
        "creative_media_get_keyframe",
        "creative_media_list_keyframes",
        "creative_media_create_edit_plan",
        "creative_media_get_edit_plan",
        "creative_media_list_edit_plans",
        "creative_media_render_edit_plan",
        "creative_media_get_render",
        "creative_media_list_renders",
        "creative_media_create_quality_job",
        "creative_media_list_quality_jobs",
        "creative_media_get_quality_job",
        "creative_media_retry_job",
        "creative_media_cost_ledger",
        "creative_media_safety_events",
    }


def test_contextual_auto_subagent_base_tools_include_granted_runtime_tools():
    tools = [
        _tool("run_system_command"),
        _tool("read_native_file"),
        _tool("creative_media_catalog"),
        _tool("creative_media_create_job"),
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
    assert {"creative_media_catalog", "creative_media_create_job"}.issubset(names)
    assert "memory_recall" not in names
    assert "http_request" not in names
