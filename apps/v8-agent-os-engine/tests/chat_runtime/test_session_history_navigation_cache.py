from __future__ import annotations

from api import session_workflow_routes


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
