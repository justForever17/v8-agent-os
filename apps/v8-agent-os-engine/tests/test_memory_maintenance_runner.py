from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.runners.maintenance_runner import memory_agent_runner


class _FakeRunHandle:
    def __init__(self) -> None:
        self.run_id = "run_memory_maintenance"
        self.session_id = "memory:maintenance"
        self.events: list[tuple[str, dict]] = []
        self.completed_reason: str | None = None

    def emit(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))

    def transition(self, *_args, **_kwargs) -> None:
        return None

    def complete(self, *, reason: str, node: str) -> None:
        self.completed_reason = f"{reason}:{node}"

    def fail(self, *_args, **_kwargs) -> None:
        return None


class MemoryMaintenanceRunnerTests(unittest.TestCase):
    def test_run_maintenance_backfills_missing_or_stale_summaries(self):
        run_handle = _FakeRunHandle()
        lane_decision = SimpleNamespace(
            acquired=True,
            waited=False,
            policy="queue",
            active_run_id=None,
            interrupted_run_id=None,
            rejected_by_run_id=None,
        )
        update_calls: list[dict] = []

        def _capture_update(_run_id: str, updates: dict) -> None:
            update_calls.append(updates)

        async def _fake_generate_periodic_summary(**kwargs):
            return {
                "status": "completed",
                "task_kind": "periodic_summary",
                "tier": kwargs["tier"],
                "target_date": kwargs["target_date"].isoformat(),
            }

        with patch("agents.runners.maintenance_runner.memory_runtime.begin_run", return_value=run_handle), patch(
            "agents.runners.maintenance_runner.session_admission_service.acquire",
            return_value=lane_decision,
        ), patch("agents.runners.maintenance_runner.session_admission_service.release"), patch(
            "agents.runners.maintenance_runner.run_service.transition_run"
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.get_memory_map_health",
            side_effect=[
                {"counts": {"missing": 3, "stale": 1}},
                {"counts": {"missing": 1, "stale": 0}},
            ],
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.list_summary_targets",
            return_value=[
                {"memoryRef": "memory://week/2026-W16", "kind": "week", "summaryState": "missing", "latestDay": "2026-04-18"},
                {"memoryRef": "memory://month/2026-04", "kind": "month", "summaryState": "stale", "latestDay": "2026-04-18"},
            ],
        ), patch(
            "agents.runners.maintenance_runner.memory_agent.generate_periodic_summary",
            side_effect=_fake_generate_periodic_summary,
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.update_run_metadata",
            side_effect=_capture_update,
        ):
            result = asyncio.run(memory_agent_runner.run_maintenance(trigger_source="CRON"))

        self.assertEqual(result["result"]["summary_backfilled_count"], 2)
        maintenance_updates = [item for item in update_calls if "memory_maintenance" in item]
        self.assertTrue(maintenance_updates)
        self.assertEqual(maintenance_updates[0]["memory_maintenance"]["summaryBackfilledCount"], 2)
        self.assertEqual(
            maintenance_updates[0]["memory_maintenance"]["touchedRefs"],
            ["memory://week/2026-W16", "memory://month/2026-04"],
        )


if __name__ == "__main__":
    unittest.main()
