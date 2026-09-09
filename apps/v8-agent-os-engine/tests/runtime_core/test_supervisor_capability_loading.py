"""Capabilities are discoverable; execution choices never replace authorization."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.runtime_tool_access import (
    RUNTIME_TOOL_GROUPS, filter_visible_tools_for_actor, grant_runtime_tool_groups,
    revoke_runtime_tool_groups, runtime_access_from_route_context, runtime_tool_guidance,
    preserve_loaded_capability_tools,
)
from core.tools.native.runtime import runtime_broker
from core.tools.native.desktop_governance import _desktop_route_gate
from core.tools.native.computer_use import _computer_use_compact_observation
from core.tool_surface import _render_computer_use_surface
from graph.route_context import merge_route_context
from graph.tool_routing import _supervisor_direct_scope_hard_block_message


@pytest.fixture(autouse=True)
def installed_pack(monkeypatch):
    monkeypatch.setattr("core.runtime.startup_profile.runtime_family_installed", lambda *a, **k: True)


def _granted():
    context, _, rejected = grant_runtime_tool_groups(
        {"runId": "owned-run", "actorRole": "supervisor"}, ["computer_use.direct"], reason="user desktop task",
    )
    assert not rejected
    return context


def _visible(context, actor="supervisor"):
    names = {name for group in RUNTIME_TOOL_GROUPS.values() for name in group["toolNames"]}
    names.update({"computer_use_desktop_capabilities", "computer_use_list_apps"})
    return {tool.name for tool in filter_visible_tools_for_actor(
        [SimpleNamespace(name=name) for name in names], actor=actor, route_context=context,
    )}


def test_discover_then_load_and_revoke_actual_actions():
    baseline = _visible({})
    assert {"computer_use_list_apps", "computer_use_desktop_capabilities", "creative_media_capabilities"} <= baseline
    assert not {"computer_use_click_target", "computer_use_execute_task", "creative_media_jobs"} & baseline
    context = _granted()
    assert "computer_use_click_target" in _visible(context)
    assert "computer_use_execute_task" not in _visible(context)
    revoked, _ = revoke_runtime_tool_groups(context, ["computer_use.direct"])
    assert "computer_use_click_target" not in _visible(revoked)
    assert "computer_use_list_apps" in _visible(revoked)


def test_desktop_projection_keeps_late_controls_and_distinguishes_same_names():
    raw = {"observation": {"windowTitle": "Owned", "elements": [
        *[{"elementId": str(i), "role": "Text", "name": "Description"} for i in range(6)],
        {"elementId": "input", "role": "Edit", "name": "Description", "automationId": "OwnedInput"},
        {"elementId": "save", "role": "Button", "name": "Submit", "automationId": "OwnedSubmit"},
    ]}}
    compact = json.loads(_computer_use_compact_observation(raw_result=raw))
    visible = _render_computer_use_surface("computer_use_observe_scene", compact, "toolobs://fixture")
    assert "automation_id=OwnedInput" in visible and "control_type=Edit" in visible
    assert "automation_id=OwnedSubmit" in visible and "Submit" in visible


@pytest.mark.parametrize("name,extra", [("computer_use_click_target", {}), ("computer_use_input_text", {"text": "receipt"})])
def test_native_input_contract_forwards_observed_selector_through_guard_and_execution(monkeypatch, name, extra):
    from core.tools.native import computer_use as native
    calls, guards = [], []
    monkeypatch.setattr(native, "_computer_use_resolve_app", lambda *_: None)
    monkeypatch.setattr(native, "_desktop_route_gate", lambda **_: (True, None, {}))
    monkeypatch.setattr(native, "_computer_use_prebind_window", lambda **_: (None, "Owned", 123, None))
    monkeypatch.setattr(native, "_computer_use_action_guard", lambda **kw: guards.append(kw) or (True, None))
    monkeypatch.setattr(native, "_computer_use_execute_single_step", lambda **kw: calls.append(kw) or {"ok": True})
    tool = getattr(native, name)
    tool.func(window_title="Owned", automation_id="OwnedInput", control_type="Edit", **extra)
    assert guards[0]["target"]["automation_id"] == "OwnedInput"
    step = calls[0]["step"]
    assert step["automation_id"] == "OwnedInput" and step["control_type"] == "Edit"
    assert not step.get("window_typing")


def test_discovery_remains_visible_without_the_desktop_execution_pack(monkeypatch):
    from erc.capability_registry import capability_registry
    monkeypatch.setattr("core.runtime.startup_profile.runtime_family_installed", lambda *a, **k: False)
    pool = [SimpleNamespace(name=name) for name in [
        "computer_use_list_apps", "computer_use_desktop_capabilities", "computer_use_click_target",
    ]]
    base = capability_registry.filter_direct_tools(pool, runtime_availability={"computer_use": False})
    visible = filter_visible_tools_for_actor(base, actor="supervisor", route_context={})
    assert {item.name for item in visible} == {"computer_use_list_apps", "computer_use_desktop_capabilities"}
    _, grants, rejected = grant_runtime_tool_groups({"runId": "run"}, ["computer_use.direct"])
    assert not grants and rejected == ["computer_use.direct"]


def test_extension_filter_cannot_drop_a_loaded_tool_or_restore_a_denied_tool():
    available = [SimpleNamespace(name=name) for name in _visible(_granted())]
    restored = preserve_loaded_capability_tools([], available, ["computer_use.direct", "creative_media.core"])
    names = {tool.name for tool in restored}
    assert {"computer_use_click_target", "creative_media_capabilities"} <= names
    assert "creative_media_jobs" not in names  # Not in the already-authorized input pool.
    assert "computer_use_execute_task" not in names


def test_contextual_child_keeps_readonly_discovery_without_mutation_grants():
    from graph.agent_factories import _select_contextual_subagent_native_tools
    pool = [SimpleNamespace(name=name) for name in [
        "computer_use_list_apps", "creative_media_capabilities", "computer_use_click_target", "creative_media_jobs",
    ]]
    selected = _select_contextual_subagent_native_tools(pool, [])
    assert {item.name for item in selected} == {"computer_use_list_apps", "creative_media_capabilities"}


def test_run_change_expires_actions_but_same_run_resume_preserves_them():
    context = _granted()
    resumed = merge_route_context(context, {"runId": "owned-run", "mode": "resume"})
    assert "computer_use_click_target" in _visible(resumed)
    changed = merge_route_context(context, {"runId": "another-run"})
    assert not runtime_access_from_route_context(changed)
    # Even a stale snapshot copied after merge cannot revive a stamped grant.
    changed["runtimeToolGrants"] = context["runtimeToolGrants"]
    assert "computer_use_click_target" not in _visible(changed)


def test_grant_metadata_survives_unrelated_grant_and_revoke():
    context = _granted()
    original = context["runtimeToolGrants"][0].copy()
    context, _, _ = grant_runtime_tool_groups(context, ["creative_media.core"], reason="image")
    context, _ = revoke_runtime_tool_groups(context, ["creative_media.core"])
    assert context["runtimeToolGrants"] == [original]
    assert runtime_tool_guidance([]) == ""
    assert "Screenshot" not in runtime_tool_guidance(["research.read"])
    assert "vision_media_analyzer" in runtime_tool_guidance(["computer_use.direct"])
    assert "providerLock" not in runtime_tool_guidance(["computer_use.direct"])


@pytest.mark.parametrize("requested,clears", [([], False), (["typo.group"], False), (None, True)])
def test_revoke_empty_unknown_and_omitted_have_distinct_meanings(requested, clears):
    context = _granted()
    command = runtime_broker.func(mode="revoke", tool_groups=requested,
                                 state={"current_route_context": context}, tool_call_id="revoke")
    actual = runtime_access_from_route_context(command.update["current_route_context"])
    assert bool(actual) is not clears
    if requested == ["typo.group"]:
        assert command.update["current_route_context"] == context
        assert "unknown_tool_group" in str(command.update["messages"][0].content)


def test_direct_choice_does_not_force_rpa_but_expired_choice_does_not_authorize():
    context = _granted()
    context["desktopRoute"] = {"executionReadyMode": "reuse_mode"}
    okay, _, route = _desktop_route_gate(state={"current_route_context": context}, tool_name="computer_use_click_target")
    assert okay and route["executionReadyMode"] == "direct_mode"
    context["runId"] = "new-run"
    okay, error, _ = _desktop_route_gate(state={"current_route_context": context}, tool_name="computer_use_click_target")
    assert not okay and "RUNTIME_MISMATCH" in error


@pytest.mark.parametrize("role", ["direct_subagent", "grandchild"])
def test_child_must_receive_its_own_task_access_not_supervisor_grants(role):
    context = _granted()
    context["actorRole"] = role
    assert "computer_use_click_target" not in _visible(context, role)
    context["taskBrief"] = {"runtimeAccess": ["computer_use.direct"]}
    assert "computer_use_click_target" in _visible(context, role)
    okay, _, _ = _desktop_route_gate(state={"current_route_context": context}, tool_name="computer_use_click_target")
    assert okay


@pytest.mark.parametrize("tool", ["creative_media_jobs", "computer_use_click_target", "browser_broker"])
def test_advisory_project_shape_does_not_deny_cross_domain_action(tool):
    request = SimpleNamespace(tool_call={"name": tool, "id": "action", "args": {"action": "click"}}, state={
        "current_route_context": {"taskShapeHint": {"taskKind": "project_coding", "boundaryDecision": {
            "primaryRuntime": "engineering", "forbiddenRoutes": ["computer_use", "creative_media"],
        }}},
    })
    assert _supervisor_direct_scope_hard_block_message(request, tool_node_name="supervisor_tools") is None
    request.state["specMode"] = True
    message = _supervisor_direct_scope_hard_block_message(request, tool_node_name="supervisor_tools")
    assert message is not None and message.additional_kwargs["riskCode"] == "spec_runtime_execution_not_approved"


def test_observation_preserves_visual_reference_and_can_find_a_late_target():
    raw = {"ok": True, "observation": {
        "windowTitle": "Owned fixture", "metadata": {"windowHandle": 123, "capturedAt": "2026-09-09T00:00:00Z"},
        "screenshotArtifact": {"artifactId": "shot", "sourcePath": "C:/fixture/screen.png"},
        "elements": [{"elementId": str(i), "name": f"Row {i}", "role": "button"} for i in range(25)],
    }}
    compact = json.loads(_computer_use_compact_observation(raw_result=raw, element_query="Row 24"))
    assert compact["ok"] and compact["elements"][0]["elementId"] == "24"
    rendered = _render_computer_use_surface("computer_use_observe_scene", compact, "detail")
    assert "C:/fixture/screen.png" in rendered and "123" in rendered and "Row 24" in rendered
    assert "2026-09-09T00:00:00Z" in rendered
    failed = json.loads(_computer_use_compact_observation(raw_result={"ok": False, "error": "unavailable"}))
    assert not failed["ok"] and failed["error"] == "unavailable"


def test_native_click_context_reaches_real_plan_owner_and_preserves_cancel(monkeypatch):
    from core.tools.native.computer_use import _computer_use_runtime_kwargs
    from erc.runtime_context import bind_runtime_context
    from runtimes.computer_use import runtime as runtime_module
    from unittest.mock import Mock

    runtime = runtime_module.ComputerUseRuntime.__new__(runtime_module.ComputerUseRuntime)
    runtime._ensure_runtime_ready = Mock()
    runtime.begin_or_attach_run = Mock(return_value=SimpleNamespace(run_id="run", session_id="session"))
    runtime._preflight = Mock()
    runtime._consume_control_signal = Mock(return_value="cancel_requested")
    runtime._build_controlled_result = lambda **kwargs: {"status": kwargs["signal"]}
    runtime._execute_plan_step = Mock(side_effect=AssertionError("Cancelled plan must not click"))
    monkeypatch.setattr(runtime_module.workflow_ledger_service, "activate_runtime_step", lambda *_, **__: None)
    with bind_runtime_context(session_id="session", run_id="run", user_id="owner", goal="Edit only my test window"):
        args = _computer_use_runtime_kwargs("click_target")
        result = runtime.execute_plan(steps=[{"action": "click"}], **args)
    assert result["status"] == "cancel_requested"
    assert runtime.begin_or_attach_run.call_args.kwargs["goal"] == "Edit only my test window"
    assert runtime.begin_or_attach_run.call_args.kwargs["user_id"] == "owner"
    runtime._execute_plan_step.assert_not_called()


@pytest.mark.parametrize("owner", ["chat", "engineering", "research"])
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_desktop_operation_cannot_finish_or_fail_its_callers_run(owner, status):
    from runtimes.computer_use.runtime import ComputerUseRuntime
    from unittest.mock import Mock

    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    handle = SimpleNamespace(descriptor=SimpleNamespace(runtime_kind=owner, status="running"),
                             transition=Mock(), fail=Mock(), emit=Mock())
    runtime.finish_operation(handle, status=status, reason="one desktop action")
    handle.transition.assert_not_called()
    handle.fail.assert_not_called()
    assert handle.descriptor.status == "running"
    assert handle.emit.call_args.args[0] == f"computer_use.operation.{status}"


def test_standalone_desktop_run_still_finishes_under_its_owner():
    from runtimes.computer_use.runtime import ComputerUseRuntime
    from unittest.mock import Mock

    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    handle = SimpleNamespace(descriptor=SimpleNamespace(runtime_kind="computer_use"), transition=Mock(), fail=Mock())
    runtime.finish_operation(handle, status="completed", reason="standalone goal")
    handle.transition.assert_called_once_with("completed", reason="standalone goal", node="computer_use_runtime")
