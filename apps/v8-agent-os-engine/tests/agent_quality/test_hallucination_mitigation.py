from __future__ import annotations

import json

from core.native_tools import tool_observation_detail
from core.runtime_episodes import build_runtime_episode
from core.database import DatabaseManager
from core.runtime_projection import project_runtime_timeline_from_events


def test_missing_delegation_result_projects_unconfirmed_instead_of_success() -> None:
    timeline = project_runtime_timeline_from_events(
        [
            {
                "event_id": "evt_delegation_start",
                "run_id": "run_quality_hallucination",
                "seq": 1,
                "topic": "tool.started",
                "payload": {"tool": {"toolCallId": "call_delegation", "toolName": "delegation_broker", "args": {"mode": "dispatch"}}},
                "event_ts": "2026-05-27T00:00:01Z",
                "source": {},
            },
            {
                "event_id": "evt_run_end",
                "run_id": "run_quality_hallucination",
                "seq": 2,
                "topic": "run.state.changed",
                "payload": {"to_status": "completed"},
                "event_ts": "2026-05-27T00:00:02Z",
                "source": {},
            },
        ]
    )
    missing = [item for item in timeline if item.get("metadata", {}).get("missingResult")]

    assert len(missing) == 1
    assert missing[0]["runtimeId"] == "subagent_swarm"
    assert missing[0]["status"] == "missing_result"
    assert "未确认实际派发" in missing[0]["summary"]


def test_unknown_episode_is_archived_without_execution_or_row_rewrite(tmp_path) -> None:
    manager = DatabaseManager(tmp_path / "runtime-compatibility.db")
    episode = build_runtime_episode(
        need={"kind": "agent_quality_unknown", "source": "test", "reason": "hallucination guard"},
        kind="agent_quality_unknown",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    manager.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    with manager.get_connection() as conn:
        before = dict(conn.execute("SELECT * FROM runtime_episodes WHERE id = ?", (episode["episodeId"],)).fetchone())

    claimed = manager.claim_runtime_episode(
        worker_id="compatibility-test",
        lease_seconds=30,
    )
    stored = manager.get_runtime_episode(episode["episodeId"])
    with manager.get_connection() as conn:
        after = dict(conn.execute("SELECT * FROM runtime_episodes WHERE id = ?", (episode["episodeId"],)).fetchone())

    assert claimed is None
    assert stored is not None
    assert stored["state"] == "queued"
    assert stored["displayState"] == "archived"
    assert stored["persistedState"] == "queued"
    assert stored["executionSupported"] is False
    assert stored["compatibilityStatus"] == "unsupported_archived_runtime"
    assert manager.list_runtime_episodes(active_only=True) == []
    assert manager.list_runtime_episode_queue(active_only=True) == []
    assert before == after


def test_research_detail_surface_prefers_final_architect_pack_not_raw_json(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    payload = {
        "kind": "research_evidence_bundle",
        "question": "How should Agent Quality Matrix report research?",
        "finalExperiencePack": {
            "architectAgentId": "web-research-architect",
            "answer": "Web Research Architect final result:\n- Keep only source-backed findings.\n- Do not expose raw search snippets as the final answer.",
            "keyFindings": [
                {
                    "claim": "Final research packs must preserve source URLs and remove unrelated snippets.",
                    "sourceTitle": "Research Runtime Contract",
                    "sourceUrl": "https://docs.example.com/research-runtime",
                }
            ],
            "sourceUrls": [
                {
                    "title": "Research Runtime Contract",
                    "url": "https://docs.example.com/research-runtime",
                    "host": "docs.example.com",
                }
            ],
            "confidence": "high",
        },
        "sourceMatrix": [{"title": "raw noisy entry", "url": "https://noise.example.com"}],
    }
    temp_db.add_tool_observation_record(
        {
            "id": "obs-quality-research",
            "raw_ref": "toolobs://obs-quality-research",
            "tool_name": "research_broker",
            "tool_call_id": "call-research",
            "runtime_kind": "research",
            "surface": "tool_node",
            "raw_chars": 1024,
            "visible_chars": 200,
            "raw_sha256": "sha",
            "raw_body": json.dumps(payload, ensure_ascii=False),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = tool_observation_detail.invoke({"raw_ref": "toolobs://obs-quality-research", "max_chars": 4000})

    assert "Research result pack" in result
    assert "Web Research Architect final result" in result
    assert "https://docs.example.com/research-runtime" in result
    assert '"sourceMatrix"' not in result
    assert "raw noisy entry" not in result
