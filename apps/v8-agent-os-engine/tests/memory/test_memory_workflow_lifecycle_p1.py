from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.database import DatabaseManager
from runtimes.memory import workflow_service as workflow_module
from runtimes.memory.workflow_service import WorkflowMemoryService


def _config(page_size: int = 2) -> dict:
    return {
        **workflow_module.WORKFLOW_MEMORY_DEFAULTS,
        "minSuccessCount": 1,
        "retention": {
            "pendingGuideTtlHours": 72,
            "episodeDays": 30,
            "hintDays": 30,
            "guideDays": 30,
            "engineeringProofDays": 30,
            "maintenancePageSize": page_size,
        },
    }


def _prepare(monkeypatch, tmp_path: Path) -> tuple[DatabaseManager, WorkflowMemoryService]:
    database = DatabaseManager(tmp_path / "state.db")
    service = WorkflowMemoryService()
    service.export_root = tmp_path / "workflow-exports"
    monkeypatch.setattr(workflow_module, "db", database)
    monkeypatch.setattr(workflow_module, "workflow_memory_config", lambda: _config())
    return database, service


def _add_candidate(service: WorkflowMemoryService, suffix: str, *, session_id: str | None = None, run_id: str | None = None) -> dict:
    episode = service.normalize_episode_payload(
        {
            "id": f"episode-{suffix}",
            "taskFamily": f"Workflow {suffix}",
            "taskFamilySignature": f"wf:{suffix}",
            "canonicalTriggerPatterns": [f"trigger {suffix}"],
            "runtimeEvidence": [{"topic": "tool.finished", "status": "success"}],
            "evidenceSource": "runtime_events",
            "sideEffectScope": "read_only",
            "goldenPathSteps": [f"step {suffix}"],
            "verificationSteps": [f"verify {suffix}"],
            "finalSuccessEvidence": "verified by fixture",
            "confidence": 0.9,
        },
        session_id=session_id,
        run_id=run_id,
        scope="global",
        extraction_source="runtime_evidence",
    )
    return service.add_episode(episode)["candidate"]


def test_guide_state_is_upserted_once_and_terminal_outcome_is_deterministic(monkeypatch, tmp_path: Path) -> None:
    database, service = _prepare(monkeypatch, tmp_path)
    database.create_or_update_session("session-1", "demo", user_id="user")
    database.create_run_record("run-1", "session-1", run_type="chat", status="running")
    candidate = _add_candidate(service, "guide", session_id="session-1", run_id="run-1")

    first = service.record_guide_state(
        candidate_id=candidate["id"],
        query="first query",
        session_id="session-1",
        run_id="run-1",
        state="matched",
    )
    second = service.record_guide_state(
        candidate_id=candidate["id"],
        query="updated query",
        session_id="session-1",
        run_id="run-1",
        state="matched",
        current_step_index=1,
    )
    service.record_hint_event(
        candidate_id=candidate["id"],
        query="updated query",
        hint={"currentStepIndex": 1},
        session_id="session-1",
        run_id="run-1",
        outcome="helped_success",
    )
    finalized = service.finalize_guides_for_run(
        session_id="session-1",
        run_id="run-1",
        run_status="completed",
    )
    repeated = service.finalize_guides_for_run(
        session_id="session-1",
        run_id="run-1",
        run_status="completed",
    )

    assert first["id"] == second["id"]
    assert finalized["finalizedCount"] == 1
    assert finalized["items"][0]["outcome"] == "helped"
    assert repeated["finalizedCount"] == 0
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT state, outcome, current_step_index, finalized_at FROM memory_workflow_guide_states WHERE candidate_id = ? AND run_id = ?",
            (candidate["id"], "run-1"),
        ).fetchall()
        candidate_row = conn.execute(
            "SELECT last_hint_outcome, metadata_json FROM memory_workflow_candidates WHERE id = ?",
            (candidate["id"],),
        ).fetchone()
    assert len(rows) == 1
    assert rows[0]["state"] == "helped"
    assert rows[0]["outcome"] == "helped"
    assert rows[0]["current_step_index"] == 1
    assert rows[0]["finalized_at"]
    assert candidate_row["last_hint_outcome"] == "helped"
    assert json.loads(candidate_row["metadata_json"])["terminalGuideOutcomeCounts"] == {"helped": 1}


