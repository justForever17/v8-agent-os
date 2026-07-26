from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.models import ChatRequest
from api import session_workflow_routes
from api.chat_realtime_routes import _normalize_upload_source_kind
from core.database import DatabaseManager
from core.tools.native import creative_media_facade as facade
from erc.runtime_context import bind_runtime_context
from runtimes.chat.runtime import ChatRuntime
from runtimes.creative_media.runtime import creative_media_runtime


def test_canvas_operation_mention_becomes_authoritative_runtime_lineage(monkeypatch) -> None:
    runtime = ChatRuntime()
    request = ChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "本消息来自画布"}],
            "data": {
                "composerPresentation": {"text": "本消息来自画布", "references": []},
                "contextMentions": [
                    {
                        "kind": "canvas_operation",
                        "id": "canvas-op-1",
                        "label": "局部编辑",
                        "sourceType": "creative_media.edit_image_region",
                    }
                ]
            },
        }
    )
    mentions = runtime._normalize_context_mentions(request, skill_references=[])
    canvas_operation_id = runtime._canvas_operation_id_from_context_mentions(mentions)
    assert canvas_operation_id == "canvas-op-1"

    monkeypatch.setattr(runtime, "_safety_approval_mode_for_run", lambda _chat_run: "strict")
    monkeypatch.setattr(runtime, "_supervisor_direct_scope_requires_engineering_route", lambda _chat_run: False)
    chat_run = SimpleNamespace(
        transport="test",
        session_id="session-1",
        active_run_id="run-1",
        user_id="user-1",
        prepared=SimpleNamespace(
            latest_user_content="edit",
            plugin_references=[],
            plugin_authorizations=[],
            canvas_operation_id=canvas_operation_id,
            spec_id="",
            live_audit_context={},
        ),
        request=SimpleNamespace(resume_value=None),
        scope_result=SimpleNamespace(
            binding=SimpleNamespace(
                project_id="project-1",
                workspace_id="workspace-1",
                workspace_path="C:/workspace",
                resolved_scope="project:project-1",
            )
        ),
    )
    context = runtime._runtime_context_kwargs(chat_run)
    assert context["canvas_operation_id"] == "canvas-op-1"
    assert context["canvasOperationId"] == "canvas-op-1"

    prepared = runtime.prepare_request(request)
    assert runtime._session_title_source(prepared) == "本消息来自画布"


def test_validated_canvas_request_starts_supervisor_without_blocking_attachment_analysis(monkeypatch) -> None:
    operation_id = "canvas-op-direct-1"
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
    request = ChatRequest.model_validate(
        {
            "session_id": "session-canvas-direct",
            "messages": [{
                "role": "user",
                "content": (
                    "This message is from Canvas\n\n"
                    "[CANVAS EXECUTION CONTRACT v1]\n"
                    f"{json.dumps(contract)}\n"
                    "[/CANVAS EXECUTION CONTRACT]"
                ),
            }],
            "attachments": [{
                "id": source_id,
                "sourceId": source_id,
                "url": "https://example.test/source.png",
                "mimeType": "image/png",
                "mediaKind": "image",
                "metadata": {"canvasOperationId": operation_id},
            }],
            "data": {
                "canvasSupervisorDirect": True,
                "composerPresentation": {"text": "This message is from Canvas", "references": []},
                "contextMentions": [{
                    "kind": "canvas_operation",
                    "id": operation_id,
                    "label": "Edit region",
                    "sourceType": "creative_media.edit_image_region",
                }],
            },
        }
    )

    def fake_get_session_source(*, session_id: str, source_id: str):
        assert session_id == "session-canvas-direct"
        if source_id == "source-image":
            return {"sourceId": source_id, "sourceKind": "web_upload"}
        if source_id == mask_source_id:
            return {"sourceId": mask_source_id, "sourceKind": "canvas_mask"}
        return None

    monkeypatch.setattr("runtimes.chat.runtime.db.get_session_source", fake_get_session_source)
    runtime = ChatRuntime()
    restored_request = ChatRequest.model_validate(request.model_dump(mode="json", by_alias=True))
    assert restored_request.data is not None
    assert restored_request.data.canvas_supervisor_direct is True
    restored_attachments = [
        item.model_dump(mode="json", by_alias=True)
        for item in restored_request.attachments or []
    ]
    assert [item.get("sourceId") for item in restored_attachments] == [source_id]
    assert mask_source_id not in json.dumps(restored_attachments, ensure_ascii=False)
    prepared = runtime.prepare_request(request)
    assert prepared.canvas_supervisor_direct is True
    assert prepared.canvas_execution_contract == contract
    assert runtime._session_title_source(prepared) == "This message is from Canvas"

    chat_run = SimpleNamespace(
        prepared=prepared,
        request=request,
        is_resume_request=False,
        session_id=prepared.session_id,
        active_run_id="run-canvas-direct",
        transport="test",
        user_id="user-1",
        scope_result=SimpleNamespace(binding=SimpleNamespace(
            workspace_path="E:/workspace",
            workspace_id="workspace-1",
            project_id="project-1",
            resolved_scope="workspace",
        )),
    )
    runtime_context = runtime._runtime_context_kwargs(chat_run)
    assert runtime_context["canvasSupervisorDirect"] is True
    assert runtime_context["canvasExecutionContract"] == contract
    assert runtime_context["canvasRuntimeRoute"]["routeKind"] == "creative_media"
    assert runtime_context["canvasRuntimeRoute"]["taskBriefs"][0]["context"]["canvasExecutionContract"] == contract
    assert runtime_context["canvasRuntimeRoute"]["taskBriefs"][0]["writeSet"] == [".v8/creative-media/"]
    assert runtime._build_attachment_preflight_work_items(chat_run) == []
    background_items = runtime._build_attachment_preflight_work_items(
        chat_run,
        allow_canvas_direct=True,
    )
    assert [item["toolName"] for item in background_items] == ["vision_media_analyzer"]
    prepared.canvas_supervisor_direct = False
    assert [
        item["toolName"]
        for item in runtime._build_attachment_preflight_work_items(chat_run)
    ] == ["vision_media_analyzer"]


