from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import action_executor as action
from core import cron_manager as cron_module
from core import hooks_manager as hooks_module
from core import storage as storage_module
from core.automation import delivery as delivery_module
from core.database import DatabaseManager
from core.terminal_post_run import TerminalPostRunService


def install_state(monkeypatch, root):
    state_db = DatabaseManager(root / "state.db")
    old_db = importlib.import_module("core.database").db
    old_storage = storage_module.storage
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", root / "config.json")
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = root
    manager._config_io_lock = storage_module._CONFIG_IO_LOCK
    manager._mcp_io_lock = storage_module._MCP_IO_LOCK
    manager._legacy_model_bindings_migrated = False
    manager._config_payload_cache_signature = None
    manager._config_payload_cache_data = None
    # Replace the canonical dependency at module import boundaries, leaving real
    # run, admission, event, receipt and Storage methods in use.
    for name, module in list(sys.modules.items()):
        if module is not None and name.startswith(("core.", "erc.", "runtimes.")):
            if getattr(module, "db", None) is old_db:
                monkeypatch.setattr(module, "db", state_db)
            if getattr(module, "storage", None) is old_storage:
                monkeypatch.setattr(module, "storage", manager)
    service = delivery_module.AutomationDeliveryService()
    for module in (delivery_module, action, hooks_module, cron_module):
        monkeypatch.setattr(module, "automation_delivery_service", service)
    monkeypatch.setattr(action.automation_runtime, "run_preflight", lambda **k: None)
    monkeypatch.setattr(action.automation_runtime, "handle_preflight_decision", lambda **k: None)
    monkeypatch.setattr(action.automation_runtime, "handle_action_decision", lambda **k: None)
    monkeypatch.setattr(action.safety_guardian, "assess_automation_action", lambda **k: object())
    monkeypatch.setattr(action.automation_runtime, "observe_post_action", lambda **k: None)
    # No provider or user action is performed by these fault tests.
    monkeypatch.setattr(action.ActionExecutor, "_consume_automation_control", lambda **k: None)
    effects = []
    def effect(target, payload, **kwargs):
        item = state_db.get_automation_delivery(kwargs["automation_delivery_id"])
        assert item["phase"] == "executing"
        assert item["admitted_at"]
        assert state_db.get_side_effect_receipt(item["receipt_key"])["state"] == "claimed"
        effects.append(target)
        return subprocess.CompletedProcess(target, 0, "fixture", "")
    monkeypatch.setattr(action.ActionExecutor, "_execute_command", effect)
    action.ActionExecutor._active_targets.clear()
    state_db.create_or_update_session("source-session", "Fixture source", user_id="user-a")
    return SimpleNamespace(db=state_db, storage=manager, service=service, effects=effects, root=root)


@pytest.fixture
def state(tmp_path, monkeypatch):
    return install_state(monkeypatch, tmp_path)


def hook(state, **overrides):
    definition = {"id": "hook-a", "name": "Audit", "events": ["on_chat_end"], "type": "command",
                  "target": "fixture-effect", "enabled": True, "async": True, **overrides}
    state.storage.save_hooks_config({"hooks": [definition]})
    return state.storage.get_hooks_config()["hooks"][0]


def emit_hook(*, event="on_chat_end", occurrence="event-1", **kwargs):
    return hooks_module.hooks_manager.execute_hook(
        event, occurrence_id=occurrence, session_id="source-session", user_id="user-a", **kwargs)


async def drain(service):
    service.kick()
    while service._active:
        tasks = [entry[0] for entry in list(service._active.values())]
        await asyncio.gather(*tasks)


def expire(state, delivery_id):
    with state.db.get_connection() as conn:
        conn.execute("UPDATE runtime_automation_deliveries SET lease_expires_at='2000-01-01', available_at='2000-01-01' WHERE delivery_id=?", (delivery_id,))
        conn.commit()


