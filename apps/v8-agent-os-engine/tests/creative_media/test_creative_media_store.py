from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from core.database import DatabaseManager
from runtimes.creative_media.store import (
    CreativeMediaLegacyFormatError,
    CreativeMediaStateRegression,
    CreativeMediaStore,
    CreativeMediaStoreConflict,
)


NOW = "2026-08-18T00:00:00Z"


def _store(tmp_path: Path) -> tuple[DatabaseManager, CreativeMediaStore]:
    database = DatabaseManager(tmp_path / "state.db")
    return database, CreativeMediaStore(database)


def _job(
    job_id: str,
    *,
    session_id: str = "session-A",
    status: str = "queued",
    adapter: str = "dashscope",
    lifecycle: dict | None = None,
) -> dict:
    payload = {
        "jobId": job_id,
        "sessionId": session_id,
        "runId": "run-A",
        "workspaceId": "workspace-A",
        "projectId": "project-A",
        "modality": "video",
        "adapter": adapter,
        "operationKind": "video.reference_to_video",
        "status": status,
        "request": {"prompt": "test"},
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    if lifecycle is not None:
        payload["lifecycle"] = lifecycle
    return payload


def _write_jobs_v1(path: Path, jobs: list[dict]) -> bytes:
    payload = {
        "version": 1,
        "jobs": {str(job["jobId"]): job for job in jobs},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_job_cas_synchronizes_lifecycle_and_projection_without_reopening_terminal(
    tmp_path: Path,
) -> None:
    database, store = _store(tmp_path)
    lifecycle = {
        "remoteReconcile": {
            "status": "uncertain",
            "detailCode": "provider_status_pending",
            "remoteTaskMayContinue": True,
            "attempt": 2,
            "nextReconcileAt": "2026-08-18T00:01:00Z",
            "projectionPending": True,
            "projectionAttempts": 1,
            "nextProjectionAt": "2026-08-18T00:00:30Z",
            "terminalProof": {"providerStatus": "failed", "taskId": "provider-1"},
            "reconciledAt": NOW,
        }
    }
    created = store.create_job(_job("cm_job", lifecycle=lifecycle))

    assert created.revision == 1
    assert store.get_job("cm_job").payload["lifecycle"] == lifecycle
    assert [item.payload["jobId"] for item in store.list_reconcile_candidates(
        due_at="2026-08-18T00:02:00Z"
    )] == ["cm_job"]
    projections = store.list_pending_projections(due_at="2026-08-18T00:01:00Z")
    assert [(item["jobId"], item["projectionKind"]) for item in projections] == [
        ("cm_job", "canvas_graph_terminal")
    ]

    cancelled_payload = dict(created.payload)
    cancelled_payload.update({"status": "cancelled", "updatedAt": "2026-08-18T00:03:00Z"})
    cancelled = store.compare_and_swap_job(
        "cm_job", expected_revision=1, payload=cancelled_payload
    )
    reopened_payload = dict(cancelled.payload)
    reopened_payload.update({"status": "running", "updatedAt": "2026-08-18T00:04:00Z"})

    with pytest.raises(CreativeMediaStateRegression):
        store.compare_and_swap_job(
            "cm_job", expected_revision=2, payload=reopened_payload
        )

    persisted = store.get_job("cm_job")
    assert persisted.revision == 2
    assert persisted.payload["status"] == "cancelled"
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT projection_pending, next_reconcile_at FROM creative_media_jobs WHERE job_id = ?",
            ("cm_job",),
        ).fetchone()
    assert dict(row) == {
        "projection_pending": 1,
        "next_reconcile_at": "2026-08-18T00:01:00Z",
    }


def test_concurrent_job_cas_has_exactly_one_winner(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    original = store.create_job(_job("cm_race"))
    barrier = Barrier(12)

    def compete(index: int) -> str:
        payload = dict(original.payload)
        payload.update({
            "status": "running",
            "worker": index,
            "updatedAt": f"2026-08-18T00:00:{index:02d}Z",
        })
        barrier.wait(timeout=5)
        try:
            store.compare_and_swap_job(
                "cm_race", expected_revision=original.revision, payload=payload
            )
        except CreativeMediaStoreConflict:
            return "conflict"
        return "updated"

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(compete, range(12)))

    assert results.count("updated") == 1
    assert results.count("conflict") == 11
    assert store.get_job("cm_race").revision == 2


def test_terminal_projection_cannot_be_reopened(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    projected = _job(
        "cm_projected",
        lifecycle={
            "remoteReconcile": {
                "status": "resolved",
                "detailCode": "provider_terminal_status_confirmed",
                "projectionPending": False,
                "projectionDisposition": "applied",
                "projectedAt": NOW,
                "terminalProof": {"providerStatus": "succeeded", "taskId": "provider-1"},
            }
        },
    )
    created = store.create_job(projected)
    reopened = dict(created.payload)
    reopened["lifecycle"] = {
        "remoteReconcile": {
            **dict(created.payload["lifecycle"]["remoteReconcile"]),
            "projectionPending": True,
            "projectionDisposition": "transient",
            "projectedAt": None,
        }
    }

    with pytest.raises(CreativeMediaStateRegression, match="terminal projection"):
        store.compare_and_swap_job(
            "cm_projected",
            expected_revision=created.revision,
            payload=reopened,
        )

    assert store.get_job("cm_projected").revision == 1
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT state, projection_pending FROM creative_media_job_projections "
            "WHERE job_id = ?",
            ("cm_projected",),
        ).fetchone()
    assert dict(row) == {"state": "applied", "projection_pending": 0}


def test_jobs_json_v1_migration_is_idempotent_sqlite_first_and_source_preserving(
    tmp_path: Path,
) -> None:
    database, store = _store(tmp_path)
    jobs_path = tmp_path / "jobs.json"
    original_bytes = _write_jobs_v1(
        jobs_path,
        [_job("cm_1"), _job("cm_2", status="running")],
    )

    legacy = store.get_job("cm_1", legacy_jobs_path=jobs_path)
    assert (legacy.source, legacy.revision) == ("legacy_json_v1", 0)

    first = store.migrate_jobs_json_v1(jobs_path)
    second = store.migrate_jobs_json_v1(jobs_path)

    assert (first.imported_count, first.skipped_count, first.already_applied) == (2, 0, False)
    assert (second.imported_count, second.skipped_count, second.already_applied) == (2, 0, True)
    assert jobs_path.read_bytes() == original_bytes
    assert store.get_job("cm_1", legacy_jobs_path=jobs_path).source == "sqlite"

    changed_job = _job("cm_1", status="failed")
    _write_jobs_v1(jobs_path, [changed_job, _job("cm_2", status="running"), _job("cm_3")])
    incremental = store.migrate_jobs_json_v1(jobs_path)

    assert (incremental.imported_count, incremental.skipped_count) == (1, 2)
    assert store.get_job("cm_1").payload["status"] == "queued"
    assert store.get_job("cm_3").payload["jobId"] == "cm_3"
    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM creative_media_store_migrations"
        ).fetchone()[0] == 2


