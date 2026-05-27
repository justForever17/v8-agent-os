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

    first_parent = db.get_runtime_episode(parent["episodeId"])
    assert first_parent is not None
    asyncio.run(runner._execute_episode(first_parent))
    waiting_parent = db.get_runtime_episode(parent["episodeId"])
    assert waiting_parent is not None
    assert waiting_parent["state"] == "waiting_child"

    children = db.list_runtime_episodes(parent_episode_id=parent["episodeId"], limit=10)
    assert len(children) == 1
    assert children[0]["kind"] == "creative_media"

    child = db.get_runtime_episode(children[0]["episodeId"])
    assert child is not None
    asyncio.run(runner._execute_episode(child))

    resumed_parent = db.get_runtime_episode(parent["episodeId"])
    assert resumed_parent is not None
    assert resumed_parent["state"] == "queued"
    assert resumed_parent["resumeToken"]["resumedFrom"] == "child_handoffs"
    assert resumed_parent["resumeToken"]["childHandoffs"][0]["kind"] == "asset_bundle"
    assert resumed_parent["resumeToken"]["handoffBundle"][0]["compactSummary"] == "asset ready"

    second_parent = db.get_runtime_episode(parent["episodeId"])
    assert second_parent is not None
    asyncio.run(runner._execute_episode(second_parent))
    completed_parent = db.get_runtime_episode(parent["episodeId"])
    assert completed_parent is not None
    assert completed_parent["state"] == "completed"


def test_engineering_resume_loads_child_handoff_payload_before_delegating_again():
    child = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "child worker finished"},
        kind="delegation",
        state="completed",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(child, enqueue=False)
    db.add_runtime_episode_handoff(
        episode_id=child["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=child["episodeId"],
            kind="delegation",
            compact_summary="child worker produced validation proof",
            status="ready",
            confidence="medium",
            extra={"delegationRefs": ["delegation:test"]},
        ),
    )
    parent = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "resume after child"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "resumeToken": {
                "resumedFrom": "child_handoffs",
                "childEpisodeIds": [child["episodeId"]],
            },
            "inputs": {
                "workerBriefs": [
                    {
                        "title": "Should not dispatch again",
                        "goal": "This task should be ignored because child handoff already exists.",
                    }
                ]
            },
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(parent))

    assert handoff["status"] == "ready"
    assert handoff["engineeringState"] == "handoff_ready"
    assert handoff["childHandoffs"][0]["kind"] == "subagent_result_bundle"
    assert "child worker produced validation proof" in handoff["compactSummary"]


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


