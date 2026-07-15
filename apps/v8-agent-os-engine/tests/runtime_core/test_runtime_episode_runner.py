from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langgraph.types import Send

from erc.runtime_context import bind_runtime_context
from core.database import DatabaseManager, db
from core.agents import default_subagent_configs
from core.delegation_broker import choose_best_local_agent_with_diagnostics
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
import core.runtime_episode_runner as runtime_episode_runner_module
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_handoff_ref, build_runtime_episode


def _delegation_send(task_id: str, *, deps: list[str] | None = None, agent_id: str = "worker") -> Send:
    task_brief = {
        "taskBriefId": task_id,
        "dependency": list(deps or []),
        "context": {"taskId": task_id},
    }
    return Send(
        "parallel_delegate_task",
        {
            "parallel_branch": {
                "agentId": agent_id,
                "agentName": agent_id,
                "taskBriefId": task_id,
                "taskBrief": task_brief,
                "dependency": list(deps or []),
                "reason": f"Run {task_id}",
            }
        },
    )


def test_child_target_inference_prioritizes_verification_over_research_evidence_terms():
    target = RuntimeEpisodeRunner._infer_child_delegation_target(
        "Verify the research evidence bundle and perform a final risk review before completion."
    )

    assert target == ("verification-engineer", "Verification Engineer", "verification_goal")


def test_runtime_episode_queue_claim_and_unknown_executor_completes_recoverably(tmp_path, monkeypatch):
    manager = DatabaseManager(tmp_path / "unknown-executor.db")
    monkeypatch.setattr(runtime_episode_runner_module, "db", manager)
    kind = "test_unknown_episode"
    episode = build_runtime_episode(
        need={"kind": kind, "source": "test", "reason": "exercise queue"},
        kind=kind,
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    manager.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    runner = RuntimeEpisodeRunner()
    claimed = manager.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert claimed is not None
    assert claimed["episodeId"] == episode["episodeId"]
    assert claimed["state"] == "active"

    asyncio.run(runner._execute_episode(claimed))

    stored = manager.get_runtime_episode(episode["episodeId"])
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


def test_engineering_plan_only_without_workers_returns_ready_handoff():
    episode = build_runtime_episode(
        need={
            "kind": "engineering",
            "source": "test",
            "reason": "请执行长任务压测方案，只输出执行地图和阶段状态，不需要真实写文件。",
            "deliverableKind": "plan_only",
            "writeRequired": False,
        },
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "task": "规划一个包含 Research、Engineering、Subagent 的压测方案，不写文件。",
                "deliverableKind": "plan_only",
                "writeRequired": False,
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert handoff["status"] == "ready"
    assert handoff["engineeringState"] == "work_plan_ready"
    assert handoff["deliverableKind"] == "plan_only"
    assert handoff["writeRequired"] is False
    assert handoff.get("recoverable") is not True
    assert "errorCode" not in handoff


def test_engineering_plan_only_with_worker_brief_generates_real_delegated_plan(monkeypatch):
    runner = RuntimeEpisodeRunner()
    captured: dict = {}

    async def _fake_execute_delegation(delegation_episode):
        captured.update(delegation_episode)
        return {
            "status": "ready",
            "confidence": "medium",
            "compactSummary": "Engineering planner returned a three-phase implementation plan.",
            "childHandoffs": [
                {
                    "kind": "subagent_result",
                    "status": "ready",
                    "compactSummary": "Phase 1 routes; phase 2 validates; phase 3 rolls back safely.",
                }
            ],
        }

    monkeypatch.setattr(runner, "_execute_delegation", _fake_execute_delegation)
    monkeypatch.setattr(
        runtime_episode_runner_module,
        "build_engineering_kernel_context",
        lambda **_kwargs: ("workspace digest", []),
    )
    episode = build_runtime_episode(
        need={
            "kind": "engineering",
            "reason": "Produce an execution plan without writing files.",
            "inputs": {
                "deliverableKind": "plan_only",
                "writeRequired": False,
                "taskBriefs": [
                    {
                        "taskBriefId": "PLAN-001",
                        "goal": "Produce a source-backed engineering execution plan.",
                        "writeRequired": False,
                        "writeSet": [],
                        "expectedOutputs": ["phases", "verification matrix", "rollback plan"],
                        "acceptanceContract": "Return an actionable plan with evidence refs.",
                        "familyHint": "engineering",
                    }
                ],
            },
        },
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )

    handoff = asyncio.run(runner._execute_engineering(episode))

    assert captured["kind"] == "delegation"
    assert captured["inputs"]["workerBriefs"][0]["taskBriefId"] == "PLAN-001"
    assert handoff["status"] == "ready"
    assert handoff["engineeringState"] == "work_plan_started"
    assert handoff["deliverableKind"] == "plan_only"
    assert handoff["writeRequired"] is False
    assert "three-phase implementation plan" in handoff["compactSummary"]


def test_engineering_plan_only_brief_stays_in_engineering_family() -> None:
    runner = RuntimeEpisodeRunner()
    briefs = runner._prepare_engineering_worker_briefs_for_delegation(
        [
            {
                "taskBriefId": "PLAN-ARCH",
                "goal": "Inspect the runtime orchestration code and return an architecture execution plan.",
                "writeRequired": False,
                "detailRefs": ["core/runtime_episode_runner.py", "graph/supervisor_turn.py"],
            }
        ],
        need={"kind": "engineering", "reason": "plan only", "writeRequired": False},
        inputs={"deliverableKind": "plan_only", "writeRequired": False},
    )
    brief = briefs[0]
    agents = [agent.model_dump() for agent in default_subagent_configs()]
    selected, diagnostics = choose_best_local_agent_with_diagnostics(brief, agents)

    assert brief["familyHint"] == "engineering"
    assert brief["readSet"] == ["core/runtime_episode_runner.py", "graph/supervisor_turn.py"]
    assert {"software_engineering", "architecture", "review"}.issubset(set(brief["requiredCapabilities"]))
    assert selected is not None
    assert selected["capabilitySnapshot"]["specialistFamily"] == "engineering"
    assert selected["id"] != "web-research-architect"
    assert diagnostics["targetFamily"] == "engineering"


def test_runtime_runner_finalizes_direct_delegation_episode(monkeypatch, tmp_path) -> None:
    manager = DatabaseManager(tmp_path / "direct-delegation.db")
    manager.create_or_update_session("session-direct", "Direct Delegation")
    manager.create_run_record(
        run_id="run-direct",
        session_id="session-direct",
        run_type="chat",
        status="running",
    )
    parent = build_runtime_episode(
        need={"kind": "engineering", "reason": "plan only"},
        kind="engineering",
        state="active",
        continuation_target="runtime_episode_runner",
        extra={"sessionId": "session-direct", "runId": "run-direct", "inputs": {"workspacePath": str(tmp_path)}},
    )
    manager.upsert_runtime_episode_record(parent, session_id="session-direct", run_id="run-direct", enqueue=False)
    delegation_id = "subagent::delegation_direct::0::PLAN-1::code-review-architect"
    direct = build_runtime_episode(
        need={
            "kind": "delegation",
            "needId": delegation_id,
            "reason": "review plan",
            "parentEpisodeId": parent["episodeId"],
            "inputs": {"workerBriefs": [{"taskBriefId": "PLAN-1", "goal": "Review plan."}]},
        },
        kind="delegation",
        state="waiting",
        parent_episode_id=parent["episodeId"],
        continuation_target="parallel_delegate_join",
        extra={"sessionId": "session-direct", "runId": "run-direct"},
    )
    manager.upsert_runtime_episode_record(direct, session_id="session-direct", run_id="run-direct", enqueue=False)
    monkeypatch.setattr(runtime_episode_runner_module, "db", manager)
    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda _self: {"code-review-architect": {"id": "code-review-architect"}})

    async def _fake_branch(_arg, _agent_data, progress_callback=None):
        if progress_callback:
            progress_callback({"stage": "working", "status": "running", "summary": "Reviewing orchestration."})
        return [], [], {
            "taskBriefId": "PLAN-1",
            "delegationId": delegation_id,
            "agentId": "code-review-architect",
            "agentName": "Code Review Architect",
            "status": "ok",
            "summary": "Three bounded phases with verification and rollback were returned.",
            "resultText": "Phase 1 inspect; phase 2 change; phase 3 verify and rollback.",
        }, []

    monkeypatch.setattr("graph.parallel_support._run_parallel_agent_branch", _fake_branch)
    from langgraph.types import Command, Send

    command = Command(
        goto=[
            Send(
                "parallel_delegate_task",
                {
                    "parallel_branch": {
                        "agentId": "code-review-architect",
                        "agentName": "Code Review Architect",
                        "delegationId": delegation_id,
                        "invocationId": "delegation_direct",
                        "taskBriefId": "PLAN-1",
                        "taskBrief": {"taskBriefId": "PLAN-1", "goal": "Review plan."},
                        "reason": "Review plan.",
                    },
                    "messages": [],
                    "todos": [],
                },
            )
        ],
        update={},
    )

    results, child_ids = asyncio.run(RuntimeEpisodeRunner()._execute_local_delegation_sends(command, parent))

    stored = manager.get_runtime_episode(delegation_id)
    handoffs = manager.list_runtime_episode_handoffs(delegation_id)
    with manager.get_connection() as conn:
        topics = [row["topic"] for row in conn.execute(
            "SELECT topic FROM runtime_episode_events WHERE episode_id = ? ORDER BY created_at",
            (delegation_id,),
        ).fetchall()]
    assert results[0]["status"] == "ok"
    assert child_ids == []
    assert stored["state"] == "completed"
    assert handoffs[-1]["payload"]["status"] == "ready"
    assert "runtime.episode.progress" in topics
    assert topics[-1] == "runtime.episode.completed"


def test_local_delegation_blocks_task_when_dependency_failed(monkeypatch):
    runner = RuntimeEpisodeRunner()
    executed: list[str] = []

    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda _self: {"worker": {"id": "worker"}})

    async def _await_without_heartbeat(_self, _episode_id, awaitable, **_kwargs):
        return await awaitable

    monkeypatch.setattr(RuntimeEpisodeRunner, "_await_with_heartbeat", _await_without_heartbeat)

    async def _fake_branch(arg, _agent_data, progress_callback=None):
        task_id = arg["parallel_branch"]["taskBriefId"]
        executed.append(task_id)
        if task_id == "TASK-001":
            return [], [], {"taskBriefId": task_id, "status": "error", "error": "research_failed"}, []
        return [], [], {"taskBriefId": task_id, "status": "ok"}, []

    monkeypatch.setattr("graph.parallel_support._run_parallel_agent_branch", _fake_branch)

    results, _children = asyncio.run(
        runner._execute_local_delegation_sends(
            SimpleNamespace(goto=[_delegation_send("TASK-001"), _delegation_send("TASK-002", deps=["TASK-001"])]),
            {"episodeId": "episode_test", "inputs": {}, "need": {}},
        )
    )

    assert executed == ["TASK-001"]
    assert [item["taskBriefId"] for item in results] == ["TASK-001", "TASK-002"]
    assert results[1]["status"] == "blocked"
    assert results[1]["error"] == "dependency_failed"
    assert results[1]["blockedDependencies"] == ["TASK-001"]


