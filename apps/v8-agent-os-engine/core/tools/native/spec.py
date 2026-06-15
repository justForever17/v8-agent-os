from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Any, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.graph import END
from langgraph.types import Command

from core.spec_service import spec_service
from erc.command_service import command_service
from erc.models import ApprovalRequest
from erc.runtime_context import get_runtime_context


_SPEC_STAGES = {"requirements", "bugfix", "design", "tasks"}
_SPEC_KINDS = {"feature", "bugfix"}
_DOWNSTREAM_STAGE_SUFFIXES = ("-requirements", "-bugfix", "-design", "-tasks")


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


def _request_spec_stage_approval(
    *,
    session_id: str,
    run_id: str,
    spec_id: str,
    stage: str,
    summary: str,
    pipeline: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = f"spec_stage_approval:{spec_id}:{stage}"
    return command_service.request_approval(
        ApprovalRequest(
            approval_id=f"approval_{uuid.uuid4().hex}",
            session_id=session_id,
            run_id=run_id,
            approval_kind="spec_stage_approval",
            request={
                "approvalKind": "spec_stage_approval",
                "specId": spec_id,
                "stage": stage,
                "summary": summary,
                "detailRef": f"spec://{spec_id}/{stage}" if spec_id and stage else "",
                "pipelineControl": pipeline,
                "operationFingerprint": fingerprint,
                "operationTargetFingerprint": fingerprint,
                "recommendedNextAction": "Approve or request revision for this Spec stage.",
            },
        )
    )


def _maybe_stop_for_spec_stage_approval(result: dict[str, Any], *, tool_call_id: str = "") -> Command | None:
    """Stop the live chat turn after a Spec stage is ready for user approval.

    Direct calls from tests/scripts should keep receiving the JSON payload. A
    real ChatRuntime tool invocation has run/session ids in runtime context.

    Spec stage approval is a pipeline gate, not a normal tool result. Return a
    graph command that ends the current turn; ChatRuntime finalization reads the
    Spec pipeline state and moves the run to waiting_input. The client or live
    harness then approves the stage and resumes a fresh Supervisor turn.
    """
    if not bool(result.get("ok")):
        return None
    pipeline = result.get("pipelineControl") if isinstance(result.get("pipelineControl"), dict) else {}
    blocked_stage = str(pipeline.get("blockedByApproval") or "").strip().lower()
    stage = str(result.get("stage") or pipeline.get("currentStage") or "").strip().lower()
    if not blocked_stage or blocked_stage != stage:
        return None
    runtime_context = get_runtime_context() or {}
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    session_id = str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip()
    if not run_id or not session_id:
        return None
    spec_id = str(result.get("specId") or "").strip()
    summary = str(result.get("summary") or f"Spec stage '{stage}' is ready for review.").strip()
    approval = _request_spec_stage_approval(
        session_id=session_id,
        run_id=run_id,
        spec_id=spec_id,
        stage=stage,
        summary=summary,
        pipeline=pipeline,
    )
    payload = _spec_broker_payload(
        ok=True,
        kind="spec_stage_waiting_user_approval",
        specId=spec_id,
        stage=stage,
        summary=summary,
        approvalId=approval.get("approval_id"),
        approvalKind=approval.get("approval_kind") or "spec_stage_approval",
        approvalStatus=approval.get("status") or "pending",
        detailRef=f"spec://{spec_id}/{stage}" if spec_id and stage else "",
        pipelineControl=pipeline,
        recommendedNextAction="Wait for the Spec approval gate before continuing to the next Spec stage.",
    )
    return Command(
        goto=END,
        update={
            "messages": [
                ToolMessage(
                    content=payload,
                    tool_call_id=str(tool_call_id or f"spec_broker:{spec_id}:{stage}"),
                )
            ],
        },
    )


def _spec_stage_from_inputs(stage: Optional[str], kind: Optional[str]) -> str:
    for value in (stage, kind):
        normalized = str(value or "").strip().lower()
        if normalized in _SPEC_STAGES:
            return normalized
    return str(stage or "").strip().lower()


def _spec_kind_from_inputs(kind: Optional[str], stage: Optional[str]) -> Optional[str]:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind in _SPEC_KINDS:
        return normalized_kind
    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage == "bugfix":
        return "bugfix"
    if normalized_stage in {"requirements", "design", "tasks"}:
        return "feature"
    return None


def _spec_match_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _spec_strip_stage_suffix(value: str) -> str:
    text = _spec_match_text(value)
    for suffix in _DOWNSTREAM_STAGE_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)].strip("-_ ")
    return text


def _spec_tokenize_for_match(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]", text or ""):
        cleaned = token.strip().lower()
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    return tokens[:80]