def test_attachment_preflight_resolves_only_workspace_bound_relative_paths(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    upload = workspace / ".v8" / "uploads" / "source.png"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"image")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        scope_result=SimpleNamespace(
            binding=SimpleNamespace(workspace_path=str(workspace)),
        ),
    )

    assert Path(runtime._resolve_attachment_local_path(
        chat_run,
        {"workspacePath": ".v8/uploads/source.png"},
    )) == upload.resolve()
    assert runtime._resolve_attachment_local_path(
        chat_run,
        {"workspacePath": "../outside.png"},
    ) == ""


def test_canvas_direct_flag_cannot_bypass_contract_or_session_lineage(monkeypatch) -> None:
    runtime = ChatRuntime()
    request = ChatRequest.model_validate(
        {
            "session_id": "session-current",
            "messages": [{"role": "user", "content": "本消息来自画布"}],
            "data": {
                "canvasSupervisorDirect": True,
                "contextMentions": [{"kind": "canvas_operation", "id": "canvas-op-1"}],
            },
        }
    )
    with pytest.raises(ValueError, match="canonical Canvas execution contract"):
        runtime.prepare_request(request)


def test_canvas_direct_creative_execution_cannot_drift_from_resource_lineage() -> None:
    operation_id = "canvas-op-drift"
    contract = {
        "schema": "v8.creative_canvas_task.v1",
        "canvasOperationId": operation_id,
        "actionId": "creative_media.edit_image_region",
        "resources": {"sourceIds": ["source-visible"]},
        "execution": {
            "tool": "creative_media_jobs",
            "arguments": {
                "action": "create",
                "request": {
                    "operationKind": "image.edit",
                    "canvasOperationId": operation_id,
                    "sourceId": "source-other",
                },
            },
        },
    }
    request = ChatRequest.model_validate({
        "session_id": "session-current",
        "messages": [{
            "role": "user",
            "content": (
                "本消息来自画布\n"
                "[CANVAS EXECUTION CONTRACT v1]\n"
                f"{json.dumps(contract)}\n"
                "[/CANVAS EXECUTION CONTRACT]"
            ),
        }],
        "attachments": [{
            "id": "source-visible",
            "sourceId": "source-visible",
            "url": "https://example.test/source-visible.png",
            "mimeType": "image/png",
            "metadata": {"canvasOperationId": operation_id},
        }],
        "data": {
            "canvasSupervisorDirect": True,
            "contextMentions": [{"kind": "canvas_operation", "id": operation_id}],
        },
    })

    with pytest.raises(ValueError, match="sources must match"):
        ChatRuntime().prepare_request(request)


