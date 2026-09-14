from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import db
from erc.command_service import ApprovalDecisionConflict, CommandService
from erc.kernel import ExecutionRuntimeCore
from erc.models import ApprovalRequest, RuntimeCommand
from erc.command_router import RuntimeCommandRouter


@pytest.fixture
def service(monkeypatch):
    value = CommandService()
    monkeypatch.setattr(value, "_remember_safety_allowlist", lambda *a: None)
    monkeypatch.setattr("erc.kernel.command_service", value)
    return value


def seed(service, *, status="waiting_approval", run=None, metadata=None, expires_at=None, kind="safety_review"):
    suffix = uuid.uuid4().hex
    session = f"approval-session-{suffix}"
    if run:
        session = db.get_run_record(run)["session_id"]
    else:
        run = f"approval-run-{suffix}"
        db.create_or_update_session(session, "approval fixture", user_id="fixture-owner")
        db.create_run_record(run, session, user_id="fixture-owner", status=status, metadata=metadata)
    approval = f"approval-{suffix}"
    service.request_approval(ApprovalRequest(approval_id=approval, session_id=session, run_id=run, approval_kind=kind,
        request={"question": "fixture", "operationFingerprint": suffix}, expires_at=expires_at))
    return run, approval


def kernel(monkeypatch):
    value = ExecutionRuntimeCore()
    events = []
    class Emitter:
        def emit(self, name, payload):
            events.append((name, payload))
            return {"topic": name, "payload": payload}
    monkeypatch.setattr(value, "_emitter_for_run", lambda *a, **k: Emitter())
    monkeypatch.setattr("erc.kernel.workflow_ledger_service.sync_run_status", lambda *a, **k: None)
    return value, events


@pytest.mark.parametrize("status", ["cancelled", "failed", "interrupted", "completed"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_late_decision_cannot_change_terminal_run_or_approval(service, status, decision):
    run, approval = seed(service, status=status)
    with pytest.raises(ApprovalDecisionConflict) as caught:
        getattr(service, decision)(approval, {"approved": decision == "approve"})
    assert caught.value.detail["code"] == "owning_run_terminal"
    assert db.get_pending_approval(approval)["status"] == "pending"
    assert db.get_run_record(run)["status"] == status


def test_pending_approval_resumes_once_and_replay_returns_the_same_receipt(service, monkeypatch):
    run, approval = seed(service)
    owner, events = kernel(monkeypatch)
    router = RuntimeCommandRouter()
    monkeypatch.setattr("erc.command_router.erc_kernel", owner)
    resumes = []
    monkeypatch.setattr(router, "_resume_from_approval", lambda a, r: resumes.append(a["id"]) or {"resume_scheduled": True})
    first = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=approval, response={"answer": "first"}))
    second = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=approval, response={"answer": "changed"}))
    assert first["decisionApplied"] and first["resume_scheduled"]
    assert second["ignored"] and not second["resume_scheduled"]
    assert first["approval"] == second["approval"]
    assert resumes == [approval]
    assert [name for name, _ in events].count("approval.approved") == 1
    record = db.get_run_record(run)
    assert record["status"] == "running"
    assert len(record["metadata"]["approvedSafetyOperations"]) == 1


@pytest.mark.parametrize("first,second", [("approve", "reject"), ("reject", "approve")])
def test_opposite_decision_cannot_overwrite_receipt(service, first, second):
    run, approval = seed(service)
    getattr(service, first)(approval, {"reason": "original"})
    before = db.get_pending_approval(approval)
    with pytest.raises(ApprovalDecisionConflict):
        getattr(service, second)(approval, {"reason": "overwrite"})
    assert db.get_pending_approval(approval) == before
    assert db.get_run_record(run)["status"] == ("running" if first == "approve" else "waiting_input")


@pytest.mark.parametrize("mode", ["same", "opposite"])
def test_concurrent_decisions_have_one_winner(service, mode):
    _, approval = seed(service)
    barrier = threading.Barrier(2)
    def decide(action):
        barrier.wait(timeout=5)
        try:
            return getattr(service, action)(approval, {"answer": action})
        except ApprovalDecisionConflict as exc:
            return exc.detail
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, action) for action in ["approve", "approve" if mode == "same" else "reject"]]
        results = [f.result(timeout=10) for f in futures]
    assert sum(bool(r.get("_decision", {}).get("updated")) for r in results) == 1
    assert sum(r.get("code") == "approval_decision_conflict" for r in results) == (mode == "opposite")


