from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from core.database import DatabaseManager
from core.runtime_continuation import (
    RuntimeContinuationContractError,
    build_runtime_continuation_request,
    validate_runtime_continuation_answers,
)
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_projection import (
    project_runtime_timeline_from_events,
    select_runtime_timeline_window,
)
from core.tools.native.delegation import delegation_broker
from core.tools.native.runtime import runtime_broker
from erc.runtime_context import bind_runtime_context
from graph.parallel_support import (
    _creative_tool_evidence,
    _run_parallel_agent_branch,
    _subagent_runtime_input_request,
)


def _creative_branch() -> dict:
    return {
        "invocationId": "invoke-creative-1",
        "delegationId": "delegation-creative-1",
        "taskBriefId": "brief-creative-1",
        "agentId": "creative-worker",
        "agentName": "Creative Worker",
        "reason": "Create and verify the requested media.",
        "taskBrief": {
            "taskBriefId": "brief-creative-1",
            "runtimeAccess": ["creative_media.core"],
            "requiredCapabilities": ["artifact_handoff", "quality_assurance"],
            "context": {"parentRuntimeEpisodeId": "episode-creative-1"},
        },
    }


def _continuation_request(*, tool_call_id: str = "call-request-input") -> dict:
    return build_runtime_continuation_request(
        required_inputs=[
            {
                "id": "format",
                "kind": "enum",
                "question": "Which output format should be used?",
                "options": ["png", "svg"],
            },
            {
                "id": "includeCaption",
                "kind": "boolean",
                "question": "Should the output include a caption?",
            },
        ],
        summary="The provider needs an explicit output choice.",
        source={
            "sessionId": "session-creative-1",
            "runId": "run-creative-1",
            "runtimeEpisodeId": "episode-creative-1",
            "taskBriefId": "brief-creative-1",
            "delegationId": "delegation-creative-1",
            "agentId": "creative-worker",
            "toolCallId": tool_call_id,
        },
    )


def _tool_payload(command: Command) -> dict:
    message = list((command.update or {}).get("messages") or [])[-1]
    return json.loads(str(message.content))


def test_direct_subagent_emits_typed_continuation_with_branch_lineage() -> None:
    branch = _creative_branch()
    state = {
        "messages": [],
        "todos": [],
        "session_id": "session-creative-1",
        "run_id": "run-creative-1",
        "parallel_branch": branch,
    }
    with bind_runtime_context(
        runtime_kind="subagent",
        actor_role="direct_subagent",
        agent_id="creative-worker",
        delegation_id="delegation-creative-1",
        delegation_depth=1,
        session_id="session-creative-1",
        run_id="run-creative-1",
        runtime_episode_id="episode-creative-1",
    ):
        command = delegation_broker.func(
            mode="request_input",
            required_inputs=[
                {
                    "id": "format",
                    "kind": "enum",
                    "question": "Which output format should be used?",
                    "options": ["png", "svg"],
                }
            ],
            continuation_summary="The provider needs an explicit output choice.",
            state=state,
            tool_call_id="call-request-input",
        )

    payload = _tool_payload(command)
    request = payload["continuationRequest"]
    assert command.goto == "supervisor"
    assert payload["mode"] == "request_input"
    assert request["resumePolicy"] == "same_episode"
    assert request["source"] == {
        "sessionId": "session-creative-1",
        "runId": "run-creative-1",
        "runtimeEpisodeId": "episode-creative-1",
        "taskBriefId": "brief-creative-1",
        "delegationId": "delegation-creative-1",
        "agentId": "creative-worker",
        "toolCallId": "call-request-input",
    }


def test_prose_missing_parameter_marker_cannot_pause_runtime() -> None:
    request = _continuation_request()
    prose = AIMessage(
        content=(
            "[V8OS_RUNTIME_INPUT_REQUIRED]\n"
            + json.dumps({"continuationRequest": request}, ensure_ascii=False)
        )
    )

    assert (
        _subagent_runtime_input_request(
            [prose],
            branch=_creative_branch(),
            agent_id="creative-worker",
        )
        is None
    )


