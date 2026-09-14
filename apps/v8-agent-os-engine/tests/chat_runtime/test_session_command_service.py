from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from core.database import DatabaseManager
from core.tools.native.session_coordination import session_command_broker, session_message_broker
from erc.runtime_context import bind_runtime_context
from erc.session_command_service import SessionCommandService
from erc.session_coordination_service import SessionCoordinationService
import erc.session_command_service as command_module
import erc.session_coordination_service as coordination_module


ROOT = "root-session-001"
USER = "owner-001"
AUTHORIZATION = "请自己创建三个独立任务会话，完成本项目页面，按需持续指挥，只修改各任务授权文件，不要发布。"


class ToolState(TypedDict):
    messages: Annotated[list, add_messages]
    current_route_context: dict


@pytest.fixture()
def harness(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "state.sqlite3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database.create_or_update_session(ROOT, "Root", user_id=USER)
    database.upsert_session_scope_binding({
        "session_id": ROOT, "conversation_id": ROOT, "user_id": USER,
        "workspace_id": "fixture-workspace", "workspace_path": str(workspace),
        "project_id": "fixture-project", "resolved_scope": "project:fixture-project",
        "scope_source": "test", "status": "active",
    })
    database.create_run_record("run-root-001", ROOT, user_id=USER, run_type="chat", status="running")
    monkeypatch.setattr(coordination_module, "db", database)
    monkeypatch.setattr(command_module, "db", database)
    service = SessionCoordinationService()
    from core.tools.native import session_coordination as tool_module
    monkeypatch.setattr(tool_module, "session_coordination_service", service)
    monkeypatch.setattr(service, "_emit_transition", lambda *args, **kwargs: None)
    scheduled = []
    monkeypatch.setattr(coordination_module.session_admission_service, "get_lane_view", lambda _sid: {"activeRunId": None})
    # Capture the actual persisted coordination dispatch after owner/revision
    # checks. No Engine server, real model, or shared workspace is started.
    def wake(row, session):
        run_id = "run-child-" + str(len(scheduled))
        database.create_run_record(run_id, session["id"], user_id=USER, run_type="chat", status="queued")
        scheduled.append(row["id"])
        updated = database.update_session_coordination_message(row["id"], state="promoted", target_run_id=run_id)
        SessionCommandService(database=database).bind_run(updated, run_id=run_id)
        return updated
    monkeypatch.setattr(service, "_wake_idle_target", wake)
    return SimpleNamespace(db=database, workspace=workspace, service=service, scheduled=scheduled, monkeypatch=monkeypatch)


def task(path="page.txt"):
    return {"goal": "Create the assigned page", "readSet": [], "writeSet": [path],
            "expectedOutputs": [path], "acceptanceContract": "The file contains checked fixture output."}


def invoke(args, *, text=AUTHORIZATION, session=ROOT, user=USER, run="run-root-001", extra_context=None, routed=False,
           human_metadata=None, tool_name="session_command_broker", route=None, messages=None):
    """Use real StructuredTool + ToolNode state injection, not service kwargs."""
    async def execute():
        graph = StateGraph(ToolState)
        if routed:
            from graph.tool_routing import create_routed_tool_node
            graph.add_node("tools", create_routed_tool_node([session_command_broker, session_message_broker], "tools", END))
        else:
            graph.add_node("tools", ToolNode([session_command_broker, session_message_broker]))
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        with bind_runtime_context(runtime_kind="chat", agent_id="supervisor", session_id=session,
                                  run_id=run, user_id=user, **(extra_context or {})):
            result = await graph.compile().ainvoke({
                "messages": [*(messages if messages is not None else [HumanMessage(content=text, additional_kwargs=human_metadata or {})]),
                             AIMessage(content="", tool_calls=[{"id": "command-call", "name": tool_name, "args": args}])],
                "current_route_context": route or {},
            })
        return json.loads(result["messages"][-1].content)
    return asyncio.run(execute())


def create(**kwargs):
    return invoke({"mode": "create", "title": "Page task", "taskBrief": task(),
                   "idempotencyKey": "page-task", **kwargs})


def send(assignment, **kwargs):
    return invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": assignment["revision"],
                   "content": "Create page.txt and verify its content.", "idempotencyKey": "start-page"}, **kwargs)


def publish_result(harness, assignment, sent, *, version, status, content, evidence=None):
    row = harness.db.get_session_coordination_message(sent["message"]["messageId"])
    return invoke({"mode": "reply", "messageId": row["id"], "replyStatus": status, "resultVersion": version,
                   "content": content, "evidenceRefs": evidence or []},
                  session=assignment["childSessionId"], run=row["targetRunId"],
                  tool_name="session_message_broker",
                  route={"sessionCoordination": harness.service.compact_ref(row)})


def test_project_accepted_cannot_consume_later_completed_result(harness):
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    # Keep result delivery pending so we can inspect the immutable source ledger.
    harness.monkeypatch.setattr(harness.service, "dispatch_message", lambda mid: harness.db.get_session_coordination_message(mid))
    accepted = publish_result(harness, assignment, sent, version=1, status="accepted", content="Started working.")
    completed = publish_result(harness, assignment, sent, version=2, status="completed", content="The page is complete.", evidence=["artifact:page"])
    assert accepted["ok"] and completed["ok"]
    assert completed["message"]["replyStatus"] == "completed"
    assert completed["message"]["messageId"] != accepted["message"]["messageId"]


def test_project_result_versions_partial_terminal_duplicate_and_restart(harness):
    assignment = create()["assignment"]
    sent = send(assignment)
    request_id = sent["message"]["messageId"]
    harness.service.mark_injected(request_id, target_run_id="run-child-0")
    harness.monkeypatch.setattr(harness.service, "dispatch_message", lambda mid: harness.db.get_session_coordination_message(mid))
    accepted = publish_result(harness, assignment, sent, version=1, status="accepted", content="Accepted work.")
    assert harness.db.get_session_coordination_message(request_id)["state"] == "injected"
    partial = publish_result(harness, assignment, sent, version=2, status="partial", content="Page body ready.", evidence=["artifact:partial"])
    assert partial["message"]["resultFinal"] is False
    assert harness.db.get_session_coordination_message(request_id)["state"] == "injected"
    completed = publish_result(harness, assignment, sent, version=3, status="completed", content="All checks pass.", evidence=["artifact:final"])
    assert completed["message"]["resultFinal"] is True
    assert harness.db.get_session_coordination_message(request_id)["state"] == "replied"
    duplicate = publish_result(harness, assignment, sent, version=3, status="completed", content="All checks pass.", evidence=["artifact:final"])
    assert duplicate["message"]["messageId"] == completed["message"]["messageId"]
    conflict = publish_result(harness, assignment, sent, version=3, status="completed", content="Changed same version.", evidence=["artifact:final"])
    assert conflict["error"] == "project_result_version_conflict"
    reopened = DatabaseManager(harness.db.db_path)
    results = reopened.list_session_project_results(ROOT)
    assert [row["metadata"]["resultVersion"] for row in results] == [1, 2, 3]
    assert [row["metadata"]["resultCursor"] for row in results] == [1, 2, 3]
    assert [row["metadata"]["superseded"] for row in results] == [True, True, False]
    tail = reopened.list_session_project_results(ROOT, after_cursor=accepted["message"]["resultCursor"])
    assert [row["replyStatus"] for row in tail] == ["partial", "completed"]
    first_page = invoke({"mode": "results", "afterCursor": 0, "limit": 2}, routed=True)
    second_page = invoke({"mode": "results", "afterCursor": first_page["nextCursor"]}, routed=True)
    assert [item["resultVersion"] for item in first_page["results"] + second_page["results"]] == [1, 2, 3]
    assert second_page["deliveryAcknowledged"] is False


