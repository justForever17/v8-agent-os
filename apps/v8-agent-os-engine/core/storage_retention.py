from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from core.observability_db import observability_db
from core.storage import storage
from core.storage_backup import StorageBackupService
from core.storage_registry import storage_registry_service
from core.v8_agent_os_paths import (
    CHECKPOINT_DB_PATH,
    OBSERVABILITY_DB_PATH,
    PLUGIN_MANAGER_LOG_ROOT,
    RUNTIME_DATA_HOME,
    STATE_DB_PATH,
    V8_AGENT_OS_HOME,
    WORKSPACE_HOME,
)


RUNNING_RUN_STATUSES = {"queued", "running"}
RECOVERABLE_RUN_STATUSES = {"waiting_approval", "waiting_input", "waiting_external_tool", "paused", "interrupted"}
ACTIVE_RUN_STATUSES = RUNNING_RUN_STATUSES | RECOVERABLE_RUN_STATUSES
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
LOG_FILE_SUFFIXES = {".log", ".jsonl", ".html", ".txt"}
IMAGE_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
STATE_LOG_TABLES = (
    "model_invocation_logs",
    "provider_health_logs",
    "prompt_cache_events",
    "prompt_cache_segments",
    "llm_response_cache",
    "tool_observation_records",
    "conversation_compaction_records",
    "system_audit_log",
)
STATE_LOG_DELETE_TABLES = (
    "prompt_cache_segments",
    "prompt_cache_events",
    "tool_observation_records",
    "conversation_compaction_records",
    "model_invocation_logs",
    "provider_health_logs",
    "llm_response_cache",
    "system_audit_log",
)
DEFAULT_LOG_BUDGET_BYTES = 1 * 1024 * 1024 * 1024
DEFAULT_CHECKPOINT_BUDGET_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_RAW_EVIDENCE_RETENTION_DAYS = 30
RECOVERY_CHECKPOINT_MAX_COUNT = 8
RECOVERY_CHECKPOINT_MAX_BYTES = 256 * 1024 * 1024


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _sqlite_family_size(path: Path) -> int:
    return sum(_file_size(candidate) for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")))


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return _file_size(path)
    total = 0
    try:
        iterator = path.rglob("*")
        for item in iterator:
            if item.is_file():
                total += _file_size(item)
    except OSError:
        return total
    return total


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _retention_journal_path() -> Path:
    return V8_AGENT_OS_HOME / "runtime" / "storage-retention-journal.json"


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class StorageRetentionService:
    """Disk-watermark governance for canonical, recoverable, and disposable storage."""

    def get_config(self) -> Dict[str, Any]:
        return storage.get_storage_retention_config()

    def disk_health(self) -> Dict[str, Any]:
        return self._disk_health()

    @staticmethod
    def _sqlite_payload_bytes(path: Path, tables: Optional[Iterable[str]] = None) -> int:
        if not path.exists():
            return 0
        total = 0
        try:
            with _connect(path) as conn:
                table_names = list(tables or [])
                if not table_names:
                    table_names = [
                        str(row["name"])
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                        ).fetchall()
                    ]
                for table in table_names:
                    if not StorageRetentionService._table_exists(conn, table):
                        continue
                    columns = [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                    if not columns:
                        continue
                    expression = "+".join(
                        f'COALESCE(length(CAST("{column}" AS BLOB)),0)' for column in columns
                    )
                    row = conn.execute(
                        f'SELECT COALESCE(SUM({expression}),0) AS bytes FROM "{table}"'
                    ).fetchone()
                    total += int(row["bytes"] or 0) if row else 0
        except sqlite3.DatabaseError:
            return _sqlite_family_size(path)
        return total

    @staticmethod
    def _checkpoint_payload_bytes() -> int:
        return StorageRetentionService._sqlite_payload_bytes(CHECKPOINT_DB_PATH, ("checkpoints", "writes"))

    def _disk_health(self) -> Dict[str, Any]:
        V8_AGENT_OS_HOME.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(V8_AGENT_OS_HOME)
        free_ratio = usage.free / max(1, usage.total)
        watermarks = dict(self.get_config().get("diskWatermarks") or {})
        warning_ratio = float(watermarks.get("warningRatio") or 0.15)
        critical_ratio = float(watermarks.get("criticalRatio") or 0.10)
        emergency_ratio = float(watermarks.get("emergencyRatio") or 0.05)
        emergency_free_bytes = int(watermarks.get("emergencyFreeBytes") or 2 * 1024 * 1024 * 1024)
        emergency = usage.free < emergency_free_bytes or free_ratio < emergency_ratio
        level = (
            "emergency"
            if emergency
            else "critical"
            if free_ratio < critical_ratio
            else "warning"
            if free_ratio < warning_ratio
            else "healthy"
        )
        return {
            "totalBytes": int(usage.total),
            "freeBytes": int(usage.free),
            "freeRatio": free_ratio,
            "watermark": level,
            "warningRatio": warning_ratio,
            "criticalRatio": critical_ratio,
            "emergencyRatio": emergency_ratio,
            "emergencyFreeBytes": emergency_free_bytes,
            "emergencySafeMode": emergency,
        }

    @staticmethod
    def _disk_pressure_reclaim_target(disk: Dict[str, Any]) -> int:
        """Return the disposable bytes needed to leave the current pressure band."""

        level = str(disk.get("watermark") or "healthy")
        total = max(0, int(disk.get("totalBytes") or 0))
        free = max(0, int(disk.get("freeBytes") or 0))
        if level == "emergency":
            target_ratio = float(disk.get("criticalRatio") or 0.10)
            target_free = max(
                int(total * target_ratio),
                int(disk.get("emergencyFreeBytes") or 0),
            )
        elif level == "critical":
            target_free = int(total * float(disk.get("warningRatio") or 0.15))
        else:
            # Warning pressure applies normal TTL/cap cleanup, but does not
            # evict otherwise-fresh disposable data merely to chase a ratio.
            return 0
        return max(0, target_free - free)

    @staticmethod
    def _journal() -> Dict[str, Any]:
        path = _retention_journal_path()
        if not path.exists():
            return {"state": "idle"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"state": "degraded", "error": "journal_unreadable"}

    @staticmethod
    def _write_journal(payload: Dict[str, Any]) -> None:
        _atomic_json(_retention_journal_path(), payload)

    def _estimate_state_log_payload_bytes(self) -> int:
        if not STATE_DB_PATH.exists():
            return 0
        total = 0
        with _connect(STATE_DB_PATH) as conn:
            for table in ("runtime_snapshots", "runtime_events", *STATE_LOG_TABLES):
                if not self._table_exists(conn, table):
                    continue
                try:
                    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                    expr = "+".join([f'COALESCE(length(CAST("{col}" AS BLOB)),0)' for col in cols])
                    row = conn.execute(f'SELECT COALESCE(SUM({expr}),0) AS bytes FROM "{table}"').fetchone()
                    total += int(row["bytes"] or 0) if row else 0
                except Exception:
                    continue
        return total

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def _log_files(self) -> List[Path]:
        roots = [PLUGIN_MANAGER_LOG_ROOT, RUNTIME_DATA_HOME / "rpa"]
        files: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for item in root.rglob("*"):
                if item.is_file() and (item.suffix.lower() in LOG_FILE_SUFFIXES or "log" in item.name.lower()):
                    files.append(item)
        return files

    def _log_files_size(self) -> int:
        return sum(_file_size(item) for item in self._log_files())

    def _structured_log_bytes(self) -> int:
        return (
            _sqlite_family_size(OBSERVABILITY_DB_PATH)
            + self._estimate_state_log_payload_bytes()
            + self._log_files_size()
        )

    def _artifact_file_bytes(self) -> int:
        paths: set[Path] = set()
        if STATE_DB_PATH.exists():
            try:
                with _connect(STATE_DB_PATH) as conn:
                    if self._table_exists(conn, "runtime_artifacts"):
                        for row in conn.execute("SELECT source_path, workspace_path FROM runtime_artifacts").fetchall():
                            for key in ("source_path", "workspace_path"):
                                raw_path = str(row[key] or "").strip()
                                if not raw_path:
                                    continue
                                path = Path(raw_path).expanduser()
                                if path.exists() and path.is_file():
                                    paths.add(path.resolve())
            except Exception:
                pass
        artifact_roots = [
            WORKSPACE_HOME / ".v8-agent-os" / "artifacts",
            RUNTIME_DATA_HOME / "artifacts",
            V8_AGENT_OS_HOME / "artifacts",
        ]
        total = sum(_file_size(path) for path in paths)
        total += sum(_directory_size(root) for root in artifact_roots if root.exists())
        return total

    def _screenshot_file_bytes(self) -> int:
        roots = [
            RUNTIME_DATA_HOME,
            WORKSPACE_HOME / ".v8-agent-os" / "artifacts",
            V8_AGENT_OS_HOME / "screenshots",
        ]
        total = 0
        for root in roots:
            if not root.exists():
                continue
            try:
                for item in root.rglob("*"):
                    if not item.is_file():
                        continue
                    normalized = str(item).lower()
                    if item.suffix.lower() in IMAGE_FILE_SUFFIXES and (
                        "screenshot" in normalized or "screen" in normalized or "capture" in normalized
                    ):
                        total += _file_size(item)
            except OSError:
                continue
        return total

    def _raw_evidence_bytes(self) -> int:
        if not OBSERVABILITY_DB_PATH.exists():
            return 0
        with _connect(OBSERVABILITY_DB_PATH) as conn:
            if not self._table_exists(conn, "tool_observation_records"):
                return 0
            try:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(
                        COALESCE(raw_chars, 0)
                        + COALESCE(length(CAST(raw_body_text AS BLOB)), 0)
                        + COALESCE(length(CAST(metadata_json AS BLOB)), 0)
                    ), 0) AS bytes
                    FROM tool_observation_records
                    """
                ).fetchone()
                return int(row["bytes"] or 0) if row else 0
            except Exception:
                return 0

    def _vector_db_bytes(self) -> int:
        candidates = [
            V8_AGENT_OS_HOME / "memory" / ".index" / "chroma_db",
            V8_AGENT_OS_HOME / "vector",
            V8_AGENT_OS_HOME / "vectors",
        ]
        return sum(_directory_size(path) for path in candidates if path.exists())

    @staticmethod
    def _knowledge_db_bytes() -> int:
        return _sqlite_family_size(V8_AGENT_OS_HOME / "memory" / ".index" / "knowledge.db")

    def _governed_total_bytes(self) -> int:
        return (
            _sqlite_family_size(OBSERVABILITY_DB_PATH)
            + _sqlite_family_size(CHECKPOINT_DB_PATH)
            + self._estimate_state_log_payload_bytes()
            + self._log_files_size()
        )

    def _governed_logical_bytes(self) -> int:
        observability_payload = self._sqlite_payload_bytes(
            OBSERVABILITY_DB_PATH,
            (
                "execution_logs",
                "model_invocation_logs",
                "provider_health_logs",
                "prompt_cache_events",
                "prompt_cache_segments",
                "llm_response_cache",
                "tool_observation_records",
                "conversation_compaction_records",
                "system_audit_log",
            ),
        )
        return (
            observability_payload
            + self._estimate_state_log_payload_bytes()
            + self._checkpoint_payload_bytes()
            + self._log_files_size()
        )

    def build_stats(self) -> Dict[str, Any]:
        config = self.get_config()
        budgets = dict(config.get("budgets") or {})
        raw_evidence_bytes = self._raw_evidence_bytes()
        artifact_bytes = self._artifact_file_bytes()
        screenshot_bytes = self._screenshot_file_bytes()
        vector_db_bytes = self._vector_db_bytes()
        knowledge_db_bytes = self._knowledge_db_bytes()
        memory_daily_bytes = _directory_size(V8_AGENT_OS_HOME / "memory" / "daily")
        memory_workflow_export_bytes = _directory_size(V8_AGENT_OS_HOME / "memory" / "workflows")
        memory_workflow_db_bytes = self._sqlite_payload_bytes(
            STATE_DB_PATH,
            (
                "memory_workflow_episodes",
                "memory_workflow_candidates",
                "memory_workflow_hint_events",
                "memory_workflow_guide_states",
            ),
        )
        research_experience_bytes = _directory_size(RUNTIME_DATA_HOME / "research")
        memory_auxiliary_bytes = (
            memory_daily_bytes
            + memory_workflow_export_bytes
            + memory_workflow_db_bytes
            + research_experience_bytes
        )
        state_log_payload_bytes = self._estimate_state_log_payload_bytes()
        observability_physical = _sqlite_family_size(OBSERVABILITY_DB_PATH)
        checkpoint_physical = _sqlite_family_size(CHECKPOINT_DB_PATH)
        checkpoint_logical = self._checkpoint_payload_bytes()
        try:
            checkpoint_policy_actions = self._prune_old_checkpoints(dry_run=True)
        except Exception:
            checkpoint_policy_actions = []
        checkpoint_policy = next(
            (item for item in checkpoint_policy_actions if item.get("action") == "prune_old_checkpoints"),
            {},
        )
        checkpoint_safe_reclaimable = int(checkpoint_policy.get("estimatedLogicalBytes") or 0)
        checkpoint_fragmentation = max(0, checkpoint_physical - checkpoint_logical)
        state_physical = _sqlite_family_size(STATE_DB_PATH)
        components = {
            "observabilityDbBytes": observability_physical,
            "checkpointDbBytes": checkpoint_physical,
            "checkpointLogicalBytes": checkpoint_logical,
            "checkpointReclaimableBytes": max(checkpoint_fragmentation, checkpoint_safe_reclaimable),
            "checkpointSafePruneBytes": checkpoint_safe_reclaimable,
            "checkpointSafePruneCount": int(checkpoint_policy.get("checkpoints") or 0),
            "stateLogPayloadBytes": state_log_payload_bytes,
            "pluginRuntimeLogBytes": self._log_files_size(),
            "stateDbBytes": state_physical,
            "protectedUserTranscriptBytes": self._protected_payload_bytes(),
            "rawEvidenceBytes": raw_evidence_bytes,
            "artifactFileBytes": artifact_bytes,
            "screenshotFileBytes": screenshot_bytes,
            "vectorDbBytes": vector_db_bytes,
            "knowledgeDbBytes": knowledge_db_bytes,
            "memoryDailyBytes": memory_daily_bytes,
            "memoryWorkflowExportBytes": memory_workflow_export_bytes,
            "memoryWorkflowDbBytes": memory_workflow_db_bytes,
            "researchExperienceBytes": research_experience_bytes,
            "memoryAuxiliaryBytes": memory_auxiliary_bytes,
        }
        total = self._governed_total_bytes()
        logical_total = (
            checkpoint_logical
            + state_log_payload_bytes
            + self._log_files_size()
            + min(observability_physical, self._raw_evidence_bytes() + self._sqlite_payload_bytes(OBSERVABILITY_DB_PATH, ("execution_logs", "model_invocation_logs", "provider_health_logs", "prompt_cache_events", "prompt_cache_segments", "llm_response_cache", "tool_observation_records", "conversation_compaction_records", "system_audit_log")))
        )
        disk_health = self._disk_health()
        registry_snapshot = storage_registry_service.snapshot(
            home=V8_AGENT_OS_HOME,
            refresh=False,
            schedule_refresh=V8_AGENT_OS_HOME.resolve(strict=False) == (Path.home() / ".v8-agent-os").resolve(strict=False),
        )
        budget_components = {
            "logs": {
                "label": "Logs",
                "usedBytes": self._structured_log_bytes(),
                "maxBytes": int((budgets.get("logs") or {}).get("maxBytes") or DEFAULT_LOG_BUDGET_BYTES),
                "mode": str((budgets.get("logs") or {}).get("mode") or "rolling"),
                "autoPrune": True,
                "classification": "derived",
            },
            "checkpoints": {
                "label": "Checkpoints",
                "usedBytes": checkpoint_logical,
                "maxBytes": int((budgets.get("checkpoints") or {}).get("maxBytes") or DEFAULT_CHECKPOINT_BUDGET_BYTES),
                "mode": "elastic",
                "autoPrune": True,
                "classification": "recoverable",
            },
            "rawEvidence": {
                "label": "Raw evidence",
                "usedBytes": raw_evidence_bytes,
                "maxBytes": int((budgets.get("rawEvidence") or {}).get("maxBytes") or 2 * 1024 * 1024 * 1024),
                "retentionDays": int((budgets.get("rawEvidence") or {}).get("retentionDays") or 30),
                "mode": str((budgets.get("rawEvidence") or {}).get("mode") or "rolling"),
                "autoPrune": False,
                "classification": "derived",
            },
            "artifacts": {
                "label": "Artifacts",
                "usedBytes": artifact_bytes,
                "maxBytes": int((budgets.get("artifacts") or {}).get("maxBytes") or 8 * 1024 * 1024 * 1024),
                "retentionDays": int((budgets.get("artifacts") or {}).get("retentionDays") or 60),
                "mode": str((budgets.get("artifacts") or {}).get("mode") or "manual_prune"),
                "autoPrune": False,
                "classification": "canonical",
            },
            "screenshots": {
                "label": "Screenshots",
                "usedBytes": screenshot_bytes,
                "maxBytes": int((budgets.get("screenshots") or {}).get("maxBytes") or 2 * 1024 * 1024 * 1024),
                "retentionDays": int((budgets.get("screenshots") or {}).get("retentionDays") or 14),
                "mode": str((budgets.get("screenshots") or {}).get("mode") or "rolling"),
                "autoPrune": False,
                "classification": "derived",
            },
            "vectorDb": {
                "label": "Vector DB",
                "usedBytes": vector_db_bytes,
                "maxBytes": int((budgets.get("vectorDb") or {}).get("maxBytes") or 4 * 1024 * 1024 * 1024),
                "mode": str((budgets.get("vectorDb") or {}).get("mode") or "warn_only"),
                "autoPrune": False,
                "classification": "derived",
            },
            "knowledgeTruth": {
                "label": "Canonical knowledge",
                "usedBytes": knowledge_db_bytes,
                "maxBytes": int((budgets.get("knowledgeTruth") or {}).get("maxBytes") or 2 * 1024 * 1024 * 1024),
                "mode": str((budgets.get("knowledgeTruth") or {}).get("mode") or "warn_only"),
                "autoPrune": False,
                "classification": "canonical",
            },
            "memoryAuxiliary": {
                "label": "Memory auxiliary records",
                "usedBytes": memory_auxiliary_bytes,
                "maxBytes": int((budgets.get("memoryAuxiliary") or {}).get("maxBytes") or 2 * 1024 * 1024 * 1024),
                "mode": str((budgets.get("memoryAuxiliary") or {}).get("mode") or "warn_only"),
                "autoPrune": False,
                "classification": "canonical",
            },
        }
        budget_findings = self._budget_findings(budget_components)
        return {
            "config": config,
            "policy": "disk_watermark",
            "totalGovernedBytes": total,
            "totalProductBytes": registry_snapshot.get("registeredBytes"),
            "registeredStorageBytes": registry_snapshot.get("registeredBytes"),
            "storageClassTotals": registry_snapshot.get("classTotals") or {},
            "physicalBytes": total,
            "logicalBytes": logical_total,
            "reclaimableBytes": max(0, total - logical_total - checkpoint_fragmentation)
            + max(checkpoint_fragmentation, checkpoint_safe_reclaimable),
            "components": components,
            "budgets": budgets,
            "budgetComponents": budget_components,
            "budgetFindings": budget_findings,
            "recommendations": self._budget_recommendations(budget_findings),
            "recentRetentionEvents": observability_db.recent_retention_events(limit=10),
            "disk": disk_health,
            "retentionJournal": self._journal(),
            "backupState": self._journal().get("backupState") or "not_started",
            "recoverability": "protected" if self._journal().get("backupManifestPath") else "plan_only",
            "storageRegistry": registry_snapshot,
        }

    @staticmethod
    def _budget_findings(budget_components: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for key, item in budget_components.items():
            used = int(item.get("usedBytes") or 0)
            cap = int(item.get("maxBytes") or 0)
            if cap <= 0:
                continue
            ratio = used / cap
            if ratio >= 1:
                severity = "error" if item.get("mode") in {"hard_rolling", "rolling"} and item.get("autoPrune") else "warning"
            elif ratio >= 0.8:
                severity = "warning"
            else:
                severity = "ok"
            findings.append(
                {
                    "key": key,
                    "label": item.get("label") or key,
                    "severity": severity,
                    "usedBytes": used,
                    "maxBytes": cap,
                    "usageRatio": ratio,
                    "mode": item.get("mode"),
                    "autoPrune": bool(item.get("autoPrune")),
                }
            )
        return findings

    @staticmethod
    def _budget_recommendations(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        for item in findings:
            if item.get("severity") == "ok":
                continue
            key = str(item.get("key") or "")
            if key == "vectorDb":
                action = "manual_vector_cleanup"
                message = "Vector DB is over budget; recommend manual knowledge cleanup or re-index compaction."
            elif key == "knowledgeTruth":
                action = "review_knowledge_growth"
                message = "Canonical knowledge is over budget; review revisions and observations before any advanced purge."
            elif key == "memoryAuxiliary":
                action = "review_memory_auxiliary_growth"
                message = "Memory journals, workflow guides, or experience packs are over budget; review their lifecycle before pruning."
            elif key == "checkpoints":
                action = "run_checkpoint_retention"
                message = "Checkpoints are over budget; inactive graph checkpoints can be pruned while active runs stay protected."
            elif key == "artifacts":
                action = "review_old_artifacts"
                message = "Artifacts are over budget; review old generated files before deleting."
            elif key == "rawEvidence":
                action = "dry_run_raw_evidence_prune"
                message = "Raw evidence is near or over budget; run a dry-run before pruning observation details."
            elif key == "screenshots":
                action = "dry_run_screenshot_prune"
                message = "Screenshots are near or over budget; old screenshots can usually be pruned after artifact handoff."
            else:
                action = "run_storage_retention"
                message = "Logs are near or over budget; hard rolling retention can prune low-risk observability logs."
            recommendations.append({"key": key, "action": action, "message": message})
        return recommendations

    def _protected_payload_bytes(self) -> int:
        if not STATE_DB_PATH.exists():
            return 0
        with _connect(STATE_DB_PATH) as conn:
            total = 0
            for table in ("sessions", "messages", "chat_canonical_messages", "runtime_artifacts"):
                if not self._table_exists(conn, table):
                    continue
                cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                expr = "+".join([f'COALESCE(length(CAST("{col}" AS BLOB)),0)' for col in cols])
                row = conn.execute(f'SELECT COALESCE(SUM({expr}),0) AS bytes FROM "{table}"').fetchone()
                total += int(row["bytes"] or 0) if row else 0
            return total

    def migrate_legacy_logs(self, *, dry_run: bool = False) -> List[Dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        actions.extend(self._migrate_state_observability_tables(dry_run=dry_run))
        actions.extend(self._migrate_knowledge_execution_logs(dry_run=dry_run))
        return actions

    def _migrate_state_observability_tables(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        actions: list[dict[str, Any]] = []
        with observability_db.get_connection() as obs_conn:
            obs_conn.execute("ATTACH DATABASE ? AS state_db", (str(STATE_DB_PATH),))
            try:
                for table in STATE_LOG_TABLES:
                    if not self._table_exists(obs_conn, f"state_db.{table}") and not self._attached_table_exists(obs_conn, "state_db", table):
                        continue
                    count = int(obs_conn.execute(f"SELECT COUNT(*) AS count FROM state_db.{table}").fetchone()["count"] or 0)
                    if count <= 0:
                        continue
                    actions.append({"action": "migrate_state_table", "table": table, "rows": count, "dryRun": dry_run})
                    if not dry_run:
                        main_cols = [row["name"] for row in obs_conn.execute(f"PRAGMA main.table_info({table})").fetchall()]
                        state_cols = [row["name"] for row in obs_conn.execute(f"PRAGMA state_db.table_info({table})").fetchall()]
                        common_cols = [col for col in main_cols if col in state_cols]
                        if common_cols:
                            column_sql = ", ".join(f'"{col}"' for col in common_cols)
                            obs_conn.execute(f"INSERT OR IGNORE INTO main.{table} ({column_sql}) SELECT {column_sql} FROM state_db.{table}")
                if not dry_run:
                    obs_conn.commit()
            finally:
                obs_conn.execute("DETACH DATABASE state_db")
        if not dry_run:
            with _connect(STATE_DB_PATH) as state_conn:
                for table in STATE_LOG_DELETE_TABLES:
                    if self._table_exists(state_conn, table):
                        state_conn.execute(f"DELETE FROM {table}")
                state_conn.commit()
        return actions

    @staticmethod
    def _attached_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
        return bool(conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def _migrate_knowledge_execution_logs(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        knowledge_path = V8_AGENT_OS_HOME / "memory" / ".index" / "knowledge.db"
        if not knowledge_path.exists():
            return []
        with _connect(knowledge_path) as knowledge_conn:
            if not self._table_exists(knowledge_conn, "execution_logs"):
                return []
            count = int(knowledge_conn.execute("SELECT COUNT(*) AS count FROM execution_logs").fetchone()["count"] or 0)
            if count <= 0:
                return []
            if not dry_run:
                with observability_db.get_connection() as obs_conn:
                    obs_conn.execute("ATTACH DATABASE ? AS knowledge_db", (str(knowledge_path),))
                    obs_conn.execute(
                        """
                        INSERT OR IGNORE INTO execution_logs (
                            id, task_name, action_type, action_target, trigger_source, status,
                            started_at, completed_at, duration_ms, error_message, payload
                        )
                        SELECT id, task_name, action_type, action_target, trigger_source, status,
                               started_at, finished_at, duration_ms, error_message, payload
                        FROM knowledge_db.execution_logs
                        """
                    )
                    obs_conn.commit()
                    obs_conn.execute("DETACH DATABASE knowledge_db")
                knowledge_conn.execute("DELETE FROM execution_logs")
                knowledge_conn.commit()
            return [{"action": "migrate_knowledge_execution_logs", "rows": count, "dryRun": dry_run}]

    def enforce(self, *, dry_run: bool = False, reason: str = "manual") -> Dict[str, Any]:
        if dry_run:
            return self._execute_retention(dry_run=True, reason=reason)
        plan = self._execute_retention(dry_run=True, reason=reason)
        digest_payload = {
            "reason": reason,
            "policy": plan.get("policy"),
            "triggerWatermark": plan.get("triggerWatermark"),
            "beforeBytes": plan.get("beforeBytes"),
            "actions": plan.get("actions") or [],
        }
        plan_digest = hashlib.sha256(
            json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        operation_id = f"retention-{uuid.uuid4().hex[:12]}"
        journal = {
            "operationId": operation_id,
            "state": "plan",
            "reason": reason,
            "planDigest": plan_digest,
            "createdAt": _utc_now_iso(),
            "backupState": "not_started",
        }
        self._write_journal(journal)
        if not plan.get("actions"):
            journal.update({"state": "completed", "completedAt": _utc_now_iso(), "backupState": "not_required"})
            self._write_journal(journal)
            return {**plan, "mode": "prune", "status": "completed", "planDigest": plan_digest}
        disk_health = self._disk_health()
        if disk_health["emergencySafeMode"]:
            # Low-space recovery may clear disposable caches/logs, but must not
            # touch checkpoints, transcripts, artifacts, or knowledge truth.
            safe_actions: List[Dict[str, Any]] = []
            safe_actions.extend(self._prune_expired_response_cache(dry_run=False))
            safe_actions.extend(self._prune_observability_logs(dry_run=False))
            safe_actions.extend(self._prune_log_files(dry_run=False))
            registry_plan = storage_registry_service.build_cleanup_plan(
                home=V8_AGENT_OS_HOME,
                pressure_bytes=self._disk_pressure_reclaim_target(disk_health),
            )
            registry_result = storage_registry_service.apply_cleanup_plan(home=V8_AGENT_OS_HOME, plan=registry_plan)
            safe_actions.append({"action": "registry_safe_cleanup", **registry_result})
            journal.update({"state": "blocked", "backupState": "blocked_low_space", "disk": disk_health, "safeActions": safe_actions})
            self._write_journal(journal)
            return {
                **plan,
                "mode": "prune",
                "status": "blocked",
                "errorCode": "emergency_safe_mode",
                "planDigest": plan_digest,
                "disk": disk_health,
                "safeActions": safe_actions,
            }
        journal.update({"state": "backup_preflight", "backupState": "running"})
        self._write_journal(journal)
        try:
            backup = StorageBackupService(root=V8_AGENT_OS_HOME / "backups").create_backup(
                purpose="storage-retention",
                sqlite_paths=(STATE_DB_PATH, CHECKPOINT_DB_PATH, OBSERVABILITY_DB_PATH),
                plan_digest=plan_digest,
                backup_id=operation_id,
            )
        except Exception as exc:
            journal.update({"state": "blocked", "backupState": "failed", "error": str(exc)})
            self._write_journal(journal)
            return {
                **plan,
                "mode": "prune",
                "status": "blocked",
                "errorCode": "backup_failed",
                "error": str(exc),
                "planDigest": plan_digest,
            }
        journal.update(
            {
                "state": "applying",
                "backupState": "ready",
                "backupManifestPath": str(Path(str(backup["path"])) / "manifest.json"),
                "updatedAt": _utc_now_iso(),
            }
        )
        self._write_journal(journal)
        try:
            result = self._execute_retention(dry_run=False, reason=reason)
            journal.update({"state": "verifying", "updatedAt": _utc_now_iso()})
            self._write_journal(journal)
            checks = {
                str(path): self._quick_check(path)
                for path in (STATE_DB_PATH, CHECKPOINT_DB_PATH, OBSERVABILITY_DB_PATH)
                if path.exists()
            }
            if any(value != "ok" for value in checks.values()):
                raise RuntimeError(f"retention verification failed: {checks}")
            journal.update({"state": "completed", "completedAt": _utc_now_iso(), "quickChecks": checks})
            self._write_journal(journal)
            return {**result, "planDigest": plan_digest, "backup": backup, "quickChecks": checks}
        except Exception as exc:
            journal.update({"state": "failed", "error": str(exc), "updatedAt": _utc_now_iso()})
            self._write_journal(journal)
            raise

    def startup_check(self) -> Dict[str, Any]:
        journal = self._journal()
        if journal.get("state") in {"applying", "verifying", "backup_preflight"}:
            journal.update({"state": "recovery_required", "updatedAt": _utc_now_iso()})
            self._write_journal(journal)
        disk = self._disk_health()
        pressure_target = self._disk_pressure_reclaim_target(disk)
        registry_plan = storage_registry_service.build_cleanup_plan(
            home=V8_AGENT_OS_HOME,
            pressure_bytes=pressure_target,
        )
        plan = self._execute_retention(
            dry_run=True,
            reason="engine_startup",
            registry_plan=registry_plan,
        )
        automatic_cleanup: Dict[str, Any] | None = None
        if disk.get("watermark") != "healthy" and registry_plan.get("actions"):
            automatic_cleanup = storage_registry_service.apply_cleanup_plan(
                home=V8_AGENT_OS_HOME,
                plan=registry_plan,
            )
            automatic_cleanup["scope"] = "derived_cache_test_owned_files"
            automatic_cleanup["triggerWatermark"] = disk.get("watermark")
            automatic_cleanup["diskAfter"] = self._disk_health()
            try:
                observability_db.add_retention_event(
                    {
                        "mode": "automatic_disk_pressure",
                        "status": automatic_cleanup.get("status") or "unknown",
                        "max_bytes": 0,
                        "before_bytes": int(registry_plan.get("candidateBytes") or 0),
                        "after_bytes": max(
                            0,
                            int(registry_plan.get("candidateBytes") or 0)
                            - int(automatic_cleanup.get("removedBytes") or 0),
                        ),
                        "actions": [
                            {
                                "action": "registry_disk_pressure_cleanup",
                                "removedFiles": int(automatic_cleanup.get("removedFiles") or 0),
                                "removedBytes": int(automatic_cleanup.get("removedBytes") or 0),
                                "failureCount": len(automatic_cleanup.get("failures") or []),
                            }
                        ],
                        "metadata": {
                            "policy": "disk_watermark",
                            "triggerWatermark": disk.get("watermark"),
                            "scope": automatic_cleanup["scope"],
                            "planDigest": registry_plan.get("planDigest"),
                            "diskFreeBeforeBytes": int(disk.get("freeBytes") or 0),
                            "diskFreeAfterBytes": int(
                                (automatic_cleanup.get("diskAfter") or {}).get("freeBytes") or 0
                            ),
                        },
                    }
                )
                automatic_cleanup["auditState"] = "recorded"
            except Exception as exc:
                automatic_cleanup["auditState"] = "degraded"
                automatic_cleanup["auditError"] = exc.__class__.__name__
        return {
            "status": (
                "auto_cleaned"
                if automatic_cleanup and automatic_cleanup.get("status") == "completed"
                else "auto_cleanup_partial"
                if automatic_cleanup
                else "planned"
            ),
            "journal": journal,
            "plan": plan,
            "automaticCleanup": automatic_cleanup,
        }

    @staticmethod
    def _quick_check(path: Path) -> str:
        with _connect(path) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0] if row else "unknown")

    def compact_physical(self, *, reason: str = "idle_maintenance") -> Dict[str, Any]:
        paths = [path for path in (STATE_DB_PATH, CHECKPOINT_DB_PATH, OBSERVABILITY_DB_PATH) if path.exists()]
        if not paths:
            return {"status": "completed", "before": {}, "after": {}, "backup": None}
        plan_digest = hashlib.sha256(f"physical:{reason}:{_utc_now_iso()}".encode("utf-8")).hexdigest()
        backup = StorageBackupService(root=V8_AGENT_OS_HOME / "backups").create_backup(
            purpose="storage-compaction",
            sqlite_paths=paths,
            plan_digest=plan_digest,
        )
        before = {str(path): _sqlite_family_size(path) for path in paths}
        for path in paths:
            self._vacuum_db(path)
            if self._quick_check(path) != "ok":
                raise RuntimeError(f"physical compaction quick_check failed: {path}")
        after = {str(path): _sqlite_family_size(path) for path in paths}
        return {"status": "completed", "before": before, "after": after, "backup": backup}

    def _execute_retention(
        self,
        *,
        dry_run: bool,
        reason: str,
        registry_plan: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        config = self.get_config()
        budgets = dict(config.get("budgets") or {})
        log_budget = int((budgets.get("logs") or {}).get("maxBytes") or DEFAULT_LOG_BUDGET_BYTES)
        disk_health = self._disk_health()
        before = self._governed_logical_bytes()
        actions = self.migrate_legacy_logs(dry_run=dry_run)
        registry_plan = registry_plan or storage_registry_service.build_cleanup_plan(
            home=V8_AGENT_OS_HOME,
            pressure_bytes=self._disk_pressure_reclaim_target(disk_health),
        )
        if registry_plan.get("actions"):
            if dry_run:
                actions.append(
                    {
                        "action": "registry_safe_cleanup",
                        "dryRun": True,
                        "candidateFiles": int(registry_plan.get("candidateFiles") or 0),
                        "candidateBytes": int(registry_plan.get("candidateBytes") or 0),
                        "planDigest": registry_plan.get("planDigest"),
                        "entries": registry_plan.get("entries") or [],
                    }
                )
            else:
                actions.append(
                    {
                        "action": "registry_safe_cleanup",
                        "dryRun": False,
                        **storage_registry_service.apply_cleanup_plan(home=V8_AGENT_OS_HOME, plan=registry_plan),
                    }
                )
        log_total = self._structured_log_bytes()
        if log_total > log_budget:
            actions.extend(self._prune_expired_response_cache(dry_run=dry_run))
            log_total = self._structured_log_bytes()
        for step in (
            self._prune_observability_logs,
            self._prune_completed_runtime_events,
            self._prune_log_files,
        ):
            while log_total > log_budget:
                step_actions = step(dry_run=dry_run)
                if not step_actions:
                    break
                actions.extend(step_actions)
                if dry_run:
                    break
                log_total = self._structured_log_bytes()
        # Runtime snapshots are derived projections. Their per-session/type
        # lifecycle is enforced independently of disk pressure.
        runtime_snapshot_actions = self._prune_runtime_snapshots(dry_run=dry_run)
        actions.extend(runtime_snapshot_actions)
        if not dry_run:
            for _ in range(20):
                if not runtime_snapshot_actions:
                    break
                runtime_snapshot_actions = self._prune_runtime_snapshots(dry_run=False)
                actions.extend(runtime_snapshot_actions)
        # Direct Canvas runs have no chat run_id, so the generic completed-run
        # event pruning path cannot own their lifecycle. Retire their derived
        # events only after the graph run is terminal and its evidence TTL has
        # elapsed; projected outbox intents are pruned in the following step.
        canvas_event_actions = self._prune_terminal_canvas_runtime_events(dry_run=dry_run)
        actions.extend(canvas_event_actions)
        if not dry_run:
            for _ in range(20):
                if not canvas_event_actions:
                    break
                canvas_event_actions = self._prune_terminal_canvas_runtime_events(dry_run=False)
                actions.extend(canvas_event_actions)
        # Canvas outbox intents remain repairable until their runtime event is
        # projected. Once runtime-event retention removes that projection, the
        # matching projected intent has completed its lifecycle as well.
        canvas_outbox_actions = self._prune_projected_canvas_outbox(dry_run=dry_run)
        actions.extend(canvas_outbox_actions)
        if not dry_run:
            for _ in range(20):
                if not canvas_outbox_actions:
                    break
                canvas_outbox_actions = self._prune_projected_canvas_outbox(dry_run=False)
                actions.extend(canvas_outbox_actions)
        # Session/thread lifecycle retention is a safety invariant, not merely
        # a budget response. Run one complete dry-run plan or drain bounded
        # apply batches before checking whether additional budget pruning is
        # necessary.
        checkpoint_actions = self._prune_old_checkpoints(dry_run=dry_run)
        actions.extend(checkpoint_actions)
        if not dry_run:
            for _ in range(20):
                if not checkpoint_actions:
                    break
                checkpoint_actions = self._prune_old_checkpoints(dry_run=False)
                actions.extend(checkpoint_actions)
        after = self._governed_logical_bytes()
        status = "dry_run" if dry_run else "completed"
        result = {
            "mode": "dry_run" if dry_run else "prune",
            "status": status,
            "reason": reason,
            "policy": "disk_watermark",
            "triggerWatermark": disk_health.get("watermark"),
            "disk": disk_health,
            "beforeBytes": before,
            "afterBytes": after,
            "actions": actions,
            "protected": {
                "messages": True,
                "chatCanonicalMessages": True,
                "runtimeArtifacts": True,
                "memoryDiariesAndSummaries": True,
            },
        }
        if not dry_run:
            observability_db.add_retention_event(
                {
                    "mode": "prune",
                    "status": status,
                    "max_bytes": 0,
                    "before_bytes": before,
                    "after_bytes": after,
                    "actions": actions,
                    "metadata": {
                        "reason": reason,
                        "policy": "disk_watermark",
                        "triggerWatermark": disk_health.get("watermark"),
                    },
                }
            )
        return result

    def _prune_expired_response_cache(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        with observability_db.get_connection() as conn:
            count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM llm_response_cache WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
            ).fetchone()["count"] or 0)
            if count <= 0:
                return []
            if not dry_run:
                conn.execute("DELETE FROM llm_response_cache WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')")
                conn.commit()
            return [{"action": "delete_expired_response_cache", "rows": count, "dryRun": dry_run}]

    def _prune_observability_logs(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        batch = 500
        actions: list[dict[str, Any]] = []
        with observability_db.get_connection() as conn:
            for table, order_col in (
                ("prompt_cache_events", "created_at"),
                ("tool_observation_records", "created_at"),
                ("conversation_compaction_records", "created_at"),
                ("provider_health_logs", "created_at"),
                ("model_invocation_logs", "started_at"),
                ("system_audit_log", "timestamp"),
                ("execution_logs", "started_at"),
            ):
                rows = conn.execute(f"SELECT id FROM {table} ORDER BY {order_col} ASC LIMIT ?", (batch,)).fetchall()
                ids = [row["id"] for row in rows]
                if not ids:
                    continue
                actions.append({"action": "prune_observability_table", "table": table, "rows": len(ids), "dryRun": dry_run})
                if not dry_run:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
                    conn.commit()
                break
        if not dry_run and actions:
            # Physical compaction is a separate idle maintenance operation.
            pass
        return actions

    def _prune_runtime_snapshots(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        with _connect(STATE_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id, snapshot_type
                               ORDER BY latest_seq DESC, created_at DESC, id DESC
                           ) AS rank_no
                    FROM runtime_snapshots
                ) ranked
                WHERE rank_no > 1
                LIMIT 1000
                """
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            if not dry_run:
                conn.execute(f"DELETE FROM runtime_snapshots WHERE id IN ({','.join('?' for _ in ids)})", ids)
                conn.commit()
            return [{"action": "prune_runtime_snapshots", "rows": len(ids), "dryRun": dry_run}]

    def _prune_completed_runtime_events(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        with _connect(STATE_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT re.id
                FROM runtime_events re
                JOIN run_records rr ON rr.id = re.run_id
                WHERE rr.status IN ('completed', 'failed', 'cancelled')
                  AND EXISTS (
                    SELECT 1 FROM chat_canonical_messages ccm
                    WHERE ccm.session_id = re.session_id
                  )
                  AND COALESCE(re.topic, '') NOT LIKE 'chat.message%'
                ORDER BY COALESCE(re.created_at, re.event_ts) ASC
                LIMIT 500
                """
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            if not dry_run:
                conn.execute(f"DELETE FROM runtime_events WHERE id IN ({','.join('?' for _ in ids)})", ids)
                conn.commit()
            return [{"action": "prune_completed_runtime_events", "rows": len(ids), "dryRun": dry_run}]

    def _prune_terminal_canvas_runtime_events(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        budgets = dict(self.get_config().get("budgets") or {})
        retention_days = max(
            1,
            int(
                (budgets.get("rawEvidence") or {}).get("retentionDays")
                or DEFAULT_RAW_EVIDENCE_RETENTION_DAYS
            ),
        )
        cutoff_modifier = f"-{retention_days} days"
        with _connect(STATE_DB_PATH) as conn:
            required_tables = {
                "runtime_events",
                "creative_canvas_graph_runs",
            }
            existing_tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not required_tables.issubset(existing_tables):
                return []
            rows = conn.execute(
                """
                SELECT event.id
                FROM runtime_events event
                WHERE event.run_id IS NULL
                  AND event.topic = 'canvas.graph.run.state'
                  AND json_valid(event.payload_json)
                  AND COALESCE(json_extract(event.payload_json, '$.graphRunId'), '') <> ''
                  AND datetime(COALESCE(event.created_at, event.event_ts)) <= datetime('now', ?)
                  AND EXISTS (
                    SELECT 1
                    FROM creative_canvas_graph_runs graph_run
                    WHERE graph_run.graph_run_id = json_extract(event.payload_json, '$.graphRunId')
                      AND graph_run.session_id = event.session_id
                      AND graph_run.chat_run_id IS NULL
                      AND graph_run.status IN ('succeeded', 'failed', 'cancelled', 'interrupted')
                      AND datetime(COALESCE(graph_run.completed_at, graph_run.updated_at)) <= datetime('now', ?)
                  )
                ORDER BY COALESCE(event.created_at, event.event_ts) ASC, event.seq ASC
                LIMIT 500
                """,
                (cutoff_modifier, cutoff_modifier),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if not ids:
                return []
            if not dry_run:
                conn.execute(
                    f"DELETE FROM runtime_events WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
                conn.commit()
            return [
                {
                    "action": "prune_terminal_canvas_runtime_events",
                    "rows": len(ids),
                    "retentionDays": retention_days,
                    "dryRun": dry_run,
                }
            ]

    def _prune_projected_canvas_outbox(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not STATE_DB_PATH.exists():
            return []
        with _connect(STATE_DB_PATH) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_canvas_graph_run_event_outbox'"
            ).fetchone()
            if not table:
                return []
            rows = conn.execute(
                """
                SELECT outbox_id
                FROM creative_canvas_graph_run_event_outbox outbox
                WHERE outbox.projected_at IS NOT NULL
                  AND outbox.runtime_event_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM runtime_events event
                    WHERE event.id = outbox.runtime_event_id
                  )
                ORDER BY outbox.outbox_sequence ASC
                LIMIT 1000
                """
            ).fetchall()
            ids = [str(row["outbox_id"]) for row in rows]
            if not ids:
                return []
            if not dry_run:
                conn.execute(
                    f"DELETE FROM creative_canvas_graph_run_event_outbox WHERE outbox_id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
                conn.commit()
            return [{"action": "prune_projected_canvas_outbox", "rows": len(ids), "dryRun": dry_run}]

    def _prune_old_checkpoints(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        if not CHECKPOINT_DB_PATH.exists():
            return []
        from erc.checkpoint_security import build_checkpoint_serializer

        serializer = build_checkpoint_serializer()
        policies = self._checkpoint_thread_policies()
        with _connect(CHECKPOINT_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id,
                       c.metadata,
                       COALESCE(length(c.checkpoint), 0) + COALESCE(length(c.metadata), 0)
                       + COALESCE((
                           SELECT SUM(COALESCE(length(w.value), 0)) FROM writes w
                           WHERE w.thread_id = c.thread_id
                             AND w.checkpoint_ns = c.checkpoint_ns
                             AND w.checkpoint_id = c.checkpoint_id
                       ), 0) AS logical_bytes
                FROM checkpoints c
                ORDER BY c.thread_id, c.checkpoint_ns, c.checkpoint_id DESC
                """
            ).fetchall()
            grouped: Dict[tuple[str, str], List[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault((str(row["thread_id"]), str(row["checkpoint_ns"])), []).append(row)
            delete_keys: List[tuple[str, str, str]] = []
            delete_sizes: Dict[tuple[str, str, str], int] = {}
            retained: Dict[tuple[str, str], List[str]] = {}
            recovery_targets: Dict[tuple[str, str], List[str]] = {}
            delta_managed_keys: set[tuple[str, str]] = set()
            anchor_count = 0
            policy_counts = {"running": 0, "recoverable": 0, "idle": 0, "orphan": 0}
            for key, checkpoints in grouped.items():
                thread_id, checkpoint_ns = key
                policy = policies.get(thread_id, "orphan")
                policy_counts[policy] = policy_counts.get(policy, 0) + 1
                if policy == "running":
                    retained[key] = [str(row["checkpoint_id"]) for row in checkpoints]
                    recovery_targets[key] = [str(checkpoints[0]["checkpoint_id"])] if checkpoints else []
                    continue
                target_ids: List[str] = []
                if policy == "recoverable":
                    kept_bytes = 0
                    for index, row in enumerate(checkpoints):
                        size = int(row["logical_bytes"] or 0)
                        if index == 0 or (
                            len(target_ids) < RECOVERY_CHECKPOINT_MAX_COUNT
                            and kept_bytes + size <= RECOVERY_CHECKPOINT_MAX_BYTES
                        ):
                            target_ids.append(str(row["checkpoint_id"]))
                            kept_bytes += size
                elif policy == "idle":
                    target_ids = [str(checkpoints[0]["checkpoint_id"])] if checkpoints else []
                recovery_targets[key] = list(target_ids)
                keep_ids, delta_managed = self._delta_safe_checkpoint_ids(
                    conn,
                    checkpoints,
                    target_ids=target_ids,
                    serializer=serializer,
                )
                if delta_managed:
                    delta_managed_keys.add(key)
                anchor_count += max(0, len(keep_ids) - len(target_ids))
                retained[key] = keep_ids
                keep_set = set(keep_ids)
                for row in checkpoints:
                    checkpoint_id = str(row["checkpoint_id"])
                    if checkpoint_id in keep_set:
                        continue
                    delete_key = (thread_id, checkpoint_ns, checkpoint_id)
                    delete_keys.append(delete_key)
                    delete_sizes[delete_key] = int(row["logical_bytes"] or 0)
            if not delete_keys:
                return []
            total_delete_count = len(delete_keys)
            total_estimated_bytes = sum(delete_sizes[key] for key in delete_keys)
            if not dry_run:
                delete_keys = delete_keys[:2000]
            estimated_bytes = sum(delete_sizes[key] for key in delete_keys)
            delete_set = set(delete_keys)
            if not dry_run:
                latest_before = {
                    (key[0], key[1], checkpoint_id): self._checkpoint_resume_fingerprint(
                        conn,
                        key[0],
                        key[1],
                        checkpoint_id,
                        serializer=serializer,
                    )
                    for key, checkpoint_ids in recovery_targets.items()
                    for checkpoint_id in checkpoint_ids
                }
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.executemany(
                        "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                        delete_keys,
                    )
                    conn.executemany(
                        "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                        delete_keys,
                    )
                    for key, keep_ids in retained.items():
                        if not keep_ids:
                            continue
                        oldest_retained = keep_ids[-1]
                        row = conn.execute(
                            "SELECT parent_checkpoint_id FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                            (key[0], key[1], oldest_retained),
                        ).fetchone()
                        if row and row["parent_checkpoint_id"] and (
                            key[0], key[1], str(row["parent_checkpoint_id"])
                        ) in delete_set:
                            checkpoint_row = conn.execute(
                                "SELECT type, checkpoint, metadata FROM checkpoints "
                                "WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                                (key[0], key[1], oldest_retained),
                            ).fetchone()
                            if key in delta_managed_keys and (
                                not checkpoint_row or not self._checkpoint_is_delta_boundary(
                                    checkpoint_row,
                                    serializer=serializer,
                                )
                            ):
                                raise RuntimeError(
                                    f"retention would sever DeltaChannel history for {(key[0], key[1], oldest_retained)}"
                                )
                            conn.execute(
                                "UPDATE checkpoints SET parent_checkpoint_id = NULL WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                                (key[0], key[1], oldest_retained),
                            )
                    for key, fingerprint in latest_before.items():
                        after = self._checkpoint_resume_fingerprint(
                            conn,
                            key[0],
                            key[1],
                            key[2],
                            serializer=serializer,
                        )
                        if after != fingerprint:
                            raise RuntimeError(f"checkpoint resume fingerprint changed for {key}")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            return [{
                "action": "prune_old_checkpoints",
                "checkpoints": total_delete_count if dry_run else len(delete_keys),
                "estimatedLogicalBytes": total_estimated_bytes if dry_run else estimated_bytes,
                "policies": policy_counts,
                "deltaAnchorCheckpoints": anchor_count,
                "dryRun": dry_run,
            }]

    @staticmethod
    def _checkpoint_message_delta_flags(
        row: sqlite3.Row,
        *,
        serializer: Any,
    ) -> tuple[bool, bool]:
        from erc.checkpoint_security import CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY

        metadata_blob = row["metadata"] if "metadata" in row.keys() else None
        if metadata_blob:
            try:
                metadata = json.loads(bytes(metadata_blob).decode("utf-8"))
            except (TypeError, ValueError, UnicodeError):
                metadata = {}
            marker = str(
                (metadata.get(CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY, "") or "")
                if isinstance(metadata, dict)
                else ""
            ).strip().lower()
            if marker == "seed":
                return True, True
            if marker == "delta":
                return True, False
            if marker == "none":
                return False, False
        checkpoint = serializer.loads_typed((str(row["type"] or ""), bytes(row["checkpoint"] or b"")))
        values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
        versions = checkpoint.get("channel_versions") if isinstance(checkpoint, dict) else None
        has_seed = isinstance(values, dict) and "messages" in values
        tracks_messages = has_seed or (isinstance(versions, dict) and "messages" in versions)
        return tracks_messages, has_seed

    @classmethod
    def _checkpoint_is_delta_boundary(
        cls,
        row: sqlite3.Row,
        *,
        serializer: Any,
    ) -> bool:
        tracks_messages, has_seed = cls._checkpoint_message_delta_flags(row, serializer=serializer)
        # A checkpoint before the messages channel exists is the deterministic
        # empty-state boundary.  A checkpoint with channel_values.messages is a
        # full DeltaChannel seed/snapshot boundary.
        return has_seed or not tracks_messages

    @classmethod
    def _delta_safe_checkpoint_ids(
        cls,
        conn: sqlite3.Connection,
        checkpoints: List[sqlite3.Row],
        *,
        target_ids: List[str],
        serializer: Any,
    ) -> tuple[List[str], bool]:
        if not target_ids:
            return [], False
        rows_by_id = {str(row["checkpoint_id"]): row for row in checkpoints}
        retained: set[str] = set()
        delta_managed = False
        flags_by_id: Dict[str, tuple[bool, bool]] = {}

        def flags(checkpoint_id: str) -> tuple[bool, bool]:
            cached = flags_by_id.get(checkpoint_id)
            if cached is not None:
                return cached
            checkpoint_row = conn.execute(
                """
                SELECT type, checkpoint, metadata FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                """,
                (
                    str(rows_by_id[checkpoint_id]["thread_id"]),
                    str(rows_by_id[checkpoint_id]["checkpoint_ns"]),
                    checkpoint_id,
                ),
            ).fetchone()
            if checkpoint_row is None:
                raise RuntimeError(f"checkpoint payload is missing at {checkpoint_id}")
            resolved = cls._checkpoint_message_delta_flags(checkpoint_row, serializer=serializer)
            flags_by_id[checkpoint_id] = resolved
            return resolved

        for target_id in target_ids:
            current_id = target_id
            visited: set[str] = set()
            while current_id:
                if current_id in retained:
                    break
                if current_id in visited:
                    raise RuntimeError(f"checkpoint parent cycle detected at {current_id}")
                visited.add(current_id)
                row = rows_by_id.get(current_id)
                if row is None:
                    raise RuntimeError(f"checkpoint parent chain is incomplete at {current_id}")
                retained.add(current_id)
                tracks_messages, has_seed = flags(current_id)
                if current_id == target_id and not tracks_messages:
                    break
                delta_managed = True
                if has_seed or not tracks_messages:
                    break
                parent_id = str(row["parent_checkpoint_id"] or "").strip()
                if not parent_id:
                    raise RuntimeError(f"DeltaChannel history has no recoverable seed at {current_id}")
                current_id = parent_id
        return (
            [str(row["checkpoint_id"]) for row in checkpoints if str(row["checkpoint_id"]) in retained],
            delta_managed,
        )

    @staticmethod
    def _checkpoint_resume_fingerprint(
        conn: sqlite3.Connection,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        *,
        serializer: Any,
    ) -> Dict[str, Any]:
        row = conn.execute(
            """
            SELECT checkpoint_id, checkpoint, metadata
            FROM checkpoints
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            """,
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchone()
        if not row:
            return {}
        digest = hashlib.sha256()
        digest.update(bytes(row["checkpoint"] or b""))
        digest.update(bytes(row["metadata"] or b""))
        writes = conn.execute(
            """
            SELECT task_id, idx, channel, type, value
            FROM writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            ORDER BY task_id, idx, channel
            """,
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()
        for write in writes:
            digest.update(str(write["task_id"]).encode("utf-8"))
            digest.update(str(write["idx"]).encode("utf-8"))
            digest.update(str(write["channel"]).encode("utf-8"))
            digest.update(str(write["type"] or "").encode("utf-8"))
            digest.update(bytes(write["value"] or b""))
        message_digest = StorageRetentionService._checkpoint_message_digest(
            conn,
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            serializer=serializer,
        )
        return {
            "checkpointId": str(row["checkpoint_id"]),
            "hash": digest.hexdigest(),
            "pendingWrites": len(writes),
            "messageDigest": message_digest,
        }

    @staticmethod
    def _checkpoint_message_digest(
        conn: sqlite3.Connection,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        *,
        serializer: Any,
    ) -> str:
        from langgraph.channels import DeltaChannel
        from langgraph.checkpoint.sqlite import SqliteSaver

        from graph.state_channels import message_state_digest_payload, reduce_message_deltas

        saver = SqliteSaver(conn, serde=serializer)
        # Retention already verified the schema. Avoid SqliteSaver.setup(), whose
        # executescript would implicitly commit our surrounding pruning transaction.
        saver.is_setup = True
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
        checkpoint_tuple = saver.get_tuple(config)
        if checkpoint_tuple is None:
            raise RuntimeError(f"checkpoint not found during retention verification: {checkpoint_id}")
        values = checkpoint_tuple.checkpoint.get("channel_values") or {}
        if "messages" in values:
            channel = DeltaChannel(reduce_message_deltas, list).from_checkpoint(values["messages"])
        else:
            history = saver.get_delta_channel_history(config=config, channels=["messages"])["messages"]
            channel = DeltaChannel(reduce_message_deltas, list).from_checkpoint(history.get("seed", []))
            channel.replay_writes(history.get("writes", []))
        payload = message_state_digest_payload(channel.get())
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _checkpoint_thread_policies(self) -> Dict[str, str]:
        policies: Dict[str, str] = {}
        if not STATE_DB_PATH.exists():
            return policies
        priority = {"orphan": 0, "idle": 1, "recoverable": 2, "running": 3}
        with _connect(STATE_DB_PATH) as conn:
            if self._table_exists(conn, "sessions"):
                for row in conn.execute("SELECT id FROM sessions").fetchall():
                    policies[str(row["id"])] = "idle"
            if self._table_exists(conn, "run_records"):
                rows = conn.execute("SELECT id, session_id, thread_id, status FROM run_records").fetchall()
                for row in rows:
                    status = str(row["status"] or "").strip().lower()
                    policy = "running" if status in RUNNING_RUN_STATUSES else (
                        "recoverable" if status in RECOVERABLE_RUN_STATUSES else "idle"
                    )
                    for key in ("session_id", "thread_id"):
                        value = str(row[key] or "").strip()
                        if not value:
                            continue
                        current = policies.get(value, "orphan")
                        if priority[policy] > priority[current]:
                            policies[value] = policy
        return policies

    def _active_checkpoint_threads(self) -> set[str]:
        protected: set[str] = set()
        if not STATE_DB_PATH.exists():
            return protected
        with _connect(STATE_DB_PATH) as conn:
            for row in conn.execute(
                "SELECT id, session_id, thread_id FROM run_records WHERE status IN ('queued','running','waiting_approval','waiting_input','waiting_external_tool','paused')"
            ).fetchall():
                for key in ("id", "session_id", "thread_id"):
                    value = row[key]
                    if value:
                        protected.add(str(value))
        return protected

    def _prune_log_files(self, *, dry_run: bool) -> List[Dict[str, Any]]:
        files = sorted(self._log_files(), key=lambda path: path.stat().st_mtime if path.exists() else 0)
        if not files:
            return []
        targets = files[:25]
        actions = [{"action": "delete_log_file", "path": str(path), "bytes": _file_size(path), "dryRun": dry_run} for path in targets]
        if not dry_run:
            for path in targets:
                try:
                    path.unlink()
                except OSError:
                    pass
        return actions

    @staticmethod
    def _vacuum_db(path: Path) -> None:
        if not path.exists():
            return
        with _connect(path) as conn:
            conn.execute("VACUUM")


storage_retention_service = StorageRetentionService()
