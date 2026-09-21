import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core.spec_service import spec_service
from core.tools.native.spec import _request_spec_stage_approval
from erc.command_router import RuntimeCommandRouter
from erc.models import RuntimeCommand
from tests.runtime_core.test_approval_resume_delivery import database as delivery_database


@pytest.fixture
def database(delivery_database, monkeypatch):
    import erc.snapshot_service as snapshots
    monkeypatch.setattr(snapshots, "db", delivery_database)
    return delivery_database


def review_card(database, tmp_path):
    created = spec_service.create_stage(workspace_path=str(tmp_path), user_request="Implement the reviewed requirement",
        feature_name="Versioned approval", stage="requirements", kind="feature")
    assert created["ok"]
    spec_id = created["specId"]
    card = _request_spec_stage_approval(session_id="session", run_id="run", spec_id=spec_id, stage="requirements",
        workspace_path=str(tmp_path), summary="Review the requirements", pipeline=created["pipelineControl"], spec_brief=created["specBrief"])
    path = spec_service.resolve_paths(str(tmp_path), spec_id=spec_id).spec_dir / "requirements.md"
    return card, path, spec_id


def router_for_review(monkeypatch, tmp_path, spec_id):
    router = RuntimeCommandRouter()
    scheduled = []
    monkeypatch.setattr(router, "_scope_payload_for_session", lambda session: {"workspace_path": str(tmp_path)})
    monkeypatch.setattr(router, "_build_manual_resume_chat_request", lambda *args, **kwargs: SimpleNamespace(
        resume_value={"specContinuation": {"specId": spec_id, "stage": "requirements"}}))
    router.configure(schedule_chat_run=lambda request, **kwargs: scheduled.append(request) or "run")
    return router, scheduled


def test_first_approval_rejects_document_changed_since_card_creation(database, tmp_path, monkeypatch):
    card, path, spec_id = review_card(database, tmp_path)
    original = path.read_bytes()
    path.write_bytes(original + b"\nAn unreviewed requirement.\n")
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    result = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"], response={"decision": "approved"}))
    assert not result.get("decisionApplied") and not result.get("resume_scheduled"), result
    assert result["spec_stage_approval"]["kind"] == "spec_approval_document_changed"
    assert not scheduled
    assert not spec_service.build_brief(workspace_path=str(tmp_path), spec_id=spec_id)["approvalState"].get("requirements")


def test_card_binds_actual_document_bytes_at_creation(database, tmp_path):
    card, path, spec_id = review_card(database, tmp_path)
    request = database.get_pending_approval(card["approval_id"])["request"]
    assert request["documentSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert request["documentPath"].endswith("/requirements.md")


@pytest.mark.parametrize("scheduler_fails", [False, True])
def test_durable_approval_returns_document_apply_receipt_independent_of_scheduling(
    database, tmp_path, monkeypatch, scheduler_fails,
):
    card, path, spec_id = review_card(database, tmp_path)
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    if scheduler_fails:
        def fail_schedule(*_args, **_kwargs):
            raise RuntimeError("synthetic scheduler failure")
        router.configure(schedule_chat_run=fail_schedule)
    result = router.dispatch_approval_command(RuntimeCommand(
        topic="approval.approve", approval_id=card["approval_id"],
        response={"documentSha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    ))
    assert result["approvalDeliveryRecorded"] is True
    assert result["decisionApplied"] is True
    assert result["approval"]["status"] == "approved"
    assert result["spec_stage_approval"]["ok"] is True
    assert result["spec_stage_approval"]["specId"] == spec_id
    assert result["spec_stage_approval"]["stage"] == "requirements"
    assert spec_service.build_brief(workspace_path=str(tmp_path), spec_id=spec_id)["approvalState"]["requirements"] is True
    assert result["resume_scheduled"] is not scheduler_fails
    if scheduler_fails:
        assert result["resume_error"] == "approval_resume_scheduler_failed:RuntimeError"
        assert not scheduled
    else:
        assert len(scheduled) == 1


def test_missing_version_card_requires_refresh_and_repeated_refresh_reuses_identity(database, tmp_path, monkeypatch):
    card, path, spec_id = review_card(database, tmp_path)
    old_id = card["approval_id"]
    with database.get_connection() as conn:
        conn.execute("UPDATE pending_approvals SET request_json=json_remove(request_json,'$.documentSha256') WHERE id=?", (old_id,))
        conn.commit()
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    blocked = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=old_id))
    assert blocked["spec_stage_approval"]["kind"] == "spec_approval_version_required"
    assert not scheduled
    current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    refreshed = router.refresh_spec_review(old_id, document_sha256=current_hash)["approval"]
    repeated = router.refresh_spec_review(old_id, document_sha256=current_hash)["approval"]
    assert refreshed["id"] == repeated["id"] != old_id
    assert database.get_pending_approval(old_id)["status"] == "cancelled"
    assert len(database.list_pending_approvals(run_id="run", status="pending")) == 1
    accepted = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=refreshed["id"], response={"documentSha256": current_hash}))
    assert accepted["decisionApplied"] and accepted["resume_scheduled"]