def test_source_and_fanout_commit_atomically(state):
    hook(state)
    rows = emit_hook()
    assert rows[0]["phase"] == "pending"
    assert rows[0]["admitted_at"] is None
    assert state.effects == []
    assert state.db.get_run_record(rows[0]["execution_run_id"]) is None
    with state.db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_events WHERE topic='automation.source.received'").fetchone()[0] == 1
    # The source is immutable: a later definition revision cannot append a new fan-out.
    hook(state, target="changed")
    repeated = emit_hook()
    assert [row["delivery_id"] for row in repeated] == [rows[0]["delivery_id"]]
    assert repeated[0]["envelope"]["target"] == "fixture-effect"


def test_source_transaction_rolls_back_when_delivery_insert_fails(state):
    hook(state)
    entry = emit_hook()[0]
    malformed = {**entry, "delivery_id": "collision"}
    event = {"event_id": "atomic-failure", "session_id": "source-session", "topic": "automation.source.received"}
    with pytest.raises(sqlite3.IntegrityError):
        state.db.enqueue_automation_deliveries(source_event=event, deliveries=[malformed, malformed])
    with state.db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_events WHERE id='atomic-failure'").fetchone()[0] == 0


def test_existing_in_progress_receipt_is_not_reported_as_completed(state):
    hook(state)
    row = emit_hook()[0]
    item = state.db.claim_automation_delivery(row["delivery_id"], owner_id="owner")
    envelope = item["envelope"]
    kwargs = dict(envelope["kwargs"], automation_delivery_owner="owner")
    handle = action.automation_runtime.begin_or_attach_run(action_type="command", target=envelope["target"],
        payload=envelope["payload"], trigger_source=kwargs["trigger"], is_async=True, kwargs=kwargs)
    action.ActionExecutor._begin_execution_side_effect(run_handle=handle, action_type="command", target=envelope["target"],
        trigger_source=kwargs["trigger"], action_payload=envelope["payload"], kwargs=kwargs)
    result = action.ActionExecutor._execute_sync("command", envelope["target"], envelope["payload"], kwargs)
    assert result["status"] == "review_required"
    assert not state.effects
    assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "blocked"


def test_completed_effect_with_failed_observation_is_not_replayed(state, monkeypatch):
    hook(state)
    row = emit_hook()[0]
    def observation_failed(**kwargs):
        raise RuntimeError("fixture observer unavailable")
    monkeypatch.setattr(action.automation_runtime, "observe_post_action", observation_failed)
    asyncio.run(drain(state.service))
    item = state.db.get_automation_delivery(row["delivery_id"])
    assert item["phase"] == "completed"
    assert state.db.get_side_effect_receipt(item["receipt_key"])["state"] == "completed"
    assert state.effects == ["fixture-effect"]
    asyncio.run(drain(state.service))
    assert state.effects == ["fixture-effect"]


def test_pending_delivery_survives_database_reopen_and_executes_once(state, monkeypatch):
    hook(state)
    row = emit_hook()[0]
    # Recreate the process-owned services and DB connection against the same disk.
    reopened = install_state(monkeypatch, state.root)
    asyncio.run(drain(reopened.service))
    result = reopened.db.get_automation_delivery(row["delivery_id"])
    assert result["phase"] == "completed"
    assert len(reopened.effects) == 1
    assert reopened.db.get_run_record(result["execution_run_id"])["status"] == "completed"
    assert reopened.db.get_side_effect_receipt(result["receipt_key"])["state"] == "completed"
    emit_hook()
    asyncio.run(drain(reopened.service))
    assert len(reopened.effects) == 1


def test_concurrent_database_claims_have_one_owner_and_fence_the_loser(state):
    from concurrent.futures import ThreadPoolExecutor
    hook(state)
    row = emit_hook()[0]
    managers = [DatabaseManager(state.root / "state.db") for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda pair: pair[1].claim_automation_delivery(row["delivery_id"], owner_id=f"owner-{pair[0]}"), enumerate(managers)))
    winners = [item for item in claims if item]
    assert len(winners) == 1
    assert not state.db.transition_automation_delivery(row["delivery_id"], owner_id="stale-owner", expected_phases=("claimed",), phase="admitted")
    assert state.db.get_automation_delivery(row["delivery_id"])["admitted_at"] is None


