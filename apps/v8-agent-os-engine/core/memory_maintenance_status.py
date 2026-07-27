from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from apscheduler.triggers.cron import CronTrigger

from core.memory_maintenance_contract import (
    SYSTEM_MEMORY_MAINTENANCE_JOB_ID,
    SYSTEM_MEMORY_MAINTENANCE_TARGET,
)
from core.observability_db import observability_db
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


_SUCCESS_STATUSES = {"success", "completed"}
_ACTIVE_GRACE = timedelta(hours=6)


def _parse_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _execution_time(item: Dict[str, Any] | None, *, prefer_completed: bool = False) -> Optional[datetime]:
    payload = item or {}
    if prefer_completed:
        return _parse_datetime(payload.get("completed_at") or payload.get("started_at"))
    return _parse_datetime(payload.get("started_at") or payload.get("completed_at"))


def evaluate_memory_maintenance_status(
    *,
    job_config: Dict[str, Any] | None,
    latest_attempt: Dict[str, Any] | None,
    latest_success: Dict[str, Any] | None,
    state_started_at: datetime,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Project whether the built-in maintenance schedule needs user attention.

    This function is deliberately read-only. A missed schedule is surfaced to
    the Admin inbox; it never starts a recovery run on Engine startup.
    """

    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_timezone = current.tzinfo or timezone.utc

    job = dict(job_config or {})
    enabled = bool(job.get("enabled", False))
    expression = str(job.get("cron_expression") or "").strip()
    attempt_at = _execution_time(latest_attempt)
    attempt_status = str((latest_attempt or {}).get("status") or "").strip().lower() or None
    success_at = _execution_time(latest_success, prefer_completed=True)
    if attempt_status in _SUCCESS_STATUSES and attempt_at and (success_at is None or attempt_at > success_at):
        success_at = _execution_time(latest_attempt, prefer_completed=True) or attempt_at

    base = {
        "enabled": enabled,
        "due": False,
        "reason": "disabled" if not enabled else "scheduled",
        "lastAttemptAt": _iso(attempt_at),
        "lastAttemptStatus": attempt_status,
        "lastSuccessAt": _iso(success_at),
        "nextScheduledAt": None,
    }
    if not enabled:
        return base
    if not expression:
        return {**base, "reason": "invalid_schedule"}

    try:
        trigger = CronTrigger.from_crontab(expression, timezone=local_timezone)
    except (TypeError, ValueError):
        return {**base, "reason": "invalid_schedule"}

    if attempt_status == "running" and attempt_at and current - attempt_at.astimezone(current.tzinfo) < _ACTIVE_GRACE:
        return {**base, "reason": "running"}

    if attempt_at and attempt_status not in _SUCCESS_STATUSES:
        if success_at is None or attempt_at > success_at:
            return {
                **base,
                "due": True,
                "reason": "stale_running" if attempt_status == "running" else "last_attempt_failed",
            }

    anchor = success_at or state_started_at
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    anchor_local = anchor.astimezone(local_timezone)
    next_scheduled = trigger.get_next_fire_time(None, anchor_local + timedelta(seconds=1))
    due = bool(next_scheduled and next_scheduled <= current.astimezone(local_timezone))
    return {
        **base,
        "due": due,
        "reason": "missed_schedule" if due else "scheduled",
        "nextScheduledAt": _iso(next_scheduled),
    }


def get_memory_maintenance_status() -> Dict[str, Any]:
    job = next(
        (
            item
            for item in storage.get_cron_config().get("jobs", [])
            if str(item.get("id") or "").strip() == SYSTEM_MEMORY_MAINTENANCE_JOB_ID
        ),
        None,
    )
    latest_attempt = observability_db.get_latest_execution(action_target=SYSTEM_MEMORY_MAINTENANCE_TARGET)
    latest_success = observability_db.get_latest_execution(
        action_target=SYSTEM_MEMORY_MAINTENANCE_TARGET,
        statuses=["success", "completed"],
    )
    try:
        state_started_at = datetime.fromtimestamp(V8_AGENT_OS_HOME.stat().st_ctime, tz=timezone.utc)
    except OSError:
        state_started_at = datetime.now(timezone.utc)
    return evaluate_memory_maintenance_status(
        job_config=job,
        latest_attempt=latest_attempt,
        latest_success=latest_success,
        state_started_at=state_started_at,
    )
