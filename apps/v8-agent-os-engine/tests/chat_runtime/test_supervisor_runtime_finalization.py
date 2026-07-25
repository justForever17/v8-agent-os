import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.models import ChatMessage, ChatRequest, ChatRequestData, EngineConfig
import graph.supervisor_turn as supervisor_turn_module
from graph.supervisor_turn import (
    _coerce_recoverable_failure_response,
    _filter_tool_names,
    _latest_message_is_true_user_input,
    _runtime_episode_handoff_ready,
    _runtime_episode_recoverable_failure,
    _runtime_handoff_continuation_message,
    _runtime_handoff_requires_continuation,
    _runtime_research_gap_state,
    _managed_research_retry_need,
    _runtime_handoff_final_response,
    _runtime_handoff_final_text,
    _runtime_handoff_final_message,
    _runtime_handoff_final_advisory_pending,
    _retry_delegation_acceptance_once,
    _runtime_recoverable_failure_final_response,
    _runtime_recoverable_failure_final_text,
    _runtime_recoverable_failure_message,
    _should_force_memory_broker_first,
    _message_session_coordination_reply,
    _looks_like_session_coordination_request,
    _latest_spec_revision_contract,
    _retry_spec_revision_once,
    _session_context_broker_first_response,
    _session_coordination_guidance,
    _session_coordination_outbound_guidance,
    _session_coordination_reply_called_since_injection,
    _session_coordination_requires_reply,
    _should_hide_todo_tools_for_direct_writing,
    _spec_revision_discipline_message,
    execute_supervisor_turn,
)
from runtimes.chat.supervisor_completion_gate import ACTIVE_EPISODE_STATES, evaluate_supervisor_completion
from runtimes.chat.runtime import ChatRuntime, _delegation_acceptance_from_final_text


def test_subagent_waiting_child_projection_is_progress_not_failure():
    emitted = []
    chat_run = SimpleNamespace(
        active_run_id="run-waiting-child",
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    execution_bundle = SimpleNamespace(runner_bundle=SimpleNamespace())
    state = {
        "parallel_results": [
            {
                "invocationId": "delegation-parent",
                "delegationId": "subagent::parent",
                "agentId": "implementation-engineer",
                "taskBriefId": "TASK-PARENT",
                "status": "waiting_child_delegation",
                "childDelegationCount": 1,
            }
        ]
    }

    async def _snapshot(_bundle):
        return state

    with (
        patch("runtimes.chat.runtime.supervisor_runner.get_state_snapshot", side_effect=_snapshot),
        patch("runtimes.chat.runtime.storage.get_agent", return_value={}),
    ):
        asyncio.run(ChatRuntime().emit_subagent_swarm_projection(chat_run, execution_bundle))

    assert emitted[0][0] == "subagent.task.updated"
    assert emitted[0][1]["status"] == "waiting_child_delegation"


def test_subagent_terminal_projection_records_supervisor_acceptance_and_nested_return():
    emitted = []
    chat_run = SimpleNamespace(
        active_run_id="run-accepted-child",
        session_id="session-accepted-child",
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    execution_bundle = SimpleNamespace(runner_bundle=SimpleNamespace())
    parent_delegation_id = "subagent::parent"
    child_delegation_id = "subagent::child"
    state = {
        "parallel_results": [
            {
                "invocationId": "delegation-parent",
                "delegationId": parent_delegation_id,
                "agentId": "implementation-engineer",
                "taskBriefId": "TASK-PARENT",
                "status": "waiting_child_delegation",
                "delegationDepth": 1,
            }
        ]
    }
    child_result = {
        "invocationId": "delegation-child",
        "delegationId": child_delegation_id,
        "parentDelegationId": parent_delegation_id,
        "agentId": "verification-engineer",
        "taskBriefId": "TASK-CHILD",
        "status": "ok",
        "delegationDepth": 2,
        "supervisorAcceptance": {"status": "pending"},
    }

    async def _snapshot(_bundle):
        return state

    with (
        patch("runtimes.chat.runtime.supervisor_runner.get_state_snapshot", side_effect=_snapshot),
        patch("runtimes.chat.runtime.storage.get_agent", return_value={}),
        patch(
            "runtimes.chat.runtime.db.get_runtime_episode",
            return_value={"episodeId": parent_delegation_id, "state": "completed", "metadata": {}},
        ),
        patch(
            "runtimes.chat.runtime.db.list_runtime_episode_handoffs",
            return_value=[{"payload": {"childHandoffs": [{"results": [child_result]}]}}],
        ),
        patch("runtimes.chat.runtime.db.add_runtime_episode_handoff") as add_handoff,
        patch("runtimes.chat.runtime.db.complete_runtime_episode") as complete_episode,
    ):
        asyncio.run(
            ChatRuntime().emit_subagent_swarm_projection(
                chat_run,
                execution_bundle,
                final_text="验收决定：ACCEPT\n理由：父子结果一致。",
            )
        )

    parent_event = next(payload for topic, payload, _ in emitted if payload.get("delegationId") == parent_delegation_id)
    child_event = next(payload for topic, payload, _ in emitted if payload.get("delegationId") == child_delegation_id)
    assert parent_event["status"] == "completed"
    assert parent_event["supervisorAcceptance"]["status"] == "accepted"
    assert child_event["status"] == "ok"
    assert child_event["parentDelegationId"] == parent_delegation_id
    acceptance_handoff = add_handoff.call_args.kwargs["handoff"]
    assert acceptance_handoff["kind"] == "subagent_acceptance"
    assert acceptance_handoff["status"] == "accepted"
    complete_episode.assert_called_once()


def test_delegation_acceptance_parser_requires_one_explicit_decision():
    accepted = _delegation_acceptance_from_final_text("验收决定：ACCEPT\n理由：证据完整。")
    assert accepted == {
        "status": "accepted",
        "decision": "ACCEPT",
        "summary": "理由：证据完整。",
    }
    markdown_accepted = _delegation_acceptance_from_final_text("> 验收决定：**ACCEPT**\n> 依据：证据完整。")
    assert markdown_accepted == {
        "status": "accepted",
        "decision": "ACCEPT",
        "summary": "依据：证据完整。",
    }
    natural_accepted = _delegation_acceptance_from_final_text(
        "验收结论：**accept**\n\n- accept：证据完整。\n- retry：不执行。\n- ignore：不执行。"
    )
    assert natural_accepted == {
        "status": "accepted",
        "decision": "ACCEPT",
        "summary": "accept：证据完整。\n- retry：不执行。\n- ignore：不执行。",
    }
    action_accepted = _delegation_acceptance_from_final_text(
        "1. **验收动作**：显式 **ACCEPT**（验收通过）。\n2. 依据：工具证据完整。"
    )
    assert action_accepted == {
        "status": "accepted",
        "decision": "ACCEPT",
        "summary": "（验收通过）。\n2. 依据：工具证据完整。",
    }
    heading_accepted = _delegation_acceptance_from_final_text(
        "## 验收动作\n\n**`accept` — 接受 Verification Engineer 的验收结果。**"
    )
    assert heading_accepted == {
        "status": "accepted",
        "decision": "ACCEPT",
        "summary": "接受 Verification Engineer 的验收结果。**",
    }
    repeated_accepted = _delegation_acceptance_from_final_text(
        "验收决定：ACCEPT\n前置结论。\n### 验收决定：ACCEPT\n依据：父子结果一致。"
    )
    assert repeated_accepted == {
        "status": "accepted",
        "decision": "ACCEPT",
        "summary": "依据：父子结果一致。",
    }
    assert _delegation_acceptance_from_final_text("结果已完成。") is None
    assert _delegation_acceptance_from_final_text("验收决定：ACCEPT\n验收决定：RETRY") is None


def test_runtime_handoff_retries_missing_delegation_acceptance_once():
    calls = []
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "handoff_ready",
            "handoffCount": 1,
        },
        "current_route_context": {
            "handoffRefs": [
                {
                    "kind": "delegation",
                    "status": "ready",
                    "results": [{"supervisorAcceptance": {"status": "pending"}}],
                }
            ]
        },
    }

    def _robust_invoke(_llm, messages, tools, **_kwargs):
        calls.append({"messages": messages, "tools": tools})
        return AIMessage(content="验收决定：ACCEPT\n依据：父子标题结果一致。")

    result = _retry_delegation_acceptance_once(
        AIMessage(content=""),
        state=state,
        prepared_messages=[HumanMessage(content="original")],
        invoke_llm=object(),
        robust_invoke=_robust_invoke,
        preferred_model_id="test-model",
        build_model=lambda _model_id: object(),
        sanitize_response_tool_calls=lambda response: response,
    )

    assert result.content.startswith("验收决定：ACCEPT")
    assert len(calls) == 1
    assert calls[0]["tools"] == []
    assert "Delegation Acceptance Discipline Correction" in calls[0]["messages"][-1].content


