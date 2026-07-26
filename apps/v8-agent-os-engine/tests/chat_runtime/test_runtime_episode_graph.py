from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.database import db
from core.runtime_episodes import build_handoff_ref, build_runtime_episode
from graph import parallel_support
from graph.parallel_support import build_parallel_delegate_join_node
from graph.supervisor_context import resolve_supervisor_request_context
from graph.supervisor_turn import (
    _authoritative_runtime_route_guidance,
    _authoritative_runtime_route_kinds,
    _delegation_dispatch_contract_error,
    _explicit_runtime_orchestration_guidance,
    _explicit_runtime_orchestration_kinds,
    _merge_runtime_route_guidance_into_primary_system,
    _normalize_runtime_broker_response_arguments,
    _observed_runtime_episode_kinds,
    _pending_runtime_continuation_kinds,
    _required_orchestration_tool_name,
    _response_has_required_broker_attempt,
    _response_runtime_route_kinds,
    _runtime_route_correction_message,
    _runtime_handoff_continuation_message,
    _runtime_handoff_requires_continuation,
)
from graph.workflow_assembly import build_runtime_episode_wait_node


def test_supervisor_request_context_ignores_runtime_handoff_envelope() -> None:
    class ScopeResolver:
        @staticmethod
        def resolve(**_kwargs):
            return SimpleNamespace(
                binding=SimpleNamespace(resolved_scope="workspace:test"),
                scope_chain=["global", "workspace:test"],
            )

    context = resolve_supervisor_request_context(
        [
            HumanMessage(
                content="先调研，再进入编程模式，最后让子代理复核。",
                additional_kwargs={"session_id": "session-test", "workspace_path": "E:/workspace"},
            ),
            HumanMessage(
                content="[Runtime Episode Handoff Ready]\nresearch evidence ready",
                additional_kwargs={"v8_governance_type": "runtime_handoff"},
            ),
        ],
        ScopeResolver(),
    )

    assert context["user_query"] == "先调研，再进入编程模式，最后让子代理复核。"
    assert context["session_id"] == "session-test"
    assert context["current_scope"] == "workspace:test"


def test_runtime_handoff_continues_unfinished_runtime_todos_without_polling() -> None:
    state = {
        "todos": [
            {"_task_init": True, "name": "multi-runtime"},
            {"text": "启动深度调研 runtime", "status": "in_progress"},
            {"text": "进入编程模式 runtime 产出只读方案", "status": "pending"},
            {"text": "派子代理复核风险", "status": "pending"},
        ],
        "current_route_context": {
            "capabilityEpisodes": [{"episodeId": "episode-research", "kind": "research", "state": "completed"}],
            "handoffRefs": [{"kind": "research_evidence_bundle", "status": "ready"}],
        },
    }

    assert _pending_runtime_continuation_kinds(state) == ["engineering", "delegation"]
    assert _runtime_handoff_requires_continuation(state) is True
    message = _runtime_handoff_continuation_message(state)
    assert "engineering, delegation" in message.content
    assert "do not inspect" in message.content
    assert "canonical typed need contract" in message.content


def test_explicit_runtime_orchestration_uses_user_order_without_clarification() -> None:
    state = {
        "task_shape_hint": {
            "boundaryDecision": {
                "primaryRuntime": "engineering",
                "supportingRuntimes": ["research", "delegation"],
                "askUserNeeded": False,
            }
        }
    }
    kinds = _explicit_runtime_orchestration_kinds(
        state,
        "先做多源调研，再产出工程方案，并派一个子代理复核风险。",
    )

    assert kinds == ["research", "engineering", "delegation"]
    guidance = _explicit_runtime_orchestration_guidance(kinds)
    assert "askUserNeeded=false" in guidance.content
    assert "Do not invent clarification questions" in guidance.content
    assert "research -> engineering -> delegation" in guidance.content
    assert '"taskBriefs": [' in guidance.content
    assert '"dependency":' not in guidance.content
    assert "dependencies is plural" in guidance.content
    assert "never send need={}" in guidance.content
    assert "..." not in guidance.content


def test_explicit_runtime_orchestration_recognizes_engineering_execution_plan_wording() -> None:
    state = {
        "task_shape_hint": {
            "boundaryDecision": {
                "primaryRuntime": "engineering",
                "supportingRuntimes": ["research", "delegation"],
                "askUserNeeded": False,
            }
        }
    }

    assert _explicit_runtime_orchestration_kinds(
        state,
        "需要先做多源调研，再产出工程执行方案，并派一个子代理复核风险。",
    ) == ["research", "engineering", "delegation"]


def test_explicit_runtime_orchestration_honors_user_runtime_broker_denial() -> None:
    state = {
        "task_shape_hint": {
            "boundaryDecision": {
                "primaryRuntime": "engineering",
                "supportingRuntimes": ["delegation"],
                "askUserNeeded": False,
            }
        }
    }

    assert _explicit_runtime_orchestration_kinds(
        state,
        (
            "Supervisor 必须直接调用 delegation_broker，不要调用 runtime_broker。"
            "targetAgentName='Implementation Engineer'，familyHint='engineering'。"
        ),
    ) == []


