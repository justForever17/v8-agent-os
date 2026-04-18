from __future__ import annotations

import asyncio
from typing import Any, Dict

from agents.runners.maintenance_runner import memory_agent_runner


def run(action_payload: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
    trigger_source = str(kwargs.get("trigger") or "cron").upper()
    user_id = str(kwargs.get("user_id") or "system")
    run_id = kwargs.get("run_id")
    session_id = kwargs.get("session_id")
    return asyncio.run(
        memory_agent_runner.run_maintenance(
            trigger_source=trigger_source,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
        )
    )
