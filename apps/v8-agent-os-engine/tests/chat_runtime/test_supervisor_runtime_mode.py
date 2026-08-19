from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from api import chat_realtime_routes
from api import session_workflow_routes
from api.models import ChatRequest
from core.database import DatabaseManager
from graph.supervisor_turn import _authoritative_runtime_route_kinds
from runtimes.chat import runtime as chat_runtime_module
from runtimes.chat.runtime import ChatRuntime


@pytest.fixture(autouse=True)
def _isolate_runtime_mode_tests_from_model_hub_and_run_storage(monkeypatch) -> None:
    # These tests exercise routing and presentation only. Model Hub resolution
    # and durable run transitions have their own contract suites and must not
    # depend on a developer's real configuration here.
    monkeypatch.setattr(ChatRuntime, "_resolve_engine_config", lambda _self, _request: None)
    monkeypatch.setattr(
        chat_runtime_module.run_service,
        "transition_run_if_status",
        lambda *_args, **_kwargs: {
            "updated": True,
            "previousStatus": "queued",
            "currentStatus": "running",
        },
    )
    monkeypatch.setattr(
        chat_runtime_module.workflow_ledger_service,
        "sync_run_status",
        lambda *_args, **_kwargs: None,
    )


def _request(*, data: dict | None = None, attachments: list[dict] | None = None) -> ChatRequest:
    return ChatRequest.model_validate({
        "session_id": "session-runtime-mode-test",
        "messages": [{"role": "user", "content": "Complete the current task."}],
        "data": data,
        "attachments": attachments or [],
    })


def _guidance_host(runtime: ChatRuntime) -> SimpleNamespace:
    request = _request(data={
        "supervisorWorkMode": "daily",
        "supervisorRuntimeMode": "auto",
    })
    prepared = runtime.prepare_request(request)
    return SimpleNamespace(
        request=request,
        prepared=prepared,
        lc_messages=prepared.lc_messages,
        session_id=prepared.session_id,
        user_id=prepared.user_id,
        active_run_id="run-runtime-mode-test",
        run_handle=SimpleNamespace(run_id="run-runtime-mode-test"),
        scope_result=SimpleNamespace(binding=SimpleNamespace(
            workspace_path="E:/workspace",
            workspace_id="workspace-1",
            project_id="project-1",
            resolved_scope="workspace",
        )),
        transport="web",
        existing_binding=None,
        preflight_decision=SimpleNamespace(),
        engineering_workspace={},
        engineering_change_set={},
    )


def test_supervisor_runtime_mode_is_strongly_typed() -> None:
    request = _request(data={"supervisorRuntimeMode": "creative_media"})

    assert request.data is not None
    assert request.data.supervisor_runtime_mode == "creative_media"
    assert request.data.model_dump(by_alias=True)["supervisorRuntimeMode"] == "creative_media"

    with pytest.raises(ValidationError):
        _request(data={"supervisorRuntimeMode": "delegation"})


def test_session_patch_rejects_unknown_runtime_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        session_workflow_routes.db,
        "get_session",
        lambda _session_id: {"id": "session-runtime-mode-test", "user_id": "user-1"},
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(session_workflow_routes.patch_session_presentation(
            "session-runtime-mode-test",
            {"userId": "user-1", "supervisorRuntimeMode": "delegation"},
        ))

    assert error.value.status_code == 400
    assert error.value.detail == "session_supervisor_runtime_mode_invalid"


def test_explicit_runtime_mode_clears_hidden_engineering_work_posture(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtimes.chat.runtime.db.get_session",
        lambda _session_id: {
            "metadata": {
                "supervisorWorkMode": "engineering",
                "supervisorRuntimeMode": "rpa",
            }
        },
    )
    request = _request(data={
        "supervisorWorkMode": "engineering",
        "supervisorRuntimeMode": "auto",
    })

    prepared = ChatRuntime().prepare_request(request)

    assert prepared.supervisor_runtime_mode == "auto"
    assert prepared.supervisor_work_mode == "daily"


def test_omitted_runtime_mode_keeps_legacy_work_posture_and_uses_session_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtimes.chat.runtime.db.get_session",
        lambda _session_id: {
            "metadata": {
                "supervisorWorkMode": "engineering",
                "supervisorRuntimeMode": "rpa",
            }
        },
    )

    prepared = ChatRuntime().prepare_request(_request())

    assert prepared.supervisor_runtime_mode == "rpa"
    assert prepared.supervisor_work_mode == "engineering"


