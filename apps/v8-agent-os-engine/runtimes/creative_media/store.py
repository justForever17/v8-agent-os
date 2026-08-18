from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, TYPE_CHECKING

from core.json_safe import to_jsonable
from core.realtime_protocol import utc_now_iso

if TYPE_CHECKING:
    from core.database import DatabaseManager


_LEGACY_JOBS_SOURCE_KIND = "creative_media_jobs_json_v1"
_LEGACY_OPERATIONAL_SOURCES = {
    "work_orders": ("creative_media_work_orders_json_v1", "workOrders", "workOrderId"),
    "cost_entries": ("creative_media_cost_ledger_json_v1", "entries", "entryId"),
    "quality_jobs": ("creative_media_quality_jobs_json_v1", "qualityJobs", "qualityJobId"),
    "safety_events": ("creative_media_safety_events_json_v1", "events", "eventId"),
}
_TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_TERMINAL_QUALITY_STATUSES = frozenset({"passed", "failed", "cancelled", "rejected"})
_LIFECYCLE_JOB_STATUSES = frozenset({"archived", "deleted"})
_TERMINAL_PROJECTION_KIND = "canvas_graph_terminal"
_STORE_TOKEN_KEYS = frozenset({"_storeRevision", "_storeState", "_storeUpdatedAt"})
_JOB_STATUS_DAG = {
    "queued": frozenset({"queued", "running", "cancelling", "succeeded", "failed", "cancelled"}),
    "running": frozenset({"running", "cancelling", "succeeded", "failed", "cancelled"}),
    "cancelling": frozenset({"cancelling", "succeeded", "failed", "cancelled"}),
    "succeeded": frozenset({"succeeded", "archived", "deleted"}),
    "failed": frozenset({"failed", "archived", "deleted"}),
    "cancelled": frozenset({"cancelled", "archived", "deleted"}),
    "archived": frozenset({"archived", "deleted"}),
    "deleted": frozenset({"deleted"}),
}
_QUALITY_STATUS_DAG = {
    "queued": frozenset({"queued", "running", "repairable", "review_required", "warning", *_TERMINAL_QUALITY_STATUSES}),
    "running": frozenset({"running", "repairable", "review_required", "warning", *_TERMINAL_QUALITY_STATUSES}),
    "repairable": frozenset({"repairable", "running", "review_required", "warning", *_TERMINAL_QUALITY_STATUSES}),
    "review_required": frozenset({"review_required", "running", *_TERMINAL_QUALITY_STATUSES}),
    "warning": frozenset({"warning", "running", *_TERMINAL_QUALITY_STATUSES}),
    **{status: frozenset({status}) for status in _TERMINAL_QUALITY_STATUSES},
}
_WORK_ORDER_STATUS_DAG = {
    "planned": frozenset({"planned", "running", "completed", "failed", "cancelled", "archived", "deleted"}),
    "running": frozenset({"running", "completed", "failed", "cancelled", "archived", "deleted"}),
    "completed": frozenset({"completed", "archived", "deleted"}),
    "failed": frozenset({"failed", "archived", "deleted"}),
    "cancelled": frozenset({"cancelled", "archived", "deleted"}),
    "archived": frozenset({"archived", "deleted"}),
    "deleted": frozenset({"deleted"}),
}


class CreativeMediaStoreError(RuntimeError):
    """Base error for the durable Creative Media store."""


class CreativeMediaStoreConflict(CreativeMediaStoreError):
    """A CAS revision or immutable ledger identity did not match."""


class CreativeMediaStateRegression(CreativeMediaStoreConflict):
    """A write attempted to reopen a terminal or deleted record."""


class CreativeMediaLegacyFormatError(CreativeMediaStoreError):
    """A legacy JSON file does not match the supported v1 envelope."""


@dataclass(frozen=True)
class CreativeMediaJobRecord:
    payload: dict[str, Any]
    revision: int
    source: str = "sqlite"


@dataclass(frozen=True)
class LegacyJobsV1Snapshot:
    jobs: dict[str, dict[str, Any]]
    source_digest: str
    source_identity: str


@dataclass(frozen=True)
class CreativeMediaMigrationResult:
    source_found: bool
    already_applied: bool
    imported_count: int
    skipped_count: int
    source_digest: str = ""
    skip_reason_counts: dict[str, int] = field(default_factory=dict)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        to_jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError) as exc:
        raise CreativeMediaStoreError("Creative Media SQLite payload is invalid JSON") from exc
    if not isinstance(value, dict):
        raise CreativeMediaStoreError("Creative Media SQLite payload must be an object")
    return value


def _without_store_tokens(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _STORE_TOKEN_KEYS}


def _with_store_tokens(
    payload: dict[str, Any],
    *,
    revision: int,
    status: str,
    updated_at: str,
) -> dict[str, Any]:
    return {
        **dict(payload),
        "_storeRevision": int(revision),
        "_storeState": _text(status).lower(),
        "_storeUpdatedAt": _text(updated_at),
    }


def _optional_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _job_status_transition_allowed(previous: str, incoming: str) -> bool:
    previous = _text(previous).lower()
    incoming = _text(incoming).lower()
    if not previous or previous == incoming:
        return True
    return incoming in _JOB_STATUS_DAG.get(previous, frozenset())


def _quality_status_transition_allowed(previous: str, incoming: str) -> bool:
    previous = _text(previous).lower()
    incoming = _text(incoming).lower()
    return not previous or previous == incoming or incoming in _QUALITY_STATUS_DAG.get(previous, frozenset())


def _work_order_status_transition_allowed(previous: str, incoming: str) -> bool:
    previous = _text(previous).lower()
    incoming = _text(incoming).lower()
    return not previous or previous == incoming or incoming in _WORK_ORDER_STATUS_DAG.get(previous, frozenset())