def test_recovery_fences_late_worker_with_a_distinct_run_identity(state):
    hook(state)
    row = emit_hook()[0]
    old = state.db.claim_automation_delivery(row["delivery_id"], owner_id="old")
    old_kwargs = dict(old["envelope"]["kwargs"], automation_delivery_owner="old")
    old_handle = action.automation_runtime.begin_or_attach_run(action_type="command", target="fixture-effect",
        payload={}, trigger_source="hook:on_chat_end", is_async=True, kwargs=old_kwargs)
    expire(state, row["delivery_id"])
    state.db.reconcile_automation_deliveries()
    new = state.db.claim_automation_delivery(row["delivery_id"], owner_id="new")
    assert new["execution_run_id"] != old["execution_run_id"]
    new_kwargs = dict(new["envelope"]["kwargs"], automation_delivery_owner="new")
    new_handle = action.automation_runtime.begin_or_attach_run(action_type="command", target="fixture-effect",
        payload={}, trigger_source="hook:on_chat_end", is_async=True, kwargs=new_kwargs)
    with pytest.raises(ValueError, match="binding_conflict"):
        action.ActionExecutor._execute_sync("command", "fixture-effect", {}, old_kwargs)
    assert state.db.get_run_record(new_handle.run_id)["status"] == "queued"
    assert state.db.get_automation_delivery(row["delivery_id"])["owner_id"] == "new"
    assert not state.effects


def test_duplicate_occurrence_is_scoped_but_new_occurrences_run(state):
    hook(state)
    for occurrence, project in [("one", "a"), ("one", "a"), ("two", "a"), ("one", "b")]:
        emit_hook(occurrence=occurrence, project_id=project)
    assert len(state.db.list_automation_deliveries()) == 3
    asyncio.run(drain(state.service))
    assert len(state.effects) == 3


def test_tool_events_without_occurrence_are_distinct_and_async_self_excitation_stops(state, monkeypatch):
    from erc.runtime_context import bind_runtime_context
    hook(state, events=["*"])
    with bind_runtime_context(session_id="source-session", run_id="source-run"):
        hooks_module.hooks_manager.execute_hook("on_tool_execute_start", tool="read")
        hooks_module.hooks_manager.execute_hook("on_tool_execute_start", tool="read")
    assert len(state.db.list_automation_deliveries()) == 2
    effect = action.ActionExecutor._execute_command
    def nested(target, payload, **kwargs):
        result = effect(target, payload, **kwargs)
        hooks_module.hooks_manager.execute_hook("on_tool_execute_start", tool="nested")
        return result
    monkeypatch.setattr(action.ActionExecutor, "_execute_command", nested)
    asyncio.run(drain(state.service))
    # The existing session creator can race before canonical admission. Its
    # rejected pre-effect attempt must remain pending and safely retry.
    for pending in state.db.list_automation_deliveries():
        expire(state, pending["delivery_id"])
    asyncio.run(drain(state.service))
    assert len(state.effects) == 2
    assert len(state.db.list_automation_deliveries(phases=("completed",))) == 2


@pytest.mark.parametrize("status", ["paused", "deleted", "disabled"])
def test_paused_and_cross_project_hook_events_do_not_create_deliveries(state, status):
    hook(state, status=status)
    emit_hook()
    hook(state, project_id="project-a")
    emit_hook(occurrence="another", project_id="project-b")
    assert state.db.list_automation_deliveries() == []


@pytest.mark.parametrize("point", ["before_run", "after_run", "after_admission"])
def test_pre_effect_crash_claim_recovers_without_losing_occurrence(state, monkeypatch, point):
    hook(state)
    row = emit_hook()[0]
    claimed = state.db.claim_automation_delivery(row["delivery_id"], owner_id="dead-owner")
    kwargs = dict(claimed["envelope"]["kwargs"], automation_delivery_owner="dead-owner")
    if point != "before_run":
        handle = action.automation_runtime.begin_or_attach_run(
            action_type="command", target="fixture-effect", payload={}, trigger_source="hook:on_chat_end",
            is_async=True, kwargs=kwargs)
        if point == "after_admission":
            action.session_admission_service.acquire(handle.session_id, handle.run_id, policy="queue", runtime_kind="automation")
            state.service.admitted(kwargs)
            action.session_admission_service.release(handle.session_id, handle.run_id)
        state.db.update_run_record(handle.run_id, status="interrupted")
    expire(state, row["delivery_id"])
    reopened = install_state(monkeypatch, state.root)
    reopened.db.reconcile_automation_deliveries()
    assert reopened.db.get_automation_delivery(row["delivery_id"])["phase"] == "pending"
    asyncio.run(drain(reopened.service))
    assert reopened.db.get_automation_delivery(row["delivery_id"])["phase"] == "completed"
    assert reopened.effects == ["fixture-effect"]


