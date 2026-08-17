from __future__ import annotations

from types import SimpleNamespace

from core.database import DatabaseManager
from core.runtime_projection import project_runtime_timeline_from_events
from core.storage_retention import StorageRetentionService
import core.storage_retention as storage_retention_module
from erc.session_history_contract import build_session_history_detail
import erc.session_history_contract as session_history_contract_module
import erc.snapshot_service as snapshot_service_module


SESSION_ID = "retention-collaboration-session"
RUN_ID = "retention-collaboration-run"
EPISODE_ID = "retention-collaboration-episode"

MILESTONE_TOPICS = [
    "runtime.episode.queued",
    "runtime.episode.started",
    "delegation.child.requested",
    "runtime.episode.waiting",
    "runtime.episode.resumed",
    "handoff.ref.created",
    "runtime.episode.completed",
    "subagent.task.completed",
]

COMPRESSIBLE_TOPICS = [
    "runtime.episode.progress",
    "subagent.text.delta",
    "subagent.tool.started",
    "delegation.progress",
]


def _event_payload(topic: str) -> dict:
    episode = {
        "episodeId": EPISODE_ID,
        "kind": "delegation",
        "state": topic.rsplit(".", 1)[-1],
        "reason": f"state:{topic}",
    }
    if topic == "runtime.episode.progress":
        return {
            "runtimeId": "subagent_swarm",
            "episode": episode,
            "progress": {
                "summary": "compressible worker progress",
                "agentName": "verification-engineer",
                "timelineNode": {
                    "id": "progress-node-1",
                    "kind": "narrative",
                    "topic": "subagent.text.delta",
                    "content": "intermediate progress",
                },
            },
        }
    if topic == "subagent.text.delta":
        return {
            "runtimeId": "subagent_swarm",
            "ownerAgentId": "verification-engineer",
            "delegationId": EPISODE_ID,
            "segmentKey": "segment-1",
            "snapshot": "intermediate child text",
        }
    if topic == "subagent.tool.started":
        return {
            "runtimeId": "subagent_swarm",
            "ownerAgentId": "verification-engineer",
            "tool": {"toolCallId": "tool-1", "toolName": "read_file", "args": {}},
        }
    if topic == "delegation.progress":
        return {
            "delegationId": EPISODE_ID,
            "summary": "external delegation heartbeat",
            "status": "running",
        }
    if topic == "delegation.child.requested":
        return {
            "runtimeId": "subagent_swarm",
            "episode": episode,
            "childDelegation": {
                "childDelegationId": "child-episode-1",
                "childAgentName": "verification-engineer",
                "summary": "child verification dispatched",
            },
        }
    if topic == "handoff.ref.created":
        return {
            "runtimeId": "subagent_swarm",
            "handoffRef": {
                "handoffId": "handoff-retention-1",
                "handoffRefId": "handoff-retention-1",
                "producerEpisodeId": EPISODE_ID,
                "kind": "subagent_result",
                "status": "ready",
                "compactSummary": "verification evidence delivered",
            },
        }
    if topic == "subagent.task.completed":
        return {
            "runtimeId": "subagent_swarm",
            "delegationId": EPISODE_ID,
            "taskBriefId": "brief-retention-1",
            "subagentId": "verification-engineer",
            "status": "completed",
            "summary": "verification task completed",
            "supervisorAcceptance": {
                "status": "accepted",
                "summary": "accepted after proof review",
            },
        }
    return {"runtimeId": "subagent_swarm", "episode": episode}


def _seed_collaboration_run(manager: DatabaseManager) -> None:
    manager.create_or_update_session(SESSION_ID, "retention", user_id="user")
    manager.create_run_record(RUN_ID, SESSION_ID, run_type="chat", status="running")
    manager.create_chat_canonical_message(
        message_id="canonical-retention-1",
        session_id=SESSION_ID,
        run_id=RUN_ID,
        ordinal=1,
        role="assistant",
        state="finalized",
        nodes=[{"type": "text", "text": "final supervisor answer"}],
        content_text="final supervisor answer",
    )
    manager.upsert_runtime_episode_record(
        {
            "episodeId": EPISODE_ID,
            "kind": "delegation",
            "state": "completed",
            "reason": "verify retention parity",
            "idempotencyKey": "retention-collaboration-idempotency",
        },
        session_id=SESSION_ID,
        run_id=RUN_ID,
    )
    manager.add_runtime_episode_event_record(
        episode_id=EPISODE_ID,
        topic="runtime.episode.completed",
        payload=_event_payload("runtime.episode.completed"),
        session_id=SESSION_ID,
        run_id=RUN_ID,
        state="completed",
    )
    manager.add_runtime_episode_handoff(
        episode_id=EPISODE_ID,
        handoff={
            "handoffId": "handoff-retention-1",
            "handoffRefId": "handoff-retention-1",
            "producerEpisodeId": EPISODE_ID,
            "kind": "subagent_result",
            "status": "ready",
            "compactSummary": "verification evidence delivered",
        },
        session_id=SESSION_ID,
        run_id=RUN_ID,
    )

    ordered_topics = [
        "runtime.episode.queued",
        "runtime.episode.started",
        "runtime.episode.progress",
        "subagent.text.delta",
        "subagent.tool.started",
        "delegation.progress",
        "delegation.child.requested",
        "runtime.episode.waiting",
        "runtime.episode.resumed",
        "handoff.ref.created",
        "runtime.episode.completed",
        "subagent.task.completed",
    ]
    for seq, topic in enumerate(ordered_topics, start=1):
        manager.add_runtime_event(
            {
                "event_id": f"event-retention-{seq}",
                "session_id": SESSION_ID,
                "run_id": RUN_ID,
                "seq": seq,
                "kind": "runtime_event",
                "topic": topic,
                "ts": f"2026-08-17T00:00:{seq:02d}Z",
                "source": {"runtime": "episode_runner"},
                "payload": _event_payload(topic),
            }
        )
    manager.update_run_record(RUN_ID, status="completed")


