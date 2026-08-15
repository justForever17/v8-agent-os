from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

import core.runtime_episode_runner as runtime_episode_runner_module
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

    with restarted_db.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes SET state = 'waiting_input', worker_id = NULL, lease_expires_at = NULL WHERE id = ?",
            ("episode-creative-1",),
        )
        conn.commit()
    monkeypatch.setattr(restarted_db, "resume_runtime_episode", lambda *_args, **_kwargs: None)
    rejected_cas = runtime_broker.func(
        mode="resume",
        episode_id="episode-creative-1",
        continuation_request_id=request["requestId"],
        continuation_inputs={"format": "png", "includeCaption": False},
        state={"session_id": "session-creative-1", "current_route_context": {}},
        tool_call_id="call-runtime-resume-cas-rejected",
    )
    assert _tool_payload(rejected_cas)["error"] == "runtime_episode_resume_conflict"
    assert rejected_cas.update["runtime_dispatch_status"]["blocked"] is True
    assert restarted_db.get_runtime_episode("episode-creative-1")["state"] == "waiting_input"


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


def test_non_exact_creative_episode_preserves_source_brief_for_director(monkeypatch) -> None:
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    captured: dict = {}
    source_brief = {
        "taskBriefId": "creative-source-brief",
        "goal": "Replace only the masked head while preserving the suit, pose, lighting, and canvas size.",
        "context": {
            "sourceIds": ["source-image"],
            "maskSourceId": "head-mask",
            "mustKeep": ["suit", "pose", "lighting", "canvas size"],
        },
        "constraints": ["Do not alter pixels outside the mask", "Return one edited image"],
        "acceptanceContract": ["Only the masked head changes"],
        "toolPolicy": {
            "mode": "allowlist",
            "allowedTools": ["creative_media_edit", "inspect_image"],
            "forbiddenTools": [],
        },
    }
    plugin_references = [{"pluginId": "media-kit", "componentIds": ["image-edit"]}]
    extension_route_context = {
        "extensionSelectorsAuthoritative": True,
        "selectedSkillIds": ["skill:image-edit-method"],
        "selectedSkillNames": ["Image Edit Method"],
        "selectedMcpTools": ["inspect_image"],
    }

    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)

    def _compile_recipe(request: dict) -> dict:
        captured["request"] = dict(request)
        return {"recipeId": "recipe-preserved", "providerStatus": "ready"}

    async def _capture_director(episode: dict) -> dict:
        captured["episode"] = episode
        return {"status": "failed", "compactSummary": "captured director handoff"}

    monkeypatch.setattr(creative_media_runtime, "compile_recipe", _compile_recipe)
    monkeypatch.setattr(runner, "_execute_delegation", _capture_director)

    handoff = asyncio.run(
        runner._execute_creative_media(
            {
                "episodeId": "episode-creative-preserved",
                "kind": "creative_media",
                "inputs": {
                    "request": {"modality": "image", "prompt": "generic provider fallback"},
                    "taskBriefs": [source_brief],
                    "pluginReferences": plugin_references,
                    **extension_route_context,
                    "extensionRouteContext": extension_route_context,
                },
                "need": {"reason": "generic runtime fallback"},
            }
        )
    )

    director_brief = captured["episode"]["inputs"]["workerBriefs"][0]
    assert captured["request"]["prompt"] == source_brief["goal"]
    assert captured["request"]["taskBriefContext"] == source_brief["context"]
    assert director_brief["goal"].startswith(source_brief["goal"])
    assert director_brief["context"]["sourceTaskBriefs"] == [source_brief]
    assert director_brief["pluginReferences"] == plugin_references
    assert director_brief["toolPolicy"] == source_brief["toolPolicy"]
    delegated_inputs = captured["episode"]["inputs"]
    assert delegated_inputs["extensionRouteContext"] == extension_route_context
    assert delegated_inputs["selectedMcpTools"] == ["inspect_image"]
    assert handoff["status"] == "failed"