def test_direct_delegation_handoff_retries_acceptance_without_runtime_dispatch_status():
    calls = []
    contract = {"supervisorAcceptance": {"status": "pending"}}
    state = {
        "messages": [
            HumanMessage(content="verify this"),
            HumanMessage(
                content="[V8OS 子代理结构化回流]",
                additional_kwargs={
                    "v8_governance_type": "delegation_handoff",
                    "v8_delegation_handoffs": [contract],
                },
            ),
        ],
        "parallel_results": [contract],
    }

    def _robust_invoke(_llm, messages, tools, **_kwargs):
        calls.append({"messages": messages, "tools": tools})
        return AIMessage(content="验收决定：ACCEPT\n依据：回流证据满足合同。")

    result = _retry_delegation_acceptance_once(
        AIMessage(content="结果已通过。"),
        state=state,
        prepared_messages=[HumanMessage(content="original")],
        invoke_llm=object(),
        robust_invoke=_robust_invoke,
        preferred_model_id="test-model",
        build_model=lambda _model_id: object(),
        sanitize_response_tool_calls=lambda response: response,
    )

    assert result.content.startswith("验收决定：ACCEPT")
    assert len(calls) == 1


def test_stale_delegation_handoff_does_not_force_acceptance_after_new_user_message():
    calls = []
    state = {
        "messages": [
            HumanMessage(
                content="[V8OS 子代理结构化回流]",
                additional_kwargs={
                    "v8_governance_type": "delegation_handoff",
                    "v8_delegation_handoffs": [{"supervisorAcceptance": {"status": "pending"}}],
                },
            ),
            HumanMessage(content="start an unrelated new task"),
        ],
        "parallel_results": [{"supervisorAcceptance": {"status": "pending"}}],
    }

    result = _retry_delegation_acceptance_once(
        AIMessage(content="new task response"),
        state=state,
        prepared_messages=[],
        invoke_llm=object(),
        robust_invoke=lambda *_args, **_kwargs: calls.append(True),
        preferred_model_id="test-model",
        build_model=lambda _model_id: object(),
        sanitize_response_tool_calls=lambda response: response,
    )

    assert result.content == "new task response"
    assert calls == []


def test_internal_delegation_handoff_is_not_treated_as_new_user_input():
    message = HumanMessage(
        content="[V8OS 子代理结构化回流]",
        additional_kwargs={"v8_governance_type": "delegation_handoff"},
    )
    assert not _latest_message_is_true_user_input([message])


def test_completion_gate_blocks_terminal_delegation_without_parent_acceptance():
    episode = {
        "episodeId": "subagent::parent",
        "kind": "delegation",
        "state": "completed",
        "metadata": {},
    }
    handoffs = {
        "subagent::parent": [
            {
                "status": "ready",
                "payload": {"results": [{"supervisorAcceptance": {"status": "pending"}}]},
            }
        ]
    }

    missing = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode=handoffs,
        final_text="子 Agent 已完成。",
    )
    accepted = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode=handoffs,
        final_text="> 验收决定：**ACCEPT**\n> 依据：证据完整。",
    )
    natural_accepted = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode=handoffs,
        final_text="验收结论：**accept**\n- retry：不执行。\n- ignore：不执行。",
    )
    repeated_accepted = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode=handoffs,
        final_text="验收决定：ACCEPT\n摘要。\n### 验收决定：ACCEPT\n依据：证据完整。",
    )
    conflicting = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode=handoffs,
        final_text="验收决定：ACCEPT\n验收决定：RETRY",
    )

    assert missing.action == "fail"
    assert missing.reason == "delegation_supervisor_acceptance_missing"
    assert accepted.action == "complete"
    assert natural_accepted.action == "complete"
    assert repeated_accepted.action == "complete"
    assert conflicting.action == "fail"
    assert conflicting.reason == "delegation_supervisor_acceptance_missing"


def test_runtime_episode_handoff_ready_requires_resume_terminal_state():
    assert _runtime_episode_handoff_ready(
        {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "handoff_ready",
                "handoffCount": 2,
            }
        }
    )

    assert not _runtime_episode_handoff_ready(
        {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "wait_episode",
                "state": "handoff_ready",
                "handoffCount": 2,
            }
        }
    )

    assert not _runtime_episode_handoff_ready(
        {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "episode_terminal",
                "handoffCount": 0,
            }
        }
    )


def test_session_context_refs_force_broker_calls_in_order_and_stop_after_failures():
    tools = [SimpleNamespace(name="session_context_broker"), SimpleNamespace(name="runtime_broker")]
    refs = [
        {"sessionId": "session-source-1", "source": "history_menu"},
        {"sessionId": "session-source-2", "source": "history_menu"},
    ]
    first_state = {
        "messages": [HumanMessage(content="continue with these sessions")],
        "contextSessionRefs": refs,
    }

    first = _session_context_broker_first_response(first_state, tools)

    assert first is not None
    assert first.tool_calls[0]["name"] == "session_context_broker"
    assert first.tool_calls[0]["args"]["sourceSessionId"] == "session-source-1"

    failed_result_state = {
        "messages": [
            HumanMessage(content="continue with these sessions"),
            first,
            {"role": "tool", "name": "session_context_broker", "content": '{"ok":false}'},
        ],
        "contextSessionRefs": refs,
    }
    second = _session_context_broker_first_response(failed_result_state, tools)

    assert second is not None
    assert second.tool_calls[0]["args"]["sourceSessionId"] == "session-source-2"
    completed_state = {"messages": [*failed_result_state["messages"], second], "contextSessionRefs": refs}
    assert _session_context_broker_first_response(completed_state, tools) is None


def test_session_coordination_discipline_requires_exact_single_reply_call():
    coordination = {
        "messageId": "coord-001",
        "messageType": "request",
        "sourceSessionId": "session-source-001",
        "targetSessionId": "session-target-001",
        "state": "injected",
        "hopCount": 1,
        "maxHops": 2,
    }
    assert _session_coordination_requires_reply(coordination)
    guidance = str(_session_coordination_guidance(coordination).content)
    assert "latest user instruction has higher priority" in guidance
    assert "Do not inherit the source workspace" in guidance
    assert "exactly one reply" in guidance

    injected = HumanMessage(
        content="[V8OS Cross-session Coordination]",
        additional_kwargs={"v8os_session_coordination": coordination},
    )
    wrong_call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-status",
                "name": "session_message_broker",
                "args": {"mode": "status", "messageId": "coord-001"},
            }
        ],
    )
    reply_call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-reply",
                "name": "session_message_broker",
                "args": {
                    "mode": "reply",
                    "messageId": "coord-001",
                    "replyStatus": "accepted",
                    "content": "Acknowledged.",
                },
            }
        ],
    )
    assert not _message_session_coordination_reply(wrong_call, "coord-001")
    assert _message_session_coordination_reply(reply_call, "coord-001")
    assert not _session_coordination_reply_called_since_injection(
        {"messages": [injected, wrong_call]},
        "coord-001",
    )
    assert _session_coordination_reply_called_since_injection(
        {"messages": [injected, reply_call]},
        "coord-001",
    )


def test_session_coordination_second_hop_cannot_request_third_reply():
    coordination = {
        "messageId": "coord-reply-001",
        "messageType": "reply",
        "sourceSessionId": "session-target-001",
        "targetSessionId": "session-source-001",
        "state": "injected",
        "hopCount": 2,
        "maxHops": 2,
    }
    assert not _session_coordination_requires_reply(coordination)
    guidance = str(_session_coordination_guidance(coordination).content)
    assert "do not create a third hop" in guidance
    assert "Do not call session_message_broker" in guidance


def test_latest_spec_revision_contract_tracks_only_the_latest_unresolved_spec_result():
    invalid = {
        "ok": True,
        "kind": "spec_stage_saved_needs_revision",
        "specId": "spec_demo",
        "stage": "requirements",
        "reviewReady": False,
        "missingConstraints": [
            {"kind": "target_output_directory", "value": ".v8/live-audit/spec-mode-v2/demo"}
        ],
    }
    messages = [
        HumanMessage(content="write requirements"),
        ToolMessage(content=json.dumps(invalid), tool_call_id="call-invalid", name="spec_broker"),
    ]

    assert _latest_spec_revision_contract(messages) == invalid
    guidance = str(_spec_revision_discipline_message(invalid).content)
    assert "rewrite_stage" in guidance
    assert ".v8/live-audit/spec-mode-v2/demo" in guidance
    assert "no human approval exists" in guidance

    valid = {
        "ok": True,
        "kind": "spec_stage_written",
        "specId": "spec_demo",
        "stage": "requirements",
        "reviewReady": True,
    }
    messages.append(ToolMessage(content=json.dumps(valid), tool_call_id="call-valid", name="spec_broker"))
    assert _latest_spec_revision_contract(messages) == {}