def test_local_delegation_passes_dependency_results_to_dependent_task(monkeypatch):
    runner = RuntimeEpisodeRunner()
    seen_args: dict[str, dict] = {}

    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda _self: {"worker": {"id": "worker"}})

    async def _await_without_heartbeat(_self, _episode_id, awaitable, **_kwargs):
        return await awaitable

    monkeypatch.setattr(RuntimeEpisodeRunner, "_await_with_heartbeat", _await_without_heartbeat)

    async def _fake_branch(arg, _agent_data, progress_callback=None):
        task_id = arg["parallel_branch"]["taskBriefId"]
        seen_args[task_id] = arg
        return [], [], {"taskBriefId": task_id, "status": "ok", "summary": f"{task_id} finished"}, []

    monkeypatch.setattr("graph.parallel_support._run_parallel_agent_branch", _fake_branch)

    results, _children = asyncio.run(
        runner._execute_local_delegation_sends(
            SimpleNamespace(goto=[_delegation_send("TASK-001"), _delegation_send("TASK-002", deps=["TASK-001"])]),
            {"episodeId": "episode_test", "inputs": {}, "need": {}},
        )
    )

    assert [item["taskBriefId"] for item in results] == ["TASK-001", "TASK-002"]
    dependency_results = seen_args["TASK-002"]["parallel_branch"]["taskBrief"]["context"]["dependencyResults"]
    assert dependency_results[0]["taskBriefId"] == "TASK-001"
    assert dependency_results[0]["status"] == "ok"
    assert "TASK-001 finished" in dependency_results[0]["summary"]


def test_cross_episode_degraded_handoff_can_continue_when_recovery_allows(monkeypatch):
    upstream = {
        "episodeId": "episode_research_degraded",
        "runId": "run-cross-episode",
        "kind": "research",
        "state": "degraded",
        "inputs": {"taskBriefs": [{"taskBriefId": "RESEARCH-001", "goal": "Collect evidence."}]},
        "metadata": {"recovery": {"canContinueParent": True}},
    }
    downstream = {
        "episodeId": "episode_engineering_consumer",
        "runId": "run-cross-episode",
        "kind": "engineering",
        "state": "queued",
        "inputs": {
            "taskBriefs": [
                {
                    "taskBriefId": "ENGINEERING-001",
                    "goal": "Use the bounded degraded evidence.",
                    "dependency": ["RESEARCH-001"],
                }
            ]
        },
    }
    monkeypatch.setattr(runtime_episode_runner_module.db, "list_runtime_episodes", lambda **_kwargs: [upstream, downstream])
    monkeypatch.setattr(
        runtime_episode_runner_module.db,
        "list_runtime_episode_handoffs",
        lambda _episode_id: [
            {
                "handoffId": "handoff-research-degraded",
                "payload": {
                    "status": "degraded",
                    "compactSummary": "Two sources are available; one requested source is missing.",
                    "degradedReason": "research_run_missing_evidence",
                },
            }
        ],
    )

    prepared, gate = RuntimeEpisodeRunner._prepare_cross_episode_dependencies(downstream)

    assert gate is None
    dependency = prepared["inputs"]["dependencyResults"][0]
    assert dependency["status"] == "degraded"
    assert dependency["canContinueParent"] is True
    assert dependency["degradedReason"] == "research_run_missing_evidence"
    assert prepared["inputs"]["taskBriefs"][0]["context"]["dependencyResults"] == [dependency]


def test_cross_episode_degraded_handoff_blocks_when_recovery_disallows(monkeypatch):
    upstream = {
        "episodeId": "episode_research_blocked",
        "runId": "run-cross-episode-blocked",
        "kind": "research",
        "state": "degraded",
        "inputs": {"taskBriefs": [{"taskBriefId": "RESEARCH-002", "goal": "Collect evidence."}]},
        "metadata": {"recovery": {"canContinueParent": False}},
    }
    downstream = {
        "episodeId": "episode_engineering_blocked",
        "runId": "run-cross-episode-blocked",
        "kind": "engineering",
        "state": "queued",
        "inputs": {
            "taskBriefs": [
                {
                    "taskBriefId": "ENGINEERING-002",
                    "goal": "Must consume accepted evidence.",
                    "dependency": ["RESEARCH-002"],
                }
            ]
        },
    }
    monkeypatch.setattr(runtime_episode_runner_module.db, "list_runtime_episodes", lambda **_kwargs: [upstream, downstream])
    monkeypatch.setattr(
        runtime_episode_runner_module.db,
        "list_runtime_episode_handoffs",
        lambda _episode_id: [{"payload": {"status": "degraded", "compactSummary": "Unusable evidence."}}],
    )

    _prepared, gate = RuntimeEpisodeRunner._prepare_cross_episode_dependencies(downstream)

    assert gate["state"] == "failed"
    assert gate["errorCode"] == "cross_episode_dependency_failed"
    assert gate["dependencyResults"][0]["status"] == "failed"