def _typed_creative_episode(*, contract: dict, tmp_path) -> dict:
    return {
        "episodeId": "episode-typed-creative",
        "kind": "creative_media",
        "sessionId": "session-typed-creative",
        "runId": "run-typed-creative",
        "inputs": {
            "workspacePath": str(tmp_path),
            "workspaceId": "workspace-typed-creative",
            "projectId": "project-typed-creative",
            "taskBriefs": [
                {
                    "taskBriefId": "typed-creative-task",
                    "goal": "Execute the exact media operation.",
                    "context": {"canvasExecutionContract": contract},
                }
            ],
        },
        "need": {"reason": "Execute exact media operation."},
    }


def _canvas_edit_contract() -> dict:
    return {
        "schema": "v8.creative_canvas_task.v1",
        "canvasOperationId": "canvas-operation-exact",
        "actionId": "creative_media.edit_image_region",
        "output": {"kind": "artifact", "slot": "image_derivative"},
        "resources": {
            "sourceIds": ["source-exact"],
            "maskSourceId": "mask-exact",
        },
        "execution": {
            "tool": "creative_media_jobs",
            "arguments": {
                "action": "create",
                "request": {
                    "modality": "image",
                    "operationKind": "image.edit",
                    "prompt": "Replace only the masked region.",
                    "canvasOperationId": "canvas-operation-exact",
                    "sourceId": "source-exact",
                    "maskSourceId": "mask-exact",
                },
            },
        },
    }


def test_generic_creative_execution_contract_is_not_canvas_specific(tmp_path) -> None:
    contract = {
        "schema": "v8.creative_media_execution.v1",
        "execution": {
            "tool": "creative_media_jobs",
            "arguments": {
                "action": "create",
                "request": {
                    "modality": "image",
                    "operationKind": "image.generate",
                    "prompt": "Create the approved abstract cover.",
                },
            },
        },
    }
    episode = _typed_creative_episode(contract=contract, tmp_path=tmp_path)
    context = episode["inputs"]["taskBriefs"][0]["context"]
    context["creativeMediaExecutionContract"] = context.pop("canvasExecutionContract")

    executions = RuntimeEpisodeRunner._typed_creative_media_executions(episode)

    assert len(executions) == 1
    assert executions[0]["sourceSchema"] == "v8.creative_media_execution.v1"
    assert executions[0]["arguments"] == contract["execution"]["arguments"]


def test_typed_creative_episode_preserves_exact_job_contract_and_skips_director(
    monkeypatch,
    tmp_path,
) -> None:
    from core.tools.native.creative_media_facade import creative_media_jobs
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    captured: dict = {}
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)

    async def _facade_call(_tool, payload: dict, *args, **kwargs) -> str:
        del args, kwargs
        captured.update(payload)
        return json.dumps(
            {
                "ok": True,
                "status": "succeeded",
                "refs": ["cm_exact", "art_exact"],
                "detailRef": "toolobs://exact-edit",
            }
        )

    async def _director_must_not_run(_episode: dict) -> dict:
        raise AssertionError("Creative Media Director must not reinterpret an exact execution")

    monkeypatch.setattr(type(creative_media_jobs), "ainvoke", _facade_call)
    monkeypatch.setattr(runner, "_execute_delegation", _director_must_not_run)
    monkeypatch.setattr(creative_media_runtime, "list_jobs", lambda **_kwargs: [])
    monkeypatch.setattr(
        creative_media_runtime,
        "get_job",
        lambda *_args, **_kwargs: {
            "jobId": "cm_exact",
            "status": "succeeded",
            "sessionId": "session-typed-creative",
            "runId": "run-typed-creative",
            "workspacePath": str(tmp_path),
            "workspaceId": "workspace-typed-creative",
            "projectId": "project-typed-creative",
            "modality": "image",
            "operationKind": "image.edit",
            "canvasOperationId": "canvas-operation-exact",
            "sourceId": "source-exact",
            "maskSourceId": "mask-exact",
            "outputKind": "artifact",
            "outputSlot": "image_derivative",
            "request": {
                "modality": "image",
                "operationKind": "image.edit",
                "canvasOperationId": "canvas-operation-exact",
                "sourceId": "source-exact",
                "maskSourceId": "mask-exact",
                "outputKind": "artifact",
                "outputSlot": "image_derivative",
            },
            "artifacts": [{"artifactId": "art_exact", "mimeType": "image/png"}],
        },
    )

    handoff = asyncio.run(
        runner._execute_creative_media(
            _typed_creative_episode(contract=_canvas_edit_contract(), tmp_path=tmp_path)
        )
    )

    expected_request = {
        **_canvas_edit_contract()["execution"]["arguments"]["request"],
        "outputKind": "artifact",
        "outputSlot": "image_derivative",
    }
    assert captured == {
        "action": "create",
        "request": expected_request,
    }
    assert handoff["status"] == "ready"
    assert handoff["artifactRefs"] == ["art_exact"]
    assert "creative-media-job://cm_exact" in handoff["proofRefs"]
    assert handoff["taskBriefIds"] == ["typed-creative-task"]
    assert handoff["coverageComplete"] is True
    assert handoff["terminalEpisode"] is True
    assert handoff["handoffStage"] == "typed_execution_delivered"


