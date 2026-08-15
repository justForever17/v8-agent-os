from __future__ import annotations

import asyncio
import io
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import FileResponse

from core.creative_canvas_graph import (
    CreativeCanvasGraphConflict,
    CreativeCanvasGraphError,
    creative_canvas_graph_service,
)
from core.creative_canvas_preview import CreativeCanvasPreviewError, resolve_canvas_preview
from core.workspace_media_library import workspace_media_library


router = APIRouter()


def _resolve_psd_path(*, session_id: str, origin: str, resource_id: str) -> Path:
    if origin == "source":
        path = workspace_media_library.resolve_source_path(session_id=session_id, source_id=resource_id)
    elif origin == "artifact":
        path = workspace_media_library.resolve_artifact_path(session_id=session_id, artifact_id=resource_id)
    elif origin == "workspace_asset":
        path = workspace_media_library.resolve_asset_path(session_id=session_id, asset_id=resource_id)
    else:
        raise CreativeCanvasGraphError("PSD resource origin is invalid")
    if path.suffix.lower() != ".psd":
        raise CreativeCanvasGraphError("Canvas PSD inspection requires a .psd resource")
    return path


def _resolve_motion_path(*, session_id: str, origin: str, resource_id: str) -> Path:
    if origin == "source":
        path = workspace_media_library.resolve_source_path(session_id=session_id, source_id=resource_id)
    elif origin == "artifact":
        path = workspace_media_library.resolve_artifact_path(session_id=session_id, artifact_id=resource_id)
    elif origin == "workspace_asset":
        path = workspace_media_library.resolve_asset_path(session_id=session_id, asset_id=resource_id)
    else:
        raise CreativeCanvasGraphError("Motion resource origin is invalid")
    if path.suffix.lower() != ".v8motion":
        raise CreativeCanvasGraphError("Canvas motion inspection requires a .v8motion resource")
    return path


@lru_cache(maxsize=32)
def _cached_psd_preview(path_value: str, modified_ns: int, byte_size: int) -> bytes:
    del modified_ns, byte_size
    from core.tools.native.creative_media_psd import render_psd_preview_image

    buffer = io.BytesIO()
    render_psd_preview_image(Path(path_value)).save(buffer, format="PNG")
    return buffer.getvalue()


def _raise_canvas_http(error: Exception) -> None:
    from runtimes.creative_media.motion_capture import MotionCaptureError

    if isinstance(error, CreativeCanvasGraphConflict):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, (CreativeCanvasGraphError, CreativeCanvasPreviewError, MotionCaptureError, ValueError)):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/sessions/{session_id}/canvas/actions")
