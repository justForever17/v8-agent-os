from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


SYSTEM_MEMORY_MAINTENANCE_JOB_ID = "system-memory-maintenance"
SYSTEM_MEMORY_MAINTENANCE_JOB_NAME = "Memory Maintenance"
SYSTEM_MEMORY_MAINTENANCE_TARGET = "agents.runners.memory_maintenance_job"
SYSTEM_MEMORY_MAINTENANCE_CRON = "0 3 * * *"


def build_system_memory_maintenance_job(*, enabled: bool = True, cron_expression: str | None = None) -> Dict[str, Any]:
    return {
        "id": SYSTEM_MEMORY_MAINTENANCE_JOB_ID,
        "name": SYSTEM_MEMORY_MAINTENANCE_JOB_NAME,
        "cron_expression": str(cron_expression or SYSTEM_MEMORY_MAINTENANCE_CRON).strip() or SYSTEM_MEMORY_MAINTENANCE_CRON,
        "action_type": "python",
        "action_target": SYSTEM_MEMORY_MAINTENANCE_TARGET,
        "payload": {"mode": "full"},
        "enabled": bool(enabled),
        "triggerKind": "nudge",
        "attachPolicy": "new_session",
        "sourceMetadata": {
            "systemJob": True,
            "description": "Backfill and maintain daily logs, week/month/year summaries, durable memory, and graph health.",
        },
    }


def normalize_cron_config_with_system_job(data: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(data or {})
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        jobs = []

    normalized_jobs: list[Dict[str, Any]] = []
    system_enabled = True
    system_cron_expression = SYSTEM_MEMORY_MAINTENANCE_CRON

    for item in jobs:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() == SYSTEM_MEMORY_MAINTENANCE_JOB_ID:
            system_enabled = bool(item.get("enabled", True))
            raw_expression = str(item.get("cron_expression") or "").strip()
            if raw_expression:
                system_cron_expression = raw_expression
            continue
        normalized_jobs.append(deepcopy(item))

    normalized_jobs.insert(
        0,
        build_system_memory_maintenance_job(
            enabled=system_enabled,
            cron_expression=system_cron_expression,
        ),
    )
    payload["jobs"] = normalized_jobs
    return payload