def test_failure_after_begin_returns_to_pending_instead_of_acking(state, monkeypatch):
    hook(state)
    row = emit_hook()[0]
    real = action.automation_runtime.begin_or_attach_run
    def interrupted(**kwargs):
        real(**kwargs)
        raise RuntimeError("crash after run persistence")
    monkeypatch.setattr(action.automation_runtime, "begin_or_attach_run", interrupted)
    asyncio.run(drain(state.service))
    item = state.db.get_automation_delivery(row["delivery_id"])
    assert item["phase"] == "pending"
    assert item["admitted_at"] is None
    assert not state.effects
    monkeypatch.setattr(action.automation_runtime, "begin_or_attach_run", real)
    expire(state, item["delivery_id"])
    asyncio.run(drain(state.service))
    assert state.effects == ["fixture-effect"]


@pytest.mark.parametrize("change", ["pause", "remove", "edit"])
def test_revision_change_cancels_queued_occurrence_and_resume_does_not_revive_it(state, change):
    definition = hook(state)
    row = emit_hook()[0]
    if change == "pause":
        state.storage.save_hooks_config({"hooks": [{**definition, "enabled": False}]})
    elif change == "remove":
        state.storage.save_hooks_config({"hooks": []})
    else:
        state.storage.save_hooks_config({"hooks": [{**definition, "target": "new-effect"}]})
    asyncio.run(drain(state.service))
    assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "cancelled"
    hook(state)
    emit_hook()
    asyncio.run(drain(state.service))
    assert not state.effects


def test_explicit_cancellation_before_admission_never_replays(state):
    hook(state)
    row = emit_hook()[0]
    state.db.cancel_automation_delivery(row["delivery_id"], reason="user_cancelled")
    emit_hook()
    asyncio.run(drain(state.service))
    assert not state.effects
    assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "cancelled"


def test_source_event_before_source_run_binds_later_without_duplicate(state):
    hook(state)
    first = emit_hook(parent_run_id="late-source-run")[0]
    state.db.create_run_record("late-source-run", "source-session", run_type="chat", status="completed")
    duplicate = emit_hook(parent_run_id="late-source-run")[0]
    assert first["delivery_id"] == duplicate["delivery_id"]
    with state.db.get_connection() as conn:
        assert conn.execute("SELECT run_id FROM runtime_events WHERE id=?", (first["source_event_id"],)).fetchone()[0] == "late-source-run"
    asyncio.run(drain(state.service))
    assert state.effects == ["fixture-effect"]


def test_cancellation_after_admission_prevents_the_effect(state, monkeypatch):
    hook(state)
    row = emit_hook()[0]
    admitted = state.service.admitted
    def cancel_after_admission(kwargs):
        admitted(kwargs)
        state.db.update_run_record(kwargs["run_id"], status="cancelled")
    monkeypatch.setattr(state.service, "admitted", cancel_after_admission)
    async def run():
        state.service.kick()
        await asyncio.gather(*(task for task, owner in list(state.service._active.values())), return_exceptions=True)
    asyncio.run(run())
    assert not state.effects
    assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "cancelled"


def test_pause_after_admission_prevents_the_effect(state, monkeypatch):
    definition = hook(state)
    row = emit_hook()[0]
    admitted = state.service.admitted
    def pause_after_admission(kwargs):
        admitted(kwargs)
        state.storage.save_hooks_config({"hooks": [{**definition, "enabled": False}]})
    monkeypatch.setattr(state.service, "admitted", pause_after_admission)
    async def run():
        state.service.kick()
        await asyncio.gather(*(task for task, owner in list(state.service._active.values())), return_exceptions=True)
    asyncio.run(run())
    assert not state.effects
    assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "cancelled"


