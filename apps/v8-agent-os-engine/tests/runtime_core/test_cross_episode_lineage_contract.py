from __future__ import annotations

from core.runtime_episode_runner import RuntimeEpisodeRunner


def test_agent_visible_cross_episode_result_keeps_delegation_and_invocation_identity():
    projected = RuntimeEpisodeRunner._agent_visible_cross_episode_result({
        "taskBriefId": "task-1", "status": "completed", "summary": "ok",
        "delegationId": "delegation-1", "invocationId": "invocation-1", "toolCallId": "call-1",
        "secret": "must-not-project",
    })
    assert projected["delegationId"] == "delegation-1"
    assert projected["invocationId"] == "invocation-1"
    assert projected["toolCallId"] == "call-1"
    assert "secret" not in projected


def test_compact_cross_episode_result_pairs_lineage_from_source_payload():
    result = RuntimeEpisodeRunner._compact_cross_episode_result(
        task_id="task-1",
        episode={"episodeId": "episode-1", "state": "completed"},
        handoff={"handoffId": "handoff-1", "payload": {"results": [{
            "taskBriefId": "task-1", "status": "completed", "summary": "ok",
            "delegationId": "delegation-1", "invocationId": "invocation-1", "toolCallId": "call-1",
        }]}},
    )
    assert result["delegationId"] == "delegation-1"
    assert result["invocationId"] == "invocation-1"
    assert result["toolCallId"] == "call-1"
