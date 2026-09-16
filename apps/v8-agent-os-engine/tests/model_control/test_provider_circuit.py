from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.database import DatabaseManager
from core.provider_circuit import ProviderCircuitOpen, ProviderCircuitService


CONFIG = {'governance': {'providerFailureThreshold': 1, 'providerErrorRateThreshold': .6,
                        'providerCircuitCooldownSeconds': 5, 'providerCircuitProbeLeaseSeconds': 10}}


@pytest.fixture
def store(tmp_path):
    clock = [100.0]
    database = DatabaseManager(tmp_path / 'circuits.db')
    return ProviderCircuitService(database, clock=lambda: clock[0]), database, clock


def open_circuit(service):
    permit = service.acquire('p', config=CONFIG)
    assert service.finish(permit, success=False, config=CONFIG, error_code='provider_unavailable')
    assert service.snapshot('p')['circuitState'] == 'open'


def test_open_rejects_actual_admission_and_reports_recovery_window(store):
    service, _, _ = store
    open_circuit(service)
    with pytest.raises(ProviderCircuitOpen) as failure:
        service.acquire('p', config=CONFIG)
    assert failure.value.code == 'provider_circuit_open'
    assert failure.value.details == {'retryAfterSeconds': 5.0, 'reason': 'open'}
    assert service.acquire('other', config=CONFIG)['providerId'] == 'other'


def test_due_half_open_has_exactly_one_probe_across_threads(store):
    service, database, clock = store
    open_circuit(service)
    clock[0] = 106
    barrier = threading.Barrier(8)

    def attempt(index):
        independent = ProviderCircuitService(database, clock=lambda: clock[0])
        barrier.wait()
        try:
            return independent.acquire('p', config=CONFIG, attempt_id=f'thread-{index}')
        except ProviderCircuitOpen:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        acquired = [permit for permit in pool.map(attempt, range(8)) if permit]
    assert len(acquired) == 1 and acquired[0]['probe'] is True
    assert service.snapshot('p')['probeInFlight'] is True
    assert service.finish(acquired[0], success=True, config=CONFIG)
    assert service.snapshot('p')['circuitState'] == 'closed'


def _process_attempt(path, ready, start, result, index):
    service = ProviderCircuitService(DatabaseManager(Path(path)), clock=lambda: 106.0)
    ready.put(index)
    start.wait(20)
    try:
        result.put(service.acquire('p', config=CONFIG, attempt_id=f'process-{index}'))
    except ProviderCircuitOpen:
        result.put(None)


def test_probe_claim_survives_process_exit_and_is_exclusive_across_processes(store):
    service, database, clock = store
    open_circuit(service)
    context = multiprocessing.get_context('spawn')
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=_process_attempt, args=(str(database.db_path), ready, start, results, i)) for i in range(3)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            ready.get(timeout=30)
        start.set()
        acquired = [value for _ in processes if (value := results.get(timeout=20))]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert len(acquired) == 1
        clock[0] = 110
        with pytest.raises(ProviderCircuitOpen):
            service.acquire('p', config=CONFIG)
        clock[0] = 117
        replacement = service.acquire('p', config=CONFIG)
        assert replacement['generation'] > acquired[0]['generation']
        with database.get_connection() as conn:
            assert conn.execute('SELECT state FROM provider_circuit_attempts WHERE id=?',
                                (acquired[0]['permitId'],)).fetchone()['state'] == 'expired'
        assert not service.finish(acquired[0], success=True, config=CONFIG)
        assert service.snapshot('p')['probeInFlight']
        assert service.finish(replacement, success=False, config=CONFIG, error_code='timeout')
        assert service.snapshot('p')['circuitState'] == 'open'
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        for queue in (ready, results):
            queue.close()


def test_expired_probe_completion_cannot_close_even_before_reclaim(store):
    service, _, clock = store
    open_circuit(service)
    clock[0] = 106
    old = service.acquire('p', config=CONFIG)
    clock[0] = 116
    assert not service.finish(old, success=True, config=CONFIG)
    assert service.snapshot('p')['circuitState'] == 'half_open'
    new = service.acquire('p', config=CONFIG)
    assert new['permitId'] != old['permitId']


