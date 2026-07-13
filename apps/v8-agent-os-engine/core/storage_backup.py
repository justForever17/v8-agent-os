from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
from typing import Any, Dict, Iterable, Optional

from core.v8_agent_os_paths import V8_AGENT_OS_HOME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return int(path.stat().st_size)
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += int(item.stat().st_size)
    return total


def sqlite_quick_check(path: Path) -> str:
    if not path.exists():
        return "missing"
    with closing(sqlite3.connect(path, timeout=60)) as conn:
        row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0] if row else "unknown")


def sqlite_schema_version(path: Path) -> int:
    if not path.exists():
        return 0
    with closing(sqlite3.connect(path, timeout=60)) as conn:
        row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0] if row else 0)


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_directory(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return digest.hexdigest()
    for item in sorted((candidate for candidate in path.rglob("*") if candidate.is_file()), key=lambda value: value.as_posix()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(_sha256_file(item).encode("ascii"))
    return digest.hexdigest()


class StorageBackupService:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or (V8_AGENT_OS_HOME / "backups")

    def preflight(self, *, sqlite_paths: Iterable[Path], directory_paths: Iterable[Path] = ()) -> Dict[str, Any]:
        sqlite_items = [Path(path) for path in sqlite_paths if Path(path).exists()]
        directory_items = [Path(path) for path in directory_paths]
        required = sum(int(path.stat().st_size) for path in sqlite_items) + sum(
            _directory_size(path) for path in directory_items if path.exists()
        )
        self.root.mkdir(parents=True, exist_ok=True)
        disk = shutil.disk_usage(self.root)
        checks = {str(path): sqlite_quick_check(path) for path in sqlite_items}
        ok = all(value == "ok" for value in checks.values()) and disk.free >= int(required * 1.10) + 256 * 1024 * 1024
        return {
            "ok": ok,
            "requiredBytes": required,
            "freeBytes": int(disk.free),
            "quickChecks": checks,
            "reason": "" if ok else ("quick_check_failed" if any(value != "ok" for value in checks.values()) else "insufficient_backup_space"),
        }

    def create_backup(
        self,
        *,
        purpose: str,
        sqlite_paths: Iterable[Path],
        directory_paths: Iterable[Path] = (),
        plan_digest: str,
        backup_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sqlite_items = [Path(path) for path in sqlite_paths if Path(path).exists()]
        directory_items = [Path(path) for path in directory_paths]
        preflight = self.preflight(sqlite_paths=sqlite_items, directory_paths=directory_items)
        if not preflight["ok"]:
            raise RuntimeError(f"backup preflight failed: {preflight['reason']}")
        normalized_id = backup_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        destination = self.root / purpose / normalized_id
        destination.mkdir(parents=True, exist_ok=False)
        database_entries = []
        directory_entries = []
        try:
            for source in sqlite_items:
                target = destination / "databases" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(source, timeout=60)) as source_conn, closing(sqlite3.connect(target, timeout=60)) as target_conn:
                    source_conn.backup(target_conn)
                quick_check = sqlite_quick_check(target)
                if quick_check != "ok":
                    raise RuntimeError(f"backup quick_check failed for {source.name}: {quick_check}")
                database_entries.append(
                    {
                        "source": str(source),
                        "backup": str(target),
                        "bytes": int(target.stat().st_size),
                        "quickCheck": quick_check,
                        "schemaVersion": sqlite_schema_version(target),
                        "sha256": _sha256_file(target),
                    }
                )
            for source in directory_items:
                if not source.exists():
                    directory_entries.append(
                        {
                            "source": str(source),
                            "backup": None,
                            "bytes": 0,
                            "sha256": None,
                            "existed": False,
                        }
                    )
                    continue
                target = destination / "derived" / source.name
                shutil.copytree(source, target)
                directory_entries.append(
                    {
                        "source": str(source),
                        "backup": str(target),
                        "bytes": _directory_size(target),
                        "sha256": _sha256_directory(target),
                        "existed": True,
                    }
                )
            manifest = {
                "backupId": normalized_id,
                "purpose": purpose,
                "createdAt": _utc_now_iso(),
                "planDigest": plan_digest,
                "preflight": preflight,
                "databases": database_entries,
                "directories": directory_entries,
                "state": "ready",
            }
            _atomic_json(destination / "manifest.json", manifest)
            return {**manifest, "path": str(destination)}
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def restore_backup(self, manifest_path: Path, *, offline: bool) -> Dict[str, Any]:
        if not offline:
            raise RuntimeError("rollback requires offline=True")
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        for item in manifest.get("databases") or []:
            source = Path(str(item["backup"]))
            target = Path(str(item["source"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sqlite_quick_check(target) != "ok":
                raise RuntimeError(f"restored database failed quick_check: {target}")
        for item in manifest.get("directories") or []:
            target = Path(str(item["source"]))
            if not bool(item.get("existed", True)):
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                continue
            source = Path(str(item["backup"]))
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            expected_hash = str(item.get("sha256") or "").strip()
            if expected_hash and _sha256_directory(target) != expected_hash:
                raise RuntimeError(f"restored directory failed hash verification: {target}")
        return {"status": "restored", "backupId": manifest.get("backupId")}


storage_backup_service = StorageBackupService()