def test_pending_guide_ttl_expires_to_ignored(monkeypatch, tmp_path: Path) -> None:
    database, service = _prepare(monkeypatch, tmp_path)
    database.create_or_update_session("session-ttl", "demo", user_id="user")
    database.create_run_record("run-ttl", "session-ttl", run_type="chat", status="running")
    candidate = _add_candidate(service, "ttl", session_id="session-ttl", run_id="run-ttl")
    guide = service.record_guide_state(
        candidate_id=candidate["id"],
        query="ttl query",
        session_id="session-ttl",
        run_id="run-ttl",
        state="matched",
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    with database.get_connection() as conn:
        conn.execute("UPDATE memory_workflow_guide_states SET expires_at = ? WHERE id = ?", (expired_at, guide["id"]))
        conn.commit()

    preview = service.expire_pending_guides(dry_run=True)
    with database.get_connection() as conn:
        before_apply = conn.execute("SELECT state, finalized_at FROM memory_workflow_guide_states WHERE id = ?", (guide["id"],)).fetchone()
    result = service.expire_pending_guides()

    assert preview["expiredCount"] == 1
    assert before_apply["state"] == "matched"
    assert before_apply["finalized_at"] is None
    assert result["expiredCount"] == 1
    with database.get_connection() as conn:
        row = conn.execute("SELECT state, outcome, finalized_at FROM memory_workflow_guide_states WHERE id = ?", (guide["id"],)).fetchone()
    assert row["state"] == "ignored"
    assert row["outcome"] == "ignored"
    assert row["finalized_at"]


def test_nightly_reconciliation_finalizes_guides_for_already_terminal_runs(monkeypatch, tmp_path: Path) -> None:
    database, service = _prepare(monkeypatch, tmp_path)
    database.create_or_update_session("session-terminal", "demo", user_id="user")
    database.create_run_record("run-terminal", "session-terminal", run_type="chat", status="completed")
    candidate = _add_candidate(service, "terminal", session_id="session-terminal", run_id="run-terminal")
    service.record_guide_state(
        candidate_id=candidate["id"],
        query="terminal query",
        session_id="session-terminal",
        run_id="run-terminal",
        state="matched",
    )

    result = service.reconcile_terminal_guides()

    assert result["runCount"] == 1
    assert result["finalizedCount"] == 1
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT state, outcome, finalized_at FROM memory_workflow_guide_states WHERE run_id = 'run-terminal'"
        ).fetchone()
    assert row["state"] == "ignored"
    assert row["outcome"] == "ignored"
    assert row["finalized_at"]


def test_candidate_delete_and_merge_remove_derived_exports(monkeypatch, tmp_path: Path) -> None:
    _, service = _prepare(monkeypatch, tmp_path)
    target = _add_candidate(service, "target")
    source = _add_candidate(service, "source")
    source_paths = service._candidate_export_paths(source["id"])
    assert all(path.exists() for path in source_paths)

    merged = service.merge_candidates(target["id"], [source["id"]])

    assert merged["id"] == target["id"]
    assert all(not path.exists() for path in source_paths)


def test_repeated_hint_delivery_is_compacted_without_losing_delivery_count(monkeypatch, tmp_path: Path) -> None:
    database, service = _prepare(monkeypatch, tmp_path)
    database.create_or_update_session("session-hint", "hint delivery", user_id="user")
    database.create_run_record("run-hint", "session-hint", run_type="chat", status="running")
    candidate = _add_candidate(service, "hint-delivery")

    first = service.record_hint_event(
        candidate_id=candidate["id"],
        query="same query",
        hint={"nextStep": "verify"},
        session_id="session-hint",
        run_id="run-hint",
        outcome="injected",
    )
    second = service.record_hint_event(
        candidate_id=candidate["id"],
        query="same query",
        hint={"nextStep": "verify"},
        session_id="session-hint",
        run_id="run-hint",
        outcome="injected",
    )

    summary = service.dashboard_summary()
    with database.get_connection() as conn:
        persisted_rows = conn.execute(
            "SELECT COUNT(*) FROM memory_workflow_hint_events WHERE candidate_id = ?",
            (candidate["id"],),
        ).fetchone()[0]

    assert first["id"] == second["id"]
    assert second["aggregated"] is True
    assert persisted_rows == 1
    assert summary["hintEventCount"] == 1
    assert summary["hintDeliveryCount7d"] == 2