async def list_canvas_actions(session_id: str):
    try:
        await asyncio.to_thread(creative_canvas_graph_service.get_graph, session_id=session_id)
        return {"actions": creative_canvas_graph_service.action_catalog()}
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/preview/{origin}/{resource_id}")
async def get_canvas_resource_preview(session_id: str, origin: str, resource_id: str):
    try:
        request = {"sessionId": session_id}
        if origin == "source":
            request["sourceId"] = resource_id
        elif origin == "artifact":
            request["artifactId"] = resource_id
        elif origin == "workspace_asset":
            request["workspaceAssetId"] = resource_id
            request["requireSessionUse"] = False
        else:
            raise CreativeCanvasPreviewError("Canvas preview origin is invalid")
        preview = await asyncio.to_thread(resolve_canvas_preview, request)
        return FileResponse(
            preview.path,
            media_type=preview.media_type,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=60, must-revalidate",
                "ETag": preview.etag,
                "X-V8-Canvas-Preview": "generated" if preview.generated else "source",
            },
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/psd/{origin}/{resource_id}/manifest")
async def get_canvas_psd_manifest(session_id: str, origin: str, resource_id: str):
    try:
        from core.tools.native.creative_media_psd import inspect_psd_manifest

        path = await asyncio.to_thread(
            _resolve_psd_path,
            session_id=session_id,
            origin=origin,
            resource_id=resource_id,
        )
        return await asyncio.to_thread(inspect_psd_manifest, path)
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/psd/{origin}/{resource_id}/preview")
async def get_canvas_psd_preview(session_id: str, origin: str, resource_id: str):
    try:
        path = await asyncio.to_thread(
            _resolve_psd_path,
            session_id=session_id,
            origin=origin,
            resource_id=resource_id,
        )
        stat = path.stat()
        content = await asyncio.to_thread(_cached_psd_preview, str(path), stat.st_mtime_ns, stat.st_size)
        return Response(
            content=content,
            media_type="image/png",
            headers={
                "Cache-Control": "private, max-age=300",
                "ETag": f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}"',
            },
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/motion/{origin}/{resource_id}/manifest")
async def get_canvas_motion_manifest(session_id: str, origin: str, resource_id: str):
    try:
        from runtimes.creative_media.motion_capture import inspect_motion_package

        path = await asyncio.to_thread(
            _resolve_motion_path,
            session_id=session_id,
            origin=origin,
            resource_id=resource_id,
        )
        return await asyncio.to_thread(inspect_motion_package, path)
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/motion/{origin}/{resource_id}/frames/{frame_index}")
async def get_canvas_motion_frame(session_id: str, origin: str, resource_id: str, frame_index: int):
    try:
        from runtimes.creative_media.motion_capture import read_motion_frame

        path = await asyncio.to_thread(
            _resolve_motion_path,
            session_id=session_id,
            origin=origin,
            resource_id=resource_id,
        )
        return await asyncio.to_thread(read_motion_frame, path, frame_index)
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/graph")
async def get_canvas_graph(session_id: str):
    try:
        return await asyncio.to_thread(creative_canvas_graph_service.get_graph, session_id=session_id)
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/graph/runs/{graph_run_id}")
async def get_canvas_graph_run(session_id: str, graph_run_id: str):
    try:
        return await asyncio.to_thread(
            creative_canvas_graph_service.get_run,
            session_id=session_id,
            graph_run_id=graph_run_id,
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/graph/runs")
async def start_canvas_graph_run(session_id: str, body: dict = Body(...)):
    """Start a persisted Canvas graph run without creating a chat turn.

    The graph already owns its typed action parameters and source lineage.  This
    route deliberately reaches the Creative Media runtime directly instead of
    proxying through ChatRuntime/Supervisor, so deterministic Canvas actions
    remain reproducible graph work rather than synthetic user messages.
    """
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        graph_id = str(body.get("graphId") or "").strip()
        graph_revision = int(body.get("graphRevision") or 0)
        if not graph_id or graph_revision < 1:
            raise CreativeCanvasGraphError("Canvas graph execution requires a persisted graph id and revision")
        raw_targets = body.get("targetNodeIds")
        target_node_ids = [str(item) for item in raw_targets if str(item).strip()] if isinstance(raw_targets, list) else []
        # Operation identity is service-owned.  The browser is allowed to name
        # graph targets, never to impersonate a previous graph operation.
        canvas_operation_id = f"canvas-operation-{uuid4().hex}"
        graph_run_id = f"canvas-run-{uuid4().hex}"
        # Fail synchronously for a stale graph or invalid typed graph. Provider
        # readiness is still enforced by the executor and reported on the run.
        await asyncio.to_thread(
            creative_canvas_graph_service.prepare_direct_execution,
            session_id=session_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            target_node_ids=target_node_ids,
        )
        task = asyncio.create_task(creative_canvas_graph_service.execute_as_creative_job(
            creative_media_runtime,
            {
                "modality": "workflow",
                "operationKind": "canvas.graph.execute",
                "sessionId": session_id,
                "graphId": graph_id,
                "graphRevision": graph_revision,
                "canvasOperationId": canvas_operation_id,
                "graphRunId": graph_run_id,
                "targetNodeIds": target_node_ids,
            },
        ))

        def consume_background_error(completed: asyncio.Task[object]) -> None:
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                # execute_as_creative_job persists the concrete failure on the
                # graph run; retrieving it here prevents an unobserved-task log.
                pass

        task.add_done_callback(consume_background_error)
        # Let the task claim its persisted run before the client begins polling.
        await asyncio.sleep(0)
        return {
            "accepted": True,
            "graphRunId": graph_run_id,
            "canvasOperationId": canvas_operation_id,
            "status": "queued",
        }
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/graph/runs/{graph_run_id}/cancel")
async def cancel_canvas_graph_run(session_id: str, graph_run_id: str, body: dict = Body(default_factory=dict)):
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return await creative_canvas_graph_service.cancel_run(
            creative_media_runtime,
            session_id=session_id,
            graph_run_id=graph_run_id,
            reason=str(body.get("reason") or "user_cancelled"),
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/graph/runs/{graph_run_id}/retry-failed-branch")
async def retry_canvas_graph_failed_branch(session_id: str, graph_run_id: str):
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        claim = await asyncio.to_thread(
            creative_canvas_graph_service.claim_failed_retry,
            session_id=session_id,
            graph_run_id=graph_run_id,
        )
        task = asyncio.create_task(creative_canvas_graph_service.retry_failed_run(
            creative_media_runtime,
            session_id=session_id,
            graph_run_id=graph_run_id,
            claim=claim,
        ))

        def consume_background_error(completed: asyncio.Task[object]) -> None:
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                # The original persisted run records retry failure details.
                pass

        task.add_done_callback(consume_background_error)
        run = dict(claim.get("projectedRun") or {})
        return {"accepted": True, "status": str(run.get("status") or "running"), "run": run}
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/graph")
async def save_canvas_graph(session_id: str, body: dict = Body(...)):
    try:
        return await asyncio.to_thread(
            creative_canvas_graph_service.save_graph,
            session_id=session_id,
            graph=dict(body.get("graph") or {}),
            expected_revision=int(body.get("expectedRevision") or 0),
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/graph/history/{direction}")
async def apply_canvas_graph_history(session_id: str, direction: str, body: dict = Body(...)):
    try:
        return await asyncio.to_thread(
            creative_canvas_graph_service.apply_history,
            session_id=session_id,
            direction=direction,
            expected_revision=int(body.get("expectedRevision") or 0),
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/graph/validate")
async def validate_canvas_graph(session_id: str, body: dict = Body(...)):
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        candidates = await asyncio.to_thread(creative_media_runtime.list_model_candidates)
        available_operation_kinds = {
            str(item.get("operationKind") or "")
            for item in candidates
            if bool(item.get("available")) and not bool(item.get("briefOnly"))
        }
        unavailable_operation_reasons: dict[str, str] = {}
        from core.runtime.feature_packs import resolve_feature_pack_asset
        if resolve_feature_pack_asset("creative_media_motion_capture", "holistic_landmarker") is None:
            unavailable_operation_reasons["video.extract_holistic_motion"] = "motion_capture_feature_pack_unavailable"
        try:
            from runtimes.plugin_manager import plugin_manager_service
            godot_setup = await asyncio.to_thread(plugin_manager_service.plugin_setup, "godot", probe=False)
            godot_status = dict(godot_setup.get("status") or {})
            if not godot_status.get("offlinePrerequisitesReady"):
                unavailable_operation_reasons["model3d.retarget_motion_godot"] = "godot_offline_configuration_unavailable"
            elif str(godot_setup.get("scenario") or "").lower() != "3d":
                unavailable_operation_reasons["model3d.retarget_motion_godot"] = "godot_scenario_must_be_3d"
        except Exception:
            unavailable_operation_reasons["model3d.retarget_motion_godot"] = "godot_plugin_configuration_unavailable"
        return await asyncio.to_thread(
            creative_canvas_graph_service.preflight_execution,
            session_id=session_id,
            graph_id=str(body.get("graphId") or ""),
            graph_revision=int(body.get("graphRevision") or 0),
            target_node_ids=[str(item) for item in list(body.get("targetNodeIds") or [])],
            available_operation_kinds=available_operation_kinds,
            unavailable_operation_reasons=unavailable_operation_reasons,
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/templates")
async def list_canvas_templates(session_id: str):
    try:
        templates = await asyncio.to_thread(creative_canvas_graph_service.list_templates, session_id=session_id)
        return {"templates": templates}
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/templates")
async def save_canvas_template(session_id: str, body: dict = Body(...)):
    try:
        return await asyncio.to_thread(
            creative_canvas_graph_service.save_template,
            session_id=session_id,
            title=str(body.get("title") or ""),
            description=str(body.get("description") or ""),
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/templates/{template_id}")
async def get_canvas_template(session_id: str, template_id: str):
    try:
        return await asyncio.to_thread(
            creative_canvas_graph_service.get_template,
            session_id=session_id,
            template_id=template_id,
        )
    except Exception as error:
        _raise_canvas_http(error)


@router.delete("/sessions/{session_id}/canvas/templates/{template_id}")
async def delete_canvas_template(session_id: str, template_id: str):
    try:
        await asyncio.to_thread(
            creative_canvas_graph_service.delete_template,
            session_id=session_id,
            template_id=template_id,
        )
        return {"deleted": True, "templateId": template_id}
    except Exception as error:
        _raise_canvas_http(error)


@router.post("/sessions/{session_id}/canvas/templates/{template_id}/instantiate")
async def instantiate_canvas_template(session_id: str, template_id: str, body: dict = Body(...)):
    try:
        return await asyncio.to_thread(
            creative_canvas_graph_service.instantiate_template,
            session_id=session_id,
            template_id=template_id,
            expected_revision=int(body.get("expectedRevision") or 0),
            mode=str(body.get("mode") or "append"),
        )
    except Exception as error:
        _raise_canvas_http(error)
