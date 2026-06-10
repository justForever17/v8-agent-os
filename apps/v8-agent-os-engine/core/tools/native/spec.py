from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.tools import tool

from core.spec_service import spec_service
from erc.runtime_context import get_runtime_context


def _spec_broker_workspace(workspace_path: Optional[str]) -> str:
    explicit = str(workspace_path or "").strip()
    if explicit:
        return explicit
    runtime_context = get_runtime_context() or {}
    for key in ("workspace_path", "workspacePath", "activeWorkspaceRoot", "workspaceRoot"):
        value = runtime_context.get(key) if isinstance(runtime_context, dict) else None
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _spec_compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _spec_broker_payload(**payload: Any) -> str:
    return json.dumps(_spec_compact_dict(payload), ensure_ascii=False, indent=2)


@tool
def spec_broker(
    mode: str = "start",
    workspace_path: Optional[str] = None,
    user_request: str = "",
    feature_name: Optional[str] = None,
    spec_id: Optional[str] = None,
    stage: Optional[str] = None,
    kind: Optional[str] = None,
    comment: str = "",
    content: str = "",
    section_ref: str = "",
    overwrite: bool = False,
    max_chars: int = 4000,
) -> str:
    """Controlled Spec Mode tool for requirements/bugfix, design, tasks, approvals, and section reads.

    Spec documents are the durable delivery contract for execution runtimes. Pass the actual
    Markdown draft in `content` when writing a stage; use edit modes for later revisions.
    """
    normalized_mode = str(mode or "start").strip().lower()
    workspace = _spec_broker_workspace(workspace_path)
    if not workspace:
        return _spec_broker_payload(
            ok=False,
            kind="spec_workspace_missing",
            summary="spec_broker needs an active workspace or explicit workspace_path.",
            recommendedNextAction="Call spec_broker again with workspace_path, or bind the current chat to a workspace.",
        )
    try:
        if normalized_mode in {"start", "create", "create_stage", "write_stage", "stage"}:
            result = spec_service.create_stage(
                workspace_path=workspace,
                user_request=user_request,
                feature_name=feature_name,
                spec_id=spec_id,
                stage=stage,
                kind=kind,
                overwrite=bool(overwrite),
            )
            if result.get("ok") and str(content or "").strip():
                result = spec_service.edit_stage(
                    workspace_path=workspace,
                    spec_id=str(result.get("specId") or spec_id or ""),
                    stage=str(result.get("stage") or stage or ""),
                    action="rewrite_stage",
                    content=str(content),
                    reason=comment or "initial_stage_markdown",
                )
            result.setdefault("recommendedNextAction", "Show the current stage to the user for approval before moving downstream.")
            return _spec_broker_payload(**result)
        if normalized_mode in {"approve", "approve_stage"}:
            if not spec_id:
                return _spec_broker_payload(
                    ok=False,
                    kind="spec_id_required",
                    summary="spec_broker approve needs spec_id.",
                    recommendedNextAction="Use specId returned by spec_broker(mode='start'/'brief').",
                )
            result = spec_service.approve_stage(
                workspace_path=workspace,
                spec_id=str(spec_id),
                stage=str(stage or ""),
                approver="user",
                comment=comment,
            )
            result.setdefault("recommendedNextAction", "Create the next Spec stage, or wait for runtime execution if tasks are approved.")
            return _spec_broker_payload(**result)
        if normalized_mode in {"revise", "request_revision", "comment"}:
            if not spec_id:
                return _spec_broker_payload(ok=False, kind="spec_id_required", summary="spec_broker revision needs spec_id.")
            result = spec_service.request_revision(
                workspace_path=workspace,
                spec_id=str(spec_id),
                stage=str(stage or ""),
                comment=comment,
                section_ref=section_ref,
            )
            return _spec_broker_payload(**result)
        if normalized_mode in {"replace_section", "append_section", "rewrite_stage"}:
            if not spec_id:
                return _spec_broker_payload(ok=False, kind="spec_id_required", summary=f"spec_broker {normalized_mode} needs spec_id.")
            edit_content = content or user_request or comment
            result = spec_service.edit_stage(
                workspace_path=workspace,
                spec_id=str(spec_id),
                stage=str(stage or ""),
                action=normalized_mode,
                content=edit_content,
                section_ref=section_ref,
                reason=comment or normalized_mode,
            )
            result.setdefault("recommendedNextAction", "Show the edited stage to the user for approval before moving downstream.")
            return _spec_broker_payload(**result)
        if normalized_mode in {"read", "read_section", "section"}:
            if not spec_id:
                return _spec_broker_payload(ok=False, kind="spec_id_required", summary="spec_broker read_section needs spec_id.")
            result = spec_service.read_section(
                workspace_path=workspace,
                spec_id=str(spec_id),
                stage=str(stage or ""),
                section_ref=section_ref,
                max_chars=max(500, min(int(max_chars or 4000), 12000)),
            )
            return _spec_broker_payload(**result)
        if normalized_mode in {"brief", "status"}:
            if not spec_id:
                return _spec_broker_payload(ok=False, kind="spec_id_required", summary="spec_broker brief needs spec_id.")
            result = spec_service.build_brief(workspace_path=workspace, spec_id=str(spec_id))
            return _spec_broker_payload(ok=True, kind="spec_brief", specBrief=result)
        return _spec_broker_payload(
            ok=False,
            kind="unsupported_spec_broker_mode",
            summary=f"Unsupported spec_broker mode: {normalized_mode}",
            supportedModes=["start", "approve", "revise", "replace_section", "append_section", "rewrite_stage", "read_section", "brief"],
        )
    except Exception as exc:
        return _spec_broker_payload(
            ok=False,
            kind="spec_broker_error",
            summary=str(exc),
            mode=normalized_mode,
            workspacePath=workspace,
        )


__all__ = [
    "_spec_broker_payload",
    "_spec_broker_workspace",
    "spec_broker",
]