def test_engineering_continuation_requires_fresh_engineering_route_until_episode_exists() -> None:
    state = {
        "task_shape_hint": {
            "primaryTaskShape": "project_coding",
            "engineeringContinuation": {
                "active": True,
                "previousEpisodeId": "episode-previous",
                "previousRunId": "run-previous",
            },
        },
        "current_route_context": {
            "engineeringRequired": True,
            "engineeringContinuation": {
                "active": True,
                "previousEpisodeId": "episode-previous",
            },
            "capabilityEpisodes": [],
        },
    }

    assert _authoritative_runtime_route_kinds(state) == ["engineering"]
    guidance = _authoritative_runtime_route_guidance(["engineering"])
    assert "authoritative continuation" in guidance.content
    assert "runtime_broker route call" in guidance.content
    assert '"taskBriefs": [' in guidance.content
    assert '"dependency":' not in guidance.content
    assert "dependencies is plural" in guidance.content
    assert "sibling top-level taskBrief" in guidance.content
    assert "never send need={}" in guidance.content
    assert "..." not in guidance.content

    state["current_route_context"]["capabilityEpisodes"] = [
        {"episodeId": "episode-current", "kind": "engineering", "state": "completed"}
    ]
    observed = _observed_runtime_episode_kinds(state)
    assert [kind for kind in _authoritative_runtime_route_kinds(state) if kind not in observed] == []


def test_validated_canvas_route_is_authoritative_and_keeps_exact_creative_contract() -> None:
    canvas_route = {
        "mode": "route",
        "routeKind": "creative_media",
        "routeReason": "validated canvas operation",
        "workspacePath": "E:/workspace",
        "taskBriefs": [{
            "taskBriefId": "canvas-creative-a1",
            "goal": "replace only the masked region",
            "context": {
                "canvasOperationId": "canvas-op-1",
                "canvasExecutionContract": {"schema": "v8.creative_canvas_task.v1"},
            },
            "writeRequired": True,
            "readOnly": False,
            "writeSet": [".v8/creative-media/"],
            "expectedOutputs": ["one image artifact"],
            "acceptanceContract": ["artifact keeps current-session lineage"],
            "constraints": ["do not expose the internal mask"],
            "detailRefs": [],
            "dependencies": [],
        }],
        "proofExpectations": ["artifact id and preview reference"],
    }
    state = {
        "current_route_context": {
            "canvasSupervisorDirect": True,
            "canvasRuntimeRoute": canvas_route,
            "engineeringRequired": True,
        }
    }

    assert _authoritative_runtime_route_kinds(state) == ["creative_media"]
    guidance = _authoritative_runtime_route_guidance(["creative_media"], state=state)
    assert "server-validated Canvas execution contract" in guidance.content
    assert "do not route it through Engineering" in guidance.content
    assert "Runtime Surface control facts" in guidance.content
    assert "Never quote or enumerate" in guidance.content
    assert '"canvasOperationId": "canvas-op-1"' in guidance.content
    assert '"workspacePath": "E:/workspace"' in guidance.content
    assert '"writeSet": [' in guidance.content
    assert "<stable task id>" not in guidance.content

    correction = _runtime_route_correction_message(
        ["creative_media"],
        authoritative=True,
        state=state,
    )
    assert '"canvasOperationId": "canvas-op-1"' in correction.content
    assert "single correction attempt" in correction.content


def test_unvalidated_canvas_route_context_does_not_override_engineering_requirement() -> None:
    state = {
        "current_route_context": {
            "canvasSupervisorDirect": False,
            "canvasRuntimeRoute": {"routeKind": "creative_media"},
            "engineeringRequired": True,
        }
    }

    assert _authoritative_runtime_route_kinds(state) == ["engineering"]


def test_required_runtime_guidance_stays_in_primary_system_message() -> None:
    messages = [
        SystemMessage(content="base supervisor contract"),
        HumanMessage(content="use Engineering runtime"),
    ]

    merged = _merge_runtime_route_guidance_into_primary_system(
        messages,
        _authoritative_runtime_route_guidance(["engineering"]),
    )

    assert len(merged) == 2
    assert isinstance(merged[0], SystemMessage)
    assert "base supervisor contract" in merged[0].content
    assert "Required Runtime Route" in merged[0].content
    assert merged[0].additional_kwargs["v8_runtime_route_guidance"] is True
    assert isinstance(merged[1], HumanMessage)


def test_required_runtime_correction_is_transient_human_turn_after_model_response() -> None:
    messages = [
        SystemMessage(content="base supervisor contract"),
        HumanMessage(content="use Engineering runtime"),
        AIMessage(content="I will run a shell command directly."),
        _runtime_route_correction_message(["engineering"], authoritative=True),
    ]

    assert isinstance(messages[-1], HumanMessage)
    assert messages[-1].additional_kwargs["v8_governance_type"] == "runtime_route_correction"
    assert "single correction attempt" in messages[-1].content
    assert "runtime_broker route call" in messages[-1].content
    assert not any(isinstance(message, SystemMessage) for message in messages[1:])


def test_explicit_engineering_runtime_request_is_authoritative_without_rebinding_work_mode() -> None:
    explicit_state = {
        "current_route_context": {
            "engineeringRequired": True,
            "engineeringMode": "force",
            "capabilityEpisodes": [],
        }
    }
    work_mode_state = {
        "current_route_context": {
            "engineeringRequired": False,
            "engineeringMode": "force",
            "capabilityEpisodes": [],
        }
    }

    assert _authoritative_runtime_route_kinds(explicit_state) == ["engineering"]
    assert _authoritative_runtime_route_kinds(work_mode_state) == []


def test_response_runtime_route_kinds_reads_runtime_and_delegation_calls() -> None:
    response = SimpleNamespace(
        tool_calls=[
            {"name": "runtime_broker", "args": {"mode": "route", "need": {"kind": "research"}}},
            {"name": "delegation_broker", "args": {"mode": "dispatch", "tasks": [{"goal": "review"}]}},
        ],
        additional_kwargs={},
    )

    assert _response_runtime_route_kinds(response) == ["research", "delegation"]


