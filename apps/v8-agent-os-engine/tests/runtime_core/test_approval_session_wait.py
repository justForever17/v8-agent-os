"""Integration guard for approval delivery followed by same-run session await."""

import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END

from erc.command_router import RuntimeCommandRouter
from erc.command_service import command_service
from erc.models import ApprovalRequest, RuntimeCommand
from erc.runtime_context import bind_runtime_context
from graph.tool_routing import create_routed_tool_node
from graph.workflow_assembly import _route_runtime_tool_commands
from runtimes.chat.runtime import ChatRuntime
from tests.chat_runtime.test_session_command_service import (
    ROOT, USER, ToolState, create, harness, publish_result, send, use_real_delivery,
    session_command_broker,
)


def test_consumed_approval_then_session_await_pauses_and_wakes_original_run(harness, monkeypatch, tmp_path):
    import core.database as database_module
    import erc.event_bus as events
    import erc.kernel as kernel
    import erc.run_service as runs
    import erc.command_router as router_module
    import erc.workflow_ledger as ledger
    import runtimes.chat.runtime as runtime_module
    for module in (database_module, events, kernel, runs, router_module, ledger, runtime_module):
        monkeypatch.setattr(module, "db", harness.db)
    monkeypatch.setattr(command_service, "_remember_safety_allowlist", lambda *args: None)
    monkeypatch.setattr(ledger.workflow_ledger_service, "sync_run_status", lambda *args, **kwargs: None)

    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    run_id = "run-root-001"
    harness.db.update_run_record(run_id, status="waiting_approval")
    command_service.request_approval(ApprovalRequest(approval_id="approval-before-await", session_id=ROOT,
        run_id=run_id, approval_kind="safety_review", request={"question": "Continue the original project",
        "operationFingerprint": "approval-session-await-fixture"}))
    approval_requests = []
    router = RuntimeCommandRouter()
    monkeypatch.setattr(router, "_build_resume_chat_request", lambda approval, response: SimpleNamespace(resume_value=response))
    router.configure(schedule_chat_run=lambda request, **kwargs: approval_requests.append(request) or run_id)
    approved = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve",
        approval_id="approval-before-await", response={"decision": "approved"}))
    assert approved["decisionApplied"] and approved["resume_scheduled"]
    runtime = ChatRuntime()
    resumed = SimpleNamespace(active_run_id=run_id, session_id=ROOT, user_id=USER,
        request=approval_requests[0], transport="system_resume", is_resume_request=True,
        emit_runtime_event=lambda *args, **kwargs: None,
        run_handle=SimpleNamespace(descriptor=SimpleNamespace(status="running"), refresh_chat_snapshot=lambda: None))
    assert runtime._consume_approval_delivery(resumed)
    assert not runtime._consume_approval_delivery(resumed)
    assert harness.db.approval_resume_state_for_run(run_id)["requiresDelivery"]

    tool_node = create_routed_tool_node([session_command_broker], "tools", "supervisor")
    effects = tmp_path / "parent-continuations.txt"
    def parent_decision(state):
        with effects.open("a") as file:
            file.write("approval-resumed\n")
        return {"messages": [AIMessage(content="", tool_calls=[{"id": "await-after-approval",
            "name": "session_command_broker", "args": {"mode": "await",
            "assignmentIds": [assignment["assignmentId"]], "idempotencyKey": "wait-after-approval"}}])]}
    async def tools(state, config):
        return _route_runtime_tool_commands(await tool_node(state, config))
    def must_yield(state):
        raise AssertionError("The awaiting graph must yield before another Supervisor turn")
    async def run_awaiting_graph():
        graph = StateGraph(ToolState).add_node("decision", parent_decision).add_node("tools", tools)
        graph.add_node("supervisor", must_yield).add_edge(START, "decision").add_edge("decision", "tools")
        graph.add_edge("supervisor", END)
        with bind_runtime_context(runtime_kind="chat", agent_id="supervisor", session_id=ROOT, run_id=run_id, user_id=USER):
            return await graph.compile().ainvoke({"messages": [], "current_route_context": {}})
    awaited = asyncio.run(run_awaiting_graph())
    assert awaited["messages"][-1].additional_kwargs["sessionResultsWait"]
    finished = runtime.finalize_success_run(resumed)
    assert finished["status"] == "paused" and finished["reason"] == "session_results_wait"
    assert harness.db.get_run_record(run_id)["status"] == "paused"
    delivery = json.loads(harness.db.get_pending_approval("approval-before-await")["resume_json"])
    assert delivery["state"] == "completed"
    assert harness.db.approval_resume_state_for_run(run_id) == {"recorded": True, "requiresDelivery": False}

    scheduled = use_real_delivery(harness)
    result = publish_result(harness, assignment, sent, version=1, status="completed",
        content="The assigned page is verified.", evidence=["artifact:page.txt"])
    harness.service.dispatch_for_session(ROOT)
    assert len(scheduled) == 1 and scheduled[0][1] == run_id
    request = scheduled[0][0]
    assert not (request.resume_value or {}).get("approvalDelivery")
    woken = SimpleNamespace(active_run_id=run_id, session_id=ROOT, user_id=USER, request=request,
        transport="session_coordination", is_resume_request=False,
        run_handle=SimpleNamespace(descriptor=SimpleNamespace(status="paused")))
    assert runtime._activate_run_for_execution(woken)["updated"]
    assert runtime._consume_approval_delivery(woken)
    inbound = runtime._normalize_session_coordination_message(request, session_id=ROOT)
    messages = []
    runtime._inject_session_coordination_message(messages, inbound)
    harness.service.mark_injected(result["message"]["messageId"], target_run_id=run_id)
    def continue_parent(state):
        assert any("The assigned page is verified." in str(message.content) for message in state["messages"])
        with effects.open("a") as file:
            file.write("session-woken\n")
        return {}
    graph = StateGraph(ToolState).add_node("supervisor", continue_parent).add_edge(START, "supervisor").add_edge("supervisor", END)
    graph.compile().invoke({"messages": messages, "current_route_context": {}})
    assert harness.service.acknowledge_project_results({"messages": messages}, session_id=ROOT, run_id=run_id) == 1
    assert effects.read_text().splitlines() == ["approval-resumed", "session-woken"]
    assert len(harness.db.list_run_records(session_id=ROOT)) == 1
    assert harness.db.get_run_record(run_id)["metadata"]["sessionResultWait"]["state"] == "consumed"
    assert not runtime._activate_run_for_execution(woken)["updated"]
    assert not runtime._consume_approval_delivery(resumed)