def test_local_delegation_retries_unsafe_verification_once_after_artifact_exists(monkeypatch, tmp_path):
    runner = RuntimeEpisodeRunner()
    artifact = tmp_path / "index.html"
    artifact.write_text("<h1>ready</h1>", encoding="utf-8")
    calls: list[dict] = []

    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda _self: {"worker": {"id": "worker"}})

    async def _await_without_heartbeat(_self, _episode_id, awaitable, **_kwargs):
        return await awaitable

    monkeypatch.setattr(RuntimeEpisodeRunner, "_await_with_heartbeat", _await_without_heartbeat)

    async def _fake_branch(arg, _agent_data, progress_callback=None):
        calls.append(arg)
        task_id = arg["parallel_branch"]["taskBriefId"]
        if task_id == "TASK-001" and len(calls) == 1:
            raise ModelGovernanceInterventionRequired(
                "unsafe verification",
                approval_kind="safety_review",
                question="approve eval",
                details={"safety": {"riskCode": "encoded_command_review"}},
            )
        return [], [], {"taskBriefId": task_id, "status": "ok", "summary": f"{task_id} finished"}, []

    monkeypatch.setattr("graph.parallel_support._run_parallel_agent_branch", _fake_branch)
    write_task = {
        "taskBriefId": "TASK-001",
        "writeRequired": True,
        "engineeringTaskCapsule": {"expectedArtifacts": ["index.html"]},
        "context": {"taskId": "TASK-001"},
    }
    dependent_task = {
        "taskBriefId": "TASK-002",
        "dependency": ["TASK-001"],
        "context": {"taskId": "TASK-002"},
    }
    sends = [
        Send(
            "parallel_delegate_task",
            {
                "parallel_branch": {
                    "agentId": "worker",
                    "agentName": "worker",
                    "taskBriefId": "TASK-001",
                    "taskBrief": write_task,
                    "reason": "Run TASK-001",
                }
            },
        ),
        Send(
            "parallel_delegate_task",
            {
                "parallel_branch": {
                    "agentId": "worker",
                    "agentName": "worker",
                    "taskBriefId": "TASK-002",
                    "taskBrief": dependent_task,
                    "dependency": ["TASK-001"],
                    "reason": "Run TASK-002",
                }
            },
        ),
    ]

    results, _children = asyncio.run(
        runner._execute_local_delegation_sends(
            SimpleNamespace(goto=sends),
            {"episodeId": "episode_test", "inputs": {"workspacePath": str(tmp_path)}, "need": {}},
        )
    )

    assert len(calls) == 3
    retry_arg = calls[1]
    assert retry_arg["parallel_branch"]["governanceSafeRetryCount"] == 1
    assert retry_arg["parallel_branch"]["taskBrief"]["context"]["governanceSafeVerificationRetry"]["required"] is True
    assert "不要重试该命令" in retry_arg["messages"][-1].content
    assert [item["status"] for item in results] == ["ok", "ok"]
    assert results[0]["governanceSafeRetry"] is True


def test_local_delegation_refreshes_stale_node_map_before_missing(monkeypatch):
    runner = RuntimeEpisodeRunner()
    calls: list[bool] = []

    def _build_map(self, *, force_refresh: bool = False):  # noqa: ANN001
        calls.append(force_refresh)
        self._agent_nodes_map_snapshot_hash = "hash-new" if force_refresh else "hash-old"
        self._agent_nodes_map_snapshot_version = "subagents:new" if force_refresh else "subagents:old"
        return {"worker": {"id": "worker"}} if force_refresh else {}

    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", _build_map)

    async def _await_without_heartbeat(_self, _episode_id, awaitable, **_kwargs):
        return await awaitable

    monkeypatch.setattr(RuntimeEpisodeRunner, "_await_with_heartbeat", _await_without_heartbeat)

    async def _fake_branch(arg, _agent_data, progress_callback=None):
        return [], [], {"taskBriefId": arg["parallel_branch"]["taskBriefId"], "status": "ok"}, []

    monkeypatch.setattr("graph.parallel_support._run_parallel_agent_branch", _fake_branch)

    results, _children = asyncio.run(
        runner._execute_local_delegation_sends(
            SimpleNamespace(
                goto=[
                    _delegation_send("TASK-001", agent_id="worker"),
                ]
            ),
            {"episodeId": "episode_test", "inputs": {}, "need": {}},
        )
    )

    assert calls == [False, True]
    assert results[0]["status"] == "ok"


def test_local_delegation_blocks_dependent_task_when_research_artifact_is_placeholder(monkeypatch, tmp_path):
    runner = RuntimeEpisodeRunner()
    executed: list[str] = []
    research_file = tmp_path / "references" / "research" / "01-writings.md"
    research_file.parent.mkdir(parents=True)
    research_file.write_text(
        "# 01 - 官方设定调研\n\n"
        "**状态：** 待执行\n\n"
        "## 证据包\n（待 Phase 1 调研完成后填充）\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(RuntimeEpisodeRunner, "_build_agent_nodes_map", lambda _self: {"worker": {"id": "worker"}})

    async def _await_without_heartbeat(_self, _episode_id, awaitable, **_kwargs):
        return await awaitable

    monkeypatch.setattr(RuntimeEpisodeRunner, "_await_with_heartbeat", _await_without_heartbeat)

    async def _fake_branch(arg, _agent_data, progress_callback=None):
        task_id = arg["parallel_branch"]["taskBriefId"]
        executed.append(task_id)
        return [], [], {"taskBriefId": task_id, "status": "ok", "summary": f"{task_id} returned"}, []

    monkeypatch.setattr("graph.parallel_support._run_parallel_agent_branch", _fake_branch)
    research_task = {
        "taskBriefId": "TASK-001",
        "goal": "调研玲的官方设定。",
        "familyHint": "research",
        "context": {"runtimeLane": "Research"},
        "engineeringTaskCapsule": {"expectedArtifacts": ["references/research/01-writings.md"]},
    }
    build_task = {
        "taskBriefId": "TASK-002",
        "dependency": ["TASK-001"],
        "context": {"taskId": "TASK-002"},
    }
    sends = [
        Send(
            "parallel_delegate_task",
            {
                "parallel_branch": {
                    "agentId": "worker",
                    "agentName": "worker",
                    "taskBriefId": "TASK-001",
                    "taskBrief": research_task,
                    "reason": "Run TASK-001",
                }
            },
        ),
        Send(
            "parallel_delegate_task",
            {
                "parallel_branch": {
                    "agentId": "worker",
                    "agentName": "worker",
                    "taskBriefId": "TASK-002",
                    "taskBrief": build_task,
                    "dependency": ["TASK-001"],
                    "reason": "Run TASK-002",
                }
            },
        ),
    ]

    results, _children = asyncio.run(
        runner._execute_local_delegation_sends(
            SimpleNamespace(goto=sends),
            {"episodeId": "episode_test", "inputs": {"workspacePath": str(tmp_path)}, "need": {}},
        )
    )

    assert executed == ["TASK-001"]
    assert results[0]["status"] == "blocked"
    assert results[0]["error"] == "expected_artifact_not_ready"
    assert results[0]["unreadyExpectedArtifacts"] == ["references/research/01-writings.md"]
    assert results[1]["status"] == "blocked"
    assert results[1]["error"] == "dependency_failed"
    assert results[1]["blockedDependencies"] == ["TASK-001"]


def test_engineering_plan_only_with_task_briefs_delegates_a_plan_specialist(monkeypatch):
    captured: dict = {}

    async def _capture_delegation(self, delegation_episode):
        captured.update(delegation_episode)
        return {
            "status": "ready",
            "compactSummary": "A bounded engineering plan was produced.",
            "childHandoffs": [
                {
                    "kind": "subagent_result",
                    "status": "ready",
                    "compactSummary": "Implement, verify, then roll back if verification fails.",
                }
            ],
        }

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _capture_delegation)
    episode = build_runtime_episode(
        need={
            "kind": "engineering",
            "source": "test",
            "reason": "explicit plan_only runtime validation",
            "deliverableKind": "plan_only",
            "writeRequired": False,
        },
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "deliverableKind": "plan_only",
                "writeRequired": False,
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Create a work plan only.",
                        "acceptanceContract": "Return work_plan_ready.",
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert captured["kind"] == "delegation"
    assert captured["inputs"]["workerBriefs"][0]["taskBriefId"] == "task-1"
    assert handoff["status"] == "ready"
    assert handoff["engineeringState"] == "work_plan_started"
    assert handoff["deliverableKind"] == "plan_only"
    assert handoff["writeRequired"] is False


def test_engineering_mixed_spec_tasks_do_not_become_plan_only():
    runner = RuntimeEpisodeRunner()
    worker_briefs = [
        {
            "taskBriefId": "TASK-001",
            "goal": "Create target output directory.",
            "writeRequired": False,
            "deliverableKind": "proof",
        },
        {
            "taskBriefId": "TASK-002",
            "goal": "Write index.html into the approved Spec target directory.",
            "writeRequired": True,
            "deliverableKind": "artifact",
            "engineeringTaskCapsule": {
                "writeRequired": True,
                "deliverableKind": "artifact",
                "writeSet": [".v8/live-audit/spec-mode-v2/demo/index.html"],
            },
        },
    ]

    assert runner._is_engineering_plan_only_request(
        need={"kind": "engineering", "reason": "approved_spec_runtime_execution"},
        inputs={"workspacePath": "E:/Projects/test3"},
        worker_briefs=worker_briefs,
    ) is False
    assert runner._engineering_requires_write_evidence(
        need={"kind": "engineering", "reason": "approved_spec_runtime_execution"},
        inputs={"workspacePath": "E:/Projects/test3"},
        worker_briefs=worker_briefs,
    ) is True


@pytest.mark.parametrize("terminal_state", ["completed", "degraded", "failed", "cancelled"])
def test_top_level_episode_terminal_state_schedules_chat_handoff_resume(monkeypatch, terminal_state):
    runner = RuntimeEpisodeRunner()
    scheduled = []
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.runtime_episode_runner._schedule_runtime_episode_handoff_resume",
        lambda episode: scheduled.append(dict(episode)) or {"resume_mode": "chat", "resume_scheduled": True},
    )

    runner._maybe_schedule_chat_handoff_resume(
        {
            "episodeId": "episode_done",
            "kind": "engineering",
            "state": terminal_state,
            "sessionId": "session_done",
            "runId": "run_done",
        }
    )

    assert scheduled and scheduled[0]["episodeId"] == "episode_done"


