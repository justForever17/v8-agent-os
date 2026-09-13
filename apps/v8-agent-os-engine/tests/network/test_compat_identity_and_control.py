from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from core.database import DatabaseManager


@pytest.fixture
def pending(monkeypatch, tmp_path):
    from runtimes.network_supervisor.service import NetworkSupervisorService
    service = NetworkSupervisorService()
    state = {"pendingExternalTools": {"openai:s:wire": {
        "protocol": "openai", "runId": "r", "compatSessionId": "s", "externalThreadId": "thread",
        "originTokenHash": "owner", "wireToolCallId": "wire", "internalAliasName": "network_read",
        "externalWireName": "Read", "checkpointResumeSupported": True, "status": "waiting_external_tool",
        "expiresAtTs": 9999999999,
    }}}
    import runtimes.network_supervisor.service as module
    database = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", database)
    monkeypatch.setattr(module, "NETWORK_SUPERVISOR_STATE_PATH", tmp_path / "network.json")
    with service._pending_store().transaction() as pending_rows:
        pending_rows.update(state["pendingExternalTools"])
    monkeypatch.setattr(module.run_ledger_service, "record_event", lambda **_: None)
    return service, state


@pytest.mark.parametrize("overrides", [{"origin_token_hash": "foreign"}, {"external_thread_id": "other"}, {"external_thread_id": None}, {"compat_session_id": "other"}])
def test_foreign_context_cannot_claim_pending(pending, overrides):
    service, state = pending
    result = service.claim_external_tool_results(**{
        "protocol": "openai", "wire_tool_call_ids": ["wire"], "origin_token_hash": "owner", "external_thread_id": "thread", **overrides,
    })
    assert not result["matched"]
    assert service.pending_external_tools_snapshot()["openai:s:wire"]["status"] == "waiting_external_tool"


def test_result_batch_is_atomic_preserves_long_tail_and_rejects_duplicate(pending):
    service, state = pending
    kwargs = {"protocol": "openai", "origin_token_hash": "owner", "external_thread_id": "thread"}
    assert not service.claim_external_tool_results(**kwargs, wire_tool_call_ids=["wire", "unknown"])["matched"]
    assert service.pending_external_tools_snapshot()["openai:s:wire"]["status"] == "waiting_external_tool"
    full = "x"*7500 + "IMPORTANT-TAIL"
    claimed = service.claim_external_tool_results(**kwargs, wire_tool_call_ids=["wire"], tool_results=[{"wireToolCallId": "wire", "content": full}])
    assert claimed["resumeValue"]["toolResults"][0]["content"] == full
    assert len(service.pending_external_tools_snapshot()["openai:s:wire"]["toolResultPreview"]) <= 4000
    assert not service.claim_external_tool_results(**kwargs, wire_tool_call_ids=["wire"])["matched"]


def test_reused_provider_tool_id_does_not_select_newest_other_run(pending):
    service, state = pending
    original = state["pendingExternalTools"]["openai:s:wire"]
    state["pendingExternalTools"]["openai:other:wire"] = {**original, "compatSessionId": "other", "runId": "other-run", "createdAt": "later"}
    with service._pending_store().transaction() as pending_rows:
        pending_rows.update(state["pendingExternalTools"])
    result = service.claim_external_tool_results(protocol="openai", wire_tool_call_ids=["wire"], origin_token_hash="owner", external_thread_id="thread")
    assert result["matched"] == []
    assert all(row["status"] == "waiting_external_tool" for row in service.pending_external_tools_snapshot().values())


