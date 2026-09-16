import threading
from pathlib import Path

import pytest

from core.database import DatabaseManager
from core.model_budget_service import ModelBudgetService, _today_bucket
from core.model_governance_exceptions import ModelGovernanceInterventionRequired


def _config(**budgets):
    return {'governance': {'budgets': {'enabled': True, **budgets}}}


def test_two_process_like_reservations_only_one_can_claim_last_estimate(tmp_path: Path):
    database = DatabaseManager(tmp_path / 'state.db')
    service = ModelBudgetService(database=database)
    config = _config(globalDailyTokenLimit=100)
    barrier = threading.Barrier(2)
    results = []

    def claim():
        barrier.wait()
        try:
            results.append(('success', service.reserve(config=config, run_id='run', model_id='fixture', estimated_tokens=60)))
        except ModelGovernanceInterventionRequired as exc:
            results.append(('blocked', exc.details['code']))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=5)
    assert sum(item[0] == 'blocked' and item[1] == 'global_daily_tokens' for item in results) == 1
    assert sum(item[0] == 'success' for item in results) == 1


def test_actual_settlement_can_exceed_estimate_and_is_not_double_charged(tmp_path: Path):
    database = DatabaseManager(tmp_path / 'state.db')
    service = ModelBudgetService(database=database)
    config = _config(globalDailyTokenLimit=100)
    reservation = service.reserve(config=config, run_id='run', model_id='fixture', estimated_tokens=30)
    with service.settlement(reservation, usage_reported=True, actual_tokens=80, actual_cost=0):
        pass
    # The reservation remains counted until the canonical usage ledger marks it.
    with database.get_connection() as conn:
        assert tuple(conn.execute("SELECT state,actual_tokens,ledger_accounted FROM model_budget_reservations WHERE id=?", (reservation,)).fetchone()) == ('settled', 80, 0)
        database.upsert_usage_ledger({"id": "ledger-1", "bucket_date": _today_bucket(), "scope_type": "session", "scope_id": "session", "provider_id": "p", "model_id": "fixture", "role": "default", "invocations": 1, "total_tokens": 80, "input_tokens": 30, "output_tokens": 50})
        conn.execute("UPDATE model_budget_reservations SET ledger_accounted=1 WHERE id=?", (reservation,))
        conn.commit()
    next_reservation = service.reserve(config=config, run_id='run-2', model_id='fixture', estimated_tokens=20)
    assert next_reservation
    with pytest.raises(ModelGovernanceInterventionRequired):
        service.reserve(config=config, run_id='run-3', estimated_tokens=1)


def test_atomic_settlement_rollback_keeps_unknown_usage_and_no_partial_ledger(tmp_path):
    database = DatabaseManager(tmp_path / 'state.db')
    service = ModelBudgetService(database)
    config = _config(globalDailyTokenLimit=100)
    reservation = service.reserve(config=config, estimated_tokens=80)
    service.mark_dispatched(reservation)
    with pytest.raises(OSError, match='fault before commit'):
        with service.settlement(reservation, usage_reported=True, actual_tokens=20) as (conn, write):
            assert write
            database.upsert_usage_ledger({'id': 'ledger', 'bucket_date': _today_bucket(),
                'scope_type': 'global', 'scope_id': 'global', 'model_id': 'fixture', 'role': 'default',
                'invocations': 1, 'total_tokens': 20}, connection=conn)
            service.mark_ledger_accounted(reservation, connection=conn)
            raise OSError('fault before commit')
    assert database.get_usage_ledger_totals()['total_tokens'] == 0
    with database.get_connection() as conn:
        row = conn.execute('SELECT state,ledger_accounted FROM model_budget_reservations').fetchone()
    assert tuple(row) == ('unknown', 0)
    summary = service.build_budget_summary(config)
    assert summary['global']['reserved']['totalTokens'] == 80
    assert summary['global']['reserved']['unknownUsageInvocations'] == 1
    with pytest.raises(ModelGovernanceInterventionRequired):
        service.reserve(config=config, estimated_tokens=21)


def test_run_settlement_counts_once_even_with_missing_or_duplicate_observability(tmp_path):
    database = DatabaseManager(tmp_path / 'state.db')
    service = ModelBudgetService(database)
    config = _config(runMaxTokens=100)
    reservation = service.reserve(config=config, run_id='R', estimated_tokens=20)
    with service.settlement(reservation, usage_reported=True, actual_tokens=80):
        pass
    # A lost observability write cannot restore already spent budget.
    with pytest.raises(ModelGovernanceInterventionRequired):
        service.reserve(config=config, run_id='R', estimated_tokens=21)
    database.add_model_invocation_log({'id': f'budget:{reservation}', 'run_id': 'R',
        'provider_id': 'p', 'model_id': 'm', 'status': 'completed', 'total_tokens': 80})
    # An eventual log must not charge the same invocation a second time.
    assert service.reserve(config=config, run_id='R', estimated_tokens=20)
    assert service.reserve(config=config, run_id='other', estimated_tokens=100)


def test_duplicate_terminal_does_not_write_a_second_ledger_charge(tmp_path):
    database = DatabaseManager(tmp_path / 'state.db')
    service = ModelBudgetService(database)
    reservation = service.reserve(config=_config(globalDailyTokenLimit=100), estimated_tokens=20)
    for _ in range(2):
        with service.settlement(reservation, usage_reported=True, actual_tokens=30) as (conn, write):
            if write:
                database.upsert_usage_ledger({'id': 'entry', 'bucket_date': _today_bucket(),
                    'scope_type': 'global', 'scope_id': 'global', 'model_id': 'fixture', 'role': 'default',
                    'invocations': 1, 'total_tokens': 30}, connection=conn)
                service.mark_ledger_accounted(reservation, connection=conn)
    assert database.get_usage_ledger_totals()['total_tokens'] == 30
    assert database.get_usage_ledger_totals()['invocations'] == 1


def test_missing_usage_is_unknown_and_keeps_estimated_hold(tmp_path: Path):
    database = DatabaseManager(tmp_path / 'state.db')
    service = ModelBudgetService(database=database)
    config = _config(globalDailyTokenLimit=100)
    reservation = service.reserve(config=config, run_id='run', model_id='fixture', estimated_tokens=80)
    with service.settlement(reservation, usage_reported=False, actual_tokens=0, actual_cost=0):
        pass
    with pytest.raises(ModelGovernanceInterventionRequired) as blocked:
        service.reserve(config=config, run_id='run-2', model_id='fixture', estimated_tokens=30)
    assert blocked.value.details['code'] == 'global_daily_tokens'


def test_cost_limit_requires_a_cost_estimate_instead_of_treating_unknown_as_zero(tmp_path: Path):
    service = ModelBudgetService(database=DatabaseManager(tmp_path / 'state.db'))
    with pytest.raises(ModelGovernanceInterventionRequired) as blocked:
        service.reserve(config=_config(globalDailyCostLimit=1), run_id='run', model_id='fixture',
                       estimated_tokens=10, estimated_cost=None)
    assert blocked.value.details['code'] == 'budget_cost_estimate_unavailable'
