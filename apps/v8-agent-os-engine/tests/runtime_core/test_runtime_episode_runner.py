from __future__ import annotations

import asyncio
from types import SimpleNamespace

from erc.runtime_context import bind_runtime_context
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


def test_runtime_episode_build_inherits_runtime_context_binding():
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.id AS session_id, r.id AS run_id
            FROM sessions s
            JOIN run_records r ON r.session_id = s.id
            ORDER BY r.started_at DESC
            LIMIT 1
            """
        )
        binding_row = cur.fetchone()
    assert binding_row is not None
    session_id = binding_row["session_id"]
    run_id = binding_row["run_id"]
    with bind_runtime_context(session_id=session_id, run_id=run_id, rootRunId=run_id):
        episode = build_runtime_episode(
            need={"kind": "engineering", "source": "test", "reason": "binding"},
            kind="engineering",
            state="queued",
            continuation_target="runtime_episode_runner",
        )
    assert episode["sessionId"] == session_id
    assert episode["session_id"] == session_id
    assert episode["runId"] == run_id
    assert episode["run_id"] == run_id
    assert episode["rootRunId"] == run_id
    persisted = db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    assert persisted["session_id"] == session_id
    assert persisted["run_id"] == run_id
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT session_id, run_id FROM runtime_episode_queue WHERE episode_id = ?", (episode["episodeId"],))
        queue_row = cur.fetchone()
    assert queue_row is not None
    assert queue_row["session_id"] == session_id
    assert queue_row["run_id"] == run_id


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


def test_research_episode_uses_task_route_query_and_runs_full_evidence(monkeypatch):
    calls: list[dict] = []

    def _fake_research_broker(**kwargs):
        calls.append(dict(kwargs))
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"mode={kwargs.get('mode')} query={kwargs.get('query')}",
                        tool_call_id=str(kwargs.get("tool_call_id") or "test"),
                    )
                ]
            }
        )

    import core.native_tools as native_tools

    monkeypatch.setattr(native_tools, "research_broker", SimpleNamespace(func=_fake_research_broker))
    episode = build_runtime_episode(
        need={"kind": "research", "source": "test", "reason": "skill_driven_writing_requires_source_evidence"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Collect real evidence for 三月七.",
                        "routeQuery": "调研《崩坏：星穹铁道》三月七官方设定与剧情台词",
                        "context": {"sourcePolicy": "multi_source_full_read_required"},
                        "requiredCapabilities": ["web_research", "evidence_bundle", "claim_table"],
                    }
                ]
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_research(episode))

    assert calls[0]["mode"] == "search_experience"
    assert calls[1]["mode"] == "run"
    assert "三月七" in calls[0]["query"]
    assert calls[0]["query"] != "skill_driven_writing_requires_source_evidence"
    assert handoff["status"] == "ready"
    assert handoff["runMode"] == "run"


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
                "allowChildDelegation": True,
                "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
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


def test_delegation_episode_fails_when_all_workers_are_child_budget_blocked(monkeypatch):
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
                        "title": "Create reusable skill",
                        "goal": "Create reusable skill",
                        "agentId": "skill-workflow-curator",
                    }
                ],
                "targetCount": 1,
            }
        },
    )

    from core.native_tools import delegation_broker
    from langgraph.types import Command

    def _fake_dispatch(**kwargs):
        assert kwargs["mode"] == "dispatch"
        return Command(
            update={
                "parallel_results": [
                    {
                        "status": "blocked",
                        "delegationId": "delegation-blocked-1",
                        "targetLabel": "Skill Workflow Curator",
                        "error": "child_delegation_not_allowed",
                        "dispatchStatus": "dispatch_missing_child_budget",
                    }
                ]
            }
        )

    monkeypatch.setattr(delegation_broker, "func", _fake_dispatch)

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_delegation(episode))

    assert handoff["status"] == "failed"
    assert handoff["failedDelegationCount"] == 0
    assert handoff["budgetBlockedChildDelegations"][0]["delegationId"] == "delegation-blocked-1"
    assert "child_budget_blocked=1" in handoff["compactSummary"]


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
                            "allowChildDelegation": True,
                            "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
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


def test_failed_grandchild_delegation_unblocks_parent_episode_chain():
    parent = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "parent waiting for delegation"},
        kind="engineering",
        state="waiting_child",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(parent, enqueue=False)
    child = build_runtime_episode(
        need={
            "kind": "delegation",
            "source": "test",
            "reason": "child delegation",
            "parentEpisodeId": parent["episodeId"],
        },
        kind="delegation",
        state="waiting_child",
        parent_episode_id=parent["episodeId"],
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(child, enqueue=False)
    grandchild = build_runtime_episode(
        need={
            "kind": "delegation",
            "source": "test",
            "reason": "grandchild delegation failed",
            "parentEpisodeId": child["episodeId"],
        },
        kind="delegation",
        state="failed",
        parent_episode_id=child["episodeId"],
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(grandchild, enqueue=False)
    db.add_runtime_episode_handoff(
        episode_id=grandchild["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=grandchild["episodeId"],
            kind="delegation",
            compact_summary="grandchild worker failed with recoverable error",
            status="failed",
            confidence="low",
            extra={"errorCode": "subagent_failed"},
        ),
    )

    RuntimeEpisodeRunner()._maybe_resume_parent_episode(grandchild, session_id=None, run_id=None)

    failed_child = db.get_runtime_episode(child["episodeId"])
    failed_parent = db.get_runtime_episode(parent["episodeId"])
    assert failed_child is not None
    assert failed_parent is not None
    assert failed_child["state"] == "failed"
    assert failed_child["error_code"] == "child_episode_failed"
    child_resume = failed_child["metadata"]["resumeToken"]
    assert child_resume["failedChildCount"] == 1
    assert child_resume["childHandoffs"][0]["compactSummary"] == "grandchild worker failed with recoverable error"
    assert failed_parent["state"] == "failed"
    assert failed_parent["error_code"] == "child_episode_failed"


def test_child_delegation_budget_boundary_requeues_parent_without_failure():
    parent = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "parent delegation waiting for child"},
        kind="delegation",
        state="waiting_child",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(parent, enqueue=False)
    child = build_runtime_episode(
        need={
            "kind": "delegation",
            "source": "test",
            "reason": "child hit delegation budget",
            "parentEpisodeId": parent["episodeId"],
        },
        kind="delegation",
        state="failed",
        parent_episode_id=parent["episodeId"],
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(child, enqueue=False)
    db.add_runtime_episode_handoff(
        episode_id=child["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=child["episodeId"],
            kind="delegation",
            compact_summary="child requested deeper delegation but budget stopped it",
            status="ready",
            confidence="medium",
            extra={
                "budgetBlockedChildDelegations": [
                    {
                        "delegationId": "delegation-child-budget",
                        "targetLabel": "Verification Engineer",
                        "error": "child_delegation_not_allowed",
                        "dispatchStatus": "dispatch_missing_child_budget",
                    }
                ],
                "results": [
                    {
                        "delegationId": "delegation-child-budget",
                        "targetLabel": "Verification Engineer",
                        "status": "blocked",
                        "error": "child_delegation_not_allowed",
                        "dispatchStatus": "dispatch_missing_child_budget",
                    }
                ],
            },
        ),
    )

    RuntimeEpisodeRunner()._maybe_resume_parent_episode(child, session_id=None, run_id=None)

    resumed_parent = db.get_runtime_episode(parent["episodeId"])
    assert resumed_parent is not None
    assert resumed_parent["state"] == "queued"
    assert resumed_parent["error_code"] is None
    resume = resumed_parent["resumeToken"]
    assert resume["failedChildCount"] == 0
    assert resume["budgetBoundaryChildCount"] == 1
    assert resume["childHandoffs"][0]["budgetBlockedChildDelegations"][0]["dispatchStatus"] == "dispatch_missing_child_budget"


def test_child_delegation_retargets_research_goal_away_from_source_agent():
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "creative worker asks for research"},
        kind="delegation",
        state="waiting_child",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=False)

    child_ids = RuntimeEpisodeRunner()._enqueue_child_delegation_requests(
        [
            {
                "requestId": "child-research-request",
                "sourceDelegationId": "delegation-parent-creative",
                "sourceInvocationId": "invoke-creative-parent",
                "sourceAgentId": "creative-media-director",
                "sourceAgentName": "Creative Media Director",
                "childInvocationId": "invoke-child-research",
                "childDelegationId": "delegation-child-research",
                "childTaskBriefId": "brief-child-research",
                "childTaskGoal": "为 V8 Agent OS 宣传视频提供调研支持，收集来源、证据和引用。",
                "childAgentId": "creative-media-director",
                "childAgentName": "Creative Media Director",
                "childDepth": 2,
                "send": {
                    "node": "parallel_delegate_task",
                    "arg": {
                        "parallel_branch": {
                            "agentId": "creative-media-director",
                            "agentName": "Creative Media Director",
                            "reason": "为 V8 Agent OS 宣传视频提供调研支持，收集来源、证据和引用。",
                            "runtimeAccess": ["delegation.recursive"],
                            "delegationDepth": 2,
                        }
                    },
                },
            }
        ],
        episode=episode,
    )

    assert len(child_ids) == 1
    child = db.get_runtime_episode(child_ids[0])
    assert child is not None
    brief = child["inputs"]["workerBriefs"][0]
    assert brief["agentId"] == "web-research-architect"
    assert brief["agentName"] == "Web Research Architect"
    assert brief["targetRepairReason"] == "research_goal"
    assert brief["originalAgentId"] == "creative-media-director"
    assert child["metadata"]["targetRepairReason"] == "research_goal"


def test_child_delegation_retargets_runtime_verification_away_from_skill_curator():
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "verification child target repair"},
        kind="delegation",
        state="waiting_child",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=False)

    child_ids = RuntimeEpisodeRunner()._enqueue_child_delegation_requests(
        [
            {
                "requestId": "child-verification-request",
                "sourceDelegationId": "delegation-parent-verification",
                "sourceInvocationId": "invoke-verification-parent",
                "sourceAgentId": "verification-engineer",
                "sourceAgentName": "Verification Engineer",
                "childInvocationId": "invoke-child-verification",
                "childDelegationId": "delegation-child-verification",
                "childTaskBriefId": "brief-child-verification",
                "childTaskGoal": "Independent verification of V8 Agent OS runtime handoff behavior and child delegation recovery.",
                "childAgentId": "skill-workflow-curator",
                "childAgentName": "Skill Workflow Curator",
                "childDepth": 2,
                "send": {
                    "node": "parallel_delegate_task",
                    "arg": {
                        "parallel_branch": {
                            "agentId": "skill-workflow-curator",
                            "agentName": "Skill Workflow Curator",
                            "reason": "Independent verification of V8 Agent OS runtime handoff behavior and child delegation recovery.",
                            "runtimeAccess": ["delegation.recursive"],
                            "delegationDepth": 2,
                        }
                    },
                },
            }
        ],
        episode=episode,
    )

    assert len(child_ids) == 1
    child = db.get_runtime_episode(child_ids[0])
    assert child is not None
    brief = child["inputs"]["workerBriefs"][0]
    assert brief["agentId"] == "verification-engineer"
    assert brief["agentName"] == "Verification Engineer"
    assert brief["targetRepairReason"] == "verification_goal"
    assert brief["originalAgentId"] == "skill-workflow-curator"


def test_completed_child_delegation_requeues_parent_with_handoff_bundle():
    parent = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "parent waiting for child handoff"},
        kind="engineering",
        state="waiting_child",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(parent, enqueue=False)
    child = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "child completed", "parentEpisodeId": parent["episodeId"]},
        kind="delegation",
        state="completed",
        parent_episode_id=parent["episodeId"],
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(child, enqueue=False)
    db.add_runtime_episode_handoff(
        episode_id=child["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=child["episodeId"],
            kind="delegation",
            compact_summary="child produced implementation proof",
            status="ready",
            confidence="medium",
        ),
    )

    RuntimeEpisodeRunner()._maybe_resume_parent_episode(child, session_id=None, run_id=None)

    resumed_parent = db.get_runtime_episode(parent["episodeId"])
    assert resumed_parent is not None
    assert resumed_parent["state"] == "queued"
    assert resumed_parent["resumeToken"]["resumedFrom"] == "child_handoffs"
    assert resumed_parent["resumeToken"]["childHandoffs"][0]["compactSummary"] == "child produced implementation proof"


def test_delegation_resume_merges_child_handoffs_without_redispatching(monkeypatch):
    child_handoff = build_handoff_ref(
        producer_episode_id="child-delegation-episode",
        kind="delegation",
        compact_summary="child delegation produced independent verification proof",
        status="ready",
        confidence="medium",
        extra={"results": [{"delegationId": "delegation-child", "status": "ok"}]},
    )
    parent = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "parent delegation resume"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workerBriefs": [{"id": "task-parent", "goal": "should not be dispatched again"}],
                "targetCount": 1,
            },
            "resumeToken": {
                "resumedFrom": "child_handoffs",
                "childEpisodeIds": ["child-delegation-episode"],
                "childHandoffs": [child_handoff],
                "handoffBundle": [child_handoff],
            },
        },
    )

    from core.native_tools import delegation_broker

    def _should_not_dispatch(**_kwargs):
        raise AssertionError("delegation_broker should not be called when child handoffs are already available")

    monkeypatch.setattr(delegation_broker, "func", _should_not_dispatch)

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_delegation(parent))

    assert handoff["status"] == "ready"
    assert handoff["delegationState"] == "handoff_ready"
    assert handoff["childHandoffs"][0]["compactSummary"] == "child delegation produced independent verification proof"
    assert "handoff_ready" in handoff["compactSummary"]


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
                "allowChildDelegation": True,
                "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
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
                            "allowChildDelegation": True,
                            "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
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
            "allowChildDelegation": True,
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


def test_parallel_branch_blocks_child_delegation_without_explicit_budget():
    from graph.parallel_support import _run_parallel_agent_branch
    from langgraph.types import Send

    parent_state = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "review_worker",
            "agentName": "Review Worker",
            "delegationId": "delegation-parent-no-child",
            "invocationId": "invoke-parent-no-child",
            "taskBriefId": "brief-parent",
            "reason": "Review evidence",
            "allowChildDelegation": False,
        },
    }
    child_arg = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "verification_worker",
            "agentName": "Verification Worker",
            "delegationId": "delegation-child-blocked",
            "invocationId": "invoke-child-blocked",
            "taskBriefId": "brief-child",
            "reason": "Run child verification",
            "delegationDepth": 2,
        },
    }

    def _node_func(_state):
        return [Send("parallel_delegate_task", child_arg)]

    _delta_messages, _delta_todos, summary, child_requests = asyncio.run(
        _run_parallel_agent_branch(parent_state, {"node_func": _node_func, "tool_mode": "test"})
    )

    assert child_requests == []
    assert summary["status"] == "blocked"
    assert summary["error"] == "child_delegation_not_allowed"
    assert summary["blockedChildDelegationCount"] == 1


def test_parallel_branch_fails_skill_artifact_acceptance_when_skill_md_missing(tmp_path):
    from graph.parallel_support import _run_parallel_agent_branch
    from langgraph.types import Command

    target_dir = tmp_path / ".agents" / "skills" / "sanyueqi-perspective"
    (target_dir / "references" / "research").mkdir(parents=True)
    (target_dir / "references" / "research" / "01-writings.md").write_text("stub\n", encoding="utf-8")

    state = {
        "messages": [],
        "todos": [],
        "workspace_path": str(tmp_path),
        "parallel_branch": {
            "agentId": "writer",
            "agentName": "Writer",
            "delegationId": "delegation-artifact-1",
            "invocationId": "invoke-artifact-1",
            "taskBriefId": "brief-artifact",
            "reason": f"Use huashu-nuwa to write {target_dir}\\SKILL.md and references/research/01-writings.md.",
            "taskBrief": {
                "goal": f"Write reusable skill into {target_dir}",
                "context": {"skillName": "huashu-nuwa"},
                "acceptanceContract": "Required files: SKILL.md and references/research/01-writings.md through 06-timeline.md.",
            },
        },
    }

    def _node_func(_state):
        return Command(goto="supervisor", update={})

    _delta_messages, _delta_todos, summary, _child_requests = asyncio.run(
        _run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"})
    )

    assert summary["status"] == "failed"
    assert summary["error"] == "artifact_acceptance_failed"
    assert any(path.endswith("SKILL.md") for path in summary["missingArtifacts"])


def test_runtime_runner_validates_skill_artifact_without_integration_error(tmp_path):
    from core.runtime_episode_runner import RuntimeEpisodeRunner
    from runtimes.extensions.skills.artifact_validator import SkillArtifactValidator

    target_dir = tmp_path / ".agents" / "skills" / "sanyueqi-perspective"
    research_dir = target_dir / "references" / "research"
    research_dir.mkdir(parents=True)
    rich_skill_body = (
        """---