def test_nonterminal_top_level_episode_does_not_schedule_chat_handoff_resume(monkeypatch):
    runner = RuntimeEpisodeRunner()
    scheduled = []
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.runtime_episode_runner._schedule_runtime_episode_handoff_resume",
        lambda episode: scheduled.append(dict(episode)) or {"resume_mode": "chat", "resume_scheduled": True},
    )

    runner._maybe_schedule_chat_handoff_resume(
        {
            "episodeId": "episode_active",
            "kind": "engineering",
            "state": "active",
            "sessionId": "session_active",
            "runId": "run_active",
        }
    )

    assert scheduled == []


def test_child_episode_completion_does_not_schedule_chat_handoff_resume(monkeypatch):
    runner = RuntimeEpisodeRunner()
    scheduled = []
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.runtime_episode_runner._schedule_runtime_episode_handoff_resume",
        lambda episode: scheduled.append(dict(episode)) or {"resume_mode": "chat", "resume_scheduled": True},
    )

    runner._maybe_schedule_chat_handoff_resume(
        {
            "episodeId": "episode_child",
            "parentEpisodeId": "episode_parent",
            "kind": "delegation",
            "state": "completed",
            "sessionId": "session_done",
            "runId": "run_done",
        }
    )

    assert scheduled == []


def test_cancelled_handoff_persists_cancelled_episode_and_resumes_chat(monkeypatch):
    runner = RuntimeEpisodeRunner()
    completed_calls = []
    scheduled = []

    async def _cancelled_research(_episode):
        return {
            "kind": "research",
            "status": "cancelled",
            "compactSummary": "Research episode was cancelled.",
        }

    monkeypatch.setattr(runner, "_execute_research", _cancelled_research)
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_maybe_resume_parent_episode", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "_maybe_schedule_chat_handoff_resume",
        lambda episode: scheduled.append(dict(episode)),
    )
    monkeypatch.setattr(
        "core.runtime_episode_runner.db.add_runtime_episode_handoff",
        lambda **kwargs: {**dict(kwargs["handoff"]), "handoffId": "handoff_cancelled"},
    )

    def _complete(episode_id, **kwargs):
        completed_calls.append({"episode_id": episode_id, **kwargs})
        return {
            "episodeId": episode_id,
            "kind": "research",
            "state": kwargs["state"],
            "sessionId": "session_cancelled",
            "runId": "run_cancelled",
        }

    monkeypatch.setattr("core.runtime_episode_runner.db.complete_runtime_episode", _complete)

    asyncio.run(
        runner._execute_episode(
            {
                "episodeId": "episode_cancelled",
                "kind": "research",
                "state": "active",
                "sessionId": "session_cancelled",
                "runId": "run_cancelled",
            }
        )
    )

    assert completed_calls[0]["state"] == "cancelled"
    assert completed_calls[0]["metadata"]["recovery"]["nextAction"] == "report_cancelled"
    assert scheduled and scheduled[0]["state"] == "cancelled"


def test_task_brief_normalization_preserves_engineering_write_contract():
    from core.delegation_broker import normalize_task_brief

    brief = normalize_task_brief(
        {
            "taskBriefId": "game-project",
            "goal": "Create a fullscreen Canvas game.",
            "deliverableKind": "artifact",
            "writeRequired": True,
        }
    )

    assert brief["deliverableKind"] == "artifact"
    assert brief["writeRequired"] is True


