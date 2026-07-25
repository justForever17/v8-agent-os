from __future__ import annotations

import asyncio


def test_compact_session_snapshot_keeps_bounded_runtime_timeline(monkeypatch) -> None:
    from api import session_workflow_routes

    timeline = [
        {"seq": index + 1, "topic": "subagent.task.completed", "metadata": {"taskBriefId": f"task-{index + 1}"}}
        for index in range(220)
    ]
    monkeypatch.setattr(
        session_workflow_routes.runtime_command_router,
        "get_snapshot",
        lambda _session_id: {
            "latestSeq": 220,
            "runtimeTimeline": timeline,
            "snapshot": {"messages": [{"id": "message-1"}], "artifacts": []},
        },
    )

    result = asyncio.run(session_workflow_routes.get_session_snapshot("session-compact", compact=1))

    assert len(result["runtimeTimeline"]) == 160
    assert result["runtimeTimeline"][0]["seq"] == 61
    assert result["runtimeTimeline"][-1]["seq"] == 220
    assert result["runtimeTimelineWindow"] == {
        "sourceCount": 220,
        "limit": 160,
        "compacted": True,
    }
    assert result["snapshot"]["messages"] == []


def test_compact_session_snapshot_preserves_historical_runtime_handoff(monkeypatch) -> None:
    from api import session_workflow_routes

    timeline = [
        {"id": "handoff-old", "seq": 1, "topic": "handoff.ref.created", "runtimeId": "creative_media"},
        *[
            {"id": f"noise-{index}", "seq": index + 2, "topic": "extension.route.selected"}
            for index in range(220)
        ],
    ]
    monkeypatch.setattr(
        session_workflow_routes.runtime_command_router,
        "get_snapshot",
        lambda _session_id: {
            "latestSeq": 221,
            "runtimeTimeline": timeline,
            "snapshot": {"messages": [], "artifacts": []},
        },
    )

    result = asyncio.run(session_workflow_routes.get_session_snapshot("session-handoff", compact=1))

    assert len(result["runtimeTimeline"]) == 161
    assert result["runtimeTimeline"][0]["id"] == "handoff-old"
    assert result["runtimeTimelineWindow"] == {
        "sourceCount": 221,
        "limit": 161,
        "compacted": True,
        "recentLimit": 160,
        "historicalMilestoneCount": 1,
    }