def test_verified_compat_tool_choice_is_carried_to_invocation():
    from runtimes.network_supervisor.openai_compat import build_engine_chat_request_from_openai
    from runtimes.network_supervisor.anthropic_compat import build_engine_chat_request_from_anthropic
    spec = {"type": "function", "function": {"name": "Read", "parameters": {"type": "object", "properties": {}}}}
    common = {"model": "fixture", "messages": [{"role": "user", "content": "read"}]}
    request = build_engine_chat_request_from_openai({**common, "tools": [spec], "tool_choice": {"type": "function", "function": {"name": "Read"}}})
    assert request.data.compat_ingress_diagnostics["requestedExternalToolChoice"] == "network_read"
    with pytest.raises(ValueError):
        build_engine_chat_request_from_openai({**common, "tools": [spec], "tool_choice": {"type": "function", "function": {"name": "Unknown"}}})
    request = build_engine_chat_request_from_anthropic({**common, "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {}}}], "tool_choice": {"type": "any"}})
    assert request.data.compat_ingress_diagnostics["requestedExternalToolChoice"] == "required"
    from runtimes.network_supervisor.openai_compat import required_tool_choice_failure
    assert required_tool_choice_failure([{"type": "done", "status": "completed"}], "network_read")
    assert not required_tool_choice_failure([{"type": "done", "status": "waiting_approval"}], "network_read")


def test_resume_tools_and_status_require_matching_current_handoff(tmp_path, monkeypatch, pending):
    from runtimes.network_supervisor import compat_run_control as module
    import runtimes.network_supervisor.service as service_module
    from runtimes.network_supervisor.openai_compat import select_external_tools_for_request
    from erc.command_router import runtime_command_router
    service, _ = pending
    monkeypatch.setattr(service_module, "network_supervisor_service", service)
    database = DatabaseManager(tmp_path/"state.db")
    monkeypatch.setattr(module, "db", database)
    specs = select_external_tools_for_request([{"type": "function", "function": {"name": "Read", "parameters": {"type": "object", "properties": {}}}}])
    context = {"protocol": "openai", "originTokenHash": "owner", "externalThreadId": "thread", "externalTools": [tool.model_dump(by_alias=True) for tool in specs]}
    database.create_or_update_session("s", "fixture", user_id=module.compat_owner("owner"))
    database.create_run_record("r", "s", user_id=module.compat_owner("owner"), status="waiting_external_tool", metadata={"compatContext": context, "model": "fixture"})
    assert runtime_command_router._engine_config_from_run(database.get_run_record("r")).external_tools[0].function.internal_alias_name == "network_read"
    def event(topic, payload):
        database.add_runtime_event({"event_id": uuid.uuid4().hex, "session_id": "s", "run_id": "r", "topic": topic, "ts": "2026-09-12T00:00:00Z", "payload": payload})
    event("tool.started", {"tool": {"toolName": "network_read", "toolCallId": "authorized-call", "args": {}}})
    assert module.run_surface("r")["deliveryError"] == "external_tool_handoff_proof_missing"
    event("network.external_tool.waiting", {"toolCallId": "wrong-call"})
    assert module.run_surface("r").get("tool_calls") == []
    event("network.external_tool.waiting", {"toolCallId": "authorized-call"})
    assert len(module.run_surface("r")["tool_calls"]) == 1
    event("approval.requested", {"approval_id": "new-pause"})
    assert module.run_surface("r")["deliveryError"] == "external_tool_handoff_proof_missing"


def test_historical_results_are_not_reclaimed():
    from api.network_supervisor_routes import _openai_tool_result_ids, _anthropic_tool_result_ids
    old = {"role": "tool", "tool_call_id": "done", "content": "old"}
    assert _openai_tool_result_ids({"messages": [old, {"role": "user", "content": "next"}]}) == []
    assert _openai_tool_result_ids({"messages": [old, {"role": "assistant", "content": "ok"}, {"role": "tool", "tool_call_id": "new"}]}) == ["new"]
    assert _anthropic_tool_result_ids({"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old"}]}, {"role": "user", "content": "new"}]}) == []


@pytest.mark.parametrize("status", ["waiting_approval", "waiting_input", "cancelled", "failed"])
def test_external_tool_is_not_released_by_model_start_before_safety(status):
    from api.network_supervisor_routes import _trim_events_after_first_external_tool
    from runtimes.network_supervisor.openai_compat import select_external_tools_for_request, extract_external_tool_calls_from_events
    tools = select_external_tools_for_request([{"type": "function", "function": {"name": "Read", "parameters": {"type": "object", "properties": {}}}}])
    events = [{"type": "tool_start", "tool": {"toolName": tools[0].function.internal_alias_name, "toolCallId": "call-1", "args": {}}}, {"type": "done", "status": status}]
    visible = _trim_events_after_first_external_tool(events, external_tools=tools, protocol="openai")
    assert not extract_external_tool_calls_from_events(visible, external_tools=tools)
    assert visible[-1]["status"] == status
    allowed = _trim_events_after_first_external_tool([events[0], {"type": "done", "status": "waiting_external_tool"}], external_tools=tools, protocol="openai")
    assert len(extract_external_tool_calls_from_events(allowed, external_tools=tools)) == 1