def test_typed_continuation_requires_real_broker_call_and_matching_lineage() -> None:
    request = _continuation_request()
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-request-input",
                    "name": "delegation_broker",
                    "args": {"mode": "request_input"},
                }
            ],
        ),
        ToolMessage(
            name="delegation_broker",
            tool_call_id="call-request-input",
            content=json.dumps(
                {
                    "ok": True,
                    "mode": "request_input",
                    "continuationRequest": request,
                }
            ),
        ),
    ]

    assert (
        _subagent_runtime_input_request(
            messages,
            branch=_creative_branch(),
            agent_id="creative-worker",
        )["requestId"]
        == request["requestId"]
    )

    mismatched_branch = _creative_branch()
    mismatched_branch["delegationId"] = "delegation-other"
    with pytest.raises(
        RuntimeContinuationContractError,
        match="delegationId",
    ):
        _subagent_runtime_input_request(
            messages,
            branch=mismatched_branch,
            agent_id="creative-worker",
        )


@pytest.mark.parametrize(
    ("answers", "error_code"),
    [
        ({"format": "png"}, "runtime_resume_inputs_missing"),
        (
            {"format": "png", "includeCaption": True, "extra": "no"},
            "runtime_resume_inputs_unknown",
        ),
        (
            {"format": "jpg", "includeCaption": True},
            "runtime_resume_input_option_invalid",
        ),
        (
            {"format": "png", "includeCaption": "true"},
            "runtime_resume_input_type_invalid",
        ),
    ],
)
def test_continuation_answers_are_strictly_validated(answers: dict, error_code: str) -> None:
    with pytest.raises(RuntimeContinuationContractError) as exc_info:
        validate_runtime_continuation_answers(_continuation_request(), answers)
    assert exc_info.value.code == error_code


def test_waiting_episode_can_resume_after_database_reopen(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "state.db"
    first_process_db = DatabaseManager(database_path)
    first_process_db.create_or_update_session("session-creative-1", "Creative continuation")
    first_process_db.create_run_record("run-creative-1", "session-creative-1")
    first_process_db.upsert_runtime_episode_record(
        {
            "episodeId": "episode-creative-1",
            "kind": "creative_media",
            "state": "waiting_input",
        },
        session_id="session-creative-1",
        run_id="run-creative-1",
    )
    request = _continuation_request()
    first_process_db.add_runtime_episode_handoff(
        episode_id="episode-creative-1",
        session_id="session-creative-1",
        run_id="run-creative-1",
        handoff={
            "handoffId": "handoff-continuation-1",
            "kind": "runtime_input_required",
            "status": "waiting_input",
            "continuationRequest": request,
        },
    )

    restarted_db = DatabaseManager(database_path)
    monkeypatch.setattr("core.tools.native.runtime.db", restarted_db)
    monkeypatch.setattr("core.tools.native.runtime._emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    rejected = runtime_broker.func(
        mode="resume",
        episode_id="episode-creative-1",
        continuation_request_id="continuation_000000000000",
        continuation_inputs={"format": "png", "includeCaption": False},
        state={"session_id": "session-creative-1", "current_route_context": {}},
        tool_call_id="call-runtime-resume-wrong-request",
    )
    assert _tool_payload(rejected)["error"] == "runtime_continuation_request_mismatch"
    assert restarted_db.get_runtime_episode("episode-creative-1")["state"] == "waiting_input"

    command = runtime_broker.func(
        mode="resume",
        episode_id="episode-creative-1",
        continuation_request_id=request["requestId"],
        continuation_inputs={"format": "png", "includeCaption": False},
        state={"session_id": "session-creative-1", "current_route_context": {}},
        tool_call_id="call-runtime-resume",
    )

    resumed = restarted_db.get_runtime_episode("episode-creative-1")
    assert command.update["runtime_dispatch_status"]["dispatched"] is True
    assert resumed["state"] == "queued"
    assert resumed["resumeToken"]["continuationRequestId"] == request["requestId"]
    assert resumed["resumeToken"]["continuationInputs"] == {
        "format": "png",
        "includeCaption": False,
    }
    queued = restarted_db.list_runtime_episode_queue(active_only=True)
    assert queued[0]["episode_id"] == "episode-creative-1"
    assert queued[0]["state"] == "queued"


def test_creative_evidence_comes_only_from_matched_successful_tool_messages() -> None:
    branch = _creative_branch()
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-artifact",
                    "name": "creative_media_jobs",
                    "args": {"action": "artifacts"},
                },
                {
                    "id": "call-quality",
                    "name": "creative_media_quality",
                    "args": {"action": "qa_check"},
                },
            ],
        ),
        ToolMessage(
            name="creative_media_jobs",
            tool_call_id="call-artifact",
            content=json.dumps(
                {
                    "ok": True,
                    "facade": "jobs",
                    "action": "artifacts",
                    "status": "ready",
                    "summary": "Artifact ready.",
                    "refs": ["artifact://image-1"],
                }
            ),
        ),
        ToolMessage(
            name="creative_media_quality",
            tool_call_id="call-quality",
            content=json.dumps(
                {
                    "ok": True,
                    "facade": "quality",
                    "action": "qa_check",
                    "status": "passed",
                    "summary": "QA passed.",
                    "refs": ["proof://qa-1"],
                    "detailRef": "detail://qa-1",
                }
            ),
        ),
        AIMessage(content="Also claim artifact://invented and proof://invented."),
        ToolMessage(
            name="creative_media_jobs",
            tool_call_id="unmatched-call",
            content=json.dumps(
                {
                    "ok": True,
                    "facade": "jobs",
                    "action": "artifacts",
                    "status": "ready",
                    "refs": ["artifact://unmatched"],
                }
            ),
        ),
    ]

    evidence = _creative_tool_evidence(messages, branch=branch)
    assert evidence["sourceRuntimeEpisodeId"] == "episode-creative-1"
    assert evidence["artifactRefs"] == ["artifact://image-1"]
    assert evidence["proofRefs"] == ["proof://qa-1", "detail://qa-1"]
    assert evidence["missingEvidence"] == []
    assert {record["toolCallId"] for record in evidence["records"]} == {
        "call-artifact",
        "call-quality",
    }


