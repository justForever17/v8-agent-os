from __future__ import annotations

import asyncio

from core.database import db
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_handoff_ref, build_runtime_episode
from tests.scripts.run_agent_quality_live_audit import LiveCaseResult, _route_evidence_for_expected_tool


def test_research_to_engineering_to_delegation_handoff_chain(monkeypatch) -> None:
    research = build_runtime_episode(
        need={"kind": "research", "source": "planner", "reason": "collect source-backed facts"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    engineering = build_runtime_episode(
        need={
            "kind": "engineering",
            "source": "planner",
            "reason": "consume research and implement",
            "inputs": {
                "handoffRefs": [research["episodeId"]],
                "workerBriefs": [
                    {
                        "title": "Implement routed work",
                        "goal": "Use research evidence and return proof.",
                        "runtimeAccess": ["memory.read"],
                    }
                ],
            },
        },
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(research, enqueue=True, priority=999)
    db.upsert_runtime_episode_record(engineering, enqueue=True, priority=999)

    async def _fake_research(self, claimed):
        return build_handoff_ref(
            producer_episode_id=claimed["episodeId"],
            kind="research",
            compact_summary="research evidence ready",
            status="ready",
            confidence="high",
            extra={"refs": ["source:https://docs.example.com"]},
        )

    async def _fake_delegation(self, episode):
        inputs = episode.get("inputs") or {}
        assert inputs.get("workerBriefs"), "engineering must pass concrete worker briefs to delegation"
        return build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="delegation",
            compact_summary="delegated worker completed patch proposal",
            status="ready",
            confidence="medium",
            extra={"refs": ["subagent_result:worker-1"], "taskConfirmed": True},
        )

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_research", _fake_research)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _fake_delegation)

    runner = RuntimeEpisodeRunner()
    claimed_research = db.get_runtime_episode(research["episodeId"])
    assert claimed_research is not None
    asyncio.run(runner._execute_episode(claimed_research))
    research_handoff = db.list_runtime_episode_handoffs(research["episodeId"])[-1]["payload"]
    assert research_handoff["kind"] == "research_evidence_bundle"

    claimed_engineering = db.get_runtime_episode(engineering["episodeId"])
    assert claimed_engineering is not None
    asyncio.run(runner._execute_episode(claimed_engineering))
    engineering_handoff = db.list_runtime_episode_handoffs(engineering["episodeId"])[-1]["payload"]

    assert engineering_handoff["kind"] == "engineering_patch_bundle"
    assert engineering_handoff["engineeringState"] == "execution_started"
    assert engineering_handoff["delegationHandoff"]["taskConfirmed"] is True
    assert "subagent_result:worker-1" in engineering_handoff["delegationHandoff"]["refs"]


def test_child_capability_need_promotes_and_parent_resumes(monkeypatch) -> None:
    parent = build_runtime_episode(
        need={
            "kind": "engineering",
            "source": "planner",
            "reason": "needs child creative episode",
            "inputs": {
                "capabilityNeeds": [
                    {"kind": "creative_media", "reason": "generate icon", "inputs": {"prompt": "minimal app icon"}}
                ]
            },
        },
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(parent, enqueue=True, priority=999)

    async def _fake_creative(self, claimed):
        return build_handoff_ref(
            producer_episode_id=claimed["episodeId"],
            kind="creative_media",
            compact_summary="asset ready",
            status="ready",
            confidence="medium",
            extra={"refs": ["asset:icon"]},
        )

    async def _fake_engineering(self, claimed):
        return build_handoff_ref(
            producer_episode_id=claimed["episodeId"],
            kind="engineering",
            compact_summary="parent resumed after child handoff",
            status="ready",
            confidence="medium",
            extra={"refs": ["patch:1"]},
        )

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_creative_media", _fake_creative)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_engineering", _fake_engineering)
    runner = RuntimeEpisodeRunner()

    first_parent = db.get_runtime_episode(parent["episodeId"])
    assert first_parent is not None
    asyncio.run(runner._execute_episode(first_parent))
    assert db.get_runtime_episode(parent["episodeId"])["state"] == "waiting_child"

    children = db.list_runtime_episodes(parent_episode_id=parent["episodeId"], limit=10)
    assert len(children) == 1
    assert children[0]["kind"] == "creative_media"

    child = db.get_runtime_episode(children[0]["episodeId"])
    assert child is not None
    asyncio.run(runner._execute_episode(child))
    assert db.get_runtime_episode(parent["episodeId"])["state"] == "queued"

    resumed = db.get_runtime_episode(parent["episodeId"])
    assert resumed is not None
    asyncio.run(runner._execute_episode(resumed))
    assert db.get_runtime_episode(parent["episodeId"])["state"] == "completed"


def test_claimed_without_dispatch_does_not_satisfy_delegation_expected_tool() -> None:
    case = LiveCaseResult(
        case_id="delegation-claimed-without-dispatch",
        matrix="multi_agent",
        prompt="演示一次调研 + 工程 + 子 agent + child delegation 的主链调度。",
        expected_tools=["delegation_broker"],
        forbidden_tools=[],
        status="live",
        actual_tools=[],
        observed_topics=["subagent.delegation.claimed_without_dispatch"],
        key_events=[],
    )

    matched, reason = _route_evidence_for_expected_tool(case, "delegation_broker")

    assert matched is False
    assert reason == "delegation_claimed_without_confirmed_dispatch"
    assert case.failure_reason == "delegation_claimed_without_confirmed_dispatch"