def pending_final(harness):
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    harness.monkeypatch.setattr(harness.service, "dispatch_message", lambda mid: harness.db.get_session_coordination_message(mid))
    final = publish_result(harness, assignment, sent, version=1, status="completed", content="Delivered file.", evidence=["artifact:file"])
    return harness.db.get_session_coordination_message(final["message"]["messageId"])


def test_project_result_ack_requires_persisted_graph_message_and_is_idempotent(harness, tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from runtimes.chat.runtime import ChatRuntime
    row = pending_final(harness)
    harness.db.create_run_record("consumer-run", ROOT, user_id=USER, run_type="chat", status="running")
    harness.db.update_session_coordination_message(row["id"], state="injected", target_run_id="consumer-run")
    assert harness.service.acknowledge_project_results({"messages": []}, session_id=ROOT, run_id="consumer-run") == 0
    message = {**harness.service.compact_ref(row), "content": row["content"]}
    injected = []
    ChatRuntime()._inject_session_coordination_message(injected, message)
    checkpoint_path = str(tmp_path / "consumer-checkpoint.sqlite")
    config = {"configurable": {"thread_id": ROOT}}
    builder = StateGraph(ToolState)
    builder.add_node("consume", lambda state: {})
    builder.add_edge(START, "consume")
    builder.add_edge("consume", END)
    with SqliteSaver.from_conn_string(checkpoint_path) as saver:
        builder.compile(checkpointer=saver).invoke({"messages": injected, "current_route_context": {}}, config=config)
    with SqliteSaver.from_conn_string(checkpoint_path) as reopened:
        persisted = builder.compile(checkpointer=reopened).get_state(config).values
        assert harness.service.acknowledge_project_results(persisted, session_id=ROOT, run_id="consumer-run") == 1
        assert harness.service.acknowledge_project_results(persisted, session_id=ROOT, run_id="consumer-run") == 0
    consumed = harness.db.get_session_coordination_message(row["id"])
    assert consumed["state"] == "injected"
    assert consumed["metadata"]["checkpointRunId"] == "consumer-run"
    harness.db.update_run_record("consumer-run", status="completed")
    harness.service.on_run_terminal(ROOT, "consumer-run", status="completed")
    assert harness.db.get_session_coordination_message(row["id"])["state"] == "replied"


def test_missing_wake_and_checkpoint_crash_replay_same_result_without_duplicate_claim(harness):
    row = pending_final(harness)
    harness.db.create_run_record("crashed-consumer", ROOT, user_id=USER, run_type="chat", status="interrupted")
    harness.db.update_session_coordination_message(row["id"], state="injected", target_run_id="crashed-consumer")
    harness.service.on_run_terminal(ROOT, "crashed-consumer", status="interrupted")
    assert harness.db.get_session_coordination_message(row["id"])["metadata"]["deliveryReplayRequired"]
    reopened = DatabaseManager(harness.db.db_path)
    harness.monkeypatch.setattr(coordination_module, "db", reopened)
    harness.monkeypatch.setattr(harness.service, "dispatch_message",
                                SessionCoordinationService.dispatch_message.__get__(harness.service))
    recovered = harness.service.recover_pending()
    assert recovered["recovered"] == 1
    replayed = reopened.get_session_coordination_message(row["id"])
    assert replayed["state"] == "promoted"
    assert replayed["targetRunId"] != "crashed-consumer"
    assert replayed["metadata"]["resultCursor"] == row["metadata"]["resultCursor"]
    assert len(reopened.list_session_project_results(ROOT)) == 1
    scheduled_count = len(harness.scheduled)
    harness.service.recover_pending()
    assert len(harness.scheduled) == scheduled_count


def test_result_delivery_does_not_report_processed_without_checkpoint(harness):
    row = pending_final(harness)
    harness.db.create_run_record("empty-consumer", ROOT, user_id=USER, run_type="chat", status="completed")
    harness.db.update_session_coordination_message(row["id"], state="injected", target_run_id="empty-consumer")
    harness.service.on_run_terminal(ROOT, "empty-consumer", status="completed")
    current = harness.db.get_session_coordination_message(row["id"])
    assert current["state"] == "failed"
    assert current["errorCode"] == "project_result_delivery_incomplete"
    assert not current["metadata"].get("replyDelivered")


def test_out_of_order_older_result_does_not_reopen_terminal_request(harness):
    assignment = create()["assignment"]
    sent = send(assignment)
    request_id = sent["message"]["messageId"]
    harness.service.mark_injected(request_id, target_run_id="run-child-0")
    harness.monkeypatch.setattr(harness.service, "dispatch_message", lambda mid: harness.db.get_session_coordination_message(mid))
    final = publish_result(harness, assignment, sent, version=3, status="completed", content="Final.", evidence=["artifact:final"])
    late = publish_result(harness, assignment, sent, version=2, status="partial", content="Delayed partial.", evidence=["artifact:partial"])
    assert late["ok"] and late["message"]["superseded"] is True
    parent = harness.db.get_session_coordination_message(request_id)
    assert parent["state"] == "replied"
    assert parent["metadata"]["latestResultId"] == final["message"]["messageId"]
    assert parent["metadata"]["latestResultVersion"] == 3
    assert publish_result(harness, assignment, sent, version=4, status="accepted", content="Reopen.")["error"] == "project_result_already_terminal"


def test_result_without_proof_or_after_revocation_is_not_published(harness):
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    assert publish_result(harness, assignment, sent, version=1, status="completed", content="No proof.")["error"] == "project_result_proof_required"
    assert harness.db.list_session_project_results(ROOT) == []
    invoke({"mode": "revoke", "assignmentId": assignment["assignmentId"], "revision": 1})
    assert publish_result(harness, assignment, sent, version=1, status="completed", content="Revoked.", evidence=["artifact:fake"])["ok"] is False
    assert harness.db.list_session_project_results(ROOT) == []


@pytest.mark.parametrize("waiting_status", ["waiting_input", "waiting_approval", "paused"])
@pytest.mark.parametrize("lane_released", [False, True])
def test_assignment_followup_preserves_human_wait_and_does_not_start_another_run(harness, waiting_status, lane_released):
    assignment = create()["assignment"]
    sent = send(assignment)
    child = assignment["childSessionId"]
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    harness.db.update_run_record("run-child-0", status=waiting_status)
    harness.db.add_pending_approval("approval-waiting", child, "run-child-0", "fixture", "pending", {"fixture": True})
    harness.monkeypatch.setattr(coordination_module.session_admission_service, "get_lane_view",
                                lambda _sid: {"activeRunId": None if lane_released else "run-child-0"})
    result = invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": 1,
                     "content": "After the human decision, use this correction.", "idempotencyKey": "steering-while-waiting"})
    assert result["ok"] and result["message"]["state"] == "queued"
    assert harness.db.get_run_record("run-child-0")["status"] == waiting_status
    assert harness.db.get_pending_approval("approval-waiting")["status"] == "pending"
    assert len(harness.db.list_run_records(session_id=child)) == 1


