from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager

import pytest

from api import session_workflow_routes
from core.database import DatabaseManager


def test_navigation_status_lookup_is_batched_and_omits_large_metadata():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE run_records (id TEXT PRIMARY KEY, session_id TEXT, status TEXT, started_at TEXT, finished_at TEXT, metadata TEXT)")
    conn.executemany("INSERT INTO run_records VALUES (?, ?, 'completed', '2026-09-08', 'end', 'private')", [(f"run-{i}", f"session-{i}") for i in range(905)])
    conn.execute("INSERT INTO run_records VALUES ('old-run', 'session-0', 'failed', '2026-09-07', 'end', 'private')")
    manager = object.__new__(DatabaseManager)
    @contextmanager
    def connection():
        yield conn
    manager.get_connection = connection
    queries = []
    conn.set_trace_callback(queries.append)
    try:
        rows = manager.get_latest_run_status_records([f"session-{i}" for i in range(905)] + ["session-0", "missing"])
        assert len(rows) == 905 and len(queries) == 2
        assert all("metadata" not in row and row["status"] == "completed" for row in rows)
        assert manager.get_latest_run_status_records([]) == [] and len(queries) == 2
    finally:
        conn.close()


def test_quick_index_invalidates_when_workspace_presentation_changes(tmp_path, monkeypatch):
    projects_path = tmp_path / "config.json"
    projects_path.write_text('{"version":2,"workspacePresentations":[]}', encoding="utf-8")
    monkeypatch.setattr(session_workflow_routes.storage, "base_dir", tmp_path)
    monkeypatch.setattr(
        session_workflow_routes,
        "_WEB_SESSION_INDEX_PATH",
        tmp_path / "web_session_index.json",
    )

    session_workflow_routes._write_web_session_index([])
    assert session_workflow_routes._read_web_session_index_payload() is not None

    projects_path.write_text(
        '{"version":2,"workspacePresentations":[{"workspacePath":"E:/demo","displayName":"Demo"}]}',
        encoding="utf-8",
    )
    assert session_workflow_routes._read_web_session_index_payload() is None


def test_quick_index_overlays_active_run_truth_without_rebuilding(monkeypatch):
    monkeypatch.setattr(session_workflow_routes.db, "get_latest_run_status_records", lambda ids: [])
    payload = {
        "version": session_workflow_routes._WEB_SESSION_INDEX_VERSION,
        "sessions": [
            {
                "id": "session-active",
                "sessionId": "session-active",
                "status": "recoverable_failed",
                "workflowStatus": "recoverable_failed",
                "workflowSummary": {"workflowStatus": "recoverable_failed"},
            }
        ],
    }
    monkeypatch.setattr(
        session_workflow_routes.db,
        "list_active_run_records",
        lambda: [
            {
                "id": "run-current",
                "session_id": "session-active",
                "status": "waiting_input",
                "started_at": "2026-07-12T10:00:00+00:00",
            }
        ],
    )

    projected = session_workflow_routes._overlay_active_run_status(payload)

    assert payload["sessions"][0]["status"] == "recoverable_failed"
    assert projected["sessions"][0]["status"] == "waiting_input"
    assert projected["sessions"][0]["workflowStatus"] == "waiting_input"
    assert projected["sessions"][0]["currentRunId"] == "run-current"
    assert projected["sessions"][0]["workflowSummary"]["workflowStatus"] == "waiting_input"


def test_quick_index_preserves_stale_cache_when_database_projection_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(session_workflow_routes, "_WEB_SESSION_INDEX_PATH", tmp_path / "web_session_index.json")
    monkeypatch.setattr(session_workflow_routes, "_build_web_session_index_records", lambda: (_ for _ in ()).throw(RuntimeError("malformed database schema")))
    session_workflow_routes._write_web_session_index([
        {"id": "stale-session", "sessionId": "stale-session", "title": "保留"},
    ])

    payload = asyncio.run(session_workflow_routes.get_sessions_quick_index(force=1))

    assert payload["degraded"] is True
    assert payload["degradation"]["code"] == "state_database_unavailable"
    assert payload["sessions"][0]["id"] == "stale-session"


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed", "interrupted"])
def test_cached_running_index_reconciles_terminal_truth_without_rebuilding(monkeypatch, status):
    row = {"sessionId": "session", "status": "running", "workflowStatus": "running",
           "currentRunId": "old", "hasPendingApproval": True, "pendingApprovalCount": 1}
    monkeypatch.setattr(session_workflow_routes.db, "list_active_run_records", lambda: [])
    calls = []
    def lookup(ids):
        calls.append(ids)
        return [{"id": "old", "session_id": "session", "status": status, "finished_at": "2026-09-08T01:00:00Z"}]
    monkeypatch.setattr(session_workflow_routes.db, "get_latest_run_status_records", lookup)
    result = session_workflow_routes._overlay_active_run_status({"sessions": [row]})["sessions"][0]
    assert calls == [["session"]]
    assert result["status"] == result["workflowStatus"] == result["stepStatus"] == status
    assert result["endedAt"] == "2026-09-08T01:00:00Z"
    assert not result["hasPendingApproval"] and result["pendingApprovalCount"] == 0
    assert row["status"] == "running"


def test_current_active_run_wins_and_missing_or_foreign_records_do_not_invent_success(monkeypatch):
    rows = [{"sessionId": sid, "currentRunId": "old-" + sid, "status": "running"} for sid in ["new", "missing", "foreign"]]
    monkeypatch.setattr(session_workflow_routes.db, "list_active_run_records", lambda: [
        {"id": "new-run", "session_id": "new", "status": "running"}])
    monkeypatch.setattr(session_workflow_routes.db, "get_latest_run_status_records", lambda ids: [
        {"id": "old-new", "session_id": "new", "status": "completed"},
        {"id": "old-foreign", "session_id": "another-session", "status": "completed"}])
    result = session_workflow_routes._overlay_active_run_status({"sessions": rows})["sessions"]
    assert result[0]["currentRunId"] == "new-run" and result[0]["status"] == "running"
    assert result[1:] == rows[1:]


@pytest.mark.parametrize("cached_status,cached_id", [("idle", None), ("failed", "old"), ("completed", "old")])
def test_fast_or_resumed_run_replaces_cache_even_without_active_record(monkeypatch, cached_status, cached_id):
    monkeypatch.setattr(session_workflow_routes.db, "list_active_run_records", lambda: [])
    monkeypatch.setattr(session_workflow_routes.db, "get_latest_run_status_records", lambda ids: [
        {"id": "latest", "session_id": "session", "status": "completed", "finished_at": "end"}])
    row = {"sessionId": "session", "status": cached_status, "currentRunId": cached_id}
    result = session_workflow_routes._overlay_active_run_status({"sessions": [row]})["sessions"][0]
    assert result["status"] == "completed" and result["currentRunId"] == "latest"


def test_quick_index_returns_structured_503_without_cache_when_database_projection_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(session_workflow_routes, "_WEB_SESSION_INDEX_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(session_workflow_routes, "_build_web_session_index_records", lambda: (_ for _ in ()).throw(RuntimeError("malformed database schema")))

    with pytest.raises(Exception) as caught:
        asyncio.run(session_workflow_routes.get_sessions_quick_index(force=1))

    error = caught.value
    assert getattr(error, "status_code", None) == 503
    assert error.detail["code"] == "state_database_unavailable"
    assert error.detail["retryable"] is True