@pytest.mark.parametrize(
    ("runtime_mode", "expects_engineering_lane"),
    [("research", False), ("engineering", False), ("auto", True)],
)
def test_selected_runtime_mode_bypasses_legacy_engineering_lane(
    monkeypatch,
    runtime_mode: str,
    expects_engineering_lane: bool,
) -> None:
    runtime = ChatRuntime()
    binding = SimpleNamespace(
        workspace_path="E:/workspace",
        workspace_id="workspace-1",
        project_id="project-1",
        resolved_scope="workspace",
    )
    scope_result = SimpleNamespace(binding=binding)
    run_handle = SimpleNamespace(run_id=f"run-{runtime_mode}")
    metadata_updates: list[dict] = []
    build_context_pack = mock.Mock(return_value={
        "triggerDecision": {"active": True, "matched": True, "reason": "project_coding"}
    })

    monkeypatch.setattr(chat_runtime_module.db, "create_or_update_session", lambda **_kwargs: None)
    monkeypatch.setattr(chat_runtime_module.session_scope_binding_service, "get_binding", lambda _session_id: None)
    monkeypatch.setattr(chat_runtime_module.scope_resolution_service, "resolve", lambda **_kwargs: scope_result)
    monkeypatch.setattr(chat_runtime_module.run_service, "update_metadata", lambda _run_id, payload: metadata_updates.append(dict(payload)))
    monkeypatch.setattr(chat_runtime_module, "build_supervisor_task_context", lambda _query: {
        "primaryTaskShape": "project_coding",
        "secondaryTaskShapes": [],
        "confidence": 0.95,
        "reason": "project_coding",
    })
    monkeypatch.setattr(chat_runtime_module, "attach_task_boundary_decision", lambda hint, **_kwargs: {
        **dict(hint),
        "boundaryDecision": {"primaryRuntime": "engineering"},
    })
    monkeypatch.setattr(chat_runtime_module.engineering_lane_service, "build_context_pack", build_context_pack)
    monkeypatch.setattr(chat_runtime_module.safety_guardian, "preflight_runtime", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(runtime, "_attach_scope_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "begin_run", lambda **_kwargs: run_handle)

    chat_run = runtime.prepare_run_context(
        _request(data={"supervisorRuntimeMode": runtime_mode}),
        transport="web",
    )

    assert build_context_pack.called is expects_engineering_lane
    if expects_engineering_lane:
        assert chat_run.prepared.engineering_mode == "force"
    else:
        assert chat_run.prepared.engineering_mode == "off"
        assert chat_run.prepared.engineering_trigger_decision["reason"] == (
            "selected_runtime_mode_uses_authoritative_runtime_route"
        )
        assert any(
            item.get("engineeringRequired") is False
            and item.get("explicitEngineeringRequested") is False
            for item in metadata_updates
        )


def test_delayed_queued_request_mode_does_not_rewrite_later_session_presentation(
    monkeypatch,
    tmp_path,
) -> None:
    test_db = DatabaseManager(tmp_path / "runtime-mode-queue.sqlite3")
    monkeypatch.setattr(chat_runtime_module, "db", test_db)
    monkeypatch.setattr(chat_realtime_routes, "db", test_db)
    test_db.create_or_update_session(
        "session-runtime-mode-test",
        "Queued mode",
        metadata={"supervisorRuntimeMode": "auto"},
    )
    queue_item = test_db.add_chat_user_message_queue_item(
        queue_id="queue-runtime-mode",
        session_id="session-runtime-mode-test",
        run_id=None,
        client_message_id="client-runtime-mode",
        content="Research this after the current run.",
        request_payload=_request(
            data={"supervisorRuntimeMode": "research"},
        ).model_dump(mode="json", by_alias=True),
    )
    test_db.update_session_presentation(
        "session-runtime-mode-test",
        {"supervisorRuntimeMode": "rpa"},
    )
    delayed_request = chat_realtime_routes._request_from_queue_item(
        queue_item,
        run_id="run-queued",
    )
    runtime = ChatRuntime()
    scope_result = SimpleNamespace(binding=SimpleNamespace(
        workspace_path="E:/workspace",
        workspace_id="workspace-1",
        project_id="project-1",
        resolved_scope="workspace",
    ))

    monkeypatch.setattr(chat_runtime_module.session_scope_binding_service, "get_binding", lambda _session_id: None)
    monkeypatch.setattr(chat_runtime_module.scope_resolution_service, "resolve", lambda **_kwargs: scope_result)
    monkeypatch.setattr(chat_runtime_module.run_service, "update_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_runtime_module, "build_supervisor_task_context", lambda _query: {
        "primaryTaskShape": "general",
        "secondaryTaskShapes": [],
        "confidence": 0.9,
        "reason": "general",
    })
    monkeypatch.setattr(chat_runtime_module, "attach_task_boundary_decision", lambda hint, **_kwargs: dict(hint))
    monkeypatch.setattr(chat_runtime_module.engineering_lane_service, "build_context_pack", lambda **_kwargs: pytest.fail("selected research mode must not enter the legacy engineering lane"))
    monkeypatch.setattr(chat_runtime_module.safety_guardian, "preflight_runtime", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(runtime, "_attach_scope_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "begin_run", lambda **_kwargs: SimpleNamespace(run_id="run-queued"))

    chat_run = runtime.prepare_run_context(
        delayed_request,
        transport="queued_user_message",
    )

    assert chat_run.prepared.supervisor_runtime_mode == "research"
    assert test_db.get_session("session-runtime-mode-test")["metadata"]["supervisorRuntimeMode"] == "rpa"


def test_resumed_request_mode_does_not_rewrite_session_presentation(monkeypatch) -> None:
    runtime = ChatRuntime()
    session_updates: list[dict] = []
    run_handle = SimpleNamespace(
        run_id="run-resume-runtime-mode",
        session_id="session-runtime-mode-test",
        descriptor=SimpleNamespace(
            conversation_id="session-runtime-mode-test",
            user_id="local-user",
        ),
    )
    scope_result = SimpleNamespace(binding=SimpleNamespace(
        workspace_path="E:/workspace",
        workspace_id="workspace-1",
        project_id="project-1",
        resolved_scope="workspace",
    ))
    request = _request(data={"supervisorRuntimeMode": "research"})
    request.resume_run_id = run_handle.run_id

    monkeypatch.setattr(chat_runtime_module.db, "get_session", lambda _session_id: {
        "metadata": {"supervisorRuntimeMode": "rpa"},
    })
    monkeypatch.setattr(
        chat_runtime_module.db,
        "update_session_metadata",
        lambda _session_id, payload: session_updates.append(dict(payload)),
    )
    monkeypatch.setattr(chat_runtime_module.session_scope_binding_service, "get_binding", lambda _session_id: None)
    monkeypatch.setattr(chat_runtime_module.scope_resolution_service, "resolve", lambda **_kwargs: scope_result)
    monkeypatch.setattr(chat_runtime_module.run_service, "update_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_runtime_module, "build_supervisor_task_context", lambda _query: {
        "primaryTaskShape": "general",
        "secondaryTaskShapes": [],
        "confidence": 0.9,
        "reason": "general",
    })
    monkeypatch.setattr(chat_runtime_module, "attach_task_boundary_decision", lambda hint, **_kwargs: dict(hint))
    monkeypatch.setattr(chat_runtime_module.safety_guardian, "preflight_runtime", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(runtime, "attach_run", lambda _run_id: run_handle)
    monkeypatch.setattr(runtime, "_attach_scope_context", lambda *_args, **_kwargs: None)

    chat_run = runtime.prepare_run_context(request, transport="web")

    assert chat_run.prepared.supervisor_runtime_mode == "research"
    assert session_updates
    assert all("supervisorRuntimeMode" not in update for update in session_updates)


def test_promoted_guidance_restores_queued_request_runtime_mode_snapshot(monkeypatch) -> None:
    runtime = ChatRuntime()
    captured: dict = {}
    snapshot = {
        "messages": [HumanMessage(content="Original request")],
        "current_route_context": {
            "supervisorRuntimeMode": "engineering",
            "supervisor_runtime_mode": "engineering",
            "routeSnapshotMarker": "keep-me",
            "capabilityEpisodes": [{
                "episodeId": "episode-before-guidance",
                "kind": "research",
                "state": "completed",
            }],
        },
    }

    async def get_state_snapshot(_bundle):
        return snapshot

    async def create_execution_bundle(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(diagnostics={})

    monkeypatch.setattr(chat_runtime_module.supervisor_runner, "get_state_snapshot", get_state_snapshot)
    monkeypatch.setattr(chat_runtime_module.supervisor_runner, "create_execution_bundle", create_execution_bundle)
    monkeypatch.setattr(runtime, "_safety_approval_mode_for_run", lambda *_args, **_kwargs: "manual")
    request = _request(data={"supervisorRuntimeMode": "engineering"})
    prepared = SimpleNamespace(
        supervisor_work_mode="daily",
        supervisor_runtime_mode="engineering",
        engineering_context_pack=None,
        task_shape_hint={},
        explicit_subagent_families=[],
        context_mentions=[],
        context_session_refs=[],
        session_coordination_message={},
    )
    chat_run = SimpleNamespace(
        request=request,
        prepared=prepared,
        lc_messages=[HumanMessage(content="Original request")],
        session_id="session-runtime-mode-test",
        user_id="anonymous",
        active_run_id="run-runtime-mode-test",
        run_handle=SimpleNamespace(run_id="run-runtime-mode-test"),
        scope_result=SimpleNamespace(binding=SimpleNamespace(
            workspace_path="E:/workspace",
            workspace_id="workspace-1",
            project_id="project-1",
            resolved_scope="workspace",
        )),
        transport="web",
        existing_binding=None,
        preflight_decision=SimpleNamespace(),
        engineering_workspace={},
        engineering_change_set={},
    )
    queue_item = {
        "id": "queue-guidance-runtime-mode",
        "content": "Switch the remaining work to research.",
        "request": _request(
            data={"supervisorRuntimeMode": "research"},
        ).model_dump(mode="json", by_alias=True),
    }

    bundle = asyncio.run(runtime.create_guidance_bundle(
        chat_run=chat_run,
        previous_bundle=SimpleNamespace(runner_bundle=object()),
        queue_item=queue_item,
    ))

    assert bundle is not None
    route_context = captured["current_route_context"]
    assert route_context["routeSnapshotMarker"] == "keep-me"
    assert route_context["supervisorRuntimeMode"] == "research"
    assert route_context["supervisor_runtime_mode"] == "research"
    assert route_context["supervisorRuntimeModeRequestScope"] == {
        "queueItemId": "queue-guidance-runtime-mode",
        "requiredRuntimeKind": "research",
        "priorEpisodeIds": ["episode-before-guidance"],
    }


def test_promoted_auto_guidance_clears_prior_canvas_and_engineering_routes(monkeypatch) -> None:
    runtime = ChatRuntime()
    host = _guidance_host(runtime)
    captured: dict = {}
    runner_payload: dict = {
        "runtime_dispatch_status": {"state": "running"},
        "engineering_context": {"stale": True},
        "context_mentions": [{"kind": "canvas_operation", "id": "old-op"}],
        "specMode": True,
        "specId": "old-spec",
        "specBrief": {"specId": "old-spec", "status": "ready"},
        "specContinuation": {"specId": "old-spec", "nextStage": "runtime_execution"},
    }
    snapshot = {
        "messages": [HumanMessage(content="Original Canvas request")],
        "specMode": True,
        "specId": "old-spec",
        "specBrief": {"specId": "old-spec", "status": "ready"},
        "specContinuation": {"specId": "old-spec", "nextStage": "runtime_execution"},
        "current_route_context": {
            "canvasSupervisorDirect": True,
            "canvas_supervisor_direct": True,
            "canvasOperationId": "old-op",
            "canvasExecutionContract": {"schema": "v8.creative_canvas_task.v1"},
            "canvasRuntimeRoute": {"routeKind": "creative_media"},
            "engineeringRequired": True,
            "explicitEngineeringRequested": True,
            "engineeringContinuation": {"active": True},
            "supervisorRuntimeMode": "creative_media",
            "specMode": True,
            "specId": "old-spec",
            "specBrief": {"specId": "old-spec", "status": "ready"},
            "specContinuation": {"specId": "old-spec", "nextStage": "runtime_execution"},
            "runtimeExecutionAllowed": True,
            "routeSnapshotMarker": "keep-me",
        },
    }

    async def get_state_snapshot(_bundle):
        return snapshot

    async def create_execution_bundle(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(diagnostics={}, payload=runner_payload)

    monkeypatch.setattr(chat_runtime_module.supervisor_runner, "get_state_snapshot", get_state_snapshot)
    monkeypatch.setattr(chat_runtime_module.supervisor_runner, "create_execution_bundle", create_execution_bundle)
    monkeypatch.setattr(runtime, "_safety_approval_mode_for_run", lambda *_args, **_kwargs: "manual")
    queued_request = _request(data={
        "supervisorWorkMode": "daily",
        "supervisorRuntimeMode": "auto",
    })
    queue_item = {
        "id": "queue-guidance-auto",
        "client_message_id": "client-guidance-auto",
        "content": "Continue normally without a forced runtime.",
        "attachments": [],
        "fileUrls": [],
        "request": queued_request.model_dump(mode="json", by_alias=True),
    }

    bundle = asyncio.run(runtime.create_guidance_bundle(
        chat_run=host,
        previous_bundle=SimpleNamespace(runner_bundle=object()),
        queue_item=queue_item,
    ))

    assert bundle is not None
    route_context = captured["current_route_context"]
    assert route_context["routeSnapshotMarker"] == "keep-me"
    assert route_context["canvasSupervisorDirect"] is False
    assert route_context["canvas_supervisor_direct"] is False
    assert route_context["canvasOperationId"] == ""
    assert route_context["canvasExecutionContract"] == {}
    assert route_context["canvasRuntimeRoute"] == {}
    assert route_context["engineeringRequired"] is False
    assert route_context["explicitEngineeringRequested"] is False
    assert route_context["engineeringContinuation"] == {}
    assert route_context["supervisorRuntimeMode"] == "auto"
    assert route_context["supervisorRuntimeModeRequestScope"] == {}
    assert route_context["specMode"] is False
    assert route_context["specId"] == ""
    assert route_context["specBrief"] == {}
    assert route_context["specContinuation"] == {}
    assert route_context["runtimeExecutionAllowed"] is False
    assert _authoritative_runtime_route_kinds({"current_route_context": route_context}) == []
    assert runner_payload["runtime_dispatch_status"] == {}
    assert runner_payload["engineering_context"] == {}
    assert runner_payload["context_mentions"] == []
    assert runner_payload["specMode"] is False
    assert runner_payload["specId"] == ""
    assert runner_payload["specBrief"] == {}
    assert runner_payload["specContinuation"] == {}


def test_promoted_attachment_restores_own_vision_preflight_and_request_scope(monkeypatch) -> None:
    runtime = ChatRuntime()
    host = _guidance_host(runtime)
    attachment = {
        "id": "source-image",
        "sourceId": "source-image",
        "name": "source.png",
        "url": "https://example.test/source.png",
        "mimeType": "image/png",
        "mediaKind": "image",
    }

    def queued_item(client_message_id: str) -> dict:
        request = _request(
            data={
                "supervisorWorkMode": "daily",
                "supervisorRuntimeMode": "auto",
            },
            attachments=[attachment],
        )
        request.client_message_id = client_message_id
        assert request.data is not None
        request.data.client_message_id = client_message_id
        return {
            "id": f"queue-{client_message_id}",
            "client_message_id": client_message_id,
            "content": "Describe this queued image.",
            "attachments": [attachment],
            "fileUrls": [attachment["url"]],
            "request": request.model_dump(mode="json", by_alias=True),
        }

    first_run = runtime._queued_guidance_chat_run(host, queued_item("client-image-1"))
    second_run = runtime._queued_guidance_chat_run(host, queued_item("client-image-2"))
    first_items = runtime._build_attachment_preflight_work_items(first_run)
    second_items = runtime._build_attachment_preflight_work_items(second_run)

    assert first_run.session_id == host.session_id
    assert first_run.request.workspace_id == "workspace-1"
    assert first_run.prepared.canvas_supervisor_direct is False
    assert [item.get("sourceId") for item in first_run.request.attachments or []] == ["source-image"]
    assert [item["toolName"] for item in first_items] == ["vision_media_analyzer"]
    assert first_items[0]["toolCallId"] != second_items[0]["toolCallId"]
    monkeypatch.setattr(runtime, "_safety_approval_mode_for_run", lambda *_args, **_kwargs: "manual")
    route_context = runtime._queued_guidance_route_context(
        first_run,
        queued_item("client-image-1"),
        {
            "current_route_context": {
                "canvasSupervisorDirect": True,
                "canvasOperationId": "old-op",
                "canvasExecutionContract": {"schema": "v8.creative_canvas_task.v1"},
                "canvasRuntimeRoute": {"routeKind": "creative_media"},
                "engineeringRequired": True,
            }
        },
    )
    assert route_context["canvasSupervisorDirect"] is False
    assert route_context["canvasRuntimeRoute"] == {}
    assert route_context["engineeringRequired"] is False
    assert _authoritative_runtime_route_kinds({"current_route_context": route_context}) == []

    calls: list[str] = []

    async def fake_preflight(chat_run, _stream_state, **_kwargs):
        calls.append(chat_run.request.client_message_id or "")
        yield {"type": "tool_start", "tool": {"toolName": "vision_media_analyzer"}}

    monkeypatch.setattr(runtime, "_run_attachment_preflight", fake_preflight)

    async def collect_events():
        return [
            event
            async for event in runtime._run_queued_guidance_attachment_preflight(
                first_run,
                SimpleNamespace(),
            )
        ]

    events = asyncio.run(collect_events())
    assert calls == ["client-image-1"]
    assert [event["tool"]["toolName"] for event in events] == ["vision_media_analyzer"]


def test_promoted_canvas_revalidates_own_contract_and_restores_exact_route(monkeypatch) -> None:
    runtime = ChatRuntime()
    host = _guidance_host(runtime)
    operation_id = "canvas-op-queued"
    source_id = "source-image"
    mask_source_id = "source-mask"
    contract = {
        "schema": "v8.creative_canvas_task.v1",
        "canvasOperationId": operation_id,
        "actionId": "creative_media.edit_image_region",
        "resources": {
            "sourceIds": [source_id],
            "maskSourceId": mask_source_id,
        },
        "execution": {
            "tool": "creative_media_jobs",
            "arguments": {
                "action": "create",
                "request": {
                    "modality": "image",
                    "operationKind": "image.edit",
                    "prompt": "replace only the masked region",
                    "canvasOperationId": operation_id,
                    "sourceId": source_id,
                    "maskSourceId": mask_source_id,
                },
            },
        },
    }
    content = (
        "This message is from Canvas\n\n"
        "[CANVAS EXECUTION CONTRACT v1]\n"
        f"{json.dumps(contract)}\n"
        "[/CANVAS EXECUTION CONTRACT]"
    )
    attachment = {
        "id": source_id,
        "sourceId": source_id,
        "name": "source.png",
        "url": "https://example.test/source.png",
        "mimeType": "image/png",
        "mediaKind": "image",
        "metadata": {"canvasOperationId": operation_id},
    }
    request = ChatRequest.model_validate({
        "session_id": host.session_id,
        "clientMessageId": "client-canvas-queued",
        "messages": [{"role": "user", "content": content}],
        "attachments": [attachment],
        "data": {
            "clientMessageId": "client-canvas-queued",
            "conversationId": host.session_id,
            "supervisorWorkMode": "daily",
            "supervisorRuntimeMode": "auto",
            "canvasSupervisorDirect": True,
            "contextMentions": [{
                "kind": "canvas_operation",
                "id": operation_id,
                "label": "Edit region",
                "sourceType": "creative_media.edit_image_region",
            }],
        },
    })

    def get_session_source(*, session_id: str, source_id: str):
        assert session_id == host.session_id
        if source_id == "source-image":
            return {"sourceId": source_id, "sourceKind": "web_upload"}
        if source_id == "source-mask":
            return {"sourceId": source_id, "sourceKind": "canvas_mask"}
        return None

    monkeypatch.setattr(chat_runtime_module.db, "get_session_source", get_session_source)
    monkeypatch.setattr(runtime, "_safety_approval_mode_for_run", lambda *_args, **_kwargs: "manual")
    queue_item = {
        "id": "queue-canvas-queued",
        "client_message_id": "client-canvas-queued",
        "content": content,
        "attachments": [attachment],
        "fileUrls": [attachment["url"]],
        "request": request.model_dump(mode="json", by_alias=True),
    }

    guidance_run = runtime._queued_guidance_chat_run(host, queue_item)
    route_context = runtime._queued_guidance_route_context(
        guidance_run,
        queue_item,
        {
            "current_route_context": {
                "canvasSupervisorDirect": False,
                "canvasOperationId": "old-op",
                "canvasRuntimeRoute": {},
                "supervisorRuntimeMode": "research",
                "engineeringRequired": True,
            }
        },
    )

    assert guidance_run.prepared.canvas_supervisor_direct is True
    assert guidance_run.prepared.canvas_operation_id == operation_id
    assert guidance_run.prepared.canvas_execution_contract == contract
    assert route_context["canvasSupervisorDirect"] is True
    assert route_context["canvasOperationId"] == operation_id
    assert route_context["canvasExecutionContract"] == contract
    assert route_context["canvasRuntimeRoute"]["routeKind"] == "creative_media"
    assert route_context["engineeringRequired"] is False
    assert route_context["supervisorRuntimeModeRequestScope"]["requiredRuntimeKind"] == "creative_media"
    assert _authoritative_runtime_route_kinds({"current_route_context": route_context}) == ["creative_media"]

    async def forbidden_blocking_preflight(*_args, **_kwargs):
        pytest.fail("queued Canvas direct requests must not block on Vision preflight")
        yield {}

    monkeypatch.setattr(runtime, "_run_attachment_preflight", forbidden_blocking_preflight)

    async def collect_events():
        return [
            event
            async for event in runtime._run_queued_guidance_attachment_preflight(
                guidance_run,
                SimpleNamespace(),
            )
        ]

    assert asyncio.run(collect_events()) == []

    invalid_contract = {**contract, "canvasOperationId": "canvas-op-other"}
    invalid_content = (
        "This message is from Canvas\n\n"
        "[CANVAS EXECUTION CONTRACT v1]\n"
        f"{json.dumps(invalid_contract)}\n"
        "[/CANVAS EXECUTION CONTRACT]"
    )
    invalid_item = {
        **queue_item,
        "content": invalid_content,
        "request": {
            **queue_item["request"],
            "messages": [{"role": "user", "content": invalid_content}],
        },
    }
    with pytest.raises(ValueError, match="operation id does not match") as error:
        runtime._queued_guidance_chat_run(host, invalid_item)

    queue_updates: list[tuple[str, dict]] = []
    failure_events: list[tuple[str, dict, dict]] = []
    host.run_handle.fail = mock.Mock()
    host.emit_runtime_event = lambda topic, payload, **kwargs: failure_events.append(
        (topic, dict(payload), dict(kwargs))
    )
    injected = mock.Mock()
    monkeypatch.setattr(runtime, "_emit_human_guidance_injected", injected)
    monkeypatch.setattr(
        chat_runtime_module.db,
        "update_chat_user_message_queue_item",
        lambda queue_id, **kwargs: queue_updates.append((queue_id, dict(kwargs))),
    )

    failure_class = runtime._record_queued_guidance_preparation_failure(
        host,
        queue_id="queue-canvas-invalid",
        exc=error.value,
    )

    assert failure_class == "queued_request_invalid"
    assert queue_updates == [(
        "queue-canvas-invalid",
        {
            "metadata_updates": {
                "guidanceInjectionFailure": "queued_request_invalid",
                "guidanceInjectionRunId": host.active_run_id,
            }
        },
    )]
    assert failure_events[0][0] == "human_guidance.failed"
    assert failure_events[0][1]["failureClass"] == "queued_request_invalid"
    assert "state" not in queue_updates[0][1]
    assert injected.call_count == 0
    assert host.run_handle.fail.call_count == 0


def _lifecycle_chat_run(
    *,
    supervisor_runtime_mode: str,
    canvas_supervisor_direct: bool,
    trigger_decision: dict,
) -> tuple[SimpleNamespace, list[dict]]:
    events: list[dict] = []
    prepared = SimpleNamespace(
        canvas_supervisor_direct=canvas_supervisor_direct,
        canvas_operation_id="canvas-operation-test",
        canvas_execution_contract={"actionId": "creative_media.edit_image_region"},
        supervisor_work_mode="daily",
        supervisor_runtime_mode=supervisor_runtime_mode,
        engineering_mode=str(trigger_decision.get("mode") or "auto"),
        engineering_trigger_decision=dict(trigger_decision),
        engineering_context_pack={"contextPack": {}} if trigger_decision.get("active") else None,
    )
    chat_run = SimpleNamespace(
        active_run_id="run-lifecycle-runtime-mode",
        transport="web",
        scope_result=SimpleNamespace(
            binding=SimpleNamespace(
                resolved_scope="workspace",
                project_id="project-1",
            ),
            reused_existing_binding=True,
        ),
        preflight_decision=SimpleNamespace(to_payload=lambda: {"decision": "allow"}),
        prepared=prepared,
        is_resume_request=False,
        user_id="local-user",
        run_handle=SimpleNamespace(
            descriptor=SimpleNamespace(status="queued", agent_id="supervisor"),
            emit=lambda *_args, **_kwargs: None,
            transition=lambda *_args, **_kwargs: None,
        ),
        existing_binding=None,
        emit_runtime_event=lambda topic, payload, **kwargs: events.append({
            "topic": topic,
            "payload": dict(payload),
            **kwargs,
        }),
    )
    return chat_run, events


@pytest.mark.parametrize(
    ("supervisor_runtime_mode", "canvas_supervisor_direct", "reason"),
    [
        ("research", False, "selected_runtime_mode_uses_authoritative_runtime_route"),
        ("auto", True, "validated_canvas_contract_bypasses_engineering_lane"),
    ],
)
def test_non_engineering_runtime_routes_do_not_emit_engineering_trigger_event(
    monkeypatch,
    supervisor_runtime_mode: str,
    canvas_supervisor_direct: bool,
    reason: str,
) -> None:
    monkeypatch.setattr(
        chat_runtime_module.workflow_ledger_service,
        "activate_runtime_step",
        lambda *_args, **_kwargs: None,
    )
    chat_run, events = _lifecycle_chat_run(
        supervisor_runtime_mode=supervisor_runtime_mode,
        canvas_supervisor_direct=canvas_supervisor_direct,
        trigger_decision={
            "mode": "off",
            "active": False,
            "matched": False,
            "reason": reason,
        },
    )

    ChatRuntime().emit_lifecycle_start_events(chat_run)

    assert all(event["topic"] != "engineering_lane.trigger.decided" for event in events)


def test_auto_mode_emits_engineering_trigger_with_flattened_truth(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_runtime_module.workflow_ledger_service,
        "activate_runtime_step",
        lambda *_args, **_kwargs: None,
    )
    trigger_decision = {
        "mode": "auto",
        "active": True,
        "matched": True,
        "reason": "engineering_signals_and_repo",
        "signals": ["code_change"],
        "workspaceMode": "repo",
    }
    chat_run, events = _lifecycle_chat_run(
        supervisor_runtime_mode="auto",
        canvas_supervisor_direct=False,
        trigger_decision=trigger_decision,
    )

    ChatRuntime().emit_lifecycle_start_events(chat_run)

    engineering_events = [
        event for event in events
        if event["topic"] == "engineering_lane.trigger.decided"
    ]
    assert len(engineering_events) == 1
    payload = engineering_events[0]["payload"]
    assert payload["active"] is True
    assert payload["matched"] is True
    assert payload["mode"] == "auto"
    assert payload["reason"] == "engineering_signals_and_repo"
    assert payload["signals"] == ["code_change"]
    assert payload["workspaceMode"] == "repo"
    assert payload["triggerDecision"] == trigger_decision


def test_regular_creative_mode_image_keeps_normal_vision_preflight() -> None:
    request = _request(
        data={"supervisorRuntimeMode": "creative_media"},
        attachments=[{
            "id": "source-image",
            "sourceId": "source-image",
            "name": "source.png",
            "url": "https://example.test/source.png",
            "mimeType": "image/png",
            "mediaKind": "image",
        }],
    )
    runtime = ChatRuntime()
    prepared = runtime.prepare_request(request)
    chat_run = SimpleNamespace(
        prepared=prepared,
        request=request,
        is_resume_request=False,
        active_run_id="run-runtime-mode-test",
        scope_result=SimpleNamespace(
            binding=SimpleNamespace(workspace_path="E:/workspace")
        ),
    )

    work_items = runtime._build_attachment_preflight_work_items(chat_run)

    assert prepared.canvas_supervisor_direct is False
    assert [item["toolName"] for item in work_items] == ["vision_media_analyzer"]