def test_model_schema_has_no_actor_authority_or_target_workspace_fields():
    fields = session_command_broker.tool_call_schema.model_json_schema()["properties"]
    assert not {"state", "actor", "authority", "userId", "rootSessionId", "authorizationRef", "workspacePath"} & set(fields)
    assert {"mode", "taskBrief", "assignmentId", "revision"} <= set(fields)


def test_production_routed_tool_surface_keeps_assignment_handles(harness):
    created = invoke({"mode": "create", "title": "Page", "taskBrief": task(),
                      "idempotencyKey": "routed-page"}, routed=True)
    assert created["ok"] is True
    assert created["assignment"]["revision"] == 1
    assert harness.db.get_session(created["assignment"]["childSessionId"])
    listed = invoke({"mode": "list"}, routed=True)
    assert listed["assignments"][0]["assignmentId"] == created["assignment"]["assignmentId"]


def test_assignment_capsule_cannot_be_forged_or_widened_and_empty_write_set_stays_closed(harness):
    from core.tools.native.workspace_file import _task_write_scope_allows
    from core.tools.native.command import _engineering_command_scope_block
    assignment = create(taskBrief={**task(), "writeSet": []})["assignment"]
    assert send(assignment)["ok"]
    forged = {**assignment, "taskBrief": task("outside.txt")}
    context = {"session_id": assignment["childSessionId"], "user_id": USER, "run_id": "run-child-0", "runtime_kind": "chat",
               "workspace_path": str(harness.workspace), "project_assignment": forged,
               "allowed_write_paths": [str(harness.workspace)], "engineering_capsule_mode": "write"}
    assert not _task_write_scope_allows(context, harness.workspace / "outside.txt")
    assert _engineering_command_scope_block(context, operation="test", command="echo forbidden > outside.txt")
    context["project_assignment"] = {**forged, "assignmentId": "forged-id"}
    assert not _task_write_scope_allows(context, harness.workspace / "outside.txt")


def test_creation_failure_rolls_back_child_binding_and_relation(harness):
    with harness.db.get_connection() as conn:
        conn.execute("CREATE TRIGGER fail_assignment BEFORE INSERT ON session_command_assignments BEGIN SELECT RAISE(ABORT, 'fixture assignment failure'); END")
        conn.commit()
    with pytest.raises(Exception, match="fixture assignment failure"):
        create()
    assert len(harness.db.get_sessions()) == 1
    assert harness.db.list_session_command_assignments(ROOT) == []
    with harness.db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_scope_bindings").fetchone()[0] == 1


def test_three_persistent_children_and_response_loss_retries_survive_restart(harness):
    children = [create(idempotencyKey=f"task-{index}", taskBrief=task(f"page-{index}.txt")) for index in range(3)]
    assert all(item["ok"] for item in children), children
    ids = {item["assignment"]["childSessionId"] for item in children}
    assert len(ids) == 3
    for item in children:
        assignment = item["assignment"]
        assert assignment["rootSessionId"] == ROOT
        assert assignment["userInstruction"] == AUTHORIZATION
        assert harness.db.get_session(assignment["childSessionId"])["user_id"] == USER
        assert harness.db.get_session(assignment["childSessionId"])["metadata"]["workspace_path"] == str(harness.workspace)
        assert harness.db.get_session(assignment["childSessionId"])["metadata"]["project_id"] == "fixture-project"
        assert harness.db.get_session_scope_binding(assignment["childSessionId"])["workspace_path"] == str(harness.workspace)
        assert assignment["taskBrief"]["engineeringTaskCapsule"]["contractStatus"] == "valid"
    reopened = DatabaseManager(harness.db.db_path)
    harness.monkeypatch.setattr(coordination_module, "db", reopened)
    harness.monkeypatch.setattr(command_module, "db", reopened)
    retried = create(idempotencyKey="task-0", taskBrief=task("page-0.txt"))
    assert retried["idempotent"] is True
    assert retried["assignment"]["childSessionId"] == children[0]["assignment"]["childSessionId"]
    assert len(reopened.get_sessions()) == 4
    listed = invoke({"mode": "list", "limit": 2})
    tail = invoke({"mode": "list", "after": listed["nextCursor"]})
    assert {x["childSessionId"] for x in listed["assignments"] + tail["assignments"]} == ids


def test_concurrent_response_loss_does_not_leave_orphan_child(harness):
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: create(), range(4)))
    assert all(item["ok"] for item in results), results
    assert len({item["assignment"]["childSessionId"] for item in results}) == 1
    assert len(harness.db.get_sessions()) == 2
    conflict = create(taskBrief=task("different.txt"))
    assert conflict["error"] == "assignment_idempotency_conflict"
    assert len(harness.db.get_sessions()) == 2


def test_root_continue_binds_new_run_without_rewriting_old_run(harness):
    assignment = create()["assignment"]
    first = send(assignment)
    old_message = harness.db.get_session_coordination_message(first["message"]["messageId"])
    harness.db.update_run_record("run-root-001", status="completed")
    harness.db.create_run_record("run-root-002", ROOT, user_id=USER, run_type="chat", status="running")
    later = invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": 1,
                    "content": "Continue the same bounded assignment.", "idempotencyKey": "continue-page"},
                   text="继续", run="run-root-002")
    assert later["ok"] is True, later
    assert harness.db.get_session_coordination_message(later["message"]["messageId"])["sourceRunId"] == "run-root-002"
    assert harness.db.get_session_coordination_message(old_message["id"])["sourceRunId"] == "run-root-001"
    duplicate = invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": 1,
                        "content": "Continue the same bounded assignment.", "idempotencyKey": "continue-page"},
                       text="继续", run="run-root-002")
    assert duplicate["message"]["messageId"] == later["message"]["messageId"]
    after_result = invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": 1,
                           "content": "Apply the observed result within the original scope.", "idempotencyKey": "after-result"},
                          text="A peer returned evidence.", run="run-root-002",
                          human_metadata={"v8os_session_coordination": {"messageType": "reply"}})
    assert after_result["ok"], after_result


