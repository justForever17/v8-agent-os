from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.storage import STRUCTURED_CONFIG_DEFAULTS, storage
from core.v8_agent_os_paths import CONFIG_JSON_PATH, V8_AGENT_OS_HOME


MIGRATION_LEDGER_PATH = V8_AGENT_OS_HOME / "config_migration_ledger.json"
MIGRATION_BACKUP_ROOT = V8_AGENT_OS_HOME / "backups" / "config_migrations"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _flatten_diff(before: Any, after: Any, *, prefix: str = "") -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            changes.extend(_flatten_diff(before.get(key), after.get(key), prefix=next_prefix))
        return changes
    if before != after:
        changes.append({"path": prefix, "before": before, "after": after})
    return changes


class ConfigMigrationService:
    """User-readable config migration plans with backup and rollback."""

    def list_ledger(self) -> dict[str, Any]:
        payload = _read_json(MIGRATION_LEDGER_PATH, {"migrations": []})
        migrations = list(payload.get("migrations") or []) if isinstance(payload, dict) else []
        return {"ledgerPath": str(MIGRATION_LEDGER_PATH), "migrations": migrations}

    def build_plan(self, *, target: str = "storage_retention_disk_watermark") -> dict[str, Any]:
        raw_config = storage._read_raw_config_payload()  # pylint: disable=protected-access
        if target != "storage_retention_disk_watermark":
            return {
                "target": target,
                "status": "unsupported_target",
                "changes": [],
                "reason": "Unknown migration target.",
            }
        current = dict(raw_config.get("storageRetention") or {})
        normalized_current = storage.get_storage_retention_config()
        desired = storage._normalize_storage_retention_config(  # pylint: disable=protected-access
            {
                **normalized_current,
                "budgets": STRUCTURED_CONFIG_DEFAULTS["storageRetention"].get("budgets") or {},
            }
        )
        before = current
        changes = _flatten_diff(before, desired, prefix="storageRetention")
        return {
            "target": target,
            "status": "no_changes" if not changes else "ready",
            "reason": "Replace the legacy total-size cap with disk-watermark and storage-class governance.",
            "runtimeImpact": ["operations", "observability", "storage_retention"],
            "reversible": True,
            "changes": changes,
            "before": before,
            "after": desired,
        }

    def apply_plan(self, *, target: str = "storage_retention_disk_watermark", reason: str = "admin_migration") -> dict[str, Any]:
        plan = self.build_plan(target=target)
        if plan.get("status") not in {"ready", "no_changes"}:
            return {"status": "skipped", "plan": plan}
        migration_id = f"cfgmig_{uuid.uuid4().hex[:12]}"
        backup_dir = MIGRATION_BACKUP_ROOT / migration_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "config.json"
        if CONFIG_JSON_PATH.exists():
            shutil.copy2(CONFIG_JSON_PATH, backup_path)
        else:
            _write_json(backup_path, {})
        if plan.get("changes"):
            payload = storage._read_config_payload()  # pylint: disable=protected-access
            payload["storageRetention"] = plan.get("after") or {}
            storage._write_config_payload(payload)  # pylint: disable=protected-access
        entry = {
            "id": migration_id,
            "target": target,
            "status": "applied",
            "reason": reason,
            "createdAt": _utc_now(),
            "backupPath": str(backup_path),
            "changes": plan.get("changes") or [],
            "runtimeImpact": plan.get("runtimeImpact") or [],
            "reversible": True,
        }
        ledger = self.list_ledger()
        migrations = list(ledger.get("migrations") or [])
        migrations.insert(0, entry)
        _write_json(MIGRATION_LEDGER_PATH, {"migrations": migrations[:100]})
        return {"status": "applied", "migration": entry, "plan": plan}

    def rollback(self, migration_id: str) -> dict[str, Any]:
        ledger = self.list_ledger()
        migrations = list(ledger.get("migrations") or [])
        entry = next((item for item in migrations if str(item.get("id") or "") == str(migration_id)), None)
        if not entry:
            return {"status": "not_found", "migrationId": migration_id}
        backup_path = Path(str(entry.get("backupPath") or ""))
        if not backup_path.exists():
            return {"status": "backup_missing", "migrationId": migration_id, "backupPath": str(backup_path)}
        CONFIG_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, CONFIG_JSON_PATH)
        entry["status"] = "rolled_back"
        entry["rolledBackAt"] = _utc_now()
        _write_json(MIGRATION_LEDGER_PATH, {"migrations": migrations[:100]})
        return {"status": "rolled_back", "migration": entry}


config_migration_service = ConfigMigrationService()