def test_nightly_candidate_maintenance_advances_cursor_across_full_set(monkeypatch, tmp_path: Path) -> None:
    database, service = _prepare(monkeypatch, tmp_path)
    for index in range(5):
        _add_candidate(service, f"page-{index}")

    pages = [service.maintenance_consolidate() for _ in range(3)]

    assert [page["candidateCount"] for page in pages] == [2, 2, 1]
    assert pages[0]["cursor"]["cursorValue"]
    assert pages[1]["cursor"]["cursorValue"]
    assert pages[2]["cursor"]["cursorValue"] == ""
    assert pages[2]["cursor"]["cycleCount"] == 1
    with database.get_connection() as conn:
        cursor = conn.execute(
            "SELECT cycle_count, last_batch_count FROM memory_maintenance_cursors WHERE phase = 'workflow_candidates'"
        ).fetchone()
    assert cursor["cycle_count"] == 1
    assert cursor["last_batch_count"] == 1


def test_workflow_retention_has_independent_ttls_and_preserves_referenced_evidence(monkeypatch, tmp_path: Path) -> None:
    database, service = _prepare(monkeypatch, tmp_path)
    candidate = _add_candidate(service, "protected")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat().replace("+00:00", "Z")
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO memory_workflow_episodes (id, task_family_signature, created_at, updated_at) VALUES ('episode-old-unreferenced', 'wf:old', ?, ?)",
            (old, old),
        )
        conn.execute(
            "INSERT INTO memory_workflow_hint_events (id, candidate_id, outcome, created_at, updated_at) VALUES ('hint-old-1', ?, 'ignored', ?, ?)",
            (candidate["id"], old, old),
        )
        conn.execute(
            "INSERT INTO memory_workflow_hint_events (id, candidate_id, outcome, created_at, updated_at) VALUES ('hint-old-2', ?, 'ignored', ?, ?)",
            (candidate["id"], old, old),
        )
        conn.execute(
            "INSERT INTO engineering_proof_entries (id, verification_status, created_at, updated_at) VALUES ('proof-old', 'verified', ?, ?)",
            (old, old),
        )
        conn.commit()

    dry_run = service.maintenance_retention(dry_run=True)
    applied = service.maintenance_retention(dry_run=False)

    assert dry_run["candidateCounts"]["episodes"] == 1
    assert dry_run["candidateCounts"]["hints"] == 1
    assert dry_run["candidateCounts"]["engineeringProofs"] == 1
    assert applied["candidateCounts"] == dry_run["candidateCounts"]
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_workflow_episodes WHERE id = 'episode-old-unreferenced'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_workflow_episodes WHERE id = ?", (candidate["sourceEpisodeIds"][0],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_workflow_hint_events WHERE candidate_id = ?", (candidate["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM engineering_proof_entries WHERE id = 'proof-old'").fetchone()[0] == 0


def test_legacy_guide_states_receive_current_marker_ttl_and_finalization(tmp_path: Path) -> None:
    path = tmp_path / "legacy-state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE memory_workflow_guide_states (
                id TEXT PRIMARY KEY,
                candidate_id TEXT,
                session_id TEXT,
                run_id TEXT,
                query TEXT,
                state TEXT,
                current_step_index INTEGER DEFAULT 0,
                last_event_topic TEXT,
                outcome TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO memory_workflow_guide_states
            (id, candidate_id, session_id, run_id, query, state, created_at, updated_at)
            VALUES (?, 'candidate-legacy', 'session-legacy', 'run-legacy', 'query', 'step_1_pending', ?, ?)
            """,
            [
                ("guide-old", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                ("guide-current", "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z"),
            ],
        )
        conn.commit()

    database = DatabaseManager(path)
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, is_current, expires_at, finalized_at FROM memory_workflow_guide_states ORDER BY id"
        ).fetchall()

    by_id = {row["id"]: row for row in rows}
    assert by_id["guide-old"]["is_current"] == 0
    assert by_id["guide-old"]["finalized_at"]
    assert by_id["guide-old"]["expires_at"] is None
    assert by_id["guide-current"]["is_current"] == 1
    assert by_id["guide-current"]["expires_at"]
    assert by_id["guide-current"]["finalized_at"] is None
