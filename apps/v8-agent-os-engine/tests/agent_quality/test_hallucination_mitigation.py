from __future__ import annotations

import json

from core.native_tools import tool_observation_detail
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_runtime_episode
from core.database import db
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


def test_unknown_episode_executor_fails_recoverably_not_success() -> None:
    episode = build_runtime_episode(
        need={"kind": "agent_quality_unknown", "source": "test", "reason": "hallucination guard"},
        kind="agent_quality_unknown",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["agent_quality_unknown"])
    assert claimed is not None

    import asyncio

    asyncio.run(runner._execute_episode(claimed))
    stored = db.get_runtime_episode(episode["episodeId"])

    assert stored is not None
    assert stored["state"] == "failed"
    assert stored["recoverable"] is True
    assert stored["resultRef"]


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