def test_canvas_masks_are_hidden_from_client_source_catalog_but_remain_queryable(monkeypatch) -> None:
    rows = [
        {"sourceId": "source-visible", "sourceKind": "web_upload"},
        {"sourceId": "source-mask", "sourceKind": "canvas_mask"},
    ]
    monkeypatch.setattr(session_workflow_routes.db, "list_session_sources", lambda **_kwargs: rows)

    visible = asyncio.run(session_workflow_routes.list_session_sources("session-1"))
    internal = asyncio.run(session_workflow_routes.list_session_sources("session-1", include_internal=True))
    assert [item["sourceId"] for item in visible["sources"]] == ["source-visible"]
    assert [item["sourceId"] for item in internal["sources"]] == ["source-visible", "source-mask"]


def test_canvas_mask_upload_kind_remains_internal_instead_of_falling_back() -> None:
    assert _normalize_upload_source_kind("canvas_mask") == "canvas_mask"
    assert _normalize_upload_source_kind("unknown") == "client_upload"


def test_creative_jobs_facade_accepts_typed_source_lineage_and_overrides_canvas_id_from_context() -> None:
    spec = facade.CREATIVE_MEDIA_ACTION_REGISTRY["jobs"]["create"]
    with bind_runtime_context(
        session_id="session-1",
        run_id="run-1",
        workspace_path="C:/workspace",
        canvas_operation_id="canvas-op-authoritative",
    ):
        payload, error = facade._validate_request(
            spec,
            {
                "modality": "image",
                "operationKind": "image.edit",
                "prompt": "replace the masked region",
                "canvasOperationId": "canvas-op-spoofed",
                "sourceId": "source-image",
                "maskSourceId": "source-mask",
            },
        )

    assert error is None
    assert payload["canvasOperationId"] == "canvas-op-authoritative"
    assert payload["sourceId"] == "source-image"
    assert payload["maskSourceId"] == "source-mask"
    assert payload["sessionId"] == "session-1"


def test_source_and_mask_resolve_only_from_current_session_ledger(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-current", "Current session", user_id="user-1")
    database.create_or_update_session("session-foreign", "Foreign session", user_id="user-1")
    workspace = tmp_path / "workspace"
    upload_dir = workspace / ".v8" / "uploads"
    upload_dir.mkdir(parents=True)
    image_path = upload_dir / "source.png"
    mask_path = upload_dir / "mask.png"
    foreign_path = upload_dir / "foreign.png"
    image_path.write_bytes(b"image")
    mask_path.write_bytes(b"mask")
    foreign_path.write_bytes(b"foreign")
    database.add_session_source(
        source_id="source-image",
        session_id="session-current",
        source_kind="web_upload",
        workspace_path=".v8/uploads/source.png",
    )
    database.add_session_source(
        source_id="source-mask",
        session_id="session-current",
        source_kind="canvas_mask",
        workspace_path=".v8/uploads/mask.png",
    )
    database.add_session_source(
        source_id="source-foreign",
        session_id="session-foreign",
        source_kind="web_upload",
        workspace_path=".v8/uploads/foreign.png",
    )
    monkeypatch.setattr("runtimes.creative_media.runtime.db", database)

    prepared = creative_media_runtime._prepare_governed_source_inputs(
        {
            "sessionId": "session-current",
            "workspacePath": str(workspace),
            "sourceId": "source-image",
            "maskSourceId": "source-mask",
        },
        operation_kind="image.edit",
    )
    assert Path(prepared["imagePath"]) == image_path.resolve()
    assert Path(prepared["maskPath"]) == mask_path.resolve()

    with pytest.raises(ValueError, match="current session source ledger"):
        creative_media_runtime._prepare_governed_source_inputs(
            {
                "sessionId": "session-current",
                "workspacePath": str(workspace),
                "sourceId": "source-foreign",
            },
            operation_kind="image.edit",
        )


def test_openai_compatible_image_edit_sends_resolved_image_and_mask_multipart(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "source.png"
    mask_path = tmp_path / "mask.png"
    image_path.write_bytes(b"source-bytes")
    mask_path.write_bytes(b"mask-bytes")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        creative_media_runtime,
        "_configured_endpoint_binding",
        lambda _request, default_model: {
            "providerId": "openai-images",
            "providerMeta": {"base_url": "https://provider.test/v1", "api_key": "secret"},
            "providerModelId": "gpt-image-2",
            "endpointPath": "images/edits",
            "operationKind": "image.edit",
        },
    )

    async def fake_request(method, url, *, headers, data, files, timeout):
        captured.update(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "data": data,
                "timeout": timeout,
                "files": {name: value[1].read() for name, value in files.items()},
            }
        )
        return {"data": [{"b64_json": "ignored"}]}

    async def fake_artifact(_response, **_kwargs):
        return {"artifactId": "artifact-1", "metadata": {}}

    monkeypatch.setattr(creative_media_runtime, "_request_multipart_json", fake_request)
    monkeypatch.setattr(creative_media_runtime, "_artifact_from_image_response", fake_artifact)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: job)
    job = {
        "jobId": "job-1",
        "modality": "image",
        "operationKind": "image.edit",
        "status": "queued",
    }
    result = asyncio.run(
        creative_media_runtime._run_openai_image_edit_job(
            job,
            {
                "prompt": "replace the masked region",
                "imagePath": str(image_path),
                "maskPath": str(mask_path),
                "sourceId": "source-image",
                "maskSourceId": "source-mask",
            },
        )
    )

    assert result["status"] == "succeeded"
    assert captured["url"] == "https://provider.test/v1/images/edits"
    assert captured["timeout"] == 300
    assert captured["files"] == {"image": b"source-bytes", "mask": b"mask-bytes"}


