from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from core.spec_service import spec_service

router = APIRouter(prefix="/specs", tags=["specs"])


def _workspace_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("workspacePath") or payload.get("workspace_path") or "").strip()


def _raise_spec_error(error: Exception) -> None:
    message = str(error)
    status = 404 if "not_found" in message else 400
    raise HTTPException(status_code=status, detail=message)


@router.get("")
async def list_specs(
    workspace_path: str = Query(..., alias="workspace_path"),
    include_archived: bool = False,
    limit: int = 100,
):
    try:
        return spec_service.list_specs(
            workspace_path=workspace_path,
            include_archived=include_archived,
            limit=limit,
        )
    except Exception as error:
        _raise_spec_error(error)


@router.get("/{spec_id}")
async def read_spec(
    spec_id: str,
    workspace_path: str = Query(..., alias="workspace_path"),
    max_chars: int = 60000,
):
    try:
        return spec_service.read_spec(
            workspace_path=workspace_path,
            spec_id=spec_id,
            max_chars=max_chars,
        )
    except Exception as error:
        _raise_spec_error(error)


@router.get("/{spec_id}/stages/{stage}")
async def read_spec_stage(
    spec_id: str,
    stage: str,
    workspace_path: str = Query(..., alias="workspace_path"),
    section_ref: str = "",
    max_chars: int = 60000,
):
    try:
        return spec_service.read_section(
            workspace_path=workspace_path,
            spec_id=spec_id,
            stage=stage,
            section_ref=section_ref or None,
            max_chars=max_chars,
        )
    except Exception as error:
        _raise_spec_error(error)


@router.post("/{spec_id}/stages/{stage}/approve")
async def approve_spec_stage(spec_id: str, stage: str, payload: dict[str, Any] = Body(default_factory=dict)):
    workspace_path = _workspace_from_payload(payload)
    try:
        return spec_service.approve_stage(
            workspace_path=workspace_path,
            spec_id=spec_id,
            stage=stage,
            approver=str(payload.get("approver") or payload.get("userEmail") or "user"),
            comment=str(payload.get("comment") or ""),
        )
    except Exception as error:
        _raise_spec_error(error)


@router.post("/{spec_id}/stages/{stage}/revise")
async def revise_spec_stage(spec_id: str, stage: str, payload: dict[str, Any] = Body(default_factory=dict)):
    workspace_path = _workspace_from_payload(payload)
    try:
        return spec_service.request_revision(
            workspace_path=workspace_path,
            spec_id=spec_id,
            stage=stage,
            section_ref=str(payload.get("sectionRef") or payload.get("section_ref") or ""),
            comment=str(payload.get("comment") or payload.get("reason") or ""),
        )
    except Exception as error:
        _raise_spec_error(error)


@router.post("/{spec_id}/stages/{stage}/edit")
async def edit_spec_stage(spec_id: str, stage: str, payload: dict[str, Any] = Body(default_factory=dict)):
    workspace_path = _workspace_from_payload(payload)
    try:
        return spec_service.edit_stage(
            workspace_path=workspace_path,
            spec_id=spec_id,
            stage=stage,
            action=str(payload.get("action") or "replace_section"),
            section_ref=str(payload.get("sectionRef") or payload.get("section_ref") or ""),
            content=str(payload.get("content") or ""),
            reason=str(payload.get("reason") or "client_spec_edit"),
        )
    except Exception as error:
        _raise_spec_error(error)