def test_document_restored_to_reviewed_bytes_matches_old_card_without_rebinding(database, tmp_path, monkeypatch):
    card, path, spec_id = review_card(database, tmp_path)
    original = path.read_bytes()
    path.write_bytes(original + b"\nTransient unreviewed content\n")
    path.write_bytes(original)
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    accepted = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"]))
    assert accepted["decisionApplied"] and len(scheduled) == 1
    assert accepted["approval"]["request"]["documentSha256"] == hashlib.sha256(original).hexdigest()


def test_replacement_does_not_resurrect_cancelled_card_when_content_changes_back(database, tmp_path, monkeypatch):
    from erc.command_service import ApprovalDecisionConflict
    card, path, spec_id = review_card(database, tmp_path)
    original = path.read_bytes()
    router, _ = router_for_review(monkeypatch, tmp_path, spec_id)
    path.write_bytes(original + b"\nNew requirement\n")
    new = router.refresh_spec_review(card["approval_id"], document_sha256=hashlib.sha256(path.read_bytes()).hexdigest())["approval"]
    path.write_bytes(original)
    with pytest.raises(ApprovalDecisionConflict):
        router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"]))
    newest = router.refresh_spec_review(new["id"], document_sha256=hashlib.sha256(original).hexdigest())["approval"]
    assert newest["id"] not in {card["approval_id"], new["id"]}
    assert database.get_pending_approval(card["approval_id"])["status"] == "cancelled"
    assert router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=newest["id"]))["decisionApplied"]


def test_first_application_rechecks_after_decision_cas_and_new_card_remains_reachable(database, tmp_path, monkeypatch):
    from erc.kernel import erc_kernel
    card, path, spec_id = review_card(database, tmp_path)
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    original_approve = erc_kernel.approve
    def change_after_decision(*args, **kwargs):
        value = original_approve(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b"\nChanged after decision\n")
        return value
    monkeypatch.setattr(erc_kernel, "approve", change_after_decision)
    blocked = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"]))
    assert blocked["decisionApplied"] and not blocked["resume_scheduled"] and not scheduled
    assert blocked["spec_stage_approval"]["kind"] == "spec_approval_document_changed"
    assert database.get_run_record("run")["status"] == "waiting_input"
    monkeypatch.setattr(erc_kernel, "approve", original_approve)
    fresh = router.refresh_spec_review(card["approval_id"], document_sha256=hashlib.sha256(path.read_bytes()).hexdigest())["approval"]
    accepted = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=fresh["id"]))
    assert accepted["decisionApplied"] and accepted["resume_scheduled"]
    from runtimes.chat.runtime import ChatRuntime
    active = SimpleNamespace(active_run_id="run", request=scheduled[-1], transport="system_resume")
    assert ChatRuntime._consume_approval_delivery(active)
    ChatRuntime._finish_approval_delivery(active)
    assert not database.approval_resume_state_for_run("run")["requiresDelivery"]
    old = database.get_pending_approval(card["approval_id"])
    assert old["status"] == "approved" and json.loads(old["resume_json"])["state"] == "superseded"