def test_runtime_runner_rejects_creative_evidence_from_another_episode() -> None:
    valid_evidence = _creative_tool_evidence(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-artifact",
                        "name": "creative_media_jobs",
                        "args": {"action": "artifacts"},
                    },
                    {
                        "id": "call-quality",
                        "name": "creative_media_quality",
                        "args": {"action": "qa_check"},
                    },
                ],
            ),
            ToolMessage(
                name="creative_media_jobs",
                tool_call_id="call-artifact",
                content=json.dumps(
                    {
                        "ok": True,
                        "facade": "jobs",
                        "action": "artifacts",
                        "status": "ready",
                        "refs": ["artifact://image-1"],
                    }
                ),
            ),
            ToolMessage(
                name="creative_media_quality",
                tool_call_id="call-quality",
                content=json.dumps(
                    {
                        "ok": True,
                        "facade": "quality",
                        "action": "qa_check",
                        "status": "passed",
                        "refs": ["proof://qa-1"],
                    }
                ),
            ),
        ],
        branch=_creative_branch(),
    )
    handoff = {"results": [{"creativeExecutionEvidence": valid_evidence}]}

    accepted = RuntimeEpisodeRunner._creative_evidence_from_delegation_handoff(
        handoff,
        parent_episode_id="episode-creative-1",
    )
    rejected = RuntimeEpisodeRunner._creative_evidence_from_delegation_handoff(
        handoff,
        parent_episode_id="episode-other",
    )

    assert accepted["artifactRefs"] == ["artifact://image-1"]
    assert accepted["proofRefs"] == ["proof://qa-1"]
    assert rejected["artifactRefs"] == []
    assert rejected["proofRefs"] == []