def test_engineering_write_contract_not_confused_by_plan_only_text_in_acceptance(monkeypatch):
    async def _fake_delegation(self, _episode):
        return {
            "kind": "subagent_result_bundle",
            "status": "ready",
            "compactSummary": "Delegation created project files.",
            "createdFiles": ["index.html", "src/main.js", "README.md"],
        }

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _fake_delegation)
    episode = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "create a canvas web game project"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "userRequest": "创建一个全屏 Canvas 像素风网页游戏项目。",
                "taskBriefs": [
                    {
                        "taskBriefId": "game-project",
                        "goal": "创建一个全屏 Canvas 像素风网页游戏项目。",
                        "deliverableKind": "artifact",
                        "writeRequired": True,
                        "requiredCapabilities": ["workspace_mutation", "verification"],
                        "behaviorScope": ["implementation", "verification"],
                        "acceptanceContract": {
                            "must": [
                                "Return engineering_patch_bundle with touched files.",
                                "For plan_only or writeRequired=false, do not fail merely because no patch worker ran.",
                            ]
                        },
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert handoff["status"] == "ready"
    assert handoff["engineeringState"] == "execution_started"
    assert handoff["delegationHandoff"]["createdFiles"] == ["index.html", "src/main.js", "README.md"]


def test_engineering_write_episode_rejects_ready_handoff_without_write_evidence(monkeypatch):
    async def _fake_delegation(self, _episode):
        return {
            "kind": "subagent_result_bundle",
            "status": "ready",
            "compactSummary": "Delegation executed 1 local subagent worker(s).",
            "results": [
                {
                    "targetLabel": "Implementation Engineer",
                    "status": "ok",
                    "toolsUsed": ["run_system_command"],
                    "compactTranscript": (
                        "$ mkdir \".v8/live-audit/pixel-run-gun/demo\"\n"
                        "$ dir \".v8/live-audit/pixel-run-gun/demo\"\n"
                        "0 个文件 0 字节"
                    ),
                }
            ],
            "acceptanceCheck": {"must": {"passed": True, "items": ["Return touched files."]}},
        }

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _fake_delegation)
    episode = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "create a canvas web game project"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "userRequest": "创建一个全屏 Canvas 像素风网页游戏项目，包含 index.html、main.js、README.md。",
                "taskBriefs": [
                    {
                        "taskBriefId": "game-project",
                        "goal": "创建一个全屏 Canvas 像素风网页游戏项目。",
                        "requiredCapabilities": ["workspace_mutation", "verification"],
                        "behaviorScope": ["implementation", "verification"],
                        "acceptanceContract": "Return touched files and proof.",
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert handoff["status"] == "degraded"
    assert handoff["engineeringState"] == "recoverable_failed"
    assert handoff["errorCode"] == "engineering_missing_write_evidence"
    assert handoff["degraded"] is True
    assert handoff["recoverable"] is True
    assert handoff["degradedReason"] == "engineering_missing_write_evidence"


def test_engineering_write_episode_rejects_claim_only_handoff_without_structured_evidence(monkeypatch):
    async def _fake_delegation(self, _episode):
        return {
            "kind": "subagent_result_bundle",
            "status": "ready",
            "compactSummary": "Created index.html and wrote the requested project files.",
            "results": [{"status": "ok", "resultText": "已写入 index.html，任务完成。"}],
        }

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _fake_delegation)
    episode = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "create index.html"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "userRequest": "创建 index.html。",
                "taskBriefs": [
                    {
                        "taskBriefId": "write-index",
                        "goal": "创建 index.html。",
                        "writeRequired": True,
                        "deliverableKind": "artifact",
                        "expectedOutputs": ["index.html exists"],
                        "acceptanceContract": "Return structured artifact evidence.",
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert handoff["status"] == "degraded"
    assert handoff["errorCode"] == "engineering_missing_write_evidence"


def test_expected_artifact_path_preserves_absolute_windows_workspace_path(tmp_path):
    from graph.parallel_support import _infer_expected_artifact_paths

    artifact = tmp_path / "result.txt"
    branch = {
        "taskBrief": {
            "writeRequired": True,
            "engineeringTaskCapsule": {
                "writeRequired": True,
                "expectedArtifacts": [str(artifact)],
            },
        }
    }

    paths = _infer_expected_artifact_paths(branch, {"workspace_path": str(tmp_path)})

    assert paths == [artifact.resolve()]


def test_delegation_handoff_accepts_nested_structured_artifact_evidence():
    handoff = {
        "status": "ready",
        "results": [
            {
                "status": "ok",
                "artifactRefs": [{"path": "result.txt", "kind": "workspace_artifact"}],
            }
        ],
    }

    assert RuntimeEpisodeRunner._delegation_handoff_has_write_evidence(handoff) is True


def test_engineering_skill_artifact_validation_failure_returns_degraded_handoff(monkeypatch, tmp_path):
    target_dir = tmp_path / ".agents" / "skills" / "ling-perspective"
    target_dir.mkdir(parents=True)

    async def _fake_delegation(self, _episode):
        return {
            "kind": "delegation",
            "status": "degraded",
            "compactSummary": "Delegation degraded after research worker model errors.",
            "delegationState": "delegation_degraded",
            "degradedReason": "delegation_worker_failed",
            "results": [
                {
                    "targetLabel": "Web Research Architect",
                    "status": "failed",
                    "error": "ModelGovernanceInterventionRequired",
                }
            ],
        }

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _fake_delegation)
    episode = build_runtime_episode(
        need={
            "kind": "engineering",
            "source": "test",
            "reason": f"生成 skill 到 {target_dir}",
            "workspacePath": str(tmp_path),
            "targetSkillRoot": str(target_dir),
            "validateSkillArtifact": True,
            "requireHuashuResearch": True,
        },
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workspacePath": str(tmp_path),
                "targetSkillRoot": str(target_dir),
                "validateSkillArtifact": True,
                "requiredSkillContracts": ["huashu-nuwa", "skill-creator"],
                "taskBriefs": [
                    {
                        "taskBriefId": "TASK-009",
                        "goal": "生成 ling-perspective/SKILL.md。",
                        "deliverableKind": "artifact",
                        "writeRequired": True,
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert handoff["status"] == "degraded"
    assert handoff["engineeringState"] == "recoverable_failed"
    assert handoff["errorCode"] == "skill_artifact_validation_failed"
    assert handoff["degraded"] is True
    assert handoff["recoverable"] is True
    assert handoff["skillArtifactValidation"]["ok"] is False
    assert handoff["delegationHandoff"]["delegationState"] == "delegation_degraded"


def test_engineering_write_episode_accepts_ready_handoff_with_created_files(monkeypatch):
    async def _fake_delegation(self, _episode):
        return {
            "kind": "subagent_result_bundle",
            "status": "ready",
            "compactSummary": "Delegation created project files.",
            "createdFiles": ["index.html", "src/main.js", "README.md"],
            "commandsRun": ["npm test"],
            "testResults": {"unit": {"status": "passed", "command": "npm test"}},
            "artifactRefs": ["artifact://game-demo"],
            "proofRefs": ["proof://unit-test"],
            "results": [
                {
                    "targetLabel": "Implementation Engineer",
                    "status": "ok",
                    "toolsUsed": ["write_native_file", "run_system_command"],
                }
            ],
        }

    monkeypatch.setattr(RuntimeEpisodeRunner, "_execute_delegation", _fake_delegation)
    episode = build_runtime_episode(
        need={"kind": "engineering", "source": "test", "reason": "create a canvas web game project"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "userRequest": "创建一个全屏 Canvas 像素风网页游戏项目，包含 index.html、main.js、README.md。",
                "taskBriefs": [
                    {
                        "taskBriefId": "game-project",
                        "goal": "创建一个全屏 Canvas 像素风网页游戏项目。",
                        "requiredCapabilities": ["workspace_mutation", "verification"],
                        "behaviorScope": ["implementation", "verification"],
                        "acceptanceContract": "Return touched files and proof.",
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_engineering(episode))

    assert handoff["status"] == "ready"
    assert handoff["engineeringState"] == "execution_started"
    assert handoff["delegationHandoff"]["createdFiles"] == ["index.html", "src/main.js", "README.md"]
    assert "index.html" in handoff["compactSummary"]
    assert "npm test" in handoff["compactSummary"]
    assert "artifact://game-demo" in handoff["compactSummary"]
    assert "proof://unit-test" in handoff["visibleEvidenceSummary"]


def test_delegation_handoff_visible_evidence_summary_extracts_nested_evidence():
    handoff = {
        "compactSummary": "Delegation executed one worker.",
        "results": [
            {
                "workerResult": {
                    "summary": "Built the page.",
                    "changedFiles": ["index.html", "src/main.js"],
                    "testResults": {"unit": {"status": "passed", "command": "npm test"}},
                    "artifactRefs": ["artifact://pixel-demo"],
                    "proofRefs": ["proof://unit-test"],
                },
                "residualRisks": ["No browser smoke was run."],
            }
        ],
    }

    summary = RuntimeEpisodeRunner._delegation_handoff_visible_evidence_summary(handoff)

    assert "index.html" in summary
    assert "src/main.js" in summary
    assert "npm test" in summary
    assert "artifact://pixel-demo" in summary
    assert "proof://unit-test" in summary
    assert "No browser smoke was run." in summary


def test_delegation_episode_without_tasks_returns_degraded_missing_tasks_handoff():
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": ""},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={"inputs": {}},
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_delegation(episode))

    assert handoff["status"] == "degraded"
    assert handoff["kind"] == "delegation_degraded"
    assert handoff["delegationState"] == "delegation_degraded"
    assert handoff["dispatchStatus"] == "missing_tasks"
    assert handoff["missingTasks"] is True
    assert handoff["errorCode"] == "delegation_missing_tasks"


def test_delegation_child_budget_boundary_returns_degraded_handoff(monkeypatch):
    from langgraph.types import Command

    import core.native_tools as native_tools

    def _fake_delegation_broker(**_kwargs):
        return Command(
            update={
                "parallel_results": [
                    {
                        "delegationId": "delegation-test",
                        "targetLabel": "Project Planner",
                        "status": "blocked",
                        "error": "child_delegation_not_allowed",
                        "dispatchStatus": "dispatch_missing_child_budget",
                    }
                ]
            }
        )

    monkeypatch.setattr(native_tools, "delegation_broker", SimpleNamespace(func=_fake_delegation_broker))
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "child budget boundary"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "tasks": [{"title": "Review handoff", "goal": "Review handoff"}],
                "targetCount": 1,
                "allowChildDelegation": False,
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_delegation(episode))

    assert handoff["status"] == "degraded"
    assert handoff["kind"] == "delegation_degraded"
    assert handoff["delegationState"] == "delegation_degraded"
    assert handoff["degradedReason"] == "child_delegation_budget_boundary"
    assert handoff["totalFailedDelegationCount"] == 1
    assert handoff["failedDelegationCount"] == 0


def test_runtime_episode_retry_policy_requeues_before_final_failure(tmp_path, monkeypatch):
    manager = DatabaseManager(tmp_path / "retry-policy.db")
    monkeypatch.setattr(runtime_episode_runner_module, "db", manager)
    kind = "test_retry_episode"
    episode = build_runtime_episode(
        need={"kind": kind, "source": "test", "reason": "retry once"},
        kind=kind,
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={"retryPolicy": {"maxAttempts": 2, "delaySeconds": 0}},
    )
    manager.upsert_runtime_episode_record(episode, enqueue=True, priority=999)

    runner = RuntimeEpisodeRunner()
    first = manager.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert first is not None
    asyncio.run(runner._execute_episode(first))

    after_first = manager.get_runtime_episode(episode["episodeId"])
    assert after_first is not None
    assert after_first["state"] == "queued"

    second = manager.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert second is not None
    asyncio.run(runner._execute_episode(second))

    final = manager.get_runtime_episode(episode["episodeId"])
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

        content = f"mode={kwargs.get('mode')} query={kwargs.get('query')}"
        if kwargs.get("mode") == "run":
            content = json.dumps(
                {
                    "ok": True,
                    "evidenceBundleId": "research_march7",
                    "researchAnswerPack": {
                        "answer": "官方资料与多源证据已汇总。",
                        "sources": [{"title": "Official", "url": "https://example.com/official"}],
                        "claimTable": [{"claim": "supported", "confidence": "high"}],
                        "limitations": [],
                    },
                },
                ensure_ascii=False,
            )
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=content,
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
    assert handoff["researchRefs"] == ["research://bundle/research_march7"]
    assert handoff["sourceCount"] == 1


def test_research_episode_run_without_evidence_bundle_is_degraded(monkeypatch):
    def _fake_research_broker(**kwargs):
        if kwargs.get("mode") == "run":
            return json.dumps({"ok": True, "summary": "Research started but returned no sources."})
        return json.dumps({"ok": True, "items": []})

    import core.native_tools as native_tools

    monkeypatch.setattr(native_tools, "research_broker", SimpleNamespace(func=_fake_research_broker))
    episode = build_runtime_episode(
        need={"kind": "research", "source": "test", "reason": "collect source-backed evidence"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "mode": "run",
                "query": "Collect authoritative evidence.",
                "taskBriefs": [
                    {
                        "taskBriefId": "research-missing-evidence",
                        "goal": "Collect authoritative evidence.",
                    }
                ],
            }
        },
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_research(episode))

    assert handoff["status"] == "degraded"
    assert handoff["degradedReason"] == "research_run_missing_evidence"
    assert handoff["researchRefs"] == []


def test_research_episode_plan_only_is_degraded_not_evidence_ready(monkeypatch):
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
        need={"kind": "research", "source": "test", "reason": "prepare research plan"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={"inputs": {"mode": "plan", "query": "先规划资料收集路径"}},
    )

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_research(episode))

    assert [item["mode"] for item in calls] == ["search_experience", "plan"]
    assert handoff["kind"] == "research_evidence_bundle"
    assert handoff["status"] == "degraded"
    assert handoff["runMode"] == "plan"
    assert handoff["researchRefs"] == []
    assert handoff["degradedReason"] == "research_plan_only_no_evidence"


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

    async def _fake_run_parallel_agent_branch(state, agent_data, progress_callback=None):
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


def test_delegation_episode_degrades_when_local_worker_fails(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "local subagent task"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workerBriefs": [
                    {
                        "id": "brief-local-fail",
                        "title": "Review one design risk",
                        "goal": "Review one design risk and return residual risk.",
                        "agentId": "review_worker",
                        "acceptanceTiers": {"must": ["Return a concrete finding."]},
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
                            "agentId": "review_worker",
                            "agentName": "Review Worker",
                            "delegationId": "delegation-local-fail-1",
                            "invocationId": "invoke-local-fail-1",
                            "taskBriefId": "brief-local-fail",
                            "reason": "Review one design risk",
                        },
                        "messages": [],
                        "todos": [],
                    },
                )
            ],
            update={},
        )

    async def _fake_run_parallel_agent_branch(state, agent_data, progress_callback=None):
        branch = state["parallel_branch"]
        return [], [], {
            "invocationId": branch["invocationId"],
            "taskBriefId": branch["taskBriefId"],
            "agentId": branch["agentId"],
            "agentName": branch["agentName"],
            "delegationId": branch["delegationId"],
            "targetLabel": branch["agentName"],
            "status": "failed",
            "error": "worker_model_error",
            "compactTranscript": "worker failed before producing a concrete finding",
        }, []

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
    assert stored["state"] == "degraded"
    payload = db.list_runtime_episode_handoffs(episode["episodeId"])[-1]["payload"]
    assert payload["kind"] == "delegation_degraded"
    assert payload["status"] == "degraded"
    assert payload["delegationState"] == "delegation_degraded"
    assert payload["degradedReason"] == "delegation_worker_failed"
    assert payload["failedDelegationCount"] == 1
    assert payload["acceptanceCheck"]["must"]["passed"] is False
    assert "narrow_contract" in payload["recoveryHints"]


def test_delegation_episode_degrades_when_all_workers_are_child_budget_blocked(monkeypatch):
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

    assert handoff["status"] == "degraded"
    assert handoff["kind"] == "delegation_degraded"
    assert handoff["delegationState"] == "delegation_degraded"
    assert handoff["degradedReason"] == "child_delegation_budget_boundary"
    assert handoff["failedDelegationCount"] == 0
    assert handoff["totalFailedDelegationCount"] == 1
    assert handoff["budgetBlockedChildDelegations"][0]["delegationId"] == "delegation-blocked-1"
    assert "child_budget_blocked=1" in handoff["compactSummary"]


def test_delegation_episode_returns_degraded_handoff_after_failure_threshold(monkeypatch):
    episode = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "three workers fail"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={
            "inputs": {
                "workerBriefs": [
                    {
                        "taskBriefId": f"brief-{index}",
                        "title": f"Worker {index}",
                        "goal": f"Worker {index} should inspect one area.",
                        "acceptanceTiers": {
                            "must": ["Return a concrete finding."],
                            "should": ["Include residual risks."],
                            "nice": ["Include timing metrics."],
                        },
                    }
                    for index in range(3)
                ],
                "targetCount": 3,
                "delegationCircuitBreakerThreshold": 3,
            }
        },
    )

    from core.native_tools import delegation_broker
    from langgraph.types import Command

    def _fake_dispatch(**kwargs):
        return Command(
            update={
                "parallel_results": [
                    {
                        "status": "failed",
                        "delegationId": f"delegation-failed-{index}",
                        "targetLabel": f"Worker {index}",
                        "error": "worker_failed",
                    }
                    for index in range(3)
                ]
            }
        )

    monkeypatch.setattr(delegation_broker, "func", _fake_dispatch)

    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_delegation(episode))

    assert handoff["kind"] == "delegation_degraded"
    assert handoff["status"] == "degraded"
    assert handoff["dispatchStatus"] == "delegation_degraded"
    assert handoff["failedDelegationCount"] == 3
    assert handoff["acceptanceCheck"]["must"]["items"] == ["Return a concrete finding."]
    assert "narrow_contract" in handoff["recoveryHints"]
    assert "direct" not in " ".join(handoff["recoveryHints"]).lower()


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

    async def _fake_run_parallel_agent_branch(state, agent_data, progress_callback=None):
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


