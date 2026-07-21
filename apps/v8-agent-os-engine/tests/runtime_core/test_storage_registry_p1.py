from __future__ import annotations

import os
import time
from pathlib import Path

from core.storage_registry import StorageRegistryService


def _write(path: Path, size: int, *, age_days: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        timestamp = time.time() - age_days * 24 * 60 * 60
        os.utime(path, (timestamp, timestamp))


def test_registry_classifies_all_storage_families_without_double_counting_reports(tmp_path: Path) -> None:
    service = StorageRegistryService()
    _write(tmp_path / "state.db", 101)
    _write(tmp_path / "reports" / "longmemeval_v2" / "dataset.json", 211)
    _write(tmp_path / "reports" / "live_engine_logs" / "result.json", 307)
    _write(tmp_path / "browser-profiles" / "computer_use" / "chrome" / "Cookies", 401)
    _write(tmp_path / "cache" / "catalog.json", 503)
    _write(tmp_path / "backups" / "manifest.json", 601)

    snapshot = service.refresh_snapshot(home=tmp_path)
    by_id = {entry["id"]: entry for entry in snapshot["entries"]}

    assert by_id["state_truth"]["classification"] == "canonical"
    assert by_id["agent_browser_profile"]["classification"] == "recoverable"
    assert by_id["cache"]["classification"] == "cache"
    assert by_id["longmemeval_reports"]["classification"] == "test"
    assert by_id["backups"]["autoDelete"] is False
    assert by_id["state_truth"]["backupPolicy"] == "verified_manifest"
    assert by_id["cache"]["restoreStrategy"] == "rebuild"
    assert by_id["longmemeval_reports"]["bytes"] == 211
    assert by_id["other_reports"]["bytes"] == 307
    assert snapshot["classTotals"]["canonical"] >= 101
    assert snapshot["registeredBytes"] == sum(int(entry["bytes"] or 0) for entry in snapshot["entries"])


def test_registry_cleanup_only_removes_owned_expired_cache_and_test_files(tmp_path: Path) -> None:
    service = StorageRegistryService()
    canonical = tmp_path / "state.db"
    old_cache = tmp_path / "cache" / "old.bin"
    fresh_cache = tmp_path / "cache" / "fresh.bin"
    old_longmem = tmp_path / "reports" / "longmemeval_v2" / "old.json"
    old_other_report = tmp_path / "reports" / "live_engine_logs" / "old.json"
    old_backup = tmp_path / "backups" / "old.db"
    _write(canonical, 101, age_days=365)
    _write(old_cache, 211, age_days=60)
    _write(fresh_cache, 307, age_days=1)
    _write(old_longmem, 401, age_days=60)
    _write(old_other_report, 503, age_days=365)
    _write(old_backup, 601, age_days=365)

    plan = service.build_cleanup_plan(home=tmp_path)
    repeated_plan = service.build_cleanup_plan(home=tmp_path)
    planned_paths = {Path(action["path"]) for action in plan["actions"]}

    assert repeated_plan["planDigest"] == plan["planDigest"]
    assert old_cache in planned_paths
    assert old_longmem in planned_paths
    assert canonical not in planned_paths
    assert fresh_cache not in planned_paths
    assert old_other_report in planned_paths
    assert old_backup not in planned_paths

    result = service.apply_cleanup_plan(home=tmp_path, plan=plan)

    assert result["status"] == "completed"
    assert result["removedFiles"] == 3
    assert not old_cache.exists()
    assert not old_longmem.exists()
    assert canonical.exists()
    assert fresh_cache.exists()
    assert not old_other_report.exists()
    assert old_backup.exists()

    refreshed = service.refresh_snapshot(home=tmp_path)
    by_id = {entry["id"]: entry for entry in refreshed["entries"]}
    assert by_id["backups"]["policyState"] == "review_required"
    assert by_id["backups"]["expiredFileCount"] == 1


def test_disk_pressure_lru_only_selects_disposable_storage_classes(tmp_path: Path) -> None:
    service = StorageRegistryService()
    canonical = tmp_path / "state.db"
    recoverable = tmp_path / "backups" / "latest.db"
    oldest_cache = tmp_path / "cache" / "oldest.bin"
    recent_test = tmp_path / "tmp" / "recent.bin"
    _write(canonical, 101, age_days=365)
    _write(recoverable, 211, age_days=365)
    _write(oldest_cache, 307, age_days=2)
    _write(recent_test, 401, age_days=1)

    plan = service.build_cleanup_plan(home=tmp_path, pressure_bytes=500)
    planned = {Path(action["path"]): action["reason"] for action in plan["actions"]}

    assert planned[oldest_cache] == "disk_pressure_lru"
    assert planned[recent_test] == "disk_pressure_lru"
    assert canonical not in planned
    assert recoverable not in planned
    assert plan["pressureTargetBytes"] == 500
    assert plan["pressureRemainingBytes"] == 0

    result = service.apply_cleanup_plan(home=tmp_path, plan=plan)

    assert result["status"] == "completed"
    assert not oldest_cache.exists()
    assert not recent_test.exists()
    assert canonical.exists()
    assert recoverable.exists()


def test_snapshot_can_return_pending_contract_without_blocking_first_request(tmp_path: Path) -> None:
    service = StorageRegistryService()
    payload = service.snapshot(home=tmp_path, refresh=False, schedule_refresh=False)

    assert payload["stale"] is True
    assert payload["refreshScheduled"] is False
    assert payload["entries"]
    assert all(entry["scanState"] == "pending" for entry in payload["entries"])
    assert all(entry["classification"] in {"canonical", "recoverable", "derived", "cache", "test"} for entry in payload["entries"])