def test_save_and_approve_binds_new_identity_once_to_explicit_displayed_hash(database, tmp_path, monkeypatch):
    card, path, spec_id = review_card(database, tmp_path)
    old_request = database.get_pending_approval(card["approval_id"])["request"]
    path.write_bytes(path.read_bytes() + b"\nThe human explicitly reviewed this edit.\n")
    displayed = hashlib.sha256(path.read_bytes()).hexdigest()
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    request = RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"],
        response={"replaceSpecReview": True, "documentSha256": displayed})
    first = router.dispatch_approval_command(request)
    second = router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"],
        response={"replaceSpecReview": True, "documentSha256": displayed}))
    assert first["decisionApplied"] and first["resume_scheduled"]
    assert first["approval"]["id"] == second["approval"]["id"] != card["approval_id"]
    assert second["ignored"] and not second["resume_scheduled"] and len(scheduled) == 1
    assert database.get_pending_approval(card["approval_id"])["request"] == old_request


def test_read_preserves_bytes_and_stale_edit_cannot_overwrite_new_document(database, tmp_path):
    from core.spec_service import SpecApprovalVersionConflict
    card, path, spec_id = review_card(database, tmp_path)
    original = b"\xef\xbb\xbf  # Review\r\n\r\nContent with trailing space.  \r\n"
    path.write_bytes(original)
    stage = spec_service.read_spec(workspace_path=str(tmp_path), spec_id=spec_id)["stages"]["requirements"]
    assert stage["content"].encode("utf-8") == original
    assert stage["documentSha256"] == hashlib.sha256(original).hexdigest() and stage["truncated"] is False
    changed = original + b"New content\r\n"
    path.write_bytes(changed)
    with pytest.raises(SpecApprovalVersionConflict):
        spec_service.edit_stage(workspace_path=str(tmp_path), spec_id=spec_id, stage="requirements", action="rewrite_stage",
            content="Would overwrite the new version", expected_document_sha256=stage["documentSha256"])
    assert path.read_bytes() == changed


def test_edit_retains_exact_reviewed_bytes_in_version_history(database, tmp_path):
    _, path, spec_id = review_card(database, tmp_path)
    original = b"\xef\xbb\xbf# Reviewed stage\r\n\r\nPreserve trailing whitespace.  \r\n"
    path.write_bytes(original)
    saved = spec_service.edit_stage(workspace_path=str(tmp_path), spec_id=spec_id, stage="requirements", action="rewrite_stage",
        content="# Human revised stage\n", expected_document_sha256=hashlib.sha256(original).hexdigest())
    assert saved["ok"]
    paths = spec_service.resolve_paths(str(tmp_path), spec_id=spec_id)
    version = json.loads(paths.manifest.read_text(encoding="utf-8"))["versionHistory"][-1]
    assert (tmp_path / version["relativePath"]).read_bytes() == original
    assert version["sha256"] == hashlib.sha256(original).hexdigest()