@pytest.mark.parametrize("crash_point", ["during_effect", "after_receipt"])
def test_process_crash_after_effect_does_not_blindly_reexecute(state, monkeypatch, crash_point):
    import os
    hook(state)
    row = emit_hook()[0]
    code = '''
import asyncio, os, runpy, sys
from pathlib import Path
import pytest
fixture = runpy.run_path('tests/runtime_core/test_automation_delivery_recovery.py')
mp = pytest.MonkeyPatch()
state = fixture['install_state'](mp, Path(sys.argv[1]))
action = fixture['action']
original = action.ActionExecutor._execute_command
def effect(target, payload, **kwargs):
    original(target, payload, **kwargs)
    (state.root / 'effect.txt').write_text('one committed effect', encoding='utf-8')
    if sys.argv[2] == 'during_effect':
        os._exit(73)
    return None
mp.setattr(action.ActionExecutor, '_execute_command', effect)
if sys.argv[2] == 'after_receipt':
    mp.setattr(state.service, 'finished', lambda *a, **k: os._exit(73))
asyncio.run(fixture['drain'](state.service))
raise SystemExit(99)
'''
    env = dict(os.environ, V8_AGENT_OS_HOME=str(state.root / "child-state"))
    result = subprocess.run([sys.executable, "-c", code, str(state.root), crash_point], env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 73, result.stdout + result.stderr
    assert (state.root / "effect.txt").read_text() == "one committed effect"
    item = state.db.get_automation_delivery(row["delivery_id"])
    assert item["phase"] == "executing"
    expire(state, item["delivery_id"])
    reopened = install_state(monkeypatch, state.root)
    reopened.db.reconcile_automation_deliveries()
    expected = "unknown" if crash_point == "during_effect" else "completed"
    assert reopened.db.get_automation_delivery(item["delivery_id"])["phase"] == expected
    asyncio.run(drain(reopened.service))
    assert not reopened.effects
    if expected == "unknown":
        assert reopened.db.get_side_effect_receipt(item["receipt_key"])["state"] == "indeterminate"
        assert reopened.db.reconcile_side_effect_receipt(idempotency_key=item["receipt_key"], outcome="completed",
            evidence={"fixtureArtifact": "effect.txt", "observedOutcome": "one committed effect"})
        reopened.db.reconcile_automation_deliveries()
        assert reopened.db.get_automation_delivery(item["delivery_id"])["phase"] == "completed"
        asyncio.run(drain(reopened.service))
        assert not reopened.effects


def test_terminal_memory_marker_cannot_hide_a_missing_hook_outbox(state, monkeypatch):
    hook(state)
    state.db.create_run_record("terminal-run", "source-session", run_type="chat", status="completed",
                               metadata={"memory_terminal_dispatched": True})
    terminal = TerminalPostRunService()
    monkeypatch.setattr(terminal, "_finalize_workflow_guides", lambda **k: None)
    monkeypatch.setattr(terminal, "_schedule_engineering_proof_if_needed", lambda **k: None)
    assert terminal.dispatch(session_id="source-session", run_id="terminal-run", source_component="fixture") is False
    rows = state.db.list_automation_deliveries()
    assert len(rows) == 1
    assert rows[0]["source_event_id"] == "terminal-hook:terminal-run"
    asyncio.run(drain(state.service))
    assert state.effects == ["fixture-effect"]
    terminal.dispatch(session_id="source-session", run_id="terminal-run", source_component="fixture")
    asyncio.run(drain(state.service))
    assert len(state.effects) == 1


def test_legacy_run_success_without_effect_receipt_requires_reconciliation(state):
    hook(state)
    state.db.create_run_record("terminal-run", "source-session", run_type="chat", status="completed",
                               metadata={"memory_terminal_dispatched": True})
    state.db.create_run_record("legacy-hook", "source-session", run_type="automation", status="completed",
        metadata={"action_target": "fixture-effect", "kwargs": {"parent_run_id": "terminal-run", "event_name": "on_chat_end"}})
    emit_hook(parent_run_id="terminal-run")
    asyncio.run(drain(state.service))
    assert not state.effects
    assert state.db.list_automation_deliveries(phases=("unknown",))[0]["execution_run_id"] == "legacy-hook"
    from core.tools.native.automation import read_audit_log
    visible = read_audit_log.func(limit=50, status="REVIEW_REQUIRED")
    assert "reconcile before retry" in visible


def test_legacy_delivery_reconciliation_uses_existing_guard_scope_and_evidence(state, monkeypatch):
    from core.tools.native import automation as native
    from erc.runtime_context import bind_runtime_context
    hook(state)
    state.db.create_run_record("source-run", "source-session", run_type="chat", status="completed")
    state.db.create_run_record("legacy-run", "source-session", run_type="automation", status="completed",
        metadata={"action_target": "fixture-effect", "kwargs": {"parent_run_id": "source-run", "event_name": "on_chat_end"}})
    item = emit_hook(parent_run_id="source-run")[0]
    guarded = []
    monkeypatch.setattr(native.safety_guardian, "assess_hook_mutation", lambda action, **k: guarded.append(action))
    monkeypatch.setattr(native, "_enforce_safety_decision", lambda *a, **k: (False, "fixture denied"))
    assert native.manage_hook.func(action="reconcile", delivery_id=item["delivery_id"], outcome="completed", evidence={"proof": "fixture"}) == "fixture denied"
    monkeypatch.setattr(native, "_enforce_safety_decision", lambda *a, **k: (True, ""))
    with bind_runtime_context(user_id="another-user"):
        assert "scope mismatch" in native.manage_hook.func(action="reconcile", delivery_id=item["delivery_id"], outcome="completed", evidence={"proof": "fixture"})
    with bind_runtime_context(user_id="user-a"):
        assert "evidence" in native.manage_hook.func(action="reconcile", delivery_id=item["delivery_id"], outcome="completed", evidence={})
        result = native.manage_hook.func(action="reconcile", delivery_id=item["delivery_id"], outcome="completed", evidence={"targetObservation": "fixture-effect confirmed"})
    assert "reconciled as completed" in result
    assert guarded == ["reconcile"] * 4
    assert state.db.get_automation_delivery(item["delivery_id"])["envelope"]["reconciliation"]["evidence"] == {"targetObservation": "fixture-effect confirmed"}
    asyncio.run(drain(state.service))
    assert not state.effects


def test_startup_tick_recovers_terminal_gap_with_a_bounded_cursor(state, monkeypatch):
    hook(state)
    state.db.create_run_record("terminal-run", "source-session", run_type="chat", status="completed",
                               metadata={"memory_terminal_dispatched": True})
    reopened = install_state(monkeypatch, state.root)
    async def run():
        await reopened.service.tick()
        await drain(reopened.service)
    asyncio.run(run())
    assert reopened.effects == ["fixture-effect"]
    assert reopened.service._terminal_scan_done


def test_cron_applied_plan_and_revision_survive_invalid_edit_and_reopen(state, monkeypatch):
    job = {"id": "cron-a", "name": "Cron", "enabled": True, "cron_expression": "0 9 * * *",
           "action_type": "command", "action_target": "fixture-effect"}
    state.storage.save_cron_config({"jobs": [job]})
    manager = cron_module.CronManager()
    assert manager.sync_jobs_to_scheduler()["status"] == "success"
    applied = state.storage.get_cron_config()["appliedPlan"]
    state.storage.save_cron_config({"jobs": [{**job, "cron_expression": "invalid"}]})
    assert manager.sync_jobs_to_scheduler()["status"] == "rejected"
    reopened = install_state(monkeypatch, state.root)
    fresh = cron_module.CronManager()
    assert fresh.sync_jobs_to_scheduler()["status"] == "recovered"
    assert fresh.scheduler.get_job("cron-a").kwargs["job_cfg"] == next(job for job in applied["jobs"] if job["id"] == "cron-a")
    assert reopened.storage.get_cron_config()["appliedPlan"] == applied


def test_definition_reads_are_stable_and_failed_plan_publication_rolls_back(state, monkeypatch):
    job = {"id": "cron-a", "name": "Cron", "enabled": True, "cron_expression": "0 9 * * *",
           "action_type": "command", "action_target": "fixture-effect"}
    state.storage.save_cron_config({"jobs": [job]})
    manager = cron_module.CronManager()
    manager.sync_jobs_to_scheduler()
    before = (state.root / "config.json").read_bytes()
    writes = []
    write_json = state.storage.write_json
    monkeypatch.setattr(state.storage, "write_json", lambda *a, **k: (writes.append(a) or write_json(*a, **k)))
    first = state.storage.get_cron_config()
    assert state.storage.get_cron_config() == first
    assert writes == []
    assert (state.root / "config.json").read_bytes() == before
    state.storage.save_cron_config({"jobs": [{**job, "cron_expression": "0 11 * * *"}]})
    def publication_failed(**kwargs):
        raise OSError("fixture publication failure")
    monkeypatch.setattr(state.storage, "publish_applied_cron_plan", publication_failed)
    assert manager.sync_jobs_to_scheduler()["status"] == "partial"
    assert manager.scheduler.get_job("cron-a").kwargs["job_cfg"]["cron_expression"] == "0 9 * * *"


def test_explicit_run_once_accepts_a_disabled_current_cron_definition(state):
    state.storage.save_cron_config({"jobs": [{"id": "manual", "name": "Manual", "enabled": False,
        "cron_expression": "0 9 * * *", "action_type": "command", "action_target": "fixture-effect"}]})
    definition = next(job for job in state.storage.get_cron_config()["jobs"] if job["id"] == "manual")
    async def run():
        result = await cron_module.CronManager().execute_job(definition)
        assert result["status"] == "queued"
        await drain(state.service)
    asyncio.run(run())
    assert state.effects == ["fixture-effect"]


def test_scheduler_occurrence_identity_uses_scheduled_time_and_deduplicates_after_reopen(state, monkeypatch):
    from datetime import datetime, timezone
    state.storage.save_cron_config({"jobs": [{"id": "cron-a", "name": "Cron", "enabled": True,
        "cron_expression": "0 9 * * *", "action_type": "command", "action_target": "fixture-effect"}]})
    manager = cron_module.CronManager()
    manager.sync_jobs_to_scheduler()
    event = SimpleNamespace(job_id="cron-a", scheduled_run_times=[datetime(2026, 9, 14, 9, tzinfo=timezone.utc)])
    manager._on_submission(event)
    reopened = install_state(monkeypatch, state.root)
    fresh = cron_module.CronManager()
    fresh.sync_jobs_to_scheduler()
    fresh._on_submission(event)
    assert len(reopened.db.list_automation_deliveries()) == 1
    asyncio.run(drain(reopened.service))
    assert reopened.effects == ["fixture-effect"]


def test_real_scheduler_submits_durably_before_callback_execution(state, monkeypatch):
    from datetime import datetime, timezone
    from apscheduler.triggers.interval import IntervalTrigger
    state.storage.save_cron_config({"jobs": [{"id": "cron-a", "name": "Cron", "enabled": True,
        "cron_expression": "0 9 * * *", "action_type": "command", "action_target": "fixture-effect"}]})
    async def run():
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        original = action.ActionExecutor._execute_command
        def effect(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.call_soon_threadsafe(done.set_result, True)
            return result
        monkeypatch.setattr(action.ActionExecutor, "_execute_command", effect)
        manager = cron_module.CronManager()
        manager.start()
        try:
            now = datetime.now(timezone.utc)
            manager.scheduler.modify_job("cron-a", trigger=IntervalTrigger(seconds=3600, start_date=now), next_run_time=now)
            await asyncio.wait_for(done, timeout=5)
            await drain(state.service)
            rows = state.db.list_automation_deliveries(phases=("completed",))
            assert len(rows) == 1
            assert rows[0]["admitted_at"]
        finally:
            manager.shutdown()
            await asyncio.sleep(0)
    asyncio.run(run())
    assert state.effects == ["fixture-effect"]