@pytest.mark.parametrize(
    ("request_mode", "error_code"),
    [
        ("missing", "runtime_continuation_request_invalid"),
        ("wrong_episode", "runtime_continuation_episode_mismatch"),
    ],
)
def test_creative_runtime_rejects_unresumable_waiting_handoff(
    monkeypatch,
    request_mode: str,
    error_code: str,
) -> None:
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        creative_media_runtime,
        "compile_recipe",
        lambda _request: {"recipeId": "recipe-test", "providerStatus": "ready"},
    )
    continuation_request = None
    if request_mode == "wrong_episode":
        continuation_request = build_runtime_continuation_request(
            required_inputs=[
                {
                    "id": "format",
                    "kind": "enum",
                    "question": "Which output format should be used?",
                    "options": ["png", "svg"],
                }
            ],
            summary="Choose the output format.",
            source={"runtimeEpisodeId": "episode-other"},
        )

    async def _waiting_delegation(_episode: dict) -> dict:
        return {
            "status": "waiting_input",
            "requiredInputs": [
                {
                    "id": "format",
                    "kind": "enum",
                    "question": "Which output format should be used?",
                    "options": ["png", "svg"],
                }
            ],
            **(
                {"continuationRequest": continuation_request}
                if continuation_request is not None
                else {}
            ),
        }

    monkeypatch.setattr(runner, "_execute_delegation", _waiting_delegation)
    handoff = asyncio.run(
        runner._execute_creative_media(
            {
                "episodeId": "episode-creative-1",
                "kind": "creative_media",
                "inputs": {"request": {"prompt": "Create a test image."}},
                "need": {"reason": "Create a test image."},
            }
        )
    )

    assert handoff["status"] == "failed"
    assert handoff["errorCode"] == error_code
    assert handoff["handoffStage"] == "continuation_contract_failed"


def test_creative_branch_stops_after_two_in_branch_evidence_corrections() -> None:
    calls: list[list] = []

    def _node_func(state: dict) -> Command:
        calls.append(list(state.get("messages") or []))
        return Command(
            goto="supervisor",
            update={"messages": [AIMessage(content="The creative task is complete.")]},
        )

    delta_messages, _todos, summary, child_requests = asyncio.run(
        _run_parallel_agent_branch(
            {"messages": [], "todos": [], "parallel_branch": _creative_branch()},
            {"node_func": _node_func, "tool_mode": "test"},
        )
    )

    corrections = [
        message
        for message in delta_messages
        if isinstance(message, HumanMessage)
        and message.additional_kwargs.get("v8_governance_type")
        == "creative_delivery_evidence_correction"
    ]
    assert len(calls) == 3
    assert [message.additional_kwargs["v8_correction_attempt"] for message in corrections] == [1, 2]
    assert child_requests == []
    assert summary["status"] == "failed"
    assert summary["error"] == "creative_media_delivery_evidence_missing"
    assert summary["creativeExecutionEvidence"]["missingEvidence"] == [
        "artifactRefs",
        "proofRefs",
    ]


def test_shared_runtime_timeline_retains_wait_resume_order_for_web_and_phone() -> None:
    events = []
    for seq, topic, state in [
        (1, "runtime.episode.started", "active"),
        (2, "runtime.episode.waiting_input", "waiting_input"),
        (3, "runtime.episode.resumed", "queued"),
        (4, "runtime.episode.completed", "completed"),
    ]:
        events.append(
            {
                "event_id": f"evt-{seq}",
                "run_id": "run-creative-1",
                "seq": seq,
                "topic": topic,
                "payload": {
                    "episode": {
                        "episodeId": "episode-creative-1",
                        "kind": "creative_media",
                        "state": state,
                    }
                },
                "source": {"agent_id": "supervisor"},
            }
        )

    full_timeline = project_runtime_timeline_from_events(events)
    compact_timeline = select_runtime_timeline_window(
        full_timeline,
        recent_limit=2,
        milestone_limit=4,
    )

    assert [item["topic"] for item in compact_timeline] == [
        "runtime.episode.started",
        "runtime.episode.waiting_input",
        "runtime.episode.resumed",
        "runtime.episode.completed",
    ]
    assert [item["seq"] for item in compact_timeline] == [1, 2, 3, 4]
    assert {item["metadata"]["episodeId"] for item in compact_timeline} == {
        "episode-creative-1"
    }