def test_edit_keeps_more_than_twenty_thousand_characters_and_returns_actual_snapshot(database, tmp_path):
    _, path, spec_id = review_card(database, tmp_path)
    original = path.read_text(encoding="utf-8")
    content = original + "\n" + "A" * 24000 + " END_OF_DOCUMENT\n"
    saved = spec_service.edit_stage(workspace_path=str(tmp_path), spec_id=spec_id, stage="requirements", action="rewrite_stage",
        content=content, expected_document_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert saved["ok"] and "END_OF_DOCUMENT" in path.read_text(encoding="utf-8")
    assert saved["content"].encode("utf-8") == path.read_bytes()
    assert saved["documentSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert saved["documentPath"].endswith("requirements.md")


def test_large_spec_can_be_read_edited_and_approved_in_full(database, tmp_path):
    _, path, spec_id = review_card(database, tmp_path)
    path.write_bytes(path.read_bytes() + b"\n" + b"Large legitimate requirement. " * 10000 + b"FINAL_REVIEW_MARKER\n")
    partial = spec_service.read_spec(workspace_path=str(tmp_path), spec_id=spec_id)["stages"]["requirements"]
    full = spec_service.read_spec(workspace_path=str(tmp_path), spec_id=spec_id, full_content=True)["stages"]["requirements"]
    assert partial["truncated"] is True and full["truncated"] is False
    assert len(full["content"]) > 200000 and full["content"].encode("utf-8") == path.read_bytes()
    saved = spec_service.edit_stage(workspace_path=str(tmp_path), spec_id=spec_id, stage="requirements", action="rewrite_stage",
        content=full["content"] + "HUMAN_REVIEWED_EDIT\n", expected_document_sha256=full["documentSha256"])
    assert saved["ok"] and "FINAL_REVIEW_MARKER" in saved["content"] and "HUMAN_REVIEWED_EDIT" in saved["content"]
    assert saved["documentSha256"] == hashlib.sha256(saved["content"].encode("utf-8")).hexdigest()
    approved = spec_service.approve_stage(workspace_path=str(tmp_path), spec_id=spec_id, stage="requirements",
        expected_document_sha256=saved["documentSha256"])
    assert approved["ok"]


def test_review_refresh_cannot_revoke_an_executing_delivery(database, tmp_path, monkeypatch):
    from runtimes.chat.runtime import ChatRuntime
    card, path, spec_id = review_card(database, tmp_path)
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=card["approval_id"]))
    active = SimpleNamespace(active_run_id="run", request=scheduled[-1], transport="system_resume")
    assert ChatRuntime._consume_approval_delivery(active)
    before = database.get_pending_approval(card["approval_id"])
    path.write_bytes(path.read_bytes() + b"\nNew version while the prior continuation is running\n")
    with pytest.raises(ValueError, match="spec_review_delivery_in_progress"):
        router.refresh_spec_review(card["approval_id"], document_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert database.get_pending_approval(card["approval_id"]) == before
    assert database.list_pending_approvals(run_id="run", status="pending") == []


def test_refresh_retry_after_concurrent_native_card_and_approval_keeps_same_receipt(database, tmp_path, monkeypatch):
    from erc.command_service import command_service
    from erc.models import ApprovalRequest
    old, path, spec_id = review_card(database, tmp_path)
    path.write_bytes(path.read_bytes() + b"\nThe newly displayed version\n")
    payload = dict(database.get_pending_approval(old["approval_id"])["request"])
    payload.pop("documentSha256")
    native = command_service.request_approval(ApprovalRequest(approval_id="native-new-card", session_id="session",
        run_id="run", approval_kind="spec_stage_approval", request=payload))
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    displayed = hashlib.sha256(path.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: router.refresh_spec_review(old["approval_id"], document_sha256=displayed), range(3)))
    new_id = results[0]["approval"]["id"]
    assert {value["approval"]["id"] for value in results} == {new_id}
    assert database.get_pending_approval(native["approval_id"])["status"] == "cancelled"
    router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=new_id))
    receipt = database.get_pending_approval(new_id)
    repeated = router.refresh_spec_review(old["approval_id"], document_sha256=displayed)
    assert repeated["approval"]["id"] == new_id and repeated["approval"]["status"] == "approved"
    assert database.get_pending_approval(new_id) == receipt and len(scheduled) == 1


