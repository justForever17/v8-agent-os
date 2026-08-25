from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from core.ui_patch import ui_patch_service


router = APIRouter(tags=["ui-patch"])


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (FileNotFoundError, LookupError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="UI Patch Workbench request failed") from exc


@router.post("/sessions/{session_id}/ui-patch/previews")
async def create_ui_patch_preview(session_id: str, body: dict[str, Any] = Body(...)):
    try:
        return await asyncio.to_thread(
            ui_patch_service.create_preview,
            session_id=session_id,
            parent_origin=str(body.get("parentOrigin") or body.get("parent_origin") or ""),
            entry_path=str(body.get("entryPath") or body.get("entry_path") or ""),
            target_url=str(body.get("targetUrl") or body.get("target_url") or ""),
            project_path=str(body.get("projectPath") or body.get("project_path") or ""),
            start_dev_server=bool(body.get("startDevServer", body.get("start_dev_server", True))),
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/sessions/{session_id}/ui-patch/projects/inspect")
async def inspect_ui_patch_project(session_id: str, body: dict[str, Any] = Body(...)):
    try:
        return await asyncio.to_thread(
            ui_patch_service.inspect_project,
            session_id=session_id,
            project_path=str(body.get("projectPath") or body.get("project_path") or ""),
        )
    except Exception as exc:
        _raise_http_error(exc)

@router.get("/sessions/{session_id}/ui-patch/previews/{patch_session_id}")
async def get_ui_patch_preview(session_id: str, patch_session_id: str):
    try:
        return await asyncio.to_thread(
            ui_patch_service.get_preview,
            session_id=session_id,
            patch_session_id=patch_session_id,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.delete("/sessions/{session_id}/ui-patch/previews/{patch_session_id}")
async def close_ui_patch_preview(session_id: str, patch_session_id: str):
    try:
        return await asyncio.to_thread(
            ui_patch_service.close_preview,
            session_id=session_id,
            patch_session_id=patch_session_id,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/sessions/{session_id}/ui-patch/previews/{patch_session_id}/selections")
async def map_ui_patch_selection(session_id: str, patch_session_id: str, body: dict[str, Any] = Body(...)):
    try:
        selection = body.get("selection")
        if not isinstance(selection, dict):
            raise ValueError("selection is required")
        return await asyncio.to_thread(
            ui_patch_service.map_selection,
            session_id=session_id,
            patch_session_id=patch_session_id,
            selection=selection,
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/sessions/{session_id}/ui-patch/previews/{patch_session_id}/commits")
async def commit_ui_patch(session_id: str, patch_session_id: str, body: dict[str, Any] = Body(...)):
    try:
        return await asyncio.to_thread(
            ui_patch_service.commit,
            session_id=session_id,
            patch_session_id=patch_session_id,
            selection_ref=str(body.get("selectionRef") or body.get("selection_ref") or ""),
            candidate_id=str(body.get("candidateId") or body.get("candidate_id") or ""),
            changes=dict(body.get("changes") or {}),
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/sessions/{session_id}/ui-patch/transactions/{transaction_id}/verification")
async def record_ui_patch_verification(session_id: str, transaction_id: str, body: dict[str, Any] = Body(...)):
    try:
        return await asyncio.to_thread(
            ui_patch_service.record_verification,
            session_id=session_id,
            transaction_id=transaction_id,
            status=str(body.get("status") or ""),
            observed_styles=dict(body.get("observedStyles") or body.get("observed_styles") or {}),
            reason=str(body.get("reason") or ""),
        )
    except Exception as exc:
        _raise_http_error(exc)


@router.post("/sessions/{session_id}/ui-patch/transactions/{transaction_id}/undo")
async def undo_ui_patch(session_id: str, transaction_id: str):
    try:
        return await asyncio.to_thread(
            ui_patch_service.undo,
            session_id=session_id,
            transaction_id=transaction_id,
        )
    except Exception as exc:
        _raise_http_error(exc)