def test_spec_revision_discipline_retries_once_when_supervisor_stops_without_tool_call():
    contract = {
        "kind": "spec_stage_saved_needs_revision",
        "specId": "spec_demo",
        "stage": "requirements",
        "reviewReady": False,
    }
    calls = []
    corrected = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-rewrite",
                "name": "spec_broker",
                "args": {
                    "mode": "rewrite_stage",
                    "spec_id": "spec_demo",
                    "stage": "requirements",
                    "content": "# Requirements",
                },
            }
        ],
    )

    def robust_invoke(_llm, messages, _tools, **_kwargs):
        calls.append(messages)
        return corrected

    response = _retry_spec_revision_once(
        AIMessage(content="requirements are done"),
        contract=contract,
        prepared_messages=[HumanMessage(content="write requirements")],
        invoke_llm=object(),
        filtered_tools=[SimpleNamespace(name="spec_broker")],
        robust_invoke=robust_invoke,
        preferred_model_id="test-model",
        build_model=lambda _model_id: object(),
        sanitize_response_tool_calls=lambda value: value,
    )

    assert response is corrected
    assert len(calls) == 1
    assert "single correction opportunity" in str(calls[0][-1].content)

    calls.clear()
    assert _retry_spec_revision_once(
        corrected,
        contract=contract,
        prepared_messages=[],
        invoke_llm=object(),
        filtered_tools=[],
        robust_invoke=robust_invoke,
        preferred_model_id="test-model",
        build_model=lambda _model_id: object(),
        sanitize_response_tool_calls=lambda value: value,
    ) is corrected
    assert calls == []


def test_natural_language_session_coordination_request_keeps_brokers_visible():
    existing = {
        "spec-mode-project-live-20260709-213406",
        "session-target-001",
    }
    session_exists = lambda session_id: session_id in existing
    assert _looks_like_session_coordination_request(
        "请通知 spec-mode-project-live-20260709-213406 核对最新任务。",
        "session-current-001",
        session_exists=session_exists,
    )
    assert _looks_like_session_coordination_request(
        "send a correction to session-target-001",
        "session-current-001",
        session_exists=session_exists,
    )
    assert not _looks_like_session_coordination_request(
        "请总结一下当前任务，不要联系其他会话。",
        "session-current-001",
        session_exists=session_exists,
    )
    assert not _looks_like_session_coordination_request(
        "send the package.json summary to the build log",
        "session-current-001",
        session_exists=session_exists,
    )
    assert not _looks_like_session_coordination_request(
        "不要通知 session-target-001，也不要创建协调草稿。",
        "session-current-001",
        session_exists=session_exists,
    )
    guidance = str(_session_coordination_outbound_guidance().content)
    assert "Do not let Memory" in guidance
    assert "pre-adjudicate a conflict" in guidance
    assert "do not replace the explicit send with advice or options" in guidance

def test_runtime_episode_degraded_handoff_ready_is_terminal():
    assert _runtime_episode_handoff_ready(
        {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "degraded_handoff_ready",
                "handoffCount": 1,
            }
        }
    )


def test_runtime_handoff_final_message_leaves_delivery_decision_to_supervisor():
    message = _runtime_handoff_final_message()
    content = str(message.content)
    assert "You are the delivery owner" in content
    assert "detail, verification, repair, or runtime tools" in content
    assert "do not re-read the same artifact" in content
    assert "no additional handoff will arrive" in content
    assert "declared taskBriefIds" in content
    assert "research-plus-implementation" in content
    assert "unfinished delivery contract" in content
    assert "machine-readable baseline" in content
    assert "route one typed Engineering episode before the first implementation command" in content
    assert "Skill may improve the method" in content
    assert "never wait for phantom handoffs" in content
    assert "quarantined candidate" in content
    assert "managed child worktree path is execution provenance" in content
    assert "parentWorktreeMerge.status=merged_to_parent" in content
    assert "already present in the current Active Workspace Root" in content
    assert "do not route the same acceptance criteria again" in content
    assert "repairTaskBriefIds" in content
    assert "do not poll the terminal episode" in content
    assert "evidence=complete" in content
    assert "验收决定：ACCEPT" in content
    assert "验收决定：RETRY" in content
    assert "验收决定：IGNORE" in content
    assert "Do not call tools" not in content
    assert "single review edge" in content
    assert "one bounded verification pass is clean" in content


def test_runtime_handoff_final_advisory_is_one_edge_per_persisted_summary():
    first_summary = HumanMessage(
        content="[Runtime Episode Handoff Ready]",
        additional_kwargs={"v8_governance_type": "runtime_handoff"},
    )
    assert _runtime_handoff_final_advisory_pending({"messages": [first_summary]})

    local_action = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "write-1",
                "name": "write_native_file",
                "args": {"path": "README.md", "content": "verified"},
            }
        ],
    )
    local_result = ToolMessage(content='{"ok":true}', tool_call_id="write-1", name="write_native_file")
    assert not _runtime_handoff_final_advisory_pending(
        {"messages": [first_summary, local_action, local_result]}
    )

    second_summary = HumanMessage(
        content="[Runtime Episode Handoff Ready]",
        additional_kwargs={"v8_governance_type": "runtime_handoff"},
    )
    assert _runtime_handoff_final_advisory_pending(
        {"messages": [first_summary, local_action, local_result, second_summary]}
    )

    # Synthetic recovery states without a persisted summary retain guidance.
    assert _runtime_handoff_final_advisory_pending({"messages": []})


def test_degraded_research_handoff_projects_exact_gap_and_one_managed_retry():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "degraded_handoff_ready",
            "handoffCount": 1,
        },
        "current_route_context": {
            "capabilityEpisodes": [
                {
                    "episodeId": "episode-research-1",
                    "kind": "research",
                    "inputs": {
                        "taskBriefs": [
                            {
                                "taskBriefId": "sqlite-jsonb",
                                "goal": "Verify the current official SQLite JSONB contract.",
                                "readOnly": True,
                                "writeSet": [],
                            }
                        ]
                    },
                }
            ],
            "handoffRefs": [
                {
                    "handoffRefId": "handoff-research-1",
                    "producerEpisodeId": "episode-research-1",
                    "kind": "research_evidence_bundle",
                    "status": "degraded",
                    "coveredTaskBriefIds": ["sqlite-fts5"],
                    "missingTaskBriefIds": ["sqlite-jsonb"],
                    "taskBriefResults": [
                        {
                            "taskBriefId": "sqlite-jsonb",
                            "status": "degraded",
                            "limitations": ["Missing official SQLite documentation."],
                            "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
                        }
                    ],
                }
            ],
        },
    }

    gap = _runtime_research_gap_state(state)
    assert gap["missingTaskBriefIds"] == ["sqlite-jsonb"]
    assert gap["attempts"] == {"sqlite-jsonb": 1}
    assert gap["retryAvailable"] is True
    assert _runtime_handoff_requires_continuation(state)
    content = str(_runtime_handoff_continuation_message(state).content)
    assert "sqlite-jsonb" in content
    assert "Verify the current official SQLite JSONB contract" in content
    assert "Exactly one managed retry remains" in content
    assert "Do not call tool_observation_detail on a runtime_broker route receipt" in content
    typed_block = content.split("```json\n", 1)[1].split("\n```", 1)[0]
    typed_args = json.loads(typed_block)
    assert typed_args["mode"] == "route"
    assert typed_args["routeKind"] == "research"
    assert typed_args["researchBriefIds"] == ["sqlite-jsonb"]
    assert typed_args["researchBriefGoals"] == ["Verify the current official SQLite JSONB contract."]
    retry_context = json.loads(typed_args["researchBriefContexts"][0])
    assert retry_context == {
        "priorEvidenceStatus": "degraded",
        "priorEvidenceReasons": ["explicit_critical_evidence_gap"],
        "priorLimitations": ["Missing official SQLite documentation."],
    }


def test_research_retry_correction_requires_exact_runtime_tool_call():
    state = {
        "current_route_context": {
            "capabilityEpisodes": [
                {
                    "episodeId": "episode-research-1",
                    "kind": "research",
                    "inputs": {
                        "taskBriefs": [
                            {
                                "taskBriefId": "python-windows",
                                "goal": "Verify the official Python Windows support contract.",
                                "readOnly": True,
                                "writeSet": [],
                                "detailRefs": ["https://python.org/downloads/windows/"],
                            }
                        ]
                    },
                }
            ],
            "handoffRefs": [
                {
                    "kind": "research_evidence_bundle",
                    "producerEpisodeId": "episode-research-1",
                    "status": "degraded",
                    "missingTaskBriefIds": ["python-windows"],
                    "taskBriefResults": [
                        {
                            "taskBriefId": "python-windows",
                            "status": "degraded",
                            "limitations": ["No official source was found."],
                            "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
                        }
                    ],
                }
            ],
        }
    }
    calls = []
    bound_tool_names = []

    def robust_invoke(_llm, messages, _tools, **_kwargs):
        calls.append(messages[-1].content)
        bound_tool_names.append([getattr(tool, "name", "") for tool in _tools])
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "retry-research",
                    "name": "runtime_broker",
                    "args": _managed_research_retry_need(_runtime_research_gap_state(state)),
                }
            ],
        )

    corrected = supervisor_turn_module._retry_missing_research_briefs_once(
        AIMessage(content="我会马上重试。"),
        state=state,
        prepared_messages=[HumanMessage(content="original")],
        invoke_llm=object(),
        filtered_tools=[SimpleNamespace(name="runtime_broker")],
        robust_invoke=robust_invoke,
        preferred_model_id="test-model",
        build_model=lambda _model_id: object(),
        sanitize_response_tool_calls=lambda response: response,
    )

    assert corrected.tool_calls[0]["name"] == "runtime_broker"
    assert len(calls) == 1
    assert bound_tool_names == [["runtime_broker"]]
    assert "exact typed route arguments" in calls[0]
    assert '"researchBriefIds":["python-windows"]' in calls[0]
    assert '"researchBriefGoals":["Verify the official Python Windows support contract."]' in calls[0]
    assert '"researchBriefContexts":[' in calls[0]