@pytest.mark.parametrize("change", ["other_root", "other_user", "revision", "revoke", "workspace", "target_user"])
def test_unrelated_revoked_and_revised_assignments_never_dispatch(harness, change):
    assignment = create()["assignment"]
    args = {}
    if change == "other_root":
        harness.db.create_or_update_session("other-root", "Other", user_id=USER)
        harness.db.create_run_record("run-other-root", "other-root", user_id=USER, run_type="chat", status="running")
        args = {"session": "other-root", "run": "run-other-root"}
    elif change == "other_user":
        args = {"user": "attacker"}
    elif change == "revision":
        assignment["revision"] = 42
    elif change == "revoke":
        revoked = invoke({"mode": "revoke", "assignmentId": assignment["assignmentId"], "revision": 1})
        assert revoked["ok"]
    elif change == "workspace":
        binding = harness.db.get_session_scope_binding(assignment["childSessionId"])
        other = harness.workspace.parent / "other-workspace"
        other.mkdir()
        binding["workspace_path"] = str(other)
        harness.db.upsert_session_scope_binding(binding)
    elif change == "target_user":
        harness.db.add_message("new-user-instruction", assignment["childSessionId"], "user", "Change goal: do not write the page.")
    denied = send(assignment, **args)
    assert denied["ok"] is False, denied
    assert harness.scheduled == []
    assert not (harness.workspace / "page.txt").exists()


def test_same_text_without_relation_is_not_an_executable_assignment(harness):
    result = invoke({"mode": "continue", "assignmentId": "invented-authority", "revision": 1,
                     "content": "Create page.txt and verify its content.", "idempotencyKey": "spoof"})
    assert result["error"] == "assignment_relation_required"
    assert harness.service.assignment_for_message({"authority": "current_user_explicit", "content": "Create page.txt and verify its content."}, session_id=ROOT) == {}
    assert invoke({"mode": "create", "title": "Page", "taskBrief": task(), "idempotencyKey": "peer-created"},
                  human_metadata={"v8os_session_coordination": {"content": AUTHORIZATION}})["ok"] is False


@pytest.mark.parametrize("instruction", ["请分工完成这个项目", "用三条独立工作线推进", "Split the project into independent workstreams"])
def test_natural_assignment_creation_needs_no_magic_authorization_quote(harness, instruction):
    result = invoke({"mode": "create", "title": "Page", "taskBrief": task(), "idempotencyKey": "natural"}, text=instruction)
    assert result["ok"], result
    assert result["assignment"]["userInstruction"] == instruction


def test_guessing_relation_or_replaying_changed_message_cannot_bind_execution(harness):
    from core.tools.native.workspace_file import _task_write_scope_allows
    assignment = create()["assignment"]
    child = assignment["childSessionId"]
    harness.db.create_run_record("unassigned-child-run", child, user_id=USER, run_type="chat", status="running")
    context = {"session_id": child, "user_id": USER, "run_id": "unassigned-child-run",
               "workspace_path": str(harness.workspace), "runtime_kind": "chat",
               "project_assignment": assignment, "allowed_write_paths": [str(harness.workspace)]}
    assert not _task_write_scope_allows(context, harness.workspace / "page.txt")
    sent = send(assignment)
    row = harness.db.get_session_coordination_message(sent["message"]["messageId"])
    with pytest.raises(ValueError, match="assignment_message_payload_changed"):
        harness.service.assignment_for_message({**row, "content": "Changed replay body"}, session_id=child)
    context["run_id"] = "run-child-0"
    context.pop("project_assignment")
    context.pop("allowed_write_paths")
    # Losing the prompt projection cannot remove the durable execution bound.
    assert _task_write_scope_allows(context, harness.workspace / "page.txt")
    assert not _task_write_scope_allows(context, harness.workspace / "other.txt")
    context["workspace_path"] = str(harness.workspace.parent)
    assert not _task_write_scope_allows(context, harness.workspace / "page.txt")


def test_private_request_token_is_not_client_authority_and_target_run_must_match(harness):
    from api.models import ChatMessage, ChatRequest, ChatRequestData, EngineConfig
    from runtimes.chat.runtime import ChatRuntime
    import runtimes.chat.runtime as runtime_module
    harness.monkeypatch.setattr(runtime_module, "db", harness.db)
    assignment = create()["assignment"]
    sent = send(assignment)
    message_id = sent["message"]["messageId"]
    client_data = ChatRequestData.model_validate({
        "_session_coordination_message_id": message_id, "sessionCoordinationMessageId": message_id,
        "project_assignment": assignment,
    })
    assert client_data._session_coordination_message_id is None
    client_data._session_coordination_message_id = message_id
    request = ChatRequest(messages=[], config=EngineConfig(), session_id=assignment["childSessionId"], user_id="attacker", data=client_data)
    with pytest.raises(ValueError, match="assignment_request_owner_mismatch"):
        ChatRuntime._normalize_session_coordination_message(request, session_id=assignment["childSessionId"])
    request.user_id = USER
    with pytest.raises(ValueError, match="assignment_request_run_binding_mismatch"):
        ChatRuntime().prepare_run_context(request, transport="session_coordination", run_id="unassigned-run")
    runtime = ChatRuntime()
    harness.monkeypatch.setattr(runtime, "_resolve_engine_config", lambda request: None)
    human_request = ChatRequest(
        messages=[ChatMessage(role="user", content="Change this task to read-only review.")],
        config=EngineConfig(), session_id=assignment["childSessionId"], user_id=USER,
    )
    runtime.prepare_request(human_request)
    denied = send(assignment)
    assert denied["ok"] is False
    assert denied["error"] == "assignment_target_revision_changed"


def test_workspace_not_ready_or_escaping_write_set_creates_nothing(harness):
    assert create(taskBrief=task("../escape.txt"))["error"] == "assignment_path_outside_workspace"
    harness.workspace.rmdir()
    assert create()["error"] == "assignment_workspace_not_ready"
    assert len(harness.db.get_sessions()) == 1


def test_dispatch_rechecks_revocation_after_message_was_queued(harness):
    assignment = create()["assignment"]
    harness.monkeypatch.setattr(harness.service, "_wake_idle_target", lambda row, target: row)
    response = send(assignment)
    message_id = response["message"]["messageId"]
    invoke({"mode": "revoke", "assignmentId": assignment["assignmentId"], "revision": 1})
    result = harness.service.dispatch_message(message_id)
    assert result["state"] == "blocked"
    assert result["errorCode"] == "assignment_revoked"


def test_assignment_failure_between_run_creation_and_schedule_closes_the_run(harness):
    assignment = create()["assignment"]
    harness.monkeypatch.setattr(harness.service, "_wake_idle_target",
                                SessionCoordinationService._wake_idle_target.__get__(harness.service))
    def rejected_binding(self, message, *, run_id):
        raise ValueError("assignment_revoked")
    harness.monkeypatch.setattr(SessionCommandService, "bind_run", rejected_binding)
    response = send(assignment)
    assert response["ok"] is False
    assert response["message"]["state"] == "blocked"
    runs = harness.db.list_run_records(session_id=assignment["childSessionId"])
    assert len(runs) == 1 and runs[0]["status"] == "failed"
    assert harness.scheduled == []


