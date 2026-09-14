import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from typing_extensions import TypedDict

import pytest
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt

from core.database import DatabaseManager
from erc.checkpoint_store import CheckpointStore
from erc.command_router import RuntimeCommandRouter
from erc.command_service import command_service
from erc.kernel import erc_kernel
from erc.models import ApprovalRequest, RuntimeCommand
from erc.workflow_ledger import WorkflowLedgerService
from runtimes.chat.runtime import ChatRuntime


@pytest.fixture
def database(tmp_path, monkeypatch):
    import core.database as database_module
    import erc.run_service as runs
    import erc.kernel as kernel
    import erc.command_router as router
    import erc.event_bus as events
    import erc.workflow_ledger as ledger
    import runtimes.chat.runtime as runtime
    instance = DatabaseManager(tmp_path / "approval-delivery.db")
    instance.create_or_update_session("session", "delivery", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="waiting_approval")
    for module in (database_module, runs, kernel, router, events, ledger, runtime):
        monkeypatch.setattr(module, "db", instance)
    monkeypatch.setattr(command_service, "_remember_safety_allowlist", lambda *args: None)
    return instance


def seed(*, kind="safety_review", request=None):
    command_service.request_approval(ApprovalRequest(approval_id="approval", session_id="session", run_id="run",
        approval_kind=kind, request=request or {"question": "Continue original operation", "operationFingerprint": "original-operation"}))


def router_with_requests(monkeypatch):
    router, requests = RuntimeCommandRouter(), []
    monkeypatch.setattr(router, "_build_resume_chat_request", lambda approval, response: SimpleNamespace(resume_value=response))
    router.configure(schedule_chat_run=lambda request, **kwargs: requests.append(request) or "run")
    return router, requests


def test_approval_commit_process_loss_and_restart_resume_real_graph_effect_once(database, tmp_path, monkeypatch):
    effects = tmp_path / "effects.txt"
    class State(TypedDict, total=False):
        complete: bool
    def prepare(state):
        with effects.open("a") as file:
            file.write("prepared\n")
        return {}
    def execute(state):
        answer = interrupt("Review operation")
        assert answer["decision"] == "approved"
        with effects.open("a") as file:
            file.write("executed\n")
        return {"complete": True}
    def graph(saver):
        return StateGraph(State).add_node("prepare", prepare).add_node("execute", execute).add_edge(START, "prepare").add_edge("prepare", "execute").add_edge("execute", END).compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "original-run"}}
    async def scenario():
        store = CheckpointStore(tmp_path / "checkpoints.db")
        await graph(await store.get_async_sqlite_saver()).ainvoke({}, config)
        await store.close()
        assert effects.read_text().splitlines() == ["prepared"]
        seed()
        assert erc_kernel.approve("approval", response={"decision": "approved"})["decisionApplied"]
        assert json.loads(database.get_pending_approval("approval")["resume_json"])["state"] == "pending"
        # No router call happened before process loss. Startup keeps the
        # committed decision and schedules its original continuation.
        WorkflowLedgerService().reconcile_orphaned_runs()
        assert database.get_run_record("run")["status"] == "running"
        router, requests = router_with_requests(monkeypatch)
        router.recover_approval_resumes(restart=True)
        router.recover_approval_resumes()
        assert len(requests) == 1
        # A second process loss occurred after scheduling, before consumption.
        router.recover_approval_resumes(restart=True)
        assert len(requests) == 2
        stale = SimpleNamespace(active_run_id="run", request=requests[0], transport="system_resume")
        current = SimpleNamespace(active_run_id="run", request=requests[1], transport="system_resume")
        assert not ChatRuntime._consume_approval_delivery(stale)
        assert ChatRuntime._consume_approval_delivery(current)
        assert not ChatRuntime._consume_approval_delivery(current)
        restored = CheckpointStore(tmp_path / "checkpoints.db")
        try:
            result = await graph(await restored.get_async_sqlite_saver()).ainvoke(Command(resume={"decision": "approved"}), config)
            assert result["complete"]
            ChatRuntime._finish_approval_delivery(current)
        finally:
            await restored.close()
        assert effects.read_text().splitlines() == ["prepared", "executed"]
        router.recover_approval_resumes(restart=True)
        assert len(requests) == 2
        assert json.loads(database.get_pending_approval("approval")["resume_json"])["state"] == "completed"
    asyncio.run(scenario())


def test_duplicate_decision_after_scheduling_failure_retries_delivery_without_changing_vote(database, monkeypatch):
    seed()
    router, requests = router_with_requests(monkeypatch)
    router.configure(schedule_chat_run=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scheduler unavailable")))
    first = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id="approval", response={"decision": "approved", "reason": "original"}))
    assert first["decisionApplied"] and not first["resume_scheduled"]
    with database.get_connection() as conn:
        conn.execute("UPDATE pending_approvals SET resume_json=json_remove(resume_json,'$.retryAfter') WHERE id='approval'")
        conn.commit()
    router.configure(schedule_chat_run=lambda request, **kwargs: requests.append(request) or "run")
    second = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id="approval", response={"reason": "different"}))
    assert second["ignored"] and second["resume_scheduled"]
    assert database.get_pending_approval("approval")["response"]["reason"] == "original"
    assert len(requests) == 1


