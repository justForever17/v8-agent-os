from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.memory_maintenance_status import evaluate_memory_maintenance_status


NOW = datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc)


def _job(*, enabled: bool = True, expression: str = "0 3 * * *"):
    return {
        "enabled": enabled,
        "cron_expression": expression,
    }


def test_disabled_maintenance_does_not_request_attention():
    status = evaluate_memory_maintenance_status(
        job_config=_job(enabled=False),
        latest_attempt=None,
        latest_success=None,
        state_started_at=NOW - timedelta(days=3),
        now=NOW,
    )

    assert status["due"] is False
    assert status["reason"] == "disabled"


def test_recent_success_schedules_next_pass_without_warning():
    completed_at = datetime(2026, 7, 26, 3, 10, tzinfo=timezone.utc)
    success = {"status": "success", "started_at": completed_at.isoformat(), "completed_at": completed_at.isoformat()}
    status = evaluate_memory_maintenance_status(
        job_config=_job(),
        latest_attempt=success,
        latest_success=success,
        state_started_at=NOW - timedelta(days=30),
        now=NOW,
    )

    assert status["due"] is False
    assert status["reason"] == "scheduled"
    assert status["nextScheduledAt"] == "2026-07-27T03:00:00Z"


def test_missed_schedule_is_projected_without_starting_a_run():
    completed_at = datetime(2026, 7, 25, 3, 10, tzinfo=timezone.utc)
    success = {"status": "success", "started_at": completed_at.isoformat(), "completed_at": completed_at.isoformat()}
    status = evaluate_memory_maintenance_status(
        job_config=_job(),
        latest_attempt=success,
        latest_success=success,
        state_started_at=NOW - timedelta(days=30),
        now=NOW,
    )

    assert status["due"] is True
    assert status["reason"] == "missed_schedule"
    assert status["nextScheduledAt"] == "2026-07-26T03:00:00Z"


def test_fresh_state_created_after_today_schedule_waits_for_next_schedule():
    status = evaluate_memory_maintenance_status(
        job_config=_job(),
        latest_attempt=None,
        latest_success=None,
        state_started_at=datetime(2026, 7, 26, 3, 30, tzinfo=timezone.utc),
        now=NOW,
    )

    assert status["due"] is False
    assert status["nextScheduledAt"] == "2026-07-27T03:00:00Z"


def test_failed_attempt_requests_user_attention():
    failed_at = datetime(2026, 7, 26, 3, 2, tzinfo=timezone.utc)
    status = evaluate_memory_maintenance_status(
        job_config=_job(),
        latest_attempt={"status": "failed", "started_at": failed_at.isoformat()},
        latest_success=None,
        state_started_at=NOW - timedelta(days=30),
        now=NOW,
    )

    assert status["due"] is True
    assert status["reason"] == "last_attempt_failed"


def test_live_attempt_suppresses_duplicate_warning():
    running_at = NOW - timedelta(hours=2)
    status = evaluate_memory_maintenance_status(
        job_config=_job(),
        latest_attempt={"status": "running", "started_at": running_at.isoformat()},
        latest_success=None,
        state_started_at=NOW - timedelta(days=30),
        now=NOW,
    )

    assert status["due"] is False
    assert status["reason"] == "running"


def test_invalid_schedule_is_diagnostic_only():
    status = evaluate_memory_maintenance_status(
        job_config=_job(expression="not a cron"),
        latest_attempt=None,
        latest_success=None,
        state_started_at=NOW - timedelta(days=30),
        now=NOW,
    )

    assert status["due"] is False
    assert status["reason"] == "invalid_schedule"