def test_second_degraded_research_handoff_exhausts_retry_and_blocks_false_completion():
    handoff = {
        "kind": "research_evidence_bundle",
        "status": "degraded",
        "missingTaskBriefIds": ["sqlite-jsonb"],
        "taskBriefResults": [
            {
                "taskBriefId": "sqlite-jsonb",
                "status": "degraded",
                "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
            }
        ],
    }
    state = {
        "current_route_context": {
            "handoffRefs": [
                {**handoff, "producerEpisodeId": "episode-research-1"},
                {**handoff, "producerEpisodeId": "episode-research-2"},
            ]
        }
    }

    gap = _runtime_research_gap_state(state)
    assert gap["attempts"] == {"sqlite-jsonb": 2}
    assert gap["retryAvailable"] is False
    assert not _runtime_handoff_requires_continuation(state)
    final_advisory = str(_runtime_handoff_final_message(state).content)
    assert "managed Research retry is exhausted" in final_advisory
    assert "Do not create a third Research route" in final_advisory

    decision = evaluate_supervisor_completion(
        episodes=[
            {"episodeId": "episode-research-1", "kind": "research", "state": "degraded"},
            {"episodeId": "episode-research-2", "kind": "research", "state": "degraded"},
        ],
        handoffs_by_episode={
            "episode-research-1": [{"payload": {**handoff, "createdAt": "2026-07-24T10:00:00Z"}}],
            "episode-research-2": [{"payload": {**handoff, "createdAt": "2026-07-24T10:05:00Z"}}],
        },
        final_text="全部文件已完成并验证。",
    )
    assert decision.action == "fail"
    assert decision.reason == "research_brief_evidence_incomplete"
    assert decision.details["missingTaskBriefIds"] == ["sqlite-jsonb"]


def test_exhausted_research_gap_does_not_swallow_pending_engineering_milestone():
    handoff = {
        "kind": "research_evidence_bundle",
        "status": "degraded",
        "downstreamAllowed": True,
        "coveredTaskBriefIds": ["sqlite-fts5"],
        "missingTaskBriefIds": ["python-win"],
        "taskBriefResults": [
            {"taskBriefId": "sqlite-fts5", "status": "ready", "researchRef": "research://bundle/fts5"},
            {
                "taskBriefId": "python-win",
                "status": "degraded",
                "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
            },
        ],
    }
    state = {
        "todos": [{"text": "进入编程模式实现并做本地验证", "status": "pending"}],
        "current_route_context": {
            "capabilityEpisodes": [
                {"episodeId": "episode-research-1", "kind": "research"},
                {"episodeId": "episode-research-2", "kind": "research"},
            ],
            "handoffRefs": [
                {**handoff, "producerEpisodeId": "episode-research-1"},
                {**handoff, "producerEpisodeId": "episode-research-2"},
            ],
        },
    }

    gap = _runtime_research_gap_state(state)
    assert gap["retryAvailable"] is False
    assert gap["downstreamAllowed"] is True
    assert gap["readyTaskBriefIds"] == ["sqlite-fts5"]
    assert _runtime_handoff_requires_continuation(state) is True
    continuation = str(_runtime_handoff_continuation_message(state).content)
    assert "Pending runtime kinds: engineering" in continuation
    assert "block only the corresponding unsupported claims" in continuation
    assert "route that downstream runtime now" in continuation


def test_exhausted_research_gap_can_finish_after_verified_downstream_carries_exact_gap():
    research_handoff = {
        "kind": "research_evidence_bundle",
        "status": "degraded",
        "createdAt": "2026-07-24T10:05:00Z",
        "missingTaskBriefIds": ["python-win"],
        "taskBriefResults": [
            {
                "taskBriefId": "python-win",
                "status": "degraded",
                "evidenceStatusReasons": ["explicit_critical_evidence_gap"],
            }
        ],
    }
    engineering_handoff = {
        "kind": "engineering_patch_bundle",
        "status": "ready",
        "artifactRefs": ["workspace://reports/local-baseline.json"],
        "proofRefs": ["proof://local-baseline-verified"],
    }
    decision = evaluate_supervisor_completion(
        episodes=[
            {"episodeId": "episode-research", "kind": "research", "state": "degraded"},
            {
                "episodeId": "episode-engineering",
                "kind": "engineering",
                "state": "completed",
                "inputs": {
                    "researchContext": {
                        "downstreamAllowed": True,
                        "evidenceGaps": [
                            {
                                "taskBriefId": "python-win",
                                "blocksClaim": True,
                                "blocksDownstream": False,
                            }
                        ],
                    },
                    "taskBriefs": [
                        {
                            "taskBriefId": "local-baseline",
                            "goal": "Verify the reversible local baseline without asserting the missing external fact.",
                            "readOnly": True,
                            "writeSet": [],
                        }
                    ],
                },
            },
        ],
        handoffs_by_episode={
            "episode-research": [{"payload": research_handoff}],
            "episode-engineering": [{"payload": engineering_handoff}],
        },
        final_text="本地基线已验证；未核实的官方 Windows 声明仍明确列为限制。",
    )

    assert decision.action == "complete"
    assert decision.reason == "research_gaps_carried_to_verified_downstream"
    assert decision.details["severity"] == "advisory"
    assert decision.details["missingTaskBriefIds"] == ["python-win"]
    assert decision.details["downstream"]["episodeId"] == "episode-engineering"


def test_later_ready_research_retry_resolves_prior_gap_for_completion_gate():
    degraded = {
        "kind": "research_evidence_bundle",
        "status": "degraded",
        "createdAt": "2026-07-24T10:00:00Z",
        "missingTaskBriefIds": ["sqlite-jsonb"],
        "taskBriefResults": [{"taskBriefId": "sqlite-jsonb", "status": "degraded"}],
    }
    ready = {
        "kind": "research_evidence_bundle",
        "status": "ready",
        "createdAt": "2026-07-24T10:05:00Z",
        "coveredTaskBriefIds": ["sqlite-jsonb"],
        "missingTaskBriefIds": [],
        "taskBriefResults": [{"taskBriefId": "sqlite-jsonb", "status": "ready"}],
    }
    decision = evaluate_supervisor_completion(
        episodes=[
            {"episodeId": "episode-research-1", "kind": "research", "state": "degraded"},
            {"episodeId": "episode-research-2", "kind": "research", "state": "completed"},
        ],
        handoffs_by_episode={
            "episode-research-1": [{"payload": degraded}],
            "episode-research-2": [{"payload": ready}],
        },
        final_text="已交付来源结论。",
    )
    assert decision.action == "complete"


def test_runtime_handoff_resume_does_not_repeat_memory_first_gate():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "handoff_ready",
            "handoffCount": 1,
        },
        "messages": [],
    }

    assert not _should_force_memory_broker_first(
        user_query="继续完成刚才的任务",
        passive_rag_diagnostics={"has_recall_cue": True},
        selected_tools=[SimpleNamespace(name="memory_broker")],
        state=state,
    )

    state["runtime_dispatch_status"] = {
        "mode": "runtime_episode",
        "nextAction": "recoverable_failure",
        "state": "episode_failed",
        "reason": "delegation_worker_failed",
    }
    assert not _should_force_memory_broker_first(
        user_query="继续修复刚才的任务",
        passive_rag_diagnostics={"has_recall_cue": True},
        selected_tools=[SimpleNamespace(name="memory_broker")],
        state=state,
    )


def test_runtime_handoff_compat_response_is_review_summary_from_handoff_refs():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "handoff_ready",
            "handoffCount": 1,
        },
        "current_route_context": {
            "handoffRefs": [
                {
                    "kind": "engineering_patch_bundle",
                    "status": "ready",
                    "compactSummary": "Engineering Runtime 已产出 work_plan_ready。",
                }
            ]
        },
    }
    text = _runtime_handoff_final_text(state)
    response = _runtime_handoff_final_response(state)
    assert "等待 Supervisor 验收" in text
    assert "engineering_patch_bundle / ready" in text
    assert "work_plan_ready" in str(response.content)


