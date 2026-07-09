from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Any, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.graph import END
from langgraph.types import Command

from core.database import db
from core.spec_service import spec_service
from erc.command_service import command_service
from erc.models import ApprovalRequest
from erc.runtime_context import get_runtime_context


_SPEC_STAGES = {"requirements", "bugfix", "design", "tasks"}
_SPEC_RUNTIME_EXECUTION = "runtime_execution"
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


def _spec_stage_contract_hint(stage: str) -> dict[str, Any]:
    normalized = str(stage or "").strip().lower()
    if normalized == "requirements":
        return {
            "stage": "requirements",
            "purpose": "requirements.md captures user-visible outcomes, acceptance criteria, and boundaries before design.",
            "mustInclude": [
                "Stable requirement IDs, e.g. REQ-001",
                "User-visible outcomes and delivery format",
                "Acceptance criteria using clear WHEN/THEN, SHALL, or explicit 验收标准 wording",
                "Out-of-scope boundaries when relevant",
            ],
            "minimalMarkdown": (
                "# Requirements: <feature name>\n\n"
                "## Goals\n\n"
                "- REQ-001: <user-visible outcome and delivery format>\n\n"
                "## Acceptance Criteria\n\n"
                "- AC-REQ-001: WHEN <condition>, THEN <observable result> SHALL <verification expectation>.\n\n"
                "## Boundaries\n\n"
                "- <what is intentionally out of scope or must not change>\n"
            ),
            "commonFailures": [
                "Only prose is provided and no REQ/AC-REQ traceability exists.",
                "Acceptance criteria describe implementation activity instead of observable user outcomes.",
                "Scope boundaries are omitted for risky or ambiguous requests.",
            ],
            "repairHint": "Rewrite requirements with REQ ids, observable acceptance criteria, and explicit boundaries; do not invent missing product intent.",
        }
    if normalized == "bugfix":
        return {
            "stage": "bugfix",
            "purpose": "bugfix.md captures observed failure, expected behavior, unchanged behavior, root-cause evidence, and acceptance.",
            "mustInclude": [
                "Stable bugfix IDs, e.g. BFIX-001",
                "Current behavior and expected behavior",
                "Unchanged behavior that must remain stable",
                "Root-cause evidence or pending-evidence marker",
                "Acceptance criteria tied to BFIX IDs",
            ],
            "minimalMarkdown": (
                "# Bugfix Spec: <feature or defect>\n\n"
                "## Current Behavior\n\n"
                "- BFIX-001: <observed failure or regression>\n\n"
                "## Expected Behavior\n\n"
                "- BFIX-002: <expected behavior after the fix>\n\n"
                "## Unchanged Behavior\n\n"
                "- BFIX-003: <behavior that must not change>\n\n"
                "## Root Cause Analysis\n\n"
                "- BFIX-004: <confirmed cause or `pending evidence`>\n\n"
                "## Acceptance Criteria\n\n"
                "- AC-BFIX-001: WHEN <fix is verified>, THEN <failing behavior> SHALL <pass condition>.\n"
            ),
            "commonFailures": [
                "The failure is described without reproducible evidence.",
                "Expected behavior is missing or mixed with implementation steps.",
                "No unchanged behavior is listed, increasing regression risk.",
            ],
            "repairHint": "Separate current/expected/unchanged behavior and only record root cause once evidence exists.",
        }
    if normalized == "design":
        return {
            "stage": "design",
            "purpose": "design.md explains the smallest technical path that satisfies approved requirements or bugfix items.",
            "mustInclude": [
                "Stable design IDs, e.g. DES-001",
                "Architecture or approach summary",
                "Runtime/subagent needs and why they are required",
                "Files, interfaces, config, or public contracts that may change",
                "Verification strategy and risks",
            ],
            "minimalMarkdown": (
                "# Design: <feature name>\n\n"
                "## Architecture\n\n"
                "- DES-001: <smallest viable technical path tied to approved requirements>\n\n"
                "## Runtime Plan\n\n"
                "- DES-002: <which runtime/subagent lanes are needed and why>\n\n"
                "## Files and Interfaces\n\n"
                "- DES-003: <expected files, interfaces, config, or contracts>\n\n"
                "## Verification Strategy\n\n"
                "- DES-004: <tests, dry-run, rollback, and proof expectations>\n\n"
                "## Risks\n\n"
                "- DES-005: <security, side-effect, compatibility, or recovery risks>\n"
            ),
            "commonFailures": [
                "Design restates requirements without a technical path.",
                "Runtime or subagent delegation is implied but not justified.",
                "Verification strategy is absent, so tasks cannot produce proof.",
            ],
            "repairHint": "Add DES sections for architecture, runtime plan, interfaces, verification, and risks; avoid implementation beyond the approved scope.",
        }
    if normalized == "tasks":
        return {
            "stage": "tasks",
            "purpose": "tasks.md is the runtime dispatch contract. Requirements/design may be loose, but tasks must be assignable and traceable.",
            "mustInclude": [
                "TASK ids, e.g. TASK-001",
                "runtimeLane for each task, e.g. Research, Engineering, Delegation/Subagent, Creative Media, Governance",
                "dependsOn for ordering, use [] or '-' when independent",
                "specRefs that cite requirement/design ids or explicit requirement/design sections",
                "expectedOutput paths or handoff/artifact names",
                "acceptance/proof checks",
                "mvpSlice for large tasks",
                "independentAcceptance for large tasks",
            ],
            "minimalMarkdown": (
                "## Task Pipeline\n\n"
                "| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof | MVP slice | Independent acceptance |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| TASK-001 | Research | Gather evidence for the required topic. | - | REQ-001, DES-001 | references/research/*.md + evidence pack | Sources and limits are recorded. | evidence packet is independently readable | Reviewer can verify cited sources exist. |\n"
                "| TASK-002 | Engineering | Create or update the requested artifact. | TASK-001 | REQ-001, DES-002 | target files/artifact paths | Files exist and match acceptance criteria. | smallest runnable change | Build/test output proves the slice. |\n\n"
                "## Task Details\n\n"
                "### TASK-001: <task title>\n\n"
                "- runtimeLane: Research\n"
                "- dependsOn: []\n"
                "- specRefs: REQ-001, DES-001\n"
                "- inputRefs: approved requirements/design sections\n"
                "- expectedOutput: <paths or handoff names>\n"
                "- acceptance: <how to verify>\n"
                "- proofRequired: <proof/handoff/artifact refs>\n"
                "- mvpSlice: <smallest independently useful slice>\n"
                "- independentAcceptance: <what a reviewer can verify without trusting the worker>\n"
            ),
            "commonFailures": [
                "Tasks only contain natural-language todos and no runtime lane.",
                "Tasks do not cite requirement/design refs, leaving workers to guess context.",
                "Large or delegated tasks omit proof, MVP slice, or independent acceptance.",
            ],
            "repairHint": "Rewrite tasks as an execution contract with one assignable task per runtime handoff and proof expectations for every executable task.",
        }
    return {}