name: sanyueqi-perspective
description: 用三月七视角分析问题和表达建议。
---

# 三月七视角

## 触发说明
当用户要求用三月七视角分析、安慰、吐槽或做角色化表达时使用。

## 心智模型
保留角色语气，但不要编造官方剧情。

## 决策启发式
优先保护同伴，先确认风险，再用轻快直接的语言鼓励对方。

## 表达DNA
轻快、真诚、带一点拍照记录感。

## 时间线
按公开剧情节点维护，不覆盖未验证版本。

## 诚实边界
未知设定必须标注假设，不把玩家二创当官方事实。

## 调研来源
见 references/research 下的来源记录。
"""
        + "\n".join(
            f"- 细化规则 {idx}: 输出时必须保留三月七的乐观、朋友优先、拍照记录感和对未知身世的诚实边界。"
            for idx in range(90)
        )
    )
    (target_dir / "SKILL.md").write_text(
        rich_skill_body,
        encoding="utf-8",
    )
    for name in SkillArtifactValidator.REQUIRED_RESEARCH_FILES:
        (research_dir / name).write_text(
            (
                f"# {name}\n\n"
                "结论：这是经过来源约束的调研条目，包含角色设定、剧情表达、玩家解读和时间线证据。\n\n"
                "来源：https://example.com/honkai-star-rail/march-7th\n"
                "可信度：medium。该条目用于测试 runner 集成层能正确调用 validator。\n"
            ),
            encoding="utf-8",
        )

    result = RuntimeEpisodeRunner()._validate_skill_artifact_if_requested(
        {"episodeId": "episode-skill"},
        need={
            "workspacePath": str(tmp_path),
            "targetSkillRoot": str(target_dir),
            "validateSkillArtifact": True,
            "requireHuashuResearch": True,
        },
        inputs={
            "workspacePath": str(tmp_path),
            "validateSkillArtifact": True,
            "requiredSkillContracts": ["huashu-nuwa", "skill-creator"],
        },
    )

    assert result is not None
    assert result["ok"] is True
    assert result["status"] == "skill_artifact_ready"
    assert result["validatedRoot"] == str(target_dir.resolve())


def test_runtime_runner_extracts_missing_skill_root_from_goal_text(tmp_path):
    from core.runtime_episode_runner import RuntimeEpisodeRunner

    target_dir = tmp_path / ".agents" / "skills" / "sanyueqi-perspective"
    target_dir.mkdir(parents=True)

    result = RuntimeEpisodeRunner()._validate_skill_artifact_if_requested(
        {"episodeId": "episode-skill-missing"},
        need={
            "workspacePath": str(tmp_path),
            "reason": f"生成 skill 到 {target_dir}；如果目录存在请覆盖。",
            "validateSkillArtifact": True,
        },
        inputs={
            "workspacePath": str(tmp_path),
            "validateSkillArtifact": True,
            "requiredSkillContracts": ["huashu-nuwa", "skill-creator"],
        },
    )

    assert result is not None
    assert result["ok"] is False
    assert result["status"] == "skill_artifact_invalid"
    assert any(item.get("skillRoot") == str(target_dir.resolve()) for item in result["results"])
    assert any("缺少 SKILL.md" in finding for finding in result["findings"])


def test_engineering_worker_briefs_for_skill_artifact_delegate_to_writing_family(tmp_path):
    from core.runtime_episode_runner import RuntimeEpisodeRunner

    worker_briefs = [
        {
            "goal": "用 huashu-nuwa 和 skill-creator 生成 SKILL.md",
            "executionLaneHint": "engineering",
            "familyHint": "engineering",
            "validateSkillArtifact": True,
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"workspacePath": str(tmp_path)},
        inputs={"workspacePath": str(tmp_path), "validateSkillArtifact": True},
    )

    assert normalized[0]["executionLaneHint"] == "subagent"
    assert normalized[0]["familyHint"] == "writing"
    assert normalized[0]["preferredAgentId"] == "skill-workflow-curator"


def test_parallel_branch_fails_huashu_skill_artifact_when_sparse_or_not_discoverable(tmp_path):
    from graph.parallel_support import _run_parallel_agent_branch
    from langgraph.types import Command

    target_dir = tmp_path / ".agents" / "skills" / "sanyueqi-perspective"
    research_dir = target_dir / "references" / "research"
    research_dir.mkdir(parents=True)
    (target_dir / "SKILL.md").write_text("# 三月七视角\n\n待调研后补充。\n", encoding="utf-8")
    for name in [
        "01-writings.md",
        "02-conversations.md",
        "03-expression-dna.md",
        "04-external-views.md",
        "05-decisions.md",
        "06-timeline.md",
    ]:
        (research_dir / name).write_text("待调研\n", encoding="utf-8")

    state = {
        "messages": [],
        "todos": [],
        "workspace_path": str(tmp_path),
        "parallel_branch": {
            "agentId": "skill-workflow-curator",
            "agentName": "Skill Workflow Curator",
            "delegationId": "delegation-artifact-sparse",
            "invocationId": "invoke-artifact-sparse",
            "taskBriefId": "brief-artifact-sparse",
            "reason": f"Use huashu-nuwa to write {target_dir}\\SKILL.md and references/research/01-writings.md.",
            "taskBrief": {
                "goal": f"Write reusable skill into {target_dir}",
                "context": {"skillName": "huashu-nuwa"},
                "acceptanceContract": "Required files: SKILL.md and references/research/01-writings.md through 06-timeline.md.",
            },
        },
    }

    def _node_func(_state):
        return Command(goto="supervisor", update={})

    _delta_messages, _delta_todos, summary, _child_requests = asyncio.run(
        _run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"})
    )

    assert summary["status"] == "failed"
    assert summary["error"] == "artifact_acceptance_failed"
    assert any("missing_frontmatter" in item for item in summary["sparseArtifacts"])
    assert any("missing_sections" in item for item in summary["sparseArtifacts"])
    assert any("too_short" in item for item in summary["sparseArtifacts"])