def test_child_delegation_request_preserves_rich_task_brief_for_grandchild():
    from graph.parallel_support import _child_request_from_send_state

    task_brief = {
        "taskBriefId": "brief-child-rich",
        "goal": "调研绝区零角色玲的官方设定、来源和可用于 skill 写作的表达约束。",
        "brief": "返回 answer/sources/score/limitations/detailRef，而不是只返回任务 ID。",
        "runtimeAccess": ["research.core", "memory.read"],
        "acceptanceContract": "父 subagent 能直接基于证据继续写 SKILL.md。",
    }
    request = _child_request_from_send_state(
        {
            "parallel_branch": {
                "agentId": "web-research-architect",
                "agentName": "Web Research Architect",
                "delegationId": "delegation-child-rich",
                "invocationId": "invoke-child-rich",
                "taskBriefId": "brief-child-rich",
                "reason": "brief-child-rich",
                "taskBrief": task_brief,
            }
        },
        source_branch={
            "agentId": "skill-workflow-curator",
            "agentName": "Skill Workflow Curator",
            "delegationId": "delegation-parent-rich",
            "invocationId": "invoke-parent-rich",
            "allowChildDelegation": True,
        },
        source_agent_id="skill-workflow-curator",
    )

    assert request is not None
    assert request["childTaskBrief"] == task_brief
    assert request["childTaskBriefId"] == "brief-child-rich"
    assert request["childTaskGoal"] == task_brief["goal"]
    assert request["childTaskGoal"] != request["childTaskBriefId"]

    worker_brief, _child_branch = RuntimeEpisodeRunner._child_worker_brief_from_request(
        request,
        workspace_path="E:/Projects/test3",
    )
    assert worker_brief["taskBriefId"] == "brief-child-rich"
    assert "绝区零角色玲" in worker_brief["goal"]
    assert worker_brief["acceptanceContract"] == "父 subagent 能直接基于证据继续写 SKILL.md。"
    assert worker_brief["runtimeAccess"] == ["research.core", "memory.read"]
    assert worker_brief["workspacePath"] == "E:/Projects/test3"


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

    async def _fake_run_parallel_agent_branch(state, agent_data, progress_callback=None):
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
    assert payload["results"][-1]["taskBriefId"] == "brief-parent"
    assert payload["results"][-1]["supervisorAcceptance"]["status"] == "pending"
    assert "acceptanceHint" in payload["results"][-1]


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


