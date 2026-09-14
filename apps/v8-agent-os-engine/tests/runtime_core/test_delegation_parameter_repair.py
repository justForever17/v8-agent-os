from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ValidationError

from core.tools.native import delegation_surface as surface
from graph.tool_routing import create_routed_tool_node, tool_input_validation_fields
from runtimes.chat.runtime import ChatRuntime


def invalid_call():
    return {"name": "delegation_broker", "id": "fixture-missing-target", "args": {"mode": "dispatch", "tasks": [{
        "taskBriefId": "validate-a", "goal": "verify existing script", "expectedOutputs": ["evidence"], "acceptanceContract": ["verified"],
    }]}}


def test_real_tool_validation_reply_teaches_public_union_and_cannot_dispatch_invalid_task(monkeypatch):
    dispatched = []
    monkeypatch.setattr(surface.delegation_broker, "func", lambda **kwargs: dispatched.append(kwargs) or "accepted dispatch")
    node = create_routed_tool_node([surface.supervisor_delegation_broker], "supervisor_tools", "supervisor")
    result = asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[invalid_call()])]}))
    message = result.update["messages"][0]
    assert dispatched == [] and message.status == "error"
    assert "tasks.0.targetAgentName" in message.content
    assert "ManualLocalDelegationTask" not in message.content
    assert "ManualExternalDelegationTask" not in message.content
    assert "no episode or worker was dispatched" in message.content
    assert "Do not switch a local task to external_worker" in message.content
    assert "expectedOutputs" in message.content and "acceptanceContract" in message.content
    # Correct the actual declared field; strict validation still runs before
    # forwarding the exact, otherwise unchanged authorized task.
    corrected = invalid_call()
    corrected["args"]["tasks"][0]["targetAgentName"] = "Fixture Registered Agent"
    result = asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[corrected])]}))
    assert len(dispatched) == 1
    assert dispatched[0]["tasks"] == corrected["args"]["tasks"]


def test_ui_input_validation_is_not_execution_unknown_and_internal_validation_stays_unknown():
    try:
        surface.supervisor_delegation_broker.invoke({"type": "tool_call", **invalid_call()})
    except ValidationError as error:
        rejected = error
    fields = tool_input_validation_fields(rejected, "delegation_broker")
    assert fields and any("targetAgentName" in field for field in fields)

    class InnerPayload(BaseModel):
        required_value: int

    effects = []
    @tool
    def effect_then_bad_contract(value: str) -> str:
        """Fixture where execution begins before internal validation fails."""
        effects.append(value)
        InnerPayload.model_validate({})
        return "unreachable"
    try:
        effect_then_bad_contract.invoke({"value": "side effect happened"})
    except ValidationError as error:
        internal = error
    assert effects == ["side effect happened"]
    assert tool_input_validation_fields(internal, "effect_then_bad_contract") is None

    async def project(error, name):
        runtime = ChatRuntime()
        captured = []
        async def projected(_chat, _state, event):
            captured.append(json.loads(event["data"]["output"].content))
            return []
        runtime.handle_stream_event = projected
        await ChatRuntime.handle_stream_event(runtime, SimpleNamespace(), SimpleNamespace(tool_call_id_by_callback_run_id={}),
            {"event": "on_tool_error", "name": name, "run_id": "fixture-callback", "data": {"error": error}})
        return captured[0]
    payload = asyncio.run(project(rejected, "delegation_broker"))
    assert payload["executionOutcome"] == "not_executed"
    assert payload["kind"] == "tool_parameter_repair" and "tasks.0.targetAgentName" in payload["invalidFields"]
    payload = asyncio.run(project(internal, "effect_then_bad_contract"))
    assert payload["executionOutcome"] == "unverified"


def test_supervisor_missing_tasks_example_survives_actual_agent_surface_and_satisfies_public_schema():
    from erc.runtime_context import bind_runtime_context
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        command = surface.supervisor_delegation_broker.invoke({"type": "tool_call", "name": "delegation_broker", "id": "missing-tasks", "args": {"mode": "dispatch"}})
    message = command.update["messages"][0]
    payload = json.loads(message.content)
    example = payload["exampleTasks"][0]
    assert "targetAgentName" in example
    surface.supervisor_delegation_broker.args_schema.model_validate({"mode": "dispatch", "tasks": [example]})
    node = create_routed_tool_node([surface.supervisor_delegation_broker], "supervisor_tools", "supervisor")
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        routed = asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[{
            "name": "delegation_broker", "id": "missing-tasks-routed", "args": {"mode": "dispatch"},
        }])]}))
    assert len(routed) == 1
    visible = routed[0].update["messages"][0]
    assert "targetAgentName" in visible.content
    assert "expectedOutputs" in visible.content and "acceptanceContract" in visible.content
    assert "nothing has been dispatched" in visible.content