def test_typed_creative_episode_inherits_workspace_identity_from_session_binding(
    monkeypatch,
    tmp_path,
) -> None:
    from core.tools.native.creative_media_facade import creative_media_jobs
    from erc.runtime_context import get_runtime_context
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    captured_context: dict = {}
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_episode_runner_module.db,
        "get_session_scope_binding",
        lambda _session_id: {
            "workspace_id": "workspace-from-binding",
            "project_id": "project-from-binding",
            "workspace_path": str(tmp_path),
        },
    )

    async def _facade_call(_tool, _payload: dict, *args, **kwargs) -> str:
        del args, kwargs
        captured_context.update(dict(get_runtime_context() or {}))
        return json.dumps({"ok": True, "status": "succeeded", "refs": ["cm_bound"]})

    monkeypatch.setattr(type(creative_media_jobs), "ainvoke", _facade_call)
    monkeypatch.setattr(creative_media_runtime, "list_jobs", lambda **_kwargs: [])
    monkeypatch.setattr(
        creative_media_runtime,
        "get_job",
        lambda *_args, **_kwargs: {
            "jobId": "cm_bound",
            "status": "succeeded",
            "sessionId": "session-typed-creative",
            "runId": "run-typed-creative",
            "workspacePath": str(tmp_path),
            "workspaceId": "workspace-from-binding",
            "projectId": "project-from-binding",
            "modality": "image",
            "operationKind": "image.edit",
            "canvasOperationId": "canvas-operation-exact",
            "sourceId": "source-exact",
            "maskSourceId": "mask-exact",
            "outputKind": "artifact",
            "outputSlot": "image_derivative",
            "artifacts": [{"artifactId": "art_bound", "mimeType": "image/png"}],
        },
    )
    episode = _typed_creative_episode(contract=_canvas_edit_contract(), tmp_path=tmp_path)
    episode["inputs"].pop("workspaceId")
    episode["inputs"].pop("projectId")

    handoff = asyncio.run(runner._execute_creative_media(episode))

    assert handoff["status"] == "ready"
    assert captured_context["workspace_id"] == "workspace-from-binding"
    assert captured_context["project_id"] == "project-from-binding"
    assert captured_context["workspace_path"] == str(tmp_path)


def test_typed_creative_failure_is_terminal_and_never_falls_back_to_director(
    monkeypatch,
    tmp_path,
) -> None:
    from core.tools.native.creative_media_facade import creative_media_jobs
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    director_calls = 0
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)

    async def _facade_call(_tool, _payload: dict, *args, **kwargs) -> str:
        del args, kwargs
        return json.dumps(
            {
                "ok": False,
                "status": "failed",
                "summary": "edit model unavailable",
                "error": {"code": "operation_unavailable", "message": "edit model unavailable"},
            }
        )

    async def _director(_episode: dict) -> dict:
        nonlocal director_calls
        director_calls += 1
        return {"status": "ready"}

    monkeypatch.setattr(type(creative_media_jobs), "ainvoke", _facade_call)
    monkeypatch.setattr(runner, "_execute_delegation", _director)
    monkeypatch.setattr(creative_media_runtime, "list_jobs", lambda **_kwargs: [])
    handoff = asyncio.run(
        runner._execute_creative_media(
            _typed_creative_episode(contract=_canvas_edit_contract(), tmp_path=tmp_path)
        )
    )

    assert handoff["status"] == "failed"
    assert handoff["errorCode"] == "operation_unavailable"
    assert handoff["recoverable"] is False
    assert director_calls == 0


