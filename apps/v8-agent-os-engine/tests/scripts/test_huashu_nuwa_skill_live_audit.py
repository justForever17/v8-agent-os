from __future__ import annotations

from tests.scripts import run_huashu_nuwa_skill_live_audit as audit


def test_spec_auto_approve_keeps_polling_when_run_is_still_waiting(monkeypatch, tmp_path) -> None:
    spec = audit.LiveCaseSpec(case_id="case", title="case", prompt="prompt")
    result = audit.LiveCaseResult(spec=spec, session_id="session-1", run_id="run-1", status="submitted")
    poll_calls = {"count": 0}
    statuses = iter(["waiting_approval", "completed"])

    def fake_poll_case(_engine_url, live_result, *, max_wait):
        poll_calls["count"] += 1
        live_result.status = "completed"
        return live_result

    monkeypatch.setattr(audit, "_poll_case", fake_poll_case)
    monkeypatch.setattr(audit, "_find_pending_spec_stage_targets", lambda _workspace: [])
    monkeypatch.setattr(audit, "_approve_pending_spec_stage_approvals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(audit, "_auto_respond_pending_ask_user", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(audit, "_current_run_status", lambda _run_id: next(statuses))

    completed = audit._poll_case_with_spec_auto_approve(
        "http://127.0.0.1:9530",
        result,
        max_wait=5,
        workspace=tmp_path,
        auto_approve_spec=True,
    )

    assert completed.status == "completed"
    assert poll_calls["count"] == 2
    assert any("runStillActiveAfterPoll" in item for item in completed.key_events)
