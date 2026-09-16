"""Explicit total deadlines use real queue/control state and executor settlement."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.database import DatabaseManager
from core import runtime_episode_control as control
from core import runtime_episode_runner as runner_module
from core import runtime_episodes as episodes


def deadline(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def database(tmp_path, monkeypatch):
    manager = DatabaseManager(tmp_path / 'deadlines.db')
    manager.create_or_update_session('session', 'deadline fixture', user_id='test')
    manager.create_run_record(run_id='run', session_id='session', run_type='chat', status='running')
    for module in (control, runner_module, episodes):
        monkeypatch.setattr(module, 'db', manager)
    monkeypatch.setattr(control, 'emit_runtime_episode_event', lambda *_args, **_kwargs: None)
    return manager


def enqueue(database, *, kind='engineering', at=None, inputs=None, name='A', parent=None):
    episode = episodes.build_runtime_episode(
        need={'episodeId': name, 'deadlineAt': at, 'inputs': inputs or {}},
        kind=kind, state='queued', parent_episode_id=parent,
        continuation_target='runtime_episode_runner')
    return database.upsert_runtime_episode_record(episode, session_id='session', run_id='run', enqueue=True)


def runner(monkeypatch):
    instance = runner_module.RuntimeEpisodeRunner()
    for name in ('_emit', '_maybe_schedule_chat_handoff_resume', '_resume_cross_episode_dependents', '_maybe_resume_parent_episode'):
        monkeypatch.setattr(instance, name, lambda *_args, **_kwargs: None)
    return instance


@pytest.mark.parametrize('kind', ['engineering', 'delegation'])
def test_expired_queue_never_enters_executor(database, monkeypatch, kind):
    enqueue(database, kind=kind, at=deadline(-1))
    instance = runner(monkeypatch)
    claimed = database.claim_runtime_episode(worker_id=instance.worker_id)
    entered = []

    async def execute(_episode):
        entered.append(True)
        return episodes.build_handoff_ref(producer_episode_id='A', kind='fixture', compact_summary='too late', status='ready')

    monkeypatch.setattr(instance, f'_execute_{kind}', execute)
    asyncio.run(instance._execute_episode(claimed))
    assert entered == []
    stored = database.get_runtime_episode('A')
    assert stored['state'] == 'failed' and stored['errorCode'] == 'episode_deadline_exceeded'
    assert database.list_runtime_episode_handoffs('A') == []
    assert database.claim_runtime_episode(worker_id='retry') is None


def test_task_budget_is_persisted_and_child_cannot_extend_parent(database):
    at = deadline(60)
    parent = enqueue(database, at=at)
    child = enqueue(database, name='child', parent='A', inputs={'workerBriefs': [
        {'taskBriefId': 'brief', 'goal': 'fixture', 'budget': {'deadlineAt': deadline(120)}}]})
    assert child['deadlineAt'] == parent['deadlineAt']
    independent = enqueue(database, name='independent', inputs={'workerBriefs': [
        {'taskBriefId': 'brief', 'goal': 'fixture', 'budget': {'deadlineAt': at}}]})
    assert independent['deadlineAt'] == parent['deadlineAt']


def test_expired_deadline_denies_new_effect_before_watchdog_poll(database):
    enqueue(database, at=deadline(-1))
    with pytest.raises(control.EpisodeControlCancelled):
        control.assert_episode_execution_allowed({'run_id': 'run', 'episode_id': 'A'})


@pytest.mark.parametrize('kind', ['engineering', 'delegation'])
def test_running_deadline_settles_and_discards_suppressed_cancel_result(database, monkeypatch, tmp_path, kind):
    enqueue(database, kind=kind, at=deadline(.35))
    instance = runner(monkeypatch)
    claimed = database.claim_runtime_episode(worker_id=instance.worker_id)
    observed = []
    output = tmp_path / 'late-write.txt'

    async def execute(_episode):
        try:
            await asyncio.sleep(.8)
        except asyncio.CancelledError:
            observed.append('cancelled')
            with pytest.raises(control.EpisodeControlCancelled):
                control.assert_episode_execution_allowed({'run_id': 'run', 'episode_id': 'A'})
            observed.append('write_denied')
        else:
            output.write_text('expired writer ran')
        return episodes.build_handoff_ref(producer_episode_id='A', kind='fixture', compact_summary='late success', status='ready')

    monkeypatch.setattr(instance, f'_execute_{kind}', execute)
    asyncio.run(asyncio.wait_for(instance._execute_episode(claimed), 3))
    assert observed == ['cancelled', 'write_denied'] and not output.exists()
    assert database.get_runtime_episode('A')['errorCode'] == 'episode_deadline_exceeded'
    assert database.list_runtime_episode_handoffs('A') == []
    messages = database.list_runtime_episode_messages(run_id='run', recipient='A', pending_only=False)
    assert any(item['kind'] == 'cancel' and item['deliveryState'] == 'stopped' for item in messages)


@pytest.mark.parametrize('state', ['queued', 'waiting_dependency', 'waiting_approval'])
def test_expired_parked_episode_settles_without_dispatch(database, monkeypatch, state):
    enqueue(database, at=deadline(-1))
    if state != 'queued':
        database.complete_runtime_episode('A', state=state)
    instance = runner(monkeypatch)
    asyncio.run(instance._settle_parked_episode_cancellations())
    stored = database.get_runtime_episode('A')
    assert stored['state'] == 'failed' and stored['errorCode'] == 'episode_deadline_exceeded'


def test_no_deadline_keeps_long_task_semantics(database, monkeypatch):
    record = enqueue(database)
    assert runner_module.RuntimeEpisodeRunner._episode_executor_deadline_seconds(record) is None
    control.assert_episode_execution_allowed({'run_id': 'run', 'episode_id': 'A'})


def test_public_runtime_tool_deadline_reaches_canonical_queue(database, monkeypatch):
    import core.tools.native.runtime as native_runtime
    from core.tools.native.runtime import RuntimeBrokerArgs, _route_need_from_public_transport
    from erc.runtime_context import bind_runtime_context
    monkeypatch.setattr(native_runtime, 'db', database)
    args = RuntimeBrokerArgs(mode='route', routeKind='engineering', routeReason='bounded task',
        deadlineAt=deadline(90), taskBriefs=[{'taskBriefId': 'brief', 'goal': 'read-only fixture'}])
    assert 'deadlineAt' in RuntimeBrokerArgs.model_json_schema()['properties']
    need = _route_need_from_public_transport(None, route_kind=args.routeKind, route_reason=args.routeReason,
                                            task_briefs=args.taskBriefs, deadline_at=args.deadlineAt)
    episode = episodes.build_runtime_episode(need=need, kind='engineering', state='queued')
    restored = database.upsert_runtime_episode_record(episode, session_id='session', run_id='run', enqueue=True)
    assert restored['deadlineAt'] == args.deadlineAt
    with pytest.raises(ValueError, match='deadline'):
        RuntimeBrokerArgs(deadlineAt='tomorrow sometime')
    with pytest.raises(ValueError, match='timezone'):
        RuntimeBrokerArgs(deadlineAt='2026-10-01T12:00:00')
    assert RuntimeBrokerArgs(deadlineAt=None).deadlineAt is None
    with bind_runtime_context(session_id='session', run_id='run', actor_role='supervisor'):
        command = native_runtime.runtime_broker.func(
            mode='route', routeKind='engineering', routeReason='explicit total deadline fixture',
            taskBriefs=[{'taskBriefId': 'public', 'goal': 'Return an engineering plan without writing files.',
                         'readOnly': True, 'writeRequired': False, 'writeSet': [],
                         'expectedOutputs': ['engineering plan'], 'acceptance': {'must': ['No files are written.']}}],
            deadlineAt=args.deadlineAt, state={'session_id': 'session', 'run_id': 'run', 'current_route_context': {}},
            tool_call_id='deadline-tool-call')
    assert command.update['current_route_context'].get('capabilityEpisodes'), command.update.get('messages')
    routed = command.update['current_route_context']['capabilityEpisodes'][-1]
    assert database.get_runtime_episode(routed['episodeId'])['deadlineAt'] == args.deadlineAt


def test_deadline_stops_real_thread_at_governed_write_boundary(database, monkeypatch, tmp_path):
    enqueue(database, at=deadline(.45))
    instance = runner(monkeypatch)
    claimed = database.claim_runtime_episode(worker_id=instance.worker_id)
    denied, stopped = threading.Event(), threading.Event()
    output = tmp_path / 'thread.txt'

    def writer():
        try:
            while True:
                try:
                    control.assert_episode_execution_allowed({'run_id': 'run', 'episode_id': 'A'})
                except control.EpisodeControlCancelled:
                    denied.set()
                    return {'status': 'ready'}  # A late success must still be discarded.
                with output.open('a') as stream:
                    stream.write('tick\n')
                time.sleep(.01)
        finally:
            stopped.set()

    async def execute(_episode):
        return await instance._run_sync_episode_call(writer)

    monkeypatch.setattr(instance, '_execute_engineering', execute)
    asyncio.run(asyncio.wait_for(instance._execute_episode(claimed), 3))
    assert stopped.is_set() and denied.is_set() and output.exists()
    size = output.stat().st_size
    time.sleep(.05)
    assert output.stat().st_size == size
    assert database.get_runtime_episode('A')['errorCode'] == 'episode_deadline_exceeded'
    assert database.list_runtime_episode_handoffs('A') == []


def test_deadline_terminates_managed_process_without_stopping_sibling(database, monkeypatch, tmp_path):
    from core.tools.native import command
    monkeypatch.setattr(command, '_bg_processes', {})
    enqueue(database, name='A', at=deadline(1.5))
    enqueue(database, name='B')
    instance = runner(monkeypatch)
    claimed = database.claim_runtime_episode(worker_id=instance.worker_id)
    script = tmp_path / 'writer.py'
    script.write_text("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile True:\n with p.open('a') as f: f.write('tick\\n'); f.flush()\n time.sleep(.02)\n")

    def start(name):
        output = tmp_path / f'{name}.txt'
        if sys.platform == 'win32':
            cmd = f"& '{sys.executable}' '{script}' '{output}'"
            dialect = 'powershell'
        else:
            import shlex
            cmd = ' '.join(shlex.quote(str(value)) for value in (sys.executable, script, output))
            dialect = 'bash'
        command._bg_processes[name] = command.BackgroundProcess(cmd, cwd=str(tmp_path), shell_dialect=dialect,
            session_id='session', run_id='run', runtime_context={'session_id': 'session', 'run_id': 'run', 'episode_id': name}, timeout_seconds=15)

    async def execute(_episode):
        start('A')
        await asyncio.Event().wait()

    start('B')
    monkeypatch.setattr(instance, '_execute_engineering', execute)
    try:
        asyncio.run(asyncio.wait_for(instance._execute_episode(claimed), 10))
        assert database.get_runtime_episode('A')['errorCode'] == 'episode_deadline_exceeded'
        a, b = tmp_path / 'A.txt', tmp_path / 'B.txt'
        assert a.exists() and b.exists()
        sizes = a.stat().st_size, b.stat().st_size
        time.sleep(.15)
        assert a.stat().st_size == sizes[0] and b.stat().st_size > sizes[1]
    finally:
        command.terminate_episode_background_commands('A')
        command.terminate_episode_background_commands('B')


def test_inline_brief_deadline_does_not_cancel_unbounded_sibling(database, monkeypatch):
    from langgraph.types import Send
    import graph.parallel_support as support
    parent = enqueue(database, name='parent', kind='delegation')
    enqueue(database, name='A', kind='delegation', parent='parent', inputs={'workerBriefs': [
        {'taskBriefId': 'A', 'goal': 'bounded fixture', 'budget': {'deadlineAt': deadline(.35)}}]})
    enqueue(database, name='B', kind='delegation', parent='parent')
    instance = runner(monkeypatch)
    monkeypatch.setattr(instance, '_build_agent_nodes_map', lambda: {'worker': {'id': 'worker'}})
    entered, cancelled = [], []

    async def branch(state, _agent, progress_callback=None):
        name = state['parallel_branch']['taskBriefId']
        entered.append(name)
        try:
            await asyncio.sleep(.7 if name == 'A' else .05)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        return [], [], {'taskBriefId': name, 'status': 'ok', 'summary': 'completed'}, []

    monkeypatch.setattr(support, '_run_parallel_agent_branch', branch)
    sends = [Send('parallel_delegate_task', {'parallel_branch': {
        'agentId': 'worker', 'agentName': 'worker', 'taskBriefId': name, 'delegationId': name,
        'taskBrief': {'taskBriefId': name, 'goal': 'fixture'}, 'reason': 'fixture'}}) for name in ('A', 'B')]
    results, _ = asyncio.run(instance._execute_local_delegation_sends(SimpleNamespace(goto=sends), parent))
    assert set(entered) == {'A', 'B'} and cancelled == ['A']
    by_id = {item['taskBriefId']: item for item in results}
    assert by_id['A']['errorCode'] == 'episode_deadline_exceeded' and by_id['B']['status'] == 'ok'
    assert database.get_runtime_episode('A')['state'] == 'failed'
    assert database.get_runtime_episode('B')['state'] == 'completed'


def test_stale_worker_deadline_cannot_cancel_or_complete_new_claim(database, monkeypatch):
    enqueue(database, at=deadline(-1))
    instance = runner(monkeypatch)
    old = database.claim_runtime_episode(worker_id=instance.worker_id)
    with database.get_connection() as conn:
        conn.execute("UPDATE runtime_episodes SET lease_expires_at='2000-01-01T00:00:00Z' WHERE id='A'")
        conn.execute("UPDATE runtime_episode_queue SET lease_expires_at='2000-01-01T00:00:00Z' WHERE episode_id='A'")
        conn.commit()
    new = database.claim_runtime_episode(worker_id='new-worker')
    assert new['leaseGeneration'] > old['leaseGeneration']
    asyncio.run(instance._execute_episode(old))
    stored = database.get_runtime_episode('A')
    assert stored['state'] == 'active' and stored['worker_id'] == 'new-worker'
    assert database.list_runtime_episode_messages(run_id='run', recipient='A', pending_only=False) == []


def test_deadline_discards_result_when_executor_blocks_event_loop(database, monkeypatch):
    enqueue(database, at=deadline(.25))
    instance = runner(monkeypatch)
    claimed = database.claim_runtime_episode(worker_id=instance.worker_id)
    entered = []

    async def execute(_episode):
        entered.append(True)
        time.sleep(.4)  # Deliberately starve the watchdog, as a faulty adapter can.
        return episodes.build_handoff_ref(producer_episode_id='A', kind='fixture', compact_summary='late', status='ready')

    monkeypatch.setattr(instance, '_execute_engineering', execute)
    asyncio.run(instance._execute_episode(claimed))
    assert entered == [True]
    assert database.get_runtime_episode('A')['errorCode'] == 'episode_deadline_exceeded'
    assert database.list_runtime_episode_handoffs('A') == []