def test_run_controls_bind_to_authenticated_owner_and_pending_interaction(tmp_path, monkeypatch):
    from runtimes.network_supervisor import compat_run_control as module
    from erc.command_router import runtime_command_router
    database = DatabaseManager(tmp_path/"state.db")
    monkeypatch.setattr(module, "db", database)
    database.create_or_update_session("s", "compat fixture", user_id=module.compat_owner("owner"))
    database.create_run_record("r", "s", user_id=module.compat_owner("owner"), status="waiting_input")
    database.add_ask_user_interaction(interaction_id="q", session_id="s", run_id="r", question="Code?", prompt="Code?", request={})
    control = {"runId": "r", "action": "status"}
    with pytest.raises(HTTPException) as foreign:
        module.control_request({"v8os_control": control, "user": module.compat_owner("owner")}, origin_token_hash="other")
    assert foreign.value.status_code == 404
    assert module.control_request({"v8os_control": control}, origin_token_hash="owner")["questions"][0]["interactionId"] == "q"
    with pytest.raises(HTTPException):
        module.control_request({"v8os_control": {**control, "action": "approve"}}, origin_token_hash="owner")
    with pytest.raises(HTTPException) as wrong_question:
        module.control_request({"v8os_control": {**control, "action": "answer", "interactionId": "foreign", "answer": "ok"}}, origin_token_hash="owner")
    assert wrong_question.value.status_code == 404
    monkeypatch.setattr(runtime_command_router, "_schedule_chat_run", None)
    with pytest.raises(HTTPException) as unavailable:
        module.control_request({"v8os_control": {**control, "action": "answer", "interactionId": "q", "answer": "ok"}}, origin_token_hash="owner")
    assert unavailable.value.status_code == 503
    assert database.get_ask_user_interaction("q")["status"] == "pending"


def test_external_safety_review_uses_durable_native_approval_without_scratchpad(monkeypatch):
    from runtimes.network_supervisor import openai_compat as module
    from core.tools.native import tool_governance
    from erc.safety_guardian import SafetyDecision
    from core.model_governance_exceptions import ModelGovernanceInterventionRequired
    decision = SafetyDecision(verdict="review", reason="fixture protected read", risk_code="external_tool_local_system_review", allow_override=True)
    monkeypatch.setattr(module.safety_guardian, "assess_external_tool_call", lambda **_: decision)
    monkeypatch.setattr(module.safety_guardian, "log_decision_event", lambda **_: None)
    monkeypatch.setattr(module.safety_guardian, "is_allowlisted", lambda _: None)
    monkeypatch.setattr(tool_governance, "should_auto_approve_safety_review", lambda _: False)
    monkeypatch.setattr(tool_governance, "_is_safety_operation_previously_approved", lambda *_: False)
    monkeypatch.setattr(module, "interrupt", lambda _: (_ for _ in ()).throw(KeyError("__pregel_scratchpad")))
    specs = module.select_external_tools_for_request([{"type": "function", "function": {"name": "Read", "parameters": {"type": "object", "properties": {}}}}])
    tool = module.build_external_langchain_tools(specs)[0]
    with pytest.raises(ModelGovernanceInterventionRequired) as paused:
        tool.invoke({"type": "tool_call", "id": "fixture-call", "name": tool.name, "args": {}})
    assert paused.value.approval_kind == "safety_review"
    assert paused.value.to_request_payload()["operationFingerprint"].startswith("safety:")


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("status", ["waiting_approval", "waiting_input", "cancelled"])
def test_stream_never_exposes_tool_before_runtime_handoff(monkeypatch, protocol, status):
    asyncio.run(_stream_case(monkeypatch, protocol, status))