def test_parallel_branch_can_continue_beyond_legacy_fixed_step_limit():
    from graph.parallel_support import _run_parallel_agent_branch
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    state = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "implementation_worker",
            "agentName": "Implementation Worker",
            "delegationId": "delegation-long-flow",
            "invocationId": "invoke-long-flow",
            "taskBriefId": "TASK-LONG",
            "reason": "Complete a long engineering flow with many observable steps.",
        },
    }
    call_count = 0

    def _node_func(_state):
        nonlocal call_count
        call_count += 1
        if call_count >= 45:
            return Command(goto="supervisor", update={"messages": [HumanMessage(content="long flow complete")]})
        return Command(
            goto="implementation_worker",
            update={"messages": [HumanMessage(content=f"progress step {call_count}")]},
        )

    _delta_messages, _delta_todos, summary, child_requests = asyncio.run(
        _run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"})
    )

    assert call_count == 45
    assert child_requests == []
    assert summary["status"] == "ok"
    assert summary["messageCount"] == 45


def test_parallel_branch_stops_repeated_no_progress_loop():
    from graph.parallel_support import _run_parallel_agent_branch
    from langgraph.types import Command

    state = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "stalled_worker",
            "agentName": "Stalled Worker",
            "delegationId": "delegation-stalled",
            "invocationId": "invoke-stalled",
            "taskBriefId": "TASK-STALLED",
            "reason": "Detect a repeated no-progress loop.",
        },
    }

    def _node_func(_state):
        return Command(goto="stalled_worker", update={})

    with pytest.raises(RuntimeError, match="连续重复同一执行状态"):
        asyncio.run(_run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"}))


def test_parallel_branch_stops_repeated_same_tool_purpose_loop():
    from graph.parallel_support import _run_parallel_agent_branch
    from langchain_core.messages import AIMessage
    from langgraph.types import Command

    state = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "looping_worker",
            "agentName": "Looping Worker",
            "delegationId": "delegation-looping-tool",
            "invocationId": "invoke-looping-tool",
            "taskBriefId": "TASK-LOOPING-TOOL",
            "reason": "Detect repeated same command purpose.",
        },
    }
    call_count = 0

    def _node_func(_state):
        nonlocal call_count
        call_count += 1
        return Command(
            goto="looping_worker",
            update={
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": f"call-repeat-{call_count}",
                                "name": "run_system_command",
                                "args": {"command": "cd project && python inspect_brief.py"},
                            }
                        ],
                    )
                ]
            },
        )

    with pytest.raises(RuntimeError, match="same tool purpose"):
        asyncio.run(_run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"}))


def test_parallel_branch_stops_semantic_artifact_stall_even_with_varied_messages(tmp_path):
    from graph.parallel_support import _run_parallel_agent_branch
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    state = {
        "messages": [],
        "todos": [],
        "workspace_path": str(tmp_path),
        "parallel_branch": {
            "agentId": "artifact_worker",
            "agentName": "Artifact Worker",
            "delegationId": "delegation-artifact-stall",
            "invocationId": "invoke-artifact-stall",
            "taskBriefId": "TASK-ARTIFACT",
            "reason": "Write the expected artifact.",
            "taskBrief": {
                "goal": "Create the expected file.",
                "writeRequired": True,
                "engineeringTaskCapsule": {
                    "writeRequired": True,
                    "expectedArtifacts": [".v8/demo/README.md"],
                },
            },
        },
    }
    counter = 0

    def _node_func(_state):
        nonlocal counter
        counter += 1
        return Command(
            goto="artifact_worker",
            update={"messages": [HumanMessage(content=f"still inspecting route {counter}")]},
        )

    with pytest.raises(RuntimeError, match="语义无进展循环"):
        asyncio.run(_run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"}))

    assert counter > 20


def test_parallel_branch_does_not_report_artifact_stall_after_expected_file_exists(tmp_path):
    from graph.parallel_support import _run_parallel_agent_branch, _runtime_context_from_parallel_state
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    expected_path = tmp_path / ".v8" / "demo" / "README.md"
    expected_path.parent.mkdir(parents=True)
    expected_path.write_text("ready\n", encoding="utf-8")
    state = {
        "messages": [],
        "todos": [],
        "workspace_path": str(tmp_path),
        "parallel_branch": {
            "agentId": "artifact_worker",
            "agentName": "Artifact Worker",
            "delegationId": "delegation-artifact-ready",
            "invocationId": "invoke-artifact-ready",
            "taskBriefId": "TASK-ARTIFACT-READY",
            "reason": "Verify the completed artifact.",
            "taskBrief": {
                "goal": "Create the expected file.",
                "writeSet": [".v8/demo/README.md"],
                "expectedOutputs": [".v8/demo/README.md"],
                "acceptanceContract": "The expected file exists and is readable.",
                "writeRequired": True,
                "engineeringTaskCapsule": {
                    "writeRequired": True,
                    "expectedArtifacts": [".v8/demo/README.md"],
                },
            },
        },
    }
    runtime_context = _runtime_context_from_parallel_state(state)
    assert runtime_context["allowed_write_paths"] == [".v8/demo/README.md"]
    counter = 0

    def _node_func(_state):
        nonlocal counter
        counter += 1
        return Command(
            goto="supervisor" if counter > 85 else "artifact_worker",
            update={"messages": [HumanMessage(content=f"checking completed artifact {counter}")]},
        )

    _messages, _todos, summary, _children = asyncio.run(
        _run_parallel_agent_branch(state, {"node_func": _node_func, "tool_mode": "test"})
    )

    assert counter > 80
    assert summary["status"] == "ok"
    assert summary["missingExpectedArtifacts"] == []


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
    assert normalized[0]["writeRequired"] is True
    assert "write_native_file" in normalized[0]["context"]["artifactWriteDiscipline"]
    assert "Empty placeholders" in normalized[0]["context"]["artifactAcceptanceGuard"]


def test_engineering_worker_briefs_for_artifact_include_write_discipline(tmp_path):
    from core.runtime_episode_runner import RuntimeEpisodeRunner

    worker_briefs = [
        {
            "taskBriefId": "TASK-010",
            "goal": "生成交付文档",
            "executionLaneHint": "engineering",
            "familyHint": "engineering",
            "deliverableKind": "artifact",
            "expectedOutputs": ["docs/delivery-summary.md"],
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"workspacePath": str(tmp_path)},
        inputs={"workspacePath": str(tmp_path)},
    )

    assert normalized[0]["executionLaneHint"] == "subagent"
    assert normalized[0]["writeRequired"] is True
    assert normalized[0]["context"]["expectedOutputs"] == ["docs/delivery-summary.md"]
    assert "write_native_file" in normalized[0]["context"]["artifactWriteDiscipline"]
    assert "shell commands are only for directories" in normalized[0]["context"]["artifactWriteDiscipline"]


def test_engineering_plan_only_handoff_synthesis_receives_no_lookup_tools(tmp_path):
    worker_briefs = [
        {
            "taskBriefId": "eng-plan-only",
            "goal": "Consume the injected research handoff and produce an engineering work plan without writing files.",
            "familyHint": "engineering",
            "readSet": ["research-mainchain-opt-001 evidence bundle (episode_abc123)"],
            "writeSet": [],
            "expectedOutputs": ["Human-readable engineering work plan"],
            "toolPolicy": {"mode": "default"},
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"writeRequired": False, "deliverableKind": "plan_only"},
        inputs={"workspacePath": str(tmp_path), "writeRequired": False, "deliverableKind": "plan_only"},
    )

    assert normalized[0]["toolPolicy"]["mode"] == "none"
    assert normalized[0]["allowedTools"] == []
    assert normalized[0]["readSet"] == []
    assert normalized[0]["context"]["injectedEvidenceRefs"] == [
        "research-mainchain-opt-001 evidence bundle (episode_abc123)"
    ]
    assert "not filesystem paths" in normalized[0]["context"]["handoffConsumptionDiscipline"]


def test_engineering_plan_only_with_declared_source_file_keeps_read_tools(tmp_path):
    worker_briefs = [
        {
            "taskBriefId": "eng-plan-source-read",
            "goal": "Inspect the declared source file and produce a read-only work plan.",
            "familyHint": "engineering",
            "readSet": ["src/app.ts"],
            "writeSet": [],
            "toolPolicy": {"mode": "default"},
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"writeRequired": False, "deliverableKind": "plan_only"},
        inputs={"workspacePath": str(tmp_path), "writeRequired": False, "deliverableKind": "plan_only"},
    )

    assert normalized[0]["toolPolicy"]["mode"] == "default"


def test_engineering_mixed_read_set_keeps_files_and_moves_symbolic_refs_to_context(tmp_path):
    worker_briefs = [
        {
            "taskBriefId": "eng-plan-mixed-readset",
            "goal": "Use the injected evidence and inspect the one declared source document.",
            "familyHint": "engineering",
            "readSet": [
                "research_evidence_bundle.summary",
                "capability_registry (engineering/research)",
                "docs/architecture.md",
            ],
            "writeSet": [],
            "toolPolicy": {"mode": "default"},
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"writeRequired": False, "deliverableKind": "plan_only"},
        inputs={"workspacePath": str(tmp_path), "writeRequired": False, "deliverableKind": "plan_only"},
    )

    assert normalized[0]["readSet"] == ["docs/architecture.md"]
    assert normalized[0]["context"]["injectedEvidenceRefs"] == [
        "research_evidence_bundle.summary",
        "capability_registry (engineering/research)",
    ]
    assert normalized[0]["toolPolicy"]["mode"] == "default"
    assert "never pass" in normalized[0]["context"]["handoffConsumptionDiscipline"]


def test_engineering_worker_briefs_do_not_treat_directory_setup_as_full_skill_artifact(tmp_path):
    from core.runtime_episode_runner import RuntimeEpisodeRunner

    skill_root = tmp_path / ".agents" / "skills" / "ling-perspective"
    worker_briefs = [
        {
            "taskBriefId": "TASK-000",
            "title": "目录初始化",
            "goal": "创建 ling-perspective skill 目录结构。",
            "executionLaneHint": "engineering",
            "familyHint": "engineering",
            "expectedOutputs": [
                str(skill_root / "scripts"),
                str(skill_root / "references" / "research"),
                str(skill_root / "references" / "sources"),
            ],
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"workspacePath": str(tmp_path), "validateSkillArtifact": True, "reason": "huashu-nuwa skill creation"},
        inputs={"workspacePath": str(tmp_path), "validateSkillArtifact": True},
    )

    assert normalized[0]["executionLaneHint"] == "subagent"
    assert normalized[0]["familyHint"] == "writing"
    assert normalized[0].get("writeRequired") is not True
    assert normalized[0].get("validateSkillArtifact") is not True
    assert "artifactWriteDiscipline" not in dict(normalized[0].get("context") or {})


def test_engineering_worker_briefs_mark_skill_md_build_as_validated_artifact(tmp_path):
    from core.runtime_episode_runner import RuntimeEpisodeRunner

    skill_root = tmp_path / ".agents" / "skills" / "ling-perspective"
    worker_briefs = [
        {
            "taskBriefId": "TASK-010",
            "title": "SKILL.md 组装构建",
            "goal": "写入完整 SKILL.md。",
            "executionLaneHint": "engineering",
            "familyHint": "engineering",
            "expectedOutputs": str(skill_root / "SKILL.md"),
        }
    ]

    normalized = RuntimeEpisodeRunner()._prepare_engineering_worker_briefs_for_delegation(
        worker_briefs,
        need={"workspacePath": str(tmp_path), "validateSkillArtifact": True, "reason": "huashu-nuwa skill creation"},
        inputs={"workspacePath": str(tmp_path), "validateSkillArtifact": True},
    )

    assert normalized[0]["writeRequired"] is True
    assert normalized[0]["validateSkillArtifact"] is True
    assert "write_native_file" in normalized[0]["context"]["artifactWriteDiscipline"]


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


def test_parallel_branch_directory_setup_does_not_require_full_skill_artifact(tmp_path):
    from graph.parallel_support import _infer_required_skill_artifacts

    skill_root = tmp_path / ".agents" / "skills" / "ling-perspective"
    branch = {
        "taskBriefId": "TASK-000",
        "reason": f"Create directories under {skill_root}.",
        "taskBrief": {
            "taskBriefId": "TASK-000",
            "title": "目录初始化",
            "goal": "创建目录结构，不写 SKILL.md 内容。",
            "context": {
                "expectedOutputs": [
                    str(skill_root / "scripts"),
                    str(skill_root / "references" / "research"),
                ],
            },
        },
    }

    assert _infer_required_skill_artifacts(branch, {"workspace_path": str(tmp_path)}) == []