def test_explicit_test_is_one_probe_and_cancel_preserves_original_cooldown(store):
    service, _, clock = store
    open_circuit(service)
    clock[0] = 101
    probe = service.acquire('p', config=CONFIG, explicit_probe=True)
    with pytest.raises(ProviderCircuitOpen):
        service.acquire('p', config=CONFIG, explicit_probe=True)
    assert service.release(probe, reason='budget_denied')
    assert service.snapshot('p')['retryAfterSeconds'] == 4
    retry = service.acquire('p', config=CONFIG, explicit_probe=True)
    assert service.finish(retry, success=True, config=CONFIG)
    assert service.snapshot('p')['circuitState'] == 'closed'


def test_late_normal_completion_cannot_close_new_circuit(store):
    service, _, _ = store
    late = service.acquire('p', config=CONFIG)
    open_circuit(service)
    assert not service.finish(late, success=True, config=CONFIG)
    assert service.snapshot('p')['circuitState'] == 'open'


@pytest.mark.parametrize('code', ['content_policy_block', 'context_window_overflow', 'invalid_request'])
def test_request_rejection_does_not_open_provider_circuit(store, code):
    service, _, _ = store
    for _ in range(4):
        permit = service.acquire('p', config=CONFIG)
        assert service.finish(permit, success=False, config=CONFIG, error_code=code)
    assert service.snapshot('p')['circuitState'] == 'closed'


def test_duplicate_admission_and_finish_are_idempotent_but_cannot_replay_finished_call(store):
    service, _, _ = store
    first = service.acquire('p', config=CONFIG, attempt_id='sdk-call')
    assert service.acquire('p', config=CONFIG, attempt_id='sdk-call') == first
    assert service.finish(first, success=False, config=CONFIG, error_code='timeout')
    assert not service.finish(first, success=False, config=CONFIG, error_code='timeout')
    with pytest.raises(ProviderCircuitOpen):
        service.acquire('p', config=CONFIG, attempt_id='sdk-call')
    with pytest.raises(ValueError, match='identity_conflict'):
        service.acquire('another', config=CONFIG, attempt_id='sdk-call')


def test_window_threshold_uses_current_generation_and_recovers_without_old_health_poison(store):
    service, database, clock = store
    config = {'governance': {**CONFIG['governance'], 'providerFailureThreshold': 3}}
    for success in (True, False):
        service.finish(service.acquire('p', config=config), success=success, config=config, error_code='timeout')
        assert service.snapshot('p')['circuitState'] == 'closed'
    service.finish(service.acquire('p', config=config), success=False, config=config, error_code='timeout')
    assert service.snapshot('p')['circuitState'] == 'open'
    clock[0] = 106
    restored = ProviderCircuitService(DatabaseManager(database.db_path), clock=lambda: clock[0])
    restored.finish(restored.acquire('p', config=config), success=True, config=config)
    restored.finish(restored.acquire('p', config=config), success=False, config=config, error_code='timeout')
    assert restored.snapshot('p')['circuitState'] == 'closed'


def test_health_projection_uses_durable_admission_not_historical_error_rate(store, monkeypatch):
    from core import provider_health_service as health
    service, database, clock = store
    monkeypatch.setattr(health, 'db', database)
    monkeypatch.setattr(health, 'provider_circuit_service', service)
    monkeypatch.setattr(database, 'get_provider_health_summary', lambda **_: [
        {'provider_id': 'p', 'events': 20, 'error_count': 20, 'success_count': 0}])
    config = {**CONFIG, 'providers': {'p': {'provider': {'name': 'P'}}}}
    assert health.ProviderHealthService()._health_map(config)['p']['circuitState'] == 'closed'
    open_circuit(service)
    clock[0] = 106
    state = health.ProviderHealthService()._health_map(config)['p']
    assert state['circuitAllowsAttempt'] and state['circuitState'] == 'half_open'
    probe = service.acquire('p', config=CONFIG)
    assert health.ProviderHealthService()._health_map(config)['p']['probeInFlight']
    service.finish(probe, success=True, config=CONFIG)
    assert health.ProviderHealthService()._health_map(config)['p']['circuitState'] == 'closed'