def test_concurrent_deliveries_claim_once_and_cancel_blocks_graph_consumer(database, monkeypatch):
    seed()
    erc_kernel.approve("approval", response={"decision": "approved"})
    router, requests = router_with_requests(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: router.deliver_approval_resume("approval"), range(4)))
    assert sum(bool(item["resume_scheduled"]) for item in results) == 1 and len(requests) == 1
    database.update_run_record("run", status="cancelled")
    assert not ChatRuntime._consume_approval_delivery(SimpleNamespace(active_run_id="run", request=requests[0], transport="system_resume"))
    router.recover_approval_resumes(restart=True)
    assert len(requests) == 1


def test_already_executing_parent_is_not_automatically_replayed_after_restart(database, monkeypatch):
    seed()
    erc_kernel.approve("approval", response={"decision": "approved"})
    router, requests = router_with_requests(monkeypatch)
    router.deliver_approval_resume("approval")
    assert ChatRuntime._consume_approval_delivery(SimpleNamespace(active_run_id="run", request=requests[0], transport="system_resume"))
    WorkflowLedgerService().reconcile_orphaned_runs()
    router.recover_approval_resumes(restart=True)
    assert database.get_run_record("run")["status"] == "interrupted" and len(requests) == 1


def test_spec_approval_application_is_idempotent_across_delivery_retry(database, tmp_path, monkeypatch):
    from core.spec_service import spec_service
    created = spec_service.create_stage(workspace_path=str(tmp_path), user_request="Implement a bounded reviewed operation",
        feature_name="Approval delivery", stage="requirements", kind="feature")
    assert created["ok"]
    spec_id = created["specId"]
    seed(kind="spec_stage_approval", request={"approvalKind": "spec_stage_approval", "specId": spec_id,
                                            "stage": "requirements", "workspacePath": str(tmp_path)})
    router = RuntimeCommandRouter()
    monkeypatch.setattr(router, "_scope_payload_for_session", lambda session_id: {"workspace_path": str(tmp_path)})
    monkeypatch.setattr(router, "_build_manual_resume_chat_request", lambda *args, **kwargs: SimpleNamespace(
        resume_value={"specContinuation": {"specId": spec_id, "stage": "requirements"}}))
    router.configure(schedule_chat_run=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("lost scheduler")))
    first = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id="approval", response={"decision": "approved"}))
    assert first["decisionApplied"] and not first["resume_scheduled"]
    paths = spec_service.resolve_paths(str(tmp_path), spec_id=spec_id)
    before = spec_service._load_manifest(paths)
    assert before["approvals"]["requirements"]["approved"]
    scheduled = []
    router.configure(schedule_chat_run=lambda request, **kwargs: scheduled.append(request) or "run")
    router.recover_approval_resumes(restart=True)
    assert len(scheduled) == 1 and scheduled[0].resume_value["approvalDelivery"]["approvalId"] == "approval"
    assert spec_service._load_manifest(paths) == before
    # The same durable decision cannot approve a changed document during replay.
    document = paths.spec_dir / "requirements.md"
    document.write_text(document.read_text(encoding="utf-8") + "\nChanged requirement.\n", encoding="utf-8")
    with database.get_connection() as conn:
        conn.execute("UPDATE pending_approvals SET resume_json=json_set(resume_json,'$.state','pending') WHERE id='approval'")
        conn.commit()
    failed = router.deliver_approval_resume("approval", restart=True)
    assert not failed["resume_scheduled"] and failed["spec_stage_approval"]["kind"] == "spec_approval_document_changed"
    assert len(scheduled) == 1


@pytest.mark.parametrize("control", ["pause", "interrupt", "cancel"])
def test_delivery_claim_obeys_current_run_control(database, monkeypatch, control):
    seed()
    erc_kernel.approve("approval", response={"decision": "approved"})
    database.update_run_record("run", status="running", metadata={"control_signal": {"command": control}})
    router, requests = router_with_requests(monkeypatch)
    assert not router.deliver_approval_resume("approval")["resume_scheduled"] and requests == []


def test_approval_delivery_scope_revision_cannot_drift(database, monkeypatch):
    database.update_run_record("run", status="waiting_approval", metadata={"scopeRevision": 1})
    seed(request={"question": "Approve", "scopeRevision": 1})
    erc_kernel.approve("approval", response={"decision": "approved"})
    database.update_run_record("run", status="running", metadata={"scopeRevision": 2})
    router, requests = router_with_requests(monkeypatch)
    assert router.deliver_approval_resume("approval")["resume_error"] == "approval_scope_changed"
    assert requests == []