def test_delegation_episode_executes_local_parallel_delegate_send(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "local subagent task"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workerBriefs": [
                    {
                        "id": "brief-local",
                        "title": "Implement isolated patch",
                        "goal": "Implement isolated patch",
                        "agentId": "engineering_worker",
                    }
                ],
                "targetCount": 1,
            }
        },
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    from core.native_tools import delegation_broker
    from langgraph.types import Command, Send

    def _fake_dispatch(**kwargs):
        assert kwargs["mode"] == "dispatch"
        return Command(
            goto=[
                Send(
                    "parallel_delegate_task",
                    {
                        "parallel_branch": {
                            "agentId": "engineering_worker",
                            "agentName": "Engineering Worker",
                            "delegationId": "delegation-local-1",
                            "invocationId": "invoke-local-1",
                            "taskBriefId": "brief-local",
                            "reason": "Implement isolated patch",
                        },
                        "messages": [],
                        "todos": [],
                    },
                )
            ],
            update={},
        )

    async def _fake_run_parallel_agent_branch(state, agent_data):
        branch = state["parallel_branch"]
        return [], [], {
            "invocationId": branch["invocationId"],
            "taskBriefId": branch["taskBriefId"],
            "agentId": branch["agentId"],
            "agentName": branch["agentName"],
            "delegationId": branch["delegationId"],
            "targetLabel": branch["agentName"],
            "status": "ok",
            "compactTranscript": "patch proposal ready",
        }, []

    monkeypatch.setattr(delegation_broker, "func", _fake_dispatch)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda self: {"engineering_worker": {"node_func": object()}})
    import graph.parallel_support as parallel_support

    monkeypatch.setattr(parallel_support, "_run_parallel_agent_branch", _fake_run_parallel_agent_branch)

    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["delegation"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    payload = db.list_runtime_episode_handoffs(episode["episodeId"])[-1]["payload"]
    assert payload["kind"] == "subagent_result_bundle"
    assert payload["delegationRefs"] == ["delegation-local-1"]
    assert payload["results"][0]["compactTranscript"] == "patch proposal ready"


def test_delegation_episode_promotes_child_delegate_send_to_child_episode(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "parent subagent task"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workerBriefs": [
                    {
                        "id": "brief-parent",
                        "title": "Review evidence",
                        "goal": "Review evidence",
                        "agentId": "review_worker",
                    }
                ],
                "targetCount": 1,
            }
        },
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    from core.native_tools import delegation_broker
    from langgraph.types import Command, Send

    def _fake_dispatch(**kwargs):
        return Command(
            goto=[
                Send(
                    "parallel_delegate_task",
                    {
                        "parallel_branch": {
                            "agentId": "review_worker",
                            "agentName": "Review Worker",
                            "delegationId": "delegation-parent-1",
                            "invocationId": "invoke-parent-1",
                            "taskBriefId": "brief-parent",
                            "reason": "Review evidence",
                        },
                        "messages": [],
                        "todos": [],
                    },
                )
            ],
            update={},
        )

    async def _fake_run_parallel_agent_branch(state, agent_data):
        branch = state["parallel_branch"]
        child_request = {
            "requestId": "child-request-1",
            "sourceDelegationId": branch["delegationId"],
            "sourceInvocationId": branch["invocationId"],
            "childInvocationId": "invoke-child-1",
            "childTaskBriefId": "brief-child",
            "childTaskGoal": "Run child verification",
            "childAgentId": "verification_worker",
            "childAgentName": "Verification Worker",
            "childDepth": 1,
            "send": {
                "arg": {
                    "parallel_branch": {
                        "runtimeAccess": ["delegation.recursive"],
                        "allowChildDelegation": False,
                    }
                }
            },
        }
        return [], [], {
            "invocationId": branch["invocationId"],
            "taskBriefId": branch["taskBriefId"],
            "agentId": branch["agentId"],
            "agentName": branch["agentName"],
            "delegationId": branch["delegationId"],
            "targetLabel": branch["agentName"],
            "status": "waiting_child_delegation",
            "error": "delegation_child_requested",
            "childDelegationCount": 1,
        }, [child_request]

    monkeypatch.setattr(delegation_broker, "func", _fake_dispatch)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda self: {"review_worker": {"node_func": object()}})
    import graph.parallel_support as parallel_support

    monkeypatch.setattr(parallel_support, "_run_parallel_agent_branch", _fake_run_parallel_agent_branch)

    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["delegation"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "waiting_child"
    child_episodes = db.list_runtime_episodes(parent_episode_id=episode["episodeId"], limit=10)
    assert len(child_episodes) == 1
    assert child_episodes[0]["kind"] == "delegation"
    payload = db.list_runtime_episode_handoffs(episode["episodeId"])[-1]["payload"]
    assert payload["status"] == "waiting"
    assert payload["childEpisodeIds"] == [child_episodes[0]["episodeId"]]


def test_delegation_episode_promotes_malformed_child_delegate_signal(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "parent subagent task"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workerBriefs": [
                    {
                        "id": "brief-parent",
                        "title": "Implement feature",
                        "goal": "Implement feature",
                        "agentId": "implementation_worker",
                    }
                ],
                "targetCount": 1,
            }
        },
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    from core.native_tools import delegation_broker
    from langgraph.types import Command, Send

    def _fake_dispatch(**kwargs):
        return Command(
            goto=[
                Send(
                    "parallel_delegate_task",
                    {
                        "parallel_branch": {
                            "agentId": "implementation_worker",
                            "agentName": "Implementation Worker",
                            "delegationId": "delegation-parent-malformed",
                            "invocationId": "invoke-parent-malformed",
                            "taskBriefId": "brief-parent",
                            "reason": "Implement feature",
                        },
                        "messages": [],
                        "todos": [],
                    },
                )
            ],
            update={},
        )

    async def _fake_run_parallel_agent_branch(state, agent_data):
        branch = state["parallel_branch"]
        return [], [], {
            "invocationId": branch["invocationId"],
            "taskBriefId": branch["taskBriefId"],
            "agentId": branch["agentId"],
            "agentName": branch["agentName"],
            "delegationId": branch["delegationId"],
            "targetLabel": branch["agentName"],
            "status": "blocked",
            "error": "delegation_child_requested",
            "nestedDispatchCount": 1,
            "childDelegationCount": 0,
        }, []

    monkeypatch.setattr(delegation_broker, "func", _fake_dispatch)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda self: {"implementation_worker": {"node_func": object()}})
    import graph.parallel_support as parallel_support

    monkeypatch.setattr(parallel_support, "_run_parallel_agent_branch", _fake_run_parallel_agent_branch)

    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=["delegation"])
    assert claimed is not None
    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "waiting_child"
    child_episodes = db.list_runtime_episodes(parent_episode_id=episode["episodeId"], limit=10)
    assert len(child_episodes) == 1
    payload = db.list_runtime_episode_handoffs(episode["episodeId"])[-1]["payload"]
    assert payload["status"] == "waiting"
    assert payload["childEpisodeIds"] == [child_episodes[0]["episodeId"]]
    assert payload["results"][-1]["status"] == "waiting_child_delegation"


def test_parallel_branch_extracts_child_delegation_from_command_update_list():
    from graph.parallel_support import _run_parallel_agent_branch
    from langgraph.types import Command

    parent_state = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "review_worker",
            "agentName": "Review Worker",
            "delegationId": "delegation-parent-1",
            "invocationId": "invoke-parent-1",
            "taskBriefId": "brief-parent",
            "reason": "Review evidence",
        },
    }
    child_arg = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "verification_worker",
            "agentName": "Verification Worker",
            "delegationId": "delegation-child-1",
            "invocationId": "invoke-child-1",
            "taskBriefId": "brief-child",
            "reason": "Run child verification",
            "delegationDepth": 2,
        },
    }

    def _node_func(_state):
        return [
            Command(
                goto="supervisor",
                update={
                    "pending_child_delegations": [
                        {
                            "requestId": "child-request-from-update",
                            "sourceDelegationId": "delegation-parent-1",
                            "sourceInvocationId": "invoke-parent-1",
                            "send": {"node": "parallel_delegate_task", "arg": child_arg},
                        }
                    ]
                },
            )
        ]

    _delta_messages, _delta_todos, summary, child_requests = asyncio.run(
        _run_parallel_agent_branch(parent_state, {"node_func": _node_func, "tool_mode": "test"})
    )

    assert summary["status"] == "waiting_child_delegation"
    assert summary["childDelegationCount"] == 1
    assert child_requests[0]["requestId"] == "child-request-from-update"
    assert child_requests[0]["childAgentId"] == "verification_worker"
    assert child_requests[0]["send"]["arg"]["parallel_branch"]["invocationId"] == "invoke-child-1"