def test_runtime_route_kind_normalizes_json_encoded_need_before_execution() -> None:
    response = SimpleNamespace(
        tool_calls=[
            {
                "name": "runtime_broker",
                "args": {
                    "mode": "route",
                    "need": '{"kind":"research","source":"supervisor","reason":"evidence"}',
                },
            }
        ],
        additional_kwargs={},
    )

    normalized = _normalize_runtime_broker_response_arguments(response)

    assert normalized.tool_calls[0]["args"]["need"]["kind"] == "research"
    assert _response_runtime_route_kinds(normalized) == ["research"]


def test_runtime_route_kind_normalizes_literal_encoded_need_before_execution() -> None:
    response = SimpleNamespace(
        tool_calls=[
            {
                "name": "runtime_broker",
                "args": {
                    "mode": "route",
                    "need": "{'kind':'engineering','source':'supervisor','reason':'plan'}",
                },
            }
        ],
        additional_kwargs={},
    )

    normalized = _normalize_runtime_broker_response_arguments(response)

    assert normalized.tool_calls[0]["args"]["need"]["kind"] == "engineering"
    assert _response_runtime_route_kinds(normalized) == ["engineering"]


def test_delegation_arguments_normalize_wrapped_json_and_drop_optional_nulls() -> None:
    response = SimpleNamespace(
        tool_calls=[
            {
                "name": "delegation_broker",
                "args": {
                    "mode": "dispatch",
                    "tasks": '{"tasks":[{"taskBriefId":"review-1","goal":"Review evidence",'
                    '"expectedOutput":"Concise verdict","acceptance":"Cite both handoffs",'
                    '"executionLaneHint":null,"context":{"dependencyResults":[{"status":"ready"}]}}]}',
                },
            }
        ],
        additional_kwargs={},
    )

    normalized = _normalize_runtime_broker_response_arguments(response)
    task = normalized.tool_calls[0]["args"]["tasks"][0]

    assert task["expectedOutputs"] == ["Concise verdict"]
    assert task["acceptanceContract"] == "Cite both handoffs"
    assert "executionLaneHint" not in task
    assert task["context"]["dependencyResults"][0]["status"] == "ready"


def test_delegation_contract_validator_rejects_missing_outputs_and_acceptance() -> None:
    response = SimpleNamespace(
        tool_calls=[
            {
                "name": "delegation_broker",
                "args": {
                    "mode": "dispatch",
                    "tasks": [{"taskBriefId": "review-1", "goal": "Review evidence"}],
                },
            }
        ]
    )

    assert _delegation_dispatch_contract_error(response) == (
        "delegation_dispatch_contract_missing:task[1].expectedOutputs,acceptanceContract"
    )


def test_delegation_contract_validator_leaves_present_wrong_types_to_tool_schema() -> None:
    response = SimpleNamespace(
        tool_calls=[
            {
                "name": "delegation_broker",
                "args": {
                    "mode": "dispatch",
                    "tasks": [
                        {
                            "taskBriefId": "review-1",
                            "goal": "Review evidence",
                            "expectedOutputs": "wrong-but-present",
                            "acceptanceContract": 42,
                        }
                    ],
                },
            }
        ]
    )

    assert _delegation_dispatch_contract_error(response) is None


def test_explicit_orchestration_forces_the_only_valid_broker_for_the_next_step() -> None:
    assert _required_orchestration_tool_name("research") == "runtime_broker"
    assert _required_orchestration_tool_name("engineering") == "runtime_broker"
    assert _required_orchestration_tool_name("delegation") == "delegation_broker"


def test_required_broker_attempt_leaves_argument_validation_to_typed_tool_boundary() -> None:
    runtime_response = SimpleNamespace(
        tool_calls=[{"name": "runtime_broker", "args": {"mode": "route", "need": None}}]
    )
    delegation_response = SimpleNamespace(
        tool_calls=[{"name": "delegation_broker", "args": {"mode": "dispatch", "tasks": None}}]
    )
    wrong_mode_response = SimpleNamespace(
        tool_calls=[{"name": "runtime_broker", "args": {"mode": "list", "need": None}}]
    )

    assert _response_has_required_broker_attempt(runtime_response, "runtime_broker") is True
    assert _response_has_required_broker_attempt(delegation_response, "delegation_broker") is True
    assert _response_has_required_broker_attempt(wrong_mode_response, "runtime_broker") is False


def test_runtime_episode_wait_node_merges_completed_handoff() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_ready_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "research", "reason": "need evidence"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="research",
        compact_summary="Research evidence bundle ready.",
        status="ready",
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    assert command.goto == "supervisor"
    refs = command.update["current_route_context"]["handoffRefs"]
    assert any(item.get("handoffRefId") == handoff["handoffRefId"] for item in refs)
    assert command.update["runtime_dispatch_status"]["state"] == "handoff_ready"