def test_jobs_json_v1_migration_rolls_back_all_rows_and_receipt_on_sql_failure(
    tmp_path: Path,
) -> None:
    database, store = _store(tmp_path)
    jobs_path = tmp_path / "jobs.json"
    source_bytes = _write_jobs_v1(jobs_path, [_job("cm_1"), _job("cm_2")])
    with database.get_connection() as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_second_creative_media_job
            BEFORE INSERT ON creative_media_jobs
            WHEN NEW.job_id = 'cm_2'
            BEGIN
                SELECT RAISE(ABORT, 'simulated migration write failure');
            END
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="simulated migration write failure"):
        store.migrate_jobs_json_v1(jobs_path)

    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creative_media_jobs").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM creative_media_store_migrations"
        ).fetchone()[0] == 0
    assert jobs_path.read_bytes() == source_bytes


def test_jobs_json_v1_rejects_ambiguous_or_malformed_envelopes(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(
        json.dumps({"version": 1, "jobs": {"cm_1": {"jobId": "cm_other", "status": "queued"}}}),
        encoding="utf-8",
    )

    with pytest.raises(CreativeMediaLegacyFormatError, match="does not match jobId"):
        store.migrate_jobs_json_v1(jobs_path)


def test_operational_ledgers_and_work_order_cas_are_atomic(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    cost = {
        "entryId": "cost-1",
        "jobId": "cm_job",
        "sessionId": "session-A",
        "status": "succeeded",
        "provider": "provider-A",
        "model": "model-A",
        "operationKind": "image.generate",
        "createdAt": NOW,
    }
    assert store.put_cost_entry(cost) is True
    assert store.put_cost_entry(cost) is False
    with pytest.raises(CreativeMediaStoreConflict):
        store.put_cost_entry({**cost, "status": "failed"})

    safety = {
        "eventId": "safety-1",
        "jobId": "cm_job",
        "sessionId": "session-A",
        "source": "job_create",
        "policy": "default",
        "createdAt": NOW,
    }
    assert store.put_safety_event(safety) is True
    assert store.put_safety_event(safety) is False

    quality = {
        "qualityJobId": "quality-1",
        "jobId": "cm_job",
        "sessionId": "session-A",
        "status": "review_required",
        "qualityProfile": "storyboard_frame",
        "createdAt": NOW,
    }
    quality_snapshot = store.save_quality_job(quality)
    store.save_quality_job(
        {
            **quality_snapshot,
            "status": "passed",
            "updatedAt": "2026-08-18T00:01:00Z",
        }
    )
    assert store.get_quality_job("quality-1")["status"] == "passed"

    work_order = {
        "workOrderId": "work-1",
        "sessionId": "session-A",
        "requestingRuntime": "creative_media",
        "status": "planned",
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    _, revision = store.create_work_order(work_order)
    _, next_revision = store.compare_and_swap_work_order(
        "work-1",
        expected_revision=revision,
        payload={**work_order, "status": "archived", "archivedAt": "2026-08-18T00:02:00Z"},
    )
    assert next_revision == 2
    with pytest.raises(CreativeMediaStateRegression, match="terminal work order"):
        store.compare_and_swap_work_order(
            "work-1",
            expected_revision=next_revision,
            payload={**work_order, "status": "planned"},
        )
    with pytest.raises(CreativeMediaStoreConflict):
        store.compare_and_swap_work_order(
            "work-1", expected_revision=revision, payload=work_order
        )


def test_quality_terminal_state_is_monotonic(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    quality = {
        "qualityJobId": "quality-terminal",
        "jobId": "cm_job",
        "sessionId": "session-A",
        "status": "passed",
        "qualityProfile": "storyboard_frame",
        "createdAt": NOW,
    }
    store.save_quality_job(quality)

    with pytest.raises(CreativeMediaStateRegression, match="terminal quality"):
        store.save_quality_job(
            {
                **quality,
                "status": "review_required",
                "updatedAt": "2026-08-18T00:01:00Z",
            }
        )

    assert store.get_quality_job("quality-terminal")["status"] == "passed"


def test_over_ten_thousand_jobs_are_fully_paginated_and_index_query_stays_bounded(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    jobs_path = tmp_path / "jobs.json"
    jobs = []
    for index in range(10_025):
        job = _job(
            f"cm_{index:05d}",
            session_id=f"session-{index % 20:02d}",
            status="running" if index % 2 == 0 else "queued",
            adapter="dashscope" if index % 3 == 0 else "volcengine_ark",
        )
        job["updatedAt"] = f"2026-08-18T{index % 24:02d}:{index % 60:02d}:{index % 60:02d}Z"
        jobs.append(job)
    _write_jobs_v1(jobs_path, jobs)
    result = store.migrate_jobs_json_v1(jobs_path)
    assert result.imported_count == 10_025
    assert sum(1 for _record in store.iter_jobs(batch_size=777)) == 10_025

    started = time.perf_counter()
    records = store.list_jobs(
        session_id="session-06",
        status="running",
        adapter="dashscope",
        limit=100,
    )
    elapsed = time.perf_counter() - started

    assert len(records) == 100
    assert elapsed < 0.5
    with database.get_connection() as conn:
        plan = " ".join(
            str(row[3])
            for row in conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT * FROM creative_media_jobs
                WHERE session_id = ? AND status = ? AND adapter = ?
                ORDER BY updated_at DESC, job_id DESC LIMIT 100
                """,
                ("session-06", "running", "dashscope"),
            ).fetchall()
        ).upper()
    assert "INDEX" in plan


def test_explicit_state_dags_and_snapshot_tokens_reject_stale_writers(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    created = store.create_job(_job("cm_dag"))
    second_snapshot = store.get_job("cm_dag")
    assert second_snapshot is not None

    running = store.compare_and_swap_job(
        "cm_dag",
        expected_revision=created.revision,
        expected_status="queued",
        expected_updated_at=NOW,
        payload={**created.payload, "status": "running", "updatedAt": "2026-08-18T00:01:00Z"},
    )
    with pytest.raises(CreativeMediaStoreConflict, match="revision conflict"):
        store.compare_and_swap_job(
            "cm_dag",
            expected_revision=second_snapshot.revision,
            expected_status="queued",
            expected_updated_at=NOW,
            payload={**second_snapshot.payload, "status": "failed"},
        )
    with pytest.raises(CreativeMediaStateRegression):
        store.compare_and_swap_job(
            "cm_dag",
            expected_revision=running.revision,
            expected_status="running",
            expected_updated_at="2026-08-18T00:01:00Z",
            payload={**running.payload, "status": "queued", "updatedAt": "2026-08-18T00:02:00Z"},
        )
    cancelling = store.compare_and_swap_job(
        "cm_dag",
        expected_revision=running.revision,
        expected_status="running",
        expected_updated_at="2026-08-18T00:01:00Z",
        payload={**running.payload, "status": "cancelling", "updatedAt": "2026-08-18T00:02:00Z"},
    )
    with pytest.raises(CreativeMediaStateRegression):
        store.compare_and_swap_job(
            "cm_dag",
            expected_revision=cancelling.revision,
            expected_status="cancelling",
            expected_updated_at="2026-08-18T00:02:00Z",
            payload={**cancelling.payload, "status": "running", "updatedAt": "2026-08-18T00:03:00Z"},
        )

    quality = store.save_quality_job(
        {
            "qualityJobId": "quality-dag",
            "jobId": "cm_dag",
            "sessionId": "session-A",
            "status": "queued",
            "createdAt": NOW,
            "updatedAt": NOW,
        }
    )
    stale_quality = dict(quality)
    quality["status"] = "running"
    quality["updatedAt"] = "2026-08-18T00:01:00Z"
    store.save_quality_job(quality)
    with pytest.raises(CreativeMediaStoreConflict, match="revision conflict"):
        store.save_quality_job({**stale_quality, "status": "failed"})

    work_order = {
        "workOrderId": "work-dag",
        "sessionId": "session-A",
        "status": "planned",
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    _, work_revision = store.create_work_order(work_order)
    running_work, running_revision = store.compare_and_swap_work_order(
        "work-dag",
        expected_revision=work_revision,
        expected_status="planned",
        expected_updated_at=NOW,
        payload={**work_order, "status": "running", "updatedAt": "2026-08-18T00:01:00Z"},
    )
    with pytest.raises(CreativeMediaStateRegression):
        store.compare_and_swap_work_order(
            "work-dag",
            expected_revision=running_revision,
            expected_status="running",
            expected_updated_at="2026-08-18T00:01:00Z",
            payload={**running_work, "status": "planned", "updatedAt": "2026-08-18T00:02:00Z"},
        )


def test_v1_operational_sources_migrate_atomically_idempotently_and_sqlite_wins(
    tmp_path: Path,
) -> None:
    database, store = _store(tmp_path)
    store.put_cost_entry(
        {
            "entryId": "cost-legacy",
            "jobId": "job-sqlite",
            "sessionId": "session-A",
            "status": "succeeded",
            "provider": "sqlite-wins",
            "createdAt": NOW,
        }
    )
    sources = {
        "jobs": ("jobs.json", "jobs", "cm-legacy", _job("cm-legacy")),
        "work_orders": (
            "work_orders.json", "workOrders", "work-legacy",
            {"workOrderId": "work-legacy", "sessionId": "session-A", "workspaceId": "workspace-A", "projectId": "project-A", "workspacePath": str(tmp_path), "status": "planned", "createdAt": NOW, "updatedAt": NOW},
        ),
        "cost_entries": (
            "cost_ledger.json", "entries", "cost-legacy",
            {"entryId": "cost-legacy", "jobId": "job-json", "sessionId": "session-A", "workspaceId": "workspace-A", "workspacePath": str(tmp_path), "status": "failed", "provider": "json-loses", "createdAt": NOW},
        ),
        "quality_jobs": (
            "quality_jobs.json", "qualityJobs", "quality-legacy",
            {"qualityJobId": "quality-legacy", "jobId": "cm-legacy", "sessionId": "session-A", "workspaceId": "workspace-A", "workspacePath": str(tmp_path), "status": "passed", "createdAt": NOW},
        ),
        "safety_events": (
            "safety_events.json", "events", "safety-legacy",
            {"eventId": "safety-legacy", "jobId": "cm-legacy", "sessionId": "session-A", "workspaceId": "workspace-A", "workspacePath": str(tmp_path), "source": "migration", "createdAt": NOW},
        ),
    }
    paths: dict[str, Path] = {}
    original_bytes: dict[str, bytes] = {}
    for name, (filename, envelope_key, identity, payload) in sources.items():
        path = tmp_path / filename
        raw = json.dumps(
            {"version": 1, envelope_key: {identity: payload}},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        path.write_bytes(raw)
        paths[name] = path
        original_bytes[name] = raw

    first = store.migrate_legacy_v1_sources(
        jobs_path=paths["jobs"], work_orders_path=paths["work_orders"],
        cost_entries_path=paths["cost_entries"], quality_jobs_path=paths["quality_jobs"],
        safety_events_path=paths["safety_events"],
    )
    second = store.migrate_legacy_v1_sources(
        jobs_path=paths["jobs"], work_orders_path=paths["work_orders"],
        cost_entries_path=paths["cost_entries"], quality_jobs_path=paths["quality_jobs"],
        safety_events_path=paths["safety_events"],
    )

    assert first["jobs"].imported_count == 1
    assert first["cost_entries"].skipped_count == 1
    assert all(item.already_applied for item in second.values())
    assert all(paths[name].read_bytes() == original_bytes[name] for name in paths)
    assert store.list_cost_entries(session_id="session-A")[0]["provider"] == "sqlite-wins"
    assert store.get_quality_job("quality-legacy")["status"] == "passed"
    assert store.get_work_order("work-legacy")[0]["status"] == "planned"
    assert store.list_safety_events(session_id="session-A")[0]["eventId"] == "safety-legacy"
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creative_media_store_migrations").fetchone()[0] == 5


def test_v1_ledgers_backfill_exact_job_owner_and_skip_unowned_records(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    workspace = tmp_path / "workspace-A"
    workspace.mkdir()
    job = {**_job("cm-owner"), "workspacePath": str(workspace)}
    jobs_path = tmp_path / "jobs.json"
    original_jobs = _write_jobs_v1(jobs_path, [job])

    cost_path = tmp_path / "cost_ledger.json"
    cost_payload = {
        "version": 1,
        "entries": {
            "cost-owned": {
                "entryId": "cost-owned",
                "jobId": "cm-owner",
                "status": "succeeded",
                "createdAt": NOW,
            },
            "cost-orphan": {
                "entryId": "cost-orphan",
                "jobId": "missing-job",
                "sessionId": "session-B",
                "workspaceId": "workspace-B",
                "workspacePath": str(tmp_path / "workspace-B"),
                "status": "failed",
                "createdAt": NOW,
            },
        },
    }
    original_cost = json.dumps(cost_payload, sort_keys=True).encode("utf-8")
    cost_path.write_bytes(original_cost)

    safety_path = tmp_path / "safety_events.json"
    safety_payload = {
        "version": 1,
        "events": {
            "safety-owned": {
                "eventId": "safety-owned",
                "jobId": "cm-owner",
                "source": "migration",
                "createdAt": NOW,
            },
            "safety-conflict": {
                "eventId": "safety-conflict",
                "jobId": "cm-owner",
                "sessionId": "session-B",
                "source": "migration",
                "createdAt": NOW,
            },
            "safety-orphan": {
                "eventId": "safety-orphan",
                "jobId": "missing-job",
                "sessionId": "session-B",
                "workspaceId": "workspace-B",
                "workspacePath": str(tmp_path / "workspace-B"),
                "source": "migration",
                "createdAt": NOW,
            },
        },
    }
    original_safety = json.dumps(safety_payload, sort_keys=True).encode("utf-8")
    safety_path.write_bytes(original_safety)

    result = store.migrate_legacy_v1_sources(
        jobs_path=jobs_path,
        cost_entries_path=cost_path,
        safety_events_path=safety_path,
    )

    assert result["cost_entries"].imported_count == 1
    assert result["cost_entries"].skipped_count == 1
    assert result["cost_entries"].skip_reason_counts == {"job_owner_missing": 1}
    assert result["safety_events"].imported_count == 1
    assert result["safety_events"].skipped_count == 2
    assert result["safety_events"].skip_reason_counts == {
        "job_owner_missing": 1,
        "owner_scope_conflict": 1,
    }
    assert store.list_cost_entries(session_id="session-A") == [
        {
            **cost_payload["entries"]["cost-owned"],
            "sessionId": "session-A",
            "workspaceId": "workspace-A",
            "projectId": "project-A",
            "workspacePath": str(workspace),
        }
    ]
    assert store.list_safety_events(session_id="session-A") == [
        {
            **safety_payload["events"]["safety-owned"],
            "sessionId": "session-A",
            "workspaceId": "workspace-A",
            "projectId": "project-A",
            "workspacePath": str(workspace),
        }
    ]
    assert jobs_path.read_bytes() == original_jobs
    assert cost_path.read_bytes() == original_cost
    assert safety_path.read_bytes() == original_safety
    with database.get_connection() as conn:
        receipts = {
            row["source_kind"]: json.loads(row["skip_reasons_json"])
            for row in conn.execute(
                """
                SELECT source_kind, skip_reasons_json
                FROM creative_media_store_migrations
                WHERE skipped_count > 0
                """
            ).fetchall()
        }
    assert receipts["creative_media_cost_ledger_json_v1"] == {"job_owner_missing": 1}
    assert receipts["creative_media_safety_events_json_v1"] == {
        "job_owner_missing": 1,
        "owner_scope_conflict": 1,
    }


def test_v1_multi_source_parse_failure_rolls_back_every_source(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    jobs_path = tmp_path / "jobs.json"
    _write_jobs_v1(jobs_path, [_job("cm-rollback")])
    malformed = tmp_path / "quality_jobs.json"
    malformed.write_text('{"version":1,"qualityJobs":[]}', encoding="utf-8")

    with pytest.raises(CreativeMediaLegacyFormatError):
        store.migrate_legacy_v1_sources(jobs_path=jobs_path, quality_jobs_path=malformed)

    assert store.get_job("cm-rollback") is None
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creative_media_store_migrations").fetchone()[0] == 0


def test_owner_scoped_ledgers_reject_unowned_writes_and_filter_cross_session(
    tmp_path: Path,
) -> None:
    _, store = _store(tmp_path)
    with pytest.raises(ValueError, match="sessionId"):
        store.put_cost_entry({"entryId": "cost-missing"})
    with pytest.raises(ValueError, match="sessionId"):
        store.put_safety_event({"eventId": "safety-missing"})
    assert store.list_cost_entries() == []
    assert store.list_safety_events() == []

    for session_id in ("session-A", "session-B"):
        store.put_cost_entry(
            {"entryId": f"cost-{session_id}", "sessionId": session_id,
             "workspaceId": "workspace-shared", "status": "succeeded", "createdAt": NOW}
        )
        store.put_safety_event(
            {"eventId": f"safety-{session_id}", "sessionId": session_id,
             "workspaceId": "workspace-shared", "source": "test", "createdAt": NOW}
        )
    assert [item["entryId"] for item in store.list_cost_entries(session_id="session-A")] == ["cost-session-A"]
    assert [item["eventId"] for item in store.list_safety_events(session_id="session-B")] == ["safety-session-B"]