def test_compiled_creative_media_handoff_requires_continuation():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "handoff_ready",
            "handoffCount": 1,
        },
        "current_route_context": {
            "handoffRefs": [
                {
                    "kind": "asset_bundle",
                    "status": "ready",
                    "compactSummary": "Creative Media recipe compiled: cm_recipe_demo",
                    "recipeRefs": ["cm_recipe_demo"],
                    "artifactRefs": [],
                    "handoffStage": "compiled",
                    "requiresContinuation": True,
                    "recommendedNextAction": "Call creative_media_create_job and poll the job.",
                }
            ]
        },
    }

    assert _runtime_episode_handoff_ready(state)
    assert _runtime_handoff_requires_continuation(state)
    message = _runtime_handoff_continuation_message(state)
    assert "not the user's final deliverable" in str(message.content)
    assert "creative_media_create_job" in str(message.content)


def test_artifact_creative_media_handoff_is_terminal():
    state = {
        "current_route_context": {
            "handoffRefs": [
                {
                    "kind": "asset_bundle",
                    "status": "ready",
                    "artifactRefs": ["art_video"],
                    "handoffStage": "delivered",
                    "requiresContinuation": False,
                }
            ]
        }
    }

    assert not _runtime_handoff_requires_continuation(state)


def test_completion_gate_reports_forward_only_text_as_advisory():
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_creative", "state": "completed", "kind": "creative_media"}],
        final_text="我已经读完技能。现在让我创建任务并生成真实素材。",
    )

    assert decision.action == "complete"
    assert decision.reason == "forward_only_supervisor_advisory"
    assert decision.details["severity"] == "advisory"


def test_completion_gate_rejects_textual_side_effect_tool_markup_as_execution():
    decision = evaluate_supervisor_completion(
        final_text=(
            "文件已经写好。\n"
            "<tool_call><invoke name=\"write_native_file\">"
            "<parameter name=\"path\">report.json</parameter>"
            "</invoke></tool_call>"
        ),
    )

    assert decision.action == "fail"
    assert decision.reason == "supervisor_pseudo_tool_markup_not_executed"
    assert decision.details["toolNames"] == ["write_native_file"]
    assert decision.details["nextAction"] == "retry_with_native_structured_tool_calls_or_report_blocker"


def test_finalize_success_schedules_one_native_tool_correction_before_failing(monkeypatch):
    emitted = []
    run_handle = SimpleNamespace(complete=Mock(), transition=Mock(), fail=Mock())
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=False, spec_id="", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-native-correction",
        active_run_id="run-native-correction",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    scheduled = []
    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episodes", lambda **_kwargs: [])
    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episode_handoffs", lambda _episode_id: [])
    monkeypatch.setattr(
        ChatRuntime,
        "_completion_final_text",
        lambda *_args, **_kwargs: '<tool_call><invoke name="run_system_command"></invoke></tool_call>',
    )
    monkeypatch.setattr(
        "erc.command_router.runtime_command_router.schedule_supervisor_native_tool_correction",
        lambda run_id, *, tool_names: scheduled.append((run_id, tool_names))
        or {"resume_scheduled": True, "resumed_run_id": run_id},
    )

    result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "running"
    assert result["reason"] == "supervisor_native_tool_correction_scheduled"
    assert scheduled == [("run-native-correction", ["run_system_command"])]
    run_handle.transition.assert_called_once_with(
        "running",
        reason="supervisor_native_tool_correction_scheduled",
        node="completion_gate",
    )
    run_handle.fail.assert_not_called()
    run_handle.complete.assert_not_called()
    assert emitted[0][0] == "run.completion.native_tool_correction_scheduled"


def test_finalize_success_fails_when_native_tool_correction_is_already_used(monkeypatch):
    emitted = []
    run_handle = SimpleNamespace(complete=Mock(), transition=Mock(), fail=Mock())
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=False, spec_id="", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-native-correction",
        active_run_id="run-native-correction",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episodes", lambda **_kwargs: [])
    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episode_handoffs", lambda _episode_id: [])
    monkeypatch.setattr(
        ChatRuntime,
        "_completion_final_text",
        lambda *_args, **_kwargs: '<tool_call><invoke name="run_system_command"></invoke></tool_call>',
    )
    monkeypatch.setattr(
        "erc.command_router.runtime_command_router.schedule_supervisor_native_tool_correction",
        lambda *_args, **_kwargs: {
            "resume_scheduled": False,
            "resume_error": "supervisor_native_tool_correction_already_used",
        },
    )
    monkeypatch.setattr(ChatRuntime, "_expire_plugin_task_grants", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ChatRuntime, "_abort_engineering_workspaces", lambda *_args, **_kwargs: None)

    result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "failed"
    assert result["reason"] == "supervisor_pseudo_tool_markup_not_executed"
    run_handle.fail.assert_called_once_with(
        "supervisor_pseudo_tool_markup_not_executed",
        node="completion_gate",
    )
    assert emitted[0][0] == "run.completion.blocked"


def test_runtime_recoverable_failure_message_blocks_false_completion():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "artifact_acceptance_failed",
        }
    }
    assert _runtime_episode_recoverable_failure(state)
    message = _runtime_recoverable_failure_message(state)
    content = str(message.content)
    assert "MUST NOT claim" in content
    assert "artifact_acceptance_failed" in content


def test_runtime_recoverable_failure_final_response_is_deterministic():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "worker_acceptance_failed",
            "failedEpisodeCount": 1,
            "failedHandoffCount": 1,
        }
    }
    text = _runtime_recoverable_failure_final_text(state)
    response = _runtime_recoverable_failure_final_response(state)
    assert "还没有真正完成" in text
    assert "worker_acceptance_failed" in text
    assert "失败 episode 数：1" in str(response.content)


def test_runtime_recoverable_failure_response_is_coerced_when_model_claims_success():
    state = {
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "artifact_acceptance_failed",
        }
    }
    response = SimpleNamespace(content="已完整生成并交付。", additional_kwargs={})
    coerced = _coerce_recoverable_failure_response(response, state)
    assert "还没有真正完成" in coerced.content
    assert "artifact_acceptance_failed" in coerced.content