class CreativeMediaStore:
    """SQLite persistence boundary for Creative Media operational truth.

    The store owns serialization, indexed projections, CAS, and legacy import.
    Provider behavior and runtime lifecycle policy deliberately remain outside
    this module.
    """

    def __init__(self, database: DatabaseManager):
        self._database = database

    @staticmethod
    def read_legacy_jobs_v1(path: Path) -> LegacyJobsV1Snapshot:
        source = Path(path)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise CreativeMediaLegacyFormatError("Creative Media jobs v1 source is unreadable") from exc
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CreativeMediaLegacyFormatError("Creative Media jobs v1 source is invalid JSON") from exc
        if not isinstance(envelope, dict) or envelope.get("version") != 1:
            raise CreativeMediaLegacyFormatError("Creative Media jobs source must use version=1")
        raw_jobs = envelope.get("jobs")
        if not isinstance(raw_jobs, dict):
            raise CreativeMediaLegacyFormatError("Creative Media jobs v1 must contain an object jobs map")
        jobs: dict[str, dict[str, Any]] = {}
        for raw_job_id, raw_job in raw_jobs.items():
            job_id = _text(raw_job_id)
            if not job_id or not isinstance(raw_job, dict):
                raise CreativeMediaLegacyFormatError("Creative Media jobs v1 contains an invalid job entry")
            job = dict(to_jsonable(raw_job) or {})
            payload_job_id = _text(job.get("jobId"))
            if payload_job_id and payload_job_id != job_id:
                raise CreativeMediaLegacyFormatError("Creative Media jobs v1 map key does not match jobId")
            job["jobId"] = job_id
            if not _text(job.get("status")):
                raise CreativeMediaLegacyFormatError("Creative Media jobs v1 job status is required")
            jobs[job_id] = job
        resolved = str(source.resolve()).replace("\\", "/").casefold()
        return LegacyJobsV1Snapshot(
            jobs=jobs,
            source_digest=hashlib.sha256(raw).hexdigest(),
            source_identity=hashlib.sha256(resolved.encode("utf-8")).hexdigest(),
        )

    def migrate_jobs_json_v1(self, path: Path) -> CreativeMediaMigrationResult:
        return self.migrate_legacy_v1_sources(jobs_path=path)["jobs"]

    @staticmethod
    def _read_legacy_map_v1(
        path: Path,
        *,
        envelope_key: str,
        id_field: str,
    ) -> LegacyJobsV1Snapshot:
        source = Path(path)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise CreativeMediaLegacyFormatError(
                f"Creative Media {envelope_key} v1 source is unreadable"
            ) from exc
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CreativeMediaLegacyFormatError(
                f"Creative Media {envelope_key} v1 source is invalid JSON"
            ) from exc
        values = envelope.get(envelope_key) if isinstance(envelope, dict) else None
        if not isinstance(envelope, dict) or envelope.get("version") != 1 or not isinstance(values, dict):
            raise CreativeMediaLegacyFormatError(
                f"Creative Media {envelope_key} source must use version=1 and an object map"
            )
        normalized: dict[str, dict[str, Any]] = {}
        for raw_identity, raw_value in values.items():
            identity = _text(raw_identity)
            if not identity or not isinstance(raw_value, dict):
                raise CreativeMediaLegacyFormatError(
                    f"Creative Media {envelope_key} contains an invalid entry"
                )
            value = dict(to_jsonable(raw_value) or {})
            payload_identity = _text(value.get(id_field))
            if payload_identity and payload_identity != identity:
                raise CreativeMediaLegacyFormatError(
                    f"Creative Media {envelope_key} map key does not match {id_field}"
                )
            value[id_field] = identity
            normalized[identity] = value
        resolved = str(source.resolve()).replace("\\", "/").casefold()
        return LegacyJobsV1Snapshot(
            jobs=normalized,
            source_digest=hashlib.sha256(raw).hexdigest(),
            source_identity=hashlib.sha256(resolved.encode("utf-8")).hexdigest(),
        )

    def migrate_legacy_v1_sources(
        self,
        *,
        jobs_path: Path | None = None,
        work_orders_path: Path | None = None,
        cost_entries_path: Path | None = None,
        quality_jobs_path: Path | None = None,
        safety_events_path: Path | None = None,
    ) -> dict[str, CreativeMediaMigrationResult]:
        configured = {
            "jobs": jobs_path,
            "work_orders": work_orders_path,
            "cost_entries": cost_entries_path,
            "quality_jobs": quality_jobs_path,
            "safety_events": safety_events_path,
        }
        snapshots: dict[str, tuple[str, LegacyJobsV1Snapshot]] = {}
        results: dict[str, CreativeMediaMigrationResult] = {}
        # Parse every source before opening the transaction. A malformed later
        # source therefore cannot leave an earlier source partially imported.
        for name, raw_path in configured.items():
            if raw_path is None or not Path(raw_path).is_file():
                results[name] = CreativeMediaMigrationResult(False, False, 0, 0)
                continue
            if name == "jobs":
                snapshot = self.read_legacy_jobs_v1(Path(raw_path))
                source_kind = _LEGACY_JOBS_SOURCE_KIND
            else:
                source_kind, envelope_key, id_field = _LEGACY_OPERATIONAL_SOURCES[name]
                snapshot = self._read_legacy_map_v1(
                    Path(raw_path),
                    envelope_key=envelope_key,
                    id_field=id_field,
                )
            snapshots[name] = (source_kind, snapshot)

        if not snapshots:
            return results
        now = utc_now_iso()
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for name, (source_kind, snapshot) in snapshots.items():
                    receipt = conn.execute(
                        """
                        SELECT imported_count, skipped_count, skip_reasons_json
                        FROM creative_media_store_migrations
                        WHERE source_kind = ? AND source_identity = ? AND source_digest = ?
                        """,
                        (source_kind, snapshot.source_identity, snapshot.source_digest),
                    ).fetchone()
                    if receipt:
                        results[name] = CreativeMediaMigrationResult(
                            True,
                            True,
                            int(receipt["imported_count"] or 0),
                            int(receipt["skipped_count"] or 0),
                            snapshot.source_digest,
                            {
                                str(reason): int(count)
                                for reason, count in _decode_payload(
                                    receipt["skip_reasons_json"]
                                ).items()
                            },
                        )
                        continue
                    imported_count = 0
                    skipped_count = 0
                    skip_reason_counts: dict[str, int] = {}
                    for value in snapshot.jobs.values():
                        skip_reason = self._insert_legacy_value(conn, name=name, payload=value)
                        if skip_reason:
                            skipped_count += 1
                            skip_reason_counts[skip_reason] = skip_reason_counts.get(skip_reason, 0) + 1
                        else:
                            imported_count += 1
                    conn.execute(
                        """
                        INSERT INTO creative_media_store_migrations (
                            source_kind, source_identity, source_digest,
                            imported_count, skipped_count, skip_reasons_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_kind,
                            snapshot.source_identity,
                            snapshot.source_digest,
                            imported_count,
                            skipped_count,
                            _canonical_json(skip_reason_counts),
                            now,
                        ),
                    )
                    results[name] = CreativeMediaMigrationResult(
                        True,
                        False,
                        imported_count,
                        skipped_count,
                        snapshot.source_digest,
                        skip_reason_counts,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return results

    def _insert_legacy_value(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        payload: dict[str, Any],
    ) -> str | None:
        if name == "jobs":
            return None if self._insert_job(conn, payload, revision=1) else "existing_record"
        scoped_payload, owner_skip_reason = self._legacy_owner_scoped_payload(conn, payload)
        if scoped_payload is None:
            return owner_skip_reason or "owner_scope_unavailable"
        payload = scoped_payload
        if name == "work_orders":
            work_order = self._normalize_work_order(payload)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO creative_media_work_orders (
                    work_order_id, session_id, requesting_runtime, status, revision,
                    payload_json, created_at, updated_at, archived_at, deleted_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                self._work_order_values(work_order),
            )
            return None if cursor.rowcount == 1 else "existing_record"
        if name == "cost_entries":
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO creative_media_cost_entries (
                    entry_id, job_id, session_id, status, provider, model,
                    operation_kind, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(payload.get("entryId")), _text(payload.get("jobId")),
                    _text(payload.get("sessionId")), _text(payload.get("status")).lower(),
                    _text(payload.get("provider")), _text(payload.get("model")),
                    _text(payload.get("operationKind")), _canonical_json(payload),
                    _text(payload.get("createdAt")) or utc_now_iso(),
                ),
            )
            return None if cursor.rowcount == 1 else "existing_record"
        if name == "quality_jobs":
            created_at = _text(payload.get("createdAt")) or utc_now_iso()
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO creative_media_quality_jobs (
                    quality_job_id, job_id, session_id, status, quality_profile,
                    revision, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    _text(payload.get("qualityJobId")), _text(payload.get("jobId")),
                    _text(payload.get("sessionId")), _text(payload.get("status")).lower(),
                    _text(payload.get("qualityProfile")), _canonical_json(payload),
                    created_at, _text(payload.get("updatedAt")) or created_at,
                ),
            )
            return None if cursor.rowcount == 1 else "existing_record"
        if name == "safety_events":
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO creative_media_safety_events (
                    event_id, job_id, session_id, source, policy, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(payload.get("eventId")), _text(payload.get("jobId")),
                    _text(payload.get("sessionId")), _text(payload.get("source")),
                    _text(payload.get("policy")), _canonical_json(payload),
                    _text(payload.get("createdAt")) or utc_now_iso(),
                ),
            )
            return None if cursor.rowcount == 1 else "existing_record"
        raise ValueError(f"Unsupported Creative Media legacy source: {name}")

    @staticmethod
    def _legacy_owner_scoped_payload(
        conn: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Backfill legacy owner claims only from the exact durable job owner."""

        normalized = dict(to_jsonable(payload) or {})
        declared = {
            "sessionId": _text(normalized.get("sessionId") or normalized.get("session_id")),
            "workspaceId": _text(normalized.get("workspaceId") or normalized.get("workspace_id")),
            "projectId": _text(normalized.get("projectId") or normalized.get("project_id")),
            "workspacePath": _text(normalized.get("workspacePath") or normalized.get("workspace_path")),
        }
        job_id = _text(normalized.get("jobId") or normalized.get("job_id"))
        job_owner: dict[str, str] = {}
        if job_id:
            row = conn.execute(
                """
                SELECT session_id, workspace_id, project_id, payload_json
                FROM creative_media_jobs WHERE job_id = ? LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if row:
                job_payload = _decode_payload(row["payload_json"])
                job_owner = {
                    "sessionId": _text(row["session_id"] or job_payload.get("sessionId")),
                    "workspaceId": _text(row["workspace_id"] or job_payload.get("workspaceId")),
                    "projectId": _text(row["project_id"] or job_payload.get("projectId")),
                    "workspacePath": _text(
                        job_payload.get("workspacePath") or job_payload.get("workspace_path")
                    ),
                }
            else:
                return None, "job_owner_missing"

        for field, declared_value in declared.items():
            owner_value = job_owner.get(field, "")
            if not declared_value or not owner_value:
                continue
            if field == "workspacePath":
                declared_key = declared_value.replace("\\", "/").rstrip("/").casefold()
                owner_key = owner_value.replace("\\", "/").rstrip("/").casefold()
                if declared_key != owner_key:
                    return None, "owner_scope_conflict"
            elif declared_value != owner_value:
                return None, "owner_scope_conflict"

        owner = {
            field: declared.get(field) or job_owner.get(field, "")
            for field in ("sessionId", "workspaceId", "projectId", "workspacePath")
        }
        if not owner["sessionId"] or not owner["workspaceId"] or not owner["workspacePath"]:
            return None, "owner_scope_incomplete"
        normalized.update(owner)
        return normalized, None

    def create_job(self, payload: dict[str, Any]) -> CreativeMediaJobRecord:
        job = self._normalize_job(payload)
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if not self._insert_job(conn, job, revision=1):
                    raise CreativeMediaStoreConflict(
                        f"Creative Media job already exists: {job['jobId']}"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return CreativeMediaJobRecord(payload=job, revision=1)

    def compare_and_swap_job(
        self,
        job_id: str,
        *,
        expected_revision: int,
        expected_status: str | None = None,
        expected_updated_at: str | None = None,
        payload: dict[str, Any],
        complete_terminal_observation: bool = False,
    ) -> CreativeMediaJobRecord:
        normalized_job_id = _text(job_id)
        if expected_revision < 1:
            raise ValueError("Creative Media expected revision must be positive")
        job = self._normalize_job(payload, expected_job_id=normalized_job_id)
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT revision, status, updated_at, session_id, workspace_id, project_id "
                    "FROM creative_media_jobs WHERE job_id = ?",
                    (normalized_job_id,),
                ).fetchone()
                if (
                    not current
                    or int(current["revision"] or 0) != expected_revision
                    or (
                        expected_status is not None
                        and _text(current["status"]).lower() != _text(expected_status).lower()
                    )
                    or (
                        expected_updated_at is not None
                        and _text(current["updated_at"]) != _text(expected_updated_at)
                    )
                ):
                    raise CreativeMediaStoreConflict(
                        f"Creative Media job revision conflict: {normalized_job_id}"
                    )
                if not _job_status_transition_allowed(current["status"], job["status"]):
                    raise CreativeMediaStateRegression(
                        "Creative Media terminal job state cannot be reopened or replaced"
                    )
                if any(
                    _text(current[column]) != _text(job.get(payload_key))
                    for column, payload_key in (
                        ("session_id", "sessionId"),
                        ("workspace_id", "workspaceId"),
                        ("project_id", "projectId"),
                    )
                ):
                    raise CreativeMediaStoreConflict(
                        "Creative Media job owner scope is immutable"
                    )
                next_revision = expected_revision + 1
                columns = self._job_columns(job)
                cursor = conn.execute(
                    """
                    UPDATE creative_media_jobs
                    SET session_id = ?, run_id = ?, workspace_id = ?, project_id = ?,
                        modality = ?, adapter = ?, operation_kind = ?, status = ?,
                        provider_task_id = ?, next_reconcile_at = ?,
                        projection_pending = ?, revision = ?, payload_json = ?,
                        created_at = ?, updated_at = ?, completed_at = ?
                    WHERE job_id = ? AND revision = ? AND status = ? AND updated_at = ?
                    """,
                    (
                        *columns,
                        next_revision,
                        _canonical_json(job),
                        self._created_at(job),
                        self._updated_at(job),
                        _text(job.get("completedAt")) or None,
                        normalized_job_id,
                        expected_revision,
                        _text(current["status"]).lower(),
                        _text(current["updated_at"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise CreativeMediaStoreConflict(
                        f"Creative Media job revision conflict: {normalized_job_id}"
                    )
                self._sync_job_children(conn, job)
                self._sync_terminal_observation_outbox(conn, job)
                if complete_terminal_observation:
                    completed_at = self._updated_at(job)
                    outbox_cursor = conn.execute(
                        """
                        UPDATE creative_media_terminal_observation_outbox
                        SET state = 'completed', updated_at = ?, completed_at = ?, last_error = NULL
                        WHERE job_id = ? AND state = 'pending'
                        """,
                        (completed_at, completed_at, normalized_job_id),
                    )
                    if outbox_cursor.rowcount != 1:
                        raise CreativeMediaStoreConflict(
                            f"Creative Media terminal observation outbox conflict: {normalized_job_id}"
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return CreativeMediaJobRecord(payload=job, revision=next_revision)

    def save_lifecycle_phase(
        self,
        job_id: str,
        *,
        phase: str,
        report: dict[str, Any],
        expected_revision: int,
    ) -> CreativeMediaJobRecord:
        record = self.get_job(job_id)
        if not record or record.revision != expected_revision:
            raise CreativeMediaStoreConflict(f"Creative Media job revision conflict: {job_id}")
        payload = dict(record.payload)
        lifecycle = dict(payload.get("lifecycle") or {})
        lifecycle[_text(phase)] = dict(to_jsonable(report) or {})
        payload["lifecycle"] = lifecycle
        payload["updatedAt"] = utc_now_iso()
        return self.compare_and_swap_job(
            job_id,
            expected_revision=expected_revision,
            expected_status=str(record.payload.get("status") or ""),
            expected_updated_at=str(record.payload.get("updatedAt") or ""),
            payload=payload,
        )

    def get_job(
        self,
        job_id: str,
        *,
        legacy_jobs_path: Path | None = None,
    ) -> CreativeMediaJobRecord | None:
        normalized_job_id = _text(job_id)
        with self._database.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM creative_media_jobs WHERE job_id = ?",
                (normalized_job_id,),
            ).fetchone()
            if row:
                return self._hydrate_job_rows(conn, [row])[0]
        if legacy_jobs_path is None or not Path(legacy_jobs_path).is_file():
            return None
        job = self.read_legacy_jobs_v1(Path(legacy_jobs_path)).jobs.get(normalized_job_id)
        return (
            CreativeMediaJobRecord(payload=dict(job), revision=0, source="legacy_json_v1")
            if job
            else None
        )

    def list_jobs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        adapter: str | None = None,
        modality: str | None = None,
        limit: int = 100,
        legacy_jobs_path: Path | None = None,
    ) -> list[CreativeMediaJobRecord]:
        bounded_limit = max(1, min(int(limit), 10_000))
        filters: list[str] = []
        params: list[Any] = []
        for column, value, casefold in (
            ("session_id", session_id, False),
            ("status", status, True),
            ("adapter", adapter, True),
            ("modality", modality, True),
        ):
            normalized = _text(value)
            if casefold:
                normalized = normalized.lower()
            if normalized:
                filters.append(f"{column} = ?")
                params.append(normalized)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._database.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_media_jobs"
                f"{where} ORDER BY updated_at DESC, job_id DESC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
            records = self._hydrate_job_rows(conn, rows)
        if legacy_jobs_path is None or not Path(legacy_jobs_path).is_file():
            return records
        by_id = {_text(item.payload.get("jobId")): item for item in records}
        snapshot = self.read_legacy_jobs_v1(Path(legacy_jobs_path))
        for job in snapshot.jobs.values():
            job_id = _text(job.get("jobId"))
            if job_id in by_id or not self._job_matches(
                job,
                session_id=session_id,
                status=status,
                adapter=adapter,
                modality=modality,
            ):
                continue
            by_id[job_id] = CreativeMediaJobRecord(
                payload=dict(job), revision=0, source="legacy_json_v1"
            )
        result = sorted(
            by_id.values(),
            key=lambda item: (
                _text(item.payload.get("updatedAt")),
                _text(item.payload.get("jobId")),
            ),
            reverse=True,
        )
        return result[:bounded_limit]

    def iter_jobs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        adapter: str | None = None,
        modality: str | None = None,
        batch_size: int = 1_000,
    ) -> Iterator[CreativeMediaJobRecord]:
        """Iterate every matching SQLite job without an aggregate row cap.

        Lifecycle and cleanup callers use primary-key keyset pagination so a
        collection larger than the public list limit cannot be silently
        truncated. Legacy JSON is intentionally excluded: initialization
        imports it transactionally before this iterator is used.
        """

        bounded_batch = max(1, min(int(batch_size), 5_000))
        filters: list[str] = []
        params: list[Any] = []
        for column, value, casefold in (
            ("session_id", session_id, False),
            ("status", status, True),
            ("adapter", adapter, True),
            ("modality", modality, True),
        ):
            normalized = _text(value)
            if casefold:
                normalized = normalized.lower()
            if normalized:
                filters.append(f"{column} = ?")
                params.append(normalized)
        cursor = ""
        while True:
            page_filters = [*filters, "job_id > ?"]
            with self._database.get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM creative_media_jobs "
                    f"WHERE {' AND '.join(page_filters)} "
                    "ORDER BY job_id ASC LIMIT ?",
                    (*params, cursor, bounded_batch),
                ).fetchall()
                records = self._hydrate_job_rows(conn, rows)
            if not records:
                return
            yield from records
            cursor = _text(records[-1].payload.get("jobId"))
            if len(records) < bounded_batch:
                return

    def list_reconcile_candidates(
        self,
        *,
        due_at: str,
        limit: int = 100,
    ) -> list[CreativeMediaJobRecord]:
        bounded_limit = max(1, min(int(limit), 10_000))
        with self._database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM creative_media_jobs
                WHERE next_reconcile_at IS NOT NULL
                  AND next_reconcile_at <= ?
                ORDER BY next_reconcile_at ASC, updated_at ASC, job_id ASC
                LIMIT ?
                """,
                (_text(due_at), bounded_limit),
            ).fetchall()
            return self._hydrate_job_rows(conn, rows)

    def list_pending_projections(
        self,
        *,
        due_at: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        with self._database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM creative_media_job_projections
                WHERE projection_pending = 1
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY COALESCE(next_attempt_at, updated_at) ASC, job_id ASC
                LIMIT ?
                """,
                (_text(due_at), bounded_limit),
            ).fetchall()
        return [
            {
                **_decode_payload(row["payload_json"]),
                "jobId": row["job_id"],
                "projectionKind": row["projection_kind"],
                "projectionRevision": int(row["attempt_count"] or 0),
            }
            for row in rows
        ]

    def put_cost_entry(self, payload: dict[str, Any]) -> bool:
        if not _text(payload.get("sessionId")):
            raise ValueError("Creative Media cost entry requires sessionId")
        return self._put_immutable_ledger(
            table="creative_media_cost_entries",
            id_column="entry_id",
            identity=_text(payload.get("entryId")),
            columns=(
                "job_id", "session_id", "status", "provider", "model",
                "operation_kind", "payload_json", "created_at",
            ),
            values=(
                _text(payload.get("jobId")),
                _text(payload.get("sessionId")),
                _text(payload.get("status")).lower(),
                _text(payload.get("provider")),
                _text(payload.get("model")),
                _text(payload.get("operationKind")),
                _canonical_json(payload),
                _text(payload.get("createdAt")) or utc_now_iso(),
            ),
        )

    def put_safety_event(self, payload: dict[str, Any]) -> bool:
        if not _text(payload.get("sessionId")):
            raise ValueError("Creative Media safety event requires sessionId")
        return self._put_immutable_ledger(
            table="creative_media_safety_events",
            id_column="event_id",
            identity=_text(payload.get("eventId")),
            columns=("job_id", "session_id", "source", "policy", "payload_json", "created_at"),
            values=(
                _text(payload.get("jobId")),
                _text(payload.get("sessionId")),
                _text(payload.get("source")),
                _text(payload.get("policy")),
                _canonical_json(payload),
                _text(payload.get("createdAt")) or utc_now_iso(),
            ),
        )

    def list_cost_entries(
        self,
        *,
        session_id: str | None = None,
        job_id: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        filters: list[str] = []
        params: list[Any] = []
        if _text(session_id):
            filters.append("session_id = ?")
            params.append(_text(session_id))
        if _text(job_id):
            filters.append("job_id = ?")
            params.append(_text(job_id))
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._database.get_connection() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM creative_media_cost_entries"
                f"{where} ORDER BY created_at DESC, entry_id DESC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
        return [_decode_payload(row["payload_json"]) for row in rows]

    def list_safety_events(
        self,
        *,
        session_id: str | None = None,
        job_id: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        filters: list[str] = []
        params: list[Any] = []
        if _text(session_id):
            filters.append("session_id = ?")
            params.append(_text(session_id))
        if _text(job_id):
            filters.append("job_id = ?")
            params.append(_text(job_id))
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._database.get_connection() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM creative_media_safety_events"
                f"{where} ORDER BY created_at DESC, event_id DESC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
        return [_decode_payload(row["payload_json"]) for row in rows]

    def save_quality_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        quality_job_id = _text(payload.get("qualityJobId"))
        if not quality_job_id:
            raise ValueError("Creative Media quality job requires qualityJobId")
        if not _text(payload.get("sessionId")):
            raise ValueError("Creative Media quality job requires sessionId")
        expected_revision = int(payload.get("_storeRevision") or 0)
        expected_status = _text(payload.get("_storeState")).lower()
        expected_updated_at = _text(payload.get("_storeUpdatedAt"))
        normalized = _without_store_tokens(dict(to_jsonable(payload) or {}))
        created_at = _text(normalized.get("createdAt")) or utc_now_iso()
        updated_at = _text(normalized.get("updatedAt")) or created_at
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT revision, status, updated_at, session_id "
                    "FROM creative_media_quality_jobs WHERE quality_job_id = ?",
                    (quality_job_id,),
                ).fetchone()
                current_status = _text(current["status"]).lower() if current else ""
                incoming_status = _text(normalized.get("status")).lower()
                if not _quality_status_transition_allowed(current_status, incoming_status):
                    message = (
                        "Creative Media terminal quality state cannot be reopened or replaced"
                        if current_status in _TERMINAL_QUALITY_STATUSES
                        else "Creative Media quality state transition is not allowed"
                    )
                    raise CreativeMediaStateRegression(
                        message
                    )
                if current and _text(current["session_id"]) != _text(normalized.get("sessionId")):
                    raise CreativeMediaStoreConflict(
                        "Creative Media quality job owner scope is immutable"
                    )
                if current:
                    if (
                        expected_revision < 1
                        or int(current["revision"] or 0) != expected_revision
                        or current_status != expected_status
                        or _text(current["updated_at"]) != expected_updated_at
                    ):
                        raise CreativeMediaStoreConflict(
                            f"Creative Media quality job revision conflict: {quality_job_id}"
                        )
                    next_revision = expected_revision + 1
                    cursor = conn.execute(
                        """
                        UPDATE creative_media_quality_jobs
                        SET job_id = ?, session_id = ?, status = ?, quality_profile = ?,
                            revision = ?, payload_json = ?, updated_at = ?
                        WHERE quality_job_id = ? AND revision = ? AND status = ? AND updated_at = ?
                        """,
                        (
                            _text(normalized.get("jobId")),
                            _text(normalized.get("sessionId")),
                            incoming_status,
                            _text(normalized.get("qualityProfile")),
                            next_revision,
                            _canonical_json(normalized),
                            updated_at,
                            quality_job_id,
                            expected_revision,
                            expected_status,
                            expected_updated_at,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CreativeMediaStoreConflict(
                            f"Creative Media quality job revision conflict: {quality_job_id}"
                        )
                else:
                    next_revision = 1
                    conn.execute(
                        """
                        INSERT INTO creative_media_quality_jobs (
                            quality_job_id, job_id, session_id, status, quality_profile,
                            revision, payload_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                        """,
                        (
                            quality_job_id,
                            _text(normalized.get("jobId")),
                            _text(normalized.get("sessionId")),
                            incoming_status,
                            _text(normalized.get("qualityProfile")),
                            _canonical_json(normalized),
                            created_at,
                            updated_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _with_store_tokens(
            normalized,
            revision=next_revision,
            status=incoming_status,
            updated_at=updated_at,
        )

    def get_quality_job(self, quality_job_id: str) -> dict[str, Any] | None:
        with self._database.get_connection() as conn:
            row = conn.execute(
                "SELECT payload_json, revision, status, updated_at "
                "FROM creative_media_quality_jobs WHERE quality_job_id = ?",
                (_text(quality_job_id),),
            ).fetchone()
        return (
            _with_store_tokens(
                _decode_payload(row["payload_json"]),
                revision=int(row["revision"] or 0),
                status=row["status"],
                updated_at=row["updated_at"],
            )
            if row
            else None
        )

    def list_quality_jobs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        filters: list[str] = []
        params: list[Any] = []
        if _text(session_id):
            filters.append("session_id = ?")
            params.append(_text(session_id))
        if _text(status):
            filters.append("status = ?")
            params.append(_text(status).lower())
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._database.get_connection() as conn:
            rows = conn.execute(
                "SELECT payload_json, revision, status, updated_at FROM creative_media_quality_jobs"
                f"{where} ORDER BY updated_at DESC, quality_job_id DESC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
        return [
            _with_store_tokens(
                _decode_payload(row["payload_json"]),
                revision=int(row["revision"] or 0),
                status=row["status"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def create_work_order(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        work_order = self._normalize_work_order(payload)
        if not _text(work_order.get("sessionId")):
            raise ValueError("Creative Media work order requires sessionId")
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO creative_media_work_orders (
                        work_order_id, session_id, requesting_runtime, status, revision,
                        payload_json, created_at, updated_at, archived_at, deleted_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    self._work_order_values(work_order),
                )
                if cursor.rowcount != 1:
                    raise CreativeMediaStoreConflict(
                        f"Creative Media work order already exists: {work_order['workOrderId']}"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return work_order, 1

    def compare_and_swap_work_order(
        self,
        work_order_id: str,
        *,
        expected_revision: int,
        expected_status: str | None = None,
        expected_updated_at: str | None = None,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        work_order = self._normalize_work_order(payload, expected_id=_text(work_order_id))
        if not _text(work_order.get("sessionId")):
            raise ValueError("Creative Media work order requires sessionId")
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT revision, status, updated_at, session_id "
                    "FROM creative_media_work_orders WHERE work_order_id = ?",
                    (_text(work_order_id),),
                ).fetchone()
                if (
                    not current
                    or int(current["revision"] or 0) != expected_revision
                    or (
                        expected_status is not None
                        and _text(current["status"]).lower() != _text(expected_status).lower()
                    )
                    or (
                        expected_updated_at is not None
                        and _text(current["updated_at"]) != _text(expected_updated_at)
                    )
                ):
                    raise CreativeMediaStoreConflict(
                        f"Creative Media work order revision conflict: {work_order_id}"
                    )
                current_status = _text(current["status"]).lower()
                incoming_status = _text(work_order.get("status")).lower()
                if _text(current["session_id"]) != _text(work_order.get("sessionId")):
                    raise CreativeMediaStoreConflict(
                        "Creative Media work order owner scope is immutable"
                    )
                if not _work_order_status_transition_allowed(current_status, incoming_status):
                    message = (
                        "Creative Media terminal work order state cannot be reopened or replaced"
                        if current_status in {"completed", "failed", "cancelled", "archived", "deleted"}
                        else "Creative Media work order state transition is not allowed"
                    )
                    raise CreativeMediaStateRegression(
                        message
                    )
                next_revision = expected_revision + 1
                cursor = conn.execute(
                    """
                    UPDATE creative_media_work_orders
                    SET session_id = ?, requesting_runtime = ?, status = ?, revision = ?,
                        payload_json = ?, created_at = ?, updated_at = ?,
                        archived_at = ?, deleted_at = ?
                    WHERE work_order_id = ? AND revision = ? AND status = ? AND updated_at = ?
                    """,
                    (
                        _text(work_order.get("sessionId")),
                        _text(work_order.get("requestingRuntime")),
                        _text(work_order.get("status")).lower(),
                        next_revision,
                        _canonical_json(work_order),
                        _text(work_order.get("createdAt")) or utc_now_iso(),
                        _text(work_order.get("updatedAt")) or utc_now_iso(),
                        _text(work_order.get("archivedAt")) or None,
                        _text(work_order.get("deletedAt")) or None,
                        _text(work_order_id),
                        expected_revision,
                        current_status,
                        _text(current["updated_at"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise CreativeMediaStoreConflict(
                        f"Creative Media work order revision conflict: {work_order_id}"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return work_order, next_revision

    def get_work_order(self, work_order_id: str) -> tuple[dict[str, Any], int] | None:
        with self._database.get_connection() as conn:
            row = conn.execute(
                "SELECT payload_json, revision FROM creative_media_work_orders "
                "WHERE work_order_id = ?",
                (_text(work_order_id),),
            ).fetchone()
        if not row:
            return None
        return _decode_payload(row["payload_json"]), int(row["revision"] or 0)

    def list_work_orders(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        requesting_runtime: str | None = None,
        include_archived: bool = False,
        limit: int = 10_000,
    ) -> list[tuple[dict[str, Any], int]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        filters: list[str] = []
        params: list[Any] = []
        if _text(session_id):
            filters.append("session_id = ?")
            params.append(_text(session_id))
        if _text(status):
            filters.append("status = ?")
            params.append(_text(status).lower())
        if _text(requesting_runtime):
            filters.append("requesting_runtime = ?")
            params.append(_text(requesting_runtime))
        if not include_archived and not _text(status):
            filters.append("status NOT IN ('archived', 'deleted')")
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._database.get_connection() as conn:
            rows = conn.execute(
                "SELECT payload_json, revision FROM creative_media_work_orders"
                f"{where} ORDER BY updated_at DESC, work_order_id DESC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
        return [(_decode_payload(row["payload_json"]), int(row["revision"] or 0)) for row in rows]

    @staticmethod
    def _normalize_job(
        payload: dict[str, Any],
        *,
        expected_job_id: str = "",
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Creative Media job payload must be an object")
        job = _without_store_tokens(dict(to_jsonable(payload) or {}))
        job_id = _text(job.get("jobId"))
        if not job_id:
            raise ValueError("Creative Media job persistence requires jobId")
        if expected_job_id and job_id != expected_job_id:
            raise ValueError("Creative Media job payload does not match the requested jobId")
        if not _text(job.get("status")):
            raise ValueError("Creative Media job persistence requires status")
        return job

    @staticmethod
    def _created_at(job: dict[str, Any]) -> str:
        return _text(job.get("createdAt")) or _text(job.get("updatedAt")) or utc_now_iso()

    @staticmethod
    def _updated_at(job: dict[str, Any]) -> str:
        return _text(job.get("updatedAt")) or _text(job.get("createdAt")) or utc_now_iso()

    @classmethod
    def _job_columns(cls, job: dict[str, Any]) -> tuple[Any, ...]:
        lifecycle = dict(job.get("lifecycle") or {})
        remote = dict(lifecycle.get("remoteReconcile") or {})
        return (
            _text(job.get("sessionId")),
            _text(job.get("runId")),
            _text(job.get("workspaceId")),
            _text(job.get("projectId")),
            _text(job.get("modality")).lower(),
            _text(job.get("adapter")).lower(),
            _text(job.get("operationKind")),
            _text(job.get("status")).lower(),
            _text(job.get("providerTaskId")) or None,
            _text(remote.get("nextReconcileAt")) or None,
            1 if bool(remote.get("projectionPending")) else 0,
        )

    def _insert_job(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        *,
        revision: int,
    ) -> bool:
        job = self._normalize_job(payload)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO creative_media_jobs (
                job_id, session_id, run_id, workspace_id, project_id, modality,
                adapter, operation_kind, status, provider_task_id,
                next_reconcile_at, projection_pending, revision, payload_json,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _text(job.get("jobId")),
                *self._job_columns(job),
                revision,
                _canonical_json(job),
                self._created_at(job),
                self._updated_at(job),
                _text(job.get("completedAt")) or None,
            ),
        )
        if cursor.rowcount != 1:
            return False
        self._sync_job_children(conn, job)
        self._sync_terminal_observation_outbox(conn, job)
        return True

    def _sync_job_children(self, conn: sqlite3.Connection, job: dict[str, Any]) -> None:
        lifecycle = dict(job.get("lifecycle") or {})
        now = self._updated_at(job)
        for raw_phase, raw_report in lifecycle.items():
            phase = _text(raw_phase)
            if not phase or not isinstance(raw_report, dict):
                continue
            report = dict(to_jsonable(raw_report) or {})
            created_at = (
                _text(report.get("createdAt"))
                or _text(report.get("reconciledAt"))
                or _text(report.get("completedAt"))
                or self._created_at(job)
            )
            conn.execute(
                """
                INSERT INTO creative_media_job_lifecycle (
                    job_id, phase, status, detail_code, remote_task_may_continue,
                    attempt_count, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, phase) DO UPDATE SET
                    status = excluded.status,
                    detail_code = excluded.detail_code,
                    remote_task_may_continue = excluded.remote_task_may_continue,
                    attempt_count = excluded.attempt_count,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    _text(job.get("jobId")),
                    phase,
                    _text(report.get("status")).lower(),
                    _text(report.get("detailCode")),
                    _optional_bool_int(report.get("remoteTaskMayContinue")),
                    max(0, int(report.get("attempt") or report.get("attempts") or 0)),
                    _canonical_json(report),
                    created_at,
                    now,
                ),
            )
        remote = dict(lifecycle.get("remoteReconcile") or {})
        if remote:
            self._sync_terminal_projection(conn, job, remote)

    def _sync_terminal_observation_outbox(
        self,
        conn: sqlite3.Connection,
        job: dict[str, Any],
    ) -> None:
        status = _text(job.get("status")).lower()
        job_id = _text(job.get("jobId"))
        if status not in _TERMINAL_JOB_STATUSES or not job_id or job.get("p4RecordedAt"):
            return
        created_at = self._updated_at(job)
        digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:24]
        marker = {
            "schema": "v8.creative_media_terminal_observation.v1",
            "jobId": job_id,
            "costEntryId": f"cm_cost_terminal_{digest}",
            "qualityJobId": f"cm_quality_terminal_{digest}",
            "createdAt": created_at,
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO creative_media_terminal_observation_outbox (
                job_id, state, attempt_count, payload_json,
                last_error, created_at, updated_at, completed_at
            ) VALUES (?, 'pending', 0, ?, NULL, ?, ?, NULL)
            """,
            (job_id, _canonical_json(marker), created_at, created_at),
        )

    def list_pending_terminal_observations(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        filters = ["o.state = 'pending'"]
        params: list[Any] = []
        if _text(job_id):
            filters.append("o.job_id = ?")
            params.append(_text(job_id))
        with self._database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT o.payload_json AS outbox_payload, o.attempt_count,
                       j.payload_json AS job_payload, j.revision,
                       j.status, j.updated_at
                FROM creative_media_terminal_observation_outbox AS o
                JOIN creative_media_jobs AS j ON j.job_id = o.job_id
                WHERE """
                + " AND ".join(filters)
                + " ORDER BY o.created_at ASC, o.job_id ASC LIMIT ?",
                (*params, bounded_limit),
            ).fetchall()
        return [
            {
                **_decode_payload(row["outbox_payload"]),
                "attempt": int(row["attempt_count"] or 0),
                "job": _decode_payload(row["job_payload"]),
                "jobRevision": int(row["revision"] or 0),
                "jobStatus": _text(row["status"]).lower(),
                "jobUpdatedAt": _text(row["updated_at"]),
            }
            for row in rows
        ]

    def mark_terminal_observation_failed(self, job_id: str, error: str) -> None:
        now = utc_now_iso()
        with self._database.get_connection() as conn:
            conn.execute(
                """
                UPDATE creative_media_terminal_observation_outbox
                SET attempt_count = attempt_count + 1,
                    last_error = ?, updated_at = ?
                WHERE job_id = ? AND state = 'pending'
                """,
                (_text(error)[:1000], now, _text(job_id)),
            )

    def _sync_terminal_projection(
        self,
        conn: sqlite3.Connection,
        job: dict[str, Any],
        report: dict[str, Any],
    ) -> None:
        pending = bool(report.get("projectionPending"))
        projected_at = _text(report.get("projectedAt")) or None
        disposition = _text(report.get("projectionDisposition")).lower()
        state = (
            "pending"
            if pending
            else disposition
            if disposition and disposition != "transient"
            else "projected"
            if projected_at
            else "idle"
        )
        current = conn.execute(
            "SELECT state, projection_pending FROM creative_media_job_projections "
            "WHERE job_id = ? AND projection_kind = ?",
            (_text(job.get("jobId")), _TERMINAL_PROJECTION_KIND),
        ).fetchone()
        if (
            current
            and not bool(current["projection_pending"])
            and _text(current["state"]) not in {"", "idle"}
            and (pending or state != _text(current["state"]))
        ):
            raise CreativeMediaStateRegression(
                "Creative Media terminal projection cannot be reopened or replaced"
            )
        proof = dict(report.get("terminalProof") or {})
        proof_digest = _text(proof.get("proofDigest"))
        if proof and not proof_digest:
            proof_digest = hashlib.sha256(_canonical_json(proof).encode("utf-8")).hexdigest()
        now = self._updated_at(job)
        conn.execute(
            """
            INSERT INTO creative_media_job_projections (
                job_id, projection_kind, state, projection_pending, attempt_count,
                next_attempt_at, proof_digest, detail_code, last_error,
                payload_json, created_at, updated_at, projected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, projection_kind) DO UPDATE SET
                state = excluded.state,
                projection_pending = excluded.projection_pending,
                attempt_count = excluded.attempt_count,
                next_attempt_at = excluded.next_attempt_at,
                proof_digest = excluded.proof_digest,
                detail_code = excluded.detail_code,
                last_error = excluded.last_error,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                projected_at = excluded.projected_at
            """,
            (
                _text(job.get("jobId")),
                _TERMINAL_PROJECTION_KIND,
                state,
                1 if pending else 0,
                max(0, int(report.get("projectionAttempts") or 0)),
                _text(report.get("nextProjectionAt")) or None,
                proof_digest or None,
                _text(report.get("detailCode")),
                _text(report.get("lastProjectionError")) or None,
                _canonical_json(report),
                _text(report.get("reconciledAt")) or self._created_at(job),
                now,
                projected_at,
            ),
        )

    @staticmethod
    def _hydrate_job_rows(
        conn: sqlite3.Connection,
        rows: Iterable[sqlite3.Row],
    ) -> list[CreativeMediaJobRecord]:
        row_list = list(rows)
        if not row_list:
            return []
        job_ids = [str(row["job_id"]) for row in row_list]
        placeholders = ",".join("?" for _ in job_ids)
        lifecycle_rows = conn.execute(
            "SELECT job_id, phase, payload_json FROM creative_media_job_lifecycle "
            f"WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        ).fetchall()
        lifecycle_by_job: dict[str, dict[str, Any]] = {}
        for row in lifecycle_rows:
            lifecycle_by_job.setdefault(str(row["job_id"]), {})[str(row["phase"])] = _decode_payload(
                row["payload_json"]
            )
        result: list[CreativeMediaJobRecord] = []
        for row in row_list:
            payload = _decode_payload(row["payload_json"])
            persisted_lifecycle = lifecycle_by_job.get(str(row["job_id"]), {})
            if persisted_lifecycle:
                payload["lifecycle"] = {
                    **dict(payload.get("lifecycle") or {}),
                    **persisted_lifecycle,
                }
            result.append(
                CreativeMediaJobRecord(
                    payload=payload,
                    revision=int(row["revision"] or 0),
                )
            )
        return result

    @staticmethod
    def _job_matches(
        job: dict[str, Any],
        *,
        session_id: str | None,
        status: str | None,
        adapter: str | None,
        modality: str | None,
    ) -> bool:
        if _text(session_id) and _text(job.get("sessionId")) != _text(session_id):
            return False
        return all(
            not _text(expected)
            or _text(job.get(field)).lower() == _text(expected).lower()
            for field, expected in (
                ("status", status),
                ("adapter", adapter),
                ("modality", modality),
            )
        )

    def _put_immutable_ledger(
        self,
        *,
        table: str,
        id_column: str,
        identity: str,
        columns: tuple[str, ...],
        values: tuple[Any, ...],
    ) -> bool:
        if not identity:
            raise ValueError(f"Creative Media {id_column} is required")
        if table not in {"creative_media_cost_entries", "creative_media_safety_events"}:
            raise ValueError("Unsupported Creative Media immutable ledger")
        with self._database.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    f"SELECT payload_json FROM {table} WHERE {id_column} = ?",
                    (identity,),
                ).fetchone()
                payload_index = columns.index("payload_json")
                if existing:
                    if str(existing["payload_json"]) != str(values[payload_index]):
                        raise CreativeMediaStoreConflict(
                            f"Creative Media immutable ledger identity conflict: {identity}"
                        )
                    conn.rollback()
                    return False
                placeholders = ",".join("?" for _ in range(len(columns) + 1))
                conn.execute(
                    f"INSERT INTO {table} ({id_column}, {', '.join(columns)}) "
                    f"VALUES ({placeholders})",
                    (identity, *values),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return True

    @staticmethod
    def _normalize_work_order(
        payload: dict[str, Any],
        *,
        expected_id: str = "",
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Creative Media work order payload must be an object")
        work_order = _without_store_tokens(dict(to_jsonable(payload) or {}))
        work_order_id = _text(work_order.get("workOrderId"))
        if not work_order_id:
            raise ValueError("Creative Media work order requires workOrderId")
        if expected_id and work_order_id != expected_id:
            raise ValueError("Creative Media work order payload does not match workOrderId")
        return work_order

    @staticmethod
    def _work_order_values(work_order: dict[str, Any]) -> tuple[Any, ...]:
        created_at = _text(work_order.get("createdAt")) or utc_now_iso()
        return (
            _text(work_order.get("workOrderId")),
            _text(work_order.get("sessionId")),
            _text(work_order.get("requestingRuntime")),
            _text(work_order.get("status")).lower(),
            _canonical_json(work_order),
            created_at,
            _text(work_order.get("updatedAt")) or created_at,
            _text(work_order.get("archivedAt")) or None,
            _text(work_order.get("deletedAt")) or None,
        )


__all__ = [
    "CreativeMediaJobRecord",
    "CreativeMediaLegacyFormatError",
    "CreativeMediaMigrationResult",
    "CreativeMediaStateRegression",
    "CreativeMediaStore",
    "CreativeMediaStoreConflict",
    "CreativeMediaStoreError",
    "LegacyJobsV1Snapshot",
]
