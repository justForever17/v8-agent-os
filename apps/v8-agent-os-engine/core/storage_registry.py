from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


STORAGE_CLASSES = {"canonical", "recoverable", "derived", "cache", "test"}
SNAPSHOT_TTL_SECONDS = 10 * 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _known_engineering_worktree_paths(home: Path) -> tuple[Path, ...]:
    paths: list[Path] = [home / "worktrees"]
    state_path = home / "state.db"
    if state_path.exists():
        try:
            with closing(sqlite3.connect(f"{state_path.resolve(strict=False).as_uri()}?mode=ro", uri=True)) as conn:
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='engineering_worktrees'"
                ).fetchone()
                if table:
                    rows = conn.execute(
                        "SELECT DISTINCT worktree_root FROM engineering_worktrees "
                        "WHERE COALESCE(worktree_root, '') <> '' AND state <> 'cleaned'"
                    ).fetchall()
                    paths.extend(Path(str(row[0])).expanduser() for row in rows if str(row[0] or "").strip())
        except sqlite3.DatabaseError:
            pass
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(os.path.abspath(os.fspath(path))).rstrip("\\/")
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


@dataclass(frozen=True)
class StorageRegistryEntry:
    id: str
    label: str
    classification: str
    paths: tuple[Path, ...]
    auto_delete: bool
    ttl_days: Optional[int]
    max_bytes: Optional[int]
    backup_policy: str
    restore_strategy: str
    cleanup_mode: str = "plan_only"
    sensitive: bool = False
    excludes: tuple[Path, ...] = field(default_factory=tuple)
    managed_by: str = "storage_registry"

    def _backup_mode(self) -> str:
        if self.id == "unclassified":
            return "review_required"
        if self.backup_policy == "none" or self.classification in {"cache", "test", "derived"}:
            return "not_required"
        if self.managed_by == "backup_service":
            return "recovery_source"
        if self.id == "agent_browser_profile":
            return "protected_manual"
        if self.managed_by in {"toolchain_manager", "plugin_manager", "runtime_registry"}:
            return "receipt"
        if self.managed_by in {"database", "knowledge_service", "storage_retention", "artifact_ledger", "memory_runtime", "config_registry"}:
            return "verified_manifest"
        return "subsystem_managed"

    def _restore_mode(self) -> str:
        if self.id == "unclassified":
            return "review_required"
        if self.classification in {"cache", "test", "derived"}:
            return "rebuild"
        if self.id == "agent_browser_profile":
            return "protected_profile"
        if self.managed_by == "backup_service":
            return "manifest_restore"
        if self.managed_by in {"toolchain_manager", "plugin_manager", "runtime_registry"}:
            return "reinstall"
        if self.managed_by in {"database", "knowledge_service", "storage_retention", "artifact_ledger", "memory_runtime", "config_registry"}:
            return "verified_restore"
        return "subsystem_reconcile"

    def public_contract(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "classification": self.classification,
            "autoDelete": self.auto_delete,
            "ttlDays": self.ttl_days,
            "maxBytes": self.max_bytes,
            "backupPolicy": self._backup_mode(),
            "restoreStrategy": self._restore_mode(),
            "cleanupMode": self.cleanup_mode,
            "sensitive": self.sensitive,
            "managedBy": self.managed_by,
        }