def test_runtime_recoverable_failure_reenters_real_supervisor_invocation(monkeypatch):
    calls = []
    decision = SimpleNamespace(as_dict=lambda: {})
    route_bundle = SimpleNamespace(
        filtered_tools=[],
        prompt_addition="",
        selected_skill_names=[],
        exposed_mcp_tool_names=[],
        candidate_summary={"reason": "test"},
    )
    runtime_service = SimpleNamespace(
        bind_execution_context=lambda **_kwargs: "token",
        reset_execution_context=lambda _token: None,
        emit_route_selected=lambda **_kwargs: None,
        emit_supervisor_diagnostics=lambda _payload: None,
        emit_response_tool_calls=lambda _response: None,
        emit_execution_completed=lambda **_kwargs: None,
    )
    monkeypatch.setattr(supervisor_turn_module, "extensions_runtime_service", runtime_service)
    monkeypatch.setattr(
        supervisor_turn_module,
        "resolve_supervisor_request_context",
        lambda _messages, _service: {
            "user_query": "repair the failed artifact",
            "current_scope": "workspace",
            "scope_chain": ["workspace"],
            "session_id": "session-repair",
        },
    )
    monkeypatch.setattr(supervisor_turn_module, "filter_visible_tools_for_actor", lambda tools, **_kwargs: tools)
    monkeypatch.setattr(supervisor_turn_module, "_filter_spec_tools_for_mode", lambda tools, _state: tools)
    monkeypatch.setattr(supervisor_turn_module, "_is_network_supervisor_compat_transport", lambda _state: False)
    monkeypatch.setattr(supervisor_turn_module, "_should_use_spec_narrow_route", lambda _state: True)
    monkeypatch.setattr(supervisor_turn_module, "_build_neutral_extensions_route", lambda _tools: route_bundle)
    monkeypatch.setattr(supervisor_turn_module, "_should_include_extensions_prefilter_prompt", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor_turn_module, "_suppress_extensions_prefilter_prompt", lambda bundle: bundle)
    monkeypatch.setattr(supervisor_turn_module, "_memory_no_match_since_latest_human", lambda _state: False)
    monkeypatch.setattr(supervisor_turn_module, "_should_hide_todo_tools_for_direct_writing", lambda *_args: False)
    monkeypatch.setattr(supervisor_turn_module, "runtime_reflex_service", SimpleNamespace(evaluate=lambda **_kwargs: decision))
    monkeypatch.setattr(supervisor_turn_module, "runtime_preflight_gate", SimpleNamespace(evaluate=lambda **_kwargs: decision))
    monkeypatch.setattr(supervisor_turn_module, "render_reflex_prompt_addition", lambda _decision: "")
    monkeypatch.setattr(supervisor_turn_module, "render_gate_prompt_addition", lambda _decision: "")
    monkeypatch.setattr(
        supervisor_turn_module,
        "build_supervisor_system_content",
        lambda **_kwargs: {"system_content": "system", "v8_prompt_segments": []},
    )
    monkeypatch.setattr(supervisor_turn_module, "_last_memory_session_context_diagnostics", lambda: {})
    monkeypatch.setattr(supervisor_turn_module, "log_memory_observation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supervisor_turn_module, "_estimate_memory_context_chars", lambda _value: 0)
    monkeypatch.setattr(
        supervisor_turn_module,
        "runtime_evidence_feedback_service",
        SimpleNamespace(record=lambda **_kwargs: decision),
    )
    monkeypatch.setattr(supervisor_turn_module, "apply_passive_rag_injection", lambda messages, **_kwargs: messages)
    monkeypatch.setattr(
        supervisor_turn_module,
        "_last_human_memory_rag_diagnostics",
        lambda _messages: {"injection_allowed": False, "reject_reason": "test"},
    )
    monkeypatch.setattr(supervisor_turn_module, "prepare_supervisor_messages", lambda **kwargs: list(kwargs["messages"]))
    monkeypatch.setattr(supervisor_turn_module, "_should_force_memory_broker_first", lambda **_kwargs: False)
    monkeypatch.setattr(supervisor_turn_module, "_spec_mode_stage_guidance", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor_turn_module, "debug_supervisor_messages", lambda _messages: None)
    monkeypatch.setattr(supervisor_turn_module, "_attach_route_context_to_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supervisor_turn_module, "apply_no_progress_breaker", lambda _messages, response: (response, None))

    def robust_invoke(*_args, **_kwargs):
        calls.append("invoked")
        return AIMessage(
            content="",
            tool_calls=[{"id": "call_repair", "name": "runtime_broker", "args": {"mode": "route"}}],
        )

    state = {
        "run_id": "run-repair",
        "runtime_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "artifact_acceptance_failed",
        },
    }
    response = execute_supervisor_turn(
        state=state,
        config={},
        messages=[HumanMessage(content="repair it")],
        loaded_agents=[],
        supervisor_tools=[],
        memory_runtime=None,
        scope_resolution_service=None,
        ensure_reasoning_content=lambda message: message,
        sanitize_message_chain=lambda messages, **_kwargs: messages,
        context_orchestrator=None,
        robust_invoke=robust_invoke,
        supervisor_base_llm=object(),
        sup_model_name="test-model",
        caller_kwargs={},
        llm_factory=SimpleNamespace(create_chat_model=lambda *_args, **_kwargs: object()),
        sanitize_response_tool_calls=lambda response: response,
    )

    assert calls == ["invoked"]
    assert response.tool_calls[0]["name"] == "runtime_broker"


def test_direct_writing_skill_plan_hides_supervisor_todo_tools():
    state = {
        "task_shape_hint": {
            "primaryTaskShape": "writing",
            "writingRoute": {
                "present": True,
                "mode": "direct_supervisor",
                "requiresSkillExecution": True,
                "requiresArtifact": False,
                "requiresResearch": False,
                "skillName": "huashu-nuwa",
            },
        }
    }

    assert _should_hide_todo_tools_for_direct_writing(
        state,
        "使用 huashu-nuwa 给我做执行计划，只输出计划，不写文件、不创建 skill。",
    )
    tools = [SimpleNamespace(name="fetch_skill_instructions"), SimpleNamespace(name="write_todos"), SimpleNamespace(name="update_todo")]
    filtered = _filter_tool_names(tools, {"write_todos", "update_todo"})
    assert [tool.name for tool in filtered] == ["fetch_skill_instructions"]


def test_runtime_or_artifact_writing_keeps_supervisor_todo_tools_available():
    state = {
        "task_shape_hint": {
            "primaryTaskShape": "writing",
            "writingRoute": {
                "present": True,
                "mode": "skill_subagent",
                "requiresSkillExecution": True,
                "requiresArtifact": True,
                "requiresResearch": True,
                "recommendedFamily": "writing",
            },
        }
    }

    assert not _should_hide_todo_tools_for_direct_writing(state, "调研后生成 skill 并保存到工作区。")


@pytest.mark.parametrize("active_state", sorted(ACTIVE_EPISODE_STATES))
def test_completion_gate_waits_for_active_runtime_episode(active_state):
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_research", "state": active_state, "kind": "research"}],
        final_text="开始并行搜索~",
    )

    assert decision.action == "waiting_runtime"
    assert decision.reason == "runtime_episode_active_at_stream_end"


def test_completion_gate_blocks_research_plan_claimed_as_ready_evidence():
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_research", "state": "completed", "kind": "research"}],
        handoffs_by_episode={
            "episode_research": [
                {
                    "handoffRefId": "handoff_research",
                    "kind": "research_evidence_bundle",
                    "status": "ready",
                    "runMode": "plan",
                }
            ]
        },
        final_text="现在我重新启动调研。",
    )

    assert decision.action == "fail"
    assert decision.reason == "research_plan_only_claimed_evidence_ready"


def test_completion_gate_blocks_failed_required_write_episode_with_degraded_handoff():
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_engineering", "state": "failed", "kind": "engineering"}],
        handoffs_by_episode={
            "episode_engineering": [
                {
                    "handoffRefId": "handoff_engineering_degraded",
                    "kind": "engineering",
                    "status": "degraded",
                    "engineeringState": "recoverable_failed",
                    "errorCode": "skill_artifact_validation_failed",
                }
            ]
        },
        final_text="技能产物没有通过验证，需要继续修复。",
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_runtime_degraded"
    assert decision.details["nextAction"] == "repair_or_retry_required_write_episode"


def test_completion_gate_blocks_spec_runtime_degraded_handoff_as_delivery():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "tasks",
            "pipelineControl": {
                "runtimeExecutionAllowed": True,
            },
        },
        episodes=[{"episodeId": "episode_engineering", "state": "failed", "kind": "engineering"}],
        handoffs_by_episode={
            "episode_engineering": [
                {
                    "handoffRefId": "handoff_engineering_degraded",
                    "kind": "engineering_patch_bundle",
                    "status": "degraded",
                    "engineeringState": "recoverable_failed",
                    "errorCode": "skill_artifact_validation_failed",
                }
            ]
        },
        final_text="技能产物已经交付。",
    )

    assert decision.action == "fail"
    assert decision.reason == "spec_runtime_execution_degraded"


def test_completion_gate_keeps_spec_stage_waiting_for_approval():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "requirements",
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "requirements",
                "blockedReason": "approval_required",
            },
        },
        final_text="需求文档已生成，请审批。",
    )

    assert decision.action == "waiting_approval"
    assert decision.reason == "approval_required"


def test_completion_gate_rejects_blocked_spec_without_real_pending_approval():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_has_pending_approval=False,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "requirements",
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "requirements",
                "blockedReason": "approval_required",
            },
        },
        final_text="我会继续修改。",
    )

    assert decision.action == "fail"
    assert decision.reason == "spec_stage_blocked_without_pending_approval"


def test_completion_gate_allows_fast_approval_continuation_window():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "requirements",
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "",
                "blockedReason": "",
                "nextStage": "design",
            },
        },
        final_text="需求已经审批，后面继续。",
    )

    assert decision.action == "complete"
    assert decision.reason == "eligible"


@pytest.mark.parametrize(
    ("blocked_reason", "expected_reason"),
    [
        ("stage_format_invalid", "spec_stage_format_invalid"),
        ("stage_analysis_invalid", "spec_stage_analysis_invalid"),
        ("stage_contract_invalid", "spec_stage_contract_invalid"),
    ],
)
def test_completion_gate_does_not_wait_for_invalid_spec_stage(blocked_reason, expected_reason):
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "tasks",
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "",
                "blockedReason": blocked_reason,
                "nextStage": "runtime_execution",
            },
        },
        final_text="任务清单已生成，请审批。",
    )

    assert decision.action == "fail"
    assert decision.reason == expected_reason


def test_completion_gate_fails_runtime_allowed_spec_without_episode():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "tasks",
            "pipelineControl": {
                "runtimeExecutionAllowed": True,
                "blockedByApproval": "",
                "blockedReason": "",
                "nextStage": "runtime_execution",
            },
        },
        episodes=[],
        final_text="我会等待 runtime 执行。",
    )

    assert decision.action == "fail"
    assert decision.reason == "spec_runtime_execution_episode_missing"
    assert decision.details["episodeCount"] == 0


def test_completion_gate_blocks_spec_mode_without_created_stage():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={},
        final_text="接下来我会编写需求文档。",
    )

    assert decision.action == "fail"
    assert decision.reason == "spec_stage_not_created"


def test_completion_spec_brief_uses_current_run_spec_tool_result():
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_brief={}, spec_id=""),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
    )
    expected = {
        "specId": "spec_live",
        "currentStage": "requirements",
        "pipelineControl": {"blockedByApproval": "requirements", "blockedReason": "approval_required"},
    }

    with (
        patch(
            "runtimes.chat.runtime.db.get_runtime_events",
            return_value=[
                {
                    "run_id": "run-spec",
                    "topic": "tool.finished",
                    "payload": {"tool": {"toolName": "spec_broker", "result": {"specId": "spec_live"}}},
                }
            ],
        ),
        patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=[]),
        patch("runtimes.chat.runtime.spec_service.build_brief", return_value=expected) as build_brief,
    ):
        brief = ChatRuntime._completion_spec_brief(chat_run)

    assert brief == expected
    build_brief.assert_called_once_with(workspace_path="E:/Projects/test3", spec_id="spec_live")


