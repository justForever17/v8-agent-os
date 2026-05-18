from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.database import DatabaseManager


def _day(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_dashboard_activity_uses_durable_usage_ledger_when_invocation_logs_are_pruned() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DatabaseManager(Path(temp_dir) / "state.db")
        db.upsert_usage_ledger(
            {
                "id": "ledger-today",
                "bucket_date": _day(),
                "scope_type": "project",
                "scope_id": "test-project",
                "provider_id": "demo-provider",
                "model_id": "demo-model",
                "role": "supervisor",
                "capability_class": "chat",
                "invocations": 7,
                "success_count": 7,
                "error_count": 0,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cost_total": 0.02,
                "latency_ms_total": 1234,
            }
        )

        daily = db.get_daily_telemetry_activity(days=7)
        today = next(item for item in daily if item["day"] == _day())
        totals = db.get_model_invocation_window_totals(days=7)
        distribution = db.get_model_usage_distribution(days=7, limit=5)

        assert today["invocations"] == 7
        assert today["total_tokens"] == 150
        assert totals["invocations"] == 7
        assert totals["total_tokens"] == 150
        assert distribution[0]["model_id"] == "demo-model"
        assert distribution[0]["invocations"] == 7


def test_counts_snapshot_uses_usage_ledger_for_total_invocations() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DatabaseManager(Path(temp_dir) / "state.db")
        db.upsert_usage_ledger(
            {
                "id": "ledger-count",
                "bucket_date": _day(),
                "scope_type": "global",
                "scope_id": "global",
                "provider_id": "demo-provider",
                "model_id": "demo-model",
                "role": "reranker",
                "capability_class": "reranker",
                "invocations": 3,
                "success_count": 3,
                "error_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0,
                "latency_ms_total": 10,
            }
        )

        assert db.get_counts_snapshot()["invocations"] == 3
