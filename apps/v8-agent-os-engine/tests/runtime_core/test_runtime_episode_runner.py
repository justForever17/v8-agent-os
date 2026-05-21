from __future__ import annotations

import asyncio

from core.database import db
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_handoff_ref, build_runtime_episode


def test_runtime_episode_queue_claim_and_unknown_executor_completes_recoverably():
    kind = "test_unknown_episode"
    episode = build_runtime_episode(
        need={"kind": kind, "source": "test", "reason": "exercise queue"},
        kind=kind,
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert claimed is not None
    assert claimed["episodeId"] == episode["episodeId"]
    assert claimed["state"] == "active"

    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "failed"
    assert stored["resultRef"]
    assert stored["recoverable"] is True


def test_runtime_episode_retry_policy_requeues_before_final_failure():
    kind = "test_retry_episode"
    episode = build_runtime_episode(
        need={"kind": kind, "source": "test", "reason": "retry once"},
        kind=kind,
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={"retryPolicy": {"maxAttempts": 2, "delaySeconds": 0}},
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    runner = RuntimeEpisodeRunner()
    first = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert first is not None
    asyncio.run(runner._execute_episode(first))

    after_first = db.get_runtime_episode(episode["episodeId"])
    assert after_first is not None
    assert after_first["state"] == "queued"

    second = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert second is not None
    asyncio.run(runner._execute_episode(second))

    final = db.get_runtime_episode(episode["episodeId"])
    assert final is not None
    assert final["state"] == "failed"
    assert final["attempt_count"] == 2


def test_runtime_episode_typed_handoff_artifact_for_research(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "research", "source": "test", "reason": "typed handoff"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    async def _fake_research(self, claimed):
        return build_handoff_ref(
            producer_episode_id=claimed["episodeId"],
            kind="research",
            compact_summary="evidence ready",
            status="ready",
            confidence="high",
            extra={"refs": ["source:1"]},
        )

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_research", _fake_research)
    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["research"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    handoffs = db.list_runtime_episode_handoffs(episode["episodeId"])
    assert handoffs
    assert handoffs[-1]["payload"]["kind"] == "research_evidence_bundle"
    assert handoffs[-1]["payload"]["artifactId"].startswith("artifact_")


def test_runtime_episode_rpa_prepare_draft_creates_trace_bundle(monkeypatch):
    episode = build_runtime_episode(
        need={
            "kind": "rpa",
            "source": "test",
            "reason": "prepare rpa draft",
            "inputs": {"draftId": "draft_canvas", "variables": {"name": "Jack"}},
        },
        kind="rpa",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    from runtimes.rpa.runtime import rpa_runtime

    def _fake_prepare_draft_run(**kwargs):
        assert kwargs["script_id"] == "draft_canvas"
        assert kwargs["variables"] == {"name": "Jack"}
        return {
            "script": {"id": "draft_canvas"},
            "export": {"path": "E:/tmp/draft_canvas.robot", "dryRunPassed": True},
            "command": ["python", "-m", "robot", "E:/tmp/draft_canvas.robot"],
        }

    monkeypatch.setattr(rpa_runtime, "prepare_draft_run", _fake_prepare_draft_run)
    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["rpa"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    handoffs = db.list_runtime_episode_handoffs(episode["episodeId"])
    payload = handoffs[-1]["payload"]
    assert payload["kind"] == "rpa_trace_bundle"
    assert payload["robotRefs"] == ["E:/tmp/draft_canvas.robot"]
    assert payload["prepared"]["scriptId"] == "draft_canvas"
    assert payload["verification"]["dryRunPassed"] is True


def test_runtime_episode_rpa_execute_draft_uses_non_chat_run(monkeypatch):
    episode = build_runtime_episode(
        need={
            "kind": "rpa",
            "source": "test",
            "reason": "execute rpa draft",
            "inputs": {"draftId": "draft_canvas", "mode": "execute", "variables": {"target": "demo"}},
        },
        kind="rpa",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    from runtimes.rpa.runtime import rpa_runtime

    def _fake_run_draft(**kwargs):
        assert kwargs["script_id"] == "draft_canvas"
        assert kwargs["trigger_source"] == "runtime_episode_runner"
        assert kwargs["non_chat_run"] is True
        assert kwargs["session_id"] is None
        assert kwargs["run_id"] is None
        return {
            "status": "completed",
            "runId": "run-rpa",
            "sessionId": "rpa:draft:draft_canvas",
            "script": {"id": "draft_canvas"},
            "export": {"path": "E:/tmp/draft_canvas.robot", "dryRunPassed": True},
            "command": ["python", "-m", "robot", "E:/tmp/draft_canvas.robot"],
            "outcomeFamily": "success",
        }

    monkeypatch.setattr(rpa_runtime, "run_draft", _fake_run_draft)
    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["rpa"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    payload = db.list_runtime_episode_handoffs(episode["episodeId"])[-1]["payload"]
    assert payload["kind"] == "rpa_trace_bundle"
    assert payload["runRefs"] == ["rpa_run:run-rpa"]
    assert "rpa_session:rpa:draft:draft_canvas" in payload["refs"]
    assert payload["verification"]["executionStatus"] == "completed"


def test_child_capability_need_promotes_to_episode_and_resumes_parent(monkeypatch):
    parent = build_runtime_episode(
        need={
            "kind": "engineering",
            "source": "test",
            "reason": "needs asset",
            "inputs": {
                "capabilityNeeds": [
                    {
                        "kind": "creative_media",
                        "reason": "generate project icon",
                        "inputs": {"prompt": "minimal icon"},
                    }
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
            compact_summary="engineering resumed with child handoff",
            status="ready",
            confidence="medium",
            extra={"refs": ["patch:1"]},
        )

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_creative_media", _fake_creative)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_engineering", _fake_engineering)
    runner = RuntimeEpisodeRunner()

    first_parent = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["engineering"])
    assert first_parent is not None
    asyncio.run(runner._execute_episode(first_parent))
    waiting_parent = db.get_runtime_episode(parent["episodeId"])
    assert waiting_parent is not None
    assert waiting_parent["state"] == "waiting_child"

    children = db.list_runtime_episodes(parent_episode_id=parent["episodeId"], limit=10)
    assert len(children) == 1
    assert children[0]["kind"] == "creative_media"

    child = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["creative_media"])
    assert child is not None
    asyncio.run(runner._execute_episode(child))

    resumed_parent = db.get_runtime_episode(parent["episodeId"])
    assert resumed_parent is not None
    assert resumed_parent["state"] == "queued"
    assert resumed_parent["resumeToken"]["resumedFrom"] == "child_handoffs"

    second_parent = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["engineering"])
    assert second_parent is not None
    asyncio.run(runner._execute_episode(second_parent))
    completed_parent = db.get_runtime_episode(parent["episodeId"])
    assert completed_parent is not None
    assert completed_parent["state"] == "completed"


def test_network_peer_target_completes_with_remote_handoff(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "remote peer task"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "targetKind": "network_peer",
            "targetId": "peer-alpha",
            "inputs": {"task": "run remote verification"},
        },
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    async def _fake_delegate_task(**kwargs):
        assert kwargs["peer_id"] == "peer-alpha"
        assert kwargs["task"] == "run remote verification"
        return {"result": "remote verification passed", "delegationId": "delegation-1", "outerRunId": "run-remote"}

    from runtimes.network_supervisor.service import network_supervisor_service

    monkeypatch.setattr(network_supervisor_service, "delegate_task", _fake_delegate_task)
    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["delegation"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    handoffs = db.list_runtime_episode_handoffs(episode["episodeId"])
    assert handoffs[-1]["payload"]["refs"] == ["network_peer:peer-alpha:delegation-1"]


def test_external_worker_target_dispatches_through_delegation_broker(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "external worker task"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "targetKind": "external_worker",
            "targetId": "worker-codex",
            "inputs": {"task": "implement patch"},
        },
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    class _Command:
        update = {
            "parallel_results": [
                {
                    "status": "completed",
                    "delegationId": "external-1",
                    "targetLabel": "Codex Worker",
                    "workerResult": "patch ready",
                    "traceRef": "external:trace:1",
                }
            ]
        }

    def _fake_dispatch(**kwargs):
        assert kwargs["mode"] == "dispatch"
        assert kwargs["target_count"] == 1
        assert kwargs["tasks"][0]["preferredWorkerType"] == "worker-codex"
        return _Command()

    from core.native_tools import delegation_broker

    monkeypatch.setattr(delegation_broker, "func", _fake_dispatch)
    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["delegation"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    handoffs = db.list_runtime_episode_handoffs(episode["episodeId"])
    assert handoffs[-1]["payload"]["refs"] == ["external:trace:1"]
