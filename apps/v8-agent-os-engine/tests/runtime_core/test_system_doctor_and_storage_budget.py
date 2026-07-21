from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.config_migration import ConfigMigrationService
from core.storage_retention import StorageRetentionService
import core.config_migration as config_migration_module
import core.storage_retention as storage_retention_module
from core.observability_db import ObservabilityDatabaseManager


def test_storage_retention_stats_include_balanced_budgets(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        monkeypatch.setattr(storage_retention_module, "STATE_DB_PATH", root / "state.db")
        monkeypatch.setattr(storage_retention_module, "CHECKPOINT_DB_PATH", root / "checkpoints.db")
        monkeypatch.setattr(storage_retention_module, "OBSERVABILITY_DB_PATH", root / "observability.db")
        monkeypatch.setattr(storage_retention_module, "PLUGIN_MANAGER_LOG_ROOT", root / "logs" / "plugins")
        monkeypatch.setattr(storage_retention_module, "RUNTIME_DATA_HOME", root / "runtime-data")
        monkeypatch.setattr(storage_retention_module, "V8_AGENT_OS_HOME", root)
        monkeypatch.setattr(storage_retention_module, "WORKSPACE_HOME", root / "workspace")
        monkeypatch.setattr(storage_retention_module, "observability_db", ObservabilityDatabaseManager(root / "observability.db"))
        service = StorageRetentionService()
        service.get_config = lambda: {
            "version": 2,
            "enabled": True,
            "policy": "disk_watermark",
            "protectUserVisibleTranscript": True,
            "diskWatermarks": {
                "warningRatio": 0.15,
                "criticalRatio": 0.10,
                "emergencyRatio": 0.05,
                "emergencyFreeBytes": 2 * 1024 * 1024 * 1024,
            },
            "budgets": {
                "logs": {"maxBytes": 200 * 1024 * 1024, "mode": "rolling"},
                "checkpoints": {"maxBytes": 4 * 1024 * 1024 * 1024, "mode": "elastic"},
                "rawEvidence": {"maxBytes": 2 * 1024 * 1024 * 1024, "retentionDays": 30, "mode": "rolling"},
                "artifacts": {"maxBytes": 8 * 1024 * 1024 * 1024, "retentionDays": 60, "mode": "manual_prune"},
                "screenshots": {"maxBytes": 2 * 1024 * 1024 * 1024, "retentionDays": 14, "mode": "rolling"},
                "vectorDb": {"maxBytes": 4 * 1024 * 1024 * 1024, "mode": "warn_only"},
            },
        }

        stats = service.build_stats()

        assert stats["budgetComponents"]["logs"]["maxBytes"] == 200 * 1024 * 1024
        assert stats["budgetComponents"]["rawEvidence"]["retentionDays"] == 30
        assert stats["budgetComponents"]["artifacts"]["mode"] == "manual_prune"
        assert stats["budgetComponents"]["vectorDb"]["autoPrune"] is False
        assert stats["budgetComponents"]["checkpoints"]["mode"] == "elastic"


def test_config_migration_plan_has_readable_storage_budget_diff(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config_path = root / "config.json"
        ledger_path = root / "config_migration_ledger.json"
        backup_root = root / "backups"
        config_path.write_text(json.dumps({"storageRetention": {"maxBytes": 200 * 1024 * 1024}}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(config_migration_module, "CONFIG_JSON_PATH", config_path)
        monkeypatch.setattr(config_migration_module, "MIGRATION_LEDGER_PATH", ledger_path)
        monkeypatch.setattr(config_migration_module, "MIGRATION_BACKUP_ROOT", backup_root)

        from core.storage import storage

        original_read_raw = storage._read_raw_config_payload
        original_write = storage._write_config_payload
        original_read = storage._read_config_payload

        storage._read_raw_config_payload = lambda: json.loads(config_path.read_text(encoding="utf-8"))
        storage._read_config_payload = lambda: json.loads(config_path.read_text(encoding="utf-8"))
        storage._write_config_payload = lambda payload: config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            service = ConfigMigrationService()
            plan = service.build_plan()
            legacy_target = service.build_plan(target="storage_retention_balanced")
            result = service.apply_plan(reason="unit_test")
            rollback = service.rollback(result["migration"]["id"])
        finally:
            storage._read_raw_config_payload = original_read_raw
            storage._write_config_payload = original_write
            storage._read_config_payload = original_read

        assert plan["status"] == "ready"
        assert plan["target"] == "storage_retention_disk_watermark"
        assert legacy_target["status"] == "unsupported_target"
        assert any(change["path"].startswith("storageRetention.budgets") for change in plan["changes"])
        assert any(change["path"] == "storageRetention.maxBytes" and change["after"] is None for change in plan["changes"])
        assert plan["after"]["policy"] == "disk_watermark"
        assert "maxBytes" not in plan["after"]
        assert result["migration"]["backupPath"]
        assert rollback["status"] == "rolled_back"