def test_completion_spec_brief_extracts_spec_id_from_command_tool_message():
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_brief={}, spec_id=""),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
    )
    expected = {
        "specId": "spec_command_live",
        "currentStage": "requirements",
        "pipelineControl": {"blockedByApproval": "requirements", "blockedReason": "approval_required"},
    }

    with (
        patch(
            "runtimes.chat.runtime.db.get_runtime_events",
            return_value=[
                {
                    "run_id": "run-spec",
                    "topic": "tool.finished",
                    "payload": {
                        "tool": {
                            "toolName": "spec_broker",
                            "result": {
                                "graph": None,
                                "update": {
                                    "messages": [
                                        {
                                            "content": (
                                                '{\n'
                                                '  "ok": true,\n'
                                                '  "kind": "spec_stage_waiting_user_approval",\n'
                                                '  "specId": "spec_command_live"\n'
                                                '}'
                                            )
                                        }
                                    ]
                                },
                            },
                        }
                    },
                }
            ],
        ),
        patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=[]),
        patch("runtimes.chat.runtime.spec_service.build_brief", return_value=expected) as build_brief,
    ):
        brief = ChatRuntime._completion_spec_brief(chat_run)

    assert brief == expected
    build_brief.assert_called_once_with(workspace_path="E:/Projects/test3", spec_id="spec_command_live")


def test_finalize_success_waits_for_spec_stage_approval_from_command_tool_message():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_command_live",
        "currentStage": "requirements",
        "pipelineControl": {"blockedByApproval": "requirements", "blockedReason": "approval_required"},
    }

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="需求文档已准备好，等待审批。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch(
            "runtimes.chat.runtime.db.list_pending_approvals",
            return_value=[{"approval_kind": "spec_stage_approval", "status": "pending"}],
        ),
        patch("runtimes.chat.runtime.spec_service.mark_delivered") as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "waiting_approval"
    run_handle.transition.assert_called_once_with(
        "waiting_approval",
        reason="approval_required",
        node="completion_gate",
    )
    run_handle.complete.assert_not_called()
    mark_delivered.assert_not_called()
    assert emitted[0][0] == "run.completion.waiting_for_spec_approval"
    assert emitted[0][1]["specId"] == "spec_command_live"


def test_finalize_success_fails_runtime_allowed_spec_without_episode():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec_ready", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_ready",
        "currentStage": "tasks",
        "pipelineControl": {"runtimeExecutionAllowed": True},
    }

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="交付完成。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch(
            "runtimes.chat.runtime.spec_service.mark_delivered",
            return_value={"ok": True, "lifecycle": "delivered", "deliveredAt": "2026-06-15T00:00:00Z"},
        ) as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "failed"
    assert result["reason"] == "spec_runtime_execution_episode_missing"
    mark_delivered.assert_not_called()
    run_handle.complete.assert_not_called()
    run_handle.fail.assert_called_once_with("spec_runtime_execution_episode_missing", node="completion_gate")
    assert emitted[0][0] == "run.completion.blocked"
    assert emitted[0][1]["specId"] == "spec_ready"


def test_finalize_success_marks_runtime_allowed_spec_delivered_after_handoff():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec_ready", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_ready",
        "currentStage": "tasks",
        "pipelineControl": {"runtimeExecutionAllowed": True},
    }
    episode = {"episodeId": "episode_engineering", "kind": "engineering", "state": "completed"}
    handoff = {"handoffRefId": "handoff_engineering", "status": "ready", "kind": "engineering_patch_bundle"}

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[episode]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[handoff]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="交付完成。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch(
            "runtimes.chat.runtime.spec_service.mark_delivered",
            return_value={"ok": True, "lifecycle": "delivered", "deliveredAt": "2026-06-15T00:00:00Z"},
        ) as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "finished"
    mark_delivered.assert_called_once_with(
        workspace_path="E:/Projects/test3",
        spec_id="spec_ready",
        run_id="run-spec",
        session_id="session-spec",
    )
    run_handle.complete.assert_called_once_with(reason="stream_finished", node="run_manager")
    assert emitted[0][0] == "spec.lifecycle.delivered"
    assert emitted[0][1]["specId"] == "spec_ready"


def test_finalize_success_blocks_spec_delivered_when_handoff_has_no_proof():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec_ready", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_ready",
        "currentStage": "tasks",
        "pipelineControl": {"runtimeExecutionAllowed": True},
        "traceability": {
            "tasks": [
                {
                    "taskId": "TASK-001",
                    "proofRequired": "changed files and test output",
                    "independentAcceptance": "reviewer can inspect proof",
                }
            ]
        },
    }
    episode = {"episodeId": "episode_engineering", "kind": "engineering", "state": "completed"}
    handoff = {"handoffRefId": "handoff_engineering", "status": "ready", "kind": "engineering_patch_bundle"}

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[episode]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[handoff]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="交付完成。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch("runtimes.chat.runtime.spec_service.mark_delivered") as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "failed"
    assert result["reason"] == "spec_runtime_execution_proof_missing"
    mark_delivered.assert_not_called()
    run_handle.fail.assert_called_once_with("spec_runtime_execution_proof_missing", node="completion_gate")
    assert emitted[0][0] == "run.completion.blocked"
    assert emitted[0][1]["taskIds"] == ["TASK-001"]


def test_finalize_success_marks_spec_delivered_from_brief_workspace_path():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec_ready", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_ready",
        "currentStage": "tasks",
        "workspacePath": "E:/Projects/test3",
        "pipelineControl": {"runtimeExecutionAllowed": True},
    }
    episode = {"episodeId": "episode_engineering", "kind": "engineering", "state": "completed"}
    handoff = {"handoffRefId": "handoff_engineering", "status": "ready", "kind": "engineering_patch_bundle"}

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[episode]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[handoff]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="交付完成。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch(
            "runtimes.chat.runtime.spec_service.mark_delivered",
            return_value={"ok": True, "lifecycle": "delivered", "deliveredAt": "2026-06-15T00:00:00Z"},
        ) as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "finished"
    mark_delivered.assert_called_once_with(
        workspace_path="E:/Projects/test3",
        spec_id="spec_ready",
        run_id="run-spec",
        session_id="session-spec",
    )
    run_handle.complete.assert_called_once_with(reason="stream_finished", node="run_manager")


def test_finalize_success_advisory_does_not_mark_spec_delivered():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec_ready", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_ready",
        "currentStage": "tasks",
        "pipelineControl": {"runtimeExecutionAllowed": True},
    }
    episode = {"episodeId": "episode_engineering", "kind": "engineering", "state": "completed"}
    handoff = {"handoffRefId": "handoff_engineering", "status": "ready", "kind": "engineering_patch_bundle"}

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[episode]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[handoff]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="Next I will inspect the artifacts."),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch("runtimes.chat.runtime.spec_service.mark_delivered") as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "finished"
    mark_delivered.assert_not_called()
    run_handle.fail.assert_not_called()
    run_handle.complete.assert_called_once_with(reason="stream_finished", node="run_manager")
    assert emitted[0][0] == "run.completion.advisory"
    assert emitted[0][1]["reason"] == "forward_only_supervisor_advisory"


def test_finalize_success_waits_for_active_runtime_episode_instead_of_failing():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=False, spec_id="", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-runtime",
        active_run_id="run-runtime",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    episode = {"episodeId": "episode_active", "kind": "engineering", "state": "active"}

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[episode]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="我已经启动工程执行。"),
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "running"
    assert result["reason"] == "runtime_episode_active_at_stream_end"
    run_handle.fail.assert_not_called()
    run_handle.complete.assert_not_called()
    run_handle.transition.assert_called_once_with(
        "running",
        reason="runtime_episode_active_at_stream_end",
        node="completion_gate",
    )


def test_finalize_success_closes_terminal_race_after_arming_runtime_resume(monkeypatch):
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=False, spec_id="", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-runtime",
        active_run_id="run-runtime",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    active_episode = {"episodeId": "episode_runtime", "kind": "engineering", "state": "active"}
    terminal_episode = {"episodeId": "episode_runtime", "kind": "engineering", "state": "completed"}
    scheduled = []
    metadata_updates = []

    monkeypatch.setattr(
        "runtimes.chat.runtime.db.list_runtime_episodes",
        Mock(side_effect=[[active_episode], [terminal_episode]]),
    )
    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episode_handoffs", lambda _episode_id: [])
    monkeypatch.setattr(ChatRuntime, "_completion_final_text", lambda *_args, **_kwargs: "执行仍在进行。")
    monkeypatch.setattr(
        "runtimes.chat.runtime.run_service.update_metadata",
        lambda run_id, updates: metadata_updates.append({"run_id": run_id, "updates": updates}),
    )
    monkeypatch.setattr(
        "erc.command_router.runtime_command_router.schedule_runtime_episode_handoff_resume",
        lambda episode: scheduled.append(dict(episode)) or {"resume_scheduled": True},
    )

    result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "running"
    assert metadata_updates[0]["updates"]["runtimeEpisodeResume"]["state"] == "waiting"
    assert scheduled == [terminal_episode]
    run_handle.complete.assert_not_called()
    run_handle.fail.assert_not_called()


