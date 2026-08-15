from __future__ import annotations

import asyncio


def test_canvas_only_session_history_allows_missing_workflow_ledger(monkeypatch) -> None:
    from api import session_workflow_routes

    session_id = "canvas-history-session"
    runtime_events = [
        {
            "seq": 1,
            "event_id": "canvas-event-1",
            "session_id": session_id,
            "topic": "canvas.graph.run.state",
            "run_id": "canvas-run-1",
            "payload": {
                "schema": "v8.creative_canvas_graph_run_state.v1",
                "sessionId": session_id,
                "workspaceId": "workspace-1",
                "graphId": "graph-1",
                "graphRunId": "canvas-run-1",
                "canvasOperationId": "canvas-operation-1",
                "runId": "canvas-run-1",
                "status": "completed",
            },
        }
    ]

    monkeypatch.setattr(
        session_workflow_routes.db,
        "get_session",
        lambda requested: {"id": requested, "title": "Canvas", "metadata": {}},
    )
    monkeypatch.setattr(session_workflow_routes.db, "get_runtime_events", lambda _session_id: runtime_events)
    monkeypatch.setattr(
        session_workflow_routes.workflow_ledger_service,
        "get_session_workflow_view",
        lambda _session_id: None,
    )
    monkeypatch.setattr(session_workflow_routes.db, "list_pending_approvals", lambda **_kwargs: [])
    monkeypatch.setattr(session_workflow_routes.db, "list_ask_user_interactions", lambda **_kwargs: [])
    monkeypatch.setattr(session_workflow_routes.db, "get_run_record", lambda _run_id: None)
    monkeypatch.setattr(session_workflow_routes, "build_canonical_chat_messages", lambda _session_id: [])
    monkeypatch.setattr(session_workflow_routes, "_filter_deleted_messages", lambda _session_id, messages: list(messages or []))
    monkeypatch.setattr(session_workflow_routes, "_filter_deleted_artifacts", lambda _session_id, artifacts: list(artifacts or []))
    monkeypatch.setattr(
        session_workflow_routes.snapshot_service,
        "build_chat_projection_payload",
        lambda _session_id: {
            "latestSeq": 1,
            "snapshot": {"messages": [], "artifacts": []},
            "sessionCoordinationMessages": [],
        },
    )

    result = asyncio.run(session_workflow_routes.get_session_history(session_id))

    assert result["record"]["sessionId"] == session_id
    assert result["record"]["workflowStatus"] == "idle"
    assert result["runtimeTimeline"][0]["topic"] == "canvas.graph.run.state"
    assert result["ledger"][0]["eventName"] == "canvas.graph.run.state"