def test_scope_drift_after_scheduling_still_blocks_consumer(database, monkeypatch):
    database.update_run_record("run", status="waiting_approval", metadata={"scopeRevision": 1})
    seed(request={"question": "Approve", "scopeRevision": 1})
    erc_kernel.approve("approval", response={"decision": "approved"})
    router, requests = router_with_requests(monkeypatch)
    assert router.deliver_approval_resume("approval")["resume_scheduled"]
    database.update_run_record("run", status="running", metadata={"scopeRevision": 2})
    assert not ChatRuntime._consume_approval_delivery(SimpleNamespace(active_run_id="run", request=requests[0], transport="system_resume"))


@pytest.mark.parametrize("transport", ["http", "websocket", "submit"])
@pytest.mark.parametrize("strip_marker", [False, True])
def test_public_resume_cannot_forge_or_strip_approval_delivery(database, monkeypatch, transport, strip_marker):
    from api.models import ChatRequest, EngineConfig
    seed()
    erc_kernel.approve("approval", response={"decision": "approved"})
    router, requests = router_with_requests(monkeypatch)
    assert router.deliver_approval_resume("approval")["resume_scheduled"]
    value = {} if strip_marker else requests[0].resume_value
    request = ChatRequest(config=EngineConfig(), messages=[], session_id="session", resume_run_id="run", resume_value=value)
    runtime = ChatRuntime()
    monkeypatch.setattr(runtime, "prepare_request", lambda request: pytest.fail("Public input reached run preparation"))
    with pytest.raises(ValueError, match="approval_resume_requires_governed_router"):
        runtime.prepare_run_context(request, transport=transport)
    assert not runtime._consume_approval_delivery(SimpleNamespace(active_run_id="run", request=request, transport=transport))
    assert json.loads(database.get_pending_approval("approval")["resume_json"])["state"] == "scheduled"


def test_trusted_request_missing_delivery_marker_cannot_bypass_pending_decision(database, monkeypatch):
    seed()
    erc_kernel.approve("approval", response={"decision": "approved"})
    router, requests = router_with_requests(monkeypatch)
    router.deliver_approval_resume("approval")
    stripped = SimpleNamespace(active_run_id="run", request=SimpleNamespace(resume_value={}), transport="system_resume")
    assert not ChatRuntime._consume_approval_delivery(stripped)
    legitimate = SimpleNamespace(active_run_id="run", request=requests[0], transport="system_resume")
    assert ChatRuntime._consume_approval_delivery(legitimate)


def test_unbound_forged_marker_is_rejected_before_creating_public_run(database, monkeypatch):
    from api.models import ChatRequest, EngineConfig
    request = ChatRequest(config=EngineConfig(), messages=[], session_id="session", resume_value={
        "approvalDelivery": {"approvalId": "fake", "generation": "fake", "attempt": 1}})
    runtime = ChatRuntime()
    monkeypatch.setattr(runtime, "prepare_request", lambda request: pytest.fail("Public input reached run preparation"))
    with pytest.raises(ValueError, match="approval_resume_requires_governed_router"):
        runtime.prepare_run_context(request, transport="http")


def test_real_scheduler_rejection_keeps_approval_delivery_retryable(database, monkeypatch):
    import api.chat_realtime_routes as routes
    from api.models import ChatRequest, EngineConfig
    monkeypatch.setattr(routes, "db", database)
    seed()
    erc_kernel.approve("approval", response={"decision": "approved"})
    router = RuntimeCommandRouter()
    monkeypatch.setattr(router, "_build_resume_chat_request", lambda approval, response: ChatRequest(
        messages=[], config=EngineConfig(), session_id="session", resume_run_id="run", resume_value=response))
    def reject(coroutine, **kwargs):
        coroutine.close()
        raise RuntimeError("scheduler admission failed")
    monkeypatch.setattr(routes.chat_run_scheduler, "submit", reject)
    router.configure(schedule_chat_run=routes._schedule_chat_run)
    failed = router.deliver_approval_resume("approval")
    assert not failed["resume_scheduled"]
    assert database.get_run_record("run")["status"] == "running"
    assert json.loads(database.get_pending_approval("approval")["resume_json"])["state"] == "pending"
    submitted = []
    def accept(coroutine, **kwargs):
        submitted.append(kwargs["task_name"])
        coroutine.close()
    monkeypatch.setattr(routes.chat_run_scheduler, "submit", accept)
    router.recover_approval_resumes(restart=True)
    assert submitted == ["chat-run-run"]
    assert json.loads(database.get_pending_approval("approval")["resume_json"])["state"] == "scheduled"


def test_existing_database_upgrade_preserves_approval_and_adds_delivery_state(database):
    seed()
    original = database.get_pending_approval("approval")["request"]
    with database.get_connection() as conn:
        conn.execute("ALTER TABLE pending_approvals DROP COLUMN resume_json")
        conn.commit()
    restored = DatabaseManager(database.db_path)
    row = restored.get_pending_approval("approval")
    assert row["request"] == original and row["status"] == "pending" and row["resume_json"] == "{}"
    decided = restored.resolve_pending_approval_if_pending("approval", status="approved", response={"decision": "approved"})
    assert decided["updated"]
    assert json.loads(restored.get_pending_approval("approval")["resume_json"])["state"] == "pending"
