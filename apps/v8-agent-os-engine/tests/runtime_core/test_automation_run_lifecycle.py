from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.terminal_post_run import TerminalPostRunService
from erc.run_service import run_service
from erc.workflow_ledger import workflow_ledger_service
from erc.session_realtime_contract import resolve_authoritative_session_runtime_state

spec = importlib.util.spec_from_file_location("lifecycle_fixture", Path(__file__).with_name("test_automation_delivery_recovery.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


@pytest.fixture
def state(tmp_path, monkeypatch):
    return base.install_state(monkeypatch, tmp_path)


def projection(state, run):
    view = workflow_ledger_service.get_session_workflow_view(run["session_id"])
    return resolve_authoritative_session_runtime_state(
        session_id=run["session_id"], workflow_view=view,
        lane_view=base.action.session_admission_service.get_lane_view(run["session_id"]),
        runtime_events=state.db.get_runtime_events(run["session_id"]),
    )


def terminal_hook_attempt(state, run, monkeypatch):
    calls = []
    terminal = TerminalPostRunService()
    monkeypatch.setattr(terminal, "_finalize_workflow_guides", lambda **k: None)
    monkeypatch.setattr(terminal, "_schedule_engineering_proof_if_needed", lambda **k: None)
    monkeypatch.setattr(terminal, "_schedule_memory_extraction", lambda **k: None)
    monkeypatch.setattr(terminal, "_run_non_memory_hooks", lambda **k: calls.append(k) or True)
    terminal.dispatch(session_id=run["session_id"], run_id=run["id"], source_component="fixture")
    return calls


@pytest.mark.parametrize("recovery", ["poll", "engine_boot"])
@pytest.mark.parametrize("crash_point", ["during_effect", "after_worker_terminal"])
def test_crash_lifecycle_four_cells_follow_product_boot_order(state, monkeypatch, recovery, crash_point):
    # A subprocess crosses the actual ActionExecutor boundary, writes one local
    # fixture effect, and exits. No provider, service or user process is used.
    base.hook(state)
    item = base.emit_hook()[0]
    script = '''
import asyncio, os, runpy, sys
from pathlib import Path
import pytest
fixture=runpy.run_path('tests/runtime_core/test_automation_delivery_recovery.py')
mp=pytest.MonkeyPatch()
state=fixture['install_state'](mp,Path(sys.argv[1]))
def effect(*a,**k):
    with open(state.root/'effect.txt','wb') as output:
        output.write(b'one synthetic effect')
        output.flush()
        os.fsync(output.fileno())
    if sys.argv[2]=='during_effect':
        os._exit(73)
    return None
mp.setattr(fixture['action'].ActionExecutor,'_execute_command',effect)
if sys.argv[2]=='after_worker_terminal':
    mp.setattr(state.service,'finished',lambda *a,**k:os._exit(73))
asyncio.run(fixture['drain'](state.service))
raise SystemExit(99)
'''
    process = subprocess.run([sys.executable, "-c", script, str(state.root), crash_point],
        capture_output=True, text=True, timeout=45,
        env=dict(os.environ, V8_AGENT_OS_HOME=str(state.root / "child-state")),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.returncode == 73, process.stderr[-1500:]
    reopened = base.install_state(monkeypatch, state.root)
    base.expire(reopened, item["delivery_id"])

    async def recover():
        if recovery == "engine_boot":
            import main
            # Assert the real startup order and call its actual two recovery
            # entrypoints. We never start the application lifespan/services.
            tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
            calls = sorted((node.lineno, ast.unparse(node.func)) for node in ast.walk(tree) if isinstance(node, ast.Call))
            orphan = next(line for line, func in calls if func == "_reconcile_orphaned_workflows")
            lanes = next(line for line, func in calls if func == "_reconcile_session_lanes")
            cron = next(line for line, func in calls if func == "_get_cron_manager().start")
            assert orphan < lanes < cron
            await main._reconcile_orphaned_workflows()
            await main._reconcile_session_lanes()
        await reopened.service.tick()
        await base.drain(reopened.service)

    asyncio.run(recover())
    run = reopened.db.get_run_record(item["execution_run_id"])
    expected = "completed" if crash_point == "after_worker_terminal" else (
        "interrupted" if recovery == "engine_boot" else "waiting_external_tool")
    assert run["status"] == expected
    assert projection(reopened, run).runtime_status == expected
    if expected == "waiting_external_tool":
        assert run["metadata"]["automationRecovery"]["processTermination"] == "unproven"
        assert run["finished_at"] is None
        assert not terminal_hook_attempt(reopened, run, monkeypatch)
    elif expected == "interrupted":
        assert run["metadata"]["orphaned"] is True
        assert run["finished_at"]
    else:
        assert terminal_hook_attempt(reopened, run, monkeypatch)
    if crash_point == "during_effect":
        reopened.service.reconcile_from_admin(delivery_id=item["delivery_id"], outcome="completed",
            evidence={"observation": "one local fixture effect"}, authenticated_owner="fixture-admin")
        assert reopened.db.get_run_record(run["id"])["status"] == expected
    for _ in range(2):
        asyncio.run(reopened.service.tick())
    assert reopened.effects == []
    assert (state.root / "effect.txt").read_bytes() == b"one synthetic effect"


@pytest.mark.parametrize("race", ["cancel", "worker_completed", "pause"])
def test_conditional_wait_never_overwrites_newer_run_status_or_metadata(state, monkeypatch, race):
    item = base._running_unknown_delivery(state)
    real_transition = state.db.transition_automation_run
    desired = {"cancel": "cancelled", "worker_completed": "completed", "pause": "paused"}[race]
    def concurrent(*args, **kwargs):
        state.db.update_run_record(item["execution_run_id"], status=desired,
            metadata={**state.db.get_run_record(item["execution_run_id"])["metadata"], "concurrentEdit": "retained"})
        return real_transition(*args, **kwargs)
    monkeypatch.setattr(state.db, "transition_automation_run", concurrent)
    assert not state.service._mark_run_waiting_for_external_outcome(item, reason="fixture")
    run = state.db.get_run_record(item["execution_run_id"])
    assert run["status"] == desired
    assert run["metadata"]["concurrentEdit"] == "retained"
    assert "automationRecovery" not in run["metadata"]


def test_wait_metadata_merges_concurrent_fields_without_restoring_old_status(state, monkeypatch):
    item = base._running_unknown_delivery(state)
    real = state.db.transition_automation_run
    def edit_before_marker(*args, **kwargs):
        run = state.db.get_run_record(item["execution_run_id"])
        state.db.update_run_record(run["id"], status=run["status"],
            metadata={**run["metadata"], "concurrentEdit": "retained"})
        return real(*args, **kwargs)
    monkeypatch.setattr(state.db, "transition_automation_run", edit_before_marker)
    state.service._mark_run_waiting_for_external_outcome(item, reason="fixture")
    run = state.db.get_run_record(item["execution_run_id"])
    assert run["metadata"]["concurrentEdit"] == "retained"
    assert run["status"] == "waiting_external_tool"


@pytest.mark.parametrize("drift", ["target", "attempt", "receipt", "run_binding"])
def test_stale_poll_cannot_move_replacement_target_or_attempt(state, monkeypatch, drift):
    old = base._running_unknown_delivery(state)
    state.db.create_run_record("replacement-run", "source-session", run_type="automation", status="running",
        metadata={"action_target": "new-target", "action_type": "command"})
    real = state.db.transition_automation_run
    def replace_at_write(*args, **kwargs):
        with state.db.get_connection() as conn:
            if drift == "target":
                envelope = json.loads(json.dumps(old["envelope"]))
                envelope["target"] = "new-target"
                conn.execute("UPDATE runtime_automation_deliveries SET envelope_json=? WHERE delivery_id=?",
                             (json.dumps(envelope), old["delivery_id"]))
            elif drift == "attempt":
                conn.execute("UPDATE runtime_automation_deliveries SET execution_run_id=? WHERE delivery_id=?",
                             ("replacement-run", old["delivery_id"]))
            elif drift == "receipt":
                conn.execute("UPDATE runtime_automation_deliveries SET receipt_key='replacement-receipt' WHERE delivery_id=?", (old["delivery_id"],))
            else:
                conn.execute("UPDATE run_records SET session_id='source-session' WHERE id=?", (old["execution_run_id"],))
            conn.commit()
        return real(*args, **kwargs)
    monkeypatch.setattr(state.db, "transition_automation_run", replace_at_write)
    assert not state.service._mark_run_waiting_for_external_outcome(old, reason="stale")
    assert state.db.get_run_record("replacement-run")["status"] == "running"
    assert state.db.get_run_record(old["execution_run_id"])["status"] == "running"


@pytest.mark.parametrize("action_type", ["command", "agent"])
@pytest.mark.parametrize("worker_status", ["completed", "failed"])
@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_admin_reconciliation_at_final_write_fences_actual_worker(state, monkeypatch, action_type, worker_status, outcome):
    base.hook(state, type=action_type)
    item = base.emit_hook()[0]
    effects = []
    def external_action():
        effects.append("one")
        if worker_status == "failed":
            raise ValueError("fixture effect error")
        return {}
    if action_type == "command":
        monkeypatch.setattr(base.action.ActionExecutor, "_execute_command", lambda *a, **k: external_action())
    else:
        from types import SimpleNamespace
        async def ainvoke(*a, **k):
            return external_action()
        monkeypatch.setattr(base.action.ActionExecutor, "_load_agent_graph", lambda target: SimpleNamespace(ainvoke=ainvoke))
    real = state.db.transition_automation_run
    injected = []
    def reconcile_at_write(snapshot, **kwargs):
        if kwargs["status"] == worker_status and not injected:
            injected.append(True)
            current = state.db.get_automation_delivery(item["delivery_id"])
            # The final write boundary is after every caller-side read. Use the
            # canonical receipt/delivery reconciliation writes, with the run still
            # running (e.g. an old Admin writer or interruption before liveness repair).
            with state.db.get_connection() as conn:
                conn.execute("UPDATE runtime_automation_deliveries SET phase='unknown' WHERE delivery_id=?", (item["delivery_id"],))
                conn.execute("UPDATE runtime_side_effect_receipts SET state='indeterminate', owner_id=NULL WHERE idempotency_key=?", (current["receipt_key"],))
                conn.commit()
            assert state.db.reconcile_side_effect_receipt(idempotency_key=current["receipt_key"], outcome=outcome, evidence={"fixture": "observed"})
            assert state.db.reconcile_automation_delivery(item["delivery_id"], outcome=outcome, evidence={"fixture": "observed"})
            assert state.db.get_run_record(item["execution_run_id"])["status"] == "running"
        return real(snapshot, **kwargs)
    monkeypatch.setattr(state.db, "transition_automation_run", reconcile_at_write)
    asyncio.run(base.drain(state.service))
    run = state.db.get_run_record(item["execution_run_id"])
    assert injected == [True]
    assert run["status"] == "waiting_external_tool"
    assert run["finished_at"] is None
    assert state.db.get_automation_delivery(item["delivery_id"])["phase"] == outcome
    assert not terminal_hook_attempt(state, run, monkeypatch)
    assert not any(event["topic"] in {"run.completed", "run.failed", "run.cancelled"}
                   for event in state.db.get_runtime_events_for_run(run["id"]))
    asyncio.run(base.drain(state.service))
    assert effects == ["one"]


@pytest.mark.parametrize("action_type", ["command", "agent"])
@pytest.mark.parametrize("race", ["cancel", "manual_reconcile"])
def test_actual_late_worker_cannot_complete_over_cancel_or_operator_receipt(state, monkeypatch, action_type, race):
    base.hook(state, type=action_type)
    item = base.emit_hook()[0]
    effects = []
    def external_action():
        effects.append("one")
        current = state.db.get_automation_delivery(item["delivery_id"])
        if race == "cancel":
            state.db.update_run_record(current["execution_run_id"], status="cancelled")
        else:
            base.expire(state, current["delivery_id"])
            state.db.reconcile_automation_deliveries()
            state.service.reconcile_from_admin(delivery_id=current["delivery_id"], outcome="completed",
                evidence={"observation": "one local effect"}, authenticated_owner="admin")
        return {}
    if action_type == "command":
        monkeypatch.setattr(base.action.ActionExecutor, "_execute_command", lambda *a, **k: external_action())
    else:
        from types import SimpleNamespace
        async def ainvoke(*a, **k):
            return external_action()
        monkeypatch.setattr(base.action.ActionExecutor, "_load_agent_graph", lambda target: SimpleNamespace(ainvoke=ainvoke))
    asyncio.run(base.drain(state.service))
    run = state.db.get_run_record(item["execution_run_id"])
    expected = "cancelled" if race == "cancel" else "waiting_external_tool"
    assert run["status"] == expected
    events = state.db.get_runtime_events_for_run(run["id"])
    assert not any(event["topic"] == "run.completed" for event in events)
    assert effects == ["one"]
    asyncio.run(base.drain(state.service))
    assert effects == ["one"]
    if race == "manual_reconcile":
        assert state.db.get_automation_delivery(item["delivery_id"])["phase"] == "completed"
        assert not terminal_hook_attempt(state, run, monkeypatch)


def test_cancelled_agent_await_preserves_unproven_external_execution(state, monkeypatch):
    from types import SimpleNamespace
    base.hook(state, type="agent")
    item = base.emit_hook()[0]
    async def cancelled(*a, **k):
        raise asyncio.CancelledError()
    monkeypatch.setattr(base.action.ActionExecutor, "_load_agent_graph", lambda target: SimpleNamespace(ainvoke=cancelled))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(base.drain(state.service))
    run = state.db.get_run_record(item["execution_run_id"])
    assert run["status"] == "waiting_external_tool"
    assert run["finished_at"] is None
    assert not terminal_hook_attempt(state, run, monkeypatch)
    asyncio.run(state.service.tick())
    current = state.db.get_automation_delivery(item["delivery_id"])
    assert current["phase"] == "unknown"
    assert state.db.get_side_effect_receipt(current["receipt_key"])["state"] == "indeterminate"


@pytest.mark.parametrize("proof", ["missing", "foreign_completed", "owner_changes_at_write", "state_changes_at_write"])
def test_success_without_owned_receipt_parks_persisted_run(state, monkeypatch, proof):
    from types import SimpleNamespace
    base.hook(state)
    item = base.emit_hook()[0]
    claimed = state.db.claim_automation_delivery(item["delivery_id"], owner_id="fixture-owner")
    envelope = claimed["envelope"]
    kwargs = dict(envelope["kwargs"], automation_delivery_owner="fixture-owner")
    handle = base.action.automation_runtime.begin_or_attach_run(action_type="command", target=envelope["target"],
        payload=envelope["payload"], trigger_source=kwargs["trigger"], is_async=True, kwargs=kwargs)
    state.service.admitted(kwargs)
    state.service.start_running(kwargs, handle, kwargs["trigger"])
    receipt = None
    if proof != "missing":
        receipt = base.action.ActionExecutor._begin_execution_side_effect(run_handle=handle, action_type="command",
            target=envelope["target"], trigger_source=kwargs["trigger"], action_payload=envelope["payload"], kwargs=kwargs)
        assert state.db.complete_side_effect_receipt(idempotency_key=receipt.idempotency_key, owner_id=receipt.owner_id, result={"fixture": "done"})
        if proof == "foreign_completed":
            receipt = SimpleNamespace(owner_id="different-worker")
        else:
            real = state.db.transition_automation_run
            def change_receipt_at_write(snapshot, **options):
                with state.db.get_connection() as conn:
                    if proof == "owner_changes_at_write":
                        conn.execute("UPDATE runtime_side_effect_receipts SET owner_id='different-worker' WHERE idempotency_key=?", (receipt.idempotency_key,))
                    else:
                        conn.execute("UPDATE runtime_side_effect_receipts SET state='indeterminate' WHERE idempotency_key=?", (receipt.idempotency_key,))
                    conn.commit()
                return real(snapshot, **options)
            monkeypatch.setattr(state.db, "transition_automation_run", change_receipt_at_write)
    assert state.service.settle_run(kwargs, handle, status="success", receipt=receipt) == "waiting_external_tool"
    run = state.db.get_run_record(handle.run_id)
    assert run["status"] == "waiting_external_tool"
    assert run["finished_at"] is None