def test_registry_preparation_cannot_complete_delegation_or_admit_mixed_side_effects():
    from graph.supervisor_turn import _is_required_orchestration_preparation, _response_runtime_route_kinds
    for mode in ("list", "inspect", "validate"):
        read = AIMessage(content="", tool_calls=[{"name": "agent_broker", "id": "registry", "args": {"mode": mode}}])
        assert _is_required_orchestration_preparation(read, "delegation", {})
        assert "delegation" not in _response_runtime_route_kinds(read)
        assert not _is_required_orchestration_preparation(read, "engineering", {})
    for calls in (
        [], [{"name": "agent_broker", "id": "create", "args": {"mode": "create"}}],
        [*read.tool_calls, {"name": "write_native_file", "id": "write", "args": {"content": "unexpected"}}],
    ):
        assert not _is_required_orchestration_preparation(AIMessage(content="", tool_calls=calls), "delegation", {})


@pytest.mark.parametrize("tool_name", ["delegation_broker", "runtime_broker", "agent_broker"])
def test_input_error_projection_preserves_repair_fields_and_execution_uncertainty(tool_name):
    from langchain_core.messages import ToolMessage
    from core.tool_surface import apply_tool_surface_budget

    for rejected in (True, False):
        payload = {
            "ok": False, "kind": "tool_parameter_repair" if rejected else "tool_execution_error",
            "error": "ValidationError", "summary": "Correct the listed fields." if rejected else "Check existing effects.",
            "executionOutcome": "not_executed" if rejected else "unverified",
            **({"invalidFields": ["tasks.0.context.command", "tasks.0.readSet"]} if rejected else {}),
        }
        message = ToolMessage(content=json.dumps(payload), name=tool_name, tool_call_id="rejected-input", status="error")
        projected = apply_tool_surface_budget(message, {"agentVisibleBudget": 2500}, tool_name=tool_name)
        assert projected.tool_call_id == message.tool_call_id and projected.status == "error"
        assert payload["executionOutcome"] in projected.content
        if rejected:
            assert all(field in projected.content for field in payload["invalidFields"])
        else:
            assert "not_executed" not in projected.content
        assert len(projected.content) <= 2500


def test_misplaced_task_boundaries_are_rejected_before_dispatch_and_explicit_repair_preserves_them(monkeypatch):
    from copy import deepcopy

    dispatched = []
    monkeypatch.setattr(surface.delegation_broker, "func", lambda **kwargs: dispatched.append(kwargs) or "dispatched")
    call = invalid_call()
    brief = call["args"]["tasks"][0]
    brief["targetAgentName"] = "Fixture Registered Agent"
    boundaries = {"readOnly": True, "writeRequired": False, "readSet": ["verify.py"], "writeSet": [],
                  "toolPolicy": {"mode": "allowlist", "allowedTools": []}}
    call["args"].update(deepcopy(boundaries))
    original = deepcopy(call)
    node = create_routed_tool_node([surface.supervisor_delegation_broker], "supervisor_tools", "supervisor")
    result = asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[call])]}))
    assert dispatched == [], "misplaced execution boundaries must not silently disappear"
    message = result.update["messages"][0]
    assert message.status == "error" and message.additional_kwargs["executionOutcome"] == "not_executed"
    assert "tasks[i].readOnly" in message.content and "tasks[i].toolPolicy" in message.content
    assert call == original
    # Only the caller's explicit corrected request may move these fields.
    for field in boundaries:
        brief[field] = call["args"].pop(field)
    asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[call])]}))
    assert len(dispatched) == 1 and dispatched[0]["tasks"] == [brief]
    assert dispatched[0]["tasks"][0]["toolPolicy"]["allowedTools"] == []


def test_tool_body_validation_cannot_claim_dispatch_was_not_executed(monkeypatch):
    class InnerPayload(BaseModel):
        required_value: int

    effects = []
    def effect_then_error(**kwargs):
        effects.append(kwargs["tasks"])
        InnerPayload.model_validate({})
    monkeypatch.setattr(surface.delegation_broker, "func", effect_then_error)
    node = create_routed_tool_node([surface.supervisor_delegation_broker], "supervisor_tools", "supervisor")
    call = invalid_call()
    call["args"]["tasks"][0]["targetAgentName"] = "Fixture Registered Agent"
    result = asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[call])]}))
    message = result.update["messages"][0]
    assert len(effects) == 1 and message.status == "error"
    assert "no episode or worker was dispatched" not in message.content
    assert message.additional_kwargs.get("executionOutcome") != "not_executed"
