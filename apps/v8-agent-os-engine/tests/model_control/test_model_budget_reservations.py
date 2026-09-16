import threading
from pathlib import Path

import pytest

from core.database import DatabaseManager
from core.model_budget_service import ModelBudgetService
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
        database.upsert_usage_ledger({"id": "ledger-1", "bucket_date": "2099-01-01", "scope_type": "run", "scope_id": "run", "provider_id": "p", "model_id": "fixture", "role": "default", "invocations": 1, "total_tokens": 80, "input_tokens": 30, "output_tokens": 50})
        conn.execute("UPDATE model_budget_reservations SET ledger_accounted=1 WHERE id=?", (reservation,))
        conn.commit()
    next_reservation = service.reserve(config=config, run_id='run-2', model_id='fixture', estimated_tokens=20)
    assert next_reservation


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
