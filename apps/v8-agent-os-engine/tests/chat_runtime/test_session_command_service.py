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
from core.tools.native.session_coordination import session_command_broker
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


def invoke(args, *, text=AUTHORIZATION, session=ROOT, user=USER, run="run-root-001", extra_context=None, routed=False, human_metadata=None):
    """Use real StructuredTool + ToolNode state injection, not service kwargs."""
    async def execute():
        graph = StateGraph(ToolState)
        if routed:
            from graph.tool_routing import create_routed_tool_node
            graph.add_node("tools", create_routed_tool_node([session_command_broker], "tools", END))
        else:
            graph.add_node("tools", ToolNode([session_command_broker]))
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        with bind_runtime_context(runtime_kind="chat", agent_id="supervisor", session_id=session,
                                  run_id=run, user_id=user, **(extra_context or {})):
            result = await graph.compile().ainvoke({
                "messages": [HumanMessage(content=text, additional_kwargs=human_metadata or {}),
                             AIMessage(content="", tool_calls=[{"id": "command-call", "name": "session_command_broker", "args": args}])],
                "current_route_context": {},
            })
        return json.loads(result["messages"][-1].content)
    return asyncio.run(execute())


def create(**kwargs):
    return invoke({"mode": "create", "title": "Page task", "taskBrief": task(),
                   "idempotencyKey": "page-task", **kwargs})


def send(assignment, **kwargs):
    return invoke({"mode": "continue", "assignmentId": assignment["assignmentId"], "revision": assignment["revision"],
                   "content": "Create page.txt and verify its content.", "idempotencyKey": "start-page"}, **kwargs)


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
