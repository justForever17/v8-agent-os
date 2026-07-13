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
        quarantined_preferences: list[tuple[str, str, str]] = []
        quarantined_knowledge: list[str] = []

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
            "agents.runners.maintenance_runner.memory_runtime.backfill_periodic_summaries",
            return_value={"updatedCount": 1, "touchedRefs": ["memory://year/2026"]},
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.run_workflow_maintenance",
            return_value={"candidateCount": 3, "updatedCount": 1, "activatedCount": 1, "quarantinedCount": 0, "mergeSuggestionCount": 0},
        ), patch(
            "agents.runners.maintenance_runner.knowledge_db.maintenance_compact_knowledge",
            return_value={
                "candidateCount": 2,
                "supersededCount": 1,
                "mergeSuggestionCount": 0,
                "budgetStopped": False,
                "graph": {
                    "relationCandidateCount": 2,
                    "rewiredRelationCount": 1,
                    "orphanedRelationCount": 0,
                    "isolatedEntityCountBefore": 2,
                    "isolatedEntityCount": 0,
                    "prunedIsolatedEntityCount": 2,
                },
            },
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.update_run_metadata",
            side_effect=_capture_update,
        ), patch(
            "agents.runners.maintenance_runner.memory_store_module.memory_store.get_scope_preferences_raw",
            return_value={
                "favorite_shoe_brand": "耐克",
                "favorite_download_path": r"C:\Users\sunny\Downloads",
            },
        ), patch(
            "agents.runners.maintenance_runner.memory_store_module.memory_store.quarantine_global_preference",
            side_effect=lambda key, value, reason, metadata=None: quarantined_preferences.append((key, value, reason)),
        ), patch(
            "agents.runners.maintenance_runner.workflow_memory_service.maintenance_cursor",
            return_value={"cursor_value": "", "last_batch_count": 0, "cycle_count": 0},
        ), patch(
            "agents.runners.maintenance_runner.workflow_memory_service.advance_maintenance_cursor",
            return_value={"cursorValue": "", "cycleCount": 1, "lastBatchCount": 1, "wrapped": True},
        ), patch(
            "agents.runners.maintenance_runner.knowledge_db.get_knowledge_maintenance_page",
            return_value={
                "items": [
                    {"id": "kg_global_path", "scope": "global", "fact": r"默认导出目录位于 C:\Users\sunny\Downloads", "category": "workspace"},
                ],
                "nextCursor": "",
                "wrapped": True,
                "batchCount": 1,
            },
        ), patch(
            "agents.runners.maintenance_runner.knowledge_db.quarantine_knowledge",
            side_effect=lambda fact_id: (quarantined_knowledge.append(fact_id) or True),
        ):
            result = asyncio.run(memory_agent_runner.run_maintenance(trigger_source="CRON"))

        self.assertEqual(result["result"]["summary_backfilled_count"], 2)
        self.assertEqual(result["result"]["legacy_summary_backfilled_count"], 1)
        self.assertEqual(result["result"]["workflow_candidate_count"], 3)
        self.assertEqual(result["result"]["workflow_active_hint_count"], 1)
        self.assertEqual(result["result"]["knowledge_superseded_count"], 1)
        self.assertEqual(result["result"]["graph_rewired_relation_count"], 1)
        self.assertEqual(result["result"]["graph_pruned_isolated_entity_count"], 2)
        maintenance_updates = [item for item in update_calls if "memory_maintenance" in item]
        self.assertTrue(maintenance_updates)
        self.assertEqual(maintenance_updates[0]["memory_maintenance"]["summaryBackfilledCount"], 2)
        self.assertEqual(maintenance_updates[0]["memory_maintenance"]["legacySummaryBackfilledCount"], 1)
        self.assertEqual(result["result"]["global_quarantined_preference_count"], 1)
        self.assertEqual(result["result"]["global_quarantined_knowledge_count"], 1)
        self.assertEqual(quarantined_preferences[0][0], "favorite_download_path")
        self.assertEqual(quarantined_knowledge, ["kg_global_path"])
        self.assertEqual(
            maintenance_updates[0]["memory_maintenance"]["touchedRefs"],
            ["memory://week/2026-W16", "memory://month/2026-04"],
        )
        self.assertEqual(maintenance_updates[0]["memory_maintenance"]["workflowCandidateCount"], 3)
        self.assertEqual(maintenance_updates[0]["memory_maintenance"]["knowledgeSupersededCount"], 1)
        self.assertEqual(maintenance_updates[0]["memory_maintenance"]["graphPrunedIsolatedEntityCount"], 2)

    def test_run_maintenance_treats_no_logs_as_skipped_no_op(self):
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
        observations: list[tuple[str, str, dict]] = []

        def _capture_update(_run_id: str, updates: dict) -> None:
            update_calls.append(updates)

        async def _fake_generate_periodic_summary(**kwargs):
            return {
                "status": "skipped",
                "reason": "no_logs_found",
                "task_kind": "periodic_summary",
                "tier": kwargs["tier"],
                "target_date": kwargs["target_date"].isoformat(),
                "input_content_length": 0,
            }

        def _capture_observation(action: str, status: str, **metadata) -> None:
            observations.append((action, status, metadata))

        with patch("agents.runners.maintenance_runner.memory_runtime.begin_run", return_value=run_handle), patch(
            "agents.runners.maintenance_runner.session_admission_service.acquire",
            return_value=lane_decision,
        ), patch("agents.runners.maintenance_runner.session_admission_service.release"), patch(
            "agents.runners.maintenance_runner.run_service.transition_run"
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.get_memory_map_health",
            side_effect=[
                {"counts": {"missing": 2, "stale": 0}},
                {"counts": {"missing": 2, "stale": 0}},
            ],
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.list_summary_targets",
            return_value=[
                {"memoryRef": "memory://week/2026-W18", "kind": "week", "summaryState": "missing", "latestDay": "2026-04-29"},
                {"memoryRef": "memory://month/2026-05", "kind": "month", "summaryState": "missing", "latestDay": "2026-05-31"},
            ],
        ), patch(
            "agents.runners.maintenance_runner.memory_agent.generate_periodic_summary",
            side_effect=_fake_generate_periodic_summary,
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.backfill_periodic_summaries",
            return_value={"updatedCount": 0, "touchedRefs": []},
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.run_workflow_maintenance",
            return_value={"candidateCount": 0, "updatedCount": 0, "activatedCount": 0, "quarantinedCount": 0, "mergeSuggestionCount": 0},
        ), patch(
            "agents.runners.maintenance_runner.knowledge_db.maintenance_compact_knowledge",
            return_value={
                "candidateCount": 0,
                "supersededCount": 0,
                "mergeSuggestionCount": 0,
                "budgetStopped": False,
                "graph": {
                    "relationCandidateCount": 0,
                    "rewiredRelationCount": 0,
                    "orphanedRelationCount": 0,
                    "isolatedEntityCount": 0,
                },
            },
        ), patch(
            "agents.runners.maintenance_runner.memory_runtime.update_run_metadata",
            side_effect=_capture_update,
        ), patch(
            "agents.runners.maintenance_runner.memory_store_module.memory_store.get_scope_preferences_raw",
            return_value={},
        ), patch(
            "agents.runners.maintenance_runner.knowledge_db.get_all_knowledge",
            return_value=[],
        ), patch(
            "agents.runners.maintenance_runner.log_memory_observation",
            side_effect=_capture_observation,
        ):
            result = asyncio.run(memory_agent_runner.run_maintenance(trigger_source="CRON"))

        self.assertEqual(result["result"]["status"], "no_op")
        self.assertEqual(result["result"]["failed_targets"], [])
        self.assertEqual(len(result["result"]["skipped_targets"]), 2)
        maintenance_updates = [item for item in update_calls if "memory_maintenance" in item]
        self.assertTrue(maintenance_updates)
        maintenance_meta = maintenance_updates[0]["memory_maintenance"]
        self.assertEqual(maintenance_meta["skippedTargetCount"], 2)
        self.assertEqual(maintenance_meta["failedTargetCount"], 0)
        self.assertEqual(maintenance_meta["noOpReason"], "summary_targets_skipped_and_no_compaction_candidates")
        maintenance_observations = [item for item in observations if item[0] == "maintenance"]
        self.assertTrue(maintenance_observations)
        self.assertEqual(maintenance_observations[-1][1], "NO_OP")
        self.assertEqual(maintenance_observations[-1][2]["failedTargetCount"], 0)
        self.assertEqual(maintenance_observations[-1][2]["skippedTargetCount"], 2)


if __name__ == "__main__":
    unittest.main()