def test_empty_child_prompt_capsule_and_actual_native_file_execution(harness):
    from api.models import ChatRequest, ChatRequestData, EngineConfig
    from runtimes.chat.runtime import ChatRuntime
    import runtimes.chat.runtime as runtime_module
    from core.tools.native.workspace_file import write_native_file
    from core.tools.native import workspace_file
    from runtimes.memory.project_registry import project_registry_service

    project_registry_service.save_project({
        "id": "fixture-project", "name": "Fixture", "workspaceId": "fixture-workspace",
        "workspacePath": str(harness.workspace), "workspaceTrustState": "trusted",
        "workspaceTrustSource": "test_user_authorized",
    })

    harness.monkeypatch.setattr(runtime_module, "db", harness.db)
    assignment = create()["assignment"]
    sent = send(assignment)
    child = assignment["childSessionId"]
    data = ChatRequestData()
    data._session_coordination_message_id = sent["message"]["messageId"]
    request = ChatRequest(messages=[], config=EngineConfig(), session_id=child, user_id=USER, data=data)
    message = ChatRuntime._normalize_session_coordination_message(request, session_id=child)
    runtime = ChatRuntime()
    # Offline model selection fixture; keep the real request preparation,
    # assignment normalization, prompt injection and Capsule path.
    harness.monkeypatch.setattr(runtime, "_resolve_engine_config", lambda request: None)
    prepared = runtime.prepare_request(request)
    messages = prepared.lc_messages
    assert prepared.session_command_assignment["assignmentId"] == assignment["assignmentId"]
    assert "Create the assigned page" in messages[-1].content
    assert AUTHORIZATION in messages[-1].content
    verified = SessionCommandService(database=harness.db).execution_context(message["projectAssignment"], session_id=child, user_id=USER)
    assert verified["engineering_capsule_mode"] == "write"
    assert verified["allowed_write_paths"] == [str(harness.workspace / "page.txt")]
    # Exercise the actual native write function and its path/Capsule checks;
    # only the independent Safety review is a fixed allow decision for fixture IO.
    harness.monkeypatch.setattr(workspace_file.safety_guardian, "assess_file_write", lambda *args, **kwargs: None)
    harness.monkeypatch.setattr(workspace_file, "workspace_safety_decision", lambda *args, **kwargs: None)
    harness.monkeypatch.setattr(workspace_file, "_enforce_safety_decision", lambda *args, **kwargs: (True, None))
    harness.monkeypatch.setattr(workspace_file.safety_guardian, "observe_post_action", lambda **kwargs: None)
    with bind_runtime_context(runtime_kind="chat", session_id=child, run_id="run-child-0",
                              user_id=USER, workspace_path=str(harness.workspace),
                              workspace_id="fixture-workspace", project_id="fixture-project", **verified):
        allowed = write_native_file.invoke({"name": "write_native_file", "type": "tool_call", "id": "write-allowed", "args": {"path": "page.txt", "content": "checked fixture output"}})
        assert (harness.workspace / "page.txt").exists(), allowed
        assert (harness.workspace / "page.txt").read_text(encoding="utf-8") == "checked fixture output", allowed
        write_native_file.invoke({"name": "write_native_file", "type": "tool_call", "id": "write-outside", "args": {"path": "outside.txt", "content": "must not write"}})
        assert not (harness.workspace / "outside.txt").exists()
        invoke({"mode": "revoke", "assignmentId": assignment["assignmentId"], "revision": 1})
        denied = write_native_file.invoke({"name": "write_native_file", "type": "tool_call", "id": "write-revoked", "args": {"path": "page.txt", "content": "must not overwrite", "allow_full_replace": True}})
        assert (harness.workspace / "page.txt").read_text(encoding="utf-8") == "checked fixture output", denied


def wait_assignment(assignment, *, generation="wait-1", after_cursor=0, wait_for="any"):
    return invoke({"mode": "await", "assignmentIds": [assignment["assignmentId"]],
                   "afterCursor": after_cursor, "waitFor": wait_for, "idempotencyKey": generation}, routed=True)


def use_real_delivery(harness):
    import erc.command_router as router_module
    from erc.command_router import runtime_command_router
    harness.monkeypatch.setattr(harness.service, "_wake_idle_target", SessionCoordinationService._wake_idle_target.__get__(harness.service))
    harness.monkeypatch.setattr(router_module, "db", harness.db)
    harness.monkeypatch.setattr(router_module, "build_canonical_chat_turn_window", lambda *args, **kwargs: {"messages": []})
    harness.monkeypatch.setattr(runtime_command_router, "_scope_payload_for_session", lambda _sid: {})
    scheduled = []
    def schedule(request, *, run_id, **kwargs):
        scheduled.append((request, run_id))
        return {"scheduled": True}
    harness.monkeypatch.setattr(runtime_command_router, "schedule_chat_run", schedule)
    return scheduled


def finalize_wait(harness):
    from runtimes.chat.runtime import ChatRuntime
    import runtimes.chat.runtime as runtime_module
    events = []
    harness.monkeypatch.setattr(runtime_module, "db", harness.db)
    harness.monkeypatch.setattr(runtime_module.workflow_ledger_service, "sync_run_status", lambda *args, **kwargs: None)
    run = SimpleNamespace(active_run_id="run-root-001", emit_runtime_event=lambda topic, payload, **kwargs: events.append((topic, payload)),
                          run_handle=SimpleNamespace(descriptor=SimpleNamespace(status="running"),
                                                     refresh_chat_snapshot=lambda: harness.db.get_run_record("run-root-001")))
    result = ChatRuntime().finalize_success_run(run)
    assert result["status"] == "paused" and result["reason"] == "session_results_wait"
    assert events[0][1]["summary"] == "等待项目任务结果"
    return result


def activate_wait(harness, request):
    from runtimes.chat.runtime import ChatRuntime
    import runtimes.chat.runtime as runtime_module
    import erc.run_service as run_module
    harness.monkeypatch.setattr(runtime_module, "db", harness.db)
    harness.monkeypatch.setattr(run_module, "db", harness.db)
    harness.monkeypatch.setattr(runtime_module.workflow_ledger_service, "sync_run_status", lambda *args, **kwargs: None)
    record = harness.db.get_run_record("run-root-001")
    run = SimpleNamespace(active_run_id="run-root-001", session_id=ROOT, user_id=USER, request=request,
                          transport="session_coordination", is_resume_request=False,
                          run_handle=SimpleNamespace(descriptor=SimpleNamespace(status=record["status"])))
    return ChatRuntime()._activate_run_for_execution(run)


