from __future__ import annotations

import asyncio
import io
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Response

from core.creative_canvas_graph import (
    CreativeCanvasGraphConflict,
    CreativeCanvasGraphError,
    creative_canvas_graph_service,
)
from core.tools.native.creative_media_psd import inspect_psd_manifest, render_psd_preview_image
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


@lru_cache(maxsize=32)
def _cached_psd_preview(path_value: str, modified_ns: int, byte_size: int) -> bytes:
    del modified_ns, byte_size
    buffer = io.BytesIO()
    render_psd_preview_image(Path(path_value)).save(buffer, format="PNG")
    return buffer.getvalue()


def _raise_canvas_http(error: Exception) -> None:
    if isinstance(error, CreativeCanvasGraphConflict):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, (CreativeCanvasGraphError, ValueError)):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/sessions/{session_id}/canvas/actions")
async def list_canvas_actions(session_id: str):
    try:
        await asyncio.to_thread(creative_canvas_graph_service.get_graph, session_id=session_id)
        return {"actions": creative_canvas_graph_service.action_catalog()}
    except Exception as error:
        _raise_canvas_http(error)


@router.get("/sessions/{session_id}/canvas/psd/{origin}/{resource_id}/manifest")
async def get_canvas_psd_manifest(session_id: str, origin: str, resource_id: str):
    try:
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


@router.get("/sessions/{session_id}/canvas/graph")
async def get_canvas_graph(session_id: str):
    try:
        return await asyncio.to_thread(creative_canvas_graph_service.get_graph, session_id=session_id)
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


@router.post("/sessions/{session_id}/canvas/graph/validate")
async def validate_canvas_graph(session_id: str, body: dict = Body(...)):
    try:
        plan = await asyncio.to_thread(
            creative_canvas_graph_service.execution_contract_summary,
            session_id=session_id,
            graph_id=str(body.get("graphId") or ""),
            graph_revision=int(body.get("graphRevision") or 0),
            target_node_ids=[str(item) for item in list(body.get("targetNodeIds") or [])],
        )
        return {"valid": True, "plan": plan}
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
