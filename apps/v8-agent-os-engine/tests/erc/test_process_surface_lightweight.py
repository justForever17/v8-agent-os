import asyncio

from erc.session_realtime_contract import build_lightweight_processes_snapshot


def test_lightweight_process_surface_filters_to_session_or_run(monkeypatch):
    def fake_processes(**_kwargs):
        return [
            {"processId": "cmd-a", "commandId": "cmd-a", "sessionId": "session-a", "runId": "run-a"},
            {"processId": "cmd-b", "commandId": "cmd-b", "sessionId": "session-b", "runId": "run-b"},
            {"processId": "cmd-c", "commandId": "cmd-c", "sessionId": None, "runId": "run-a"},
            {"processId": "cmd-d", "commandId": "cmd-d", "sessionId": None, "runId": None},
        ]

    monkeypatch.setattr("core.native_tools.list_background_process_snapshots", fake_processes)

    surface = build_lightweight_processes_snapshot(session_id="session-a", run_id="run-a")

    assert [item["processId"] for item in surface] == ["cmd-a", "cmd-c"]
    assert surface[0]["sessionId"] == "session-a"
    assert surface[1]["runId"] == "run-a"


def test_session_processes_route_does_not_refresh_chat_projection(monkeypatch):
    from api import session_workflow_routes as routes

    monkeypatch.setattr(routes.db, "get_session", lambda session_id: {"id": session_id, "title": "Long Session"})
    monkeypatch.setattr(routes.db, "get_latest_runtime_seq", lambda _session_id: 42)
    monkeypatch.setattr(
        routes.workflow_ledger_service,
        "get_session_workflow_view",
        lambda _session_id: {"rootRunId": "run-root"},
    )
    monkeypatch.setattr(
        routes.session_admission_service,
        "get_lane_view",
        lambda _session_id: {"activeRunId": "run-active"},
    )

    def fail_if_projection_is_used(_session_id):
        raise AssertionError("process surface must not refresh full chat projection")

    monkeypatch.setattr(routes.snapshot_service, "build_chat_projection_payload", fail_if_projection_is_used)
    monkeypatch.setattr(
        routes,
        "build_lightweight_processes_snapshot",
        lambda *, session_id, run_id: [{"processId": "cmd-a", "sessionId": session_id, "runId": run_id}],
    )

    payload = asyncio.run(routes.get_session_processes("session-a"))

    assert payload["sessionId"] == "session-a"
    assert payload["currentRunId"] == "run-active"
    assert payload["latestSeq"] == 42
    assert payload["processes"] == [{"processId": "cmd-a", "sessionId": "session-a", "runId": "run-active"}]
    assert payload["_profile"]["processSurfaceMode"] == "lightweight"