def test_runtime_episode_wait_node_keeps_direct_creative_refs_in_runtime_surface() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_creative_refs_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "creative_media", "reason": "execute exact Canvas edit"},
        kind="creative_media",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="creative_media",
        compact_summary="The exact Canvas edit produced art_creative_ready at E:/private/output.png.",
        status="ready",
        consumer_hint="Consume creative-media-job://cm_creative_ready from E:/private/output.png.",
        extra={
            "artifactRefs": ["art_creative_ready"],
            "proofRefs": ["creative-media-job://cm_creative_ready"],
            "taskBriefResults": [
                {
                    "taskBriefId": "task-private-canvas",
                    "result": "Saved art_creative_ready to E:/private/output.png.",
                    "artifactRefs": ["art_creative_ready"],
                    "verificationResults": [{"status": "verified"}],
                }
            ],
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    message = command.update["messages"][0]
    content = str(message.content)
    assert "governed Creative Media evidence is ready: artifacts=1, proofRefs=1" in content
    assert "art_creative_ready" not in content
    assert "creative-media-job://cm_creative_ready" not in content
    assert "E:/private/output.png" not in content
    assert "task-private-canvas" not in content
    assert message.additional_kwargs["v8_runtime_handoffs"][0]["refs"] == ["art_creative_ready"]
    assert message.additional_kwargs["v8_runtime_handoffs"][0]["proofRefs"] == [
        "creative-media-job://cm_creative_ready"
    ]
    assert "Governed Creative Media execution evidence is available" in content
    assert "execution results remain structured Runtime Surface evidence: results=1; semanticallyVerified=1" in content


def test_runtime_episode_wait_node_preserves_terminal_brief_coverage_for_supervisor() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_brief_coverage_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "research", "reason": "cover three questions"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="research",
        compact_summary="Research bundle ready.",
        status="ready",
        extra={
            "taskBriefIds": ["fts5", "jsonb", "python-win"],
            "taskBriefCount": 3,
            "sourceCount": 4,
            "limitations": ["One version remains provisional."],
            "terminalEpisode": True,
            "remainingHandoffsExpected": 0,
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    message = command.update["messages"][0]
    projected = message.additional_kwargs["v8_runtime_handoffs"][0]
    assert projected["taskBriefIds"] == ["fts5", "jsonb", "python-win"]
    assert projected["taskBriefCount"] == 3
    assert projected["remainingHandoffsExpected"] == 0
    assert "no further handoffs will arrive" in str(message.content)
    assert "fts5, jsonb, python-win" in str(message.content)


def test_runtime_episode_wait_node_projects_exact_research_gap_for_bounded_retry() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_research_gap_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "research", "reason": "verify official contracts"},
        kind="research",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="research",
        compact_summary="Two briefs are supported; one still lacks official evidence.",
        status="degraded",
        extra={
            "detailRef": "research://bundle/research-gap",
            "taskBriefIds": ["fts5", "jsonb", "python-win"],
            "coveredTaskBriefIds": ["fts5", "python-win"],
            "missingTaskBriefIds": ["jsonb"],
            "claimBlockers": ["jsonb"],
            "evidenceGaps": [
                {
                    "taskBriefId": "jsonb",
                    "status": "unverified",
                    "blocksClaim": True,
                    "blocksDownstream": False,
                    "limitations": ["Official SQLite JSONB boundary was not established."],
                    "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
                }
            ],
            "downstreamAllowed": True,
            "continuationPolicy": {
                "retryLimit": 1,
                "retryExhaustedAction": "continue_with_explicit_evidence_gaps",
            },
            "coverageComplete": False,
            "recommendedNextAction": "retry_missing_research_briefs",
            "taskBriefResults": [
                {
                    "taskBriefId": "jsonb",
                    "status": "degraded",
                    "answer": "Only one secondary source was found; the official release boundary remains unresolved.",
                    "evidenceBundleId": "jsonb-partial",
                    "researchRef": "research://bundle/jsonb-partial",
                    "detailTool": "research_broker(mode='get_evidence', evidenceBundleId='jsonb-partial')",
                    "sourceUrls": ["https://example.test/jsonb"],
                    "sourceCount": 1,
                    "claimCount": 2,
                    "limitations": ["Official SQLite JSONB boundary was not established."],
                    "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
                }
            ],
            "terminalEpisode": True,
            "remainingHandoffsExpected": 0,
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="degraded", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    assert command.goto == "supervisor"
    assert command.update["runtime_dispatch_status"]["state"] == "degraded_handoff_ready"
    message = command.update["messages"][0]
    projected = message.additional_kwargs["v8_runtime_handoffs"][0]
    assert projected["coveredTaskBriefIds"] == ["fts5", "python-win"]
    assert projected["missingTaskBriefIds"] == ["jsonb"]
    assert projected["claimBlockers"] == ["jsonb"]
    assert projected["evidenceGaps"][0]["taskBriefId"] == "jsonb"
    assert projected["evidenceGaps"][0]["blocksClaim"] is True
    assert projected["evidenceGaps"][0]["blocksDownstream"] is False
    assert projected["downstreamAllowed"] is True
    assert projected["continuationPolicy"]["retryExhaustedAction"] == "continue_with_explicit_evidence_gaps"
    assert projected["coverageComplete"] is False
    assert projected["detailRef"] == ""
    assert projected["results"][0]["result"].startswith("Only one secondary source")
    assert projected["results"][0]["sourceUrls"] == ["https://example.test/jsonb"]
    assert projected["results"][0]["detailTool"] == (
        "research_broker(mode='get_evidence', evidenceBundleId='jsonb-partial')"
    )
    assert projected["results"][0]["evidenceStatusReasons"] == ["explicit_critical_evidence_gap"]
    assert "covered=fts5, python-win; missing=jsonb; complete=False" in str(message.content)
    assert "retry only the missing brief IDs once" in str(message.content)
    assert "explicit_critical_evidence_gap" in str(message.content)
    assert "research:// is evidence lineage, not a toolobs:// rawRef" in str(message.content)
    assert "never pass research:// to tool_observation_detail" in str(message.content)


def test_runtime_episode_wait_node_projects_nested_delegation_proof_without_loss() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_proof_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "engineering", "reason": "write and verify artifact"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="engineering_patch_bundle",
        compact_summary="Engineering execution completed.",
        status="ready",
        extra={
            "delegationHandoff": {
                "status": "ready",
                "results": [
                    {
                        "taskBriefId": "write-result",
                        "targetLabel": "Implementation Engineer",
                        "status": "ok",
                        "artifactRefs": [{"path": "result.txt", "kind": "workspace_artifact"}],
                        "resultText": (
                            "byte_length=26; sha256="
                            "2b6be405b49da69a63f3b451be6f9fc98b3f542ddb816a0d36f506e5aaa4c84b; "
                            "bom_detected=false"
                        ),
                    }
                ],
            }
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    message = command.update["messages"][0]
    content = str(message.content)
    projected = message.additional_kwargs["v8_runtime_handoffs"][0]["results"][0]
    assert "byte_length=26" in content
    assert "sha256=2b6be405" in content
    assert "evidence: complete" in content
    assert projected["evidenceComplete"] is True


def test_runtime_episode_wait_node_quarantines_rejected_worker_success_claim() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_rejected_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "engineering", "reason": "write a governed baseline"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="engineering_degraded",
        compact_summary="All baseline files landed and verification passed.",
        status="degraded",
        extra={
            "delegationHandoff": {
                "status": "failed",
                "results": [
                    {
                        "taskBriefId": "baseline-implementation",
                        "targetLabel": "Implementation Engineer",
                        "status": "error",
                        "error": "managed_worktree_finalize_failed:worktree_write_set_violation",
                        "workerReportedSummary": "All baseline files landed and verification passed.",
                        "artifactRefs": [
                            {"path": "baseline/manifest.json", "kind": "workspace_artifact"}
                        ],
                        "artifactRefsAccepted": False,
                        "sandboxEvidence": {
                            "state": "failed",
                            "candidateState": "quarantined_unmerged",
                            "errorCode": "worktree_write_set_violation",
                            "violations": ["baseline/.tmp/probe.json"],
                            "writeSet": ["baseline/manifest.json"],
                            "repairAction": "Repair the task contract and route one bounded retry.",
                        },
                        "verificationEvidence": {"passed": True},
                    }
                ],
            },
            "terminalEpisode": True,
            "remainingHandoffsExpected": 0,
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="degraded", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    message = command.update["messages"][0]
    projected_handoff = message.additional_kwargs["v8_runtime_handoffs"][0]
    projected = projected_handoff["results"][0]
    assert "All baseline files landed" not in projected_handoff["summary"]
    assert projected["result"] == "managed_worktree_finalize_failed:worktree_write_set_violation"
    assert projected["artifactRefsAccepted"] is False
    assert projected["artifactRefs"][0]["accepted"] is False
    assert projected["artifactRefs"][0]["state"] == "quarantined_unmerged"
    assert projected["proofRefs"] == []
    assert projected["verificationPassed"] is False
    assert projected["evidenceComplete"] is False
    assert projected["workerReport"].startswith("All baseline files landed")
    assert projected["sandboxEvidence"]["violations"] == ["baseline/.tmp/probe.json"]
    assert "quarantined candidates (unmerged)" in str(message.content)
    assert "All baseline files landed and verification passed" not in str(message.content)


def test_runtime_episode_wait_node_projects_recursive_grandchild_verification_truth() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_recursive_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "engineering", "reason": "write and verify recursively"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="engineering_patch_bundle",
        compact_summary="Engineering execution completed with child verification.",
        status="ready",
        extra={
            "childHandoffs": [
                {
                    "status": "ready",
                    "childHandoffs": [
                        {
                            "status": "ready",
                            "results": [
                                {
                                    "taskBriefId": "verify-result",
                                    "delegationId": "delegation-grandchild",
                                    "parentDelegationId": "delegation-parent",
                                    "delegationDepth": 2,
                                    "targetLabel": "Implementation Engineer · worker-01",
                                    "status": "ok",
                                    "toolsUsed": ["read_native_file", "run_system_command"],
                                    "verificationEvidence": {
                                        "passed": True,
                                        "observations": [
                                            {"tool": "read_native_file", "path": "src/result.py"},
                                            {
                                                "tool": "run_system_command",
                                                "command": "python src/result.py",
                                                "returnCode": 0,
                                                "stdout": "exact-proof",
                                                "stderr": "",
                                            },
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    message = command.update["messages"][0]
    content = str(message.content)
    projected = message.additional_kwargs["v8_runtime_handoffs"][0]["results"][0]
    assert "python src/result.py" in content
    assert "stdout='exact-proof'" in content
    assert "depth=2" in content
    assert projected["parentDelegationId"] == "delegation-parent"
    assert projected["verificationPassed"] is True
    assert projected["evidenceComplete"] is True


def test_runtime_episode_wait_node_projects_computer_use_task_brief_proof() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_computer_use_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "computer_use", "reason": "download and close browser"},
        kind="computer_use",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    artifact_ref = "workspace:computer-use-acceptance/image.jpg"
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="computer_observation_bundle",
        compact_summary="Computer Use completed one governed task.",
        status="ready",
        extra={
            "taskBriefResults": [
                {
                    "taskBriefId": "download-image",
                    "status": "completed",
                    "summary": "Downloaded a content image and closed Agent Browser.",
                    "artifactRefs": [artifact_ref],
                    "proofRefs": ["workspace:.v8-agent-os/proof/frame-04.jpg"],
                    "verification": {
                        "passed": True,
                        "missing": [],
                        "files": [
                            {
                                "workspacePath": "computer-use-acceptance/image.jpg",
                                "mime": "image/jpeg",
                                "magic": "FFD8FFE000104A46",
                                "sha256": "27b9a8cc870f3fa7c143c898a35405d448624df99bed32facf50bed0208e4360",
                            }
                        ],
                        "browserClosed": True,
                        "applicationClosed": False,
                    },
                }
            ]
        },
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    message = command.update["messages"][0]
    content = str(message.content)
    projected = message.additional_kwargs["v8_runtime_handoffs"][0]["results"][0]
    assert artifact_ref in content
    assert "magic=FFD8FFE000104A46" in content
    assert "browserClosed=True" in content
    assert "evidence: complete" in content
    assert projected["verificationPassed"] is True
    assert projected["evidenceComplete"] is True


def test_runtime_episode_wait_node_reports_failed_handoff_as_recoverable_failure() -> None:
    node = build_runtime_episode_wait_node()
    episode_id = f"episode_wait_failed_{uuid4().hex}"
    episode = build_runtime_episode(
        need={"episodeId": episode_id, "kind": "engineering", "reason": "create artifact"},
        kind="engineering",
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
    handoff = build_handoff_ref(
        producer_episode_id=episode_id,
        kind="engineering",
        compact_summary="Delegated artifact creation failed acceptance.",
        status="failed",
        extra={"errorCode": "artifact_acceptance_failed"},
    )
    db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
    db.complete_runtime_episode(
        episode_id,
        state="failed",
        result_ref=handoff["handoffRefId"],
        error_code="artifact_acceptance_failed",
        error_message="Required output was missing.",
    )

    command = asyncio.run(node({"current_route_context": {"capabilityEpisodes": [episode]}}))

    assert command.goto == "supervisor"
    status = command.update["runtime_dispatch_status"]
    assert status["nextAction"] == "recoverable_failure"
    assert status["state"] == "episode_failed"
    assert status["failedHandoffCount"] == 1


def test_runtime_episode_wait_node_uses_proven_retry_instead_of_stale_failure() -> None:
    node = build_runtime_episode_wait_node()
    run_id = f"run_wait_retry_{uuid4().hex}"
    session_id = f"session_wait_retry_{uuid4().hex}"
    failed_id = f"episode_wait_old_failure_{uuid4().hex}"
    proven_id = f"episode_wait_proven_retry_{uuid4().hex}"
    task_inputs = {
        "workspacePath": "E:/workspace/retry-proof",
        "taskBriefs": [
            {
                "taskBriefId": "repair-result",
                "goal": "Repair result.py.",
                "writeRequired": True,
                "writeSet": ["src/result.py"],
            }
        ],
    }
    db.create_or_update_session(session_id=session_id, title="Runtime retry proof", user_id="test")
    db.create_run_record(run_id=run_id, session_id=session_id, run_type="chat", status="running")
    failed = build_runtime_episode(
        need={"episodeId": failed_id, "runId": run_id, "kind": "delegation", "inputs": task_inputs},
        kind="delegation",
        state="queued",
    )
    db.upsert_runtime_episode_record(failed, session_id=session_id, run_id=run_id, enqueue=True, priority=999)
    failed_handoff = build_handoff_ref(
        producer_episode_id=failed_id,
        kind="subagent_result_bundle",
        compact_summary="First attempt failed.",
        status="failed",
        extra={"errorCode": "first_attempt_failed"},
    )
    db.add_runtime_episode_handoff(episode_id=failed_id, session_id=session_id, run_id=run_id, handoff=failed_handoff)
    db.complete_runtime_episode(
        failed_id,
        state="failed",
        result_ref=failed_handoff["handoffRefId"],
        error_code="first_attempt_failed",
    )

    proven = build_runtime_episode(
        need={"episodeId": proven_id, "runId": run_id, "kind": "engineering", "inputs": task_inputs},
        kind="engineering",
        state="queued",
    )
    db.upsert_runtime_episode_record(proven, session_id=session_id, run_id=run_id, enqueue=True, priority=999)
    proven_handoff = build_handoff_ref(
        producer_episode_id=proven_id,
        kind="engineering_patch_bundle",
        compact_summary="Retry produced the requested file and independent verification.",
        status="ready",
        extra={
            "changedFiles": ["src/result.py"],
            "verificationResults": [
                {
                    "status": "verified",
                    "passed": True,
                    "observations": [
                        {
                            "command": "python src/result.py",
                            "returnCode": 0,
                            "stdout": "exact-proof",
                        }
                    ],
                }
            ],
        },
    )
    db.add_runtime_episode_handoff(episode_id=proven_id, session_id=session_id, run_id=run_id, handoff=proven_handoff)
    db.complete_runtime_episode(proven_id, state="completed", result_ref=proven_handoff["handoffRefId"])

    command = asyncio.run(
        node(
            {
                "run_id": run_id,
                "session_id": session_id,
                "current_route_context": {
                    "runId": run_id,
                    "capabilityEpisodes": [failed, proven],
                },
            }
        )
    )

    status = command.update["runtime_dispatch_status"]
    assert command.goto == "supervisor"
    assert status["nextAction"] == "resume_supervisor"
    assert status["state"] == "handoff_ready"
    assert command.update["current_route_context"]["supersededRuntimeEpisodeIds"] == [failed_id]
    assert "Retry produced the requested file" in str(command.update["messages"][0].content)


def test_runtime_episode_wait_node_resumes_when_only_optional_lane_failed() -> None:
    node = build_runtime_episode_wait_node()
    research_id = f"episode_wait_research_{uuid4().hex}"
    delegation_id = f"episode_wait_optional_{uuid4().hex}"
    research = build_runtime_episode(
        need={"episodeId": research_id, "kind": "research", "reason": "need evidence"},
        kind="research",
        state="completed",
        continuation_target="runtime_episode_runner",
    )
    optional_delegation = build_runtime_episode(
        need={"episodeId": delegation_id, "kind": "delegation", "reason": "optional review"},
        kind="delegation",
        state="queued",
        continuation_target="runtime_episode_runner",
        extra={"optional": True, "dependencyMode": "optional"},
    )
    db.upsert_runtime_episode_record(research, enqueue=False)
    db.upsert_runtime_episode_record(optional_delegation, enqueue=False)
    research_handoff = build_handoff_ref(
        producer_episode_id=research_id,
        kind="research",
        compact_summary="Research evidence bundle ready.",
        status="ready",
    )
    delegation_handoff = build_handoff_ref(
        producer_episode_id=delegation_id,
        kind="delegation",
        compact_summary="Optional subagent review failed.",
        status="failed",
        extra={"errorCode": "optional_subagent_failed"},
    )
    db.add_runtime_episode_handoff(episode_id=research_id, handoff=research_handoff)
    db.complete_runtime_episode(research_id, state="completed", result_ref=research_handoff["handoffRefId"])
    db.add_runtime_episode_handoff(episode_id=delegation_id, handoff=delegation_handoff)
    db.complete_runtime_episode(
        delegation_id,
        state="failed",
        result_ref=delegation_handoff["handoffRefId"],
        error_code="optional_subagent_failed",
        error_message="Optional lane failed.",
    )

    command = asyncio.run(
        node({"current_route_context": {"capabilityEpisodes": [research, optional_delegation]}})
    )

    status = command.update["runtime_dispatch_status"]
    assert command.goto == "supervisor"
    assert status["nextAction"] == "resume_supervisor"
    assert status["state"] == "degraded_handoff_ready"
    assert status["degradedEpisodeCount"] == 1


def test_runtime_episode_wait_node_does_not_resume_on_partial_handoff() -> None:
    node = build_runtime_episode_wait_node()
    research_id = f"episode_wait_partial_research_{uuid4().hex}"
    engineering_id = f"episode_wait_partial_engineering_{uuid4().hex}"
    research = build_runtime_episode(
        need={"episodeId": research_id, "kind": "research", "reason": "need evidence"},
        kind="research",
        state="completed",
        continuation_target="runtime_episode_runner",
    )
    engineering = build_runtime_episode(
        need={"episodeId": engineering_id, "kind": "engineering", "reason": "implementation still running"},
        kind="engineering",
        state="active",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(research, enqueue=False)
    db.upsert_runtime_episode_record(engineering, enqueue=False)
    handoff = build_handoff_ref(
        producer_episode_id=research_id,
        kind="research",
        compact_summary="Research evidence bundle ready.",
        status="ready",
    )
    db.add_runtime_episode_handoff(episode_id=research_id, handoff=handoff)
    db.complete_runtime_episode(research_id, state="completed", result_ref=handoff["handoffRefId"])

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            asyncio.wait_for(
                node({"current_route_context": {"capabilityEpisodes": [research, engineering]}}),
                timeout=0.2,
            )
        )


def test_parallel_join_routes_pending_child_delegations_from_top_level() -> None:
    join_node = build_parallel_delegate_join_node()
    child_state = {
        "messages": [],
        "parallel_branch": {
            "invocationId": "delegation_child",
            "branchIndex": 0,
            "agentId": "child-agent",
            "agentName": "Child Agent",
            "reason": "Review one isolated file",
            "taskBriefId": "task-child",
            "delegationId": "subagent::child",
            "parentDelegationId": "subagent::parent",
            "delegationDepth": 2,
            "lane": "subagent",
        },
    }

    command = join_node(
        {
            "parallel_invocations": [{"invocationId": "delegation_parent", "expected": 1}],
            "parallel_results": [
                {
                    "invocationId": "delegation_parent",
                    "status": "waiting_child_delegation",
                    "childDelegationRequestIds": ["child_req"],
                }
            ],
            "pending_child_delegations": [
                {
                    "requestId": "child_req",
                    "sourceInvocationId": "delegation_parent",
                    "sourceDelegationId": "subagent::parent",
                    "send": {"node": "parallel_delegate_task", "arg": child_state},
                }
            ],
        }
    )

    assert command.goto == "runtime_episode"
    assert "child_req" in command.update["routed_child_delegation_request_ids"]
    route_context = command.update["current_route_context"]
    child_episode = route_context["capabilityEpisodes"][-1]
    assert child_episode["kind"] == "delegation"
    assert child_episode["state"] == "queued"
    assert child_episode["parentEpisodeId"] == "subagent::parent"
    pending_message = command.update["messages"][0]
    assert pending_message.additional_kwargs["v8_governance_type"] == "delegation_child_pending"
    assert "不是可验收结果" in pending_message.content
    assert "不得根据任务说明猜测" in pending_message.content


def test_parallel_join_reuses_broker_persisted_child_episode() -> None:
    join_node = build_parallel_delegate_join_node()
    suffix = uuid4().hex[:10]
    parent_id = f"subagent::parent::{suffix}"
    child_id = f"subagent::child::{suffix}"
    parent = build_runtime_episode(
        need={"kind": "delegation", "source": "test", "reason": "parent"},
        kind="delegation",
        state="waiting_child",
        continuation_target="runtime_episode_runner",
        extra={"episodeId": parent_id, "needId": parent_id},
    )
    db.upsert_runtime_episode_record(parent, enqueue=False)
    existing = build_runtime_episode(
        need={
            "kind": "delegation",
            "source": "delegation_broker",
            "reason": "Read README.md independently.",
            "parentEpisodeId": parent_id,
            "inputs": {"workerBriefs": [{"taskBriefId": "task-child", "goal": "Read README.md."}]},
        },
        kind="delegation",
        state="waiting",
        parent_episode_id=parent_id,
        continuation_target="parallel_delegate_join",
        extra={"episodeId": child_id, "needId": child_id},
    )
    db.upsert_runtime_episode_record(existing, enqueue=False)
    child_state = {
        "messages": [],
        "parallel_branch": {
            "invocationId": f"delegation_child_{suffix}",
            "branchIndex": 0,
            "agentId": "child-agent",
            "agentName": "Child Agent",
            "reason": "Read README.md independently.",
            "taskBriefId": "task-child",
            "delegationId": child_id,
            "parentDelegationId": parent_id,
            "delegationDepth": 2,
            "lane": "subagent",
        },
    }

    command = join_node(
        {
            "parallel_invocations": [{"invocationId": f"delegation_parent_{suffix}", "expected": 1}],
            "parallel_results": [
                {
                    "invocationId": f"delegation_parent_{suffix}",
                    "status": "waiting_child_delegation",
                    "childDelegationRequestIds": [f"child_req_{suffix}"],
                }
            ],
            "pending_child_delegations": [
                {
                    "requestId": f"child_req_{suffix}",
                    "sourceInvocationId": f"delegation_parent_{suffix}",
                    "sourceDelegationId": parent_id,
                    "childDelegationId": child_id,
                    "send": {"node": "parallel_delegate_task", "arg": child_state},
                }
            ],
        }
    )

    assert command.goto == "runtime_episode"
    child_episode_ids = command.update["current_route_context"]["lastChildDelegationRouted"]["childEpisodeIds"]
    assert child_episode_ids == [child_id]
    children = db.list_runtime_episodes(parent_episode_id=parent_id, limit=20)
    assert [item["episodeId"] for item in children if item["episodeId"] == child_id] == [child_id]
    assert not any(item["source"] == "subagent" and item["episodeId"] != child_id for item in children)
    stored_parent = db.get_runtime_episode(parent_id)
    assert stored_parent is not None
    assert stored_parent["state"] == "waiting_child"
    parent_handoffs = db.list_runtime_episode_handoffs(parent_id)
    assert parent_handoffs[-1]["payload"]["status"] == "waiting"

    second_command = join_node(
        {
            "parallel_invocations": [{"invocationId": f"delegation_parent_{suffix}", "expected": 1}],
            "parallel_results": [
                {
                    "invocationId": f"delegation_parent_{suffix}",
                    "delegationId": parent_id,
                    "status": "waiting_child_delegation",
                    "childDelegationRequestIds": [f"child_req_{suffix}"],
                }
            ],
            "pending_child_delegations": [
                {
                    "requestId": f"child_req_{suffix}",
                    "sourceInvocationId": f"delegation_parent_{suffix}",
                    "sourceDelegationId": parent_id,
                    "childDelegationId": child_id,
                    "send": {"node": "parallel_delegate_task", "arg": child_state},
                }
            ],
            "routed_child_delegation_request_ids": [f"child_req_{suffix}"],
            "current_route_context": command.update["current_route_context"],
        }
    )

    assert second_command.goto == "supervisor"
    assert second_command.update["current_route_context"]["lastDelegationHandoff"]["state"] == "waiting_child"
    assert all(row["payload"]["status"] != "failed" for row in db.list_runtime_episode_handoffs(parent_id))


def test_parallel_join_creates_and_persists_handoff_for_completed_subagent(monkeypatch) -> None:
    persisted_handoffs: list[tuple[dict, dict]] = []
    persisted_episodes: list[tuple[dict, dict]] = []

    def _persist_handoff(handoff, **kwargs):
        persisted_handoffs.append((dict(handoff), dict(kwargs)))
        return dict(handoff)

    def _persist_episode(episode, **kwargs):
        persisted_episodes.append((dict(episode), dict(kwargs)))
        return dict(episode)

    monkeypatch.setattr(parallel_support, "persist_handoff_ref", _persist_handoff)
    monkeypatch.setattr(parallel_support, "persist_runtime_episode", _persist_episode)
    join_node = build_parallel_delegate_join_node()
    command = join_node(
        {
            "session_id": "session-parallel-join",
            "run_id": "run-parallel-join",
            "parallel_invocations": [{"invocationId": "delegation_parent", "expected": 1}],
            "current_route_context": {
                "activeCapabilityEpisodeId": "subagent::child",
                "capabilityEpisodes": [
                    {
                        "episodeId": "subagent::child",
                        "needId": "subagent::child",
                        "kind": "delegation",
                        "state": "waiting",
                    }
                ],
            },
            "parallel_results": [
                {
                    "invocationId": "delegation_parent",
                    "delegationId": "subagent::child",
                    "status": "ok",
                    "taskBriefId": "task-child",
                    "agentId": "child-agent",
                    "compactTranscript": "Reviewed the isolated file and found no blocking issues.",
                }
            ],
        }
    )

    route_context = command.update["current_route_context"]
    assert command.goto == "supervisor"
    assert route_context["handoffRefs"][0]["producerEpisodeId"] == "subagent::child"
    assert route_context["handoffRefs"][0]["compactSummary"].startswith("Delegation completed:")
    assert "Reviewed the isolated file" in route_context["handoffRefs"][0]["compactSummary"]
    assert route_context["capabilityEpisodes"][0]["state"] == "completed"
    assert "activeCapabilityEpisodeId" not in route_context
    message = command.update["messages"][0]
    assert "<delegation_handoffs>" in message.content
    assert "accept、retry 或 ignore" in message.content
    assert message.additional_kwargs["v8_governance_type"] == "delegation_handoff"
    assert persisted_handoffs[0][1]["session_id"] == "session-parallel-join"
    assert persisted_handoffs[0][1]["run_id"] == "run-parallel-join"
    assert persisted_episodes[0][0]["state"] == "completed"