def test_await_toolnode_ends_graph_and_early_result_returns_without_wait(harness, tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from graph.tool_routing import create_routed_tool_node
    from graph.workflow_assembly import _route_runtime_tool_commands
    assignment = create()["assignment"]
    node = create_routed_tool_node([session_command_broker], "tools", "supervisor")
    calls = []
    def route(state, config):
        return _route_runtime_tool_commands(asyncio.run(node(state, config)))
    graph = StateGraph(ToolState)
    graph.add_node("tools", route)
    graph.add_node("supervisor", lambda state: calls.append("must not call the model while waiting") or {})
    graph.add_edge(START, "tools")
    graph.add_edge("supervisor", END)
    config = {"configurable": {"thread_id": "root-await-checkpoint"}}
    with SqliteSaver.from_conn_string(str(tmp_path / "await-checkpoint.sqlite3")) as saver:
        compiled = graph.compile(checkpointer=saver)
        with bind_runtime_context(runtime_kind="chat", agent_id="supervisor", session_id=ROOT, run_id="run-root-001", user_id=USER):
            compiled.invoke({"messages": [AIMessage(content="", tool_calls=[{
                "id": "wait-call", "name": "session_command_broker", "args": {"mode": "await", "assignmentIds": [assignment["assignmentId"]],
                "idempotencyKey": "wait-1"}}, {"id": "list-call", "name": "session_command_broker", "args": {"mode": "list"}}])], "current_route_context": {}}, config)
    assert calls == []
    with SqliteSaver.from_conn_string(str(tmp_path / "await-checkpoint.sqlite3")) as saver:
        snapshot = graph.compile(checkpointer=saver).get_state(config)
        assert snapshot.next == ()
        receipts = {message.tool_call_id: message for message in snapshot.values["messages"] if hasattr(message, "tool_call_id")}
        assert set(receipts) == {"wait-call", "list-call"}
        assert receipts["wait-call"].additional_kwargs["sessionResultsWait"] == harness.db.get_run_record("run-root-001")["metadata"]["sessionResultWait"]["generation"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    use_real_delivery(harness)
    result = publish_result(harness, assignment, sent, version=1, status="completed", content="Already available.", evidence=["artifact:page"])
    ready = wait_assignment(assignment, generation="ready-now")
    assert ready["waiting"] is False and ready["results"][0]["messageId"] == result["message"]["messageId"]
    assert harness.db.get_run_record("run-root-001")["metadata"]["sessionResultWait"]["state"] == "consumed"


def test_result_before_lane_release_resumes_same_run_once_and_checkpoint_consumes(harness):
    from runtimes.chat.runtime import ChatRuntime
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    waiting = wait_assignment(assignment)
    assert waiting["waiting"]
    scheduled = use_real_delivery(harness)
    harness.monkeypatch.setattr(coordination_module.session_admission_service, "get_lane_view", lambda sid: {"activeRunId": "run-root-001"})
    result = publish_result(harness, assignment, sent, version=1, status="completed", content="The bounded task finished.", evidence=["artifact:page"])
    assert scheduled == []
    finalize_wait(harness)
    harness.service.dispatch_for_session(ROOT)
    assert scheduled == []  # Still owns the lane.
    harness.monkeypatch.setattr(coordination_module.session_admission_service, "get_lane_view", lambda sid: {"activeRunId": None})
    harness.service.dispatch_for_session(ROOT)
    harness.service.dispatch_for_session(ROOT)
    assert len(scheduled) == 1 and scheduled[0][1] == "run-root-001"
    request = scheduled[0][0]
    assert request.data._session_coordination_wait_generation == waiting["generation"]
    assert activate_wait(harness, request)["updated"]
    assert not activate_wait(harness, request)["updated"]
    row = harness.db.get_session_coordination_message(result["message"]["messageId"])
    inbound = ChatRuntime._normalize_session_coordination_message(request, session_id=ROOT)
    messages = []
    ChatRuntime()._inject_session_coordination_message(messages, inbound)
    assert "The bounded task finished." in messages[-1].content
    harness.service.mark_injected(row["id"], target_run_id="run-root-001")
    assert harness.service.acknowledge_project_results({"messages": messages}, session_id=ROOT, run_id="run-root-001") == 1
    assert harness.db.get_run_record("run-root-001")["metadata"]["sessionResultWait"]["state"] == "consumed"
    assert len(harness.db.list_run_records(session_id=ROOT)) == 1


def test_await_schedule_failure_boot_recovery_and_orphan_projection(harness):
    from erc.command_router import runtime_command_router
    import erc.workflow_ledger as ledger_module
    from erc.liveness_projection import build_liveness_view
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    wait_assignment(assignment)
    finalize_wait(harness)
    scheduled = use_real_delivery(harness)
    original_schedule = runtime_command_router.schedule_chat_run
    harness.monkeypatch.setattr(runtime_command_router, "schedule_chat_run", lambda *args, **kwargs: None)
    final = publish_result(harness, assignment, sent, version=1, status="completed", content="Durable final.", evidence=["artifact:page"])
    assert harness.db.get_run_record("run-root-001")["metadata"]["sessionResultWait"]["state"] == "waiting"
    harness.monkeypatch.setattr(runtime_command_router, "schedule_chat_run", original_schedule)
    harness.service.dispatch_for_session(ROOT)
    assert len(scheduled) == 1
    reopened = DatabaseManager(harness.db.db_path)
    harness.monkeypatch.setattr(coordination_module, "db", reopened)
    harness.monkeypatch.setattr(coordination_module, "_PROJECT_RESULT_DELIVERY_OWNER", "new-engine-boot")
    harness.monkeypatch.setattr(ledger_module, "db", reopened)
    ledger = ledger_module.WorkflowLedgerService()
    harness.monkeypatch.setattr(ledger, "emit_reconciliation_event", lambda *args, **kwargs: None)
    harness.monkeypatch.setattr(ledger, "sync_run_status", lambda *args, **kwargs: None)
    ledger.reconcile_orphaned_runs()
    record = reopened.get_run_record("run-root-001")
    assert record["status"] == "paused" and not record["metadata"].get("orphaned")
    projection = build_liveness_view(run_record=record, workflow_view={}, runtime_events=[], lane_view={})
    assert projection["status"] == "waiting" and projection["idleReason"] == "session_results_wait"
    harness.service.recover_pending()
    harness.service.recover_pending()
    assert len(scheduled) == 2 and scheduled[-1][1] == "run-root-001"
    assert scheduled[-1][0].data._session_coordination_message_id == final["message"]["messageId"]


@pytest.mark.parametrize("change", ["manual_pause", "pause_signal_without_reason", "new_human", "new_run", "pending_approval", "new_generation"])
def test_stale_scheduled_wake_cannot_override_human_or_new_wait(harness, change):
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    wait_assignment(assignment)
    finalize_wait(harness)
    scheduled = use_real_delivery(harness)
    publish_result(harness, assignment, sent, version=1, status="accepted", content="Working.")
    request = scheduled[0][0]
    record = harness.db.get_run_record("run-root-001")
    if change == "manual_pause":
        harness.db.update_run_record("run-root-001", status="paused", metadata={**record["metadata"], "pause_reason": "human_pause"})
    elif change == "pause_signal_without_reason":
        harness.db.update_run_record("run-root-001", status="paused", metadata={**record["metadata"], "control_signal": {"command": "pause"}})
    elif change == "new_human":
        harness.db.add_message("new-human", ROOT, "user", "先做别的事情")
    elif change == "pending_approval":
        harness.db.add_pending_approval("approval-root", ROOT, "run-root-001", "fixture", "pending", {})
    elif change == "new_run":
        harness.db.create_run_record("new-human-run", ROOT, user_id=USER, run_type="chat", status="running")
    else:
        from api.models import ChatRequest, EngineConfig
        manual = ChatRequest(messages=[], session_id=ROOT, user_id=USER, config=EngineConfig())
        assert activate_wait(harness, manual)["updated"]
        assert harness.db.get_run_record("run-root-001")["metadata"]["sessionResultWait"]["state"] == "superseded"
        next_wait = wait_assignment(assignment, generation="wait-2", after_cursor=1)
        assert next_wait["waiting"]
        finalize_wait(harness)
        publish_result(harness, assignment, sent, version=2, status="completed", content="Ready.", evidence=["artifact:page"])
        assert scheduled[-1][0].data._session_coordination_wait_generation == next_wait["generation"]
    assert not activate_wait(harness, request)["updated"]
    assert harness.db.get_run_record("run-root-001")["status"] == "paused"
    if change == "new_generation":
        assert activate_wait(harness, scheduled[-1][0])["updated"]


def test_all_final_await_ignores_partial_and_rejects_unrelated_selection(harness):
    from api.models import ChatRequestData
    first = create()["assignment"]
    second = create(idempotencyKey="second-task", taskBrief=task("second.txt"))["assignment"]
    first_send, second_send = send(first), send(second)
    for response in (first_send, second_send):
        row = harness.db.get_session_coordination_message(response["message"]["messageId"])
        harness.service.mark_injected(row["id"], target_run_id=row["targetRunId"])
    args = {"mode": "await", "assignmentIds": [first["assignmentId"], second["assignmentId"]], "waitFor": "all_final", "idempotencyKey": "both"}
    waiting = invoke(args, routed=True)
    assert waiting["waiting"]
    assert invoke({**args, "afterCursor": 9})["error"] == "session_wait_generation_conflict"
    assert invoke({**args, "assignmentIds": ["unrelated"]})["error"] == "session_wait_assignment_scope_mismatch"
    forged = ChatRequestData.model_validate({"_session_coordination_wait_generation": waiting["generation"]})
    assert forged._session_coordination_wait_generation is None
    finalize_wait(harness)
    scheduled = use_real_delivery(harness)
    publish_result(harness, first, first_send, version=1, status="completed", content="First done.", evidence=["artifact:first"])
    publish_result(harness, second, second_send, version=1, status="partial", content="Second partial.", evidence=["artifact:second-partial"])
    assert scheduled == []
    publish_result(harness, second, second_send, version=2, status="completed", content="Second done.", evidence=["artifact:second"])
    assert len(scheduled) == 1 and scheduled[0][1] == "run-root-001"


def test_late_schedule_failure_cannot_restore_a_new_wait_generation(harness):
    from erc.command_router import runtime_command_router
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    first_wait = wait_assignment(assignment)
    finalize_wait(harness)
    use_real_delivery(harness)
    new_generation = []
    def late_failure(request, **kwargs):
        # A human resumes and the Supervisor chooses another wait while the
        # original scheduler call is still in flight.
        harness.db.update_run_record("run-root-001", status="running")
        next_wait = wait_assignment(assignment, generation="next-wait", after_cursor=1)
        new_generation.append(next_wait["generation"])
        harness.db.pause_session_result_wait("run-root-001")
        return None
    harness.monkeypatch.setattr(runtime_command_router, "schedule_chat_run", late_failure)
    publish_result(harness, assignment, sent, version=1, status="accepted", content="Accepted.")
    wait = harness.db.get_run_record("run-root-001")["metadata"]["sessionResultWait"]
    assert wait["generation"] == new_generation[0] != first_wait["generation"]
    assert wait["state"] == "waiting"


def cancellation_control_owner(harness):
    import erc.command_service as control_module
    import erc.run_service as run_module
    control = control_module.CommandService()
    harness.monkeypatch.setattr(run_module, "db", harness.db)
    harness.monkeypatch.setattr(control_module, "command_service", control)
    return control


@pytest.mark.parametrize("child_status", ["running", "waiting_input", "waiting_approval", "paused"])
def test_cancel_requests_exact_child_without_faking_stopped_or_dismissing_approval(harness, child_status):
    from core.tools.native.workspace_file import write_native_file
    from erc.command_service import CommandService
    import erc.run_service as run_module
    control = cancellation_control_owner(harness)
    assignment = create()["assignment"]
    sent = send(assignment)
    message_id = sent["message"]["messageId"]
    harness.service.mark_injected(message_id, target_run_id="run-child-0")
    child = assignment["childSessionId"]
    harness.db.update_run_record("run-child-0", status=child_status)
    harness.db.add_pending_approval("pending-child-approval", child, "run-child-0", "fixture", "pending", {})
    args = {"mode": "cancel", "assignmentId": assignment["assignmentId"], "revision": 1,
            "targetRunId": "run-child-0", "idempotencyKey": "cancel-page"}
    result = invoke(args, routed=True)
    assert result["ok"] and result["controlStatus"] == "cancellation_requested"
    assert result["observedRunStatus"] == child_status and result["stopConfirmed"] is False
    assert harness.db.get_run_record("run-child-0")["status"] == child_status
    assert harness.db.get_pending_approval("pending-child-approval")["status"] == "pending"
    assert invoke(args)["requestId"] == result["requestId"]
    # Restart retains the request. Clearing the ordinary control signal during
    # a governed resume must not restore the old assignment's write authority.
    reopened = DatabaseManager(harness.db.db_path)
    harness.monkeypatch.setattr(run_module, "db", reopened)
    harness.monkeypatch.setattr(command_module, "db", reopened)
    assert CommandService().peek_control_signal("run-child-0")["command"] == "cancel"
    control.clear_control_signal("run-child-0")
    reopened.update_run_record("run-child-0", status="running")
    with bind_runtime_context(runtime_kind="chat", session_id=child, run_id="run-child-0", user_id=USER,
                              workspace_path=str(harness.workspace)):
        receipt = write_native_file.invoke({"name": "write_native_file", "type": "tool_call", "id": "after-cancel",
                                           "args": {"path": "page.txt", "content": "must not write after cancel"}})
    assert not (harness.workspace / "page.txt").exists(), receipt
    with pytest.raises(ValueError, match="assignment_cancellation_requested"):
        SessionCommandService(database=reopened).bind_run(reopened.get_session_coordination_message(message_id), run_id="run-child-0")


@pytest.mark.parametrize("changed", ["other_user", "other_root", "wrong_target", "old_revision", "revoked", "terminal", "binding"])
def test_cancel_rejects_unrelated_or_changed_target_without_control_signal(harness, changed):
    cancellation_control_owner(harness)
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    args = {"mode": "cancel", "assignmentId": assignment["assignmentId"], "revision": 1,
            "targetRunId": "run-child-0", "idempotencyKey": "cancel-page"}
    identity = {}
    if changed == "other_user":
        identity["user"] = "intruder"
    elif changed == "other_root":
        harness.db.create_or_update_session("other-root", "Other", user_id=USER)
        harness.db.create_run_record("other-root-run", "other-root", user_id=USER, run_type="chat", status="running")
        identity = {"session": "other-root", "run": "other-root-run"}
    elif changed == "wrong_target":
        args["targetRunId"] = "run-root-001"
    elif changed == "old_revision":
        args["revision"] = 99
    elif changed == "revoked":
        invoke({"mode": "revoke", "assignmentId": assignment["assignmentId"], "revision": 1})
    elif changed == "terminal":
        harness.db.update_run_record("run-child-0", status="completed")
    else:
        record = harness.db.get_run_record("run-child-0")
        metadata = record["metadata"]
        metadata["sessionAssignment"]["messageId"] = "forged-message"
        harness.db.update_run_record("run-child-0", status="running", metadata=metadata)
    before = harness.db.get_run_record("run-child-0")
    denied = invoke(args, **identity)
    assert denied["ok"] is False, denied
    after = harness.db.get_run_record("run-child-0")
    assert after["status"] == before["status"]
    assert not after["metadata"].get("control_signal")
    assert not after["metadata"]["sessionAssignment"].get("cancelRequested")


def test_cancel_binding_cas_does_not_cancel_rebound_message(harness):
    cancellation_control_owner(harness)
    assignment = create()["assignment"]
    sent = send(assignment)
    old_message = sent["message"]["messageId"]
    record = harness.db.get_run_record("run-child-0")
    metadata = record["metadata"]
    metadata["sessionAssignment"]["messageId"] = "new-dispatch"
    metadata["sessionResultWait"] = {"generation": "child-new-wait", "state": "waiting"}
    harness.db.update_run_record("run-child-0", status="queued", metadata=metadata)
    with pytest.raises(ValueError, match="assignment_cancel_binding_changed"):
        harness.db.request_session_assignment_cancel(assignment_id=assignment["assignmentId"], revision=1,
            root_session_id=ROOT, user_id=USER, target_run_id="run-child-0", expected_message_id=old_message,
            expected_status="queued", idempotency_key="cancel-page")
    actual = harness.db.get_run_record("run-child-0")["metadata"]
    assert actual["sessionResultWait"]["generation"] == "child-new-wait"
    assert not actual["sessionAssignment"].get("cancelRequested")


def test_cancel_wins_against_concurrent_stale_run_rebinding(harness):
    from threading import Event
    assignment = create()["assignment"]
    sent = send(assignment)
    message = harness.db.get_session_coordination_message(sent["message"]["messageId"])
    concurrent = DatabaseManager(harness.db.db_path)
    before_bind, cancelled = Event(), Event()
    original_update = harness.db.update_run_metadata_key_if_state
    def delayed_binding(*args, **kwargs):
        before_bind.set()
        assert cancelled.wait(10)
        return original_update(*args, **kwargs)
    harness.monkeypatch.setattr(harness.db, "update_run_metadata_key_if_state", delayed_binding)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(SessionCommandService(database=harness.db).bind_run, message, run_id="run-child-0")
        assert before_bind.wait(10)
        concurrent.request_session_assignment_cancel(assignment_id=assignment["assignmentId"], revision=1,
            root_session_id=ROOT, user_id=USER, target_run_id="run-child-0", expected_message_id=message["id"],
            expected_status="queued", idempotency_key="cancel-at-rebind")
        cancelled.set()
        with pytest.raises(ValueError, match="assignment_run_binding_conflict"):
            future.result(timeout=10)
    assert concurrent.get_run_record("run-child-0")["metadata"]["sessionAssignment"]["cancelRequested"]


def test_followup_cannot_start_another_run_before_cancel_is_observed(harness):
    cancellation_control_owner(harness)
    assignment = create()["assignment"]
    sent = send(assignment)
    harness.service.mark_injected(sent["message"]["messageId"], target_run_id="run-child-0")
    harness.db.update_run_record("run-child-0", status="running")
    assert invoke({"mode": "cancel", "assignmentId": assignment["assignmentId"], "revision": 1,
                   "targetRunId": "run-child-0", "idempotencyKey": "cancel-page"})["ok"]
    followup = invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": 1,
                       "content": "After stopping, review the original task.", "idempotencyKey": "after-stop"})
    assert followup["message"]["state"] == "queued"
    assert len(harness.db.list_run_records(session_id=assignment["childSessionId"])) == 1