class StorageRegistryService:
    """Inventory and safe cleanup policy for all V8OS-owned storage families."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._refreshing: set[str] = set()

    @staticmethod
    def _entries(home: Path) -> List[StorageRegistryEntry]:
        reports = home / "reports"
        longmem_paths = (reports / "longmemeval", reports / "longmemeval_v2")
        memory_index = home / "memory" / ".index"
        entries = [
            StorageRegistryEntry(
                "state_truth", "会话与运行真相", "canonical", (home / "state.db",), False, None, None,
                "online SQLite backup before mutation", "restore state.db from a verified manifest", sensitive=True,
                managed_by="database",
            ),
            StorageRegistryEntry(
                "knowledge_truth", "长期知识真相", "canonical", (memory_index / "knowledge.db",), False, None, None,
                "online SQLite backup before migration", "restore knowledge.db and rebuild projections", sensitive=True,
                managed_by="knowledge_service",
            ),
            StorageRegistryEntry(
                "checkpoints", "任务恢复点", "recoverable", (home / "checkpoints.db",), True, None, None,
                "online SQLite backup before retention", "restore checkpoints.db and verify latest resume hash",
                cleanup_mode="checkpoint_policy", managed_by="storage_retention",
            ),
            StorageRegistryEntry(
                "observability", "运行诊断记录", "derived", (home / "observability.db", home / "logs"), True, 30, 1 * 1024**3,
                "not required for rolling diagnostic rows", "regenerated by future runs",
                cleanup_mode="observability_policy", managed_by="storage_retention",
            ),
            StorageRegistryEntry(
                "agent_browser_profile", "Agent 浏览器登录态", "recoverable", (home / "browser-profiles",), False, None, None,
                "manual encrypted profile backup only", "restore the profile directory to the same V8OS home", sensitive=True,
                managed_by="agent_browser",
            ),
            StorageRegistryEntry(
                "cache", "可重建缓存", "cache", (home / "cache", home / "web_fetch"), True, 30, 2 * 1024**3,
                "none", "recreated on demand", cleanup_mode="owned_files_ttl_lru",
            ),
            StorageRegistryEntry(
                "longmemeval_reports", "LongMemEval 测试数据", "test", longmem_paths, True, 30, 2 * 1024**3,
                "none", "rerun the explicit LongMemEval harness", cleanup_mode="owned_files_ttl_lru",
            ),
            StorageRegistryEntry(
                "other_reports", "其他测试与验收报告", "derived", (reports,), True, 90, 2 * 1024**3,
                "none", "rerun the owning harness when reproducible",
                cleanup_mode="owned_files_ttl_lru", excludes=longmem_paths,
            ),
            StorageRegistryEntry(
                "temporary_builds", "临时构建与测试目录", "test", (home / "tmp",), True, 14, 2 * 1024**3,
                "none", "rerun the owning build or test", cleanup_mode="owned_files_ttl_lru",
            ),
            StorageRegistryEntry(
                "backups", "本机恢复备份", "recoverable", (home / "backups",), False, 90, None,
                "the backup set is itself the recovery source", "restore through its manifest and quick-check",
                managed_by="backup_service",
            ),
            StorageRegistryEntry(
                "memory_daily", "记忆日志与周期摘要", "canonical", (home / "memory" / "daily",), False, None, None,
                "file backup with memory migration manifest", "restore files and rebuild memory map",
                managed_by="memory_runtime",
            ),
            StorageRegistryEntry(
                "memory_workflow_exports", "工作流学习导出", "derived", (home / "memory" / "workflows",), True, 365, 512 * 1024**2,
                "none", "regenerate from workflow candidate state", cleanup_mode="owned_files_ttl_lru", managed_by="workflow_retention",
            ),
            StorageRegistryEntry(
                "memory_vectors", "记忆向量投影", "derived", (memory_index / "chroma_db",), False, None, 4 * 1024**3,
                "none", "rebuild from canonical active knowledge", managed_by="knowledge_projection",
            ),
            StorageRegistryEntry(
                "research_experience", "深度调研经验与账本", "recoverable", (home / "runtime-data" / "research",), False, 365, None,
                "include in runtime-data backup when pruning", "restore the research ledger directory",
                managed_by="research_runtime",
            ),
            StorageRegistryEntry(
                "rpa_history", "自动流程历史版本", "recoverable", (home / "rpa", home / "runtime-data" / "rpa"), False, 365, None,
                "include template history before manual purge", "restore template history and reconcile active revision",
                managed_by="rpa_runtime",
            ),
            StorageRegistryEntry(
                "toolchains", "本机运行工具链", "recoverable", (home / "toolchains",), False, None, None,
                "record versions and receipts", "reinstall pinned toolchain receipts",
                managed_by="toolchain_manager",
            ),
            StorageRegistryEntry(
                "plugins", "已安装插件", "recoverable", (home / "plugins", home / "bin"), False, None, None,
                "plugin receipt and managed directory backup", "reconcile or reinstall pinned plugin receipts",
                managed_by="plugin_manager",
            ),
            StorageRegistryEntry(
                "artifacts", "用户可见产物", "canonical", (home / "artifacts",), False, None, None,
                "workspace or artifact backup before manual purge", "restore through artifact refs",
                managed_by="artifact_ledger",
            ),
            StorageRegistryEntry(
                "configuration", "配置、凭据引用与用户档案", "canonical",
                (
                    home / "config.json", home / "computer_use.json", home / "mcp.json",
                    home / "users.json", home / "keys", home / "secrets", home / "core",
                    home / "agents", home / "commands", home / "todos",
                ),
                False, None, None,
                "configuration backup with secret-safe manifest", "restore config and credential references together",
                sensitive=True, managed_by="config_registry",
            ),
            StorageRegistryEntry(
                "runtime_control", "运行控制与会话辅助状态", "recoverable",
                (home / "runtime", home / "sessions", home / "workspace"),
                False, 90, None,
                "include required descriptors in runtime backup", "restart Engine and reconcile runtime descriptors",
                sensitive=True,
                excludes=(home / "runtime" / "sandboxes", home / "runtime" / "managed-git"),
                managed_by="runtime_fabric",
            ),
            StorageRegistryEntry(
                "engineering_worktrees", "工程隔离工作树", "recoverable",
                _known_engineering_worktree_paths(home), True, 7, None,
                "preserve active and unaccepted Git change-set refs",
                "recreate from the recorded base and candidate commits",
                cleanup_mode="engineering_worktree_state",
                sensitive=True,
                managed_by="engineering_sandbox",
            ),
            StorageRegistryEntry(
                "engineering_sandbox_runtime", "工程沙箱运行策略", "derived",
                (home / "runtime" / "sandboxes", home / "runtime" / "managed-git"),
                True, 7, 512 * 1024**2,
                "none", "rebuild from active worktree and lease state",
                cleanup_mode="engineering_sandbox_state",
                sensitive=True,
                managed_by="engineering_sandbox",
            ),
            StorageRegistryEntry(
                "runtime_assets", "运行时资源与识别数据", "recoverable",
                (home / "creative_media", home / "tessdata"),
                False, None, None,
                "record installed asset versions", "reinstall or restore pinned runtime assets",
                managed_by="runtime_registry",
            ),
        ]
        registered_paths = tuple(path for entry in entries for path in entry.paths)
        entries.append(
            StorageRegistryEntry(
                "unclassified", "待归类本机数据", "recoverable", (home,), False, None, None,
                "review before assigning a cleanup policy", "restore according to the owning subsystem",
                sensitive=True, excludes=registered_paths, managed_by="storage_registry",
            )
        )
        return entries

    @staticmethod
    def _snapshot_path(home: Path) -> Path:
        return home / "runtime" / "storage-registry-snapshot.json"

    @staticmethod
    def _is_excluded(path: Path, excludes: Iterable[Path]) -> bool:
        candidate = path.resolve(strict=False)
        for excluded in excludes:
            root = excluded.resolve(strict=False)
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _normalized_path(path: Path | str) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path))).rstrip("\\/")

    @classmethod
    def _is_excluded_normalized(cls, path: Path | str, excluded_roots: tuple[str, ...]) -> bool:
        candidate = cls._normalized_path(path)
        for root in excluded_roots:
            if candidate == root or candidate.startswith(root + os.sep):
                return True
        return False

    def _iter_files(self, entry: StorageRegistryEntry) -> Iterable[Path]:
        seen: set[str] = set()
        excluded_roots = tuple(self._normalized_path(path) for path in entry.excludes)
        for root in entry.paths:
            if not root.exists():
                continue
            if root.is_file():
                key = self._normalized_path(root)
                if key not in seen:
                    seen.add(key)
                    yield root
                continue
            stack = [root]
            while stack:
                current = stack.pop()
                try:
                    with os.scandir(current) as iterator:
                        for item in iterator:
                            path = Path(item.path)
                            if excluded_roots and self._is_excluded_normalized(path, excluded_roots):
                                continue
                            try:
                                if item.is_symlink():
                                    continue
                                if item.is_dir(follow_symlinks=False):
                                    stack.append(path)
                                elif item.is_file(follow_symlinks=False):
                                    key = self._normalized_path(path)
                                    if key not in seen:
                                        seen.add(key)
                                        yield path
                            except OSError:
                                continue
                except OSError:
                    continue

    @staticmethod
    def _retention_timestamp(entry: StorageRegistryEntry, stat: os.stat_result) -> float:
        # Cache is access-sensitive. Test and derived data use modification time
        # so inventory/report reads cannot indefinitely renew their retention.
        if entry.classification == "cache":
            return max(float(stat.st_atime or 0), float(stat.st_mtime or 0))
        return float(stat.st_mtime or 0)

    def _scan_entry(self, entry: StorageRegistryEntry) -> Dict[str, Any]:
        total = 0
        count = 0
        latest_access = 0.0
        expired_bytes = 0
        expired_files = 0
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(days=entry.ttl_days)).timestamp()
            if entry.ttl_days is not None
            else None
        )
        for path in self._iter_files(entry):
            try:
                stat = path.stat()
            except OSError:
                continue
            total += int(stat.st_size)
            count += 1
            accessed = self._retention_timestamp(entry, stat)
            latest_access = max(latest_access, accessed)
            if cutoff is not None and accessed and accessed < cutoff:
                expired_files += 1
                expired_bytes += int(stat.st_size)
        over_capacity_bytes = max(0, total - int(entry.max_bytes or 0)) if entry.max_bytes is not None else 0
        requires_action = bool(expired_files or over_capacity_bytes)
        public = entry.public_contract()
        public.update(
            {
                "bytes": total,
                "fileCount": count,
                "lastAccessAt": datetime.fromtimestamp(latest_access, tz=timezone.utc).isoformat().replace("+00:00", "Z") if latest_access else None,
                "expiredBytes": expired_bytes,
                "expiredFileCount": expired_files,
                "overCapacityBytes": over_capacity_bytes,
                "policyState": (
                    "cleanup_available"
                    if requires_action and entry.auto_delete
                    else "review_required"
                    if requires_action
                    else "within_policy"
                ),
                "scanState": "ready",
            }
        )
        return public

    def refresh_snapshot(self, *, home: Path) -> Dict[str, Any]:
        started = time.perf_counter()
        entries = [self._scan_entry(entry) for entry in self._entries(home)]
        disk = os.statvfs(home) if hasattr(os, "statvfs") else None
        payload = {
            "version": 1,
            "generatedAt": _utc_now_iso(),
            "scanDurationMs": round((time.perf_counter() - started) * 1000, 2),
            "entries": entries,
            "registeredBytes": sum(int(item.get("bytes") or 0) for item in entries),
            "classTotals": {
                classification: sum(int(item.get("bytes") or 0) for item in entries if item.get("classification") == classification)
                for classification in sorted(STORAGE_CLASSES)
            },
        }
        if disk is not None:
            payload["disk"] = {
                "totalBytes": int(disk.f_blocks * disk.f_frsize),
                "freeBytes": int(disk.f_bavail * disk.f_frsize),
            }
        _atomic_json(self._snapshot_path(home), payload)
        return payload

    def _schedule_refresh(self, home: Path) -> None:
        key = str(home.resolve(strict=False)).lower()
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def _worker() -> None:
            try:
                self.refresh_snapshot(home=home)
            except Exception:
                # Inventory refresh is advisory and must never break Engine startup.
                pass
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        threading.Thread(target=_worker, name="storage-registry-scan", daemon=True).start()

    def snapshot(self, *, home: Path, refresh: bool = False, schedule_refresh: bool = True) -> Dict[str, Any]:
        if refresh:
            return self.refresh_snapshot(home=home)
        path = self._snapshot_path(home)
        payload: Dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        generated_at = _parse_time(payload.get("generatedAt"))
        stale = generated_at is None or (datetime.now(timezone.utc) - generated_at).total_seconds() > SNAPSHOT_TTL_SECONDS
        if stale and schedule_refresh:
            self._schedule_refresh(home)
        if payload:
            return {**payload, "stale": stale, "refreshScheduled": bool(stale and schedule_refresh)}
        return {
            "version": 1,
            "generatedAt": None,
            "stale": True,
            "refreshScheduled": bool(schedule_refresh),
            "entries": [
                {**entry.public_contract(), "bytes": None, "fileCount": None, "lastAccessAt": None, "scanState": "pending"}
                for entry in self._entries(home)
            ],
            "registeredBytes": None,
            "classTotals": {},
        }

    def build_cleanup_plan(
        self,
        *,
        home: Path,
        max_files: int = 5000,
        pressure_bytes: int = 0,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        actions: List[Dict[str, Any]] = []
        entry_summaries: List[Dict[str, Any]] = []
        pressure_pool: List[tuple[float, StorageRegistryEntry, Path, int]] = []
        selected_paths: set[str] = set()
        for entry in self._entries(home):
            if not entry.auto_delete or entry.cleanup_mode != "owned_files_ttl_lru":
                continue
            files: List[tuple[Path, int, float]] = []
            total = 0
            for path in self._iter_files(entry):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                access = self._retention_timestamp(entry, stat)
                size = int(stat.st_size)
                total += size
                files.append((path, size, access))
            selected: Dict[str, Dict[str, Any]] = {}
            if entry.ttl_days is not None:
                cutoff = (now - timedelta(days=entry.ttl_days)).timestamp()
                for path, size, access in files:
                    if access and access < cutoff:
                        selected[self._normalized_path(path)] = {
                            "entryId": entry.id,
                            "path": str(path),
                            "bytes": size,
                            "reason": "ttl_expired",
                        }
            projected = total - sum(int(item["bytes"]) for item in selected.values())
            if entry.max_bytes is not None and projected > entry.max_bytes:
                for path, size, _access in sorted(files, key=lambda item: item[2]):
                    key = self._normalized_path(path)
                    if key in selected:
                        continue
                    selected[key] = {"entryId": entry.id, "path": str(path), "bytes": size, "reason": "capacity_lru"}
                    projected -= size
                    if projected <= entry.max_bytes:
                        break
            entry_actions = sorted(
                selected.values(),
                key=lambda item: self._normalized_path(str(item.get("path") or "")),
            )
            remaining_slots = max(0, max_files - len(actions))
            accepted_actions = entry_actions[:remaining_slots]
            actions.extend(accepted_actions)
            selected_paths.update(
                self._normalized_path(str(item.get("path") or "")) for item in accepted_actions
            )
            for path, size, access in files:
                if self._normalized_path(path) not in selected_paths:
                    pressure_pool.append((access, entry, path, size))
            entry_summaries.append(
                {
                    "entryId": entry.id,
                    "currentBytes": total,
                    "candidateBytes": sum(int(item["bytes"]) for item in entry_actions),
                    "candidateFiles": len(entry_actions),
                }
            )
            if len(actions) >= max_files:
                break
        pressure_target = max(0, int(pressure_bytes or 0))
        pressure_remaining = max(
            0,
            pressure_target - sum(int(item.get("bytes") or 0) for item in actions),
        )
        if pressure_remaining > 0 and len(actions) < max_files:
            for _access, entry, path, size in sorted(
                pressure_pool,
                key=lambda item: (item[0], self._normalized_path(item[2])),
            ):
                key = self._normalized_path(path)
                if key in selected_paths:
                    continue
                actions.append(
                    {
                        "entryId": entry.id,
                        "path": str(path),
                        "bytes": size,
                        "reason": "disk_pressure_lru",
                    }
                )
                selected_paths.add(key)
                pressure_remaining = max(0, pressure_remaining - size)
                if pressure_remaining <= 0 or len(actions) >= max_files:
                    break
        digest = hashlib.sha256(json.dumps(actions, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return {
            "mode": "plan",
            "planDigest": digest,
            "createdAt": _utc_now_iso(),
            "actions": actions,
            "candidateBytes": sum(int(item.get("bytes") or 0) for item in actions),
            "candidateFiles": len(actions),
            "pressureTargetBytes": pressure_target,
            "pressureRemainingBytes": pressure_remaining,
            "entries": entry_summaries,
        }

    def apply_cleanup_plan(self, *, home: Path, plan: Dict[str, Any]) -> Dict[str, Any]:
        expected = self.build_cleanup_plan(
            home=home,
            max_files=max(1, len(list(plan.get("actions") or []))),
            pressure_bytes=max(0, int(plan.get("pressureTargetBytes") or 0)),
        )
        if str(expected.get("planDigest") or "") != str(plan.get("planDigest") or ""):
            raise ValueError("storage cleanup plan changed; generate a new plan")
        entries = {entry.id: entry for entry in self._entries(home)}
        removed_files = 0
        removed_bytes = 0
        failures: List[Dict[str, str]] = []
        for action in list(plan.get("actions") or []):
            entry = entries.get(str(action.get("entryId") or ""))
            if not entry or not entry.auto_delete or entry.cleanup_mode != "owned_files_ttl_lru":
                failures.append({"path": str(action.get("path") or ""), "reason": "entry_not_auto_deletable"})
                continue
            path = Path(str(action.get("path") or ""))
            resolved = path.resolve(strict=False)
            allowed = False
            for root in entry.paths:
                try:
                    resolved.relative_to(root.resolve(strict=False))
                    allowed = True
                    break
                except ValueError:
                    continue
            if not allowed or self._is_excluded(resolved, entry.excludes):
                failures.append({"path": str(path), "reason": "path_outside_owned_root"})
                continue
            try:
                if path.is_file() and not path.is_symlink():
                    size = int(path.stat().st_size)
                    path.unlink()
                    removed_files += 1
                    removed_bytes += size
            except OSError as exc:
                failures.append({"path": str(path), "reason": str(exc)})
        # Invalidate the cached inventory. The next normal stats read schedules
        # a refresh for the canonical home; cleanup itself must not leave a
        # background writer racing temporary workspaces or shutdown.
        self._snapshot_path(home).unlink(missing_ok=True)
        return {
            "mode": "apply",
            "status": "completed" if not failures else "partial",
            "removedFiles": removed_files,
            "removedBytes": removed_bytes,
            "failures": failures[:50],
            "planDigest": plan.get("planDigest"),
        }


storage_registry_service = StorageRegistryService()