def test_finalize_success_does_not_arm_resume_for_terminal_spec_handoff_pending(monkeypatch):
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec-runtime", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-runtime",
        active_run_id="run-runtime",
        run_handle=run_handle,
        emit_runtime_event=lambda *_args, **_kwargs: None,
    )
    terminal_episode = {"episodeId": "episode_runtime", "kind": "engineering", "state": "completed"}
    metadata_updates = []
    scheduled = []

    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episodes", lambda **_kwargs: [terminal_episode])
    monkeypatch.setattr("runtimes.chat.runtime.db.list_runtime_episode_handoffs", lambda _episode_id: [])
    monkeypatch.setattr(ChatRuntime, "_completion_final_text", lambda *_args, **_kwargs: "执行已结束。")
    monkeypatch.setattr(
        ChatRuntime,
        "_completion_spec_brief",
        lambda *_args, **_kwargs: {
            "specId": "spec-runtime",
            "status": "active",
            "pipelineControl": {"runtimeExecutionAllowed": True},
        },
    )
    monkeypatch.setattr(
        "runtimes.chat.runtime.run_service.update_metadata",
        lambda run_id, updates: metadata_updates.append({"run_id": run_id, "updates": updates}),
    )
    monkeypatch.setattr(
        "erc.command_router.runtime_command_router.schedule_runtime_episode_handoff_resume",
        lambda episode: scheduled.append(dict(episode)) or {"resume_scheduled": True},
    )

    result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "running"
    assert result["reason"] == "spec_runtime_execution_handoff_pending"
    assert metadata_updates == []
    assert scheduled == []
    run_handle.complete.assert_not_called()
    run_handle.fail.assert_not_called()


def test_system_resume_restores_persisted_safety_mode_and_workspace_binding(monkeypatch):
    monkeypatch.setattr(
        "runtimes.chat.runtime.db.get_run_record",
        lambda _run_id: {"metadata": {"safetyApprovalMode": "reduced"}},
    )
    chat_run = SimpleNamespace(
        request=SimpleNamespace(data=None, resume_value={}),
        active_run_id="run-resume",
        session_id="session-resume",
        user_id="user-demo",
        transport="runtime_episode_handoff",
        prepared=SimpleNamespace(latest_user_content="继续处理", spec_id="", spec_brief={}, live_audit_context={}),
        scope_result=SimpleNamespace(
            binding=SimpleNamespace(
                project_id="project-resume",
                workspace_id="workspace-resume",
                workspace_path="E:/Projects/resume",
                resolved_scope="project:project-resume",
            )
        ),
    )
    runtime = ChatRuntime()

    context = runtime._runtime_context_kwargs(chat_run)
    restarted = runtime._restart_route_context(
        chat_run,
        {"current_route_context": {"safetyApprovalMode": "reduced"}},
    )

    assert context["safetyApprovalMode"] == "reduced"
    assert context["workspace_binding"]["projectId"] == "project-resume"
    assert context["workspace_binding"]["workspaceId"] == "workspace-resume"
    assert restarted["safetyApprovalMode"] == "reduced"
    assert restarted["workspaceBinding"]["projectId"] == "project-resume"
    assert restarted["workspaceBinding"]["workspaceId"] == "workspace-resume"


@pytest.mark.parametrize("terminal_state", ["completed", "degraded", "failed", "cancelled"])
def test_runtime_episode_handoff_resume_enters_wait_episode_state(monkeypatch, terminal_state):
    captured = {}

    async def fake_create_execution_bundle(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(graph=None, payload=None, graph_config={}, mode="start", diagnostics={})

    monkeypatch.setattr("runtimes.chat.runtime.supervisor_runner.create_execution_bundle", fake_create_execution_bundle)
    monkeypatch.setattr("graph.workflow_assembly.emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    request = ChatRequest(
        messages=[
            ChatMessage(
                role="user",
                content="[Runtime Episode Terminal]\nepisodeId: episode_runtime",
            )
        ],
        config=EngineConfig(provider="deepseek", model_name="deepseek-v4-flash"),
        session_id="session-runtime",
        conversation_id="session-runtime",
        user_id="user-demo",
        workspace_path="E:/Projects/test3",
        workspace_id="test3",
        scope_mode="explicit",
        resume_run_id="run-runtime",
        resume_value={
            "runtimeEpisodeHandoff": {
                "episodeId": "episode_runtime",
                "episodeKind": "engineering",
                "episodeState": terminal_state,
                "specId": "spec_runtime",
            }
        },
        data=ChatRequestData(
            specMode=True,
            specId="spec_runtime",
            safetyApprovalMode="minimal",
        ),
    )
    runtime = ChatRuntime()
    prepared = runtime.prepare_request(request)
    prepared.spec_brief = {
        "specId": "spec_runtime",
        "pipelineControl": {"runtimeExecutionAllowed": True},
    }
    chat_run = SimpleNamespace(
        prepared=prepared,
        request=request,
        session_id="session-runtime",
        user_id="user-demo",
        active_run_id="run-runtime",
        run_handle=SimpleNamespace(run_id="run-runtime"),
        scope_result=SimpleNamespace(
            binding=SimpleNamespace(
                project_id="test3",
                workspace_id="test3",
                workspace_path="E:/Projects/test3",
                resolved_scope="project:test3",
            )
        ),
        transport="system_resume",
        lc_messages=prepared.lc_messages,
        emit_runtime_event=lambda *_args, **_kwargs: None,
    )

    asyncio.run(runtime.create_execution_bundle(chat_run=chat_run))

    assert captured["runtime_dispatch_status"]["nextAction"] == "wait_episode"
    assert captured["runtime_dispatch_status"]["state"] == "handoff_resume_requested"
    route_context = captured["current_route_context"]
    assert route_context["projectId"] == "test3"
    assert route_context["workspaceId"] == "test3"
    assert route_context["workspacePath"] == "E:/Projects/test3"
    assert route_context["resolvedScope"] == "project:test3"
    assert route_context["safetyApprovalMode"] == "minimal"
    assert route_context["workspaceBinding"]["projectId"] == "test3"
    assert route_context["workspaceBinding"]["workspaceId"] == "test3"
    assert route_context["runtimeEpisodeHandoffResume"]["episodeId"] == "episode_runtime"
    episodes = route_context["capabilityEpisodes"]
    assert episodes and episodes[-1]["episodeId"] == "episode_runtime"
    assert episodes[-1]["state"] == terminal_state
    assert "planner_plan" not in captured


def test_runtime_episode_handoff_resume_restarts_wait_graph_instead_of_resuming_completed_checkpoint(monkeypatch):
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        is_resume_request=True,
        request=SimpleNamespace(
            resume_value={
                "runtimeEpisodeHandoff": {
                    "episodeId": "episode-runtime-ready",
                    "episodeKind": "delegation",
                    "episodeState": "completed",
                }
            }
        ),
    )
    calls = []

    async def _create_execution_bundle(*, chat_run):
        calls.append("start")
        return "fresh-runtime-handoff-graph"

    async def _create_resume_bundle(*, chat_run):
        calls.append("resume")
        return "completed-checkpoint-resume"

    monkeypatch.setattr(runtime, "create_execution_bundle", _create_execution_bundle)
    monkeypatch.setattr(runtime, "create_resume_bundle", _create_resume_bundle)

    result = asyncio.run(runtime.resolve_execution_bundle(chat_run=chat_run))

    assert result == "fresh-runtime-handoff-graph"
    assert calls == ["start"]


def test_client_transport_disconnect_cancels_a_phantom_running_run(monkeypatch):
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(active_run_id="run-client-disconnect")
    cancelled = []

    monkeypatch.setattr(
        "runtimes.chat.runtime.db.get_run_record",
        lambda run_id: {"id": run_id, "status": "running"},
    )
    monkeypatch.setattr(
        "runtimes.chat.runtime.erc_kernel.cancel_run",
        lambda run_id, *, reason: cancelled.append((run_id, reason)),
    )

    assert runtime.finalize_client_transport_disconnect(chat_run, transport="websocket") is True
    assert cancelled == [("run-client-disconnect", "websocket_client_disconnected")]


def test_durable_submit_disconnect_does_not_cancel_engine_owned_run(monkeypatch):
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(active_run_id="run-durable-submit")
    cancel_run = Mock()

    monkeypatch.setattr(
        "runtimes.chat.runtime.db.get_run_record",
        lambda run_id: {"id": run_id, "status": "running"},
    )
    monkeypatch.setattr("runtimes.chat.runtime.erc_kernel.cancel_run", cancel_run)

    assert runtime.finalize_client_transport_disconnect(chat_run, transport="submit") is False
    cancel_run.assert_not_called()