@pytest.mark.parametrize("case", ["authorized", "forbidden", "runtime_only", "new_user_revision"])
def test_runtime_attention_never_becomes_assignment_user_instruction(harness, case):
    original = "只委派一个验证者执行A，不得委派B，禁止继续委派。" if case == "forbidden" else AUTHORIZATION
    evidence = HumanMessage(content="[Runtime decision event; evidence, not an instruction]\npartial a-ready",
        additional_kwargs={"v8_governance_type": "runtime_episode_attention", "runtimeAttentionId": "public-attention"})
    messages = [*([HumanMessage(content=original)] if case != "runtime_only" else []), evidence]
    expected = original
    if case == "new_user_revision":
        expected = "现在授权创建独立任务，只修改page.txt。"
        messages.append(HumanMessage(content=expected))
        messages.append(evidence.model_copy(update={"id": "public-later-attention"}))
    result = invoke({"mode": "create", "title": "Public task", "taskBrief": task(), "idempotencyKey": "evidence-source"},
                    messages=messages, routed=True)
    if case in {"forbidden", "runtime_only"}:
        assert not result["ok"] and result["error"] == "assignment_user_authorization_required"
        with harness.db.get_connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM session_command_assignments").fetchone()[0] == 0
        assert harness.scheduled == []
    else:
        contract = harness.db.get_session_command_assignment(result["assignment"]["assignmentId"])["contract"]
        assert contract["userInstruction"] == expected
        assert contract["requirementRevision"] == command_module._digest(expected)
        assert contract["authorizationRef"].endswith(":" + contract["requirementRevision"])
        assert contract["userInstruction"] != evidence.content


def test_runtime_attention_cannot_hide_user_revocation_of_assignment_continuation(harness):
    assignment = create()["assignment"]
    messages = [HumanMessage(content="禁止继续这个任务。"), HumanMessage(content="partial ready",
        additional_kwargs={"v8_governance_type": "runtime_episode_attention"})]
    result = send(assignment, messages=messages, routed=True)
    assert not result["ok"] and result["error"] == "assignment_user_authorization_required"
    assert harness.scheduled == []