def test_canvas_typed_execution_reuses_one_existing_durable_job(
    monkeypatch,
    tmp_path,
) -> None:
    from core.tools.native.creative_media_facade import creative_media_jobs
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_emit", lambda *_args, **_kwargs: None)

    existing_job = {
        "jobId": "cm_existing_exact",
        "status": "succeeded",
        "sessionId": "session-typed-creative",
        "runId": "run-typed-creative",
        "workspaceId": "workspace-typed-creative",
        "projectId": "project-typed-creative",
        "workspacePath": str(tmp_path),
        "modality": "image",
        "operationKind": "image.edit",
        "canvasOperationId": "canvas-operation-exact",
        "sourceId": "source-exact",
        "maskSourceId": "mask-exact",
        "outputKind": "artifact",
        "outputSlot": "image_derivative",
        "request": {
            "modality": "image",
            "operationKind": "image.edit",
            "canvasOperationId": "canvas-operation-exact",
            "sourceId": "source-exact",
            "maskSourceId": "mask-exact",
            "outputKind": "artifact",
            "outputSlot": "image_derivative",
        },
        "artifacts": [{"artifactId": "art_existing_exact", "mimeType": "image/png"}],
    }

    async def _facade_must_not_create(_tool, _payload: dict, *args, **kwargs) -> str:
        del args, kwargs
        raise AssertionError("An existing Canvas operation must not create a duplicate job")

    monkeypatch.setattr(type(creative_media_jobs), "ainvoke", _facade_must_not_create)
    monkeypatch.setattr(creative_media_runtime, "list_jobs", lambda **_kwargs: [existing_job])

    handoff = asyncio.run(
        runner._execute_creative_media(
            _typed_creative_episode(contract=_canvas_edit_contract(), tmp_path=tmp_path)
        )
    )

    assert handoff["status"] == "ready"
    assert handoff["artifactRefs"] == ["art_existing_exact"]
    assert handoff["jobRefs"] == ["creative-media-job://cm_existing_exact"]


def test_prose_only_creative_episode_still_uses_director(monkeypatch) -> None:
    from runtimes.creative_media.runtime import creative_media_runtime

    runner = RuntimeEpisodeRunner()
    director_calls = 0
    monkeypatch.setattr(runner, "_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        creative_media_runtime,
        "compile_recipe",
        lambda _request: {"recipeId": "recipe-prose", "providerStatus": "ready"},
    )

    async def _director(_episode: dict) -> dict:
        nonlocal director_calls
        director_calls += 1
        return {
            "status": "ready",
            "compactSummary": "Director completed the unresolved creative plan.",
            "results": [
                {
                    "creativeExecutionEvidence": {
                        "schemaVersion": "creative-execution-evidence/v1",
                        "sourceRuntimeEpisodeId": "episode-prose-creative",
                        "taskBriefId": "director-task",
                        "delegationId": "director-delegation",
                        "records": [
                            {
                                "tool": "creative_media_jobs",
                                "toolCallId": "tool-director",
                            }
                        ],
                        "artifactRefs": ["art_director"],
                        "proofRefs": ["proof_director"],
                    }
                }
            ],
        }

    monkeypatch.setattr(runner, "_execute_delegation", _director)
    handoff = asyncio.run(
        runner._execute_creative_media(
            {
                "episodeId": "episode-prose-creative",
                "kind": "creative_media",
                "inputs": {"request": {"prompt": "Plan an unresolved creative task."}},
                "need": {"reason": "Plan an unresolved creative task."},
            }
        )
    )

    assert handoff["status"] == "ready"
    assert director_calls == 1
    assert handoff["artifactRefs"] == ["art_director"]


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
