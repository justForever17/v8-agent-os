from __future__ import annotations

from core.terminal_post_run import TerminalPostRunService
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
            "run_record": {**run_record, "status": "interrupted"},
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
            {"from_status": "running", "to_status": "interrupted", "reason": "manual_interrupt"},
        ),
        ("run.interrupted", {"run_id": "run_live", "reason": "manual_interrupt"}),
    ]
    assert synced == [("run_live", "interrupted")]


def test_interrupted_run_executes_terminal_post_run_cleanup(monkeypatch):
    service = TerminalPostRunService()
    calls: list[str] = []
    monkeypatch.setattr(
        "core.terminal_post_run.db.get_run_record",
        lambda _run_id: {
            "id": "run_interrupted",
            "session_id": "session_interrupted",
            "status": "interrupted",
            "metadata": {},
        },
    )
    monkeypatch.setattr(
        "core.terminal_post_run.run_service.update_metadata",
        lambda *_args, **_kwargs: calls.append("metadata"),
    )
    monkeypatch.setattr(service, "_finalize_workflow_guides", lambda **_kwargs: calls.append("guides"))
    monkeypatch.setattr(
        service,
        "_schedule_engineering_proof_if_needed",
        lambda **_kwargs: calls.append("proof") or False,
    )
    monkeypatch.setattr(service, "_schedule_memory_extraction", lambda **_kwargs: calls.append("memory"))
    monkeypatch.setattr(service, "_run_non_memory_hooks", lambda **_kwargs: calls.append("hooks"))

    assert service.dispatch(
        session_id="session_interrupted",
        run_id="run_interrupted",
        source_component="test",
    ) is True
    assert calls == ["guides", "proof", "metadata", "memory", "hooks"]