def _spec_resolve_active_spec_id(
    *,
    workspace: str,
    spec_id: Optional[str],
    feature_name: Optional[str],
    user_request: str,
    content: str,
    comment: str,
) -> str:
    explicit = str(spec_id or "").strip()
    try:
        listing = spec_service.list_specs(workspace_path=workspace, include_archived=False, limit=30)
    except Exception:
        return explicit
    candidates = [item for item in list(listing.get("specs") or []) if isinstance(item, dict)]
    if not candidates:
        return explicit
    feature = _spec_match_text(feature_name)
    feature_alias = _spec_strip_stage_suffix(feature)
    explicit_alias = _spec_strip_stage_suffix(explicit)

    if explicit:
        exact_id = [item for item in candidates if str(item.get("specId") or "").strip() == explicit]
        if len(exact_id) == 1:
            return str(exact_id[0].get("specId") or "").strip()
        exact_named = [
            item
            for item in candidates
            if explicit_alias
            and (
                explicit_alias in _spec_match_text(item.get("featureName") or item.get("slug"))
                or _spec_match_text(item.get("featureName") or item.get("slug")) in explicit_alias
            )
        ]
        if len(exact_named) == 1:
            return str(exact_named[0].get("specId") or "").strip()
        if explicit.startswith("spec_"):
            return explicit

    if feature:
        exact = [
            item
            for item in candidates
            if feature_alias
            and (
                feature_alias in _spec_match_text(item.get("featureName") or item.get("slug"))
                or _spec_match_text(item.get("featureName") or item.get("slug")) in feature_alias
            )
        ]
        if len(exact) == 1:
            return str(exact[0].get("specId") or "").strip()
    if len(candidates) == 1:
        return str(candidates[0].get("specId") or "").strip()

    query = " ".join([explicit, str(feature_name or ""), str(user_request or ""), str(content or "")[:3000], str(comment or "")])
    tokens = _spec_tokenize_for_match(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in candidates:
        haystack = " ".join(
            [
                str(item.get("featureName") or ""),
                str(item.get("slug") or ""),
                str(item.get("sourceRequest") or ""),
                str(item.get("specId") or ""),
            ]
        ).lower()
        score = sum(1 for token in tokens if token and token.lower() in haystack)
        if feature_alias and feature_alias in haystack:
            score += 10
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return str(scored[0][1].get("specId") or "").strip()
    if explicit:
        return explicit
    return str(candidates[0].get("specId") or "").strip()


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
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str | Command:
    """Controlled Spec Mode tool for writing and governing Spec documents.

    Use this tool for Spec artifacts only: requirements/bugfix, design, tasks,
    approvals, revisions, and section reads under `.v8/specs/<feature>/`.
    It does not write final project deliverables such as SKILL.md or source files.

    Common modes:
    - `start`, `create`, `write_stage`: create/write a stage; pass the full Markdown draft in `content`.
    - `edit`, `write`, `update`, `rewrite_stage`: rewrite the current stage or inferred active stage.
    - `replace_section`, `append_section`: local stage edits by stable section ID.
    - `read`, `read_stage`, `read_section`: read the current stage/section.
    - `approve`, `revise`, `brief`, `list`: approve, request changes, inspect status, or list Specs.

    Stages are `requirements` or `bugfix`, then `design`, then `tasks`. The
    `tasks` stage must be a pipeline-ready contract with TASK IDs, runtime lane,
    dependencies, Spec refs, expected output, and acceptance/proof expectations.
    After `tasks` is approved, route execution with `runtime_broker`; do not use
    Spec tools or Supervisor direct file tools as the implementation path.
    """
    normalized_mode = str(mode or "start").strip().lower()
    if normalized_mode in {"edit", "write", "update"}:
        normalized_mode = "rewrite_stage"
    if normalized_mode == "read_stage":
        normalized_mode = "read"
    workspace = _spec_broker_workspace(workspace_path)
    if not workspace:
        return _spec_broker_payload(
            ok=False,
            kind="spec_workspace_missing",
            summary="spec_broker needs an active workspace or explicit workspace_path.",
            recommendedNextAction="Call spec_broker again with workspace_path, or bind the current chat to a workspace.",
        )
    try:
        if normalized_mode == "stage":
            inferred_spec_id = _spec_resolve_active_spec_id(
                workspace=workspace,
                spec_id=spec_id,
                feature_name=feature_name,
                user_request=user_request,
                content=content,
                comment=comment,
            )
            if inferred_spec_id:
                spec_id = inferred_spec_id
                normalized_mode = "rewrite_stage"
        if normalized_mode in {"start", "create", "create_stage", "write_stage", "stage"}:
            requested_stage = _spec_stage_from_inputs(stage, kind)
            requested_kind = _spec_kind_from_inputs(kind, requested_stage)
            if requested_stage in {"design", "tasks"}:
                inferred_spec_id = _spec_resolve_active_spec_id(
                    workspace=workspace,
                    spec_id=spec_id,
                    feature_name=feature_name,
                    user_request=user_request,
                    content=content,
                    comment=comment,
                )
                if inferred_spec_id:
                    spec_id = inferred_spec_id
            result = spec_service.create_stage(
                workspace_path=workspace,
                user_request=user_request,
                feature_name=feature_name,
                spec_id=spec_id,
                stage=requested_stage or stage,
                kind=requested_kind,
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
            stop_command = _maybe_stop_for_spec_stage_approval(result, tool_call_id=tool_call_id)
            if stop_command is not None:
                return stop_command
            return _spec_broker_payload(**result)
        if normalized_mode in {"list", "catalog"}:
            result = spec_service.list_specs(workspace_path=workspace, include_archived=False, limit=max(1, min(int(max_chars or 20), 50)))
            result.setdefault("recommendedNextAction", "Use the desired specId with spec_broker(mode='brief'/'read_section'/'approve').")
            return _spec_broker_payload(**result)
        if normalized_mode in {"approve", "approve_stage"}:
            resolved_spec_id = _spec_resolve_active_spec_id(
                workspace=workspace,
                spec_id=spec_id,
                feature_name=feature_name,
                user_request=user_request,
                content=content,
                comment=comment,
            )
            if not resolved_spec_id:
                return _spec_broker_payload(
                    ok=False,
                    kind="spec_id_required",
                    summary="spec_broker approve needs spec_id.",
                    recommendedNextAction="Use specId returned by spec_broker(mode='start'/'brief').",
                )
            result = spec_service.approve_stage(
                workspace_path=workspace,
                spec_id=str(resolved_spec_id),
                stage=_spec_stage_from_inputs(stage, kind),
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
            resolved_spec_id = _spec_resolve_active_spec_id(
                workspace=workspace,
                spec_id=spec_id,
                feature_name=feature_name,
                user_request=user_request,
                content=content,
                comment=comment,
            )
            if not resolved_spec_id:
                return _spec_broker_payload(
                    ok=False,
                    kind="spec_id_required",
                    summary=f"spec_broker {normalized_mode} needs spec_id when no unique active Spec can be inferred.",
                    recommendedNextAction="Call spec_broker(mode='brief', spec_id='<id>') or retry with the specId returned by the current Spec stage.",
                )
            resolved_stage = _spec_stage_from_inputs(stage, kind)
            edit_content = content or user_request or comment
            if normalized_mode == "rewrite_stage" and resolved_stage:
                created = spec_service.create_stage(
                    workspace_path=workspace,
                    user_request=user_request or edit_content,
                    feature_name=feature_name,
                    spec_id=resolved_spec_id,
                    stage=resolved_stage,
                    overwrite=False,
                )
                if not created.get("ok") and str(created.get("kind") or "") == "spec_stage_blocked":
                    return _spec_broker_payload(**created)
            result = spec_service.edit_stage(
                workspace_path=workspace,
                spec_id=str(resolved_spec_id),
                stage=resolved_stage,
                action=normalized_mode,
                content=edit_content,
                section_ref=section_ref,
                reason=comment or normalized_mode,
            )
            result.setdefault("recommendedNextAction", "Show the edited stage to the user for approval before moving downstream.")
            stop_command = _maybe_stop_for_spec_stage_approval(result, tool_call_id=tool_call_id)
            if stop_command is not None:
                return stop_command
            return _spec_broker_payload(**result)
        if normalized_mode in {"read", "read_section", "section"}:
            resolved_spec_id = _spec_resolve_active_spec_id(
                workspace=workspace,
                spec_id=spec_id,
                feature_name=feature_name,
                user_request=user_request,
                content=content,
                comment=comment,
            )
            if not resolved_spec_id:
                return _spec_broker_payload(ok=False, kind="spec_id_required", summary="spec_broker read_section needs spec_id.")
            result = spec_service.read_section(
                workspace_path=workspace,
                spec_id=str(resolved_spec_id),
                stage=_spec_stage_from_inputs(stage, kind),
                section_ref=section_ref,
                max_chars=max(500, min(int(max_chars or 4000), 12000)),
            )
            return _spec_broker_payload(**result)
        if normalized_mode in {"brief", "status"}:
            resolved_spec_id = _spec_resolve_active_spec_id(
                workspace=workspace,
                spec_id=spec_id,
                feature_name=feature_name,
                user_request=user_request,
                content=content,
                comment=comment,
            )
            if not resolved_spec_id:
                return _spec_broker_payload(ok=False, kind="spec_id_required", summary="spec_broker brief needs spec_id.")
            result = spec_service.build_brief(workspace_path=workspace, spec_id=str(resolved_spec_id))
            return _spec_broker_payload(ok=True, kind="spec_brief", specBrief=result)
        return _spec_broker_payload(
            ok=False,
            kind="unsupported_spec_broker_mode",
            summary=f"Unsupported spec_broker mode: {normalized_mode}",
            supportedModes=["start", "list", "approve", "revise", "replace_section", "append_section", "rewrite_stage", "edit", "read", "read_stage", "read_section", "brief"],
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