def test_other_pending_approval_and_pause_are_not_consumed(service, monkeypatch):
    owner, _ = kernel(monkeypatch)
    run, a = seed(service)
    _, b = seed(service, run=run)
    first = owner.approve(a)
    assert not first["resume_eligible"]
    assert db.get_run_record(run)["status"] == "waiting_approval"
    assert owner.approve(b)["resume_eligible"]
    paused, c = seed(service, status="paused", metadata={"control_signal": {"command": "pause", "reason": "user"}})
    assert not owner.approve(c)["resume_eligible"]
    assert db.get_run_record(paused)["status"] == "paused"
    assert db.get_run_record(paused)["metadata"]["control_signal"]["command"] == "pause"


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_cancel_signal_is_never_cleared_by_decision(service, decision):
    run, approval = seed(service)
    service.issue_control_signal(run, command="cancel", reason="user")
    with pytest.raises(ApprovalDecisionConflict) as caught:
        getattr(service, decision)(approval)
    assert caught.value.detail["code"] == "owning_run_stopping"
    assert service.peek_control_signal(run)["command"] == "cancel"
    assert db.get_pending_approval(approval)["status"] == "pending"


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_cancel_between_decision_and_projection_never_revives_run(service, monkeypatch, decision):
    run, approval = seed(service)
    owner, events = kernel(monkeypatch)
    original = owner._project_approval_decision
    def project(receipt, status):
        service.cancel_run(run, reason="cancel after receipt")
        return original(receipt, status)
    monkeypatch.setattr(owner, "_project_approval_decision", project)
    result = getattr(owner, decision)(approval)
    assert result["decisionApplied"] and not result["resume_eligible"]
    assert db.get_run_record(run)["status"] == "cancelled"
    assert service.peek_control_signal(run)["command"] == "cancel"
    assert not any(name == "run.state.changed" and value["to_status"] == "running" for name, value in events)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_concurrent_cancel_and_decision_leave_cancel_authoritative(service, decision):
    for _ in range(4):
        run, approval = seed(service)
        barrier = threading.Barrier(2)
        def decide():
            barrier.wait(timeout=5)
            try:
                getattr(service, decision)(approval)
            except ApprovalDecisionConflict:
                pass
        def cancel():
            barrier.wait(timeout=5)
            service.cancel_run(run, reason="concurrent cancel")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(decide), pool.submit(cancel)]
            for future in futures:
                future.result(timeout=10)
        assert db.get_run_record(run)["status"] == "cancelled"
        assert service.peek_control_signal(run)["command"] == "cancel"


def test_two_approved_operations_do_not_lose_clearance_or_other_control(service):
    run, a = seed(service)
    _, b = seed(service, run=run)
    barrier = threading.Barrier(2)
    def approve(identifier):
        barrier.wait(timeout=5)
        return service.approve(identifier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve, identifier) for identifier in (a, b)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(bool(item["_decision"]["resumeEligible"]) for item in results) == 1
    record = db.get_run_record(run)
    assert record["status"] == "running"
    assert {item["approval_id"] for item in record["metadata"]["approvedSafetyOperations"]} == {a, b}


def test_cancel_between_old_read_and_atomic_decision_wins(service, monkeypatch):
    run, approval = seed(service)
    original = service._approved_operation
    def build_operation(a, r):
        service.cancel_run(run, reason="cancel before CAS")
        return original(a, r)
    monkeypatch.setattr(service, "_approved_operation", build_operation)
    with pytest.raises(ApprovalDecisionConflict):
        service.approve(approval)
    assert db.get_run_record(run)["status"] == "cancelled"
    assert db.get_pending_approval(approval)["status"] == "cancelled"


def test_expiry_request_change_scope_revision_and_identity_are_rejected(service):
    run, expired = seed(service, expires_at="2000-01-01T00:00:00Z")
    with pytest.raises(ApprovalDecisionConflict) as caught:
        service.approve(expired)
    assert caught.value.detail["code"] == "approval_expired"
    run, approval = seed(service, metadata={"scopeRevision": 2})
    prior = db.get_pending_approval(approval)["request"]
    changed = {**prior, "scopeRevision": 1, "target": "changed"}
    with db.get_connection() as conn:
        conn.execute("UPDATE pending_approvals SET request_json = ? WHERE id = ?", (json.dumps(changed), approval))
        conn.commit()
    assert db.resolve_pending_approval_if_pending(approval, status="approved", expected_request=prior)["reason"] == "approval_request_changed"
    with pytest.raises(ApprovalDecisionConflict) as caught:
        service.approve(approval)
    assert caught.value.detail["code"] == "approval_scope_changed"
    _, exact = seed(service)
    with pytest.raises(ApprovalDecisionConflict) as caught:
        service.approve(exact, {"approvalId": "wrong-approval"})
    assert caught.value.detail["code"] == "approval_identity_mismatch"


def test_rest_conflict_is_409_and_does_not_claim_success(service, monkeypatch):
    from api.run_control_routes import router as api_router
    owner, _ = kernel(monkeypatch)
    monkeypatch.setattr("erc.command_router.erc_kernel", owner)
    _, approval = seed(service)
    service.reject(approval)
    app = FastAPI()
    app.include_router(api_router)
    with TestClient(app) as client:
        response = client.post(f"/approvals/{approval}/approve", json={"response": {"approved": True}})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approval_decision_conflict"


def test_resume_preflight_rechecks_cancel_after_approval(service):
    run, approval = seed(service)
    decision = service.approve(approval)
    service.cancel_run(run, reason="cancel before scheduling")
    router = RuntimeCommandRouter()
    router.configure(schedule_chat_run=lambda *a, **k: pytest.fail("must not schedule after cancel"))
    result = router._resume_from_approval(decision, {})
    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "run_not_ready_after_approval"