def _spec_tasks_stage_contract_hint() -> dict[str, Any]:
    return _spec_stage_contract_hint("tasks")


def _spec_contract_payload_for_stage(stage: str) -> dict[str, Any]:
    contract = _spec_stage_contract_hint(stage)
    return {"stageContract": contract} if contract else {}


def _spec_transition_hint(*, spec_id: str, stage: str = "", pipeline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return compact next-step guidance for the Spec state machine."""

    control = dict(pipeline or {})
    current = str(stage or control.get("currentStage") or "").strip().lower()
    next_stage = str(control.get("nextStage") or "").strip().lower()
    blocked = str(control.get("blockedByApproval") or "").strip().lower()
    blocked_reason = str(control.get("blockedReason") or "").strip().lower()
    if blocked_reason == "stage_format_invalid":
        return {
            "state": "stage_needs_revision",
            "specId": spec_id,
            "currentStage": current,
            "nextStage": current or next_stage,
            "requiredNextTool": "spec_broker",
            "whenReady": (
                f"Rewrite or edit stage {current or next_stage} with spec_broker(mode='rewrite_stage', "
                f"spec_id='<current specId>', stage='{current or next_stage}', content='<corrected markdown>')."
            ),
            "doNot": [
                "Do not wait for user approval; no approval gate is open until this stage passes format checks.",
                "Do not move downstream before this stage is valid and then approved by the user/client.",
            ],
            **({"requiredStageContract": _spec_stage_contract_hint(current or next_stage)} if (current or next_stage) in _SPEC_STAGES else {}),
        }
    if blocked:
        downstream = {
            "requirements": "design",
            "bugfix": "design",
            "design": "tasks",
            "tasks": _SPEC_RUNTIME_EXECUTION,
        }.get(blocked, next_stage)
        if downstream == _SPEC_RUNTIME_EXECUTION:
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
            **({"requiredNextStageContract": _spec_stage_contract_hint(downstream)} if downstream in _SPEC_STAGES else {}),
        }
    if bool(control.get("runtimeExecutionAllowed")) or next_stage == _SPEC_RUNTIME_EXECUTION:
        return {
            "state": "runtime_execution_ready",
            "specId": spec_id,
            "currentStage": current,
            "nextStage": _SPEC_RUNTIME_EXECUTION,
            "requiredNextTool": "runtime_broker",
            "whenReady": (
                "Call runtime_broker(mode='route', runtime_kind='engineering', "
                "need={'kind':'engineering','reason':'approved_spec_runtime_execution','specId':'<current specId>'}) "
                "and wait for the runtime episode handoff."
            ),
            "doNot": [
                "Do not rewrite requirements/design/tasks.",
                "Do not call spec_broker with stage='runtime_execution'; runtime_execution is not a Spec document stage.",
                "Do not implement final deliverables through spec_broker.",
                "Do not treat this as Supervisor self-approval.",
            ],
        }
    if next_stage in _SPEC_STAGES:
        result = {
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
        if next_stage in _SPEC_STAGES:
            result["requiredStageContract"] = _spec_stage_contract_hint(next_stage)
        return result
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
    workspace_path: str = "",
    spec_brief: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
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
                "workspacePath": workspace_path,
                "pipelineControl": pipeline,
                "specBrief": spec_brief or {},
                "analysis": analysis or {},
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
    workspace_path = str(runtime_context.get("workspace_path") or runtime_context.get("workspacePath") or "").strip()
    if not run_id or not session_id:
        return None
    spec_id = str(result.get("specId") or "").strip()
    summary = str(result.get("summary") or f"Spec stage '{stage}' is ready for review.").strip()
    spec_brief = result.get("specBrief") if isinstance(result.get("specBrief"), dict) else {}
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    approval = _request_spec_stage_approval(
        session_id=session_id,
        run_id=run_id,
        spec_id=spec_id,
        stage=stage,
        summary=summary,
        pipeline=pipeline,
        workspace_path=workspace_path,
        spec_brief=spec_brief,
        analysis=analysis,
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
        specBrief=spec_brief,
        analysis=analysis,
        stageContract=_spec_stage_contract_hint(stage),
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
        if next_stage in _SPEC_STAGES or next_stage == _SPEC_RUNTIME_EXECUTION:
            return next_stage
    runtime_context = get_runtime_context() or {}
    if not isinstance(runtime_context, dict):
        return ""
    for key in ("spec_next_stage", "specNextStage"):
        value = str(runtime_context.get(key) or "").strip().lower()
        if value in _SPEC_STAGES or value == _SPEC_RUNTIME_EXECUTION:
            return value
    return ""


def _spec_runtime_execution_not_stage_payload(*, spec_id: str = "", attempted_mode: str = "") -> str:
    return _spec_broker_payload(
        ok=False,
        kind="spec_runtime_execution_not_stage",
        summary=(
            "Spec requirements/design/tasks are already complete enough for execution. "
            "runtime_execution is not a writable Spec stage."
        ),
        specId=spec_id,
        attemptedMode=attempted_mode,
        state="runtime_execution_ready",
        requiredNextTool="runtime_broker",
        recommendedNextAction=(
            "Call runtime_broker(mode='route', runtime_kind='engineering', "
            "need={'kind':'engineering','reason':'approved_spec_runtime_execution','specId':'<current specId>'}) "
            "and wait for the runtime episode handoff."
        ),
        doNot=[
            "Do not call spec_broker(stage='runtime_execution').",
            "Do not rewrite requirements/design/tasks unless the user requests a revision.",
            "Do not implement final deliverables through spec_broker.",
        ],
    )


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


def _spec_runtime_ids() -> tuple[str, str]:
    runtime_context = get_runtime_context() or {}
    if not isinstance(runtime_context, dict):
        return "", ""
    return (
        str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip(),
        str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip(),
    )


def _spec_context_matches_stage(request: dict[str, Any], *, spec_id: str, stage: str, feature_name: str = "") -> bool:
    context = request.get("specContext") if isinstance(request.get("specContext"), dict) else {}
    if not context:
        return False
    kind = str(context.get("kind") or context.get("contextKind") or "").strip().lower()
    if kind not in {"spec_clarification", "spec-clarification"}:
        return False
    requested_stage = str(context.get("stage") or context.get("specStage") or "").strip().lower()
    if requested_stage and requested_stage != stage:
        return False
    requested_spec_id = str(context.get("specId") or context.get("spec_id") or "").strip()
    if requested_spec_id and spec_id and requested_spec_id != spec_id:
        return False
    requested_feature = str(context.get("featureName") or "").strip().lower()
    if requested_feature and feature_name and requested_feature not in str(feature_name).strip().lower():
        return False
    return True


def _spec_resolved_clarification_interactions(*, spec_id: str, stage: str, feature_name: str = "") -> list[dict[str, Any]]:
    session_id, run_id = _spec_runtime_ids()
    if not session_id or not run_id:
        return []
    matches: list[dict[str, Any]] = []
    for interaction in db.list_ask_user_interactions(session_id=session_id, run_id=run_id, status="resolved"):
        request = interaction.get("request") if isinstance(interaction.get("request"), dict) else {}
        if _spec_context_matches_stage(request, spec_id=spec_id, stage=stage, feature_name=feature_name):
            matches.append(interaction)
    return matches


def _spec_record_resolved_clarifications(
    *,
    workspace: str,
    spec_id: str,
    stage: str,
    feature_name: str = "",
) -> None:
    for interaction in _spec_resolved_clarification_interactions(spec_id=spec_id, stage=stage, feature_name=feature_name):
        request = interaction.get("request") if isinstance(interaction.get("request"), dict) else {}
        try:
            spec_service.record_clarification(
                workspace_path=workspace,
                spec_id=spec_id,
                stage=stage,
                question=str(request.get("question") or request.get("prompt") or ""),
                answer=str(interaction.get("answer_text") or ""),
                source_run_id=str(interaction.get("run_id") or ""),
                tool_call_id=str(interaction.get("tool_call_id") or ""),
                interaction_id=str(interaction.get("id") or ""),
                feature_name=feature_name,
            )
        except Exception:
            continue


def _spec_clarification_ready(*, workspace: str, spec_id: str, stage: str, feature_name: str = "") -> bool:
    session_id, run_id = _spec_runtime_ids()
    if not session_id or not run_id:
        return True
    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage not in _SPEC_STAGES:
        return True
    if spec_id and spec_service.has_stage_clarification(workspace_path=workspace, spec_id=spec_id, stage=normalized_stage):
        return True
    return bool(_spec_resolved_clarification_interactions(spec_id=spec_id, stage=normalized_stage, feature_name=feature_name))


def _spec_clarification_required_payload(*, spec_id: str, stage: str, feature_name: str = "", workspace: str = "") -> str:
    return _spec_broker_payload(
        ok=False,
        kind="spec_clarification_required",
        specId=spec_id or None,
        featureName=feature_name or None,
        stage=stage,
        stageContract=_spec_stage_contract_hint(stage),
        summary=(
            "Spec document writing is blocked until this stage records at least one human clarification "
            "through ask_user."
        ),
        recommendedNextAction=(
            "Call ask_user with one to three plain-language questions and include "
            "specContext={kind:'spec_clarification', specId, featureName, stage, workspacePath}. "
            "After the user answers, retry spec_broker with the complete Markdown stage."
        ),
        askUserTemplate={
            "interactionKind": "ask_user",
            "question": f"为了写好 {feature_name or '当前 Spec'} 的 {stage}，我需要确认几个关键点。",
            "specContext": {
                "kind": "spec_clarification",
                **({"specId": spec_id} if spec_id else {}),
                **({"featureName": feature_name} if feature_name else {}),
                "stage": stage,
                **({"workspacePath": workspace} if workspace else {}),
            },
        },
    )


def _spec_stage_mismatch_payload(*, attempted_stage: str, expected_stage: str, spec_id: str) -> str:
    if expected_stage == _SPEC_RUNTIME_EXECUTION:
        return _spec_runtime_execution_not_stage_payload(spec_id=spec_id, attempted_mode="stage_mismatch")
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
        requiredStageContract=_spec_stage_contract_hint(expected_stage),
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
        **({"stageContract": _spec_stage_contract_hint(next_stage or requested)} if (next_stage or requested) in _SPEC_STAGES else {}),
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

    User-facing wording: call this the `规格文档` or `Spec 模式` workflow.
    Keep `spec_broker` as an internal tool name for tool calls, diagnostics,
    logs, and exact references; do not present it as a product feature name.

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
    - In a live chat run, the first write of each main stage requires a
      resolved `ask_user` clarification with
      `specContext.kind='spec_clarification'`; if missing, this tool returns
      `spec_clarification_required`.
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

    `tasks.md` must be pipeline-ready. Requirements/design can be loose, but
    tasks must be assignable and traceable: include `TASK-001` style IDs,
    runtime lane / `runtimeLane`, `dependsOn`, `specRefs` that cite
    requirement/design ids or sections, expected output paths/handoffs,
    acceptance/proof checks, and for large tasks `mvpSlice` plus
    `independentAcceptance`. A natural-language task list without refs is only
    a draft and will not open the approval gate.
    Approval payloads include checklist/analysis evidence; summarize that
    evidence for users instead of dumping raw JSON.
    After tasks are approved, internally route execution with `runtime_broker`
    into the required specialist mode; when speaking to users say 编程模式,
    多媒体创作, 桌面操作, 自动流程, or 子代理协作 as appropriate. Do not
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
            if requested_stage == _SPEC_RUNTIME_EXECUTION:
                return _spec_runtime_execution_not_stage_payload(
                    spec_id=str(spec_id or _spec_context_spec_id() or "").strip(),
                    attempted_mode=normalized_mode,
                )
            continuation_next_stage = _spec_context_next_stage()
            continuation_spec_id = _spec_context_spec_id()
            if continuation_next_stage and continuation_spec_id:
                if continuation_next_stage == _SPEC_RUNTIME_EXECUTION:
                    return _spec_runtime_execution_not_stage_payload(
                        spec_id=continuation_spec_id,
                        attempted_mode=normalized_mode,
                    )
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
                    or re.search(r"(?i)fail|failed|failure|repair|recover|empty|bugfix|修复|失败|报错|为空|异常|补救", " ".join([user_request, feature_name or "", comment, content[:1200]]))
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
            clarification_stage = requested_stage or ("bugfix" if requested_kind == "bugfix" else "requirements")
            clarification_feature = str(feature_name or user_request or "").strip()
            if not _spec_clarification_ready(
                workspace=workspace,
                spec_id=str(spec_id or ""),
                stage=clarification_stage,
                feature_name=clarification_feature,
            ):
                return _spec_clarification_required_payload(
                    spec_id=str(spec_id or ""),
                    stage=clarification_stage,
                    feature_name=clarification_feature,
                    workspace=workspace,
                )
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
            if result.get("ok"):
                _spec_record_resolved_clarifications(
                    workspace=workspace,
                    spec_id=str(result.get("specId") or spec_id or ""),
                    stage=str(result.get("stage") or clarification_stage),
                    feature_name=clarification_feature,
                )
                try:
                    result["specBrief"] = spec_service.build_brief(
                        workspace_path=workspace,
                        spec_id=str(result.get("specId") or spec_id or ""),
                    )
                    result["linkedSections"] = result["specBrief"].get("linkedSections")
                except Exception:
                    pass
            result.setdefault(
                "transitionHint",
                _spec_transition_hint(
                    spec_id=str(result.get("specId") or spec_id or ""),
                    stage=str(result.get("stage") or stage or ""),
                    pipeline=result.get("pipelineControl") if isinstance(result.get("pipelineControl"), dict) else {},
                ),
            )
            result.setdefault(
                "stageContract",
                _spec_stage_contract_hint(str(result.get("stage") or requested_stage or stage or clarification_stage)),
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
            result.setdefault("stageContract", _spec_stage_contract_hint(str(result.get("stage") or _spec_stage_from_inputs(stage, kind) or "")))
            if isinstance(result.get("analysis"), dict) and list(result["analysis"].get("hardBlockers") or []):
                result.setdefault("hardBlockers", result["analysis"].get("hardBlockers"))
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
            if resolved_stage == _SPEC_RUNTIME_EXECUTION:
                return _spec_runtime_execution_not_stage_payload(
                    spec_id=str(resolved_spec_id or ""),
                    attempted_mode=normalized_mode,
                )
            clarification_stage = resolved_stage or _spec_context_next_stage()
            if clarification_stage in _SPEC_STAGES and not _spec_clarification_ready(
                workspace=workspace,
                spec_id=str(resolved_spec_id or ""),
                stage=clarification_stage,
                feature_name=str(feature_name or user_request or "").strip(),
            ):
                return _spec_clarification_required_payload(
                    spec_id=str(resolved_spec_id or ""),
                    stage=clarification_stage,
                    feature_name=str(feature_name or user_request or "").strip(),
                    workspace=workspace,
                )
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
            _spec_record_resolved_clarifications(
                workspace=workspace,
                spec_id=str(resolved_spec_id),
                stage=str(result.get("stage") or clarification_stage or resolved_stage),
                feature_name=str(feature_name or user_request or "").strip(),
            )
            result.setdefault(
                "transitionHint",
                _spec_transition_hint(
                    spec_id=str(result.get("specId") or resolved_spec_id or ""),
                    stage=str(result.get("stage") or resolved_stage or ""),
                    pipeline=result.get("pipelineControl") if isinstance(result.get("pipelineControl"), dict) else {},
                ),
            )
            result.setdefault(
                "stageContract",
                _spec_stage_contract_hint(str(result.get("stage") or resolved_stage or clarification_stage or "")),
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
            if requested_stage == _SPEC_RUNTIME_EXECUTION:
                return _spec_runtime_execution_not_stage_payload(
                    spec_id=str(resolved_spec_id or ""),
                    attempted_mode=normalized_mode,
                )
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
