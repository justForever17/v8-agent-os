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


def _spec_transition_hint(*, spec_id: str, stage: str = "", pipeline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return compact next-step guidance for the Spec state machine."""

    control = dict(pipeline or {})
    current = str(stage or control.get("currentStage") or "").strip().lower()
    next_stage = str(control.get("nextStage") or "").strip().lower()
    blocked = str(control.get("blockedByApproval") or "").strip().lower()
    if blocked:
        downstream = {
            "requirements": "design",
            "bugfix": "design",
            "design": "tasks",
            "tasks": "runtime_execution",
        }.get(blocked, next_stage)
        if downstream == "runtime_execution":
            approved_action = (
                "If the user approves tasks, route the approved Spec to runtime execution with runtime_broker(mode='route')."
            )
        else:
            approved_action = (
                f"If the user approves {blocked}, write only the next stage {downstream} with "
                f"spec_broker(mode='write_stage', spec_id='<current specId>', stage='{downstream}', content='<complete markdown>')."
            )
        return {
            "state": "waiting_user_approval",
            "specId": spec_id,
            "currentStage": blocked,
            "nextStageAfterApproval": downstream,
            "ifApproved": approved_action,
            "ifRevisionRequested": (
                f"Edit only the current unapproved stage {blocked} using replace_section, append_section, or rewrite_stage."
            ),
            "doNot": [
                "Do not move downstream before the user/client approval event.",
                "Do not self-approve this stage.",
            ],
        }
    if bool(control.get("runtimeExecutionAllowed")) or next_stage == "runtime_execution":
        return {
            "state": "runtime_execution_ready",
            "specId": spec_id,
            "currentStage": current,
            "nextStage": "runtime_execution",
            "requiredNextTool": "runtime_broker",
            "whenReady": (
                "Call runtime_broker(mode='route', runtime_kind='engineering', "
                "need={'kind':'engineering','reason':'approved_spec_runtime_execution','specId':'<current specId>'}) "
                "and wait for the runtime episode handoff."
            ),
            "doNot": [
                "Do not rewrite requirements/design/tasks.",
                "Do not implement final deliverables through spec_broker.",
                "Do not treat this as Supervisor self-approval.",
            ],
        }
    if next_stage in _SPEC_STAGES:
        return {
            "state": "stage_ready_to_write",
            "specId": spec_id,
            "currentStage": current,
            "nextStage": next_stage,
            "requiredNextTool": "spec_broker",
            "whenReady": (
                f"Write stage {next_stage} with spec_broker(mode='write_stage', spec_id='<current specId>', "
                f"stage='{next_stage}', content='<complete markdown>')."
            ),
        }
    return {
        "state": "inspect_spec_brief",
        "specId": spec_id,
        "currentStage": current,
        "nextStage": next_stage,
        "whenReady": "Call spec_broker(mode='brief') and follow specBrief.pipelineControl.nextStage.",
    }


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
        transitionHint=_spec_transition_hint(spec_id=spec_id, stage=stage, pipeline=pipeline),
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


def _spec_context_spec_id() -> str:
    runtime_context = get_runtime_context() or {}
    if not isinstance(runtime_context, dict):
        return ""
    continuation = runtime_context.get("specContinuation")
    if isinstance(continuation, dict):
        value = str(continuation.get("specId") or continuation.get("spec_id") or "").strip()
        if value:
            return value
    for key in ("spec_id", "specId", "currentSpecId", "activeSpecId"):
        value = str(runtime_context.get(key) or "").strip()
        if value:
            return value
    for key in ("specBrief", "currentSpec", "activeSpec"):
        value = runtime_context.get(key)
        if isinstance(value, dict):
            nested = str(value.get("specId") or value.get("spec_id") or "").strip()
            if nested:
                return nested
    return ""


def _spec_context_continuation() -> dict[str, Any]:
    runtime_context = get_runtime_context() or {}
    if not isinstance(runtime_context, dict):
        return {}
    continuation = runtime_context.get("specContinuation")
    if isinstance(continuation, dict) and str(continuation.get("specId") or "").strip():
        return dict(continuation)
    return {}


def _spec_context_next_stage() -> str:
    continuation = _spec_context_continuation()
    if continuation:
        next_stage = str(continuation.get("nextStage") or "").strip().lower()
        if next_stage in _SPEC_STAGES:
            return next_stage
    runtime_context = get_runtime_context() or {}
    if not isinstance(runtime_context, dict):
        return ""
    for key in ("spec_next_stage", "specNextStage"):
        value = str(runtime_context.get(key) or "").strip().lower()
        if value in _SPEC_STAGES:
            return value
    return ""


def _spec_approve_blocked_for_supervisor() -> bool:
    runtime_context = get_runtime_context() or {}
    if not isinstance(runtime_context, dict):
        return False
    runtime_kind = str(runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or "").strip().lower()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    session_id = str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip()
    if runtime_kind != "chat" or not run_id or not session_id:
        return False
    approval_actor = str(
        runtime_context.get("spec_approval_actor")
        or runtime_context.get("specApprovalActor")
        or ""
    ).strip().lower()
    return approval_actor not in {"user", "client", "admin", "live_harness"}


def _spec_stage_mismatch_payload(*, attempted_stage: str, expected_stage: str, spec_id: str) -> str:
    return _spec_broker_payload(
        ok=False,
        kind="spec_stage_mismatch",
        summary=(
            f"Current Spec continuation expects stage '{expected_stage}', "
            f"but the tool call attempted stage '{attempted_stage or 'unspecified'}'."
        ),
        specId=spec_id,
        attemptedStage=attempted_stage,
        expectedStage=expected_stage,
        recommendedNextAction=(
            "Call spec_broker with mode='write_stage', the current specId, "
            f"stage='{expected_stage}', and the full Markdown draft for that stage."
        ),
    )


def _spec_id_mismatch_payload(*, attempted_spec_id: str, expected_spec_id: str) -> str:
    return _spec_broker_payload(
        ok=False,
        kind="spec_id_mismatch",
        summary=(
            f"Current Spec continuation is bound to specId '{expected_spec_id}', "
            f"but the tool call attempted specId '{attempted_spec_id}'."
        ),
        attemptedSpecId=attempted_spec_id,
        expectedSpecId=expected_spec_id,
        recommendedNextAction=(
            "Retry with the current continuation specId, or call spec_broker(mode='brief') "
            "without spec_id to inspect the active Spec."
        ),
    )


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
    context_spec_id = "" if explicit else _spec_context_spec_id()
    try:
        listing = spec_service.list_specs(workspace_path=workspace, include_archived=False, limit=30)
    except Exception:
        return explicit or context_spec_id
    candidates = [item for item in list(listing.get("specs") or []) if isinstance(item, dict)]
    if not candidates:
        return explicit or context_spec_id
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

    if context_spec_id:
        exact_context = [item for item in candidates if str(item.get("specId") or "").strip() == context_spec_id]
        if len(exact_context) == 1:
            return context_spec_id
        if context_spec_id.startswith("spec_"):
            return context_spec_id

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


def _latest_runtime_execution_ready_spec(workspace: str) -> dict[str, Any]:
    try:
        listing = spec_service.list_specs(workspace_path=workspace, include_archived=False, limit=10)
    except Exception:
        return {}
    for item in list(listing.get("specs") or []):
        if not isinstance(item, dict):
            continue
        spec_id = str(item.get("specId") or "").strip()
        if not spec_id:
            continue
        try:
            brief = spec_service.build_brief(workspace_path=workspace, spec_id=spec_id)
        except Exception:
            continue
        pipeline = dict(brief.get("pipelineControl") or {})
        if bool(pipeline.get("runtimeExecutionAllowed")) and str(brief.get("lifecycle") or "").strip().lower() == "active":
            return brief
    return {}


def _spec_runtime_execution_active_payload(*, active_spec: dict[str, Any]) -> str:
    spec_id = str(active_spec.get("specId") or "").strip()
    return _spec_broker_payload(
        ok=False,
        kind="spec_runtime_execution_active",
        summary=(
            "当前工作区已有一个 tasks 已审批、等待执行/修复的 Spec。"
            "不要新建 bugfix/requirements Spec 来绕过当前交付合同。"
        ),
        specId=spec_id,
        featureName=active_spec.get("featureName"),
        pipelineControl=active_spec.get("pipelineControl"),
        recommendedNextAction=(
            "继续使用当前 specId，通过 runtime_broker(mode='route', need={'kind':'engineering','specId':'<current specId>'}) "
            "执行或修复；需要查看内容时用 spec_broker(mode='read_section'/'brief')。"
        ),
    )


def _spec_missing_stage_payload(
    *,
    workspace: str,
    spec_id: str,
    stage: str,
    error: str,
) -> str:
    """Return a state-machine style response when a stage document is absent."""

    brief: dict[str, Any] = {}
    try:
        brief = spec_service.build_brief(workspace_path=workspace, spec_id=spec_id)
    except Exception:
        brief = {}
    pipeline = dict(brief.get("pipelineControl") or {}) if isinstance(brief, dict) else {}
    transition = _spec_transition_hint(spec_id=spec_id, stage=stage, pipeline=pipeline)
    next_stage = str(pipeline.get("nextStage") or transition.get("nextStage") or "").strip().lower()
    requested = str(stage or "").strip().lower()
    if requested and next_stage and requested != next_stage:
        summary = (
            f"Spec stage '{requested}' does not exist yet. The current pipeline expects '{next_stage}' next."
        )
    elif requested:
        summary = f"Spec stage '{requested}' does not exist yet and should be written next."
    else:
        summary = "The requested Spec stage does not exist yet; inspect pipelineControl.nextStage before writing."
    return _spec_broker_payload(
        ok=False,
        kind="spec_stage_missing",
        summary=summary,
        error=error,
        specId=spec_id,
        stage=requested,
        nextStage=next_stage or None,
        pipelineControl=pipeline,
        transitionHint=transition,
        specBrief=brief,
        recommendedNextAction=(
            f"Call spec_broker(mode='write_stage', spec_id='{spec_id}', stage='{next_stage or requested}', "
            "content='<complete markdown document>') for the pipeline next stage."
            if (next_stage or requested) in _SPEC_STAGES
            else "Call spec_broker(mode='brief') and follow specBrief.pipelineControl.nextStage."
        ),
    )


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
    """Write/read/edit Spec Mode documents under `.v8/specs/<feature>`; user/client approval gates advance stages.

    It writes and reads Spec contract documents only: `requirements.md` or
    `bugfix.md`, then `design.md`, then `tasks.md`. It never writes final
    deliverables such as source files, SKILL.md, images, or README.md.

    Engine creates the canonical `specId`; never invent one. If the current run
    is already bound to a Spec, omit `spec_id` or reuse the provided/current
    `specId`. When unsure, first call `spec_broker(mode='brief')` and follow
    `specBrief.pipelineControl.nextStage`.

    Write flow:
    - `mode='write_stage'`, `stage='requirements'|'bugfix'|'design'|'tasks'`,
      `content='<complete markdown document>'`.
    - `mode='edit'|'write'|'update'|'rewrite_stage'` rewrites an existing
      unapproved stage; `read`/`read_stage`/`read_section` read the current
      document or section.
    - Use `replace_section`/`append_section`/`rewrite_stage` only while the
      current stage is still unapproved or after a real user revision gate.
    - Approved stages are locked. If you try to rewrite an approved stage, the
      tool returns `spec_stage_locked`; move to `nextStage` instead.
    - `mode='approve'` is reserved for user/client approval continuations,
      Admin/API flows, or test harnesses. A Supervisor tool call must not use it
      to approve its own draft.

    In a Spec approval continuation, the active `specId` and `nextStage` are
    supplied by Engine. The only valid document write is for that exact
    `nextStage`. Do not restart requirements/design/tasks from older chat text.

    `tasks.md` must be pipeline-ready: TASK IDs, runtime lane, dependencies,
    requirement/design refs, expected output paths, and acceptance/proof checks.
    After tasks are approved, route execution with `runtime_broker`; do not
    implement through Spec tools or Supervisor direct file writes.
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
        context_spec_id = _spec_context_spec_id()
        mutating_modes = {
            "start",
            "create",
            "create_stage",
            "write_stage",
            "stage",
            "approve",
            "approve_stage",
            "revise",
            "request_revision",
            "comment",
            "replace_section",
            "append_section",
            "rewrite_stage",
        }
        if context_spec_id and spec_id and str(spec_id).strip() != context_spec_id and normalized_mode in mutating_modes:
            return _spec_id_mismatch_payload(
                attempted_spec_id=str(spec_id).strip(),
                expected_spec_id=context_spec_id,
            )
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
            continuation_next_stage = _spec_context_next_stage()
            continuation_spec_id = _spec_context_spec_id()
            if continuation_next_stage and continuation_spec_id:
                if spec_id and str(spec_id).strip() != continuation_spec_id:
                    return _spec_id_mismatch_payload(
                        attempted_spec_id=str(spec_id).strip(),
                        expected_spec_id=continuation_spec_id,
                    )
                if not requested_stage:
                    requested_stage = continuation_next_stage
                    stage = continuation_next_stage
                    spec_id = spec_id or continuation_spec_id
                elif requested_stage != continuation_next_stage:
                    return _spec_stage_mismatch_payload(
                        attempted_stage=requested_stage,
                        expected_stage=continuation_next_stage,
                        spec_id=continuation_spec_id,
                    )
                elif not spec_id:
                    spec_id = continuation_spec_id
            requested_kind = _spec_kind_from_inputs(kind, requested_stage)
            if not continuation_spec_id and not str(spec_id or "").strip() and (requested_stage in {"", "requirements", "bugfix"} or requested_kind == "bugfix"):
                active_spec = _latest_runtime_execution_ready_spec(workspace)
                if active_spec and (
                    requested_kind == "bugfix"
                    or requested_stage == "bugfix"
                    or re.search(r"(?i)fail|failed|failure|repair|recover|empty|validation|修复|失败|报错|为空|验证", " ".join([user_request, feature_name or "", comment, content[:1200]]))
                ):
                    return _spec_runtime_execution_active_payload(active_spec=active_spec)
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
            result.setdefault(
                "transitionHint",
                _spec_transition_hint(
                    spec_id=str(result.get("specId") or spec_id or ""),
                    stage=str(result.get("stage") or stage or ""),
                    pipeline=result.get("pipelineControl") if isinstance(result.get("pipelineControl"), dict) else {},
                ),
            )
            result.setdefault("recommendedNextAction", "Show the current stage to the user for approval before moving downstream.")
            stop_command = _maybe_stop_for_spec_stage_approval(result, tool_call_id=tool_call_id)
            if stop_command is not None:
                return stop_command
            return _spec_broker_payload(**result)
        if normalized_mode in {"list", "catalog"}:
            result = spec_service.list_specs(workspace_path=workspace, include_archived=False, limit=max(1, min(int(max_chars or 20), 50)))
            result.setdefault(
                "recommendedNextAction",
                "Use the desired specId with spec_broker(mode='brief'/'read_section'). Stage approval is handled by the user/client approval event.",
            )
            return _spec_broker_payload(**result)
        if normalized_mode in {"approve", "approve_stage"}:
            if _spec_approve_blocked_for_supervisor():
                return _spec_broker_payload(
                    ok=False,
                    kind="spec_user_approval_required",
                    summary=(
                        "Spec approval is a user/client governance gate. The Supervisor cannot approve "
                        "its own Spec draft through spec_broker."
                    ),
                    recommendedNextAction=(
                        "Wait for the Phone/Web/Admin approval event. If the user requested revisions, "
                        "edit the current unapproved stage instead."
                    ),
                    transitionHint={
                        "state": "waiting_user_approval",
                        "doNot": [
                            "Do not self-approve this stage.",
                            "Do not move downstream before the user/client approval event.",
                        ],
                    },
                )
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
            result.setdefault(
                "transitionHint",
                _spec_transition_hint(
                    spec_id=str(result.get("specId") or resolved_spec_id or ""),
                    stage=str(result.get("stage") or ""),
                    pipeline=result.get("pipelineControl") if isinstance(result.get("pipelineControl"), dict) else {},
                ),
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
            result.setdefault(
                "transitionHint",
                _spec_transition_hint(
                    spec_id=str(result.get("specId") or resolved_spec_id or ""),
                    stage=str(result.get("stage") or resolved_stage or ""),
                    pipeline=result.get("pipelineControl") if isinstance(result.get("pipelineControl"), dict) else {},
                ),
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
            requested_stage = _spec_stage_from_inputs(stage, kind)
            try:
                result = spec_service.read_section(
                    workspace_path=workspace,
                    spec_id=str(resolved_spec_id),
                    stage=requested_stage,
                    section_ref=section_ref,
                    max_chars=max(500, min(int(max_chars or 4000), 12000)),
                )
            except ValueError as exc:
                message = str(exc)
                if message.startswith("spec_document_not_found:"):
                    missing_stage = message.split(":", 1)[1] if ":" in message else requested_stage
                    return _spec_missing_stage_payload(
                        workspace=workspace,
                        spec_id=str(resolved_spec_id),
                        stage=missing_stage or requested_stage,
                        error=message,
                    )
                raise
            pipeline = (
                dict((result.get("specBrief") or {}).get("pipelineControl") or {})
                if isinstance(result.get("specBrief"), dict)
                else {}
            )
            result.setdefault("pipelineControl", pipeline)
            result.setdefault(
                "transitionHint",
                _spec_transition_hint(
                    spec_id=str(result.get("specId") or resolved_spec_id or ""),
                    stage=str(result.get("stage") or requested_stage or ""),
                    pipeline=pipeline,
                ),
            )
            result.setdefault(
                "recommendedNextAction",
                (
                    result["transitionHint"].get("whenReady")
                    if isinstance(result.get("transitionHint"), dict)
                    else "Use spec_broker(mode='brief') and follow pipelineControl.nextStage."
                ),
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
    "_spec_transition_hint",
    "spec_broker",
]