def _build_snapshot_service(monkeypatch, manager: DatabaseManager):
    monkeypatch.setattr(snapshot_service_module, "db", manager)
    monkeypatch.setattr(
        snapshot_service_module,
        "build_canonical_chat_messages",
        lambda _session_id: [
            {
                "id": "canonical-retention-1",
                "role": "assistant",
                "content": "final supervisor answer",
                "nodes": [],
                "artifacts": [],
            }
        ],
    )
    monkeypatch.setattr(
        snapshot_service_module.workflow_ledger_service,
        "get_session_workflow_view",
        lambda _session_id: {},
    )
    monkeypatch.setattr(
        snapshot_service_module.workflow_projection_service,
        "build",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        snapshot_service_module.session_admission_service,
        "get_lane_view",
        lambda _session_id: {},
    )
    monkeypatch.setattr(
        snapshot_service_module,
        "resolve_authoritative_session_runtime_state",
        lambda **_kwargs: SimpleNamespace(
            run_record=manager.get_run_record(RUN_ID),
            current_run_id=RUN_ID,
            current_run={"id": RUN_ID, "status": "completed"},
            todos={"items": [], "allCompleted": True},
            runtime_status="completed",
        ),
    )
    monkeypatch.setattr(snapshot_service_module, "derive_recovery_class", lambda *_args, **_kwargs: "none")
    monkeypatch.setattr(snapshot_service_module, "build_liveness_view", lambda **_kwargs: {})
    monkeypatch.setattr(snapshot_service_module, "build_processes_snapshot", lambda **_kwargs: [])
    service = snapshot_service_module.SnapshotService()
    monkeypatch.setattr(service, "_session_coordination_messages", lambda _session_id: [])
    return service


def _milestone_rows(events: list[dict]) -> list[tuple]:
    return [
        (
            event.get("event_id"),
            event.get("seq"),
            event.get("topic"),
            event.get("payload"),
        )
        for event in events
        if event.get("topic") in MILESTONE_TOPICS
    ]


def test_completed_run_retention_preserves_collaboration_reload_milestones(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "state.db"
    monkeypatch.setattr(storage_retention_module, "STATE_DB_PATH", state_path)
    manager = DatabaseManager(state_path)
    _seed_collaboration_run(manager)
    projection_service = _build_snapshot_service(monkeypatch, manager)
    monkeypatch.setattr(session_history_contract_module, "build_processes_snapshot", lambda **_kwargs: [])

    before_events = manager.get_runtime_events(SESSION_ID)
    before_milestones = _milestone_rows(before_events)
    before_snapshot = projection_service.build_chat_projection_payload(SESSION_ID)
    before_timeline = project_runtime_timeline_from_events(before_events)
    assert "runtime.episode.progress" in [item.get("topic") for item in before_timeline]
    assert "subagent.text.delta" in [item.get("topic") for item in before_timeline]

    dry_run_actions = StorageRetentionService()._prune_completed_runtime_events(dry_run=True)
    assert dry_run_actions == [
        {
            "action": "prune_completed_runtime_events",
            "rows": len(COMPRESSIBLE_TOPICS),
            "dryRun": True,
        }
    ]
    assert manager.get_runtime_events(SESSION_ID) == before_events

    actions = StorageRetentionService()._prune_completed_runtime_events(dry_run=False)

    assert actions == [
        {
            "action": "prune_completed_runtime_events",
            "rows": len(COMPRESSIBLE_TOPICS),
            "dryRun": False,
        }
    ]
    after_events = manager.get_runtime_events(SESSION_ID)
    assert _milestone_rows(after_events) == before_milestones
    assert [event.get("topic") for event in after_events] == MILESTONE_TOPICS
    assert manager.get_runtime_episode(EPISODE_ID) is not None
    assert manager.list_runtime_episode_handoffs(EPISODE_ID)[0]["payload"]["handoffRefId"] == "handoff-retention-1"

    after_snapshot = projection_service.build_chat_projection_payload(SESSION_ID)
    expected_timeline = [
        item for item in before_timeline if item.get("topic") in MILESTONE_TOPICS
    ]
    assert before_snapshot["runtimeTimeline"] == before_timeline
    assert after_snapshot["runtimeTimeline"] == expected_timeline

    history = build_session_history_detail(
        session_row=manager.get_session(SESSION_ID) or {},
        workflow_view={},
        approvals=[],
        snapshot=after_snapshot.get("snapshot") or {},
        timeline_messages=list((after_snapshot.get("snapshot") or {}).get("messages") or []),
        latest_seq=int(after_snapshot.get("latestSeq") or 0),
        source="retention_reload_test",
        runtime_events=after_events,
        runtime_timeline=after_snapshot["runtimeTimeline"],
        run_record=manager.get_run_record(RUN_ID),
    )
    assert history["runtimeTimeline"] == expected_timeline
    assert [item.get("eventName") for item in history["ledger"]] == MILESTONE_TOPICS
    assert history["messages"][0]["content"] == "final supervisor answer"