async def _stream_case(monkeypatch, protocol, status):
    from api import network_supervisor_routes as routes
    from runtimes.network_supervisor.openai_compat import select_external_tools_for_request
    specs = select_external_tools_for_request([{"type": "function", "function": {"name": "Read", "parameters": {"type": "object", "properties": {}}}}])
    request = SimpleNamespace(resume_run_id=None, session_id="fixture-session", config=SimpleNamespace(external_tools=specs))
    async def events(*_, **__):
        yield {"type": "tool_start", "tool": {"toolName": specs[0].function.internal_alias_name, "toolCallId": "fixture-call", "args": {}}}
        yield {"type": "done", "status": status, "run_id": "fixture-run"}
    monkeypatch.setattr(routes, "_iterate_chat_events_with_timeout", events)
    monkeypatch.setattr(routes, "run_surface", lambda *_, **__: {"runId": "fixture-run", "status": status})
    monkeypatch.setattr(routes.network_supervisor_memory_adapter, "record_openai_compat_delta", lambda **_: {})
    monkeypatch.setattr(routes, "_record_openai_memory_adapter_status", lambda _: None)
    registered = []
    monkeypatch.setattr(routes.network_supervisor_service, "record_pending_external_tool", lambda **kw: registered.append(kw))
    if protocol == "openai":
        response = await routes._stream_openai_chat_completion({}, chat_request=request, response_model_name="v8os", project_id=None,
            workspace_id=None, scope_hint=None, external_thread_id=None, external_user_id=None)
    else:
        response = await routes._stream_anthropic_message({}, chat_request=request, response_model_name="v8os", include_thinking=False)
    body = b"".join([chunk async for chunk in response.body_iterator]).decode()
    frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ") and line[6:] != "[DONE]"]
    assert not registered
    assert not any(c.get("delta", {}).get("tool_calls") for f in frames for c in f.get("choices", []))
    assert not any(f.get("content_block", {}).get("type") == "tool_use" for f in frames)
    assert any((f.get("v8os_run") or f).get("status") == status for f in frames)
    if protocol == "openai":
        assert all("choices" in f and f.get("object") == "chat.completion.chunk" for f in frames)
    else:
        assert all(f.get("type") in {"message_start", "message_delta", "message_stop", "content_block_start", "content_block_delta", "content_block_stop"} for f in frames)


def test_compat_resume_clears_old_tool_choice_and_keeps_restricted_transport(tmp_path, monkeypatch):
    import runtimes.chat.runtime as runtime_module
    from runtimes.network_supervisor import compat_run_control
    from api.models import ChatRequest, EngineConfig, ExternalToolSpec
    from agents.runners.supervisor_runner import SupervisorExecutionBundle
    database = DatabaseManager(tmp_path/"state.db")
    monkeypatch.setattr(runtime_module, "db", database)
    monkeypatch.setattr(compat_run_control, "db", database)
    database.create_or_update_session("s", "fixture", user_id=compat_run_control.compat_owner("owner"))
    database.create_run_record("r", "s", user_id=compat_run_control.compat_owner("owner"), status="running", metadata={"compatContext": {
        "originTokenHash": "owner", "protocol": "openai", "ingressDiagnostics": {"externalToolsPrimary": True}, "externalTools": []}})
    async def resumed(**_):
        return SupervisorExecutionBundle(graph=None, payload=None, graph_config={})
    monkeypatch.setattr(runtime_module.supervisor_runner, "create_resume_bundle", resumed)
    request = ChatRequest(messages=[], config=EngineConfig(model_name="fixture"), resume_run_id="r", resume_value={"kind": "external_tool_result"})
    run = SimpleNamespace(active_run_id="r", session_id="s", request=request, run_handle=None)
    bundle = asyncio.run(runtime_module.ChatRuntime().create_resume_bundle(chat_run=run))
    update = bundle.runner_bundle.payload.update
    assert "transport" not in update  # LastValue already has a pending checkpoint write.
    assert update["current_route_context"]["transport"] == "network_supervisor_openai"
    assert update["current_route_context"]["compatIngressDiagnostics"]["requestedExternalToolChoice"] is None
    assert update["current_route_context"]["compatIngressDiagnostics"]["externalToolsPrimary"] is True


def test_native_external_pause_is_not_projected_as_tool_failure():
    from runtimes.chat.runtime import ChatRuntime, ChatStreamState
    from runtimes.network_supervisor.compat_errors import CompatExternalToolRequest
    events = asyncio.run(ChatRuntime().handle_stream_event(SimpleNamespace(), ChatStreamState(), {
        "event": "on_tool_error", "name": "network_read", "data": {"error": CompatExternalToolRequest({"toolCallId": "call"})}}))
    assert events == []