def test_job_and_artifact_keep_typed_canvas_source_lineage(tmp_path, monkeypatch) -> None:
    output = tmp_path / "result.png"
    output.write_bytes(b"result")
    job = creative_media_runtime._new_job(
        modality="image",
        adapter="openai_images",
        request={
            "operationKind": "image.edit",
            "canvasOperationId": "canvas-op-1",
            "sourceId": "source-image",
            "maskSourceId": "source-mask",
        },
    )
    captured: dict[str, object] = {}

    def fake_record_artifact(**kwargs):
        captured.update(kwargs)
        return {"artifactId": "artifact-1", "metadata": kwargs["metadata"]}

    monkeypatch.setattr("runtimes.creative_media.runtime.artifact_store.record_artifact", fake_record_artifact)
    artifact = creative_media_runtime._record_local_artifact(
        file_path=output,
        job=job,
        kind="image",
        mime_type="image/png",
        metadata={"provider": "openai-images"},
    )

    assert job["canvasOperationId"] == "canvas-op-1"
    assert job["sourceId"] == "source-image"
    assert job["maskSourceId"] == "source-mask"
    assert artifact["metadata"]["canvasOperationId"] == "canvas-op-1"
    assert artifact["metadata"]["sourceId"] == "source-image"
    assert artifact["metadata"]["maskSourceId"] == "source-mask"
    assert artifact["metadata"]["operationKind"] == "image.edit"


def test_endpoint_binding_operation_excludes_mismatched_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "openai-images": {
                    "provider": {"name": "OpenAI Images", "api_standard": "openai"},
                    "models": {
                        "images/edits/gpt-image-2": {
                            "type": "IMAGE",
                            "operationKinds": ["image.generate", "image.edit"],
                            "mediaLimits": {
                                "operationKinds": ["image.generate", "image.edit"],
                                "capabilityModes": ["image.generate", "image.edit"],
                            },
                            "endpointBinding": {
                                "adapter": "openai_images",
                                "endpointPath": "images/edits",
                                "providerModelId": "gpt-image-2",
                                "operationKind": "image.edit",
                                "provenance": {"source": "manual"},
                            },
                        }
                    },
                }
            }
        },
    )
    candidates = [
        item
        for item in creative_media_runtime.list_model_candidates()
        if item.get("providerId") == "openai-images"
    ]
    assert [item["operationKind"] for item in candidates] == ["image.edit"]


def test_explicit_provider_model_cannot_bypass_disabled_exact_operation(monkeypatch) -> None:
    candidate = {
        "candidateId": "candidate-1",
        "modality": "image",
        "operationKind": "image.edit",
        "providerId": "openai-images",
        "modelId": "images/edits/gpt-image-2",
        "modelRef": "openai-images::images/edits/gpt-image-2",
        "adapter": "openai_images",
        "endpointBinding": {
            "providerModelId": "gpt-image-2",
            "operationKind": "image.edit",
        },
        "enabled": False,
        "available": True,
        "priority": 10,
    }
    monkeypatch.setattr(
        creative_media_runtime,
        "_all_model_candidates_for_operation",
        lambda _operation_kind: [candidate],
    )
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: job)

    job = asyncio.run(
        creative_media_runtime.create_job(
            {
                "modality": "image",
                "operationKind": "image.edit",
                "providerId": "openai-images",
                "modelId": "gpt-image-2",
                "prompt": "edit",
            }
        )
    )
    assert job["status"] == "failed"
    assert "not enabled" in job["error"]