@pytest.mark.parametrize("control", ["cancel", "pause", "interrupt"])
def test_refresh_retry_does_not_override_current_control(database, tmp_path, monkeypatch, control):
    card, path, spec_id = review_card(database, tmp_path)
    router, _ = router_for_review(monkeypatch, tmp_path, spec_id)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    refreshed = router.refresh_spec_review(card["approval_id"], document_sha256=digest)["approval"]
    with database.get_connection() as conn:
        conn.execute("UPDATE run_records SET metadata=? WHERE id='run'", (json.dumps({"control_signal": {"command": control}}),))
        conn.commit()
    with pytest.raises(ValueError, match="spec_review_run_not_available"):
        router.refresh_spec_review(card["approval_id"], document_sha256=digest)
    assert database.get_pending_approval(refreshed["id"])["status"] == "pending"
    assert database.get_run_record("run")["metadata"]["control_signal"]["command"] == control


def test_http_conflicts_and_saved_document_snapshot(database, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.run_control_routes as controls
    import api.spec_routes as specs
    card, path, spec_id = review_card(database, tmp_path)
    router, scheduled = router_for_review(monkeypatch, tmp_path, spec_id)
    monkeypatch.setattr(controls, "runtime_command_router", router)
    monkeypatch.setattr(controls, "db", database)
    app = FastAPI()
    app.include_router(controls.router)
    app.include_router(specs.router)
    with TestClient(app) as client:
        prefix = f"/specs/{spec_id}/stages/requirements"
        base = {"workspacePath": str(tmp_path)}
        missing = client.post(prefix + "/approve", json=base)
        assert missing.status_code == 409 and missing.json()["detail"]["code"] == "spec_approval_version_required"
        path.write_bytes(path.read_bytes() + b"\n" + b"Large approved requirement. " * 10000 + b"FINAL_HTTP_REVIEW_MARKER\n")
        shown = client.get(f"/specs/{spec_id}", params={"workspace_path": str(tmp_path), "full_content": True}).json()["stages"]["requirements"]
        assert shown["truncated"] is False and len(shown["content"]) > 200000
        assert "FINAL_HTTP_REVIEW_MARKER" in shown["content"]
        path.write_bytes(path.read_bytes() + b"\nThe file changed after viewing\n")
        stale = client.post(prefix + "/approve", json={**base, "documentSha256": shown["documentSha256"]})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "spec_approval_document_changed"
        old_approve = client.post(f"/approvals/{card['approval_id']}/approve", json={"response": {"documentSha256": shown["documentSha256"]}})
        assert old_approve.status_code == 409 and old_approve.json()["detail"]["code"] == "spec_approval_document_changed"
        shown = client.get(f"/specs/{spec_id}", params={"workspace_path": str(tmp_path), "full_content": True}).json()["stages"]["requirements"]
        saved = client.post(prefix + "/edit", json={**base, "action": "rewrite_stage", "content": shown["content"] + "\nApproved human edit\n",
            "expectedDocumentSha256": shown["documentSha256"]})
        assert saved.status_code == 200 and saved.json()["ok"] is True
        digest = saved.json()["documentSha256"]
        assert digest == hashlib.sha256(saved.json()["content"].encode("utf-8")).hexdigest()
        stale_edit = client.post(prefix + "/edit", json={**base, "action": "rewrite_stage", "content": "lost update", "expectedDocumentSha256": shown["documentSha256"]})
        assert stale_edit.status_code == 409
        refreshed = client.post(f"/approvals/{card['approval_id']}/refresh-spec-review", json={"response": {"documentSha256": digest}})
        assert refreshed.status_code == 200 and refreshed.json()["approval"]["status"] == "pending"
        accepted = client.post(f"/approvals/{card['approval_id']}/approve", json={"response": {"documentSha256": digest, "replaceSpecReview": True}})
        assert accepted.status_code == 200 and accepted.json()["decisionApplied"] is True
        assert accepted.json()["approval"]["id"] == refreshed.json()["approval"]["id"]
        assert len(scheduled) == 1
