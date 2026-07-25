from __future__ import annotations

from erc.kernel import ExecutionRuntimeCore


def test_late_interrupt_does_not_reopen_completed_run(monkeypatch):
    kernel = ExecutionRuntimeCore()
    emitted: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "erc.kernel.run_service.get_run",
        lambda _run_id: {
            "id": "run_done",
            "session_id": "session_done",
            "status": "completed",
            "run_type": "chat",
        },
    )
    monkeypatch.setattr(
        "erc.kernel.command_service.interrupt_run",
        lambda _run_id, reason=None: {
            "updated": False,
            "reason": "status_mismatch:completed",
            "currentStatus": "completed",
        },
    )
    monkeypatch.setattr(
        kernel,
        "_emitter_for_run",
        lambda *_args, **_kwargs: type(
            "Emitter",
            (),
            {"emit": lambda _self, topic, payload: emitted.append((topic, payload)) or payload},
        )(),
    )
    monkeypatch.setattr(
        "erc.kernel.workflow_ledger_service.sync_run_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("terminal run must not be resynced")),
    )

    result = kernel.interrupt_run("run_done", reason="late_ui_interrupt")

    assert result == {
        "ignored": True,
        "reason": "status_mismatch:completed",
        "run_status": "completed",
        "run_id": "run_done",
    }
    assert emitted == []


def test_interrupt_emits_events_only_after_atomic_transition(monkeypatch):
    kernel = ExecutionRuntimeCore()
    emitted: list[tuple[str, dict]] = []
    synced: list[tuple[str, str]] = []

    run_record = {
        "id": "run_live",
        "session_id": "session_live",
        "status": "running",
        "run_type": "chat",
    }
    monkeypatch.setattr("erc.kernel.run_service.get_run", lambda _run_id: run_record)
    monkeypatch.setattr(
        "erc.kernel.command_service.interrupt_run",
        lambda _run_id, reason=None: {
            "updated": True,
            "previousStatus": "running",
            "run_record": {**run_record, "status": "paused"},
        },
    )
    monkeypatch.setattr(
        kernel,
        "_emitter_for_run",
        lambda *_args, **_kwargs: type(
            "Emitter",
            (),
            {"emit": lambda _self, topic, payload: emitted.append((topic, payload)) or {"topic": topic}},
        )(),
    )
    monkeypatch.setattr(
        "erc.kernel.workflow_ledger_service.sync_run_status",
        lambda run_id, *, run_status, reason=None: synced.append((run_id, run_status)),
    )

    result = kernel.interrupt_run("run_live", reason="manual_interrupt")

    assert result == {
        "transition_event": {"topic": "run.state.changed"},
        "command_event": {"topic": "run.interrupted"},
    }
    assert emitted == [
        (
            "run.state.changed",
            {"from_status": "running", "to_status": "paused", "reason": "manual_interrupt"},
        ),
        ("run.interrupted", {"run_id": "run_live", "reason": "manual_interrupt"}),
    ]
    assert synced == [("run_live", "interrupted")]
