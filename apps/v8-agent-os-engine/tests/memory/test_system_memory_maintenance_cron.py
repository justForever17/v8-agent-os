from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.cron_manager import CronManager
from core.storage import storage
from core.memory_maintenance_contract import SYSTEM_MEMORY_MAINTENANCE_JOB_ID


class SystemMemoryMaintenanceCronTests(unittest.TestCase):
    def test_scheduler_start_does_not_auto_run_missed_memory_maintenance(self):
        manager = object.__new__(CronManager)
        manager.scheduler = Mock()
        manager.sync_jobs_to_scheduler = Mock()

        manager.start()

        manager.sync_jobs_to_scheduler.assert_called_once_with()
        manager.scheduler.start.assert_called_once_with()

    def test_get_cron_config_injects_system_memory_job(self):
        with patch.object(storage, "read_json", return_value={"jobs": [{"id": "user-job", "name": "User", "cron_expression": "0 9 * * *", "action_type": "agent", "action_target": "supervisor", "enabled": True}]}), patch.object(storage, "write_json") as write_json:
            config = storage.get_cron_config()
        self.assertEqual(config["jobs"][0]["id"], SYSTEM_MEMORY_MAINTENANCE_JOB_ID)
        self.assertEqual(config["jobs"][1]["id"], "user-job")
        write_json.assert_called_once()

    def test_save_cron_config_preserves_system_job_contract(self):
        incoming = {
            "jobs": [
                {
                    "id": SYSTEM_MEMORY_MAINTENANCE_JOB_ID,
                    "name": "Hacked",
                    "cron_expression": "15 4 * * *",
                    "action_type": "command",
                    "action_target": "bad.target",
                    "payload": {"oops": True},
                    "enabled": False,
                },
                {
                    "id": "user-job",
                    "name": "User",
                    "cron_expression": "0 9 * * *",
                    "action_type": "agent",
                    "action_target": "supervisor",
                    "enabled": True,
                },
            ]
        }
        with patch.object(storage, "write_json") as write_json:
            storage.save_cron_config(incoming)
        saved_payload = write_json.call_args.args[1]
        self.assertEqual(saved_payload["jobs"][0]["id"], SYSTEM_MEMORY_MAINTENANCE_JOB_ID)
        self.assertEqual(saved_payload["jobs"][0]["name"], "Memory Maintenance")
        self.assertEqual(saved_payload["jobs"][0]["action_type"], "python")
        self.assertEqual(saved_payload["jobs"][0]["action_target"], "agents.runners.memory_maintenance_job")
        self.assertEqual(saved_payload["jobs"][0]["payload"], {"mode": "full"})
        self.assertEqual(saved_payload["jobs"][0]["cron_expression"], "15 4 * * *")
        self.assertFalse(saved_payload["jobs"][0]["enabled"])
        self.assertEqual(saved_payload["jobs"][1]["id"], "user-job")

if __name__ == "__main__":
    unittest.main()
